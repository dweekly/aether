# FlightSchoolMapping — incremental, idempotent pipeline
#
# Staleness is decided by CONTENT, not mtime. Each stage records a digest of
# its inputs in .build/<stage>.inputs; the digest file is only replaced when
# the digest actually changes, so a git checkout, an rsync, or the 15-minute
# ADS-B cron touching the snapshot directory does not trigger a rebuild.
#
# Cost tiers — `make` on its own never spends money and never crawls:
#
#   make             free, local, deterministic. aggregate -> build -> pdf merge
#   make refresh     re-download FAA registry + airport reference (free, ~350 MB)
#   make directory   re-fetch the AOPA school directory (free)
#   make validate    build, then cross-validate the fleet against ADS-B
#   make crawl       HTTP + browser + expansion crawl   (hours; ALLOW_CRAWL=1)
#   make extract     Sonnet batch extraction            ($$; ALLOW_SPEND=1)
#   make pdf-online  fetch new rate sheets + Sonnet     ($$; ALLOW_SPEND=1)
#   make discover    propose new FAA-registry candidates — see note below
#   make all         refresh + crawl + extract + build  (both gates required)
#
#   make status      what is stale, and why
#   make clean       remove digests only — never deletes pipeline output
#
# Offsite data (Cloudflare R2, via scripts/r2_sync.sh):
#
#   make backup      push snapshots + crawl caches + extraction results
#   make r2-status   what is currently stored offsite
#
# The extraction artifacts (*.jsonl and the crawl indexes) are build inputs
# kept only in R2, so every build first runs `restore-inputs`, which pulls
# anything missing locally. That no-ops in seconds when nothing is absent.
#
# Every stage writes via temp-file + atomic rename, and no stage reads a file
# it also writes, so any target can be rerun any number of times safely.
#
# CAVEAT: `make -n` and `make -q` over-report. The digest recipes are driven by
# a FORCE prerequisite, so a dry run has to assume every digest changes and
# will list the whole graph as needing work. Use `make status`, which
# recomputes the digests for real (~1 s), to see what is actually stale.

SHELL := /bin/bash
.DELETE_ON_ERROR:
.SECONDARY:

ROOT      := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
SCRIPTS   := $(ROOT)/scripts
OUT       := $(ROOT)/output
DATA      := $(ROOT)/data
BUILD     := $(ROOT)/.build
SNAPSHOTS := $(DATA)/adsb/snapshots

PY   := $(SCRIPTS)/.venv/bin/python
HASH := $(PY) $(SCRIPTS)/hashinputs.py

ALLOW_SPEND ?= 0
ALLOW_CRAWL ?= 0

.DEFAULT_GOAL := build

# ── Guards ───────────────────────────────────────────────────────────────────

define require_spend
@if [ "$(ALLOW_SPEND)" != "1" ]; then \
  echo ""; \
  echo "  REFUSING: '$@' calls the Anthropic API and costs money (~\$$25 for a full pass)."; \
  echo "  Re-run with:  make $@ ALLOW_SPEND=1"; \
  echo "  (ALLOW_SPEND=1 can be added to any parent target, e.g. 'make extract ALLOW_SPEND=1')"; \
  echo ""; \
  exit 1; \
fi
endef

define require_crawl
@if [ "$(ALLOW_CRAWL)" != "1" ]; then \
  echo ""; \
  echo "  REFUSING: '$@' crawls a few thousand external websites and takes hours."; \
  echo "  Re-run with:  make $@ ALLOW_CRAWL=1"; \
  echo "  (ALLOW_CRAWL=1 can be added to any parent target, e.g. 'make crawl ALLOW_CRAWL=1')"; \
  echo ""; \
  exit 1; \
fi
endef

$(BUILD):
	@mkdir -p $(BUILD)

# ── Content digests ──────────────────────────────────────────────────────────
#
# Recomputed on every invocation, but only rewritten when the digest changes.
# That is what makes the mtime Make sees meaningful.

.PHONY: FORCE
FORCE:

$(BUILD)/%.inputs: FORCE | $(BUILD)
	@$(HASH) $($*_INPUTS) > $@.tmp
	@if cmp -s $@.tmp $@; then rm -f $@.tmp; else mv $@.tmp $@; fi

# ── Stage 1: reference data (network, free) ──────────────────────────────────
#
# No local inputs — these mirror remote sources. Built only when absent;
# `make refresh` forces a re-download.

$(OUT)/faa_master.csv $(OUT)/ACFTREF.txt &:
	$(PY) $(SCRIPTS)/01_faa_registry.py

$(OUT)/airports.csv:
	$(PY) $(SCRIPTS)/02_airport_reference.py

.PHONY: refresh
refresh:
	$(PY) $(SCRIPTS)/01_faa_registry.py --force
	$(PY) $(SCRIPTS)/02_airport_reference.py
	@$(MAKE) --no-print-directory build

# ── Stage 2: school directory (network, free) ────────────────────────────────

.PHONY: directory
directory:
	$(PY) $(SCRIPTS)/08b_aopa_directory.py
	$(PY) $(SCRIPTS)/08c_aopa_enrich.py

# 08e_faa_registry_discovery.py is deliberately NOT an automatic build target.
#
# It reads flight_line.json to decide which FAA entities are "not already in
# our corpus", so the graph would be cyclic:
#
#   flight_line.json -> 08e -> faa_discovered_schools.json
#                           -> 21 -> flight_line_base.json -> 22 -> flight_line.json
#
# It is a discovery step, not a build step: it proposes new crawl candidates
# based on what you already have, and each round finds fewer. Rerunning it
# without then crawling what it found SHRINKS faa_discovered_schools.json and
# silently drops schools from the build. So it is manual, and its output is
# treated as a pipeline SOURCE.
#
# Run it only when you intend to act on the results:
#   make discover   ->  then crawl the candidates, then rebuild.

.PHONY: discover
discover:
	@echo "  NOTE: this overwrites faa_discovered_schools.json, a build input."
	@echo "  It is git-tracked — commit or stash first if you want a way back."
	@echo ""
	$(PY) $(SCRIPTS)/08e_faa_registry_discovery.py

# ── Stage 3: crawl (network, hours, free) ────────────────────────────────────

.PHONY: crawl crawl-http crawl-browser crawl-expansion
crawl: crawl-http crawl-browser crawl-expansion

crawl-http:
	$(require_crawl)
	$(PY) $(SCRIPTS)/09a_fleet_crawl.py

crawl-browser:
	$(require_crawl)
	$(PY) $(SCRIPTS)/10_fleet_browser_crawl.py

crawl-expansion:
	$(require_crawl)
	$(PY) $(SCRIPTS)/09f_corpus_expansion.py --crawl-only

# ── Stage 3x: LLM extraction (network, paid) ─────────────────────────────────

.PHONY: extract extract-http extract-browser extract-expansion
extract: extract-http extract-browser extract-expansion

extract-http:
	$(require_spend)
	$(PY) $(SCRIPTS)/09b_fleet_extract.py

extract-browser:
	$(require_spend)
	$(PY) $(SCRIPTS)/09c_browser_extract.py

extract-expansion:
	$(require_spend)
	$(PY) $(SCRIPTS)/09f_corpus_expansion.py --extract-only

# ── Stage 4: ADS-B aggregation (local, free, seconds) ────────────────────────
#
# The snapshot directory is hashed by listing (name + size), not by reading
# 11 GB of JSON. Cron appending a snapshot changes the listing, which is
# exactly the signal we want.
#
# The aggregate itself is incremental: it keeps per-aircraft counters in
# output/adsb_aggregate_state.json.gz and folds in only snapshots it has not
# seen. Cron adds one every 15 min, so the usual case is a couple of files and
# a few seconds instead of re-reading the whole 11 GB tree.
#
# The checkpoint is an OUTPUT of this stage, so it must never appear in
# adsb_INPUTS — that would make the stage permanently stale.

adsb_INPUTS = $(SNAPSHOTS) $(OUT)/airports.csv $(SCRIPTS)/11_adsb_dual_source.py

$(OUT)/adsb_activity.csv $(OUT)/adsb_activity_summary.json &: $(BUILD)/adsb.inputs | $(OUT)/airports.csv
	$(PY) $(SCRIPTS)/11_adsb_dual_source.py --aggregate

$(BUILD)/adsb.inputs: | $(OUT)/airports.csv

.PHONY: adsb
adsb: $(OUT)/adsb_activity.csv

# Discard the incremental checkpoint and re-read every snapshot (~2 min).
# Only needed if the checkpoint is suspected wrong; the aggregate already
# falls back to a full rebuild on its own when the checkpoint is stale,
# unreadable, or inconsistent with what is on disk.
.PHONY: rebuild-adsb
rebuild-adsb:
	$(PY) $(SCRIPTS)/11_adsb_dual_source.py --aggregate --rebuild
	@$(MAKE) --no-print-directory build

# ── Stage 5: assembly (local, free, ~60 s) ───────────────────────────────────

base_INPUTS = \
  $(SCRIPTS)/21_build_flight_line.py \
  $(OUT)/aopa_flight_schools.json \
  $(OUT)/faa_discovered_schools.json \
  $(OUT)/expansion_extract.jsonl \
  $(OUT)/fleet_browser_extract_results.jsonl \
  $(OUT)/fleet_browser_extract_index.json \
  $(OUT)/fleet_extract_results.jsonl \
  $(OUT)/fleet_crawl_index.json \
  $(OUT)/fleet_browser_index.json \
  $(OUT)/faa_master.csv \
  $(OUT)/ACFTREF.txt \
  $(OUT)/adsb_activity.csv

$(OUT)/flight_line_base.json: $(BUILD)/base.inputs
	$(PY) $(SCRIPTS)/21_build_flight_line.py

$(BUILD)/base.inputs: | restore-inputs $(OUT)/adsb_activity.csv $(OUT)/faa_master.csv $(OUT)/ACFTREF.txt

# The extraction artifacts are build inputs whose only offsite copy is R2.
# 21_build_flight_line.py tolerates a missing input by quietly producing a
# SMALLER dataset rather than failing, so a silent absence here is worse than
# an error — pull anything missing before the digest is taken.
#
# No-ops in seconds when everything is present, so it is safe on every build.
.PHONY: restore-inputs
restore-inputs:
	@$(SCRIPTS)/r2_sync.sh restore-extract

# ── Stage 5b: PDF rate sheet overlay ─────────────────────────────────────────
#
# Default path runs offline: reuses already-downloaded PDFs and the cached
# Sonnet batch, so it is free and deterministic. `make pdf-online` fetches new
# rate sheets and may submit a batch.

# NOTE: rate_sheet_text/ is deliberately absent — stage 22's own extract phase
# rewrites it on every run, so listing it here made the stage permanently stale.
# rate_sheet_pdf/ and the two progress caches ARE listed: the offline path only
# reads them; only `make pdf-online` writes them.
pdf_INPUTS = \
  $(SCRIPTS)/22_pdf_rate_sheets.py \
  $(OUT)/flight_line_base.json \
  $(OUT)/pdf_download_progress.json \
  $(OUT)/pdf_batch_results.json \
  $(OUT)/rate_sheet_pdf \
  $(OUT)/fleet_html_raw \
  $(OUT)/fleet_browser_html_raw \
  $(OUT)/expansion_extract.jsonl \
  $(OUT)/fleet_browser_extract_results.jsonl \
  $(OUT)/fleet_extract_results.jsonl \
  $(OUT)/faa_master.csv \
  $(OUT)/ACFTREF.txt \
  $(OUT)/adsb_activity.csv

$(OUT)/flight_line.json: $(BUILD)/pdf.inputs
	$(PY) $(SCRIPTS)/22_pdf_rate_sheets.py --offline

$(BUILD)/pdf.inputs: | $(OUT)/flight_line_base.json

.PHONY: pdf-online
pdf-online:
	$(require_spend)
	$(PY) $(SCRIPTS)/22_pdf_rate_sheets.py
	@$(MAKE) --no-print-directory build

# ── Top-level ────────────────────────────────────────────────────────────────

.PHONY: build
build: $(OUT)/flight_line.json
	@echo "  flight_line.json up to date"

.PHONY: validate
validate: build
	$(PY) $(SCRIPTS)/14_adsb_validate.py

.PHONY: all
all:
	$(require_crawl)
	$(require_spend)
	@$(MAKE) --no-print-directory refresh
	@$(MAKE) --no-print-directory directory
	@$(MAKE) --no-print-directory crawl ALLOW_CRAWL=1
	@$(MAKE) --no-print-directory extract ALLOW_SPEND=1
	@$(MAKE) --no-print-directory pdf-online ALLOW_SPEND=1

# ── Introspection ────────────────────────────────────────────────────────────

# `make -n` and `make -q` CANNOT answer this correctly: the digest recipes are
# FORCE-driven, so a dry run assumes every digest changes and reports the whole
# graph as stale. Recompute the digests for real and compare — about a second.
define check_stage
@cur=$$($(HASH) $($(1)_INPUTS) 2>/dev/null); \
 old=$$(cat $(BUILD)/$(1).inputs 2>/dev/null || echo none); \
 if [ ! -e "$(2)" ]; then st="MISSING    — will be built"; \
 elif [ "$$cur" != "$$old" ]; then st="STALE      — $(3)"; \
 elif [ "$(2)" -ot "$(BUILD)/$(1).inputs" ]; then st="STALE      — output older than its inputs"; \
 else st="up to date"; fi; \
 printf "  %-24s %s\n" "$$(basename $(2))" "$$st"
endef

.PHONY: status
status:
	@echo ""
	@echo "  Pipeline status"
	@echo "  ---------------"
	$(call check_stage,adsb,$(OUT)/adsb_activity.csv,new or changed ADS-B snapshots)
	$(call check_stage,base,$(OUT)/flight_line_base.json,extraction or reference inputs changed)
	$(call check_stage,pdf,$(OUT)/flight_line.json,base build or rate-sheet cache changed)
	@echo ""
	@echo "  ADS-B snapshots: $$(ls $(SNAPSHOTS) 2>/dev/null | wc -l)"
	@echo "  Note: 'make -n' over-reports — it cannot know a digest is unchanged."
	@echo ""

# ── Offsite backup (Cloudflare R2) ───────────────────────────────────────────

.PHONY: backup backup-adsb backup-cache backup-extract r2-status restore-adsb
backup:
	@$(SCRIPTS)/r2_sync.sh backup-all

backup-adsb:
	@$(SCRIPTS)/r2_sync.sh backup-adsb

backup-cache:
	@$(SCRIPTS)/r2_sync.sh backup-cache

backup-extract:
	@$(SCRIPTS)/r2_sync.sh backup-extract

restore-adsb:
	@$(SCRIPTS)/r2_sync.sh restore-adsb

r2-status:
	@$(SCRIPTS)/r2_sync.sh status

.PHONY: clean
clean:
	@rm -rf $(BUILD)
	@echo "  Removed $(BUILD) — next build re-runs every stage."
	@echo "  (No pipeline output was deleted.)"

.PHONY: help
help:
	@awk '/^#/ { sub(/^# ?/, ""); print; next } { exit }' $(firstword $(MAKEFILE_LIST))
