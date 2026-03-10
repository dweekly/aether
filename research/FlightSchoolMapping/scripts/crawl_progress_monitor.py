#!/usr/bin/env python3
"""
crawl_progress_monitor.py — Monitors the fleet crawl and writes progress JSON
for the FLEET RECON II dashboard.

Watches:
  - The crawl log file (tqdm output) for done/total counts
  - output/fleet_html_raw/ for new school directories
  - output/fleet_html/ for cached pages
  - Image metadata in newly created crawl index entries

Writes: output/crawl_progress.json every 2 seconds.

Usage:
    python crawl_progress_monitor.py
    python crawl_progress_monitor.py --log /path/to/crawl.log
"""

import argparse
import csv
import io
import json
import os
import re
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
RAW_HTML_DIR = OUTPUT_DIR / "fleet_html_raw"
BROWSER_RAW_DIR = OUTPUT_DIR / "fleet_browser_html_raw"
HTML_DIR = OUTPUT_DIR / "fleet_html"
PROGRESS_FILE = OUTPUT_DIR / "crawl_progress.json"

NN_PATTERN = re.compile(r"\bN\d{1,5}[A-Z]{0,2}\b")


def load_faa_set():
    """Load just the set of valid N-numbers."""
    faa_path = OUTPUT_DIR / "faa_master.csv"
    nns = set()
    if not faa_path.exists():
        return nns
    with open(faa_path, "r") as f:
        content = f.read()
    lines = content.splitlines()
    if not lines[0].startswith("N-NUMBER"):
        idx = lines[0].find("N-NUMBER")
        if idx >= 0:
            lines[0] = lines[0][idx:]
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    for row in reader:
        nn = row.get("N-NUMBER", "").strip()
        if nn:
            nns.add("N" + nn)
    return nns


def count_dirs(path):
    if not path.exists():
        return 0
    return sum(1 for d in path.iterdir() if d.is_dir())


def count_html_files(path):
    if not path.exists():
        return 0
    return sum(1 for f in path.rglob("*.html"))


def parse_tqdm_line(line):
    """Extract done/total from tqdm output line."""
    # Pattern: "  Crawling fleet pages:  42%|████      | 1680/4000 [05:23<07:28,  5.17it/s]"
    m = re.search(r"(\d+)/(\d+)\s+\[(\d+:\d+)<(\d+:\d+),\s+([\d.]+)it/s\]", line)
    if m:
        done = int(m.group(1))
        total = int(m.group(2))
        elapsed_parts = m.group(3).split(":")
        elapsed = int(elapsed_parts[0]) * 60 + int(elapsed_parts[1])
        eta_parts = m.group(4).split(":")
        eta = int(eta_parts[0]) * 60 + int(eta_parts[1])
        rate = float(m.group(5))
        return {"done": done, "total": total, "elapsed_sec": elapsed, "eta_sec": eta, "rate_per_sec": rate}
    return None


def scan_recent_schools(raw_dir, limit=20):
    """Get the most recently modified school directories."""
    if not raw_dir.exists():
        return []
    dirs = []
    for d in raw_dir.iterdir():
        if d.is_dir():
            mtime = d.stat().st_mtime
            dirs.append((mtime, d))
    dirs.sort(reverse=True)
    return [d for _, d in dirs[:limit]]


def analyze_school_dir(school_dir, faa_set):
    """Analyze a school directory for images and N-numbers."""
    images = 0
    nns = set()
    nn_confirmed = set()
    flags = []

    for html_file in school_dir.glob("*.html"):
        try:
            content = html_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        # Count images
        images += content.count("<img ")

        # Find N-numbers in image attributes
        # Quick regex: find img tags with src/alt containing N-numbers
        for m in re.finditer(r'<img[^>]*(?:src|alt|title)="([^"]*)"', content):
            attr_val = m.group(1)
            for nn_match in NN_PATTERN.finditer(attr_val):
                nn = nn_match.group()
                nns.add(nn)
                if nn in faa_set:
                    nn_confirmed.add(nn)

        # Check for thin content
        text_len = len(re.sub(r"<[^>]+>", "", content))
        if text_len < 500:
            if "fleet" in html_file.name.lower():
                flags.append("fleet_pages_thin")

        # Check captcha
        lower = content.lower()
        if any(sig in lower for sig in ["captcha", "challenge-form", "checking your browser"]):
            flags.append("captcha_challenge")

    return {
        "images": images,
        "nns": nns,
        "nn_confirmed": nn_confirmed,
        "flags": list(set(flags)),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", help="Path to crawl log file to tail")
    args = parser.parse_args()

    print("FLEET RECON II — Progress Monitor")
    print("=" * 40)
    print(f"Output: {PROGRESS_FILE}")
    print(f"Raw HTML dir: {RAW_HTML_DIR}")

    faa_set = load_faa_set()
    print(f"FAA registry: {len(faa_set):,} N-numbers")

    # Find log file
    log_path = None
    if args.log:
        log_path = Path(args.log)
    else:
        # Auto-detect from /private/tmp/claude-501/
        tmp_dir = Path("/private/tmp/claude-501/-Users-dew-dev-aether-research-FlightSchoolMapping-scripts/tasks/")
        if tmp_dir.exists():
            outputs = sorted(tmp_dir.glob("*.output"), key=lambda p: p.stat().st_mtime, reverse=True)
            if outputs:
                log_path = outputs[0]
                print(f"Auto-detected log: {log_path}")

    t0 = time.time()
    recent_schools = deque(maxlen=30)
    seen_domains = set()
    recent_nns = deque(maxlen=20)
    total_images_all = 0
    total_nns_all = set()
    total_nns_confirmed = set()
    schools_with_images = set()
    flagged_captcha = 0
    flagged_thin = 0

    last_log_pos = 0
    last_tqdm = None

    print("\nMonitoring... (Ctrl+C to stop)\n")

    while True:
        # 1. Parse log file for tqdm progress
        if log_path and log_path.exists():
            try:
                with open(log_path, "r") as f:
                    f.seek(last_log_pos)
                    new_content = f.read()
                    last_log_pos = f.tell()

                # Find last tqdm line
                for line in new_content.split("\r"):
                    line = line.strip()
                    if "Crawling" in line and "/" in line:
                        parsed = parse_tqdm_line(line)
                        if parsed:
                            last_tqdm = parsed

                # Check for completion
                if "Phase 9a complete" in new_content or "=== Results ===" in new_content:
                    if last_tqdm:
                        last_tqdm["phase"] = "complete"
            except Exception:
                pass

        # 2. Scan filesystem for new school dirs
        for raw_dir in [RAW_HTML_DIR, BROWSER_RAW_DIR]:
            for school_dir in scan_recent_schools(raw_dir, limit=10):
                domain = school_dir.name
                if domain in seen_domains:
                    continue
                seen_domains.add(domain)

                analysis = analyze_school_dir(school_dir, faa_set)

                # Track totals
                total_images_all += analysis["images"]
                if analysis["images"] > 0:
                    schools_with_images.add(domain)
                total_nns_all.update(analysis["nns"])
                total_nns_confirmed.update(analysis["nn_confirmed"])
                if "captcha_challenge" in analysis["flags"]:
                    flagged_captcha += 1
                if "fleet_pages_thin" in analysis["flags"]:
                    flagged_thin += 1

                # Add to recent feed
                page_count = len(list(school_dir.glob("*.html")))
                status = "error" if "captcha_challenge" in analysis["flags"] else "done"
                if analysis["flags"] and status != "error":
                    status = "flagged" if analysis["flags"] else "done"

                recent_schools.appendleft({
                    "school": domain,
                    "domain": domain,
                    "status": status,
                    "pages": page_count,
                    "images": analysis["images"],
                    "nns": len(analysis["nns"]),
                    "flags": analysis["flags"],
                })

                # Track new N-numbers for gallery
                for nn in analysis["nn_confirmed"]:
                    recent_nns.appendleft({"n_number": nn, "confirmed": True})
                for nn in analysis["nns"] - analysis["nn_confirmed"]:
                    recent_nns.appendleft({"n_number": nn, "confirmed": False})

        # 3. Count filesystem stats
        raw_html_dirs = count_dirs(RAW_HTML_DIR) + count_dirs(BROWSER_RAW_DIR)

        # 4. Build progress JSON
        elapsed = time.time() - t0
        done = last_tqdm["done"] if last_tqdm else len(seen_domains)
        total = last_tqdm["total"] if last_tqdm else 4000
        rate = last_tqdm["rate_per_sec"] if last_tqdm else (done / max(elapsed, 1))
        eta = last_tqdm["eta_sec"] if last_tqdm else ((total - done) / max(rate, 0.01))
        phase = (last_tqdm or {}).get("phase", "crawling")

        # Count fleet pages (pages with "fleet" in filename)
        fleet_pages = 0
        if RAW_HTML_DIR.exists():
            for school_dir in RAW_HTML_DIR.iterdir():
                if school_dir.is_dir():
                    for f in school_dir.glob("*.html"):
                        # Pages that aren't "index" are likely fleet/location
                        if "index" not in f.name.lower():
                            fleet_pages += 1

        progress = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "total_schools": total,
            "done": done,
            "errors": 0,  # Will be filled from log parsing
            "rate_per_sec": round(rate, 1),
            "elapsed_sec": round(last_tqdm["elapsed_sec"] if last_tqdm else elapsed),
            "eta_sec": round(eta),
            "raw_html_dirs": raw_html_dirs,
            "total_pages": count_html_files(RAW_HTML_DIR),
            "schools_with_fleet": fleet_pages,
            "schools_with_images": len(schools_with_images),
            "total_images": total_images_all,
            "nn_from_images": len(total_nns_all),
            "nn_faa_confirmed": len(total_nns_confirmed),
            "flagged_captcha": flagged_captcha,
            "flagged_thin": flagged_thin,
            "recent": list(recent_schools)[:20],
            "recent_nns": list(recent_nns)[:15],
        }

        # Parse errors from log
        if log_path and log_path.exists():
            try:
                content = log_path.read_text()
                error_match = re.search(r"Errors:\s+(\d+)", content)
                if error_match:
                    progress["errors"] = int(error_match.group(1))
            except Exception:
                pass

        with open(PROGRESS_FILE, "w") as f:
            json.dump(progress, f, indent=2)

        # Print status
        pct = done / max(total, 1) * 100
        print(f"\r  [{pct:5.1f}%] {done}/{total} | {rate:.1f}/s | "
              f"imgs:{total_images_all} NNs:{len(total_nns_all)} "
              f"FAA:{len(total_nns_confirmed)} | dirs:{raw_html_dirs}  ",
              end="", flush=True)

        if phase == "complete":
            print("\n\nMISSION COMPLETE")
            # Write final progress
            with open(PROGRESS_FILE, "w") as f:
                json.dump(progress, f, indent=2)
            break

        time.sleep(2)


if __name__ == "__main__":
    main()
