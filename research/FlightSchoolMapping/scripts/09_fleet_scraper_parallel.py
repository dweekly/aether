#!/usr/bin/env python3
"""
Phase 9 (Parallel): Flight Line Scraper — massively concurrent edition.

Designed to run on a local machine with lots of RAM and fast internet.
Uses asyncio + aiohttp to scrape all ~2,100 schools concurrently.

Since every school runs its own website on separate infrastructure,
high concurrency doesn't burden any single server.

Usage:
    python 09_fleet_scraper_parallel.py                  # all schools
    python 09_fleet_scraper_parallel.py --limit 500      # first 500
    python 09_fleet_scraper_parallel.py --concurrency 500 # max 500 in-flight

Input:  output/flight_schools_enriched.json
Output: output/flight_line.json
"""

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup, Tag
from tqdm.asyncio import tqdm as atqdm

OUTPUT_DIR = Path(__file__).parent.parent / "output"

# ── Tuning knobs ─────────────────────────────────────────────────
# Each school is on its own server, so we can be very parallel.
# The semaphore limits total in-flight requests across all schools.
DEFAULT_CONCURRENCY = 200       # simultaneous school-level tasks
PER_SCHOOL_DELAY = 0.3          # polite pause between requests to the SAME domain
REQUEST_TIMEOUT = 20            # per-request timeout
MAX_RETRIES = 2
CONNECTOR_LIMIT = 500           # aiohttp TCP connector pool size

USER_AGENT = "AetherResearch/1.0 (aviation research; contact: research@aether.aero)"

# ── Regex patterns (identical to serial version) ─────────────────

N_NUMBER_RE = re.compile(r"\bN(\d{1,5}[A-Z]{0,2})\b")

RATE_RE = re.compile(
    r"\$\s?([\d,]+(?:\.\d{2})?)\s*(?:/\s*h(?:ou)?r\.?|per\s+h(?:ou)?r\.?)",
    re.IGNORECASE,
)

AIRPORT_ICAO_RE = re.compile(r"\b(K[A-Z0-9]{3})\b")
AIRPORT_FAA_RE = re.compile(
    r"(?:airport|field|located at|based at|fly(?:ing)?\s+out\s+of)\s+"
    r"(?:\w+\s+){0,4}?\(?([A-Z]{3,4})\)?",
    re.IGNORECASE,
)

FLEET_LINK_PATTERNS = re.compile(
    r"\b(fleet|aircraft|our\s+planes?|rental(?:s)?|rates?\s*(?:&|and)?\s*fleet"
    r"|airplane(?:s)?|flight\s+line|rent\s+(?:a|an)\s+(?:plane|aircraft))\b",
    re.IGNORECASE,
)

LOCATION_LINK_PATTERNS = re.compile(
    r"\b(locations?|campus(?:es)?|bases?|airports?)\b",
    re.IGNORECASE,
)

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
    r"|(?:172|152|150|182|206|210|310|340|402)\b"
    r"|(?:PA-28|PA-32|PA-34|PA-38|PA-44|PA-46)\b"
    r")\b",
    re.IGNORECASE,
)

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


# ── Async HTTP helpers ───────────────────────────────────────────

async def fetch_page(session: aiohttp.ClientSession, url: str) -> BeautifulSoup | None:
    """Fetch and parse a page with retries."""
    for attempt in range(MAX_RETRIES):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                                   allow_redirects=True) as resp:
                if resp.status == 200:
                    html = await resp.text(errors="replace")
                    return BeautifulSoup(html, "html.parser")
                if resp.status == 429:
                    await asyncio.sleep(5 * (attempt + 1))
        except (aiohttp.ClientError, asyncio.TimeoutError):
            await asyncio.sleep(2 * (attempt + 1))
    return None


# ── Page discovery (same logic, async fetch) ─────────────────────

def _same_domain(base: str, candidate: str) -> bool:
    base_domain = urlparse(base).netloc.lower().lstrip("www.")
    cand_domain = urlparse(candidate).netloc.lower().lstrip("www.")
    return cand_domain == base_domain or cand_domain.endswith("." + base_domain)


def find_fleet_pages(base_url: str, home_soup: BeautifulSoup) -> list[str]:
    candidates: list[str] = []
    for a in home_soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        if FLEET_LINK_PATTERNS.search(text):
            full = urljoin(base_url, href)
            if _same_domain(base_url, full):
                candidates.append(full)
        path = urlparse(urljoin(base_url, href)).path.lower()
        if any(seg in path for seg in ["/fleet", "/aircraft", "/rental", "/planes", "/rates"]):
            full = urljoin(base_url, href)
            if _same_domain(base_url, full):
                candidates.append(full)
    seen = set()
    unique = []
    for u in candidates:
        normalized = u.rstrip("/")
        if normalized not in seen:
            seen.add(normalized)
            unique.append(u)
    return unique[:8]


def find_location_pages(base_url: str, home_soup: BeautifulSoup) -> list[str]:
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


# ── Extraction (identical to serial version) ─────────────────────

def extract_airports(text: str) -> list[str]:
    icao = AIRPORT_ICAO_RE.findall(text)
    faa = AIRPORT_FAA_RE.findall(text)
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


def _find_aircraft_containers(soup: BeautifulSoup) -> list[Tag]:
    for tag_type in ["div", "section", "article", "li", "tr"]:
        candidates = soup.find_all(tag_type)
        n_num_containers = []
        for c in candidates:
            text = c.get_text(strip=True)
            n_nums = N_NUMBER_RE.findall(text)
            if len(n_nums) == 1 and len(text) < 2000:
                n_num_containers.append(c)
        if len(n_num_containers) >= 2:
            return n_num_containers
    return []


def _parse_aircraft_container(container: Tag) -> dict | None:
    text = container.get_text(separator=" ", strip=True)
    n_nums = N_NUMBER_RE.findall(text)
    if not n_nums:
        return None

    n_number = "N" + n_nums[0]
    display_name = ""
    desc_match = AIRCRAFT_DESCRIPTOR_RE.search(text)
    if desc_match:
        display_name = desc_match.group(0).strip()

    wet_rate = None
    rate_match = RATE_RE.search(text)
    if rate_match:
        rate_str = rate_match.group(1).replace(",", "")
        try:
            wet_rate = float(rate_str)
        except ValueError:
            pass

    equip_matches = EQUIPMENT_RE.findall(text)
    description = ", ".join(dict.fromkeys(equip_matches))
    if display_name and description:
        description = f"{display_name} — {description}"
    elif display_name:
        description = display_name

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


def extract_fleet_from_page(soup: BeautifulSoup, page_url: str) -> list[dict]:
    aircraft: list[dict] = []
    text_full = soup.get_text(separator=" ", strip=True)

    containers = _find_aircraft_containers(soup)
    if containers:
        for container in containers:
            entry = _parse_aircraft_container(container)
            if entry:
                aircraft.append(entry)

    if not aircraft:
        aircraft = _extract_from_flat_text(text_full)

    seen = set()
    unique = []
    for a in aircraft:
        nn = a.get("n_number", "")
        if nn and nn not in seen:
            seen.add(nn)
            unique.append(a)
    return unique


# ── Async location extraction ────────────────────────────────────

async def extract_locations(
    session: aiohttp.ClientSession,
    base_url: str,
    home_soup: BeautifulSoup,
    known_airport: str,
) -> list[dict]:
    locations: list[dict] = []
    all_codes: list[str] = []

    home_text = home_soup.get_text(separator=" ", strip=True)
    all_codes.extend(extract_airports(home_text))

    # Fetch location pages concurrently (within same school domain, small fan-out)
    loc_urls = find_location_pages(base_url, home_soup)
    if loc_urls:
        tasks = [fetch_page(session, url) for url in loc_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, BeautifulSoup):
                all_codes.extend(extract_airports(r.get_text(separator=" ", strip=True)))

    if known_airport:
        code = known_airport.upper()
        if len(code) == 3:
            code = "K" + code
        all_codes.append(code)

    seen = set()
    for code in all_codes:
        if code not in seen:
            seen.add(code)
            locations.append({"airport_id": code, "airport_name": "", "label": ""})
    return locations


# ── Per-school async scraper ─────────────────────────────────────

async def scrape_school_fleet(
    session: aiohttp.ClientSession,
    school: dict,
    school_semaphore: asyncio.Semaphore,
) -> dict | None:
    """Scrape one school, respecting the global concurrency semaphore."""
    async with school_semaphore:
        return await _scrape_school_fleet_inner(session, school)


async def _scrape_school_fleet_inner(
    session: aiohttp.ClientSession,
    school: dict,
) -> dict | None:
    website = (school.get("website") or "").strip()
    if not website:
        return None
    if not website.startswith("http"):
        website = "https://" + website

    # Fetch home page
    home_soup = await fetch_page(session, website)
    if not home_soup:
        return None

    # Find fleet pages
    fleet_urls = find_fleet_pages(website, home_soup)

    all_aircraft: list[dict] = []
    fleet_page_url = ""

    # Check home page
    home_fleet = extract_fleet_from_page(home_soup, website)
    if home_fleet:
        all_aircraft.extend(home_fleet)
        fleet_page_url = website

    # Fetch all fleet sub-pages concurrently (same domain, small fan-out)
    if fleet_urls:
        tasks = [fetch_page(session, url) for url in fleet_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for url, r in zip(fleet_urls, results):
            if isinstance(r, BeautifulSoup):
                page_fleet = extract_fleet_from_page(r, url)
                if page_fleet:
                    all_aircraft.extend(page_fleet)
                    if not fleet_page_url or len(page_fleet) > len(home_fleet):
                        fleet_page_url = url

    # Merge by N-number
    merged: dict[str, dict] = {}
    for a in all_aircraft:
        nn = a.get("n_number", "")
        if not nn:
            continue
        if nn not in merged:
            merged[nn] = a
        else:
            existing = merged[nn]
            for key in ["display_name", "description", "wet_rate_hr", "equipment", "airport_hint"]:
                if not existing.get(key) and a.get(key):
                    existing[key] = a[key]
    unique_aircraft = list(merged.values())

    if not unique_aircraft:
        return None

    # Locations
    known_airport = school.get("airport_code", "")
    locations = await extract_locations(session, website, home_soup, known_airport)

    location_ids = [loc["airport_id"] for loc in locations]
    for ac in unique_aircraft:
        hint = ac.pop("airport_hint", None)
        if hint and hint in location_ids:
            ac["location_index"] = location_ids.index(hint)

    return {
        "school_name": school.get("name", ""),
        "school_website": website,
        "locations": locations,
        "fleet": unique_aircraft,
        "fleet_page_url": fleet_page_url,
        "scrape_ts": datetime.now(timezone.utc).isoformat(),
    }


# ── Main ─────────────────────────────────────────────────────────

async def run_async(limit: int | None = None, concurrency: int = DEFAULT_CONCURRENCY):
    print("=" * 60)
    print("Phase 9 (Parallel): Flight Line Scraper")
    print(f"  Concurrency: {concurrency} schools at once")
    print("=" * 60)

    # Load schools
    enriched_path = OUTPUT_DIR / "flight_schools_enriched.json"
    if not enriched_path.exists():
        print("  Error: Run Phase 8 (directory scraper) first.")
        return None
    with open(enriched_path) as f:
        schools = json.load(f)

    with_websites = [s for s in schools if s.get("website")]
    print(f"  Total schools: {len(schools):,}")
    print(f"  With websites: {len(with_websites):,}")

    if limit:
        with_websites = with_websites[:limit]
        print(f"  Limited to: {limit}")

    school_semaphore = asyncio.Semaphore(concurrency)

    connector = aiohttp.TCPConnector(limit=CONNECTOR_LIMIT, ttl_dns_cache=600)
    timeout = aiohttp.ClientTimeout(total=60)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    t0 = time.time()

    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
        tasks = [scrape_school_fleet(session, school, school_semaphore) for school in with_websites]

        flight_line: list[dict] = []
        schools_with_fleet = 0
        total_aircraft = 0
        errors = 0

        results = await atqdm.gather(*tasks, desc="  Scraping fleet pages")

        for r in results:
            if isinstance(r, Exception):
                errors += 1
            elif r is not None:
                flight_line.append(r)
                schools_with_fleet += 1
                total_aircraft += len(r["fleet"])

    elapsed = time.time() - t0

    # Save results
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "flight_line.json"
    with open(out_path, "w") as f:
        json.dump(flight_line, f, indent=2)

    print(f"\n  === Results ===")
    print(f"  Schools scraped:      {len(with_websites):,}")
    print(f"  Schools with fleet:   {schools_with_fleet:,}")
    print(f"  Total aircraft found: {total_aircraft:,}")
    print(f"  Errors:               {errors:,}")
    print(f"  Wall-clock time:      {elapsed:.1f}s")
    print(f"  Output: {out_path}")

    if flight_line:
        multi_loc = sum(1 for r in flight_line if len(r["locations"]) > 1)
        with_rates = sum(1 for r in flight_line for a in r["fleet"] if a.get("wet_rate_hr"))
        print(f"  Multi-airport schools: {multi_loc}")
        print(f"  Aircraft with rates:   {with_rates}")

    print(f"\nPhase 9 (Parallel) complete.")
    return flight_line


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Parallel fleet scraper — run on beefy local machine")
    parser.add_argument("--limit", type=int, help="Max schools to scrape (for testing)")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                        help=f"Max schools in-flight at once (default: {DEFAULT_CONCURRENCY})")
    args = parser.parse_args()
    asyncio.run(run_async(limit=args.limit, concurrency=args.concurrency))


if __name__ == "__main__":
    main()
