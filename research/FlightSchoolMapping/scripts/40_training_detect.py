#!/usr/bin/env python3
"""Finding 3: find training aircraft from how they fly, then find the training
fleets the web didn't tell us about.

Per aircraft (piston fixed-wing and rotorcraft on the registry, >= MIN_LEGS
airborne legs in the release window), from the legs table:
  same_airport_share   legs that start and end at the same known airport
  short_share          airborne legs under 20 min (touch-and-goes split legs)
  low_share            legs whose max altitude is under 5,000 ft
  legs_per_day         airborne legs per flying day
  days_share           flying days / window days
  airports             distinct airports touched
  weekday_share        share of flying days that are Mon-Fri

Labels: positive = tail listed on a school's own website (schools_full.json,
evidence "website"). Everything else is unlabeled, not negative: it contains
schools we never found. So the reported precision is a lower bound, and the
"discovered" list is exactly the high-scoring unlabeled aircraft.

Model: gradient-boosted trees, 5-fold cross-validated scores (every aircraft is
scored by a model that never saw it). model_school_share is derived from labels,
so it is recomputed inside each fold from that fold's training labels only;
registrant_active_fleet uses no labels. Registrants that already hold any tail
on a known fleet are excluded from "unlisted operations".

Writes data/release/<release>/training.json (metrics, feature weights,
per-airport unlisted training fleets) and training_scores.parquet.

Usage: 40_training_detect.py --release dev
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_window import day_view, included_days  # noqa: E402
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score
from sklearn.base import clone
from sklearn.model_selection import StratifiedKFold

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "data" / "history"
RELEASES = ROOT / "data" / "release"
MIN_LEGS = 5
FEATURES = ["same_airport_share", "short_share", "low_share", "legs_per_day",
            "days_share", "airports", "weekday_share", "lesson_share", "median_leg_min",
            "registrant_active_fleet", "model_school_share"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", required=True)
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--op-threshold", type=float, default=0.7)
    a = ap.parse_args()
    rel = RELEASES / a.release
    results = json.loads((rel / "results.json").read_text())
    win = results["window"]
    c = duckdb.connect()
    c.execute("SET threads TO 16")
    day_view(c, "legs_all", HISTORY / "legs" / "*" / "*" / "*.parquet", included_days(rel))
    # An airborne leg: qualified airborne minutes, or at least one altitude reading.
    c.execute("CREATE VIEW legs AS SELECT * FROM legs_all WHERE airborne_min > 0 OR max_alt_ft IS NOT NULL")
    c.execute(f"""CREATE VIEW ac AS SELECT * FROM read_parquet('{rel / "aircraft.parquet"}')
                  WHERE observed_class <> 'not_observed'
                    AND (aircraft_type = 'Rotorcraft' OR (aircraft_type LIKE 'Fixed wing%'
                         AND engine_type IN ('Reciprocating', '4 Cycle', '2 Cycle')))""")
    rows = c.execute(f"""
        SELECT ac.n_number, ac.hex, ac.home_airport, ac.registrant, ac.mfr, ac.model, ac.aircraft_type, ac.registrant_type,
               count(*)                                                            AS legs,
               avg(coalesce(dep = arr, false)::INT)                           AS same_airport_share,
               avg(coalesce(l.airborne_min < 20, false)::INT)                                     AS short_share,
               avg((coalesce(l.max_alt_ft, 99999) < 5000)::INT)                    AS low_share,
               count(*) / count(DISTINCT l.day)                                    AS legs_per_day,
               count(DISTINCT l.day) / {win["days"]}::DOUBLE                       AS days_share,
               count(DISTINCT coalesce(dep, '?')) + count(DISTINCT coalesce(arr, '?')) AS airports,
               avg(coalesce(dayofweek(l.day) BETWEEN 1 AND 5, false)::INT)                         AS weekday_share,
               avg(coalesce(dep = arr AND l.airborne_min BETWEEN 30 AND 150, false)::INT) AS lesson_share,
               median(l.airborne_min)                                               AS median_leg_min
        FROM legs l JOIN ac ON ac.hex = l.hex
        GROUP BY ALL HAVING count(*) >= {MIN_LEGS}
    """).fetchall()
    cols = ["n", "hex", "home", "registrant", "mfr", "model", "type", "registrant_type", "legs",
            "same_airport_share", "short_share", "low_share", "legs_per_day", "days_share", "airports",
            "weekday_share", "lesson_share", "median_leg_min"]
    data = [dict(zip(cols, r)) for r in rows]

    schools = json.loads((rel / "schools_full.json").read_text())["schools"]
    website_tails = {f["n"] for s in schools for f in s["fleet"] if f["evidence"] == "website"}
    # Registrant fleet size among scored (active) aircraft: schools and leasing LLCs hold many.
    owner_n = Counter(d["registrant"] for d in data)
    for d in data:
        d["registrant_active_fleet"] = owner_n[d["registrant"]]
    any_school_tails = {f["n"] for s in schools for f in s["fleet"]}
    # Registrants that already hold a tail on some known fleet are known operators.
    known_registrants = {d["registrant"] for d in data if d["n"] in any_school_tails and d["registrant"]}
    base = [f for f in FEATURES if f != "model_school_share"]
    Xb = np.nan_to_num(np.array([[d[f] for f in base] for d in data], dtype=float))
    y = np.array([d["n"] in website_tails for d in data], dtype=int)
    mk = [f"{d['mfr']} {d['model']}" for d in data]

    def model_prior(train_idx):
        """How common each make/model is on known school lines, from training labels only."""
        pos = Counter(mk[i] for i in train_idx if y[i])
        seen = Counter(mk[i] for i in train_idx)
        return np.array([pos[m] / seen[m] if seen[m] else 0.0 for m in mk])

    model = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.08, class_weight="balanced", random_state=7)
    cv = StratifiedKFold(5, shuffle=True, random_state=7)
    scores = np.zeros(len(data))
    for tr, te in cv.split(Xb, y):  # the label-derived prior is rebuilt inside every fold
        X = np.column_stack([Xb, model_prior(tr)])
        scores[te] = clone(model).fit(X[tr], y[tr]).predict_proba(X[te])[:, 1]
    X = np.column_stack([Xb, model_prior(np.arange(len(data)))])
    model.fit(X, y)
    imp = permutation_importance(model, X, y, n_repeats=5, random_state=7, scoring="average_precision")
    weights = dict(zip(base + ["model_school_share"], imp.importances_mean.round(4).tolist()))

    flagged = scores >= a.threshold
    tp = int((flagged & (y == 1)).sum())
    recall = tp / max(1, int(y.sum()))
    precision_lb = tp / max(1, int(flagged.sum()))
    # Unlabeled high scorers that are not in any known fleet: the discoveries.
    discovered = [d | {"score": round(float(s), 3)} for d, s, f in zip(data, scores, flagged)
                  if f and d["n"] not in any_school_tails]
    by_airport = defaultdict(list)
    for d in discovered:
        by_airport[d["home"] or "unknown"].append(d)
    clusters = []
    for apt, ds in by_airport.items():
        owners = Counter(d["registrant"] for d in ds)
        clusters.append({"airport": apt, "aircraft": len(ds),
                         "top_registrants": owners.most_common(3),
                         "tails": sorted(d["n"] for d in ds)})
    clusters.sort(key=lambda x: -x["aircraft"])

    # The unit that can honestly be called "a training operation missing from our school list":
    # a non-individual registrant with >= 3 training-like aircraft, none of them in any known
    # fleet. Grouped by registrant alone (a university with two airports is one operation).
    # Hand check of 20 random operations, 2026-10-23: 12 schools, 2 clubs, 3 leasebacks onto
    # school flight lines, 0 non-training, 3 unclear holding LLCs. Several were schools our
    # crawl missed rather than ones absent from the web.
    ops = defaultdict(list)
    for d in discovered:
        if d["score"] >= a.op_threshold and d["registrant_type"] not in (None, "Individual", "Co-Owned") \
                and d["registrant"] not in ("REGISTRATION PENDING", "SALE REPORTED") \
                and d["registrant"] not in known_registrants:
            ops[d["registrant"]].append(d)
    operations = sorted(({"registrant": k, "homes": Counter(x["home"] for x in v if x["home"]).most_common(3),
                          "aircraft": len(v),
                          "mean_score": round(sum(x["score"] for x in v) / len(v), 3),
                          "models": Counter(f"{x['mfr']} {x['model']}" for x in v).most_common(3),
                          "tails": sorted(x["n"] for x in v)}
                         for k, v in ops.items() if len(v) >= 3), key=lambda o: (-o["aircraft"], -o["mean_score"]))

    ap_score = average_precision_score(y, scores)
    out = {
        "release": a.release, "window": win, "threshold": a.threshold,
        "population": {"aircraft_scored": len(data), "website_school_tails_in_population": int(y.sum())},
        "metrics": {"average_precision_lower_bound": round(float(ap_score), 3),
                    "base_rate": round(float(y.mean()), 3),
                    "recall_on_website_tails": round(recall, 3),
                    "precision_lower_bound": round(precision_lb, 3),
                    "flagged": int(flagged.sum()), "flagged_known_school": tp,
                    "flagged_not_in_any_known_fleet": len(discovered)},
        "feature_importance_permutation_ap": weights,
        "unlisted_training_clusters": clusters[:200],
        "unlisted_operations": operations,
        "method": "5-fold cross-validated gradient-boosted trees; unlabeled aircraft treated as negatives, "
                  "so precision is a lower bound (some 'false positives' are schools we never found).",
    }
    (rel / "training.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    c.execute("CREATE TABLE s(n VARCHAR, score DOUBLE)")
    c.executemany("INSERT INTO s VALUES (?, ?)", [(d["n"], float(sc)) for d, sc in zip(data, scores)])
    c.execute(f"COPY s TO '{rel / 'training_scores.parquet'}' (FORMAT parquet)")
    print(json.dumps({k: out[k] for k in ("population", "metrics", "feature_importance_permutation_ap")}, indent=1))
    print(f"unlisted operations (>=3 aircraft, score >= {a.op_threshold}): {len(operations)}")
    for o in operations[:25]:
        print(f"  {o['aircraft']:3d} {(o['homes'][0][0] if o['homes'] else '?'):6s} {o['registrant']}  {o['models'][0][0]}")


if __name__ == "__main__":
    main()
