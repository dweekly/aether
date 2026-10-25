# Hackers 2026 fact sheet

Generated 2026-10-25 from release `v0.3-20261023` by `research/FlightSchoolMapping/scripts/43_talk_facts.py`.
Window 2025-10-23 → 2026-10-22 (363 days of the adsb.lol archive). Registry snapshot 2026-10-23. Regenerate rather than edit.

## 1. The registry vs the sky

- The FAA counts 173,663 ADS-B-equipped US civil aircraft (as of 2026-10-01; 136,553 on 1090ES, 35,762 on UAT, 1,348 dual). adsb.lol's volunteers heard 169,344 registered aircraft broadcast their own ADS-B in this window: 98% of the FAA's count. The FAA's count may include aircraft no longer on the registry, so treat it as a ceiling, not a matched denominator.
- 33,265 of those were heard only as FAA ADS-R rebroadcasts: UAT aircraft the 1090 network hears because the FAA's ground stations repeat them. A floor on UAT aircraft heard, since direct UAT receivers may report as plain ADS-B.
- 317,972 aircraft on the FAA registry; 171,646 (54%) were heard at least once in the window. 146,326 were silent.
- Of those heard, 169,344 broadcast their own ADS-B; 2,302 were heard only via MLAT or TIS-B.
- Gyroplanes: 431 registered, 178 heard.
- Single-engine pistons built in the 1940s: 17% heard (20,255 registered). Built in the 2020s: 88% (11,703).
- Split by airworthiness class, decade by decade from the 1940s to the 2020s: standard-category single-engine pistons 17% → 43% → 61% → 72% → 80% → 83% → 89% → 94% → 98% heard; experimentals 23% → 36% → 18% → 17% → 24% → 30% → 44% → 56% → 72%. The newer the certified plane, the likelier it is heard.
- The 1990s dip in the blended chart is the experimentals: 4,630 of 9,205 1990s single pistons are experimental.
- 37,773 single-engine pistons have no year of manufacture on file.
- Caveat to say out loud: silent is not the same as not flying. Aircraft without ADS-B Out may fly legally outside
  rule airspace, and coverage is thin in places. Every claim is "heard / not heard by this network in this window".

## 2. Finding flight schools from the sky

- Model: gradient-boosted trees on flight behavior plus two priors, 5-fold cross-validated, the label-derived prior recomputed inside each fold. Average precision 0.415 against website labels (base rate 0.037); at the 0.5 threshold, recall 0.854 on known school aircraft and precision of at least 0.234.
- 372 training operations (non-individual registrant, ≥ 3 training-like aircraft, no tail on any known fleet) missing from our school list.
- Hand check of 20 random operations (2026-10-23, earlier window): 12 schools, 2 clubs, 3 leasebacks onto a school's
  line, 0 non-training, 3 unclear holding LLCs: 85% training-related. Several were schools our crawl missed, so say
  "missing from our list", not "unknown to the web".
- Most of the lift comes from the priors (which models schools fly, how many active aircraft the registrant holds),
  not from behavior alone (NEGATIVE_RESULTS.md).

## 3. How trackable you are

- Home base inferred for 54% of the 171,123 aircraft that flew; 80% correct against the airport schools list for their own aircraft (n = 2,681).
- Weekday routine does not predict flying: 82.9% vs a 82.9% baseline (152,525 aircraft). Don't claim "predictable schedules".
- 15,214 aircraft on the FAA's LADD opt-out list were visible on adsb.lol in the window (~3,013 a day). LADD binds FAA-sourced feeds (SWIM subscribers must filter); open community feeds don't.
- 541 PIA (privacy ICAO address) addresses observed. Aggregates only;
  we do not publish PIA-to-tail links. Prior art: Michel & Strohmeier, "Flying in Private Mode".

## 4. What renting costs, and how much the planes fly

- 2,760 rental aircraft carry a wet rate on their school's own page and a tail on the registry (rates as published when crawled; the fleet file was built 2026-03 → 2026-08).
- 164 of them (6%) were never airborne in the window: on the rate sheet, not in the sky.
- Those that flew: median 199 observed hours a year, annualized (quartiles 79–370).
- Cessna 172s (1,201): median $179/h wet (quartiles $160–$195), median 293 hours a year (quartiles 126–464); 39 never flew.
- Price does not track use: Spearman rho 0.06 between a 172's rate and its hours.
- Rate x observed hours for a 172 that flew: median $54,162 a year (quartiles $24,999–$84,569).
- Observed hours are a floor: time flown outside receiver coverage is not counted.

## 5. When the silent ones went quiet

- Of the 146,326 aircraft silent in the window, 14,007 (10%) were heard by adsb.lol between 2024-01-01 and the window's start: they went quiet recently.
- 8,071 (6%) more were last seen in OpenSky's Monday samples (257 Mondays, 2017-06-05 → 2022-06-27); by year last seen: 2017 483, 2018 985, 2019 1,150, 2020 1,488, 2021 1,931, 2022 2,034.
- 124,248 (85%) appear in neither.
- How much a Monday sample misses: of the 90,213 aircraft heard in this window whose registration certificate predates 2022-06-27, 66,464 (74%) are on at least one Monday. A regular flier usually lands in the sample, but "neither" still holds some that flew.
- An address follows the N-number, so a hit years ago may be an earlier airframe that held the same number.
- OpenSky's tables stay internal (their terms forbid redistribution and require de-identification): counts only.

## Sources to cite on a slide

- Contains information from adsb.lol, made available under the ODbL. FAA Releasable Aircraft Registry (public domain).
- Any OpenSky-derived count (section 5): cite exactly "The OpenSky Network, http://www.opensky-network.org" and send
  OpenSky a copy of or link to the talk (their data license, section 4(i)). The license covers non-profit research and
  education only.
