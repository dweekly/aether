#!/usr/bin/env python3
"""
20_rebuild_flight_line.py — Deterministic rebuild of flight_line.json from source artifacts.

Assembles the canonical flight_line.json from scratch using ONLY existing
extraction results and crawl indexes. No API calls. No Sonnet. No crawling.
Fully reproducible.

Source artifacts consumed (all read-only):
  1. output/fleet_crawl_index.json                — HTTP crawl school directory (3,115 schools)
  2. output/fleet_extract_results.jsonl            — Sonnet batch extraction from HTTP crawl
  3. output/fleet_browser_extract_results.jsonl    — Sonnet batch extraction from browser crawl
  4. output/fleet_browser_extract_index.json       — Browser extraction custom_id -> school mapping
  5. output/faa_master.csv                         — FAA aircraft registry
  6. output/ACFTREF.txt                            — FAA aircraft type reference
  7. output/fleet_html_raw/                        — Raw HTML (for image metadata NN extraction)
  8. output/fleet_browser_html_raw/                — Raw browser HTML (for image metadata NN extraction)

Design principles:
  - Each school is keyed by normalized domain (not URL path)
  - One school entry per domain — no merging across domains
  - Each aircraft carries _extraction_method and _extracted_at provenance
  - source_url on each aircraft must match the school's domain
  - No union-find, no shared-NN merging, no transitive closure
  - Schools with multiple URL paths on the same domain are collapsed
  - Platform domains (facebook.com, wixsite.com, etc.) are excluded from domain keying
  - School names come from the AOPA directory, not from Sonnet extraction

Usage:
    python 20_rebuild_flight_line.py              # rebuild from all sources
    python 20_rebuild_flight_line.py --dry-run    # show stats without writing
    python 20_rebuild_flight_line.py --diff       # compare to existing flight_line.json

Output: output/flight_line.json
"""

import argparse
import csv
import io
import json
import re
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

warnings.filterwarnings("ignore", message="It looks like you're using an HTML parser")

OUTPUT_DIR = Path(__file__).parent.parent / "output"

# Domains that are platforms, not school websites — never use as dedup keys
PLATFORM_DOMAINS = {
    "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "linkedin.com", "tiktok.com",
    "wixsite.com", "weebly.com", "squarespace.com",
    "wordpress.com", "blogspot.com", "tripod.com",
    "godaddysites.com", "sites.google.com", "google.com",
    "yelp.com", "yellowpages.com", "bbb.org",
}


def normalize_domain(url: str) -> str:
    """Extract and normalize domain from URL. Returns empty string for platforms."""
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


def parse_llm_response(text: str) -> dict | None:
    """Parse Sonnet's JSON response from extraction results.

    Handles truncated JSON (when Sonnet hits max_tokens mid-output)
    by attempting to close open arrays/objects.
    """
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

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try finding the JSON object
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    # Handle truncated JSON — try to close open structures
    if start >= 0:
        fragment = text[start:]
        # Try progressively closing open brackets
        for suffix in [']}\n', ']\n}\n', '"]\n}\n]\n}\n', '\n]\n}\n']:
            # Strip trailing comma if present
            trimmed = fragment.rstrip().rstrip(",")
            try:
                return json.loads(trimmed + suffix)
            except json.JSONDecodeError:
                continue
        # More aggressive: find last complete fleet entry, close there
        last_brace = fragment.rfind("}")
        if last_brace > 0:
            trimmed = fragment[:last_brace + 1]
            for suffix in ["]}", "]\n}",  "]\n}\n"]:
                try:
                    return json.loads(trimmed + suffix)
                except json.JSONDecodeError:
                    continue

    return None


def load_faa_registry() -> dict[str, dict]:
    """Load FAA master registry keyed by N-number."""
    faa_path = OUTPUT_DIR / "faa_master.csv"
    if not faa_path.exists():
        print("  WARNING: faa_master.csv not found, skipping FAA enrichment")
        return {}

    registry: dict[str, dict] = {}
    with open(faa_path, "rb") as f:
        raw = f.read()

    text = raw.decode("utf-8", errors="replace")
    header_start = text.find("N-NUMBER")
    if header_start > 0:
        text = text[header_start:]

    lines = text.splitlines()
    if not lines:
        return registry

    header = lines[0]
    reader = csv.DictReader(lines[1:], fieldnames=header.split(","))

    for row in reader:
        nn = (row.get("N-NUMBER") or "").strip()
        if nn:
            nn_key = f"N{nn}" if not nn.startswith("N") else nn
            registry[nn_key] = row

    print(f"  FAA registry: {len(registry):,} registrations loaded")
    return registry


def load_acftref() -> dict[str, dict]:
    """Load FAA aircraft type reference keyed by mfr_mdl_code."""
    path = OUTPUT_DIR / "ACFTREF.txt"
    if not path.exists():
        return {}
    ref = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("CODE") or "").strip()
            if code:
                ref[code] = row
    return ref


def enrich_aircraft_faa(aircraft: dict, faa_registry: dict, acftref: dict) -> dict:
    """Add FAA enrichment data to an aircraft entry."""
    nn = aircraft.get("n_number", "")
    if not nn or nn not in faa_registry:
        return aircraft

    reg = faa_registry[nn]
    mfr_code = (reg.get("MFR MDL CODE") or "").strip()
    ref = acftref.get(mfr_code, {})

    aircraft["faa"] = {
        "serial_number": (reg.get("SERIAL NUMBER") or "").strip(),
        "faa_mfr": (ref.get("MFR") or "").strip(),
        "faa_model": (ref.get("MODEL") or "").strip(),
        "mfr_mdl_code": mfr_code,
        "year_mfr": (reg.get("YEAR MFR") or "").strip(),
        "registrant_name": (reg.get("NAME") or "").strip(),
        "registrant_city": (reg.get("CITY") or "").strip(),
        "registrant_state": (reg.get("STATE") or "").strip(),
        "airworthiness_date": (reg.get("AIR WORTH DATE") or "").strip(),
        "cert_issue_date": (reg.get("CERT ISSUE DATE") or "").strip(),
        "expiration_date": (reg.get("EXPIRATION DATE") or "").strip(),
        "mode_s_hex": (reg.get("MODE S CODE HEX") or "").strip(),
        "type_aircraft": (reg.get("TYPE AIRCRAFT") or "").strip(),
        "type_engine": (reg.get("TYPE ENGINE") or "").strip(),
        "status_code": (reg.get("STATUS CODE") or "").strip(),
    }
    return aircraft


def validate_source_url(aircraft: dict, school_domain: str) -> bool:
    """Check if aircraft source_url belongs to the school's domain."""
    source_url = aircraft.get("source_url", "")
    if not source_url:
        return True  # No source_url = benefit of the doubt
    src_domain = normalize_domain(source_url)
    if not src_domain:
        return True  # Platform/CDN URL
    # Allow subdomains and parent domain relationships
    return (src_domain == school_domain
            or src_domain.endswith(f".{school_domain}")
            or school_domain.endswith(f".{src_domain}"))


# ── Phase 1: HTTP Crawl Extractions ──────────────────────────────

def load_http_extractions() -> dict[str, dict]:
    """
    Load Sonnet extraction results from HTTP crawl batch API.
    Returns dict[domain] -> school entry.
    """
    crawl_index_path = OUTPUT_DIR / "fleet_crawl_index.json"
    results_path = OUTPUT_DIR / "fleet_extract_results.jsonl"

    if not crawl_index_path.exists() or not results_path.exists():
        print("  WARNING: HTTP crawl artifacts missing")
        return {}

    crawl_index = json.loads(crawl_index_path.read_text())
    print(f"  HTTP crawl index: {len(crawl_index):,} schools")

    with open(results_path) as f:
        batch_results = [json.loads(line) for line in f]
    print(f"  Batch extraction results: {len(batch_results):,}")

    # Map custom_id -> crawl index entry
    id_to_school: dict[str, dict] = {}
    for i, school in enumerate(crawl_index):
        id_to_school[f"school_{i:04d}"] = school

    schools: dict[str, dict] = {}
    successes = 0
    parse_errors = 0

    for result in batch_results:
        cid = result.get("custom_id", "")
        school_meta = id_to_school.get(cid)
        if not school_meta:
            continue

        result_body = result.get("result", {})
        if result_body.get("type") != "succeeded":
            continue

        message = result_body.get("message", {})
        text_parts = []
        for block in message.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block["text"])
        response_text = "\n".join(text_parts)

        parsed = parse_llm_response(response_text)
        if not parsed:
            parse_errors += 1
            continue

        domain = normalize_domain(school_meta.get("school_website", ""))
        if not domain:
            continue

        fleet_page_url = ""
        for page in school_meta.get("pages", []):
            if page.get("type") == "fleet":
                fleet_page_url = page["url"]
                break

        # Tag provenance
        for aircraft in parsed.get("fleet", []):
            aircraft["_extraction_method"] = "sonnet_text"
            aircraft["_extracted_at"] = school_meta.get("crawl_ts", "")

        # Use directory name, NOT Sonnet's canonical_name
        school_name = school_meta.get("school_name", parsed.get("canonical_name", ""))

        entry = {
            "school_name": school_name,
            "school_website": school_meta["school_website"],
            "school_type": parsed.get("school_type", ""),
            "source": "http_crawl",
            "fleet_page_url": fleet_page_url,
            "contact": parsed.get("contact", {}),
            "locations": parsed.get("locations", []),
            "fleet": parsed.get("fleet", []),
            "scrape_ts": school_meta.get("crawl_ts", ""),
        }

        # Keep entry with more fleet data for this domain
        existing = schools.get(domain)
        if not existing or len(entry.get("fleet", [])) > len(existing.get("fleet", [])):
            schools[domain] = entry
            successes += 1

    print(f"  HTTP extraction: {successes:,} schools with data, {parse_errors:,} parse errors")
    return schools


# ── Phase 2: Browser Crawl Extractions ───────────────────────────

def load_browser_extractions() -> dict[str, dict]:
    """
    Load Sonnet extraction results from browser crawl batch.
    Reads fleet_browser_extract_results.jsonl + fleet_browser_extract_index.json.
    """
    results_path = OUTPUT_DIR / "fleet_browser_extract_results.jsonl"
    index_path = OUTPUT_DIR / "fleet_browser_extract_index.json"

    if not results_path.exists() or not index_path.exists():
        print("  WARNING: Browser extraction results missing")
        print("  Run 09c_browser_extract.py first to generate them")
        return {}

    index = json.loads(index_path.read_text())
    print(f"  Browser extraction index: {len(index):,} schools")

    # Map custom_id -> school metadata
    id_to_school: dict[str, dict] = {}
    for entry in index:
        id_to_school[entry["custom_id"]] = entry

    with open(results_path) as f:
        batch_results = [json.loads(line) for line in f]
    print(f"  Browser extraction results: {len(batch_results):,}")

    schools: dict[str, dict] = {}
    successes = 0
    parse_errors = 0

    for result in batch_results:
        cid = result.get("custom_id", "")
        school_meta = id_to_school.get(cid)
        if not school_meta:
            continue

        result_body = result.get("result", {})
        if not result_body or result_body.get("type") != "succeeded":
            continue

        message = result_body.get("message", {})
        if not message:
            continue
        text_parts = []
        for block in message.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block["text"])
        response_text = "\n".join(text_parts)

        parsed = parse_llm_response(response_text)
        if not parsed:
            parse_errors += 1
            continue

        domain = school_meta.get("domain", normalize_domain(school_meta.get("school_website", "")))
        if not domain:
            continue

        # Tag provenance
        for aircraft in parsed.get("fleet", []):
            aircraft["_extraction_method"] = "sonnet_text"
            aircraft["_extracted_at"] = datetime.now(timezone.utc).isoformat()

        # Use directory name, NOT Sonnet's canonical_name
        school_name = school_meta.get("school_name", parsed.get("canonical_name", ""))

        entry = {
            "school_name": school_name,
            "school_website": school_meta.get("school_website", f"https://{domain}/"),
            "school_type": parsed.get("school_type", ""),
            "source": "browser_crawl",
            "fleet_page_url": "",
            "rate_sheet_url": parsed.get("rate_sheet_url", ""),
            "contact": parsed.get("contact", {}),
            "locations": parsed.get("locations", []),
            "fleet": parsed.get("fleet", []),
            "scrape_ts": datetime.now(timezone.utc).isoformat(),
        }

        # Keep entry with more fleet data
        existing = schools.get(domain)
        if not existing or len(entry.get("fleet", [])) > len(existing.get("fleet", [])):
            schools[domain] = entry
            successes += 1

    print(f"  Browser extraction: {successes:,} schools with data, {parse_errors:,} parse errors")
    return schools


# ── Phase 3: Image Metadata N-Numbers ────────────────────────────

def load_image_nn_extractions(faa_registry: dict) -> dict[str, list[dict]]:
    """
    Scan raw HTML for N-numbers in image metadata using BeautifulSoup.
    Only returns N-numbers that are confirmed in the FAA registry.
    Returns dict[domain] -> list of aircraft dicts.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("  WARNING: BeautifulSoup not available, skipping image NN extraction")
        return {}

    nn_pattern = re.compile(r"\bN\d{1,5}[A-Z]{0,2}\b")
    faa_nn_set = set(faa_registry.keys()) if faa_registry else set()

    results: dict[str, list[dict]] = defaultdict(list)
    seen_per_domain: dict[str, set[str]] = defaultdict(set)
    total_scanned = 0

    for raw_dir in [OUTPUT_DIR / "fleet_html_raw", OUTPUT_DIR / "fleet_browser_html_raw"]:
        if not raw_dir.exists():
            continue
        for domain_dir in sorted(raw_dir.iterdir()):
            if not domain_dir.is_dir():
                continue
            domain = domain_dir.name

            for html_file in domain_dir.glob("*.html"):
                try:
                    html = html_file.read_text(errors="replace")
                    if not html.strip() or html[:20].startswith("%PDF"):
                        continue
                except Exception:
                    continue

                total_scanned += 1
                try:
                    soup = BeautifulSoup(html, "html.parser")
                except Exception:
                    continue

                for img in soup.find_all("img"):
                    src = (img.get("src") or "").strip()
                    alt = (img.get("alt") or "").strip()
                    title = (img.get("title") or "").strip()
                    caption = ""
                    figure = img.find_parent("figure")
                    if figure:
                        fc = figure.find("figcaption")
                        if fc:
                            caption = fc.get_text(strip=True)

                    data_text = " ".join(
                        v for k, v in (img.attrs or {}).items()
                        if k.startswith("data-") and isinstance(v, str)
                    )

                    all_text = f"{src} {alt} {title} {caption} {data_text}"
                    for m in nn_pattern.finditer(all_text):
                        nn = m.group().upper()
                        if nn in seen_per_domain[domain]:
                            continue
                        # Only keep FAA-confirmed N-numbers
                        if faa_nn_set and nn not in faa_nn_set:
                            continue
                        seen_per_domain[domain].add(nn)

                        display_name = ""
                        # Try to extract aircraft info from alt text
                        alt_info = re.match(
                            r"(\d{4})\s+(.+?)\s+\(" + re.escape(nn) + r"\)",
                            alt,
                        )
                        if alt_info:
                            display_name = alt_info.group(2).strip()

                        results[domain].append({
                            "n_number": nn,
                            "display_name": display_name,
                            "_extraction_method": "image_metadata",
                            "_extracted_at": datetime.now(timezone.utc).isoformat(),
                        })

    total = sum(len(v) for v in results.values())
    print(f"  Image metadata: {total_scanned:,} HTML files scanned")
    print(f"  Image metadata N-numbers: {total:,} FAA-confirmed across {len(results):,} domains")
    return dict(results)


# ── Phase 4: FAA Address Matching ────────────────────────────────

def _normalize_addr(street: str, city: str, state: str) -> str:
    """Normalize address for matching. Returns 'street|city|ST' or ''."""
    s = (street or "").lower().strip()
    c = (city or "").lower().strip()
    st = (state or "").upper().strip()[:2]
    s = re.sub(r"\b(ste|suite|unit|apt|bldg|building|#)\s*\S+", "", s).strip()
    s = s.rstrip(".,")
    s = re.sub(r"\s+", " ", s)
    return f"{s}|{c}|{st}" if s and c else ""


def _parse_school_address(addr: str) -> tuple[str, str, str]:
    """Parse 'street, city, ST zip' into (street, city, state)."""
    parts = [p.strip() for p in addr.split(",")]
    if len(parts) >= 3:
        street = parts[0]
        city = parts[1]
        state_zip = parts[2].strip()
        state = state_zip[:2] if len(state_zip) >= 2 else ""
        return street, city, state
    elif len(parts) == 2:
        state_zip = parts[1].strip()
        if len(state_zip) >= 2 and state_zip[:2].isalpha():
            sp = state_zip.split()
            if len(sp) >= 2 and len(sp[-2]) == 2:
                state = sp[-2]
                city = " ".join(sp[:-2]) if len(sp) > 2 else parts[0]
                street = parts[0] if len(sp) > 2 else ""
                return street, city, state
            return parts[0], "", state_zip[:2]
    return "", "", ""


def _name_similarity(name1: str, name2: str) -> float:
    """Fuzzy similarity between two entity names."""
    from difflib import SequenceMatcher
    a = name1.lower().strip()
    b = name2.lower().strip()
    for suffix in ("llc", "inc", "corp", "ltd", "co", "company"):
        a = re.sub(rf"\b{suffix}\b\.?", "", a).strip()
        b = re.sub(rf"\b{suffix}\b\.?", "", b).strip()
    return SequenceMatcher(None, a, b).ratio()


def run_faa_address_matching(flight_line: list[dict], faa_registry: dict, acftref: dict) -> int:
    """
    Cross-reference school contact addresses against FAA registry.
    Mutates flight_line entries in place by adding matched aircraft.
    Returns count of aircraft added.
    """
    # Build address index from FAA registry
    addr_index: dict[str, list[dict]] = defaultdict(list)
    for nn_key, row in faa_registry.items():
        owner = (row.get("NAME") or "").strip()
        street = (row.get("STREET") or "").strip()
        city = (row.get("CITY") or "").strip()
        state = (row.get("STATE") or "").strip()
        key = _normalize_addr(street, city, state)
        if key:
            addr_index[key].append({
                "n_number": nn_key,
                "owner": owner,
            })

    if not addr_index:
        print("  WARNING: Could not build FAA address index")
        return 0

    total_added = 0
    for school in flight_line:
        contact = school.get("contact", {})
        addr = contact.get("address", "")
        if not addr:
            continue

        street, city, state = _parse_school_address(addr)
        if not street:
            continue

        key = _normalize_addr(street, city, state)
        if not key or key not in addr_index:
            continue

        faa_aircraft = addr_index[key]
        existing_nn = {ac.get("n_number", "") for ac in school.get("fleet", [])}
        school_name = school.get("school_name", "")

        for ac in faa_aircraft:
            nn = ac["n_number"]
            if nn in existing_nn:
                continue
            sim = _name_similarity(school_name, ac["owner"])
            if sim < 0.4:
                continue

            # Add aircraft
            new_ac = {
                "n_number": nn,
                "display_name": "",
                "_extraction_method": "faa_address",
                "_extracted_at": datetime.now(timezone.utc).isoformat(),
                "_source": "faa_address_match",
                "_owner_similarity": round(sim, 3),
            }
            enrich_aircraft_faa(new_ac, faa_registry, acftref)
            if new_ac.get("faa", {}).get("faa_mfr"):
                new_ac["display_name"] = f"{new_ac['faa']['faa_mfr']} {new_ac['faa']['faa_model']}".strip()
            school["fleet"].append(new_ac)
            existing_nn.add(nn)
            total_added += 1

    return total_added


# ── Assembly ─────────────────────────────────────────────────────

def merge_fleet(base: list[dict], additions: list[dict]) -> list[dict]:
    """Merge additional aircraft into base fleet, deduping by N-number."""
    seen_nns = set()
    merged = []
    for a in base:
        nn = a.get("n_number", "")
        if nn:
            seen_nns.add(nn)
        merged.append(a)
    for a in additions:
        nn = a.get("n_number", "")
        if nn and nn in seen_nns:
            continue
        if nn:
            seen_nns.add(nn)
        merged.append(a)
    return merged


def build_flight_line(dry_run: bool = False, diff: bool = False):
    """Assemble flight_line.json from all source artifacts."""
    ts = datetime.now(timezone.utc).isoformat()
    print(f"=== Rebuild flight_line.json — {ts} ===\n")

    # Load FAA data first (needed for image NN validation)
    print("Loading FAA registry...")
    faa_registry = load_faa_registry()
    acftref = load_acftref()
    print()

    # Load extraction sources
    print("Loading HTTP crawl extractions...")
    http_schools = load_http_extractions()
    print()

    print("Loading browser crawl extractions...")
    browser_schools = load_browser_extractions()
    print()

    print("Loading image metadata N-numbers...")
    image_nns = load_image_nn_extractions(faa_registry)
    print()

    # Assemble: start with HTTP crawl, overlay browser crawl (richer for JS sites)
    print("Assembling...")
    all_domains = set(http_schools.keys()) | set(browser_schools.keys())
    flight_line: list[dict] = []
    foreign_removed = 0

    for domain in sorted(all_domains):
        http_entry = http_schools.get(domain)
        browser_entry = browser_schools.get(domain)

        # Prefer browser crawl if it has more fleet data (it saw JS-rendered content)
        if browser_entry and http_entry:
            if len(browser_entry.get("fleet", [])) >= len(http_entry.get("fleet", [])):
                entry = browser_entry
            else:
                entry = http_entry
        elif browser_entry:
            entry = browser_entry
        else:
            entry = http_entry

        # Validate: remove any aircraft with foreign source_url
        before = len(entry.get("fleet", []))
        clean_fleet = [a for a in entry.get("fleet", []) if validate_source_url(a, domain)]
        removed = before - len(clean_fleet)
        if removed > 0:
            foreign_removed += removed
        entry["fleet"] = clean_fleet

        # Merge image metadata N-numbers (only FAA-confirmed)
        if domain in image_nns:
            entry["fleet"] = merge_fleet(entry["fleet"], image_nns[domain])

        # FAA enrichment
        for aircraft in entry.get("fleet", []):
            enrich_aircraft_faa(aircraft, faa_registry, acftref)

        flight_line.append(entry)

    # FAA address matching (after assembly, needs contact addresses)
    print("Running FAA address matching...")
    addr_added = run_faa_address_matching(flight_line, faa_registry, acftref)
    print(f"  Added {addr_added:,} aircraft via FAA address matching")

    if foreign_removed:
        print(f"  Removed {foreign_removed:,} aircraft with foreign source_urls")

    # Stats
    total_schools = len(flight_line)
    schools_with_fleet = sum(1 for s in flight_line if s.get("fleet"))
    total_aircraft = sum(len(s.get("fleet", [])) for s in flight_line)
    unique_nns = set()
    for s in flight_line:
        for a in s.get("fleet", []):
            nn = a.get("n_number", "")
            if nn:
                unique_nns.add(nn)
    with_rates = sum(
        1 for s in flight_line for a in s.get("fleet", [])
        if a.get("wet_rate_hr") or a.get("dry_rate_hr")
    )

    # Source breakdown
    sources = defaultdict(int)
    for s in flight_line:
        sources[s.get("source", "(unknown)")] += 1

    # Provenance breakdown
    methods = defaultdict(int)
    for s in flight_line:
        for a in s.get("fleet", []):
            methods[a.get("_extraction_method", "unknown")] += 1

    # School type breakdown
    types = defaultdict(int)
    for s in flight_line:
        types[s.get("school_type", "(unclassified)")] += 1

    print(f"\n=== REBUILD COMPLETE ===")
    print(f"  Schools:          {total_schools:,}")
    print(f"  With fleet:       {schools_with_fleet:,}")
    print(f"  Total aircraft:   {total_aircraft:,}")
    print(f"  Unique N-numbers: {len(unique_nns):,}")
    print(f"  With rates:       {with_rates:,}")
    print(f"\n  Source breakdown:")
    for src, count in sorted(sources.items(), key=lambda x: -x[1]):
        print(f"    {src}: {count:,}")
    print(f"\n  Provenance breakdown:")
    for method, count in sorted(methods.items(), key=lambda x: -x[1]):
        print(f"    {method}: {count:,}")
    print(f"\n  School type breakdown:")
    for stype, count in sorted(types.items(), key=lambda x: -x[1]):
        print(f"    {stype}: {count:,}")

    if diff:
        existing_path = OUTPUT_DIR / "flight_line.json"
        if existing_path.exists():
            existing = json.loads(existing_path.read_text())
            old_schools = len(existing)
            old_nns = set()
            for s in existing:
                for a in s.get("fleet", []):
                    nn = a.get("n_number", "")
                    if nn:
                        old_nns.add(nn)
            print(f"\n  Comparison to existing flight_line.json:")
            print(f"    Schools: {old_schools:,} -> {total_schools:,} ({total_schools - old_schools:+,})")
            print(f"    N-numbers: {len(old_nns):,} -> {len(unique_nns):,} ({len(unique_nns) - len(old_nns):+,})")
            lost = old_nns - unique_nns
            gained = unique_nns - old_nns
            if lost:
                print(f"    Lost N-numbers: {len(lost):,}")
                if len(lost) <= 30:
                    print(f"      {sorted(lost)}")
            if gained:
                print(f"    Gained N-numbers: {len(gained):,}")

    if not dry_run:
        out_path = OUTPUT_DIR / "flight_line.json"
        with open(out_path, "w") as f:
            json.dump(flight_line, f, indent=2)
        print(f"\n  Written to {out_path}")
    else:
        print(f"\n  DRY RUN — no file written")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild flight_line.json from source artifacts")
    parser.add_argument("--dry-run", action="store_true", help="Show stats without writing")
    parser.add_argument("--diff", action="store_true", help="Compare to existing flight_line.json")
    args = parser.parse_args()

    build_flight_line(dry_run=args.dry_run, diff=args.diff)
