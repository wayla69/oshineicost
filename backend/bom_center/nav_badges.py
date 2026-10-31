# -*- coding: utf-8 -*-
"""Task count badges (PR-D4, owner 2026-10-21) - the red numbers on the department icons, the page tiles and the bottom navigation.

GET /api/nav/badges answers, for the signed-in account, `{"pages": {"<tab>" | "<tab>:<pane>": n}, "at": iso}`: how many tasks wait for
THIS person's own move on each page. It invents no business rule - every count is a row that หน้างาน (todo.py) already builds:

  * the open items of todo.build whose owner is the viewer's role (the same "mine" rule as หน้างาน, js/01-core.js _shmMine:
    the role's owner words in `owner_th`, else the item's `owner_modules` against the viewer's modules);
  * for the three roles that own steps of the month close (analyst, salessupport, accounting) the open steps they own - the
    "งานของฉัน" list of หน้างาน shows them next to the items (management reads the whole close list, it is not "mine");
  * ONLY `state == "todo"` - the person's own move (PR-U1: the one rule of mine_open below, shared with the todo answer). A task waiting for someone else's review (รอตรวจ) and a finished one (เสร็จแล้ว)
    never count. Each row counts once, on the page its `go` opens (a duty of eight branches = eight tasks on that page).

Nothing heavy: todo.build is COUNT/EXISTS on bc_* tables (no report query). The router caches the answer per user for 60 s
(core.ttl_cache) and every bom-center write drops it (its function name starts with `bc_`), so a number is right after a save.
A login pinned to a branch is scoped to that branch by todo.build; a role that cannot read หน้างาน gets an empty `pages`.
"""
from . import todo as _td
from datetime import datetime, time

from .clock import BKK, now_bkk

# the owner words of the role in `owner_th` (js/01-core.js _SHM_ROLE_OWNERS - keep the two in step)
ROLE_OWNERS = {
    "branch": ("สาขา",), "salessupport": ("ประสานงานขาย",), "analyst": ("ฝ่ายวิเคราะห์",), "accounting": ("บัญชี",),
    "innovation": ("ครัว",), "operation": ("ฝ่ายปฏิบัติการ",), "management": ("ผู้บริหาร", "ผู้ดูแลระบบ"), "warehouse": ("คลัง",),
}
# the roles whose open close steps are part of their own list (js/16-supplyhome.js: management reads them all, so they are not "mine")
CLOSE_ROLES = ("analyst", "salessupport", "accounting")


def is_mine(it: dict, role, modules) -> bool:
    """Is this task the viewer's? role None = auth off: the module rule decides (every module is held)."""
    words = ROLE_OWNERS.get(role) if role else None
    if words and it.get("owner_th"):
        return any(w in it["owner_th"] for w in words)
    return any(m in modules for m in (it.get("owner_modules") or ()))


def mine_open(d: dict, role, modules) -> list:
    """PR-U1 (2.7): THE list of the viewer's own open tasks - the one number every screen shows as "ยังไม่เสร็จ": the red badges (their sum),
    the launcher's tab, หน้างาน's "งานของฉัน" and the phone's ต้องทำวันนี้. From a todo.build answer `d` built for `role`:

      * the items, plus the open close steps for the roles that own steps of the month close (CLOSE_ROLES);
      * only the viewer's own move (`state == "todo"`) and the viewer's own duty (is_mine);
      * not a round whose cutoff is still to come (`upcoming`, todo._week_items): it is "ถัดไป", not a task of today;
      * the slip days a branch left behind are ONE task (หน้างาน shows them as one row "ใบตัดเบิกค้าง N วัน"), kept on the oldest day.
    """
    rows = list(d["items"]) + (list(d["close"]["steps"]) if role in CLOSE_ROLES else [])
    out, gaps = [], []
    for it in rows:
        if it.get("state") != "todo" or it.get("upcoming") or not is_mine(it, role, modules) or not page_key(it.get("go")):
            continue
        if str(it.get("key") or "").split(":")[0].startswith("issue_missing_"):
            gaps.append(it)
            continue
        out.append(it)
    if gaps:
        out.append(min(gaps, key=lambda g: str(g.get("slip_date") or "")))
    return out


def page_key(go: dict):
    """The page a task opens: "<tab>" or "<tab>:<pane>" (the key the department registry looks up)."""
    tab = (go or {}).get("tab")
    if not tab:
        return None
    pane = go.get("pane")
    return f"{tab}:{pane}" if pane else str(tab)


def build(con, role, modules, scoped_branch=None, today=None, now=None) -> dict:
    """{"pages": {key: n}, "at": iso} for a viewer of `role` holding `modules` (a set); `scoped_branch` = the branch name of a pinned login."""
    pages: dict = {}
    try:
        d = _td.build(con, None, None, scoped_branch, today=today, now=now, viewer_role=role)
    except (PermissionError, ValueError):
        d = None                                              # a pinned account whose branch is gone: no badges, no error
    total = 0
    mine = mine_open(d, role, modules) if d else []
    for it in mine:
        k = page_key(it.get("go"))
        pages[k] = pages.get(k, 0) + 1
        total += 1
    out = {"pages": pages, "total": total, "at": (now or now_bkk()).isoformat(timespec="seconds")}
    # 2026-10-31 (phone review item 4): the department hero of หน้าแรก, read from the SAME list (dept_hero.py); only the HQ departments
    # without the chain sales hero get one - management, operation, branch and franchise answers are unchanged
    from . import dept_hero as _dh
    if now is None and today is not None:                    # the clock todo.build read: 08:00 of `today` (tests)
        now = datetime.combine(today, time(8, 0), tzinfo=BKK)
    hero = _dh.build(con, d, role, modules, mine, now=now) if d else None
    if hero:
        out["hero"] = hero
    return out
