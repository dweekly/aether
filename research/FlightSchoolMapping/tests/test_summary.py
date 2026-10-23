"""Fixtures for 34_fleet_summary.py: one aircraft per classification branch.

Builds a tiny registry snapshot and archive-day tables under tmp_path, runs the
real script with AETHER_DATA pointed there, and checks every branch.
"""

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def ad(day, hexid, sources, ground_apt=None, airborne=30.0, n_airborne=5, ground_ts=50.0):
    return {"day": day, "hex": hexid, "reg": None, "type": "C172", "desc": None, "pia": False, "ladd": hexid == "a00005",
            "airborne_min": airborne, "n_airborne": n_airborne, "n_legs": 1, "n_points": 10,
            "last_ground_airport": ground_apt, "last_ground_ts": ground_ts if ground_apt else None,
            "last_ts": 0.0, "sources": json.dumps(sources)}


def leg(day, hexid, arr, end_ts=100.0):
    return {"day": day, "hex": hexid, "arr": arr, "end_ts": end_ts, "end_observed_low": True}


def test_classification_branches(tmp_path):
    reg = tmp_path / "registry" / "2026-10-23"
    master = [{"n_number": f"N{i}", "mode_s_code_hex": f"a0000{i}", "serial_number": str(i), "mfr_mdl_code": "X",
               "year_mfr": "1975", "type_aircraft_label": "Fixed wing single engine", "type_engine_label": "Reciprocating",
               "type_registrant_label": "Corporation", "name": "ACME FLIGHT", "city": "PALO ALTO", "state": "CA",
               "status_code": "V", "status_label": "Valid registration", "status_valid": True,
               "cert_issue_date": "", "expiration_date": "", "last_action_date": ""} for i in range(1, 10)]
    write(reg / "master.parquet", master)
    write(reg / "acftref.parquet", [{"code": "X", "mfr": "CESSNA", "model": "172", "no_seats": "4", "no_eng": "1"}])
    days = [date(2026, 10, d) for d in (1, 2, 3, 4)]
    adsb, mlat = {"adsb_icao": 5}, {"mlat": 5}
    rows = []
    rows += [ad(d, "a00001", adsb, "KPAO") for d in days]            # adsb, home KPAO (4/4 nights)
    rows += [ad(d, "a00002", mlat, "KSQL") for d in days[:2]]        # other_only, too few nights -> no home
    # a00003 never observed -> not_observed
    rows += [ad(d, "a00004", adsb, None) for d in days[:3]]          # no ground point; home from last arrival
    rows += [ad(days[0], "a00005", adsb, "KPAO"), ad(days[1], "a00005", adsb, "KSQL"),
             ad(days[2], "a00005", adsb, "KHAF"), ad(days[3], "a00005", adsb, "KOAK")]  # no airport >= 50% -> abstain
    rows += [ad(days[0], "affff0", adsb, None)]                      # US-block address not on the registry
    rows += [ad(days[0], "a00007", adsb, None, airborne=0.0, n_airborne=1)]  # one airborne point, 0 qualified minutes
    rows += [ad(days[0], "a00008", adsb, "KPAO"), ad(days[1], "a00008", adsb, "KPAO"),
             ad(days[2], "a00008", adsb, "KSQL")]                    # winner has 2 nights: abstain
    rows += [ad(d, "a00009", adsb, "KPAO", ground_ts=10.0) for d in days[:3]]  # morning ground fix, later arrival
    for d in days:
        write(tmp_path / "history" / "aircraft_day" / "2026" / "10" / f"{d.day:02d}.parquet", [r for r in rows if r["day"] == d])
        legs = [leg(d, "a00004", "KSQL"), leg(d, "a00009", "KSQL", end_ts=100.0)] if d in days[:3] else [leg(d, "a00001", "KPAO")]
        write(tmp_path / "history" / "legs" / "2026" / "10" / f"{d.day:02d}.parquet", legs)
    env = dict(os.environ, AETHER_DATA=str(tmp_path))
    subprocess.run([sys.executable, str(SCRIPTS / "34_fleet_summary.py"), "--registry", "2026-10-23", "--release", "t"],
                   env=env, check=True, capture_output=True)
    out = {r["n_number"]: r for r in pq.read_table(tmp_path / "release" / "t" / "aircraft.parquet").to_pylist()}
    assert out["N1"]["observed_class"] == "adsb" and out["N1"]["home_airport"] == "KPAO"
    assert out["N2"]["observed_class"] == "other_only" and out["N2"]["home_airport"] is None
    assert out["N3"]["observed_class"] == "not_observed" and out["N3"]["days_observed"] == 0
    assert out["N4"]["home_airport"] == "KSQL"            # night-airport fallback
    assert out["N5"]["home_airport"] is None              # abstains below 50% share
    assert out["N5"]["ladd_flag"] is True
    assert out["N6"]["observed_class"] == "not_observed"
    assert out["N7"]["days_airborne"] == 1                # airborne point counts even with 0 minutes
    assert out["N8"]["home_airport"] is None              # 2 of 3 nights is not enough nights
    assert out["N9"]["home_airport"] == "KSQL"            # later arrival beats morning ground fix
    unmatched = pq.read_table(tmp_path / "release" / "t" / "unmatched.parquet").to_pylist()
    assert [u["hex"] for u in unmatched] == ["affff0"]
    results = json.loads((tmp_path / "release" / "t" / "results.json").read_text())
    assert results["window"] == {"first_day": "2026-10-01", "last_day": "2026-10-04", "days": 4,
                                 "included_days": ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]}
    assert sum(t["n"] for t in results["totals"]) == 9
