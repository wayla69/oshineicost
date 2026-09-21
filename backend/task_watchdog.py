# -*- coding: utf-8 -*-
"""Watch the Windows scheduled tasks that keep Oshinei alive.

Task Scheduler is the only thing that knows a nightly job failed, and it tells
nobody. `Oshinei Push Invisible` exited 1 for 15 days (2026-09-06 -> 09-21, a
renamed host) and the MSSQL sync was dead for 7 (2026-07-20 -> 07-27, a missing
.env) before anyone happened to look. This looks every day and says so.

    py task_watchdog.py              # check, record, notify
    py task_watchdog.py --dry-run    # check and print only - nothing is sent or saved
    py task_watchdog.py --json       # the report as JSON (implies nothing else changes)

A problem, for every ENABLED task whose name starts with "Oshinei":
    fail    its last exit code is not 0 (and is not "running" / "has not run yet")
    missed  its latest daily slot passed more than 90 minutes ago and it did not run
    stale   it has not run at all for two days or more
    stuck   it has been "Running" for more than three hours

Where a problem is reported:
    1. the live app - POST /api/_admin/task-health (seed token in a header).
       Admins see it on the home page. The app also knows WHEN it last heard from
       here, so it notices the watchdog itself going quiet (PC off for days) -
       the one failure no script on this PC can report.
    2. Telegram, once OSHINEI_TG_TOKEN / OSHINEI_TG_CHAT_ID are in backend/.env.
    3. a Windows toast on this PC, which opens the plain-text report.
2 and 3 fire when the set of problems CHANGES, then at most once every 20 hours
while it lasts, and once more when everything is green again. 1 is sent every run:
it is also the heartbeat.

The exit code is about the WATCHDOG (0 = it looked), never about what it found -
otherwise it would become one more red task in the list it reads.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape as _xml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TASK_PREFIX = "Oshinei"
SELF_NAME = "Oshinei Task Watchdog"
DEFAULT_API = "https://oshinei-dashboard.fly.dev"
STATE_FILE = HERE / "logs" / "task_watchdog.state.json"
REPORT_FILE = HERE / "logs" / "task_watchdog.report.txt"

GRACE = timedelta(minutes=90)          # a slot this recent may still be starting / catching up
STALE_AFTER = timedelta(days=2)
STUCK_AFTER = timedelta(hours=3)       # the longest task limit is 2 h; the scheduler kills at the limit
RENOTIFY_AFTER = timedelta(hours=20)
PROBLEM_LEVELS = ("fail", "stale", "stuck", "missed")     # worst first

RUNNING, NEVER_RAN = 0x41301, 0x41303
RESULT_MEANING = {
    0: "ok",
    1: "the .bat ended on its FAILED branch - the reason is in its log",
    RUNNING: "running now",
    NEVER_RAN: "has not run yet",
    0x41306: "someone stopped it (End task)",
    0x8004131F: "skipped - the previous run was still going",
    0xC000013A: "its window was closed while it ran (not a bug - do not close the black window)",
    0x80070002: "a file it needs is gone - was the folder moved or renamed?",
    0x800710E0: "refused - nobody was logged on, or the account may not run it",
    0x80041326: "refused - the task is disabled by policy",
}

# One PowerShell call returns everything; ASCII-only so PS 5.1 parses it under any codepage.
PS_QUERY = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$out = @(Get-ScheduledTask | Where-Object { $_.TaskName -like '__PREFIX__*' } | ForEach-Object {
  $i = $_ | Get-ScheduledTaskInfo
  [pscustomobject]@{
    name     = $_.TaskName
    state    = [string]$_.State
    last_run = if ($i.LastRunTime -and $i.LastRunTime.Year -gt 2000) { $i.LastRunTime.ToString('s') } else { $null }
    result   = [int64]$i.LastTaskResult
    action   = (@($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join ' ; ')
    triggers = @($_.Triggers | ForEach-Object { [pscustomobject]@{
        kind = [string]$_.CimClass.CimClassName; start = [string]$_.StartBoundary
        days_interval = [int]$_.DaysInterval; enabled = [bool]$_.Enabled } })
  }
})
ConvertTo-Json -InputObject $out -Depth 5 -Compress
"""


# ── read ─────────────────────────────────────────────────────────────────────
def read_tasks(prefix: str = TASK_PREFIX) -> list[dict]:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_QUERY.replace("__PREFIX__", prefix)],
                       capture_output=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError("Get-ScheduledTask failed: " + r.stderr.decode("utf-8", "replace")[:300])
    data = json.loads(r.stdout.decode("utf-8", "replace") or "[]")
    tasks = [data] if isinstance(data, dict) else list(data or [])
    for t in tasks:                                  # PS 5.1 unwraps a one-element array
        if isinstance(t.get("triggers"), dict):
            t["triggers"] = [t["triggers"]]
    return tasks


# ── judge (pure: no clock, no disk, no network) ──────────────────────────────
def _when(s):
    try:
        return datetime.strptime((s or "")[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def latest_slot(triggers, now: datetime):
    """The most recent moment a once-a-day trigger should have fired, at least GRACE ago."""
    slots = []
    for t in triggers or []:
        if not t.get("enabled", True) or "Daily" not in (t.get("kind") or "") or (t.get("days_interval") or 1) != 1:
            continue
        first = _when(t.get("start"))
        if not first:
            continue
        slot = now.replace(hour=first.hour, minute=first.minute, second=0, microsecond=0)
        if slot > now - GRACE:
            slot -= timedelta(days=1)
        if slot >= first.replace(second=0):          # never expect a run from before the trigger existed
            slots.append(slot)
    return max(slots) if slots else None


def judge(task: dict, now: datetime) -> dict:
    name, state = task.get("name") or "?", task.get("state") or ""
    result = int(task.get("result") or 0) & 0xFFFFFFFF
    last = _when(task.get("last_run"))
    out = {"name": name, "level": "ok", "result": result, "last_run": task.get("last_run"),
           "meaning": RESULT_MEANING.get(result, f"exit code {result} (0x{result:08X})")}
    if state == "Disabled":
        out.update(level="disabled", meaning="switched off - not watched")
    elif state == "Running" or result == RUNNING:
        if last and now - last > STUCK_AFTER:
            out.update(level="stuck", meaning=f"still running after {int((now - last).total_seconds() // 3600)} h")
    elif result not in (0, NEVER_RAN):
        out["level"] = "fail"
    else:
        slot = latest_slot(task.get("triggers"), now)
        if slot and (last is None or last < slot - timedelta(minutes=5)):
            stale = last is None or now - last >= STALE_AFTER
            out.update(level="stale" if stale else "missed",
                       meaning=("no run for %d days" % (now - last).days) if stale and last
                       else "never ran" if last is None
                       else "did not run at its %s slot" % slot.strftime("%H:%M"))
    return out


def carry_since(findings: list[dict], prev: dict, now: datetime) -> dict:
    """How long each problem has lasted. Task Scheduler keeps no history here (its
    event log is off), so the watchdog remembers the first time it saw each one."""
    keep = {}
    for f in findings:
        if f["level"] not in PROBLEM_LEVELS:
            continue
        was = prev.get(f["name"]) or {}
        since = was.get("since") or f.get("last_run") or now.strftime("%Y-%m-%dT%H:%M:%S")
        f["since"] = since
        f["days"] = max(0, (now - (_when(since) or now)).days)
        keep[f["name"]] = {"since": since, "level": f["level"]}
    return keep


def signature(findings: list[dict]) -> str:
    return "|".join(sorted(f"{f['name']}={f['level']}" for f in findings if f["level"] in PROBLEM_LEVELS))


# ── why it failed: the last telling line of the task's own log ───────────────
_TELLING = re.compile(r"HTTP \d{3}|Error|ERROR|Traceback|FAILED|failed|refused|denied|not found|timed? ?out|cannot|can't", re.I)
_BANNER = re.compile(r"^\*{3}.*\*{3}$")


def log_path_for(action: str):
    """Task action -> the .bat it runs -> the log that .bat tees into (-Log "%~dp0...")."""
    m = re.search(r'([A-Za-z]:\\[^"]+?\.bat)', action or "")
    if not m:
        return None
    bat = Path(m.group(1))
    try:
        rel = re.search(r'-Log\s+"%~dp0([^"]+)"', bat.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return bat.parent / rel.group(1) if rel else None


def last_telling_line(log: Path, limit: int = 220) -> str:
    try:
        with open(log, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 65536))
            raw = fh.read()
    except OSError:
        return ""
    # older logs are UTF-16, newer lines UTF-8 (_tee.ps1 changed 2026-09-07) - sometimes in ONE file
    text = raw.decode("utf-16-le", "replace") if raw.count(b"\x00") > len(raw) // 4 else raw.decode("utf-8", "replace")
    run = re.split(r"(?m)^={10,}.*$", text)[-1]                  # the last run only
    lines = [ln.strip() for ln in run.splitlines() if ln.strip()]
    telling = [ln for ln in lines if _TELLING.search(ln) and not _BANNER.match(ln)]
    pick = telling[-1] if telling else (lines[-1] if lines else "")
    return pick[:limit]


# ── tell ─────────────────────────────────────────────────────────────────────
def build_report(tasks: list[dict], now: datetime, prev_since: dict) -> tuple[dict, dict]:
    findings = [judge(t, now) for t in tasks if t.get("name") != SELF_NAME]
    since = carry_since(findings, prev_since, now)
    by_name = {t.get("name"): t for t in tasks}
    for f in findings:
        if f["level"] == "fail":
            log = log_path_for(by_name[f["name"]].get("action"))
            if log:
                f["log_file"], f["log_line"] = str(log), last_telling_line(log)
    findings.sort(key=lambda f: (PROBLEM_LEVELS.index(f["level"]) if f["level"] in PROBLEM_LEVELS else 9, f["name"]))
    problems = [f for f in findings if f["level"] in PROBLEM_LEVELS]
    return ({"host": socket.gethostname(), "checked_at": now.strftime("%Y-%m-%d %H:%M:%S"),
             "ok": not problems, "problems": len(problems), "signature": signature(findings), "tasks": findings}, since)


def text_report(rep: dict) -> str:
    head = "ALL GREEN" if rep["ok"] else f"{rep['problems']} PROBLEM(S)"
    out = [f"Oshinei task watchdog - {rep['checked_at']} on {rep['host']} - {head}", ""]
    for f in rep["tasks"]:
        last = (f.get("last_run") or "never").replace("T", " ")[:16]
        line = f"  [{f['level'].upper():8}] {f['name']:<28} last run {last}  - {f['meaning']}"
        if f.get("days"):
            line += f"  ({f['days']} day(s) now)"
        out.append(line)
        if f.get("log_line"):
            out.append(f"{'':13}log: {f['log_line']}")
            out.append(f"{'':13}     {f['log_file']}")
    return "\n".join(out) + "\n"


_TH_LEVEL = {"fail": "ล้มเหลว", "stale": "ไม่ได้รันหลายวัน", "stuck": "ค้าง", "missed": "ไม่ได้รันรอบล่าสุด"}


def telegram_text(rep: dict) -> str:
    import html
    if rep["ok"]:
        return "✅ <b>งานอัตโนมัติ Oshinei กลับมาปกติทุกงานแล้ว</b>"
    lines = [f"🛠️ <b>งานอัตโนมัติ Oshinei มีปัญหา {rep['problems']} งาน</b> ({html.escape(rep['host'])})"]
    for f in rep["tasks"]:
        if f["level"] not in PROBLEM_LEVELS:
            continue
        days = f" · {f['days']} วันแล้ว" if f.get("days") else ""
        lines.append(f"• <b>{html.escape(f['name'])}</b> — {_TH_LEVEL[f['level']]}{days}")
        lines.append(f"  {html.escape(f.get('log_line') or f['meaning'])}")
    return "\n".join(lines)


def _seed_token() -> str:
    token = os.environ.get("OSHINEI_SEED_TOKEN", "").strip()
    if ":" in token:
        token = token.split(":", 1)[1].strip()
    if token:
        return token
    import refresh_fly_db as _r
    for name in (".seed_token.txt", ".seed_tokens.txt"):
        f = HERE / name
        if f.exists():
            token = _r._parse_token_file(f) or ""
            if token:
                return token
    return ""


def post_to_app(rep: dict, api: str, timeout: int = 45) -> str:
    """Report + heartbeat. The token goes in a header, never the URL."""
    token = _seed_token()
    if not token:
        return "skipped (no seed token here)"
    req = urllib.request.Request(api.rstrip("/") + "/api/_admin/task-health", method="POST",
                                 data=json.dumps(rep, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "X-Seed-Token": token})
    for attempt in (1, 2):                          # Fly auto-sleeps; the first call may be the wake-up
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return f"sent (HTTP {r.status})"
        except urllib.error.HTTPError as e:
            return f"NOT sent (HTTP {e.code}{' - the live app predates this endpoint' if e.code in (401, 404, 405) else ''})"
        except Exception as e:                      # offline is normal for a laptop; never fail the run
            err = f"NOT sent ({type(e).__name__}: {e})"
    return err


_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml($env:OSH_TOAST_XML)
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($x))
"""


def toast(rep: dict, report_file: Path = REPORT_FILE) -> str:
    bad = [f for f in rep["tasks"] if f["level"] in PROBLEM_LEVELS]
    title = "Oshinei: all scheduled tasks are green again" if rep["ok"] else f"Oshinei: {len(bad)} scheduled task(s) need attention"
    body = "; ".join(f"{f['name'].replace('Oshinei ', '')} - {f['level']}" + (f" {f['days']}d" if f.get("days") else "") for f in bad[:4])
    uri = report_file.resolve().as_uri()
    xml = (f'<toast scenario="reminder" activationType="protocol" launch="{_xml(uri)}"><visual><binding template="ToastGeneric">'
           f"<text>{_xml(title)}</text><text>{_xml(body or 'Nothing to do.')}</text></binding></visual>"
           f'<actions><action content="Open report" activationType="protocol" arguments="{_xml(uri)}"/>'
           f'<action content="Dismiss" activationType="system" arguments="dismiss"/></actions></toast>')
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS],
                           env={**os.environ, "OSH_TOAST_XML": xml}, capture_output=True, timeout=60)
        return "shown" if r.returncode == 0 else "NOT shown (" + r.stderr.decode("utf-8", "replace").strip()[:120] + ")"
    except Exception as e:
        return f"NOT shown ({type(e).__name__})"


def should_notify(rep: dict, state: dict, now: datetime) -> bool:
    last_sig, last_at = state.get("notified_signature", ""), _when(state.get("notified_at"))
    if rep["signature"] != last_sig:
        return bool(rep["signature"] or last_sig)            # changed - including "back to green"
    return bool(rep["signature"]) and (last_at is None or now - last_at >= RENOTIFY_AFTER)


def _load_env(path=HERE / ".env"):
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="check and print; send and save nothing")
    ap.add_argument("--json", action="store_true", help="print the report as JSON (implies --dry-run)")
    ap.add_argument("--api", default=os.environ.get("OSHINEI_API") or DEFAULT_API)
    a = ap.parse_args(argv)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    _load_env()
    now = datetime.now()
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    rep, since = build_report(read_tasks(), now, state.get("since") or {})
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0
    print(text_report(rep))
    if a.dry_run:
        print("dry run - nothing sent, nothing saved")
        return 0
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(text_report(rep), encoding="utf-8")
    print("live app :", post_to_app(rep, a.api))
    state["since"] = since
    if should_notify(rep, state, now):
        import notify
        print("telegram :", "sent" if notify.send_telegram(telegram_text(rep)) else "not configured (OSHINEI_TG_TOKEN / OSHINEI_TG_CHAT_ID)")
        print("toast    :", toast(rep))
        state.update(notified_signature=rep["signature"], notified_at=now.strftime("%Y-%m-%dT%H:%M:%S"))
    else:
        print("notify   : nothing new since", state.get("notified_at") or "-")
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
