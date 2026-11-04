# Flight School Aircraft Mapping

**Question:** Can we build a comprehensive map of every US flight school, the aircraft
on their flight line (by make/model/type), their activity rates, and their contact
information — using publicly available ADS-B telemetry, FAA registration data, and
targeted web scraping?

**Status (Aug 2, 2026):** 4,825 schools/clubs, 16,881 aircraft, 11,749 unique N-numbers, 8,929 ADS-B active, 4,387 with rental rates. Built by `make` (`21_build_flight_line.py` → `22_pdf_rate_sheets.py`). ADS-B via 6,018 dual-source snapshots (OpenSky + adsb.lol, cron every 15 min), 146,235 unique transponders observed.

Snapshot coverage has a gap from Apr 22 – Jul 17, 2026 (the collector broke when the repo moved from macOS to Linux and the venv's interpreter symlinks went dangling). Per-aircraft `total_sightings` are counted against a flat `n_snapshots` denominator spanning both windows, so activity *rates* derived from them understate aircraft that were only active in one window.

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
   - The cleaner drops elements whose class or id looks like boilerplate (sidebar, widget,
     modal...). Since 2026-11-04 it never drops `<html>` or `<body>`: themes put those words
     on `<body>`, which emptied about 40% of the March HTTP pages. Crawls before that date
     (all of March) carry the loss; Elementor sites are still emptied (ROADMAP)
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

### Phase 4c: Corpus Expansion (`09f_corpus_expansion.py`)

For AOPA flying clubs and university programs not covered in the initial crawl:

- HTTP crawl + Sonnet extraction in a single pass
- Each result embeds its domain in the JSONL record (no positional index needed)
- 1,049 new domains crawled, 3,006 aircraft from 692 schools

### Phase 5: Assembly + Enrichment (`21_build_flight_line.py`)

Deterministic clean-slate rebuild (~60s, no API calls):

- Merges all extraction sources (HTTP, browser, expansion) keyed by normalized domain
- Recovers broken HTTP batch index via source_url parsing, contact matching, fuzzy name
- FAA enrichment: N-number → manufacturer, model, Mode S hex, owner, serial number
- ADS-B cross-reference: Mode S hex → sightings count, home airport
- School names from AOPA directory (not Sonnet) to avoid hallucination

### Phase 5b: PDF Rate Sheet Extraction (`22_pdf_rate_sheets.py`)

For schools with fleet descriptions but 0 N-numbers:

- Scans cached HTML for PDF links, downloads rate sheets
- Regex extraction of N-numbers validated against FAA registry
- Sonnet batch fallback for complex PDFs
- Patches flight_line.json in-place with enrichment

## Output Schemas

### `output/flight_line.json` — Primary Output (4,892 schools: 4,825 from the March build plus 67 added 2026-11-04)

The canonical dataset. Each entry is a school/club keyed by normalized domain,
with fleet, locations, contact info, and ADS-B cross-references.

```jsonc
{
  "name": "Wisconsin Aviation-Dodge County",     // from AOPA directory (not Sonnet)
  "domain": "wisconsinaviation.com",             // normalized domain = primary key
  "aopa_type": "Flight School",                  // "Flight School", "Flying Club", "College/University", or ""
  "aopa_id": "13941",

  "contact": {
    "phone": "920-261-4567",
    "email": "Maintenance@WisAv.com",
    "address": "Watertown Municipal Airport (RYV), Watertown, WI",
    "website": "http://www.wisconsinaviation.com"
  },

  "locations": [
    {
      "airport_id": "KRYV",                     // ICAO identifier
      "airport_name": "Watertown Municipal Airport",
      "label": "Main base / Headquarters"
    }
  ],

  "fleet": [
    {
      "n_number": "N49288",                     // FAA registration, "" if unknown
      "display_name": "Cessna 152",             // make/model as displayed on website
      "description": "Training aircraft",
      "wet_rate_hr": 165.0,                      // $/hr wet rental rate, null if unknown
      "equipment": ["Garmin G1000"],             // avionics/equipment list
      "airport_id": "",                          // specific location if multi-airport school
      "source_url": "https://...",               // page this aircraft was extracted from

      "faa": {                                   // FAA registry enrichment
        "mode_s_hex": "A61B17",                  // ICAO hex for ADS-B matching
        "mfr": "CESSNA",
        "model": "152",
        "type_designator": "2071835",            // MFR MDL CODE
        "year_mfr": "1977",
        "owner": "WISCONSIN AVIATION INC",
        "status": "V",                           // "V"=valid
        "serial_number": "15281226",
        "registrant_city": "WATERTOWN",
        "registrant_state": "WI",
        "type_aircraft": "4",                    // 4=fixed wing single-engine
        "type_engine": "8"
      },

      "adsb": {                                  // ADS-B cross-reference
        "active": true,
        "total_sightings": 21,
        "home_airport": "RYV"
      }
    }
  ],

  "extraction_sources": ["browser", "http_recovered"],  // which pipelines contributed
  "fleet_count": 37,
  "n_number_count": 37,
  "adsb_active_count": 28,

  // Only on schools added late from outside lists (57_added_schools.py): one entry per
  // list row that named it. Such a school's name is the one set by hand from the list.
  "provenance": [
    {"list": "mi_mdot", "record_id": "row38", "list_date": "2026-05",
     "pulled": "2026-11-03", "name_on_list": "Kalamazoo Flight Instruction"}
  ]
}
```

**`aopa_type` values:** `"Flight School"` (1,743), `"Flying Club"` (653),
`"College/University"` (248), `""` (2,181 FAA-discovered entities).

**`extraction_sources` values:** `browser`, `http_recovered`, `expansion`,
`faa_registry`, `pdf_rate_sheet`, `added` (late-added schools, `added_extract.jsonl`).

---

### `output/aopa_flight_schools.json` — AOPA Directory (3,667 entries)

Merged from Coveo search API (`08b_aopa_directory.py`) + Airports REST API enrichment (`08c_aopa_enrich.py`). This is an intermediate file used as input to crawl targeting.

```jsonc
{
  // From Coveo search API (08b)
  "name": "East Bay Flight",
  "city": "Oakland",
  "state": "CA",
  "state_name": "California",
  "airport_id": "OAK",                          // FAA identifier (no K prefix)
  "airport_name": "Oakland San Francisco Bay Airport",
  "latitude": 37.721261,
  "longitude": -122.22115,
  "description": "...",                          // marketing blurb from Coveo
  "aopa_award": "No",                           // "Yes"/"No" — AOPA Distinguished Flight School
  "va_approved": "No",                           // "Yes"/"No" — VA GI Bill approved
  "classifications": "Part 61",                  // "Part 61", "Part 141", or "Part 61; Part 141"
  "certifications": "Commercial; Flight Instructor CFI; ...",   // semicolon-separated
  "ratings": "Instrument",                       // rating offerings
  "services": "Annual Recurrent Training; Biennial Flight Review; ...",
  "advanced_training": "Cirrus; Glass Cockpit Transition; ...",
  "aircraft_rental": "Single Engine",            // rental categories
  "simulator_rental": "",
  "flying_club_members": "",
  "aopa_url": "https://www.aopa.org/destinations/business/40806",  // AOPA detail page
  "source": "aopa",

  // From Airports REST API enrichment (08c)
  "website": "http://www.eastbayflight.com",     // NOT in Coveo — only from Airports API
  "email": "info@eastbayflight.com",
  "phone": "5106863512",
  "fax": "",
  "hours": "",                                   // free-text hours string
  "address1": "8344 Earhart Rd. Suite 1",
  "address2": "",
  "zip": "94621",
  "airport_icao": "KOAK",                        // ICAO identifier from Airports API
  "additional_airports": [],                     // additional ICAO identifiers if multi-airport
  "dba": "",                                     // "doing business as" name
  "description_api": "...",                      // description from Airports API (may differ from Coveo)
  "fte_award_year": "2026",                      // most recent FTE award year
  "instructors_full_time": 5,                    // null if not reported
  "instructors_part_time": 3,
  "contacts": [                                  // published contacts (only okToPublish=true)
    {
      "name": "Patrick Carpenter",
      "title": "Business Manager",
      "phone": "7076530249",
      "email": "info@eastbayflight.com"
    }
  ],
  "structured_hours": [                          // machine-readable business hours
    {
      "days": "Monday-Friday",
      "from": "08:00",
      "to": "17:00"
    }
  ]
}
```

**AOPA API authentication** (public SPA credentials):
```
POST https://authapi.aopa.org/identity/connect/token
  grant_type=client_credentials
  client_id=AOPAAirports
  client_secret=AOPA_PUBLIC_TOKEN_REDACTED
  scope=AirportsAPI

GET https://webapp.aopa.org/AirportsAPI/businesses/{business_id}
  Authorization: Bearer {token}
```
Business ID is extracted from `aopa_url` field: `/business/{id}`. Token valid ~8 hours.

---

### `output/fleet_crawl_index.json` — HTTP Crawl Index (3,169 entries)

One entry per school website crawled by `09a_fleet_crawl.py`. Maps school to discovered pages. Cleaned HTML in `output/fleet_html/`, raw unaltered HTML in `output/fleet_html_raw/`.

```jsonc
{
  "school_name": "ACES Aviation",
  "school_website": "https://www.acesbvi.com",
  "known_airport": "AGC",                       // airport code from directory, if known
  "city": "Beaver Falls",
  "state": "Pennsylvania",
  "crawl_ts": "2026-03-09T...",                  // crawl timestamp
  "canonical_url": "",                           // rel=canonical URL if different from school_website
  "redirect_domain": "",                         // final domain if redirected
  "crawl_flags": [],                             // see flags list below
  "pages": [
    {
      "url": "https://www.acesbvi.com",
      "html_path": "acesbvi.com/index_2a5354e064de.html",  // relative to output/fleet_html/
      "type": "home",                            // "home", "fleet", "about", "contact", "location"
      "images": [                                // extracted from raw HTML before cleaning (optional)
        {
          "src": "https://cdn.example.com/N12345.jpg",
          "alt": "Cessna 172 N12345",
          "title": "",
          "caption": "Our Cessna 172",           // from <figcaption> if in <figure>
          "srcset": "...",                        // responsive image srcset
          "data-src": "..."                       // lazy-load data attributes
        }
      ],
      "json_ld": [                               // JSON-LD structured data (optional)
        {
          "@context": "http://schema.org",
          "@type": "LocalBusiness",
          "name": "ACES Aviation",
          "address": "...",
          "telephone": "..."
        }
      ]
    },
    {
      "url": "https://www.acesbvi.com/our-fleet-rental",
      "html_path": "acesbvi.com/our-fleet-rental_60a090587c5e.html",
      "type": "fleet"
    }
  ]
}
```

---

### `output/fleet_browser_index.json` — Browser Crawl Index (1,510 entries)

Same structure as `fleet_crawl_index.json` but from Playwright headless Chromium (`10_fleet_browser_crawl.py`). Rendered text in `output/fleet_browser_text/`, raw rendered HTML in `output/fleet_browser_html_raw/`.

```jsonc
{
  "school_name": "Alpha One Air Service",
  "school_website": "https://www.alphaoneairservice.com/",
  "known_airport": "KAFP",
  "city": "",
  "state": "North Carolina",
  "canonical_url": "https://alphaoneflighttraining.com/",
  "redirect_domain": "alphaoneflighttraining.com",
  "crawl_flags": ["no_fleet_pages"],
  "pages": [
    {
      "url": "https://www.alphaoneairservice.com/",
      "type": "home",
      "text_path": "alphaoneairservice.com/index_837cd13b.txt",  // relative to output/fleet_browser_text/
      "raw_html_path": "alphaoneairservice.com/index_837cd13b.html",  // raw rendered HTML
      "char_count": 9177,                        // rendered text length (useful for thin-content detection)
      "images": [...],                           // same image metadata format as fleet_crawl_index
      "json_ld": [...]                           // same JSON-LD format as fleet_crawl_index
    }
  ],
  "pdfs": []                                     // list of PDF URLs found and extracted
}
```

**Crawl flags** (both indexes):

| Flag | Meaning |
|------|---------|
| `homepage_failed` | Could not load the homepage (DNS, timeout, 4xx/5xx) |
| `no_fleet_pages` | Homepage loaded but no fleet/aircraft/rental links found |
| `all_pages_thin` | All pages returned <500 chars (likely JS-rendered shell) |
| `js_rendered` | Detected JS framework, content may be incomplete (HTTP crawl only) |
| `rate_limited` | Got 429 or Cloudflare challenge |
| `redirect_loop` | Redirect chain exceeded limit |

---

### `output/flight_schools_enriched.json` — Deduplicated Directory (2,894 schools)

Merged and deduplicated school records from all directory sources. Used as input to crawl targeting. This is the "school universe" before fleet extraction.

```jsonc
{
  "name": "Example Aviation",
  "city": "Palo Alto",
  "state": "CA",
  "state_name": "California",
  "airport_code": "KPAO",
  "airport_name": "Palo Alto Airport",
  "latitude": 37.461,
  "longitude": -122.115,
  "website": "https://www.exampleaviation.com",
  "phone": "(650) 555-0123",
  "email": "info@exampleaviation.com",
  "address": "1901 Embarcadero Rd, Palo Alto, CA 94303",
  "zip": "94303",
  "zip_code": "94303",                           // duplicate of zip (from different sources)
  "ratings_offered": "Private, Instrument, Commercial",
  "detail_url": "https://www.aopa.org/...",
  "source": "aopa",                              // primary source
  "sources": ["aopa", "faa141"],                 // all sources that listed this school
  "n_sources": 2,                                // number of directory sources
  "distance_mi": 0.0,                            // dedup distance to nearest match
  "cirrus_training_center": false,
  "cirrus_relationship": ""
}
```

---

### `output/adsb_activity_summary.json` — ADS-B Fleet Observations (69,576 aircraft)

Per-aircraft aggregation across all ADS-B snapshots. Used by `14_adsb_validate.py` for cross-validation.

```jsonc
{
  "icao_hex": "A61B17",                          // ICAO Mode S hex code
  "total_sightings": 42,                         // total position reports across all snapshots
  "on_ground_count": 12,                         // sightings where aircraft was on ground
  "on_ground_pct": 0.286,                        // fraction on ground
  "home_airport": "KMSN",                        // most frequently observed ground airport
  "home_airport_nights": 3,                      // overnight stays at home airport
  "n_snapshots": 246,                            // total snapshots in dataset
  "overnight_airports": ["KMSN", "KORD"],        // airports with overnight stays
  "airports_seen": {                             // airport -> sighting count
    "KMSN": 18,
    "KORD": 5,
    "KDPA": 3
  }
}
```

---

### `data/adsb/snapshots/snapshot_YYYYMMDD_HHMMSS.json` — ADS-B Snapshots

Point-in-time capture from OpenSky Network + adsb.lol, taken every 15 minutes by cron (`13_adsb_cron.py`).

```jsonc
{
  "capture_utc": "2026-03-09T22:30:00+00:00",
  "total_aircraft": 7399,
  "on_ground": 751,
  "sources": {
    "opensky": 6496,                             // aircraft from OpenSky Network
    "adsblol": 5200,                             // total from adsb.lol (before dedup)
    "adsblol_unique": 903                        // adsb.lol-only (not in OpenSky)
  },
  "aircraft": [
    {
      "icao_hex": "A61B17",                      // ICAO Mode S address (US = A00001-AFFFFF)
      "callsign": "N49288",                      // ADS-B callsign (usually N-number)
      "latitude": 43.14,
      "longitude": -89.34,
      "altitude_m": 457.2,                       // barometric altitude in meters
      "geo_altitude_m": 460.0,                   // geometric altitude (GPS-derived)
      "on_ground": false,
      "velocity_ms": 51.3,                       // ground speed in m/s
      "heading": 270.0,                          // track angle in degrees
      "vertical_rate": -1.5,                     // vertical rate in m/s
      "squawk": "1200",                          // transponder squawk code
      "timestamp": 1741560600,                   // Unix epoch
      "source": "opensky",                       // "opensky" or "adsblol"

      // OpenSky-only fields (null/absent from adsb.lol):
      "origin_country": "United States",
      "time_position": 1741560598,
      "last_contact": 1741560600,
      "sensors": null,
      "spi": false,                              // special position indicator
      "category": 0,                             // ADS-B emitter category

      // adsb.lol-only fields (absent from OpenSky):
      "registration": "N49288",                  // decoded registration
      "aircraft_type": "C152"                    // ICAO type designator
    }
  ]
}
```

---

### `output/fleet_browser_progress.json` — Live Crawl Progress

Written by `10_fleet_browser_crawl.py` during a crawl run. Read by `browser_crawl_dashboard.html` for real-time display.

```jsonc
{
  "updated_at": "2026-03-09T22:32:17+00:00",
  "total": 703,                                  // total schools in this crawl run
  "done": 450,                                   // schools completed
  "schools": [                                   // most recent completions (ring buffer)
    {
      "school": "Tennessee Colleges Of Applied Technology-Memphis",
      "url": "https://tcatmemphis.edu/",
      "status": "done",                          // "done", "flagged", "crawling"
      "ts": "2026-03-09T22:32:17+00:00",
      "pages": 6,                                // pages discovered
      "chars": 28530,                            // total rendered text characters
      "pdfs": 3,                                 // PDFs found and extracted
      "flags": [],                               // crawl flags (see table above)
      "page_links": [{"url": "..."}],            // discovered page URLs
      "pdf_links": [{"url": "..."}]              // discovered PDF URLs
    }
  ]
}
```

---

### `output/night_flying_by_school.json` / `night_flying_by_airport.json`

Night flying analysis output from `12_night_flying.py`. Per-school and per-airport aggregation of night ADS-B activity. Sun position computed per-aircraft from lat/lon at civil twilight (-6°, matching FAR § 1.1).

### `output/cirrus_training_network.json` — Cirrus API Raw Data (141 entries)

Raw response from `locator.cirrusaircraft.com/api/v1/ctc`, ingested by `15_cirrus_ctc_ingest.py`.

### `output/school_aircraft_map.json` — FAA Registrant Name Match

Legacy output from `11_school_aircraft_merge.py`. Strict exact-normalized-name match between school names and FAA registrant names + same state.

### Temporary/working files (prefixed with `_`)

| File | Purpose |
|------|---------|
| `_aopa_crawl_targets.json` | Schools from AOPA enrichment needing HTTP crawl |
| `_aopa_browser_targets.json` | AOPA schools needing browser crawl |
| `_browser_targets.json` | Priority browser crawl target list |
| `_browser_targets_full.json` | Full browser crawl target list (1,510) |
| `_aopa_extract_index.json` | Subset of crawl index for AOPA-sourced extraction |
| `_rate_sheet_targets.json` | Schools with PDF rate sheets to process |
| `_recrawl_targets.json` | Failed crawls to retry |

## Data Sources

All data is from public sources. 15 of 17 sources are free; only the Anthropic API (at least $100; see below) and ADS-B Exchange (~$10/mo, optional) are paid.

### Flight school directories

| Source | URL | Records | Script | Notes |
|--------|-----|---------|--------|-------|
| **AOPA Flight School Finder** | `aopa9nc4vnbc.org.coveo.com/rest/search/v2` | 3,667 | `08b_aopa_directory.py` | Coveo search API. Includes flight schools (2,255), flying clubs (1,139), and universities (270+). 100% have airport IDs, lat/lon, certifications. |
| **AOPA Airports REST API** | `webapp.aopa.org/AirportsAPI/businesses/{id}` | 3,667 | `08c_aopa_enrich.py` | Supplements Coveo with website, email, phone, fax, address, contacts, hours, instructor counts, FTE award. Auth: client_credentials with public SPA keys. |
| **FAA Part 141 Registry** | FAA Safety & Flight Standards data (TSV download) | ~630 | `00_faa141_ingest.py` | Official FAA certificated pilot schools. UTF-16 TSV. Matched to airports via city+state with geocoding fallback. |
| **bestaviation.net** | `bestaviation.net/flight_school/{state}/{city}/` | ~500 | `08_directory_scraper.py` | Web scrape by state/city. |
| **faaflightschools.com** | `faaflightschools.com/{state}/` | ~500 | `08_directory_scraper.py` | Web scrape by state. Largely overlaps with bestaviation. |
| **flightschoollist.com** | `flightschoollist.com/airports/usa/{state}/` | ~400 | `08_directory_scraper.py` | Structured by airport code. |
| **Cirrus Training Network** | `locator.cirrusaircraft.com/api/v1/ctc` | 141 | `15_cirrus_ctc_ingest.py` | Locator API. 52 matched existing schools, 37 new. |

### FAA aircraft data

| Source | URL | Records | Script | Notes |
|--------|-----|---------|--------|-------|
| **FAA Releasable Aircraft Database** | `registry.faa.gov/database/ReleasableAircraft.zip` | 310,353 | `01_faa_registry.py`, `17_faa_enrich_fleet.py` | Bulk ZIP: MASTER.txt (registrations), ACFTREF.txt (type codes), ENGINE.txt. N-numbers, manufacturer, model, year, registrant, Mode S code. Public domain. |
| **FAA registrant address matching** | Same database, cross-referenced | 375 aircraft | `16_faa_address_match.py` | School address → FAA registrant address. Owner-name similarity via SequenceMatcher (threshold ≥0.4). |

### ADS-B telemetry

| Source | URL | Coverage | Script | Auth | Cost |
|--------|-----|----------|--------|------|------|
| **OpenSky Network** | `opensky-network.org/api/states/all` | CONUS + Hawaii + Alaska | `13_adsb_cron.py`, `10_adsb_scheduled.py` | Optional (higher rate limits with credentials) | Free |
| **adsb.lol** | `api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/250` | CONUS (32 tiles) + Hawaii (1) + Alaska (2) | `13_adsb_cron.py` | None | Free |
| **ADS-B Exchange** | `adsbexchange-com1.p.rapidapi.com/v2` | US-wide, per-hex lookup | `03_adsb_activity.py` | RapidAPI key | ~$10/mo hobbyist |

### Airport reference

| Source | URL | Records | Script | Notes |
|--------|-----|---------|--------|-------|
| **OurAirports** | `davidmegginson.github.io/ourairports-data/airports.csv` | 16,210 | `02_airport_reference.py` | Community-maintained. Identifiers, coordinates, type, city, state, elevation. |

### Fleet content extraction

| Source | URL | Volume | Script | Notes |
|--------|-----|--------|--------|-------|
| **Flight school websites** | ~3,500 school domains | 24,728+ pages | `09a_fleet_crawl.py`, `10_fleet_browser_crawl.py`, `09f_corpus_expansion.py` | Async HTTP + Playwright headless Chromium. Rate-limited, respectful headers. |
| **PDF rate sheets** | Linked from school websites | 367 files (135 PDF, 232 HTML) | `22_pdf_rate_sheets.py` | Text extraction via pdfplumber, regex N-number finding + FAA validation. |

### AI/LLM services

| Source | URL | Usage | Script | Auth | Cost |
|--------|-----|-------|--------|------|------|
| **Anthropic Claude API** | `api.anthropic.com/v1/messages/batches` (Sonnet 4.6) | ~5,000 batch requests | `09b_fleet_extract.py`, `09c_browser_extract.py`, `09f_corpus_expansion.py`, `22_pdf_rate_sheets.py` | API key | At least $100: the HTTP and browser passes recorded 55.9M input and 2.2M output tokens, at Sonnet 4.6 batch prices of $1.50/$7.50 per MTok (half the $3/$15 list price). The expansion, PDF and photo passes recorded no usage. `49_scrape_facts.py` recomputes it. |

### Geocoding

| Source | URL | Usage | Script | Notes |
|--------|-----|-------|--------|-------|
| **OpenStreetMap Nominatim** | `nominatim.openstreetmap.org/search` (via geopy) | Address → lat/lon for FAA 141 schools | `00_faa141_ingest.py`, `04_home_base.py` | Free, rate-limited (~1 req/18s). User-agent: `aether-research`. |

## Quick Reference: Common Operations

The pipeline is driven by a **Makefile** that reruns only what is stale and is
safe to rerun any number of times. Run it from `research/FlightSchoolMapping/`.

```bash
make                              # ADS-B aggregate → build → PDF overlay
                                  #   free, local, deterministic (~90s cold, ~1s warm)
make status                       # what is stale and why (~1s)
make refresh                      # re-download FAA registry + airports, then rebuild
make validate                     # build, then cross-validate fleet against ADS-B
make rebuild-adsb                 # force a from-scratch ADS-B aggregation

make backup                       # push snapshots + caches + extracts to R2
make r2-status                    # what is stored offsite

make crawl      ALLOW_CRAWL=1     # HTTP + browser + expansion crawl (hours, free)
make extract    ALLOW_SPEND=1     # Sonnet batch extraction (about $100 for a full run)
make pdf-online ALLOW_SPEND=1     # fetch new rate sheets + Sonnet fallback
make discover                     # propose new FAA-registry crawl candidates
make all        ALLOW_CRAWL=1 ALLOW_SPEND=1   # everything

make clean                        # drop digests only — never deletes output
```

`make` on its own never spends money and never crawls; the crawl and LLM stages
refuse to run without their explicit gate.

### How staleness is decided

By **content hash**, not mtime. Each stage records a digest of its inputs in
`.build/<stage>.inputs`, and the digest file is only replaced when the digest
actually changes. A `git checkout`, an `rsync`, or the 15-minute ADS-B cron
touching the snapshot directory therefore does *not* trigger a rebuild.
Directories (the crawl caches, the 11 GB snapshot tree) are hashed by listing
rather than by reading their contents.

Caveat: `make -n` and `make -q` over-report, because a dry run cannot know a
digest will come out unchanged. Use `make status`.

Stages cache their own expensive work on top of that, so a full rebuild is
~90 s rather than the ~10 min it used to be:

| Stage | Was | Now | How |
|-------|-----|-----|-----|
| ADS-B aggregate | ~2–4 min | ~5 s | checkpointed counters; only new snapshots read |
| 21 assembly | ~80 s | ~80 s | genuine work — parses the 190 MB FAA registry |
| 22 PDF overlay | ~6 min 45 s | ~3 s | target list keyed on crawl-cache digest; extracted PDF text reused when newer than its sources |

### Offsite data (Cloudflare R2)

`scripts/r2_sync.sh` mirrors data to R2 under `aether-research/`, on cron
(snapshots + extracts nightly, crawl caches weekly):

| Prefix | Contents | Why |
|--------|----------|-----|
| `adsb-snapshots/` | 10.8 GB | Irreplaceable — observations cannot be re-fetched |
| `crawl-cache/` | ~10 GB | Re-crawlable in principle, but the web has moved on |
| `extract/` | 27 MB | at least $100 of Sonnet; also committed to git, so R2 is the second copy |

Every build first runs `restore-inputs`, which pulls any missing extract file
from R2. Without it, `21` would quietly
emit a *smaller* dataset rather than fail. `make restore-adsb` pulls the full
snapshot history back.

Note: rclone v1.60 on this box logs spurious `501 NotImplemented` on the first
PUT of each object and succeeds on retry, so `r2_sync.sh` verifies by checksum
rather than trusting exit codes.

### Individual scripts

```bash
cd research/FlightSchoolMapping/scripts
source .venv/bin/activate

python 21_build_flight_line.py          # → flight_line_base.json (~60s)
python 22_pdf_rate_sheets.py --offline  # base → flight_line.json, no network
python 11_adsb_dual_source.py --aggregate   # snapshots → adsb_activity.csv
python 11_adsb_dual_source.py --snapshot    # one ADS-B snapshot, for testing
python 09f_corpus_expansion.py              # crawl + Sonnet on new AOPA domains
```

## Build Sequence

The pipeline produces intermediate artifacts at each stage. `21_build_flight_line.py`
assembles everything from scratch (deterministic, no API calls). `22_pdf_rate_sheets.py`
patches the result with PDF-extracted N-numbers.

```
STAGE 1 — Reference Data (run once, refresh monthly)             [make refresh]
  00_faa141_ingest.py          → faa141_schools.csv
  01_faa_registry.py --force   → faa_master.csv, ACFTREF.txt  (raw, from the ZIP)
                               → faa_ga_registry.csv, faa_school_candidates.csv
  02_airport_reference.py      → airports.csv

  faa_master.csv / ACFTREF.txt are byte-for-byte copies of MASTER.txt and
  ACFTREF.txt out of ReleasableAircraft.zip — they keep the FAA's original
  column names, which is what stages 4 and 4b parse. Without --force, a cached
  ZIP is reused.

STAGE 2 — School Directory Assembly (run once, refresh quarterly)
  08b_aopa_directory.py        → aopa_flight_schools.json (3,667 schools+clubs)
  08c_aopa_enrich.py           → aopa_flight_schools.json (enriched: websites, emails)

STAGE 3a — HTTP Fleet Crawl & Extraction
  09a_fleet_crawl.py           → fleet_crawl_index.json, fleet_html/, fleet_html_raw/
  09b_fleet_extract.py         → fleet_extract_results.jsonl (Sonnet batch)

STAGE 3b — Browser Fleet Crawl & Extraction
  10_fleet_browser_crawl.py    → fleet_browser_text/, fleet_browser_html_raw/
  09c_browser_extract.py       → fleet_browser_extract_results.jsonl + index

STAGE 3c — Corpus Expansion (new domains from AOPA clubs + universities)
  09f_corpus_expansion.py      → expansion_extract.jsonl (crawl + extract, domain embedded)

STAGE 4 — Assembly (deterministic rebuild, no API calls, ~60s)   [make]
  21_build_flight_line.py      → flight_line_base.json, build_diagnostics.json
    Reads: expansion_extract.jsonl, fleet_browser_extract_results.jsonl,
           fleet_extract_results.jsonl, aopa_flight_schools.json,
           faa_discovered_schools.json, faa_master.csv, ACFTREF.txt,
           adsb_activity.csv, and the late-added schools: flight_schools_enriched.json
           entries with a "provenance" list plus added_extract.jsonl (57_added_schools.py).
           Those join after HTTP recovery and their records carry "provenance".

STAGE 4b — PDF Rate Sheet Overlay                                [make]
  22_pdf_rate_sheets.py        → flight_line.json, pdf_extraction_diagnostics.json
    Reads flight_line_base.json and writes flight_line.json — two different
    files, so neither script mutates something it also reads. --offline (the
    default under make) reuses downloaded PDFs and the cached Sonnet batch.

STAGE 5 — ADS-B Observation (collection via cron, every 15 min)
  13_adsb_cron.js              → data/adsb/snapshots/  (crontab, Node, no venv)
  11_adsb_dual_source.py --aggregate → adsb_activity.csv, adsb_activity_summary.json  [make]
  14_adsb_validate.py          → validation report                                    [make validate]

  Collection is automated by cron; aggregation is NOT — it runs when you `make`.

  Aggregation is incremental: per-aircraft counters live in
  output/adsb_aggregate_state.json.gz and only unseen snapshots are read
  (~3 s vs ~2 min for all 6,400). It falls back to a full rebuild by itself
  whenever the checkpoint cannot be trusted; `make rebuild-adsb` forces one.
  An incremental fold is byte-identical to a from-scratch rebuild.
```

**Not in the automatic graph:** `08e_faa_registry_discovery.py` reads
`flight_line.json` to decide which FAA entities are not yet in the corpus, which
makes it cyclic with the build. It is a discovery step, not a build step:
rerunning it without then crawling what it found shrinks
`faa_discovered_schools.json` and silently drops schools. Use `make discover`
only when you intend to act on the results.

**Key design rules for `flight_line.json`:**
- Primary key: normalized domain (strip protocol, www., trailing slash)
- One entry per domain — no cross-domain merging
- School names come from the AOPA directory, NOT from Sonnet extraction
- Each aircraft's `source_url` must match its school's domain (foreign URLs rejected)
- No union-find, no shared-NN merging, no transitive closure
- Image metadata N-numbers are only included if FAA-confirmed
- Platform domains (facebook.com, wixsite.com, etc.) are excluded from domain keying
- The rebuild is idempotent: same inputs always produce the same output

### Script Variants (Not Part of Standard Build)

| Script | Purpose | When to use |
|--------|---------|-------------|
| `09a_fleet_crawl_targeted.py` | HTTP crawl a specific JSON target list | Re-crawl subset of schools |
| `09b_fleet_extract_parallel.py` | Async parallel extraction (sync API) | When batch API is stuck/slow |
| `09b_fleet_extract_chunked.py` | Chunk-based batch submission | Alternative to parallel |
| `09_fleet_scraper.py` | Deprecated: single-phase crawl+extract | Do not use |
| `09_fleet_scraper_parallel.py` | Deprecated: parallel single-phase | Do not use |
| `11_rate_sheet_extract.py` | PDF rate sheet extraction (old) | Superseded by 22_pdf_rate_sheets.py |
| `11_image_nn_extract.py` | Image metadata NN extraction | Now built into 20_rebuild |
| `11_school_aircraft_merge.py` | FAA registrant name matching | Supplementary linkage |
| `12_vlm_nn_extract.py` | VLM photo NN extraction | Per-image API cost, targeted |
| `16_faa_address_match.py` | FAA address matching | Now built into 20_rebuild |
| `17_faa_enrich_fleet.py` | FAA enrichment | Now built into 20_rebuild |
| `15_cirrus_ctc_ingest.py` | Cirrus CTC tags | Future: integrate into 20_rebuild |
| `crawl_progress_monitor.py` | Real-time progress JSON for dashboards | During crawl runs |

### All Scripts

| Script | Purpose |
|--------|---------|
| `00_faa141_ingest.py` | Ingest FAA Part 141 certified schools, match to airports |
| `01_faa_registry.py` | Download and parse FAA aircraft registration database |
| `02_airport_reference.py` | Build airport reference table from FAA NASR + OurAirports |
| `03_adsb_activity.py` | Query ADS-B Exchange for aircraft positions and activity |
| `04_home_base.py` | Determine home airport for each tail number |
| `05_school_identifier.py` | Identify likely flight schools from fleet clusters |
| `06_web_enrichment.py` | Scrape flight school websites for contact info and fleet |
| `07_assemble_dataset.py` | Combine all sources into final output dataset |
| `08_directory_scraper.py` | Scrape bestaviation.net and faaflightschools.com directories |
| `08b_aopa_directory.py` | Fetch AOPA flight school directory via Coveo API (2,255 schools) |
| `08c_aopa_enrich.py` | Enrich AOPA schools via Airports REST API (website, email, contacts) |
| `09a_fleet_crawl.py` | Async HTTP crawl of flight school websites for fleet pages |
| `09b_fleet_extract.py` | Sonnet batch extraction from HTTP crawl HTML → fleet_extract_results.jsonl |
| `09c_browser_extract.py` | Sonnet batch extraction from browser text → fleet_browser_extract_results.jsonl |
| `10_fleet_browser_crawl.py` | Playwright browser crawl for JS-rendered sites → fleet_browser_text/ |
| `10_adsb_scheduled.py` | Scheduled ADS-B data collection |
| `11_image_nn_extract.py` | Extract N-numbers from image metadata (free, no API) |
| `12_vlm_nn_extract.py` | Vision model N-number extraction from aircraft photos |
| `12_night_flying.py` | Night flying analysis from ADS-B data |
| `13_adsb_cron.py` | Continuous ADS-B snapshot collection (OpenSky + adsb.lol) |
| `14_adsb_validate.py` | Cross-validate fleet against ADS-B observations |
| `15_cirrus_ctc_ingest.py` | Ingest Cirrus Training Network API, tag/add CTC schools |
| `16_faa_address_match.py` | Discover fleet via FAA registry address matching |
| `17_faa_enrich_fleet.py` | Enrich fleet with FAA registry data (Mode S hex, etc.) |
| `20_rebuild_flight_line.py` | Predecessor to 21 — HTTP-only rebuild (deprecated) |
| `21_build_flight_line.py` | **Deterministic assembly** of flight_line.json from all extraction artifacts |
| `22_pdf_rate_sheets.py` | Download PDF rate sheets, extract N-numbers via regex + Sonnet batch |
| `09f_corpus_expansion.py` | Crawl + extract new AOPA domains (clubs, universities) |
| `49_scrape_facts.py` | Check the raw model output of 09b/09c against the registry and a release's flight data; cost from recorded usage; exits 1 if the shuffled-airport control fails |
| `demo_extract.py` | Talk demo: one school URL in, its fleet and rates out, each tail checked against a release (`demo_extract.tape` records it with VHS) |
| `57_added_schools.py` | Add schools found on outside lists (FSAI directory, Michigan and Minnesota flight-school licenses, state education lists, SEVP, VA, IPEDS, the unlisted-operations pass) to `flight_schools_enriched.json` with a `provenance` list, from the hand dispositions in `docs/hackers-2026-added-schools.csv`; folds their extractions into `added_extract.jsonl`; `check` and `release-check` gate the rebuild (see `docs/hackers-2026-added-schools.md`) |
| `56_fsai_directory.py` | Parse the FSAI (formerly FSANA) member directory pulled in a browser by `probes/fsai_directory_pull.js`, classify entries and match US training providers against `flight_line.json`; writes `docs/hackers-2026-fsai-directory.md` |
| `59_google_places.py` | Find flight schools with Google Places (New): free IDs-only text searches over self-splitting US tiles, paid details only after a printed estimate under `--max-usd`, matched against `flight_line.json`, unmatched confirmed from each school's own site |

## Aircraft Provenance Model

Every aircraft in `flight_line.json` should carry enough metadata to answer:
**"Why do we believe this aircraft belongs to this school?"**

### Evidence Sources (ranked by confidence)

| Source | Field | Confidence | Description |
|--------|-------|------------|-------------|
| **Website fleet page** | `_extraction_method: "sonnet_text"` | High | School's own fleet/rental page lists the aircraft by N-number, make/model, and rate. Sonnet extracted from crawled HTML. `source_url` points to the specific page. |
| **Website image metadata** | `_extraction_method: "image_metadata"` | Medium-High | N-number found in image filename, alt text, or caption on the school's website. Free extraction, no LLM. FAA-confirmed. |
| **Vision model (photo)** | `_extraction_method: "vlm_photo"` | Medium | N-number read from aircraft photo on the school's website by vision model. May have OCR correction via FAA fuzzy match. |
| **FAA address match** | `_extraction_method: "faa_address"` | Medium | Aircraft registered at the same physical address as the school. `_owner_similarity` score indicates registrant name similarity. |
| **Rate sheet PDF** | `_extraction_method: "rate_sheet"` | High | Aircraft listed in a PDF rate sheet linked from the school's website. |
| **Cirrus CTC API** | `_extraction_method: "cirrus_ctc"` | Medium | Aircraft listed in the Cirrus Training Center locator API. |

### Corroboration Signals

Each aircraft may have additional signals that strengthen or weaken the linkage:

| Signal | Where | Meaning |
|--------|-------|---------|
| `faa.registrant_name` matches school name | `faa` dict | Strong: school owns the aircraft outright |
| `faa.registrant_city/state` matches school location | `faa` dict | Moderate: aircraft is local |
| ADS-B home base matches school airport | `14_adsb_validate.py` | Strong: aircraft is regularly observed at the school's airport |
| N-number on a fleet page with a rate | `wet_rate_hr` not null | Strong: school is actively renting it |
| N-number only in an image filename | `extraction_method: "image_metadata"` | Weak alone: could be a stock photo or old aircraft |

### Provenance Integrity

As of the `20_rebuild_flight_line.py` rebuild, **every aircraft** carries
`_extraction_method` and `_extracted_at` provenance fields. The rebuild script
tags all aircraft at assembly time based on their source:
- `sonnet_text` — extracted by Sonnet from HTTP or browser crawl text
- `image_metadata` — N-number found in image filename/alt/caption (FAA-confirmed)

Additionally, every aircraft's `source_url` is validated against its school's
domain — aircraft with foreign source URLs (from incorrect merges) are rejected.

**TODO:** Backfill `_extraction_method: "sonnet_text"` on all existing
Sonnet-extracted aircraft that lack the field. This is a one-time data
migration on `flight_line.json` — aircraft without `_extraction_method`
and without `_source` are implicitly Sonnet text extractions.

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

## Current Results (as of August 2, 2026)

| Metric | Count |
|--------|------:|
| Schools/clubs in dataset | 4,825 |
| With fleet data | 3,323 |
| Total aircraft entries | 16,881 |
| Unique N-numbers | 11,749 |
| ADS-B active aircraft | 8,929 |
| Aircraft with rental rates | 4,387 |

Sources: 1,743 flight schools, 653 flying clubs, 248 college/university programs (from AOPA), plus 2,181 FAA-discovered entities.

### ADS-B Coverage

626 dual-source snapshots (OpenSky + adsb.lol), every 15 min via cron since Mar 5. CONUS + Hawaii + Alaska coverage. 69,576 unique transponders observed.
| Avg sightings per aircraft | 13.2 (median 10, max 72) |
| Class B airspace coverage | 43/44 (97.7%) |
| Class C airspace coverage | 58/75 (77.3%) |

ADS-B collection is ongoing via cron. Continuous collection over weeks will
validate a larger fraction of the fleet. Only Bravo miss is HNL (no Honolulu
flight school in corpus; Kona and Maui covered).
