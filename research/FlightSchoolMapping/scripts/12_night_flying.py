#!/usr/bin/env python3
"""
Phase 12: ADS-B night flying analysis — who's flying at night?

Extends the scheduled ADS-B capture pipeline with night-time snapshots and
solar position calculations to answer: which GA aircraft (and which flight
schools) are operating after dark?

Three modes:
  --capture   Take ADS-B snapshots during evening/night hours (7 PM – 1 AM local)
  --analyze   Process all existing snapshots and classify day vs. night
  --report    Generate a summary report of night flying activity

The night determination uses astronomical solar position (no external API needed)
computed from each aircraft's lat/lon and the snapshot UTC timestamp.

Environment:
    OPENSKY_USER / OPENSKY_PASS — OpenSky Network credentials (optional)
    TZ — timezone for capture scheduling (default: America/Los_Angeles)
"""

import json
import math
import os
import sched
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# Reuse infrastructure from the existing pipeline
SCRIPTS_DIR = Path(__file__).parent
BASE_DIR = SCRIPTS_DIR.parent
DATA_DIR = BASE_DIR / "data" / "adsb"
OUTPUT_DIR = BASE_DIR / "output"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"

# Night capture schedule: 7 PM, 9 PM, 11 PM, 1 AM local
NIGHT_CAPTURE_HOURS = [19, 21, 23, 1]
CAPTURE_DAYS = 14

# Import the snapshot machinery from script 10
from importlib.util import spec_from_file_location, module_from_spec

_spec = spec_from_file_location("adsb_scheduled", SCRIPTS_DIR / "10_adsb_scheduled.py")
_mod = module_from_spec(_spec)
_spec.loader.exec_module(_mod)
fetch_opensky_snapshot = _mod.fetch_opensky_snapshot
save_snapshot = _mod.save_snapshot
AirportIndex = _mod.AirportIndex

# ---------------------------------------------------------------------------
# Solar position calculation (simplified, no external deps)
# ---------------------------------------------------------------------------

def _julian_day(dt: datetime) -> float:
    """Convert a UTC datetime to Julian Day Number."""
    a = (14 - dt.month) // 12
    y = dt.year + 4800 - a
    m = dt.month + 12 * a - 3
    jdn = dt.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    return jdn + (dt.hour - 12) / 24.0 + dt.minute / 1440.0 + dt.second / 86400.0


def solar_elevation(lat: float, lon: float, dt_utc: datetime) -> float:
    """
    Compute the sun's elevation angle (degrees) at a given lat/lon and UTC time.

    Positive = sun above horizon, negative = sun below horizon.
    Uses the standard astronomical algorithm (accurate to ~1 degree).
    """
    jd = _julian_day(dt_utc)
    n = jd - 2451545.0  # days since J2000.0

    # Mean solar longitude and anomaly
    L = (280.460 + 0.9856474 * n) % 360
    g = math.radians((357.528 + 0.9856003 * n) % 360)

    # Ecliptic longitude of the sun
    lam = math.radians(L + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))

    # Obliquity of the ecliptic
    eps = math.radians(23.439 - 0.0000004 * n)

    # Right ascension and declination
    sin_dec = math.sin(eps) * math.sin(lam)
    dec = math.asin(sin_dec)

    # Greenwich Mean Sidereal Time (hours)
    gmst_h = (18.697374558 + 24.06570982441908 * n) % 24
    # Local hour angle
    lha = math.radians((gmst_h * 15.0 + lon) % 360)

    # Elevation angle
    lat_r = math.radians(lat)
    sin_elev = (
        math.sin(lat_r) * math.sin(dec)
        + math.cos(lat_r) * math.cos(dec) * math.cos(lha)
    )
    return math.degrees(math.asin(max(-1, min(1, sin_elev))))


def is_night(lat: float, lon: float, dt_utc: datetime, threshold_deg: float = -6.0) -> bool:
    """
    Determine if it's 'night' at a given position and time.

    Uses civil twilight threshold (-6°) by default, matching the FAR § 1.1
    definition of "night" (end of evening civil twilight to beginning of
    morning civil twilight).
    """
    return solar_elevation(lat, lon, dt_utc) < threshold_deg


# ---------------------------------------------------------------------------
# Night capture
# ---------------------------------------------------------------------------

def take_night_snapshot() -> Path | None:
    """Take an ADS-B snapshot labeled as a night capture."""
    now = datetime.now(timezone.utc)
    print(f"  [{now.strftime('%H:%M:%S UTC')}] Night capture...")
    records = fetch_opensky_snapshot()
    if records:
        path = save_snapshot(records, label="night")
        airborne = sum(1 for r in records if not r.get("on_ground"))
        print(f"  Captured {len(records)} aircraft ({airborne} airborne) -> {path.name}")
        return path
    else:
        print("  Warning: No data returned from OpenSky")
        return None


def run_night_capture():
    """Run scheduled night captures for CAPTURE_DAYS days."""
    print("=" * 60)
    print("ADS-B Night Capture — Starting")
    print("=" * 60)
    print(f"  Capture hours (local): {NIGHT_CAPTURE_HOURS}")
    print(f"  Duration: {CAPTURE_DAYS} days")
    print()

    scheduler = sched.scheduler(time.time, time.sleep)
    now = datetime.now()
    end_date = now + timedelta(days=CAPTURE_DAYS)

    scheduled = 0
    for day_offset in range(CAPTURE_DAYS + 1):
        day = now.date() + timedelta(days=day_offset)
        for hour in NIGHT_CAPTURE_HOURS:
            capture_time = datetime.combine(day, datetime.min.time().replace(hour=hour))
            # Hours < 6 are "next day" captures (e.g. 1 AM)
            if hour < 6 and day_offset > 0:
                pass  # keep as-is, it's the early morning of this day
            if capture_time <= now or capture_time.date() > end_date.date():
                continue
            delay = (capture_time - now).total_seconds()
            scheduler.enter(delay, 1, take_night_snapshot)
            scheduled += 1

    print(f"  Scheduled {scheduled} night captures through {end_date.date()}")
    try:
        scheduler.run()
    except KeyboardInterrupt:
        print("\n  Capture interrupted.")
    print("  Done. Run --analyze to process results.")


# ---------------------------------------------------------------------------
# Analysis: classify all snapshots as day/night per aircraft position
# ---------------------------------------------------------------------------

def analyze_snapshots():
    """
    Process all ADS-B snapshots and classify each aircraft sighting as day or night.

    Produces:
      - output/night_flying_activity.json  — per-aircraft night/day breakdown
      - output/night_flying_by_airport.json — per-airport night activity summary
      - output/night_flying_by_school.json  — per-school night activity (if school data exists)
    """
    print("=" * 60)
    print("Night Flying Analysis")
    print("=" * 60)

    snapshot_files = sorted(SNAPSHOTS_DIR.glob("snapshot_*.json"))
    print(f"  Snapshot files: {len(snapshot_files)}")
    if not snapshot_files:
        print("  No snapshots found. Run --capture or use existing Phase 10 snapshots.")
        return

    # Load airport index
    airports_path = OUTPUT_DIR / "airports.csv"
    apt_index = None
    if airports_path.exists():
        airports = pd.read_csv(airports_path, keep_default_na=False)
        airport_coords = airports[
            airports["latitude"].notna() & airports["longitude"].notna()
        ][["airport_id", "faa_id", "latitude", "longitude"]].copy()
        airport_coords["latitude"] = airport_coords["latitude"].astype(float)
        airport_coords["longitude"] = airport_coords["longitude"].astype(float)
        apt_index = AirportIndex(airport_coords)
        print(f"  Airport KD-tree: {len(airport_coords)} airports")

    # Per-aircraft counters
    aircraft_day = Counter()
    aircraft_night = Counter()
    aircraft_night_airports = defaultdict(Counter)  # hex -> airport -> count
    aircraft_callsigns = {}  # hex -> most recent callsign

    # Per-snapshot stats
    snapshot_stats = []

    for snap_path in snapshot_files:
        with open(snap_path) as f:
            snap = json.load(f)

        capture_utc_str = snap.get("capture_utc", "")
        if capture_utc_str:
            snap_dt = datetime.fromisoformat(capture_utc_str)
        else:
            # Fall back to timestamp from first aircraft record
            ts = snap.get("aircraft", [{}])[0].get("timestamp", 0)
            snap_dt = datetime.fromtimestamp(ts, tz=timezone.utc)

        snap_night = 0
        snap_day = 0
        snap_airborne_night = 0

        for ac in snap.get("aircraft", []):
            hex_code = ac.get("icao_hex", "")
            lat = ac.get("latitude")
            lon = ac.get("longitude")
            on_ground = ac.get("on_ground", False)
            callsign = ac.get("callsign", "")

            if not hex_code or lat is None or lon is None:
                continue

            if callsign:
                aircraft_callsigns[hex_code] = callsign

            night = is_night(float(lat), float(lon), snap_dt)

            if night:
                aircraft_night[hex_code] += 1
                snap_night += 1
                if not on_ground:
                    snap_airborne_night += 1
                    # Find nearest airport for airborne night flights
                    if apt_index:
                        nearest = apt_index.nearest(float(lat), float(lon))
                        if nearest:
                            aircraft_night_airports[hex_code][nearest] += 1
            else:
                aircraft_day[hex_code] += 1
                snap_day += 1

        snapshot_stats.append({
            "file": snap_path.name,
            "capture_utc": capture_utc_str,
            "total_aircraft": len(snap.get("aircraft", [])),
            "night_aircraft": snap_night,
            "day_aircraft": snap_day,
            "airborne_at_night": snap_airborne_night,
        })

    # ------ Build per-aircraft summary ------
    all_hexes = set(aircraft_day) | set(aircraft_night)
    print(f"\n  Unique aircraft across all snapshots: {len(all_hexes)}")
    print(f"  Aircraft seen at night (any snapshot): {len(aircraft_night)}")

    aircraft_records = []
    for hex_code in sorted(all_hexes):
        day_ct = aircraft_day.get(hex_code, 0)
        night_ct = aircraft_night.get(hex_code, 0)
        total = day_ct + night_ct
        night_airports = dict(aircraft_night_airports.get(hex_code, {}))
        top_night_airport = max(night_airports, key=night_airports.get) if night_airports else ""

        aircraft_records.append({
            "icao_hex": hex_code,
            "callsign": aircraft_callsigns.get(hex_code, ""),
            "total_sightings": total,
            "day_sightings": day_ct,
            "night_sightings": night_ct,
            "night_pct": round(night_ct / total, 3) if total else 0,
            "night_airports": night_airports,
            "top_night_airport": top_night_airport,
        })

    # Sort by night activity
    aircraft_records.sort(key=lambda r: r["night_sightings"], reverse=True)

    # ------ Per-airport night summary ------
    airport_night_counts = Counter()
    for hex_code, apt_counts in aircraft_night_airports.items():
        for apt, ct in apt_counts.items():
            airport_night_counts[apt] += ct

    airport_records = [
        {"airport": apt, "night_sightings": ct}
        for apt, ct in airport_night_counts.most_common()
    ]

    # ------ Cross-reference with flight schools ------
    school_night = []
    school_map_path = OUTPUT_DIR / "school_aircraft_map.json"
    if school_map_path.exists():
        print("  Cross-referencing with flight school fleet data...")
        with open(school_map_path) as f:
            school_map = json.load(f)

        # Build hex -> night sightings lookup
        night_by_hex = {r["icao_hex"]: r for r in aircraft_records if r["night_sightings"] > 0}

        # Build N-number -> hex mapping from snapshot callsigns
        n_to_hex = {}
        for hex_code, callsign in aircraft_callsigns.items():
            # Callsigns for GA are typically N-numbers (e.g. "N12345")
            cs = callsign.strip().upper()
            if cs.startswith("N") and len(cs) >= 4:
                n_to_hex[cs] = hex_code

        for school in school_map:
            school_name = school.get("name", "")
            airport = school.get("airport_id", "")
            fleet = school.get("aircraft", [])
            night_aircraft = []

            for ac in fleet:
                n_num = ac.get("n_number", "").strip().upper()
                if not n_num.startswith("N"):
                    n_num = "N" + n_num
                hex_code = n_to_hex.get(n_num, "")
                if hex_code and hex_code in night_by_hex:
                    info = night_by_hex[hex_code]
                    night_aircraft.append({
                        "n_number": n_num,
                        "icao_hex": hex_code,
                        "make_model": ac.get("make", "") + " " + ac.get("model", ""),
                        "night_sightings": info["night_sightings"],
                        "night_pct": info["night_pct"],
                    })

            if night_aircraft:
                school_night.append({
                    "school": school_name,
                    "airport": airport,
                    "night_aircraft_count": len(night_aircraft),
                    "total_fleet": len(fleet),
                    "total_night_sightings": sum(a["night_sightings"] for a in night_aircraft),
                    "aircraft": night_aircraft,
                })

        school_night.sort(key=lambda s: s["total_night_sightings"], reverse=True)
        print(f"  Schools with night flying activity: {len(school_night)}")

    # ------ Save results ------
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Aircraft-level
    ac_path = OUTPUT_DIR / "night_flying_activity.json"
    with open(ac_path, "w") as f:
        json.dump(aircraft_records, f, indent=2)

    # Also save a CSV (without the nested airport dict)
    csv_records = [{k: v for k, v in r.items() if k != "night_airports"} for r in aircraft_records]
    csv_path = OUTPUT_DIR / "night_flying_activity.csv"
    pd.DataFrame(csv_records).to_csv(csv_path, index=False)

    # Airport-level
    apt_path = OUTPUT_DIR / "night_flying_by_airport.json"
    with open(apt_path, "w") as f:
        json.dump(airport_records, f, indent=2)

    # School-level
    if school_night:
        school_path = OUTPUT_DIR / "night_flying_by_school.json"
        with open(school_path, "w") as f:
            json.dump(school_night, f, indent=2)

    # Snapshot stats
    snap_path = OUTPUT_DIR / "night_flying_snapshot_stats.json"
    with open(snap_path, "w") as f:
        json.dump(snapshot_stats, f, indent=2)

    print(f"\n  Results saved:")
    print(f"    {ac_path.name} — {len(aircraft_records)} aircraft")
    print(f"    {csv_path.name}")
    print(f"    {apt_path.name} — {len(airport_records)} airports")
    if school_night:
        print(f"    {school_path.name} — {len(school_night)} schools")
    print(f"    {snap_path.name} — {len(snapshot_stats)} snapshots analyzed")

    return aircraft_records


# ---------------------------------------------------------------------------
# Report: human-readable summary
# ---------------------------------------------------------------------------

def generate_report():
    """Print a human-readable night flying report from analyzed data."""
    print("=" * 60)
    print("Night Flying Report")
    print("=" * 60)

    ac_path = OUTPUT_DIR / "night_flying_activity.json"
    if not ac_path.exists():
        print("  No analysis data found. Run --analyze first.")
        return

    with open(ac_path) as f:
        aircraft = json.load(f)

    night_fliers = [a for a in aircraft if a["night_sightings"] > 0]
    print(f"\n  Total aircraft tracked: {len(aircraft):,}")
    print(f"  Aircraft seen at night: {len(night_fliers):,} ({len(night_fliers)/len(aircraft)*100:.1f}%)")

    if night_fliers:
        total_night = sum(a["night_sightings"] for a in night_fliers)
        total_all = sum(a["total_sightings"] for a in aircraft)
        print(f"  Night sightings: {total_night:,} of {total_all:,} total ({total_night/total_all*100:.1f}%)")

        print(f"\n  Top 20 night fliers:")
        print(f"  {'ICAO Hex':<10} {'Callsign':<10} {'Night':>6} {'Total':>6} {'Night%':>7} {'Top Airport'}")
        print(f"  {'─'*10} {'─'*10} {'─'*6} {'─'*6} {'─'*7} {'─'*12}")
        for a in night_fliers[:20]:
            print(
                f"  {a['icao_hex']:<10} {a['callsign']:<10} "
                f"{a['night_sightings']:>6} {a['total_sightings']:>6} "
                f"{a['night_pct']*100:>6.1f}% {a['top_night_airport']}"
            )

    # Airport report
    apt_path = OUTPUT_DIR / "night_flying_by_airport.json"
    if apt_path.exists():
        with open(apt_path) as f:
            airports = json.load(f)
        if airports:
            print(f"\n  Top 20 airports by night GA activity:")
            print(f"  {'Airport':<8} {'Night Sightings':>16}")
            print(f"  {'─'*8} {'─'*16}")
            for a in airports[:20]:
                print(f"  {a['airport']:<8} {a['night_sightings']:>16}")

    # School report
    school_path = OUTPUT_DIR / "night_flying_by_school.json"
    if school_path.exists():
        with open(school_path) as f:
            schools = json.load(f)
        if schools:
            print(f"\n  Top 20 flight schools with night operations:")
            print(f"  {'School':<40} {'Airport':<8} {'Night AC':>9} {'Fleet':>6} {'Night Obs':>10}")
            print(f"  {'─'*40} {'─'*8} {'─'*9} {'─'*6} {'─'*10}")
            for s in schools[:20]:
                name = s["school"][:39]
                print(
                    f"  {name:<40} {s['airport']:<8} "
                    f"{s['night_aircraft_count']:>9} {s['total_fleet']:>6} "
                    f"{s['total_night_sightings']:>10}"
                )

    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run():
    """Execute Phase 12 in pipeline mode: analyze existing snapshots."""
    return analyze_snapshots()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="ADS-B night flying analysis — who's flying at night?"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--capture", action="store_true", help="Start night ADS-B capture (evenings)")
    group.add_argument("--analyze", action="store_true", help="Analyze all snapshots for night activity")
    group.add_argument("--report", action="store_true", help="Print night flying summary report")
    args = parser.parse_args()

    if args.capture:
        run_night_capture()
    elif args.analyze:
        analyze_snapshots()
    elif args.report:
        generate_report()
