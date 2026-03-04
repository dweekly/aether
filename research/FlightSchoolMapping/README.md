# Flight School Aircraft Mapping

**Question:** Can we build a comprehensive map of every US flight school, the aircraft
on their flight line (by make/model/type), their activity rates, and their contact
information — using publicly available ADS-B telemetry, FAA registration data, and
targeted web scraping?

**Status:** Pipeline built, awaiting execution

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

The pipeline has four phases:

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

### Phase 4: Flight School Enrichment (Web Scraping)

For each candidate flight school entity identified in Phases 1–3:

1. **Web search** — search for "{school name} {airport code} flight school"
2. **Website scrape** — extract from school websites:
   - Physical/mailing address
   - Phone number, email, contact form URL
   - Fleet page (aircraft listed on their site)
   - Ratings offered (PPL, IR, CPL, CFI, etc.)
3. **Cross-validate** — compare website fleet list against our ADS-B/FAA-derived fleet
4. **Directory sources** — scrape/query:
   - AOPA flight school finder
   - Flight School Association of North America (FSANA) directory
   - Google Maps / Yelp business listings for airport addresses

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

| Source | Type | Access | Cost |
|--------|------|--------|------|
| FAA Aircraft Registry | Bulk CSV | Free download | Free |
| FAA NASR Airport Data | Bulk CSV | Free download | Free |
| OurAirports | CSV | Free download | Free |
| ADS-B Exchange | API | API key required | ~$10/mo hobbyist, enterprise pricing varies |
| ADS-B Exchange Historical | Bulk feeds | Requires agreement | Varies |
| AOPA Flight School Finder | Web scrape | Public website | Free (rate-limited) |
| Google Maps API | API | API key | Free tier then pay-per-use |
| Flight school websites | Web scrape | Public | Free (rate-limited) |

## Pipeline Scripts

| Script | Purpose |
|--------|---------|
| `scripts/01_faa_registry.py` | Download and parse FAA aircraft registration database |
| `scripts/02_airport_reference.py` | Build airport reference table from FAA NASR + OurAirports |
| `scripts/03_adsb_activity.py` | Query ADS-B Exchange for aircraft positions and activity |
| `scripts/04_home_base.py` | Determine home airport for each tail number |
| `scripts/05_school_identifier.py` | Identify likely flight schools from fleet clusters |
| `scripts/06_web_enrichment.py` | Scrape flight school websites for contact info and fleet |
| `scripts/07_assemble_dataset.py` | Combine all sources into final output dataset |
| `scripts/run_pipeline.py` | Orchestrate full pipeline end-to-end |

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
pip install requests beautifulsoup4 pandas geopy tqdm
```

Optional for ADS-B Exchange API access:
```bash
export ADSBX_API_KEY="your-api-key-here"
```
