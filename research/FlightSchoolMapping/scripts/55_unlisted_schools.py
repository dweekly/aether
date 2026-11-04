#!/usr/bin/env python3
"""Workstream 4 gate: are the 372 sky-found training operations real, with web presence?

Inputs (all under data/research-2026-11/unlisted/ in the production checkout):
  discovery.jsonl      55a_unlisted_discover.py: website, evidence, operation type
  crawl/               55b_unlisted_crawl.py: 09a HTTP crawl and 10_ browser crawl
  extract/http|browser extract_v2.py runs on that crawl, Claude Haiku 5.5 at effort medium

For every operation with a website, two independent counts of how many of the
registrant's tails (every aircraft the FAA registry lists for that registrant, not only
the training-like ones) appear on the site:
  text      the N-number, as written on any crawled page (N12345, N-12345, N 12345)
  extract   the n_number of any aircraft extract_v2 read off the site
A site with at least one is confirmed; a site with none is reported as unconfirmed, and
a site that could not be fetched as unreachable. Neither is a match.

Negative control: each site is also searched for the tails of a different operation
(one whose site has another registrable domain); those hits should be near zero.

Exits non-zero if the 372 cannot be rebuilt, an operation has no discovery result, a
found site has no crawl record, a crawled site with page text has no extraction result,
or the control finds more than a fifth as many confirmations as the real pairing.

Writes scores.json and summary.json next to the inputs, and with --doc the Markdown
report.

Usage: 55_unlisted_schools.py [--doc docs/hackers-2026-unlisted-operations.md]
"""

import argparse
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import unlisted_ops as U  # noqa: E402

TYPES = ["school", "club", "leaseback_llc", "charter", "aerial_work", "government", "other", "unknown"]
TAIL_RE = re.compile(r"(?<![A-Z0-9])N[\s-]?([1-9][0-9A-Z]{0,4})(?![A-Z0-9])")
EXTRACT_MODEL = "claude-haiku-5-5"
EXTRACT_DIR = "claude-haiku-5-5-effort-medium"  # 55b_unlisted_crawl.py runs extract_v2 at effort medium
CONTROL_MAX = 0.2


def fail(msg):
    print(f"FAIL {msg}")
    sys.exit(1)


def site_key(url):
    """Registrable domain, US-centric: the last two labels (three under .us)."""
    d = U.domain(url)
    parts = d.split(".")
    n = 3 if parts[-1] == "us" and len(parts) >= 3 else 2
    return ".".join(parts[-n:])


def norm_tail(s):
    t = re.sub(r"[\s-]", "", (s or "").upper())
    if not t:
        return ""
    return t if t.startswith("N") else "N" + t


def page_tails(text):
    return {"N" + m.group(1) for m in TAIL_RE.finditer(text.upper())}


def registrant_tails(registrants):
    c = duckdb.connect()
    rows = c.execute(f"SELECT registrant, n_number FROM read_parquet('{U.REL / 'aircraft.parquet'}') "
                     "WHERE registrant IN (SELECT unnest(?))", [sorted(registrants)]).fetchall()
    out = defaultdict(set)
    for reg, n in rows:
        out[reg].add(n)
    return out


def load_crawl(crawl):
    """website -> {"status", "texts": [page text...], "domain"} from the HTTP and browser crawls."""
    sites = {}
    http = json.loads((crawl / "fleet_crawl_index.json").read_text()) if (crawl / "fleet_crawl_index.json").exists() else []
    for e in http:
        texts = []
        for p in e.get("pages", []):
            f = crawl / "fleet_html" / p["html_path"]
            if f.exists():
                texts.append(f.read_text(encoding="utf-8", errors="replace"))
            raw = crawl / "fleet_html_raw" / p["html_path"]
            if raw.exists():  # tails sometimes sit in alt text or attributes the cleaner strips
                texts.append(raw.read_text(encoding="utf-8", errors="replace"))
        sites[e["school_website"]] = {"http_flags": e.get("crawl_flags", []), "http_pages": len(e.get("pages", [])),
                                      "texts": texts, "browser_pages": 0}
    bpath = crawl / "fleet_browser_index.json"
    for e in (json.loads(bpath.read_text()) if bpath.exists() else []):
        s = sites.setdefault(e["school_website"], {"http_flags": ["http_failed"], "http_pages": 0, "texts": [],
                                                   "browser_pages": 0})
        s["browser_flags"] = e.get("crawl_flags", [])
        s["browser_pages"] = len(e.get("pages", []))
        for p in e.get("pages", []) + e.get("pdfs", []):
            if p.get("text"):
                s["texts"].append(p["text"])
            tp = p.get("text_path")
            if tp and (crawl / "fleet_browser_text" / tp).exists():
                s["texts"].append((crawl / "fleet_browser_text" / tp).read_text(encoding="utf-8", errors="replace"))
    return sites


def load_extractions(extract_dir):
    """registrable domain -> set of normalized n_numbers, plus the set of domains with a result."""
    tails, scored, requested = defaultdict(set), set(), set()
    for layout in ("http", "browser"):
        run = extract_dir / layout
        if not (run / "index.json").exists():
            continue
        idx = {i["custom_id"]: i for i in json.loads((run / "index.json").read_text())}
        requested |= {site_key(i["school_website"]) for i in idx.values()}
        res = run / EXTRACT_DIR / "results.jsonl"
        if not res.exists():
            continue
        for line in res.open():
            r = json.loads(line)
            meta = idx.get(r["custom_id"])
            if not meta or r["result"]["type"] != "succeeded":
                continue
            msg = r["result"]["message"]
            text = next((b["text"] for b in msg["content"] if b["type"] == "text"), "")
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            k = site_key(meta["school_website"])
            scored.add(k)
            for f in data.get("fleet", []):
                t = norm_tail(f.get("n_number"))
                if t:
                    tails[k].add(t)
    return tails, scored, requested


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", type=Path)
    a = ap.parse_args()
    try:
        ops = U.rebuild()
    except U.RebuildError as e:
        fail(f"rebuild: {e}")
    print(f"rebuilt {len(ops)} operations, identical to training.json")

    disc = {}
    for line in (U.OUT / "discovery.jsonl").open():
        d = json.loads(line)
        disc[d["registrant"]] = d
    no_result = [o["registrant"] for o in ops if not (disc.get(o["registrant"]) or {}).get("result")]
    if no_result:
        fail(f"{len(no_result)} operations have no discovery result, e.g. {no_result[:5]}")
    spend_discovery = 0.0
    for line in (U.OUT / "discovery.jsonl").open():
        spend_discovery += json.loads(line)["usd"]

    crawl = U.OUT / "crawl"
    sites = load_crawl(crawl)
    ex_tails, ex_scored, ex_requested = load_extractions(U.OUT / "extract")
    reg_tails = registrant_tails({o["registrant"] for o in ops})
    march = defaultdict(set)
    for d, srcs in U.march_domains().items():
        march[site_key(d)] |= srcs

    rows, problems = [], []
    for o in ops:
        r = disc[o["registrant"]]["result"]
        row = {"registrant": o["registrant"], "aircraft": o["aircraft"], "home": o["homes"][0][0] if o["homes"] else "",
               "operation_type": r["operation_type"], "confidence": r["confidence"], "website": r["website"],
               "website_relation": r["website_relation"], "operator_name": r["operator_name"],
               "evidence": r["evidence"], "registered_tails": len(reg_tails[o["registrant"]])}
        if r["website"]:
            site = r["website"].strip()
            site = site if "://" in site else "https://" + site
            k = site_key(site)
            row["domain"] = k
            row["in_march_list"] = sorted(march.get(k, ()))
            s = sites.get(site)
            if s is None:
                problems.append(f"no crawl record for {site} ({o['registrant']})")
                row["status"] = "not_crawled"
                rows.append(row)
                continue
            text_tails = set().union(*(page_tails(t) for t in s["texts"])) if s["texts"] else set()
            has_text = any(t.strip() for t in s["texts"])
            if has_text and k in ex_requested and k not in ex_scored:
                problems.append(f"extraction requested but no result for {k} ({o['registrant']})")
            if has_text and k not in ex_requested:
                problems.append(f"crawled text but no extraction request for {k} ({o['registrant']})")
            mine = reg_tails[o["registrant"]]
            row["tails_in_text"] = sorted(mine & text_tails)
            row["tails_in_extract"] = sorted(mine & ex_tails.get(k, set()))
            row["flagged_tails_on_site"] = sorted(set(o["tails"]) & (text_tails | ex_tails.get(k, set())))
            row["site_tails_total"] = len(text_tails | ex_tails.get(k, set()))
            row["crawl"] = {kk: s[kk] for kk in ("http_flags", "http_pages", "browser_pages") if kk in s}
            n = len(set(row["tails_in_text"]) | set(row["tails_in_extract"]))
            row["tails_on_site"] = n
            row["status"] = "confirmed" if n else ("unconfirmed" if has_text else "unreachable")
            row["_text_tails"] = text_tails | ex_tails.get(k, set())
        else:
            row["status"] = "no_website"
        rows.append(row)
    if problems:
        for p in problems[:20]:
            print("  " + p)
        fail(f"{len(problems)} found sites are unscored")

    # Negative control: another operation's tails against this site (different registrable domain).
    with_site = [r for r in rows if "_text_tails" in r]
    rng = random.Random(20261107)
    control_hits = 0
    for r in with_site:
        others = [x for x in with_site if x["domain"] != r["domain"]]
        other = rng.choice(others)
        if reg_tails[other["registrant"]] & r["_text_tails"]:
            control_hits += 1
    real_hits = sum(r["status"] == "confirmed" for r in rows)
    for r in rows:
        r.pop("_text_tails", None)

    by_type = {}
    for t in TYPES:
        rs = [r for r in rows if r["operation_type"] == t]
        by_type[t] = {"operations": len(rs), "aircraft": sum(r["aircraft"] for r in rs),
                      "website": sum(bool(r["website"]) for r in rs),
                      "confirmed": sum(r["status"] == "confirmed" for r in rs),
                      "unconfirmed": sum(r["status"] == "unconfirmed" for r in rs),
                      "unreachable": sum(r["status"] == "unreachable" for r in rs)}
    sc = [r for r in rows if r["status"] == "confirmed" and r["operation_type"] in ("school", "club")]
    missed = [r for r in sc if not r["in_march_list"]]
    known = [r for r in sc if r["in_march_list"]]
    summary = {
        "operations": len(rows), "aircraft": sum(r["aircraft"] for r in rows),
        "with_website": sum(bool(r["website"]) for r in rows),
        "distinct_sites": len({r["domain"] for r in rows if r.get("domain")}),
        "confirmed": real_hits,
        "unconfirmed": sum(r["status"] == "unconfirmed" for r in rows),
        "unreachable": sum(r["status"] == "unreachable" for r in rows),
        "no_website": sum(r["status"] == "no_website" for r in rows),
        "by_type": by_type,
        "confirmed_school_or_club": len(sc),
        "confirmed_school_or_club_sites": len({r["domain"] for r in sc}),
        "confirmed_school_or_club_missed_by_march": len(missed),
        "confirmed_school_or_club_missed_by_march_sites": len({r["domain"] for r in missed}),
        "confirmed_school_or_club_site_in_march_list": len(known),
        "confirmed_aircraft": sum(r["aircraft"] for r in rows if r["status"] == "confirmed"),
        "confirmed_by_text_only": sum(1 for r in rows if r.get("tails_in_text") and not r.get("tails_in_extract")),
        "confirmed_by_extract_only": sum(1 for r in rows if r.get("tails_in_extract") and not r.get("tails_in_text")),
        "control": {"sites": len(with_site), "hits": control_hits, "real_hits": real_hits},
        "spend_usd": {"discovery": round(spend_discovery, 2)},
    }
    for layout in ("http", "browser"):
        u = U.OUT / "extract" / layout / EXTRACT_DIR / "usage.json"
        if u.exists():
            summary["spend_usd"][f"extract_{layout}"] = json.loads(u.read_text())["cost_usd_batch"]
    (U.OUT / "scores.json").write_text(json.dumps(rows, indent=1) + "\n")
    (U.OUT / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    if real_hits and control_hits > CONTROL_MAX * real_hits:
        fail(f"negative control confirms {control_hits} sites against {real_hits} real")
    if a.doc:
        a.doc.write_text(render(summary, rows))
        print(f"wrote {a.doc}")
    print("PASS")


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "-"


def render(s, rows):
    bt = s["by_type"]
    L = []
    w = L.append
    w("# Hackers 2026: the 372 training operations missing from our school list")
    w("")
    w("Generated by `research/FlightSchoolMapping/scripts/55_unlisted_schools.py --doc` from "
      "`data/research-2026-11/unlisted/` (workstream 4 of [the research plan](hackers-2026-research-plan.md)). "
      "Regenerate rather than edit.")
    w("")
    w("## What was done")
    w("")
    w(f"- The list: {s['operations']} operations ({s['aircraft']:,} aircraft): non-individual FAA registrants with at least "
      "three aircraft that fly like trainers (training score >= 0.7) and no tail on any school fleet we know "
      "(`40_training_detect.py`, release v0.3-20261023). Rebuilt from `training_scores.parquet`, `aircraft.parquet` and "
      "`schools_full.json`; identical, tail for tail, to `training.json`.")
    w("- Discovery: Claude Sonnet 5.5 with the server-side web search tool, given the registrant's name, type and "
      "address, its home airports, main models and up to twelve tails, returned a website (the registrant's own, or "
      "the school or club that flies a leasing entity's aircraft), its evidence and an operation type "
      "(`55a_unlisted_discover.py`).")
    w("- Crawl: every website found, with the March HTTP crawler (`09a_fleet_crawl.py --input --output-dir`), and the "
      "March Playwright crawler (`10_fleet_browser_crawl.py --crawl-only --output-dir`) for every site the HTTP crawl "
      "flagged or could not fetch (`55b_unlisted_crawl.py`).")
    w("- Extraction: `extract_v2.py` (the March prompt, unchanged) with Claude Haiku 5.5 at effort medium on the Message Batches API.")
    w("- Scoring: for each site, how many of the registrant's tails (every aircraft the registry lists for it) appear "
      "on the crawled pages as written, or among the aircraft the extraction read off them. At least one is "
      "**confirmed**. A site with page text and none is **unconfirmed**, not a match; a site that returned no page "
      "text is **unreachable**.")
    w("")
    w("## Headline")
    w("")
    w(f"- {s['with_website']} of {s['operations']} operations ({pct(s['with_website'], s['operations'])}) have a website "
      f"the search could tie to them ({s['distinct_sites']} distinct sites).")
    w(f"- {s['confirmed']} ({pct(s['confirmed'], s['operations'])} of all {s['operations']}) are confirmed: at least one "
      f"of the registrant's own tails is on the site. {s['unconfirmed']} sites list none of them; "
      f"{s['unreachable']} could not be read.")
    w(f"- Confirmed schools and clubs: {s['confirmed_school_or_club']} operations on "
      f"{s['confirmed_school_or_club_sites']} sites. **{s['confirmed_school_or_club_missed_by_march']} of them "
      f"({s['confirmed_school_or_club_missed_by_march_sites']} sites) were missed entirely by the March web "
      "search**: their domain is in none of the March directory, AOPA, Cirrus, expansion or crawl lists. The other "
      f"{s['confirmed_school_or_club_site_in_march_list']} were on a site we already knew, whose March fleet "
      "extraction did not carry these tails.")
    w(f"- Negative control: each site searched for a different operation's tails confirms {s['control']['hits']} of "
      f"{s['control']['sites']} (real pairing: {s['control']['real_hits']}).")
    w("")
    w("## By operation type")
    w("")
    w("Type is the discovery model's call from what it found, not a hand check.")
    w("")
    w("| Type | Operations | Aircraft | Website | Confirmed | Unconfirmed | Unreachable |")
    w("|---|---:|---:|---:|---:|---:|---:|")
    for t, v in bt.items():
        if v["operations"]:
            w(f"| {t} | {v['operations']} | {v['aircraft']} | {v['website']} | {v['confirmed']} | {v['unconfirmed']} "
              f"| {v['unreachable']} |")
    w(f"| **all** | {s['operations']} | {s['aircraft']} | {s['with_website']} | {s['confirmed']} | {s['unconfirmed']} "
      f"| {s['unreachable']} |")
    w("")
    w("## How the confirmations split")
    w("")
    w(f"- Tails found by text match only: {s['confirmed_by_text_only']}; by extraction only: "
      f"{s['confirmed_by_extract_only']}; the rest by both.")
    w(f"- Aircraft in confirmed operations: {s['confirmed_aircraft']:,}.")
    w("")
    w("## The largest confirmed operations missed by the March search")
    w("")
    w("| Registrant | Home | Aircraft | Type | Site | Tails on site |")
    w("|---|---|---:|---|---|---:|")
    top = [r for r in rows if r["status"] == "confirmed" and r["operation_type"] in ("school", "club")
           and not r["in_march_list"]][:25]
    for r in top:
        w(f"| {r['registrant']} | {r['home']} | {r['aircraft']} | {r['operation_type']} | {r['domain']} | "
          f"{r['tails_on_site']} of {r['registered_tails']} |")
    w("")
    w("## Cost")
    w("")
    sp = s["spend_usd"]
    w("- " + ", ".join(f"{k}: ${v:.2f}" for k, v in sp.items()) + f"; total ${sum(sp.values()):.2f}. Discovery is "
      "recorded usage at list prices plus $10 per 1,000 searches; extraction is recorded usage at batch prices.")
    w("")
    w("## Caveats")
    w("")
    w("- Unconfirmed is not wrong: many school sites list makes and models without tails. It means this check could not "
      "tie the site to the registrant's aircraft.")
    w("- A confirmed site can be the operating school for a leasing LLC (`website_relation` in `scores.json`); the "
      "operation is then real training, but the registrant is not itself a school.")
    w("- Operation types and websites come from one model pass over web search results; the evidence text and the "
      "URLs the search returned are kept per operation in `discovery.jsonl`.")
    w("- \"Missed by the March search\" compares registrable domains (the last two labels) against every website "
      "the March directory, AOPA, Cirrus, expansion and crawl lists held.")
    w("")
    return "\n".join(L)


if __name__ == "__main__":
    main()
