# Flight School Aircraft Mapping

**Question:** Can we build a comprehensive map of every US flight school, the aircraft
on their flight line (by make/model/type), their activity rates, and their contact
information — using publicly available ADS-B telemetry, FAA registration data, and
targeted web scraping?

**Status:** Fleet mapping complete for 2,150 deduplicated schools; 1,040 with fleet data (3,459 unique N-numbers, 3,369 FAA-validated)

---

## Motivation

Understanding the GA training fleet landscape is critical for Aether's go-to-market.
If we know which flight schools operate which tail numbers out of which airports with
what activity rates, we can:

1. **Size the market** — total addressable fleet by aircraft type and geography
2. **Prioritize outreach** — target schools with high activity and compatible aircraft
3. **Understand fleet composition** — which makes/models dominate the training fleet
4. **Track fleet health signals** — activity rate as a proxy for utilization and demand

## Approach

The pipeline has six phases:

### Phase 1: FAA Registration Data (Bulk Download)

Source: [FAA Aircraft Registration Database](https://www.faa.gov/licenses_certificates/aircraft_certification/aircraft_registry/releasable_aircraft_download)

- Download the full FAA releasable database (~350K active registrations)
- Parse the MASTER, ACFTREF, and DEREG files
- Extract: N-number, manufacturer, model, type (fixed-wing, rotorcraft, etc.),
  engine type, registrant name, address, city, state, zip
- Filter to GA-relevant types: single-engine piston, multi-engine piston, and
  common training rotorcraft
- Flag registrants whose names suggest flight schools/academies
  (pattern matching on "flight school", "aviation academy", "flying club", etc.)

**Key files:**
- `MASTER.txt` — registration records (N-number → owner/operator)
- `ACFTREF.txt` — aircraft reference (MFR/model/series → type/category/engine)
- `ENGINE.txt` — engine reference data

### Phase 2: ADS-B Position Data (ADS-B Exchange)

Source: [ADS-B Exchange](https://www.adsbexchange.com/) historical data

- Pull historical position data for GA aircraft (filtered by ICAO hex codes
  corresponding to N-numbers from Phase 1)
- For each tail number, determine:
  - **Home airport** — most frequent ground location (using airport coordinate matching)
  - **Activity rate** — flights per month over trailing 6–12 months
  - **Pattern work indicator** — ratio of flights that depart and return to same airport
    (high ratio = likely training aircraft)
  - **Last seen** — recency of activity (filter out inactive/stored aircraft)

The ADS-B Exchange API provides:
- `/v2/hex/{icao_hex}/` — current position by ICAO hex
- Historical data feeds (requires API key for bulk historical access)
- Rapid API endpoint for programmatic access

ICAO hex ↔ N-number conversion uses the FAA-assigned block (A00001–AFFFFF).

### Phase 3: Airport-Based Aggregation

Source: [FAA NASR 28-day data](https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/) and [OurAirports](https://ourairports.com/data/)

- Build an airport reference table (identifier, name, lat/lon, city, state, type)
- For each airport, aggregate:
  - All tail numbers whose ADS-B home base matches that airport
  - Group by registrant/operator
  - Flag clusters of training-type aircraft under the same operator → likely flight school
- Cross-reference with known flight school databases and web presence

### Phase 4: Fleet Page Scraping — HTTP Crawl (09a + 09b)

For each of the ~3,965 flight schools with known websites:

1. **Async HTTP crawl** (`09a_fleet_crawl.py`) — aiohttp-based crawler with three-level
   link discovery:
   - Level 1: Homepage links matching fleet/aircraft/rental keywords
   - Level 2: Parent path discovery from fleet page URLs
   - Level 3: Internal links from fleet pages with path-prefix filtering
   - Saves cleaned HTML to `output/fleet_html/`; outputs `fleet_crawl_index.json`
   - Flags JS-rendered sites returning thin content for browser re-crawl

2. **LLM extraction** (`09b_fleet_extract.py`) — Anthropic Message Batches API
   (Sonnet 4.6) for structured extraction from cached HTML:
   - Extracts N-numbers, aircraft make/model, rates, equipment, contact info
   - Per-aircraft source URLs pointing to best detail page
   - Results: 2,790 schools processed, 938 with fleet data, 5,290 aircraft

### Phase 4b: Fleet Page Scraping — Browser Crawl (10)

For the ~1,985 schools that returned thin/empty content via HTTP (JS-rendered
sites like Squarespace, Wix, React apps):

1. **Playwright browser crawl** (`10_fleet_browser_crawl.py`) — headless Chromium
   renders each site, extracts text from the fully-rendered DOM:
   - 50 parallel browser processes (one per worker, not shared context)
   - Heuristic link discovery from rendered page for fleet/rental/aircraft pages
   - PDF rate sheet detection and text extraction via pdfplumber
   - Rendered text cached to `output/fleet_browser_text/` for re-extraction
   - Live progress dashboard at `output/dashboard.html`

2. **LLM extraction** — same Sonnet 4.6 batch extraction, now with `school_type`
   classification to filter chaff:
   - `flight_school_with_rentals` (844), `flying_club` (45),
     `university_program` (113), `fbo` (64), `defunct` (96), etc.
   - Results: 1,190 additional schools extracted, 680 with fleet data

### Phase 5: FAA Registry Enrichment

Cross-reference extracted N-numbers against FAA ReleasableAircraft database:

- 97.6% of extracted N-numbers match valid, active FAA registrations
- Each aircraft enriched with: serial number, FAA manufacturer/model, year
  manufactured, registrant name/address, Mode S hex code, airworthiness dates,
  engine/aircraft type codes
- Mode S hex codes enable ADS-B cross-referencing

### Phase 6: FAA Registrant Name Matching

For the ~2,930 aircraft identified by make/model but lacking N-numbers, attempt
reverse-lookup against the FAA registry by matching the flight school's business
name to FAA registrant names:

- Normalize both names (strip LLC/Inc/Corp suffixes, punctuation)
- **Strict matching only**: exact normalized name match (no substring/fuzzy)
- Filter to same state as the school's airport
- Match aircraft type (model family) between website listing and FAA registration
- Confidence scoring: "high" when fleet size roughly matches registrant's aircraft
  count in state; "medium" otherwise

Results: 448 N-numbers recovered across 178 schools (250 high confidence,
198 medium confidence). Spot-checked against FAA registry to verify registrant
names, cities, and states align.

### Phase 6b: Rate Sheet PDF Extraction

For 139 schools with `rate_sheet_url` but no fleet data, download and extract
text from PDF rate sheets and pricing pages:

- PDF text extraction via pdfplumber (up to 20 pages)
- HTML rate pages parsed with BeautifulSoup
- Sonnet 4.6 extraction of aircraft make/model, rental rates, equipment
- Most rate sheets list aircraft types and hourly rates but not tail numbers
  (only ~3% of rate sheets include N-numbers)
- Results: 157 additional aircraft from 37 schools, with school_type classification

## Output Schema

The final dataset maps each flight school to its fleet:

```
flight_schools:
  - name: "Example Aviation Academy"
    airport_id: "KPAO"
    airport_name: "Palo Alto Airport"
    city: "Palo Alto"
    state: "CA"
    mailing_address: "1901 Embarcadero Rd, Palo Alto, CA 94303"
    phone: "(650) 555-0123"
    email: "info@exampleaviation.com"
    website: "https://www.exampleaviation.com"
    fleet:
      - n_number: "N12345"
        make: "Cessna"
        model: "172S Skyhawk SP"
        type: "Fixed Wing Single-Engine"
        engine: "Lycoming IO-360-L2A"
        year: 2005
        activity_rate: 4.2  # flights/day trailing 90d
        pattern_work_ratio: 0.72
        last_seen: "2026-02-28"
      - n_number: "N67890"
        make: "Piper"
        model: "PA-28-181 Archer III"
        ...
    fleet_summary:
      total_aircraft: 12
      by_type:
        "Cessna 172": 6
        "Piper PA-28": 3
        "Cessna 152": 2
        "Beechcraft Bonanza": 1
      avg_daily_flights: 18.4
      most_active_tail: "N12345"
```

## Data Sources Summary

### School directories (collected)

| Source | Records | Script | Notes |
|--------|---------|--------|-------|
| **FAA Part 141 Registry** | ~630 locations | `00_faa141_ingest.py` | Official FAA certificated pilot schools (UTF-16 TSV). Matched to airports via city+state with geocoding fallback. |
| **AOPA Flight School Finder** | 2,255 schools | `08b_aopa_directory.py` | Coveo search API. 100% have airport IDs, lat/lon, certifications, ratings, services, aircraft rental types. Most comprehensive single source. |
| **bestaviation.net** | ~500 | `08_directory_scraper.py` | Web scrape by state. |
| **faaflightschools.com** | ~500 | `08_directory_scraper.py` | Web scrape by state. Largely overlaps with bestaviation. |

### Aircraft & ADS-B

| Source | Type | Access | Cost |
|--------|------|--------|------|
| FAA Aircraft Registry | Bulk CSV | Free download | Free |
| FAA NASR Airport Data | Bulk CSV | Free download | Free |
| OurAirports | CSV | Free download | Free |
| ADS-B Exchange | API | API key required | ~$10/mo hobbyist, enterprise pricing varies |
| ADS-B Exchange Historical | Bulk feeds | Requires agreement | Varies |

### Other enrichment

| Source | Type | Access | Cost |
|--------|------|--------|------|
| Google Maps API | API | API key | Free tier then pay-per-use |
| Flight school websites | Web scrape | Public | Free (rate-limited) |

## Pipeline Scripts

| Script | Purpose |
|--------|---------|
| `scripts/00_faa141_ingest.py` | Ingest FAA Part 141 certified schools, match to airports |
| `scripts/01_faa_registry.py` | Download and parse FAA aircraft registration database |
| `scripts/02_airport_reference.py` | Build airport reference table from FAA NASR + OurAirports |
| `scripts/03_adsb_activity.py` | Query ADS-B Exchange for aircraft positions and activity |
| `scripts/04_home_base.py` | Determine home airport for each tail number |
| `scripts/05_school_identifier.py` | Identify likely flight schools from fleet clusters |
| `scripts/06_web_enrichment.py` | Scrape flight school websites for contact info and fleet |
| `scripts/07_assemble_dataset.py` | Combine all sources into final output dataset |
| `scripts/08_directory_scraper.py` | Scrape bestaviation.net and faaflightschools.com directories |
| `scripts/08b_aopa_directory.py` | Fetch AOPA flight school directory via Coveo API (2,255 schools) |
| `scripts/09a_fleet_crawl.py` | Async HTTP crawl of flight school websites for fleet pages |
| `scripts/09b_fleet_extract.py` | Sonnet 4.6 batch extraction of fleet data from cached HTML |
| `scripts/10_fleet_browser_crawl.py` | Playwright browser crawl + Sonnet extraction for JS-rendered sites |
| `scripts/10_adsb_scheduled.py` | Scheduled ADS-B data collection |
| `scripts/11_rate_sheet_extract.py` | Download rate sheet PDFs/pages, extract fleet data with Sonnet |
| `scripts/11_school_aircraft_merge.py` | Merge school directory with fleet/ADS-B data |
| `scripts/12_night_flying.py` | Night flying analysis: solar position calc, day/night classification, per-school night ops |
| `scripts/run_pipeline.py` | Orchestrate full pipeline end-to-end |

## Taking an ADS-B Snapshot

ADS-B snapshots capture a point-in-time view of all US-registered aircraft broadcasting
their position via the OpenSky Network. Each snapshot records ~3,000–5,000 aircraft with
their ICAO hex, position, altitude, ground status, and callsign.

### Quick start: single snapshot

```bash
cd research/FlightSchoolMapping
python scripts/10_adsb_scheduled.py --snapshot
```

This fetches all US airspace from OpenSky, filters to US-registered aircraft (ICAO hex
starting with `A`), and saves a timestamped JSON to `data/adsb/snapshots/`.

### Daytime collection (14-day scheduled)

```bash
python scripts/10_adsb_scheduled.py --start
```

Captures at 10 AM, noon, and 2 PM local time daily for 14 days. Runs as a foreground
process — use `tmux`, `screen`, or `nohup` to keep it alive.

### Night capture

```bash
python scripts/12_night_flying.py --capture
```

Captures at 7 PM, 9 PM, 11 PM, and 1 AM local time for 14 days. Each snapshot is saved
with a `_night` label.

### Processing snapshots after collection

```bash
# Aggregate daytime snapshots into per-aircraft activity summaries
python scripts/10_adsb_scheduled.py --aggregate

# Classify all snapshots (day and night) by solar position, cross-reference with schools
python scripts/12_night_flying.py --analyze

# Print a human-readable night flying report
python scripts/12_night_flying.py --report
```

### Checking status

```bash
python scripts/10_adsb_scheduled.py --status
```

Shows snapshot count, date range, and completion percentage against the 14-day plan.

### Environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `OPENSKY_USER` | No | OpenSky Network username (higher rate limits) |
| `OPENSKY_PASS` | No | OpenSky Network password |
| `ADSBX_API_KEY` | No | ADS-B Exchange RapidAPI key (alternative source) |
| `TZ` | No | Timezone for capture scheduling (default: `America/New_York` for daytime, `America/Los_Angeles` for night) |

### Snapshot file format

Each snapshot is saved as `data/adsb/snapshots/snapshot_YYYYMMDD_HHMMSS[_night].json`:

```json
{
  "capture_utc": "2026-03-05T04:59:21+00:00",
  "total_aircraft": 3412,
  "on_ground": 991,
  "aircraft": [
    {
      "icao_hex": "A12345",
      "callsign": "N12345",
      "latitude": 37.46,
      "longitude": -122.12,
      "altitude_m": 457.2,
      "on_ground": false,
      "velocity_ms": 51.3,
      "heading": 270.0,
      "timestamp": 1741150761
    }
  ]
}
```

### Tips

- **Anonymous access** works but is rate-limited (~5 req/min). Add OpenSky credentials for
  higher throughput.
- **One snapshot ≈ one API call.** The OpenSky `/states/all` endpoint returns all aircraft in
  the US bounding box in a single response.
- **Night determination** is computed per-aircraft from its lat/lon using solar position math
  (civil twilight at -6°, matching FAR § 1.1 "night" definition). No external API needed.
- **Snapshot size** is typically 1–3 MB of JSON per capture.

## Legal & Ethical Notes

- **FAA data** is public domain (government data, no copyright)
- **ADS-B data** is publicly broadcast radio signals; ADS-B Exchange aggregates them
  under their terms of service
- **Web scraping** should respect `robots.txt`, use reasonable rate limits (1 req/sec),
  and identify our user agent
- **Output data** is a derived dataset of publicly available information; no PII beyond
  what registrants voluntarily filed with the FAA or published on their websites
- All scraping scripts include configurable rate limiting and respectful headers

## Prerequisites

```bash
cd scripts
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium  # for browser-based crawl (10_)
```

API keys (in `../../.env` or environment):
```bash
ANTHROPIC_API_KEY="sk-..."   # for Sonnet extraction (09b, 10)
ADSBX_API_KEY="..."          # for ADS-B Exchange API
```

## Current Results (as of March 2026)

| Metric | Count |
|--------|------:|
| Schools in dataset (deduplicated) | 2,150 |
| Schools with fleet data | 1,040 |
| Total aircraft entries | 5,873 |
| Unique N-numbers | 3,459 |
| — FAA-validated (active registration) | 3,369 |
| Aircraft identified by make/model only | 2,414 |
| Aircraft with rental rates | 2,727 |
| Schools with phone contact | 1,367 |
| Duplicate N-numbers across schools | 0 |
| N-number FAA validation rate | 97.4% |

### School Type Classification

| Type | Count |
|------|------:|
| Flight school with rentals | ~843 |
| University/college program | ~114 |
| Defunct/irrelevant | ~96 |
| FBO (no training) | ~63 |
| Flight school (no rentals) | ~63 |
| Flying club | ~45 |
| Other | ~194 |
| Unclassified (from HTTP crawl) | ~730 |

### ADS-B Cross-Validation

Of 3,391 fleet aircraft with Mode S hex codes, 213 (6.3%) were observed in 7 ADS-B
snapshots. Of those observations:

- **75.4%** were within 30nm of the assigned airport (expected for training ops)
- **41.7%** were within 10nm (pattern work / local flights)
- **11.7%** were >100nm away (cross-country training, ferry flights)

The low hit rate reflects limited snapshot coverage (7 captures over 2 days), not
data quality — continuous ADS-B collection over weeks would validate a much larger
fraction of the fleet.
