#!/usr/bin/env python3
"""
08e_faa_registry_discovery.py — Discover flight schools/clubs from FAA registry.

Scans the FAA aircraft registry for entities owning 2+ training-type aircraft
(C172, C152, PA-28, PA-44, DA20, DA40, DA42, SR20, BE76) that are NOT already
in our school corpus. Outputs a ranked list of candidates for crawling.

Training aircraft identification uses ACFTREF.txt type codes matched against
known trainer models. Only 'V' (valid) registrations are considered.

Delaware LLCs and trustee entities are flagged separately since they obscure
the actual operating school.

Output: output/faa_discovered_schools.csv
        output/faa_discovered_schools.json

Usage:
    python 08e_faa_registry_discovery.py                # full discovery
    python 08e_faa_registry_discovery.py --min-fleet 3  # require 3+ aircraft
"""
import argparse
import csv
import io
import json
from collections import Counter, defaultdict
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent / "output"


def load_training_codes() -> dict[str, str]:
    """Load ACFTREF and return {mfr_mdl_code: description} for training types."""
    acft_path = OUTPUT_DIR / "ACFTREF.txt"
    raw = acft_path.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))
    training_codes = {}
    for r in reader:
        code = r.get("CODE", "").strip()
        mfr = r.get("MFR", "").strip().upper()
        model = r.get("MODEL", "").strip().upper()
        if not code:
            continue
        is_trainer = (
            ("CESSNA" in mfr and any(m in model for m in ["172", "152"]))
            or ("PIPER" in mfr and any(m in model for m in ["PA-28", "PA-44"]))
            or (
                "DIAMOND" in mfr
                and any(
                    m in model
                    for m in ["DA 20", "DA 40", "DA 42", "DA20", "DA40", "DA42"]
                )
            )
            or ("CIRRUS" in mfr and "SR20" in model)
            or ("BEECH" in mfr and "76" in model)
        )
        if is_trainer:
            training_codes[code] = f"{mfr} {model}"
    return training_codes


def load_known_nn() -> set[str]:
    """Load N-numbers already in flight_line.json."""
    fl_path = OUTPUT_DIR / "flight_line.json"
    if not fl_path.exists():
        return set()
    fl = json.loads(fl_path.read_text())
    known = set()
    for school in fl:
        for ac in school.get("fleet", []):
            nn = (ac.get("n_number") or "").upper()
            if nn:
                known.add(nn)
    return known


def load_known_schools() -> set[str]:
    """Load school names/domains already in our corpus for matching."""
    fl_path = OUTPUT_DIR / "flight_line.json"
    if not fl_path.exists():
        return set()
    fl = json.loads(fl_path.read_text())
    names = set()
    for school in fl:
        names.add(school.get("school_name", "").upper().strip())
        # Also add normalized website domain
        website = school.get("school_website", "")
        if website:
            from urllib.parse import urlparse

            domain = urlparse(website).netloc.lower().replace("www.", "")
            if domain:
                names.add(domain)
    return names


def run(min_fleet: int = 2):
    training_codes = load_training_codes()
    print(f"Training aircraft type codes: {len(training_codes)}")

    known_nn = load_known_nn()
    print(f"Known N-numbers in flight_line.json: {len(known_nn)}")

    # Scan FAA registry
    faa_path = OUTPUT_DIR / "faa_master.csv"
    raw = faa_path.read_text(encoding="utf-8")
    start = raw.find("N-NUMBER")
    if start > 0:
        raw = raw[start:]

    reader = csv.DictReader(io.StringIO(raw))

    # Group by registrant
    registrant_fleet: dict[tuple, list] = defaultdict(list)
    for row in reader:
        code = row.get("MFR MDL CODE", "").strip()
        status = row.get("STATUS CODE", "").strip()
        if code in training_codes and status == "V":
            name = row.get("NAME", "").strip()
            city = row.get("CITY", "").strip()
            state = row.get("STATE", "").strip()
            nn = "N" + row.get("N-NUMBER", "").strip()
            street = row.get("STREET", "").strip()
            zip_code = row.get("ZIP CODE", "").strip()
            hex_code = row.get("MODE S CODE HEX", "").strip()
            key = (name, city, state)
            registrant_fleet[key].append(
                {
                    "n_number": nn,
                    "type": training_codes[code],
                    "hex": hex_code,
                    "year": row.get("YEAR MFR", "").strip(),
                    "street": street,
                    "zip": zip_code,
                }
            )

    multi = {k: v for k, v in registrant_fleet.items() if len(v) >= min_fleet}
    print(f"Registrants with {min_fleet}+ training aircraft: {len(multi)}")

    # Split into: already known, new discoveries
    new_discoveries = []
    already_known = 0
    for (name, city, state), aircraft in multi.items():
        nns = [ac["n_number"] for ac in aircraft]
        overlap = sum(1 for nn in nns if nn in known_nn)
        if overlap > 0:
            already_known += 1
            continue

        # Classify entity type
        uname = name.upper()
        is_de_llc = state == "DE" and ("LLC" in uname or "TRUSTEE" in uname)
        is_trustee = "TRUSTEE" in uname
        is_university = any(
            kw in uname
            for kw in [
                "UNIVERSITY",
                "COLLEGE",
                "STATE U",
                "INSTITUTE OF TECH",
            ]
        )

        school_keywords = [
            "FLIGHT",
            "FLYING",
            "AVIATION",
            "AERO",
            "AIR ",
            "PILOT",
            "SCHOOL",
            "CLUB",
            "ACADEMY",
            "TRAINING",
            "WINGS",
        ]
        has_school_name = any(kw in uname for kw in school_keywords)

        entity_type = "unknown"
        if is_de_llc or is_trustee:
            entity_type = "leasing_entity"
        elif is_university:
            entity_type = "university"
        elif has_school_name:
            entity_type = "likely_school"
        else:
            entity_type = "possible_school"

        types = Counter(ac["type"] for ac in aircraft)
        type_summary = ", ".join(f"{v}x {k}" for k, v in types.most_common(3))

        new_discoveries.append(
            {
                "registrant_name": name,
                "city": city,
                "state": state,
                "fleet_size": len(aircraft),
                "entity_type": entity_type,
                "type_summary": type_summary,
                "n_numbers": [ac["n_number"] for ac in aircraft],
                "aircraft": aircraft,
                "street": aircraft[0]["street"] if aircraft else "",
                "zip": aircraft[0]["zip"] if aircraft else "",
            }
        )

    new_discoveries.sort(key=lambda x: -x["fleet_size"])

    print(f"Already in our DB: {already_known}")
    print(f"NEW discoveries: {len(new_discoveries)}")

    # Stats by entity type
    by_type = Counter(d["entity_type"] for d in new_discoveries)
    total_ac = sum(d["fleet_size"] for d in new_discoveries)
    print(f"Total new aircraft: {total_ac}")
    print("By entity type:")
    for t, c in by_type.most_common():
        ac_count = sum(d["fleet_size"] for d in new_discoveries if d["entity_type"] == t)
        print(f"  {t}: {c} entities, {ac_count} aircraft")

    # Save CSV (flat, one row per entity)
    csv_path = OUTPUT_DIR / "faa_discovered_schools.csv"
    columns = [
        "registrant_name",
        "city",
        "state",
        "fleet_size",
        "entity_type",
        "type_summary",
        "street",
        "zip",
        "n_numbers",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for d in new_discoveries:
            row = dict(d)
            row["n_numbers"] = ";".join(d["n_numbers"])
            writer.writerow(row)
    print(f"\nCSV: {csv_path} ({len(new_discoveries)} rows)")

    # Save JSON (full detail including per-aircraft data)
    json_path = OUTPUT_DIR / "faa_discovered_schools.json"
    with open(json_path, "w") as f:
        json.dump(new_discoveries, f, indent=2)
    print(f"JSON: {json_path}")

    # Print top discoveries
    print(f"\n{'='*80}")
    print(f"Top 50 NEW discoveries (not in flight_line.json):")
    print(f"{'='*80}")
    for d in new_discoveries[:50]:
        tag = f"[{d['entity_type']}]"
        print(
            f"  {d['fleet_size']:3d} ac  {tag:18s}  {d['registrant_name']:<45s}  {d['city']}, {d['state']}"
        )
        print(f"         {d['type_summary']}")

    return new_discoveries


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-fleet", type=int, default=2, help="Minimum fleet size")
    args = parser.parse_args()
    run(min_fleet=args.min_fleet)
