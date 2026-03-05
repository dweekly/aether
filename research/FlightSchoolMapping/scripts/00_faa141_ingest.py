#!/usr/bin/env python3
"""
Phase 0: Ingest FAA Part 141 Certificated Pilot Schools.

Reads the official FAA Part 141 flight school directory (UTF-16 TSV exported
from the FAA's Safety & Flight Standards facility dashboard) and normalizes it
into a clean CSV. Matches each school to the nearest airport using:
  1. City+state matching against the airport reference table (fast, no API)
  2. Geocoding fallback via Nominatim for unmatched (slow, rate-limited)

Input:  faa-141-flightschools.csv (UTF-16LE, tab-delimited)
Output: output/faa141_schools.csv — normalized schools with airport assignments
"""

import math
import re
import time
from pathlib import Path

import pandas as pd
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from tqdm import tqdm

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
RAW_FILE = BASE_DIR / "faa-141-flightschools.csv"

_geocode_cache: dict[str, tuple[float, float] | None] = {}
GEOCODER = None


def get_geocoder():
    global GEOCODER
    if GEOCODER is None:
        GEOCODER = Nominatim(user_agent="aether-research", timeout=10)
    return GEOCODER


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance between two points in nautical miles."""
    R_NM = 3440.065
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return R_NM * 2 * math.asin(math.sqrt(a))


def find_nearest_airport(
    lat: float, lon: float, airports: pd.DataFrame, max_nm: float = 30.0
) -> dict | None:
    """Find the nearest airport within max_nm of a coordinate."""
    dists = airports.apply(
        lambda r: haversine_nm(lat, lon, float(r["latitude"]), float(r["longitude"])),
        axis=1,
    )
    min_idx = dists.idxmin()
    min_dist = dists[min_idx]
    if min_dist > max_nm:
        return None
    apt = airports.loc[min_idx]
    return {
        "nearest_airport_id": apt.get("faa_id") or apt["airport_id"],
        "nearest_airport_name": apt["airport_name"],
        "airport_distance_nm": round(min_dist, 1),
        "match_method": "geocoded",
    }


def normalize_city(city: str) -> str:
    """Normalize a city name for matching."""
    city = city.upper().strip()
    # Remove common suffixes/prefixes
    city = re.sub(r"\bST\.?\b", "SAINT", city)
    city = re.sub(r"\bFT\.?\b", "FORT", city)
    city = re.sub(r"\bMT\.?\b", "MOUNT", city)
    city = re.sub(r"[^A-Z\s]", "", city)
    return city.strip()


def match_by_city_state(
    df: pd.DataFrame, airports: pd.DataFrame
) -> pd.DataFrame:
    """
    Match schools to airports by city+state. For cities with multiple airports,
    picks the largest (by type: large > medium > small). Returns df with airport
    columns populated where matches are found.
    """
    # Build airport lookup: (normalized_city, state) -> best airport
    type_rank = {"large_airport": 3, "medium_airport": 2, "small_airport": 1}
    airports = airports.copy()
    airports["_city_norm"] = airports["city"].apply(normalize_city)
    airports["_type_rank"] = airports["airport_type"].map(type_rank).fillna(0)

    # For each city+state, pick the highest-ranked airport
    airport_lookup = {}
    for _, apt in airports.sort_values("_type_rank", ascending=False).iterrows():
        key = (apt["_city_norm"], apt.get("state", ""))
        if key not in airport_lookup and apt["_city_norm"]:
            airport_lookup[key] = {
                "nearest_airport_id": apt.get("faa_id") or apt["airport_id"],
                "nearest_airport_name": apt["airport_name"],
                "airport_distance_nm": 0.0,
                "match_method": "city_state",
            }

    # Also try matching on address keywords that contain airport names/codes
    airport_code_pattern = re.compile(
        r"\b([A-Z]{3,4})\b.*(?:AIRPORT|AIRFIELD|AIRPARK|FIELD)", re.IGNORECASE
    )

    matched = 0
    cols = ["nearest_airport_id", "nearest_airport_name", "airport_distance_nm", "match_method"]
    for col in cols:
        df[col] = ""

    for idx, row in df.iterrows():
        city_norm = normalize_city(row["city"])
        state = row["state"].strip().upper()
        key = (city_norm, state)

        if key in airport_lookup:
            for col in cols:
                df.at[idx, col] = str(airport_lookup[key][col])
            matched += 1

    return df, matched


def geocode_unmatched(
    df: pd.DataFrame, airports: pd.DataFrame
) -> tuple[pd.DataFrame, int, int]:
    """Geocode schools that weren't matched by city+state."""
    unmatched = df[df["nearest_airport_id"] == ""]
    if unmatched.empty:
        return df, 0, 0

    # Deduplicate by city+state+zip to minimize API calls
    unique_keys = set()
    rows_to_geocode = []
    for idx, row in unmatched.iterrows():
        cache_key = f"{row['city']}|{row['state']}|{row['postal_code']}"
        if cache_key not in unique_keys:
            unique_keys.add(cache_key)
            rows_to_geocode.append((idx, row, cache_key))

    geocoded = 0
    failures = 0

    for idx, row, cache_key in tqdm(rows_to_geocode, desc="Geocoding misses"):
        if cache_key in _geocode_cache:
            coords = _geocode_cache[cache_key]
        else:
            coords = None
            queries = [
                f"{row['address_1']}, {row['city']}, {row['state']} {row['postal_code']}",
                f"{row['city']}, {row['state']} {row['postal_code']}",
            ]
            for query in queries:
                try:
                    loc = get_geocoder().geocode(query, country_codes="us")
                    if loc:
                        coords = (loc.latitude, loc.longitude)
                        break
                except (GeocoderTimedOut, GeocoderServiceError):
                    time.sleep(2)
                except Exception:
                    pass
                time.sleep(1.1)
            _geocode_cache[cache_key] = coords

        if coords:
            nearest = find_nearest_airport(coords[0], coords[1], airports)
            if nearest:
                # Apply to all rows with same city+state+zip
                mask = (
                    (df["city"] == row["city"])
                    & (df["state"] == row["state"])
                    & (df["postal_code"] == row["postal_code"])
                    & (df["nearest_airport_id"] == "")
                )
                for col in ["nearest_airport_id", "nearest_airport_name", "airport_distance_nm", "match_method"]:
                    df.loc[mask, col] = str(nearest[col])
                geocoded += mask.sum()
        else:
            failures += 1

    return df, geocoded, failures


def load_faa141() -> pd.DataFrame:
    """Load and normalize the FAA Part 141 schools CSV."""
    print("  Reading UTF-16 TSV...")
    df = pd.read_csv(
        RAW_FILE,
        encoding="utf-16",
        sep="\t",
        dtype=str,
        keep_default_na=False,
    )

    # Strip whitespace from column names and values
    df.columns = df.columns.str.strip()
    for col in df.columns:
        df[col] = df[col].str.strip()

    # Normalize column names
    rename_map = {
        "Agency Name": "agency_name",
        "DSGN_CODE": "dsgn_code",
        "DBA": "dba",
        "Cert_No": "cert_no",
        "Address Line 1": "address_1",
        "Address Line 2": "address_2",
        "Address Line 3": "address_3",
        "City": "city",
        "State/Province": "state",
        "Country": "country",
        "Postal Code": "postal_code",
        "Agency Phone Number": "phone",
        "Agency Email": "email",
        "Accountable Manager": "accountable_manager",
        "Accountable Manager Phone Number": "manager_phone",
        "Accountable Manager Email": "manager_email",
        "Liaison": "liaison",
        "Liaison Phone Number": "liaison_phone",
        "Liaison Email": "liaison_email",
        "Update_Date": "update_date",
    }
    df = df.rename(columns=rename_map)

    # Drop any fully-empty trailing columns
    df = df.loc[:, df.columns != ""]
    df = df.dropna(how="all", axis=1)

    # Build a combined address field
    df["full_address"] = (
        df["address_1"]
        + df["address_2"].apply(lambda x: f", {x}" if x else "")
        + df["address_3"].apply(lambda x: f", {x}" if x else "")
    )

    # Build display name (prefer DBA if present)
    df["display_name"] = df.apply(
        lambda r: r["dba"] if r["dba"] else r["agency_name"], axis=1
    )

    # Build best contact email (prefer agency email, then liaison, then manager)
    df["best_email"] = df.apply(
        lambda r: r["email"] or r["liaison_email"] or r["manager_email"], axis=1
    )

    # Build best phone
    df["best_phone"] = df.apply(
        lambda r: r["phone"] or r["liaison_phone"] or r["manager_phone"], axis=1
    )

    # Filter to US only
    us_mask = df["country"].str.upper().str.contains("UNITED STATES", na=False)
    non_us = len(df) - us_mask.sum()
    if non_us > 0:
        print(f"  Filtering out {non_us} non-US entries")
    df = df[us_mask].copy()

    return df


def run(airports: pd.DataFrame = None, geocode: bool = True):
    """Execute Phase 0: FAA 141 Ingest. Set geocode=False to skip slow Nominatim lookups."""
    print("=" * 60)
    print("Phase 0: FAA Part 141 School Ingest")
    print("=" * 60)

    if not RAW_FILE.exists():
        print(f"  Error: {RAW_FILE} not found")
        print("  Download from FAA Safety & Flight Standards facility dashboard")
        return None

    # Step 1: Load and normalize
    print("\n[1/4] Loading FAA 141 school directory...")
    df = load_faa141()
    print(f"  Loaded {len(df):,} US school locations")

    unique_certs = df["cert_no"].nunique()
    print(f"  Unique certificate numbers: {unique_certs}")
    print(f"  Total locations (incl. satellites): {len(df)}")

    # Step 2: Load airport reference
    print("\n[2/4] Loading airport reference table...")
    if airports is None:
        airports_path = OUTPUT_DIR / "airports.csv"
        if airports_path.exists():
            airports = pd.read_csv(airports_path, keep_default_na=False)
            print(f"  Loaded {len(airports):,} airports")
        else:
            print("  Warning: airports.csv not found — run Phase 2 first")
            print("  Skipping airport matching, saving normalized data only")

    # Step 3: Match to airports
    if airports is not None:
        # Pass 1: Fast city+state matching
        print("\n[3/4] Matching schools to airports...")
        print("  Pass 1: City+state matching...")
        df, city_matched = match_by_city_state(df, airports)
        print(f"  City+state matches: {city_matched:,} / {len(df):,}")

        # Pass 2: Geocode remaining (optional, slow)
        remaining = (df["nearest_airport_id"] == "").sum()
        if remaining > 0 and geocode:
            print(f"  Pass 2: Geocoding {remaining} unmatched schools...")
            df, geo_matched, geo_failures = geocode_unmatched(df, airports)
            print(f"  Geocoded matches: {geo_matched:,}")
            print(f"  Geocode failures: {geo_failures:,}")
        elif remaining > 0:
            print(f"  Pass 2: Skipping geocoding ({remaining} unmatched, use --geocode to resolve)")

        total_matched = (df["nearest_airport_id"] != "").sum()
        print(f"  Total matched: {total_matched:,} / {len(df):,} ({100*total_matched/len(df):.0f}%)")
    else:
        print("\n[3/4] Skipping airport matching (no airport reference)")

    # Step 4: Save
    print("\n[4/4] Saving normalized FAA 141 schools...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    output_cols = [
        "agency_name",
        "display_name",
        "dba",
        "dsgn_code",
        "cert_no",
        "full_address",
        "address_1",
        "city",
        "state",
        "postal_code",
        "best_phone",
        "best_email",
        "accountable_manager",
        "liaison",
        "update_date",
    ]
    if "nearest_airport_id" in df.columns:
        output_cols += [
            "nearest_airport_id",
            "nearest_airport_name",
            "airport_distance_nm",
            "match_method",
        ]

    output_path = OUTPUT_DIR / "faa141_schools.csv"
    df[output_cols].to_csv(output_path, index=False)
    print(f"  Saved {len(df):,} schools to {output_path}")

    # Summary stats
    print(f"\n  State distribution (top 10):")
    top_states = df["state"].value_counts().head(10)
    for state, count in top_states.items():
        print(f"    {state}: {count:,}")

    if "nearest_airport_id" in df.columns and (df["nearest_airport_id"] != "").any():
        print(f"\n  Top airports by school count:")
        top_airports = (
            df[df["nearest_airport_id"] != ""]["nearest_airport_id"]
            .value_counts()
            .head(15)
        )
        for apt, count in top_airports.items():
            print(f"    {apt}: {count}")

        # Match method breakdown
        if "match_method" in df.columns:
            print(f"\n  Match method breakdown:")
            methods = df[df["match_method"] != ""]["match_method"].value_counts()
            for method, count in methods.items():
                print(f"    {method}: {count:,}")

    print(f"\nPhase 0 complete.")
    return df


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Phase 0: FAA 141 School Ingest")
    parser.add_argument(
        "--geocode", action="store_true", default=False,
        help="Enable Nominatim geocoding for unmatched schools (slow, ~1 req/18s)",
    )
    args = parser.parse_args()
    run(geocode=args.geocode)
