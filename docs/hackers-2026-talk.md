# Hackers 2026 talk: narrative and facts

David's 7 minutes in the Aviation session, Saturday 16:00–17:00 (assumed 2026-11-07). David writes the talk track. This file is the narrative it hangs on, the facts with their sources, and the slide list. The backdrop deck is a Claude Slides artifact (link in the PR).

## Narrative

1. I wanted a list of every flight school in the US, with the planes each one flies and what it charges. There isn't one.
2. So we built it in a week in March 2026: we found the schools, had a language model read their websites, and spent about $100 on model time.
3. Then we checked what the model read against two independent public sources, the FAA registry and a year of flight tracking. It holds up.
4. With the list in hand, a few things show up that nobody could see before.
5. Some things are still out of reach, because they're withheld by law or by license, not because they're hard to gather.
6. Gathering this used to be a months-long job for a research firm. Now it's a week and $100 for one person. A lot of information that's public but scattered is about to become datasets.
7. The data, the explorer and the code are available.

## Facts, by section

### 1. There is no list

- The FAA certificates Part 141 pilot schools: 519 certificates at 703 locations (the FAA's own export, 2026-03-02, `research/FlightSchoolMapping/faa-141-flightschools.csv`).
- Part 61 instruction, which is most training, needs no school certificate, so those schools appear on no government list.
- AOPA runs a directory. Its terms forbid copying or republishing it, so it seeded the search but isn't in the release.
- We ended up with 4,825 schools, clubs and university programs (`research/FlightSchoolMapping/README.md`, current results).

### 2. How it was built

- Dates: 2026-03-05 to 03-12 (git history). The first full extraction ran on day one.
- Where the schools came from: the AOPA directory, the FAA Part 141 list, three web directories, the Cirrus training locator, and FAA registrants with school-like names.
- Pages: 32,462 cached, 26,087 fetched over plain HTTP from 4,780 school sites and 6,375 rendered in headless Chromium from 1,987 sites that show nothing without a browser (Wix, Squarespace and the like). The sites also carry PDF rate sheets, photographed rate sheets and tail numbers painted on aircraft in photos.
- Model: Claude Sonnet 4.6 through the Message Batches API, 4,909 calls, 55.9M input tokens and 2.2M output. That's $100 at batch prices, a floor, since the expansion, PDF and photo passes didn't record usage (`docs/hackers-2026-scrape-facts.md`).
- Demo (recorded, 22 s, `docs/hackers-2026-charts/demo_extract.mp4`): Skybound Aviation, Peachtree-DeKalb (KPDK). Homepage in, and 12 s later ten aircraft with rates of $205–$275/h wet, each checked against the registry and the flight data. It cost $0.02 on Claude Opus 5.5. One row disagrees: the site says Piper Warrior, the registry says a 1976 PA-28-181 (sold as the Archer). A 1970 Cessna 172K rents for $205/h.

### 3. Checking it

Ground truth: the FAA Releasable Aircraft Registry (public domain, snapshot 2026-10-23) and the adsb.lol archive (ODbL, 2025-10-23 to 2026-10-22). Raw model output from the HTTP and browser passes, before any cleanup (`docs/hackers-2026-scrape-facts.md`):

- 4,296 distinct tail numbers.
- 98.5% are well-formed N-numbers.
- 98.0% of those are on today's registry. 68 more were deregistered after the crawl. 18 (0.4%) aren't found anywhere.
- Misreads look like OCR errors: N16OME, N87IOU, N318OU (letter O for zero).
- Make on the website matches the registry's manufacturer: 99.4% (4,903 of 4,934).
- Flew in the last 12 months: 93.0% of registered tails.
- Based at the airport the website names: 78.3% (3,275 of 4,184). Control: shuffle the website airports across the same tails and it drops to 0.5%.

### 4. What the list shows

From the fact sheet (`docs/hackers-2026-facts.md`, sections 2 and 4):

- 2,760 rental aircraft have a published wet rate and a registered tail. 164 of them (6%) didn't fly once in 12 months.
- Cessna 172s (1,201 of them): median $179/h wet. Rate and hours flown are unrelated (Spearman ρ 0.06). Chart: `docs/hackers-2026-charts/c172-rate-vs-hours-dark.png`.
- The median 172 that flew grossed $54,162 a year at its posted rate, a floor because some flying happens out of receiver range.
- Registry plus flight behavior finds 372 operations with 3 or more aircraft that fly like schools but aren't on our list. In a hand check of 20, 17 were schools, clubs or leasebacks.
- The busiest school airplane, N43171, a 1983 PA-28-181 based at Fort Lauderdale Executive, flew 1,966 observed hours on 331 days.

### 5. What stays hidden

- Who owns an LLC. Montana's business registry for N970PM LLC lists only its registered agent, All Day $49 Montana Registered Agent LLC, and no members.
- Beneficial ownership. FinCEN's database was never public, and its 2026 rule exempts US companies. The FAA's planned collection of >25% owners won't be public either.
- Licensed data: the AOPA directory, OpenSky's research-only archive. We used them only where their terms allow, and kept them out of the release.

### 6. Where to get it

- Explorer: aether-flight.com/explore (2,470 school pages built from the schools' own sites, tail lookup, airports).
- Downloads: aether-flight.com/explore/data, ODbL.
- Code: the repo, once it's public (needs David's go; procedure in the plan).

## Slides

Backdrops only: one number, chart or image each.

| # | Slide | On it |
|---|---|---|
| 1 | Title | Talk title, name |
| 2 | No list | 519 Part 141 certificates · 4,825 schools found |
| 3 | The build | 32,462 pages · 4,909 calls · 7 days · $100 |
| 4 | Demo | The recording |
| 5 | Checked | Bar chart of the six numbers in section 3 |
| 6 | Misreads | N16OME N87IOU N318OU |
| 7 | Never flew | 6% (164 of 2,760) |
| 8 | 172 rate vs hours | The chart |
| 9 | Not on any list | 372 |
| 10 | Hidden | The Montana record: agent, no members |
| 11 | Get it | QR code, URL |

## Open items

| Item | Owner | Default if no answer |
|---|---|---|
| Make the repo public | David | Slide 11 points at the explorer only |
| Ask Dynasty Aviation about the eight Slings | David | No school named beyond Skybound in the demo |
| Talk title | David | Slide 1 says "Every flight school in the US" |
