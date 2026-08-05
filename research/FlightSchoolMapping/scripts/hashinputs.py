#!/usr/bin/env python3
"""Compute a stable digest over a set of pipeline inputs.

Used by the Makefile to decide staleness by *content* rather than mtime, so
that a git checkout, an rsync, or the 15-minute ADS-B cron touching a
directory does not trigger a spurious multi-minute rebuild.

Hashing rules:

  regular file   full content hash (sha256 of the bytes)
  directory      manifest hash — sorted (relative path, size) of every file
                 underneath it, *not* their contents
  missing path   a MISSING sentinel, so a target correctly rebuilds once a
                 previously-absent input shows up

Directories are hashed by manifest because the crawl caches and the ADS-B
snapshot directory run to gigabytes and are append-only in practice: a new
snapshot changes the listing, which is the signal we actually care about.
Pass --mtime to fold mtime into the manifest as well, for a directory whose
files get rewritten in place at identical size.

Usage:
    hashinputs.py PATH [PATH...]
    hashinputs.py --mtime PATH [PATH...]

Prints one hex digest on stdout. Order of arguments does not matter.
"""
import argparse
import hashlib
import os
import sys
from pathlib import Path

CHUNK = 1 << 20  # 1 MiB


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def hash_dir(path: Path, use_mtime: bool) -> str:
    """Hash the listing (paths + sizes), not the file contents."""
    h = hashlib.sha256()
    entries = []
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            fp = Path(dirpath) / name
            try:
                st = fp.stat()
            except OSError:
                continue  # vanished mid-walk (cron writing a snapshot)
            rel = fp.relative_to(path).as_posix()
            if use_mtime:
                entries.append(f"{rel}\0{st.st_size}\0{st.st_mtime_ns}")
            else:
                entries.append(f"{rel}\0{st.st_size}")
    for e in sorted(entries):
        h.update(e.encode("utf-8", "surrogateescape"))
        h.update(b"\n")
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+")
    ap.add_argument(
        "--mtime",
        action="store_true",
        help="include mtime in directory manifests (default: path + size only)",
    )
    args = ap.parse_args()

    outer = hashlib.sha256()
    # Sort so the digest does not depend on argument order.
    for raw in sorted(args.paths):
        p = Path(raw)
        if p.is_dir():
            kind, digest = "dir", hash_dir(p, args.mtime)
        elif p.is_file():
            kind, digest = "file", hash_file(p)
        elif p.exists():
            kind, digest = "other", ""
        else:
            kind, digest = "missing", ""
        outer.update(f"{p.as_posix()}\0{kind}\0{digest}\n".encode("utf-8", "surrogateescape"))

    print(outer.hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
