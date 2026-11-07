# Hackers 2026: flight schools found through Google Places, and where every field came from

Written 2026-11-04. Stage: prototype work inside an MVP release (`docs/hackers-2026-plan.md`).
Scripts: `research/FlightSchoolMapping/scripts/60_google_schools.py` (classify, dedupe, add),
`61_contact_provenance.py` (contact details from each school's own pages), with `page_text.py` and
`haiku_batch.py`; the build (`21`), release (`38`) and gates (`57`) carry the new fields. Every
candidate's disposition is in [`hackers-2026-google-schools.csv`](hackers-2026-google-schools.csv).

> **Headline.** Of the 771 websites Google Places led us to, **566 are flight-training operations we
> did not have** and are now in `flight_line.json`: 4,892 records became **5,458**, and no existing
> record changed except for three added fields. 343 of the 566 list a fleet or prices, so the staged
> release has **2,836 school pages, up from 2,493 in rc1** (2,470 in v0.3). Every record now says where
> each name and contact value came from, carries contact details from permitted sources only, and
> carries its Google place IDs (2,110 schools, 2,609 IDs), which the explorer turns into an "Open in
> Google Maps" link. Claude Haiku 5.5 classified, read and extracted for **$3.00** in total. Nothing is
> published: release candidate `v0.4-20261104-rc2` is staged on trogdor.

> **Correction to a working assumption.** The Google Places doc estimated from a hand check of 30 that
> 53% to 73% of the 771 are flight training we lacked (411 to 565 schools). After classifying every
> site and deduping, the count is 566 (73%), the top of that range: most of the hand check's "unclear"
> sites turned out to be schools. The classifier agreed with that hand check on 23 of its 24 decided
> sites (all 16 "training" and the 1 "ours" are "yes", 6 of 7 "not" are "no"); the one disagreement, sabriscorporation.com, offers glider
> training alongside aircraft services, which the brief counts. The talk doc's numbers were left
> unedited, as asked.

## Why

The talk says our list misses schools that a map search finds. Adding them, from the schools' own
pages and with a record of how each was found, makes the explorer more complete before the room tries
it, and tests the claim. David also asked that every bit of data say where it came from, so each
record now names the source, reference and date of every name and contact value. Another approach
would serve if it adds only verified flight-training operations, keeps Google content out of
everything we store or publish, and leaves existing records unchanged except for added fields.

## Google's terms, as applied

The Maps Platform terms let us keep place IDs indefinitely and Places coordinates for 30 days, nothing
else. So Google's names, addresses, labels and websites were used only to find and check candidates:

- They stay in the git-ignored `data/research-2026-11/google/` (on this Mac, and the copy on trogdor in
  the `aether-addschools` clone, whose `schools/candidates.json` holds them for dedupe). The ROADMAP
  line to delete them by 2026-12-03 now covers both copies.
- Crawls and prompts never saw them: crawl targets were named by domain, with no city or state.
- A school's name, address, phone, hours and website come from its own pages, each value found again
  verbatim on the page its provenance names (`60 check`). Tracking parameters Google appends to
  listing links (`utm_*`) are stripped from every URL we record.
- A leak scan of the committed files for Google's formatted addresses found 12 in `flight_line.json`.
  Five were there in March, before any Google query (schools copy Google's format onto their own
  pages); every other one traces to the school's own page, verified. The disposition CSV holds none.
- The only Google content published is place IDs.

## Method

1. **Candidates** (`60 candidates`). 59's selection, recomputed: the same 771 sites (checked against
   the list in `docs/hackers-2026-google-places.md`), with the 829 place IDs whose website lands on
   them (branches give a site several).
2. **Crawl.** `09a_fleet_crawl.py --targets --out-dir` on all 771 (770 answered with pages; 45 refused
   the research user agent with a 403). Then `10_fleet_browser_crawl.py --crawl-only --no-evasion` on
   253: no HTTP pages (46), a refusal or challenge (18), thin pages (186) or a home page under 600
   characters of text (3). Nothing tried to get past a bot check.
3. **Classify** (`60 classify-build`, Message Batches, `claude-haiku-5-5`, effort medium, structured
   output). One request per site with its crawled pages (home first, then location and fleet pages; raw
   HTML reduced to visible text plus JSON-LD and `tel:` links, up to 36,000 characters). The prompt is
   below. Every value the model returns must appear verbatim, after normalizing case, punctuation and
   spacing (phones as ten digits), on the page it names, or on another page in the same request, which
   then becomes the reference; otherwise it is dropped (`page_text.py`).
4. **Gate**: a seeded random 30 of the "yes" set read by hand against the pages.
5. **Dedupe** (`60 dedupe`) against `flight_line.json` as committed (4,892 records, which include the 67
   schools added from public lists): same domain, including where our site or theirs redirects (March
   and November crawls); a near-identical name in the same state (59's rule, tightened so a name must
   match whole words: the substring version matched "Colorado Flight School" to "Flights, Inc."); the
   same street address with a shared distinctive name word; or the address of a Google place already
   matched to one of ours with a shared name word. The 140 pairs it flags as ambiguous (similar names
   in a state, or a shared address where the candidate's pages name our school) were read by hand;
   decisions that changed a disposition are in `HAND` in the script.
6. **Add** (`60 ingest`): enriched entries with source `google_places` and provenance (one entry per
   place ID, list date and pulled date 2026-11-04), name, address, phone, city and state from the
   verified page values with per-field provenance. Six sites on site-builder subdomains (Wix,
   Squarespace) are added without a domain, as 21 keys domains.
7. **Extract** fleets and rates with `extract_v2.py` (the March prompt plus training prices), Haiku 5.5,
   effort medium: 546 HTTP and 143 browser requests, all succeeded, merged by `57 merge-extract`.
8. **Shared tails.** `60 check` lists tails an added school shares with a school we already had. Six
   added schools turned out to be the same operation as an existing record (most of their tails are
   that record's) and were moved to duplicate; the rest share one to a few tails with a leaseback
   owner or another school, and stay (see "Things that look wrong").
9. **Rebuild** `21_build_flight_line.py` and `22_pdf_rate_sheets.py --offline`.

### Classifier prompt

System (shared with 61):

> You read a business's own web pages and answer questions about it, only from what the pages say.
> When asked to copy a value, copy it exactly as it is written on the page, character for character,
> and give the URL from the '--- Page: <url> ---' header of the page it is on. Use an empty string when
> the pages do not give a value. Never guess, complete, reformat or combine values from different
> places.

User (abridged only where marked):

> Business website: {site}. Answer these questions from the web pages below.
>
> - flight_instruction: does this business itself give flight instruction (lessons toward a pilot
>   certificate or rating, or dual instruction) in manned aircraft: airplanes, helicopters, gliders,
>   gyroplanes, balloons, or light-sport aircraft (including weight-shift trikes and powered
>   parachutes)? "yes", "no" or "unclear". Answer "no" for: paramotor, paraglider, hang glider or other
>   foot-launched flying; drones or UAS; simulator-only training (type ratings or simulator sessions
>   with no flying in an aircraft); ground school or test prep only; charter, tours, sightseeing or
>   rides with no training program; aircraft sales, maintenance, FBO services or avionics; software,
>   apps, directories, associations, publications, or a business that lists or refers to schools run by
>   others; skydiving. A flight school, flying club, college or university flight program, or
>   independent flight instructor that teaches in aircraft is "yes", even if it also does charter,
>   maintenance or FBO services. "unclear" only if the pages are too thin or off-topic to decide.
> - operation_type: flight_school, flying_club, university_program, independent_instructor, or none.
> - not_training_kind; aircraft; evidence (a short quote copied exactly) and evidence_url; airports.
> - name, phone, street address, hours (each with its URL), city and state, as 61 asks (below).

The answer is constrained to a JSON schema with enums for the answers above.

## Classifier results and gate

| Answer | Sites |
|---|---:|
| yes | 605 (flight school 459, independent instructor 121, flying club 24, university program 1) |
| no | 130 (simulator training 18, paramotor 14, online ground school, charter brokers, FBOs, maintenance, model-aircraft clubs, software, a drone ride, ...) |
| unclear | 36 |

The evidence quote was found verbatim on a page for 747 of 771 answers (4 not found, 20 empty).

**Gate (hand read, seed 20261105, 30 of the 605 "yes"): 29 correct, 96.7%** (`hackers-2026-google-classifier-check.csv`;
`60 sample --n 30 --seed 20261105` prints the sample with its page text). The miss is
flightadventuresjax.com, which sells Stearman rides as "discovery training flights" with no training
program; it is rejected by hand. Two right answers sit on the brief's boundaries: an FBO whose pages
say its instructors teach (counted, as the brief says), and two powered-parachute instructors
(wheeled light-sport, counted; foot-launched paramotors are not). The gate needed one attempt.

## Dispositions

| Disposition | Sites | Detail |
|---|---:|---|
| **added** | **566** | flight school 426, independent instructor 121, flying club 19; aircraft taught: airplane 501, helicopter 32, light-sport 26, gyroplane 7, glider 6, powered parachute 3, weight-shift 1 |
| duplicate | 38 | domain or redirect 15, name in the same state 8, street address and name word 1, hand review of ambiguous pairs 8, shared tails after extraction 6 |
| rejected | 167 | not flight training 130, unclear from its pages 36, a ride business found in the gate 1 |

The known miss is caught: spartaaero.com is a duplicate of our "Sparta Aero Services, Inc."
(ksarairport.com) by name in Illinois. Duplicates found by hand: jberlife.com (the base page for our
Elmendorf Aero Club, same address and phone), spartaaviation.com (our spartafbo.com, same phone),
neworleansaerialtours.com (our flynola.com, same phone), orientflightschool.com (our Pilot Training
Center Homestead, as the previous pass found), aviationworksinc.com and northcountyflighttraining.com
(our records for them sit on domains that now serve other sites), charlestonaviationvault.com (a
subsidiary of our CHS Flight School), firstflightaviation.com (our FAA registrant record GC AVIATION
SERVICES-1 LLC). After extraction: coloradosbestflightschool.com (10 of its 17 tails are Flights,
Inc.'s), desertskiesaviationaz.com, graceaeroaviation.com, goflytexas.com, livelyaviation.com and
ggahub.com (most or all of their tails belong to one FAA registrant record we already list). Nine
rule matches were overturned by hand as different businesses that share a place name and a street
(for example Miami Flight School and Pilot Training Center Miami, in one building).

### The added schools' fleets and tails

158 of the 566 list tails, 185 list only makes and models (with prices), and 223 list no aircraft:
566 tail rows in all. 343 have a fleet row or a listing and so get an explorer page.

Their tails, scored with the same checks as March (`49_scrape_facts.py --results ... --min-tails 1
--min-home 1`; the two new flags lower the small-corpus floor and leave the other gates as they are):

| Check | March (`hackers-2026-scrape-facts.md`) | 67 list schools (57) | 566 Google schools (60) |
|---|---:|---:|---:|
| Distinct tails claimed | 4,296 | 51 | 566 |
| Well-formed N-numbers | 99.7% | 100% (50 of 50) | 99.8% (563 of 564) |
| On the FAA registry (of well-formed) | 98.0% | 98.0% | 97.7% (550); 10 more deregistered |
| Not found anywhere | 0.4% | 2.0% (1) | 0.5% (3) |
| Website make agrees with the registry | 99.4% | 100% (55 of 55) | 99.2% (512 of 516) |
| Flew in the release window (of registered) | 93.0% | 98.0% | 92.0% (506) |
| Lives where the website says | 78.3% | 95.3% (41 of 43) | 84.6% (269 of 318) |
| Same, website airports shuffled (control) | 0.5% | 20.9% | 3.8% |
| Model and cost | Sonnet 4.6, $100 | Haiku 5.5, $0.14 | Haiku 5.5, $1.71 |

Reproduce (trogdor, from `research/FlightSchoolMapping`):

```sh
$PY scripts/60_google_schools.py scrape-inputs   # the runs restricted to the 566 added sites
cd scripts && R=../data/research-2026-11/google/schools/scrape/runs
AETHER_DATA=/mnt/data/home/dew/dev/aether/research/FlightSchoolMapping/data $PY 49_scrape_facts.py \
  --release v0.3-20261023 --results http=$R/http/results.jsonl --index http=$R/http/index.json \
  --results browser=$R/browser/results.jsonl --index browser=$R/browser/index.json \
  --json-out /tmp/google.json --out /tmp/google.md --min-tails 1 --min-home 1
```

The 67-school column uses the same command on `data/research-2026-11/added/extract/{http,browser}`.

The Google schools' tails hold up as well as March's. The 67-school column is small (43 home-base
pairs, so its shuffled control is high) and is there for completeness.

## Per-field provenance

Every record in `flight_line.json` now carries three added fields:

```
"field_sources":   {"name": P, "contact.phone": P, "contact.email": P, "contact.address": P, "contact.website": P}
"public_contact":  {"name"|"website"|"phone"|"address"|"hours"|"city_state": {"value": ..., **P}}
"google_place_ids": [{"place_id": "ChIJ...", "match": "domain"|"exact"|"address"|"fuzzy"|"added_from_google_places"}]

P = {"source": ..., "ref": URL or record id, "retrieved": date}
```

`field_sources` covers every non-empty value already in `name` and `contact`, which are unchanged.
`public_contact` is new: values from permitted sources only, never AOPA and never Google. `38_schools.py`
publishes it as `contact` and the place IDs as `google_place_ids`; it still drops `name`, `aopa_*` and
`contact` from flight_line, so AOPA-sourced values stay internal as before.

Sources:

| source | what it means | ref | retrieved |
|---|---|---|---|
| `site` | copied verbatim from the school's own page and found there again | the page URL | crawl date of that page |
| `site_extraction` | read off the school's pages by the March fleet extraction (Sonnet 4.6) or the added-school extraction; not checked verbatim | the site | crawl date |
| `aopa` | the AOPA directory (internal only) | `aopa:<id>` | 2026-03-11 |
| `faa_registry` | the FAA registrant behind an FAA-discovered record | `registrant:<name>` | 2026-03-11 |
| `fsai` | FSAI member directory listing matched by website | `FSAI directory listing <id>` | 2026-11-04 |
| list names (`mi_mdot`, `mn_mndot`, `state_edu`, `sevp`, `fsai_directory`, `unlisted_registry`) | the public list a late-added school came from | `<list>:<record id>` | date pulled |
| `domain`, `crawl_target` | a website derived from the record's domain; a name given by the list the crawl was seeded from | the domain | crawl date |

**61's pass over every school website.** For each of the 3,343 websites in flight_line (before the
Google additions), Haiku 5.5 (effort low) read the cached home, location, about and contact pages, the
November 2026 re-crawl first and the March crawl after (09a raw HTML, else its cleaned copy; browser
renders; the 09f expansion homepages; the 57 crawl), skipping any page with no aviation words (lapsed
domains). 2,772 had page text; 571 had none in any cache. The Google schools' values come from their
classification request, read the same way. Then FSAI listings (matched by website) fill a missing street
address or phone, and state lists a missing city and state.

| Field (public_contact) | Schools | from the school's pages | other sources |
|---|---:|---:|---|
| name | 4,746 | 3,192 | FAA registry 1,525; state lists 29 |
| website | 3,332 | 3,332 | |
| phone | 2,403 | 2,393 | FSAI 10 |
| street address | 2,162 | 2,134 | FSAI 28 |
| city and state | 3,960 | 2,392 | FAA registry 1,525; state lists 40; FSAI directory 3 |
| hours | 944 | 944 | |

Values the model gave that were on no page in its request, and were dropped: 34 of 1,785 street
addresses, 53 of 1,974 phones, 19 of 805 hours and 3 of 2,636 names.

| Existing field (field_sources) | Non-empty | aopa | site_extraction | site | faa_registry | lists and other |
|---|---:|---:|---:|---:|---:|---:|
| name | 5,458 | 2,644 | 71 | 565 | 1,525 | 653 (crawl target 574, lists 67, domain 12) |
| contact.address | 5,109 | 688 | 2,389 | 469 | 1,525 | 38 |
| contact.phone | 3,414 | 949 | 1,988 | 477 | | |
| contact.email | 2,842 | 1,419 | 1,423 | | | |
| contact.website | 3,909 | 2,644 | | 566 | | 699 (domain 656, lists 43) |

**Gates.** Every non-empty name and contact value has a provenance entry with a source, a reference and
a date (counts above match the non-empty counts exactly; `57 check` and the release check also fail on
a contact value without provenance or from AOPA or Google). A seeded random 30 of the accepted phones
and street addresses (`61 sample --n 30 --seed 20261105`), read by hand against the page around each:
**28 are the business's own** (`hackers-2026-contact-check.csv`). The two misses are verbatim but the
wrong kind of number: an airport's after-hours call-out line and a member's phone for a club dinner.
Three more are right but institutional (a college's main line or campus address rather than its
aviation center's), and two addresses are the street line without the city.

## Google place IDs

`output/google_place_ids.json` (59's match: 1,543 of our schools, 1,993 IDs) and each Google school's own
IDs go into `google_place_ids`: 2,110 records and 2,609 IDs in `flight_line.json` (one registrant name,
EPIC AVIATION INC, is two records, so one ID sits on both), and 1,515 of the 2,836 release schools with
1,949 IDs. The explorer's school page (`website/src/pages/explore/school/[key].astro`, committed on
`hackers-airports` as 642eeb9, so the publish commands below pick it up) shows one "Open in Google
Maps" chip per ID, best match first, linking `https://www.google.com/maps/place/?q=place_id:<ID>`.

## New totals (for the talk; the talk doc was not edited)

| | v0.3 / March | rc1 | rc2 |
|---|---:|---:|---:|
| Records in `flight_line.json` | 4,825 | 4,892 | 5,458 |
| with no fleet | 1,502 | 1,546 | 1,769 |
| with no website | 1,525 | 1,549 | 1,555 |
| Fleet rows | 16,881 | 16,981 | 18,040 |
| Distinct tails | 11,602 | 11,636 | 12,076 |
| School pages in the release | 2,470 | 2,493 | 2,836 |

## Gates

Run on trogdor from `/mnt/data/home/dew/dev/aether-addschools/research/FlightSchoolMapping` with
`PY=/mnt/data/home/dew/dev/aether/research/FlightSchoolMapping/scripts/.venv/bin/python`.

| Gate | Command | Result |
|---|---|---|
| Rebuild with the new fields reproduces the committed flight_line | 21 + 22 before any Google school, compared with the field_sources, public_contact and google_place_ids keys removed | identical, 4,892 records, key order too |
| Every candidate has one disposition | `$PY scripts/60_google_schools.py dedupe` | 771 of 771 |
| Classifier precision | hand read of 30 | 29 of 30 (96.7%) |
| Added schools present with provenance; pre-existing records unchanged except added fields; nothing else new | `$PY scripts/57_added_schools.py check --reference <flight_line.json at c7b2b1d>` | **PASS**: 4,892 to 5,458, 566 new, 0 pre-existing changed. Negative control: one wet rate bumped in the reference fails on that school |
| Google schools: place IDs present; every name, address and phone found again verbatim on its page; none equal to Google's value | `$PY scripts/60_google_schools.py check` | **PASS**: name 565, phone 477, address 443 (+26 built from a city and a state each on the page), website or domain 567 |
| Provenance on every non-empty field | counts of `field_sources` against non-empty values | 5,458 / 3,414 / 2,842 / 5,109 / 3,909, all covered |
| Contact values are the school's | hand read of 30 | 28 of 30 |
| Release: control schools identical (added fields aside), added schools present and named, tail records unchanged | `$PY scripts/57_added_schools.py release-check --release v0.4-20261104-rc2 --reference-release v0.4-20261104-rc1` (and `v0.3-20261023`) | **PASS** both: 2,493 to 2,836 (0 of 2,493 changed, 343 of 343 added present); 2,470 to 2,836 vs v0.3 (366 of 366). One tail, N4084H, differs only in the last of its 8 recent legs; see below. Negative control: one field changed in a copy of rc1's schools file fails |
| Release build's own checks | `$PY -m pytest -q tests/`; each stage ran; 42 wrote its manifest | 24 passed; `v0.4-20261104-rc2: 33 files, 2354 MB` |
| The candidate renders | rc2's `web/`, `schools_full.json`, `airports_full.json` in the `hackers-airports` explorer at 642eeb9, `npx astro dev`, `/explore/school/centennialflyers.com/` and `/explore/school/plusoneflyers.org/` | HTTP 200; Centennial Flyers (Google-found, KAPA) with 34 tails, all flying, and one Maps link; Plus One Flyers with three; screenshot checked at 390 px |

**N4084H.** 35 keeps each tail's last 8 legs with `row_number() OVER (PARTITION BY hex ORDER BY
start_ts DESC)`; legs with tied start times are picked arbitrarily. Running 35 twice on identical
inputs gave N4084H's last leg as day 2 once and day 1 once, the same difference rc2 shows against rc1.
The release check now counts a tail that differs only in its last recent leg separately (1 here)
instead of failing on it.

## Cost

| Item | Recorded usage at batch prices |
|---|---:|
| Classification, 771 requests (6.36M in, 0.63M out) | $0.475 |
| Contact pass, 2,772 requests (10.15M in, 1.15M out) | $0.794 |
| Fleet extraction, 546 HTTP + 143 browser requests (13.72M in, 0.57M out) | $1.726 |
| Six synchronous test requests before submitting | under $0.01 |
| **Total** | **$3.00** of the $15 budget |

The estimates before each run (token counts on a sample, free) were $0.52, $1.14 and $1.95.

## Staged candidate

- Release `v0.4-20261104-rc2` on trogdor:
  `/mnt/data/home/dew/dev/aether-addschools/research/FlightSchoolMapping/data/release/v0.4-20261104-rc2/`
  (2.6 GB; `download/` 2.35 GB, 33 files). Built by
  `data/research-2026-11/google/schools/build_rc2.sh`: v0.3's frozen tables as for rc1, then 38, 35
  (hackers-airports version), 44, 40, 41, 42.
- `schools_full.json` and the downloadable `schools.json` now include each school's `contact` (name,
  website, phone, address, hours, city and state from its own pages, the FAA registry, FSAI or a state
  list, with source, reference and date) and `google_place_ids`. The explorer shows only the Maps link;
  the contact block is data for now. Publishing contact values sourced from FSAI (28 addresses, 10
  phones) is David's call; drop them in 61 if not.
- 40 was re-run, so rc2's training numbers are one more draw of a nondeterministic stage (as rc1's).

## Publish (not run; David's call)

On monster4, from `/Users/dew/dev/aether-talk`. Downloads first, then the site, the pointer last. The
site is built from `origin/hackers-airports`, which now has the Maps link (642eeb9):

```sh
REL=v0.4-20261104-rc2
SRC=trogdor:/mnt/data/home/dew/dev/aether-addschools/research/FlightSchoolMapping/data/release/$REL
W=$(mktemp -d) && /usr/bin/rsync -a "$SRC/download/" "$W/download/"
python3 research/FlightSchoolMapping/scripts/36_publish_r2.py "$W/download" "$REL"
git fetch origin && git worktree add "$W/site" origin/hackers-airports --detach
cd "$W/site/website" && npm ci \
  && /usr/bin/rsync -a --delete "$SRC/web/" public/x/ \
  && /usr/bin/rsync -a "$SRC/schools_full.json" "$SRC/airports_full.json" src/data/explore/ \
  && npm run build \
  && npx -y wrangler@4 pages deploy dist --project-name aether-flight --branch main
printf '{"release": "%s"}' "$REL" > "$W/latest.json" && npx -y wrangler@4 r2 object put aether-public/latest.json \
  --file "$W/latest.json" --content-type application/json --cache-control "public, max-age=60" --remote
```

## Rollback to v0.3-20261023 (one command)

Unchanged from rc1 (`docs/hackers-2026-added-schools.md`); the deployment ID was checked live on
2026-11-04 by that pass and should be re-checked with `wrangler pages deployment list --project-name
aether-flight` before use:

```sh
cf pages deployments rollback 9b5069e8-efed-4767-8723-8d8e9aa38e53 && T=$(mktemp) && printf '{"release": "v0.3-20261023"}' > "$T" && npx -y wrangler@4 r2 object put aether-public/latest.json --file "$T" --content-type application/json --cache-control "public, max-age=60" --remote
```

## Things that look wrong (found on the way)

- **A school keyed by `sites.google.com`.** flight_line has a record whose domain is the Google Sites
  host itself (whiskey17flight). Any other Google Sites school would collide with it; 60 skips the
  domain rule for that host. ROADMAP.
- **FAA registrant records that are another school's fleet.** Dedupe after extraction found added
  schools whose tails are mostly an FAA-discovered registrant record we already list (six were moved to
  duplicate). Others remain, because the record could be a leaseback owner rather than the operation:
  myflighttime.com (9 of FLY AVIATION CORP's 9 tails, 3 of FLIGHT LEASING CORP's), 5starflight.com (5 of
  CASH MANAGEMENT CONSULTING LLC's 8), flighttestprep.com (6 tails of UNIQUE EDUCATIONAL EXPERIENCES,
  which is itself two records), flydtpa.com (4 of ROTORMATE LLC's 7), bragflight.com (all 3 of
  WESTOPLEX HOLDINGS LLC's), and about 40 schools sharing one or two tails with an owner LLC or person.
  These registrant records count operations twice or count owners as schools. ROADMAP.
- **Two schools share six Cirrus tails**: kuhntrainingcenter.com and our novapilots.com (Leesburg area);
  aircraft management or a change of operator. Both kept.
- **Non-training records in our list**: centralarkansasskydiving.com (skydiving) and
  sundogparagliding.com (paragliding) are schools in flight_line; the contact hand check found them.
- **Domains that moved**: our northcountyflight.com served "Platinum Aviation is now AAMRO Aviation" in
  March; our newarkheathairport.com redirects to an unrelated site. Both records were left as they are.
- **One registrant name is two records**: EPIC AVIATION INC.
- **21 prefers the fleet extraction's contact over the directory's**, and the fleet extraction's
  values are not checked verbatim (`site_extraction` above). For the Google schools, 21 now takes only
  verified values; for everyone else `public_contact` holds the verified ones next to the unchanged
  `contact`.
- **35's recent legs are not deterministic** (N4084H above). ROADMAP.
- **The explorer page overflows at 390 px** (the stat cards and chips run past the right edge in a
  headless 390 px render); it predates this change.

## Not done

- The contact block is not rendered in the explorer; only the Maps link is. ROADMAP.
- 571 of our school websites have no cached page with text, so no `site` contact; refetching their
  contact pages would fill some.
- 36 sites the classifier called unclear were rejected without a hand look; some may be schools.
- The 45 sites that refused the research user agent were rendered in a browser without evasion; any
  still behind a challenge were classified from what loaded.
- Nothing is published.
