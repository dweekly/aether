#!/bin/bash
# Pull a built explorer release from trogdor:
#   web/                -> public/x/                (shipped to browsers)
#   schools_full.json,  -> src/data/explore/        (read at build time to generate school
#   airports_full.json                               and airport pages)
# Both destinations are gitignored.
#
# Usage: scripts/sync_explorer_data.sh <release>
set -euo pipefail
rel="${1:?usage: sync_explorer_data.sh <release>}"
here="$(cd "$(dirname "$0")/.." && pwd)"
src="trogdor:/mnt/data/home/dew/dev/aether/research/FlightSchoolMapping/data/release/${rel}"
mkdir -p "$here/public/x" "$here/src/data/explore"
/usr/bin/rsync -a --delete "$src/web/" "$here/public/x/"
/usr/bin/rsync -a "$src/schools_full.json" "$here/src/data/explore/schools_full.json"
/usr/bin/rsync -a "$src/airports_full.json" "$here/src/data/explore/airports_full.json"
echo "synced $(find "$here/public/x" -type f | wc -l | tr -d ' ') files + schools_full.json for release $rel"
