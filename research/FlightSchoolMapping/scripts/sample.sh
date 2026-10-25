#!/bin/bash
# make sample: run the real ingest (30) and registry join (34) on the committed
# fixtures in sample/ and check the result against sample/expected.json.
# Needs only python3; installs pyarrow and duckdb into .sample-venv on first run.
# Exit 0 only if the result matches.
set -euo pipefail
cd "$(dirname "$0")/.."
VENV=.sample-venv
# The stamp is written only after a full install, so an interrupted one is redone.
if ! cmp -s scripts/requirements-sample.txt "$VENV/.installed" 2>/dev/null; then
  "${PYTHON:-python3}" -m venv "$VENV"
  "$VENV/bin/pip" install -q -r scripts/requirements-sample.txt
  cp scripts/requirements-sample.txt "$VENV/.installed"
fi
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/registry"
cp -R sample/registry/. "$tmp/registry/"
AETHER_DATA="$tmp" "$VENV/bin/python" scripts/30_adsblol_history.py 2026-10-22 --traces sample --workers 2 > /dev/null
AETHER_DATA="$tmp" "$VENV/bin/python" scripts/34_fleet_summary.py --registry 2026-10-23 --release sample > /dev/null
"$VENV/bin/python" - "$tmp/release/sample/results.json" sample/expected.json <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
got = {"window": r["window"]["included_days"], "totals": {x["observed_class"]: x["n"] for x in r["totals"]}}
want = json.load(open(sys.argv[2]))
t = got["totals"]
print(f"sample: {sum(t.values())} registered aircraft, {t.get('adsb', 0) + t.get('other_only', 0)} heard on "
      f"{got['window'][0]}, {t.get('not_observed', 0)} silent")
if got != want:
    print(f"MISMATCH\n  got:      {got}\n  expected: {want}")
    sys.exit(1)
print("matches sample/expected.json")
PY
