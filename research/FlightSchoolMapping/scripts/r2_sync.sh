#!/bin/bash
# r2_sync.sh — back up / restore FlightSchoolMapping data to Cloudflare R2.
#
# What lives where, and why:
#
#   adsb-snapshots/  data/adsb/snapshots  ~11 GB, +160 MB/day. IRREPLACEABLE —
#                    point-in-time observations that can never be re-fetched.
#                    Sync nightly.
#   crawl-cache/     fleet_html*, fleet_browser_*, rate_sheet_*  ~10 GB. Static.
#                    Re-crawlable in principle, but the web has moved on since
#                    March 2026, so treat as semi-permanent. Sync weekly.
#   archive-history/ data/history/{aircraft_day,legs,manifest}  ~8.5 GB, +~10 MB/day.
#                    adsb.lol's daily archive reduced per aircraft and per leg.
#                    Rebuildable only by re-downloading ~3 TB from adsb.lol's
#                    GitHub releases, which may not stay up. Sync nightly.
#   opensky-mondays/ data/opensky_mondays  ~130 MB. Internal only (OpenSky's
#                    license forbids redistribution); this private bucket is
#                    storage, not distribution. Static once built.
#   extract/         output/*.jsonl  ~11 MB. ~$25 of Sonnet plus hours of
#                    crawling. These are BUILD INPUTS, committed to git and
#                    mirrored here; restore-extract fills in any that a
#                    checkout is missing before `make` builds flight_line.json.
#
# Deliberately NOT backed up: faa_master.csv, ACFTREF.txt, airports.csv.
# `make refresh` re-downloads them for free.
#
# Credentials are reused from nanoclaw-data/.env. That token is scoped to the
# nanoclaw-data bucket and cannot create buckets, hence the aether-research/
# prefix inside it and --s3-no-check-bucket on every call.
#
# Usage:
#   r2_sync.sh backup-adsb        # nightly
#   r2_sync.sh backup-cache       # weekly
#   r2_sync.sh backup-extract     # after any crawl/extract run
#   r2_sync.sh backup-history     # nightly, after 37_history_daily.sh
#   r2_sync.sh backup-all
#   r2_sync.sh restore-extract    # what `make` calls when the .jsonl are absent
#   r2_sync.sh restore-adsb       # full snapshot history (11 GB — slow)
#   r2_sync.sh restore-history    # reduced archive + OpenSky Mondays (~8.6 GB)
#   r2_sync.sh status             # what is up there
#
# Add --dry-run to any backup subcommand to see what would transfer.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OUT_DIR="$PROJECT_DIR/output"
ADSB_DIR="$PROJECT_DIR/data/adsb/snapshots"
HISTORY_DIR="$PROJECT_DIR/data/history"
OPENSKY_DIR="$PROJECT_DIR/data/opensky_mondays"
HISTORY_PARTS=(aircraft_day legs manifest)

CRED_ENV="${R2_CRED_ENV:-$HOME/dev/nanoclaw-data/.env}"
REMOTE_BASE="${R2_REMOTE_BASE:-r2:nanoclaw-data/aether-research}"

if [ ! -f "$CRED_ENV" ]; then
  echo "ERROR: R2 credentials not found at $CRED_ENV" >&2
  echo "       Set R2_CRED_ENV to point at a file defining R2_ACCESS_KEY_ID," >&2
  echo "       R2_SECRET_ACCESS_KEY and R2_ENDPOINT." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$CRED_ENV"
set +a

for var in R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_ENDPOINT; do
  if [ -z "${!var:-}" ]; then
    echo "ERROR: $var is not set in $CRED_ENV" >&2
    exit 1
  fi
done

# rclone env-var backend — no rclone.conf entry needed.
export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
export RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT"

# The scoped token cannot CreateBucket, and rclone probes for that by default.
#
# On the 501 NotImplemented noise: this box has rclone v1.60 (Ubuntu package,
# 2022) and Cloudflare's edge rejects its first PUT of any new object with a
# plain-text 501, then accepts the retry. Verified 2026-08-06 across every
# checksum/multipart/ACL/path-style flag combination — none of them change it,
# and nanoclaw's own nightly backup has logged 4,063 of these while still
# completing successfully. So: retry generously and, crucially, verify by
# checksum afterwards rather than trusting the exit code. Upgrading rclone past
# ~1.64 would likely quiet it.
# --s3-no-head: after a PUT, rclone re-HEADs the object using the versionId R2
#   returns. R2 has no object versioning, so that HEAD 501s and rclone reports a
#   SUCCESSFUL upload as failed. The bytes land; only the bookkeeping is wrong.
#   Without this flag a single ADS-B run emitted 62,420 bogus ERROR lines, which
#   is how a genuine failure would have been buried. Losing rclone's own
#   post-upload check is fine here because verify() below runs a full
#   `rclone check --checksum` against the source, which is strictly stronger.
#   (Same reasoning, same flag, as ~/dev/nanoclaw-data/scripts/backup-r2.sh.)
RCLONE_OPTS=(--s3-no-check-bucket --s3-no-head --transfers 8 --checkers 16 --retries 5)

# Crawl caches, relative to output/.
CACHE_DIRS=(
  fleet_html
  fleet_html_raw
  fleet_browser_text
  fleet_browser_html_raw
  rate_sheet_pdf
  rate_sheet_text
)

# Build inputs that live only in R2.
EXTRACT_FILES=(
  expansion_crawl.jsonl
  expansion_extract.jsonl
  fleet_browser_extract_results.jsonl
  fleet_browser_extract_index.json
  fleet_extract_results.jsonl
  fleet_extract_batches.json
  fleet_crawl_index.json
  fleet_browser_index.json
  aopa_flight_schools.json
  faa_discovered_schools.json
)

cmd="${1:-}"
shift || true
EXTRA=("$@")

# `sync` mirrors deletions; the snapshot archive is append-only and must never
# lose history to a local mishap, so it uses `copy` instead.
backup_adsb() {
  echo "→ ADS-B snapshots (append-only copy, never deletes remote)"
  rclone copy "$ADSB_DIR" "$REMOTE_BASE/adsb-snapshots" \
    "${RCLONE_OPTS[@]}" --stats-one-line "${EXTRA[@]}"
  verify "$ADSB_DIR" "$REMOTE_BASE/adsb-snapshots"
}

# The 501 noise above means a clean exit code is not proof the bytes arrived.
# Compare checksums and fail loudly if anything is missing or differs.
verify() {
  local src="$1" dst="$2"
  case " ${EXTRA[*]} " in *" --dry-run "*) return 0 ;; esac
  echo "  verifying $dst ..."
  # Capture first, THEN match. Piping rclone straight into `grep -q` under
  # `set -o pipefail` inverts the result: grep exits on its first match, rclone
  # takes SIGPIPE, the pipeline reports non-zero, and the `if` concludes "no
  # problems" precisely BECAUSE problems were found. Every run since this script
  # was written logged "verified OK" — including nights when the destination was
  # empty. The old failure pattern was also unmatchable: rclone prints
  # "N differences found", not "differences found: N".
  local out rc=0
  out=$(rclone check "$src" "$dst" --s3-no-check-bucket --checksum --one-way "${@:3}" 2>&1) || rc=$?
  if [ "$rc" -ne 0 ] || printf '%s\n' "$out" | grep -qE 'ERROR|[1-9][0-9]* differences found'; then
    echo "  VERIFY FAILED for $dst — re-run this subcommand" >&2
    printf '%s\n' "$out" | grep -vE '^\s*$' | tail -5 >&2
    return 1
  fi
  # A check that compared nothing is not a passing check: demand the summary line.
  if ! printf '%s\n' "$out" | grep -q '0 differences found'; then
    echo "  VERIFY INCONCLUSIVE for $dst — rclone printed no comparison summary" >&2
    printf '%s\n' "$out" | tail -3 >&2
    return 1
  fi
  echo "  verified OK ($(printf '%s\n' "$out" | grep -oE '[0-9]+ matching files' | tail -1))"
}

# Re-ingesting a day replaces its files, so `copy` updates them; it never deletes remote.
backup_history() {
  echo "→ reduced archive history and OpenSky Mondays (copy, never deletes remote)"
  for d in "${HISTORY_PARTS[@]}"; do
    echo "  $d"
    rclone copy "$HISTORY_DIR/$d" "$REMOTE_BASE/archive-history/$d" \
      "${RCLONE_OPTS[@]}" --stats-one-line "${EXTRA[@]}"
    verify "$HISTORY_DIR/$d" "$REMOTE_BASE/archive-history/$d"
  done
  rclone copy "$OPENSKY_DIR" "$REMOTE_BASE/opensky-mondays" \
    "${RCLONE_OPTS[@]}" --stats-one-line "${EXTRA[@]}"
  verify "$OPENSKY_DIR" "$REMOTE_BASE/opensky-mondays"
}

restore_history() {
  echo "→ restoring reduced archive history and OpenSky Mondays from R2 (~8.6 GB)"
  for d in "${HISTORY_PARTS[@]}"; do
    mkdir -p "$HISTORY_DIR/$d"
    rclone copy "$REMOTE_BASE/archive-history/$d" "$HISTORY_DIR/$d" \
      "${RCLONE_OPTS[@]}" --stats-one-line "${EXTRA[@]}"
  done
  mkdir -p "$OPENSKY_DIR"
  rclone copy "$REMOTE_BASE/opensky-mondays" "$OPENSKY_DIR" \
    "${RCLONE_OPTS[@]}" --stats-one-line "${EXTRA[@]}"
}

backup_cache() {
  echo "→ crawl caches"
  for d in "${CACHE_DIRS[@]}"; do
    [ -d "$OUT_DIR/$d" ] || { echo "  (skip $d — not present)"; continue; }
    echo "  $d"
    rclone sync "$OUT_DIR/$d" "$REMOTE_BASE/crawl-cache/$d" \
      "${RCLONE_OPTS[@]}" --stats-one-line "${EXTRA[@]}"
    verify "$OUT_DIR/$d" "$REMOTE_BASE/crawl-cache/$d"
  done
}

backup_extract() {
  echo "→ extraction results (build inputs — git + R2)"
  local present=()
  for f in "${EXTRACT_FILES[@]}"; do
    if [ -f "$OUT_DIR/$f" ]; then
      present+=("$f")
    else
      echo "  WARNING: $f missing locally — not backed up" >&2
    fi
  done
  [ ${#present[@]} -gt 0 ] || { echo "  nothing to back up" >&2; return 1; }
  printf '%s\n' "${present[@]}" > /tmp/.r2_extract_list.$$
  rclone copy "$OUT_DIR" "$REMOTE_BASE/extract" \
    --files-from /tmp/.r2_extract_list.$$ \
    "${RCLONE_OPTS[@]}" --stats-one-line "${EXTRA[@]}"
  rm -f /tmp/.r2_extract_list.$$
  printf '%s\n' "${present[@]}" > /tmp/.r2_verify_list.$$
  verify "$OUT_DIR" "$REMOTE_BASE/extract" --files-from /tmp/.r2_verify_list.$$
  rm -f /tmp/.r2_verify_list.$$
  echo "  backed up ${#present[@]} file(s)"
}

restore_extract() {
  echo "→ restoring extraction results from R2"
  mkdir -p "$OUT_DIR"
  local missing=()
  for f in "${EXTRACT_FILES[@]}"; do
    [ -f "$OUT_DIR/$f" ] || missing+=("$f")
  done
  if [ ${#missing[@]} -eq 0 ]; then
    echo "  all present locally — nothing to restore"
    return 0
  fi
  echo "  missing locally: ${missing[*]}"
  printf '%s\n' "${missing[@]}" > /tmp/.r2_restore_list.$$
  rclone copy "$REMOTE_BASE/extract" "$OUT_DIR" \
    --files-from /tmp/.r2_restore_list.$$ \
    "${RCLONE_OPTS[@]}" --stats-one-line "${EXTRA[@]}"
  rm -f /tmp/.r2_restore_list.$$

  local still=()
  for f in "${missing[@]}"; do
    [ -f "$OUT_DIR/$f" ] || still+=("$f")
  done
  if [ ${#still[@]} -gt 0 ]; then
    echo "ERROR: not in R2 either: ${still[*]}" >&2
    echo "       These are build inputs. Without them the build silently" >&2
    echo "       produces a smaller dataset. Regenerate with 'make crawl'" >&2
    echo "       and 'make extract' (hours, ~\$25)." >&2
    return 1
  fi
  echo "  restored ${#missing[@]} file(s)"
}

restore_adsb() {
  echo "→ restoring ADS-B snapshot history from R2 (~11 GB, slow)"
  mkdir -p "$ADSB_DIR"
  rclone copy "$REMOTE_BASE/adsb-snapshots" "$ADSB_DIR" \
    "${RCLONE_OPTS[@]}" --stats-one-line "${EXTRA[@]}"
}

status() {
  echo "R2: $REMOTE_BASE"
  for p in adsb-snapshots archive-history opensky-mondays crawl-cache extract; do
    printf '  %-16s ' "$p"
    rclone size "$REMOTE_BASE/$p" "${RCLONE_OPTS[@]}" 2>/dev/null \
      | tr '\n' ' ' | sed 's/Total //g' || echo "(empty or unreachable)"
    echo ""
  done
}

case "$cmd" in
  backup-adsb)     backup_adsb ;;
  backup-cache)    backup_cache ;;
  backup-extract)  backup_extract ;;
  backup-history)  backup_history ;;
  backup-all)      backup_adsb; backup_history; backup_cache; backup_extract ;;
  restore-extract) restore_extract ;;
  restore-adsb)    restore_adsb ;;
  restore-history) restore_history ;;
  status)          status ;;
  *)
    sed -n '2,42p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
    exit 1
    ;;
esac
