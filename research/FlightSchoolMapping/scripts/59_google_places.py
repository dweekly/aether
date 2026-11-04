"""Find flight schools through Google Places (New) and compare them with our school list.

Places has no flight-school type, so this runs text searches ("flight school", "flight training",
"flying club", ...) restricted to rectangles that tile the US. Text Search with an IDs-only field mask
is free with no monthly cap (Google Maps Platform pricing, checked 2026-11-04), and returns at most 60
results per query; any rectangle that returns 60 is split in four and searched again.

Paid steps print their call count and cost estimate first and stop unless --max-usd covers it:
  details   Place Details Pro (display name, Maps category label, address, status):
            5,000 free a month, then $17 per 1,000.
  websites  Place Details Enterprise (websiteUri) only for flight schools we could not match:
            1,000 free a month, then $20 per 1,000.
Free caps are per billing account per month, so other use of the same account eats into them.

Google's terms let us keep place IDs; the other fields are working data for matching and stay in the
git-ignored data dir. The output is a list of leads, each confirmed from the school's own website
before it goes anywhere.

  export GOOGLE_PLACES_API_KEY=...   (or put it in the main checkout's .env; never in the repo)
  python scripts/59_google_places.py search   [--out data/research-2026-11/google]
  python scripts/59_google_places.py details  --max-usd 0
  python scripts/59_google_places.py match
  python scripts/59_google_places.py websites --max-usd 0
  python scripts/59_google_places.py report   [--md docs/hackers-2026-google-places.md]
"""
import argparse, collections, csv, importlib.util, json, math, os, re, subprocess, sys, time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT.parent.parent
API = "https://places.googleapis.com/v1"
QUERIES = ["flight school", "flight training", "flying club", "flight instruction", "aviation academy",
           "flight lessons", "helicopter flight training", "glider rides and instruction"]
# Lower 48 in 4x4 degree starting tiles, plus Alaska, Hawaii and Puerto Rico.
START = ([(la, lo, la + 4, lo + 4) for la in range(24, 50, 4) for lo in range(-125, -65, 4)]
         + [(51, -180, 72, -129), (18.5, -161, 22.5, -154), (17.8, -67.5, 18.6, -65.2)])
MIN_SIDE = 0.05  # degrees; below this a saturated tile is recorded, not split
PRICES = {"details": (5000, 17.0), "websites": (1000, 20.0)}  # free per month, USD per 1,000 after
DETAIL_FIELDS = ("id,displayName,formattedAddress,addressComponents,location,types,primaryType,"
                 "primaryTypeDisplayName,googleMapsTypeLabel,businessStatus")
SCHOOL_LABEL = re.compile(r"flight school|flying club|aero ?club|flight instructor|aviation training|pilot school|"
                          r"aircraft rental|\bglider|(?<!hang )(?<!para)gliding|soaring", re.I)
US_CODES = {"US", "PR", "VI", "GU", "AS", "MP"}  # Google gives the territories their own country codes


def api_key():
    k = os.environ.get("GOOGLE_PLACES_API_KEY")
    if k:
        return k
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True, cwd=HERE).stdout.strip()
    env = Path(common).parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("GOOGLE_PLACES_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    sys.exit("GOOGLE_PLACES_API_KEY is not set (environment or the main checkout's .env)")


def call(method, path, key, mask, body=None, tries=5):
    data = json.dumps(body).encode() if body is not None else None
    for i in range(tries):
        req = urllib.request.Request(API + path, data=data, method=method, headers={
            "Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": mask})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")[:300]
            if e.code in (429, 500, 503) and i < tries - 1:
                time.sleep(2 ** i)
                continue
            raise SystemExit(f"HTTP {e.code} on {path}: {msg}")


def search(a):
    key, out = api_key(), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cache_p = out / "search_cache.json"
    cache = json.loads(cache_p.read_text()) if cache_p.exists() else {}
    found = collections.defaultdict(set)  # place id -> queries
    calls = saturated_floor = 0
    for q in QUERIES:
        stack = list(START)
        while stack:
            t = stack.pop()
            ck = f"{q}|{t[0]:.4f},{t[1]:.4f},{t[2]:.4f},{t[3]:.4f}"
            if ck not in cache:
                ids, token = [], None
                for _ in range(3):  # 3 pages of 20 = the 60-result maximum
                    body = {"textQuery": q, "pageSize": 20, "regionCode": "US",
                            "locationRestriction": {"rectangle": {"low": {"latitude": t[0], "longitude": t[1]},
                                                                  "high": {"latitude": t[2], "longitude": t[3]}}}}
                    if token:
                        body["pageToken"] = token
                    r = call("POST", "/places:searchText", key, "places.id,nextPageToken", body)
                    calls += 1
                    ids += [p["id"] for p in r.get("places", [])]
                    token = r.get("nextPageToken")
                    if not token:
                        break
                    time.sleep(0.3)
                cache[ck] = ids
                if calls % 50 == 0:
                    cache_p.write_text(json.dumps(cache))
                    print(f"  {calls} calls, {len(found)} places so far", flush=True)
            ids = cache[ck]
            for i in ids:
                found[i].add(q)
            if len(ids) >= 60:
                if min(t[2] - t[0], t[3] - t[1]) / 2 < MIN_SIDE:
                    saturated_floor += 1
                    continue
                mla, mlo = (t[0] + t[2]) / 2, (t[1] + t[3]) / 2
                stack += [(t[0], t[1], mla, mlo), (t[0], mlo, mla, t[3]), (mla, t[1], t[2], mlo), (mla, mlo, t[2], t[3])]
    cache_p.write_text(json.dumps(cache))
    (out / "place_ids.json").write_text(json.dumps({k: sorted(v) for k, v in found.items()}, indent=0))
    print(f"search: {calls} new calls (free, IDs only), {len(cache)} tiles cached, {len(found)} distinct places, "
          f"{saturated_floor} tiles still saturated at the minimum size")


def ledger(out, step=None, add=0):
    """Paid calls made per SKU per calendar month, so estimates count what this month already used."""
    p = Path(out) / "billing_ledger.json"
    L = json.loads(p.read_text()) if p.exists() else {}
    month = time.strftime("%Y-%m")
    if step:
        L.setdefault(month, {}).setdefault(step, 0)
        L[month][step] += add
        p.write_text(json.dumps(L, indent=1))
    return L.get(month, {})


def estimate(step, n, out):
    free, per_k = PRICES[step]
    used = ledger(out).get(step, 0)
    usd = (max(0, used + n - free) - max(0, used - free)) * per_k / 1000
    print(f"{step}: {n:,} calls; {used:,} already this month, {free:,} free a month, then ${per_k:.0f}/1,000 "
          f"-> about ${usd:,.2f} (calls made outside this script on the same billing account are not counted)")
    return usd


def details(a):
    out = Path(a.out)
    allids = json.loads((out / "place_ids.json").read_text())
    import random
    many = sorted(i for i, q in allids.items() if len(q) >= a.min_queries)
    rest = sorted(i for i, q in allids.items() if len(q) < a.min_queries)
    sample = random.Random(a.seed).sample(rest, min(a.sample_rest, len(rest)))
    if (out / "details_selection.json").exists() and not (out / "details_selection_pass1.json").exists():
        (out / "details_selection.json").rename(out / "details_selection_pass1.json")
    (out / "details_selection.json").write_text(json.dumps(
        {"min_queries": a.min_queries, "seed": a.seed, "many": len(many), "rest": len(rest), "sample": sample}))
    ids = many + sample
    print(f"details for {len(many):,} places found by >= {a.min_queries} queries and a random {len(sample):,} "
          f"of the other {len(rest):,}")
    det_p = out / "details.json"
    det = json.loads(det_p.read_text()) if det_p.exists() else {}
    todo = [i for i in ids if i not in det]
    if estimate("details", len(todo), out) > a.max_usd:
        sys.exit(f"stopping: over --max-usd {a.max_usd}")
    key = api_key()
    for n, i in enumerate(todo, 1):
        det[i] = call("GET", f"/places/{i}", key, DETAIL_FIELDS)
        ledger(out, "details", 1)
        if n % 200 == 0:
            det_p.write_text(json.dumps(det))
            print(f"  {n}/{len(todo)}", flush=True)
        time.sleep(0.05)
    det_p.write_text(json.dumps(det))
    labels = collections.Counter((d.get("googleMapsTypeLabel") or {}).get("text", "") for d in det.values())
    print(f"details: {len(det):,} places; top labels {labels.most_common(15)}")


def comp(d, kind, short=True):
    for c in d.get("addressComponents", []):
        if kind in c.get("types", []):
            return c.get("shortText" if short else "longText", "")
    return ""


def is_school(d):
    label = " ".join([(d.get("googleMapsTypeLabel") or {}).get("text", ""),
                      (d.get("primaryTypeDisplayName") or {}).get("text", "")])
    return bool(SCHOOL_LABEL.search(label)) and d.get("businessStatus", "OPERATIONAL") == "OPERATIONAL" \
        and comp(d, "country") in US_CODES


_MX = None


def _mx():
    global _MX
    if _MX is None:
        spec = importlib.util.spec_from_file_location("mx", HERE / "probes" / "match_external_school_lists.py")
        _MX = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_MX)
    return _MX


def confirmed_match(r):
    """The shared matcher's name, address and fuzzy rules are too loose for Google listings (hand check of
    28, 2026-11-04: 1 of 8 exact, 2 of 8 address and 5 of 12 fuzzy were different businesses). Keep a match
    if the websites agree, or if the names share a distinctive word and are nearly the same: similarity
    >= 0.8, or one squashed name inside the other."""
    if r["match_type"] == "domain":
        return True
    if r["match_type"] not in ("exact", "address", "fuzzy"):
        return False
    from difflib import SequenceMatcher
    mx = _mx()

    generic = mx.GENERIC - {"north", "south", "east", "west", "central", "northern", "southern", "eastern",
                            "western", "county", "state", "international"}

    def norm(n):
        n = re.sub(r"\.(com|org|net|aero)\b", " ", (n or "").lower().replace("&", " and "))
        n = re.sub(mx.LEGAL, " ", re.sub(r"[^a-z0-9 ]+", " ", n))
        return n.split()

    a, b = norm(r["name"]), norm(r["school_name"])
    sa, sb = "".join(a), "".join(b)
    if sa and sa == sb:  # the same name, even when every word is generic ("The Flight School, Inc.")
        return True
    da, db = set(a) - generic, set(b) - generic
    if not da or not db:
        return False
    # every distinctive word of one name appears in the other (squashed, so FlyRight = Fly Right)
    if not (all(t in sb for t in da) or all(t in sa for t in db)):
        return False
    short, long_ = sorted((sa, sb), key=len)
    return SequenceMatcher(None, sa, sb).ratio() >= 0.8 or (len(short) >= 6 and short in long_)


def run_matcher(rows, out, name):
    path = out / f"{name}.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["source", "name", "city", "state", "website", "address", "place_id", "label"])
        w.writeheader()
        w.writerows(rows)
    ap = f"{ROOT / 'output' / 'airports.csv'},{ROOT / 'data' / 'ourairports_airports.csv'}"
    p = subprocess.run([sys.executable, "-I", str(HERE / "probes" / "match_external_school_lists.py"),
                        str(ROOT / "output" / "flight_line.json"), ap, str(out), str(path)],
                       capture_output=True, text=True, check=True)
    with open(out / f"{name}_matched.csv") as f:
        return p.stdout.strip().splitlines()[-1], list(csv.DictReader(f))


def match(a):
    out = Path(a.out)
    det = json.loads((out / "details.json").read_text())
    web = json.loads((out / "websites.json").read_text()) if (out / "websites.json").exists() else {}
    rows = []
    for i, d in det.items():
        if not is_school(d):
            continue
        rows.append({"source": "c_google", "name": (d.get("displayName") or {}).get("text", ""),
                     "city": comp(d, "locality", False) or comp(d, "postal_town", False),
                     "state": comp(d, "administrative_area_level_1"), "website": web.get(i, ""),
                     "address": d.get("formattedAddress", ""), "place_id": i,
                     "label": (d.get("googleMapsTypeLabel") or {}).get("text", "")})
    summary, matched = run_matcher(rows, out, "c_google")
    print(f"match: {len(rows):,} operating US places labelled as flight training; {summary}")


def school_airport_distance(out):
    """km from each fetched place to the nearest airport where flight_line.json lists a school."""
    ap = {}
    with open(ROOT / "output" / "airports.csv") as f:
        for r in csv.DictReader(f):
            try:
                p = (float(r["latitude"]), float(r["longitude"]))
            except (KeyError, ValueError):
                continue
            for k in (r.get("airport_id"), r.get("faa_id"), r.get("gps_code")):
                if k:
                    ap.setdefault(k.upper(), p)
    fl = json.loads((ROOT / "output" / "flight_line.json").read_text())
    pts = set()
    for s_ in (fl if isinstance(fl, list) else fl.values()):
        for loc in s_.get("locations") or []:
            k = (loc.get("airport_id") or "").upper()
            p = ap.get(k) or ap.get("K" + k)
            if p:
                pts.add(p)
    if not pts:
        sys.exit("no school airports resolved from airports.csv; cannot measure distance")
    det = json.loads((out / "details.json").read_text())
    out_d = {}
    for i, d in det.items():
        la, lo = d["location"]["latitude"], d["location"]["longitude"]
        c = math.cos(math.radians(la))
        out_d[i] = min(math.hypot((la - a_) * 111.2, (lo - b_) * 111.2 * c) for a_, b_ in pts)
    return out_d


def websites(a):
    out = Path(a.out)
    with open(out / "c_google_matched.csv") as f:
        un = [r for r in csv.DictReader(f) if r["match_type"] in ("none", "colocated")]
    web_p = out / "websites.json"
    web = json.loads(web_p.read_text()) if web_p.exists() else {}
    if a.skip_near_km:
        dist = school_airport_distance(out)
        near = [r for r in un if dist.get(r["place_id"], 1e9) <= a.skip_near_km]
        un = [r for r in un if dist.get(r["place_id"], 1e9) > a.skip_near_km]
        print(f"skipping {len(near):,} unmatched places within {a.skip_near_km} km of an airport where we list a school")
    todo = [r["place_id"] for r in un if r["place_id"] not in web]
    if estimate("websites", len(todo), out) > a.max_usd:
        sys.exit(f"stopping: over --max-usd {a.max_usd}")
    key = api_key()
    for n, i in enumerate(todo, 1):
        web[i] = call("GET", f"/places/{i}", key, "websiteUri").get("websiteUri", "")
        ledger(out, "websites", 1)
        if n % 200 == 0:
            web_p.write_text(json.dumps(web))
    web_p.write_text(json.dumps(web))
    print(f"websites: {sum(1 for v in web.values() if v):,} of {len(web):,} unmatched places list a website; "
          f"re-run `match` to use them for domain matching")


# Places whose label and homepage pass the checks but are not flight training in our sense (found by
# reading a random 30 of the first 315, 2026-11-04): model aircraft, foot-launched flying, simulator
# and ground-only training, schools for dispatchers or high schoolers, and directory or social pages.
NOT_OURS = re.compile(r"\brc\b|r/c|radio control|model (aircraft|aviation|airplane)|\bama\b|flying site|"
                      r"paraglid|paramotor|hang ?glid|powered parachute|\bdrone|\buas\b|"
                      r"simulat|\bsim\b|ftiratings|afgsim|dispatch|high school|\bhs\b|"
                      r"facebook\.com|instagram\.com|modelaircraft\.org|yelp\.com|linktr\.ee", re.I)


def report(a):
    out = Path(a.out)
    det = json.loads((out / "details.json").read_text())
    ids = json.loads((out / "place_ids.json").read_text())
    with open(out / "c_google_matched.csv") as f:
        m = list(csv.DictReader(f))
    spec = importlib.util.spec_from_file_location("fsai", HERE / "56_fsai_directory.py")
    fsai = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fsai)
    cache_p = out / "site_checks.json"
    cache = json.loads(cache_p.read_text()) if cache_p.exists() else {}
    MATCHED = ("domain", "exact", "address", "fuzzy")
    for r in m:
        r["confirmed"] = confirmed_match(r)
    new = [r for r in m if not r["confirmed"]]
    loose = sum(1 for r in m if r["match_type"] in MATCHED and not r["confirmed"])
    for r in new:
        r["site"] = fsai.check_site(r["website"], cache) if r["website"] else {}
    cache_p.write_text(json.dumps(cache, indent=1))
    passed = [r for r in new if r["site"].get("training")]
    excluded = [r for r in passed if NOT_OURS.search(f"{r['name']} {r['website']} {r['site'].get('final', '')}")]
    seen, confirmed = set(), []
    for r in passed:
        if r in excluded:
            continue
        dom = re.sub(r"^https?://(www\.)?", "", (r["site"].get("final") or r["website"]).lower()).split("/")[0]
        if dom in seen:
            continue
        seen.add(dom)
        confirmed.append(r)
    labels = collections.Counter((d.get("googleMapsTypeLabel") or {}).get("text", "") for d in det.values())
    mt = collections.Counter(r["match_type"] for r in m)
    L = ["# Google Places vs our flight-school list", "",
         "Generated by `research/FlightSchoolMapping/scripts/59_google_places.py`; do not edit by hand.", "",
         f"> **Headline.** Text searches tiling the US found {len(ids):,} places; {len(m):,} are operating US places "
         f"Google labels as flight training. {sum(r['confirmed'] for r in m):,} match a school in our list "
         f"(website, or a near-identical name: {loose:,} looser name or address matches were set aside as unmatched); of the "
         f"{len(new):,} that don't, {len(passed):,} have a live website that talks about flight training; "
         f"after dropping {len(excluded):,} model-aircraft, paragliding, simulator, high-school and social-page "
         f"entries and collapsing repeated websites, {len(confirmed):,} remain.", "",
         "## Method", "",
         f"- Queries: {', '.join(QUERIES)}. Each runs over rectangles tiling the US, split in four whenever a "
         "rectangle returns Google's 60-result maximum. IDs-only text search is free.",
         "- Place Details Pro for every place found (name, Maps category label, address, status); a place counts "
         f"as flight training when its label matches `{SCHOOL_LABEL.pattern}`.",
         "- Matched against `output/flight_line.json` with `probes/match_external_school_lists.py`; websites "
         "(Place Details Enterprise) fetched only for unmatched places, then matched again by domain.",
         "- Each unmatched place's website checked with `56_fsai_directory.check_site` (live, mentions flight "
         "training). Google content other than place IDs stays out of the repo.", "",
         "## Maps category labels of everything found", "", "| Label | Places |", "|---|---|"]
    L += [f"| {k or '(none)'} | {n:,} |" for k, n in labels.most_common(30)]
    sel_p = out / "details_selection_pass1.json"
    if not sel_p.exists():
        sel_p = out / "details_selection.json"
    if sel_p.exists():
        sel = json.loads(sel_p.read_text())
        smp = [det[i] for i in sel["sample"] if i in det]
        k = sum(is_school(d) for d in smp)
        L += ["", "## What skipping single-query places misses", "",
              f"Details were fetched for the {sel['many']:,} places found by at least {sel['min_queries']} queries "
              f"and a random {len(smp):,} of the other {sel['rest']:,} (seed {sel['seed']}). {k} of those "
              f"{len(smp):,} ({k / max(1, len(smp)):.1%}) are labelled as flight training, so about "
              f"{round(k / max(1, len(smp)) * (sel['rest'] - len(smp))):,} more such places sit among the "
              f"{sel['rest'] - len(smp):,} not fetched."]
    L += ["", "## Matching", "", "| Match rule | Places |", "|---|---|"]
    L += [f"| {k} | {mt[k]:,} |" for k in ("domain", "exact", "address", "fuzzy", "colocated", "none")]
    smp_p = REPO / "docs" / "hackers-2026-google-sample.csv"
    if smp_p.exists():
        with open(smp_p) as f:
            js = collections.Counter(r["judgement"] for r in csv.DictReader(f))
        n_s, k_s = sum(js.values()), js["training"]
        z = 1.96; ph = k_s / n_s; den = 1 + z * z / n_s
        mid = (ph + z * z / (2 * n_s)) / den; half = z * math.sqrt(ph * (1 - ph) / n_s + z * z / (4 * n_s * n_s)) / den
        L += ["", "## Hand check", "",
              f"A random {n_s} of the {len(confirmed):,} (drawn with Python's random.seed(99) from the list as "
              f"first generated, sorted by state and name) were read by hand "
              f"(`docs/hackers-2026-google-sample.csv`): {k_s} are flight training (schools, clubs, soaring "
              f"clubs and independent instructors), {js['not']} are not, {js['unclear']} could not be read. "
              f"Precision {ph:.0%} (95% Wilson {mid - half:.0%} to {mid + half:.0%}), so about "
              f"{round(ph * len(confirmed)):,} of the {len(confirmed):,} ({round((mid - half) * len(confirmed)):,} to "
              f"{round((mid + half) * len(confirmed)):,}) are flight-training operations we did not have. "
              "Several are one instructor's business, Part 61 instruction that no directory lists."]
    def dom(r):
        return re.sub(r"^https?://(www\.)?", "", (r["site"].get("final") or r["website"]).lower()).split("/")[0]
    L += ["", f"## {len(confirmed):,} confirmed, not in our list", "",
          "Listed by the website each was checked on, which we fetched ourselves; Google's names and "
          "addresses for them stay in the ignored data dir (`c_google_matched.csv`).", ""]
    L += [f"- {d}" for d in sorted(dom(r) for r in confirmed)]
    Path(a.md).write_text("\n".join(L) + "\n")
    print(f"report: {len(confirmed)} confirmed new -> {a.md}")


def chart(a):
    """Backdrop: every rectangle searched, for all queries (finer where Google's 60-result cap forced
    splits, drawn brighter), with a dot for each place labelled as flight training. Lower 48 only."""
    out = Path(a.out)
    spec = importlib.util.spec_from_file_location("bd", HERE / "53_talk_backdrops.py")
    bd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bd)
    import matplotlib.patches as mp
    bd.setup(Path(a.fonts))
    cache = json.loads((out / "search_cache.json").read_text())
    det = json.loads((out / "details.json").read_text())
    tiles = {tuple(map(float, k.split("|", 1)[1].split(","))) for k in cache}
    conus = [t for t in tiles if t[0] >= 24 and t[2] <= 50 and t[1] >= -125 and t[3] <= -65]
    sch = [d["location"] for d in det.values() if is_school(d)]
    k = math.cos(math.radians(38))
    fig = bd.frame("Searching Google Maps one rectangle at a time",
                   f"{len(cache):,} searches over {len(QUERIES)} phrases; a rectangle with 60 results (the cap) splits in four")
    ax = fig.add_axes([0.04, 0.10, 0.92, 0.72])
    for la0, lo0, la1, lo1 in sorted(conus, key=lambda t: -(t[2] - t[0])):
        side = la1 - la0
        col, lw, al = ((bd.GRID, 1.0, 1.0) if side >= 4 else (bd.MUTED, 0.8, 0.6) if side >= 1 else (bd.BUFF, 0.6, 0.8))
        ax.add_patch(mp.Rectangle((lo0 * k, la0), (lo1 - lo0) * k, side, fill=False, edgecolor=col, linewidth=lw, alpha=al))
    pts = [(p["latitude"], p["longitude"]) for p in sch if 24 <= p["latitude"] <= 50 and -125 <= p["longitude"] <= -65]
    ax.scatter([lo * k for la, lo in pts], [la for la, lo in pts], s=9, color=bd.AMBER, alpha=0.75, linewidth=0, zorder=3)
    ax.set_xlim(-125 * k, -65 * k)
    ax.set_ylim(24, 50)
    ax.set_aspect("equal")
    ax.axis("off")
    bd.footer(fig, f"{len(tiles):,} distinct rectangles; {len(pts):,} dots, places Google labels as flight training. "
                   "Searches return IDs only, which is free.")
    dest = Path(a.chart_out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest)
    print(f"chart: {len(tiles):,} tiles, {len(pts):,} dots -> {dest}")


def placeids(a):
    """Our schools that a Google place matched, with the place ID (which Google's terms let us keep),
    for joining website, AOPA id, place ID and fleet. Several places can match one school (branches)."""
    out = Path(a.out)
    with open(out / "c_google_matched.csv") as f:
        m = [r for r in csv.DictReader(f) if confirmed_match(r)]
    rows = sorted(({"school_name": r["school_name"], "school_domain": r["school_domain"], "place_id": r["place_id"],
                    "match_type": r["match_type"], "match_score": float(r["match_score"])} for r in m),
                  key=lambda r: (r["school_domain"], r["school_name"], r["place_id"]))
    dest = ROOT / "output" / "google_place_ids.json"
    dest.write_text(json.dumps({"source": "Google Places API (New), text search + Place Details, 2026-11-04",
                                "note": "place IDs only; match_type from probes/match_external_school_lists.py",
                                "matches": rows}, indent=1) + "\n")
    schools = {(r["school_domain"], r["school_name"]) for r in rows}
    print(f"placeids: {len(rows):,} place matches for {len(schools):,} schools "
          f"({dict(collections.Counter(r['match_type'] for r in rows))}) -> {dest}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["search", "details", "match", "websites", "report", "chart", "placeids"])
    ap.add_argument("--out", default=str(ROOT / "data" / "research-2026-11" / "google"))
    ap.add_argument("--max-usd", type=float, default=0.0, help="refuse a paid step whose estimate exceeds this")
    ap.add_argument("--md", default=str(REPO / "docs" / "hackers-2026-google-places.md"))
    ap.add_argument("--min-queries", type=int, default=1, help="details only for places found by this many queries")
    ap.add_argument("--sample-rest", type=int, default=0, help="plus a random sample of this many of the others")
    ap.add_argument("--seed", type=int, default=20261104)
    ap.add_argument("--skip-near-km", type=float, default=0.0,
                    help="websites: skip unmatched places this close to an airport where we list a school")
    ap.add_argument("--fonts", help="chart: directory with the deck's TTFs (Archivo Narrow, JetBrains Mono)")
    ap.add_argument("--chart-query", default="flight school")
    ap.add_argument("--chart-out", default=str(REPO / "docs" / "hackers-2026-charts" / "backdrops" / "google_tiles.png"))
    ap.add_argument("--ipv4", action="store_true",
                    help="connect over IPv4 only (for a key restricted to an IPv4 address)")
    a = ap.parse_args()
    if a.ipv4:
        import socket
        orig = socket.getaddrinfo
        socket.getaddrinfo = lambda *x, **k: [r for r in orig(*x, **k) if r[0] == socket.AF_INET]
    {"search": search, "details": details, "match": match, "websites": websites, "report": report,
     "chart": chart, "placeids": placeids}[a.step](a)


if __name__ == "__main__":
    main()
