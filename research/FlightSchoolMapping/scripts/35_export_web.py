#!/usr/bin/env python3
"""Export a release as small static JSON shards for the explorer.

  data/release/<release>/web/index.json    release, window, day list, headline results
  data/release/<release>/web/t/<key>.json  aircraft records keyed by N-number, each with
                                           "hist": [[day_index, airborne_min, legs], ...]

<key> is the three characters after the "N" (N12345 -> "123", N5 -> "5__"), so a
tail lookup fetches exactly one file. Files are deployed with the Pages site:
Cloudflare's REST API rate limit (HTTP 429 at ~1,200 requests / 5 min) makes
uploading thousands of small objects to R2 impractical without S3 keys.

Usage: 35_export_web.py --release dev
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_window import day_view, included_days  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "data" / "history"
RECENT_LEGS = 8
RELEASES = ROOT / "data" / "release"


def shard_key(n_number):
    s = n_number[1:4].upper()
    return s + "_" * (3 - len(s))


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":"), default=str))
    tmp.replace(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", required=True)
    a = ap.parse_args()
    rel = RELEASES / a.release
    web = rel / "web"
    c = duckdb.connect()
    c.execute("SET threads TO 16")
    days = included_days(rel)  # frozen by 34; see release_window.py
    day_view(c, "ad", HISTORY / "aircraft_day" / "*" / "*" / "*.parquet", days)
    day_index = {d: i for i, d in enumerate(days)}

    parked = {r[0]: r[1:] for r in c.execute(f"""
        SELECT hex, max(day), arg_max_null(last_ground_airport, day),
               arg_max_null(round(last_ground_lat, 5), day), arg_max_null(round(last_ground_lon, 5), day)
        FROM ad WHERE last_ground_lat IS NOT NULL GROUP BY hex
    """).fetchall()}

    cur = c.execute(f"SELECT * FROM read_parquet('{rel / 'aircraft.parquet'}')")
    cols = [d[0] for d in cur.description]
    shards = defaultdict(dict)
    hex_to_n = {}
    for r in cur.fetchall():
        x = dict(zip(cols, r))
        n = x["n_number"]
        rec = {
            "n": n, "hex": x["hex"], "mfr": x["mfr"], "model": x["model"], "yr": x["year_mfr"],
            "type": x["aircraft_type"], "eng": x["engine_type"], "seats": x["seats"],
            "regType": x["registrant_type"], "owner": x["registrant"], "city": x["city"],
            "st": x["state"], "status": x["status_code"], "statusLabel": x["status_label"],
            "valid": x["status_valid"], "exp": x["expiration_date"], "cls": x["observed_class"],
            "days": x["days_observed"], "airDays": x["days_airborne"], "first": x["first_seen"],
            "last": x["last_seen"], "lastFlew": x["last_flew"], "hrs": x["airborne_hours"], "legs": x["legs"],
            "home": x["home_airport"], "homeShare": x["home_share"], "pia": x["pia_seen"],
            "ladd": x["ladd_flag"],
        }
        p = parked.get(x["hex"])
        if p:
            rec["park"] = {"day": p[0], "apt": p[1], "lat": p[2], "lon": p[3]}
        shards[shard_key(n)][n] = {k: v for k, v in rec.items() if v not in (None, "")}
        if x["observed_class"] != "not_observed":
            hex_to_n[x["hex"]] = n

    # The last RECENT_LEGS airborne legs per aircraft, newest first:
    # [day_index, dep or null, arr or null, airborne minutes].
    day_view(c, "legs", HISTORY / "legs" / "*" / "*" / "*.parquet", days)
    recent = defaultdict(list)
    for hexid, day, dep, arr, mins in c.execute(f"""
        SELECT hex, day, dep, arr, round(airborne_min)::INT FROM (
            SELECT *, row_number() OVER (PARTITION BY hex ORDER BY start_ts DESC) AS rk
            FROM legs WHERE airborne_min > 0 OR max_alt_ft IS NOT NULL)
        WHERE rk <= {RECENT_LEGS} ORDER BY hex, start_ts DESC
    """).fetchall():
        n = hex_to_n.get(hexid)
        if n:
            recent[n].append([day_index[str(day)], dep, arr, mins])

    hist = defaultdict(lambda: defaultdict(list))
    for hexid, day, mins, legs in c.execute(f"""
        SELECT hex, day, round(airborne_min)::INT, n_legs FROM ad
        WHERE n_airborne > 0 ORDER BY hex, day
    """).fetchall():
        n = hex_to_n.get(hexid)
        if n:
            hist[shard_key(n)][n].append([day_index[str(day)], mins, legs])

    for k, recs in shards.items():
        for n, h in hist.get(k, {}).items():
            recs[n]["hist"] = h
        for n in recs:
            if recent.get(n):
                recs[n]["recent"] = recent[n]
        write(web / "t" / f"{k}.json", recs)
    results = json.loads((rel / "results.json").read_text())
    write(web / "index.json", {"release": a.release, "days": days, "results": results,
                               "shards": sorted(shards)})
    print(f"{a.release}: {sum(len(v) for v in shards.values())} aircraft in {len(shards)} shards, "
          f"{len(days)} days")


if __name__ == "__main__":
    main()
