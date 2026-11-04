#!/usr/bin/env python3
"""Workstream 4, step 2: crawl every website discovery found, with the March crawlers.

Writes crawl/targets.json (one entry per distinct website; several operations can
share one, e.g. leasing LLCs that fly on the same school's line), runs
09a_fleet_crawl.py over it, then runs 10_fleet_browser_crawl.py --crawl-only on every
site the HTTP crawl flagged (thin pages, captcha, no fleet page, blocked) or could not
fetch at all, as the March pipeline did. Both write only under crawl/.

Usage: 51b_unlisted_crawl.py [--skip-http] [--skip-browser]
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import unlisted_ops as U  # noqa: E402

HERE = Path(__file__).resolve().parent
PY = sys.executable


def load_discovery():
    last = {}
    for line in (U.OUT / "discovery.jsonl").open():
        d = json.loads(line)
        last[d["registrant"]] = d
    return last


def norm_site(url):
    u = url.strip()
    if "://" not in u:
        u = "https://" + u
    return u


def targets(ops, disc):
    by_site = {}
    for op in ops:
        r = (disc.get(op["registrant"]) or {}).get("result") or {}
        if not r.get("website"):
            continue
        site = norm_site(r["website"])
        t = by_site.setdefault(site, {
            "name": r.get("operator_name") or op["registrant"], "website": site,
            "airport_code": op["homes"][0][0] if op["homes"] else "",
            "city": op["registrant_city"].title(), "state": op["registrant_state"], "registrants": []})
        t["registrants"].append(op["registrant"])
    return list(by_site.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-http", action="store_true")
    ap.add_argument("--skip-browser", action="store_true")
    ap.add_argument("--browser-concurrency", type=int, default=6)
    a = ap.parse_args()
    ops = U.rebuild()
    disc = load_discovery()
    missing = [o["registrant"] for o in ops if o["registrant"] not in disc]
    if missing:
        sys.exit(f"{len(missing)} operations have no discovery record yet; run 51a first")
    crawl = U.OUT / "crawl"
    crawl.mkdir(parents=True, exist_ok=True)
    ts = targets(ops, disc)
    (crawl / "targets.json").write_text(json.dumps(ts, indent=1) + "\n")
    print(f"{len(ts)} distinct websites for {sum(len(t['registrants']) for t in ts)} operations", flush=True)

    if not a.skip_http:
        subprocess.run([PY, str(HERE / "09a_fleet_crawl.py"), "--input", str(crawl / "targets.json"),
                        "--output-dir", str(crawl), "--concurrency", "20"], check=True)

    index = json.loads((crawl / "fleet_crawl_index.json").read_text())
    got = {e["school_website"] for e in index}
    browser = [e for e in index if e.get("crawl_flags")]
    browser += [{"school_name": t["name"], "school_website": t["website"], "known_airport": t["airport_code"],
                 "city": t["city"], "state": t["state"], "pages": [], "crawl_flags": ["http_failed"]}
                for t in ts if t["website"] not in got]
    (crawl / "browser_targets.json").write_text(json.dumps(browser, indent=1) + "\n")
    print(f"HTTP crawl: {len(index)} of {len(ts)} sites answered; {len(browser)} go to the browser", flush=True)

    if not a.skip_browser and browser:
        env = dict(os.environ, ANTHROPIC_API_KEY=U.api_key())  # 10_ builds a client even with --crawl-only
        subprocess.run([PY, str(HERE / "10_fleet_browser_crawl.py"), "--crawl-only",
                        "--targets", str(crawl / "browser_targets.json"), "--output-dir", str(crawl),
                        "--concurrency", str(a.browser_concurrency)], check=True, env=env)


if __name__ == "__main__":
    main()
