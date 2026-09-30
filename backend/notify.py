# -*- coding: utf-8 -*-
"""Alerts to people. One channel today: a Telegram bot.

    OSHINEI_TG_TOKEN     the bot token from @BotFather
    OSHINEI_TG_CHAT_ID   the chat/group id the bot posts into
in backend/.env (or the environment). Unset → every send is a silent no-op that
returns False, so callers never need to check the config themselves.
"""
import html
import json
import os
import urllib.error
import urllib.request

TELEGRAM_API = "https://api.telegram.org"


def _post_json(url, body, timeout=30):
    req = urllib.request.Request(url, method="POST", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def telegram_config(env=None) -> tuple:
    env = os.environ if env is None else env
    return (env.get("OSHINEI_TG_TOKEN") or "").strip(), (env.get("OSHINEI_TG_CHAT_ID") or "").strip()


def send_telegram(text: str, token: str = None, chat_id: str = None, http=None, env=None) -> bool:
    """Send one HTML-formatted message. Returns True when Telegram accepted it."""
    t, c = telegram_config(env)
    token, chat_id = token or t, chat_id or c
    if not token or not chat_id or not text:
        return False
    http = http or (lambda method, url, headers, body: _post_json(url, body))
    status, j = http("POST", f"{TELEGRAM_API}/bot{token}/sendMessage", {"Content-Type": "application/json"},
                     {"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True})
    return status == 200 and bool((j or {}).get("ok"))


def negative_digest(items: list, source: str, app_url: str = "") -> str:
    """The alert text for newly collected negative reviews."""
    lines = [f"⚠ <b>รีวิวเชิงลบใหม่ {len(items)} รายการ</b> ({html.escape(source or '')})"]
    for it in sorted(items, key=lambda x: x.get("severity") != "high")[:15]:
        stars = f"{it['rating']:g}★ · " if it.get("rating") is not None else ""
        fire = f"<b>ร้ายแรง{(' — ' + html.escape(it['severity_note'])) if it.get('severity_note') else ''}</b> · " if it.get("severity") == "high" else ""
        lines.append(f"• {fire}<b>{html.escape(str(it.get('branch') or '-'))}</b> · {stars}{html.escape((it.get('text') or '')[:160])}")
        if it.get("url"):
            lines.append(f"  {html.escape(it['url'])}")
    if len(items) > 15:
        lines.append(f"… และอีก {len(items) - 15} รายการ")
    if app_url:
        lines.append(f"เปิดแท็บ เสียงลูกค้า: {html.escape(app_url.rstrip('/'))}/")
    return "\n".join(lines)


def _arrow(cur, prev, digits=0, unit="", invert=False):
    if cur is None or prev is None:
        return ""
    d = cur - prev
    if abs(d) < (0.05 if digits else 0.5):
        return " (=)"
    good = (d < 0) if invert else (d > 0)
    return f" ({'▲' if d > 0 else '▼'}{abs(d):.{digits}f}{unit}{' ✓' if good else ''})"


def weekly_digest(d: dict, app_url: str = "") -> str:
    """The week in numbers for the group chat — this window against the one
    before, what waits now, and whether the fixes moved the voices
    (routers/feedback_actions.compute_digest shape)."""
    r = d.get("range") or ["", ""]
    lines = [f"<b>สรุปเสียงลูกค้า {d.get('days', 7)} วัน</b> ({html.escape(r[0])} → {html.escape(r[1])})"]
    v, pv = d.get("voices") or 0, d.get("prev_voices")
    line = f"เสียง <b>{v}</b>{_arrow(v, pv)}"
    if d.get("neg_pct") is not None and v:
        line += f" · ลบ <b>{d['neg_pct']:g}%</b>{_arrow(d['neg_pct'], d.get('prev_neg_pct'), 1, ' pt', invert=True)}"
    if d.get("avg_rating") is not None:
        line += f" · ★ <b>{d['avg_rating']:.2f}</b>{_arrow(d['avg_rating'], d.get('prev_avg_rating'), 2)}"
    lines.append(line)
    resp = d.get("response") or {}
    if resp.get("answerable"):
        lines.append(f"ตอบกลับ <b>{resp.get('rate') or 0}%</b> ({resp.get('answered', 0)}/{resp['answerable']})"
                     f"{_arrow(resp.get('rate'), d.get('prev_response_rate'), 0, ' pt')}"
                     + (f" · เวลากลาง {resp['median_days']} วัน" if resp.get("median_days") is not None else ""))
    waiting = []
    if d.get("unreplied"):
        waiting.append(f"รอสาขาตอบตอนนี้ <b>{d['unreplied']}</b>")
    if d.get("sla_breached"):
        waiting.append(f"เกิน SLA <b>{d['sla_breached']}</b> ⚠")
    if d.get("severe"):
        waiting.append(f"ร้ายแรง <b>{d['severe']}</b>")
    if d.get("flagged"):
        waiting.append(f"{d['flagged']}")
    if waiting:
        lines.append(" · ".join(waiting))
    th = d.get("themes") or []
    if th:
        lines.append("หัวข้อ: " + " · ".join(f"{html.escape(t['theme'])} {t['n']}{_arrow(t['n'], t.get('prev_n'), invert=True)}" for t in th[:5]))
    w = d.get("branches_watch") or []
    if w:
        lines.append("สาขาที่ต้องดู: " + " · ".join(f"<b>{html.escape(b['branch'])}</b> ลบ {b['neg_pct']:g}% ({b['neg']}/{b['n']})"
                                                 + (f" — {html.escape(b['top_theme'])}" if b.get("top_theme") else "") for b in w))
    p = d.get("branches_praised") or []
    if p:
        lines.append("ชมมากสุด: " + " · ".join(f"{html.escape(b['branch'])} ★{b['avg_rating']:.2f} (n={b['n']})" for b in p))
    rb = [x for x in (d.get("response_by_branch") or []) if (x.get("answerable") or 0) >= 2][:3]
    if rb:
        lines.append("ตอบช้าสุด: " + " · ".join(f"{html.escape(x['branch'])} {x.get('rate') or 0}% ({x.get('answered', 0)}/{x['answerable']}"
                                              + (f", กลาง {x['median_days']} วัน" if x.get('median_days') is not None else "") + ")" for x in rb))
    sv = d.get("survey") or {}
    if sv.get("n"):
        lines.append(f"แบบสอบถาม QR: {sv['n']} คำตอบ{_arrow(sv['n'], sv.get('prev_n'))}"
                     + (f" · ★{sv['avg_rating']:.2f}" if sv.get("avg_rating") is not None else ""))
    a = d.get("actions") or {}
    if any(a.get(k) for k in ("open", "doing", "done", "better", "same", "worse", "no_data")):
        bits = [f"ค้าง {a.get('open', 0) + a.get('doing', 0)}" + (f" (เกินกำหนด {a['overdue']})" if a.get("overdue") else "")]
        if a.get("done"):
            bits.append(f"เสร็จสัปดาห์นี้ {a['done']}")
        ver = [f"ดีขึ้น {a['better']}" if a.get("better") else "", f"เท่าเดิม {a['same']}" if a.get("same") else "",
               f"แย่ลง {a['worse']} ⚠" if a.get("worse") else ""]
        ver = [x for x in ver if x]
        if ver:
            bits.append("พิสูจน์ผล: " + " / ".join(ver))
        lines.append("งานแก้: " + " · ".join(bits))
        for it in (a.get("items") or [])[:4]:
            res = {"better": "✓ ดีขึ้น", "same": "= เท่าเดิม", "worse": "✗ แย่ลง", "no_data": "ไม่มีข้อมูล"}.get(it.get("result") or "", "")
            rate = (f" {it['before_rate']:g} → {it['after_rate']:g} ลบ/100 เสียง" if it.get("before_rate") is not None and it.get("after_rate") is not None else "")
            left = f" (อีก {it['days_left']} วัน)" if it.get("days_left") else ""
            lines.append(f"  • {html.escape(it['title'])}{' · ' + html.escape(it['branch']) if it.get('branch') else ''}: {res or 'รอครบ 28 วัน'}{rate}{left}")
    if app_url:
        lines.append(f"เปิดแท็บ เสียงลูกค้า: {html.escape(app_url.rstrip('/'))}/")
    return "\n".join(lines)


def sla_alert_digest(items: list, app_url: str = "") -> str:
    """The alert text for cases that just crossed the SLA — same shape as
    negative_digest() but framed around "still waiting", since these can be
    old cases from earlier fetches, not necessarily today's new reviews."""
    lines = [f"<b>เกิน SLA {len(items)} รายการ</b> (ยังไม่มีคนตอบ)"]
    for it in sorted(items, key=lambda x: -(x.get("age_days") or 0))[:15]:
        stars = f"{it['rating']:g}★ · " if it.get("rating") is not None else ""
        fire = f"<b>ร้ายแรง{(' — ' + html.escape(it['severity_note'])) if it.get('severity_note') else ''}</b> · " if it.get("severity") == "high" else ""
        age = f"{it.get('age_days', 0)} วัน · " if it.get("age_days") is not None else ""
        lines.append(f"• {fire}<b>{html.escape(str(it.get('branch') or '-'))}</b> · {age}{stars}{html.escape((it.get('text') or '')[:160])}")
        if it.get("url"):
            lines.append(f"  {html.escape(it['url'])}")
    if len(items) > 15:
        lines.append(f"… และอีก {len(items) - 15} รายการ")
    if app_url:
        lines.append(f"เปิดแท็บ เสียงลูกค้า: {html.escape(app_url.rstrip('/'))}/")
    return "\n".join(lines)


def daily_report_digest(d: dict, app_url: str = "") -> str:
    """One day's (or period's) report, compact for a daily Telegram push —
    complements weekly_digest() (the week-over-week numbers + action verdicts,
    Mondays only) with the brand-new / recurring problems the report surfaces,
    so marketing doesn't have to open the app and click "สร้างรายงาน" to see
    them (routers/feedback_daily_report.compute_daily_report shape)."""
    label = d["date_from"] if d.get("date_from") == d.get("date_to") else f"{d.get('date_from')} – {d.get('date_to')}"
    branch_bit = f" · {html.escape(d['branch'])}" if d.get("branch") else ""
    lines = [f"<b>รายงานเสียงลูกค้า {html.escape(label)}</b>{branch_bit}"]
    lines.append(html.escape(d.get("headline") or ""))
    if d.get("total"):
        new_i, open_i, done_i, chronic = d.get("new_issues") or [], d.get("open_issues") or [], d.get("resolved_issues") or [], d.get("chronic_branches") or []
        bits = []
        if new_i:
            bits.append(f"ใหม่ {len(new_i)}")
        if open_i:
            bits.append(f"ค้าง {len(open_i)}")
        if done_i:
            bits.append(f"ปิดแล้ว {len(done_i)}")
        if chronic:
            bits.append(f"ซ้ำซาก {len(chronic)}")
        if bits:
            lines.append(" · ".join(bits))
        if new_i:
            lines.append("ปัญหาใหม่ยังไม่มีคนตาม:")
            for it in new_i[:3]:
                lines.append(f"• {html.escape(it.get('branch') or '-')}: {html.escape(it['theme'])} ({it['n']})")
        if chronic:
            lines.append("สาขาที่ปัญหาเดิมวนซ้ำ:")
            for it in chronic[:3]:
                lines.append(f"• {html.escape(it.get('branch') or '-')}: {html.escape(it['theme'])} ({it['periods']}/{it['of_periods']} ช่วง)")
    if app_url:
        lines.append(f"เปิดแท็บ เสียงลูกค้า: {html.escape(app_url.rstrip('/'))}/")
    return "\n".join(lines)


__all__ = ["send_telegram", "negative_digest", "sla_alert_digest", "weekly_digest", "daily_report_digest", "telegram_config"]
