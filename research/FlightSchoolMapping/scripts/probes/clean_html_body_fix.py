#!/usr/bin/env python3
"""Before/after for the 09a clean_html fix (never decompose <html> or <body>).

Picks cached March HTTP pages whose <body> class or id matches 09a's boilerplate
filter and whose March cleaned copy (output/fleet_html/, what 09b sent the model)
kept under 5% of the raw page's visible text, then re-cleans the raw page
(output/fleet_html_raw/) with the current clean_html and prints visible-text lengths:
raw, March cleaned, fixed cleaned.

Exit 1 unless every picked page is no longer emptied after the fix: it keeps at least 5%
of its raw text, the audit's line (docs/hackers-2026-extraction-audit.md).

Negative control: --crawler pointed at the pre-fix 09a (git show 187743b:<path>) must FAIL.

Usage: clean_html_body_fix.py [--output OUTPUT_DIR] [--n 8] [--crawler 09a_fleet_crawl.py]
"""

import argparse
import html as htmllib
import importlib.util
import json
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent


def load_crawler(path):
    spec = importlib.util.spec_from_file_location("crawl09a", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

BOILER = re.compile(r"(cookie|gdpr|consent|popup|modal|overlay|sidebar|widget|social|share|"
                    r"newsletter|subscribe|banner|advertisement|ad-|advert)", re.I)  # 09a's filter


def text_len(h):
    h = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", h, flags=re.S | re.I)
    return len(re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", h))).strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, default=SCRIPTS.parent / "output")
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--crawler", type=Path, default=SCRIPTS / "09a_fleet_crawl.py")
    a = ap.parse_args()
    crawl = load_crawler(a.crawler)
    index = json.loads((a.output / "fleet_crawl_index.json").read_text())
    rows = []
    for s in sorted(index, key=lambda s: s.get("school_website") or ""):
        for p in s.get("pages", []):
            fc, fr = a.output / "fleet_html" / p["html_path"], a.output / "fleet_html_raw" / p["html_path"]
            if not (fc.exists() and fr.exists()):
                continue
            raw = fr.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"<body([^>]*)>", raw[:200_000], re.I)
            if not m:
                continue
            attrs = " ".join(re.findall(r'(?:class|id)\s*=\s*["\']([^"\']*)', m.group(1)))
            hit = BOILER.search(attrs)
            raw_t = text_len(raw)
            march_t = text_len(fc.read_text(encoding="utf-8", errors="replace"))
            if not hit or raw_t < 2000 or march_t >= 0.05 * raw_t:
                continue
            rows.append((p["html_path"], hit.group(0), raw_t, march_t, text_len(crawl.clean_html(raw))))
            break  # one page per school
        if len(rows) >= a.n:
            break
    print(f"{'page':60} {'body match':>10} {'raw':>7} {'March':>7} {'fixed':>7} {'kept':>5}")
    for path, hit, raw_t, march_t, fixed_t in rows:
        print(f"{path[:60]:60} {hit:>10} {raw_t:>7,} {march_t:>7,} {fixed_t:>7,} {fixed_t / raw_t:>5.0%}")
    bad = [r for r in rows if r[4] < 0.05 * r[2]]
    if len(rows) < a.n or bad:
        print(f"FAIL: {len(rows)} pages found, {len(bad)} still under 5% of their raw text")
        sys.exit(1)
    print(f"PASS: all {len(rows)} pages keep at least 5% of their raw text after the fix")


if __name__ == "__main__":
    main()
