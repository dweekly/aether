#!/usr/bin/env python3
"""Join the dated FAA registry to every ingested archive day.

Writes data/release/<release>/:
  aircraft.parquet   one row per registered N-number: registry attributes plus
                     what the archive observed (or didn't) in the window
  unmatched.parquet  US-block addresses observed but absent from the registry
  results.json       the frozen headline numbers and the exact cohort/window

Classification is observational only (Codex plan review, 2026-10-23):
  adsb          at least one point from the aircraft's own ADS-B transmitter
                (readsb sources adsb_icao, adsb_icao_nt, adsr_icao)
  other_only    seen, but only via MLAT / TIS-B / Mode S / other
  not_observed  in the registry snapshot, never seen in the window
Registration status, type, engine and year stay separate attributes; nothing
here claims *why* an aircraft was not observed.

A day counts as flown when any airborne point was received (n_airborne > 0);
airborne hours stay the conservative qualified-interval sum.

Home base (fixed before looking at results): each observed day gets a "night
airport": whichever came last, the airport of the last ground position or the
arrival airport of a later leg that ended low near an airport (receivers often
lose low aircraft on landing). Home is the most frequent night airport,
provided that airport itself has >= 3 days and >= 50% of all night airports.
Otherwise null (abstain).

Usage: 34_fleet_summary.py [--registry 2026-10-23] [--release v0.1-20261023]
"""

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

import os

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("AETHER_DATA", ROOT / "data"))  # tests point this at a fixture tree
HISTORY = DATA / "history"
REGISTRY = DATA / "registry"
RELEASES = DATA / "release"

HOME_MIN_DAYS = 3
HOME_MIN_SHARE = 0.5
ADSB_SOURCES = ("adsb_icao", "adsb_icao_nt", "adsr_icao")


def latest_registry():
    return sorted(p.name for p in REGISTRY.iterdir() if (p / "master.parquet").exists())[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=None)
    ap.add_argument("--release", default=None)
    a = ap.parse_args()
    reg_date = a.registry or latest_registry()
    release = a.release or f"v0.1-{date.today():%Y%m%d}"
    out = RELEASES / release
    out.mkdir(parents=True, exist_ok=True)

    c = duckdb.connect()
    c.execute("SET threads TO 16")
    master = REGISTRY / reg_date / "master.parquet"
    acft = REGISTRY / reg_date / "acftref.parquet"
    days_glob = str(HISTORY / "aircraft_day" / "*" / "*" / "*.parquet")
    adsb_pred = " OR ".join(f"sources LIKE '%\"{s}\"%'" for s in ADSB_SOURCES)

    c.execute(f"""
        CREATE TABLE ad AS
        SELECT day, hex, reg, "type", "desc", pia, ladd, airborne_min, n_airborne, n_legs, n_points,
               last_ground_airport, last_ground_ts, last_ts, ({adsb_pred}) AS has_adsb
        FROM read_parquet('{days_glob}')
        WHERE day <= current_date - 1
    """)
    window = c.execute("SELECT min(day), max(day), count(DISTINCT day) FROM ad").fetchone()
    # The exact days frozen into this release. Later stages filter on this list, not on
    # the bounds, so a day backfilled mid-build cannot slip into the downloads.
    included = [str(r[0]) for r in c.execute("SELECT DISTINCT day FROM ad ORDER BY 1").fetchall()]

    c.execute(f"""
        CREATE TABLE obs AS
        SELECT hex,
               count(*)                                   AS days_observed,
               count(*) FILTER (WHERE n_airborne > 0)     AS days_airborne,
               max(day) FILTER (WHERE n_airborne > 0)     AS last_flew,
               min(day)                                   AS first_seen,
               max(day)                                   AS last_seen,
               sum(airborne_min)                          AS airborne_min_total,
               sum(n_legs)                                AS legs_total,
               bool_or(has_adsb)                          AS any_adsb,
               bool_or(pia)                               AS any_pia,
               bool_or(ladd)                              AS any_ladd,
               max_by(reg, day)                           AS archive_reg,
               max_by(type, day)                          AS archive_type
        FROM ad GROUP BY hex
    """)
    legs_glob = str(HISTORY / "legs" / "*" / "*" / "*.parquet")
    c.execute(f"""
        CREATE TABLE night AS
        WITH last_arr AS (
            SELECT hex, day, max_by(arr, end_ts) AS arr, max(end_ts) AS arr_ts
            FROM read_parquet('{legs_glob}') WHERE arr IS NOT NULL AND end_observed_low
            GROUP BY 1, 2
        )
        -- Whichever evidence came last that day: a ground fix, or a later low arrival.
        SELECT ad.hex, ad.day,
               CASE WHEN la.arr IS NOT NULL AND (ad.last_ground_ts IS NULL OR la.arr_ts > ad.last_ground_ts)
                    THEN la.arr ELSE ad.last_ground_airport END AS apt
        FROM ad LEFT JOIN last_arr la USING (hex, day)
    """)
    c.execute(f"""
        CREATE TABLE home AS
        WITH g AS (
            SELECT hex, apt, count(*) AS n
            FROM night WHERE apt IS NOT NULL GROUP BY 1, 2
        ), t AS (SELECT hex, sum(n) AS total FROM g GROUP BY 1),
        r AS (SELECT g.hex, g.apt, g.n, t.total,
                     row_number() OVER (PARTITION BY g.hex ORDER BY g.n DESC, g.apt) AS rk
              FROM g JOIN t USING (hex))
        SELECT hex,
               CASE WHEN n >= {HOME_MIN_DAYS} AND n::DOUBLE / total >= {HOME_MIN_SHARE}
                    THEN apt END            AS home_airport,
               n                            AS home_days,
               total                        AS ground_days,
               round(n::DOUBLE / total, 3)  AS home_share
        FROM r WHERE rk = 1
    """)
    c.execute(f"""
        CREATE TABLE aircraft AS
        SELECT m.n_number, m.mode_s_code_hex AS hex, m.serial_number,
               a.mfr, a.model, try_cast(a.no_seats AS INT) AS seats, try_cast(a.no_eng AS INT) AS engines,
               try_cast(m.year_mfr AS INT) AS year_mfr,
               m.type_aircraft_label AS aircraft_type, m.type_engine_label AS engine_type,
               m.type_registrant_label AS registrant_type, m.name AS registrant,
               m.city, m.state, m.status_code, m.status_label, m.status_valid,
               m.cert_issue_date, m.expiration_date, m.last_action_date,
               CASE WHEN o.hex IS NULL THEN 'not_observed'
                    WHEN o.any_adsb THEN 'adsb' ELSE 'other_only' END AS observed_class,
               coalesce(o.days_observed, 0) AS days_observed,
               coalesce(o.days_airborne, 0) AS days_airborne,
               o.first_seen, o.last_seen, o.last_flew,
               round(coalesce(o.airborne_min_total, 0) / 60.0, 1) AS airborne_hours,
               coalesce(o.legs_total, 0) AS legs,
               coalesce(o.any_pia, false) AS pia_seen, coalesce(o.any_ladd, false) AS ladd_flag,
               h.home_airport, h.home_days, h.ground_days, h.home_share
        FROM read_parquet('{master}') m
        LEFT JOIN read_parquet('{acft}') a ON a.code = m.mfr_mdl_code
        LEFT JOIN obs o ON o.hex = m.mode_s_code_hex
        LEFT JOIN home h ON h.hex = m.mode_s_code_hex
    """)
    c.execute(f"""
        CREATE TABLE unmatched AS
        SELECT o.* FROM obs o
        WHERE o.hex BETWEEN 'a00001' AND 'afffff'
          AND o.hex NOT IN (SELECT mode_s_code_hex FROM read_parquet('{master}'))
    """)
    c.execute(f"COPY aircraft TO '{out / 'aircraft.parquet'}' (FORMAT parquet, COMPRESSION zstd)")
    c.execute(f"COPY unmatched TO '{out / 'unmatched.parquet'}' (FORMAT parquet, COMPRESSION zstd)")

    def rows(sql):
        cur = c.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    results = {
        "release": release,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cohort": f"N-numbers in the FAA Releasable Aircraft Registry snapshot of {reg_date}",
        "registry_date": reg_date,
        "window": {"first_day": str(window[0]), "last_day": str(window[1]), "days": window[2],
                   "included_days": included},
        "source": "adsb.lol globe_history (ODbL 1.0), prod-0 releases",
        "definitions": {
            "adsb": "observed at least once via the aircraft's own ADS-B (adsb_icao, adsb_icao_nt, adsr_icao)",
            "other_only": "observed only via MLAT, TIS-B, Mode S or other",
            "not_observed": "in the registry snapshot, not observed by adsb.lol in the window",
            "home_airport": f"most frequent end-of-day ground airport if >= {HOME_MIN_DAYS} days and >= {HOME_MIN_SHARE:.0%} share",
        },
        "totals": rows("SELECT observed_class, count(*) n FROM aircraft GROUP BY 1 ORDER BY 1"),
        "by_aircraft_type": rows("""
            SELECT aircraft_type, count(*) registered,
                   count(*) FILTER (WHERE observed_class <> 'not_observed') observed,
                   count(*) FILTER (WHERE days_airborne > 0) flew
            FROM aircraft GROUP BY 1 ORDER BY registered DESC"""),
        "single_piston_by_decade": rows("""
            SELECT CASE WHEN year_mfr IS NULL OR year_mfr < 1900 THEN 'unknown'
                        ELSE (year_mfr // 10 * 10)::VARCHAR END AS decade,
                   count(*) registered,
                   count(*) FILTER (WHERE observed_class <> 'not_observed') observed
            FROM aircraft
            WHERE aircraft_type = 'Fixed wing single engine' AND engine_type IN ('Reciprocating', '4 Cycle', '2 Cycle')
            GROUP BY 1 ORDER BY 1"""),
        "by_status": rows("""
            SELECT status_code, coalesce(status_label, 'undocumented code') AS status_label, count(*) registered,
                   count(*) FILTER (WHERE observed_class <> 'not_observed') observed
            FROM aircraft GROUP BY 1, 2 ORDER BY registered DESC"""),
        "home_base": rows("""
            SELECT count(*) FILTER (WHERE days_airborne > 0) flew,
                   count(*) FILTER (WHERE days_airborne > 0 AND home_airport IS NOT NULL) with_home
            FROM aircraft"""),
        "unmatched_us_addresses": rows("""
            SELECT count(*) addresses, count(*) FILTER (WHERE any_pia) pia_flagged,
                   count(*) FILTER (WHERE days_airborne > 0) flew FROM unmatched"""),
        "flags": rows("""
            SELECT count(*) FILTER (WHERE ladd_flag) ladd_registered_observed,
                   count(*) FILTER (WHERE pia_seen) pia_on_registered_hex FROM aircraft"""),
    }
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str) + "\n")
    print(json.dumps({k: results[k] for k in ("release", "window", "totals", "home_base",
                                               "unmatched_us_addresses")}, default=str))


if __name__ == "__main__":
    main()
