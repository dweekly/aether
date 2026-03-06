#!/usr/bin/env python3
"""
13_adsb_cron.py — Cron-friendly ADS-B snapshot from multiple sources.

Takes a snapshot from OpenSky Network AND adsb.lol, deduplicates by ICAO hex,
and saves to data/adsb/snapshots/. Designed to run every 15 minutes via cron.

Sources:
  - OpenSky Network: /states/all with US bounding box (bulk, one request)
  - adsb.lol: /v2/lat/{lat}/lon/{lon}/dist/250 tiled across CONUS (~32 requests)

Usage:
    python 13_adsb_cron.py              # take snapshot from all sources
    python 13_adsb_cron.py --aggregate  # re-aggregate all snapshots

Cron setup (every 15 min for 30 days):
    */15 * * * * cd /Users/dew/dev/aether/research/FlightSchoolMapping/scripts && .venv/bin/python 13_adsb_cron.py >> ../data/adsb/cron.log 2>&1
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "adsb"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# OpenSky
OPENSKY_URL = "https://opensky-network.org/api/states/all"
OPENSKY_USER = os.environ.get("OPENSKY_USER", "")
OPENSKY_PASS = os.environ.get("OPENSKY_PASS", "")
US_BBOX = {"lamin": 24.0, "lamax": 50.0, "lomin": -125.0, "lomax": -66.0}

# adsb.lol — tile CONUS with 250nm radius circles
ADSBLOL_URL = "https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/250"
ADSBLOL_GRID = [
    (lat, lon)
    for lat in [27, 33, 39, 45]
    for lon in [-123, -115, -107, -99, -91, -83, -75, -67]
]


def fetch_opensky() -> dict[str, dict]:
    """Fetch from OpenSky Network. Returns {icao_hex: record}."""
    FIELDS = [
        "icao_hex", "callsign", "origin_country", "time_position",
        "last_contact", "longitude", "latitude", "altitude_m",
        "on_ground", "velocity_ms", "heading", "vertical_rate",
        "sensors", "geo_altitude_m", "squawk", "spi", "category",
    ]
    try:
        auth = (OPENSKY_USER, OPENSKY_PASS) if OPENSKY_USER else None
        resp = requests.get(OPENSKY_URL, params=US_BBOX, auth=auth, timeout=30)
        if resp.status_code != 200:
            print(f"  OpenSky: HTTP {resp.status_code}")
            return {}
        data = resp.json()
        ts = data.get("time", int(time.time()))
        results = {}
        for sv in data.get("states", []):
            rec = {}
            for i, field in enumerate(FIELDS):
                if i < len(sv):
                    rec[field] = sv[i]
            rec["timestamp"] = ts
            hex_code = (rec.get("icao_hex") or "").strip().upper()
            if hex_code.startswith("A"):
                rec["icao_hex"] = hex_code
                rec["callsign"] = (rec.get("callsign") or "").strip()
                rec["source"] = "opensky"
                results[hex_code] = rec
        print(f"  OpenSky: {len(results)} US aircraft")
        return results
    except Exception as e:
        print(f"  OpenSky error: {e}")
        return {}


def fetch_adsblol() -> dict[str, dict]:
    """Fetch from adsb.lol by tiling CONUS. Returns {icao_hex: record}."""
    results = {}
    for lat, lon in ADSBLOL_GRID:
        try:
            url = ADSBLOL_URL.format(lat=lat, lon=lon)
            resp = requests.get(url, timeout=15, headers={
                "User-Agent": "FlightSchoolResearch/1.0"
            })
            if resp.status_code != 200:
                print(f"  adsb.lol ({lat},{lon}): HTTP {resp.status_code}")
                continue
            data = resp.json()
            for ac in data.get("ac", []):
                hex_code = (ac.get("hex") or "").strip().upper()
                if not hex_code.startswith("A"):
                    continue
                lat_val = ac.get("lat")
                lon_val = ac.get("lon")
                if lat_val is None or lon_val is None:
                    continue
                rec = {
                    "icao_hex": hex_code,
                    "callsign": (ac.get("flight") or "").strip(),
                    "latitude": lat_val,
                    "longitude": lon_val,
                    "altitude_m": _feet_to_meters(ac.get("alt_baro")),
                    "on_ground": ac.get("alt_baro") == "ground",
                    "velocity_ms": _knots_to_ms(ac.get("gs")),
                    "heading": ac.get("track"),
                    "registration": (ac.get("r") or "").strip(),
                    "aircraft_type": (ac.get("t") or "").strip(),
                    "timestamp": int(time.time()),
                    "source": "adsblol",
                }
                # Don't overwrite if already seen (OpenSky has richer data)
                if hex_code not in results:
                    results[hex_code] = rec
            time.sleep(0.2)  # gentle rate limit
        except Exception as e:
            print(f"  adsb.lol ({lat},{lon}): {e}")
            continue

    print(f"  adsb.lol: {len(results)} US aircraft (from {len(ADSBLOL_GRID)} tiles)")
    return results


def _feet_to_meters(ft):
    if ft is None or ft == "ground":
        return 0
    try:
        return round(float(ft) * 0.3048, 1)
    except (ValueError, TypeError):
        return 0


def _knots_to_ms(kts):
    if kts is None:
        return None
    try:
        return round(float(kts) * 0.5144, 1)
    except (ValueError, TypeError):
        return None


def take_snapshot():
    """Fetch from all sources, deduplicate, and save."""
    now = datetime.now(timezone.utc)
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S UTC')}] Taking ADS-B snapshot...")

    # Fetch from both sources
    opensky = fetch_opensky()
    adsblol = fetch_adsblol()

    # Merge: OpenSky as primary, adsb.lol fills gaps
    merged = dict(opensky)
    new_from_lol = 0
    for hex_code, rec in adsblol.items():
        if hex_code not in merged:
            merged[hex_code] = rec
            new_from_lol += 1

    records = list(merged.values())
    on_ground = sum(1 for r in records if r.get("on_ground"))

    print(f"  Merged: {len(records)} aircraft ({len(opensky)} OpenSky + {new_from_lol} adsb.lol-only), {on_ground} on ground")

    # Save
    ts_str = now.strftime("%Y%m%d_%H%M%S")
    path = SNAPSHOTS_DIR / f"snapshot_{ts_str}.json"
    with open(path, "w") as f:
        json.dump({
            "capture_utc": now.isoformat(),
            "total_aircraft": len(records),
            "on_ground": on_ground,
            "sources": {
                "opensky": len(opensky),
                "adsblol": len(adsblol),
                "adsblol_unique": new_from_lol,
            },
            "aircraft": records,
        }, f)

    print(f"  Saved: {path.name} ({path.stat().st_size / 1024:.0f} KB)")

    # Update latest_summary
    summary = {
        "fetch_time": int(now.timestamp()),
        "total_aircraft": len(records),
        "airborne": len(records) - on_ground,
        "on_ground": on_ground,
        "us_registered": len(records),
        "sources": {"opensky": len(opensky), "adsblol_unique": new_from_lol},
    }
    with open(DATA_DIR / "latest_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    return path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--aggregate", action="store_true", help="Aggregate all snapshots")
    args = parser.parse_args()

    if args.aggregate:
        # Reuse the existing aggregator
        sys.path.insert(0, str(Path(__file__).parent))
        from importlib import import_module
        mod = import_module("10_adsb_scheduled")
        mod.aggregate_snapshots()
    else:
        take_snapshot()
