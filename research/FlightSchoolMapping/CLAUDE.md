# FlightSchoolMapping

See [README.md](README.md) for full documentation: pipeline overview, build sequence,
output schemas, data sources, and current stats.

## Key conventions

- **Primary key:** normalized domain (strip protocol, www., trailing slash). One entry per domain.
- **Schema field names:** `name` (not `school_name`), `domain` (not `school_website`), `fleet` (not `aircraft`), `wet_rate_hr` (not `hourly_rate`), `faa.mode_s_hex` for ADS-B matching.
- **School names come from AOPA directory**, not Sonnet extraction (Sonnet hallucinates names).
- **Never merge across domains via shared N-numbers** — Sonnet hallucinates N-numbers, union-find on shared NNs caused catastrophic cross-contamination.

## Common operations

```bash
cd scripts && source .venv/bin/activate

# Full rebuild (deterministic, ~60s, no API calls)
python 21_build_flight_line.py

# Apply PDF rate sheet overlay
python 22_pdf_rate_sheets.py

# Fold in latest ADS-B: aggregate → rebuild → overlay
python 11_adsb_dual_source.py --aggregate
python 21_build_flight_line.py
python 22_pdf_rate_sheets.py

# Expand corpus with new domains
python 09f_corpus_expansion.py
```

## Maintenance

- **Keep README.md up to date** when adding scripts, changing the pipeline, or after significant data changes. Update the status line and Current Results table.
- **API key:** `ANTHROPIC_API_KEY` in `../../.env` (3 levels up from scripts/).
- **ADS-B cron:** `13_adsb_cron.py` runs every 15 min, snapshots in `data/adsb/snapshots/`.
- **FAA data:** `faa_master.csv` has a UTF-8 BOM that needs manual stripping (use `text.find("N-NUMBER")` to skip it). `encoding='utf-8-sig'` does NOT work due to double-encoded BOM.
