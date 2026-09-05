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
    lines = [f"⚠️ <b>รีวิวเชิงลบใหม่ {len(items)} รายการ</b> ({html.escape(source or '')})"]
    for it in items[:15]:
        stars = f"{it['rating']:g}★ · " if it.get("rating") is not None else ""
        lines.append(f"• <b>{html.escape(str(it.get('branch') or '-'))}</b> · {stars}{html.escape((it.get('text') or '')[:160])}")
        if it.get("url"):
            lines.append(f"  {html.escape(it['url'])}")
    if len(items) > 15:
        lines.append(f"… และอีก {len(items) - 15} รายการ")
    if app_url:
        lines.append(f"เปิดแท็บ 💬 เสียงลูกค้า: {html.escape(app_url.rstrip('/'))}/")
    return "\n".join(lines)


__all__ = ["send_telegram", "negative_digest", "telegram_config"]
