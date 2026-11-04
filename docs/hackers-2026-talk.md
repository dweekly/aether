# Hackers 2026 talk: narrative and facts

David's 7 minutes in the Aviation session, Saturday 16:00–17:00 (assumed 2026-11-07). David writes the talk track. This file is the narrative it hangs on, the facts with their sources, and the slide list. The backdrop deck is a Claude Slides artifact (link in the PR).

## Narrative

Three acts (David, 2026-11-03):

Umbrella theme (David): which queryable datasets do we wish existed, and how does AI turn scattered, tedious sources into tidy, joinable ones? Along the way, point out where the federal government publishes well and where it doesn't, concretely enough for someone in the room to route a gripe.

1. **What's out there.** A lot about aviation is public in theory: the FAA registry, flight tracking, state business filings, FCC radio licenses, accident reports, and every school's own website. Each source has its own access: bulk and free, one record at a time, paid, licensed, or not public at all.
2. **Pulling it down.** In practice, turning that into structured data used to be the hard part. Worked example: there's no complete public list of US flight schools, so we built a first version in a week, with about $100 of recorded model cost for the extraction, by having a language model read their websites, then checked it for consistency against the registry and a year of flights.
3. **Joining it.** Once the pieces are datasets, joining them on a tail number, a name or an airport shows things no single source does: planes on a rate sheet that never fly, prices that don't track use, schools nobody listed, and where ownership trails stop.

## Facts, by section

### Act 1. What's out there

Source survey: `docs/hackers-2026-data-sources.md` (on PR #8's branch), 39 sources rated by access. Slide 2 groups the ones the talk touches.

### Act 2. There is no list

- The FAA certificates Part 141 pilot schools: 519 certificates at 703 locations (the FAA's own export, 2026-03-02, `research/FlightSchoolMapping/faa-141-flightschools.csv`).
- Part 61 training needs no FAA pilot-school certificate: the instructors hold FAA certificates, the business doesn't, so the FAA's Part 141 directory covers only part of the market. Part 61 providers are still regulated in other ways (TSA's flight-training rules, 49 CFR 1552, apply to them), but no complete, reusable public directory of training businesses exists.
- Part 141 is the alternative: an FAA-approved syllabus, FAA inspections and a pilot school certificate. For an airplane private pilot certificate: 35 hours in an approved Part 141 course (Part 141 Appendix B), 40 under 14 CFR 61.109.
- AOPA runs a directory. Its terms forbid copying or republishing it, so it seeded the search but isn't in the release.
- We assembled 4,825 candidate records: schools, clubs, university programs and FAA registrants with school-like names (`research/FlightSchoolMapping/README.md`). 1,502 have no fleet and 1,525 no website; the public release has 2,470 school pages with fleet evidence. National completeness is not measured.

### Act 2. How it was built

- Dates: 2026-03-05 to 03-12 (git history). The first full extraction ran on day one.
- Where the schools came from: the AOPA directory, the FAA Part 141 list, three web directories, the Cirrus training locator, and FAA registrants with school-like names.
- Pages: 32,462 cached, 26,087 fetched over plain HTTP from 4,780 school sites and 6,375 rendered in headless Chromium from 1,987 sites that show nothing without a browser (Wix, Squarespace and the like). The sites also carry PDF rate sheets, photographed rate sheets and tail numbers painted on aircraft in photos.
- Model: Claude Sonnet 4.6 through the Message Batches API, 4,909 calls, 55.9M input tokens and 2.2M output. That's $100 at batch prices, a floor, since the expansion, PDF and photo passes didn't record usage (`docs/hackers-2026-scrape-facts.md`).
- Demo (recorded, 22 s, `docs/hackers-2026-charts/demo_extract.mp4`): Skybound Aviation, Peachtree-DeKalb (KPDK). Homepage in, and 12 s later ten aircraft with rates of $205–$275/h wet, each checked against the registry and the flight data. It cost $0.02 on Claude Opus 5.5. One row disagrees: the site says Piper Warrior, the registry says a 1976 PA-28-181 (sold as the Archer). A 1970 Cessna 172K rents for $205/h.

### Act 2. Checking it

Ground truth: the FAA Releasable Aircraft Registry (public domain, snapshot 2026-10-23) and the adsb.lol archive (ODbL, 2025-10-23 to 2026-10-22). Raw model output from the HTTP and browser passes, before any cleanup (`docs/hackers-2026-scrape-facts.md`):

- 4,296 distinct tail numbers.
- 98.5% are well-formed N-numbers.
- 98.0% of those are in the registry snapshot; 38 of them are pending or sale-reported, so 97.1% hold a valid registration. 68 more are in the deregistered file. 18 (0.4%) are in neither file.
- Bad strings include N16OME, N87IOU, N318OU (letter O for zero). Not yet traced to the page or the model (the error audit in the research plan does that).
- Manufacturer family on the website matches the registry: 99.4% (4,903 of 4,934 eligible claims). This checks the make only, not model or variant.
- Observed airborne in the last 12 months: 93.0% of tails in the snapshot. No observations is not the same as not flying.
- The model's airport agrees with the flight-inferred home airport for 78.3% of eligible claims (3,275 of 4,184, on 3,224 tails). The prompt gave the model a known-airport hint, so this isn't an independent check of the website. Control: shuffled nationally, 0.5%; that rules out chance, not the wrong school's airplane at the same airport.

### Act 3. What the joins show

From the fact sheet (`docs/hackers-2026-facts.md`, sections 2 and 4):

- 2,760 rental aircraft have a published rate and a registered tail. 164 of them (6%) had no observed flights in 12 months: a stale listing, an idle plane, or flying out of receiver range, not yet separated.
- Cessna 172s (1,201 of them): median $179/h posted. Posted rate and observed airborne hours have almost no pooled rank correlation (Spearman ρ 0.06); not a price effect. Chart: `docs/hackers-2026-charts/c172-rate-vs-hours-dark.png`.
- 372 registrant names with 3 or more aircraft our model ranks as trainers have no link to a fleet in our data. They're leads, not schools: a hand check of 20 (earlier window) found 12 schools, 2 clubs, 3 leasebacks and 3 unclear. Most of the model's lift comes from aircraft type and registrant fleet size, not flight behavior.
- The busiest aircraft in our matched school-fleet data, N43171, a 1983 PA-28-181 based at Fort Lauderdale Executive, flew 1,966 observed hours on 331 days.

### Act 3. Where ownership stops

- Florida's free quarterly corporate file names at least one person as manager, member or officer for 82.5% of LLC aircraft with a Florida address (6,225 of 7,541). A named role is not ownership. Validation: where a pilot lives at the registry address, the pilot's surname is in an officer's name 67.5% of the time, against 0.2% for another aircraft's officers (`docs/hackers-2026-florida-owners.md`, `52_florida_owners.py`, PASS).

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

| # | Act | Slide | On it |
|---|---|---|---|
| 1 | | Title | Talk title, name, the three acts |
| 2 | 1 | Public, in theory | Sources grouped by access: bulk free, per record, paid, licensed, not public |
| 3 | 2 | No list | 519 Part 141 certificates; 4,825 schools found |
| 4 | 2 | The build | 32,462 pages, 4,909 calls, 7 days, $100 |
| 5 | 2 | Demo | The recording |
| 6 | 2 | Checked | Bar chart of the six numbers above |
| 7 | 2 | Misreads | N16OME N87IOU N318OU |
| 8 | 3 | The joins | Which datasets join on which key |
| 9 | 3 | Never flew | 6% (164 of 2,760) |
| 10 | 3 | 172 rate vs hours | The chart |
| 11 | 3 | Not on any list | 372 |
| 12 | 3 | Ownership | Florida names managers for 82.5%; Montana's N970PM record names none |
| 13 | | Get it | QR code, URL |

## Open items

| Item | Owner | Default if no answer |
|---|---|---|
| Make the repo public | David | Slide 13 points at the explorer only |
| Ask Dynasty Aviation about the eight Slings | David | No school named beyond Skybound in the demo |
| Talk title | David | Slide 1 says "Every flight school in the US" |
