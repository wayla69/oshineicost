"""PR-F2 - ร้านของฉัน, deeper: sales mix and busy hours, the controls, the guests' reviews, and the morning push.

  * GET /api/franchise/insight: the payload shape; each figure equals the engine a management page already shows, for one
    branch and month (report tier / peak / headline, the Daily Brief's line predicates, extra_customer_type, the daypart grid,
    discount_reasons, coupon_series_breakdown, extra_cancel_bills, extra_cash(+detail), feedback_summary / feedback_items);
  * the forbidden-word and other-branch scan on the new payload (a customer-type label and a review naming another branch
    come back as "สาขาอื่น"; names the feedback analysis found in a review are masked); 403 for another branch and for a
    franchise login without a branch;
  * Web Push: kind "franchise" is offered to the franchise role alone, a franchise device is offered and sent no chain kind,
    it can subscribe / read its prefs / unsubscribe its own device only, and the morning message is built from the summary
    (F1 target, the daily table) and the controls.
"""
import json
import sqlite3
from datetime import date, datetime

import pytest

import auth
from test_franchise_role import TOKEN, _login, _walk, auth_on, world      # noqa: F401  (fixtures used by name)

FORBIDDEN = ("recipe", "price", "cashier", "staff", "supplier", "receipt_no", "bill_no", "username", "password", "token", "bom")
MONTH = "2026-06"


def _db():
    import main
    return sqlite3.connect(str(main.DB_PATH))


@pytest.fixture
def f2data(app_db):
    """BranchB (id 2) rows in the staged pos_* tables and two months of reviews; everything put back afterwards."""
    from routers import feedback as FB
    import main
    c = _db()
    made = []
    for t, ddl in (("pos_discount_month", "CREATE TABLE pos_discount_month(branch_id INT, period_month TEXT, reason_id INT, bills INT, amount REAL)"),
                   ("pos_discount_reason", "CREATE TABLE pos_discount_reason(reason_id INT PRIMARY KEY, reason_desc TEXT, amount_percent REAL)"),
                   ("coupon_series", "CREATE TABLE coupon_series(coupon_series_id INT PRIMARY KEY, name TEXT, code TEXT, active INT)")):
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone():
            c.execute(ddl)
            made.append(t)
    with main.get_db() as con:
        FB._ensure(con)
        con.commit()
    c.executemany("INSERT INTO pos_customer_type_month VALUES(2,?,?,?,?)",
                  [(MONTH, "ลูกค้าทั่วไป", 900, 90000.0), (MONTH, "Grab", 30, 6420.0), (MONTH, "ลูกค้าทั่วไป BranchA", 10, 1070.0),
                   ("2026-05", "ลูกค้าทั่วไป", 1000, 107000.0)])
    c.execute("INSERT INTO pos_cancel_bill VALUES(2, ?, 2, 4, 3000)", (MONTH,))
    for d, v in (("01", 0.0), ("02", -2500.0), ("03", 120.0)):
        c.execute("INSERT INTO pos_cashcount_day VALUES(2, ?, ?, 20000, 40, 10, 60, 0)", (f"{MONTH}-{d}", v))
    c.executemany("INSERT INTO pos_discount_month VALUES(2,?,?,?,?)", [(MONTH, 0, 3, 40.0), (MONTH, 9001, 2, 20.0)])
    c.execute("INSERT OR IGNORE INTO pos_discount_reason VALUES(9001, 'ส่วนลด 10%', 10)")
    c.execute("INSERT INTO pos_coupon_month VALUES(2, ?, 9002, 3, 900)", (MONTH,))
    c.execute("INSERT OR IGNORE INTO coupon_series VALUES(9002, 'ส่วนลดวันเกิด 10%', 'BD', 1)")
    rv = [("f2-1", "2026-06-02", 5, "อร่อยมาก คุณสมชายบริการดี ดีกว่าสาขา BranchA", '["คุณสมชาย"]', "google"),
          ("f2-2", "2026-06-01", 3, "รอนาน", "[]", "facebook"),
          ("f2-3", "2026-05-20", 4, "ดี", "[]", "google")]
    for fp, day, stars, text, staff, src in rv:
        c.execute("INSERT INTO customer_feedback(branch_id, branch_name, source, rated_at, rating, text, sentiment, kind, staff, "
                  "flag_note, severity_note, contacted_by, fingerprint) VALUES(2, 'BranchB', ?, ?, ?, ?, 'pos', 'review', ?, "
                  "'internal note', 'internal', 'manager x', ?)", (src, day, stars, text, staff, fp))
    # a private message and a mention of the same branch must never reach the page
    c.execute("INSERT INTO customer_feedback(branch_id, branch_name, source, rated_at, rating, text, kind, fingerprint) "
              "VALUES(2, 'BranchB', 'facebook', '2026-06-03', NULL, 'ข้อความส่วนตัว', 'message', 'f2-4')")
    c.commit()
    try:
        yield
    finally:
        c.execute("DELETE FROM pos_customer_type_month WHERE branch_id=2")
        c.execute("DELETE FROM pos_cancel_bill WHERE branch_id=2")
        c.execute("DELETE FROM pos_cashcount_day WHERE branch_id=2")
        c.execute("DELETE FROM pos_coupon_month WHERE branch_id=2")
        c.execute("DELETE FROM customer_feedback WHERE fingerprint LIKE 'f2-%'")
        if "pos_discount_month" in made:
            c.execute("DROP TABLE pos_discount_month")
        else:
            c.execute("DELETE FROM pos_discount_month WHERE branch_id=2")
        for t in ("pos_discount_reason", "coupon_series"):
            if t in made:
                c.execute(f"DROP TABLE {t}")
        c.execute("DELETE FROM pos_discount_reason WHERE reason_id=9001") if "pos_discount_reason" not in made else None
        c.execute("DELETE FROM coupon_series WHERE coupon_series_id=9002") if "coupon_series" not in made else None
        c.commit()
        c.close()
        if hasattr(main, "invalidate_cache"):
            try:
                main.invalidate_cache()
            except Exception:
                pass


def _insight(c, **q):
    r = c.get("/api/franchise/insight", params={"month": MONTH, **q})
    assert r.status_code == 200, r.text
    return r.json()


# ── the rule ─────────────────────────────────────────────────────────────────
def test_the_insight_is_under_the_one_get_rule():
    mods = auth.ROLE_MODULES["franchise"]
    assert auth.path_allowed("GET", "/api/franchise/insight", mods)
    for m in ("POST", "PUT", "PATCH", "DELETE"):
        assert not auth.path_allowed(m, "/api/franchise/insight", mods), m


# ── the payload ──────────────────────────────────────────────────────────────
def test_the_payload_has_the_agreed_shape(world, f2data):
    d = _insight(world["b"])
    assert d["branch"] == "BranchB" and d["month"] == MONTH and d["available"] is True
    assert {"mix", "peak", "controls", "voice", "through", "label"} <= set(d)
    assert {"tiers", "lines", "channels", "window", "compare_window"} <= set(d["mix"])
    assert [x["key"] for x in d["mix"]["lines"]] == ["adult", "child", "ala", "other"]
    for x in d["mix"]["channels"]:
        assert set(x) == {"type", "sales_share", "sales_share_prev", "tag_share", "tag_share_prev", "delivery"}
    assert {"hours", "peak_hour", "dayparts", "heat", "heat_basis", "heat_best"} <= set(d["peak"])
    c = d["controls"]
    assert {"discount", "voids", "cash", "flags"} == set(c)
    assert {"pct", "policy_pct", "baht", "over_policy_baht", "by_reason", "by_coupon"} == set(c["discount"])
    assert {"bills", "baht", "rate_pct", "by_reason"} == set(c["voids"])
    assert {"net", "short_total", "over_total", "short_days", "days_counted", "days"} == set(c["cash"])
    assert all(set(x) == {"date", "over_short"} for x in c["cash"]["days"])
    assert c["flags"]["level"] in ("good", "watch", "fix") and all(set(i) == {"level", "key", "text"} for i in c["flags"]["items"])
    v = d["voice"]
    assert {"available", "rating_avg", "reviews_month", "rating_avg_prev", "reviews_prev", "rating_change", "latest"} <= set(v)
    assert all(set(x) == {"date", "stars", "text", "source"} for x in v["latest"])


def test_the_numbers_are_the_engines(world, f2data):
    """Every figure is the one the management page shows for the same branch and month."""
    import json as _j
    from routers.reports import report_management
    from routers.home_extra import extra_customer_type, extra_cancel_bills, extra_cash, extra_cash_detail
    from routers.analysis_discounts import coupon_series_breakdown
    from routers.analysis_time import analysis_daypart_grid
    from routers.feedback import feedback_summary
    d = _insight(world["b"])
    rep = _j.loads(report_management(period="monthly", date=None, week=None, month=MONTH, branch="BranchB", group=None,
                                     lang="th", mtd="1").body)
    # tiers and hours: the report's own
    assert [t["covers"] for t in d["mix"]["tiers"]] == [t["pax"] for t in ((rep.get("tier") or {}).get("rows") or [])]
    assert [(h["hour"], h["sales"], h["covers"]) for h in d["peak"]["hours"]] == \
        [(h["hour"], round(h["revenue"]), h["covers"]) for h in rep["peak"]["hourly"]]
    assert d["mix"]["window"] == rep["range"] and d["through"] == rep["range"][1]
    # the line split over the report's window: the fixture's daily lines are one à-la-carte dish (฿500 a day) and a buffet
    # dish with no line revenue, so à-la-carte is the whole of the line sales
    ala = [x for x in d["mix"]["lines"] if x["key"] == "ala"][0]
    assert ala["sales"] == 1500 and ala["sales_share"] == 100.0
    # channels: the ประเภทลูกค้า page's shares
    ct = {t["type"]: t for t in extra_customer_type(month=MONTH, branch="BranchB", group=None)["types"]}
    got = {x["type"]: x for x in d["mix"]["channels"]}
    assert got["Grab"]["sales_share"] == ct["Grab"]["net_pct"] and got["Grab"]["tag_share"] == ct["Grab"]["cust_pct"]
    assert got["Grab"]["delivery"] is True and got["ลูกค้าทั่วไป"]["delivery"] is False
    assert got["ลูกค้าทั่วไป"]["sales_share_prev"] == 100.0
    # the heat: the ช่วงเวลา page's grid
    g = _j.loads(analysis_daypart_grid(month=MONTH, month_from=None, month_to=None, branch="BranchB", metric="revenue").body)
    assert [(x["dow"], x["hour"], x["sales"]) for x in d["peak"]["heat"]] == [(x["dow"], x["hour"], round(x["value"])) for x in g["cells"]]
    # controls
    H = rep["headline"]
    assert d["controls"]["discount"]["pct"] == H["discount_pct"] and d["controls"]["discount"]["policy_pct"] == H["discount_policy_pct"]
    cs = coupon_series_breakdown(month_from=None, month_to=None, month=MONTH, branch="BranchB", group=None)["series"]
    assert [(x["name"], x["baht"]) for x in d["controls"]["discount"]["by_coupon"]] == [(x["name"], x["amount"]) for x in cs]
    assert [x["reason"] for x in d["controls"]["discount"]["by_reason"]] == ["ไม่ระบุ (ไม่เลือกเหตุผล)", "ส่วนลด 10%"]
    cb = extra_cancel_bills(month=MONTH, branch="BranchB", group=None)["summary"]
    v = d["controls"]["voids"]
    assert (v["bills"], v["baht"], v["rate_pct"]) == (cb["bills"], cb["amount"], cb["cancel_rate"]) == (4, 3000, cb["cancel_rate"])
    days = extra_cash_detail(branch="BranchB", month=MONTH)["days"]
    assert [(x["date"], x["over_short"]) for x in d["controls"]["cash"]["days"]] == [(x["day"], round(x["over_short"])) for x in days]
    s = extra_cash(month=MONTH, branch="BranchB", group=None)["summary"]
    assert d["controls"]["cash"]["short_days"] == s["short_days"] == 1 and d["controls"]["cash"]["short_total"] == s["short_total"]
    # the guests' voice
    fs = feedback_summary(None, branch="BranchB", month_from=MONTH, month_to=MONTH, source=None, kind="review")
    vo = d["voice"]
    assert vo["rating_avg"] == fs["avg_rating"] == 4.0 and vo["reviews_month"] == fs["total"] == 2
    assert vo["rating_avg_prev"] == 4.0 and vo["reviews_prev"] == 1 and vo["rating_change"] == 0.0
    assert [x["date"] for x in vo["latest"]] == ["2026-06-02", "2026-06-01", "2026-05-20"]          # newest first, reviews only
    assert vo["latest"][0]["source"] == "Google" and vo["latest"][0]["stars"] == 5


def test_the_flags_follow_the_thresholds(world, f2data):
    from routers import franchise as F
    items = {i["key"]: i for i in _insight(world["b"])["controls"]["flags"]["items"]}
    assert items["cash"]["level"] == "fix" and "2,500" in items["cash"]["text"]                  # one day short by ≥ ฿2,000
    # pure rule checks
    f = F._control_flags({"pct": 2.0, "policy_pct": 2.5}, {"bills": 1, "baht": 10, "rate_pct": 0.4}, {"days": [], "short_days": 0})
    assert f == {"level": "good", "items": []}
    f = F._control_flags({"pct": 3.0, "policy_pct": 2.5}, {"bills": 9, "baht": 10, "rate_pct": 0.8},
                         {"days": [{"date": "2026-06-01", "over_short": -100}], "short_days": 3, "short_total": -300})
    assert f["level"] == "watch" and [i["key"] for i in f["items"]] == ["discount", "voids", "cash"]
    f = F._control_flags({"pct": 6.0, "policy_pct": 2.5}, {"bills": 9, "baht": 10, "rate_pct": 1.5}, {"days": []})
    assert f["level"] == "fix" and all(i["level"] == "fix" for i in f["items"])
    assert all("ควร" in i["text"] for i in f["items"])


def test_the_payload_holds_no_forbidden_word_person_or_other_branch(world, f2data):
    for who, other in (("b", "BranchA"), ("a", "BranchB")):
        d = _insight(world[who])
        hits = [(p, kind, s) for p, kind, s in _walk(d) if any(w in s.lower() for w in FORBIDDEN)]
        assert not hits, hits
        text = json.dumps(d, ensure_ascii=False)
        assert other not in text
        for w in ("internal note", "manager x", "คุณสมชาย", "ข้อความส่วนตัว", "severity", "contact", "reply", "flag_note"):
            assert w not in text, (who, w)
    d = _insight(world["b"])
    types = [x["type"] for x in d["mix"]["channels"]]
    assert "ลูกค้าทั่วไป สาขาอื่น" in types                                                   # another branch's name in a POS label
    t = d["voice"]["latest"][0]["text"]
    assert "พนักงาน" in t and "สาขาอื่น" in t and "BranchA" not in t                            # the named person masked, the branch scrubbed


def test_another_branch_and_a_login_without_a_branch_are_refused(world, f2data):
    r = world["b"].get("/api/franchise/insight", params={"branch_id": 1, "month": MONTH})
    assert r.status_code == 403 and r.headers.get("X-Denied") == "role"
    for q in ({}, {"branch_id": 1}, {"branch_id": 2}):
        r = world["nobranch"].get("/api/franchise/insight", params=q)
        assert r.status_code == 403 and r.headers.get("X-Denied") == "role", q
    assert world["mg"].get("/api/franchise/insight").status_code == 400
    assert world["mg"].get("/api/franchise/insight", params={"branch_id": 1, "month": MONTH}).json()["branch"] == "BranchA"
    for m in ("POST", "PUT", "DELETE"):
        r = world["b"].request(m, "/api/franchise/insight", headers={"X-Upload-Token": TOKEN})
        assert r.status_code == 403 and r.headers.get("X-Denied") == "role", m


def test_no_reviews_is_a_neutral_state(world):
    v = _insight(world["a"])["voice"]
    assert v["available"] is False and v["latest"] == [] and v["rating_avg"] is None


def test_the_scrubber_keeps_the_own_name_inside_a_longer_one():
    from routers import franchise as F
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE branches(name TEXT)")
    c.executemany("INSERT INTO branches VALUES(?)", [("โคราช1",), ("โคราช2",), ("ALL",)])
    s = F._scrubber(c, "โคราช1")
    assert s("ไปโคราช2 แล้วกลับโคราช1") == "ไปสาขาอื่น แล้วกลับโคราช1"
    assert s("สาขาโคราช2 เร็วกว่า") == "สาขาอื่น เร็วกว่า"
    assert F._mask_names("น้องเอ บริการดี น้องเอ", ["น้องเอ"]) == "พนักงาน บริการดี พนักงาน"


# ── Web Push: kind "franchise" ───────────────────────────────────────────────
def _keys(monkeypatch):
    monkeypatch.setenv("OSHINEI_VAPID_PRIVATE", "cHJpdmF0ZQ")
    monkeypatch.setenv("OSHINEI_VAPID_PUBLIC", "cHVibGlj")


EP = "https://push.example/f2-own"


def _wipe_push():
    import main
    from routers import push_api as P
    with main.get_db() as con:
        P.ensure(con)
        con.execute("DELETE FROM push_subscription WHERE endpoint LIKE 'https://push.example/f2-%'")
        con.execute("DELETE FROM usage_meta WHERE key LIKE 'push_franchise%'")
        con.commit()


@pytest.fixture
def pushed(world, monkeypatch):
    _keys(monkeypatch)
    _wipe_push()
    b = world["b"]
    assert b.get("/api/push/vapid-public").json()["configured"] is True
    r = b.post("/api/push/subscribe", json={"endpoint": EP, "keys": {"p256dh": "p", "auth": "a"}})
    assert r.status_code == 200, r.text
    r = world["mg"].post("/api/push/subscribe", json={"endpoint": "https://push.example/f2-mg", "keys": {"p256dh": "p", "auth": "a"}})
    assert r.status_code == 200, r.text
    yield world
    _wipe_push()


def test_a_franchise_login_subscribes_and_is_offered_its_kind_only(pushed):
    b = pushed["b"]
    j = b.post("/api/push/prefs", json={"endpoint": EP}).json()
    assert [k["id"] for k in j["kinds"]] == ["franchise", "franchise_reply"]          # PR-F4: + head office's replies
    st = b.get("/api/push/status").json()
    assert st["last"] is None and st["devices"] == st["my_devices"] == 1                     # no chain figure, no chain count
    # muting is per device and only the franchise kind is known to it
    j = b.post("/api/push/prefs", json={"endpoint": EP, "off": ["franchise", "morning"]}).json()
    assert "franchise" in j["off"]
    b.post("/api/push/prefs", json={"endpoint": EP, "off": []})
    # a centre login is never offered the franchise kind
    mk = [k["id"] for k in pushed["mg"].post("/api/push/prefs", json={"endpoint": "https://push.example/f2-mg"}).json()["kinds"]]
    assert "franchise" not in mk and "franchise_reply" not in mk and "morning" in mk


def test_the_franchise_login_reaches_no_other_push_path_and_removes_only_its_own_device(pushed):
    b = pushed["b"]
    r = b.get("/api/push")
    assert r.status_code == 403 and r.headers.get("X-Denied") == "role"
    r = b.post("/api/push/unsubscribe", json={"endpoint": "https://push.example/f2-mg"})          # the manager's device
    assert r.status_code == 200 and r.json()["removed"] == 0
    with _db() as c:
        assert c.execute("SELECT COUNT(*) FROM push_subscription WHERE endpoint='https://push.example/f2-mg'").fetchone()[0] == 1
        assert c.execute("SELECT branch FROM push_subscription WHERE endpoint=?", (EP,)).fetchone()[0] == "BranchB"


def test_the_test_push_tells_the_owner_what_it_will_receive(pushed):
    import main
    from routers import push_api as P
    sent = []
    orig = P._default_send
    P._default_send = lambda sub, data: (sent.append((sub["endpoint"], json.loads(data))), 201)[1]
    try:
        r = pushed["b"].post("/api/push/test")
    finally:
        P._default_send = orig
    assert r.status_code == 200, r.text
    assert [e for e, _ in sent] == [EP] and "ร้านของฉัน" in sent[0][1]["body"]


def test_a_franchise_device_never_receives_a_chain_kind(pushed):
    import main
    from routers import push_api as P
    log = []
    send = lambda sub, data: (log.append(sub["endpoint"]), 201)[1]
    with main.get_db() as con:
        for kind in ("morning", "weekly", "drop", "stale", "cash", "disc"):
            P.send_to_all(con, {"title": "t", "body": "รายได้ทั้งเครือ", "url": "/"}, send=send, chain_only=True, kind=kind)
        con.commit()
    assert EP not in log and "https://push.example/f2-mg" in log
    # the supply lines go by role: a franchise login is not one of them
    assert "franchise" not in P.SUPPLY_ROLES


def test_the_morning_message_is_built_from_the_summary_and_the_controls(pushed, f2data):
    import main
    from routers import franchise as F
    from routers import push_api as P
    log = []
    send = lambda sub, data: (log.append((sub["endpoint"], json.loads(data))), 201)[1]
    assert P.maybe_send_franchise(now=datetime(2026, 6, 4, 8, 29), send=send) is None                 # not yet 08:30
    rec = P.maybe_send_franchise(now=datetime(2026, 6, 4, 8, 30), send=send)
    assert [e for e, _ in log] == [EP], rec                                                            # the owner's own device only
    d = F.franchise_summary_data(2, None)
    want = F.franchise_morning_payload(d, F.franchise_insight_data(2, d["month"]), today=date(2026, 6, 4))
    got = log[0][1]
    assert got == want and got["url"] == "/?go=franchise" and got["tag"] == "oshinei-franchise"
    assert got["title"].startswith("ร้านของฉัน · เมื่อวาน (3 มิ.ย.) ฿")
    ld = d["sales"]["last_day"]["date"]
    row = [x for x in d["sales"]["daily"] if x["date"] == ld][0]
    assert ld == "2026-06-03" and F._fmt_k(row["revenue"]) in got["title"]
    flags = F.franchise_insight_data(2, d["month"])["controls"]["flags"]["items"]
    assert {i["key"] for i in flags if i["level"] == "fix"} >= {"cash", "voids"}      # a day short by ฿2,500; 4 voids of 7 bills
    n_fix = len(d["verdict"]["fix"]) + sum(1 for i in flags if i["level"] == "fix" and i["key"] != "discount")
    assert f"ต้องแก้ {n_fix} รายการ" in got["body"] and got["fix_count"] == n_fix
    assert rec["payloads"][0]["branch"] == "BranchB" and rec["sent"] == 1
    assert P.maybe_send_franchise(now=datetime(2026, 6, 4, 9, 0), send=send) is None and len(log) == 1  # once a day
    # a muted device is skipped
    with _db() as c:
        c.execute("UPDATE push_subscription SET off='franchise' WHERE endpoint=?", (EP,))
        c.commit()
    rec = P.maybe_send_franchise(now=datetime(2026, 6, 5, 8, 30), send=send)
    assert len(log) == 1 and rec["muted"] == 1


def test_the_message_builder_without_keys_still_records_what_it_would_send(pushed, monkeypatch):
    from routers import push_api as P
    monkeypatch.delenv("OSHINEI_VAPID_PRIVATE", raising=False)
    monkeypatch.delenv("OSHINEI_VAPID_PUBLIC", raising=False)
    log = []
    rec = P.maybe_send_franchise(now=datetime(2026, 6, 4, 8, 30), send=lambda s, d: (log.append(d), 201)[1])
    assert log == [] and rec["configured"] is False and rec["payloads"] and rec["payloads"][0]["title"].startswith("ร้านของฉัน")


def test_the_payload_words(monkeypatch):
    from routers import franchise as F
    d = {"available": True, "sales": {"last_day": {"date": "2026-10-20", "revenue": 52300.0}, "mtd": 900000.0,
                                      "daily": [{"date": "2026-10-20", "revenue": 52300.0, "vs_last_week_pct": -4.2}]},
         "target": {"source": "manual", "vs_to_date_pct": 3.25}, "verdict": {"fix": ["a"]}}
    ins = {"controls": {"flags": {"items": [{"level": "fix", "key": "discount"}, {"level": "fix", "key": "voids"},
                                            {"level": "watch", "key": "cash"}]}}}
    p = F.franchise_morning_payload(d, ins, today=date(2026, 10, 21))
    assert p["title"] == "ร้านของฉัน · เมื่อวาน (20 ต.ค.) ฿52.3K ▼4.2% เทียบวันอังคารก่อน"
    assert p["body"] == "ยอดสะสมเดือนนี้สูงกว่าเป้าถึงวันนี้ 3.2% · ต้องแก้ 2 รายการ"        # the discount line counted once
    p = F.franchise_morning_payload({**d, "target": {"source": "none"}, "verdict": {"fix": []}}, None, today=date(2026, 10, 25))
    assert p["title"].startswith("ร้านของฉัน · วันล่าสุดที่ปิดการขาย (20 ต.ค.)") and p["body"].endswith("ไม่มีรายการต้องแก้")
    assert F.franchise_morning_payload({"available": False}, None) is None


def test_the_scheduler_runs_the_franchise_step():
    from pathlib import Path
    src = (Path(__file__).resolve().parent / "main.py").read_text(encoding="utf-8")
    assert "_push_api.maybe_send_franchise" in src


def test_the_franchise_module_imports_its_neighbours_relatively():
    """On Fly the app is backend.main: an absolute `from routers.X import` inside a router loads X a second time and its
    @ttl_cache names collide (the summary then reads as 'no data'). The router must import its neighbours relatively."""
    from pathlib import Path
    import re
    src = (Path(__file__).resolve().parent / "routers" / "franchise.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*from routers[. ]", src, re.M) and not re.search(r"^\s*import routers", src, re.M)


def test_void_bands_owner_2026_10_22():
    """Owner 2026-10-22: voided bills ควรดู above 0.5 %, ต้องแก้ above 1 % of all bills."""
    from routers import franchise as F
    assert (F.T_CANCEL_WATCH, F.T_CANCEL_FIX) == (0.5, 1.0)

    def level(rate):
        f = F._control_flags({}, {"bills": 5, "baht": 1000, "rate_pct": rate}, {"days": []})
        return [i["level"] for i in f["items"] if i["key"] == "voids"]
    assert level(0.5) == [] and level(0.6) == ["watch"] and level(1.0) == ["watch"] and level(1.2) == ["fix"]
