# -*- coding: utf-8 -*-
"""Set up Telegram alerts - guided, run by the owner, the token never leaves this PC.

    py backend/setup_telegram.py              # the whole walk-through
    py backend/setup_telegram.py --test       # send one test message with the saved settings
    py backend/setup_telegram.py --chat-only  # keep the bot, pick a different chat/group
    py backend/setup_telegram.py --fly-only   # copy the saved settings to the live app

What Telegram alerts carry once this is done (all of them already exist and are
silent no-ops until the two values below are set - see notify.py):
    this PC   task watchdog (a scheduled task failed / was missed / the PC healed it),
              new negative reviews, reply-SLA alerts, the daily + weekly voice reports
    live app  the Monday adoption digest

It replaces steps 3-5 of the hand procedure in the feedback rollout checklist, which
had the owner paste the bot token into a BROWSER URL (api.telegram.org/bot<TOKEN>/
getUpdates - it stays in the history) and hand-edit backend/.env. Here the token is
typed once, hidden, straight into this program; it is never printed (only masked),
never put on a command line (flyctl gets it on stdin), and is written to one place:
backend/.env, which is gitignored and copied nightly to OneDrive/Oshinei Backups/
secrets by "Backup DB.bat".

The one thing no program can do for you: create the bot. In Telegram open @BotFather,
send /newbot, pick a name and a username ending in "bot", and copy the token it shows.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import notify  # noqa: E402

ENV_FILE = HERE / ".env"
FLY_APP = "oshinei-dashboard"
TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
FIND_CHAT_SECONDS = 180


def mask(token: str) -> str:
    return (token[:4] + "..." + token[-4:]) if token and len(token) > 12 else "(not set)"


# ── Telegram ─────────────────────────────────────────────────────────────────
def tg_call(token: str, method: str, params: dict = None, timeout: int = 40) -> dict:
    """One Bot API call. Telegram puts the token in the URL path by design; this goes
    straight to api.telegram.org over TLS and is logged nowhere here."""
    req = urllib.request.Request(f"{notify.TELEGRAM_API}/bot{token}/{method}", method="POST",
                                 data=json.dumps(params or {}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except ValueError:
            return {"ok": False, "error_code": e.code, "description": "HTTP %s" % e.code}
    except Exception as e:                                    # offline, DNS, timeout
        return {"ok": False, "description": f"{type(e).__name__}: {e}".replace(token, "<token>")}


def chats_in(updates: list) -> dict:
    """{chat_id: label} from getUpdates. A bot hears about a chat when someone writes to
    it, when a command is sent in a group, or when it is ADDED to a group (my_chat_member)
    - the last one works even with group privacy mode on."""
    found = {}
    for u in updates or []:
        for key in ("message", "edited_message", "channel_post", "my_chat_member", "chat_member"):
            chat = (u.get(key) or {}).get("chat")
            if not chat or "id" not in chat:
                continue
            name = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) \
                or chat.get("username") or "?"
            found[str(chat["id"])] = f"{name}  [{chat.get('type', '?')}]"
    return found


# ── backend/.env ─────────────────────────────────────────────────────────────
def read_env(path: Path = ENV_FILE) -> dict:
    out = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def write_env(values: dict, path: Path = ENV_FILE) -> None:
    """Set KEY=VALUE lines in place; every other line (the MSSQL login lives here too)
    is kept byte for byte. Written to a temp file and swapped, so a crash cannot leave
    half a .env behind - a missing .env once killed the sync for 7 days."""
    try:
        raw = path.read_bytes().decode("utf-8")      # bytes: read_text() would turn CRLF into LF
    except OSError:
        raw = ""
    eol = "\r\n" if "\r\n" in raw else "\n"
    lines, left = raw.splitlines(), dict(values)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in left and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={left.pop(key)}"
    if left:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# Telegram alerts (backend/setup_telegram.py)")
        lines += [f"{k}={v}" for k, v in left.items()]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(eol.join(lines) + eol, encoding="utf-8", newline="")
    os.replace(tmp, path)


# ── the live app ─────────────────────────────────────────────────────────────
def a_sync_is_running() -> str:
    """Setting a Fly secret restarts the machine; mid-upload that kills the daily DB push."""
    try:
        import task_watchdog as tw
        return next((t["name"] for t in tw.read_tasks() if t.get("state") == "Running" and "Sync" in t["name"]), "")
    except Exception:
        return ""


def push_to_fly(token: str, chat_id: str, run=subprocess.run) -> tuple[bool, str]:
    """`flyctl secrets import` reads NAME=VALUE from STDIN - the token is never an argument,
    so it never shows in a process list or a shell history."""
    try:
        r = run(["flyctl", "secrets", "import", "-a", FLY_APP],
                input=f"OSHINEI_TG_TOKEN={token}\nOSHINEI_TG_CHAT_ID={chat_id}\n".encode("utf-8"),
                capture_output=True, timeout=300)
    except FileNotFoundError:
        return False, "flyctl is not installed on this PC"
    except Exception as e:
        return False, type(e).__name__
    tail = (r.stdout + r.stderr).decode("utf-8", "replace").replace(token, "<token>").strip().splitlines()
    return r.returncode == 0, (tail[-1] if tail else "")


# ── the walk-through ─────────────────────────────────────────────────────────
class UI:
    """Console I/O in one place so the flow can be driven by a test."""
    out = staticmethod(print)
    ask = staticmethod(input)
    secret = staticmethod(getpass.getpass)
    sleep = staticmethod(time.sleep)
    clock = staticmethod(time.monotonic)


def get_token(ui, call, saved: str) -> str:
    if saved:
        me = call(saved, "getMe")
        if me.get("ok") and ui.ask(f"  A bot is already saved: @{me['result'].get('username')} ({mask(saved)}). Keep it? [Y/n] ").strip().lower() != "n":
            return saved
    ui.out("")
    ui.out("STEP 1 - the bot. In Telegram open @BotFather -> /newbot -> copy the token it gives you.")
    ui.out("         (looks like 123456789:AAH...; typing is hidden here - paste with right-click, then Enter)")
    for _ in range(5):
        token = (ui.secret("  bot token: ") or "").strip()
        if not TOKEN_RE.match(token):
            ui.out("  x that does not look like a bot token (digits, a colon, then ~35 letters). Try again.")
            continue
        me = call(token, "getMe")
        if me.get("ok"):
            ui.out(f"  ok - this is @{me['result'].get('username')} ({me['result'].get('first_name')})")
            return token
        ui.out(f"  x Telegram refused it: {me.get('description')}. Copy it again from @BotFather.")
    return ""


def find_chat(ui, call, token: str, bot: str) -> str:
    ui.out("")
    ui.out("STEP 2 - where alerts should arrive. Do ONE of these in Telegram now:")
    ui.out(f"         a group  : create/open the group -> Add member -> @{bot} -> then type /start in the group")
    ui.out(f"         just you : open @{bot} and press Start")
    ui.out(f"         (waiting up to {FIND_CHAT_SECONDS // 60} minutes...)")
    seen, offset, end = {}, None, ui.clock() + FIND_CHAT_SECONDS
    while ui.clock() < end:
        r = call(token, "getUpdates", {"timeout": 20, **({"offset": offset} if offset else {}),
                                       "allowed_updates": ["message", "channel_post", "my_chat_member"]}, 40)
        if not r.get("ok"):
            if r.get("error_code") == 409:
                ui.out("  x this bot is already being read by another program (a webhook or another poller).")
                ui.out("    Make a NEW bot in @BotFather just for Oshinei alerts and run this again.")
                return ""
            ui.sleep(3)
            continue
        for u in r.get("result") or []:
            offset = u["update_id"] + 1
        new = {k: v for k, v in chats_in(r.get("result")).items() if k not in seen}
        seen.update(new)
        for cid, label in new.items():
            ui.out(f"  found: {label}  id {cid}")
        if seen and not new:
            break                                   # one quiet poll after the first find: list is complete
    if not seen:
        ui.out("  x nothing arrived. Check the bot was added / Start was pressed, then run this again.")
        return ""
    ids = list(seen)
    if len(ids) == 1:
        return ids[0]
    for n, cid in enumerate(ids, 1):
        ui.out(f"    {n}. {seen[cid]}  id {cid}")
    pick = ui.ask(f"  Which one should get the alerts? [1-{len(ids)}] ").strip()
    return ids[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= len(ids) else ""


def offer_fly(ui, token: str, chat_id: str, push=push_to_fly, busy=a_sync_is_running) -> None:
    ui.out("")
    ui.out("STEP 4 - the live app (optional). It sends the Monday adoption digest. This stores the same")
    ui.out("         two values as Fly secrets and restarts the app (about 10 seconds offline).")
    if ui.ask("  Do it now? [y/N] ").strip().lower() != "y":
        ui.out("  skipped - run  py backend/setup_telegram.py --fly-only  whenever you want it.")
        return
    running = busy()
    if running:
        ui.out(f"  x not now: '{running}' is running and a restart would kill its upload.")
        ui.out("    Run  py backend/setup_telegram.py --fly-only  after it finishes.")
        return
    ok, last = push(token, chat_id)
    ui.out("  ok - the live app has them" + (f" ({last})" if last else "") if ok else f"  x flyctl failed: {last}")


def run(args, ui=UI, call=tg_call, env_file: Path = ENV_FILE, push=push_to_fly, busy=a_sync_is_running) -> int:
    saved = read_env(env_file)
    token, chat_id = saved.get("OSHINEI_TG_TOKEN", ""), saved.get("OSHINEI_TG_CHAT_ID", "")
    if args.test or args.fly_only:
        if not (token and chat_id):
            ui.out("Telegram is not set up yet - run this without options first.")
            return 1
        if args.fly_only:
            offer_fly(ui, token, chat_id, push, busy)
            return 0
        sent = call(token, "sendMessage", {"chat_id": chat_id, "text": "Oshinei alerts: test message"})
        ui.out("ok - sent" if sent.get("ok") else f"x not sent: {sent.get('description')}")
        return 0 if sent.get("ok") else 1

    ui.out("Oshinei - Telegram alerts setup. Nothing is saved until the test message arrives.")
    token = token if args.chat_only and token else get_token(ui, call, token)
    if not token:
        return 1
    bot = (call(token, "getMe").get("result") or {}).get("username") or "your_bot"
    chat_id = find_chat(ui, call, token, bot)
    if not chat_id:
        return 1
    ui.out("")
    ui.out("STEP 3 - test message...")
    sent = call(token, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML", "text":
                "✅ <b>Oshinei alerts are connected.</b>\nThis chat will get: scheduled-task problems on the office PC, "
                "new negative reviews, reply-SLA alerts, and the daily / weekly reports."})
    if not sent.get("ok"):
        ui.out(f"  x Telegram would not deliver it: {sent.get('description')}  - nothing was saved.")
        return 1
    write_env({"OSHINEI_TG_TOKEN": token, "OSHINEI_TG_CHAT_ID": chat_id}, env_file)
    ui.out(f"  ok - delivered, and saved to {env_file.name} (bot {mask(token)}, chat {chat_id}).")
    ui.out("       'Backup DB.bat' copies that file to OneDrive/Oshinei Backups/secrets every morning.")
    offer_fly(ui, token, chat_id, push, busy)
    ui.out("")
    ui.out("Done. The task watchdog, review alerts and reports will use Telegram from their next run.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--test", action="store_true", help="send one test message with the saved settings")
    ap.add_argument("--chat-only", action="store_true", help="keep the saved bot, choose the chat again")
    ap.add_argument("--fly-only", action="store_true", help="copy the saved settings to the live app")
    try:
        return run(ap.parse_args(argv))
    except (KeyboardInterrupt, EOFError):
        print("\ncancelled - nothing was changed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
