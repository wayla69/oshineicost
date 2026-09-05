# -*- coding: utf-8 -*-
"""Mentions from RSS / Atom feeds — the legitimate "anywhere on the web" signal:
  • Google News search for the brand keywords (built in, no setup),
  • Google Alerts feeds (the owner creates an alert with "Deliver to: RSS
    feed" and pastes its URL — blogs, forums, news, anything Google indexes),
  • any other feed (a YouTube channel feed, a Pantip tag feed if one exists…).

Config (backend/.feedback_config.json → "web"):
    {"keywords": ["โอชิเน", "oshinei"],        # one Google News search feed per keyword
     "google_news": true,
     "feeds": [{"url": "https://www.google.com/alerts/feeds/…", "name": "Google Alerts โอชิเน", "source": "Web"}]}
Every item becomes a row of kind "mention": title + summary as the text, the
article link as url, the branch detected from the text on the server. Nothing
here can be replied to.
"""
import hashlib
import html
import re
import urllib.parse
import xml.etree.ElementTree as ET

from feedback_sources.google_gbp import thai_date

SOURCE = "Web"
GOOGLE_NEWS = "https://news.google.com/rss/search?q={q}&hl=th&gl=TH&ceid=TH:th"
DEFAULT_KEYWORDS = ["โอชิเน", "oshinei"]
_ATOM = "{http://www.w3.org/2005/Atom}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_TAG = re.compile(r"<[^>]+>")


def strip_html(s) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", s or ""))).strip()


def clean_link(link: str) -> str:
    """Google Alerts wraps every link in a google.com/url?…&url=REAL redirect."""
    link = (link or "").strip()
    if "google.com/url?" in link:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
        if q.get("url"):
            return q["url"][0]
    return link


def parse_feed(text) -> list:
    """RSS 2.0 or Atom → [{id, title, summary, link, date}] (empty on anything unparseable)."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if not isinstance(text, str) or not text.strip():
        return []
    try:
        root = ET.fromstring(text.strip())
    except ET.ParseError:
        return []
    items = []
    if root.tag == _ATOM + "feed":
        for e in root.findall(_ATOM + "entry"):
            link = ""
            for ln in e.findall(_ATOM + "link"):
                if ln.get("rel") in (None, "alternate"):
                    link = ln.get("href") or ""
                    break
            items.append({"id": (e.findtext(_ATOM + "id") or link or "").strip(),
                          "title": strip_html(e.findtext(_ATOM + "title") or ""),
                          "summary": strip_html(e.findtext(_ATOM + "summary") or e.findtext(_ATOM + "content") or ""),
                          "link": link.strip(),
                          "date": (e.findtext(_ATOM + "published") or e.findtext(_ATOM + "updated") or "").strip()})
    else:
        for it in root.iter("item"):
            items.append({"id": (it.findtext("guid") or it.findtext("link") or "").strip(),
                          "title": strip_html(it.findtext("title") or ""),
                          "summary": strip_html(it.findtext("description") or ""),
                          "link": (it.findtext("link") or "").strip(),
                          "date": (it.findtext("pubDate") or it.findtext(_DC + "date") or "").strip()})
    return items


def item_to_row(item: dict, source: str = SOURCE, feed_name: str = ""):
    title, summary = item.get("title") or "", item.get("summary") or ""
    text = title
    if summary and summary != title:
        text = f"{title} — {summary[:400]}" if title else summary[:400]
    if not text:
        return None
    link = clean_link(item.get("link") or "")
    ext = hashlib.sha1((item.get("id") or link or text).encode("utf-8")).hexdigest()[:24]
    return {"date": thai_date(item.get("date")), "branch": "", "source": source, "rating": None, "text": text,
            "external_id": f"item:{ext}", "url": link, "author": "", "reply": "", "replied_at": "",
            "location": feed_name, "kind": "mention", "detect_branch": True}


def feeds_for(cfg: dict) -> list:
    out = []
    if cfg.get("google_news", True):
        for kw in cfg.get("keywords") or DEFAULT_KEYWORDS:
            out.append({"url": GOOGLE_NEWS.format(q=urllib.parse.quote(kw)), "name": f"Google News: {kw}", "source": "Google News"})
    for f in cfg.get("feeds") or []:
        if isinstance(f, dict) and f.get("url"):
            out.append({"url": f["url"], "name": f.get("name") or f["url"], "source": f.get("source") or SOURCE})
        elif isinstance(f, str) and f.strip():
            out.append({"url": f.strip(), "name": f.strip(), "source": SOURCE})
    return out


def fetch_rows(cfg: dict, http, since: str = "", log=None) -> tuple:
    """Every feed → (rows, {feeds: [{url, name, source, items}], fetched})."""
    log = log or (lambda *a: None)
    feeds = feeds_for(cfg)
    if not feeds:
        raise RuntimeError("web config has neither keywords nor feeds")
    rows, meta, seen = [], {"feeds": [], "fetched": 0}, set()
    for f in feeds:
        status, body = http("GET", f["url"], {"User-Agent": "Mozilla/5.0 (Oshinei feedback fetcher)"}, None)
        if status >= 400:
            raise RuntimeError(f"feed {f['name']} failed (HTTP {status})")
        n = 0
        for item in parse_feed(body):
            row = item_to_row(item, f["source"], f["name"])
            if not row or row["external_id"] in seen:
                continue
            if since and row["date"] and row["date"] < since:
                continue
            seen.add(row["external_id"])
            rows.append(row)
            n += 1
        meta["feeds"].append({**f, "items": n})
        meta["fetched"] += n
        log(f"  {f['name'][:40]:<40} {n:>4} item(s)")
    return rows, meta


__all__ = ["parse_feed", "item_to_row", "feeds_for", "fetch_rows", "clean_link", "strip_html", "SOURCE"]
