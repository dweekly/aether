#!/usr/bin/env python3
"""Flight schools found through Google Places that we lacked: classify, dedupe, add.

59_google_places.py found 771 websites of places Google labels as flight training that match no
school in output/flight_line.json. A hand check of 30 found only 53-73% are flight training we
lacked. This script crawls each site, has Claude Haiku 5.5 classify it from its own pages, dedupes
strictly against flight_line.json, and adds the confirmed schools through 57_added_schools.py's
pipeline (source "google_places", provenance: place ID and date).

Google's Maps Platform terms let us keep place IDs indefinitely and Places coordinates for 30 days,
nothing else. Google's names, addresses, phones, labels, coordinates and websiteUri are used here only
as working data to find and check candidates; they stay in the git-ignored data dir
(data/research-2026-11/google/) and never reach flight_line.json, the release or any committed file.
A school's name, address, phone, hours and website come from its own pages.

Steps, from research/FlightSchoolMapping/ (working files in data/research-2026-11/google/schools/):
  candidates   59's 771 candidates (recomputed exactly as its report does) -> candidates.json,
               targets.json for 09a (named by domain: no Google name goes into a crawl or prompt)
  (crawl)      09a_fleet_crawl.py --targets targets.json --out-dir crawl/, then
               10_fleet_browser_crawl.py --crawl-only --no-evasion on the thin ones (browser_targets)
  browser-targets  the crawled sites whose HTTP pages are thin, flagged or missing -> browser_targets.json
  classify-build   one request per crawled site: is it flight instruction in manned aircraft, what kind
               of operation, and its name, street address, phone and hours as written -> classify/
  estimate / submit / fetch   Message Batches, claude-haiku-5-5 (run dir layout as extract_v2.py)
  classify-report  parse, verify every returned value verbatim on its page -> classified.json
  sample       a seeded random N of the "yes" set, with the page text to read by hand
  dedupe       strict dedupe against flight_line.json -> docs/hackers-2026-google-schools.csv
               (hand decisions for ambiguous pairs are in HAND below)
  ingest       "added" rows -> output/flight_schools_enriched.json (source google_places) and the
               extraction inputs (crawl index and browser text restricted to the added sites)

Usage:
  60_google_schools.py candidates
  60_google_schools.py browser-targets
  60_google_schools.py classify-build
  60_google_schools.py estimate|submit|fetch --run data/research-2026-11/google/schools/classify
  60_google_schools.py classify-report
  60_google_schools.py sample --n 30 --seed 20261105
  60_google_schools.py dedupe
  60_google_schools.py ingest
"""

import argparse
import csv
import importlib.util
import json
import random
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
REPO = ROOT.parent.parent
OUT = ROOT / "output"
G = ROOT / "data" / "research-2026-11" / "google"
W = G / "schools"
CRAWL = W / "crawl"
DISPOSITIONS = REPO / "docs" / "hackers-2026-google-schools.csv"
ENRICHED = OUT / "flight_schools_enriched.json"
SOURCE = "google_places"
PULLED = "2026-11-04"  # Place Details and the homepage checks (59), all on this day
sys.path.insert(0, str(SCRIPTS))


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def site_dom(url):
    return re.sub(r"^https?://(www\.)?", "", (url or "").lower()).split("/")[0]


def cmd_candidates(a):
    m59 = _load("g59", "59_google_places.py")
    fsai = _load("fsai56", "56_fsai_directory.py")
    with open(G / "c_google_matched.csv") as f:
        m = list(csv.DictReader(f))
    cache = json.loads((G / "site_checks.json").read_text())
    n_cached = len(cache)
    new = [r for r in m if not m59.confirmed_match(r)]
    for r in new:
        r["site"] = fsai.check_site(r["website"], cache) if r["website"] else {}
    if len(cache) != n_cached:
        sys.exit(f"FAIL {len(cache) - n_cached} websites were not in 59's homepage cache; run 59 report first")
    passed = [r for r in new if r["site"].get("training")]
    excluded = [r for r in passed if m59.NOT_OURS.search(f"{r['name']} {r['website']} {r['site'].get('final', '')}")]
    ex_ids = {r["place_id"] for r in excluded}
    seen, confirmed = set(), []
    for r in passed:  # same order and rule as 59's report
        if r["place_id"] in ex_ids:
            continue
        dom = site_dom(r["site"].get("final") or r["website"])
        if dom in seen:
            continue
        seen.add(dom)
        confirmed.append(r)
    branches = defaultdict(list)  # every unmatched place whose website lands on the same site
    for r in new:
        if r["website"]:
            branches[site_dom(r["site"].get("final") or r["website"])].append(r["place_id"])
    out = []
    for r in confirmed:
        dom = site_dom(r["site"].get("final") or r["website"])
        out.append({"candidate_id": r["place_id"], "place_ids": sorted(set(branches[dom])), "site": dom,
                    "url": r["site"].get("final") or r["website"],
                    # Google working data, for dedupe only; never written to a committed file
                    "g_name": r["name"], "g_address": r["address"], "g_city": r["city"], "g_state": r["state"]})
    W.mkdir(parents=True, exist_ok=True)
    (W / "candidates.json").write_text(json.dumps(out, indent=1) + "\n")
    targets = [{"name": c["site"], "website": c["url"], "airport_code": "", "city": "", "state_name": ""} for c in out]
    (W / "targets.json").write_text(json.dumps(targets, indent=1) + "\n")
    print(f"{len(out)} candidates ({sum(len(c['place_ids']) for c in out)} place IDs) -> {W / 'candidates.json'}; "
          f"targets.json for 09a")
    if len(out) != 771:
        sys.exit(f"FAIL expected 59's 771 candidates, got {len(out)}")


def cmd_browser_targets(a):
    """Sites to render: no HTTP pages, a refusal or challenge, thin pages, or a home page with under 600
    characters of visible text (script-built sites). 10 runs with --no-evasion: an ordinary browser,
    nothing that gets past a bot check."""
    import page_text as pt
    cands = json.loads((W / "candidates.json").read_text())
    idx = {site_dom(s["school_website"]): s for s in json.loads((CRAWL / "fleet_crawl_index.json").read_text())}
    pages = pt.http_pages(CRAWL / "fleet_crawl_index.json", CRAWL / "fleet_html_raw", "google-http", site_dom)
    out, why = [], Counter()
    for c in cands:
        s = idx.get(c["site"])
        flags = set((s or {}).get("crawl_flags") or [])
        home = [p for p in pages.get(c["site"], []) if p["type"] == "home"]
        home_chars = len(pt.read_page(home[0])) if home else 0
        reason = ("no_http_pages" if not s or not s.get("pages") else
                  "refused_or_challenge" if any(f.startswith("http_") or f == "captcha_challenge" for f in flags) else
                  "thin" if flags & {"all_pages_thin", "fleet_pages_thin"} else
                  "thin_home" if home_chars < 600 else "")
        if reason:
            why[reason] += 1
            out.append({"school_name": c["site"], "school_website": c["url"], "known_airport": "", "city": "",
                        "state": ""})
    (W / "browser_targets.json").write_text(json.dumps(out, indent=1) + "\n")
    print(f"{len(out)} browser targets {dict(why)} -> {W / 'browser_targets.json'}")

CLASSIFY_QUESTIONS = """\
Business website: {site}

Answer these questions from the web pages below.

- flight_instruction: does this business itself give flight instruction (lessons toward a pilot certificate or \
rating, or dual instruction) in manned aircraft: airplanes, helicopters, gliders, gyroplanes, balloons, or \
light-sport aircraft (including weight-shift trikes and powered parachutes)? "yes", "no" or "unclear".
  Answer "no" for: paramotor, paraglider, hang glider or other foot-launched flying; drones or UAS; \
simulator-only training (type ratings or simulator sessions with no flying in an aircraft); ground school or \
test prep only; charter, tours, sightseeing or rides with no training program; aircraft sales, maintenance, FBO \
services or avionics; software, apps, directories, associations, publications, or a business that lists or \
refers to schools run by others; skydiving.
  A flight school, flying club, college or university flight program, or independent flight instructor that \
teaches in aircraft is "yes", even if it also does charter, maintenance or FBO services. "unclear" only if the \
pages are too thin or off-topic to decide.
- operation_type: "flight_school", "flying_club", "university_program" (a college or university \
flight-training program), "independent_instructor" (one or a few instructors teaching under their own name, \
often in the student's own aircraft), or "none" when flight_instruction is not "yes".
- not_training_kind: when flight_instruction is "no" or "unclear", what the business is in a few words (for \
example "charter operator", "FBO", "maintenance shop", "paramotor school", "simulator training", "drone \
school", "directory", "software", "tour operator", "skydiving", "airport"). Otherwise "".
- aircraft: the kinds of aircraft it teaches in. Empty when none.
- evidence: one short quote, copied exactly from the pages, that best supports your flight_instruction answer, \
and evidence_url.
- airports: airport identifiers (such as KPAO or 1C5) or airport names the pages give as where it operates, \
each copied exactly. Empty when none.
{contact}

Web pages:
{pages}"""

CLASS_PROPS = {
    "flight_instruction": {"type": "string", "enum": ["yes", "no", "unclear"]},
    "operation_type": {"type": "string", "enum": ["flight_school", "flying_club", "university_program",
                                                  "independent_instructor", "none"]},
    "not_training_kind": {"type": "string"},
    "aircraft": {"type": "array", "items": {"type": "string", "enum": [
        "airplane", "helicopter", "glider", "gyroplane", "balloon", "light_sport", "powered_parachute",
        "weight_shift"]}},
    "evidence": {"type": "string"}, "evidence_url": {"type": "string"},
    "airports": {"type": "array", "items": {"type": "string"}},
}
CAPS = {"home": 12000, "location": 5000, "*": 4000}
MAX_CHARS = 36000
CLASSIFY_RUN = W / "classify"


def candidate_pages(pt):
    """site -> its crawled pages: 09a's (raw HTML) and 10's (rendered), home first, one copy per URL
    (the longer text when both crawls have it)."""
    http = pt.http_pages(CRAWL / "fleet_crawl_index.json", CRAWL / "fleet_html_raw", "http", site_dom)
    brw = pt.browser_pages(CRAWL / "fleet_browser_index.json", CRAWL / "fleet_browser_html_raw",
                           CRAWL / "fleet_browser_text", "browser", site_dom)
    order = {"home": 0, "location": 1, "about": 1}
    out = {}
    for site in set(http) | set(brw):
        best = {}
        for pg in http.get(site, []) + brw.get(site, []):
            pg["text"] = pt.read_page(pg)
            k = pg["url"].split("#")[0].rstrip("/").lower()
            if k not in best or len(pg["text"]) > len(best[k]["text"]):
                best[k] = pg
        out[site] = sorted(best.values(), key=lambda p: (order.get(p["type"], 2), p["url"]))
    return out


def cmd_classify_build(a):
    import haiku_batch as hb
    import page_text as pt
    cands = json.loads((W / "candidates.json").read_text())
    pages = candidate_pages(pt)
    reqs, index, none = [], [], []
    for i, c in enumerate(cands):
        pgs, total, keep = pages.get(c["site"], []), 0, []
        for pg in pgs:
            if total >= MAX_CHARS:
                break
            keep.append(pg)
            total += min(len(pg["text"]), CAPS.get(pg["type"], CAPS["*"]))
        block, sent = pt.pages_block(keep, CAPS)
        if not sent:
            none.append(c["site"])
            continue
        cid = f"g{i:04d}"
        user = CLASSIFY_QUESTIONS.format(site=c["site"], contact=pt.CONTACT_QUESTIONS, pages=block)
        reqs.append({"custom_id": cid, "params": pt.params(user, {**CLASS_PROPS, **pt.CONTACT_PROPS}, "medium")})
        index.append({"custom_id": cid, "candidate_id": c["candidate_id"], "site": c["site"],
                      "pages": [{"url": p["url"], "path": p["path"], "kind": p["kind"], "type": p["type"],
                                 "retrieved": p["retrieved"], "cache": p["cache"]} for p in keep if p["url"] in dict(sent)]})
    (W / "no_pages.json").write_text(json.dumps(none, indent=1) + "\n")
    hb.write_run(CLASSIFY_RUN, reqs, index, {"what": "60 classify", "prompt": CLASSIFY_QUESTIONS, "caps": CAPS,
                                             "max_chars": MAX_CHARS})
    print(f"{len(reqs)} requests; {len(none)} candidates with no page text -> no_pages.json")


def sent_pages(pt, entry):
    """Rebuild exactly the page texts a request carried, for the verbatim check."""
    pgs = []
    for p in entry["pages"]:
        p = dict(p)
        p["text"] = pt.read_page(p)
        pgs.append(p)
    return pt.pages_block(pgs, CAPS)[1]


def cmd_classify_report(a):
    import haiku_batch as hb
    import page_text as pt
    index = json.loads((CLASSIFY_RUN / "index.json").read_text())
    ans = hb.answers(CLASSIFY_RUN)
    out, stats = [], Counter()
    for e in index:
        x = ans.get(e["custom_id"])
        rec = {"candidate_id": e["candidate_id"], "site": e["site"], "custom_id": e["custom_id"]}
        if x is None:
            rec["flight_instruction"] = "failed"
            stats["failed"] += 1
            out.append(rec)
            continue
        sent = sent_pages(pt, e)
        ev_ref, ev_how = pt.verify(x["evidence"], x["evidence_url"], sent)
        rec.update({k: x[k] for k in ("flight_instruction", "operation_type", "not_training_kind", "aircraft")})
        rec["evidence"] = {"quote": x["evidence"], "ref": ev_ref or "", "how": ev_how}
        rec["airports"] = [ap for ap in x["airports"] if any(pt.on_page(ap, t) for _, t in sent)]
        rec["contact"] = pt.verify_contact(x, sent)
        rec["retrieved"] = {p["url"]: p["retrieved"] for p in e["pages"]}
        stats[x["flight_instruction"]] += 1
        stats["evidence_" + ev_how] += 1
        for f in ("name", "phone", "street_address", "hours"):
            stats[f"{f}_{rec['contact'][f]['how']}"] += 1
        out.append(rec)
    (W / "classified.json").write_text(json.dumps(out, indent=1) + "\n")
    yes = [r for r in out if r["flight_instruction"] == "yes"]
    print(f"{len(out)} classified -> classified.json: {dict(sorted(stats.items()))}")
    print("operation types of yes:", dict(Counter(r["operation_type"] for r in yes)))
    print("not-training kinds:", Counter(r.get("not_training_kind", "").lower() for r in out
                                         if r["flight_instruction"] == "no").most_common(25))


def cmd_sample(a):
    """A seeded random N of the "yes" set, each with the model's answer and the page text it read, for
    reading by hand. Judgements go in docs/hackers-2026-google-classifier-check.csv."""
    import page_text as pt
    cls = json.loads((W / "classified.json").read_text())
    index = {e["custom_id"]: e for e in json.loads((CLASSIFY_RUN / "index.json").read_text())}
    yes = sorted((r for r in cls if r["flight_instruction"] == "yes"), key=lambda r: r["site"])
    smp = random.Random(a.seed).sample(yes, min(a.n, len(yes)))
    with open(W / f"sample_{a.seed}.txt", "w") as f:
        for r in smp:
            f.write(f"\n{'=' * 100}\n{r['site']}  type={r['operation_type']} aircraft={r['aircraft']}\n"
                    f"evidence ({r['evidence']['how']}): {r['evidence']['quote']}\n")
            for url, text in sent_pages(pt, index[r["custom_id"]]):
                f.write(f"--- {url}\n{text[:a.chars]}\n")
    print("\n".join(r["site"] for r in smp))
    print(f"-> {W / f'sample_{a.seed}.txt'}")


# ── Dedupe ───────────────────────────────────────────────────────

PROD = Path("/mnt/data/home/dew/dev/aether/research/FlightSchoolMapping")  # read-only: the November recrawl
from page_text import STATES  # noqa: E402
ABBR = set(STATES.values())

# Hand decisions on the ambiguous pairs `dedupe` prints (site -> (disposition, detail)). Every entry was
# read against both sites or records; see docs/hackers-2026-google-schools.md.
HAND = {}


def state_of(addr):
    a = (addr or "").strip()
    m = re.search(r"\b([A-Z]{2})\b[\s,]*(\d{5})?(-\d{4})?[\s,]*(USA|United States)?\s*$", a)
    if m and m.group(1) in ABBR:
        return m.group(1)
    low = a.lower()
    for name, ab in sorted(STATES.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{name}\b", low):
            return ab
    m = re.search(r"\b([A-Z]{2})\b", a)
    return m.group(1) if m and m.group(1) in ABBR else ""


def street_key(addr):
    """(number, first street word, ZIP-or-'') of a street address, or None."""
    m = re.search(r"\b(\d{1,6})\s+([A-Za-z0-9]+)", addr or "")
    if not m:
        return None
    z = re.search(r"\b(\d{5})(?:-\d{4})?\b(?!.*\d{5})", addr)
    return (m.group(1), m.group(2).lower(), z.group(1) if z else "")


def _mx():
    return _load("g59b", "59_google_places.py")._mx()


def name_tokens(n, mx):
    n = re.sub(r"\.(com|org|net|aero)\b", " ", (n or "").lower().replace("&", " and "))
    return re.sub(mx.LEGAL, " ", re.sub(r"[^a-z0-9 ]+", " ", n)).split()


def name_match(a, b, mx, generic):
    """59's rule for a confirmed name match: the same squashed name, or every distinctive word of one in the
    other and similarity >= 0.8 (or one squashed name inside the other). Returns (strict, loose_score)."""
    ta, tb = name_tokens(a, mx), name_tokens(b, mx)
    sa, sb = "".join(ta), "".join(tb)
    if not sa or not sb:
        return False, 0.0
    ratio = SequenceMatcher(None, sa, sb).ratio()
    if sa == sb:
        return True, 1.0
    da, db = set(ta) - generic, set(tb) - generic
    if not da or not db:
        return False, ratio if (da & db) else 0.0
    shared = bool(set(ta) & set(tb) & (da | db))
    if not (all(t in sb for t in da) or all(t in sa for t in db)):
        return False, ratio if shared else 0.0
    short, long_ = sorted((sa, sb), key=len)
    return (ratio >= 0.8 or (len(short) >= 6 and short in long_)), ratio


def our_records():
    """Every school in flight_line.json with the keys dedupe compares: domain and the domains it redirects
    to (March and November crawls), name, state, street key."""
    fl = json.loads((OUT / "flight_line.json").read_text())
    build = _load("build21", "21_build_flight_line.py")
    nd = build.normalize_domain
    redirects = defaultdict(set)
    for p in [OUT / "fleet_crawl_index.json", OUT / "fleet_browser_index.json",
              PROD / "data/research-2026-11/recrawl/fleet_crawl_index.json",
              PROD / "data/research-2026-11/recrawl/fleet_browser_index.json"]:
        if not p.exists():
            print(f"  note: {p} missing; its redirects are not used")
            continue
        for x in json.loads(p.read_text()):
            d = nd(x.get("school_website", ""))
            for k in ("redirect_domain", "final_url", "canonical_url"):
                t = nd(x.get(k) or "")
                if d and t and t != d:
                    redirects[d].add(t)
    recs = []
    for s in fl:
        c = s.get("contact") or {}
        addr = c.get("address", "")
        recs.append({"key": s["domain"] or f"{s['name']} ({addr})", "domain": s["domain"],
                     "domains": ({s["domain"]} | redirects.get(s["domain"], set()) | ({nd(c.get("website", ""))} - {""}))
                     if s["domain"] else ({nd(c.get("website", ""))} - {""}),
                     "name": s["name"], "state": state_of(addr), "street": street_key(addr), "address": addr,
                     "added": bool(s.get("provenance"))})
    return recs, nd


def cmd_dedupe(a):
    mx = _mx()
    generic = mx.GENERIC - {"north", "south", "east", "west", "central", "northern", "southern", "eastern",
                            "western", "county", "state", "international"}
    cands = {c["candidate_id"]: c for c in json.loads((W / "candidates.json").read_text())}
    cls = {r["candidate_id"]: r for r in json.loads((W / "classified.json").read_text())}
    ours, nd = our_records()
    by_key = {o["domain"] or o["name"]: o for o in ours}
    by_domain = defaultdict(list)
    for o in ours:
        for d in o["domains"]:
            by_domain[d].append(o)
    final = defaultdict(set)  # candidate site -> domains it lands on in our crawl
    for line in (CRAWL / "crawl_attempts_http.jsonl").open():
        x = json.loads(line)
        for k in ("final_domain", "redirect_domain"):
            if x.get(k):
                final[site_dom(x["website"])].add(nd(x[k]))
    for p in (CRAWL / "fleet_crawl_index.json", CRAWL / "fleet_browser_index.json"):
        for x in json.loads(p.read_text()):
            for k in ("redirect_domain", "final_url", "canonical_url"):
                if x.get(k):
                    final[site_dom(x["school_website"])].add(nd(x[k]))
    # Google working data: places matched to one of our schools, by street key, to catch a candidate at the
    # same address as a place we already have (spartaaero.com is our Sparta Aero Services at KSAR).
    with open(G / "c_google_matched.csv") as f:
        gm = list(csv.DictReader(f))
    m59 = _load("g59c", "59_google_places.py")
    ours_at = defaultdict(set)
    for r in gm:
        if m59.confirmed_match(r) and street_key(r["address"]) and street_key(r["address"])[2]:
            ours_at[street_key(r["address"])].add(r["school_domain"] or r["school_name"])
    # For pairs at one street address with no shared name word (common: several businesses on one
    # airport road), the pair is ambiguous only if it could be a rename: the candidate's pages name our
    # school (its distinctive name words run together, or its domain's first label).
    loose_generic = generic | {"operations", "flyers", "aviators", "aviator", "adventures", "wings", "sky", "skies",
                               "flights", "instructor", "pilots", "jet", "jets", "rental", "rentals", "charter"}
    import page_text as pt
    cand_text = {}
    for site, pgs in candidate_pages(pt).items():
        cand_text[site] = pt.norm(" ".join(p["text"] for p in pgs)).replace(" ", "")

    def could_be_rename(c, o):
        squashed = "".join(t for t in name_tokens(o["name"], mx) if t not in generic)
        root = (o["domain"] or "").split(".")[0].replace("-", "")
        txt = cand_text.get(c["site"], "")
        return (len(squashed) >= 5 and squashed in txt) or (len(root) >= 5 and root in txt)
    rows, ambiguous = [], []
    seen_new = {}
    for cid, c in cands.items():
        r = cls.get(cid, {"flight_instruction": "no_pages"})
        ct = r.get("contact") or {}
        site_name = (ct.get("name") or {}).get("value", "")
        cs = ct.get("city_state") or {}
        site_state = cs.get("state", "")
        st = site_state or c["g_state"]
        doms = {c["site"], nd(c["url"])} | final.get(c["site"], set())
        doms.discard("")
        row = {"candidate_id": cid, "place_ids": ";".join(c["place_ids"]), "site": c["site"],
               "classification": r["flight_instruction"], "operation_type": r.get("operation_type", ""),
               "aircraft": ";".join(r.get("aircraft") or []), "not_training_kind": r.get("not_training_kind", ""),
               "evidence_url": (r.get("evidence") or {}).get("ref", ""), "school_name": site_name,
               "city": cs.get("city", ""), "state": site_state, "disposition": "", "school_key": "", "detail": ""}
        hits = []
        for d in doms:
            for o in by_domain.get(d, []):
                hits.append((o, f"domain {d}"))
        names = [n for n in (site_name, c["g_name"]) if n]
        streets = {k for k in (street_key((ct.get("street_address") or {}).get("value", "")),
                               street_key(c["g_address"])) if k}
        amb = []
        for o in ours:
            if any(h[0] is o for h in hits):
                continue
            for n in names:
                strict, score = name_match(n, o["name"], mx, generic)
                same_state = st and o["state"] == st
                if strict and same_state:
                    hits.append((o, f"name in {st}"))
                    break
                loose_shared = set(name_tokens(n, mx)) & set(name_tokens(o["name"], mx)) - loose_generic
                if (strict and not o["state"]) or (same_state and score >= 0.6 and loose_shared):
                    amb.append((o, f"name {score:.2f} ({'state unknown' if not o['state'] else st})"))
                    break
            if o["street"] and o["street"][2] and o["street"] in streets:
                shared = any(set(name_tokens(n, mx)) & set(name_tokens(o["name"], mx)) - generic for n in names)
                if shared:
                    hits.append((o, "street address and a name word"))
                elif could_be_rename(c, o):
                    amb.append((o, "same street address; could be a rename"))
        for k in streets:
            for who in ours_at.get(k, ()):
                o = by_key.get(who)
                if o is None or any(h[0] is o for h in hits):
                    continue
                if any(set(name_tokens(n, mx)) & set(name_tokens(o["name"], mx)) - generic for n in names):
                    hits.append((o, "at the address of a Google place matched to it, sharing a name word"))
                elif could_be_rename(c, o):
                    amb.append((o, "at the address of a Google place matched to it; could be a rename"))
        if c["site"] in HAND:
            row["disposition"], row["detail"] = HAND[c["site"]]
        elif r["flight_instruction"] != "yes":
            row["disposition"] = "rejected"
            row["detail"] = {"no": f"not flight training: {r.get('not_training_kind', '')}",
                             "unclear": f"unclear from its pages ({r.get('not_training_kind', '')})",
                             "failed": "classification request failed",
                             "no_pages": "no page text (site down, refused or a challenge)"}[r["flight_instruction"]]
        elif hits:
            o, why = hits[0]
            row["disposition"] = "duplicate"
            row["detail"] = f"{o['key']} ({why}){' - an earlier added school' if o['added'] else ''}"
        else:
            row["disposition"] = "added"
            for o, why in amb:
                ambiguous.append((c["site"], site_name, st, o["key"], o["name"], o["state"], why))
        rows.append(row)
    # candidates that are the same school as another candidate (two domains, same name and state)
    added = [x for x in rows if x["disposition"] == "added"]
    for i, x in enumerate(added):
        for y in added[i + 1:]:
            if x["state"] and x["state"] == y["state"] and name_match(x["school_name"], y["school_name"], mx, generic)[0]:
                ambiguous.append((x["site"], x["school_name"], x["state"], y["site"], y["school_name"], y["state"],
                                  "two candidates"))
    for x in rows:
        if x["disposition"] == "added":
            x["school_key"] = nd("https://" + x["site"]) or "nosite:" + x["site"]
    rows.sort(key=lambda x: (x["disposition"], x["site"]))
    fields = list(rows[0])
    with open(DISPOSITIONS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} candidates -> {DISPOSITIONS.name}: {dict(Counter(x['disposition'] for x in rows))}")
    print("duplicates by rule:", dict(Counter(re.sub(r'.*\((domain|name|street|at the)[^)]*\).*', r'\1', x["detail"])
                                              for x in rows if x["disposition"] == "duplicate")))
    print(f"\n{len(ambiguous)} ambiguous pairs among 'added' (resolve in HAND):")
    for t in sorted(set(ambiguous)):
        print("  " + " | ".join(str(v) for v in t))


# ── Ingest ───────────────────────────────────────────────────────

AIRPORT_ID = re.compile(r"^(K[A-Z0-9]{3}|P[AHF][A-Z0-9]{2}|[A-Z0-9]{3}|[0-9][A-Z0-9]{2,3}|[A-Z]{2}[0-9]{2})$")
EXTRACT = W / "extract"


def cmd_ingest(a):
    """'added' rows -> enriched entries (source google_places), each named, located and reachable as its own
    pages say, with per-field provenance; and the extraction inputs restricted to the added sites."""
    with open(DISPOSITIONS) as f:
        rows = [r for r in csv.DictReader(f) if r["disposition"] == "added"]
    cls = {r["candidate_id"]: r for r in json.loads((W / "classified.json").read_text())}
    cands = {c["candidate_id"]: c for c in json.loads((W / "candidates.json").read_text())}
    build = _load("build21", "21_build_flight_line.py")
    fl = json.loads((OUT / "flight_line.json").read_text())
    ours = {s["domain"] for s in fl if s["domain"]}
    entries, sites, unnamed = [], {}, []
    for r in rows:
        c, k = cands[r["candidate_id"]], cls[r["candidate_id"]]
        ct = k["contact"]
        dom = build.normalize_domain(c["url"])
        if dom and dom in ours:
            sys.exit(f"FAIL {dom} is already in flight_line.json (dispose as duplicate)")
        prov = lambda v: {"source": "site", "ref": v["ref"], "retrieved": k["retrieved"].get(v["ref"], PULLED)}
        first = next(iter(k["retrieved"]))
        name = ct["name"]["value"]
        if not name:
            unnamed.append(r["site"])
            name = r["site"]
        fs = {"name": prov(ct["name"]) if ct["name"]["value"] else {"source": "domain", "ref": r["site"], "retrieved": PULLED},
              "contact.website": {"source": "site", "ref": first, "retrieved": k["retrieved"][first]}}
        addr, phone = ct["street_address"]["value"], ct["phone"]["value"]
        if addr:
            fs["contact.address"] = prov(ct["street_address"])
        elif ct["city_state"]["value"]:
            addr = ct["city_state"]["value"]
            fs["contact.address"] = prov(ct["city_state"])
        if phone:
            fs["contact.phone"] = prov(ct["phone"])
        apt = next((x.upper() for x in k["airports"] if AIRPORT_ID.match(x.upper().strip())), "")
        e = {"name": name, "website": c["url"], "city": ct["city_state"]["city"], "state": ct["city_state"]["state"],
             "state_name": ct["city_state"]["state"], "airport_code": apt, "address": addr, "phone": phone,
             "source": SOURCE, "field_sources": fs,
             "provenance": [{"list": SOURCE, "record_id": pid, "list_date": PULLED, "pulled": PULLED}
                            for pid in c["place_ids"]]}
        entries.append(e)
        if dom:
            sites[r["site"]] = (dom, name)
    enriched = json.loads(ENRICHED.read_text())
    kept = [e for e in enriched if e.get("source") != SOURCE]
    clash = {build.normalize_domain(e.get("website", "")) for e in kept if e.get("provenance")} & {d for d, _ in sites.values()}
    if clash:
        sys.exit(f"FAIL already added by 57: {sorted(clash)}")
    ENRICHED.write_text(json.dumps(kept + entries, indent=2) + "\n")
    # Extraction inputs: 09a's index and 10's text, only for the added sites, named as their pages name them.
    EXTRACT.mkdir(parents=True, exist_ok=True)
    http = []
    for x in json.loads((CRAWL / "fleet_crawl_index.json").read_text()):
        if site_dom(x["school_website"]) in sites and x.get("pages"):
            http.append({**x, "school_name": sites[site_dom(x["school_website"])][1]})
    (EXTRACT / "http_index.json").write_text(json.dumps(http, indent=1) + "\n")
    meta, tdir = [], EXTRACT / "browser_text"
    tdir.mkdir(exist_ok=True)
    for x in json.loads((CRAWL / "fleet_browser_index.json").read_text()):
        s_ = site_dom(x["school_website"])
        if s_ in sites and x.get("pages"):
            meta.append({**x, "school_name": sites[s_][1]})
            for d in {p["text_path"].split("/")[0] for p in x["pages"] if p.get("text_path")}:
                link = tdir / d
                if not link.exists():
                    link.symlink_to((CRAWL / "fleet_browser_text" / d).resolve())
    (EXTRACT / "browser_meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(f"{len(entries)} schools ({len(sites)} with a website domain; {len(entries) - len(sites)} on a site "
          f"builder subdomain, added without one) -> {ENRICHED.name} ({len(enriched) - len(kept)} replaced); "
          f"{len(unnamed)} with no name on their pages {unnamed[:5]}; extraction inputs: {len(http)} HTTP, "
          f"{len(meta)} browser -> {EXTRACT}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ap.add_argument("--env-file", default="/mnt/data/home/dew/dev/aether/.env",
                    help="where ANTHROPIC_API_KEY is (the production checkout's .env on trogdor; never copied)")
    for c in ("candidates", "browser-targets", "classify-build", "classify-report", "dedupe", "ingest"):
        sub.add_parser(c)
    sm = sub.add_parser("sample")
    sm.add_argument("--n", type=int, default=30)
    sm.add_argument("--seed", type=int, default=20261105)
    sm.add_argument("--chars", type=int, default=2500)
    import haiku_batch as hb
    hb.add_cli(sub)
    a = ap.parse_args()
    if a.cmd in ("estimate", "submit", "fetch"):
        return hb.run_cli(a, a.env_file)
    {"candidates": cmd_candidates, "browser-targets": cmd_browser_targets, "classify-build": cmd_classify_build,
     "classify-report": cmd_classify_report, "sample": cmd_sample, "dedupe": cmd_dedupe, "ingest": cmd_ingest}[a.cmd](a)


if __name__ == "__main__":
    main()
