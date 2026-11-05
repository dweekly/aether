# Hackers 2026: an outside "US flight schools master" list against ours

David supplied two CSVs on 2026-11-04 (provenance: Antigravity session 81a6f59f-d642-4f01-bc50-7eb6e038c55e, a Part 61 enumeration run outside this repo; its transcript is not on monster4, so how it collected the AOPA listings is not known here): `us_flight_schools_master.csv` (4,957 entities) and `unmatched_faa_instr_airports.csv` (594 airports whose FAA 5010 record flags instruction or rental with no school matched). Working copies, with their emails and phones, are in the git-ignored `research/FlightSchoolMapping/data/research-2026-11/external/`; this doc holds counts only.

> **Headline.** About a quarter of the outside list is not in ours (1,315 of 4,957), and a seeded hand check of 30 suggests about half of those are real training operations we lack, mostly flying clubs. So roughly 600-800 additions, after removing skydiving operators, airports, jet FBOs and duplicates under other names.

## Method

- Sources inside the master list (an entity can have several): AOPA Flight School Finder 2,278, 150th Aero / Tach & Go 2,062, AOPA aircraft rental 2,034, AOPA flying clubs 1,149, BestAviation 841, AOPA universities 284, ASA dealers 73.
- Matched against `output/flight_line.json` (5,458 schools) with `scripts/probes/match_external_school_lists.py`, then a 10-digit phone match, then 59's `confirmed_match` for fuzzy candidates. Commands are at the end.

## Results

| Match | Entities |
|---|---|
| Website domain | 3,223 |
| Exact name in the same state | 347 |
| Phone | 21 |
| Fuzzy, confirmed by 59's rule | 51 |
| Not in our list | 1,315 |

The 1,315 by the list's own category: flying club 529, flight school 483, aircraft rental / training provider 183, FBO 118, collegiate 2. By source: AOPA flying clubs only 389, Tach & Go only 342, AOPA rental only 159, BestAviation only 154, AOPA rental and school 84, AOPA school only 50, ASA dealer only 45, combinations the rest. 463 have a website.

Hand check, 30 drawn with seed 66: 15 plausibly real and new (11 flying clubs, 4 schools), 3 ours under another name (an aero club renamed with its base, a school with a longer listing name, a misspelled school name), 8 not training (4 skydiving operators, 2 airports, 1 jet FBO, 1 club "in formation"), 4 unclear (FBOs and one name with no context). The matcher misses come from renames that share few distinctive words; a phone or airport-plus-name check would catch two of the three.

## Constraints

- Most of the new entries come only from AOPA's directories. AOPA-derived fields stay out of public outputs (see `38_schools.py`'s public contact policy), so an AOPA-only club can enter the public release only once its own website or another public source confirms it.
- ASA dealers are pilot shops, not schools (45 entries; e.g. a parts supplier listed as a flight school).
- The airport file overlaps 65's NASR INSTR/RNTL pass; 427 of its 594 airports carry an on-field FBO candidate.

## Next

Drop skydiving, airport and jet-FBO names; crawl the 463 with websites and classify them as in 60 (Haiku, about $0.50 per 700 sites); for website-less clubs, look for a public source other than AOPA; hand-check 30 again; then add through 57/61 with provenance.

```
cd research/FlightSchoolMapping
python3 -I scripts/probes/match_external_school_lists.py output/flight_line.json \
  output/airports.csv,data/ourairports_airports.csv data/research-2026-11/external data/research-2026-11/external/c_master.csv
python3 -I scripts/probes/confirm_external_matches.py output/flight_line.json data/research-2026-11/external/c_master_matched.csv
```
`c_master.csv` is the master list reduced to source, name, city, state, website, entity_id, category, sources, faa_lid, phone.
