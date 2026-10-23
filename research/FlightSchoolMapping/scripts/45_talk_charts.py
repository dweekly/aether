#!/usr/bin/env python3
"""Slide charts for the Hackers 2026 panel, drawn from one frozen release.

Writes 16:9 SVGs (1600 x 900) in a dark and a light variant to
docs/hackers-2026-charts/, named <chart>-<theme>.svg. Same release, same
numbers as docs/hackers-2026-facts.md (43_talk_facts.py); rerun both together.
PNG copies for slide tools: rsvg-convert -w 1600 X.svg -o X.png

Colors: "heard" is the accent and everything else a neutral context color,
so the split reads by lightness and survives every color-vision deficiency
(checked with the dataviz validator); the neutral sits under 3:1 contrast,
so every neutral mark carries a direct label.

Usage: 45_talk_charts.py --release v0.2-20261023 [--out ../../../docs/hackers-2026-charts]
"""

import argparse
import json
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASES = ROOT / "data" / "release"
DEFAULT_OUT = ROOT.parent.parent / "docs" / "hackers-2026-charts"
W, H = 1600, 900
FONT = "'Azeret Mono', Menlo, Consolas, monospace"
THEMES = {
    "dark": {"bg": "#08090c", "ink": "#f0f1f4", "dim": "#8d909a", "rule": "#262933",
             "accent": "#c9a64a", "context": "#4a5263", "s1": "#b08a32", "s2": "#3987e5"},
    "light": {"bg": "#ffffff", "ink": "#1a1e27", "dim": "#5d6270", "rule": "#e3e5ea",
              "accent": "#9a7420", "context": "#b9bfcb", "s1": "#9a7420", "s2": "#2a78d6"},
}


def n(x):
    return f"{x:,}"


class Svg:
    def __init__(self, t):
        self.t, self.parts = t, []

    def rect(self, x, y, w, h, fill, title=None, rx=4):
        tip = f"<title>{escape(title)}</title>" if title else ""
        self.parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0):.1f}" height="{max(h, 0):.1f}" '
                          f'rx="{rx}" fill="{self.t[fill]}">{tip}</rect>')

    def text(self, x, y, s, size=26, fill="ink", anchor="start", weight=400):
        self.parts.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" '
                          f'fill="{self.t[fill]}" text-anchor="{anchor}">{escape(s)}</text>')

    def line(self, x1, y1, x2, y2, stroke="rule", width=2):
        self.parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                          f'stroke="{self.t[stroke]}" stroke-width="{width}"/>')

    def dot(self, x, y, r, fill, opacity, title=None):
        tip = f"<title>{escape(title)}</title>" if title else ""
        self.parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{self.t[fill]}" '
                          f'fill-opacity="{opacity}">{tip}</circle>')

    def render(self):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
                f'font-family="{FONT}"><rect width="{W}" height="{H}" fill="{self.t["bg"]}"/>'
                + "".join(self.parts) + "</svg>\n")


def footer(s, release, window):
    s.text(80, H - 40, f"aether-flight.com/explore · release {release} · adsb.lol archive {window} · "
                       "FAA registry", 20, "dim")


def registry_vs_sky(s, r, release, window):
    tot = {x["observed_class"]: x["n"] for x in r["totals"]}
    reg = sum(tot.values())
    heard = tot.get("adsb", 0) + tot.get("other_only", 0)
    x0, x1, y, h = 80, W - 80, 380, 150
    wh = (x1 - x0) * heard / reg
    s.rect(x0, y, wh - 1, h, "accent", f"Heard: {n(heard)}")
    s.rect(x0 + wh + 1, y, x1 - x0 - wh - 1, h, "context", f"Silent: {n(reg - heard)}")
    s.text(x0, y - 70, f"{n(heard)} heard", 54, weight=600)
    s.text(x0, y - 24, f"{100 * heard / reg:.0f}% of the registry", 28, "dim")
    s.text(x1, y - 70, f"{n(reg - heard)} silent", 54, anchor="end", weight=600)
    s.text(x1, y - 24, f"{100 * (reg - heard) / reg:.0f}%", 28, "dim", anchor="end")
    s.text(x0, y + h + 60, f"{n(reg)} aircraft on the FAA registry, {window}", 28, "dim")
    footer(s, release, window)


def faa_equipped(s, r, release, window, faa):
    tot = {x["observed_class"]: x["n"] for x in r["totals"]}
    rows = [("On the FAA registry", sum(tot.values()), "context"),
            (f"ADS-B equipped (FAA count, {faa['as_of']})", faa["all"], "context"),
            ("Heard broadcasting ADS-B", tot.get("adsb", 0), "accent")]
    x0, x1, y, h, gap = 80, W - 300, 150, 120, 110
    top = max(v for _, v, _ in rows)
    for i, (label, v, c) in enumerate(rows):
        yy = y + i * (h + gap)
        s.text(x0, yy - 18, label, 30)
        s.rect(x0, yy, (x1 - x0) * v / top, h, c, f"{label}: {n(v)}")
        s.text(x0 + (x1 - x0) * v / top + 20, yy + h / 2 + 14, n(v), 40, weight=600)
    s.text(x0, H - 110, f"Heard = {100 * tot.get('adsb', 0) / faa['all']:.0f}% of the FAA's equipped count, "
                        f"{window}", 28, "dim")
    footer(s, release, window)


def heard_by_decade(s, r, release, window):
    rows = [x for x in r["single_piston_by_decade"] if x["decade"] != "unknown" and int(x["decade"]) >= 1930]
    x0, x1, base, top = 100, W - 80, 700, 170
    s.line(x0, base, x1, base)
    slot = (x1 - x0) / len(rows)
    bw = slot * 0.62
    for i, x in enumerate(rows):
        share = x["observed"] / x["registered"]
        cx = x0 + slot * (i + 0.5)
        hgt = (base - top) * share
        s.rect(cx - bw / 2, base - hgt, bw, hgt, "accent", f"{x['decade']}s: {n(x['observed'])} of {n(x['registered'])}")
        s.text(cx, base - hgt - 16, f"{100 * share:.0f}%", 32, anchor="middle", weight=600)
        s.text(cx, base + 44, f"{x['decade']}s", 28, anchor="middle")
        s.text(cx, base + 80, n(x["registered"]), 20, "dim", anchor="middle")
    s.text(x0, 90, "Share heard, single-engine pistons by decade built", 30, "dim")
    s.text(x0, base + 120, "registered, under each decade", 20, "dim")
    unknown = next((x for x in r["single_piston_by_decade"] if x["decade"] == "unknown"), None)
    if unknown:
        s.text(x1, base + 120, f"{n(unknown['registered'])} more have no year on file", 20, "dim", anchor="end")
    footer(s, release, window)


def heard_by_decade_cert(s, cert, release, window):
    decades = list(cert)
    series = [("standard", "Standard category", "s1"), ("experimental", "Experimental", "s2")]
    x0, x1, base, top = 100, W - 80, 700, 190
    s.line(x0, base, x1, base)
    slot = (x1 - x0) / len(decades)
    bw = slot * 0.34
    for i, d in enumerate(decades):
        cx = x0 + slot * (i + 0.5)
        for j, (key, label, col) in enumerate(series):
            v = cert[d].get(key)
            if not v:
                continue
            share = v["observed"] / v["registered"]
            bx = cx - bw - 1 + j * (bw + 2)
            hgt = (base - top) * share
            s.rect(bx, base - hgt, bw, hgt, col, f"{d}s {label}: {n(v['observed'])} of {n(v['registered'])}")
            s.text(bx + bw / 2, base - hgt - 12, f"{100 * share:.0f}", 22, anchor="middle", weight=600)
        s.text(cx, base + 44, f"{d}s", 28, anchor="middle")
    s.text(x0, 70, "Share heard (%), single-engine pistons by decade built and airworthiness class", 30, "dim")
    for j, (key, label, col) in enumerate(series):
        lx = x0 + j * 600
        s.rect(lx, 108, 28, 28, col, rx=4)
        total = sum(cert[d].get(key, {}).get("registered", 0) for d in decades)
        s.text(lx + 42, 132, f"{label} ({n(total)})", 26)
    footer(s, release, window)


def heard_by_type(s, r, release, window):
    rows = sorted((x for x in r["by_aircraft_type"] if x["registered"] >= 400),
                  key=lambda x: -x["observed"] / x["registered"])
    x0, lab, x1, y, h, gap = 80, 500, W - 440, 120, 44, 24
    for i, x in enumerate(rows):
        share = x["observed"] / x["registered"]
        yy = y + i * (h + gap)
        s.text(lab - 24, yy + h / 2 + 10, x["aircraft_type"], 28, anchor="end")
        s.rect(lab, yy, (x1 - lab), h, "context", rx=4)
        s.rect(lab, yy, max((x1 - lab) * share, 4), h, "accent", f"{x['aircraft_type']}: {n(x['observed'])} of {n(x['registered'])}")
        s.text(x1 + 20, yy + h / 2 + 10, f"{100 * share:.0f}%", 26, weight=600)
        s.text(x1 + 110, yy + h / 2 + 10, f"{n(x['observed'])} of {n(x['registered'])}", 22, "dim")
    s.text(x0, 70, "Share of registered aircraft heard, by type", 30, "dim")
    footer(s, release, window)


def c172_rate_vs_hours(s, schools, days, release, window):
    seen, pts = set(), []
    for sc in schools:
        for f in sc["fleet"]:
            m = (f.get("model") or "").upper()
            if f.get("wet") and f.get("on_registry") and "CESSNA" in m and "172" in m and f["n"] not in seen:
                seen.add(f["n"])
                pts.append((f["wet"], (f.get("hrs") or 0) * 365 / days, f["n"]))
    xmin, xmax, ymax = 100, 300, 1200
    x0, x1, y0, y1 = 200, W - 100, 690, 130
    sx = lambda v: x0 + (x1 - x0) * (v - xmin) / (xmax - xmin)
    sy = lambda v: y0 - (y0 - y1) * v / ymax
    for v in range(0, ymax + 1, 300):
        s.line(x0, sy(v), x1, sy(v))
        s.text(x0 - 20, sy(v) + 9, n(v), 24, "dim", anchor="end")
    for v in range(xmin, xmax + 1, 50):
        s.text(sx(v), y0 + 44, f"${v}", 24, "dim", anchor="middle")
    inside = [p for p in pts if xmin <= p[0] <= xmax and p[1] <= ymax]
    for wet, hrs, tail in inside:
        s.dot(sx(wet), sy(hrs), 6, "accent", 0.5, f"{tail}: ${wet:g}/h, {hrs:.0f} h/yr")
    s.text(x0, 70, "Cessna 172 rentals: advertised wet rate vs observed hours flown a year", 30, "dim")
    s.text(x1, y0 + 84, "wet rate, $ per hour", 24, "dim", anchor="end")
    s.text(x0 - 20, y1 - 30, "hours/yr", 24, "dim", anchor="end")
    s.text(x0, y0 + 84, f"{n(len(pts))} aircraft; {n(len(pts) - len(inside))} outside the axes not drawn", 22, "dim")
    footer(s, release, window)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", required=True)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args()
    rel = RELEASES / a.release
    r = json.loads((rel / "results.json").read_text())
    schools = json.loads((rel / "schools_full.json").read_text())["schools"]
    w = r["window"]
    window = f"{w['first_day']} → {w['last_day']}"
    from importlib import import_module
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    facts = import_module("43_talk_facts")
    faa = facts.FAA_EQUIPPED
    cert = facts.piston_by_decade_cert(rel, r["registry_date"])
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    charts = {
        "registry-vs-sky": lambda s: registry_vs_sky(s, r, a.release, window),
        "faa-equipped": lambda s: faa_equipped(s, r, a.release, window, faa),
        "heard-by-decade": lambda s: heard_by_decade(s, r, a.release, window),
        "heard-by-decade-cert": lambda s: heard_by_decade_cert(s, cert, a.release, window),
        "heard-by-type": lambda s: heard_by_type(s, r, a.release, window),
        "c172-rate-vs-hours": lambda s: c172_rate_vs_hours(s, schools, w["days"], a.release, window),
    }
    for name, draw in charts.items():
        for theme, t in THEMES.items():
            s = Svg(t)
            draw(s)
            (out / f"{name}-{theme}.svg").write_text(s.render())
    print(f"wrote {len(charts) * len(THEMES)} charts to {out}")


if __name__ == "__main__":
    main()
