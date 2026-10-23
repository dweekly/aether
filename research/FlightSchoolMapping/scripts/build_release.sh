#!/bin/bash
# Build one explorer release from whatever archive days are ingested: the
# registry join (34) and the web export (35) always run together, so the
# window in results.json matches the day list the explorer draws.
# Usage: scripts/build_release.sh <release>
set -euo pipefail
rel="${1:?usage: build_release.sh <release>}"
cd "$(dirname "$0")"
rm -rf "../data/release/$rel"
.venv/bin/python 32_reassign_airports.py   # bring any day matched with an older airport index up to date
.venv/bin/python 34_fleet_summary.py --release "$rel"
.venv/bin/python 35_export_web.py --release "$rel"
.venv/bin/python 38_schools.py --release "$rel"
.venv/bin/python 44_airports.py --release "$rel"
