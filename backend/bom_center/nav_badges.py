# -*- coding: utf-8 -*-
"""Task count badges (PR-D4, owner 2026-10-21) - the red numbers on the department icons, the page tiles and the bottom navigation.

GET /api/nav/badges answers, for the signed-in account, `{"pages": {"<tab>" | "<tab>:<pane>": n}, "at": iso}`: how many tasks wait for
THIS person's own move on each page. It invents no business rule - every count is a row that หน้างาน (todo.py) already builds:

  * the open items of todo.build whose owner is the viewer's role (the same "mine" rule as หน้างาน, js/01-core.js _shmMine:
    the role's owner words in `owner_th`, else the item's `owner_modules` against the viewer's modules);
  * for the three roles that own steps of the month close (analyst, salessupport, accounting) the open steps they own - the
    "งานของฉัน" list of หน้างาน shows them next to the items (management reads the whole close list, it is not "mine");
  * ONLY `state == "todo"` - the person's own move. A task waiting for someone else's review (รอตรวจ) and a finished one (เสร็จแล้ว)
    never count. Each row counts once, on the page its `go` opens (a duty of eight branches = eight tasks on that page).

Nothing heavy: todo.build is COUNT/EXISTS on bc_* tables (no report query). The router caches the answer per user for 60 s
(core.ttl_cache) and every bom-center write drops it (its function name starts with `bc_`), so a number is right after a save.
A login pinned to a branch is scoped to that branch by todo.build; a role that cannot read หน้างาน gets an empty `pages`.
"""
from . import todo as _td
from .clock import now_bkk

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
    if d:
        rows = list(d["items"])
        if role in CLOSE_ROLES:
            rows += d["close"]["steps"]
        for it in rows:
            if it.get("state") != "todo" or not is_mine(it, role, modules):
                continue
            k = page_key(it.get("go"))
            if k:
                pages[k] = pages.get(k, 0) + 1
    return {"pages": pages, "at": (now or now_bkk()).isoformat(timespec="seconds")}
