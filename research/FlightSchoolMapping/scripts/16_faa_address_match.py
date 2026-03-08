#!/usr/bin/env python3
"""
16_faa_address_match.py — Discover fleet aircraft via FAA registry address matching.

Cross-references flight school contact addresses against the FAA aircraft
registry (MASTER.txt / faa_master.csv) to find aircraft registered at the
same physical address. This catches aircraft that are:
  - Owned by the flight school directly
  - Owned by LLCs at the school's address (common for liability)
  - Part of leaseback fleets based at the school

This is especially valuable for schools where web scraping missed fleet data
(JS-rendered pages, captcha-blocked sites, photos-only tail numbers).

Usage:
    python 16_faa_address_match.py                # report matches
    python 16_faa_address_match.py --apply        # merge high-confidence matches into flight_line
    python 16_faa_address_match.py --threshold 0.5  # require 50% owner-name similarity
"""

import argparse
import csv
import json
import re
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR.parent / "output"
FLIGHT_LINE = OUTPUT_DIR / "flight_line.json"
FAA_MASTER = OUTPUT_DIR / "faa_master.csv"

# Aircraft type codes from FAA registry (column index 2 = MFR MDL CODE)
# We'll use the ACFTREF.txt for model lookups if available
ACFTREF = OUTPUT_DIR / "ACFTREF.txt"


def normalize_addr(street: str, city: str, state: str) -> str:
    """Normalize address for matching. Returns 'street|city|ST' or ''."""
    s = (street or "").lower().strip()
    c = (city or "").lower().strip()
    st = (state or "").upper().strip()[:2]
    # Remove suite/unit/apt numbers
    s = re.sub(r"\b(ste|suite|unit|apt|bldg|building|#)\s*\S+", "", s).strip()
    # Remove trailing punctuation
    s = s.rstrip(".,")
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s)
    return f"{s}|{c}|{st}" if s and c else ""


def name_similarity(name1: str, name2: str) -> float:
    """Fuzzy similarity between two entity names."""
    a = name1.lower().strip()
    b = name2.lower().strip()
    # Remove common suffixes
    for suffix in ("llc", "inc", "corp", "ltd", "co", "company"):
        a = re.sub(rf"\b{suffix}\b\.?", "", a).strip()
        b = re.sub(rf"\b{suffix}\b\.?", "", b).strip()
    return SequenceMatcher(None, a, b).ratio()


def load_faa_registry() -> dict[str, list[dict]]:
    """Load FAA registry indexed by normalized address."""
    addr_index: dict[str, list[dict]] = defaultdict(list)

    if not FAA_MASTER.exists():
        print(f"Error: {FAA_MASTER} not found")
        return addr_index

    with open(FAA_MASTER, errors="replace") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 12:
                continue
            nn = row[0].strip()
            if not nn or not nn[0].isdigit():
                continue

            owner = row[6].strip()
            street = row[7].strip()
            city = row[9].strip()
            state = row[10].strip()

            key = normalize_addr(street, city, state)
            if key:
                addr_index[key].append(
                    {
                        "n_number": "N" + nn,
                        "owner": owner,
                        "street": street,
                        "city": city,
                        "state": state,
                    }
                )

    return addr_index


def parse_school_address(addr: str) -> tuple[str, str, str]:
    """Parse 'street, city, ST zip' into (street, city, state)."""
    parts = [p.strip() for p in addr.split(",")]
    if len(parts) >= 3:
        street = parts[0]
        city = parts[1]
        state_zip = parts[2].strip()
        state = state_zip[:2] if len(state_zip) >= 2 else ""
        return street, city, state
    elif len(parts) == 2:
        # Could be "City, ST" or "Street, City ST"
        state_zip = parts[1].strip()
        if len(state_zip) >= 2 and state_zip[:2].isalpha():
            # "Street, City ST zip" or "City, ST zip"
            # Try splitting second part on space
            sp = state_zip.split()
            if len(sp) >= 2 and len(sp[-2]) == 2:
                state = sp[-2]
                city = " ".join(sp[:-2]) if len(sp) > 2 else parts[0]
                street = parts[0] if len(sp) > 2 else ""
                return street, city, state
            return parts[0], "", state_zip[:2]
    return "", "", ""


def find_matches(flight_line: list[dict], addr_index: dict) -> list[dict]:
    """Find schools with FAA-registered aircraft at same address."""
    matches = []

    for school in flight_line:
        contact = school.get("contact", {})
        addr = contact.get("address", "")
        if not addr:
            continue

        street, city, state = parse_school_address(addr)
        if not street:
            continue

        key = normalize_addr(street, city, state)
        if not key or key not in addr_index:
            continue

        faa_aircraft = addr_index[key]
        existing_nn = {ac.get("n_number", "") for ac in school.get("fleet", [])}
        new_aircraft = [a for a in faa_aircraft if a["n_number"] not in existing_nn]

        if not faa_aircraft:
            continue

        # Score each aircraft by owner name similarity to school name
        school_name = school.get("school_name", "")
        for ac in faa_aircraft:
            ac["name_similarity"] = name_similarity(school_name, ac["owner"])

        # High-confidence: owner name matches school name
        high_conf = [a for a in new_aircraft if a["name_similarity"] > 0.4]
        low_conf = [a for a in new_aircraft if a["name_similarity"] <= 0.4]

        matches.append(
            {
                "school_name": school_name,
                "school_website": school.get("school_website", ""),
                "school_address": addr,
                "existing_fleet": len(school.get("fleet", [])),
                "faa_total": len(faa_aircraft),
                "new_total": len(new_aircraft),
                "high_confidence": high_conf,
                "low_confidence": low_conf,
            }
        )

    return matches


def print_report(matches: list[dict]):
    """Print summary of FAA address matches."""
    matches.sort(key=lambda m: len(m["high_confidence"]), reverse=True)

    total_high = sum(len(m["high_confidence"]) for m in matches)
    total_low = sum(len(m["low_confidence"]) for m in matches)

    print("=" * 70)
    print("FAA Registry Address Match Report")
    print("=" * 70)
    print(f"  Schools with address matches: {len(matches)}")
    print(f"  High-confidence new aircraft: {total_high}")
    print(f"  Low-confidence new aircraft:  {total_low}")
    print()

    print("High-confidence matches (owner name ≈ school name):")
    print(f"  {'School':<40} {'Exist':>5} {'High':>5} {'Low':>5}")
    print("-" * 70)
    for m in matches:
        if not m["high_confidence"]:
            continue
        print(
            f"  {m['school_name'][:38]:<38} {m['existing_fleet']:>5} "
            f"{len(m['high_confidence']):>5} {len(m['low_confidence']):>5}"
        )
        for ac in m["high_confidence"][:3]:
            print(f"    {ac['n_number']} ({ac['owner'][:40]}) sim={ac['name_similarity']:.2f}")
        if len(m["high_confidence"]) > 3:
            print(f"    ... and {len(m['high_confidence']) - 3} more")

    print()
    print("Schools with only low-confidence matches (shared address, different owner):")
    low_only = [m for m in matches if not m["high_confidence"] and m["low_confidence"]]
    for m in low_only[:15]:
        print(f"  {m['school_name'][:38]:<38}  {len(m['low_confidence']):>3} aircraft at same address")


def apply_matches(
    flight_line: list[dict], matches: list[dict], threshold: float
) -> int:
    """Merge high-confidence FAA matches into flight_line."""
    fl_by_web = {s["school_website"]: s for s in flight_line}
    added = 0

    for m in matches:
        school = fl_by_web.get(m["school_website"])
        if not school:
            continue

        for ac in m["high_confidence"]:
            if ac["name_similarity"] < threshold:
                continue
            school.setdefault("fleet", []).append(
                {
                    "n_number": ac["n_number"],
                    "display_name": "",
                    "description": f"FAA registry: owner={ac['owner']}",
                    "wet_rate_hr": None,
                    "equipment": [],
                    "airport_id": "",
                    "source_url": "",
                    "_source": "faa_address_match",
                    "_owner_similarity": round(ac["name_similarity"], 2),
                }
            )
            added += 1

    return added


def main():
    parser = argparse.ArgumentParser(
        description="FAA registry address matching for fleet discovery"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Merge matches into flight_line"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.4,
        help="Min name similarity for auto-apply (default: 0.4)",
    )
    args = parser.parse_args()

    print("Loading FAA registry...")
    addr_index = load_faa_registry()
    print(f"  {sum(len(v) for v in addr_index.values()):,} aircraft at {len(addr_index):,} addresses")

    with open(FLIGHT_LINE) as f:
        flight_line = json.load(f)
    print(f"  {len(flight_line):,} schools in flight_line")

    matches = find_matches(flight_line, addr_index)
    print_report(matches)

    if args.apply:
        added = apply_matches(flight_line, matches, args.threshold)
        with open(FLIGHT_LINE, "w") as f:
            json.dump(flight_line, f, indent=2)
        print(f"\n  Applied: {added} aircraft added to flight_line")


if __name__ == "__main__":
    main()
