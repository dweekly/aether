# Deck review, 2026-11-04: Codex Sol, Codex Astra, Opus

Three independent reviews of the 19-slide deck ("Scrape. Join. Interrogate.") from the same brief: rendered slide images plus each slide's text and speaker notes, read against this repo. Reviewers did not see each other's reports. The demo GIF (slide 8) and QR code (slide 19) appeared broken to all three only because the local renderer could not load those two assets; they load in the live deck.

## Applied (deck version 1793842325-ef3a)

- "4,825 websites" (slides 2, 3) was the March record count; now 3,903 websites (records with a domain in flight_line.json).
- Slide 3: the summer-intern line replaced.
- Slide 4: rates not drawn are 26 (21 below $120, 5 above $280), not 13, recomputed from flight_line.json; the post-1996 comparison states both medians; fuel inclusion caveat added (36 of 55 audited rates say fuel is included).
- Slide 5: "likely schools, clubs and instructors".
- Slide 7: "newer, smarter" cut; the long-table weakness given in numbers (146 and 188 of 231); the prompt difference disclosed.
- Slide 9: "sites unreachable"; "26% of rates went up"; footnote rewritten, with the $185 median in both months.
- Slide 10 notes: the browser user agent was a diagnostic request; "points to platform filters".
- Slide 11: Alaska Flight Academy double count removed (+42); "121 of the 566 from Google"; stale notes replaced.
- Slide 13: subtitle "built before 1987" (62% by 1986; 37% in the 1970s); labels no longer crossed by the dashed lines; notes no longer say shipments "never recovered".
- Slide 14: card 3 labelled Florida and set to 83% (the state file alone); card 2 uses the surname check instead of the weak house-number control; "the Montana filing we checked".
- Slide 15: exposure (181 vs 21 hours) and the flew-vs-flew comparison (14.1% vs 11.4%) on the slide; notes add the make/model-path caveat, serial-only 11.7% vs 7.9%, accidents alone 15.4%.
- Slide 16: $3.1M; "151 schools"; "no public lookup of drone owners".
- Slides 17, 18: tip wording scoped; the "X, not Y" slogans rewritten.
- Slide 19: "any US-registered plane".
- New slide after 15: the strangest things in the data (see [the oddities list](../hackers-2026-oddities.md)).

## Not applied, for David

- Length: the notes run about 2,900 words, roughly 19 minutes; all three reviewers say cut for a 7-12 minute slot (Sol: move 2, 9, 10, 11, 12, 16 to backup; Astra: 10, 13, 14, 16; Opus: fold 10 into 9, merge 17 and 18).
- "Wet" in the 172 question: only about two-thirds of rates say fuel is included. Recomputing on a confirmed-wet subset, or asking "posted rate", is the alternative.
- Slide 6 map: lower 48 only while the count is national; ring wording is inside the chart image.
- Slide 12: dots are per-school medians, and the title's claim is stronger than the metro comparison; chart regeneration needed.
- Slide 13: the 63% Cirrus figure leaves out the airline academies' new Pipers (noted in the speaker notes only).
- Slide 15 title still says "accident record" for 15.6%, which includes incidents (accidents alone 15.4%, still about 1 in 6).
- Slide 19: repo link placeholder until the repo is public.
- A source → extracted row → join example on one slide (all three reviewers).


## Codex Sol (gpt-6.1-sol, high)

Reviewed all 19 images and the repo findings. Nothing edited.

**1 — Cover**

- The aircraft strips need a caption: **“Skybound Aviation, Atlanta. Aircraft registration, model/year, airport, rental $/hour including fuel.”** Otherwise “KPDK” and the dollar amounts are unexplained.
- Notes: **“a weekend”** contradicts slide 7’s week-long build. Use **“a week and a few hundred dollars in API charges”**, with human time accounted for separately.

**2 — Landscape**

- **“FAA registry: every aircraft”** → **“US civil aircraft registrations.”**
- **“ADS-B: every transponder ping”** overstates coverage. Use **“Aircraft positions heard by a volunteer receiver network.”** Define ADS-B as aircraft position broadcasts. The archive contains received observations, not every transmission. [adsb.lol](https://www.adsb.lol/about)
- **“FAA approves 519”** needs **“519 Part 141 school certificates, March 2026”**; that is not 519 training locations.
- **“TSA knows 13,000, won’t say”** → **“TSA reports 13,000 registrations since the program began; no public directory.”**
- Delete the notes’ **“next two slides”** promise about Lampson Field and drone counts; those slides are absent.

**3 — Asking about the 172**

- **“Spread over 4,825 small websites”** is wrong. That is the March record count, including 1,525 records without websites. It also implies all those sites post 172 prices. Use **“Scattered across thousands of school websites.”**
- If retaining **“dry”** and **“block-hour,”** gloss them as fuel excluded and prepaid-hour discounts.
- Cut **“Easy to ask. Until recently, a summer intern’s job to answer.”** Slide 7 already makes that comparison.

**4 — Answering about the 172**

- **“an hour, wet”** promises a distinction the extraction did not reliably establish: only 36 of 55 audited rates explicitly included fuel. Either recompute a confirmed-wet subset or change both the question and answer to **posted hourly rental rates**.
- **“$177”** should be **“$177.50 median”** or approximately **“$178.”**
- **“A post-1996 172 rents for just $20 more”** hides the comparison group. Use **“1996-and-newer: $195 median; 1986-and-older: $175.”**
- Add a vertical-axis label, **“Number of listings.”** Enlarge the sample/date caption; it currently carries essential qualifications in small, dim text.
- Notes: **“one dry rate stored as wet”** conflicts with the March hand audit, which explicitly reports no such case. Reconcile the audits. [Extraction audit](/Users/dew/dev/aether-talk/docs/hackers-2026-extraction-audit.md)

**5 — No list**

- **“~9,000 businesses and instructors teach flying”** turns cumulative registrations into a current population estimate. Use **“TSA reported about 9,000 Part 61 providers registered since the program began.”** Restore that omitted qualification in the quotation. [2024 rule, p. 35605](https://www.govinfo.gov/content/pkg/FR-2024-05-01/pdf/2024-08800.pdf#page=26)
- **“5,458 schools, clubs and instructors”** is the largest credibility problem. The total includes inferred aircraft-owner records, duplicate owner/operator representations, and known non-training entries. Use **“5,458 candidate records”** and distinguish confirmed training operations from inferred owners. Do not imply measured national completeness. [Known limitations](/Users/dew/dev/aether-talk/docs/hackers-2026-google-schools.md:365)

**6 — School map**

- **“4,185 … placed at an airport”** counts locations outside the displayed lower 48. Add Alaska/Hawaii insets or state that the map shows the lower 48 and the count is nationwide.
- **“Ring: where its planes are based”** → **“Ring: airport inferred from aircraft observations.”** The present wording sounds established.
- Notes: **“at least two of its planes”** disagrees with the code, which also accepts one aircraft when only one has a known base. The 73% check covers the two-or-more subset and tests whether *any* inferred airport matches a listed airport, not necessarily the single airport plotted. State those limits. [Inference code](/Users/dew/dev/aether-talk/research/FlightSchoolMapping/scripts/infer_airports.py:2)

**7 — Reading cost**

- Cut **“newer, smarter.”** The results shown do not establish smarter.
- Replace **“The cheapest model was the right one … mostly goes away with a little more thinking”** with **“Haiku matched the ordinary-site sample. On 15 difficult sites, medium effort found 188 tails versus March’s 231.”** Forty-three missing tails remain.
- **“same cached pages and prompt”** is too strong: the HTTP school sets differ, and the rerun added training-price instructions and structured output. Label the full-run comparison approximate; the browser-only comparison is the controlled one.
- Clarify that **“$100”** covers measured extraction passes, excluding expansion, PDF/photo passes and unrecorded work. It is not the whole project cost. [Rerun findings](/Users/dew/dev/aether-talk/docs/hackers-2026-model-rerun.md)

**8 — Demo**

- **The rendered demo is broken:** a black rectangle with a broken-image icon and alt text. Repair the media and provide a readable static result as fallback.
- **“start to finish”** overstates a fresh extraction joined to precomputed datasets. Use **“One school: extract, then check against cached records.”** Put that distinction beside the demonstration, not solely in the footer.

**9 — Eight months**

- **“157 sites gone”** → **“157 sites unreachable on two attempts.”** TLS failures and timeouts do not establish that a site disappeared.
- Beside **“207 now refuse the crawler,”** add **“November used a different machine/IP.”** This matters to the apparent change.
- **“each month’s rate is on its own page only”** contradicts the unchanged pairs. The code explicitly retains equal rates. Use **“1,281 comparisons; changed prices also checked against the other month’s pages.”**
- Make clear these are extracted rate comparisons, not percentages of schools. The hand check found a wrong aircraft attachment, so avoid presenting them as verified aircraft-level price changes.

**10 — Blocking**

- Notes: **“no faking a browser to get past a block”** contradicts the browser-user-agent probe described on the slide. Say **“We tested one browser user agent to diagnose the refusals; we did not solve challenges.”**
- **“hosting platforms’ defaults, not … the schools”** exceeds the evidence. Use **“The pattern points to platform filters; we cannot see individual schools’ settings.”**
- **“4 of 47”** needs its **5 of 146 control** beside it, or should be cut. Alone, it invites the anti-AI interpretation the analysis rejects.

**11 — Sources and cost**

- **“AOPA, 3 web directories, FAA Part 141, Cirrus → 4,825 schools”** omits FAA registrant-name discovery and calls candidates schools. Add the registry source and label the output **candidate records**.
- **“Cost $100”** is fleet extraction, not the cost of obtaining those directory records. Name the operation being priced.
- **“Machine time: 7 days”** is elapsed build time. The Google **“65 min”** covers search/detail requests, not subsequent crawling and extraction. Use consistent timing labels.
- Notes saying **“model pass … in progress”** and **“4,892 … before Google”** are stale.

**12 — School versus city**

- **“Each dot is one school’s posted hourly rate”** → **“Each dot is the median of that school’s posted 172 rates.”**
- **“Where you live barely changes the price”** is stronger than this selected sample establishes. Use **“Schools within one metro often differ more than metro medians do.”**
- Highlight **“$33 between metro medians; $39 within-metro middle-half spread.”** Currently viewers must reconstruct the title’s argument from twelve rows. Six metros would communicate it faster.
- Apply slide 4’s fuel qualification here too.

**13 — Production cliff**

- **“most training planes date from the 1970s”** is false for these cohorts: 43.8% on websites, 36.7% in the broader fleet set. Use **“Two-thirds of website-listed four-seaters were built by 1986.”**
- **“63% … are Cirrus”** needs the academy omission visibly disclosed. Including registry-matched fleets makes Piper the largest new-school-aircraft group. This is a consequential selection effect.
- Notes: **“they never recovered”** is unsupported by a shipments series ending in 2019.
- Three charts, five makes, historical annotations and two percentages are too much for this slot. Keep the production collapse and one fleet-age chart; move the Cirrus finding to backup. [Chart definitions and results](/Users/dew/dev/aether-talk/docs/hackers-2026-slide-research.md)

**14 — People behind LLCs**

- **“86% of Florida LLC aircraft … both routes”** → **“86% of aircraft registered to LLCs at Florida addresses yield a named person through at least one route.”** “Both” currently suggests an intersection.
- **“Change the house number and it’s 1 in 4,000”** → **“Shuffling house numbers within ZIP codes: about 0.02% match.”** This identifies the control.
- **“We publish counts, never names”** is broader than intended. Use **“We do not publish inferred pilot–aircraft links.”**
- **“Montana’s record names only the agent”** should specify **the summary record checked**. The repo says filing history was not examined. Cut the unverified tax interpretation from the spoken notes.

**15 — NTSB**

- **“15.6% … accident record”** includes incidents. Accident-only is **15.4%**.
- **“Matched on the same airframe”** overstates the model-only fallback. The repo explicitly says that fallback can accept another airframe of the same model; the shuffled-serial control does not test it. For a confirmed-airframe claim, use **11.7% serial-confirmed versus 7.9%** in the comparison cohort.
- The chart compares all school-listed aircraft with observed-flying comparison aircraft. For a symmetric observed-flying comparison, the reported figures are **14.1% versus 11.4%**.
- Put **“Aircraft observed flying: median 181 hours versus 21”** visibly on the slide. Without exposure context, the chart reads as a school-safety comparison.
- Notes: **“three free federal datasets”** is wrong; the school fleets are your assembled dataset. [NTSB findings and limitations](/Users/dew/dev/aether-talk/docs/hackers-2026-ntsb.md)

**16 — Wishlist**

- **“Made. 5,458”** repeats the completeness problem from slide 5.
- **“$12,000 median, as posted”** needs **“136 schools after range filtering; mostly estimates, March pages.”** The unfiltered 151-school median is $11,655.
- **“$10 a plane: $3.2M for the fleet”** is the records-purchase fee, excluding reading and processing. At 312,519 valid registrations it is approximately **$3.1M**. [FAA fee](https://www.faa.gov/licenses_certificates/aircraft_certification/aircraft_registry/copies_aircraft_records)
- I would cut this slide from the main talk. It opens several new subjects immediately before the practical takeaway.

**17 — Getting data**

- **“Google search … names $17 per 1,000”** should name **Google Places API** and **Place Details Pro**, including the monthly free allowance. Otherwise attendees may apply the figure to ordinary search or another Places endpoint. [Google pricing](https://developers.google.com/maps/billing-and-pricing/pricing)
- **“Haiku matched Sonnet for an eighth of the cost”** needs **“on the ordinary-site sample”** and **“Sonnet 5.5’s estimated cost.”**
- **“look for the front door”** → **“Try another public source.”** It describes the examples more directly.

**18 — Making it joinable**

- **“One row per aircraft … structured output”** needs the qualification demonstrated by slide 7: schema compliance did not prevent one-row-per-type omissions.
- **“not on the page, not kept”** is not a pipeline-wide guarantee. Verbatim checks cover particular newer fields; older extraction values were not all checked.
- **“School airport = plane’s base 78%; shuffled 0.5%”** needs disclosure that the extraction prompt supplied a known-airport hint. It is not wholly independent validation.
- Replace the conspicuous slogan forms:
  - **“Join on two keys, not one”** → **“Check tail number against serial number.”**
  - **“Fix the pipeline, not the rows”** → **“Fix the extractor and rerun the sample.”**
  - **“If the shuffle matches too, it’s lying”** → **“A high shuffled match rate suggests a weak join.”**
- Replace the closing provenance slogan with one concrete example: preserving `fuel_included = unknown` instead of silently calling an unspecified price wet.

**19 — Get it**

- **The QR image is broken.** The large pale square contains a broken-image icon and alt text.
- Replace **“[repo link once public]”** with the actual public URL before presenting.
- **“Look up any plane”** → **“Look up a US registration.”**
- Give one explicit first action: **“Clone the repo and run the sample pipeline.”** Link directly to its instructions. The current ending offers resources but leaves the starting point implicit.

**Deck-level changes**

- The notes contain about **2,900 words**, approximately **19 minutes at 150 words/minute**, before pauses and demonstration.
- For twelve minutes, move **2, 9, 10, 11, 12 and 16** to backup; simplify 13 substantially or move it too. For seven minutes, keep only one substantial join finding.
- Move practical instructions into the example: show **one source excerpt, its extracted JSON, and the registry join** during slide 8. This supplies the missing reproducible method before the closing tip lists.
- Essential caveats currently sit in small, low-contrast footers, especially on 4, 7, 9 and 14. Move qualifications that change the conclusion into the main text.
- The strongest ending is a working URL/QR plus a runnable sample, followed by **“What dataset would you build?”**

**Top five, ranked**

1. Qualify **5,458** throughout; separate confirmed operations from inferred owner records.
2. Repair the **demo and QR**, and replace the repository placeholder.
3. Resolve the **wet-rate claim** before presenting the $177 answer.
4. Correct the **NTSB matching and comparison claims**.
5. Cut the talk to fit twelve minutes and demonstrate one extraction-to-join example.

## Codex Astra (gpt-6-astra)

I inspected all 19 images and checked the research docs and relevant code. Nothing edited.

**1 — Cover**

- “What a few hundred dollars of AI does to public data” is vague. Replace with **“Building a flight-school dataset from websites and public records.”** Keep the dollar claim for the measured-cost slide.
- The aircraft strips need column labels: **Registration / Model, year / Airport / $ per hour, fuel included**. Explain KPDK as Peachtree-DeKalb once.
- Notes: “a weekend” contradicts slide 7’s week and omits development and checking time. Say **“The first extraction cost about $100 in model calls; the build ran for a week.”**

**2 — Landscape**

- “ADS-B: every transponder ping” overclaims coverage and conflates ADS-B with all transponder traffic. Use **“ADS-B: aircraft position broadcasts received by volunteer stations.”**
- “FAA registry: every aircraft” → **“US-registered aircraft, registered owner and mailing address.”**
- “TSA knows 13,000, won’t say” implies a current census and a refusal. Use **“TSA reported ~13,000 provider registrations in 2024; no public list.”**
- “4,825 websites” is the wrong unit: that is the March candidate-record count, including records without websites.
- Delete the notes’ promise that the “next two slides” cover Lampson and drones; those slides are absent.

**3 — Asking about rental prices**

- “Spread over 4,825 small websites” repeats the record/website error and suggests every candidate posts a 172 price. Replace with **“Scattered across school websites.”**
- “Until recently, a summer intern’s job to answer” presents an unmeasured historical comparison as fact. **“Answering means reading hundreds of rate sheets.”**

**4 — Rental-price answer**

- “$177 … an hour, wet” needs two corrections: the median is **$177.50**, and fuel inclusion is not established for every rate. Use **“$177.50 median posted hourly rate”**, with a visible **“Fuel inclusion often unspecified.”**
- “13 rates below $120 or above $280 not drawn” is wrong against the current file. Reproducing the **1,318 rates / 389 schools / $177.50 median** yields **26 outside that range: 21 below, five above**.
- “A post-1996 172 rents for just $20 more” leaves the comparison implicit. Use **“Median: $175 for aircraft built through 1986; $195 for 1996 onward.”**
- Add a vertical-axis label, **“Number of listings,”** and mark the median.
- The notes’ “one dry rate” conflicts with the [March audit](/Users/dew/dev/aether-talk/docs/hackers-2026-extraction-audit.md), which found none filed as wet in that sample. The later November audit found a dry-rate example; distinguish the audits.

**5 — No list**

- “~9,000 businesses and instructors teach flying” reads as a current active-provider count. The source describes registrations accumulated since the program began. Use **“TSA reported about 9,000 Part 61 providers registered since the program began.”** [2024 rule](https://www.govinfo.gov/content/pkg/FR-2024-05-01/pdf/2024-08800.pdf)
- “5,458 schools, clubs and instructors” is stronger than the dataset supports. The original pool includes FAA registrants selected by name; the research explicitly identifies non-school organizations. Use **“5,458 candidate records, with sources”** until the whole pool is classified.
- Make **“Coverage is incomplete”** explicit. Juxtaposing 9,000 and 5,458 otherwise invites an invalid completeness calculation. [Repo’s own scope statement](/Users/dew/dev/aether-talk/docs/hackers-2026-talk.md:33)

**6 — School map**

- “4,185 … placed at an airport” counts nationwide placements, but the drawing filters to the lower 48. Label **“Lower 48 shown; counts include all US placements”**, or use counts for the displayed area.
- “Ring: where its planes are based” sounds established. Use **“Ring: airport inferred from aircraft activity.”** Put the **73% agreement on known schools** beside that explanation, rather than leaving uncertainty entirely in notes.
- Enlarge the legend; distinguishing the tiny rings from filled dots while reading the bottom sentence is too much work.

**7 — Reading cost**

- “Same cached pages and prompt” is not strictly true. The rerun added training-price instructions and structured output; the HTTP request set changed. The browser subset is the controlled comparison. Say **“Rerun of the March cache; HTTP coverage and extraction instructions differ.”**
- “Its one weakness … mostly goes away” understates the remaining miss rate: medium-effort Haiku found **188 versus March’s 231** tails on the long-list sample. Replace with **“Haiku matched the ordinary-site sample; long fleet tables still needed extra checks.”**
- Delete “newer, smarter”; your results do not support it.
- Label the dollars **“Extraction API cost.”** The $100 excludes additional passes, and none of these numbers prices the complete project. [Rerun methods and results](/Users/dew/dev/aether-talk/docs/hackers-2026-model-rerun.md)

**8 — Demo**

- **The image is broken:** the entire demonstration area is black with a broken-image icon and exposed alt text. Repair the embedded asset and include a readable final-frame fallback.
- “Start to finish” suggests the checks were computed in those 12 seconds. Use **“One school: extract its fleet, then check against cached records.”**
- Translate the yellow row aloud: **“The website and registry disagree about the model.”** Warrior, Archer and PA-28-181 will not explain themselves.

**9 — Eight months**

- “157 sites gone” → **“157 sites unreachable on two attempts.”** DNS, TLS errors and timeouts do not establish permanent disappearance.
- “One flight school is now an online slots site” → **“One former school domain now redirects to a slots site.”**
- “207 now refuse the crawler” needs the visible qualifier **“November crawl used a different IP.”** Slide 10 also reclassifies 11 as sold/renamed domains and nine as unreachable; call 207 the **initial refusal/challenge classification**.
- Label the percentages **“1,281 matched extracted rate pairs.”** They are not percentages of schools, and the hand check found an aircraft-assignment error. The footnote “each month’s rate is on its own page only” is cryptic and omits the unchanged-rate exception.
- Notes: say **“Both months’ cached pages re-extracted with the same model.”** “Same model in both months” otherwise conflicts with March’s Sonnet run.

**10 — Blocking**

- Notes: “no faking a browser to get past a block” contradicts the diagnostic requests using a Chrome user agent—and the blue bars show those requests succeeded. Say **“We made a diagnostic request with a browser user agent; we did not solve challenges.”** Distinguish this investigation from the extraction policy.
- “The robots are being kept out by hosting platforms’ defaults, not by the schools” claims configuration knowledge you do not have. Use **“The pattern points to platform bot filters; we cannot tell who enabled them.”**
- “4 of 47” needs **“versus 5 of 146 accessible-site controls”**, or remove it. Alone, it does not support the conclusion. [Probe methods](/Users/dew/dev/aether-talk/docs/hackers-2026-slide-research.md)

**11 — Sources and costs**

- **The rows sum to 5,459, not 5,458.** Alaska Flight Academy appears in both FSAI and state lists. Add **“−1 overlap”** or make the additions mutually exclusive. [Deduplication explanation](/Users/dew/dev/aether-talk/docs/hackers-2026-added-schools.md)
- The original 4,825 also include FAA-registry discovery, which the first source row omits.
- “Machine time: 7 days” is elapsed build time; the other rows measure narrower operations. Rename the column **“Recorded elapsed time”** and identify what each measurement covers.
- “Each new site … all 633” is false: 24 of the first 67 additions had no website. Say **“633 records added; extraction/classification cost $3.14.”**
- Replace the stale notes saying Google classification is “in progress” and the total is 4,892.

**12 — Choosing a school**

- “Posted hourly rate with fuel” inherits the unverified-wet problem. Use **“Posted hourly rate; fuel inclusion not always stated.”**
- The title claims a general variance result; the analysis compares the range of metro medians with typical within-metro quartile spreads. A defensible title is **“Prices overlap widely across these 12 metros.”** Annotate **“Metro medians: $157–190; typical within-metro middle half: $39.”**
- “Each dot is one school’s … rate” → **“Each dot is one school’s median 172 rate.”**
- Bring the median-bar legend above the chart and brighten the blue dots and gray labels. The chart is readable up close but weak for a projected 20-second glance.

**13 — Production collapse**

- “So most training planes date from the 1970s” is false. The research gives **36.7% of all school four-seaters**, or **43.8% of website-listed ones**. Replace with **“The 1970s are the largest surviving cohort.”**
- The **63% Cirrus** callout excludes major academy fleets. Across all school records, new aircraft are **50% Piper and 32% Cirrus**. Show that comparison or drop the school-Cirrus claim; the missing academies change its interpretation materially.
- Three charts, three historical annotations and two percentages are too much here. Keep production plus the school-fleet age chart; move the Cirrus comparison to backup.
- Notes: soften the product-liability explanation. The repo explicitly says the shipment data establishes timing, not causes. [Research and cohort definitions](/Users/dew/dev/aether-talk/docs/hackers-2026-slide-research.md)

**14 — Aircraft LLCs**

- The arrows imply one nationwide funnel, but “86%” switches to Florida and combines two routes. Label the denominators directly: **“20% of US-address LLC aircraft”** and **“86% of Florida-address LLC aircraft, using either source.”**
- “Change the house number and it’s 1 in 4,000” oversimplifies a specially filtered control. The code excludes shuffled addresses that coincide with another pilot’s real address. Drop this line from the slide; retain the full control explanation in notes.
- “Montana’s record names only the agent” generalizes from a particular lookup. Use **“The Montana filing we checked named only the registered agent.”**
- The Montana strip introduces a separate tax story and is too dense. Move it to backup.
- “A lead, not proof of ownership” → **“These matches identify possible connections to the LLC.”**

**15 — Accident history**

- “NTSB accident record” is mislabeled: **15.6% includes incidents**; accidents alone are **15.4%**. Use **“NTSB accident or incident record”** or change the plotted series.
- “Matched on the same airframe” overstates the make/model fallback. It can accept a different aircraft of the same model that previously held that registration; the serial shuffle does not test that path. Distinguish **serial-confirmed** from **make/model-only** matches.
- The bars compare **all school aircraft** with **active comparison aircraft**. Prefer the available active-to-active comparison: **14.1% versus 11.4%** for any record, and regenerate the other comparison consistently.
- Put **“Lifetime history; not adjusted for hours flown”** on the slide. The current visual invites a safety conclusion before the notes can explain exposure.
- Notes: “three free federal datasets” is wrong; the school fleet list is yours. [Definitions and alternate comparisons](/Users/dew/dev/aether-talk/docs/hackers-2026-ntsb.md)

**16 — Wish list**

- “Made. 5,458” implies complete fleets and prices for every record. Use **“5,458 candidate records; fleet and price coverage varies.”**
- “$12,000 median, as posted” is a filtered estimate, not the raw median or observed completion cost. Use **“$12,000 median advertised budget/package; 136 schools after filtering.”**
- “$10 a plane: $3.2M for the fleet” means **record-acquisition fees**, not AI extraction or total project cost. Say that explicitly; the FAA charges $10 per aircraft CD. [FAA records pricing](https://www.faa.gov/licenses_certificates/aircraft_certification/aircraft_registry/copies_aircraft_records)
- Replace “No public Part 48 lookup” with **“No public lookup of small-drone owners.”**
- This is a strong candidate for backup: it restarts the dataset tour just when the talk should deliver practical advice.

**17 — Getting data**

- “Google search” → **“Google Places Text Search API.”** The price applies to a specific API and field selection.
- “Haiku matched Sonnet for an eighth of the cost” needs the comparator and scope: **“$15 versus an estimated $120 for Sonnet 5.5; ordinary-site tail extraction matched.”**
- “Triage before you crawl deep” is not what the documented 771-site experiment did: crawling preceded classification. Use **“Classify candidates before fleet extraction.”**
- “Look for the front door” → **“Try official files and other directories.”**

**18 — Making it joinable**

- “Not on the page, not kept” overstates implementation. The build’s `extraction_prov` explicitly says fleet extraction values are **“not checked verbatim.”** Scope the claim to checked contact fields and present broader verification as advice. [Build code](/Users/dew/dev/aether-talk/research/FlightSchoolMapping/scripts/21_build_flight_line.py:123)
- “959 reused tail numbers would have joined wrong” treats every rejected match as proven reassignment. Some were rejected for missing evidence or near-matching serials. Use **“Additional identity checks rejected matches for 959 aircraft.”**
- “Join on two keys, not one” → **“Check registration against serial number.”**
- “If the shuffle matches too, it’s lying” → **“Compare the real match rate with a shuffled baseline.”** Nonzero shuffled matches are expected.
- “Fix the pipeline, not the rows” → **“Fix the cause, then rerun the affected records.”**
- Notes: the cleaner emptied **41% of assessed schools’ HTTP page sets**, not 41% of pages.
- Cut the closing provenance slogan; the source-retention row already makes the point.

**19 — Get it**

- **The QR code is broken:** the image shows an empty cream rectangle and alt text.
- Replace **“Code: [repo link once public]”** with the actual accessible destination before presenting.
- “Look up any plane” → **“Look up a US aircraft registration.”**
- Add one concrete action: **“Download the data, run one join, or submit a missing school.”** Put downloads and code behind the same working landing page.
- Scope “every field with its source” to the provenance actually supplied; source metadata is not the same as verified page-level evidence.

**Deck-level changes**

- Nineteen slides are workable only if several are nearly wordless. Here, the dense research slides crowd out the practical lesson. For 12 minutes, move **10, 13, 14 and 16** to backup. For seven minutes, also cut **2, 6 and 9**.
- Keep the rental question and answer together. Move the source table next to the list-building slide, and put the practical tips immediately after the demo.
- Add one small, readable example of **source text → extracted row → normalized key → checked join**. That is the missing bridge between “the model read websites” and advice attendees can apply.
- Make unknown values visible in that example: `fuel_included: unknown` would teach more than another broad success claim.
- Essential qualifications currently live in tiny, low-contrast footnotes. Move cohort, uncertainty and comparison labels beside the numbers they qualify.

**Top five changes, ranked**

1. **Repair the demo and QR code; replace the repository placeholder.**
2. **Stop presenting candidate records as a verified census of 5,458 schools; fix the source-table double count.**
3. **Correct the rental headline and outlier count: $177.50, uncertain fuel inclusion, 26 omitted rates.**
4. **Repair the NTSB labels, identity certainty and comparison cohorts before showing the accident chart.**
5. **Cut four research digressions and give the practical method enough time—including one concrete extraction-and-join example.**

## Opus 5.5 (fresh agent, no prior context)

Numbers spot-checked against the repo: "4,825 websites" wrong (records, not sites; 3,343 websites in March); slide 13 "most from the 1970s" false (36.7%; 62% by 1986, median 1979); slide 11 rows sum to 5,459 (Alaska Flight Academy in both FSAI and Alaska lists); "121 one instructor" counts only the 566 Google additions; slide 9 26% confirmed but median wet rate $185 both months; slide 15 confirmed, but the school group counts every listed tail while the comparison counts only planes that flew (13.1% against all comparable registered planes); slide 14 figures right, the 86% is Florida-only; slide 16 $12,000 from 151 schools, 92 of them estimates, and $3.2M unsourced ($3.1M at $10 × 312,519); slides 7 and 17 confirmed.

Slide notes: 2 and 3, fix 4,825; 3, the intern couplet is metered; 4, state both medians, say "$10 bins"; 5, put 13,000 TSA beside 5,458; 6, Alaska and Hawaii not drawn, rings hard to tell from dots; 7, "1.5× the tokens" garbled, give the long-table numbers; 8, demo footer says Opus right after a Haiku slide; 9, "26% up" reads as a 26% price rise, label the 2% sliver, say both months were read by Haiku, and 207 includes sold or unreachable domains; 10, put "we took no for an answer" on the slide, grey the non-hosting rows; 11, double count, scope the 121, stale notes; 12, "Cessna 172 rates" not "172 prices", explain the amber Bay Area; 13, subtitle, label collisions, Cirrus 63% vs full-fleet 50% Piper / 32% Cirrus; 14, arrows imply a funnel, "both routes" is jargon, dead gap beside 665, explain "$49 registered agent"; 15, exposure on the slide, mixed date formats; 16, qualify $12,000, source $3.2M, "Part 48" is jargon, "Partly" shares the red of "Blocked", ask the closing question; 17, tip 3 overstated, bottom 40% empty; 18, rewrite three slogans, put the cleaner and matcher examples on the slide; 19, QR and repo link.

Deck level: move slide 11 next to 5 and 6; no slide traces one tail through registry, ADS-B and NTSB (a static four-row trace of one tail would do); 19 dense slides is a lot for the slot (fold 10 into 9, merge 17 and 18); give the ending a concrete action.

Top five: broken media and repo link; the wrong-base numbers (4,825 websites, 1970s, double count); slide 15 exposure and cohort mismatch; slide 9 wording; slide 14 funnel and the slogans on 18 and 3.
