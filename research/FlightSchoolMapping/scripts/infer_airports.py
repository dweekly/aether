#!/usr/bin/env python3
"""Infer the airports a school operates from, from where its fleet is based.

Each fleet tail carries an ADS-B home base (`adsb.home_airport`: where the aircraft spends its nights,
from 21's ADS-B enrichment). A school's inferred airports are those home to at least two of its tails,
or, for a school with exactly one based tail, that tail's base. The result goes in a separate field,
`inferred_locations`, so listed airports (`locations`, from the school's own pages or directories) are
never changed:

  "inferred_locations": [{"airport_id": "KPAO", "tails": 7, "of": 9, "listed": true,
                          "source": "fleet_adsb_home_base"}]

`listed` says whether the airport is also among the school's listed airports. Checked 2026-11-05 on
flight_line.json: for 341 schools with a listed airport and two or more based tails, the inferred set
includes a listed airport for 72.7%; with fleets shuffled between schools, 1.2%. Of 1,728 schools with
no listed airport, 601 get one or more inferred airports.

Called by 22_pdf_rate_sheets.py before it writes flight_line.json (so PDF-found tails count), and
runnable on its own:  python scripts/infer_airports.py output/flight_line.json [--check]
"""
import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

SOURCE = "fleet_adsb_home_base"


def _canon(a):
    a = (a or "").strip().upper()
    return a[1:] if len(a) == 4 and a.startswith("K") else a


def infer(school):
    homes = Counter((f.get("adsb") or {}).get("home_airport") for f in school.get("fleet") or []
                    if (f.get("adsb") or {}).get("home_airport"))
    n = sum(homes.values())
    listed = {_canon(l.get("airport_id")) for l in school.get("locations") or [] if l.get("airport_id")}
    out = [{"airport_id": a, "tails": c, "of": n, "listed": _canon(a) in listed, "source": SOURCE}
           for a, c in homes.most_common() if c >= 2 or n == 1]
    return out


def apply(flight_line):
    for s in flight_line:
        s["inferred_locations"] = infer(s)
    return flight_line


def check(flight_line, seed=5):
    """Agreement with listed airports, and the same with fleets shuffled between schools."""
    val = [s for s in flight_line if s.get("locations") and infer(s) and infer(s)[0]["of"] >= 2]
    hit = sum(1 for s in val if any(x["listed"] for x in infer(s)))
    fleets = [s.get("fleet") or [] for s in val]
    random.Random(seed).shuffle(fleets)
    ctl = sum(1 for s, f in zip(val, fleets)
              if any(x["listed"] for x in infer({"fleet": f, "locations": s["locations"]})))
    none = [s for s in flight_line if not s.get("locations")]
    placed = sum(1 for s in none if infer(s))
    return {"validated_schools": len(val), "agree": hit, "control_agree": ctl,
            "no_listed_airport": len(none), "placed_by_inference": placed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("flight_line", type=Path)
    ap.add_argument("--check", action="store_true", help="print agreement and control; exit 1 if the control is not far below")
    a = ap.parse_args()
    fl = json.loads(a.flight_line.read_text())
    if a.check:
        r = check(fl)
        print(json.dumps(r))
        ok = r["agree"] >= 0.5 * r["validated_schools"] and r["control_agree"] * 10 < r["agree"]
        print("PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    apply(fl)
    tmp = a.flight_line.with_suffix(".tmp")
    tmp.write_text(json.dumps(fl, indent=2))
    tmp.replace(a.flight_line)
    print(f"inferred_locations written for {len(fl):,} schools; "
          f"{sum(1 for s in fl if s['inferred_locations']):,} have at least one")


if __name__ == "__main__":
    main()
