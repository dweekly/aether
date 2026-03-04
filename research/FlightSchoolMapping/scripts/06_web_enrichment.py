#!/usr/bin/env python3
"""
Phase 6: Enrich flight school candidates with web-scraped data.

For each candidate flight school from Phase 5, searches the web and scrapes
school websites to collect:
- Mailing/physical address
- Phone number and email
- Website URL
- Fleet listed on their website
- Ratings/certificates offered
- AOPA directory listing data

Rate-limited and robots.txt-respectful.

Output: output/enriched_schools.csv — schools with contact info and web fleet data
"""

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
import pandas as pd
from bs4 import BeautifulSoup
from tqdm import tqdm

OUTPUT_DIR = Path(__file__).parent.parent / "output"

# Respectful scraping configuration
REQUEST_DELAY_SEC = 1.5  # seconds between requests
USER_AGENT = "AetherResearch/1.0 (aviation research; contact: research@aether.aero)"
REQUEST_TIMEOUT = 15
MAX_RETRIES = 2

# AOPA flight school finder
AOPA_SEARCH_URL = "https://www.aopa.org/learntofly/school/search_results.cfm"

# Phone number regex (US format)
PHONE_REGEX = re.compile(
    r"(?:\+?1[-.\s]?)?\(?(\d{3})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})"
)

# Email regex
EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
)

# Common training certificate patterns
CERT_PATTERNS = {
    "PPL": re.compile(r"\b(private\s+pilot|PPL)\b", re.IGNORECASE),
    "IR": re.compile(r"\b(instrument\s+rating|IFR|IR)\b", re.IGNORECASE),
    "CPL": re.compile(r"\b(commercial\s+pilot|CPL)\b", re.IGNORECASE),
    "CFI": re.compile(r"\b(certified\s+flight\s+instructor|CFI)\b", re.IGNORECASE),
    "CFII": re.compile(r"\b(CFII|instrument\s+instructor)\b", re.IGNORECASE),
    "MEI": re.compile(r"\b(MEI|multi.engine\s+instructor)\b", re.IGNORECASE),
    "ATP": re.compile(r"\b(airline\s+transport|ATP)\b", re.IGNORECASE),
    "Sport": re.compile(r"\b(sport\s+pilot)\b", re.IGNORECASE),
}

# N-number regex
N_NUMBER_REGEX = re.compile(r"\bN\d{1,5}[A-Z]{0,2}\b")


def get_session() -> requests.Session:
    """Create a requests session with respectful headers."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    })
    return session


def check_robots_txt(url: str) -> bool:
    """Check if we're allowed to scrape the given URL per robots.txt."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = RobotFileParser()
    try:
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        # If we can't read robots.txt, default to allowed
        return True


def fetch_page(session: requests.Session, url: str) -> BeautifulSoup | None:
    """Fetch a page and return parsed HTML, respecting robots.txt."""
    if not check_robots_txt(url):
        return None

    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return BeautifulSoup(resp.text, "html.parser")
            elif resp.status_code == 429:
                time.sleep(5 * (attempt + 1))
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))

    return None


def extract_contact_info(soup: BeautifulSoup, url: str) -> dict:
    """Extract contact information from a parsed webpage."""
    text = soup.get_text(separator=" ", strip=True)
    result = {}

    # Phone numbers
    phones = PHONE_REGEX.findall(text)
    if phones:
        # Format as (XXX) XXX-XXXX
        formatted = [f"({p[0]}) {p[1]}-{p[2]}" for p in phones]
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for p in formatted:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        result["phones"] = unique[:3]  # Top 3 unique numbers

    # Email addresses
    emails = EMAIL_REGEX.findall(text)
    if emails:
        # Filter out common false positives
        filtered = [
            e for e in emails
            if not any(x in e.lower() for x in ["example.com", "sentry.io", "wixpress"])
        ]
        result["emails"] = list(set(filtered))[:3]

    # Physical address — look for structured data first
    address_tags = soup.find_all(
        attrs={"itemprop": re.compile(r"address|streetAddress|addressLocality")}
    )
    if address_tags:
        result["structured_address"] = " ".join(
            tag.get_text(strip=True) for tag in address_tags
        )

    # N-numbers mentioned on the page (fleet page)
    n_numbers = N_NUMBER_REGEX.findall(text)
    if n_numbers:
        result["website_n_numbers"] = list(set(n_numbers))

    # Certificates/ratings offered
    certs = []
    for cert_name, pattern in CERT_PATTERNS.items():
        if pattern.search(text):
            certs.append(cert_name)
    if certs:
        result["ratings_offered"] = certs

    return result


def search_school_website(session: requests.Session, school_name: str, airport_code: str, state: str) -> str | None:
    """
    Search for a flight school's website.

    Uses a simple heuristic: construct likely search queries and check results.
    In production, this would use a search API (Google Custom Search, Bing, etc.)
    """
    # For now, return None — in production, integrate with a search API
    # The AOPA directory and direct Google searches would go here
    return None


def scrape_aopa_directory(session: requests.Session, state: str) -> list[dict]:
    """
    Scrape the AOPA flight school directory for a given state.

    Note: AOPA may require authentication or have anti-scraping measures.
    This function attempts a best-effort scrape.
    """
    results = []

    # AOPA flight school finder URL
    url = f"https://www.aopa.org/learntofly/school/search_results.cfm?state={state}"

    if not check_robots_txt(url):
        return results

    soup = fetch_page(session, url)
    if not soup:
        return results

    # Parse school listings (structure depends on AOPA's current HTML)
    # This is a best-effort parser that may need updating
    listings = soup.find_all("div", class_=re.compile(r"school|listing|result"))
    for listing in listings:
        name_tag = listing.find(["h2", "h3", "h4", "a"])
        if name_tag:
            school = {
                "name": name_tag.get_text(strip=True),
                "source": "aopa_directory",
            }
            # Try to extract address and phone
            text = listing.get_text(separator="\n", strip=True)
            phones = PHONE_REGEX.findall(text)
            if phones:
                school["phone"] = f"({phones[0][0]}) {phones[0][1]}-{phones[0][2]}"
            results.append(school)

    return results


def enrich_school(session: requests.Session, school: pd.Series) -> dict:
    """Enrich a single flight school candidate with web data."""
    enriched = {
        "entity_name": school.get("entity_name", ""),
        "home_airport": school.get("home_airport", ""),
        "state": school.get("state", ""),
        "website": "",
        "phone": "",
        "email": "",
        "mailing_address": "",
        "website_fleet": "",
        "ratings_offered": "",
        "enrichment_source": "",
    }

    name = school.get("entity_name", "")
    airport = school.get("home_airport", "")
    state = school.get("state", "")

    # Try to find school website
    website = search_school_website(session, name, airport, state)
    if website:
        enriched["website"] = website
        enriched["enrichment_source"] = "website_scrape"

        # Scrape the website
        soup = fetch_page(session, website)
        if soup:
            info = extract_contact_info(soup, website)
            if info.get("phones"):
                enriched["phone"] = info["phones"][0]
            if info.get("emails"):
                enriched["email"] = info["emails"][0]
            if info.get("structured_address"):
                enriched["mailing_address"] = info["structured_address"]
            if info.get("website_n_numbers"):
                enriched["website_fleet"] = ",".join(info["website_n_numbers"])
            if info.get("ratings_offered"):
                enriched["ratings_offered"] = ",".join(info["ratings_offered"])

            # Also check for a fleet/aircraft page
            fleet_links = soup.find_all("a", string=re.compile(r"fleet|aircraft|planes", re.IGNORECASE))
            for link in fleet_links:
                href = link.get("href")
                if href:
                    fleet_url = urljoin(website, href)
                    fleet_soup = fetch_page(session, fleet_url)
                    if fleet_soup:
                        fleet_info = extract_contact_info(fleet_soup, fleet_url)
                        if fleet_info.get("website_n_numbers"):
                            existing = enriched["website_fleet"].split(",") if enriched["website_fleet"] else []
                            all_nums = list(set(existing + fleet_info["website_n_numbers"]))
                            enriched["website_fleet"] = ",".join(all_nums)
                    time.sleep(REQUEST_DELAY_SEC)
                    break  # Only check first fleet page

    # Use FAA registry address as fallback
    if not enriched["mailing_address"]:
        street = school.get("street", "")
        city = school.get("city", "")
        state_val = school.get("state", "")
        zip_code = school.get("zip_code", "")
        if street and city:
            enriched["mailing_address"] = f"{street}, {city}, {state_val} {zip_code}".strip()
            enriched["enrichment_source"] = enriched["enrichment_source"] or "faa_registry"

    return enriched


def run(schools: pd.DataFrame = None):
    """Execute Phase 6: Web enrichment."""
    print("=" * 60)
    print("Phase 6: Web Enrichment")
    print("=" * 60)

    # Load data if not provided
    if schools is None:
        path = OUTPUT_DIR / "identified_schools.csv"
        if path.exists():
            schools = pd.read_csv(path, dtype=str, keep_default_na=False)
        else:
            print("  Error: Run Phase 5 first")
            return None

    print(f"\n  Candidate schools to enrich: {len(schools):,}")

    session = get_session()
    enriched_records = []

    for _, school in tqdm(schools.iterrows(), total=len(schools), desc="Enriching"):
        enriched = enrich_school(session, school)
        enriched_records.append(enriched)
        time.sleep(REQUEST_DELAY_SEC)

    result = pd.DataFrame(enriched_records)

    # Merge enriched data with original school data
    if not result.empty:
        # Combine with original columns
        combined = schools.copy()
        for col in ["website", "phone", "email", "mailing_address",
                     "website_fleet", "ratings_offered", "enrichment_source"]:
            if col in result.columns:
                combined[col] = result[col].values

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / "enriched_schools.csv"
        combined.to_csv(output_path, index=False)
        print(f"\n  Saved enriched schools: {output_path}")

        with_website = (combined.get("website", pd.Series(dtype=str)) != "").sum()
        with_phone = (combined.get("phone", pd.Series(dtype=str)) != "").sum()
        with_email = (combined.get("email", pd.Series(dtype=str)) != "").sum()
        with_address = (combined.get("mailing_address", pd.Series(dtype=str)) != "").sum()

        print(f"  Enrichment coverage:")
        print(f"    With website: {with_website:,}")
        print(f"    With phone: {with_phone:,}")
        print(f"    With email: {with_email:,}")
        print(f"    With address: {with_address:,}")
    else:
        print("  No enrichment results.")

    print(f"\nPhase 6 complete.")
    return result


if __name__ == "__main__":
    run()
