# Hackers 2026: "Tedium was the moat" (7-minute talk)

Written 2026-11-03. Replaces the 2026-10-27 field-guide spine in [hackers-2026-plan.md](hackers-2026-plan.md) as the talk itself; the plan still governs the release, explorer and licensing.

**Slot.** One of several presenters in the Aviation session David leads: Saturday 16:00–17:00, one hour ([conference wiki](https://wiki.hackersconference.org/books/2026/page/aviation)); Saturday assumed to be 2026-11-07. David's own talk is 7 minutes. The release freeze moves from Wed 11-04 to Thu 11-05, two days before.

**Why this spine.** David, 2026-11-03: pulling ADS-B and the FCC and FAA registrations alone isn't interesting enough, and another panelist covers ADS-B. What is new is that a list nobody had (every flight school in the US, what it flies and what it charges) was assembled in a week for about $100 of model time. The idea for the room: information that used to be protected only by how tedious it was to gather isn't protected any more. A different talk would serve too, if it gives the room the same realization with numbers that survive a skeptic.

Bullets are for David. The words on stage are his.

## The arc, with times

| Beat | Time | Slide | Every number from |
|---|---|---|---|
| 1. There is no list | 0:00–0:50 | One sentence, no chart | FAA Part 141 export (March 2026) |
| 2. So we read their websites | 0:50–2:00 | 22 s recorded demo, then the cost line | [scrape facts](hackers-2026-scrape-facts.md) |
| 3. Trust, then verify | 2:00–3:15 | Funnel: claimed → real → right make → flew → lives there | [scrape facts](hackers-2026-scrape-facts.md) |
| 4. What the map shows | 3:15–4:45 | Two findings, one slide each | [fact sheet](hackers-2026-facts.md) §2, §4 |
| 5. The moat moves | 4:45–6:15 | Two columns: "was tedious" / "still hidden" | Sources survey, romp doc |
| 6. Fork it | 6:15–7:00 | QR code | Explorer, downloads, repo |

### 1. There is no list

- The FAA certificates Part 141 schools. Most training happens under Part 61, and a Part 61 school doesn't register anywhere.
- The closest thing to a national list is AOPA's directory, and its terms forbid copying or republishing it.
- So "every flight school in America, what it flies and what it charges" was a consulting engagement. Nobody had published it.
- The FAA's own Part 141 list (exported 2026-03-02, `research/FlightSchoolMapping/faa-141-flightschools.csv`) has 519 certificated schools at 703 locations. That's a small slice of the 4,825 schools and clubs we found.

### 2. So we read their websites

- One week, 2026-03-05 → 03-12 (git history). The first full extraction ran within a day of starting.
- The sites are the long tail: Wix and Squarespace pages that render only in a browser, PDF rate sheets, rate sheets posted as photos, N-numbers painted on airplanes in pictures.
- 32,462 cached pages: 26,087 fetched over plain HTTP from 4,780 school sites, and 6,375 rendered in headless Chromium from 1,987 sites that show nothing without a browser. 4,909 model calls.
- Model time: **$100** at batch prices, from the token counts in the results. That's a floor, since three later passes (corpus expansion, rate sheets, photos) didn't record usage. (The pipeline README's "~$15–25" was wrong.)
- **Recorded demo, 22 s** (`docs/hackers-2026-charts/demo_extract.mp4`, also `.gif`; `scripts/demo_extract.py`, recorded by `vhs scripts/demo_extract.tape`): one school's homepage in, and 12 s later its fleet and prices come out, each tail checked against the FAA registry and this year's flights. The school is Skybound Aviation at Peachtree-DeKalb (KPDK): ten tails, $205–$275/h wet, all registered, all flew, all based at KPDK. One row is yellow: the site says "Piper Warrior" and the registry says a 1976 PA-28-181 (sold as the Archer). Say that they disagree, not which is wrong. A 1970 Cessna 172K rents for $205/h. The run cost $0.02 on Claude Opus 5.5. It's recorded, so nothing calls the API on stage.

### 3. Trust, then verify

The reason to believe the map: aviation has independent ground truth for every claim the model makes. Of 4,296 distinct tails the model read off school pages:
- 98.5% are well-formed N-numbers, and 98.0% of those are on the FAA registry today. Another 68 have since been deregistered. Only 18 (0.4%) can't be found anywhere.
- The errors look like a scanner's: `N16OME`, `N87IOU`, letter O for zero.
- The make on the site matches the registry's manufacturer 99.2% of the time.
- 93% of the registered tails flew in the last 12 months.
- 78% live at the airport the website names. With the airports shuffled across the same tails, that falls to 0.5%, so the match isn't luck.
- The lesson for the room: extraction is trustworthy when an independent source can check it, so build the check before you trust the output.

### 4. What the map shows (pick two for the slot)

- **On the rate sheet, not in the sky.** Of 2,760 rental planes with a published price and a registered tail, 164 (6%) didn't fly once in the year.
- **Price doesn't track use.** A Cessna 172 rents for a median $179/h wet whether it flies 100 hours a year or 500 (Spearman ρ 0.06). The median 172 that flew grossed at least $54k at its posted rate.
- **Schools the web never mentioned.** Joining the registry with flight behavior finds 372 operations that fly like flight schools but appear on no list. A hand check of 20 found 17 training-related (85%).
- **The vivid example (unconfirmed):** ten Sling LSAs based at North Perry. Two are registered to Dynasty Aviation, eight to separate Montana LLCs at one registered agent's suite, and our classifier scores nine of the ten as trainers. Ask the school before saying anything on stage beyond "the joins suggest one fleet".

### 5. The moat moves

- **Was tedious, now a weekend:** fleets, prices, who flies how much, where planes sleep.
- **Still hidden, by law or license, not effort:** who owns an LLC (the Montana record names only its agent), beneficial ownership (FinCEN's data isn't public, and the FAA's planned >25% owner collection won't be either), AOPA's directory terms, OpenSky's research-only license.
- Obscurity was doing privacy and competitive work that nobody wrote down. A school's prices used to be comparable only by phone. Now a stranger can compare all of them overnight.
- Question for the room: which list doesn't exist in your industry yet?

### 6. Fork it

- The QR opens aether-flight.com/explore: 2,470 school pages built only from the schools' own pages, plus tail lookup, airports and ODbL downloads.
- The code, `make sample` and an "add a source" recipe. **Blocker:** the repo has to be public before the QR goes on a slide (the procedure is in the plan; it needs David's go).

## Cut for time (keep as backup slides or Q&A)

The FAA's-books audit (registry ghosts, TAF counts unchanged for 28 years), trustee registrations flying abroad, Montana and Delaware agent mailboxes, home-base privacy and LADD, the OpenSky depth. Every one of them is in the fact sheet and the romp doc.

## Gates

- `scripts/.venv/bin/python scripts/49_scrape_facts.py --release v0.3-20261023` (on trogdor) exits 0. It exits 1 if the corpus is implausibly small or the shuffled-airport control comes within 3× of the real home-base match. Last run 2026-11-03: PASS (78.3% vs 0.5%).
- `43_talk_facts.py --release v0.3-20261023` regenerates the fact sheet. Beat 4 quotes only its sections 2 and 4.
- The demo recording shows only tails that `49` would score green or red. No live API call on stage.

## Open items

| Item | Owner | Default if no answer |
|---|---|---|
| Ask Dynasty Aviation about the eight Slings | David | Beat 4 uses the schools-from-the-sky finding without naming a school |
| Make the repo public (plan's "Opening the repo") | David's go, Claude executes | QR points at the explorer only |
| Other presenters' names and topics, for the wiki page | David | Wiki left with David's entry only |
