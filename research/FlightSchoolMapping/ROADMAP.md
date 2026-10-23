# Flight School Mapping — Roadmap

## Collection health (found 2026-10-23)

- **adsb.lol yield fell ~2/3 from 2026-10-17.** Daily mean per snapshot went from ~4,000
  to ~1,500 aircraft; most tiles now 429 or fail. OpenSky (~7,900/snapshot) is healthy, so
  merged totals dropped far less. adsb.lol publishes no fixed limit ("dynamic based on the
  environment load") and says API keys will come from feeding. Options, cheapest first:
  spread the 35 tiles across the 15-minute window instead of 1/s; send a User-Agent with a
  contact address; run a feeder; ask the maintainer.
- **The ADS-B collector has no status checker.** monster4's copy of the cron failed every
  15 minutes from 2026-04-22 to 2026-10-23 (a Homebrew upgrade removed the venv's Python)
  and nothing noticed. trogdor's collector needs a daily reader that alerts on missing
  snapshots or a collapsed per-source count, like `~/forgejo/check-backup-status.sh`.

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
- `output/adsb_activity_summary.json` (55 MB) and `adsb_activity.csv` are committed and
  rewritten on every ADS-B fold, so each rebuild commit adds tens of MB to history. They
  are derived from the snapshots: move them to R2 with the other big blobs, out of git.
- Website deploy path to Cloudflare is not recorded anywhere; write it down or script it.
- monster4 still holds a stale ~18 GB copy of `data/` and `output/` (a checksum-verified
  subset of trogdor's). Safe to delete once trogdor + R2 are the agreed home.
- Stale remote branches `claude/adsb-data-snapshot-P5LM4`, `claude/update-night-flying-ads-2AKrz`,
  `claude/flight-schools-aircraft-mapping-thAqt`: superseded by main except two March 6
  snapshots that only exist on the first one.
- Deduplicate `src/data/` and `public/data/` in website (charts fetch from public/)
- OG images for stories (screenshot a chart)
- Mobile testing pass on all chart components
