"""Page text for model passes over a school's own pages, and the verbatim check on what comes back.

A value a model returns (a phone, a street address, opening hours, a name, a quote) is accepted only
if it appears on the page the model says it came from, after the same normalization on both sides:
lower case, "&" as "and", punctuation and runs of space collapsed. Phones are compared as their ten
digits against every phone-shaped run on the page. The page's checkable text is its visible text plus
its JSON-LD blocks and tel:/mailto: links, all of which are in the page source.

Used by 60_google_schools.py (classify) and 61_contact_provenance.py.
"""

import html as _html
from pathlib import Path
import json
import re

from bs4 import BeautifulSoup

PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.\-]*)?\(?([2-9]\d{2})\)?[\s.\-]*([2-9]\d{2})[\s.\-]*(\d{4})(?!\d)")


def visible_text(raw_html):
    """Visible text of a raw HTML page (footer and header kept: that is where contact details live),
    then its JSON-LD blocks and tel:/mailto: link targets."""
    soup = BeautifulSoup(raw_html, "html.parser")
    ld = [t.get_text() for t in soup.find_all("script", type=re.compile("ld\\+json", re.I))]
    links = sorted({a["href"].split(":", 1)[1].split("?")[0].strip() for a in soup.find_all("a", href=True)
                    if re.match(r"(?i)^(tel|mailto):", a["href"])})
    for t in soup(["script", "style", "noscript", "svg", "template", "iframe"]):
        t.decompose()
    text = soup.get_text("\n")
    lines = [" ".join(x.split()) for x in text.splitlines()]
    out = "\n".join(x for x in lines if x)
    extra = []
    for blob in ld:
        try:
            extra.append(json.dumps(json.loads(blob), ensure_ascii=False))
        except (json.JSONDecodeError, ValueError):
            extra.append(" ".join(blob.split()))
    if extra:
        out += "\n[JSON-LD] " + "\n[JSON-LD] ".join(extra)
    if links:
        out += "\n[links] " + " | ".join(_html.unescape(x) for x in links)
    return out


def norm(s):
    s = _html.unescape(s or "").lower().replace("&", " and ").replace("’", "'")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def phones(text):
    return {"".join(m.groups()) for m in PHONE.finditer(text or "")}


def phone10(value):
    m = PHONE.search(value or "")
    return "".join(m.groups()) if m else ""


def on_page(value, page_text, kind="text"):
    """True if value appears verbatim (normalized) on the page text."""
    if not value or not page_text:
        return False
    if kind == "phone":
        p = phone10(value)
        return bool(p) and p in phones(page_text)
    v = norm(value)
    return bool(v) and f" {v} " in f" {norm(page_text)} "  # whole words: "Main St" is not in "Main Street"


# ── Cached pages of a school's site ──────────────────────────────

def _date(ts, path=None):
    if ts:
        return str(ts)[:10]
    if path is not None and Path(path).exists():
        from datetime import datetime, timezone
        return datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc).date().isoformat()
    return ""


def http_pages(index_path, raw_dir, label, key, cleaned_dir=None):
    """domain -> pages from a 09a crawl index: raw HTML of each page (not 09a's cleaned copy, which drops
    the header and footer), else the cleaned copy when the raw one was not kept. key(url) gives the
    domain key."""
    out = {}
    if not Path(index_path).exists():
        return out
    for s in json.loads(Path(index_path).read_text()):
        d = key(s.get("school_website", ""))
        if not d:
            continue
        for pg in s.get("pages") or []:
            p = Path(raw_dir) / pg["html_path"]
            if not p.exists() and cleaned_dir is not None:
                p = Path(cleaned_dir) / pg["html_path"]
            if p.exists():
                out.setdefault(d, []).append({"url": pg["url"], "path": str(p), "kind": "html", "type": pg.get("type", ""),
                                              "retrieved": _date(s.get("crawl_ts"), p), "cache": label})
    return out


def browser_pages(index_path, raw_dir, text_dir, label, key):
    """domain -> pages from a 10 browser index: the rendered raw HTML when kept, else the rendered text."""
    out = {}
    if not Path(index_path).exists():
        return out
    for s in json.loads(Path(index_path).read_text()):
        d = key(s.get("school_website", ""))
        if not d:
            continue
        for pg in s.get("pages") or []:
            tp = pg.get("text_path")
            rp = pg.get("raw_html_path") or (tp[:-4] + ".html" if tp and tp.endswith(".txt") else None)
            if rp and (Path(raw_dir) / rp).exists():
                p, kind = Path(raw_dir) / rp, "html"
            elif tp and (Path(text_dir) / tp).exists():
                p, kind = Path(text_dir) / tp, "text"
            else:
                continue
            out.setdefault(d, []).append({"url": pg["url"], "path": str(p), "kind": kind, "type": pg.get("type", ""),
                                          "retrieved": _date(s.get("crawl_ts"), p), "cache": label})
    return out


def expansion_pages(log_path, html_dir, label, key):
    """domain -> home page from 09f's corpus expansion: raw HTML at html_dir/<slug>__index.html."""
    out = {}
    if not Path(log_path).exists():
        return out
    for line in Path(log_path).open():
        x = json.loads(line)
        slug = x["domain"].replace(".", "_").replace("-", "_")
        p = Path(html_dir) / f"{slug}__index.html"
        d = key("https://" + x["domain"])
        if d and p.exists() and x.get("status") == "done":
            out.setdefault(d, []).append({"url": x.get("final_url") or f"https://{x['domain']}/", "path": str(p),
                                          "kind": "html", "type": "home", "retrieved": _date(x.get("ts"), p),
                                          "cache": label})
    return out


def read_page(pg):
    raw = Path(pg["path"]).read_text(encoding="utf-8", errors="replace")
    return visible_text(raw) if pg["kind"] == "html" else raw


def pages_block(pages, caps):
    """The "--- Page: <url> (retrieved <date>) ---" block a prompt carries; caps[type] or caps['*']
    limits each page's characters. Returns (block, [(url, text)] actually sent)."""
    parts, sent = [], []
    for pg in pages:
        text = pg.get("text")
        if text is None:
            text = read_page(pg)
        if len(text.strip()) < 30:
            continue
        cap = caps.get(pg.get("type"), caps["*"])
        if len(text) > cap:
            text = text[:cap] + "\n[... truncated ...]"
        parts.append(f"--- Page: {pg['url']} (retrieved {pg['retrieved']}) ---\n{text}")
        sent.append((pg["url"], text))
    return "\n\n".join(parts), sent


def verify(value, url, sent, kind="text"):
    """(accepted_url or None, how): the value must appear on the page the model named; failing that, on
    another page sent in the same request (its URL is then the reference)."""
    if not value:
        return None, "empty"
    by_url = dict(sent)
    if url in by_url and on_page(value, by_url[url], kind):
        return url, "on_named_page"
    for u, t in sent:
        if on_page(value, t, kind):
            return u, "on_other_page"
    return None, "not_on_page"


STATES = {"alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
          "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
          "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
          "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
          "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
          "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
          "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
          "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
          "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
          "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC", "puerto rico": "PR",
          "guam": "GU", "virgin islands": "VI"}


# ── Prompt pieces shared by 60 (classify) and 61 (contact) ───────

SYSTEM = ("You read a business's own web pages and answer questions about it, only from what the pages say. "
          "When asked to copy a value, copy it exactly as it is written on the page, character for character, and "
          "give the URL from the '--- Page: <url> ---' header of the page it is on. Use an empty string when the "
          "pages do not give a value. Never guess, complete, reformat or combine values from different places.")

CONTACT_QUESTIONS = """\
- name: the business's own name as the pages write it (prefer the full name in the footer, copyright line, \
about text or page title over a slogan), and name_url.
- phone: the business's main phone number as written (not a fax), and phone_url.
- street_address: the business's street address as written, with city, state and ZIP when they are written \
next to it. A PO box only if no street address is given. And address_url.
- hours: the business's opening or office hours as written, and hours_url.
- city and state: the city and two-letter state of its main location, as the pages give them."""

CONTACT_PROPS = {k: {"type": "string"} for k in (
    "name", "name_url", "phone", "phone_url", "street_address", "address_url", "hours", "hours_url", "city", "state")}

CONTACT_FIELDS = [("name", "name_url", "text"), ("phone", "phone_url", "phone"),
                  ("street_address", "address_url", "text"), ("hours", "hours_url", "text")]


def schema(props):
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def params(user, props, effort, max_tokens=16000):
    return {"max_tokens": max_tokens, "system": SYSTEM, "messages": [{"role": "user", "content": user}],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema(props)}}}


def verify_contact(ans, sent):
    """Each contact value the model gave -> {value, ref, how} if it is on a page sent, else dropped with
    how='not_on_page'. City/state are accepted only if both appear on one page."""
    out = {}
    for f, uf, kind in CONTACT_FIELDS:
        v = (ans.get(f) or "").strip()
        ref, how = verify(v, ans.get(uf, ""), sent, kind)
        out[f] = {"value": v if ref else "", "ref": ref or "", "how": how, "claimed": v}
    city, st_raw = (ans.get("city") or "").strip(), (ans.get("state") or "").strip()
    st = STATES.get(st_raw.lower(), st_raw.upper().replace(".", ""))
    ok = ""
    if city and st in STATES.values():
        for u, t in sent:  # the state as the model wrote it ("CA", "S.C." or "Colorado") must be on the page
            if on_page(city, t) and on_page(st_raw, t):
                ok = u
                break
    out["city_state"] = {"value": f"{city}, {st}" if ok else "", "ref": ok, "city": city if ok else "",
                         "state": st if ok else ""}
    return out
