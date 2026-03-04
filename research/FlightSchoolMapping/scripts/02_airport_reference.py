#!/usr/bin/env python3
"""
Phase 2: Build airport reference table.

Downloads airport data from OurAirports (CSV, free, comprehensive) and builds
a reference table with identifiers, names, coordinates, city, state, and type.
This is used later to match ADS-B ground positions to airports.

Output: data/airports.csv — airport reference with coordinates
"""

import os
from pathlib import Path

import pandas as pd
import requests
from tqdm import tqdm

DATA_DIR = Path(__file__).parent.parent / "data"
OUTPUT_DIR = Path(__file__).parent.parent / "output"

# OurAirports provides freely downloadable CSV datasets
AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
RUNWAYS_URL = "https://davidmegginson.github.io/ourairports-data/runways.csv"

# US airport types relevant to GA training
RELEVANT_TYPES = {"small_airport", "medium_airport", "large_airport"}


def download_csv(url: str, filename: str) -> Path:
    """Download a CSV file to the data directory."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    filepath = DATA_DIR / filename

    if filepath.exists():
        print(f"  Using cached {filepath}")
        return filepath

    print(f"  Downloading {url}...")
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()

    total = int(resp.headers.get("content-length", 0))
    with open(filepath, "wb") as f:
        with tqdm(total=total, unit="B", unit_scale=True, desc=filename) as pbar:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                pbar.update(len(chunk))

    return filepath


def run():
    """Execute Phase 2: Build airport reference table."""
    print("=" * 60)
    print("Phase 2: Airport Reference Table")
    print("=" * 60)

    # Step 1: Download
    print("\n[1/3] Downloading airport data...")
    airports_path = download_csv(AIRPORTS_URL, "ourairports_airports.csv")

    # Step 2: Parse and filter
    print("\n[2/3] Parsing and filtering to US GA airports...")
    airports = pd.read_csv(airports_path, dtype=str, keep_default_na=False)

    # Filter to US airports
    us_airports = airports[airports["iso_country"] == "US"].copy()
    print(f"  Total US airports: {len(us_airports):,}")

    # Filter to relevant types (exclude heliports, seaplane bases, closed)
    ga_airports = us_airports[us_airports["type"].isin(RELEVANT_TYPES)].copy()
    print(f"  GA-relevant airports: {len(ga_airports):,}")

    # Select and rename columns
    result = ga_airports[
        [
            "ident",
            "type",
            "name",
            "latitude_deg",
            "longitude_deg",
            "elevation_ft",
            "iso_region",
            "municipality",
            "gps_code",
            "iata_code",
            "local_code",
        ]
    ].copy()

    result = result.rename(
        columns={
            "ident": "airport_id",
            "type": "airport_type",
            "name": "airport_name",
            "latitude_deg": "latitude",
            "longitude_deg": "longitude",
            "iso_region": "region",
            "municipality": "city",
            "local_code": "faa_id",
        }
    )

    # Extract state from region (format: "US-CA")
    result["state"] = result["region"].str.extract(r"US-(\w+)", expand=False)

    # Convert coordinates to float
    result["latitude"] = pd.to_numeric(result["latitude"], errors="coerce")
    result["longitude"] = pd.to_numeric(result["longitude"], errors="coerce")

    # Step 3: Save
    print("\n[3/3] Saving airport reference table...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "airports.csv"
    result.to_csv(output_path, index=False)

    print(f"  Saved {len(result):,} US airports to {output_path}")
    print(f"  States covered: {result['state'].nunique()}")
    print(f"\n  Top states by airport count:")
    top_states = result["state"].value_counts().head(10)
    for state, count in top_states.items():
        print(f"    {state}: {count:,}")

    print(f"\nPhase 2 complete.")
    return result


if __name__ == "__main__":
    run()
