#!/usr/bin/env python3
"""
12_vlm_nn_extract.py — Two-pass VLM + FAA correction for image N-numbers.

Pass 1: Send aircraft images to a vision model (Sonnet/Haiku) asking it to
        read tail numbers visible on the aircraft.
Pass 2: For N-numbers that don't match FAA registry, fuzzy-search nearby
        valid N-numbers with their aircraft types, and ask the VLM (with
        original image still in context) to pick the correct one.

This handles the case where N-numbers are only visible in photos, not in
any text metadata (filenames, alt text, captions).

Usage:
    python 12_vlm_nn_extract.py --school hawaiiflightschool.com
    python 12_vlm_nn_extract.py --school lonemountainaircraft.com --model claude-haiku-4-5-20251001
    python 12_vlm_nn_extract.py --all --dry-run   # scan all, show candidates
"""

import argparse
import base64
import csv
import io
import json
import re
from pathlib import Path
from urllib.parse import urljoin

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

import anthropic
import httpx

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
HTTP_RAW_DIR = OUTPUT_DIR / "fleet_html_raw"
BROWSER_RAW_DIR = OUTPUT_DIR / "fleet_browser_html_raw"

NN_PATTERN = re.compile(r"\bN\d{1,5}[A-Z]{0,2}\b")

DEFAULT_MODEL = "claude-sonnet-4-6"


def load_faa() -> dict:
    """Load FAA registry + ACFTREF."""
    faa_path = OUTPUT_DIR / "faa_master.csv"
    acftref_path = OUTPUT_DIR / "ACFTREF.txt"

    faa = {}
    if faa_path.exists():
        with open(faa_path, "r") as f:
            content = f.read()
        lines = content.splitlines()
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

    for rec in faa.values():
        code = rec["model_code"]
        if code in acftref:
            rec["mfr_name"] = acftref[code]["mfr"]
            rec["model_name"] = acftref[code]["model"]

    return faa


def fuzzy_nns(target: str, registry: dict, max_results: int = 8) -> list[str]:
    """Find N-numbers within edit distance 1, prioritizing OCR-likely swaps."""
    candidates = []
    t = target.upper()
    ocr_swaps = {
        "W": "N", "N": "W", "E": "F", "F": "E", "3": "8", "8": "3",
        "S": "5", "5": "S", "0": "O", "O": "0", "1": "I", "I": "1",
        "B": "8", "D": "0", "G": "6", "6": "G", "Z": "2", "2": "Z",
    }
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    for i in range(1, len(t)):
        # Substitution
        for c in chars:
            candidate = t[:i] + c + t[i + 1 :]
            if candidate != t and candidate in registry:
                priority = 0 if t[i] in ocr_swaps and ocr_swaps[t[i]] == c else 1
                candidates.append((priority, candidate))
        # Deletion
        candidate = t[:i] + t[i + 1 :]
        if candidate in registry:
            candidates.append((1, candidate))
        # Insertion
        for c in chars:
            candidate = t[:i] + c + t[i:]
            if candidate in registry:
                candidates.append((2, candidate))
    # Transposition
    for i in range(1, len(t) - 1):
        candidate = t[:i] + t[i + 1] + t[i] + t[i + 2 :]
        if candidate in registry:
            candidates.append((1, candidate))

    seen = set()
    result = []
    for _, c in sorted(candidates):
        if c not in seen:
            seen.add(c)
            result.append(c)
        if len(result) >= max_results:
            break
    return result


def download_image(url: str, referer: str = "") -> tuple[bytes, str] | None:
    """Download an image, returns (bytes, media_type) or None."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    }
    if referer:
        headers["Referer"] = referer

    try:
        resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=15)
        if resp.status_code != 200:
            return None
        ct = resp.headers.get("content-type", "")
        if "jpeg" in ct or "jpg" in ct:
            media_type = "image/jpeg"
        elif "png" in ct:
            media_type = "image/png"
        elif "webp" in ct:
            media_type = "image/webp"
        elif "gif" in ct:
            media_type = "image/gif"
        else:
            # Guess from URL
            url_lower = url.lower()
            if ".png" in url_lower:
                media_type = "image/png"
            elif ".webp" in url_lower:
                media_type = "image/webp"
            else:
                media_type = "image/jpeg"
        return resp.content, media_type
    except Exception as e:
        print(f"    Download failed: {e}")
        return None


def find_aircraft_images(school_domain: str) -> list[dict]:
    """Find likely aircraft images from raw HTML for a school."""
    images = []
    seen_srcs = set()

    for raw_dir in [HTTP_RAW_DIR, BROWSER_RAW_DIR]:
        school_dir = raw_dir / school_domain
        if not school_dir.exists():
            continue

        from bs4 import BeautifulSoup

        for html_file in sorted(school_dir.glob("*.html")):
            html = html_file.read_text(encoding="utf-8", errors="replace")
            soup = BeautifulSoup(html, "html.parser")

            for img in soup.find_all("img"):
                src = (img.get("src") or "").strip()
                if not src or src in seen_srcs:
                    continue
                seen_srcs.add(src)

                alt = (img.get("alt") or "").strip()

                # Filter: skip tiny images, icons, logos
                width = img.get("width", "")
                height = img.get("height", "")
                if width and width.isdigit() and int(width) < 100:
                    continue
                if height and height.isdigit() and int(height) < 100:
                    continue

                # Skip common non-aircraft patterns
                skip_patterns = [
                    "logo", "icon", "avatar", "favicon", "banner",
                    "social", "facebook", "twitter", "instagram",
                    "google-map", "map-marker", "loading", "spinner",
                    "placeholder", "blank", "spacer", "pixel",
                ]
                src_lower = src.lower()
                if any(p in src_lower for p in skip_patterns):
                    continue

                # Already has N-number in metadata? Skip (handled by 11_)
                all_text = f"{src} {alt}"
                if NN_PATTERN.search(all_text):
                    continue

                # Likely aircraft image heuristics
                aircraft_signals = [
                    "fleet", "aircraft", "airplane", "plane", "cessna",
                    "piper", "cirrus", "diamond", "beech", "mooney",
                    "172", "152", "182", "da40", "da42", "sr20", "sr22",
                    "archer", "warrior", "cherokee", "bonanza", "baron",
                    "rental", "trainer", "training",
                ]
                is_likely = any(s in src_lower or s in alt.lower() for s in aircraft_signals)

                images.append({
                    "src": src,
                    "alt": alt,
                    "likely_aircraft": is_likely,
                    "html_file": html_file.name,
                })

    return images


def vlm_extract_pass1(
    client: anthropic.Anthropic,
    image_data: bytes,
    media_type: str,
    model: str = DEFAULT_MODEL,
) -> list[str]:
    """Pass 1: Ask VLM to read tail numbers from an aircraft image."""
    response = client.messages.create(
        model=model,
        max_tokens=256,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.b64encode(image_data).decode(),
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Look at this image. If it shows an aircraft, read "
                            "any US tail numbers (N-numbers) visible on it. "
                            "US tail numbers start with N followed by 1-5 digits "
                            "and 0-2 uppercase letters (e.g., N172SP, N12345, N88PK).\n\n"
                            "Reply with ONLY the N-number(s), one per line. "
                            "If no aircraft or no visible tail number, reply: NONE"
                        ),
                    },
                ],
            }
        ],
    )

    text = response.content[0].text.strip()
    if "NONE" in text.upper():
        return []

    return NN_PATTERN.findall(text)


def vlm_correct_pass2(
    client: anthropic.Anthropic,
    image_data: bytes,
    media_type: str,
    original_guess: str,
    nearby: list[tuple[str, str]],  # (nn, description)
    model: str = DEFAULT_MODEL,
) -> str | None:
    """Pass 2: Show nearby FAA matches, ask VLM to pick the right one."""
    options = "\n".join(
        f"  - {nn}: {desc}" for nn, desc in nearby
    )

    response = client.messages.create(
        model=model,
        max_tokens=256,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.b64encode(image_data).decode(),
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            f"You read this aircraft's tail number as {original_guess}, "
                            f"but that's not a registered US N-number.\n\n"
                            f"Here are similar registered N-numbers with their aircraft types:\n"
                            f"{options}\n\n"
                            f"Look at the image again carefully. Which of these "
                            f"registered N-numbers is actually on this aircraft? "
                            f"Consider: which aircraft type matches what you see?\n\n"
                            f"Reply with ONLY the correct N-number, or NONE if you "
                            f"can't determine it."
                        ),
                    },
                ],
            }
        ],
    )

    text = response.content[0].text.strip()
    matches = NN_PATTERN.findall(text)
    if matches:
        # Return only if it's one of the offered options
        valid = {nn for nn, _ in nearby}
        for m in matches:
            if m in valid:
                return m
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--school", help="School domain to process")
    parser.add_argument("--all", action="store_true", help="Process all schools with raw HTML")
    parser.add_argument("--dry-run", action="store_true", help="Just show candidate images, don't call API")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model to use")
    parser.add_argument("--limit", type=int, default=20, help="Max images per school")
    args = parser.parse_args()

    faa = load_faa()
    print(f"FAA registry: {len(faa):,} aircraft\n")

    # Determine schools to process
    if args.school:
        domains = [args.school]
    elif args.all:
        domains = set()
        for raw_dir in [HTTP_RAW_DIR, BROWSER_RAW_DIR]:
            if raw_dir.exists():
                for d in raw_dir.iterdir():
                    if d.is_dir():
                        domains.add(d.name)
        domains = sorted(domains)
    else:
        parser.error("Specify --school DOMAIN or --all")
        return

    for domain in domains:
        print(f"\n{'='*50}")
        print(f"  {domain}")
        print(f"{'='*50}")

        images = find_aircraft_images(domain)
        aircraft_imgs = [i for i in images if i["likely_aircraft"]]
        print(f"  Total images: {len(images)}")
        print(f"  Likely aircraft: {len(aircraft_imgs)}")

        if args.dry_run:
            for img in aircraft_imgs[:args.limit]:
                print(f"    {img['src'][:80]}")
                if img["alt"]:
                    print(f"      alt: {img['alt'][:60]}")
            continue

        if not aircraft_imgs:
            print("  No candidate aircraft images found")
            continue

        client = anthropic.Anthropic()
        results = []

        for img in aircraft_imgs[:args.limit]:
            src = img["src"]
            print(f"\n  Image: ...{src[-60:]}")

            # Download
            dl = download_image(src)
            if not dl:
                print("    Skip: download failed")
                continue
            img_bytes, media_type = dl

            # Pass 1
            nns = vlm_extract_pass1(client, img_bytes, media_type, model=args.model)
            if not nns:
                print("    No N-numbers detected")
                continue

            for nn in nns:
                if nn in faa:
                    r = faa[nn]
                    type_str = f"{r.get('mfr_name', '?')} {r.get('model_name', '?')}"
                    print(f"    ✓ {nn}: {r['year']} {type_str} (FAA confirmed)")
                    results.append({"n_number": nn, "status": "confirmed", "pass": 1})
                else:
                    # Pass 2: fuzzy search + correction
                    nearby = fuzzy_nns(nn, faa)
                    if not nearby:
                        print(f"    ✗ {nn}: NOT in FAA, no nearby matches")
                        results.append({"n_number": nn, "status": "unmatched", "pass": 1})
                        continue

                    nearby_with_desc = []
                    for n in nearby[:6]:
                        r = faa[n]
                        desc = f"{r['year']} {r.get('mfr_name', '?')} {r.get('model_name', '?')}"
                        nearby_with_desc.append((n, desc))

                    desc_str = ", ".join(f"{n}={d}" for n, d in nearby_with_desc)
                    print(f"    ? {nn}: NOT in FAA — trying correction from: {desc_str}")

                    corrected = vlm_correct_pass2(
                        client, img_bytes, media_type, nn, nearby_with_desc, model=args.model
                    )
                    if corrected:
                        r = faa[corrected]
                        type_str = f"{r.get('mfr_name', '?')} {r.get('model_name', '?')}"
                        print(f"    → {corrected}: {r['year']} {type_str} (corrected from {nn})")
                        results.append({
                            "n_number": corrected,
                            "status": "corrected",
                            "original": nn,
                            "pass": 2,
                        })
                    else:
                        print(f"    ✗ {nn}: correction failed")
                        results.append({"n_number": nn, "status": "unmatched", "pass": 2})

        confirmed = sum(1 for r in results if r["status"] in ("confirmed", "corrected"))
        print(f"\n  Results: {confirmed}/{len(results)} confirmed/corrected")


if __name__ == "__main__":
    main()
