#!/usr/bin/env python3
"""Write the Hackers 2026 fact sheet from one frozen release.

Every number in docs/hackers-2026-facts.md comes from results.json,
training.json and privacy.json of the named release, with its window and
definitions, so the talk cannot drift from the data. Rerun after each release.

Usage: 43_talk_facts.py --release dev [--out ../../../docs/hackers-2026-facts.md]
"""

import argparse
import json
import statistics
from datetime import date
from pathlib import Path

import duckdb
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parent.parent
RELEASES = ROOT / "data" / "release"
REGISTRY = ROOT / "data" / "registry"
# FAA "Current Equipage Levels", All USA Civil Aircraft ADS-B Equipage Counts, row 1-Oct-26
# (faa.gov/air_traffic/technology/equipadsb/installation/current_equipage_levels, read 2026-10-23).
FAA_EQUIPPED = {"as_of": "2026-10-01", "all": 173_663, "es1090": 136_553, "uat": 35_762, "dual": 1_348}
DEFAULT_OUT = ROOT.parent.parent / "docs" / "hackers-2026-facts.md"


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "n/a"


def n(x):
    return f"{x:,}"


def piston_by_decade_cert(rel, registry_date):
    """Single-engine pistons heard, by decade built and airworthiness class (registry
    `certification`, first character: 1 Standard, 4 Experimental, per ardata.pdf).
    Same single-piston definition as 34's single_piston_by_decade."""
    rows = duckdb.connect().execute(f"""
        SELECT (a.year_mfr // 10 * 10)::VARCHAR AS decade,
               CASE left(m.certification, 1) WHEN '1' THEN 'standard' WHEN '4' THEN 'experimental' ELSE 'other' END AS cls,
               count(*) AS registered, count(*) FILTER (WHERE a.observed_class <> 'not_observed') AS observed
        FROM read_parquet('{rel / "aircraft.parquet"}') a
        JOIN read_parquet('{REGISTRY / registry_date / "master.parquet"}') m USING (n_number)
        WHERE a.aircraft_type = 'Fixed wing single engine' AND a.engine_type IN ('Reciprocating', '4 Cycle', '2 Cycle')
          AND a.year_mfr >= 1940
        GROUP BY 1, 2 ORDER BY 1, 2
    """).fetchall()
    out = {}
    for decade, cls, reg, obs in rows:
        out.setdefault(decade, {})[cls] = {"registered": reg, "observed": obs}
    return out


def quartiles(xs):
    q1, med, q3 = statistics.quantiles(xs, n=4)
    return round(q1), round(med), round(q3)


def rental_utilization(schools, days):
    """Fleet rows with a wet rate from the school's own page and a tail on the registry,
    one row per tail; hours are observed airborne hours annualized from the window."""
    seen, rows = set(), []
    for s in schools:
        for f in s["fleet"]:
            if f.get("wet") and f.get("on_registry") and f["n"] not in seen:
                seen.add(f["n"])
                rows.append(f)
    ann = lambda f: (f.get("hrs") or 0) * 365 / days
    c172 = [f for f in rows if "CESSNA" in (f.get("model") or "").upper() and "172" in (f.get("model") or "")]
    rho, pval = spearmanr([f["wet"] for f in c172], [ann(f) for f in c172])
    return {
        "rated": len(rows), "never": sum(1 for f in rows if not f.get("last_flew")),
        "hours_flew": quartiles([ann(f) for f in rows if f.get("last_flew")]),
        "c172": len(c172), "c172_never": sum(1 for f in c172 if not f.get("last_flew")),
        "c172_wet": quartiles([f["wet"] for f in c172]), "c172_hours": quartiles([ann(f) for f in c172]),
        "c172_rho": rho, "c172_p": pval,
        "c172_billed": quartiles([f["wet"] * ann(f) for f in c172 if f.get("last_flew")]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", required=True)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args()
    rel = RELEASES / a.release
    r = json.loads((rel / "results.json").read_text())
    t = json.loads((rel / "training.json").read_text())
    p = json.loads((rel / "privacy.json").read_text())
    w = r["window"]
    totals = {x["observed_class"]: x["n"] for x in r["totals"]}
    registered = sum(totals.values())
    heard = totals.get("adsb", 0) + totals.get("other_only", 0)
    gyro = next((x for x in r["by_aircraft_type"] if x["aircraft_type"] == "Gyroplane"), None)
    sep = {x["decade"]: x for x in r["single_piston_by_decade"]}
    hb = p["home_base"]
    acc = hb["accuracy_vs_school_listed_airport"]
    wk = p["weekly_pattern"]
    m = t["metrics"]
    ops = t.get("unlisted_operations", [])
    # readsb source types (README-json.md): adsb_icao is the aircraft's own 1090 ADS-B;
    # adsr_icao is an FAA ground-station rebroadcast of a message sent on another link (UAT).
    src = duckdb.connect().execute(f"""
        WITH reg AS (SELECT DISTINCT lower(trim(mode_s_code_hex)) AS hex
                     FROM read_parquet('{REGISTRY / r["registry_date"] / "master.parquet"}')),
        s AS (SELECT hex, bool_or(sources LIKE '%"adsb_icao"%') AS direct,
                     bool_or(sources LIKE '%"adsr_icao"%') AS adsr, bool_or(sources LIKE '%"adsb_icao_nt"%') AS nt
              FROM read_parquet('{rel / "download" / "aircraft_day" / "*.parquet"}') GROUP BY hex)
        SELECT count(*) FILTER (WHERE adsr AND NOT direct AND NOT nt) FROM s JOIN reg USING (hex)
    """).fetchone()[0]
    faa = FAA_EQUIPPED
    cert = piston_by_decade_cert(rel, r["registry_date"])
    util = rental_utilization(json.loads((rel / "schools_full.json").read_text())["schools"], w["days"])

    lines = [
        "# Hackers 2026 fact sheet",
        "",
        f"Generated {date.today()} from release `{a.release}` by `research/FlightSchoolMapping/scripts/43_talk_facts.py`.",
        f"Window {w['first_day']} → {w['last_day']} ({w['days']} days of the adsb.lol archive). "
        f"Registry snapshot {r['registry_date']}. Regenerate rather than edit.",
        "",
        "## 1. The registry vs the sky",
        "",
        f"- The FAA counts {n(faa['all'])} ADS-B-equipped US civil aircraft (as of {faa['as_of']}; {n(faa['es1090'])} on 1090ES, "
        f"{n(faa['uat'])} on UAT, {n(faa['dual'])} dual). adsb.lol's volunteers heard {n(totals.get('adsb', 0))} registered aircraft "
        f"broadcast their own ADS-B in this window: {pct(totals.get('adsb', 0), faa['all'])} of the FAA's count. The FAA's count may "
        "include aircraft no longer on the registry, so treat it as a ceiling, not a matched denominator.",
        f"- {n(src)} of those were heard only as FAA ADS-R rebroadcasts: UAT aircraft the 1090 network hears because the FAA's "
        "ground stations repeat them. A floor on UAT aircraft heard, since direct UAT receivers may report as plain ADS-B.",
        f"- {n(registered)} aircraft on the FAA registry; {n(heard)} ({pct(heard, registered)}) were heard at least once "
        f"in the window. {n(totals.get('not_observed', 0))} were silent.",
        f"- Of those heard, {n(totals.get('adsb', 0))} broadcast their own ADS-B; {n(totals.get('other_only', 0))} were heard "
        "only via MLAT or TIS-B.",
    ]
    if gyro:
        lines.append(f"- Gyroplanes: {n(gyro['registered'])} registered, {n(gyro['observed'])} heard.")
    if "1940" in sep and "2020" in sep:
        lines.append(f"- Single-engine pistons built in the 1940s: {pct(sep['1940']['observed'], sep['1940']['registered'])} heard "
                     f"({n(sep['1940']['registered'])} registered). Built in the 2020s: "
                     f"{pct(sep['2020']['observed'], sep['2020']['registered'])} ({n(sep['2020']['registered'])}).")
    std = " → ".join(pct(v["standard"]["observed"], v["standard"]["registered"]) for v in cert.values() if "standard" in v)
    exp = " → ".join(pct(v["experimental"]["observed"], v["experimental"]["registered"]) for v in cert.values() if "experimental" in v)
    lines += [
        f"- Split by airworthiness class, decade by decade from the {next(iter(cert))}s to the {list(cert)[-1]}s: standard-category "
        f"single-engine pistons {std} heard; experimentals {exp}. The newer the certified plane, the likelier it is heard.",
    ]
    if "1990" in cert:
        d = cert["1990"]
        lines.append(f"- The 1990s dip in the blended chart is the experimentals: {n(d['experimental']['registered'])} of "
                     f"{n(sum(v['registered'] for v in d.values()))} 1990s single pistons are experimental.")
    if "unknown" in sep:
        lines.append(f"- {n(sep['unknown']['registered'])} single-engine pistons have no year of manufacture on file.")
    lines += [
        "- Caveat to say out loud: silent is not the same as not flying. Aircraft without ADS-B Out may fly legally outside",
        "  rule airspace, and coverage is thin in places. Every claim is \"heard / not heard by this network in this window\".",
        "",
        "## 2. Finding flight schools from the sky",
        "",
        f"- Model: gradient-boosted trees on flight behavior plus two priors, 5-fold cross-validated, the label-derived prior "
        f"recomputed inside each fold. Average precision {m['average_precision_vs_website_labels']} against website labels "
        f"(base rate {m['base_rate']}); at the {t['threshold']} threshold, recall {m['recall_on_website_tails']} on known "
        f"school aircraft and precision of at least {m['precision_lower_bound']}.",
        f"- {n(len(ops))} training operations (non-individual registrant, ≥ 3 training-like aircraft, no tail on any known fleet) "
        "missing from our school list.",
        "- Hand check of 20 random operations (2026-10-23, earlier window): 12 schools, 2 clubs, 3 leasebacks onto a school's",
        "  line, 0 non-training, 3 unclear holding LLCs: 85% training-related. Several were schools our crawl missed, so say",
        "  \"missing from our list\", not \"unknown to the web\".",
        "- Most of the lift comes from the priors (which models schools fly, how many active aircraft the registrant holds),",
        "  not from behavior alone (NEGATIVE_RESULTS.md).",
        "",
        "## 3. How trackable you are",
        "",
        f"- Home base inferred for {pct(hb['assigned'], hb['flew'])} of the {n(hb['flew'])} aircraft that flew; "
        f"{pct(acc['correct'], acc['labelled'])} correct against the airport schools list for their own aircraft "
        f"(n = {n(acc['labelled'])}).",
        f"- Weekday routine does not predict flying: {wk['accuracy']:.1%} vs a {wk['baseline_accuracy']:.1%} baseline "
        f"({n(wk['aircraft'])} aircraft). Don't claim \"predictable schedules\".",
        f"- {n(p['ladd']['distinct_ladd_aircraft_shown'])} aircraft on the FAA's LADD opt-out list were visible on adsb.lol in "
        f"the window (~{n(int(p['ladd']['per_day_mean']))} a day). LADD binds FAA-sourced feeds (SWIM subscribers must filter); "
        "open community feeds don't.",
        f"- {n(p['pia']['distinct_pia_addresses_observed'])} PIA (privacy ICAO address) addresses observed. Aggregates only;",
        "  we do not publish PIA-to-tail links. Prior art: Michel & Strohmeier, \"Flying in Private Mode\".",
        "",
        "## 4. What renting costs, and how much the planes fly",
        "",
        f"- {n(util['rated'])} rental aircraft carry a wet rate on their school's own page and a tail on the registry "
        "(rates as published when crawled; the fleet file was built 2026-03 → 2026-08).",
        f"- {n(util['never'])} of them ({pct(util['never'], util['rated'])}) were never airborne in the window: on the rate "
        "sheet, not in the sky.",
        f"- Those that flew: median {n(util['hours_flew'][1])} observed hours a year, annualized "
        f"(quartiles {n(util['hours_flew'][0])}–{n(util['hours_flew'][2])}).",
        f"- Cessna 172s ({n(util['c172'])}): median ${util['c172_wet'][1]}/h wet (quartiles ${util['c172_wet'][0]}–"
        f"${util['c172_wet'][2]}), median {n(util['c172_hours'][1])} hours a year (quartiles {n(util['c172_hours'][0])}–"
        f"{n(util['c172_hours'][2])}); {n(util['c172_never'])} never flew.",
        f"- Price does not track use: Spearman rho {util['c172_rho']:.2f} between a 172's rate and its hours.",
        f"- Rate x observed hours for a 172 that flew: median ${n(util['c172_billed'][1])} a year (quartiles "
        f"${n(util['c172_billed'][0])}–${n(util['c172_billed'][2])}).",
        "- Observed hours are a floor: time flown outside receiver coverage is not counted.",
        "",
        "## Sources to cite on a slide",
        "",
        "- Contains information from adsb.lol, made available under the ODbL. FAA Releasable Aircraft Registry (public domain).",
        "- Any OpenSky-derived count: cite \"The OpenSky Network, https://opensky-network.org\" and send them the talk link.",
        "",
    ]
    out = Path(a.out)
    out.write_text("\n".join(lines))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
