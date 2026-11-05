#!/usr/bin/env python3
"""Three more full-slide charts (1920x1080 PNG) for the Hackers 2026 deck, in 53's look.

  blocked.png        Who refuses our crawler now: the 207 school sites that served it in March,
                     by the system that answered in November (62_blocked_sites.py's output)
  yearbuilt_make.png New US single-engine piston airplanes shipped a year (GAMA), and the year
                     built of four-seat piston singles by make: on school fleets, and seen flying
  wetrate_map.png    Median posted wet rate of a Cessna 172 by state, with the cheapest and
                     priciest metros and the Bay Area

Definitions (also in docs/hackers-2026-slide-research.md):
  four-seat piston single  registry aircraft_type "Fixed wing single engine", engine type
                           Reciprocating, 4 Cycle or 2 Cycle, 4 or 5 seats (ACFTREF seat count,
                           which includes the pilot), year built 1940-2026
  make                     Cessna (CESSNA*, and TEXTRON AVIATION INC models that start with a
                           digit or T+digit: 172S, 182T, T206H...), Piper (PIPER*), Cirrus
                           (CIRRUS*), Diamond (DIAMOND*), everything else Other
  seen flying              days_airborne > 0 in the release's ADS-B window
  172 wet rate             a fleet entry whose registry make is Cessna/Textron and model starts
                           "172" (not 172RG), or with no registry match a display name with
                           "172"; floats, seaplane and amphibian entries and schools based at a
                           seaplane base are left out; one value per school (its median)
  metro                    the Census Combined Statistical Area the school's first airport is
                           in, else its CBSA (2023 cartographic boundaries)

Usage (on the Mac, worktree venv; needs matplotlib, duckdb, pyshp, shapely):
  64_slide_charts.py --fonts DIR --flight-line output/flight_line.json \\
      --aircraft aircraft.parquet --gama ../../docs/hackers-2026-gama-piston-shipments.csv \\
      --airports data/ourairports_airports.csv --census DIR --blocked blocked_sites.json \\
      --out ../../docs/hackers-2026-charts/backdrops [--min-schools 10]
Exits 1 if an input is missing or a chart's data looks wrong.
"""

import argparse
import csv
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from importlib import import_module
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.patheffects  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
B = import_module("53_talk_backdrops")
BG, INK, MUTED, GRID, AMBER, BUFF = B.BG, B.INK, B.MUTED, B.GRID, B.AMBER, B.BUFF
# Makes: four hues validated together on the deck's surface (dataviz validate_palette.js,
# --mode dark --surface #0f1517: all checks pass in this stack order), Other in a neutral.
MAKES = ["Cessna", "Piper", "Diamond", "Cirrus", "Other"]
MAKE_COLOR = {"Cessna": "#3987e5", "Piper": "#199e70", "Diamond": "#9085e9", "Cirrus": "#c98500", "Other": "#46524f"}
# Sequential blue, dark (cheap) to light (dear), steps 600..200 of the reference ramp.
SEQ = ["#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4"]

MAKE_SQL = """CASE WHEN mfr LIKE 'CIRRUS%' THEN 'Cirrus'
  WHEN mfr LIKE 'DIAMOND%' THEN 'Diamond'
  WHEN mfr LIKE 'CESSNA%' OR (mfr = 'TEXTRON AVIATION INC' AND regexp_matches(model, '^T?[0-9]')) THEN 'Cessna'
  WHEN mfr LIKE 'PIPER%' THEN 'Piper' ELSE 'Other' END"""
CLASS_SQL = """aircraft_type = 'Fixed wing single engine' AND engine_type IN ('Reciprocating', '4 Cycle', '2 Cycle')
  AND seats BETWEEN 4 AND 5 AND year_mfr BETWEEN 1940 AND 2026"""


def school_tails(fl):
    return sorted({re.sub(r"\s+", "", f["n_number"]).upper() for s in fl for f in (s.get("fleet") or [])
                   if (f.get("n_number") or "").strip()})


# ── Slide 2: year built by make ──────────────────────────────────


def yearbuilt_make(a, fl, out):
    import duckdb
    c = duckdb.connect()
    c.execute("CREATE TABLE t AS SELECT unnest(?) AS n", [school_tails(fl)])
    # Sensitivity: only tails read off a school's own website (flight_line entries with a domain),
    # leaving out the registry address matches (some of which are not schools).
    c.execute("CREATE TABLE tw AS SELECT unnest(?) AS n", [school_tails([s for s in fl if s.get("domain")])])
    c.execute(f"""CREATE TABLE a AS SELECT year_mfr, model, {MAKE_SQL} AS make, n_number IN (SELECT n FROM t) AS school,
                  n_number IN (SELECT n FROM tw) AS school_web,
                  coalesce(days_airborne, 0) > 0 AS flew FROM read_parquet('{a.aircraft}') WHERE {CLASS_SQL}""")
    pops = {}
    for pop in ("school", "school_web", "flew"):
        rows = c.execute(f"SELECT year_mfr, make, count(*) FROM a WHERE {pop} GROUP BY ALL").fetchall()
        d = defaultdict(lambda: defaultdict(int))
        for y, m, n in rows:
            d[m][y] += n
        pops[pop] = d
    tot = {p: sum(sum(v.values()) for v in pops[p].values()) for p in pops}
    if tot["school"] < 5000 or tot["flew"] < 40000:
        sys.exit(f"FAIL yearbuilt_make: {tot}")

    def share(pop, make, lo, hi):
        n = sum(pops[pop][make].get(y, 0) for y in range(lo, hi + 1))
        d = sum(pops[pop][m].get(y, 0) for m in MAKES for y in range(lo, hi + 1))
        return round(100 * n / d, 1) if d else None, d

    def built(pop, lo, hi):
        return sum(pops[pop][m].get(y, 0) for m in MAKES for y in range(lo, hi + 1))

    eras = [(1940, 1986), (1987, 1996), (1997, 2009), (2010, 2026)]
    res = {"tails": tot,
           "share_all": {p: {m: share(p, m, 1940, 2026)[0] for m in MAKES} for p in pops},
           "share_built_2010_on": {p: {m: share(p, m, 2010, 2026)[0] for m in MAKES} for p in pops},
           "cirrus_share_by_era": {p: {f"{lo}-{hi}": share(p, "Cirrus", lo, hi) for lo, hi in eras} for p in pops},
           "cessna_share_by_era": {p: {f"{lo}-{hi}": share(p, "Cessna", lo, hi) for lo, hi in eras} for p in pops},
           "built_1975_79_vs_1987_96": {p: [built(p, 1975, 1979), built(p, 1987, 1996)] for p in pops},
           "share_built_1986_or_before": {p: round(100 * built(p, 1940, 1986) / tot[p], 1) for p in pops},
           "share_built_1970s": {p: round(100 * built(p, 1970, 1979) / tot[p], 1) for p in pops},
           "median_year": {p: c.execute(f"SELECT median(year_mfr) FROM a WHERE {p}").fetchone()[0] for p in pops},
           "top_models_built_2010_on_school": c.execute(
               "SELECT make, model, count(*) FROM a WHERE school AND year_mfr >= 2010 GROUP BY ALL ORDER BY 3 DESC LIMIT 6").fetchall()}
    gama = {int(r["year"]): int(r["single_engine_piston_shipped"]) for r in csv.DictReader(a.gama.open())}
    res["gama"] = {"peak": max(gama.items(), key=lambda kv: kv[1]), "low": min(gama.items(), key=lambda kv: kv[1]),
                   "1986": gama[1986], "1997": gama[1997], "first": min(gama), "last": max(gama)}
    cs = res["share_built_2010_on"]["school"]
    cf = res["share_built_2010_on"]["flew"]

    fig = B.frame("The industry nearly stopped building small planes",
                  "So schools fly 1970s airplanes. Of the four-seaters built since 2010, half are Cirrus.")
    x0, w = 0.075, 0.68
    axes = [fig.add_axes([x0, 0.605, w, 0.175]), fig.add_axes([x0, 0.365, w, 0.17]), fig.add_axes([x0, 0.125, w, 0.17])]
    years = list(range(1945, 2027))
    ax = axes[0]
    gy = sorted(gama)
    ax.fill_between(gy, [gama[y] for y in gy], color=BUFF, alpha=0.22, lw=0, step="mid")
    ax.step(gy, [gama[y] for y in gy], where="mid", color=BUFF, lw=2.5)
    py, pv = res["gama"]["peak"]
    ly, lv = res["gama"]["low"]
    ax.text(py - 0.8, pv + 600, f"{pv:,} in {py}", fontsize=24, color=INK, ha="right", va="bottom")
    ax.annotate(f"{lv:,} in {ly}", xy=(ly, lv), xytext=(ly + 6.5, 6200), fontsize=24, color=INK,
                arrowprops=dict(arrowstyle="-", color=MUTED, lw=1.5))
    ax.set_ylim(0, 18500)
    ax.set_yticks([0, 5000, 10000, 15000])
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v / 1000:.0f}k" if v else "0"))
    titles = [f"New US-built piston singles shipped each year (GAMA, {res['gama']['first']}–{res['gama']['last']})",
              f"On flight-school fleets: {tot['school']:,} four-seat piston singles, by year built",
              f"Seen flying on ADS-B in the past year: {tot['flew']:,}, by year built"]
    for ax_, pop in ((axes[1], "school"), (axes[2], "flew")):
        bottom = [0] * len(years)
        for m in MAKES:
            vals = [pops[pop][m].get(y, 0) for y in years]
            ax_.bar(years, vals, bottom=bottom, width=0.86, color=MAKE_COLOR[m], linewidth=0)
            bottom = [b + v for b, v in zip(bottom, vals)]
        ax_.set_ylim(0, max(bottom) * 1.08)
        ax_.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(3, integer=True))
        ax_.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    for ax_, t in zip(axes, titles):
        ax_.set_title(t, loc="left", fontsize=25, color=INK, pad=10)
        ax_.set_xlim(1944.5, 2026.5)
        ax_.tick_params(labelsize=20, length=0)
        ax_.grid(axis="x", visible=False)
        for xv in (1986.5, 1994.6):
            ax_.axvline(xv, color=MUTED, lw=1.2, ls=(0, (3, 4)), zorder=0)
        if ax_ is not axes[2]:
            ax_.set_xticklabels([])
    axes[0].text(1987.2, 17800, "1986: Cessna stops building piston singles", fontsize=21, color=MUTED, va="top")
    axes[0].text(1995.2, 13300, "1994: liability reform (GARA)", fontsize=21, color=MUTED, va="top")
    # Legend and the shares, right of the panels.
    lx = x0 + w + 0.035
    fig.text(lx, 0.765, "Make", fontsize=24, color=MUTED)
    for i, m in enumerate(reversed(MAKES)):
        yy = 0.72 - i * 0.043
        fig.patches.append(matplotlib.patches.Rectangle((lx, yy), 0.016, 0.026, transform=fig.transFigure,
                                                        color=MAKE_COLOR[m], figure=fig))
        fig.text(lx + 0.024, yy + 0.002, m, fontsize=24, color=INK)
    fig.text(lx, 0.47, "Built since 2010:", fontsize=26, color=MUTED, va="top")
    fig.text(lx, 0.42, f"{cf['Cirrus']:.0f}%", fontsize=56, color=INK, fontweight="semibold", va="top")
    fig.text(lx, 0.345, "of those flying are Cirrus", fontsize=23, color=MUTED, va="top")
    fig.text(lx, 0.28, f"{cs['Cirrus']:.0f}%", fontsize=56, color=INK, fontweight="semibold", va="top")
    fig.text(lx, 0.205, f"of school fleets are Cirrus;\n{cs['Piper']:.0f}% are Pipers", fontsize=23, color=MUTED,
             va="top", linespacing=1.25)
    B.footer(fig, "GAMA Databooks 2016 and 2019. FAA registry 2026-10-23 (single-engine piston, 4–5 seats). "
                  "ADS-B: Oct 2025–Oct 2026.")
    fig.savefig(out / "yearbuilt_make.png")
    plt.close(fig)
    return res


# ── Slide 3: what a 172 rents for, on a map ──────────────────────


def airport_index(path):
    pos = {}
    for r in csv.DictReader(path.open(encoding="utf-8")):
        if r["iso_country"] != "US":
            continue
        rec = (float(r["latitude_deg"]), float(r["longitude_deg"]), r["type"])
        for code in (r["ident"], r["gps_code"], r["local_code"], "K" + r["local_code"] if len(r["local_code"]) == 3 else ""):
            if code:
                pos.setdefault(code.upper(), rec)
    return pos


SEAPLANE = re.compile(r"float|seaplane|amphib|on floats|\bseaplanes?\b", re.I)


def is_172(f):
    fa = f.get("faa") or {}
    mfr, mdl = (fa.get("mfr") or "").upper(), (fa.get("model") or "").upper()
    if fa:
        return ("CESSNA" in mfr or "TEXTRON" in mfr) and mdl.startswith("172") and "RG" not in mdl
    return re.search(r"\b172(?!\s*-?RG)", f.get("display_name") or "") is not None


def school_rates(fl, pos):
    out, dropped = [], defaultdict(int)
    for s in fl:
        rates = [f["wet_rate_hr"] for f in s.get("fleet") or [] if f.get("wet_rate_hr") and is_172(f)
                 and not SEAPLANE.search(" ".join(str(f.get(k) or "") for k in ("display_name", "description")))]
        if not rates:
            continue
        ap = next((pos[l["airport_id"].upper()] for l in s.get("locations") or []
                   if l.get("airport_id") and l["airport_id"].upper() in pos), None)
        if not ap:
            dropped["no_airport"] += 1
            continue
        if ap[2] == "seaplane_base":
            dropped["seaplane_base"] += 1
            continue
        out.append({"domain": s["domain"], "rate": statistics.median(rates), "tails": len(rates),
                    "lat": ap[0], "lon": ap[1], "type": s.get("aopa_type") or ""})
    return out, dict(dropped)


def load_shapes(path):
    import shapefile
    from shapely.geometry import shape
    r = shapefile.Reader(str(path))
    names = [f[0] for f in r.fields[1:]]
    return [(dict(zip(names, rec.record)), shape(rec.shape.__geo_interface__)) for rec in r.shapeRecords()]


def locate(points, shapes, key):
    from shapely.geometry import Point
    from shapely.strtree import STRtree
    geoms = [g for _, g in shapes]
    tree = STRtree(geoms)
    for p in points:
        pt = Point(p["lon"], p["lat"])
        hit = next((shapes[i][0] for i in tree.query(pt) if geoms[i].contains(pt)), None)
        p[key] = hit


def wetrate_map(a, fl, out):
    pos = airport_index(a.airports)
    pts, dropped = school_rates(fl, pos)
    states = load_shapes(a.census / "cb_2023_us_state_20m" / "cb_2023_us_state_20m")
    csa = load_shapes(a.census / "cb_2023_us_csa_500k" / "cb_2023_us_csa_500k")
    cbsa = load_shapes(a.census / "cb_2023_us_cbsa_500k" / "cb_2023_us_cbsa_500k")
    locate(pts, states, "state")
    locate(pts, csa, "csa")
    locate(pts, cbsa, "cbsa")
    for p in pts:
        p["st"] = p["state"]["STUSPS"] if p["state"] else ""
        p["metro"] = (p["csa"] or p["cbsa"] or {}).get("NAME", "")
    lower48 = [p for p in pts if p["st"] and p["st"] not in ("AK", "HI", "PR")]
    if len(lower48) < 400:
        sys.exit(f"FAIL wetrate_map: only {len(lower48)} schools placed")
    mn = a.min_schools

    def groups(key):
        g = defaultdict(list)
        for p in lower48:
            if p[key]:
                g[p[key]].append(p)
        return {k: v for k, v in g.items()}

    def stat(v):
        r = [p["rate"] for p in v]
        q = statistics.quantiles(r, n=4) if len(r) >= 4 else [None, None, None]
        return {"n": len(r), "median": statistics.median(r), "p25": q[0], "p75": q[2],
                "lat": statistics.mean(p["lat"] for p in v), "lon": statistics.mean(p["lon"] for p in v)}

    st = {k: stat(v) for k, v in groups("st").items()}
    mt = {k: stat(v) for k, v in groups("metro").items()}
    st_ok = {k: v for k, v in st.items() if v["n"] >= mn}
    mt_ok = {k: v for k, v in mt.items() if v["n"] >= mn}
    if len(mt_ok) < 6 or len(st_ok) < 8:
        sys.exit(f"FAIL wetrate_map: {len(mt_ok)} metros, {len(st_ok)} states with n >= {mn}")
    bay = next(k for k in mt if k.startswith("San Jose-San Francisco-Oakland"))
    ranked = sorted(mt_ok, key=lambda k: mt_ok[k]["median"])
    cheap, dear = ranked[:3], ranked[-3:]
    national = statistics.median(p["rate"] for p in lower48)
    clubs = [p["rate"] for p in lower48 if p["type"] == "Flying Club"]
    res = {"schools": len(lower48), "dropped": dropped, "outside_lower48": len(pts) - len(lower48),
           "national_median": national, "min_schools": mn,
           "club_median": statistics.median(clubs) if clubs else None, "clubs": len(clubs),
           "states_shown": {k: st_ok[k] for k in sorted(st_ok, key=lambda k: st_ok[k]["median"])},
           "metros_shown": {k: mt_ok[k] for k in ranked}, "bay_area": {bay: mt[bay]},
           "cheapest": cheap, "priciest": dear,
           "club_share_by_metro": {k: round(sum(1 for p in groups("metro")[k] if p["type"] == "Flying Club") / mt_ok[k]["n"], 2)
                                   for k in ranked}}
    lo, hi = mt_ok[cheap[0]]["median"], mt[bay]["median"]
    res["bay_minus_cheapest_hr"] = hi - lo
    res["at_40_hours"] = 40 * (hi - lo)  # 14 CFR 61.109(a): 40 hours minimum for a private certificate

    # Within-metro spread against between-metro spread, for the slide's claim.
    iqr = {k: v["p75"] - v["p25"] for k, v in mt_ok.items()}
    res["metro_median_range"] = [mt_ok[cheap[0]]["median"], mt_ok[dear[-1]]["median"]]
    res["metro_iqr_width_median"] = statistics.median(iqr.values())
    res["bay_area_rates"] = sorted(p["rate"] for p in groups("metro")[bay])
    res["cheapest_to_priciest_at_40_hours"] = 40 * (mt_ok[dear[-1]]["median"] - mt_ok[cheap[0]]["median"])
    res["_schools"] = [{k: p[k] for k in ("domain", "rate", "tails", "st", "metro", "type")} for p in lower48]

    # Fixed $10 classes, so equal steps in shade are equal steps in dollars.
    edges = [150, 160, 170, 180]
    labels = ["under $150", "$150–159", "$160–169", "$170–179", "$180 and up"]

    def shade(v):
        return SEQ[sum(v >= e for e in edges)]

    k = math.cos(math.radians(38))
    lo_m, hi_m = mt_ok[cheap[0]], mt_ok[dear[-1]]
    fig = B.frame("Where you live barely changes the price",
                  f"Cessna 172 with fuel, median posted hourly rate by state ({mn}+ schools) and metro")
    ax = fig.add_axes([0.015, 0.10, 0.725, 0.72])
    for rec, geom in states:
        if rec["STUSPS"] in ("AK", "HI", "PR"):
            continue
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        v = st_ok.get(rec["STUSPS"])
        for pg in polys:
            xy = [(x * k, y) for x, y in pg.exterior.coords]
            ax.add_patch(MplPolygon(xy, closed=True, facecolor=shade(v["median"]) if v else BG,
                                    edgecolor="#3a4745", lw=1.2))
    # Label placement (degrees lon, lat, alignment), chosen by eye to keep labels apart.
    place = {"New York": (-0.8, -1.9, "right"), "Boston": (-0.8, 1.5, "right"), "Detroit": (-0.6, 1.4, "right"),
             "Seattle": (1.0, 0.0, "left"), "Atlanta": (1.0, -1.2, "left"), "Phoenix": (1.0, -1.2, "left"),
             "Bay Area": (1.1, -0.2, "left")}
    halo = [matplotlib.patheffects.withStroke(linewidth=4, foreground=BG)]
    for name in cheap + dear + ([bay] if bay not in cheap + dear else []):
        v = mt[name]
        lab = "Bay Area" if name == bay else short_metro(name)
        ax.scatter([v["lon"] * k], [v["lat"]], s=200, color=INK, edgecolor=BG, linewidth=3, zorder=5)
        dx, dy, ha = place.get(lab, (1.0, 0.8, "left"))
        ax.text((v["lon"] + dx) * k, v["lat"] + dy, f"{lab} ${v['median']:.0f}", fontsize=27, color=INK, zorder=6,
                ha=ha, va="center", path_effects=halo)
    ax.set_xlim(-125 * k, -66.5 * k)
    ax.set_ylim(24, 50)
    ax.set_aspect("equal")
    ax.axis("off")
    lx = 0.775

    def lines(y, txts, size=24, color=MUTED, step=0.036):
        for t in txts:
            fig.text(lx, y, t, fontsize=size, color=color, va="top")
            y -= step
        return y

    y = lines(0.79, ["Cheapest to priciest metro"])
    fig.text(lx, y, f"${hi_m['median'] - lo_m['median']:.0f} an hour", fontsize=50, color=INK, fontweight="semibold", va="top")
    y = lines(y - 0.075, [f"${res['cheapest_to_priciest_at_40_hours']:,.0f} over the FAA's", "40-hour minimum"])
    y = lines(y - 0.035, ["Middle half of schools", "in a typical metro"])
    fig.text(lx, y, f"${res['metro_iqr_width_median']:.0f} apart", fontsize=50, color=INK, fontweight="semibold", va="top")
    y -= 0.11
    for i, (c, lab) in enumerate(zip(SEQ, labels)):
        yy = y - i * 0.031
        fig.patches.append(matplotlib.patches.Rectangle((lx, yy), 0.018, 0.022, transform=fig.transFigure,
                                                        color=c, figure=fig))
        fig.text(lx + 0.026, yy, lab, fontsize=19, color=INK)
    fig.text(lx, y - 5 * 0.031, f"no fill: under {mn} schools", fontsize=19, color=MUTED)
    B.footer(fig, f"Rates posted on {len(lower48)} school websites (median per school), March 2026 crawl. "
                  "Metros: Census combined areas. Map: US Census Bureau.")
    fig.savefig(out / "wetrate_map.png")
    plt.close(fig)
    spread(res, groups("metro"), ranked, bay, out)
    return res


def blocked(a, out):
    """Slide 1: the newly blocked school sites by who answered, split by whether the same
    address with a browser user agent got the page (62_blocked_sites.py report output)."""
    R = json.loads(a.blocked.read_text())
    g, gm = R["slide_group"], R["slide_group_mechanism"]
    total = R["blocked_sites"]
    if total < 200 or sum(g.values()) != total:
        sys.exit(f"FAIL blocked: groups {g} do not add up to {total}")
    rows = ["SiteGround", "Cloudflare", "WordPress.com", "Other hosts and firewalls", "Moved or for sale", "Unreachable now"]
    shown = {"Moved or for sale": "Domain sold or renamed", "Unreachable now": "Unreachable now"}
    both, ua = "#c98500", "#3987e5"  # validated pair (dark surface); never next to red
    fig = B.frame("Most schools didn't lock us out. Their platforms did.",
                  f"The {total} school sites that served our crawler in March and refused it in November, by who answered")
    ax = fig.add_axes([0.255, 0.25, 0.45, 0.56])
    for i, name in enumerate(rows):
        y = len(rows) - 1 - i
        n_all = g.get(name, 0)
        n_ua = gm.get(f"{name}: serves a browser user agent", 0)
        n_both = gm.get(f"{name}: refuses a browser user agent too", 0)
        if name in shown:
            ax.barh(y, n_all, color="#46524f", height=0.62)
        else:
            ax.barh(y, n_both, color=both, height=0.62)
            ax.barh(y, n_ua, left=n_both, color=ua, height=0.62)
        ax.text(n_all + 2, y, f"{n_all}", fontsize=30, color=INK, va="center")
        ax.text(-0.012, y, shown.get(name, name), transform=matplotlib.transforms.blended_transform_factory(ax.transAxes, ax.transData),
                fontsize=29, color=INK, ha="right", va="center")
    ax.set_xlim(0, 118)
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.axis("off")
    for j, (c, lab) in enumerate(((both, "refused even a browser's user agent"),
                                  (ua, "served a browser's user agent, refused ours"))):
        yy = 0.185 - j * 0.042
        fig.patches.append(matplotlib.patches.Rectangle((0.255, yy), 0.016, 0.026, transform=fig.transFigure,
                                                        color=c, figure=fig))
        fig.text(0.278, yy + 0.002, lab, fontsize=23, color=INK)
    ps = R["platform_share"]
    lx = 0.755

    def lines(y, txts, size=24, color=MUTED, step=0.037):
        for t in txts:
            fig.text(lx, y, t, fontsize=size, color=color, va="top")
            y -= step
        return y

    y = lines(0.80, ["Of each platform's school", "sites, the share refusing us"])
    for p in ("SiteGround", "WordPress.com", "Cloudflare"):
        n, m = ps[p]["refuse"], ps[p]["refuse"] + ps[p]["served"]
        y -= 0.012
        fig.text(lx, y, f"{100 * n / m:.0f}%", fontsize=50, color=INK, fontweight="semibold", va="top")
        fig.text(lx + 0.095, y - 0.012, p, fontsize=27, color=INK, va="top")
        fig.text(lx + 0.095, y - 0.052, f"{n} of {m}", fontsize=21, color=MUTED, va="top")
        y -= 0.105
    rb, rc = R["robots_blocked"], R["robots_control"]
    y = lines(y - 0.02, [f"Added AI-bot rules to robots.txt:", f"{rb['ai_full_block_added_of_both']} of {rb['robots_both_readable']} "
                         "(readable both times).", f"No robots.txt of {rb['robots_now_readable'] + rc['robots_now_readable']:,} names ours."],
              size=22)
    B.footer(fig, "November 2026 recrawl, then one request per site with a desktop Chrome user agent from the same address. "
                  "robots.txt then: Internet Archive.")
    fig.savefig(out / "blocked.png")
    plt.close(fig)
    return {"groups": g, "platform_share": R["platform_share"]}


def short_metro(name):
    return {"New York-Newark": "New York", "San Jose-San Francisco-Oakland": "Bay Area",
            "Washington-Baltimore-Arlington": "Washington", "Atlanta--Athens-Clarke County--Sandy Springs": "Atlanta",
            "Los Angeles-Long Beach": "Los Angeles", "Dallas-Fort Worth": "Dallas–Fort Worth",
            "Seattle-Tacoma": "Seattle", "Boston-Worcester-Providence": "Boston"}.get(
        name.split(",")[0], name.split(",")[0].split("-")[0])


def spread(res, by_metro, ranked, bay, out):
    """Alternative slide: every school's rate in each metro with 10+ schools, so the spread
    inside a metro can be compared with the spread between metros."""
    fig = B.frame("Pick the school, not the city",
                  "Cessna 172 rental with fuel, one dot per school, metros with at least "
                  f"{res['min_schools']} schools posting a rate")
    ax = fig.add_axes([0.20, 0.12, 0.74, 0.70])
    import random
    rnd = random.Random(7)
    for i, name in enumerate(ranked):
        pts = sorted(p["rate"] for p in by_metro[name])
        ys = [i + rnd.uniform(-0.18, 0.18) for _ in pts]
        hl = name == bay
        ax.scatter(pts, ys, s=90, color=AMBER if hl else "#3987e5", alpha=0.85, edgecolor=BG, linewidth=1.5, zorder=3)
        med = statistics.median(pts)
        ax.plot([med, med], [i - 0.36, i + 0.36], color=INK, lw=3.5, zorder=4)
        ax.text(0.19, i, f"{short_metro(name)}", transform=matplotlib.transforms.blended_transform_factory(fig.transFigure, ax.transData),
                fontsize=25, color=INK, ha="right", va="center")
        ax.text(262, i, f"${med:.0f}  ({len(pts)})", fontsize=23, color=MUTED, va="center")
    ax.set_xlim(70, 290)
    ax.set_ylim(-0.7, len(ranked) - 0.3)
    ax.set_yticks([])
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"${v:.0f}"))
    ax.set_xticks([100, 150, 200, 250])
    ax.tick_params(labelsize=22, length=0)
    ax.grid(axis="y", visible=False)
    ax.text(262, len(ranked) - 0.45, "median (n)", fontsize=20, color=MUTED, va="center")
    B.footer(fig, "Bar: metro median. Rates posted on school websites, March 2026 crawl; one median per school. "
                  "Metros: Census combined areas.")
    fig.savefig(out / "wetrate_spread.png")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for kk in ("fonts", "flight-line", "aircraft", "gama", "airports", "census", "out"):
        ap.add_argument(f"--{kk}", type=Path, required=True)
    ap.add_argument("--blocked", type=Path)
    ap.add_argument("--min-schools", type=int, default=10)
    ap.add_argument("--only", choices=("blocked", "yearbuilt_make", "wetrate_map"))
    a = ap.parse_args()
    for p in (a.fonts, a.flight_line, a.aircraft, a.gama, a.airports, a.census):
        if not p.exists():
            sys.exit(f"FAIL missing {p}")
    a.out.mkdir(parents=True, exist_ok=True)
    B.setup(a.fonts)
    fl = json.loads(a.flight_line.read_text())
    res_path = a.out / "slide_charts.json"
    res = json.loads(res_path.read_text()) if res_path.exists() else {}
    if a.only in (None, "yearbuilt_make"):
        res["yearbuilt_make"] = yearbuilt_make(a, fl, a.out)
    if a.only in (None, "wetrate_map"):
        res["wetrate_map"] = wetrate_map(a, fl, a.out)
    if a.only in (None, "blocked") and a.blocked:
        res["blocked"] = blocked(a, a.out)
    res_path.write_text(json.dumps(res, indent=2, default=str) + "\n")
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
