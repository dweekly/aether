#!/usr/bin/env python3
"""
Phase 9: Flight Line Scraper — Scrape flight school websites for fleet pages.

The goal is to find authoritative "flight line" data: tail numbers (N-numbers)
published on flight school websites, which map a specific aircraft to a specific
school and airport. This is the gold-standard evidence that a school operates
a given aircraft.

Key design decisions:
  - Schools can operate from MULTIPLE airports (e.g. San Carlos Flight Center
    at KSQL + KPAO, West Valley Flying Club at KPAO + KRHV).
  - Each aircraft on the flight line is tied to a specific location when possible.
  - We capture optional details: aircraft description (e.g. "Cirrus SR22T w/FIKI"),
    hourly wet rental rate, and equipment/avionics notes.

Input:  output/flight_schools_enriched.json (schools with websites)
Output: output/flight_line.json — authoritative aircraft-to-school mappings

Schema (flight_line.json):
[
  {
    "school_name": "West Valley Flying Club",
    "school_website": "https://wvfc.org",
    "locations": [
      {
        "airport_id": "KPAO",
        "airport_name": "Palo Alto Airport",
        "label": "Palo Alto base"
      },
      {
        "airport_id": "KRHV",
        "airport_name": "Reid-Hillview Airport",
        "label": "San Jose base"
      }
    ],
    "fleet": [
      {
        "n_number": "N12345",
        "display_name": "Cessna 172S Skyhawk",
        "description": "G1000, 180hp",
        "location_index": 0,
        "wet_rate_hr": 189.0,
        "category": "single_engine_land"
      },
      ...
    ],
    "fleet_page_url": "https://wvfc.org/aircraft/",
    "scrape_ts": "2026-03-05T12:00:00Z"
  }
]
"""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from tqdm import tqdm

OUTPUT_DIR = Path(__file__).parent.parent / "output"

# Respectful scraping
REQUEST_DELAY_SEC = 1.5
USER_AGENT = "AetherResearch/1.0 (aviation research; contact: research@aether.aero)"
REQUEST_TIMEOUT = 15
MAX_RETRIES = 2

# ── Regex patterns ───────────────────────────────────────────────

# Tail number: N followed by 1-5 digits and 0-2 letters
N_NUMBER_RE = re.compile(r"\bN(\d{1,5}[A-Z]{0,2})\b")

# Rental rate: dollar amounts near keywords like "/hr", "per hour", "wet"
RATE_RE = re.compile(
    r"\$\s?([\d,]+(?:\.\d{2})?)\s*(?:/\s*h(?:ou)?r|per\s+h(?:ou)?r)",
    re.IGNORECASE,
)

# Airport identifier patterns: ICAO (KXXX) or FAA (XXX) in context
AIRPORT_ICAO_RE = re.compile(r"\b(K[A-Z0-9]{3})\b")
AIRPORT_FAA_RE = re.compile(
    r"(?:airport|field|located at|based at|fly(?:ing)?\s+out\s+of)\s+"
    r"(?:\w+\s+){0,4}?\(?([A-Z]{3,4})\)?",
    re.IGNORECASE,
)

# Fleet/aircraft page link text patterns
FLEET_LINK_PATTERNS = re.compile(
    r"\b(fleet|aircraft|our\s+planes?|rental(?:s)?|rates?\s*(?:&|and)?\s*fleet"
    r"|airplane(?:s)?|flight\s+line|rent\s+(?:a|an)\s+(?:plane|aircraft))\b",
    re.IGNORECASE,
)

# Multi-location / satellite hints
LOCATION_LINK_PATTERNS = re.compile(
    r"\b(locations?|campus(?:es)?|bases?|airports?)\b",
    re.IGNORECASE,
)

# Aircraft make/model patterns (common GA trainers)
AIRCRAFT_DESCRIPTOR_RE = re.compile(
    r"\b(Cessna\s+\d{3}[A-Z]?"
    r"|Piper\s+(?:(?:PA-?\d{2}[A-Z]?)|(?:Cherokee|Warrior|Archer|Arrow|Seminole|Seneca|Navajo|Comanche|Lance))"
    r"|Cirrus\s+SR-?\d{2}[A-Z]?"
    r"|Diamond\s+DA-?\d{2}[A-Z]?"
    r"|Beech(?:craft)?\s+(?:Bonanza|Baron|King\s*Air|Duchess|Musketeer|Sundowner|Sierra)"
    r"|Mooney\s+M\d{2}[A-Z]?"
    r"|Robinson\s+R-?\d{2}"
    r"|Tecnam\s+P\d{4}"
    r"|Grumman\s+(?:Tiger|Cheetah|Traveler|AA-?\d[A-Z]?)"
    r"|Socata\s+TB-?\d+"
    r"|(?:172|152|150|182|206|210|310|340|402)\b"  # bare Cessna model numbers
    r"|(?:PA-28|PA-32|PA-34|PA-38|PA-44|PA-46)\b"  # bare Piper designations
    r")\b",
    re.IGNORECASE,
)

# Equipment / avionics keywords
EQUIPMENT_KEYWORDS = [
    "G1000", "G500", "G600", "G3X", "GFC 700", "GFC700",
    "Avidyne", "Aspen", "Garmin", "GTN", "GNS",
    "FIKI", "TKS", "de-ice", "deice",
    "autopilot", "A/P", "KAP",
    "turbo", "turbocharged", "turbonormalized",
    "constant.speed", "CS prop",
    "retractable", "retract", "complex",
    "TAA", "glass cockpit", "steam gauge",
    "IFR certified", "IFR equipped",
    "180hp", "160hp", "200hp", "235hp", "310hp",
]
EQUIPMENT_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in EQUIPMENT_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def get_session() -> requests.Session:
    """Create a requests session with respectful headers."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    })
    return session


def fetch_page(session: requests.Session, url: str) -> BeautifulSoup | None:
    """Fetch and parse a page with retries."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if resp.status_code == 200:
                return BeautifulSoup(resp.text, "html.parser")
            if resp.status_code == 429:
                time.sleep(5 * (attempt + 1))
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
    return None


# ── Page discovery ───────────────────────────────────────────────

def find_fleet_pages(session: requests.Session, base_url: str, home_soup: BeautifulSoup) -> list[str]:
    """Find URLs that likely contain fleet / aircraft listings."""
    candidates: list[str] = []

    for a in home_soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        # Match link text
        if FLEET_LINK_PATTERNS.search(text):
            full = urljoin(base_url, href)
            if _same_domain(base_url, full):
                candidates.append(full)
        # Match href path segments
        path = urlparse(urljoin(base_url, href)).path.lower()
        if any(seg in path for seg in ["/fleet", "/aircraft", "/rental", "/planes", "/rates"]):
            full = urljoin(base_url, href)
            if _same_domain(base_url, full):
                candidates.append(full)

    # Deduplicate, preserving order
    seen = set()
    unique = []
    for u in candidates:
        normalized = u.rstrip("/")
        if normalized not in seen:
            seen.add(normalized)
            unique.append(u)
    return unique[:8]  # Cap at 8 fleet pages to be polite


def find_location_pages(session: requests.Session, base_url: str, home_soup: BeautifulSoup) -> list[str]:
    """Find URLs that list multiple locations / airports."""
    candidates: list[str] = []
    for a in home_soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        if LOCATION_LINK_PATTERNS.search(text):
            full = urljoin(base_url, href)
            if _same_domain(base_url, full):
                candidates.append(full)
        path = urlparse(urljoin(base_url, href)).path.lower()
        if any(seg in path for seg in ["/location", "/campus", "/airport"]):
            full = urljoin(base_url, href)
            if _same_domain(base_url, full):
                candidates.append(full)
    seen = set()
    unique = []
    for u in candidates:
        n = u.rstrip("/")
        if n not in seen:
            seen.add(n)
            unique.append(u)
    return unique[:4]


def _same_domain(base: str, candidate: str) -> bool:
    """Check that candidate URL is on the same domain as base."""
    base_domain = urlparse(base).netloc.lower().lstrip("www.")
    cand_domain = urlparse(candidate).netloc.lower().lstrip("www.")
    return cand_domain == base_domain or cand_domain.endswith("." + base_domain)


# ── Extraction ───────────────────────────────────────────────────

def extract_airports(text: str) -> list[str]:
    """Extract airport identifiers from page text."""
    icao = AIRPORT_ICAO_RE.findall(text)
    faa = AIRPORT_FAA_RE.findall(text)
    # Deduplicate; normalize KXXX format
    codes = set()
    for c in icao:
        codes.add(c.upper())
    for c in faa:
        c = c.upper()
        if len(c) == 3:
            codes.add("K" + c)
        else:
            codes.add(c)
    return sorted(codes)


def extract_fleet_from_page(soup: BeautifulSoup, page_url: str) -> list[dict]:
    """
    Extract aircraft entries from a fleet page.

    Strategy: look for recurring structural patterns (cards, list items, table rows)
    that each contain an N-number. Extract the surrounding context for details.
    """
    aircraft: list[dict] = []
    text_full = soup.get_text(separator=" ", strip=True)

    # Strategy 1: Find structured containers (divs/cards/rows) with N-numbers
    containers = _find_aircraft_containers(soup)
    if containers:
        for container in containers:
            entry = _parse_aircraft_container(container)
            if entry:
                aircraft.append(entry)

    # Strategy 2: If no structured containers, scan full text for N-numbers
    # and grab surrounding context
    if not aircraft:
        aircraft = _extract_from_flat_text(text_full)

    # Deduplicate by N-number
    seen = set()
    unique = []
    for a in aircraft:
        nn = a.get("n_number", "")
        if nn and nn not in seen:
            seen.add(nn)
            unique.append(a)

    return unique


def _find_aircraft_containers(soup: BeautifulSoup) -> list[Tag]:
    """Find HTML elements that each represent one aircraft."""
    containers = []

    # Look for common patterns: cards, list items, table rows, sections
    # that contain N-numbers
    for tag_type in ["div", "section", "article", "li", "tr"]:
        candidates = soup.find_all(tag_type)
        n_num_containers = []
        for c in candidates:
            text = c.get_text(strip=True)
            n_nums = N_NUMBER_RE.findall(text)
            if len(n_nums) == 1 and len(text) < 2000:
                n_num_containers.append(c)

        # If we found multiple containers of the same type each with exactly
        # one N-number, that's likely the fleet listing structure
        if len(n_num_containers) >= 2:
            containers = n_num_containers
            break

    return containers


def _parse_aircraft_container(container: Tag) -> dict | None:
    """Parse a single aircraft container element."""
    text = container.get_text(separator=" ", strip=True)
    n_nums = N_NUMBER_RE.findall(text)
    if not n_nums:
        return None

    n_number = "N" + n_nums[0]

    # Aircraft type / description
    display_name = ""
    desc_match = AIRCRAFT_DESCRIPTOR_RE.search(text)
    if desc_match:
        display_name = desc_match.group(0).strip()

    # Rental rate
    wet_rate = None
    rate_match = RATE_RE.search(text)
    if rate_match:
        rate_str = rate_match.group(1).replace(",", "")
        try:
            wet_rate = float(rate_str)
        except ValueError:
            pass

    # Equipment / avionics
    equip_matches = EQUIPMENT_RE.findall(text)
    description = ", ".join(dict.fromkeys(equip_matches))  # dedupe, preserve order

    # If we found a display name, prepend it to description
    if display_name and description:
        description = f"{display_name} — {description}"
    elif display_name:
        description = display_name

    # Airport mentioned in this container
    airports = extract_airports(text)

    entry = {
        "n_number": n_number,
        "display_name": display_name,
        "description": description,
    }
    if wet_rate is not None:
        entry["wet_rate_hr"] = wet_rate
    if airports:
        entry["airport_hint"] = airports[0]
    if equip_matches:
        entry["equipment"] = list(dict.fromkeys(equip_matches))

    return entry


def _extract_from_flat_text(text: str) -> list[dict]:
    """Extract aircraft from unstructured text by finding N-numbers with context."""
    aircraft = []
    for match in N_NUMBER_RE.finditer(text):
        start = max(0, match.start() - 200)
        end = min(len(text), match.end() + 200)
        context = text[start:end]

        n_number = "N" + match.group(1)
        display_name = ""
        desc_match = AIRCRAFT_DESCRIPTOR_RE.search(context)
        if desc_match:
            display_name = desc_match.group(0).strip()

        wet_rate = None
        rate_match = RATE_RE.search(context)
        if rate_match:
            rate_str = rate_match.group(1).replace(",", "")
            try:
                wet_rate = float(rate_str)
            except ValueError:
                pass

        equip_matches = EQUIPMENT_RE.findall(context)

        entry = {
            "n_number": n_number,
            "display_name": display_name,
            "description": display_name,
        }
        if wet_rate is not None:
            entry["wet_rate_hr"] = wet_rate
        if equip_matches:
            entry["equipment"] = list(dict.fromkeys(equip_matches))

        aircraft.append(entry)

    return aircraft


def extract_locations(
    session: requests.Session,
    base_url: str,
    home_soup: BeautifulSoup,
    known_airport: str,
) -> list[dict]:
    """
    Determine all airport locations a school operates from.

    Checks: home page text, dedicated locations page, and fleet page context.
    """
    locations: list[dict] = []
    all_codes: list[str] = []

    # Extract from home page
    home_text = home_soup.get_text(separator=" ", strip=True)
    all_codes.extend(extract_airports(home_text))

    # Check dedicated location pages
    loc_pages = find_location_pages(session, base_url, home_soup)
    for loc_url in loc_pages:
        time.sleep(REQUEST_DELAY_SEC)
        loc_soup = fetch_page(session, loc_url)
        if loc_soup:
            loc_text = loc_soup.get_text(separator=" ", strip=True)
            all_codes.extend(extract_airports(loc_text))

    # Add the known airport from directory data
    if known_airport:
        code = known_airport.upper()
        if len(code) == 3:
            code = "K" + code
        all_codes.append(code)

    # Deduplicate
    seen = set()
    for code in all_codes:
        if code not in seen:
            seen.add(code)
            locations.append({
                "airport_id": code,
                "airport_name": "",  # will be enriched later from airport ref
                "label": "",
            })

    return locations


# ── Main scraping orchestration ──────────────────────────────────

def scrape_school_fleet(
    session: requests.Session,
    school: dict,
) -> dict | None:
    """
    Scrape a single flight school's website for flight line data.

    Returns a flight_line record or None if no fleet data found.
    """
    website = (school.get("website") or "").strip()
    if not website:
        return None
    if not website.startswith("http"):
        website = "https://" + website

    # Fetch home page
    home_soup = fetch_page(session, website)
    if not home_soup:
        return None

    # Find fleet pages
    fleet_urls = find_fleet_pages(session, website, home_soup)

    # Also scan home page for N-numbers (some small schools list fleet on home page)
    all_aircraft: list[dict] = []
    fleet_page_url = ""

    # Check home page first
    home_fleet = extract_fleet_from_page(home_soup, website)
    if home_fleet:
        all_aircraft.extend(home_fleet)
        fleet_page_url = website

    # Check fleet sub-pages
    for fleet_url in fleet_urls:
        time.sleep(REQUEST_DELAY_SEC)
        fleet_soup = fetch_page(session, fleet_url)
        if fleet_soup:
            page_fleet = extract_fleet_from_page(fleet_soup, fleet_url)
            if page_fleet:
                all_aircraft.extend(page_fleet)
                if not fleet_page_url or len(page_fleet) > len(home_fleet):
                    fleet_page_url = fleet_url

    # Deduplicate aircraft by N-number
    seen_n = set()
    unique_aircraft = []
    for a in all_aircraft:
        nn = a.get("n_number", "")
        if nn and nn not in seen_n:
            seen_n.add(nn)
            unique_aircraft.append(a)

    # Only return a record if we found at least one tail number
    if not unique_aircraft:
        return None

    # Extract locations
    known_airport = school.get("airport_code", "")
    locations = extract_locations(session, website, home_soup, known_airport)

    # If fleet entries have airport hints, try to match to locations
    location_ids = [loc["airport_id"] for loc in locations]
    for ac in unique_aircraft:
        hint = ac.pop("airport_hint", None)
        if hint and hint in location_ids:
            ac["location_index"] = location_ids.index(hint)

    record = {
        "school_name": school.get("name", ""),
        "school_website": website,
        "locations": locations,
        "fleet": unique_aircraft,
        "fleet_page_url": fleet_page_url,
        "scrape_ts": datetime.now(timezone.utc).isoformat(),
    }

    return record


def run(schools: list[dict] | None = None, limit: int | None = None):
    """
    Execute Phase 9: Fleet / flight line scraping.

    Args:
        schools: List of school dicts (from enriched JSON). If None, loads from file.
        limit: Max number of schools to scrape (for testing). None = all.
    """
    print("=" * 60)
    print("Phase 9: Flight Line Scraper")
    print("=" * 60)

    # Load schools
    if schools is None:
        enriched_path = OUTPUT_DIR / "flight_schools_enriched.json"
        if not enriched_path.exists():
            print("  Error: Run Phase 8 (directory scraper) first.")
            return None
        with open(enriched_path) as f:
            schools = json.load(f)

    # Filter to schools with websites
    with_websites = [s for s in schools if s.get("website")]
    print(f"  Total schools: {len(schools):,}")
    print(f"  With websites: {len(with_websites):,}")

    if limit:
        with_websites = with_websites[:limit]
        print(f"  Limited to: {limit}")

    session = get_session()
    flight_line: list[dict] = []
    schools_with_fleet = 0
    total_aircraft = 0

    for school in tqdm(with_websites, desc="  Scraping fleet pages"):
        try:
            record = scrape_school_fleet(session, school)
            if record:
                flight_line.append(record)
                schools_with_fleet += 1
                total_aircraft += len(record["fleet"])
        except Exception as e:
            name = school.get("name", "?")
            tqdm.write(f"  Warning: {name}: {e}")

        time.sleep(REQUEST_DELAY_SEC)

    # Save results
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "flight_line.json"
    with open(out_path, "w") as f:
        json.dump(flight_line, f, indent=2)

    print(f"\n  === Results ===")
    print(f"  Schools scraped:      {len(with_websites):,}")
    print(f"  Schools with fleet:   {schools_with_fleet:,}")
    print(f"  Total aircraft found: {total_aircraft:,}")
    print(f"  Output: {out_path}")

    # Quick stats
    if flight_line:
        multi_loc = sum(1 for r in flight_line if len(r["locations"]) > 1)
        with_rates = sum(
            1 for r in flight_line
            for a in r["fleet"]
            if a.get("wet_rate_hr")
        )
        print(f"  Multi-airport schools: {multi_loc}")
        print(f"  Aircraft with rates:   {with_rates}")

    print(f"\nPhase 9 complete.")
    return flight_line


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scrape flight school fleet pages")
    parser.add_argument("--limit", type=int, help="Max schools to scrape (for testing)")
    args = parser.parse_args()
    run(limit=args.limit)
