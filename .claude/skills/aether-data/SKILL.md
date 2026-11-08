---
name: aether-data
description: Explains and queries the Aether flight-school and aircraft-activity corpus (aether-flight.com) - the FAA registry joined to a year of adsb.lol ADS-B history, flight-school fleets and posted rental rates scraped from school websites, and the frozen findings. Use when someone asks what is in this dataset, where a file or field lives, how to download or query it, how the tables join, or wants a question answered from it (a tail number, a school, an airport, an aircraft type, rental rates, who owns what, what flies).
---

# The Aether corpus

A public, ODbL-licensed join of four things:
1. The **FAA Releasable Aircraft Registry** (every N-number, owner, type, year).
2. A year of **adsb.lol** ADS-B history, reduced per aircraft per day and per leg.
3. **Flight schools and clubs**, with fleets and rental rates read from each school's own website.
4. The **frozen numbers** behind the published findings.

Data is published by release; the repo holds the code that built it. Query the published files. Don't rebuild from the repo unless the question needs something the release doesn't have.

## Where the data is

- Current release: `curl -s https://data.aether-flight.com/latest.json` → `{"release": "v0.4-20261105-rc5"}`.
- File list with sizes and the observation window: `https://data.aether-flight.com/v/<release>/manifest.json`.
- Every file: `https://data.aether-flight.com/v/<release>/<path>`. Files are public: no account, no key.
- Provenance and licenses: `LICENSES.md` in the release. Human-readable page: <https://aether-flight.com/explore/data/>.

Always read `latest.json` and the manifest first. Quote the release ID and window with any number you give, because numbers change between releases.

| File | Grain | Size (rc5) |
|---|---|---|
| `aircraft.parquet` | one row per N-number on the registry snapshot (318k rows) | 13 MB |
| `aircraft_day/YYYY-MM.parquet` | one row per aircraft per day observed (~0.8M rows a month) | ~45 MB/month |
| `legs/YYYY-MM.parquet` | one row per observed flight leg (~2–3M rows a month) | ~140 MB/month |
| `unmatched.parquet` | US-block ICAO addresses heard on ADS-B but not on the registry | 0.3 MB |
| `schools.json` | flight schools and clubs, each with its fleet (2,911 in rc5) | 12 MB |
| `results.json`, `training.json`, `privacy.json` | frozen findings with definitions | small |

## Schemas (rc5; re-check with `DESCRIBE` on a new release)

**`aircraft.parquet`**
- Registry fields: `n_number` (with the leading N, e.g. `N172SX`), `hex` (ICAO 24-bit, lowercase), `serial_number`, `mfr`, `model`, `seats`, `engines`, `year_mfr`, `aircraft_type`, `engine_type`.
- Ownership: `registrant_type` (Individual, LLC, Corporation, Co-Owned, Government, Partnership, Non Citizen Corporation), `registrant`, `city`, `state`.
- Registration status: `status_code`, `status_label`, `status_valid`, `cert_issue_date`, `expiration_date`, `last_action_date`.
- ADS-B activity over the window: `observed_class`, `days_observed`, `days_airborne`, `first_seen`, `last_seen`, `last_flew`, `airborne_hours`, `legs`, `pia_seen`, `ladd_flag`, `home_airport`, `home_days`, `ground_days`, `home_share`.
- `observed_class`, as defined in `results.json`:
  - `adsb`: heard at least once on its own ADS-B.
  - `other_only`: heard only through MLAT, TIS-B, Mode S or other.
  - `not_observed`: never heard in the window.
- `home_airport`: the most frequent end-of-day ground airport, set only when there are at least 3 days and a share of at least 50%.

**`aircraft_day/*.parquet`**
`day`, `hex`, `reg`, `type`, `desc`, `db_flags`, `pia`, `ladd`, `n_points`, `n_ground`, `n_airborne`, `sources`, `first_ts`, `last_ts`, `airborne_min`, `max_alt_ft`, `n_legs`, `callsigns`, `last_ground_ts`, `last_ground_lat`, `last_ground_lon`, `last_ground_airport`. Timestamps are Unix seconds (UTC).

**`legs/*.parquet`**
`day`, `hex`, `reg`, `leg`, `start_ts`, `end_ts`, `airborne_min`, `max_alt_ft`, `n_points`, `start_lat`, `start_lon`, `end_lat`, `end_lon`, `start_observed_low`, `end_observed_low`, `dep`, `dep_nm`, `dep_ambiguous`, `arr`, `arr_nm`, `arr_ambiguous`, `callsign`. `dep` and `arr` are set only when the aircraft was seen low near an airport, so a null means "not seen landing", not "no airport".

**`unmatched.parquet`**
`hex`, `days_observed`, `days_airborne`, `last_flew`, `first_seen`, `last_seen`, `airborne_min_total`, `legs_total`, `any_adsb`, `any_pia`, `any_ladd`, `archive_reg`, `archive_type`. This is not an estimate of privacy-address use.

**`schools.json`**: `{"days": [...dates...], "schools": [...]}`. Each school has these fields:
- Identity: `key`, `domain`, `name` and `name_source` (where the name came from: `og:site_name`, `title`, `faa_registrant`, `site_verified`, `domain`, `added_list`).
- Location: `airport`, `airport_name`, `city`, `state`, `airports`, `listed_airports`, `inferred_airports` (each `{id, tails, of, listed}`).
- `evidence`: `website` (the fleet came from the school's site) or `registry` (the fleet was found by FAA registrant name).
- Fleet counts: `fleet_listed`, `listings_without_tail`, `fleet_on_registry`, `fleet_flew`, `fleet_hours`, `wet_rate_median`.
- `contact`: name, website, phone, address and city_state, each as `{value, source, ref, retrieved}`.
- `google_place_ids`: IDs only.
- `daily_hours`: pairs `[day_index, hours]`, where the index points into the top-level `days`.
- `fleet[]`, one per aircraft:
  - From the website: `n`, `listed_as`, `wet`, `dry` (USD an hour; wet includes fuel), `airport`, `source_url` (the page the rate was read from), `evidence`.
  - From the registry: `on_registry`, `hex`, `model`, `year`, `registrant`.
  - From ADS-B: `cls` (the same classes as `observed_class`), `days`, `hrs`, `legs`, `last`, `last_flew`, `home`.

## How the tables join

- `hex` joins everything: `aircraft` ↔ `aircraft_day` ↔ `legs` ↔ `schools.fleet[].hex`.
- Tail numbers carry the N prefix everywhere: `aircraft.n_number`, `reg` and `fleet[].n`.
- Airports are ICAO codes such as `KPAO`. `dep`, `arr`, `home_airport` and `last_ground_airport` all use them.

## Querying

DuckDB reads the files straight from HTTPS (`INSTALL httpfs; LOAD httpfs;`). With no DuckDB installed, use `uvx --with duckdb python`.

```sql
-- set once
SET VARIABLE b = 'https://data.aether-flight.com/v/v0.4-20261105-rc5';

-- share of registered aircraft whose registrant type is Government (rc5: 1.81%, 5,751)
SELECT count(*) FILTER (WHERE registrant_type = 'Government') AS gov,
       round(100.0 * gov / count(*), 2) AS pct
FROM read_parquet(getvariable('b') || '/aircraft.parquet');

-- one tail, everything known
SELECT * FROM read_parquet(getvariable('b') || '/aircraft.parquet') WHERE n_number = 'N172SX';

-- arrivals at an airport in a month
SELECT count(*) FROM read_parquet(getvariable('b') || '/legs/2026-10.parquet') WHERE arr = 'KPAO';

-- posted wet rates for Cessna 172s (download schools.json first; rc5: 1,345 rates, 401 schools, median $175)
WITH f AS (SELECT s.key, unnest(s.fleet) AS f
           FROM (SELECT unnest(schools) AS s FROM read_json('schools.json', maximum_object_size = 50000000)))
SELECT count(*) AS rates, count(DISTINCT key) AS schools, median(f.wet)
FROM f WHERE f.wet IS NOT NULL AND f.model ILIKE 'CESSNA 172%';
```

## Traps

- **Globs don't work over HTTP.** `legs/*.parquet` fails. Pass a list instead: `read_parquet(['…/legs/2026-09.parquet', '…/legs/2026-10.parquet'])`. Or download the months you need. A full year of `legs` is about 1.7 GB.
- **"Not observed" is not "not flown."** Coverage depends on volunteer receivers and on equipment. Older aircraft without ADS-B Out show up as `not_observed` or `other_only`. Say "unseen", not "unflown".
- **Rates are as posted.** Each one has its `source_url` and was true on the crawl date. Many schools post no rates. Only 389–401 schools post a 172 rate, out of 2,911 records. Don't treat medians as market-wide.
- **Medians depend on the filter.** The talk's $177 comes from one definition, and the query above gives $175. State the filter you used.
- **Schools here are not all US flight schools.** No public list exists: TSA holds registrations but doesn't publish them. `schools.json` holds the records that met the release's evidence bar.
- **Registry date and observation window differ.** The registry snapshot is from 2026-10-23. ADS-B covers 2025-10-23 → 2026-10-22, with a few days missing (see `included_days` in the manifest).
- **Not in the release:** anything from OpenSky, AOPA's directory or Flightradar24, whose terms forbid redistribution, and Google or Yelp details beyond place IDs.

## When you need more than the release

The pipeline is in `research/FlightSchoolMapping/`:
- **Scripts** are numbered by stage in `scripts/`. `38_schools.py` builds the school records; `42_package_release.py` packages the release.
- **`make sample`** runs the registry and ADS-B join on small licensed fixtures in `sample/`, with the expected answer in `sample/expected.json`.
- **`GOTCHAS.md`** covers the adsb.lol archive's quirks; `NEGATIVE_RESULTS.md` records what didn't work.
- **`docs/`** holds the research write-ups behind each finding, indexed in the root `README.md`.
