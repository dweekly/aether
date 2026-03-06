#!/usr/bin/env python3
"""
11_rate_sheet_extract.py — Download rate sheet PDFs and web pages for schools
with rate_sheet_url but no fleet data, extract text, and run Sonnet extraction.

Usage:
    python 11_rate_sheet_extract.py [--limit N] [--dry-run]
"""
import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import aiohttp
import pdfplumber
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

sys.stdout.reconfigure(line_buffering=True)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
FLIGHT_LINE = OUTPUT_DIR / "flight_line.json"
RATE_TEXT_DIR = OUTPUT_DIR / "rate_sheet_text"
RATE_TEXT_DIR.mkdir(exist_ok=True)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8192
CONCURRENCY = 20
TIMEOUT = 30

EXTRACTION_PROMPT = """\
You are extracting fleet/aircraft data from a flight school's rate sheet or pricing page.

The text below was extracted from a PDF rate sheet or pricing web page for: {school_name}
Website: {website}

Extract ALL aircraft listed. For each aircraft, extract:
- n_number: FAA N-number if shown (e.g. "N12345"), null if not listed
- display_name: Make and model (e.g. "Cessna 172S Skyhawk SP")
- wet_rate_hr: Wet rental rate per hour (fuel included) as a number, null if not listed
- dry_rate_hr: Dry rental rate per hour, null if not listed
- description: Any notable details (year, avionics, engine, etc.)
- equipment: List of notable avionics/equipment items

Also extract:
- school_type: One of "flight_school_with_rentals", "flight_school_no_rentals",
  "flying_club", "fbo", "university_program", "maintenance_shop",
  "charter_operator", "defunct", "other"

Return ONLY valid JSON (no markdown fences):
{{
  "school_type": "...",
  "fleet": [
    {{
      "n_number": "N12345" or null,
      "display_name": "Cessna 172S",
      "wet_rate_hr": 185.0 or null,
      "dry_rate_hr": null,
      "description": "...",
      "equipment": ["G1000", "GFC700"]
    }}
  ]
}}

If no aircraft are found, return {{"school_type": "...", "fleet": []}}.

--- BEGIN TEXT ---
{text}
--- END TEXT ---
"""


async def download_and_extract_text(session: aiohttp.ClientSession, url: str, school_name: str) -> str | None:
    """Download URL content and extract text. Handles PDFs and HTML."""
    safe_name = re.sub(r'[^\w\-.]', '_', school_name)[:80]
    cache_path = RATE_TEXT_DIR / f"{safe_name}.txt"

    if cache_path.exists():
        text = cache_path.read_text()
        if text.strip():
            return text

    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) FlightSchoolResearch/1.0"}
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=TIMEOUT), headers=headers, ssl=False) as resp:
            if resp.status != 200:
                print(f"  [{resp.status}] {school_name}: {url}")
                return None

            content_type = resp.headers.get("Content-Type", "")
            data = await resp.read()

            if ".pdf" in url.lower() or "application/pdf" in content_type:
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(data)
                    tmp_path = tmp.name
                try:
                    text = ""
                    with pdfplumber.open(tmp_path) as pdf:
                        for page in pdf.pages[:20]:
                            page_text = page.extract_text()
                            if page_text:
                                text += page_text + "\n"
                finally:
                    os.unlink(tmp_path)
            else:
                # HTML page — extract text simply
                from bs4 import BeautifulSoup
                html = data.decode("utf-8", errors="replace")
                soup = BeautifulSoup(html, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                text = soup.get_text(separator="\n", strip=True)

            if text.strip():
                cache_path.write_text(text)
                return text
            else:
                print(f"  [empty] {school_name}: {url}")
                return None

    except Exception as e:
        print(f"  [error] {school_name}: {e}")
        return None


async def extract_with_sonnet(text: str, school_name: str, website: str) -> dict | None:
    """Call Sonnet to extract fleet data from rate sheet text."""
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    # Truncate text to ~12K chars to stay within context
    if len(text) > 12000:
        text = text[:12000] + "\n[... truncated ...]"

    prompt = EXTRACTION_PROMPT.format(
        school_name=school_name,
        website=website,
        text=text,
    )

    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```\w*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  [json error] {school_name}: {e}")
        return None
    except Exception as e:
        print(f"  [sonnet error] {school_name}: {e}")
        return None


async def process_school(sem: asyncio.Semaphore, session: aiohttp.ClientSession, school_idx: int, school: dict, flight_line: list) -> dict | None:
    """Process one school: download, extract text, run Sonnet."""
    async with sem:
        name = school.get("school_name", "?")
        url = school.get("rate_sheet_url", "")
        website = school.get("school_website", "")

        text = await download_and_extract_text(session, url, name)
        if not text:
            return None

        chars = len(text)
        result = await extract_with_sonnet(text, name, website)
        if not result:
            return None

        fleet = result.get("fleet", [])
        school_type = result.get("school_type")

        if fleet:
            # Add source_url to each aircraft
            for ac in fleet:
                ac["source_url"] = url

        n_with_tail = sum(1 for ac in fleet if ac.get("n_number"))
        print(f"  {name}: {len(fleet)} aircraft ({n_with_tail} with N-numbers), {chars} chars, type={school_type}")

        return {
            "school_idx": school_idx,
            "fleet": fleet,
            "school_type": school_type,
        }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with open(FLIGHT_LINE) as f:
        flight_line = json.load(f)

    # Find targets: schools with rate_sheet_url but no N-numbers
    targets = []
    for i, school in enumerate(flight_line):
        if not school.get("rate_sheet_url"):
            continue
        fleet = school.get("fleet") or []
        has_n_numbers = any(ac.get("n_number") for ac in fleet)
        if not has_n_numbers:
            targets.append((i, school))

    print(f"Found {len(targets)} schools with rate sheet URLs but no fleet data")

    if args.limit:
        targets = targets[:args.limit]
        print(f"  (limited to {args.limit})")

    if args.dry_run:
        for i, school in targets[:20]:
            print(f"  [{i}] {school.get('school_name','?')}: {school.get('rate_sheet_url','')}")
        return

    sem = asyncio.Semaphore(CONCURRENCY)
    connector = aiohttp.TCPConnector(limit=CONCURRENCY, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            process_school(sem, session, idx, school, flight_line)
            for idx, school in targets
        ]
        results = await asyncio.gather(*tasks)

    # Apply results back to flight_line
    updated = 0
    total_aircraft = 0
    total_n_numbers = 0
    for result in results:
        if not result or not result["fleet"]:
            continue
        idx = result["school_idx"]
        existing_fleet = flight_line[idx].get("fleet") or []
        new_fleet = result["fleet"]

        # If we got N-numbers from the rate sheet that existing fleet lacks,
        # try to match by display_name and enrich; otherwise replace if new has more data
        new_has_tails = any(ac.get("n_number") for ac in new_fleet)
        old_has_tails = any(ac.get("n_number") for ac in existing_fleet)

        if new_has_tails or len(new_fleet) >= len(existing_fleet):
            # Merge: if both have entries, try to enrich existing with N-numbers
            if existing_fleet and new_has_tails and not old_has_tails:
                # Build lookup by normalized display_name
                new_by_name = {}
                for ac in new_fleet:
                    name = (ac.get("display_name") or "").lower().strip()
                    if name:
                        new_by_name.setdefault(name, []).append(ac)
                for ac in existing_fleet:
                    name = (ac.get("display_name") or "").lower().strip()
                    matches = new_by_name.get(name, [])
                    if matches and not ac.get("n_number"):
                        donor = matches.pop(0)
                        if donor.get("n_number"):
                            ac["n_number"] = donor["n_number"]
                        if donor.get("wet_rate_hr") and not ac.get("wet_rate_hr"):
                            ac["wet_rate_hr"] = donor["wet_rate_hr"]
                flight_line[idx]["fleet"] = existing_fleet
            else:
                flight_line[idx]["fleet"] = new_fleet

        if result["school_type"]:
            flight_line[idx]["school_type"] = result["school_type"]
        flight_line[idx]["rate_sheet_extracted"] = True
        updated += 1
        final_fleet = flight_line[idx]["fleet"]
        total_aircraft += len(final_fleet)
        total_n_numbers += sum(1 for ac in final_fleet if ac.get("n_number"))

    print(f"\nResults:")
    print(f"  Schools updated: {updated}/{len(targets)}")
    print(f"  Aircraft found: {total_aircraft}")
    print(f"  With N-numbers: {total_n_numbers}")

    # Save updated flight_line
    with open(FLIGHT_LINE, "w") as f:
        json.dump(flight_line, f, indent=2)
    print(f"  Saved to {FLIGHT_LINE}")


if __name__ == "__main__":
    asyncio.run(main())
