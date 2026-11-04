#!/usr/bin/env python3
"""What it costs to learn to fly, from the training-price fields extract_v2.py adds.

For the Hackers 2026 talk (docs/hackers-2026-research-plan.md, workstream 2). Reads one
extract_v2 run (results + sidecar index, one pair per pass, as 49_scrape_facts.py takes
them), keeps one record per school domain (the HTTP pass where both passes read a site,
else the browser pass), and reports coverage of each price field, medians nationally and
by census region (by state where a state has enough schools), and the private pilot
package prices.

Hand check: --check N draws N schools at random (seeded) from those with any price and,
for every extracted price, finds where that number appears on the school's cached pages
and prints the text around it, for a person to compare. The script exits 1 if any
extracted price appears nowhere on the pages the model was given (a number the model
computed or invented); whether each number is the right kind of price is the person's
call, recorded in docs/hackers-2026-model-rerun.md.

Usage (on trogdor, from research/FlightSchoolMapping):
  51_learn_to_fly.py --results http=RUN/http/MODEL/results.jsonl --index http=RUN/http/index.json \
      --results browser=RUN/browser/MODEL/results.jsonl --index browser=RUN/browser/index.json \
      --json-out OUT.json [--check 20 --check-out CHECK.md]
"""

import argparse
import json
import random
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

PRICES = ["instructor_rate_hr", "private_pilot_package.price_usd", "club_initiation_fee", "club_dues_usd",
          "simulator_rate_hr", "block_discounts"]
# Plausible ranges, reported (not filtered) so a slide can say how many fell outside.
PLAUSIBLE = {"instructor_rate_hr": (25, 250), "private_pilot_package.price_usd": (3_000, 60_000),
             "club_initiation_fee": (0, 50_000), "club_dues_monthly": (5, 1_000), "simulator_rate_hr": (10, 400),
             "wet_rate_hr": (60, 1_500)}
STATES = {  # name or USPS code -> census region
    **{s: "Northeast" for s in "CT ME MA NH RI VT NJ NY PA".split()},
    **{s: "Midwest" for s in "IL IN MI OH WI IA KS MN MO NE ND SD".split()},
    **{s: "South" for s in "DE DC FL GA MD NC SC VA WV AL KY MS TN AR LA OK TX".split()},
    **{s: "West" for s in "AZ CO ID MT NV NM UT WY AK CA HI OR WA".split()},
}
NAMES = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
         "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
         "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
         "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA",
         "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
         "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
         "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
         "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
         "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
         "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY"}
PER_MONTH = {"month": 1, "quarter": 3, "year": 12}


def state_code(s):
    s = (s or "").strip()
    return NAMES.get(s, s.upper() if s.upper() in STATES else "")


def load(passes, indexes):
    """domain -> record from the preferred pass (http over browser)."""
    out = {}
    for name in sorted(passes, key=lambda k: k != "http"):
        index = {e["custom_id"]: e for e in json.loads(Path(indexes[name]).read_text())}
        for line in open(passes[name]):
            r = json.loads(line)
            if r["result"]["type"] != "succeeded":
                continue
            text = "".join(b.get("text", "") for b in r["result"]["message"]["content"] if b.get("type") == "text")
            try:
                d = json.loads(text)
            except json.JSONDecodeError:
                continue
            e = index[r["custom_id"]]
            if e["domain"] in out:
                continue
            out[e["domain"]] = {"pass": name, "custom_id": r["custom_id"], "index": e, "d": d}
    return out


def values(d):
    ppl = d.get("private_pilot_package") or {}
    dues = d.get("club_dues_usd")
    per = PER_MONTH.get(d.get("club_dues_period") or "")
    return {
        "instructor_rate_hr": d.get("instructor_rate_hr"),
        "private_pilot_package.price_usd": ppl.get("price_usd"),
        "club_initiation_fee": d.get("club_initiation_fee"),
        "club_dues_usd": dues,
        "club_dues_monthly": dues / per if dues is not None and per else None,
        "simulator_rate_hr": d.get("simulator_rate_hr"),
        "block_discounts": len(d.get("block_discounts") or []) or None,
        "wet_rate_hr": [a["wet_rate_hr"] for a in d.get("fleet") or [] if a.get("wet_rate_hr")],
    }


def med(xs):
    xs = [x for x in xs if x is not None]
    return {"n": len(xs), "median": statistics.median(xs) if xs else None,
            "p25": statistics.quantiles(xs, n=4)[0] if len(xs) >= 4 else None,
            "p75": statistics.quantiles(xs, n=4)[2] if len(xs) >= 4 else None}


def number_patterns(x):
    """Ways a price can be written on a page: 195, 195.00, 1,195, 12,500, 12.5k; with a $ first."""
    whole = int(round(x))
    forms = {f"{whole:,}", str(whole)}
    if abs(x - whole) > 1e-9:
        forms |= {f"{x:,.2f}", f"{x:.2f}", f"{x:g}"}
    else:
        forms |= {f"{whole:,}.00", f"{whole}.00"}
        if whole >= 1000 and whole % 100 == 0:
            forms |= {f"{whole / 1000:g}k", f"{whole / 1000:g}K"}
    alts = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
    return [re.compile(r"\$\s?(?:" + alts + r")(?![\d])"),             # "$195", tried first
            re.compile(r"(?<![\d.,])(?:" + alts + r")(?![\d])")]          # bare "195"


def page_texts(e):
    root = Path(e["pages_dir"])
    for p in e["pages"]:
        f = root / p["path"]
        if f.exists():
            yield p["url"], f.read_text(encoding="utf-8", errors="replace")


def find(e, x):
    """Up to two places the number appears, $-prefixed occurrences first."""
    texts = [(url, re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))) for url, text in page_texts(e)]
    hits = []
    for pat in number_patterns(x):
        for url, flat in texts:
            for m in pat.finditer(flat):
                hits.append((url, flat[max(0, m.start() - 110): m.end() + 70]))
                if len(hits) >= 2:
                    return hits
        if hits:
            return hits
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", action="append", required=True)
    ap.add_argument("--index", action="append", required=True)
    ap.add_argument("--json-out", type=Path, required=True)
    ap.add_argument("--check", type=int, default=0)
    ap.add_argument("--check-out", type=Path)
    ap.add_argument("--seed", type=int, default=20261105)
    ap.add_argument("--min-n", type=int, default=10, help="fewest schools for a regional or state median")
    a = ap.parse_args()
    pair = lambda xs: dict(x.split("=", 1) for x in xs)
    schools = load(pair(a.results), pair(a.index))
    rows = {dom: {**values(s["d"]), "state": state_code(s["index"].get("state")), "school_type": s["d"].get("school_type")}
            for dom, s in schools.items()}
    for r in rows.values():
        r["region"] = STATES.get(r["state"], "")

    fields = ["instructor_rate_hr", "private_pilot_package.price_usd", "club_initiation_fee", "club_dues_monthly",
              "simulator_rate_hr"]
    out = {"schools": len(rows), "coverage": {}, "national": {}, "by_region": {}, "by_state": {},
           "outside_plausible": {}, "instructor_included_in_rental": {}, "block_discounts_schools": 0}
    for f in fields + ["block_discounts"]:
        out["coverage"][f] = sum(1 for r in rows.values() if r[f] is not None)
    out["coverage"]["any_training_price"] = sum(1 for r in rows.values() if any(r[f] is not None for f in fields))
    out["coverage"]["any_wet_rate"] = sum(1 for r in rows.values() if r["wet_rate_hr"])
    for f in fields:
        out["national"][f] = med([r[f] for r in rows.values()])
        lo, hi = PLAUSIBLE[f]
        out["outside_plausible"][f] = sorted(x for x in (r[f] for r in rows.values()) if x is not None and not lo <= x <= hi)
        out.setdefault("national_plausible", {})[f] = med([r[f] for r in rows.values() if r[f] is not None and lo <= r[f] <= hi])
        for key, label in (("region", "by_region"), ("state", "by_state")):
            groups = defaultdict(list)
            for r in rows.values():
                if r[key] and r[f] is not None:
                    groups[r[key]].append(r[f])
            out[label][f] = {g: med(xs) for g, xs in sorted(groups.items()) if len(xs) >= a.min_n}
    school_wet = [statistics.median(r["wet_rate_hr"]) for r in rows.values() if r["wet_rate_hr"]]
    out["national"]["wet_rate_hr_school_median"] = med(school_wet)
    inc = defaultdict(int)
    for s in schools.values():
        inc[str(s["d"].get("instructor_included_in_rental"))] += 1
    out["instructor_included_in_rental"] = dict(inc)
    out["block_discounts_schools"] = out["coverage"]["block_discounts"]
    ppl = [(dom, s["d"]["private_pilot_package"]) for dom, s in schools.items()
           if (s["d"].get("private_pilot_package") or {}).get("price_usd") is not None]
    out["ppl_packages"] = {"n": len(ppl), "estimates": sum(1 for _, p in ppl if p.get("is_estimate")),
                           "fixed": sum(1 for _, p in ppl if not p.get("is_estimate")),
                           "fixed_price": med([p["price_usd"] for _, p in ppl if not p.get("is_estimate")]),
                           "estimate_price": med([p["price_usd"] for _, p in ppl if p.get("is_estimate")])}
    lo, hi = PLAUSIBLE["private_pilot_package.price_usd"]
    ok = [(d, p) for d, p in ppl if lo <= p["price_usd"] <= hi]
    out["ppl_packages"]["plausible_fixed_price"] = med([p["price_usd"] for _, p in ok if not p.get("is_estimate")])
    out["ppl_packages"]["plausible_estimate_price"] = med([p["price_usd"] for _, p in ok if p.get("is_estimate")])

    fails = []
    if a.check:
        priced = sorted(d for d, r in rows.items() if any(r[f] is not None for f in fields))
        sample = random.Random(a.seed).sample(priced, min(a.check, len(priced)))
        lines, unfound = ["# Hand check of extracted training prices", "",
                          f"{len(sample)} schools drawn at random (seed {a.seed}) from {len(priced)} with any training "
                          "price. For each number: where it appears on the pages the model was given.", ""], 0
        for dom in sample:
            s = schools[dom]
            d, e = s["d"], s["index"]
            lines.append(f"## {dom} ({s['pass']} {s['custom_id']}; pricing page {d.get('pricing_source_url') or '-'})")
            items = [(k, v) for k, v in (("instructor_rate_hr", d.get("instructor_rate_hr")),
                                         ("private_pilot_package.price_usd", (d.get("private_pilot_package") or {}).get("price_usd")),
                                         ("club_initiation_fee", d.get("club_initiation_fee")),
                                         ("club_dues_usd", d.get("club_dues_usd")),
                                         ("simulator_rate_hr", d.get("simulator_rate_hr"))) if v is not None]
            for i, b in enumerate(d.get("block_discounts") or []):
                items += [(f"block[{i}].{k}", b[k]) for k in ("prepay_usd", "discounted_rate_hr") if b.get(k) is not None]
            lines.append(f"- instructor included in rental: {d.get('instructor_included_in_rental')}; "
                         f"PPL package: {json.dumps(d.get('private_pilot_package'))}; dues period: {d.get('club_dues_period')!r}")
            for i, b in enumerate(d.get("block_discounts") or []):
                lines.append(f"- block[{i}]: {b.get('description')!r}")
            for k, v in items:
                hits = find(e, v)
                if not hits:
                    unfound += 1
                lines.append(f"- **{k} = {v:g}**: " + ("NOT ON ANY PAGE" if not hits else
                                                         " / ".join(f"`{h[1]}` ({h[0]})" for h in hits)))
            lines.append("")
        if unfound:
            fails.append(f"{unfound} extracted prices appear on no cached page")
        out["check"] = {"seed": a.seed, "schools": sample, "unfound": unfound}
        if a.check_out:
            a.check_out.write_text("\n".join(lines) + "\n")
    a.json_out.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: out[k] for k in ("schools", "coverage", "ppl_packages", "instructor_included_in_rental")}, indent=1))
    for f in fields + ["wet_rate_hr_school_median"]:
        print(f, out["national"][f], "plausible:", out.get("national_plausible", {}).get(f))
    if fails:
        sys.exit("FAIL " + "; ".join(fails))
    print("PASS")


if __name__ == "__main__":
    main()
