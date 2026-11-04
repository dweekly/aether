"""The 372 unlisted training operations, rebuilt from the frozen release.

40_training_detect.py defines "a training operation missing from our school list" as a
non-individual registrant with >= 3 aircraft scored >= 0.7 by the training model (and
flagged at >= 0.5), none of whose tails is on any known fleet, and whose registrant
holds no tail on any known fleet. That script wrote the list into training.json; this
module recomputes it from training_scores.parquet, aircraft.parquet and
schools_full.json and refuses to continue unless both agree tail for tail.

Shared by 55a_unlisted_discover.py, 55b_unlisted_crawl.py and 55_unlisted_schools.py.
"""

import csv
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

import duckdb

# The production checkout's data is read-only input; outputs go under OUT.
FSM = Path(os.environ.get("AETHER_FSM", "/mnt/data/home/dew/dev/aether/research/FlightSchoolMapping"))
RELEASE = os.environ.get("AETHER_RELEASE", "v0.3-20261023")
REL = FSM / "data" / "release" / RELEASE
OUT = FSM / "data" / "research-2026-11" / "unlisted"
EXPECTED = 372
THRESHOLD, OP_THRESHOLD, MIN_AIRCRAFT = 0.5, 0.7, 3  # 40_training_detect.py defaults
ENV_FILE = FSM.parents[1] / ".env"


class RebuildError(RuntimeError):
    pass


def api_key():
    """ANTHROPIC_API_KEY from the environment, else the production checkout's .env. Never printed."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    for line in ENV_FILE.read_text().splitlines():
        k, _, v = line.partition("=")
        if k.strip() == "ANTHROPIC_API_KEY":
            return v.strip().strip('"').strip("'")
    raise RuntimeError(f"ANTHROPIC_API_KEY not in environment or {ENV_FILE}")


def _airport_names():
    names = {}
    with open(FSM / "data" / "ourairports_airports.csv", newline="") as f:
        for r in csv.DictReader(f):
            label = f"{r['name']} ({r['municipality']}, {r['iso_region'].removeprefix('US-')})"
            for k in ("ident", "gps_code", "icao_code", "local_code"):
                if r[k]:
                    names.setdefault(r[k], label)
    return names


def rebuild():
    """Return the operations, sorted as in training.json. Raises RebuildError on any mismatch."""
    schools = json.loads((REL / "schools_full.json").read_text())["schools"]
    any_school_tails = {f["n"] for s in schools for f in s["fleet"]}
    known_registrants = {f["registrant"] for s in schools for f in s["fleet"] if f.get("registrant")}
    c = duckdb.connect()
    rows = c.execute(f"""
        SELECT s.n, s.score, a.registrant, a.registrant_type, a.home_airport, a.city, a.state,
               a.mfr, a.model
        FROM read_parquet('{REL / "training_scores.parquet"}') s
        JOIN read_parquet('{REL / "aircraft.parquet"}') a ON a.n_number = s.n""").fetchall()
    ops = defaultdict(list)
    for n, score, reg, rtype, home, city, state, mfr, model in rows:
        score = round(float(score), 3)  # 40_training_detect.py compares the rounded score
        if score < THRESHOLD or score < OP_THRESHOLD or n in any_school_tails:
            continue
        if not (reg or "").strip() or rtype in (None, "Individual", "Co-Owned"):
            continue
        if reg in ("REGISTRATION PENDING", "SALE REPORTED") or reg in known_registrants:
            continue
        ops[reg].append({"n": n, "score": score, "home": home, "city": city, "state": state,
                         "registrant_type": rtype, "model": f"{mfr} {model}"})
    apt = _airport_names()
    out = []
    for reg, v in ops.items():
        if len(v) < MIN_AIRCRAFT:
            continue
        homes = Counter(x["home"] for x in v if x["home"]).most_common(3)
        city, state = Counter((x["city"], x["state"]) for x in v).most_common(1)[0][0]
        out.append({
            "registrant": reg,
            "registrant_type": Counter(x["registrant_type"] for x in v).most_common(1)[0][0],
            "registrant_city": city, "registrant_state": state,
            "homes": [[h, k, apt.get(h, "")] for h, k in homes],
            "aircraft": len(v),
            "mean_score": round(sum(x["score"] for x in v) / len(v), 3),
            "models": Counter(x["model"] for x in v).most_common(3),
            "tails": sorted(x["n"] for x in v),
        })
    out.sort(key=lambda o: (-o["aircraft"], -o["mean_score"]))

    published = json.loads((REL / "training.json").read_text())["unlisted_operations"]
    want = {o["registrant"]: sorted(o["tails"]) for o in published}
    got = {o["registrant"]: o["tails"] for o in out}
    if len(out) != EXPECTED or len(published) != EXPECTED:
        raise RebuildError(f"rebuilt {len(out)} operations, training.json has {len(published)}, expected {EXPECTED}")
    if want != got:
        diff = sorted(set(want) ^ set(got)) or [k for k in got if got[k] != want.get(k)]
        raise RebuildError(f"rebuilt operations differ from training.json: {diff[:10]}")
    return out


def domain(url):
    """Registrable-ish host: lowercase, no scheme, no leading www."""
    if not url:
        return ""
    u = url.strip()
    if "://" not in u:
        u = "http://" + u
    host = (urlparse(u).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def march_domains():
    """Every website domain the March discovery and crawl knew about, by source."""
    o = FSM / "output"
    found = defaultdict(set)

    def add(src, url):
        d = domain(url)
        if d and "." in d:
            found[d].add(src)

    for s in json.loads((REL / "schools_full.json").read_text())["schools"]:
        add("schools_full", s.get("domain"))
    for s in json.loads((o / "flight_schools_enriched.json").read_text()):
        add("directory", s.get("website"))
    for s in json.loads((o / "aopa_flight_schools.json").read_text()):
        add("aopa", s.get("website"))
    for v in json.loads((o / "aopa_website_cache.json").read_text()).values():
        add("aopa", v)
    for name in ("fleet_crawl_index.json", "fleet_browser_index.json"):
        p = o / name
        if p.exists():
            data = json.loads(p.read_text())
            for s in (data if isinstance(data, list) else data.values() if isinstance(data, dict) else []):
                if isinstance(s, dict):
                    add("crawl", s.get("school_website") or s.get("website") or s.get("url"))
                    add("crawl", s.get("redirect_domain"))
    p = o / "expansion_crawl.jsonl"
    if p.exists():
        for line in p.open():
            try:
                s = json.loads(line)
            except json.JSONDecodeError:
                continue
            add("expansion", s.get("domain") or s.get("school_website") or s.get("website"))
    p = o / "cirrus_training_network.json"
    if p.exists():
        data = json.loads(p.read_text())
        for s in (data if isinstance(data, list) else data.get("schools", [])):
            if isinstance(s, dict):
                add("cirrus", s.get("training_website__c") or s.get("website"))
    return found
