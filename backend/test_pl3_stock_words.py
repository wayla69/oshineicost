# -*- coding: utf-8 -*-
"""PL3 (RV3 F7, owner 2026-10-23): in stock mode the analytics pages say "ต้นทุนต่อจาน" / dish cost - never BoM or recipe.

The words reach a page from three places: the page fragments (tabs/*.html), the strings a slice draws at run time, and the server (insight
sentences, the dish-cost source label). js/01-core.js keeps ONE rewrite list (_STK_WORDS, stkText) that runs on the text of every analytics page
while the mode is 'stock'; the supply, franchise, manual and settings pages are not touched, and full mode keeps its own words.

This module reads the rewrite list out of the source, runs it in node over the text of the analytics fragments and over every string literal of the
analytics slices, and fails when a BoM or recipe word is left; it also pins the cases the rewrite is made for and that full mode is not touched.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_less_text import _js_literals, _TAG

REPO = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

# the pages of the supply module, the franchise owner, the manual and the settings are not analytics pages (the rewrite skips them too)
SKIP_TABS = {"47-manual", "48-settings", "50-checklist", "51-ingpurchase", "52-bomcenter", "53-branchstock", "54-orderhub", "55-supplyhome",
             "56-franchise", "57-launcher", "58-allpages", "59-fmsg", "07-ordering", "08-ordersetup",
             # recipe pages: they are not offered in stock mode at all (tabAllowed / bomHidden)
             "09-menuimpact", "23-branchbom", "24-bomcost"}
# the slices that draw analytics pages (the supply slices 13-17, the launcher and the phone shell are not analytics)
ANALYTICS_SLICES = ["js/02-mgt-report.js", "js/03-tabs-main.js", "js/04-report-cost.js", "js/05-pnl-settings.js", "js/06-features.js",
                    "js/07-home.js", "js/08-guest-buffet.js", "js/09-analysis-tabs.js", "js/09b-cost-pnl.js", "js/09e-menu-compare-bom.js"]
RECIPE_WORD = re.compile(r"BoM|สูตร")


def _src(rel):
    return (REPO / rel).read_text(encoding="utf-8")


def _rewrite_code():
    core = _src("js/01-core.js")
    a = core.index("// ── PL3 stock words")
    b = core.index("// ── end PL3 stock words")
    return core[a:b]


def _run(strings, mode="stock"):
    """The text of each string after stkText, run by node."""
    prelude = f"let BC_MODE = {json.dumps(mode)};\nfunction stkWordsStart() {{}}\n" + _rewrite_code()
    # the DOM helpers in the block are not called here; only _STK_WORDS and stkText are
    prelude = re.sub(r"const _STK_SKIP[\s\S]*", "", prelude) if "const _STK_SKIP" in prelude else prelude
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        data = Path(d) / "in.json"
        data.write_text(json.dumps(strings, ensure_ascii=False), encoding="utf-8")
        js = Path(d) / "run.js"
        js.write_text(prelude + "\nconst fs = require('fs');\nconst xs = JSON.parse(fs.readFileSync(" + json.dumps(str(data)) + ", 'utf8'));\n"
                      "fs.writeFileSync(" + json.dumps(str(Path(d) / "out.json")) + ", JSON.stringify(xs.map(stkText)));\n", encoding="utf-8")
        subprocess.run([NODE, str(js)], capture_output=True, text=True, encoding="utf-8", check=True)
        return json.loads((Path(d) / "out.json").read_text(encoding="utf-8"))


@needs_node
def test_the_cases_the_rewrite_is_made_for():
    cases = {
        "ต้นทุน BoM พุ่งในเดือน กรกฎาคม 2026 — 60.0% ของรายได้": "ต้นทุนอาหารพุ่ง",
        "กำไรขั้นต้น (BoM)": "กำไรขั้นต้น",
        "ต้นทุน BoM รวม": "ต้นทุนรวม",
        "ใช้สูตร BoM เท่านั้น": "ใช้ต้นทุนต่อจาน",
        "56.1% ของรายได้ · BoM · MTD 1–19": "56.1% ของรายได้ · MTD 1–19",
        "BoM Cost รวม": "dish cost รวม",
        "BoM": "ต้นทุนต่อจาน",
        "สูตรเสนอ": "ต้นทุนเสนอ",
        "BoM basis · last month 56.6%": "dish-cost basis · last month 56.6%",
        "Food cost is running at 56% of sales (BoM basis)": "Food cost is running at 56% of sales",
    }
    got = _run(list(cases))
    for src, out in zip(cases, got):
        assert out.startswith(cases[src]) or cases[src] in out, (src, out)
        assert "BoM" not in out and "BoM" not in got[0]


@needs_node
def test_full_mode_keeps_its_own_words():
    xs = ["ต้นทุน BoM รวม", "ใช้สูตร BoM เท่านั้น", "BoM Cost", "สูตรที่ประกาศใช้"]
    assert _run(xs, mode="full") == xs


@needs_node
def test_a_text_without_the_words_is_returned_as_it_is():
    xs = ["ยอดขายรวม", "Top 60", "", "ต้นทุนต่อจาน 12.5"]
    assert _run(xs) == xs


class _Text:
    """The visible and the help text of a fragment (templates included: a help is read on a tap), tags removed."""

    @staticmethod
    def of(html):
        html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
        return [t.strip() for t in re.split(r"<[^>]*>", html) if t.strip()]


@needs_node
@pytest.mark.parametrize("path", sorted(p for p in (REPO / "tabs").glob("*.html") if p.stem not in SKIP_TABS), ids=lambda p: p.stem)
def test_no_recipe_word_is_left_on_an_analytics_page_in_stock_mode(path):
    texts = [t for t in _Text.of(path.read_text(encoding="utf-8")) if RECIPE_WORD.search(t)]
    left = [t for t in _run(texts) if RECIPE_WORD.search(t)]
    assert not left, f"{path.name}: {len(left)} text(s) still name BoM / a recipe in stock mode, e.g. {left[:3]}"


@needs_node
@pytest.mark.parametrize("rel", ANALYTICS_SLICES)
def test_no_recipe_word_is_left_in_what_an_analytics_slice_draws_in_stock_mode(rel):
    """Every Thai or English sentence a slice builds with BoM or สูตร in it comes out of the rewrite free of both (code-like strings - class
    names, endpoints, keys - are not text and are left out)."""
    texts = []
    for _, body in _js_literals(_src(rel)):
        for t in _TAG.sub("\x00", body).split("\x00"):
            t = t.strip()
            if RECIPE_WORD.search(t) and re.search(r"[฀-๿]|[A-Za-z]{3,} [A-Za-z]{2,}", t) and not re.search(r"[{};=]|^\s*/", t):
                texts.append(t)
    left = [t for t in _run(texts) if RECIPE_WORD.search(t)]
    assert not left, f"{rel}: {len(left)} string(s) still name BoM / a recipe in stock mode, e.g. {left[:4]}"


def test_the_rewrite_is_wired_to_the_mode_the_charts_and_the_dish_cost_page():
    core = _src("js/01-core.js")
    assert "stkWordsStart()" in core.split("async function loadBcMode()")[1].split("\n}\n")[0]      # started when the mode is loaded, stock only
    assert "stkChartWords(config);" in core                                                           # chart labels
    assert "_STK_SKIP" in core and "#tab-bomcenter" in core and "#bmc-pane-dishcost" in core          # supply pages skipped, the dish-cost pane is not
    assert (REPO / "backend/bom_center/dish_cost.py").read_text(encoding="utf-8").count("ต้นทุนที่ประกาศใช้") == 1


def test_the_dish_cost_source_label_names_no_recipe_in_stock_mode(client, app_db, monkeypatch):
    import sqlite3
    from bom_center import close_settings
    from bom_center import dish_cost as DC
    con = sqlite3.connect(str(app_db))
    con.row_factory = sqlite3.Row
    try:
        for mode, want in (("stock", "ต้นทุนที่ประกาศใช้"), ("full", "สูตรที่ประกาศใช้")):
            monkeypatch.setattr(close_settings, "MODE_DEFAULT", mode)
            label = DC._source_of(con, "2026-10")
            # a SKU that is not in the released recipe map is "ต้นทุนเดิม" / "ไม่มีต้นทุน" in both modes
            assert label(-1, 5.0) == "ต้นทุนเดิม" and label(-1, 0.0) == "ไม่มีต้นทุน"
            assert want in DC._source_of.__code__.co_consts or want in Path(DC.__file__).read_text(encoding="utf-8")
    finally:
        con.close()
