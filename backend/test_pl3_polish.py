# -*- coding: utf-8 -*-
"""PL3 (RV3 F5-F37): the screen polish that can be pinned in the source.

  * the polish sheet is linked after theme-vivid.css and carries the rules the review asked for (the phone tap floor, the supply bar as one row, the
    header ink on the gradient, the text floor, the grids that stay one column, the branch strip, the mgt report on a phone);
  * explanatory prose on the old analytics pages is behind a "?" (a fragment keeps at most 3 explanatory blocks; every "?" points at a template);
  * one number format (a negative figure is shortened like a positive one), the report opens in Thai, no "กรุณา" / "ระบบจะ" / "ฉบับร่าง" in the
    strings a person reads, no developer words (file names, API key names) on a page, the market card is Thai;
  * a franchise summary line is a sentence and a "ถาม" chip; the note is behind the "?".
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_less_text import explanatory_blocks, LIMIT

REPO = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _src(rel):
    return (REPO / rel).read_text(encoding="utf-8")


# ── the sheet ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_polish_sheet_is_linked_last_and_carries_the_rules():
    html = _src("index.html")
    order = [html.index(x) for x in ("theme-formal.css", "theme-groups.css", "theme-vivid.css", "theme-polish.css")]
    assert order == sorted(order) and html.index("theme-polish.css") > html.rindex("</style>", 0, html.index("</head>"))
    css = _src("docs/ui-kit/theme-polish.css")
    for needle in ("min-height: 44px !important",                       # the phone tap floor, over an id-scoped 34px
                   "#clusterBar.supply-bar { flex-wrap: nowrap; overflow-x: auto;",   # the supply bar is one scrolling row
                   "h2.text-base { display: none; }",                   # ... without its repeated title card
                   "thead th, .wt th):not(.tbl-stick",                   # white header ink on the gradient
                   '[class~="grid-cols-1"][class*="lg:grid-cols-"]',     # a one-column grid stays one column on a phone
                   "#tab-menucompare :is(input[type=\"month\"]",         # A/B pickers
                   "#tab-mgtreport #mgtRptBody :is(svg text",            # the report's small print on a phone
                   "body .section-title { flex-wrap: wrap",              # a title with a note beside it
                   ".brn-cluster { height: auto !important",             # the branch strip grows to its controls
                   "body .logo-mark { color: #1f3a6b; }"):
        assert needle in css, needle
    # the sheet takes no colour of its own: every colour is a token, white on the header gradient, or the logo's own navy / the dark header blue
    hexes = set(re.findall(r"#[0-9a-fA-F]{3,8}\b", re.sub(r"/\*.*?\*/", "", css, flags=re.S)))
    assert hexes <= {"#ffffff", "#1f3a6b", "#3a3590", "#ec4899"}, hexes


def test_the_polish_text_floor_names_no_size_under_12px():
    css = re.sub(r"/\*.*?\*/", "", _src("docs/ui-kit/theme-polish.css"), flags=re.S)
    for m in re.finditer(r"font-size:\s*([0-9.]+)px", css):
        assert float(m.group(1)) >= 12, m.group(0)


# ── "?" instead of prose ────────────────────────────────────────────────────────────────────────────────────────────────
FOLDED = ["01-home", "13-menuefficiency", "14-menuactionboard", "15-menuopportunities", "18-closetime", "19-guest", "21-buffetperf", "22-gpbranch",
          "23-branchbom", "27-heavyeater", "28-menucompare", "31-more", "33-staff", "34-childbuffet", "35-discounts", "37-overall", "38-menu", "41-report",
          "49-feedback"]


@pytest.mark.parametrize("name", FOLDED)
def test_an_old_analytics_page_keeps_its_explanations_behind_a_question_mark(name):
    blocks = explanatory_blocks(f"tabs/{name}.html")
    assert len(blocks) <= LIMIT, f"{name}: {len(blocks)} explanatory blocks\n" + "\n".join(f"  line {ln} <{t}> {txt[:80]}" for ln, t, txt in blocks)


def test_every_question_mark_of_a_fragment_points_at_a_template_that_exists():
    for f in sorted((REPO / "tabs").glob("*.html")):
        s = f.read_text(encoding="utf-8")
        ids = set(re.findall(r'<template\b[^>]*\bid="([^"]+)"', s))
        for h in re.findall(r'data-help="([^"]+)"', s):
            assert h in ids, (f.name, h)
        # a template id is unique on a page
        tpl = re.findall(r'<template\b[^>]*\bid="([^"]+)"', s)
        assert len(tpl) == len(set(tpl)), (f.name, [t for t in tpl if tpl.count(t) > 1][:3])


def test_a_value_a_script_fills_in_behind_a_question_mark_is_found_in_its_template():
    core = _src("js/01-core.js")
    assert "function tplById(" in core and ".content.getElementById(id)" in core
    for rel, ident in (("js/06-features.js", "gpbTargetText"), ("js/10-ordering.js", "opTolLabel"), ("js/11-feedback.js", "fbSlaDays")):
        s = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        assert f"tplById('{ident}')" in s and f"document.getElementById('{ident}')" not in s, (rel, ident)
    assert 'id="gpbTargetText"' in _src("tabs/22-gpbranch.html")


def test_the_page_narrative_of_the_gp_page_is_behind_its_heading():
    f = _src("js/06-features.js")
    body = f.split("function _gpbRenderNarrative(")[1].split("function _gpbRenderBoxes")[0]
    assert "uiQ('gpb:narr'" in body and "el.innerHTML = uiQ(" in body


def test_the_franchise_summary_is_short_lines_with_a_ask_chip_and_the_note_behind_a_question_mark():
    f = _src("js/17-franchise.js")
    v = f.split("function _frcVerdict(")[1].split("function _frcSales")[0]
    assert ">ถาม</button>" in v and "สอบถามเรื่องนี้</button>" not in v
    assert "uiQ('frc:verdict'" in v and 'class="ms-row" style="${_FRC_MUTED};font-size:12px">${esc(v.note)}' not in v


# ── one number format · Thai first · formal words ────────────────────────────────────────────────────────────────────────
@needs_node
def test_a_negative_figure_is_shortened_like_a_positive_one(tmp_path):
    core = _src("js/01-core.js")
    line = next(l for l in core.splitlines() if l.startswith("const fmt  = n =>"))
    js = tmp_path / "f.js"
    js.write_text("const _num = v => (v == null || v === '' || isNaN(v)) ? null : Number(v);\n" + line +
                  "\nconsole.log(JSON.stringify([fmt(-155931), fmt(155931), fmt(-47834), fmt(-98), fmt(-0.2), fmt(0), fmt(1250000), fmt(-1250000), fmt(null)]));", encoding="utf-8")
    out = json.loads(subprocess.run([NODE, str(js)], capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    assert out == ["-155.9K", "155.9K", "-47.8K", "-98", "0", "0", "1.25M", "-1.25M", "—"]


def test_the_management_report_opens_in_thai_unless_english_was_chosen():
    s = _src("js/02-mgt-report.js")
    assert "=== 'en' ? 'en' : 'th'" in s and "return 'th'; } })();" in s


def test_the_money_columns_of_the_issue_summary_are_whole_baht():
    s = _src("js/13-bom-center.js")
    assert "const money = v => v == null ? '<span class=\"wt-muted\">—</span>' : '฿' + _bmcNum(v, 0);" in s


BANNED = re.compile(r"กรุณา|ระบบจะ|ฉบับร่าง")


@pytest.mark.parametrize("rel", sorted(str(p.relative_to(REPO)).replace("\\", "/") for p in
                                       list((REPO / "js").glob("*.js")) + list((REPO / "tabs").glob("*.html"))
                                       + [REPO / "backend/routers" / n for n in ("ingredients.py", "reports.py")]
                                       + [REPO / "backend/main.py", REPO / "backend/auth.py", REPO / "scripts/branch_accounts.py"]))
def test_no_banned_word_in_what_a_person_reads(rel):
    s = (REPO / rel).read_text(encoding="utf-8", errors="replace")
    left = [m.group(0) for m in BANNED.finditer(s)]
    assert not left, (rel, left[:3])


DEV_WORDS = ("backend/mark_downtime.py", "ANTHROPIC_API_KEY", "unit_cost ใน Settings", "ตั้ง Claude ได้ใน Checklist")


def test_no_developer_word_on_a_page():
    for rel in ("js/11-feedback.js", "js/08-guest-buffet.js", "backend/main.py", "tabs/49-feedback.html", "tabs/48-settings.html"):
        s = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        if rel == "backend/main.py":
            s = s.split("def data_quality(")[1].split("def ")[0] if "def data_quality(" in s else s
        for w in DEV_WORDS:
            if rel == "tabs/48-settings.html" and w == "unit_cost ใน Settings":
                continue
            assert w not in s, (rel, w)


def test_english_labels_of_the_old_pages_are_thai_and_the_market_card_too(client):
    for rel, bad in (("js/09-analysis-tabs.js", ("'Total Discounted'", "'Discount Transactions'", "'Total Staff Revenue'", "'Network Avg Ticket'")),
                     ("js/05-pnl-settings.js", ("label:'Overall Margin'", "Insights:</span>")),
                     ("js/09e-menu-compare-bom.js", ("Search SKU/description", "Scenario:\n")),
                     ("tabs/36-timeflow.html", ("Click a cell to see detail",)), ("tabs/39-trend.html", ("(selected month)",)),
                     ("js/01-core.js", ("['simhub','Simulators']",))):
        s = _src(rel)
        for b in bad:
            assert b not in s, (rel, b)
    j = client.get("/api/market").json()
    for x in j["macro"] + j["industry"]:
        assert re.search(r"[฀-๿]", x["label"]) and re.search(r"[฀-๿]", x["sub"]), x
    assert re.search(r"[฀-๿]", j["as_of"])
