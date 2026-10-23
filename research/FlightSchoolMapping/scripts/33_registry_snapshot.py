#!/usr/bin/env python3
"""Download today's FAA Releasable Aircraft Registry and write a dated snapshot.

  data/registry/<date>/ReleasableAircraft.zip
  data/registry/<date>/master.parquet    one row per registered N-number
  data/registry/<date>/dereg.parquet     deregistered aircraft
  data/registry/<date>/acftref.parquet   make/model reference

Code labels come verbatim from the FAA's data dictionary
(registry.faa.gov/database/ardata.pdf, revised 2025-05-08).
registry.faa.gov sits behind a WAF that rejects python-requests, curl/* and
any user agent containing "@"; the UA below is the one 01_faa_registry.py
verified.

Usage: 33_registry_snapshot.py [--date YYYY-MM-DD] [--zip existing.zip]
"""

import argparse
import csv
import io
import shutil
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "data" / "registry"
URL = "https://registry.faa.gov/database/ReleasableAircraft.zip"
UA = "AetherResearch/1.0 (aviation research)"

TYPE_AIRCRAFT = {
    "1": "Glider", "2": "Balloon", "3": "Blimp/Dirigible", "4": "Fixed wing single engine",
    "5": "Fixed wing multi engine", "6": "Rotorcraft", "7": "Weight-shift-control",
    "8": "Powered Parachute", "9": "Gyroplane", "H": "Hybrid Lift", "O": "Other",
}
TYPE_ENGINE = {
    "0": "None", "1": "Reciprocating", "2": "Turbo-prop", "3": "Turbo-shaft", "4": "Turbo-jet",
    "5": "Turbo-fan", "6": "Ramjet", "7": "2 Cycle", "8": "4 Cycle", "9": "Unknown",
    "10": "Electric", "11": "Rotary",
}
TYPE_REGISTRANT = {
    "1": "Individual", "2": "Partnership", "3": "Corporation", "4": "Co-Owned",
    "5": "Government", "7": "LLC", "8": "Non Citizen Corporation", "9": "Non Citizen Co-Owned",
}
STATUS = {
    "A": "Triennial form mailed, not returned by Post Office",
    "D": "Expired Dealer",
    "E": "Certificate revoked by enforcement action",
    "M": "Valid - assigned to manufacturer under Dealer Certificate",
    "N": "Non-citizen corporation, flight hour reports not returned",
    "R": "Registration pending",
    "S": "Second triennial form mailed, not returned",
    "T": "Valid registration from a trainee",
    "V": "Valid registration",
    "W": "Certificate deemed ineffective or invalid",
    "X": "Enforcement letter",
    "Z": "Permanent reserved",
    "1": "Triennial form returned undeliverable",
    "2": "N-number assigned, not yet registered",
    "3": "N-number assigned as non-type-certificated, not yet registered",
    "4": "N-number assigned as import, not yet registered",
    "5": "Reserved N-number",
    "6": "Administratively canceled",
    "7": "Sale reported",
    "8": "Second triennial mailing attempt, no response",
    "9": "Certificate of registration revoked",
}
VALID_STATUS = {"V", "M", "T"}


def read_faa_csv(zf, name):
    """FAA files are comma-separated with padded fields and (sometimes) a BOM."""
    text = zf.read(name).decode("utf-8", errors="replace")
    first_header = text.split(",", 1)[0]
    start = text.find(first_header.lstrip("﻿ï»¿"))
    rows = csv.reader(io.StringIO(text[start:]))
    header = [h.strip() for h in next(rows)]
    for r in rows:
        if not r or len(r) < len(header) - 1:
            continue
        yield {h: (r[i].strip() if i < len(r) else "") for i, h in enumerate(header) if h}


def to_table(rows):
    rows = list(rows)
    cols = sorted({k for r in rows for k in r}) if rows else []
    return pa.table({c: [r.get(c) for r in rows] for c in cols})


def norm_cols(rec):
    return {k.lower().replace(" ", "_").replace("-", "_").replace("(", "").replace(")", ""): v
            for k, v in rec.items()}


def master_rows(zf):
    for r in read_faa_csv(zf, "MASTER.txt"):
        rec = norm_cols(r)
        rec["n_number"] = "N" + rec["n_number"]
        rec["mode_s_code_hex"] = rec.get("mode_s_code_hex", "").lower()
        rec["type_aircraft_label"] = TYPE_AIRCRAFT.get(rec.get("type_aircraft", ""))
        rec["type_engine_label"] = TYPE_ENGINE.get(rec.get("type_engine", "").lstrip("0") or "0")
        rec["type_registrant_label"] = TYPE_REGISTRANT.get(rec.get("type_registrant", ""))
        rec["status_label"] = STATUS.get(rec.get("status_code", ""))
        rec["status_valid"] = rec.get("status_code", "") in VALID_STATUS
        yield rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=str(date.today()))
    ap.add_argument("--zip")
    a = ap.parse_args()
    out = REGISTRY / a.date
    out.mkdir(parents=True, exist_ok=True)
    zpath = out / "ReleasableAircraft.zip"
    if a.zip:
        shutil.copy(a.zip, zpath)
    elif not zpath.exists():
        req = urllib.request.Request(URL, headers={"User-Agent": UA})
        tmp = zpath.with_suffix(".part")
        with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.replace(zpath)
    with zipfile.ZipFile(zpath) as zf:
        names = set(zf.namelist())
        pq.write_table(to_table(master_rows(zf)), out / "master.parquet", compression="zstd")
        if "DEREG.txt" in names:
            pq.write_table(to_table(norm_cols(r) for r in read_faa_csv(zf, "DEREG.txt")),
                           out / "dereg.parquet", compression="zstd")
        pq.write_table(to_table(norm_cols(r) for r in read_faa_csv(zf, "ACFTREF.txt")),
                       out / "acftref.parquet", compression="zstd")
    m = pq.read_metadata(out / "master.parquet")
    print(f"{a.date}: master rows={m.num_rows}")


if __name__ == "__main__":
    main()
