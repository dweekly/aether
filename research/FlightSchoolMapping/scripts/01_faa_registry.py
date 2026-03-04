#!/usr/bin/env python3
"""
Phase 1: Download and parse the FAA Aircraft Registration Database.

Downloads the releasable aircraft database from the FAA, parses MASTER.txt
and ACFTREF.txt, filters to GA-relevant training aircraft, and flags
registrants whose names suggest flight schools.

Output: data/faa_registry.csv — enriched registration records for GA fleet
        data/faa_schools_candidates.csv — subset flagged as likely flight schools
"""

import csv
import io
import os
import re
import zipfile
from pathlib import Path

import requests
import pandas as pd
from tqdm import tqdm

DATA_DIR = Path(__file__).parent.parent / "data"
OUTPUT_DIR = Path(__file__).parent.parent / "output"

FAA_REGISTRY_URL = (
    "https://registry.faa.gov/database/ReleasableAircraft.zip"
)

# GA training-relevant type codes from ACFTREF
# Type Aircraft: 1=Glider, 2=Balloon, 3=Blimp, 4=Fixed Wing Single Engine,
#                5=Fixed Wing Multi Engine, 6=Rotorcraft, 7=Weight-Shift,
#                8=Powered Parachute, 9=Gyroplane
GA_TRAINING_TYPES = {"4", "5", "6", "9"}

# Engine type codes: 1=Reciprocating, 2=Turbo-prop, 5=Turboshaft
GA_ENGINE_TYPES = {"1", "2", "5"}

# Patterns that suggest a flight school or training operation
SCHOOL_PATTERNS = [
    r"flight\s+school",
    r"flying\s+school",
    r"aviation\s+academy",
    r"flight\s+academy",
    r"flying\s+academy",
    r"aero\s+academy",
    r"flight\s+training",
    r"aviation\s+training",
    r"flying\s+club",
    r"aero\s+club",
    r"flight\s+center",
    r"aviation\s+center",
    r"flight\s+college",
    r"aviation\s+college",
    r"aviation\s+university",
    r"aviation\s+institute",
    r"pilot\s+training",
    r"learn\s+to\s+fly",
    r"air\s+academy",
    r"flight\s+operations",
    r"aviation\s+services",
    r"aero\s+services",
    r"flight\s+services",
    r"cfi\b",
    r"\bfbo\b",
]
SCHOOL_REGEX = re.compile("|".join(SCHOOL_PATTERNS), re.IGNORECASE)

# Common training aircraft models (partial match)
TRAINING_MODELS = [
    "172", "152", "150",  # Cessna trainers
    "PA-28", "PA-38", "PA-44",  # Piper trainers
    "DA20", "DA40", "DA42",  # Diamond trainers
    "SR20", "SR22",  # Cirrus
    "C-172", "C-152", "C-150",
    "ARCHER", "WARRIOR", "SEMINOLE", "CHEROKEE",
    "SKYHAWK", "SKYLANE",
    "R22", "R44",  # Robinson helicopters
    "KATANA", "ECLIPSE",
]


def download_faa_registry() -> Path:
    """Download the FAA releasable aircraft database ZIP."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DATA_DIR / "ReleasableAircraft.zip"

    if zip_path.exists():
        print(f"  Using cached {zip_path}")
        return zip_path

    print(f"  Downloading FAA registry from {FAA_REGISTRY_URL}...")
    resp = requests.get(FAA_REGISTRY_URL, stream=True, timeout=120)
    resp.raise_for_status()

    total = int(resp.headers.get("content-length", 0))
    with open(zip_path, "wb") as f:
        with tqdm(total=total, unit="B", unit_scale=True, desc="FAA ZIP") as pbar:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                pbar.update(len(chunk))

    return zip_path


def extract_file_from_zip(zip_path: Path, filename: str) -> pd.DataFrame:
    """Extract a specific file from the FAA ZIP and return as DataFrame."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        # FAA files use inconsistent naming; search case-insensitively
        matching = [n for n in zf.namelist() if filename.upper() in n.upper()]
        if not matching:
            raise FileNotFoundError(
                f"{filename} not found in ZIP. Contents: {zf.namelist()}"
            )
        target = matching[0]
        print(f"  Extracting {target}...")
        with zf.open(target) as f:
            # FAA files are comma-delimited with trailing commas and spaces
            content = f.read().decode("latin-1")
            df = pd.read_csv(
                io.StringIO(content),
                dtype=str,
                keep_default_na=False,
                skipinitialspace=True,
            )
            # Strip whitespace from column names and values
            df.columns = [c.strip() for c in df.columns]
            df = df.apply(lambda col: col.str.strip() if col.dtype == "object" else col)
            return df


def parse_master(zip_path: Path) -> pd.DataFrame:
    """Parse MASTER.txt — the primary registration file."""
    df = extract_file_from_zip(zip_path, "MASTER.txt")

    # Standardize column names (FAA uses inconsistent casing across releases)
    col_map = {}
    for col in df.columns:
        col_map[col] = col.upper().replace(" ", "_")
    df = df.rename(columns=col_map)

    # Key columns we need
    keep_cols = [
        "N-NUMBER", "SERIAL_NUMBER", "MFR_MDL_CODE",
        "ENG_MFR_MDL", "YEAR_MFR", "TYPE_REGISTRANT",
        "NAME", "STREET", "STREET2", "CITY", "STATE", "ZIP_CODE",
        "REGION", "COUNTY", "COUNTRY",
        "TYPE_AIRCRAFT", "TYPE_ENGINE", "STATUS_CODE",
        "MODE_S_CODE_HEX", "CERTIFICATION",
    ]
    available = [c for c in keep_cols if c in df.columns]
    df = df[available].copy()

    return df


def parse_acftref(zip_path: Path) -> pd.DataFrame:
    """Parse ACFTREF.txt — aircraft make/model reference."""
    df = extract_file_from_zip(zip_path, "ACFTREF.txt")

    col_map = {}
    for col in df.columns:
        col_map[col] = col.upper().replace(" ", "_")
    df = df.rename(columns=col_map)

    return df


def n_number_to_icao_hex(n_number: str) -> str:
    """
    Convert an N-number to its ICAO 24-bit hex address.

    The FAA allocates ICAO addresses in the range A00001-AFFFFF.
    The encoding follows a specific algorithm based on the N-number characters.
    If MODE_S_CODE_HEX is available in the data, prefer that instead.
    """
    # This is a simplified conversion — the FAA data includes MODE_S_CODE_HEX
    # directly, so we prefer that field when available.
    return ""


def is_training_model(model_str: str) -> bool:
    """Check if a model string matches common training aircraft."""
    if not model_str:
        return False
    model_upper = model_str.upper()
    return any(m.upper() in model_upper for m in TRAINING_MODELS)


def flag_flight_school(name: str) -> bool:
    """Check if a registrant name suggests a flight school."""
    if not name:
        return False
    return bool(SCHOOL_REGEX.search(name))


def run():
    """Execute Phase 1: FAA Registry download and parse."""
    print("=" * 60)
    print("Phase 1: FAA Aircraft Registration Database")
    print("=" * 60)

    # Step 1: Download
    print("\n[1/4] Downloading FAA registry...")
    zip_path = download_faa_registry()

    # Step 2: Parse MASTER and ACFTREF
    print("\n[2/4] Parsing registration records...")
    master = parse_master(zip_path)
    acftref = parse_acftref(zip_path)
    print(f"  MASTER records: {len(master):,}")
    print(f"  ACFTREF records: {len(acftref):,}")

    # Step 3: Join and filter
    print("\n[3/4] Filtering to GA training fleet...")

    # Join master with aircraft reference to get make/model info
    if "MFR_MDL_CODE" in master.columns and "CODE" in acftref.columns:
        merged = master.merge(
            acftref, left_on="MFR_MDL_CODE", right_on="CODE", how="left"
        )
    else:
        merged = master.copy()
        print("  Warning: Could not join ACFTREF (column mismatch)")

    # Filter to GA-relevant types
    if "TYPE_AIRCRAFT" in merged.columns:
        ga_mask = merged["TYPE_AIRCRAFT"].isin(GA_TRAINING_TYPES)
        merged = merged[ga_mask].copy()
        print(f"  After GA type filter: {len(merged):,}")

    # Filter to relevant engine types
    if "TYPE_ENGINE" in merged.columns:
        engine_mask = merged["TYPE_ENGINE"].isin(GA_ENGINE_TYPES)
        merged = merged[engine_mask].copy()
        print(f"  After engine type filter: {len(merged):,}")

    # Filter to active registrations only
    if "STATUS_CODE" in merged.columns:
        active_mask = merged["STATUS_CODE"].isin({"V", "A", ""})
        merged = merged[active_mask].copy()
        print(f"  After active-only filter: {len(merged):,}")

    # Step 4: Flag flight schools and training aircraft
    print("\n[4/4] Flagging flight schools and training aircraft...")
    if "NAME" in merged.columns:
        merged["IS_FLIGHT_SCHOOL"] = merged["NAME"].apply(flag_flight_school)
        school_count = merged["IS_FLIGHT_SCHOOL"].sum()
        print(f"  Registrants flagged as flight schools: {school_count:,}")

    model_col = "MODEL" if "MODEL" in merged.columns else None
    if model_col:
        merged["IS_TRAINING_MODEL"] = merged[model_col].apply(is_training_model)
        training_count = merged["IS_TRAINING_MODEL"].sum()
        print(f"  Aircraft flagged as training models: {training_count:,}")

    # Save outputs
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    registry_path = OUTPUT_DIR / "faa_ga_registry.csv"
    merged.to_csv(registry_path, index=False)
    print(f"\n  Saved full GA registry: {registry_path}")

    if "IS_FLIGHT_SCHOOL" in merged.columns:
        schools = merged[merged["IS_FLIGHT_SCHOOL"]].copy()
        schools_path = OUTPUT_DIR / "faa_school_candidates.csv"
        schools.to_csv(schools_path, index=False)
        print(f"  Saved school candidates: {schools_path}")

    print(f"\nPhase 1 complete. {len(merged):,} GA records processed.")
    return merged


if __name__ == "__main__":
    run()
