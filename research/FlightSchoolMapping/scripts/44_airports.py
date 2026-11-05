#!/usr/bin/env python3
"""Airport pages: which registered aircraft live at each airport, and what flies there.

An aircraft "lives" at an airport when 34_fleet_summary.py assigned it that
home base (>= 3 nights and >= 50% of nights). Airports with at least
MIN_BASED based aircraft get a page.

Writes data/release/<release>/airports_full.json (read at site build time)
and web/airports.json (compact search index).

Usage: 44_airports.py --release dev
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from airport_index import canonical_ids  # noqa: E402
from release_window import day_view, included_days  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "data" / "history"
RELEASES = ROOT / "data" / "release"
AIRPORTS = ROOT / "data" / "ourairports_airports.csv"
MIN_BASED = 5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", required=True)
    a = ap.parse_args()
    rel = RELEASES / a.release
    c = duckdb.connect()
    days = included_days(rel)
    day_view(c, "legs", HISTORY / "legs" / "*" / "*" / "*.parquet", days)
    alias, meta = canonical_ids()

    based = defaultdict(list)
    for row in c.execute(f"""
        SELECT home_airport, n_number, mfr, model, year_mfr, aircraft_type, registrant, days_airborne,
               airborne_hours, last_flew, home_share
        FROM read_parquet('{rel / "aircraft.parquet"}') WHERE home_airport IS NOT NULL
        ORDER BY airborne_hours DESC""").fetchall():
        based[row[0]].append({"n": row[1], "model": " ".join(str(x) for x in (row[2], row[3]) if x), "year": row[4],
                              "type": row[5], "owner": row[6], "days": row[7], "hrs": row[8],
                              "last_flew": str(row[9]) if row[9] else None, "share": row[10]})

    movements = dict(c.execute("""
        SELECT apt, count(*) FROM (
            SELECT dep AS apt FROM legs WHERE dep IS NOT NULL AND (airborne_min > 0 OR max_alt_ft IS NOT NULL)
            UNION ALL
            SELECT arr FROM legs WHERE arr IS NOT NULL AND (airborne_min > 0 OR max_alt_ft IS NOT NULL))
        GROUP BY apt""").fetchall())

    # A school counts at every airport it lists, or, when it lists none, at the airports its fleet is
    # based at (38's inferred_airports); "how" says which. Releases before rc4 have neither field,
    # so they fall back to the school's primary airport from its fleet.
    schools = json.loads((rel / "schools_full.json").read_text())["schools"]
    at_airport = defaultdict(list)
    for s in schools:
        if "listed_airports" in s:
            places = [(a, "listed", None) for a in s["listed_airports"]] or \
                     [(x["id"], "inferred", f"{x['tails']} of {x['of']}") for x in s["inferred_airports"]]
        else:
            places = [(s["airport"], "fleet", None)] if s["airport"] else []
        for apt, how, share in places:
            row = {"key": s["key"], "name": s["name"], "fleet": s["fleet_listed"], "flew": s["fleet_flew"], "how": how}
            if share:
                row["based"] = share
            at_airport[alias.get(apt.upper(), apt)].append(row)

    airports = []
    for apt, acs in based.items():
        if len(acs) < MIN_BASED:
            continue
        m = meta.get(apt)
        airports.append({
            "id": apt, "name": m[0] if m else apt, "city": m[1] if m else None,
            "state": m[2].split("-")[-1] if m and m[2] else None, "kind": m[3] if m else None,
            "based": len(acs), "based_flew": sum(1 for x in acs if x["days"]),
            "based_hours": round(sum(x["hrs"] or 0 for x in acs), 1),
            "movements": movements.get(apt, 0),
            "types": dict(sorted(defaultdict(int, {t: sum(1 for x in acs if x["type"] == t)
                                                   for t in {x["type"] for x in acs}}).items(), key=lambda kv: -kv[1])),
            "schools": sorted(at_airport.get(apt, []), key=lambda s: -s["flew"]),
            "aircraft": acs,
        })
    airports.sort(key=lambda x: -x["based"])
    (rel / "airports_full.json").write_text(json.dumps({"days": days, "airports": airports}, default=str))
    (rel / "web" / "airports.json").write_text(json.dumps(
        [[x["id"], x["name"], x["city"], x["state"], x["based"]] for x in airports], separators=(",", ":")))
    print(f"{a.release}: {len(airports)} airports with >= {MIN_BASED} based aircraft; "
          f"top: {[(x['id'], x['based']) for x in airports[:5]]}")


if __name__ == "__main__":
    main()
