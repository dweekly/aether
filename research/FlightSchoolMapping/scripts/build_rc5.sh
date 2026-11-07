#!/bin/bash
# Release candidate rc5 (docs/hackers-2026-rc5-additions.md): built like rc4 (v0.3-20261023's frozen aircraft
# tables, 34 not rerun; the school stages on a new flight_line.json), with
#   - flight_line.json rebuilt in aether-addschools by 21 + 22 --offline with output/added_schools_rc5.json
#     (67_lead_schools.py), copied in and its sha256 recorded (it is not committed: 21 rebuilds it from the
#     committed inputs);
#   - 38 leaving out the schools in output/lapsed_domains.json;
#   - 35 from hackers-airports (the code the live explorer runs, the same file rc4's second pass used) with
#     the NTSB tail records, in one pass.
# Code runs from a detached checkout of hackers-talk at $1 in aether-desc-build; the release is written to
# aether-addschools' data/release like rc1-rc4. Usage: build_rc5.sh <hackers-talk commit>
set -euo pipefail
TALK=${1:?hackers-talk commit}
A=/mnt/data/home/dew/dev/aether-addschools
B=/mnt/data/home/dew/dev/aether-desc-build
PY=/mnt/data/home/dew/dev/aether/research/FlightSchoolMapping/scripts/.venv/bin/python
SITE=origin/hackers-airports
R=v0.4-20261105-rc5
REL=$A/research/FlightSchoolMapping/data/release
NTSB=$A/research/FlightSchoolMapping/data/research-2026-11/ntsb/tail_records.json
FL=$A/research/FlightSchoolMapping/output/flight_line.json
test ! -e $REL/$R || { echo "$REL/$R exists"; exit 1; }
git -C $A fetch -q origin
git -C $B fetch -q origin
F=$B/research/FlightSchoolMapping
git -C $B checkout -q -f --detach $TALK
git -C $A show $SITE:research/FlightSchoolMapping/scripts/35_export_web.py > $F/scripts/35_export_web.py
git -C $A show $SITE:research/FlightSchoolMapping/tests/test_summary.py > $F/tests/test_summary.py
cp -p $FL $F/output/flight_line.json
for d in fleet_html_raw fleet_browser_html_raw; do
  test -e $F/output/$d || ln -s $A/research/FlightSchoolMapping/output/$d $F/output/$d
done
test -L $F/data/history || ln -sfn $(readlink -f $A/research/FlightSchoolMapping/data/history) $F/data/history
mkdir -p $F/data/release $REL/$R
for x in v0.3-20261023 v0.4-20261105-rc4 $R; do ln -sfn $REL/$x $F/data/release/$x; done
V3=$REL/v0.3-20261023
for f in aircraft.parquet unmatched.parquet results.json airport_ops.json survey_check.json scrape_facts.json; do cp -p $V3/$f $REL/$R/; done
{ echo "built $(date -u +%FT%TZ) on $(hostname)"; echo "hackers-talk $(git -C $B rev-parse HEAD)"
  echo "35_export_web.py from $SITE $(git -C $A rev-parse --short $SITE)"
  echo "flight_line.json sha256 $(sha256sum $F/output/flight_line.json | cut -c1-64) (rebuilt in aether-addschools by 21 + 22 --offline)"
  echo "lapsed_domains.json sha256 $(sha256sum $F/output/lapsed_domains.json | cut -c1-64)"
  echo "ntsb tail_records.json sha256 $(sha256sum $NTSB | cut -c1-64)"; } > $REL/$R/BUILD.txt
cd $F && echo "== pytest $(date -u +%T)" && $PY -m pytest -q tests/ 2>&1 | tail -2
cd $F/scripts
ulimit -n 65536
echo "== 38 $(date -u +%T)"; $PY 38_schools.py --release $R
echo "== 35 $(date -u +%T)"; AETHER_DATA=$F/data $PY 35_export_web.py --release $R --ntsb $NTSB
for s in 44_airports 40_training_detect 41_privacy 42_package_release; do echo "== $s $(date -u +%T)"; $PY $s.py --release $R; done
cd $F
for ref in v0.4-20261105-rc4 v0.3-20261023; do
  echo "== release-check vs $ref $(date -u +%T)"
  $PY scripts/57_added_schools.py release-check --release $R --reference-release $ref 2>&1 \
    | grep -vE "^  [a-z0-9./-]+ +.* fleet +[0-9]+ flew" | cut -c1-400 || echo "release-check vs $ref exit $?"
done
echo DONE $(date -u +%T)
