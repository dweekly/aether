#!/usr/bin/env python3
"""Ingest one day of the adsb.lol open-data archive and reduce it to tables.

Source: github.com/adsblol/globe_history_<year>, one release per day per
readsb instance, ODbL 1.0. A release is a tar split into ~2 GB parts
(.tar.aa, .tar.ab, ...) holding traces/<xx>/trace_full_<hex>.json (gzip).

For every aircraft that is US-registered (hex in the a00000-afffff block or a
registration starting "N") this writes, per day:

  data/history/aircraft_day/YYYY/MM/DD.parquet   one row per aircraft
  data/history/legs/YYYY/MM/DD.parquet           one row per observed leg
  data/history/manifest/YYYY-MM-DD.json          release, assets, sha256, counts

Definitions (kept deliberately literal; see docs/hackers-2026-plan.md):
  * observed airborne minutes: sum of gaps between consecutive airborne
    points, counting a gap only if it is <= MAX_GAP_S and the later point is
    not flagged stale. Coverage holes therefore never count as flying.
  * a leg starts at readsb's own new-leg flag (flags & 2) or after a gap
    > LEG_GAP_S. Its endpoint airport is only set when the leg was observed
    on the ground or below LOW_ALT_FT at that end; otherwise it is unknown.

Raw downloads are deleted after a successful write unless --keep-raw.

Usage:
  30_adsblol_history.py 2026-10-22 [--instance auto|prod-0|staging-0] [--keep-raw] [--workers 16]

--instance auto (the default) compares the prod-0 and staging-0 releases for the
day and takes the larger. adsb.lol's own instructions say to ignore a replica
that is significantly smaller (2026-03-24: prod 1,961 MiB vs staging 2,948 MiB).
"""

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from airport_index import AIRPORT_INDEX_VERSION, AirportIndex  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "data" / "history"
WORK = Path(os.environ.get("ADSBLOL_WORK", "/mnt/data/home/dew/scratch/adsblol"))

MAX_GAP_S = 120          # longest gap between airborne points still counted as flying
LEG_GAP_S = 30 * 60      # silence longer than this starts a new leg
LOW_ALT_FT = 1500        # endpoint below this (baro) counts as near an airport
ENDPOINT_MAX_NM = 3.0
PARSER_VERSION = "1"

AIRCRAFT_DAY_SCHEMA = pa.schema([
    ("day", pa.date32()),
    ("hex", pa.string()),
    ("reg", pa.string()),
    ("type", pa.string()),
    ("desc", pa.string()),
    ("db_flags", pa.int16()),
    ("pia", pa.bool_()),
    ("ladd", pa.bool_()),
    ("n_points", pa.int32()),
    ("n_ground", pa.int32()),
    ("n_airborne", pa.int32()),
    ("sources", pa.string()),            # JSON {source: count}, original readsb labels
    ("first_ts", pa.float64()),
    ("last_ts", pa.float64()),
    ("airborne_min", pa.float32()),
    ("max_alt_ft", pa.int32()),
    ("n_legs", pa.int16()),
    ("callsigns", pa.string()),          # JSON list in first-seen order
    ("last_ground_ts", pa.float64()),
    ("last_ground_lat", pa.float64()),
    ("last_ground_lon", pa.float64()),
    ("last_ground_airport", pa.string()),
])

LEG_SCHEMA = pa.schema([
    ("day", pa.date32()),
    ("hex", pa.string()),
    ("reg", pa.string()),
    ("leg", pa.int16()),
    ("start_ts", pa.float64()),
    ("end_ts", pa.float64()),
    ("airborne_min", pa.float32()),
    ("max_alt_ft", pa.int32()),
    ("n_points", pa.int32()),
    ("start_lat", pa.float64()),
    ("start_lon", pa.float64()),
    ("end_lat", pa.float64()),
    ("end_lon", pa.float64()),
    ("start_observed_low", pa.bool_()),
    ("end_observed_low", pa.bool_()),
    ("dep", pa.string()),
    ("dep_nm", pa.float32()),
    ("dep_ambiguous", pa.bool_()),
    ("arr", pa.string()),
    ("arr_nm", pa.float32()),
    ("arr_ambiguous", pa.bool_()),
    ("callsign", pa.string()),
])


# ── Release discovery and download ──────────────────────────────────────────

def gh_json(url):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "aether-research"})
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def find_release(day, instance):
    tag = f"v{day:%Y.%m.%d}-planes-readsb-{instance}"
    # A day is published in the repo for the year it was uploaded, so 2025-12-31 lives in
    # globe_history_2026.
    for year in (day.year, day.year + 1):
        try:
            rel = gh_json(f"https://api.github.com/repos/adsblol/globe_history_{year}/releases/tags/{tag}")
            break
        except urllib.error.HTTPError as e:
            if e.code != 404 or year != day.year:
                raise
    # Most days are split (.tar.aa, .tar.ab, ...); some, e.g. 2025-01-01, ship one unsplit .tar.
    parts = sorted((a for a in rel["assets"] if a["name"].endswith(".tar") or ".tar." in a["name"]),
                   key=lambda a: a["name"])
    if not parts:
        raise RuntimeError(f"{tag}: no .tar.* assets")
    return tag, parts


def choose_release(day, instance):
    """Return (instance, tag, parts, sizes). 'auto' picks the larger replica."""
    if instance != "auto":
        tag, parts = find_release(day, instance)
        return instance, tag, parts, {instance: sum(a["size"] for a in parts)}
    found = {}
    for inst in ("prod-0", "staging-0"):
        try:
            found[inst] = find_release(day, inst)
        except Exception:  # noqa: BLE001 - a missing replica is normal; both missing raises below
            pass
    if not found:
        raise RuntimeError(f"{day}: no prod-0 or staging-0 release")
    sizes = {inst: sum(a["size"] for a in parts) for inst, (_, parts) in found.items()}
    best = max(sizes, key=lambda i: (sizes[i], i == "prod-0"))
    return best, found[best][0], found[best][1], sizes


def download(parts, dest):
    dest.mkdir(parents=True, exist_ok=True)
    for a in parts:
        path = dest / a["name"]
        if path.exists() and path.stat().st_size == a["size"]:
            continue
        # curl resumes a partial file with -C -; --fail turns HTTP errors into exit codes.
        subprocess.run(["curl", "-sSL", "--fail", "--retry", "5", "-C", "-", "-o", str(path),
                        a["browser_download_url"]], check=True)
        if path.stat().st_size != a["size"]:
            raise RuntimeError(f"{a['name']}: size {path.stat().st_size} != {a['size']}")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def extract(parts, src, dest):
    dest.mkdir(parents=True, exist_ok=True)
    cat = subprocess.Popen(["cat", *[str(src / a["name"]) for a in parts]], stdout=subprocess.PIPE)
    tar = subprocess.run(["tar", "-x", "-C", str(dest), "--wildcards", "./traces/*"], stdin=cat.stdout)
    cat.stdout.close()
    if cat.wait() != 0 or tar.returncode != 0:
        raise RuntimeError("tar extraction failed")


# ── Reduction ───────────────────────────────────────────────────────────────

_AIRPORTS = None


def _airports():
    global _AIRPORTS
    if _AIRPORTS is None:
        _AIRPORTS = AirportIndex()
    return _AIRPORTS


def is_us(hexid, reg):
    return (hexid[:1] == "a" and len(hexid) == 6) or (reg or "").upper().startswith("N")


def reduce_trace(doc, day):
    """Return (aircraft_day_row, [leg_rows]) for one trace document."""
    hexid = doc["icao"].lower()
    reg = doc.get("r")
    base_ts = doc["timestamp"]
    db_flags = int(doc.get("dbFlags") or 0)
    pts = doc.get("trace") or []

    sources = Counter()
    callsigns = []
    n_ground = n_air = 0
    airborne_s = 0.0
    max_alt = None
    last_ground = None
    legs = []
    cur = None
    prev_t = None
    prev_air = False

    for p in pts:
        t = base_ts + p[0]
        lat, lon, alt, flags = p[1], p[2], p[3], p[6] or 0
        src = p[9] if len(p) > 9 and p[9] else "unknown"
        sources[src] += 1
        details = p[8] if len(p) > 8 else None
        if isinstance(details, dict):
            cs = (details.get("flight") or "").strip()
            if cs and cs not in callsigns:
                callsigns.append(cs)
        on_ground = alt == "ground"
        airborne = isinstance(alt, (int, float)) and not on_ground
        if on_ground:
            n_ground += 1
            last_ground = (t, lat, lon)
        elif airborne:
            n_air += 1
            max_alt = alt if max_alt is None else max(max_alt, alt)

        new_leg = cur is None or (flags & 2) or (prev_t is not None and t - prev_t > LEG_GAP_S)
        if new_leg:
            if cur:
                legs.append(cur)
            cur = {"start": (t, lat, lon, alt), "end": (t, lat, lon, alt), "air_s": 0.0,
                   "max_alt": None, "n": 0, "callsign": None}
        gap = t - prev_t if prev_t is not None else None
        if airborne and prev_air and gap is not None and gap <= MAX_GAP_S and not (flags & 1) and not new_leg:
            airborne_s += gap
            cur["air_s"] += gap
        if airborne:
            cur["max_alt"] = alt if cur["max_alt"] is None else max(cur["max_alt"], alt)
        if cur["callsign"] is None and isinstance(details, dict):
            cs = (details.get("flight") or "").strip()
            cur["callsign"] = cs or None
        cur["end"] = (t, lat, lon, alt)
        cur["n"] += 1
        prev_t, prev_air = t, airborne

    if cur:
        legs.append(cur)

    ap = _airports()

    def endpoint(pt):
        _, lat, lon, alt = pt
        low = alt == "ground" or (isinstance(alt, (int, float)) and alt < LOW_ALT_FT)
        m = ap.nearest(lat, lon, max_nm=ENDPOINT_MAX_NM) if low else None
        return low, m

    leg_rows = []
    for i, lg in enumerate(legs):
        s_low, s_m = endpoint(lg["start"])
        e_low, e_m = endpoint(lg["end"])
        leg_rows.append({
            "day": day, "hex": hexid, "reg": reg, "leg": i,
            "start_ts": lg["start"][0], "end_ts": lg["end"][0],
            "airborne_min": lg["air_s"] / 60.0,
            "max_alt_ft": int(lg["max_alt"]) if lg["max_alt"] is not None else None,
            "n_points": lg["n"],
            "start_lat": lg["start"][1], "start_lon": lg["start"][2],
            "end_lat": lg["end"][1], "end_lon": lg["end"][2],
            "start_observed_low": s_low, "end_observed_low": e_low,
            "dep": s_m.ident if s_m else None, "dep_nm": s_m.distance_nm if s_m else None,
            "dep_ambiguous": s_m.ambiguous if s_m else None,
            "arr": e_m.ident if e_m else None, "arr_nm": e_m.distance_nm if e_m else None,
            "arr_ambiguous": e_m.ambiguous if e_m else None,
            "callsign": lg["callsign"],
        })

    lg_ap = ap.nearest(last_ground[1], last_ground[2], max_nm=ENDPOINT_MAX_NM) if last_ground else None
    row = {
        "day": day, "hex": hexid, "reg": reg, "type": doc.get("t"), "desc": doc.get("desc"),
        "db_flags": db_flags, "pia": bool(db_flags & 4), "ladd": bool(db_flags & 8),
        "n_points": len(pts), "n_ground": n_ground, "n_airborne": n_air,
        "sources": json.dumps(dict(sources), sort_keys=True),
        "first_ts": base_ts + pts[0][0] if pts else None,
        "last_ts": base_ts + pts[-1][0] if pts else None,
        "airborne_min": airborne_s / 60.0,
        "max_alt_ft": int(max_alt) if max_alt is not None else None,
        "n_legs": len(legs),
        "callsigns": json.dumps(callsigns),
        "last_ground_ts": last_ground[0] if last_ground else None,
        "last_ground_lat": last_ground[1] if last_ground else None,
        "last_ground_lon": last_ground[2] if last_ground else None,
        "last_ground_airport": lg_ap.ident if lg_ap else None,
    }
    return row, leg_rows


def reduce_files(args):
    paths, day = args
    rows, legs, skipped = [], [], 0
    for p in paths:
        try:
            with gzip.open(p, "rt") as f:
                doc = json.load(f)
        except (OSError, json.JSONDecodeError):
            skipped += 1
            continue
        if not is_us(doc.get("icao", "").lower(), doc.get("r")):
            continue
        r, lg = reduce_trace(doc, day)
        rows.append(r)
        legs.extend(lg)
    return rows, legs, skipped


def write_parquet(rows, schema, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=schema).sort_by([("hex", "ascending")])
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(fd)
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("day")
    ap.add_argument("--instance", default="auto")
    ap.add_argument("--keep-raw", action="store_true")
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()
    day = date.fromisoformat(a.day)
    t0 = time.time()

    instance, tag, parts, replica_sizes = choose_release(day, a.instance)
    raw = WORK / f"{day}-{instance}" / "raw"
    ext = WORK / f"{day}-{instance}" / "x"
    download(parts, raw)
    digests = {p["name"]: sha256(raw / p["name"]) for p in parts}
    if ext.exists():
        shutil.rmtree(ext)
    extract(parts, raw, ext)
    t_dl = time.time()

    files = sorted(str(p) for p in (ext / "traces").glob("*/trace_full_*.json"))
    chunks = [files[i::a.workers * 4] for i in range(a.workers * 4)]
    rows, legs, skipped = [], [], 0
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        for r, lg, sk in pool.map(reduce_files, [(c, day) for c in chunks]):
            rows.extend(r)
            legs.extend(lg)
            skipped += sk

    y, m, d = f"{day:%Y}", f"{day:%m}", f"{day:%d}"
    write_parquet(rows, AIRCRAFT_DAY_SCHEMA, HISTORY / "aircraft_day" / y / m / f"{d}.parquet")
    write_parquet(legs, LEG_SCHEMA, HISTORY / "legs" / y / m / f"{d}.parquet")

    manifest = {
        "day": str(day), "release": tag, "instance": instance, "replica_sizes": replica_sizes,
        "parser_version": PARSER_VERSION, "airport_index_version": AIRPORT_INDEX_VERSION,
        "assets": [{"name": p["name"], "size": p["size"], "sha256": digests[p["name"]]} for p in parts],
        "trace_files": len(files), "unreadable_files": skipped,
        "us_aircraft": len(rows), "legs": len(legs),
        "pia_aircraft": sum(r["pia"] for r in rows), "ladd_aircraft": sum(r["ladd"] for r in rows),
        "download_extract_s": round(t_dl - t0, 1), "reduce_s": round(time.time() - t_dl, 1),
        "written_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    mpath = HISTORY / "manifest" / f"{day}.json"
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))

    if not a.keep_raw:
        shutil.rmtree(WORK / f"{day}-{instance}")


if __name__ == "__main__":
    main()
