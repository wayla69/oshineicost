# -*- coding: utf-8 -*-
"""หน้างาน — what each person has to do now, and where a month stands on its way to
being closed (BoM Center UX overhaul, 2026-10-07).

One GET /api/bom-center/todo answers both:
  * items — today's and this month's work, each tagged with the modules that own it,
    so the page shows a person their own work first and folds the rest away;
  * close — the retro-close checklist of ONE month, per branch: the version in force,
    the centre's report (list + deliveries), code links, begin and end counts,
    receipts confirmed, the monthly summary.
Every entry carries a deep link (`go`) for supplyGo (js/01-core.js), so a task opens
on the right pane with the month and the branch already chosen.

Cheap by construction: COUNT/EXISTS on bc_* tables, the order calendar per branch,
and at most ONE variance summary (only when a single branch is in context) — never
the monthly summary, never the fuzzy code matching. Not cached on purpose: several
writes do not invalidate the cache, and this list must be right straight after a save.
"""
import re
from datetime import date, datetime, timedelta

from .clock import BKK, now_bkk
from .fmt import NORMAL_OR_MEDIAN_TH, at_th as _fmt_at, day_at_th as _day_at, day_th as _fmt_day, lbl as _lbl, left_th as _left_th, month_th as _mth, round_day_th as _round_day, who as _who

CLOSE_TOLERANCE_DAYS = 2          # a "1st of the month" count may be dated up to two days later
RELEASE_DAY = 25                  # owner §10-2: the analyst releases by the 25th

# ── state: ยังไม่เสร็จ · รอตรวจ · เสร็จแล้ว (PR-TF, owner 2026-10-21) ──────────────────────────────────────────
# `status` is unchanged (todo | blocked | done | na). `state` is DERIVED once, after every item is built, from the viewer's role:
#   done   - status done or na;
#   review - someone else's move holds the item: status blocked, or the viewer is a WAITER of an item in REVIEW_KEYS and
#            the item is either the reviewer's open queue (status todo) or the viewer's own item marked `awaiting`;
#   todo   - everything else that is open (my move).
# REVIEW_KEYS: item key (base key; a branch suffix and a round suffix are dropped) -> (the roles that WAIT on it, kind).
# Every other key in this module is a plain duty of its owner, or a step that only waits when it is blocked.
#   kind "own"   - the waiter's OWN item: it is review only while the builder marked it `awaiting` (the waiter did its part;
#                  before that it is the waiter's move, after the other side acts it is done);
#   kind "queue" - the REVIEWER's open queue: todo for the reviewer (its move), review for the waiter whose work sits in it.
#   draft                 innovation   own    the kitchen's monthly draft once sent with "ส่งตรวจ" (status done)
#   review_pending        innovation   queue  the analyst's review of that draft
#   price_submit          salessupport own    the price files Sales Support sent while some still wait (status done)
#   price_review          salessupport queue  the analyst's review of those files
#   code_request_review   salessupport queue  new codes Sales Support reported, waiting for the analyst
#   claims_open           branch       own    the branch's claims waiting for Sales Support (status na)
#   claims_pending        branch       queue  Sales Support's review of those claims
#   pack_report_review    branch       queue  pack sizes a branch reported, waiting for the analyst
#   queue_pending         branch       queue  the warehouse's processing of the orders the branches sent
#   order_due             branch       own    the branch's order sent and not yet processed by the warehouse (status done)
#   acknowledge           analyst      queue  the released version waiting for management to acknowledge it
REVIEW_KEYS = {
    "draft": (("innovation",), "own"), "review_pending": (("innovation",), "queue"),
    "price_submit": (("salessupport",), "own"), "price_review": (("salessupport",), "queue"),
    "code_request_review": (("salessupport",), "queue"),
    "claims_open": (("branch",), "own"), "claims_pending": (("branch",), "queue"),
    "pack_report_review": (("branch",), "queue"), "queue_pending": (("branch",), "queue"),
    "order_due": (("branch",), "own"),
    "acknowledge": (("analyst",), "queue"),
}
STATE_TH = {"todo": "ยังไม่เสร็จ", "review": "รอตรวจ", "done": "เสร็จแล้ว"}

# PR-S1 (owner 2026-10-22, docs/STOCK_FIRST_PLAN.md): while close_settings.mode is `stock` build() leaves out every item below - the recipe
# (BoM) work the app does not take up yet. Nothing is deleted: in `full` mode the list is exactly what it was. Base keys (a branch suffix is dropped).
RECIPE_KEYS = (
    "draft",                    # the kitchen's monthly recipe draft and its "ส่งตรวจ"
    "review_pending",           # the analyst's review of that draft
    "unmatched_pos",            # the draft's menus not yet matched to POS codes
    "checks",                   # the release checks of the draft
    "release",                  # the analyst's release (ประกาศใช้) of the version
    "acknowledge",              # management's acknowledgement of the released version
    "sub_recipes",              # the prepared items that still lack a sub-recipe
    "code_review",              # the legacy recipe codes whose name differs from the register
    "recipe_check_units",       # the units checked against real purchases versus the recipes (ตรวจสูตรด้วยยอดซื้อ)
    "backlog",                  # the data-quality backlog ranked by the plates a recipe line touches
    "feedback_formula_excess",  # the task "ตรวจสูตร" the variance reasons make (opens ตรวจสูตรด้วยยอดซื้อ)
    "release_in_force",         # close step 1: the recipe version in force for the month
    "monthly_summary",          # close step 9: สรุปผลต่างรายเดือน (period use against the recipes); stock mode ends at ใบสรุปตัดเบิก
)


def is_recipe_item(it: dict) -> bool:
    """True for an item / close step of RECIPE_KEYS (a feedback task is named by its rule: its key is `feedback:<task key>`)."""
    key = _base_key(it.get("key"))
    if key == "feedback":
        key = f"feedback_{it.get('rule')}"
    return key in RECIPE_KEYS
_ROUND_SUFFIX = re.compile(r"_(1|2|salmon)$")


def _base_key(key: str) -> str:
    return _ROUND_SUFFIX.sub("", str(key or "").split(":")[0])


def item_state(it: dict, role: str = None) -> str:
    """The state of ONE item for a viewer of `role` (None = no viewer: nobody waits, so a review queue is simply todo)."""
    st = it.get("status")
    rule = REVIEW_KEYS.get(_base_key(it.get("key")))
    if rule and role in rule[0] and ((rule[1] == "queue" and st == "todo") or it.get("awaiting")):
        return "review"
    if st in ("done", "na"):
        return "done"
    if st == "blocked":
        return "review"
    return "todo"


def annotate_states(entries: list, role: str = None) -> None:
    for it in entries:
        it["state"] = item_state(it, role)
        it["state_th"] = STATE_TH[it["state"]]


# ── small helpers ─────────────────────────────────────────────────────────────
def _has(con, table: str) -> bool:
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone())


def _month_add(m: str, n: int) -> str:
    k = int(m[:4]) * 12 + int(m[5:7]) - 1 + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def _ym(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _default_month(con, today: date) -> str:
    """The newest month of sales that is over, else last calendar month."""
    try:
        from .release import last_full_month
        return last_full_month(con)
    except Exception:
        return _month_add(_ym(today), -1)


def _branch_rows(con) -> list:
    from .pilot import active_branches                      # the one definition of an active branch
    return active_branches(con)


def _names_th(names: list, n: int = 5) -> str:
    """'ก · ข · ค และอีก 12 สาขา' - a list of branch names that stays one line when it covers 30 branches."""
    names = list(names)
    return " · ".join(names[:n]) + (f" และอีก {len(names) - n} สาขา" if len(names) > n else "")


def _scalar(con, sql, args=()):
    r = con.execute(sql, args).fetchone()
    return r[0] if r else None


def _e(key, seq, group, title, status, detail="", count=None, owner=(), owner_th="", go=None, branch=None, **extra):
    d = {"key": key, "seq": seq, "group": group, "title": title, "status": status, "detail": detail or "",
         "count": count, "owner_modules": list(owner), "owner_th": owner_th, "go": dict(go or {})}
    if branch:                      # a per-branch item: its key names the branch so each is its own card
        d["key"] = f"{key}:{branch['branch_id']}"
        d["branch_id"], d["branch"] = branch["branch_id"], branch["name"]
        d["go"].setdefault("branch", branch["branch_id"])
    d.update(extra)
    return d


# ── the pieces ───────────────────────────────────────────────────────────────
def _setup_items(con) -> list:
    from .codes import review
    out = []
    n = _scalar(con, "SELECT COUNT(*) FROM bc_ingredient WHERE COALESCE(active,1)=1") or 0
    out.append(_e("master_seed", 10, "setup", "เติมทะเบียนวัตถุดิบจากข้อมูลในระบบ",
                  "done" if n else "todo", f"ทะเบียนวัตถุดิบ {n:,} รายการ" if n else "ยังไม่มีทะเบียนวัตถุดิบ",
                  owner=("bom.center",), owner_th="ฝ่ายวิเคราะห์", go={"tab": "bomcenter", "pane": "overview"}))
    if not n:
        for k, s, t in (("master_issues", 20, "ระบุหน่วยฐานและขนาดบรรจุ"), ("code_review", 30, "ตรวจรหัสสูตรเดิมกับทะเบียน"),
                        ("sub_recipes", 40, "สูตรกึ่งสำเร็จ (ไม่บังคับ)")):
            out.append(_e(k, s, "setup", t, "na", "รอเติมทะเบียนก่อน", owner=("bom.center",), owner_th="ฝ่ายวิเคราะห์",
                          go={"tab": "bomcenter", "pane": "overview"}))
        return out
    no_base = _scalar(con, "SELECT COUNT(*) FROM bc_ingredient WHERE COALESCE(active,1)=1"
                           " AND (base_unit IS NULL OR TRIM(base_unit)='')") or 0
    no_pack = _scalar(con, "SELECT COUNT(*) FROM bc_ingredient WHERE COALESCE(active,1)=1 AND kind='raw'"
                           " AND (qty_per_recv IS NULL OR qty_per_recv<=0)") or 0
    out.append(_e("master_issues", 20, "setup", "ระบุหน่วยฐานและขนาดบรรจุที่ยังว่าง",
                  "done" if not (no_base or no_pack) else "todo",
                  f"ไม่มีหน่วยฐาน {no_base} · ไม่มีขนาดบรรจุ {no_pack}" if (no_base or no_pack) else "ครบทุกรายการ",
                  count=(no_base + no_pack) or None, owner=("bom.center",), owner_th="ฝ่ายวิเคราะห์",
                  go={"tab": "bomcenter", "pane": "ing", "extra": {"prob": "ไม่มีหน่วยฐาน" if no_base else "ไม่มีขนาดบรรจุ"}}))
    try:
        c = review(con, suggestions=False)["counts"]
    except Exception:
        c = {"conflict": 0, "missing": 0}
    pend = (c.get("conflict", 0) or 0) + (c.get("missing", 0) or 0)
    out.append(_e("code_review", 30, "setup", "ตรวจรหัสสูตรเดิมที่ไม่ตรงกับทะเบียน",
                  "done" if not pend else "todo",
                  f"ชื่อไม่ตรง {c.get('conflict', 0)} · ไม่อยู่ในทะเบียน {c.get('missing', 0)}" if pend else "ตรวจครบแล้ว",
                  count=pend or None, owner=("bom.center",), owner_th="ฝ่ายวิเคราะห์",
                  go={"tab": "bomcenter", "pane": "overview", "anchor": "bmcIssuesCard"}))
    missing_prep = _scalar(con, "SELECT COUNT(*) FROM bc_ingredient WHERE COALESCE(active,1)=1 AND kind='prepared'"
                                " AND code NOT IN (SELECT prepared_code FROM bc_prep_recipe)") or 0
    out.append(_master_gaps_item(con))
    out.append(_e("sub_recipes", 40, "setup", "นำเข้าสูตรกึ่งสำเร็จจากไฟล์ครัว (ไม่บังคับ)",
                  "done" if not missing_prep else "todo",
                  f"ยังไม่มีสูตร {missing_prep} รายการ · ช่วยให้แผนสั่งแนะนำวัตถุดิบดิบในสูตรได้" if missing_prep else "มีสูตรครบ",
                  count=missing_prep or None, owner=("bom.center",), owner_th="ฝ่ายวิเคราะห์ / ครัว",
                  go={"tab": "bomcenter", "pane": "prep"}))
    return out


GAP_LABEL = {"fresh_no_shelf": "ของสด / แช่เย็นที่ยังไม่มีอายุการเก็บ", "no_moq": "รายการที่ยังไม่มีขั้นต่ำการสั่ง",
             "loss_zero": "เนื้อสัตว์ / ผักที่สูญเสีย 0%"}


def _master_gaps_item(con) -> dict:
    """PR-B4a (1.5): the card "ข้อมูลตั้งต้นที่ยังว่าง" - how many items the order logic reads a blank field of, with a link to the
    register filtered to them and the items that cost the most first (master_gaps). No value is filled in by the system."""
    from .master_gaps import gaps
    g = gaps(con)
    c = g["counts"]
    n = c["items"]
    lines = []
    for k in ("fresh_no_shelf", "no_moq", "loss_zero"):
        lst = g["lists"][k]
        lines.append({"key": k, "label": GAP_LABEL[k], "count": lst["count"], "items": lst["items"],
                      "go": {"tab": "bomcenter", "pane": "ing", "extra": {"gap": k}}})
    return _e("master_gaps", 35, "setup", "ข้อมูลตั้งต้นที่ยังว่าง",
              "todo" if n else "done",
              "ยอดแนะนำอ่านค่าเหล่านี้ · แต่ละกลุ่มเรียงตามมูลค่าที่ซื้อมากที่สุดก่อน" if n else "กรอกครบทุกรายการ",
              count=n or None, owner=("bom.center",), owner_th="ฝ่ายวิเคราะห์",
              go={"tab": "bomcenter", "pane": "ing", "extra": {"gap": "fresh_no_shelf" if c["fresh_no_shelf"] else
                                                                "no_moq" if c["no_moq"] else "loss_zero"}},
              gaps=lines)


_SHORT_UNIT = {"กิโลกรัม": "กก.", "มิลลิลิตร": "มล."}


def _unit_review_items(con) -> list:
    """PR-U2 — the analyst's two review queues of the unit register, each present only while something waits:
    the average weight per piece the receipts disagree with, and the pack sizes the branches reported."""
    from .pack_reports import listing, pending_count
    from .units import avg_proposals, fmt_num
    out = []
    owner = dict(owner=("bom.center", "bom.prices"), owner_th="ฝ่ายวิเคราะห์")
    try:
        props = [r for r in avg_proposals(con) if r["proposal"]]
    except Exception:                                      # the list must open even if the receipts cannot be read
        props = []
    if props:
        def one(r):
            u = _SHORT_UNIT.get(r["parent"], r["parent"])
            return (f"{r['name']}: ใช้ {fmt_num(round(r['current'], 2))} {u} · รับจริง ≈ {fmt_num(round(r['observed'], 2))} {u}"
                    f" ({r['diff_pct']:+.1f}%)")
        out.append(_e("unit_avg_review", 24, "setup", f"ตรวจค่าเฉลี่ยน้ำหนักต่อชิ้นจากการรับจริง ({len(props)} รายการ)", "todo",
                      " · ".join(one(r) for r in props[:3]) + (f" · และอีก {len(props) - 3} รายการ" if len(props) > 3 else ""),
                      count=len(props), go={"tab": "bomcenter", "pane": "conv", "anchor": "bmcAvgCard"}, **owner))
    try:
        n = pending_count(con)
        groups = listing(con, "pending") if n else []
    except Exception:
        n, groups = 0, []
    if n:
        det = " · ".join(f"{g['name'] or g['ingredient_code']}: ในระบบ {g['system_text']} · สาขาแจ้ง {g['reported_text']}"
                         for g in groups[:3])
        out.append(_e("pack_report_review", 25, "setup", f"ตรวจขนาดแพ็คที่สาขาแจ้ง ({n} เรื่อง)", "todo",
                      det + (f" · และอีก {n - 3} เรื่อง" if n > 3 else ""), count=n,
                      go={"tab": "bomcenter", "pane": "conv", "anchor": "bmcPackCard"}, **owner))
    return out


def _code_request_items(con) -> list:
    """PR-Y — the analyst's queue of new codes for existing items that Sales Support reported; present only while one waits."""
    from .aliases import listing, pending_count
    try:
        n = pending_count(con)
        rows = listing(con, "pending", limit=3) if n else []
    except Exception:                                      # the list must open even if the table cannot be read
        return []
    if not n:
        return []
    det = " · ".join(f"{r['item_name'] or r['ingredient_code']}: {r['old_code'] or '-'} → {r['new_code']} ({r['system_th']})" for r in rows)
    return [_e("code_request_review", 27, "setup", f"ตรวจรหัสใหม่ที่ประสานงานขายแจ้ง ({n} เรื่อง)", "todo",
               det + (f" · และอีก {n - 3} เรื่อง" if n > 3 else ""), count=n, owner=("bom.center", "bom.prices"),
               owner_th="ฝ่ายวิเคราะห์", go={"tab": "bomcenter", "pane": "ing", "anchor": "bmcCodeReqCard"})]


def _recipe_check_items(con, today: date) -> list:
    """PR-X — the analyst's check of the units against real purchases: present only while a month that has the company
    branches' purchases holds an ingredient bought ten times more (or less) than its recipes say."""
    from .recipe_check import x10_rows
    rows, m = x10_rows(con, today)
    if not rows:
        return []
    shown = " · ".join(f"{r['name']}: ซื้อ {r['bought']:,.0f} {r['base_unit'] or ''} · ตามสูตร {r['recipe']:,.0f} {r['base_unit'] or ''}".replace("  ", " ").rstrip()
                       for r in rows[:3])
    return [_e("recipe_check_units", 26, "setup", f"ตรวจหน่วยของวัตถุดิบที่ยอดซื้อต่างจากสูตรเกิน 10 เท่า ({len(rows)} รายการ)", "todo",
               f"เดือน{_mth(m)} · ต่อยอดขาย ฿100,000 · {shown}" + (f" · และอีก {len(rows) - 3} รายการ" if len(rows) > 3 else ""), count=len(rows),
               owner=("bom.center",), owner_th="ฝ่ายวิเคราะห์", go={"tab": "bomcenter", "pane": "rcheck", "month": m})]


FEEDBACK_GO = {
    "formula_excess": lambda t: {"tab": "bomcenter", "pane": "rcheck", "month": t["month"], "extra": {"item": t["item"]}},
    "miscount": lambda t: {"tab": "orderhub", "pane": "month", "month": t["month"], "branch": t["branches"][0]["branch_id"]},
    "stock_less": lambda t: {"tab": "orderhub", "pane": "issuevar", "month": t["month"], "branch": t["branches"][0]["branch_id"]},
}


def _feedback_go(con, t: dict) -> dict:
    """PR-S4: where a feedback task opens. The month summary is a recipe page: in stock mode the miscount task opens the month-close
    summary (ใบสรุปตัดเบิก / กระทบยอด, system stock against the count) of that branch instead."""
    from .close_settings import stock_mode
    if t["rule"] == "miscount" and stock_mode(con):
        return {"tab": "bomcenter", "pane": "issue", "month": t["month"], "branch": t["branches"][0]["branch_id"]}
    return FEEDBACK_GO[t["rule"]](t)


def _feedback_items(con) -> list:
    """Batch 4b (2.6): the tasks the variance and order reasons make (feedback.evaluate): one item per task, closed by an
    acknowledgement or when the rule stops holding. Each says who owns it; ตรวจสูตร links to ตรวจสูตรด้วยยอดซื้อ for the item."""
    from .feedback import evaluate
    try:
        tasks = evaluate(con)
    except Exception:                                      # the list must open even if the reasons cannot be read
        return []
    out = []
    for t in tasks:
        out.append(_e(f"feedback_{t['rule']}", 340, "week", t["title"], "todo", t["detail"], owner=tuple(t["owner_modules"]),
                      owner_th=t["owner_th"], go=_feedback_go(con, t), feedback_key=t["key"], rule=t["rule"], subject=t["subject"],
                      month=t["month"], branch_names=[b["name"] for b in t["branches"]], ack_modules=t["ack_modules"]))
        out[-1]["key"] = f"feedback:{t['key']}"
    return out


def _backlog_item(con) -> list:
    """Batch 4b (3.5): the analyst's data-quality backlog, the top 10 by the plates each item touches (backlog.build)."""
    from .backlog import build
    try:
        b = build(con)
    except Exception:
        return []
    if not b["total"]:
        return []
    return [_e("backlog", 36, "setup", f"งานแก้ข้อมูลค้างเรียงตามผลกระทบ ({b['total']} รายการ)", "todo",
               "แก้รายการบนสุดก่อน", count=b["total"], owner=("bom.center",), owner_th="ฝ่ายวิเคราะห์",
               go={"tab": "bomcenter", "pane": "overview", "anchor": "bmcBacklogCard"}, backlog=b["top"], backlog_window=b["window"], backlog_counts=b["counts"],
               backlog_total=b["total"])]


def _merge_gaps_into_backlog(items: list, backlog: list) -> list:
    """PR-U2 (3): ONE data-quality task for the analyst. While the impact-ranked backlog has rows, the card "ข้อมูลตั้งต้นที่ยังว่าง"
    is not a second task: its blank-field lines ride on the backlog card (the same `gaps` table) and the item leaves the list, so
    หน้างาน and the badges (nav_badges.mine_open counts rows) see the work once. With no backlog the gaps card stays as it is."""
    if not backlog:
        return items
    bk = backlog[0]
    gp = next((it for it in items if it.get("key") == "master_gaps"), None)
    if gp is not None:
        if gp.get("status") == "todo" and gp.get("gaps"):
            bk["gaps"] = gp["gaps"]
            bk["gaps_count"] = gp.get("count")
        items = [it for it in items if it is not gp]
    return items + [bk]


def _dish_cost_item(con, month: str) -> list:
    """PR-S2: the analyst's monthly dish cost list. A task until every menu sold in the month holds a cost from the list (stock mode keeps it:
    it is not a recipe item)."""
    from . import dish_cost as dc
    if not month or month < dc.MIN_MONTH or not _has(con, "bc_sku_cost_list"):
        return []
    try:
        n = len(dc.sold_without_cost(con, month))
    except Exception:                                       # a database without the sales tables: nothing to ask for
        return []
    title = f"นำเข้าต้นทุนต่อจานเดือน{_mth(month)}"
    go = {"tab": "bomcenter", "pane": "dishcost", "month": month}
    if n:
        return [_e("dish_cost_month", 102, "month", f"{title} (เมนูที่ขายแต่ไม่มีต้นทุน {n})", "todo",
                   "ดาวน์โหลดแม่แบบ กรอกต้นทุน แล้วนำเข้า", count=n, owner=("bom.center",), owner_th="ฝ่ายวิเคราะห์", go=go)]
    last = con.execute("SELECT at, who FROM bc_audit WHERE action='dish_cost_import' AND row_key=? ORDER BY id DESC LIMIT 1",
                       (month,)).fetchone()
    return [_e("dish_cost_month", 102, "month", title, "done",
               "ครบ" + (f" · นำเข้าล่าสุด {_fmt_at(last[0])} โดย {_who(con, last[1])}" if last else ""),
               owner=("bom.center",), owner_th="ฝ่ายวิเคราะห์", go=go)]


def _month_items(con, month: str, today: date) -> list:
    from .release import checks_summary
    out = []
    this_m = _ym(today)
    eff = _month_add(this_m, 1)
    n = _scalar(con, "SELECT COUNT(*) FROM bc_ingredient_price WHERE month=? AND source='purchase_avg'", (month,)) or 0
    last = con.execute("SELECT at, who FROM bc_audit WHERE action='price_import' ORDER BY id DESC LIMIT 1").fetchone()
    out.append(_e("price_month", 100, "month", f"นำเข้าราคาซื้อจริงเดือน{_mth(month)}", "done" if n else "todo",
                  (f"ราคาซื้อจริง {n:,} รหัส" + (f" · นำเข้าล่าสุด {_fmt_at(last[0])} โดย {_who(con, last[1])}" if last else ""))
                  if n else "นำเข้าจากไฟล์ที่ประสานงานขายส่งมา หรือนำเข้าเอง",
                  owner=("bom.center",), owner_th="ฝ่ายวิเคราะห์", go={"tab": "bomcenter", "pane": "price", "month": month}))
    out += _dish_cost_item(con, month)
    if _has(con, "bc_price_submission"):
        subs = con.execute("SELECT COUNT(*), MAX(submitted_at) FROM bc_price_submission WHERE month=?", (month,)).fetchone()
        n_sub = subs[0] or 0
        if n_sub:
            lastsub = con.execute("SELECT submitted_at, submitted_by FROM bc_price_submission WHERE month=?"
                                  " ORDER BY sub_id DESC LIMIT 1", (month,)).fetchone()
            sub_status, sub_detail = "done", (f"ส่งแล้ว {n_sub} ไฟล์ · ล่าสุด {_fmt_at(lastsub[0])}"
                                              f" โดย {_who(con, lastsub[1])}")
        elif n:
            sub_status, sub_detail = "na", "นำเข้าราคาของเดือนนี้แล้ว ไม่ต้องส่งไฟล์"
        else:
            sub_status, sub_detail = "todo", "ไฟล์รายงานจ่ายสินค้าภายใน หรือไฟล์ตามแม่แบบไฟล์ราคา"
        pend = _scalar(con, "SELECT COUNT(*) FROM bc_price_submission WHERE status='pending'") or 0
        out.append(_e("price_submit", 95, "month", f"ส่งไฟล์ราคาเดือน{_mth(month)} ให้ฝ่ายวิเคราะห์", sub_status, sub_detail,
                      owner=("supply.ordering",), owner_th="ประสานงานขาย", awaiting=bool(n_sub and pend),
                      go={"tab": "bomcenter", "pane": "pricefile", "month": month, "anchor": "bmcPriceSubmitCard"}))
        if pend:
            out.append(_e("price_review", 98, "month", f"ตรวจและนำเข้าไฟล์ราคาที่ประสานงานขายส่งมา ({pend} ไฟล์)", "todo",
                          "ดูตัวอย่างก่อนบันทึก แล้วนำเข้าหรือส่งกลับพร้อมเหตุผล", count=pend,
                          owner=("bom.center",), owner_th="ฝ่ายวิเคราะห์",
                          go={"tab": "bomcenter", "pane": "price", "month": month, "anchor": "bmcPriceSubList"}))
    dr = con.execute("SELECT release_id, label, effective_month, status, price_month, submitted_by, submitted_at"
                     " FROM bc_release WHERE kind='monthly'"
                     " AND status IN ('draft','review') ORDER BY release_id DESC LIMIT 1").fetchone()
    released_eff = _scalar(con, "SELECT release_id FROM bc_release WHERE kind='monthly' AND status='released'"
                                " AND effective_month=?", (eff,))
    if dr:
        rid, label, deff, rstatus, pm, sub_by, sub_at = dr
        n_rec = _scalar(con, "SELECT COUNT(*) FROM bc_recipe WHERE release_id=?", (rid,)) or 0
        kitchen = dict(owner=("bom.recipes",), owner_th="ครัว",
                       go={"tab": "bomcenter", "pane": "rec", "extra": {"release_id": rid}})
        if rstatus == "review":
            out.append(_e("draft", 110, "month", f"สร้างสูตรเดือน{_mth(deff)} และส่งตรวจ", "done",
                          f"ส่งตรวจแล้ว {_fmt_at(sub_at)} โดย {_who(con, sub_by)}", awaiting=True, **kitchen))
            out.append(_e("review_pending", 115, "month", f"สูตรรอตรวจ: {_lbl(label)}", "todo",
                          f"มีผลเดือน{_mth(deff)} · ตรวจรายการ แก้ไข หรือส่งกลับให้ครัว",
                          owner=("bom.release",), owner_th="ฝ่ายวิเคราะห์",
                          go={"tab": "bomcenter", "pane": "rel", "extra": {"release_id": rid}}))
        else:
            out.append(_e("draft", 110, "month", f"สร้างสูตรเดือน{_mth(deff)} และส่งตรวจ", "todo",
                          f"ร่าง {_lbl(label)} · {n_rec} สูตร · เมื่อครบแล้วกด “ส่งตรวจ”", **kitchen))
        unm = _scalar(con, "SELECT COUNT(*) FROM bc_release_check WHERE release_id=? AND check_code='unmatched'", (rid,)) or 0
        out.append(_e("unmatched_pos", 120, "month", "จับคู่เมนูในสูตรกับรหัส POS", "done" if not unm else "todo",
                      f"ยังไม่จับคู่ {unm} เมนู" if unm else "จับคู่ครบแล้ว", count=unm or None,
                      owner=("bom.center",), owner_th="ฝ่ายวิเคราะห์", go={"tab": "bomcenter", "pane": "rec", "extra": {"release_id": rid}}))
        cs = checks_summary(con, rid)
        ran = any(r["check_code"] != "UNM" for r in cs["rows"])
        blocks = cs["open_blocks"]
        if not ran:
            out.append(_e("checks", 130, "month", "ตรวจรายการของร่าง", "todo", "ยังไม่ได้ตรวจ",
                          owner=("bom.release",), owner_th="ฝ่ายวิเคราะห์",
                          go={"tab": "bomcenter", "pane": "rel", "anchor": "bmcRlChecks", "extra": {"release_id": rid}}))
        else:
            out.append(_e("checks", 130, "month", "แก้ไขหรือยอมรับรายการที่ไม่ผ่านการตรวจ", "done" if not blocks else "todo",
                          f"ติด {blocks} ข้อ · ข้อสังเกต {cs['warnings']}" if blocks else f"ผ่านการตรวจ · ข้อสังเกต {cs['warnings']}",
                          count=blocks or None, owner=("bom.release",), owner_th="ฝ่ายวิเคราะห์ / ครัว",
                          go={"tab": "bomcenter", "pane": "rel", "anchor": "bmcRlChecks", "extra": {"release_id": rid}}))
        backdated = deff <= this_m
        deadline = date.fromisoformat(_month_add(deff, -1) + f"-{RELEASE_DAY:02d}")
        left = (deadline - today).days
        if backdated:
            when = "ประกาศใช้ย้อนหลัง ต้องระบุเหตุผล"
        elif left >= 0:
            when = f"ประกาศใช้ภายในวันที่ {RELEASE_DAY} (เหลือ {left} วัน)"
        else:
            when = f"เกินกำหนดวันที่ {RELEASE_DAY} มาแล้ว {-left} วัน"
        out.append(_e("release", 140, "month", f"ประกาศใช้เวอร์ชัน {_lbl(label)}",
                      "blocked" if (blocks or not ran) else "todo",
                      ("รอผ่านการตรวจก่อน · " if (blocks or not ran) else "") + when,
                      owner=("bom.release",), owner_th="ฝ่ายวิเคราะห์", urgent=bool(not backdated and left < 0 and not blocks),
                      go={"tab": "bomcenter", "pane": "rel", "extra": {"release_id": rid}}))
    elif released_eff:
        out.append(_e("draft", 110, "month", f"เวอร์ชันสูตรเดือน{_mth(eff)}", "done", "ประกาศใช้แล้ว",
                      owner=("bom.center",), owner_th="ฝ่ายวิเคราะห์", go={"tab": "bomcenter", "pane": "rel"}))
    else:
        out.append(_e("draft", 110, "month", f"สร้างสูตรเดือน{_mth(eff)} และส่งตรวจ", "todo",
                      "ดาวน์โหลดแม่แบบสูตรตามระดับราคา กรอกสูตรแล้วนำเข้าไฟล์สูตรตามระดับราคา หรือสร้างร่างจากเวอร์ชันที่ประกาศใช้แล้ว",
                      owner=("bom.recipes",), owner_th="ครัว", go={"tab": "bomcenter", "pane": "rec"}))
    ack = con.execute("SELECT release_id, label, released_by FROM bc_release WHERE kind='monthly' AND status='released'"
                      " AND acknowledged_at IS NULL ORDER BY release_id DESC LIMIT 1").fetchone()
    any_rel = _scalar(con, "SELECT COUNT(*) FROM bc_release WHERE kind='monthly' AND status='released'") or 0
    out.append(_e("acknowledge", 150, "month", "ผู้บริหารรับทราบเวอร์ชันที่ประกาศใช้" if not ack else f"รับทราบเวอร์ชัน {_lbl(ack[1])}",
                  "todo" if ack else ("done" if any_rel else "na"),
                  f"ประกาศใช้โดย {_who(con, ack[2])} · อ่านผลกระทบแล้วกดรับทราบ" if ack else ("รับทราบครบทุกเวอร์ชัน" if any_rel else "ยังไม่มีเวอร์ชันที่ประกาศใช้"),
                  owner=("bom.audit",), owner_th="ผู้บริหาร",
                  go={"tab": "bomcenter", "pane": "rel", "extra": {"release_id": ack[0]} if ack else {}}))
    return out


def _slip_info(con, bid: int, today: date) -> dict:
    """A branch's issue slips as หน้างาน needs them: today's and yesterday's status, whether the branch has
    started (a month-end count or any slip), and the days since the last count / the last week it left open."""
    from .issues import status_days
    d_prev = today - timedelta(days=1)
    t, y = status_days(con, bid, d_prev, today)[::-1]
    from .started import branch_start
    last = con.execute("SELECT MAX(count_date) FROM bc_stock_count WHERE branch_id=? AND kind IN ('begin','end')"
                       " AND COALESCE(status,'submitted')='submitted'", (bid,)).fetchone()[0]
    started = bool(last) or bool(con.execute("SELECT 1 FROM bc_issue_slip WHERE branch_id=? LIMIT 1", (bid,)).fetchone())
    lo = max(date.fromisoformat(last), today - timedelta(days=7)) if last else today - timedelta(days=7)
    first = branch_start(con, bid)                      # PR-U1: a day before the branch started is never a slip left behind
    if first and date.fromisoformat(first) > lo:
        lo = date.fromisoformat(first)
    gaps = [x["date"] for x in status_days(con, bid, lo, d_prev) if x["status"] in ("missing", "draft")] \
        if started and lo <= d_prev else []
    return {"today": t, "yesterday": y, "started": started, "gaps": gaps}


def _count_items(con, b: dict, today: date) -> list:
    """The optional weekly count of the class-A items ("นับเสริมรายสัปดาห์ (ไม่ใช่การปิดเดือน)", batch 4b). Owner 2026-10-13: a month is closed
    only by a full count of every item, so there is no task to close a month on a partial count and no quarter-end rule; this item exists
    only while the switch is on, and never as an obligation of the month close."""
    from .stock import CYCLE_TH, count_plan
    try:
        plan = count_plan(con, b["branch_id"], today)
    except Exception:
        return []
    if not plan["cycle_counts"] or plan["first"]:
        return []
    done = not plan["cycle_due"]
    return [_e("cycle_count", 334, "week", CYCLE_TH, "done" if done else "todo",
               (f"นับแล้ว {_fmt_day(plan['cycle_last'])} · สัปดาห์นี้ครบแล้ว" if done else
                ("นับเพิ่มเฉพาะของแพง · สัปดาห์นี้ยังไม่นับ"
                 + (f" · ล่าสุด {_fmt_day(plan['cycle_last'])}" if plan["cycle_last"] else ""))),
               owner=("branch.stock",), owner_th="สาขา", branch=b, go={"tab": "branchstock", "pane": "count"})]


def _month_end_view(con, bid: int):
    """The latest period of a branch as the month-close task reads it: {begin_date, end_count_id, complete, needs_reason, stock}.
    Stock mode (PL1 #2): the reasons the branch can give are the สิ้นเดือน tab's lines (issue_reports.reconcile: system stock against
    the count, the same reasons table), never the recipe period (variance.period) - that tab is not offered in stock mode and with
    no released recipe usage every item used would read as a gap nobody can answer. Full mode keeps variance.period."""
    from .close_settings import stock_mode
    if stock_mode(con):
        from .issue_reports import reconcile
        from .stock import periods as _periods
        ps = _periods(con, bid)
        if not ps:
            return None
        e = ps[-1][1]
        try:
            r = reconcile(con, bid, e["count_id"])
        except ValueError:
            return None
        return {"begin_date": r["period"]["begin_date"], "end_count_id": r["period"]["end_count_id"],
                "complete": bool(r["status"]["receipts_confirmed"]), "needs_reason": int(r["totals"]["needs_reason"]), "stock": True}
    from .variance import period
    try:
        v = period(con, bid, summary_only=True)
    except ValueError:
        return None
    return v


def _week_items(con, branches: list, today: date, single: bool, centre: bool, now=None) -> list:
    from .forecast import calendar, next_deliveries
    from .hub import delivery_of
    from .orders import STATUS_TH
    out = []
    slips = {b["branch_id"]: _slip_info(con, b["branch_id"], today) for b in branches}
    if centre:
        users = {}
        if _has(con, "app_user"):
            for b, active in con.execute("SELECT branch, active FROM app_user WHERE role='branch'"):
                users[b] = users.get(b) or bool(active)
        no_acc = [b for b in _branch_rows(con) if not users.get(b["name"])]      # PR-U2: every active branch, not the pilot four
        out.append(_e("branch_account", 200, "week", "สร้างผู้ใช้ของสาขา", "done" if not no_acc else "todo",
                      ("ยังไม่มีผู้ใช้: " + _names_th(b["name"] for b in no_acc)) if no_acc else "ทุกสาขามีผู้ใช้แล้ว",
                      count=len(no_acc) or None, owner=("system.admin",), owner_th="ผู้ดูแลระบบ",
                      go={"tab": "settings", "pane": "users"}))
        unfilled = [b for b in branches if not all(calendar(con, b["branch_id"])[r]["filled"] for r in ("1", "2", "salmon"))]
        out.append(_e("calendar", 210, "week", "กำหนดรอบส่งของสาขา (รอบ 1 · รอบ 2 · แซลมอนสด)",
                      "done" if not unfilled else "todo",
                      ("ยังใช้ค่าเริ่มต้น: " + _names_th(b["name"] for b in unfilled)) if unfilled else "ครบทุกสาขา",
                      count=len(unfilled) or None, owner=("supply.ordering",), owner_th="ประสานงานขาย",
                      go={"tab": "bomcenter", "pane": "cal"}))
        q = con.execute("SELECT COUNT(*), MIN(cycle_date), SUM(COALESCE(late,0)) FROM bc_order"
                        " WHERE status='submitted'").fetchone()
        late = bool(q[0] and q[1] and q[1] < today.isoformat())          # past its cutoff day: the 14:00 target has gone
        n_late = int(q[2] or 0)                                           # submitted after the 13:15 cutoff (late fee)
        out.append(_e("queue_pending", 220, "week", "ระบุยอดส่งจริงของใบสั่งซื้อที่สาขาส่งมา", "todo" if q[0] else "done",
                      (f"รอ {q[0]} ใบ ภายใน 14:00 ของวันตัดรอบ" + (f" · ส่งช้า {n_late} ใบ (มีค่าปรับ)" if n_late else "")
                       + (" · มีใบที่เลยวันตัดรอบแล้ว" if late else "")) if q[0] else "ไม่มีใบสั่งซื้อค้าง",
                      count=q[0] or None, owner=("supply.warehouse",), owner_th="คลัง", urgent=late,    # owner 2026-10-08
                      late_orders=n_late, go={"tab": "orderhub", "pane": "queue"}))
        nc = _scalar(con, "SELECT COUNT(*) FROM bc_claim WHERE status='submitted'") if _has(con, "bc_claim") else 0
        out.append(_e("claims_pending", 225, "week", "พิจารณาเคลมสินค้าที่สาขาแจ้ง", "todo" if nc else "done",
                      f"รอพิจารณา {nc} เรื่อง" if nc else "ไม่มีเคลมค้าง",
                      count=nc or None, owner=("supply.ordering",), owner_th="ประสานงานขาย",
                      go={"tab": "orderhub", "pane": "claims"}))
        if any(r[1] == "accounting_keyed_at" for r in con.execute("PRAGMA table_info(bc_order)")):
            nk = _scalar(con, "SELECT COUNT(*) FROM bc_order WHERE status IN ('processed','received')"
                              " AND accounting_keyed_at IS NULL")
            out.append(_e("accounting_keyed", 227, "week", "คีย์ใบสั่งซื้อที่คลังจัดส่งแล้วเข้าระบบบัญชี",
                          "todo" if nk else "done",
                          f"ยังไม่คีย์ {nk} ใบ · ดาวน์โหลดสรุปรวมตามวันส่ง (Excel) แล้วกด “คีย์บัญชีแล้ว”" if nk else "คีย์ครบทุกใบ",
                          count=nk or None, owner=("supply.ordering",), owner_th="ประสานงานขาย",
                          go={"tab": "orderhub", "pane": "queue"}))
        # ใบตัดเบิก: the branches that have started and did not confirm yesterday's slip
        open_y = [b["name"] for b in branches if slips[b["branch_id"]]["started"]
                  and slips[b["branch_id"]]["yesterday"]["status"] in ("missing", "draft")]
        out.append(_e("issue_missing", 228, "week", "ติดตามสาขาที่ยังไม่ส่งใบตัดเบิกของเมื่อวาน",
                      "todo" if open_y else "done",
                      ("ยังไม่ส่ง: " + " · ".join(open_y)) if open_y else "ทุกสาขาที่เริ่มใช้งานแล้วยืนยันใบตัดเบิกของเมื่อวานแล้ว",
                      count=len(open_y) or None, owner=("supply.ordering", "ops.report"),
                      owner_th="ประสานงานขาย", urgent=bool(open_y), branch_names=open_y,
                      go={"tab": "orderhub", "pane": "issues", "extra": {"day": (today - timedelta(days=1)).isoformat()}}))
        # ใบตัดเบิก vs สูตร: a branch issuing far over the recipes day after day (waste or loss) — Operations follows it up
        odd = []
        try:
            from .issue_reports import OVER_PCT, STREAK_DAYS, anomalies
            odd = anomalies(con, today - timedelta(days=1), 7, today=today)
        except Exception:                                    # the list must open even if the report cannot be built
            odd = []
        shown = " · ".join(f"{a['branch']} ({', '.join(a['items'][:3])})" for a in odd)
        from .close_settings import stock_mode as _stock_mode
        _sm = _stock_mode(con)                               # PR-S4: with no recipe the word is ปกติ (the branch's own or the all-branch median)
        out.append(_e("issue_anomaly", 229, "week", "ตรวจสอบสาขาที่เบิกเกินปกติติดต่อกัน" if _sm else "ตรวจสอบสาขาที่เบิกเกินสูตรติดต่อกัน",
                      "todo" if odd else "done",
                      ((f"เบิกเกิน{NORMAL_OR_MEDIAN_TH}มากกว่า {OVER_PCT:.0f}% ติดต่อกันอย่างน้อย {STREAK_DAYS} วัน: {shown}" if _sm
                        else f"เบิกเกินสูตรมากกว่า {OVER_PCT:.0f}% ติดต่อกันอย่างน้อย {STREAK_DAYS} วัน: {shown}") if odd
                       else ("ไม่พบการเบิกเกินปกติติดต่อกันใน 7 วันล่าสุด" if _sm else "ไม่พบการเบิกเกินสูตรติดต่อกันใน 7 วันล่าสุด")),
                      count=len(odd) or None, owner=("supply.ordering", "ops.report"),
                      owner_th="ประสานงานขาย / ฝ่ายปฏิบัติการ", urgent=bool(odd), branch_names=[a["branch"] for a in odd],
                      anomalies=[{"branch_id": a["branch_id"], "branch": a["branch"], "items": a["items"][:3],
                                  "max_pct": a["max_pct"]} for a in odd],
                      go={"tab": "orderhub", "pane": "issuevar", **({"branch": odd[0]["branch_id"]} if odd else {})}))
        # PR-B4a (1.4): the other direction - a class-A item issued far UNDER the recipes day after day (the slip left blank, or the
        # branch used the item without writing it) makes the system stock run high and the next order too small
        low = []
        try:
            from .issue_reports import STREAK_DAYS as _SD, UNDER_PCT
            low = anomalies(con, today - timedelta(days=1), 7, today=today, direction="under")
        except Exception:
            low = []
        shown_low = " · ".join(f"{a['branch']} ({', '.join(a['items'][:3])})" for a in low)
        from .issue_reports import UNDER_PCT_STOCK
        out.append(_e("issue_under", 230, "week", "ตรวจสอบสาขาที่เบิกต่ำกว่าปกติติดต่อกัน" if _sm else "ตรวจสอบสาขาที่เบิกน้อยกว่าสูตรติดต่อกัน",
                      "todo" if low else "done",
                      ((f"เบิกต่ำกว่า{NORMAL_OR_MEDIAN_TH}เกิน {UNDER_PCT_STOCK:.0f}% ติดต่อกันอย่างน้อย {_SD} วัน: {shown_low}" if _sm
                        else f"เบิกน้อยกว่าสูตรเกิน {UNDER_PCT:.0f}% ติดต่อกันอย่างน้อย {_SD} วัน: {shown_low}") if low
                       else ("ไม่พบการเบิกต่ำกว่าปกติติดต่อกันใน 7 วันล่าสุด" if _sm else "ไม่พบการเบิกน้อยกว่าสูตรติดต่อกันใน 7 วันล่าสุด")),
                      count=len(low) or None, owner=("supply.ordering", "ops.report"),
                      owner_th="ประสานงานขาย / ฝ่ายปฏิบัติการ", urgent=False, branch_names=[a["branch"] for a in low],
                      anomalies=[{"branch_id": a["branch_id"], "branch": a["branch"], "items": a["items"][:3],
                                  "max_pct": a["max_pct"]} for a in low],
                      go={"tab": "orderhub", "pane": "issuevar", **({"branch": low[0]["branch_id"]} if low else {})}))
    for b in branches:
        bid = b["branch_id"]
        info = slips[bid]
        st_today = info["today"]
        slip_ok = st_today["status"] in ("confirmed", "late")
        hhmm = None
        if st_today["status"] in ("confirmed", "late"):
            at = st_today["confirmed_at"]
            hhmm = datetime.fromisoformat(at).astimezone(BKK).strftime("%H:%M") if at else "-"
            slip_detail = f"ส่งแล้ว เวลา {hhmm} โดย {_who(con, st_today['confirmed_by'])}"
        elif st_today["status"] == "draft":
            slip_detail = "บันทึกไว้ก่อนแล้ว · ส่งใบตัดเบิกก่อนส่งใบสั่งซื้อ"
        else:
            slip_detail = "ยังไม่บันทึก · ส่งใบตัดเบิกก่อนส่งใบสั่งซื้อ"
        out.append(_e("issue_today", 305, "week", f"ส่งใบตัดเบิกประจำวันที่ {today.day}/{today.month}",
                      "done" if slip_ok else "todo", slip_detail,
                      owner=("branch.stock",), owner_th="สาขา", branch=b,
                      yesterday=info["yesterday"]["status"], slip_status=st_today["status"], started=info["started"],
                      confirmed_hhmm=hhmm, appends=int(st_today.get("appends") or 0),     # PR-M7: the branch's "วันนี้เหลือทำอะไร" strip
                      go={"tab": "branchstock", "pane": "issue", "extra": {"date": today.isoformat()}}))
        for gd in info["gaps"]:
            g = date.fromisoformat(gd)
            out.append(_e(f"issue_missing_{gd}", 306, "week", f"ส่งใบตัดเบิกประจำวันที่ {g.day}/{g.month} (ย้อนหลัง)",
                          "todo", "ยังไม่ส่ง · บันทึกย้อนหลังได้ (นับว่าส่งช้า)",
                          owner=("branch.stock",), owner_th="สาขา", branch=b, urgent=True, slip_date=gd,
                          go={"tab": "branchstock", "pane": "issue", "extra": {"date": gd}}))
        # one duty per round: the nearest delivery, and the cutoff the branch has to beat for it
        nearest = {}
        # PL3 (RV3 F19) + P3-1: a round whose cutoff is on or before the branch's first day is not "เลยเวลาส่ง · มีค่าปรับ" - the branch's
        # first round is the next one it can still make (started.late_exempt; a branch with no activity yet starts today). Fallback: the nearest.
        from .started import effective_start, late_exempt
        first_day = effective_start(con, bid, today)
        all_it = next_deliveries(con, bid, today, horizon_days=14, now=now)        # 14: a round that is late today has its next delivery a week on
        for it in all_it:
            if not (it["late"] and late_exempt(con, bid, it["cycle_date"], today, first_day)):
                nearest.setdefault(it["round"], it)
        for it in all_it:
            nearest.setdefault(it["round"], it)
        round_items = []                                      # QA M6: the rounds in the order of their cutoff, the nearest deadline first
        for r in sorted(("1", "2", "salmon"), key=lambda k: (nearest[k]["cutoff_at"], k)):
            it = nearest[r]
            row = con.execute("SELECT status FROM bc_order WHERE branch_id=? AND round=? AND cycle_date=?"
                              " AND status<>'cancelled' ORDER BY order_id DESC LIMIT 1",
                              (bid, r, it["cycle_date"])).fetchone()
            st = row[0] if row else None
            sent = st in ("submitted", "processed", "received")
            late_now = it["late"] and not sent
            if sent:
                detail = f"สถานะ: {STATUS_TH.get(st, st)}"
            elif late_now:
                detail = "เลยเวลาส่ง · มีค่าปรับ"
            elif st:
                detail = f"{_left_th(it['hours_left'])} · สถานะ: {STATUS_TH.get(st, st)}"
            else:
                detail = f"{_left_th(it['hours_left'])} · ยังไม่ได้เปิดใบสั่งซื้อ"
            if not sent and not slip_ok:                     # the order is made from the stock after today's slip
                detail += " · ส่งใบตัดเบิกวันนี้ก่อน"
            # PR-U1 (2.8): a round is today's duty only when its cutoff is today or has passed; a later one is "ถัดไป", never a task
            # of today - no badge, not in "ยังไม่เสร็จ" (nav_badges.mine_open leaves it out), named on the phone as the next round
            upcoming = not sent and not it["late"] and it["cycle_date"] > today.isoformat()
            # M6: named as the สั่งของ page names a round - "รอบ 2 (ส่ง 17/10)", "แซลมอนสด (ส่ง 19/10)" (fmt.round_day_th)
            round_items.append(_e(f"order_due_{r}", 310, "week",
                          f"ส่งใบสั่งซื้อภายใน {it['cutoff_th']} · {_round_day(r, it['delivery'])}",
                          "done" if sent else "todo", detail,
                          owner=("branch.stock",), owner_th="สาขา", branch=b, awaiting=st == "submitted",   # sent, the warehouse has not processed it
                          urgent=bool(not sent and (it["late"] or 0 <= it["hours_left"] < 4)),
                          round_th=it["round_th"], delivery=it["delivery"], delivery_th=it["delivery_th"],
                          cutoff_at=it["cutoff_at"], cutoff_th=it["cutoff_th"], cutoff_date=it["cycle_date"],
                          late=bool(it["late"]), due_today=it["cycle_date"] == today.isoformat(), upcoming=upcoming,
                          next_th=(f"ถัดไป: {_round_day(r, it['delivery'])} ส่งก่อน {it['cutoff_th']}" if upcoming else None),
                          go={"tab": "branchstock", "pane": "order", "extra": {"round": r, "delivery": it["delivery"]}}))
        out += round_items
        due = []
        for oid, r, cyc, dd in con.execute("SELECT order_id, round, cycle_date, delivery_date FROM bc_order"
                                           " WHERE branch_id=? AND status='processed' ORDER BY cycle_date", (bid,)):
            dlv = delivery_of(con, bid, r, cyc, dd)
            if dlv <= today.isoformat():
                due.append((oid, r, dlv))
        if due:
            out.append(_e("receive_pending", 320, "week", "บันทึกรับของตามใบสั่งซื้อที่ส่งถึงแล้ว", "todo",
                          " · ".join(_round_day(r, d) for o, r, d in due),             # 4.2: the round and the delivery day, never "#9"
                          count=len(due), owner=("branch.stock",), owner_th="สาขา", branch=b,
                          go={"tab": "branchstock", "pane": "recv", "extra": {"order_id": due[0][0]}}))
        nc_b = _scalar(con, "SELECT COUNT(*) FROM bc_claim WHERE branch_id=? AND status='submitted'", (bid,)) \
            if _has(con, "bc_claim") else 0
        if nc_b:
            out.append(_e("claims_open", 325, "week", "ของขาด/เสียที่แจ้งไว้ รอพิจารณา", "na",
                          f"รอพิจารณา {nc_b} เรื่อง", count=nc_b, owner=("branch.stock",), owner_th="สาขา", branch=b, awaiting=True,
                          go={"tab": "branchstock", "pane": "recv", "anchor": "brnClaimCard"}))
        out += _count_items(con, b, today)
        if single:
            v = _month_end_view(con, bid)
            if v:
                open_ = (not v["complete"]) or v["needs_reason"] > 0
                left = v["needs_reason"] + (0 if v["complete"] else 1)
                from .fmt import MONTH_TH
                out.append(_e("variance_open", 330, "week", f"ปิดเดือน{MONTH_TH[int(str(v['begin_date'])[5:7]) - 1]}: บอกเหตุผลส่วนต่าง แล้วแจ้งรับของครบ",
                              "todo" if open_ else "done",
                              f"เหลือ {left} รายการ" if left else "ครบแล้ว",
                              count=left or None,
                              owner=("branch.stock",), owner_th="สาขา", branch=b,
                              go={"tab": "branchstock", "pane": "var",
                                  "extra": {"end_count_id": v["end_count_id"], **({"var_tab": "recon"} if v.get("stock") else {})}}))
    if centre and not single:
        n = _scalar(con, "SELECT COUNT(*) FROM bc_stock_count WHERE kind='end' AND COALESCE(status,'submitted')='submitted'"
                         " AND count_id NOT IN (SELECT end_count_id FROM bc_variance_period)") or 0
        out.append(_e("variance_open", 330, "week", "เดือนที่ยังไม่ได้แจ้งว่าบันทึกรับของครบ", "todo" if n else "done",
                      f"{n} เดือน" if n else "แจ้งครบทุกเดือน", count=n or None, owner=("branch.stock", "bom.center"),
                      owner_th="สาขา", go={"tab": "branchstock", "pane": "var"}))
    return out


def _close_steps(con, month: str, branches: list, scoped: bool) -> dict:
    from .central import items as central_items, link_counts
    from .engine import release_in_force
    steps = []
    m0, m1 = month + "-01", _month_add(month, 1) + "-01"
    tol = timedelta(days=CLOSE_TOLERANCE_DAYS)
    rid = release_in_force(con, month)
    lbl = _scalar(con, "SELECT label FROM bc_release WHERE release_id=?", (rid,)) if rid else None
    steps.append(_e("release_in_force", 1, "close", f"สูตรเวอร์ชันที่มีผลเดือน{_mth(month)}", "done" if rid else "todo",
                    f"ใช้เวอร์ชัน {_lbl(lbl)}" if rid else "ยังไม่มีเวอร์ชันที่ประกาศใช้และมีผลถึงเดือนนี้",
                    owner=("bom.release",), owner_th="ฝ่ายวิเคราะห์", go={"tab": "bomcenter", "pane": "rel"}))
    ci = central_items(con)
    steps.append(_e("central_report", 2, "close", "นำเข้ารายงานไดกิโตะ กรุ๊ปประจำเดือน (บัญชีสินค้าและใบรับสินค้า)",
                    "done" if ci["count"] and not ci["needs_factor"] else "todo",
                    (f"บัญชีสินค้า {ci['count']} รายการ" + (f" · ยังไม่ระบุอัตราแปลงหน่วย {ci['needs_factor']} รายการ" if ci["needs_factor"] else ""))
                    if ci["count"] else "ยังไม่มีบัญชีสินค้า",
                    count=ci["needs_factor"] or None, owner=("supply.ordering", "bom.center"), owner_th="ประสานงานขาย / ฝ่ายวิเคราะห์",
                    go={"tab": "orderhub", "pane": "central", "month": month, "anchor": "ohbCenStat"}))
    lc = link_counts(con)
    steps.append(_e("code_links", 3, "close", "จับคู่รหัสเดิมกับรหัสสินค้าของไดกิโตะ กรุ๊ป",
                    "na" if not ci["count"] else ("todo" if (lc["candidates"] and not lc["links"]) else "done"),
                    f"จับคู่แล้ว {lc['links']} · รหัสเดิมที่ไม่อยู่ในบัญชีสินค้า {lc['candidates']} (สาขาซื้อเอง หรือรอจับคู่)"
                    if ci["count"] else "รอบัญชีสินค้าก่อน",
                    owner=("supply.ordering", "bom.center"), owner_th="ประสานงานขาย / ฝ่ายวิเคราะห์",
                    go={"tab": "orderhub", "pane": "central", "anchor": "ohbLinks"}))

    from .started import NOT_STARTED_TH, branch_start
    starts = {b["branch_id"]: branch_start(con, b["branch_id"]) for b in branches}
    # a month the centre closes after the fact (its report for the month carries the branch's deliveries) is held whatever the start
    retro = {r[0] for r in con.execute("SELECT DISTINCT branch_id FROM bc_receipt WHERE ref=?", (f"CENTRAL-{month}",))}

    def held(bid) -> bool:
        return bid in retro or bool(starts.get(bid) and starts[bid] < m1)

    def per_branch(key, seq, title, owner, owner_th, fn):
        # PR-U1: a branch that had not started before the month ended is not asked for that month - "ยังไม่เริ่มใช้ระบบ", neutral
        rows = [fn(b) if held(b["branch_id"]) else
                dict(fn(b), status="na", not_started=True, status_th=NOT_STARTED_TH,
                     detail=f"เริ่มใช้ระบบวันที่ {_fmt_day(starts[b['branch_id']])}" if starts.get(b["branch_id"]) else NOT_STARTED_TH)
                for b in branches]
        if not branches:
            steps.append(_e(key, seq, "close", title, "todo", "ยังไม่มีสาขาที่เริ่มปิดงวดเดือนนี้", owner=owner, owner_th=owner_th,
                            go={"tab": "orderhub", "pane": "central", "month": month}, branches=[]))
            return
        sts = [r["status"] for r in rows]
        if all(s == "na" for s in sts):
            status = "na"
        else:
            status = "done" if all(s in ("done", "na") for s in sts) else ("todo" if "todo" in sts else "blocked")
        pend = [r for r in rows if r["status"] not in ("done", "na")]
        first = (pend or rows)[0]
        steps.append(_e(key, seq, "close", title, status,
                        " · ".join(f"{r['branch']}: {r['detail']}" for r in rows), count=len(pend) or None,
                        owner=owner, owner_th=owner_th, go=first["go"], branches=rows))

    def deliveries(b):
        bid = b["branch_id"]
        n = _scalar(con, "SELECT COUNT(*) FROM bc_receipt r JOIN bc_receipt_line l USING(receipt_id)"
                         " WHERE r.branch_id=? AND r.ref=?", (bid, f"CENTRAL-{month}")) or 0
        own = _scalar(con, "SELECT COUNT(*) FROM bc_receipt WHERE branch_id=? AND source IN ('branch','order')"
                           " AND receipt_date>=? AND receipt_date<?", (bid, m0, m1)) or 0
        go = {"tab": "branchstock", "pane": "recv", "branch": bid} if scoped else \
             {"tab": "orderhub", "pane": "central", "month": month, "branch": bid, "anchor": "ohbDelReport"}
        if n:
            return {"branch_id": bid, "branch": b["name"], "status": "done", "detail": f"จากรายงานไดกิโตะ กรุ๊ป {n} รายการ", "go": go}
        if own:
            return {"branch_id": bid, "branch": b["name"], "status": "done", "detail": f"สาขาบันทึกรับของเอง {own} ใบ (ไม่ต้องนำเข้าซ้ำ)", "go": go}
        return {"branch_id": bid, "branch": b["name"], "status": "todo", "detail": "ยังไม่บันทึกรับของในเดือนนี้", "go": go}

    from .variance import month_period as _month_period

    def count_in(bid, first_day):
        """The month's begin (first_day = m0) or end (first_day = m1) count, by the SAME rule as the ใบสรุปตัดเบิก periods (QA H1):
        the end count is the one that closes a period priced in `month` - dated the 1st of the next month OR the last day of this one.
        Before the period exists the begin count is looked up by date alone (the day before the 1st up to the tolerance)."""
        pr = _month_period(con, bid, month)
        if pr:
            c = pr[1] if first_day == m1 else pr[0]
            return (c["count_id"], c["date"])
        if first_day == m1:
            return None
        d0 = date.fromisoformat(first_day)
        hi = (d0 + tol).isoformat()
        st = starts.get(bid)
        if st and m0 < st < m1:                       # PR-U1: a branch that started mid-month begins with its first count of the month
            hi = max(hi, (date.fromisoformat(m1) - timedelta(days=1)).isoformat())
        return con.execute("SELECT count_id, count_date FROM bc_stock_count WHERE branch_id=?"
                           " AND COALESCE(status,'submitted')='submitted' AND kind IN ('begin','end')"
                           " AND count_date BETWEEN ? AND ?"
                           " ORDER BY count_date, count_id LIMIT 1",
                           (bid, (d0 - timedelta(days=1)).isoformat(), hi)).fetchone()

    def late_end(bid):
        """An end count that exists but is dated too late to close `month` (the 2nd or 3rd): it closes the next month."""
        d1 = date.fromisoformat(m1)
        return con.execute("SELECT count_date FROM bc_stock_count WHERE branch_id=? AND COALESCE(status,'submitted')='submitted'"
                           " AND kind IN ('begin','end') AND count_date BETWEEN ? AND ?"
                           " ORDER BY count_date LIMIT 1", (bid, (d1 + timedelta(days=1)).isoformat(), (d1 + tol).isoformat())).fetchone()

    def count_step(kind, first_day):
        def fn(b):
            bid = b["branch_id"]
            c = count_in(bid, first_day)
            go = {"tab": "branchstock", "pane": "count", "branch": bid} if scoped else \
                 {"tab": "orderhub", "pane": "central", "month": month, "branch": bid, "anchor": "ohbCntReport",
                  "extra": {"count_date": first_day}}
            if c:
                # a period an earlier build closed on a PARTIAL count (before the owner's decision of 2026-10-13) is labelled as such, never rewritten
                p = _scalar(con, "SELECT COALESCE(partial,0) FROM bc_stock_count WHERE count_id=?", (c[0],))
                return {"branch_id": bid, "branch": b["name"], "status": "done",
                        "detail": f"นับของ {_fmt_day(c[1])}" + (" · ปิดด้วยการนับเสริมรายสัปดาห์ (รายการเดิม ของที่ควรเหลือไม่ได้นับ)" if p else ""), "go": go}
            if kind == "end" and not count_in(bid, m0):
                return {"branch_id": bid, "branch": b["name"], "status": "blocked", "detail": "รอผลนับครั้งแรกก่อน", "go": go}
            late = late_end(bid) if kind == "end" else None
            if late:
                return {"branch_id": bid, "branch": b["name"], "status": "todo", "go": go,
                        "detail": f"ผลนับลงวันที่ {_fmt_day(late[0])} ใช้ปิดเดือนถัดไป · เดือนนี้ปิดด้วยผลนับวันที่ {_fmt_day(m1)} หรือวันสุดท้ายของเดือน"}
            st = starts.get(bid)
            if kind == "begin" and st and m0 < st < m1:   # PR-U1: started mid-month - its first count is the begin, not one dated the 1st
                return {"branch_id": bid, "branch": b["name"], "status": "todo", "go": go,
                        "detail": f"เริ่มใช้ระบบวันที่ {_fmt_day(st)} · นับของครบทุกรายการเป็นการนับครั้งแรก"}
            return {"branch_id": bid, "branch": b["name"], "status": "todo", "detail": f"ยังไม่มีผลนับวันที่ {_fmt_day(first_day)}", "go": go}
        return fn

    def confirm(b):
        bid = b["branch_id"]
        e = count_in(bid, m1)
        go = {"tab": "branchstock", "pane": "var", "branch": bid, "extra": {"end_count_id": e[0]} if e else {}}
        if not e or not count_in(bid, m0):
            return {"branch_id": bid, "branch": b["name"], "status": "blocked", "detail": "รอผลนับครั้งแรกและนับสิ้นเดือน", "go": go}
        conf = _scalar(con, "SELECT receipts_confirmed_by FROM bc_variance_period WHERE end_count_id=?", (e[0],))
        reasons = _scalar(con, "SELECT COUNT(*) FROM bc_variance_reason WHERE end_count_id=?", (e[0],)) or 0
        if conf:
            return {"branch_id": bid, "branch": b["name"], "status": "done", "detail": f"แจ้งโดย {_who(con, conf)} · เหตุผล {reasons} รายการ", "go": go}
        return {"branch_id": bid, "branch": b["name"], "status": "todo", "detail": f"ยังไม่ได้แจ้ง · เหตุผลที่บันทึก {reasons}", "go": go}

    def issue(b):
        bid = b["branch_id"]
        e = count_in(bid, m1)
        go = {"tab": "bomcenter", "pane": "issue", "month": month, "branch": bid}
        if not e or not count_in(bid, m0):
            return {"branch_id": bid, "branch": b["name"], "status": "blocked", "detail": "รอผลนับครั้งแรกและนับสิ้นเดือน", "go": go}
        row = con.execute("SELECT receipts_confirmed_by, accounting_posted_at, accounting_posted_by FROM bc_variance_period"
                          " WHERE end_count_id=?", (e[0],)).fetchone()
        if not row:
            return {"branch_id": bid, "branch": b["name"], "status": "blocked", "detail": "รอให้สาขาแจ้งว่าบันทึกรับของครบก่อน", "go": go}
        if row[1]:
            return {"branch_id": bid, "branch": b["name"], "status": "done",
                    "detail": f"บันทึกแล้ว {_day_at(row[1])} โดย {_who(con, row[2])}", "go": go}
        return {"branch_id": bid, "branch": b["name"], "status": "todo",
                "detail": "สาขาแจ้งว่าบันทึกรับของครบแล้ว รอบัญชีดาวน์โหลดและบันทึก", "go": go}

    per_branch("deliveries", 4, f"บันทึกรับของประจำเดือน{_mth(month)}", ("supply.ordering", "bom.center"), "ประสานงานขาย", deliveries)
    per_branch("begin_count", 5, f"ผลนับครั้งแรก (นับครบทุกรายการ) 1 {_mth(month)}", ("supply.ordering", "bom.center", "branch.stock"), "ประสานงานขาย / ฝ่ายวิเคราะห์ / สาขา", count_step("begin", m0))
    per_branch("end_count", 6, f"ผลนับสิ้นเดือน (นับครบทุกรายการ) 1 {_mth(_month_add(month, 1))}",
               ("supply.ordering", "bom.center", "branch.stock"), "ประสานงานขาย / ฝ่ายวิเคราะห์ / สาขา", count_step("end", m1))
    per_branch("receipts_confirm", 7, "แจ้งว่าบันทึกรับของครบและระบุเหตุผลของส่วนต่าง", ("branch.stock", "supply.ordering", "bom.center"), "สาขา", confirm)
    conf_done = branches and steps[-1]["status"] == "done"
    per_branch("issue_posted", 8, "ดาวน์โหลดใบสรุปตัดเบิกและบันทึกเข้าระบบบัญชี", ("cost.analysis", "bom.center"), "บัญชี", issue)
    steps.append(_e("monthly_summary", 9, "close", f"สรุปผลต่างรายเดือน{_mth(month)}", "done" if conf_done else "todo",
                    "ดูผลต่างการใช้ของทุกสาขา และข้อ 13A ในรายงานผู้บริหาร" if conf_done else "รอขั้นตอนก่อนหน้าครบทุกสาขา",
                    owner=("ops.report", "bom.center", "supply.ordering"), owner_th="ฝ่ายปฏิบัติการ / ฝ่ายวิเคราะห์",
                    go={"tab": "orderhub", "pane": "month", "month": month}))
    return {"month": month, "steps": steps, "done": sum(1 for s in steps if s["status"] == "done"), "total": len(steps),
            "branches": [{"branch_id": b["branch_id"], "name": b["name"]} for b in branches]}


# PL1: the stock-mode month close is ONE list of five steps, used by the stepper (ปิดงวดเดือน) and by หน้างาน alike. The nine steps of
# the full mode are grouped the way the stepper always showed them: the centre's report and the month's deliveries are one step, the
# first and the end count are one step. A merged step keeps its originals in `parts` (the stepper draws a block for each).
STOCK_CLOSE_GROUPS = (
    ("central_report", "นำเข้ารายงานไดกิโตะ กรุ๊ปประจำเดือน", ("central_report", "deliveries")),
    ("code_links", None, ("code_links",)),
    ("counts", "ใบตรวจนับต้นงวดและปลายงวด", ("begin_count", "end_count")),
    ("receipts_confirm", None, ("receipts_confirm",)),
    ("issue_posted", None, ("issue_posted",)),
)


def _worst(statuses: list) -> str:
    if all(x == "na" for x in statuses):
        return "na"
    if all(x in ("done", "na") for x in statuses):
        return "done"
    return "todo" if "todo" in statuses else "blocked"


def stock_close_steps(steps: list) -> list:
    """Group the nine full-mode close steps into the five of the stock-mode close (see STOCK_CLOSE_GROUPS), numbered 1..5."""
    by = {s["key"]: s for s in steps}
    out = []
    for key, title, parts_keys in STOCK_CLOSE_GROUPS:
        parts = [by[k] for k in parts_keys if k in by]
        if not parts:
            continue
        if len(parts) == 1:
            m = dict(parts[0])
        else:
            pend = [p for p in parts if p["status"] not in ("done", "na")]
            first = (pend or parts)[0]
            owners = []
            for p in parts:
                owners += [o for o in p["owner_modules"] if o not in owners]
            counts = [p["count"] for p in parts if p.get("count")]
            m = dict(parts[0], key=key, title=title, status=_worst([p["status"] for p in parts]),
                     detail=" · ".join(p["detail"] for p in parts if p.get("detail")),
                     count=sum(counts) or None, owner_modules=owners, go=dict(first["go"]), parts=[dict(p) for p in parts])
            rows = {}
            for p in parts:
                for r in p.get("branches") or []:
                    rows.setdefault(r["branch_id"], []).append(r)
            if rows:
                merged = []
                for bid, rs in rows.items():
                    lead = next((r for r in rs if r["status"] not in ("done", "na")), rs[0])
                    merged.append(dict(lead, status=_worst([r["status"] for r in rs]),
                                       detail=" · ".join(r["detail"] for r in rs if r.get("detail"))))
                m["branches"] = merged
        m["key"] = key
        out.append(m)
    for n, s in enumerate(out, 1):
        s["seq"] = n
    for s in out:                                            # the reasons are given on the สิ้นเดือน tab, the only variance tab with them in stock mode
        if s["key"] == "receipts_confirm":
            for g in [s] + list(s.get("branches") or []):
                if g.get("go", {}).get("extra", {}).get("end_count_id"):
                    g["go"]["extra"]["var_tab"] = "recon"
    return out


# ── the answer ───────────────────────────────────────────────────────────────
def build(con, month: str = None, branch_id: int = None, scoped_branch: str = None, today: date = None,
          now=None, viewer_role: str = None) -> dict:
    """`now` (tests): the Bangkok clock the cutoff countdown reads; with only `today` it is 08:00 that day.
    `viewer_role` (PR-TF): the role of the caller; every item and close step gets `state` / `state_th` for that viewer."""
    if now is None and today is None:
        now = now_bkk()
    today = today or now.date()
    month = month or _default_month(con, today)
    all_br = _branch_rows(con)
    by_id = {b["branch_id"]: b for b in all_br}
    if scoped_branch:
        sb = next((b for b in all_br if b["name"] == scoped_branch), None)
        if not sb:
            raise PermissionError("ผู้ใช้นี้ผูกกับสาขาที่ไม่พบ · แจ้งผู้ดูแลระบบ")
        if branch_id is not None and int(branch_id) != sb["branch_id"]:
            raise PermissionError("ผู้ใช้ของสาขาเข้าถึงได้เฉพาะสาขาของตัวเอง")
        branch_id = sb["branch_id"]
    chosen = by_id.get(int(branch_id)) if branch_id is not None else None
    if branch_id is not None and not chosen:
        raise ValueError("ไม่พบสาขา")
    # the centre's branch set: every active branch (PR-U2: the 30-branch rollout, not the pilot four); the month close keeps the
    # branches with counts, orders or a central note
    active_ids = {r[0] for r in con.execute("SELECT branch_id FROM bc_stock_count UNION SELECT branch_id FROM bc_order")}
    active_ids |= {r[0] for r in con.execute("SELECT branch_id FROM bc_receipt WHERE ref=?", (f"CENTRAL-{month}",))}
    week_br = [chosen] if chosen else sorted({b["branch_id"] for b in all_br})
    week_br = [by_id[i] for i in week_br if i in by_id] if not chosen else week_br
    close_br = [chosen] if chosen else [by_id[i] for i in sorted(active_ids) if i in by_id]
    centre = not scoped_branch
    from .close_settings import stock_mode
    stock = stock_mode(con)                                  # PR-S1: stock mode leaves out every item of RECIPE_KEYS
    items = []
    if centre:
        items += _setup_items(con)
        items += _unit_review_items(con)
        items += _code_request_items(con)
        if not stock:
            items += _recipe_check_items(con, today)
        if chosen is None:                                   # the whole-centre list, not one branch seen by the centre (the reminders ask for those)
            items = _merge_gaps_into_backlog(items, [] if stock else _backlog_item(con))
            items += _feedback_items(con)
        items += _month_items(con, month, today)
    items += _week_items(con, week_br, today, single=chosen is not None, centre=centre, now=now)
    close = _close_steps(con, month, close_br, scoped=bool(scoped_branch))
    in_force = close["steps"][0]["detail"]
    if stock:
        items = [it for it in items if not is_recipe_item(it)]
        for it in items:
            if it.get("key") == "master_seed":                # the seed button lives on the register page, which stays
                it["go"] = {"tab": "bomcenter", "pane": "ing"}
        close["steps"] = stock_close_steps([s for s in close["steps"] if not is_recipe_item(s)])   # PL1: five steps, numbered 1..5 (the stepper and หน้างาน print the same list)
        close["done"] = sum(1 for s in close["steps"] if s["status"] == "done")
        close["total"] = len(close["steps"])
    annotate_states(items + close["steps"], viewer_role)
    this_m = _ym(today)
    return {"month": month, "today": today.isoformat(), "scoped": bool(scoped_branch),
            "branch": chosen, "branches": [b for b in all_br if (not scoped_branch or b["branch_id"] == branch_id)],
            "release": {"effective_month": _month_add(this_m, 1), "in_force": in_force},
            "items": items, "close": close}
