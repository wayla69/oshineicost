# -*- coding: utf-8 -*-
"""E7 — the porting boundary: every host coupling of the customer-voice module
goes through feedback_host.py, the host contract is complete here, and the
module's files never import the host application directly."""
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import feedback_host as H  # noqa: E402

MODULE_FILES = ["routers/feedback.py", "routers/feedback_actions.py", "routers/survey.py", "routers/feedback_ai.py",
                "routers/feedback_business.py", "routers/feedback_competitors.py", "feedback_nlp.py", "notify.py",
                "fetch_feedback.py"] + [f"feedback_sources/{p.name}" for p in (HERE / "feedback_sources").glob("*.py")]
FORBIDDEN = (re.compile(r"^\s*import auth\b", re.M), re.compile(r"^\s*from core import", re.M),
             re.compile(r"^\s*from routers\.checklist import", re.M), re.compile(r"^\s*import core\b", re.M),
             re.compile(r"^\s*from main import|^\s*import main\b", re.M))


def test_host_contract_is_complete_and_callable():
    for name in H.CONTRACT:
        assert hasattr(H, name), name
    assert len(H.thai_today()) == 10 and len(H.thai_now()) == 19
    assert H.who(None, {"by": "เก่ง"}) == "เก่ง" and H.who(None, {}) == "local" and H.scope_branch(None) is None
    import sqlite3
    con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
    assert H.branch_rows(con) == [] and H.menu_names(con) == [] and H.sales_available(con) is False
    con.execute("CREATE TABLE branches(branch_id INTEGER, name TEXT, branch_type TEXT, active INTEGER)")
    con.execute("INSERT INTO branches VALUES (2, 'B', 'franchise', 0), (1, 'A', 'master', 1), (9, 'ALL', 'x', 1)")
    rows = H.branch_rows(con)
    assert [r["name"] for r in rows] == ["A", "B"] and rows[0]["active"] is True and rows[1]["active"] is False and rows[1]["branch_type"] == "franchise"


def test_module_files_never_import_the_host_directly():
    bad = []
    for rel in MODULE_FILES:
        src = (HERE / rel).read_text(encoding="utf-8")
        for pat in FORBIDDEN:
            if pat.search(src):
                bad.append((rel, pat.pattern))
    assert not bad, f"host coupling outside feedback_host.py: {bad}"


def test_module_tables_are_preserved_across_the_db_push():
    import core
    for t in ("customer_feedback", "feedback_sync_log", "feedback_webhook_events", "feedback_survey_link", "feedback_action", "feedback_competitor"):
        assert core._preserved_pred("name").replace("name", f"'{t}'") and (t in core._PRESERVED_EXACT or t.startswith(core._PRESERVED_PREFIXES))
