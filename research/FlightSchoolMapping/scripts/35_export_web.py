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
from collections import defaultdict
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "data" / "history"
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
    win = json.loads((rel / "results.json").read_text())["window"]
    # Only the days frozen into results.json, so a backfill running in parallel
    # cannot make the explorer's day list disagree with the release's numbers.
    c.execute(f"""CREATE VIEW ad AS SELECT * FROM read_parquet('{HISTORY / "aircraft_day" / "*" / "*" / "*.parquet"}')
                  WHERE day BETWEEN DATE '{win["first_day"]}' AND DATE '{win["last_day"]}'""")
    days = [str(r[0]) for r in c.execute("SELECT DISTINCT day FROM ad ORDER BY 1").fetchall()]
    day_index = {d: i for i, d in enumerate(days)}

    parked = {r[0]: r[1:] for r in c.execute(f"""
        SELECT hex, max_by(day, day), max_by(last_ground_airport, day),
               max_by(round(last_ground_lat, 5), day), max_by(round(last_ground_lon, 5), day)
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
            "last": x["last_seen"], "hrs": x["airborne_hours"], "legs": x["legs"],
            "home": x["home_airport"], "homeShare": x["home_share"], "pia": x["pia_seen"],
            "ladd": x["ladd_flag"],
        }
        p = parked.get(x["hex"])
        if p:
            rec["park"] = {"day": p[0], "apt": p[1], "lat": p[2], "lon": p[3]}
        shards[shard_key(n)][n] = {k: v for k, v in rec.items() if v not in (None, "")}
        if x["observed_class"] != "not_observed":
            hex_to_n[x["hex"]] = n

    hist = defaultdict(lambda: defaultdict(list))
    for hexid, day, mins, legs in c.execute(f"""
        SELECT hex, day, round(airborne_min)::INT, n_legs FROM ad
        WHERE airborne_min > 0 ORDER BY hex, day
    """).fetchall():
        n = hex_to_n.get(hexid)
        if n:
            hist[shard_key(n)][n].append([day_index[str(day)], mins, legs])

    for k, recs in shards.items():
        for n, h in hist.get(k, {}).items():
            recs[n]["hist"] = h
        write(web / "t" / f"{k}.json", recs)
    results = json.loads((rel / "results.json").read_text())
    write(web / "index.json", {"release": a.release, "days": days, "results": results,
                               "shards": sorted(shards)})
    print(f"{a.release}: {sum(len(v) for v in shards.values())} aircraft in {len(shards)} shards, "
          f"{len(days)} days")


if __name__ == "__main__":
    main()
