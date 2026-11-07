#!/usr/bin/env python3
"""Package a release's public downloads into data/release/<release>/download/.

Every table here is built only from permitted sources (see LICENSES.md, which
this script writes next to the data):
  aircraft.parquet           FAA registry snapshot x adsb.lol archive
  unmatched.parquet          US-block addresses heard but not on the registry
  aircraft_day/YYYY-MM.parquet, legs/YYYY-MM.parquet   reduced adsb.lol traces
  schools.json               school records (school websites + FAA), no AOPA fields
  results.json, training.json, privacy.json            frozen findings
Nothing derived from OpenSky (no redistribution) or AOPA (no republication).

Usage: 42_package_release.py --release dev
"""

import argparse
import json
import shutil
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "data" / "history"
RELEASES = ROOT / "data" / "release"

LICENSES = """# Licenses and provenance

Release {release}. Window {first} → {last} ({days} days). Registry snapshot {registry}.

| File | Built from | License |
|---|---|---|
| `aircraft_day/*.parquet`, `legs/*.parquet` | adsb.lol globe_history daily archive, reduced per aircraft per day / per leg | ODbL 1.0 (derivative database, §4.4) |
| `aircraft.parquet` | FAA Releasable Aircraft Registry snapshot joined to the adsb.lol tables | ODbL 1.0 |
| `unmatched.parquet` | adsb.lol addresses in the US block absent from the registry snapshot | ODbL 1.0 |
| `schools.json` | Fleets and rates extracted from each school's own website (with `source_url`), FAA registry matches, and the adsb.lol activity above | ODbL 1.0 |
| `results.json`, `training.json`, `privacy.json` | Computed from the tables above | ODbL 1.0 |

Contains information from adsb.lol (https://github.com/adsblol/globe_history_2026), which is made
available here under the Open Database License (ODbL): https://opendatacommons.org/licenses/odbl/1-0/.
FAA registry data is a US government work in the public domain. The code that produced these files
is in https://github.com/dweekly/aether (research/FlightSchoolMapping/scripts), MIT licensed, which
together with these files satisfies ODbL §4.6.

Not included, by design: anything derived from the OpenSky Network (its terms forbid
redistribution) and any field from AOPA's directory (its terms forbid republication).
"""


# Fields held out of the downloadable schools.json; schools_full.json (the site's pages) keeps them.
# Flipping either is a one-line change here.
# description: each school's own words for its aircraft, held out pending David's call (2026-11-05).
DOWNLOAD_HOLD_ROW_FIELDS = ("description",)
# aopa_url: a link built from AOPA's directory id; the download carries no field from AOPA (LICENSES).
DOWNLOAD_HOLD_SCHOOL_FIELDS = ("aopa_url",)


def download_schools(full):
    """schools_full.json as published: the held-out fields removed from each school and its rows."""
    out = []
    for s in full["schools"]:
        s = {k: v for k, v in s.items() if k not in DOWNLOAD_HOLD_SCHOOL_FIELDS}
        for k in ("fleet", "listings"):
            s[k] = [{f: v for f, v in r.items() if f not in DOWNLOAD_HOLD_ROW_FIELDS} for r in s.get(k) or []]
        out.append(s)
    return {**full, "schools": out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", required=True)
    a = ap.parse_args()
    rel = RELEASES / a.release
    out = rel / "download"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    results = json.loads((rel / "results.json").read_text())
    win = results["window"]
    for f in ("aircraft.parquet", "unmatched.parquet", "results.json", "training.json", "privacy.json"):
        if (rel / f).exists():
            shutil.copy(rel / f, out / f)
    (out / "schools.json").write_text(json.dumps(download_schools(json.loads((rel / "schools_full.json").read_text())),
                                                 default=str))

    c = duckdb.connect()
    c.execute("SET threads TO 16")
    in_days = ", ".join(f"DATE '{d}'" for d in win["included_days"])  # frozen by 34
    for table in ("aircraft_day", "legs"):
        months = [r[0] for r in c.execute(f"""
            SELECT DISTINCT strftime(day, '%Y-%m') FROM read_parquet('{HISTORY / table / "*" / "*" / "*.parquet"}')
            WHERE day IN ({in_days}) ORDER BY 1""").fetchall()]
        (out / table).mkdir()
        for m in months:
            y, mm = m.split("-")
            c.execute(f"""COPY (SELECT * FROM read_parquet('{HISTORY / table / y / mm / "*.parquet"}')
                               WHERE day IN ({in_days})
                               ORDER BY day, hex)
                          TO '{out / table / f"{m}.parquet"}' (FORMAT parquet, COMPRESSION zstd)""")
    (out / "LICENSES.md").write_text(LICENSES.format(release=a.release, first=win["first_day"], last=win["last_day"],
                                                      days=win["days"], registry=results["registry_date"]))
    files = sorted(p for p in out.rglob("*") if p.is_file())
    manifest = [{"path": p.relative_to(out).as_posix(), "bytes": p.stat().st_size} for p in files]
    (out / "manifest.json").write_text(json.dumps({"release": a.release, "window": win, "files": manifest}, indent=1))
    print(f"{a.release}: {len(files)} files, {sum(m['bytes'] for m in manifest) / 1e6:.0f} MB, "
          f"largest {max(m['bytes'] for m in manifest) / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
