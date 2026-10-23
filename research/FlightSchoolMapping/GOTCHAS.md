
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
