# Flight School Mapping — Roadmap

## Collection health (found 2026-10-23)

- ~~adsb.lol yield fell ~2/3 from 2026-10-17.~~ Cause: a 500 req/day per-IP cap
  (`x-ratelimit-limit: 500, 4;w=1, 60;w=60, 500;w=86400`) against ~3,360/day from the
  poller. Resolved 2026-10-23 by retiring the poller for the daily archive (see GOTCHAS).
- **Ask adsb.lol for a feeder key or a panel-day allowance** so the school page's live
  layer survives a room of phones behind one NAT. Feeding adsb.lol is the route they
  name for keys.
- **The ADS-B collector has no status checker.** monster4's copy of the cron failed every
  15 minutes from 2026-04-22 to 2026-10-23 (a Homebrew upgrade removed the venv's Python)
  and nothing noticed. trogdor's collector needs a daily reader that alerts on missing
  snapshots or a collapsed per-source count, like `~/forgejo/check-backup-status.sh`.

## Data Expansion

### Rate coverage gap
Only 754 of 4,825 schools (16%) have rental rate data — 4,387 of 16,881 aircraft (26%).
The remaining 74% of the fleet is identified via FAA registry + ADS-B but lacks scraped rates.

**Priority targets:**
- Schools with large ADS-B-active fleets but no rate data (high-value, provably active)
- Schools with PDF rate sheets not yet extracted (Phase 5 pipeline exists but incomplete)
- Re-crawl schools whose websites have changed since initial scrape

### Story ideas
- "Which training fleets are actually flying?" — ADS-B activity rates vs. fleet size
- "The GA training fleet is old" — age distribution, regional differences
- "Flying clubs vs. Part 141 vs. Part 61" — fleet size, rates, aircraft mix by school type
- "Where are the Cirrus trainers?" — geographic distribution of modern vs. legacy fleets

## From the Hackers 2026 plan review (Codex, 2026-10-23)
- `AirportIndex` in `11_adsb_dual_source.py` projects longitude with each point's own
  latitude and then compares in Euclidean space; in a synthetic check it picked the wrong
  nearest airport 18 times in 250. `02_airport_reference.py` also drops heliports and
  seaplane bases. Existing `home_airport` values inherit both.
- `01_faa_registry.py` labels FAA engine code 5 as turboshaft (FAA: 3 = turboshaft,
  5 = turbofan), and `21` stores the FAA model code in a field named `type_designator`.
- Causal ghost attribution (per-aircraft equipage, storage, coverage modelling), full
  historical address-assignment intervals, 2023 and pre-2023 depth (ADS-B Exchange sample
  days, OpenSky Monday state vectors, Trino if granted), generalized PIA linkage.
- `flight_line.json` mixes OpenSky-derived activity with AOPA-sourced fields; neither can be
  redistributed, so it must never be published wholesale.

## After the panel
- Match the FAA Part 141 certificate list (`faa-141-flightschools.csv`, UTF-16, tab-separated)
  to school records, for a "Part 141" flag.
- 2023 archive backfill (adsb.lol starts 2023-02-16) and the 2022-07 → 2023-02 gap, which no
  open source covers.
- PIA linkage research beyond aggregates (Michel & Strohmeier method), aggregate-only.
- Normalize multi-year comparisons for receiver growth: a mid-2024 archive day is ~2.2 GB
  against ~4 GB in late 2026, and OpenSky's GA coverage roughly doubled 2018 → 2022.
- Automate explorer releases: rebuild and deploy after the daily ingest. Deploying from
  trogdor needs a Cloudflare API token scoped to the aether-flight Pages project and the
  aether-public bucket (today wrangler's OAuth login lives only on monster4).
- The live C172 story still shows school names that came from the AOPA directory, whose
  terms forbid republication. The open data no longer carries them; the story should
  switch to names from the schools' own sites.
- Back up `data/history/` (reduced archive tables) to R2; it is re-derivable from
  adsb.lol only as long as adsb.lol keeps its releases.

## Later products
- **Ask the fleet:** a chat interface, Claude skill or MCP server over the public parquet,
  for questions like "how many operational gyrocopters are there in the US?" Registry plus
  aircraft-day tables answer those. "How many heavy jets passed within 3 miles of my house
  yesterday?" also needs a position index (e.g. H3 cell × hour × hex) built during
  reduction, which the aircraft-day/leg tables do not keep.

## Tech Debt
- `output/adsb_activity_summary.json` (55 MB) and `adsb_activity.csv` are committed and
  rewritten on every ADS-B fold, so each rebuild commit adds tens of MB to history. They
  are derived from the snapshots: move them to R2 with the other big blobs, out of git.
- Website deploy path to Cloudflare is not recorded anywhere; write it down or script it.
- monster4 still holds a stale ~18 GB copy of `data/` and `output/` (a checksum-verified
  subset of trogdor's). Safe to delete once trogdor + R2 are the agreed home.
- Stale remote branches `claude/adsb-data-snapshot-P5LM4`, `claude/update-night-flying-ads-2AKrz`,
  `claude/flight-schools-aircraft-mapping-thAqt`: superseded by main except two March 6
  snapshots that only exist on the first one.
- Deduplicate `src/data/` and `public/data/` in website (charts fetch from public/)
- OG images for stories (screenshot a chart)
- Mobile testing pass on all chart components
