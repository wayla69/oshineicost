# Handoff Spec: กระทบยอด Recon — "Inbox" UI (Phases 0–4)

As-built developer handoff for the reconciliation app overhaul. Source of truth is
`recon/frontend/index.html` (single-file vanilla HTML/JS, no framework/build step) +
`recon/reconcile_v2.py` (FastAPI payload). This doc is descriptive of what shipped
(commits `bcf14b1`, `fd097dc`), plus accessibility recommendations where the current
build has gaps.

> Stack: vanilla ES (no bundler), server-rendered JSON → client `innerHTML`. Thai-first
> (`<html lang="th">`), light mode only. Fonts: Inter + Noto Sans Thai (400/500/600/700).
> Money is always `Math.round()` + `toLocaleString('th-TH')` via `fmt()`.

---

## Overview

The month is an **inbox**, not a spreadsheet. Two screens:
1. **หน้าแรก (overview)** — chain-wide branch list; tap a branch to enter its inbox.
2. **หน้าสาขา (branch)** — a prioritized stack of plain-Thai cards (one per real problem),
   with reconciled/timing channels folded into drawers. Work to zero → ส่งตรวจ → ปิดเดือน.

The legacy variance grid, 31-day calendar, and manual matcher live on in `classic.html`
(reachable via "⋯ เพิ่มเติม → เครื่องมือขั้นสูง") as the rollback path — hidden, not deleted.

---

## Design Tokens (actual, from `:root`)

There is **no formal spacing scale** — spacing is inline px (common steps: 6/8/10/12/14/18).
Color + type are tokenized:

| Token | Value | Usage |
|-------|-------|-------|
| `--brand` | `#0F6E56` | primary buttons (`.btn-pri`), logo, links |
| `--brand-deep` | `#0a523f` | primary button hover |
| `--ok` / `--ok-bg` | `#047857` / `#ecfdf5` | success chip, green drawer, reclass banner |
| `--warn` / `--warn-bg` | `#b45309` / `#fffbeb` | waiting chip, problem card (setup/waiting), review-bar |
| `--err` / `--err-bg` | `#b91c1c` / `#fef2f2` | diff chip, problem card (diff), negative variance |
| `--info` / `--info-bg` | `#1d4ed8` / `#eff6ff` | setup chip, timing drawer bg |
| `--bg` | `#f0f4f8` | page background |
| `--card` | `#fff` | card surface |
| `--line` / `--line-soft` | `#e2e8f0` / `#f1f5f9` | borders / hairline + coin-card bg |
| `--txt` / `--sub` / `--mut` | `#0f172a` / `#475569` / `#94a3b8` | body / secondary / muted |
| `--shadow` | `0 1px 3px /.04 + 0 2px 10px /.05` | card elevation |

Type: body `14.5px/1.55`; `h1 19/700`, `h2 17/600`; `.lbl 11.5px/500 --mut`; `.muted 13px --sub`.
Money uses `.mono`/`.num` → `font-variant-numeric: tabular-nums`.

Behavioral constants (JS): `COIN_TOL = 100` (฿ monthly coin-residue threshold), daily tolerance
±฿50 (engine). `progCls(p)`: `p≥95` green / `70–94` amber (`.p-warn`) / `<70` red (`.p-err`).

---

## Components

| Component | Class / fn | Variants | Notes |
|-----------|-----------|----------|-------|
| Card | `.card` | `coin-card`, `ch-prob`, `drawer`, `ov-hero`, `hero-rc` | radius 12, 1px `--line`, `--shadow` |
| Status chip | `.chip` | `c-ok` `c-warn` `c-err` `c-mut` `c-setup` | radius 999, 12px/600, soft bg + bold text |
| Button | `.btn` | `.btn-pri`, `.btn-sm` | radius 9; pri = `--brand` fill |
| Reclass hero | `reclassHero(s,i)` | confidence `high` \| `med` | top of branch view; see States |
| Problem card | `chProblemCard(ch,i,closed)` | status `diff`/`setup`/`waiting_doc(missing)` | bg+left-border by status; header row → drill |
| Coin rollup | coin block + `acceptCoinAll(idxs)` | open only (read-only when closed) | one card for all ≤฿100 channels |
| Drawer | `.drawer` + `toggleDrawer(name)` | `wait` (timing, `--info-bg`) / `green` (`--ok-bg`) | collapsed by default; rows = `chDrawerRow` |
| Channel drill | `toggleDays(i)` → `#days-i` | per-channel | emitted in EVERY bucket so drill works anywhere |
| Day evidence | `openDay(i,day)` | — | POS bills vs bank lines; "น่าจะคู่กัน" hint chip |
| Branch row | `.ov-row` | default / `ov-row-done` | overview list item |
| Overview hero | `.ov-hero` | has-todo / all-clear / empty | chain count + progress |
| Manager bar | `.review-bar` | shown when `in_review` branches exist | links into each branch |
| Reclass banner | `.ov-reclass` | shown when `reclass_count_total>0` | chain-wide wrong-channel total |
| Bottom sheet | `showSheet(html)` / `closeSheet()` | accept-reason / more-menu | `.sheet-overlay` + `.sheet-box` |

---

## States & Interactions

| Element | State | Behavior |
|---------|-------|----------|
| `.btn` | hover | bg → `--line-soft`, border `#cbd5e1`, `.15s` |
| `.btn-pri` | hover | bg → `--brand-deep` |
| `.btn[disabled]` | disabled | opacity .45, `not-allowed`; ปิดเดือน disabled while `todo>0` |
| `.chip` (button) | hover | `filter: brightness(.95)` |
| Reclass hero | `high` | one tap "✅ ใช่ เป็นเงินก้อนเดียวกัน" → `applyReclass(i)` → POST `/api/manual` → re-render |
| Reclass hero | `med` | "✅ ใช่" expands inline confirm `#rc-med-i` (preselected reason chip) → "ยืนยันย้าย" → `applyReclass(i)` |
| Reclass hero | any | "🔍 ขอดูรายการ" → `drillReclass` → `openEvidence(pos_cat)`; "ไม่ใช่" → hides card client-side |
| Problem card | default | header row + "🔍 ดูรายการ ▾" both call `toggleDays(i)` (expand inline `#days-i`) |
| Problem card | accepted | shows `"<reason>"` + "เลิกรับทราบ" (`unaccept`) unless month closed |
| Coin rollup | open | "✓ รับทราบทั้งหมด" → `confirm()` → loop POST `/api/lanes/adj` (`adj_type:'accept'`, auto-reason ≥10 chars) |
| Coin rollup | — | each channel name (`.coin-name`, dotted underline) → `toggleDays(i)` to verify before accepting |
| Drawer header | toggle | `toggleDrawer(name)` flips `#dw-name` display + swaps ▾/▴ caret |
| `openEvidence(key)` | drill | **expands the parent drawer first** if collapsed, then `toggleDays`, then `scrollIntoView` |
| Day evidence | hint | unmatched bank line within ฿1 of a POS-code amount → advisory "น่าจะคู่กัน" chip (no auto-tick) |
| Accept (todo) | trigger | `acceptTodo` opens bottom sheet of canned reasons; ≥10-char reason enforced client + server (AUD-8) |
| Overview row | click | `location.hash = 'b/<id>'` |
| Manager bar link | click | `location.hash = 'b/<id>'` (jump to that branch) |

All re-renders go through `route()` → full `innerHTML` rebuild. **No UI state persists across a
save** — drawers/menus default collapsed and derive from data each render (intentional, matches the
existing toggle behavior; do not add cross-save persistence).

---

## Responsive Behavior

Single breakpoint: **`@media (max-width: 640px)`**. No tablet tier.

| Breakpoint | Changes |
|------------|---------|
| Desktop (>640px) | container `max-width 1020px`, centered; `.grid-ch` 5-col `1.45fr 1fr 1fr 1.35fr 20px`; `.daygrid` `minmax(92px,1fr)`; `.ov-row` progress block fixed 150px |
| Mobile (≤640px) | `.grid-ch` → 2-col, caret column hidden; `.daygrid` → `minmax(68px,1fr)`; `.wrap` padding 10px; table cells 7px/12.5px; `.ov-row` padding 11/12, progress `min-width 54px`, `.row width:auto`; `.ov-hero-line` 14.5px |

Bottom sheet (`.sheet-box`) is `width:100%; max-width:520px`, bottom-anchored — already mobile-first.

---

## Edge Cases

- **Empty DB / no branches**: overview hero shows month only (no ✓/100% bar); list shows "ยังไม่มีข้อมูลสาขาสำหรับเดือนนี้".
- **Reclass-only branch** (`todo_count==0, reclass_count>0`): stays in the OPEN list reading "เหลือ 1 เรื่อง: ยอดอยู่ผิดช่อง" (NOT hidden in "เรียบร้อยแล้ว"); the chain hero count uses todos only (reclass is advisory, doesn't block close).
- **`in_review` branch with no open items**: excluded from the "เรียบร้อยแล้ว" drawer; surfaces only in the manager `.review-bar`.
- **COUPON channel**: variance is loyalty **points**, not baht — excluded from the coin bucket, from the problem-card "เงินเข้าจริงน้อยกว่า POS ฿X" wording (shows its own detail), and from drawer gross figures (shows "แต้ม"/—).
- **Within-daily-tolerance monthly drift**: a channel matched every day but off >฿100 for the month escalates to `diff` (raises a todo, blocks close) — backend `reconcile_month`.
- **Partial document**: chip shows "ถึงวันที่ N — ขาด N+1–<horizon>", where horizon is capped to today for an in-progress month (never asks to upload future days).
- **Missing bank statement** (channel=None doc): routes its QR/TRANSFER/CASH channel to an actionable problem card, not the never-red timing drawer.
- **Closed (approved) month**: 🔒 banner; everything read-only — no coin button, no upload, no accept, no unaccept link, no nudge checkbox.
- **Long Thai branch/reason text**: flex rows `flex-wrap:wrap`; reasons truncate via `title` tooltip on the `.lbl`. No hard char limit enforced client-side (server caps where relevant).
- **Loading**: each loader writes `header() + '<div class="muted">กำลังโหลด…</div>'`; errors render a `.c-err` card with the message. No skeletons.

---

## Animation / Motion

| Element | Trigger | Animation | Duration | Easing |
|---------|---------|-----------|----------|--------|
| `.btn` / `.btn-pri` | hover | bg + border color | 150ms | default |
| `.grid-ch`, `.daycell`, `.ov-row` | hover | bg tint | 120ms | default |
| `.prog > div` | data change | width fill | 300ms | default |
| `.toast` | show/hide | opacity | 300ms | default |
| `.chip` (button) | hover | brightness filter | 150ms | default |
| Drawers / sheets / inline-confirms | toggle | **none** — instant `display` swap | — | — |

No transform/slide animations on drawers or the bottom sheet (they appear/disappear via
`display`). Acceptable for the staff workflow; a slide-up on `.sheet-box` would be a nice-to-have.

---

## Accessibility Notes (current state + recommendations)

Current build is functional but minimal — recommendations for a follow-up a11y pass:

- **Lang**: `<html lang="th">` set ✓. Money uses `aria`-free spans — fine (visible text).
- **Custom toggles**: drawer headers are `<a href="#" onclick>`; problem-card headers are
  `onclick` divs. **Recommend**: `role="button"`, `aria-expanded`, and `Enter/Space` key handlers;
  pair each header with `aria-controls="dw-<name>"` / `aria-controls="days-<i>"`.
- **Bottom sheet** (`showSheet`): closes on overlay click only. **Recommend**: trap focus, move
  focus to the sheet on open, restore on close, close on `Escape`, add `role="dialog"` +
  `aria-modal="true"` + an `aria-label` from the `.sheet-h`.
- **Focus order**: DOM order is logical (hero → docs → problems → drawers → todo → actions). The
  reclass hero "ไม่ใช่" is an `<a>` — give it `role="button"`.
- **Color**: status is encoded by chip text (Thai words) **and** color — not color-only, good for
  color-blind users. Verify chip text vs soft bg meets WCAG AA (the 800/600-ish pairs generally do).
- **Icons/emoji** (⏳ ✓ 🔀 🪙 🔍): decorative; ensure the adjacent Thai text carries the meaning
  (it does). For screen readers, consider `aria-hidden` on the emoji spans.
- **Touch targets**: chips/`.btn-sm` are ~24–28px tall — below the 44px guideline on mobile;
  consider bumping the tap area for the primary actions (✅ ใช่, รับทราบทั้งหมด, ปิดเดือน).
- **Live regions**: `toast()` is not an `aria-live` region — wrap `#toast` in `aria-live="polite"`.

---

## API Contracts (for reference)

- `GET /api/v2/overview?month=YYYY-MM` → `{ period_month, channel_order[], channel_labels{},
  reclass_count_total, reclass_amount_total, branches[] }`; each branch:
  `{ branch_id, branch_name, progress_pct, todo_count, status, pos_total, ext_total,
  non_revenue, reclass_count, reclass_amount, channels{} }`.
- `GET /api/v2/reconcile?branch_id&month` → `reconcile_month()` payload: `channels[]`
  (`key,label,pos,ext,variance,status∈{ok,diff,waiting_doc,no_source,setup},detail,days[],
  days_done,days_total,banks[],accepted,…`), `documents[]`, `reclass_suggestions[]`
  (`amount,confidence,pos_day,pos_cat_th,ext_cat_th,ext_bank_th,…`), `todo[]`, `progress_pct`,
  `can_close`, `session`.
- Mutations: `POST /api/manual` (reclass apply), `POST /api/lanes/adj` (`adj_type:'accept'`),
  `PATCH /api/bank-lines/{id}/ignore` (internal-transfer exclude). All write endpoints are
  token-gated (`X-Upload-Token`) on the server; the client attaches it from `localStorage`.
