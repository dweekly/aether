# Flight School Mapping — Roadmap

## Data Expansion

### Rate coverage gap
Only 754 of 4,825 schools (16%) have rental rate data — 4,387 of 16,881 aircraft (26%).
The remaining 74% of the fleet is identified via FAA registry + ADS-B but lacks scraped rates.

**Priority targets:**
- Schools with large ADS-B-active fleets but no rate data (high-value, provably active)
- Schools with PDF rate sheets not yet extracted (Phase 5 pipeline exists but incomplete)
- Re-crawl schools whose websites have changed since initial scrape

### Story ideas
- "Which training fleets are actually flying?" — ADS-B activity rates vs. fleet size
- "The GA training fleet is old" — age distribution, regional differences
- "Flying clubs vs. Part 141 vs. Part 61" — fleet size, rates, aircraft mix by school type
- "Where are the Cirrus trainers?" — geographic distribution of modern vs. legacy fleets

## Tech Debt
- Deduplicate `src/data/` and `public/data/` in website (charts fetch from public/)
- OG images for stories (screenshot a chart)
- Mobile testing pass on all chart components
