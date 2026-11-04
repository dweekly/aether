#!/usr/bin/env python3
"""Recompute airport fields in already-ingested archive days from stored coordinates.

When airport_index.py's matching rules change, this brings every day up to
AIRPORT_INDEX_VERSION without re-downloading anything: legs keep their start/end
coordinates and low flags, and aircraft_day keeps the last ground position.
Each day's manifest records the version it was matched with; current days are
skipped, so this is safe to rerun.

Usage: 32_reassign_airports.py [--workers 8]
"""

import argparse
import json
import os
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from airport_index import AIRPORT_INDEX_VERSION, AirportIndex  # noqa: E402

HISTORY = Path(__file__).resolve().parent.parent / "data" / "history"
ENDPOINT_MAX_NM = 3.0
_IDX = None


def idx():
    global _IDX
    if _IDX is None:
        _IDX = AirportIndex()
    return _IDX


def rewrite(path, table):
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(fd)
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, path)


def match(lat, lon, low):
    if not low or lat is None:
        return None, None, None
    m = idx().nearest(lat, lon, max_nm=ENDPOINT_MAX_NM)
    return (m.ident, m.distance_nm, m.ambiguous) if m else (None, None, None)


def do_day(day):
    mpath = HISTORY / "manifest" / f"{day}.json"
    man = json.loads(mpath.read_text())
    if man.get("airport_index_version", 1) >= AIRPORT_INDEX_VERSION:
        return day, "current"
    y, m, d = day.split("-")
    lp = HISTORY / "legs" / y / m / f"{d}.parquet"
    t = pq.read_table(lp)
    rows = t.to_pylist()
    for r in rows:
        r["dep"], r["dep_nm"], r["dep_ambiguous"] = match(r["start_lat"], r["start_lon"], r["start_observed_low"])
        r["arr"], r["arr_nm"], r["arr_ambiguous"] = match(r["end_lat"], r["end_lon"], r["end_observed_low"])
    rewrite(lp, pa.Table.from_pylist(rows, schema=t.schema))
    ap = HISTORY / "aircraft_day" / y / m / f"{d}.parquet"
    t = pq.read_table(ap)
    rows = t.to_pylist()
    for r in rows:
        r["last_ground_airport"] = match(r["last_ground_lat"], r["last_ground_lon"], True)[0]
    rewrite(ap, pa.Table.from_pylist(rows, schema=t.schema))
    man["airport_index_version"] = AIRPORT_INDEX_VERSION
    tmp = mpath.with_suffix(".tmp")
    tmp.write_text(json.dumps(man, indent=2) + "\n")
    tmp.replace(mpath)
    return day, "updated"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    days = sorted(p.stem for p in (HISTORY / "manifest").glob("*.json"))
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        for day, status in pool.map(do_day, days):
            if status != "current":
                print(day, status, flush=True)


if __name__ == "__main__":
    main()
