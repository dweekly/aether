#!/usr/bin/env python3
"""
Scrape the AOPA Flight School Finder via their Coveo search API.

The AOPA directory (aopa.org/training-and-safety/learn-to-fly/flight-schools)
is powered by a Coveo search index. This script pages through all Flight School
results and writes a normalized CSV + JSON.

Output: output/aopa_flight_schools.csv, output/aopa_flight_schools.json

Total schools: ~2,255 as of March 2025.  The API returns up to 200 results per
page, so we need ~12 requests — fast and lightweight.
"""

import csv
import json
import time
from pathlib import Path

import requests
from tqdm import tqdm

OUTPUT_DIR = Path(__file__).parent.parent / "output"

COVEO_ENDPOINT = "https://aopa9nc4vnbc.org.coveo.com/rest/search/v2"
COVEO_TOKEN = "AOPA_PUBLIC_TOKEN_REDACTED"
PAGE_SIZE = 200
DELAY_BETWEEN_REQUESTS = 1.0  # be polite

# Fields we want to extract from each Coveo result's `raw` dict.
FIELD_MAP = {
    "aopaufbusinessname": "name",
    "aopaufcity": "city",
    "aopaufstate": "state",
    "aopaufstatename": "state_name",
    "aopaufphone": "phone",
    "aopaufwebsite": "website",
    "aopaufairportident": "airport_id",
    "aopaufairportname": "airport_name",
    "aopauflatitude": "latitude",
    "aopauflongitude": "longitude",
    "aopaufbusinessdescription": "description",
    "aopaufflightschoolaward": "aopa_award",
    "aopaufflighttrainingvaapproved": "va_approved",
    "aopaufflighttrainingclassifications": "classifications",
    "aopaufflighttrainingcertifications": "certifications",
    "aopaufflighttrainingratings": "ratings",
    "aopaufflighttrainingservices": "services",
    "aopaufflighttrainingadvanced": "advanced_training",
    "aopaufaircraftrental": "aircraft_rental",
    "aopaufsimulatorrental": "simulator_rental",
    "aopaufflyingclubmembers": "flying_club_members",
}


def fetch_page(first_result: int) -> dict:
    """Fetch one page of results from the Coveo API."""
    resp = requests.post(
        COVEO_ENDPOINT,
        headers={
            "Authorization": f"Bearer {COVEO_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "q": "",
            "aq": '@aopauftype=="Flight School"',
            "firstResult": first_result,
            "numberOfResults": PAGE_SIZE,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def extract_school(result: dict) -> dict:
    """Extract and normalize a school record from a Coveo result."""
    raw = result.get("raw", {})
    school = {}
    for coveo_field, our_field in FIELD_MAP.items():
        val = raw.get(coveo_field, "")
        # List fields → semicolon-separated string
        if isinstance(val, list):
            val = "; ".join(str(v) for v in val)
        school[our_field] = val if val else ""

    # AOPA detail page
    school["aopa_url"] = raw.get("clickableuri", raw.get("sysuri", ""))
    school["source"] = "aopa"
    return school


def run():
    """Fetch all AOPA flight schools."""
    print("=" * 60)
    print("AOPA Flight School Directory Scraper")
    print("=" * 60)

    # First request to get total count
    print("\n[1/3] Querying AOPA Coveo API...")
    data = fetch_page(0)
    total = data["totalCountFiltered"]
    print(f"  Total schools: {total:,}")

    schools = [extract_school(r) for r in data["results"]]
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE

    # Remaining pages
    print(f"\n[2/3] Fetching {pages} pages of {PAGE_SIZE}...")
    for page in tqdm(range(1, pages), desc="  Pages"):
        time.sleep(DELAY_BETWEEN_REQUESTS)
        data = fetch_page(page * PAGE_SIZE)
        schools.extend(extract_school(r) for r in data["results"])

    print(f"  Fetched {len(schools):,} school records")

    # Save
    print("\n[3/3] Saving...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Stable column order
    columns = [
        "name", "city", "state", "state_name", "phone", "website",
        "airport_id", "airport_name", "latitude", "longitude",
        "classifications", "certifications", "ratings", "services",
        "advanced_training", "aircraft_rental", "simulator_rental",
        "aopa_award", "va_approved", "flying_club_members",
        "description", "aopa_url", "source",
    ]

    csv_path = OUTPUT_DIR / "aopa_flight_schools.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(schools)
    print(f"  CSV: {csv_path} ({len(schools):,} rows)")

    json_path = OUTPUT_DIR / "aopa_flight_schools.json"
    with open(json_path, "w") as f:
        json.dump(schools, f, indent=2)
    print(f"  JSON: {json_path}")

    # Stats
    from collections import Counter
    states = Counter(s["state"] for s in schools if s["state"])
    print(f"\n  States represented: {len(states)}")
    print(f"  Top 10 states:")
    for st, count in states.most_common(10):
        print(f"    {st}: {count}")

    with_airport = sum(1 for s in schools if s["airport_id"])
    print(f"\n  With airport identifier: {with_airport:,} / {len(schools):,} ({100*with_airport/len(schools):.0f}%)")

    with_coords = sum(1 for s in schools if s["latitude"])
    print(f"  With coordinates: {with_coords:,} / {len(schools):,} ({100*with_coords/len(schools):.0f}%)")

    awards = sum(1 for s in schools if s["aopa_award"] == "Yes")
    print(f"  AOPA award winners: {awards}")

    print(f"\nDone.")
    return schools


if __name__ == "__main__":
    run()
