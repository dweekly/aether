#!/usr/bin/env python3
"""Error audit of the bad tails in the March raw extraction (Hackers 2026, workstream 5).

49_scrape_facts.py finds tails the model reported that are malformed N-numbers or are
well-formed but on neither the FAA registry nor the deregistered file. This script
finds, for each such tail, the cached page it came from and the text around it, so a
person can classify it, and then checks the classification file covers every tail.

  1. 49_scrape_facts.py --release v0.3-20261023 --rows-out ROWS.jsonl   (March rows)
  2. 50_error_audit.py evidence --rows ROWS.jsonl --output-dir OUTPUT --out EVIDENCE.jsonl
     For each bad (pass, school, tail) claim: the school's domain (browser: the March
     index; http: the domain of the model's source_url for that school, since the March
     HTTP index positions no longer match today's crawl index), every cached page of
     that domain where the raw string appears (case-insensitive, spaces and hyphens
     optional), and a snippet. Where the raw string is on no page, near matches: the
     same characters with one edit (insert, delete, substitute) or with O/0, I/1 swapped.
  3. Classify by hand into docs/hackers-2026-error-audit.csv
     (tail,outcome,class,subtype,raw,pass,domain,url,note). Classes:
       page_typo      the school's own page (or its URL) shows the bad string, so the model
                      copied it faithfully; the note names a registered one-edit variant of
                      the same make and model where there is one
       model_misread  the page shows something else and the model garbled or invented it
       non_us_or_sim  the page shows a non-US registration (subtype canada) or a simulator
                      ID (subtype simulator), not a US aircraft
       antique_nc     a vintage NC marking whose N-number is registered: correct reading,
                      rejected only by the format check
       unclear        the page shows the string verbatim but nothing settles whether it is
                      a typo or a number newer than, or missing from, the registry
  4. 50_error_audit.py check --rows ROWS.jsonl --classes docs/hackers-2026-error-audit.csv
     Exits 1 unless every bad tail has a class from the list and a URL; prints the table.
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

CLASSES = ["page_typo", "model_misread", "non_us_or_sim", "antique_nc", "unclear"]
BAD = ("malformed", "not_found")


def dom(u):
    try:
        d = urlparse(u if "://" in u else "https://" + u).netloc.lower().split(":")[0]
    except ValueError:
        return ""
    return d[4:] if d.startswith("www.") else d


def bad_tails(rows_path):
    rows = [json.loads(l) for l in open(rows_path)]
    return rows, [r for r in rows if r["outcome"] in BAD]


def pattern(raw):
    """The raw string with optional spaces/hyphens between characters, case-insensitive."""
    chars = [re.escape(c) for c in re.sub(r"[\s\-]", "", raw)]
    return re.compile(r"(?<![A-Za-z0-9])" + r"[\s\-]?".join(chars) + r"(?![A-Za-z0-9])", re.I)


def variants(raw):
    s = re.sub(r"[\s\-]", "", raw.upper())
    alpha = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789"
    out = set()
    for i in range(len(s) + 1):
        for c in alpha:
            out.add(s[:i] + c + s[i:])
    for i in range(len(s)):
        out.add(s[:i] + s[i + 1:])
        for c in alpha:
            out.add(s[:i] + c + s[i + 1:])
    swap = {"O": "0", "0": "O", "I": "1", "1": "I"}
    out |= {s[:i] + swap[ch] + s[i + 1:] for i, ch in enumerate(s) if ch in swap}
    out.discard(s)
    return {v for v in out if len(v) >= 3}


def cmd_evidence(a):
    out_dir = Path(a.output_dir)
    rows, bad = bad_tails(a.rows)
    browser_idx = {e["custom_id"]: e["domain"] for e in json.loads((out_dir / "fleet_browser_extract_index.json").read_text())}
    # http: the domain of the school's own source_urls (all its claims, not only the bad one).
    school_doms = defaultdict(Counter)
    for r in rows:
        if r["source_url"]:
            school_doms[(r["pass"], r["school"])][dom(r["source_url"])] += 1
    url_of = {}
    for s in json.loads((out_dir / "fleet_crawl_index.json").read_text()):
        for p in s.get("pages", []):
            url_of[p["html_path"]] = p["url"]
    tok = re.compile(r"[A-Za-z0-9]{3,8}")
    with open(a.out, "w") as f:
        for r in bad:
            if r["pass"] == "browser":
                d = browser_idx.get(r["school"], "")
                root = out_dir / "fleet_browser_text"
            else:
                c = school_doms.get((r["pass"], r["school"]))
                d = c.most_common(1)[0][0] if c else dom(r["source_url"])
                root = out_dir / "fleet_html"
            files = sorted((root / d).glob("*")) if d and (root / d).is_dir() else []
            pat, near = pattern(r["n_raw"]), variants(r["n_raw"])
            hits, nears = [], Counter()
            for fp in files:
                text = fp.read_text(encoding="utf-8", errors="replace")
                for m in pat.finditer(text):
                    if len(hits) < 4:
                        hits.append({"file": f"{d}/{fp.name}", "url": url_of.get(f"{d}/{fp.name}", ""),
                                     "snippet": re.sub(r"\s+", " ", text[max(0, m.start() - 90): m.end() + 90])})
                for t in tok.findall(text):
                    if t.upper() in near:
                        nears[(t.upper(), f"{d}/{fp.name}")] += 1
            f.write(json.dumps({**r, "domain_found": d, "files_searched": len(files), "hits": hits,
                                "near": [f"{k[0]} in {k[1]} x{v}" for k, v in nears.most_common(6)]}) + "\n")
    print(f"{len(bad)} bad claims, {len({r['n'] for r in bad})} distinct tails -> {a.out}")


def cmd_check(a):
    rows, bad = bad_tails(a.rows)
    want = {(r["outcome"], r["n"]) for r in bad}
    got = {}
    with open(a.classes) as f:
        for row in csv.DictReader(f):
            got[(row["outcome"], row["tail"])] = row
    fails = []
    missing = want - set(got)
    extra = set(got) - want
    if missing:
        fails.append(f"{len(missing)} bad tails have no row: {sorted(missing)[:5]}")
    if extra:
        fails.append(f"{len(extra)} rows are not bad tails of this run: {sorted(extra)[:5]}")
    for k, row in got.items():
        if row["class"] not in CLASSES:
            fails.append(f"{k}: class {row['class']!r} not in {CLASSES}")
        if not row["url"].startswith("http"):
            fails.append(f"{k}: no URL")
    split = Counter((row["outcome"], row["class"]) for k, row in got.items() if k in want)
    print("| | " + " | ".join(CLASSES) + " | total |")
    print("|---|" + "---:|" * (len(CLASSES) + 1))
    for o in BAD:
        n = [split[(o, c)] for c in CLASSES]
        print(f"| {o} | " + " | ".join(map(str, n)) + f" | {sum(n)} |")
    tot = [sum(split[(o, c)] for o in BAD) for c in CLASSES]
    print("| all | " + " | ".join(map(str, tot)) + f" | {sum(tot)} |")
    if fails:
        sys.exit("FAIL " + "; ".join(fails[:10]))
    sub = Counter((row["class"], row["subtype"]) for k, row in got.items() if k in want and row["subtype"])
    print("subtypes: " + ", ".join(f"{c}/{t} {n}" for (c, t), n in sorted(sub.items())))
    print(f"PASS: all {len(want)} bad tails classified with a URL")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("evidence")
    e.add_argument("--rows", required=True)
    e.add_argument("--output-dir", required=True)
    e.add_argument("--out", required=True)
    c = sub.add_parser("check")
    c.add_argument("--rows", required=True)
    c.add_argument("--classes", required=True)
    a = ap.parse_args()
    {"evidence": cmd_evidence, "check": cmd_check}[a.cmd](a)


if __name__ == "__main__":
    main()
