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
from datetime import date, datetime, timedelta

from .clock import BKK, now_bkk
from .fmt import at_th as _fmt_at, day_th as _fmt_day, lbl as _lbl, month_th as _mth, who as _who

CLOSE_TOLERANCE_DAYS = 2          # a "1st of the month" count may be dated up to two days later
RELEASE_DAY = 25                  # owner §10-2: the analyst releases by the 25th


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
    cols = {r[1] for r in con.execute("PRAGMA table_info(branches)")}
    sql = "SELECT branch_id, name FROM branches WHERE name <> 'ALL'"
    if "active" in cols:
        sql += " AND COALESCE(active, 1) = 1"
    return [{"branch_id": r[0], "name": r[1]} for r in con.execute(sql + " ORDER BY name")]


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
    out.append(_e("sub_recipes", 40, "setup", "นำเข้าสูตรกึ่งสำเร็จจากไฟล์ครัว (ไม่บังคับ)",
                  "done" if not missing_prep else "todo",
                  f"ยังไม่มีสูตร {missing_prep} รายการ · ช่วยให้แผนสั่งแนะนำวัตถุดิบดิบในสูตรได้" if missing_prep else "มีสูตรครบ",
                  count=missing_prep or None, owner=("bom.center",), owner_th="ฝ่ายวิเคราะห์ / ครัว",
                  go={"tab": "bomcenter", "pane": "prep"}))
    return out


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
            sub_status, sub_detail = "todo", "ไฟล์รายงานจ่ายสินค้าภายใน หรือไฟล์ตามแม่แบบยอดซื้อรายบรรทัด"
        out.append(_e("price_submit", 95, "month", f"ส่งไฟล์ราคาเดือน{_mth(month)} ให้ฝ่ายวิเคราะห์", sub_status, sub_detail,
                      owner=("supply.ordering",), owner_th="ประสานงานขาย",
                      go={"tab": "bomcenter", "pane": "pricefile", "month": month, "anchor": "bmcPriceSubmitCard"}))
        pend = _scalar(con, "SELECT COUNT(*) FROM bc_price_submission WHERE status='pending'") or 0
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
                          f"ส่งตรวจแล้ว {_fmt_at(sub_at)} โดย {_who(con, sub_by)}", **kitchen))
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
                      "ดาวน์โหลดแม่แบบ กรอกสูตรแล้วนำเข้า หรือสร้างร่างจากเวอร์ชันที่ประกาศใช้แล้ว",
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
    last = con.execute("SELECT MAX(count_date) FROM bc_stock_count WHERE branch_id=? AND kind IN ('begin','end')"
                       " AND COALESCE(status,'submitted')='submitted'", (bid,)).fetchone()[0]
    started = bool(last) or bool(con.execute("SELECT 1 FROM bc_issue_slip WHERE branch_id=? LIMIT 1", (bid,)).fetchone())
    lo = max(date.fromisoformat(last), today - timedelta(days=7)) if last else today - timedelta(days=7)
    gaps = [x["date"] for x in status_days(con, bid, lo, d_prev) if x["status"] in ("missing", "draft")] \
        if started and lo <= d_prev else []
    return {"today": t, "yesterday": y, "started": started, "gaps": gaps}


def _week_items(con, branches: list, today: date, single: bool, centre: bool, now=None) -> list:
    from .forecast import ROUND_TH, WD_TH, _wd, calendar, next_deliveries
    from .hub import delivery_of
    from .orders import STATUS_TH
    out = []
    slips = {b["branch_id"]: _slip_info(con, b["branch_id"], today) for b in branches}
    if centre:
        from .pilot import pilot_branches
        users = {}
        if _has(con, "app_user"):
            for b, active in con.execute("SELECT branch, active FROM app_user WHERE role='branch'"):
                users[b] = users.get(b) or bool(active)
        no_acc = [b for b in pilot_branches(con) if not users.get(b["name"])]
        out.append(_e("branch_account", 200, "week", "สร้างผู้ใช้ของสาขานำร่อง", "done" if not no_acc else "todo",
                      ("ยังไม่มีผู้ใช้: " + " · ".join(b["name"] for b in no_acc)) if no_acc else "ทุกสาขานำร่องมีผู้ใช้แล้ว",
                      count=len(no_acc) or None, owner=("system.admin",), owner_th="ผู้ดูแลระบบ",
                      go={"tab": "settings", "pane": "users"}))
        unfilled = [b for b in branches if not all(calendar(con, b["branch_id"])[r]["filled"] for r in ("1", "2", "salmon"))]
        out.append(_e("calendar", 210, "week", "กำหนดรอบส่งของสาขา (รอบ 1 · รอบ 2 · แซลมอนสด)",
                      "done" if not unfilled else "todo",
                      ("ยังใช้ค่าเริ่มต้น: " + " · ".join(b["name"] for b in unfilled)) if unfilled else "ครบทุกสาขา",
                      count=len(unfilled) or None, owner=("supply.ordering",), owner_th="ประสานงานขาย",
                      go={"tab": "bomcenter", "pane": "cal"}))
        q = con.execute("SELECT COUNT(*), MIN(cycle_date), SUM(COALESCE(late,0)) FROM bc_order"
                        " WHERE status='submitted'").fetchone()
        late = bool(q[0] and q[1] and q[1] < today.isoformat())          # past its cutoff day: the 14:00 target has gone
        n_late = int(q[2] or 0)                                           # submitted after the 13:15 cutoff (late fee)
        out.append(_e("queue_pending", 220, "week", "ระบุยอดส่งจริงของใบสั่งซื้อที่สาขาส่งมา", "todo" if q[0] else "done",
                      (f"รอ {q[0]} ใบ ภายใน 14:00 ของวันตัดรอบ" + (f" · ส่งล่าช้า {n_late} ใบ (มีค่าปรับ)" if n_late else "")
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
                          f"ยังไม่คีย์ {nk} ใบ · ดาวน์โหลด Excel แล้วกด “คีย์บัญชีแล้ว”" if nk else "คีย์ครบทุกใบ",
                          count=nk or None, owner=("supply.ordering",), owner_th="ประสานงานขาย",
                          go={"tab": "orderhub", "pane": "queue"}))
        # ใบตัดเบิก: the branches that have started and did not confirm yesterday's slip
        open_y = [b["name"] for b in branches if slips[b["branch_id"]]["started"]
                  and slips[b["branch_id"]]["yesterday"]["status"] in ("missing", "draft")]
        out.append(_e("issue_missing", 228, "week", "ติดตามสาขาที่ยังไม่ยืนยันใบตัดเบิกของเมื่อวาน",
                      "todo" if open_y else "done",
                      ("ยังไม่ยืนยัน: " + " · ".join(open_y)) if open_y else "ทุกสาขาที่เริ่มใช้งานแล้วยืนยันใบตัดเบิกของเมื่อวานแล้ว",
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
        out.append(_e("issue_anomaly", 229, "week", "ตรวจสอบสาขาที่เบิกเกินสูตรติดต่อกัน",
                      "todo" if odd else "done",
                      (f"เบิกเกินสูตรมากกว่า {OVER_PCT:.0f}% ติดต่อกันอย่างน้อย {STREAK_DAYS} วัน: {shown}") if odd
                      else "ไม่พบการเบิกเกินสูตรติดต่อกันใน 7 วันล่าสุด",
                      count=len(odd) or None, owner=("supply.ordering", "ops.report"),
                      owner_th="ประสานงานขาย / ฝ่ายปฏิบัติการ", urgent=bool(odd), branch_names=[a["branch"] for a in odd],
                      anomalies=[{"branch_id": a["branch_id"], "branch": a["branch"], "items": a["items"][:3],
                                  "max_pct": a["max_pct"]} for a in odd],
                      go={"tab": "orderhub", "pane": "issuevar", **({"branch": odd[0]["branch_id"]} if odd else {})}))
    for b in branches:
        bid = b["branch_id"]
        info = slips[bid]
        st_today = info["today"]
        slip_ok = st_today["status"] in ("confirmed", "late")
        if st_today["status"] in ("confirmed", "late"):
            at = st_today["confirmed_at"]
            hhmm = datetime.fromisoformat(at).astimezone(BKK).strftime("%H:%M") if at else "-"
            slip_detail = f"ยืนยันแล้ว เวลา {hhmm} โดย {_who(con, st_today['confirmed_by'])}"
        elif st_today["status"] == "draft":
            slip_detail = "มีร่างแล้ว · ยืนยันก่อนส่งใบสั่งซื้อ"
        else:
            slip_detail = "ยังไม่บันทึก · ยืนยันก่อนส่งใบสั่งซื้อ"
        out.append(_e("issue_today", 305, "week", f"ยืนยันใบตัดเบิกประจำวันที่ {today.day}/{today.month}",
                      "done" if slip_ok else "todo", slip_detail,
                      owner=("branch.stock",), owner_th="สาขา", branch=b,
                      yesterday=info["yesterday"]["status"], slip_status=st_today["status"], started=info["started"],
                      go={"tab": "branchstock", "pane": "issue", "extra": {"date": today.isoformat()}}))
        for gd in info["gaps"]:
            g = date.fromisoformat(gd)
            out.append(_e(f"issue_missing_{gd}", 306, "week", f"ยืนยันใบตัดเบิกประจำวันที่ {g.day}/{g.month} (ย้อนหลัง)",
                          "todo", "ยังไม่ยืนยัน · บันทึกย้อนหลังได้ (นับเป็นล่าช้า)",
                          owner=("branch.stock",), owner_th="สาขา", branch=b, urgent=True, slip_date=gd,
                          go={"tab": "branchstock", "pane": "issue", "extra": {"date": gd}}))
        # one duty per round: the nearest delivery, and the cutoff the branch has to beat for it
        nearest = {}
        for it in next_deliveries(con, bid, today, horizon_days=7, now=now):
            nearest.setdefault(it["round"], it)
        for r in ("1", "2", "salmon"):
            it = nearest[r]
            row = con.execute("SELECT status FROM bc_order WHERE branch_id=? AND round=? AND cycle_date=?"
                              " AND status<>'cancelled' ORDER BY order_id DESC LIMIT 1",
                              (bid, r, it["cycle_date"])).fetchone()
            st = row[0] if row else None
            sent = st in ("submitted", "processed", "received")
            dlv = date.fromisoformat(it["delivery"])
            dlv_par = f"วัน{WD_TH[_wd(dlv)]} ({dlv.day}/{dlv.month})"
            late_now = it["late"] and not sent
            hours = int(max(it["hours_left"], 0))
            if sent:
                detail = f"สถานะ: {STATUS_TH.get(st, st)}"
            elif late_now:
                detail = "เลยเวลาตัดรอบ · ส่งตอนนี้มีค่าปรับล่าช้า"
            elif st:
                detail = f"เหลือ {hours} ชม. · สถานะ: {STATUS_TH.get(st, st)}"
            else:
                detail = f"เหลือ {hours} ชม. · ยังไม่ได้เปิดใบสั่งซื้อ"
            if not sent and not slip_ok:                     # the order is made from the stock after today's slip
                detail += " · ยืนยันใบตัดเบิกวันนี้ก่อน"
            what = "ส่งใบสั่งซื้อแซลมอนสด" if r == "salmon" else "ส่งใบสั่งซื้อ"
            out.append(_e(f"order_due_{r}", 310, "week",
                          f"{what}{' ' if r == 'salmon' else ''}รอบส่ง{dlv_par} ภายใน {it['cutoff_th']}",
                          "done" if sent else "todo", detail,
                          owner=("branch.stock",), owner_th="สาขา", branch=b,
                          urgent=bool(not sent and (it["late"] or 0 <= it["hours_left"] < 4)),
                          round_th=it["round_th"], delivery=it["delivery"], delivery_th=it["delivery_th"],
                          cutoff_at=it["cutoff_at"], cutoff_th=it["cutoff_th"], cutoff_date=it["cycle_date"],
                          late=bool(it["late"]), due_today=it["cycle_date"] == today.isoformat(),
                          go={"tab": "branchstock", "pane": "order", "extra": {"round": r, "delivery": it["delivery"]}}))
        due = []
        for oid, r, cyc, dd in con.execute("SELECT order_id, round, cycle_date, delivery_date FROM bc_order"
                                           " WHERE branch_id=? AND status='processed' ORDER BY cycle_date", (bid,)):
            dlv = delivery_of(con, bid, r, cyc, dd)
            if dlv <= today.isoformat():
                due.append((oid, r, dlv))
        if due:
            out.append(_e("receive_pending", 320, "week", "บันทึกรับสินค้าตามใบสั่งซื้อที่ส่งถึงแล้ว", "todo",
                          "ใบสั่งซื้อ " + " · ".join(f"#{o} ({ROUND_TH[r]} ส่ง {_fmt_day(d)})" for o, r, d in due),
                          count=len(due), owner=("branch.stock",), owner_th="สาขา", branch=b,
                          go={"tab": "branchstock", "pane": "recv", "extra": {"order_id": due[0][0]}}))
        nc_b = _scalar(con, "SELECT COUNT(*) FROM bc_claim WHERE branch_id=? AND status='submitted'", (bid,)) \
            if _has(con, "bc_claim") else 0
        if nc_b:
            out.append(_e("claims_open", 325, "week", "เคลมสินค้าที่แจ้งไว้ รอประสานงานขายพิจารณา", "na",
                          f"รอพิจารณา {nc_b} เรื่อง", count=nc_b, owner=("branch.stock",), owner_th="สาขา", branch=b,
                          go={"tab": "branchstock", "pane": "recv", "anchor": "brnClaimCard"}))
        if single:
            from .variance import period
            try:
                v = period(con, bid, summary_only=True)
            except ValueError:
                v = None
            if v:
                open_ = (not v["complete"]) or v["needs_reason"] > 0
                out.append(_e("variance_open", 330, "week", "ระบุเหตุผลผลต่างและยืนยันรับสินค้าครบของงวดล่าสุด",
                              "todo" if open_ else "done",
                              f"งวด {_fmt_day(v['begin_date'])} → {_fmt_day(v['end_date'])} · ยังไม่ระบุเหตุผล {v['needs_reason']}"
                              + ("" if v["complete"] else " · ยังไม่ยืนยันรับสินค้าครบ"),
                              count=(v["needs_reason"] + (0 if v["complete"] else 1)) or None,
                              owner=("branch.stock",), owner_th="สาขา", branch=b,
                              go={"tab": "branchstock", "pane": "var", "extra": {"end_count_id": v["end_count_id"]}}))
    if centre and not single:
        n = _scalar(con, "SELECT COUNT(*) FROM bc_stock_count WHERE kind='end' AND COALESCE(status,'submitted')='submitted'"
                         " AND count_id NOT IN (SELECT end_count_id FROM bc_variance_period)") or 0
        out.append(_e("variance_open", 330, "week", "งวดที่ยังไม่ยืนยันรับสินค้าครบ", "todo" if n else "done",
                      f"{n} งวด" if n else "ยืนยันครบทุกงวด", count=n or None, owner=("branch.stock", "bom.center"),
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
    steps.append(_e("central_report", 2, "close", "นำเข้ารายงานของไดกิโตะ กรุ๊ปประจำเดือน (บัญชีสินค้าและใบรับสินค้า)",
                    "done" if ci["count"] and not ci["needs_factor"] else "todo",
                    (f"บัญชีสินค้าส่วนกลาง {ci['count']} รายการ" + (f" · ยังไม่ระบุอัตราแปลงหน่วย {ci['needs_factor']} รายการ" if ci["needs_factor"] else ""))
                    if ci["count"] else "ยังไม่มีบัญชีสินค้าส่วนกลาง",
                    count=ci["needs_factor"] or None, owner=("supply.ordering", "bom.center"), owner_th="ประสานงานขาย / ฝ่ายวิเคราะห์",
                    go={"tab": "orderhub", "pane": "central", "month": month, "anchor": "ohbCenStat"}))
    lc = link_counts(con)
    steps.append(_e("code_links", 3, "close", "จับคู่รหัสในสูตรกับรหัสสินค้าของไดกิโตะ กรุ๊ป",
                    "na" if not ci["count"] else ("todo" if (lc["candidates"] and not lc["links"]) else "done"),
                    f"จับคู่แล้ว {lc['links']} · รหัสในสูตรที่ไม่อยู่ในบัญชีสินค้าส่วนกลาง {lc['candidates']} (สาขาซื้อเอง หรือรอจับคู่)"
                    if ci["count"] else "รอบัญชีสินค้าส่วนกลางก่อน",
                    owner=("supply.ordering", "bom.center"), owner_th="ประสานงานขาย / ฝ่ายวิเคราะห์",
                    go={"tab": "orderhub", "pane": "central", "anchor": "ohbLinks"}))

    def per_branch(key, seq, title, owner, owner_th, fn):
        rows = [fn(b) for b in branches]
        if not branches:
            steps.append(_e(key, seq, "close", title, "todo", "ยังไม่มีสาขาที่เริ่มปิดงวดเดือนนี้", owner=owner, owner_th=owner_th,
                            go={"tab": "orderhub", "pane": "central", "month": month}, branches=[]))
            return
        sts = [r["status"] for r in rows]
        status = "done" if all(s == "done" for s in sts) else ("todo" if "todo" in sts else "blocked")
        pend = [r for r in rows if r["status"] != "done"]
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
            return {"branch_id": bid, "branch": b["name"], "status": "done", "detail": f"จากรายงานของไดกิโตะ กรุ๊ป {n} รายการ", "go": go}
        if own:
            return {"branch_id": bid, "branch": b["name"], "status": "done", "detail": f"สาขาบันทึกรับสินค้าเอง {own} ใบ (ไม่ต้องนำเข้าซ้ำ)", "go": go}
        return {"branch_id": bid, "branch": b["name"], "status": "todo", "detail": "ยังไม่บันทึกรับสินค้าของเดือนนี้", "go": go}

    def count_in(bid, first_day):
        d0 = date.fromisoformat(first_day)
        return con.execute("SELECT count_id, count_date FROM bc_stock_count WHERE branch_id=?"
                           " AND COALESCE(status,'submitted')='submitted' AND kind IN ('begin','end')"
                           " AND count_date BETWEEN ? AND ?"
                           " ORDER BY count_date, count_id LIMIT 1", (bid, first_day, (d0 + tol).isoformat())).fetchone()

    def count_step(kind, first_day):
        def fn(b):
            bid = b["branch_id"]
            c = count_in(bid, first_day)
            go = {"tab": "branchstock", "pane": "count", "branch": bid} if scoped else \
                 {"tab": "orderhub", "pane": "central", "month": month, "branch": bid, "anchor": "ohbCntReport",
                  "extra": {"count_date": first_day}}
            if c:
                return {"branch_id": bid, "branch": b["name"], "status": "done", "detail": f"ตรวจนับ {_fmt_day(c[1])}", "go": go}
            if kind == "end" and not count_in(bid, m0):
                return {"branch_id": bid, "branch": b["name"], "status": "blocked", "detail": "รอผลตรวจนับต้นงวดก่อน", "go": go}
            return {"branch_id": bid, "branch": b["name"], "status": "todo", "detail": f"ยังไม่มีผลตรวจนับวันที่ {_fmt_day(first_day)}", "go": go}
        return fn

    def confirm(b):
        bid = b["branch_id"]
        e = count_in(bid, m1)
        go = {"tab": "branchstock", "pane": "var", "branch": bid, "extra": {"end_count_id": e[0]} if e else {}}
        if not e or not count_in(bid, m0):
            return {"branch_id": bid, "branch": b["name"], "status": "blocked", "detail": "รอผลตรวจนับต้นงวดและปลายงวด", "go": go}
        conf = _scalar(con, "SELECT receipts_confirmed_by FROM bc_variance_period WHERE end_count_id=?", (e[0],))
        reasons = _scalar(con, "SELECT COUNT(*) FROM bc_variance_reason WHERE end_count_id=?", (e[0],)) or 0
        if conf:
            return {"branch_id": bid, "branch": b["name"], "status": "done", "detail": f"ยืนยันโดย {_who(con, conf)} · เหตุผล {reasons} รายการ", "go": go}
        return {"branch_id": bid, "branch": b["name"], "status": "todo", "detail": f"ยังไม่ยืนยัน · เหตุผลที่บันทึก {reasons}", "go": go}

    def issue(b):
        bid = b["branch_id"]
        e = count_in(bid, m1)
        go = {"tab": "bomcenter", "pane": "issue", "month": month, "branch": bid}
        if not e or not count_in(bid, m0):
            return {"branch_id": bid, "branch": b["name"], "status": "blocked", "detail": "รอผลตรวจนับต้นงวดและปลายงวด", "go": go}
        row = con.execute("SELECT receipts_confirmed_by, accounting_posted_at, accounting_posted_by FROM bc_variance_period"
                          " WHERE end_count_id=?", (e[0],)).fetchone()
        if not row:
            return {"branch_id": bid, "branch": b["name"], "status": "blocked", "detail": "รอยืนยันรับสินค้าครบก่อน", "go": go}
        if row[1]:
            return {"branch_id": bid, "branch": b["name"], "status": "done",
                    "detail": f"บันทึกแล้ว {_fmt_day(row[1][:10])} โดย {_who(con, row[2])}", "go": go}
        return {"branch_id": bid, "branch": b["name"], "status": "todo",
                "detail": "ยืนยันรับสินค้าครบแล้ว รอบัญชีดาวน์โหลดและบันทึก", "go": go}

    per_branch("deliveries", 4, f"บันทึกรับสินค้าประจำเดือน{_mth(month)}", ("supply.ordering", "bom.center"), "ประสานงานขาย", deliveries)
    per_branch("begin_count", 5, f"ผลตรวจนับต้นงวด 1 {_mth(month)}", ("supply.ordering", "bom.center", "branch.stock"), "ประสานงานขาย / ฝ่ายวิเคราะห์ / สาขา", count_step("begin", m0))
    per_branch("end_count", 6, f"ผลตรวจนับปลายงวด 1 {_mth(_month_add(month, 1))}", ("supply.ordering", "bom.center", "branch.stock"), "ประสานงานขาย / ฝ่ายวิเคราะห์ / สาขา", count_step("end", m1))
    per_branch("receipts_confirm", 7, "ยืนยันรับสินค้าครบและระบุเหตุผลผลต่าง", ("branch.stock", "supply.ordering", "bom.center"), "สาขา", confirm)
    conf_done = branches and steps[-1]["status"] == "done"
    per_branch("issue_posted", 8, "ดาวน์โหลดใบสรุปตัดเบิกและบันทึกเข้าระบบบัญชี", ("cost.analysis", "bom.center"), "บัญชี", issue)
    steps.append(_e("monthly_summary", 9, "close", f"สรุปผลต่างรายเดือน{_mth(month)}", "done" if conf_done else "todo",
                    "ดูผลต่างการใช้ของทุกสาขา และข้อ 13A ในรายงานผู้บริหาร" if conf_done else "รอขั้นตอนก่อนหน้าครบทุกสาขา",
                    owner=("ops.report", "bom.center", "supply.ordering"), owner_th="ฝ่ายปฏิบัติการ / ฝ่ายวิเคราะห์",
                    go={"tab": "orderhub", "pane": "month", "month": month}))
    return {"month": month, "steps": steps, "done": sum(1 for s in steps if s["status"] == "done"), "total": len(steps),
            "branches": [{"branch_id": b["branch_id"], "name": b["name"]} for b in branches]}


# ── the answer ───────────────────────────────────────────────────────────────
def build(con, month: str = None, branch_id: int = None, scoped_branch: str = None, today: date = None,
          now=None) -> dict:
    """`now` (tests): the Bangkok clock the cutoff countdown reads; with only `today` it is 08:00 that day."""
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
    # the centre's branch set: the pilot plus every branch with counts, orders or a central note
    from .pilot import pilot_branches
    active_ids = {r[0] for r in con.execute("SELECT branch_id FROM bc_stock_count UNION SELECT branch_id FROM bc_order")}
    active_ids |= {r[0] for r in con.execute("SELECT branch_id FROM bc_receipt WHERE ref=?", (f"CENTRAL-{month}",))}
    week_br = [chosen] if chosen else sorted({b["branch_id"] for b in pilot_branches(con)} | active_ids)
    week_br = [by_id[i] for i in week_br if i in by_id] if not chosen else week_br
    close_br = [chosen] if chosen else [by_id[i] for i in sorted(active_ids) if i in by_id]
    centre = not scoped_branch
    items = []
    if centre:
        items += _setup_items(con)
        items += _month_items(con, month, today)
    items += _week_items(con, week_br, today, single=chosen is not None, centre=centre, now=now)
    close = _close_steps(con, month, close_br, scoped=bool(scoped_branch))
    this_m = _ym(today)
    return {"month": month, "today": today.isoformat(), "scoped": bool(scoped_branch),
            "branch": chosen, "branches": [b for b in all_br if (not scoped_branch or b["branch_id"] == branch_id)],
            "release": {"effective_month": _month_add(this_m, 1), "in_force": close["steps"][0]["detail"]},
            "items": items, "close": close}
