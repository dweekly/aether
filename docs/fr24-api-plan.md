# Flightradar24 API as an internal input

**Why.** Our flight history comes from adsb.lol and OpenSky. Both depend on volunteer receivers, so small planes flying low, far from a receiver, go unseen. Flightradar24's paid API adds its own receiver network and MLAT. Its data cannot be republished, so it feeds analysis and aggregate findings only. Nothing from it goes into the ODbL release or the explorer (David, 2026-11-06).

**Route.** Use only the official API through `fr24sdk` (0.3.1, `FR24_API_TOKEN` read from `.env`). Do not use the unofficial `fr24` gRPC library or `FlightRadarAPI`. They depend on private web endpoints that FR24 has put behind bot protection, and we don't get around bot checks.

## Plans and prices (read 2026-11-06 from fr24api.flightradar24.com)

| Plan | Price | Credits/month (doubled until 2026-12-31) | Rate | History |
|---|---|---|---|---|
| Explorer | $9 | 60,000 | 10/min | 30 days |
| Essential | $90 | 666,000 | 30/min | 2 years |
| Advanced | $900 | 8,100,000 | 90/min | all |

Credits charged per returned row:

| Endpoint | Credits |
|---|---|
| Flight summary light, flight older than 30 days | 3 |
| Flight summary full, flight older than 30 days | 6 |
| Historic positions light | 6 |
| Historic positions full | 8 |
| Flight tracks | 40 |
| `/count` call | 15% of full, rounded up (about 1 per call) |
| Empty result | 1 |

Each call accepts up to 15 registrations and a window of at most 14 days. Top-up credits cost about $0.30 per 1,000. The FAQ says commercial use is allowed under FR24's terms.

## What it would answer, sized against our data

| Question | Method | Size | Credits |
|---|---|---|---|
| How many days do Montana-LLC aircraft spend in Montana? This is the Idaho Tax Commission's method. | Flight summary light by registration, 12 months. Count the days with a landing in MT and in each other state. | About 3,000 Montana-address LLC aircraft at about 20 flights a year | ~180,000 |
| Does adsb.lol miss school flights? This tests "78% flew" and the 181-hour median. | `/count` per tail, 26 windows a year | 11,867 valid school tails | ~310,000 calls/credits. The 30/min rate limit means about 7 days of wall time. |
| Can FR24 place schools we couldn't put at an airport? | Flight summary light for those schools' tails, 12 months, origin and destination | 2,817 tails across about 1,100 schools | ~150,000 if half of them fly |
| First check, before any of the above | Compare FR24 and our data for 200 random school tails over 3 months | 200 tails | ~20,000 |

Everything fits in one Essential month at the doubled credits, except that the full per-tail count is bound by the rate limit. Start with the 200-tail check. If FR24 sees little that we miss, stop there.

## Output rules

- Aggregates only. Never tails or owners for LLC findings. FR24 rows stay in a git-ignored `data/fr24/` cache.
- Every finding states that FR24 was the source and where its coverage stops.

## Subscription

Essential started 2026-11-06 ($90/month). The token is in `/Users/dew/dev/aether/.env` as `FR24_API_TOKEN`. **Cancel before the renewal around 2026-12-06** once the pulls are done; there's a Todoist reminder for 2026-11-30.
