#!/usr/bin/env python3
"""
50a_recrawl_targets.py — the March 2026 crawl's target lists, for a re-crawl.

Rebuilds, from the March crawl's own indexes and caches, the list of sites each
March crawler visited, in the input format each crawler takes, so a re-crawl
visits the same sites:

  targets_http.json       09a_fleet_crawl.py --targets      (name, website, ...)
      every school in fleet_crawl_index.json (09a, with the AOPA targeted crawl
      merged in) and _aopa_extract_index.json (the AOPA targeted crawl's own
      index), one record per distinct website URL
  targets_browser.json    10_fleet_browser_crawl.py --targets (school_name, school_website, ...)
      every school in fleet_browser_index.json and _aopa_browser_targets.json,
      plus any domain in fleet_browser_text/ neither list names (https://domain/)
  targets_expansion.json  09f_corpus_expansion.py --targets  (name, domain, website, ...)
      every domain in expansion_crawl.jsonl, its website taken from
      aopa_flight_schools.json as 09f's build_targets() did
  march_domains.json      {domain: [lists it was in]}: the denominator of the
      re-crawl's coverage gate (domains attempted >= 90% of March's)

Usage:
    python 50a_recrawl_targets.py --march ../output --out ../data/research-2026-11/recrawl

Exits non-zero if a March input is missing or a list comes out empty.
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse


def norm_domain(url: str) -> str:
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    d = urlparse(url).netloc.lower().split(":")[0]
    return d[4:] if d.startswith("www.") else d


def load(path: Path):
    if not path.exists():
        sys.exit(f"missing March input: {path}")
    if path.suffix == ".jsonl":
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    return json.loads(path.read_text())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--march", required=True, help="the March crawl's output/ directory (read only)")
    ap.add_argument("--out", required=True, help="where to write the target lists")
    args = ap.parse_args()
    march, out = Path(args.march), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    march_domains: dict[str, set[str]] = {}

    def mark(domain: str, label: str) -> None:
        if domain:
            march_domains.setdefault(domain, set()).add(label)

    # ── HTTP (09a) ──
    http: list[dict] = []
    seen_web: set[str] = set()
    for fname in ("fleet_crawl_index.json", "_aopa_extract_index.json"):
        for e in load(march / fname):
            web = (e.get("school_website") or "").strip()
            if not web or web in seen_web:
                continue
            seen_web.add(web)
            http.append({
                "name": e.get("school_name", ""),
                "website": web,
                "airport_code": e.get("known_airport", ""),
                "city": e.get("city", ""),
                "state_name": e.get("state", ""),
            })
            mark(norm_domain(web), "http")

    # ── Browser (10) ──
    meta: dict[str, dict] = {}
    for e in load(march / "fleet_crawl_index.json"):
        meta.setdefault(norm_domain(e.get("school_website", "")), e)
    browser: list[dict] = []
    seen_dom: set[str] = set()
    for fname in ("fleet_browser_index.json", "_aopa_browser_targets.json"):
        for e in load(march / fname):
            web = (e.get("school_website") or "").strip()
            d = norm_domain(web)
            if not d or d in seen_dom:
                continue
            seen_dom.add(d)
            browser.append({k: e.get(k, "") for k in
                            ("school_name", "school_website", "known_airport", "city", "state")})
            mark(d, "browser")
    text_dir = march / "fleet_browser_text"
    if not text_dir.is_dir():
        sys.exit(f"missing March input: {text_dir}")
    extra = 0
    for sub in sorted(text_dir.iterdir()):
        d = norm_domain(sub.name)
        if not sub.is_dir() or d in seen_dom:
            continue
        seen_dom.add(d)
        m = meta.get(d, {})
        browser.append({
            "school_name": m.get("school_name", d),
            "school_website": m.get("school_website") or f"https://{sub.name}/",
            "known_airport": m.get("known_airport", ""),
            "city": m.get("city", ""),
            "state": m.get("state", ""),
        })
        mark(d, "browser")
        extra += 1

    # ── Expansion (09f) ──
    aopa_web: dict[str, dict] = {}
    for s in load(march / "aopa_flight_schools.json"):
        w = s.get("website", "")
        if not w:
            continue
        if not w.startswith("http"):
            w = "https://" + w
        # 09f's build_targets() keyed on netloc minus "www." without lowercasing first;
        # norm_domain lowercases, which only merges case variants of one host.
        aopa_web.setdefault(norm_domain(w), {**s, "website": w})
    expansion: list[dict] = []
    for rec in load(march / "expansion_crawl.jsonl"):
        d = (rec.get("domain") or "").lower()
        if not d:
            continue
        s = aopa_web.get(norm_domain(d), {})
        expansion.append({
            "name": rec.get("name") or s.get("name", ""),
            "domain": d,
            "website": s.get("website") or f"https://{d}",
            "airport_id": s.get("airport_id", ""),
            "city": s.get("city", ""),
            "state": s.get("state", ""),
            "aopa_type": s.get("aopa_type", ""),
            "aopa_url": s.get("aopa_url", ""),
        })
        mark(norm_domain(d), "expansion")
    no_web = sum(1 for t in expansion if norm_domain(t["website"]) not in aopa_web)

    for name, rows in (("targets_http.json", http), ("targets_browser.json", browser),
                       ("targets_expansion.json", expansion)):
        if not rows:
            sys.exit(f"{name}: empty")
        (out / name).write_text(json.dumps(rows, indent=1))
    (out / "march_domains.json").write_text(
        json.dumps({d: sorted(v) for d, v in sorted(march_domains.items())}, indent=1))

    print(f"HTTP targets:      {len(http):,} websites")
    print(f"Browser targets:   {len(browser):,} domains ({extra} only in fleet_browser_text/)")
    print(f"Expansion targets: {len(expansion):,} domains ({no_web} with no AOPA website, https://domain used)")
    print(f"March domains (union, normalized): {len(march_domains):,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
