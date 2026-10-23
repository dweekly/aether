#!/usr/bin/env python3
"""Run 30_adsblol_history.py over a date range, a few days at a time.

Days that already have a manifest are skipped, so this is safe to rerun. A day
whose prod-0 release is missing or fails is retried once from staging-0, then
recorded in data/history/GAPS.md with the error.

Usage:
  31_history_backfill.py 2026-03-05 2026-10-21 [--parallel 3] [--newest-first]
"""

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
HISTORY = HERE.parent / "data" / "history"
INGEST = [sys.executable, str(HERE / "30_adsblol_history.py")]


def run_day(day):
    if (HISTORY / "manifest" / f"{day}.json").exists():
        return day, "skip", ""
    last = ""
    for instance in ("prod-0", "staging-0"):
        p = subprocess.run([*INGEST, str(day), "--instance", instance, "--workers", "8"],
                           capture_output=True, text=True)
        if p.returncode == 0:
            return day, instance, ""
        last = (p.stderr or p.stdout).strip().splitlines()[-1:] or ["unknown error"]
        last = last[0]
    return day, "FAILED", last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("start")
    ap.add_argument("end")
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--newest-first", action="store_true")
    a = ap.parse_args()
    d0, d1 = date.fromisoformat(a.start), date.fromisoformat(a.end)
    days = [d0 + timedelta(n) for n in range((d1 - d0).days + 1)]
    if a.newest_first:
        days.reverse()
    gaps = HISTORY / "GAPS.md"
    with ThreadPoolExecutor(max_workers=a.parallel) as pool:
        for day, status, err in pool.map(run_day, days):
            print(f"{day} {status} {err}", flush=True)
            if status == "FAILED":
                gaps.parent.mkdir(parents=True, exist_ok=True)
                with open(gaps, "a") as f:
                    f.write(f"- {day}: no usable release ({err})\n")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    main()
