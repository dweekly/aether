#!/usr/bin/env python3
"""
09b_fleet_extract_chunked.py — Submit extraction in chunks of ~600.

Splits the crawl index into chunks, submits each as a separate batch,
polls all in parallel, then merges results.

Usage:
    python 09b_fleet_extract_chunked.py
    python 09b_fleet_extract_chunked.py --chunk-size 500
    python 09b_fleet_extract_chunked.py --resume   # resume polling existing batches
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

import anthropic

# Import from 09b
import sys
sys.path.insert(0, str(Path(__file__).parent))
from importlib import import_module
extract_mod = import_module("09b_fleet_extract")

OUTPUT_DIR = Path(__file__).parent.parent / "output"
BATCH_STATE_FILE = OUTPUT_DIR / "fleet_extract_batches.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunk-size", type=int, default=600)
    parser.add_argument("--resume", action="store_true", help="Resume polling existing batches")
    args = parser.parse_args()

    client = anthropic.Anthropic()

    if args.resume:
        # Load saved batch state
        if not BATCH_STATE_FILE.exists():
            print("No batch state file found. Run without --resume first.")
            return
        state = json.load(open(BATCH_STATE_FILE))
        batch_ids = state["batch_ids"]
        crawl_index = json.load(open(state["crawl_index_path"]))
        all_requests = extract_mod.build_batch_requests(crawl_index)
        chunks = []
        for i in range(0, len(all_requests), state["chunk_size"]):
            chunks.append(all_requests[i:i + state["chunk_size"]])
        print(f"Resuming {len(batch_ids)} batches...")
    else:
        # Load crawl index and build requests
        index_path = OUTPUT_DIR / "fleet_crawl_index.json"
        crawl_index = json.load(open(index_path))
        print(f"Schools in crawl index: {len(crawl_index):,}")

        all_requests = extract_mod.build_batch_requests(crawl_index)
        print(f"Total requests: {len(all_requests):,}")

        # Split into chunks
        chunks = []
        for i in range(0, len(all_requests), args.chunk_size):
            chunks.append(all_requests[i:i + args.chunk_size])
        print(f"Split into {len(chunks)} chunks of ~{args.chunk_size}")

        # Submit each chunk as a batch
        batch_ids = []
        for i, chunk in enumerate(chunks):
            print(f"\n  Submitting chunk {i+1}/{len(chunks)} ({len(chunk)} requests)...")
            batch_requests = []
            for req in chunk:
                batch_requests.append({
                    "custom_id": req["custom_id"],
                    "params": {
                        "model": req["params"]["model"],
                        "max_tokens": req["params"]["max_tokens"],
                        "system": req["params"]["system"],
                        "messages": req["params"]["messages"],
                    },
                })
            batch = client.messages.batches.create(requests=batch_requests)
            batch_ids.append(batch.id)
            print(f"    Batch ID: {batch.id}")
            time.sleep(1)  # Brief pause between submissions

        # Save state
        state = {
            "batch_ids": batch_ids,
            "chunk_size": args.chunk_size,
            "crawl_index_path": str(index_path),
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(BATCH_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
        print(f"\nBatch IDs saved to {BATCH_STATE_FILE}")

    # Poll all batches
    print(f"\nPolling {len(batch_ids)} batches...")
    completed = set()
    all_results = []

    while len(completed) < len(batch_ids):
        for i, bid in enumerate(batch_ids):
            if bid in completed:
                continue
            batch = client.messages.batches.retrieve(bid)
            c = batch.request_counts
            done = c.succeeded + c.errored + c.canceled + c.expired
            total = done + c.processing

            if batch.processing_status == "ended":
                print(f"  Chunk {i+1}: DONE ({c.succeeded} ok, {c.errored} err)")
                # Download results
                for result in client.messages.batches.results(bid):
                    all_results.append({
                        "custom_id": result.custom_id,
                        "result": {
                            "type": result.result.type,
                            "message": result.result.message.model_dump()
                            if hasattr(result.result, "message") and result.result.message
                            else {},
                        },
                    })
                completed.add(bid)
            else:
                print(f"  Chunk {i+1}: {done}/{total} ({c.succeeded} ok) - {batch.processing_status}")

        if len(completed) < len(batch_ids):
            remaining = len(batch_ids) - len(completed)
            print(f"  ... {remaining} chunks remaining, waiting 30s")
            time.sleep(30)

    print(f"\nAll batches complete. {len(all_results):,} total results.")

    # Process results
    flight_line = extract_mod.process_results(crawl_index, all_results, all_requests)

    # Save
    out_path = OUTPUT_DIR / "flight_line.json"
    with open(out_path, "w") as f:
        json.dump(flight_line, f, indent=2)

    total_aircraft = sum(len(s["fleet"]) for s in flight_line)
    with_n_numbers = sum(1 for s in flight_line for a in s["fleet"] if a.get("n_number"))

    print(f"\n  === Results ===")
    print(f"  Schools with fleet: {len(flight_line):,}")
    print(f"  Total aircraft: {total_aircraft:,}")
    print(f"  With N-numbers: {with_n_numbers:,}")
    print(f"  Output: {out_path}")


if __name__ == "__main__":
    main()
