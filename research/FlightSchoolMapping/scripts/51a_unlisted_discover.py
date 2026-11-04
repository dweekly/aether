#!/usr/bin/env python3
"""Workstream 4, step 1: find the website (if any) of each of the 372 unlisted
training operations, with Claude Sonnet 5.5 and the server-side web search tool.

For each operation the model gets the FAA registrant name, type and address
city/state, the home airports our flight data inferred, the main models and a
sample of tails, and returns: the website where these aircraft are offered (the
registrant's own, or the school or club that flies a leasing entity's aircraft),
its evidence, and what kind of operation it appears to be.

Resumable: one JSON line per operation in discovery.jsonl; finished registrants are
skipped. Spend is computed from each response's recorded usage and the run stops
before the budget is exceeded.

Usage:
  51a_unlisted_discover.py --limit 10          # pilot
  51a_unlisted_discover.py --budget 15         # the rest
"""

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parent))
import unlisted_ops as U  # noqa: E402

MODEL = "claude-sonnet-5-5"
MAX_SEARCHES = 3
EFFORT = "low"  # the first 20 operations ran at medium with 4-5 searches; see the doc
# USD per token / per search, platform.claude.com/docs/en/about-claude/pricing (read 2026-11-03).
PRICE = {"input": 2e-6, "cache_write": 2.5e-6, "cache_read": 0.10e-6, "output": 10e-6, "search": 0.01}
TYPES = ["school", "club", "leaseback_llc", "charter", "aerial_work", "government", "other", "unknown"]

SCHEMA = {
    "type": "object",
    "properties": {
        "website": {"type": ["string", "null"],
                    "description": "Home page URL where these aircraft are offered for training or rental, or null"},
        "website_relation": {"type": "string", "enum": ["registrant_own", "operating_school_or_club", "none"]},
        "operator_name": {"type": ["string", "null"]},
        "operation_type": {"type": "string", "enum": TYPES},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "tails_seen_online": {"type": "array", "items": {"type": "string"},
                              "description": "Listed tails you saw on a page tied to this website"},
        "evidence": {"type": "string", "description": "What links the registrant to the website, with source URLs"},
    },
    "required": ["website", "website_relation", "operator_name", "operation_type", "confidence",
                 "tails_seen_online", "evidence"],
    "additionalProperties": False,
}

SYSTEM = """You identify US general-aviation operators from FAA registry records, using web search.
Work efficiently: a few targeted searches, then answer. Prefer the operator's own site over directories
(AOPA, Yelp, LinkedIn, Facebook, OpenCorporates and registry mirrors such as FlightAware are evidence, not
websites). If the registrant is a leasing or holding LLC whose aircraft fly on a flight school's or club's line,
give that school's or club's website and set website_relation to operating_school_or_club. Only name a website
you have evidence ties to this registrant or these tails (same name, same airport, a listed tail, or a stated
leaseback). If nothing ties, return website null. Do not guess.

operation_type: school (flight school, academy, college or university aviation program), club (flying club),
leaseback_llc (leasing or holding entity, including one that leases onto a school), charter (Part 135 or air tour),
aerial_work (banner towing, survey, pipeline patrol, ag, photography), government (police, state, federal, military),
other (a business flying its own aircraft, a dealer, a museum, a maintenance shop), unknown (nothing found)."""


def prompt(op):
    homes = "; ".join(f"{h} {name} ({k} aircraft)" for h, k, name in op["homes"]) or "unknown"
    models = ", ".join(f"{m} x{k}" for m, k in op["models"])
    tails = ", ".join(op["tails"][:12]) + (f" (and {len(op['tails']) - 12} more)" if len(op["tails"]) > 12 else "")
    return (f"FAA registrant: {op['registrant']}\n"
            f"Registrant type: {op['registrant_type']}\n"
            f"Registered address: {op['registrant_city'].title()}, {op['registrant_state']}\n"
            f"Home airports seen in flight data: {homes}\n"
            f"Aircraft: {op['aircraft']} training-like aircraft; main models {models}\n"
            f"Tails: {tails}\n\n"
            "These aircraft fly like trainers. Find who operates them and the website where they are offered, "
            "and say what kind of operation this is.")


def cost(u):
    st = getattr(u, "server_tool_use", None)
    searches = (getattr(st, "web_search_requests", 0) or 0) if st else 0
    return {"input": u.input_tokens, "cache_write": u.cache_creation_input_tokens or 0,
            "cache_read": u.cache_read_input_tokens or 0, "output": u.output_tokens, "searches": searches}


def usd(c):
    return (c["input"] * PRICE["input"] + c["cache_write"] * PRICE["cache_write"]
            + c["cache_read"] * PRICE["cache_read"] + c["output"] * PRICE["output"]
            + c["searches"] * PRICE["search"])


def discover(client, op, raw_dir):
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": MAX_SEARCHES,
              "user_location": {"type": "approximate", "country": "US",
                                "region": op["registrant_state"] or None,
                                "city": (op["registrant_city"] or "").title() or None}}]
    tools[0]["user_location"] = {k: v for k, v in tools[0]["user_location"].items() if v}
    messages = [{"role": "user", "content": prompt(op)}]
    total = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0, "searches": 0}
    raw = []
    for _ in range(5):  # pause_turn continuations
        r = client.messages.create(
            model=MODEL, max_tokens=8000, messages=messages, tools=tools,
            # Breakpoint 1: tools + system, shared by every call. Top-level: the growing turn.
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            cache_control={"type": "ephemeral"},
            output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}})
        for k, v in cost(r.usage).items():
            total[k] += v
        raw.append(r.model_dump(mode="json"))
        if r.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": r.content}]
    safe = "".join(ch if ch.isalnum() else "_" for ch in op["registrant"])[:80]
    (raw_dir / f"{safe}.json").write_text(json.dumps(raw))
    texts = [b.text for b in r.content if b.type == "text"]
    result, err = None, None
    if r.stop_reason == "end_turn" and texts:
        try:
            result = json.loads(texts[-1])
        except json.JSONDecodeError as e:
            err = f"unparseable: {e}"
    else:
        err = f"stop_reason {r.stop_reason}"
    # Every URL the search tool returned, so evidence can be checked against what was actually seen.
    seen = sorted({c.get("url") for resp in raw for b in resp["content"]
                   if b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list)
                   for c in b["content"] if isinstance(c, dict) and c.get("url")})
    return {"registrant": op["registrant"], "result": result, "error": err, "usage": total,
            "usd": round(usd(total), 5), "search_urls": seen, "model": MODEL,
            "ts": datetime.now(timezone.utc).isoformat()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--budget", type=float, default=15.0, help="stop starting new calls at this total USD")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--retry-errors", action="store_true")
    a = ap.parse_args()

    ops = U.rebuild()
    U.OUT.mkdir(parents=True, exist_ok=True)
    raw_dir = U.OUT / "discovery_raw"
    raw_dir.mkdir(exist_ok=True)
    (U.OUT / "operations.json").write_text(json.dumps(ops, indent=1) + "\n")
    path = U.OUT / "discovery.jsonl"
    done, spent = {}, 0.0
    if path.exists():
        for line in path.open():  # a retried operation has several lines: the last wins, all are paid
            d = json.loads(line)
            done[d["registrant"]] = d
            spent += d["usd"]
    todo = [o for o in ops if o["registrant"] not in done
            or (a.retry_errors and done[o["registrant"]]["error"])]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(ops)} operations, {len(done)} done, ${spent:.2f} spent so far, {len(todo)} to run", flush=True)

    client = anthropic.Anthropic(api_key=U.api_key(), max_retries=4)
    lock = threading.Lock()
    state = {"spent": spent, "n": 0, "stopped": False}

    def run(op):
        with lock:
            if state["spent"] >= a.budget:
                state["stopped"] = True
                return None
        try:
            d = discover(client, op, raw_dir)
        except anthropic.APIError as e:
            d = {"registrant": op["registrant"], "result": None, "error": f"api: {type(e).__name__}: {e}"[:500],
                 "usage": {}, "usd": 0.0, "search_urls": [], "model": MODEL,
                 "ts": datetime.now(timezone.utc).isoformat()}
        with lock:
            state["spent"] += d["usd"]
            state["n"] += 1
            with path.open("a") as f:
                f.write(json.dumps(d) + "\n")
            r = d["result"] or {}
            print(f"[{state['n']}/{len(todo)}] ${state['spent']:.2f} {op['registrant'][:40]:40s} "
                  f"{r.get('operation_type', '-'):14s} {r.get('website') or d['error'] or '-'}", flush=True)
        return d

    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as ex:
        for _ in as_completed([ex.submit(run, o) for o in todo]):
            pass
    print(f"done in {time.time() - t0:.0f}s; total spend ${state['spent']:.2f}"
          + (" (budget reached, some operations not run)" if state["stopped"] else ""))


if __name__ == "__main__":
    main()
