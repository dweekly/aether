#!/usr/bin/env python3
"""
Phase 7: Assemble the final flight school dataset.

Combines all prior phases into a single comprehensive dataset mapping
each flight school to its fleet, location, contact info, and activity metrics.

Output:
  - output/flight_school_fleet_map.csv — flat CSV with one row per aircraft per school
  - output/flight_school_fleet_map.json — structured JSON with nested fleet per school
  - output/summary_stats.json — aggregate statistics
"""

import json
from pathlib import Path

import pandas as pd

OUTPUT_DIR = Path(__file__).parent.parent / "output"


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

    # Build flat dataset: one row per aircraft per school
    print("\n[1/3] Building flat dataset...")
    flat_records = []

    for _, school in schools.iterrows():
        n_numbers = str(school.get("n_numbers", "")).split(",")
        n_numbers = [n.strip() for n in n_numbers if n.strip()]

        for n_num in n_numbers:
            record = {
                "school_name": school.get("entity_name", ""),
                "school_score": school.get("school_score", ""),
                "home_airport": school.get("home_airport", ""),
                "airport_name": school.get("airport_name", ""),
                "state": school.get("state", ""),
                "city": school.get("city", ""),
                "mailing_address": school.get("mailing_address", school.get("street", "")),
                "phone": school.get("phone", ""),
                "email": school.get("email", ""),
                "website": school.get("website", ""),
                "ratings_offered": school.get("ratings_offered", ""),
                "n_number": n_num,
            }

            # Enrich with registry data
            if registry is not None:
                match = registry[registry["N-NUMBER"] == n_num]
                if not match.empty:
                    row = match.iloc[0]
                    record["make"] = row.get("MFR", row.get("MANUFACTURER", ""))
                    record["model"] = row.get("MODEL", "")
                    record["year_mfr"] = row.get("YEAR_MFR", "")
                    record["type_aircraft"] = row.get("TYPE_AIRCRAFT", "")
                    record["type_engine"] = row.get("TYPE_ENGINE", "")
                    record["icao_hex"] = row.get("MODE_S_CODE_HEX", "")

            flat_records.append(record)

    flat_df = pd.DataFrame(flat_records)

    # Save flat CSV
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    flat_path = OUTPUT_DIR / "flight_school_fleet_map.csv"
    flat_df.to_csv(flat_path, index=False)
    print(f"  Flat dataset: {flat_path} ({len(flat_df):,} rows)")

    # Build structured JSON: nested fleet per school
    print("\n[2/3] Building structured JSON...")
    structured = []

    for _, school in schools.iterrows():
        n_numbers = str(school.get("n_numbers", "")).split(",")
        n_numbers = [n.strip() for n in n_numbers if n.strip()]

        fleet = []
        for n_num in n_numbers:
            aircraft = {"n_number": n_num}
            if registry is not None:
                match = registry[registry["N-NUMBER"] == n_num]
                if not match.empty:
                    row = match.iloc[0]
                    aircraft["make"] = row.get("MFR", row.get("MANUFACTURER", ""))
                    aircraft["model"] = row.get("MODEL", "")
                    aircraft["year"] = row.get("YEAR_MFR", "")
            fleet.append(aircraft)

        entry = {
            "name": school.get("entity_name", ""),
            "airport_id": school.get("home_airport", ""),
            "airport_name": school.get("airport_name", ""),
            "city": school.get("city", ""),
            "state": school.get("state", ""),
            "mailing_address": school.get("mailing_address", school.get("street", "")),
            "phone": school.get("phone", ""),
            "email": school.get("email", ""),
            "website": school.get("website", ""),
            "ratings_offered": school.get("ratings_offered", "").split(",") if school.get("ratings_offered") else [],
            "school_score": int(school.get("school_score", 0) or 0),
            "fleet": fleet,
            "fleet_size": len(fleet),
        }
        structured.append(entry)

    # Sort by score
    structured.sort(key=lambda x: x["school_score"], reverse=True)

    json_path = OUTPUT_DIR / "flight_school_fleet_map.json"
    with open(json_path, "w") as f:
        json.dump(structured, f, indent=2)
    print(f"  Structured JSON: {json_path}")

    # Generate summary statistics
    print("\n[3/3] Generating summary statistics...")
    stats = {
        "total_schools_identified": len(schools),
        "total_aircraft_mapped": len(flat_df),
        "states_covered": flat_df["state"].nunique() if "state" in flat_df else 0,
        "airports_covered": flat_df["home_airport"].nunique() if "home_airport" in flat_df else 0,
    }

    if "state" in flat_df.columns:
        stats["schools_by_state"] = (
            flat_df.drop_duplicates(subset=["school_name", "state"])
            ["state"].value_counts()
            .head(20)
            .to_dict()
        )

    if "make" in flat_df.columns:
        stats["aircraft_by_make"] = (
            flat_df["make"].value_counts().head(20).to_dict()
        )

    if "model" in flat_df.columns:
        stats["aircraft_by_model"] = (
            flat_df["model"].value_counts().head(30).to_dict()
        )

    stats_path = OUTPUT_DIR / "summary_stats.json"
    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"  Summary stats: {stats_path}")

    print(f"\n  === Summary ===")
    print(f"  Total flight schools: {stats['total_schools_identified']:,}")
    print(f"  Total aircraft mapped: {stats['total_aircraft_mapped']:,}")
    print(f"  States covered: {stats['states_covered']}")
    print(f"  Airports covered: {stats['airports_covered']}")

    print(f"\nPhase 7 complete. Final dataset assembled.")
    return structured


if __name__ == "__main__":
    run()
