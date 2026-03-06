#!/usr/bin/env python3
"""
Phase 9a: Fleet Page Crawl — discover and cache fleet page HTML.

Crawls all flight school websites to find fleet/aircraft pages,
saves the cleaned HTML to disk for later LLM extraction.

This is the "crawl" half of a two-phase pipeline:
  09a_fleet_crawl.py   → discover fleet pages, save HTML
  09b_fleet_extract.py → send HTML to Sonnet for structured extraction

Usage:
    python 09a_fleet_crawl.py                   # all schools
    python 09a_fleet_crawl.py --limit 50        # first 50 (testing)
    python 09a_fleet_crawl.py --concurrency 300 # tune parallelism

Input:  output/flight_schools_enriched.json (+ output/aopa_flight_schools.json)
Output: output/fleet_html/          — cleaned HTML files per page
        output/fleet_crawl_index.json — index mapping schools → pages
"""

import asyncio
import hashlib
import json
import re
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup, Comment, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
from tqdm.asyncio import tqdm as atqdm

OUTPUT_DIR = Path(__file__).parent.parent / "output"
HTML_DIR = OUTPUT_DIR / "fleet_html"

# ── Tuning knobs ─────────────────────────────────────────────────
DEFAULT_CONCURRENCY = 50
REQUEST_TIMEOUT = 20
PER_SCHOOL_TIMEOUT = 120
MAX_RETRIES = 2
CONNECTOR_LIMIT = 300

USER_AGENT = "AetherResearch/1.0 (aviation research; contact: research@aether.aero)"

# ── Link discovery patterns ──────────────────────────────────────

FLEET_LINK_PATTERNS = re.compile(
    r"\b(fleet|aircraft|our\s+planes?|rental(?:s)?|rates?\s*(?:&|and)?\s*fleet"
    r"|airplane(?:s)?|flight\s+line|rent\s+(?:a|an)\s+(?:plane|aircraft))\b",
    re.IGNORECASE,
)

LOCATION_LINK_PATTERNS = re.compile(
    r"\b(locations?|campus(?:es)?|bases?|airports?)\b",
    re.IGNORECASE,
)

# ── AOPA helpers (reused from 09_fleet_scraper_parallel) ─────────

AOPA_API_BASE = "https://webapp.aopa.org/AirportsAPI/businesses"
AOPA_API_TOKEN = "AOPA_PUBLIC_TOKEN_REDACTED"
AOPA_CACHE_PATH = OUTPUT_DIR / "aopa_website_cache.json"


def _slugify_name(name: str) -> str:
    name = name.lower()
    for suffix in [" inc", " llc", " ltd", " corp", " co"]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return re.sub(r"[^a-z0-9]", "", name)


def _extract_aopa_business_id(aopa_url: str) -> str | None:
    m = re.search(r"/business/(\d+)", aopa_url)
    return m.group(1) if m else None


def _load_aopa_cache() -> dict[str, str]:
    if AOPA_CACHE_PATH.exists():
        with open(AOPA_CACHE_PATH) as f:
            return json.load(f)
    return {}


def _save_aopa_cache(cache: dict[str, str]) -> None:
    AOPA_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(AOPA_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


async def _resolve_aopa_websites(
    session: aiohttp.ClientSession,
    records: list[dict],
    semaphore: asyncio.Semaphore,
) -> dict[str, str]:
    cache = _load_aopa_cache()
    id_to_rec: dict[str, dict] = {}
    for rec in records:
        for aopa_url in rec.get("_aopa_urls", []):
            bid = _extract_aopa_business_id(aopa_url)
            if bid and bid not in id_to_rec:
                id_to_rec[bid] = rec

    if not id_to_rec:
        return cache

    results: dict[str, str] = {}
    ids_to_fetch: list[str] = []
    for bid in id_to_rec:
        if bid in cache:
            results[bid] = cache[bid]
        else:
            ids_to_fetch.append(bid)

    if ids_to_fetch:
        print(f"    Cache: {len(results):,} hits, {len(ids_to_fetch):,} to fetch from AOPA API")
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
                        url, headers=headers,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            web = (data.get("webAddress") or "").strip()
                            if web:
                                if not web.startswith("http"):
                                    web = "https://" + web
                                results[bid] = web
                                cache[bid] = web
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    pass

        tasks = [fetch_one(bid) for bid in ids_to_fetch]
        await asyncio.gather(*tasks)
        _save_aopa_cache(cache)
        print(f"    Cache updated: {len(cache):,} total entries")
    else:
        print(f"    All {len(results):,} AOPA lookups served from cache")

    return results


def merge_aopa_schools(enriched: list[dict]) -> list[dict]:
    aopa_path = OUTPUT_DIR / "aopa_flight_schools.json"
    if not aopa_path.exists():
        return enriched

    with open(aopa_path) as f:
        aopa_schools = json.load(f)

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

    annotated = 0
    for s in enriched:
        if s.get("website"):
            continue
        name_lower = s.get("name", "").strip().lower()
        for aopa_name, aopa_rec in aopa_by_name.items():
            if aopa_name.lower() == name_lower and aopa_rec.get("_aopa_urls"):
                s["_aopa_urls"] = list(aopa_rec["_aopa_urls"])
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

async def fetch_html(session: aiohttp.ClientSession, url: str) -> str | None:
    """Fetch raw HTML with retries. Returns HTML string or None."""
    for attempt in range(MAX_RETRIES):
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                allow_redirects=True,
            ) as resp:
                if resp.status == 200:
                    return await resp.text(errors="replace")
                if resp.status == 429:
                    await asyncio.sleep(5 * (attempt + 1))
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError):
            await asyncio.sleep(2 * (attempt + 1))
    return None


async def probe_url(session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=10),
            allow_redirects=True,
        ) as resp:
            if resp.status == 200:
                text = await resp.text(errors="replace")
                if len(text) > 1000:
                    return str(resp.url)
    except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError):
        pass
    return None


# ── HTML cleaning ────────────────────────────────────────────────

def clean_html(raw_html: str) -> str:
    """Strip scripts, styles, nav, footer, and other boilerplate.

    Returns simplified HTML that preserves structure for LLM extraction
    while dramatically reducing token count.
    """
    soup = BeautifulSoup(raw_html, "html.parser")

    # Remove elements that never contain fleet data
    for tag in soup.find_all(["script", "style", "noscript", "iframe",
                              "svg", "path", "meta", "link"]):
        tag.decompose()

    # Remove HTML comments
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # Remove common boilerplate sections
    for tag in soup.find_all(["nav", "footer", "header"]):
        tag.decompose()

    # Remove elements with common boilerplate classes/ids
    boilerplate_patterns = re.compile(
        r"(cookie|gdpr|consent|popup|modal|overlay|sidebar|widget|social|share|"
        r"newsletter|subscribe|banner|advertisement|ad-|advert)",
        re.IGNORECASE,
    )
    for tag in soup.find_all(True):
        if tag.attrs is None:
            continue
        classes = " ".join(tag.get("class", []))
        tag_id = tag.get("id", "")
        if boilerplate_patterns.search(classes) or boilerplate_patterns.search(tag_id):
            tag.decompose()

    # Strip all attributes except href (needed for link context)
    for tag in soup.find_all(True):
        if tag.attrs is None:
            continue
        attrs = dict(tag.attrs)
        for attr in attrs:
            if attr != "href":
                del tag[attr]

    # Get the cleaned HTML string
    html = str(soup)

    # Collapse excessive whitespace
    html = re.sub(r"\n{3,}", "\n\n", html)
    html = re.sub(r"[ \t]+", " ", html)

    return html.strip()


# ── Page discovery ───────────────────────────────────────────────

def _same_domain(base: str, candidate: str) -> bool:
    base_domain = urlparse(base).netloc.lower().lstrip("www.")
    cand_domain = urlparse(candidate).netloc.lower().lstrip("www.")
    return cand_domain == base_domain or cand_domain.endswith("." + base_domain)


def _find_fleet_links(base_url: str, soup: BeautifulSoup) -> list[str]:
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


def _find_location_links(base_url: str, soup: BeautifulSoup) -> list[str]:
    candidates: list[str] = []
    for a in soup.find_all("a", href=True):
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
    return candidates


def _dedupe_urls(urls: list[str], limit: int = 16) -> list[str]:
    seen = set()
    unique = []
    for u in urls:
        # Strip fragments — /page#section is the same as /page
        normalized = u.split("#")[0].rstrip("/")
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return unique[:limit]


def _parent_paths(urls: list[str], base_url: str) -> list[str]:
    existing = {u.rstrip("/") for u in urls}
    existing.add(base_url.rstrip("/"))
    parents: list[str] = []
    for url in urls:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        parent_path = path.rsplit("/", 1)[0]
        if parent_path and parent_path != "/":
            parent_url = f"{parsed.scheme}://{parsed.netloc}{parent_path}/"
            if parent_url.rstrip("/") not in existing:
                parents.append(parent_url)
                existing.add(parent_url.rstrip("/"))
    return parents


# ── HTML storage ─────────────────────────────────────────────────

def _url_to_path(url: str) -> str:
    """Deterministic filename for a URL. Returns relative path under fleet_html/."""
    h = hashlib.sha256(url.encode()).hexdigest()[:12]
    parsed = urlparse(url)
    domain = parsed.netloc.lower().replace("www.", "")
    # Sanitize path for filesystem
    path_slug = parsed.path.strip("/").replace("/", "_")[:60] or "index"
    path_slug = re.sub(r"[^a-zA-Z0-9_-]", "", path_slug)
    return f"{domain}/{path_slug}_{h}.html"


def _save_html(relative_path: str, html: str) -> Path:
    """Save cleaned HTML to fleet_html/ and return the full path."""
    full_path = HTML_DIR / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(html, encoding="utf-8")
    return full_path


# ── Per-school crawl ─────────────────────────────────────────────

async def crawl_school(
    session: aiohttp.ClientSession,
    school: dict,
    semaphore: asyncio.Semaphore,
) -> dict | None:
    """Crawl one school: discover fleet pages, save HTML."""
    async with semaphore:
        try:
            return await asyncio.wait_for(
                _crawl_school_inner(session, school),
                timeout=PER_SCHOOL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return None


async def _resolve_website(session: aiohttp.ClientSession, school: dict) -> str | None:
    candidates = school.get("_website_candidates", [])
    for url in candidates:
        resolved = await probe_url(session, url)
        if resolved:
            return resolved
    return None


async def _crawl_school_inner(
    session: aiohttp.ClientSession,
    school: dict,
) -> dict | None:
    website = (school.get("website") or "").strip()
    if not website:
        website = await _resolve_website(session, school) or ""
    if not website:
        return None
    if not website.startswith("http"):
        website = "https://" + website

    # Fetch home page
    home_html = await fetch_html(session, website)
    if not home_html:
        return None

    home_soup = BeautifulSoup(home_html, "html.parser")

    # ── Fleet page discovery (3 levels, same as original) ────────
    # Level 1: fleet links from home page
    fleet_urls = _dedupe_urls(_find_fleet_links(website, home_soup), limit=8)

    # Level 2: parent paths → discover sibling fleet pages
    parent_urls = _parent_paths(fleet_urls, website)
    parent_htmls: dict[str, str] = {}
    if parent_urls:
        tasks = [fetch_html(session, url) for url in parent_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for url, r in zip(parent_urls, results):
            if isinstance(r, str):
                parent_htmls[url] = r
                parent_soup = BeautifulSoup(r, "html.parser")
                fleet_urls.extend(_find_fleet_links(url, parent_soup))

    fleet_urls = _dedupe_urls(fleet_urls, limit=16)
    fleet_urls = [u for u in fleet_urls if u.rstrip("/") != website.rstrip("/")]

    # Fetch all fleet pages
    fleet_htmls: dict[str, str] = {}
    urls_to_fetch = [u for u in fleet_urls if u not in parent_htmls]
    if urls_to_fetch:
        tasks = [fetch_html(session, url) for url in urls_to_fetch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for url, r in zip(urls_to_fetch, results):
            if isinstance(r, str):
                fleet_htmls[url] = r
    # Include parent pages that are also fleet pages
    fleet_htmls.update({u: h for u, h in parent_htmls.items() if u in fleet_urls})

    # Level 3: from fleet pages, follow all same-domain links that
    # aren't clearly site-wide nav (catches individual aircraft detail
    # pages like /diamond-da40-180-g1000/ without following /admissions/)
    #
    # Heuristic: skip links whose path shares no common prefix with any
    # fleet page beyond the bare domain root.
    base_path = urlparse(website).path.rstrip("/") or ""
    fleet_page_parents = set()
    for u in fleet_urls:
        p = urlparse(u).path.rstrip("/")
        # Add the parent directory of each fleet page
        parent = p.rsplit("/", 1)[0]
        if parent:
            fleet_page_parents.add(parent)
    # Also include the school's base path
    if base_path:
        fleet_page_parents.add(base_path)
    # For sites where fleet pages are at root (e.g. /our-fleet-rental/),
    # allow all root-level pages
    if "" in fleet_page_parents or "/" in fleet_page_parents or not fleet_page_parents:
        fleet_page_parents.add("")  # allow everything

    extra_urls: list[str] = []
    all_seen = {u.rstrip("/").split("#")[0].rstrip("/") for u in fleet_urls}
    all_seen.add(website.rstrip("/"))
    for url, html in fleet_htmls.items():
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            full = urljoin(url, a["href"]).split("#")[0]  # strip fragments
            if not full or full.rstrip("/") in all_seen:
                continue
            if not _same_domain(website, full):
                continue
            link_path = urlparse(full).path.rstrip("/")
            # Check link shares a parent prefix with fleet pages
            if any(link_path.startswith(pfx + "/") or pfx == "" for pfx in fleet_page_parents):
                extra_urls.append(full)
                all_seen.add(full.rstrip("/"))

    if extra_urls:
        extra_urls = _dedupe_urls(extra_urls, limit=20)
        tasks = [fetch_html(session, url) for url in extra_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for url, r in zip(extra_urls, results):
            if isinstance(r, str):
                fleet_htmls[url] = r

    # ── Location page discovery ──────────────────────────────────
    loc_urls = _dedupe_urls(_find_location_links(website, home_soup), limit=4)
    loc_htmls: dict[str, str] = {}
    if loc_urls:
        tasks = [fetch_html(session, url) for url in loc_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for url, r in zip(loc_urls, results):
            if isinstance(r, str):
                loc_htmls[url] = r

    # ── Save cleaned HTML ────────────────────────────────────────
    saved_pages: list[dict] = []

    # Save home page
    home_path = _url_to_path(website)
    _save_html(home_path, clean_html(home_html))
    saved_pages.append({"url": website, "html_path": home_path, "type": "home"})

    # Save fleet pages
    for url, html in fleet_htmls.items():
        rel_path = _url_to_path(url)
        _save_html(rel_path, clean_html(html))
        saved_pages.append({"url": url, "html_path": rel_path, "type": "fleet"})

    # Save location pages
    for url, html in loc_htmls.items():
        rel_path = _url_to_path(url)
        _save_html(rel_path, clean_html(html))
        saved_pages.append({"url": url, "html_path": rel_path, "type": "location"})

    # ── Crawl quality assessment ────────────────────────────────
    crawl_flags: list[str] = []

    # Check if we found any fleet pages at all
    fleet_pages = [p for p in saved_pages if p["type"] == "fleet"]
    if not fleet_pages:
        crawl_flags.append("no_fleet_pages")

    # Check for thin/empty pages (JS-rendered sites return little text).
    # Strip nav/footer/script/style before measuring — empty JS shells
    # often have 200+ chars of HTML structure with zero visible content.
    MIN_CONTENT_CHARS = 500

    def _extract_visible_text(html_path: Path) -> str:
        if not html_path.exists():
            return ""
        soup = BeautifulSoup(
            html_path.read_text(encoding="utf-8", errors="replace"),
            "html.parser",
        )
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)

    thin_pages = 0
    for page in saved_pages:
        text = _extract_visible_text(HTML_DIR / page["html_path"])
        if len(text) < MIN_CONTENT_CHARS:
            thin_pages += 1
    if thin_pages == len(saved_pages):
        crawl_flags.append("all_pages_thin")

    # Also flag if fleet pages specifically are thin, even if other pages aren't
    if fleet_pages:
        fleet_thin = all(
            len(_extract_visible_text(HTML_DIR / p["html_path"])) < MIN_CONTENT_CHARS
            for p in fleet_pages
        )
        if fleet_thin:
            crawl_flags.append("fleet_pages_thin")

    return {
        "school_name": school.get("name", ""),
        "school_website": website,
        "known_airport": school.get("airport_code", ""),
        "city": school.get("city", ""),
        "state": school.get("state_name", school.get("state", "")),
        "pages": saved_pages,
        "crawl_flags": crawl_flags,
        "crawl_ts": datetime.now(timezone.utc).isoformat(),
    }


# ── Main ─────────────────────────────────────────────────────────

async def run_async(limit: int | None = None, concurrency: int = DEFAULT_CONCURRENCY):
    print("=" * 60)
    print("Phase 9a: Fleet Page Crawl")
    print(f"  Concurrency: {concurrency} schools at once")
    print("=" * 60)

    enriched_path = OUTPUT_DIR / "flight_schools_enriched.json"
    if not enriched_path.exists():
        print("  Error: Run Phase 8 (directory scraper) first.")
        return None
    with open(enriched_path) as f:
        schools = json.load(f)

    print(f"  Directory schools: {len(schools):,}")
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

    HTML_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
        # Resolve AOPA websites
        needs_website = [s for s in schools if not s.get("website") and s.get("_aopa_urls")]
        if needs_website:
            print(f"\n  Resolving websites for {len(needs_website):,} AOPA schools via API...")
            api_semaphore = asyncio.Semaphore(50)
            aopa_websites = await _resolve_aopa_websites(session, needs_website, api_semaphore)
            resolved_count = 0
            for s in needs_website:
                for aopa_url in s.get("_aopa_urls", []):
                    bid = _extract_aopa_business_id(aopa_url)
                    if bid and bid in aopa_websites:
                        s["website"] = aopa_websites[bid]
                        resolved_count += 1
                        break
            print(f"  Resolved {resolved_count:,} websites from AOPA API")

        scrapeable = [s for s in schools if s.get("website") or s.get("_website_candidates")]
        print(f"  Scrapeable (website or candidates): {len(scrapeable):,}")

        tasks = [crawl_school(session, school, school_semaphore) for school in scrapeable]
        crawl_index: list[dict] = []
        total_pages = 0
        errors = 0

        pbar = atqdm(total=len(tasks), desc="  Crawling fleet pages")

        async def _tracked(coro):
            try:
                result = await coro
            except Exception as exc:
                result = exc
            pbar.update(1)
            return result

        results = await asyncio.gather(*[_tracked(t) for t in tasks])
        pbar.close()

        for r in results:
            if isinstance(r, Exception):
                errors += 1
            elif r is not None:
                crawl_index.append(r)
                total_pages += len(r["pages"])

    elapsed = time.time() - t0

    # Save crawl index
    index_path = OUTPUT_DIR / "fleet_crawl_index.json"
    with open(index_path, "w") as f:
        json.dump(crawl_index, f, indent=2)

    schools_with_fleet = sum(
        1 for s in crawl_index
        if any(p["type"] == "fleet" for p in s["pages"])
    )
    flagged = [s for s in crawl_index if s.get("crawl_flags")]
    needs_browser = [s for s in flagged
                     if "all_pages_thin" in s["crawl_flags"]
                     or "fleet_pages_thin" in s["crawl_flags"]]

    print(f"\n  === Results ===")
    print(f"  Schools crawled:        {len(scrapeable):,}")
    print(f"  Schools with pages:     {len(crawl_index):,}")
    print(f"  Schools with fleet pg:  {schools_with_fleet:,}")
    print(f"  Total pages saved:      {total_pages:,}")
    print(f"  Flagged (crawl issues): {len(flagged):,}")
    print(f"  Needs browser (thin):   {len(needs_browser):,}")
    print(f"  Errors:                 {errors:,}")
    print(f"  Wall-clock time:        {elapsed:.1f}s")
    print(f"  HTML dir:               {HTML_DIR}")
    print(f"  Index:                  {index_path}")

    print(f"\nPhase 9a complete. Run 09b_fleet_extract.py next.")
    return crawl_index


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Phase 9a: Crawl flight school fleet pages")
    parser.add_argument("--limit", type=int, help="Max schools to crawl (for testing)")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    args = parser.parse_args()
    asyncio.run(run_async(limit=args.limit, concurrency=args.concurrency))


if __name__ == "__main__":
    main()
