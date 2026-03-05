#!/usr/bin/env python3
"""
Phase 10: Scheduled ADS-B snapshot capture.

Takes ADS-B snapshots at 10 AM, noon, and 2 PM local time every day for two
weeks. Three midday captures per day give good statistical coverage of active
GA training aircraft without excessive API usage.

After the two-week collection window, aggregates the data to produce:
  - Per-aircraft activity summary (sightings, on-ground %, home airport)
  - Per-airport traffic counts
  - High-confidence home-base assignments

Usage:
    # Start the capture daemon (runs for 14 days)
    python 10_adsb_scheduled.py --start

    # Take a single snapshot now (useful for testing)
    python 10_adsb_scheduled.py --snapshot

    # Aggregate all collected snapshots into activity summary
    python 10_adsb_scheduled.py --aggregate

    # Show capture schedule and status
    python 10_adsb_scheduled.py --status

Environment:
    OPENSKY_USER / OPENSKY_PASS — OpenSky Network credentials (optional, higher rate limits)
    ADSBX_API_KEY — ADS-B Exchange RapidAPI key (alternative source)
    TZ — timezone for scheduling (default: America/New_York)
"""

import json
import math
import os
import sched
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).parent.parent / "data" / "adsb"
OUTPUT_DIR = Path(__file__).parent.parent / "output"

# Capture configuration
CAPTURE_HOURS_LOCAL = [10, 12, 14]  # 10 AM, noon, 2 PM
CAPTURE_DAYS = 14
SNAPSHOTS_DIR = DATA_DIR / "snapshots"

# OpenSky Network (free, public)
OPENSKY_ALL_URL = "https://opensky-network.org/api/states/all"
OPENSKY_USER = os.environ.get("OPENSKY_USER", "")
OPENSKY_PASS = os.environ.get("OPENSKY_PASS", "")

# US bounding box for filtering
US_BBOX = {
    "lamin": 24.0,   # south
    "lamax": 50.0,   # north
    "lomin": -125.0, # west
    "lomax": -66.0,  # east
}

# Airport proximity matching
AIRPORT_PROXIMITY_NM = 2.0
R_NM = 3440.065  # Earth radius in nautical miles


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance between two points in nautical miles."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R_NM * 2 * math.asin(math.sqrt(a))


def fetch_opensky_snapshot() -> list[dict]:
    """Fetch current US aircraft states from OpenSky Network."""
    params = {
        "lamin": US_BBOX["lamin"],
        "lamax": US_BBOX["lamax"],
        "lomin": US_BBOX["lomin"],
        "lomax": US_BBOX["lomax"],
    }
    auth = (OPENSKY_USER, OPENSKY_PASS) if OPENSKY_USER else None

    for attempt in range(3):
        try:
            resp = requests.get(
                OPENSKY_ALL_URL, params=params, auth=auth, timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                states = data.get("states", [])
                ts = data.get("time", int(time.time()))
                return _parse_opensky_states(states, ts)
            if resp.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"  Rate limited, waiting {wait}s...")
                time.sleep(wait)
        except requests.RequestException as e:
            print(f"  Fetch error (attempt {attempt + 1}): {e}")
            time.sleep(5 * (attempt + 1))

    return []


def _parse_opensky_states(states: list, timestamp: int) -> list[dict]:
    """Parse OpenSky state vectors into structured dicts."""
    # OpenSky state vector field indices
    FIELDS = [
        "icao_hex", "callsign", "origin_country", "time_position",
        "last_contact", "longitude", "latitude", "altitude_m",
        "on_ground", "velocity_ms", "heading", "vertical_rate",
        "sensors", "geo_altitude_m", "squawk", "spi", "category",
    ]
    records = []
    for sv in states:
        rec = {}
        for i, field in enumerate(FIELDS):
            if i < len(sv):
                rec[field] = sv[i]
        rec["timestamp"] = timestamp
        # Only keep US-registered (ICAO hex starts with A)
        hex_code = (rec.get("icao_hex") or "").strip().upper()
        if hex_code.startswith("A"):
            rec["icao_hex"] = hex_code
            rec["callsign"] = (rec.get("callsign") or "").strip()
            records.append(rec)
    return records


def save_snapshot(records: list[dict], label: str = "") -> Path:
    """Save a snapshot to the snapshots directory."""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    ts_str = now.strftime("%Y%m%d_%H%M%S")
    filename = f"snapshot_{ts_str}.json"
    if label:
        filename = f"snapshot_{ts_str}_{label}.json"
    path = SNAPSHOTS_DIR / filename
    with open(path, "w") as f:
        json.dump({
            "capture_utc": now.isoformat(),
            "total_aircraft": len(records),
            "on_ground": sum(1 for r in records if r.get("on_ground")),
            "aircraft": records,
        }, f)
    return path


def take_snapshot() -> Path | None:
    """Take a single ADS-B snapshot now."""
    print(f"  [{datetime.now().strftime('%H:%M:%S')}] Fetching ADS-B snapshot...")
    records = fetch_opensky_snapshot()
    if records:
        path = save_snapshot(records)
        on_ground = sum(1 for r in records if r.get("on_ground"))
        print(f"  Captured {len(records)} aircraft ({on_ground} on ground) -> {path.name}")
        return path
    else:
        print("  Warning: No data returned from OpenSky")
        return None


def aggregate_snapshots():
    """
    Aggregate all collected snapshots into activity summaries.

    For each aircraft (ICAO hex), computes:
      - Total sightings across all snapshots
      - % of sightings on ground
      - Most-frequent ground airport (home base candidate)
      - Sighting count at each airport
    """
    print("=" * 60)
    print("Aggregating ADS-B snapshots")
    print("=" * 60)

    if not SNAPSHOTS_DIR.exists():
        print("  No snapshots directory found.")
        return None

    snapshot_files = sorted(SNAPSHOTS_DIR.glob("snapshot_*.json"))
    print(f"  Snapshot files found: {len(snapshot_files)}")
    if not snapshot_files:
        return None

    # Load airport reference for proximity matching
    airports_path = OUTPUT_DIR / "airports.csv"
    airport_coords = None
    if airports_path.exists():
        airports = pd.read_csv(airports_path, keep_default_na=False)
        airport_coords = airports[
            airports["latitude"].notna() & airports["longitude"].notna()
        ][["airport_id", "faa_id", "latitude", "longitude"]].copy()
        airport_coords["latitude"] = airport_coords["latitude"].astype(float)
        airport_coords["longitude"] = airport_coords["longitude"].astype(float)
        print(f"  Airport reference loaded: {len(airport_coords)} airports")

    # Process all snapshots
    from collections import Counter, defaultdict

    aircraft_sightings: dict[str, int] = Counter()
    aircraft_ground: dict[str, int] = Counter()
    aircraft_airports: dict[str, Counter] = defaultdict(Counter)

    for snap_path in snapshot_files:
        with open(snap_path) as f:
            snap = json.load(f)
        for ac in snap.get("aircraft", []):
            hex_code = ac.get("icao_hex", "")
            if not hex_code:
                continue
            aircraft_sightings[hex_code] += 1
            if ac.get("on_ground"):
                aircraft_ground[hex_code] += 1
                # Find nearest airport
                lat = ac.get("latitude")
                lon = ac.get("longitude")
                if lat and lon and airport_coords is not None:
                    nearest = _find_nearest_airport(
                        float(lat), float(lon), airport_coords
                    )
                    if nearest:
                        aircraft_airports[hex_code][nearest] += 1

    # Build summary
    print(f"\n  Unique aircraft seen: {len(aircraft_sightings)}")

    summary_records = []
    for hex_code in sorted(aircraft_sightings, key=aircraft_sightings.get, reverse=True):
        total = aircraft_sightings[hex_code]
        ground = aircraft_ground.get(hex_code, 0)
        airport_counts = aircraft_airports.get(hex_code, Counter())
        home_airport = airport_counts.most_common(1)[0][0] if airport_counts else ""
        home_count = airport_counts.most_common(1)[0][1] if airport_counts else 0

        summary_records.append({
            "icao_hex": hex_code,
            "total_sightings": total,
            "on_ground_count": ground,
            "on_ground_pct": round(ground / total, 3) if total else 0,
            "home_airport": home_airport,
            "home_airport_sightings": home_count,
            "airports_seen": dict(airport_counts),
            "n_snapshots": len(snapshot_files),
        })

    # Save
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    summary_path = OUTPUT_DIR / "adsb_activity_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary_records, f, indent=2)

    csv_records = [{k: v for k, v in r.items() if k != "airports_seen"} for r in summary_records]
    csv_path = OUTPUT_DIR / "adsb_activity.csv"
    pd.DataFrame(csv_records).to_csv(csv_path, index=False)

    print(f"  Activity summary: {summary_path}")
    print(f"  Activity CSV: {csv_path}")
    print(f"  Total unique aircraft: {len(summary_records)}")
    ground_based = sum(1 for r in summary_records if r["on_ground_pct"] > 0.5)
    print(f"  Primarily ground-based (>50%): {ground_based}")
    with_home = sum(1 for r in summary_records if r["home_airport"])
    print(f"  With home airport assignment: {with_home}")

    return summary_records


def _find_nearest_airport(lat: float, lon: float, airports: pd.DataFrame) -> str:
    """Find the nearest airport within AIRPORT_PROXIMITY_NM."""
    min_dist = float("inf")
    nearest = ""
    for _, apt in airports.iterrows():
        dist = haversine_nm(lat, lon, apt["latitude"], apt["longitude"])
        if dist < min_dist:
            min_dist = dist
            nearest = apt["faa_id"] or apt["airport_id"]
    if min_dist <= AIRPORT_PROXIMITY_NM:
        return nearest
    return ""


def show_status():
    """Show capture schedule status."""
    print("=" * 60)
    print("ADS-B Capture Status")
    print("=" * 60)

    if SNAPSHOTS_DIR.exists():
        files = sorted(SNAPSHOTS_DIR.glob("snapshot_*.json"))
        print(f"  Snapshots collected: {len(files)}")
        if files:
            first = files[0].stem.replace("snapshot_", "")[:8]
            last = files[-1].stem.replace("snapshot_", "")[:8]
            print(f"  Date range: {first} to {last}")

            # Expected total: 3 per day * 14 days = 42
            expected = CAPTURE_DAYS * len(CAPTURE_HOURS_LOCAL)
            pct = len(files) / expected * 100 if expected else 0
            print(f"  Expected total: {expected}")
            print(f"  Completion: {pct:.0f}%")
    else:
        print("  No snapshots collected yet.")

    print(f"\n  Schedule: {CAPTURE_HOURS_LOCAL} local time")
    print(f"  Duration: {CAPTURE_DAYS} days")
    print(f"  OpenSky credentials: {'configured' if OPENSKY_USER else 'anonymous (lower rate limits)'}")


def run_scheduled_capture():
    """
    Run the scheduled capture loop for CAPTURE_DAYS days.

    Uses Python's sched module to fire at the configured hours.
    Designed to be run as a background process or cron-triggered.
    """
    print("=" * 60)
    print("ADS-B Scheduled Capture — Starting")
    print("=" * 60)
    print(f"  Capture hours (local): {CAPTURE_HOURS_LOCAL}")
    print(f"  Duration: {CAPTURE_DAYS} days")
    print(f"  Total snapshots planned: {CAPTURE_DAYS * len(CAPTURE_HOURS_LOCAL)}")
    print()

    scheduler = sched.scheduler(time.time, time.sleep)
    now = datetime.now()
    end_date = now + timedelta(days=CAPTURE_DAYS)

    # Schedule all captures
    scheduled = 0
    current_day = now.date()
    for day_offset in range(CAPTURE_DAYS + 1):
        day = now.date() + timedelta(days=day_offset)
        for hour in CAPTURE_HOURS_LOCAL:
            capture_time = datetime.combine(day, datetime.min.time().replace(hour=hour))
            if capture_time <= now:
                continue
            if capture_time.date() > end_date.date():
                continue
            delay = (capture_time - now).total_seconds()
            scheduler.enter(delay, 1, take_snapshot)
            scheduled += 1

    print(f"  Scheduled {scheduled} captures from now through {end_date.date()}")
    print(f"  First capture at next scheduled hour. Running...\n")

    try:
        scheduler.run()
    except KeyboardInterrupt:
        print("\n  Capture interrupted by user.")
    finally:
        print(f"\n  Capture session ended. Run --aggregate to process results.")


def run():
    """Execute Phase 10: Take a single snapshot (for pipeline integration)."""
    print("=" * 60)
    print("Phase 10: ADS-B Snapshot")
    print("=" * 60)
    take_snapshot()
    print("Phase 10 complete.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scheduled ADS-B snapshot capture")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--start", action="store_true", help="Start 14-day capture daemon")
    group.add_argument("--snapshot", action="store_true", help="Take one snapshot now")
    group.add_argument("--aggregate", action="store_true", help="Aggregate collected snapshots")
    group.add_argument("--status", action="store_true", help="Show capture status")
    args = parser.parse_args()

    if args.start:
        run_scheduled_capture()
    elif args.snapshot:
        take_snapshot()
    elif args.aggregate:
        aggregate_snapshots()
    elif args.status:
        show_status()
