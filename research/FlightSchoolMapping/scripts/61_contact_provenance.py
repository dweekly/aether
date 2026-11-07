#!/usr/bin/env python3
"""Contact details for every school, each with where it came from: output/contact_sourced.json.

For each school website in flight_line.json, Claude Haiku 5.5 reads the school's own cached pages (home,
location, about and contact pages; the November 2026 re-crawl first, then the March crawl) and copies out
its name, main phone, street address and opening hours with the URL each is on. A value is kept only if
it appears verbatim (normalized; page_text.py) on a page that request carried. Schools added from Google
Places (60_google_schools.py) were read the same way in their classification request, and those verified
values are used as they are.

Then, for any field the school's pages did not give, the next source in this order:
  fsai        FSAI member directory (pulled 2026-11-04), only for listings matched to the school by website:
              street address and phone
  state list  Michigan MDOT / Minnesota MnDOT / state education lists matched by website: city and state
FAA registry city and state, and the late-added list schools without a website, are filled in by
21_build_flight_line.py, which holds those records. AOPA is never a source here: its fields stay in
contact{} and are stripped from the release by 38_schools.py.

Each value: {"value", "source", "ref" (URL or record id), "retrieved" (date)}.

Steps (on trogdor, from research/FlightSchoolMapping):
  build      one request per school website with cached pages -> data/research-2026-11/contact/run/
  estimate | submit --max-usd N | fetch   (haiku_batch.py)
  report     verify, add fallbacks -> output/contact_sourced.json, and coverage per field and source
  sample     a seeded random N of the accepted phones and street addresses with the page text around
             each, for the hand check

This reads flight_line.json only to choose which websites to send (cost); its output is keyed by
website domain, so it does not change when other schools are added. Run it by hand, like 08e.
"""

import argparse
import csv
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
import haiku_batch as hb  # noqa: E402
import page_text as pt  # noqa: E402

OUT = ROOT / "output"
R11 = ROOT / "data" / "research-2026-11"
WORK = R11 / "contact"
RUN = WORK / "run"
DEST = OUT / "contact_sourced.json"
PROD = Path("/mnt/data/home/dew/dev/aether/research/FlightSchoolMapping")  # read-only page caches
REC = PROD / "data/research-2026-11/recrawl"
CAPS = {"home": 9000, "*": 4000}
MAX_CHARS = 20000
CONTACTY = re.compile(r"contact|about|location|directions|hours|find-us|visit", re.I)
USER = """\
Business website: {site}

Copy these details of the business from its web pages below.
{contact}

Web pages:
{pages}"""
FSAI_PULLED = "2026-11-04"
AVIATION = re.compile(r"flight|flying|aviation|pilot|aircraft|airport|airplane|helicopter|glider|soaring|cfi", re.I)


def nd(url):
    import importlib.util
    global _nd
    if "_nd" not in globals():
        spec = importlib.util.spec_from_file_location("b21", SCRIPTS / "21_build_flight_line.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _nd = m.normalize_domain
    return _nd(url)


def caches():
    """(label, domain -> pages), freshest first."""
    return [
        ("2026-11 recrawl", pt.http_pages(REC / "fleet_crawl_index.json", REC / "fleet_html_raw", "2026-11 recrawl", nd,
                                          cleaned_dir=REC / "fleet_html")),
        ("2026-11 recrawl retry", pt.http_pages(REC / "retry/fleet_crawl_index.json", REC / "retry/fleet_html_raw",
                                                "2026-11 recrawl retry", nd, cleaned_dir=REC / "retry/fleet_html")),
        ("2026-11 recrawl browser", pt.browser_pages(REC / "fleet_browser_index.json", REC / "fleet_browser_html_raw",
                                                     REC / "fleet_browser_text", "2026-11 recrawl browser", nd)),
        ("2026-11 added", pt.http_pages(R11 / "added/crawl/fleet_crawl_index.json", R11 / "added/crawl/fleet_html_raw",
                                        "2026-11 added", nd)),
        ("2026-11 added browser", pt.browser_pages(R11 / "added/crawl/fleet_browser_index.json",
                                                   R11 / "added/crawl/fleet_browser_html_raw",
                                                   R11 / "added/crawl/fleet_browser_text", "2026-11 added browser", nd)),
        ("2026-03 crawl", pt.http_pages(PROD / "output/fleet_crawl_index.json", PROD / "output/fleet_html_raw",
                                        "2026-03 crawl", nd, cleaned_dir=PROD / "output/fleet_html")),
        ("2026-03 expansion", pt.expansion_pages(PROD / "output/expansion_crawl.jsonl", PROD / "output/fleet_html",
                                                 "2026-03 expansion", nd)),
        ("2026-03 browser", pt.browser_pages(PROD / "output/fleet_browser_index.json", PROD / "output/fleet_browser_html_raw",
                                             PROD / "output/fleet_browser_text", "2026-03 browser", nd)),
    ]


def pick(pages):
    """Home and contact-like pages, freshest copy of each URL, home first within each cache."""
    best, order = {}, []
    for pg in pages:
        if pg["type"] not in ("home", "location", "about") and not CONTACTY.search(pg["url"]):
            continue
        k = re.sub(r"^https?://(www\.)?", "", pg["url"].split("#")[0].rstrip("/").lower())
        if k in best:
            continue
        pg["text"] = pt.read_page(pg)
        if len(pg["text"].strip()) < 30 or not AVIATION.search(pg["text"]):
            continue  # an empty page, or a lapsed domain now serving something else
        best[k] = pg
        order.append(pg)
    order.sort(key=lambda p: (p["cache"][:7] != "2026-11", p["type"] != "home"))
    keep, total = [], 0
    for p in order:
        if total >= MAX_CHARS:
            break
        keep.append(p)
        total += min(len(p["text"]), CAPS.get(p["type"], CAPS["*"]))
    return keep


def google_added():
    p = ROOT.parent.parent / "docs" / "hackers-2026-google-schools.csv"
    if not p.exists():
        return set()
    with open(p) as f:
        return {r["school_key"] for r in csv.DictReader(f) if r["disposition"] == "added"}


def rc5_added():
    p = ROOT.parent.parent / "docs" / "hackers-2026-rc5-additions.csv"
    if not p.exists():
        return set()
    with open(p) as f:
        return {r["school_key"] for r in csv.DictReader(f) if r["disposition"] == "added" and r["site"]}


def cmd_build(a):
    fl = json.loads((OUT / "flight_line.json").read_text())
    want = sorted({s["domain"] for s in fl if s["domain"]} - google_added())
    by = defaultdict(list)
    for label, c in caches():
        print(f"  {label}: {len(c):,} sites")
        for d, pgs in c.items():
            by[d] += pgs
    reqs, index, none = [], [], []
    for i, d in enumerate(want):
        keep = pick(by.get(d, []))
        block, sent = pt.pages_block(keep, CAPS)
        if not sent:
            none.append(d)
            continue
        cid = f"c{i:05d}"
        reqs.append({"custom_id": cid, "params": pt.params(USER.format(site=d, contact=pt.CONTACT_QUESTIONS, pages=block),
                                                           pt.CONTACT_PROPS, "low")})
        index.append({"custom_id": cid, "domain": d,
                      "pages": [{k: p[k] for k in ("url", "path", "kind", "type", "retrieved", "cache")}
                                for p in keep if p["url"] in dict(sent)]})
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "no_pages.json").write_text(json.dumps(none, indent=1) + "\n")
    hb.write_run(RUN, reqs, index, {"what": "61 contact", "prompt": USER + pt.CONTACT_QUESTIONS, "caps": CAPS,
                                    "max_chars": MAX_CHARS})
    print(f"{len(want):,} school websites; {len(reqs):,} requests; {len(none):,} with no cached page text")


def _sent(entry):
    pgs = []
    for p in entry["pages"]:
        p = dict(p)
        p["text"] = pt.read_page(p)
        pgs.append(p)
    return pt.pages_block(pgs, CAPS)[1]


FIELDS = {"name": "name", "phone": "phone", "street_address": "address", "hours": "hours"}


def cmd_report(a):
    out, how = {}, Counter()
    # 1. The school's own pages, read by this run
    index = json.loads((RUN / "index.json").read_text())
    ans = hb.answers(RUN)
    for e in index:
        x = ans.get(e["custom_id"])
        if x is None:
            how["request_failed"] += 1
            continue
        retrieved = {p["url"]: (p["retrieved"], p["cache"]) for p in e["pages"]}
        v = pt.verify_contact(x, _sent(e))
        rec = {}
        for f, name in FIELDS.items():
            how[f"{name}:{v[f]['how']}"] += 1
            if v[f]["value"]:
                r, cache = retrieved[v[f]["ref"]]
                rec[name] = {"value": v[f]["value"], "source": "site", "ref": pt.clean_url(v[f]["ref"]),
                             "retrieved": r, "cache": cache}
        if v["city_state"]["value"]:
            r, cache = retrieved[v["city_state"]["ref"]]
            rec["city_state"] = {"value": v["city_state"]["value"], "source": "site",
                                 "ref": pt.clean_url(v["city_state"]["ref"]), "retrieved": r, "cache": cache}
        home = [p for p in e["pages"] if p["type"] == "home"] or e["pages"]
        rec["website"] = {"value": pt.clean_url(home[0]["url"]), "source": "site", "ref": pt.clean_url(home[0]["url"]),
                          "retrieved": home[0]["retrieved"], "cache": home[0]["cache"]}
        out[e["domain"]] = rec
    # 2. Schools added from Google Places (60) and from the gap-source leads (67): their classification
    # request read the same pages the same way
    for gp, added, label in ((R11 / "google/schools/classified.json", google_added(), "2026-11 google"),
                             (R11 / "rc5/classified.json", rc5_added(), "2026-11 rc5")):
        if not gp.exists():
            continue
        for r in json.loads(gp.read_text()):
            d = nd("https://" + r["site"])
            if d not in added or r.get("flight_instruction") != "yes":
                continue
            rec = {}
            for f, name in FIELDS.items():
                c = r["contact"][f]
                if c["value"]:
                    rec[name] = {"value": c["value"], "source": "site", "ref": pt.clean_url(c["ref"]),
                                 "retrieved": r["retrieved"].get(c["ref"], ""), "cache": label}
            cs = r["contact"]["city_state"]
            if cs["value"]:
                rec["city_state"] = {"value": cs["value"], "source": "site", "ref": pt.clean_url(cs["ref"]),
                                     "retrieved": r["retrieved"].get(cs["ref"], ""), "cache": label}
            first = next(iter(r["retrieved"]), "")
            rec["website"] = {"value": pt.clean_url(first), "source": "site", "ref": pt.clean_url(first),
                              "retrieved": r["retrieved"].get(first, ""), "cache": label}
            out[d] = rec
            how["google_added" if label.endswith("google") else "rc5_added"] += 1
    # 3. FSAI listings matched to a school by website: street address and phone where the site gave none
    fsai = {x["id"]: x for x in json.loads((R11 / "fsai/fsai_listings.json").read_text())}
    with open(R11 / "fsai/c_fsai_matched.csv") as f:
        for r in csv.DictReader(f):
            d = r["school_domain"]
            if r["match_type"] != "domain" or not d or r["fsai_id"] not in fsai:
                continue
            x, rec = fsai[r["fsai_id"]], out.setdefault(d, {})
            ref = f"FSAI directory listing {r['fsai_id']}"
            if not rec.get("address") and x.get("street"):
                val = ", ".join(v for v in (x["street"], x.get("city", ""), f"{x.get('state', '')} {x.get('zip', '')}".strip()) if v)
                rec["address"] = {"value": val, "source": "fsai", "ref": ref, "retrieved": FSAI_PULLED}
                how["address:fsai"] += 1
            if not rec.get("phone") and pt.phone10(x.get("phone", "")):
                rec["phone"] = {"value": x["phone"], "source": "fsai", "ref": ref, "retrieved": FSAI_PULLED}
                how["phone:fsai"] += 1
    # 4. State lists matched by website: city and state
    st = R11 / "states/matched"
    for fname, lst, pulled in (("c_mi_mdot_matched.csv", "mi_mdot", "2026-11-03"),
                               ("c_mn_mndot_matched.csv", "mn_mndot", "2026-11-03"),
                               ("c_state_edu_matched.csv", "state_edu", "2026-11-03")):
        with open(st / fname) as f:
            for i, r in enumerate(csv.DictReader(f)):
                d = r["school_domain"]
                if r["match_type"] != "domain" or not d or not r["city"] or not r["state"]:
                    continue
                rec = out.setdefault(d, {})
                if not rec.get("city_state"):
                    rec["city_state"] = {"value": f"{r['city']}, {r['state']}", "source": lst,
                                         "ref": f"{lst} row{i}", "retrieved": pulled}
                    how[f"city_state:{lst}"] += 1
    DEST.write_text(json.dumps({"note": "contact details with provenance, from 61_contact_provenance.py; "
                                        "never AOPA or Google content", "schools": dict(sorted(out.items()))},
                               indent=1, sort_keys=False) + "\n")
    cov = Counter()
    for d, rec in out.items():
        for f, v in rec.items():
            cov[(f, v["source"])] += 1
    print(f"{len(out):,} websites -> {DEST.name}")
    print("verification:", dict(sorted(how.items())))
    print("coverage (field, source):", dict(sorted(cov.items())))
    u = json.loads((RUN / hb.MODEL / "usage.json").read_text())
    print(f"usage: {u['tokens_in']:,} in, {u['tokens_out']:,} out, ${u['cost_usd_batch']}")


def cmd_sample(a):
    """A seeded random N of the accepted site phones and street addresses, with 300 characters of page text
    around each, for checking by hand that the value is the school's own."""
    data = json.loads(DEST.read_text())["schools"]
    index = {e["domain"]: e for e in json.loads((RUN / "index.json").read_text())}
    pool = [(d, f, v) for d, rec in sorted(data.items()) for f, v in sorted(rec.items())
            if f in ("phone", "address") and v["source"] == "site" and d in index]
    smp = random.Random(a.seed).sample(pool, a.n)
    for d, f, v in smp:
        text = {pt.clean_url(u): t for u, t in _sent(index[d])}.get(v["ref"], "")
        if f == "phone":
            p = pt.phone10(v["value"])
            m = next((m for m in pt.PHONE.finditer(text) if "".join(m.groups()) == p), None)
            at = m.start() if m else 0
        else:
            at = max(0, pt.norm(text).find(pt.norm(v["value"])))  # approximate offset in the raw text
            at = text.lower().find(v["value"].split(",")[0].lower()[:12]) if at else at
        print(f"\n=== {d} {f}: {v['value']!r}  ({v['ref']}, {v['retrieved']})")
        print("   ", " ".join(text[max(0, at - 200):at + 200].split()))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env-file", default="/mnt/data/home/dew/dev/aether/.env")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sub.add_parser("report")
    s = sub.add_parser("sample")
    s.add_argument("--n", type=int, default=30)
    s.add_argument("--seed", type=int, default=20261105)
    hb.add_cli(sub)
    a = ap.parse_args()
    if a.cmd in ("estimate", "submit", "fetch"):
        return hb.run_cli(a, a.env_file)
    {"build": cmd_build, "report": cmd_report, "sample": cmd_sample}[a.cmd](a)


if __name__ == "__main__":
    main()
