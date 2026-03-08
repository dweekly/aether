#!/usr/bin/env python3
"""
14_adsb_validate.py — Cross-validate fleet assignments against ADS-B observations.

Compares the flight school → aircraft → airport mapping in flight_line.json
against aggregated ADS-B activity data to answer:

  1. What fraction of fleet aircraft have been observed in ADS-B?
  2. Of those observed, how many are near their assigned airport?
  3. Which schools/aircraft have suspicious mismatches?

Usage:
    python 14_adsb_validate.py                # summary report
    python 14_adsb_validate.py --detail        # per-school breakdown
    python 14_adsb_validate.py --mismatches    # show likely misassigned aircraft
    python 14_adsb_validate.py --coverage      # airport class coverage check
"""

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR.parent / "output"
FLIGHT_LINE = OUTPUT_DIR / "flight_line.json"
ACTIVITY_SUMMARY = OUTPUT_DIR / "adsb_activity_summary.json"
AIRPORTS_CSV = OUTPUT_DIR / "airports.csv"

R_NM = 3440.065  # Earth radius in nautical miles


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance between two points in nautical miles."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return R_NM * 2 * math.asin(math.sqrt(a))


def load_airports() -> dict[str, tuple[float, float]]:
    """Load airport ID → (lat, lon) mapping.

    Indexes by both raw FAA ID (e.g. SQL) and K-prefixed ICAO (e.g. KSQL)
    so lookups work regardless of prefix convention.
    """
    airports = {}
    with open(AIRPORTS_CSV) as f:
        for row in csv.DictReader(f):
            aid = row.get("faa_id") or row.get("airport_id", "")
            lat = float(row.get("latitude", 0))
            lon = float(row.get("longitude", 0))
            if aid and lat and lon:
                airports[aid] = (lat, lon)
                # Also index with/without K prefix
                if aid.startswith("K") and len(aid) == 4:
                    airports[aid[1:]] = (lat, lon)
                elif len(aid) == 3:
                    airports["K" + aid] = (lat, lon)
    return airports


def load_data():
    """Load flight_line and ADS-B activity."""
    with open(FLIGHT_LINE) as f:
        flight_line = json.load(f)
    with open(ACTIVITY_SUMMARY) as f:
        activity = json.load(f)
    hex_activity = {rec["icao_hex"]: rec for rec in activity}
    return flight_line, hex_activity


def get_aircraft_hex(ac: dict) -> str:
    """Extract Mode S hex from an aircraft record."""
    faa = ac.get("faa") or {}
    return (faa.get("mode_s_hex") or "").strip().upper()


def get_school_airport(school: dict) -> str:
    """Get the airport ID for a school.

    flight_line.json stores it in locations[0].airport_id or per-aircraft
    fleet[].airport_id. The crawl index uses known_airport.
    """
    # Try locations array first
    locs = school.get("locations") or []
    if locs and isinstance(locs, list):
        aid = locs[0].get("airport_id", "")
        if aid:
            return aid
    # Fallback to direct fields
    return school.get("known_airport") or school.get("airport_id") or ""


def validate_fleet(flight_line, hex_activity, airports):
    """Cross-validate fleet against ADS-B and return per-aircraft results."""
    results = []
    for school in flight_line:
        airport = get_school_airport(school)
        airport_coords = airports.get(airport)
        school_name = school.get("school_name") or school.get("name") or ""
        school_url = school.get("school_website") or school.get("website") or ""

        for ac in school.get("fleet", []):
            hex_code = get_aircraft_hex(ac)
            n_number = ac.get("n_number", "")
            if not hex_code:
                continue

            rec = {
                "school_name": school_name,
                "airport": airport,
                "n_number": n_number,
                "hex": hex_code,
                "seen": False,
                "sightings": 0,
                "home_airport": "",
                "dist_nm": None,
                "home_match": False,
            }

            act = hex_activity.get(hex_code)
            if act:
                rec["seen"] = True
                rec["sightings"] = act.get("total_sightings", 0)
                rec["home_nights"] = act.get("home_airport_nights", 0)
                rec["home_airport"] = act.get("home_airport", "")

                # Check home airport match (with/without K prefix)
                # Only count as match if both are non-empty
                home = rec["home_airport"]
                if home and airport:
                    rec["home_match"] = (
                        home == airport
                        or home == "K" + airport
                        or "K" + home == airport
                        or home.lstrip("K") == airport.lstrip("K")
                    )

                # Compute distance from school airport to ADS-B home airport
                home_coords = airports.get(home) or airports.get(home.lstrip("K"))
                if airport_coords and home_coords:
                    rec["dist_nm"] = haversine_nm(
                        airport_coords[0], airport_coords[1],
                        home_coords[0], home_coords[1],
                    )

            results.append(rec)
    return results


def print_summary(results):
    """Print high-level validation summary."""
    total = len(results)
    seen = [r for r in results if r["seen"]]
    with_home = [r for r in seen if r["home_airport"]]
    home_match = [r for r in seen if r["home_match"]]
    with_dist = [r for r in seen if r["dist_nm"] is not None]

    within_10 = [r for r in with_dist if r["dist_nm"] <= 10]
    within_30 = [r for r in with_dist if r["dist_nm"] <= 30]
    far_100 = [r for r in with_dist if r["dist_nm"] > 100]

    print("=" * 60)
    print("ADS-B Fleet Validation Summary")
    print("=" * 60)
    print(f"  Fleet aircraft with Mode S hex: {total}")
    print(f"  Seen in ADS-B data: {len(seen)} ({pct(len(seen), total)})")
    print(f"  With ADS-B home airport assigned: {len(with_home)} ({pct(len(with_home), len(seen))})")
    print()
    print(f"  Of {len(with_home)} with home airport:")
    print(f"    Home matches school airport: {len(home_match)} ({pct(len(home_match), len(with_home))})")
    if with_dist:
        print(f"    Within 10nm of school: {len(within_10)} ({pct(len(within_10), len(with_dist))})")
        print(f"    Within 30nm of school: {len(within_30)} ({pct(len(within_30), len(with_dist))})")
        print(f"    >100nm from school: {len(far_100)} ({pct(len(far_100), len(with_dist))})")
    print()

    # Sighting distribution
    sighting_counts = [r["sightings"] for r in seen]
    if sighting_counts:
        avg = sum(sighting_counts) / len(sighting_counts)
        sighting_counts.sort()
        median = sighting_counts[len(sighting_counts) // 2]
        print(f"  Sightings per aircraft: avg={avg:.1f}, median={median}, max={max(sighting_counts)}")


def print_detail(results):
    """Per-school breakdown."""
    schools = defaultdict(list)
    for r in results:
        schools[r["school_name"]].append(r)

    print(f"\n{'School':<40} {'Hex':>4} {'Seen':>5} {'Match':>6} {'Rate':>6}")
    print("-" * 70)
    for name in sorted(schools):
        recs = schools[name]
        total = len(recs)
        seen = sum(1 for r in recs if r["seen"])
        matched = sum(1 for r in recs if r["home_match"])
        rate = f"{100 * seen / total:.0f}%" if total else "-"
        print(f"  {name[:38]:<38} {total:>4} {seen:>5} {matched:>6} {rate:>6}")


def print_mismatches(results):
    """Show aircraft where ADS-B home doesn't match school airport."""
    mismatches = [
        r for r in results
        if r["seen"] and not r["home_match"] and r["home_airport"]
    ]
    mismatches.sort(key=lambda r: r.get("dist_nm") or 0, reverse=True)

    print(f"\nMismatched aircraft ({len(mismatches)} total):")
    print(f"  {'N-Number':<10} {'School Airport':>8} {'ADS-B Home':>10} {'Dist':>7}  School")
    print("-" * 80)
    for r in mismatches[:50]:
        dist = f"{r['dist_nm']:.0f}nm" if r["dist_nm"] is not None else "?"
        print(f"  {r['n_number']:<10} {r['airport']:>8} {r['home_airport']:>10} {dist:>7}  {r['school_name'][:35]}")


def print_coverage(results, airports):
    """Check coverage of Class B and Class C airports."""
    # Class B airports (major US airports)
    class_b = [
        "ATL", "BOS", "BWI", "CLT", "DCA", "DEN", "DFW", "DTW", "EWR",
        "FLL", "HNL", "IAD", "IAH", "JFK", "LAS", "LAX", "LGA", "MCO",
        "MDW", "MEM", "MIA", "MSP", "ORD", "PHL", "PHX", "PIT", "SAN",
        "SEA", "SFO", "SLC", "STL", "TPA", "AUS", "BNA", "CLE", "CVG",
        "DAL", "HOU", "IND", "MCI", "MKE", "MSY", "OAK", "RDU",
    ]
    # Class C airports (medium US airports)
    class_c = [
        "ABQ", "ALB", "ANC", "BDL", "BHM", "BOI", "BTV", "BUF", "BUR",
        "CAE", "CHS", "CMH", "COS", "CRP", "DAB", "DAY", "DSM", "ELP",
        "FAT", "FNT", "GEG", "GPT", "GRR", "GSO", "GSP", "GUM", "HSV",
        "ICT", "ILM", "JAX", "JAN", "LBB", "LEX", "LIT", "MAF", "MCI",
        "MHT", "MKE", "MLB", "MOB", "MSN", "MYR", "OKC", "OMA", "ONT",
        "PBI", "PDX", "PNS", "PVD", "PWM", "RDU", "RIC", "RNO", "ROC",
        "RSW", "SAT", "SAV", "SBA", "SBN", "SDF", "SHV", "SJC", "SMF",
        "SNA", "SPI", "SRQ", "STT", "SYR", "TLH", "TOL", "TRI", "TUL",
        "TUS", "TYS", "VPS",
    ]

    # Build set of airports that have at least one fleet aircraft seen nearby
    fleet_airports = set()
    for r in results:
        if r["airport"]:
            fleet_airports.add(r["airport"])
            fleet_airports.add(r["airport"].lstrip("K"))
            fleet_airports.add("K" + r["airport"].lstrip("K"))

    seen_airports = set()
    for r in results:
        if r["seen"]:
            seen_airports.add(r["airport"])
            seen_airports.add(r["airport"].lstrip("K"))
            if r["home_airport"]:
                seen_airports.add(r["home_airport"])
                seen_airports.add(r["home_airport"].lstrip("K"))

    def check_class(name, airport_list):
        covered = 0
        missing = []
        for ap in airport_list:
            ap_coords = airports.get(ap) or airports.get("K" + ap)
            if not ap_coords:
                missing.append(ap)
                continue

            # Check if any fleet aircraft is assigned to a school within 30nm
            near = False
            for r in results:
                school_ap = r["airport"]
                school_coords = airports.get(school_ap)
                if not school_coords:
                    continue
                dist = haversine_nm(
                    ap_coords[0], ap_coords[1],
                    school_coords[0], school_coords[1],
                )
                if dist <= 30:
                    near = True
                    break
            if near:
                covered += 1
            else:
                missing.append(ap)
        total = len(airport_list)
        print(f"\n  {name}: {covered}/{total} ({pct(covered, total)})")
        if missing:
            print(f"    Missing: {', '.join(missing[:20])}")

    print("\nAirport Class Coverage")
    print("=" * 60)
    check_class("Class Bravo", class_b)
    check_class("Class Charlie", class_c)


def pct(n, d):
    return f"{100 * n / d:.1f}%" if d else "-"


def main():
    parser = argparse.ArgumentParser(description="ADS-B fleet validation")
    parser.add_argument("--detail", action="store_true", help="Per-school breakdown")
    parser.add_argument("--mismatches", action="store_true", help="Show misassigned aircraft")
    parser.add_argument("--coverage", action="store_true", help="Airport class coverage")
    args = parser.parse_args()

    flight_line, hex_activity = load_data()
    airports = load_airports()
    results = validate_fleet(flight_line, hex_activity, airports)

    print_summary(results)

    if args.detail:
        print_detail(results)
    if args.mismatches:
        print_mismatches(results)
    if args.coverage:
        print_coverage(results, airports)

    if not (args.detail or args.mismatches or args.coverage):
        # Default: show coverage too
        print_coverage(results, airports)


if __name__ == "__main__":
    main()
