> **About this folder.** A repo copy of the Oshinei design system published at
> https://claude.ai/artifact/3UfNoQPSWHZzAav4TF1Fup, last synced 2026-09-30 against the app at
> `5eb03b3`. The app itself never loads these files: its colours come from
> `docs/ui-kit/tokens.css`. When a colour changes, change it there first, then update
> `tokens.json` here (and republish the system) so the two stay in step.
> Fonts are the app's own `vendor/fonts/prompt-*.ttf` (`tokens.json` names them as `fonts/...`).
> The `components/*/preview.html` cards render only inside the published system, which
> generates `tokens.css` from `tokens.json`. This folder sits outside `docs/` on purpose:
> `docs/` is copied into the Fly image, and parts of it are served without a login.

Oshinei is the sales and cost dashboard for a Thai buffet restaurant chain: 29 branches, one owner, branch managers, accounting and kitchen staff. People open it to answer "how did yesterday go, what is leaking money, and what do I order". Every screen is a working screen: numbers first, one accent, calm surfaces.

## Using this system

- Load `tokens.css` (or the product's `/docs/ui-kit/tokens.css`), the Prompt font files and `components/bundle.css`. Class names in the component cards are the live product's own — markup copied from a preview drops into `index.html` unchanged.
- The product switches themes with `body.dark`. In this system the same rules key off `[data-theme="dark"]`; when porting to the app, write `body.dark` instead.
- There is no JavaScript component library. Components are CSS classes on plain HTML; charts are Chart.js 4 with the defaults under **Charts** below.
- Style through tokens. Never type a hex that has a token (`#4f46e5` is `indigo`, `#e2e8f0` is `border`).

## Content fundamentals

- **Thai first, English terms of art.** Labels are Thai; keep the business vocabulary in English where the team uses it: KPI, MoM, YoY, MTD, BoM, P&L, GP, SKU. Examples from the product: "ยอดขายรายวัน", "เปรียบเทียบ A/B", "BoM + Simulator", "P&L ต่อบิล".
- **Page titles pair Thai with a short English gloss** separated by a middle dot: "หน้าแรก · Daily Brief", "ภาพรวม & KPI". Tooltips follow the same pattern and say what the page answers: "ยอดขายรายวัน · รายสาขา วัน/สัปดาห์/เดือน + เทียบเดือนก่อน/ปีก่อน".
- **Emoji mark navigation, not content.** Every nav label and nav section starts with one emoji (🏠 หน้าแรก, 🧾 P&L ต่อบิล, 📊 วิเคราะห์). Don't add emoji to body copy, table cells or KPI values. Flags in tables use the fixed vocabulary ⚠ bad · ★ good · 🔥 warn.
- **Money is Thai baht with a ฿ prefix and thousand separators**, no decimals above ฿100 (`฿1,284,560`). Percentages carry one decimal (`66.3%`); deltas carry an arrow and sign (`▲ 12.4%`, `▼ 3.1%`).
- **Revenue is ex-VAT.** Never show VAT as revenue; a figure labelled ยอดขาย is net of VAT and service charge unless the label says otherwise.
- **Headlines are the latest period plus movement** (vs last month, vs same month last year, year-to-date change) — never an accumulated all-time total. Partial months compare against a weekday-fair baseline and say so in the kpi-sub line ("เทียบวันเดียวกันสัปดาห์ก่อน").
- **Recommendations are imperative and specific**: name the branch, the menu item and the baht at stake. "ลดปริมาณแซลมอนต่อจาน สาขาสุรินทร์ — ประหยัด ฿38,000/เดือน", not "consider optimising portions".

## Visual foundations

### Color

- `indigo` is the only accent. Use it for the active tab, the focus ring, links, the primary button and the default KPI top rule. `indigo-soft` is its tinted ground; `indigo-mid` its hover in light — in dark, hover goes lighter to `border-hover`, since neither label colour reaches 4.5:1 on `indigo-mid` there. Label an indigo fill with `on-indigo` — white in light, near-black in dark, where indigo lightens — never a literal white. The logo tile and mobile header, which keep white text in both themes, use the fixed `brand-indigo`.
- `good`, `bad` and `warn` carry direction only: up/on-track, down/off-track, near/needs a look. They appear as rules, fills and dots — a 3px KPI top rule (`kpi-up` / `kpi-down`), a progress fill, a bullet's left rule. For coloured TEXT use `good-ink`, `bad-ink`, `warn-ink` (the `.up` / `.down` classes do); the fills read 2.5–3.8:1 as text. Direction is never colour alone: pair it with ▲ ▼ or a word.
- Surfaces are cool slate: `bg-page` behind everything, `bg-card` for cards, `bg-subtle` for table heads and quiet wells, `bg-hover` for row hover. The sidebar is `bg-sidebar` navy in both themes.
- Text runs `text-primary` → `text-secondary` → `text-muted`. Every text token clears 4.5:1 on every surface its note names, in both themes; `text-muted` is the floor (4.76:1 on `bg-hover`), so never go lighter for text.
- Pills keep their light colours in both themes (`pill-up-bg` / `pill-up-ink` etc.); they read 6.4–8.1:1.
- Charts cycle `chart-1` … `chart-12`; a branch hashes to a fixed slot so it keeps its colour across every chart. Year-over-year charts draw the latest year in `indigo` and earlier years in slate, cyan, green, amber.
- `navy` and `navy-deep` belong to the business deck and printed reports. `salmon`, `salmon-deep` and `teal` are an optional marketing extension — never put them in the dashboard.

### Typography

- One family: **Prompt** (Thai + Latin), weights 300–900, shipped as local files. Fallback `'Noto Sans Thai', system-ui`.
- The scale is small and dense: `body` 13px, `control` 12.5px, `small` 12px, `caption` 11px. Numbers get the weight: `kpi-value` 26px/800 and `kpi-hero` 27px/800 with −0.02em tracking.
- Eyebrows and table heads are UPPERCASE at 9.5–10.5px, weight 700, tracked 0.06–0.1em (`kpi-label`, `section-label`, `table-head`, `nav-section`). Thai text in those slots stays as written.
- Set every column of figures with `font-variant-numeric: tabular-nums` (the `.num` class right-aligns and does this).

### Layout and spacing

- Desktop shell: 224px navy sidebar (64px collapsed), sticky top bar, content area padded `space-content` (28px); 12px on phones.
- Cards sit in a `.grid` with a `space-4` (16px) gap; `.card.pad` is 18px (`space-card`). Grids of 3–5 columns fall to 2 under 1000px and 1 under 640px.
- Table cells pad `cell-y` × `cell-x` (10 × 14px). Rows separate with `border-soft`; even rows tint faintly; hover turns the row `bg-hover`.
- Under 700px the desktop app is replaced by the mobile owner summary (indigo header, stacked KPI cards, chips).

### Shape, depth and motion

- Radii step by use: `r-xs` 6 (segments, chart bars), `r-sm` 8 (inputs, buttons), `r-nav` 9, `r-bar` 10 (bars, banners, logo tile), `r-md` 12 (tiles, modal), `r-lg` 16 (cards), `r-pill` for pills and tracks.
- Cards rest on `shadow-card` with a 1px border at 70% of `border`; on hover they lift 1px to `shadow-card-hover`. Quadrant tiles lift 2px to `shadow-md`. The modal panel uses `shadow-modal` over a blurred `scrim`.
- Motion is quick and small: 0.15s ease on hover and colour, 0.2s on card lift, 0.22s `cubic-bezier(.16,1,.3,1)` fade-up when a tab opens, a 0.97 press scale on buttons. Loading shows a shimmering skeleton and a 3px indigo bar across the top. Honour `prefers-reduced-motion`.
- Focus: 2px solid `indigo` outline, 2px offset, on every focusable element; inputs add the `focus-halo` ring.

### Charts

Chart.js 4 defaults set once in `setupChartDefaults()`:

- Font Prompt 11.5px. Legends and titles use `chart-ink`, axis ticks `chart-tick`, gridlines `chart-grid`, all per theme: `applyChartTheme()` writes them into each chart when it is built, and toggling the theme destroys and rebuilds every chart. Don't rely on the global `Chart.defaults.color`; it is a fixed light-theme value.
- Bars: corner radius 6, no skipped border, max 46px thick.
- Lines: tension 0.32, width 2.5; points 2.2px at rest, 6px on hover.
- Legends: point-style, 8px boxes, 14px padding.
- Tooltip: `tooltip-bg` card, radius 9, padding 11, title 12.5px/600 in `tooltip-title`, body 12px in `tooltip-body`; values thousand-separated, 2 decimals only under 100.
- On-chart data labels are rounded to 4 decimals so float dust never prints.

## Iconography

- Navigation uses 49 stroke icons in `assets/Icons/`, one per page (named by page id: `home`, `dailysales`, `pnlbill` …). They are drawn on a 24 grid with a 1.75 stroke, round caps and joins, no fill — the Feather/Lucide idiom — and shown at 17px (`nav-icon-size`).
- In the app they are inline `<svg class="nav-icon">` using `currentColor`: `nav-ink` at rest (5.8:1 on the rail), `nav-active-icon` when hovered or active. The files here are stroked in `indigo` so they show as images; when inlining, drop the stroke attribute.
- Emoji prefix nav labels (see Content). They are not a substitute for the stroke icons.
- Table sort state uses text glyphs ⇅ ▲ ▼; direction in figures uses ▲ ▼.

## Logo

- **App mark:** the hiragana お in white Prompt 800 on a 36px `r-bar` tile filled with a 135° gradient from `brand-indigo` to `violet`, glowing with `shadow-logo`, beside "Oshinei" (14px/800, `sidebar-title`) and "Sales & Cost Analysis". It is CSS, not an image — see the LogoMark card.
- **Deck roundel:** `assets/Logos/oshinei-roundel.svg` — a `navy` disc with a white hairline ring, OSHINEI over おしねい and a rising sparkline. Use it on white for decks, printed reports and document corners. It sets its type in Prompt, so it needs the font on the page when inlined.
