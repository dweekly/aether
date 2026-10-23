# Hackers 2026: registry ghosts and ADS-B privacy

Stage: MVP (public users for the first time; crude inside is fine if listed in the ROADMAP).
Written 2026-10-23. Panel assumed ~2026-11-06 (date not on the calendar yet; see Open questions).

## Why

David leads an aviation panel at Hackers. The goal is to hand the room a QR code that opens
something they can play with on their phones (data, code and an explorer) while David
presents a story built on it. Two threads, chosen 2026-10-23:

1. **Registry ghosts.** About 315K aircraft are on the FAA registry. Only 44% of them showed
   up on ADS-B in March–August 2026 snapshots: 42% of single-engine pistons, 10% of
   1940s-built ones, 81% of 2020s-built ones. Some of the gap is retired paper, some is
   no-transponder airframes legally flying outside rule airspace, some is coverage, and some
   is privacy addresses. Separating those is the finding.
2. **ADS-B privacy.** Anyone can reconstruct where a GA aircraft lives and when it flies from
   free, openly licensed feeds. The point is what LADD and PIA do and do not hide, shown with
   real numbers.

Naming: everything is public data and the release is fully specific (David, 2026-10-23).

A different solution also serves the intent if it gives the room something live and
reproducible, and if the story's numbers survive a skeptical audience.

## What changed in the approach (2026-10-23)

- **Source data moves to adsb.lol's daily archive** (`github.com/adsblol/globe_history_2023…2026`,
  ODbL 1.0, about 4 GB/day, full per-aircraft traces). Spike on 2026-10-22: 51 s to download,
  4 s to extract, 38,139 US-block aircraft. Same day, our 96 snapshots saw 33,736, and 98.6%
  of those are in the archive, which adds 4,880 aircraft the snapshots never caught. The
  archive also fills the 85-day snapshot hole and goes back to 2023.
- **OpenSky data stays out of the release.** Its terms forbid redistribution even for
  non-commercial use ("You will not distribute, disclose, transfer or otherwise make
  available the data set(s)"). They also require a written agreement for "operational REST
  API use … even for non-profit or governmental entities", which arguably covers our
  15-minute cron.
- **ODbL obligations** (read from the license text, opendatacommons.org/licenses/odbl/1-0/).
  It explicitly allows commercial use (§3.1), so the trigger is public use, not money.
  - Charts, the story, the slides and explorer views are Produced Works. Each needs the
    attribution notice "Contains information from adsb.lol, which is made available here
    under the Open Database License (ODbL)" (§4.3) and stays under whatever license we like
    (§4.5b).
  - The released aircraft-day tables are a Derivative Database in public use. They must be
    released under ODbL (§4.4), with the data or the method that made it offered free
    (§4.6). The public repo's code satisfies the method requirement.
  - The FAA registry (public domain) and our school/fleet extraction ship beside it as
    separate databases in a Collective Database, so share-alike does not reach them (§4.5a).
    We still publish them openly.
  - Code is MIT.

## Work, in order

1. **History ingest (trogdor).** `scripts/30_adsblol_history.py DAY` downloads one day's
   prod-0 release (staging-0 when prod is short), keeps US-block hexes (`a00000`–`afffff`)
   plus any hex whose registration resolves to N-, and reduces each trace to one row per
   aircraft-day: first/last seen, airborne minutes, points by source (adsb/mlat/tisb), max
   altitude, inferred departure/arrival airports, callsigns. Output goes to
   `data/history/YYYY/MM/DD.parquet`. The raw archive is deleted after reduction (it stays on
   GitHub). Backfill 2024-01-01 → today first (~1,030 days, ~4 TB through the pipe, ~1 day
   of download), then 2023 if time allows. A daily cron keeps it current, and its status
   checker alerts on a missing day or an aircraft count more than 30% below the trailing
   median.
2. **Registry join and ghost taxonomy.** Join aircraft-days to the FAA MASTER and DEREG
   tables on Mode S hex. For each registered aircraft: last seen, days active, home airport.
   Every unseen aircraft gets one class, by rule: expired/invalid registration (MASTER
   expiration and status fields), not expected to transmit (glider, balloon, no electrical
   system, pre-1960 type with no ADS-B STC), seen only on MLAT or TIS-B, or unexplained.
   Separately, US-block hexes with no registry match estimate PIA/temporary-address use.
3. **Privacy analysis.** From the same tables: share of aircraft whose home base and weekly
   pattern can be inferred from 30 days of data, the LADD question (adsb.lol does not filter
   LADD), and how many PIA-looking hexes link back to a tail by continuity (same airport,
   same schedule, callsign). Shown in aggregate, plus one worked example on a consenting
   aircraft (see Open questions).
4. **Public data release + code.** A new public repo: ingest, reduce, join and analysis
   code, and a `make sample` that runs on a laptop in minutes. Data goes on a public R2
   bucket (zero egress) as monthly parquet: aircraft-days, registry snapshot, ghost
   classes, schools and fleets with source URLs. Each file carries a DATA_LICENSE (ODbL) and
   attribution (adsb.lol, FAA). `dweekly/aether` stays private: it holds email drafts, the
   PRD and the IP strategy.
5. **Explorer** at aether-flight.com/explore. Static, phone-first, DuckDB-WASM querying the
   R2 parquet: search by N-number, airport or school; activity timeline; map of home bases;
   a ghosts view by type and decade.
6. **Story + talk.** One data story on aether-flight.com ("Registry ghosts"), 6–10 slides
   built from the same charts, and a QR code to the explorer and the repo.
7. **Retire the 15-minute poller** once item 1 passes its gates. That stops both the
   adsb.lol API load and the unlicensed OpenSky use. Existing snapshots stay in R2 as an
   internal cross-check.

## Graduation gates (commands that pass or fail)

- `make history DAY=2026-10-22 && python scripts/check_history_vs_snapshots.py 2026-10-22`:
  the archive covers ≥98% of snapshot-seen US hexes for that day (measured today: 98.6%).
  The negative control is a day from the 85-day hole, where the check must report "no
  snapshots" and not pass vacuously.
- `make history-status` exits 0: every day in the window is ingested or listed in
  `data/history/GAPS.md` with a reason.
- `make ghosts` writes `output/ghosts.json`, and `pytest tests/test_ghosts.py` passes: a
  known-active training aircraft is "seen", a known-deregistered tail is absent from MASTER,
  and the class counts sum to the registry total.
- In the public repo, a fresh clone plus `make sample` finishes in under 5 minutes against
  the published R2 URLs.
- A Playwright smoke test at phone viewport, throttled to 4G: the explorer loads, and an
  N-number search returns in under 3 s.
- One day before the panel, the QR code resolves and the site serves from Cloudflare cache.

## Out of scope

Rate-coverage expansion and new school crawls, CVR/GA-CVR work, any OpenSky redistribution,
commercial data licensing, accounts or anything with a backend.

## Open questions (defaults in force until answered)

- **Panel date.** Assumed 2026-11-06, with code freeze two days earlier.
- **Public repo name.** Default `dweekly/ga-registry-ghosts`.
- **The site's "license this data" CTA** conflicts with a free release. Default: reword it
  to "custom analysis" and keep it.
- **Privacy worked example.** Default: aggregate numbers plus one aircraft whose owner has
  agreed (one of David's own flights, or JATO with April's OK). Re-identifying a PIA user
  without consent is public-data-legal, but it is the exact harm PIA exists to prevent, so
  it should be a deliberate call, not a side effect of "fully specific".
- **AOPA-sourced fields** (school names, contact details) in the release: check AOPA's terms
  before publishing. Fallback: names and websites only, sourced from the schools' own sites.

## Cost

Downloads are free. R2 storage for the release is tens of GB, well under $1/month, and R2
has no egress fees. No Sonnet spend is planned.
