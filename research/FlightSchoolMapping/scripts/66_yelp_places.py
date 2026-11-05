"""Find flight-instruction businesses through Yelp's Places API and compare them with our school list.

Yelp's Business Search takes a point and a radius (max 40 km) and a category; `flightinstruction` is
Yelp's "Flight Instruction" category (Specialty Schools). The trial key allows 300 calls a day
(RateLimit-DailyLimit header, checked 2026-11-05), so instead of tiling the whole country this
targets where training happens: the airports of every school in flight_line.json (listed and
inferred) plus the places Google labelled as flight training, covered greedily by 38 km circles,
densest first. Each run spends at most --max-calls (default 290) and resumes where it stopped.

Yelp returns name, phone, address and coordinates, but not the business's own website. Matching
is by phone (strongest; we have phones from schools' own pages), then name and address with the
state-list matcher plus 59's strict confirmation. Yelp content other than business IDs and Yelp
page URLs stays in the git-ignored data dir; details for anything we add come from its own site.

  python scripts/66_yelp_places.py plan            # build the circle list
  python scripts/66_yelp_places.py search          # spend today's calls
  python scripts/66_yelp_places.py match           # compare with flight_line
"""
import argparse, collections, csv, importlib.util, json, math, os, re, subprocess, sys, time
import urllib.parse, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT.parent.parent
OUT = ROOT / "data" / "research-2026-11" / "yelp"
API = "https://api.yelp.com/v3/businesses/search"
R_KM = 38.0  # circle radius used for planning; the API call asks for 40 km
CAT = "flightinstruction"


def key():
    k = os.environ.get("YELP_API_KEY")
    if k:
        return k
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True, cwd=HERE).stdout.strip()
    for line in (Path(common).parent / ".env").read_text().splitlines():
        if line.startswith("YELP_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    sys.exit("YELP_API_KEY not set (environment or the main checkout's .env)")


def km(a, b):
    la = math.radians((a[0] + b[0]) / 2)
    return math.hypot((a[0] - b[0]) * 111.2, (a[1] - b[1]) * 111.2 * math.cos(la))


def airport_coords():
    pos = {}
    with open(ROOT / "data" / "ourairports_airports.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["iso_country"] != "US":
                continue
            ll = (float(r["latitude_deg"]), float(r["longitude_deg"]))
            for c in (r["ident"], r["gps_code"], r["local_code"]):
                if c:
                    pos.setdefault(c.upper(), ll)
                    if len(c) == 3:
                        pos.setdefault("K" + c.upper(), ll)
    return pos


def plan(a):
    """Training points: school airports (listed + inferred) and Google flight-training places;
    greedy cover with R_KM circles centred on training points, most points first."""
    OUT.mkdir(parents=True, exist_ok=True)
    pos = airport_coords()
    pts = []
    for s in json.loads((ROOT / "output" / "flight_line.json").read_text()):
        ids = [l.get("airport_id") for l in s.get("locations") or []] + \
              [x["airport_id"] for x in s.get("inferred_locations") or []]
        for i in ids:
            if i and i.upper() in pos:
                pts.append(pos[i.upper()])
    gdet = ROOT / "data" / "research-2026-11" / "google" / "details.json"
    if gdet.exists():  # Google working data, used only to decide where to look (purged by 2026-12-03)
        spec = importlib.util.spec_from_file_location("g", HERE / "59_google_places.py")
        g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(g)
        for d in json.loads(gdet.read_text()).values():
            if g.is_school(d):
                pts.append((d["location"]["latitude"], d["location"]["longitude"]))
    pts = [(round(la, 3), round(lo, 3)) for la, lo in pts]
    weight = collections.Counter(pts)
    uncovered = dict(weight)
    circles = []
    cand = list(weight)
    # bucket candidates on a coarse grid to keep the greedy step fast
    grid = collections.defaultdict(list)
    for p in cand:
        grid[(int(p[0] * 2), int(p[1] * 2))].append(p)

    def near(c):
        gx, gy = int(c[0] * 2), int(c[1] * 2)
        for dx in (-1, 0, 1):
            for dy in (-2, -1, 0, 1, 2):
                for p in grid.get((gx + dx, gy + dy), []):
                    if p in uncovered and km(c, p) <= R_KM:
                        yield p
    gain = {c: sum(uncovered[p] for p in near(c)) for c in cand}
    while uncovered:
        c = max(gain, key=gain.get)
        covered = list(near(c))
        if not covered:
            gain.pop(c)
            continue
        circles.append({"lat": c[0], "lon": c[1], "points": sum(uncovered[p] for p in covered)})
        for p in covered:
            uncovered.pop(p, None)
        for p in list(gain):
            if km(c, p) <= 2 * R_KM:
                gain[p] = sum(uncovered[q] for q in near(p))
    (OUT / "circles.json").write_text(json.dumps(circles))
    print(f"plan: {sum(weight.values()):,} training points at {len(weight):,} places -> {len(circles):,} circles "
          f"(first 300 cover {sum(c['points'] for c in circles[:300]):,} points, "
          f"first 900 {sum(c['points'] for c in circles[:900]):,})")


def search(a):
    circles = json.loads((OUT / "circles.json").read_text())
    cache_p = OUT / "search_cache.json"
    cache = json.loads(cache_p.read_text()) if cache_p.exists() else {}
    k, calls = key(), 0
    for i, c in enumerate(circles):
        ck = f"{c['lat']},{c['lon']}"
        if ck in cache:
            continue
        found, offset, total = [], 0, None
        while True:
            if calls >= a.max_calls:
                break
            q = urllib.parse.urlencode({"categories": CAT, "latitude": c["lat"], "longitude": c["lon"],
                                        "radius": 40000, "limit": 50, "offset": offset})
            req = urllib.request.Request(f"{API}?{q}", headers={"Authorization": "Bearer " + k})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    d = json.load(r)
                    left = r.headers.get("RateLimit-Remaining")
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "ignore")[:200]
                if e.code == 429:
                    print(f"search: daily limit reached after {calls} calls ({body})")
                    cache_p.write_text(json.dumps(cache))
                    return
                raise SystemExit(f"HTTP {e.code}: {body}")
            calls += 1
            total = d.get("total", 0)
            found += d.get("businesses", [])
            offset += 50
            if offset >= min(total, 240):
                break
            time.sleep(0.3)
        if calls >= a.max_calls and (total is None or offset < min(total or 0, 240)):
            break
        cache[ck] = {"total": total, "businesses": found}
        if i % 25 == 0:
            cache_p.write_text(json.dumps(cache))
            print(f"  circle {i + 1}/{len(circles)}, {calls} calls, Yelp says {left} left today", flush=True)
        time.sleep(0.3)
    cache_p.write_text(json.dumps(cache))
    biz = {b["id"]: b for v in cache.values() for b in v["businesses"]}
    print(f"search: {calls} calls this run; {len(cache):,} of {len(circles):,} circles done; "
          f"{len(biz):,} distinct businesses so far")


def digits(p):
    d = re.sub(r"\D", "", p or "")
    return d[-10:] if len(d) >= 10 else ""


def match(a):
    cache = json.loads((OUT / "search_cache.json").read_text())
    biz = {b["id"]: b for v in cache.values() for b in v["businesses"] if not b.get("is_closed")}
    fl = json.loads((ROOT / "output" / "flight_line.json").read_text())
    by_phone = {}
    for s in fl:
        for p in ((s.get("public_contact") or {}).get("phone", {}).get("value"), (s.get("contact") or {}).get("phone")):
            if digits(p):
                by_phone.setdefault(digits(p), s)
    rows, phone_hits = [], {}
    for i, b in biz.items():
        loc = b.get("location") or {}
        if loc.get("country") not in (None, "", "US"):
            continue
        hit = by_phone.get(digits(b.get("phone")))
        if hit:
            phone_hits[i] = hit
        rows.append({"source": "c_yelp", "name": b["name"], "city": loc.get("city", ""), "state": loc.get("state", ""),
                     "website": "", "address": ", ".join(x for x in loc.get("display_address") or [] if x),
                     "yelp_id": i, "yelp_url": b.get("url", "").split("?")[0], "phone": b.get("phone", ""),
                     "categories": "|".join(c["alias"] for c in b.get("categories") or [])})
    path = OUT / "c_yelp.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    ap = f"{ROOT / 'output' / 'airports.csv'},{ROOT / 'data' / 'ourairports_airports.csv'}"
    p = subprocess.run([sys.executable, "-I", str(HERE / "probes" / "match_external_school_lists.py"),
                        str(ROOT / "output" / "flight_line.json"), ap, str(OUT), str(path)],
                       capture_output=True, text=True, check=True)
    spec = importlib.util.spec_from_file_location("g", HERE / "59_google_places.py")
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    with open(OUT / "c_yelp_matched.csv") as f:
        m = list(csv.DictReader(f))
    for r in m:
        if r["yelp_id"] in phone_hits:
            s = phone_hits[r["yelp_id"]]
            r["match_type"], r["school_name"], r["school_domain"], r["verdict"] = "phone", s.get("name", ""), s.get("domain", ""), "ours"
        else:
            r["verdict"] = "ours" if g.confirmed_match(r) else "new"
    with open(OUT / "c_yelp_matched.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(m[0].keys()))
        w.writeheader()
        w.writerows(m)
    v = collections.Counter(r["verdict"] for r in m)
    print(f"match: {len(m):,} open US businesses; ours {v['ours']:,} (phone {len(phone_hits):,}), new {v['new']:,}; "
          f"matcher: {p.stdout.strip().splitlines()[-1]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["plan", "search", "match"])
    ap.add_argument("--max-calls", type=int, default=290, help="calls to spend this run (trial: 300 a day)")
    a = ap.parse_args()
    {"plan": plan, "search": search, "match": match}[a.step](a)


if __name__ == "__main__":
    main()
