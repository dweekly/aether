#!/usr/bin/env python3
"""Write the Hackers 2026 fact sheet from one frozen release.

Every number in docs/hackers-2026-facts.md comes from results.json,
training.json and privacy.json of the named release, with its window and
definitions, so the talk cannot drift from the data. Rerun after each release.

Usage: 43_talk_facts.py --release dev [--out ../../../docs/hackers-2026-facts.md]
"""

import argparse
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASES = ROOT / "data" / "release"
DEFAULT_OUT = ROOT.parent.parent / "docs" / "hackers-2026-facts.md"


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "n/a"


def n(x):
    return f"{x:,}"


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

    lines = [
        "# Hackers 2026 fact sheet",
        "",
        f"Generated {date.today()} from release `{a.release}` by `research/FlightSchoolMapping/scripts/43_talk_facts.py`.",
        f"Window {w['first_day']} → {w['last_day']} ({w['days']} days of the adsb.lol archive). "
        f"Registry snapshot {r['registry_date']}. Regenerate rather than edit.",
        "",
        "## 1. The registry vs the sky",
        "",
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
