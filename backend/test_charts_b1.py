# -*- coding: utf-8 -*-
"""Charts in theme B (VZ2): bar style B1 app-wide, through the shared glance layer in js/01-core.js and the phone SVG columns.

The promises:
  * the B1 classifier (above / below the period average, weekend, latest finished period, best period) is applied to a one-series
    period bar chart (days, weeks, months) and NOT to stacked, multi-series, horizontal or category-axis charts;
  * the period still in progress is never "latest", never in the average (the closed-day rule stays);
  * under 4 finished periods there is no classifier (an average of three says nothing);
  * the canvas palette (B1_PAL) equals the --ch-* tokens, light and dark; the old twelve series colours map to the B set;
  * the phone columns (msColumns) wear the same look: gradient defs, gold latest with a dark edge, weekend dots, text >= 12 px.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _src(rel):
    return (REPO / rel).read_text(encoding="utf-8")


def _fn(src, name):
    m = re.search(r"(?:async )?function %s\(" % re.escape(name), src)
    assert m, name
    i = src.index("{", src.index(")", m.end()))
    depth, j = 0, i
    while j < len(src):
        c = src[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
        j += 1
    raise AssertionError("unbalanced " + name)


def _const(src, name):
    m = re.search(r"^(?:const|let) %s = .*?;\s*$" % re.escape(name), src, re.M | re.S)
    assert m, name
    return m.group(0)


def _node(prelude, expr):
    out = subprocess.run([NODE, "-e", prelude + f"\nconsole.log(JSON.stringify({expr}));"], capture_output=True, text=True,
                         encoding="utf-8", check=True).stdout
    return json.loads(out)


CORE = _src("js/01-core.js")
MOB = _src("js/09c-mobile.js")
TOKENS = _src("docs/ui-kit/tokens.css")
FINITE = re.search(r"^const _glanceFinite = v => \{.*?^\};", CORE, re.M | re.S).group(0)
PRE = "\n".join([_const(CORE, "_GLANCE_TIME_RE"), _const(CORE, "_GLANCE_SKIP_RE"), FINITE, _fn(CORE, "glanceB1Eligible"), _fn(CORE, "glanceB1Kinds")])


def _daily(n=30, **kw):
    labels = [f"2026-09-{d:02d}" for d in range(1, n + 1)]
    cfg = {"type": "bar", "data": {"labels": labels, "datasets": [{"label": "ยอดขาย", "data": [100 + (i % 7) * 10 for i in range(n)]}]}}
    cfg.update(kw)
    return cfg


# ── the classifier ───────────────────────────────────────────────────────────────────────────────────────────────────
@needs_node
def test_b1_classifier_marks_above_below_weekend_latest_and_best():
    got = _node(PRE, """(() => {
  const vals = [10, 30, 20, 40, 50, 15, 25, 60, 35];
  const wk = vals.map((v, i) => i === 5 || i === 6);
  const r = glanceB1Kinds(vals, { weekend: wk });
  return { avg: r.avg, latest: r.latest, best: r.best, tone: r.kinds.map(k => k.tone), wk: r.kinds.map(k => k.wk) };
})()""")
    assert got["latest"] == 8 and got["best"] == 7
    assert abs(got["avg"] - sum([10, 30, 20, 40, 50, 15, 25, 60, 35]) / 9) < 1e-9
    # avg = 31.67: 10 lo, 30 lo, 20 lo, 40 hi, 50 hi, 15 lo, 25 lo, 60 hi, 35 is the latest -> "now"
    assert got["tone"] == ["lo", "lo", "lo", "hi", "hi", "lo", "lo", "hi", "now"]
    assert got["wk"] == [False] * 5 + [True, True, False, False]


@needs_node
def test_b1_classifier_keeps_the_period_in_progress_out_of_latest_and_the_average():
    got = _node(PRE, """(() => {
  const r = glanceB1Kinds([100, 100, 100, 100, 100, 1], { partial: 5 });
  return { avg: r.avg, latest: r.latest, best: r.best, part: r.kinds.map(k => k.part), tone: r.kinds.map(k => k.tone) };
})()""")
    assert got["avg"] == 100 and got["latest"] == 4 and got["best"] == 0          # the 1 (today, still trading) is not in the average
    assert got["part"] == [False] * 5 + [True] and got["tone"][4] == "now" and got["tone"][5] == "lo"


@needs_node
def test_b1_classifier_needs_four_finished_periods():
    got = _node(PRE, "[glanceB1Kinds([1, 2, 3], {}), glanceB1Kinds([1, 2, 3, 4], { partial: 3 }), glanceB1Kinds([1, null, 3, 4, 5], {}) !== null]")
    assert got == [None, None, True]


# ── who gets B1 ──────────────────────────────────────────────────────────────────────────────────────────────────────
@needs_node
def test_b1_applies_to_a_one_series_period_bar_chart_only():
    def elig(cfg):
        return _node(PRE, f"glanceB1Eligible({json.dumps(cfg)})")
    assert elig(_daily()) is True                                                                     # sales by day
    months = {"type": "bar", "data": {"labels": ["ม.ค. 69", "ก.พ. 69", "มี.ค. 69", "เม.ย. 69", "พ.ค. 69", "มิ.ย. 69"],
                                      "datasets": [{"label": "ยอดขาย", "data": [5, 6, 7, 8, 9, 10]}]}}
    assert elig(months) is True                                                                       # a monthly axis is periodic too
    two = _daily(); two["data"]["datasets"].append({"label": "ปีก่อน", "data": [90] * 30})
    assert elig(two) is False                                                                         # comparison / multi-series
    line_overlay = _daily(); line_overlay["data"]["datasets"].append({"type": "line", "label": "แนวโน้ม", "data": [100] * 30})
    assert elig(line_overlay) is False
    stacked = _daily(options={"scales": {"x": {"stacked": True}, "y": {"stacked": True}}})
    assert elig(stacked) is False
    assert elig(_daily(options={"indexAxis": "y"})) is False                                          # horizontal
    cats = {"type": "bar", "data": {"labels": ["ขอนแก่น", "โคราช", "อุดร", "สุรินทร์", "อุบล", "นครสวรรค์"],
                                    "datasets": [{"label": "ยอดขาย", "data": [5, 6, 7, 8, 9, 10]}]}}
    assert elig(cats) is False                                                                        # a category axis has no "latest"
    assert elig(_daily(options={"plugins": {"glance": {"b1": False}}})) is False                      # opt-out
    assert elig(_daily(options={"plugins": {"glance": False}})) is False
    assert elig({"type": "line", "data": _daily()["data"]}) is False
    assert elig(_daily(n=4)) is False                                                                 # too few periods


@needs_node
def test_b1_frame_is_built_for_one_series_and_not_for_several():
    pre = "\n".join([_const(CORE, "_GLANCE_TIME_RE"), FINITE, _const(CORE, "B1_PAL"), _fn(CORE, "glanceB1Kinds"),
                     _const(CORE, "_glancePartial"),
                     _const(CORE, "_glanceIsDark"), _fn(CORE, "_glanceB1Frame")])
    pre = "const document = { body: { classList: { contains: () => false } } };\n" + pre
    got = _node(pre, """(() => {
  const mk = (n, metas, b1) => ({ $b1: b1, $gi: { partial: -1 }, canvas: null,
    data: { datasets: metas.map(() => ({ data: Array.from({ length: n }, (_, i) => 10 + (i % 5)) })) },
    getSortedVisibleDatasetMetas: () => metas.map((t, index) => ({ type: t, index })) });
  const gi = { grain: 'day', dates: Array.from({ length: 12 }, (_, i) => ({ y: 2026, m: 9, d: i + 1 })) };
  const one = _glanceB1Frame(mk(12, ['bar'], true), gi), two = _glanceB1Frame(mk(12, ['bar', 'bar'], true), gi);
  const off = _glanceB1Frame(mk(12, ['bar'], false), gi), line = _glanceB1Frame(mk(12, ['line'], true), gi);
  return { one: one && [one.latest, one.kinds.length, one.pal.t1], two, off, line, wk: one.kinds.filter(k => k.wk).length };
})()""")
    assert got["one"] == [11, 12, "#e0a43a"] and got["two"] is None and got["off"] is None and got["line"] is None
    assert got["wk"] == 3          # 2026-09-05/06 (Sat, Sun) and 2026-09-12 (Sat)


# ── palette ──────────────────────────────────────────────────────────────────────────────────────────────────────────
@needs_node
def test_the_canvas_palette_equals_the_ch_tokens_light_and_dark():
    pal = _node(_const(CORE, "B1_PAL"), "B1_PAL")
    assert TOKENS.count("--ch-a1:") == 2
    blocks = [TOKENS[m.start():m.start() + 900] for m in re.finditer(r"--ch-a1:", TOKENS)]
    for name, block in zip(("light", "dark"), blocks):
        tok = dict(re.findall(r"--ch-([a-z0-9]+): (#[0-9a-fA-F]{6})", block))
        p = pal[name]
        for k in ("a1", "a2", "aw1", "aw2", "b1", "b2", "bw1", "bw2", "t1", "t2", "tstroke", "avg"):
            assert tok[k].lower() == p[k].lower(), (name, k)
        for i in range(6):
            assert tok[f"c{i + 1}"].lower() == p["c"][i].lower(), (name, "c", i)
        for i in range(5):
            assert tok[f"s{i + 1}"].lower() == p["s"][i].lower(), (name, "s", i)
    # the heat scale is the sequential set
    html = _src("index.html")
    for name, sel in (("light", ":root {\n  --heat-s1"), ("dark", "body.dark {\n  --heat-s1")):
        i = html.index(sel)
        got = re.findall(r"--heat-s[1-5]:(#[0-9a-fA-F]{6})", html[i:i + 400])
        assert [g.lower() for g in got] == [c.lower() for c in pal[name]["s"]], name


@needs_node
def test_old_series_colours_become_the_b_set_and_keep_their_alpha():
    pre = _const(CORE, "PALETTE") + "\n" + _const(CORE, "B1_PAL") + "\n" + _const(CORE, "_B_OLD") + "\n" + _const(CORE, "_B_EXTRA") + "\n" + _fn(CORE, "glanceBColour")
    got = _node(pre, "[glanceBColour('#2a78d6', false), glanceBColour('#2A78D6', true), glanceBColour('#1baf7a33', false), glanceBColour('#5a9f33', false), "
                     "glanceBColour('#4a70b0', false), glanceBColour('#123456', false), glanceBColour('rgba(1,2,3,.5)', false), glanceBColour(null, false)]")
    assert got == ["#5b6ba8", "#9aa7e0", "#d08a3e33", "#a99cc9", "#5b6ba8", "#123456", "rgba(1,2,3,.5)", None]   # Pastel A (2026-10-31) series set


def test_line_charts_are_2_5_px_and_chart_text_is_at_least_12px():
    assert "Chart.defaults.elements.line.borderWidth = 2.5" in MOB
    assert "ds.borderWidth = 2.5" in CORE
    plugin = CORE[CORE.index("const GLANCE_PLUGIN = {"):]
    for bad in re.findall(r"_glanceText\([^;]*?(?:,\s*)(\d+),\s*\d+\);", plugin):
        assert int(bad) >= 12, bad
    assert not re.search(r"narrow \? 10", CORE)
    assert "font: { size: 11 }" not in CORE and "{ size: 11 }" not in CORE


# ── the phone columns ────────────────────────────────────────────────────────────────────────────────────────────────
ESC = "const esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;');\n"


@needs_node
def test_phone_columns_wear_b1():
    pre = ESC + _fn(CORE, "glanceB1Kinds") + "\n" + _fn(MOB, "msColumns")
    got = _node(pre, """(() => {
  const vals = [50, 52, 49, 90, 95, 51, 53, 55, 88, 99, 54, 60, 61];
  const wk = vals.map((v, i) => [3, 4, 8, 9].includes(i));
  const svg = msColumns(vals, { partial: 12, weekend: wk, labels: vals.map((v, i) => 'd' + i), fmt: v => String(Math.round(v)), grain: 'day', height: 110 });
  const few = msColumns([1, 2, 3], { labels: ['a', 'b', 'c'], fmt: String });
  const sizes = [...svg.matchAll(/font-size="(\\d+)"/g)].map(m => +m[1]);
  return { defs: (svg.match(/<linearGradient/g) || []).length, grad: (svg.match(/fill="url\\(#/g) || []).length, dots: (svg.match(/<circle/g) || []).length,
           edge: svg.includes('--ch-tstroke'), dashed: svg.includes('stroke-dasharray="3 2"'), avgLine: svg.includes('stroke-dasharray="5 3"'),
           minFont: Math.min(...sizes), gold: svg.includes('--gold-i'), few: few.includes('linearGradient') };
})()""")
    assert got["defs"] == 5 and got["grad"] == 12                  # 12 finished columns filled by a gradient; the 13th is the hollow in-progress one
    assert got["dots"] == 4 and got["edge"] and got["dashed"] and got["avgLine"] and got["gold"]
    assert got["minFont"] >= 12
    assert got["few"] is False                                     # under 4 finished periods: the plain accent columns


# ── the period still in progress, every granularity ─────────────────────────────────────────────────────────────────
PERIODS = "\n".join(["const window = { __dataLastDate: '2026-10-19' };", _const(CORE, "_GLANCE_EN_MON"), _const(CORE, "_GLANCE_TH_MON"),
                     _fn(CORE, "_glanceYear"), _fn(CORE, "_glanceParseLabel"), _fn(CORE, "_glanceLastDate"), _fn(CORE, "glancePeriods")])


@needs_node
def test_the_period_in_progress_is_found_for_month_week_quarter_and_year_bars():
    got = _node(PERIODS, """(() => {
  const r = l => { const x = glancePeriods(l, {}); return [x.grain, x.partial]; };
  return {
    month: r(['2026-03-01', '2026-04-01', '2026-05-01', '2026-06-01', '2026-07-01', '2026-08-01', '2026-09-01', '2026-10-01']),
    monthDone: r(['2026-02-01', '2026-03-01', '2026-04-01', '2026-05-01', '2026-06-01', '2026-07-01', '2026-08-01', '2026-09-01']),
    week: r(['2026-09-14', '2026-09-21', '2026-09-28', '2026-10-05', '2026-10-12', '2026-10-19']),
    weekDone: r(['2026-09-07', '2026-09-14', '2026-09-21', '2026-09-28', '2026-10-05', '2026-10-12']),
    quarter: r(['2025-10-01', '2026-01-01', '2026-04-01', '2026-07-01', '2026-10-01']),
    year: r(['2023-01-01', '2024-01-01', '2025-01-01', '2026-01-01']),
    day: r(['2026-10-15', '2026-10-16', '2026-10-17', '2026-10-18', '2026-10-19']),
    note: glancePeriods(['2026-08-01', '2026-09-01', '2026-10-01'], {}).note,
  };
})()""")
    assert got["month"] == ["month", 7] and got["monthDone"] == ["month", -1]
    assert got["week"] == ["week", 5] and got["weekDone"] == ["week", -1]        # the week of 19 Oct holds the last day with data and is not over
    assert got["quarter"] == ["quarter", 4] and got["year"] == ["year", 3]
    assert got["day"][0] == "day" and got["note"] == "19/31 วัน"


@needs_node
def test_b1_never_makes_the_partial_period_latest_or_best_at_any_granularity():
    # the in-progress bar is last and the LARGEST: it still must not be gold, best, or in the average
    got = _node(PRE, """(() => {
  const a = glanceB1Kinds([50, 52, 49, 55, 51, 120], { partial: 5 });
  const b = glanceB1Kinds([50, 52, 49, 55, 51, 120, 130], { partial: 6 });
  return [a.latest, a.best, a.avg, a.kinds[5].part, a.kinds[5].tone, b.latest, b.best, b.avg];
})()""")
    assert got[:5] == [4, 3, 51.4, True, "hi"] and got[5:] == [5, 5, (50 + 52 + 49 + 55 + 51 + 120) / 6]


# ── label collisions ────────────────────────────────────────────────────────────────────────────────────────────────
@needs_node
def test_labels_are_clamped_inside_the_chart_and_never_land_on_a_more_important_one():
    got = _node(_fn(CORE, "glanceLabelSpot"), """(() => {
  const B = { left: 40, right: 200 };
  const edgeR = glanceLabelSpot([], { x: 198, y: 20, w: 60, align: 'center' }, B);      // would clip at the right edge
  const edgeL = glanceLabelSpot([], { x: 41, y: 20, w: 60, align: 'center' }, B);
  const leftAl = glanceLabelSpot([], { x: 190, y: 20, w: 60, align: 'left' }, B);      // a left-aligned end label near the right edge
  const rightAl = glanceLabelSpot([], { x: 45, y: 20, w: 60, align: 'right' }, B);
  const placed = [{ x: 100, y: 20, w: 50 }];                                              // the gold latest label, placed first
  const onTop = glanceLabelSpot(placed, { x: 110, y: 28, w: 60, align: 'center' }, B);   // the average value on it -> no spot
  const apart = glanceLabelSpot(placed, { x: 110, y: 50, w: 60, align: 'center' }, B);   // a line below -> fine
  const side = glanceLabelSpot(placed, { x: 190, y: 20, w: 40, align: 'center' }, B);    // same line, far away -> fine
  return { edgeR: edgeR.cx + 30, edgeL: edgeL.cx - 30, leftAl: leftAl.x + 60, rightAl: rightAl.x - 60, onTop, apart: !!apart, side: !!side };
})()""")
    assert got["edgeR"] <= 200 and got["edgeL"] >= 40 and got["leftAl"] <= 200 and got["rightAl"] >= 40
    assert got["onTop"] is None and got["apart"] and got["side"]


def test_the_average_value_is_placed_last_and_may_move_or_drop():
    plugin = CORE[CORE.index("const GLANCE_PLUGIN = {"):]
    assert plugin.index("2) the primary series") < plugin.index("3) the average's value") < plugin.index("4) name the weekend bands")
    assert "[a.left + 4, ay - 8, 'left'], [a.left + 4, ay + 9, 'left'], [a.right - 4, ay - 8, 'right'], [a.right - 4, ay + 9, 'right']" in plugin


def test_a_table_and_chart_pair_stacks_full_width_on_a_phone():
    html = _src("index.html")
    assert '.content-area [class*="lg:grid-cols-"]:has(> .card canvas) { grid-template-columns: minmax(0, 1fr) !important; }' in html
    assert html.index(':has(> .card canvas)') < html.index(".content-area .card { overflow-x: auto; }")


# ── the change pills on the gradient KPI tiles (coordinator fix 2) ──────────────────────────────────────────────────
def _lum(h):
    h = h.lstrip("#")
    r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def test_change_pills_on_the_tiles_keep_their_ink_and_clear_4_5_to_1():
    # the tile rule forced every span in a tile to inherit the tile's white, which turned msPchip's deep ink white on its pale pill
    css = _src("docs/ui-kit/theme-vivid.css")
    rule = [ln for ln in css.splitlines() if ln.startswith("body .ms-kpi:not(#mobileSummary .ms-kpi) :is(p, span")][0]
    assert ".ms-chip, .ms-chip *" in rule
    for ink, bg in (("#047857", "#dcfce7"), ("#b91c1c", "#fee2e2"), ("#5f6c80", "#f1f5f9")):     # msPchip: up / down / flat, and the none chip
        assert ink in MOB and bg in MOB
        hi, lo = sorted((_lum(ink), _lum(bg)), reverse=True)
        assert (hi + 0.05) / (lo + 0.05) >= 4.5, (ink, bg)
