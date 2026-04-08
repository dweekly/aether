#!/usr/bin/env python3
"""
Prepare story-specific JSON data files from flight_line.json.

Story: "What Does It Cost to Rent a Cessna 172?"
Reads the master flight_line.json and outputs compact, pre-aggregated
JSON files for each chart in the story.

Usage:
    python3 scripts/prep_story_data.py
"""

import json
import csv
import statistics
import sys
from pathlib import Path
from collections import defaultdict

# Paths
SCRIPT_DIR = Path(__file__).parent
WEBSITE_DIR = SCRIPT_DIR.parent
# Use main repo's research dir (worktrees don't have untracked output files)
MAIN_REPO = Path.home() / "dev" / "aether"
RESEARCH_DIR = MAIN_REPO / "research" / "FlightSchoolMapping"
FLIGHT_LINE = RESEARCH_DIR / "output" / "flight_line.json"
PUBLIC_CSV = RESEARCH_DIR / "output" / "flight_schools_public.csv"
STORY_DIR = WEBSITE_DIR / "src" / "data" / "stories" / "cessna-172-rental-cost"

# FAA regions by state (for regional grouping)
FAA_REGIONS = {
    "AK": "Alaska", "HI": "Western-Pacific",
    "WA": "Northwest Mountain", "OR": "Northwest Mountain", "ID": "Northwest Mountain",
    "MT": "Northwest Mountain", "WY": "Northwest Mountain", "CO": "Northwest Mountain", "UT": "Northwest Mountain",
    "CA": "Western-Pacific", "NV": "Western-Pacific", "AZ": "Western-Pacific",
    "NM": "Southwest", "TX": "Southwest", "OK": "Southwest", "AR": "Southwest", "LA": "Southwest",
    "MN": "Great Lakes", "WI": "Great Lakes", "MI": "Great Lakes", "IL": "Great Lakes",
    "IN": "Great Lakes", "OH": "Great Lakes", "ND": "Great Lakes", "SD": "Great Lakes",
    "NE": "Central", "KS": "Central", "MO": "Central", "IA": "Central",
    "FL": "Southern", "GA": "Southern", "AL": "Southern", "MS": "Southern",
    "SC": "Southern", "NC": "Southern", "TN": "Southern", "KY": "Southern",
    "VA": "Southern", "WV": "Southern", "PR": "Southern", "VI": "Southern",
    "ME": "New England", "NH": "New England", "VT": "New England",
    "MA": "New England", "RI": "New England", "CT": "New England",
    "NY": "Eastern", "NJ": "Eastern", "PA": "Eastern",
    "DE": "Eastern", "MD": "Eastern", "DC": "Eastern",
}


def load_flight_line():
    """Load and return the master flight_line.json."""
    print(f"Loading {FLIGHT_LINE}...")
    with open(FLIGHT_LINE) as f:
        data = json.load(f)
    print(f"  Loaded {len(data)} schools")
    return data


def load_school_locations():
    """Load lat/lng from flight_schools_public.csv, keyed by school name."""
    locations = {}
    with open(PUBLIC_CSV) as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = row["name"]
            lat = row.get("latitude", "")
            lng = row.get("longitude", "")
            if lat and lng:
                try:
                    locations[key] = (float(lat), float(lng))
                except ValueError:
                    pass
    print(f"  Loaded {len(locations)} school locations from CSV")
    return locations


def extract_aircraft_rates(data):
    """
    Extract all aircraft with rental rates, returning a list of records
    with school context.
    """
    records = []
    for school in data:
        school_name = school.get("name", "")
        school_state = ""
        school_airport = ""

        # Get primary location
        locs = school.get("locations", [])
        if locs:
            school_airport = locs[0].get("airport_id", "")
            label = locs[0].get("label", "")
            # Extract state from label (typically "City, ST ZIP" or similar)
            # Also check FAA registrant state from fleet
            parts = label.split(",")
            if len(parts) >= 2:
                state_part = parts[-1].strip().split()[0] if parts[-1].strip() else ""
                if len(state_part) == 2 and state_part.isalpha():
                    school_state = state_part.upper()

        for ac in school.get("fleet", []):
            rate = ac.get("wet_rate_hr")
            if rate is None or rate <= 0:
                continue

            faa = ac.get("faa", {})
            display = ac.get("display_name", "")
            mfr = faa.get("mfr", "")
            model = faa.get("model", "")
            year = faa.get("year_mfr", "")

            # Infer state from FAA registrant if not from location
            state = school_state
            if not state:
                state = faa.get("registrant_state", "")

            adsb = ac.get("adsb", {})

            records.append({
                "school": school_name,
                "airport": school_airport or ac.get("airport_id", ""),
                "state": state,
                "display_name": display,
                "mfr": mfr,
                "model": model,
                "year": int(year) if year and str(year).isdigit() else None,
                "rate": round(rate, 2),
                "equipment": ac.get("equipment", []),
                "adsb_active": adsb.get("active", False),
                "n_number": ac.get("n_number", ""),
            })

    return records


def classify_type(mfr, model, display_name):
    """Classify aircraft into broad comparison types."""
    mfr_upper = (mfr or "").upper()
    model_upper = (model or "").upper()
    display_upper = (display_name or "").upper()

    # Cessna 172 variants
    if "CESSNA" in mfr_upper or "CESSNA" in display_upper:
        if "172" in model_upper or "172" in display_upper:
            return "Cessna 172"
        if "152" in model_upper or "152" in display_upper:
            return "Cessna 152"
        if "150" in model_upper or "150" in display_upper:
            return "Cessna 150"
        if "182" in model_upper or "182" in display_upper:
            return "Cessna 182"

    # Piper
    if "PIPER" in mfr_upper or "PIPER" in display_upper:
        if any(x in display_upper for x in ["WARRIOR", "PA-28-161", "PA28-161"]):
            return "Piper Warrior"
        if any(x in display_upper for x in ["ARCHER", "PA-28-181", "PA28-181"]):
            return "Piper Archer"
        if any(x in display_upper for x in ["CHEROKEE", "PA-28-140", "PA28-140"]):
            return "Piper Cherokee"
        if "PA-28" in model_upper or "PA-28" in display_upper:
            return "Piper PA-28"

    # Diamond
    if "DIAMOND" in mfr_upper or "DIAMOND" in display_upper:
        if "DA40" in model_upper or "DA40" in display_upper or "DA 40" in display_upper:
            return "Diamond DA40"
        if "DA20" in model_upper or "DA20" in display_upper or "DA 20" in display_upper:
            return "Diamond DA20"

    # Cirrus
    if "CIRRUS" in mfr_upper or "CIRRUS" in display_upper:
        if "SR20" in model_upper or "SR20" in display_upper or "SR 20" in display_upper:
            return "Cirrus SR20"
        if "SR22" in model_upper or "SR22" in display_upper or "SR 22" in display_upper:
            return "Cirrus SR22"

    return None


def build_c172_rates(all_rates):
    """Filter to Cessna 172 variants and output individual rates."""
    c172 = [r for r in all_rates if classify_type(r["mfr"], r["model"], r["display_name"]) == "Cessna 172"]
    # Filter out extreme outliers (likely errors)
    c172 = [r for r in c172 if 50 <= r["rate"] <= 500]
    print(f"  Cessna 172 rates: {len(c172)} (after filtering $50-$500)")

    # Output compact records
    output = []
    for r in c172:
        rec = {
            "school": r["school"],
            "airport": r["airport"],
            "state": r["state"],
            "rate": r["rate"],
        }
        if r["year"]:
            rec["year"] = r["year"]
        if r["equipment"]:
            rec["equipment"] = r["equipment"]
        output.append(rec)

    return output


def build_histogram(c172_rates):
    """Pre-bin rates into $10 histogram bins."""
    rates = [r["rate"] for r in c172_rates]
    if not rates:
        return {"bins": [], "stats": {}}

    bin_width = 10
    min_rate = int(min(rates) // bin_width) * bin_width
    max_rate = int(max(rates) // bin_width + 1) * bin_width

    bins = []
    for lo in range(min_rate, max_rate, bin_width):
        hi = lo + bin_width
        count = sum(1 for r in rates if lo <= r < hi)
        if count > 0:
            bins.append({"lo": lo, "hi": hi, "count": count})

    stats = {
        "count": len(rates),
        "min": round(min(rates), 2),
        "max": round(max(rates), 2),
        "mean": round(statistics.mean(rates), 2),
        "median": round(statistics.median(rates), 2),
        "p25": round(sorted(rates)[len(rates) // 4], 2),
        "p75": round(sorted(rates)[3 * len(rates) // 4], 2),
        "stdev": round(statistics.stdev(rates), 2) if len(rates) > 1 else 0,
    }

    print(f"  Histogram: {len(bins)} bins, median=${stats['median']}, mean=${stats['mean']}")
    return {"bins": bins, "stats": stats}


def build_regional_summary(c172_rates):
    """Aggregate rates by state and FAA region."""
    by_state = defaultdict(list)
    for r in c172_rates:
        if r["state"]:
            by_state[r["state"]].append(r["rate"])

    states = []
    for state, rates in sorted(by_state.items()):
        if len(rates) < 3:
            continue  # Skip states with too few data points
        sorted_rates = sorted(rates)
        n = len(sorted_rates)
        states.append({
            "state": state,
            "region": FAA_REGIONS.get(state, "Other"),
            "count": n,
            "median": round(statistics.median(sorted_rates), 2),
            "mean": round(statistics.mean(sorted_rates), 2),
            "p25": round(sorted_rates[n // 4], 2),
            "p75": round(sorted_rates[3 * n // 4], 2),
            "min": round(sorted_rates[0], 2),
            "max": round(sorted_rates[-1], 2),
        })

    # Also aggregate by region
    by_region = defaultdict(list)
    for s in states:
        by_region[s["region"]].extend(
            by_state[s["state"]]
        )

    regions = []
    for region, rates in sorted(by_region.items()):
        sorted_rates = sorted(rates)
        n = len(sorted_rates)
        regions.append({
            "region": region,
            "count": n,
            "median": round(statistics.median(sorted_rates), 2),
            "mean": round(statistics.mean(sorted_rates), 2),
            "p25": round(sorted_rates[n // 4], 2),
            "p75": round(sorted_rates[3 * n // 4], 2),
            "min": round(sorted_rates[0], 2),
            "max": round(sorted_rates[-1], 2),
        })

    print(f"  Regional: {len(states)} states, {len(regions)} FAA regions")
    return {"states": states, "regions": regions}


def build_type_comparison(all_rates):
    """Build rate summaries for major trainer types."""
    types_of_interest = [
        "Cessna 150", "Cessna 152", "Cessna 172", "Cessna 182",
        "Piper Warrior", "Piper Archer", "Piper Cherokee",
        "Diamond DA20", "Diamond DA40",
        "Cirrus SR20", "Cirrus SR22",
    ]

    by_type = defaultdict(list)
    for r in all_rates:
        t = classify_type(r["mfr"], r["model"], r["display_name"])
        if t and t in types_of_interest:
            rate = r["rate"]
            if 30 <= rate <= 800:  # Wider range for diverse types
                by_type[t].append(rate)

    output = []
    for t in types_of_interest:
        rates = by_type.get(t, [])
        if len(rates) < 5:
            continue
        sorted_rates = sorted(rates)
        n = len(sorted_rates)
        output.append({
            "type": t,
            "count": n,
            "median": round(statistics.median(sorted_rates), 2),
            "mean": round(statistics.mean(sorted_rates), 2),
            "p25": round(sorted_rates[n // 4], 2),
            "p75": round(sorted_rates[3 * n // 4], 2),
            "min": round(sorted_rates[0], 2),
            "max": round(sorted_rates[-1], 2),
            "rates": [round(r, 2) for r in sorted_rates],  # Individual rates for distribution plot
        })

    print(f"  Type comparison: {len(output)} types with 5+ data points")
    return output


def build_school_map(c172_rates, school_locations):
    """Build compact map data: [lat, lng, rate, school, airport, state]."""
    # Average rate per school (some schools have multiple C172s)
    school_avg = defaultdict(lambda: {"rates": [], "airport": "", "state": ""})
    for r in c172_rates:
        entry = school_avg[r["school"]]
        entry["rates"].append(r["rate"])
        entry["airport"] = r["airport"]
        entry["state"] = r["state"]

    points = []
    for school_name, info in school_avg.items():
        loc = school_locations.get(school_name)
        if not loc:
            continue
        avg_rate = round(statistics.mean(info["rates"]), 2)
        points.append({
            "lat": round(loc[0], 4),
            "lng": round(loc[1], 4),
            "rate": avg_rate,
            "school": school_name,
            "airport": info["airport"],
            "state": info["state"],
            "count": len(info["rates"]),
        })

    print(f"  Map points: {len(points)} schools with C172 rates + coordinates")
    return points


def write_json(path, data):
    """Write JSON file with compact formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    size_kb = path.stat().st_size / 1024
    print(f"  Wrote {path.name} ({size_kb:.1f} KB)")


def main():
    print("=== Aether Flight: Story Data Prep ===\n")

    # Load source data
    data = load_flight_line()
    school_locations = load_school_locations()

    # Extract all rates
    print("\nExtracting aircraft rates...")
    all_rates = extract_aircraft_rates(data)
    print(f"  Total aircraft with rates: {len(all_rates)}")

    # Build story-specific datasets
    print("\nBuilding Cessna 172 story data...")

    c172_rates = build_c172_rates(all_rates)
    histogram = build_histogram(c172_rates)
    regional = build_regional_summary(c172_rates)
    type_comp = build_type_comparison(all_rates)
    map_data = build_school_map(c172_rates, school_locations)

    # Write outputs
    print(f"\nWriting to {STORY_DIR}/")
    write_json(STORY_DIR / "rates_individual.json", c172_rates)
    write_json(STORY_DIR / "rate_histogram.json", histogram)
    write_json(STORY_DIR / "regional_summary.json", regional)
    write_json(STORY_DIR / "type_comparison.json", type_comp)
    write_json(STORY_DIR / "school_map.json", map_data)

    print("\nDone!")


if __name__ == "__main__":
    main()
