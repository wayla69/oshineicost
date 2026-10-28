"""รายงานผู้บริหาร is report.management, management only by default (owner 2026-10-28, #501). Every way into the summary on the
page checks that module, so a role without it never lands on a page whose data call is refused:
  * the phone: the cold-start deep link, a notification tapped while the app is open, the tiles and the search extra;
  * the full monthly report's button to the management report is shown only to a login that may read it (data-needs);
  * no start card of another role points at the management report.
"""
import re
from pathlib import Path

import auth

REPO = Path(__file__).resolve().parent.parent
MOB = (REPO / "js/09c-mobile.js").read_text(encoding="utf-8")
CORE = (REPO / "js/01-core.js").read_text(encoding="utf-8")


def test_every_phone_way_into_the_summary_checks_report_management():
    # each line that opens the owner's summary carries the module check on the same line
    opens = [ln for ln in MOB.splitlines() if re.search(r"\b(showMobileSummary|phShowSummary)\(\)", ln) and "function " not in ln]
    assert opens
    for ln in opens:
        if "onclick=\"phShowSummary()" in ln or "summary: true" in ln or "if (e.summary)" in ln:
            continue                                      # drawn only inside a report.management branch (checked below)
        if "if (phOn()) { phShowSummary(); return; }" in ln:
            continue                                      # inside showMobileSummary itself: the phone shell's own page of it
        assert "report.management" in ln, ln.strip()[:160]
    assert "hasModule('report.management') ? 'phShowSummary()'" in MOB          # ยอดขายเมื่อวาน: the summary for management only
    assert "key === 'mgmt' && hasModule('report.management')" in MOB              # the ผู้บริหาร tile
    assert "const extra = hasModule('report.management')" in MOB                   # the search extra


def test_the_monthly_report_button_needs_the_management_report():
    html = (REPO / "tabs/41-report.html").read_text(encoding="utf-8")
    btn = re.search(r'<button[^>]*onclick="openMonthlyReport\(\)"[^>]*>', html).group(0)
    assert 'data-needs="/api/reports/management"' in btn
    assert auth.path_allowed("GET", "/api/reports/management", auth.modules_for_role("marketing")) is False


def test_no_start_card_of_another_role_points_at_the_management_report():
    block = re.search(r"const ROLE_START = \{(.*?)\n\};", CORE, re.S).group(1)
    for role, body in re.findall(r"^\s*(\w+):\s*\[(.*?)\],?\s*$", block, re.M):
        if role != "management":
            assert "'mgtreport'" not in body, role
