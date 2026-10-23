# FlightSchoolMapping

See [README.md](README.md) for full documentation: pipeline overview, build sequence,
output schemas, data sources, and current stats.

## Key conventions

- **Primary key:** normalized domain (strip protocol, www., trailing slash). One entry per domain.
- **Schema field names:** `name` (not `school_name`), `domain` (not `school_website`), `fleet` (not `aircraft`), `wet_rate_hr` (not `hourly_rate`), `faa.mode_s_hex` for ADS-B matching.
- **School names come from AOPA directory**, not Sonnet extraction (Sonnet hallucinates names).
- **Never merge across domains via shared N-numbers** — Sonnet hallucinates N-numbers, union-find on shared NNs caused catastrophic cross-contamination.

## Common operations

**Use the Makefile.** It reruns only what is stale and is safe to rerun any
number of times. Run it from `research/FlightSchoolMapping/` (not `scripts/`).

```bash
make            # aggregate ADS-B → build → PDF overlay. Free, local, ~7 min cold
make status     # what is stale and why (~1s). Do NOT use `make -n` — it over-reports
make refresh    # re-download FAA registry + airports, then rebuild
make crawl      ALLOW_CRAWL=1     # HTTP + browser + expansion crawl (hours)
make extract    ALLOW_SPEND=1     # Sonnet batch extraction (~$25)
make pdf-online ALLOW_SPEND=1     # fetch new rate sheets + Sonnet fallback
make clean      # drop digests only — never deletes pipeline output
```

Staleness is by **content hash**, not mtime, so a `git checkout` or the 15-min
ADS-B cron touching files does not trigger a spurious rebuild. Digests live in
`.build/` (gitignored).

### Pipeline invariants the Makefile depends on

- `21_build_flight_line.py` writes **`flight_line_base.json`**, not
  `flight_line.json`. `22_pdf_rate_sheets.py` reads the base and writes
  `flight_line.json`. Neither reads a file it writes — that is what makes the
  graph acyclic. Do not "simplify" this back into an in-place patch.
- `22` runs with `--offline` in the default build: reuses downloaded PDFs and
  the cached Sonnet batch, so `make` is free and deterministic.
- **`22` caches its two expensive phases** (together they were 6m45s of the old
  10-minute build). Phase 1's target list is keyed on `pdf_targets.key` — a
  digest of the 0-NN domain set plus the crawl-cache listings, deliberately
  *not* `flight_line_base.json`, which churns on every ADS-B fold while the
  0-NN set almost never moves. Phase 3 reuses `rate_sheet_text/{domain}.txt`
  when it is newer than every source it derives from. Both fresh and cached
  paths go through `_summarize_extraction()` so they cannot drift.
- **Extraction artifacts are committed to git and mirrored to R2.** `make` runs
  `restore-inputs` first (`scripts/r2_sync.sh restore-extract`). This matters
  because `21` treats a missing input as "no data" and silently builds a
  smaller dataset instead of failing — see the 08e incident below for what that
  looks like.
- **`08e_faa_registry_discovery.py` is NOT an automatic target.** It reads
  `flight_line.json` to decide what is "not already in the corpus", which makes
  it cyclic with the build. Rerunning it without then crawling what it found
  *shrinks* `faa_discovered_schools.json` and silently drops ~1,400 schools from
  the output. It is `make discover`, run only when you intend to act on it.
- Anything else that reads `flight_line.json` (`14`, `15`, `16`, `17`) is a
  consumer, not a build input. Keep it that way.
- Never add a stage's own output directory to its `*_INPUTS` list — that makes
  it permanently stale. `rate_sheet_text/` is the one that bit us, and
  `adsb_aggregate_state.json.gz` is the same hazard.
- **ADS-B aggregation is incremental.** `11_adsb_dual_source.py --aggregate`
  keeps per-aircraft counters in `output/adsb_aggregate_state.json.gz` (2.9 MB)
  and folds in only unseen snapshots: ~3 s instead of ~2 min. It falls back to a
  full rebuild automatically when the checkpoint is missing, version-stale,
  unreadable, references snapshots no longer on disk, or when a new legacy
  `dual_*` file appears (which would sort before already-folded files and break
  fold-order equivalence). `--rebuild` / `make rebuild-adsb` forces one.
  All accumulators are additive or last-write-wins, and every output ordering is
  sorted by value not insertion order, so an incremental fold is byte-identical
  to a from-scratch rebuild. **If you add a field to the summary, make its
  ordering data-determined or you break that guarantee** — and bump
  `AGG_STATE_VERSION` if the checkpoint shape changes.

## Maintenance

- **Keep README.md up to date** when adding scripts, changing the pipeline, or after significant data changes. Update the status line and Current Results table.
- **API key:** `ANTHROPIC_API_KEY` in `../../.env` (3 levels up from scripts/).
- **ADS-B cron:** `13_adsb_cron.js` (Node port) runs every 15 min via crontab,
  snapshots in `data/adsb/snapshots/`. Collection is automated; **aggregation is
  not** — it happens when you run `make`.
- **venv:** `scripts/.venv` is Linux/py3.12. The old macOS venv is kept at
  `scripts/.venv.macos-dead`; if a script dies with a missing interpreter, that
  is why. Rebuild with `python3 -m venv .venv && .venv/bin/pip install -r
  requirements.txt scipy`.
- **FAA data:** `faa_master.csv` and `ACFTREF.txt` are the *raw* MASTER.txt and
  ACFTREF.txt from the FAA ZIP, with the FAA's original column names (`N-NUMBER`,
  `MFR MDL CODE` — spaces, not underscores). `01_faa_registry.py` exports them
  verbatim; do not substitute its normalized `faa_ga_registry.csv`, which nothing
  downstream reads. Historically these two were extracted by hand, so they went
  stale for months without anyone noticing.
- **FAA BOM:** the hand-made `faa_master.csv` had a double-encoded UTF-8 BOM, so
  `encoding='utf-8-sig'` does NOT work — readers use `text.find("N-NUMBER")` to
  skip it. That approach also handles a clean or single-BOM file, so fresh
  exports are fine.
- **registry.faa.gov is behind a WAF.** It 403s the default `python-requests` UA,
  `curl/*`, and *any* UA containing an `@`. `01_faa_registry.py` sends
  `AetherResearch/1.0 (aviation research)`, verified working 2026-08-05. If a
  refresh starts 403ing, this is the first thing to check.
