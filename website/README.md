# aether-flight.com

Static Astro site publishing data stories from the Flight School Mapping dataset.
One story so far: [What does it cost to rent a Cessna 172?](src/pages/stories/cessna-172-rental-cost.mdx).

## Build

```sh
npm install
npm run dev      # local preview
npm run build    # static output in dist/
```

## Data

Charts read pre-aggregated JSON, not the full dataset.
`scripts/prep_story_data.py` reads `research/FlightSchoolMapping/output/flight_line.json`
and `flight_schools_public.csv` from `~/dev/aether` and writes per-chart files under
`src/data/stories/<story>/`. Rerun it after rebuilding the dataset.

## Deploy

Served by Cloudflare at https://aether-flight.com. How `dist/` gets there is not
recorded in this repo (no GitHub deployment or webhook exists); see the roadmap.

`screenshots/2026-04/` holds QA captures from the April 2026 launch.
