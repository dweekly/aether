#!/usr/bin/env python3
"""Finding 4: how trackable is a GA aircraft from free, open data?

All numbers are aggregates. Nothing here publishes a link between a PIA
address and a tail number.

1. Home base coverage: share of aircraft that flew in the window for which
   the summary assigns a home (rules fixed in 34_fleet_summary.py).
2. Home base accuracy: against an independent label, the airport a school
   lists for that aircraft on its own website (fleet rows with airport_id
   and evidence "website"). Inference never reads that field.
3. Weekly pattern: for aircraft with >= 8 flying days, learn P(flies | weekday)
   from the first half of the window and predict the second half day by day.
   Compared with a naive baseline that predicts every day as the aircraft's
   overall majority (fly / don't fly).
4. LADD and PIA: how many LADD-flagged aircraft adsb.lol shows (it does not
   filter LADD), and how many PIA-flagged addresses were observed.

Writes data/release/<release>/privacy.json.
Usage: 41_privacy.py --release dev
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_window import day_view, included_days  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "data" / "history"
RELEASES = ROOT / "data" / "release"


AIRPORTS_CSV = ROOT / "data" / "ourairports_airports.csv"


def airport_aliases(csv_path=AIRPORTS_CSV):
    """Map every identifier form an airport goes by to its OurAirports ident:
    ident, gps_code, local_code, and the K-prefixed local code that school sites
    often write (KLHD for Lake Hood, whose GPS code is PALH)."""
    import csv
    alias = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["type"] == "closed":
                continue
            for code in (r["ident"], r["gps_code"], r["local_code"],
                         "K" + r["local_code"] if len(r["local_code"]) == 3 else ""):
                if code:
                    alias.setdefault(code.upper(), r["ident"])
    return alias


def same_airport(a, b, alias):
    if not a or not b:
        return False
    ca, cb = alias.get(a.upper(), a.upper()), alias.get(b.upper(), b.upper())
    return ca == cb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", required=True)
    a = ap.parse_args()
    rel = RELEASES / a.release
    win = json.loads((rel / "results.json").read_text())["window"]
    c = duckdb.connect()
    day_view(c, "ad", HISTORY / "aircraft_day" / "*" / "*" / "*.parquet", included_days(rel))
    aircraft = f"read_parquet('{rel / 'aircraft.parquet'}')"

    flew, with_home = c.execute(f"SELECT count(*) FILTER (WHERE days_airborne > 0), "
                                f"count(*) FILTER (WHERE days_airborne > 0 AND home_airport IS NOT NULL) FROM {aircraft}").fetchone()
    by_days = c.execute(f"""
        SELECT CASE WHEN days_airborne >= 10 THEN '10+' WHEN days_airborne >= 5 THEN '5-9' ELSE '1-4' END b,
               count(*), count(home_airport) FROM {aircraft} WHERE days_airborne > 0 GROUP BY 1 ORDER BY 1""").fetchall()

    # 2. Accuracy against school-listed airports.
    homes = dict(c.execute(f"SELECT n_number, home_airport FROM {aircraft} WHERE home_airport IS NOT NULL").fetchall())
    schools = json.loads((rel / "schools_full.json").read_text())["schools"]
    alias = airport_aliases()
    labelled, correct = 0, 0
    for s in schools:
        for f in s["fleet"]:
            if f["evidence"] == "website" and f["airport"] and f["n"] in homes:
                labelled += 1
                correct += same_airport(homes[f["n"]], f["airport"], alias)

    # 3. Weekly pattern, first half -> second half.
    d0, d1 = date.fromisoformat(win["first_day"]), date.fromisoformat(win["last_day"])
    mid = d0 + (d1 - d0) / 2
    all_days = [r[0] for r in c.execute("SELECT DISTINCT day FROM ad ORDER BY 1").fetchall()]
    flying = defaultdict(set)
    for hexid, day in c.execute("SELECT hex, day FROM ad WHERE n_airborne > 0").fetchall():
        flying[hexid].add(day)
    train_days = [d for d in all_days if d <= mid]
    test_days = [d for d in all_days if d > mid]
    n_ac = model_hits = base_hits = total = 0
    for hexid, days in flying.items():
        if len(days) < 8:
            continue
        p = {}
        for wd in range(7):
            tr = [d for d in train_days if d.weekday() == wd]
            p[wd] = sum(d in days for d in tr) / len(tr) if tr else 0
        majority = sum(d in days for d in train_days) / max(1, len(train_days)) >= 0.5
        n_ac += 1
        for d in test_days:
            actual = d in days
            model_hits += (p[d.weekday()] >= 0.5) == actual
            base_hits += majority == actual
            total += 1

    # 4. LADD / PIA.
    ladd_aircraft, pia_addresses = c.execute(
        "SELECT count(DISTINCT hex) FILTER (WHERE ladd), count(DISTINCT hex) FILTER (WHERE pia) FROM ad").fetchone()
    ladd_daily = c.execute("SELECT round(avg(n)) FROM (SELECT day, count(*) n FROM ad WHERE ladd GROUP BY day)").fetchone()[0]

    out = {
        "release": a.release, "window": win,
        "home_base": {"flew": flew, "assigned": with_home, "coverage": round(with_home / max(1, flew), 3),
                      "coverage_by_flying_days": [{"days": b, "flew": n, "assigned": h} for b, n, h in by_days],
                      "accuracy_vs_school_listed_airport": {"labelled": labelled, "correct": correct,
                                                            "accuracy": round(correct / max(1, labelled), 3)}},
        "weekly_pattern": {"aircraft": n_ac, "test_days": len(test_days), "predictions": total,
                           "accuracy": round(model_hits / max(1, total), 3),
                           "baseline_accuracy": round(base_hits / max(1, total), 3)},
        "ladd": {"distinct_ladd_aircraft_shown": ladd_aircraft, "per_day_mean": ladd_daily,
                 "note": "adsb.lol does not filter LADD; FAA SWIM subscribers are contractually required to."},
        "pia": {"distinct_pia_addresses_observed": pia_addresses},
    }
    (rel / "privacy.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
