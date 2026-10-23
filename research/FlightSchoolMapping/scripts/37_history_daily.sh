#!/bin/bash
# Daily: ingest any of the last 7 archive days not yet ingested (adsb.lol
# publishes day D around 03:30 UTC on D+1), then write a status file that
# check_history_status.sh reads. Cron on trogdor, 06:30 UTC.
set -uo pipefail
cd "$(dirname "$0")"
STATE="$HOME/.local/state/aether"
mkdir -p "$STATE"
start=$(date -u -d '7 days ago' +%F)
end=$(date -u -d '1 day ago' +%F)
export GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"
out=$(.venv/bin/python 31_history_backfill.py "$start" "$end" --parallel 2 2>&1); rc=$?
echo "$out"
latest=$(ls ../data/history/manifest | sort | tail -1 | sed 's/.json$//')
failed=$(printf '%s\n' "$out" | grep -c ' FAILED ' || true)
status=ok; detail="latest day $latest"
[ "$rc" -eq 0 ] || { status=FAILED; detail="backfill exit $rc"; }
[ "$failed" -eq 0 ] || { status=FAILED; detail="$failed day(s) failed: $(printf '%s\n' "$out" | grep ' FAILED ' | tr '\n' ';')"; }
jq -n --arg s "$status" --arg at "$(date -u +%FT%TZ)" --arg d "$detail" --arg l "$latest" \
  '{status:$s, at:$at, detail:$d, latest_day:$l}' > "$STATE/history-status.json.tmp" \
  && mv "$STATE/history-status.json.tmp" "$STATE/history-status.json"
