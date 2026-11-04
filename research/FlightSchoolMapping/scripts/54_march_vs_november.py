#!/usr/bin/env python3
"""March vs November 2026: what changed on flight-school websites in eight months.

For the Hackers 2026 talk (docs/hackers-2026-research-plan.md, workstream 3). Inputs:

  March crawl     the production output/ dir: fleet_crawl_index.json, _aopa_extract_index.json,
                  fleet_browser_index.json, fleet_browser_text/, expansion_crawl.jsonl
  November crawl  data/research-2026-11/recrawl/: the same crawlers on the same target
                  lists (54a_recrawl_targets.py), with one attempt line per site
                  (crawl_attempts_http.jsonl, crawl_attempts_browser.jsonl, expansion_crawl.jsonl)
                  and an optional retry of the sites that failed (retry/crawl_attempts_http.jsonl)
  Extractions     extract_v2.py run dirs (http/, browser/) for each month, same prompt and
                  model: <runs>/<layout>/index.json, requests.jsonl, <model>/results.jsonl

Measures:

  1. Sites: reachable in March, unreachable now (DNS gone, refused, HTTP error), now
     redirecting to another domain, or now classed "defunct" by the model.
  2. Fleets: per school, tails (well-formed N-numbers) added and removed, comparing only
     the crawl passes the school has in both months.
  3. Prices: for the same tail at the same school with one wet rate in each month, the
     change. Also the subset where each month's rate appears verbatim on that month's page
     text, so a misread cannot pass as a price change.
  4. New fields (instructor rate, PPL package price, club dues and initiation, simulator
     rate): change where both months have a value.
  Control: schools whose page text is byte-identical in both months. Any change there is
  the extraction's own run-to-run noise, not the website's.

Gates (exit 1, after writing the outputs with the failure stated): any input missing;
November attempted fewer than 90% of March's domains; fewer than 300 same-tail,
same-school price pairs.

Usage (on trogdor, from research/FlightSchoolMapping):
  PY=scripts/.venv/bin/python   # the production checkout's venv
  $PY scripts/54_march_vs_november.py --march-out OUT --recrawl REC \\
      --march-runs RUNS_MARCH --nov-runs RUNS_NOV --model claude-haiku-5-5
Writes REC/march_vs_november.json and docs/hackers-2026-march-vs-november.md.
"""

import argparse
import hashlib
import html as htmllib
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from importlib import import_module
from pathlib import Path
from urllib.parse import urlparse

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
DEFAULT_MD = SCRIPTS.parents[2] / "docs" / "hackers-2026-march-vs-november.md"

_m49 = import_module("49_scrape_facts")  # the same tail rules as the scrape-facts check
N_RE, normalize_tail = _m49.N_RE, _m49.normalize

PASSES = ("http", "browser")
MIN_PAIRS = 300
MIN_COVERAGE = 0.90
FLAT_USD = 1.0  # |change| under a dollar counts as flat
NEW_FIELDS = ("instructor_rate_hr", "ppl_package_usd", "club_initiation_fee", "club_dues_usd", "simulator_rate_hr")
RANK = {"ok": 3, "challenge": 2, "blocked": 2, "http_error": 1, "unreachable": 0}


def norm_domain(url):
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    d = urlparse(url).netloc.lower().split(":")[0]
    return d[4:] if d.startswith("www.") else d


def site(domain):
    """Registrable part, crudely: the last two labels (three under a two-letter ccTLD's second level)."""
    parts = domain.split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "ac", "edu", "gov"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def jsonl(path):
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


# ── Site status ──────────────────────────────────────────────────


def error_kind(err):
    e = (err or "").lower()
    if any(s in e for s in ("name or service not known", "nodename", "err_name_not_resolved",
                            "no address associated", "temporary failure in name resolution")):
        return "dns"
    if any(s in e for s in ("ssl", "certificate", "err_cert", "err_ssl")):
        return "tls"
    if "timeout" in e or "timed_out" in e:
        return "timeout"
    if any(s in e for s in ("refused", "connect call failed", "err_connection", "cannot connect", "reset")):
        return "connect"
    return "other"


def http_kind(status):
    """A served answer that is not the page: a challenge or refusal (blocked), or an error."""
    if status < 400 or status in (401, 403, 429, 503):
        return "blocked"
    return "http_error"


def march_status(out, honest_only=False):
    """domain -> best status any March crawler recorded. honest_only: only the HTTP and
    expansion crawlers, which send the research user agent and no browser evasion."""
    st = {}

    def put(d, s):
        if d and RANK[s] > RANK.get(st.get(d), -1):
            st[d] = s

    for f in ("fleet_crawl_index.json", "_aopa_extract_index.json"):
        for e in json.loads((out / f).read_text()):
            d, flags = norm_domain(e.get("school_website", "")), e.get("crawl_flags", [])
            if e.get("pages"):
                put(d, "challenge" if "captcha_challenge" in flags else "ok")
            elif any(f_.startswith("http_") for f_ in flags):
                put(d, "blocked")
    for e in ([] if honest_only else json.loads((out / "fleet_browser_index.json").read_text())):
        d, flags = norm_domain(e.get("school_website", "")), e.get("crawl_flags", [])
        if "homepage_failed" in flags or e.get("redirect_domain") == "chromewebdata":
            put(d, "unreachable")
        elif "captcha_challenge" in flags:
            put(d, "challenge")
        elif e.get("pages"):
            put(d, "ok")
    for sub in ([] if honest_only else (out / "fleet_browser_text").iterdir()):  # the AOPA browser crawl wrote no index
        if sub.is_dir() and sum(f.stat().st_size for f in sub.glob("*.txt")) >= 200:
            put(norm_domain(sub.name), "ok")
    for e in jsonl(out / "expansion_crawl.jsonl"):
        s = e.get("status", "")
        put(norm_domain(e.get("domain", "")), "ok" if s == "done" else
            http_kind(int(s[5:])) if s.startswith("http_") else "unreachable")
    return st


def november_attempts(rec):
    """domain -> list of attempts {crawler, status, final_domain, error, kind}."""
    att = defaultdict(list)
    for crawler, fname in (("http", "crawl_attempts_http.jsonl"), ("browser", "crawl_attempts_browser.jsonl")):
        for a in jsonl(rec / fname):
            flags, code = a.get("crawl_flags", []), a.get("status")
            if crawler == "browser" and "homepage_failed" in flags:
                s = "unreachable"
            elif code is None:
                s = "unreachable"
            elif code >= 400:
                s = http_kind(code)
            elif a.get("final_domain") == "chromewebdata" or (code != 200 and (
                    not a.get("pages") or (crawler == "browser" and a.get("chars", 0) < 200))):
                s = "blocked"  # e.g. a 202 WAF challenge: no content, or Chrome's own error page
            elif "captcha_challenge" in flags:
                s = "challenge"
            else:
                s = "ok"
            att[norm_domain(a.get("domain") or a.get("website", ""))].append(
                {"crawler": crawler, "status": s, "code": code, "final_domain": a.get("final_domain", ""),
                 "error": a.get("error", ""), "kind": error_kind(a.get("error")) if s == "unreachable" else ""})
    for a in jsonl(rec / "expansion_crawl.jsonl"):
        s = a.get("status", "")
        code = a.get("http_status")
        status = "ok" if s == "done" else http_kind(code or int(s[5:])) if s.startswith("http_") else "unreachable"
        err = "; ".join(a.get("errors", []))
        att[norm_domain(a.get("domain", ""))].append(
            {"crawler": "expansion", "status": status, "code": code,
             "final_domain": norm_domain(a.get("final_url", "")), "error": err,
             "kind": error_kind(err) if status == "unreachable" else ""})
    return att


# ── 09a's cleaner emptying pages (both months share it) ─────────
# Same measure as `extraction_audit.py body-loss` (docs/hackers-2026-extraction-audit.md, on
# research-audit): a school counts when its raw pages hold at least 2,000 characters of visible
# text and the cleaned pages 09b sent hold under 5% of that; the cause is a <body> class or id
# matching clean_html's boilerplate filter, which deletes the whole body.
BOILER = re.compile(r"(cookie|gdpr|consent|popup|modal|overlay|sidebar|widget|social|share|"
                    r"newsletter|subscribe|banner|advertisement|ad-|advert)", re.I)


def text_len(h):
    h = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", h, flags=re.S | re.I)
    return len(re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", h))).strip())


def is_binary(t):
    if not t:
        return False
    bad = sum(1 for ch in t[:5000] if ch == "\ufffd" or (ord(ch) < 32 and ch not in "\n\r\t"))
    return bad / min(len(t), 5000) > 0.05


def body_loss(index_path, html_dir, raw_dir, browser_domains):
    n = lost = body_hit = rescued = 0
    emptied = set()
    for s in json.loads(Path(index_path).read_text()):
        raw_t = clean_t = 0
        body_match = False
        for p in s.get("pages", []):
            fc, fr = html_dir / p["html_path"], raw_dir / p["html_path"]
            if not (fc.exists() and fr.exists()):
                continue
            rh = fr.read_text(encoding="utf-8", errors="replace")
            if is_binary(rh):
                continue
            raw_t += text_len(rh)
            clean_t += text_len(fc.read_text(encoding="utf-8", errors="replace"))
            m = re.search(r"<body([^>]*)>", rh[:200_000], re.I)
            if m and BOILER.search(" ".join(re.findall(r'(?:class|id)\s*=\s*["\']([^"\']*)', m.group(1)))):
                body_match = True
        if raw_t < 2000:
            continue
        n += 1
        if clean_t < 0.05 * raw_t:
            emptied.add(norm_domain(s.get("school_website", "")))
            lost += 1
            body_hit += body_match
            rescued += norm_domain(s.get("school_website", "")) in browser_domains
    return {"schools_with_raw_text": n, "kept_under_5pct": lost,
            "share": round(lost / n, 3) if n else None, "body_matches_filter": body_hit,
            "also_in_browser_pass": rescued}, emptied


def best(attempts):
    return max(attempts, key=lambda a: RANK[a["status"]])


# ── Extractions ──────────────────────────────────────────────────


def parse_result(rec):
    res = rec["result"]
    if res["type"] != "succeeded":
        return None
    text = "".join(b.get("text", "") for b in res["message"]["content"] if b.get("type") == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        try:
            return json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            return None


def pages_part(user):
    i = user.find("\nWeb pages:\n\n")
    return user[i:] if i >= 0 else user


def load_month(runs, model):
    """{pass: {domain: {"tails": {tail: [rates]}, "fields": {f: [values]}, "types": [..],
    "pages": set(sha), "text": str}}} plus counts of results by type."""
    out, counts = {}, Counter()
    for p in PASSES:
        run = runs / p
        index = {e["custom_id"]: e for e in json.loads((run / "index.json").read_text())}
        users = {r["custom_id"]: r["user"] for r in map(json.loads, (run / "requests.jsonl").open())}
        doms = {}
        for rec in jsonl(run / model / "results.jsonl"):
            cid = rec["custom_id"]
            data = parse_result(rec)
            counts[(p, rec["result"]["type"] if data is not None or rec["result"]["type"] != "succeeded" else "unparseable")] += 1
            if data is None:
                continue
            d = index[cid]["domain"].lower()
            e = doms.setdefault(d, {"tails": defaultdict(list), "fields": defaultdict(list), "types": [],
                                    "pages": set(), "text": []})
            body = pages_part(users[cid])
            e["pages"].add(hashlib.sha256(body.encode()).hexdigest())
            e["text"].append(body)
            e["types"].append(data.get("school_type", ""))
            for a in data.get("fleet") or []:
                t = normalize_tail(a.get("n_number") or "") if (a.get("n_number") or "").strip() else ""
                if t and N_RE.match(t):
                    e["tails"][t].append(a.get("wet_rate_hr"))
            ppl = data.get("private_pilot_package") or {}
            vals = {"instructor_rate_hr": data.get("instructor_rate_hr"), "ppl_package_usd": ppl.get("price_usd"),
                    "club_initiation_fee": data.get("club_initiation_fee"),
                    "club_dues_usd": (data.get("club_dues_usd"), data.get("club_dues_period") or ""),
                    "simulator_rate_hr": data.get("simulator_rate_hr")}
            for f, v in vals.items():
                if (v[0] if isinstance(v, tuple) else v) is not None:
                    e["fields"][f].append(v)
        for e in doms.values():
            e["text"] = "\n".join(e["text"])
        out[p] = doms
    return out, counts


def one(values):
    """The single distinct non-null value, or None if there are none or several."""
    vs = {v for v in values if v is not None}
    return vs.pop() if len(vs) == 1 else None


def on_page(rate, text):
    """Does the rate appear as a number on the page text? 185 matches 185, 185.00, $185; 1250 matches 1,250; not inside a tail like N185SP."""
    if rate is None or rate <= 0:
        return False
    whole = int(rate) if float(rate).is_integer() else None
    forms = []
    if whole is not None:
        forms += [str(whole), f"{whole:,}"]
        forms = [re.escape(f) + r"(?:\.0{1,2})?" for f in dict.fromkeys(forms)]
    else:
        forms = [re.escape(f"{rate:.2f}"), re.escape(f"{rate:,.2f}")]
    return re.search(r"(?<![\d.,A-Za-z])(?:" + "|".join(forms) + r")(?![\d]|,\d)", text) is not None


# ── Statistics ───────────────────────────────────────────────────

BIN_LABELS = ["< -20%", "-20 to -10%", "-10 to -5%", "-5 to 0%", "flat",
              "0 to +5%", "+5 to +10%", "+10 to +20%", "> +20%"]


def bin_label(delta, p):
    if abs(delta) < FLAT_USD:
        return "flat"
    for edge, label in ((-20, "< -20%"), (-10, "-20 to -10%"), (-5, "-10 to -5%"), (0, "-5 to 0%")):
        if p < edge:
            return label
    for edge, label in ((5, "0 to +5%"), (10, "+5 to +10%"), (20, "+10 to +20%")):
        if p <= edge:
            return label
    return "> +20%"


def change_stats(pairs):
    """pairs: [(march, november)] in dollars."""
    if not pairs:
        return {"n": 0}
    d = [n - m for m, n in pairs]
    pct = [(n - m) / m * 100 for m, n in pairs]
    up = sum(1 for x in d if x >= FLAT_USD)
    down = sum(1 for x in d if x <= -FLAT_USD)
    flat = len(d) - up - down
    hist = Counter(bin_label(x, p) for x, p in zip(d, pct))
    changed = [p for x, p in zip(d, pct) if abs(x) >= FLAT_USD]
    return {"n": len(pairs), "up": up, "down": down, "flat": flat,
            "share_up": round(up / len(d), 3), "share_down": round(down / len(d), 3),
            "share_flat": round(flat / len(d), 3),
            "median_change_usd": round(statistics.median(d), 2),
            "median_change_pct": round(statistics.median(pct), 2),
            "mean_change_pct": round(statistics.mean(pct), 2),
            "median_change_pct_when_changed": round(statistics.median(changed), 2) if changed else None,
            "median_march_usd": round(statistics.median(m for m, _ in pairs), 2),
            "median_november_usd": round(statistics.median(n for _, n in pairs), 2),
            "bins": {label: hist.get(label, 0) for label in BIN_LABELS}}


# ── Main ─────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--march-out", required=True, type=Path)
    ap.add_argument("--recrawl", required=True, type=Path)
    ap.add_argument("--march-runs", required=True, type=Path)
    ap.add_argument("--nov-runs", required=True, type=Path)
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--md", type=Path, default=DEFAULT_MD)
    a = ap.parse_args()

    need = [a.march_out / f for f in ("fleet_crawl_index.json", "_aopa_extract_index.json", "fleet_browser_index.json",
                                      "fleet_browser_text", "expansion_crawl.jsonl")]
    need += [a.recrawl / f for f in ("march_domains.json", "crawl_attempts_http.jsonl", "crawl_attempts_browser.jsonl",
                                     "expansion_crawl.jsonl")]
    for runs in (a.march_runs, a.nov_runs):
        need += [runs / p / f for p in PASSES
                 for f in ("index.json", "requests.jsonl", f"{a.model}/results.jsonl", f"{a.model}/batches.json")]
    missing = [str(p) for p in need if not p.exists()]
    if missing:
        sys.exit("FAIL missing inputs:\n  " + "\n  ".join(missing))

    gates = {}
    R = {"model": a.model}

    # Like for like: each pass ran with the same model, effort and prompt in both months.
    settings, same = {}, True
    for p in PASSES:
        bm = json.loads((a.march_runs / p / a.model / "batches.json").read_text())
        bn = json.loads((a.nov_runs / p / a.model / "batches.json").read_text())
        keys = ("model", "effort", "thinking", "max_tokens", "prompt_fingerprint")
        settings[p] = {"march": {k: bm.get(k) for k in keys}, "november": {k: bn.get(k) for k in keys}}
        same &= settings[p]["march"] == settings[p]["november"]
    gates["same_settings"] = {"pass": same, "settings": settings}
    R["effort"] = sorted({settings[p][m]["effort"] for p in PASSES for m in ("march", "november")})

    # ── 1. Sites ──
    march_domains = json.loads((a.recrawl / "march_domains.json").read_text())
    att = november_attempts(a.recrawl)
    retry = {}
    retry_path = a.recrawl / "retry" / "crawl_attempts_http.jsonl"
    if retry_path.exists():
        for x in jsonl(retry_path):
            retry[norm_domain(x.get("domain") or x.get("website", ""))] = x
    attempted = {d for d in march_domains if d in att}
    coverage = len(attempted) / len(march_domains)
    gates["coverage"] = {"march_domains": len(march_domains), "attempted": len(attempted),
                         "share": round(coverage, 4), "min": MIN_COVERAGE, "pass": coverage >= MIN_COVERAGE}
    mst = march_status(a.march_out)
    nst = {d: best(att[d]) for d in attempted}
    R["status_march"] = dict(Counter(mst.get(d, "not_recorded") for d in march_domains))
    R["status_november"] = dict(Counter(nst[d]["status"] for d in attempted))
    alive_march = [d for d in attempted if mst.get(d) == "ok"]
    died, died_kinds, died_examples = [], Counter(), []
    for d in alive_march:
        b = nst[d]
        if b["status"] not in ("unreachable", "http_error"):
            continue
        r = retry.get(d)
        if r is not None and r.get("status") is not None and r["status"] < 400:
            continue  # came back on the retry: transient
        kind = b["kind"] if b["status"] == "unreachable" else f"http_{b['code']}"
        died.append(d)
        died_kinds[kind] += 1
        if len(died_examples) < 12:
            died_examples.append({"domain": d, "why": kind})
    honest = march_status(a.march_out, honest_only=True)
    blocked_now = [d for d in alive_march if nst[d]["status"] in ("blocked", "challenge")]
    newly_blocked = Counter(f"{nst[d]['status']}_{nst[d]['code']}" for d in blocked_now if honest.get(d) == "ok")
    blocked_browser_only = sum(1 for d in blocked_now if honest.get(d) != "ok")
    redirected, red_examples = [], []
    march_redirects = {norm_domain(e.get("school_website", "")): e.get("redirect_domain", "")
                       for e in json.loads((a.march_out / "fleet_browser_index.json").read_text())}
    already = 0
    for d in alive_march:
        oks = [x for x in att[d] if x["status"] == "ok" and x["final_domain"]
               and x["final_domain"] != "chromewebdata"]
        if oks and all(site(x["final_domain"]) != site(d) for x in oks):
            to = oks[0]["final_domain"]
            if march_redirects.get(d) and site(march_redirects[d]) == site(to):
                already += 1
                continue
            redirected.append(d)
            if len(red_examples) < 12:
                red_examples.append({"from": d, "to": to})
    targets = json.loads((a.recrawl / "targets_http.json").read_text()) if (a.recrawl / "targets_http.json").exists() else []
    logged = {x.get("website", "") for x in jsonl(a.recrawl / "crawl_attempts_http.jsonl")}
    unlogged = [t["website"] for t in targets
                if t["website"] not in logged and "https://" + t["website"] not in logged]
    R["crawl_volume"] = {
        "march_http_pages": sum(len(e.get("pages", [])) for e in json.loads((a.march_out / "fleet_crawl_index.json").read_text())),
        "november_http_pages": sum(x.get("pages", 0) for x in jsonl(a.recrawl / "crawl_attempts_http.jsonl")),
        "november_browser_pages": sum(x.get("pages", 0) for x in jsonl(a.recrawl / "crawl_attempts_browser.jsonl")),
        "http_targets": len(targets), "http_targets_without_attempt_line": len(unlogged),
        "http_targets_without_attempt_line_list": unlogged}
    R["sites"] = {"march_domains": len(march_domains), "attempted": len(attempted),
                  "alive_in_march": len(alive_march), "died": len(died), "died_by_reason": dict(died_kinds),
                  "died_examples": died_examples, "retried": len(retry),
                  "newly_blocked": sum(newly_blocked.values()), "newly_blocked_by_code": dict(newly_blocked),
                  "blocked_now_reached_in_march_only_by_stealth_browser": blocked_browser_only,
                  "redirect_away_new": len(redirected), "redirect_away_same_as_march": already,
                  "redirect_examples": red_examples}

    # 09a's cleaner bug, measured the same way in both months.
    mar_bdom = {norm_domain(sub.name) for sub in (a.march_out / "fleet_browser_text").iterdir()
                if sub.is_dir() and any(sub.glob("*.txt"))}
    nov_bdom = {norm_domain(x.get("domain", "")) for x in jsonl(a.recrawl / "crawl_attempts_browser.jsonl")
                if x.get("pages")}
    bl_m, emp_m = body_loss(a.march_out / "fleet_crawl_index.json", a.march_out / "fleet_html",
                            a.march_out / "fleet_html_raw", mar_bdom)
    bl_n, emp_n = body_loss(a.recrawl / "fleet_crawl_index.json", a.recrawl / "fleet_html",
                            a.recrawl / "fleet_html_raw", nov_bdom)
    emptied_either = emp_m | emp_n
    bl_n["emptied_in_one_month_only"] = len(emp_m ^ emp_n)
    R["cleaner_body_loss"] = {"march": bl_m, "november": bl_n}

    # ── 2-4. Extractions ──
    mar, mar_counts = load_month(a.march_runs, a.model)
    nov, nov_counts = load_month(a.nov_runs, a.model)
    R["results"] = {"march": {f"{p}:{t}": n for (p, t), n in sorted(mar_counts.items())},
                    "november": {f"{p}:{t}": n for (p, t), n in sorted(nov_counts.items())}}

    def types(month, d):
        return {t for p in PASSES for t in month[p].get(d, {}).get("types", [])}

    both_domains = {d for p in PASSES for d in mar[p] if d in nov[p]}
    newly_defunct = sorted(d for d in both_domains if "defunct" in types(nov, d) and "defunct" not in types(mar, d))
    R["sites"]["newly_defunct"] = len(newly_defunct)
    R["sites"]["newly_defunct_examples"] = newly_defunct[:12]

    # Fleets, per school, over the passes the school has in both months.
    fleet = {"schools": 0, "schools_with_tails": 0, "unchanged": 0, "changed": 0, "added": 0, "removed": 0,
             "kept": 0, "tails_march": 0, "tails_november": 0}
    control = {"schools": 0, "schools_with_tails": 0, "changed": 0, "added": 0, "removed": 0, "kept": 0}
    per_school = []
    pairs, pairs_verbatim, pairs_ctrl, ambiguous = [], [], [], 0
    pair_rows = []
    fields = {f: [] for f in NEW_FIELDS}
    fields_ctrl = {f: [] for f in NEW_FIELDS}
    http_dropped = 0
    for d in sorted(both_domains):
        ps = [p for p in PASSES if d in mar[p] and d in nov[p]]
        if "http" in ps and d in emptied_either:  # the cleaner emptied its HTTP pages in a month
            ps.remove("http")
            http_dropped += 1
        if not ps:
            continue
        identical = all(mar[p][d]["pages"] == nov[p][d]["pages"] for p in ps)
        tm = set().union(*(mar[p][d]["tails"] for p in ps))
        tn = set().union(*(nov[p][d]["tails"] for p in ps))
        tgt = control if identical else fleet
        tgt["schools"] += 1
        if tm or tn:
            tgt["schools_with_tails"] += 1
            add, rem = tn - tm, tm - tn
            tgt["added"] += len(add)
            tgt["removed"] += len(rem)
            tgt["kept"] += len(tm & tn)
            if add or rem:
                tgt["changed"] += 1
            elif not identical:
                fleet["unchanged"] += 1
            if not identical:
                fleet["tails_march"] += len(tm)
                fleet["tails_november"] += len(tn)
                per_school.append({"domain": d, "march": len(tm), "november": len(tn),
                                   "added": len(add), "removed": len(rem)})
        # Prices: same tail, same school, same pass, one rate each month.
        for t in sorted(tm & tn):
            for p in ps:
                rm, rn = mar[p][d]["tails"].get(t), nov[p][d]["tails"].get(t)
                if rm is None or rn is None:
                    continue
                vm, vn = one(rm), one(rn)
                if vm is None or vn is None:
                    if any(r is not None for r in rm) and any(r is not None for r in rn):
                        ambiguous += 1
                    continue
                if vm <= 0 or vn <= 0:
                    continue
                if identical:
                    pairs_ctrl.append((vm, vn))
                else:
                    pairs.append((vm, vn))
                    verb = on_page(vm, mar[p][d]["text"]) and on_page(vn, nov[p][d]["text"])
                    if verb:
                        pairs_verbatim.append((vm, vn))
                    pair_rows.append({"domain": d, "tail": t, "pass": p, "march": vm, "november": vn,
                                      "verbatim": verb})
                break
        for f in NEW_FIELDS:
            for p in ps:
                vm, vn = one(mar[p][d]["fields"].get(f, [])), one(nov[p][d]["fields"].get(f, []))
                if vm is None or vn is None:
                    continue
                if f == "club_dues_usd":
                    if vm[1] != vn[1] or not vm[1]:
                        break
                    vm, vn = vm[0], vn[0]
                if vm <= 0 or vn <= 0:
                    break
                (fields_ctrl if identical else fields)[f].append((vm, vn))
                break

    fleet["http_pass_dropped_cleaner_emptied"] = http_dropped
    R["fleet"] = fleet
    R["fleet_control_identical_pages"] = control
    big = sorted(per_school, key=lambda r: -(r["added"] + r["removed"]))[:10]
    R["fleet_biggest_changes"] = big
    R["prices"] = {"all": change_stats(pairs), "verbatim_on_both_pages": change_stats(pairs_verbatim),
                   "control_identical_pages": change_stats(pairs_ctrl), "ambiguous_skipped": ambiguous}
    R["new_fields"] = {f: change_stats(v) for f, v in fields.items()}
    R["new_fields_control_identical_pages"] = {f: change_stats(v) for f, v in fields_ctrl.items()}
    n_pairs = len(pairs)
    gates["matched_price_pairs"] = {"n": n_pairs, "min": MIN_PAIRS, "pass": n_pairs >= MIN_PAIRS}
    R["gates"] = gates
    cost = {}
    for p in PASSES:
        u = a.nov_runs / p / a.model / "usage.json"
        est = [json.loads(l) for l in (a.nov_runs / p / "log.jsonl").open() if '"estimate"' in l] \
            if (a.nov_runs / p / "log.jsonl").exists() else []
        cost[p] = {"recorded_usd": json.loads(u.read_text())["cost_usd_batch"] if u.exists() else None,
                   "estimated_usd": est[-1]["est_cost_usd_batch"] if est else None}
    R["november_extraction_cost"] = cost
    R["all_gates_pass"] = all(g["pass"] for g in gates.values())

    (a.recrawl / "march_vs_november.json").write_text(json.dumps(R, indent=2) + "\n")
    with open(a.recrawl / "march_vs_november_pairs.jsonl", "w") as f:
        for row in pair_rows:
            f.write(json.dumps(row) + "\n")
    a.md.write_text(render(R))
    print(json.dumps({"gates": gates, "sites": {k: v for k, v in R["sites"].items() if not k.endswith("examples")},
                      "prices_all": R["prices"]["all"].get("n"), "fleet": fleet}, indent=2))
    if not R["all_gates_pass"]:
        print("FAIL gate(s): " + ", ".join(k for k, g in gates.items() if not g["pass"]))
        return 1
    print("PASS")
    return 0


def pct(x):
    return f"{x * 100:.0f}%" if x is not None else "n/a"


def stat_row(label, s):
    if not s.get("n"):
        return f"| {label} | 0 | | | | | |"
    return (f"| {label} | {s['n']:,} | {pct(s['share_up'])} | {pct(s['share_down'])} | {pct(s['share_flat'])} | "
            f"{s['median_change_usd']:+,.2f} | {s['median_change_pct']:+.1f}% |")


def render(R):
    g, s, f, c, pr = R["gates"], R["sites"], R["fleet"], R["fleet_control_identical_pages"], R["prices"]
    lines = [
        "# March vs November 2026: flight-school websites eight months apart",
        "",
        "Generated by `research/FlightSchoolMapping/scripts/54_march_vs_november.py`; do not edit by hand.",
        "Workstream 3 of [the research plan](hackers-2026-research-plan.md). Every number below comes from",
        f"that script's run, which {'passed' if R['all_gates_pass'] else '**failed**'} its gates.",
        "",
        "## Method",
        "",
        "- **Same sites.** The November crawl visited the March target lists, rebuilt from March's own",
        "  indexes by `54a_recrawl_targets.py`, with the March crawlers (`09a`, `10`, `09f`) and their",
        "  rate limits and headers, writing a new directory. One difference: the browser crawl ran with",
        "  `--no-evasion` (no stealth script, no automation-flag masking, no waiting out bot challenges),",
        "  so a site behind a challenge that March got through counts as challenged now, not as dead.",
        "- **Different machine.** March's crawl ran on macOS (its error strings are macOS resolver",
        "  messages); November's ran on trogdor (Linux). Some challenges (SiteGround's `sgcaptcha`, for",
        "  one) key on the client IP, so part of the new blocking may be about who is asking, not the site.",
        f"- **Same extraction.** Both months were extracted by `extract_v2.py` with `{R['model']}`, the same",
        "  prompt (the March prompt plus the training-price block) and the same settings. The March side is",
        "  workstream 1's run over the March caches. The expansion crawl (`09f`) is counted for site",
        "  status only: it had its own prompt in March and `extract_v2` has no layout for it.",
        f"- **Effort {', '.join(R['effort'])} in both months**, as the March-side run used. At low effort the",
        "  model sometimes lists one entry per aircraft type instead of per tail on big fleet tables, so",
        "  tails are undercounted there in both months alike; the control below bounds what that adds to",
        "  run-to-run churn. Re-running both sides at medium would have been about twice the budget.",
        "- **Pairing is by domain**, through each run's own `index.json`, never by March batch `custom_id`.",
        "- **Both months share 09a's cleaner bug.** `clean_html` deletes any element whose class or id",
        "  matches its boilerplate filter, and on many sites that is `<body>`, so the HTTP pass sees an",
        "  empty page (found by the hand audit, `docs/hackers-2026-extraction-audit.md` on `research-audit`).",
        "  The cleaner was not changed for November, so the loss is the same in kind both months; see",
        "  *HTTP pages emptied by the cleaner* below for how much. Most of those sites are also in the",
        "  browser pass, which does not use that cleaner.",
        "- **Same school, same tail.** A price change is the same well-formed N-number at the same school",
        "  domain, in the same crawl pass, with exactly one wet rate in each month. Tails with two different",
        "  rates in one month are skipped and counted.",
        "- **Control.** Schools whose page text is byte-identical in both months cannot have changed on",
        "  the web, so whatever changes there is the model's own run-to-run noise. They are reported",
        "  separately and left out of the headline numbers.",
        "",
        "## Gates",
        "",
        "| Gate | Value | Threshold | Result |",
        "|---|---|---|---|",
        f"| Domains attempted vs March | {g['coverage']['attempted']:,} of {g['coverage']['march_domains']:,} "
        f"({g['coverage']['share'] * 100:.1f}%) | ≥ {MIN_COVERAGE * 100:.0f}% | {'pass' if g['coverage']['pass'] else 'FAIL'} |",
        f"| Same-tail, same-school price pairs | {g['matched_price_pairs']['n']:,} | ≥ {MIN_PAIRS} | "
        f"{'pass' if g['matched_price_pairs']['pass'] else 'FAIL'} |",
        f"| Same model, effort and prompt fingerprint per pass, both months | "
        + "; ".join(f"{p}: {v['november']['effort']}, `{v['november']['prompt_fingerprint']}`"
                    for p, v in g["same_settings"]["settings"].items())
        + f" | identical | {'pass' if g['same_settings']['pass'] else 'FAIL'} |",
        "",
        "## Sites",
        "",
        f"Of {s['alive_in_march']:,} domains a March crawler reached, **{s['died']:,}** could not be reached in"
        f" November by any crawler{' or on a retry' if s['retried'] else ''}, "
        f"**{s['redirect_away_new']:,}** now redirect to a different domain (another "
        f"{s['redirect_away_same_as_march']:,} already did in March), and **{s['newly_defunct']:,}** are now classed"
        " defunct by the model (parked or closed) where March's pages were not. Another"
        f" **{s['newly_blocked']:,}** answer but refuse the crawler (a 403 or similar, or a bot challenge):"
        " each served a page to the same plain-HTTP crawler and user agent in March, so this is new"
        " blocking, not new death (a further"
        f" {s['blocked_now_reached_in_march_only_by_stealth_browser']:,} blocked now were reached in March only by"
        " the stealth browser, so the change there may be ours)."
        " By status: " + ", ".join(f"{k} {v:,}" for k, v in sorted(s["newly_blocked_by_code"].items(),
                                                                   key=lambda kv: -kv[1])) + ".",
        "",
        "| Why unreachable | Domains |",
        "|---|---|",
    ]
    for k, v in sorted(s["died_by_reason"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {k} | {v:,} |")
    lines += ["", "Status by month (best of all crawlers):", "",
              "| Status | March | November |", "|---|---|---|"]
    for k in ("ok", "challenge", "blocked", "http_error", "unreachable", "not_recorded"):
        lines.append(f"| {k} | {R['status_march'].get(k, 0):,} | {R['status_november'].get(k, 0):,} |")
    if s["redirect_examples"]:
        lines += ["", "Examples of new redirects: " + "; ".join(f"`{e['from']}` → `{e['to']}`"
                                                              for e in s["redirect_examples"][:8]) + "."]
    lines += [
        "",
        "## Fleets",
        "",
        f"{f['schools']:,} schools have an extraction in both months with changed page text; "
        f"{f['schools_with_tails']:,} list at least one tail in either month. Of those, {f['changed']:,} "
        f"changed their tail list: **{f['added']:,} tails added and {f['removed']:,} removed**, against "
        f"{f['kept']:,} kept ({f['tails_march']:,} tails in March, {f['tails_november']:,} in November).",
        "",
        f"Control: on {c['schools_with_tails']:,} schools with tails and byte-identical pages, the model's"
        f" own churn was {c['added']:,} added and {c['removed']:,} removed against {c['kept']:,} kept"
        f" ({c['changed']:,} school{'s' if c['changed'] != 1 else ''} changed). Read the change above against that floor.",
        "",
        "## Prices: same tail, same school",
        "",
        "| Sample | n | Up | Down | Flat (< $1) | Median change | Median % |",
        "|---|---|---|---|---|---|---|",
        stat_row("All pairs, changed pages", pr["all"]),
        stat_row("Both rates verbatim on their page", pr["verbatim_on_both_pages"]),
        stat_row("Control: identical pages", pr["control_identical_pages"]),
        "",
        f"{pr['ambiguous_skipped']:,} tail pairs were skipped because a month had two different rates for one tail.",
    ]
    if pr["all"].get("n"):
        a = pr["all"]
        lines += ["", f"Median wet rate {a['median_march_usd']:,.0f} USD in March, {a['median_november_usd']:,.0f} USD"
                  f" in November on the same tails; where a rate moved, the median move was "
                  f"{a['median_change_pct_when_changed']:+.1f}%.", "",
                  "| Change | Tails |", "|---|---|"]
        for label, n in a["bins"].items():
            lines.append(f"| {label} | {n:,} |")
    lines += ["", "## Training prices (new fields)", "",
              "Where a school gave the field in both months, changed pages only.", "",
              "| Field | n | Up | Down | Flat (< $1) | Median change | Median % |", "|---|---|---|---|---|---|---|"]
    for k, v in R["new_fields"].items():
        lines.append(stat_row(f"`{k}`", v))
    bl = R["cleaner_body_loss"]
    lines += ["", "## HTTP pages emptied by the cleaner", "",
              "Schools whose raw HTTP pages hold at least 2,000 characters of visible text, and how many of"
              " them kept under 5% of it after 09a's `clean_html` (what the HTTP pass's model saw).", "",
              "| Month | Schools with raw text | Kept under 5% | Share | `<body>` matches the filter |"
              " Also in the browser pass |", "|---|---|---|---|---|---|"]
    for m in ("march", "november"):
        x = bl[m]
        lines.append(f"| {m.title()} | {x['schools_with_raw_text']:,} | {x['kept_under_5pct']:,} | "
                     f"{pct(x['share'])} | {x['body_matches_filter']:,} | {x['also_in_browser_pass']:,} |")
    lines += ["", f"{bl['november']['emptied_in_one_month_only']:,} schools were emptied in one month but not the"
              " other, or had enough raw text to measure in only one month (a site changed its `<body>` class,"
              " say, or its pages); either would show up as tails appearing or vanishing. So every school emptied in either month is compared on its browser pass only:"
              f" the HTTP pass was dropped for {R['fleet']['http_pass_dropped_cleaner_emptied']:,} schools in the"
              " fleet, price and field comparisons."]
    cv, cost = R["crawl_volume"], R["november_extraction_cost"]
    lines += ["", "## Crawl and cost", "",
              f"- HTTP pages saved: {cv['march_http_pages']:,} in March, {cv['november_http_pages']:,} in November;"
              f" browser pages in November: {cv['november_browser_pages']:,}.",
              f"- {cv['http_targets_without_attempt_line']:,} of {cv['http_targets']:,} HTTP targets raised an"
              " exception in the HTTP crawl that left no attempt line (they are not in the November HTTP pass): "
              + (", ".join(f"`{w}`" for w in cv["http_targets_without_attempt_line_list"]) or "none") + ".",
              "- November extraction at Message Batches prices: " + "; ".join(
                  f"{p} estimated ${c['estimated_usd']}, recorded ${c['recorded_usd']}" for p, c in cost.items())
              + f"; total recorded ${sum(c['recorded_usd'] or 0 for c in cost.values()):.2f}.",
              "", "## Extraction results", "",
              "| Pass:result | March | November |", "|---|---|---|"]
    keys = sorted(set(R["results"]["march"]) | set(R["results"]["november"]))
    for k in keys:
        lines.append(f"| {k} | {R['results']['march'].get(k, 0):,} | {R['results']['november'].get(k, 0):,} |")
    lines += ["", "## Reproduce", "", "On trogdor, from `research/FlightSchoolMapping/` in a checkout of this branch,"
              " with the production checkout's venv as `$PY`, its `output/` as `$OUT` and"
              " `data/research-2026-11/recrawl` as `$REC` (`PLAYWRIGHT_BROWSERS_PATH=0` for the browser crawl):", "",
              "```",
              "$PY scripts/54a_recrawl_targets.py --march $OUT --out $REC",
              "$PY scripts/09a_fleet_crawl.py --targets $REC/targets_http.json --out-dir $REC",
              "$PY scripts/09f_corpus_expansion.py --crawl-only --targets $REC/targets_expansion.json --out-dir $REC",
              "$PY scripts/10_fleet_browser_crawl.py --crawl-only --no-evasion --concurrency 8 \\",
              "    --targets $REC/targets_browser.json --out-dir $REC",
              "$PY scripts/54a_recrawl_targets.py --retry-from $REC",
              "$PY scripts/09a_fleet_crawl.py --targets $REC/retry/targets_http.json --out-dir $REC/retry",
              "$PY scripts/extract_v2.py build --layout http --index $REC/fleet_crawl_index.json \\",
              "    --pages-dir $REC/fleet_html --run $REC/extract/http",
              "$PY scripts/extract_v2.py build --layout browser --pages-dir $REC/fleet_browser_text \\",
              "    --meta-index $OUT/fleet_crawl_index.json --meta-index $OUT/fleet_browser_index.json --run $REC/extract/browser",
              "# then for each run: extract_v2.py estimate | submit | fetch --wait, --model claude-haiku-5-5",
              "$PY scripts/54_march_vs_november.py --march-out $OUT --recrawl $REC \\",
              "    --march-runs data/research-2026-11/model-rerun --nov-runs $REC/extract --model claude-haiku-5-5",
              "```", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
