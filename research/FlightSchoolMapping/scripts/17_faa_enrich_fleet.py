#!/usr/bin/env python3
"""
17_faa_enrich_fleet.py — Enrich flight_line aircraft with FAA registry data.

Looks up each N-number in the FAA releasable database (faa_master.csv) and
adds registration details: manufacturer, model, year, Mode S hex code,
registrant info, airworthiness dates, etc.

This enrichment is required for ADS-B cross-validation (script 14) which
uses mode_s_hex to match aircraft against ADS-B observations.

Usage:
    python 17_faa_enrich_fleet.py           # enrich all aircraft missing FAA data
    python 17_faa_enrich_fleet.py --force   # re-enrich all aircraft (overwrite)
    python 17_faa_enrich_fleet.py --stats   # just print coverage stats
"""

import argparse
import csv
import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR.parent / "output"
FLIGHT_LINE = OUTPUT_DIR / "flight_line.json"
FAA_MASTER = OUTPUT_DIR / "faa_master.csv"

# Column indices in faa_master.csv
# N-NUMBER, SERIAL NUMBER, MFR MDL CODE, ENG MFR MDL, YEAR MFR, TYPE REGISTRANT,
# NAME, STREET, STREET2, CITY, STATE, ZIP CODE, REGION, COUNTY, COUNTRY,
# LAST ACTION DATE, CERT ISSUE DATE, CERTIFICATION, TYPE AIRCRAFT, TYPE ENGINE,
# STATUS CODE, MODE S CODE, FRACT OWNER, AIR WORTH DATE, ...EXPIRATION DATE
COL = {
    "n_number": 0,
    "serial_number": 1,
    "mfr_mdl_code": 2,
    "eng_mfr_mdl": 3,
    "year_mfr": 4,
    "type_registrant": 5,
    "name": 6,
    "street": 7,
    "street2": 8,
    "city": 9,
    "state": 10,
    "zip_code": 11,
    "region": 12,
    "county": 13,
    "country": 14,
    "last_action_date": 15,
    "cert_issue_date": 16,
    "certification": 17,
    "type_aircraft": 18,
    "type_engine": 19,
    "status_code": 20,
    "mode_s_code": 21,
    "fract_owner": 22,
    "airworthiness_date": 23,
}
# Expiration date is typically column 24 or 25 depending on format


def mode_s_oct_to_hex(oct_code: str) -> str:
    """Convert Mode S octal code to hex."""
    oct_code = oct_code.strip()
    if not oct_code:
        return ""
    try:
        return format(int(oct_code, 8), "06X")
    except ValueError:
        return ""


def load_faa_registry() -> dict[str, dict]:
    """Load FAA registry indexed by N-number."""
    registry: dict[str, dict] = {}

    if not FAA_MASTER.exists():
        print(f"Error: {FAA_MASTER} not found")
        return registry

    with open(FAA_MASTER, errors="replace") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 24:
                continue
            nn = row[COL["n_number"]].strip()
            if not nn or not nn[0].isdigit():
                continue

            n_number = "N" + nn

            # Find manufacturer/model from ACFTREF if available, else use code
            mfr_code = row[COL["mfr_mdl_code"]].strip()
            mode_s_hex = mode_s_oct_to_hex(row[COL["mode_s_code"]])

            # Try to get expiration date (varies by CSV format)
            expiration = ""
            if len(row) > 24:
                expiration = row[24].strip()

            registry[n_number] = {
                "serial_number": row[COL["serial_number"]].strip(),
                "mfr_mdl_code": mfr_code,
                "year_mfr": row[COL["year_mfr"]].strip(),
                "registrant_name": row[COL["name"]].strip(),
                "registrant_city": row[COL["city"]].strip(),
                "registrant_state": row[COL["state"]].strip(),
                "airworthiness_date": row[COL["airworthiness_date"]].strip(),
                "cert_issue_date": row[COL["cert_issue_date"]].strip(),
                "expiration_date": expiration,
                "mode_s_hex": mode_s_hex,
                "type_aircraft": row[COL["type_aircraft"]].strip(),
                "type_engine": row[COL["type_engine"]].strip(),
                "status_code": row[COL["status_code"]].strip(),
            }

    return registry


def load_acftref() -> dict[str, tuple[str, str]]:
    """Load aircraft reference file for mfr/model names. Returns code -> (mfr, model)."""
    acftref_path = OUTPUT_DIR / "ACFTREF.txt"
    if not acftref_path.exists():
        acftref_path = OUTPUT_DIR / "acftref.csv"
    if not acftref_path.exists():
        return {}

    ref: dict[str, tuple[str, str]] = {}
    with open(acftref_path, errors="replace") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 3:
                code = row[0].strip()
                mfr = row[1].strip()
                model = row[2].strip()
                if code:
                    ref[code] = (mfr, model)
    return ref


def enrich_fleet(force: bool = False) -> tuple[int, int, int]:
    """Enrich flight_line aircraft with FAA data. Returns (enriched, skipped, not_found)."""
    print("Loading FAA registry...")
    registry = load_faa_registry()
    print(f"  {len(registry):,} aircraft in registry")

    acftref = load_acftref()
    if acftref:
        print(f"  {len(acftref):,} aircraft type codes loaded")

    with open(FLIGHT_LINE) as f:
        flight_line = json.load(f)

    enriched = 0
    skipped = 0
    not_found = 0

    for school in flight_line:
        for ac in school.get("fleet", []):
            if ac.get("faa") and not force:
                skipped += 1
                continue

            nn = (ac.get("n_number") or "").strip().upper()
            if not nn:
                skipped += 1
                continue

            faa = registry.get(nn)
            if not faa:
                not_found += 1
                continue

            # Resolve manufacturer/model names from ACFTREF
            mfr_code = faa["mfr_mdl_code"]
            if mfr_code and mfr_code in acftref:
                mfr, model = acftref[mfr_code]
                faa["faa_mfr"] = mfr
                faa["faa_model"] = model
            else:
                faa["faa_mfr"] = ""
                faa["faa_model"] = ""

            ac["faa"] = faa
            enriched += 1

    with open(FLIGHT_LINE, "w") as f:
        json.dump(flight_line, f, indent=2)

    return enriched, skipped, not_found


def print_stats():
    """Print FAA enrichment coverage stats."""
    with open(FLIGHT_LINE) as f:
        flight_line = json.load(f)

    total = 0
    with_nn = 0
    with_faa = 0
    with_hex = 0

    for school in flight_line:
        for ac in school.get("fleet", []):
            total += 1
            if ac.get("n_number"):
                with_nn += 1
            faa = ac.get("faa")
            if faa:
                with_faa += 1
                if faa.get("mode_s_hex"):
                    with_hex += 1

    print(f"Fleet: {total} aircraft")
    print(f"  With N-number: {with_nn} ({100*with_nn/total:.0f}%)")
    print(f"  With FAA data: {with_faa} ({100*with_faa/total:.0f}%)")
    print(f"  With Mode S hex: {with_hex} ({100*with_hex/total:.0f}%)")


def main():
    parser = argparse.ArgumentParser(description="FAA registry enrichment")
    parser.add_argument("--force", action="store_true", help="Re-enrich all")
    parser.add_argument("--stats", action="store_true", help="Print stats only")
    args = parser.parse_args()

    if args.stats:
        print_stats()
        return

    enriched, skipped, not_found = enrich_fleet(force=args.force)
    print(f"\nResults:")
    print(f"  Enriched: {enriched}")
    print(f"  Skipped (already had FAA or no N-number): {skipped}")
    print(f"  N-number not in FAA registry: {not_found}")
    print()
    print_stats()


if __name__ == "__main__":
    main()
