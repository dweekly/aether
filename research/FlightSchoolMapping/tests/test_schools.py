"""Fixtures for 38_schools.py helpers flagged in review."""

import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("schools", SCRIPTS / "38_schools.py")
schools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schools)


def test_resolve_relative_and_schemeless_sources():
    r = schools.resolve_url
    assert r("aircraft.html", "niceair.com") == "https://niceair.com/aircraft.html"
    assert r("aircraft-2", "louisvilleaviation.com") == "https://louisvilleaviation.com/aircraft-2"
    assert r("inthepattern.com/our-fleet", "inthepattern.com") == "https://inthepattern.com/our-fleet"
    assert r("https://example.com/fleet", "other.com") == "https://example.com/fleet"
    assert r("//cdn.example.com/rates.pdf", "x.com") == "https://cdn.example.com/rates.pdf"
    assert r("", "x.com") is None
    assert r("fleet.html", "") is None


def test_relative_source_counts_as_website_evidence():
    src = schools.resolve_url("aircraft.html", "niceair.com")
    assert schools.norm_domain(src).endswith("niceair.com")


def test_median_of_even_list():
    assert schools.median([120, 130, 160, 250, 350, 350]) == 205


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_known_operators_include_unscored_fleet_rows():
    detect = _load("detect", "40_training_detect.py")
    schools_data = [{"fleet": [{"n": "N1", "registrant": "ACME FLIGHT LLC", "days": 0},
                               {"n": "N2", "registrant": None}]}]
    assert detect.known_operator_registrants(schools_data) == {"ACME FLIGHT LLC"}


def test_airport_aliases_match_local_and_gps_codes():
    privacy = _load("privacy", "41_privacy.py")
    alias = privacy.airport_aliases()
    assert privacy.same_airport("PALH", "KLHD", alias)      # Lake Hood: GPS code vs K + local code
    assert privacy.same_airport("KPAO", "PAO", alias)
    assert not privacy.same_airport("KPAO", "KSQL", alias)


def test_aopa_link_from_numeric_id_only():
    assert schools.aopa_url("29699") == "https://www.aopa.org/destinations/business/29699"
    assert schools.aopa_url(29699) == "https://www.aopa.org/destinations/business/29699"
    assert schools.aopa_url("") is None and schools.aopa_url(None) is None
    assert schools.aopa_url("../x") is None
