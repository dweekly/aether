"""Match external lists of flight schools (federal and state registries) against flight_line.json.

Usage (run with -I; inputs are untrusted downloads, so this script lives elsewhere):
  python -I match_external_school_lists.py FLIGHT_LINE_JSON AIRPORTS_CSV[,OURAIRPORTS_CSV] OUT_DIR SOURCE_CSV [SOURCE_CSV ...]

Each SOURCE_CSV must have the columns: source, name, city, state, website (website may be blank).
An optional 'address' column (street address with ZIP) enables the address rule. Other columns pass
through to the output. Output per source: OUT_DIR/<source>_matched.csv with
match_type, matched school name/domain, best candidate and score for unmatched rows.

Match rules, in order (the first that fires wins):
  domain   registered domain of the source website equals a school's domain or website domain
           (generic hosts such as facebook.com, wixsite.com and google sites are ignored)
  exact    normalized name (legal suffixes and punctuation removed) equal, same state
  address  same house number and same ZIP (or same house number, first street word and city) as a
           school's contact address, plus a shared distinctive name token or ratio >= 0.6; an address
           hit without that is 'colocated' (another tenant at the same airport address), not a match
  fuzzy    same state, and either SequenceMatcher ratio >= 0.95 on normalized names, or the distinctive
           tokens (generic words like aviation, flight, academy, and the source city's words removed) overlap (Jaccard >= 0.5, or one
           set inside the other) together with a city match or a ratio >= 0.85
A city match means the source city equals the city in the school's contact address or the city of
any of its airports (airports.csv). Everything else is 'none', with the best same-state candidate.
"""
import csv, json, re, sys
from difflib import SequenceMatcher
from pathlib import Path

GENERIC_HOSTS = {'facebook.com', 'wixsite.com', 'google.com', 'business.site', 'squarespace.com',
                 'weebly.com', 'wordpress.com', 'godaddysites.com', 'linkedin.com', 'instagram.com',
                 'yelp.com', 'blogspot.com', 'gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'aol.com',
                 'icloud.com', 'comcast.net', 'msn.com', 'live.com', 'me.com', 'att.net', 'verizon.net', 'earthlink.net', 'square.site', 'site123.me', 'webs.com', 'myshopify.com'}
LEGAL = r'\b(incorporated|inc|llc|l l c|corp|corporation|co|company|ltd|lp|llp|pllc|pc|the)\b'
GENERIC = {'aviation', 'flight', 'flying', 'fly', 'school', 'schools', 'academy', 'training', 'center',
           'centre', 'aero', 'aeronautics', 'aeronautical', 'air', 'pilot', 'pilots', 'services', 'service',
           'club', 'college', 'university', 'institute', 'of', 'and', 'at', 'a', 'in', 'aircraft',
           'international', 'intl', 'group', 'enterprises', 'department', 'dept', 'program', 'campus',
           'helicopter', 'helicopters', 'heli', 'airport', 'municipal', 'community', 'state', 'education',
           'llc', 'inc', 'north', 'south', 'east', 'west', 'central', 'northern', 'southern', 'eastern',
           'western', 'county', 'learning', 'solutions', 'systems', 'foundation', 'aerospace', 'science', 'sciences', 'technology', 'professional', 'instruction'}


def reg_domain(url):
    if not url:
        return ''
    u = url.strip().lower()
    u = re.sub(r'^[a-z]+://', '', u).split('/')[0].split('?')[0].split(':')[0]
    u = re.sub(r'^www\d?\.', '', u)
    parts = [p for p in u.split('.') if p]
    if len(parts) < 2:
        return ''
    d = '.'.join(parts[-2:])
    return '' if d in GENERIC_HOSTS else d


def variants(name):
    n = (name or '').lower().replace('&', ' and ').replace("'", '').replace('\u2019', '')
    pieces = re.split(r'\bd\s*/?\s*b\s*/?\s*a\b|\(|\)|\s-\s|/|,\s*dba\s', n)
    out = []
    for p in [n] + pieces:
        p = re.sub(r"[^a-z0-9 ]", ' ', p)
        p = re.sub(LEGAL, ' ', p)
        p = re.sub(r'\s+', ' ', p).strip()
        if len(p) >= 3 and p not in out:
            out.append(p)
    return out


def core(v):
    return {t for t in v.split() if t not in GENERIC and len(t) > 1}


def norm_city(c):
    c = (c or '').lower().strip()
    c = re.sub(r'[^a-z ]', ' ', c)
    c = re.sub(r'\bsaint\b', 'st', c)
    c = re.sub(r'\bfort\b', 'ft', c)
    return re.sub(r'\s+', ' ', c).strip()


STATES = {'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR', 'california': 'CA', 'colorado': 'CO',
          'connecticut': 'CT', 'delaware': 'DE', 'district of columbia': 'DC', 'florida': 'FL', 'georgia': 'GA',
          'hawaii': 'HI', 'idaho': 'ID', 'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA', 'kansas': 'KS',
          'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD', 'massachusetts': 'MA',
          'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS', 'missouri': 'MO', 'montana': 'MT',
          'nebraska': 'NE', 'nevada': 'NV', 'new hampshire': 'NH', 'new jersey': 'NJ', 'new mexico': 'NM',
          'new york': 'NY', 'north carolina': 'NC', 'north dakota': 'ND', 'ohio': 'OH', 'oklahoma': 'OK',
          'oregon': 'OR', 'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC', 'south dakota': 'SD',
          'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT', 'vermont': 'VT', 'virginia': 'VA', 'washington': 'WA',
          'west virginia': 'WV', 'wisconsin': 'WI', 'wyoming': 'WY', 'puerto rico': 'PR', 'guam': 'GU'}
CODE_TO_STATE = {v: k for k, v in STATES.items()}
ADDR = re.compile(r'([A-Za-z .\'-]+),\s*(' + '|'.join(sorted(STATES, key=len, reverse=True)) + r'|[A-Z]{2})\b(?=[\s\d,-]*(?:usa|united states)?\s*$|\s+\d{5})',
                  re.I)


def parse_addr(addr):
    """Last 'City, ST' or 'City, Statename' in an address, or None."""
    ms = list(ADDR.finditer(addr or ''))
    if not ms:
        return None
    m = ms[-1]
    st = m.group(2)
    st = STATES.get(st.lower(), st.upper())
    if st not in STATES.values():
        return None
    return norm_city(m.group(1).split(',')[-1]), st


def load_airports(ap_paths):
    """airports.csv (airport_id, city, state) and/or OurAirports (ident, municipality, iso_region)."""
    ap = {}
    for path in ap_paths.split(','):
        with open(path, newline='', encoding='utf-8') as f:
            for r in csv.DictReader(f):
                if 'airport_id' in r:
                    keys, val = (r['airport_id'], r.get('faa_id'), r.get('gps_code')), (r['city'], r['state'])
                else:
                    if r.get('iso_country') != 'US':
                        continue
                    keys = (r['ident'], r.get('gps_code'), r.get('local_code'), r.get('icao_code'))
                    val = (r['municipality'], (r['iso_region'] or '').replace('US-', ''))
                for k in keys:
                    if k and k.upper() not in ap:
                        ap[k.upper()] = (norm_city(val[0]), (val[1] or '').upper())
    return ap


def load_schools(fl_path, ap_path):
    ap = load_airports(ap_path)
    schools = json.load(open(fl_path, encoding='utf-8'))
    out = []
    for s in schools:
        cities, states = set(), set()
        for loc in s.get('locations') or []:
            a = ap.get((loc.get('airport_id') or '').upper())
            if a:
                cities.add(a[0]); states.add(a[1])
        addr = (s.get('contact') or {}).get('address') or ''
        pa = parse_addr(addr)
        if pa:
            cities.add(pa[0]); states.add(pa[1])
        doms = {reg_domain(s.get('domain') or ''), reg_domain((s.get('contact') or {}).get('website') or '')} - {''}
        vs = variants(s.get('name') or '')
        out.append(dict(addr=addr_key(addr), name=s.get('name') or '', domain=s.get('domain') or '', doms=doms, cities=cities,
                        states=states, variants=vs, cores=[core(v) for v in vs]))
    return out


def addr_key(addr):
    """(house number, first street word, ZIP) from a street address; parts may be ''."""
    a = (addr or '').lower()
    m = re.search(r'\b(\d{2,6})\s+(?:[nsew]\.?\s+|north\s+|south\s+|east\s+|west\s+|sw\s+|nw\s+|se\s+|ne\s+)?([a-z0-9]+)', a)
    z = re.findall(r'\b(\d{5})(?:-\d{4})?\b', a)
    return (m.group(1) if m else '', m.group(2) if m else '', z[-1] if z else '')


def place_tokens(city):
    """Words of a normalized city name, with ft/st also spelled out, for removal from name cores."""
    toks = set(city.split())
    return toks | ({'fort'} if 'ft' in toks else set()) | ({'saint'} if 'st' in toks else set())


def best(row_vs, row_cores, city, cands, state_words=frozenset()):
    """Return (accepted, score, school): the best candidate meeting a fuzzy rule, else the best by ratio."""
    ok, top = [], (0.0, None)
    for s in cands:
        cm = bool(city) and city in s['cities']
        r = max((SequenceMatcher(None, a, b).ratio() for a in row_vs for b in s['variants']), default=0)
        place = place_tokens(city) | state_words
        pairs = [(c1, c2 - place) for c1 in row_cores for c2 in s['cores'] if c1 and c2 - place]
        jac = max((len(c1 & c2) / len(c1 | c2) for c1, c2 in pairs), default=0)
        contained = any(c1 <= c2 or c2 <= c1 for c1, c2 in pairs)
        if r >= 0.95 or ((jac >= 0.5 or contained) and (cm or r >= 0.85)):
            ok.append((r, s))
        if r > top[0]:
            top = (r, s)
    if ok:
        r, s = max(ok, key=lambda x: x[0])
        return True, r, s
    return False, top[0], top[1]


def main():
    fl, ap, outdir = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    outdir.mkdir(parents=True, exist_ok=True)
    schools = load_schools(fl, ap)
    by_dom, by_state = {}, {}
    for s in schools:
        for d in s['doms']:
            by_dom.setdefault(d, []).append(s)
        for st in s['states']:
            by_state.setdefault(st, []).append(s)
    print(f'{len(schools)} schools loaded; {sum(1 for s in schools if s["states"])} with a state')
    for src in sys.argv[4:]:
        rows = list(csv.DictReader(open(src, newline='', encoding='utf-8')))
        counts = {}
        for r in rows:
            st = (r.get('state') or '').strip().upper()
            city = norm_city(r.get('city'))
            vs = variants(r.get('name'))
            place = place_tokens(city) | {st.lower()} | set(CODE_TO_STATE.get(st, '').split())
            cs = [core(v) - place for v in vs]
            d = reg_domain(r.get('website'))
            mt, hit, score = 'none', None, 0.0
            if d and d in by_dom:
                hit = max(by_dom[d], key=lambda s: max((SequenceMatcher(None, a, b).ratio()
                                                        for a in vs for b in s['variants']), default=0))
                mt, score = 'domain', 1.0
            else:
                cands = by_state.get(st, [])
                ex = [s for s in cands if set(vs) & set(s['variants'])]
                ak = addr_key(r.get('address'))
                ad = [s for s in cands if ak[0] and s['addr'][0] == ak[0] and
                      ((ak[2] and s['addr'][2] == ak[2]) or (ak[1] and s['addr'][1] == ak[1] and city in s['cities']))]
                if ex:
                    mt, hit, score = 'exact', ex[0], 1.0
                else:
                    if ad:
                        # Airport street addresses are shared by tenants, so an address hit needs some
                        # name signal too; without one it is 'colocated' and counts as unmatched.
                        def sig(s):
                            r_ = max((SequenceMatcher(None, a, b).ratio() for a in vs for b in s['variants']), default=0)
                            return r_, any(c1 & c2 for c1 in cs for c2 in s['cores'])
                        (r_, ov), s = max(((sig(s), s) for s in ad), key=lambda x: x[0])
                        mt, hit, score = ('address' if (ov or r_ >= 0.6) else 'colocated'), s, r_
                    if mt != 'address':
                        accepted, fscore, fhit = best(vs, cs, city, cands, set(CODE_TO_STATE.get(st, '').split()))
                        if accepted:
                            mt, hit, score = 'fuzzy', fhit, fscore
                        elif mt != 'colocated':
                            hit, score = fhit, fscore
            r['match_type'] = mt
            r['match_score'] = f'{score:.2f}'
            r['school_name'] = hit['name'] if hit else ''
            r['school_domain'] = hit['domain'] if hit else ''
            counts[mt] = counts.get(mt, 0) + 1
        name = Path(src).stem
        w = csv.DictWriter(open(outdir / f'{name}_matched.csv', 'w', newline='', encoding='utf-8'),
                           fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
        print(name, len(rows), counts)


if __name__ == '__main__':
    main()
