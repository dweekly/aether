# Hackers 2026: flight schools added from outside lists

Written 2026-11-04. Stage: prototype work inside an MVP release (`docs/hackers-2026-plan.md`).
Every candidate's disposition is in [`hackers-2026-added-schools.csv`](hackers-2026-added-schools.csv).
The script is `research/FlightSchoolMapping/scripts/57_added_schools.py`.

> **Headline.** 178 candidates from three sources came down to **67 schools added** to
> `flight_line.json`: 4,825 records became 4,892, and no existing record changed. 43 of the 67
> have a website. 23 have a fleet or price list on that site, so they get explorer pages: the
> staged release has **2,493 school pages, up from 2,470**. The other 44 have no website (24
> schools licensed by Michigan or Minnesota that we found no site for) or a site that lists no
> aircraft. They are in the data but have no explorer page. Extraction cost **$0.14**.
> Nothing is published: the candidate is staged on trogdor.

> **Correction to a working assumption.** The talk doc says the FSAI directory yields "22 live
> flight-training websites we didn't have (... FTI in Denver among them)". After checking, the
> number is 20. FTI (ftiratings.com) sells ATP-CTP and Boeing/Airbus type ratings in
> simulators. Orient Flight School is already in our list: our "Pilot Training Center Homestead"
> (pilottrainingcenterhomestead.com) has the same street address, and its site now calls itself
> Orient Flight School. The "about 40" schools that Michigan and Minnesota would add becomes 39,
> 15 of them with a website. The talk doc's numbers were left unedited, as asked.

## Why

The talk compares our school list with every outside list we could find. Each of those
comparisons turned up real schools we did not have. Adding them, with a record of where each
came from, makes the explorer more complete before the room tries it. It also tests whether
the pipeline can take late additions without disturbing the paid March extractions. Another
approach would serve the same goal if it adds only verified schools, keeps provenance per
school, and leaves every existing record byte-identical.

> **Followed by.** Release candidate rc2 (`v0.4-20261104-rc2`) adds 566 schools found through Google
> Places on top of these 67, and gives every record per-field provenance, contact details from
> permitted sources and Google place IDs: [`hackers-2026-google-schools.md`](hackers-2026-google-schools.md).
> Its publish commands supersede the rc1 ones below.

## Sources and candidates

| Source | List | Candidates | Schools added | with a website | Duplicates | Rejected |
|---|---|---:|---:|---:|---:|---:|
| FSAI member directory | `fsai_directory` (pulled 2026-11-04): the 22 hand-labelled schools whose homepage showed training text | 22 | 20 | 20 | 1 | 1 |
| State | `mi_mdot`: Michigan MDOT licensed flight schools (May 2026), unmatched rows | 30 | 23 | 8 | 5 | 0 |
| State | `mn_mndot`: Minnesota MnDOT flight-school licenses (list dated 2024-06-04), unmatched rows | 19 | 16 | 7 | 0 | 1 |
| State | `state_edu`: state education-board lists (AK, AL, DE, KY, NM, PA, SC, NJ, DC, ID), unmatched aviation rows | 19 | 2 | 2 | 9 | 8 |
| Federal | `sevp`: ICE SEVP flight schools (2026-10-28), schools with no matched campus | 22 | 2 | 2 | 11 | 9 |
| Federal | `va_weams`: VA-approved flight facilities (2026-10-08), unmatched | 51 | 0 | 0 | 28 | 23 |
| Federal | `ipeds`: colleges with 49.0102/49.0108 awards (2024), unmatched | 2 | 0 | 0 | 0 | 2 |
| Unlisted operations | `unlisted_registry`: confirmed schools and clubs whose site was on no March list (`55_unlisted_schools.py`) | 13 | 5 | 5 | 8 | 0 |
| **All** | | **178** | **67 distinct** | **43** | **62** | **44** |

Counts are rows except "Schools added", which counts distinct schools. One school appears on
two lists: Alaska Flight Academy, in both FSAI and Alaska's ACPE list. Michigan lists Three
Point Aviation and Hall's Flying Service twice each (two airports), and Minnesota lists Janson
Flying Service three times.

## Method

1. **Candidates** (`57 candidates`). FSAI comes from `56_fsai_directory.py`'s outputs and
   `docs/hackers-2026-fsai-labels.csv`. The unlisted operations come from
   `data/research-2026-11/unlisted/scores.json`. The state and federal rows come from the
   CSVs reduced on 2026-11-03 for `docs/hackers-2026-state-registries.md`, from downloads kept in
   their own directory and treated as untrusted. Before using them, I re-ran
   `scripts/probes/match_external_school_lists.py` on them with `python -I`. It reproduced the
   counts printed in that doc exactly (`c_mi_mdot 75 {none 30, domain 21, exact 22, fuzzy 2}`,
   `c_mn_mndot 56 {domain 28, exact 7, none 19, fuzzy 2}`, `c_sevp 511 ...`, `c_va 519 ...`,
   `c_ipeds 221 ...`, `c_state_edu 63 ...`). Every unmatched or merely colocated row became a
   candidate. For SEVP, a school counts only if none of its campuses matched. For VA, only
   currently approved facilities count. For IPEDS, only 49.0102/49.0108 colleges count.
2. **Checks.** `57 sitecheck` fetched each candidate website and each proposed school website
   once, using 56's homepage check (a plain request with a research user agent).
   Challenges and refusals were recorded as blocked and not circumvented. For 47 state and
   federal rows with no website or an unclear one, a subagent ran web searches (results
   recorded in the `detail` column). I re-checked its claims where they decided a disposition:
   its claim that Grey Eagle Aviation is not on the flight-school list was wrong, because the
   MnDOT PDF lists it under Flight School.
3. **Dispositions**, one per candidate, set by hand in the CSV:
   - **added**: the rule from the brief. A state license list counts as evidence by itself. A
     federal side-list row needs a live website showing flight training.
   - **duplicate**: already in `flight_line.json` by domain, by name in the same state, by
     street address, or as a campus or alias. The detail names the record.
   - **rejected**: not flight training (simulator type-rating centers, maintenance, drone and ATC
     schools, an aerial photography firm, an air cargo carrier), or a federal row with no live
     site and no other confirmation.
   Michigan and Minnesota schools with no website found are still added, as records with no
   domain, because the state license is the evidence.
4. **Ingest** (`57 ingest`). Following `15_cirrus_ctc_ingest.py --add-missing`, the 67 schools go
   into `output/flight_schools_enriched.json` with `source: hackers_2026_added` and a
   `provenance` list: list, record id, list date, date pulled, and the name as listed. The
   step refuses any domain already in `flight_line.json`.
5. **Crawl.** The 43 websites went through `09a_fleet_crawl.py --targets --out-dir`, with the
   `clean_html` fix below. The 28 that were thin, flagged or unanswered then went through
   `10_fleet_browser_crawl.py --crawl-only --no-evasion`. Results: 38 answered over HTTP and 25
   had browser text. Hall's Flying Service served a challenge and CKT Aviation refused (401);
   neither was circumvented. Three sites timed out.
6. **Extract.** `extract_v2.py` ran with the March prompt, `claude-haiku-5-5`, effort medium,
   on the Message Batches API: 36 HTTP requests and 24 browser requests, all succeeded.
   `57 merge-extract` folded them into `output/added_extract.jsonl`, keyed by domain and layout,
   without touching the March extraction files.
7. **Build.** `21_build_flight_line.py` now reads enriched entries that carry a `provenance`
   list. They join the directory after HTTP recovery, so the March recovery cannot be steered
   onto them. It also reads `added_extract.jsonl` as a fourth extraction source and copies
   `provenance` onto each school's record. Then `22_pdf_rate_sheets.py --offline`.
8. **Release candidate.** The v0.3-20261023 frozen tables are reused: `aircraft.parquet`,
   `unmatched.parquet` and `results.json` are copied, and 34 is not re-run, so the window and
   every aircraft number stay as they were. Then the school stages run on the rebuilt flight
   line: 38, 35, 44, 40, 41 and 42 (`data/research-2026-11/added/build_rc.sh` on trogdor). 35
   is the hackers-airports version, the code the live explorer was built from (see "Things that
   look wrong"). In the release, schools that came from a list keep the name set by hand and
   carry `added_from` (list and date pulled; record ids stay internal). Page titles alone gave
   names like "MA" and "Flights School".

### The 09a cleaner fix

`clean_html` deleted any element whose class or id matched its boilerplate words, including
`<body>`. It now skips `<html>` and `<body>`. Evidence (`scripts/probes/clean_html_body_fix.py`,
on the cached March pages):

| Page | `<body>` match | Raw text | March cleaned | Fixed |
|---|---|---:|---:|---:|
| aerobatics.com/index | sidebar | 4,892 | 88 | 4,294 |
| barioaviationservices.com/bario-news | sidebar | 2,444 | 40 | 1,778 |
| springcityaviation.com/index | modal | 2,126 | 63 | 1,372 |
| wingsflighttraining.com/index | sidebar | 3,729 | 28 | 67 |
| advancedaviation.com/instrument-rating | banner | 2,174 | 0 | 1,339 |
| americanwinds.edu/index | sidebar | 12,970 | 0 | 10,593 |
| curtiseads.com/index | Sidebar | 6,436 | 32 | 5,154 |
| flycda.com/index | banner | 2,356 | 0 | 1,354 |

Across the whole cache, one page per school, the fix un-empties 429 of the 529 emptied pages
whose `<body>` matched (`--n 0`). Run against the pre-fix 09a (`--crawler`, from
`git show 187743b`), the same probe un-empties 1 of 529, which is the negative control. The 100
pages still emptied are mostly Elementor sites: every content block there carries
`elementor-widget`, and the same filter deletes it (wingsflighttraining.com above). That is a
ROADMAP line. The fix changes future crawls only; no existing school was re-crawled.
`tests/test_clean_html.py` pins the invariant.

## Gates

All run on trogdor from `/mnt/data/home/dew/dev/aether-addschools/research/FlightSchoolMapping`
with `PY=/mnt/data/home/dew/dev/aether/research/FlightSchoolMapping/scripts/.venv/bin/python`.

| Gate | Command | Result |
|---|---|---|
| Baseline rebuild reproduces production | `$PY scripts/21_build_flight_line.py && $PY scripts/22_pdf_rate_sheets.py --offline`, before any addition, then `cmp` against production `output/flight_line.json` | identical, byte for byte (`flight_line_base.json` too) |
| Every candidate has one disposition | `$PY scripts/57_added_schools.py ingest` (exits 1 otherwise) | 178 of 178 |
| Every added school is in the rebuilt flight line with its provenance; pre-existing schools unchanged; nothing else new | `$PY scripts/57_added_schools.py check --reference /mnt/data/home/dew/dev/aether/research/FlightSchoolMapping/output/flight_line.json` | **PASS**: 4,825 to 4,892, 67 new records each with provenance, 0 pre-existing changed. Negative control: one rate bumped in the reference makes it FAIL on that school |
| Release: control schools identical, added schools present and named, tail records unchanged | `$PY scripts/57_added_schools.py release-check --release v0.4-20261104-rc1 --reference-release v0.3-20261023` (the live v0.3 release, linked read-only) | **PASS**: 2,470 to 2,493; 0 of the 2,470 changed; 23 of 23 added schools with a fleet row or listing present; 317,972 tail records, 0 differing beyond an added school's name in their `sch` list. Negative control: one field changed in the reference schools file makes it FAIL |
| Release build's own checks | `$PY -m pytest -q tests/`; each stage ran to completion, and 42 wrote its manifest | 24 passed; `v0.4-20261104-rc1: 33 files, 2351 MB` |
| The candidate renders through the explorer code | Release `web/`, `schools_full.json` and `airports_full.json` synced into a copy of `website/` at `origin/hackers-airports` (the live site's code); `npx astro dev`; `curl /explore/school/kalamazooflightinstruction.com/` | HTTP 200; the page names the school and lists its 8 tails, all on the registry and all flying in the window; screenshot checked at 390 px |

Two caveats the gates do not cover:

- **The 38 reproduction.** Run on the old flight line, `38_schools.py` gives exactly the live
  v0.3 `schools_full.json`. `44_airports.py` gives the same airports, with aircraft lists in a
  different order.
- **40 is not deterministic.** On the unchanged v0.3 inputs, six runs gave 376, 372, 366, 360,
  364 and 353 unlisted operations. The candidate's own run gave 353, so its training numbers
  cannot be compared with v0.3's 372. What can be measured: 3 of v0.3's 372 operations now have
  a tail on an added school's fleet (AFTCLOUD INC = Cloud Aviation, FLY CESSNA LLC = Fly
  Glendale, LITTLE DOG AVIATION LLC).

## New totals (for the talk; the talk doc was not edited)

| | v0.3 / March | Candidate |
|---|---:|---:|
| Records in `flight_line.json` | 4,825 | 4,892 |
| with no fleet | 1,502 | 1,546 |
| with no website | 1,525 | 1,549 |
| Fleet rows | 16,881 | 16,981 |
| Distinct tails | 11,602 | 11,636 |
| School pages in the release | 2,470 | 2,493 |
| FSAI "live sites we didn't have" | 22 | 20 (after removing FTI and Orient) |
| Michigan + Minnesota schools not in our list | "about 40" | 39 added (15 with a website), plus 5 rows that were ours (SkyWalker, three ATP sites, Solo Aviation) and 1 aerial photography firm |

The 23 added schools that get explorer pages: Kalamazoo Flight Instruction (8 tails, all flew),
Cloud Aviation (7), Alaska Flight Academy (6), Fly Glendale (5), Speedway Flight Training (5),
Aero Global Aviation Academy (4), Three Point Aviation (4), Lapeer Aviation (3), Maxwell
Aviation (3), Wings Over Water (2), Air Conrad, Huron Aviation Services, Victors Aviation (1
each). Ten more have only listings (models and prices, no tails): American Flight Schools,
Waypoint, Norton Aviation, JC Air Academy, Sawtooth Aviation, World Wide Wings, Flight
College, Flight Stars, SkyView Aviation (3 Day Multi) and Maxflight Helicopter Services.

## Cost

| Item | Cost |
|---|---:|
| Extraction, Message Batches, recorded usage at batch prices (HTTP 876,703 in / 28,414 out: $0.1195; browser 185,640 / 27,491: $0.0162) | $0.136 |
| Token-count calls for the estimate | free |
| Pre-run estimate (assumed 1,500 output tokens per request) | $0.16 |

## Staged candidate

- Release `v0.4-20261104-rc1` on trogdor:
  `/mnt/data/home/dew/dev/aether-addschools/research/FlightSchoolMapping/data/release/v0.4-20261104-rc1/`.
  It holds `web/` (explorer shards and indexes), `schools_full.json`, `airports_full.json`,
  `training.json`, `privacy.json` and `download/` (2.35 GB, 33 files).
- `results.json`, `aircraft.parquet`, `unmatched.parquet`, `airport_ops.json`,
  `survey_check.json` and `scrape_facts.json` are v0.3's files, copied unchanged.
  `results.json` therefore still says `"release": "v0.3-20261023"`, which is accurate: those
  findings are v0.3's.
- The rebuilt `flight_line.json` and its inputs are committed on `hackers-talk`. The trogdor
  worktree `/mnt/data/home/dew/dev/aether-addschools` is a separate clone, so the production
  checkout and its cron were not touched. Its `output/` links the production crawl caches
  read-only, and its `data/history` is a read-only link.

## Publish (not run; David's call)

On monster4, from `/Users/dew/dev/aether-talk`. Downloads go first, then the site, and the
`latest.json` pointer flips last:

```sh
REL=v0.4-20261104-rc1
SRC=trogdor:/mnt/data/home/dew/dev/aether-addschools/research/FlightSchoolMapping/data/release/$REL
W=$(mktemp -d) && /usr/bin/rsync -a "$SRC/download/" "$W/download/"
python3 research/FlightSchoolMapping/scripts/36_publish_r2.py "$W/download" "$REL"
git worktree add "$W/site" origin/hackers-airports --detach
cd "$W/site/website" && npm ci \
  && /usr/bin/rsync -a --delete "$SRC/web/" public/x/ \
  && /usr/bin/rsync -a "$SRC/schools_full.json" "$SRC/airports_full.json" src/data/explore/ \
  && npm run build \
  && npx -y wrangler@4 pages deploy dist --project-name aether-flight --branch main
printf '{"release": "%s"}' "$REL" > "$W/latest.json" && npx -y wrangler@4 r2 object put aether-public/latest.json \
  --file "$W/latest.json" --content-type application/json --cache-control "public, max-age=60" --remote
```

The site is built from `origin/hackers-airports` because that is where the live explorer's code
lives (deployment 9b5069e8 is commit 2834001). `hackers-talk` lacks that commit's tail-page
change.

## Rollback to v0.3-20261023 (one command)

```sh
cf pages deployments rollback 9b5069e8-efed-4767-8723-8d8e9aa38e53 && T=$(mktemp) && printf '{"release": "v0.3-20261023"}' > "$T" && npx -y wrangler@4 r2 object put aether-public/latest.json --file "$T" --content-type application/json --cache-control "public, max-age=60" --remote
```

`9b5069e8-…` is the production deployment live today (`wrangler pages deployment list
--project-name aether-flight`, checked 2026-11-04). The v0.3 downloads stay under `v/v0.3-20261023/`
in R2, so only the site and the pointer move. The `cf` rollback command is from `cf cli search`.
It was not run, and whether it needs the project or account given explicitly is unverified;
the dashboard's Rollback button on that deployment does the same thing.

## Things that look wrong (found on the way)

- **`40_training_detect.py` is not deterministic** (six runs, 353 to 376; see Gates). The
  talk's "372 training operations missing from our list" is one draw. Unverified cause: the
  feature query `GROUP BY`s without `ORDER BY`, and the shuffled StratifiedKFold assigns folds
  by row position. ROADMAP.
- **Five "missing" unlisted operations are in `flight_line.json` already**, as FAA registrant
  records: RD AIRE LLC, AEROLANDING LLC, FLY VERMONT LLC, FMFA INC, and GC AVIATION SERVICES-1
  LLC (First Flight Aviation, from the VA list). `38_schools.py` drops registrant-only records
  whose names lack an operator word, so their tails count as on no school's fleet. ROADMAP.
- **Schools whose domain moved**: Lincoln Skyways (lincolnskyways.com), Nulton
  (flynultonaviation.com), Pilot Training Center Homestead (now Orient Flight School), and
  flightschooljackson.com, which redirects to Solo Aviation. They were recorded as duplicates
  and left alone, since fixing them would edit existing records. ROADMAP.
- **A lapsed school domain now serves gambling spam**: spectrumpilotservices.com, listed for a
  Minnesota-licensed school. It was not attached. Other old domains in our list may have lapsed
  the same way.
- **`docs/hackers-2026-state-registries.md` miscounted SEVP**: 22 schools were left over and it
  accounts for 21. The one it missed, American Aviation Academy (El Cajon), has no live site;
  a search result says it was folded into Sling Pilot Academy (unverified).
- **The explorer code differs between branches**: `hackers-talk` lacks 2834001 (tail pages
  naming their schools). A site built from `hackers-talk` would drop that feature, so the
  publish commands build from `hackers-airports`.
- **`56_fsai_directory.py`'s `TRAINING_TEXT` has `cfi\\b` inside a raw string**, so "CFI"
  never counts as training text. ROADMAP.
- **35 orders a tail's same-day legs nondeterministically** ("recent" for 3 of 317,972 tails
  came out in a different order on a re-run). Harmless; the release check compares them as sets.
- **The FSAI homepage check counts a site as "confirmed" from keywords**, which let a
  simulator type-rating center through (FTI).

## Not done

- The FAA Part 141 list's unmatched rows (up to about 25 schools, per the state-registries doc)
  were not in the brief's sources. That remains the ROADMAP item "match the Part 141 list".
- The 24 Michigan and Minnesota schools with no website have no fleet and no explorer page.
- Hall's Flying Service (challenge page) and CKT Aviation (401) were not crawled past their bot
  walls. Blue Lakes Aviation, Midwest Aviation and Plainwell Aviation timed out.
- Nothing is published.
