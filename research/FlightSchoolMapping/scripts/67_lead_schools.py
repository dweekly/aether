#!/usr/bin/env python3
"""Release rc5: flight schools from the gap-source leads, each confirmed on its own website.

Leads (docs/hackers-2026-rc5-additions.md):
  tachgo   Tach & Go directory listings that match no school of ours (65_gap_sources.py; 433)
  ppp      SBA PPP NAICS 611512 borrowers that are organizations and match no school of ours (65; 308)
  master   the outside master list's rows with verdict=new (docs/hackers-2026-master-list-check.md; 1,315).
           Its AOPA-only rows are leads only (David, 2026-11-04): nothing AOPA-derived (name spelling,
           phone, email, description, address) is stored on a school or published. Counted separately.

Every value an added school carries comes from its own pages (or another public, non-AOPA page that names
it), copied verbatim and found again there (page_text.py), with per-field provenance as in 60 and 61.
Lead names go into web searches and dedupe only; crawls and classification prompts are named by domain.

Steps, from research/FlightSchoolMapping/ (working files in data/research-2026-11/rc5/, git-ignored):
  leads        pool the three lists, drop out-of-scope names by rule (skydiving, airports and
               municipalities, jet-charter FBOs, ASA pilot-shop dealers, clubs "in formation"),
               de-duplicate (same website, or the same name in the same state) -> leads.json, drops.csv
  search       website-less leads: Claude Haiku 5.5 with web search (aopa.org blocked) looks for the
               operator's own site or another public page -> search/results.jsonl (budget-capped)
  search-report  -> search.json; own sites join the crawl, other public pages are fetched one by one
  targets      crawl targets (websites from the lists and from search) -> targets.json for 09a
  (crawl)      09a_fleet_crawl.py --targets targets.json --out-dir crawl/, then
               10_fleet_browser_crawl.py --crawl-only --no-evasion on browser_targets.json
  browser-targets  thin, refused or missing HTTP crawls -> browser_targets.json (60's rule)
  pages        fetch the public non-own pages search found (one GET each, our user agent) -> pages/
  classify-build | estimate | submit | fetch | classify-report   60's prompt and checks (Message Batches)
  sample       a seeded random N of the accepted additions with page text, for the hand check
  dedupe       60's strict dedupe against flight_line.json and among the leads ->
               docs/hackers-2026-rc5-additions.csv (one disposition per lead)
  ingest       'added' rows -> output/added_schools_rc5.json (the separate file 21 merges) and the
               extraction inputs for extract_v2.py; then 57 merge-extract, 61 report, 21 and 22
  check        gates on the rebuilt flight_line.json
  domains      school domains that now serve unrelated content (parked, gambling, for sale), from
               the November re-crawl -> output/lapsed_domains.json (38 publishes those schools without
               the link)
  report       docs/hackers-2026-rc5-additions.md

Usage: 67_lead_schools.py <step> [options]   (see --help of each step)
"""

import argparse
import csv
import importlib.util
import json
import random
import re
import subprocess
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
REPO = ROOT.parent.parent
OUT = ROOT / "output"
R11 = ROOT / "data" / "research-2026-11"
W = R11 / "rc5"
INPUTS = W / "inputs"
CRAWL = W / "crawl"
PAGES = W / "pages"
SEARCH = W / "search"
CLASSIFY_RUN = W / "classify"
EXTRACT = W / "extract"
DISPOSITIONS = REPO / "docs" / "hackers-2026-rc5-additions.csv"
HANDCHECK = REPO / "docs" / "hackers-2026-rc5-handcheck.csv"
ADDED = OUT / "added_schools_rc5.json"
LAPSED = OUT / "lapsed_domains.json"
SOURCE = "rc5_leads"
UA = "aether-flight-school-research/1.0 (david@weekly.org)"  # 65's identified user agent
GAPS = Path("/mnt/data/home/dew/dev/aether-gaps/research/FlightSchoolMapping/data/research-2026-11")
PROD = Path("/mnt/data/home/dew/dev/aether/research/FlightSchoolMapping")  # read-only: the November recrawl
sys.path.insert(0, str(SCRIPTS))


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def m60():
    """60_google_schools.py with its working paths pointed at this pass (its page, classify and dedupe
    helpers are reused as they are)."""
    m = _load("g60_rc5", "60_google_schools.py")
    m.W, m.CRAWL, m.CLASSIFY_RUN, m.EXTRACT, m.DISPOSITIONS = W, CRAWL, CLASSIFY_RUN, EXTRACT, DISPOSITIONS
    m.SOURCE = SOURCE  # our_records() skips only this pass's own additions
    return m


def site_dom(url):
    return re.sub(r"^https?://(www\.)?", "", (url or "").strip().lower()).split("/")[0].split("?")[0].strip(".")


def env_file():
    """The main checkout's .env (as 66_yelp_places.py's key() finds it): the parent of git's common dir.
    aether-addschools is its own clone, so fall back to the production checkout's .env, as 60 does."""
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True, cwd=SCRIPTS).stdout.strip()
    for p in (Path(common).parent / ".env", Path("/mnt/data/home/dew/dev/aether/.env")):
        if p.exists() and any(l.startswith("ANTHROPIC_API_KEY=") for l in p.read_text().splitlines()):
            return str(p)
    sys.exit("ANTHROPIC_API_KEY not in the main checkout's .env")


# ── Leads ────────────────────────────────────────────────────────

SKYDIVING = re.compile(r"sky\s?div|parachut|jumptown|gojump|\bjump\b", re.I)
TRAINING_WORD = re.compile(r"\b(club|flyers?|flying|flight|aero|aviators?|aviation|school|academy|training|pilots?|"
                           r"soaring|glid\w*|helicopters?|instruction|instructor|cfi|university|college)\b", re.I)
AIRPORTISH = re.compile(r"\b(airport|airfield|airpark|sky ?park|municipal|authority|commission|city of|town of|"
                        r"village of|county commission)\b|^[\w .'’-]+ county$", re.I)
JET_CHARTER = re.compile(r"\bjets?\b|jet ?cent|\bcharters?\b|signature flight|atlantic aviation|million air|sheltair|"
                         r"velocity fbo|ascension fbo|odyssey aviation", re.I)
TRAINING_STRONG = re.compile(r"flight (training|school|academy|instruction)|flying club|aero club|\bacademy\b|"
                             r"\btraining\b|\bschool\b|\bclub\b", re.I)
IN_FORMATION = re.compile(r"in formation|\bforming\b", re.I)
ASA_ONLY = {"ASA_Dealer"}
PUBLIC_MASTER = {"150thAero", "BestAviation"}  # public directory pages, as the master list says


ASA_SCHOOLISH = re.compile(r"\b(flight|flyers|flying|school|academy|training)\b", re.I)


def drop_reason(name, master_sources):
    n = " ".join((name or "").split())
    if master_sources and set(master_sources) <= ASA_ONLY and not ASA_SCHOOLISH.search(n):
        return "asa_dealer"  # a pilot shop; an ASA dealer named as a school stays a lead
    if IN_FORMATION.search(n):
        return "club_in_formation"
    if SKYDIVING.search(n):
        return "skydiving"
    if AIRPORTISH.search(n) and not TRAINING_WORD.search(AIRPORTISH.sub(" ", n)):
        return "airport_or_municipality"
    if JET_CHARTER.search(n) and not TRAINING_STRONG.search(n):
        return "jet_charter_fbo"
    return ""


def nkey(name):
    n = (name or "").lower().replace("&", " and ")
    n = re.sub(r"\b(llc|l\.l\.c\.?|inc|incorporated|corp|corporation|co|company|ltd|the|dba)\b\.?", " ", n)
    return re.sub(r"[^a-z0-9]+", "", n)


def cmd_leads(a):
    INPUTS.mkdir(parents=True, exist_ok=True)
    for src, dst in ((REPO / "docs/hackers-2026-gap-candidates.csv", "gap_candidates.csv"),
                     (GAPS / "gaps/tachgo/tachgo.csv", "tachgo.csv"),
                     (GAPS / "external/c_master_matched.csv", "c_master_matched.csv")):
        if not (INPUTS / dst).exists():
            (INPUTS / dst).write_bytes(Path(src).read_bytes())
    gap = list(csv.DictReader((INPUTS / "gap_candidates.csv").open()))
    tg_detail = {r["id"]: r for r in csv.DictReader((INPUTS / "tachgo.csv").open())}
    master = [r for r in csv.DictReader((INPUTS / "c_master_matched.csv").open()) if r["verdict"] == "new"]
    rows = []
    for r in gap:
        if r["source"] not in ("tachgo", "ppp"):
            continue
        d = tg_detail.get(r["id"], {})
        rows.append({"source": r["source"], "id": r["id"], "name": r["name"], "city": r["city"], "state": r["state"],
                     "website": r["website"], "kind": d.get("kind", "PPP borrower" if r["source"] == "ppp" else ""),
                     "airport": d.get("airport_id", ""), "master_sources": []})
    for r in master:
        srcs = r["sources"].split("|")
        rows.append({"source": "master", "id": r["entity_id"], "name": r["name"].split("\n")[0].strip(), "city": r["city"], "state": r["state"],
                     "website": r["website"], "kind": r["category"], "airport": r["faa_lid"], "master_sources": srcs})
    counts = Counter(r["source"] for r in rows)
    # drops by rule, before anything is fetched
    drops, kept = [], []
    for r in rows:
        why = drop_reason(r["name"], r["master_sources"])
        if why:
            drops.append({**r, "reason": why})
        else:
            kept.append(r)
    # de-duplicate: union of rows with the same website domain, or the same name in the same state
    parent = list(range(len(kept)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    by = defaultdict(list)
    for i, r in enumerate(kept):
        d = site_dom(r["website"])
        if d and d not in ("facebook.com", "sites.google.com", "m.facebook.com"):
            by["d:" + d].append(i)
        if nkey(r["name"]) and r["state"]:
            by[f"n:{nkey(r['name'])}:{r['state']}"].append(i)
    for idx in by.values():
        for j in idx[1:]:
            parent[find(j)] = find(idx[0])
    groups = defaultdict(list)
    for i in range(len(kept)):
        groups[find(i)].append(kept[i])
    leads = []
    for n, g in enumerate(sorted(groups.values(), key=lambda g: (g[0]["state"], nkey(g[0]["name"])))):
        srcs = sorted({r["source"] for r in g})
        msrc = sorted({s for r in g for s in r["master_sources"]})
        aopa_only = srcs == ["master"] and not (set(msrc) & PUBLIC_MASTER)
        # a website from a public list first (Tach & Go), then the master list's (a lead to check, whatever
        # its origin: the site itself is the source once crawled)
        web = next((r["website"] for r in g if r["source"] == "tachgo" and r["website"]), "") or \
            next((r["website"] for r in g if r["website"]), "")
        leads.append({"lead_id": f"L{n:04d}", "sources": srcs, "master_sources": msrc, "aopa_only": aopa_only,
                      "rows": [{"source": r["source"], "id": r["id"]} for r in g],
                      # working data for search and dedupe only; never written to a committed file
                      "name": g[0]["name"], "city": next((r["city"] for r in g if r["city"]), ""),
                      "state": g[0]["state"], "kind": next((r["kind"] for r in g if r["kind"]), ""),
                      "airport": next((r["airport"] for r in g if r["airport"]), ""), "website": web})
    # leads whose list website is a social page count as website-less (search finds their own page)
    for l in leads:
        if site_dom(l["website"]) in ("facebook.com", "m.facebook.com", "instagram.com", "linkedin.com"):
            l["social"], l["website"] = l["website"], ""
    W.mkdir(parents=True, exist_ok=True)
    (W / "leads.json").write_text(json.dumps(leads, indent=1) + "\n")
    stats(leads={"rows": dict(counts), "dropped": len(drops), "leads": len(leads),
                 "aopa_only": sum(l["aopa_only"] for l in leads), "website": sum(1 for l in leads if l["website"])})
    with open(W / "drops.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["source", "id", "reason", "name", "state"], extrasaction="ignore")
        w.writeheader()
        w.writerows(drops)
    print(f"rows {dict(counts)} = {len(rows)}; dropped by rule {len(drops)} {dict(Counter(d['reason'] for d in drops))}")
    print(f"dropped by source: {dict(Counter((d['source'], d['reason']) for d in drops))}")
    print(f"{len(kept)} rows -> {len(leads)} leads; by source set: {dict(Counter('+'.join(l['sources']) for l in leads))}")
    print(f"AOPA-only leads {sum(l['aopa_only'] for l in leads)}; with a website {sum(1 for l in leads if l['website'])}, "
          f"website-less {sum(1 for l in leads if not l['website'])}")


# ── Search for website-less leads ────────────────────────────────

SEARCH_MODEL = "claude-haiku-5-5"
SEARCH_SYSTEM = ("You look for the public web presence of a small aviation business. Search the web, then report "
                 "only what the search results show. Never guess a URL.")
SEARCH_USER = """\
Business: {name}
Location: {city}, {state}
Listed as: {kind}{airport}

Search the web for this business, then answer:
- own_website: the URL of the business's own website (its own domain, or a site it runs on a site builder such as \
Wix or Google Sites), if a search result shows one. Else "".
- public_pages: up to 3 URLs of other public pages that show this business exists and what it does: its Facebook \
page, an EAA chapter page, a page of the airport it is based at, a state or club association list, a news story. \
Not aopa.org. Each with kind: "facebook", "eaa", "airport", "state_list", "association", "news", "directory" (a \
site listing many businesses, such as AirNav, Yelp, Manta or the Tach & Go directory) or "other".
- same_business: "yes" if the results are clearly this very business (same name and place), else "no".
- status: "found", "closed" (results say it closed, dissolved or sold its aircraft) or "not_found".
- note: a few words on what the results show."""
SEARCH_PROPS = {
    "own_website": {"type": "string"},
    "public_pages": {"type": "array", "items": {"type": "object", "properties": {
        "url": {"type": "string"}, "kind": {"type": "string", "enum": [
            "facebook", "eaa", "airport", "state_list", "association", "news", "directory", "other"]}},
        "required": ["url", "kind"], "additionalProperties": False}},
    "same_business": {"type": "string", "enum": ["yes", "no"]},
    "status": {"type": "string", "enum": ["found", "closed", "not_found"]},
    "note": {"type": "string"},
}
SEARCH_PRICE = {"in": 0.10e-6, "out": 0.50e-6, "search": 0.01}  # Haiku 5.5 standard (prompt < 100K), web search


def search_cost(u):
    st = (u.get("server_tool_use") or {}).get("web_search_requests", 0) or 0
    return (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) or 0) * SEARCH_PRICE["in"] + \
        (u.get("cache_read_input_tokens", 0) or 0) * SEARCH_PRICE["in"] * 0.1 + \
        u.get("output_tokens", 0) * SEARCH_PRICE["out"] + st * SEARCH_PRICE["search"]


def search_one(c, l, max_uses):
    import page_text as pt
    user = SEARCH_USER.format(name=l["name"], city=l["city"] or "?", state=l["state"], kind=l["kind"] or "aviation business",
                              airport=f", at airport {l['airport']}" if l["airport"] else "")
    for attempt in range(4):
        try:
            r = c.messages.create(
                model=SEARCH_MODEL, max_tokens=4000, system=SEARCH_SYSTEM,
                messages=[{"role": "user", "content": user}],
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses,
                        "blocked_domains": ["aopa.org"]}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": pt.schema(SEARCH_PROPS)}})
            break
        except Exception as e:  # rate limits and overloads: the SDK retries too; give up after 4
            err = repr(e)
            time.sleep(10 * (attempt + 1))
    else:
        return {"lead_id": l["lead_id"], "error": err}
    blocks = r.model_dump()["content"]
    results, queries = [], []
    for b in blocks:
        if b["type"] == "server_tool_use":
            queries.append((b.get("input") or {}).get("query", ""))
        if b["type"] == "web_search_tool_result" and isinstance(b.get("content"), list):
            results += [{"url": x.get("url"), "title": x.get("title"), "page_age": x.get("page_age")} for x in b["content"]]
    text = "".join(b.get("text", "") for b in blocks if b["type"] == "text")
    try:
        ans = json.loads(text)
    except json.JSONDecodeError:
        ans = None
    u = r.usage.model_dump()
    return {"lead_id": l["lead_id"], "answer": ans, "raw_text": None if ans else text, "queries": queries,
            "results": results, "usage": u, "cost": round(search_cost(u), 5), "stop_reason": r.stop_reason,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def cmd_search(a):
    """Website-less leads, in a fixed seeded order, until --max-usd is spent (resumable)."""
    import extract_v2 as xv
    leads = [l for l in json.loads((W / "leads.json").read_text()) if not l["website"]]
    random.Random(67).shuffle(leads)
    # public-list leads first, then AOPA-only, then PPP-only (the lowest yield in 65's hand check), so a
    # cap that runs out leaves the least promising unsearched
    leads.sort(key=lambda l: 2 if l["sources"] == ["ppp"] else 1 if l["aopa_only"] else 0)
    SEARCH.mkdir(parents=True, exist_ok=True)
    path = SEARCH / "results.jsonl"
    done = {}
    if path.exists():
        for line in path.open():
            x = json.loads(line)
            if "error" not in x:
                done[x["lead_id"]] = x
    spent = sum(x["cost"] for x in done.values())
    todo = [l for l in leads if l["lead_id"] not in done][:a.limit or None]
    print(f"{len(leads)} website-less leads, {len(done)} searched (${spent:.2f}); {len(todo)} to go, cap ${a.max_usd}")
    c = xv.client(env_file())
    with open(path, "a") as f, ThreadPoolExecutor(a.workers) as ex:
        for i in range(0, len(todo), a.workers * 4):
            if spent >= a.max_usd:
                print(f"STOP at the cap: ${spent:.2f}")
                break
            for x in ex.map(lambda l: search_one(c, l, a.max_uses), todo[i:i + a.workers * 4]):
                f.write(json.dumps(x) + "\n")
                f.flush()
                spent += x.get("cost", 0)
            print(f"  {min(i + a.workers * 4, len(todo))}/{len(todo)} ${spent:.2f}", flush=True)
    print(f"spent ${spent:.2f} on search")


def stats(**kw):
    """Numbers each step leaves for the report, merged into stats.json."""
    p = W / "stats.json"
    s = json.loads(p.read_text()) if p.exists() else {}
    s.update(kw)
    p.write_text(json.dumps(s, indent=1, sort_keys=True) + "\n")


NOT_OWN = re.compile(r"(^|\.)(facebook|fb|instagram|linkedin|twitter|x|youtube|yelp|airnav|150thaero|bestaviation|"
                     r"mapquest|manta|bizapedia|opencorporates|flightschools|flightschoollist|aopa|eaa|nextdoor|"
                     r"tripadvisor|google|bbb|yellowpages|chamberofcommerce|buzzfile|dnb|zoominfo|wikipedia|"
                     r"flightcircle|schedulemaster|flyingclubs|pilotsofamerica|faa|globalair|airport-data|"
                     r"aviationdb|flightaware)\.(com|org|net|gov|fyi|info)$")
PAGE_KINDS = ("eaa", "airport", "state_list", "association", "news", "other")


def cmd_search_report(a):
    leads = {l["lead_id"]: l for l in json.loads((W / "leads.json").read_text())}
    res = {}
    for line in (SEARCH / "results.jsonl").open():
        x = json.loads(line)
        if "error" not in x or x["lead_id"] not in res:
            res[x["lead_id"]] = x
    out, why = {}, Counter()
    for lid, l in leads.items():
        if l["website"]:
            continue
        x = res.get(lid)
        o = {"outcome": "", "site_url": "", "pages": []}
        if x is None:
            o["outcome"] = "not_searched"
        elif x.get("error") or not x.get("answer"):
            o["outcome"] = "search_failed"
        else:
            ans = x["answer"]
            own = (ans.get("own_website") or "").strip()
            seen = {r["url"] for r in x["results"]}
            pages = [p for p in ans.get("public_pages") or [] if p["kind"] in PAGE_KINDS
                     and not NOT_OWN.search(site_dom(p["url"])) and p["url"] in seen]
            if ans["status"] == "closed":
                o["outcome"] = "closed_per_search"
            elif ans["same_business"] != "yes" or ans["status"] != "found":
                o["outcome"] = "no_public_source"
            elif own and not NOT_OWN.search(site_dom(own)) and (own in seen or any(site_dom(own) == site_dom(u) for u in seen)):
                o["outcome"], o["site_url"] = "own_site", own
            elif own and not NOT_OWN.search(site_dom(own)):
                o["outcome"], o["site_url"] = "own_site_not_in_results", own
            elif pages:
                o["outcome"], o["pages"] = "public_page", pages[:3]
            elif any(p["kind"] == "facebook" for p in ans.get("public_pages") or []):
                o["outcome"] = "facebook_only"
            else:
                o["outcome"] = "directory_only"
        why[o["outcome"]] += 1
        out[lid] = o
    (W / "search.json").write_text(json.dumps(out, indent=1) + "\n")
    spent = sum(x.get("cost", 0) for x in res.values())
    n_search = sum((x.get("usage") or {}).get("server_tool_use", {}).get("web_search_requests", 0) for x in res.values())
    stats(search={"leads": len(out), "searched": sum(1 for x in res.values() if x.get("answer")), "outcomes": dict(why),
                  "cost_usd": round(spent, 3), "web_searches": n_search,
                  "tokens_in": sum((x.get("usage") or {}).get("input_tokens", 0) for x in res.values()),
                  "tokens_out": sum((x.get("usage") or {}).get("output_tokens", 0) for x in res.values())})
    print(f"{len(out)} website-less leads: {dict(why)}; search cost ${spent:.2f} ({n_search} searches)")


def ours_domains():
    m = m60()
    recs, nd = m.our_records()
    by = {}
    for o in recs:
        for d in o["domains"]:
            by.setdefault(d, o["key"])
    return by, nd


def cmd_targets(a):
    """Crawl targets: list websites and sites search found, one per domain, minus domains we already have."""
    leads = json.loads((W / "leads.json").read_text())
    srch = json.loads((W / "search.json").read_text())
    ours, nd = ours_domains()
    sites, pre_dup = {}, {}
    for l in leads:
        o = srch.get(l["lead_id"], {})
        # a site the model named but that was not among the result URLs is crawled too: its own pages decide
        url = l["website"] or (o.get("site_url") if o.get("outcome") in ("own_site", "own_site_not_in_results") else "")
        if not url:
            continue
        if not url.startswith("http"):
            url = "https://" + url
        d = site_dom(url)
        hit = ours.get(nd(url)) or ours.get(d)
        if hit and d not in ("sites.google.com",):
            pre_dup.setdefault(d, {"school_key": hit, "leads": []})["leads"].append(l["lead_id"])
            continue
        s = sites.setdefault(d, {"url": url, "leads": [], "via": "list" if l["website"] else
                                 "search" if o.get("outcome") == "own_site" else "search_named"})
        s["leads"].append(l["lead_id"])
    byid = {l["lead_id"]: l for l in leads}
    cands = []
    for i, (d, s) in enumerate(sorted(sites.items())):
        l0 = byid[s["leads"][0]]
        cands.append({"candidate_id": f"S{i:04d}", "site": d, "url": s["url"], "place_ids": [], "lead_ids": s["leads"],
                      "via": s["via"],
                      # lead working data, for dedupe only; never written to a committed file
                      "g_name": l0["name"], "g_address": "", "g_city": l0["city"], "g_state": l0["state"]})
    (W / "candidates.json").write_text(json.dumps(cands, indent=1) + "\n")
    (W / "pre_crawl_duplicates.json").write_text(json.dumps(pre_dup, indent=1) + "\n")
    targets = [{"name": c["site"], "website": c["url"], "airport_code": "", "city": "", "state_name": ""} for c in cands]
    (W / "targets.json").write_text(json.dumps(targets, indent=1) + "\n")
    stats(targets={"sites": len(cands), "from_list": sum(1 for c in cands if c["via"] == "list"),
                   "from_search": sum(1 for c in cands if c["via"] == "search"),
                   "from_search_named": sum(1 for c in cands if c["via"] == "search_named"),
                   "already_ours_before_crawl": len(pre_dup),
                   "leads_already_ours_before_crawl": sum(len(v["leads"]) for v in pre_dup.values())})
    print(f"{len(cands)} sites to crawl ({Counter(c['via'] for c in cands)}); {len(pre_dup)} domains already ours "
          f"({sum(len(v['leads']) for v in pre_dup.values())} leads) -> targets.json")


def cmd_browser_targets(a):
    m60().cmd_browser_targets(a)


def cmd_pages(a):
    """One GET per public page search found (our identified user agent, no retries past a refusal)."""
    import hashlib
    srch = json.loads((W / "search.json").read_text())
    todo = [(lid, p) for lid, o in srch.items() if o["outcome"] == "public_page" for p in o["pages"]]
    (PAGES / "raw").mkdir(parents=True, exist_ok=True)
    idx_p = PAGES / "index.json"
    idx = {(x["lead_id"], x["url"]): x for x in (json.loads(idx_p.read_text()) if idx_p.exists() else [])}

    def get(item):
        lid, p = item
        rec = {"lead_id": lid, "url": p["url"], "kind": p["kind"], "retrieved": date.today().isoformat()}
        try:
            req = urllib.request.Request(p["url"], headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                body = r.read(3_000_000)
                rec.update(status=r.status, final_url=r.geturl())
        except urllib.error.HTTPError as e:
            rec.update(status=e.code, error="http")
            return rec
        except Exception as e:  # noqa: BLE001  timeouts, TLS, DNS: recorded, not retried
            rec.update(status=0, error=type(e).__name__)
            return rec
        text = body.decode("utf-8", errors="replace")
        if re.search(r"cf-chl|challenge-platform|captcha|Just a moment", text[:20000], re.I):
            rec["error"] = "challenge"
            return rec
        path = PAGES / "raw" / (hashlib.sha1(p["url"].encode()).hexdigest()[:16] + ".html")
        path.write_text(text)
        rec["path"] = str(path)
        return rec
    new = [t for t in todo if (t[0], t[1]["url"]) not in idx]
    with ThreadPoolExecutor(4) as ex:
        for rec in ex.map(get, new):
            idx[(rec["lead_id"], rec["url"])] = rec
    idx_p.write_text(json.dumps(list(idx.values()), indent=1) + "\n")
    st = Counter("ok" if x.get("path") else (x.get("error") or "fail") + f"_{x.get('status')}" for x in idx.values())
    stats(pages={"fetched": len(idx), "by_status": dict(st)})
    print(f"{len(idx)} public pages: {dict(st)}")


PAGE_QUESTIONS = """\
Business: {name}, {city}, {state}

The pages below are public pages from other websites (an airport, an EAA chapter, an association, a news site) \
that may mention this business. Answer these questions about this business only, from what the pages say about \
it. If the pages do not mention it, answer "unclear" and leave the values empty.

- flight_instruction: does this business itself give flight instruction (lessons toward a pilot certificate or \
rating, or dual instruction) in manned aircraft, or is it a flying club whose members fly club aircraft? "yes", \
"no" or "unclear". Answer "no" for skydiving, paragliding, drones, simulator-only training, ground school only, \
charter, tours, sales, maintenance and FBO services with no training.
- operation_type: "flight_school", "flying_club", "university_program", "independent_instructor", or "none".
- not_training_kind: when not "yes", what it is in a few words. Otherwise "".
- aircraft: the kinds of aircraft it teaches or flies in. Empty when none.
- evidence: one short quote, copied exactly from the pages, that best supports your answer, and evidence_url.
- airports: airport identifiers or names the pages give for where it operates, each copied exactly.
{contact}

Web pages:
{pages}"""
PAGE_RUN = W / "classify_pages"


def page_requests():
    import page_text as pt
    leads = {l["lead_id"]: l for l in json.loads((W / "leads.json").read_text())}
    by_lead = defaultdict(list)
    for x in json.loads((PAGES / "index.json").read_text()):
        if x.get("path"):
            by_lead[x["lead_id"]].append({"url": x["url"], "path": x["path"], "kind": "html", "type": x["kind"],
                                          "retrieved": x["retrieved"], "cache": "rc5-pages"})
    return leads, by_lead, pt


def cmd_classify_build(a):
    import haiku_batch as hb
    m = m60()
    m.cmd_classify_build(a)  # the site candidates, exactly as 60 builds them
    leads, by_lead, pt = page_requests()
    reqs, index = [], []
    for i, (lid, pgs) in enumerate(sorted(by_lead.items())):
        block, sent = pt.pages_block(pgs, m.CAPS)
        if not sent:
            continue
        l = leads[lid]
        user = PAGE_QUESTIONS.format(name=l["name"], city=l["city"], state=l["state"], contact=pt.CONTACT_QUESTIONS,
                                     pages=block)
        cid = f"p{i:04d}"
        reqs.append({"custom_id": cid, "params": pt.params(user, {**m.CLASS_PROPS, **pt.CONTACT_PROPS}, "medium")})
        index.append({"custom_id": cid, "lead_id": lid, "site": "", "pages": [
            {k: p[k] for k in ("url", "path", "kind", "type", "retrieved", "cache")} for p in pgs if p["url"] in dict(sent)]})
    if reqs:
        hb.write_run(PAGE_RUN, reqs, index, {"what": "67 classify public pages", "prompt": PAGE_QUESTIONS,
                                             "caps": m.CAPS})
    print(f"{len(reqs)} public-page requests -> {PAGE_RUN}")


def cmd_classify_report(a):
    import haiku_batch as hb
    import page_text as pt
    m = m60()
    m.cmd_classify_report(a)
    out, st = [], Counter()
    if (PAGE_RUN / "index.json").exists():
        ans = hb.answers(PAGE_RUN)
        for e in json.loads((PAGE_RUN / "index.json").read_text()):
            x = ans.get(e["custom_id"])
            rec = {"lead_id": e["lead_id"], "custom_id": e["custom_id"]}
            if x is None:
                rec["flight_instruction"] = "failed"
            else:
                sent = m.sent_pages(pt, e)
                ref, how = pt.verify(x["evidence"], x["evidence_url"], sent)
                rec.update({k: x[k] for k in ("flight_instruction", "operation_type", "not_training_kind", "aircraft")})
                rec["evidence"] = {"quote": x["evidence"], "ref": ref or "", "how": how}
                rec["airports"] = [ap for ap in x["airports"] if any(pt.on_page(ap, t) for _, t in sent)]
                rec["contact"] = pt.verify_contact(x, sent)
                rec["retrieved"] = {p["url"]: p["retrieved"] for p in e["pages"]}
                if rec["flight_instruction"] == "yes" and not ref:
                    rec["flight_instruction"] = "unclear"  # no verbatim evidence on a third-party page: not enough
            st[rec["flight_instruction"]] += 1
            out.append(rec)
    (W / "classified_pages.json").write_text(json.dumps(out, indent=1) + "\n")
    cls = json.loads((W / "classified.json").read_text())
    # crawl outcomes per site, by where its URL came from
    via = {c["site"]: c["via"] for c in json.loads((W / "candidates.json").read_text())}
    out_h = {}
    for x in map(json.loads, (CRAWL / "crawl_attempts_http.jsonl").open()):
        fl = set(x.get("crawl_flags") or [])
        out_h[site_dom(x["website"])] = ("pages" if x.get("pages") else "challenge" if "captcha_challenge" in fl else
                                        f"refused_{x['status']}" if x.get("status") in (401, 403, 429, 503) else
                                        "dns_error" if "DNS" in (x.get("error") or "") else
                                        f"http_{x['status']}" if x.get("status") not in (200, None, 0) else "no_answer")
    bw = {site_dom(x["website"]) for x in map(json.loads, (CRAWL / "crawl_attempts_browser.jsonl").open()) if x.get("pages")}
    with_text = {r["site"] for r in cls}
    crawl = {}
    for v in ("list", "search", "search_named"):
        ds = [d for d, w in via.items() if w == v]
        crawl[f"sites from {v}"] = (f"{len(ds)}: HTTP {dict(Counter(out_h.get(d, 'not_attempted') for d in ds))}; "
                                    f"browser pages for {sum(1 for d in ds if d in bw)}; page text sent to the "
                                    f"classifier for {sum(1 for d in ds if d in with_text)}")
    stats(crawl=crawl)
    stats(classify={"sites": dict(Counter(r["flight_instruction"] for r in cls)),
                    "site_types_yes": dict(Counter(r.get("operation_type") for r in cls if r["flight_instruction"] == "yes")),
                    "pages": dict(st)})
    print(f"public pages classified: {dict(st)}")


# Hand decisions (site or lead id -> (disposition, detail)), from the hand check and the ambiguous pairs `dedupe`
# prints; each was read against the pages or records named.
HAND = {
    "highdesertaviationllc.com": ("rejected", "an aircraft maintenance shop; the owner holds a CFI but the site offers "
                                             "no instruction (hand check)"),
    "destinyaviationservices.com": ("rejected", "a youth aviation-education nonprofit whose newest content is from "
                                                "2014-2016; no current flight-training offer (second hand check)"),
}

# A school with no website is added from another public page only when that page is an airport's, a state list, an
# association's or an EAA chapter's, or one of the official pages below. News stories, archives, registries and
# accident reports show that an operation existed, not that it runs today: the first hand check found four of its
# five misses there (a 2013, a 2018 and a 2021 story, and a 2023 story about a club still being formed).
PAGE_KINDS_ADD = ("airport", "state_list", "association", "eaa")
PAGE_OK = {  # evidence page -> why it counts (read by hand)
    "navymwrmidsouth.com": "the Navy MWR page for its own flying club",
    "cannonforce.com": "the Air Force base's page for its own aero club",
    "uvaviation.com": "the operator's page carrying the club's application",
}


def page_kind(o, url):
    """The kind search gave the public page a classification's evidence came from ('other' when not among them)."""
    return next((p["kind"] for p in o["pages"] if p["url"] == url), "other")


def cmd_dedupe(a):
    m = m60()
    m.HAND = {**m.HAND, **{k: v for k, v in HAND.items() if not k.startswith("L")}}
    m.cmd_dedupe(a)  # site candidates -> DISPOSITIONS, 60's rules and 60's hand decisions for sites it saw
    site_rows = list(csv.DictReader(DISPOSITIONS.open()))
    leads = {l["lead_id"]: l for l in json.loads((W / "leads.json").read_text())}
    cands = {c["candidate_id"]: c for c in json.loads((W / "candidates.json").read_text())}
    srch = json.loads((W / "search.json").read_text())
    pre = json.loads((W / "pre_crawl_duplicates.json").read_text())
    pcls = {r["lead_id"]: r for r in json.loads((W / "classified_pages.json").read_text())}
    mx = m._mx()
    generic = (mx.GENERIC - {"north", "south", "east", "west", "central", "northern", "southern", "eastern",
                             "western", "county", "state", "international"}) | {"airplane", "airplanes", "leasing"}
    ours, _ = m.our_records()

    def lead_cols(lids):
        ls = [leads[i] for i in lids]
        srcs = sorted({s for l in ls for s in l["sources"]})
        public = [l for l in ls if not l["aopa_only"]]
        return {"lead_ids": ";".join(lids), "sources": ";".join(srcs),
                "aopa_only": "yes" if all(l["aopa_only"] for l in ls) else "no",
                # lead names only from public lists (Tach & Go, PPP, the master list's public directory rows)
                "lead_name": public[0]["name"] if public else "",
                "lead_state": ls[0]["state"]}
    FIELDS = ["candidate_id", "lead_ids", "sources", "aopa_only", "lead_name", "lead_state", "via", "site",
              "classification", "operation_type", "aircraft", "not_training_kind", "evidence_url", "school_name",
              "city", "state", "disposition", "school_key", "detail"]
    rows = []
    for r in site_rows:
        c = cands[r["candidate_id"]]
        row = {k: r.get(k, "") for k in FIELDS}
        row.update(lead_cols(c["lead_ids"]), via=c["via"])
        rows.append(row)
    for d, v in sorted(pre.items()):
        rows.append({**{k: "" for k in FIELDS}, **lead_cols(v["leads"]), "candidate_id": "pre:" + d, "site": d,
                     "disposition": "duplicate", "detail": f"{v['school_key']} (domain {d}, before crawling)",
                     "via": "list" if leads[v["leads"][0]]["website"] else "search"})
    for lid, o in sorted(srch.items()):
        if o["outcome"] in ("own_site", "own_site_not_in_results"):
            continue  # a site candidate (or a pre-crawl duplicate) above
        row = {**{k: "" for k in FIELDS}, **lead_cols([lid]), "candidate_id": lid, "via": "search"}
        if o["outcome"] == "public_page" and lid in pcls:
            r = pcls[lid]
            ct = r.get("contact") or {}
            name = (ct.get("name") or {}).get("value", "")
            cs = ct.get("city_state") or {}
            row.update(classification=r["flight_instruction"], operation_type=r.get("operation_type", ""),
                       aircraft=";".join(r.get("aircraft") or []), not_training_kind=r.get("not_training_kind", ""),
                       evidence_url=(r.get("evidence") or {}).get("ref", ""), school_name=name,
                       city=cs.get("city", ""), state=cs.get("state", ""))
            hit = None
            for ob in ours:
                if name and ob["state"] and ob["state"] == (cs.get("state") or leads[lid]["state"]) \
                        and m.name_match(name, ob["name"], mx, generic)[0]:
                    hit = ob
                    break
            if lid in HAND:
                row["disposition"], row["detail"] = HAND[lid]
            elif r["flight_instruction"] != "yes":
                row["disposition"] = "rejected"
                row["detail"] = f"public page: {r['flight_instruction']} ({r.get('not_training_kind', '')})"
            elif not name:
                row["disposition"], row["detail"] = "rejected", "public page does not name it verbatim"
            elif page_kind(o, row["evidence_url"]) not in PAGE_KINDS_ADD and site_dom(row["evidence_url"]) not in PAGE_OK:
                row["disposition"] = "left_out"
                row["detail"] = (f"only a {page_kind(o, row['evidence_url'])} page names it (news, archive or registry; "
                                 f"not current confirmation)")
            elif hit:
                row["disposition"], row["detail"] = "duplicate", f"{hit['key']} (name in {hit['state']})"
            else:
                row["disposition"], row["detail"] = "added", "from a public page that names it (no website)"
                row["school_key"] = "nosite:" + lid
        elif o["outcome"] == "public_page":
            row.update(disposition="left_out", detail="public page not readable (refused, challenge or down)")
        else:
            row.update(disposition="left_out", detail={
                "not_searched": "not searched (search budget)", "search_failed": "search failed",
                "closed_per_search": "search results say it closed", "no_public_source": "no public source found",
                "own_site_not_in_results": "a website was named but not in the search results",
                "facebook_only": "only a Facebook page, which our crawler cannot read",
                "directory_only": "only directory listings (no own site or page that describes it)"}[o["outcome"]])
            row["classification"] = o["outcome"]
        rows.append(row)
    # the same phone as a school we already had (60's rules do not compare phones): a rename or a second domain
    import page_text as pt
    fl = json.loads((OUT / "flight_line.json").read_text())
    by_phone = defaultdict(set)
    for s in fl:
        for p in ((s.get("contact") or {}).get("phone"), ((s.get("public_contact") or {}).get("phone") or {}).get("value")):
            if pt.phone10(p or ""):
                by_phone[pt.phone10(p)].add(s["domain"] or s["name"])
    cls_by_cand = {r["candidate_id"]: r for r in json.loads((W / "classified.json").read_text())}
    for r in rows:
        if r["disposition"] != "added" or r["candidate_id"] in HAND or r["site"] in HAND:
            continue
        k = cls_by_cand.get(r["candidate_id"]) or pcls.get(r["candidate_id"]) or {}
        ph = pt.phone10(((k.get("contact") or {}).get("phone") or {}).get("value", ""))
        if ph and by_phone.get(ph):
            r["disposition"] = "duplicate"
            r["detail"] = f"{sorted(by_phone[ph])[0]} (same phone)"
            r["school_key"] = ""
            print(f"  same phone: {r['site'] or r['candidate_id']} {r['school_name']} -> {sorted(by_phone[ph])}")
    # leads that reach the same school by two routes (a site and a public page): keep the site
    added = [r for r in rows if r["disposition"] == "added"]
    for i, x in enumerate(added):
        for y in added[i + 1:]:
            if x["state"] and x["state"] == y["state"] and m.name_match(x["school_name"], y["school_name"], mx, generic)[0]:
                drop = y if (x["site"] and not y["site"]) or (x["site"] and y["site"] and x["site"] < y["site"]) else x
                keep = x if drop is y else y
                if drop["disposition"] == "added" and (drop["candidate_id"] not in HAND):
                    drop["disposition"] = "duplicate"
                    drop["detail"] = f"{keep['site'] or keep['school_name']} (another lead's candidate, same name in {x['state']})"
                    drop["school_key"] = ""
    # gate: every lead has exactly one row
    seen = Counter(lid for r in rows for lid in r["lead_ids"].split(";"))
    missing, twice = set(leads) - set(seen), [k for k, v in seen.items() if v > 1]
    if missing or twice:
        sys.exit(f"FAIL leads without a row {sorted(missing)[:10]} or with two {twice[:10]}")
    rows.sort(key=lambda x: (x["disposition"], x["site"], x["candidate_id"]))
    with open(DISPOSITIONS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    disp = Counter(r["disposition"] for r in rows)
    stats(dedupe={"rows": len(rows), "leads": len(leads), "dispositions": dict(disp),
                  "leads_by_disposition": dict(Counter(r["disposition"] for r in rows for _ in r["lead_ids"].split(";")))})
    print(f"{len(rows)} rows for {len(leads)} leads -> {DISPOSITIONS.name}: {dict(disp)}")


AIRPORT_ID = re.compile(r"^(K[A-Z0-9]{3}|P[AHF][A-Z0-9]{2}|[A-Z0-9]{3}|[0-9][A-Z0-9]{2,3}|[A-Z]{2}[0-9]{2})$")
PULLED = "2026-11-05"


def cmd_ingest(a):
    """'added' rows -> output/added_schools_rc5.json, each named, located and reachable as its own pages (or the
    public page that names it) say, with per-field provenance; and extraction inputs for the added sites."""
    import page_text as pt
    build = _load("build21", "21_build_flight_line.py")
    rows = [r for r in csv.DictReader(DISPOSITIONS.open()) if r["disposition"] == "added"]
    cls = {r["candidate_id"]: r for r in json.loads((W / "classified.json").read_text())}
    pcls = {r["lead_id"]: r for r in json.loads((W / "classified_pages.json").read_text())}
    cands = {c["candidate_id"]: c for c in json.loads((W / "candidates.json").read_text())}
    leads = {l["lead_id"]: l for l in json.loads((W / "leads.json").read_text())}
    fl = json.loads((OUT / "flight_line.json").read_text())
    ours = {s["domain"] for s in fl if s["domain"] and (s.get("provenance") or [{}])[0].get("list") != SOURCE}
    entries, sites = [], {}
    for r in rows:
        page = r["candidate_id"].startswith("L")
        k = pcls[r["lead_ids"]] if page else cls[r["candidate_id"]]
        ct = k["contact"]
        url = "" if page else cands[r["candidate_id"]]["url"]
        dom = build.normalize_domain(url) if url else ""
        if dom and dom in ours:
            sys.exit(f"FAIL {dom} is already in flight_line.json (dispose as duplicate)")
        src = "public_page" if page else "site"

        def prov(v):
            return {"source": src, "ref": pt.clean_url(v["ref"]), "retrieved": k["retrieved"].get(v["ref"], PULLED)}
        name = ct["name"]["value"] or r["site"]
        fs = {"name": prov(ct["name"]) if ct["name"]["value"] else {"source": "domain", "ref": r["site"], "retrieved": PULLED}}
        if not page:
            first = next(iter(k["retrieved"]))
            fs["contact.website"] = {"source": "site", "ref": pt.clean_url(first), "retrieved": k["retrieved"][first]}
        addr, phone = ct["street_address"]["value"], ct["phone"]["value"]
        if r["operation_type"] == "independent_instructor" or page:
            addr = ""  # 38's public contact policy: no street address for one instructor; none from a third party
        if addr:
            fs["contact.address"] = prov(ct["street_address"])
        elif ct["city_state"]["value"]:
            addr = ct["city_state"]["value"]
            fs["contact.address"] = prov(ct["city_state"])
        if page:
            phone = ""  # a phone on someone else's page may be theirs, not the school's
        if phone:
            fs["contact.phone"] = prov(ct["phone"])
        apt = next((x.upper().strip() for x in k["airports"] if AIRPORT_ID.match(x.upper().strip())), "")
        lids = r["lead_ids"].split(";")
        via = [f"{row['source']}:{row['id']}" for lid in lids for row in leads[lid]["rows"]]
        e = {"name": name, "website": pt.clean_url(url) if url else "", "city": ct["city_state"]["city"],
             "state": ct["city_state"]["state"], "state_name": ct["city_state"]["state"], "airport_code": apt,
             "address": addr, "phone": phone, "source": SOURCE, "operation_type": r["operation_type"],
             "field_sources": fs,
             "provenance": [{"list": SOURCE, "record_id": r["candidate_id"], "leads": via, "list_date": PULLED,
                             "pulled": PULLED}]}
        entries.append(e)
        if dom:
            sites[r["site"]] = (dom, name)
    enriched = json.loads((OUT / "flight_schools_enriched.json").read_text())
    clash = {build.normalize_domain(x.get("website", "")) for x in enriched if x.get("provenance")} & {d for d, _ in sites.values()}
    if clash:
        sys.exit(f"FAIL already added by 57 or 60: {sorted(clash)}")
    ADDED.write_text(json.dumps(entries, indent=1) + "\n")
    added_ext = OUT / "added_extract.jsonl"  # drop extractions of rc5 sites no longer added (reruns)
    if added_ext.exists():
        keep_d = {build.normalize_domain(x.get("website", "")) for x in enriched + entries if x.get("provenance")}
        lines = added_ext.read_text().splitlines()
        kept_l = [l for l in lines if json.loads(l)["domain"] in keep_d]
        if len(kept_l) != len(lines):
            added_ext.write_text("".join(l + "\n" for l in kept_l))
    EXTRACT.mkdir(parents=True, exist_ok=True)
    http = [{**x, "school_name": sites[site_dom(x["school_website"])][1]}
            for x in json.loads((CRAWL / "fleet_crawl_index.json").read_text())
            if site_dom(x["school_website"]) in sites and x.get("pages")]
    (EXTRACT / "http_index.json").write_text(json.dumps(http, indent=1) + "\n")
    meta, tdir = [], EXTRACT / "browser_text"
    tdir.mkdir(exist_ok=True)
    bidx = CRAWL / "fleet_browser_index.json"
    for x in (json.loads(bidx.read_text()) if bidx.exists() else []):
        s_ = site_dom(x["school_website"])
        if s_ in sites and x.get("pages"):
            meta.append({**x, "school_name": sites[s_][1]})
            for d in {p["text_path"].split("/")[0] for p in x["pages"] if p.get("text_path")}:
                link = tdir / d
                if not link.exists():
                    link.symlink_to((CRAWL / "fleet_browser_text" / d).resolve())
    (EXTRACT / "browser_meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    stats(ingest={"schools": len(entries), "with_website": len(sites), "from_public_page": len(entries) - len(sites),
                  "by_type": dict(Counter(e["operation_type"] for e in entries))})
    print(f"{len(entries)} schools ({len(sites)} with a website) -> {ADDED.name}; extraction inputs: {len(http)} HTTP, "
          f"{len(meta)} browser -> {EXTRACT}")


def cmd_check(a):
    """Gates on the rebuilt flight_line.json: every rc5 school present once with provenance; its name, address and
    phone each found again verbatim on the page its provenance names; nothing from AOPA; no lead's AOPA-derived
    name, phone or email on any record; tails shared with schools we already had, for hand review."""
    import page_text as pt
    m = m60()
    fl = json.loads((OUT / "flight_line.json").read_text())
    entries = json.loads(ADDED.read_text())
    leads = {l["lead_id"]: l for l in json.loads((W / "leads.json").read_text())}
    master = {r["entity_id"]: r for r in csv.DictReader((INPUTS / "c_master_matched.csv").open())}
    idx = {e["custom_id"]: e for e in json.loads((CLASSIFY_RUN / "index.json").read_text())}
    by_cand = {e["candidate_id"]: e for e in idx.values()}
    pidx = {e["lead_id"]: e for e in json.loads((PAGE_RUN / "index.json").read_text())} if (PAGE_RUN / "index.json").exists() else {}
    added = [s for s in fl if (s.get("provenance") or [{}])[0].get("list") == SOURCE]
    fails, checked = [], Counter()
    if len(added) != len(entries):
        fails.append(f"{len(entries)} entries but {len(added)} rc5 records in flight_line.json")
    old_tails = defaultdict(set)
    for s in fl:
        if (s.get("provenance") or [{}])[0].get("list") != SOURCE:
            for f in s["fleet"]:
                if f.get("n_number"):
                    old_tails[f["n_number"]].add(s["domain"] or s["name"])
    aopa_vals = set()
    for l in leads.values():
        if l["aopa_only"]:
            for row in l["rows"]:
                r = master.get(row["id"], {})
                aopa_vals |= {pt.norm(r.get("name", "")), pt.phone10(r.get("phone", ""))} - {""}
    overlaps = []
    for s in added:
        pv = s["provenance"][0]
        e = pidx.get(pv["record_id"]) if pv["record_id"].startswith("L") else by_cand.get(pv["record_id"])
        sent = {pt.clean_url(u): t for u, t in m.sent_pages(pt, e)} if e else {}
        for field, value in [("name", s["name"])] + [("contact." + k, v) for k, v in s["contact"].items() if v]:
            prov = s["field_sources"].get(field)
            if prov is None:
                fails.append(f"{s['name']} {field}: no provenance")
                continue
            if prov["source"] in ("aopa", "google_places"):
                fails.append(f"{s['name']} {field}: source {prov['source']}")
            if field != "contact.website" and (pt.norm(value) in aopa_vals or pt.phone10(value) in aopa_vals) \
                    and not (prov["source"] in ("site", "public_page") and pt.on_page(value, sent.get(prov["ref"], ""),
                                                                                       "phone" if "phone" in field else "text")):
                fails.append(f"{s['name']} {field}={value!r}: equals an AOPA-only lead's value and is not on a page")
            if prov["source"] in ("domain", "site_extraction") or field == "contact.website":
                checked[f"{field} ({prov['source']})"] += 1
                continue
            kind = "phone" if field == "contact.phone" else "text"
            if pt.on_page(value, sent.get(prov["ref"], ""), kind):
                checked[field] += 1
            elif "," in value and all(pt.on_page(x.strip(), sent.get(prov["ref"], "")) or
                                      any(pt.on_page(n, sent.get(prov["ref"], "")) for n, ab in pt.STATES.items() if ab == x.strip())
                                      for x in value.rsplit(",", 1)):
                checked[field + " (city and state)"] += 1
            else:
                fails.append(f"{s['name']} {field}={value!r}: not found again on {prov['ref']} ({prov['source']})")
        for f in s["fleet"]:
            if f.get("n_number") and f["n_number"] in old_tails:
                overlaps.append((s["domain"] or s["name"], f["n_number"], sorted(old_tails[f["n_number"]])))
    with_tails = sum(1 for s in added if any(f.get("n_number") for f in s["fleet"]))
    models_only = sum(1 for s in added if s["fleet"] and not any(f.get("n_number") for f in s["fleet"]))
    stats(check={"records": len(added), "verified": dict(checked), "with_tails": with_tails, "models_only": models_only,
                 "tail_rows": sum(len([f for f in s["fleet"] if f.get("n_number")]) for s in added),
                 "shared_tails": len(overlaps), "fails": len(fails), "flight_line_records": len(fl)})
    print(f"{len(added)} rc5 schools in flight_line.json ({len(fl)} records); verified {dict(checked)}")
    print(f"fleets: {with_tails} with tails, {models_only} models only; {len(overlaps)} tails shared with earlier schools:")
    for o in overlaps:
        print("  ", o)
    if fails:
        print("FAIL\n  " + "\n  ".join(fails[:30]))
        sys.exit(1)
    print("PASS")


def cmd_sample(a):
    """A seeded random N of the added schools, each with its pages, for the hand check
    (labels go in docs/hackers-2026-rc5-handcheck.csv)."""
    import page_text as pt
    m = m60()
    rows = sorted((r for r in csv.DictReader(DISPOSITIONS.open()) if r["disposition"] == "added"),
                  key=lambda r: r["candidate_id"])
    smp = random.Random(a.seed).sample(rows, min(a.n, len(rows)))
    idx = {e["candidate_id"]: e for e in json.loads((CLASSIFY_RUN / "index.json").read_text())}
    pidx = {e["lead_id"]: e for e in json.loads((PAGE_RUN / "index.json").read_text())} if (PAGE_RUN / "index.json").exists() else {}
    with open(W / f"sample_{a.seed}.txt", "w") as f:
        for r in smp:
            e = pidx.get(r["candidate_id"]) if r["candidate_id"].startswith("L") else idx.get(r["candidate_id"])
            f.write(f"\n{'=' * 100}\n{r['candidate_id']} {r['site']} | {r['school_name']} | {r['city']}, {r['state']} | "
                    f"type={r['operation_type']} aircraft={r['aircraft']} | evidence {r['evidence_url']}\n")
            for url, text in (m.sent_pages(pt, e) if e else []):
                f.write(f"--- {url}\n{text[:a.chars]}\n")
    for r in smp:
        print(r["candidate_id"], r["site"] or "(page)", "|", r["school_name"], "|", r["operation_type"])
    print(f"-> {W / f'sample_{a.seed}.txt'}")


# ── Airports for the additions (67 airports) ──────────────────────────────

NASR_BASE = GAPS / "gaps" / "nasr" / "x" / "APT_BASE.csv"  # FAA NASR 2026-10-29 (65), for public use and city
AIRPORT_MATCH = W / "airport_match.json"
AIRPORT_RUN = W / "airports_model"
GENERIC_AP = {"airport", "airfield", "field", "international", "intl", "regional", "rgnl", "municipal", "muni",
              "the", "airpark", "aerodrome", "apt", "arpt", "fld", "intl", "executive", "exec", "jetport", "air",
              "park", "aero", "port"}
AP_PHRASE = re.compile(r"\b(airport|airfield|field|airpark|aerodrome|jetport|airstrip)\b", re.I)


def ap_core(name):
    import page_text as pt
    return " ".join(w for w in pt.norm(name).split() if w not in GENERIC_AP)


def airport_tables():
    """airports.csv rows (the ids flight_line uses) joined to NASR rows (public use, city, status)."""
    rows = list(csv.DictReader((OUT / "airports.csv").open(newline="", encoding="utf-8")))
    by_faa, by_id = {}, {}
    for r in rows:
        if r["faa_id"]:
            by_faa.setdefault(r["faa_id"].upper(), r)
        by_id[r["airport_id"].upper()] = r
        if r["gps_code"]:
            by_id.setdefault(r["gps_code"].upper(), r)
    nasr = [r for r in csv.DictReader(NASR_BASE.open(newline="", encoding="utf-8", errors="replace"))
            if r["COUNTRY_CODE"] == "US"]
    return rows, by_faa, by_id, nasr


def table_row(code, by_faa, by_id):
    c = code.upper().strip()
    return by_faa.get(c) or by_id.get(c) or (by_faa.get(c[1:]) if len(c) == 4 and c[0] == "K" else None)


def entry_state(e, leads):
    """The state the school's own page gives, else a public (non-AOPA) lead list's state, for matching only."""
    if e["state"]:
        return e["state"], "page"
    lids = [l for l in leads.values() if l["lead_id"] in e["_lead_ids"] and not l["aopa_only"]]
    return (lids[0]["state"], "public lead list") if lids and lids[0]["state"] else ("", "")


def cmd_airports(a):
    """Every rc5 addition: the airport its cited pages name -> airport_code plus airport_match (page, quoted text,
    how matched) in added_schools_rc5.json. Order: an identifier the pages give, a full airport name in the
    school's state, the school's city with a single open public-use airport; the rest go to --model-build."""
    import page_text as pt
    m = m60()
    entries = json.loads(ADDED.read_text())
    rows = {r["candidate_id"]: r for r in csv.DictReader(DISPOSITIONS.open()) if r["disposition"] == "added"}
    leads = {l["lead_id"]: l for l in json.loads((W / "leads.json").read_text())}
    cls = {r["candidate_id"]: r for r in json.loads((W / "classified.json").read_text())}
    pcls = {r["lead_id"]: r for r in json.loads((W / "classified_pages.json").read_text())}
    idx = {e["candidate_id"]: e for e in json.loads((CLASSIFY_RUN / "index.json").read_text())}
    pidx = {e["lead_id"]: e for e in json.loads((PAGE_RUN / "index.json").read_text())}
    rows_t, by_faa, by_id, nasr = airport_tables()
    nasr_by_id = {r["ARPT_ID"]: r for r in nasr}
    model = json.loads((W / "airport_model.json").read_text()) if (W / "airport_model.json").exists() else {}
    out, how_n, why_n = {}, Counter(), Counter()
    for e in entries:
        rid = e["provenance"][0]["record_id"]
        page = rid.startswith("L")
        k = pcls[rid] if page else cls[rid]
        ix = pidx.get(rid) if page else idx.get(rid)
        sent = [(pt.clean_url(u), t) for u, t in (m.sent_pages(pt, ix) if ix else [])]
        e["_lead_ids"] = rows[rid]["lead_ids"].split(";")
        st, st_src = entry_state(e, leads)
        e.pop("_lead_ids")

        def where(q):
            return next((u for u, t in sent if pt.on_page(q, t)), "")
        res = None
        reasons = []
        # 1. identifiers and names the classifier copied from the pages (each found again verbatim)
        for q in k.get("airports") or []:
            url = where(q)
            if not url:
                reasons.append(f"{q!r} not found again on a page")
                continue
            qq = q.strip().upper()
            paren = re.search(r"\(\s*([A-Z0-9]{3,4})\s*\)\s*$", qq)  # "Bend Airport (KBDN)": the code it gives
            if paren and AIRPORT_ID.match(paren.group(1)):
                qq = paren.group(1)
            if AIRPORT_ID.match(qq):
                r = table_row(qq, by_faa, by_id)
                if r and (not st or r["state"] == st):
                    res = {"how": "code", "row": r, "page": url, "quote": q}
                    break
                reasons.append(f"code {q!r} " + ("not in airports.csv" if not r else f"is in {r['state']}, not {st}"))
                continue
            core = ap_core(q)
            if core and not st:  # no state: only a full name that is unique in the whole table
                hits = {r["airport_id"] for r in rows_t if r["airport_type"] not in ("closed", "heliport")
                        and pt.norm(r["airport_name"]) == pt.norm(q)}
                if len(hits) == 1:
                    res = {"how": "name", "row": by_id[hits.pop().upper()], "page": url, "quote": q}
                    break
                reasons.append(f"name {q!r} without a state ({len(hits)} airports of that exact name)")
                continue
            if not core:
                reasons.append(f"name {q!r} is generic")
                continue
            hits = {r["airport_id"] for r in rows_t if r["state"] == st and r["airport_type"] not in ("closed", "heliport")
                    and ap_core(r["airport_name"]) == core}
            hits |= {table_row(n["ARPT_ID"], by_faa, by_id)["airport_id"] for n in nasr if n["STATE_CODE"] == st
                     and n["SITE_TYPE_CODE"] == "A" and n["ARPT_STATUS"] == "O" and ap_core(n["ARPT_NAME"]) == core
                     and table_row(n["ARPT_ID"], by_faa, by_id)}
            if len(hits) == 1:
                res = {"how": "name", "row": by_id[hits.pop().upper()], "page": url, "quote": q}
                break
            reasons.append(f"name {q!r}: {len(hits)} airports of that name in {st}")
        # 2. a full airport name (with Airport, Field, ...) from the table, in the school's state, on the pages
        if not res and st:
            found = {}
            for r in rows_t:
                if r["state"] != st or r["airport_type"] in ("closed", "heliport") or not AP_PHRASE.search(r["airport_name"]) \
                        or len(r["airport_name"]) < 12:
                    continue
                url = where(r["airport_name"])
                if url:
                    found[r["airport_id"]] = (r, url)
            if len(found) == 1:
                r, url = next(iter(found.values()))
                res = {"how": "name", "row": r, "page": url, "quote": r["airport_name"]}
            elif found:
                reasons.append(f"{len(found)} airport names on the pages: {sorted(found)}")
        # 3. the city the pages give, with exactly one open public-use airport there
        if not res and e["city"] and st:
            hits = [n for n in nasr if n["STATE_CODE"] == st and n["CITY"] == e["city"].upper() and n["SITE_TYPE_CODE"] == "A"
                    and n["FACILITY_USE_CODE"] == "PU" and n["ARPT_STATUS"] == "O"]
            cs = (e["field_sources"].get("contact.address") or {})
            if len(hits) == 1 and table_row(hits[0]["ARPT_ID"], by_faa, by_id) and cs.get("ref"):
                res = {"how": "city", "row": table_row(hits[0]["ARPT_ID"], by_faa, by_id), "page": cs["ref"],
                       "quote": f"{e['city']}, {st}"}
            else:
                reasons.append(f"city {e['city']}, {st}: {len(hits)} public-use airports")
        # 4. the model's pick (--model-build / fetch), checked: a listed candidate and a quote found on the page
        if not res and rid in model:
            x = model[rid]
            r = by_id.get((x.get("airport_id") or "").upper())
            url = where(x.get("quote") or "")
            if r and url and x["airport_id"].upper() in x["_candidates"]:
                res = {"how": "model", "row": r, "page": url, "quote": x["quote"]}
            else:
                reasons.append("model: " + ("none" if x.get("why") == "none" else f"pick failed the checks ({x.get('why')})"))
        if not res and not reasons:
            reasons.append("no airport, code, city or state on the pages" if not (k.get("airports") or e["city"])
                           else "no state to match in")
        rec = {"school": e["website"] or e["name"], "record_id": rid, "state": st, "state_from": st_src}
        if res:
            r = res["row"]
            rec.update(airport_id=r["airport_id"], airport_name=r["airport_name"], how=res["how"], page=res["page"],
                       quote=res["quote"], city=e["city"])
            e["airport_code"] = r["airport_id"]
            e["airport_match"] = {"source": "public_page" if page else "site", "ref": res["page"], "quote": res["quote"],
                                  "how": res["how"], "matched": f"{r['airport_id']} {r['airport_name']}",
                                  "faa_id": r["faa_id"],
                                  "retrieved": k["retrieved"].get(res["page"], PULLED)}
            how_n[res["how"]] += 1
        else:
            e["airport_code"] = ""
            e.pop("airport_match", None)
            rec["unmatched"] = reasons
            why_n[reasons[-1].split(":")[0].split(" ")[0] if reasons else "?"] += 1
        out[rid] = rec
    ADDED.write_text(json.dumps(entries, indent=1) + "\n")
    AIRPORT_MATCH.write_text(json.dumps(out, indent=1) + "\n")
    unmatched = [r for r in out.values() if "unmatched" in r]
    stats(airports={"schools": len(entries), "by_how": dict(how_n), "unmatched": len(unmatched),
                    "unmatched_why": dict(Counter(classify_why(r["unmatched"]) for r in unmatched))})
    print(f"{len(entries)} schools: matched {dict(how_n)}; unmatched {len(unmatched)} "
          f"{dict(Counter(classify_why(r['unmatched']) for r in unmatched))}")


AIRPORT_QUESTIONS = """Business: {who}

The pages below are this business's own pages (or a public page that names it). Which airport does the business fly
from, as the pages say? Choose airport_id from the candidate list below (the open airports, seaplane bases and
gliderports in {state} from the FAA's airport file). Answer airport_id "" if the pages do not name an airport, an
airport identifier, or a place that identifies exactly one candidate; do not guess from the business name.
- airport_id: one candidate's identifier exactly as listed, or "".
- quote: the shortest text copied exactly from the pages that names that airport (its name, its identifier, or the
  town it is in), and quote_url, the page it is on.

Candidates (identifier | name | city):
{candidates}

Web pages:
{pages}"""
AIRPORT_PROPS = {"airport_id": {"type": "string"}, "quote": {"type": "string"}, "quote_url": {"type": "string"}}


def airport_candidates(st, nasr, by_faa, by_id):
    out = {}
    for n in nasr:
        if n["STATE_CODE"] == st and n["ARPT_STATUS"] == "O" and n["SITE_TYPE_CODE"] in ("A", "C", "G"):
            r = table_row(n["ARPT_ID"], by_faa, by_id)
            if r:
                out[r["airport_id"].upper()] = (n["ARPT_ID"], n["ICAO_ID"], n["ARPT_NAME"], n["CITY"])
    return out


def cmd_airports_model_build(a):
    """The additions `airports` left unmatched that have a state: one request each (Message Batches)."""
    import haiku_batch as hb
    import page_text as pt
    m = m60()
    match = json.loads(AIRPORT_MATCH.read_text())
    idx = {e["candidate_id"]: e for e in json.loads((CLASSIFY_RUN / "index.json").read_text())}
    pidx = {e["lead_id"]: e for e in json.loads((PAGE_RUN / "index.json").read_text())}
    _, by_faa, by_id, nasr = airport_tables()
    reqs, index = [], []
    for rid, r in sorted(match.items()):
        if "unmatched" not in r or not r["state"]:
            continue
        ix = pidx.get(rid) if rid.startswith("L") else idx.get(rid)
        pgs = [{**p, "text": pt.read_page(p)} for p in (ix or {}).get("pages", [])]
        block, sent = pt.pages_block(pgs, m.CAPS)
        if not sent:
            continue
        cands = airport_candidates(r["state"], nasr, by_faa, by_id)
        lines = "\n".join(f"{k} | {v[2].title()} | {v[3].title()}" for k, v in sorted(cands.items()))
        cid = f"a{len(reqs):04d}"
        reqs.append({"custom_id": cid, "params": pt.params(AIRPORT_QUESTIONS.format(
            who=r["school"], state=r["state"], candidates=lines, pages=block), AIRPORT_PROPS, "medium", 4000)})
        index.append({"custom_id": cid, "record_id": rid, "state": r["state"], "pages": ix["pages"]})
    hb.write_run(AIRPORT_RUN, reqs, index, {"what": "67 airports model", "prompt": AIRPORT_QUESTIONS, "caps": m.CAPS})
    print(f"{len(reqs)} requests -> {AIRPORT_RUN}")


def cmd_airports_model_report(a):
    """Model answers -> airport_model.json, each kept only if the airport is a listed candidate and its quote is on the
    page and names exactly one candidate (its identifier, a distinctive word of its name, or its city)."""
    import haiku_batch as hb
    import page_text as pt
    m = m60()
    ans = hb.answers(AIRPORT_RUN)
    _, by_faa, by_id, nasr = airport_tables()
    out, st = {}, Counter()
    for e in json.loads((AIRPORT_RUN / "index.json").read_text()):
        x = ans.get(e["custom_id"]) or {}
        cands = airport_candidates(e["state"], nasr, by_faa, by_id)
        pick = (x.get("airport_id") or "").strip().upper()
        q = (x.get("quote") or "").strip()
        sent = m.sent_pages(pt, e)
        on = any(pt.on_page(q, t) for _, t in sent) if q else False

        def names(k):
            faa, icao, name, city = cands[k]
            toks = {w for w in ap_core(name).split() if len(w) > 2} | {faa.lower(), icao.lower(), k.lower()} - {""}
            return toks, pt.norm(city)
        qn = f" {pt.norm(q)} "
        hits = [k for k in cands if any(f" {t} " in qn for t in names(k)[0]) or f" {names(k)[1]} " in qn]
        ok = bool(pick) and pick in cands and on and hits == [pick]
        why = ("none" if not pick else "not a candidate" if pick not in cands else "quote not on page" if not on
               else f"quote names {len(hits)} candidates" if hits != [pick] else "ok")
        st[why] += 1
        out[e["record_id"]] = {"airport_id": pick if ok else "", "quote": q, "why": why, "_candidates": [pick] if ok else []}
    (W / "airport_model.json").write_text(json.dumps(out, indent=1) + "\n")
    print(f"model answers: {dict(st)}")


def cmd_airports_sample(a):
    """A seeded random N of the new matches (additions that had no airport before this step), each with the text
    around its quote, for the hand check (labels in docs/hackers-2026-rc5-airport-check.csv)."""
    import page_text as pt
    m = m60()
    before = {e["provenance"][0]["record_id"] for e in json.loads(Path(a.before).read_text()) if e["airport_code"]}
    match = json.loads(AIRPORT_MATCH.read_text())
    new = sorted(rid for rid, r in match.items() if r.get("airport_id") and rid not in before)
    smp = random.Random(a.seed).sample(new, min(a.n, len(new)))
    idx = {e["candidate_id"]: e for e in json.loads((CLASSIFY_RUN / "index.json").read_text())}
    pidx = {e["lead_id"]: e for e in json.loads((PAGE_RUN / "index.json").read_text())}
    with open(W / f"airport_sample_{a.seed}.txt", "w") as f:
        for rid in smp:
            r = match[rid]
            ix = pidx.get(rid) if rid.startswith("L") else idx.get(rid)
            text = dict((pt.clean_url(u), t) for u, t in m.sent_pages(pt, ix)).get(r["page"], "")
            flat = " ".join(text.split())
            i = flat.lower().find(r["quote"].lower().split(",")[0])
            f.write(f"\n{'=' * 90}\n{rid} {r['school']} | {r['how']} | {r['airport_id']} {r['airport_name']} | "
                    f"state {r['state']} ({r['state_from']}) city {r.get('city')}\n  page {r['page']}\n  quote {r['quote']!r}\n"
                    f"  context: {flat[max(0, i - 300):i + 300] if i >= 0 else '(quote not located)'}\n")
    stats(airports={**json.loads((W / "stats.json").read_text()).get("airports", {}), "new_matches": len(new)})
    print(f"{len(new)} new matches; sample of {len(smp)} -> {W / f'airport_sample_{a.seed}.txt'}")


def classify_why(reasons):
    t = " | ".join(reasons)
    if "model: none" in t:
        return "the model found none on the pages"
    if "pick failed" in t:
        return "the model's pick failed the checks"
    if "airport names on the pages" in t or "airports of that name" in t:
        return "ambiguous: several airports match"
    if "not in airports.csv" in t or "is in" in t:
        return "a code that is not in the table or is in another state"
    if "public-use airports" in t:
        return "city with no or several public-use airports"
    if "without a state" in t or "no state" in t:
        return "no state on the pages or public lists"
    if "not found again" in t:
        return "the copied airport text is not on the pages"
    return "no airport, code or city on the pages"

# ── School domains that now serve unrelated content ──────────────

GAMBLING = re.compile(r"\b(slot gacor|slot online|situs slot|judi|togel|casino|poker online|sbobet|bandar|taruhan|"
                      r"baccarat|sportsbook|gacor|maxwin|rtp slot|pragmatic play|online betting|link alternatif|"
                      r"daftar slot|bet88|toto)\b", re.I)
PARKED = re.compile(r"(domain (name )?(is|may be) for sale|buy this domain|this domain is for sale|domain is parked|"
                    r"parked free|hugedomains|\bdan\.com\b|\bsedo\b|afternic|this domain has expired|renew (this|your) "
                    r"domain|related searches|is available for purchase|future home of|domain names? for sale)", re.I)
AVIATION = re.compile(r"\b(flight|fly|flying|flies|aviation|aircraft|airplane|aeroplane|pilot|airport|cessna|piper|"
                      r"cirrus|diamond|hangar|instructor|instruction|helicopter|glider|soaring|aero|cfi|faa|"
                      r"airfield|airpark|runway|club|lessons?)", re.I)
CHALLENGE = re.compile(r"just a moment|robot challenge|checking (your browser|the site connection)|verify(ing)? (that )?you|"
                       r"are (not )?a (human|robot|bot)|captcha|performing security verification|access denied|"
                       r"enable (javascript|cookies)|403 forbidden|request blocked|attention required", re.I)
BROKEN = re.compile(r"dns resolution error|error establishing a database|internal server error|404 not found|"
                    r"web server is returning|site not found|this site can.t be reached|bad gateway|"
                    r"service unavailable|has been removed|account (has been )?suspended|suspendedpage|"
                    r"temporarily paused|launching soon|coming soon|under construction", re.I)
PLACEHOLDER = re.compile(r"reconnect your domain|this domain isn.t connected to a site|domain is not connected|"
                         r"has been recently registered|default web site page|welcome to nginx|apache2? .{0,20}default|"
                         r"index of /", re.I)
# Categories that make a domain lapsed by rule; the rest are listed for the doc but not acted on.
LAPSED_BY_RULE = ("gambling", "parked_or_for_sale", "placeholder")


def domain_category(d, text):
    head = text[:400]
    if len(GAMBLING.findall(text)) >= 2 and not AVIATION.search(head):
        return "gambling"
    if PARKED.search(head) or d == "hugedomains.com":
        return "parked_or_for_sale"
    if PLACEHOLDER.search(head):
        return "placeholder"
    if CHALLENGE.search(head):
        return "challenge" if not AVIATION.search(text) else ""
    if BROKEN.search(head) and not AVIATION.search(text[400:]):
        return "broken"
    if len(GAMBLING.findall(text)) >= 2:
        return "gambling_links_on_school_site"
    if len(text) > 150 and not AVIATION.search(text) and not d.endswith(".edu"):
        return "unrelated_candidate"
    return ""


# Hand decisions on the flagged domains (domain -> (lapsed reason, or "" for a false flag, note)). Each was read
# in the November page text `domains` prints, and by a homepage fetch when the text was ambiguous.
DOMAIN_HAND = {
    "aftcenter.com": ("gambling", "an Indonesian slot-game blog (Rubah4d)"),
    "aircombat.com": ("unrelated", "redirects to a French IPTV seller"),
    "airlinetravelcareers.com": ("placeholder", "the domain name repeated, nothing else"),
    "allmondaviation.com": ("gambling", "a Chinese sports-betting portal"),
    "charlestonhelicopters.com": ("parked_or_for_sale", "redirects to a domain reseller"),
    "cloudmakermedia.com": ("unrelated", "a musician's lyric-video page (our record: Keystone Flyers)"),
    "daytonaaviation.com": ("parked_or_for_sale", "domain for sale (DNmeta)"),
    "directwfc.com": ("gambling", "redirects to a restaurant page carrying a betting site"),
    "eaglevillesailplanes.com": ("unrelated", "a Japanese company-research blog"),
    "flybuzzair.com": ("unrelated", "redirects to a tool shop"),
    "hangglidetennessee.com": ("unrelated", "a youth-sports news site"),
    "kb-air.com": ("gambling", "a Chinese betting portal"),
    "lightsport.com": ("parked_or_for_sale", "domain for sale"),
    "maxairmech.com": ("unrelated", "an HVAC and plumbing contractor"),
    "modern-pilot.com": ("placeholder", "a web host's default page"),
    "niedra.com": ("placeholder", "a 2009 test blog post"),
    "ocsoaring.org": ("unrelated", "a general news blog"),
    "peaktopeakparagliding.com": ("gambling", "a crash-betting game page"),
    "raleighflyingclub.com": ("unrelated", "a graphic designer's portfolio"),
    "sanangeloflying.com": ("unrelated", "an Italian supplements shop"),
    "sierraflying.com": ("placeholder", "Squarespace: account not available"),
    "skydivesouthshore.com": ("gambling", "a slot-game site"),
    "skythrills.com": ("unrelated", "redirects to a French IPTV seller"),
    "tetonsoaring.org": ("unrelated", "a Thai business-services page"),
    "thunderboltair.com": ("unrelated", "redirects to a lifestyle blog (womanofmanyroles.com), fetched 2026-11-05"),
    "topfunskydiving.com": ("unrelated", "a spam blog"),
    "vegasextremeskydiving.com": ("gambling", "a slot-game site"),
    "yardscreeksoaring.com": ("gambling", "a Chinese betting portal"),
    # flagged by the rule, but not lapsed
    "atlantic.edu": ("", "a college; its casino words are its hospitality programs"),
    "skydivelaketahoe.com": ("", "the operator's own skydiving site, with gambling links injected"),
}


def nov_texts():
    """domain -> (text, url, source, date) of its November home page, from the production re-crawl and the
    2026-11 crawls of the added schools (57, 60, 67); and domain -> final domain of the re-crawl."""
    import page_text as pt
    build = _load("build21", "21_build_flight_line.py")
    nd = build.normalize_domain
    rec = PROD / "data/research-2026-11/recrawl"
    out, final = {}, {}
    for label, idx_p, raw_dir, kind in (
            ("recrawl-http", rec / "fleet_crawl_index.json", rec / "fleet_html_raw", "http"),
            ("recrawl-browser", rec / "fleet_browser_index.json", rec / "fleet_browser_html_raw", "browser"),
            ("google-http", R11 / "google/schools/crawl/fleet_crawl_index.json", R11 / "google/schools/crawl/fleet_html_raw", "http"),
            ("google-browser", R11 / "google/schools/crawl/fleet_browser_index.json", R11 / "google/schools/crawl/fleet_browser_html_raw", "browser")):
        if not idx_p.exists():
            continue
        for x in json.loads(idx_p.read_text()):
            d = nd(x.get("school_website", ""))
            pages = x.get("pages") or []
            home = next((p for p in pages if p.get("type") == "home"), pages[0] if pages else None)
            if not d or not home or d in out:
                continue
            if kind == "http":
                p = raw_dir / home["html_path"]
                if not p.exists():
                    p = raw_dir.parent / "fleet_html" / home["html_path"]
                text = pt.visible_text(p.read_text(errors="replace")) if p.exists() else ""
            else:
                rp = raw_dir / (home.get("raw_html_path") or "")
                tp = raw_dir.parent / "fleet_browser_text" / (home.get("text_path") or "")
                text = pt.visible_text(rp.read_text(errors="replace")) if home.get("raw_html_path") and rp.exists() \
                    else tp.read_text(errors="replace") if home.get("text_path") and tp.exists() else ""
            if len(text.strip()) >= 30:
                out[d] = (text, home["url"], label, (x.get("crawl_ts") or "2026-11")[:10])
    for f in ("crawl_attempts_http.jsonl", "crawl_attempts_browser.jsonl"):
        p = rec / f
        for line in (p.open() if p.exists() else []):
            x = json.loads(line)
            d = nd(x.get("website", ""))
            if d and x.get("final_domain") and x.get("status") == 200:
                final.setdefault(d, nd("https://" + x["final_domain"]))
    return out, final


def cmd_domains(a):
    fl = json.loads((OUT / "flight_line.json").read_text())
    doms = sorted({s["domain"] for s in fl if s["domain"]})
    texts, final = nov_texts()
    fetched_p = W / "domains_fetch.json"
    fetched = json.loads(fetched_p.read_text()) if fetched_p.exists() else {}
    missing = [d for d in doms if d not in texts]
    if a.fetch:  # one polite homepage GET for the domains no November crawl has text for
        import page_text as pt

        def get(d):
            try:
                req = urllib.request.Request(f"https://{d}/", headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=20) as r:
                    body = r.read(2_000_000).decode("utf-8", errors="replace")
                    return d, {"status": r.status, "final": r.geturl(), "text": pt.visible_text(body)[:20000]}
            except urllib.error.HTTPError as e:
                return d, {"status": e.code}
            except Exception as e:  # noqa: BLE001
                return d, {"status": 0, "error": type(e).__name__}
        todo = [d for d in missing if d not in fetched]
        with ThreadPoolExecutor(8) as ex:
            for d, r in ex.map(get, todo):
                fetched[d] = {**r, "retrieved": date.today().isoformat()}
        fetched_p.write_text(json.dumps(fetched) + "\n")
    for d, r in fetched.items():
        if d not in texts and len((r.get("text") or "").strip()) >= 30:
            texts[d] = (r["text"], r.get("final") or f"https://{d}/", "rc5-fetch", r["retrieved"])
            if r.get("final"):
                final.setdefault(d, site_dom(r["final"]))
    flags = []
    for d in doms:
        if d not in texts:
            continue
        text, url, src, when = texts[d]
        cat = domain_category(d, text[:20000])
        moved = final.get(d) and final[d].split(".")[-2:] != d.split(".")[-2:]
        if cat or d in DOMAIN_HAND:
            flags.append({"domain": d, "flag": cat, "url": url, "source": src, "retrieved": when,
                          "redirects_to": final.get(d, "") if moved else "", "text": " ".join(text[:20000].split())[:300]})
    lapsed = []
    for f in flags:
        reason, note = DOMAIN_HAND.get(f["domain"], (f["flag"] if f["flag"] in LAPSED_BY_RULE else None, "by rule"))
        if reason:
            lapsed.append({"domain": f["domain"], "reason": reason, "note": note, "evidence_url": f["url"],
                           "checked": f["retrieved"], "via": f["source"]})
    for d, (reason, note) in DOMAIN_HAND.items():  # decided by hand with no November text (a fresh fetch)
        if reason and d not in {x["domain"] for x in lapsed}:
            lapsed.append({"domain": d, "reason": reason, "note": note, "evidence_url": f"https://{d}/",
                           "checked": date.today().isoformat(), "via": "hand"})
    (W / "domain_flags.json").write_text(json.dumps(flags, indent=1) + "\n")
    LAPSED.write_text(json.dumps({"note": "school domains that now serve unrelated content; 38_schools.py leaves "
                                          "their schools out of the release (67_lead_schools.py domains)",
                                  "domains": sorted(lapsed, key=lambda x: x["domain"])}, indent=1) + "\n")
    in_fl = Counter(f["flag"] for f in flags)
    by_reason = defaultdict(list)
    for x in lapsed:
        by_reason[x["reason"]].append(x["domain"])
    stats(domains={"flight_line_domains": len(doms), "with_november_text": sum(1 for d in doms if texts.get(d) and texts[d][2] != "rc5-fetch"),
                   "fetched_now": sum(1 for d in doms if texts.get(d) and texts[d][2] == "rc5-fetch"),
                   "no_text": sum(1 for d in doms if d not in texts), "flags": dict(in_fl), "lapsed": len(lapsed),
                   "lapsed_by_reason": dict(Counter(x["reason"] for x in lapsed)),
                   "lapsed_domains": {k: sorted(v) for k, v in by_reason.items()},
                   "gambling_links_on_school_site": sorted(f["domain"] for f in flags if f["flag"] == "gambling_links_on_school_site")})
    print(f"{len(doms)} domains; November text {sum(1 for d in doms if d in texts)}; missing {len(missing)} "
          f"(fetched {len(fetched)}); flags {dict(in_fl)}; lapsed (hand) {len(lapsed)} -> {LAPSED.name}")
    for f in flags:
        if f["domain"] not in DOMAIN_HAND and (a.show == "all" or f["flag"] == a.show):
            print(f"  {f['domain']} [{f['flag']}] {f['redirects_to']} {f['url']} :: {f['text'][:200]}")


# ── Report ───────────────────────────────────────────────────────

DOC = REPO / "docs" / "hackers-2026-rc5-additions.md"
SOURCE_LABEL = {"tachgo": "Tach & Go directory", "ppp": "SBA PPP NAICS 611512 organizations",
                "master_public": "Master list, public-directory rows (Tach & Go or BestAviation)",
                "master_aopa": "Master list, AOPA-only rows (leads only)"}


def lead_buckets(l):
    out = set()
    for s in l["sources"]:
        if s == "master":
            out.add("master_aopa" if not (set(l["master_sources"]) & PUBLIC_MASTER) else "master_public")
        else:
            out.add(s)
    return out


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" if i == 0 else "---:" for i in range(len(head))) + "|"]
    out += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]
    return "\n".join(out)


def n(x):
    return f"{x:,}" if isinstance(x, int) else str(x)


NOTES = {  # narrative sections of the report; the numbers in them were measured on the runs named
    "gates": {"records_before": 5458, "list": [
        {"gate": "Every lead has exactly one disposition", "command": "$PY scripts/67_lead_schools.py dedupe",
         "result": "1,402 leads in 1,392 rows (exits 1 otherwise)"},
        {"gate": "Every rc5 school is in the rebuilt flight line with provenance; its name, phone and address are found "
                 "again on the page named; no AOPA source and no AOPA-only lead value",
         "command": "$PY scripts/67_lead_schools.py check", "result": "**PASS**: 156 schools, 5,458 to 5,614 records"},
        {"gate": "Pre-existing records unchanged (airport assignments included); nothing else new",
         "command": "$PY scripts/57_added_schools.py check --reference <rc4 flight_line.json>",
         "result": "**PASS**: 0 of 5,458 changed or missing, 156 new. Negative control: one airport changed in the "
                   "reference makes it FAIL on that school"},
        {"gate": "Hand check of 30 seeded additions, at least 25 correct",
         "command": "$PY scripts/67_lead_schools.py sample --n 30 --seed 20261106",
         "result": "round 1: 25 of 30 (rule tightened); round 2: **29 of 30**"},
        {"gate": "Airports for the additions: 20 seeded new matches read against their quotes, at least 18 correct",
         "command": "$PY scripts/67_lead_schools.py airports-sample --n 20 --seed 20261106 --before <file>",
         "result": "**20 of 20**"},
        {"gate": "Release build's own checks and the release check against rc4 and v0.3",
         "command": "research/FlightSchoolMapping/scripts/build_rc5.sh <commit>", "result": "**PASS**: pytest 29 passed; every stage ran, 42 wrote `v0.4-20261105-rc5: 33 files, 2354 MB`; "
                   "against rc4: 2,836 to 2,911 school pages, 0 control schools changed, 13 removed for a lapsed domain, "
                   "88 of 88 added schools with a fleet row or listing present, 317,972 tail records, 0 differing beyond "
                   "an added school's name; against v0.3 the same, PASS. Rebuilt after `67 airports` from 6520af5 "
                   "with the same results"},
    ]},
    "release": {"schools": 2911, "reference_schools": 2836, "added": 88, "removed": 13},
    "staged": [
        "`research/FlightSchoolMapping/output/added_schools_rc5.json`: the 156 additions, merged by "
        "`21_build_flight_line.py` (57's pattern: its own file, source `rc5_leads`).",
        "`research/FlightSchoolMapping/output/flight_line.json` rebuilt by 61, 21 and 22 (5,614 records), committed.",
        "`research/FlightSchoolMapping/output/lapsed_domains.json`: 140 domains whose schools 38 leaves out of the "
        "release.",
        "Release `v0.4-20261105-rc5` on trogdor in "
        "`/mnt/data/home/dew/dev/aether-addschools/research/FlightSchoolMapping/data/release/`, built by "
        "`scripts/build_rc5.sh` from a detached worktree (`aether-desc-build`) of the commit in its `BUILD.txt`. "
        "Ready for `36_publish_r2.py`; nothing was uploaded, deployed or pointed at. The first build (before "
        "`67 airports`) is kept beside it as `v0.4-20261105-rc5-pre-airports`.",
    ],
    "publish": """REL=v0.4-20261105-rc5
SRC=trogdor:/mnt/data/home/dew/dev/aether-addschools/research/FlightSchoolMapping/data/release/$REL
W=$(mktemp -d) && /usr/bin/rsync -a "$SRC/download/" "$W/download/"
python3 research/FlightSchoolMapping/scripts/36_publish_r2.py "$W/download" "$REL"
git fetch origin && git worktree add "$W/site" origin/hackers-airports --detach
cd "$W/site/website" && npm ci \\
  && /usr/bin/rsync -a --delete "$SRC/web/" public/x/ \\
  && /usr/bin/rsync -a "$SRC/schools_full.json" "$SRC/airports_full.json" src/data/explore/ \\
  && npm run build \\
  && npx -y wrangler@4 pages deploy dist --project-name aether-flight --branch main
printf '{"release": "%s"}' "$REL" > "$W/latest.json" && npx -y wrangler@4 r2 object put aether-public/latest.json \\
  --file "$W/latest.json" --content-type application/json --cache-control "public, max-age=60" --remote""",
    "rollback": """# rc4's site deployment (33eb2273, listed by `cf pages deployments list --project-name aether-flight` on
# 2026-11-05); re-check that it is still the one to return to before use
cf pages deployments rollback 33eb2273-64ed-43b8-8551-f27666555260 && T=$(mktemp) \\
  && printf '{"release": "v0.4-20261105-rc4"}' > "$T" && npx -y wrangler@4 r2 object put aether-public/latest.json \\
  --file "$T" --content-type application/json --cache-control "public, max-age=60" --remote""",
    "wrong": [
        "**Correction: \"105 of 156 additions have no airport\" was wrong.** It counted the staged file's "
        "`airport_code` only; 21 also takes airports from the fleet extraction, so the first rc5 build already had "
        "a valid airport on 97 of the 156 records (none with provenance, some with malformed identifiers such as "
        "`K3AU`). After `67 airports`: 137 of 156 have a valid airport, 131 of them with page-and-quote provenance "
        "(6 only from the extraction); 19 have none. On the release's pages, 81 of the 88 added schools list an "
        "airport (71 before).",
        "**Public-page schools carry almost nothing.** The 25 schools added from an airport or state page have a name "
        "and provenance but no phone or street address (by rule) and usually no city: the page rarely says, and the "
        "lead lists' states were not used for stored values.",
        "**Tails shared with records we already had** (26): most are FAA registrant records that look like leasebacks "
        "or owners (an LLC or a person); five are one tail shared with another school's website record "
        "(phx-afc.com and maf-club.com, bakersfieldflyingclub.com and chickadeeaviation.com, flyathena.com and "
        "savannahaviation.com, wetsuaviation.com and backcountryaviation.net, stuartflyingclub.org and "
        "rightrudderaviation.com). Left for review, not merged.",
        "**The web search was most of the spend.** $12.40 of the $13.51 went to 1,067 synchronous searches at "
        "standard price (web search has no batch discount); 260 of the searched leads had no public source and 151 "
        "only directory listings.",
        "**Clubs are judged two ways.** The site prompt (60's) answers \"no\" for a flying club whose pages describe "
        "aircraft access but no instruction, so such clubs were rejected (counted above); the public-page prompt "
        "accepts a club whose members fly club aircraft. Some public-page clubs may therefore be included that the "
        "site rule would have left out. One rule should be chosen for both.",
        "**140 school domains have lapsed**, far more than the two known cases; five live school sites carry "
        "injected gambling links and stay.",
        "**`build_rc5.sh` named a build directory that no longer existed** (`aether-desc-build`); it was recreated as "
        "a detached worktree of aether-addschools before the build.",
    ],
    "not_done": [
        "Leads found only on Facebook (25) are left out: our identified crawler gets a login page.",
        "The 49 ambiguous name pairs `dedupe` prints were read; the five closest "
        "(DoubleDay Aviation and Flight School of Greenville, FlyWaterloo and Fly There, McLaren Flight Academy and "
        "3 Amigos Flying Club, Sierra Aero and Tahoe Flying Club, Accelerated Flight Academy and Accelerated Flight "
        "School: four share an airport street address) have different phones and were kept as distinct; the rest are similar names in one state.",
    ],
    "commands": """# trogdor, /mnt/data/home/dew/dev/aether-addschools/research/FlightSchoolMapping
PY=/mnt/data/home/dew/dev/aether/research/FlightSchoolMapping/scripts/.venv/bin/python
S="$PY scripts/67_lead_schools.py"; R=data/research-2026-11/rc5
$S leads && $S search --max-usd 12.9 && $S search-report && $S targets
$PY scripts/09a_fleet_crawl.py --targets $R/targets.json --out-dir $R/crawl
$S browser-targets
$PY scripts/10_fleet_browser_crawl.py --crawl-only --no-evasion --user-agent "AetherResearch/1.0 (david@weekly.org)" ...
$S pages && $S classify-build
for r in classify classify_pages; do for c in estimate submit fetch; do $S $c --run $R/$r ...; done; done
$S classify-report && $S domains --fetch && $S dedupe && $S sample --n 30 --seed 20261106 && $S ingest
X="$PY scripts/extract_v2.py --env-file /mnt/data/home/dew/dev/aether/.env"
$X build --layout http --index $R/extract/http_index.json --pages-dir $R/crawl/fleet_html --run $R/extract/http
$X build --layout browser --pages-dir $R/extract/browser_text --meta-index $R/extract/browser_meta.json --run $R/extract/browser
for r in http browser; do $X submit --run $R/extract/$r --model claude-haiku-5-5 --effort medium
  $X fetch --run $R/extract/$r --model claude-haiku-5-5 --effort medium --wait
  $PY scripts/57_added_schools.py merge-extract --run $R/extract/$r --model claude-haiku-5-5; done
$PY scripts/61_contact_provenance.py report && $PY scripts/21_build_flight_line.py && $PY scripts/22_pdf_rate_sheets.py --offline
$S airports && $S airports-model-build && $S submit --run $R/airports_model --max-usd 1.5 \
  && $S fetch --run $R/airports_model && $S airports-model-report && $S airports
$S airports-sample --n 20 --seed 20261106 --before <added_schools_rc5.json before airports>
$PY scripts/21_build_flight_line.py && $PY scripts/22_pdf_rate_sheets.py --offline
$S check && $PY scripts/57_added_schools.py check --reference <rc4 flight_line.json>
bash scripts/build_rc5.sh <hackers-talk commit>
$S report""",
}


def batch_costs():
    """Message Batches spend, from each run's fetch events (batch prices, as haiku_batch and extract_v2 log them)."""
    out = {}
    for label, run in (("Classify sites (Message Batches, Haiku 5.5)", CLASSIFY_RUN),
                       ("Classify public pages (Message Batches)", PAGE_RUN),
                       ("Extract fleets and prices, HTTP pages (Message Batches)", EXTRACT / "http"),
                       ("Extract fleets and prices, browser pages (Message Batches)", EXTRACT / "browser"),
                       ("Airports for the additions, model step (Message Batches; $1.50 cap)", AIRPORT_RUN)):
        log = run / "log.jsonl"
        ev = [json.loads(x) for x in log.open()] if log.exists() else []
        fetched = [e for e in ev if e.get("event") == "fetch"]
        if fetched:
            out[label] = fetched[-1]["cost_usd_batch"]
    return out


def cmd_report(a):
    st = json.loads((W / "stats.json").read_text())
    for k, v in NOTES.items():
        st.setdefault(k, v)
    leads = {l["lead_id"]: l for l in json.loads((W / "leads.json").read_text())}
    rows = list(csv.DictReader(DISPOSITIONS.open()))
    drops = list(csv.DictReader((W / "drops.csv").open()))
    hand = list(csv.DictReader(HANDCHECK.open())) if HANDCHECK.exists() else []
    lapsed = json.loads(LAPSED.read_text())["domains"] if LAPSED.exists() else []
    gates = st.get("gates", {})
    rel = st.get("release", {})
    raw_rows = Counter()
    for r in csv.DictReader((INPUTS / "gap_candidates.csv").open()):
        if r["source"] in ("tachgo", "ppp"):
            raw_rows[r["source"]] += 1
    raw_rows["master"] = sum(1 for r in csv.DictReader((INPUTS / "c_master_matched.csv").open()) if r["verdict"] == "new")
    # per source: leads, added, duplicate, rejected, left out (a lead counts under each source it came from)
    per = defaultdict(Counter)
    for r in rows:
        for lid in r["lead_ids"].split(";"):
            for b in lead_buckets(leads[lid]):
                per[b][r["disposition"]] += 1
                per[b]["leads"] += 1
    added = [r for r in rows if r["disposition"] == "added"]
    added_by = Counter()
    for r in added:
        for b in set().union(*(lead_buckets(leads[lid]) for lid in r["lead_ids"].split(";"))):
            added_by[b] += 1
    only_aopa = sum(1 for r in added if all(leads[l]["aopa_only"] for l in r["lead_ids"].split(";")))
    ck, ing, srch, cls, dom = st.get("check", {}), st.get("ingest", {}), st.get("search", {}), st.get("classify", {}), st.get("domains", {})
    costs = batch_costs()
    total_cost = round(srch.get("cost_usd", 0) + sum(costs.values()), 2)
    hand_last = [h for h in hand if h["round"] == "2"] or hand
    hand_ok = sum(1 for h in hand_last if h["label"] == "correct")
    fl_before, fl_after = gates.get("records_before", 5458), ck.get("flight_line_records", "?")
    L = []
    L.append("# Hackers 2026 rc5: flight schools from the gap-source leads, each confirmed on its own pages\n")
    L.append(f"Generated by `research/FlightSchoolMapping/scripts/67_lead_schools.py report` on {date.today().isoformat()}; "
             "do not edit by hand. Stage: prototype work inside an MVP release (`docs/hackers-2026-plan.md`). One "
             "disposition per lead is in [`hackers-2026-rc5-additions.csv`](hackers-2026-rc5-additions.csv), the hand "
             "check in [`hackers-2026-rc5-handcheck.csv`](hackers-2026-rc5-handcheck.csv).\n")
    L.append(f"> **Headline.** {n(sum(raw_rows.values()))} rows from three lead lists came down to {n(len(leads))} "
             f"distinct leads after rule drops and de-duplication, and to **{n(len(added))} flight-training operations "
             f"added**, each confirmed on its own website ({n(ing.get('with_website', 0))}) or on another public page "
             f"that names it ({n(ing.get('from_public_page', 0))}). `flight_line.json` would go from {n(fl_before)} to "
             f"{n(fl_after)} records. By lead source (a school can have several): Tach & Go {n(added_by['tachgo'])}, "
             f"PPP {n(added_by['ppp'])}, master list public rows {n(added_by['master_public'])}, master list AOPA-only "
             f"rows {n(added_by['master_aopa'])} ({n(only_aopa)} reached only through an AOPA-only lead). The staged "
             f"release has **{n(rel.get('schools', '?'))} school pages** (rc4: {n(rel.get('reference_schools', '?'))}): "
             f"{n(rel.get('added', '?'))} added schools list a fleet or prices, and {n(rel.get('removed', '?'))} schools "
             f"whose domain now serves gambling, parking or other unrelated content were taken out. Hand check: "
             f"**{hand_ok} of {len(hand_last)}** additions correct, after a first round at 25 of 30 tightened the rule "
             f"for schools with no website. Cost **${total_cost:.2f}** of the $15 cap. Nothing is "
             f"published.\n")
    for note in st.get("callouts", []):
        L.append(f"> {note}\n")
    L.append("## Why\n")
    L.append("The gap-source passes (`hackers-2026-gap-sources.md`, `hackers-2026-master-list-check.md`) estimated "
             "that a few hundred real training operations on outside lists are missing from ours. This pass checks "
             "every one of those leads against the operator's own pages and adds the ones that hold up, for release "
             "rc5. Another approach would serve if it adds only operations confirmed from their own public pages, "
             "keeps everything AOPA-derived out of stored and published values, and leaves existing records "
             "unchanged.\n")
    L.append("## Leads and drops\n")
    drop_by = defaultdict(Counter)
    for d in drops:
        drop_by[d["reason"]][d["source"]] += 1
    L.append(table(["Source", "Rows", "Dropped by rule", "Leads (after de-duplication; a lead can come from several)"],
                   [["Tach & Go, unmatched training listings (65)", raw_rows["tachgo"],
                     sum(v["tachgo"] for v in drop_by.values()), per["tachgo"]["leads"]],
                    ["PPP NAICS 611512, unmatched organizations (65)", raw_rows["ppp"],
                     sum(v["ppp"] for v in drop_by.values()), per["ppp"]["leads"]],
                    ["Master list, verdict new", raw_rows["master"], sum(v["master"] for v in drop_by.values()),
                     f"{per['master_public']['leads']} public rows, {per['master_aopa']['leads']} AOPA-only"],
                    ["**All**", sum(raw_rows.values()), len(drops), f"**{len(leads)} distinct**"]]))
    L.append("\nDrop rules, applied to names before anything was fetched (`drop_reason` in the script):\n")
    L.append(table(["Rule", "Tach & Go", "PPP", "Master", "What it matches"],
                   [[k, drop_by[k]["tachgo"], drop_by[k]["ppp"], drop_by[k]["master"], {
                       "skydiving": "skydiving and parachute centers",
                       "airport_or_municipality": "airports, airport authorities, cities and counties with no club, "
                                                  "school or flying word in the rest of the name",
                       "jet_charter_fbo": "jet centers, charter operators and FBO chains with no training word",
                       "asa_dealer": "master-list rows whose only source is the ASA dealer list (pilot shops); an ASA "
                                     "dealer named as a school, club or academy stays a lead",
                       "club_in_formation": "clubs listed as \"in formation\""}[k]] for k in sorted(drop_by)]))
    L.append(f"\nDe-duplication: rows with the same website domain, or the same normalized name in the same state, "
             f"are one lead. {n(sum(1 for l in leads.values() if l['website']))} leads had a website on a list; the other "
             f"{n(srch.get('leads', 0))} were searched for.\n")
    L.append("## Method\n")
    L.append("1. **Search** (`67 search`), website-less leads only: Claude Haiku 5.5 with Anthropic's web search tool, "
             "one search per lead, `aopa.org` blocked, effort low, asked for the operator's own website or another "
             "public page that shows it (an airport page, an EAA chapter, an association list, a news story), with "
             "Facebook and directory pages reported but not used. A site the model named is crawled; its own pages "
             "decide. Lead names go into the search and into dedupe only.")
    L.append("2. **Crawl** the websites (from the lists and from search): `09a_fleet_crawl.py --targets --out-dir` with "
             "09a's identified user agent, then `10_fleet_browser_crawl.py --crawl-only --no-evasion --user-agent "
             "\"AetherResearch/1.0 (...)\"` for thin, refused or missing HTTP crawls (60's rule). The browser crawl "
             "sent the same identified user agent, not a desktop Chrome one (`--user-agent` is new in 10 for this). "
             "Nothing tried to get past a bot check; refusals and challenges are counted. Public pages found by "
             "search were fetched once each with 65's identified user agent.")
    L.append("3. **Classify** (`67 classify-build`, Message Batches, Haiku 5.5, effort medium): 60's prompt and schema "
             "on each site's own pages, named by domain only; a variant for the public pages that asks about the "
             "named business only. Every value returned (name, phone, street address, hours, city and state, the "
             "evidence quote) must be found verbatim on a page the request carried (`page_text.py`), or it is "
             "dropped; a public-page answer of \"yes\" without a verbatim quote becomes \"unclear\".")
    L.append("4. **Dedupe** (`67 dedupe`): 60's strict rules against `flight_line.json` (same domain or redirect, "
             "near-identical name in the state, same street address with a shared name word), then between leads. "
             "Domains already ours were dropped before crawling.")
    L.append("5. **Hand check**: a seeded random 30 of the additions read against their pages (`67 sample --n 30 "
             "--seed 20261106`).")
    L.append("6. **Add** (`67 ingest`): `output/added_schools_rc5.json`, a separate file that `21_build_flight_line.py` "
             "merges like 57's and 60's entries, with source `rc5_leads`, per-field provenance (`site` or "
             "`public_page`, the page URL and its crawl date) and the lead ids in `provenance` (public-list ids, and "
             "the master list's own entity ids; no AOPA values). Independent instructors get no street address (38's "
             "policy, now also reading this pass's CSV); public-page schools get no phone or street address.")
    L.append("7. **Extract** fleets and prices with `extract_v2.py` (the March prompt), Haiku 5.5, merged by "
             "`57 merge-extract`; `61_contact_provenance.py report` takes the verified contact values; 21 and 22 "
             "rebuild `flight_line.json`.\n")
    L.append("## What happened to every lead\n")
    disp = ["added", "duplicate", "rejected", "left_out"]
    L.append(table(["Lead source", "Leads"] + disp,
                   [[SOURCE_LABEL[b], per[b]["leads"]] + [per[b][d] for d in disp] for b in
                    ("tachgo", "ppp", "master_public", "master_aopa")]))
    L.append("\nCounts are leads; a lead on two lists counts under both. *left_out*: no public source was found or "
             "readable (below).\n")
    det, kinds = Counter(), Counter()
    for r in rows:
        if r["disposition"] in ("left_out", "rejected", "duplicate"):
            k = re.sub(r"\s*\(.*", "", r["detail"]) if r["disposition"] != "duplicate" else re.sub(
                r".*\((domain|name|street|at the|another lead|same phone)[^)]*\).*", r"\1", r["detail"])
            k = k.replace("only a other page", "only an archive, registry or other unofficial page")
            if r["disposition"] == "rejected" and k.startswith("not flight training:"):
                kinds[k.split(":", 1)[1].strip()] += len(r["lead_ids"].split(";"))
                continue
            det[(r["disposition"], k[:90])] += len(r["lead_ids"].split(";"))
    club = sum(v for k, v in kinds.items() if "club" in k.lower())
    det[("rejected", f"not flight training, by its own pages ({len(kinds)} kinds; flying clubs with no instruction "
                     f"{club}; the rest mostly FBOs, charter, maintenance and avionics shops)")] = sum(kinds.values())
    L.append(table(["Disposition", "Reason", "Leads"], [[d, k, v] for (d, k), v in sorted(det.items(), key=lambda x: (x[0][0], -x[1]))]))
    L.append("\n<details><summary>The kinds of business the rejected sites are</summary>\n")
    L.append(table(["Kind (the classifier's words)", "Leads"], [[k, v] for k, v in kinds.most_common()]))
    L.append("\n</details>\n")
    L.append("\n### Search (website-less leads)\n")
    oc = srch.get("outcomes", {})
    L.append(table(["Outcome", "Leads"], [[k, v] for k, v in sorted(oc.items(), key=lambda x: -x[1])]))
    L.append(f"\n{n(srch.get('web_searches', 0))} searches, {n(srch.get('tokens_in', 0))} input and "
             f"{n(srch.get('tokens_out', 0))} output tokens, ${srch.get('cost_usd', 0):.2f}. *own_site_not_in_results*: "
             "the model named a website that was not among the result URLs; those were crawled too, and their own "
             "pages decided. Facebook pages cannot be read by our crawler (it serves a login page to an identified "
             "user agent), so a lead found only on Facebook is left out and counted.\n")
    L.append("### Crawl and classification\n")
    tg, pg = st.get("targets", {}), st.get("pages", {})
    L.append(f"- Sites crawled: {n(tg.get('sites', 0))} ({n(tg.get('from_list', 0))} from the lists, "
             f"{n(tg.get('from_search', 0))} from search results, {n(tg.get('from_search_named', 0))} named by the "
             f"model); {n(tg.get('already_ours_before_crawl', 0))} more domains were ours already.")
    for k, v in st.get("crawl", {}).items():
        L.append(f"- {k}: {v}")
    L.append(f"- Public pages fetched: {pg.get('by_status', {})}.")
    L.append(f"- Classifier answers on sites: {cls.get('sites', {})}; types of the yes answers: {cls.get('site_types_yes', {})}; "
             f"on public pages: {cls.get('pages', {})}.\n")
    L.append("## Added schools\n")
    L.append(table(["", "Schools"], [["added", len(added)], ["with a website", ing.get("with_website", 0)],
                                      ["from a public page only (no website)", ing.get("from_public_page", 0)]] +
                   [[f"type: {k}", v] for k, v in sorted(ing.get("by_type", {}).items(), key=lambda x: -x[1])] +
                   [["list tails", ck.get("with_tails", "?")], ["list only makes and models", ck.get("models_only", "?")],
                    ["tail rows", ck.get("tail_rows", "?")], ["tails shared with a school we already had", ck.get("shared_tails", "?")]]))
    ap = st.get("airports", {})
    if ap:
        hw = ap.get("by_how", {})
        apc = list(csv.DictReader((REPO / "docs" / "hackers-2026-rc5-airport-check.csv").open())) \
            if (REPO / "docs" / "hackers-2026-rc5-airport-check.csv").exists() else []
        L.append("\n## Airports for the additions\n")
        L.append(f"`67 airports` gives each addition the airport its cited pages name, recorded in the school's "
                 f"`locations` with provenance (the page, the quoted text, how it was matched, and the row it matched). "
                 f"**{sum(hw.values())} of {ap.get('schools', 0)}** are matched (51 had an `airport_code` in the staged "
                 f"file before, all still the same airport); {ap.get('unmatched', 0)} are not. `21_build_flight_line.py` "
                 f"puts the match on the record: on the fleet extraction's location when that names the same "
                 f"airport, else first. In the rebuilt `flight_line.json`, 137 of the 156 records have a valid "
                 f"airport (97 in the first rc5 build), 131 with this provenance and 6 only from the extraction; 19 "
                 f"have none. Existing schools' airports are untouched (`57 check`).\n")
        L.append("Order of matching, each on text found again verbatim on a page the classifier read (no page was "
                 "refetched): (1) **code**, an identifier the pages give, alone or in parentheses after a name, found in "
                 "`output/airports.csv` and in the school's state; (2) **name**, an airport name the pages give whose "
                 "words (less generic ones such as Airport, Field, Regional) equal exactly one airport's in the state, "
                 "or a full airport name from the table for that state found on the pages when only one is; with no "
                 "state, only an exact full name unique in the whole table; (3) **city**, the city and state the "
                 "pages give with exactly one open public-use airport in FAA NASR (2026-10-29); (4) **model**, Haiku "
                 "5.5 choosing from the state's open airports, seaplane bases and gliderports, kept only if its quote "
                 "is on the page and names that candidate and no other. The state is the page's, else a public "
                 "(non-AOPA) lead list's, used for matching only. Stored identifiers use `airports.csv`'s "
                 "`airport_id` (Meadow Lake, FAA FLY, is `K00V`), as the rest of `flight_line.json` does.\n")
        L.append(table(["How matched", "Schools"], [[k, v] for k, v in sorted(hw.items(), key=lambda x: -x[1])] +
                       [["**none**", ap.get("unmatched", 0)]]))
        L.append("\n" + table(["Why no airport", "Schools"],
                               [[k, v] for k, v in sorted(ap.get("unmatched_why", {}).items(), key=lambda x: -x[1])]))
        if apc:
            ok = sum(1 for r in apc if r["label"] == "correct")
            L.append(f"\nHand check (`67 airports-sample --n 20 --seed 20261106`, 20 of the "
                     f"{ap.get('new_matches', '?')} schools that had no airport before, each quote read in context "
                     f"against the airport matched; labels in "
                     f"[`hackers-2026-rc5-airport-check.csv`](hackers-2026-rc5-airport-check.csv)): **{ok} of "
                     f"{len(apc)} correct** (gate: 18).\n")
    L.append("\n## Hand check\n")
    if hand:
        r1 = [h for h in hand if h["round"] == "1"]
        r2 = [h for h in hand if h["round"] == "2"]
        ok1, ok2 = (sum(1 for h in r if h["label"] == "correct") for r in (r1, r2))
        L.append(f"Seed 20261106, 30 additions each round, read against the pages the classifier read and a live fetch "
                 f"of the evidence page (our identified user agent). Gate: 25 of 30.\n")
        L.append(f"- **Round 1** (180 additions): {ok1} of {len(r1)} correct, at the gate. Four of the five misses were "
                 f"schools added from a news story (2013, 2018, 2021, and a 2023 story about a club still being "
                 f"formed); the fifth a maintenance shop whose owner is a CFI. The rule was tightened instead of "
                 f"shipping at the margin: a website-less school is added only from an airport, state-list, association "
                 f"or EAA page, or an official page in `PAGE_OK`; news, archives, registries and accident reports leave "
                 f"it out. The shop was rejected by hand.")
        L.append(f"- **Round 2** (the tightened set, re-drawn with the same seed): **{ok2} of {len(r2)} correct**. "
                 f"The miss, a youth aviation-education nonprofit with no current training offer, was rejected by hand.\n")
        for h in hand:
            if h["label"] != "correct":
                L.append(f"- round {h['round']}, {h['site'] or h['school_name']}: {h['note']}")
        L.append("")
    L.append("## School domains that now serve unrelated content\n")
    L.append(f"Every website domain in `flight_line.json` ({n(dom.get('flight_line_domains', 0))}) was checked against "
             f"its November home page: the production re-crawl and the 2026-11 crawls of added schools "
             f"({n(dom.get('with_november_text', 0))} domains), then one homepage fetch with the identified user agent "
             f"for the rest ({n(dom.get('fetched_now', 0))} with text; {n(dom.get('no_text', 0))} gave none). Rules "
             f"flag gambling text, parking and for-sale pages, site-builder placeholders, challenges, broken pages and "
             f"pages with no aviation words; the last group was read by hand (`DOMAIN_HAND`).\n")
    L.append(table(["Flag", "Domains"], [[k, v] for k, v in sorted(dom.get("flags", {}).items(), key=lambda x: -x[1])]))
    L.append(f"\n**{len(lapsed)} domains are lapsed** ({dict(Counter(x['reason'] for x in lapsed))}). They are listed "
             f"in `output/lapsed_domains.json`; their schools stay in `flight_line.json` and `38_schools.py` leaves them "
             f"out of the release, so the explorer links to none of them. Emptying the file undoes it. The two known "
             f"cases are among them: iflycfc.com (an Indonesian slot-game site) and thunderboltair.com (now redirects "
             f"to a lifestyle blog). Challenges and broken pages are not treated as lapsed. Sites that still show the "
             f"school with gambling links injected: {', '.join(dom.get('gambling_links_on_school_site', []))}.\n")
    L.append("<details><summary>The lapsed domains</summary>\n")
    L.append(table(["Domain", "Reason", "Note"], [[x["domain"], x["reason"], x["note"]] for x in lapsed]))
    L.append("\n</details>\n")
    L.append("## Gates\n")
    L.append("Run on trogdor from `/mnt/data/home/dew/dev/aether-addschools/research/FlightSchoolMapping` with "
             "`PY=/mnt/data/home/dew/dev/aether/research/FlightSchoolMapping/scripts/.venv/bin/python`.\n")
    L.append(table(["Gate", "Command", "Result"], [[g["gate"], f"`{g['command']}`", g["result"]] for g in gates.get("list", [])]))
    L.append("\n## Cost\n")
    L.append("The airport step's model call had its own $1.50 cap; the rest counts against the $15 cap.\n")
    L.append(table(["Item", "Recorded usage"], [["Web search pass (Haiku 5.5, standard price, $10 per 1,000 searches)",
                                                  f"${srch.get('cost_usd', 0):.2f}"]] +
                   [[k, f"${v:.2f}"] for k, v in costs.items()] + [["**Total**", f"**${total_cost:.2f}** of the $15 cap"]]))
    L.append("\n## Staged candidate (not published)\n")
    for x in st.get("staged", []):
        L.append(f"- {x}")
    L.append("\n## Publish (not run; David's call)\n")
    L.append("On monster4, from `/Users/dew/dev/aether-talk`. Downloads first, then the site, the pointer last, as for rc4:\n")
    L.append("```sh\n" + st.get("publish", "") + "\n```\n")
    L.append("## Rollback to rc4 (one command)\n")
    L.append("```sh\n" + st.get("rollback", "") + "\n```\n")
    L.append("## Things that look wrong (found on the way)\n")
    for x in st.get("wrong", []):
        L.append(f"- {x}")
    L.append("\n## Not done\n")
    for x in st.get("not_done", []):
        L.append(f"- {x}")
    L.append("\n## Reproduce\n")
    L.append("```sh\n" + st.get("commands", "") + "\n```")
    DOC.write_text("\n".join(L) + "\n")
    print(f"-> {DOC}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("leads")
    sp = sub.add_parser("search")
    sp.add_argument("--max-usd", type=float, required=True)
    sp.add_argument("--max-uses", type=int, default=1)
    sp.add_argument("--workers", type=int, default=6)
    sp.add_argument("--limit", type=int, default=0)
    for c in ("report", "search-report", "targets", "browser-targets", "pages", "classify-build", "classify-report", "dedupe",
              "ingest", "check", "airports", "airports-model-build", "airports-model-report"):
        sub.add_parser(c)
    dm = sub.add_parser("domains")
    dm.add_argument("--fetch", action="store_true", help="a homepage GET for domains with no November text")
    dm.add_argument("--show", default="unrelated_candidate", help="which flags to print (or all)")
    asm = sub.add_parser("airports-sample")
    asm.add_argument("--n", type=int, default=20)
    asm.add_argument("--seed", type=int, default=20261106)
    asm.add_argument("--before", required=True, help="added_schools_rc5.json as it was before `airports`")
    sm = sub.add_parser("sample")
    sm.add_argument("--n", type=int, default=30)
    sm.add_argument("--seed", type=int, default=20261106)
    sm.add_argument("--chars", type=int, default=2500)
    import haiku_batch as hb
    hb.add_cli(sub)
    a = ap.parse_args()
    if a.cmd in ("estimate", "submit", "fetch"):
        return hb.run_cli(a, env_file())
    {"leads": cmd_leads, "search": cmd_search, "search-report": cmd_search_report, "targets": cmd_targets,
     "browser-targets": cmd_browser_targets, "pages": cmd_pages, "classify-build": cmd_classify_build,
     "classify-report": cmd_classify_report, "dedupe": cmd_dedupe, "ingest": cmd_ingest, "check": cmd_check,
     "sample": cmd_sample, "domains": cmd_domains, "report": cmd_report, "airports": cmd_airports,
     "airports-model-build": cmd_airports_model_build, "airports-model-report": cmd_airports_model_report,
     "airports-sample": cmd_airports_sample}[a.cmd](a)


if __name__ == "__main__":
    main()
