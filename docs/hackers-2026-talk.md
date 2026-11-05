# Hackers 2026 talk: narrative and facts

David's 7 minutes in the Aviation session, Saturday 16:00–17:00 (assumed 2026-11-07). David writes the talk track. This file is the narrative it hangs on, the facts with their sources, and the slide list. The backdrop deck is a Claude Slides artifact (link in the PR).

## Narrative

Title: **Scrape. Join. Interrogate.** (David, 2026-11-04). Nobody cares about a flight-school list for its own sake. The point is that using AI to scrape, join and interrogate public data now lets one person learn things that used to take a team and a budget, and that the audience can build on this work: the data, the method and the scripts are published so they can make their own scrapers and provenance-tracked datasets, in aviation or anywhere.

1. **Public, in theory.** What data exists about aviation (airplanes, flights, pilots, crashes, schools, prices, owners, drones), and how hard each is to get: some are a bulk download tonight; some are public but scattered over thousands of places; some aren't public at all. "Hard" doesn't mean secret. Example of hard: "What does a Cessna 172 rent for, wet?" Everyone asks it, no agency collects it, and the answer sits on thousands of school websites in prose, PDFs and photos. With the dataset it's one query: $177 an hour, half between $160 and $195, the typical plane built in 1978 (the published story on aether-flight.com says $176 on March data).
2. **Scrape it.** Why no list exists (Part 61 needs no FAA approval; TSA knows 13,000 providers and doesn't publish them), how a model read 32,462 pages for $100 and how the same job costs $15 now, the demo, one row traced, and how it was checked (registry, flights, a shuffled control, hand reads of random samples). The web keeps moving: eight months later 157 sites are gone and 207 refuse robots. Every list we could find, with what each added, its time and its cost.
3. **Join it.** What the joins show: where learning to fly is cheap or dear, the 1986 production cliff and Cirrus's rise, who's behind an aircraft LLC in three free joins, and (pending) which training aircraft have an accident history.
4. **Build your own.** Datasets we wish existed; the recipe and what each step caught; steal this (explorer, downloads with per-field sources, scripts).

## Facts, by section

### Act 1. What's out there

Source survey: `docs/hackers-2026-data-sources.md` (on PR #8's branch), 39 sources rated by access. Slide 2 groups the ones the talk touches.

### Act 2. There is no list

- The FAA certificates Part 141 pilot schools: 519 certificates at 703 locations (the FAA's own export, 2026-03-02, `research/FlightSchoolMapping/faa-141-flightschools.csv`).
- Part 61 training needs no FAA pilot-school certificate: the instructors hold FAA certificates, the business doesn't, so the FAA's Part 141 directory covers only part of the market. Part 61 providers are still regulated in other ways (TSA's flight-training rules, 49 CFR 1552, apply to them), but no complete, reusable public directory of training businesses exists.
- Part 141 is the alternative: an FAA-approved syllabus, FAA inspections and a pilot school certificate. For an airplane private pilot certificate: 35 hours in an approved Part 141 course (Part 141 Appendix B), 40 under 14 CFR 61.109.
- There is a government list, and it isn't public. Every flight-training provider, Part 61 included, registers with TSA's Flight Training Security Program (49 CFR 1552). TSA's 2024 final rule (89 FR 35580): "approximately 9,000 of the 13,000 flight training providers registered with TSA operate under 14 CFR part 61"; a Part 61 provider is an individual CFI or a group of CFIs, so this is not a count of schools. Verified in the GovInfo text of the rule, 2026-11-03.
- State lists (`docs/hackers-2026-state-registries.md`): 24 states register aircraft, none publishes the registry free in bulk; Michigan (75) and Minnesota (56) license flight schools and publish the lists, which would add about 40 schools we don't have.
- The trade association's list (`docs/hackers-2026-fsai-directory.md`): the Flight School Association International (FSANA until February 2026) publishes a member directory, 567 entries, searchable only through its own page. 385 are US training providers by their tags or names; 241 were already in our list. Of the 144 that weren't, sorting by hand found examiners, insurers, vendors, an airline and individuals who ticked "flight school"; 20 are live flight-training websites we didn't have (Alaska Flight Academy and American Flight Schools in Englewood among them), and 11 more listed school sites no longer answer.
- Google Places (`docs/hackers-2026-google-places.md`): free IDs-only text searches over 516 self-splitting rectangles (2,360 searches, 26 minutes) found 11,420 places; Google labels 4,527 operating US places as flight training. 1,993 match a school in our list by website or a near-identical name, which also gives 1,543 of our schools a Google place ID. 771 others have a live website that mentions flight training; a hand check of 30 says 53% to 73% of those are real operations we lacked (about 410 to 565), the rest being charter operators, FBOs, vendors and paramotor schools. Then Claude Haiku 5.5 read each crawled site (`docs/hackers-2026-google-schools.md`): 605 give flight instruction, 130 don't, 36 unclear; a hand check of 30 of its yeses found 29 right. 566 added after dedupe (426 schools, 121 independent instructors, 19 clubs); their tails score like March's (99.8% well-formed, 97.7% in the registry, 92.0% seen flying, 84.6% home base matches). Haiku cost $3.00. Cost: about $142 of Place Details ($109 names and labels, $33 websites); the searches were free. Google's terms let us keep only the place IDs, so names, phones and hours come from the schools' own sites.
- Added since (`docs/hackers-2026-added-schools.md`, `docs/hackers-2026-google-schools.md`): 67 schools from FSAI (20), Michigan and Minnesota licenses (39), other state and federal lists (4) and the sky-found operations (5), then 566 found through Google Places, so 5,458 in the rebuilt list, every name and contact value with its source. Published 2026-11-05 as release v0.4-20261105-rc4 (site deployment 33eb2273, built from hackers-airports ac220d0): 2,836 school pages with inferred airports, the school's own aircraft descriptions, link-outs (FAA registry, school listing, trackers, Google Maps, AOPA), and NTSB history on every tail page (same airframe: serial match, or make/model from 1982 on). Public contact as in rc3 (no independent instructors' street addresses, no FSAI values); AOPA-only listed airports and descriptions held out of the download. Rollback to rc3: `cf pages deployments rollback fb679077-6a7b-48e7-b21a-1b36490b24a8` plus latest.json back to v0.4-20261104-rc3 (or to v0.3 per docs/hackers-2026-google-schools.md).
- AOPA runs a directory. Its terms forbid copying or republishing it, so it seeded the search but isn't in the release.
- We assembled 4,825 candidate records: schools, clubs, university programs and FAA registrants with school-like names (`research/FlightSchoolMapping/README.md`). 1,502 have no fleet and 1,525 no website; the public release has 2,470 school pages with fleet evidence. National completeness is not measured.

### Act 2. How it was built

- Dates: 2026-03-05 to 03-12 (git history). The first full extraction ran on day one.
- Where the schools came from: the AOPA directory, the FAA Part 141 list, three web directories, the Cirrus training locator, and FAA registrants with school-like names.
- Pages: 32,462 cached, 26,087 fetched over plain HTTP from 4,780 school sites and 6,375 rendered in headless Chromium from 1,987 sites that show nothing without a browser (Wix, Squarespace and the like). The sites also carry PDF rate sheets, photographed rate sheets and tail numbers painted on aircraft in photos.
- Model: Claude Sonnet 4.6 through the Message Batches API, 4,909 calls, 55.9M input tokens and 2.2M output. That's $100 at batch prices, a floor, since the expansion, PDF and photo passes didn't record usage (`docs/hackers-2026-scrape-facts.md`).
- Demo (recorded, 22 s, `docs/hackers-2026-charts/demo_extract.mp4`): Skybound Aviation, Peachtree-DeKalb (KPDK). Homepage in, and 12 s later ten aircraft with rates of $205–$275/h wet, each checked against the registry and the flight data. It cost $0.02 on Claude Opus 5.5. One row disagrees: the site says Piper Warrior, the registry says a 1976 PA-28-181 (sold as the Archer). A 1970 Cessna 172K rents for $205/h.

### Act 2. The same job, eight months later

- Claude Haiku 5.5 re-read the same cached March pages with the same prompt for $15.15, against $100.22 for Sonnet 4.6 in March, with the same consistency numbers (98.1% vs 98.0% in the registry). It reported 4,066 distinct tails to March's 4,296: at low effort the 5.5 models write one entry per aircraft type on big fleet tables; medium effort recovers most of them (`docs/hackers-2026-model-rerun.md`).

- The same sites, re-crawled 2026-11-04 with the March crawlers and re-read with Haiku 5.5 for $13.67 (`docs/hackers-2026-march-vs-november.md`, gates pass, re-run and reproduced). Of 3,067 sites reached in March: 157 (5.1%) are gone (53 no longer resolve, 40 return 404), 207 now refuse the crawler (each served the same crawler a page in March; March ran from a Mac and November from trogdor, so some of this is who is asking), 111 redirect to another domain (one school's old domain now forwards to an online slots site) and 68 read as parked or closed.
- Posted rates: of 1,281 strict pairs (same school, same tail, each month's rate on its own page only), 26% went up, 2% down, 72% unchanged; where a rate moved, the median move was +5.7%. On byte-identical pages, 58 of 58 unchanged. Read it as the school's posted prices rising, not a per-aircraft fact: a hand check of 3 random increases found all 3 real price rises, but one was a dry rate with fuel billed separately (Colorado Flight Center, $160 + $82 fuel for a 182) and one pinned the first column of a price row to a different aircraft (COS Pilot).
- Fleets: 302 of 548 schools with tails changed their list (610 added, 746 removed); the identical-pages control churned 0 added and 4 removed of 97. The `clean_html` bug below hit both months (812 and 709 schools emptied), so those schools are compared on the browser pass only.

- The 372 is one run of a model whose answer depended on row order: `40_training_detect.py` assigned cross-validation folds by row position, and DuckDB's GROUP BY returned rows in a different order each run (found 2026-11-04 by the added-schools agent: 353 to 376 over six runs). Sorted, it is deterministic (two runs byte-identical); across six fold seeds on the v0.3 inputs it finds 365 to 378, 318 operations appear in every run and 435 in at least one, and 310 of the published 372 are in that always-found core. Say "about 370"; the 255 → 62 → 13 funnel describes the 372 that were checked.

### Act 2. A hand audit, and the bug it found

- 40 random extraction calls read by hand (`docs/hackers-2026-extraction-audit.md`, method committed before the draw): tail precision 68 of 75 (91%; 6 of the 7 false tails from one site whose pages arrived empty, so the model read sold aircraft out of URL slugs), recall 68 of 68 on the pages each call saw, rates 52 of 55 (one invented, copied from the plane above it). Only 36 of the 55 returned rates are stated as including fuel on the page; for 19 the page doesn't say. Of the 15 calls that returned no fleet, the model missed a listed fleet 0 times.
- The bigger failure was before the model: `09a`'s `clean_html` deletes any element whose class or id contains "sidebar", "widget", "ad-" and the like, and many sites put such a word on `<body>`. 812 of 1,992 schools in the HTTP crawl kept under 5% of their page text; the browser pass happened to re-read 799 of them.

### Act 2. Checking it

Ground truth: the FAA Releasable Aircraft Registry (public domain, snapshot 2026-10-23) and the adsb.lol archive (ODbL, 2025-10-23 to 2026-10-22). Raw model output from the HTTP and browser passes, before any cleanup (`docs/hackers-2026-scrape-facts.md`):

- 4,296 distinct tail numbers: 4,245 US and 51 foreign registrations (mostly Canadian, reported as written).
- 99.7% of the US tails are well-formed N-numbers. (Until 2026-11-03 the scoring script prefixed an N to every tail, turning a correct C-FEFY into a "malformed" NCFEFY; that was 50 of the 81 "bad" tails.)
- 98.0% of those are in the registry snapshot; 38 of them are pending or sale-reported, so 97.1% hold a valid registration. 68 more are in the deregistered file. 18 (0.4%) are in neither file.
- The 81 "bad" tails, each opened at its source page (`docs/hackers-2026-error-audit.csv`): 50 were my scoring bug (correct Canadian registrations), 15 unclear, 7 typos on the school's own page (N16OME for N160ME), 4 simulator IDs, 3 vintage NC markings read correctly, and 2 model misreads (a tail lifted from an email address; a table cell run into an airport code).
- Manufacturer family on the website matches the registry: 99.4% (4,903 of 4,934 eligible claims). This checks the make only, not model or variant.
- Observed airborne in the last 12 months: 93.0% of tails in the snapshot. No observations is not the same as not flying.
- The model's airport agrees with the flight-inferred home airport for 78.3% of eligible claims (3,275 of 4,184, on 3,224 tails). The prompt gave the model a known-airport hint, so this isn't an independent check of the website. Control: shuffled nationally, 0.5%; that rules out chance, not the wrong school's airplane at the same airport.

### Act 3. What the joins show

From the fact sheet (`docs/hackers-2026-facts.md`, sections 2 and 4):

- 2,760 rental aircraft have a published rate and a registered tail. 164 of them (6%) had no observed flights in 12 months: a stale listing, an idle plane, or flying out of receiver range, not yet separated.
- Cessna 172s (1,201 of them): median $179/h posted. Posted rate and observed airborne hours have almost no pooled rank correlation (Spearman ρ 0.06); not a price effect. Chart: `docs/hackers-2026-charts/c172-rate-vs-hours-dark.png`.
- Follow-up (`docs/hackers-2026-unlisted-operations.md`): of the 372, 255 have a website a web search tied to them, 62 show at least one of their own registered tails on it (against another operation's tails: 0 of 255), and 13 of the confirmed schools and clubs were on none of the March lists. 30 of the 62 confirmations came only from a raw text match, 21 of them because the tail is only in an image file name, alt text or a link: an extraction recall gap.
- 372 registrant names with 3 or more aircraft our model ranks as trainers have no link to a fleet in our data. They're leads, not schools: a hand check of 20 (earlier window) found 12 schools, 2 clubs, 3 leasebacks and 3 unclear. Most of the model's lift comes from aircraft type and registrant fleet size, not flight behavior.
- The busiest aircraft in our matched school-fleet data, N43171, a 1983 PA-28-181 based at Fort Lauderdale Executive, flew 1,966 observed hours on 331 days.

### Act 3. What learning to fly costs, as posted

- From the re-run's new price fields: median private pilot certificate $12,000 (151 schools, interquartile $9,060 to $15,000; 92 are estimates, not packages), instructor $70/h, wet airplane $175/h, simulator $75/h, club dues $50/month. Only 347 of 2,453 school sites post any training price. Hand check: 63 of 66 prices right; quote medians, not single schools.

### Act 3. Accident history on the flight line

- NTSB accident database joined to school fleets (`docs/hackers-2026-ntsb.md`, `63_ntsb_history.py`): 1,825 of 11,713 school aircraft (15.6%) have an NTSB accident or incident record on the same airframe (N-number plus serial, or make and model for pre-1982 records), against 11.8% for comparable private planes that flew last year (same make, model, build years). 77% of the records predate the current owner's registration; since then 3.8% vs 1.8%. Exposure explains most of it: school planes flew a median 181 hours last year, comparison planes 21. Shuffled serials match 0. 959 school aircraft would have been miscounted on a reused N-number alone.

### Act 3. Where ownership stops

- Florida's free quarterly corporate file names at least one person as manager, member or officer for 82.5% of LLC aircraft with a Florida address (6,225 of 7,541). A named role is not ownership. Validation: where a pilot lives at the registry address, the pilot's surname is in an officer's name 67.5% of the time, against 0.2% for another aircraft's officers (`docs/hackers-2026-florida-owners.md`, `52_florida_owners.py`, PASS).
- Any state, from the FAA's own airmen file (`docs/hackers-2026-airmen-llc.md`): a certificated pilot gets mail at exactly the registry address of 20.0% of LLC aircraft (17,362 of 86,805) and 10.8% of corporation aircraft; with house numbers shuffled, 0.02%. Montana-address LLCs: 8.4%, the registered-agent effect. In Florida a matched pilot's surname is among the LLC's named officers 86% of the time (control 0.1%), and the two routes together name someone for 85.7% of Florida LLC aircraft. A pilot at the address is a probable principal, family member or employee, not proof of ownership; the file holds only the roughly half of pilots who let the FAA release their address. The first control (house numbers shuffled within ZIP, keeping hits on other airmen's real addresses) failed its gate for corporations and was changed after seeing the data; the doc shows both.

- Who is behind an LLC. Montana's summary page for N970PM LLC names only its registered agent, All Day $49 Montana Registered Agent LLC. Montana annual reports may name managers or members; we haven't pulled the filing history.
- Beneficial ownership. FinCEN's database was never public, and its 2026 rule exempts US companies. The FAA's planned collection of >25% owners won't be public either.
- Licensed data: the AOPA directory, OpenSky's research-only archive. We used them only where their terms allow, and kept them out of the release.

### Where to get it

- Explorer: aether-flight.com/explore (2,470 school pages built from the schools' own sites, tail lookup, airports).
- Downloads: aether-flight.com/explore/data, ODbL.
- Code: the repo, once it's public (needs David's go; procedure in the plan).

## Critique folded in (2026-11-03)

Codex, prompted as a harsh pilot-and-engineer critic, reviewed this doc and the slides: [docs/reviews/2026-11-03-codex-talk-critique.md](reviews/2026-11-03-codex-talk-critique.md). Folded in: "every school" became candidate records; registry presence is no longer called validation; no observations is no longer called not flying; the revenue figure was cut; the 100-hour inspection aside was cut and the TSA rules acknowledged; the airport check now discloses the prompt's known-airport hint; the 372 are leads, not schools; the Florida names are roles, not owners; Montana is limited to the summary page; the sources slide reclassifies FAA difficulty reports, NASA safety reports and enforcement; a traced row (N6860J) was added. Still open: a representative hand audit of extraction (precision, missed tails, rate meaning) on random cached pages; a same-airport control; Montana's filing history for N970PM.

## Slides

Backdrops only: one number, chart, image or record each.

| # | Act | Slide id | On it |
|---|---|---|---|
| 1 |  | `cover` | Scrape. Join. Interrogate. |
| 2 | 1 | `landscape` | What's public about aviation, and how hard it is to get |
| 3 | 1 | `ask172` | "What does a Cessna 172 rent for, wet?" |
| 4 | 1 | `answer172` | $177/h wet, half $160 to $195; typical plane 1978 |
| 5 | 2 | `nolist` | ~9,000 Part 61 providers (TSA); nobody publishes the list; so we assembled 5,458 |
| 6 | 2 | `schoolsmap` | Map: 4,185 placed (3,581 listed airport, 604 where their planes are based) |
| 7 | 2 | `readcost` | What it costs to read 32,462 pages: person ~$11,000, Sonnet 4.6 $100, Sonnet 5.5 ~$120, Haiku 5.5 $15 |
| 8 | 2 | `demo` | Demo recording |
| 9 | 2 | `checked` | Consistency checks, not accuracy |
| 10 | 2 | `misreads` | 81 bad tails, whose fault |
| 11 | 2 | `audit` | 40 random pages read by hand |
| 12 | 2 | `eightmonths` | Eight months later: 157 gone, 207 refuse, prices up |
| 13 | 2 | `blocked` | Most schools didn't lock us out; their platforms did |
| 14 | 2 | `everylist` | Every list we could find: added, time, cost |
| 15 | 3 | `pickschool` | Pick the school, not the city (metro medians $157-$190; within a metro $39 apart) |
| 16 | 3 | `cliff` | The industry nearly stopped building small planes; Cirrus is the new generation |
| 17 | 3 | `hidden` | Who's behind the LLC? Three free joins |
| 18 | 3 | `ntsb` | Flight-school aircraft have a past: 15.6% vs 11.8% |
| 19 | 4 | `wish` | Datasets we wish existed |
| 20 | 4 | `recipe` | The recipe, and what each step caught |
| 21 | 4 | `getit` | Steal this |

The deck is https://claude.ai/artifact/KUkKop9nCZBpYwrHGcuBtW (private to David). Cut 2026-11-04 after David's review (code fixes, joins table, never-flew, rate-vs-hours, the 372 funnel and the Google tiles slide): kept as files in the session scratchpad only. Being reworked: eightmonths (are the new blocks anti-AI defenses?), learntofly (a cost map), yearbuilt (the 1986 cliff and Cirrus).

## Open items

| Item | Owner | Default if no answer |
|---|---|---|
| Make the repo public | David | The getit slide points at the explorer only |
| Ask Dynasty Aviation about the eight Slings | David | No school named beyond Skybound in the demo |
| Talk title | David | Slide 1 says "Every flight school in the US" |
