# Hackers 2026: enriching the school scrape before the talk

Written 2026-11-03. Stage: MVP (same as the Hackers plan). Talk: Saturday 2026-11-07, freeze Thursday 2026-11-05 evening.

## Why

The talk's claim is that a dataset nobody had can now be built in a week for about $100, and checked. Four measurements make that sharper, each scored by the same registry and flight-data check (`49_scrape_facts.py`) so the numbers are comparable:

1. **Today's models on the same pages.** Re-extract the cached March pages with Claude Haiku 5.5 and Claude Sonnet 5.5. Result: cost and accuracy per model for an identical job.
2. **What it costs to learn to fly.** The same re-extraction adds fields: instructor rate, private pilot package price, club dues and initiation, block-time discounts.
3. **March vs November.** Re-crawl every school site now, extract with Haiku 5.5, and compare per tail and per school: price changes, aircraft added or gone, sites that died.
4. **The 372 unlisted operations.** Search the web for each one's website, crawl and extract it, and see whether the sky-found schools are real schools with web presence.
5. **Error audit.** Open the source page for each of the 81 bad tails (63 malformed, 18 found nowhere) and classify it: typo on the school's page, model misread, or other.

A different solution would also serve if it produces measured numbers the talk can quote, with the same checks.

## Shared rules

- Work on trogdor in a separate worktree, never in the production checkout at `/mnt/data/home/dew/dev/aether` (its daily ingest cron runs from there). Read that checkout's `research/FlightSchoolMapping/output/` and `data/` as inputs only.
- Never overwrite an existing file in `output/` or `data/`. New outputs go under `research/FlightSchoolMapping/data/research-2026-11/<workstream>/`.
- Extraction for workstreams 1–4 uses one module, `scripts/extract_v2.py`: the March prompt and 50k-character page cap unchanged, plus the new fields, with Message Batches and structured output. Its prompt text is fixed before any batch is submitted, so every comparison is like for like.
- API budget for everything: $150. Each batch's estimated cost is logged before submit and its recorded usage after.
- AOPA: David allows AOPA directory fields for the talk (2026-11-03). They may appear on slides, marked as AOPA-derived. They stay out of the explorer, the downloads and the repo's published outputs.
- Every number for a slide comes out of a script that exits non-zero when its gate fails.

## Gates

| Workstream | Gate (a command that passes or fails) |
|---|---|
| 1 | `49_scrape_facts.py --results <run>` exits 0 for each model run, and the shuffled-airport control fails at ≥ a third of the real home match |
| 2 | A hand check of 20 random schools against their cached pages: every extracted price matches the page, recorded in the facts doc |
| 3 | Domains attempted ≥ 90% of March's; price comparisons only for the same tail at the same school, reported with counts |
| 4 | For every website found, at least one registered tail of that operation appears on the site, or the match is reported as unconfirmed |
| 5 | Every one of the 81 tails has a class and the URL of the page it came from |

## Out of scope

Publishing any of this in the explorer or the release, re-running the photo/VLM or PDF passes, a new school discovery crawl beyond the 372, changes to the March outputs.

## More school sources (probed 2026-11-04, not yet run)

What March used: AOPA's finder, the FAA Part 141 list, three web directories, Cirrus's locator, then state and federal side-lists, the 372 sky-found operations and FSAI's member directory (`docs/hackers-2026-fsai-directory.md`). Candidates for what's still missing, best first:

- **Common Crawl's URL index.** Every domain with a crawled path like `/discovery-flight`, `/aircraft-rental` or `/flight-training`, found with SQL over the columnar index on AWS Athena (a few dollars a query) instead of crawling the web ourselves. Also enumerates the customer paths of scheduling platforms (myFBO, Flight Circle, AircraftClubs). The free CDX index API returned 504s for those domains on 2026-11-04.
- **FAA airmen file.** The releasable airmen database lists certificate holders who allow their address to be released, with ratings including flight instructor. That gives where Part 61 instruction can happen (CFIs per town) rather than schools; joined to aircraft owners by name and address it finds instructors who own a trainer.
- **Niche associations.** The Soaring Society of America's club and commercial operator list, the Seaplane Pilots Association's school list, helicopter training lists and the airline pathway programs' partner schools. Small lists, but in segments the general directories cover worst.
- **OpenStreetMap.** Only 230 objects worldwide carry `amenity=flight_school` (taginfo, 2026-11-04). A California-only Overpass query by tag and name returned 52 objects, about 35 of them real training operations, of which about 6 are not in our list by a rough name and domain check (Aerocademy, Fresno Flight Center, Flight School Bakersfield, Golden State Flying Club, Hillside Aviation, Pacific States Aviation). Nationwide by name is too heavy for the public Overpass servers (502 and 504); run it on trogdor over the Geofabrik US extract instead.
- **Google Places.** "Flight school" is a Google business category, so a text search around each public-use airport would have the best recall, at a few hundred dollars of API calls, and Google's terms forbid storing the results, so it could inform the talk but not the public explorer.

Not useful: Certificate Transparency for scheduling platforms. myFBO, AircraftClubs, Flight Schedule Pro and Schedule Master serve customers from wildcard certificates or paths, so the logs name no customers (Cert Spotter, 2026-11-04; crt.sh returned 502).
