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
  The default kind is the constant `PERIOD_DEFAULT_KIND = 'year'` (js/01-core.js): year to date, 1 Jan … the anchor, so every page
  that still reads the month fields keeps showing Jan … latest as before. PP3 switches it to `'month'`. A device that chose a kind keeps it.
- Phone: one sideways-scrolling row inside the folded tools, the period (stepper) first, 44 px targets.
- API: `gPeriodGet()`, `gPeriodSet({kind, pick, from, to}, silent)`, `gPeriodResolve()` (a `periodRange` result),
  `gPeriodAnchor()`, `getPeriodKind()`; remembered on the device (`oshinei_gperiod`, `oshinei_chartwin`).

## Grain per figure (what a page may offer)

| Source | Grains | Notes |
|---|---|---|
| Sales: `sales_transaction`, `sales_sku_daily`, `sale_payment`, daily `pos_*` | day · week · month · year · range | `sales_sku_daily` starts 2026-03-22 |
| Sales: `sales_month_summary`, `pos_*_month` (customer type, payment, channel, void, coupon, cancel) | month · year | the week chip is hidden, not disabled |
| Cost: price list (`bom_center/prices.py`), dish-cost list (`bom_center/dish_cost.py`), requisitions, month-end counts / variance | month · year | |
| Cost: day-level dish cost (`sales_sku_daily` cost) | day · week from 2026-03-22 | before that, and cost YoY on the week grain, show "—" with the reason in "?" (sync keeps 120 days: day/week cost YoY is not possible before ~2027-03) |
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

Cost figures (the overall GP / COGS tiles, the home food-cost card, the report's cost card) keep their basis until PR-PP3.

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

PR-PP2 helpers (`js/01-core.js`, after `periodCmpHtml`):

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
