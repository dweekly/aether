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
#   extract/         output/*.jsonl  ~11 MB. ~$25 of Sonnet plus hours of
#                    crawling. These are BUILD INPUTS and live ONLY in R2 —
#                    they are gitignored, so a fresh clone must restore them
#                    before `make` can produce flight_line.json.
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
#   r2_sync.sh backup-all
#   r2_sync.sh restore-extract    # what `make` calls when the .jsonl are absent
#   r2_sync.sh restore-adsb       # full snapshot history (11 GB — slow)
#   r2_sync.sh status             # what is up there
#
# Add --dry-run to any backup subcommand to see what would transfer.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
OUT_DIR="$PROJECT_DIR/output"
ADSB_DIR="$PROJECT_DIR/data/adsb/snapshots"

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
RCLONE_OPTS=(--s3-no-check-bucket --transfers 8 --checkers 16 --retries 5)

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
  if rclone check "$src" "$dst" --s3-no-check-bucket --checksum \
       --one-way "${@:3}" 2>&1 | grep -qE "ERROR|differences found: [1-9]"; then
    echo "  VERIFY FAILED for $dst — re-run this subcommand" >&2
    return 1
  fi
  echo "  verified OK"
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
  echo "→ extraction results (build inputs — R2 is the only copy)"
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
  for p in adsb-snapshots crawl-cache extract; do
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
  backup-all)      backup_adsb; backup_cache; backup_extract ;;
  restore-extract) restore_extract ;;
  restore-adsb)    restore_adsb ;;
  status)          status ;;
  *)
    sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
    exit 1
    ;;
esac
