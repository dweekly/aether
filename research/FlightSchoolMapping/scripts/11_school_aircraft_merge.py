#!/usr/bin/env python3
"""
Phase 11: Merge schools → aircraft — link AOPA/FAA-141 schools to tail numbers.

Three matching strategies, in order of confidence:

  1. **Registrant name match** (high confidence)
     AOPA school name ↔ FAA registrant NAME, fuzzy-matched within same
     city/state. A match means the school *owns* the aircraft.

  2. **Airport co-location** (medium confidence)
     AOPA airport ID ↔ FAA school-candidate aircraft registered in the same
     city/state as the airport. Weaker than (1) but catches aircraft owned
     by LLCs or trusts whose name doesn't match the DBA.

  3. **ADS-B home base** (observational)
     Aircraft observed on the ground at an AOPA-school airport via ADS-B.
     Doesn't prove ownership, but shows the aircraft *operates* there.

Output: output/school_aircraft_map.csv  — one row per (school, aircraft) pair
        output/school_aircraft_map.json — same, grouped by school
"""

import csv
import json
import math
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd
from tqdm import tqdm

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output"

# ── Helpers ──────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    """Normalize a business name for fuzzy matching."""
    name = name.upper().strip()
    # Remove legal suffixes
    for suffix in [
        " LLC", " INC", " CORP", " CORPORATION", " CO", " COMPANY",
        " LTD", " LP", " LLP", " TRUSTEE", " TRUST", " DBA",
    ]:
        name = name.replace(suffix, "")
    # Remove punctuation
    name = re.sub(r"[^A-Z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def name_similarity(a: str, b: str) -> float:
    """
    Token-based Jaccard similarity between two normalized names.
    Returns 0.0–1.0.
    """
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    R_NM = 3440.065
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R_NM * 2 * math.asin(math.sqrt(a))


# ── Data loading ─────────────────────────────────────────────────

def load_aopa() -> list[dict]:
    path = OUTPUT_DIR / "aopa_flight_schools.json"
    with open(path) as f:
        return json.load(f)


def load_faa141() -> pd.DataFrame:
    path = OUTPUT_DIR / "faa141_schools.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def load_faa_candidates() -> pd.DataFrame:
    path = OUTPUT_DIR / "faa_school_candidates.csv"
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def load_airports() -> pd.DataFrame:
    path = OUTPUT_DIR / "airports.csv"
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def load_adsb_census() -> pd.DataFrame:
    path = OUTPUT_DIR / "aircraft_census.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype=str, keep_default_na=False)


# ── Strategy 1: Registrant name match ───────────────────────────

def match_by_name(aopa_schools: list[dict], faa_candidates: pd.DataFrame) -> list[dict]:
    """
    Fuzzy-match AOPA school names to FAA registrant names.
    Require same state + similarity >= 0.5 (token Jaccard).
    """
    matches = []

    # Build lookup: (state) -> [(normalized_name, row), ...]
    faa_by_state = defaultdict(list)
    for _, row in faa_candidates.iterrows():
        state = row["STATE"].strip().upper()
        norm = normalize_name(row["NAME"])
        faa_by_state[state].append((norm, row))

    for school in tqdm(aopa_schools, desc="  Name matching"):
        school_name_norm = normalize_name(school["name"])
        state = school["state"].strip().upper()
        if not state:
            continue

        best_sim = 0.0
        best_rows = []

        for faa_norm, faa_row in faa_by_state.get(state, []):
            sim = name_similarity(school_name_norm, faa_norm)
            if sim > best_sim:
                best_sim = sim
                best_rows = [faa_row]
            elif sim == best_sim and sim >= 0.5:
                best_rows.append(faa_row)

        if best_sim >= 0.5:
            for faa_row in best_rows:
                matches.append({
                    "school_name": school["name"],
                    "school_city": school["city"],
                    "school_state": school["state"],
                    "school_airport": school["airport_id"],
                    "n_number": "N" + faa_row["N-NUMBER"],
                    "registrant": faa_row["NAME"],
                    "mfr": faa_row["MFR"],
                    "model": faa_row["MODEL"],
                    "year_mfr": faa_row["YEAR_MFR"],
                    "seats": faa_row["NO-SEATS"],
                    "icao_hex": faa_row["MODE_S_CODE_HEX"],
                    "match_method": "name",
                    "match_score": round(best_sim, 3),
                })

    return matches


# ── Strategy 2: Airport co-location ─────────────────────────────

def match_by_airport(
    aopa_schools: list[dict],
    faa_candidates: pd.DataFrame,
    airports: pd.DataFrame,
) -> list[dict]:
    """
    Match FAA school-candidate aircraft to AOPA schools at the same airport.
    Uses city+state of the FAA registrant vs. airport city+state.
    """
    matches = []

    # Build airport lookup: faa_id -> (city_upper, state)
    apt_lookup = {}
    for _, apt in airports.iterrows():
        faa_id = apt.get("faa_id", "")
        if faa_id:
            apt_lookup[faa_id] = (
                apt["city"].upper().strip(),
                apt["state"].strip().upper(),
            )

    # Build AOPA school lookup: airport_id -> [school, ...]
    schools_by_airport = defaultdict(list)
    for school in aopa_schools:
        aid = school.get("airport_id", "")
        if aid:
            schools_by_airport[aid].append(school)

    # For each FAA candidate, find its likely airport via city+state
    faa_city_state = defaultdict(list)
    for _, row in faa_candidates.iterrows():
        key = (row["CITY"].upper().strip(), row["STATE"].strip().upper())
        faa_city_state[key].append(row)

    # Match: airport city+state → FAA candidates in that city+state
    matched_pairs = set()  # (school_name, n_number) to dedup
    for apt_id, apt_loc in apt_lookup.items():
        schools = schools_by_airport.get(apt_id, [])
        if not schools:
            continue

        candidates = faa_city_state.get(apt_loc, [])
        if not candidates:
            continue

        for school in schools:
            for faa_row in candidates:
                n_num = "N" + faa_row["N-NUMBER"]
                pair = (school["name"], n_num)
                if pair in matched_pairs:
                    continue
                matched_pairs.add(pair)

                matches.append({
                    "school_name": school["name"],
                    "school_city": school["city"],
                    "school_state": school["state"],
                    "school_airport": school["airport_id"],
                    "n_number": n_num,
                    "registrant": faa_row["NAME"],
                    "mfr": faa_row["MFR"],
                    "model": faa_row["MODEL"],
                    "year_mfr": faa_row["YEAR_MFR"],
                    "seats": faa_row["NO-SEATS"],
                    "icao_hex": faa_row["MODE_S_CODE_HEX"],
                    "match_method": "airport_colocation",
                    "match_score": 0.4,
                })

    return matches


# ── Strategy 3: ADS-B home base ─────────────────────────────────

def match_by_adsb(
    aopa_schools: list[dict],
    airports: pd.DataFrame,
    census: pd.DataFrame,
    faa_candidates: pd.DataFrame,
) -> list[dict]:
    """
    Match aircraft seen on the ground at school airports via ADS-B.
    """
    if census.empty:
        return []

    matches = []

    # Build airport coordinate lookup
    apt_coords = {}
    for _, apt in airports.iterrows():
        faa_id = apt.get("faa_id", "")
        try:
            lat = float(apt["latitude"])
            lon = float(apt["longitude"])
            apt_coords[faa_id] = (lat, lon)
        except (ValueError, TypeError):
            pass

    # Schools by airport
    schools_by_airport = defaultdict(list)
    for school in aopa_schools:
        aid = school.get("airport_id", "")
        if aid:
            schools_by_airport[aid].append(school)

    # FAA candidate lookup by icao_hex for enrichment
    faa_by_hex = {}
    for _, row in faa_candidates.iterrows():
        hex_code = row["MODE_S_CODE_HEX"].strip()
        if hex_code:
            faa_by_hex[hex_code] = row

    # For each aircraft in census that was on ground
    matched_pairs = set()
    for _, ac in tqdm(census.iterrows(), total=len(census), desc="  ADS-B matching"):
        try:
            on_ground_pct = float(ac.get("on_ground_pct", 0))
        except (ValueError, TypeError):
            continue
        if on_ground_pct < 0.1:  # must be seen on ground at least sometimes
            continue

        try:
            lat = float(ac["latest_lat"])
            lon = float(ac["latest_lon"])
        except (ValueError, TypeError):
            continue

        icao_hex = ac["icao_hex"].strip()

        # Find nearest airport
        nearest_apt = None
        nearest_dist = 5.0  # max 5nm
        for apt_id, (alat, alon) in apt_coords.items():
            dist = haversine_nm(lat, lon, alat, alon)
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_apt = apt_id

        if not nearest_apt:
            continue

        schools = schools_by_airport.get(nearest_apt, [])
        if not schools:
            continue

        # Enrich from FAA registry
        faa_row = faa_by_hex.get(icao_hex)
        n_number = ac.get("n_number", "") or (("N" + faa_row["N-NUMBER"]) if faa_row is not None else "")

        for school in schools:
            pair = (school["name"], n_number or icao_hex)
            if pair in matched_pairs:
                continue
            matched_pairs.add(pair)

            matches.append({
                "school_name": school["name"],
                "school_city": school["city"],
                "school_state": school["state"],
                "school_airport": school["airport_id"],
                "n_number": n_number,
                "registrant": faa_row["NAME"] if faa_row is not None else "",
                "mfr": faa_row["MFR"] if faa_row is not None else "",
                "model": faa_row["MODEL"] if faa_row is not None else "",
                "year_mfr": faa_row["YEAR_MFR"] if faa_row is not None else "",
                "seats": faa_row["NO-SEATS"] if faa_row is not None else "",
                "icao_hex": icao_hex,
                "match_method": "adsb_homebase",
                "match_score": round(0.3 * on_ground_pct, 3),
                "adsb_on_ground_pct": on_ground_pct,
                "adsb_distance_nm": round(nearest_dist, 2),
            })

    return matches


# ── Merge & deduplicate ──────────────────────────────────────────

def merge_matches(all_matches: list[dict]) -> list[dict]:
    """
    Deduplicate across strategies. When the same (school, n_number) pair
    appears from multiple strategies, keep the highest-confidence one.
    """
    best = {}  # (school_name, n_number) -> match dict
    for m in all_matches:
        key = (m["school_name"], m["n_number"])
        if key not in best or m["match_score"] > best[key]["match_score"]:
            best[key] = m

    return sorted(best.values(), key=lambda m: (-m["match_score"], m["school_name"]))


def group_by_school(matches: list[dict]) -> list[dict]:
    """Group flat matches into per-school records with fleet arrays."""
    schools = {}
    for m in matches:
        name = m["school_name"]
        if name not in schools:
            schools[name] = {
                "school_name": name,
                "school_city": m["school_city"],
                "school_state": m["school_state"],
                "school_airport": m["school_airport"],
                "fleet": [],
            }
        schools[name]["fleet"].append({
            k: v for k, v in m.items()
            if k not in ("school_name", "school_city", "school_state", "school_airport")
        })

    # Sort by fleet size descending
    result = sorted(schools.values(), key=lambda s: -len(s["fleet"]))
    return result


# ── Main ─────────────────────────────────────────────────────────

def run():
    print("=" * 60)
    print("Phase 11: School → Aircraft Merge")
    print("=" * 60)

    # Load data
    print("\n[1/5] Loading data...")
    aopa = load_aopa()
    print(f"  AOPA schools: {len(aopa):,}")

    faa_candidates = load_faa_candidates()
    print(f"  FAA school-candidate aircraft: {len(faa_candidates):,}")

    airports = load_airports()
    print(f"  Airports: {len(airports):,}")

    census = load_adsb_census()
    print(f"  ADS-B census: {len(census):,} aircraft")

    all_matches = []

    # Strategy 1: Name match
    print("\n[2/5] Strategy 1: Registrant name matching...")
    name_matches = match_by_name(aopa, faa_candidates)
    print(f"  Name matches: {len(name_matches):,}")
    all_matches.extend(name_matches)

    # Strategy 2: Airport co-location
    print("\n[3/5] Strategy 2: Airport co-location...")
    airport_matches = match_by_airport(aopa, faa_candidates, airports)
    print(f"  Airport co-location matches: {len(airport_matches):,}")
    all_matches.extend(airport_matches)

    # Strategy 3: ADS-B home base
    print("\n[4/5] Strategy 3: ADS-B home base...")
    adsb_matches = match_by_adsb(aopa, airports, census, faa_candidates)
    print(f"  ADS-B matches: {len(adsb_matches):,}")
    all_matches.extend(adsb_matches)

    # Merge and deduplicate
    print("\n[5/5] Merging and deduplicating...")
    merged = merge_matches(all_matches)
    print(f"  Total unique (school, aircraft) pairs: {len(merged):,}")

    # Save flat CSV
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    csv_columns = [
        "school_name", "school_city", "school_state", "school_airport",
        "n_number", "registrant", "mfr", "model", "year_mfr", "seats",
        "icao_hex", "match_method", "match_score",
    ]
    csv_path = OUTPUT_DIR / "school_aircraft_map.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged)
    print(f"  CSV: {csv_path}")

    # Save grouped JSON
    grouped = group_by_school(merged)
    json_path = OUTPUT_DIR / "school_aircraft_map.json"
    with open(json_path, "w") as f:
        json.dump(grouped, f, indent=2)
    print(f"  JSON: {json_path}")

    # Stats
    schools_with_aircraft = len(grouped)
    total_aircraft = len(merged)
    unique_tails = len(set(m["n_number"] for m in merged if m["n_number"]))

    print(f"\n  === Summary ===")
    print(f"  Schools with aircraft:  {schools_with_aircraft:,}")
    print(f"  Total (school,aircraft) pairs: {total_aircraft:,}")
    print(f"  Unique tail numbers:    {unique_tails:,}")

    # Method breakdown
    from collections import Counter
    methods = Counter(m["match_method"] for m in merged)
    print(f"\n  Match method breakdown:")
    for method, count in methods.most_common():
        print(f"    {method}: {count:,}")

    # Top schools by fleet size
    print(f"\n  Top 15 schools by fleet size:")
    for s in grouped[:15]:
        methods_used = set(a["match_method"] for a in s["fleet"])
        print(f"    {s['school_name']:<45s} {len(s['fleet']):>3d} aircraft  [{', '.join(methods_used)}]")

    # Top makes
    makes = Counter(m["mfr"] for m in merged if m["mfr"])
    print(f"\n  Top aircraft manufacturers:")
    for mfr, count in makes.most_common(10):
        print(f"    {mfr}: {count:,}")

    print(f"\nPhase 11 complete.")
    return merged, grouped


if __name__ == "__main__":
    run()
