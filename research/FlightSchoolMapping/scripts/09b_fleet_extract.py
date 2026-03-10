#!/usr/bin/env python3
"""
Phase 9b: Fleet Extraction via Sonnet — LLM-based structured extraction.

Reads cached fleet page HTML (from 09a_fleet_crawl.py) and sends it to
Claude Sonnet via the Anthropic Message Batches API for structured
extraction of aircraft, locations, and rental rates.

Usage:
    python 09b_fleet_extract.py                    # process all schools
    python 09b_fleet_extract.py --limit 10         # first 10 (testing)
    python 09b_fleet_extract.py --poll BATCH_ID    # resume polling a batch
    python 09b_fleet_extract.py --sync             # use sync API (testing)

Input:  output/fleet_crawl_index.json + output/fleet_html/
Output: output/flight_line.json
"""

import argparse
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

import anthropic

OUTPUT_DIR = Path(__file__).parent.parent / "output"
HTML_DIR = OUTPUT_DIR / "fleet_html"

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096

# ── Valid airport set for post-processing ────────────────────────

_VALID_AIRPORT_IDS: set[str] | None = None


def _load_valid_airports() -> set[str]:
    global _VALID_AIRPORT_IDS
    if _VALID_AIRPORT_IDS is not None:
        return _VALID_AIRPORT_IDS
    airports_path = OUTPUT_DIR / "airports.csv"
    ids: set[str] = set()
    if airports_path.exists():
        with open(airports_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                for col in ["airport_id", "faa_id", "gps_code"]:
                    aid = (row.get(col) or "").strip().upper()
                    if aid:
                        ids.add(aid)
                        if len(aid) == 3:
                            ids.add("K" + aid)
    _VALID_AIRPORT_IDS = ids
    return ids


# ── FAA N-number registry for validation ─────────────────────────

_FAA_N_NUMBERS: set[str] | None = None


def _load_faa_n_numbers() -> set[str]:
    """Load registered N-numbers from FAA master file if available."""
    global _FAA_N_NUMBERS
    if _FAA_N_NUMBERS is not None:
        return _FAA_N_NUMBERS
    # Try common locations for the FAA releasable database MASTER.txt
    candidates = [
        OUTPUT_DIR / "faa_master.csv",
        OUTPUT_DIR / "MASTER.txt",
        OUTPUT_DIR.parent / "data" / "faa_master.csv",
        OUTPUT_DIR.parent / "data" / "MASTER.txt",
    ]
    _FAA_N_NUMBERS = set()
    for path in candidates:
        if path.exists():
            with open(path, newline="", errors="replace") as f:
                reader = csv.reader(f)
                for row in reader:
                    if row:
                        nn = row[0].strip().upper()
                        if nn and nn[0].isdigit():
                            _FAA_N_NUMBERS.add("N" + nn)
            print(f"  Loaded {len(_FAA_N_NUMBERS):,} N-numbers from {path.name}")
            break
    return _FAA_N_NUMBERS


# ── Extraction prompt ────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an aviation data extraction specialist. Your job is to find every \
single aircraft that a flight school operates, rents, or uses for training, \
plus the school's contact information.

CRITICAL: Be exhaustive. Scour every page thoroughly for:
- FAA tail numbers (N-numbers): format is N followed by 1-5 digits and \
0-2 letters (e.g., N172SP, N12345, N88PK, N5990E). They appear in page \
text, URLs, image captions, table cells, sidebars — look EVERYWHERE.
- Aircraft make/model: Cessna 172, Diamond DA40, Piper Archer, etc.
- Rental rates: hourly wet rates, dry rates, block rates — capture what \
you find.
- Equipment: G1000, Garmin 430, autopilot, IFR certified, etc.
- Contact info: phone numbers, email addresses, mailing addresses.

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
  "canonical_name": "Best Human-Readable Name for This School",
  "school_type": "flight_school_with_rentals",
  "contact": {{
    "phone": "(555) 123-4567",
    "email": "info@example.com",
    "address": "123 Airport Rd, City, ST 12345"
  }},
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
- canonical_name: the best human-readable name for this flight school as it \
would appear on a business card. Derive from the site's title, headers, \
logo text, or about page — NOT from the directory listing or domain name. \
For example, wvfc.org should be "West Valley Flying Club", not "WVFC".
- school_type: one of "flight_school_with_rentals", "flight_school_no_rentals", \
"flying_club", "fbo", "university_program", "maintenance_shop", \
"charter_operator", "defunct", "other"
- contact.phone: primary phone number, or "" if not found
- contact.email: primary contact email, or "" if not found
- contact.address: mailing/physical address, or "" if not found
- locations[].airport_id: ICAO code (4 letters starting with K for US)
- fleet[].n_number: FAA N-number if visible, else ""
- fleet[].display_name: aircraft make/model (e.g. "Cessna 172S Skyhawk")
- fleet[].description: brief description of equipment/features
- fleet[].wet_rate_hr: hourly rental rate including fuel (number), or null
- fleet[].equipment: list of notable avionics/features
- fleet[].airport_id: which airport this aircraft is based at, if known
- fleet[].source_url: the URL of the page with the best/most detailed \
info about this aircraft. Prefer an individual aircraft detail page over \
a fleet listing page. Use the exact URL from the "--- Page: <url> ---" \
headers above.

Web pages:

{pages_content}"""


# ── Build requests ───────────────────────────────────────────────

def _load_page_html(html_path: str) -> str:
    """Load cleaned HTML from the fleet_html directory."""
    full_path = HTML_DIR / html_path
    if full_path.exists():
        return full_path.read_text(encoding="utf-8", errors="replace")
    return ""


def _build_pages_content(school: dict) -> str:
    """Concatenate all pages for a school into a single text block."""
    parts: list[str] = []
    for page in school["pages"]:
        html = _load_page_html(page["html_path"])
        if not html:
            continue
        # Truncate very large pages to stay within token budget
        if len(html) > 50_000:
            html = html[:50_000] + "\n[... truncated ...]"
        parts.append(f"--- Page: {page['url']} (type: {page['type']}) ---\n{html}")
    return "\n\n".join(parts)


def build_batch_requests(crawl_index: list[dict]) -> list[dict]:
    """Build Anthropic batch request objects from the crawl index."""
    requests = []
    for i, school in enumerate(crawl_index):
        pages_content = _build_pages_content(school)
        if not pages_content:
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
            "custom_id": f"school_{i:04d}",
            "params": {
                "model": MODEL,
                "max_tokens": MAX_TOKENS,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_msg}],
            },
        })

    return requests


# ── Result parsing ───────────────────────────────────────────────

def _parse_llm_response(text: str) -> dict | None:
    """Extract JSON from the LLM response text."""
    text = text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (code fences)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                return None
    return None


def _try_fix_n_number(nn: str, faa_numbers: set[str]) -> str | None:
    """Try to auto-correct an invalid N-number. Returns corrected or None."""
    import re
    # Too many characters — try stripping leading digit(s) after N
    # e.g. N16186T (from URL slug) -> N6186T
    if len(nn) > 7:  # N + max 6 chars
        for trim in range(1, len(nn) - 5):
            candidate = "N" + nn[1 + trim:]
            if re.match(r"^N\d{1,5}[A-Z]{0,2}$", candidate) and candidate in faa_numbers:
                return candidate

    # Common OCR-style substitutions: O↔0, I↔1, S↔5
    subs = {"O": "0", "0": "O", "I": "1", "1": "I", "S": "5", "5": "S"}
    for i, ch in enumerate(nn[1:], 1):
        if ch in subs:
            candidate = nn[:i] + subs[ch] + nn[i + 1:]
            if re.match(r"^N\d{1,5}[A-Z]{0,2}$", candidate) and candidate in faa_numbers:
                return candidate

    return None


def _validate_and_flag(school_entry: dict, school_meta: dict) -> dict:
    """Validate extracted data and flag suspicious entries."""
    import re
    valid_airports = _load_valid_airports()
    faa_numbers = _load_faa_n_numbers()

    # Validate airport codes
    for loc in school_entry.get("locations", []):
        code = (loc.get("airport_id") or "").upper()
        if code and code not in valid_airports:
            loc["_flag"] = "unknown_airport"

    # Validate N-numbers and airport references in fleet
    for ac in school_entry.get("fleet", []):
        flags: list[str] = []
        nn = (ac.get("n_number") or "").upper()
        if nn:
            valid_format = bool(re.match(r"^N\d{1,5}[A-Z]{0,2}$", nn))
            in_registry = faa_numbers and nn in faa_numbers

            if not valid_format or (faa_numbers and not in_registry):
                # Try auto-correction
                fixed = _try_fix_n_number(nn, faa_numbers) if faa_numbers else None
                if fixed:
                    ac["n_number"] = fixed
                    ac.setdefault("_corrections", []).append(
                        f"n_number: {nn} -> {fixed}"
                    )
                elif not valid_format:
                    flags.append("invalid_n_number_format")
                else:
                    flags.append("n_number_not_in_faa_registry")

        airport = (ac.get("airport_id") or "").upper()
        if airport and airport not in valid_airports:
            flags.append("unknown_airport")

        if flags:
            ac["_flags"] = flags

    return school_entry


def process_results(
    crawl_index: list[dict],
    batch_results: list[dict],
    requests: list[dict],
) -> list[dict]:
    """Parse batch results and build the final flight_line.json."""
    # Map custom_id → crawl index entry
    id_to_school: dict[str, dict] = {}
    for req in requests:
        cid = req["custom_id"]
        idx = int(cid.split("_")[1])
        id_to_school[cid] = crawl_index[idx]

    flight_line: list[dict] = []
    successes = 0
    parse_errors = 0
    api_errors = 0

    for result in batch_results:
        cid = result.get("custom_id", "")
        school_meta = id_to_school.get(cid)
        if not school_meta:
            continue

        result_body = result.get("result", {})
        if result_body.get("type") != "succeeded":
            api_errors += 1
            continue

        # Extract text from the message response
        message = result_body.get("message", {})
        text_parts = []
        for block in message.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block["text"])
        response_text = "\n".join(text_parts)

        parsed = _parse_llm_response(response_text)
        if not parsed:
            parse_errors += 1
            continue

        # Validate and flag
        parsed = _validate_and_flag(parsed, school_meta)

        # Find the best fleet listing page for the school
        fleet_page_url = ""
        for page in school_meta.get("pages", []):
            if page["type"] == "fleet":
                fleet_page_url = page["url"]
                break

        # Tag each aircraft with extraction provenance
        for aircraft in parsed.get("fleet", []):
            if "_extraction_method" not in aircraft:
                aircraft["_extraction_method"] = "sonnet_text"
            if "_extracted_at" not in aircraft:
                aircraft["_extracted_at"] = school_meta.get("crawl_ts", "")

        entry = {
            "school_name": school_meta["school_name"],
            "school_website": school_meta["school_website"],
            "fleet_page_url": fleet_page_url,
            "contact": parsed.get("contact", {}),
            "locations": parsed.get("locations", []),
            "fleet": parsed.get("fleet", []),
            "scrape_ts": school_meta.get("crawl_ts", ""),
        }

        if entry["fleet"] or entry["locations"] or any(entry.get("contact", {}).values()):
            flight_line.append(entry)
            successes += 1

    print(f"  Parsed: {successes:,} schools with data")
    print(f"  Parse errors: {parse_errors:,}")
    print(f"  API errors: {api_errors:,}")
    return flight_line


# ── Batch API ────────────────────────────────────────────────────

def submit_batch(requests: list[dict]) -> str:
    """Submit requests to the Anthropic Message Batches API."""
    client = anthropic.Anthropic()

    batch_requests = []
    for req in requests:
        batch_requests.append({
            "custom_id": req["custom_id"],
            "params": {
                "model": req["params"]["model"],
                "max_tokens": req["params"]["max_tokens"],
                "system": req["params"]["system"],
                "messages": req["params"]["messages"],
            },
        })

    print(f"  Submitting batch of {len(batch_requests):,} requests...")
    batch = client.messages.batches.create(requests=batch_requests)
    print(f"  Batch ID: {batch.id}")
    print(f"  Status: {batch.processing_status}")
    return batch.id


def poll_batch(batch_id: str) -> list[dict]:
    """Poll until batch completes, then download results."""
    client = anthropic.Anthropic()

    print(f"  Polling batch {batch_id}...")
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        total = counts.processing + counts.succeeded + counts.errored + counts.canceled + counts.expired
        done = counts.succeeded + counts.errored + counts.canceled + counts.expired
        print(f"    {done}/{total} done ({counts.succeeded} ok, {counts.errored} err) "
              f"- {batch.processing_status}")

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
                "message": result.result.message.model_dump()
                if hasattr(result.result, "message") and result.result.message
                else {},
            },
        })

    print(f"  Downloaded {len(results):,} results")
    return results


def run_sync(requests: list[dict]) -> list[dict]:
    """Process requests synchronously via standard API (for testing)."""
    client = anthropic.Anthropic()
    results = []

    for i, req in enumerate(requests):
        print(f"  [{i+1}/{len(requests)}] {req['custom_id']}...", end=" ", flush=True)
        try:
            message = client.messages.create(
                model=req["params"]["model"],
                max_tokens=req["params"]["max_tokens"],
                system=req["params"]["system"],
                messages=req["params"]["messages"],
            )
            results.append({
                "custom_id": req["custom_id"],
                "result": {
                    "type": "succeeded",
                    "message": message.model_dump(),
                },
            })
            print("ok")
        except Exception as e:
            results.append({
                "custom_id": req["custom_id"],
                "result": {"type": "errored", "message": {}},
            })
            print(f"error: {e}")

    return results


# ── Main ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Phase 9b: Extract fleet data via Sonnet")
    parser.add_argument("--limit", type=int, help="Max schools to process")
    parser.add_argument("--poll", type=str, help="Poll an existing batch ID instead of submitting")
    parser.add_argument("--sync", action="store_true", help="Use sync API instead of batch (testing)")
    parser.add_argument("--source", type=str, help="Filter crawl index to schools from this source")
    parser.add_argument("--index", type=str, help="Path to crawl index JSON (default: fleet_crawl_index.json)")
    parser.add_argument("--merge", action="store_true", help="Merge into existing flight_line.json instead of overwriting")
    args = parser.parse_args()

    print("=" * 60)
    print("Phase 9b: Fleet Extraction via Sonnet")
    print("=" * 60)

    # Load crawl index
    index_path = Path(args.index) if args.index else OUTPUT_DIR / "fleet_crawl_index.json"
    if not index_path.exists():
        print(f"  Error: {index_path} not found.")
        return
    with open(index_path) as f:
        crawl_index = json.load(f)

    print(f"  Schools in crawl index: {len(crawl_index):,}")

    if args.source:
        # Filter to schools whose website matches the source in enriched data
        enriched_path = OUTPUT_DIR / "flight_schools_enriched.json"
        if enriched_path.exists():
            with open(enriched_path) as f:
                enriched = json.load(f)
            source_websites = set()
            for s in enriched:
                if s.get("source") == args.source:
                    web = (s.get("website") or "").rstrip("/")
                    if web:
                        source_websites.add(web)
            crawl_index = [
                s for s in crawl_index
                if (s.get("school_website") or "").rstrip("/") in source_websites
            ]
            print(f"  Filtered to source={args.source}: {len(crawl_index):,}")

    if args.limit:
        crawl_index = crawl_index[:args.limit]
        print(f"  Limited to: {args.limit}")

    if args.poll:
        # Resume polling an existing batch
        requests = build_batch_requests(crawl_index)
        results = poll_batch(args.poll)
    else:
        # Build and submit
        requests = build_batch_requests(crawl_index)
        print(f"  Batch requests built: {len(requests):,}")

        if not requests:
            print("  No requests to process.")
            return

        # Estimate token usage
        total_chars = sum(
            len(r["params"]["system"]) + len(r["params"]["messages"][0]["content"])
            for r in requests
        )
        est_tokens = total_chars // 4
        est_cost = est_tokens / 1_000_000 * 1.50 + len(requests) * 500 / 1_000_000 * 7.50
        print(f"  Estimated input tokens: ~{est_tokens:,.0f}")
        print(f"  Estimated batch cost: ~${est_cost:.2f}")

        if args.sync:
            results = run_sync(requests)
        else:
            batch_id = submit_batch(requests)
            # Save batch ID so we can resume
            batch_ref = OUTPUT_DIR / "fleet_extract_batch_id.txt"
            batch_ref.write_text(batch_id)
            print(f"  Batch ID saved to {batch_ref}")
            results = poll_batch(batch_id)

    # Process results
    flight_line = process_results(crawl_index, results, requests)

    # Save (merge if requested)
    out_path = OUTPUT_DIR / "flight_line.json"
    if args.merge and out_path.exists():
        with open(out_path) as f:
            existing = json.load(f)
        new_websites = {s["school_website"] for s in flight_line if s.get("school_website")}
        merged = [s for s in existing if s.get("school_website") not in new_websites]
        merged.extend(flight_line)
        flight_line_out = merged
        print(f"\n  Merged {len(flight_line)} new + {len(merged) - len(flight_line)} existing = {len(merged)} total")
    else:
        flight_line_out = flight_line
    with open(out_path, "w") as f:
        json.dump(flight_line_out, f, indent=2)

    # Summary stats (report on newly extracted, not full merged set)
    total_aircraft = sum(len(s["fleet"]) for s in flight_line)
    flagged_aircraft = sum(
        1 for s in flight_line for a in s["fleet"] if a.get("_flags")
    )
    multi_loc = sum(1 for s in flight_line if len(s["locations"]) > 1)
    with_rates = sum(
        1 for s in flight_line for a in s["fleet"] if a.get("wet_rate_hr")
    )
    with_n_numbers = sum(
        1 for s in flight_line for a in s["fleet"] if a.get("n_number")
    )

    print(f"\n  === Results ===")
    print(f"  Schools with fleet:     {len(flight_line):,}")
    print(f"  Total aircraft:         {total_aircraft:,}")
    print(f"  With N-numbers:         {with_n_numbers:,}")
    print(f"  Flagged aircraft:       {flagged_aircraft:,}")
    print(f"  Multi-airport schools:  {multi_loc:,}")
    print(f"  Aircraft with rates:    {with_rates:,}")
    print(f"  Output: {out_path}")

    print(f"\nPhase 9b complete.")


if __name__ == "__main__":
    main()
