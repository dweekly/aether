#!/usr/bin/env python3
"""Is every day of the privacy window ingested, and does each span the whole day?

The privacy findings (41_privacy.py) read the last 60 archive days. For each day
through the newest one the daily job can have fetched (adsb.lol publishes day D
around 03:30 UTC on D+1; 37_history_daily.sh runs at 06:30 UTC), this checks that
a manifest and the legs file exist, the manifest found trace files, and legs start
in all 24 UTC hours (a day cut short upstream, like 2026-05-07, fails here).

Exit 0 only if every day passes. `make history-status` runs it.

Usage: check_history_window.py [--days 60] [--end YYYY-MM-DD]
"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb

HISTORY = Path(__file__).resolve().parent.parent / "data" / "history"


def hours_covered(day):
    """UTC hours of the day in which at least one leg starts, or None without a legs file."""
    legs = HISTORY / "legs" / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}.parquet"
    if not legs.exists():
        return None
    return duckdb.connect().execute(
        f"SELECT count(DISTINCT floor((start_ts % 86400) / 3600)) FROM read_parquet('{legs}')").fetchone()[0]


def check(day):
    m = HISTORY / "manifest" / f"{day}.json"
    if not m.exists():
        return "not ingested"
    if json.loads(m.read_text()).get("trace_files", 0) == 0:
        return "manifest has no trace files"
    hours = hours_covered(day)
    if hours is None:
        return "no legs file"
    return None if hours == 24 else f"legs start in only {hours} of 24 UTC hours"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    newest = (datetime.now(timezone.utc) - timedelta(hours=31)).date()  # D is fetched by 06:30 UTC on D+1
    ap.add_argument("--end", type=date.fromisoformat, default=newest)
    a = ap.parse_args()
    days = [a.end - timedelta(days=i) for i in range(a.days)][::-1]
    bad = [(d, p) for d in days if (p := check(d))]
    for d, p in bad:
        print(f"{d}  {p}")
    print(f"{len(days) - len(bad)} of {len(days)} days OK, {days[0]} → {days[-1]}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
