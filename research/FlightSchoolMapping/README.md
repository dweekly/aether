# Flight School Aircraft Mapping

**Question:** Can we build a comprehensive map of every US flight school, the aircraft
on their flight line (by make/model/type), their activity rates, and their contact
information — using publicly available ADS-B telemetry, FAA registration data, and
targeted web scraping?

**Status:** Corpus substantially complete — 2,586 schools, 8,932 aircraft, 4,862 unique N-numbers, 678 schools with rental rates. ADS-B cross-validation ongoing (246+ snapshots, CONUS + Hawaii + Alaska coverage).

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

### Phase 5: FAA Registry Enrichment (`17_faa_enrich_fleet.py`)

Cross-reference extracted N-numbers against FAA ReleasableAircraft database:

- 98.3% of extracted N-numbers match valid, active FAA registrations
- Each aircraft enriched with: serial number, FAA manufacturer/model, year
  manufactured, registrant name/address, Mode S hex code, airworthiness dates,
  engine/aircraft type codes
- Mode S hex codes enable ADS-B cross-referencing (3,890 aircraft with hex codes)
- N-number auto-correction catches URL-slug extraction errors and OCR substitutions
  (O/0, I/1, S/5), cross-validated against FAA registry

### Phase 5b: FAA Address Matching (`16_faa_address_match.py`)

Cross-reference school contact addresses against FAA aircraft registry to find
aircraft registered at the same physical address:

- Normalizes addresses (strips suite/unit numbers, collapses whitespace)
- Owner-name similarity scoring via SequenceMatcher (threshold ≥0.4)
- Catches aircraft owned by LLCs at the school's address (common for liability)
- Results: 375 high-confidence aircraft added across 210 schools

### Phase 6: Supplementary Data Sources

**Cirrus Training Network** (`15_cirrus_ctc_ingest.py`) — Fetches Cirrus locator
API (`/api/v1/ctc`), cross-references 141 CTC entries against corpus. Tagged 51
existing schools as Cirrus Training Centers, added 37 new schools.

**FAA Registrant Name Matching** (`11_school_aircraft_merge.py`) — Strict exact
normalized name match + same state + model type. No substring/fuzzy matching.

**Rate Sheet PDF Extraction** (`11_rate_sheet_extract.py`) — PDF text extraction
via pdfplumber, HTML rate pages parsed with BeautifulSoup, Sonnet 4.6 extraction.

## Output Schemas

### `output/flight_line.json` — Primary Output (2,586 schools)

The canonical dataset. Each entry is a flight school with its fleet, locations, and contact info, as extracted by Sonnet 4.6 from crawled web content.

```jsonc
{
  "school_name": "Wisconsin Aviation-Madison",
  "school_website": "https://www.wisconsinaviation.com/",
  "school_type": "flight_school_with_rentals",  // see enum below
  "source": "browser_crawl",                    // "browser_crawl" or "" (HTTP crawl)
  "scrape_ts": "2026-03-09T...",                // extraction timestamp

  // Best fleet/rate page found during crawl
  "fleet_page_url": "https://www.wisconsinaviation.com/flight-school-rentals/flight-training",
  "rate_sheet_url": "",                          // PDF or HTML rate sheet URL
  "rate_sheet_extracted": false,                 // true if rate sheet was processed

  "contact": {
    "phone": "608-268-5024",
    "email": "FlightSchool@WisAv.com",
    "address": "Dane County Regional Airport, Madison, WI"
  },

  "locations": [                                 // one or more airport locations
    {
      "airport_id": "KMSN",                     // ICAO or FAA identifier
      "airport_name": "Dane County Regional Airport",
      "label": "Madison",                        // human label: "Main base", "Satellite", etc.
      "address": "",                             // optional street address
      "_flag": ""                                // optional: "unverified_airport_id"
    }
  ],

  // Cirrus Training Center fields (present only when applicable)
  "cirrus_training_center": true,
  "cirrus_relationship": "Cirrus Training Center",

  "fleet": [
    {
      "n_number": "N49288",                     // FAA registration, e.g. "N12345" or "" if unknown
      "display_name": "Cessna 152",             // make/model as displayed on website
      "description": "",                         // free-text description from website
      "wet_rate_hr": 165.0,                      // $/hr wet rental rate, null if unknown
      "dry_rate_hr": null,                       // $/hr dry rental rate, null if unknown
      "equipment": ["Garmin G1000", "IFR certified"],  // avionics/equipment list
      "airport_id": "",                          // specific airport if multi-location school
      "source_url": "https://...",               // page this aircraft was extracted from
      "_source": "",                             // "faa_address_match" if from FAA address matching, else ""
      "_owner_similarity": 0.85,                 // FAA address match confidence (0-1), only if _source="faa_address_match"
      "_flags": [],                              // extraction flags, e.g. ["n_number_corrected"]
      "_extraction_note": "",                    // LLM note about extraction confidence

      "faa": {                                   // FAA registry enrichment (from 17_faa_enrich_fleet.py)
        "serial_number": "15281226",
        "faa_mfr": "CESSNA",                    // official FAA manufacturer name
        "faa_model": "152",                      // official FAA model designation
        "mfr_mdl_code": "2072403",               // FAA manufacturer/model code
        "year_mfr": "1977",
        "registrant_name": "WISCONSIN AVIATION INC",
        "registrant_city": "WATERTOWN",
        "registrant_state": "WI",
        "airworthiness_date": "19780119",        // YYYYMMDD
        "cert_issue_date": "19851101",
        "expiration_date": "20281031",
        "mode_s_hex": "A61B17",                  // ICAO hex for ADS-B tracking
        "type_aircraft": "4",                    // FAA type code (4=fixed wing single-engine)
        "type_engine": "8",                      // FAA engine code
        "status_code": "V"                       // "V"=valid, "N"=non-citizen corp, etc.
      }
    }
  ]
}
```

**`school_type` enum values:**

| Value | Count | Description |
|-------|------:|-------------|
| `flight_school_with_rentals` | 1,254 | School with aircraft available for rent |
| `flight_school_no_rentals` | 367 | Instruction only, no rental fleet |
| `defunct` | 228 | Closed, unreachable, or no longer operating |
| *(empty)* | 203 | Not yet classified (HTTP crawl, pre-browser) |
| `other` | 171 | Doesn't fit other categories |
| `university_program` | 170 | Collegiate aviation program |
| `fbo` | 81 | Fixed-base operator with some training |
| `flying_club` | 75 | Member-owned club |
| `maintenance_shop` | 20 | Primarily maintenance, some training |
| `charter_operator` | 17 | Charter with incidental training |

---

### `output/aopa_flight_schools.json` — AOPA Directory (2,255 schools)

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

### `output/adsb_activity_summary.json` — ADS-B Fleet Observations (52,071 aircraft)

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

All data is from public sources. 15 of 17 sources are free; only the Anthropic API (~$15 total) and ADS-B Exchange (~$10/mo, optional) are paid.

### Flight school directories

| Source | URL | Records | Script | Notes |
|--------|-----|---------|--------|-------|
| **AOPA Flight School Finder** | `aopa9nc4vnbc.org.coveo.com/rest/search/v2` | 2,255 | `08b_aopa_directory.py` | Coveo search API. 100% have airport IDs, lat/lon, certifications. Most comprehensive single source. Does NOT include website/email (use Airports API). |
| **AOPA Airports REST API** | `webapp.aopa.org/AirportsAPI/businesses/{id}` | 2,255 | `08c_aopa_enrich.py` | Supplements Coveo with website, email, phone, fax, address, contacts, hours, instructor counts, FTE award. Auth: client_credentials with public SPA keys. |
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
| **Flight school websites** | ~2,200 school domains | 24,728 pages | `09a_fleet_crawl.py`, `10_fleet_browser_crawl.py` | Async HTTP (aiohttp, 50 concurrent) + Playwright headless Chromium with stealth mode for JS-rendered and bot-protected sites. Rate-limited, respectful headers, robots.txt compliance. |
| **PDF rate sheets** | Linked from school websites | ~200 PDFs | `10_fleet_browser_crawl.py`, `11_rate_sheet_extract.py` | Text extraction via pdfplumber. Max 3 PDFs per school. |

### AI/LLM services

| Source | URL | Usage | Script | Auth | Cost |
|--------|-----|-------|--------|------|------|
| **Anthropic Claude API** | `api.anthropic.com/v1/messages/batches` (Sonnet 4.6) | ~3,000 batch requests | `09b_fleet_extract.py`, `10_fleet_browser_crawl.py`, `11_rate_sheet_extract.py` | API key | ~$15 total (batch pricing: $1.50/M input, $7.50/M output) |

### Geocoding

| Source | URL | Usage | Script | Notes |
|--------|-----|-------|--------|-------|
| **OpenStreetMap Nominatim** | `nominatim.openstreetmap.org/search` (via geopy) | Address → lat/lon for FAA 141 schools | `00_faa141_ingest.py`, `04_home_base.py` | Free, rate-limited (~1 req/18s). User-agent: `aether-research`. |

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
| `scripts/08c_aopa_enrich.py` | Enrich AOPA schools via Airports REST API (website, email, contacts) |
| `scripts/09a_fleet_crawl.py` | Async HTTP crawl of flight school websites for fleet pages |
| `scripts/09a_fleet_crawl_targeted.py` | Wrapper for 09a that accepts custom JSON target list |
| `scripts/09b_fleet_extract.py` | Sonnet 4.6 batch extraction of fleet data from cached HTML |
| `scripts/10_fleet_browser_crawl.py` | Playwright browser crawl + Sonnet extraction for JS-rendered sites |
| `scripts/10_adsb_scheduled.py` | Scheduled ADS-B data collection |
| `scripts/11_rate_sheet_extract.py` | Download rate sheet PDFs/pages, extract fleet data with Sonnet |
| `scripts/11_school_aircraft_merge.py` | Merge school directory with fleet/ADS-B data |
| `scripts/12_night_flying.py` | Night flying analysis: solar position calc, day/night classification, per-school night ops |
| `scripts/13_adsb_cron.py` | Continuous ADS-B snapshot collection (OpenSky + adsb.lol) |
| `scripts/14_adsb_validate.py` | Cross-validate fleet against ADS-B observations |
| `scripts/15_cirrus_ctc_ingest.py` | Ingest Cirrus Training Network API, tag/add CTC schools |
| `scripts/16_faa_address_match.py` | Discover fleet via FAA registry address matching |
| `scripts/17_faa_enrich_fleet.py` | Enrich fleet with FAA registry data (Mode S hex, manufacturer, etc.) |
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

## Current Results (as of March 9, 2026)

| Metric | Count |
|--------|------:|
| Schools in dataset (deduplicated) | 2,586 |
| Schools with fleet data | 1,552 |
| Total aircraft entries | 8,932 |
| Unique N-numbers | 4,862 |
| Schools with rental rates | 678 |
| Cirrus Training Centers tagged | 52 |

### School Type Classification

| Type | Count |
|------|------:|
| Flight school with rentals | 1,254 |
| Flight school (no rentals) | 367 |
| Defunct / unreachable | 228 |
| Not yet classified | 203 |
| Other | 171 |
| University/college program | 170 |
| Flying club | 75 |
| FBO | 81 |
| Maintenance shop | 20 |
| Charter operator | 17 |

### ADS-B Cross-Validation

246 snapshots from OpenSky Network + adsb.lol, every 15 min since Mar 5. CONUS + Hawaii + Alaska coverage (Hawaii/Alaska added Mar 9).

| Metric | Value |
|--------|------:|
| Fleet with Mode S hex | 3,938 |
| Observed in ADS-B | 1,891 (48.0%) |
| Within 30nm of home airport | 83.7% |
| Avg sightings per aircraft | 13.2 (median 10, max 72) |
| Class B airspace coverage | 43/44 (97.7%) |
| Class C airspace coverage | 58/75 (77.3%) |

ADS-B collection is ongoing via cron. Continuous collection over weeks will
validate a larger fraction of the fleet. Only Bravo miss is HNL (no Honolulu
flight school in corpus; Kona and Maui covered).
