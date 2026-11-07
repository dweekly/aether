#!/usr/bin/env python3
"""How many LLC-held aircraft does Florida's free corporate file tie to named people? Gated.

Promoted from probes/beneficial_florida_join.py (2026-10-27) for the Hackers 2026 talk.
Florida's quarterly corporate file (cordata*.txt, fixed width, layout at
dos.sunbiz.org/data-definitions/cor.html) lists up to six officers or managers per entity.
Registrant names in the FAA registry snapshot are matched to Florida entity names after
normalizing (upper case, punctuation dropped, LLC/INC spellings unified, spaces removed); the
registry cuts names at 50 characters, so a registry name of 49+ characters matches on prefix;
active Florida entities are preferred.

Headline cohort: valid registrations, registrant type LLC, mailing address in Florida, where a
Florida name match is close to an identity. Corporations and out-of-state registrants are
reported beside it.

Validation: for Florida-address LLC aircraft that also have a pilot (FAA airmen file) living at
the registry address, how often does that pilot's surname appear in a named officer's name?
Control: the same check against the officers of a different aircraft. If the officers were wrong
people, the two rates would be close. The script exits 1 unless agreement is at least 5x the
control, the control is under 10%, the Florida file holds at least 3M active entities and the
cohort holds at least 5,000 aircraft.

Usage (trogdor): scripts/.venv/bin/python scripts/52_florida_owners.py \\
    --florida /mnt/data/home/dew/scratch/florida --airmen /mnt/data/home/dew/scratch/airmen \\
    --registry 2026-10-23 --cache /mnt/data/home/dew/scratch/florida/cordata_officers.parquet \\
    --out /tmp/florida.md
"""

import argparse
import json
import sys
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
NORM = """replace(regexp_replace(regexp_replace(regexp_replace(upper({}), '[^A-Z0-9 ]', '', 'g'),
  '\\b(L L C|LIMITED LIABILITY COMPANY|LTD LIABILITY CO)\\b', 'LLC', 'g'),
  '\\b(INCORPORATED)\\b|\\b(CORPORATION)\\b', 'INC', 'g'), ' ', '')"""
ADDR = "trim(regexp_replace(regexp_replace(upper({}), '[^A-Z0-9 ]', '', 'g'), ' +', ' ', 'g'))"


def fld(line, start, length):
    return line[start - 1:start - 1 + length].strip()


def build_cache(florida, cache):
    cols = {k: [] for k in ["doc", "name", "status", "officers"]}
    files = sorted(florida.glob("cordata*.txt"))
    if not files:
        sys.exit(f"FAIL no cordata*.txt in {florida}")
    for f in files:
        with open(f, encoding="latin-1") as fh:
            for line in fh:
                if len(line) < 1400:
                    continue
                offs = []
                for i in range(6):
                    b = 669 + 128 * i
                    nm = fld(line, b + 5, 42)
                    if nm:
                        offs.append({"title": fld(line, b, 4), "type": fld(line, b + 4, 1), "name": nm})
                cols["doc"].append(fld(line, 1, 12))
                cols["name"].append(fld(line, 13, 192))
                cols["status"].append(fld(line, 205, 1))
                cols["officers"].append(offs)
    pq.write_table(pa.table(cols), cache, compression="zstd")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--florida", type=Path, required=True)
    ap.add_argument("--airmen", type=Path, required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    master = ROOT / "data" / "registry" / a.registry / "master.parquet"
    pilots = a.airmen / "PILOT_BASIC.csv"
    for p in (master, pilots):
        if not p.exists():
            sys.exit(f"FAIL missing {p}")
    if not a.cache.exists():
        build_cache(a.florida, a.cache)

    c = duckdb.connect()
    c.execute(f"CREATE TEMP TABLE fl AS SELECT doc, status, officers, {NORM.format('name')} k FROM read_parquet('{a.cache}')")
    fl_all, fl_active = c.execute("SELECT count(*), count(*) FILTER (WHERE status='A') FROM fl").fetchone()
    c.execute(f"""CREATE TEMP TABLE m AS SELECT n_number, name, type_registrant, state='FL' fl_addr, {NORM.format('name')} k
        FROM read_parquet('{master}') WHERE status_valid AND type_registrant IN ('3','7') AND trim(name) <> ''""")
    c.execute("""CREATE TEMP TABLE j AS SELECT * EXCLUDE (rk) FROM (
        SELECT m.n_number, m.type_registrant, m.fl_addr, fl.officers,
          row_number() OVER (PARTITION BY m.n_number ORDER BY (fl.k = m.k) DESC, (fl.status='A') DESC) rk
        FROM m JOIN fl ON fl.k = m.k OR (length(m.name) >= 49 AND starts_with(fl.k, m.k))) WHERE rk = 1""")
    c.execute("CREATE TEMP TABLE jp AS SELECT *, len(list_filter(officers, lambda o: o.type = 'P')) n_people FROM j")
    cohorts = [dict(zip(["where", "registrant", "aircraft", "matched", "named_person"], r)) for r in c.execute("""
        SELECT CASE WHEN m.fl_addr THEN 'Florida address' ELSE 'elsewhere (name match only)' END,
               CASE m.type_registrant WHEN '7' THEN 'LLC' ELSE 'corporation' END,
               count(*), count(jp.n_number), count(*) FILTER (WHERE jp.n_people > 0)
        FROM m LEFT JOIN jp USING (n_number) GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()]
    head = next(x for x in cohorts if x["where"] == "Florida address" and x["registrant"] == "LLC")

    c.execute(f"""CREATE TEMP TABLE p AS SELECT upper(trim("LAST NAME")) last, {ADDR.format('"STREET 1"')} st,
        substr(trim("ZIP CODE"),1,5) z FROM read_csv('{pilots}', all_varchar=true, header=true)
        WHERE trim("STREET 1") <> '' AND trim(COUNTRY)='USA'""")
    c.execute(f"""CREATE TEMP TABLE ma AS SELECT n_number, {ADDR.format('street')} st, substr(trim(zip_code),1,5) z
        FROM read_parquet('{master}') WHERE status_valid""")
    c.execute("CREATE TEMP TABLE pl AS SELECT ma.n_number, list_distinct(list(p.last)) lasts FROM ma JOIN p USING (st, z) GROUP BY 1")
    c.execute("""CREATE TEMP TABLE b AS SELECT jp.n_number, jp.officers, pl.lasts, row_number() OVER (ORDER BY hash(jp.n_number)) i
        FROM jp JOIN pl USING (n_number) WHERE jp.fl_addr AND jp.type_registrant='7' AND jp.n_people > 0""")
    agree = """len(list_filter({o}, lambda o: o.type='P' AND len(list_filter({l}, lambda x: length(x) > 2 AND
        position(x IN upper(o.name)) > 0)) > 0)) > 0"""
    n_both = c.execute("SELECT count(*) FROM b").fetchone()[0]
    real = c.execute(f"SELECT avg(CASE WHEN {agree.format(o='officers', l='lasts')} THEN 1.0 ELSE 0 END) FROM b").fetchone()[0] or 0
    ctrl = c.execute(f"""SELECT avg(CASE WHEN {agree.format(o='y.officers', l='x.lasts')} THEN 1.0 ELSE 0 END)
        FROM b x JOIN b y ON y.i = (x.i % {max(n_both, 1)}) + 1""").fetchone()[0] or 0

    out = {"registry": a.registry, "florida_entities": fl_all, "florida_active": fl_active, "cohorts": cohorts,
           "validation": {"aircraft_with_officer_and_pilot": n_both, "agree": round(real, 4), "control": round(ctrl, 4)}}
    pct = lambda x, y: f"{100 * x / y:.1f}%" if y else "n/a"  # noqa: E731
    rows = "\n".join(f"| {x['registrant']}, {x['where']} | {x['aircraft']:,} | {x['matched']:,} | {x['named_person']:,} | "
                     f"{pct(x['named_person'], x['aircraft'])} |" for x in cohorts)
    md = f"""# Hackers 2026: who is behind the LLC, from Florida's corporate file

Generated by `research/FlightSchoolMapping/scripts/52_florida_owners.py --registry {a.registry}`. Regenerate rather than edit.
Florida quarterly corporate file: {fl_all:,} entities, {fl_active:,} active. FAA registry snapshot {a.registry}, valid registrations.

| Cohort | Aircraft | Matched a Florida entity | With at least one named person | Share |
|---|---|---|---|---|
{rows}

**Headline:** {head['named_person']:,} of {head['aircraft']:,} LLC aircraft with a Florida address ({pct(head['named_person'], head['aircraft'])}) resolve to at least one named manager, member or officer.

**Validation:** {n_both:,} of those aircraft also have a pilot living at the registry address. A pilot's surname appears in a named officer's name for {100 * real:.1f}% of them, against {100 * ctrl:.1f}% when checked against another aircraft's officers.

Out-of-state registrants are matched by name only; a same-named Florida entity may be a different company.
"""
    a.out.write_text(md)
    (a.cache.parent / "florida_owners.json").write_text(json.dumps(out, indent=2))
    print(md)
    fails = []
    if fl_active < 3_000_000:
        fails.append(f"only {fl_active:,} active Florida entities")
    if head["aircraft"] < 5000:
        fails.append(f"cohort only {head['aircraft']:,}")
    if ctrl >= 0.10 or real < 5 * ctrl or n_both < 200:
        fails.append(f"validation weak: agree {real:.3f}, control {ctrl:.3f}, n {n_both}")
    if fails:
        sys.exit("FAIL " + "; ".join(fails))
    print("PASS")


if __name__ == "__main__":
    main()
