# -*- coding: utf-8 -*-
"""Google Places (New) — the competitors around each branch (E6 of the
expansion plan). Public listing data only: rating, number of ratings, price
level, distance. Once a month the fetcher looks up each branch's own listing
("โอชิเน <branch>"), then searches the buffet / shabu / Japanese keywords
around it and keeps every other restaurant within RADIUS_M. The snapshot
lands in feedback_competitor and the tab shows where each branch stands.
Config (backend/.feedback_config.json), radius_m/keywords chain-wide unless a
branch overrides them (in the app's ⚙ ตั้งค่า panel, or here directly):
  "places": {"api_key": "AIza…", "radius_m": 3000,
             "keywords": ["บุฟเฟ่ต์ญี่ปุ่น", "ชาบู", "หมูกระทะ", "ปิ้งย่าง"],
             "self_query": "โอชิเน {branch}",           # optional template
             "branches": {"ขอนแก่น": {"query": "Oshinei Khon Kaen",       # find_self() override
                                     "radius_m": 5000, "keywords": [...]}}}   # this branch only
Every call goes through the injected http(method, url, headers, body).
"""
import math

SOURCE = "Google Places"
API = "https://places.googleapis.com/v1/places:searchText"
FIELDS = "places.id,places.displayName,places.rating,places.userRatingCount,places.location,places.priceLevel,places.formattedAddress,places.primaryType"
DEFAULT_KEYWORDS = ["บุฟเฟ่ต์ญี่ปุ่น", "ชาบู", "หมูกระทะ", "ปิ้งย่าง", "buffet"]
RADIUS_M = 3000
OWN_MARKS = ("โอชิเน", "oshinei")
PRICE = {"PRICE_LEVEL_FREE": 0, "PRICE_LEVEL_INEXPENSIVE": 1, "PRICE_LEVEL_MODERATE": 2, "PRICE_LEVEL_EXPENSIVE": 3, "PRICE_LEVEL_VERY_EXPENSIVE": 4}


def haversine_m(lat1, lng1, lat2, lng2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _place(p: dict) -> dict:
    loc = p.get("location") or {}
    return {"place_id": p.get("id") or "", "name": ((p.get("displayName") or {}).get("text") or "").strip(),
            "rating": float(p["rating"]) if p.get("rating") is not None else None,
            "ratings_total": int(p.get("userRatingCount") or 0), "price_level": PRICE.get(p.get("priceLevel") or "", None),
            "lat": loc.get("latitude"), "lng": loc.get("longitude"), "address": p.get("formattedAddress") or "",
            "type": p.get("primaryType") or ""}


def text_search(query: str, api_key: str, http, center=None, radius_m: int = RADIUS_M, max_results: int = 20) -> list:
    body = {"textQuery": query, "languageCode": "th", "regionCode": "TH", "maxResultCount": max(1, min(max_results, 20))}
    if center:
        body["locationBias"] = {"circle": {"center": {"latitude": center[0], "longitude": center[1]}, "radius": float(radius_m)}}
    status, j = http("POST", API, {"Content-Type": "application/json", "X-Goog-Api-Key": api_key, "X-Goog-FieldMask": FIELDS}, body)
    if status >= 400:
        msg = (j or {}).get("error", {}).get("message") if isinstance(j, dict) else str(j)[:200]
        raise RuntimeError(f"Places search failed (HTTP {status}): {msg}")
    return [_place(p) for p in ((j or {}).get("places") or []) if isinstance(p, dict)]


def is_own(name: str) -> bool:
    n = (name or "").lower()
    return any(m in n for m in OWN_MARKS)


def find_self(branch: str, cfg: dict, http) -> dict:
    """The branch's own listing: the configured query, else 'โอชิเน <branch>'."""
    ov = ((cfg.get("branches") or {}).get(branch) or {})
    q = ov.get("query") or (cfg.get("self_query") or "โอชิเน {branch}").format(branch=branch)
    found = text_search(q, cfg["api_key"], http, max_results=5)
    own = [p for p in found if is_own(p["name"])] or found[:1]
    if not own or own[0].get("lat") is None:
        raise RuntimeError(f"own listing not found for {branch} (query {q!r})")
    return own[0]


def competitors_near(center, cfg: dict, http, branch: str = None) -> list:
    """radius_m/keywords: this branch's override (cfg["branches"][branch]) when
    it has one, else the config's chain-wide values, else the shipped defaults —
    same three-level fallback as find_self()'s query override."""
    ov = ((cfg.get("branches") or {}).get(branch) or {}) if branch else {}
    radius = int(ov.get("radius_m") or cfg.get("radius_m") or RADIUS_M)
    seen, out = set(), []
    for kw in (ov.get("keywords") or cfg.get("keywords") or DEFAULT_KEYWORDS):
        for p in text_search(kw, cfg["api_key"], http, center=center, radius_m=radius):
            if not p["place_id"] or p["place_id"] in seen or is_own(p["name"]) or p.get("lat") is None:
                continue
            d = haversine_m(center[0], center[1], p["lat"], p["lng"])
            if d > radius:
                continue
            seen.add(p["place_id"])
            p["distance_m"] = round(d)
            p["keyword"] = kw
            out.append(p)
    out.sort(key=lambda p: (-(p["ratings_total"] or 0), p["distance_m"]))
    return out


def snapshot(cfg: dict, http, branches: list, log=None) -> tuple:
    """One row per (branch, place) — the branch itself (is_self=1) and the
    competitors within the radius. Returns (rows, meta)."""
    log = log or (lambda *a: None)
    if not cfg.get("api_key"):
        raise RuntimeError("places config has no api_key")
    rows, meta = [], {"branches": [], "fetched": 0}
    for b in branches:
        try:
            me = find_self(b, cfg, http)
        except Exception as e:
            log(f"  {b}: {e}")
            meta["branches"].append({"branch": b, "error": str(e)[:160]})
            continue
        comps = competitors_near((me["lat"], me["lng"]), cfg, http, branch=b)
        rows.append({**me, "branch": b, "is_self": 1, "distance_m": 0, "keyword": ""})
        rows.extend({**c, "branch": b, "is_self": 0} for c in comps)
        meta["branches"].append({"branch": b, "self": me["name"], "competitors": len(comps)})
        meta["fetched"] += 1 + len(comps)
        log(f"  {b:<14} {me['name'][:30]:<30} ★{me['rating'] or '-'} ({me['ratings_total']}) · {len(comps)} competitor(s)")
    return rows, meta


__all__ = ["SOURCE", "snapshot", "text_search", "find_self", "competitors_near", "haversine_m", "is_own", "DEFAULT_KEYWORDS", "RADIUS_M"]
