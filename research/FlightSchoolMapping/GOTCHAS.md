
## The R2 backup verified nothing for months (fixed 2026-08-24)

`verify()` piped `rclone check` straight into `grep -q` under `set -o pipefail`:

```sh
if rclone check … 2>&1 | grep -qE "ERROR|differences found: [1-9]"; then
```

`grep -q` exits on its first match, `rclone` then takes SIGPIPE and dies
non-zero, `pipefail` propagates that, and the `if` concludes "no problems"
**because problems were found**. Every run logged `verified OK`. The failure
pattern was unmatchable in any case: rclone prints `N differences found`, not
`differences found: N`.

Measured cost: `aether-research/adsb-snapshots`, `/extract` and `/crawl-cache`
all held **zero objects** on 2026-08-24 while the log said `verified OK` nightly.
14 GB of irreplaceable ADS-B history existed on one disk.

Fixed by capturing output first, then matching — and by requiring the
`0 differences found` summary to be present, so a check that compared nothing
cannot pass. Verified both ways: it now returns 1 against an empty prefix
(`8126 files missing`) and 0 with a count against the real one.

## `--s3-no-head` is required, not optional

Without it every PUT is followed by a HEAD carrying the versionId R2 returns.
R2 has no versioning, that HEAD 501s, and rclone reports a *successful* upload as
failed. The bytes land — one ADS-B run produced 62,420 bogus `ERROR` lines, which
is exactly how a real failure gets buried. Same flag, same reasoning as
`~/dev/nanoclaw-data/scripts/backup-r2.sh`.

## The snapshot history has a permanent 85-day hole

No snapshots exist for 2026-04-23 → 2026-07-16: the cron job did not run (zero
log lines in the window; last run Apr 22 21:15 UTC, next Jul 17 05:00 UTC).
ADS-B is point-in-time and cannot be backfilled. Present: 2026-03-05 → 04-22 and
2026-07-17 → now, at a clean 96 files/day.

## adsb.lol caps each IP at 500 requests a day (found 2026-10-23)

The response headers say so: `x-ratelimit-limit: 500, 4;w=1, 60;w=60, 500;w=86400`.
The 15-minute poller made 35 tile requests per run, about 3,360 a day, from the same
public IP that monster4 and trogdor share. From about 2026-10-17 adsb.lol's yield fell
by two-thirds, and any ad-hoc request from home got a 429 with `x-ratelimit-remaining: 0`.
The README only says limits are "dynamic", so read the headers, not the docs. The poller
was retired the same day; the daily ODbL archive (`adsblol/globe_history_*`) replaces it.

## adsb.lol ships replicas of different sizes, and some days unsplit (found 2026-10-23)

Each day is published by several readsb pods (`prod-0`, `staging-0`, plus an
`mlatonly-0`). They are not equal: for 2026-03-24 prod is 1,961 MiB and staging
2,948 MiB, and adsb.lol's own instructions say to ignore a replica that is
significantly smaller. `30_adsblol_history.py --instance auto` (the default)
takes the larger; `31_history_backfill.py --recheck` re-ingests days taken from
an undersized one. Most days are split into `.tar.aa`, `.tar.ab`, ... but some
(2025-01-01) ship one unsplit `.tar`. Archive members are `./`-prefixed.

## `pkill -f` kills the ssh session that runs it (2026-10-23)

`ssh trogdor 'pkill -f 39_opensky_mondays; ...'` matched its own `bash -c`
command line and killed itself (exit 255). Use the bracket trick:
`pkill -f "[3]9_opensky_mondays.py --parallel"`. Same trap as LESSONS.md's
pgrep note. Background jobs started over ssh also need `setsid nohup ... < /dev/null`
(and `ssh -n`) or the ssh call hangs until they exit.

The bracket trick only hides the pattern itself. It still matches any process whose
command line contains the literal text somewhere else: the same ssh call restarting
the job (`pkill -f "[3]9_x --parallel 4"; ... 39_x --parallel 4 &` killed itself
again), or a parent `bash -c` that wrote a chain script by heredoc (the first
`aether-chain.sh` waited forever on its own parent). Kill by PID, wait on `$!` in
one script, or start jobs from a script file so no command line carries the text.

## OpenSky's Monday archive changes layout by date (2026-10-23)

Days before mid-2020 live under dot-prefixed keys (`states/.2017-06-12/`), and
an hour may be a `.csv.tar` holding a `.csv.gz` or a bare `.csv.gz`, with or
without the dot in the filename. `39_opensky_mondays.py` lists each day's keys
rather than constructing names.

## A day's release can sit in the next year's repo (2026-10-23)

adsb.lol files each release under the year it was uploaded, not the day it covers:
2025-12-31's three replicas are in `globe_history_2026`. The backfill looked only in
`globe_history_2025` and logged the day as having no release. `30_adsblol_history.py`
now tries the next year's repo on a 404.

## Mid-2025 days ship under `-0tmp` replica names (2026-10-24)

From 2025-05-23 to 06-12 adsb.lol also published `prod-0tmp` and `staging-0tmp` releases.
For 2025-05-28 → 06-10 those are the only replicas, and on 06-11 the plain ones are a
fifth the size (0.73 GB vs 3.58 GB). The backfill looked only for `prod-0`/`staging-0`,
so it logged 14 days as missing and took the stub for 06-11. `--instance auto` now
weighs all four names. List a year's tags (`gh api repos/adsblol/globe_history_YYYY/releases`)
before assuming the naming is fixed.
