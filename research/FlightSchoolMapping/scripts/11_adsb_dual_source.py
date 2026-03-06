#!/usr/bin/env python3
"""
Phase 11: Dual-source ADS-B snapshot capture (adsb.lol + OpenSky).

Takes continuous ADS-B snapshots every 15 minutes from two free sources:
  - adsb.lol  — unfiltered, no auth required, richer data fields
  - OpenSky Network — single bounding-box call for full US coverage

Records are deduplicated by ICAO hex, preferring adsb.lol data (richer fields).
After collection, aggregates snapshots to determine fleet activity and home bases.

Usage:
    # Take a single dual-source snapshot now (testing)
    python 11_adsb_dual_source.py --snapshot

    # Run continuous capture every 15 min for N days (default 30)
    python 11_adsb_dual_source.py --start [--days 30] [--interval 15]

    # Aggregate all collected snapshots
    python 11_adsb_dual_source.py --aggregate

    # Show status
    python 11_adsb_dual_source.py --status

    # Compare source coverage (single snapshot, prints stats)
    python 11_adsb_dual_source.py --compare
"""

import json
import math
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from scipy.spatial import cKDTree

DATA_DIR = Path(__file__).parent.parent / "data" / "adsb"
OUTPUT_DIR = Path(__file__).parent.parent / "output"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"

# ── Source configuration ──────────────────────────────────────────────

# adsb.lol: free, no auth, unfiltered.  Rate limit: 1 req/sec.
ADSBLOL_BASE = "https://api.adsb.lol/v2"

# Tile the continental US with overlapping 250nm-radius circles.
US_TILES = [
    (41.0, -74.0),   # Northeast
    (38.0, -77.0),   # Mid-Atlantic
    (33.0, -84.0),   # Southeast
    (27.0, -81.0),   # Florida
    (41.0, -87.0),   # Midwest
    (39.0, -95.0),   # Plains
    (30.0, -90.0),   # Gulf
    (32.0, -97.0),   # Texas
    (40.0, -105.0),  # Mountain
    (34.0, -118.0),  # SoCal
    (38.0, -122.0),  # NorCal
    (47.0, -122.0),  # Pacific NW
]

# OpenSky Network: free, optional auth for higher rate limits.
OPENSKY_URL = "https://opensky-network.org/api/states/all"
OPENSKY_USER = os.environ.get("OPENSKY_USER", "")
OPENSKY_PASS = os.environ.get("OPENSKY_PASS", "")
US_BBOX = {"lamin": 24.0, "lamax": 50.0, "lomin": -125.0, "lomax": -66.0}

# Airport proximity
AIRPORT_PROXIMITY_NM = 2.0
R_NM = 3440.065


# ── Fetchers ──────────────────────────────────────────────────────────

def fetch_adsblol_snapshot() -> dict[str, dict]:
    """Fetch US aircraft from adsb.lol by tiling 250nm circles.

    Returns dict keyed by uppercase ICAO hex.
    """
    aircraft = {}
    for lat, lon in US_TILES:
        try:
            r = requests.get(
                f"{ADSBLOL_BASE}/lat/{lat}/lon/{lon}/dist/250",
                timeout=15,
            )
            if r.status_code == 200:
                for ac in r.json().get("ac", []):
                    hex_code = (ac.get("hex") or "").strip().upper()
                    if hex_code.startswith("A") and hex_code not in aircraft:
                        aircraft[hex_code] = _normalise_adsblol(ac)
            elif r.status_code == 429:
                print(f"  adsb.lol rate limited, pausing...")
                time.sleep(5)
        except requests.RequestException as e:
            print(f"  adsb.lol error ({lat},{lon}): {e}")
        time.sleep(1.1)  # respect rate limit
    return aircraft


def _normalise_adsblol(ac: dict) -> dict:
    """Normalise an adsb.lol record to our common schema."""
    hex_code = (ac.get("hex") or "").strip().upper()
    on_ground = ac.get("alt_baro") == "ground"
    return {
        "icao_hex": hex_code,
        "callsign": (ac.get("flight") or "").strip(),
        "registration": (ac.get("r") or "").strip(),
        "aircraft_type": (ac.get("t") or "").strip(),
        "latitude": ac.get("lat"),
        "longitude": ac.get("lon"),
        "altitude_baro_ft": None if on_ground else ac.get("alt_baro"),
        "altitude_geo_ft": ac.get("alt_geom"),
        "ground_speed_kt": ac.get("gs"),
        "heading": ac.get("track"),
        "vertical_rate_fpm": ac.get("baro_rate"),
        "on_ground": on_ground,
        "squawk": ac.get("squawk"),
        "category": ac.get("category"),
        "source": "adsb.lol",
    }


def fetch_opensky_snapshot() -> dict[str, dict]:
    """Fetch US aircraft from OpenSky Network.

    Returns dict keyed by uppercase ICAO hex.
    """
    auth = (OPENSKY_USER, OPENSKY_PASS) if OPENSKY_USER else None
    aircraft = {}

    for attempt in range(3):
        try:
            r = requests.get(
                OPENSKY_URL, params=US_BBOX, auth=auth, timeout=30,
            )
            if r.status_code == 200:
                data = r.json()
                ts = data.get("time", int(time.time()))
                for sv in data.get("states", []):
                    rec = _normalise_opensky(sv, ts)
                    if rec:
                        aircraft[rec["icao_hex"]] = rec
                return aircraft
            if r.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"  OpenSky rate limited, waiting {wait}s...")
                time.sleep(wait)
        except requests.RequestException as e:
            print(f"  OpenSky error (attempt {attempt + 1}): {e}")
            time.sleep(5 * (attempt + 1))

    return aircraft


def _normalise_opensky(sv: list, timestamp: int) -> dict | None:
    """Normalise an OpenSky state vector to our common schema."""
    hex_code = (sv[0] or "").strip().upper()
    if not hex_code.startswith("A"):
        return None
    on_ground = bool(sv[8])
    alt_m = sv[7]
    geo_m = sv[13]
    vel_ms = sv[9]
    vr_ms = sv[11]
    return {
        "icao_hex": hex_code,
        "callsign": (sv[1] or "").strip(),
        "registration": "",  # OpenSky doesn't provide this
        "aircraft_type": "",
        "latitude": sv[6],
        "longitude": sv[5],
        "altitude_baro_ft": round(alt_m * 3.28084) if alt_m is not None else None,
        "altitude_geo_ft": round(geo_m * 3.28084) if geo_m is not None else None,
        "ground_speed_kt": round(vel_ms * 1.94384, 1) if vel_ms is not None else None,
        "heading": sv[10],
        "vertical_rate_fpm": round(vr_ms * 196.85, 0) if vr_ms is not None else None,
        "on_ground": on_ground,
        "squawk": sv[14],
        "category": sv[16] if len(sv) > 16 else None,
        "source": "opensky",
    }


# ── Snapshot operations ───────────────────────────────────────────────

def take_dual_snapshot() -> Path | None:
    """Take a merged snapshot from both sources.

    adsb.lol data is preferred (richer fields).  OpenSky fills gaps.
    """
    now = datetime.now(timezone.utc)
    print(f"  [{now.strftime('%H:%M:%S UTC')}] Fetching dual-source snapshot...")

    # Fetch both sources (adsb.lol first since it's rate-limited per tile)
    lol = fetch_adsblol_snapshot()
    print(f"    adsb.lol: {len(lol)} US aircraft")

    osky = fetch_opensky_snapshot()
    print(f"    OpenSky:  {len(osky)} US aircraft")

    # Merge: adsb.lol wins on overlap
    merged = dict(lol)  # start with all adsb.lol records
    opensky_only = 0
    for hex_code, rec in osky.items():
        if hex_code not in merged:
            merged[hex_code] = rec
            opensky_only += 1

    on_ground = sum(1 for r in merged.values() if r.get("on_ground"))
    both = len(lol) + len(osky) - len(merged)

    print(f"    Merged:   {len(merged)} unique aircraft "
          f"({len(lol)} lol + {opensky_only} opensky-only, {both} overlap)")
    print(f"    On ground: {on_ground}")

    if not merged:
        print("  Warning: No data from either source")
        return None

    # Save
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    ts_str = now.strftime("%Y%m%d_%H%M%S")
    path = SNAPSHOTS_DIR / f"dual_{ts_str}.json"
    with open(path, "w") as f:
        json.dump({
            "capture_utc": now.isoformat(),
            "sources": {
                "adsb_lol": len(lol),
                "opensky": len(osky),
                "overlap": both,
                "opensky_only": opensky_only,
            },
            "total_aircraft": len(merged),
            "on_ground": on_ground,
            "aircraft": list(merged.values()),
        }, f)
    print(f"    Saved: {path.name} ({path.stat().st_size / 1024:.0f} KB)")
    return path


def compare_sources():
    """Take one snapshot from each source and compare coverage."""
    print("=" * 60)
    print("Source Coverage Comparison")
    print("=" * 60)

    lol = fetch_adsblol_snapshot()
    osky = fetch_opensky_snapshot()

    lol_set = set(lol.keys())
    osky_set = set(osky.keys())
    both = lol_set & osky_set
    lol_only = lol_set - osky_set
    osky_only = osky_set - lol_set

    print(f"\n  adsb.lol:     {len(lol_set):5d} US aircraft")
    print(f"  OpenSky:      {len(osky_set):5d} US aircraft")
    print(f"  Both sources: {len(both):5d} (overlap)")
    print(f"  adsb.lol only:{len(lol_only):5d}")
    print(f"  OpenSky only: {len(osky_only):5d}")
    print(f"  Union total:  {len(lol_set | osky_set):5d}")

    # Ground aircraft comparison
    lol_ground = {h for h, r in lol.items() if r.get("on_ground")}
    osky_ground = {h for h, r in osky.items() if r.get("on_ground")}
    print(f"\n  On ground (adsb.lol): {len(lol_ground)}")
    print(f"  On ground (OpenSky):  {len(osky_ground)}")
    print(f"  On ground (union):    {len(lol_ground | osky_ground)}")

    # Check if adsb.lol provides registration / type for its aircraft
    with_reg = sum(1 for r in lol.values() if r.get("registration"))
    with_type = sum(1 for r in lol.values() if r.get("aircraft_type"))
    print(f"\n  adsb.lol extras: {with_reg} with registration, {with_type} with aircraft type")

    # Check fleet match
    n_numbers_path = OUTPUT_DIR / "n_numbers_seen.txt"
    if n_numbers_path.exists():
        fleet_n = set(n_numbers_path.read_text().strip().split("\n"))
        lol_regs = {r.get("registration") for r in lol.values() if r.get("registration")}
        fleet_seen = fleet_n & lol_regs
        print(f"\n  Fleet N-numbers on file: {len(fleet_n)}")
        print(f"  Fleet aircraft currently visible (adsb.lol): {len(fleet_seen)}")
        if fleet_seen:
            print(f"  Sample: {list(fleet_seen)[:10]}")


# ── Continuous capture ────────────────────────────────────────────────

def run_continuous(days: int = 30, interval_min: int = 15):
    """Run continuous dual-source capture."""
    print("=" * 60)
    print("Dual-Source ADS-B Continuous Capture")
    print("=" * 60)

    total_snapshots = days * 24 * 60 // interval_min
    print(f"  Interval: every {interval_min} minutes")
    print(f"  Duration: {days} days")
    print(f"  Expected snapshots: {total_snapshots}")
    print(f"  Estimated storage: ~{total_snapshots * 1.5:.0f} MB")
    print()

    end_time = datetime.now(timezone.utc) + timedelta(days=days)
    count = 0

    try:
        while datetime.now(timezone.utc) < end_time:
            take_dual_snapshot()
            count += 1
            remaining = (end_time - datetime.now(timezone.utc)).total_seconds()
            if remaining > interval_min * 60:
                print(f"    Next snapshot in {interval_min} min "
                      f"(#{count}/{total_snapshots}, "
                      f"{remaining / 3600:.1f}h remaining)\n")
                time.sleep(interval_min * 60)
            else:
                break
    except KeyboardInterrupt:
        print(f"\n  Interrupted after {count} snapshots.")
    finally:
        print(f"\n  Capture complete: {count} snapshots collected.")
        print(f"  Run --aggregate to process results.")


# ── Aggregation ───────────────────────────────────────────────────────

def haversine_nm(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R_NM * 2 * math.asin(math.sqrt(a))


class AirportIndex:
    """KD-tree for fast nearest-airport lookups."""

    def __init__(self, airports: pd.DataFrame):
        lats = airports["latitude"].values.astype(float)
        lons = airports["longitude"].values.astype(float)
        cos_lat = np.cos(np.radians(lats))
        self._tree = cKDTree(np.column_stack([lons * 60.0 * cos_lat, lats * 60.0]))
        self._ids = [
            (row["faa_id"] or row["airport_id"])
            for _, row in airports.iterrows()
        ]
        self._lats = lats
        self._lons = lons

    def nearest(self, lat: float, lon: float) -> str:
        cos_lat = math.cos(math.radians(lat))
        dist_approx, idx = self._tree.query([lon * 60.0 * cos_lat, lat * 60.0])
        true_dist = haversine_nm(lat, lon, self._lats[idx], self._lons[idx])
        if true_dist <= AIRPORT_PROXIMITY_NM:
            return self._ids[idx]
        return ""


def aggregate_snapshots():
    """Aggregate all dual-source snapshots into activity summaries."""
    print("=" * 60)
    print("Aggregating Dual-Source ADS-B Snapshots")
    print("=" * 60)

    if not SNAPSHOTS_DIR.exists():
        print("  No snapshots directory found.")
        return None

    # Include both old single-source and new dual-source snapshots
    snap_files = sorted(
        list(SNAPSHOTS_DIR.glob("dual_*.json"))
        + list(SNAPSHOTS_DIR.glob("snapshot_*.json"))
    )
    print(f"  Snapshot files: {len(snap_files)}")
    if not snap_files:
        return None

    # Load airport index
    airports_path = OUTPUT_DIR / "airports.csv"
    apt_index = None
    if airports_path.exists():
        airports = pd.read_csv(airports_path, keep_default_na=False)
        valid = airports[airports["latitude"].notna() & airports["longitude"].notna()].copy()
        valid["latitude"] = valid["latitude"].astype(float)
        valid["longitude"] = valid["longitude"].astype(float)
        apt_index = AirportIndex(valid[["airport_id", "faa_id", "latitude", "longitude"]])
        print(f"  Airport index: {len(valid)} airports")

    # Process snapshots
    sightings: Counter = Counter()
    ground_count: Counter = Counter()
    airport_counts: dict[str, Counter] = defaultdict(Counter)
    registrations: dict[str, str] = {}   # hex -> N-number
    aircraft_types: dict[str, str] = {}  # hex -> type code
    sources_seen: dict[str, set] = defaultdict(set)

    for snap_path in snap_files:
        with open(snap_path) as f:
            snap = json.load(f)
        for ac in snap.get("aircraft", []):
            hex_code = (ac.get("icao_hex") or ac.get("hex", "")).upper()
            if not hex_code:
                continue

            sightings[hex_code] += 1
            source = ac.get("source", "unknown")
            sources_seen[hex_code].add(source)

            # Track registration and type from adsb.lol
            reg = ac.get("registration", "")
            if reg:
                registrations[hex_code] = reg
            atype = ac.get("aircraft_type", "")
            if atype:
                aircraft_types[hex_code] = atype

            on_ground = ac.get("on_ground", False)
            if on_ground:
                ground_count[hex_code] += 1
                lat = ac.get("latitude")
                lon = ac.get("longitude")
                if lat and lon and apt_index:
                    nearest = apt_index.nearest(float(lat), float(lon))
                    if nearest:
                        airport_counts[hex_code][nearest] += 1

    print(f"\n  Unique aircraft: {len(sightings)}")

    # Build summary
    records = []
    for hex_code in sorted(sightings, key=sightings.get, reverse=True):
        total = sightings[hex_code]
        ground = ground_count.get(hex_code, 0)
        apt = airport_counts.get(hex_code, Counter())
        home = apt.most_common(1)[0] if apt else ("", 0)

        records.append({
            "icao_hex": hex_code,
            "registration": registrations.get(hex_code, ""),
            "aircraft_type": aircraft_types.get(hex_code, ""),
            "total_sightings": total,
            "on_ground_count": ground,
            "on_ground_pct": round(ground / total, 3) if total else 0,
            "home_airport": home[0],
            "home_airport_sightings": home[1],
            "airports_seen": dict(apt),
            "n_snapshots": len(snap_files),
            "sources": list(sources_seen.get(hex_code, set())),
        })

    # Save
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    summary_path = OUTPUT_DIR / "adsb_activity_summary.json"
    with open(summary_path, "w") as f:
        json.dump(records, f, indent=2)

    csv_records = [
        {k: v for k, v in r.items() if k not in ("airports_seen", "sources")}
        for r in records
    ]
    csv_path = OUTPUT_DIR / "adsb_activity.csv"
    pd.DataFrame(csv_records).to_csv(csv_path, index=False)

    # Fleet cross-reference
    n_numbers_path = OUTPUT_DIR / "n_numbers_seen.txt"
    fleet_match = 0
    if n_numbers_path.exists():
        fleet_n = set(n_numbers_path.read_text().strip().split("\n"))
        seen_regs = set(registrations.values())
        fleet_match = len(fleet_n & seen_regs)

    print(f"  Saved: {summary_path}")
    print(f"  Saved: {csv_path}")
    print(f"  Total unique aircraft: {len(records)}")
    ground_based = sum(1 for r in records if r["on_ground_pct"] > 0.5)
    print(f"  Primarily ground-based (>50%): {ground_based}")
    with_home = sum(1 for r in records if r["home_airport"])
    print(f"  With home airport: {with_home}")
    print(f"  Fleet N-numbers matched: {fleet_match}")

    return records


def show_status():
    """Show capture status."""
    print("=" * 60)
    print("Dual-Source ADS-B Capture Status")
    print("=" * 60)

    if not SNAPSHOTS_DIR.exists():
        print("  No snapshots collected yet.")
        return

    dual = sorted(SNAPSHOTS_DIR.glob("dual_*.json"))
    legacy = sorted(SNAPSHOTS_DIR.glob("snapshot_*.json"))
    all_files = sorted(dual + legacy)

    print(f"  Dual-source snapshots: {len(dual)}")
    print(f"  Legacy snapshots: {len(legacy)}")
    print(f"  Total: {len(all_files)}")

    if all_files:
        first = all_files[0].stem.split("_")[1][:8]
        last = all_files[-1].stem.split("_")[1][:8] if len(all_files[-1].stem.split("_")) > 1 else "?"
        print(f"  Date range: {first} to {last}")

    if dual:
        # Show source breakdown from most recent
        with open(dual[-1]) as f:
            latest = json.load(f)
        src = latest.get("sources", {})
        print(f"\n  Latest snapshot ({dual[-1].name}):")
        print(f"    adsb.lol:     {src.get('adsb_lol', '?')}")
        print(f"    OpenSky:      {src.get('opensky', '?')}")
        print(f"    Overlap:      {src.get('overlap', '?')}")
        print(f"    Total merged: {latest.get('total_aircraft', '?')}")
        print(f"    On ground:    {latest.get('on_ground', '?')}")

    total_mb = sum(f.stat().st_size for f in all_files) / (1024 * 1024)
    print(f"\n  Total storage: {total_mb:.1f} MB")


# ── CLI ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Dual-source ADS-B snapshot capture (adsb.lol + OpenSky)"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", action="store_true", help="Take one dual-source snapshot")
    group.add_argument("--start", action="store_true", help="Start continuous capture")
    group.add_argument("--aggregate", action="store_true", help="Aggregate all snapshots")
    group.add_argument("--status", action="store_true", help="Show capture status")
    group.add_argument("--compare", action="store_true", help="Compare source coverage")
    parser.add_argument("--days", type=int, default=30, help="Days to capture (default: 30)")
    parser.add_argument("--interval", type=int, default=15, help="Minutes between snapshots (default: 15)")

    args = parser.parse_args()

    if args.snapshot:
        take_dual_snapshot()
    elif args.start:
        run_continuous(days=args.days, interval_min=args.interval)
    elif args.aggregate:
        aggregate_snapshots()
    elif args.status:
        show_status()
    elif args.compare:
        compare_sources()
