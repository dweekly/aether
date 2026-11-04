#!/usr/bin/env python3
"""Upload a built release to the public R2 bucket behind data.aether-flight.com.

Runs on monster4, where wrangler's OAuth login lives (the token is not copied
to trogdor). Meant for a release's few download files; the explorer's thousands
of small shards ship with the Pages site instead. Pull the release first:

  rsync -a trogdor:/mnt/data/home/dew/dev/aether/research/FlightSchoolMapping/data/release/<rel>/ <dir>/

Layout in the bucket (pass the release's download/ directory as <dir>):
  v/<release>/...            downloads (parquet, JSON, LICENSES.md), immutable
  latest.json                {"release": "<release>"}, short cache

Usage: 36_publish_r2.py <dir> <release> [--set-latest] [--workers 4]
"""

import argparse
import json
import mimetypes
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BUCKET = "aether-public"
IMMUTABLE = "public, max-age=31536000, immutable"
SHORT = "public, max-age=60"


def put(key, path, ctype, cache):
    """wrangler refreshes its own OAuth token; calling the REST API directly
    with the cached token fails with 401 once it expires (about hourly)."""
    for attempt in range(3):
        r = subprocess.run(["npx", "-y", "wrangler@4", "r2", "object", "put", f"{BUCKET}/{key}", "--file", str(path),
                            "--content-type", ctype, "--cache-control", cache, "--remote"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return key
        err = (r.stderr or r.stdout).strip().splitlines()[-1:]
    raise RuntimeError(f"{key}: {err}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("release")
    ap.add_argument("--set-latest", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    root = Path(a.dir)
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix in (".json", ".parquet", ".gz", ".txt", ".md", ".csv")]

    def job(p):
        rel = p.relative_to(root).as_posix()
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if p.suffix == ".parquet":
            ctype = "application/vnd.apache.parquet"
        return put(f"v/{a.release}/{rel}", p, ctype, IMMUTABLE)

    done = 0
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for _ in pool.map(job, files):
            done += 1
            if done % 1000 == 0:
                print(f"  {done}/{len(files)}", flush=True)
    print(f"uploaded {done} files to v/{a.release}/")
    if a.set_latest:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"release": a.release}, f)
        put("latest.json", f.name, "application/json", SHORT)
        print(f"latest.json -> {a.release}")


if __name__ == "__main__":
    main()
