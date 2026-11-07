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


def test_listed_airports_skip_aopa_sourced_locations():
    canon = {"MYF": "KMYF", "KSEE": "KSEE", "PAO": "KPAO"}.get
    locs = [{"airport_id": "MYF", "label": "Main base"}, {"airport_id": "KSEE", "label": "AOPA listed"},
            {"airport_id": "PAO", "label": "Known airport per prior records"}, {"airport_id": "MYF", "label": "Satellite"},
            {"airport_id": "", "label": "Main base"}]
    assert schools.public_listed_airports(locs, lambda x: canon(x, x)) == ["KMYF"]


def test_inferred_airports_from_release_home_bases():
    fleet = [{"home": "KPAO"}, {"home": "KPAO"}, {"home": "KSQL"}, {"home": None}]
    assert schools.inferred_airports(fleet, ["KSQL"]) == [{"id": "KPAO", "tails": 2, "of": 3, "listed": False}]
    assert schools.inferred_airports([{"home": "KHWD"}], []) == [{"id": "KHWD", "tails": 1, "of": 1, "listed": False}]
    assert schools.inferred_airports([{"home": "KPAO"}, {"home": "KPAO"}], ["PAO"])[0]["listed"] is True


def test_download_holds_out_descriptions_and_aopa_link():
    pkg = _load("package", "42_package_release.py")
    full = {"days": [], "schools": [{"key": "a", "aopa_url": "https://www.aopa.org/destinations/business/1",
                                     "fleet": [{"n": "N1", "description": "x", "listed_as": "y"}],
                                     "listings": [{"listed_as": "z", "description": "w"}]}]}
    out = pkg.download_schools(full)["schools"][0]
    assert "aopa_url" not in out and out["fleet"] == [{"n": "N1", "listed_as": "y"}] and out["listings"] == [{"listed_as": "z"}]
    assert full["schools"][0]["fleet"][0]["description"] == "x"  # the site's copy is untouched
