#!/usr/bin/env python3
"""
Phase 5: Identify likely flight schools from fleet clusters.

Combines FAA registry data with home base assignments to identify entities
that operate multiple training aircraft from the same airport. These clusters
are strong signals of flight school operations.

Scoring heuristics:
- Multiple training-type aircraft under same registrant → high confidence
- Name matches flight school patterns → high confidence
- 3+ aircraft at same airport under same name → medium confidence
- Training models (172, PA-28, DA40, etc.) → bonus score
- Pattern work ratio > 0.5 (from ADS-B) → bonus score

Output: output/identified_schools.csv — ranked list of likely flight schools
"""

import re
from pathlib import Path

import pandas as pd

OUTPUT_DIR = Path(__file__).parent.parent / "output"

# Scoring weights
SCORE_NAME_MATCH = 30        # Name matches flight school patterns
SCORE_PER_AIRCRAFT = 5       # Per training aircraft at same airport
SCORE_TRAINING_MODEL = 3     # Per training-model aircraft
SCORE_MULTI_AIRCRAFT = 15    # 3+ aircraft under same entity at same airport
SCORE_PATTERN_WORK = 10      # High pattern work ratio from ADS-B

# Minimum score to be considered a candidate school
MIN_SCHOOL_SCORE = 20


def score_entity(group: pd.DataFrame) -> dict:
    """Score an entity (registrant at an airport) for flight school likelihood."""
    score = 0
    reasons = []

    name = group.iloc[0].get("NAME", "")
    n_aircraft = len(group)

    # Name-based scoring
    from scripts_01 import SCHOOL_REGEX  # noqa — would use if importable
    school_patterns = [
        r"flight\s+school", r"flying\s+school", r"aviation\s+academy",
        r"flight\s+academy", r"flight\s+training", r"aviation\s+training",
        r"flying\s+club", r"aero\s+club", r"flight\s+center",
        r"aviation\s+services", r"pilot\s+training",
    ]
    school_regex = re.compile("|".join(school_patterns), re.IGNORECASE)

    if school_regex.search(name):
        score += SCORE_NAME_MATCH
        reasons.append("name_match")

    # Fleet size scoring
    score += n_aircraft * SCORE_PER_AIRCRAFT
    if n_aircraft >= 3:
        score += SCORE_MULTI_AIRCRAFT
        reasons.append(f"fleet_size_{n_aircraft}")

    # Training model scoring
    if "IS_TRAINING_MODEL" in group.columns:
        n_training = (group["IS_TRAINING_MODEL"] == "True").sum()
        score += n_training * SCORE_TRAINING_MODEL
        if n_training > 0:
            reasons.append(f"training_models_{n_training}")

    return {
        "score": score,
        "reasons": ",".join(reasons),
        "n_aircraft": n_aircraft,
    }


def run(registry: pd.DataFrame = None, home_bases: pd.DataFrame = None):
    """Execute Phase 5: Flight school identification."""
    print("=" * 60)
    print("Phase 5: Flight School Identification")
    print("=" * 60)

    # Load data if not provided
    if registry is None:
        path = OUTPUT_DIR / "faa_ga_registry.csv"
        if path.exists():
            registry = pd.read_csv(path, dtype=str, keep_default_na=False)
        else:
            print("  Error: Run Phase 1 first")
            return None

    if home_bases is None:
        path = OUTPUT_DIR / "tail_home_bases.csv"
        if path.exists():
            home_bases = pd.read_csv(path, dtype=str, keep_default_na=False)

    # Merge registry with home bases
    print("\n[1/3] Merging registry with home base data...")

    if home_bases is not None and not home_bases.empty:
        if "icao_hex" in home_bases.columns and "MODE_S_CODE_HEX" in registry.columns:
            merged = registry.merge(
                home_bases[["icao_hex", "home_airport", "airport_name", "confidence"]],
                left_on="MODE_S_CODE_HEX",
                right_on="icao_hex",
                how="left",
            )
        else:
            merged = registry.copy()
            merged["home_airport"] = ""
    else:
        merged = registry.copy()
        merged["home_airport"] = ""

    # For aircraft without ADS-B home base, use registrant state as rough proxy
    if "home_airport" in merged.columns and "STATE" in merged.columns:
        merged["home_airport"] = merged["home_airport"].fillna("")

    print(f"  Total records: {len(merged):,}")
    has_home = (merged.get("home_airport", pd.Series(dtype=str)) != "").sum()
    print(f"  With home airport: {has_home:,}")

    # Group by registrant name + home airport (or state as fallback)
    print("\n[2/3] Identifying fleet clusters...")

    # Create grouping key: NAME + airport (or STATE)
    merged["group_key"] = merged["NAME"].str.upper().str.strip()
    if "home_airport" in merged.columns:
        merged["group_loc"] = merged["home_airport"].where(
            merged["home_airport"] != "", merged.get("STATE", "")
        )
    else:
        merged["group_loc"] = merged.get("STATE", "")

    merged["entity_key"] = merged["group_key"] + "|" + merged["group_loc"]

    # Score each entity
    results = []
    for entity_key, group in merged.groupby("entity_key"):
        if len(group) < 1:
            continue

        scores = score_entity(group)

        if scores["score"] >= MIN_SCHOOL_SCORE:
            first = group.iloc[0]
            fleet_models = []
            if "MODEL" in group.columns:
                fleet_models = group["MODEL"].value_counts().to_dict()
            elif "MFR" in group.columns and "MODEL" in group.columns:
                fleet_models = (group["MFR"] + " " + group["MODEL"]).value_counts().to_dict()

            results.append({
                "entity_name": first.get("NAME", ""),
                "home_airport": first.get("home_airport", ""),
                "airport_name": first.get("airport_name", ""),
                "state": first.get("STATE", ""),
                "city": first.get("CITY", ""),
                "street": first.get("STREET", ""),
                "zip_code": first.get("ZIP_CODE", ""),
                "n_aircraft": scores["n_aircraft"],
                "school_score": scores["score"],
                "score_reasons": scores["reasons"],
                "fleet_models": str(fleet_models),
                "n_numbers": ",".join(group.get("N-NUMBER", pd.Series(dtype=str)).tolist()),
            })

    results_df = pd.DataFrame(results)

    if not results_df.empty:
        results_df = results_df.sort_values("school_score", ascending=False)

    # Save
    print("\n[3/3] Saving identified schools...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "identified_schools.csv"
    results_df.to_csv(output_path, index=False)

    print(f"  Candidate flight schools identified: {len(results_df):,}")
    if not results_df.empty:
        print(f"\n  Top candidates by score:")
        for _, row in results_df.head(20).iterrows():
            name = row["entity_name"][:50]
            apt = row["home_airport"] or row["state"]
            n = row["n_aircraft"]
            score = row["school_score"]
            print(f"    [{score:3d}] {name:<50s} | {apt:<6s} | {n} aircraft")

    print(f"\nPhase 5 complete.")
    return results_df


if __name__ == "__main__":
    run()
