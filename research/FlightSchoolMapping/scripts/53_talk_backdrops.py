#!/usr/bin/env python3
"""Full-slide chart backdrops (1920x1080 PNG) for the Hackers 2026 deck.

Four charts, each drawn from a source file, in the deck's look (dark slate, amber marks, buff
secondary, Archivo Narrow and JetBrains Mono):

  lampson.png   Lampson Field (1O2) GA operations in the FAA Terminal Area Forecast, 1990-2055
  drones.png    "Drones Registered" on the FAA's Drones by the Numbers page, by archive capture
  yearbuilt.png Year built (FAA registry) of the tails on US flight-school websites
  schools.png   One dot per school, club or university program, at its first listed airport

Usage:
  53_talk_backdrops.py --fonts DIR --taf lampson_rows.csv --drones drones_by_the_numbers_wayback.csv \\
      --flight-line output/flight_line.json --aircraft data/release/v0.3-20261023/aircraft.parquet \\
      --airports data/ourairports_airports.csv --out docs/hackers-2026-charts/backdrops
Exits 1 if an input is missing or a chart's data looks wrong (empty, or the Lampson series
does not show the flat run the slide claims).
"""

import argparse
import csv
import json
import statistics
import sys
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

BG, INK, MUTED, GRID, AMBER, BUFF = "#0f1517", "#dde4e1", "#8a9895", "#24302f", "#d9ad4a", "#e6ddc4"


def setup(fonts):
    for f in fonts.glob("*.ttf"):
        font_manager.fontManager.addfont(str(f))
    plt.rcParams.update({
        "font.family": "Archivo Narrow", "font.size": 26, "text.color": INK,
        "axes.facecolor": BG, "figure.facecolor": BG, "axes.edgecolor": GRID, "axes.labelcolor": MUTED,
        "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 1,
        "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
    })


def frame(title, subtitle):
    fig = plt.figure(figsize=(19.2, 10.8), dpi=100)
    fig.text(0.067, 0.90, title, fontsize=60, fontweight="semibold", color=INK, va="bottom")
    if subtitle:
        fig.text(0.067, 0.855, subtitle, fontsize=30, color=MUTED, va="bottom")
    return fig


def footer(fig, text):
    fig.text(0.067, 0.045, text, fontsize=22, color=MUTED)


def lampson(a, out):
    rows = [r for r in csv.reader(a.taf.open()) if r and r[0].strip() == "1O2"]
    by_year = {}
    for r in rows:
        by_year[int(r[2])] = (int(r[1]), int(r[5]), int(r[7]))  # scenario, itinerant GA, local GA
    years = sorted(by_year)
    flat = [y for y in years if y <= 2024 and by_year[y][1] == 37000 and by_year[y][2] == 37000]
    if not years or len(flat) < 25:
        sys.exit(f"FAIL lampson: {len(flat)} flat historical years")
    fig = frame("Lampson Field, Lakeport CA", "General aviation operations a year, FAA Terminal Area Forecast")
    ax = fig.add_axes([0.08, 0.14, 0.86, 0.66])
    itn = [by_year[y][1] for y in years]
    loc = [by_year[y][2] for y in years]
    first_fc = min(y for y in years if by_year[y][0] == 1)
    ax.axvspan(first_fc - 0.5, years[-1] + 0.5, color="#16201f", zorder=0)
    ax.text(first_fc + 1, 47000, "forecast", fontsize=28, color=MUTED)
    ax.plot(years, loc, color=BUFF, lw=3, ls=(0, (6, 4)), zorder=3)
    ax.plot(years, itn, color=AMBER, lw=4, zorder=4)
    ax.text(1990.3, 35800, "itinerant", fontsize=26, color=INK)
    ax.text(1990.3, 19800, "local", fontsize=26, color=INK)
    ax.annotate(f"37,000 and 37,000, every year {flat[0]}–{flat[-1]}", xy=(2010, 37000), xytext=(2000, 26000),
                fontsize=32, color=INK, arrowprops=dict(arrowstyle="-", color=MUTED, lw=1.5))
    ax.set_ylim(0, 50000)
    ax.set_yticks([10000, 20000, 30000, 40000, 50000])
    ax.set_xlim(years[0] - 0.5, years[-1] + 0.5)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.tick_params(labelsize=24, length=0)
    ax.grid(axis="x", visible=False)
    footer(fig, f"FAA Terminal Area Forecast 2025 (APO100). Historical through 2024, forecast {first_fc}–{years[-1]}. Non-towered field: the counts are estimates.")
    fig.savefig(out / "lampson.png")
    plt.close(fig)
    return {"flat_years": [flat[0], flat[-1], len(flat)], "forecast_to": years[-1]}


def drones(a, out):
    pts = []
    for r in csv.DictReader(a.drones.open()):
        if r["label"].strip() == "Drones Registered" and r["value"]:
            s = r["snapshot"]
            pts.append((date(int(s[:4]), int(s[4:6]), int(s[6:8])), int(r["value"])))
    pts.sort()
    if len(pts) < 10:
        sys.exit(f"FAIL drones: {len(pts)} captures")
    drop = max(range(1, len(pts)), key=lambda i: pts[i - 1][1] - pts[i][1])
    (d0, v0), (d1, v1) = pts[drop - 1], pts[drop]
    fig = frame("Drones registered with the FAA", "As published on the FAA’s own “Drones by the Numbers” page, by archive capture")
    ax = fig.add_axes([0.10, 0.14, 0.84, 0.66])
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    ax.plot(xs, ys, color=AMBER, lw=3, zorder=3)
    ax.scatter(xs, ys, s=90, color=AMBER, edgecolor=BG, linewidth=2, zorder=4)
    ax.annotate(f"{v0:,} → {v1:,}\nbetween {d0:%b %Y} and {d1:%b %Y}", xy=(d1, v1), xytext=(d1.replace(year=d1.year + 1), 1_500_000),
                fontsize=32, color=INK, arrowprops=dict(arrowstyle="-", color=MUTED, lw=1.5))
    ax.set_ylim(0, max(ys) * 1.15)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v / 1e6:.1f}M"))
    ax.tick_params(labelsize=24, length=0)
    ax.grid(axis="x", visible=False)
    footer(fig, "Internet Archive captures of faa.gov “Drones by the Numbers”. The live page on 2026-10-27 still showed November 2025 figures.")
    fig.savefig(out / "drones.png")
    plt.close(fig)
    return {"captures": len(pts), "drop": [str(d0), v0, str(d1), v1]}


def yearbuilt(a, out):
    fl = json.loads(a.flight_line.read_text())
    tails = {f["n_number"].strip().upper() for s in fl for f in (s.get("fleet") or []) if (f.get("n_number") or "").strip()}
    import duckdb  # only this chart needs it; the styling helpers are imported by 59
    c = duckdb.connect()
    yrs = [r[0] for r in c.execute(f"""SELECT year_mfr FROM read_parquet('{a.aircraft}')
        WHERE n_number IN (SELECT unnest(?)) AND year_mfr BETWEEN 1930 AND 2026""", [sorted(tails)]).fetchall()]
    if len(yrs) < 3000:
        sys.exit(f"FAIL yearbuilt: only {len(yrs)} registered tails with a year")
    med = statistics.median(yrs)
    fig = frame("When the training fleet was built", f"Year built, {len(yrs):,} registered aircraft on the school fleets we found")
    ax = fig.add_axes([0.08, 0.14, 0.86, 0.66])
    ax.hist(yrs, bins=range(1940, 2028), color=AMBER, edgecolor=BG, linewidth=2)
    ax.axvline(med, color=INK, lw=2, ls=(0, (4, 4)))
    ax.text(med + 0.8, ax.get_ylim()[1] * 0.92, f"median {med:.0f}: {2026 - med:.0f} years old", fontsize=32, color=INK)
    ax.tick_params(labelsize=24, length=0)
    ax.grid(axis="x", visible=False)
    footer(fig, "Fleets from the March 2026 crawl (school websites, plus registry address matches); year built from the FAA registry, 2026-10-23.")
    fig.savefig(out / "yearbuilt.png")
    plt.close(fig)
    return {"tails": len(yrs), "median_year": med}


def schools(a, out):
    pos = {}
    for r in csv.DictReader(a.airports.open(encoding="utf-8")):
        if r["iso_country"] != "US":
            continue
        ll = (float(r["latitude_deg"]), float(r["longitude_deg"]))
        for code in (r["ident"], r["gps_code"], r["local_code"], "K" + r["local_code"] if len(r["local_code"]) == 3 else ""):
            if code:
                pos.setdefault(code.upper(), ll)
    fl = json.loads(a.flight_line.read_text())
    pts, missing = [], 0
    for s in fl:
        ids = [l.get("airport_id") for l in (s.get("locations") or []) if l.get("airport_id")]
        ll = next((pos[i.upper()] for i in ids if i.upper() in pos), None)
        if ll:
            pts.append(ll)
        else:
            missing += 1
    conus = [(la, lo) for la, lo in pts if 24 <= la <= 50 and -125 <= lo <= -66]
    if len(conus) < 3000:
        sys.exit(f"FAIL schools: only {len(conus)} placed")
    fig = frame("Flight schools, clubs and university programs", f"{len(pts):,} of the {len(fl):,} we found have a known airport")
    ax = fig.add_axes([0.04, 0.10, 0.92, 0.72])
    import math
    k = math.cos(math.radians(38))
    ax.scatter([lo * k for la, lo in conus], [la for la, lo in conus], s=26, color=AMBER, alpha=0.55, linewidth=0)
    ax.set_aspect("equal")
    ax.axis("off")
    footer(fig, f"One dot per school at its first listed airport: {len(conus):,} in the lower 48, {len(pts) - len(conus)} elsewhere. {missing:,} have no airport on file yet.")
    fig.savefig(out / "schools.png")
    plt.close(fig)
    return {"schools": len(fl), "placed": len(pts), "conus": len(conus), "missing": missing}


def main():
    ap = argparse.ArgumentParser()
    for k in ("fonts", "taf", "drones", "flight-line", "aircraft", "airports", "out"):
        ap.add_argument(f"--{k}", type=Path, required=True)
    a = ap.parse_args()
    for p in (a.fonts, a.taf, a.drones, a.flight_line, a.aircraft, a.airports):
        if not p.exists():
            sys.exit(f"FAIL missing {p}")
    a.out.mkdir(parents=True, exist_ok=True)
    setup(a.fonts)
    res = {"lampson": lampson(a, a.out), "drones": drones(a, a.out), "yearbuilt": yearbuilt(a, a.out), "schools": schools(a, a.out)}
    (a.out / "backdrops.json").write_text(json.dumps(res, indent=2, default=str) + "\n")
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
