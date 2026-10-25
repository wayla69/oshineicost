# One period pattern — every sales and cost figure

Owner 2026-10-24: every sales figure, and cost too, follows ONE pattern: week · month · year, and a period not finished is
compared to-date. PR-PP1 adds the helpers; PR-PP2 (sales pages) and PR-PP3 (cost pages) move every page onto them.

## The rule

| | |
|---|---|
| Kinds | `day` (where a page has it) · `week` (Mon–Sun) · `month` · `year` (calendar) · `range` (where a page has a free range) |
| Anchor | the last **closed** day — `periods.anchor(con)` = `rules.latest_settled_day`. The day still trading never enters a window. |
| Unfinished period | to-date: current = the period's first day … the anchor (n days); both bases are their first n days |
| Finished period | whole vs whole (a closed month vs the whole previous month, whatever its length) |
| Two comparisons | every figure shows `เทียบช่วงก่อน` and `เทียบปีก่อน`; the compared ranges go in the tooltip and the "?" |
| No base | "—", never a made-up % (`pct` returns None/null when the base is missing or not above zero) |
| Ratios | GP %, food-cost %, cost per head, per bill: the change in percentage points (`จุด`, `pp`) on the same windows; baht / qty in % |

### The windows, per kind

| Kind | Current (anchor 2026-10-23, Fri) | เทียบช่วงก่อน | เทียบปีก่อน |
|---|---|---|---|
| day | 23 ต.ค. 2569 | the same weekday a week earlier: 16 ต.ค. 2569 | 364 days back (same weekday): 24 ต.ค. 2568 |
| week | 19–23 ต.ค. 2569 (Mon → anchor) | the same weekdays last week: 12–16 ต.ค. 2569 | 364 days back: 20–24 ต.ค. 2568 |
| month | 1–23 ต.ค. 2569 | days 1–23 of last month (clamped to its length): 1–23 ก.ย. 2569 | days 1–23 of the month a year back: 1–23 ต.ค. 2568 |
| year | 1 ม.ค.–23 ต.ค. 2569 | 1 ม.ค.–23 ต.ค. 2568 | the same (one chip is shown) |
| range | from … min(to, anchor) | the first n days of the equal-length window just before | the same calendar dates a year back |

Clamping: 1–30 มี.ค. → 1–28 ก.พ.; 29 ก.พ. a year back → 28 ก.พ.; a finished March vs the whole of February.

Decisions taken in PP1 (the brief left them open):
- **day** compares with the same weekday a week earlier and 364 days back, as the management report's daily mode always has
  (restaurant sales are weekday-seasonal). The brief's "same calendar dates one year back" is kept for month, year and range.
- **year**: เทียบช่วงก่อน and เทียบปีก่อน are the same window, so `periodCmpHtml` draws one chip (เทียบปีก่อน).
- **range**: a range that runs past the anchor is clamped to it; its base is the first n days of the window just before.
- The weekday-fair MoM (`mom_basis='dow'`) stays for the management report §3 pace and the tests that pin it; the headline %
  moves to the calendar to-date rule page by page in PP2/PP3.

## The top bar: ONE period bar (owner 2026-10-24)

`สัปดาห์ · เดือน · ปี · กำหนดเอง` + a stepper `‹ ต.ค. 2569 ›` for the chosen kind (the current period to date by default; ›
stops at it, ‹ at the first data month) + จาก–ถึง for กำหนดเอง. It replaces the วัน/สัปดาห์/เดือน/ไตรมาส/ปี toggle, the two date
pickers, the two month pickers with 3ด./6ด./ปีนี้/12ด. and the เลือกเร็ว row (เดือนนี้ … ทั้งหมด · ปี · ปีก่อน · เทียบปีก่อน ·
กำหนดเอง · ช่วงที่บันทึกไว้). There is no compare filter: every figure shows เทียบช่วงก่อน and เทียบปีก่อน itself.

- **Chart option, not period**: `ช่วงกราฟ` (3 · 6 · 12 เดือน · ทั้งหมด · ทั้งหมด รายไตรมาส · เทียบรายปี) is shown only on
  ภาพรวมยอดขาย, whose trend chart is the one reader (`getTrendParams` → `gPeriodTrendWindow`): the chart ends at the bar's
  period and reaches back that far; its bucket is weekly for สัปดาห์, daily for a กำหนดเอง of up to 62 days, else monthly.
- **Saved ranges** live in one place: the ★ ช่วงที่บันทึก menu. A new one stores `{period}`; an older one
  (`month_from` / `month_to`) still loads — one month becomes that month, a span becomes กำหนดเอง from its first to its last day.
- **Compatibility until PP2/PP3**: the bar writes the hidden `selMonthFrom` / `selMonthTo` / `selDateFrom` / `selDateTo`
  (`#gPeriodState`) and `currentGranularity` (week → weekly, กำหนดเอง → daily, month / year → monthly), so `getMonthFrom`,
  `getMonthTo`, `qp()`, `summaryParams()`, `_filterKey`, `onFilterChange` and `TABS[].follow` keep working unchanged.
  The default kind is the constant `PERIOD_DEFAULT_KIND = 'month'` (js/01-core.js, since PR-PP3): the month to date, day 1 … the
  anchor. Through PP1/PP2 it was `'year'` so that no page still reading the month fields shrank on deploy; with PP3 every sales and
  cost page reads the bar. A device that chose a kind keeps it.
- Phone: one sideways-scrolling row inside the folded tools, the period (stepper) first, 44 px targets.
- API: `gPeriodGet()`, `gPeriodSet({kind, pick, from, to}, silent)`, `gPeriodResolve()` (a `periodRange` result),
  `gPeriodAnchor()`, `getPeriodKind()`; remembered on the device (`oshinei_gperiod`, `oshinei_chartwin`).

## Grain per figure (what a page may offer)

| Source | Grains | Notes |
|---|---|---|
| Sales: `sales_transaction`, `sales_sku_daily`, `sale_payment`, daily `pos_*` | day · week · month · year · range | `sales_sku_daily` starts 2026-03-22 |
| Sales: `sales_month_summary`, `pos_*_month` (customer type, payment, channel, void, coupon, cancel) | month · year | the week chip is hidden, not disabled |
| Cost: price list (`bom_center/prices.py`), dish-cost list (`bom_center/dish_cost.py`), requisitions, month-end counts / variance | month · year | |
| Cost: day-level dish cost (`sales_sku_daily` cost) | day · week from its first day (`period_cost.cost_floor`) | the sync keeps a rolling 120 days (2026-03-22 on the live data at the time of the brief; 2026-06-22 on the deck28 copy), so a part month / week / day before it, and cost YoY on the day / week grain, show "—" with the reason in "?" (`ต้นทุนรายวันมีตั้งแต่ …`) |
| Cost: gpbranch, buffet/gp day / week COGS | day · week, pro-rated | labelled `ประมาณการ` |
| Purchases (`ingpurchase`) | week (native) · month · year | weekly import |
| Per-bill COGS | rolling window only | |

## Helper API

### Backend — `backend/periods.py` (pure; only `anchor` reads the database)

```python
resolve(kind, anchor, today=None, date_from=None, date_to=None) -> {
    "kind", "cur": (from, to), "prev": (from, to), "ly": (from, to),   # ISO strings
    "days", "complete", "label_th", "prev_label_th", "ly_label_th" }   # '1–23 ต.ค. 2569' (Buddhist year)
pct(cur, base)   # % change, 1 decimal; None without a base above zero
pp(cur, base)    # change of a ratio in จุด, 1 decimal
anchor(con, where=..., params=None)   # the last closed day with its bills in
label_th(a, b)
```

- `kind` day | week | month | year | range (`'mtd'` is read as month).
- `date_from`: day/week/month/year = any day of the period wanted (default: the period holding the anchor); range = first day.
- `today`: moves the anchor back to the day before (the day still trading).
- Raises `ValueError` for an unknown kind or a period starting after the anchor.

Delegating today: `routers/sales.py:_mtd_window`, `routers/reports.py:_mgt_month_bounds` and the day / week windows of
`_mgt_window` (same outputs). `backend/to_date.py:to_date_ranges` (PR-OB, the anomaly box) is a thin alias over `resolve`.

PR-PP2 adds the request side (still in `periods.py`; `windows` reads the database, the rest is pure):

```python
windows(con, kind, where, params, pick=None, date_from=None, date_to=None, anchor_day=None)
    # resolve() on one scope's data: anchor = the scope's last closed day; + {anchor, first_day, has_prev, has_ly}
    # (a base that starts before the scope's first day with bills is not compared). A pick after the anchor falls back
    # to the anchor's own period. None without data.
public(w)                 # the `period` object an endpoint returns (the JS periodCmpHtml reads it as its range)
kind_of(g, default)       # 'daily' | 'weekly' | 'monthly' | 'yearly' | 'mtd' | a kind -> the kind
covered(win, first_day)   # the data reaches back to the window's first day
shift(kind, d, n)         # the first day of the period n steps away (the trailing trends)
span_of(month_from, month_to, date_from, date_to)   # a free A/B period as days
guard(spans, anchor, first_day, lead=('a', 'b'))    # the free compare pages: every period ends by the anchor; when the
    # first lead period runs past it, it is to date (n days) and every other period is cut to its own first n days;
    # a base before the data is listed in no_base (no %)
```

### The sales endpoints (PR-PP2)

| Endpoint | Period | Comparisons |
|---|---|---|
| `/api/sales/daily?gran=day\|week\|month\|year&date=` | `windows` (date = any day of the period) | `period`, `total.prev` / `total.ly`, per branch `mom_pct` / `yoy_pct`; `mom_basis` = `to_date` \| `full` (the weekday-fair `dow` is gone here); the trend ends with the period to date (`partial`) |
| `/api/period/sales?kind=&pick=&date_from=&date_to=&by_branch=1` (new) | `windows` | revenue · covers · bills · per head · per bill · discount % for cur / prev / ly (+ per branch); `change` in % (discount in จุด) |
| `/api/period/headline?page=&kind=&pick=` (new) | `windows`; a month-grain source (`pos_customer_type_month`) shows the month in progress without comparison and runs a year over closed months (`grain_note`) | the headline figures of the pages that had no comparison (guest · buffetperf · timeflow · staff · engineering · cashctl · custtype · pricezone · cancelbill · opsreport); a POS table that does not reach a window's first day gives no value |
| `/api/reports/management?period=yearly&year=` | the year to date (1 Jan .. the latest complete day) or a whole year | prev = ly = the year before, same days |
| `/api/reports/management?period=weekly\|monthly` | the week / month in progress is offered to date (`weeks[0].to_date`, `months[0].mtd`); the default stays the latest finished one (`mtd=1` opens the month in progress) | the headline and the branch league compare to date on the calendar (`headline.mom_basis = 'to_date'`); **§3 pace keeps the weekday-fair baseline** (`pace.mom_basis = 'dow'`) |
| `/api/reports/management/months` | as before | the month in progress `mom.basis = 'to_date'` (was `dow`) |
| `/api/analysis/compare` · `/api/analysis/period_compare` · `/api/sales/menu-compare` | `guard`: either side in progress cuts both to the same days; the anchor is the last closed day | `to_date`, `n_days`, `windows` |
| `/api/analysis/yoy` | the month of the anchor, not finished, is month to date on both sides | no raw % when the year-ago month starts before the data |
| `/api/analysis/health` · `/api/menu/lifecycle` | closed months only | — |
| `/api/sales/top_ordered` · `/api/sales/dish-movers` · `/api/analysis/coupon-series` | the month in progress to date from the day tables, on the scope's last closed day | + `qty_yoy_pct` / `delta_ly_pct` / `yoy_pct` |
| `/api/buffet/child` | follows the top bar (`kind`, `pick`); it was the latest month whatever the filter | cur / prev / ly per branch and chain |
| `/api/home/brief` (`mtd_revenue`), `/api/meta/data-window`, `/api/overall/latest-month-mtd` | end on the last closed day (the day still trading was in them) | — |

### The cost figures (PR-PP3)

`backend/period_cost.py` - ONE engine for COGS on a period (the app-wide COGS expression, `core._unified_cogs_expr`):

```python
cost_floor(con)                        # the first day of the day-level dish table
segments(s, e) / plan(s, e, floor)     # the window cut at month ends: a whole month -> the month table, a part month -> the
                                       # day table; (None, reason) when a part month starts before the day table
cost_sum(con, s, e, branch, group, floor, by_branch)   # -> ({revenue, cogs, gp, gp_pct, food_cost_pct, customers,
                                       #   cost_per_head, gp_per_head, rev_per_head, cost_coverage_pct, src}, reason)
compare(con, w, branch, group, by_branch)              # cur / prev / ly of one periods.windows result + change + reasons
```

Revenue for GP and food-cost % is the same table's ex-VAT dish revenue (GP = revenue − COGS on one base); covers are the bills of
the same days. Baht and per head compare in %, GP % · food-cost % · plate coverage in จุด; COGS: down = good.

`periods.reasons(w)` / `public(w)["reasons"]`: why a base is not compared. A year names the month the data starts
(`ข้อมูลปีก่อนเริ่ม พ.ค. 2568` - live data starts 2025-05, so a year to date has no whole year-ago base until 2027), other kinds the
first day (`ข้อมูลเริ่ม 23 พ.ค. 2568`); `period_cost` adds `ต้นทุนรายวันมีตั้งแต่ …`. `periodQ` / `periodCmpHtml` show it in the "?"
and the chip's tooltip, so "—" is never bare.

| Endpoint | Period | Comparisons |
|---|---|---|
| `/api/period/cost?kind=&pick=&date_from=&date_to=&by_branch=1` (new; cost.analysis / profit.pnl) | `windows` + `period_cost.compare` | cur / prev / ly + `change.prev` / `change.ly` + `reasons`; per branch |
| `/api/sales/cost-per-head?gran=day\|week\|month\|year&date=` | `windows` (was full calendar bounds, the day still trading in the focus) | `prev_*` and new `ly_*` per branch and total; `period`, `reasons`; cost unknown before the day table = None |
| `/api/reports/pnl-bill` (+ `-drill`) | `windows` on the per-bill table's window (was full bounds) | `summary_prev` / `summary_ly` where the rolling per-bill window holds them, else None + reason |
| `/api/pnl/monthly` | the month in progress: `mom_basis = 'to_date'` (GP to date vs the same days of last month, `period`, `gp_ly_pct`); the others `'full'` | was a part month's GP against a whole month's |
| `/api/pnl/branch-month-detail` | the month in progress to date from the day table (summary + rising / falling dishes) | `rev_ly_delta_pct`, `gp_margin_prev_pp` / `_ly_pp`, `period` |
| `/api/pnl/branch` | fixed cost of the month in progress × its closed days ÷ its days (`months_prorated`) | — |
| `/api/bom-cost/branch-foodcost-mom` | the month in progress to date (boxes and movers on the same days), a finished month whole | `ly_food_cost_pct`, `ly_delta_pp`, `mom_basis`, `period` |
| `/api/bom-cost/cost-drivers` | the to-date basis only when the window's last month is the month in progress | — |
| `/api/home/brief` (`food_cost_pct_mtd`, `cost_health`) | the month to date on the dish table (it was the whole month table over the bills' revenue, moved against the whole last month) | `delta_pp` / `ly_delta_pp`, `prev_food_pct` / `ly_food_pct`, `period`, `reasons` |
| `/api/analysis/anomalies` (`cost_now`) | the insight box's cost headline: the month of the tiles, to date | prev / ly in จุด; the bar chart keeps closed months |
| `/api/gp/by-branch-month?granularity=week` | the weekly trend is pro-rated from the month (`estimate: true`, shown `ประมาณการ`) | — |
| `/api/franchise/supply` (`purchases`) | an open month against the first n days of last month (`basis = 'to_date'`, `prev_range`) | was the whole last month |
| bom-center price preview, dish-cost month table | month-grain lists (their own month picker on an `ownFilters` page) | + `ly_price` / `ly_change_pct`, `ly_cost` / `ly_change_pct` (tooltip of the change cell) |

Cost pages (all on the top bar, no page-level period control; the old pickers are gone or hidden and follow it):

| Page | Kinds | Notes |
|---|---|---|
| ต้นทุน/หัว (costhead) | วัน · สัปดาห์ · เดือน · ปี | KPI tiles with chips; table: ต้นทุน/หัว เทียบช่วงก่อน · เทียบปีก่อน |
| P&L ต่อบิล (pnlbill) | วัน · สัปดาห์ · เดือน | per-bill COGS is a rolling ~31 days |
| ต้นทุน (cost) | เดือน · ปี | branch cost / head on the bar's period; the food-cost strip from /api/period/cost |
| GP รายสาขา (gpbranch) | สัปดาห์ · เดือน · ปี · กำหนดเอง | summary + branch boxes from /api/period/cost; a week's rows `ประมาณการ` |
| ต้นทุน BoM (bomcost) | สัปดาห์ · เดือน · ปี · กำหนดเอง | KPI chips; branch boxes on the same days |
| P&L (pnl), P&L รายสาขา (pnlbranch) | เดือน · ปี · กำหนดเอง | tiles = the bar's period with chips; the month select is hidden |
| Top 60 ต้นทุน/หัว (costheadtop60) | เดือน | month-grain report; no period comparison (branch vs chain) |
| ต้นทุนครบ (coverage) | เดือน · ปี | plate coverage with chips (จุด) |
| ประสิทธิภาพต้นทุนเมนู (menuefficiency) | เดือน · ปี | month-grain: the total cost vs the month before / a year back over closed months; the month in progress "—" |
| ยอดซื้อวัตถุดิบ (ingpurchase) | สัปดาห์ · เดือน · ปี · กำหนดเอง | weekly import; chips on purchases, sales, % (จุด) |
| วิเคราะห์ BoM รายสาขา (branchbom) | สัปดาห์ · เดือน · ปี · กำหนดเอง | the requisition block reads closed months only |
| home food-cost card, overall GP / COGS tiles, the report's cost card, the insight box cost block | the bar's period / the headline month | moved here from PP2 |

### Frontend — `js/01-core.js`

```js
periodRange(kind, anchorIso, {date_from, date_to, today})   // = periods.resolve; also .from/.to/.label (PR-SP shape)
periodPct(cur, base), periodPp(cur, base)                   // = periods.pct / pp
periodLabelTh(fromIso, toIso)
periodBar(el, {kinds, value, onChange, range, key})         // the chip track วัน · สัปดาห์ · เดือน · ปี · กำหนดเอง
periodCmpHtml(cur, prev, ly, {unit: '%'|'pp', upGood, range})  // the two comparison chips + "?"
getPeriodKind()                                             // the top bar's kind (week | month | year | range)
gPeriodGet(), gPeriodSet(v, silent), gPeriodResolve()       // the top bar's period bar (see above)
```

- `periodBar`: 44 px targets and a scrolling track on a phone; remembers the choice per page (`localStorage
  oshinei_period_<key>`, try/catch); returns `{get(), set(value, silent), kinds}`. A month-only figure passes
  `kinds: ['month', 'year']` — the week chip is not drawn.
- `periodCmpHtml`: good / bad ink by direction and by whether up is good (`upGood: false` for a cost).
- PR-PP2 (owner 2026-10-24: "ถ้ามีสองอันแบบนี้จะซ้ำซ้อนไหมครับ"): **the top bar is the ONLY period control of a page.** A page
  declares the kinds it can show in `TABS[tab].follow.period = { kinds: [...] }` (default สัปดาห์ · เดือน · ปี · กำหนดเอง).
  The top bar draws only those chips: **วัน** appears on a page that draws one day (ยอดขายรายวัน), a month-grain page
  (cashctl · custtype · pricezone · cancelbill) shows **เดือน** only, opsreport สัปดาห์ · เดือน · กำหนดเอง, the
  month-range pages (guest · buffetperf · timeflow · staff · engineering) เดือน · ปี. Opening a page whose kinds exclude the
  bar's kind moves the bar to the nearest kind it has (`gPeriodForTab`, in `switchTab`; the period that holds the end of
  the one on screen). The stepper ‹ › moves the day / week / month / year. The page-own pickers are gone or hidden and
  follow the bar (ยอดขายรายวัน's date and chip track, guest's มุมมอง month, the month selects of the month pages).
  The resolved range shows ONCE, as the chip beside the page title (`#gpRangeChip`, `periodChip(r, fromServer)`): the bar's
  own range, replaced by the server's `period` when the page loads. รายงานผู้บริหาร has no top-bar period (its cover
  toggle is its control). The string form `follow: { period: '<bar key>' }` (`_syncExtraPeriod`) is kept for a future
  page-own bar but no page uses it.

PR-PP2 helpers (`js/01-core.js`, after `periodCmpHtml`; PR-PP3: `periodQ` / `periodCmpHtml` show `range.reasons`):

```js
periodOf(d)                     // the server's `period` object (or the older cur_range / prev_range / yoy_range trio)
periodSpan(r)                   // '1–19 ต.ค. 2569 · ถึงวันที่ปิดยอดล่าสุด'
periodQ(r, unit)                // the "?" alone (beside a table heading)
periodKpi({l, v, c, cur, prev, ly, unit, upGood, range, q, sub})   // a KPI tile with its two chips
periodDelta(cur, base, opt) / periodPctDelta(pct, opt)             // one compact change for a table cell
periodHeadline(hostId, page)                                       // /api/period/headline on the top bar's period: tiles
gPeriodQuery(extra)             // the top bar's period as kind + pick (or date_from / date_to) + the branch scope
gPeriodPick()                   // {kind, pick (the end of the period on screen), from, to}
gPeriodForTab(tab), gPeriodKindsOf(tab), periodChip(r, fromServer)  // the kinds of a page; the range chip by the title
```

Pages: ยอดขายรายวัน, the overall hero, KPI cards and best / needs-attention branch, เทียบสาขา, บุฟเฟ่ต์เด็ก and the
headline pages all read the top bar (one control per page); รายงานผู้บริหาร keeps its own cover toggle (รายวัน ·
รายสัปดาห์ · รายเดือน · **รายปี**) and pickers, and the top bar's period is hidden there. The menu-spike card keeps its own period
chips (PR-SP; its "vs normal" baseline is not a period comparison). ความต้องการพิเศษ (`pos_modifier`) has no date
column: all-time counts, no period.

The block between `// <period-core>` and `// </period-core>` is pure; `backend/test_periods.py` runs it in node on the
same date table as `periods.resolve` (parity).
