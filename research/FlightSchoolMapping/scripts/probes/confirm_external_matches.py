"""Second pass over match_external_school_lists.py output: domain and exact matches are ours; else a
10-digit phone match against flight_line's phones; else 59's confirmed_match. Writes a `verdict`
column (ours/new) back into the matched CSV and prints counts.

  python3 -I scripts/probes/confirm_external_matches.py output/flight_line.json data/research-2026-11/external/c_master_matched.csv
"""
import collections, csv, importlib.util, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("g", HERE / "59_google_places.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def dig(p):
    d = re.sub(r"\D", "", p or "")
    return d[-10:] if len(d) >= 10 else ""


fl = json.loads(Path(sys.argv[1]).read_text())
ph = {}
for s in fl:
    for p in (((s.get("public_contact") or {}).get("phone") or {}).get("value"), (s.get("contact") or {}).get("phone")):
        if dig(p):
            ph.setdefault(dig(p), s)
path = Path(sys.argv[2])
m = list(csv.DictReader(path.open()))
for r in m:
    if r["match_type"] in ("domain", "exact"):
        r["verdict"] = "ours"
    elif dig(r.get("phone")) in ph:
        r["verdict"], r["match_type"] = "ours", "phone"
    else:
        r["verdict"] = "ours" if g.confirmed_match(r) else "new"
with path.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(m[0]))
    w.writeheader()
    w.writerows(m)
print("match types", dict(collections.Counter(r["match_type"] for r in m)))
print("verdicts", dict(collections.Counter(r["verdict"] for r in m)))
