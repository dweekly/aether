#!/usr/bin/env python3
"""
Flight School Aircraft Mapping — Full Pipeline Orchestrator

Runs all 7 phases in sequence, passing data between phases to avoid
redundant file I/O. Can also resume from a specific phase.

Usage:
    python run_pipeline.py              # Run full pipeline
    python run_pipeline.py --from 3     # Resume from Phase 3
    python run_pipeline.py --only 1 2   # Run only Phases 1 and 2
    python run_pipeline.py --dry-run    # Show what would run
"""

import argparse
import sys
import time
from pathlib import Path

# Add scripts directory to path for imports
SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))


def main():
    parser = argparse.ArgumentParser(
        description="Flight School Aircraft Mapping Pipeline"
    )
    parser.add_argument(
        "--from", dest="from_phase", type=int, default=1,
        help="Start from this phase number (1-7)",
    )
    parser.add_argument(
        "--only", nargs="+", type=int,
        help="Run only these specific phases",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would run without executing",
    )
    args = parser.parse_args()

    phases = {
        1: ("FAA Registry Download & Parse", "01_faa_registry"),
        2: ("Airport Reference Table", "02_airport_reference"),
        3: ("ADS-B Activity Data", "03_adsb_activity"),
        4: ("Home Base Assignment", "04_home_base"),
        5: ("Flight School Identification", "05_school_identifier"),
        6: ("Web Enrichment", "06_web_enrichment"),
        7: ("Assemble Final Dataset", "07_assemble_dataset"),
    }

    # Determine which phases to run
    if args.only:
        run_phases = sorted(args.only)
    else:
        run_phases = [p for p in phases if p >= args.from_phase]

    print("=" * 60)
    print("  Flight School Aircraft Mapping Pipeline")
    print("=" * 60)
    print(f"\n  Phases to run: {run_phases}")
    print()

    if args.dry_run:
        for p in run_phases:
            name, module = phases[p]
            print(f"  [DRY RUN] Phase {p}: {name} ({module}.py)")
        return

    # Run each phase
    registry = None
    airports = None
    adsb_data = None
    home_bases = None
    schools = None

    for phase_num in run_phases:
        name, module_name = phases[phase_num]
        print(f"\n{'#' * 60}")
        print(f"# Phase {phase_num}: {name}")
        print(f"{'#' * 60}\n")

        start = time.time()

        try:
            module = __import__(module_name)

            if phase_num == 1:
                registry = module.run()
            elif phase_num == 2:
                airports = module.run()
            elif phase_num == 3:
                adsb_data = module.run(registry=registry, airports=airports)
            elif phase_num == 4:
                home_bases = module.run(
                    registry=registry, airports=airports, adsb_data=adsb_data
                )
            elif phase_num == 5:
                schools = module.run(registry=registry, home_bases=home_bases)
            elif phase_num == 6:
                module.run(schools=schools)
            elif phase_num == 7:
                module.run()

            elapsed = time.time() - start
            print(f"\n  Phase {phase_num} completed in {elapsed:.1f}s")

        except Exception as e:
            elapsed = time.time() - start
            print(f"\n  Phase {phase_num} FAILED after {elapsed:.1f}s: {e}")
            print(f"  Stopping pipeline.")
            sys.exit(1)

    print(f"\n{'=' * 60}")
    print(f"  Pipeline complete!")
    print(f"  Check output/ directory for results.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
