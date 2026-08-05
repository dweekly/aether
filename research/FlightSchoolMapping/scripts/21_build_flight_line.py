#!/usr/bin/env python3
"""
21_build_flight_line.py — Clean-slate rebuild of flight_line.json.

Merges AOPA directory, FAA registry, three extraction pipelines (expansion,
browser, HTTP-recovered), and ADS-B activity into a single canonical output.

Every school is keyed by normalized domain — no positional indices anywhere.
The HTTP batch extraction results have a broken positional index, so we recover
them via source_url parsing, contact matching, and fuzzy name matching.

Data sources:
  - output/aopa_flight_schools.json          (AOPA directory: 3,667 schools/clubs)
  - output/faa_discovered_schools.json       (FAA registry: 1,819 entities)
  - output/expansion_extract.jsonl           (expansion crawl: domain embedded)
  - output/fleet_browser_extract_results.jsonl + index  (browser batch: explicit mapping)
  - output/fleet_extract_results.jsonl       (HTTP batch: broken index, needs recovery)
  - output/faa_master.csv                    (FAA registration database)
  - output/ACFTREF.txt                       (FAA aircraft type reference)
  - output/adsb_activity.csv                 (ADS-B surveillance activity)

Output:
  - output/flight_line_base.json             (fleet data, before PDF overlay)
  - output/build_diagnostics.json            (recovery stats, merge info)
  - output/html_cache_index.json             (domain → cached HTML mapping)
  - output/unrecovered_extractions.json      (HTTP results we couldn't map)

This writes flight_line_base.json, NOT the canonical flight_line.json.
22_pdf_rate_sheets.py reads the base and writes flight_line.json on top of it.
Keeping the two separate means neither script mutates a file it also reads,
which is what lets `make` model the pipeline as a dependency graph.

Usage:
    python 21_build_flight_line.py              # full rebuild
    python 21_build_flight_line.py --dry-run    # show stats only
    python 21_build_flight_line.py --diff       # compare to previous build
    python 21_build_flight_line.py --out PATH   # write somewhere else
"""

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

OUTPUT_DIR = Path(__file__).parent.parent / "output"

# Domains that are platforms, not school websites
PLATFORM_DOMAINS = {
    "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "linkedin.com", "tiktok.com",
    "wixsite.com", "weebly.com", "squarespace.com",
    "wordpress.com", "blogspot.com", "tripod.com",
    "godaddysites.com", "sites.google.com", "google.com",
    "yelp.com", "yellowpages.com", "bbb.org",
}


# ── Utilities ────────────────────────────────────────────────────

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


def normalize_phone(phone: str) -> str:
    """Strip a phone number to just digits for matching."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) >= 7 else ""


def normalize_name(name: str) -> str:
    """Normalize a school/entity name for matching."""
    n = name.lower().strip()
    for suffix in ("llc", "inc", "corp", "ltd", "co", "company", "l.l.c.", "l.l.c"):
        n = re.sub(rf"\b{re.escape(suffix)}\b\.?", "", n)
    n = re.sub(r"[^\w\s]", "", n)
    return re.sub(r"\s+", " ", n).strip()


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

    # Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find JSON object
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    # Handle truncated JSON
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


def normalize_nn(nn: str) -> str:
    """Normalize an N-number: uppercase, ensure N prefix."""
    if not nn:
        return ""
    nn = nn.upper().strip()
    if nn and not nn.startswith("N"):
        nn = f"N{nn}"
    return nn


# ── Phase 1: Build School Directory ─────────────────────────────

def build_school_directory():
    """
    Build unified school directory from AOPA + FAA sources.
    Returns:
        directory: dict[domain, SchoolRecord]
        aopa_by_phone: dict[phone_digits, domain]
        aopa_by_email: dict[email, domain]
        aopa_by_name_state: dict[(normalized_name, state), domain]
        faa_only_schools: list of FAA entities with no website match
    """
    print("Phase 1: Building school directory...")

    # Load AOPA
    aopa_path = OUTPUT_DIR / "aopa_flight_schools.json"
    aopa_schools = json.loads(aopa_path.read_text()) if aopa_path.exists() else []
    print(f"  AOPA schools/clubs loaded: {len(aopa_schools):,}")

    directory = {}  # domain -> record
    aopa_by_phone = {}
    aopa_by_email = {}
    aopa_by_name_state = {}
    aopa_no_domain = []

    for school in aopa_schools:
        domain = normalize_domain(school.get("website", ""))

        # Build location from AOPA fields
        locations = []
        airport_id = school.get("airport_icao") or school.get("airport_id", "")
        if airport_id:
            locations.append({
                "airport_id": airport_id,
                "airport_name": school.get("airport_name", ""),
                "lat": school.get("latitude"),
                "lon": school.get("longitude"),
                "label": "AOPA listed",
            })

        # Build address string
        addr_parts = [school.get("address1", ""), school.get("city", ""),
                      f"{school.get('state', '')} {school.get('zip', '')}".strip()]
        address = ", ".join(p for p in addr_parts if p)

        record = {
            "name": school.get("name", ""),
            "domain": domain,
            "aopa_type": school.get("aopa_type", ""),
            "aopa_id": (school.get("aopa_url") or "").split("/")[-1] if school.get("aopa_url") else "",
            "locations": locations,
            "contact": {
                "phone": school.get("phone", ""),
                "email": school.get("email", ""),
                "website": school.get("website", ""),
                "address": address,
            },
            "faa_entities": [],
        }

        if domain:
            # Keep the one with more info if duplicate domain
            if domain not in directory:
                directory[domain] = record
            phone = normalize_phone(school.get("phone", ""))
            if phone:
                aopa_by_phone[phone] = domain
            email = (school.get("email") or "").lower().strip()
            if email:
                aopa_by_email[email] = domain
            name_key = (normalize_name(school.get("name", "")), school.get("state", "").upper()[:2])
            if name_key[0]:
                aopa_by_name_state[name_key] = domain
        else:
            aopa_no_domain.append(record)

    print(f"  AOPA with domain: {len(directory):,}, without: {len(aopa_no_domain):,}")
    print(f"  Phone index: {len(aopa_by_phone):,}, email index: {len(aopa_by_email):,}")

    # Load FAA discovered schools
    faa_path = OUTPUT_DIR / "faa_discovered_schools.json"
    faa_entities = json.loads(faa_path.read_text()) if faa_path.exists() else []
    print(f"  FAA discovered entities: {len(faa_entities):,}")

    faa_only_schools = []
    faa_matched = 0
    for entity in faa_entities:
        name = entity.get("registrant_name", "")
        state = entity.get("state", "").upper()[:2]
        name_key = (normalize_name(name), state)

        # Try to match to AOPA directory
        matched_domain = aopa_by_name_state.get(name_key)
        if matched_domain and matched_domain in directory:
            directory[matched_domain]["faa_entities"].append(entity)
            faa_matched += 1
        else:
            faa_only_schools.append(entity)

    print(f"  FAA matched to AOPA: {faa_matched:,}, FAA-only: {len(faa_only_schools):,}")

    return directory, aopa_by_phone, aopa_by_email, aopa_by_name_state, faa_only_schools


# ── Phase 2: Recover HTTP Batch Extractions ──────────────────────

def recover_http_extractions(directory, aopa_by_phone, aopa_by_email, aopa_by_name_state):
    """
    Recover the HTTP batch results whose positional index is broken.
    Returns:
        recovered: dict[domain, parsed_content]
        unrecovered: list of parsed results we couldn't map
        stats: dict of recovery method counts
    """
    print("\nPhase 2: Recovering HTTP batch extractions...")

    results_path = OUTPUT_DIR / "fleet_extract_results.jsonl"
    if not results_path.exists():
        print("  WARNING: fleet_extract_results.jsonl not found")
        return {}, [], {}

    with open(results_path) as f:
        batch_results = [json.loads(line) for line in f if line.strip()]
    print(f"  HTTP batch results: {len(batch_results):,}")

    recovered = {}  # domain -> content
    unrecovered = []
    stats = {"source_url": 0, "contact_phone": 0, "contact_email": 0,
             "name_match": 0, "failed": 0, "parse_error": 0, "no_succeed": 0}

    for result in batch_results:
        result_body = result.get("result", {})
        if result_body.get("type") != "succeeded":
            stats["no_succeed"] += 1
            continue

        message = result_body.get("message", {})
        text_parts = []
        for block in message.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block["text"])
        response_text = "\n".join(text_parts)

        parsed = parse_llm_response(response_text)
        if not parsed:
            stats["parse_error"] += 1
            continue

        domain = None

        # Strategy 1: source_url in fleet items
        for aircraft in parsed.get("fleet", []):
            url = aircraft.get("source_url", "")
            if url:
                d = normalize_domain(url)
                if d:
                    domain = d
                    break

        if domain:
            stats["source_url"] += 1
        else:
            # Strategy 2: phone match
            contact = parsed.get("contact", {})
            phone = normalize_phone(contact.get("phone", ""))
            if phone and phone in aopa_by_phone:
                domain = aopa_by_phone[phone]
                stats["contact_phone"] += 1

        if not domain:
            # Strategy 3: email match
            contact = parsed.get("contact", {})
            email = (contact.get("email") or "").lower().strip()
            if email and email in aopa_by_email:
                domain = aopa_by_email[email]
                stats["contact_email"] += 1

        if not domain:
            # Strategy 4: name match (fuzzy, must be unique enough)
            school_name = parsed.get("canonical_name", "")
            if school_name:
                norm = normalize_name(school_name)
                best_score = 0
                best_domain = None
                for (aopa_name, _state), d in aopa_by_name_state.items():
                    score = SequenceMatcher(None, norm, aopa_name).ratio()
                    if score > best_score:
                        best_score = score
                        best_domain = d
                if best_score >= 0.85 and best_domain:
                    domain = best_domain
                    stats["name_match"] += 1

        if domain:
            # Keep richer extraction for each domain
            existing = recovered.get(domain)
            if existing:
                old_nn = sum(1 for a in existing.get("fleet", []) if a.get("n_number"))
                new_nn = sum(1 for a in parsed.get("fleet", []) if a.get("n_number"))
                if new_nn <= old_nn:
                    continue
            recovered[domain] = parsed
        else:
            stats["failed"] += 1
            # Only keep unrecovered results that have fleet data
            if parsed.get("fleet"):
                unrecovered.append({
                    "canonical_name": parsed.get("canonical_name", ""),
                    "school_type": parsed.get("school_type", ""),
                    "fleet_count": len(parsed.get("fleet", [])),
                    "n_numbers": [a.get("n_number") for a in parsed.get("fleet", []) if a.get("n_number")],
                    "contact": parsed.get("contact", {}),
                })

    print(f"  Recovery results:")
    print(f"    source_url:    {stats['source_url']:,}")
    print(f"    phone match:   {stats['contact_phone']:,}")
    print(f"    email match:   {stats['contact_email']:,}")
    print(f"    name match:    {stats['name_match']:,}")
    print(f"    failed:        {stats['failed']:,}")
    print(f"    parse errors:  {stats['parse_error']:,}")
    print(f"    not succeeded: {stats['no_succeed']:,}")
    print(f"  Recovered: {len(recovered):,} domains, unrecovered with fleet: {len(unrecovered):,}")

    return recovered, unrecovered, stats


# ── Phase 3: Load All Extractions ────────────────────────────────

def load_all_extractions(http_recovered):
    """
    Load all three extraction sources into unified format keyed by domain.
    Returns dict[domain, list of (source, parsed_content)]
    """
    print("\nPhase 3: Loading all extractions...")
    extractions = defaultdict(list)  # domain -> [(source, content)]

    # 3a: Expansion crawl (domain embedded)
    exp_path = OUTPUT_DIR / "expansion_extract.jsonl"
    exp_count = 0
    if exp_path.exists():
        for line in exp_path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            domain = rec.get("domain", "")
            data = rec.get("data")
            if not domain or not data:
                continue
            extractions[domain].append(("expansion", data, rec.get("ts", "")))
            exp_count += 1
    print(f"  Expansion: {exp_count:,} results")

    # 3b: Browser batch (explicit index)
    browser_results_path = OUTPUT_DIR / "fleet_browser_extract_results.jsonl"
    browser_index_path = OUTPUT_DIR / "fleet_browser_extract_index.json"
    browser_count = 0
    if browser_results_path.exists() and browser_index_path.exists():
        index = json.loads(browser_index_path.read_text())
        id_to_meta = {e["custom_id"]: e for e in index}

        with open(browser_results_path) as f:
            for line in f:
                if not line.strip():
                    continue
                result = json.loads(line)
                cid = result.get("custom_id", "")
                meta = id_to_meta.get(cid)
                if not meta:
                    continue
                result_body = result.get("result", {})
                if result_body.get("type") != "succeeded":
                    continue
                message = result_body.get("message", {})
                text_parts = []
                for block in message.get("content", []):
                    if block.get("type") == "text":
                        text_parts.append(block["text"])
                parsed = parse_llm_response("\n".join(text_parts))
                if not parsed:
                    continue
                domain = meta.get("domain", normalize_domain(meta.get("school_website", "")))
                if not domain:
                    continue
                # Inject school name from index metadata
                if meta.get("school_name"):
                    parsed["_index_school_name"] = meta["school_name"]
                extractions[domain].append(("browser", parsed, ""))
                browser_count += 1
    print(f"  Browser:   {browser_count:,} results")

    # 3c: HTTP recovered (from Phase 2)
    for domain, content in http_recovered.items():
        extractions[domain].append(("http_recovered", content, ""))
    print(f"  HTTP recv: {len(http_recovered):,} results")

    domains_with_fleet = sum(
        1 for exts in extractions.values()
        if any(e[1].get("fleet") for e in exts)
    )
    print(f"  Total domains with extractions: {len(extractions):,}")
    print(f"  Domains with fleet data: {domains_with_fleet:,}")

    return dict(extractions)


# ── Phase 4: Merge & Deduplicate ─────────────────────────────────

def score_extraction(content: dict) -> tuple:
    """Score an extraction by richness: (n_number_count, fleet_size)."""
    fleet = content.get("fleet", [])
    nn_count = sum(1 for a in fleet if a.get("n_number"))
    return (nn_count, len(fleet))


def dedup_fleet(fleet_list: list[dict]) -> list[dict]:
    """Deduplicate fleet by N-number, keeping the entry with most detail."""
    seen_nn = {}
    no_nn = []
    for aircraft in fleet_list:
        nn = normalize_nn(aircraft.get("n_number", ""))
        if nn:
            aircraft["n_number"] = nn
            existing = seen_nn.get(nn)
            if existing:
                # Keep whichever has more fields populated
                old_fields = sum(1 for v in existing.values() if v)
                new_fields = sum(1 for v in aircraft.values() if v)
                if new_fields > old_fields:
                    seen_nn[nn] = aircraft
            else:
                seen_nn[nn] = aircraft
        else:
            no_nn.append(aircraft)
    return list(seen_nn.values()) + no_nn


def merge_extractions(directory, extractions):
    """
    For each domain, merge all extraction results into one canonical fleet.
    Returns dict[domain, merged_record].
    """
    print("\nPhase 4: Merging extractions...")

    merged = {}  # domain -> record
    multi_source = 0

    for domain, ext_list in extractions.items():
        # Sort by richness: best first
        ext_list.sort(key=lambda x: score_extraction(x[1]), reverse=True)
        sources_used = set()
        primary_source, primary_content, primary_ts = ext_list[0]
        sources_used.add(primary_source)

        # Start with the richest extraction's fleet
        combined_fleet = list(primary_content.get("fleet", []))

        # Merge from other sources
        for source, content, _ts in ext_list[1:]:
            sources_used.add(source)
            additional = content.get("fleet", [])
            if additional:
                existing_nns = {normalize_nn(a.get("n_number", "")) for a in combined_fleet if a.get("n_number")}
                for aircraft in additional:
                    nn = normalize_nn(aircraft.get("n_number", ""))
                    if nn and nn not in existing_nns:
                        combined_fleet.append(aircraft)
                        existing_nns.add(nn)
                    elif not nn:
                        combined_fleet.append(aircraft)

        combined_fleet = dedup_fleet(combined_fleet)

        if len(sources_used) > 1:
            multi_source += 1

        # Get best contact info (prefer non-empty)
        best_contact = primary_content.get("contact", {})
        for _source, content, _ts in ext_list[1:]:
            alt = content.get("contact", {})
            if not best_contact.get("phone") and alt.get("phone"):
                best_contact["phone"] = alt["phone"]
            if not best_contact.get("email") and alt.get("email"):
                best_contact["email"] = alt["email"]
            if not best_contact.get("address") and alt.get("address"):
                best_contact["address"] = alt["address"]

        # Prefer directory name if available
        dir_entry = directory.get(domain)
        school_name = ""
        if dir_entry:
            school_name = dir_entry.get("name", "")
        if not school_name:
            school_name = primary_content.get("_index_school_name", "") or primary_content.get("canonical_name", "")

        merged[domain] = {
            "fleet": combined_fleet,
            "contact": best_contact,
            "school_type": primary_content.get("school_type", ""),
            "school_name": school_name,
            "fleet_page_url": primary_content.get("fleet_page_url", ""),
            "rate_sheet_url": primary_content.get("rate_sheet_url", ""),
            "locations": primary_content.get("locations", []),
            "extraction_sources": sorted(sources_used),
        }

    print(f"  Merged domains: {len(merged):,}")
    print(f"  Multi-source domains: {multi_source:,}")
    return merged


# ── Phase 5: FAA Enrichment + ADS-B Cross-Reference ─────────────

def load_faa_registry() -> dict[str, dict]:
    """Load FAA master registry keyed by N-number (with N prefix)."""
    faa_path = OUTPUT_DIR / "faa_master.csv"
    if not faa_path.exists():
        print("  WARNING: faa_master.csv not found")
        return {}

    registry = {}
    with open(faa_path, "rb") as f:
        raw = f.read()

    text = raw.decode("utf-8", errors="replace")
    # Skip BOM if present
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
    """Load ADS-B activity keyed by icao_hex (lowercase)."""
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

    # ADS-B cross-reference
    if mode_s_hex and mode_s_hex in adsb:
        adsb_rec = adsb[mode_s_hex]
        sightings = int(adsb_rec.get("total_sightings", 0) or 0)
        aircraft["adsb"] = {
            "active": sightings > 0,
            "total_sightings": sightings,
            "home_airport": (adsb_rec.get("home_airport") or "").strip(),
        }


# ── Phase 6: Build HTML Cache Index ─────────────────────────────

def build_html_cache_index():
    """Map domain → {sources[], total_files} from cached HTML directories."""
    print("\nPhase 6: Building HTML cache index...")

    cache_dirs = {
        "fleet_html": OUTPUT_DIR / "fleet_html",
        "fleet_html_raw": OUTPUT_DIR / "fleet_html_raw",
        "fleet_browser_html_raw": OUTPUT_DIR / "fleet_browser_html_raw",
        "fleet_browser_text": OUTPUT_DIR / "fleet_browser_text",
        "expansion_html": OUTPUT_DIR / "expansion_html",
    }

    index = {}  # domain -> {sources, total_files}
    for cache_name, cache_path in cache_dirs.items():
        if not cache_path.exists():
            continue
        count = 0
        for domain_dir in cache_path.iterdir():
            if not domain_dir.is_dir():
                continue
            domain = domain_dir.name
            files = list(domain_dir.iterdir())
            if not files:
                continue
            if domain not in index:
                index[domain] = {"sources": [], "total_files": 0}
            index[domain]["sources"].append(cache_name)
            index[domain]["total_files"] += len(files)
            count += 1
        print(f"  {cache_name}: {count:,} domains")

    print(f"  Total cached domains: {len(index):,}")
    return index


# ── Phase 7: Assemble & Output ───────────────────────────────────

def write_json_atomic(path, obj):
    """Write JSON via a temp file + rename, so an interrupted build never
    leaves a truncated artifact behind for the next stage to read."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    tmp.replace(path)


def build_flight_line(dry_run=False, diff=False, out_path=None):
    ts = datetime.now(timezone.utc).isoformat()
    print(f"=== Build flight_line.json — {ts} ===\n")

    # Phase 1: School directory
    directory, aopa_by_phone, aopa_by_email, aopa_by_name_state, faa_only_schools = (
        build_school_directory()
    )

    # Phase 2: Recover HTTP batch
    http_recovered, unrecovered, recovery_stats = recover_http_extractions(
        directory, aopa_by_phone, aopa_by_email, aopa_by_name_state
    )

    # Phase 3: Load all extractions
    extractions = load_all_extractions(http_recovered)

    # Phase 4: Merge
    merged = merge_extractions(directory, extractions)

    # Phase 5: FAA + ADS-B
    print("\nPhase 5: FAA enrichment + ADS-B cross-reference...")
    faa_registry = load_faa_registry()
    acftref = load_acftref()
    adsb = load_adsb_activity()

    # Enrich web-extracted fleet
    for domain, rec in merged.items():
        for aircraft in rec.get("fleet", []):
            enrich_aircraft(aircraft, faa_registry, acftref, adsb)

    # Assemble final output: combine directory + merged extractions
    print("\nAssembling final output...")
    flight_line = []
    domains_seen = set()

    # First: all domains that have extraction data
    for domain in sorted(merged.keys()):
        rec = merged[domain]
        dir_entry = directory.get(domain, {})
        domains_seen.add(domain)

        fleet = rec["fleet"]
        nn_count = sum(1 for a in fleet if a.get("n_number"))
        adsb_active = sum(
            1 for a in fleet
            if a.get("adsb", {}).get("active")
        )

        # Merge directory metadata
        locations = rec.get("locations") or []
        if not locations and dir_entry.get("locations"):
            locations = dir_entry["locations"]

        contact = rec.get("contact", {})
        dir_contact = dir_entry.get("contact", {})
        if dir_contact:
            if not contact.get("phone") and dir_contact.get("phone"):
                contact["phone"] = dir_contact["phone"]
            if not contact.get("email") and dir_contact.get("email"):
                contact["email"] = dir_contact["email"]
            if not contact.get("website") and dir_contact.get("website"):
                contact["website"] = dir_contact["website"]
            if not contact.get("address") and dir_contact.get("address"):
                contact["address"] = dir_contact["address"]
        if not contact.get("website"):
            contact["website"] = f"https://{domain}/"

        school = {
            "name": rec.get("school_name") or dir_entry.get("name", domain),
            "domain": domain,
            "aopa_type": dir_entry.get("aopa_type", ""),
            "aopa_id": dir_entry.get("aopa_id", ""),
            "locations": locations,
            "contact": contact,
            "fleet": fleet,
            "extraction_sources": rec.get("extraction_sources", []),
            "fleet_count": len(fleet),
            "n_number_count": nn_count,
            "adsb_active_count": adsb_active,
        }
        flight_line.append(school)

    # Second: directory-only schools (no extraction but have AOPA entry)
    dir_only = 0
    for domain, dir_entry in sorted(directory.items()):
        if domain in domains_seen:
            continue
        domains_seen.add(domain)
        dir_only += 1
        school = {
            "name": dir_entry.get("name", domain),
            "domain": domain,
            "aopa_type": dir_entry.get("aopa_type", ""),
            "aopa_id": dir_entry.get("aopa_id", ""),
            "locations": dir_entry.get("locations", []),
            "contact": dir_entry.get("contact", {}),
            "fleet": [],
            "extraction_sources": [],
            "fleet_count": 0,
            "n_number_count": 0,
            "adsb_active_count": 0,
        }

        # Check if FAA entities have aircraft
        for faa_ent in dir_entry.get("faa_entities", []):
            for ac in faa_ent.get("aircraft", []):
                nn = normalize_nn(ac.get("n_number", ""))
                if nn:
                    aircraft_entry = {
                        "n_number": nn,
                        "display_name": ac.get("type", ""),
                        "source_url": "",
                    }
                    enrich_aircraft(aircraft_entry, faa_registry, acftref, adsb)
                    school["fleet"].append(aircraft_entry)

        school["fleet"] = dedup_fleet(school["fleet"])
        school["fleet_count"] = len(school["fleet"])
        school["n_number_count"] = sum(1 for a in school["fleet"] if a.get("n_number"))
        school["adsb_active_count"] = sum(
            1 for a in school["fleet"] if a.get("adsb", {}).get("active")
        )
        if school["fleet"]:
            school["extraction_sources"] = ["faa_registry"]
        flight_line.append(school)

    # Third: FAA-only schools (no website, no AOPA match)
    faa_only_added = 0
    for entity in faa_only_schools:
        name = entity.get("registrant_name", "")
        if not name:
            continue
        # Create a pseudo-domain from name for keying
        pseudo_domain = f"_faa_{re.sub(r'[^a-z0-9]', '_', name.lower())}"

        fleet = []
        for ac in entity.get("aircraft", []):
            nn = normalize_nn(ac.get("n_number", ""))
            if nn:
                aircraft_entry = {
                    "n_number": nn,
                    "display_name": ac.get("type", ""),
                    "source_url": "",
                }
                enrich_aircraft(aircraft_entry, faa_registry, acftref, adsb)
                fleet.append(aircraft_entry)

        fleet = dedup_fleet(fleet)
        if not fleet:
            continue

        school = {
            "name": name,
            "domain": "",
            "aopa_type": "",
            "aopa_id": "",
            "locations": [],
            "contact": {
                "phone": "",
                "email": "",
                "website": "",
                "address": f"{entity.get('city', '')}, {entity.get('state', '')}".strip(", "),
            },
            "fleet": fleet,
            "extraction_sources": ["faa_registry"],
            "fleet_count": len(fleet),
            "n_number_count": len(fleet),
            "adsb_active_count": sum(
                1 for a in fleet if a.get("adsb", {}).get("active")
            ),
        }
        flight_line.append(school)
        faa_only_added += 1

    print(f"  Directory-only schools added: {dir_only:,}")
    print(f"  FAA-only schools added: {faa_only_added:,}")

    # Sort by fleet count descending, then name
    flight_line.sort(key=lambda s: (-s["fleet_count"], s.get("name", "")))

    # Phase 6: HTML cache index
    html_cache = build_html_cache_index()

    # === Stats ===
    total_schools = len(flight_line)
    with_fleet = sum(1 for s in flight_line if s["fleet_count"] > 0)
    total_aircraft = sum(s["fleet_count"] for s in flight_line)
    unique_nns = set()
    for s in flight_line:
        for a in s.get("fleet", []):
            nn = a.get("n_number", "")
            if nn:
                unique_nns.add(nn)
    with_adsb = sum(s["adsb_active_count"] for s in flight_line)
    with_rates = sum(
        1 for s in flight_line for a in s.get("fleet", [])
        if a.get("wet_rate_hr") or a.get("dry_rate_hr")
    )

    # Type breakdown
    types = defaultdict(int)
    for s in flight_line:
        t = s.get("aopa_type") or "(no AOPA type)"
        types[t] += 1

    # Source breakdown
    source_counts = defaultdict(int)
    for s in flight_line:
        for src in s.get("extraction_sources", ["(none)"]):
            source_counts[src] += 1

    print(f"\n{'='*50}")
    print(f"=== BUILD COMPLETE ===")
    print(f"{'='*50}")
    print(f"  Schools/clubs:    {total_schools:,}")
    print(f"  With fleet:       {with_fleet:,}")
    print(f"  Total aircraft:   {total_aircraft:,}")
    print(f"  Unique N-numbers: {len(unique_nns):,}")
    print(f"  ADS-B active:     {with_adsb:,}")
    print(f"  With rates:       {with_rates:,}")
    print(f"\n  AOPA type breakdown:")
    for t, c in sorted(types.items(), key=lambda x: -x[1]):
        print(f"    {t}: {c:,}")
    print(f"\n  Extraction source breakdown:")
    for src, c in sorted(source_counts.items(), key=lambda x: -x[1]):
        print(f"    {src}: {c:,}")

    out_path = Path(out_path) if out_path else OUTPUT_DIR / "flight_line_base.json"

    # Diff — compare against the previous build of *this* artifact
    if diff:
        existing_path = out_path
        if existing_path.exists():
            existing = json.loads(existing_path.read_text())
            old_count = len(existing)
            old_nns = set()
            for s in existing:
                for a in s.get("fleet", []):
                    nn = a.get("n_number", "")
                    if nn:
                        old_nns.add(nn)
            print(f"\n  Comparison to existing {existing_path.name}:")
            print(f"    Schools: {old_count:,} -> {total_schools:,} ({total_schools - old_count:+,})")
            print(f"    N-numbers: {len(old_nns):,} -> {len(unique_nns):,} ({len(unique_nns) - len(old_nns):+,})")
            lost = old_nns - unique_nns
            gained = unique_nns - old_nns
            if lost:
                print(f"    Lost: {len(lost):,}")
                if len(lost) <= 20:
                    print(f"      {sorted(lost)}")
            if gained:
                print(f"    Gained: {len(gained):,}")

    # Write outputs
    if not dry_run:
        # Diagnostics
        diagnostics = {
            "build_ts": ts,
            "total_schools": total_schools,
            "with_fleet": with_fleet,
            "total_aircraft": total_aircraft,
            "unique_n_numbers": len(unique_nns),
            "adsb_active": with_adsb,
            "with_rates": with_rates,
            "recovery_stats": recovery_stats,
            "unrecovered_with_fleet": len(unrecovered),
            "dir_only_schools": dir_only,
            "faa_only_schools": faa_only_added,
            "aopa_type_breakdown": dict(types),
            "source_breakdown": dict(source_counts),
        }

        write_json_atomic(out_path, flight_line)
        print(f"\n  Written: {out_path}")

        diag_path = OUTPUT_DIR / "build_diagnostics.json"
        write_json_atomic(diag_path, diagnostics)
        print(f"  Written: {diag_path}")

        cache_path = OUTPUT_DIR / "html_cache_index.json"
        write_json_atomic(cache_path, html_cache)
        print(f"  Written: {cache_path}")

        unrec_path = OUTPUT_DIR / "unrecovered_extractions.json"
        write_json_atomic(unrec_path, unrecovered)
        print(f"  Written: {unrec_path}")
    else:
        print(f"\n  DRY RUN — no files written")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean-slate rebuild of flight_line_base.json")
    parser.add_argument("--dry-run", action="store_true", help="Show stats without writing")
    parser.add_argument("--diff", action="store_true", help="Compare to the previous build")
    parser.add_argument(
        "--out",
        default=None,
        help="Output path (default: output/flight_line_base.json)",
    )
    args = parser.parse_args()
    build_flight_line(dry_run=args.dry_run, diff=args.diff, out_path=args.out)
