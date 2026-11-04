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
SCHOOL_LABEL = re.compile(r"flight school|flying club|flight instructor|aviation training|pilot school|"
                          r"glider|gliding|soaring|helicopter tour and charter", re.I)


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


def estimate(step, n):
    free, per_k = PRICES[step]
    usd = max(0, n - free) * per_k / 1000
    print(f"{step}: {n:,} calls; {free:,} free a month, then ${per_k:.0f}/1,000 -> about ${usd:,.2f} "
          f"(more if this billing account already used its free calls this month)")
    return usd


def details(a):
    out = Path(a.out)
    ids = json.loads((out / "place_ids.json").read_text())
    det_p = out / "details.json"
    det = json.loads(det_p.read_text()) if det_p.exists() else {}
    todo = [i for i in ids if i not in det]
    if estimate("details", len(todo)) > a.max_usd:
        sys.exit(f"stopping: over --max-usd {a.max_usd}")
    key = api_key()
    for n, i in enumerate(todo, 1):
        det[i] = call("GET", f"/places/{i}", key, DETAIL_FIELDS)
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
        and comp(d, "country") == "US"


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


def websites(a):
    out = Path(a.out)
    with open(out / "c_google_matched.csv") as f:
        un = [r for r in csv.DictReader(f) if r["match_type"] in ("none", "colocated")]
    web_p = out / "websites.json"
    web = json.loads(web_p.read_text()) if web_p.exists() else {}
    todo = [r["place_id"] for r in un if r["place_id"] not in web]
    if estimate("websites", len(todo)) > a.max_usd:
        sys.exit(f"stopping: over --max-usd {a.max_usd}")
    key = api_key()
    for n, i in enumerate(todo, 1):
        web[i] = call("GET", f"/places/{i}", key, "websiteUri").get("websiteUri", "")
        if n % 200 == 0:
            web_p.write_text(json.dumps(web))
    web_p.write_text(json.dumps(web))
    print(f"websites: {sum(1 for v in web.values() if v):,} of {len(web):,} unmatched places list a website; "
          f"re-run `match` to use them for domain matching")


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
    new = [r for r in m if r["match_type"] not in MATCHED]
    for r in new:
        r["site"] = fsai.check_site(r["website"], cache) if r["website"] else {}
    cache_p.write_text(json.dumps(cache, indent=1))
    confirmed = [r for r in new if r["site"].get("training")]
    labels = collections.Counter((d.get("googleMapsTypeLabel") or {}).get("text", "") for d in det.values())
    mt = collections.Counter(r["match_type"] for r in m)
    L = ["# Google Places vs our flight-school list", "",
         "Generated by `research/FlightSchoolMapping/scripts/59_google_places.py`; do not edit by hand.", "",
         f"> **Headline.** Text searches tiling the US found {len(ids):,} places; {len(m):,} are operating US places "
         f"Google labels as flight training. {sum(mt[k] for k in MATCHED):,} match a school in our list; of the "
         f"{len(new):,} that don't, {len(confirmed):,} have a live website that talks about flight training.", "",
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
    L += ["", "## Matching", "", "| Match rule | Places |", "|---|---|"]
    L += [f"| {k} | {mt[k]:,} |" for k in ("domain", "exact", "address", "fuzzy", "colocated", "none")]
    L += ["", f"## {len(confirmed):,} confirmed, not in our list", "", "| Name | City | State | Website |", "|---|---|---|---|"]
    L += [f"| {r['name']} | {r['city']} | {r['state']} | {r['website']} |" for r in sorted(confirmed, key=lambda r: (r["state"], r["name"]))]
    Path(a.md).write_text("\n".join(L) + "\n")
    print(f"report: {len(confirmed)} confirmed new -> {a.md}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["search", "details", "match", "websites", "report"])
    ap.add_argument("--out", default=str(ROOT / "data" / "research-2026-11" / "google"))
    ap.add_argument("--max-usd", type=float, default=0.0, help="refuse a paid step whose estimate exceeds this")
    ap.add_argument("--md", default=str(REPO / "docs" / "hackers-2026-google-places.md"))
    ap.add_argument("--ipv4", action="store_true",
                    help="connect over IPv4 only (for a key restricted to an IPv4 address)")
    a = ap.parse_args()
    if a.ipv4:
        import socket
        orig = socket.getaddrinfo
        socket.getaddrinfo = lambda *x, **k: [r for r in orig(*x, **k) if r[0] == socket.AF_INET]
    {"search": search, "details": details, "match": match, "websites": websites, "report": report}[a.step](a)


if __name__ == "__main__":
    main()
