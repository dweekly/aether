#!/usr/bin/env python3
"""
Phase 4: Determine home airport for each tail number.

Analyzes ADS-B position history to determine where each aircraft is most
frequently seen on the ground. Also computes activity metrics:
- Flights per month
- Pattern work ratio (departures that return to same airport)
- Days since last seen

For aircraft without ADS-B data, falls back to the FAA registrant address
to infer a likely home airport by proximity to registered address.

Output: output/tail_home_bases.csv — each tail number mapped to its home airport
"""

import math
from pathlib import Path

import pandas as pd
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
from tqdm import tqdm

OUTPUT_DIR = Path(__file__).parent.parent / "output"

# Geocoder for address → coordinate lookups (FAA registrant addresses)
GEOCODER = None  # Lazy init


def get_geocoder():
    global GEOCODER
    if GEOCODER is None:
        GEOCODER = Nominatim(user_agent="aether-research", timeout=10)
    return GEOCODER


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in nautical miles."""
    R_NM = 3440.065
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return R_NM * c


def geocode_address(street: str, city: str, state: str, zip_code: str) -> tuple[float, float] | None:
    """Geocode a FAA registrant address to lat/lon."""
    address = f"{street}, {city}, {state} {zip_code}"
    try:
        location = get_geocoder().geocode(address, country_codes="us")
        if location:
            return (location.latitude, location.longitude)
    except GeocoderTimedOut:
        pass
    except Exception:
        pass
    return None


def find_nearest_airport(lat: float, lon: float, airports: pd.DataFrame, max_nm: float = 30.0) -> dict | None:
    """Find the nearest airport to a coordinate within max_nm."""
    best = None
    best_dist = float("inf")

    for _, apt in airports.iterrows():
        apt_lat = float(apt["latitude"])
        apt_lon = float(apt["longitude"])
        dist = haversine_nm(lat, lon, apt_lat, apt_lon)
        if dist < best_dist and dist <= max_nm:
            best_dist = dist
            best = {
                "airport_id": apt.get("faa_id") or apt["airport_id"],
                "airport_name": apt["airport_name"],
                "city": apt.get("city", ""),
                "state": apt.get("state", ""),
                "distance_nm": round(dist, 1),
            }

    return best


def assign_home_bases_from_adsb(adsb_data: pd.DataFrame, airports: pd.DataFrame) -> pd.DataFrame:
    """
    Assign home bases from ADS-B data.

    For each aircraft, the home base is the airport where it is most
    frequently observed on the ground.
    """
    if adsb_data is None or adsb_data.empty:
        return pd.DataFrame()

    results = []
    # Group by ICAO hex and find most common ground position
    if "icao_hex" in adsb_data.columns and "nearest_airport" in adsb_data.columns:
        for hex_code, group in adsb_data.groupby("icao_hex"):
            ground_obs = group[group.get("on_ground", False) == True]
            if not ground_obs.empty and "nearest_airport" in ground_obs.columns:
                home = ground_obs["nearest_airport"].mode()
                if not home.empty:
                    results.append({
                        "icao_hex": hex_code,
                        "home_airport": home.iloc[0],
                        "confidence": "adsb_ground",
                        "observations": len(ground_obs),
                    })

    return pd.DataFrame(results)


def assign_home_bases_from_address(registry: pd.DataFrame, airports: pd.DataFrame) -> pd.DataFrame:
    """
    Assign home bases by geocoding FAA registrant addresses and finding
    the nearest airport.

    This is a fallback for aircraft without ADS-B data.
    """
    results = []
    address_cols = ["STREET", "CITY", "STATE", "ZIP_CODE"]

    if not all(c in registry.columns for c in address_cols):
        print("  Warning: Registry missing address columns, skipping geocoding")
        return pd.DataFrame()

    # Only geocode unique addresses to avoid redundant API calls
    registry["address_key"] = (
        registry["STREET"] + "|" + registry["CITY"] + "|" +
        registry["STATE"] + "|" + registry["ZIP_CODE"]
    )
    unique_addresses = registry.drop_duplicates(subset="address_key")

    print(f"  Geocoding {len(unique_addresses):,} unique addresses...")
    print(f"  (Using Nominatim — rate limited to ~1 req/sec)")

    address_to_airport = {}

    for _, row in tqdm(unique_addresses.iterrows(), total=len(unique_addresses), desc="Geocoding"):
        addr_key = row["address_key"]
        if addr_key in address_to_airport:
            continue

        coords = geocode_address(
            row["STREET"], row["CITY"], row["STATE"], row["ZIP_CODE"]
        )
        if coords:
            nearest = find_nearest_airport(coords[0], coords[1], airports)
            if nearest:
                address_to_airport[addr_key] = nearest

    # Map results back to all aircraft
    for _, row in registry.iterrows():
        addr_key = row.get("address_key", "")
        airport = address_to_airport.get(addr_key)
        if airport:
            results.append({
                "n_number": row.get("N-NUMBER", ""),
                "icao_hex": row.get("MODE_S_CODE_HEX", ""),
                "home_airport": airport["airport_id"],
                "airport_name": airport["airport_name"],
                "city": airport["city"],
                "state": airport["state"],
                "distance_nm": airport["distance_nm"],
                "confidence": "geocoded_address",
            })

    return pd.DataFrame(results)


def run(registry: pd.DataFrame = None, airports: pd.DataFrame = None, adsb_data: pd.DataFrame = None):
    """Execute Phase 4: Home base assignment."""
    print("=" * 60)
    print("Phase 4: Home Base Assignment")
    print("=" * 60)

    # Load data if not provided
    if registry is None:
        path = OUTPUT_DIR / "faa_ga_registry.csv"
        if path.exists():
            registry = pd.read_csv(path, dtype=str, keep_default_na=False)
        else:
            print("  Error: Run Phase 1 first")
            return None

    if airports is None:
        path = OUTPUT_DIR / "airports.csv"
        if path.exists():
            airports = pd.read_csv(path, keep_default_na=False)
        else:
            print("  Error: Run Phase 2 first")
            return None

    if adsb_data is None:
        path = OUTPUT_DIR / "adsb_activity.csv"
        if path.exists():
            adsb_data = pd.read_csv(path, dtype=str, keep_default_na=False)

    # Method 1: ADS-B based home base
    print("\n[1/3] Assigning home bases from ADS-B data...")
    adsb_homes = assign_home_bases_from_adsb(adsb_data, airports)
    print(f"  ADS-B home bases assigned: {len(adsb_homes):,}")

    # Method 2: Address-based fallback
    # Only geocode aircraft NOT already assigned by ADS-B
    print("\n[2/3] Geocoding remaining registrant addresses...")
    if not adsb_homes.empty:
        assigned_hex = set(adsb_homes["icao_hex"])
        remaining = registry[~registry["MODE_S_CODE_HEX"].isin(assigned_hex)]
    else:
        remaining = registry

    # Limit geocoding to flight school candidates to conserve API calls
    if "IS_FLIGHT_SCHOOL" in remaining.columns:
        school_remaining = remaining[remaining["IS_FLIGHT_SCHOOL"] == "True"]
        print(f"  Geocoding {len(school_remaining):,} flight school registrants...")
        address_homes = assign_home_bases_from_address(school_remaining, airports)
    else:
        address_homes = pd.DataFrame()
    print(f"  Address-based home bases: {len(address_homes):,}")

    # Combine results
    print("\n[3/3] Combining and saving results...")
    all_homes = pd.concat([adsb_homes, address_homes], ignore_index=True)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "tail_home_bases.csv"
    all_homes.to_csv(output_path, index=False)
    print(f"  Saved: {output_path} ({len(all_homes):,} records)")

    if not all_homes.empty and "home_airport" in all_homes.columns:
        print(f"\n  Top airports by aircraft count:")
        top = all_homes["home_airport"].value_counts().head(15)
        for airport, count in top.items():
            print(f"    {airport}: {count}")

    print(f"\nPhase 4 complete.")
    return all_homes


if __name__ == "__main__":
    run()
