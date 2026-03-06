#!/usr/bin/env python3
"""
10_fleet_browser_crawl.py - Browser-based fleet scrape for JS-rendered sites.

Uses Playwright to render JavaScript-heavy flight school websites that returned
thin/empty content during the HTTP crawl (09a), then feeds rendered text to
Sonnet 4.6 for structured fleet extraction.

Lessons from manual Chrome DevTools testing (e.g. JATO Aviation / Squarespace):
- a11y snapshots give excellent structured content from JS-rendered pages
- Fleet listing pages link to individual aircraft detail pages
- PDF rate sheets (linked from rental pages) are authoritative pricing sources
- Contact info lives in footers; airport assignments in category tags
- Multiple fleet-related pages per school capture different data

Pipeline:
  1. Playwright renders homepage -> extracts text + discovers links
  2. Follows fleet/rental/aircraft links -> extracts text from each
  3. Follows aircraft detail links found on fleet pages
  4. Detects and downloads PDF rate sheets -> extracts text via pdfplumber
  5. Sends all collected text to Sonnet 4.6 for structured extraction
  6. Merges results into flight_line.json

Usage:
    python 10_fleet_browser_crawl.py --limit 5           # test with 5 schools
    python 10_fleet_browser_crawl.py --school "JATO"     # single school
    python 10_fleet_browser_crawl.py --batch             # all flagged, batch API
    python 10_fleet_browser_crawl.py --resume            # resume interrupted run
    python 10_fleet_browser_crawl.py --concurrency 10    # parallel browsers
    python 10_fleet_browser_crawl.py --crawl-only        # crawl without extraction
    python 10_fleet_browser_crawl.py --extract-only --batch  # extract from cache
"""

import argparse
import asyncio
import hashlib
import io
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

# Force unbuffered output for background/piped runs
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

import anthropic
from playwright.async_api import async_playwright, Page, BrowserContext

# ── Paths ────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR.parent / "output"
CRAWL_INDEX = OUTPUT_DIR / "fleet_crawl_index.json"
BROWSER_INDEX = OUTPUT_DIR / "fleet_browser_index.json"
BROWSER_TEXT_DIR = OUTPUT_DIR / "fleet_browser_text"
FLIGHT_LINE = OUTPUT_DIR / "flight_line.json"

# ── Config ───────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 16384
PAGE_TIMEOUT = 10_000        # 10s per page load
SCHOOL_TIMEOUT = 45          # 45s max per school
NAV_DELAY = 0.3              # seconds between navigations (politeness)
MAX_FLEET_PAGES = 5          # max fleet pages to visit per school
MAX_DETAIL_PAGES = 3         # max aircraft detail pages per school
MAX_PDFS = 3                 # max PDFs to download per school
SAVE_EVERY = 25              # save index every N schools

# ── Link discovery patterns ──────────────────────────────────────

FLEET_LINK_RE = re.compile(
    r'fleet|aircraft|airplane|planes?\b|rental|rates?|pricing|schedule'
    r'|our.?(?:planes?|aircraft|fleet)|club.?(?:planes?|aircraft)'
    r'|what.?we.?fly|train|learn.?to.?fly|discovery.?flight'
    r'|membership|about.?(?:the|our|us)'
    r'|cessna|piper|cirrus|beech|diamond|mooney|bonanza|skylane|skyhawk'
    r'|warrior|archer|seminole|cherokee'
    r'|172|152|182|206|sr20|sr22|da[24]0|pa-?(?:28|44)',
    re.IGNORECASE,
)

NAV_JUNK_RE = re.compile(
    r'privacy|terms|cookie|login|sign.?in|cart|checkout|apply|enroll'
    r'|admission|financial.?aid|tuition|career|job|blog(?!.*fleet)'
    r'|facebook|instagram|youtube|twitter|linkedin|tiktok',
    re.IGNORECASE,
)

PDF_RE = re.compile(r'\.pdf(?:\?.*)?$', re.IGNORECASE)

RATE_PDF_RE = re.compile(
    r'rate|price|pricing|rental|fleet|aircraft|schedule|cost',
    re.IGNORECASE,
)


# ── Sonnet extraction prompt ────────────────────────────────────

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


# ── Disk cache for rendered text ─────────────────────────────────

def _domain_dir(url: str) -> str:
    return urlparse(url).netloc.replace('www.', '') or 'unknown'


def _page_filename(url: str) -> str:
    """Deterministic filename for a page URL."""
    slug = urlparse(url).path.strip('/').replace('/', '_') or 'index'
    h = hashlib.md5(url.encode()).hexdigest()[:8]
    return f"{slug}_{h}.txt"


def save_page_text(school_url: str, page_url: str, text: str) -> str:
    """Save rendered page text to disk. Returns relative path."""
    domain = _domain_dir(school_url)
    d = BROWSER_TEXT_DIR / domain
    d.mkdir(parents=True, exist_ok=True)
    fname = _page_filename(page_url)
    path = d / fname
    path.write_text(text, encoding='utf-8')
    return f"{domain}/{fname}"


def load_page_text(rel_path: str) -> str:
    """Load rendered page text from disk cache."""
    full = BROWSER_TEXT_DIR / rel_path
    if full.exists():
        return full.read_text(encoding='utf-8', errors='replace')
    return ""


def save_pdf_text(school_url: str, pdf_url: str, text: str) -> str:
    """Save extracted PDF text to disk. Returns relative path."""
    domain = _domain_dir(school_url)
    d = BROWSER_TEXT_DIR / domain
    d.mkdir(parents=True, exist_ok=True)
    h = hashlib.md5(pdf_url.encode()).hexdigest()[:8]
    fname = f"pdf_{h}.txt"
    path = d / fname
    path.write_text(text, encoding='utf-8')
    return f"{domain}/{fname}"


# ── Page text extraction ─────────────────────────────────────────

async def get_page_text(page: Page) -> str:
    """Extract structured text content from a rendered page."""
    text = await page.evaluate("""() => {
        function getText(el) {
            if (!el) return '';
            const tag = el.tagName;
            const skip = ['SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG', 'IFRAME'];
            if (skip.includes(tag)) return '';
            let result = '';
            if (['H1','H2','H3','H4','H5','H6'].includes(tag))
                result += '\\n## ';
            else if (['P','DIV','SECTION','ARTICLE','LI','TR'].includes(tag))
                result += '\\n';
            else if (tag === 'BR')
                return '\\n';
            for (const child of el.childNodes) {
                if (child.nodeType === 3)
                    result += child.textContent;
                else if (child.nodeType === 1)
                    result += getText(child);
            }
            if (['H1','H2','H3','H4','H5','H6','P','TR'].includes(tag))
                result += '\\n';
            return result;
        }
        return getText(document.body);
    }""")
    lines = [line.strip() for line in text.split('\n')]
    return '\n'.join(line for line in lines if line)


async def extract_links(page: Page, base_url: str) -> list[dict]:
    """Extract all same-domain links from the rendered page."""
    links = await page.evaluate("""() => {
        return Array.from(document.querySelectorAll('a[href]')).map(a => ({
            href: a.href,
            text: (a.textContent || '').trim().substring(0, 200),
        }));
    }""")
    base_domain = urlparse(base_url).netloc.replace('www.', '')
    seen = set()
    result = []
    for link in links:
        href = link['href'].split('#')[0].rstrip('/')
        if not href or href in seen:
            continue
        parsed = urlparse(href)
        link_domain = parsed.netloc.replace('www.', '')
        # Allow same-domain links AND absolute PDF URLs on CDNs
        if link_domain and link_domain != base_domain and not PDF_RE.search(href):
            continue
        seen.add(href)
        result.append({'url': href, 'text': link['text']})
    return result


def classify_links(links: list[dict]) -> tuple[list[str], list[dict]]:
    """Split links into fleet page URLs and PDF descriptors."""
    fleet_urls = []
    pdfs = []
    for link in links:
        url, text = link['url'], link['text']
        if NAV_JUNK_RE.search(text) or NAV_JUNK_RE.search(url):
            continue
        if PDF_RE.search(url):
            pdfs.append({
                'url': url,
                'text': text,
                'is_rate_sheet': bool(
                    RATE_PDF_RE.search(text) or RATE_PDF_RE.search(url)
                ),
            })
            continue
        path = urlparse(url).path.lower()
        if FLEET_LINK_RE.search(text) or FLEET_LINK_RE.search(path):
            fleet_urls.append(url)
    return fleet_urls, pdfs


# ── PDF text extraction ──────────────────────────────────────────

async def download_pdf_text(page: Page, url: str) -> str:
    """Download a PDF and extract text with pdfplumber."""
    try:
        import pdfplumber
    except ImportError:
        return ""
    try:
        response = await page.context.request.get(url, timeout=15_000)
        if response.status != 200:
            return ""
        pdf_bytes = await response.body()
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            texts = []
            for p in pdf.pages[:10]:
                t = p.extract_text()
                if t:
                    texts.append(t)
            return '\n\n'.join(texts)
    except Exception:
        return ""


# ── Per-school browser crawl ─────────────────────────────────────

async def crawl_school_browser(context: BrowserContext, school: dict) -> dict:
    """Render a school's site and collect all text content."""
    website = school['school_website']
    name = school['school_name']
    result = {
        'school_name': name,
        'school_website': website,
        'known_airport': school.get('known_airport', ''),
        'city': school.get('city', ''),
        'state': school.get('state', ''),
        'pages': [],        # [{url, type, text_path, char_count}]
        'pdfs': [],         # [{url, text, is_rate_sheet, text_path, char_count}]
        'crawl_flags': [],
    }

    page = await context.new_page()
    try:
        # --- Homepage ---
        try:
            await page.goto(website, wait_until='domcontentloaded', timeout=PAGE_TIMEOUT)
            await page.wait_for_timeout(2000)
        except Exception:
            try:
                await page.goto(website, wait_until='commit', timeout=PAGE_TIMEOUT)
                await page.wait_for_timeout(3000)
            except Exception:
                result['crawl_flags'].append('homepage_failed')
                return result

        home_text = await get_page_text(page)
        text_path = save_page_text(website, website, home_text)
        home_links = await extract_links(page, website)
        result['pages'].append({
            'url': website,
            'type': 'home',
            'text_path': text_path,
            'char_count': len(home_text),
        })

        fleet_urls, pdfs = classify_links(home_links)
        all_pdfs = list(pdfs)

        # --- Fleet pages ---
        visited = {website.rstrip('/')}
        deeper_fleet_urls = []

        for url in fleet_urls[:MAX_FLEET_PAGES]:
            norm = url.rstrip('/')
            if norm in visited:
                continue
            visited.add(norm)
            await asyncio.sleep(NAV_DELAY)

            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=PAGE_TIMEOUT)
                await page.wait_for_timeout(1500)
            except Exception:
                try:
                    await page.goto(url, wait_until='commit', timeout=PAGE_TIMEOUT)
                    await page.wait_for_timeout(2000)
                except Exception:
                    continue

            page_text = await get_page_text(page)
            tp = save_page_text(website, url, page_text)
            page_links = await extract_links(page, website)
            result['pages'].append({
                'url': url,
                'type': 'fleet',
                'text_path': tp,
                'char_count': len(page_text),
            })

            sub_fleet, sub_pdfs = classify_links(page_links)
            deeper_fleet_urls.extend(sub_fleet)
            all_pdfs.extend(sub_pdfs)

        # --- Aircraft detail pages ---
        for url in deeper_fleet_urls[:MAX_DETAIL_PAGES]:
            norm = url.rstrip('/')
            if norm in visited:
                continue
            visited.add(norm)
            await asyncio.sleep(NAV_DELAY)

            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=PAGE_TIMEOUT)
                await page.wait_for_timeout(1500)
            except Exception:
                try:
                    await page.goto(url, wait_until='commit', timeout=PAGE_TIMEOUT)
                    await page.wait_for_timeout(2000)
                except Exception:
                    continue

            page_text = await get_page_text(page)
            tp = save_page_text(website, url, page_text)
            page_links = await extract_links(page, website)
            result['pages'].append({
                'url': url,
                'type': 'detail',
                'text_path': tp,
                'char_count': len(page_text),
            })

            _, sub_pdfs = classify_links(page_links)
            all_pdfs.extend(sub_pdfs)

        # --- Dedupe and download PDFs ---
        seen_pdf_urls = set()
        unique_pdfs = []
        for pdf in all_pdfs:
            if pdf['url'] not in seen_pdf_urls:
                seen_pdf_urls.add(pdf['url'])
                unique_pdfs.append(pdf)
        unique_pdfs.sort(key=lambda p: (not p['is_rate_sheet'], p['url']))

        for pdf in unique_pdfs[:MAX_PDFS]:
            pdf_text = await download_pdf_text(page, pdf['url'])
            if pdf_text:
                tp = save_pdf_text(website, pdf['url'], pdf_text)
                pdf['text_path'] = tp
            pdf['char_count'] = len(pdf_text)
        result['pdfs'] = unique_pdfs[:MAX_PDFS]

        # --- Quality flags ---
        fleet_pages = [p for p in result['pages'] if p['type'] in ('fleet', 'detail')]
        total_chars = sum(p['char_count'] for p in result['pages'])
        if not fleet_pages:
            result['crawl_flags'].append('no_fleet_pages')
        if total_chars < 200:
            result['crawl_flags'].append('all_pages_thin')

    except Exception as e:
        result['crawl_flags'].append(f'error:{str(e)[:80]}')
    finally:
        await page.close()

    return result


# ── Build Sonnet content from cached text ────────────────────────

def build_pages_content(crawl_result: dict) -> str:
    """Build the text block for Sonnet from cached page + PDF text."""
    parts = []
    for pg in crawl_result['pages']:
        text = load_page_text(pg['text_path']) if pg.get('text_path') else ""
        if not text:
            continue
        if len(text) > 40_000:
            text = text[:40_000] + '\n[... truncated ...]'
        parts.append(f"--- Page: {pg['url']} (type: {pg['type']}) ---\n{text}")

    for pdf in crawl_result.get('pdfs', []):
        pdf_text = load_page_text(pdf['text_path']) if pdf.get('text_path') else ""
        if not pdf_text:
            continue
        label = "rate sheet PDF" if pdf.get('is_rate_sheet') else "PDF"
        parts.append(f"--- {label}: {pdf['url']} ---\n{pdf_text}")

    return '\n\n'.join(parts)


def build_user_prompt(school: dict, pages_content: str) -> str:
    return USER_PROMPT_TEMPLATE.format(
        school_name=school.get('school_name', 'Unknown'),
        school_website=school.get('school_website', ''),
        known_airport=school.get('known_airport', ''),
        city_state=f"{school.get('city', '')}, {school.get('state', '')}",
        pages_content=pages_content,
    )


# ── Sonnet extraction ────────────────────────────────────────────

def _parse_sonnet_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1] if '\n' in text else text[3:]
    if text.endswith('```'):
        text = text[:-3]
    if text.startswith('json'):
        text = text[4:]
    return json.loads(text.strip())


def extract_sync(client: anthropic.Anthropic, school: dict, pages_content: str) -> dict | None:
    if not pages_content.strip():
        return None
    user_prompt = build_user_prompt(school, pages_content)
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return _parse_sonnet_json(resp.content[0].text)
    except Exception as e:
        print(f"    Sonnet error: {e}")
        # Log extraction error to progress
        for ev in _events:
            if ev.get("url") == school.get("school_website") and ev.get("status") in ("done", "flagged"):
                ev["extract_error"] = str(e)[:120]
                break
        _save_progress(_events)
        return None


def build_batch_request(school: dict, pages_content: str, custom_id: str) -> dict | None:
    if not pages_content.strip():
        return None
    user_prompt = build_user_prompt(school, pages_content)
    return {
        "custom_id": custom_id,
        "params": {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_prompt}],
        },
    }


# ── Merge into flight_line.json ──────────────────────────────────

def merge_extraction(school: dict, extraction: dict, crawl_result: dict) -> dict:
    fleet_page_url = ""
    for pg in crawl_result.get('pages', []):
        if pg['type'] == 'fleet':
            fleet_page_url = pg['url']
            break

    return {
        "school_name": school.get("school_name", ""),
        "school_website": school.get("school_website", ""),
        "school_type": extraction.get("school_type", ""),
        "fleet_page_url": fleet_page_url,
        "rate_sheet_url": extraction.get("rate_sheet_url", ""),
        "contact": extraction.get("contact", {"phone": "", "email": "", "address": ""}),
        "locations": extraction.get("locations", []),
        "fleet": extraction.get("fleet", []),
        "scrape_ts": datetime.now(timezone.utc).isoformat(),
        "source": "browser_crawl",
    }


# ── Batch polling ────────────────────────────────────────────────

def poll_batch(client: anthropic.Anthropic, batch_id: str) -> list:
    print(f"Polling batch {batch_id}...")
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        total = counts.processing + counts.succeeded + counts.errored + counts.canceled + counts.expired
        print(f"  {counts.succeeded}/{total} succeeded, "
              f"{counts.processing} processing, {counts.errored} errored")
        if batch.processing_status == "ended":
            break
        time.sleep(30)
    return list(client.messages.batches.results(batch_id))


# ── Parallel crawl worker ────────────────────────────────────────

_progress_lock = asyncio.Lock()
_progress = {"done": 0, "total": 0}
PROGRESS_FILE = OUTPUT_DIR / "fleet_browser_progress.json"


def _save_progress(events: list[dict]):
    """Write progress state to a JSON file for the dashboard."""
    summary = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total": _progress["total"],
        "done": _progress["done"],
        "schools": events[-200:],  # last 200 events
    }
    try:
        PROGRESS_FILE.write_text(json.dumps(summary), encoding="utf-8")
    except Exception:
        pass


_events: list[dict] = []






# ── Main ─────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Browser-based fleet scrape")
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--school', type=str, default='')
    parser.add_argument('--batch', action='store_true', help='Use batch API (50%% cheaper)')
    parser.add_argument('--poll', type=str, default='', help='Poll existing batch ID')
    parser.add_argument('--resume', action='store_true', help='Skip already-crawled')
    parser.add_argument('--crawl-only', action='store_true', help='Crawl without extraction')
    parser.add_argument('--extract-only', action='store_true', help='Extract from cache')
    parser.add_argument('--concurrency', type=int, default=1,
                        help='Parallel browser tabs (default 1)')
    args = parser.parse_args()

    client = anthropic.Anthropic()

    # --- Poll mode ---
    if args.poll:
        results = poll_batch(client, args.poll)
        if not BROWSER_INDEX.exists():
            print("No browser index found")
            return
        with open(BROWSER_INDEX) as f:
            browser_data = json.load(f)
        school_by_id = {f"browser_{i:04d}": entry for i, entry in enumerate(browser_data)}

        existing_fl = {}
        if FLIGHT_LINE.exists():
            with open(FLIGHT_LINE) as f:
                for entry in json.load(f):
                    existing_fl[entry['school_website']] = entry

        extracted = 0
        for r in results:
            if r.result.type != "succeeded":
                continue
            try:
                extraction = _parse_sonnet_json(r.result.message.content[0].text)
            except Exception:
                continue
            school_data = school_by_id.get(r.custom_id, {})
            merged = merge_extraction(school_data, extraction, school_data)
            if merged.get('fleet') or merged.get('contact', {}).get('phone') or merged.get('school_type'):
                existing_fl[merged['school_website']] = merged
                extracted += 1

        with open(FLIGHT_LINE, 'w') as f:
            json.dump(list(existing_fl.values()), f, indent=2)
        print(f"Extracted {extracted} schools, total {len(existing_fl)} in flight_line.json")
        return

    # --- Load targets ---
    with open(CRAWL_INDEX) as f:
        all_schools = json.load(f)

    if args.school:
        targets = [s for s in all_schools
                    if args.school.lower() in s['school_name'].lower()]
    else:
        targets = [s for s in all_schools if s.get('crawl_flags')]

    # Resume support: skip schools already in browser index
    existing_browser = {}
    if args.resume and BROWSER_INDEX.exists():
        with open(BROWSER_INDEX) as f:
            for entry in json.load(f):
                existing_browser[entry['school_website']] = entry
        before = len(targets)
        targets = [s for s in targets if s['school_website'] not in existing_browser]
        print(f"Resume: skipping {before - len(targets)} already crawled")

    if args.limit:
        targets = targets[:args.limit]

    # --- Extract-only mode ---
    if args.extract_only:
        if not BROWSER_INDEX.exists():
            print("No browser index found")
            return
        with open(BROWSER_INDEX) as f:
            browser_data = json.load(f)
        if args.school:
            browser_data = [b for b in browser_data
                           if args.school.lower() in b['school_name'].lower()]
        if args.limit:
            browser_data = browser_data[:args.limit]

        if args.batch:
            batch_requests = []
            for i, cr in enumerate(browser_data):
                pc = build_pages_content(cr)
                req = build_batch_request(cr, pc, f"browser_{i:04d}")
                if req:
                    batch_requests.append(req)
            print(f"Submitting {len(batch_requests)} batch requests...")
            batch = client.messages.batches.create(requests=batch_requests)
            print(f"Batch ID: {batch.id}")
            print(f"Poll: python {Path(__file__).name} --poll {batch.id}")
        else:
            existing_fl = {}
            if FLIGHT_LINE.exists():
                with open(FLIGHT_LINE) as f:
                    for entry in json.load(f):
                        existing_fl[entry['school_website']] = entry
            extracted = 0
            for i, cr in enumerate(browser_data):
                pc = build_pages_content(cr)
                if not pc.strip():
                    continue
                print(f"[{i+1}/{len(browser_data)}] {cr['school_name']}")
                ext = extract_sync(client, cr, pc)
                if ext:
                    merged = merge_extraction(cr, ext, cr)
                    if merged.get('fleet') or merged.get('contact', {}).get('phone') or merged.get('school_type'):
                        existing_fl[merged['school_website']] = merged
                        extracted += 1
                        print(f"    -> {len(merged.get('fleet', []))} aircraft")
            with open(FLIGHT_LINE, 'w') as f:
                json.dump(list(existing_fl.values()), f, indent=2)
            print(f"\nExtracted {extracted}, total {len(existing_fl)} in flight_line.json")
        return

    # --- Browser crawl ---
    BROWSER_TEXT_DIR.mkdir(parents=True, exist_ok=True)
    browser_results = list(existing_browser.values())
    concurrency = max(1, args.concurrency)

    print(f"Crawling {len(targets)} schools, concurrency={concurrency}")

    # Split targets into N chunks, one per browser process
    n_workers = concurrency
    chunks = [[] for _ in range(n_workers)]
    for i, school in enumerate(targets):
        chunks[i % n_workers].append(school)

    _progress["total"] = len(targets)
    _progress["done"] = 0

    print(f"Launching {n_workers} browser workers...")

    new_results: list[dict] = []
    results_lock = asyncio.Lock()

    async def browser_worker(worker_id: int, schools: list[dict]):
        """Each worker gets its own browser process."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                ),
                viewport={'width': 1280, 'height': 720},
            )
            for school in schools:
                name = school['school_name']
                url = school['school_website']

                async with _progress_lock:
                    _progress["done"] += 1
                    idx = _progress["done"]
                    total = _progress["total"]
                print(f"  [{idx}/{total}] W{worker_id} START {name}")
                _events.append({
                    "school": name, "url": url, "status": "crawling",
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
                _save_progress(_events)

                try:
                    crawl_result = await asyncio.wait_for(
                        crawl_school_browser(context, school),
                        timeout=SCHOOL_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    crawl_result = {
                        'school_name': name, 'school_website': url,
                        'known_airport': school.get('known_airport', ''),
                        'city': school.get('city', ''), 'state': school.get('state', ''),
                        'pages': [], 'pdfs': [], 'crawl_flags': ['timeout'],
                    }
                except Exception as e:
                    crawl_result = {
                        'school_name': name, 'school_website': url,
                        'known_airport': school.get('known_airport', ''),
                        'city': school.get('city', ''), 'state': school.get('state', ''),
                        'pages': [], 'pdfs': [], 'crawl_flags': [f'error:{str(e)[:80]}'],
                    }

                n_pages = len(crawl_result['pages'])
                total_chars = sum(p_['char_count'] for p_ in crawl_result['pages'])
                total_chars += sum(p_.get('char_count', 0) for p_ in crawl_result.get('pdfs', []))
                n_pdfs = len(crawl_result['pdfs'])
                flags = crawl_result.get('crawl_flags', [])

                detail = f"{n_pages} pgs, {total_chars:,} chars"
                if n_pdfs:
                    detail += f", {n_pdfs} PDFs"
                if flags:
                    detail += f", flags={flags}"
                print(f"  [{idx}/{total}] W{worker_id} DONE  {name} -> {detail}")

                # Update progress
                page_links = [
                    {"url": p_["url"], "type": p_["type"], "chars": p_["char_count"]}
                    for p_ in crawl_result.get("pages", [])
                ]
                pdf_links = [
                    {"url": p_["url"], "text": p_.get("text", ""), "rate_sheet": p_.get("is_rate_sheet", False)}
                    for p_ in crawl_result.get("pdfs", [])
                ]
                for ev in _events:
                    if ev["url"] == url and ev["status"] == "crawling":
                        ev["status"] = "done" if not flags else "flagged"
                        ev["pages"] = n_pages
                        ev["chars"] = total_chars
                        ev["pdfs"] = n_pdfs
                        ev["flags"] = flags
                        ev["page_links"] = page_links
                        ev["pdf_links"] = pdf_links
                        break
                _save_progress(_events)

                async with results_lock:
                    new_results.append(crawl_result)

            await context.close()
            await browser.close()

    # Run all workers concurrently — each is a separate browser process
    await asyncio.gather(
        *[browser_worker(i, chunk) for i, chunk in enumerate(chunks)],
        return_exceptions=True,
    )

    browser_results = list(existing_browser.values()) + new_results

    # Mark any lingering "crawling" as done in progress
    for ev in _events:
        if ev["status"] == "crawling":
            ev["status"] = "done"
            ev["pages"] = 0
            ev["chars"] = 0
    _save_progress(_events)

    # Final save of browser index
    with open(BROWSER_INDEX, 'w') as f:
        json.dump(browser_results, f, indent=2)

    # --- Extraction phase ---
    if not args.crawl_only:
        existing_fl = {}
        if FLIGHT_LINE.exists():
            with open(FLIGHT_LINE) as f:
                for entry in json.load(f):
                    existing_fl[entry['school_website']] = entry

        # Filter to schools that have content
        to_extract = [r for r in new_results
                      if sum(p['char_count'] for p in r['pages']) > 200]

        if args.batch and to_extract:
            batch_requests = []
            # Renumber from full browser_results for stable IDs
            idx_map = {r['school_website']: i for i, r in enumerate(browser_results)}
            for cr in to_extract:
                idx = idx_map.get(cr['school_website'], 0)
                pc = build_pages_content(cr)
                req = build_batch_request(cr, pc, f"browser_{idx:04d}")
                if req:
                    batch_requests.append(req)
            print(f"\nSubmitting {len(batch_requests)} batch requests...")
            batch = client.messages.batches.create(requests=batch_requests)
            print(f"Batch ID: {batch.id}")
            print(f"Poll: python {Path(__file__).name} --poll {batch.id}")
        elif to_extract:
            extracted = 0
            for cr in to_extract:
                pc = build_pages_content(cr)
                ext = extract_sync(client, cr, pc)
                if ext:
                    merged = merge_extraction(cr, ext, cr)
                    if merged.get('fleet') or merged.get('contact', {}).get('phone') or merged.get('school_type'):
                        existing_fl[merged['school_website']] = merged
                        extracted += 1
            with open(FLIGHT_LINE, 'w') as f:
                json.dump(list(existing_fl.values()), f, indent=2)
            print(f"\nExtracted {extracted} new schools, total {len(existing_fl)}")

    # Stats
    total = len(browser_results)
    with_pages = sum(1 for r in browser_results if r['pages'])
    with_pdfs = sum(1 for r in browser_results if r['pdfs'])
    rate_sheets = sum(1 for r in browser_results
                      if any(p.get('is_rate_sheet') for p in r.get('pdfs', [])))
    still_flagged = sum(1 for r in browser_results if r.get('crawl_flags'))
    print(f"\nCrawl complete: {total} schools")
    print(f"  {with_pages} with content, {with_pdfs} with PDFs, {rate_sheets} rate sheets")
    print(f"  {still_flagged} still flagged")


if __name__ == '__main__':
    asyncio.run(main())
