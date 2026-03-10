#!/usr/bin/env python3
"""
09b_fleet_extract_parallel.py — Parallel sync extraction via Sonnet.

Uses asyncio + httpx to run many concurrent Sonnet API calls.
Saves results incrementally so progress is never lost.

Usage:
    python 09b_fleet_extract_parallel.py
    python 09b_fleet_extract_parallel.py --concurrency 30
    python 09b_fleet_extract_parallel.py --resume   # skip already-done schools
"""

import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

import anthropic
from tqdm.asyncio import tqdm as atqdm

import sys
sys.path.insert(0, str(Path(__file__).parent))
from importlib import import_module
extract_mod = import_module("09b_fleet_extract")

OUTPUT_DIR = Path(__file__).parent.parent / "output"
PROGRESS_FILE = OUTPUT_DIR / "fleet_extract_progress.json"
RESULTS_FILE = OUTPUT_DIR / "fleet_extract_results.jsonl"


async def call_sonnet(client, req, semaphore, retries=2):
    """Make a single Sonnet API call with retry."""
    async with semaphore:
        for attempt in range(retries + 1):
            try:
                message = await client.messages.create(
                    model=req["params"]["model"],
                    max_tokens=req["params"]["max_tokens"],
                    system=req["params"]["system"],
                    messages=req["params"]["messages"],
                )
                return {
                    "custom_id": req["custom_id"],
                    "result": {
                        "type": "succeeded",
                        "message": message.model_dump(),
                    },
                }
            except anthropic.RateLimitError:
                wait = 10 * (attempt + 1)
                await asyncio.sleep(wait)
                continue
            except anthropic.APIStatusError as e:
                if e.status_code >= 500 and attempt < retries:
                    await asyncio.sleep(3)
                    continue
                return {
                    "custom_id": req["custom_id"],
                    "result": {"type": "errored", "error": str(e)},
                }
            except Exception as e:
                if attempt < retries:
                    await asyncio.sleep(2)
                    continue
                return {
                    "custom_id": req["custom_id"],
                    "result": {"type": "errored", "error": str(e)},
                }
        return {
            "custom_id": req["custom_id"],
            "result": {"type": "errored", "error": "max retries exceeded"},
        }


async def run_parallel(requests, concurrency=25, resume_ids=None):
    """Run all requests with bounded concurrency."""
    client = anthropic.AsyncAnthropic()
    semaphore = asyncio.Semaphore(concurrency)

    # Filter out already-done
    if resume_ids:
        requests = [r for r in requests if r["custom_id"] not in resume_ids]
        print(f"  Skipping {len(resume_ids)} already done, {len(requests)} remaining")

    if not requests:
        print("  Nothing to do!")
        return []

    results = []
    succeeded = 0
    errored = 0
    t0 = time.time()

    # Open results file for incremental writes
    results_fh = open(RESULTS_FILE, "a")

    pbar = atqdm(total=len(requests), desc="  Extracting")

    async def _tracked(req):
        nonlocal succeeded, errored
        result = await call_sonnet(client, req, semaphore)
        if result["result"]["type"] == "succeeded":
            succeeded += 1
        else:
            errored += 1
        # Save incrementally
        results_fh.write(json.dumps(result) + "\n")
        results_fh.flush()
        results.append(result)
        pbar.update(1)

        # Update progress file periodically
        done = succeeded + errored
        if done % 50 == 0 or done == len(requests):
            elapsed = time.time() - t0
            rate = done / max(elapsed, 1)
            remaining = len(requests) - done
            progress = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "total": len(requests) + len(resume_ids or set()),
                "done": done + len(resume_ids or set()),
                "succeeded": succeeded + len(resume_ids or set()),
                "errored": errored,
                "rate_per_sec": round(rate, 1),
                "elapsed_sec": round(elapsed),
                "eta_sec": round(remaining / max(rate, 0.01)),
            }
            with open(PROGRESS_FILE, "w") as f:
                json.dump(progress, f, indent=2)

        return result

    # Run with gather for maximum throughput
    await asyncio.gather(*[_tracked(r) for r in requests])
    pbar.close()
    results_fh.close()

    elapsed = time.time() - t0
    print(f"\n  Done: {succeeded} ok, {errored} errors in {elapsed:.0f}s ({len(requests)/max(elapsed,1):.1f} req/s)")

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=25)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true", help="Resume from incremental results")
    args = parser.parse_args()

    print("=" * 60)
    print("Phase 9b: Fleet Extraction (Parallel Sync)")
    print(f"  Concurrency: {args.concurrency}")
    print("=" * 60)

    # Load crawl index
    index_path = OUTPUT_DIR / "fleet_crawl_index.json"
    crawl_index = json.load(open(index_path))
    print(f"  Schools in crawl index: {len(crawl_index):,}")

    if args.limit:
        crawl_index = crawl_index[:args.limit]

    # Build requests
    all_requests = extract_mod.build_batch_requests(crawl_index)
    print(f"  Requests to process: {len(all_requests):,}")

    # Check for resume
    resume_ids = set()
    existing_results = []
    if args.resume and RESULTS_FILE.exists():
        with open(RESULTS_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    if r["result"].get("type") == "succeeded":
                        resume_ids.add(r["custom_id"])
                        existing_results.append(r)
                except json.JSONDecodeError:
                    continue
        print(f"  Found {len(resume_ids)} completed results to resume from")
    elif not args.resume and RESULTS_FILE.exists():
        # Fresh run — clear old results
        RESULTS_FILE.unlink()

    # Estimate cost
    total_chars = sum(
        len(r["params"]["system"]) + len(r["params"]["messages"][0]["content"])
        for r in all_requests if r["custom_id"] not in resume_ids
    )
    est_tokens = total_chars // 4
    remaining = len(all_requests) - len(resume_ids)
    # Sonnet sync: $3/MTok input, $15/MTok output (2x batch price)
    est_cost = est_tokens / 1_000_000 * 3.0 + remaining * 500 / 1_000_000 * 15.0
    print(f"  Estimated cost: ~${est_cost:.2f} (sync pricing)")
    print(f"  Estimated time: ~{remaining / args.concurrency * 6 / 60:.0f} min")

    # Run
    results = asyncio.run(run_parallel(
        all_requests,
        concurrency=args.concurrency,
        resume_ids=resume_ids if resume_ids else None,
    ))

    all_results = existing_results + results

    # Process results
    flight_line = extract_mod.process_results(crawl_index, all_results, all_requests)

    # Save
    out_path = OUTPUT_DIR / "flight_line.json"
    with open(out_path, "w") as f:
        json.dump(flight_line, f, indent=2)

    total_aircraft = sum(len(s["fleet"]) for s in flight_line)
    with_n_numbers = sum(1 for s in flight_line for a in s["fleet"] if a.get("n_number"))
    with_rates = sum(1 for s in flight_line for a in s["fleet"] if a.get("wet_rate_hr"))

    print(f"\n  === Results ===")
    print(f"  Schools with fleet: {len(flight_line):,}")
    print(f"  Total aircraft: {total_aircraft:,}")
    print(f"  With N-numbers: {with_n_numbers:,}")
    print(f"  With rates: {with_rates:,}")
    print(f"  Output: {out_path}")


if __name__ == "__main__":
    main()
