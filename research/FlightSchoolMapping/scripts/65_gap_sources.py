#!/usr/bin/env python3
"""Three public lists that might name flight-training operations our school list misses, measured
against output/flight_line.json:

  nasr    FAA NASR 28-day airport file (APT_BASE.csv): airports whose OTHER_SERVICES lists INSTR
          (flight instruction) or RNTL (aircraft rental). The gap is the flagged airports where no
          school in our list is listed or inferred (scripts/infer_airports.py), with what the
          release's ADS-B data says about training-like aircraft based there.
  tachgo  Tach & Go's public directory of flying clubs, flight schools and FBOs (150thaero.org/directory),
          one static page per state plus filter pages and one page per listing for its website.
  ppp     SBA Paycheck Protection Program FOIA loan data, borrowers with NAICS 611512 (Flight Training).

Downloads are untrusted: they live in the git-ignored data/research-2026-11/gaps/ (one directory per
source) and this script reads them with python -I. PPP borrowers include sole proprietors, so nothing
from PPP that names a person or gives a street address is written to docs/; individuals appear only in
counts.

  python -I scripts/65_gap_sources.py fetch-tachgo        # polite: one request every 4 s
  python -I scripts/65_gap_sources.py ppp-filter          # NAICS 611512 rows from the 13 FOIA CSVs
  python -I scripts/65_gap_sources.py report --release DIR [--check-sites]

Downloads (identified user agent "aether-flight-school-research/1.0 (david@weekly.org)"):
  nasr/29_Oct_2026_APT_CSV.zip   https://nfdc.faa.gov/webContent/28DaySub/extra/29_Oct_2026_APT_CSV.zip
                                 (unzipped into nasr/x/)
  ppp/public_*_240930.csv        https://data.sba.gov/dataset/ppp-foia (13 CSVs, release of 2024-09-30)
  tachgo/                        written by fetch-tachgo

Hand labels for the seeded samples are in docs/hackers-2026-gap-labels.csv, keyed by source and id.
"""
import argparse, collections, csv, glob, hashlib, html, importlib.util, json, math, random, re, subprocess, sys, time
import urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT.parent.parent
RAW = ROOT / "data" / "research-2026-11" / "gaps"
UA = "aether-flight-school-research/1.0 (david@weekly.org)"
TG = "https://150thaero.org/directory"
FILTERS = ("type=school", "type=rental", "type=club", "tag=soaring")
SEED = 65
SAMPLE = 30
INDIVIDUAL_TYPES = {"Sole Proprietorship", "Independent Contractors", "Self-Employed Individuals"}
# A PPP name is published only if it carries a word that names a business, not just a legal suffix,
# so "Jane Doe LLC" stays out and "Doe Aviation LLC" goes in.
ORG_WORD = re.compile(r"aviat|aero|\bair|flight|\bfly|flying|pilot|academy|school|training|heli|wing|sky|jet|"
                      r"soar|glid|balloon|drone|\buas\b|\bsim|enterprise|service|group|company|club|center|"
                      r"centre|institute|college|universit|holding|ventures|partners|associates|solutions|"
                      r"international|systems|technolog|aircraft|airport|airways|airline|\bfbo\b|charter|"
                      r"propel|rotor|wright|eagle|hawk|falcon|condor|phoenix", re.I)
TRAINING_TEXT = re.compile(r"flight training|flight school|flight lessons?|learn to fly|discovery flight|"
                           r"intro(ductory)? flight|private pilot|instrument rating|flight instruct|\bcfi|"
                           r"pilot training|aircraft rental|airplane rental|rent an? (air)?plane|ground school|"
                           r"checkride|part 141|part 61|flying club|glider (rides|instruction|lessons)", re.I)
CLOSED_TEXT = re.compile(r"(we (have|are) (now )?(permanently )?closed|permanently closed|has (permanently )?closed|"
                         r"no longer in business|ceased operations|closed (its|our) doors|is now closed|"
                         r"no longer (offer|provid)ing flight|out of business)", re.I)
PARKED_TEXT = re.compile(r"(domain (name )?(is )?(for sale|may be for sale)|buy this domain|this domain is parked|"
                         r"parked free|hugedomains|sedoparking|dan\.com|afternic|domain has expired|"
                         r"godaddy\.com/domainsearch|this site can.t be reached|account suspended|"
                         r"website (is )?(coming soon|under construction)|future home of)", re.I)


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def mx():
    return _load("mx", HERE / "probes" / "match_external_school_lists.py")


def text_of(body):
    t = re.sub(r"(?is)<(script|style|svg)\b.*?</\1>", " ", body)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))).strip()


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "ignore")


# ---------------------------------------------------------------- Tach & Go

def tg_rows(raw):
    """Listings from the per-state pages: slug, state, name, kind, airport, town."""
    out = []
    for f in sorted(glob.glob(str(raw / "tachgo" / "state_*.html"))):
        st = Path(f).stem.split("_")[1].upper()
        for li in re.findall(r'(?s)<li data-testid="directory-row".*?</li>', Path(f).read_text()):
            slug = re.search(r'data-slug="([^"]+)"', li).group(1)
            name = html.unescape(re.search(r'directory-row-name"[^>]*>(.*?)</a>', li).group(1)).strip()
            spans = [html.unescape(re.sub(r"<[^>]+>", "", x)).strip() for x in re.findall(r"<span[^>]*>(.*?)</span>", li)]
            where = spans[1] if len(spans) > 1 else ""
            place, _, town = where.rpartition(" · ") if " · " in where else (where, "", "")
            ap_id, _, ap_name = place.partition(", ")
            out.append({"id": f"{st.lower()}/{slug}", "state": st, "name": name, "kind": spans[0] if spans else "",
                        "airport_id": ap_id.strip().upper(), "airport_name": ap_name.strip(), "town": town.strip()})
    return out


def tg_filters(raw):
    tags = collections.defaultdict(set)
    for f in glob.glob(str(raw / "tachgo" / "filter_*.html")):
        st, q = re.match(r"filter_([a-z]{2})_(.+)\.html", Path(f).name).groups()
        for slug in re.findall(r'data-testid="directory-row" data-slug="([^"]+)"', Path(f).read_text()):
            tags[f"{st}/{slug}"].add(q.split("_", 1)[1])
    return tags


def tg_relevant(r, tags):
    """Schools and clubs by kind, and FBOs the directory files under flight schools or aircraft rental."""
    t = tags.get(r["id"], set())
    return "school" in r["kind"].lower() or "club" in r["kind"].lower() or bool(t & {"school", "rental", "club"})


def tg_detail(raw, rid):
    p = raw / "tachgo" / "detail" / (rid.replace("/", "__") + ".html")
    if not p.exists():
        return None
    b = p.read_text()
    w = re.search(r'href="([^"]+)"[^>]*data-testid="directory-listing-website"', b)
    town = re.search(r'directory-listing-town">(.*?)</dd>', b)
    checked = re.search(r'directory-listing-checked">(.*?)</p>', b)
    return {"website": html.unescape(w.group(1)) if w else "",
            "town_full": html.unescape(town.group(1)) if town else "",
            "checked": html.unescape(re.sub(r"<[^>]+>", "", checked.group(1))) if checked else ""}


def fetch_tachgo(a):
    raw = Path(a.raw)
    d = raw / "tachgo"
    (d / "detail").mkdir(parents=True, exist_ok=True)
    log = open(d / "fetch.log", "a")

    def pull(url, path):
        if path.exists() and path.stat().st_size:
            return
        try:
            st, body = get(url)
        except urllib.error.HTTPError as e:
            st, body = e.code, ""
        path.write_text(body)
        log.write(f"{time.strftime('%FT%T')} {st} {len(body)} {url}\n"); log.flush()
        time.sleep(a.delay)

    pull(TG, d / "index.html")
    states = sorted(set(re.findall(r'href="/directory/([a-z]{2})"', (d / "index.html").read_text())))
    for s in states:
        pull(f"{TG}/{s}", d / f"state_{s}.html")
    for q in FILTERS:
        for s in states:
            pull(f"{TG}/{s}?{q}", d / f"filter_{s}_{q.replace('=', '_')}.html")
    tags = tg_filters(raw)
    todo = [r for r in tg_rows(raw) if tg_relevant(r, tags)]
    need = raw / "tachgo" / "need_detail.txt"
    if need.exists():  # written by report: listings not matched by name
        ids = set(need.read_text().split())
        todo = [r for r in todo if r["id"] in ids]
    print(f"{len(states)} states; {len(todo)} listings need a detail page")
    for i, r in enumerate(todo):
        pull(f"{TG}/{r['id']}", d / "detail" / (r["id"].replace("/", "__") + ".html"))
        if i % 200 == 0:
            print(i, flush=True)


# ---------------------------------------------------------------- PPP

def ppp_filter(a):
    raw = Path(a.raw) / "ppp"
    files = sorted(glob.glob(str(raw / "public_*.csv")))
    n, keep, fields = 0, [], None
    for f in files:
        with open(f, newline="", encoding="latin-1") as fh:
            rd = csv.DictReader(fh)
            fields = rd.fieldnames
            for r in rd:
                n += 1
                if (r.get("NAICSCode") or "").strip() == "611512":
                    keep.append(r)
    with open(raw / "naics_611512.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader(); w.writerows(keep)
    json.dump({"files": [Path(f).name for f in files], "rows": n, "naics_611512": len(keep)},
              open(raw / "filter_summary.json", "w"), indent=1)
    print(f"{n:,} loans in {len(files)} files; {len(keep):,} with NAICS 611512")


def ppp_borrowers(raw, m):
    """One row per borrower (normalized name and ZIP), with its loan count."""
    loans = list(csv.DictReader(open(raw / "ppp" / "naics_611512.csv", newline="", encoding="utf-8")))
    by = {}
    for r in loans:
        vs = m.variants(r["BorrowerName"])
        key = ((vs[0] if vs else r["BorrowerName"].lower()), (r["BorrowerZip"] or "")[:5])
        b = by.setdefault(key, {"id": hashlib.sha1("|".join(key).encode()).hexdigest()[:10], "name": r["BorrowerName"].strip(),
                                "city": r["BorrowerCity"].strip().title(), "state": r["BorrowerState"].strip().upper(),
                                "address": f'{r["BorrowerAddress"]}, {r["BorrowerCity"]}, {r["BorrowerState"]} {r["BorrowerZip"]}',
                                "btype": r["BusinessType"].strip(), "loans": 0, "jobs": 0, "status": set(), "nonprofit": r["NonProfit"]})
        b["loans"] += 1
        b["status"].add(r["LoanStatus"])
        try:
            b["jobs"] = max(b["jobs"], int(float(r["JobsReported"] or 0)))
        except ValueError:
            pass
    for b in by.values():
        b["individual"] = b["btype"] in INDIVIDUAL_TYPES or not ORG_WORD.search(re.sub(m.LEGAL, " ", b["name"].lower()))
    return loans, list(by.values())


# ---------------------------------------------------------------- shared

def run_matcher(rows, out, name, fields):
    path = out / f"{name}.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    ap = f"{ROOT / 'output' / 'airports.csv'},{ROOT / 'data' / 'ourairports_airports.csv'}"
    p = subprocess.run([sys.executable, "-I", str(HERE / "probes" / "match_external_school_lists.py"),
                        str(ROOT / "output" / "flight_line.json"), ap, str(out), str(path)],
                       capture_output=True, text=True, check=True)
    with open(out / f"{name}_matched.csv") as f:
        return p.stdout.strip().splitlines()[-1], list(csv.DictReader(f))


def site_check(url):
    """Homepage once: live, blocked or down; does it talk about flight training; does it say it closed
    or look parked. Challenge and refusal answers (401/402/403/409/429/503, or 202 with no text) are
    'blocked' and nothing tries to get past them."""
    u = url if re.match(r"^https?://", url, re.I) else "http://" + url
    res = None
    for attempt in (u, re.sub(r"^https://", "http://", u, flags=re.I)):
        try:
            req = urllib.request.Request(attempt, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                t = text_of(r.read(500_000).decode("utf-8", "ignore"))
                words = len(t.split())
                res = {"status": r.status, "final": r.geturl(), "training": bool(TRAINING_TEXT.search(t)),
                       "closed": bool(CLOSED_TEXT.search(t)), "parked": bool(PARKED_TEXT.search(t)) or words < 15,
                       "words": words, "state": "blocked" if (r.status == 202 and words < 20) else "ok"}
            break
        except urllib.error.HTTPError as e:
            res = {"status": e.code, "state": "blocked" if e.code in (401, 402, 403, 409, 429, 503) else "down",
                   "error": f"HTTP {e.code}"}
            break
        except Exception as e:  # noqa: BLE001 - DNS, TLS, timeout
            res = {"status": None, "state": "down", "error": f"{type(e).__name__}: {str(e)[:120]}"}
            if "CERTIFICATE" not in str(e) and "SSL" not in str(e):
                break
    return res


def check_sites(urls, cache_path, fetch):
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    todo = sorted({u for u in urls if u and u not in cache})
    if todo and fetch:
        print(f"checking {len(todo)} homepages", flush=True)
        with ThreadPoolExecutor(8) as ex:
            for u, r in zip(todo, ex.map(site_check, todo)):
                cache[u] = r
        cache_path.write_text(json.dumps(cache, indent=0, sort_keys=True))
    return cache


def site_class(s):
    if not s:
        return "unchecked"
    if s["state"] != "ok":
        return s["state"]
    if s.get("parked"):
        return "parked"
    if s.get("closed"):
        return "closed_text"
    return "training" if s.get("training") else "no_training_text"


def wilson(k, n, z=1.96):
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def hav_nm(la1, lo1, la2, lo2):
    p1, p2 = math.radians(la1), math.radians(la2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 2 * 3440.065 * math.asin(math.sqrt(a))


def load_labels(path):
    if not path.exists():
        return {}
    return {(r["source"], r["id"]): r for r in csv.DictReader(open(path, newline=""))}


# ---------------------------------------------------------------- report

def report(a):
    raw, rel = Path(a.raw), Path(a.release)
    m = mx()
    g59 = _load("g59", HERE / "59_google_places.py")
    ai = _load("ai", HERE / "airport_index.py")
    alias, _meta = ai.canonical_ids(ROOT / "data" / "ourairports_airports.csv")

    def key(x):
        x = (x or "").strip().upper()
        if not x:
            return ""
        return alias.get(x) or (alias.get(x[1:]) if len(x) == 4 and x[0] == "K" else None) or x

    fl = json.load(open(ROOT / "output" / "flight_line.json"))
    labels = load_labels(Path(a.labels))
    L = []  # markdown lines

    # ---- schools' airports and fleets
    listed, inferred = set(), set()
    fleet_tails, tail_home = set(), collections.defaultdict(list)
    for s in fl:
        for l in s.get("locations") or []:
            if l.get("airport_id"):
                listed.add(key(l["airport_id"]))
        for l in s.get("inferred_locations") or []:
            inferred.add(key(l["airport_id"]))
        for f in s.get("fleet") or []:
            n = (f.get("n_number") or "").upper()
            if n:
                fleet_tails.add(n)
            h = (f.get("adsb") or {}).get("home_airport")
            if h and n:
                tail_home[key(h)].append((n, s["domain"]))
    school_ap = listed | inferred

    # ---- release aircraft: home airport and training score
    import pandas as pd
    ac = pd.read_parquet(rel / "aircraft.parquet", columns=["n_number", "home_airport", "registrant", "state", "days_airborne"])
    sc = pd.read_parquet(rel / "training_scores.parquet")
    ac = ac.merge(sc, left_on="n_number", right_on="n", how="left")
    ac["home"] = ac["home_airport"].fillna("").map(key)
    ac["on_fleet"] = ac["n_number"].str.upper().isin(fleet_tails)
    tr = json.load(open(rel / "training.json"))
    thr = tr["threshold"]
    by_home = {}
    for h, grp in ac[ac["home"] != ""].groupby("home"):
        s_ = grp["score"].fillna(0)
        by_home[h] = {"based": len(grp), "flag": int((s_ >= thr).sum()),
                      "flag_new": int(((s_ >= thr) & ~grp["on_fleet"]).sum()),
                      "strong_new": int(((s_ >= 0.7) & ~grp["on_fleet"]).sum())}

    # ---- NASR
    base = list(csv.DictReader(open(raw / "nasr" / "x" / "APT_BASE.csv", newline="", encoding="utf-8", errors="replace")))
    eff = sorted({r["EFF_DATE"] for r in base})
    for r in base:
        r["flags"] = {t.strip() for t in (r["OTHER_SERVICES"] or "").split(",") if t.strip()}
        r["key"] = key(r["ICAO_ID"]) if r["ICAO_ID"].strip() else key(r["ARPT_ID"])
        if r["key"] not in alias.values() and key(r["ARPT_ID"]) in alias.values():
            r["key"] = key(r["ARPT_ID"])
    nasr_ll = {r["key"]: (float(r["LAT_DECIMAL"]), float(r["LONG_DECIMAL"])) for r in base}
    ap_ll = {}
    for r in csv.DictReader(open(ROOT / "output" / "airports.csv")):
        try:
            ap_ll[key(r["airport_id"])] = (float(r["latitude"]), float(r["longitude"]))
        except ValueError:
            pass
    school_pts = [nasr_ll.get(k) or ap_ll.get(k) for k in school_ap]
    school_pts = [p for p in school_pts if p]
    services = collections.Counter(t for r in base for t in r["flags"])
    flag_n = {f: sum(1 for r in base if f in r["flags"]) for f in ("INSTR", "RNTL", "GLD")}
    us = [r for r in base if r["COUNTRY_CODE"] == "US"]
    tr_ap = [r for r in us if r["flags"] & {"INSTR", "RNTL"}]
    site = {"A": "airport", "H": "heliport", "C": "seaplane base", "G": "gliderport", "U": "ultralight", "B": "balloonport"}

    # ---- Tach & Go (needed for the NASR cross-check)
    tgr = tg_rows(raw)
    tags = tg_filters(raw)
    for r in tgr:
        r["tags"] = sorted(tags.get(r["id"], set()))
        r["relevant"] = tg_relevant(r, tags)
        r.update(tg_detail(raw, r["id"]) or {"website": "", "town_full": "", "checked": ""})
        r["city"] = r["town"].split("/")[0].strip()
    tg_at = collections.defaultdict(list)
    for r in tgr:
        if r["relevant"] and r["airport_id"]:
            tg_at[key(r["airport_id"])].append(r)

    # ---- Tach & Go matching (the NASR gap uses it)
    rel_rows = [r for r in tgr if r["relevant"]]
    kinds = collections.Counter(r["kind"] for r in tgr)
    tg_fields = ["source", "id", "name", "city", "state", "website", "kind", "tags", "airport_id", "airport_name"]
    for r in rel_rows:
        r["source"] = "tachgo"
        r["tags"] = ",".join(r["tags"]) if isinstance(r["tags"], list) else r["tags"]
    tg_summary, tg_m = run_matcher(rel_rows, raw / "tachgo", "tachgo", tg_fields)
    tg_by_id = {r["id"]: r for r in rel_rows}
    for r in tg_m:
        r.update({k: v for k, v in tg_by_id[r["id"]].items() if k not in r})
        r["confirmed"] = g59.confirmed_match(r)
    mt = collections.Counter(r["match_type"] for r in tg_m)
    tg_new = [r for r in tg_m if not r["confirmed"]]
    # fetch-tachgo pulls a listing page (for its website) only for listings the name rules do not already match
    (raw / "tachgo" / "need_detail.txt").write_text("".join(f"{r['id']}\n" for r in sorted(tg_new, key=lambda r: r["id"])))
    tg_kind = lambda r: ("Flight school" if "school" in r["kind"].lower() else "Flying club" if "club" in r["kind"].lower()
                         else "FBO, filed under school" if "school" in r["tags"] else "FBO, filed under rental")
    conf_ids = {r["id"]: r["school_domain"] or "(a record without a website)" for r in tg_m if r["confirmed"]}
    # schools in our list whose contact address is in a gap airport's city: in our list, filed under another airport
    addr_city = collections.defaultdict(set)
    for s in fl:
        pa = m.parse_addr((s.get("contact") or {}).get("address") or "")
        if pa:  # domains only: records without a website include FAA registrants named after people
            addr_city[pa].add(s["domain"] or "(a record without a website)")

    gap = []
    for r in tr_ap:
        if r["key"] in school_ap:
            continue
        la, lo = float(r["LAT_DECIMAL"]), float(r["LONG_DECIMAL"])
        near = min((hav_nm(la, lo, p[0], p[1]) for p in school_pts), default=999)
        bh = by_home.get(r["key"], {"based": 0, "flag": 0, "flag_new": 0, "strong_new": 0})
        gap.append({"airport_id": r["ARPT_ID"], "key": r["key"], "name": r["ARPT_NAME"].title(), "city": r["CITY"].title(),
                    "state": r["STATE_CODE"], "site_type": site.get(r["SITE_TYPE_CODE"], r["SITE_TYPE_CODE"]),
                    "use": "public" if r["FACILITY_USE_CODE"] == "PU" else "private",
                    "instr": "INSTR" in r["flags"], "rntl": "RNTL" in r["flags"], "last_info": r["LAST_INFO_RESPONSE"],
                    "last_insp": r["LAST_INSPECTION"],
                    "school_tails": len(tail_home.get(r["key"], [])),
                    "school_tail_domains": sorted({d for _, d in tail_home.get(r["key"], [])}),
                    "based_adsb": bh["based"], "flagged_new": bh["flag_new"], "strong_new": bh["strong_new"],
                    "nearest_school_nm": round(near, 1), "tachgo": [x["name"] for x in tg_at.get(r["key"], [])],
                    "tachgo_in_list": sorted({conf_ids[x["id"]] for x in tg_at.get(r["key"], []) if x["id"] in conf_ids}),
                    "addr_schools": sorted(addr_city.get((m.norm_city(r["CITY"]), r["STATE_CODE"]), set()))})
    covered = [r for r in tr_ap if r["key"] in school_ap]
    via_inf = [r for r in covered if r["key"] not in listed]

    # recall of the INSTR flag at airports where our list has a school (US NASR airports only)
    nasr_keys = {r["key"]: r for r in us}
    sch_in_nasr = [nasr_keys[k] for k in listed if k in nasr_keys]
    sch_instr = sum(1 for r in sch_in_nasr if r["flags"] & {"INSTR", "RNTL"})

    L += ["# Three more public sources vs our flight-school list", "",
          "Generated by `research/FlightSchoolMapping/scripts/65_gap_sources.py`; do not edit by hand.", ""]
    head_idx = len(L)
    L += ["## Sources and method", "",
          f"- **School list**: `output/flight_line.json`, {len(fl):,} schools. A school's airports are its listed `locations` "
          f"plus `inferred_locations` (airports home to its fleet's tails, `scripts/infer_airports.py`). Identifiers are "
          f"normalized through `scripts/airport_index.py canonical_ids`, so KSQL and SQL are the same airport.",
          f"- **ADS-B**: release `{rel.name}` (`aircraft.parquet` home airports, `training_scores.parquet` from "
          f"`40_training_detect.py`). A score of {thr} or more is that model's training flag; its precision against website "
          f"fleets is at least {tr['metrics']['precision_lower_bound']:.0%} (a lower bound, because unlabeled aircraft "
          f"include schools we never found). 'New' flagged aircraft are not on any school's fleet list.",
          f"- **FAA NASR**: 28-day subscription APT_CSV, effective {', '.join(eff)} "
          f"(`nfdc.faa.gov/webContent/28DaySub/extra/29_Oct_2026_APT_CSV.zip`; nfdc.faa.gov's robots.txt allows "
          f"`/webContent/`). `OTHER_SERVICES` is self-reported by the airport to the FAA; `LAST_INFO_RESPONSE` is when it last answered.",
          "- **Tach & Go** (SyncEtc LLC): `150thaero.org/directory`, one static page per state, filter pages "
          "(`?type=school`, `rental`, `club`, `?tag=soaring`) and a page per listing for its website. robots.txt disallows "
          "only `/feeds/`; the Customer and User Terms (updated 2026-10-14) say listings show name, kind, airport, "
          "website and offerings and contain no rule against reading the public directory. Fetched one request every 4 s "
          f"with an identified user agent (`{UA}`).",
          "- **SBA PPP**: the FOIA loan files on data.sba.gov (`public_150k_plus_240930.csv` and twelve "
          "`public_up_to_150k_*_240930.csv`), filtered to `NAICSCode` 611512 (Flight Training). Borrowers are de-duplicated "
          "by normalized name and ZIP. Sole proprietors, independent contractors, self-employed people and any name "
          "without a business word are *individuals*: counted, never named here.",
          "- **Matching**: `probes/match_external_school_lists.py` (domain, exact name, street address, fuzzy name within a "
          "state), then the stricter `confirmed_match` from `59_google_places.py` (websites agree, or the names share every "
          "distinctive word and are nearly the same). A loose match that fails the strict rule is *unconfirmed* and is "
          "treated as possibly new.",
          "- **Homepages**: unmatched entries with a website get one fetch of the homepage: live, blocked (401/403/429/503, "
          "nothing tries to get past them) or down; training words on the page; parked-domain or 'we have closed' text.",
          f"- **Hand checks**: a seeded random sample (`random.Random({SEED})`) of {SAMPLE} unmatched entries per source, "
          "each looked up on its own website or by web search, never AOPA. Labels and evidence URLs are in "
          "`docs/hackers-2026-gap-labels.csv`.", ""]

    # ---------------- NASR section
    by_state = collections.Counter(g["state"] for g in gap)

    def bins(field):
        c = collections.Counter("none on file" if not g[field] else "before 2000" if g[field] < "2000" else
                                "2000-2019" if g[field] < "2020" else "2020-2023" if g[field] < "2024" else "2024-2026"
                                for g in gap)
        return ", ".join(f"{k} {c[k]:,}" for k in ("2024-2026", "2020-2023", "2000-2019", "before 2000", "none on file") if c[k])
    sig = lambda g: g["school_tails"] > 0 or g["flagged_new"] > 0
    L += ["## 1. FAA NASR airports that list flight instruction or rental", "",
          "| Flag in OTHER_SERVICES | Reported by the other researcher | All NASR records | US, open |",
          "|---|---|---|---|"]
    rep = {"INSTR": "about 2,088", "RNTL": "1,483", "GLD": "162"}
    for f_ in ("INSTR", "RNTL", "GLD"):
        L.append(f"| {f_} | {rep[f_]} | {flag_n[f_]:,} | "
                 f"{sum(1 for r in us if f_ in r['flags'] and r['ARPT_STATUS'] == 'O'):,} |")
    both = sum(1 for r in tr_ap if {"INSTR", "RNTL"} <= r["flags"])
    L += ["", f"Our counts are from the {eff[0]} cycle and run slightly above the reported ones (an earlier cycle, or a filter "
          f"we do not know). {len(tr_ap):,} US airports list INSTR or RNTL ({both:,} list both). Other flags for scale: "
          + ", ".join(f"{k} {v:,}" for k, v in services.most_common() if k not in ("INSTR", "RNTL", "GLD")) + ".", "",
          "Site types of the INSTR-or-RNTL airports: " + ", ".join(
              f"{site.get(k, k)} {v:,}" for k, v in collections.Counter(r["SITE_TYPE_CODE"] for r in tr_ap).most_common()) + ".", "",
          f"**How complete the flag is.** Of the {len(sch_in_nasr):,} US NASR airports where our list *lists* a school, "
          f"{sch_instr:,} ({sch_instr / max(1, len(sch_in_nasr)):.0%}) carry INSTR or RNTL. So the flag is absent at about one in "
          f"{round(len(sch_in_nasr) / max(1, len(sch_in_nasr) - sch_instr))} airports that do have training, and nothing checks that a "
          f"flagged airport still has it: a flagged airport without a school is a lead, not a finding.", "",
          "### The gap", "",
          f"- {len(covered):,} of the {len(tr_ap):,} flagged airports have a school in our list "
          f"({len(covered) - len(via_inf):,} by listed airport, {len(via_inf):,} more only by inferred airport).",
          f"- **{len(gap):,} flagged airports have no school in our list** "
          f"({sum(1 for g in gap if g['instr']):,} list INSTR, {sum(1 for g in gap if g['rntl']):,} RNTL; "
          f"{sum(1 for g in gap if g['use'] == 'private'):,} are private-use).",
          f"- {sum(1 for g in gap if g['school_tails']):,} gap airports are home to at least one tail on some school's fleet "
          f"({sum(g['school_tails'] for g in gap):,} tails), which usually means a school at a neighboring field keeps aircraft there.",
          f"- {sum(1 for g in gap if g['flagged_new']):,} are home to at least one training-flagged aircraft that is on no "
          f"school's fleet; {sum(1 for g in gap if g['strong_new'] >= 3):,} to three or more scoring 0.7 or higher.",
          f"- {sum(1 for g in gap if not g['based_adsb']):,} are home to no aircraft at all in the release (it assigns a home airport "
          f"to {int((ac['home'] != '').sum()):,} aircraft at {ac.loc[ac['home'] != '', 'home'].nunique():,} airports, so small fields often have none).",
          f"- {sum(1 for g in gap if g['tachgo']):,} have a Tach & Go school, club or rental listing at the same airport; at "
          f"**{sum(1 for g in gap if g['tachgo_in_list']):,}** that listing strictly matches a school *in our list*, which our list "
          f"files under a different airport (or none).",
          f"- {sum(1 for g in gap if g['addr_schools']):,} are in a city where a school in our list has its contact address.",
          f"- **Unexplained gap**: {sum(1 for g in gap if not (g['tachgo_in_list'] or g['addr_schools'] or g['school_tails'])):,} "
          f"airports have none of those three explanations (a matched Tach & Go listing, a school address in the city, a school "
          f"tail based there); {sum(1 for g in gap if not (g['tachgo_in_list'] or g['addr_schools'] or g['school_tails']) and g['flagged_new']):,} "
          f"of them have a training-flagged aircraft on no fleet.",
          f"- Median distance to the nearest airport with a school in our list: "
          f"{sorted(g['nearest_school_nm'] for g in gap)[len(gap) // 2] if gap else 0} nm; "
          f"{sum(1 for g in gap if g['nearest_school_nm'] <= 10):,} are within 10 nm.",
          "- When the FAA last heard from these airports: last data response " + bins("last_info") +
          "; last inspection " + bins("last_insp") + ".", "",
          "Gap airports by state, with how many have any ADS-B or fleet signal (a school's tail based there, or a "
          "training-flagged aircraft on no fleet):", "",
          "| State | Gap airports | With a signal | With 3+ strong new aircraft |", "|---|---|---|---|"]
    for st_, n_ in sorted(by_state.items(), key=lambda x: (-x[1], x[0])):
        gs = [g for g in gap if g["state"] == st_]
        L.append(f"| {st_} | {n_} | {sum(1 for g in gs if sig(g))} | {sum(1 for g in gs if g['strong_new'] >= 3)} |")
    L += [""]
    strong = sorted([g for g in gap if g["strong_new"] >= 3], key=lambda g: (-g["strong_new"], g["airport_id"]))
    L += ["Gap airports with the most strongly flagged aircraft that are on no school's fleet "
          "(full list with every signal in `docs/hackers-2026-gap-airports.csv`):", "",
          "| Airport | Name | City | State | Strong new | Flagged new | Tach & Go listing | In our list under another airport |",
          "|---|---|---|---|---|---|---|---|"]
    for g in strong[:25]:
        L.append(f"| {g['airport_id']} | {g['name']} | {g['city']} | {g['state']} | {g['strong_new']} | {g['flagged_new']} | "
                 f"{'; '.join(g['tachgo'])[:60]} | {', '.join(g['tachgo_in_list'] or g['addr_schools'])[:60]} |")
    L += [""]
    # NASR hand check: seeded sample of gap airports with a strong signal
    nasr_pool = sorted(strong, key=lambda g: g["airport_id"])
    nasr_sample = random.Random(SEED).sample(nasr_pool, min(SAMPLE, len(nasr_pool)))
    nasr_lab = [labels.get(("nasr", g["airport_id"])) for g in nasr_sample]
    vc = collections.Counter((x or {}).get("verdict", "unlabeled") for x in nasr_lab)
    L += [f"**Hand check.** {len(nasr_sample)} airports drawn at random from the {len(nasr_pool)} with three or more strong "
          f"new aircraft, each searched for a training operation on the field: " +
          ", ".join(f"{k} {v}" for k, v in vc.most_common()) + ".", ""]
    nasr_new = vc.get("new_training", 0)
    nasr_weak = vc.get("new_training_weak", 0)

    # ---------------- Tach & Go section
    L += ["## 2. Tach & Go directory", "",
          f"{len(tgr):,} listings across {len(set(r['state'] for r in tgr))} state pages (each page's own count agrees). "
          "Kinds: " + ", ".join(f"{k} {v:,}" for k, v in kinds.most_common()) + ". Filter tags: " +
          ", ".join(f"{q.split('=')[1]} {sum(1 for t in tags.values() if q.split('=')[1] in t):,}" for q in FILTERS) + ".", "",
          f"Training-relevant listings (schools, clubs, and FBOs the directory files under flight schools or aircraft rental): "
          f"**{len(rel_rows):,}**. Listing pages (which carry the website) were fetched only for the "
          f"{sum(1 for r in rel_rows if tg_detail(raw, r['id']) is not None):,} listings the name rules did not already match; "
          f"{sum(1 for r in rel_rows if r['website']):,} of those have a website. "
          f"The other {len(tgr) - len(rel_rows):,} are FBOs with neither tag and are left out.", "",
          f"Matcher output: `{tg_summary}`", "",
          "| Kind | Listings | Matched (strict) | Loose match only | No match |", "|---|---|---|---|---|"]
    for k in ("Flight school", "Flying club", "FBO, filed under school", "FBO, filed under rental"):
        rs = [r for r in tg_m if tg_kind(r) == k]
        if not rs:
            continue
        L.append(f"| {k} | {len(rs):,} | {sum(r['confirmed'] for r in rs):,} | "
                 f"{sum(1 for r in rs if not r['confirmed'] and r['match_type'] in ('exact', 'address', 'fuzzy')):,} | "
                 f"{sum(1 for r in rs if r['match_type'] in ('none', 'colocated')):,} |")
    conf = sum(r["confirmed"] for r in tg_m)
    # where both lists place a matched school: is Tach & Go's airport among our airports for it?
    aps_by = collections.defaultdict(set)
    for s in fl:
        ks = {key(l["airport_id"]) for l in (s.get("locations") or []) + (s.get("inferred_locations") or []) if l.get("airport_id")}
        for k_ in (s.get("domain") or "", s.get("name") or ""):
            if k_:
                aps_by[k_] |= ks if ks else {"<none>"}
    placed = [r for r in tg_m if r["confirmed"] and r["airport_id"] and (r["school_domain"] or r["school_name"])]
    agree = [r for r in placed if key(r["airport_id"]) in aps_by.get(r["school_domain"] or r["school_name"], set())]
    no_ap = [r for r in placed if aps_by.get(r["school_domain"] or r["school_name"]) == {"<none>"}]
    # listed airport identifiers in a different state from the school's own contact address
    mism, tot_l = 0, 0
    for s in fl:
        pa = m.parse_addr((s.get("contact") or {}).get("address") or "")
        if not pa:
            continue
        for l in s.get("locations") or []:
            md = _meta.get(key(l.get("airport_id")))
            if md and md[2].startswith("US-"):
                tot_l += 1
                mism += md[2][3:] != pa[1]
    L += ["", f"Overlap: **{conf:,} of {len(tg_m):,} ({conf / len(tg_m):.0%})** match a school in our list under the strict rule "
          f"(by rule: " + ", ".join(f"{k} {v:,}" for k, v in mt.most_common()) + " before the strict filter). "
          f"{len(tg_new):,} are possibly new.", "",
          f"**Where the two lists place the same school.** Of {len(placed):,} strictly matched listings with an airport, "
          f"{len(agree):,} ({len(agree) / max(1, len(placed)):.0%}) have Tach & Go's airport among our airports for that school "
          f"(listed or inferred); {len(no_ap):,} of the rest are schools with no airport in our list at all. The remainder are "
          f"multi-base schools whose other base we lack, or a wrong identifier on one side. A check on our side alone: of "
          f"{tot_l:,} listed school airports with a parseable contact address, {mism:,} ({mism / max(1, tot_l):.1%}) are in a "
          f"different state from the address (some are real out-of-state bases; others are wrong identifiers, such as KPWT "
          f"(Bremerton, WA) for a Weatherford, TX base).", ""]
    sites = check_sites([r["website"] for r in tg_new], raw / "tachgo" / "sites.json", a.check_sites)
    for r in tg_new:
        r["site"] = site_class(sites.get(r["website"])) if r["website"] else "no_website"
    sc_ = collections.Counter(r["site"] for r in tg_new)
    L += ["Homepage checks of the possibly new listings:", "", "| Homepage | Schools | Clubs | FBOs | All |", "|---|---|---|---|---|"]
    for c_ in ("training", "no_training_text", "closed_text", "parked", "down", "blocked", "no_website", "unchecked"):
        rs = [r for r in tg_new if r["site"] == c_]
        if rs:
            L.append(f"| {c_} | {sum(1 for r in rs if tg_kind(r) == 'Flight school')} | {sum(1 for r in rs if tg_kind(r) == 'Flying club')} | "
                     f"{sum(1 for r in rs if tg_kind(r).startswith('FBO'))} | {len(rs)} |")
    tg_sample = random.Random(SEED).sample(sorted(tg_new, key=lambda r: r["id"]), min(SAMPLE, len(tg_new)))
    tg_lab = [labels.get(("tachgo", r["id"])) for r in tg_sample]
    tvc = collections.Counter((x or {}).get("verdict", "unlabeled") for x in tg_lab)
    tg_k = tvc.get("new_training", 0)
    tg_w = tg_k + tvc.get("new_training_weak", 0)
    lo_, hi_ = wilson(tg_k, len(tg_sample))
    wlo, whi = wilson(tg_w, len(tg_sample))
    L += ["", f"**Hand check** of {len(tg_sample)} drawn at random from the {len(tg_new):,} possibly new: " +
          ", ".join(f"{k} {v}" for k, v in tvc.most_common()) +
          f". *new_training_weak* means the only evidence of training is a self-reported service checkbox (AirNav) or a bare "
          f"listing, with no site of its own. Precision of 'possibly new' as a genuinely new training operation: strict "
          f"{tg_k}/{len(tg_sample)} ({lo_:.0%}–{hi_:.0%}, 95% Wilson); counting weak evidence {tg_w}/{len(tg_sample)} "
          f"({wlo:.0%}–{whi:.0%}). Applied to all {len(tg_new):,}: about **{round(len(tg_new) * tg_k / max(1, len(tg_sample))):,}** "
          f"new operations ({round(len(tg_new) * lo_):,}–{round(len(tg_new) * hi_):,}) on strict evidence, "
          f"{round(len(tg_new) * tg_w / max(1, len(tg_sample))):,} counting weak. The strict ones by kind: " + ", ".join(
              f"{k} {v}" for k, v in collections.Counter(tg_kind(r) for r, x in zip(tg_sample, tg_lab)
                                                          if x and x["verdict"] == "new_training").most_common()) + ".", ""]
    L += ["| Listing | Kind | State | Homepage | Verdict | Note |", "|---|---|---|---|---|---|"]
    for r, lab in zip(tg_sample, tg_lab):
        L.append(f"| {r['name']} | {tg_kind(r)} | {r['state']} | {r['site']} | {(lab or {}).get('verdict', '')} | {(lab or {}).get('note', '')} |")
    L += [""]

    # ---------------- PPP section
    fsum = json.load(open(raw / "ppp" / "filter_summary.json"))
    loans, bor = ppp_borrowers(raw, m)
    ind = [b for b in bor if b["individual"]]
    org = [b for b in bor if not b["individual"]]
    for b in bor:
        b["source"] = "ppp"
        b["website"] = ""
    pp_summary, pp_m = run_matcher(bor, raw / "ppp", "ppp_borrowers", ["source", "id", "name", "city", "state", "website", "address"])
    for b, r in zip(bor, pp_m):
        b["match_type"], b["school_name"], b["school_domain"] = r["match_type"], r["school_name"], r["school_domain"]
        b["confirmed"] = g59.confirmed_match(r)
    # registry: aircraft registered in the borrower's exact normalized name in the same state
    regs = collections.defaultdict(list)
    for rg, st_, n_, sc0, da in ac[["registrant", "state", "n_number", "score", "days_airborne"]].itertuples(index=False):
        if isinstance(rg, str):
            vs = m.variants(rg)
            if vs:
                regs[(vs[0], st_)].append((n_, 0 if sc0 != sc0 else sc0, da or 0))
    for b in bor:
        vs = m.variants(b["name"])
        hits = regs.get((vs[0], b["state"]), []) if vs else []
        b["reg_aircraft"] = len(hits)
        b["reg_flying"] = sum(1 for h in hits if h[2] > 0)
        b["reg_flagged"] = sum(1 for h in hits if h[1] >= thr)
    pmt = collections.Counter(b["match_type"] for b in bor)
    pp_new = [b for b in org if not b["confirmed"]]
    L += ["## 3. SBA PPP loans to flight-training businesses (NAICS 611512)", "",
          f"{fsum['rows']:,} loans in {len(fsum['files'])} files; **{fsum['naics_611512']:,}** with NAICS 611512, "
          f"from **{len(bor):,}** borrowers ({len(bor) and sum(1 for b in bor if b['loans'] > 1):,} took two draws). "
          f"Loan status: " + ", ".join(f"{k} {v:,}" for k, v in collections.Counter(l['LoanStatus'] for l in loans).most_common()) + ".", "",
          "| Borrower type | Borrowers |", "|---|---|"]
    for k, v in collections.Counter(b["btype"] for b in bor).most_common():
        L.append(f"| {k} | {v:,} |")
    L += ["", f"{len(ind):,} borrowers are individuals by the rule above and are not named anywhere; {len(org):,} are organizations.",
          "By state (borrowers): " + ", ".join(f"{k} {v}" for k, v in collections.Counter(b['state'] for b in bor).most_common(12)) + ".", "",
          f"Matcher output (all borrowers): `{pp_summary}`", "",
          "| | Organizations | Individuals |", "|---|---|---|",
          f"| Matched (strict) | {sum(b['confirmed'] for b in org):,} | {sum(b['confirmed'] for b in ind):,} |",
          f"| Loose match only | {sum(1 for b in org if not b['confirmed'] and b['match_type'] in ('exact', 'address', 'fuzzy')):,} | "
          f"{sum(1 for b in ind if not b['confirmed'] and b['match_type'] in ('exact', 'address', 'fuzzy')):,} |",
          f"| No match | {sum(1 for b in org if b['match_type'] in ('none', 'colocated')):,} | {sum(1 for b in ind if b['match_type'] in ('none', 'colocated')):,} |",
          "", f"By rule before the strict filter: " + ", ".join(f"{k} {v:,}" for k, v in pmt.most_common()) + ".", "",
          f"**Registry evidence.** Of the {len(pp_new):,} organizations not matched, {sum(1 for b in pp_new if b['reg_aircraft']):,} have one or "
          f"more aircraft registered in exactly their name in their state ({sum(1 for b in pp_new if b['reg_flying']):,} with an aircraft "
          f"seen flying in the window, {sum(1 for b in pp_new if b['reg_flagged']):,} with a training-flagged one). For comparison, of the "
          f"{sum(b['confirmed'] for b in org):,} matched organizations, {sum(1 for b in org if b['confirmed'] and b['reg_aircraft']):,} do. "
          "Many schools lease aircraft from owners' LLCs, so no aircraft in the borrower's name is weak evidence against.",
          "", "The PPP file has no website, so a homepage is not derivable from it; the hand check found one by web search.", ""]
    pp_sample = random.Random(SEED).sample(sorted(pp_new, key=lambda b: b["id"]), min(SAMPLE, len(pp_new)))
    pp_lab = [labels.get(("ppp", b["id"])) for b in pp_sample]
    pvc = collections.Counter((x or {}).get("verdict", "unlabeled") for x in pp_lab)
    pp_k = pvc.get("new_training", 0)
    plo, phi = wilson(pp_k, len(pp_sample))
    L += [f"**Hand check** of {len(pp_sample)} drawn at random from the {len(pp_new):,} unmatched organizations: " +
          ", ".join(f"{k} {v}" for k, v in pvc.most_common()) +
          f". Precision as a genuinely new, still-operating training operation: {pp_k}/{len(pp_sample)} ({plo:.0%}–{phi:.0%}). "
          f"Applied to all {len(pp_new):,}: about **{round(len(pp_new) * pp_k / max(1, len(pp_sample))):,}** "
          f"({round(len(pp_new) * plo):,}–{round(len(pp_new) * phi):,}).", "",
          "| Borrower | State | Aircraft in its name | Verdict | Note |", "|---|---|---|---|---|"]
    for b, lab in zip(pp_sample, pp_lab):
        L.append(f"| {b['name'].title()} | {b['state']} | {b['reg_aircraft']} | {(lab or {}).get('verdict', '')} | {(lab or {}).get('note', '')} |")
    L += [""]

    # ---------------- cross-check with another agent's enumeration. Counts and source labels only: those files carry
    # contact details and AOPA-derived rows, so no name or field from them is written here. AOPA-only rows are counted
    # separately so the split stays visible.
    if a.external:
        ext = Path(a.external)
        cm = list(csv.DictReader(open(ext / "c_master_matched.csv", newline="", encoding="utf-8", errors="replace")))
        cls = lambda r: ("Tach & Go" if "150thAero" in r["sources"] else "BestAviation, no Tach & Go"
                         if "BestAviation" in r["sources"] else "AOPA only" if all(x.startswith("AOPA") for x in r["sources"].split("|"))
                         else "other")
        nk = lambda n, st: ((m.variants(n) or [""])[0], (st or "").strip().upper())
        their = {}
        for r in cm:
            if "150thAero" in r["sources"]:
                their.setdefault(nk(r["name"], r["state"]), r)
        mine = {nk(r["name"], r["state"]): r for r in tg_m}
        allmine = {nk(r["name"], r["state"]) for r in tgr}
        both = set(their) & set(mine)
        xt = collections.Counter(("ours" if mine[k]["confirmed"] else "new", their[k]["verdict"]) for k in both)
        t_only_new = [k for k, r in their.items() if r["verdict"] == "new" and r["sources"] == "150thAero"]
        t_only_new_mine = collections.Counter(("possibly new" if not mine[k]["confirmed"] else "matched")
                                              if k in mine else ("FBO left out" if k in allmine else "no name match")
                                              for k in t_only_new)
        my_new_keys = [nk(r["name"], r["state"]) for r in tg_new]
        my_new_theirs = collections.Counter(("they match it to ours" if their[k]["verdict"] == "ours" else "they also call it new")
                                            if k in their else "not in their Tach & Go rows" for k in my_new_keys)
        by_cls = collections.Counter((cls(r), r["verdict"]) for r in cm)
        L += ["## Cross-check with the Part 61 enumeration", "",
              f"Another agent's merged list ({len(cm):,} entities, matched to our list in `docs/hackers-2026-master-list-check.md`). "
              "Its AOPA rows came from AOPA's AirportsAPI, which this project does not otherwise use; they appear below only as "
              "counts, kept separate. Compared by normalized name and state.", "",
              "| Source of the entity | Entities | Matched to our list (theirs) | Unmatched |", "|---|---|---|---|"]
        for c_ in ("Tach & Go", "BestAviation, no Tach & Go", "AOPA only", "other"):
            n_ = by_cls[(c_, "ours")] + by_cls[(c_, "new")]
            if n_:
                L.append(f"| {c_} | {n_:,} | {by_cls[(c_, 'ours')]:,} | {by_cls[(c_, 'new')]:,} |")
        L += ["",
              f"- {len(their):,} distinct name-and-state pairs carry Tach & Go as a source; {len(both):,} pair with one of my "
              f"{len(tg_m):,} training-relevant listings.",
              "- Agreement on the paired ones (mine / theirs): " + ", ".join(f"{a_} / {b_} {v:,}" for (a_, b_), v in sorted(xt.items())) + ".",
              f"- Their {len(t_only_new):,} unmatched Tach & Go-only entities, in my pass: " +
              ", ".join(f"{k} {v:,}" for k, v in t_only_new_mine.most_common()) + ".",
              f"- My {len(tg_new):,} possibly new listings, in theirs: " + ", ".join(f"{k} {v:,}" for k, v in my_new_theirs.most_common()) + "."]
        ua = list(csv.DictReader(open(ext / "unmatched_faa_instr_airports.csv", newline="", encoding="utf-8", errors="replace")))
        theirs_ap = {key(r["icao_id"] or r["faa_lid"]) for r in ua}
        mine_ap = {g["key"] for g in gap}
        t_only, m_only = theirs_ap - mine_ap, mine_ap - theirs_ap
        lids, at_lid = collections.defaultdict(set), collections.defaultdict(list)
        for r in cm:
            if r["faa_lid"]:
                at_lid[key(r["faa_lid"])].append(r)
        for r in cm:
            if r["faa_lid"]:
                lids[key(r["faa_lid"])].add(cls(r))
        m_only_cls = collections.Counter("a non-AOPA entity" if lids.get(k, set()) - {"AOPA only"} else
                                         "an AOPA-only entity" if lids.get(k) else "no entity" for k in m_only)
        L += [f"- NASR: their {len(theirs_ap):,} unmatched INSTR/RNTL airports (no school in *their* merged list, AOPA rows "
              f"included) vs my {len(mine_ap):,} (no school in ours): {len(theirs_ap & mine_ap):,} in both. Of their {len(t_only):,} "
              f"not in mine, {sum(1 for k in t_only if k in listed):,} have a school listed there in our list and "
              f"{sum(1 for k in t_only if k in inferred and k not in listed):,} an inferred one. Of my {len(m_only):,} not in theirs, "
              "their list has at the airport: " + ", ".join(f"{k} {v:,}" for k, v in m_only_cls.most_common()) + ".",
              f"- My {len(gap):,} gap airports with an unmatched entity of theirs on the field: "
              + ", ".join(f"{c_} {sum(1 for g in gap if any(cls(r) == c_ and r['verdict'] == 'new' for r in at_lid.get(g['key'], [])))}"
                          for c_ in ("Tach & Go", "BestAviation, no Tach & Go", "AOPA only")) + ".", ""]

    # ---------------- staleness
    def closed_share(labs):
        lab = [x for x in labs if x]
        return sum(1 for x in lab if x["verdict"] == "closed"), len(lab)
    L += ["## Staleness: closed businesses that lists still carry", "",
          "- **Diamond Aviation, San Carlos (KSQL)**: the other researcher found it on AOPA's per-airport business list and not "
          "on ours. David confirms it is closed. Google Places still labels \"Diamond Aviation Maintenance\" at the airport as an "
          "operating aircraft-maintenance company. Our list has six schools at San Carlos; leaving it off was correct.",
          f"- **Tach & Go** possibly-new listings with a website: {sum(1 for r in tg_new if r['site'] in ('down', 'parked', 'closed_text'))} of "
          f"{sum(1 for r in tg_new if r['website'])} homepages are down, parked or say they closed "
          f"({sc_.get('down', 0)} down, {sc_.get('parked', 0)} parked, {sc_.get('closed_text', 0)} closed text); "
          f"hand check: {closed_share(tg_lab)[0]} of {closed_share(tg_lab)[1]} closed. The directory says each listing was checked "
          "(\"Last checked October 2026\").",
          f"- **PPP** (loans of 2020–21): hand check {closed_share(pp_lab)[0]} of {closed_share(pp_lab)[1]} closed, and "
          f"{sum(1 for x in pp_lab if x and x['verdict'] == 'unclear')} more left no web trace at all.",
          f"- **NASR**: {sum(1 for g in gap if g['last_info'] and g['last_info'][:4] < '2020')} gap airports last answered the FAA "
          f"before 2020, so their flags may be old; hand check {closed_share(nasr_lab)[0]} of {closed_share(nasr_lab)[1]} closed.", ""]

    # ---------------- what each adds + headline
    adds = [("NASR", len(gap), nasr_new, len(nasr_sample)), ("Tach & Go", len(tg_new), tg_k, len(tg_sample)),
            ("PPP", len(pp_new), pp_k, len(pp_sample))]
    from difflib import SequenceMatcher
    tgv = [(r["state"], m.variants(r["name"])) for r in tg_new]
    xover = 0
    for b in pp_new:
        bv = m.variants(b["name"])
        xover += any(st_ == b["state"] and (set(bv) & set(tv) or max((SequenceMatcher(None, x, y).ratio()
                     for x in bv for y in tv), default=0) >= 0.9) for st_, tv in tgv)
    L += ["## What each would add to the 5,458", "",
          "| Source | Entries | Matched to our list | Possibly new | Hand-check precision | Estimated new operations |", "|---|---|---|---|---|---|",
          f"| NASR INSTR/RNTL airports | {len(tr_ap):,} airports | {len(covered):,} | {len(gap):,} airports | "
          f"{nasr_new}/{len(nasr_sample)} strict, {nasr_new + nasr_weak} with weak (strong-signal airports) | {nasr_new}–{nasr_new + nasr_weak} found by hand; airports are leads, not schools |",
          f"| Tach & Go (training-relevant) | {len(rel_rows):,} | {conf:,} | {len(tg_new):,} | {tg_k}/{len(tg_sample)} strict, {tg_w}/{len(tg_sample)} with weak | "
          f"~{round(len(tg_new) * tg_k / max(1, len(tg_sample))):,} ({round(len(tg_new) * lo_):,}–{round(len(tg_new) * hi_):,}); "
          f"~{round(len(tg_new) * tg_w / max(1, len(tg_sample))):,} with weak |",
          f"| PPP NAICS 611512 (organizations) | {len(org):,} | {sum(b['confirmed'] for b in org):,} | {len(pp_new):,} | {pp_k}/{len(pp_sample)} | "
          f"~{round(len(pp_new) * pp_k / max(1, len(pp_sample))):,} ({round(len(pp_new) * plo):,}–{round(len(pp_new) * phi):,}) |", "",
          f"The estimates barely overlap: {xover} of the unmatched PPP organizations are also unmatched Tach & Go listings "
          "(same state, same or nearly the same name). They are not de-duplicated against the outside master list's additions.", "",
          "Nothing here has been added to `flight_line.json`. Candidates are in `docs/hackers-2026-gap-candidates.csv` "
          "(organizations only), the gap airports in `docs/hackers-2026-gap-airports.csv`.", ""]
    est = {"Tach & Go": round(len(tg_new) * tg_k / max(1, len(tg_sample))), "PPP": round(len(pp_new) * pp_k / max(1, len(pp_sample)))}
    mis = vc.get("in_list_other_airport", 0)
    tg_west = round(len(tg_new) * tg_w / max(1, len(tg_sample)))
    headline = (f"> **Headline.** Together the three point to about {est['Tach & Go'] + est['PPP'] + nasr_new:,} training "
                f"operations missing from our {len(fl):,} on strict evidence (about {tg_west + est['PPP'] + nasr_new + nasr_weak:,} "
                f"counting weak evidence), most of them from Tach & Go. Tach & Go: {conf:,} of its {len(rel_rows):,} "
                f"training-relevant listings are already in our list; of the {len(tg_new):,} that are not, a hand check finds "
                f"{tg_k} of {len(tg_sample)} to be real training operations we lack on their own evidence (another {tg_w - tg_k} only "
                f"by an AirNav checkbox or bare listing), so about {est['Tach & Go']:,} "
                f"({round(len(tg_new) * lo_):,}–{round(len(tg_new) * hi_):,}). PPP NAICS 611512: {len(pp_new):,} unmatched "
                f"organizations, {pp_k} of {len(pp_sample)} new and operating, so about {est['PPP']:,}. NASR: {len(gap):,} airports "
                f"flag instruction or rental with no school of ours, but at {mis} of the {len(nasr_sample)} with the strongest ADS-B "
                f"training signal the school *is* in our list, filed under the wrong airport or none; {nasr_new} were new ({nasr_weak} more on weak evidence).\n\n"
                f"> **Correction to a working assumption.** The NASR gap measures our airport assignments as much as our coverage: "
                f"wrong or missing `locations` (KPWT for Weatherford TX, KAVL for Greenville NC, Tucson only for Epic Flight Academy's "
                f"New Smyrna Beach main base) hide schools we already have.")
    L[head_idx:head_idx] = [headline, ""]
    Path(a.md).write_text("\n".join(L) + "\n")

    # ---------------- CSVs
    with open(a.airports_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["airport_id", "name", "city", "state", "site_type", "use", "instr", "rntl", "last_info_response",
                    "school_fleet_tails_based", "adsb_based_aircraft", "flagged_not_on_fleet", "strong_not_on_fleet",
                    "nearest_school_airport_nm", "tachgo_listings", "tachgo_listing_in_our_list", "our_schools_with_address_in_city", "hand_check"])
        for g in sorted(gap, key=lambda g: (g["state"], g["airport_id"])):
            lab = labels.get(("nasr", g["airport_id"]))
            w.writerow([g["airport_id"], g["name"], g["city"], g["state"], g["site_type"], g["use"], int(g["instr"]), int(g["rntl"]),
                        g["last_info"], g["school_tails"], g["based_adsb"], g["flagged_new"], g["strong_new"], g["nearest_school_nm"],
                        "; ".join(g["tachgo"]), "; ".join(g["tachgo_in_list"]), "; ".join(g["addr_schools"]), (lab or {}).get("verdict", "")])
    with open(a.candidates_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source", "id", "name", "city", "state", "website", "evidence", "verdict"])
        for r in sorted(tg_new, key=lambda r: r["id"]):
            lab = labels.get(("tachgo", r["id"]))
            ev = f"{tg_kind(r)} at {r['airport_id']}; homepage {r['site']}; matcher {r['match_type']}"
            if r["match_type"] != "none" and r.get("school_domain"):
                ev += f" (nearest: {r['school_domain']})"
            w.writerow(["tachgo", r["id"], r["name"], r["city"], r["state"], r["website"], ev,
                        (lab or {}).get("verdict", "unchecked")])
        for b in sorted(pp_new, key=lambda b: b["id"]):
            lab = labels.get(("ppp", b["id"]))
            ev = (f"PPP NAICS 611512, {b['loans']} loan(s), {b['btype']}; aircraft registered in this name: {b['reg_aircraft']} "
                  f"({b['reg_flagged']} training-flagged); matcher {b['match_type']}")
            w.writerow(["ppp", b["id"], b["name"].title(), b["city"], b["state"], (lab or {}).get("website", ""), ev,
                        (lab or {}).get("verdict", "unchecked")])
        for g in sorted(nasr_sample, key=lambda g: g["airport_id"]):
            lab = labels.get(("nasr", g["airport_id"]))
            if lab and lab.get("org"):
                w.writerow(["nasr", g["airport_id"], lab["org"], g["city"], g["state"], lab.get("website", ""),
                            f"NASR {'INSTR' if g['instr'] else ''}{'/RNTL' if g['rntl'] else ''} at {g['airport_id']}; "
                            f"{g['strong_new']} strong new aircraft", lab["verdict"]])

    # ---------------- worksheet for hand checks (git-ignored)
    near = collections.defaultdict(list)
    for s in fl:
        for l in (s.get("locations") or []) + (s.get("inferred_locations") or []):
            if l.get("airport_id"):
                near[key(l["airport_id"])].append(s["name"])
    ws = {"tachgo": [dict(id=r["id"], name=r["name"], kind=tg_kind(r), airport=r["airport_id"], town=r["town_full"] or r["town"],
                          state=r["state"], website=r["website"], site=r["site"], match=r["match_type"], nearest=r.get("school_name"),
                          schools_at_airport=sorted(set(near.get(key(r["airport_id"]), [])))) for r in tg_sample],
          "ppp": [dict(id=b["id"], name=b["name"], city=b["city"], state=b["state"], btype=b["btype"], reg_aircraft=b["reg_aircraft"],
                       reg_flagged=b["reg_flagged"], match=b["match_type"], nearest=b["school_name"]) for b in pp_sample],
          "nasr": [dict(id=g["airport_id"], name=g["name"], city=g["city"], state=g["state"], strong=g["strong_new"],
                        school_tails=g["school_tail_domains"], tachgo=g["tachgo"], tachgo_in_list=g["tachgo_in_list"],
                        addr_schools=g["addr_schools"]) for g in nasr_sample]}
    (raw / "hand_check_worksheet.json").write_text(json.dumps(ws, indent=1))
    print(headline)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch-tachgo"); f.add_argument("--raw", default=str(RAW)); f.add_argument("--delay", type=float, default=4.0)
    p = sub.add_parser("ppp-filter"); p.add_argument("--raw", default=str(RAW))
    r = sub.add_parser("report")
    r.add_argument("--raw", default=str(RAW))
    r.add_argument("--release", required=True, help="release directory with aircraft.parquet, training.json, training_scores.parquet")
    r.add_argument("--check-sites", action="store_true", help="fetch homepages not yet in the cache")
    r.add_argument("--external", help="directory with another agent's c_master_matched.csv and unmatched_faa_instr_airports.csv (counts only)")
    r.add_argument("--labels", default=str(REPO / "docs" / "hackers-2026-gap-labels.csv"))
    r.add_argument("--md", default=str(REPO / "docs" / "hackers-2026-gap-sources.md"))
    r.add_argument("--candidates-csv", default=str(REPO / "docs" / "hackers-2026-gap-candidates.csv"))
    r.add_argument("--airports-csv", default=str(REPO / "docs" / "hackers-2026-gap-airports.csv"))
    a = ap.parse_args()
    {"fetch-tachgo": fetch_tachgo, "ppp-filter": ppp_filter, "report": report}[a.cmd](a)


if __name__ == "__main__":
    main()
