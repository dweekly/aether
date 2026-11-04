#!/usr/bin/env python3
"""How many LLC- and corporation-held aircraft share an exact street address with a certificated
pilot in the FAA's releasable airmen file? Gated, with a shuffled-address control.

Companion to 52_florida_owners.py, which names the people behind Florida LLCs from Florida's
corporate file; this script works nationally from the FAA's own airmen file and needs no state
data. Promoted from the probe beneficial_airmen_join.py (2026-10-27, CS102026, street 1 + ZIP5,
neighbouring-ZIP control), whose result is in docs/data-merge-research-plan.md.

Inputs
  Airmen   FAA "Releasable Airmen" download, comma-separated (CSxxYYYY.zip from registry.faa.gov,
           linked from faa.gov/licenses_certificates/airmen_certification/releasable_airmen_download).
           --download fetches the page, the current zip and its Last-Modified date into --airmen
           (an otherwise empty directory under the git-ignored data/) and writes source.json there.
           PILOT_BASIC.csv (one row per airman with an active pilot-type certificate, address
           included) and PILOT_CERT.csv (one row per certificate). Airmen who asked the FAA to
           withhold their address are not in the file at all.
  Registry FAA MASTER snapshot as written by 01_faa_registry.py: data/registry/<snap>/master.parquet,
           valid registrations only (status_valid). Registrant type 7 = LLC, 3 = corporation,
           1 = individual (reported as the positive reference).

Address key (stated and measured in the doc): street 1 upper-cased; '.' and apostrophes dropped,
other punctuation to spaces, spaces collapsed; USPS-style abbreviations for common street suffixes,
directionals and unit words; P.O. box spellings unified; plus the first five digits of the ZIP.
Street 2 is ignored (under 4% of LLC/corporation rows and under 1% of airmen use it; units mostly
sit in street 1, so an apartment or suite number written there must agree). US addresses only.

Shared addresses: an address used by more than --max-registrants distinct registrant names in the
whole registry (registered agents, law firms, management companies) or by more than --max-airmen
airmen (airports, bases, dormitories, crew mail drops) is excluded from the join. The doc shows
match counts and surname agreement by sharing level, which is what the cut-offs rest on.

Certificates: "pilot" = any certificate of type P (student through ATP); remote-pilot-only (U)
airmen are not pilots here. "Instructor" = a type F (flight instructor) certificate whose expiry
date is on or after the file's creation date.

Negative control: every airman's house number (the first run of digits in the address key: the
street number, or the box number of a P.O. box) is given to the next airman in a random order
within the same ZIP5, so street names, ZIP codes and the distribution of numbers are kept and
only the pairing of number and street is broken. Airmen whose number does not change (no digits,
alone in their ZIP, or the neighbour had the same number) are dropped from the control rather
than left at their real address, and so is a shuffled address that is another airman's real
address (it carries a real match into the control; the doc measures this). The same join and
exclusions are then run on the shuffled file. The control as first planned (without that second
drop) is still computed and its gate printed, marked as not gating.

Validation: for each matched aircraft, does a matched pilot's surname (suffixes such as JR
removed, 3+ letters) appear in the registrant name or the registry's other-owner names? Control,
as in 52: the same aircraft's pilot surnames checked against another matched aircraft's names.
Individuals are the positive reference. With --florida-cache (52's cordata_officers.parquet), the
Florida-address LLC aircraft are also checked against Florida's named managers and officers.

Secondary (Part 61 instruction with no school behind it): LLC/corporation aircraft at an
instructor's address; their ZIP5 centroids (Census ZCTA gazetteer, --zcta) against airports in
OurAirports (small, medium, large) and the airports where flight_line.json lists a school.

Gates (exit 1 on failure): the airmen file is complete (>= 500k pilot-file airmen, >= 350k with a
P certificate); the LLC+corporation cohort is >= 100k aircraft; the shuffled control matches
under a tenth of the real rate for LLCs and corporations (corrected control); LLC surname agreement >= 5x its control
on >= 1,000 aircraft; individuals' agreement >= 70%; with Florida, officer agreement >= 5x control;
and the generated doc contains no N-number.

Raw joins (tail, airman id) stay in --airmen (git-ignored); the doc carries aggregates only.

Usage (trogdor, from research/FlightSchoolMapping):
  scripts/.venv/bin/python -I scripts/58_airmen_llc_join.py --download \\
      --airmen data/research-2026-11/airmen --zcta data/research-2026-11/zcta \\
      --florida-cache /mnt/data/home/dew/scratch/florida/cordata_officers.parquet \\
      --registry 2026-10-23 --out ../../docs/hackers-2026-airmen-llc.md
AETHER_DATA moves data/ (for the registry snapshot) when running outside the production checkout.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyarrow as pa

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("AETHER_DATA", ROOT / "data"))
PAGE = "https://www.faa.gov/licenses_certificates/airmen_certification/releasable_airmen_download"
UA = {"User-Agent": "Mozilla/5.0"}  # faa.gov answers 403 to descriptive agents
SEED = 58
# FAA U.S. Civil Airmen Statistics 2025, table 1 (read 2026-11-04): pilots total and flight-instructor certificates.
FAA_STATS_URL = "https://www.faa.gov/data_research/aviation_data_statistics/civil_airmen_statistics/2025_Active_Civil_Airmen_Statistics_FINAL.xlsx"
FAA_PILOTS_2025, FAA_CFI_2025 = 887_519, 145_538

# Same entity-name normalization as 52_florida_owners.py (used for distinct-registrant counts and Florida).
# The Florida join is 52's, plus a tie-break on document number: 52 picks among same-named entities
# arbitrarily, so its counts move by a few aircraft from run to run.
NORM = """replace(regexp_replace(regexp_replace(regexp_replace(upper({}), '[^A-Z0-9 ]', '', 'g'),
  '\\b(L L C|LIMITED LIABILITY COMPANY|LTD LIABILITY CO)\\b', 'LLC', 'g'),
  '\\b(INCORPORATED)\\b|\\b(CORPORATION)\\b', 'INC', 'g'), ' ', '')"""
# 52's address key, kept to measure what the standardization adds.
ADDR52 = "trim(regexp_replace(regexp_replace(upper({}), '[^A-Z0-9 ]', '', 'g'), ' +', ' ', 'g'))"

TOKENS = {
    # street suffixes (USPS Publication 28 abbreviations)
    "STREET": "ST", "STR": "ST", "AVENUE": "AVE", "AV": "AVE", "AVN": "AVE", "ROAD": "RD", "DRIVE": "DR",
    "LANE": "LN", "COURT": "CT", "CIRCLE": "CIR", "BOULEVARD": "BLVD", "PLACE": "PL", "TERRACE": "TER",
    "PARKWAY": "PKWY", "HIGHWAY": "HWY", "TRAIL": "TRL", "SQUARE": "SQ", "POINT": "PT", "COVE": "CV",
    "CROSSING": "XING", "EXPRESSWAY": "EXPY", "FREEWAY": "FWY", "TURNPIKE": "TPKE", "CENTER": "CTR",
    "HEIGHTS": "HTS", "RIDGE": "RDG", "HOLLOW": "HOLW", "JUNCTION": "JCT", "PLAZA": "PLZ",
    "CREEK": "CRK", "CANYON": "CYN", "MOUNTAIN": "MTN", "VALLEY": "VLY", "VIEW": "VW", "VILLAGE": "VLG",
    "MEADOWS": "MDWS", "SPRINGS": "SPGS", "STATION": "STA", "ESTATES": "ESTS", "ALLEY": "ALY",
    # directionals
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
    # unit words
    "SUITE": "STE", "APARTMENT": "APT", "BUILDING": "BLDG", "FLOOR": "FL", "ROOM": "RM", "HANGAR": "HNGR",
}
PO_BOX = re.compile(r"^(POST OFFICE BOX|POST OFFICE|P O BOX|P O B|POBOX|POB|PO BX|P O|BOX)\b\s*")
SUFFIX = "'\\s+(JR|SR|II|III|IV|V|2ND|3RD)$'"


def norm_street(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"[.']", "", s.upper())
    s = re.sub(r"[^A-Z0-9 ]", " ", s)  # '#', '-', ',' and '/' separate tokens
    s = re.sub(r"(?<=[0-9])(?=[A-Z])|(?<=[A-Z])(?=[0-9])", " ", s)  # 12A = 12-A = 12 A; APT#5 = APT 5
    words = [TOKENS.get(w, w) for w in s.split()]
    s = " ".join(words)
    s = PO_BOX.sub("PO BOX ", s).strip() if PO_BOX.match(s) else s
    return s


def download(dest: Path) -> dict:
    page = urllib.request.urlopen(urllib.request.Request(PAGE, headers=UA), timeout=60).read().decode("utf-8", "replace")
    m = re.search(r"https://registry\.faa\.gov/database/(CS\d{6})\.zip", page)
    if not m:
        sys.exit(f"FAIL no CSV zip link on {PAGE}")
    url, name = m.group(0), m.group(1)
    created = re.search(r'id="createDate"><p>([^<]+)</p>', page)
    uploaded = re.search(r'id="uploadDate"><p>([^<]+)<', page)
    zpath = dest / f"{name}.zip"
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=600) as r:
        last_mod = r.headers.get("Last-Modified")
        if not zpath.exists():
            zpath.write_bytes(r.read())
    with zipfile.ZipFile(zpath) as z:
        for f in ("PILOT_BASIC.csv", "PILOT_CERT.csv"):
            if f not in z.namelist():
                sys.exit(f"FAIL {f} not in {zpath}")
            (dest / f).write_bytes(z.read(f))
    src = {"page": PAGE, "url": url, "zip": zpath.name, "last_modified": last_mod,
           "page_upload_date": uploaded.group(1).strip() if uploaded else None,
           "page_file_creation_date": created.group(1).strip() if created else None,
           "sha256": hashlib.sha256(zpath.read_bytes()).hexdigest(),
           "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (dest / "source.json").write_text(json.dumps(src, indent=2))
    return src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--airmen", type=Path, required=True, help="directory for the airmen download (git-ignored)")
    ap.add_argument("--download", action="store_true", help="fetch the current file from faa.gov first")
    ap.add_argument("--registry", required=True)
    ap.add_argument("--zcta", type=Path, help="directory with the Census ZCTA gazetteer *_Gaz_zcta_national.txt")
    ap.add_argument("--florida-cache", type=Path, help="52's cordata_officers.parquet, for the Florida comparison")
    ap.add_argument("--max-registrants", type=int, default=5)
    ap.add_argument("--max-airmen", type=int, default=5)
    ap.add_argument("--radius-km", type=float, default=20.0)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    src = download(a.airmen) if a.download else json.loads((a.airmen / "source.json").read_text())
    master = DATA / "registry" / a.registry / "master.parquet"
    basic, certs = a.airmen / "PILOT_BASIC.csv", a.airmen / "PILOT_CERT.csv"
    for p in (master, basic, certs):
        if not p.exists():
            sys.exit(f"FAIL missing {p}")
    file_date = datetime.strptime(src["page_file_creation_date"], "%B %d, %Y").date()

    c = duckdb.connect()
    q = lambda s: c.execute(s).fetchall()  # noqa: E731  (duckdb 2 wants every result consumed before register())
    run = q
    one = lambda s: c.execute(s).fetchall()[0]  # noqa: E731
    run("SET threads=16")
    c.create_function("norm_street", norm_street, ["VARCHAR"], "VARCHAR", null_handling="special")

    # --- airmen -------------------------------------------------------------------------------
    run(f"""CREATE TEMP TABLE ab AS SELECT trim("UNIQUE ID") uid,
        regexp_replace(upper(trim("LAST NAME")), {SUFFIX}, '') last,
        "STREET 1" street, trim(COUNTRY) country, substr(trim("ZIP CODE"), 1, 5) z
        FROM read_csv('{basic}', all_varchar=true, header=true)""")
    run(f"""CREATE TEMP TABLE ac AS SELECT trim("UNIQUE ID") uid,
        bool_or(trim("TYPE") = 'P') has_p,
        bool_or(trim("TYPE") = 'P' AND trim("LEVEL") <> 'S') has_p_ns,
        bool_or(trim("TYPE") = 'F' AND try_strptime(trim("EXPIRE DATE"), '%m%d%Y')::DATE >= DATE '{file_date}') has_cfi
        FROM read_csv('{certs}', all_varchar=true, header=true) GROUP BY 1""")
    n_basic, n_cert_rows = one("SELECT count(*) FROM ab")[0], one(f"SELECT count(*) FROM read_csv('{certs}', all_varchar=true, header=true)")[0]
    run("""CREATE TEMP TABLE air AS SELECT ab.uid, ab.last, norm_street(ab.street) st,
        """ + ADDR52.format("ab.street") + """ st52, ab.z, ac.has_p, ac.has_p_ns, ac.has_cfi
        FROM ab JOIN ac USING (uid) WHERE ab.country = 'USA' AND trim(ab.street) <> '' AND regexp_matches(ab.z, '^[0-9]{5}$')""")
    airmen = dict(zip(["basic", "us_street", "pilots", "pilots_nonstudent", "cfi_current", "cert_rows"], one(f"""
        SELECT {n_basic}, count(*), count(*) FILTER (WHERE has_p), count(*) FILTER (WHERE has_p_ns),
               count(*) FILTER (WHERE has_cfi), {n_cert_rows} FROM air""")))
    airmen["pilots_all"] = one("SELECT count(*) FROM ac WHERE has_p")[0]
    airmen["cfi_any"] = one(f"SELECT count(DISTINCT trim(\"UNIQUE ID\")) FROM read_csv('{certs}', all_varchar=true, header=true) WHERE trim(\"TYPE\")='F'")[0]

    # --- registry ------------------------------------------------------------------------------
    run(f"""CREATE TEMP TABLE reg AS SELECT n_number, type_registrant tr, state, name,
        upper(concat_ws(' ', name, other_names1, other_names2, other_names3, other_names4, other_names5)) names,
        {NORM.format('name')} nk, norm_street(street) st, {ADDR52.format('street')} st52,
        substr(trim(zip_code), 1, 5) z, type_aircraft, type_engine
        FROM read_parquet('{master}') WHERE status_valid AND country = 'US' AND trim(street) <> ''
          AND regexp_matches(substr(trim(zip_code), 1, 5), '^[0-9]{{5}}$')""")
    reg_all = dict(q(f"SELECT type_registrant, count(*) FROM read_parquet('{master}') WHERE status_valid GROUP BY 1"))
    run("CREATE TEMP TABLE regn AS SELECT st, z, count(DISTINCT nk) n_reg FROM reg GROUP BY 1, 2")

    # --- the shuffled control: house numbers rotated within ZIP5 -------------------------------
    run(f"""CREATE TEMP TABLE shuf AS WITH t AS (
          SELECT *, regexp_extract(st, '[0-9]+') num,
                 row_number() OVER (PARTITION BY z ORDER BY hash(uid || '{SEED}')) i,
                 count(*) OVER (PARTITION BY z) n
          FROM air WHERE regexp_matches(st, '[0-9]')),
        r AS (SELECT t.*, u.num newnum FROM t JOIN t u ON u.z = t.z AND u.i = (t.i % t.n) + 1)
        SELECT uid, last, regexp_replace(st, '[0-9]+', newnum) st, z, has_p, has_p_ns, has_cfi
        FROM r WHERE newnum <> num""")
    # A shuffled address that is some other airman's real address carries the real signal back into the
    # control (P.O. boxes at one post office, fly-in communities with many pilots on one street): drop it.
    run("""CREATE TEMP TABLE shuf_leak AS SELECT s.*, EXISTS (SELECT 1 FROM air x WHERE x.st = s.st AND x.z = s.z) real_addr FROM shuf s""")
    run("CREATE TEMP TABLE shuf_clean AS SELECT * EXCLUDE (real_addr) FROM shuf_leak WHERE NOT real_addr")
    shuf_n = one("SELECT count(*) FROM shuf_clean")[0]
    shuf_rotated, shuf_leaked = one("SELECT count(*), count(*) FILTER (WHERE real_addr) FROM shuf_leak")

    def build(tbl, src_tbl, key):
        run(f"""CREATE OR REPLACE TEMP TABLE {tbl}_n AS SELECT {key} st, z, count(*) n_air FROM {src_tbl} GROUP BY 1, 2""")
        run(f"""CREATE OR REPLACE TEMP TABLE {tbl} AS
            SELECT r.n_number, r.tr, x.uid, x.last, x.has_p, x.has_p_ns, x.has_cfi, coalesce(g.n_reg, 1) n_reg, an.n_air,
                   (coalesce(g.n_reg, 1) > {a.max_registrants} OR an.n_air > {a.max_airmen}) shared,
                   r.st LIKE 'PO BOX%' po_box
            FROM reg r JOIN {src_tbl} x ON x.{key} = r.{key} AND x.z = r.z
            JOIN {tbl}_n an ON an.st = r.{key} AND an.z = r.z
            LEFT JOIN regn g ON g.st = r.st AND g.z = r.z""")

    build("jr", "air", "st")
    build("js", "shuf_clean", "st")
    build("jl", "shuf", "st")
    run("""CREATE TEMP TABLE j52 AS SELECT DISTINCT r.n_number FROM reg r JOIN air x ON x.st52 = r.st52 AND x.z = r.z
                 WHERE x.has_p""")

    COH = """CASE WHEN tr = '7' AND state = 'FL' THEN 'LLC, Florida address' WHEN tr = '7' AND state = 'MT' THEN 'LLC, Montana address'
                  WHEN tr = '7' THEN 'LLC, other states' WHEN tr = '3' THEN 'corporation' WHEN tr = '1' THEN 'individual (reference)' END"""

    def hits(tbl, cond):
        return f"(SELECT DISTINCT n_number FROM {tbl} WHERE NOT shared AND {cond})"

    run(f"""CREATE TEMP TABLE coh AS SELECT r.n_number, r.tr, r.state, r.names, r.st, r.z, r.type_aircraft, r.type_engine, {COH} cohort,
          p.n_number IS NOT NULL m_p, ns.n_number IS NOT NULL m_ns, cf.n_number IS NOT NULL m_cfi, s.n_number IS NOT NULL c_p,
          r52.n_number IS NOT NULL m_52, sa.n_number IS NOT NULL m_any, l.n_number IS NOT NULL c_leaky
        FROM reg r
        LEFT JOIN {hits('jr', 'has_p')} p USING (n_number) LEFT JOIN {hits('jr', 'has_p_ns')} ns USING (n_number)
        LEFT JOIN {hits('jr', 'has_cfi')} cf USING (n_number) LEFT JOIN {hits('js', 'has_p')} s USING (n_number)
        LEFT JOIN j52 r52 USING (n_number)
        LEFT JOIN (SELECT DISTINCT n_number FROM jr WHERE has_p) sa USING (n_number)
        LEFT JOIN {hits('jl', 'has_p')} l USING (n_number)
        WHERE r.tr IN ('1', '3', '7')""")
    cols = ["cohort", "aircraft", "pilot", "pilot_nonstudent", "cfi", "control", "raw52", "unfiltered", "control_leaky"]
    sel = """count(*), count(*) FILTER (WHERE m_p), count(*) FILTER (WHERE m_ns), count(*) FILTER (WHERE m_cfi),
             count(*) FILTER (WHERE c_p), count(*) FILTER (WHERE m_52), count(*) FILTER (WHERE m_any), count(*) FILTER (WHERE c_leaky)"""
    order = ["LLC, Florida address", "LLC, Montana address", "LLC, other states", "corporation", "individual (reference)"]
    rows = {r[0]: dict(zip(cols, r)) for r in q(f"SELECT cohort, {sel} FROM coh GROUP BY 1")}
    rows["LLC, all"] = dict(zip(cols, one(f"SELECT 'LLC, all', {sel} FROM coh WHERE tr = '7'")))
    rows["LLC + corporation"] = dict(zip(cols, one(f"SELECT 'LLC + corporation', {sel} FROM coh WHERE tr IN ('3', '7')")))
    llc, corp, ind, both = rows["LLC, all"], rows["corporation"], rows["individual (reference)"], rows["LLC + corporation"]

    # --- what normalization and the exclusions do (LLC + corporation aircraft) ------------------
    addr = dict(zip(["aircraft", "po_box", "no_digit", "reg_shared", "addresses"], one(f"""
        SELECT count(*), count(*) FILTER (WHERE r.st LIKE 'PO BOX%'), count(*) FILTER (WHERE NOT regexp_matches(r.st, '[0-9]')),
               count(*) FILTER (WHERE g.n_reg > {a.max_registrants}), count(DISTINCT (r.st, r.z))
        FROM reg r JOIN regn g USING (st, z) WHERE r.tr IN ('3', '7')""")))
    addr["air_addresses"], addr["air_shared"] = one(f"SELECT count(*), count(*) FILTER (WHERE n_air > {a.max_airmen}) FROM jr_n")
    addr["air_shared_people"] = one(f"SELECT coalesce(sum(n_air), 0) FROM jr_n WHERE n_air > {a.max_airmen}")[0]
    addr["po_box_matched"] = one("""SELECT count(DISTINCT n_number) FROM jr WHERE tr IN ('3', '7') AND has_p AND NOT shared AND po_box""")[0]
    addr["shared_dropped"] = one("""SELECT count(*) FROM coh WHERE tr IN ('3','7') AND m_any AND NOT m_p""")[0]

    # --- validation: surname in registrant / other names ----------------------------------------
    agree = "len(list_filter({l}, lambda s: length(s) > 2 AND position(s IN {n}) > 0)) > 0"
    run("""CREATE TEMP TABLE lastnames AS SELECT n_number, list_distinct(list(last)) lasts, max(n_reg) n_reg, max(n_air) n_air
                 FROM jr WHERE has_p GROUP BY 1""")
    run("""CREATE TEMP TABLE lastnames_s AS SELECT n_number, list_distinct(list(last)) lasts FROM js WHERE has_p AND NOT shared GROUP BY 1""")
    run("""CREATE TEMP TABLE lastnames_l AS SELECT n_number, list_distinct(list(last)) lasts FROM jl WHERE has_p AND NOT shared GROUP BY 1""")

    def validate(where, tbl="coh", flag="m_p", lt="lastnames"):
        run(f"""CREATE OR REPLACE TEMP TABLE v AS SELECT x.n_number, x.names, l.lasts, row_number() OVER (ORDER BY hash(x.n_number)) i
                      FROM {tbl} x JOIN {lt} l USING (n_number) WHERE x.{flag} AND {where}""")
        n = one("SELECT count(*) FROM v")[0]
        real = one(f"SELECT avg(CASE WHEN {agree.format(l='lasts', n='names')} THEN 1.0 ELSE 0 END) FROM v")[0] or 0
        ctrl = one(f"""SELECT avg(CASE WHEN {agree.format(l='x.lasts', n='y.names')} THEN 1.0 ELSE 0 END)
                       FROM v x JOIN v y ON y.i = (x.i % {max(n, 1)}) + 1""")[0] or 0
        return {"n": n, "agree": round(real, 4), "control": round(ctrl, 4)}

    val = {"LLC": validate("tr = '7'"), "corporation": validate("tr = '3'"), "individual": validate("tr = '1'"),
           "LLC, shuffled-address matches": validate("tr = '7'", flag="c_p", lt="lastnames_s"),
           "LLC, first-run control matches": validate("tr = '7'", flag="c_leaky", lt="lastnames_l")}

    # surname agreement by sharing level, before exclusion: what the cut-offs rest on
    run(f"""CREATE TEMP TABLE sh AS SELECT l.n_number, l.lasts, x.names, x.tr,
          CASE WHEN l.n_reg <= 1 THEN '0: 1' WHEN l.n_reg <= {a.max_registrants} THEN '1: 2-{a.max_registrants}'
               WHEN l.n_reg <= 20 THEN '2: {a.max_registrants + 1}-20' ELSE '3: 21+' END reg_b,
          CASE WHEN l.n_air <= 2 THEN '0: 1-2' WHEN l.n_air <= {a.max_airmen} THEN '1: 3-{a.max_airmen}'
               WHEN l.n_air <= 20 THEN '2: {a.max_airmen + 1}-20' ELSE '3: 21+' END air_b
        FROM lastnames l JOIN coh x USING (n_number) WHERE x.tr IN ('3', '7')""")
    share_tbl = {}
    for dim in ("reg_b", "air_b"):
        share_tbl[dim] = [dict(zip(["bucket", "aircraft", "agree"], r)) for r in q(f"""
            SELECT substr({dim}, 4), count(*), avg(CASE WHEN {agree.format(l='lasts', n='names')} THEN 1.0 ELSE 0 END)
            FROM sh GROUP BY {dim} ORDER BY {dim}""")]

    # --- Florida: overlap with 52's corporate-file route and officer-surname validation ---------
    fla = None
    if a.florida_cache and a.florida_cache.exists():
        run(f"CREATE TEMP TABLE fl AS SELECT doc, status, officers, {NORM.format('name')} k FROM read_parquet('{a.florida_cache}')")
        run(f"""CREATE TEMP TABLE m52 AS SELECT n_number, name, {NORM.format('name')} k
            FROM read_parquet('{master}') WHERE status_valid AND type_registrant = '7' AND state = 'FL' AND trim(name) <> ''""")
        run("""CREATE TEMP TABLE jfl AS SELECT * EXCLUDE (rk) FROM (
            SELECT m52.n_number, fl.officers, row_number() OVER (PARTITION BY m52.n_number ORDER BY (fl.k = m52.k) DESC, (fl.status='A') DESC, fl.doc) rk
            FROM m52 JOIN fl ON fl.k = m52.k OR (length(m52.name) >= 49 AND starts_with(fl.k, m52.k))) WHERE rk = 1""")
        run("""CREATE TEMP TABLE jfl2 AS SELECT n_number, officers,
            list_transform(list_filter(officers, lambda o: o.type = 'P'), lambda o: upper(o.name)) people FROM jfl""")
        run("""CREATE TEMP TABLE flx AS SELECT m52.n_number, coalesce(len(j.people) > 0, false) named,
            coalesce(c.m_p, false) m_p, array_to_string(j.people, ' | ') names
            FROM m52 LEFT JOIN jfl2 j USING (n_number) LEFT JOIN coh c USING (n_number)""")
        fla = dict(zip(["aircraft", "named", "pilot", "both", "either"], one("""SELECT count(*), count(*) FILTER (WHERE named),
            count(*) FILTER (WHERE m_p), count(*) FILTER (WHERE named AND m_p), count(*) FILTER (WHERE named OR m_p) FROM flx""")))
        fla["validation"] = validate("named", tbl="flx", flag="m_p")

    # --- secondary: instructor addresses and airports without a listed school -------------------
    cfi = dict(zip(["aircraft", "addresses", "piston_single"], one("""SELECT count(*), count(DISTINCT (st, z)),
        count(*) FILTER (WHERE type_aircraft = '4' AND type_engine = '1')
        FROM coh WHERE tr IN ('3', '7') AND m_cfi""")))
    fl_json = ROOT / "output" / "flight_line.json"
    ops = json.loads(fl_json.read_text())
    fleet = {"N" + f["n_number"].strip().upper().removeprefix("N") for o in ops for f in o.get("fleet", []) if f.get("n_number")}
    school_ap = sorted({l["airport_id"].upper() for o in ops for l in o.get("locations", []) if l.get("airport_id")})
    c.register("fleet_v", pa.table({"n": sorted(fleet)}))
    run("CREATE TEMP TABLE fleet AS SELECT n FROM fleet_v")
    cfi["in_school_fleet"] = one("SELECT count(*) FROM coh JOIN fleet ON fleet.n = coh.n_number WHERE tr IN ('3','7') AND m_cfi")[0]
    geo = None
    zfiles = sorted(a.zcta.glob("*_Gaz_zcta_national.txt")) if a.zcta else []
    if zfiles:
        ap_csv = ROOT / "data" / "ourairports_airports.csv"
        run(f"""CREATE TEMP TABLE zc AS SELECT trim(GEOID) z, CAST(INTPTLAT AS DOUBLE) lat, CAST(trim(INTPTLONG) AS DOUBLE) lon
                      FROM read_csv('{zfiles[-1]}', delim='|', header=true, all_varchar=true)""")
        run(f"""CREATE TEMP TABLE apt AS SELECT ident, gps_code, local_code, icao_code,
                      CAST(latitude_deg AS DOUBLE) lat, CAST(longitude_deg AS DOUBLE) lon
                      FROM read_csv('{ap_csv}', header=true, all_varchar=true) WHERE iso_country = 'US' AND type IN ('small_airport', 'medium_airport', 'large_airport')""")
        c.register("sap_v", pa.table({"id": school_ap}))
        run("CREATE TEMP TABLE sap AS SELECT id FROM sap_v")
        run("""CREATE TEMP TABLE apt2 AS SELECT apt.*, EXISTS (SELECT 1 FROM sap WHERE sap.id IN (apt.ident, apt.gps_code, apt.local_code, apt.icao_code)) school FROM apt""")
        sch_found = one("SELECT count(*) FROM sap WHERE id IN (SELECT ident FROM apt UNION SELECT gps_code FROM apt UNION SELECT local_code FROM apt UNION SELECT icao_code FROM apt)")[0]
        dlat = a.radius_km / 111.0
        km = "6371 * 2 * asin(sqrt(pow(sin(radians(p.lat - zc.lat) / 2), 2) + cos(radians(zc.lat)) * cos(radians(p.lat)) * pow(sin(radians(p.lon - zc.lon) / 2), 2)))"
        # Inner range join, then an equality LEFT JOIN: under duckdb 2.0.0 the LEFT range join itself returned a
        # different set of null-extended ZIPs from run to run (seen 2026-11-04, five runs on identical inputs).
        run(f"""CREATE TEMP TABLE zn AS WITH zr AS (SELECT * FROM zc WHERE z IN (SELECT DISTINCT z FROM reg)),
            pairs AS (SELECT zc.z, count(*) n_ap, count(*) FILTER (WHERE p.school) n_sch
              FROM zr zc JOIN apt2 p ON p.lat BETWEEN zc.lat - {dlat} AND zc.lat + {dlat}
                AND p.lon BETWEEN zc.lon - {dlat} / cos(radians(zc.lat)) AND zc.lon + {dlat} / cos(radians(zc.lat))
                AND {km} <= {a.radius_km}
              GROUP BY 1)
            SELECT zr.z, coalesce(pairs.n_ap, 0) n_ap, coalesce(pairs.n_sch, 0) n_sch FROM zr LEFT JOIN pairs USING (z)""")

        def near(addr_sql):
            return dict(zip(["addresses", "with_centroid", "near_airport", "near_no_school"], one(f"""
                WITH ad AS ({addr_sql})
                SELECT count(*), count(zn.z), count(*) FILTER (WHERE n_ap > 0), count(*) FILTER (WHERE n_ap > 0 AND n_sch = 0)
                FROM ad LEFT JOIN zn USING (z)""")))

        geo = {"radius_km": a.radius_km, "airports": one("SELECT count(*) FROM apt2")[0], "school_airports": one("SELECT count(*) FROM apt2 WHERE school")[0],
               "school_airport_ids": len(school_ap), "school_airport_ids_found": sch_found, "zcta_file": zfiles[-1].name,
               "cfi": near("SELECT DISTINCT st, z FROM coh WHERE tr IN ('3','7') AND m_cfi"),
               "all": near("SELECT DISTINCT st, z FROM coh WHERE tr IN ('3','7')")}

    # --- raw join stays in the ignored airmen dir ----------------------------------------------
    run(f"COPY (SELECT n_number, tr, uid, has_p, has_p_ns, has_cfi, n_reg, n_air, shared, po_box FROM jr) TO '{a.airmen / 'join_raw.parquet'}' (FORMAT parquet)")

    out = {"source": src, "registry": a.registry, "airmen": airmen, "registry_valid_by_type": reg_all, "cohorts": rows,
           "address": addr, "sharing": share_tbl, "validation": val, "florida": fla, "cfi": cfi, "geo": geo,
           "shuffled_airmen": shuf_n, "params": {"max_registrants": a.max_registrants, "max_airmen": a.max_airmen, "seed": SEED}}
    (a.airmen / "airmen_llc.json").write_text(json.dumps(out, indent=2, default=str))

    # --- doc ------------------------------------------------------------------------------------
    pct = lambda x, y: f"{100 * x / y:.1f}%" if y else "n/a"  # noqa: E731
    pct2 = lambda x, y: f"{100 * x / y:.2f}%" if y else "n/a"  # noqa: E731
    N = f"{a.max_registrants}"
    coh_rows = "\n".join(
        f"| {k} | {r['aircraft']:,} | {r['pilot']:,} | {pct(r['pilot'], r['aircraft'])} | {pct(r['pilot_nonstudent'], r['aircraft'])} | "
        f"{r['control']:,} ({pct2(r['control'], r['aircraft'])}) | {pct(r['cfi'], r['aircraft'])} |"
        for k in ["LLC, all", *order[:3], "corporation", "LLC + corporation", "individual (reference)"] for r in [rows[k]])
    vrows = "\n".join(f"| {k} | {v['n']:,} | {100 * v['agree']:.1f}% | {100 * v['control']:.2f}% |" for k, v in val.items())
    shr = lambda dim: "\n".join(f"| {b['bucket']} | {b['aircraft']:,} | {100 * b['agree']:.1f}% |" for b in share_tbl[dim])  # noqa: E731
    fl_md = "Not run: no Florida corporate-file cache given." if not fla else f"""52's Florida cohort (valid LLC registrations with a Florida mailing address, matched to Florida's corporate file the same way 52 does):

| Florida-address LLC aircraft | Count | Share |
|---|---|---|
| All | {fla['aircraft']:,} | |
| A named person in Florida's corporate file (52's route) | {fla['named']:,} | {pct(fla['named'], fla['aircraft'])} |
| A certificated pilot at the exact registry address (this route) | {fla['pilot']:,} | {pct(fla['pilot'], fla['aircraft'])} |
| Both | {fla['both']:,} | {pct(fla['both'], fla['aircraft'])} |
| Either | {fla['either']:,} | {pct(fla['either'], fla['aircraft'])} |

Where both routes apply, a matched pilot's surname is in a named Florida manager's, member's or officer's name for {100 * fla['validation']['agree']:.1f}% of {fla['validation']['n']:,} aircraft, against {100 * fla['validation']['control']:.1f}% for another aircraft's officers. 52's own check of this (its pilot list includes remote-pilot-only airmen, its address key is street 1 + ZIP5 with no standardization or exclusions, and it uses the October airmen file) gave 67.5% against 0.2% on 1,750 aircraft."""
    geo_md = "Not run: no ZCTA gazetteer given." if not geo else f"""Each address is placed at its ZIP code's centroid (Census {geo['zcta_file']}) and compared with {geo['airports']:,} US airports in OurAirports (small, medium and large; no heliports, seaplane bases or closed fields), {geo['school_airports']:,} of which host a school in `flight_line.json` ({geo['school_airport_ids_found']:,} of its {geo['school_airport_ids']:,} school airport codes found in OurAirports).

| Distinct LLC/corporation addresses | With a current instructor | All LLC/corporation addresses (for comparison) |
|---|---|---|
| Addresses | {geo['cfi']['addresses']:,} | {geo['all']['addresses']:,} |
| ZIP centroid known | {geo['cfi']['with_centroid']:,} | {geo['all']['with_centroid']:,} |
| An airport within {geo['radius_km']:.0f} km | {geo['cfi']['near_airport']:,} | {geo['all']['near_airport']:,} |
| ...and no listed school at any airport within {geo['radius_km']:.0f} km | {geo['cfi']['near_no_school']:,} ({pct(geo['cfi']['near_no_school'], geo['cfi']['near_airport'])}) | {geo['all']['near_no_school']:,} ({pct(geo['all']['near_no_school'], geo['all']['near_airport'])}) |"""

    fl_pct = f"{100 * fla['validation']['agree']:.1f}% of the time against {100 * fla['validation']['control']:.1f}% for another LLC's managers" if fla else "(Florida check not run)"
    fl_cmp = (f"Florida's corporate file names a person for {pct(fla['named'], fla['aircraft'])} of Florida-address LLC aircraft (52); the airmen file finds a pilot at the address for "
              f"{pct(fla['pilot'], fla['aircraft'])} of the same aircraft, against {pct(llc['pilot'], llc['aircraft'])} for LLCs nationally. Together they reach {pct(fla['either'], fla['aircraft'])}: "
              f"the airmen route adds {fla['either'] - fla['named']:,} aircraft the corporate file doesn't name. It needs no state data, so it works in the states whose corporate files are not free or do not list managers, "
              f"but in Florida it covers {fla['pilot'] / max(fla['named'], 1):.0%} as many aircraft as the corporate file.") if fla else "Not run."
    md = f"""# Hackers 2026: the pilot at the LLC's address, from the FAA airmen file

Generated by `research/FlightSchoolMapping/scripts/58_airmen_llc_join.py --registry {a.registry}`. Regenerate rather than edit.
FAA releasable airmen file `{src['zip']}` ({src['url']}, file created {src['page_file_creation_date']}, Last-Modified {src['last_modified']}, sha256 `{src['sha256'][:16]}...`): {airmen['basic']:,} airmen in the pilot file, {airmen['us_street']:,} with a US street address, {airmen['pilots']:,} of those holding a pilot certificate (any grade, students included) and {airmen['cfi_current']:,} a current flight-instructor certificate. FAA registry snapshot {a.registry}, valid registrations with a US street address and ZIP.

**Headline:** {llc['pilot']:,} of {llc['aircraft']:,} LLC-registered aircraft ({pct(llc['pilot'], llc['aircraft'])}) have a certificated pilot at exactly the registry's mailing address, as do {corp['pilot']:,} of {corp['aircraft']:,} corporation-registered aircraft ({pct(corp['pilot'], corp['aircraft'])}). With every airman's house number shuffled within the ZIP code, the same join finds {llc['control']:,} LLC aircraft ({pct2(llc['control'], llc['aircraft'])}). Where a pilot is found, the pilot's surname is in the LLC's name {100 * val['LLC']['agree']:.1f}% of the time against {100 * val['LLC']['control']:.1f}% for another LLC's name, and in Florida, where the state's corporate file names the LLC's managers, a matched pilot's surname is among them {fl_pct}.

**Against Florida:** {fl_cmp}

**What a match is, and isn't:** a pilot who gets mail where the LLC does is a probable principal, family member or employee, not proof of ownership or of who flies the aircraft. It is a lower bound: airmen who asked the FAA to withhold their address are absent from the file entirely, and so are owners who are not pilots or whose LLC uses an agent's or office address.

## Results

| Cohort | Aircraft | Pilot at the address | Share | Share, non-student pilots only | Shuffled control | Current instructor at the address |
|---|---|---|---|---|---|---|
{coh_rows}

Florida and Montana rows are split out of the LLCs because 52 covers Florida and Montana is the usual registered-agent state for aircraft LLCs; "LLC, all" is their sum.

## Validation: is the matched pilot plausibly behind the registrant?

For each matched aircraft, does a matched pilot's surname (3+ letters, JR/SR/II/III/IV removed) appear in the registrant name or the registry's other-owner names? Control: the same pilots' surnames checked against another matched aircraft's names, as in 52. All matched aircraft are checked, not a sample. LLC and corporation names usually carry no surname, so their agreement is low in absolute terms; the test is agreement against control. Individuals are the positive reference: their registrant name is a person.

| Matched aircraft | Aircraft | Surname in the registrant's names | Control (another aircraft's names) |
|---|---|---|---|
{vrows}

The last two rows check the matches the shuffled-address controls made, using the shuffled airman's surname: if they agreed as often as real matches, the real agreement would mean nothing. The first-run control's matches are at real pilot addresses but with a different airman from the same ZIP code, which makes them the sharper test.

### Florida, where the two routes overlap

{fl_md}

## Method

- **Address key.** Street 1, upper-cased; periods and apostrophes dropped, other punctuation turned into spaces, letters and digits split apart (12A, 12-A and 12 A agree), spaces collapsed; common street suffixes, directionals and unit words abbreviated the USPS way (STREET to ST, NORTH to N, SUITE to STE); P.O. box spellings unified to PO BOX; plus the 5-digit ZIP. Street 2 is ignored; unit numbers written in street 1 must agree. US addresses only on both sides.
- **What the standardization does.** With 52's key (punctuation dropped, nothing abbreviated) {rows['LLC + corporation']['raw52']:,} LLC/corporation aircraft match a pilot; the standardized key matches {rows['LLC + corporation']['unfiltered']:,} before exclusions ({rows['LLC, all']['unfiltered']:,} LLC aircraft, {pct(rows['LLC, all']['unfiltered'], rows['LLC, all']['aircraft'])}).
- **Shared addresses.** {addr['aircraft']:,} LLC/corporation aircraft sit at {addr['addresses']:,} distinct addresses. {addr['po_box']:,} aircraft ({pct(addr['po_box'], addr['aircraft'])}) use a P.O. box and {addr['no_digit']:,} an address with no number at all. {addr['reg_shared']:,} aircraft ({pct(addr['reg_shared'], addr['aircraft'])}) are at an address used by more than {N} distinct registrant names in the whole registry: registered agents, law firms, management companies. On the airmen side, {addr['air_shared']:,} addresses hold more than {a.max_airmen} airmen ({addr['air_shared_people']:,} airmen in all): airports, bases, schools, crew mail drops. Matches at either kind of address are excluded; that drops {addr['shared_dropped']:,} LLC/corporation aircraft from the matched count. P.O. boxes are kept ({addr['po_box_matched']:,} matched aircraft) because a box number plus ZIP is as specific as a street address.
- **Why those cut-offs.** Both were set at {N} before the run. Surname agreement for matched LLC/corporation aircraft, before exclusion, by how many distinct registrant names share the address and how many airmen do. Agreement holds flat up to five registrant names and collapses past twenty (registered agents and mail-forwarding addresses that some pilots also use as their own); on the airmen side it falls from the first bucket on, so a tighter airmen cut-off would also be defensible:

| Registrant names at the address | Matched aircraft | Surname agreement |
|---|---|---|
{shr('reg_b')}

| Airmen at the address | Matched aircraft | Surname agreement |
|---|---|---|
{shr('air_b')}

- **Who counts.** A pilot is any airman with a type P certificate (student, sport, recreational, private, commercial, ATP); airmen with only a remote-pilot (drone) certificate are not counted. An instructor holds a flight-instructor certificate whose expiry date is on or after the file date.
- **Negative control.** Each airman's house number (the first number in the address key, or the box number of a P.O. box) is given to the next airman in a seeded random order within the same ZIP code, so the street names, ZIP codes and numbers present are unchanged and only which number goes with which street is broken. Airmen whose number would not change are left out rather than kept at their real address, and so is any shuffled address that turns out to be another airman's real address ({shuf_leaked:,} of {shuf_rotated:,}), because a match there is a real pilot's match carried into the control: P.O. boxes at one post office are numbered densely, and fly-in communities put many pilots on one street. {shuf_n:,} of {airmen['us_street']:,} airmen remain. The same join and exclusions are then run.
- **The control as first run.** Keeping those addresses, the shuffled join matched {llc['control_leaky']:,} LLC aircraft and {corp['control_leaky']:,} corporation aircraft ({pct2(llc['control_leaky'], llc['aircraft'])} and {pct2(corp['control_leaky'], corp['aircraft'])}), and its planned gate (control under a tenth of real) failed for corporations at {corp['pilot'] / max(corp['control_leaky'], 1):.1f}x. Dropping the {pct(shuf_leaked, shuf_rotated)} of shuffled addresses that are some other airman's real address removed {pct(llc['control_leaky'] - llc['control'], llc['control_leaky'])} of its LLC hits. Those hits are real pilot addresses that the real join already counts, reached with the wrong pilot: the shuffled pilot's surname is in the LLC's name for {100 * val['LLC, first-run control matches']['agree']:.1f}% of them, against {100 * val['LLC']['agree']:.1f}% for real matches (validation table).
- **What the control does and doesn't show.** It shows that exact matches are not string coincidences between unrelated addresses in the same ZIP code. It is generous to the real join in one way: many shuffled addresses don't exist (a house number past the end of the street), and a non-existent address can't match anything. So the evidence that a matched pilot is tied to the registrant rests on the surname checks above, Florida's officer check most of all, not on the address control alone.
- **Prior probe.** `beneficial_airmen_join.py` (2026-10-27, October file, 52's key, no exclusions, remote-pilot-only airmen counted as pilots, control = the same street in the neighbouring ZIP) found 23.7% of non-Montana LLC aircraft at a pilot's address (`docs/data-merge-research-plan.md` on `hackers-airports`). Here the same cohort is {pct(rows['LLC, other states']['pilot'], rows['LLC, other states']['aircraft'])}; the drop is the exclusions and the narrower definition of a pilot.

## Part 61 instruction with no school behind it (secondary)

{cfi['aircraft']:,} LLC/corporation aircraft share an address with a current flight instructor, at {cfi['addresses']:,} distinct addresses; {cfi['piston_single']:,} of the aircraft are single-engine pistons and {cfi['in_school_fleet']:,} are in a fleet listed in `flight_line.json`. An instructor at the owner's address says instruction could happen in that aircraft, not that it does.

{geo_md}

## Caveats

- **Opt-in bias.** The releasable file holds only airmen who did not ask the FAA to withhold their address. It lists {airmen['pilots_all']:,} pilot-certificate holders and {airmen['cfi_any']:,} flight instructors; the FAA's [2025 airmen statistics]({FAA_STATS_URL}) count {FAA_PILOTS_2025:,} active pilots and {FAA_CFI_2025:,} flight-instructor certificates at the end of 2025, so the file holds roughly {100 * airmen['pilots_all'] / FAA_PILOTS_2025:.0f}% and {100 * airmen['cfi_any'] / FAA_CFI_2025:.0f}% of them. The two counts are ten months apart and the FAA's "active" requires a current medical, so the ratio is only a rough size of the opt-out. Privacy-minded owners, the ones most likely to register through an LLC, are also the most likely to withhold, so the match rate understates who is behind LLCs and is not a random sample of them.
- **Mailing address, not home.** The registry address is where the FAA sends mail. A match at an office (a corporation's headquarters where a company pilot gets mail) is a real link but not an owner. Excluding heavily shared addresses removes most offices, not all.
- **Exact match only.** Different spellings of the same address that the standardization doesn't fix (FIRST vs 1ST, a unit in street 2 on one side) are missed, so this undercounts.
- **One snapshot each.** The airmen file is from {src['page_file_creation_date']}; the registry from {a.registry}. People move.
- **Privacy.** This doc and the committed script carry aggregate counts only. The tail-to-airman join (`join_raw.parquet`) stays in the git-ignored data directory.

## Gates

{{gates}}

## Reproduce (trogdor)

```
cd research/FlightSchoolMapping
scripts/.venv/bin/python -I scripts/58_airmen_llc_join.py --download \\
    --airmen data/research-2026-11/airmen --zcta data/research-2026-11/zcta \\
    --florida-cache /mnt/data/home/dew/scratch/florida/cordata_officers.parquet \\
    --registry {a.registry} --out ../../docs/hackers-2026-airmen-llc.md
```

The ZCTA gazetteer is `https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2026_Gazetteer/2026_Gaz_zcta_national.zip`, unzipped into its own directory. The Florida cache is built by 52.
"""

    gates = []

    def gate(name, ok, detail):
        gates.append((name, ok, detail))

    gate("airmen file complete", airmen["basic"] >= 500_000 and airmen["pilots_all"] >= 350_000,
         f"{airmen['basic']:,} airmen, {airmen['pilots_all']:,} with a pilot certificate")
    gate("cohort size", both["aircraft"] >= 100_000, f"{both['aircraft']:,} LLC/corporation aircraft")
    for k, r in (("LLC", llc), ("corporation", corp)):
        gate(f"shuffled control, {k}", r["control"] * 10 < r["pilot"], f"control {r['control']:,} vs real {r['pilot']:,}")
    v = val["LLC"]
    gate("LLC surname validation", v["n"] >= 1000 and v["agree"] >= 5 * v["control"],
         f"{100 * v['agree']:.1f}% vs {100 * v['control']:.1f}% on {v['n']:,}")
    gate("individual positive reference", val["individual"]["agree"] >= 0.70, f"{100 * val['individual']['agree']:.1f}%")
    if fla:
        fv = fla["validation"]
        gate("Florida officer validation", fv["n"] >= 200 and fv["agree"] >= 5 * fv["control"],
             f"{100 * fv['agree']:.1f}% vs {100 * fv['control']:.1f}% on {fv['n']:,}")
    gate("planned control (not gating, kept for the record): shuffled addresses incl. other airmen's real ones, corporations",
         corp["control_leaky"] * 10 < corp["pilot"], f"control {corp['control_leaky']:,} vs real {corp['pilot']:,}; LLC {llc['control_leaky']:,} vs {llc['pilot']:,}")
    tails = re.findall(r"\bN[1-9][0-9]{0,4}[A-Z]{0,2}\b", md)
    gate("no N-numbers in the doc", not tails, f"{len(tails)} found")
    gtxt = "\n".join(f"- {'PASS' if ok else 'FAIL'} {n}: {d}" for n, ok, d in gates)
    md = md.replace("{gates}", gtxt)
    a.out.write_text(md)
    print(md)
    fails = [n for n, ok, _ in gates if not ok and not n.startswith("planned control")]
    if fails:
        sys.exit("FAIL " + "; ".join(fails))
    print("PASS")


if __name__ == "__main__":
    main()
