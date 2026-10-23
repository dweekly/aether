# Hackers 2026: registry ghosts and ADS-B privacy

Stage: MVP (public users for the first time; crude inside is fine if listed in the ROADMAP).
Written 2026-10-23; revised the same day after one Codex review (gpt-6.1-sol, high).
Panel assumed ~2026-11-06 (not on the calendar yet; see Open questions).

## Why

David leads an aviation panel at Hackers. The goal is to hand the room a QR code that opens
something they can play with on their phones (data, code and an explorer) while David
presents a story built on it. Two threads, chosen 2026-10-23:

1. **Registry ghosts.** In our March–August 2026 snapshots, 44% of FAA-registered aircraft
   were observed on ADS-B: 42% of single-engine pistons, 10% of 1940s-built pistons, 81% of
   2020s-built ones. These are preliminary figures and get recomputed from the archive below.
   The finding is how much of the gap each documented factor accounts for (registration
   status, aircraft category, the FAA's own count of 173,663 ADS-B-equipped US aircraft, and
   coverage), with the rest reported as unexplained, not guessed at.
2. **ADS-B privacy.** How much of where a GA aircraft lives and when it flies can be
   reconstructed from free, openly licensed feeds, and what LADD and PIA do and do not hide.
   This is measured, with accuracy reported alongside coverage.

Naming: the data is public and the release is specific to the tail number (David,
2026-10-23). The one boundary is linking a PIA address back to a tail, which is published
only for a consenting aircraft (see Open questions).

A different solution also serves the intent if it gives the room something live and
reproducible, and if the story's numbers survive a skeptical audience. The biggest risk
(Codex) is publishing confident explanations the data cannot establish, so every claim is
phrased as "observed / not observed by this network in this window".

## The product and the findings

**One URL, three layers.** The QR code opens a landing page.
- **Look up any tail.** Type an N-number: is it on the registry, has it been seen, when
  last, where it lives, how often it flies. People will try their own plane, a friend's,
  a celebrity's. That is the hook, and it is the privacy demo.
- **Explore the fleet.** National and airport views: the registry vs what actually flies,
  by type, decade and state; the busiest training aircraft and airports.
- **Take it home.** ODbL downloads and the public repo with `make sample`.

**The school page is the hero view** (David, 2026-10-23). Look up a school and get one
consistent, structured record:
- **In the air now:** embed adsb.lol's own tar1090 map, filtered to the school's fleet
  (David's idea, 2026-10-23). We relay nothing, and the map loads from adsb.lol's viewer
  data, not the rate-limited `/v2` API.
  - The URL, per tar1090's README-query.md:
    `https://adsb.lol/?icaoFilter=hex1,hex2,…&hideSideBar&hideButtons&zoom=…`
  - The same parameters give the privacy demo: `filterDbFlag=pia,ladd` shows every
    PIA- and LADD-flagged aircraft live.
  - Framing is **unverified**. adsb.lol's docs site sends `X-Frame-Options: SAMEORIGIN`
    (`adsblol/website/layouts/index.headers`), but that may not cover the map, and our IP
    got 403 while over its cap. Test from a browser on another network. If framing is
    blocked, the page shows an "Open live map" link with the same parameters.
  - Not via `/v2/hex` from our page. That API caps each IP
    (`x-ratelimit-limit: 500, 4;w=1, 60;w=60, 500;w=86400`), and a room of phones behind
    one venue NAT would share it. A Worker relay is no fix either: adsb.lol PR #63 notes
    shared Cloudflare egress gets 429'd.
- **Where each plane was last parked:** last ground position and airport from the most
  recent archive day.
- **Operating history:** flights per day and observed airborne hours per tail and month,
  2023-02 → now from the archive. Fleet additions and departures come from the dated
  registry history (2023-08 →).
- **Fleet size and composition:** type, year/age and engine from the FAA registry. The
  school's own advertised fleet is shown beside what the registry and ADS-B show.
- **Cost:** rates from the school's own pages, each with its source URL and capture date.
- **Machine-readable:** every school is also `/schools/{domain}.json` in one schema, which
  is what a later chat interface would query.

**Findings we are aiming for.** Each one is a measurement with a stated test, and gets
replaced by "we couldn't tell" if the data won't support it.
1. **The real size of the active US GA fleet.** Count the registered aircraft that flew at
   least once in the last 12 months, and set it against the FAA's survey-based estimate
   (GA & Part 135 Activity Survey) and its count of 173,663 ADS-B-equipped aircraft.
   Checking a government number against direct observation is the opening slide.
2. **Registry ghosts, with an age.** How many registered aircraft were never observed in
   the window, broken down into documented attributes. The OpenSky Mondays split them into
   "went quiet after 2017–22" and "not seen at all since 2017" (counts only).
3. **Finding flight schools from the sky.** Training aircraft fly a signature: many short
   legs from one airport, pattern circuits, practice-area maneuvering. We detect that from
   the traces and score it against our 11,602 known school tails as labels, reporting
   precision and recall. Then we report the schools the web never told us about.
   Leaseback fleets registered to anonymous LLCs show up here, where registry-name
   matching misses them.
4. **How trackable you are.** For active GA aircraft: share with an inferable home base,
   and its measured accuracy on the consenting sample; share with a predictable weekly
   pattern. What LADD and PIA change and don't. Prior art: Michel & Strohmeier.
5. **What it costs to learn to fly** (the existing C172 story), now joined to utilization:
   the rate per hour against how many hours a day the aircraft actually flies.

**Schools: no re-scrape for this panel.** The 4,825-school / 11,602-tail dataset is
labelled training data for finding 3, not the product. Its gaps get filled by detecting
schools from flight behavior, which also tells us which advertised fleets actually fly.
Before release, school records are rebuilt from the schools' own cached pages (10,326 of
the fleet rows already carry a `source_url`) so that no AOPA field ships. A web re-crawl
for rates stays on the roadmap.

## Workback (panel assumed Fri 2026-11-06)

| Date | Done by then | David's part |
|---|---|---|
| Fri 10-23 | Plan, OpenSky off, archive spike | — |
| Sun 10-25 | Thin slice live: one archive day → public bucket → explorer tail lookup on a phone → public repo `make sample` | Confirm panel date and repo name |
| Tue 10-27 | 60-day privacy window and 2026-03-05 → now ingested; first numbers for findings 1–2; Monday depth running | **Pick the headline** from the first numbers (30 min) |
| Thu 10-29 | Finding 3 (school detection) scored against labels; finding 4 with home-base accuracy | Consenting aircraft and flight dates in hand by **Fri 10-30** |
| Sun 11-01 | Explorer complete (tail, airport, national, schools); downloads and LICENSES.md published | Click through it on your phone; send taste notes |
| Mon 11-02 | Frozen results artifact v1; charts final; story outline and fact bullets | **Write the story and talk words** (Mon–Tue) |
| Wed 11-04 | **Freeze**: release v1 tagged, QR printed, previous release reachable, cache warm | Dry run |
| Thu 11-05 | Offline fallback: screenshots and a short screen recording in case the venue Wi-Fi fails | — |
| Fri 11-06 | Panel | — |

Anything not done by its row slips to the roadmap rather than pushing the freeze.

## Sources and licenses

| Source | Use | License / terms | In the public release? |
|---|---|---|---|
| adsb.lol daily archive (`adsblol/globe_history_2023…2026`) | Every trace, 2023 → now | ODbL 1.0 | Yes. Derived tables are ODbL (§4.4) |
| FAA Releasable Aircraft Registry | Cohort, type, year, status | US government, public domain | Yes, as its own table |
| Daily registry git history (`simonw/scrape-faa-releasable-aircraft`, since 2023-08) | Dated registry snapshots: when tails appear, transfer or vanish | FAA data; repo has no license file | Yes, our derived snapshots |
| Mictronics aircraft-database | Hex → registration and type cross-check | ODC Attribution | Yes, with attribution |
| FAA ADS-B equipage levels; GA & Part 135 Activity Survey | Official denominators (equipped count, hours flown, active share) | US government | Cited figures |
| ADS-B Exchange first-of-month sample days (2016 →) | Independent network, 10-year depth | JETNET terms: derivatives stay inside your organization | No. Internal aggregates only |
| OpenSky scientific datasets (Monday state vectors 2017-06 → 2022-06-27, ~255 days, ~3.8 GB each; pre-mid-2020 dates sit under dot-prefixed `states/.YYYY-MM-DD/` keys) | "Last seen" depth for aircraft not observed since 2023 | Bundled LICENSE.txt: non-profit research only; no redistribution (§3(iii)); "anonymize or de-identify … aircraft identifiers" in any publication (§3(i)); cite and send OpenSky the publication; expunge after the research | No. Aggregate counts only, never per-tail facts in the explorer |
| OpenSky Trino (`flights_data4` since 2016, `state_vectors_data4` since 2013) | Per-flight history: "has this tail flown since 2016?" | Application only: "university-affiliated researchers, governmental organisations, and aviation authorities"; private entities need a license. 100 GB scan cap per query; no bulk extraction | No. Aggregates with citation, if access is granted |
| OpenSky live API (our 15-min cron) | — | Automated use needs a written agreement | Disabled now (see Work item 0) |
| OpenSky REST API, authenticated (client `dweekly-api-client`, credentials in `~/.config/opensky/credentials.json` on monster4, never in a repo) | Ad-hoc research checks: `/flights/aircraft` against a sample of "not observed" tails, and the consenting home-base sample. Standard tier: 4,000 credits/day per endpoint bucket; one aircraft-day costs 4 | Same terms: research use, no redistribution, cite and notify | No. Validation counts only |
| AOPA directory | — | "You may not … copy, distribute … publish … any information" | No. School fields come from schools' own pages |
| Michel & Strohmeier, "Flying in Private Mode" (doi 10.2514/1.I010938) | Prior art on PIA re-identification | Paper | Cited |

ODbL obligations, from the license text: charts, the story, slides and explorer views are
Produced Works and need only the notice "Contains information from adsb.lol, which is made
available here under the Open Database License (ODbL)" (§4.3). Released derived tables are
ODbL, and the complete tables are published, which satisfies §4.6. Commercial use is
explicitly allowed (§3.1), so the trigger is publishing, not money. Each released table gets
a row in `LICENSES.md`: source, license, derivation. Code is MIT.

Spike, 2026-10-22: the day's archive took 51 s to download and 4 s to extract, and holds
38,139 US-block aircraft. It contains 98.6% of the 33,736 US aircraft our 96 snapshots saw
that day, plus 4,880 the snapshots never caught.

## Work, in order

0. **Done 2026-10-23: the 15-minute poller is retired.** The OpenSky fetch was switched off
   first. Then the adsb.lol response headers showed a 500-request/day per-IP cap; the
   poller made ~3,360 a day from the same home IP as monster4, so it was 429ing most of
   every day and blocking our own lookups. The archive already outperforms it, so it went
   ahead of item 1's gates rather than after. The snapshot series ends 2026-10-23 and stays
   in R2.
1. **One day, end to end, deployed first.** This is a thin slice, so delivery problems
   surface early.
   - Ingest one archive day.
   - Reduce it, then join it.
   - Compute the headline numbers.
   - Publish versioned files to a public R2 bucket with a custom domain, CORS and cache
     headers.
   - Ship an explorer page and a credential-free `make sample` in the public repo.
   - Check it all from a phone on a cold cache.
2. **Ingest** (`30_adsblol_history.py DAY`, on trogdor).
   - **Download:** discover every asset of the release (`.aa`/`.ab`/`.ac`…, prod vs staging,
     plus the separate `mlatonly` release), download resumably, validate, and record release
     ID, sizes, hashes and counts.
   - **Two tables per day:**
     - *aircraft-day*: observed points by original source type, and observed airborne
       minutes (only qualified intervals, with a maximum gap, split at midnight).
     - *legs and ground stops*: timestamps, airport candidates with a distance and an
       ambiguity flag, callsign intervals, confidence. Endpoints are marked unknown when
       coverage starts or ends in flight.
   - **Airport matching** uses a spherical nearest-neighbor index, not the existing
     `AirportIndex`, which Codex showed picks the wrong airport. Heliports and seaplane bases
     are included.
   - **Cleanup:** delete the raw archive only after validation.
3. **Windows.** Ingest in this order: the most recent 60 days (the privacy window), then
   2026-03-05 → now (so the preliminary figures can be recomputed), then 2024–2025 in the
   background. In parallel, also in the background: stream all OpenSky Mondays and keep only
   (date, hex, first/last time, airborne point count). That's a few MB, kept internally. It
   splits the ghosts into "last seen 2017–22" and "not seen since 2017", reported as counts
   only.
   - Coverage caveat: single-engine pistons seen in the same 18Z hour went from 1,004
     (2018-06-25) to 1,897 (2022-06-27). Receiver growth makes raw year-over-year
     comparison invalid.
   - Data gap: there is no source for 2022-07 → 2023-02-15.
   2023 itself and ADS-B Exchange sample days are roadmap.
4. **Ghost classification.** It is observational first: *ADS-B observed*, *observed only by
   other surveillance (MLAT/TIS-B)*, *not observed*. Registration status, category, unknown
   year, and documented equipage are separate attributes. The cohort is "aircraft in the
   registry snapshot of DATE whose assigned Mode S address was observed between A and B".
   Ambiguous address histories are flagged, not merged. Every headline comes from one
   frozen results manifest: registry date, observation dates, included days, filters,
   denominator, release ID.
5. **Privacy measures.**
   - **Home base:** evidence thresholds and abstention are fixed in advance, and accuracy is
     reported next to coverage. Validation is against a small consenting sample (David's
     flights, and JATO if April agrees), never against the registry address or a school's
     listed airport.
   - **Weekly pattern:** predicted on held-out days and compared with a naive baseline.
   - **Addresses:** reported as "observed addresses unmatched to the registry", explicitly
     *not* an estimate of PIA use. PIA candidates come only from documented address
     classification.
   - **LADD:** discussed from documented facts. The note that SWIM subscribers must filter
     LADD while open feeds don't is itself a finding.
6. **Release.**
   - A new public repo holds the canonical processing code, with the private pipeline
     calling a pinned version of it.
   - Data goes out through an explicit field/source allowlist, with telemetry fields
     regenerated from the archive. Nothing is copied wholesale from `flight_line.json`.
   - No AOPA code goes into the public repo. `08c_aopa_enrich.py` and the README section on
     it embed AOPA's public web-app client credential. A secret scan (`gitleaks`) runs on
     the public repo before its first push.
   - School fleet membership is labelled separately by evidence: the school's own website
     vs inferred from the registry.
   - `make sample` runs reduction, join and analysis on small licensed fixtures and checks
     an expected result.
7. **Explorer** at aether-flight.com/explore, behind a stable landing page (explorer,
   story, repo, downloads, methodology, release date, short URL under the QR).
   - Searches hit a small search index plus precomputed airport, school and ghost
     summaries. Timeline partitions are fetched on demand.
   - DuckDB-WASM where it helps. A static JSON fallback is acceptable if WASM threatens
     the date.
8. **Story and talk.** A "Registry ghosts" story and 6–10 slides, all reading one frozen
   results artifact, so a daily refresh cannot change the talk's numbers. The C172 story's
   "license this data" CTA becomes a free-download link plus "custom analysis".

## Graduation gates (commands that pass or fail)

- `python scripts/check_history_vs_snapshots.py` over a fixed set of dates (one quiet, one
  busy, one weekend, 2026-10-22) passes only if each date covers ≥98% of snapshot-seen US
  hexes. It reports misses by region and source. A date from the 85-day hole returns
  NO-REFERENCE, which counts as a failure, not a pass.
- `make history-status` exits 0 only if every day in the privacy window is ingested, with
  timestamps spanning the day. Older windows may list gaps in `data/history/GAPS.md`.
- `pytest tests/` fixtures cover:
  - each classification branch;
  - a source switch mid-trace;
  - ground-only observation;
  - a coverage gap;
  - a midnight crossing;
  - an ambiguous address history;
  - the airport matcher against brute-force haversine.
- Fresh clone of the public repo: install plus `make sample` finishes under 5 minutes and
  prints the expected result.
- Explorer cold load and cold N-number search under 3 s at phone viewport on a fixed 4G
  profile, in iOS Safari and Android Chrome. The byte budget is recorded.
- The collector's status checker fires when a failure is forced.
- Release freeze two days before the panel: the QR resolves, data is served from the custom
  domain with cache hits, and the previous release is still reachable.

## Out of scope (ROADMAP)

Causal ghost attribution beyond documented attributes, full historical identity resolution,
2023 and pre-2023 depth, generalized PIA linkage research, rate-coverage and school
expansion, CVR work, commercial licensing, any backend.

## Open questions (defaults in force until answered)

- **Panel date.** Assumed 2026-11-06, with code freeze 2026-11-04.
- **Public repo name.** Default `dweekly/ga-registry-ghosts`.
- **OpenSky Trino access.** Applying needs an eligible affiliation, or a direct request to
  OpenSky as a private researcher. Default: don't apply for this panel. adsb.lol covers 2023
  onward, and the Monday state vectors cover 2017–22.
- **Consenting worked example.** Needed by 2026-10-30. Default: one of David's own flights.
  If none is available, the privacy section ships aggregate-only.

## Review record

Folded in from Codex, all 18 blocking-class points:
- observational taxonomy;
- one results manifest;
- dated cohort;
- unmatched-address naming;
- leg/stop table;
- airborne-minutes definition;
- the airport-matcher bug;
- home-base validation;
- LADD/PIA evidence;
- allowlist release (AOPA terms confirmed restrictive);
- OpenSky off now;
- per-table license matrix;
- asset discovery;
- stronger gates;
- portable `make sample`;
- explorer indexes;
- deploy early;
- thin slice first.

Its roadmap items went to `research/FlightSchoolMapping/ROADMAP.md`. Declined: none.

## Cost

Downloads are free. The R2 public bucket holds tens of GB, under $1/month in storage; reads
are billed, egress is not. Requests per cold search and the size of the first month go on
record after item 1. No Sonnet spend.
