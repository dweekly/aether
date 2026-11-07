#!/usr/bin/env python3
"""On-stage demo (recorded): one flight school's website in, its checked fleet out.

1. Render the school's homepage in headless Chromium and follow up to four links
   that look like fleet, aircraft, rental or rates pages.
2. Ask Claude for the fleet as JSON: tail, make and model, wet rate.
3. Check each tail against the frozen release: on the FAA registry? same make?
   flew in the last 12 months? based at this school's airport?

Usage: scripts/.venv/bin/python scripts/demo_extract.py https://school.example --release v0.3-20261023
Needs ANTHROPIC_API_KEY (read from the repo's .env if unset) and the release's
aircraft.parquet under data/release/<release>/ (rsync it from trogdor).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import anthropic
import duckdb
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from airport_index import canonical_ids  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT.parent.parent / ".env"
MODEL = "claude-opus-5-5"
LINK_WORDS = re.compile(r"fleet|aircraft|airplane|rental|rates|pricing|planes", re.I)
G, R, Y, DIM, B, X = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"
SCHEMA = {
    "type": "object",
    "properties": {
        "school": {"type": "string"},
        "home_airport": {"type": "string", "description": "ICAO or FAA identifier of the school's main airport"},
        "fleet": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "n_number": {"type": "string"},
                "make_model": {"type": "string"},
                "wet_rate_hr": {"type": ["number", "null"]},
            },
            "required": ["n_number", "make_model", "wet_rate_hr"],
            "additionalProperties": False,
        }},
    },
    "required": ["school", "home_airport", "fleet"],
    "additionalProperties": False,
}


def say(msg):
    print(msg, flush=True)


def read_site(url, max_pages=4):
    bare = lambda u: urlparse(u).netloc.lower().removeprefix("www.")  # noqa: E731
    host = bare(url)
    pages = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="load", timeout=30000); page.wait_for_timeout(1500)
        pages.append((url, page.inner_text("body")))
        links = []
        for a in page.query_selector_all("a[href]"):
            href, text = a.get_attribute("href") or "", a.inner_text() or ""
            full = urljoin(url, href).split("#")[0]
            if bare(full) == host and LINK_WORDS.search(href + " " + text) and full not in links and full != url:
                links.append(full)
        for link in links[:max_pages]:
            try:
                page.goto(link, wait_until="load", timeout=20000); page.wait_for_timeout(1000)
                pages.append((link, page.inner_text("body")))
            except Exception:
                continue
        browser.close()
    return pages


def extract(pages):
    # A worktree has no .env of its own; fall back to the main checkout's.
    common = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True).stdout.strip()
    env = next((p for p in (ENV, Path(common).parent / ".env") if p.exists()), None)
    if not os.environ.get("ANTHROPIC_API_KEY") and env:
        for line in env.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip().strip('"')
    text = "\n\n".join(f"=== {u}\n{t[:30000]}" for u, t in pages)
    resp = anthropic.Anthropic().messages.create(
        model=MODEL,
        max_tokens=8000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content":
                   "These are pages from a flight school's website. List every aircraft it offers with its "
                   "US tail number, make and model, and published hourly wet rate (null if none). Only include "
                   "aircraft whose tail number appears on the pages.\n\n" + text}],
    )
    if resp.stop_reason == "refusal":
        sys.exit("model declined")
    data = json.loads(next(b.text for b in resp.content if b.type == "text"))
    return data, resp.usage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--release", default="v0.3-20261023")
    a = ap.parse_args()
    parquet = ROOT / "data" / "release" / a.release / "aircraft.parquet"
    if not parquet.exists():
        sys.exit(f"missing {parquet}")

    t0 = time.time()
    say(f"{DIM}reading{X} {a.url}")
    pages = read_site(a.url)
    say(f"{DIM}  {len(pages)} pages, {sum(len(t) for _, t in pages):,} characters{X}")
    say(f"{DIM}asking {MODEL} for the fleet…{X}")
    data, usage = extract(pages)
    cost = usage.input_tokens / 1e6 * 4 + usage.output_tokens / 1e6 * 20
    say(f"{B}{data['school']}{X} at {data['home_airport']}: {len(data['fleet'])} aircraft "
        f"{DIM}({time.time() - t0:.0f} s, ${cost:.3f}){X}\n")

    c = duckdb.connect()
    alias = canonical_ids()[0]
    canon = lambda x: alias.get(x.upper(), x.upper()) if x else None  # noqa: E731
    say(f"{B}{'tail':8} {'website says':26} {'rate':>6}  {'FAA registry':30} {'hrs/12mo':>8}  home{X}")
    for f in data["fleet"]:
        n = re.sub(r"[\s\-]", "", f["n_number"].upper())
        n = n if n.startswith("N") else "N" + n
        row = c.execute(f"SELECT mfr, model, year_mfr, airborne_hours, home_airport FROM read_parquet('{parquet}') "
                        "WHERE n_number = ?", [n]).fetchone()
        rate = f"${f['wet_rate_hr']:.0f}" if f["wet_rate_hr"] else "—"
        if not row:
            say(f"{R}{n:8}{X} {f['make_model'][:26]:26} {rate:>6}  {R}{'not on the registry':30}{X}")
            continue
        mfr, model, year, hours, home = row
        reg = f"{year or ''} {mfr.title()} {model}"[:30]
        # Green only if the website names both the registry's make and its model (e.g. "172R").
        squash = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())  # noqa: E731
        make_ok = mfr.split()[0].lower() in f["make_model"].lower() and squash(model) in squash(f["make_model"])
        hrs = f"{hours:,.0f} h" if hours else "—"
        home_ok = home and canon(home) == canon(data["home_airport"])  # KMRI and PAMR are both Merrill Field
        say(f"{G}{n:8}{X} {f['make_model'][:26]:26} {rate:>6}  {(G if make_ok else Y)}{reg:30}{X} "
            f"{(G if hours else Y)}{hrs:>8}{X}  {(G if home_ok else Y)}{home or '—'}{X}")


if __name__ == "__main__":
    main()
