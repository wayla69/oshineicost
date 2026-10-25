# -*- coding: utf-8 -*-
"""PR-PP2 · ONE period control per page (owner 2026-10-24: "ถ้ามีสองอันแบบนี้จะซ้ำซ้อนไหมครับ").

The top bar (#gPeriod) is the only period control: no page slice mounts a periodBar of its own, the migrated pages declare
the kinds they can show in TABS[tab].follow.period.kinds (วัน only on ยอดขายรายวัน, เดือน only on the month-grain pages),
the old page pickers are gone or hidden, and the range shows once as the chip beside the page title (#gpRangeChip)."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
JS = {p.name: p.read_text(encoding="utf-8") for p in (REPO / "js").glob("*.js")}
CORE = JS["01-core.js"]
INDEX = (REPO / "index.html").read_text(encoding="utf-8")


def _tab(name):
    m = re.search(rf"^  {name}: \{{.*$", CORE, re.M)
    assert m, name
    return m.group(0)


def test_no_page_mounts_a_period_bar_of_its_own():
    calls = [(f, line.strip()[:60]) for f, s in JS.items() for line in s.splitlines()
             if re.search(r"\bperiodBar\(", line) and not line.lstrip().startswith("//")
             and not line.startswith("function periodBar(")]
    assert calls == [], calls


def test_pages_declare_their_kinds_and_the_top_bar_has_a_hidden_day_chip():
    assert "follow: { period: { kinds: ['day', 'week', 'month', 'year'] } }" in _tab("dailysales")
    for t in ("cashctl", "custtype", "pricezone", "cancelbill"):
        assert "period: { kinds: ['month'] }" in _tab(t), t
    for t in ("guest", "buffetperf", "timeflow", "staff", "engineering"):
        assert "period: { kinds: ['month', 'year'] }" in _tab(t), t
    assert "period: { kinds: ['week', 'month', 'range'] }" in _tab("opsreport")
    assert "follow: { month: 'cmpMonth', period: { kinds: ['month'] }" in _tab("trend")      # แนวโน้ม: A = the top bar's month
    assert re.search(r'data-k="day"[^>]*hidden>วัน</button>', INDEX)
    assert 'id="gpRangeChip"' in INDEX
    assert "gPeriodForTab(name);" in CORE                      # switchTab applies the page's kinds before it loads


def test_the_old_page_pickers_are_gone_or_hidden():
    ds = (REPO / "tabs" / "02-dailysales.html").read_text(encoding="utf-8")
    assert 'id="dsDate"' not in ds and 'id="dsPeriod"' not in ds and 'id="dsRange"' not in ds
    for f, sel in (("05-opsreport", "opMonth"), ("10-cashctl", "ccMonth"), ("11-custtype", "ctMonth"),
                   ("16-pricezone", "pzMonth"), ("17-cancelbill", "cbMonth")):
        s = (REPO / "tabs" / f"{f}.html").read_text(encoding="utf-8")
        assert f'<select id="{sel}" class="hidden"' in s, f
    g = (REPO / "tabs" / "19-guest.html").read_text(encoding="utf-8")
    assert re.search(r'<div class="card [^"]*\bhidden\b[^"]*"[^>]*>\s*<span[^>]*>มุมมอง:', g)
    assert "guestMonthPicker');\n        if (sel) { sel.value" not in JS["08-guest-buffet.js"]   # a tapped month moves the top bar
    d = (REPO / "tabs" / "35-discounts.html").read_text(encoding="utf-8")
    assert re.search(r'<div class="card [^"]*\bhidden\b[^"]*"[^>]*>\s*<span[^>]*>ดูช่วงวัน', d)       # ส่วนลด: the top bar's days
    o = (REPO / "tabs" / "37-overall.html").read_text(encoding="utf-8")
    assert re.search(r'flex-wrap hidden"[^>]*>.*\n\s*<span class="text-slate-400">เทียบเดือน:', o)   # the movers' month pair
