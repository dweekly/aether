#!/usr/bin/env python3
"""
Phase 7: Assemble the final flight school dataset.

Combines all prior phases into a single comprehensive dataset mapping
each flight school to its locations, fleet, contact info, and activity metrics.

Key schema change: schools can operate from MULTIPLE airports. The structured
JSON uses a `locations` array instead of a single `airport_id`. Each aircraft
in `fleet` can reference a location via `location_index`.

If Phase 9 (flight line scraper) has been run, its authoritative tail-number
data is merged in, adding website-confirmed aircraft with optional rental rates
and equipment details.

Output:
  - output/flight_school_fleet_map.csv — flat CSV (one row per aircraft per school per location)
  - output/flight_school_fleet_map.json — structured JSON with nested locations + fleet
  - output/summary_stats.json — aggregate statistics
"""

import json
from pathlib import Path

import pandas as pd

OUTPUT_DIR = Path(__file__).parent.parent / "output"


def _normalize_name(name: str) -> str:
    """Normalize a school/entity name for matching."""
    return name.strip().lower()


def _school_key(name: str, airport: str) -> str:
    """Composite key: normalized name + primary airport code."""
    return f"{_normalize_name(name)}|{airport.strip().upper()}"


def _load_flight_line() -> dict[str, dict]:
    """Load Phase 9 flight-line data, keyed by name|airport composite key.

    Also builds a name-only fallback index for schools where the airport code
    is missing from one side of the join.
    """
    fl_path = OUTPUT_DIR / "flight_line.json"
    if not fl_path.exists():
        return {}
    with open(fl_path) as f:
        records = json.load(f)
    index = {}
    for rec in records:
        name = rec.get("school_name", "").strip()
        if not name:
            continue
        # Primary key: name + first airport
        airports = [
            loc.get("airport_id", "")
            for loc in rec.get("locations", [])
            if loc.get("airport_id")
        ]
        if airports:
            for apt in airports:
                key = _school_key(name, apt)
                index[key] = rec
        # Fallback: name-only key (prefixed to distinguish)
        index[f"name_only|{_normalize_name(name)}"] = rec
    return index


def run():
    """Execute Phase 7: Assemble final dataset."""
    print("=" * 60)
    print("Phase 7: Assemble Final Dataset")
    print("=" * 60)

    # Load all intermediate outputs
    schools_path = OUTPUT_DIR / "enriched_schools.csv"
    if not schools_path.exists():
        schools_path = OUTPUT_DIR / "identified_schools.csv"
    if not schools_path.exists():
        print("  Error: Run prior phases first")
        return None

    schools = pd.read_csv(schools_path, dtype=str, keep_default_na=False)
    print(f"  Schools loaded: {len(schools):,}")

    registry_path = OUTPUT_DIR / "faa_ga_registry.csv"
    registry = None
    if registry_path.exists():
        registry = pd.read_csv(registry_path, dtype=str, keep_default_na=False)
        print(f"  Registry records: {len(registry):,}")

    home_bases_path = OUTPUT_DIR / "tail_home_bases.csv"
    home_bases = None
    if home_bases_path.exists():
        home_bases = pd.read_csv(home_bases_path, dtype=str, keep_default_na=False)
        print(f"  Home base records: {len(home_bases):,}")

    adsb_path = OUTPUT_DIR / "adsb_activity.csv"
    adsb = None
    if adsb_path.exists():
        adsb = pd.read_csv(adsb_path, dtype=str, keep_default_na=False)
        print(f"  ADS-B activity records: {len(adsb):,}")

    # Load flight-line data from Phase 9 (if available)
    flight_line_index = _load_flight_line()
    if flight_line_index:
        print(f"  Flight line records: {len(flight_line_index):,}")

    # Also load the enriched directory data for merging
    dir_path = OUTPUT_DIR / "flight_schools_enriched.json"
    dir_index: dict[str, dict] = {}
    if dir_path.exists():
        with open(dir_path) as f:
            for rec in json.load(f):
                name = rec.get("name", "").strip()
                apt = rec.get("airport_code", "")
                if name and apt:
                    dir_index[_school_key(name, apt)] = rec
                if name:
                    dir_index[f"name_only|{_normalize_name(name)}"] = rec

    # ── Helper: look up registry by N-number ─────────────────────
    def enrich_from_registry(n_num: str) -> dict:
        info = {}
        if registry is not None:
            match = registry[registry["N-NUMBER"] == n_num]
            if not match.empty:
                row = match.iloc[0]
                info["make"] = row.get("MFR", row.get("MANUFACTURER", ""))
                info["model"] = row.get("MODEL", "")
                info["year_mfr"] = row.get("YEAR_MFR", "")
                info["type_aircraft"] = row.get("TYPE_AIRCRAFT", "")
                info["type_engine"] = row.get("TYPE_ENGINE", "")
                info["icao_hex"] = row.get("MODE_S_CODE_HEX", "")
        return info

    # ── Build structured JSON ────────────────────────────────────
    print("\n[1/3] Building structured JSON with multi-airport support...")
    structured = []

    for _, school in schools.iterrows():
        name = school.get("entity_name", "")
        name_key = _normalize_name(name)

        # Start with FAA-identified N-numbers
        n_numbers = str(school.get("n_numbers", "")).split(",")
        n_numbers = [n.strip() for n in n_numbers if n.strip()]

        # Primary location from Phase 5 identification
        primary_airport = school.get("home_airport", "")
        primary_airport_name = school.get("airport_name", "")

        # Check flight-line data for authoritative multi-location + fleet
        # Try composite key (name + airport) first, fall back to name-only
        composite = _school_key(name, primary_airport) if primary_airport else ""
        fl_rec = (
            flight_line_index.get(composite)
            if composite
            else None
        ) or flight_line_index.get(f"name_only|{name_key}")

        # Also check directory data for extra contact info
        dir_rec = (
            dir_index.get(composite)
            if composite
            else None
        ) or dir_index.get(f"name_only|{name_key}")

        # ── Build locations array ────────────────────────────────
        locations = []
        if fl_rec and fl_rec.get("locations"):
            # Use flight-line locations (authoritative, may include multiple airports)
            locations = fl_rec["locations"]
        else:
            # Fallback to single airport from identification phase
            if primary_airport:
                locations = [{
                    "airport_id": primary_airport,
                    "airport_name": primary_airport_name,
                    "label": "",
                }]
            # Try directory data for airport code
            if not locations and dir_rec and dir_rec.get("airport_code"):
                locations = [{
                    "airport_id": dir_rec["airport_code"],
                    "airport_name": dir_rec.get("airport_name", ""),
                    "label": "",
                }]

        # ── Build fleet array ────────────────────────────────────
        fleet = []
        seen_n = set()

        # Priority 1: flight-line data (website-confirmed tail numbers)
        if fl_rec:
            for ac in fl_rec.get("fleet", []):
                nn = ac.get("n_number", "")
                if nn and nn not in seen_n:
                    seen_n.add(nn)
                    entry = {
                        "n_number": nn,
                        "display_name": ac.get("display_name", ""),
                        "description": ac.get("description", ""),
                        "source": "website",
                    }
                    if ac.get("wet_rate_hr") is not None:
                        entry["wet_rate_hr"] = ac["wet_rate_hr"]
                    if ac.get("equipment"):
                        entry["equipment"] = ac["equipment"]
                    if ac.get("location_index") is not None:
                        entry["location_index"] = ac["location_index"]
                    # Enrich with FAA registry
                    reg = enrich_from_registry(nn)
                    entry.update(reg)
                    fleet.append(entry)

        # Priority 2: FAA-identified tail numbers (not already from website)
        for n_num in n_numbers:
            if n_num not in seen_n:
                seen_n.add(n_num)
                entry = {
                    "n_number": n_num,
                    "source": "faa_registry",
                }
                reg = enrich_from_registry(n_num)
                entry.update(reg)
                fleet.append(entry)

        # ── Contact info (merge directory + FAA) ─────────────────
        phone = school.get("phone", "")
        email = school.get("email", "")
        website = school.get("website", "")
        address = school.get("mailing_address", school.get("street", ""))
        ratings = school.get("ratings_offered", "")

        if dir_rec:
            phone = phone or dir_rec.get("phone", "")
            email = email or dir_rec.get("email", "")
            website = website or dir_rec.get("website", "")
            address = address or dir_rec.get("address", "")
            ratings = ratings or dir_rec.get("ratings_offered", "")

        entry = {
            "name": name,
            "locations": locations,
            "city": school.get("city", ""),
            "state": school.get("state", ""),
            "mailing_address": address,
            "phone": phone,
            "email": email,
            "website": website,
            "ratings_offered": ratings.split(",") if ratings else [],
            "school_score": int(school.get("school_score", 0) or 0),
            "fleet": fleet,
            "fleet_size": len(fleet),
            "has_flight_line": bool(fl_rec),
        }

        # Add fleet page URL if we have it
        if fl_rec and fl_rec.get("fleet_page_url"):
            entry["fleet_page_url"] = fl_rec["fleet_page_url"]

        structured.append(entry)

    # ── Add flight-line schools not already matched to a Phase 5 entity ──
    # These are directory-discovered schools (Phase 8/9) whose names don't
    # appear in the FAA registrant-based identified_schools.csv — i.e. the
    # leaseback case where the school doesn't own aircraft under its own name.
    matched_fl_keys = set()
    for _, school in schools.iterrows():
        name = school.get("entity_name", "")
        apt = school.get("home_airport", "")
        if apt:
            matched_fl_keys.add(_school_key(name, apt))
        matched_fl_keys.add(f"name_only|{_normalize_name(name)}")

    fl_only_count = 0
    for fl_key, fl_rec in flight_line_index.items():
        if fl_key in matched_fl_keys or fl_key.startswith("name_only|"):
            continue
        # Check if this record was already added via a name_only fallback
        fl_name = _normalize_name(fl_rec.get("school_name", ""))
        if f"name_only|{fl_name}" in matched_fl_keys:
            continue

        # This school was only found via directory/website scraping
        fleet = []
        for ac in fl_rec.get("fleet", []):
            nn = ac.get("n_number", "")
            if nn:
                entry = {
                    "n_number": nn,
                    "display_name": ac.get("display_name", ""),
                    "description": ac.get("description", ""),
                    "source": "website",
                }
                if ac.get("wet_rate_hr") is not None:
                    entry["wet_rate_hr"] = ac["wet_rate_hr"]
                if ac.get("equipment"):
                    entry["equipment"] = ac["equipment"]
                if ac.get("location_index") is not None:
                    entry["location_index"] = ac["location_index"]
                reg = enrich_from_registry(nn)
                entry.update(reg)
                fleet.append(entry)

        # Pull contact info from directory data
        dir_rec = dir_index.get(fl_key) or dir_index.get(f"name_only|{fl_name}") or {}

        structured.append({
            "name": fl_rec.get("school_name", ""),
            "locations": fl_rec.get("locations", []),
            "city": dir_rec.get("city", ""),
            "state": dir_rec.get("state", ""),
            "mailing_address": dir_rec.get("address", ""),
            "phone": dir_rec.get("phone", ""),
            "email": dir_rec.get("email", ""),
            "website": fl_rec.get("school_website", dir_rec.get("website", "")),
            "ratings_offered": (dir_rec.get("ratings_offered", "") or "").split(",")
                if dir_rec.get("ratings_offered") else [],
            "school_score": 0,
            "fleet": fleet,
            "fleet_size": len(fleet),
            "has_flight_line": True,
            "fleet_page_url": fl_rec.get("fleet_page_url", ""),
            "source": "directory_only",
        })
        fl_only_count += 1
        matched_fl_keys.add(fl_key)
        matched_fl_keys.add(f"name_only|{fl_name}")

    if fl_only_count:
        print(f"  Directory-only schools added (not in FAA registrant list): {fl_only_count}")

    # Sort by score
    structured.sort(key=lambda x: x["school_score"], reverse=True)

    json_path = OUTPUT_DIR / "flight_school_fleet_map.json"
    with open(json_path, "w") as f:
        json.dump(structured, f, indent=2)
    print(f"  Structured JSON: {json_path}")

    # ── Build flat CSV ───────────────────────────────────────────
    print("\n[2/3] Building flat CSV...")
    flat_records = []

    for entry in structured:
        # Flatten locations into a pipe-separated string for CSV
        loc_str = " | ".join(
            loc.get("airport_id", "") for loc in entry["locations"]
        )
        loc_names = " | ".join(
            loc.get("airport_name", "") for loc in entry["locations"]
        )

        for ac in entry["fleet"]:
            record = {
                "school_name": entry["name"],
                "school_score": entry["school_score"],
                "locations": loc_str,
                "location_names": loc_names,
                "state": entry["state"],
                "city": entry["city"],
                "mailing_address": entry["mailing_address"],
                "phone": entry["phone"],
                "email": entry["email"],
                "website": entry["website"],
                "ratings_offered": ",".join(entry["ratings_offered"]),
                "has_flight_line": entry["has_flight_line"],
                "n_number": ac.get("n_number", ""),
                "display_name": ac.get("display_name", ""),
                "make": ac.get("make", ""),
                "model": ac.get("model", ""),
                "year_mfr": ac.get("year_mfr", ""),
                "type_aircraft": ac.get("type_aircraft", ""),
                "type_engine": ac.get("type_engine", ""),
                "icao_hex": ac.get("icao_hex", ""),
                "wet_rate_hr": ac.get("wet_rate_hr", ""),
                "equipment": ",".join(ac.get("equipment", [])),
                "description": ac.get("description", ""),
                "aircraft_source": ac.get("source", ""),
            }
            flat_records.append(record)

        # Schools with no fleet still get a row
        if not entry["fleet"]:
            flat_records.append({
                "school_name": entry["name"],
                "school_score": entry["school_score"],
                "locations": loc_str,
                "location_names": loc_names,
                "state": entry["state"],
                "city": entry["city"],
                "mailing_address": entry["mailing_address"],
                "phone": entry["phone"],
                "email": entry["email"],
                "website": entry["website"],
                "ratings_offered": ",".join(entry["ratings_offered"]),
                "has_flight_line": entry["has_flight_line"],
            })

    flat_df = pd.DataFrame(flat_records)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    flat_path = OUTPUT_DIR / "flight_school_fleet_map.csv"
    flat_df.to_csv(flat_path, index=False)
    print(f"  Flat dataset: {flat_path} ({len(flat_df):,} rows)")

    # ── Summary statistics ───────────────────────────────────────
    print("\n[3/3] Generating summary statistics...")

    schools_with_fleet_line = sum(1 for e in structured if e["has_flight_line"])
    multi_airport = sum(1 for e in structured if len(e["locations"]) > 1)
    total_aircraft = sum(e["fleet_size"] for e in structured)
    website_confirmed = sum(
        1 for e in structured
        for a in e["fleet"]
        if a.get("source") == "website"
    )
    with_rates = sum(
        1 for e in structured
        for a in e["fleet"]
        if a.get("wet_rate_hr")
    )

    all_airports = set()
    for e in structured:
        for loc in e["locations"]:
            aid = loc.get("airport_id", "")
            if aid:
                all_airports.add(aid)

    stats = {
        "total_schools": len(structured),
        "schools_with_flight_line": schools_with_fleet_line,
        "multi_airport_schools": multi_airport,
        "total_aircraft_mapped": total_aircraft,
        "website_confirmed_aircraft": website_confirmed,
        "aircraft_with_rental_rates": with_rates,
        "airports_covered": len(all_airports),
        "states_covered": len(set(e["state"] for e in structured if e["state"])),
    }

    if "state" in flat_df.columns:
        stats["schools_by_state"] = (
            flat_df.drop_duplicates(subset=["school_name", "state"])
            ["state"].value_counts()
            .head(20)
            .to_dict()
        )

    if "make" in flat_df.columns:
        makes = flat_df["make"].replace("", pd.NA).dropna()
        if not makes.empty:
            stats["aircraft_by_make"] = makes.value_counts().head(20).to_dict()

    if "model" in flat_df.columns:
        models = flat_df["model"].replace("", pd.NA).dropna()
        if not models.empty:
            stats["aircraft_by_model"] = models.value_counts().head(30).to_dict()

    stats_path = OUTPUT_DIR / "summary_stats.json"
    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"  Summary stats: {stats_path}")

    print(f"\n  === Summary ===")
    print(f"  Total flight schools:          {stats['total_schools']:,}")
    print(f"  With flight line (website):    {stats['schools_with_flight_line']:,}")
    print(f"  Multi-airport schools:         {stats['multi_airport_schools']:,}")
    print(f"  Total aircraft mapped:         {stats['total_aircraft_mapped']:,}")
    print(f"  Website-confirmed aircraft:    {stats['website_confirmed_aircraft']:,}")
    print(f"  Aircraft with rental rates:    {stats['aircraft_with_rental_rates']:,}")
    print(f"  Airports covered:              {stats['airports_covered']:,}")
    print(f"  States covered:                {stats['states_covered']}")

    print(f"\nPhase 7 complete. Final dataset assembled.")
    return structured


if __name__ == "__main__":
    run()
