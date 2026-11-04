#!/usr/bin/env python3
"""Build the school records for a release, from permitted sources only.

Kept from output/flight_line.json:
  domain                 the school's own website
  fleet[].n_number, display_name, wet_rate_hr, dry_rate_hr, airport_id,
  source_url             extracted from the school's own pages (or PDF rate sheets)
Dropped: name, aopa_*, contact and location labels (AOPA's terms forbid
republishing them) and every adsb field (OpenSky-derived; its terms forbid
redistribution). Activity is recomputed from the adsb.lol archive.

School name comes from the school's own cached pages (og:site_name, else the
<title> segment that best matches the domain), else the domain itself. Schools added
late from public lists (57_added_schools.py; they carry a provenance) keep the name set
from that list, and their record says which list ("added_from"). Schools
with no website were discovered from the FAA registry; their key is
"faa-<slug>" and their name is the FAA registrant name (public domain). Entries
with no website and an AOPA-sourced name are named after the registrant that
holds most of their fleet, or dropped if none is known.
Fleet rows without an N-number (a model and a rate) are kept as listings.
Fleet evidence per tail: "website" when the row's source_url is on the
school's domain, else "registry" (matched by FAA registrant name).

Writes data/release/<release>/:
  schools_full.json      one record per school, used to generate the site pages
  web/schools.json       compact search index shipped to browsers

Usage: 38_schools.py --release dev
"""

import argparse
import html
import sys
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from urllib.parse import urljoin, urlparse

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from airport_index import canonical_ids  # noqa: E402
from release_window import day_view, included_days  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
HISTORY = ROOT / "data" / "history"
RELEASES = ROOT / "data" / "release"
AIRPORTS = ROOT / "data" / "ourairports_airports.csv"
GENERIC = {"home", "welcome", "index", "about", "about us", "fleet", "our fleet", "aircraft",
           "rates", "rental", "contact", "untitled", "page not found", "404", "redirect", "redirecting",
           "moved", "301 moved permanently", "302 found", "just a moment...", "attention required!",
           "access denied", "site not found", "coming soon", "under construction", "loading..."}
# FAA-discovered entities must look like an operator, not a private owner.
OPERATOR_WORDS = re.compile(r"\b(FLIGHT|FLYING|FLYERS?|AVIATION|AERO|AIR|AIRWAYS|ACADEMY|SCHOOL|CLUB|COLLEGE|"
                            r"UNIVERSITY|PILOTS?|TRAINING|SOARING|GLIDING|HELICOPTERS?|WINGS|SKY|JET|AIRCRAFT)\b")
SEP = re.compile(r"\s*[|–—:·•]\s*|\s+-\s+")


def resolve_url(src, domain):
    """Make a crawled source_url absolute: relative paths ("aircraft.html") resolve
    against the school's site; scheme-less hosts ("example.com/fleet") get https."""
    if not src:
        return None
    if re.match(r"^[a-z][a-z0-9+.-]*://", src, re.I):
        return src
    if src.startswith("//"):
        return "https:" + src
    head = src.split("/", 1)[0]
    if "." in head and not head.endswith((".html", ".htm", ".php", ".pdf", ".aspx")):
        return "https://" + src
    return urljoin(f"https://{domain}/", src) if domain else None


def norm_domain(url):
    host = urlparse(url if "//" in url else "//" + url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def site_name(domain):
    root = re.sub(r"[^a-z0-9]", "", domain.split(".")[0])
    for d in (OUT / "fleet_html_raw" / domain, OUT / "fleet_browser_html_raw" / domain):
        if not d.is_dir():
            continue
        pages = sorted(d.glob("*.html"), key=lambda p: (not p.name.startswith("index"), p.name))
        for p in pages[:5]:
            text = p.read_text(errors="replace")[:200_000]
            m = re.search(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']{2,80})', text, re.I)
            if m and " ".join(html.unescape(m.group(1)).split()).lower() not in GENERIC:
                return " ".join(html.unescape(m.group(1)).split()), "og:site_name"
            m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
            if m:
                parts = [s.strip() for s in SEP.split(html.unescape(m.group(1)))
                         if 2 < len(s.strip()) < 70 and s.strip().lower() not in GENERIC]
                if not parts:
                    continue
                scored = sorted(parts, key=lambda s: -len(set(re.sub(r"[^a-z0-9]", "", s.lower())) & set(root)) / max(1, len(set(root))))
                best = scored[0] if scored else parts[0]
                return " ".join(best.split()), "title"
    return domain, "domain"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", required=True)
    a = ap.parse_args()
    rel = RELEASES / a.release
    c = duckdb.connect()
    aircraft = {r[0]: r for r in c.execute(f"""
        SELECT n_number, hex, mfr, model, year_mfr, observed_class, days_airborne, airborne_hours,
               legs, last_seen, home_airport, status_valid, registrant, last_flew
        FROM read_parquet('{rel / 'aircraft.parquet'}')""").fetchall()}
    alias, airports = canonical_ids()
    canon = lambda x: alias.get(x.upper(), x) if x else x  # noqa: E731

    fl = json.loads((OUT / "flight_line.json").read_text())
    raw = {}  # key -> {"name", "name_source", "domain", "fleet", "listings"}
    for s in fl:
        domain = norm_domain(s.get("domain") or "")
        fleet, listings = [], []
        for f in s.get("fleet") or []:
            n = (f.get("n_number") or "").upper()
            src = f.get("source_url") or ""
            src = resolve_url(src, domain)
            on_site = bool(src and domain and norm_domain(src).endswith(domain))
            if not re.fullmatch(r"N[0-9][0-9A-Z]{0,4}", n):
                if f.get("display_name"):
                    listings.append({"listed_as": f.get("display_name"), "wet": f.get("wet_rate_hr"),
                                     "dry": f.get("dry_rate_hr"), "source_url": resolve_url(src, domain)})
                continue
            ac = aircraft.get(n)
            fleet.append({
                "n": n, "listed_as": f.get("display_name"),
                "wet": f.get("wet_rate_hr"), "dry": f.get("dry_rate_hr"),
                "airport": canon(f.get("airport_id")), "source_url": src or None,
                "evidence": "website" if on_site else "registry",
                "on_registry": ac is not None,
                "hex": ac[1] if ac else None,
                "model": " ".join(str(x) for x in (ac[2], ac[3]) if x) if ac else None,
                "year": ac[4] if ac else None, "cls": ac[5] if ac else None,
                "days": ac[6] if ac else 0, "hrs": ac[7] if ac else 0, "legs": ac[8] if ac else 0,
                "last": str(ac[9]) if ac and ac[9] else None, "home": ac[10] if ac else None,
                "last_flew": str(ac[13]) if ac and ac[13] else None,
                "registrant": ac[12] if ac else None,
            })
        if not fleet and not listings:
            continue
        if domain and s.get("provenance"):
            # Added late from an outside list (57_added_schools.py): the name is the one we set
            # from that list by hand, since page titles of small sites are often unusable ("MA").
            key, name, name_src = domain, s["name"], "added_list"
        elif domain:
            key = domain
            name, name_src = site_name(domain)
        elif not s.get("aopa_id") and s.get("name"):
            if not OPERATOR_WORDS.search(s["name"].upper()):
                continue
            name, name_src = s["name"], "faa_registrant"
            key = "faa-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        else:
            owners = Counter(f["registrant"] for f in fleet if f["registrant"])
            if not owners:
                continue
            name, name_src = owners.most_common(1)[0][0], "fleet_registrant"
            if not OPERATOR_WORDS.search(name.upper()):
                continue
            key = "faa-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if key in raw:  # same registrant discovered twice: merge, keeping each tail once
            seen = {f["n"] for f in raw[key]["fleet"]}
            raw[key]["fleet"] += [f for f in fleet if f["n"] not in seen]
            raw[key]["listings"] += listings
        else:
            raw[key] = {"name": name, "name_source": name_src, "domain": domain or None,
                        "fleet": fleet, "listings": listings}
            if s.get("provenance"):  # which public list it came from; record ids stay internal
                raw[key]["added_from"] = [{"list": p["list"], "pulled": p["pulled"]} for p in s["provenance"]]

    hex_school = defaultdict(set)
    schools = []
    for key, r in raw.items():
        fleet, listings = r["fleet"], r["listings"]
        for f in fleet:
            if f["hex"]:
                hex_school[f["hex"]].add(key)
        homes = Counter(f["home"] for f in fleet if f["home"]) + Counter(f["airport"] for f in fleet if f["airport"])
        primary = homes.most_common(1)[0][0] if homes else None
        apt = airports.get(primary)
        rated = sorted(f["wet"] for f in fleet + listings if f["wet"])
        schools.append({
            "key": key, "domain": r["domain"], "name": r["name"], "name_source": r["name_source"],
            "airport": primary, "airport_name": apt[0] if apt else None,
            "city": apt[1] if apt else None,
            "state": apt[2].split("-")[-1] if apt and apt[2] else None,
            "airports": [k for k, _ in homes.most_common(5)],
            "fleet_listed": len(fleet), "listings_without_tail": len(listings),
            "fleet_on_registry": sum(f["on_registry"] for f in fleet),
            "fleet_flew": sum(1 for f in fleet if f["days"]),
            "fleet_hours": round(sum(f["hrs"] or 0 for f in fleet), 1),
            "wet_rate_median": median(rated) if rated else None,
            # From the individual rows: all tails from the site, some, or none (registry only).
            # A school with only tail-less listings has its listings from its own site.
            "evidence": ("website" if all(f["evidence"] == "website" for f in fleet) else
                         "mixed" if any(f["evidence"] == "website" for f in fleet) else "registry")
                        if fleet else ("website" if r["domain"] else "registry"),
            "fleet": sorted(fleet, key=lambda f: (-(f["hrs"] or 0), f["n"])),
            "listings": listings,
            **({"added_from": r["added_from"]} if r.get("added_from") else {}),
        })

    # Daily fleet hours per school, for the activity chart.
    days = included_days(rel)  # frozen by 34; see release_window.py
    day_view(c, "ad", HISTORY / "aircraft_day" / "*" / "*" / "*.parquet", days)
    idx = {d: i for i, d in enumerate(days)}
    daily = defaultdict(lambda: defaultdict(float))
    c.execute("CREATE TEMP TABLE h(hex VARCHAR)")
    c.executemany("INSERT INTO h VALUES (?)", [(x,) for x in hex_school])
    for hexid, day, mins in c.execute(f"""
        SELECT a.hex, a.day, a.airborne_min FROM ad a JOIN h USING (hex) WHERE a.airborne_min > 0""").fetchall():
        for key in hex_school[hexid]:
            daily[key][idx[str(day)]] += mins / 60.0
    for s in schools:
        s["daily_hours"] = [[i, round(v, 1)] for i, v in sorted(daily[s["key"]].items())]
    schools.sort(key=lambda s: (-s["fleet_hours"], s["key"]))
    (rel / "schools_full.json").write_text(json.dumps({"days": days, "schools": schools}, default=str))
    index = [[s["key"], s["name"], s["airport"], s["city"], s["state"], s["fleet_listed"], s["fleet_flew"]]
             for s in schools]
    (rel / "web").mkdir(exist_ok=True)
    (rel / "web" / "schools.json").write_text(json.dumps(index, separators=(",", ":")))
    srcs = Counter(s["name_source"] for s in schools)
    print(f"{a.release}: {len(schools)} schools; names {dict(srcs)}; "
          f"{sum(s['fleet_flew'] > 0 for s in schools)} with a fleet aircraft that flew")


if __name__ == "__main__":
    main()
