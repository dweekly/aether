#!/usr/bin/env python3
"""March vs November 2026: what changed on flight-school websites in eight months.

For the Hackers 2026 talk (docs/hackers-2026-research-plan.md, workstream 3). Inputs:

  March crawl     the production output/ dir: fleet_crawl_index.json, _aopa_extract_index.json,
                  fleet_browser_index.json, fleet_browser_text/, expansion_crawl.jsonl
  November crawl  data/research-2026-11/recrawl/: the same crawlers on the same target
                  lists (50a_recrawl_targets.py), with one attempt line per site
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
  $PY scripts/50_march_vs_november.py --march-out OUT --recrawl REC \\
      --march-runs RUNS_MARCH --nov-runs RUNS_NOV --model claude-haiku-5-5
Writes REC/march_vs_november.json and docs/hackers-2026-march-vs-november.md.
"""

import argparse
import hashlib
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
    if status in (401, 403, 429, 503):
        return "blocked"
    return "http_error"


def march_status(out):
    """domain -> best status any March crawler recorded."""
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
    for e in json.loads((out / "fleet_browser_index.json").read_text()):
        d, flags = norm_domain(e.get("school_website", "")), e.get("crawl_flags", [])
        if "homepage_failed" in flags:
            put(d, "unreachable")
        elif "captcha_challenge" in flags:
            put(d, "challenge")
        elif e.get("pages"):
            put(d, "ok")
    for sub in (out / "fleet_browser_text").iterdir():  # the AOPA browser crawl wrote no index
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
            elif code != 200 and not a.get("pages"):
                s = "blocked"  # e.g. a 202 WAF challenge with no content
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
        need += [runs / p / f for p in PASSES for f in ("index.json", "requests.jsonl", f"{a.model}/results.jsonl")]
    missing = [str(p) for p in need if not p.exists()]
    if missing:
        sys.exit("FAIL missing inputs:\n  " + "\n  ".join(missing))

    gates = {}
    R = {"model": a.model}

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
    redirected, red_examples = [], []
    march_redirects = {norm_domain(e.get("school_website", "")): e.get("redirect_domain", "")
                       for e in json.loads((a.march_out / "fleet_browser_index.json").read_text())}
    already = 0
    for d in alive_march:
        oks = [x for x in att[d] if x["status"] == "ok" and x["final_domain"]]
        if oks and all(site(x["final_domain"]) != site(d) for x in oks):
            to = oks[0]["final_domain"]
            if march_redirects.get(d) and site(march_redirects[d]) == site(to):
                already += 1
                continue
            redirected.append(d)
            if len(red_examples) < 12:
                red_examples.append({"from": d, "to": to})
    R["sites"] = {"march_domains": len(march_domains), "attempted": len(attempted),
                  "alive_in_march": len(alive_march), "died": len(died), "died_by_reason": dict(died_kinds),
                  "died_examples": died_examples, "retried": len(retry),
                  "redirect_away_new": len(redirected), "redirect_away_same_as_march": already,
                  "redirect_examples": red_examples}

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
    for d in sorted(both_domains):
        ps = [p for p in PASSES if d in mar[p] and d in nov[p]]
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
        "Generated by `research/FlightSchoolMapping/scripts/50_march_vs_november.py`; do not edit by hand.",
        "Workstream 3 of [the research plan](hackers-2026-research-plan.md). Every number below comes from",
        f"that script's run, which {'passed' if R['all_gates_pass'] else '**failed**'} its gates.",
        "",
        "## Method",
        "",
        "- **Same sites.** The November crawl visited the March target lists, rebuilt from March's own",
        "  indexes by `50a_recrawl_targets.py`, with the March crawlers (`09a`, `10`, `09f`) and their",
        "  rate limits and headers, writing a new directory. One difference: the browser crawl ran with",
        "  `--no-evasion` (no stealth script, no automation-flag masking, no waiting out bot challenges),",
        "  so a site behind a challenge that March got through counts as challenged now, not as dead.",
        f"- **Same extraction.** Both months were extracted by `extract_v2.py` with `{R['model']}`, the same",
        "  prompt (the March prompt plus the training-price block) and the same settings. The March side is",
        "  workstream 1's run over the March caches. The expansion crawl (`09f`) is counted for site",
        "  status only: it had its own prompt in March and `extract_v2` has no layout for it.",
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
        "",
        "## Sites",
        "",
        f"Of {s['alive_in_march']:,} domains a March crawler reached, **{s['died']:,}** could not be reached in"
        f" November by any crawler{' or on a retry' if s['retried'] else ''}, "
        f"**{s['redirect_away_new']:,}** now redirect to a different domain (another "
        f"{s['redirect_away_same_as_march']:,} already did in March), and **{s['newly_defunct']:,}** are now classed"
        " defunct by the model (parked or closed) where March's pages were not.",
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
        f" ({c['changed']:,} schools). Read the change above against that floor.",
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
    lines += ["", "## Extraction results", "",
              "| Pass:result | March | November |", "|---|---|---|"]
    keys = sorted(set(R["results"]["march"]) | set(R["results"]["november"]))
    for k in keys:
        lines.append(f"| {k} | {R['results']['march'].get(k, 0):,} | {R['results']['november'].get(k, 0):,} |")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
