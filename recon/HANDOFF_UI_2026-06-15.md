# Handoff Spec: Recon UI — Wrong-channel report, Statement-code dictionary, Pattern guard

Generated 2026-06-15 from the implemented code (no Figma — this is a vanilla-HTML app,
so the spec references the real CSS-variable tokens and JSON contracts, not mockups).

## Overview

Three surfaces shipped this session, all inside the **revenue-reconciliation (`recon/`)
app** served by FastAPI at `oshinei-dashboard.fly.dev:8443/app/`:

1. **Wrong-channel report** (`#wrong`) — a log + signable sheet + self-improvement
   coaching report of confirmed mis-entries (money rung in the wrong POS channel).
2. **Statement-code dictionary + editor** (setup ⑤ + printable review sheet) — lets a
   manager remap a bank-statement abbreviation → channel without a code change.
3. **Reclass-pattern guard** — a non-blocking manager banner on the overview that warns
   when wrong-channel *suggestions* look systematic (a mis-mapped code, not human error).

User context: branch managers + HQ accounting. Thai-first UI. Used monthly during the
reconciliation close. Money figures must be scannable (right-aligned, tabular numerals).

### Tech stack (important for implementers)
- **No framework, no build.** Single `frontend/index.html` (SPA via `location.hash`
  router) + `frontend/setup.html`. Plain `<script>`, template literals, `fetch`.
- **FastAPI** backend; **SQLite**. Frontend is served same-origin by StaticFiles.
- **Design tokens = CSS custom properties** in `:root` (mirrors `docs/ui-kit/tokens.css`).
- Fonts: **Inter + Noto Sans Thai** (app, via Google Fonts, print-media deferred load);
  **Sarabun** for the print/PDF endpoints.
- "Component props" in this app = **JSON response fields** from the API. Each surface's
  contract is documented below so a rewrite (e.g. to React) has the exact data shape.

---

## Design Tokens Used

Defined in `recon/frontend/index.html` `:root` (setup.html + dashboard share them).

| Token | Value | Usage |
|-------|-------|-------|
| `--brand` | `#0F6E56` | Primary buttons (`.btn-pri`), links, progress fill base |
| `--brand-deep` | `#0a523f` | Primary button hover |
| `--brand-soft` | `#e6f4ef` | Focus ring, active filter chip bg |
| `--ok` / `--ok-bg` | `#047857` / `#ecfdf5` | "ดีขึ้น", success chips, reclass banner |
| `--warn` / `--warn-bg` | `#b45309` / `#fffbeb` | Mis-entry amounts, "รอตรวจ", attention bars, hero-rc left border |
| `--err` / `--err-bg` | `#b91c1c` / `#fef2f2` | Errors, load-fail card |
| `--info` / `--info-bg` | `#1d4ed8` / `#eff6ff` | Setup/mapping cards (`.c-setup`), target box |
| `--bg` | `#f0f4f8` | Page background |
| `--card` | `#fff` | Card surface |
| `--line` / `--line-soft` | `#e2e8f0` / `#f1f5f9` | Borders / row dividers, muted chip bg |
| `--txt` / `--sub` / `--mut` | `#0f172a` / `#475569` / `#94a3b8` | Body / secondary / tertiary text |
| `--shadow` | `0 1px 3px rgba(15,23,42,.04), 0 2px 10px rgba(15,23,42,.05)` | Card elevation |
| font (app) | `'Inter','Noto Sans Thai',system-ui` · base `14.5px` / `1.55` | Body |
| `h1` / `h2` | `19px/700` / `17px/600`, `letter-spacing:-.01em` | Titles |
| `.lbl` | `11.5px / 500`, `--mut` | Micro-labels |
| money | `font-variant-numeric: tabular-nums` (`.mono`/`.num`) | All amounts — keeps digits aligned |

**Print/PDF endpoints** use a separate inline token set (no shared vars): font
**Sarabun**; accent **`#ea580c`** (orange) for `h2` left-border + arrows; trend bars
`#fdba74` (past) / `#ea580c` (current month); review-sheet review rows `#fffbeb`.

### Shared components (CSS classes, real values)

| Class | Spec |
|-------|------|
| `.card` | `#fff`, `1px var(--line)`, `radius 12px`, `padding 16px 18px`, `--shadow` |
| `.btn` | `1px var(--line)`, `radius 9px`, `padding 8px 15px`, `13.5px/500`; hover `bg --line-soft`, `transition .15s` |
| `.btn-pri` | `bg --brand`, `#fff`; hover `--brand-deep` |
| `.btn-sm` | `12px`, `padding 4px 10px` |
| `.btn[disabled]` | `opacity .45`, `cursor not-allowed` |
| `.chip` | inline-flex, `12px/600`, `padding 3px 10px`, `radius 999px`, `white-space:nowrap`; variants `.c-ok/.c-warn/.c-err/.c-mut/.c-setup` (soft bg + dark text). `.ov-mini` = `11px`, `padding 1px 8px` |
| `table` | full-width, collapsed; `td/th padding 9px 12px`, `13.5px`; **`th` `11.5px/600` `--mut`**; **all cells `text-align:right` except first child = left** (money-table convention) |
| `.toast` | fixed bottom-center, `#0f172a`/`#fff`, fade `opacity .3s`, auto-dismiss |
| `.prog` | `height 9px`, track `--line`, fill `#10b981` (`.p-warn`→`#f59e0b`, `.p-err`→`#ef4444`), `width transition .3s` |

---

## Surface 1 — Wrong-channel report (`#wrong`)

`loadWrongChannel()` in `index.html`. Route: `location.hash === 'wrong'`. Entered from the
⋯ "เพิ่มเติม" bottom-sheet → "⇄ รายงานเงินบันทึกผิดช่อง".

### API contract — `GET /api/reports/wrong-channel?period_month=YYYY-MM`
```jsonc
{
  "period_month": "2026-05",
  "total_count": 1, "total_amount": 200.0,
  "summary": [{
    "branch_id": 4, "branch_name": "อุบลราชธานี",
    "count": 2, "months": 1, "total": 122520.0,
    "direction": "down|up|flat",          // vs previous month
    "target": 1,                           // next-month goal (≈ current//2)
    "trend": [{"month":"2026-03","count":5,"amount":260000}, ...up to 6],
    "patterns": [{"from_th":"เงินสด","to_th":"QR พร้อมเพย์","count":1,"amount":92520}],
    "tips": ["ลูกค้าสแกนจ่าย QR แต่…", ...]   // one per pattern + closing habit
  }],
  "items": [{ "branch_name","period_month","days":["2026-05-23"],
              "from_th","to_th","to_bank_th","amount","reason","by","at" }]
}
```
Other endpoints: `.xlsx` (2-sheet download), `.print[&branch_id=]` (A4 sign-off PDF).

### Layout (screen)
Single column, `max-width 1020px` (`.wrap`). Top→bottom:
1. **Header** (shared) + title row with `.btn-pri` "🖨️ ใบเซ็นรับทราบ" + `.btn-sm` "⬇ Excel".
2. **Plain-Thai explainer** (`.muted`).
3. **KPI card** — flex row, 3 stats (จำนวนรายการ / มูลค่ารวม / สาขา), each `20px/700`.
4. **Summary table** — columns: สาขา · จำนวนครั้ง · ยอดรวม (`--warn`, 600) · **แนวโน้ม (direction chip)** · ใบเซ็นรับทราบ (print-per-branch link).
5. **"แนวทางพัฒนารายสาขา"** — one `.card` per branch: name + direction chip + target (`.lbl`, right), confusion-pattern chips (`.c-warn.ov-mini`), tips `<ul>`.
6. **Full items table** — สาขา · วันที่ · บันทึกผิดเป็น · ที่จริงเข้า (`→` in `--warn`) · ยอด · เหตุผล · โดย.

### Print sheet (`/api/reports/wrong-channel.print`)
A4, one `page-break-after:always` `.sheet` **per branch**. Order: title + sub → items
table (tfoot รวม) → **① trend** (CSS bar chart, current month bar `#ea580c`, others
`#fdba74`; height = `8 + 52*count/max` px) + **🎯 target box** (`#eff6ff`) → **② confusion
patterns table** → **③ tips** (`<ul class=tips>`, `💡` ::before) → **รับทราบ block**
(`#fff7ed`) → **3 signature lines** (manager / cashier / accounting).

### States & Interactions
| Element | State | Behavior |
|---------|-------|----------|
| View | Loading | Replaces `#app` with `<div class=muted>กำลังโหลด…</div>` |
| View | Error | `.card.c-err` "โหลดไม่สำเร็จ: {msg}" |
| View | Empty (`items.length===0`) | `.card` explaining the report fills when staff confirm "ใช่ เป็นเงินก้อนเดียวกัน" |
| Direction chip | `down` | `.c-ok` "✓ ดีขึ้น" |
| Direction chip | `up` | `.c-warn` "▲ ผิดมากขึ้น" |
| Direction chip | `flat` | `.c-mut` "— ทรงตัว" |
| Print link | click | Opens print endpoint in new tab (`target=_blank`); page has a `🖨️ พิมพ์/บันทึก PDF` button calling `window.print()` |
| Excel link | click | Browser download (Content-Disposition attachment) |

### Edge cases
- **Empty**: dedicated empty card (above). Print endpoint renders a centered `.empty` note.
- **Trend < 2 months**: `direction = "flat"` (no false ดีขึ้น/แย่ลง).
- **Long branch/reason text**: tables wrap (`vertical-align:top` in print); no truncation
  (reasons are short canned strings). Thai has no word spaces — rely on natural wrapping.
- **Genuine vs false entries**: report is built only from *confirmed* manual matches with a
  *different* real channel (`to_cat != from_cat`); SDCH-type false positives are excluded
  upstream (see [[sdch-statement-codes]]).

---

## Surface 2 — Statement-code dictionary + editor

`setup.html` **section ⑤** ("รหัสในสเตทเมนต์ → ช่องทาง") + printable review sheet
`/api/statement-codes.html`. The dictionary is the single source the parser uses
(`parsers/bank.py STATEMENT_CODE_MAP`); overrides live in `recon_statement_code_map`.

### API contract
```jsonc
GET /api/statement-codes  →
{ "codes":[{ "code":"SDCH","kind":"code|keyword","bank":"KTB","meaning_th":"…",
             "status":"confirmed|review",
             "channel":"QR", "channel_th":"QR พร้อมเพย์",          // base
             "effective_channel":"CASH_DEP","effective_channel_th":"…", // after override
             "overridden":false, "override_code":null }],
  "review_count":4, "override_count":0,
  "channel_options":["CASH_DEP","CC_SETTLE","FEE","GRAB_PAYOUT","LINEMAN_PAYOUT","OTHER","QR","TRANSFER"] }

POST   /api/statement-codes/override   {code,channel,note,by}  (X-Manager-Token)
       → {code, channel, lines_reclassified}   // re-classifies stored bank lines now
DELETE /api/statement-codes/override/{code}    (X-Manager-Token)
       → {code, removed:true, lines_reclassified}   // reverts to parser base
```
`OTHER` channel = "mark non-revenue" (excluded from reconciliation buckets).

### Layout (setup ⑤)
A `.card` with title + "🖨️ พิมพ์ใบให้ทีมตรวจ" link + explainer, then an async-filled
`#codes-body` table: **รหัส/คำ (+status chip)** · ความหมาย · ระบบจัดเป็น (effective, shows
"(เดิม X)" when overridden) · **แก้เป็น (`<select>` of `channel_options`, Thai labels)** ·
actions (`.btn-pri.btn-sm` บันทึก + `.btn-sm` คืนค่าเดิม when overridden).

### Review sheet (`/api/statement-codes.html`)
**A4 landscape**, Sarabun. Lead note highlights the SDCH example. Table: รหัส (`<code>`) ·
ชนิด (badge code/keyword) · ธนาคาร · ความหมาย · ระบบจัดเป็นช่องทาง · สถานะ · **ทีมยืนยัน
(▢ ใช่ ▢ ไม่ใช่)** · **ที่ถูกต้องคือ (write-in line)**. `review` rows tinted `#fffbeb`;
overridden rows show "✏️ ทีมแก้แล้ว" (`#dbeafe`). Footer: ผู้ตรวจสอบ / วันที่ sign lines.

### States & Interactions
| Element | State | Behavior |
|---------|-------|----------|
| Status chip | `overridden` | `.c-ok` "✏️ ทีมแก้แล้ว" |
| Status chip | `review` | `.c-warn` "⚠ รอตรวจ" |
| Status chip | `confirmed` | `.c-mut` "✓" |
| Save | click | `mgrApi` POST; if 403 → prompt for manager code, retry once; toast "บันทึก {code} → {ch} (แก้ N บรรทัด)"; reload table |
| Revert | click | `mgrApi` DELETE; toast + reload |
| `<select>` | change | No autosave — explicit Save (matches the lane-config pattern in setup) |

### Edge cases
- **No manager token set (local)**: endpoints open → no prompt. On prod
  (`OSHINEI_MANAGER_TOKEN` set), first save prompts and caches in `sessionStorage('mgr_code')`.
- **Override a keyword row**: token = first whitespace/`/`/`|`-split segment of the display
  code. Bank-code review rows (SDCH/IORSDT/NBSDT/SDCK) yield clean single tokens; warn
  implementers not to override broad keyword rows (e.g. "QR / PromptPay").
- **No bank lines yet**: `lines_reclassified: 0` — override still saved, applies to future uploads.
- **Reclassify cost**: bounded by rows whose description `LIKE %code%`. Fine at branch scale.

---

## Surface 3 — Reclass-pattern guard (overview banner)

`checkReclassPattern()` fires after `loadOverview()` renders (non-blocking; failure is
silent so it never breaks the overview).

### API contract — `GET /api/diagnostics/reclass-pattern?month=YYYY-MM&min_cluster=6`
```jsonc
{ "month":"2026-05","min_cluster":6,
  "clusters":[{from_cat,from_th,to_cat,to_th,day_gap,same_bank,count,amount,branches,suspicious}],
  "flagged":[…subset where suspicious===true…],
  "warning": "พบ N คู่รูปแบบเดียวกัน (… → …, ห่าง K วัน, คนละธนาคาร) ใน B สาขา — …" | null }
```
`suspicious = count >= min_cluster && !same_bank && branches >= 2` (the SDCH fingerprint).

### Layout / behavior
When `warning` is non-null, prepend a `.card.c-warn` (left-border `--warn`) **just under
the header**: 🔎 icon + bold "รูปแบบน่าสงสัย — อาจไม่ใช่คนกดผิด" + warning text (`.lbl`) +
link "→ เปิดพจนานุกรมรหัสในสเตทเมนต์" (`/api/statement-codes.html`, new tab).

### States
| State | Behavior |
|-------|----------|
| No suspicious cluster (`warning===null`) | Render nothing (no false alarm) |
| Fetch error | Silent return — overview unaffected |
| Suspicious cluster found | Insert banner via `app.insertBefore` after the header row |

---

## Responsive Behavior

| Breakpoint | Changes |
|------------|---------|
| Default (≤1020px wrap) | Single column, `.wrap` max-width 1020px, padding `20px 18px 48px` |
| **Mobile (≤640px)** | `.wrap` padding → `10px 10px 40px`; `td/th` padding → `7px 7px`, `12.5px`; overview rows tighten; channel grid → 2-col. Wide tables (items/codes) scroll horizontally inside their card (`.scroll`/`overflow-x:auto`). |
| Print (`@media print`) | App: n/a (reports are separate endpoints). Report/review pages: hide `.toolbar`, drop card shadows, `page-break-after:always` per `.sheet`; review sheet is A4 **landscape**. |

Mobile is the primary field context (managers on phones) — coaching cards and the summary
table are designed to stack/scroll rather than truncate.

## Animation / Motion

| Element | Trigger | Animation | Duration | Easing |
|---------|---------|-----------|----------|--------|
| `.btn` / `.chip` | hover | bg / brightness shift | `150ms` | default |
| `.prog > div` | data load | width grow | `300ms` | default |
| `.toast` | save/error | fade in/out | `300ms` | default |
| `.ov-row`, table rows | hover | bg → `#f8fafc` | `120ms` | default |

No custom keyframes; all motion is CSS `transition`. Keep it subtle — this is a finance tool.

## Accessibility Notes

- **Language**: `<html lang="th">`; print pages also `lang='th'`. Thai screen-reader
  pronunciation depends on correct lang.
- **Color is never the only signal**: direction uses chip **text** ("ดีขึ้น"/"ผิดมากขึ้น")
  not just green/amber; status chips carry icons + words.
- **Contrast**: soft-bg/dark-text chips meet AA (e.g. `--warn #b45309` on `--warn-bg
  #fffbeb`). Verify any new pairing.
- **Tables**: use real `<th>` headers (already present) so SR users get column context.
  Add `scope="col"` if rebuilding (current markup omits it — improvement opportunity).
- **Focus order**: native DOM order is correct (header → actions → table). The `<select>`
  + Save buttons in setup ⑤ are keyboard-operable; ensure Save is reachable by Tab after
  each select.
- **Manager-token prompt** uses `window.prompt` (not screen-reader-ideal) — acceptable for
  an internal tool; a modal with a labelled input would be the a11y upgrade.
- **Print buttons** are real `<button>`s (focusable). Links use `target=_blank` — fine.
- **ARIA gaps to close on a rewrite**: the overview warning banner should be
  `role="status"`/`aria-live="polite"` so it's announced when injected; the toast likewise.

## Open improvement opportunities (not blockers)
- Add `scope="col"` to table headers and `aria-live` to toast + injected banner.
- Replace `window.prompt` manager gate with a labelled modal.
- The print pages fetch Google Fonts (Sarabun) at render — for fully-offline PDF, self-host.
