#!/usr/bin/env python3
"""
08c_aopa_enrich.py — Enrich AOPA schools via the Airports REST API.

The Coveo search index (used by 08b) omits key fields like website, email,
and mailing address. The AOPA Airports API at webapp.aopa.org/AirportsAPI
has the full records.

This script:
  1. Reads business IDs from output/aopa_flight_schools.json (the aopa_url field)
  2. Gets a bearer token from authapi.aopa.org
  3. Fetches each business record from the Airports API (concurrent)
  4. Merges website, email, phone, address, contacts, hours, airport ICAO
  5. Writes enriched output to aopa_flight_schools.json (overwrites)

Usage:
    python 08c_aopa_enrich.py                 # enrich all schools
    python 08c_aopa_enrich.py --resume        # skip already-enriched records
    python 08c_aopa_enrich.py --workers 20    # adjust concurrency (default: 10)
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import os
import sys
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[3] / ".env")  # AOPA web-app keys live in .env, not in the repo

import requests
from tqdm import tqdm

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
CACHE_PATH = OUTPUT_DIR / "aopa_website_cache.json"

# AOPA Airports API — client credentials from the public SPA
AUTH_URL = "https://authapi.aopa.org/identity/connect/token"
AUTH_BODY = {
    "grant_type": "client_credentials",
    "client_id": "AOPAAirports",
    "client_secret": os.environ.get("AOPA_CLIENT_SECRET") or sys.exit("AOPA_CLIENT_SECRET not set (.env)"),
    "scope": "AirportsAPI",
}
API_BASE = "https://webapp.aopa.org/AirportsAPI"


def get_token() -> str:
    """Get a bearer token (valid 8 hours)."""
    resp = requests.post(AUTH_URL, data=AUTH_BODY, timeout=15)
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_business(session: requests.Session, business_id: int) -> dict | None:
    """Fetch a single business record from the Airports API."""
    try:
        resp = session.get(
            f"{API_BASE}/businesses/{business_id}",
            timeout=15,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  Error fetching business {business_id}: {e}")
        return None


def extract_enrichment(biz: dict) -> dict:
    """Extract the fields we want from an Airports API business record."""
    enriched = {}

    # Core contact info
    enriched["website"] = (biz.get("webAddress") or "").strip()
    if enriched["website"] and not enriched["website"].startswith("http"):
        enriched["website"] = "http://" + enriched["website"]
    enriched["email"] = (biz.get("emailAddress") or "").strip()
    enriched["phone"] = (biz.get("phone1") or "").strip()
    enriched["fax"] = (biz.get("fax") or "").strip()
    enriched["hours"] = (biz.get("hours") or "").strip()

    # Mailing/business address
    for addr in biz.get("addresses", []):
        if addr.get("type") == "business":
            enriched["address1"] = (addr.get("addr1") or "").strip()
            enriched["address2"] = (addr.get("addr2") or "").strip()
            enriched["zip"] = (addr.get("zip") or "").strip()

    # Contacts (name, title, phone, email for each)
    contacts = []
    for c in biz.get("businessContacts", []):
        if not c.get("okToPublish"):
            continue
        contacts.append({
            "name": (c.get("contactName") or "").strip(),
            "title": (c.get("titleDept") or "").strip(),
            "phone": (c.get("phone") or "").strip(),
            "email": (c.get("email") or "").strip(),
        })
    if contacts:
        enriched["contacts"] = contacts

    # Airport ICAO from the associations
    airports = biz.get("airports", [])
    if airports:
        icao = airports[0].get("icaoId") or airports[0].get("faaId") or ""
        enriched["airport_icao"] = icao
        if len(airports) > 1:
            enriched["additional_airports"] = [
                a.get("icaoId") or a.get("faaId") or "" for a in airports[1:]
            ]

    # Flight school details
    fs = biz.get("businessFlightSchool", {})
    if fs:
        enriched["instructors_full_time"] = fs.get("instructorFull")
        enriched["instructors_part_time"] = fs.get("instructorPart")

    # FTE award
    enriched["fte_award_year"] = biz.get("fteAwardYear") or ""

    # Description
    enriched["description_api"] = (biz.get("description") or "").strip()

    # DBA name
    enriched["dba"] = (biz.get("doBusAs") or "").strip()

    # Business hours (structured)
    hours_list = biz.get("businessHours", [])
    if hours_list:
        enriched["structured_hours"] = [
            {
                "days": f"{h.get('fromWeekDay','')}-{h.get('toWeekDay','')}",
                "from": h.get("fromTime", ""),
                "to": h.get("toTime", ""),
            }
            for h in hours_list
        ]

    return enriched


def run(resume: bool = False, workers: int = 10):
    print("=" * 60)
    print("AOPA Airports API Enrichment")
    print("=" * 60)

    # Load existing schools
    json_path = OUTPUT_DIR / "aopa_flight_schools.json"
    schools = json.load(open(json_path))
    print(f"\nLoaded {len(schools):,} schools from {json_path.name}")

    # Extract business IDs — handles both /business/{id} and /flyingclub/{id}
    id_map = {}  # business_id -> index in schools list
    for i, s in enumerate(schools):
        url = s.get("aopa_url", "")
        m = re.search(r"/(?:business|flyingclub)/(\d+)", url)
        if m:
            id_map[int(m.group(1))] = i
    print(f"Business IDs found: {len(id_map):,}")

    # Skip already-enriched if resuming
    if resume:
        already = {bid for bid, idx in id_map.items() if schools[idx].get("website")}
        to_fetch = {bid: idx for bid, idx in id_map.items() if bid not in already}
        print(f"Already enriched: {len(already):,}, remaining: {len(to_fetch):,}")
    else:
        to_fetch = id_map

    if not to_fetch:
        print("Nothing to fetch!")
        return

    # Get token
    print("\n[1/3] Authenticating...")
    token = get_token()
    print(f"  Got bearer token (len={len(token)})")

    # Create session with auth
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Origin": "https://www.aopa.org",
        "Referer": "https://www.aopa.org/",
    })

    # Fetch all businesses concurrently
    print(f"\n[2/3] Fetching {len(to_fetch):,} business records ({workers} workers)...")
    results = {}  # business_id -> enrichment dict
    errors = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_business, session, bid): bid
            for bid in to_fetch.keys()
        }
        for future in tqdm(as_completed(futures), total=len(futures), desc="  Fetching"):
            bid = futures[future]
            try:
                biz = future.result()
                if biz:
                    results[bid] = extract_enrichment(biz)
                else:
                    errors += 1
            except Exception as e:
                print(f"  Error for {bid}: {e}")
                errors += 1

    print(f"  Fetched: {len(results):,} OK, {errors} errors")

    # Merge into schools
    print(f"\n[3/3] Merging...")
    with_website = 0
    with_email = 0
    with_contacts = 0
    for bid, enrichment in results.items():
        idx = to_fetch[bid]
        schools[idx].update(enrichment)
        if enrichment.get("website"):
            with_website += 1
        if enrichment.get("email"):
            with_email += 1
        if enrichment.get("contacts"):
            with_contacts += 1

    print(f"  Enriched fields:")
    print(f"    With website: {with_website:,}")
    print(f"    With email: {with_email:,}")
    print(f"    With contacts: {with_contacts:,}")

    # Total counts across all schools
    total_web = sum(1 for s in schools if s.get("website"))
    total_email = sum(1 for s in schools if s.get("email"))
    print(f"\n  Total schools with website: {total_web:,} / {len(schools):,}")
    print(f"  Total schools with email: {total_email:,} / {len(schools):,}")

    # Save
    with open(json_path, "w") as f:
        json.dump(schools, f, indent=2)
    print(f"\n  Saved: {json_path}")

    # Also save a quick CSV of websites for easy review
    web_csv = OUTPUT_DIR / "aopa_websites.csv"
    import csv
    with open(web_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "state", "airport_id", "website", "email", "phone"])
        for s in schools:
            writer.writerow([
                s.get("name", ""),
                s.get("state", ""),
                s.get("airport_id", ""),
                s.get("website", ""),
                s.get("email", ""),
                s.get("phone", ""),
            ])
    print(f"  Saved: {web_csv}")

    print("\nDone.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true", help="Skip already-enriched")
    parser.add_argument("--workers", type=int, default=10, help="Concurrent workers")
    args = parser.parse_args()
    run(resume=args.resume, workers=args.workers)
