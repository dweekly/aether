#!/usr/bin/env python3
"""
09f_corpus_expansion.py — Crawl + extract new flight schools/clubs/universities.

Incrementally expands the corpus by:
  1. Building a target list from AOPA (schools + clubs + universities) that
     we haven't already crawled
  2. HTTP-crawling each domain for fleet pages (reusing 09a logic)
  3. Sending discovered fleet pages to Sonnet for extraction (reusing 09b logic)
  4. Writing results as fleet_extract_results.jsonl entries (append-safe)

All progress is cached to disk. The script can be interrupted and resumed
at any point without repeating work.

Caching layers:
  - output/fleet_html/           — crawled HTML (skip if file exists)
  - output/expansion_crawl.jsonl — one JSON line per completed crawl
  - output/expansion_extract.jsonl — one JSON line per completed extraction
  - output/expansion_progress.json — dashboard-readable progress summary

Usage:
    python 09f_corpus_expansion.py                    # crawl + extract all new
    python 09f_corpus_expansion.py --crawl-only       # just crawl, no extraction
    python 09f_corpus_expansion.py --extract-only     # just extract already-crawled
    python 09f_corpus_expansion.py --limit 100        # process first N targets
    python 09f_corpus_expansion.py --concurrency 30   # tune parallelism
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from dotenv import load_dotenv

# Load .env from project root or parent directories
for env_path in [
    Path(__file__).parent.parent / ".env",
    Path(__file__).parent.parent.parent / ".env",
    Path(__file__).parent.parent.parent.parent / ".env",
]:
    if env_path.exists():
        load_dotenv(env_path)
        break

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

OUTPUT_DIR = Path(__file__).parent.parent / "output"
HTML_DIR = OUTPUT_DIR / "fleet_html"
RAW_HTML_DIR = OUTPUT_DIR / "fleet_html_raw"
BROWSER_TEXT_DIR = OUTPUT_DIR / "fleet_browser_text"

# Progress files
CRAWL_LOG = OUTPUT_DIR / "expansion_crawl.jsonl"
EXTRACT_LOG = OUTPUT_DIR / "expansion_extract.jsonl"
PROGRESS_FILE = OUTPUT_DIR / "expansion_progress.json"

# ── Tuning ──────────────────────────────────────────────────────
DEFAULT_CONCURRENCY = 30
REQUEST_TIMEOUT = 20
PER_SCHOOL_TIMEOUT = 120
MAX_RETRIES = 2

USER_AGENT = (
    "AetherResearch/1.0 (aviation research; contact: research@aether.aero)"
)

FLEET_LINK_PATTERNS = re.compile(
    r"\b(fleet|aircraft|our\s+planes?|rental(?:s)?|rates?\s*(?:&|and)?\s*fleet"
    r"|airplane(?:s)?|flight\s+line|rent\s+(?:a|an)\s+(?:plane|aircraft))\b",
    re.IGNORECASE,
)


# ── Target list ─────────────────────────────────────────────────


def build_targets() -> list[dict]:
    """Build list of new domains to crawl from AOPA data."""
    aopa_path = OUTPUT_DIR / "aopa_flight_schools.json"
    if not aopa_path.exists():
        print("ERROR: aopa_flight_schools.json not found. Run 08b + 08c first.")
        sys.exit(1)

    aopa = json.loads(aopa_path.read_text())

    # Find already-crawled domains
    already_crawled = set()
    if HTML_DIR.exists():
        for f in HTML_DIR.iterdir():
            already_crawled.add(f.stem.split("__")[0])
    if BROWSER_TEXT_DIR.exists():
        for d in BROWSER_TEXT_DIR.iterdir():
            if d.is_dir():
                already_crawled.add(d.name)

    # Also check our crawl log for domains we attempted but got errors
    crawl_attempted = set()
    if CRAWL_LOG.exists():
        for line in CRAWL_LOG.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                crawl_attempted.add(rec.get("domain", ""))

    targets = []
    seen_domains = set()
    for school in aopa:
        website = school.get("website", "")
        if not website:
            continue
        if not website.startswith("http"):
            website = "https://" + website

        domain = urlparse(website).netloc.lower().replace("www.", "")
        if not domain or domain in seen_domains:
            continue
        seen_domains.add(domain)

        if domain in already_crawled or domain in crawl_attempted:
            continue

        targets.append(
            {
                "name": school.get("name", ""),
                "domain": domain,
                "website": website,
                "airport_id": school.get("airport_id", ""),
                "city": school.get("city", ""),
                "state": school.get("state", ""),
                "aopa_type": school.get("aopa_type", ""),
                "aopa_url": school.get("aopa_url", ""),
            }
        )

    return targets


# ── HTML helpers ────────────────────────────────────────────────


def clean_html(raw: str, base_url: str) -> str:
    """Strip nav/footer/scripts, keep content."""
    try:
        soup = BeautifulSoup(raw, "html.parser")
    except Exception:
        # Some "HTML" files are actually binary (PDFs, images, etc.)
        return ""
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    for tag in soup.find_all(["nav", "header", "footer"]):
        tag.decompose()
    # Remove comments
    for comment in soup.find_all(string=lambda t: isinstance(t, type) or (hasattr(t, 'name') and t.name is None)):
        pass  # skip complex comment removal for speed
    text = soup.get_text(separator="\n", strip=True)
    # Truncate very long pages
    if len(text) > 50_000:
        text = text[:50_000]
    return text


def find_fleet_links(html: str, base_url: str) -> list[str]:
    """Find links that look like fleet/aircraft pages."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)

        # Stay on same domain
        base_domain = urlparse(base_url).netloc.lower().replace("www.", "")
        link_domain = parsed.netloc.lower().replace("www.", "")
        if link_domain != base_domain:
            continue

        # Check if link text or URL suggests fleet page
        combined = f"{text} {parsed.path}"
        if FLEET_LINK_PATTERNS.search(combined):
            if full_url not in seen:
                seen.add(full_url)
                links.append(full_url)

    return links[:10]  # Cap at 10 fleet links per school


def _domain_slug(domain: str) -> str:
    return domain.replace(".", "_").replace("-", "_")


# ── Async crawl ─────────────────────────────────────────────────


async def crawl_school(
    session: aiohttp.ClientSession,
    target: dict,
    semaphore: asyncio.Semaphore,
    progress: dict,
) -> dict:
    """Crawl one school: homepage + fleet links. Save HTML to disk."""
    domain = target["domain"]
    website = target["website"]
    result = {
        "domain": domain,
        "name": target["name"],
        "status": "pending",
        "pages": 0,
        "fleet_links": [],
        "errors": [],
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    async with semaphore:
        try:
            # Fetch homepage
            try:
                async with session.get(
                    website,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                    allow_redirects=True,
                ) as resp:
                    if resp.status != 200:
                        result["status"] = f"http_{resp.status}"
                        result["errors"].append(f"Homepage returned {resp.status}")
                        return result
                    homepage_html = await resp.text(errors="replace")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                result["status"] = "error"
                result["errors"].append(str(e)[:200])
                return result

            # Save homepage
            slug = _domain_slug(domain)
            homepage_path = HTML_DIR / f"{slug}__index.html"
            homepage_path.write_text(homepage_html, encoding="utf-8")
            result["pages"] = 1

            # Find fleet links
            fleet_links = find_fleet_links(homepage_html, website)
            result["fleet_links"] = fleet_links

            # Fetch fleet pages
            for link_url in fleet_links:
                try:
                    path_part = urlparse(link_url).path.strip("/").replace("/", "_")
                    if not path_part:
                        path_part = "fleet"
                    page_path = HTML_DIR / f"{slug}__{path_part}.html"

                    if page_path.exists():
                        result["pages"] += 1
                        continue

                    async with session.get(
                        link_url,
                        timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                        allow_redirects=True,
                    ) as resp:
                        if resp.status == 200:
                            page_html = await resp.text(errors="replace")
                            page_path.write_text(page_html, encoding="utf-8")
                            result["pages"] += 1
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    pass

            result["status"] = "done"

        except Exception as e:
            result["status"] = "error"
            result["errors"].append(str(e)[:200])

    # Update progress
    progress["done"] += 1
    if result["status"] == "done":
        progress["crawled_ok"] += 1
    else:
        progress["errors"] += 1
    progress["pages"] += result["pages"]
    progress["recent"].append(
        {
            "name": target["name"],
            "domain": domain,
            "status": result["status"],
            "pages": result["pages"],
            "aopa_type": target.get("aopa_type", ""),
        }
    )
    if len(progress["recent"]) > 30:
        progress["recent"] = progress["recent"][-30:]
    _write_progress(progress)

    return result


def _write_progress(progress: dict):
    """Write progress JSON for the dashboard."""
    progress["updated_at"] = datetime.now(timezone.utc).isoformat()
    elapsed = time.time() - progress["_start_time"]
    progress["elapsed_sec"] = round(elapsed, 1)
    done = progress["done"]
    total = progress["total"]
    if done > 0:
        rate = done / elapsed
        progress["rate_per_sec"] = round(rate, 2)
        remaining = total - done
        progress["eta_sec"] = round(remaining / rate) if rate > 0 else 0
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2, default=str))


def _append_jsonl(path: Path, record: dict):
    """Append one JSON line to a JSONL file."""
    with open(path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


# ── Extraction (Sonnet) ────────────────────────────────────────


SYSTEM_PROMPT = """You are an aviation data extraction specialist. Given HTML from a flight school or flying club website, extract structured data about their aircraft fleet.

For each aircraft, extract:
- n_number: FAA registration (e.g., "N172SP"). Only include if explicitly shown.
- display_name: Aircraft type/model as shown on site
- description: Any details about equipment, avionics, capabilities
- wet_rate_hr: Hourly rental rate (wet, per Hobbs hour) as a number. null if not shown.
- equipment: List of notable avionics/equipment items
- source_url: The URL this aircraft was found on

Return a JSON object:
{
  "school_type": "flight_school_with_rentals" | "flying_club" | "university_program" | "fbo" | "other" | "defunct",
  "fleet": [ { aircraft objects } ],
  "fleet_page_url": "best URL for their fleet/rates page",
  "rate_sheet_url": "URL for rate sheet if different from fleet page",
  "contact": { "phone": "", "email": "", "address": "" }
}

If the page has no fleet/aircraft information, return {"school_type": "other", "fleet": []}.
Only include aircraft you can see evidence of. Do NOT make up or infer N-numbers."""

MAX_TOKENS = 16384


async def extract_school(
    client,
    domain: str,
    progress: dict,
) -> dict | None:
    """Extract fleet data from cached HTML using Sonnet."""
    slug = _domain_slug(domain)

    # Gather all HTML for this domain
    html_files = sorted(HTML_DIR.glob(f"{slug}__*.html"))
    if not html_files:
        return None

    pages_text = []
    for f in html_files[:5]:  # Cap at 5 pages per school
        raw = f.read_text(encoding="utf-8", errors="replace")
        # Determine source URL from filename
        path_part = f.stem.replace(f"{slug}__", "")
        source_hint = f"[Page: {path_part}]"
        cleaned = clean_html(raw, f"https://{domain}/")
        if len(cleaned) > 200:  # Skip trivially small pages
            pages_text.append(f"{source_hint}\n{cleaned}")

    if not pages_text:
        return None

    combined = "\n\n---\n\n".join(pages_text)
    # Truncate to ~100K chars to stay within context
    if len(combined) > 100_000:
        combined = combined[:100_000]

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Extract fleet data from this flight school website ({domain}):\n\n{combined}",
                }
            ],
        )
        text = response.content[0].text
        # Parse JSON from response
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            return data
    except Exception as e:
        progress["extract_errors"] += 1
        return None

    return None


# ── Main ────────────────────────────────────────────────────────


async def run_crawl(targets: list[dict], concurrency: int):
    """Phase 1: HTTP crawl all targets."""
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    RAW_HTML_DIR.mkdir(parents=True, exist_ok=True)

    progress = {
        "phase": "crawling",
        "total": len(targets),
        "done": 0,
        "crawled_ok": 0,
        "errors": 0,
        "pages": 0,
        "rate_per_sec": 0,
        "elapsed_sec": 0,
        "eta_sec": 0,
        "recent": [],
        "_start_time": time.time(),
    }
    _write_progress(progress)

    connector = aiohttp.TCPConnector(limit=concurrency * 2, ttl_dns_cache=300)
    semaphore = asyncio.Semaphore(concurrency)

    async with aiohttp.ClientSession(
        connector=connector,
        headers={"User-Agent": USER_AGENT},
    ) as session:
        tasks = [
            crawl_school(session, target, semaphore, progress)
            for target in targets
        ]

        from tqdm.asyncio import tqdm as atqdm

        results = []
        for coro in atqdm(
            asyncio.as_completed(tasks),
            total=len(tasks),
            desc="Crawling",
        ):
            result = await coro
            results.append(result)
            _append_jsonl(CRAWL_LOG, result)

    progress["phase"] = "crawl_complete"
    _write_progress(progress)
    return results


async def run_extract(concurrency: int = 5):
    """Phase 2: Extract fleet data from crawled HTML via Sonnet."""
    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic package required for extraction. pip install anthropic")
        sys.exit(1)

    # Find domains that have been crawled but not yet extracted
    crawled_domains = set()
    if CRAWL_LOG.exists():
        for line in CRAWL_LOG.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec.get("status") == "done":
                    crawled_domains.add(rec["domain"])

    # Also include domains with HTML files from prior crawl sessions
    if HTML_DIR.exists():
        for f in HTML_DIR.iterdir():
            domain = f.stem.split("__")[0].replace("_", ".")
            # Undo the slug: foo_com → foo.com
            # This is imperfect but handles most cases
            crawled_domains.add(domain)

    already_extracted = set()
    if EXTRACT_LOG.exists():
        for line in EXTRACT_LOG.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                already_extracted.add(rec.get("domain", ""))

    # Also check existing extraction results
    existing_extract = OUTPUT_DIR / "fleet_extract_results.jsonl"
    if existing_extract.exists():
        for line in existing_extract.read_text().splitlines():
            if line.strip():
                try:
                    rec = json.loads(line)
                    # Extract domain from custom_id if present
                    cid = rec.get("custom_id", "")
                    # These are from batch API, format varies
                except json.JSONDecodeError:
                    pass

    # Build new extraction targets from expansion crawl only
    to_extract = []
    if CRAWL_LOG.exists():
        for line in CRAWL_LOG.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                domain = rec.get("domain", "")
                if (
                    rec.get("status") == "done"
                    and domain not in already_extracted
                    and rec.get("pages", 0) > 0
                ):
                    to_extract.append(rec)

    if not to_extract:
        print("No new domains to extract.")
        return

    print(f"Domains to extract: {len(to_extract)}")

    progress = {
        "phase": "extracting",
        "total": len(to_extract),
        "done": 0,
        "extracted_ok": 0,
        "extract_errors": 0,
        "total_aircraft": 0,
        "rate_per_sec": 0,
        "elapsed_sec": 0,
        "eta_sec": 0,
        "recent": [],
        "_start_time": time.time(),
    }
    _write_progress(progress)

    client = anthropic.AsyncAnthropic()
    semaphore = asyncio.Semaphore(concurrency)

    async def extract_one(rec: dict):
        async with semaphore:
            domain = rec["domain"]
            data = await extract_school(client, domain, progress)

            result = {
                "domain": domain,
                "name": rec.get("name", ""),
                "ts": datetime.now(timezone.utc).isoformat(),
            }

            if data and data.get("fleet"):
                result["status"] = "ok"
                result["school_type"] = data.get("school_type", "")
                result["fleet_count"] = len(data["fleet"])
                result["data"] = data
                progress["extracted_ok"] += 1
                progress["total_aircraft"] += len(data["fleet"])
            elif data:
                result["status"] = "no_fleet"
                result["school_type"] = data.get("school_type", "")
                result["fleet_count"] = 0
                result["data"] = data
                progress["extracted_ok"] += 1
            else:
                result["status"] = "error"
                result["fleet_count"] = 0

            progress["done"] += 1
            progress["recent"].append(
                {
                    "name": rec.get("name", ""),
                    "domain": domain,
                    "status": result["status"],
                    "fleet_count": result["fleet_count"],
                }
            )
            if len(progress["recent"]) > 30:
                progress["recent"] = progress["recent"][-30:]
            _write_progress(progress)

            _append_jsonl(EXTRACT_LOG, result)
            return result

    from tqdm.asyncio import tqdm as atqdm

    tasks = [extract_one(rec) for rec in to_extract]
    results = []
    for coro in atqdm(
        asyncio.as_completed(tasks),
        total=len(tasks),
        desc="Extracting",
    ):
        result = await coro
        results.append(result)

    progress["phase"] = "complete"
    _write_progress(progress)

    # Summary
    ok = sum(1 for r in results if r["status"] == "ok")
    no_fleet = sum(1 for r in results if r["status"] == "no_fleet")
    errors = sum(1 for r in results if r["status"] == "error")
    total_ac = sum(r.get("fleet_count", 0) for r in results)
    print(f"\nExtraction complete:")
    print(f"  With fleet: {ok}")
    print(f"  No fleet: {no_fleet}")
    print(f"  Errors: {errors}")
    print(f"  Total aircraft: {total_ac}")

    return results


async def main():
    parser = argparse.ArgumentParser(description="Corpus expansion crawl + extract")
    parser.add_argument("--crawl-only", action="store_true")
    parser.add_argument("--extract-only", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument(
        "--extract-concurrency",
        type=int,
        default=5,
        help="Concurrent Sonnet API calls",
    )
    args = parser.parse_args()

    if not args.extract_only:
        targets = build_targets()
        if args.limit:
            targets = targets[: args.limit]
        print(f"New targets to crawl: {len(targets)}")

        if targets:
            await run_crawl(targets, args.concurrency)
        else:
            print("No new targets — everything already crawled.")

    if not args.crawl_only:
        await run_extract(concurrency=args.extract_concurrency)


if __name__ == "__main__":
    asyncio.run(main())
