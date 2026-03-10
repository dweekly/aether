#!/usr/bin/env python3
"""
11_image_nn_extract.py — Extract N-numbers from image metadata in raw HTML.

Scans saved raw HTML files for <img> tags where the src, alt, title, caption,
or data-* attributes contain US tail numbers. This is a free extraction step
(no API calls) that complements the Sonnet text extraction and VLM vision.

Sources of N-numbers in images:
  - Filenames:  /uploads/N541LC.jpg, N808CE-Exterior-480x320.jpeg
  - Alt text:   alt="2024 Cessna T182T Skylane (N107WT)"
  - Captions:   <figcaption>Our Cessna 172 N12345</figcaption>
  - Title attr: title="N5785C - Cirrus SR22T"
  - Data attrs: data-caption="N-number: N926"

Validates against FAA registry and resolves aircraft types via ACFTREF.

Usage:
    python 11_image_nn_extract.py                # scan all raw HTML
    python 11_image_nn_extract.py --merge         # merge into flight_line.json
    python 11_image_nn_extract.py --stats         # just print stats
"""

import argparse
import csv
import io
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
HTTP_RAW_DIR = OUTPUT_DIR / "fleet_html_raw"
BROWSER_RAW_DIR = OUTPUT_DIR / "fleet_browser_html_raw"

NN_PATTERN = re.compile(r"\bN\d{1,5}[A-Z]{0,2}\b")
# WordPress thumbnail suffix: -480x320.jpeg
WP_THUMB_SUFFIX = re.compile(r"-\d+x\d+(\.\w+)$")


def load_faa() -> dict:
    """Load FAA registry + ACFTREF for type resolution."""
    faa_path = OUTPUT_DIR / "faa_master.csv"
    acftref_path = OUTPUT_DIR / "ACFTREF.txt"

    faa = {}
    if faa_path.exists():
        with open(faa_path, "r") as f:
            content = f.read()
        lines = content.splitlines()
        # Handle BOM
        if not lines[0].startswith("N-NUMBER"):
            idx = lines[0].find("N-NUMBER")
            if idx >= 0:
                lines[0] = lines[0][idx:]
        reader = csv.DictReader(io.StringIO("\n".join(lines)))
        for row in reader:
            nn = row.get("N-NUMBER", "").strip()
            if nn:
                faa[f"N{nn}"] = {
                    "owner": row.get("NAME", "").strip(),
                    "model_code": row.get("MFR MDL CODE", "").strip(),
                    "city": row.get("CITY", "").strip(),
                    "state": row.get("STATE", "").strip(),
                    "year": row.get("YEAR MFR", "").strip(),
                }

    acftref = {}
    if acftref_path.exists():
        with open(acftref_path) as f:
            c = f.read()
            if not c.startswith("CODE"):
                idx = c.find("CODE")
                if idx >= 0:
                    c = c[idx:]
        reader = csv.DictReader(io.StringIO(c))
        for row in reader:
            code = row.get("CODE", "").strip()
            if code:
                acftref[code] = {
                    "mfr": row.get("MFR", "").strip(),
                    "model": row.get("MODEL", "").strip(),
                }

    # Resolve model codes
    for rec in faa.values():
        code = rec["model_code"]
        if code in acftref:
            rec["mfr_name"] = acftref[code]["mfr"]
            rec["model_name"] = acftref[code]["model"]

    return faa


def extract_image_nns(html: str, page_url: str = "") -> list[dict]:
    """Extract N-numbers from image metadata in HTML."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen = set()

    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        alt = (img.get("alt") or "").strip()
        title = (img.get("title") or "").strip()
        caption = ""
        figure = img.find_parent("figure")
        if figure:
            fc = figure.find("figcaption")
            if fc:
                caption = fc.get_text(strip=True)

        # Collect data-* attributes
        data_text = " ".join(
            v for k, v in (img.attrs or {}).items()
            if k.startswith("data-") and isinstance(v, str)
        )

        all_text = f"{src} {alt} {title} {caption} {data_text}"
        for m in NN_PATTERN.finditer(all_text):
            nn = m.group()
            if nn in seen:
                continue
            seen.add(nn)

            # Determine source
            if nn in src:
                source = "filename"
            elif nn in alt:
                source = "alt_text"
            elif nn in caption:
                source = "caption"
            elif nn in title:
                source = "title"
            else:
                source = "data_attr"

            rec = {
                "n_number": nn,
                "source": source,
                "image_src": src[:200],
            }
            if alt:
                rec["alt"] = alt[:200]
            if caption:
                rec["caption"] = caption[:200]

            # Try to extract aircraft info from structured alt text
            # Pattern: "2024 Cessna T182T Skylane (N107WT)"
            alt_info = re.match(
                r"(\d{4})\s+(.+?)\s+\(" + re.escape(nn) + r"\)",
                alt,
            )
            if alt_info:
                rec["year_from_alt"] = alt_info.group(1)
                rec["type_from_alt"] = alt_info.group(2).strip()

            results.append(rec)

    return results


def scan_school_dir(school_dir: Path) -> list[dict]:
    """Scan all raw HTML files in a school directory for image N-numbers."""
    all_nns = []
    seen = set()
    for html_file in sorted(school_dir.glob("*.html")):
        try:
            html = html_file.read_text(encoding="utf-8", errors="replace")
            if not html.strip() or html[:20].startswith("%PDF"):
                continue
        except Exception:
            continue
        try:
            recs = extract_image_nns(html)
        except Exception:
            continue
        for rec in recs:
            nn = rec["n_number"]
            if nn not in seen:
                seen.add(nn)
                all_nns.append(rec)
    return all_nns


def main():
    parser = argparse.ArgumentParser(description="Extract N-numbers from image metadata")
    parser.add_argument("--merge", action="store_true", help="Merge findings into flight_line.json")
    parser.add_argument("--stats", action="store_true", help="Just print stats")
    args = parser.parse_args()

    faa = load_faa()
    print(f"FAA registry: {len(faa):,} aircraft\n")

    # Also scan crawl indexes for image metadata already extracted
    all_findings = {}  # school_domain -> list of nn records

    # 1. Scan raw HTML directories
    for raw_dir, label in [(HTTP_RAW_DIR, "HTTP"), (BROWSER_RAW_DIR, "Browser")]:
        if not raw_dir.exists():
            continue
        school_dirs = sorted(d for d in raw_dir.iterdir() if d.is_dir())
        print(f"{label} raw HTML: {len(school_dirs)} school directories")
        for school_dir in school_dirs:
            domain = school_dir.name
            nns = scan_school_dir(school_dir)
            if nns:
                all_findings.setdefault(domain, []).extend(nns)

    # 2. Scan crawl index image metadata (for schools crawled with new pipeline)
    for index_file in ["fleet_crawl_index.json", "fleet_browser_index.json"]:
        path = OUTPUT_DIR / index_file
        if not path.exists():
            continue
        idx = json.load(open(path))
        for school in idx:
            website = school.get("school_website", "") or ""
            domain = re.sub(r"https?://(www\.)?", "", website).rstrip("/")
            for page in school.get("pages", []):
                for img in page.get("images", []):
                    src = img.get("src", "")
                    alt = img.get("alt", "")
                    caption = img.get("caption", "")
                    title = img.get("title", "")
                    all_text = f"{src} {alt} {caption} {title}"
                    for m in NN_PATTERN.finditer(all_text):
                        nn = m.group()
                        if domain not in all_findings:
                            all_findings[domain] = []
                        existing = {r["n_number"] for r in all_findings[domain]}
                        if nn not in existing:
                            source = "filename" if nn in src else "alt_text" if nn in alt else "caption" if nn in caption else "title"
                            all_findings[domain].append({
                                "n_number": nn,
                                "source": source,
                                "image_src": src[:200],
                            })

    # Deduplicate within each school
    for domain in all_findings:
        seen = set()
        deduped = []
        for rec in all_findings[domain]:
            if rec["n_number"] not in seen:
                seen.add(rec["n_number"])
                deduped.append(rec)
        all_findings[domain] = deduped

    # Validate against FAA
    total_nns = 0
    confirmed = 0
    schools_with_nns = 0

    print(f"\n{'='*60}")
    print(f"Image Metadata N-Number Extraction Results")
    print(f"{'='*60}\n")

    for domain in sorted(all_findings.keys()):
        nns = all_findings[domain]
        if not nns:
            continue
        schools_with_nns += 1
        total_nns += len(nns)

        faa_count = sum(1 for r in nns if r["n_number"] in faa)
        confirmed += faa_count

        print(f"  {domain}: {len(nns)} N-numbers, {faa_count} FAA-confirmed")
        for rec in nns:
            nn = rec["n_number"]
            if nn in faa:
                r = faa[nn]
                type_str = f"{r.get('mfr_name', '?')} {r.get('model_name', '?')}"
                print(f"    ✓ {nn}: {r['year']} {type_str} [{rec['source']}]")
            else:
                print(f"    ✗ {nn}: NOT in FAA [{rec['source']}]")

    print(f"\n{'='*60}")
    print(f"  Schools with image N-numbers: {schools_with_nns}")
    print(f"  Total N-numbers extracted:    {total_nns}")
    print(f"  FAA confirmed:                {confirmed}")
    if total_nns:
        print(f"  Confirmation rate:            {confirmed/total_nns*100:.0f}%")
    print(f"{'='*60}")

    if args.merge and all_findings:
        # Merge confirmed N-numbers into flight_line.json
        flight_line_path = OUTPUT_DIR / "flight_line.json"
        if flight_line_path.exists():
            flight_line = json.load(open(flight_line_path))
        else:
            flight_line = []

        added = 0
        for school in flight_line:
            website = school.get("school_website", "") or ""
            domain = re.sub(r"https?://(www\.)?", "", website).rstrip("/")
            if domain not in all_findings:
                continue

            existing_nns = {
                a.get("n_number", "").upper()
                for a in school.get("fleet", [])
                if a.get("n_number")
            }

            for rec in all_findings[domain]:
                nn = rec["n_number"]
                if nn.upper() in existing_nns:
                    continue
                if nn not in faa:
                    continue  # Only merge FAA-confirmed

                r = faa[nn]
                type_str = f"{r.get('mfr_name', '?')} {r.get('model_name', '?')}".strip()
                aircraft = {
                    "n_number": nn,
                    "display_name": type_str,
                    "description": f"Extracted from image {rec['source']}",
                    "source_url": "",
                    "_extraction_method": "image_metadata",
                    "_faa_owner": r["owner"],
                    "_faa_year": r["year"],
                }
                school.setdefault("fleet", []).append(aircraft)
                existing_nns.add(nn.upper())
                added += 1

        with open(flight_line_path, "w") as f:
            json.dump(flight_line, f, indent=2)
        print(f"\n  Merged {added} new aircraft into flight_line.json")


if __name__ == "__main__":
    main()
