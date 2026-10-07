# -*- coding: utf-8 -*-
"""Code check for the legacy recipes (bom_ingredients) against the ingredient master.

The legacy recipes and the accounting/DATAP master grew up with different
numbering in some families: in the old recipes CK-001 is "น้ำซุปใส" and V-024 is
"หัวไชเท้าหั่นฝอย", while in DATAP CK-001 is a rice item and V-024 is a lime.
A code that exists in both but names different things silently orders/prices
the wrong ingredient. Worse, the old recipes reuse one code under several names
(CK-001 is rice in 113 dishes and น้ำซุปใส in 2), so everything here is judged
per (code, name). This module lists, per code the legacy recipes use:

  missing   — not in the master at all (seeding the master adds it)
  conflict  — in the master, but the master's name does not look like the
              recipe's name (suggests the master code whose name matches best)
  same      — the analyst confirmed it is the same item (names just differ)
  remap     — the analyst decided the legacy code really means `use_code`

Decisions live in bc_code_review (site-owned, survives the daily swap). A remap
is applied only where legacy recipes are read for planning (forecast/order
plan) — released and historical costs never change because of it.
"""
import re
from difflib import SequenceMatcher

from .master import audit, now

DDL = """CREATE TABLE IF NOT EXISTS bc_code_review(
    code TEXT NOT NULL, recipe_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('same','remap')),
    use_code TEXT, note TEXT, who TEXT, at TEXT,
    PRIMARY KEY (code, recipe_name))"""

# words that describe the cut/prep or the pack, not the item — "หัวไชเท้าหั่นฝอย" is still หัวไชเท้า
_PREP = ("หั่นเต๋า", "หั่นเส้น", "หั่นฝอย", "หั่นครึ่ง", "หั่น", "ซอย", "สไลด์", "ลวก", "ต้มสุก", "ทอดกรอบ",
         "สับ", "ฝอย", "ชิ้นละ", "ชิ้น", "ใช้", "แท่งละ", "แท่ง", "ถุงใหญ่", "ถุง", "กก", "กรัม", "มล",
         "ลิตร", "ตรา", "แพ็ค")
_GENERIC = ("ซอส", "น้ำ", "ผง", "เส้น", "แผ่น", "ปลา", "เนื้อ")
_STRIP = re.compile(r"[\s()\[\]\-/.,:;+*%&'\"0-9a-zA-Z@#]")


def key(name: str) -> str:
    """Thai letters only, prep/pack words removed."""
    s = _STRIP.sub("", (name or "").replace("ํา", "ำ"))   # งาดํา (nikhahit+sara aa) = งาดำ
    for w in _PREP:
        s = s.replace(w, "")
    return s


def _core(k: str) -> str:
    for w in _GENERIC:
        k = k.replace(w, "")
    return k


def _lcs(a: str, b: str) -> int:
    if not a or not b:
        return 0
    return SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b)).size


def same_item(recipe_name: str, master_name: str) -> bool:
    """Loose: one name contains the other, or they share a 4-letter run outside generic words."""
    a, b = key(recipe_name), key(master_name)
    if not a or not b:
        return True                     # nothing to compare — don't cry wolf
    if a in b or b in a:
        return True
    ca, cb = _core(a), _core(b)
    if ca and cb and min(len(ca), len(cb)) >= 3 and (ca in cb or cb in ca):
        return True
    return _lcs(ca, cb) >= 4 or SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.6


def _score(rk: str, name: str) -> float:
    b = key(name)
    if not rk or not b:
        return 0.0
    if rk in b or b in rk:
        # containment: the closer the lengths the better — หัวไชเท้า beats หัวไชเท้าแดง
        return 1.0 + 0.5 * min(len(rk), len(b)) / max(len(rk), len(b))
    cr = _core(rk)
    return max(SequenceMatcher(None, rk, b, autojunk=False).ratio(), _lcs(cr, _core(b)) / max(len(cr), 1))


def suggest(master: dict, recipe_name: str, exclude: str = None, n: int = 3) -> list:
    """Best master codes for a recipe name: [{code, name, score}]."""
    rk = key(recipe_name)
    scored = []
    for code, (name, _kind) in master.items():
        if code == exclude:
            continue
        s = _score(rk, name or "")
        if s >= 0.65:
            scored.append((s, code, name))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [{"code": c, "name": nm, "score": round(s, 2)} for s, c, nm in scored[:n]]


def ensure(con):
    con.execute(DDL)


def remaps(con) -> dict:
    """{(legacy_code, recipe_name): use_code} — the analyst's "this line really means X".

    Keyed by the line's NAME too: the old recipes reuse one code for different
    things (CK-001 is rice in 113 dishes and น้ำซุปใส in 2), so only the lines
    whose name was judged are moved. Planning only — costs never read this."""
    try:
        return {(c, n): u for c, n, u in con.execute(
            "SELECT code, recipe_name, use_code FROM bc_code_review"
            " WHERE status='remap' AND COALESCE(use_code,'')<>''")}
    except Exception:
        return {}


def _lines(con) -> list:
    """[(code, name, unit, dishes)] per distinct (code, name) in the old recipes."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='bom_ingredients'").fetchone():
        return []
    return con.execute("""
        SELECT TRIM(ingredient_code), COALESCE(ingredient_name,''), MAX(unit), COUNT(DISTINCT sku_id)
          FROM bom_ingredients WHERE COALESCE(TRIM(ingredient_code),'')<>'' AND ingredient_code<>'#N/A'
         GROUP BY 1, 2""").fetchall()


def review(con, with_ok: bool = False, suggestions: bool = True) -> dict:
    """One row per code the old recipes use, with every name it is used under.
    suggestions=False skips the fuzzy matching (the counts alone, for หน้างาน).

    A name is: ok (looks like the master's item) / conflict (looks like another
    item) / missing (code not in the master) / same / remap (the analyst decided).
    A code is a conflict while any of its names is an undecided conflict."""
    ensure(con)
    master = {c: (n, k) for c, n, k in con.execute("SELECT code, name_th, kind FROM bc_ingredient")}
    decided = {(r[0], r[1]): {"status": r[2], "use_code": r[3], "note": r[4], "who": r[5], "at": r[6]}
               for r in con.execute("SELECT code, recipe_name, status, use_code, note, who, at FROM bc_code_review")}
    lines = _lines(con)
    dishes_per_code = dict(con.execute(
        "SELECT TRIM(ingredient_code), COUNT(DISTINCT sku_id) FROM bom_ingredients"
        " WHERE COALESCE(TRIM(ingredient_code),'')<>'' GROUP BY 1")) if lines else {}
    by_code = {}
    for code, name, unit, dishes in lines:
        m = master.get(code)
        d = decided.get((code, name))
        if d:
            st = d["status"]
        elif m is None:
            st = "missing"
        elif same_item(name, m[0]):
            st = "ok"
        else:
            st = "conflict"
        nm = {"name": name, "unit": unit, "dishes": dishes, "status": st}
        if d:
            nm.update({"use_code": d["use_code"], "use_name": (master.get(d["use_code"]) or (None,))[0],
                       "note": d["note"], "who": d["who"], "at": d["at"]})
        by_code.setdefault(code, []).append(nm)
    rows, counts = [], {"missing": 0, "conflict": 0, "same": 0, "remap": 0, "ok": 0}
    sugg_cache = {}
    for code, names in by_code.items():
        names.sort(key=lambda n: (-n["dishes"], n["name"]))
        sts = {n["status"] for n in names}
        status = next(s for s in ("conflict", "missing", "remap", "same", "ok") if s in sts)
        counts[status] += 1
        if status == "ok" and not with_ok:
            continue
        m = master.get(code)
        todo = [n for n in names if n["status"] in ("conflict", "missing")]
        row = {"code": code, "recipe_name": names[0]["name"], "unit": names[0]["unit"],
               "dishes": dishes_per_code.get(code, sum(n["dishes"] for n in names)), "status": status,
               "master_name": m[0] if m else None, "master_kind": m[1] if m else None, "names": names,
               "todo_names": [n["name"] for n in todo], "todo_dishes": sum(n["dishes"] for n in todo)}
        if todo and master and suggestions:
            # one code's wrong names can be different items (F-003: เนยจืด and สะโพกไก่) — group
            # them by the master code each one points to, so every group gets its own decision
            groups = {}
            for n in todo:
                k = key(n["name"])
                if k not in sugg_cache:
                    sugg_cache[k] = suggest(master, n["name"], exclude=code)
                sg = sugg_cache[k]
                g = groups.setdefault(sg[0]["code"] if sg else None, {"names": [], "dishes": 0, "suggest": sg})
                g["names"].append(n["name"])
                g["dishes"] += n["dishes"]
            row["groups"] = sorted(groups.values(), key=lambda g: -g["dishes"])
            row["suggest"] = row["groups"][0]["suggest"]
        rows.append(row)
    order = {"conflict": 0, "missing": 1, "remap": 2, "same": 3, "ok": 4}
    rows.sort(key=lambda r: (order[r["status"]], -(r["todo_dishes"] or r["dishes"] or 0), r["code"]))
    return {"master_size": len(master), "used": len(by_code), "counts": counts, "rows": rows}


def decide(con, code: str, status: str, use_code: str = None, note: str = None, who: str = "-",
           names: list = None) -> dict:
    """Record the analyst's decision for the names of `code` that still need one
    (default), or for `names`. status 'clear' removes the code's decisions (or `names`')."""
    ensure(con)
    code = (code or "").strip()
    if not code:
        raise ValueError("ต้องระบุรหัส")
    if status == "clear":
        q, args = "DELETE FROM bc_code_review WHERE code=?", [code]
        if names:
            q += f" AND recipe_name IN ({','.join('?' * len(names))})"
            args += list(names)
        n = con.execute(q, args).rowcount
        audit(con, who, "code_review_clear", "bc_code_review", code, names, None)
        return {"code": code, "status": None, "names": n}
    if status not in ("same", "remap"):
        raise ValueError("สถานะต้องเป็น same / remap / clear")
    use_code = (use_code or "").strip() or None
    if status == "remap":
        if not use_code:
            raise ValueError("ต้องเลือกรหัสที่จะใช้แทน")
        if use_code == code:
            raise ValueError("รหัสที่ใช้แทนต้องไม่ใช่รหัสเดิม")
        if not con.execute("SELECT 1 FROM bc_ingredient WHERE code=?", (use_code,)).fetchone():
            raise ValueError(f"ไม่พบรหัส {use_code} ในทะเบียน")
    else:
        use_code = None
    if not names:
        row = next((r for r in review(con)["rows"] if r["code"] == code), None)
        names = (row or {}).get("todo_names") or []
    if not names:
        raise ValueError(f"รหัส {code} ไม่มีชื่อที่ต้องตรวจสอบ")
    t = now()
    for nm in names:
        con.execute("INSERT INTO bc_code_review(code, recipe_name, status, use_code, note, who, at)"
                    " VALUES(?,?,?,?,?,?,?) ON CONFLICT(code, recipe_name) DO UPDATE SET status=excluded.status,"
                    " use_code=excluded.use_code, note=excluded.note, who=excluded.who, at=excluded.at",
                    (code, nm, status, use_code, (note or "").strip() or None, who, t))
    audit(con, who, "code_review", "bc_code_review", code, None, {"status": status, "use_code": use_code,
                                                                   "names": names})
    return {"code": code, "status": status, "use_code": use_code, "names": names}
