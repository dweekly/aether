# Hackers 2026: the March pages re-read by today's models, the cost of learning to fly, and the bad tails

Workstreams 1, 2 and 5 of the [research plan](hackers-2026-research-plan.md), run 2026-11-03 on trogdor. Every number below comes from a script in `research/FlightSchoolMapping/scripts/` run on the March crawl caches. The commands are at the end. Outputs are under `research/FlightSchoolMapping/data/research-2026-11/` in the production checkout: `model-rerun/` (requests, results, scores, learn-to-fly) and `error-audit/`.

## Summary

- **Same pages, same prompt: Haiku 5.5 did the March job for $15 instead of $100**, with the same accuracy on the tails it reported. Of its tails, 98.6% are well-formed and 98.1% of those are on the FAA registry (March, with Sonnet 4.6: 98.5% and 98.0%). Make agreement is 99.6% and the home-base match 77% against a 0.4% shuffled control. On the browser pass, where the requests are identical to March, Haiku cost $1.38 against Sonnet 4.6's $23.99.
- **Sonnet 5.5 costs about what Sonnet 4.6 did** on identical requests ($25.90 against $23.99 for the browser pass). Its price per token is a third lower, but its tokenizer counts the same pages as 1.4 to 1.6 times as many tokens, and the structured-output schema adds about 1,600 tokens per request.
- **Correction to a working assumption: newer is not uniformly better here.** On 150 randomly chosen schools, all three models found exactly the same registered tails. The difference is on the few sites that list dozens of aircraft in one table or PDF. On 15 such schools March found 231 registered tails. Haiku 5.5 found 146 at effort `low` and 188 at `medium`; Sonnet 5.5 found 59 at `low` and 128 at `high`. At low effort the 5.5 models tend to list one entry per aircraft type rather than one per tail.
- **What it costs to learn to fly, as posted on school websites:** median CFI rate $70 an hour, median aircraft wet rate $175 an hour, and a median private pilot certificate of $12,000 (interquartile range about $9,000 to $15,000). Only one school in seven posts any training price at all: 347 of 2,453 sites in the Haiku run.
- **The hand-check gate failed for both runs.** Haiku got 34 of 35 checked prices right and Sonnet 5.5 got 29 of 31. The three misses are of two kinds. Twice a "dual" rate (airplane plus instructor) was read as the instructor rate. Once a $72,651 twelve-month professional pilot program was entered as a private pilot price. The medians barely move: the plausible-range medians are the same numbers.
- **The 81 bad March tails were mostly not model errors.** 50 are Canadian registrations on Canadian schools' pages, 4 are simulator IDs and 3 are correctly read vintage "NC" markings. 7 are typos on the school's own page, and in 5 of those a one-character variant of the same make and model is registered. 2 are model misreads, and 15 appear verbatim on the page without settling whether they are typos or numbers missing from the registry.

## How it was run

`extract_v2.py` rebuilds the March requests from the cached pages exactly as `09b_fleet_extract.py` (HTTP) and `09c_browser_extract.py` (browser) did. Its `selftest` checks that every prompt is byte-identical to what 09b and 09c build, apart from the inserted pricing block. It covers 2,955 HTTP and 1,928 browser requests. The details:

- **Prompt:** the March system and user prompt text, imported from 09b and 09c, plus one block before "Web pages:" asking for the training prices. The answer is constrained by structured output (`output_config.format`, a JSON schema with the March field names plus the new fields) instead of "return only JSON". Hence 0 unparseable answers, against 5 in March.
- **Page cap:** 50,000 characters per page for HTTP and 40,000 for browser. The 40,000 is 09c's cap, so "the same 50,000-character cap" in the brief holds only for the HTTP pass.
- **Settings:** adaptive thinking, effort `low` for both models (Sonnet 5.5 defaults to `high`, Haiku 5.5 to `medium`), `max_tokens` 32,000, Message Batches. Every request ended `end_turn`; there were no refusals and no truncations.
- **The HTTP school set is not identical to March.** The March HTTP batch used a crawl index of about 3,115 schools that no longer exists. Today's `fleet_crawl_index.json` has 2,958, and its positions no longer match the March custom_ids (`21_build_flight_line.py` already recovers that join by source URL). The re-run covers today's index: 2,955 requests against March's 3,079 calls. The browser pass is identical to March, all 1,928 custom_ids with the same domains, so it is the like-for-like comparison.
- **Sonnet 5.5 ran on a sample of the HTTP pass.** A full Sonnet 5.5 run would have cost about $120 on Haiku's measured token counts (the two models count tokens identically), more than the budget left after Haiku. It ran on the whole browser pass and on a seeded random 55% of the HTTP requests (1,625; ids in `model-rerun/http/sonnet_sample_ids.txt`). Haiku is scored on the same 55% for the paired HTTP column.
- **Scoring:** `49_scrape_facts.py --results ... --index ...` applies exactly the checks and gates of the March facts to each run. Its default output (the March facts) is byte-identical to before; this was checked with `cmp` against the old script on the same inputs.

## Side by side

The whole of each run. The HTTP school sets differ (see above), so tail counts are not paired here.

| | March Sonnet 4.6 (both passes) | Haiku 5.5 (both passes) | Sonnet 5.5 (browser + 55% HTTP) |
|---|---:|---:|---:|
| Calls succeeded | 4,909 | 4,883 | 3,553 |
| Errored or unparseable | 103 | 0 | 0 |
| Tokens in / out (M) | 55.88 / 2.19 | 106.28 / 2.71 | 69.6 / 1.86 |
| Cost at batch prices | $100.22 | $15.15 | $78.90 |
| (school, tail) claims | 5,586 | 5,536 | 4,183 |
| Distinct tails | 4,296 | 4,066 | 3,538 |
| Well-formed | 4,233 (98.5%) | 4,010 (98.6%) | 3,432 (97.0%) |
| On the registry | 4,147 (98.0%) | 3,932 (98.1%) | 3,362 (98.0%) |
| Deregistered only | 68 | 65 | 55 |
| Not found anywhere | 18 (0.4%) | 13 (0.3%) | 15 (0.4%) |
| Make agrees | 4,903/4,934 (99.4%) | 4,817/4,838 (99.6%) | 3,633/3,648 (99.6%) |
| Flew in window | 3,857 (93.0%) | 3,668 (93.3%) | 3,151 (93.7%) |
| Home match | 3,275/4,184 (78.3%) | 3,126/4,059 (77.0%) | 2,390/2,963 (80.7%) |
| Shuffled control | 0.5% | 0.4% | 0.7% |

**Browser pass, identical requests** (all 1,928 custom_ids and domains as in March; the like-for-like comparison):

| | March Sonnet 4.6 | Haiku 5.5 | Sonnet 5.5 |
|---|---:|---:|---:|
| Calls succeeded | 1,928 | 1,928 | 1,928 |
| Errored or unparseable | 3 | 0 | 0 |
| Tokens in / out (M) | 11.03 / 0.99 | 20.46 / 1.25 | 20.46 / 1.09 |
| Cost at batch prices | $23.99 | $1.38 | $25.90 |
| (school, tail) claims | 2,641 | 2,542 | 2,547 |
| Distinct tails | 2,612 | 2,515 | 2,520 |
| Well-formed | 2,566 (98.2%) | 2,477 (98.5%) | 2,422 (96.1%) |
| On the registry | 2,525 (98.4%) | 2,438 (98.4%) | 2,376 (98.1%) |
| Deregistered only | 33 | 33 | 36 |
| Not found anywhere | 8 (0.3%) | 6 (0.2%) | 10 (0.4%) |
| Make agrees | 2,368/2,377 (99.6%) | 2,300/2,308 (99.7%) | 2,245/2,256 (99.5%) |
| Flew in window | 2,380 (94.3%) | 2,301 (94.4%) | 2,242 (94.4%) |
| Home match | 1,718/2,059 (83.4%) | 1,577/1,945 (81.1%) | 1,514/1,827 (82.9%) |
| Shuffled control | 0.6% | 0.6% | 0.5% |

**HTTP pass, the 55% sample** (identical requests for both 5.5 models):

| | Haiku 5.5 | Sonnet 5.5 |
|---|---:|---:|
| Calls succeeded | 1,625 | 1,625 |
| Errored or unparseable | 0 | 0 |
| Tokens in / out (M) | 49.14 / 0.81 | 49.14 / 0.77 |
| Cost at batch prices | $8.03 | $53.00 |
| (school, tail) claims | 1,651 | 1,636 |
| Distinct tails | 1,370 | 1,370 |
| Well-formed | 1,355 (98.9%) | 1,346 (98.2%) |
| On the registry | 1,327 (97.9%) | 1,317 (97.8%) |
| Deregistered only | 22 | 22 |
| Not found anywhere | 6 (0.4%) | 7 (0.5%) |
| Make agrees | 1,402/1,413 (99.2%) | 1,388/1,392 (99.7%) |
| Flew in window | 1,220 (91.9%) | 1,223 (92.9%) |
| Home match | 828/1,177 (70.3%) | 876/1,136 (77.1%) |
| Shuffled control | 1.4% | 0.9% |

Shares in brackets follow the March facts: well-formed is a share of distinct tails, registry a share of well-formed, flew a share of registered. All three runs pass `49_scrape_facts.py`'s gates: at least 1,000 distinct tails and 500 home checks, and a shuffled control under a third of the real match.

## Where the models differ: long fleet lists

The browser pass is paired request by request. Of the (school, registered tail) pairs, March Sonnet 4.6 found 172 that Sonnet 5.5 at effort `low` did not, and Sonnet 5.5 found 21 that March did not. For Haiku 5.5 at `low` the figures are 125 and 36. The misses are concentrated: 167 of Sonnet 5.5's 172 come from 10 schools. Examples are plusoneflyers.org (a 77-row fleet table, of which Sonnet 5.5 listed 10 aircraft types with 9 tails) and flyjetaccess.com (44 tails in a fleet PDF; both 5.5 models returned 8 type entries with no tails).

To see whether that is the effort setting, those 15 gap schools and 150 other browser schools drawn at random (seed 20261104) were re-run at each model's default effort. That cost $2.55 for 330 requests (`model-rerun/browser/*-effort-*`). The number is registered tails found:

| | March Sonnet 4.6 | Haiku 5.5 `low` | Haiku 5.5 `medium` | Sonnet 5.5 `low` | Sonnet 5.5 `high` |
|---|---:|---:|---:|---:|---:|
| 150 random schools | 140 | 140 | 140 | 140 | 139 |
| 15 long-list schools | 231 | 146 | 188 | 59 | 128 |
| Cost of these 165 requests at batch prices | $2.22 (March) | $0.12 | $0.14 | $2.22 | $2.41 |

On ordinary sites the models agree exactly. On long lists, raising the effort recovers part of the gap but not all of it. The 15 schools were picked because Sonnet 5.5 `low` missed tails there, so its row is biased against it; Haiku's rows are a fairer read. These subsets are too small for 49's gates, which need 1,000 distinct tails, so their 49 runs exit 1 on size. The comparison above is the paired count, not a gated run. **For the November re-crawl and the 372 discovery runs, Haiku 5.5 at `medium` is the better setting:** it costs about 17% more than `low` here and recovers most of the long-list tails. `extract_v2.py` still defaults to `low` so that runs already in flight stay comparable; pass `--effort medium`.

Other differences:

- **Format errors:** Haiku occasionally reports a model designation as a tail (8KCAB, 7ECA, THTA624R). Sonnet 5.5's lower well-formed share (96.1% on browser) is mostly Canadian registrations it lists faithfully: 81 of its 98 malformed browser tails, on Canadian schools such as pacificflying.com (30) and proifr.com (24).
- **Haiku's second rate card:** prompts over 100,000 tokens cost five times as much on Haiku 5.5. 204 HTTP requests (7%) were that long and were $11.39 of Haiku's $13.77 HTTP bill.

## What it costs to learn to fly

From `51_learn_to_fly.py`, one record per school domain (the HTTP pass where both passes read a site). Haiku 5.5 is the main run because it covers every school; Sonnet 5.5 covers the browser pass and 55% of HTTP, and serves as a cross-check. Medians are over schools that post the price. The "plausible" column drops values outside a sanity range (CFI $25-250, private pilot $3,000-60,000, simulator $10-400, dues $5-1,000 a month); the ranges are reported, not hidden.

| Field | Schools posting it (Haiku) | Median (Haiku) | Plausible median and IQR (Haiku) | Schools posting it (Sonnet 5.5) | Plausible median (Sonnet 5.5) |
|---|---:|---:|---:|---:|---:|
| Instructor (CFI) rate per hour | 222 | $70 | $70 ($60-82), n 215 | 231 | $70 ($60-80) |
| Private pilot certificate price | 151 | $11,655 | $12,000 ($9,060-15,000), n 136 | 160 | $12,000 ($9,425-15,330) |
| Simulator rate per hour | 127 | $75 | $75 ($50-90) | 135 | $75 ($50-99) |
| Club initiation fee | 26 | $400 | $400 ($193-663) | 26 | $325 ($169-800) |
| Club dues per month | 34 | $50 | $50 ($37-76) | 33 | $56 ($40-98) |
| Block-time or prepay discount offered | 80 | | | 88 | |
| Aircraft wet rate (school median) | 310 | $175 | $175 ($155-200) | 309 | $175 ($156-202) |
| Any training price | 347 of 2,453 sites | | | 357 of 2,251 | |

- **Private pilot prices:** most are estimates of the total ("a budget of $15,000 is realistic"), not fixed packages. In Haiku's run 92 of 151 are estimates (median $12,162) and 59 are fixed prices (median $9,689, or $10,698 within the plausible range). Those outside the range are partial packages ($59 to $1,290: ground school, discovery flights) and career programs ($65,000 to $72,300).
- **Instructor included in rental:** schools that say rental rates exclude the instructor outnumber those that include it, 212 to 10 (Haiku; 252 to 14 for Sonnet 5.5). Most sites don't say.
- **By region** (Haiku; census regions; n at least 10): CFI rate Midwest $65 (n 35), South $69 (67), Northeast $75 (21), West $75 (66). Private pilot price Midwest $10,852 (21); Northeast, South and West $12,000 (n 12, 55 and 39). Simulator Midwest $65, South $74.50, West $70.
- **By state** (n at least 10): CFI rate CA $77.50 (28), FL $69 (19), TX $70 (11). Private pilot price CA $13,300 (18), FL $11,275 (18), TX $13,500 (10). Sonnet 5.5 agrees within a few dollars (CA $80 and $15,000, FL $70 and $10,301).
- **Caveat:** these are the prices schools chose to post, read off pages cached in March 2026. Schools that post prices may not be typical.

### Hand check (the gate): failed, 63 of 66 prices right

20 schools per run, drawn at random (seed 20261105) from those with any training price. For every number, `51_learn_to_fly.py --check` prints where it appears on the pages the model was given, and I read each context. The script's own check, that every number appears on some page, passed for both runs: no number was invented or computed. The gate asks more than that, that every price is the right kind of price, and it failed:

- **Haiku 5.5: 34 of 35 prices right.** Wrong: westernoregonaviation.com, where the instructor rate of $175 is "20 hours dual @ $175.00 per hour", the airplane-plus-instructor rate (solo is $145). Its "instructor included in rental: false" is wrong for the same reason.
- **Sonnet 5.5: 29 of 31 prices right.** Wrong: cortezflyingservice.com, where the instructor rate of $180 is "Flight Instruction Dual - $180/hour" against "Airplane Rental Solo - $130/hour", the same error. And flypureflight.com, where the private pilot price of $72,651 is a twelve-month professional pilot program with six ratings.
- Checked and right include range low ends ("$12k to $12.5k" as $12,000), member versus non-member simulator rates, an equity buy-in recorded as a club initiation fee, and a $3,000 prepay minimum recorded as a block deposit.

One wrong value among a few hundred barely moves a median, so the medians above hold; individual schools' extracted prices should not go on a slide unchecked. The check files are `model-rerun/learn-to-fly/<model>.check.md`.

## Error audit: the 81 bad tails in the March output

`49_scrape_facts.py` finds 63 malformed tails and 18 that are well-formed but on neither the registry nor the deregistered file. `50_error_audit.py evidence` finds, for each one, the cached page it came from and the text around it, plus any registered one-character variant of the same make. Each tail was then classified by reading the page; the classifications are in [hackers-2026-error-audit.csv](hackers-2026-error-audit.csv). `50_error_audit.py check` passes: every one of the 81 tails has a class and a URL.

| | Typo on the school's page | Model misread | Non-US registration or simulator ID | Vintage NC marking (correct) | Unclear | Total |
|---|---:|---:|---:|---:|---:|---:|
| Malformed | 7 | 1 | 52 | 3 | 0 | 63 |
| Not found | 0 | 1 | 2 | 0 | 15 | 18 |
| **All** | **7** | **2** | **54** (50 Canadian, 4 simulators) | **3** | **15** | **81** |

- **A correction to the March caveat**, which said "not found" mixes school typos and model misreads. Of the 18 "not found" tails, 15 are on the school's page as the model reported them (13 verbatim, 2 without the N). Only 1 is a misread: N2195S was lifted from an instructor's email address. 2 are simulator IDs.
- **"Unclear" means** the page shows the number but nothing settles whether it is a typo or a registration newer than, or missing from, the 2026-10-23 registry. Several are charter jets, light-sport aircraft, or listings marked "coming soon". For 2 of them (N15RY, N666CK) a one-character variant of the same model is registered, which points to a typo.

| Tail (as the model wrote it) | Check | Class | Page | What the page shows |
|---|---|---|---|---|
| N13006C | malformed | page_typo | [learntoflysacramento.com](https://learntoflysacramento.com/general-aviation-pilot-training-executive-flyers-sacramento) | page lists '2007 Cessna 172S N13006C'; registered N1306C is a Cessna 172S |
| N16186T | malformed | page_typo | [atlasaviation.com](https://atlasaviation.com/cessna-172sp-skyhawk-n16186t) | only in the school's URL slug ...-skyhawk-n16186t; registered N6186T is a Cessna 172S |
| N16OME | malformed | page_typo | [silverhawkaviation.net](https://www.silverhawkaviation.net/aircraftinfo) | page shows letter O; the school's series N160ME-N169ME are registered Cessna 172S |
| N318OU | malformed | page_typo | [flyredarrow.com](https://www.flyredarrow.com/about/our-fleet) | page shows letter O; registered N3180U is a Cessna 182F, the model the page names |
| N411OS | malformed | page_typo | [kenmoreairharbor.com](https://www.kenmoreairharbor.com/aircraft-refurbishment-and-modification.html) | page heading shows letter O (no I or O allowed in N-numbers); no registered one-edit variant |
| N725295 | malformed | page_typo | [heritageairllc.com](https://heritageairllc.com/our-fleet) | page shows N725295 (six digits); registered N72529 is a Cessna 140, the model the listing names |
| N87IOU | malformed | page_typo | [savannahaviation.com](https://www.savannahaviation.com/flight-school-fleet) | page shows 'Cessna 172-F N87IOU' with letters I and O; no registered one-edit variant |
| N2195S | not found | model_misread | [hiltonheadflighttraining.com](http://www.hiltonheadflighttraining.com) | taken from the instructor's email address n2195s@..., not from an aircraft listing |
| N472KES | malformed | model_misread | [wvfc.org](https://wvfc.org/aircraft-rentals/advanced-aircraft) | rendered table cell 'N472KESQL2019Cirrus': tail N472KE (registered SR22) run into the airport SQL |
| NC41578 | malformed | antique_nc | [snohomishflying.com](https://www.snohomishflying.com/aircraft-fleet) | vintage NC marking; registered N41578 is a Piper J3C-65 Cub |
| NC71017 | malformed | antique_nc | [rightrudderaviation.com](https://rightrudderaviation.com/product/1946-piper-cub-nc71017) | vintage NC marking (in the page URL); registered N71017 is a Piper J3C-65 Cub |
| NC7987H | malformed | antique_nc | [allsportsaviation.com](https://allsportsaviation.com/about) | vintage NC marking; registered N7987H is a Piper PA-12 |
| N051GX | malformed | non_us_or_sim | [brettaviation.com](https://www.brettaviation.com/aircraft-rental/aircraft-rental-fleet) | page lists a 2014 One G AATD simulator under the ID N051GX |
| N364RB | not found | non_us_or_sim | [aero-sphere.com](https://aero-sphere.com/flight-training/rates) | page labels a Redbird FMX AATD simulator N364RB |
| N855RB | not found | non_us_or_sim | [aero-sphere.com](https://aero-sphere.com/flight-training/rates) | page labels a Redbird LD AATD simulator N855RB |
| SFTI1 | malformed | non_us_or_sim | [specialtyflight.com](http://www.specialtyflight.com/rentalfleet.html) | page labels a Redbird FMX simulator SFTI1 |
| N15RY | not found | unclear | [honakeraviation.com](https://honakeraviation.com/fleet) | page shows 'Hawker 800XP - N15RY' (charter); registered N15RK is a Hawker 800XP, so possibly a page typo |
| N190CE | not found | unclear | [capital-flight.com](https://www.capital-flight.com/flight-training) | page shows SR20 G7+ N190CE; N190CD and N190CT are registered SR20s; may be newer than the registry |
| N20867 | not found | unclear | [plusoneflyers.org](https://plusoneflyers.org/palomar-fleet) | page table lists '20867 Cessna C182P' without the N; not registered |
| N42310 | not found | unclear | [oracleaviation.com](https://www.oracleaviation.com/flight-school-rental-aircraft) | page shows 'Cessna 172M N42310 $180/hr'; not registered, no matching one-edit variant |
| N544TH | not found | unclear | [flylftc.com](https://flylftc.com/aircraft-fleet) | page shows 'Cessna 172S - N544TH' on fleet and contact pages; N444TH and N944TH are registered 172S |
| N624TA | not found | unclear | [westhoustonairport.com](https://www.westhoustonairport.com/aircraftrental/aircraftrental.$html) | rate table shows '172S Analog N624TA/N945SA'; not registered |
| N632HB | not found | unclear | [wingsflighttraining.com](https://wingsflighttraining.com/aircraft-rental) | page shows 'N632HB Aeroprakt A32'; not registered (light-sport, possibly pending) |
| N636TG | not found | unclear | [rickaviation.com](https://rickaviation.com/fleet) | page shows 'Coming Soon: BE-66 Baron N636TG'; not registered |
| N650DR | not found | unclear | [tritonairways.com](https://tritonairways.com/our-fleet) | charter page shows 'Citation Excel XLS+ N650DR'; not registered |
| N666CK | not found | unclear | [midwest-aviation.com](https://midwest-aviation.com/aircraft/beechcraft-58-baron) | page shows 'Registration Number N666CK' for a Baron 58; registered N660CK is a Beech 58, so possibly a page typo |
| N752XF | not found | unclear | [skyeagle.aero](https://skyeagle.aero/about_academy/our-fleet) | page shows 'Tecnam Echo MkII | N752XF'; not registered |
| N7BR | not found | unclear | [fortworthflightschool.com](https://fortworthflightschool.com/piper-7br) | page titled 'Piper 7BR' with no body text; the model added the N |
| N803MT | not found | unclear | [flygriffing.com](https://flygriffing.com/fleet) | charter page lists King Air N803MT; not registered |
| N835FS | not found | unclear | [coloradosportpilot.com](https://www.coloradosportpilot.com) | photo caption 'Remos GX (N835FS)'; not registered |
| N8384F | not found | unclear | [flyquestaviation.com](https://www.flyquestaviation.com/rates) | rate table 'Piper Archer (N8384F / N8439T)'; not registered |
| 12 tails: C-FMBX, C-FMBY, C-FMBZ, C-FNJD, C-FNJF, C-GMZA, C-GMZC, C-GMZP, C-GMZW, C-GZPE, C-GZPO, C-GZPV | malformed | non_us_or_sim (canada) | [cfc.aero](https://cfc.aero/about-cfc/fleet/cessna-152) | Canadian registrations on a Canadian school's fleet page |
| 4 tails: C-FNJB, C-FTDI, C-GTAE, C-GZPK | malformed | non_us_or_sim (canada) | [cfc.aero](https://cfc.aero/about-cfc/fleet/cessna-172) | Canadian registrations on a Canadian school's fleet page |
| 3 tails: C-FEFY, C-GMUF, C-GYPN | malformed | non_us_or_sim (canada) | [islandcoastalaviation.com](https://islandcoastalaviation.com/our-fleet) | Canadian registrations on a Canadian school's fleet page |
| 14 tails: C-FISY, C-FQNC, C-FVPV, C-GCZN, C-GDVP, C-GGCG, C-GHFH, C-GIFM, C-GILY, C-GWCG, C-GWOK, C-GXRE, C-GXRG, C-GZTI | malformed | non_us_or_sim (canada) | [proifr.com](https://proifr.com/about-us/our-fleet) | Canadian registrations on a Canadian school's fleet page |
| 8 tails: C-FIBX, C-GFBB, C-GJKE, C-GQSS, C-GVNU, C-GVVG, C-GZSP, C-GZTZ | malformed | non_us_or_sim (canada) | [proifr.com](https://proifr.com/about-us/our-fleet/cessna-152) | Canadian registrations on a Canadian school's fleet page |
| 1 tail: C-GBJD | malformed | non_us_or_sim (canada) | [proifr.com](https://proifr.com/about-us/our-fleet/cessna-152/c-gbjd) | Canadian registrations on a Canadian school's fleet page |
| 1 tail: C-GBXJ | malformed | non_us_or_sim (canada) | [proifr.com](https://proifr.com/about-us/our-fleet/cessna-152/c-gbxj) | Canadian registrations on a Canadian school's fleet page |
| 7 tails: C-FENF, C-FGTV, C-FPME, C-GEML, C-GLCL, C-GPDP, C-GRTJ | malformed | non_us_or_sim (canada) | [sealandair.ca](https://www.sealandair.ca/fleet.php) | Canadian registrations on a Canadian school's fleet page |

## Spend

| Run | Requests | Recorded cost at batch prices |
|---|---:|---:|
| Haiku 5.5, both passes | 4,883 | $15.15 |
| Sonnet 5.5, browser pass plus 55% of HTTP | 3,553 | $78.90 |
| Effort test (Haiku `medium`, Sonnet `high`) | 330 | $2.55 |
| Smoke test, 5 schools, sync API | 5 | $0.01 |
| **Total** | | **$96.61** of the $110 budget |

Each batch's estimated cost was logged before it was submitted and its recorded usage after, in `model-rerun/<pass>/log.jsonl`. Haiku was estimated at $18.41 and cost $15.15. Sonnet 5.5 was estimated at $79.88 and cost $78.90. Token counting is free.

## Reproduce

On trogdor, from a checkout's `research/FlightSchoolMapping`, with `PY` the production venv, `P` the production `research/FlightSchoolMapping` and `R=$P/data/research-2026-11/model-rerun`:

```
$PY scripts/extract_v2.py selftest --output-dir $P/output
$PY scripts/extract_v2.py build --layout http --index $P/output/fleet_crawl_index.json --pages-dir $P/output/fleet_html --run $R/http
$PY scripts/extract_v2.py build --layout browser --pages-dir $P/output/fleet_browser_text \
    --meta-index $P/output/fleet_crawl_index.json --meta-index $P/output/fleet_browser_index.json --run $R/browser
$PY scripts/extract_v2.py --env-file <repo>/.env estimate|submit|fetch --run $R/http --model claude-haiku-5-5   # likewise browser, sonnet
AETHER_DATA=$P/data $PY scripts/49_scrape_facts.py --release v0.3-20261023 --output-dir $P/output \
    --results http=$R/http/MODEL/results.jsonl --index http=$R/http/index.json \
    --results browser=$R/browser/MODEL/results.jsonl --index browser=$R/browser/index.json \
    --json-out $R/scores/MODEL.json --out $R/scores/MODEL.md --rows-out $R/scores/MODEL.rows.jsonl
$PY scripts/51_learn_to_fly.py --results http=... --index http=... --results browser=... --index browser=... \
    --json-out $R/learn-to-fly/MODEL.json --check 20 --check-out $R/learn-to-fly/MODEL.check.md
$PY scripts/49_scrape_facts.py --release v0.3-20261023 --rows-out ROWS.jsonl     # March rows, in a scratch copy of the tree
$PY scripts/50_error_audit.py evidence --rows ROWS.jsonl --output-dir $P/output --out EVIDENCE.jsonl
$PY scripts/50_error_audit.py check --rows ROWS.jsonl --classes ../../docs/hackers-2026-error-audit.csv
```

The 49 default run (no `--results`) writes `data/release/<release>/scrape_facts.json` and `docs/hackers-2026-scrape-facts.md`. To get the March rows without touching either, run it in a scratch copy of the tree whose `output/` and `data/registry` are symlinks.

## What did not run to a result

- **The hand-check gate failed** (63 of 66), as described above. That is a result, not a gap: the price fields carry a known misreading of "dual" rates.
- **The effort-test subsets fail 49's size gates** (under 1,000 tails), as they must. Their paired counts are the result. No gated full run exists at `medium` or `high` effort.
- **Sonnet 5.5 did not run on the whole HTTP pass.** Its all-runs column covers 55% of HTTP; the paired HTTP and browser tables are the like-for-like comparisons.
- **The March HTTP batch cannot be paired with the re-run by school,** because its crawl index is gone. The browser pass is paired exactly.
