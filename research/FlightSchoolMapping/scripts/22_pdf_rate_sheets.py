#!/usr/bin/env python3
"""
22_pdf_rate_sheets.py — Extract N-numbers from PDF rate sheets.

930 schools in flight_line.json have fleet descriptions but 0 N-numbers.
Many of these have PDF rate sheets linked from their websites that contain
the actual tail numbers. This script:

1. Builds a target list of URLs to download (PDF links from cached HTML,
   rate_sheet_url from Sonnet extractions, fleet_page_url fallback)
2. Downloads PDFs and HTML pages
3. Extracts text and runs regex for N-numbers (validated against FAA registry)
4. For complex PDFs where regex fails, uses Sonnet batch API
5. Merges new N-numbers + rates into flight_line.json

Reads output/flight_line_base.json (built by 21_build_flight_line.py) and
writes output/flight_line.json — the canonical artifact everything downstream
consumes. Input and output are deliberately different files so that rerunning
is non-destructive and `make` can model this as an ordinary build edge.

Usage:
    python 22_pdf_rate_sheets.py                    # full pipeline
    python 22_pdf_rate_sheets.py --phase targets    # phase 1 only: build target list
    python 22_pdf_rate_sheets.py --phase download   # phases 1-2: build + download
    python 22_pdf_rate_sheets.py --phase extract    # phases 1-3: build + download + regex
    python 22_pdf_rate_sheets.py --phase batch      # phases 1-4: + Sonnet batch
    python 22_pdf_rate_sheets.py --phase merge      # phases 1-5: + merge into flight_line
    python 22_pdf_rate_sheets.py --dry-run          # show stats only, no downloads
    python 22_pdf_rate_sheets.py --offline          # no network, no API: reuse caches only
"""

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import warnings

import pdfplumber
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

OUTPUT_DIR = Path(__file__).parent.parent / "output"
SCRIPTS_DIR = Path(__file__).parent

# Reuse PLATFORM_DOMAINS from 21_build_flight_line.py
PLATFORM_DOMAINS = {
    "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "linkedin.com", "tiktok.com",
    "wixsite.com", "weebly.com", "squarespace.com",
    "wordpress.com", "blogspot.com", "tripod.com",
    "godaddysites.com", "sites.google.com", "google.com",
    "yelp.com", "yellowpages.com", "bbb.org",
}

# N-number regex: N followed by 1-5 digits, optionally ending with 1-2 letters
NN_PATTERN = re.compile(r'\bN(\d{1,5}[A-Z]{0,2})\b')

# PDF/rate-related keywords in href or anchor text
RATE_KEYWORDS = re.compile(
    r'rate|fleet|aircraft|rental|price|schedule|checkout|plane',
    re.IGNORECASE,
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

SONNET_EXTRACTION_PROMPT = """Extract all aircraft from this flight school document.

For each aircraft, provide:
- n_number: FAA N-number (e.g., "N172SP")
- display_name: Aircraft type and model (e.g., "Cessna 172S Skyhawk")
- wet_rate_hr: Wet hourly rental rate in USD (null if not listed)
- dry_rate_hr: Dry hourly rental rate in USD (null if not listed)
- description: Any additional details (equipment, fuel capacity, etc.)
- equipment: List of notable equipment (e.g., ["G1000", "GFC 700 autopilot"])

Return JSON: {"fleet": [...]}

If no aircraft with N-numbers are found, return {"fleet": []}.
Focus on extracting N-numbers (tail numbers) — these are the most important field."""


# ── Utilities (mirrored from 21_build_flight_line.py) ─────────────


def normalize_domain(url: str) -> str:
    """Extract and normalize domain from URL. Returns '' for platforms."""
    if not url:
        return ""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        domain = parsed.netloc.lower().strip()
    except Exception:
        return ""
    if domain.startswith("www."):
        domain = domain[4:]
    if ":" in domain:
        domain = domain.split(":")[0]
    for plat in PLATFORM_DOMAINS:
        if domain == plat or domain.endswith(f".{plat}"):
            return ""
    return domain


def normalize_nn(nn: str) -> str:
    """Normalize an N-number: uppercase, ensure N prefix."""
    if not nn:
        return ""
    nn = nn.upper().strip()
    if nn and not nn.startswith("N"):
        nn = f"N{nn}"
    return nn


def parse_llm_response(text: str) -> dict | None:
    """Parse Sonnet's JSON response, handling markdown wrappers and truncation."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines[1:] if not l.strip().startswith("```")]
        text = "\n".join(lines)
    if text.startswith("json"):
        text = text[4:]
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    if start >= 0:
        fragment = text[start:]
        for suffix in ["]}", "]\n}", '"]\n}\n]\n}', "\n]\n}\n"]:
            trimmed = fragment.rstrip().rstrip(",")
            try:
                return json.loads(trimmed + suffix)
            except json.JSONDecodeError:
                continue
        last_brace = fragment.rfind("}")
        if last_brace > 0:
            trimmed = fragment[:last_brace + 1]
            for suffix in ["]}", "]\n}"]:
                try:
                    return json.loads(trimmed + suffix)
                except json.JSONDecodeError:
                    continue
    return None


def load_faa_registry() -> dict[str, dict]:
    """Load FAA master registry keyed by N-number (with N prefix)."""
    faa_path = OUTPUT_DIR / "faa_master.csv"
    if not faa_path.exists():
        print("  WARNING: faa_master.csv not found")
        return {}

    with open(faa_path, "rb") as f:
        raw = f.read()

    text = raw.decode("utf-8", errors="replace")
    header_start = text.find("N-NUMBER")
    if header_start > 0:
        text = text[header_start:]

    lines = text.splitlines()
    if not lines:
        return {}

    header = lines[0]
    reader = csv.DictReader(lines[1:], fieldnames=header.split(","))
    registry = {}
    for row in reader:
        nn = (row.get("N-NUMBER") or "").strip()
        if nn:
            nn_key = f"N{nn}" if not nn.startswith("N") else nn
            registry[nn_key] = row

    print(f"  FAA registry: {len(registry):,} registrations")
    return registry


def load_acftref() -> dict[str, dict]:
    """Load FAA aircraft type reference keyed by MFR MDL CODE."""
    path = OUTPUT_DIR / "ACFTREF.txt"
    if not path.exists():
        return {}
    ref = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("CODE") or "").strip()
            if code:
                ref[code] = row
    print(f"  ACFTREF: {len(ref):,} type codes")
    return ref


def load_adsb_activity() -> dict[str, dict]:
    """Load ADS-B activity keyed by icao_hex (uppercase)."""
    path = OUTPUT_DIR / "adsb_activity.csv"
    if not path.exists():
        print("  WARNING: adsb_activity.csv not found")
        return {}
    activity = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            hex_code = (row.get("icao_hex") or "").strip().upper()
            if hex_code:
                activity[hex_code] = row
    print(f"  ADS-B activity: {len(activity):,} transponders")
    return activity


def enrich_aircraft(aircraft: dict, faa_registry: dict, acftref: dict, adsb: dict):
    """Add FAA registration and ADS-B data to an aircraft entry. Mutates in place."""
    nn = aircraft.get("n_number", "")
    if not nn or nn not in faa_registry:
        return

    reg = faa_registry[nn]
    mfr_code = (reg.get("MFR MDL CODE") or "").strip()
    ref = acftref.get(mfr_code, {})

    mfr = (ref.get("MFR") or "").strip()
    model = (ref.get("MODEL") or "").strip()
    mode_s_hex = (reg.get("MODE S CODE HEX") or "").strip()

    aircraft["faa"] = {
        "mode_s_hex": mode_s_hex,
        "mfr": mfr,
        "model": model,
        "type_designator": mfr_code,
        "year_mfr": (reg.get("YEAR MFR") or "").strip(),
        "owner": (reg.get("NAME") or "").strip(),
        "status": (reg.get("STATUS CODE") or "").strip(),
        "serial_number": (reg.get("SERIAL NUMBER") or "").strip(),
        "registrant_city": (reg.get("CITY") or "").strip(),
        "registrant_state": (reg.get("STATE") or "").strip(),
        "type_aircraft": (reg.get("TYPE AIRCRAFT") or "").strip(),
        "type_engine": (reg.get("TYPE ENGINE") or "").strip(),
    }

    if mode_s_hex and mode_s_hex in adsb:
        adsb_rec = adsb[mode_s_hex]
        sightings = int(adsb_rec.get("total_sightings", 0) or 0)
        aircraft["adsb"] = {
            "active": sightings > 0,
            "total_sightings": sightings,
            "home_airport": (adsb_rec.get("home_airport") or "").strip(),
        }


# ── Phase 1: Build Target List ───────────────────────────────────


def load_zero_nn_domains(flight_line: list[dict]) -> dict[str, dict]:
    """Return {domain: school_summary} for schools with fleet but 0 N-numbers."""
    result = {}
    for school in flight_line:
        domain = school.get("domain", "")
        if not domain:
            continue
        fleet_count = school.get("fleet_count", 0)
        nn_count = school.get("n_number_count", 0)
        if fleet_count > 0 and nn_count == 0:
            result[domain] = {
                "name": school.get("name", ""),
                "fleet_count": fleet_count,
                "extraction_sources": school.get("extraction_sources", []),
            }
    return result


def scan_html_for_pdf_links(html_dir: Path, zero_nn_domains: set[str]) -> list[dict]:
    """Scan cached HTML files for PDF links with rate/fleet keywords."""
    targets = []
    if not html_dir.exists():
        return targets

    for domain_dir in html_dir.iterdir():
        if not domain_dir.is_dir():
            continue
        domain = domain_dir.name
        if domain not in zero_nn_domains:
            continue

        for html_file in domain_dir.iterdir():
            if not html_file.suffix == ".html":
                continue
            try:
                html = html_file.read_text(errors="replace")
            except Exception:
                continue

            try:
                soup = BeautifulSoup(html, "html.parser")
            except Exception:
                continue
            base_url = f"https://{domain}/"

            for a in soup.find_all("a", href=True):
                href = a["href"]
                anchor_text = a.get_text(strip=True)

                # Check if it's a PDF link
                is_pdf = href.lower().endswith(".pdf")
                has_keyword = bool(
                    RATE_KEYWORDS.search(href) or RATE_KEYWORDS.search(anchor_text)
                )

                if is_pdf and has_keyword:
                    full_url = urljoin(base_url, href)
                    targets.append({
                        "domain": domain,
                        "url": full_url,
                        "source": "html_pdf_link",
                        "source_file": str(html_file.relative_to(OUTPUT_DIR)),
                    })

    return targets


def _resolve_url(url: str, domain: str) -> str:
    """Resolve a URL that might be bare path, bare domain, or full URL."""
    url = url.strip()
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    # Check if it looks like domain/path or just a path
    if "." in url.split("/")[0]:
        # Looks like a domain (e.g., "school.com/fleet")
        return f"https://{url}"
    # Bare path — resolve against domain
    return f"https://{domain}/{url.lstrip('/')}"


def load_extraction_urls(zero_nn_domains: set[str]) -> list[dict]:
    """Load rate_sheet_url and fleet_page_url from extraction results."""
    targets = []
    seen = set()  # (domain, url) dedup

    # Source 1: expansion_extract.jsonl (has domain embedded)
    exp_path = OUTPUT_DIR / "expansion_extract.jsonl"
    if exp_path.exists():
        with open(exp_path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                domain = rec.get("domain", "")
                if domain not in zero_nn_domains:
                    continue
                data = rec.get("data", {})
                if not isinstance(data, dict):
                    continue

                for field, source_tag in [
                    ("rate_sheet_url", "expansion_rate_sheet"),
                    ("fleet_page_url", "expansion_fleet_page"),
                ]:
                    url = data.get(field, "")
                    if url:
                        url = _resolve_url(url, domain)
                        if not url:
                            continue
                        key = (domain, url)
                        if key not in seen:
                            seen.add(key)
                            targets.append({
                                "domain": domain,
                                "url": url,
                                "source": source_tag,
                            })

    # Source 2: browser batch results (custom_id → domain via index)
    browser_idx_path = OUTPUT_DIR / "fleet_browser_extract_index.json"
    browser_results_path = OUTPUT_DIR / "fleet_browser_extract_results.jsonl"
    if browser_idx_path.exists() and browser_results_path.exists():
        idx = json.load(open(browser_idx_path))
        cid_to_domain = {item["custom_id"]: item["domain"] for item in idx}

        with open(browser_results_path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cid = rec.get("custom_id", "")
                domain = cid_to_domain.get(cid, "")
                if domain not in zero_nn_domains:
                    continue

                # Parse the nested Sonnet response
                try:
                    text = rec["result"]["message"]["content"][0].get("text", "")
                except (KeyError, IndexError):
                    continue
                parsed = parse_llm_response(text)
                if not parsed:
                    continue

                for field, source_tag in [
                    ("rate_sheet_url", "browser_rate_sheet"),
                    ("fleet_page_url", "browser_fleet_page"),
                ]:
                    url = parsed.get(field, "")
                    if url:
                        url = _resolve_url(url, domain)
                        if not url:
                            continue
                        key = (domain, url)
                        if key not in seen:
                            seen.add(key)
                            targets.append({
                                "domain": domain,
                                "url": url,
                                "source": source_tag,
                            })

    # Source 3: HTTP batch results (custom_id positional, but we also check
    # for rate_sheet_url in the response text itself)
    http_results_path = OUTPUT_DIR / "fleet_extract_results.jsonl"
    http_index_path = OUTPUT_DIR / "fleet_extract_batches.json"
    if http_results_path.exists() and http_index_path.exists():
        # Build custom_id → domain mapping from batches index
        try:
            batches = json.load(open(http_index_path))
            cid_to_domain = {}
            if isinstance(batches, list):
                for batch in batches:
                    for item in batch.get("items", []):
                        cid = item.get("custom_id", "")
                        domain = normalize_domain(item.get("source_url", ""))
                        if cid and domain:
                            cid_to_domain[cid] = domain
            elif isinstance(batches, dict):
                for cid, info in batches.items():
                    domain = normalize_domain(info.get("source_url", ""))
                    if domain:
                        cid_to_domain[cid] = domain
        except Exception:
            cid_to_domain = {}

        with open(http_results_path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cid = rec.get("custom_id", "")
                domain = cid_to_domain.get(cid, "")
                if domain not in zero_nn_domains:
                    continue

                try:
                    text = rec["result"]["message"]["content"][0].get("text", "")
                except (KeyError, IndexError):
                    continue
                parsed = parse_llm_response(text)
                if not parsed:
                    continue

                for field, source_tag in [
                    ("rate_sheet_url", "http_rate_sheet"),
                    ("fleet_page_url", "http_fleet_page"),
                ]:
                    url = parsed.get(field, "")
                    if url:
                        url = _resolve_url(url, domain)
                        if not url:
                            continue
                        key = (domain, url)
                        if key not in seen:
                            seen.add(key)
                            targets.append({
                                "domain": domain,
                                "url": url,
                                "source": source_tag,
                            })

    return targets


def _targets_cache_key(zero_nn_set: set) -> str:
    """Fingerprint everything build_targets() derives its answer from.

    Parsing ~3,000 domains of cached HTML for PDF links costs ~80 s, and the
    answer only moves when the crawl caches change or the set of schools with
    a fleet but no N-numbers changes. flight_line_base.json is NOT an input
    here — it churns on every ADS-B fold, but the 0-NN domain set it yields
    almost never does.
    """
    h = hashlib.sha256()
    for d in sorted(zero_nn_set):
        h.update(d.encode("utf-8", "surrogateescape"))
        h.update(b"\0")
    for html_dir_name in ["fleet_html_raw", "fleet_browser_html_raw"]:
        html_dir = OUTPUT_DIR / html_dir_name
        h.update(html_dir_name.encode())
        if not html_dir.is_dir():
            continue
        entries = []
        for dirpath, dirnames, filenames in os.walk(html_dir):
            dirnames.sort()
            for name in sorted(filenames):
                fp = Path(dirpath) / name
                try:
                    entries.append(f"{fp.relative_to(html_dir).as_posix()}\0{fp.stat().st_size}")
                except OSError:
                    continue
        for e in sorted(entries):
            h.update(e.encode("utf-8", "surrogateescape"))
            h.update(b"\n")
    return h.hexdigest()


def build_targets(flight_line: list[dict], dry_run: bool = False) -> list[dict]:
    """Phase 1: Build deduplicated target list of URLs to download."""
    print("\n═══ Phase 1: Building target list ═══")

    zero_nn = load_zero_nn_domains(flight_line)
    zero_nn_set = set(zero_nn.keys())
    print(f"  0-NN schools with fleet: {len(zero_nn)}")

    cache_key = _targets_cache_key(zero_nn_set)
    cache_path = OUTPUT_DIR / "pdf_targets.json"
    key_path = OUTPUT_DIR / "pdf_targets.key"
    if cache_path.exists() and key_path.exists():
        if key_path.read_text().strip() == cache_key:
            cached = json.loads(cache_path.read_text())
            unique_domains = len({t["domain"] for t in cached})
            print(f"  Reusing cached target list: {len(cached)} URLs across {unique_domains} domains")
            return cached

    # Source A: PDF links in cached HTML
    pdf_targets = []
    for html_dir_name in ["fleet_html_raw", "fleet_browser_html_raw"]:
        html_dir = OUTPUT_DIR / html_dir_name
        found = scan_html_for_pdf_links(html_dir, zero_nn_set)
        pdf_targets.extend(found)
        print(f"  PDF links from {html_dir_name}/: {len(found)}")

    # Source B+C: Extraction URLs
    ext_targets = load_extraction_urls(zero_nn_set)
    print(f"  URLs from extraction results: {len(ext_targets)}")

    # Combine and deduplicate by (domain, url)
    all_targets = pdf_targets + ext_targets
    seen = set()
    deduped = []
    for t in all_targets:
        key = (t["domain"], t["url"])
        if key not in seen:
            seen.add(key)
            # Enrich with school info
            info = zero_nn.get(t["domain"], {})
            t["school_name"] = info.get("name", "")
            t["fleet_count"] = info.get("fleet_count", 0)
            deduped.append(t)

    # Sort: high-fleet-count domains first, then PDF links before fleet pages
    source_priority = {
        "html_pdf_link": 0,
        "html_pdf_link_generic": 1,
        "expansion_rate_sheet": 2,
        "browser_rate_sheet": 3,
        "http_rate_sheet": 4,
        "expansion_fleet_page": 5,
        "browser_fleet_page": 6,
        "http_fleet_page": 7,
    }
    deduped.sort(key=lambda t: (-t["fleet_count"], source_priority.get(t["source"], 9)))

    unique_domains = len({t["domain"] for t in deduped})
    print(f"\n  Total targets: {len(deduped)} URLs across {unique_domains} domains")

    # Save target list, and the key it was computed under
    if not dry_run:
        with open(cache_path, "w") as f:
            json.dump(deduped, f, indent=2)
        key_path.write_text(cache_key + "\n")
        print(f"  Saved: output/pdf_targets.json")

    return deduped


# ── Phase 2: Download PDFs ───────────────────────────────────────


def download_targets(targets: list[dict], dry_run: bool = False, offline: bool = False) -> dict:
    """Phase 2: Download PDFs and HTML pages. Returns progress dict."""
    print("\n═══ Phase 2: Downloading targets ═══")

    pdf_dir = OUTPUT_DIR / "rate_sheet_pdf"
    progress_path = OUTPUT_DIR / "pdf_download_progress.json"

    # Load existing progress
    progress = {}
    if progress_path.exists():
        try:
            progress = json.load(open(progress_path))
        except Exception:
            progress = {}

    if offline:
        have = sum(1 for t in targets if progress.get(t["url"], {}).get("status") == "ok")
        print(f"  OFFLINE — using {have} previously downloaded of {len(targets)} targets")
        print(f"  ({len(targets) - have} not cached; run without --offline to fetch them)")
        return progress

    if dry_run:
        already = sum(1 for t in targets if t["url"] in progress)
        print(f"  Would download {len(targets) - already} of {len(targets)} targets")
        print(f"  Already downloaded: {already}")
        return progress

    pdf_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    downloaded = 0
    skipped = 0
    errors = 0

    for i, target in enumerate(targets):
        url = target["url"]
        domain = target["domain"]

        # Skip already-downloaded
        if url in progress and progress[url].get("status") == "ok":
            skipped += 1
            continue

        # Create domain directory
        domain_dir = pdf_dir / domain
        domain_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename from URL
        parsed = urlparse(url)
        filename = parsed.path.strip("/").replace("/", "_") or "index"
        if len(filename) > 100:
            filename = filename[:100]
        # Sanitize
        filename = re.sub(r'[^\w\-.]', '_', filename)

        try:
            resp = session.get(url, timeout=15, allow_redirects=True)
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "").lower()

            if "pdf" in content_type or url.lower().endswith(".pdf"):
                if not filename.endswith(".pdf"):
                    filename += ".pdf"
                filepath = domain_dir / filename
                filepath.write_bytes(resp.content)
                progress[url] = {
                    "status": "ok",
                    "type": "pdf",
                    "domain": domain,
                    "path": str(filepath.relative_to(OUTPUT_DIR)),
                    "size": len(resp.content),
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            elif "html" in content_type or "text" in content_type:
                if not filename.endswith(".html"):
                    filename += ".html"
                filepath = domain_dir / filename
                filepath.write_text(resp.text, encoding="utf-8")
                progress[url] = {
                    "status": "ok",
                    "type": "html",
                    "domain": domain,
                    "path": str(filepath.relative_to(OUTPUT_DIR)),
                    "size": len(resp.text),
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            else:
                progress[url] = {
                    "status": "skip",
                    "reason": f"unexpected content-type: {content_type}",
                    "domain": domain,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }

            downloaded += 1

        except Exception as e:
            progress[url] = {
                "status": "error",
                "error": str(e)[:200],
                "domain": domain,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            errors += 1

        # Rate limiting
        time.sleep(0.5)

        # Progress update every 50
        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{len(targets)}] downloaded={downloaded} skipped={skipped} errors={errors}")
            # Save progress incrementally
            with open(progress_path, "w") as f:
                json.dump(progress, f, indent=2)

    # Final save
    with open(progress_path, "w") as f:
        json.dump(progress, f, indent=2)

    ok = sum(1 for v in progress.values() if v.get("status") == "ok")
    pdfs = sum(1 for v in progress.values() if v.get("type") == "pdf")
    htmls = sum(1 for v in progress.values() if v.get("type") == "html")
    print(f"\n  Download complete: {ok} OK ({pdfs} PDF, {htmls} HTML), {errors} errors, {skipped} skipped")

    return progress


# ── Phase 3: Extract Text + Regex N-Numbers ──────────────────────


def _summarize_extraction(
    combined_text: str, source_urls: list, text_path: Path, faa_registry: dict
) -> dict:
    """Build the per-domain extraction record.

    Shared by the fresh-extract and cached-text paths so the two cannot drift.
    """
    n_numbers = find_n_numbers(combined_text, faa_registry)
    return {
        "n_numbers": n_numbers,
        "text_length": len(combined_text),
        "source_urls": source_urls,
        "needs_sonnet": len(n_numbers) == 0 and len(combined_text) > 500,
        "text_path": str(text_path.relative_to(OUTPUT_DIR)),
    }


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract text from PDF using pdfplumber, preserving table structure."""
    try:
        text_parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                # Try table extraction first for structured data
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        for row in table:
                            if row:
                                cells = [str(c or "").strip() for c in row]
                                text_parts.append("\t".join(cells))
                # Also get regular text
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n".join(text_parts)
    except Exception as e:
        return f"[PDF extraction error: {e}]"


def extract_text_from_html(html_path: Path) -> str:
    """Extract text from HTML using BeautifulSoup."""
    try:
        html = html_path.read_text(errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        # Remove script and style elements
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
    except Exception as e:
        return f"[HTML extraction error: {e}]"


def find_n_numbers(text: str, faa_registry: dict) -> list[str]:
    """Find N-numbers in text, validated against FAA registry."""
    raw_matches = NN_PATTERN.findall(text)
    validated = []
    seen = set()
    for match in raw_matches:
        nn = normalize_nn(f"N{match}")
        if nn in seen:
            continue
        seen.add(nn)
        if nn in faa_registry:
            validated.append(nn)
    return sorted(validated)


def extract_text_and_numbers(
    download_progress: dict, faa_registry: dict, dry_run: bool = False
) -> dict[str, dict]:
    """Phase 3: Extract text from downloads, find N-numbers via regex.

    Returns {domain: {n_numbers: [...], text_length: int, source_urls: [...], needs_sonnet: bool}}
    """
    print("\n═══ Phase 3: Extracting text + regex N-numbers ═══")

    text_dir = OUTPUT_DIR / "rate_sheet_text"
    text_dir.mkdir(parents=True, exist_ok=True)

    results = {}  # domain -> extraction result
    ok_downloads = [
        (url, info)
        for url, info in download_progress.items()
        if info.get("status") == "ok"
    ]

    if dry_run:
        domains = {info["domain"] for _, info in ok_downloads}
        print(f"  Would extract text from {len(ok_downloads)} files across {len(domains)} domains")
        return results

    # Group downloads by domain
    by_domain: dict[str, list[tuple[str, dict]]] = {}
    for url, info in ok_downloads:
        domain = info["domain"]
        by_domain.setdefault(domain, []).append((url, info))

    total_nn = 0
    domains_with_nn = 0

    reused = 0
    for domain, downloads in sorted(by_domain.items()):
        text_path = text_dir / f"{domain}.txt"

        # pdfplumber over every cached PDF costs ~5 minutes across the corpus,
        # and the sources only change when `--phase download` actually fetches
        # something new. Reuse the previously extracted text whenever it is
        # newer than every file it was derived from.
        sources = [
            (url, OUTPUT_DIR / info["path"])
            for url, info in downloads
            if (OUTPUT_DIR / info["path"]).exists()
        ]
        if not sources:
            continue

        if text_path.exists():
            cached_mtime = text_path.stat().st_mtime
            if all(p.stat().st_mtime <= cached_mtime for _, p in sources):
                combined_text = text_path.read_text(encoding="utf-8")
                source_urls = [url for url, _ in sources]
                reused += 1
                results[domain] = _summarize_extraction(
                    combined_text, source_urls, text_path, faa_registry
                )
                nn = results[domain]["n_numbers"]
                if nn:
                    total_nn += len(nn)
                    domains_with_nn += 1
                continue

        all_text_parts = []
        source_urls = []
        for url, filepath in sources:
            source_urls.append(url)
            info = dict(downloads)[url]

            if info["type"] == "pdf":
                text = extract_text_from_pdf(filepath)
            elif info["type"] == "html":
                text = extract_text_from_html(filepath)
            else:
                continue

            if text:
                all_text_parts.append(f"--- Source: {url} ---\n{text}")

        combined_text = "\n\n".join(all_text_parts)
        if not combined_text:
            continue

        # Save extracted text
        text_path.write_text(combined_text, encoding="utf-8")

        results[domain] = _summarize_extraction(
            combined_text, source_urls, text_path, faa_registry
        )
        n_numbers = results[domain]["n_numbers"]

        if n_numbers:
            domains_with_nn += 1
            total_nn += len(n_numbers)

    print(f"  Extracted text from {len(results)} domains ({reused} reused from cache)")
    print(f"  Regex N-numbers: {total_nn} across {domains_with_nn} domains")
    sonnet_needed = sum(1 for r in results.values() if r["needs_sonnet"])
    print(f"  Domains needing Sonnet: {sonnet_needed}")

    return results


# ── Phase 4: Sonnet Batch Extraction ─────────────────────────────


def run_sonnet_batch(
    extraction_results: dict[str, dict],
    faa_registry: dict,
    dry_run: bool = False,
    offline: bool = False,
) -> dict[str, list[str]]:
    """Phase 4: Use Sonnet batch API for PDFs where regex found 0 N-numbers.

    Returns {domain: [n_numbers]} for newly found N-numbers.
    """
    print("\n═══ Phase 4: Sonnet batch extraction ═══")

    # Check for cached batch results
    batch_cache_path = OUTPUT_DIR / "pdf_batch_results.json"
    if offline and not batch_cache_path.exists():
        print("  OFFLINE — no cached batch results; skipping Sonnet extraction")
        return {}
    if batch_cache_path.exists() and not dry_run:
        print("  Loading cached batch results...")
        cached = json.load(open(batch_cache_path))
        new_nn_by_domain: dict[str, list[str]] = {}
        for domain, data in cached.items():
            nns = data.get("n_numbers", [])
            validated = [nn for nn in nns if nn in faa_registry]
            if validated:
                new_nn_by_domain[domain] = validated
                if domain in extraction_results:
                    extraction_results[domain]["n_numbers"] = validated
                    if "sonnet_fleet" in data:
                        extraction_results[domain]["sonnet_fleet"] = data["sonnet_fleet"]
        total_cached = sum(len(nns) for nns in new_nn_by_domain.values())
        print(f"  Cached: {total_cached} N-numbers across {len(new_nn_by_domain)} domains")
        return new_nn_by_domain

    if offline:
        # Reachable only with --offline --dry-run; never submit a batch offline.
        print("  OFFLINE — skipping Sonnet extraction")
        return {}

    # Aircraft-related terms that suggest the text might contain fleet info
    AIRCRAFT_TERMS = re.compile(
        r'cessna|piper|beechcraft|cirrus|mooney|diamond|bonanza|'
        r'skyhawk|archer|warrior|cherokee|seminole|seneca|'
        r'C172|C152|PA-28|PA-44|DA40|DA42|SR20|SR22|'
        r'rental rate|wet rate|dry rate|hourly rate|per hour|/hr|'
        r'checkout|aircraft rental|tail.?number',
        re.IGNORECASE,
    )

    # Find domains that need Sonnet — only if text contains aircraft terms
    sonnet_targets = {}
    skipped_no_terms = 0
    for domain, info in extraction_results.items():
        if not info.get("needs_sonnet", False):
            continue
        # Read text and check for aircraft terms
        text_path = OUTPUT_DIR / info.get("text_path", "")
        if not text_path.exists():
            continue
        text = text_path.read_text(encoding="utf-8")
        if AIRCRAFT_TERMS.search(text):
            sonnet_targets[domain] = info
        else:
            skipped_no_terms += 1

    if not sonnet_targets:
        print("  No domains need Sonnet extraction")
        if skipped_no_terms:
            print(f"  ({skipped_no_terms} domains skipped — no aircraft terms in text)")
        return {}

    print(f"  Sonnet targets: {len(sonnet_targets)} domains")
    if skipped_no_terms:
        print(f"  Skipped (no aircraft terms): {skipped_no_terms} domains")

    # Estimate cost
    total_chars = sum(info["text_length"] for info in sonnet_targets.values())
    estimated_tokens = total_chars / 4  # rough char-to-token ratio
    # Batch pricing: $3/MTok input for Sonnet
    estimated_cost = (estimated_tokens / 1_000_000) * 3
    print(f"  Estimated input: ~{estimated_tokens:,.0f} tokens (~${estimated_cost:.2f} batch)")

    if dry_run:
        return {}

    # Check for anthropic API key
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("  WARNING: ANTHROPIC_API_KEY not set, skipping Sonnet batch")
        return {}

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    # Build batch requests
    batch_requests = []
    cid_to_domain = {}  # safe custom_id -> domain mapping

    for domain, info in sorted(sonnet_targets.items()):
        text_path = OUTPUT_DIR / info["text_path"]
        if not text_path.exists():
            continue

        text = text_path.read_text(encoding="utf-8")
        # Truncate very long texts to 100K chars (~25K tokens)
        if len(text) > 100_000:
            text = text[:100_000] + "\n[... truncated]"

        # custom_id must match ^[a-zA-Z0-9_-]{1,64}$
        safe_id = f"pdf_{re.sub(r'[^a-zA-Z0-9_-]', '_', domain)[:58]}"
        cid_to_domain[safe_id] = domain
        batch_requests.append({
            "custom_id": safe_id,
            "params": {
                "model": "claude-sonnet-4-6",
                "max_tokens": 4096,
                "system": SONNET_EXTRACTION_PROMPT,
                "messages": [
                    {
                        "role": "user",
                        "content": f"Extract all aircraft with N-numbers from this flight school document:\n\n{text}",
                    }
                ],
            },
        })
        # domain already tracked in cid_to_domain

    if not batch_requests:
        print("  No valid batch requests")
        return {}

    print(f"  Submitting batch of {len(batch_requests)} requests...")

    # Submit batch
    batch = client.messages.batches.create(requests=batch_requests)
    batch_id = batch.id
    print(f"  Batch ID: {batch_id}")
    print(f"  Status: {batch.processing_status}")

    # Poll for completion
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

    # Retrieve results
    new_nn_by_domain: dict[str, list[str]] = {}

    for result in client.messages.batches.results(batch_id):
        custom_id = result.custom_id
        domain = cid_to_domain.get(custom_id, "")

        if result.result and result.result.type == "succeeded":
            try:
                text = result.result.message.content[0].text
            except (AttributeError, IndexError):
                continue
            parsed = parse_llm_response(text)
            if parsed and "fleet" in parsed:
                n_numbers = []
                for ac in parsed["fleet"]:
                    nn = normalize_nn(ac.get("n_number", ""))
                    if nn and nn in faa_registry:
                        n_numbers.append(nn)
                if n_numbers:
                    new_nn_by_domain[domain] = n_numbers
                    # Also update extraction_results
                    if domain in extraction_results:
                        extraction_results[domain]["n_numbers"] = n_numbers
                        extraction_results[domain]["sonnet_fleet"] = parsed["fleet"]

    total_new = sum(len(nns) for nns in new_nn_by_domain.values())
    print(f"\n  Sonnet found: {total_new} N-numbers across {len(new_nn_by_domain)} domains")

    # Cache batch results to avoid re-submitting
    cache_data = {}
    for domain, nns in new_nn_by_domain.items():
        cache_data[domain] = {
            "n_numbers": nns,
            "sonnet_fleet": extraction_results.get(domain, {}).get("sonnet_fleet", []),
        }
    with open(batch_cache_path, "w") as f:
        json.dump(cache_data, f, indent=2)
    print(f"  Cached batch results to: output/pdf_batch_results.json")

    return new_nn_by_domain


# ── Phase 5: Merge into flight_line.json ─────────────────────────


def write_json_atomic(path, obj):
    """Write JSON via a temp file + rename, so an interrupted run never leaves
    a truncated flight_line.json behind."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    tmp.replace(path)


def merge_into_flight_line(
    flight_line: list[dict],
    extraction_results: dict[str, dict],
    faa_registry: dict,
    acftref: dict,
    adsb: dict,
    dry_run: bool = False,
    out_path: Path = None,
) -> dict:
    """Phase 5: Merge new N-numbers into flight_line.json entries.

    Returns diagnostics dict.
    """
    print("\n═══ Phase 5: Merging into flight_line.json ═══")

    # Build domain → school index
    domain_idx = {s["domain"]: s for s in flight_line if s.get("domain")}

    # Count domains with actual new N-numbers
    domains_with_nn = {
        domain: info
        for domain, info in extraction_results.items()
        if info.get("n_numbers")
    }

    print(f"  Domains with new N-numbers: {len(domains_with_nn)}")

    if dry_run:
        total_nn = sum(len(info["n_numbers"]) for info in domains_with_nn.values())
        print(f"  Would add ~{total_nn} new N-numbers")
        return {"domains_updated": len(domains_with_nn), "total_new_nn": total_nn}

    updated = 0
    total_new_nn = 0
    total_new_adsb = 0

    for domain, info in sorted(domains_with_nn.items()):
        school = domain_idx.get(domain)
        if not school:
            continue

        existing_nn = {
            a.get("n_number") for a in school.get("fleet", []) if a.get("n_number")
        }
        new_nns = [nn for nn in info["n_numbers"] if nn not in existing_nn]

        if not new_nns:
            continue

        # Get Sonnet fleet data if available (has richer info)
        sonnet_fleet = {
            normalize_nn(a.get("n_number", "")): a
            for a in info.get("sonnet_fleet", [])
            if a.get("n_number")
        }

        for nn in new_nns:
            sonnet_ac = sonnet_fleet.get(nn, {})

            aircraft = {
                "n_number": nn,
                "display_name": sonnet_ac.get("display_name", ""),
                "description": sonnet_ac.get("description", ""),
                "wet_rate_hr": sonnet_ac.get("wet_rate_hr"),
                "dry_rate_hr": sonnet_ac.get("dry_rate_hr"),
                "equipment": sonnet_ac.get("equipment", []),
                "source_url": info.get("source_urls", [""])[0] if info.get("source_urls") else "",
            }

            # FAA enrichment
            enrich_aircraft(aircraft, faa_registry, acftref, adsb)

            # Use FAA data for display_name if not from Sonnet
            if not aircraft["display_name"] and "faa" in aircraft:
                faa = aircraft["faa"]
                mfr = faa.get("mfr", "")
                model = faa.get("model", "")
                if mfr or model:
                    aircraft["display_name"] = f"{mfr} {model}".strip()

            school["fleet"].append(aircraft)
            total_new_nn += 1

            if aircraft.get("adsb", {}).get("active"):
                total_new_adsb += 1

        # Update counts
        fleet = school["fleet"]
        school["fleet_count"] = len(fleet)
        school["n_number_count"] = sum(1 for a in fleet if a.get("n_number"))
        school["adsb_active_count"] = sum(
            1 for a in fleet if a.get("adsb", {}).get("active")
        )

        # Add source marker
        sources = school.get("extraction_sources", [])
        if "pdf_rate_sheet" not in sources:
            sources.append("pdf_rate_sheet")
            school["extraction_sources"] = sources

        updated += 1

    print(f"  Updated {updated} schools with {total_new_nn} new N-numbers")
    print(f"  New ADS-B active aircraft: {total_new_adsb}")

    # Always write the output — it is this stage's build artifact, so it has to
    # exist (and be newer than its input) even on a run that added nothing.
    fl_path = Path(out_path) if out_path else OUTPUT_DIR / "flight_line.json"
    # Airports inferred from where each school's fleet is based (separate field; listed
    # locations untouched). After the PDF pass so its tails count.
    import infer_airports
    infer_airports.apply(flight_line)
    if not dry_run:
        write_json_atomic(fl_path, flight_line)
        print(f"  Saved: {fl_path}")

    # Compute total stats
    total_schools = len(flight_line)
    total_fleet = sum(s.get("fleet_count", 0) for s in flight_line)
    total_nn_all = sum(s.get("n_number_count", 0) for s in flight_line)
    total_adsb_all = sum(s.get("adsb_active_count", 0) for s in flight_line)

    diagnostics = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "domains_with_new_nn": len(domains_with_nn),
        "domains_updated": updated,
        "total_new_nn": total_new_nn,
        "total_new_adsb_active": total_new_adsb,
        "flight_line_totals": {
            "schools": total_schools,
            "fleet": total_fleet,
            "n_numbers": total_nn_all,
            "adsb_active": total_adsb_all,
        },
        "per_domain": {
            domain: {
                "n_numbers_added": len(info["n_numbers"]),
                "source_urls": info.get("source_urls", []),
                "text_length": info.get("text_length", 0),
                "used_sonnet": bool(info.get("sonnet_fleet")),
            }
            for domain, info in domains_with_nn.items()
        },
    }

    diag_path = OUTPUT_DIR / "pdf_extraction_diagnostics.json"
    if not dry_run:
        write_json_atomic(diag_path, diagnostics)
        print(f"  Saved: output/pdf_extraction_diagnostics.json")

    return diagnostics


# ── Main ─────────────────────────────────────────────────────────


PHASES = ["targets", "download", "extract", "batch", "merge"]


def main():
    parser = argparse.ArgumentParser(description="Extract N-numbers from PDF rate sheets")
    parser.add_argument(
        "--phase",
        choices=PHASES,
        default="merge",
        help="Run up to this phase (default: merge = all phases)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show stats only, no downloads or modifications",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="No network and no API calls: reuse downloaded PDFs and cached batch results only",
    )
    parser.add_argument(
        "--in",
        dest="in_path",
        default=None,
        help="Input path (default: output/flight_line_base.json)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output path (default: output/flight_line.json)",
    )
    args = parser.parse_args()

    phase_idx = PHASES.index(args.phase)

    # Load the base build produced by 21_build_flight_line.py
    fl_path = Path(args.in_path) if args.in_path else OUTPUT_DIR / "flight_line_base.json"
    if not fl_path.exists():
        print(f"ERROR: {fl_path} not found — run 21_build_flight_line.py first")
        sys.exit(1)
    flight_line = json.load(open(fl_path))
    print(f"Loaded {fl_path.name}: {len(flight_line)} schools")

    # Phase 1: Build targets
    targets = build_targets(flight_line, dry_run=args.dry_run)
    if phase_idx == 0:
        return

    # Phase 2: Download
    download_progress = download_targets(
        targets, dry_run=args.dry_run, offline=args.offline
    )
    if phase_idx == 1:
        return

    # Phase 3: Extract text + regex
    print("\nLoading FAA data for N-number validation...")
    faa_registry = load_faa_registry()
    extraction_results = extract_text_and_numbers(
        download_progress, faa_registry, dry_run=args.dry_run
    )
    if phase_idx == 2:
        return

    # Phase 4: Sonnet batch for complex PDFs
    run_sonnet_batch(
        extraction_results, faa_registry, dry_run=args.dry_run, offline=args.offline
    )
    if phase_idx == 3:
        return

    # Phase 5: Merge into flight_line.json
    acftref = load_acftref()
    adsb = load_adsb_activity()
    diagnostics = merge_into_flight_line(
        flight_line,
        extraction_results,
        faa_registry,
        acftref,
        adsb,
        dry_run=args.dry_run,
        out_path=args.out,
    )

    # Summary
    print("\n═══ Summary ═══")
    if args.dry_run:
        print("  (dry run — no changes made)")
    else:
        totals = diagnostics.get("flight_line_totals", {})
        print(f"  Schools: {totals.get('schools', 0):,}")
        print(f"  Fleet:   {totals.get('fleet', 0):,}")
        print(f"  N-nums:  {totals.get('n_numbers', 0):,}")
        print(f"  ADS-B:   {totals.get('adsb_active', 0):,}")


if __name__ == "__main__":
    main()
