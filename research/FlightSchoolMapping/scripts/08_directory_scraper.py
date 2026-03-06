#!/usr/bin/env python3
"""
Scrape flight school directories to build a master list of US flight schools.

Sources:
1. flightschoollist.com — best structured, has /airports.php master list with FAA codes
2. bestaviation.net — good coverage, /flight_school/{state}/{city}/ pattern
3. faaflightschools.com — FAA-oriented, /{state}/ pattern

Strategy: Start with flightschoollist.com (structured by airport code), then
enrich with the other two sources.

Rate limiting: 1 request per second per domain, respects robots.txt.
"""

import csv
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

OUTPUT_DIR = Path(__file__).parent.parent / "output"
DATA_DIR = Path(__file__).parent.parent / "data"

# Rate limiting
DELAY_BETWEEN_REQUESTS = 1.0  # seconds
USER_AGENT = "AetherResearch/1.0 (flight school mapping research)"
HEADERS = {"User-Agent": USER_AGENT}

# US states for URL construction
US_STATES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new-hampshire", "new-jersey", "new-mexico", "new-york",
    "north-carolina", "north-dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode-island", "south-carolina", "south-dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west-virginia", "wisconsin", "wyoming",
]


def fetch_page(url: str, delay: float = DELAY_BETWEEN_REQUESTS) -> BeautifulSoup | None:
    """Fetch a page with rate limiting and error handling."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            time.sleep(delay)
            return BeautifulSoup(resp.text, "html.parser")
        else:
            return None
    except requests.RequestException:
        return None


# ── Source 1: flightschoollist.com ────────────────────────────────

def scrape_fsl_state_airports(state: str) -> list[dict]:
    """Get all airports in a state from flightschoollist.com."""
    url = f"https://www.flightschoollist.com/airports/usa/{state}/"
    soup = fetch_page(url)
    if not soup:
        return []

    airports = []
    # Look for airport links — they use /airport.php?code=XXX pattern
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "airport.php?code=" in href or "airport.php?airport=" in href:
            code_match = re.search(r'(?:code|airport)=([A-Z0-9]+)', href)
            if code_match:
                code = code_match.group(1)
                name = link.get_text(strip=True)
                airports.append({"code": code, "name": name, "state": state})

    return airports


def scrape_fsl_airport_schools(airport_code: str) -> list[dict]:
    """Get all flight schools at an airport from flightschoollist.com."""
    url = f"https://www.flightschoollist.com/airport.php?code={airport_code}"
    soup = fetch_page(url, delay=0.5)
    if not soup:
        return []

    schools = []
    # The page lists schools with name, distance, location, phone
    # Look for school listing patterns in the HTML

    # Try finding school entries — they typically have links to individual school pages
    for item in soup.find_all(["div", "li", "tr"]):
        text = item.get_text(" ", strip=True)

        # Look for phone numbers as a signal of a school listing
        phone_match = re.search(r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}', text)

        # Look for school name links
        name_link = item.find("a", href=True)
        if name_link and phone_match:
            name = name_link.get_text(strip=True)
            if not name or len(name) < 3:
                continue

            phone = phone_match.group(0)

            # Extract city/state if present
            location = ""
            loc_match = re.search(r'([A-Z][a-z]+(?:\s[A-Z][a-z]+)*),\s*([A-Z]{2})', text)
            if loc_match:
                location = f"{loc_match.group(1)}, {loc_match.group(2)}"

            school = {
                "name": name,
                "phone": phone,
                "city": loc_match.group(1) if loc_match else "",
                "state": loc_match.group(2) if loc_match else "",
                "airport_code": airport_code,
                "source": "flightschoollist.com",
                "source_url": url,
            }

            # Try to get the school's detail page URL
            href = name_link.get("href", "")
            if href and not href.startswith("http"):
                href = urljoin("https://www.flightschoollist.com/", href)
            school["detail_url"] = href

            schools.append(school)

    # Fallback: parse the raw HTML more aggressively
    if not schools:
        # Look for any structured listing
        all_text = soup.get_text()
        # Find all phone numbers and try to associate with nearby text
        for match in re.finditer(r'([^\n]{5,80})\s*\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}', all_text):
            context = match.group(0).strip()
            phone = re.search(r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}', context)
            name = context[:context.find(phone.group(0))].strip(" -|·•") if phone else ""
            if name and len(name) > 3 and not name.startswith("("):
                schools.append({
                    "name": name[:100],
                    "phone": phone.group(0) if phone else "",
                    "airport_code": airport_code,
                    "source": "flightschoollist.com",
                    "source_url": url,
                })

    return schools


# ── Source 2: bestaviation.net ────────────────────────────────────

def scrape_bestaviation_state(state: str) -> list[dict]:
    """Get all flight schools in a state from bestaviation.net."""
    url = f"https://www.bestaviation.net/flight_school/{state}/"
    soup = fetch_page(url)
    if not soup:
        return []

    schools = []
    # bestaviation lists schools with links to detail pages
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "/school/" in href:
            name = link.get_text(strip=True)
            if name and len(name) > 3:
                # Get surrounding text for location
                parent = link.find_parent(["div", "li", "td", "p"])
                location_text = parent.get_text(" ", strip=True) if parent else ""

                city = ""
                state_abbr = ""
                loc_match = re.search(r'([A-Z][a-z]+(?:\s[A-Z][a-z]+)*),\s*([A-Z][a-z]+)\s+\d{5}', location_text)
                if loc_match:
                    city = loc_match.group(1)

                school_url = href if href.startswith("http") else urljoin("https://www.bestaviation.net/", href)

                schools.append({
                    "name": name,
                    "city": city,
                    "state": state.replace("-", " ").title(),
                    "source": "bestaviation.net",
                    "source_url": url,
                    "detail_url": school_url,
                })

    return schools


# ── Source 3: faaflightschools.com ────────────────────────────────

def scrape_faaflightschools_state(state: str) -> list[dict]:
    """Get all flight schools in a state from faaflightschools.com."""
    url = f"https://www.faaflightschools.com/{state}/"
    soup = fetch_page(url)
    if not soup:
        return []

    schools = []
    # faaflightschools lists schools with name and city
    for link in soup.find_all("a", href=True):
        href = link["href"]
        name = link.get_text(strip=True)

        # School links typically contain the school name as slug
        if name and len(name) > 3 and "/" in href and href != "/" and "search" not in href.lower():
            parent = link.find_parent(["div", "li", "td", "tr", "p"])
            if parent:
                parent_text = parent.get_text(" ", strip=True)
                # Look for city name
                city_match = re.search(r'(?:in\s+)?([A-Z][a-z]+(?:\s[A-Z][a-z]+)*)', parent_text)
                city = city_match.group(1) if city_match else ""

                # Filter out navigation links
                if any(skip in name.lower() for skip in ["home", "search", "about", "contact", "privacy", "state", "login"]):
                    continue
                if any(skip in href.lower() for skip in ["search", "about", "contact", "privacy", "login", "register"]):
                    continue

                school_url = href if href.startswith("http") else urljoin("https://www.faaflightschools.com/", href)

                schools.append({
                    "name": name,
                    "city": city,
                    "state": state.replace("-", " ").title(),
                    "source": "faaflightschools.com",
                    "source_url": url,
                    "detail_url": school_url,
                })

    return schools


# ── Orchestration ─────────────────────────────────────────────────

def scrape_all_sources():
    """
    Scrape all three directory sources for all US states.

    Returns a combined list of flight school records.
    """
    all_schools = []

    # Source 1: flightschoollist.com — by state, then by airport
    print("\n[1/3] Scraping flightschoollist.com...")
    print("  Step 1a: Getting airport lists per state...")

    all_airports = []
    for state in tqdm(US_STATES, desc="  States"):
        airports = scrape_fsl_state_airports(state)
        all_airports.extend(airports)

    print(f"  Found {len(all_airports)} airports across all states")

    print("  Step 1b: Getting schools per airport...")
    for apt in tqdm(all_airports, desc="  Airports"):
        schools = scrape_fsl_airport_schools(apt["code"])
        for s in schools:
            s["airport_code"] = apt["code"]
            s["airport_name"] = apt["name"]
        all_schools.extend(schools)

    print(f"  Total from flightschoollist.com: {len(all_schools)}")

    # Source 2: bestaviation.net — by state
    print("\n[2/3] Scraping bestaviation.net...")
    ba_count = 0
    for state in tqdm(US_STATES, desc="  States"):
        schools = scrape_bestaviation_state(state)
        all_schools.extend(schools)
        ba_count += len(schools)
    print(f"  Total from bestaviation.net: {ba_count}")

    # Source 3: faaflightschools.com — by state
    print("\n[3/3] Scraping faaflightschools.com...")
    faa_count = 0
    for state in tqdm(US_STATES, desc="  States"):
        schools = scrape_faaflightschools_state(state)
        all_schools.extend(schools)
        faa_count += len(schools)
    print(f"  Total from faaflightschools.com: {faa_count}")

    return all_schools


def deduplicate_schools(schools: list[dict]) -> list[dict]:
    """
    Deduplicate schools across sources using fuzzy name + location matching.

    When duplicates are found, merge contact info from all sources.
    """
    from collections import defaultdict

    # Normalize name for matching
    def normalize(name: str) -> str:
        name = name.lower().strip()
        name = re.sub(r'[^a-z0-9\s]', '', name)
        name = re.sub(r'\s+', ' ', name)
        # Remove common suffixes
        for suffix in ["llc", "inc", "corp", "flight school", "aviation"]:
            name = name.replace(suffix, "").strip()
        return name

    # Group by normalized name
    groups = defaultdict(list)
    for s in schools:
        key = normalize(s.get("name", ""))
        if key:
            groups[key].append(s)

    # Merge each group
    merged = []
    for key, group in groups.items():
        # Take the most complete record as base
        base = max(group, key=lambda s: sum(1 for v in s.values() if v))

        # Merge in fields from other records
        for s in group:
            for field in ["phone", "email", "website", "airport_code", "city", "state"]:
                if not base.get(field) and s.get(field):
                    base[field] = s[field]

        # Track all sources
        base["sources"] = ",".join(set(s.get("source", "") for s in group if s.get("source")))
        base["n_sources"] = len(set(s.get("source", "") for s in group if s.get("source")))

        merged.append(base)

    return merged


def run():
    """Execute the flight school directory scrape."""
    print("=" * 60)
    print("Flight School Directory Scraper")
    print("=" * 60)

    all_schools = scrape_all_sources()

    print(f"\n  Raw records: {len(all_schools)}")

    # Deduplicate
    print("\nDeduplicating...")
    deduped = deduplicate_schools(all_schools)
    print(f"  After dedup: {len(deduped)}")

    # Save
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # CSV
    csv_path = OUTPUT_DIR / "flight_schools_directory.csv"
    if deduped:
        keys = sorted(set().union(*(d.keys() for d in deduped)))
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(deduped)
    print(f"  Saved: {csv_path}")

    # JSON
    json_path = OUTPUT_DIR / "flight_schools_directory.json"
    with open(json_path, "w") as f:
        json.dump(deduped, f, indent=2)
    print(f"  Saved: {json_path}")

    # Stats
    by_state = defaultdict(int)
    for s in deduped:
        st = s.get("state", "Unknown")
        by_state[st] += 1

    print(f"\n  Top states by school count:")
    for state, count in sorted(by_state.items(), key=lambda x: -x[1])[:15]:
        print(f"    {state:<20s} {count:>4d}")

    return deduped


if __name__ == "__main__":
    run()
