# Hackers 2026 talk: prior art and fact check (2026-11-07)

**Why.** Five hours before the talk, a research pass for prior art, factual risks and improvements. Gemini (`gemini-4-argon-high` through `ask-model`) wrote the first pass from the deck's text and notes; agy headless cannot open pages, so every claim below was then checked by Claude against the source itself (WebFetch, web search, or the Federal Register text on govinfo). Claims that could not be confirmed are listed separately and should not be used.

## Verified prior art

| Work | What it found | Overlap with the talk |
|---|---|---|
| Samip Shah (CFI), PilotBound, [Cessna 172 rental cost](https://pilotbound.app/aircraft/cessna-172) (updated 2026-11-07) | $180/h wet median, IQR $165–$195.60, 703 C172s at 256 schools, tails matched to the FAA releasable database | Slides 3–4 ($177, 1,318 C172s at 389 schools). Independent, by a different method; same answer |
| PilotBound, [Fleet age report](https://pilotbound.app/research/fleet-age) (updated 2026-11-07) | Median build year 1978 over 1,750 tail-matched school aircraft (568 rated schools); Cirrus 102 of 232 built since 2010 (44%) | Slide 9 (cliff, 1978, Cirrus share) |
| PilotBound flight-training cost report, covered by [AVweb](https://avweb.com/aviation-news/private-pilot-certificate-now-18257/) (2026-09) and [General Aviation News](https://generalaviationnews.com/2026/07/31/new-tool-calculates-the-true-cost-of-flight-training/) (2026-07-31) | PPL about $18,000 from ~500 schools' posted rates | Context for slide 4 |
| [Flight School Rates](https://flightschoolrates.com/methodology/) (operator unnamed) | 74 schools in 4 metros, crawler `OmniSitesResearchBot/1.0` obeying robots.txt and terms; 47 post rates | Same method at small scale; their terms-exclusion rule is a good practice to mention |
| Redbird, State of Flight Training 2026 ([AVweb](https://avweb.com/flight-safety/flight-training/survey-private-pilot-flight-training-cost/)) | Survey-based; PPL median above $16,000 | Survey vs posted prices |

Not checked, from Gemini, plausible and well known: Boston Globe Spotlight "Secrets in the Sky" (2017) and GAO-20-164 (2020) on opaque aircraft ownership.

## Verified factual corrections to the speaker's words or slides

- **"FCC" (wishlist slide, spoken):** drones are registered by the FAA. The deck's own notes already flag this.
- **TSA "~13,000 providers" (landscape and wishlist slides):** the TSA final rule (89 FR 35580, 2024-05-01) says "Since the inception of the program, approximately 9,000 of the 13,000 flight training providers registered with TSA operate under 14 CFR part 61" (individual instructors), 500 under parts 121/135, about 3,500 under 141/142. Its paperwork estimate: about 4,206 providers actively training candidates and 19,738 who train only U.S. citizens. So 13,000 is cumulative and mostly individual CFIs, not a list of schools. Source: [govinfo text](https://www.govinfo.gov/content/pkg/FR-2024-05-01/html/2024-08800.htm).
- **"One CD per plane" (wishlist):** FAA CARES lets anyone with a free Login.gov account view and download an aircraft's records as PDFs, one aircraft at a time ([Airworthy help](https://help.airworthy.com/download-faa-aircraft-records)). The ask is still valid as "in bulk", but "CD" is out of date.
- **Registry privacy (LLC slide, a likely question):** under Section 803 of the 2024 FAA reauthorization, since 2025-03-28 owners can ask the FAA to withhold their name and address from public FAA registry sites; the FAA is weighing making that the default ([AIN](https://ainonline.com/aviation-news/general-aviation/2025-03-31/faa-allows-owners-shield-registration-details), [Husch Blackwell](https://www.huschblackwell.com/newsandinsights/faa-implements-section-803s-data-privacy-provisions)).
- **Your own April story contradicts the geography slide:** [aether-flight.com/stories/cessna-172-rental-cost](https://aether-flight.com/stories/cessna-172-rental-cost/) (2026-04-05) says "Geography is the single biggest predictor of what you'll pay"; the slide now says within-metro spread beats between-metro spread. Either say why the view changed or retire the line on the site.
- Already flagged in the deck's notes and confirmed again: "vast majority" of aircraft in LLCs (28% overall, 44% of new); "made in the 90s" (1987–1996 gap; 172R/S from 1996); "public domain" vs ODbL; FinCEN's 2025 rule exempts US companies.

## Verified timely news

- ADS-B-based landing-fee billing bans: Montana (2025-05), Florida SB 422 (signed 2026-04-23, [AOPA](https://www.aopa.org/news-and-media/all-news/2026/april/30/florida-becomes-second-state-to-ban-ads-b-based-billing)), Louisiana (2026); the federal ALERT Act carries a national ban. Relevant to the "ADS-B is public" framing.

## Not confirmed; do not use

- "Marble Institute Zipline report" (marbletech.us): no trace found.
- "WIFE APPROVED LLC" as a viral Las Vegas Class B bust: no support; N731NR is a 1980 P210N registered in Torrance, CA.
- Spribe *Aviator* domain drop-catching as a known SEO tactic: plausible, unsourced.
- Gemini's PilotBound "Sept 3 cost report" URL and AVweb URL were wrong (404); the working URLs are above.
