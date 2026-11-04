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
            "last_ground_lat": 37.46 if ground_apt else None, "last_ground_lon": -122.11 if ground_apt else None,
            "last_ts": 0.0, "sources": json.dumps(sources)}


def leg(day, hexid, arr, end_ts=100.0):
    return {"day": day, "hex": hexid, "dep": None, "arr": arr, "start_ts": end_ts - 50, "end_ts": end_ts,
            "end_observed_low": True, "end_lat": 37.51, "end_lon": -122.25, "airborne_min": 10.0, "max_alt_ft": 1500}


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

    # Last parked (35): the latest ground fix or low arrival, every field from that one event.
    subprocess.run([sys.executable, str(SCRIPTS / "35_export_web.py"), "--release", "t"],
                   env=env, check=True, capture_output=True)
    web = {}
    for f in (tmp_path / "release" / "t" / "web" / "t").glob("*.json"):
        web.update(json.loads(f.read_text()))
    assert web["N9"]["park"] == {"day": "2026-10-03", "apt": "KSQL", "lat": 37.51, "lon": -122.25}  # arrival after ground fix
    assert web["N4"]["park"]["apt"] == "KSQL"            # arrivals only, no ground fix
    assert web["N1"]["park"]["day"] == "2026-10-04"
    assert "park" not in web["N2"] or web["N2"]["park"]["apt"] == "KSQL"

    # --from drops earlier archive days from every count, including the night-airport vote.
    subprocess.run([sys.executable, str(SCRIPTS / "34_fleet_summary.py"), "--registry", "2026-10-23", "--release", "w",
                    "--from", "2026-10-02"], env=env, check=True, capture_output=True)
    out = {r["n_number"]: r for r in pq.read_table(tmp_path / "release" / "w" / "aircraft.parquet").to_pylist()}
    assert out["N1"]["days_observed"] == 3
    assert out["N7"]["observed_class"] == "not_observed"  # seen only on 10-01
    assert out["N8"]["home_airport"] is None              # KPAO now 1 of 2 nights
    results = json.loads((tmp_path / "release" / "w" / "results.json").read_text())
    assert results["window"]["included_days"] == ["2026-10-02", "2026-10-03", "2026-10-04"]


def test_crowded_shards_split_into_parts():
    import importlib.util
    spec = importlib.util.spec_from_file_location("export", SCRIPTS / "35_export_web.py")
    export = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(export)
    pad = "x" * 200
    tails = ("N172", "N1720", "N172SP", "N17234", "N1729Z")
    shards = {"172": {n: {"n": n, "pad": pad} for n in tails}, "5__": {"N5": {"n": "N5"}}}
    files, parts = export.split_large(shards, limit=500, part_bytes=500)
    # The small shard stays whole; the crowded one becomes parts, and every record lands
    # in the part a lookup computes from its N-number alone.
    assert "5__" in files and "172" not in files and set(parts) == {"172"} and parts["172"] >= 2
    assert {n for recs in files.values() for n in recs} == set(tails) | {"N5"}
    for n in tails:
        assert n in files[f"172-{export.part_of(n, parts['172'])}"]
    assert export.part_of("N172", 3) == 0  # no fourth character -> "_", index 0
    # Under the limit nothing splits.
    assert export.split_large(shards, limit=10_000) == (shards, {})
