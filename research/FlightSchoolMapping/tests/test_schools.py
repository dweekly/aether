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
