#!/usr/bin/env python3
"""How far can you trust what a language model read off 4,000 flight-school websites?

For the Hackers 2026 talk ("tedium was the moat"). Reads the raw Message Batches
results of the two crawl passes (09b over plain HTTP, 09c over a rendered browser)
exactly as the model returned them, before any of 21_build_flight_line's cleanup,
and checks every tail number the model reported against independent ground truth:

  1. Is it a well-formed US N-number?               (FAA rules, 14 CFR 47.15)
  2. Is it on the FAA registry?                       (snapshot in the release, or deregistered)
  3. Does the make the website names match the registry's manufacturer?
  4. Did it fly in the release window?               (adsb.lol archive, release aircraft.parquet)
  5. Does it live where the website says it does?   (model's airport vs inferred home base)

Negative control for 5: shuffle the website airports across the same tails. If the
shuffled match rate is not far below the real one, the home-base check proves nothing
and the script exits 1. It also exits 1 on missing inputs or an implausibly small corpus.

Cost uses the token usage recorded in each batch result, priced at Claude Sonnet 4.6
Message Batches rates ($1.50 / $7.50 per million input / output tokens: half the
$3 / $15 list price). The corpus-expansion pass (09f) recorded no usage, so the cost
is a floor, and its aircraft are not in this check.

Usage (on trogdor): scripts/.venv/bin/python scripts/49_scrape_facts.py --release v0.3-20261023
Writes data/release/<release>/scrape_facts.json and docs/hackers-2026-scrape-facts.md.

Any other run (an extract_v2.py re-extraction) is scored with exactly the same checks
and gates by naming its results, one per pass, with the sidecar index that maps each
custom_id to its school's domain:

  49_scrape_facts.py --release v0.3-20261023 \
      --results http=RUN/http/MODEL/results.jsonl --index http=RUN/http/index.json \
      --results browser=RUN/browser/MODEL/results.jsonl --index browser=RUN/browser/index.json \
      --json-out OUT.json --out OUT.md [--rows-out ROWS.jsonl]

The model is read from the results and priced at its batch rates. Every succeeded
custom_id must be in its index, or the script exits 1. --rows-out writes one line per
(pass, school, tail) claim with its check outcome and source URL. --output-dir and
AETHER_DATA move the inputs (output/ and data/) when running outside the checkout
that holds them.
"""

import argparse
import json
import os
import random
import re
import sys
from collections import Counter
from pathlib import Path

import duckdb

from airport_index import canonical_ids

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "output"
DATA = Path(os.environ.get("AETHER_DATA", ROOT / "data"))
RELEASES = DATA / "release"
REGISTRY = DATA / "registry"
DEFAULT_MD = ROOT.parent.parent / "docs" / "hackers-2026-scrape-facts.md"
PASSES = {"http": OUT_DIR / "fleet_extract_results.jsonl", "browser": OUT_DIR / "fleet_browser_extract_results.jsonl"}
PAGE_DIRS = {"http": OUT_DIR / "fleet_html", "browser": OUT_DIR / "fleet_browser_text"}
PRICE_IN, PRICE_OUT = 1.50, 7.50  # $/MTok, Sonnet 4.6 batch
# $/MTok batch rates by model for --results runs; Haiku 5.5 has a second rate card for
# prompts over 100,000 tokens, chosen per request (platform.claude.com pricing, 2026-11-03).
BATCH_PRICES = {
    "claude-sonnet-4-6": [(None, 1.50, 7.50)],
    "claude-sonnet-5-5": [(None, 1.00, 5.00)],
    "claude-haiku-5-5": [(100_000, 0.05, 0.25), (None, 0.25, 1.25)],
}
MODEL_NAMES = {"claude-sonnet-4-6": "Sonnet 4.6", "claude-sonnet-5-5": "Sonnet 5.5", "claude-haiku-5-5": "Haiku 5.5"}


def request_cost(model, tokens_in, tokens_out):
    for limit, pin, pout in BATCH_PRICES[model]:
        if limit is None or tokens_in <= limit:
            return tokens_in / 1e6 * pin + tokens_out / 1e6 * pout
# 14 CFR 47.15: N + 1-5 characters, first a digit 1-9, at most two trailing letters, no I or O.
N_RE = re.compile(r"^N[1-9](\d{0,4}|\d{0,3}[A-HJ-NP-Z]|\d{0,2}[A-HJ-NP-Z]{2})$")
# Website make words -> the FAA manufacturer prefix that should accompany them.
MAKES = {
    "CESSNA": ("CESSNA", "TEXTRON"), "SKYHAWK": ("CESSNA", "TEXTRON"), "SKYLANE": ("CESSNA", "TEXTRON"),
    "PIPER": ("PIPER", "NEW PIPER"), "WARRIOR": ("PIPER", "NEW PIPER"), "ARCHER": ("PIPER", "NEW PIPER"),
    "SENECA": ("PIPER", "NEW PIPER"), "SEMINOLE": ("PIPER", "NEW PIPER"), "CHEROKEE": ("PIPER", "NEW PIPER"),
    "CIRRUS": ("CIRRUS",), "DIAMOND": ("DIAMOND",), "BEECH": ("BEECH", "HAWKER BEECHCRAFT", "RAYTHEON", "TEXTRON"),
    "BEECHCRAFT": ("BEECH", "HAWKER BEECHCRAFT", "RAYTHEON", "TEXTRON"),
    "BONANZA": ("BEECH", "HAWKER BEECHCRAFT", "RAYTHEON", "TEXTRON"),
    "MOONEY": ("MOONEY",), "GRUMMAN": ("GRUMMAN", "AMERICAN GENERAL", "GULFSTREAM AMERICAN", "TIGER"), "ROBINSON": ("ROBINSON",),
    "SLING": ("SLING", "THE AIRPLANE FACTORY"), "TECNAM": ("TECNAM", "COSTRUZIONI"), "PIPISTREL": ("PIPISTREL",),
    "AERONCA": ("AERONCA",), "CHAMPION": ("CHAMPION", "AMERICAN CHAMPION", "BELLANCA"),
    "DECATHLON": ("CHAMPION", "AMERICAN CHAMPION", "BELLANCA"), "CITABRIA": ("CHAMPION", "AMERICAN CHAMPION", "BELLANCA"),
    "SCHWEIZER": ("SCHWEIZER", "SIKORSKY"), "ICON": ("ICON",), "SCHEMPP": ("SCHEMPP-HIRTH",), "ERCOUPE": ("ERCO", "FORNEY", "ALON"),
}


def model_items(passes=PASSES, indexes=None):
    """Every fleet entry the model emitted, keyed by pass, plus recorded token usage.

    usage also carries the models seen and the per-request cost at each model's batch rates.
    """
    items, usage, calls = [], {"in": 0, "out": 0, "cost": 0.0, "models": Counter(), "unindexed": 0}, Counter()
    for name, path in passes.items():
        index = (indexes or {}).get(name)
        for line in path.open():
            r = json.loads(line)
            res = r["result"]
            calls[(name, res["type"])] += 1
            if res["type"] != "succeeded":
                continue
            if index is not None and r["custom_id"] not in index:
                usage["unindexed"] += 1
            u = res["message"]["usage"]
            tin = u["input_tokens"] + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
            usage["in"] += tin
            usage["out"] += u["output_tokens"]
            model = res["message"].get("model") or "claude-sonnet-4-6"
            usage["models"][model] += 1
            usage["cost"] += request_cost(model, tin, u["output_tokens"]) if model in BATCH_PRICES else float("nan")
            text = "".join(b.get("text", "") for b in res["message"]["content"])
            m = re.search(r"\{.*\}", text, re.S)
            try:
                d = json.loads(m.group(0)) if m else {}
            except json.JSONDecodeError:
                calls[(name, "unparseable")] += 1
                continue
            for a in d.get("fleet") or []:
                n = (a.get("n_number") or "").strip()
                if n:
                    items.append({"pass": name, "school": r["custom_id"], "n_raw": n,
                                  "name": a.get("display_name") or "", "airport": (a.get("airport_id") or "").strip(),
                                  "rate": a.get("wet_rate_hr"), "source_url": a.get("source_url") or "",
                                  "domain": (index or {}).get(r["custom_id"], {}).get("domain", "")})
    return items, usage, calls


def normalize(n):
    n = re.sub(r"[\s\-]", "", n.upper())
    return n if n.startswith("N") else "N" + n


# Registry manufacturers that must match exactly: "AMERICAN" alone is a Grumman American
# airframe, but as a prefix it would also take AMERICAN CHAMPION.
EXACT = {"GRUMMAN": {"AMERICAN"}}


def make_agrees(word, mfr):
    mfr = (mfr or "").upper().strip()
    return mfr.startswith(MAKES[word]) or mfr in EXACT.get(word, set())


def make_word(name):
    for w in re.findall(r"[A-Z]+", name.upper()):
        if w in MAKES:
            return w
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", required=True)
    ap.add_argument("--out", type=Path, help=f"Markdown out (default {DEFAULT_MD}; with --results, required)")
    ap.add_argument("--results", action="append", metavar="PASS=FILE", help="score this run instead of March")
    ap.add_argument("--index", action="append", metavar="PASS=FILE", help="sidecar index for a --results pass")
    ap.add_argument("--json-out", type=Path, help="JSON out (default data/release/<release>/scrape_facts.json; "
                                                   "with --results, required)")
    ap.add_argument("--rows-out", type=Path, help="JSONL of every (pass, school, tail) claim and its outcome")
    ap.add_argument("--label", default="", help="--results: what the run is, for the Markdown heading")
    ap.add_argument("--output-dir", type=Path, default=OUT_DIR, help="where the March results and page caches are")
    a = ap.parse_args()
    pair = lambda xs: {k: Path(v) for k, v in (x.split("=", 1) for x in xs or [])}
    run_mode = bool(a.results)
    if run_mode and not (a.out and a.json_out):
        sys.exit("FAIL --results needs --out and --json-out (the defaults are the March facts)")
    passes = pair(a.results) if run_mode else {k: a.output_dir / v.relative_to(OUT_DIR) for k, v in PASSES.items()}
    page_dirs = {k: a.output_dir / v.relative_to(OUT_DIR) for k, v in PAGE_DIRS.items()}
    indexes = None
    if run_mode:
        idx_paths = pair(a.index)
        if set(idx_paths) != set(passes):
            sys.exit(f"FAIL each --results pass needs an --index: {sorted(passes)} vs {sorted(idx_paths)}")
        indexes = {k: {e["custom_id"]: e for e in json.loads(v.read_text())} for k, v in idx_paths.items()}
        page_dirs = {k: Path(next(iter(ix.values())).get("pages_dir") or page_dirs.get(k, ""))
                     for k, ix in indexes.items() if ix}
    rel = RELEASES / a.release
    results = json.loads((rel / "results.json").read_text())
    reg_date = results.get("registry_date") or results.get("registry", {}).get("date")
    missing = [p for p in [*passes.values(), rel / "aircraft.parquet"] if not p.exists()]
    if missing:
        sys.exit(f"FAIL missing inputs: {missing}")

    items, usage, calls = model_items(passes, indexes)
    c = duckdb.connect()
    reg = {r[0]: r[1:] for r in c.sql(
        f"SELECT n_number, mfr, days_airborne, home_airport FROM read_parquet('{rel / 'aircraft.parquet'}')").fetchall()}
    dereg_path = REGISTRY / (reg_date or "") / "dereg.parquet"
    dereg = set()
    if reg_date and dereg_path.exists():
        dereg = {"N" + r[0].strip() for r in c.sql(f"SELECT n_number FROM read_parquet('{dereg_path}')").fetchall()}

    # One row per distinct (pass, school, tail): a tail repeated on several pages of one site counts once.
    seen, rows = set(), []
    for it in items:
        it["n"] = normalize(it["n_raw"])
        key = (it["pass"], it["school"], it["n"])
        if key not in seen:
            seen.add(key)
            rows.append(it)
    tails = {r["n"] for r in rows}
    wellformed = {n for n in tails if N_RE.match(n)}
    on_reg = {n for n in wellformed if n in reg}
    only_dereg = {n for n in wellformed - on_reg if n in dereg}
    flew = {n for n in on_reg if (reg[n][1] or 0) > 0}

    make_rows = [r for r in rows if r["n"] in on_reg and make_word(r["name"])]
    make_ok = [r for r in make_rows if make_agrees(make_word(r["name"]), reg[r["n"]][0])]

    alias = canonical_ids()[0]
    canon = lambda x: alias.get(x.upper(), x.upper()) if x else None
    home_rows = [r for r in rows if r["n"] in flew and r["airport"] and reg[r["n"]][2]]
    match = sum(canon(r["airport"]) == canon(reg[r["n"]][2]) for r in home_rows)
    rng = random.Random(20261103)
    shuffled = [r["airport"] for r in home_rows]
    rng.shuffle(shuffled)
    control = sum(canon(s) == canon(reg[r["n"]][2]) for s, r in zip(shuffled, home_rows))

    # Pages sit under one directory per school domain, so count files, not directories.
    pages = {k: sum(1 for f in d.rglob("*") if f.is_file()) if d.exists() else None for k, d in page_dirs.items()}
    cost = usage["in"] / 1e6 * PRICE_IN + usage["out"] / 1e6 * PRICE_OUT
    out = {
        "release": a.release, "registry_date": reg_date,
        "calls": {f"{k[0]}:{k[1]}": v for k, v in sorted(calls.items())},
        "schools_with_any_tail": len({(r["pass"], r["school"]) for r in rows}),
        "pages_cached": pages,
        "tokens_in_m": round(usage["in"] / 1e6, 2), "tokens_out_m": round(usage["out"] / 1e6, 2),
        "cost_usd_batch": round(cost, 2),
        "tail_rows": len(rows), "distinct_tails": len(tails),
        "wellformed": len(wellformed), "on_registry": len(on_reg), "deregistered_only": len(only_dereg),
        "not_found": len(wellformed - on_reg - only_dereg), "malformed": len(tails - wellformed),
        "flew_in_window": len(flew),
        "make_checked": len(make_rows), "make_agrees": len(make_ok),
        "home_checked": len(home_rows), "home_matches": match, "home_matches_shuffled": control,
        "malformed_examples": sorted(tails - wellformed)[:12],
        "not_found_examples": sorted(wellformed - on_reg - only_dereg)[:12],
    }
    if run_mode:
        models = dict(usage["models"])
        out.update({"results": {k: str(v) for k, v in passes.items()}, "label": a.label, "models": models,
                    "cost_usd_batch": round(usage["cost"], 2), "unindexed_custom_ids": usage["unindexed"],
                    "home_match_rate": round(match / len(home_rows), 4) if home_rows else None,
                    "home_shuffled_rate": round(control / len(home_rows), 4) if home_rows else None})
    (a.json_out or rel / "scrape_facts.json").write_text(json.dumps(out, indent=2) + "\n")
    if a.rows_out:
        def outcome(n):
            if n not in wellformed:
                return "malformed"
            return "registered" if n in on_reg else "deregistered" if n in only_dereg else "not_found"
        with open(a.rows_out, "w") as f:
            for r in rows:
                f.write(json.dumps({k: r[k] for k in ("pass", "school", "domain", "n_raw", "n", "name", "airport",
                                                      "source_url")} | {"outcome": outcome(r["n"])}) + "\n")

    p = lambda x, y: f"{100 * x / y:.1f}%" if y else "n/a"
    o = out
    body = f"""## What the model said, against the registry and the radios

- {o['tail_rows']:,} (school, tail) claims; {o['distinct_tails']:,} distinct tails, from {o['schools_with_any_tail']:,} school extractions with at least one tail (a site read by both passes counts twice).
- Well-formed N-numbers: {o['wellformed']:,} ({p(o['wellformed'], o['distinct_tails'])}). Malformed examples: {', '.join(o['malformed_examples']) or 'none'}.
- On the FAA registry of {reg_date}: {o['on_registry']:,} ({p(o['on_registry'], o['wellformed'])} of well-formed). A further {o['deregistered_only']:,} are deregistered (sold abroad, destroyed, or lapsed since the crawl). Not found anywhere: {o['not_found']:,} ({p(o['not_found'], o['wellformed'])}), e.g. {', '.join(o['not_found_examples'])}.
- Make named on the website agrees with the registry's manufacturer: {o['make_agrees']:,} of {o['make_checked']:,} checked ({p(o['make_agrees'], o['make_checked'])}).
- Flew in the release window: {o['flew_in_window']:,} ({p(o['flew_in_window'], o['on_registry'])} of registered).
- Lives where the website says: {o['home_matches']:,} of {o['home_checked']:,} with both a website airport and an inferred home base ({p(o['home_matches'], o['home_checked'])}). Negative control, website airports shuffled across the same tails: {p(o['home_matches_shuffled'], o['home_checked'])}.

Caveats: a tail that is real and registered can still be a stock photo or a plane the school sold; "not found" includes typos on the school's own page as well as model misreads, and this check cannot tell them apart without opening the page.
"""
    if run_mode:
        names = ", ".join(f"{MODEL_NAMES.get(m, m)} ({n:,} calls)" for m, n in sorted(o["models"].items()))
        args = " ".join(f"--results {k}=... --index {k}=..." for k in passes)
        md = f"""# Scrape facts: {a.label or names}

Generated by `research/FlightSchoolMapping/scripts/49_scrape_facts.py --release {a.release} {args}`, with the same checks and gates as the March facts. Regenerate rather than edit.

## The work

- Model: {names}. {sum(v for k, v in o['calls'].items() if k.endswith(':succeeded')):,} extraction calls succeeded ({', '.join(f'{k} {v:,}' for k, v in o['calls'].items())}).
- Pages cached for the model to read: {'; '.join(f'{k} {v:,} files' for k, v in o['pages_cached'].items() if v is not None)}.
- Tokens: {o['tokens_in_m']}M in, {o['tokens_out_m']}M out. Recorded usage at batch prices: **${o['cost_usd_batch']:,.2f}**.

""" + body
    else:
        md = f"""# Hackers 2026: what the model read off flight-school websites, checked

Generated by `research/FlightSchoolMapping/scripts/49_scrape_facts.py --release {a.release}`. Regenerate rather than edit.
Raw model output from the HTTP (09b) and browser (09c) crawl passes, March 2026, before any cleanup. The corpus-expansion pass (09f) is not included: it recorded no token usage.

## The work

- {sum(v for k, v in o['calls'].items() if k.endswith(':succeeded')):,} extraction calls succeeded ({', '.join(f'{k} {v:,}' for k, v in o['calls'].items())}).
- Pages cached for the model to read: {o['pages_cached']['http']:,} HTTP-crawled files and {o['pages_cached']['browser']:,} browser-rendered texts.
- Tokens: {o['tokens_in_m']}M in, {o['tokens_out_m']}M out. At Sonnet 4.6 batch prices that is **${o['cost_usd_batch']:,.0f}**. That is a floor: it leaves out the expansion pass, rate-sheet and photo passes, and any runs whose results were overwritten.

""" + body
    (a.out or DEFAULT_MD).write_text(md)
    print(md)

    fails = []
    if o["distinct_tails"] < 1000:
        fails.append(f"only {o['distinct_tails']} distinct tails")
    if o["home_checked"] < 500:
        fails.append(f"home check too small ({o['home_checked']})")
    elif o["home_matches_shuffled"] * 3 >= o["home_matches"]:  # also fails 0 vs 0
        fails.append("negative control within 3x of the real home-base match")
    if usage["unindexed"]:
        fails.append(f"{usage['unindexed']} succeeded custom_ids are not in their index")
    if fails:
        sys.exit("FAIL " + "; ".join(fails))
    print("PASS")


if __name__ == "__main__":
    main()
