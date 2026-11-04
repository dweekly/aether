#!/usr/bin/env python3
"""Hand audit of the March 2026 fleet extraction (09b HTTP pass, 09c browser pass).

Draws a seeded, stratified sample of model calls, reconstructs the pages each call saw,
and scores the model's output against hand labels kept in LABELS below. Method and
results: docs/hackers-2026-extraction-audit.md.

    python extraction_audit.py sample            # the draw, with mapping checks
    python extraction_audit.py dump DIR          # per call: the capped page text and the model's output, for reading
    python extraction_audit.py score             # metrics and the per-school table (markdown)
    python extraction_audit.py body-loss         # corpus-wide count of pages emptied by 09a's HTML cleaning

--output points at the pipeline's output/ directory (default: ../../output, or $AETHER_OUTPUT).
Reads only; writes only under DIR for `dump`. Exits 1 on missing inputs or an incomplete label set.
"""

import argparse
import difflib
import html as htmllib
import json
import math
import os
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
SEED = 20261107
TARGET = {"F": 25, "E": 15}
HTTP_CAP, BROWSER_CAP = 50_000, 40_000  # 09b and 09c per-page caps
OFFSET_TOL = 3
NAME_MIN = 0.85


def dom(u):
    u = (u or "").strip()
    if not u:
        return ""
    d = urlparse(u if "://" in u else "https://" + u).netloc.lower().split(":")[0]
    return d[4:] if d.startswith("www.") else d


def parse(res):
    """Same parse as 49_scrape_facts.py: the outermost {...} of the text blocks."""
    if res["type"] != "succeeded":
        return None
    text = "".join(b.get("text", "") for b in res["message"]["content"])
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


def norm_tail(n):
    n = re.sub(r"[\s\-]", "", (n or "").upper())
    if not n:
        return ""
    return n if n.startswith("N") else "N" + n


def norm_url(u):
    return u.rstrip("/").replace("http://", "https://").replace("://www.", "://")


class Corpus:
    def __init__(self, out):
        self.out = out
        need = ["fleet_extract_results.jsonl", "fleet_browser_extract_results.jsonl",
                "fleet_browser_extract_index.json", "fleet_crawl_index.json", "fleet_html", "fleet_browser_text"]
        missing = [n for n in need if not (out / n).exists()]
        if missing:
            sys.exit(f"missing inputs under {out}: {missing}")
        self.ci = json.loads((out / "fleet_crawl_index.json").read_text())
        self.ci_pos = defaultdict(list)  # domain -> positions
        for i, s in enumerate(self.ci):
            self.ci_pos[dom(s["school_website"])].append(i)
        self.bx = {e["custom_id"]: e for e in json.loads((out / "fleet_browser_extract_index.json").read_text())}
        self.calls = {}  # (pass, cid) -> (parsed, usage)
        for name, fn in (("http", "fleet_extract_results.jsonl"), ("browser", "fleet_browser_extract_results.jsonl")):
            for line in (out / fn).open():
                r = json.loads(line)
                d = parse(r["result"])
                if d is not None:
                    self.calls[(name, r["custom_id"])] = (d, r["result"]["message"]["usage"])
        self._anchors()

    # HTTP positional mapping is broken; anchor on calls whose cited URLs name the domain.
    def _cited_domain(self, d):
        srcs = [dom(a.get("source_url")) for a in d.get("fleet") or [] if a.get("source_url")]
        srcs = [s for s in srcs if s]
        return max(set(srcs), key=srcs.count) if srcs else ""

    def _anchors(self):
        self.anchor = []  # (k, offset)
        for (p, cid), (d, _) in self.calls.items():
            if p != "http":
                continue
            D = self._cited_domain(d)
            if D in self.ci_pos:
                k = int(cid.split("_")[1])
                pos = min(self.ci_pos[D], key=lambda x: abs(k - x))
                self.anchor.append((k, k - pos))
        self.anchor.sort()

    def local_offsets(self, k):
        """Offsets of the nearest anchors below and above k (excluding k itself)."""
        below = [o for kk, o in self.anchor if kk < k][-2:]
        above = [o for kk, o in self.anchor if kk > k][:2]
        return below + above

    def map_http(self, cid, d):
        k = int(cid.split("_")[1])
        route, D = None, self._cited_domain(d)
        if D in self.ci_pos:
            route = "source_url"
        else:
            email = (d.get("contact") or {}).get("email") or ""
            em = email.split("@")[-1].lower().strip() if "@" in email else ""
            em = em[4:] if em.startswith("www.") else em
            if em in self.ci_pos:
                route, D = "email", em
            else:
                cn = (d.get("canonical_name") or "").lower()
                best = max(((difflib.SequenceMatcher(None, cn, s["school_name"].lower()).ratio(), i)
                            for i, s in enumerate(self.ci)), default=(0, None))
                if best[0] >= NAME_MIN:
                    route, D = f"name({best[0]:.2f})", dom(self.ci[best[1]]["school_website"])
        if not route:
            return None, "no domain from source_url, email or name"
        locs = self.local_offsets(k)
        cited = [norm_url(a["source_url"]) for a in d.get("fleet") or [] if a.get("source_url")]

        def urls_ok_at(i):
            urls = {norm_url(p["url"]) for p in self.ci[i]["pages"]}
            return bool(cited) and all(u in urls for u in cited)
        # A domain can have several index entries (one per location page): prefer the
        # entry whose pages hold every cited URL, then the one nearest the expected position.
        cands = self.ci_pos[D]
        pos = min(cands, key=lambda i: (not urls_ok_at(i), abs(k - i)))
        off = k - pos
        off_ok = any(abs(off - o) <= OFFSET_TOL for o in locs)
        entry = self.ci[pos]
        urls_ok = urls_ok_at(pos)
        if not (off_ok or (route == "source_url" and urls_ok)):
            return None, f"{route} -> {D} at offset {off}, neighbours {locs}, cited URLs on pages: {urls_ok}"
        return {"domain": D, "pos": pos, "route": route, "offset": off, "neighbour_offsets": locs,
                "offset_ok": off_ok, "cited_urls_on_pages": urls_ok if cited else None, "entry": entry}, None

    def pages(self, key, m):
        """[(url, capped text the model saw)] in prompt order."""
        out = []
        if key[0] == "http":
            for p in m["entry"]["pages"]:
                f = self.out / "fleet_html" / p["html_path"]
                t = f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""
                if t:
                    out.append((p["url"], t[:HTTP_CAP], len(t) > HTTP_CAP))
        else:
            for f in sorted((self.out / "fleet_browser_text" / m["domain"]).glob("*.txt")):
                t = f.read_text(encoding="utf-8", errors="replace")
                if len(t.strip()) < 50:
                    continue
                out.append((f.name, t[:BROWSER_CAP], len(t) > BROWSER_CAP))
        return out

    def map(self, key):
        d, _ = self.calls[key]
        if key[0] == "browser":
            e = self.bx.get(key[1])
            if not e:
                return None, "not in browser extract index"
            return {"domain": e["domain"], "route": "index", "school_name": e["school_name"]}, None
        return self.map_http(key[1], d)


def draw(c):
    strata = {"F": [], "E": []}
    for key, (d, _) in sorted(c.calls.items()):
        strata["F" if d.get("fleet") else "E"].append(key)
    rng = random.Random(SEED)
    sample, skipped = [], []
    for s in ("F", "E"):
        order = list(strata[s])
        rng.shuffle(order)
        taken = 0
        for key in order:
            if taken == TARGET[s]:
                break
            m, why = c.map(key)
            if m is None:
                skipped.append((s, key, why))
                continue
            sample.append((s, key, m))
            taken += 1
    return sample, skipped, {s: len(v) for s, v in strata.items()}


def is_binary(t):
    """A cached 'page' that is really a GIF, PDF or other binary file read as text."""
    if not t:
        return False
    bad = sum(1 for ch in t[:5000] if ch == "\ufffd" or (ord(ch) < 32 and ch not in "\n\r\t"))
    return bad / min(len(t), 5000) > 0.05


def readable(t):
    """Tags stripped for reading; image alt/src and link targets kept inline."""
    if is_binary(t):
        return f"[binary file read as text: {len(t)} characters of noise in the prompt]"
    t = re.sub(r"<img[^>]*?(?:alt=\"([^\"]*)\")?[^>]*?(?:src=\"([^\"]*)\")?[^>]*>",
               lambda m: f" [img {m.group(1) or ''} {m.group(2) or ''}] ", t)
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</(p|div|li|tr|h\d|td|th)>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = htmllib.unescape(t)
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def token_ratio(c, key, m):
    _, u = c.calls[key]
    chars = sum(len(t) + len(url) + 30 for url, t, _ in c.pages(key, m))
    return round(chars / max(1, u["input_tokens"] - 1100), 2)


def cmd_sample(c, args):
    sample, skipped, sizes = draw(c)
    print(f"pool: F={sizes['F']} E={sizes['E']} (http+browser calls that succeeded and parse)")
    for s, key, m in sample:
        d, _ = c.calls[key]
        extra = ""
        if key[0] == "http":
            extra = f" route={m['route']} offset={m['offset']} nbrs={m['neighbour_offsets']} urls_ok={m['cited_urls_on_pages']}"
        print(f"{s} {key[0]:7} {key[1]:13} {m['domain']:35} fleet={len(d.get('fleet') or []):3} "
              f"pages={len(c.pages(key, m))} ratio={token_ratio(c, key, m)}{extra}")
    for s, key, why in skipped:
        print(f"SKIP {s} {key}: {why}")


def cmd_dump(c, args):
    sample, _, _ = draw(c)
    od = Path(args.dir)
    od.mkdir(parents=True, exist_ok=True)
    for i, (s, key, m) in enumerate(sample):
        d, _ = c.calls[key]
        name = f"{i:02d}_{s}_{key[0]}_{key[1]}_{m['domain']}"
        with open(od / f"{name}.txt", "w") as f:
            f.write(f"# {key} {m['domain']} stratum {s}\n")
            if key[0] == "http":
                e = m["entry"]
                f.write(f"# prompt school: {e['school_name']} | {e['school_website']} | {e.get('known_airport')} | {e.get('city')}, {e.get('state')}\n")
            for url, t, trunc in c.pages(key, m):
                body = readable(t) if key[0] == "http" else (readable(t) if is_binary(t) else t)
                f.write(f"\n\n===== PAGE {url} ({len(t)} chars{' TRUNCATED' if trunc else ''}) =====\n{body}\n")
        (od / f"{name}.model.json").write_text(json.dumps(d, indent=1))
    print(f"wrote {len(sample)} calls to {od}")


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (mid - half, mid + half)


def pct(k, n):
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {100*k/n:.0f}% (95% CI {100*lo:.0f}-{100*hi:.0f}%)" if n else f"0/0"


# ── Hand labels ──────────────────────────────────────────────────
# One entry per sampled call, keyed "pass:custom_id", written by reading the capped page
# text the call saw (`dump`). Fields:
#   school         name the pages give (or the directory's, when the pages give none)
#   site           right: the pages are that school's site | wrong: parked, repurposed or another
#                  organisation's site | unreadable: blank, redirect or bot wall
#   name           the model's canonical_name (HTTP pass only; 09c did not ask for one):
#                  right | partly | wrong | echo (pages carry no name, so it can only repeat the prompt) | not_asked
#   tails          {tail: description}: the school's aircraft shown with a tail on those pages
#   untailed       the school's aircraft shown without a tail, one item per aircraft or type as the page presents them
#   untailed_correct  how many of the model's untailed entries match one of those
#   rates          posted prices, free text for the table
#   model_rates    one verdict per model fleet entry with a non-null wet_rate_hr:
#                  "<number>/<wet>/<label>", number in right | wrong_attach | invented | not_aircraft,
#                  wet in stated | implied | contradicted | unknown, label in standard | member | estimate | none
#   null_dry       posted prices the model left null because the page calls them dry (the schema has no dry field)
#   missed_rate    other posted per-aircraft prices the model left null
#   empty_class    stratum E only: no_fleet | model_missed | unreadable | wrong_site
#   judged_raw     a verdict also used the raw HTML (output/fleet_html_raw/), which the model did not see
#   stale, note, false_note  free text
LABELS = {
    'http:school_2093': dict(
        school='FlightSafety International, Denver Learning Center', site='right', name='right',
        note=('the pages list full-flight simulators (AS350, Bell 407, CRJ, EC1xx, ERJ, PC-12), not aircraft; the model returned all nine as '
              'fleet, each described as a simulator'),
    ),
    'http:school_1512': dict(
        school='U S Flight Academy', site='right', name='right',
        untailed=['Cessna 150', 'Cessna 152', 'Cessna 182', 'Cessna 310'],
        untailed_correct=4,
        note='pages are title-only (one per model); two 50,000-character binary files (a GIF and a PDF) were fed to the model as page text',
    ),
    'http:school_1542': dict(
        school='Vector Air', site='right', name='right',
        tails={'N4633J': '1979 Cessna 172N',
               'N523AB': '1999 Cessna 172S',
               'N64464': '1982 Cessna 172P',
               'N733XB': '1977 Cessna 172N',
               'N855CP': '1998 Cessna 172R'},
        rates={'N4633J': '$146/hr wet',
               'N523AB': '$170/hr wet',
               'N64464': '$163/hr wet',
               'N733XB': '$161/hr wet',
               'N855CP': '$168/hr wet',
               'instructor': '$65/hr'},
        model_rates={'N4633J': 'right/stated/standard',
                     'N523AB': 'right/stated/standard',
                     'N64464': 'right/stated/standard',
                     'N733XB': 'right/stated/standard',
                     'N855CP': 'right/stated/standard'},
    ),
    'http:school_1974': dict(
        school='Aamro Aviation', site='right', name='right',
        untailed=['Cessna 172S (2023, plural)', 'Piper Archer TX (plural)', 'Tecnam P2006T'],
        untailed_correct=3,
        note='types only, no tails or hourly prices; counts per type not given',
    ),
    'http:school_0030': dict(
        school='Decatur Jet Center', site='right', name='right',
        note=('every cleaned page the model saw was an empty HTML shell; the six tails came from URL slugs (/n969dm etc.). The raw HTML shows '
              'all six are aircraft-sales listings marked SOLD in 2013-2016, not school aircraft'),
        judged_raw=True,
    ),
    'browser:browser_1609': dict(
        school='Sky Creation', site='right', name='not_asked',
        untailed=['Cessna 172', 'Beechcraft Duchess'],
        untailed_correct=2,
        note='Japanese-language site; only course package prices, no hourly rates; model gave no school name',
    ),
    'http:school_0070': dict(
        school='Peak Aviation Center', site='right', name='right',
        tails={'N1168V': '1977 Cessna 172XP',
               'N2949V': '1977 Cessna 172XP',
               'N359JP': '1999 Diamond DA-20',
               'N487ER': '2007 Cessna 172SP G1000',
               'N567JW': '1999 Cessna 172SP'},
        untailed=['Cessna 182RG (priced only in the commercial cost estimate)'],
        untailed_correct=0,
        rates={'182RG': '10 h for $2,470',
               'N1PP (simulator)': '$68/hr',
               'N2949V/N1168V': '$217/hr',
               'N359JP': 'Unavailable',
               'N487ER': '$222/hr',
               'N567JW': '$207/hr'},
        model_rates={'N1168V': 'right/unknown/standard',
                     'N1PP': 'not_aircraft/unknown/standard',
                     'N2949V': 'right/unknown/standard',
                     'N487ER': 'right/unknown/standard',
                     'N567JW': 'right/unknown/standard'},
        false_note={'N1PP': "a simulator's identifier"},
        stale='DA-20 rate shown as Unavailable',
    ),
    'browser:browser_1586': dict(
        school='Sheboygan Flying Club', site='right', name='not_asked',
        tails={'N106PE': 'Piper Arrow',
               'N196MT': 'Piper Cherokee 180',
               'N21389': 'Cessna 172M',
               'N2490Y': 'Piper Archer',
               'N68499': 'Bellanca Citabria',
               'N8749E': 'Piper Warrior'},
        rates={'N106PE': '$170 wet',
               'N196MT': 'none posted',
               'N21389': '$140 wet',
               'N2490Y': '$155 wet',
               'N68499': '$130 wet',
               'N8749E': '$140 wet'},
        model_rates={'N106PE': 'right/stated/standard',
                     'N196MT': 'invented/unknown/none',
                     'N21389': 'right/stated/standard',
                     'N2490Y': 'right/stated/standard',
                     'N68499': 'right/stated/standard',
                     'N8749E': 'right/stated/standard'},
        note=("the model gave the Cherokee 180 (no price, no details on the page) $140 and 'IFR certified', copied from the Warrior; no "
              'school name returned'),
    ),
    'http:school_1826': dict(
        school='First Step Aviation', site='right', name='right',
        untailed=['Piper Cherokee PA-28-180D'],
        untailed_correct=1,
    ),
    'http:school_0002': dict(
        school='Ace Pilot Training (Acepilot Training)', site='right', name='right',
        tails={'N10VF': 'title-only page',
               'N15037': 'title-only page',
               'N25899': 'title-only page',
               'N32177': 'title-only page',
               'N47108': 'title-only page',
               'N55264': 'title-only page',
               'N586FL': 'title-only page',
               'N6596P': 'title-only page',
               'N68329': 'title-only page',
               'N6887U': 'title-only page',
               'N760A': 'title-only page',
               'N8543E': 'title-only page',
               'N89980': 'title-only page',
               'N94577': 'title-only page'},
        note=("every cleaned page was a bare title ('N55264 | Acepilot Training'); the raw HTML has the full fleet list and a rate table "
              '(Cherokee $185-190, C152 $160-165, 172P and Mooney $275, Duchess $410) that the cleaner removed, so the model could not see a '
              'single price or most models'),
        judged_raw=True,
    ),
    'http:school_2789': dict(
        school='North Coast Air', site='right', name='right',
        tails={'N12161': 'Cessna 172M',
               'N146TP': 'Tecnam P2002 Sierra',
               'N151F': 'Beech Baron 95-B55',
               'N20230': 'Cessna 172M',
               'N3427M': 'Piper Warrior II (page typo NN3427M)',
               'N454DM': 'Cessna TR182',
               'N5216N': 'Cessna 162',
               'N65647': 'Cessna 172S',
               'N714WS': 'Cessna 152'},
        rates={'N12161/N20230': '$166 wet',
               'N146TP': '$135 wet',
               'N151F': '$485 wet',
               'N3427M': '$182 wet',
               'N454DM': '$265 wet',
               'N5216N': '$140 wet',
               'N65647': '$196 wet',
               'N714WS': '$150 wet',
               'footnote': 'fuel surcharge may be applied'},
        model_rates={'N12161': 'right/stated/standard',
                     'N146TP': 'right/stated/standard',
                     'N151F': 'right/stated/standard',
                     'N20230': 'right/stated/standard',
                     'N3427M': 'right/stated/standard',
                     'N454DM': 'right/stated/standard',
                     'N5216N': 'right/stated/standard',
                     'N65647': 'right/stated/standard',
                     'N714WS': 'right/stated/standard'},
        stale='N146TP and N151F marked NOT AVAILABLE; footer says Copyright 2013; scenic tours page says COVID-19 closure',
        note='the model cited a detail-page URL for N146TP that is not among the pages it was given',
    ),
    'http:school_0657': dict(
        school='Balloon NY', site='right', name='right',
        untailed=['hot air balloon, 2 passengers', 'hot air balloon, 4 passengers', 'hot air balloon, 8 passengers'],
        untailed_correct=3,
        note='a balloon ride operator; prompt name was Catskill Mountain Balloon, pages say Balloon NY; per-person ride prices only',
    ),
    'http:school_1745': dict(
        school='Flat Lake Instruction', site='right', name='right',
        untailed=['1976 Taylorcraft F19', '1946 Taylorcraft BC12D on floats'],
        untailed_correct=2,
        rates={'BC12D (float/ski)': '$350/hr wet', 'F19 (tailwheel)': '$300/hr wet', 'instructor': '$100/hr'},
        model_rates={'1946 Taylorcraft BC12D (floatplane)': 'right/stated/standard', '1976 Taylorcraft F19': 'right/stated/standard'},
    ),
    'browser:browser_1721': dict(
        school='Stallion 51', site='right', name='not_asked',
        tails={'N6320T': "TF-51D 'Little Witch'"},
        untailed=['P-51D Crazy Horse', 'P-51D Crazy Horse 2', 'T-6G Texan', 'L-39'],
        untailed_correct=4,
        stale='N6320T listed SOLD',
        note=('N6320T is ambiguous: the sales page lists it SOLD, a customer review describes flying it at Stallion 51; scored correct. The '
              'model rightly skipped eleven other tails that appear only as sold sales listings (N335J, N97GG, N644TB ...)'),
    ),
    'browser:browser_0761': dict(
        school='Hilton Head Helicopter Tours', site='right', name='not_asked',
        untailed=['three-passenger helicopter (model not given)'],
        untailed_correct=1,
        note='a sightseeing tour operator, not a school; per-person tour prices only',
    ),
    'browser:browser_0727': dict(
        school='Charlottesville Flight Center', site='right', name='not_asked',
        tails={'N12600': 'Cessna 172',
               'N13405': 'Cessna 172',
               'N13644': 'Cessna 172',
               'N20376': 'Cessna 172',
               'N5409J': 'Cessna 172',
               'N59VL': 'Cessna 172',
               'N727RC': 'Cessna 172',
               'N7540G': 'Cessna 172',
               'N7575G': 'Cessna 172'},
        untailed=['Cessna 152 (only in the rate list)'],
        untailed_correct=1,
        rates={'Cessna 152': '$140/hr', 'all Cessna 172s': '$185/hr', 'instruction': '$75/hr, advanced $90/hr', 'simulator': '$75/hr'},
        model_rates={'Cessna 152': 'right/unknown/standard',
                     'N12600': 'right/unknown/standard',
                     'N13405': 'right/unknown/standard',
                     'N13644': 'right/unknown/standard',
                     'N20376': 'right/unknown/standard',
                     'N5409J': 'right/unknown/standard',
                     'N59VL': 'right/unknown/standard',
                     'N727RC': 'right/unknown/standard',
                     'N7540G': 'right/unknown/standard',
                     'N7575G': 'right/unknown/standard',
                     'Redbird AATD Simulator': 'not_aircraft/unknown/standard'},
        note=('tails are printed without the N (7575G, 59vl); the model added it correctly. Also returned the Redbird simulator as an '
              'aircraft. Page: the 172 fleet rotates with Blue Ridge Aviation'),
    ),
    'browser:browser_0544': dict(
        school='Deer Valley SkyHawks', site='right', name='not_asked',
        tails={'N2837X': '1967 Cessna 177 Cardinal',
               'N336RS': '1964 Cessna 172F',
               'N353JK': '1975 Cessna 150M',
               'N5713R': '1965 Cessna 172F',
               'N75600': '1976 Cessna 172N'},
        rates={'172s': '$174/hr wet (membership rate)', 'N2837X': '$184/hr wet', 'N353JK': '$145/hr wet'},
        model_rates={'N2837X': 'right/stated/member',
                     'N336RS': 'right/stated/member',
                     'N353JK': 'right/stated/member',
                     'N5713R': 'right/stated/standard',
                     'N75600': 'right/stated/member'},
        note="the aircraft list is paginated ('1 2 Next') and page 2 was not crawled, so the fleet may be larger",
    ),
    'browser:browser_1248': dict(
        school='Minot Aero Center', site='right', name='not_asked',
        untailed=['Cessna 172L', 'Cessna 172L (IFR)', 'Piper PA-28-151 Warrior', 'Piper PA-28-151 Warrior (IFR)', 'Bellanca 7GCBC Citabria',
                  'Mooney M20J 201', 'Piper PA-34-200 Seneca'],
        untailed_correct=7,
        rates={'172L/Warrior': '$175/hr fuel included',
               'Citabria': '$200/hr fuel included',
               'Mooney': '$265/hr fuel included',
               'Seneca': '$395/hr fuel included on the rentals list, $375/hr fuel included on the multi-engine page',
               'instruction': '$65/hr'},
        model_rates={'172L a': 'right/stated/standard',
                     '172L b': 'right/stated/standard',
                     'Citabria': 'right/stated/standard',
                     'Mooney': 'right/stated/standard',
                     'Seneca': 'right/stated/standard',
                     'Warrior a': 'right/stated/standard',
                     'Warrior b': 'right/stated/standard'},
        note="the site itself gives the Seneca two prices; the model took the rentals list's",
    ),
    'browser:browser_0623': dict(
        school='Executive Aero Club', site='right', name='not_asked',
        note=("the fleet page is a template placeholder ('Plane. Details. Lorem ipsum ...'); the model turned 'training supports the full "
              "Cirrus lineup, including the SR20, SR22, and SR22T' into three fleet aircraft"),
    ),
    'http:school_0402': dict(
        school='Upper Limit Aviation', site='right', name='right',
        tails={'N172WB': 'Cessna 172N',
               'N248TA': 'Tecnam P2006T',
               'N48068': 'Cessna 152',
               'N742TW': 'Cessna 172S',
               'N743TW': 'Cessna 172S',
               'N746TW': 'Cessna 172N',
               'N9042Q': 'Cessna 172N'},
        note=("each aircraft page is a bare title ('N248TA Tecnam P2006T'); the rates page is empty after cleaning; two PDFs went in as "
              '100,000 characters of binary noise'),
    ),
    'browser:browser_0511': dict(
        school='Crossairocean Flight School', site='right', name='not_asked',
        untailed=['Cessna 150', 'Piper PA-28-150', 'Piper Seneca I (PA-34-200)'],
        untailed_correct=3,
        rates={'Cessna 150': '$115/hr wet ($125 for a flight review)',
               'PA-28-150': '$160/hr wet ($170)',
               'Seneca I': '$375/hr wet ($400)',
               'instructor': '$65/hr'},
        model_rates={'Cessna 150': 'right/stated/standard', 'PA-28-150': 'right/stated/standard', 'Seneca': 'right/stated/standard'},
        note='FAQ says five aircraft options but names three',
    ),
    'browser:browser_1404': dict(
        school='Phoenix East Aviation', site='right', name='not_asked',
        untailed=['Cessna 172 (70+ in the fleet, by type)', 'Piper PA-34 Seneca', 'American Champion 8KCAB Super Decathlon'],
        untailed_correct=3,
        note='a 70-aircraft fleet given only by type; the model returns one entry per type, so fleet size is lost',
    ),
    'http:school_2870': dict(
        school='Three Wing Aviation Group', site='right', name='right',
        tails={'N1469T': 'Piper Arrow II',
               'N2884D': 'Piper Warrior II',
               'N33CT': 'Piper Archer II',
               'N4315Y': 'Piper Archer II',
               'N4332N': 'Piper Warrior II',
               'N454X': 'Piper Archer II',
               'N80964': 'Piper Warrior II'},
        rates={'N1469T': '$180 dry',
               'N2884D': '$155 dry',
               'N33CT': '$165 dry',
               'N4315Y': '$170 dry',
               'N4332N': '$150 dry',
               'N454X': '$135 dry',
               'N80964': '$135 dry',
               'club dues': '$90/month'},
        null_dry=7,
        note=('every price is posted as dry and the model left every wet_rate_hr null: right under the schema, but the prices are lost '
              'because the schema has no dry field'),
    ),
    'http:school_0471': dict(
        school='AeroGuard Flight Training Center', site='right', name='right',
        untailed=['Piper Archer TX', 'Piper Seminole', 'Cessna 172', 'Cessna 152', 'Diamond DA42'],
        untailed_correct=5,
        note='a 120+ aircraft fleet given only by type, plus a RedBird simulator the model also returned as an aircraft',
    ),
    'http:school_2337': dict(
        school='Aeronautics Academy', site='right', name='right',
        untailed=['Piper PA-28', 'Cessna 172', 'Robinson R44'],
        untailed_correct=3,
        rates={'aircraft (type not named)': '$159/hr in the private-pilot cost table', 'instructor': '$65/hr'},
        model_rates={'Cessna 172 Skyhawk': 'right/unknown/estimate', 'Piper PA-28 Cherokee': 'right/unknown/estimate'},
        note=('the only aircraft price is a $159/hr line in a training cost estimate that names no type; the model attached it to both '
              'airplanes'),
    ),
    'browser:browser_0917': dict(
        school='Goodrich Aviation', site='right', name='not_asked',
        empty_class='no_fleet',
        note="air-conditioning STC product pages for owners' aircraft; the training centre is a separate site",
    ),
    'http:school_2987': dict(
        school='Skymates', site='unreadable', name='echo',
        empty_class='unreadable',
        note='all five cleaned pages are empty HTML shells (50 characters); the raw fleet page is JavaScript-rendered and lists nothing either',
    ),
    'http:school_0443': dict(
        school='Academy College', site='unreadable', name='echo',
        empty_class='unreadable',
        note=('all six cleaned pages are empty shells; the raw HTML names the training types (Piper Warrior, Archer, Arrow, Seminole, Cessna '
              '172), lost in cleaning'),
        judged_raw=True,
    ),
    'browser:browser_0563': dict(
        school='Don Davis Aviation', site='right', name='not_asked',
        empty_class='no_fleet',
        note='FBO and aircraft-management pages; no aircraft named',
    ),
    'http:school_3016': dict(
        school='Sundance Flying Club', site='unreadable', name='echo',
        empty_class='unreadable',
        note="the only page is a 'Redirect' stub",
    ),
    'browser:browser_1460': dict(
        school='Pearl River Community College (directory says Ozark Technical Community College)', site='wrong', name='not_asked',
        empty_class='wrong_site',
        note="the directory paired Ozark Tech with prcc.edu; the pages are another college's workforce courses (CDL, medical assistant)",
    ),
    'browser:browser_1101': dict(
        school='Kal-Aero Flight Instruction', site='right', name='not_asked',
        empty_class='no_fleet',
        note='home page only; no aircraft named',
    ),
    'browser:browser_1843': dict(
        school='Tulsa Tech', site='right', name='not_asked',
        empty_class='no_fleet',
        note='the crawl reached truck-driving, workplace-safety and conference pages, none about aviation',
    ),
    'http:school_2463': dict(
        school='Florida Flyers Flight Academy', site='unreadable', name='echo',
        empty_class='unreadable',
        note=('24 cleaned pages of empty div skeletons; the raw fleet page lists Tecnam P-Mentor, Cessna 172 S/SP and P/N, Cessna 310 and '
              'Piper Seneca, all lost in cleaning'),
        judged_raw=True,
    ),
    'browser:browser_1514': dict(
        school='North Central Flight Center (per the directory)', site='wrong', name='not_asked',
        empty_class='wrong_site',
        note='ripilot.com is a HugeDomains for-sale page; the model typed it defunct',
    ),
    'browser:browser_0662': dict(
        school='Flight Time Aviation (per the directory)', site='wrong', name='not_asked',
        empty_class='wrong_site',
        note="the domain now hosts an 'aviator' crash-gambling game",
    ),
    'http:school_2937': dict(
        school='Northwoods Aviation', site='right', name='echo',
        empty_class='no_fleet',
        note="one line of text ('Learn to Fly Floats. Learn to Fly Skiplanes. Learn to Fly Tailwheel.')",
    ),
    'browser:browser_0033': dict(
        school='Fly The Wing (Garry Wing, CFI-I)', site='right', name='not_asked',
        empty_class='no_fleet',
        note="an instructor's blog and video site, no aircraft",
    ),
    'browser:browser_0820': dict(
        school='Skybound Aviation (per the directory)', site='wrong', name='not_asked',
        empty_class='wrong_site',
        note="an 'Access Denied' page and a GoDaddy for-sale page carrying an unrelated article; the model typed it defunct",
    ),
    'browser:browser_1132': dict(
        school='Langer Aviation', site='right', name='not_asked',
        empty_class='no_fleet',
        note="home page; a testimonial mentions 'the planes' but none is named",
    ),
}


def model_tails(d):
    return [norm_tail(a.get("n_number")) for a in d.get("fleet") or [] if norm_tail(a.get("n_number"))]


def cmd_score(c, args):
    sample, skipped, sizes = draw(c)
    keys = [f"{k[0]}:{k[1]}" for _, k, _ in sample]
    missing = [k for k in keys if k not in LABELS]
    extra = [k for k in LABELS if k not in keys]
    if missing or extra:
        sys.exit(f"labels do not match the draw: unlabelled {missing}, not drawn {extra}")
    T, R, WET, LAB, SITE, NAME, EMPTY = Counter(), Counter(), Counter(), Counter(), Counter(), Counter(), Counter()
    per = Counter()
    by_pass = defaultdict(Counter)
    rows, problems = [], []
    for s, key, m in sample:
        k = f"{key[0]}:{key[1]}"
        L = LABELS[k]
        d, _ = c.calls[key]
        fleet = d.get("fleet") or []
        truth = {norm_tail(t) for t in L.get("tails", {})}
        mts = set(model_tails(d))
        corr, false, miss = mts & truth, mts - truth, truth - mts
        for name, n in (("model", len(mts)), ("correct", len(corr)), ("false", len(false)), ("missed", len(miss))):
            T[name] += n
            by_pass[key[0]][name] += n
        m_unt = [a for a in fleet if not norm_tail(a.get("n_number"))]
        uc = L.get("untailed_correct", 0)
        if uc > len(m_unt) or uc > len(L.get("untailed", [])):
            problems.append(f"{k}: untailed_correct {uc} exceeds model or page count")
        T["u_model"] += len(m_unt); T["u_truth"] += len(L.get("untailed", [])); T["u_correct"] += uc
        if s == "F":
            per["F"] += 1
            if mts:
                per["with_tails"] += 1; per["no_false"] += not false
            if truth:
                per["truth_tails"] += 1; per["no_missed"] += not miss
            per["any_false_entry"] += bool(false) or len(m_unt) > uc
        nonnull = [a for a in fleet if a.get("wet_rate_hr") is not None]
        mr = L.get("model_rates", {})
        if len(mr) != len(nonnull):
            problems.append(f"{k}: {len(nonnull)} non-null rates returned but {len(mr)} rate verdicts")
        for v in mr.values():
            num, wet, lab = v.split("/")
            R[num] += 1; WET[wet] += 1; LAB[lab] += 1
        R["null_dry"] += L.get("null_dry", 0); R["missed"] += L.get("missed_rate", 0)
        SITE[L["site"]] += 1; NAME[L["name"]] += 1
        if s == "E":
            EMPTY[L["empty_class"]] += 1
            if fleet:
                problems.append(f"{k}: stratum E but fleet is not empty")
        rows.append((s, key, m, L, d, sorted(corr), sorted(false), sorted(miss), len(m_unt), uc))
    if problems:
        sys.exit("label problems:\n  " + "\n  ".join(problems))

    out = print
    out("## 3. Results\n")
    out(f"Generated by `extraction_audit.py score` from the labels in that file. Pool: {sizes['F']:,} calls with a fleet, "
        f"{sizes['E']:,} with an empty fleet (both passes). Drawn: {len(sample)} ({TARGET['F']} F, {TARGET['E']} E); "
        f"skipped as unidentifiable on the way: {len(skipped)}.\n")
    out("### Tails\n")
    out(f"- Precision: {pct(T['correct'], T['model'])}. False tails: {T['false']}.")
    out(f"- Recall: {pct(T['correct'], T['correct'] + T['missed'])}. Missed tails: {T['missed']}.")
    for p in ("http", "browser"):
        b = by_pass[p]
        out(f"  - {p} pass: {b['correct']} correct, {b['false']} false, {b['missed']} missed of {b['model']} returned.")
    out(f"- Per school (stratum F, {per['F']} calls): {per['no_false']} of {per['with_tails']} calls that returned tails had no false tail; "
        f"{per['no_missed']} of {per['truth_tails']} calls whose pages showed tails missed none.")
    out("\n### Aircraft without a tail\n")
    ua = T["u_model"]
    out(f"- The model returned {ua} untailed entries; {T['u_correct']} match an aircraft or type on the pages ({pct(T['u_correct'], ua)}), "
        f"{ua - T['u_correct']} do not. The pages show {T['u_truth']} untailed aircraft or types; the model found {pct(T['u_correct'], T['u_truth'])}.")
    allm, allc, allt = T["model"] + ua, T["correct"] + T["u_correct"], T["correct"] + T["missed"] + T["u_truth"]
    out(f"- All fleet entries, tailed or not: precision {pct(allc, allm)}; recall {pct(allc, allt)}.")
    out(f"- Calls in stratum F with at least one false entry (tailed or not): {per['any_false_entry']} of {per['F']}.")
    out("\n### Rates\n")
    nr = sum(R[x] for x in ("right", "wrong_attach", "invented", "not_aircraft"))
    out(f"- Non-null `wet_rate_hr` values returned: {nr}. The number is posted for that aircraft: {pct(R['right'], nr)}; "
        f"attached to the wrong aircraft: {R['wrong_attach']}; on no page: {R['invented']}; a simulator's price: {R['not_aircraft']}.")
    out(f"- Wet status on the page of those {nr}: " + ", ".join(f"{k} {v}" for k, v in WET.most_common()) + ".")
    out(f"- Label of the posted price taken: " + ", ".join(f"{k} {v}" for k, v in LAB.most_common()) + ".")
    out(f"- Posted per-aircraft prices left null: {R['null_dry'] + R['missed']} ({R['null_dry']} because the page says dry).")
    out("\n### School and site\n")
    out(f"- The pages are the named school's site: {SITE['right']}; another or a dead site: {SITE['wrong']}; unreadable: {SITE['unreadable']} (of {len(rows)}).")
    out(f"- HTTP pass `canonical_name` (09c asked for none): " + ", ".join(f"{k} {v}" for k, v in NAME.most_common() if k != "not_asked") + ".")
    out(f"\n### Empty extractions (stratum E, {sum(EMPTY.values())})\n")
    out("- " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in EMPTY.most_common()) + (", model missed 0." if not EMPTY["model_missed"] else "."))
    out("\n### Per call\n")
    out("Stratum, pass and custom_id identify the call; tails are normalised (upper case, N added). "
        "Aircraft on the pages: my reading. Model: its fleet entries with any wet_rate_hr. Tails: correct / false / missed. "
        "Untailed: model entries that match / model entries.\n")
    out("| # | Call | School (domain) | Pages | On the pages | Model returned | Tails c/f/m | Untailed | Rates | Verdicts and notes |")
    out("|---|---|---|---|---|---|---|---|---|---|")
    esc = lambda x: str(x).replace("|", "/")
    for i, (s, key, m, L, d, corr, false, miss, mu, uc) in enumerate(rows):
        mine = "; ".join([f"{t} {v}" for t, v in L.get("tails", {}).items()] + L.get("untailed", [])) or "none"
        if L.get("rates"):
            mine += ". Prices: " + "; ".join(f"{k2} {v}" for k2, v in L["rates"].items())
        mod = "; ".join(f"{norm_tail(a.get('n_number')) or '-'} {a.get('display_name') or ''}".strip()
                        + (f" ${a['wet_rate_hr']:g}" if a.get("wet_rate_hr") is not None else "")
                        for a in d.get("fleet") or []) or "none"
        tv = f"{len(corr)} / {len(false)}" + (f" ({', '.join(false)})" if false else "") + f" / {len(miss)}" + (f" ({', '.join(miss)})" if miss else "")
        rv = "; ".join(f"{k2}: {v}" for k2, v in L.get("model_rates", {}).items())
        if L.get("null_dry"):
            rv += ("; " if rv else "") + f"{L['null_dry']} dry prices left null"
        verdict = f"{s}; site {L['site']}"
        if L["name"] != "not_asked":
            verdict += f"; name {L['name']} ({d.get('canonical_name') or ''})"
        if s == "E":
            verdict += f"; {L['empty_class'].replace('_', ' ')}"
        for f in ("stale", "note"):
            if L.get(f):
                verdict += f"; {f + ': ' if f == 'stale' else ''}{L[f]}"
        if L.get("judged_raw"):
            verdict += " (checked against the raw HTML)"
        out(f"| {i + 1} | {s} {key[0]} {key[1]} | {esc(L['school'])} ({m['domain']}) | {len(c.pages(key, m))} | {esc(mine)} | {esc(mod)} | {tv} | {uc} / {mu} | {esc(rv) or '-'} | {esc(verdict)} |")
    if skipped:
        out("\nSkipped while drawing (school not identifiable under the rule in section 1):\n")
        for s, key, why in skipped:
            out(f"- {s} {key[0]} {key[1]}: {why}")


BOILER = re.compile(r"(cookie|gdpr|consent|popup|modal|overlay|sidebar|widget|social|share|"
                    r"newsletter|subscribe|banner|advertisement|ad-|advert)", re.I)  # 09a clean_html's filter


def text_len(h):
    h = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", h, flags=re.S | re.I)
    return len(re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", h))).strip())


def cmd_body_loss(c, args):
    """How many HTTP-crawled schools lost (nearly) all page text in 09a's cleaning.

    A school counts when its raw pages hold at least 2,000 characters of visible text and its
    cleaned pages, which is what 09b sent, hold under 5% of that. The cause is checked on each:
    whether a <body> element's class or id matches 09a's boilerplate filter (so the filter
    deleted the whole body)."""
    raw_dir = c.out / "fleet_html_raw"
    if not raw_dir.exists():
        sys.exit(f"missing {raw_dir}")
    n = lost = body_hit = rescued = 0
    examples = []
    browser_domains = {e["domain"].lower() for e in c.bx.values()}
    for s in c.ci:
        raw_t = clean_t = 0
        body_match = False
        for p in s["pages"]:
            fc, fr = c.out / "fleet_html" / p["html_path"], raw_dir / p["html_path"]
            if not (fc.exists() and fr.exists()):
                continue
            rh = fr.read_text(encoding="utf-8", errors="replace")
            if is_binary(rh):
                continue
            raw_t += text_len(rh)
            clean_t += text_len(fc.read_text(encoding="utf-8", errors="replace"))
            m = re.search(r"<body([^>]*)>", rh[:200_000], re.I)
            if m and BOILER.search(" ".join(re.findall(r'(?:class|id)\s*=\s*["\']([^"\']*)', m.group(1)))):
                body_match = True
        if raw_t < 2000:
            continue
        n += 1
        if clean_t < 0.05 * raw_t:
            lost += 1
            body_hit += body_match
            rescued += dom(s["school_website"]) in browser_domains
            if len(examples) < 12:
                examples.append(dom(s["school_website"]))
    print(f"HTTP-crawl schools with >=2,000 characters of raw page text: {n:,}")
    print(f"  of which the cleaned pages the model saw kept under 5% of it: {lost:,} ({100 * lost / max(1, n):.1f}%)")
    print(f"  of those, a <body> class or id matches 09a's boilerplate filter: {body_hit:,}")
    print(f"  of those, also read by the browser pass (09c): {rescued:,}")
    print(f"  examples: {', '.join(examples)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--output", type=Path, default=Path(os.environ.get("AETHER_OUTPUT", ROOT / "output")))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sample")
    p = sub.add_parser("dump"); p.add_argument("dir")
    sub.add_parser("score")
    sub.add_parser("body-loss")
    args = ap.parse_args()
    c = Corpus(args.output)
    {"sample": cmd_sample, "dump": cmd_dump, "score": cmd_score, "body-loss": cmd_body_loss}[args.cmd](c, args)


if __name__ == "__main__":
    main()
