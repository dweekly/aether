"""Message Batches run directories for the structured-output passes in 60 and 61.

Same layout and helpers as extract_v2.py (client, batch chunking, result records, usage pricing),
for requests whose params are built by the caller:

  <run>/requests.jsonl         {"custom_id", "params"} (model-independent params minus "model")
  <run>/index.json             custom_id -> whatever the caller needs to read the answer back
  <run>/run.json               what was built, when, prompt fingerprint
  <run>/<model>/batches.json   batch ids, settings
  <run>/<model>/results.jsonl  one {"custom_id", "result"} per request
  <run>/<model>/usage.json     recorded tokens and cost at batch prices
  <run>/log.jsonl              estimates, submits, fetches
"""

import hashlib
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_v2 as xv  # noqa: E402

MODEL = "claude-haiku-5-5"


def write_run(run, requests, index, meta):
    run = Path(run)
    if (run / "requests.jsonl").exists():
        sys.exit(f"FAIL {run}/requests.jsonl exists (frozen once built; delete the run dir to rebuild)")
    run.mkdir(parents=True, exist_ok=True)
    with open(run / "requests.jsonl", "w") as f:
        for r in requests:
            f.write(json.dumps(r) + "\n")
    (run / "index.json").write_text(json.dumps(index, indent=1) + "\n")
    fp = hashlib.sha256(json.dumps([requests[0]["params"]["system"], meta.get("prompt", "")],
                                   sort_keys=True).encode()).hexdigest()[:16] if requests else ""
    meta = {**meta, "built_at": xv._now(), "requests": len(requests), "prompt_fingerprint": fp}
    (run / "run.json").write_text(json.dumps(meta, indent=2) + "\n")
    xv.log(run, event="build", requests=len(requests), chars=sum(len(json.dumps(r)) for r in requests),
           prompt_fingerprint=fp)


def _params(r, model):
    return {"model": model, **r["params"]}


def load(run):
    return [json.loads(l) for l in (Path(run) / "requests.jsonl").open()]


def estimate(run, env_file, model=MODEL, sample=30, out_tokens=800, seed=20261105):
    reqs = load(run)
    c = xv.client(env_file)
    smp = random.Random(seed).sample(reqs, min(sample, len(reqs)))
    ratios = []
    for r in smp:
        p = _params(r, model)
        n = c.messages.count_tokens(model=model, system=p["system"], messages=p["messages"],
                                    thinking=p.get("thinking"), output_config=p["output_config"]).input_tokens
        ratios.append(n / len(json.dumps(r["params"]["messages"])))
    ratio = sum(ratios) / len(ratios)
    est_in = [len(json.dumps(r["params"]["messages"])) * ratio for r in reqs]
    cost = sum(xv.price_request(model, t, out_tokens) for t in est_in)
    xv.log(run, event="estimate", model=model, sample=len(smp), est_tokens_in=int(sum(est_in)),
           assumed_out_tokens_per_request=out_tokens, est_cost_usd_batch=round(cost, 2))
    return cost


def submit(run, env_file, model=MODEL, max_usd=None):
    run = Path(run)
    d = run / model
    if (d / "batches.json").exists():
        sys.exit(f"FAIL {d}/batches.json exists: already submitted")
    if max_usd is not None:
        est = [json.loads(l) for l in (run / "log.jsonl").open() if '"estimate"' in l]
        if not est or est[-1]["est_cost_usd_batch"] > max_usd:
            sys.exit(f"FAIL no estimate, or estimate over --max-usd {max_usd}")
    d.mkdir(exist_ok=True)
    reqs = load(run)
    c = xv.client(env_file)
    lines = [json.dumps({"custom_id": r["custom_id"], "params": _params(r, model)}) for r in reqs]
    ids = []
    for chunk in xv._chunks(lines):
        b = c.messages.batches.create(requests=[json.loads(x) for x in chunk])
        ids.append(b.id)
        print(f"  {b.id}: {len(chunk)} requests")
    info = {"model": model, "requests": len(reqs), "batch_ids": ids, "submitted_at": xv._now()}
    (d / "batches.json").write_text(json.dumps(info, indent=2) + "\n")
    xv.log(run, event="submit", **info)


def fetch(run, env_file, model=MODEL, wait=True):
    run = Path(run)
    d = run / model
    info = json.loads((d / "batches.json").read_text())
    c = xv.client(env_file)
    while True:
        bs = [c.messages.batches.retrieve(i) for i in info["batch_ids"]]
        done = sum(b.request_counts.succeeded + b.request_counts.errored + b.request_counts.canceled
                   + b.request_counts.expired for b in bs)
        print(f"  {xv._now()} {done}/{info['requests']} done", flush=True)
        if all(b.processing_status == "ended" for b in bs) or not wait:
            break
        time.sleep(60)
    if not all(b.processing_status == "ended" for b in bs):
        sys.exit("not ended yet")
    recs = [xv._result_record(r.custom_id, r.result) for i in info["batch_ids"] for r in c.messages.batches.results(i)]
    want = {r["custom_id"] for r in load(run)}
    got = {r["custom_id"] for r in recs}
    tmp = d / "results.jsonl.tmp"
    with open(tmp, "w") as f:
        for x in sorted(recs, key=lambda x: x["custom_id"]):
            f.write(json.dumps(x) + "\n")
    tmp.rename(d / "results.jsonl")
    u = xv.summarize_usage(model, recs)
    u["missing"], u["unexpected"] = sorted(want - got), sorted(got - want)
    (d / "usage.json").write_text(json.dumps(u, indent=2) + "\n")
    xv.log(run, event="fetch", **{k: v for k, v in u.items() if k not in ("missing", "unexpected")},
           n_missing=len(u["missing"]), n_unexpected=len(u["unexpected"]))
    if u["missing"] or u["unexpected"]:
        sys.exit("FAIL results do not match the requests by custom_id")


def answers(run, model=MODEL):
    """custom_id -> parsed JSON answer (None if the request failed or the text did not parse)."""
    out = {}
    for line in (Path(run) / model / "results.jsonl").open():
        r = json.loads(line)
        res = r["result"]
        if res["type"] != "succeeded":
            out[r["custom_id"]] = None
            continue
        text = "".join(b.get("text", "") for b in res["message"]["content"] if b.get("type") == "text")
        try:
            out[r["custom_id"]] = json.loads(text)
        except json.JSONDecodeError:
            out[r["custom_id"]] = None
    return out


def add_cli(sub):
    for name in ("estimate", "submit", "fetch"):
        p = sub.add_parser(name)
        p.add_argument("--run", required=True)
        p.add_argument("--model", default=MODEL)
        if name == "submit":
            p.add_argument("--max-usd", type=float, required=True)
        if name == "fetch":
            p.add_argument("--no-wait", action="store_true")


def run_cli(a, env_file):
    if a.cmd == "estimate":
        estimate(a.run, env_file, a.model)
    elif a.cmd == "submit":
        submit(a.run, env_file, a.model, a.max_usd)
    elif a.cmd == "fetch":
        fetch(a.run, env_file, a.model, wait=not a.no_wait)
