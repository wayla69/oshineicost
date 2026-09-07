# -*- coding: utf-8 -*-
"""📰 รายงานประจำวัน/สัปดาห์/เดือน — เสียงลูกค้า, ออกแบบให้ทีมการตลาดใช้ได้ทันที:
เลือกช่วง (วันเดียว, สัปดาห์, เดือน หรือกำหนดเอง — และสาขาถ้าต้องการ) แล้วได้
สรุปพร้อมใช้ — คะแนน/สัดส่วนความรู้สึกของช่วงนั้น กี่รายการตอบแล้ว/รอตอบ/เกิน
SLA เคสร้ายแรงที่ต้องรู้ หัวข้อ/เมนูที่ถูกพูดถึง เสียงชมที่หยิบไปใช้ต่อได้ และ
รายชื่อที่ต้องติดตาม (รวมเบอร์โทรกลับจาก QR — ดู js/11-feedback.js:_fbContactHtml
สำหรับ tel: link ฝั่งแอป).

_aggregate() (routers/feedback.py) คำนวณให้ทุกอย่างอยู่แล้วสำหรับ /api/feedback/
summary — ที่นี่แค่เป็นหน้าต่าง [date_from, date_to] ของฟังก์ชันเดียวกัน ไม่ใช่
โมเดลใหม่ จึงรับประกันว่าตัวเลขตรงกับที่แท็บโชว์เป๊ะ. วันเดียว (date_to ไม่ใส่
หรือเท่ากับ date) คือ "รายงานประจำวัน" เดิม — ใส่ date_to ให้ต่างจาก date จะได้
รายงานตามช่วงที่เลือก (สัปดาห์/เดือน/กำหนดเอง แล้วแต่หน้าเว็บส่งมา).

  GET /api/feedback/daily-report?date=&date_to=&branch=          JSON (ใช้ render ในแอป)
  GET /api/feedback/daily-report.print?date=&date_to=&branch=      ฉบับพิมพ์ A4 (พิมพ์/บันทึก PDF)
"""
import datetime
import html
from typing import Optional

from fastapi import APIRouter, Query, Request, Response

from feedback_host import branch_rows, get_db, scope_branch, thai_today
from routers.feedback import _ROW_COLS, _aggregate, _ensure

router = APIRouter()


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _d(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def _period_label(date_from: str, date_to: str) -> str:
    return date_from if date_from == date_to else f"{date_from} – {date_to}"


def _period_word(date_from: str, date_to: str) -> str:
    """วันนี้ for a single day, ช่วงนี้ for any range — used everywhere a section
    heading would otherwise hard-code "today" for what may be a week or month."""
    return "วันนี้" if date_from == date_to else "ช่วงนี้"


def _prev_period(date_from: str, date_to: str) -> tuple:
    """The comparison window: for one day, the SAME WEEKDAY 7 days back (a
    Friday-vs-Thursday comparison is meaningless for restaurant traffic — see
    mtd_weekday_fair_baseline); for a range, the immediately preceding period
    of equal length (a week/month already balances weekdays on its own)."""
    d_from, d_to = _d(date_from), _d(date_to)
    span = (d_to - d_from).days + 1
    if span == 1:
        p = d_from - datetime.timedelta(days=7)
        return p.isoformat(), p.isoformat()
    pe = d_from - datetime.timedelta(days=1)
    ps = pe - datetime.timedelta(days=span - 1)
    return ps.isoformat(), pe.isoformat()


def _headline(agg: dict, date_from: str, date_to: str, branch: Optional[str]) -> str:
    label = _period_label(date_from, date_to)
    if not agg["total"]:
        return f"ไม่มีเสียงลูกค้าเข้ามาในช่วง {label}" + (f" ที่สาขา {branch}" if branch else "")
    bits = [f"{agg['total']} เสียง", f"บวก {agg['sentiment']['pos']} · ลบ {agg['sentiment']['neg']} · กลาง {agg['sentiment']['neu']}"]
    if agg["avg_rating"] is not None:
        bits.append(f"เฉลี่ย {agg['avg_rating']}★")
    if agg["unreplied"]:
        bits.append(f"รอตอบ {agg['unreplied']}")
    if agg["sla_breached"]:
        bits.append(f"เกิน SLA {agg['sla_breached']}")
    if agg["severe"]:
        bits.append(f"⚠ ร้ายแรง {agg['severe']}")
    return " · ".join(bits)


def compute_daily_report(con, date: str, branch: Optional[str] = None, date_to: Optional[str] = None) -> dict:
    """[date, date_to] of customer_feedback (date_to defaults to date — one
    calendar day), through the same _aggregate() the tab's /api/feedback/summary
    uses — so the numbers never drift apart."""
    _ensure(con)
    date_from = date
    date_to = date_to or date_from
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    w, p = ["rated_at >= ?", "rated_at < ?"], [date_from, (_d(date_to) + datetime.timedelta(days=1)).isoformat()]
    if branch:
        w.append("branch_name = ?"); p.append(branch)
    rows = [dict(r) for r in con.execute(f"SELECT {_ROW_COLS} FROM customer_feedback WHERE {' AND '.join(w)}", p)]
    agg = _aggregate(rows, today=date_to)
    p_from, p_to = _prev_period(date_from, date_to)
    wp, pp = ["rated_at >= ?", "rated_at < ?"], [p_from, (_d(p_to) + datetime.timedelta(days=1)).isoformat()]
    if branch:
        wp.append("branch_name = ?"); pp.append(branch)
    prev_total = con.execute(f"SELECT COUNT(*) FROM customer_feedback WHERE {' AND '.join(wp)}", pp).fetchone()[0]
    all_branches = [b["name"] for b in branch_rows(con) if b["active"]]
    return {"date_from": date_from, "date_to": date_to, "branch": branch, "branch_count": 1 if branch else len(all_branches),
            "prev_period": {"date_from": p_from, "date_to": p_to, "total": prev_total},
            "headline": _headline(agg, date_from, date_to, branch), **agg}


@router.get("/api/feedback/daily-report")
def feedback_daily_report(request: Request, date: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                          branch: Optional[str] = Query(None)):
    scope = scope_branch(request)
    if scope:
        branch = scope
    d = (date or "")[:10] or thai_today()
    dt = (date_to or "")[:10] or None
    with get_db() as con:
        return compute_daily_report(con, d, branch, date_to=dt)


# ── print edition: one A4 page, teal/indigo to match the tab, testimonial-ready quotes ──
_PRINT_CSS = """
@page { size: A4; margin: 12mm 12mm 14mm 12mm; }
* { box-sizing: border-box; }
body { margin: 0; font: 10pt/1.45 "Sarabun", "Segoe UI", Tahoma, sans-serif; color: #1b232a; background: #fff; }
h1, h2, h3 { font-family: "Bai Jamjuree", "Sarabun", sans-serif; margin: 0; }
.head { display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 2px solid #0f766e; padding-bottom: 6pt; margin-bottom: 8pt; }
.brand { font: 700 8pt "Bai Jamjuree"; letter-spacing: .1em; text-transform: uppercase; color: #0f766e; }
.head h1 { font-size: 18pt; margin-top: 2pt; }
.head .meta { text-align: right; font-size: 8.5pt; color: #64748b; }
.headline { background: #f0fdfa; border: .6pt solid #99f6e4; border-radius: 4pt; padding: 6pt 9pt; font-size: 10.5pt; font-weight: 600; margin-bottom: 9pt; }
.kpis { display: grid; grid-template-columns: repeat(6, 1fr); gap: 6pt; margin-bottom: 9pt; }
.kpi { border: .6pt solid #d9e2df; border-radius: 4pt; padding: 5pt 7pt; }
.kpi .l { font-size: 7pt; text-transform: uppercase; letter-spacing: .04em; color: #64748b; }
.kpi .v { font-size: 15pt; font-weight: 700; color: #0f172a; }
.kpi .v.warn { color: #b45309; } .kpi .v.bad { color: #b91c1c; }
h2.sec { font-size: 11pt; border-left: 3pt solid #0f766e; padding-left: 6pt; margin: 10pt 0 5pt; }
table { width: 100%; border-collapse: collapse; font-size: 8.8pt; margin-bottom: 4pt; }
th { text-align: left; font-size: 7.6pt; color: #64748b; text-transform: uppercase; letter-spacing: .03em; border-bottom: .8pt solid #cbd5e1; padding: 2pt 5pt; }
td { padding: 3pt 5pt; border-bottom: .5pt solid #e2e8f0; vertical-align: top; }
.quote { border-left: 3pt solid #a7f3d0; background: #f0fdf4; padding: 4pt 8pt; margin: 4pt 0; border-radius: 0 4pt 4pt 0; font-size: 9.2pt; }
.quote .m { font-size: 7.6pt; color: #64748b; margin-top: 1pt; }
.item { border-left: 3pt solid #fca5a5; background: #fef2f2; padding: 4pt 8pt; margin: 4pt 0; border-radius: 0 4pt 4pt 0; font-size: 9pt; }
.item .m { font-size: 7.6pt; color: #64748b; margin-top: 1pt; }
.item .m a { color: inherit; text-decoration: underline; }
.tag { display: inline-block; font-size: 7.4pt; background: #fef3c7; color: #92400e; border-radius: 3pt; padding: 0 4pt; margin-right: 2pt; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14pt; }
.three { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0 12pt; break-inside: avoid-page; }
.empty { color: #94a3b8; font-size: 9pt; font-style: italic; }
.branch-h { font: 700 8.6pt "Bai Jamjuree"; color: #0f766e; margin: 7pt 0 2pt; padding-top: 3pt; border-top: .5pt dashed #cbd5e1; }
.branch-h:first-child { border-top: 0; padding-top: 0; }
.ex { font-size: 7.6pt; color: #64748b; font-style: italic; }
.foot { margin-top: 10pt; font-size: 7.6pt; color: #94a3b8; }
"""


def _tel_href(contact: str):
    digits = "".join(c for c in (contact or "") if c.isdigit() or c == "+")
    return "tel:" + digits if digits and 8 <= len(digits.lstrip("+")) <= 15 else None


def _group_by_branch(items: list) -> list:
    """[(branch, [items…]), …] in descending count order — used to break the
    negative/positive lists out per branch on the ทุกสาขา view (a flat blended
    list across many branches is hard to scan for a branch manager)."""
    groups: dict = {}
    for it in items:
        groups.setdefault(it.get("branch") or "(ไม่ระบุสาขา)", []).append(it)
    return sorted(groups.items(), key=lambda kv: -len(kv[1]))


def _build_print_html(data: dict) -> str:
    date_from, date_to, branch = data["date_from"], data["date_to"], data["branch"]
    label, pw = _period_label(date_from, date_to), _period_word(date_from, date_to)
    kind = "รายงานประจำวัน" if date_from == date_to else "รายงานตามช่วง"
    title = f"{kind} {label}" + (f" · {branch}" if branch else " · ทุกสาขา")
    kpis = [
        ("เสียงทั้งหมด", data["total"], ""),
        ("คะแนนเฉลี่ย", f"{data['avg_rating']:.2f}★" if data["avg_rating"] is not None else "—", ""),
        ("เชิงลบ", f"{data['neg_pct']}%", "bad" if data["neg_pct"] >= 30 else ""),
        ("รอตอบ", data["unreplied"], "warn" if data["unreplied"] else ""),
        ("เกิน SLA", data["sla_breached"], "bad" if data["sla_breached"] else ""),
        ("ร้ายแรง", data["severe"], "bad" if data["severe"] else ""),
    ]
    kpi_html = "".join(f'<div class="kpi"><div class="l">{_esc(l)}</div><div class="v {c}">{_esc(v)}</div></div>' for l, v, c in kpis)
    pp_ = data["prev_period"]
    pp_label = _period_label(pp_["date_from"], pp_["date_to"])
    cmp_line = f"เทียบช่วงก่อนหน้าที่ยาวเท่ากัน ({pp_label}): {pp_['total']} เสียง" + \
               (f" ({'มากขึ้น' if data['total'] > pp_['total'] else 'น้อยลง' if data['total'] < pp_['total'] else 'เท่าเดิม'})" if pp_["total"] else "")

    def theme_rows():
        if not data["themes"]:
            return f'<p class="empty">ยังไม่มีหัวข้อที่ถูกพูดถึง{pw}</p>'
        rows = []
        for t in data["themes"][:12]:
            ex = t["examples"][0]["text"] if t.get("examples") else ""
            rows.append(f'<tr><td>{_esc(t["theme"])}</td><td>{t["n"]}</td><td>{t["neg"]}</td><td>{", ".join(_esc(b) for b in t["branches"])}</td>'
                       f'<td class="ex">{f"“{_esc(ex)}”" if ex else ""}</td></tr>')
        return f'<table><thead><tr><th>หัวข้อ</th><th>พูดถึง</th><th>ลบ</th><th>สาขา</th><th>ตัวอย่าง</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'

    def menu_rows():
        if not data["menus"]:
            return f'<p class="empty">ยังไม่มีเมนูที่ถูกพูดถึง{pw}</p>'
        rows = "".join(f'<tr><td>{_esc(m["menu"])}</td><td>{m["n"]}</td><td>{m["pos"]}</td><td>{m["neg"]}</td></tr>' for m in data["menus"][:12])
        return f'<table><thead><tr><th>เมนู</th><th>พูดถึง</th><th>บวก</th><th>ลบ</th></tr></thead><tbody>{rows}</tbody></table>'

    def _star(v):
        return f" · {v}★" if v is not None else ""

    def _neg_card(it):
        tel = _tel_href(it.get("contact") or "")
        contact_html = f' · 📞 <a href="{tel}">{_esc(it["contact"])}</a>' if tel else (f' · 📞 {_esc(it["contact"])}' if it.get("contact") else "")
        sev = ' <span class="tag">🔥 ร้ายแรง</span>' if it.get("severity") == "high" else ""
        status = "ตอบแล้ว" if it["replied"] else ("เกิน SLA" if it["sla_breached"] else "รอตอบ")
        date_bit = f'{_esc((it.get("date") or "")[:10])} · ' if date_from != date_to else ""
        branch_bit = "" if data["branch"] else f'{_esc(it["branch"] or "-")} · '
        return (f'<div class="item">“{_esc(it["text"])}”{sev}<div class="m">{date_bit}{branch_bit}{_esc(it["source"] or "")}'
               f'{_star(it.get("rating"))} · {status}{contact_html}</div></div>')

    def _pos_card(q):
        date_bit = f'{_esc((q.get("date") or "")[:10])} · ' if date_from != date_to else ""
        branch_bit = "" if data["branch"] else f'{_esc(q["branch"] or "-")} · '
        return f'<div class="quote">“{_esc(q["text"])}”<div class="m">— {date_bit}{branch_bit}{_esc(q["source"] or "")}{_star(q.get("rating"))}</div></div>'

    def negative_items():
        if not data["recent_negative"]:
            return f'<p class="empty">ไม่มีรายการเชิงลบที่ต้องติดตาม{pw} 🎉</p>'
        if data["branch"]:
            return "".join(_neg_card(it) for it in data["recent_negative"])
        return "".join(f'<div class="branch-h">🏪 {_esc(b)} ({len(items)})</div>' + "".join(_neg_card(it) for it in items)
                       for b, items in _group_by_branch(data["recent_negative"]))

    def positive_quotes():
        if not data["recent_positive"]:
            return f'<p class="empty">ยังไม่มีเสียงชมที่หยิบไปใช้ได้{pw}</p>'
        if data["branch"]:
            return "".join(_pos_card(q) for q in data["recent_positive"][:5])
        return "".join(f'<div class="branch-h">🏪 {_esc(b)} ({len(items)})</div>' + "".join(_pos_card(q) for q in items)
                       for b, items in _group_by_branch(data["recent_positive"]))

    def branch_table():
        if data["branch"] or not data["branches"]:
            return ""
        rows = "".join(f'<tr><td>{_esc(b["branch"])}</td><td>{b["n"]}</td><td>{b["neg_pct"]}%</td><td>{b["avg_rating"] if b["avg_rating"] is not None else "—"}</td>'
                       f'<td>{b["unreplied"]}</td><td>{b["flagged"]}</td><td>{_esc(b["top_theme"] or "—")}</td></tr>' for b in data["branches"][:15])
        return (f'<h2 class="sec">🏪 รายสาขาที่มีเสียง{pw} ({len(data["branches"])})</h2>'
               f'<table><thead><tr><th>สาขา</th><th>เสียง</th><th>ลบ%</th><th>★</th><th>รอตอบ</th><th>ปักหมุด</th><th>หัวข้อเด่น</th></tr></thead><tbody>{rows}</tbody></table>')

    def sources_block():
        if not data["sources"]:
            return '<p class="empty">—</p>'
        items = sorted(data["sources"].items(), key=lambda kv: -kv[1])
        return "".join(f'<div style="display:flex;justify-content:space-between"><span>{_esc(s)}</span><b>{n}</b></div>' for s, n in items)

    def response_block():
        r = data["response"]
        rate = f'{r["rate"]}%' if r["rate"] is not None else "—"
        median = f'{r["median_days"]} วัน' if r["median_days"] is not None else "—"
        return (f'<div style="display:flex;justify-content:space-between"><span>ตอบได้ทั้งหมด</span><b>{r["answerable"]}</b></div>'
               f'<div style="display:flex;justify-content:space-between"><span>ตอบแล้ว</span><b>{r["answered"]}</b></div>'
               f'<div style="display:flex;justify-content:space-between"><span>อัตราตอบกลับ</span><b>{rate}</b></div>'
               f'<div style="display:flex;justify-content:space-between"><span>ตอบไวเฉลี่ย</span><b>{median}</b></div>')

    def staff_block():
        if not data["staff"]:
            return f'<p class="empty">ยังไม่มีพนักงานถูกระบุชื่อ{pw}</p>'
        return "".join(f'<div style="display:flex;justify-content:space-between"><span>{_esc(s["name"])} <span class="ex">({_esc(s["branch"])})</span></span>'
                       f'<b>👍{s["pos"]} 👎{s["neg"]}</b></div>' for s in data["staff"][:8])

    return f"""<!doctype html>
<html lang="th"><head><meta charset="utf-8"><title>{_esc(title)}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bai+Jamjuree:wght@600;700&family=Sarabun:wght@400;500;600;700&display=swap">
<style>{_PRINT_CSS}</style></head><body>
<div class="head"><div><div class="brand">Oshinei Dashboard · โมดูล 💬 เสียงลูกค้า</div><h1>{_esc(title)}</h1></div>
<div class="meta">พิมพ์เมื่อ {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}<br>{data['branch_count']} สาขาในรายงานนี้</div></div>
<div class="headline">{_esc(data['headline'])}<br><span style="font-weight:400;font-size:9pt;color:#3a4a45">{_esc(cmp_line)}</span></div>
<div class="kpis">{kpi_html}</div>
<div class="two">
<div><h2 class="sec">🏷️ หัวข้อที่ถูกพูดถึง{pw}</h2>{theme_rows()}</div>
<div><h2 class="sec">🍣 เมนูที่ถูกพูดถึง{pw}</h2>{menu_rows()}</div>
</div>
<h2 class="sec">🚩 ต้องติดตาม{pw} ({len(data['recent_negative'])})</h2>
{negative_items()}
<h2 class="sec">🌟 เสียงชมที่หยิบไปใช้ได้ ({len(data['recent_positive'])})</h2>
{positive_quotes()}
<div class="three">
<div><h2 class="sec">📡 ช่องทางที่มา{pw}</h2>{sources_block()}</div>
<div><h2 class="sec">⏱️ อัตราตอบกลับ</h2>{response_block()}</div>
<div><h2 class="sec">👥 พนักงานที่ถูกพูดถึง</h2>{staff_block()}</div>
</div>
{branch_table()}
<div class="foot">Oshinei · {_esc(kind)}เสียงลูกค้า · คำนวณจากข้อมูลชุดเดียวกับแท็บ 💬 เสียงลูกค้าในแอป</div>
</body></html>"""


@router.get("/api/feedback/daily-report.print")
def feedback_daily_report_print(request: Request, date: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                                branch: Optional[str] = Query(None)):
    scope = scope_branch(request)
    if scope:
        branch = scope
    d = (date or "")[:10] or thai_today()
    dt = (date_to or "")[:10] or None
    with get_db() as con:
        data = compute_daily_report(con, d, branch, date_to=dt)
    return Response(_build_print_html(data), media_type="text/html")


__all__ = ["router", "compute_daily_report"]
