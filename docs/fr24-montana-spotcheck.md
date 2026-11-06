# Montana-LLC aircraft: Flightradar24 spot check (2026-11-06)

**Why.** The deck's notes say 86% of Montana-LLC aircraft are based outside Montana. That figure comes from inferring each plane's home airport from adsb.lol and OpenSky data. This check reads Flightradar24's flight history for a random sample to see where those planes actually land.

**Method.** The sample frame is the FAA registry on trogdor: valid registrations with a Montana address, registrant type LLC, and an inferred US home airport in `output/adsb_activity.csv`. That gives 776 aircraft, 719 of them (92.7%) inferred outside Montana. This frame is smaller than the 1,167 in hackers-2026-romp.md (different registry date and home table). From it, 30 aircraft were drawn with `random.seed(1107)`. Each one's flight history page was read by hand-paced browsing in David's logged-in FR24 Gold account (David's choice of a spot check over automation at scale). For busy aircraft the first page shows the most recent 100 flights; otherwise it covers 365 days. Each landing airport was mapped to its state with `output/airports.csv`. Tails are not recorded here; per-tail data stays in the session scratchpad.

## Results

| | Aircraft |
|---|---|
| Drawn | 30 |
| No history shown on FR24 (mostly jets, likely owner-blocked) | 8 |
| With history | 22 |
| Our inference: based outside Montana | 19 of 22 |
| FR24: Montana is the most frequent landing airport | 7 of 22 |
| FR24: no Montana landing in the window | 13 of 22 |
| Inferred outside Montana, but FR24 shows Montana as the most frequent airport | 4 of 19 |

**Reading.**
- Most Montana-LLC aircraft really do live elsewhere. 15 of 22 (68%) mostly land outside Montana, and 13 of 22 never landed there in the window.
- Our 86% overstates it. In this sample our inference put 4 Montana-based planes in other states (Wisconsin, California, Utah, Colorado). adsb.lol and OpenSky have few receivers in Montana, so a plane's out-of-state trips are seen and its home flying isn't.
- With 22 aircraft the uncertainty is wide: 15 of 22 has a 95% interval of roughly 45% to 86%. Say "about two-thirds, maybe more" until the API run.
- Our ADS-B home-base inference is biased wherever receiver coverage is thin, Montana included. That affects inferred school airports in rural states too.

## Next

Run the Essential API over the full frame: flight summary light for 12 months, landing days by state (~100k credits for 776 aircraft). Re-check inferred home bases against FR24 in low-coverage states. See [fr24-api-plan.md](fr24-api-plan.md).
