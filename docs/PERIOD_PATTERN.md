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
- The top bar already has วัน · สัปดาห์ · เดือน · ไตรมาส · ปี beside the month filter (`setGranularity`); that toggle is the
  global period kind (`getPeriodKind()`, a quarter reads as month) instead of a second chip row.
- The weekday-fair MoM (`mom_basis='dow'`) stays for the management report §3 pace and the tests that pin it; the headline %
  moves to the calendar to-date rule page by page in PP2/PP3.

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

### Frontend — `js/01-core.js`

```js
periodRange(kind, anchorIso, {date_from, date_to, today})   // = periods.resolve; also .from/.to/.label (PR-SP shape)
periodPct(cur, base), periodPp(cur, base)                   // = periods.pct / pp
periodLabelTh(fromIso, toIso)
periodBar(el, {kinds, value, onChange, range, key})         // the chip track วัน · สัปดาห์ · เดือน · ปี · กำหนดเอง
periodCmpHtml(cur, prev, ly, {unit: '%'|'pp', upGood, range})  // the two comparison chips + "?"
getPeriodKind()                                             // the top bar's kind
```

- `periodBar`: 44 px targets and a scrolling track on a phone; remembers the choice per page (`localStorage
  oshinei_period_<key>`, try/catch); returns `{get(), set(value, silent), kinds}`. A month-only figure passes
  `kinds: ['month', 'year']` — the week chip is not drawn.
- `periodCmpHtml`: good / bad ink by direction and by whether up is good (`upGood: false` for a cost).
- A page that follows the top bar declares `follow: { period: '<bar key>' }` in `TABS` (`_syncExtraPeriod`).

The block between `// <period-core>` and `// </period-core>` is pure; `backend/test_periods.py` runs it in node on the
same date table as `periods.resolve` (parity).
