#!/usr/bin/env python3
"""Reduce OpenSky's "Weekly 24 Hours of State Vector Data" (Mondays, 2017-06 to
2022-06) to one small table per Monday: which addresses appeared, and how much.

  data/opensky_mondays/<date>.parquet   hex, n_points, n_airborne, first_ts, last_ts

Each hour is downloaded, reduced with DuckDB and deleted, so disk use stays
under a few GB. Days before mid-2020 live under dot-prefixed keys
(states/.YYYY-MM-DD/). The bundled LICENSE.txt (OpenSky General Terms of Use)
allows non-profit research only, forbids redistribution (3(iii)) and requires
aircraft identifiers to be de-identified in any publication (3(i)): these
tables stay internal and only aggregate counts are ever published.

Usage: 39_opensky_mondays.py [--parallel 4] [--limit N]
"""

import argparse
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "opensky_mondays"
WORK = Path("/mnt/data/home/dew/scratch/opensky-mondays")
BASE = "https://s3.opensky-network.org/data-samples"


def list_days():
    xml = urllib.request.urlopen(f"{BASE}?prefix=states/&delimiter=/", timeout=60).read().decode()
    prefixes = re.findall(r"<Prefix>states/(\.?\d{4}-\d{2}-\d{2})/</Prefix>", xml)
    return sorted(prefixes, key=lambda p: p.lstrip("."))


def reduce_day(prefix):
    day = prefix.lstrip(".")
    out = OUT / f"{day}.parquet"
    if out.exists():
        return day, "skip"
    work = Path(tempfile.mkdtemp(dir=WORK, prefix=day + "-"))
    con = duckdb.connect()
    con.execute("CREATE TABLE agg (hex VARCHAR, n_points BIGINT, n_airborne BIGINT, first_ts DOUBLE, last_ts DOUBLE)")
    hours = 0
    try:
        listing = urllib.request.urlopen(f"{BASE}?prefix=states/{prefix}/", timeout=60).read().decode()
        keys = re.findall(r"<Key>([^<]+)</Key>", listing)
        # Layouts vary by date: <hh>/states_<day>-<hh>.csv.tar (a tar holding .csv.gz),
        # a bare .csv.gz, and some names keep the dot prefix. Use whatever CSV exists.
        by_hour = {}
        for k in keys:
            m = re.search(r"/(\d{2})/[^/]*\.csv\.(tar|gz)$", k)
            if m and (m.group(1) not in by_hour or k.endswith(".csv.gz")):
                by_hour[m.group(1)] = k
        for hh, key in sorted(by_hour.items()):
            local = work / key.rsplit("/", 1)[1]
            r = subprocess.run(["curl", "-sSf", "--retry", "4", "-o", str(local), f"{BASE}/{key}"])
            if r.returncode != 0:
                continue
            if local.suffix == ".tar":
                with tarfile.open(local) as tf:
                    member = next((m for m in tf.getmembers() if m.name.endswith(".csv.gz")), None)
                    if member is None:
                        continue
                    tf.extract(member, work, filter="data")
                    csv_gz = work / member.name
                local.unlink(missing_ok=True)
            else:
                csv_gz = local
            con.execute(f"""
                INSERT INTO agg
                SELECT lower(icao24), count(*), count(*) FILTER (WHERE NOT onground),
                       min(time), max(time)
                FROM read_csv('{csv_gz}', header=true, nullstr='', types={{'icao24': 'VARCHAR', 'onground': 'BOOLEAN', 'time': 'DOUBLE'}})
                GROUP BY 1
            """)
            hours += 1
            csv_gz.unlink(missing_ok=True)
        if hours == 0:
            return day, "no-data"
        OUT.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        con.execute(f"""
            COPY (SELECT hex, sum(n_points) n_points, sum(n_airborne) n_airborne,
                         min(first_ts) first_ts, max(last_ts) last_ts, {hours} AS hours_present
                  FROM agg GROUP BY hex ORDER BY hex)
            TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)""")
        tmp.replace(out)
        return day, f"ok {hours}h"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    days = list_days()[: a.limit] if a.limit else list_days()
    print(f"{len(days)} Mondays listed", flush=True)
    with ThreadPoolExecutor(max_workers=a.parallel) as pool:
        for day, status in pool.map(reduce_day, days):
            print(day, status, flush=True)


if __name__ == "__main__":
    main()
