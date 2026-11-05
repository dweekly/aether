#!/usr/bin/env python3
"""Why do 207 flight-school sites that served our crawler in March refuse it in November?

For the Hackers 2026 talk (docs/hackers-2026-slide-research.md, slide 1). The hypothesis under
test: schools, or their hosts, rolled out defenses against AI scrapers between March and
November 2026.

Subcommands (run on trogdor, from research/FlightSchoolMapping, with the production venv):

  lists   Rebuild 54's site statuses from its own inputs and its own functions, and write
          OUT/lists.json: the newly blocked domains (served the plain HTTP crawler in March,
          blocked or challenged in November) and the control (served in both months).
  probe   Polite, resumable fetches, at most one of each per site, no challenge solving:
            live.jsonl     the homepage of each newly blocked site, once, with a desktop
                           Chrome user agent (status, headers, first 6 KB of body)
            robots.jsonl   /robots.txt now, every site in both groups, same user agent
            wayback.jsonl  /robots.txt as the Internet Archive saw it: the last capture
                           with status 200 from 2025-01-01 through 2026-03-31 (the March
                           baseline), found through the CDX API, fetched raw (id_)
          probe --timeline instead lists the Archive's homepage captures of each blocked site
          since 2025 (timestamp, status; one CDX query per site) into timeline.jsonl
  report  Classify each block (from November's saved browser page and the live answer),
          diff the robots.txt rules for AI crawlers between the two dates, and write
          OUT/blocked_sites.json and OUT/blocked_sites.csv.

Gates (report exits 1): fewer than 200 blocked domains rebuilt, or fewer than 90% of them
probed live; fewer than 50% of the control with a robots.txt answer now.

Usage:
  PY=scripts/.venv/bin/python
  $PY scripts/62_blocked_sites.py lists  --march-out output --recrawl data/research-2026-11/recrawl --out DIR
  $PY scripts/62_blocked_sites.py probe  --out DIR
  $PY scripts/62_blocked_sites.py report --recrawl data/research-2026-11/recrawl --out DIR
"""

import argparse
import csv
import json
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "Accept-Language": "en-US,en;q=0.9"}
# The seven the slide names, then others commonly listed in AI-blocking robots.txt templates.
AI_BOTS = ("gptbot", "claudebot", "anthropic-ai", "ccbot", "google-extended", "perplexitybot", "bytespider")
AI_BOTS_MORE = ("chatgpt-user", "oai-searchbot", "claude-web", "claude-searchbot", "claude-user",
                "applebot-extended", "meta-externalagent", "facebookbot", "amazonbot", "cohere-ai",
                "diffbot", "omgili", "imagesiftbot", "perplexity-user", "timpibot")
WB_FROM, WB_TO = "20250101", "20260331"
SG_HOST_HEADER = "8441280b0c35cbc1147f8ba998a563a7"  # the Host-Header value SiteGround's servers send


def jsonl(p):
    p = Path(p)
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


# ── lists ────────────────────────────────────────────────────────


def cmd_lists(a):
    m = import_module("54_march_vs_november")
    md = json.loads((a.recrawl / "march_domains.json").read_text())
    att = m.november_attempts(a.recrawl)
    attempted = {d for d in md if d in att}
    mst, honest = m.march_status(a.march_out), m.march_status(a.march_out, honest_only=True)
    nst = {d: m.best(att[d]) for d in attempted}
    alive = sorted(d for d in attempted if mst.get(d) == "ok")
    blocked = [d for d in alive if nst[d]["status"] in ("blocked", "challenge") and honest.get(d) == "ok"]
    ok = [d for d in alive if nst[d]["status"] == "ok"]
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "lists.json").write_text(json.dumps({
        "blocked": {d: {"attempts": att[d], "best": nst[d]} for d in blocked},
        "ok": {d: nst[d]["website"] for d in ok}}, indent=1))
    print(f"march domains {len(md)}, alive in March {len(alive)}, newly blocked {len(blocked)}, control {len(ok)}")


# ── probe ────────────────────────────────────────────────────────


def _get(session, url, timeout=20):
    import requests
    t = time.time()
    try:
        r = session.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        body = r.content[:6144].decode("utf-8", "replace")
        return {"url": url, "status": r.status_code, "final_url": r.url, "headers": dict(r.headers),
                "history": [h.status_code for h in r.history], "body": body, "secs": round(time.time() - t, 2)}
    except requests.RequestException as e:
        return {"url": url, "status": None, "error": f"{type(e).__name__}: {e}"[:300], "secs": round(time.time() - t, 2)}


def _start_url(domain, website):
    return website if website and website.startswith("http") else f"https://{domain}/"


def cmd_probe(a):
    import requests
    L = json.loads((a.out / "lists.json").read_text())
    sites = {d: _start_url(d, (r["best"].get("website") or "")) for d, r in L["blocked"].items()}
    sites.update({d: _start_url(d, w) for d, w in L["ok"].items()})
    lock = threading.Lock()

    def appender(name):
        done = {x["domain"] for x in jsonl(a.out / name)}
        fh = (a.out / name).open("a")

        def put(rec):
            with lock:
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
        return done, put

    live_done, live_put = appender("live.jsonl")
    rob_done, rob_put = appender("robots.jsonl")
    wb_done, wb_put = appender("wayback.jsonl")

    def live(d):
        s = requests.Session()
        live_put({"domain": d, **_get(s, sites[d])})

    def robots(d):
        s = requests.Session()
        u = re.match(r"https?://[^/]+", sites[d]).group(0) + "/robots.txt"
        r = _get(s, u)
        r["body"] = r.get("body", "")
        rob_put({"domain": d, **r})

    def wayback_all():
        s = requests.Session()
        s.headers["User-Agent"] = "AetherResearch/1.0 (aviation research; contact: research@aether.aero)"
        for d in [d for d in sites if d not in wb_done]:
            rec = {"domain": d}
            for attempt in range(5):
                try:
                    r = s.get("https://web.archive.org/cdx/search/cdx", timeout=60, params={
                        "url": f"{d}/robots.txt", "from": WB_FROM, "to": WB_TO, "output": "json",
                        "filter": "statuscode:200", "fl": "timestamp,original,digest"})
                    if r.status_code in (429, 503):
                        time.sleep(30 * (attempt + 1))
                        continue
                    rows = r.json()[1:] if r.text.strip() else []
                    rec["captures"] = len(rows)
                    if rows:
                        ts, orig, dig = max(rows, key=lambda x: x[0])
                        time.sleep(1.5)
                        c = s.get(f"https://web.archive.org/web/{ts}id_/{orig}", timeout=60)
                        rec.update({"timestamp": ts, "original": orig, "digest": dig, "status": c.status_code,
                                    "body": c.content[:65536].decode("utf-8", "replace")})
                    break
                except (requests.RequestException, ValueError) as e:
                    rec["error"] = f"{type(e).__name__}: {e}"[:300]
                    time.sleep(10 * (attempt + 1))
            wb_put(rec)
            time.sleep(1.5)

    if a.timeline:
        # The Internet Archive's own homepage captures of each newly blocked site since 2025:
        # status codes only (a SiteGround challenge is archived as a 202), to date the change
        # and to see whether it also met the Archive's crawler, which is not us.
        s = requests.Session()
        s.headers["User-Agent"] = "AetherResearch/1.0 (aviation research; contact: research@aether.aero)"
        tl_done, tl_put = appender("timeline.jsonl")
        for d in [d for d in L["blocked"] if d not in tl_done]:
            rec = {"domain": d}
            for attempt in range(4):
                try:
                    r = s.get("https://web.archive.org/cdx/search/cdx", timeout=60, params={
                        "url": f"{d}/", "from": "20250101", "output": "json",
                        "fl": "timestamp,statuscode,mimetype,length", "collapse": "timestamp:8"})
                    if r.status_code in (429, 503):
                        time.sleep(30 * (attempt + 1))
                        continue
                    rec["rows"] = r.json()[1:] if r.text.strip() else []
                    break
                except (requests.RequestException, ValueError) as e:
                    rec["error"] = f"{type(e).__name__}: {e}"[:300]
                    time.sleep(10 * (attempt + 1))
            tl_put(rec)
            time.sleep(3)
        return

    wb = threading.Thread(target=wayback_all, daemon=True)
    wb.start()
    with ThreadPoolExecutor(12) as ex:
        list(ex.map(live, [d for d in L["blocked"] if d not in live_done]))
        list(ex.map(robots, [d for d in sites if d not in rob_done]))
    print("live and robots done; waiting on wayback", flush=True)
    wb.join()
    print("probe done")


# ── classify ─────────────────────────────────────────────────────


def classify(status, headers, body):
    """(vendor, kind) of a refusal from one HTTP answer. kind: challenge (asks the client to run
    JavaScript or solve something), block (a refusal page), page (the site itself), error."""
    h = {k.lower(): str(v).lower() for k, v in (headers or {}).items()}
    b = (body or "").lower()
    server = h.get("server", "")
    if status is None:
        return "none", "error"
    if "sgcaptcha" in b or "robot challenge screen" in b:
        return "SiteGround", "challenge"
    if h.get("cf-mitigated") == "challenge" or "just a moment" in b or "challenge-platform" in b:
        return "Cloudflare", "challenge"
    if "cloudflare" in server and status in (403, 429, 503) or "attention required! | cloudflare" in b:
        return "Cloudflare", "block"
    if "x-vercel-mitigated" in h or "vercel security checkpoint" in b:
        return "Vercel", "challenge"
    if "__challenge/" in b and "hashcash" in b:
        return "WordPress.com", "challenge"
    if "x-sucuri-id" in h or "sucuri" in server or "sucuri website firewall" in b:
        return "Sucuri", "block" if status >= 400 else "challenge"
    if "wordfence" in b or "your access to this site has been limited" in b:
        return "Wordfence", "block"
    if "imunify360" in b or "imunify" in server or "bot-protection" in b and "imunify" in b:
        return "Imunify360", "challenge" if status < 400 else "block"
    if "_incapsula_resource" in b or "x-iinfo" in h:
        return "Imperva", "block"
    if "errors.edgesuite.net" in b or "akamaighost" in server:
        return "Akamai", "block"
    if "awswaf" in b or "x-amzn-waf-action" in h:
        return "AWS WAF", "challenge"
    if status >= 400 or status == 202:
        host = ("Cloudflare" if "cloudflare" in server or "cf-ray" in h else
                "Wix" if "pepyaka" in server or "x-wix-request-id" in h else
                "Squarespace" if "squarespace" in server else
                "GoDaddy" if "godaddy" in b or "x-siteid" in h else "other")
        return host, "block" if status in (401, 403, 429, 503, 202) else "error"
    return host_of(headers), "page"


def host_of(headers):
    h = {k.lower(): str(v).lower() for k, v in (headers or {}).items()}
    server = h.get("server", "")
    if "cloudflare" in server or "cf-ray" in h:
        return "Cloudflare"
    if "host-header" in h and ("wordpress.com" in h["host-header"] or "wpcloud" in h["host-header"]) \
            or "atomic" in h.get("x-ac", ""):
        return "WordPress.com"
    if "pepyaka" in server or "x-wix-request-id" in h:
        return "Wix"
    if "squarespace" in server:
        return "Squarespace"
    if "x-sucuri-id" in h:
        return "Sucuri"
    if "sg-optimizer" in json.dumps(h) or "x-proxy-cache-info" in h or h.get("host-header") == "6b7412fb82ca5edfd0917e3957f05d89":
        return "SiteGround"
    if "x-vercel-id" in h:
        return "Vercel"
    if "x-amz-cf-id" in h or "cloudfront" in server:
        return "CloudFront"
    if "x-goog" in json.dumps(h) and "google" in server or server == "gws" or "esf" in server:
        return "Google"
    if "akamai" in server:
        return "Akamai"
    return "other"


def robots_rules(text):
    """Per user agent token, whether its group disallows the whole site. Returns
    {agent: full_disallow}, plus markers for hosting-managed blocks."""
    groups, agents, in_rules = {}, [], False
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if in_rules:
                agents, in_rules = [], False
            agents.append(v.lower())
            for ag in agents:
                groups.setdefault(ag, False)
        elif k in ("disallow", "allow"):
            in_rules = True
            if k == "disallow" and v == "/":
                for ag in agents:
                    groups[ag] = True
    return groups


def ai_view(text, bots=AI_BOTS):
    if not text:
        return {"named": [], "full": []}
    g = robots_rules(text)
    named = sorted(b for b in bots if any(b in ag for ag in g))
    full = sorted(b for b in bots if any(b in ag and g[ag] for ag in g))
    return {"named": named, "full": full}


def is_robots(r):
    """A robots.txt answer we can read: 200 and not an HTML page."""
    if not r or r.get("status") != 200:
        return False
    b = (r.get("body") or "").lstrip().lower()
    return not (b.startswith("<!doctype") or b.startswith("<html") or "<head" in b[:500])


def markers(text):
    t = (text or "").lower()
    return {"cloudflare_managed": "begin cloudflare managed content" in t or "cloudflare managed" in t,
            "content_signal": "content-signal" in t}


def cmd_report(a):
    L = json.loads((a.out / "lists.json").read_text())
    live = {x["domain"]: x for x in jsonl(a.out / "live.jsonl")}
    rob = {x["domain"]: x for x in jsonl(a.out / "robots.jsonl")}
    wb = {x["domain"]: x for x in jsonl(a.out / "wayback.jsonl")}
    timeline = {x["domain"]: x for x in jsonl(a.out / "timeline.jsonl")}
    blocked, ok = sorted(L["blocked"]), sorted(L["ok"])
    raw = a.recrawl / "fleet_browser_html_raw"
    rows = []
    for d in blocked + ok:
        grp = "blocked" if d in L["blocked"] else "control"
        r = {"domain": d, "group": grp}
        if grp == "blocked":
            best = L["blocked"][d]["best"]
            r["nov_code"] = best["code"]
            r["nov_crawler"] = best["crawler"]
            saved = next((p for c in (d, "www." + d) if (raw / c).is_dir()
                          for p in sorted((raw / c).glob("*.html"))[:1]), None)
            nb = saved.read_text(errors="replace")[:20000] if saved else ""
            r["nov_saved_vendor"], r["nov_saved_kind"] = classify(best["code"] or 0, {}, nb) if saved else ("", "")
            lv = live.get(d)
            if lv:
                r["live_status"] = lv.get("status")
                r["live_final_domain"] = re.sub(r"^https?://(www\.)?([^/]+).*", r"\2", lv.get("final_url") or "")
                r["live_vendor"], r["live_kind"] = classify(lv.get("status"), lv.get("headers"), lv.get("body"))
                r["live_host"] = host_of(lv.get("headers"))
                r["live_error"] = (lv.get("error") or "")[:120]
            # one vendor per site: the live answer when it is a refusal, else what November saved
            if lv and r["live_kind"] in ("challenge", "block"):
                r["vendor"], r["how"] = r["live_vendor"], r["live_kind"]
            elif r["nov_saved_kind"] in ("challenge", "block") and r["nov_saved_vendor"] not in ("other", ""):
                r["vendor"], r["how"] = r["nov_saved_vendor"], r["nov_saved_kind"]
            elif lv and r["live_kind"] == "page":
                r["vendor"], r["how"] = r["live_host"], "browser_ua_served"
            else:
                r["vendor"], r["how"] = (r.get("live_vendor") or "unknown"), (r.get("live_kind") or "unknown")
            # The live answer landed on another site (a parked or sold domain, a new name): not a block.
            fd = (r.get("live_final_domain") or "").lower().split(":")[0]
            moved = bool(fd) and fd.removeprefix("www.") != d.lower() and lv and lv.get("status") == 200 or \
                "forsale" in fd or "parked" in fd
            r["live_moved"] = bool(moved)
            r["slide_group"] = ("Moved or for sale" if moved else
                                "Unreachable now" if r["how"] == "error" else
                                r["vendor"] if r["vendor"] in ("SiteGround", "Cloudflare", "WordPress.com") else
                                "Other hosts and firewalls")
            # From the live answer alone: did the same address, with a browser user agent, get the page?
            r["mechanism"] = ("serves a browser user agent" if lv and r.get("live_kind") == "page" else
                              "refuses a browser user agent too" if lv and r.get("live_kind") in ("challenge", "block")
                              else "error")
            tl = timeline.get(d, {}).get("rows") or []
            r["archive_captures_2025_on"] = len(tl)
            r["archive_202_2025_on"] = sum(1 for x in tl if x[1] == "202")
            last26 = [x for x in tl if x[0] >= "2026" and x[1] in ("200", "202", "403")]
            r["archive_last_2026_status"] = last26[-1][1] if last26 else ""
            r["archive_last_2026_ts"] = last26[-1][0] if last26 else ""
        nowr, thenr = rob.get(d), wb.get(d)
        hdrs = [((nowr or {}).get("headers") or {}), ((live.get(d) or {}).get("headers") or {})]
        r["siteground_hosted"] = any(any(k.lower() in ("sg-captcha", "x-proxy-cache-info") or
                                         (k.lower() == "host-header" and v == SG_HOST_HEADER) for k, v in h.items())
                                     for h in hdrs)
        r["robots_now_status"] = (nowr or {}).get("status")
        r["robots_now_ok"] = is_robots(nowr)
        r["robots_now_host"] = host_of((nowr or {}).get("headers"))
        r["wayback_ts"] = (thenr or {}).get("timestamp", "")
        r["robots_then_ok"] = is_robots(thenr) if thenr and thenr.get("timestamp") else False
        tn = (nowr or {}).get("body") if r["robots_now_ok"] else ""
        tt = (thenr or {}).get("body") if r["robots_then_ok"] else ""
        vn, vt = ai_view(tn), ai_view(tt)
        vn2, vt2 = ai_view(tn, AI_BOTS + AI_BOTS_MORE), ai_view(tt, AI_BOTS + AI_BOTS_MORE)
        r["ai_named_now"], r["ai_named_then"] = ";".join(vn["named"]), ";".join(vt["named"])
        r["ai_full_now"], r["ai_full_then"] = ";".join(vn["full"]), ";".join(vt["full"])
        r["ai_any_named_now"], r["ai_any_named_then"] = bool(vn2["named"]), bool(vt2["named"])
        r["wildcard_full_now"] = robots_rules(tn).get("*", False)
        r["wildcard_full_then"] = robots_rules(tt).get("*", False)
        mn, mt = markers(tn), markers(tt)
        r["cf_managed_now"], r["cf_managed_then"] = mn["cloudflare_managed"], mt["cloudflare_managed"]
        r["content_signal_now"], r["content_signal_then"] = mn["content_signal"], mt["content_signal"]
        rows.append(r)

    def summarize(rs):
        both = [r for r in rs if r["robots_now_ok"] and r["robots_then_ok"]]
        added = [r for r in both if r["ai_full_now"] and not r["ai_full_then"]]
        named_added = [r for r in both if r["ai_any_named_now"] and not r["ai_any_named_then"]]
        return {
            "sites": len(rs),
            "robots_now_readable": sum(r["robots_now_ok"] for r in rs),
            "robots_then_readable": sum(r["robots_then_ok"] for r in rs),
            "robots_both_readable": len(both),
            "ai_full_block_now": sum(bool(r["ai_full_now"]) for r in rs if r["robots_now_ok"]),
            "ai_full_block_then_of_both": sum(bool(r["ai_full_then"]) for r in both),
            "ai_full_block_now_of_both": sum(bool(r["ai_full_now"]) for r in both),
            "ai_full_block_added_of_both": len(added),
            "ai_full_block_removed_of_both": sum(1 for r in both if r["ai_full_then"] and not r["ai_full_now"]),
            "ai_any_named_added_of_both": len(named_added),
            "cf_managed_now": sum(r["cf_managed_now"] for r in rs),
            "cf_managed_added_of_both": sum(1 for r in both if r["cf_managed_now"] and not r["cf_managed_then"]),
            "content_signal_now": sum(r["content_signal_now"] for r in rs),
            "wildcard_full_now": sum(r["wildcard_full_now"] for r in rs if r["robots_now_ok"]),
            "host_from_robots_answer": dict(Counter(r["robots_now_host"] for r in rs if r["robots_now_status"]).most_common()),
            "added_examples": [r["domain"] for r in added[:15]],
        }

    B = [r for r in rows if r["group"] == "blocked"]
    C = [r for r in rows if r["group"] == "control"]
    res = {
        "blocked_sites": len(B), "control_sites": len(C),
        "probed_live": sum(1 for r in B if "live_status" in r),
        "nov_code": dict(Counter(str(r["nov_code"]) for r in B).most_common()),
        "nov_saved_page": sum(1 for r in B if r["nov_saved_kind"]),
        "nov_saved_vendor": dict(Counter(f"{r['nov_saved_vendor']}:{r['nov_saved_kind']}" for r in B if r["nov_saved_kind"]).most_common()),
        "live_status": dict(Counter(str(r.get("live_status")) for r in B).most_common()),
        "live_vendor_kind": dict(Counter(f"{r.get('live_vendor')}:{r.get('live_kind')}" for r in B).most_common()),
        "vendor_how": dict(Counter(f"{r['vendor']}:{r['how']}" for r in B).most_common()),
        "vendor": dict(Counter(r["vendor"] for r in B).most_common()),
        "how": dict(Counter(r["how"] for r in B).most_common()),
        "live_redirect_away": sum(1 for r in B if r.get("live_final_domain") and r["live_final_domain"].lower().split(":")[0] not in (r["domain"], "www." + r["domain"])),
        "slide_group": dict(Counter(r["slide_group"] for r in B).most_common()),
        "slide_group_mechanism": dict(Counter(f"{r['slide_group']}: {r['mechanism']}" for r in B
                                              if r["slide_group"] not in ("Moved or for sale", "Unreachable now")).most_common()),
        "siteground_archive_last_2026": dict(Counter(r["archive_last_2026_status"] or "none"
                                                     for r in B if r["vendor"] == "SiteGround").most_common()),
        "timeline_sites": len(timeline),
        # Of each platform's school sites in our list, how many now refuse us. Blocked sites by
        # who answered (slide_group); control sites by the headers on their robots.txt answer.
        "platform_share": {p: {"refuse": sum(1 for r in B if r["slide_group"] == p),
                               "served": sum(1 for r in C if (r["siteground_hosted"] if p == "SiteGround"
                                                              else r["robots_now_host"] == p))}
                           for p in ("SiteGround", "WordPress.com", "Cloudflare")},
        "siteground_hosted": {"blocked": sum(r["siteground_hosted"] for r in B),
                              "control": sum(r["siteground_hosted"] for r in C),
                              "blocked_challenged_now": sum(1 for r in B if r["siteground_hosted"] and r["vendor"] == "SiteGround")},
        "robots_blocked": summarize(B), "robots_control": summarize(C),
    }
    gates = {"blocked_rebuilt": len(B) >= 200, "probed_live_90pct": res["probed_live"] >= 0.9 * len(B),
             "control_robots_answer_50pct": sum(1 for r in C if r["robots_now_status"]) >= 0.5 * len(C)}
    res["gates"], res["all_gates_pass"] = gates, all(gates.values())
    (a.out / "blocked_sites.json").write_text(json.dumps(res, indent=2) + "\n")
    keys = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("domain", "group"), k))
    with (a.out / "blocked_sites.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, keys)
        w.writeheader()
        w.writerows(rows)
    print(json.dumps(res, indent=2))
    if not res["all_gates_pass"]:
        sys.exit("FAIL gates: " + ", ".join(k for k, v in gates.items() if not v))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("lists", "probe", "report"))
    ap.add_argument("--march-out", type=Path)
    ap.add_argument("--recrawl", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--timeline", action="store_true", help="probe: only the Archive's homepage captures of the blocked sites")
    a = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).parent))
    {"lists": cmd_lists, "probe": cmd_probe, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
