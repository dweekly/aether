#!/bin/bash
# Alert when the adsb.lol archive ingest stops or degrades. Modelled on
# ~/dev/nanoclaw-data/scripts/check-backup-status.sh: every check runs, every
# problem is collected, one alert goes to ntfy and Mailgun.
#
# Checks what an exit code cannot:
#   1. the daily job's status file says ok
#   2. it ran in the last 30 h (silence is the failure mode)
#   3. the newest ingested day is at most 3 days old
#   4. the newest day's US aircraft count is not >30% below the trailing median
#
# CHECK_TEST=1 forces a problem and sends to a throwaway ntfy topic with
# Mailgun in test mode, which proves the delivery path without paging anyone.
set -uo pipefail
cd "$(dirname "$0")"
STATUS="$HOME/.local/state/aether/history-status.json"
MANIFESTS=../data/history/manifest
NTFY_TOPIC="${NTFY_TOPIC:-dew-mastodon-f44f6012}"
source ~/.config/monitor/mailgun.env
ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
problems=()
problem() { echo "$(ts) PROBLEM: $*"; problems+=("$*"); }
age_h() { local s; s=$(date -u -d "${1:-1970-01-01}" +%s 2>/dev/null || echo 0); echo $(( ($(date -u +%s) - s) / 3600 )); }

deliver() {
  local body ntfy_rc mg_rc topic="$NTFY_TOPIC" testmode=no
  [ "${CHECK_TEST:-0}" = 1 ] && { topic="aether-check-test-$$"; testmode=yes; }
  body=$(printf '%s\n\n' "${problems[@]}")
  curl -sf -H "Title: aether: ${#problems[@]} ADS-B archive problem(s)" -H "Priority: high" -H "Tags: airplane,warning" \
       --data-raw "$body" "https://ntfy.sh/$topic" >/dev/null 2>&1; ntfy_rc=$?
  curl -sf --user "api:$MAILGUN_API_KEY" "https://api.mailgun.net/v3/$MAILGUN_DOMAIN/messages" \
       -F from="$MAILGUN_FROM" -F to="$MAILGUN_TO" -F o:testmode="$testmode" \
       -F subject="aether: ${#problems[@]} ADS-B archive problem(s)" -F text="$body" >/dev/null 2>&1; mg_rc=$?
  echo "$(ts) delivery: ntfy rc=$ntfy_rc mailgun rc=$mg_rc testmode=$testmode"
}

[ "${CHECK_TEST:-0}" = 1 ] && problem "forced test problem (CHECK_TEST=1)"
if [ -f "$STATUS" ]; then
  st=$(jq -r '.status // empty' "$STATUS"); at=$(jq -r '.at // empty' "$STATUS")
  [ "$st" = ok ] || problem "daily ingest status=$st at $at: $(jq -r '.detail' "$STATUS")"
  [ "$(age_h "$at")" -le 30 ] || problem "daily ingest last ran $(age_h "$at")h ago (expected daily 06:30Z)"
else
  problem "no status file at $STATUS -- has 37_history_daily.sh run?"
fi
latest=$(ls "$MANIFESTS" 2>/dev/null | sort | tail -1 | sed 's/.json$//')
if [ -n "$latest" ]; then
  [ "$(age_h "$latest")" -le 96 ] || problem "newest ingested day is $latest ($(age_h "$latest")h old)"
  counts=$(ls "$MANIFESTS" | sort | tail -15 | while read -r f; do jq -r '.us_aircraft' "$MANIFESTS/$f"; done)
  last=$(printf '%s\n' "$counts" | tail -1)
  median=$(printf '%s\n' "$counts" | head -n -1 | sort -n | awk '{a[NR]=$1} END {print (NR ? a[int((NR+1)/2)] : 0)}')
  if [ "${median:-0}" -gt 0 ] && [ $(( last * 100 / median )) -lt 70 ]; then
    problem "$latest has $last US aircraft, under 70% of the trailing median $median"
  fi
else
  problem "no manifests in $MANIFESTS"
fi

if [ ${#problems[@]} -gt 0 ]; then deliver; exit 1; fi
echo "$(ts) archive ingest OK (latest $latest)"
