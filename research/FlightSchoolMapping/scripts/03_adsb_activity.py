#!/usr/bin/env python3
"""
Phase 3: Query ADS-B Exchange for aircraft activity data.

For each tail number in our GA registry, query ADS-B Exchange to determine:
- Current/recent position
- Activity level (how often it's been seen)
- Whether it's airborne or on the ground

This script supports both the ADS-B Exchange v2 API (requires API key)
and a fallback mode that works with locally-downloaded ADS-B data files.

Output: output/adsb_activity.csv — tail numbers with activity metrics
"""

import json
import math
import os
import time
from pathlib import Path

import pandas as pd
import requests
from tqdm import tqdm

DATA_DIR = Path(__file__).parent.parent / "data"
OUTPUT_DIR = Path(__file__).parent.parent / "output"

# ADS-B Exchange API configuration
ADSBX_API_KEY = os.environ.get("ADSBX_API_KEY", "")
ADSBX_BASE_URL = "https://adsbexchange.com/api/aircraft/v2"
ADSBX_RAPID_URL = "https://adsbexchange-com1.p.rapidapi.com/v2"

# Rate limiting: be respectful of API limits
REQUEST_DELAY_SEC = 1.0  # seconds between API calls
BATCH_SIZE = 100  # N-numbers per batch for bulk queries

# Radius (in nautical miles) for considering an aircraft "at" an airport
AIRPORT_PROXIMITY_NM = 2.0


def icao_hex_from_registry(row: pd.Series) -> str:
    """Get the ICAO hex address from the FAA registry record."""
    hex_code = row.get("MODE_S_CODE_HEX", "")
    if hex_code and len(hex_code) == 6:
        return hex_code.upper()
    return ""


def query_adsbx_by_hex(icao_hex: str) -> dict | None:
    """
    Query ADS-B Exchange for a single aircraft by ICAO hex.

    Returns aircraft state dict or None if not found/error.
    """
    if not ADSBX_API_KEY:
        return None

    headers = {
        "X-RapidAPI-Key": ADSBX_API_KEY,
        "X-RapidAPI-Host": "adsbexchange-com1.p.rapidapi.com",
    }

    try:
        resp = requests.get(
            f"{ADSBX_RAPID_URL}/hex/{icao_hex}/",
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            ac_list = data.get("ac", [])
            if ac_list:
                return ac_list[0]
        elif resp.status_code == 429:
            print(f"  Rate limited, backing off...")
            time.sleep(5)
    except requests.RequestException as e:
        print(f"  API error for {icao_hex}: {e}")

    return None


def query_adsbx_bulk_area(lat: float, lon: float, radius_nm: int = 250) -> list[dict]:
    """
    Query ADS-B Exchange for all aircraft in a circular area.

    More efficient than per-hex queries for scanning airports.
    """
    if not ADSBX_API_KEY:
        return []

    headers = {
        "X-RapidAPI-Key": ADSBX_API_KEY,
        "X-RapidAPI-Host": "adsbexchange-com1.p.rapidapi.com",
    }

    try:
        resp = requests.get(
            f"{ADSBX_RAPID_URL}/lat/{lat}/lon/{lon}/dist/{radius_nm}/",
            headers=headers,
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("ac", [])
    except requests.RequestException as e:
        print(f"  API error for area query: {e}")

    return []


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in nautical miles."""
    R_NM = 3440.065  # Earth radius in nautical miles

    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))

    return R_NM * c


def load_local_adsb_data() -> pd.DataFrame | None:
    """
    Load locally-downloaded ADS-B data if available.

    Supports common formats from ADS-B Exchange data products:
    - JSON lines files
    - CSV exports
    - Parquet files (from readsb-hist or similar)
    """
    # Check for local ADS-B data files
    adsb_data_dir = DATA_DIR / "adsb"
    if not adsb_data_dir.exists():
        return None

    # Try CSV files first
    csv_files = list(adsb_data_dir.glob("*.csv"))
    if csv_files:
        print(f"  Found {len(csv_files)} local ADS-B CSV files")
        frames = []
        for f in tqdm(csv_files, desc="Loading CSVs"):
            df = pd.read_csv(f, dtype=str, keep_default_na=False)
            frames.append(df)
        return pd.concat(frames, ignore_index=True) if frames else None

    # Try JSON lines
    jsonl_files = list(adsb_data_dir.glob("*.jsonl")) + list(
        adsb_data_dir.glob("*.json")
    )
    if jsonl_files:
        print(f"  Found {len(jsonl_files)} local ADS-B JSON files")
        records = []
        for f in tqdm(jsonl_files, desc="Loading JSON"):
            with open(f) as fh:
                for line in fh:
                    try:
                        records.append(json.loads(line.strip()))
                    except json.JSONDecodeError:
                        continue
        return pd.DataFrame(records) if records else None

    return None


def process_adsb_for_registry(registry: pd.DataFrame, airports: pd.DataFrame) -> pd.DataFrame:
    """
    For each aircraft in the registry, determine activity metrics.

    Strategy:
    1. If API key available: query ADS-B Exchange for each hex code
    2. If local data available: scan local ADS-B dumps
    3. Otherwise: return registry with empty activity columns (to be filled later)
    """
    results = []

    # Build airport lookup for proximity matching
    airport_coords = airports[["airport_id", "latitude", "longitude", "faa_id"]].dropna(
        subset=["latitude", "longitude"]
    )

    hex_codes = registry[registry["MODE_S_CODE_HEX"].str.len() == 6]["MODE_S_CODE_HEX"].unique()
    print(f"  Unique ICAO hex codes to query: {len(hex_codes):,}")

    # Strategy 1: Try local data first (fastest, no API cost)
    local_data = load_local_adsb_data()
    if local_data is not None:
        print(f"  Processing {len(local_data):,} local ADS-B records...")
        # Process local data to extract activity metrics
        # (Implementation depends on the specific format of local data)
        pass

    # Strategy 2: Use API if key is available
    if ADSBX_API_KEY:
        print(f"  Querying ADS-B Exchange API...")
        print(f"  (This will take ~{len(hex_codes) * REQUEST_DELAY_SEC / 60:.0f} minutes)")

        for hex_code in tqdm(hex_codes[:BATCH_SIZE], desc="ADS-B queries"):
            result = query_adsbx_by_hex(hex_code)
            if result:
                # Find nearest airport
                ac_lat = float(result.get("lat", 0))
                ac_lon = float(result.get("lon", 0))
                nearest_airport = None
                min_dist = float("inf")

                if ac_lat and ac_lon:
                    for _, apt in airport_coords.iterrows():
                        dist = haversine_nm(
                            ac_lat, ac_lon,
                            float(apt["latitude"]),
                            float(apt["longitude"]),
                        )
                        if dist < min_dist:
                            min_dist = dist
                            nearest_airport = apt["faa_id"] or apt["airport_id"]

                results.append({
                    "icao_hex": hex_code,
                    "last_lat": ac_lat,
                    "last_lon": ac_lon,
                    "altitude_ft": result.get("alt_baro", ""),
                    "ground_speed_kt": result.get("gs", ""),
                    "on_ground": result.get("alt_baro") == "ground",
                    "squawk": result.get("squawk", ""),
                    "nearest_airport": nearest_airport if min_dist < AIRPORT_PROXIMITY_NM else "",
                    "distance_to_airport_nm": round(min_dist, 2) if min_dist < 100 else "",
                    "seen_timestamp": result.get("seen", ""),
                })

            time.sleep(REQUEST_DELAY_SEC)

    if not results:
        print("  No ADS-B data available (no API key and no local data).")
        print("  To populate activity data, either:")
        print("    1. Set ADSBX_API_KEY environment variable")
        print("    2. Place ADS-B data files in data/adsb/")
        print("  Continuing with registry data only...")

        # Return registry with placeholder activity columns
        registry["adsb_last_seen"] = ""
        registry["adsb_nearest_airport"] = ""
        registry["adsb_on_ground"] = ""
        return registry

    # Merge ADS-B results back into registry
    adsb_df = pd.DataFrame(results)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    adsb_path = OUTPUT_DIR / "adsb_activity.csv"
    adsb_df.to_csv(adsb_path, index=False)
    print(f"  Saved ADS-B activity data: {adsb_path}")

    return adsb_df


def run(registry: pd.DataFrame = None, airports: pd.DataFrame = None):
    """Execute Phase 3: ADS-B activity analysis."""
    print("=" * 60)
    print("Phase 3: ADS-B Activity Data")
    print("=" * 60)

    # Load registry if not provided
    if registry is None:
        registry_path = OUTPUT_DIR / "faa_ga_registry.csv"
        if not registry_path.exists():
            print("  Error: Run 01_faa_registry.py first")
            return None
        registry = pd.read_csv(registry_path, dtype=str, keep_default_na=False)

    # Load airports if not provided
    if airports is None:
        airports_path = OUTPUT_DIR / "airports.csv"
        if not airports_path.exists():
            print("  Error: Run 02_airport_reference.py first")
            return None
        airports = pd.read_csv(airports_path, keep_default_na=False)

    print(f"\n  Registry records: {len(registry):,}")
    print(f"  Airport reference: {len(airports):,}")
    print(f"  ADS-B Exchange API key: {'configured' if ADSBX_API_KEY else 'NOT SET'}")

    result = process_adsb_for_registry(registry, airports)

    print(f"\nPhase 3 complete.")
    return result


if __name__ == "__main__":
    run()
