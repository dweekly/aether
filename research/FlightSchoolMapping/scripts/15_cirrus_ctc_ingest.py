#!/usr/bin/env python3
"""
15_cirrus_ctc_ingest.py — Ingest Cirrus Training Network data.

Fetches the Cirrus Aircraft locator API for Certified Training Centers (CTC)
and cross-references against our flight school corpus to:

  1. Tag existing schools as Cirrus Training Centers
  2. Identify CTC schools missing from our corpus
  3. Optionally add missing CTC schools to flight_schools_enriched.json
     for crawling by the fleet pipeline (09a/09b/10)

Data source: https://locator.cirrusaircraft.com/api/v1/ctc
(powers the map at https://cirrusaircraft.com/training-network/)

Usage:
    python 15_cirrus_ctc_ingest.py                # report only
    python 15_cirrus_ctc_ingest.py --add-missing  # add missing schools to enriched
    python 15_cirrus_ctc_ingest.py --tag-flight-line  # tag flight_line entries
    python 15_cirrus_ctc_ingest.py --refresh       # re-fetch from Cirrus API
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR.parent / "output"
CTC_CACHE = OUTPUT_DIR / "cirrus_training_network.json"
ENRICHED = OUTPUT_DIR / "flight_schools_enriched.json"
FLIGHT_LINE = OUTPUT_DIR / "flight_line.json"

CTC_API = "https://locator.cirrusaircraft.com/api/v1/ctc"


def fetch_ctc(force: bool = False) -> list[dict]:
    """Fetch CTC data from Cirrus API, caching locally."""
    if CTC_CACHE.exists() and not force:
        with open(CTC_CACHE) as f:
            return json.load(f)

    print(f"Fetching CTC data from {CTC_API}...")
    resp = requests.get(CTC_API, timeout=30)
    resp.raise_for_status()
    data = resp.json()["data"]
    with open(CTC_CACHE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  Cached {len(data)} training centers to {CTC_CACHE.name}")
    return data


def normalize_domain(url: str) -> str:
    """Extract and normalize domain from URL."""
    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    return urlparse(url).netloc.replace("www.", "").lower().rstrip("/")


def build_corpus_index(schools: list[dict]) -> tuple[dict, dict]:
    """Build lookup indices by domain and by normalized name."""
    by_domain: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for s in schools:
        web = s.get("school_website") or s.get("website") or ""
        domain = normalize_domain(web)
        if domain:
            by_domain[domain] = s

        name = (s.get("school_name") or s.get("name") or "").lower().strip()
        if name:
            by_name[name] = s
    return by_domain, by_name


def match_ctc_to_corpus(
    ctc_entry: dict, by_domain: dict, by_name: dict
) -> dict | None:
    """Try to match a CTC entry to a corpus school."""
    web = ctc_entry.get("training_website__c") or ""
    domain = normalize_domain(web)
    if domain and domain in by_domain:
        return by_domain[domain]

    name = (ctc_entry.get("account_name") or "").lower().strip()
    if name in by_name:
        return by_name[name]

    # Strip airport suffix like " - KGRB"
    clean = name.rsplit(" - ", 1)[0] if " - " in name else name
    if clean in by_name:
        return by_name[clean]

    return None


def ctc_to_enriched_entry(ctc_entry: dict) -> dict:
    """Convert a CTC entry to flight_schools_enriched.json format."""
    name = ctc_entry.get("account_name", "")
    # Clean airport suffix for display name
    clean_name = name.rsplit(" - ", 1)[0] if " - " in name else name

    web = ctc_entry.get("training_website__c") or ""
    if web and not web.startswith("http"):
        web = "https://" + web

    return {
        "name": clean_name,
        "website": web,
        "phone": ctc_entry.get("training_phone__c", ""),
        "email": ctc_entry.get("training_email__c", ""),
        "city": ctc_entry.get("shippingcity", ""),
        "state": ctc_entry.get("shippingstate", ""),
        "zip": ctc_entry.get("shippingpostalcode", ""),
        "address": ctc_entry.get("shippingstreet", ""),
        "airport_code": (ctc_entry.get("airport_name") or "").lstrip("K"),
        "latitude": ctc_entry.get("latitude__c"),
        "longitude": ctc_entry.get("longitude__c"),
        "source": "cirrus_training_network",
        "cirrus_training_center": True,
        "cirrus_relationship": ctc_entry.get("relationship_type__c", ""),
    }


def report(ctc_data: list[dict], corpus: list[dict], corpus_type: str):
    """Print cross-reference report."""
    us_ctc = [d for d in ctc_data if d.get("shippingcountry") == "United States"]
    intl = [d for d in ctc_data if d.get("shippingcountry") != "United States"]

    by_domain, by_name = build_corpus_index(corpus)

    matched = []
    unmatched = []
    for d in us_ctc:
        m = match_ctc_to_corpus(d, by_domain, by_name)
        if m:
            matched.append((d, m))
        else:
            unmatched.append(d)

    # Check Cirrus in fleet for matched schools
    cirrus_kw = ["cirrus", "sr20", "sr22", "sf50"]

    def has_cirrus(fleet):
        for ac in fleet:
            text = (
                (ac.get("display_name") or "")
                + " "
                + (ac.get("description") or "")
            ).lower()
            if any(kw in text for kw in cirrus_kw):
                return True
        return False

    with_cirrus = 0
    no_cirrus = 0
    no_fleet = 0
    for ctc_entry, fl_entry in matched:
        fleet = fl_entry.get("fleet", [])
        if not fleet:
            no_fleet += 1
        elif has_cirrus(fleet):
            with_cirrus += 1
        else:
            no_cirrus += 1

    print("=" * 60)
    print("Cirrus Training Network Cross-Reference")
    print("=" * 60)
    print(f"  Total CTC entries:     {len(ctc_data)}")
    print(f"    US:                  {len(us_ctc)}")
    print(f"    International:       {len(intl)}")
    print()
    print(f"  Matched to {corpus_type}: {len(matched)}")
    print(f"    With Cirrus fleet:   {with_cirrus}")
    print(f"    Fleet, no Cirrus:    {no_cirrus}")
    print(f"    No fleet data:       {no_fleet}")
    print(f"  Not in corpus:         {len(unmatched)}")
    print()

    if unmatched:
        print(f"Missing CTC schools ({len(unmatched)}):")
        for d in sorted(unmatched, key=lambda x: x.get("shippingstate", "")):
            apt = d.get("airport_name", "")
            city = d.get("shippingcity", "")
            state = d.get("shippingstate", "")
            web = d.get("training_website__c", "") or "(no website)"
            print(f"  {d['account_name']:<45} {apt:<6} {city}, {state:<4} {web}")

    return matched, unmatched


def add_missing_to_enriched(unmatched: list[dict]):
    """Add unmatched CTC schools to flight_schools_enriched.json."""
    if not ENRICHED.exists():
        print(f"  Error: {ENRICHED} not found")
        return

    with open(ENRICHED) as f:
        enriched = json.load(f)

    existing_domains = set()
    for s in enriched:
        web = s.get("website") or ""
        d = normalize_domain(web)
        if d:
            existing_domains.add(d)

    added = 0
    for ctc_entry in unmatched:
        web = ctc_entry.get("training_website__c") or ""
        domain = normalize_domain(web)
        if domain and domain in existing_domains:
            continue  # Already present under different name

        entry = ctc_to_enriched_entry(ctc_entry)
        enriched.append(entry)
        existing_domains.add(domain)
        added += 1

    with open(ENRICHED, "w") as f:
        json.dump(enriched, f, indent=2)
    print(f"  Added {added} CTC schools to {ENRICHED.name} (total: {len(enriched)})")


def tag_flight_line(matched: list[tuple[dict, dict]]):
    """Tag matched flight_line entries as Cirrus Training Centers."""
    if not FLIGHT_LINE.exists():
        print(f"  Error: {FLIGHT_LINE} not found")
        return

    with open(FLIGHT_LINE) as f:
        flight_line = json.load(f)

    # Build reverse lookup: corpus entry id → CTC data
    ctc_by_domain = {}
    ctc_by_name = {}
    for ctc_entry, fl_entry in matched:
        web = fl_entry.get("school_website") or ""
        domain = normalize_domain(web)
        if domain:
            ctc_by_domain[domain] = ctc_entry
        name = (fl_entry.get("school_name") or "").lower().strip()
        if name:
            ctc_by_name[name] = ctc_entry

    tagged = 0
    for school in flight_line:
        web = school.get("school_website") or ""
        domain = normalize_domain(web)
        name = (school.get("school_name") or "").lower().strip()

        ctc_entry = ctc_by_domain.get(domain) or ctc_by_name.get(name)
        if ctc_entry:
            school["cirrus_training_center"] = True
            school["cirrus_relationship"] = ctc_entry.get(
                "relationship_type__c", ""
            )
            tagged += 1

    with open(FLIGHT_LINE, "w") as f:
        json.dump(flight_line, f, indent=2)
    print(f"  Tagged {tagged} schools in {FLIGHT_LINE.name}")


def main():
    parser = argparse.ArgumentParser(description="Ingest Cirrus Training Network")
    parser.add_argument(
        "--refresh", action="store_true", help="Re-fetch from Cirrus API"
    )
    parser.add_argument(
        "--add-missing",
        action="store_true",
        help="Add missing CTC schools to enriched",
    )
    parser.add_argument(
        "--tag-flight-line",
        action="store_true",
        help="Tag flight_line entries as CTC",
    )
    args = parser.parse_args()

    ctc_data = fetch_ctc(force=args.refresh)

    # Use flight_line as primary corpus if available, else enriched
    if FLIGHT_LINE.exists():
        with open(FLIGHT_LINE) as f:
            corpus = json.load(f)
        corpus_type = "flight_line"
    elif ENRICHED.exists():
        with open(ENRICHED) as f:
            corpus = json.load(f)
        corpus_type = "enriched"
    else:
        print("Error: No corpus file found")
        sys.exit(1)

    matched, unmatched = report(ctc_data, corpus, corpus_type)

    if args.tag_flight_line:
        tag_flight_line(matched)

    if args.add_missing:
        add_missing_to_enriched(unmatched)


if __name__ == "__main__":
    main()
