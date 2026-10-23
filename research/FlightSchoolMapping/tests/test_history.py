"""Fixtures for the archive reduction and the airport matcher.

Run from research/FlightSchoolMapping: scripts/.venv/bin/python -m pytest tests -q
"""

import importlib.util
import random
import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from airport_index import AirportIndex, haversine_nm  # noqa: E402

spec = importlib.util.spec_from_file_location("hist", SCRIPTS / "30_adsblol_history.py")
hist = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hist)

DAY = date(2026, 10, 22)
KPAO = (37.461111, -122.115000)   # Palo Alto
KSQL = (37.511944, -122.249444)   # San Carlos


def pt(t, lat, lon, alt, flags=0, src="adsb_icao", details=None):
    return [t, lat, lon, alt, 90.0, 0.0, flags, 0, details, src, None, None, None, None]


def doc(points, hexid="a12345", reg="N12345", db_flags=0):
    return {"icao": hexid, "r": reg, "t": "C172", "desc": "CESSNA 172", "dbFlags": db_flags,
            "timestamp": 1792627200.0, "trace": points}


@pytest.fixture(scope="module")
def airports():
    return AirportIndex()


def test_airport_index_matches_brute_force(airports):
    rng = random.Random(7)
    us = [i for i in range(len(airports.idents))
          if 25 < airports.lats[i] < 49 and -125 < airports.lons[i] < -67]
    for i in rng.sample(us, 300):
        lat = airports.lats[i] + rng.uniform(-0.03, 0.03)
        lon = airports.lons[i] + rng.uniform(-0.03, 0.03)
        m = airports.nearest(lat, lon, max_nm=50)
        best = min(range(len(airports.idents)),
                   key=lambda j: haversine_nm(lat, lon, airports.lats[j], airports.lons[j]))
        true_nm = haversine_nm(lat, lon, airports.lats[best], airports.lons[best])
        assert m is not None
        # The pick is the true nearest, or a runway airport inside the 0.5 nm ambiguity window.
        assert true_nm - 1e-3 <= m.distance_nm < true_nm + 0.5


def test_ground_only_never_counts_as_flying(airports):
    row, legs = hist.reduce_trace(doc([pt(0, *KPAO, "ground"), pt(60, *KPAO, "ground")]), DAY)
    assert row["airborne_min"] == 0
    assert row["n_airborne"] == 0
    assert row["last_ground_airport"] == "KPAO"
    assert len(legs) == 1 and legs[0]["airborne_min"] == 0


def test_coverage_gap_is_not_flying(airports):
    pts = [pt(0, *KPAO, 2000), pt(60, *KPAO, 2000), pt(60 + 600, *KSQL, 2000)]  # 10-min hole
    row, _ = hist.reduce_trace(doc(pts), DAY)
    assert row["airborne_min"] == pytest.approx(1.0)


def test_stale_point_gap_is_not_flying(airports):
    pts = [pt(0, *KPAO, 2000), pt(60, *KPAO, 2000, flags=1)]
    row, _ = hist.reduce_trace(doc(pts), DAY)
    assert row["airborne_min"] == 0


def test_new_leg_flag_splits_and_sets_endpoints(airports):
    pts = [pt(0, *KPAO, "ground"), pt(60, *KPAO, 800), pt(120, *KSQL, 900), pt(180, *KSQL, "ground"),
           pt(240, *KSQL, "ground", flags=2), pt(300, *KSQL, 700), pt(360, *KPAO, 600)]
    row, legs = hist.reduce_trace(doc(pts), DAY)
    assert row["n_legs"] == 2
    assert (legs[0]["dep"], legs[0]["arr"]) == ("KPAO", "KSQL")
    assert (legs[1]["dep"], legs[1]["arr"]) == ("KSQL", "KPAO")


def test_leg_starting_in_flight_has_unknown_departure(airports):
    pts = [pt(0, 39.0, -110.0, 9500), pt(60, 39.01, -110.0, 9500), pt(120, *KPAO, "ground")]
    _, legs = hist.reduce_trace(doc(pts), DAY)
    assert legs[0]["dep"] is None and legs[0]["start_observed_low"] is False
    assert legs[0]["arr"] == "KPAO"


def test_long_silence_starts_new_leg(airports):
    pts = [pt(0, *KPAO, 2000), pt(60, *KPAO, 2000), pt(60 + 3600, *KSQL, 2000)]
    row, legs = hist.reduce_trace(doc(pts), DAY)
    assert row["n_legs"] == 2


def test_sources_keep_original_labels(airports):
    pts = [pt(0, *KPAO, 2000, src="adsb_icao"), pt(10, *KPAO, 2000, src="mlat"),
           pt(20, *KPAO, 2000, src="tisb_trackfile")]
    row, _ = hist.reduce_trace(doc(pts), DAY)
    assert row["sources"] == '{"adsb_icao": 1, "mlat": 1, "tisb_trackfile": 1}'


def test_db_flags(airports):
    row, _ = hist.reduce_trace(doc([pt(0, *KPAO, "ground")], db_flags=4 | 8), DAY)
    assert row["pia"] and row["ladd"]


def test_us_selection():
    assert hist.is_us("a12345", None)
    assert hist.is_us("c01234", "N123AB")      # N-registered on a non-US-block address
    assert not hist.is_us("c01234", "C-GABC")
    assert not hist.is_us("~a1234", None)      # non-ICAO TIS-B track ids


def test_auto_replica_takes_the_larger(monkeypatch):
    sizes = {"prod-0": [1_961, 0], "staging-0": [2_000, 948]}
    monkeypatch.setattr(hist, "find_release",
                        lambda day, inst: (f"tag-{inst}", [{"name": f"p{i}", "size": s} for i, s in enumerate(sizes[inst])]))
    inst, tag, parts, seen = hist.choose_release(DAY, "auto")
    assert inst == "staging-0" and seen == {"prod-0": 1_961, "staging-0": 2_948}


def test_auto_replica_prefers_prod_on_tie(monkeypatch):
    monkeypatch.setattr(hist, "find_release", lambda day, inst: (inst, [{"name": "a", "size": 100}]))
    assert hist.choose_release(DAY, "auto")[0] == "prod-0"


def test_unsplit_tar_release_is_accepted(monkeypatch):
    monkeypatch.setattr(hist, "gh_json", lambda url: {"assets": [
        {"name": "v2025.01.01-planes-readsb-prod-0.tar", "size": 10},
        {"name": "README.md", "size": 1}]})
    tag, parts = hist.find_release(date(2025, 1, 1), "prod-0")
    assert [p["name"] for p in parts] == ["v2025.01.01-planes-readsb-prod-0.tar"]


def test_runway_airport_beats_nearby_heliport(airports):
    # Over Schaumburg's runway; Clarke Heliport (LL95) is 0.2 nm away and nearer to this point.
    m = airports.nearest(41.9866, -88.1006)
    assert m.ident == "06C" and m.type == "small_airport"
