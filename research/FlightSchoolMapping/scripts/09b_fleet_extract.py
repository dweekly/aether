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

import anthropic

OUTPUT_DIR = Path(__file__).parent.parent / "output"
HTML_DIR = OUTPUT_DIR / "fleet_html"

MODEL = "claude-sonnet-4-20250514"
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
You are an aviation data extraction specialist. You extract structured \
information about flight school aircraft fleets from their website HTML.

IMPORTANT RULES:
- Only extract aircraft that the school actually operates, rents out, or \
uses for training. Do NOT invent or hallucinate aircraft.
- N-numbers are FAA tail numbers in the format N followed by 1-5 digits \
and 0-2 letters (e.g., N172SP, N12345, N88PK). They uniquely identify \
US-registered aircraft.
- If you cannot find N-numbers on the page, still extract aircraft types \
and other details — just leave n_number as empty string.
- Airport codes should be 3-4 letter ICAO codes (e.g., KSQL, KPAO). \
For 3-letter FAA codes, prepend K (e.g., SQL → KSQL).
- Wet rates are per-hour rental rates that include fuel.
- If information is not clearly stated on the page, leave the field empty \
rather than guessing.
- Return ONLY the JSON object. No markdown, no explanation."""

USER_PROMPT_TEMPLATE = """\
Extract the fleet and location information for {school_name} from the \
following web pages.

Known information:
- School website: {school_website}
- Known airport: {known_airport}
- City/State: {city_state}

Return a JSON object with this exact schema:
{{
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
      "airport_id": "KXXX"
    }}
  ]
}}

Fields:
- locations[].airport_id: ICAO code (4 letters starting with K for US)
- fleet[].n_number: FAA N-number if visible, else ""
- fleet[].display_name: aircraft make/model (e.g. "Cessna 172S Skyhawk")
- fleet[].description: brief description of equipment/features
- fleet[].wet_rate_hr: hourly rental rate including fuel (number), or null
- fleet[].equipment: list of notable avionics/features
- fleet[].airport_id: which airport this aircraft is based at, if known

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


def _validate_and_flag(school_entry: dict, school_meta: dict) -> dict:
    """Validate extracted data and flag suspicious entries."""
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
            # Check format: N + 1-5 digits + 0-2 letters
            import re
            if not re.match(r"^N\d{1,5}[A-Z]{0,2}$", nn):
                flags.append("invalid_n_number_format")
            elif faa_numbers and nn not in faa_numbers:
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

        # Find the best fleet page URL
        fleet_page_url = ""
        for page in school_meta.get("pages", []):
            if page["type"] == "fleet":
                fleet_page_url = page["url"]
                break

        entry = {
            "school_name": school_meta["school_name"],
            "school_website": school_meta["school_website"],
            "locations": parsed.get("locations", []),
            "fleet": parsed.get("fleet", []),
            "fleet_page_url": fleet_page_url,
            "scrape_ts": school_meta.get("crawl_ts", ""),
        }

        if entry["fleet"] or entry["locations"]:
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
        batch_requests.append(
            anthropic.types.messages.batch_create_params.Request(
                custom_id=req["custom_id"],
                params=anthropic.types.MessageCreateParamsNonStreaming(
                    model=req["params"]["model"],
                    max_tokens=req["params"]["max_tokens"],
                    system=req["params"]["system"],
                    messages=req["params"]["messages"],
                ),
            )
        )

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
    args = parser.parse_args()

    print("=" * 60)
    print("Phase 9b: Fleet Extraction via Sonnet")
    print("=" * 60)

    # Load crawl index
    index_path = OUTPUT_DIR / "fleet_crawl_index.json"
    if not index_path.exists():
        print("  Error: Run 09a_fleet_crawl.py first.")
        return
    with open(index_path) as f:
        crawl_index = json.load(f)

    print(f"  Schools in crawl index: {len(crawl_index):,}")

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

    # Save
    out_path = OUTPUT_DIR / "flight_line.json"
    with open(out_path, "w") as f:
        json.dump(flight_line, f, indent=2)

    # Summary stats
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
