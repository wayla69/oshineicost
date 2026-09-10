#!/usr/bin/env python3
"""Record the days a branch was legitimately not trading.

WHY THIS EXISTS
---------------
The data-health check (⏰ เวลาปิดร้าน → 🩺 ตรวจสุขภาพข้อมูล) reports every day with
no sales inside a branch's own active span as missing data. It has no way to tell
a renovation or a soft launch from a sync that dropped a day, because the POS
carries no "closed today" record — so a shut branch is flagged for ever, and real
gaps get lost in the noise of ones nobody intends to fix.

This writes the human answer into `branch_downtime`. The check then SPLITS those
days into their own "บันทึกสาเหตุไว้แล้ว" finding instead of dropping them, so a
wrong entry stays visible rather than silently erasing a genuine gap.

Verified before recording anything: query HQ MSSQL for the days in question. If
the source has rows that are merely unfinalised/void, that is still a closure of
sorts; if the source has nothing AND the surrounding volume shows a ramp-up or a
clean stop-and-resume, it is a closure. A day the source HAS but we do not is a
sync fault and must NOT be recorded here — fix the sync instead.

    python backend/mark_downtime.py list
    python backend/mark_downtime.py add "ศรีสะเกษ" 2026-07-28 2026-08-07 \
        --reason closed --note "ปิดปรับปรุง — ต้นทาง MSSQL ไม่มีข้อมูลเช่นกัน"
    python backend/mark_downtime.py remove 3
    python backend/mark_downtime.py seed        # the five verified on 2026-09-10
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("OSHINEI_DB") or os.path.join(HERE, "oshinei.db")

DDL = """
CREATE TABLE IF NOT EXISTS branch_downtime (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    branch     TEXT NOT NULL,
    date_from  TEXT NOT NULL,
    date_to    TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT 'closed',
    note       TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(branch, date_from, date_to)
);
"""

# Reason codes kept deliberately few — the note carries the detail.
REASONS = ("closed", "pre_open", "renovation", "disaster")

# The five gaps checked against HQ MSSQL on 2026-09-10: none of these days exist
# at source either, and the surrounding volume identifies each one.
SEED = [
    ("นครสวรรค์", "2025-07-02", "2025-07-17", "pre_open",
     "ช่วงทดลองก่อนเปิดจริง — ก่อนหน้าวันละ 5-8 บิล เปิดเต็มรูปแบบ 18 ก.ค. (58 บิล). "
     "ต้นทางมีแถวบางวันแต่ IsFinalize=0/Active=0 ทั้งหมด"),
    ("ศรีสะเกษ", "2026-07-28", "2026-08-07", "closed",
     "ปิดชั่วคราว — ก่อนหน้าวันละ 35-61 บิล กลับมา 78-82 บิล. ต้นทาง MSSQL ไม่มีข้อมูลเลย"),
    ("ศรีสะเกษ", "2025-11-16", "2025-11-16", "closed",
     "ปิด 1 วัน — ต้นทาง MSSQL ไม่มีข้อมูล"),
    ("เชียงราย", "2025-11-10", "2025-11-16", "closed",
     "ปิดเป็นช่วง (10, 11, 13, 15, 16 พ.ย.) — ต้นทางมี 2 แถวเมื่อ 10 พ.ย. แต่ IsFinalize=0/Active=0"),
    ("เชียงราย", "2026-04-22", "2026-04-22", "closed", "ปิด 1 วัน — ต้นทาง MSSQL ไม่มีข้อมูล"),
    ("บุรีรัมย์", "2026-01-06", "2026-01-06", "closed", "ปิด 1 วัน — ต้นทาง MSSQL ไม่มีข้อมูล"),
    ("สุพรรณบุรี", "2026-04-24", "2026-04-24", "closed", "ปิด 1 วัน — ต้นทาง MSSQL ไม่มีข้อมูล"),
]


def _con():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(DDL)
    return con


def _valid_day(s):
    try:
        dt.date.fromisoformat(s)
    except ValueError:
        sys.exit(f"วันที่ไม่ถูกต้อง (ต้องเป็น YYYY-MM-DD): {s}")
    return s


def _branch_exists(con, name):
    return bool(con.execute("SELECT 1 FROM branches WHERE name=?", (name,)).fetchone())


def cmd_add(a):
    con = _con()
    _valid_day(a.date_from), _valid_day(a.date_to)
    if a.date_from > a.date_to:
        sys.exit("date_from ต้องไม่เกิน date_to")
    if a.reason not in REASONS:
        sys.exit(f"reason ต้องเป็นหนึ่งใน: {', '.join(REASONS)}")
    # A typo in the branch name would record downtime nobody can see, so refuse it.
    if not _branch_exists(con, a.branch):
        names = [r[0] for r in con.execute("SELECT name FROM branches ORDER BY name")]
        sys.exit(f"ไม่พบสาขา '{a.branch}'\nสาขาที่มี: {', '.join(names)}")
    try:
        con.execute("INSERT INTO branch_downtime(branch,date_from,date_to,reason,note) "
                    "VALUES (?,?,?,?,?)", (a.branch, a.date_from, a.date_to, a.reason, a.note))
        con.commit()
        print(f"[downtime] บันทึกแล้ว: {a.branch} {a.date_from} → {a.date_to} ({a.reason})")
    except sqlite3.IntegrityError:
        print(f"[downtime] มีอยู่แล้ว: {a.branch} {a.date_from} → {a.date_to} — ไม่เปลี่ยนแปลง")


def cmd_list(_a):
    con = _con()
    rows = con.execute("SELECT * FROM branch_downtime ORDER BY branch, date_from").fetchall()
    if not rows:
        print("(ยังไม่มีการบันทึกช่วงปิดร้าน)")
        return
    print(f"{'id':>3}  {'สาขา':<14} {'ตั้งแต่':<11} {'ถึง':<11} {'วัน':>4}  เหตุผล")
    for r in rows:
        days = (dt.date.fromisoformat(r["date_to"]) - dt.date.fromisoformat(r["date_from"])).days + 1
        print(f'{r["id"]:>3}  {r["branch"]:<14} {r["date_from"]:<11} {r["date_to"]:<11} '
              f'{days:>4}  {r["reason"]} — {(r["note"] or "")[:60]}')


def cmd_remove(a):
    con = _con()
    cur = con.execute("DELETE FROM branch_downtime WHERE id=?", (a.id,))
    con.commit()
    print(f"[downtime] ลบ {cur.rowcount} รายการ" if cur.rowcount else f"[downtime] ไม่พบ id={a.id}")


def cmd_seed(_a):
    con = _con()
    added = skipped = 0
    for br, f, t, reason, note in SEED:
        if not _branch_exists(con, br):
            print(f"  ! ข้าม {br} — ไม่พบสาขานี้ในฐานข้อมูล")
            skipped += 1
            continue
        try:
            con.execute("INSERT INTO branch_downtime(branch,date_from,date_to,reason,note) "
                        "VALUES (?,?,?,?,?)", (br, f, t, reason, note))
            added += 1
        except sqlite3.IntegrityError:
            skipped += 1
    con.commit()
    print(f"[downtime] seed: เพิ่ม {added} รายการ · มีอยู่แล้ว/ข้าม {skipped}")


def main():
    ap = argparse.ArgumentParser(description="บันทึกช่วงที่สาขาไม่ได้เปิดขาย")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="เพิ่มช่วงปิด")
    a.add_argument("branch")
    a.add_argument("date_from")
    a.add_argument("date_to")
    a.add_argument("--reason", default="closed", help=f"หนึ่งใน: {', '.join(REASONS)}")
    a.add_argument("--note", default="")
    a.set_defaults(fn=cmd_add)
    sub.add_parser("list", help="ดูรายการทั้งหมด").set_defaults(fn=cmd_list)
    r = sub.add_parser("remove", help="ลบตาม id")
    r.add_argument("id", type=int)
    r.set_defaults(fn=cmd_remove)
    sub.add_parser("seed", help="ใส่ 5 ช่วงที่ตรวจสอบกับ MSSQL แล้วเมื่อ 2026-09-10").set_defaults(fn=cmd_seed)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
