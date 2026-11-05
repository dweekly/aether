# Hackers 2026: the strangest things the data turned up

A ranked list of the funny, weird and questionable findings from the flight-school project, for the talk's "strangest things" slide. Each item gives the number, where it is proven, and the risk of showing it. Compiled 2026-11-04 by reading the docs on `hackers-talk` and `hackers-airports` and re-running registry queries against the 2026-10-23 FAA registry snapshot. Rules for the slide: no private individuals, no tail numbers next to owner-chosen LLC names, no claim that any particular owner avoided tax.

## Top ten

1. **FAA traffic counts that never change.** Lampson Field (1O2, Lakeport CA) shows 37,000 itinerant and 37,000 local GA operations every year from 1997 to 2024 in the FAA Terminal Area Forecast, and the forecast holds them flat. Across 2,584 non-towered fields, 29% of GA itinerant counts are round thousands (towered: 1%), 67% are unchanged for 5+ years and 44% for 10+. Source: `hackers-airports:docs/hackers-2026-facts.md` §10. Risk: a public airport; say "estimates nobody updates", since the TAF labels non-towered counts as estimates.
2. **The Montana LLC mill.** One Kalispell registered agent's suite is the registry address of 665 aircraft; LLCTLC in Helena sells an aircraft package that "Includes a Hangar Lease" and says "pay $0 in sales tax" ([talk doc](hackers-2026-talk.md), Montana bullet). Of 1,167 Montana-address LLC registrations with an inferred US home airport, 1,004 (86.0%) are based outside Montana, against 23.3% for other LLCs; most often California, Texas, Florida, Arizona (`hackers-airports:docs/hackers-2026-romp.md`). Idaho's Tax Commission (docket 0-384-096-256, 2023) combined an FAA purchase record with FlightAware landing days and upheld use tax on a Montana-LLC aircraft. The Kalispell agent's registered trade names include "Save on Montana Luxury Car Taxes" (romp, from a Montana SOS lookup on 2026-10-27, not redone). Risk: named businesses, quoting their own advertising; never tie to a tail or owner.
3. **The HTML cleaner deleted `<body>`.** 812 of 1,992 schools' HTTP page sets kept under 5% of their text (41%); Elementor's `elementor-widget` class still empties about 100 pages after the fix (`docs/hackers-2026-extraction-audit.md`, ROADMAP).
4. **The backup that verified nothing.** `rclone check | grep -q` under `pipefail`: grep exits on its first match, rclone dies of SIGPIPE, and the `if` read "no problems" because problems were found. Three R2 prefixes held zero objects on 2026-08-24 while the log said "verified OK" nightly; 14 GB of ADS-B history sat on one disk (`research/FlightSchoolMapping/GOTCHAS.md`). Separately, a Homebrew upgrade removed the venv's Python and the collector cron failed every 15 minutes for months, leaving an 85-day hole.
5. **The most-registered new aircraft of 2026 is a delivery drone.** Zipline P2 Zip: 2,121 registrations with 2026 certificate dates, against 517 Cirrus SR22Ts (DJI Agras T100 next at 751). The registry's most common "address" is blank: 3,658 records with no name, street or state (romp; registry query).
6. **Registry typos.** Valid owners include UNTIED AIRLINES INC and SKYWEST AILINES INC (one registration each, checked 2026-11-04). Year-built oddities (a TU206G recorded as built in 1918, a PA-22 in 1910) are in the romp but marked *verify* there; not on the slide.
7. **Owner-chosen LLC names.** Valid registrations: WIFE APPROVED LLC (2), 1 TROPHY HUSBAND LLC, MID-LIFE CRISIS AVIATION LLC, EXPENSIVE HOBBY LLC, AIRPLANE MONEY PIT LLC (each checked 2026-11-04); the romp also lists FLYING MONEY PIT LLC, DEAD BROKE LLC, GONE BROKE LLC, 2 BROKE PILOTS LLC and SECRET SQUIRREL AEROSPACE LLC. Risk: single-plane LLCs lead to people; names only.
8. **School domains turned gambling sites.** flighttimeaviation[.]com hosts an "Aviator" crash-gambling game; aeronationalinc[.]com redirects to a slots domain; 111 school sites now redirect elsewhere and 68 read as parked (`docs/hackers-2026-extraction-audit.md`, `docs/hackers-2026-march-vs-november.md`). Risk: show domains, not school names; the schools may simply have closed.
9. **Opted out of tracking, still tracked.** 15,214 aircraft on the FAA's LADD list were seen on adsb.lol in the window, about 3,013 a day; LADD binds only FAA-sourced feeds (`hackers-airports:docs/hackers-2026-facts.md`). Aggregate only.
10. **Ghosts with fresh paperwork.** 121,024 valid registrations were silent in the window, in the adsb.lol archive since January 2024 and in every OpenSky Monday sample 2017-2022; 31,052 of them got a new certificate after 2023-10-24 (`hackers-airports:docs/hackers-2026-facts.md`). Say aloud: silent isn't grounded; planes without ADS-B Out can fly legally where it isn't required.

## Runners-up

- One host blocked 102 schools at once: SiteGround refuses us on 102 of its 103 school sites; WordPress.com refuses our user agent on all 33 (`docs/hackers-2026-slide-research.md`).
- The scoring bug was ours: 50 of 81 "bad" tails came from the scorer prefixing N to Canadian tails (C-FEFY became NCFEFY) (`docs/hackers-2026-talk.md`).
- The "372" was one draw of a non-deterministic model; six runs gave 353 to 376.
- Google listed every flight-training place as OPERATIONAL, including one the speaker knows closed.
- Eight Sling LSAs at one Florida airport, each in its own Montana LLC at one agent's suite (keep the school unnamed).
- 44 school-fleet aircraft carry an NTSB record coding them destroyed, 28 with a fatality aboard: rebuilt and re-registered, or the record is wrong (`docs/hackers-2026-ntsb.md`). Don't name tails.
- Trustee-registered N-planes live abroad: 23.8% made at least 80% of departures from non-US airports, against 0.6% for others (romp).
- Christmas Day 2025: 12,629 aircraft airborne, against 28,181 the Thursday before (romp).
- Balloons are nearly invisible: 23 of 4,762 lighter-than-air aircraft were heard (facts).
- Pipeline howlers: a CFI regex that never matched (`cfi\\b` in a raw string), "ATP Flight Academy" carrying auburn.edu, a state aviation authority's 44 Pipers counted as a school, a school keyed by sites.google.com itself (ROADMAP).
- No 100-hour-inspection peak in the flight data, though we expected one (`hackers-airports:docs/hackers-2026-talk-options.md`).

Left out on purpose: an individual holding 92 aircraft (a private person).
