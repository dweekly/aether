"""Flight School Association International (FSAI, formerly FSANA) member directory vs our school list.

Input is the directory as pulled on 2026-11-04 from FSAI's public search page
(https://mms.fsai.org/members/directory/search_bootstrap.php?org_id=FSAN) by running the page's own
search in a browser: one empty search (every listing), then one search per Classification and per
Type, which tags each listing (Product Category alone is ignored by the server). The page's results endpoint refuses requests made
outside the page, so the pull ran in the page; the browser snippet is
scripts/probes/fsai_directory_pull.js. The raw JSON stays outside git (data/ is ignored): listings
include individual members at what may be home addresses.

  python scripts/56_fsai_directory.py RAW_JSON OUT_DIR [--md docs/hackers-2026-fsai-directory.md]

Steps: parse each listing (name, contact, street, city, state, ZIP, phone, website, tags); classify it
as a training provider, a vendor, or an individual; collapse listings repeated under several member
ids (same name, street and city); write OUT_DIR/c_fsai.csv for match_external_school_lists.py, run
the matcher against output/flight_line.json, and write the summary doc.

Gates: the empty search must return at least 500 listings; every tagged id must be among them (a tag
search that finds a listing the full list missed means the full pull was incomplete); and every
unmatched entry must carry a hand label in docs/hackers-2026-fsai-labels.csv.
"""
import argparse, collections, csv, importlib.util, json, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT.parent.parent
MATCHER = HERE / "probes" / "match_external_school_lists.py"

US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
                "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC PR VI GU AS MP".split())
# Tags that mark a listing as offering flight training (classification, category or type labels).
TRAINING = re.compile(r"flight school|part 141|part 61|part 142|private pilot|instrument pilot|commercial pilot|"
                      r"sport pilot|recreational pilot|multi-engine pilot|glider pilot|certified flight instructor|"
                      r"airline transport pilot|career pilot training|accelerated training|accredited flight school|"
                      r"aircraft rental|tail wheel|seaplane|upset|aviation degree|aerocamp|foreign student", re.I)
STATE_NAMES = {"ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA",
               "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE", "FLORIDA": "FL", "GEORGIA": "GA",
               "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
               "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA",
               "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT",
               "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM",
               "NEW YORK": "NY", "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
               "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
               "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT",
               "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
               "PUERTO RICO": "PR"}
# Names that read as flight training, for listings that carry no tags at all.
SCHOOL_NAME = re.compile(r"aviation|flight|flying|\bfly|aero|academy|pilot|aeronautic|helicopter|wings|"
                         r"university|college|soaring|airways|\bair\b|airborne", re.I)
NOT_SCHOOL = re.compile(r"association|insurance|law\b|consult|software|simulat|radar|engine|motivational|"
                        r"knowledge|accounting|parts|supply|supplies|chat", re.I)
ORG_WORD = re.compile(r"aviation|flight|fly|flying|aero|air|academy|school|college|university|institute|"
                      r"inc\b|llc|corp|company|co\.|ltd|club|center|centre|services|training|pilot|wings|"
                      r"jet|heli|sky|field|airport|group|association|foundation", re.I)


def norm_state(s):
    """'FL', 'Florida', or the directory's three-letter forms ('FLA', 'CON', 'GEO', 'ARI') -> 'FL'."""
    s = s.strip().upper().rstrip(".")
    if s in US_STATES:
        return s
    if s in STATE_NAMES:
        return STATE_NAMES[s]
    hits = {c for n, c in STATE_NAMES.items() if len(s) >= 3 and n.startswith(s)}
    return hits.pop() if len(hits) == 1 else s


def parse_city_line(line):
    """'Lafayette, LA 70598' -> (city, state, zip). ZIPs lose leading zeros in the source ('7035')."""
    m = re.match(r"^(.*?),\s*([A-Za-z .]+?)\s+([A-Za-z0-9\- ]{3,10})$", line.strip())
    if m:
        z = m.group(3).strip()
        if re.fullmatch(r"\d{3,4}", z):
            z = z.zfill(5)
        return m.group(1).strip(), m.group(2).strip().upper(), z
    m = re.match(r"^(.*?),\s*([A-Za-z .]+)$", line.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip().upper(), ""
    return "", "", ""


def load(raw_path):
    raw = json.loads(Path(raw_path).read_text())
    members, tags = raw["members"], raw["tags"]
    tag_of = collections.defaultdict(set)
    for label, ids in tags.items():
        if label == "ALL":
            continue
        for i in ids:
            tag_of[str(i)].add(label)
    rows = []
    for mid, m in members.items():
        addr = [a for a in m.get("address", []) if a]
        city = state = zipc = ""
        street = []
        for a in addr:
            c, s, z = parse_city_line(a)
            if c and (s in US_STATES or len(s) == 2 or z):
                city, state, zipc = c, norm_state(s), z
            else:
                street.append(a)
        t = sorted(tag_of.get(str(mid), ()))
        name = m.get("name", "").strip()
        is_training = any(TRAINING.search(x.split(":", 1)[1]) for x in t)
        # A listing with no organization word in its name and no contact line is an individual member.
        individual = not ORG_WORD.search(name) and not m.get("second")
        if individual:
            kind = "individual"
        elif is_training:
            kind = "training"
        elif t:
            kind = "vendor"
        elif SCHOOL_NAME.search(name) and not NOT_SCHOOL.search(name):
            kind = "school_name"  # no tags, but the name reads as flight training
        else:
            kind = "untagged"
        rows.append({"id": str(mid), "name": name, "contact": m.get("second", ""), "street": "; ".join(street),
                     "city": city, "state": state, "zip": zipc, "phone": m.get("phone", ""),
                     "website": m.get("website", ""), "tags": t, "kind": kind,
                     "us": state in US_STATES})
    return raw, rows


def collapse(rows):
    """Listings repeated under several member ids: same normalized name, street and city."""
    key = lambda r: (re.sub(r"\W+", "", r["name"].lower()), re.sub(r"\W+", "", r["street"].lower()),
                     r["city"].lower())
    groups = collections.OrderedDict()
    for r in sorted(rows, key=lambda r: int(r["id"]) if r["id"].isdigit() else 0):
        groups.setdefault(key(r), []).append(r)
    out = []
    for g in groups.values():
        r = dict(g[0])
        r["ids"] = [x["id"] for x in g]
        r["tags"] = sorted(set().union(*[set(x["tags"]) for x in g]))
        r["website"] = next((x["website"] for x in g if x["website"]), "")
        kinds = [x["kind"] for x in g]
        r["kind"] = next((k for k in ("training", "school_name", "vendor", "untagged", "individual") if k in kinds),
                         r["kind"])
        out.append(r)
    return out


def run_matcher(csv_path, out_dir):
    fl = ROOT / "output" / "flight_line.json"
    ap = f"{ROOT / 'output' / 'airports.csv'},{ROOT / 'data' / 'ourairports_airports.csv'}"
    p = subprocess.run([sys.executable, "-I", str(MATCHER), str(fl), ap, str(out_dir), str(csv_path)],
                       capture_output=True, text=True, check=True)
    with open(Path(out_dir) / "c_fsai_matched.csv") as f:
        return p.stdout.strip(), list(csv.DictReader(f))


TRAINING_TEXT = re.compile(r"flight training|flight school|flight lessons?|learn to fly|discovery flight|"
                           r"intro(ductory)? flight|private pilot|instrument rating|flight instruct|cfi\\b|"
                           r"pilot training|rental|ground school|checkride|part 141|part 61", re.I)


def check_site(url, cache):
    """Fetch a homepage once (cached): is it live, and does it talk about flight training?
    A certificate error is retried over plain HTTP. Challenge and refusal pages (202 with no text,
    401/402/403/409/429/503) count as blocked; nothing tries to get past them."""
    import urllib.request
    if url in cache:
        return cache[url]
    u = url if re.match(r"^https?://", url, re.I) else "http://" + url
    res = None
    for attempt in (u, re.sub(r"^https://", "http://", u, flags=re.I)):
        try:
            req = urllib.request.Request(attempt, headers={"User-Agent": "Mozilla/5.0 (aether flight-school research)"})
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read(400_000).decode("utf-8", "ignore")
                text = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style)\b.*?</(script|style)>", " ", body))
                words = len(text.split())
                res = {"status": r.status, "final": r.geturl(), "training": bool(TRAINING_TEXT.search(text)),
                       "chars": words, "state": "blocked" if (r.status == 202 and words < 20) else "ok"}
            break
        except urllib.error.HTTPError as e:
            res = {"status": e.code, "training": False,
                   "state": "blocked" if e.code in (401, 402, 403, 409, 429, 503) else "down",
                   "error": f"HTTP {e.code}"}
            break
        except Exception as e:  # noqa: BLE001 - DNS, TLS, timeout
            res = {"status": None, "training": False, "state": "down", "error": f"{type(e).__name__}: {str(e)[:120]}"}
            if "CERTIFICATE" not in str(e) and "SSL" not in str(e):
                break
    cache[url] = res
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw")
    ap.add_argument("out_dir")
    ap.add_argument("--md", default=str(REPO / "docs" / "hackers-2026-fsai-directory.md"))
    ap.add_argument("--labels", default=str(REPO / "docs" / "hackers-2026-fsai-labels.csv"),
                    help="Hand labels for the entries the matcher did not place, keyed by FSAI member id "
                         "(individuals' names are blanked)")
    a = ap.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    raw, rows = load(a.raw)
    all_ids = set(map(str, raw["tags"].get("ALL", [])))
    tagged_ids = {str(i) for k, v in raw["tags"].items() if k != "ALL" for i in v}
    missing = tagged_ids - all_ids
    gate_n = len(all_ids) >= 500
    gate_cover = not missing
    listings = collapse(rows)
    (out / "fsai_listings.json").write_text(json.dumps(listings, indent=1))

    # Candidates: tagged training providers and untagged entries whose names read as flight training,
    # in the US or with no address (state unknown).
    cands = [r for r in listings if r["kind"] in ("training", "school_name") and (r["us"] or not r["state"])]
    train_us = [r for r in listings if r["kind"] == "training" and r["us"]]
    with open(out / "c_fsai.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source", "name", "city", "state", "website", "address", "fsai_id", "basis", "tags"])
        for r in cands:
            w.writerow(["c_fsai", r["name"], r["city"], r["state"], r["website"],
                        f"{r['street']}, {r['city']}, {r['state']} {r['zip']}".strip(", "),
                        r["ids"][0], "tags" if r["kind"] == "training" else "name", "|".join(r["tags"])])
    summary, matched = run_matcher(out / "c_fsai.csv", out)

    # The matcher compares names within a state; entries with no address get a nationwide exact-name check.
    spec = importlib.util.spec_from_file_location("mx", MATCHER)
    mx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mx)
    schools = mx.load_schools(str(ROOT / "output" / "flight_line.json"),
                              f"{ROOT / 'output' / 'airports.csv'},{ROOT / 'data' / 'ourairports_airports.csv'}")
    by_variant = collections.defaultdict(list)
    for sc in schools:
        for v in sc["variants"]:
            by_variant[v].append(sc)
    for m in matched:
        if m["match_type"] == "none" and not m["state"]:
            hit = next((by_variant[v][0] for v in mx.variants(m["name"]) if by_variant.get(v)), None)
            if hit:
                m["match_type"], m["school_name"], m["match_score"] = "exact_any_state", hit["name"], "1.00"
    with open(out / "c_fsai_matched.csv", "w", newline="") as f:
        dw = csv.DictWriter(f, fieldnames=list(matched[0].keys()))
        dw.writeheader()
        dw.writerows(matched)

    tag_counts = collections.Counter(t for r in listings for t in r["tags"])
    kinds = collections.Counter(r["kind"] for r in listings)
    MATCHED = ("domain", "exact", "address", "fuzzy", "exact_any_state")
    new = [m for m in matched if m["match_type"] not in MATCHED]
    by_id = {r["ids"][0]: r for r in cands}

    labels = {}
    if Path(a.labels).exists():
        with open(a.labels) as f:
            labels = {r["fsai_id"]: r["label"] for r in csv.DictReader(f)}
    cache_path = out / "fsai_site_checks.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    unlabeled = [m for m in new if m["fsai_id"] not in labels]
    for m in new:
        m["label"] = labels.get(m["fsai_id"], "unlabeled")
        m["site"] = check_site(m["website"], cache) if m["label"] == "school" and m["website"] else {}
        if m["label"] == "school":
            st = m["site"].get("state")
            m["verdict"] = ("confirmed" if m["site"].get("training") else
                            "blocked" if st == "blocked" else "site down" if st == "down" or not st
                            else "no training text")
    cache_path.write_text(json.dumps(cache, indent=1))
    confirmed = [m for m in new if m.get("verdict") == "confirmed"]
    gate_labels = not unlabeled

    def mcount(basis, which):
        return sum(1 for m in matched if m["basis"] == basis and (m["match_type"] in MATCHED) == which)

    def tagged(label_re):
        return sum(1 for r in train_us if any(re.search(label_re, t, re.I) for t in r["tags"]))

    n_match = sum(1 for m in matched if m["match_type"] in MATCHED)
    L = []
    w = L.append
    w("# FSAI member directory vs our flight-school list")
    w("")
    w("Generated by `research/FlightSchoolMapping/scripts/56_fsai_directory.py`; do not edit by hand.")
    w("")
    w(f"> **Headline.** The Flight School Association International's public directory lists {len(all_ids):,} "
      f"member entries ({len(listings):,} after collapsing repeats). {len(cands):,} are US (or unaddressed) "
      f"flight-training providers, {mcount('tags', True) + mcount('tags', False):,} by their own tags and "
      f"{mcount('name', True) + mcount('name', False):,} by name alone. {n_match:,} match a school already in our "
      f"list; {len(new):,} do not.")
    w("")
    w("## Source and method")
    w("")
    w("- FSAI was the Flight School Association of North America (FSANA) until February 2026. Its "
      "directory is linked as \"School search\" on fsai.org and needs no login. It lists members only, "
      "so it is a sample of schools that pay dues, not a census.")
    w("- Pulled 2026-11-04 by running the page's own search in a browser: one empty search (every entry), "
      "then one search per Classification and per Type to tag entries (the server ignores Product Category "
      "on its own, so it was not used). The results endpoint "
      "refuses requests made outside the page, so the pull ran in the page at about one request a second.")
    w("- Kinds: *training* if any tag is a flight-school classification or a training type (Part 141, "
      "Part 61, private pilot and so on); *vendor* if tagged only with other things (software, financing, "
      "examiners, consultants); *school_name* if untagged but the name reads as flight training; "
      "*individual* if the name has no organization word and there is no contact line; *untagged* otherwise. "
      "Individuals' names and addresses are not reproduced here.")
    w("- Training and school_name entries in the US, or with no address at all, were matched against "
      "`output/flight_line.json` with `probes/match_external_school_lists.py` (domain, exact name, street "
      "address, fuzzy name within a state; the same rules as the state lists in "
      "`docs/hackers-2026-state-registries.md`). Entries with no address get a nationwide exact-name check "
      "(`exact_any_state`).")
    w("")
    w("## Gates")
    w("")
    w("| Gate | Value | Result |")
    w("|---|---|---|")
    w(f"| Empty search returns at least 500 entries | {len(all_ids):,} | {'pass' if gate_n else 'FAIL'} |")
    w(f"| Every tagged entry is in the full list | {len(missing)} missing | {'pass' if gate_cover else 'FAIL'} |")
    w(f"| Every unmatched entry has a hand label | {len(unlabeled)} unlabeled | {'pass' if gate_labels else 'FAIL'} |")
    w("")
    w("## What is in it")
    w("")
    w("| Kind | Listings | US | No address |")
    w("|---|---|---|---|")
    for k in ("training", "school_name", "vendor", "individual", "untagged"):
        xs = [r for r in listings if r["kind"] == k]
        w(f"| {k} | {len(xs):,} | {sum(r['us'] for r in xs):,} | {sum(not r['state'] for r in xs):,} |")
    w("")
    w(f"Of the {len(train_us):,} tagged training providers in the US, "
      f"{tagged(r'Part 141')} are tagged Part 141, {tagged(r'Part 61')} Part 61, "
      f"{tagged(r'Accredited Flight School')} accredited, {tagged(r'International Students|Foreign Student')} "
      f"take international students, {tagged(r'Veterans')} take veterans' benefits and "
      f"{tagged(r'AeroCamp')} run an AeroCamp. Tags are self-reported by members.")
    w("")
    w("Most common tags:")
    w("")
    w("| Tag | Listings |")
    w("|---|---|")
    for t, n in tag_counts.most_common(25):
        w(f"| {t.split('#')[0]} | {n:,} |")
    w("")
    w("## Matching against our 4,825 schools")
    w("")
    w(f"Matcher output: `{summary.splitlines()[-1]}`")
    w("")
    w("| Match rule | By tags | By name only |")
    w("|---|---|---|")
    for k in ("domain", "exact", "exact_any_state", "address", "fuzzy", "colocated", "none"):
        a_ = sum(1 for m in matched if m["match_type"] == k and m["basis"] == "tags")
        b_ = sum(1 for m in matched if m["match_type"] == k and m["basis"] == "name")
        w(f"| {k} | {a_:,} | {b_:,} |")
    w("")
    w("`colocated` means another tenant at the same airport address, not the same school, so it counts as "
      "not matched. Some matches are to FAA aircraft-owner records in our list rather than school records.")
    w("")
    w("### Not in our list, sorted by hand")
    w("")
    w(f"Each of the {len(new):,} unmatched entries was labelled by hand from its name, tags and website "
      f"(`docs/hackers-2026-fsai-labels.csv`). Members tick their own tags, so many entries tagged as flight "
      f"schools are examiners, insurers, vendors or one person. Entries labelled *school* had their homepage "
      f"fetched on {__import__('datetime').date.today().isoformat()}: *confirmed* means the site answered and "
      f"mentions flight training (lessons, discovery flights, private pilot and the like).")
    w("")
    w("| Label | Entries |")
    w("|---|---|")
    for k, n in collections.Counter(m["label"] for m in new).most_common():
        w(f"| {k} | {n:,} |")
    w("")
    vc = collections.Counter(m["verdict"] for m in new if m["label"] == "school")
    w(f"Of the {sum(vc.values())} labelled school: {vc['confirmed']} confirmed, {vc['no training text']} answered "
      f"without training text on the homepage, {vc['blocked']} served a bot challenge or refusal (not "
      f"circumvented) and {vc['site down']} did not answer (most no longer resolve in DNS).")
    w("")
    w(f"### {len(confirmed)} confirmed flight-training operations not in our list")
    w("")
    w("| Name | City | State | Website | FSAI tags |")
    w("|---|---|---|---|---|")
    for m in sorted(confirmed, key=lambda m: (m["state"], m["name"])):
        r = by_id.get(m["fsai_id"], {})
        tags = ", ".join(dict.fromkeys(t.split(":", 1)[1].split("#")[0] for t in r.get("tags", [])
                         if re.search(r"part 141|part 61|accredited|flight school - ", t, re.I)))
        w(f"| {m['name']} | {m['city']} | {m['state']} | {m['website']} | {tags} |")
    w("")
    w("### Every unmatched entry")
    w("")
    w("Closest is the nearest same-state name in our list and its similarity, for reading misses by eye. "
      "Basis says whether the entry is here by its tags or by its name alone.")
    w("")
    w("| Name | City | State | Website | Basis | Label | Closest in ours |")
    w("|---|---|---|---|---|---|---|")
    for m in sorted(new, key=lambda m: (m["state"] or "~", m["name"])):
        r = by_id.get(m["fsai_id"], {})
        tags = ", ".join(dict.fromkeys(t.split(":", 1)[1].split("#")[0] for t in r.get("tags", [])
                         if re.search(r"part 141|part 61|accredited|flight school - ", t, re.I)))
        near = f"{m['school_name']} ({m['match_score']})" if m.get("school_name") else ""
        lab = m["label"] + (f" ({m['verdict']})" if m.get("verdict") else "")
        if m["label"] == "individual":  # one person, possibly at home: no name or place
            w(f"| (individual) | | {m['state']} | | {m['basis']} | {lab} | |")
            continue
        w(f"| {m['name']} | {m['city']} | {m['state']} | {m['website']} | {m['basis']} | {lab} | {near} |")
    w("")
    w("## Reproduce")
    w("")
    w("Open the directory page in a browser and run `scripts/probes/fsai_directory_pull.js` in its console; "
      "it pulls every entry and tag and saves `fsai_raw.json` as a download. Move that to "
      "`data/research-2026-11/fsai/`, then, from `research/FlightSchoolMapping/`:")
    w("")
    w("```")
    w("python scripts/56_fsai_directory.py data/research-2026-11/fsai/fsai_raw.json data/research-2026-11/fsai")
    w("```")
    Path(a.md).write_text("\n".join(L) + "\n")
    print(summary)
    print(f"entries {len(all_ids)}, listings {len(listings)}, kinds {dict(kinds)}, candidates {len(cands)}, "
          f"matched {n_match}, not matched {len(new)}")
    print(f"confirmed new {len(confirmed)}; verdicts {dict(collections.Counter(m.get('verdict') for m in new if m.get('verdict')))}")
    ok = gate_n and gate_cover and gate_labels
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
