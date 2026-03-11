#!/usr/bin/env python3
"""
09c_browser_extract.py — Sonnet extraction from browser-crawled text.

Reads cached rendered text from output/fleet_browser_text/ and sends it to
Claude Sonnet via the Anthropic Message Batches API for structured extraction.

This script produces a reproducible extraction results file
(fleet_browser_extract_results.jsonl) that can be consumed by
20_rebuild_flight_line.py — no results are written directly to flight_line.json.

Input:
  - output/fleet_browser_text/          — rendered text per domain
  - output/fleet_crawl_index.json       — school metadata (name, airport, city, state)
  - output/fleet_browser_index.json     — browser crawl metadata (if available)
  - output/faa_master.csv               — FAA N-number registry for validation

Output:
  - output/fleet_browser_extract_results.jsonl   — one JSON object per school
  - output/fleet_browser_extract_index.json      — index mapping custom_id -> school

Usage:
    python 09c_browser_extract.py                    # submit batch
    python 09c_browser_extract.py --poll BATCH_ID    # poll existing batch
    python 09c_browser_extract.py --limit 10         # first 10 (testing)
    python 09c_browser_extract.py --dry-run          # show stats, no API call
"""

import argparse
import csv
import json
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

import anthropic

OUTPUT_DIR = Path(__file__).parent.parent / "output"
TEXT_DIR = OUTPUT_DIR / "fleet_browser_text"

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 16384

# ── Extraction prompt (identical to 10_fleet_browser_crawl.py) ───

SYSTEM_PROMPT = """\
You are an aviation data extraction specialist. Your job is to find every \
single aircraft that a flight school operates, rents, or uses for training, \
plus the school's contact information.

CRITICAL: Be exhaustive. Scour every page thoroughly for:
- FAA tail numbers (N-numbers): format is N followed by 1-5 digits and \
0-2 letters (e.g., N172SP, N12345, N88PK, N5990E). They appear in page \
text, URLs, image captions, table cells, sidebars - look EVERYWHERE.
- Aircraft make/model: Cessna 172, Diamond DA40, Piper Archer, etc.
- Rental rates: hourly wet rates, dry rates, block rates, member rates - \
capture what you find.
- Equipment: G1000, Garmin 430, autopilot, IFR certified, etc.
- Contact info: phone numbers, email addresses, mailing addresses.
- Rate sheet URLs: if there is a linked PDF or page with a rate sheet or \
pricing document, include its URL.

RULES:
- Extract EVERY aircraft you can find evidence of. Each tail number is a \
separate aircraft entry even if same model.
- If a page describes an aircraft without an N-number, still extract it.
- Do NOT invent aircraft. Only extract what is actually on the pages.
- Airport codes: use 4-letter ICAO (prepend K to 3-letter FAA codes).
- Return ONLY the JSON object. No markdown, no explanation."""

USER_PROMPT_TEMPLATE = """\
Extract the fleet, location, and contact information for {school_name} \
from the following web pages.

Known information:
- School website: {school_website}
- Known airport: {known_airport}
- City/State: {city_state}

Return a JSON object with this exact schema:
{{
  "school_type": "flight_school_with_rentals",
  "contact": {{
    "phone": "(555) 123-4567",
    "email": "info@example.com",
    "address": "123 Airport Rd, City, ST 12345"
  }},
  "rate_sheet_url": "https://example.com/rates.pdf",
  "locations": [
    {{
      "airport_id": "KXXX",
      "airport_name": "Full Airport Name",
      "label": "optional label like 'Main base' or 'Satellite'"
    }}
  ],
  "fleet": [
    {{
      "n_number": "N12345",
      "display_name": "Cessna 172S Skyhawk",
      "description": "IFR equipped, G1000 glass cockpit",
      "wet_rate_hr": 195.00,
      "equipment": ["G1000", "IFR certified"],
      "airport_id": "KXXX",
      "source_url": "https://example.com/fleet/cessna-172s"
    }}
  ]
}}

Fields:
- school_type: classify this entity. One of:
  "flight_school_with_rentals" — offers flight training AND has aircraft \
available for rental/training (this is what we want)
  "flight_school_no_rentals" — offers flight training but no evidence of \
rentable aircraft (e.g. ground school only, sim-only, partner school)
  "flying_club" — membership-based flying club with shared aircraft
  "fbo" — fixed-base operator, may offer fuel/hangar/services but not \
primarily a flight school with a rentable training fleet
  "university_program" — university/college aviation degree program \
(may or may not have own fleet)
  "maintenance_shop" — aircraft maintenance/repair, not training
  "charter_operator" — charter/air taxi service, not training
  "defunct" — website is dead, parked domain, or business appears closed
  "other" — none of the above
- contact.phone: primary phone number, or "" if not found
- contact.email: primary contact email, or "" if not found
- contact.address: mailing/physical address, or "" if not found
- rate_sheet_url: URL to a PDF or page with the full rate sheet, or "" \
if not found
- locations[].airport_id: ICAO code (4 letters starting with K for US)
- fleet[].n_number: FAA N-number if visible, else ""
- fleet[].display_name: aircraft make/model (e.g. "Cessna 172S Skyhawk")
- fleet[].description: brief description of equipment/features
- fleet[].wet_rate_hr: hourly rental rate including fuel (number), or null
- fleet[].equipment: list of notable avionics/features
- fleet[].airport_id: which airport this aircraft is based at, if known
- fleet[].source_url: the URL of the page with the best/most detailed \
info about this aircraft. Prefer an individual aircraft detail page over \
a fleet listing page.

Web pages:

{pages_content}"""


# ── Build school index from browser text dirs + crawl metadata ───

def build_browser_school_index() -> list[dict]:
    """
    Build an index of all browser-crawled schools from the text cache.

    Cross-references with fleet_crawl_index.json and fleet_browser_index.json
    for school metadata (name, airport, city, state).
    """
    # Load metadata sources
    crawl_meta: dict[str, dict] = {}  # domain -> metadata

    crawl_index_path = OUTPUT_DIR / "fleet_crawl_index.json"
    if crawl_index_path.exists():
        for school in json.loads(crawl_index_path.read_text()):
            domain = _normalize_domain(school.get("school_website", ""))
            if domain:
                crawl_meta[domain] = school

    browser_index_path = OUTPUT_DIR / "fleet_browser_index.json"
    if browser_index_path.exists():
        for school in json.loads(browser_index_path.read_text()):
            domain = _normalize_domain(school.get("school_website", ""))
            if domain:
                # Browser index overrides HTTP crawl index
                crawl_meta[domain] = school

    # Scan text directories
    schools = []
    if not TEXT_DIR.exists():
        print("ERROR: fleet_browser_text/ directory not found")
        return []

    for domain_dir in sorted(TEXT_DIR.iterdir()):
        if not domain_dir.is_dir():
            continue
        domain = domain_dir.name

        # Collect text files with their page URLs (reconstructed from filenames)
        text_files = sorted(domain_dir.glob("*.txt"))
        if not text_files:
            continue

        # Get metadata from crawl indexes
        meta = crawl_meta.get(domain, {})

        # Build page list
        pages = []
        for tf in text_files:
            # Try to reconstruct URL from filename
            # Format: path_slug_hash.txt
            stem = tf.stem
            # Remove the hash suffix (last 8 hex chars after underscore)
            parts = stem.rsplit("_", 1)
            if len(parts) == 2 and len(parts[1]) == 8:
                path_slug = parts[0]
            else:
                path_slug = stem

            # Reconstruct approximate URL
            if path_slug == "index":
                url = f"https://{domain}/"
            else:
                url = f"https://{domain}/{path_slug.replace('_', '/')}"

            # Determine page type from path
            page_type = "home" if path_slug == "index" else "fleet"
            lower_slug = path_slug.lower()
            if any(kw in lower_slug for kw in ["fleet", "aircraft", "rental", "plane", "rate"]):
                page_type = "fleet"
            elif any(kw in lower_slug for kw in ["about", "contact", "location"]):
                page_type = "about"

            pages.append({
                "url": url,
                "text_path": f"{domain}/{tf.name}",
                "type": page_type,
            })

        # Try to get better page URLs from the crawl indexes
        if meta.get("pages"):
            # Use the crawl index page list if available (has real URLs)
            meta_pages = {_normalize_domain(p.get("url", "")): p for p in meta["pages"]
                         if p.get("url")}
            # But we still need to map to our text files
            pass  # Use our reconstructed URLs — they're fine for extraction

        schools.append({
            "domain": domain,
            "school_name": meta.get("school_name", domain),
            "school_website": meta.get("school_website", f"https://{domain}/"),
            "known_airport": meta.get("known_airport", ""),
            "city": meta.get("city", ""),
            "state": meta.get("state", ""),
            "pages": pages,
        })

    return schools


def _normalize_domain(url: str) -> str:
    """Extract domain from URL."""
    from urllib.parse import urlparse
    if not url:
        return ""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        domain = parsed.netloc.lower().strip()
        if domain.startswith("www."):
            domain = domain[4:]
        if ":" in domain:
            domain = domain.split(":")[0]
        return domain
    except Exception:
        return ""


def load_page_text(text_path: str) -> str:
    """Load rendered text from cache."""
    full_path = TEXT_DIR / text_path
    if full_path.exists():
        return full_path.read_text(encoding="utf-8", errors="replace")
    return ""


def build_pages_content(school: dict) -> str:
    """Concatenate all pages for a school into a single text block."""
    parts = []
    for pg in school["pages"]:
        text = load_page_text(pg["text_path"])
        if not text or len(text.strip()) < 50:
            continue
        if len(text) > 40_000:
            text = text[:40_000] + "\n[... truncated ...]"
        parts.append(f"--- Page: {pg['url']} (type: {pg['type']}) ---\n{text}")
    return "\n\n".join(parts)


# ── Batch API ────────────────────────────────────────────────────

def build_batch_requests(schools: list[dict]) -> list[dict]:
    """Build Anthropic batch request objects."""
    requests = []
    for i, school in enumerate(schools):
        pages_content = build_pages_content(school)
        if not pages_content.strip():
            continue

        city = school.get("city", "")
        state = school.get("state", "")
        city_state = f"{city}, {state}" if city and state else city or state or "Unknown"

        known_airport = school.get("known_airport", "")
        if known_airport:
            code = known_airport.upper()
            if len(code) == 3:
                code = "K" + code
            known_airport = code

        user_msg = USER_PROMPT_TEMPLATE.format(
            school_name=school["school_name"],
            school_website=school["school_website"],
            known_airport=known_airport or "Not specified",
            city_state=city_state,
            pages_content=pages_content,
        )

        requests.append({
            "custom_id": f"browser_{i:04d}",
            "domain": school["domain"],
            "params": {
                "model": MODEL,
                "max_tokens": MAX_TOKENS,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_msg}],
            },
        })

    return requests


def submit_batch(requests: list[dict]) -> str:
    """Submit to Anthropic Message Batches API."""
    client = anthropic.Anthropic()

    batch_requests = []
    for req in requests:
        batch_requests.append({
            "custom_id": req["custom_id"],
            "params": req["params"],
        })

    print(f"  Submitting batch of {len(batch_requests):,} requests...")
    batch = client.messages.batches.create(requests=batch_requests)
    print(f"  Batch ID: {batch.id}")
    print(f"  Status: {batch.processing_status}")
    return batch.id


def poll_batch(batch_id: str) -> list[dict]:
    """Poll batch until complete, return results."""
    client = anthropic.Anthropic()

    while True:
        batch = client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        total = counts.processing + counts.succeeded + counts.errored + counts.canceled + counts.expired
        done = counts.succeeded + counts.errored + counts.canceled + counts.expired
        pct = (done / total * 100) if total else 0

        print(f"  Status: {batch.processing_status} — {done}/{total} ({pct:.0f}%) "
              f"[ok:{counts.succeeded} err:{counts.errored}]")

        if batch.processing_status == "ended":
            break

        time.sleep(30)

    # Download results
    results = []
    for result in client.messages.batches.results(batch_id):
        results.append({
            "custom_id": result.custom_id,
            "result": {
                "type": result.result.type,
                "message": result.result.message.model_dump() if hasattr(result.result, 'message') and result.result.message else None,
            } if result.result else None,
        })

    print(f"  Downloaded {len(results):,} results")
    return results


def save_results(results: list[dict], schools: list[dict], requests: list[dict]):
    """Save extraction results and index."""
    # Save results
    results_path = OUTPUT_DIR / "fleet_browser_extract_results.jsonl"
    with open(results_path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"  Results saved to {results_path}")

    # Save index (custom_id -> school metadata)
    index = []
    req_map = {r["custom_id"]: r["domain"] for r in requests}
    school_map = {s["domain"]: s for s in schools}
    for req in requests:
        domain = req["domain"]
        school = school_map.get(domain, {})
        index.append({
            "custom_id": req["custom_id"],
            "domain": domain,
            "school_name": school.get("school_name", ""),
            "school_website": school.get("school_website", ""),
            "known_airport": school.get("known_airport", ""),
            "city": school.get("city", ""),
            "state": school.get("state", ""),
        })

    index_path = OUTPUT_DIR / "fleet_browser_extract_index.json"
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)
    print(f"  Index saved to {index_path}")


def main():
    parser = argparse.ArgumentParser(description="Sonnet extraction from browser-crawled text")
    parser.add_argument("--poll", type=str, help="Poll existing batch ID")
    parser.add_argument("--limit", type=int, help="Limit number of schools")
    parser.add_argument("--dry-run", action="store_true", help="Show stats, no API call")
    args = parser.parse_args()

    print("=== Browser Text Extraction ===\n")

    # Build school index
    print("Building school index from browser text cache...")
    schools = build_browser_school_index()
    print(f"  {len(schools):,} domains with text")

    if args.limit:
        schools = schools[:args.limit]
        print(f"  Limited to {len(schools):,}")

    # Build batch requests
    print("\nBuilding batch requests...")
    requests = build_batch_requests(schools)
    print(f"  {len(requests):,} requests with content")

    # Estimate cost
    total_chars = sum(
        len(r["params"]["messages"][0]["content"])
        for r in requests
    )
    est_input_tokens = total_chars / 4  # rough chars-to-tokens
    est_cost = (est_input_tokens / 1e6) * 1.50  # batch pricing
    print(f"  Estimated input: {est_input_tokens/1e6:.1f}M tokens")
    print(f"  Estimated cost: ~${est_cost:.2f} (batch pricing)")

    if args.dry_run:
        print("\nDRY RUN — no API call")
        return

    if args.poll:
        # Poll existing batch
        print(f"\nPolling batch {args.poll}...")
        results = poll_batch(args.poll)
    else:
        # Submit new batch
        print("\nSubmitting batch...")
        batch_id = submit_batch(requests)

        # Save batch ID for resume
        batch_id_path = OUTPUT_DIR / "fleet_browser_extract_batch_id.txt"
        batch_id_path.write_text(batch_id)
        print(f"  Batch ID saved to {batch_id_path}")

        # Poll
        print("\nPolling for results...")
        time.sleep(10)
        results = poll_batch(batch_id)

    # Save results
    print("\nSaving results...")
    save_results(results, schools, requests)

    # Quick stats
    succeeded = sum(1 for r in results if r.get("result", {}).get("type") == "succeeded")
    print(f"\n=== COMPLETE ===")
    print(f"  Succeeded: {succeeded:,}")
    print(f"  Total: {len(results):,}")


if __name__ == "__main__":
    main()
