#!/usr/bin/env python3
"""Which aircraft on flight-school lines have an NTSB accident or incident history, and is that more
than for comparable aircraft that are not on a school line? Gated, with a shuffled-serial control.

Inputs
  NTSB     The NTSB aviation accident database as published at https://data.ntsb.gov/avdata
           (the "MDB Download Directory"): avall.zip (2008 to the latest monthly update),
           Pre2008.zip (1982-2007) and PRE1982.zip (1962-1981, a different, flat layout), each one
           Access .mdb, plus the data dictionary PDFs (eadmspub.pdf, eadmspub_legacy.pdf, codman.pdf).
           --download fetches the directory page and those files into --ntsb/raw (an otherwise empty,
           git-ignored directory) and writes --ntsb/source.json with the listing date and size of
           each file, the bytes received and their sha256. --convert unzips each .mdb into its own
           directory and exports the events, aircraft and injury tables (PRE1982: tblFirstHalf and
           its code table) to CSV with mdbtools' mdb-export (--mdbtools DIR, or on PATH). Narratives
           are not exported.
  Registry FAA MASTER and ACFTREF as written by 01_faa_registry.py (data/registry/<snap>/), valid
           registrations only (status_valid).
  Schools  output/flight_line.json: every n_number on a school's fleet list.
  ADS-B    data/release/<release>/aircraft.parquet: aircraft airborne on at least one day of the
           release window are the "active" comparison.

Airframe test (N-numbers are reassigned, so an N-number match alone is not the same aircraft):
  an NTSB aircraft record matches a registered aircraft only if the N-number agrees (upper-cased,
  punctuation removed, leading N dropped) AND
    - serial path: NTSB has a serial and it equals the FAA serial after upper-casing, removing
      everything but letters and digits, and dropping leading zeros; or
    - make/model path: NTSB has no serial (always so before 1982) or the FAA has none, the makes
      agree (first significant word, corporate words dropped, a few successor names folded:
      TEXTRON to CESSNA or BEECH, RAYTHEON/HAWKER to BEECH), the models agree (letters and digits
      only; equal, one a prefix of the other of at least 3 characters, or the same first number of
      at least 2 digits, so C 182 = 182S and T206B = TU206B), and the event year is
      on or after the FAA year of manufacture.
  N-number matches failing the test are counted by reason. Serial near-misses (one edit apart, or
  one the tail of the other) are rejected but counted, since they may be typos for the same airframe.

Base rate: the share of non-school registered aircraft with a matched record, directly
standardized to the school fleet's mix of FAA make/model code x 5-year build bin (each school
aircraft gets the rate of its own stratum). Two comparison pools: all valid registrations, and those
airborne in the ADS-B release window. A crude single-engine-piston comparison is also shown.

Negative control: FAA serial numbers shuffled among valid registrations of the same make/model
code (seeded); aircraft whose serial is unchanged by the shuffle are dropped from the control. The
serial path is re-run; it should match close to nothing. The make/model path does not use the
serial, so the control says nothing about it; the doc reports that path separately.

Era: each matched record is placed before or after the current registration's certificate issue
date (the MASTER CERT ISSUE DATE; the doc shows why that is not reset by renewals), and by decade.
The NTSB record's owner name is also compared with the current registrant's name (internal only).

Gates (exit 1 on failure): each NTSB table loads with at least its expected size; the school cohort
is >= 5,000 registered aircraft; >= 90% of school aircraft fall in a stratum with >= 5 comparison
aircraft; the shuffled control's serial-path match rate is under a twentieth of the real one for
school and comparison aircraft; and the generated doc contains no N-number.

Raw matches (tail, event id) stay in --ntsb (git-ignored); the doc carries aggregates only.

Usage (trogdor, from research/FlightSchoolMapping):
  AETHER_DATA=/mnt/data/home/dew/dev/aether/research/FlightSchoolMapping/data \\
  scripts/.venv/bin/python -I scripts/63_ntsb_history.py --download --convert \\
      --ntsb data/research-2026-11/ntsb --mdbtools ~/.local/mdbtools/bin \\
      --registry 2026-10-23 --release v0.3-20261023 --out ../../docs/hackers-2026-ntsb.md
  scripts/.venv/bin/python -I scripts/63_ntsb_history.py --chart --summary data/research-2026-11/ntsb/summary.json \\
      --fonts FONTDIR --png ../../docs/hackers-2026-charts/backdrops/ntsb.png
AETHER_DATA moves data/ (registry snapshot, release) when running outside the production checkout.
"""

import argparse
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("AETHER_DATA", ROOT / "data"))
BASE = "https://data.ntsb.gov/avdata"
DIR_PAGE = f"{BASE}/FileDirectory"
FILES = ["avall.zip", "Pre2008.zip", "PRE1982.zip", "eadmspub.pdf", "eadmspub_legacy.pdf", "codman.pdf", "MDB_Release_Notes.pdf"]
UA = {"User-Agent": "Mozilla/5.0"}
SEED = 63
MDB = {"avall": "avall.mdb", "Pre2008": "Pre2008.mdb", "PRE1982": "PRE1982.MDB"}
TABLES = {"avall": ["events", "aircraft", "injury"], "Pre2008": ["events", "aircraft", "injury"],
          "PRE1982": ["tblFirstHalf", "ct_Pre1982"]}
MIN_ROWS = {"avall events": 25_000, "Pre2008 events": 50_000, "PRE1982 records": 50_000}

DAMAGE = ["DEST", "SUBS", "MINR", "NONE", "UNK"]
DAMAGE_LABEL = {"DEST": "destroyed", "SUBS": "substantial", "MINR": "minor", "NONE": "none", "UNK": "unknown"}
INJ = ["FATL", "SERS", "MINR", "NONE", "UNK"]
INJ_LABEL = {"FATL": "fatal", "SERS": "serious", "MINR": "minor", "NONE": "none", "UNK": "unknown"}
PRE82_DAMAGE = {"D": "DEST", "S": "SUBS", "M": "MINR", "N": "NONE"}
PRE82_INJ = {"F": "FATL", "C": "SERS", "M": "MINR", "N": "NONE"}
PRE82_ACC = set("ABFG")  # A/B/F/G are accidents, C/D/E/H/I/J incidents (ct_Pre1982, ACC_INC_CLASS)

CORP = set("INC CORP CORPORATION CO COMPANY AIRCRAFT ACFT AIRPLANE AIRPLANES AVIATION MFG MANUFACTURING IND "
           "INDUSTRIES LTD LLC DIV DIVISION THE NEW OF AND ENG ENGINEERING INTL INTERNATIONAL GROUP SA AG GMBH "
           "AIRCRAFTS AERO DESIGN".split())
MAKE_ALIAS = {"TEXTRON": {"CESSNA", "BEECH"}, "RAYTHEON": {"BEECH"}, "HAWKER": {"BEECH"}, "BEECHCRAFT": {"BEECH"},
              "AMERICAN": {"GRUMMAN", "AMERICAN"}, "GULFSTREAM": {"GRUMMAN", "GULFSTREAM"}, "AYRES": {"ROCKWELL", "AYRES"},
              "NORTH": {"ROCKWELL", "NORTH"}, "CUB": {"PIPER", "CUB"}, "SOCATA": {"SOCATA", "AEROSPATIALE"},
              "AEROSPATIALE": {"SOCATA", "AEROSPATIALE", "EUROCOPTER"}, "EUROCOPTER": {"AEROSPATIALE", "EUROCOPTER", "AIRBUS"},
              "AIRBUS": {"AIRBUS", "EUROCOPTER"}, "LIBERTY": {"LIBERTY"}, "TAYLORCRAFT": {"TAYLORCRAFT"}}
BAD_SERIAL = {"", "UNK", "UNKNOWN", "NONE", "NA", "N", "0", "UNKN", "NOTREPORTED"}


# ---------------------------------------------------------------- normalization

def nkey(s):
    """N-number key: upper, letters and digits only, leading N dropped; None if not a US mark."""
    if not s:
        return None
    s = re.sub(r"[^A-Z0-9]", "", str(s).upper())
    if re.fullmatch(r"N[1-9][0-9]{0,4}[A-Z]{0,2}", s):
        return s[1:]
    if re.fullmatch(r"[1-9][0-9]{0,4}[A-Z]{0,2}", s):
        return s
    return None


def skey(s):
    s = re.sub(r"[^A-Z0-9]", "", str(s or "").upper()).lstrip("0")
    return None if s in BAD_SERIAL else s


def make_set(s):
    words = [w for w in re.sub(r"[^A-Z0-9 ]", " ", str(s or "").upper()).split() if w not in CORP]
    if not words:
        return set()
    w = words[0]
    return MAKE_ALIAS.get(w, {w})


def make_agree(a, b):
    sa, sb = make_set(a), make_set(b)
    if not sa or not sb:
        return False
    if sa & sb:
        return True
    return any(x[:5] == y[:5] and len(x) >= 4 and len(y) >= 4 for x in sa for y in sb)


def mkey(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def model_agree(a, b):
    a, b = mkey(a), mkey(b)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    if len(short) >= 3 and long_.startswith(short):
        return True
    da, db = re.search(r"[0-9]+", a), re.search(r"[0-9]+", b)  # same first number: C182 = 182S, T206B = TU206B
    return bool(da and db and len(da.group()) >= 2 and da.group() == db.group())


def near_miss(a, b):
    if not a or not b or a == b:
        return False
    if len(a) >= 4 and len(b) >= 4 and (a.endswith(b) or b.endswith(a)):
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    # one edit (substitution, insertion or deletion)
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    s, l = sorted((a, b), key=len)
    for i in range(len(l)):
        if l[:i] + l[i + 1:] == s:
            return True
    return False


NAME_NORM = re.compile(r"[^A-Z0-9]")


def name_key(s):
    s = str(s or "").upper()
    s = re.sub(r"\b(L L C|LIMITED LIABILITY COMPANY|LTD LIABILITY CO)\b", "LLC", s)
    s = re.sub(r"\b(INCORPORATED|CORPORATION|CORP)\b", "INC", s)
    return NAME_NORM.sub("", s)


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def pct(k, n, d=1):
    return f"{100 * k / n:.{d}f}%" if n else "n/a"


# ---------------------------------------------------------------- download and convert

def download(dest: Path) -> dict:
    raw = dest / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    page = urllib.request.urlopen(urllib.request.Request(DIR_PAGE, headers=UA), timeout=60).read().decode("utf-8", "replace")
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
    listing = {}
    for f in FILES:
        m = re.search(re.escape(f) + r" (\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2} [AP]M) (\d+)", text)
        if not m:
            sys.exit(f"FAIL {f} not listed on {DIR_PAGE}")
        listing[f] = {"listed_date": m.group(1), "listed_bytes": int(m.group(2))}
    for f in FILES:
        url = f"{BASE}/FileDirectory/DownloadFile?fileID={f}"
        p = raw / f
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=1800) as r, p.open("wb") as out:
            shutil.copyfileobj(r, out)
        listing[f].update({"url": url, "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
        if listing[f]["bytes"] != listing[f]["listed_bytes"]:
            sys.exit(f"FAIL {f}: {listing[f]['bytes']} bytes received, {listing[f]['listed_bytes']} listed")
    src = {"page": DIR_PAGE, "files": listing, "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (dest / "source.json").write_text(json.dumps(src, indent=2))
    return src


def convert(dest: Path, mdbtools):
    exe = str(Path(mdbtools).expanduser() / "mdb-export") if mdbtools else shutil.which("mdb-export")
    if not exe or not Path(exe).exists():
        sys.exit("FAIL mdb-export not found (install mdbtools or pass --mdbtools)")
    csvd = dest / "csv"
    csvd.mkdir(exist_ok=True)
    for db, mdb in MDB.items():
        d = dest / f"mdb_{db}"
        d.mkdir(exist_ok=True)
        with zipfile.ZipFile(dest / "raw" / f"{db}.zip") as z:
            names = [n for n in z.namelist() if n.lower() == mdb.lower()]
            if not names:
                sys.exit(f"FAIL {mdb} not in {db}.zip")
            (d / mdb).write_bytes(z.read(names[0]))
        for t in TABLES[db]:
            with (csvd / f"{db}_{t}.csv").open("wb") as out:
                subprocess.run([exe, "-D", "%Y-%m-%d", "-T", "%Y-%m-%d %H:%M:%S", str(d / mdb), t], stdout=out, check=True)


# ---------------------------------------------------------------- load

def load_ntsb(dest: Path):
    import duckdb
    c = duckdb.connect()
    csvd = dest / "csv"
    rd = lambda n: f"read_csv('{csvd / n}', all_varchar=true, header=true, strict_mode=false)"  # noqa: E731
    stats = {}
    recs = []
    seen_ev = set()
    for db in ("avall", "Pre2008"):
        stats[f"{db} events"] = c.sql(f"select count(*) from {rd(db + '_events.csv')}").fetchall()[0][0]
        ev = c.sql(f"select ev_type, count(*) from {rd(db + '_events.csv')} group by 1").fetchall()
        stats[f"{db} event types"] = dict(ev)
        stats[f"{db} aircraft"] = c.sql(f"select count(*) from {rd(db + '_aircraft.csv')}").fetchall()[0][0]
        rng = c.sql(f"select min(ev_date), max(ev_date) from {rd(db + '_events.csv')}").fetchall()[0]
        stats[f"{db} range"] = [str(rng[0])[:10], str(rng[1])[:10]]
        cols = [r[0] for r in c.sql(f"describe select * from {rd(db + '_aircraft.csv')}").fetchall()]
        tf = "a.type_fly" if "type_fly" in cols else "null"
        rows = c.sql(f"""
            with inj as (
              select ev_id, Aircraft_Key,
                max(case injury_level when 'FATL' then 4 when 'SERS' then 3 when 'MINR' then 2 when 'NONE' then 1 end)
                  filter (where injury_level <> 'TOTL' and try_cast(inj_person_count as int) > 0) lvl
              from {rd(db + '_injury.csv')} group by all)
            select e.ev_id, e.ev_type, e.ev_date, e.ev_highest_injury, a.Aircraft_Key, a.regis_no, a.acft_make, a.acft_model,
                   a.acft_serial_no, a.damage, {tf}, a.owner_acft, inj.lvl, a.far_part
            from {rd(db + '_events.csv')} e join {rd(db + '_aircraft.csv')} a using (ev_id)
            left join inj on inj.ev_id = a.ev_id and inj.Aircraft_Key = a.Aircraft_Key""").fetchall()
        dup = 0
        evs = set()
        for (ev_id, ev_type, ev_date, hi, akey, reg, make, model, serial, dmg, tfly, owner, lvl, far) in rows:
            if ev_id in seen_ev:
                dup += 1
                continue
            evs.add(ev_id)
            injury = {4: "FATL", 3: "SERS", 2: "MINR", 1: "NONE"}.get(lvl) or (hi if hi in ("FATL", "SERS", "MINR", "NONE") else "UNK")
            recs.append({"src": db, "ev_id": ev_id, "akey": akey, "kind": "ACC" if ev_type == "ACC" else "INC",
                         "date": str(ev_date)[:10] if ev_date else None, "nkey": nkey(reg), "make": (make or "").strip(),
                         "model": (model or "").strip(), "serial": skey(serial),
                         "damage": dmg if dmg in DAMAGE else "UNK", "injury": injury,
                         "instr": (tfly or "").strip() == "INST", "owner": (owner or "").strip(), "far": (far or "").strip()})
        seen_ev |= evs
        stats[f"{db} duplicate aircraft rows already in a newer file"] = dup
    stats["PRE1982 records"] = c.sql(f"select count(*) from {rd('PRE1982_tblFirstHalf.csv')}").fetchall()[0][0]
    rows = c.sql(f"""select RecNum, REGIST_NO, DATE_OCCURRENCE, ACFT_MAKE, ACFT_MODEL, ACC_INC_CLASS, ACFT_ADAMG, INJURY_INDEX
                     from {rd('PRE1982_tblFirstHalf.csv')}""").fetchall()
    dates = [str(r[2])[:10] for r in rows if r[2]]
    stats["PRE1982 range"] = [min(dates), max(dates)]
    stats["PRE1982 classes"] = dict(Counter(r[5] for r in rows))
    for (rec, reg, d, make, model, cls, dmg, inj) in rows:
        recs.append({"src": "PRE1982", "ev_id": f"P82-{rec}", "akey": "1", "kind": "ACC" if cls in PRE82_ACC else "INC",
                     "date": str(d)[:10] if d else None, "nkey": nkey(reg), "make": (make or "").strip(), "model": (model or "").strip(),
                     "serial": None, "damage": PRE82_DAMAGE.get(dmg, "UNK"), "injury": PRE82_INJ.get(inj, "UNK"),
                     "instr": None, "owner": "", "far": ""})
    stats["records"] = len(recs)
    stats["records with a US N-number"] = sum(1 for r in recs if r["nkey"])
    stats["records with a serial"] = sum(1 for r in recs if r["serial"])
    return recs, stats


def load_registry(snap, release):
    import duckdb
    reg = DATA / "registry" / snap
    rows = duckdb.sql(f"""
        select m.n_number, m.serial_number, m.mfr_mdl_code, m.year_mfr, m.type_aircraft, m.type_engine, m.cert_issue_date,
               m.name, r.mfr, r.model, r.no_eng
        from '{reg / 'master.parquet'}' m left join '{reg / 'acftref.parquet'}' r on r.code = m.mfr_mdl_code
        where m.status_valid""").fetchall()
    acft = {}
    for (n, serial, code, yr, ta, te, cert, name, mfr, model, no_eng) in rows:
        k = nkey(n)
        if not k:
            continue
        y = int(yr) if (yr or "").strip().isdigit() else None
        acft[k] = {"serial": skey(serial), "code": code, "year": y, "ta": ta, "te": te, "cert": (cert or "").strip() or None,
                   "name": name or "", "mfr": (mfr or "").strip(), "model": (model or "").strip(),
                   "no_eng": int(no_eng) if (no_eng or "").strip().isdigit() else None}
    rel = DATA / "release" / release
    active = {nkey(n): (h or 0.0) for n, h in duckdb.sql(
        f"select n_number, airborne_hours from '{rel / 'aircraft.parquet'}' where days_airborne > 0").fetchall()}
    w = json.loads((rel / "results.json").read_text())["window"]
    window = [w["first_day"], w["last_day"]]
    return acft, active, window


def load_schools(path):
    schools = json.loads(Path(path).read_text())
    tails, per_school = set(), []
    for s in schools:
        ks = {nkey(f.get("n_number")) for f in (s.get("fleet") or [])} - {None}
        tails |= ks
        if ks:
            per_school.append(ks)
    return tails, per_school, len(schools)


# ---------------------------------------------------------------- airframe test

def test_pair(a, r, serial_override=None):
    """Return (outcome, path_or_reason) for registered aircraft a and NTSB record r with the same N-number."""
    fs = a["serial"] if serial_override is None else serial_override
    yr = int(r["date"][:4]) if r["date"] else None
    if r["serial"] and fs:
        if r["serial"] == fs:
            return True, "serial"
        return False, "serial near-miss" if near_miss(r["serial"], fs) and make_agree(r["make"], a["mfr"]) else "serial differs"
    if not make_agree(r["make"], a["mfr"]):
        return False, "no serial; make differs"
    if not model_agree(r["model"], a["model"]):
        return False, "no serial; model differs"
    if a["year"] is None or yr is None:
        return False, "no serial; build or event year unknown"
    if yr < a["year"]:
        return False, "no serial; event before the build year"
    return True, "make/model" + (" (no FAA serial)" if r["serial"] and not fs else "")


# ---------------------------------------------------------------- analysis

def analyse(a):
    recs, ntsb = load_ntsb(a.ntsb)
    for k, v in MIN_ROWS.items():
        if ntsb[k] < v:
            sys.exit(f"FAIL {k}: {ntsb[k]:,} < {v:,}")
    acft, active, window = load_registry(a.registry, a.release)
    school_tails, per_school, n_schools = load_schools(a.flight_line)
    src = json.loads((a.ntsb / "source.json").read_text())

    by_n = defaultdict(list)
    for r in recs:
        if r["nkey"]:
            by_n[r["nkey"]].append(r)

    school = {k for k in school_tails if k in acft}
    rng = random.Random(SEED)
    # Negative control: shuffle FAA serials within make/model code across all valid registrations.
    pools = defaultdict(list)
    for k, v in acft.items():
        if v["serial"] and v["code"]:
            pools[v["code"]].append(k)
    shuffled = {}
    for code, ks in pools.items():
        sers = [acft[k]["serial"] for k in ks]
        rng.shuffle(sers)
        for k, s in zip(ks, sers):
            if s != acft[k]["serial"]:
                shuffled[k] = s

    match = {}          # nkey -> list of (record, path)
    reasons = Counter()  # rejected N-number-only pairs, all valid registrations
    reasons_school = Counter()
    nonly_aircraft, nonly_aircraft_school = set(), set()
    near = set()  # aircraft with a rejected serial near-miss and no accepted record
    ctl_serial = set()   # aircraft matched by the serial path under the shuffle
    real_serial_in_ctl = set()
    for k, v in acft.items():
        rs = by_n.get(k)
        if not rs:
            continue
        hits = []
        for r in rs:
            ok, why = test_pair(v, r)
            if ok:
                hits.append((r, why))
            else:
                reasons[why] += 1
                if why == "serial near-miss":
                    near.add(k)
                if k in school:
                    reasons_school[why] += 1
            if k in shuffled and r["serial"]:
                if test_pair(v, r)[1] == "serial":
                    real_serial_in_ctl.add(k)
                ok2, why2 = test_pair(v, r, serial_override=shuffled[k])
                if ok2 and why2 == "serial":
                    ctl_serial.add(k)
        if hits:
            match[k] = hits
        else:
            nonly_aircraft.add(k)
            if k in school:
                nonly_aircraft_school.add(k)

    def has(k, kind=None, path=None):
        hs = match.get(k, [])
        if kind:
            hs = [h for h in hs if h[0]["kind"] == kind]
        if path:
            hs = [h for h in hs if h[1].startswith(path)]
        return bool(hs)

    # strata
    def stratum(v):
        return (v["code"], (v["year"] // 5 * 5) if v["year"] else None)

    def rate_table(pool, flag):
        n, k = Counter(), Counter()
        for x in pool:
            s = stratum(acft[x])
            n[s] += 1
            k[s] += flag(x)
        return n, k

    comp_all = [x for x in acft if x not in school]
    comp_act = [x for x in comp_all if x in active]
    flags = {
        "any": lambda x: has(x),
        "accident": lambda x: has(x, "ACC"),
        "incident only": lambda x: has(x) and not has(x, "ACC"),
        "serial-confirmed": lambda x: has(x, path="serial"),
        "any, counting serial near-misses": lambda x: has(x) or x in near,
    }

    def standardized(pool, flag, cohort):
        n, k = rate_table(pool, flag)
        covered, exp = 0, 0.0
        for x in cohort:
            s = stratum(acft[x])
            if n[s] >= 5:
                covered += 1
                exp += k[s] / n[s]
        return exp / covered if covered else 0.0, covered

    res = {}
    cohorts = {"school": sorted(school), "school, airborne in the window": sorted(x for x in school if x in active),
               "school, not airborne": sorted(x for x in school if x not in active)}
    for cname, coh in cohorts.items():
        row = {"n": len(coh)}
        for fname, f in flags.items():
            kk = sum(f(x) for x in coh)
            row[fname] = {"k": kk, "rate": kk / len(coh), "ci": wilson(kk, len(coh))}
            for pname, pool in (("all registered", comp_all), ("airborne", comp_act)):
                e, cov = standardized(pool, f, coh)
                row[fname][pname] = e
                row[f"coverage {pname}"] = cov
        res[cname] = row

    sep = lambda x: acft[x]["ta"] == "4" and acft[x]["te"] == "1" and acft[x]["no_eng"] == 1  # noqa: E731
    crude = {}
    for cname, coh in (("school single-engine pistons", [x for x in school if sep(x)]),
                       ("non-school single-engine pistons, all registered", [x for x in comp_all if sep(x)]),
                       ("non-school single-engine pistons, airborne", [x for x in comp_act if sep(x)])):
        kk = sum(flags["any"](x) for x in coh)
        ka = sum(flags["accident"](x) for x in coh)
        crude[cname] = {"n": len(coh), "any": kk, "accident": ka}

    # control
    ctl = {}
    for cname, coh in (("school", school), ("all registered, non-school", set(comp_all))):
        elig = [x for x in coh if x in shuffled]
        real = sum(has(x, path="serial") for x in elig)
        fake = sum(x in ctl_serial for x in elig)
        ctl[cname] = {"eligible": len(elig), "real": real, "control": fake}

    # severity, era, purpose: school matched aircraft and their records
    sev_ev = {"damage": Counter(), "injury": Counter()}
    sev_ac = {"damage": Counter(), "injury": Counter()}
    sev_ac_comp = {"damage": Counter(), "injury": Counter()}
    era = Counter()
    decade = Counter()
    owner_same = Counter()
    instr = Counter()
    kinds = Counter()
    paths = Counter()
    events_per = Counter()
    src_count = Counter()
    for x in school:
        hs = match.get(x)
        if not hs:
            continue
        events_per[min(len(hs), 4)] += 1
        sev_ac["damage"][min((DAMAGE.index(h[0]["damage"]) for h in hs))] += 1
        sev_ac["injury"][min((INJ.index(h[0]["injury"]) for h in hs))] += 1
        for r, path in hs:
            sev_ev["damage"][r["damage"]] += 1
            sev_ev["injury"][r["injury"]] += 1
            kinds[r["kind"]] += 1
            paths[path.split(" (")[0]] += 1
            src_count[r["src"]] += 1
            cert = acft[x]["cert"]
            d = (r["date"] or "").replace("-", "")
            if cert and d:
                era["before current registration" if d < cert else "after current registration"] += 1
            else:
                era["unknown"] += 1
            if r["date"]:
                decade[int(r["date"][:4]) // 10 * 10] += 1
            if r["owner"]:
                same = name_key(r["owner"]) == name_key(acft[x]["name"])
                owner_same["same" if same else "different"] += 1
            else:
                owner_same["no NTSB owner"] += 1
            if r["instr"] is not None:
                instr["instructional" if r["instr"] else "other"] += 1
    for x in comp_act:
        hs = match.get(x)
        if hs:
            sev_ac_comp["damage"][min((DAMAGE.index(h[0]["damage"]) for h in hs))] += 1
            sev_ac_comp["injury"][min((INJ.index(h[0]["injury"]) for h in hs))] += 1

    # recent era: records after the current registration, standardized like the headline
    def recent(x):
        cert = acft[x]["cert"]
        return any(cert and h[0]["date"] and h[0]["date"].replace("-", "") >= cert for h in match.get(x, []))
    rec_school = sum(recent(x) for x in school)
    # school tails not airborne in the window: is a recent record the reason?
    cut = f"{int(window[0][:4]) - 1}{window[0][4:]}"
    grounded = {}
    for cname, coh in (("airborne", [x for x in school if x in active]), ("not airborne", [x for x in school if x not in active])):
        grounded[cname] = {"n": len(coh), "any": sum(has(x) for x in coh),
                           "since": sum(any(h[0]["date"] and h[0]["date"] >= cut for h in match.get(x, [])) for x in coh)}
    grounded["since_date"] = cut
    rec_exp_act, _ = standardized(comp_act, recent, sorted(school))

    # schools with at least one matched aircraft
    schools_hit = sum(1 for ks in per_school if any(k in match for k in ks if k in acft))
    schools_reg = sum(1 for ks in per_school if any(k in acft for k in ks))

    cert_year = lambda xs: med(sorted(int(acft[x]["cert"][:4]) for x in xs if acft[x]["cert"]))  # noqa: E731
    cert_old = sum(1 for v in acft.values() if v["cert"] and v["cert"] < "20160101")
    hours_s = sorted(active[x] for x in school if x in active)
    school_codes = {acft[x]["code"] for x in school}
    hours_c = sorted(active[x] for x in comp_act if acft[x]["code"] in school_codes)
    med = lambda v: v[len(v) // 2] if v else 0  # noqa: E731
    cert_med = {"school": cert_year(school), "airborne comparison": cert_year([x for x in comp_act if acft[x]["code"] in school_codes])}

    summary = {
        "ntsb": ntsb, "source": src, "registry": a.registry, "release": a.release, "window": window,
        "schools": n_schools, "schools_with_fleet": len(per_school), "schools_registered": schools_reg, "schools_hit": schools_hit,
        "school_tails": len(school_tails), "school_registered": len(school), "registered": len(acft), "airborne": len(active),
        "results": res, "crude": crude, "control": ctl,
        "reasons": dict(reasons), "reasons_school": dict(reasons_school),
        "nonly_aircraft": len(nonly_aircraft), "nonly_aircraft_school": len(nonly_aircraft_school),
        "matched_aircraft": len(match), "pairs": sum(len(by_n.get(k, [])) for k in acft),
        "sev_ev": {k: dict(v) for k, v in sev_ev.items()}, "sev_ac": {k: {str(i): c for i, c in v.items()} for k, v in sev_ac.items()},
        "sev_ac_comp": {k: {str(i): c for i, c in v.items()} for k, v in sev_ac_comp.items()},
        "era": dict(era), "decade": {str(k): v for k, v in sorted(decade.items())}, "owner_same": dict(owner_same),
        "instr": dict(instr), "kinds": dict(kinds), "paths": dict(paths), "events_per": {str(k): v for k, v in sorted(events_per.items())},
        "src_count": dict(src_count), "recent": {"school": rec_school, "expected_airborne": rec_exp_act}, "grounded": grounded,
        "cert_old": cert_old, "cert_med": cert_med, "hours_school_median": med(hours_s), "hours_comp_median": med(hours_c),
        "shuffled": len(shuffled), "real_serial_in_ctl": len(real_serial_in_ctl),
    }
    (a.ntsb / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    # internal only: matched school aircraft (git-ignored directory)
    with (a.ntsb / "school_matches.tsv").open("w") as f:
        f.write("n_number\tev_id\tsource\tdate\tkind\tdamage\tinjury\tpath\tntsb_make\tntsb_model\n")
        for x in sorted(school):
            for r, p in match.get(x, []):
                f.write(f"N{x}\t{r['ev_id']}\t{r['src']}\t{r['date']}\t{r['kind']}\t{r['damage']}\t{r['injury']}\t{p}\t{r['make']}\t{r['model']}\n")
    with (a.ntsb / "rejected_school.tsv").open("w") as f:
        f.write("n_number\tev_id\tdate\treason\tntsb_make\tntsb_model\tntsb_serial\tfaa_mfr\tfaa_model\tfaa_serial\tfaa_year\n")
        for x in sorted(school):
            v = acft[x]
            for r in by_n.get(x, []):
                ok, why = test_pair(v, r)
                if not ok:
                    f.write(f"N{x}\t{r['ev_id']}\t{r['date']}\t{why}\t{r['make']}\t{r['model']}\t{r['serial']}\t{v['mfr']}\t{v['model']}\t{v['serial']}\t{v['year']}\n")
    return summary


# ---------------------------------------------------------------- doc

def write_doc(s, a):
    R = s["results"]["school"]
    RA = s["results"]["school, airborne in the window"]
    n = R["n"]
    anyk, acc = R["any"], R["accident"]
    nt = s["ntsb"]
    files = s["source"]["files"]
    src_lines = "\n".join(
        f"| `{f}` | {v['listed_date']} | {v['bytes']:,} | `{v['sha256'][:16]}...` |" for f, v in files.items() if f.endswith(".zip"))

    def ratio(obs, exp):
        return f"{obs / exp:.2f}x" if exp else "n/a"

    rows = []
    for fname, label in (("any", "Any NTSB record"), ("accident", "At least one accident"),
                         ("incident only", "Incidents only"), ("serial-confirmed", "Serial-confirmed record"),
                         ("any, counting serial near-misses", "Any record, near-misses counted")):
        for cname, row in (("School fleet", R), ("School fleet, airborne in the window", RA)):
            f = row[fname]
            rows.append(f"| {label} | {cname} | {f['k']:,} of {row['n']:,} | {100 * f['rate']:.1f}% ({100 * f['ci'][0]:.1f}-{100 * f['ci'][1]:.1f}) | "
                        f"{100 * f['all registered']:.1f}% | {100 * f['airborne']:.1f}% | {ratio(f['rate'], f['airborne'])} |")
    table = "\n".join(rows)

    cr = s["crude"]
    crude_rows = "\n".join(f"| {k} | {v['n']:,} | {v['any']:,} ({pct(v['any'], v['n'])}) | {v['accident']:,} ({pct(v['accident'], v['n'])}) |" for k, v in cr.items())

    ctl = s["control"]
    ctl_rows = "\n".join(f"| {k} | {v['eligible']:,} | {v['real']:,} ({pct(v['real'], v['eligible'], 2)}) | {v['control']:,} ({pct(v['control'], v['eligible'], 3)}) |" for k, v in ctl.items())

    rs = s["reasons"]
    rss = s["reasons_school"]
    order = ["serial differs", "serial near-miss", "no serial; make differs", "no serial; model differs",
             "no serial; event before the build year", "no serial; build or event year unknown"]
    rej_rows = "\n".join(f"| {k} | {rss.get(k, 0):,} | {rs.get(k, 0):,} |" for k in order)
    rej_school_total = sum(rss.values())
    rej_total = sum(rs.values())

    ev = s["sev_ev"]
    ac = s["sev_ac"]
    acc_ = s["sev_ac_comp"]
    tot_ev = sum(ev["damage"].values())
    tot_ac = sum(ac["damage"].values())
    tot_acc = sum(acc_["damage"].values())
    dmg_rows = "\n".join(
        f"| {DAMAGE_LABEL[d]} | {ev['damage'].get(d, 0):,} ({pct(ev['damage'].get(d, 0), tot_ev)}) | {ac['damage'].get(str(i), 0):,} ({pct(ac['damage'].get(str(i), 0), tot_ac)}) | {pct(acc_['damage'].get(str(i), 0), tot_acc)} |"
        for i, d in enumerate(DAMAGE))
    inj_rows = "\n".join(
        f"| {INJ_LABEL[d]} | {ev['injury'].get(d, 0):,} ({pct(ev['injury'].get(d, 0), tot_ev)}) | {ac['injury'].get(str(i), 0):,} ({pct(ac['injury'].get(str(i), 0), tot_ac)}) | {pct(acc_['injury'].get(str(i), 0), tot_acc)} |"
        for i, d in enumerate(INJ))
    era = s["era"]
    dec_rows = "\n".join(f"| {k}s | {v:,} |" for k, v in s["decade"].items())
    os_ = s["owner_same"]
    os_n = os_.get("same", 0) + os_.get("different", 0)
    ins = s["instr"]
    ins_n = sum(ins.values())
    kinds = s["kinds"]
    paths = s["paths"]
    ep = s["events_per"]
    rec = s["recent"]
    Rn = R["any, counting serial near-misses"]
    g = s["grounded"]
    RG = s["results"]["school, not airborne"]
    destroyed = ac["damage"].get("0", 0)
    fat = ac["injury"].get("0", 0)

    md = f"""# Hackers 2026: NTSB accident and incident history of flight-school aircraft

Generated by `research/FlightSchoolMapping/scripts/63_ntsb_history.py --registry {s['registry']} --release {s['release']}`. Regenerate rather than edit.

**Headline:** {anyk['k']:,} of the {n:,} registered aircraft on flight-school fleet lists ({100 * anyk['rate']:.1f}%) have an NTSB accident or incident record that is the same airframe, under the N-number they carry today; {acc['k']:,} ({100 * acc['rate']:.1f}%) have at least one accident. For non-school aircraft of the same make/model and build years that flew in the last year, the rate is {100 * anyk['airborne']:.1f}% (any record) and {100 * acc['airborne']:.1f}% (accident): school aircraft carry about {ratio(anyk['rate'], anyk['airborne'])} the history of comparable active aircraft that are not on a school line. Against all registered aircraft of the same mix, flying or not, it is {100 * anyk['all registered']:.1f}%.

**Most of that history comes with the airframe.** Of the {sum(era.values()):,} matched records on school aircraft, {era.get('before current registration', 0):,} ({pct(era.get('before current registration', 0), sum(era.values()))}) are dated before the current owner's registration certificate was issued. Counting only records dated on or after it, {rec['school']:,} school aircraft ({pct(rec['school'], n)}) have one, against an expected {100 * rec['expected_airborne']:.1f}% for active comparable aircraft; the median current registration dates from {s['cert_med']['school']} for school aircraft and {s['cert_med']['airborne comparison']} for the comparison.

**Control:** with FAA serial numbers shuffled among aircraft of the same make/model, the serial test matches {ctl['school']['control']:,} of {ctl['school']['eligible']:,} school aircraft (real: {ctl['school']['real']:,}). The N-number alone would have matched {s['nonly_aircraft_school']:,} more school aircraft that the airframe test rejects.

## Data

NTSB aviation accident database, [MDB Download Directory]({s['source']['page']}) (fetched {s['source']['fetched'][:10]}):

| File | Listed date | Bytes | sha256 |
|---|---|---|---|
{src_lines}

| Database | Events | Aircraft records | Event dates | Accidents / incidents |
|---|---|---|---|---|
| avall | {nt['avall events']:,} | {nt['avall aircraft']:,} | {nt['avall range'][0]} to {nt['avall range'][1]} | {nt['avall event types'].get('ACC', 0):,} / {nt['avall event types'].get('INC', 0):,} |
| Pre2008 | {nt['Pre2008 events']:,} | {nt['Pre2008 aircraft']:,} | {nt['Pre2008 range'][0]} to {nt['Pre2008 range'][1]} | {nt['Pre2008 event types'].get('ACC', 0):,} / {nt['Pre2008 event types'].get('INC', 0):,} |
| PRE1982 | {nt['PRE1982 records']:,} (one row per aircraft) | {nt['PRE1982 records']:,} | {nt['PRE1982 range'][0]} to {nt['PRE1982 range'][1]} | {sum(v for k, v in nt['PRE1982 classes'].items() if k in PRE82_ACC):,} / {sum(v for k, v in nt['PRE1982 classes'].items() if k and k not in PRE82_ACC):,} |

Read with mdbtools 1.0.1 (`mdb-export`); layouts per the NTSB's `eadmspub.pdf` (avall, Pre2008) and `eadmspub_legacy.pdf` and the `ct_Pre1982` code table (PRE1982). {nt['records']:,} aircraft records in all after dropping {nt['Pre2008 duplicate aircraft rows already in a newer file']:,} Pre2008 rows whose event is also in avall; {nt['records with a US N-number']:,} carry a US N-number and {nt['records with a serial']:,} a serial number (PRE1982 has none). The database covers accidents and the incidents the NTSB chose to investigate, not the FAA's own incident files, so incidents are a small, uneven share.

FAA registry snapshot {s['registry']} (MASTER and ACFTREF), {s['registered']:,} valid registrations. School fleets: `flight_line.json`, {s['schools']:,} schools, {s['schools_with_fleet']:,} with a fleet list, {s['school_tails']:,} distinct N-numbers, {s['school_registered']:,} of them validly registered (the cohort). ADS-B release {s['release']}, window {s['window'][0]} to {s['window'][1]}: {s['airborne']:,} registered aircraft airborne on at least one day.

## Results

| Measure | Cohort | Aircraft | Rate (95% CI) | Expected: all registered, same mix | Expected: airborne, same mix | Ratio to airborne |
|---|---|---|---|---|---|---|
{table}

"Expected" is the rate among non-school aircraft, directly standardized to the school cohort's mix of FAA make/model code and 5-year build bin: every school aircraft gets its own stratum's rate. {R['coverage airborne']:,} of {n:,} school aircraft ({pct(R['coverage airborne'], n)}) are in a stratum with at least five airborne comparison aircraft; the rest are left out of the expected rate. {s['schools_hit']:,} of the {s['schools_registered']:,} schools with a registered aircraft on their list have at least one aircraft with a matched record.

Crude comparison, single-engine pistons only, no standardization:

| Group | Aircraft | Any record | Accident |
|---|---|---|---|
{crude_rows}

**School tails that did not fly.** {g['not airborne']['n']:,} school-listed aircraft were not airborne in the ADS-B window; {g['not airborne']['any']:,} of them ({pct(g['not airborne']['any'], g['not airborne']['n'])}) have a matched record, against {pct(g['airborne']['any'], g['airborne']['n'])} of those that flew, and {g['not airborne']['since']:,} ({pct(g['not airborne']['since'], g['not airborne']['n'])}) a record dated {g['since_date']} or later, against {pct(g['airborne']['since'], g['airborne']['n'])}, so recent accidents do not explain the gap. Their older mix explains part of it: standardized to their make/model and build years, all registered non-school aircraft would have {100 * RG['any']['all registered']:.1f}% and airborne ones {100 * RG['any']['airborne']:.1f}%. The rest is not explained here; a website that still lists aircraft the school no longer flies puts them in this group.

**Why school aircraft are higher.** They fly more: the median school aircraft airborne in the window logged {s['hours_school_median']:.0f} hours of ADS-B flight time, against {s['hours_comp_median']:.0f} for airborne non-school aircraft of the same make/model codes. An airframe's chance of ever appearing in the NTSB file grows with the hours it has flown, and the comparison does not control for lifetime hours, which no public file gives. Read the ratio as "school airframes have more history", not "schools are less safe".

## Severity (school aircraft with a matched record)

Damage to the aircraft, per record and per aircraft (an aircraft's worst record); the last column is the per-aircraft share for matched airborne non-school aircraft.

| Damage | Records | Aircraft | Comparison aircraft |
|---|---|---|---|
{dmg_rows}

Highest injury aboard that aircraft (the NTSB injury table for that aircraft; the event's highest injury where the table is empty):

| Injury | Records | Aircraft | Comparison aircraft |
|---|---|---|---|
{inj_rows}

{destroyed:,} school-listed aircraft, validly registered today, have a record that coded the aircraft as destroyed, and {fat:,} a record with a fatality aboard. "Destroyed" is the NTSB's damage category, not a statement that the airframe ceased to exist: these airframes were rebuilt and re-registered under the same N-number and serial, or the record is wrong. The airframe test cannot tell those apart.

Records: {kinds.get('ACC', 0):,} accidents and {kinds.get('INC', 0):,} incidents; by source {', '.join(f'{k} {v:,}' for k, v in s['src_count'].items())}. Matched aircraft by number of records: {', '.join(f"{'4+' if k == '4' else k}: {v:,}" for k, v in ep.items())}. Of {ins_n:,} 1982-onward records with a purpose of flight, {ins.get('instructional', 0):,} ({pct(ins.get('instructional', 0), ins_n)}) were instructional flights.

## Era

| Record date vs the current registration certificate | Records |
|---|---|
| Before | {era.get('before current registration', 0):,} |
| On or after | {era.get('after current registration', 0):,} |
| Unknown (no certificate date or event date) | {era.get('unknown', 0):,} |

| Decade of the record | Records |
|---|---|
{dec_rows}

The certificate issue date is a proxy for when the current owner registered the aircraft. Renewals do not reset it: {s['cert_old']:,} valid registrations carry an issue date before 2016, which they could not under the three-year expiry in force until 2023 if a renewal re-issued the date. A school that bought an aircraft through a holding company, or re-registered after a name change, has a later date than its real start. As a cross-check where the NTSB record names an owner, the owner's name (normalized) equals the current registrant on {os_.get('same', 0):,} of {os_n:,} records ({pct(os_.get('same', 0), os_n)}); PRE1982 and many older records have no owner field.

## The airframe test

A record counts only if the N-number agrees and either the serial numbers agree (letters and digits only, leading zeros dropped) or, where either side lacks a serial, the makes and models agree and the event year is on or after the FAA build year. Matched school records by path: {', '.join(f'{k} {v:,}' for k, v in paths.items())}. The make/model path is the weaker one: it accepts a different airframe of the same model that held the N-number earlier, and the shuffle control does not test it.

{s['pairs']:,} NTSB aircraft records share an N-number with a valid registration today. The test rejected {rej_total:,} of those pairs; {s['nonly_aircraft']:,} registered aircraft ({s['nonly_aircraft_school']:,} on school lists) would have had a record on the N-number alone and have none after the test. Rejected pairs by reason:

| Reason | School aircraft | All registered |
|---|---|---|
{rej_rows}

"Serial near-miss" pairs (one character apart, or one serial the tail of the other, with the makes agreeing) are mostly the same airframe written differently: read by hand, the school near-misses are largely Cessna serials with the model prefix dropped or 172S written as 1725, and Pipers with a stray zero. They are rejected, as the rule says; counting them would move the school rate to {100 * Rn['rate']:.1f}% and the airborne comparison to {100 * Rn['airborne']:.1f}% (results table, last rows). Records filed under an earlier N-number of the same airframe are not searched, which also undercounts.

## Negative control

FAA serial numbers shuffled at random (seed {SEED}) among valid registrations of the same make/model code; {s['shuffled']:,} aircraft received a different serial and are the eligible set. The serial path is then re-run against the same NTSB records.

| Cohort | Eligible aircraft | Real serial-path matches | Shuffled serial-path matches |
|---|---|---|---|
{ctl_rows}

## Caveats

- **Lifetime exposure.** No public file gives an airframe's lifetime hours, so the comparison standardizes for model and age, and for flying at all in the last year, but not for how much each airframe has flown over its life.
- **Lower bounds.** Records under a previous N-number, serial typos and owners' failure to report are all missed.
- **Fleet lists.** A tail on a school's website is a claim that it is on the line; `docs/hackers-2026-scrape-facts.md` measures how well those claims hold. Tails that are not validly registered today are not in the cohort.
- **Privacy.** This doc carries aggregate counts only. The matched school tails (`school_matches.tsv`) and the rejected pairs stay in the git-ignored data directory. NTSB narratives were not exported or read.

## Gates

{{gates}}

## Reproduce (trogdor)

```
cd research/FlightSchoolMapping
AETHER_DATA=/mnt/data/home/dew/dev/aether/research/FlightSchoolMapping/data \\
scripts/.venv/bin/python -I scripts/63_ntsb_history.py --download --convert \\
    --ntsb data/research-2026-11/ntsb --mdbtools ~/.local/mdbtools/bin \\
    --registry {s['registry']} --release {s['release']} --out ../../docs/hackers-2026-ntsb.md
```

mdbtools 1.0.1 was built from its release tarball with `./configure --disable-glib --disable-man`.

The slide (`docs/hackers-2026-charts/backdrops/ntsb.png`: ever vs since the current registration, school vs the airborne comparison) is drawn from `summary.json` on a machine with matplotlib and the deck fonts:

```
scripts/.venv/bin/python -I scripts/63_ntsb_history.py --chart --summary data/research-2026-11/ntsb/summary.json \\
    --fonts FONTDIR --png ../../docs/hackers-2026-charts/backdrops/ntsb.png
```
"""
    gates = []

    def gate(name, ok, detail):
        gates.append((name, ok, detail))

    for k, v in MIN_ROWS.items():
        gate(f"NTSB {k}", nt[k] >= v, f"{nt[k]:,} (>= {v:,})")
    gate("school cohort size", n >= 5000, f"{n:,} registered school aircraft")
    gate("stratum coverage", R["coverage airborne"] >= 0.9 * n, f"{pct(R['coverage airborne'], n)}")
    for k, v in ctl.items():
        gate(f"shuffled control, {k}", v["control"] * 20 < max(v["real"], 1), f"{v['control']:,} vs real {v['real']:,}")
    tails = re.findall(r"\bN[1-9][0-9]{0,4}[A-Z]{0,2}\b", md)
    gate("no N-numbers in the doc", not tails, f"{len(tails)} found")
    md = md.replace("{gates}", "\n".join(f"- {'PASS' if ok else 'FAIL'} {nm}: {d}" for nm, ok, d in gates))
    a.out.write_text(md)
    print(md)
    fails = [nm for nm, ok, _ in gates if not ok]
    if fails:
        sys.exit("FAIL " + "; ".join(fails))
    print("PASS")


# ---------------------------------------------------------------- chart

def chart(a):
    import importlib.util
    spec = importlib.util.spec_from_file_location("bd", Path(__file__).with_name("53_talk_backdrops.py"))
    bd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bd)
    import matplotlib.pyplot as plt
    s = json.loads(Path(a.summary).read_text())
    R = s["results"]["school"]
    bd.setup(Path(a.fonts))
    rec = s["recent"]
    era = s["era"]
    before = era.get("before current registration", 0)
    tot = sum(era.values())
    fig = bd.frame("Flight-school aircraft have a past",
                   "Share with an NTSB accident or incident record on the same airframe (N-number and serial or model)")
    # legend as text, top left, out of the bars' way
    from matplotlib.patches import Rectangle
    fig.add_artist(Rectangle((0.08, 0.752), 0.012, 0.026, color=bd.AMBER, transform=fig.transFigure))
    fig.text(0.10, 0.765, f"on a flight-school fleet list ({R['n']:,} aircraft)", color=bd.INK, fontsize=28, va="center")
    fig.add_artist(Rectangle((0.08, 0.702), 0.012, 0.026, color=bd.BUFF, alpha=0.6, transform=fig.transFigure))
    fig.text(0.10, 0.715, "not a school aircraft: same make, model and build years, flew in the last year",
             color=bd.INK, fontsize=28, va="center")
    ax = fig.add_axes([0.08, 0.20, 0.86, 0.45])
    groups = [(100 * R["any"]["rate"], 100 * R["any"]["airborne"], "Ever, on this airframe"),
              (100 * rec["school"] / R["n"], 100 * rec["expected_airborne"], "Since the current owner registered it")]
    w = 0.36
    for i, (sv, cv, _) in enumerate(groups):
        for x0, v, col, al in ((i - w / 2, sv, bd.AMBER, 1.0), (i + w / 2, cv, bd.BUFF, 0.6)):
            ax.bar(x0, v, w, color=col, alpha=al, zorder=3)
            ax.text(x0, v + 0.35, f"{v:.1f}%", ha="center", va="bottom", fontsize=40, fontfamily="JetBrains Mono", color=bd.INK)
    ax.set_xticks(range(len(groups)), [g[2] for g in groups], fontsize=32, color=bd.INK)
    ax.set_ylim(0, max(max(g[0], g[1]) for g in groups) * 1.22)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.grid(axis="x", visible=False)
    ax.tick_params(axis="x", length=0, pad=14)
    bd.footer(fig, f"{100 * before / tot:.0f}% of school aircraft's records predate the current registration.  "
                   f"NTSB avdata {s['source']['files']['avall.zip']['listed_date'].split()[0]}, FAA registry {s['registry']}, "
                   f"ADS-B {s['window'][0]} to {s['window'][1]}")
    out = Path(a.png)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=100)
    print(f"wrote {out}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ntsb", type=Path, default=ROOT / "data" / "research-2026-11" / "ntsb")
    p.add_argument("--download", action="store_true")
    p.add_argument("--convert", action="store_true")
    p.add_argument("--mdbtools")
    p.add_argument("--registry", default="2026-10-23")
    p.add_argument("--release", default="v0.3-20261023")
    p.add_argument("--flight-line", type=Path, default=ROOT / "output" / "flight_line.json")
    p.add_argument("--out", type=Path, default=ROOT.parent.parent / "docs" / "hackers-2026-ntsb.md")
    p.add_argument("--chart", action="store_true", help="draw the slide PNG from --summary (no data needed)")
    p.add_argument("--summary", type=Path)
    p.add_argument("--fonts")
    p.add_argument("--png")
    a = p.parse_args()
    if a.chart:
        return chart(a)
    a.ntsb.mkdir(parents=True, exist_ok=True)
    if a.download:
        download(a.ntsb)
    if a.convert:
        convert(a.ntsb, a.mdbtools)
    s = analyse(a)
    write_doc(s, a)


if __name__ == "__main__":
    main()
