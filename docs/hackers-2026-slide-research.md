# Hackers 2026: research behind three slides

Three questions for the talk, each checked against data or a primary source, with one chart each.
The site checks come from `research/FlightSchoolMapping/scripts/62_blocked_sites.py` (run on trogdor) and the charts from
`64_slide_charts.py` (run on the Mac). Numbers are from runs on 2026-11-04 and 2026-11-05 (UTC). The commands are at the end.

| Slide | Chart | The point |
|---|---|---|
| Who blocks us | `docs/hackers-2026-charts/backdrops/blocked.png` | The new blocks are mostly hosting platforms reacting to our crawler, not schools adding anti-AI rules. |
| The 1986 cliff | `docs/hackers-2026-charts/backdrops/yearbuilt_make.png` | New small-plane shipments fell 97% from 1978 to 1994, so training fleets are 1970s airplanes. Half the four-seat singles built since 2010 that fly today are Cirrus. |
| What a 172 costs | `docs/hackers-2026-charts/backdrops/wetrate_map.png` (alternative: `wetrate_spread.png`) | Location moves the hourly price less than the choice of school does, and the Bay Area sits in the middle. |

## 1. Why 207 school sites now refuse our crawler

> **Headline.** Of the 207 school sites that served our plain crawler in March and refused it in November, 102 are on
> SiteGround and 33 on WordPress.com. Each of those platforms now refuses us on essentially every school site it hosts
> (SiteGround 102 of 103, WordPress.com 33 of 33). Cloudflare fronts 35 of the 207, but only 35 of the 807 Cloudflare
> sites in our list (4%) refuse us. Robots.txt rules show no sign of an anti-AI rollout: 4 of 47 blocked sites added
> AI-crawler rules since their last Archive capture before April 2026, against 5 of 146 in a random sample of sites that
> still serve us (a difference this small sample cannot tell from chance, Fisher p = 0.22). None of the 2,070 robots.txt
> files we could read names our crawler.

> **Correction to the working hypothesis.** "Schools rolling out anti-AI-scraper defenses" does not fit most of the 207.
> About two-thirds are platform-wide bot filters that the school never configured. SiteGround's filter appears to have
> flagged our network address. WordPress.com refuses our crawler's user agent on every hosted school site, while serving
> a browser's user agent from the same address. Another 11 of the 207 were not blocks at all: the domain was sold or
> renamed. Cloudflare's AI-crawler defaults are real (dates below), but they apply per site, mostly to new domains,
> and account for at most the 35 Cloudflare refusals, 4% of the Cloudflare-fronted school sites in our list.

### Method

- **Which 207.** `62_blocked_sites.py lists` rebuilds 54's site statuses with 54's own functions and inputs (production
  `output/` for March, `data/research-2026-11/recrawl/` for November). A site counts when the plain HTTP or expansion
  crawler (user agent `AetherResearch/1.0 (aviation research; contact: research@aether.aero)`) got its pages in March and
  every November crawler got a refusal or a challenge. The result matches `docs/hackers-2026-march-vs-november.md`: 207.
  The control is the 2,651 sites a crawler reached in both months.
- **What answered in November.** The November crawl saved no headers or bodies for refusals from its HTTP crawler. Its
  browser crawler (headless, no evasion) saved a page for 121 of the 207, and those pages were classified by their markers.
- **A fresh request, 2026-11-04 23:54 UTC.** One GET to each of the 207 homepages from trogdor, which shares monster4's
  public address. It used Python `requests` with a desktop Chrome user agent and followed redirects, but did not solve
  any challenge or run JavaScript. Status, headers and the first 6 KB of the body were saved. A refusal was classified by
  its markers: SiteGround (`SG-Captcha: challenge` header, `/.well-known/sgcaptcha/` body); Cloudflare (`cf-mitigated`,
  "Just a moment", `challenge-platform`, or a `cloudflare` server with 403/429/503); WordPress.com ("Checking your
  browser" page with `/__challenge/` and `HASHCASH_CONFIG`); Sucuri, Wordfence, Imunify360, Imperva, Akamai, AWS WAF and
  Vercel by their documented markers. A site was **moved** when the fresh request landed on another domain (five on
  HugeDomains or GoDaddy for-sale pages, six on the school's new domain).
- **Same address, different user agent.** November's refusal went to our crawler's user agent. The fresh request used a
  browser's user agent from the same address on the same day. If that got the page, the filter keys on the user agent.
  If it got a challenge too, the filter keys on the address or on clients that do not run JavaScript.
- **robots.txt now and then.** `/robots.txt` was fetched once for all 2,858 sites with the same user agent. A readable
  file is a 200 that is not an HTML page. The "then" copy is the Internet Archive's last 200 capture from 2025-01-01
  through 2026-03-31, found through the CDX API and fetched raw. A site "blocks AI crawlers" when a group naming
  GPTBot, ClaudeBot, anthropic-ai, CCBot, Google-Extended, PerplexityBot or Bytespider has `Disallow: /`. The control's
  Archive lookups ran in a seeded random order (seed 20261104) and were stopped after 429 of the 2,651 sites, at the
  Archive's pace of about ten lookups a minute, so the control's then-and-now figures are a random sample. Half the
  baseline captures are from October 2025 or later; the rest are older, so "added since" means since that capture,
  which can be up to 15 months before March.
- **Platform share.** For each platform, the blocked sites it answered for are compared with the control sites whose
  robots.txt answer carried its headers. SiteGround is identified by its `Host-Header` value
  `8441280b0c35cbc1147f8ba998a563a7`, `X-Proxy-Cache-Info` or `SG-Captcha`; WordPress.com by `Host-Header: WordPress.com`
  or `wpcloud`, or `x-ac: …atomic`; Cloudflare by `server: cloudflare` or `cf-ray`. A control site with no robots.txt
  answer (152) has no platform, so the shares are slight overestimates.
- **The Archive's own view.** For each blocked site, `probe --timeline` lists the Archive's homepage captures since 2025
  with their status codes. The Archive's crawler comes from other addresses, so this shows whether the same sites
  served someone else in 2026.

### Results

Who answered for the 207, and whether the same address with a browser's user agent got the page:

| Who answered | Sites | Refused a browser's user agent too | Served a browser's user agent |
|---|---:|---:|---:|
| SiteGround | 102 | 102 | 0 |
| Cloudflare | 35 | 18 | 17 |
| WordPress.com | 33 | 0 | 33 |
| Other hosts and firewalls (Squarespace, Akamai, Vercel, CloudFront, unidentified) | 17 | 9 | 8 |
| Domain sold or renamed | 11 | | |
| Unreachable now (TLS errors, timeouts) | 9 | | |

Of each platform's school sites in our list, the share refusing us now:

| Platform | Refuse us | Serve us | Share refusing |
|---|---:|---:|---:|
| SiteGround | 102 | 1 | 99% |
| WordPress.com | 33 | 0 | 100% |
| Cloudflare | 35 | 772 | 4% |

The one SiteGround control site (iowalakes.edu) served the November crawl but challenged our robots.txt request on
2026-11-04. By name servers (`dig +short NS`), 56 blocked sites and 5 control sites use SiteGround DNS. None of those 5
control sites answered with SiteGround's web-server headers: two answered through Cloudflare, two from other servers,
and one is served by Vercel.

**SiteGround.** The challenge page's fallback link carries `ipr:` and our public address
(`/.well-known/captcha/?y=ipr:<our address>:…`). SiteGround's own description of the system (blog, 2017-05-04, "How
Our New Anti-Bot AI Prevents Millions of Brute-Force Attacks", read through the Archive's 2026-10-18 capture because
siteground.com itself challenged us) says it "is able to collect and analyze simultaneously the data from all our
servers". Among its signals is the "number of simultaneous connections to different URLs". It also says "once our
system flags a certain IP address or user agent as malicious, it's been immediately blocked and challenged with a
Captcha page". A crawl that visits a hundred SiteGround sites from one address in a few minutes fits that description.
The Archive's crawler, coming from other addresses, got a 200 on its last 2026 capture of 35 of the 38 SiteGround sites
it had captured in 2026. siteground.com's own blog challenged our address too, with a browser's user agent. So the
evidence points to our address being flagged across SiteGround's network, not to 102 schools each turning something on.
This is an inference from the marker, the documentation and the Archive's captures; a request from a second address
would settle it, and we did not make one. The system dates from 2017, so it is not an AI-era rollout. What changed
between March and November is that it flagged us.

**WordPress.com.** All 33 refused our crawler's user agent in November (403), and 14 showed the headless browser a
"Checking your browser…" challenge. On 2026-11-04, a browser's user agent from the same address got the page on all 33.
WordPress.com documents that challenge as "defensive mode" (support page "Defensive mode", read 2026-11-04). It is
opt-in on Business and Commerce plans, but "WordPress.com staff may enable defensive mode on your behalf if your site is
attacked". The user-agent refusal held across every WordPress.com school site in our list, so it is a platform rule,
not a per-school setting. We found no WordPress.com document describing a user-agent rule.

**Cloudflare.** Cloudflare's bot settings are per site: 772 Cloudflare-fronted school sites still serve us. Of the 35
that refuse, 17 serve a browser's user agent (a rule on the user agent, such as Block AI Bots or Bot Fight Mode; we
cannot see which from outside) and 18 challenge any client that does not run JavaScript. Dated defaults from Cloudflare:

- 2024-09: a one-click option to block AI crawlers (Cloudflare press release of 2025-07-01).
- 2025-07-01: "every new domain will now be asked if they want to allow AI crawlers… every new domain starts with the
  default of control" (Cloudflare press release, 2025-07-01).
- 2026-07-01, effective 2026-09-15: "new domains onboarding to Cloudflare receive updated defaults". Training and Agent
  crawlers are blocked on pages that display ads, and Search stays allowed (Cloudflare changelog and blog
  "content-independence-day-ai-options", 2026-07-01). Neither says existing zones change. Customers who already had
  managed robots.txt get an added `use=reference` content signal.

None of these targets a user agent like ours by name. Whether any of the 35 changed a setting because of these launches
cannot be seen from outside.

**robots.txt.**

| | Blocked (207) | Control (2,651) |
|---|---:|---:|
| Readable robots.txt now | 75 | 1,995 |
| …of which shut out at least one of the seven AI crawlers | 7 (9%) | 103 (5%) |
| …carrying Cloudflare's managed block (`BEGIN Cloudflare Managed Content`) | 2 | 67 |
| …carrying a `Content-Signal` line | 4 | 111 |
| Readable both now and in the Archive before April 2026 | 47 | 146 of a random 429 |
| …added an AI-crawler block | 4 (9%) | 5 (3%) |
| …removed one | 1 | 2 |
| Name our crawler (`aether`, case-insensitive), any file | 0 | 0 |

Most blocked sites have no readable robots.txt now because SiteGround challenges that request too. Of the 4 blocked
sites that added AI-crawler rules, 2 carry Cloudflare's managed block, 1 is on Squarespace and 1 is on Cloudflare with
a hand-written list. robots.txt is advisory and none of these files names us, so none of them caused a refusal.

**Is it interesting?** Yes, but as a correction. The web is closing to robots, and for a small business the decision
sits with its host: one platform's bot filter shut us out of 102 school sites at once. The AI-specific defaults that
Cloudflare publicised reach 4–5% of school sites through robots.txt and do not explain the 207.

## 2. The 1986 cliff and the Cirrus rise

> **Headline.** US makers shipped 14,398 new single-engine piston airplanes in 1978 and 444 in 1994, a 97% drop (GAMA).
> Of the four-seat piston singles seen flying in the past year, 12,345 were built in 1975–79 and 1,182 in 1987–96.
> 62% of the four-seaters on school fleets were built in 1986 or earlier (median year 1979). Of the four-seaters built
> since 2010 that flew in the past year, 51% are Cirrus.

> **Correction to the working assumption.** The collapse started before 1986. Shipments fell from 13,286 in 1979 to
> 2,871 in 1982, four years before Cessna stopped. 1986 is when Cessna gave up, not when the market fell. And on school
> fleets Cirrus is not the leader among new airplanes overall: of school four-seaters built since 2010, 50% are Piper
> (almost all PA-28-181 Archers) and 32% Cirrus. The Archers sit mostly with airline academies and universities that
> we matched through the registry. On fleets read off school websites alone, Cirrus is 63% of those built since 2010.

### Definitions

- **Four-seat piston single:** FAA registry `aircraft_type` "Fixed wing single engine", engine type Reciprocating,
  4 Cycle or 2 Cycle, and 4 or 5 seats in ACFTREF (the seat count includes the pilot). This covers the Cessna 172 and
  182, the Piper PA-28, the Cirrus SR20 and SR22, the Diamond DA40 and the Beech and Mooney four-seaters. It leaves out
  two-seat trainers (Cessna 152, Diamond DA20) and six-seaters. Year built 1940–2026. Registry snapshot 2026-10-23
  (release `v0.3-20261023` `aircraft.parquet`).
- **Make:** Cessna is `CESSNA*` plus `TEXTRON AVIATION INC` models starting with a digit or T+digit. Textron registers
  new 172S, 182T and T206H airplanes under its own name, and its Beech G36 stays in Other. Piper is `PIPER*`, Cirrus is
  `CIRRUS*`, Diamond is `DIAMOND*`, and everything else is Other (mostly Beech, Mooney and Grumman American).
- **Seen flying:** `days_airborne > 0` in the release's ADS-B window, 2025-10-23 to 2026-10-22.
- **School fleets:** every tail on `output/flight_line.json` fleets (5,458 schools, this branch). That includes 1,555
  entries found by registry address match, some of which are not schools (Civil Air Patrol, a pipeline-patrol company).
  The website-only figures are given alongside.

### Numbers

| | School fleets | School websites only | Seen flying |
|---|---:|---:|---:|
| Four-seat piston singles | 8,447 | 3,811 | 62,397 |
| Median year built | 1979 | 1979 | 1976 |
| Built 1986 or earlier | 61.7% | 66.9% | 68.6% |
| Built in the 1970s | 36.7% | 43.8% | 30.4% |
| Cirrus, all years | 7.6% | 9.4% | 12.4% |
| Cirrus, built 1997–2009 | 9.1% | 6.6% | 30.9% |
| Cirrus, built since 2010 | 31.9% | 62.7% | 51.3% |
| Piper, built since 2010 | 49.6% | 14.7% | 13.2% |
| Cessna, built since 2010 | 13.6% | 19.4% | 19.7% |

New single-engine piston airplanes shipped by US makers (GAMA): 1966 13,250; 1978 14,398 (the peak); 1979 13,286; 1982
2,871; 1986 985; 1994 444 (the low); 1997 898; 2006 2,208; 2019 825. The series is in
`docs/hackers-2026-gama-piston-shipments.csv`, from GAMA's 2016 Databook table 1.5 (1959–1969) and 2019 Databook table
1.4 (1970–2019). The two tables agree on every year they share (1970–2016). It covers US-built airplanes only, so it
leaves out Diamond (Austria and Canada) and other imports. GAMA's 2025 annual report has no historical table.

### Sources for the history

- **Cessna stopped in 1986 and restarted in 1996.** AOPA Pilot, "Newfound independence", September 1996: Cessna
  "stopped building piston-powered aircraft in 1986". The Independence, Kansas, factory opened on 1996-07-03, and the
  first new 172 was to be delivered "in January" (1997). The registry agrees: 102 Cessna piston singles of all seat
  counts with a Reciprocating engine built in 1986 are registered today, 0 to 2 a year for 1987–1996, and 186 for 1997.
- **GARA.** Public Law 103-298, approved 1994-08-17 (108 Stat. 1552, read from govinfo): an 18-year limitation period
  on suits against general aviation manufacturers.
- **Why it fell.** AOPA's account ties the halt to product liability, and Cessna's chairman promised Congress to resume
  production if liability reform passed. The data here shows the timing, not the causes. The 1979–82 drop came before
  the liability argument peaked, and what drove it is not something this data can show.

## 3. What a Cessna 172 rents for, by place

> **Headline.** The median posted wet rate for a Cessna 172 is $170 an hour across 524 lower-48 schools. Among the 12
> metros with at least 10 schools posting one, the median runs from $157 (Detroit) to $190 (New York): $33 an hour, or
> $1,320 over the FAA's 40-hour minimum for a private certificate. The middle half of schools within a typical metro
> spans $39, so the choice of school moves the price more than the choice of city.

> **Contradicts the working assumption.** The Bay Area is not expensive for this: $172 median (20 schools; $130 to
> $235), mid-pack and $2 above the national median. The priciest metros are New York ($190), Atlanta ($182) and
> Seattle and Washington ($178).

### Method

- **Rates:** `output/flight_line.json` fleet entries with `wet_rate_hr`, as posted on school websites: the March 2026
  crawl, plus 79 of the 524 schools that were added from the November 2026 crawl. An entry counts as a 172 when its registry make is Cessna or Textron and its model starts "172" (not
  172RG), or, with no registry match, when its display name has "172". Entries mentioning floats, seaplanes or
  amphibians are left out, and so is any school whose first airport is a seaplane base. Each school contributes one
  value, the median of its 172 rates. 555 schools have one; 20 have no airport we can place, 1 is at a seaplane base
  and 10 are outside the lower 48, leaving 524.
- **Place:** the school's first listed airport with coordinates in OurAirports, located by point-in-polygon in the
  Census Bureau's 2023 cartographic boundary files (`cb_2023_us_state_20m`, `cb_2023_us_csa_500k`,
  `cb_2023_us_cbsa_500k`; public domain). A **metro** is the Combined Statistical Area if the airport is in one, else
  the CBSA. The Bay Area is the San Jose–San Francisco–Oakland CSA.
- **Minimum sample:** 10 schools for a state or metro (`--min-schools 10`). That leaves 19 states and 12 metros. States
  are shaded in fixed $10 classes, so equal steps in shade are equal steps in dollars.
- **Hours:** 14 CFR 61.109(a) requires at least 40 hours of flight time for a private pilot certificate with an
  airplane single-engine rating (eCFR, current 2026-11-03). Most students fly more than the minimum; we found no primary
  source for a typical figure, so the $1,320 is a floor.

### Numbers

| Metro (CSA) | Schools | Median | Middle half |
|---|---:|---:|---|
| New York–Newark | 15 | $190 | $164–207 |
| Atlanta–Athens | 14 | $182.50 | $151–206 |
| Seattle–Tacoma | 11 | $177.50 | $152–193 |
| Washington–Baltimore | 12 | $177.50 | $147–185 |
| Los Angeles–Long Beach | 36 | $175 | $161–194 |
| Chicago–Naperville | 11 | $175 | $159–205 |
| Dallas–Fort Worth | 12 | $175 | $160–191 |
| San Jose–San Francisco–Oakland | 20 | $171.75 | $149–204 |
| Houston–Pasadena | 10 | $170 | $158–195 |
| Phoenix–Mesa | 10 | $161.25 | $149–171 |
| Boston–Worcester–Providence | 11 | $161 | $155–180 |
| Detroit–Warren–Ann Arbor | 10 | $157 | $129–184 |

State medians (10+ schools) run from Minnesota $127.25 (10 schools, many of them clubs) and Wisconsin $140 to New
Jersey $190, with California $173.75 (82 schools). Every metro median is in `slide_charts.json`.

**Flying clubs** post lower rates: a $135 median across 66 clubs, against $175 for everyone else. Leaving clubs out,
only 7 metros keep 10 schools, running from $175 (Dallas–Fort Worth, Chicago) to $199 (New York), with the Bay Area at
$177.25. The ordering barely moves.

For the whole license, the posted private-pilot package prices are in `docs/hackers-2026-model-rerun.md`: a $12,000
national median, and by state CA $13,300 (18 schools), FL $11,275 (18), TX $13,500 (10). That sample is too small to
map.

**Which chart.** `wetrate_map.png` is the map that was asked for. `wetrate_spread.png` shows the same 12 metros as one
dot per school with the median marked, and it makes the point (school spread larger than city spread) more directly.

## Reproduce

On trogdor (production venv; the probe takes hours because of Archive rate limits, and it resumes where it stopped):

```sh
cd research/FlightSchoolMapping
PY=/mnt/data/home/dew/dev/aether/research/FlightSchoolMapping/scripts/.venv/bin/python
P=/mnt/data/home/dew/dev/aether/research/FlightSchoolMapping
OUT=/mnt/data/home/dew/scratch_blocked
$PY scripts/62_blocked_sites.py lists  --march-out $P/output --recrawl $P/data/research-2026-11/recrawl --out $OUT
$PY scripts/62_blocked_sites.py probe  --out $OUT              # live, robots.txt now, robots.txt in the Archive
$PY scripts/62_blocked_sites.py probe  --out $OUT --timeline   # the Archive's homepage captures of the 207
$PY scripts/62_blocked_sites.py report --recrawl $P/data/research-2026-11/recrawl --out $OUT
# name servers, for the SiteGround cross-check
python3 -c "import json;L=json.load(open('$OUT/lists.json'));print('\n'.join(list(L['blocked'])+list(L['ok'])))" \
  | xargs -P 16 -I{} sh -c 'echo {} $(dig +short NS {} | tr "\n" " ")' > $OUT/ns.txt
```

`report` writes `blocked_sites.json` and `blocked_sites.csv`, which are copied to `docs/hackers-2026-blocked-sites.json`
and `docs/hackers-2026-blocked-sites.csv` (one row per site, both groups).

On the Mac (worktree venv with matplotlib, duckdb, pyshp and shapely; Census files unzipped under `$CENSUS`):

```sh
cd research/FlightSchoolMapping
MPLCONFIGDIR=$TMPDIR/mpl scripts/.venv/bin/python scripts/64_slide_charts.py --fonts $FONTS \
  --flight-line output/flight_line.json --aircraft aircraft.parquet \
  --gama ../../docs/hackers-2026-gama-piston-shipments.csv --airports data/ourairports_airports.csv \
  --census $CENSUS --blocked ../../docs/hackers-2026-blocked-sites.json \
  --out ../../docs/hackers-2026-charts/backdrops
```

It writes the four PNGs and `slide_charts.json` (every number above for slides 2 and 3, plus the per-school rates).
