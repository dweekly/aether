#!/usr/bin/env python3
"""extract_v2: the March fleet extraction, re-runnable on any model and any crawl dir.

One module for every extraction in the Hackers 2026 research plan
(docs/hackers-2026-research-plan.md, workstreams 1-4). It builds Message Batches
requests from a crawl cache exactly as the March scripts did:

  layout "http"     09b_fleet_extract.py: a crawl index (fleet_crawl_index.json) whose
                    pages[].html_path point into an HTML dir (fleet_html/). Pages are
                    concatenated as "--- Page: <url> (type: <type>) ---", each capped at
                    50,000 chars; custom_id "school_<i>" by index position.
  layout "browser"  09c_browser_extract.py: a dir of <domain>/*.txt rendered text
                    (fleet_browser_text/), metadata joined from crawl indexes by domain.
                    Pages under 50 non-blank chars are skipped, each page capped at
                    40,000 chars (09c's cap, not 09b's); custom_id "browser_<i>".

The system prompt and user prompt are the March text, imported from 09b/09c rather
than copied, with one block inserted before "Web pages:" that asks for the training
prices (PRICING_FIELDS). The answer is constrained by structured output
(output_config.format json_schema) holding the March fields under their March names
plus the new ones. `selftest` proves the page concatenation and custom_id/domain
mapping are byte-identical to what 09b/09c build.

Every request's custom_id, domain and school metadata go in a sidecar index.json, and
results are written keyed by custom_id. Run directory layout:

  <run>/index.json                 sidecar: custom_id -> domain, school metadata, pages
  <run>/requests.jsonl             {"custom_id", "system", "user"}, model-independent
  <run>/<model>/batches.json       batch ids, settings, estimated cost before submit
  <run>/<model>/results.jsonl      one {"custom_id", "result": {...}} per request
  <run>/<model>/usage.json         recorded token usage and cost after fetch
  <run>/log.jsonl                  append-only log of estimates, submits and fetches

Usage (on trogdor, from research/FlightSchoolMapping):
  PY=/mnt/data/home/dew/dev/aether/research/FlightSchoolMapping/scripts/.venv/bin/python
  OUT=/mnt/data/home/dew/dev/aether/research/FlightSchoolMapping/output
  $PY scripts/extract_v2.py build --layout http --index $OUT/fleet_crawl_index.json \
      --pages-dir $OUT/fleet_html --run RUN_DIR
  $PY scripts/extract_v2.py build --layout browser --pages-dir $OUT/fleet_browser_text \
      --meta-index $OUT/fleet_crawl_index.json --meta-index $OUT/fleet_browser_index.json --run RUN_DIR
  $PY scripts/extract_v2.py estimate --run RUN_DIR --model claude-haiku-5-5 --sample 40
  $PY scripts/extract_v2.py sync     --run RUN_DIR --model claude-haiku-5-5 --limit 5
  $PY scripts/extract_v2.py submit   --run RUN_DIR --model claude-haiku-5-5
  $PY scripts/extract_v2.py fetch    --run RUN_DIR --model claude-haiku-5-5
  $PY scripts/extract_v2.py selftest --output-dir $OUT

The API key is read from ANTHROPIC_API_KEY, else from --env-file (default: the repo's .env).
Library use: build_schools_http / build_schools_browser -> build_requests -> params_for.
"""

import argparse
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from urllib.parse import urlparse

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
REPO = SCRIPTS.parents[2]

# The March prompt text, imported so it cannot drift from what was run.
_m09b = import_module("09b_fleet_extract")
_m09c = import_module("09c_browser_extract")

MAX_TOKENS = 32000  # thinking counts toward max_tokens on the 5.5 models; billed only as used
DEFAULT_EFFORT = "low"
BATCH_MAX_BYTES = 150_000_000  # API limit is 256 MB per batch
BATCH_MAX_REQUESTS = 10_000

# $/MTok at Message Batches rates (half of list), from platform.claude.com/docs/en/about-claude/pricing
# (read 2026-11-03). Haiku 5.5 has two rate cards chosen per request by prompt length.
BATCH_PRICES = {
    "claude-sonnet-4-6": [(None, 1.50, 7.50)],
    "claude-sonnet-5-5": [(None, 1.00, 5.00)],
    "claude-haiku-5-5": [(100_000, 0.05, 0.25), (None, 0.25, 1.25)],
}


def price_request(model, tokens_in, tokens_out):
    """Batch cost in USD of one request, given its total prompt and output tokens."""
    for limit, pin, pout in BATCH_PRICES[model]:
        if limit is None or tokens_in <= limit:
            return tokens_in / 1e6 * pin + tokens_out / 1e6 * pout
    raise AssertionError("unreachable")


PRICING_FIELDS = """\
Also extract the school's training prices (school-wide, not per aircraft). Use null, \
false or "" when the pages do not say. Only extract prices that appear on the pages; \
do not compute or estimate any.
- instructor_rate_hr: hourly rate for flight instruction by a CFI (number), or null. \
If several, the standard primary-training CFI rate.
- instructor_included_in_rental: true if the pages say the aircraft rental rates \
already include the instructor, false if instruction is billed separately or the \
rates are solo/rental only, null if not stated.
- private_pilot_package: a private pilot certificate (PPL) program, package or \
advertised total cost. price_usd: the stated price (number; the low end if a range), \
or null if none. price_text: the price exactly as written, or "". is_estimate: true if \
the page presents it as an estimate or typical cost rather than a fixed package price. \
includes: what the price covers as stated (hours, aircraft, ground school, exam fees), \
or "". source_url: the page it is on, or "".
- club_initiation_fee: one-time flying club initiation, joining or equity fee \
(number), or null.
- club_dues_usd: recurring flying club dues (number), or null. club_dues_period: \
"month", "quarter", "year" or "other" as stated, or "" if no dues.
- block_discounts: block-time, prepaid-account or hour-package discounts, each with \
description (as stated), prepay_usd (the amount paid up front, or null) and \
discounted_rate_hr (the resulting hourly rate, or null). Empty list if none.
- simulator_rate_hr: hourly rate for a flight simulator, AATD, BATD or FTD (number), \
or null.
- pricing_source_url: the URL of the page where most of these training prices \
appear, or "".
Use the exact URLs from the "--- Page: <url> ---" headers above."""

_MARKER = "\n\nWeb pages:\n\n{pages_content}"


def _with_pricing(template):
    assert template.count(_MARKER) == 1 and template.endswith(_MARKER), "March template changed shape"
    return template[: -len(_MARKER)] + "\n\n" + PRICING_FIELDS + _MARKER


LAYOUTS = {
    "http": {"system": _m09b.SYSTEM_PROMPT, "user": _with_pricing(_m09b.USER_PROMPT_TEMPLATE),
             "prefix": "school", "page_cap": 50_000, "min_chars": 0, "extra": {"canonical_name": {"type": "string"}}},
    "browser": {"system": _m09c.SYSTEM_PROMPT, "user": _with_pricing(_m09c.USER_PROMPT_TEMPLATE),
                "prefix": "browser", "page_cap": 40_000, "min_chars": 50, "extra": {"rate_sheet_url": {"type": "string"}}},
}

SCHOOL_TYPES = ["flight_school_with_rentals", "flight_school_no_rentals", "flying_club", "fbo",
                "university_program", "maintenance_shop", "charter_operator", "defunct", "other"]


def _obj(props):
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


S, N_NULL, B_NULL = {"type": "string"}, {"type": ["number", "null"]}, {"type": ["boolean", "null"]}


def schema_for(layout):
    """March fields under their March names, plus the training-price fields."""
    props = dict(LAYOUTS[layout]["extra"])
    props.update({
        "school_type": {"type": "string", "enum": SCHOOL_TYPES},
        "contact": _obj({"phone": S, "email": S, "address": S}),
        "locations": {"type": "array", "items": _obj({"airport_id": S, "airport_name": S, "label": S})},
        "fleet": {"type": "array", "items": _obj({
            "n_number": S, "display_name": S, "description": S, "wet_rate_hr": N_NULL,
            "equipment": {"type": "array", "items": S}, "airport_id": S, "source_url": S})},
        "instructor_rate_hr": N_NULL,
        "instructor_included_in_rental": B_NULL,
        "private_pilot_package": _obj({"price_usd": N_NULL, "price_text": S, "is_estimate": {"type": "boolean"},
                                       "includes": S, "source_url": S}),
        "club_initiation_fee": N_NULL,
        "club_dues_usd": N_NULL,
        "club_dues_period": {"type": "string", "enum": ["month", "quarter", "year", "other", ""]},
        "block_discounts": {"type": "array", "items": _obj({"description": S, "prepay_usd": N_NULL,
                                                             "discounted_rate_hr": N_NULL})},
        "simulator_rate_hr": N_NULL,
        "pricing_source_url": S,
    })
    return _obj(props)


# ── Schools from a crawl cache ───────────────────────────────────


def normalize_domain(url):
    return _m09c._normalize_domain(url)


def build_schools_http(index_path):
    """09b's school list: the crawl index as-is, position i -> custom_id school_<i>."""
    schools = json.loads(Path(index_path).read_text())
    for s in schools:
        s.setdefault("domain", normalize_domain(s.get("school_website", "")))
    return schools


def build_schools_browser(text_dir, meta_indexes):
    """09c's school list (build_browser_school_index) for any text dir and metadata indexes.

    Metadata is joined by normalized domain; later indexes override earlier ones, as 09c
    lets fleet_browser_index.json override fleet_crawl_index.json.
    """
    meta = {}
    for p in meta_indexes:
        for school in json.loads(Path(p).read_text()):
            d = normalize_domain(school.get("school_website", ""))
            if d:
                meta[d] = school
    schools = []
    for domain_dir in sorted(Path(text_dir).iterdir()):
        if not domain_dir.is_dir():
            continue
        text_files = sorted(domain_dir.glob("*.txt"))
        if not text_files:
            continue
        domain = domain_dir.name
        m = meta.get(domain, {})
        pages = []
        for tf in text_files:  # 09c reconstructs the URL from the file name
            parts = tf.stem.rsplit("_", 1)
            slug = parts[0] if len(parts) == 2 and len(parts[1]) == 8 else tf.stem
            url = f"https://{domain}/" if slug == "index" else f"https://{domain}/{slug.replace('_', '/')}"
            ptype = "home" if slug == "index" else "fleet"
            low = slug.lower()
            if any(k in low for k in ["fleet", "aircraft", "rental", "plane", "rate"]):
                ptype = "fleet"
            elif any(k in low for k in ["about", "contact", "location"]):
                ptype = "about"
            pages.append({"url": url, "text_path": f"{domain}/{tf.name}", "type": ptype})
        schools.append({"domain": domain, "school_name": m.get("school_name", domain),
                        "school_website": m.get("school_website", f"https://{domain}/"),
                        "known_airport": m.get("known_airport", ""), "city": m.get("city", ""),
                        "state": m.get("state", ""), "pages": pages})
    return schools


def pages_content(layout, school, pages_dir):
    cfg = LAYOUTS[layout]
    key = "html_path" if layout == "http" else "text_path"
    parts = []
    for page in school["pages"]:
        f = Path(pages_dir) / page[key]
        text = f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""
        if not text or (cfg["min_chars"] and len(text.strip()) < cfg["min_chars"]):
            continue
        if len(text) > cfg["page_cap"]:
            text = text[: cfg["page_cap"]] + "\n[... truncated ...]"
        parts.append(f"--- Page: {page['url']} (type: {page['type']}) ---\n{text}")
    return "\n\n".join(parts)


def build_requests(layout, schools, pages_dir, prefix=None):
    """(requests, index). Schools with no page text get no request but keep their position."""
    cfg = LAYOUTS[layout]
    prefix = prefix or cfg["prefix"]
    requests, index = [], []
    for i, school in enumerate(schools):
        content = pages_content(layout, school, pages_dir)
        if not content.strip():
            continue
        city, state = school.get("city", ""), school.get("state", "")
        city_state = f"{city}, {state}" if city and state else city or state or "Unknown"
        known = school.get("known_airport", "")
        if known:
            known = known.upper()
            if len(known) == 3:
                known = "K" + known
        user = cfg["user"].format(school_name=school["school_name"], school_website=school["school_website"],
                                  known_airport=known or "Not specified", city_state=city_state,
                                  pages_content=content)
        cid = f"{prefix}_{i:04d}"
        requests.append({"custom_id": cid, "system": cfg["system"], "user": user})
        index.append({"custom_id": cid, "domain": school.get("domain") or normalize_domain(school["school_website"]),
                      "school_name": school["school_name"], "school_website": school["school_website"],
                      "known_airport": school.get("known_airport", ""), "city": city, "state": state,
                      "layout": layout, "pages_dir": str(pages_dir),
                      "pages": [{"url": p["url"], "path": p.get("html_path") or p.get("text_path"), "type": p["type"]}
                                for p in school["pages"]],
                      "user_chars": len(user)})
    return requests, index


def params_for(req, layout, model, effort=DEFAULT_EFFORT):
    return {"model": model, "max_tokens": MAX_TOKENS, "system": req["system"],
            "messages": [{"role": "user", "content": req["user"]}],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema_for(layout)}}}


def prompt_fingerprint(layout):
    cfg = LAYOUTS[layout]
    blob = json.dumps([cfg["system"], cfg["user"], schema_for(layout)], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ── Run directory helpers ────────────────────────────────────────


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(run, **kw):
    kw = {"at": _now(), **kw}
    with open(Path(run) / "log.jsonl", "a") as f:
        f.write(json.dumps(kw) + "\n")
    print(json.dumps(kw))


def load_run(run):
    run = Path(run)
    meta = json.loads((run / "run.json").read_text())
    index = json.loads((run / "index.json").read_text())
    reqs = [json.loads(l) for l in (run / "requests.jsonl").open()]
    return meta, index, reqs


def client(env_file):
    import anthropic
    if not os.environ.get("ANTHROPIC_API_KEY"):
        from dotenv import dotenv_values
        key = dotenv_values(env_file).get("ANTHROPIC_API_KEY")
        if not key:
            sys.exit(f"FAIL no ANTHROPIC_API_KEY in environment or {env_file}")
        return anthropic.Anthropic(api_key=key, max_retries=5)
    return anthropic.Anthropic(max_retries=5)


def _result_record(custom_id, result):
    """Same shape as the March results files: {"custom_id", "result": {"type", "message"|"error"}}."""
    out = {"type": result.type}
    if result.type == "succeeded":
        out["message"] = result.message.model_dump(mode="json")
    elif result.type == "errored":
        out["error"] = result.error.model_dump(mode="json") if hasattr(result.error, "model_dump") else str(result.error)
    return {"custom_id": custom_id, "result": out}


def summarize_usage(model, records):
    tin = tout = 0
    cost = 0.0
    stops, types = {}, {}
    for r in records:
        res = r["result"]
        types[res["type"]] = types.get(res["type"], 0) + 1
        if res["type"] != "succeeded":
            continue
        u = res["message"]["usage"]
        i = u["input_tokens"] + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
        o = u["output_tokens"]
        tin += i
        tout += o
        cost += price_request(model, i, o)
        sr = res["message"].get("stop_reason")
        stops[sr] = stops.get(sr, 0) + 1
    return {"model": model, "results": len(records), "types": types, "stop_reasons": stops,
            "tokens_in": tin, "tokens_out": tout, "cost_usd_batch": round(cost, 4)}


# ── Commands ─────────────────────────────────────────────────────


def cmd_build(a):
    run = Path(a.run)
    if (run / "requests.jsonl").exists() and not a.force:
        sys.exit(f"FAIL {run}/requests.jsonl exists (prompt text is frozen once built; --force to rebuild)")
    run.mkdir(parents=True, exist_ok=True)
    if a.layout == "http":
        schools = build_schools_http(a.index)
    else:
        schools = build_schools_browser(a.pages_dir, a.meta_index or [])
    if a.limit:
        schools = schools[: a.limit]
    reqs, index = build_requests(a.layout, schools, a.pages_dir, a.prefix)
    with open(run / "requests.jsonl", "w") as f:
        for r in reqs:
            f.write(json.dumps(r) + "\n")
    (run / "index.json").write_text(json.dumps(index, indent=1) + "\n")
    meta = {"layout": a.layout, "built_at": _now(), "schools": len(schools), "requests": len(reqs),
            "index": str(a.index or ""), "pages_dir": str(a.pages_dir), "meta_index": a.meta_index or [],
            "prompt_fingerprint": prompt_fingerprint(a.layout), "max_tokens": MAX_TOKENS}
    (run / "run.json").write_text(json.dumps(meta, indent=2) + "\n")
    log(run, event="build", **{k: meta[k] for k in ("layout", "schools", "requests", "prompt_fingerprint")},
        user_chars=sum(len(r["user"]) for r in reqs))


def cmd_estimate(a):
    meta, index, reqs = load_run(a.run)
    c = client(a.env_file)
    rng = random.Random(a.seed)
    sample = rng.sample(reqs, min(a.sample, len(reqs)))
    ratios, big = [], 0
    for r in sample:
        p = params_for(r, meta["layout"], a.model, a.effort)
        n = c.messages.count_tokens(model=p["model"], system=p["system"], messages=p["messages"],
                                    thinking=p["thinking"], output_config=p["output_config"]).input_tokens
        ratios.append(n / len(r["user"]))
    ratio = sum(ratios) / len(ratios)
    est_in = [len(r["user"]) * ratio for r in reqs]
    big = sum(1 for t in est_in if t > 100_000)
    out_per = a.out_tokens
    cost = sum(price_request(a.model, t, out_per) for t in est_in)
    log(a.run, event="estimate", model=a.model, effort=a.effort, sample=len(sample),
        tokens_per_char=round(ratio, 4), est_tokens_in=int(sum(est_in)), requests_over_100k=big,
        assumed_out_tokens_per_request=out_per, est_cost_usd_batch=round(cost, 2))


def cmd_sync(a):
    meta, index, reqs = load_run(a.run)
    c = client(a.env_file)
    d = Path(a.run) / f"{a.model}-sync"
    d.mkdir(exist_ok=True)
    recs = []
    for r in reqs[: a.limit]:
        p = params_for(r, meta["layout"], a.model, a.effort)
        try:
            with c.messages.stream(**p) as st:  # max_tokens this large needs streaming on the sync API
                m = st.get_final_message()
            recs.append({"custom_id": r["custom_id"], "result": {"type": "succeeded", "message": m.model_dump(mode="json")}})
        except Exception as e:  # recorded, not swallowed: shows up as errored in the results file
            recs.append({"custom_id": r["custom_id"], "result": {"type": "errored", "error": repr(e)}})
            print(f"  {r['custom_id']}: {e!r}", file=sys.stderr)
    with open(d / "results.jsonl", "w") as f:
        for x in recs:
            f.write(json.dumps(x) + "\n")
    u = summarize_usage(a.model, recs)
    u["cost_usd_sync"] = round(u["cost_usd_batch"] * 2, 4)
    (d / "usage.json").write_text(json.dumps(u, indent=2) + "\n")
    log(a.run, event="sync", **u)


def _chunks(lines):
    cur, size = [], 0
    for line in lines:
        if cur and (size + len(line) > BATCH_MAX_BYTES or len(cur) >= BATCH_MAX_REQUESTS):
            yield cur
            cur, size = [], 0
        cur.append(line)
        size += len(line)
    if cur:
        yield cur


def cmd_submit(a):
    meta, index, reqs = load_run(a.run)
    d = Path(a.run) / (a.model + (f"-{a.tag}" if a.tag else ""))
    if (d / "batches.json").exists():
        sys.exit(f"FAIL {d}/batches.json exists: already submitted")
    d.mkdir(exist_ok=True)
    if a.only:
        keep = set(Path(a.only).read_text().split())
        reqs = [r for r in reqs if r["custom_id"] in keep]
    c = client(a.env_file)
    lines = [json.dumps({"custom_id": r["custom_id"], "params": params_for(r, meta["layout"], a.model, a.effort)})
             for r in reqs]
    ids = []
    for chunk in _chunks(lines):
        b = c.messages.batches.create(requests=[json.loads(x) for x in chunk])
        ids.append(b.id)
        print(f"  {b.id}: {len(chunk)} requests, {sum(map(len, chunk)) / 1e6:.0f} MB")
    info = {"model": a.model, "effort": a.effort, "tag": a.tag, "thinking": "adaptive", "max_tokens": MAX_TOKENS,
            "prompt_fingerprint": prompt_fingerprint(meta["layout"]), "requests": len(reqs),
            "batch_ids": ids, "submitted_at": _now()}
    (d / "batches.json").write_text(json.dumps(info, indent=2) + "\n")
    log(a.run, event="submit", **info)


def cmd_fetch(a):
    meta, index, reqs = load_run(a.run)
    d = Path(a.run) / (a.model + (f"-{a.tag}" if a.tag else ""))
    info = json.loads((d / "batches.json").read_text())
    c = client(a.env_file)
    while True:
        bs = [c.messages.batches.retrieve(i) for i in info["batch_ids"]]
        done = sum(b.request_counts.succeeded + b.request_counts.errored + b.request_counts.canceled
                   + b.request_counts.expired for b in bs)
        print(f"  {_now()} {done}/{info['requests']} done; " + ", ".join(b.processing_status for b in bs), flush=True)
        if all(b.processing_status == "ended" for b in bs) or not a.wait:
            break
        time.sleep(60)
    if not all(b.processing_status == "ended" for b in bs):
        sys.exit("not ended yet")
    recs = []
    for i in info["batch_ids"]:
        for r in c.messages.batches.results(i):
            recs.append(_result_record(r.custom_id, r.result))
    want = {r["custom_id"] for r in reqs} if not a.only else set(Path(a.only).read_text().split())
    got = {r["custom_id"] for r in recs}
    tmp = d / "results.jsonl.tmp"
    with open(tmp, "w") as f:
        for x in sorted(recs, key=lambda x: x["custom_id"]):
            f.write(json.dumps(x) + "\n")
    tmp.rename(d / "results.jsonl")
    u = summarize_usage(a.model, recs)
    u["missing_custom_ids"] = sorted(want - got)
    u["unexpected_custom_ids"] = sorted(got - want)
    (d / "usage.json").write_text(json.dumps(u, indent=2) + "\n")
    log(a.run, event="fetch", **{k: v for k, v in u.items() if not k.endswith("custom_ids")},
        missing=len(u["missing_custom_ids"]), unexpected=len(u["unexpected_custom_ids"]))
    if u["missing_custom_ids"] or u["unexpected_custom_ids"]:
        sys.exit("FAIL results do not match the requests by custom_id")


def cmd_selftest(a):
    """The page text, custom_ids and domains match what 09b/09c build for the March caches."""
    out = Path(a.output_dir)
    fails = []
    # HTTP: 09b's builder reads module-level HTML_DIR.
    _m09b.HTML_DIR = out / "fleet_html"
    schools = build_schools_http(out / "fleet_crawl_index.json")
    old = _m09b.build_batch_requests(schools)
    new, idx = build_requests("http", schools, out / "fleet_html")
    if [r["custom_id"] for r in old] != [r["custom_id"] for r in new]:
        fails.append("http custom_ids differ from 09b")
    for o, n in zip(old, new):
        march_user = o["params"]["messages"][0]["content"]
        if n["user"].replace("\n\n" + PRICING_FIELDS, "", 1) != march_user or n["system"] != o["params"]["system"]:
            fails.append(f"http prompt differs from 09b at {o['custom_id']}")
            break
    print(f"  http: {len(new)} requests checked against 09b")
    # Browser: 09c's builder reads module-level OUTPUT_DIR / TEXT_DIR.
    _m09c.OUTPUT_DIR, _m09c.TEXT_DIR = out, out / "fleet_browser_text"
    old_s = _m09c.build_browser_school_index()
    old = _m09c.build_batch_requests(old_s)
    bs = build_schools_browser(out / "fleet_browser_text",
                               [out / "fleet_crawl_index.json", out / "fleet_browser_index.json"])
    new, idx = build_requests("browser", bs, out / "fleet_browser_text")
    if [(r["custom_id"], r["domain"]) for r in old] != [(r["custom_id"], i["domain"]) for r, i in zip(new, idx)]:
        fails.append("browser custom_id/domain differ from 09c")
    for o, n in zip(old, new):
        if n["user"].replace("\n\n" + PRICING_FIELDS, "", 1) != o["params"]["messages"][0]["content"] \
                or n["system"] != o["params"]["system"]:
            fails.append(f"browser prompt differs from 09c at {o['custom_id']}")
            break
    print(f"  browser: {len(new)} requests checked against 09c")
    # March browser index (written when the batch ran) agrees with today's rebuild where ids overlap.
    march = {e["custom_id"]: e["domain"] for e in json.loads((out / "fleet_browser_extract_index.json").read_text())}
    now = {i["custom_id"]: i["domain"] for i in idx}
    same = sum(1 for k in march if now.get(k) == march[k])
    print(f"  browser: {same} of {len(march)} March custom_ids map to the same domain today")
    if same < len(march):
        print("  NOTE browser cache or metadata has changed since March; compare by domain, not custom_id")
    if fails:
        sys.exit("FAIL " + "; ".join(fails))
    print("PASS")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", default=str(REPO / ".env"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--layout", choices=list(LAYOUTS), required=True)
    b.add_argument("--index", help="http: crawl index JSON")
    b.add_argument("--pages-dir", required=True, help="http: HTML dir; browser: text dir of <domain>/*.txt")
    b.add_argument("--meta-index", action="append", help="browser: crawl index(es) for school metadata, later wins")
    b.add_argument("--prefix", help="custom_id prefix (default school/browser, as in March)")
    b.add_argument("--run", required=True)
    b.add_argument("--limit", type=int)
    b.add_argument("--force", action="store_true")
    for name in ("estimate", "sync", "submit", "fetch"):
        p = sub.add_parser(name)
        p.add_argument("--run", required=True)
        p.add_argument("--model", required=True, choices=list(BATCH_PRICES))
        p.add_argument("--effort", default=DEFAULT_EFFORT)
        if name == "estimate":
            p.add_argument("--sample", type=int, default=40)
            p.add_argument("--seed", type=int, default=20261103)
            p.add_argument("--out-tokens", type=int, default=1500, help="assumed output tokens per request")
        if name == "sync":
            p.add_argument("--limit", type=int, default=5)
        if name in ("submit", "fetch"):
            p.add_argument("--only", help="file of custom_ids to restrict to")
            p.add_argument("--tag", default="", help="results dir suffix for a variant run, e.g. effort-high")
        if name == "fetch":
            p.add_argument("--wait", action="store_true")
    t = sub.add_parser("selftest")
    t.add_argument("--output-dir", required=True)
    a = ap.parse_args()
    {"build": cmd_build, "estimate": cmd_estimate, "sync": cmd_sync, "submit": cmd_submit,
     "fetch": cmd_fetch, "selftest": cmd_selftest}[a.cmd](a)


if __name__ == "__main__":
    main()
