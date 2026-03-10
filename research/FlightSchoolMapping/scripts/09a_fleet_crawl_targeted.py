#!/usr/bin/env python3
"""
09a_fleet_crawl_targeted.py — Crawl a targeted list of schools.

Wraps the 09a crawl engine to accept a custom JSON input instead of
flight_schools_enriched.json. Merges results into the existing
fleet_crawl_index.json.

Usage:
    python 09a_fleet_crawl_targeted.py output/_aopa_crawl_targets.json
    python 09a_fleet_crawl_targeted.py output/_aopa_crawl_targets.json --concurrency 80
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import aiohttp
from tqdm.asyncio import tqdm as atqdm

# Import crawl machinery from 09a
sys.path.insert(0, str(Path(__file__).parent))
from importlib import import_module

crawl_mod = import_module("09a_fleet_crawl")

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


async def run_targeted(input_path: str, concurrency: int = 80):
    schools = json.load(open(input_path))
    print(f"=" * 60)
    print(f"Targeted Fleet Crawl")
    print(f"  Input: {input_path}")
    print(f"  Schools: {len(schools):,}")
    print(f"  Concurrency: {concurrency}")
    print(f"=" * 60)

    scrapeable = [s for s in schools if s.get("website")]
    print(f"  With website: {len(scrapeable):,}")

    semaphore = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(limit=300, ttl_dns_cache=600)
    timeout = aiohttp.ClientTimeout(total=60)
    headers = {
        "User-Agent": crawl_mod.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    crawl_mod.HTML_DIR.mkdir(parents=True, exist_ok=True)
    crawl_mod.RAW_HTML_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    async with aiohttp.ClientSession(
        connector=connector, timeout=timeout, headers=headers
    ) as session:
        tasks = [
            crawl_mod.crawl_school(session, school, semaphore)
            for school in scrapeable
        ]
        crawl_index = []
        total_pages = 0
        errors = 0

        pbar = atqdm(total=len(tasks), desc="  Crawling")

        async def _tracked(coro):
            try:
                result = await coro
            except Exception:
                result = None
            pbar.update(1)
            return result

        results = await asyncio.gather(*[_tracked(t) for t in tasks])
        pbar.close()

        for r in results:
            if r is None:
                errors += 1
            elif isinstance(r, dict):
                crawl_index.append(r)
                total_pages += len(r.get("pages", []))

    elapsed = time.time() - t0

    # Merge into existing crawl index
    index_path = OUTPUT_DIR / "fleet_crawl_index.json"
    if index_path.exists():
        existing = json.load(open(index_path))
        new_websites = {s["school_website"] for s in crawl_index if s.get("school_website")}
        merged = [s for s in existing if s.get("school_website") not in new_websites]
        merged.extend(crawl_index)
        print(f"\n  Merged: {len(crawl_index)} new + {len(merged) - len(crawl_index)} existing = {len(merged)} total")
    else:
        merged = crawl_index

    with open(index_path, "w") as f:
        json.dump(merged, f, indent=2)

    schools_with_fleet = sum(
        1 for s in crawl_index
        if any(p["type"] == "fleet" for p in s.get("pages", []))
    )
    flagged = sum(1 for s in crawl_index if s.get("crawl_flags"))

    print(f"\n  === Results ===")
    print(f"  Schools crawled:       {len(scrapeable):,}")
    print(f"  Schools with pages:    {len(crawl_index):,}")
    print(f"  Schools with fleet pg: {schools_with_fleet:,}")
    print(f"  Total pages saved:     {total_pages:,}")
    print(f"  Flagged:               {flagged:,}")
    print(f"  Errors/timeouts:       {errors:,}")
    print(f"  Time:                  {elapsed:.1f}s")
    print(f"\n  Next: python 09b_fleet_extract.py --merge")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="Path to JSON file with school records")
    parser.add_argument("--concurrency", type=int, default=80)
    args = parser.parse_args()
    asyncio.run(run_targeted(args.input, concurrency=args.concurrency))
