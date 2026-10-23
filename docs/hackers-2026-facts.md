# Hackers 2026 fact sheet

Generated 2026-10-23 from release `v0.2-20261023` by `research/FlightSchoolMapping/scripts/43_talk_facts.py`.
Window 2026-03-05 → 2026-10-22 (230 days of the adsb.lol archive). Registry snapshot 2026-10-23. Regenerate rather than edit.

## 1. The registry vs the sky

- The FAA counts 173,663 ADS-B-equipped US civil aircraft (as of 2026-10-01; 136,553 on 1090ES, 35,762 on UAT, 1,348 dual). adsb.lol's volunteers heard 163,876 registered aircraft broadcast their own ADS-B in this window: 94% of the FAA's count. The FAA's count may include aircraft no longer on the registry, so treat it as a ceiling, not a matched denominator.
- 31,295 of those were heard only as FAA ADS-R rebroadcasts: UAT aircraft the 1090 network hears because the FAA's ground stations repeat them. A floor on UAT aircraft heard, since direct UAT receivers may report as plain ADS-B.
- 317,972 aircraft on the FAA registry; 166,135 (52%) were heard at least once in the window. 151,837 were silent.
- Of those heard, 163,876 broadcast their own ADS-B; 2,259 were heard only via MLAT or TIS-B.
- Gyroplanes: 431 registered, 171 heard.
- Single-engine pistons built in the 1940s: 16% heard (20,255 registered). Built in the 2020s: 87% (11,703).
- 37,773 single-engine pistons have no year of manufacture on file.
- Caveat to say out loud: silent is not the same as not flying. Aircraft without ADS-B Out may fly legally outside
  rule airspace, and coverage is thin in places. Every claim is "heard / not heard by this network in this window".

## 2. Finding flight schools from the sky

- Model: gradient-boosted trees on flight behavior plus two priors, 5-fold cross-validated, the label-derived prior recomputed inside each fold. Average precision 0.405 against website labels (base rate 0.039); at the 0.5 threshold, recall 0.846 on known school aircraft and precision of at least 0.229.
- 384 training operations (non-individual registrant, ≥ 3 training-like aircraft, no tail on any known fleet) missing from our school list.
- Hand check of 20 random operations (2026-10-23, earlier window): 12 schools, 2 clubs, 3 leasebacks onto a school's
  line, 0 non-training, 3 unclear holding LLCs: 85% training-related. Several were schools our crawl missed, so say
  "missing from our list", not "unknown to the web".
- Most of the lift comes from the priors (which models schools fly, how many active aircraft the registrant holds),
  not from behavior alone (NEGATIVE_RESULTS.md).

## 3. How trackable you are

- Home base inferred for 53% of the 165,577 aircraft that flew; 79% correct against the airport schools list for their own aircraft (n = 2,636).
- Weekday routine does not predict flying: 81.5% vs a 81.5% baseline (138,503 aircraft). Don't claim "predictable schedules".
- 14,672 aircraft on the FAA's LADD opt-out list were visible on adsb.lol in the window (~3,079 a day). LADD binds FAA-sourced feeds (SWIM subscribers must filter); open community feeds don't.
- 452 PIA (privacy ICAO address) addresses observed. Aggregates only;
  we do not publish PIA-to-tail links. Prior art: Michel & Strohmeier, "Flying in Private Mode".

## Sources to cite on a slide

- Contains information from adsb.lol, made available under the ODbL. FAA Releasable Aircraft Registry (public domain).
- Any OpenSky-derived count: cite "The OpenSky Network, https://opensky-network.org" and send them the talk link.
