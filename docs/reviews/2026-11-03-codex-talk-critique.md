# Codex critique of the Hackers 2026 talk narrative and slides

Run 2026-11-03 with `codex exec -s read-only` (gpt-6.1-sol, high), prompted as a harsh expert critic (pilot plus AI/data engineer) over docs/hackers-2026-talk.md and the slide text. Kept verbatim as the record; what was folded in is in docs/hackers-2026-talk.md.

The talk repeatedly turns a measured proxy into a stronger claim: a candidate catalog becomes “every school,” a registry match becomes validation, missing observations become inactivity, and rate × hours becomes revenue. That is where this audience will stop trusting you.

I checked the cited documents and implementation, reproduced the raw extraction counts against the frozen aircraft table, and inspected the source survey and airport-count work on `hackers-airports`. I did not independently rerun the Florida join against its original corporate and airmen files.

## 1. Claims that are wrong, or that an expert would call wrong

### 1. “Every flight school in the US”

Also: **“Schools we found / 4,825 / schools, clubs and university programs”** and **“Every flight school, its fleet and prices / Made.”**

**How I hear it:** “You completed a census, verified the entities, and established their current fleets and prices.”

You did none of those things. The checked-in `flight_line.json` has:

- 4,825 entries.
- 1,502 with no fleet.
- 1,525 with no website domain.
- 1,536 whose only extraction source is `faa_registry`.

Those counts overlap. They establish that 4,825 is a mixed catalog, including directory entries and inferred registrants. The assembly explicitly adds directory-only entries and FAA-only registrants. Neither “aviation” in a registrant’s name nor ownership of trainers establishes that it operates a flight school. A trustee, lessor, university, club, business location and training provider are different entities. Domain-based deduplication also merges multiple locations and leaves organizations with multiple domains unresolved. [Assembly implementation](/Users/dew/dev/aether-talk/research/FlightSchoolMapping/scripts/21_build_flight_line.py:765)

**Fix:** Title it **“Building an open flight-school dataset.”** Say:

> “We assembled 4,825 candidate school, club, university and registrant records. The public release contains 2,470 school pages with fleet or aircraft-listing evidence. We have not measured national completeness.”

Replace “Made” with **“Built a first version.”** “Every” is the first word I would cut.

### 2. “A Part 61 school’s rental and training planes need a 100-hour inspection”

**How I hear it:** “Renting an airplane triggers the 100-hour requirement.”

That is a familiar aviation-regulation mistake. Rental alone does not trigger §91.409(b). Its instruction provision applies when the person providing paid instruction also provides the aircraft; paid instruction in the student’s own aircraft is a different case. There are also alternative inspection programs and exceptions. [14 CFR 91.409](https://www.ecfr.gov/current/title-14/chapter-I/subchapter-F/part-91/subpart-E/section-91.409)

**Fix:** Cut this detail. It contributes nothing to the dataset argument and creates an unnecessary regulation exam.

If retained:

> “School-provided aircraft used for paid instruction generally require annual or 100-hour inspections, subject to the applicable inspection rules.”

### 3. “The only rule aimed at the operation is the 100-hour inspection”

Also: **“nothing else registers the business”** and **“so it appears on no government list.”**

**How I hear it:** “Part 61 training businesses are essentially unregulated, and the government has no provider records.”

Wrong. TSA requirements apply to covered flight-training providers, including Part 61 providers: student eligibility verification, security coordinators, employee security training and recordkeeping. State business records and airport operating requirements are separate again. None of this establishes that a complete, usable public directory exists—but it destroys your explanation for why one does not. [49 CFR Part 1552](https://www.ecfr.gov/current/title-49/subtitle-B/chapter-XII/subchapter-C/part-1552)

**Fix:**

> “Part 61 does not require an FAA pilot-school certificate. The FAA’s Part 141 directory therefore covers only one part of the training market. We found no complete, freely reusable public directory of training businesses and their fleets.”

Distinguish **no FAA school certificate**, **no complete public directory**, and **no government records**. Only the first two belong here.

### 4. “6% of rental planes with a posted price didn’t fly once in 12 months”

Also: **“planes on a rate sheet that never fly”** and **“on the rate sheet, not in the sky.”**

**How I hear it:** “These airplanes were grounded or fictitious inventory.”

You measured no airborne observations in this network. You did not measure no flying. The repo’s fact sheet explicitly says this distinction must be spoken aloud. Equipment, receiver coverage and low-altitude reception matter. Your own endpoint analysis demonstrates substantial geographic measurement bias. [Fact-sheet caveat](/Users/dew/dev/aether-talk/docs/hackers-2026-facts.md:23)

**Fix, including the large slide text:**

> “6% had no observed flights in the archive.”

Then say:

> “That can mean stale listings, inactivity, or missing observations. We haven’t separated those explanations.”

This caveat cannot live only in notes while the projected headline asserts the opposite.

### 5. “The median 172 that flew grossed $54,162 a year at its posted rate, a floor”

**How I hear it:** “You estimated actual rental revenue and proved it is at least $54,162.”

No. You multiplied an advertised rate by observed airborne hours. Those hours can include owner flying, maintenance flights, repositioning and discounted rentals. Club rates may not resemble commercial rental revenue. Posted prices can include instructor charges or exclude surcharges if extraction gets the semantics wrong. Airborne time is not Hobbs or tach billing time.

Incomplete flight observations can make the *hours* conservative. They do not make the resulting *revenue estimate* a lower bound. Annualizing the hours is another assumption. The calculation itself is plainly visible in the generator. [Calculation](/Users/dew/dev/aether-talk/research/FlightSchoolMapping/scripts/43_talk_facts.py:108)

**Fix:**

> “Posted wet rate × annualized observed airborne hours had a median of $54,162. That is an activity-and-price index, not measured revenue.”

For seven minutes, cut it. The explanation required to rescue it costs more than the finding earns.

### 6. “Based where the site says / 78.3%”

**How I hear it:** “The model independently extracted an airport from the website, and radio observations corroborated it.”

The extraction prompts supply **“Known airport”** as input. The browser prompt does too. Thus the output airport can reproduce supplied metadata rather than extract an independently stated website fact. [HTTP prompt and request construction](/Users/dew/dev/aether-talk/research/FlightSchoolMapping/scripts/09b_fleet_extract.py:120), [browser prompt](/Users/dew/dev/aether-talk/research/FlightSchoolMapping/scripts/09c_browser_extract.py:74)

This does not invalidate the comparison with flight-derived home base. It invalidates the claim about the airport’s provenance.

**Fix:**

> “The model-reported airport agreed with the flight-inferred home airport for 78.3% of eligible claims. The model was given a known-airport hint.”

To claim website extraction accuracy, rerun without that hint or audit the actual source evidence.

### 7. “all registered” and “93.0% of registered tails”

**How I hear it:** “The matched aircraft have valid current registrations.”

Your check tests membership in `aircraft.parquet`, without filtering registration validity. I reproduced 4,147 matches; **38 have statuses the release marks invalid**:

- 31 registration pending.
- Four sale reported.
- Two non-citizen corporations with missing flight-hour reports.
- One other status.

That leaves 4,109 marked valid, or approximately 97.1% of the 4,233 well-formed outputs. This is not evidence of illegal flying; it is evidence that your label exceeds the test. [Registry-match implementation](/Users/dew/dev/aether-talk/research/FlightSchoolMapping/scripts/49_scrape_facts.py:129)

**Fix:** Use **“present in the registry snapshot”** throughout, or filter validity and regenerate the corresponding numbers.

Also fix:

> “68 more were deregistered after the crawl.”

The script establishes presence in the deregistration file, not that deregistration occurred after each crawl. It does not compare those dates.

And:

> “18 … aren’t found anywhere.”

Means absent from the current and deregistered files checked—not absent from every historical registry record.

### 8. “372 operations with three or more aircraft that fly short local legs from one field”

**How I hear it:** “You found 372 distinct training businesses operating identifiable fleets at individual airports.”

The code groups **registrant-name strings**, not resolved operators, and explicitly groups across airports. “Short local legs from one field” is not a required predicate for membership. A classifier score is. Most classifier lift comes from make/model prevalence and registrant fleet size. [Operation grouping](/Users/dew/dev/aether-talk/research/FlightSchoolMapping/scripts/40_training_detect.py:150)

A lessor can support several schools. One school can lease from several registrants. One registrant name is not one operation.

**Fix:**

> “372 registrant groups had at least three high-scoring aircraft and no existing link to a listed fleet. They are leads for finding missing training activity.”

Use **“Missing from our dataset”**, replacing **“on no list.”** You did not check every list.

### 9. “Who is behind the LLC”

And: **“82.5% … get a named manager or member.”**

**How I hear it:** “You identified the people who own these aircraft.”

The Florida file supplies named **managers, members or officers**. Your implementation counts person-type entries in those fields. An officer or manager need not own any equity. A member can itself be an entity. Neither a corporate role nor a surname match establishes ultimate beneficial ownership.

The slide also silently drops “officer” from the measured category. [Florida implementation](/Users/dew/dev/aether-talk/research/FlightSchoolMapping/scripts/52_florida_owners.py:93)

**Fix:**

> “A named person associated with the registered LLC.”

And:

> “82.5% matched a Florida corporate record naming at least one person as a manager, member or officer.”

That is useful. Calling it ownership resolution makes it false.

### 10. “Florida publishes it; the other states we checked don’t”

Notes: **“Florida’s free corporate file names managers; Montana and Delaware don’t.”**

**How I hear it:** “Montana does not publish managers or members.”

That generalization is wrong. Montana law requires annual reports to identify managers for manager-managed LLCs or members for member-managed LLCs. An entity summary screen that names no member does not establish that the filing history contains no such information. [Montana §35-8-208](https://mca.legmt.gov/bills/mca/title_0350/chapter_0080/part_0020/section_0080/0350-0080-0020-0080.html)

The particular N970PM summary may genuinely show no named member. Keep that observation narrow.

**Fix:**

> “Florida gave us a free bulk file with named people. The Montana summary record we inspected named only an agent; we have not established whether its underlying filings identify members.”

Before making this your ownership dead end, inspect the articles and annual reports. Otherwise the pointed question is simply: “Did you click Filing History?”

### 11. The source-access slide misclassifies available data

Exact text:

> “One record at a time / FAA service difficulty reports / NASA safety reports”

And:

> “Not public / FAA enforcement, FOIA only”

**How I hear it:** “You surveyed publication practices carefully enough to criticize the agencies.”

The FAA offers **annual Service Difficulty Report downloads**. NASA ASRS exports up to **10,000 incident records per download**. FAA publishes quarterly reports of certain closed enforcement actions. The full EIS database is a separate matter. [FAA SDR downloads](https://www.faa.gov/av-info/download_SDR), [NASA ASRS exports](https://asrs.arc.nasa.gov/search/database.html), [FAA enforcement reports](https://www.faa.gov/about/office_org/headquarters_offices/agc/practice_areas/enforcement/reports)

**Fix:**

- SDR: free bulk downloads.
- ASRS: searchable, de-identified, batched exports.
- Enforcement: published subsets; full case-level database requires a request.

Your source survey’s failed fetch of an old SDR endpoint is not evidence that bulk access does not exist.

### 12. “Every aircraft’s ownership and repair papers / Makeable. The FAA sells the scans, $10 a plane”

**How I hear it:** “For $10 I get the aircraft’s ownership and maintenance history.”

You get FAA-held registration and airworthiness documents—not the owner’s complete maintenance logbooks or every repair record. The FAA also explicitly excludes certain ancillary ownership documents from the standard CD/paper packet. [FAA records description and exclusions](https://www.faa.gov/licenses_certificates/aircraft_certification/aircraft_registry/copies_aircraft_records)

**Fix:**

> “FAA-held registration and airworthiness document images.”

The CD-ROM and $10 claims are supported by the current FAA page. The scope is the problem. At your 317,972-record scale, even the nominal acquisition charge is about **$3.18 million**, before processing or any special bulk arrangement. “Makeable” needs an economic qualifier.

### 13. “Private pilot minimum: 35 hours in a 141 course, 40 under Part 61 (14 CFR 61.109)”

**How I hear it:** “This is a universal private-pilot comparison, and §61.109 establishes both numbers.”

Scope it to the airplane rating being discussed. The 35-hour approved-course requirement comes from **Part 141 Appendix B**, not §61.109. Different categories and training credits complicate an unqualified comparison. [Part 141 Appendix B](https://www.ecfr.gov/current/title-14/chapter-I/subchapter-H/part-141/appendix-Appendix%20B%20to%20Part%20141)

**Fix:** Correct the citation and scope, or cut it. This talk does not need a minimum-hours digression.

## 2. Technically defensible claims that are glib, overstated, or invite a pointed question

### 1. “One week … $100”

Also: **“we built one in a week for about $100”** and **“Made. School websites and a model, $100.”**

**How I hear it:** “That is the cost and development time of the released dataset.”

The arithmetic works: retained responses imply roughly **$100.22 at the stated batch prices**. But that excludes expansion, PDF/photo work, overwritten runs, engineering labor, crawling, flight-history processing and release preparation. The README’s quoted final state is from August, not the initial March week. [Scrape accounting](/Users/dew/dev/aether-talk/docs/hackers-2026-scrape-facts.md:7)

**Fix:**

> “The first extraction ran on day one. The retained HTTP and browser extraction responses price out to about $100; later passes and engineering costs are excluded.”

On the slide: **“~$100 recorded extraction cost”**. A footnote saying “floor” does not rescue a spoken total-cost claim.

### 2. “32,462 / pages read” and “4,909 / model calls”

**How I hear it:** “You accounted for exactly what entered the model and every request made.”

The page count is a recursive count of cached files, not an accounting of pages actually included in successful requests. Inputs were truncated. The 4,909 counts successful API responses; there were also 98 HTTP errors, and five successful responses were unparseable.

**Fix:** **“32,462 cached page files; 4,909 successful extraction responses.”**

The numbers are fine as engineering scale indicators. They are not an end-to-end processing ledger.

### 3. “1,987 sites that show nothing without a browser”

**How I hear it:** “You diagnosed JavaScript dependence across nearly two thousand sites.”

Thin or missing crawler output can come from rendering, bot defenses, redirects, extraction mistakes or transient failures. “Squarespace requires a browser” is an especially easy assertion to challenge.

**Fix:**

> “We retried 1,987 sites with a browser after the HTTP crawl returned insufficient content.”

Also make clear that the HTTP and browser site populations overlap.

### 4. “Checked against the registry and a year of flights”

**How I hear it:** “You have measured the extraction’s accuracy.”

You have measured several consistency properties, on different selected populations:

| Check | Actual denominator |
|---|---:|
| Well-formed | 4,296 distinct normalized tails |
| Registry presence | 4,233 well-formed tails |
| Make agreement | 4,934 eligible pass/school/tail claims |
| Observed airborne | 4,147 registry-present tails |
| Airport agreement | 4,184 eligible claims, representing 3,224 distinct tails |

The six bars are not six comparable accuracy measurements. “Flew” is not an extraction-correctness test. A real, flying airplane can still belong to another school.

**Fix:** Label the slide **“Independent consistency checks”**, show denominators, and explicitly say:

> “These checks do not measure fleet-membership precision or extraction recall.”

### 5. “Make matches the registry / 99.4%”

**How I hear it:** “The aircraft descriptions are 99.4% correct.”

The test recognizes a manufacturer-related word and compares broad manufacturer prefixes. It does not validate the model, variant, year, equipment or rate. Your Warrior/Archer demo discrepancy can pass the aggregate manufacturer check.

**Fix:**

> “Manufacturer-family agreement: 99.4% of eligible claims.”

Do not let this become “99.4% accurate extraction” in the talk track.

### 6. “The model’s mistakes”

And: **“Misreads look like OCR errors.”**

**How I hear it:** “You traced these errors to the model reading the source.”

You have not distinguished website typos, bad alt text, historical markings, text extraction problems and model transcription errors. These particular evaluation passes consume HTML or rendered text; they are not evidence of the photo pass’s OCR quality.

The three displayed examples are malformed. The accompanying sentence about **18 well-formed** missing tails describes a different error class.

**Fix:**

> “Bad tail strings in the extraction output.”

Show:

> “63 malformed strings; 18 additional well-formed strings absent from the registry files checked.”

Call them model errors only after opening the source and showing that the source was correct.

### 7. “Shuffle … and the home match drops to 0.5%, so 78% isn’t luck”

**How I hear it:** “You ruled out plausible incorrect fleet assignments.”

You ruled out an extremely weak null: arbitrary tails assigned arbitrary airports across the country. The dangerous errors are assigning the wrong school’s airplane **at the same airport**, or using a stale fleet listing. Those survive this check.

The 4,184 claims also include repeated tails across passes. They are not 4,184 independent confirmations.

**Fix:**

> “The agreement is far above a global shuffle. It supports geographic consistency, not correct school attribution.”

A stronger control would shuffle among schools at the same airport or in the same region.

### 8. “Rate and hours flown are unrelated”

And: **“price doesn’t track use.”**

**How I hear it:** “Price has no relationship with utilization, and perhaps lowering prices would not increase demand.”

A pooled Spearman ρ of 0.06 means little aggregate monotonic association **in this selected sample**. It does not establish independence or a causal price effect. Geography, variant, avionics, membership terms and school-level pricing can mask relationships. Flights are not demand, and annual hours are not availability-adjusted utilization.

**Fix:**

> “Across these 1,201 matched 172s, advertised wet rate had almost no pooled rank correlation with observed airborne hours.”

Keep the chart’s existing disclosure that **12 aircraft outside the axes are not drawn**.

### 9. “Rates as published … 2026-03 to 2026-08” joined to a full year of flights

**How I hear it:** “These prices and fleet memberships describe the period whose activity you analyze.”

A spring or summer roster is joined to flights starting the previous October. A tail may change schools, owners, rates or even airframes across the relevant history. A static registry join does not resolve those changes.

The rental calculation also keeps the first encountered priced listing for each tail; it does not reconcile conflicting school-specific offers.

**Fix:**

> “A cross-sectional comparison of advertised listings collected March–August with activity observed over the surrounding year.”

Do not attribute that whole year’s flying to the school currently listing the aircraft.

### 10. “17 of 20 checked by hand were schools, clubs or leasebacks”

**How I hear it:** “The 372 candidates have about 85% precision.”

This is promising, but the hand check was on an **earlier window**. It is not documented here as a fresh random sample of the final 372. Even a representative 17/20 result has a wide approximate 95% interval—roughly 64–95%.

Also, the breakdown matters: **12 schools, two clubs, three leasebacks, three unclear holding LLCs**. That supports “training-related leads,” not “17 new schools.”

**Fix:** Give that breakdown and identify the sample’s release/window. Do not extrapolate an exact count of undiscovered schools.

### 11. “Classifier on flight behavior plus registry priors”

**How I hear it:** “You discovered training signatures that generalize to previously unseen operators.”

The documented cross-validation holds out **aircraft**, not registrants, schools or airports. Related fleet members can straddle folds. Recomputing the label-derived make/model prior inside each fold is good, but it does not test generalization to entirely unseen operators. Feature importance is also computed on the fitted training population.

Most lift comes from the priors; the negative-results file says so explicitly. [Classifier implementation](/Users/dew/dev/aether-talk/research/FlightSchoolMapping/scripts/40_training_detect.py:112), [negative results](/Users/dew/dev/aether-talk/research/FlightSchoolMapping/NEGATIVE_RESULTS.md:15)

**Fix:**

> “A candidate-ranking model using flight behavior, aircraft type and registrant fleet size.”

Keep the behavior-only failure in the story. It is more interesting than pretending the sky revealed a clean classifier.

### 12. “Validation … 67.5% … against 0.2%” and “PASS”

**How I hear it:** “The Florida entity links are validated, and the named people are the owners.”

The corporate join uses normalized names, including prefix matching for truncated names. It does **not** compare the FAA address with the Florida entity address. A Florida mailing address merely selects the headline cohort. Active entities are preferred, rather than required.

The pilot comparison uses mailing street and ZIP, then surname substrings. “A pilot lives there” is stronger than what the data establishes. The 1,750-aircraft subset is selected for having both a named person and a matching pilot address. It is also aircraft-weighted, so multiple aircraft under one entity repeat evidence.

**Fix:**

> “In a selected address-linked subset, officer-name agreement was much higher than a shuffled control.”

This supports nonrandom linkage. It does not estimate match precision, certify residence or identify beneficial owners. `PASS` means your programmed gates passed.

### 13. “Real traffic counts … Our count failed our own test”

**How I hear it:** “You nearly built an airport traffic counter, but responsibly rejected it at ρ < 0.8.”

The airport branch reports a much more substantial problem:

- 529 towered airports in the comparison.
- Different years: FY2024 official counts versus your 2025–26 window.
- Median observed/official ratio **0.38**, with IQR **0.12–0.55**.
- Endpoint attribution below **1,500 feet barometric MSL**, which suppresses matches at elevated airports.

The fixed MSL cutoff is an implementation defect for this purpose, not an inherent property of ADS-B. Furthermore, even ρ = 0.8 would not establish accurate counts: ranks can correlate strongly while totals are badly wrong.

**Fix:**

> “Our first attempt could not reproduce tower counts. Coverage varied, and our endpoint rule mishandled elevated airports. We did not use it to audit individual airports.”

Show the **38% median ratio** or the altitude failure. Merely saying “0.74 versus our bar of 0.8” hides the useful engineering lesson.

### 14. “How the FAA publishes / Badly”

Specific examples: **“Registry website: rejects curl”**, **“44% unchanged for 10+ years”**, and the unexplained drone-count drop.

**How I hear it:** “You are turning scraping annoyances and suspicious-looking numbers into evidence of agency failure.”

A default-user-agent block is a minor complaint when the agency provides the bulk download you actually need. Unchanged estimates establish staleness, not the true traffic count. Historical estimates and future forecasts need separate labels. Drone registrations are not necessarily unique active aircraft, and registration expirations or reporting-definition changes can produce discontinuities.

**Fix:** Keep one strong, evidenced complaint with a concrete remedy. For example:

> “The FAA provides excellent daily registry downloads, but its document images are still sold one aircraft at a time.”

Or:

> “Many non-towered activity estimates have not changed for a decade; publish their measurement dates and methods.”

Cut the curl gripe.

### 15. “Public, in theory”

Columns: **“Bulk, free … Paid … Licensed … Not public.”**

**How I hear it:** “These are mutually exclusive access categories.”

They mix access mechanics, price and reuse rights. adsb.lol is licensed too—under ODbL. School websites are individually crawled publications, not a bulk structured feed. Texas has free taxpayer data alongside paid SOS products. Corporate files can expose agents without exposing managers or owners.

**Fix:** Describe the two questions separately:

> “Can I retrieve it at scale?”  
> “Can I redistribute the result?”

Reduce the slide to the handful of sources actually used.

### 16. “One school, start to finish … 12 seconds and $0.02”

**How I hear it:** “This is the original pipeline’s performance, including verification.”

It is a November demo on Opus 5.5, while the March extraction used Sonnet 4.6 in batches. The demo checks a prebuilt registry-and-flight table. It does not ingest a year of flights in 12 seconds. Its green fields do not independently verify the posted rate or prove current rental availability. The code reads text from up to five pages, with truncation. [Demo implementation](/Users/dew/dev/aether-talk/research/FlightSchoolMapping/scripts/demo_extract.py:115)

**Fix:**

> “A fresh extraction from this site took 12 seconds and about two cents; checks used our precomputed October dataset.”

Say explicitly that this is one recorded example. Keep the Warrior/Archer disagreement; it is more credible than an all-green demonstration.

### 17. Small count and scope inconsistencies

> “519 certificates at 703 locations.”

The raw export has 703 rows and **521 distinct certificate numbers**. Filtering to rows explicitly marked `UNITED STATES` gives 700 rows and 519 distinct certificates. Three rows have a blank country.

**Fix:** Explain the filter and use a consistent population. Do not combine the filtered certificate count with the unfiltered location count without explanation.

> “From a survey of 40 public sources…”

The narrative says 39; the survey’s table has 40 entries, some of which are mechanisms or limitations rather than datasets.

**Fix:** Say **“A survey of aviation data sources.”** Counting sources adds no value.

> “The busiest school airplane…”

**Fix:** **“The busiest aircraft in our matched school-fleet dataset.”** The N43171 hours and days reproduce; the national superlative does not.

> “Who owns each small drone / Blocked. No public lookup.”

**Fix:** Scope this to **Part 48 registration identity lookup**. Small drones registered under Part 47 can have searchable N-numbers. “We found no public Part 48 owner-lookup service” is stronger research language than an unqualified universal.

## 3. Q&A questions the speaker should be ready for

These answers should be available without inventing certainty on stage.

| Question | Honest answer supported by the repo |
|---|---|
| **How many U.S. flight schools actually exist? What fraction did you find?** | We do not know. The 4,825 figure is a mixed catalog, not an audited national census. We have not established a completeness denominator. |
| **How many of the known Part 141 schools did your public release recover?** | That would be a useful coverage benchmark, but the cited fact sheets do not report the matched recall. Do not substitute the size of the input list for recovery performance. |
| **What are extraction precision and recall against human-labeled pages?** | Not established by these checks. The registry and flights provide consistency tests. There is no reported representative human benchmark for all tails, rates and omitted aircraft. |
| **How do you know a real registered tail belongs to that school?** | Website association is evidence, but it can be stale, promotional or mistaken. A registry match alone does not validate membership. Same-airport activity strengthens geographic plausibility, not operator identity. |
| **Did the model hallucinate those bad tails, or were they already wrong on the website?** | The current check cannot distinguish those causes without inspecting the cached source. The scrape facts explicitly acknowledge this. |
| **Did you test the rates? Wet versus dry, member versus nonmember, block rates, fuel surcharge, instructor included?** | The extraction schema asks for wet rates, but the cited results do not report a completed representative rate-semantic audit. The November research plan proposes a manual price check; a proposed gate is not a result. |
| **Why use an LLM instead of regex and a crawler?** | Heterogeneous pages benefit from semantic extraction, but there is no measured baseline comparison proving the LLM’s incremental benefit. The PDF pass itself recovered 91 N-numbers from nine schools using regex. |
| **How much human time and total compute did this take?** | The first full extraction ran on day one, and the initial build developed over a week. The retained extraction responses price to about $100. Total labor, all model runs and infrastructure costs were not fully recorded. |
| **How do you count hours? Is this Hobbs time?** | No. It sums qualified intervals between airborne points, excluding gaps longer than 120 seconds. The price analysis annualizes those observed hours by the included-day count. It does not reconstruct billing-meter time. |
| **Was it really a full year?** | The release spans October 23, 2025 through October 22, 2026, with 363 included archive days and several partial days. It is a roughly annual observation window, not uninterrupted surveillance. |
| **What about UAT, ADS-R, MLAT and aircraft outside ADS-B-required airspace?** | The processing recognizes multiple observation sources, including ADS-R rebroadcasts. Nevertheless, equipment and coverage vary. Missing observations do not establish no flying, especially at low altitude. |
| **Does near-zero price correlation mean schools should not lower prices?** | No. This is a pooled observational association in a selected sample. It does not estimate demand elasticity or a causal pricing effect. |
| **Is a classifier score of 0.7 a 70% chance it is a trainer?** | No calibration is demonstrated. The model is class-weighted and trained against incomplete website labels. Scores rank candidates; they are not established real-world probabilities. |
| **What did cross-validation actually hold out?** | Individual aircraft in stratified folds. The make/model prior is recomputed inside each fold. There is no reported registrant-held-out, airport-held-out or future-period evaluation. |
| **How good is the school detector?** | Against website labels: average precision 0.415 versus a 0.037 label base rate; recall 0.854 and observed-label precision 0.234 at the 0.5 aircraft threshold. Missing labels make interpretation difficult. The operation candidates use the separate 0.7 threshold and grouping rules. |
| **Are the 372 new schools?** | No. They are registrant groups missing from our existing fleet links. An earlier hand check found 12 schools, two clubs, three leasebacks and three unclear entities among 20 candidates. |
| **What does the Florida surname test prove?** | That one selected linkage signal occurs far more frequently than under a shuffled control. It does not establish beneficial ownership or estimate the precision of all corporate matches. |
| **Did you inspect Montana’s underlying filings or just the summary?** | The repo records an inspected N970PM summary with no named member. It does not establish exhaustive review of that entity’s articles and annual reports. Say that plainly unless further inspection closes the gap. |
| **Can you really compute a transponder address from an N-number?** | Yes, for the standard U.S. allocation, and the project checked the mapping against all 317,972 registry rows. That does not prove a transmitter is correctly programmed, resolve a PIA address, or establish historical airframe identity after reassignment. |
| **Did you really keep AOPA and OpenSky out of the release?** | The release code drops AOPA names/contact/location fields, rebuilds names from cached sites or FAA records, drops legacy activity fields and recomputes activity from adsb.lol. That is an implemented provenance boundary. It is not a claim that discovery was independent of AOPA. [Release school construction](/Users/dew/dev/aether-talk/research/FlightSchoolMapping/scripts/38_schools.py:1) |
| **Is “FinCEN’s 2026 rule” a date error?** | No. The domestic-company exemption began under the March 2025 interim rule; the August 2026 final rule made it permanent. That part survives checking. It still does not turn Florida officers into beneficial owners. [FinCEN’s current explanation](https://www.fincen.gov/boi) |
| **Why did the airport counter fail?** | Variable coverage, an MSL endpoint cutoff that mishandled elevated fields, mismatched comparison years and imperfect operation classification. The result does not show that accurate ADS-B-based traffic estimation is impossible. |
| **Can I reproduce and update this?** | There are frozen releases, generators and provenance machinery. The entire original crawl and bill are not demonstrated as a fully reproducible $100 run. Code availability remains contingent on David’s decision; the download link should be independently usable. |

## 4. What is missing that would make this land

### 1. A thesis more precise than cheap extraction

> “Which queryable datasets do we wish existed?”

That is the right question, but the talk currently answers it with page counts and model prices.

The interesting claim is:

> “Extraction became cheap enough that building the dataset was worth attempting. Establishing what each row actually means remained the hard part.”

That connects aviation, AI and data engineering without requiring inflated completeness or accuracy claims.

### 2. One visible chain of evidence

> “Homepage in … ten aircraft with rates…”

Show an actual source fragment beside one resulting row:

- The exact tail and rate on the page.
- The model’s extracted values.
- The registry’s manufacturer/model.
- Observed activity and inferred airport.
- What remains unverified.

Distinguish **source statement**, **model output**, **deterministic match** and **inference** visually. A terminal full of green is weaker evidence than one inspectable row.

### 3. An evaluation of the thing people actually care about

> “Checked…”

The missing evaluation is:

> “For a representative sample, did we extract the right aircraft, attach the right rate and identify the right school—and what did we miss?”

The current high percentages mainly answer easier questions.

Before the talk, the highest-value additional work would be a small, recorded random audit of cached pages, including sites where extraction returned nothing. Report tail precision, missed tails, correct rate interpretation and stale-listing ambiguity separately. Do not replace this with another registry lookup.

### 4. A real engineering failure, not three illegal characters

> “The model’s mistakes / N16 O ME …”

An engineer can reject I and O with a regex in seconds. That is not an interesting account of what made the project hard.

Your repo contains better material:

- A broken HTTP batch index that required recovery.
- Cross-domain or entity-assignment mistakes.
- Advertised aircraft that pass registry checks but may no longer belong to the school.
- A geographic inference rule accidentally using MSL as proximity to the ground.
- A behavior-only classifier that mostly failed.

Pick one and explain what changed because of it. That is Hackers material.

### 5. One result that actually changes what you know

> “The joins…”

A join diagram is plumbing. The room needs a concrete example where the join resolves a question that neither input can answer.

The strongest choices are:

- A previously missed training-related entity, then confirmed from its own fleet page.
- A Florida LLC linked to a named corporate role, with the role explicitly distinguished from ownership.
- A stale advertised aircraft investigated far enough to distinguish a listing problem from a receiver problem.

The 6% statistic without investigating its causes produces a question, not yet a finding about idle airplanes.

### 6. A government gripe with an owner and a requested change

> “concretely enough for someone in the room to route a gripe”

The current slide gives them a complaint buffet.

Choose one specific request:

- **Registry document service:** provide downloadable document images and an index in bulk, with clearly documented exclusions and redactions.
- **Airport activity publication:** expose the estimate’s date, source and method alongside each count.
- **Training-provider data:** establish whether an existing government provider dataset can support a public business directory without exposing student/security information.

Distinguish a retrieval problem, a measurement problem and a deliberate disclosure restriction. A better scraper addresses only the first.

### 7. A maintained dataset, rather than a one-week trophy

> “Every flight school, its fleet and prices / Made.”

A March crawl is not a November fleet database merely because it joins to an October registry.

Be ready to state:

- Last observed source date.
- How stale prices and sold aircraft are represented.
- Whether a vanished listing is removed or retained historically.
- How contradictory offers are handled.
- How operators can correct a wrong association.

The persistent, useful artifact is more impressive than the cheap initial scrape.

### 8. An actual seven-minute cut

> “Backdrops only: one number, chart, image or record each.”

Slides 2, 3, 9 and 14 violate that instruction. The deck also grew from the 13-slide outline to 15 slides. Slide 2 alone is a source catalog; slide 3 is a separate talk about FAA publication practices.

For the stated seven-minute slot, I would use:

| Time | Content |
|---|---|
| 0:00–0:40 | The missing dataset and a carefully scoped definition |
| 0:40–1:15 | The sources actually used |
| 1:15–1:50 | Initial build, with honest cost accounting |
| 1:50–2:35 | Recorded demo and one inspectable row |
| 2:35–3:45 | What validation establishes, and what it does not |
| 3:45–5:00 | One substantive join result |
| 5:00–6:15 | One failure or ownership boundary, then one concrete dataset request |
| 6:15–7:00 | Download URL, scope, and the closing question |

Cut the minimum-hours comparison, the 100-hour-inspection aside, the revenue claim, the standalone malformed-tail slide and most of the source inventory. At twelve minutes, add a second finding with its caveat. Do not spend the extra five minutes reciting more datasets.
