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

Input:  output/flight_schools_enriched.json (+ output/aopa_flight_schools.json)
Output: output/flight_line.json
"""

import asyncio
import json
import re
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning

# Some school URLs serve XML (sitemaps, RSS feeds) — we intentionally
# parse everything as HTML, so silence this warning.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
from tqdm.asyncio import tqdm as atqdm

OUTPUT_DIR = Path(__file__).parent.parent / "output"

# ── Tuning knobs ─────────────────────────────────────────────────
# Each school is on its own server, so we can be very parallel.
# The semaphore limits total in-flight requests across all schools.
DEFAULT_CONCURRENCY = 200       # simultaneous school-level tasks
PER_SCHOOL_DELAY = 0.3          # polite pause between requests to the SAME domain
REQUEST_TIMEOUT = 20            # per-request timeout
PER_SCHOOL_TIMEOUT = 90         # hard cap on total time for any single school
MAX_RETRIES = 2
CONNECTOR_LIMIT = 500           # aiohttp TCP connector pool size

USER_AGENT = "AetherResearch/1.0 (aviation research; contact: research@aether.aero)"

# ── Regex patterns (identical to serial version) ─────────────────

N_NUMBER_RE = re.compile(r"\bN(\d{1,5}[A-Z]{0,2})\b", re.ASCII)

# Minimum total length for a valid N-number suffix (digits + letters).
# Real N-numbers are N + 1-5 digits + 0-2 letters, but single-digit
# suffixes (N1, N6, …) are almost always false positives from page text.
_MIN_N_SUFFIX_LEN = 2

RATE_RE = re.compile(
    r"\$\s?([\d,]+(?:\.\d{2})?)\s*(?:/\s*h(?:ou)?r\.?|per\s+h(?:ou)?r\.?)",
    re.IGNORECASE,
)

AIRPORT_ICAO_RE = re.compile(r"\b(K[A-Z]{3})\b")
AIRPORT_FAA_RE = re.compile(
    r"(?:airport|field|located at|based at|fly(?:ing)?\s+out\s+of)\s+"
    r"(?:\w+\s+){0,4}?\(?([A-Z]{3,4})\)?",
    re.IGNORECASE,
)

# ── Valid airport code set (loaded once) ──────────────────────────
_VALID_AIRPORT_IDS: set[str] | None = None


def _load_valid_airports() -> set[str]:
    """Load the set of valid US airport identifiers from airports.csv."""
    global _VALID_AIRPORT_IDS
    if _VALID_AIRPORT_IDS is not None:
        return _VALID_AIRPORT_IDS
    import csv
    airports_path = OUTPUT_DIR / "airports.csv"
    ids: set[str] = set()
    if airports_path.exists():
        with open(airports_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                aid = (row.get("airport_id") or "").strip().upper()
                if aid:
                    ids.add(aid)
                    # Also add with K-prefix for 3-letter FAA codes
                    if len(aid) == 3:
                        ids.add("K" + aid)
                faa = (row.get("faa_id") or "").strip().upper()
                if faa:
                    ids.add(faa)
                    if len(faa) == 3:
                        ids.add("K" + faa)
                gps = (row.get("gps_code") or "").strip().upper()
                if gps:
                    ids.add(gps)
    _VALID_AIRPORT_IDS = ids
    return ids

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


# ── School list helpers ───────────────────────────────────────────

def _slugify_name(name: str) -> str:
    """Turn a business name into a plausible domain slug.

    'JATO Aviation' → 'jatoaviation'
    'San Carlos Flight Center Inc' → 'sancarlosflightcenter'
    """
    name = name.lower()
    # Drop common suffixes that rarely appear in domains
    for suffix in [" inc", " llc", " ltd", " corp", " co"]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return re.sub(r"[^a-z0-9]", "", name)


def _extract_aopa_business_id(aopa_url: str) -> str | None:
    """Extract numeric business ID from an AOPA detail URL."""
    m = re.search(r"/business/(\d+)", aopa_url)
    return m.group(1) if m else None


# The AOPA Airports webapp exposes a public JSON API for business details.
# The bearer token is a static public key embedded in their Angular app.
AOPA_API_BASE = "https://webapp.aopa.org/AirportsAPI/businesses"
AOPA_API_TOKEN = "AOPA_PUBLIC_TOKEN_REDACTED"


async def _resolve_aopa_websites(
    session: aiohttp.ClientSession,
    records: list[dict],
    semaphore: asyncio.Semaphore,
) -> dict[str, str]:
    """Batch-resolve website URLs for AOPA schools via their detail API.

    Returns a mapping of AOPA business ID → website URL.
    """
    # Collect all unique business IDs that need resolution
    id_to_rec: dict[str, dict] = {}
    for rec in records:
        for aopa_url in rec.get("_aopa_urls", []):
            bid = _extract_aopa_business_id(aopa_url)
            if bid and bid not in id_to_rec:
                id_to_rec[bid] = rec

    if not id_to_rec:
        return {}

    results: dict[str, str] = {}
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {AOPA_API_TOKEN}",
        "Origin": "https://www.aopa.org",
        "Referer": "https://www.aopa.org/",
    }

    async def fetch_one(bid: str) -> None:
        async with semaphore:
            url = f"{AOPA_API_BASE}/{bid}"
            try:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        web = (data.get("webAddress") or "").strip()
                        if web:
                            if not web.startswith("http"):
                                web = "https://" + web
                            results[bid] = web
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass

    tasks = [fetch_one(bid) for bid in id_to_rec]
    await asyncio.gather(*tasks)
    return results


def merge_aopa_schools(enriched: list[dict]) -> list[dict]:
    """Merge AOPA directory schools and annotate existing ones with AOPA data.

    1. For enriched schools that already exist but lack a website, attach
       their AOPA business IDs so we can batch-resolve via the AOPA API.
    2. For AOPA-only schools not in the enriched list, add them with
       business IDs and domain-guess candidates as fallback.
    """
    aopa_path = OUTPUT_DIR / "aopa_flight_schools.json"
    if not aopa_path.exists():
        return enriched

    with open(aopa_path) as f:
        aopa_schools = json.load(f)

    # Collapse multi-location AOPA entries (same name) into one record,
    # keeping the first entry's details but collecting all AOPA detail URLs.
    aopa_by_name: dict[str, dict] = {}
    for s in aopa_schools:
        name = s.get("name", "").strip()
        if not name:
            continue
        aopa_url = s.get("aopa_url", "")
        if name not in aopa_by_name:
            aopa_by_name[name] = {
                "name": name,
                "city": s.get("city", ""),
                "state_name": s.get("state_name", ""),
                "phone": s.get("phone", ""),
                "website": s.get("website", ""),
                "airport_code": s.get("airport_id", ""),
                "source": "aopa",
                "sources": "aopa",
                "n_sources": 1,
                "_aopa_cities": {s.get("city", "").strip().lower()},
                "_aopa_urls": [aopa_url] if aopa_url else [],
            }
        else:
            aopa_by_name[name]["_aopa_cities"].add(s.get("city", "").strip().lower())
            if aopa_url:
                aopa_by_name[name]["_aopa_urls"].append(aopa_url)
            if not aopa_by_name[name]["website"] and s.get("website"):
                aopa_by_name[name]["website"] = s["website"]

    # Pass 1: annotate EXISTING enriched schools that lack websites with
    # AOPA business IDs (matched by normalized name).
    annotated = 0
    for s in enriched:
        if s.get("website"):
            continue
        name_lower = s.get("name", "").strip().lower()
        for aopa_name, aopa_rec in aopa_by_name.items():
            if aopa_name.lower() == name_lower and aopa_rec.get("_aopa_urls"):
                s["_aopa_urls"] = list(aopa_rec["_aopa_urls"])
                # Also attach domain-guess fallback
                slug = _slugify_name(aopa_name)
                if slug:
                    s["_website_candidates"] = [
                        f"https://{slug}.com",
                        f"https://www.{slug}.com",
                    ]
                annotated += 1
                break

    if annotated:
        print(f"  Existing schools annotated with AOPA IDs: {annotated}")

    # Pass 2: add AOPA-only schools not already in the enriched list.
    existing: set[tuple[str, str]] = set()
    for s in enriched:
        key = (s.get("name", "").strip().lower(), s.get("city", "").strip().lower())
        existing.add(key)

    added = 0
    for name, rec in aopa_by_name.items():
        cities = rec.pop("_aopa_cities")
        name_lower = name.strip().lower()
        if any((name_lower, c) in existing for c in cities):
            continue

        # Generate fallback candidate website URLs from the business name
        if not rec["website"]:
            slug = _slugify_name(name)
            if slug:
                rec["_website_candidates"] = [
                    f"https://{slug}.com",
                    f"https://www.{slug}.com",
                ]

        enriched.append(rec)
        added += 1

    if added:
        print(f"  AOPA-only schools merged: {added}")
    return enriched


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


async def probe_url(session: aiohttp.ClientSession, url: str) -> str | None:
    """Check if a URL resolves to a real website.  Returns final URL or None."""
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=10),
            allow_redirects=True,
        ) as resp:
            if resp.status == 200:
                text = await resp.text(errors="replace")
                # Reject parked / placeholder domains (very short pages)
                if len(text) > 1000:
                    return str(resp.url)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        pass
    return None


# ── Page discovery (same logic, async fetch) ─────────────────────

def _same_domain(base: str, candidate: str) -> bool:
    base_domain = urlparse(base).netloc.lower().lstrip("www.")
    cand_domain = urlparse(candidate).netloc.lower().lstrip("www.")
    return cand_domain == base_domain or cand_domain.endswith("." + base_domain)


def _find_fleet_links(base_url: str, soup: BeautifulSoup) -> list[str]:
    """Extract fleet-related links from a page's <a> tags."""
    candidates: list[str] = []
    for a in soup.find_all("a", href=True):
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
    return candidates


def _dedupe_urls(urls: list[str], limit: int = 16) -> list[str]:
    """Deduplicate URLs by stripping trailing slashes, preserving order."""
    seen = set()
    unique = []
    for u in urls:
        normalized = u.rstrip("/")
        if normalized not in seen:
            seen.add(normalized)
            unique.append(u)
    return unique[:limit]


def _parent_paths(urls: list[str], base_url: str) -> list[str]:
    """Compute parent directory URLs for a set of fleet page URLs.

    E.g. /aircraft-rentals/advanced-aircraft/ → /aircraft-rentals/
    Skips the site root and any URLs already in the input set.
    """
    existing = {u.rstrip("/") for u in urls}
    existing.add(base_url.rstrip("/"))
    parents: list[str] = []
    for url in urls:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        # Go up one level
        parent_path = path.rsplit("/", 1)[0]
        if parent_path and parent_path != "/":
            parent_url = f"{parsed.scheme}://{parsed.netloc}{parent_path}/"
            if parent_url.rstrip("/") not in existing:
                parents.append(parent_url)
                existing.add(parent_url.rstrip("/"))
    return parents


def find_fleet_pages(base_url: str, home_soup: BeautifulSoup) -> list[str]:
    """Find fleet page URLs from the home page."""
    candidates = _find_fleet_links(base_url, home_soup)
    return _dedupe_urls(candidates, limit=8)


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
    return _dedupe_urls(candidates, limit=4)


# ── Extraction (identical to serial version) ─────────────────────

def extract_airports(text: str) -> list[str]:
    valid = _load_valid_airports()
    icao = AIRPORT_ICAO_RE.findall(text)
    faa = AIRPORT_FAA_RE.findall(text)
    codes = set()
    for c in icao:
        c = c.upper()
        if c in valid:
            codes.add(c)
    for c in faa:
        c = c.upper()
        candidate = ("K" + c) if len(c) == 3 else c
        if candidate in valid:
            codes.add(candidate)
    return sorted(codes)


def _find_aircraft_containers(soup: BeautifulSoup) -> list[Tag]:
    for tag_type in ["div", "section", "article", "li", "tr"]:
        candidates = soup.find_all(tag_type)
        n_num_containers = []
        for c in candidates:
            text = c.get_text(strip=True)
            n_nums = [s for s in N_NUMBER_RE.findall(text) if len(s) >= _MIN_N_SUFFIX_LEN]
            if len(n_nums) == 1 and len(text) < 2000:
                n_num_containers.append(c)
        if len(n_num_containers) >= 2:
            return n_num_containers
    return []


def _parse_aircraft_container(container: Tag) -> dict | None:
    text = container.get_text(separator=" ", strip=True)
    n_nums = [s for s in N_NUMBER_RE.findall(text) if len(s) >= _MIN_N_SUFFIX_LEN]
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
        suffix = match.group(1)
        if len(suffix) < _MIN_N_SUFFIX_LEN:
            continue
        start = max(0, match.start() - 200)
        end = min(len(text), match.end() + 200)
        context = text[start:end]

        n_number = "N" + suffix
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
        valid = _load_valid_airports()
        if code in valid:
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
        try:
            return await asyncio.wait_for(
                _scrape_school_fleet_inner(session, school),
                timeout=PER_SCHOOL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return None


async def _resolve_website(
    session: aiohttp.ClientSession,
    school: dict,
) -> str | None:
    """Try to resolve a website for a school that has no known URL.

    Probes candidate domains derived from the business name (e.g.
    'JATO Aviation' → jatoaviation.com).
    """
    candidates = school.get("_website_candidates", [])
    for url in candidates:
        resolved = await probe_url(session, url)
        if resolved:
            return resolved
    return None


async def _scrape_school_fleet_inner(
    session: aiohttp.ClientSession,
    school: dict,
) -> dict | None:
    website = (school.get("website") or "").strip()

    # For schools without a known website, try to resolve one
    if not website:
        website = await _resolve_website(session, school) or ""

    if not website:
        return None
    if not website.startswith("http"):
        website = "https://" + website

    # Fetch home page
    home_soup = await fetch_page(session, website)
    if not home_soup:
        return None

    # ── Fleet page discovery (two levels) ────────────────────────
    # Level 1: find fleet links from the home page
    fleet_urls = find_fleet_pages(website, home_soup)

    # Level 2: also check parent paths of discovered fleet pages.
    # This handles sites like WVFC where /aircraft-rentals/advanced-aircraft/
    # is found from the home page but the sibling /aircraft-rentals/training-aircraft/
    # is only linked from the parent /aircraft-rentals/ page.
    parent_urls = _parent_paths(fleet_urls, website)
    parent_soups: dict[str, BeautifulSoup] = {}
    if parent_urls:
        tasks = [fetch_page(session, url) for url in parent_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for url, r in zip(parent_urls, results):
            if isinstance(r, BeautifulSoup):
                parent_soups[url] = r
                # Find more fleet links from the parent page
                more = _find_fleet_links(url, r)
                fleet_urls.extend(more)

    # Dedupe after the two-level expansion
    fleet_urls = _dedupe_urls(fleet_urls, limit=16)
    # Remove home page URL from fleet list (we handle it separately)
    fleet_urls = [u for u in fleet_urls if u.rstrip("/") != website.rstrip("/")]

    all_aircraft: list[dict] = []
    fleet_page_url = ""

    # Check home page for aircraft
    home_fleet = extract_fleet_from_page(home_soup, website)
    if home_fleet:
        all_aircraft.extend(home_fleet)
        fleet_page_url = website

    # Fetch all fleet sub-pages concurrently (same domain, small fan-out)
    # Re-use parent soups we already fetched instead of hitting them again.
    fleet_soups: dict[str, BeautifulSoup] = {}
    urls_to_fetch = [u for u in fleet_urls if u not in parent_soups]
    if urls_to_fetch:
        tasks = [fetch_page(session, url) for url in urls_to_fetch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for url, r in zip(urls_to_fetch, results):
            if isinstance(r, BeautifulSoup):
                fleet_soups[url] = r
    fleet_soups.update({u: s for u, s in parent_soups.items() if u in fleet_urls})

    # Level 3: scan fetched fleet pages for yet more fleet sub-page links
    # (handles pagination, tabbed fleet pages, etc.)
    extra_urls: list[str] = []
    all_seen = {u.rstrip("/") for u in fleet_urls} | {website.rstrip("/")}
    for url, soup in fleet_soups.items():
        for link in _find_fleet_links(url, soup):
            if link.rstrip("/") not in all_seen:
                extra_urls.append(link)
                all_seen.add(link.rstrip("/"))

    if extra_urls:
        extra_urls = extra_urls[:8]
        tasks = [fetch_page(session, url) for url in extra_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for url, r in zip(extra_urls, results):
            if isinstance(r, BeautifulSoup):
                fleet_soups[url] = r

    # Extract aircraft from all fleet pages
    for url, soup in fleet_soups.items():
        page_fleet = extract_fleet_from_page(soup, url)
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

    print(f"  Directory schools: {len(schools):,}")

    # Merge AOPA schools not already covered by the 3 directory sources
    schools = merge_aopa_schools(schools)

    print(f"  Total schools: {len(schools):,}")

    if limit:
        schools = schools[:limit]
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
        # ── Resolve websites for AOPA schools via their detail API ────
        needs_website = [s for s in schools if not s.get("website") and s.get("_aopa_urls")]
        if needs_website:
            print(f"\n  Resolving websites for {len(needs_website):,} AOPA schools via API...")
            api_semaphore = asyncio.Semaphore(50)  # polite concurrency for AOPA API
            aopa_websites = await _resolve_aopa_websites(
                session, needs_website, api_semaphore,
            )
            # Apply resolved websites back to school records
            resolved_count = 0
            for s in needs_website:
                for aopa_url in s.get("_aopa_urls", []):
                    bid = _extract_aopa_business_id(aopa_url)
                    if bid and bid in aopa_websites:
                        s["website"] = aopa_websites[bid]
                        resolved_count += 1
                        break
            print(f"  Resolved {resolved_count:,} websites from AOPA API")

        # Schools with a known website go straight to scraping.
        # Schools with _website_candidates get probed for a working domain.
        scrapeable = [s for s in schools if s.get("website") or s.get("_website_candidates")]
        print(f"  Scrapeable (website or candidates): {len(scrapeable):,}")

        tasks = [scrape_school_fleet(session, school, school_semaphore) for school in scrapeable]

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
    print(f"  Schools scraped:      {len(scrapeable):,}")
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
