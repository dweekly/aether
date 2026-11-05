#!/usr/bin/env python3
"""Add flight schools found on outside lists after the March build, with provenance.

Like 15_cirrus_ctc_ingest.py --add-missing, schools go into
output/flight_schools_enriched.json with a "source" field; each also carries a
"provenance" list (which list, its record id, the list's date, the date pulled), which
21_build_flight_line.py reads and copies onto the school's flight_line record.

Sources (docs/hackers-2026-added-schools.md):
  fsai      FSAI member directory: entries hand-labelled school whose homepage showed
            flight training (56_fsai_directory.py, docs/hackers-2026-fsai-labels.csv)
  state     Michigan MDOT and Minnesota MnDOT flight-school licenses and the state
            education-board lists: rows the matcher left unmatched
  federal   SEVP flight schools, VA-approved flight facilities, IPEDS colleges with
            49.0102/49.0108 awards: rows the matcher left unmatched
  unlisted  schools and clubs found from the registry, confirmed by their own tails on
            their site, whose domain was on no March list (55_unlisted_schools.py)

Steps, from research/FlightSchoolMapping/ (data under data/research-2026-11/, git-ignored):
  candidates     every candidate row from the sources -> added/candidates.csv
  sitecheck      fetch each candidate website once, as 56 does -> added/site_checks.json
  (hand)         one disposition per candidate in docs/hackers-2026-added-schools.csv
  ingest         'added' rows -> output/flight_schools_enriched.json (merged by school, so a
                 rerun replaces only these entries) and added/targets.json for 09a --targets
  merge-extract  an extract_v2 run's results for the added domains -> output/added_extract.jsonl
                 (merged by domain and layout; other lines kept)
  check          gates on the rebuilt output/flight_line.json against the pre-change copy
  release-check  gates on a release built from it against one built the same way from the
                 pre-change flight_line.json

Usage:
  57_added_schools.py candidates
  57_added_schools.py sitecheck
  57_added_schools.py ingest
  57_added_schools.py merge-extract --run RUN_DIR --model claude-haiku-5-5
  57_added_schools.py check --reference OLD_FLIGHT_LINE
  57_added_schools.py release-check --release RC --reference-release CONTROL
"""

import argparse
import csv
import importlib.util
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
REPO = ROOT.parent.parent
OUT = ROOT / "output"
R11 = ROOT / "data" / "research-2026-11"
WORK = R11 / "added"
DISPOSITIONS = REPO / "docs" / "hackers-2026-added-schools.csv"
ENRICHED = OUT / "flight_schools_enriched.json"
RC5 = OUT / "added_schools_rc5.json"  # 67_lead_schools.py's additions, in their own file


def late_entries():
    """Every late-added entry: 57's and 60's (in flight_schools_enriched.json) and 67's (added_schools_rc5.json)."""
    out = [e for e in json.loads(ENRICHED.read_text()) if e.get("provenance")]
    return out + ([e for e in json.loads(RC5.read_text()) if e.get("provenance")] if RC5.exists() else [])
ADDED_EXTRACT = OUT / "added_extract.jsonl"
SOURCE = "hackers_2026_added"


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build = _load("build21", "21_build_flight_line.py")
normalize_domain = build.normalize_domain  # the key 21 uses, so dedupe and build agree

# list name -> (source group, list date, date pulled); dates from the research docs
LISTS = {
    "fsai_directory": ("fsai", "2026-11-04", "2026-11-04"),
    "mi_mdot": ("state", "2026-05", "2026-11-03"),
    "mn_mndot": ("state", "2024-06-04", "2026-11-03"),
    "state_edu": ("state", "various", "2026-11-03"),
    "sevp": ("federal", "2026-10-28", "2026-11-03"),
    "va_weams": ("federal", "2026-10-08", "2026-11-03"),
    "ipeds": ("federal", "2024", "2026-11-03"),
    "unlisted_registry": ("unlisted", "2026-11-03", "2026-11-03"),
}
CANDIDATE_FIELDS = ["candidate_id", "source", "list", "record_id", "list_date", "pulled", "name", "city", "state",
                    "airport", "website", "matcher"]
DISPOSITION_FIELDS = CANDIDATE_FIELDS + ["disposition", "school_key", "school_name", "school_website",
                                         "school_airport", "detail"]


def read_csv(p):
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


def write_csv(p, rows, fields):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def row(list_name, record_id, name, city, state, airport="", website="", matcher=""):
    src, list_date, pulled = LISTS[list_name]
    return {"candidate_id": f"{list_name}:{record_id}", "source": src, "list": list_name, "record_id": record_id,
            "list_date": list_date, "pulled": pulled, "name": " ".join((name or "").split()), "city": city or "",
            "state": state or "", "airport": airport or "", "website": (website or "").strip().rstrip(";"),
            "matcher": matcher}


UNMATCHED = ("none", "colocated")
WEBMAIL = {"gmail.com", "yahoo.com", "me.com", "charter.net", "ameritech.net", "aol.com", "hotmail.com",
           "outlook.com", "icloud.com", "comcast.net", "att.net", "sbcglobal.net"}


def cmd_candidates(a):
    rows = []
    # 1. FSAI: the hand-labelled schools whose homepage answered and showed training text.
    fsai = R11 / "fsai"
    labels = {r["fsai_id"]: r["label"] for r in read_csv(REPO / "docs" / "hackers-2026-fsai-labels.csv")}
    checks = json.loads((fsai / "fsai_site_checks.json").read_text())
    for r in read_csv(fsai / "c_fsai_matched.csv"):
        chk = checks.get(r["website"]) or {}
        if r["match_type"] in UNMATCHED and labels.get(r["fsai_id"]) == "school" \
                and chk.get("state") == "ok" and chk.get("training"):
            rows.append(row("fsai_directory", r["fsai_id"], r["name"], r["city"], r["state"],
                            website=r["website"], matcher=r["match_type"]))
    # 2. State lists: every row the matcher left unmatched.
    st = R11 / "states" / "matched"
    for i, r in enumerate(read_csv(st / "c_mi_mdot_matched.csv")):
        if r["match_type"] in UNMATCHED:
            site = r["website"].rstrip(";")  # Michigan lists an email address; this is its domain
            rows.append(row("mi_mdot", f"row{i}", r["name"], r["city"], r["state"], r.get("airport"),
                            "" if site in WEBMAIL else site, r["match_type"]))
    for i, r in enumerate(read_csv(st / "c_mn_mndot_matched.csv")):
        if r["match_type"] in UNMATCHED:
            apt = re.search(r"airport=([^;]*)", r.get("evidence", ""))
            rows.append(row("mn_mndot", f"row{i}", r["name"], r["city"], r["state"], apt.group(1).strip() if apt else "",
                            r["website"], r["match_type"]))
    for i, r in enumerate(read_csv(st / "c_state_edu_matched.csv")):
        if r["match_type"] in UNMATCHED:
            rows.append(row("state_edu", f"{r['state']}-row{i}", r["name"], r["city"], r["state"],
                            website=r["website"], matcher=r["match_type"]))
    # 3. Federal lists. SEVP is per campus: a school is a candidate when none of its campuses matched.
    by_school = defaultdict(list)
    for r in read_csv(st / "c_sevp_matched.csv"):
        by_school[r["name"].split(" / ")[0].strip()].append(r)
    for name, rs in sorted(by_school.items()):
        if all(r["match_type"] in UNMATCHED for r in rs):
            r = rs[0]
            rows.append(row("sevp", r["campus_code"], name, r["city"], r["state"], website=r["website"],
                            matcher=r["match_type"]))
    for r in read_csv(st / "c_va_matched.csv"):
        if r["match_type"] in UNMATCHED and r["approved"] == "True":
            rows.append(row("va_weams", r["facility_code"], r["name"], r["city"], r["state"], website=r["website"],
                            matcher=r["match_type"]))
    for r in read_csv(st / "c_ipeds_matched.csv"):
        if r["match_type"] in UNMATCHED and (int(r["awards_490102"] or 0) or int(r["awards_490108"] or 0)):
            rows.append(row("ipeds", r["unitid"], r["name"].split(" / ")[0], r["city"], r["state"],
                            website=r["website"], matcher=r["match_type"]))
    # 4. Unlisted: confirmed schools and clubs whose site no March list held.
    for s in json.loads((R11 / "unlisted" / "scores.json").read_text()):
        if s["status"] == "confirmed" and s["operation_type"] in ("school", "club") and not s["in_march_list"]:
            rows.append(row("unlisted_registry", s["registrant"], s.get("operator_name") or s["registrant"], "", "",
                            s.get("home") or "", s.get("website") or s.get("domain"), "not on March lists"))
    ids = [r["candidate_id"] for r in rows]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        sys.exit(f"FAIL duplicate candidate ids: {sorted(dup)}")
    write_csv(WORK / "candidates.csv", rows, CANDIDATE_FIELDS)
    counts = defaultdict(int)
    for r in rows:
        counts[r["list"]] += 1
    print(f"{len(rows)} candidates -> {WORK / 'candidates.csv'}: {dict(counts)}")


def cmd_sitecheck(a):
    """Every candidate website and every proposed school website, fetched once with 56's check."""
    m56 = _load("fsai56", "56_fsai_directory.py")
    path = WORK / "site_checks.json"
    cache = json.loads(path.read_text()) if path.exists() else {}
    urls = {r["website"] for r in read_csv(WORK / "candidates.csv") if "." in r["website"]}
    if DISPOSITIONS.exists():
        urls |= {r["school_website"] for r in read_csv(DISPOSITIONS) if r.get("school_website")}
    for u in sorted(urls):
        if u not in cache:
            res = m56.check_site(u, cache)
            print(f"  {res['state']:7} {str(res.get('status')):4} training={res['training']!s:5} {u}", flush=True)
            res["checked"] = datetime.now(timezone.utc).date().isoformat()
            path.write_text(json.dumps(cache, indent=1, sort_keys=True))
    print(f"{len(urls)} sites; {sum(1 for u in urls if cache[u]['state'] == 'ok')} answered")


def load_dispositions():
    rows = read_csv(DISPOSITIONS)
    cand = {r["candidate_id"] for r in read_csv(WORK / "candidates.csv")}
    have = [r["candidate_id"] for r in rows]
    missing, extra = cand - set(have), set(have) - cand
    blank = [r["candidate_id"] for r in rows if r["disposition"] not in ("added", "duplicate", "rejected")]
    if missing or extra or blank or len(have) != len(set(have)):
        sys.exit(f"FAIL dispositions: {len(missing)} candidates without one {sorted(missing)[:5]}, "
                 f"{len(extra)} not candidates {sorted(extra)[:5]}, {len(blank)} blank {blank[:5]}")
    return rows


def school_key(r):
    d = normalize_domain(r["school_website"])
    return d or "nosite:" + re.sub(r"[^a-z0-9]+", "-", r["school_name"].lower()).strip("-") + ":" + r["state"]


def cmd_ingest(a):
    rows = load_dispositions()
    fl = json.loads((OUT / "flight_line.json").read_text())
    ours = {s["domain"] for s in fl if s.get("domain")}
    groups = defaultdict(list)
    for r in rows:
        if r["disposition"] == "added":
            k = school_key(r)
            if r["school_key"] != k:
                sys.exit(f"FAIL {r['candidate_id']}: school_key {r['school_key']!r} does not match its website ({k!r})")
            groups[k].append(r)
    entries, targets = [], []
    for k, rs in sorted(groups.items()):
        lead = rs[0]
        if not k.startswith("nosite:") and k in ours:
            sys.exit(f"FAIL {k} is already a school in flight_line.json (dispose as duplicate)")
        names = {r["school_name"] for r in rs}
        if len(names) > 1:
            sys.exit(f"FAIL {k} has several school names: {names}")
        e = {"name": lead["school_name"], "website": lead["school_website"], "city": lead["city"],
             "state": lead["state"], "state_name": lead["state"], "airport_code": lead["school_airport"],
             "source": SOURCE,
             "provenance": [{"list": r["list"], "record_id": r["record_id"], "list_date": r["list_date"],
                             "pulled": r["pulled"], "name_on_list": r["name"]} for r in rs]}
        entries.append(e)
        if lead["school_website"]:
            targets.append({k2: e[k2] for k2 in ("name", "website", "airport_code", "city", "state_name")})
    enriched = json.loads(ENRICHED.read_text())
    kept = [e for e in enriched if e.get("source") != SOURCE]
    ENRICHED.write_text(json.dumps(kept + entries, indent=2) + "\n")
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "targets.json").write_text(json.dumps(targets, indent=1) + "\n")
    by_src = defaultdict(set)
    for k, rs in groups.items():
        for r in rs:
            by_src[r["source"]].add(k)
    print(f"{len(entries)} schools ({len(targets)} with a website) from {sum(len(v) for v in groups.values())} rows "
          f"-> {ENRICHED.name} ({len(enriched) - len(kept)} replaced); per source: "
          + ", ".join(f"{s} {len(v)}" for s, v in sorted(by_src.items())))


def answer_json(message):
    text = "".join(b.get("text", "") for b in message.get("content", []) if b.get("type") == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def cmd_merge_extract(a):
    run = Path(a.run)
    meta = json.loads((run / "run.json").read_text())
    index = {e["custom_id"]: e for e in json.loads((run / "index.json").read_text())}
    added = {normalize_domain(e["website"]) for e in late_entries() if e.get("website")}  # 57's, 60's and 67's
    new, skipped = {}, defaultdict(int)
    for line in (run / a.model / "results.jsonl").open():
        r = json.loads(line)
        meta_i = index[r["custom_id"]]
        domain = normalize_domain(meta_i["school_website"]) or meta_i["domain"]
        if domain not in added:
            sys.exit(f"FAIL {r['custom_id']} is for {domain}, which is not an added school")
        if r["result"]["type"] != "succeeded":
            skipped[r["result"]["type"]] += 1
            continue
        data = answer_json(r["result"]["message"])
        if data is None:
            skipped["unparsed"] += 1
            continue
        data["_index_school_name"] = meta_i["school_name"]
        new[(domain, meta["layout"])] = {"domain": domain, "layout": meta["layout"], "run": str(run),
                                         "model": a.model, "custom_id": r["custom_id"], "ts": meta["built_at"],
                                         "data": data}
    old = [json.loads(l) for l in ADDED_EXTRACT.open()] if ADDED_EXTRACT.exists() else []
    kept = [x for x in old if (x["domain"], x["layout"]) not in new]
    out = sorted(kept + list(new.values()), key=lambda x: (x["domain"], x["layout"]))
    tmp = ADDED_EXTRACT.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(x) + "\n" for x in out))
    tmp.rename(ADDED_EXTRACT)
    print(f"{len(new)} results merged ({len(old) - len(kept)} replaced, {len(kept)} kept); skipped {dict(skipped)}; "
          f"{ADDED_EXTRACT.name} now {len(out)} lines")


# Fields 21 adds to every record (provenance per field, public contact, place IDs). The gates compare
# pre-existing records with these removed: "unchanged except added fields".
ADDED_FIELDS = ("field_sources", "public_contact", "google_place_ids", "inferred_locations")
RELEASE_ADDED_FIELDS = ("google_place_ids", "contact", "aopa_url", "listed_airports", "inferred_airports")
# Added to tail records (web/t) after rc3: NTSB records of the same airframe (35, from 63 --tails).
RELEASE_ADDED_TAIL_FIELDS = ("ntsb",)


# Added to each school's fleet rows and listings after rc3: the school's own description of the aircraft.
RELEASE_ADDED_ROW_FIELDS = ("description",)


def strip(rec, fields):
    return {k: v for k, v in rec.items() if k not in fields}


def strip_release(rec):
    """A schools_full.json record without the fields added since the control releases."""
    out = strip(rec, RELEASE_ADDED_FIELDS)
    for k in ("fleet", "listings"):
        if k in out:
            out[k] = [strip(r, RELEASE_ADDED_ROW_FIELDS) for r in out[k]]
    return out


def key_of(s):
    return s["domain"] or f"{s['name']}|{(s.get('contact') or {}).get('address', '')}"


def cmd_check(a):
    """Gates. 1: every added school is in flight_line.json with its provenance. 2: every school in the
    reference is present and byte-identical (JSON), and nothing else is new."""
    new = [strip(s, ADDED_FIELDS) for s in json.loads((OUT / "flight_line.json").read_text())]
    ref = [strip(s, ADDED_FIELDS) for s in json.loads(Path(a.reference).read_text())]
    entries = late_entries()  # 57's, 60's and 67's
    fails = []
    nk = defaultdict(list)
    for s in new:
        nk[key_of(s)].append(s)
    for e in entries:
        d = normalize_domain(e["website"])
        hits = [s for s in new if s.get("provenance") == e["provenance"] and (s["domain"] == d)]
        if len(hits) != 1:
            fails.append(f"added school {e['name']} ({d or 'no site'}): {len(hits)} records with its provenance")
    rk = defaultdict(list)
    for s in ref:
        rk[key_of(s)].append(s)
    changed = 0
    for k, rs in rk.items():
        a_ = sorted(json.dumps(x, sort_keys=True) for x in rs)
        b_ = sorted(json.dumps(x, sort_keys=True) for x in nk.get(k, []))
        if a_ != b_:
            changed += 1
            if changed <= 5:
                fails.append(f"pre-existing school changed or missing: {k}")
    extra = [s for k, ss in nk.items() if k not in rk for s in ss]
    if any(not s.get("provenance") for s in extra):
        fails.append(f"{sum(1 for s in extra if not s.get('provenance'))} new records without provenance")
    print(f"reference {len(ref):,} schools, rebuilt {len(new):,}; added entries {len(entries)}; "
          f"new records {len(extra)}; pre-existing changed or missing {changed}")
    print(f"added schools with a fleet row: {sum(1 for s in extra if s['fleet'])}; "
          f"tails: {sum(s['n_number_count'] for s in extra)}; "
          f"rows with a wet rate: {sum(1 for s in extra for f in s['fleet'] if f.get('wet_rate_hr'))}")
    if len(new) != len(ref) + len(extra):
        fails.append(f"count: {len(new)} != {len(ref)} + {len(extra)}")
    if len(extra) != sum(1 for e in entries if not any(s.get("provenance") == e["provenance"] for s in ref)):
        fails.append(f"{len(extra)} new records, but {len(entries)} added entries of which some were in the reference")
    if fails:
        print("FAIL\n  " + "\n  ".join(fails))
        sys.exit(1)
    print("PASS")


def cmd_release_check(a):
    """Gates on the release. 1: every school in the control release is in the candidate,
    identical. 2: the candidate's other schools are exactly the added schools that have a
    fleet row or listing, each named from its own site. 3: every tail record (web/t) is
    unchanged except that a tail on an added school's fleet may name that school in "sch"
    (its "recent" legs compared as a set: 35 does not order legs within a day)."""
    rel = ROOT / "data" / "release"
    rc, ref = rel / a.release, rel / a.reference_release
    new_full = {s["key"]: s for s in json.loads((rc / "schools_full.json").read_text())["schools"]}
    new = {k: strip_release(s) for k, s in new_full.items()}
    old = {s["key"]: strip_release(s) for s in json.loads((ref / "schools_full.json").read_text())["schools"]}
    fl = json.loads((OUT / "flight_line.json").read_text())
    want = {s["domain"] for s in fl if s.get("provenance") and s["domain"]
            and any(f.get("n_number") or f.get("display_name") for f in s["fleet"])} - set(old)
    fails = []
    # schools whose domain now serves unrelated content leave the release (67 domains, 38): they must be gone
    lp = OUT / "lapsed_domains.json"
    lapsed = {d["domain"] for d in json.loads(lp.read_text())["domains"]} if lp.exists() else set()
    removed = [k for k in old if k in lapsed]
    if any(k in new for k in removed):
        fails.append(f"schools with a lapsed domain still in the release: {[k for k in removed if k in new][:5]}")
    changed = [k for k in old if k not in lapsed and json.dumps(old[k], sort_keys=True) != json.dumps(new.get(k), sort_keys=True)]
    if changed:
        fails.append(f"{len(changed)} control schools changed or missing, e.g. {changed[:3]}")
    extra = set(new) - set(old)
    if extra != want:
        fails.append(f"new schools {sorted(extra - want)[:5]} not added; added {sorted(want - extra)[:5]} missing")
    unnamed = sorted(k for k in extra if new[k]["name_source"] == "domain")
    # The added fields: place IDs are strings; contact values each carry a source, ref and date, never AOPA
    for k, x in new_full.items():
        bad = [f for f, v in (x.get("contact") or {}).items()
               if not (v.get("value") and v.get("source") and v.get("ref")) or v["source"] in ("aopa", "google_places")]
        if x.get("aopa_url") and not re.fullmatch(r"https://www\.aopa\.org/destinations/business/\d+", x["aopa_url"]):
            fails.append(f"{k}: aopa_url is not a bare AOPA Airports business link: {x['aopa_url']}")
            break
        if bad or not all(isinstance(g, str) and g for g in x.get("google_place_ids", [])):
            fails.append(f"{k}: contact fields without provenance or from AOPA/Google {bad}, or bad place IDs")
            break
    def tails(r):
        """Every tail record in the shards index.json points at (35 does not delete shards an
        earlier export split differently, so web/t can hold stale files the explorer never reads)."""
        split = json.loads((r / "web" / "index.json").read_text()).get("split", {})
        out, stale = {}, 0
        for p in (r / "web" / "t").glob("*.json"):
            key, _, part = p.stem.partition("-")
            live = (key in split and part.isdigit() and int(part) < split[key]) or (key not in split and not part)
            if live:
                out.update(json.loads(p.read_text()))
            else:
                stale += 1
        print(f"  {r.name}: {stale} stale shard files not read")
        return out
    t_new, t_old = tails(rc), tails(ref)
    added_tails = {f["n"] for k in extra for f in new[k]["fleet"]}
    t_diff, t_tie = [], []
    for n in set(t_new) | set(t_old):
        a_, b_ = dict(t_new.get(n) or {}), dict(t_old.get(n) or {})
        for x in (a_, b_):  # sch rows may carry [listed_as, description] after the first six (35, after rc3)
            if "sch" in x:
                x["sch"] = [r[:6] for r in x["sch"]]
            for f in RELEASE_ADDED_TAIL_FIELDS:
                x.pop(f, None)
        if lapsed and "sch" in b_:  # a school removed for its lapsed domain leaves its tails' sch lists
            b_["sch"] = [x for x in b_["sch"] if x[0] not in lapsed]
            if not b_["sch"] and "sch" not in a_:
                b_.pop("sch")
        if n in added_tails:  # its "sch" list may now name an added school; nothing else may change
            a_["sch"] = [x for x in a_.get("sch", []) if x[0] not in extra]
            b_.setdefault("sch", [])
            a_.setdefault("sch", [])
        for x in (a_, b_):  # 35 orders a day's legs by day only, so ties can swap between runs
            if "recent" in x:
                x["recent"] = sorted(x["recent"], key=json.dumps)
        if a_ != b_:
            # 35 keeps a tail's last 8 legs by row_number() over start_ts; legs with tied start times are
            # picked arbitrarily, so the last leg kept can differ between two runs on identical inputs
            # (shown for N4084H, docs/hackers-2026-google-schools.md). Counted apart, not as a change.
            ra, rb = (t_new.get(n) or {}).get("recent", []), (t_old.get(n) or {}).get("recent", [])
            if {k: v for k, v in a_.items() if k != "recent"} == {k: v for k, v in b_.items() if k != "recent"} \
                    and len(ra) == len(rb) and ra[:-1] == rb[:-1]:
                t_tie.append(n)
                continue
            t_diff.append(n)
    if t_tie:
        print(f"  {len(t_tie)} tails differ only in the last of their recent legs (35's tie at the cut): {sorted(t_tie)[:5]}")
    if t_diff:
        fails.append(f"{len(t_diff)} tail records differ beyond an added school in their sch list, e.g. {sorted(t_diff)[:3]}")
    print(f"control {len(old):,} schools, candidate {len(new):,}; removed for a lapsed domain {len(removed)}; "
          f"control schools changed {len(changed)}; "
          f"added schools in the release {len(extra)} of {len(want)} with a fleet row or listing "
          f"({len(unnamed)} named only by domain: {unnamed[:5]}); tails {len(t_new):,} vs {len(t_old):,}, "
          f"{len(added_tails)} on added fleets, differing otherwise {len(t_diff)}")
    for k in sorted(extra):
        s = new[k]
        print(f"  {k:32} {s['name'][:36]:36} {s['airport'] or '-':5} fleet {s['fleet_listed']:2} flew {s['fleet_flew']:2} "
              f"listings {s['listings_without_tail']:2} wet median {s['wet_rate_median']}")
    if fails:
        print("FAIL\n  " + "\n  ".join(fails))
        sys.exit(1)
    print("PASS")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("candidates")
    sub.add_parser("sitecheck")
    sub.add_parser("ingest")
    m = sub.add_parser("merge-extract")
    m.add_argument("--run", required=True)
    m.add_argument("--model", default="claude-haiku-5-5")
    c = sub.add_parser("check")
    c.add_argument("--reference", required=True)
    rc = sub.add_parser("release-check")
    rc.add_argument("--release", required=True)
    rc.add_argument("--reference-release", required=True)
    a = ap.parse_args()
    {"release-check": cmd_release_check, "candidates": cmd_candidates, "sitecheck": cmd_sitecheck, "ingest": cmd_ingest,
     "merge-extract": cmd_merge_extract, "check": cmd_check}[a.cmd](a)


if __name__ == "__main__":
    main()
