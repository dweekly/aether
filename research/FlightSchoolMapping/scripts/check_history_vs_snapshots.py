#!/usr/bin/env python3
"""Does the adsb.lol archive hold the US aircraft our own snapshots saw?

For each date, compares the US-block addresses (hex a00000-afffff) in that day's
15-minute snapshots (data/adsb/snapshots, polled live from OpenSky and adsb.lol)
with the addresses in the ingested archive day (data/history/aircraft_day). A
date passes when the archive holds at least 98% of the snapshot addresses.
Misses are broken down by snapshot source and by the region of the first
position fix. A date with no snapshots (the 2026-04-23 -> 07-16 hole, or after
the poller was retired on 2026-10-23) prints NO-REFERENCE and counts as a failure.

Exit 0 only if every date passes.

Usage: check_history_vs_snapshots.py [YYYY-MM-DD ...]   (default: the plan's fixed set)
"""

import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / "data" / "adsb" / "snapshots"
HISTORY = ROOT / "data" / "history"
# Quiet weekday, busy weekday, weekend, and the newest archive day at the time the gate was written.
DEFAULT_DATES = ["2026-03-10", "2026-10-15", "2026-08-15", "2026-10-22"]
THRESHOLD = 0.98


def region(lat, lon):
    if lat is None or lon is None:
        return "no position"
    if lat > 51 and lon < -129:
        return "Alaska"
    if 18 < lat < 23 and -161 < lon < -154:
        return "Hawaii"
    if not (24 <= lat <= 50 and -125 <= lon <= -66):
        return "outside US"
    return "CONUS west" if lon < -104 else "CONUS central" if lon < -87 else "CONUS east"


def snapshot_hexes(day):
    """{hex: (source, region of first fix)} for US-block addresses in the day's snapshots."""
    seen = {}
    for f in sorted(SNAPSHOTS.glob(f"snapshot_{day:%Y%m%d}_*.json")):
        for a in json.loads(f.read_text())["aircraft"]:
            h = (a.get("icao_hex") or "").lower()
            if len(h) == 6 and h[0] == "a" and h not in seen:
                seen[h] = (a.get("source"), region(a.get("latitude"), a.get("longitude")))
    return seen


def archive_hexes(day):
    path = HISTORY / "aircraft_day" / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}.parquet"
    if not path.exists():
        return None
    return {r[0] for r in duckdb.connect().execute(
        f"SELECT DISTINCT lower(hex) FROM read_parquet('{path}') WHERE lower(hex) LIKE 'a%' AND length(hex) = 6").fetchall()}


def main():
    days = [date.fromisoformat(s) for s in (sys.argv[1:] or DEFAULT_DATES)]
    ok = True
    for day in days:
        snap = snapshot_hexes(day)
        arch = archive_hexes(day)
        if not snap:
            print(f"{day}  NO-REFERENCE  no snapshots for this date")
            ok = False
            continue
        if arch is None:
            print(f"{day}  FAIL  archive day not ingested ({len(snap):,} snapshot addresses)")
            ok = False
            continue
        miss = {h: v for h, v in snap.items() if h not in arch}
        cov = 1 - len(miss) / len(snap)
        verdict = "PASS" if cov >= THRESHOLD else "FAIL"
        ok &= verdict == "PASS"
        print(f"{day}  {verdict}  {cov:.2%} of {len(snap):,} snapshot addresses in the archive "
              f"({len(miss):,} missing; archive has {len(arch):,})")
        by_src = Counter(s for s, _ in snap.values())
        miss_src = Counter(s for s, _ in miss.values())
        print("    by source: " + ", ".join(f"{s} {miss_src[s]:,}/{n:,} missing" for s, n in by_src.most_common()))
        miss_reg = Counter(r for _, r in miss.values())
        print("    missing by region: " + ", ".join(f"{r} {n:,}" for r, n in miss_reg.most_common()))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
