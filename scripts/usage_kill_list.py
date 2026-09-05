#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The lean kill list, from what people actually opened (lean overhaul Phase 2).

Pulls the usage telemetry the live app has been counting since 2026-07-20
(Settings → 📈 การใช้งานจริง · `/api/usage/summary`), joins it with the tab
registry (`TABS`, `TAB_CLUSTERS`, `SOFT_KILL` in js/01-core.js), the route table
of the backend and the endpoints each tab's JS can reach, and writes the report
docs/LEAN_KILL_LIST_<date>.html: which tabs carry the app, which nobody opens,
which endpoints die with them, and the keep / merge / kill proposal for the
owner to sign. Re-run it on the review date to see whether the folded tabs
stayed at zero.

    py scripts/usage_kill_list.py                                # live app, 90 days → docs/
    py scripts/usage_kill_list.py --days 30 --md                 # markdown tables on stdout
    py scripts/usage_kill_list.py --from-json usage.json --out x.html   # offline

The endpoint reach map is static analysis of the JS call graph: an endpoint an
opened tab never called is "dormant", never "dead" — every route in the table
is still referenced by code (checked by grep 2026-09-05), so deletions follow
tabs, not this map alone.
"""
import argparse
import datetime as dt
import glob
import html
import json
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TELEMETRY_SINCE = "2026-07-20"          # the deploy that started counting (eecd402)
NEW_TABS = {"feedback": "2026-09-04", "checklist": "2026-09-04"}   # shipped inside the window — not judged yet
ALWAYS_HIDDEN = {"action": "ซ่อนจาก sidebar ตั้งแต่ 2026-07-20 (หน้าแรกทำหน้าที่แทน)",
                 "pnl": "ไม่มีรายการเมนูมาแต่เดิม (เข้าไม่ถึงจาก UI)"}
NOISE_API = {"GET /api/months", "GET /api/branches", "GET /api/auth/me", "POST /api/auth/login", "POST /api/auth/logout",
             "POST /api/_admin/seed-db", "GET /api/_admin/export-state", "POST /api/usage/tab", "GET /api/usage/summary"}


# ── inputs ───────────────────────────────────────────────────────────────────
def pull_usage(api: str, days: int) -> dict:
    import fetch_feedback as ff
    import refresh_fly_db as rf
    login = rf.get_login(types.SimpleNamespace(user=None, password=None))
    sink = ff.ApiSink(api, ff.resolve_token(None), login=login)
    out = {}
    for d in sorted({30, days}):
        out[str(d)] = sink._call("GET", f"/api/usage/summary?days={d}")
    return out


def js_registry() -> dict:
    src = (ROOT / "js" / "01-core.js").read_text(encoding="utf-8")
    tabs_src = re.search(r"const TABS = \{(.*?)\n\};", src, re.S).group(1)
    tabs = re.findall(r"^\s*([A-Za-z0-9_]+)\s*:\s*\{", tabs_src, re.M)
    clusters = {}
    for m in re.finditer(r"key:\s*'([a-z]+)',\s*label:\s*'([^']+)',\s*tabs:\s*\[(.*?)\]\s*\}", src):
        clusters[m.group(1)] = {"label": m.group(2), "tabs": re.findall(r"\['([a-z0-9]+)',", m.group(3))}
    titles = dict(re.findall(r"\b([A-Za-z0-9_]+):\s*'([^']*)'", re.search(r"const PAGE_TITLES = \{(.*?)\};", src, re.S).group(1)))
    kill_m = re.search(r"const SOFT_KILL = new Set\(\[(.*?)\]\)", src, re.S)
    soft_kill = re.findall(r"'([a-z0-9]+)'", kill_m.group(1)) if kill_m else []
    review = re.search(r"const KILL_REVIEW_DATE = '([0-9-]+)'", src)
    return {"tabs": tabs, "clusters": clusters, "titles": titles, "soft_kill": soft_kill,
            "review": review.group(1) if review else ""}


def routes() -> list:
    import main  # noqa: F401  (backend/ on sys.path)

    def walk(rs):
        for r in rs:
            n = type(r).__name__
            if n == "APIRoute":
                yield r
            elif n == "_IncludedRouter":
                yield from walk(r.original_router.routes)
            elif hasattr(r, "routes"):
                yield from walk(r.routes)
    return sorted({(sorted(r.methods)[0], r.path) for r in walk(main.app.routes) if r.path.startswith("/api/")})


API_RE = re.compile(r"/api/[A-Za-z0-9_\-/.{}$]+")
CALL_RE = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")


def _bodies(src: str) -> dict:
    """Top-level JS function bodies, brace-matched (functions and arrow consts)."""
    out = {}
    pats = (r"(?:^|\n)(?:async\s+)?function\s+([A-Za-z0-9_$]+)\s*\(",
            r"(?:^|\n)(?:const|let|var)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z0-9_$]+)\s*=>\s*\{")
    for pat in pats:
        for m in re.finditer(pat, src):
            name = m.group(1)
            if name in out:
                continue
            i = src.index("{", m.end() - 1)
            depth, j = 0, i
            while j < len(src):
                c = src[j]
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out[name] = src[i:j + 1]
    return out


def tab_reach(tabs: list) -> dict:
    """tab → the /api literals reachable from its loader (transitive) and its panel's onclicks."""
    src = "\n".join(p.read_text(encoding="utf-8") for p in sorted((ROOT / "js").glob("*.js")))
    pages = "\n".join(p.read_text(encoding="utf-8") for p in [ROOT / "index.html"] + sorted((ROOT / "tabs").glob("*.html")))
    funcs = _bodies(src)
    tabs_src = re.search(r"const TABS = \{(.*?)\n\};", src, re.S).group(1)
    entries = {m.group(1): m.group(2) for m in re.finditer(r"^\s*([A-Za-z0-9_]+)\s*:\s*\{(.*)\}\s*,?\s*$", tabs_src, re.M)}

    def reach(start):
        seen, stack = set(), [start]
        while stack:
            f = stack.pop()
            if f in seen or f not in funcs:
                continue
            seen.add(f)
            stack.extend(c for c in CALL_RE.findall(funcs[f]) if c in funcs and c not in seen)
        return seen
    out = {}
    for t in tabs:
        starts = set(CALL_RE.findall(entries.get(t, "")))
        panel = re.search(r'<div[^>]+id="tab-%s"[^>]*>(.*?)(?=<div[^>]+id="tab-[a-z0-9]+"|\Z)' % t, pages, re.S)
        if panel:
            starts.update(CALL_RE.findall(panel.group(1)))
        fset = set()
        for s in starts:
            fset |= reach(s)
        out[t] = sorted({a.rstrip(".").split("?")[0] for f in fset for a in API_RE.findall(funcs[f])})
    return out


def nonui_refs() -> dict:
    """/api literals in scripts, batch files and backend modules (machine callers)."""
    refs = {}
    files = [*glob.glob(str(ROOT / "scripts" / "*.py")), *glob.glob(str(ROOT / "backend" / "*.py")),
             *glob.glob(str(ROOT / "backend" / "*.bat")), *glob.glob(str(ROOT / "*.bat"))]
    for f in files:
        name = Path(f).name
        if name.startswith("test_") or name in ("main.py", "conftest.py"):
            continue
        s = Path(f).read_text(encoding="utf-8", errors="ignore")
        for m in API_RE.finditer(s):
            refs.setdefault(m.group(0).rstrip(".").split("?")[0], set()).add(str(Path(f).relative_to(ROOT)))
    return refs


def _pat(path):
    return re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path)) + "$")


def _matches(lit, path, p):
    if lit == path or p.match(lit):
        return True
    return "{" in path and lit.endswith("/") and lit == path.split("{", 1)[0]


# ── the join ─────────────────────────────────────────────────────────────────
def build(usage: dict, days: int, reg: dict, rts: list, reach: dict, refs: dict) -> dict:
    uN, u30 = usage[str(days)], usage["30"]
    th = {t["name"]: t for t in uN["tabs"]}
    th30 = {t["name"]: t for t in u30["tabs"]}
    ah = {t["name"]: t for t in uN["api"]}
    cluster_of = {t: k for k, c in reg["clusters"].items() for t in c["tabs"]}
    soft = set(reg["soft_kill"])
    tabs = []
    for t in reg["tabs"]:
        h, h30 = th.get(t), th30.get(t)
        hits, dseen = (h["hits"], h["days_seen"]) if h else (0, 0)
        if t in NEW_TABS:
            verdict, why = "new", f"ใหม่ {NEW_TABS[t]} — ยังไม่ตัดสิน"
        elif t in ALWAYS_HIDDEN:
            verdict, why = "kill", ALWAYS_HIDDEN[t]
        elif hits == 0:
            verdict, why = "kill", "0 ครั้งตลอดช่วงที่นับ"
        elif hits <= 2 and dseen <= 1:
            verdict, why = "merge", "เปิดวันเดียว — ยุบเข้าหน้าแม่"
        else:
            verdict, why = "keep", ""
        tabs.append({"tab": t, "title": reg["titles"].get(t, t), "cluster": cluster_of.get(t, "sidebar"),
                     "hits": hits, "days": dseen, "last": h["last_seen"] if h else "", "hits30": h30["hits"] if h30 else 0,
                     "verdict": verdict, "why": why, "folded": t in soft, "n_apis": len(reach.get(t, []))})
    kill_tabs = {t["tab"] for t in tabs if t["verdict"] == "kill"}
    eps = []
    for method, path in rts:
        key = f"{method} {path}"
        p = _pat(path)
        via = [t for t, apis in reach.items() if any(_matches(a, path, p) for a in apis)]
        machine = sorted({f for lit, fs in refs.items() if _matches(lit, path, p) for f in fs})
        h = ah.get(key)
        hits = h["hits"] if h else 0
        if hits:
            group = "used"
        elif via and set(via) <= kill_tabs and not machine:
            group = "dies_with_tab"
        elif via:
            group = "dormant"
        elif machine:
            group = "machine"
        else:
            group = "unmapped"
        eps.append({"key": key, "hits": hits, "days": h["days_seen"] if h else 0, "last": h["last_seen"] if h else "",
                    "via": via, "machine": machine, "group": group, "noise": key in NOISE_API})
    total_tab_hits = sum(t["hits"] for t in tabs)
    ranked = sorted((t for t in tabs if t["hits"]), key=lambda t: -t["hits"])
    top6 = sum(t["hits"] for t in ranked[:6])
    return {"days": days, "tabs": tabs, "eps": eps, "total_tab_hits": total_tab_hits, "top6_share": (100 * top6 / total_tab_hits) if total_tab_hits else 0,
            "active_days": max((t["days"] for t in tabs), default=0), "kill_tabs": sorted(kill_tabs),
            "should_fold": sorted(kill_tabs - soft - set(ALWAYS_HIDDEN)), "folded_but_used": sorted(t["tab"] for t in tabs if t["folded"] and t["hits"]),
            "review": reg["review"], "generated": dt.date.today().isoformat()}


# ── output ───────────────────────────────────────────────────────────────────
def markdown(r: dict) -> str:
    out = [f"## แท็บ ({r['days']} วัน · เปิดรวม {r['total_tab_hits']} ครั้ง · วันที่มีการใช้ {r['active_days']})", "",
           "| แท็บ | หมวด | ครั้ง | วัน | ล่าสุด | ผล |", "|---|---|---:|---:|---|---|"]
    for t in sorted(r["tabs"], key=lambda t: (-t["hits"], t["tab"])):
        out.append(f"| {t['tab']} | {t['cluster']} | {t['hits']} | {t['days']} | {t['last']} | {t['verdict']}{' (folded)' if t['folded'] else ''} |")
    out += ["", "## endpoints ที่ 0 ครั้ง", ""]
    for g in ("dies_with_tab", "dormant", "machine", "unmapped"):
        rows = [e for e in r["eps"] if e["group"] == g]
        out.append(f"### {g} ({len(rows)})")
        out += [f"- `{e['key']}` ← {', '.join(e['via']) or ', '.join(e['machine'])}" for e in rows]
        out.append("")
    if r["should_fold"]:
        out.append(f"⚠ ควรอยู่ใน SOFT_KILL แต่ยังไม่ได้ซ่อน: {', '.join(r['should_fold'])}")
    if r["folded_but_used"]:
        out.append(f"⚠ ซ่อนอยู่แต่มีคนเปิด: {', '.join(r['folded_but_used'])}")
    return "\n".join(out)


CSS = """
:root{--ink:#1f2937;--muted:#6b7280;--line:#e5e7eb;--soft:#f8fafc;--teal:#0f766e;--red:#b91c1c;--amber:#b45309;--green:#15803d}
*{box-sizing:border-box}body{margin:0;background:#fff;color:var(--ink);font:14px/1.6 'Segoe UI',Tahoma,system-ui,sans-serif}
main{max-width:1080px;margin:0 auto;padding:32px 28px 64px}h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:36px 0 10px;padding-bottom:6px;border-bottom:2px solid var(--teal)}
h3{font-size:15px;margin:22px 0 8px}.sub{color:var(--muted);margin-bottom:22px}.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:18px 0}
.kpi{background:var(--soft);border:1px solid var(--line);border-radius:10px;padding:12px 14px}.kpi b{display:block;font-size:26px;line-height:1.1}.kpi span{color:var(--muted);font-size:12px}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}th,td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{background:var(--soft);font-weight:600;color:#374151}td.n,th.n{text-align:right}tr.kill td{color:var(--red)}tr.merge td{color:var(--amber)}tr.new td{color:var(--muted);font-style:italic}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:600}.keep{background:#dcfce7;color:var(--green)}.merge{background:#fef3c7;color:var(--amber)}.kill{background:#fee2e2;color:var(--red)}.new{background:#e5e7eb;color:#374151}
code{font-family:Consolas,monospace;font-size:12px;background:var(--soft);padding:0 4px;border-radius:4px}.note{background:#fffbeb;border-left:4px solid var(--amber);padding:10px 14px;margin:14px 0;border-radius:0 8px 8px 0}
.wrap{overflow-x:auto}ul{padding-left:20px}li{margin:3px 0}.sign td{height:44px}@media print{main{padding:0}h2{page-break-after:avoid}tr{page-break-inside:avoid}}
"""


def render_html(r: dict) -> str:
    e = html.escape
    tabs = sorted(r["tabs"], key=lambda t: (-t["hits"], t["tab"]))
    by_v = {v: [t for t in r["tabs"] if t["verdict"] == v] for v in ("keep", "merge", "kill", "new")}
    eps = {g: [x for x in r["eps"] if x["group"] == g] for g in ("used", "dies_with_tab", "dormant", "machine", "unmapped")}
    used_real = [x for x in eps["used"] if not x["noise"]]
    head = [t for t in tabs if t["hits"]][:6]

    def tab_row(t):
        return (f"<tr class='{t['verdict']}'><td>{e(t['tab'])}<br><span style='color:var(--muted);font-size:11px'>{e(t['title'])}</span></td>"
                f"<td>{e(t['cluster'])}</td><td class='n'>{t['hits']}</td><td class='n'>{t['days']}</td><td class='n'>{t['hits30']}</td><td>{e(t['last'])}</td>"
                f"<td><span class='tag {t['verdict']}'>{t['verdict'].upper()}</span>{' · ซ่อนแล้ว' if t['folded'] else ''}</td><td>{e(t['why'])}</td></tr>")

    def ep_rows(rows):
        return "".join(f"<tr><td><code>{e(x['key'])}</code></td><td>{e(', '.join(x['via']) or ', '.join(x['machine']))}</td></tr>" for x in rows)
    parts = [f"<!doctype html><html lang='th'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Lean Kill List {r['generated'][:7]}</title><style>{CSS}</style></head><body><main>",
             f"<h1>Lean Kill List — แท็บที่ใช้จริง vs แท็บที่ไม่มีใครเปิด</h1>",
             f"<div class='sub'>ข้อมูลการใช้งานจริง {e(TELEMETRY_SINCE)} → {e(r['generated'])} ({r['days']} วันย้อนหลัง) · สร้างโดย <code>scripts/usage_kill_list.py</code> · แผนแม่: docs/LEAN_OVERHAUL_PLAN.md Phase 2</div>",
             "<div class='kpis'>",
             f"<div class='kpi'><b>{len(r['tabs'])}</b><span>แท็บทั้งหมด</span></div>",
             f"<div class='kpi'><b>{sum(1 for t in r['tabs'] if t['hits'])}</b><span>แท็บที่ถูกเปิดอย่างน้อย 1 ครั้ง</span></div>",
             f"<div class='kpi'><b>{r['total_tab_hits']}</b><span>ครั้งที่เปิดแท็บรวม · ใช้งาน {r['active_days']} วัน</span></div>",
             f"<div class='kpi'><b>{r['top6_share']:.0f}%</b><span>ของการเปิดทั้งหมดอยู่ใน 6 แท็บ</span></div>",
             f"<div class='kpi'><b>{len(by_v['kill'])}</b><span>แท็บที่ไม่มีใครเปิดเลย → kill list</span></div>",
             f"<div class='kpi'><b>{len(eps['dies_with_tab'])}</b><span>endpoints ที่หายไปพร้อมแท็บเหล่านั้น</span></div>",
             "</div>",
             "<h2>1) สรุป</h2><ul>",
             f"<li><b>แอปที่ใช้จริงคือ 6 หน้า:</b> {e(' · '.join(f'{t['tab']} ({t['hits']})' for t in head))} — รวม {r['top6_share']:.0f}% ของการเปิดแท็บทั้งหมด</li>",
             f"<li><b>{len(by_v['keep'])} แท็บ KEEP</b> (เปิดซ้ำหลายวัน) · <b>{len(by_v['merge'])} แท็บ MERGE</b> (เปิดวันเดียวครั้งเดียว — คงเนื้อหาไว้เป็นส่วนหนึ่งของหน้าแม่) · <b>{len(by_v['kill'])} แท็บ KILL</b> (0 ครั้งตลอด {r['days']} วัน) · {len(by_v['new'])} แท็บใหม่ยังไม่นับ</li>",
             f"<li><b>ขั้นที่ 1 (ทำแล้ววันนี้ ย้อนกลับได้):</b> แท็บใน kill list ถูกพับไว้หลังปุ่ม <b>⋯</b> ในแถบมุมมองย่อยของหมวด — โค้ด/ข้อมูลอยู่ครบ เปิดได้ด้วยคลิกเดียว bookmark เดิมใช้ได้</li>",
             f"<li><b>ขั้นที่ 2 (ทบทวน {e(r['review'])}):</b> แท็บที่ยังเป็น 0 → ลบแท็บ + JS + endpoints ที่มีแต่แท็บนั้นเรียก ({len(eps['dies_with_tab'])} เส้น ตารางข้อ 4) · แท็บ MERGE ตัดสินทีละแท็บ</li>",
             "</ul>",
             "<div class='note'><b>ข้อควรระวังในการอ่านตัวเลข</b> — (1) นับเฉพาะการเปิดแท็บผ่านเมนู/แถบมุมมองย่อย (beacon ใน switchTab) จึงไม่เห็นการเปิดจากหน้า mobile ที่ไม่ผ่าน switchTab · "
             "(2) การเปิดวันที่ 2026-07-20/22 และ 2026-09-05 บางส่วนเป็นการทดสอบหลัง deploy ของผู้พัฒนา ไม่ใช่เจ้าของ (แท็บ report, pnlbranch, ops, cashctl 1 ครั้ง; feedback/checklist endpoints) · "
             "(3) แท็บที่ผู้ใช้เปิดผ่านหน้าอื่นโดยไม่สลับแท็บ (เช่น drill modal) ถูกนับที่ endpoint ไม่ใช่ที่แท็บ</div>",
             "<h2>2) แท็บทั้งหมด เรียงตามการใช้งาน</h2><div class='wrap'><table><thead><tr><th>แท็บ</th><th>หมวด</th><th class='n'>ครั้ง</th><th class='n'>วันที่เปิด</th><th class='n'>30 วันล่าสุด</th><th>เปิดล่าสุด</th><th>ผล</th><th>เหตุผล</th></tr></thead><tbody>",
             "".join(tab_row(t) for t in tabs), "</tbody></table></div>",
             "<h2>3) ข้อเสนอ keep / merge / kill</h2>",
             "<h3>KEEP — คงไว้ตามเดิม</h3><p>" + e(", ".join(t["tab"] for t in by_v["keep"])) + "</p>",
             "<h3>MERGE — เปิดครั้งเดียววันเดียว: เก็บเนื้อหาไว้เป็นส่วนย่อยของหน้าแม่ แล้วเอาแท็บออก</h3><ul>",
             "".join(f"<li><b>{e(t['tab'])}</b> ({e(t['title'])}) — หมวด {e(t['cluster'])} · เปิด {t['last']}</li>" for t in by_v["merge"]), "</ul>",
             f"<h3>KILL — พับไว้แล้ว รอยืนยัน {e(r['review'])}</h3><ul>",
             "".join(f"<li><b>{e(t['tab'])}</b> ({e(t['title'])}) — {e(t['why'])}</li>" for t in by_v["kill"]), "</ul>",
             f"<h2>4) Endpoints — {len(r['eps'])} เส้น · ถูกเรียก {len(eps['used'])} (ไม่นับ login/months/branches/sync = {len(used_real)}) · 0 ครั้ง {len(r['eps']) - len(eps['used'])}</h2>",
             f"<h3>4.1 หายไปพร้อมแท็บ kill ({len(eps['dies_with_tab'])}) — ลบในขั้นที่ 2</h3><div class='wrap'><table><thead><tr><th>endpoint</th><th>เรียกจากแท็บ</th></tr></thead><tbody>{ep_rows(eps['dies_with_tab'])}</tbody></table></div>",
             f"<h3>4.2 อยู่ในแท็บที่ใช้ แต่ไม่มีใครกด ({len(eps['dormant'])}) — ฟีเจอร์ย่อย/ปุ่ม export/ตั้งค่า · คงไว้ (ลบเมื่อยกเครื่องแท็บนั้น)</h3><div class='wrap'><table><thead><tr><th>endpoint</th><th>เรียกจากแท็บ</th></tr></thead><tbody>{ep_rows(eps['dormant'])}</tbody></table></div>",
             f"<h3>4.3 เครื่องเรียก ไม่ใช่คน ({len(eps['machine'])}) — sync/backup/fetcher/webhook · ห้ามลบ</h3><div class='wrap'><table><thead><tr><th>endpoint</th><th>ผู้เรียก</th></tr></thead><tbody>{ep_rows(eps['machine'])}</tbody></table></div>",
             f"<h3>4.4 แผนที่ static หาไม่เจอ ({len(eps['unmapped'])}) — ตรวจด้วย grep ก่อนตัดสิน (2026-09-05: ทุกเส้นยังถูกอ้างในโค้ด)</h3><div class='wrap'><table><thead><tr><th>endpoint</th><th></th></tr></thead><tbody>{ep_rows(eps['unmapped'])}</tbody></table></div>",
             "<h3>4.5 Top endpoints ที่ใช้จริง</h3><div class='wrap'><table><thead><tr><th>endpoint</th><th class='n'>ครั้ง</th><th class='n'>วัน</th><th>ล่าสุด</th></tr></thead><tbody>",
             "".join(f"<tr><td><code>{e(x['key'])}</code></td><td class='n'>{x['hits']}</td><td class='n'>{x['days']}</td><td>{e(x['last'])}</td></tr>" for x in sorted(used_real, key=lambda x: -x['hits'])[:25]),
             "</tbody></table></div>",
             "<h2>5) ขั้นตอนถัดไป</h2><ol>",
             "<li>เจ้าของทบทวนตาราง 3) — ถ้าแท็บใด KILL แต่ยังต้องการ ให้เปิดใช้ 1 ครั้ง (ตัวนับจะบันทึก) หรือแจ้งเอาออกจาก kill list</li>",
             f"<li>{e(r['review'])}: รัน <code>py scripts/usage_kill_list.py</code> อีกครั้ง — แท็บที่ยัง 0 ลบจริงพร้อม endpoints ข้อ 4.1 (PR เดียว มี golden test คุม), อัปเดต RBAC/PAGE_TITLES/คู่มือ</li>",
             "<li>แท็บ MERGE: ยุบทีละหมวด (บุฟเฟ่ต์ → buffetperf, กำไร → gpbranch, ปฏิบัติการ → opsreport) หลังลบขั้นที่ 2 เสร็จ</li></ol>",
             "<h2>6) ลงชื่อรับทราบ</h2><table class='sign'><thead><tr><th>รายการ</th><th>เจ้าของ</th><th>วันที่</th><th>หมายเหตุ</th></tr></thead><tbody>",
             "<tr><td>ขั้นที่ 1 — พับแท็บ kill list</td><td></td><td></td><td></td></tr><tr><td>ขั้นที่ 2 — ลบจริง</td><td></td><td></td><td></td></tr><tr><td>MERGE — ยุบหมวด</td><td></td><td></td><td></td></tr>",
             "</tbody></table></main></body></html>"]
    return "\n".join(parts)


def main(argv=None) -> int:
    import fetch_feedback as ff
    ap = argparse.ArgumentParser(description="usage telemetry → lean kill list report")
    ap.add_argument("--api", default=ff.DEFAULT_API)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--from-json", help="a saved /api/usage/summary bundle {days: summary} instead of pulling")
    ap.add_argument("--save-json", help="write the pulled bundle here")
    ap.add_argument("--out", help=f"HTML report path (default docs/LEAN_KILL_LIST_<YYYY-MM>.html)")
    ap.add_argument("--md", action="store_true", help="print markdown tables instead of writing HTML")
    args = ap.parse_args(argv)
    usage = json.load(open(args.from_json, encoding="utf-8")) if args.from_json else pull_usage(args.api, args.days)
    if args.save_json:
        Path(args.save_json).write_text(json.dumps(usage, ensure_ascii=False, indent=1), encoding="utf-8")
    reg = js_registry()
    r = build(usage, args.days, reg, routes(), tab_reach(reg["tabs"]), nonui_refs())
    if args.md:
        print(markdown(r))
        return 0
    out = Path(args.out) if args.out else ROOT / "docs" / f"LEAN_KILL_LIST_{r['generated'][:7]}.html"
    out.write_text(render_html(r), encoding="utf-8")
    print(f"✓ {out} — tabs {len(r['tabs'])}, opened {sum(1 for t in r['tabs'] if t['hits'])}, kill {len(r['kill_tabs'])}, "
          f"endpoints {len(r['eps'])} (0-hit {sum(1 for x in r['eps'] if not x['hits'])}, die with tabs {sum(1 for x in r['eps'] if x['group'] == 'dies_with_tab')})")
    if r["should_fold"]:
        print(f"⚠ in the kill list but not folded (add to SOFT_KILL): {', '.join(r['should_fold'])}")
    if r["folded_but_used"]:
        print(f"⚠ folded but opened since: {', '.join(r['folded_but_used'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
