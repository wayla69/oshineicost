> **About this folder.** A repo copy of the Oshinei design system published at
> https://claude.ai/artifact/3UfNoQPSWHZzAav4TF1Fup, last synced 2026-09-30 (formal theme, KIRA UI + Noto symbols, text inks; app at `8c19443`;
> navy accent in `docs/ui-kit/theme-formal.css`). The app itself never loads these files: its colours come from
> `docs/ui-kit/tokens.css`. When a colour changes, change it there first, then update
> `tokens.json` here (and republish the system) so the two stay in step.
> Fonts are the app's own `vendor/fonts/KIRA-UI-*.woff2` and `vendor/fonts/symbols/*.subset.woff2` (`tokens.json` names them as `fonts/...`; the OFL licences sit beside them).
> The `components/*/preview.html` cards render only inside the published system, which
> generates `tokens.css` from `tokens.json`. This folder sits outside `docs/` on purpose:
> `docs/` is copied into the Fly image, and parts of it are served without a login.

Oshinei is the sales and cost dashboard for a Thai buffet restaurant chain: 29 branches, one owner, branch managers, accounting and kitchen staff. People open it to answer "how did yesterday go, what is leaking money, and what do I order". It should read like a well-kept management report: navy, ruled, exact. Numbers first, one accent, nothing decorative.

## Using this system

- Load `tokens.css` (or the product's `/docs/ui-kit/tokens.css`), the KIRA UI and Noto symbol font files and `components/bundle.css`. Class names in the component cards are the app's own, so markup from a preview drops into `index.html` unchanged.
- The app switches themes with `body.dark`. Here the same rules key off `[data-theme="dark"]`; when porting to the app, write `body.dark`.
- The app still calls the accent `--indigo` / `--indigo-mid` / `--indigo-soft`. Those are legacy aliases of `accent` / `accent-hover` / `accent-soft`; new code uses the accent names.
- There is no JavaScript component library. Components are CSS classes on plain HTML; charts are Chart.js 4 with the defaults under **Charts**.
- Style through tokens. Never type a hex that has a token.

## Content fundamentals

- **Thai first, English terms of art.** Labels are Thai; keep the business vocabulary in English where the team uses it: KPI, MoM, YoY, MTD, BoM, P&L, GP, SKU. For example "ยอดขายรายวัน", "เปรียบเทียบ A/B", "BoM + Simulator", "P&L ต่อบิล".
- **Page titles pair Thai with a short English gloss** separated by a middle dot: "หน้าแรก · Daily Brief". Tooltips say what the page answers.
- **No emoji anywhere in the interface**: not in navigation, headings, buttons, findings or table flags. Status is a word in a tag (ดี, ต้องแก้, เฝ้าดู, ข้อมูล); direction is ▲ ▼ with a sign; pictograms are the stroke icons.
- **Money is Thai baht with a ฿ prefix and thousand separators**, no decimals above ฿100 (`฿1,284,560`). In a table column whose header names the unit, drop the ฿. Percentages carry one decimal (`66.3%`); deltas carry an arrow and sign (`▲ 12.4%`).
- **Revenue is ex-VAT.** Never show VAT as revenue; a figure labelled ยอดขาย is net of VAT and service charge unless the label says otherwise.
- **Headlines are the latest period plus movement** (vs last month, vs same month last year, year-to-date change), never an accumulated all-time total. Partial months compare against a weekday-fair baseline and say so ("เทียบวันเดียวกันสัปดาห์ก่อน").
- **Recommendations are imperative and specific**: name the branch, the menu item and the baht at stake. "ลดขนาดชิ้นปลาไหล 20% — ประหยัดราว ฿41,000/เดือน", not "consider optimising portions".

## Visual foundations

### Principles

- **Borders, not shadows.** Surfaces separate with a 1px `border` and the grid gap. Only floating things (menus, the modal) cast a shadow.
- **Square, not soft.** Corners are 2–4px (`r-xs`, `r-sm`, `r-md`). `r-pill` is only for dots and progress tracks; tags and chips are square-cornered.
- **Flat colour.** No gradients: fills, rules, bars and the logo are solid.
- **Still.** No hover lifts, no shimmer, no slide-ins. Hover changes colour or border only, in 0.12s.

### Color

- `accent` (navy `#1f3a6b`; `#93b4e6` in dark) is the only accent: the active tab underline, the active nav marker, the focus ring, links, the primary button and the default KPI rule. `accent-soft` is its tinted ground; `accent-hover` its hover. Label an accent fill with `on-accent`, never a literal white.
- `good`, `bad` and `warn` carry direction only (up/on-track, down/off-track, needs a look), as the KPI top rule, a progress fill or a status dot. For coloured text use `good-ink`, `bad-ink`, `warn-ink`, `teal-ink` / `rose-ink` for teal and rose, `cyan-ink` / `sky-ink` for cyan and sky, `navy-ink` / `violet-ink` / `fuchsia-ink` / `pink-ink` for chart-series colours used as figures, and `amber-ink` / `orange-ink` for warnings and mid bands; tags use the `pill-*` pairs. Status always comes with a word or ▲ ▼, never colour alone.
- Up/down figures are TEXT in `good-ink` / `bad-ink`, never in the mark colours. In markup use `.up` / `.down` (Tailwind `.text-emerald-500/600/700` and `.text-red-500/600/700` are remapped to the inks too, as are `.text-rose-500/600/700`, `.text-teal-700`, `.text-cyan-600/700` and `.text-sky-600/700` to their inks, and `.text-blue-600/700` to `navy-ink` in dark mode); in scripts, a colour that feeds both a mark and a figure keeps its hex for the mark and passes the figure through `textInk(hex)`, which returns the matching ink for the known greens, reds, teals, roses, cyans, skies, navies (blue-600/700 included), violets, fuchsias, pinks, ambers (the yellows included: `#facc15` is 1.53:1 on white) and oranges, and `text-muted` for the inline greys.
- The inks follow the nearest light or dark ground. In dark mode, anything that stays light keeps the light inks: add `.on-light` to such a box; the `-50` tints, the emerald/green/red/rose `-100` chips and pastel inline card fills (`background:#d…/#e…/#f…`) are scoped the same way. Anything dark mode repaints (`.bg-white`, `.bg-slate-50`, plain cards, hovered rows) takes the dark inks again. Paper is white, so print always uses the light inks. The text tokens (`text-primary` / `-secondary` / `-muted`), the accent and the input/border tokens follow the same scoping, so body text inside a light box stays dark in dark mode; an inline pastel fill counts as a light box when its hex is in the list in index.html (add new pastels there, or use `.on-light`). A fill set from script (`el.style.cssText` / `style.background`) is serialised as `rgb(…)`, so the hex list never matches it: give that box `.on-light`.
- Translucent tints (`bg-red-50/50` and similar) would composite to a mid-grey over the dark card; in dark mode they become the same hue at about 12% (`accent-soft` for indigo), so both the text and the inks stay above 4.5:1.
- Surfaces: `bg-page` behind everything, `bg-card` for cards, `bg-subtle` for table heads and wells, `bg-hover` for row hover. The sidebar is `bg-sidebar` navy in both themes.
- Lines: `border` for card and panel edges, `border-soft` between rows, `border-control` (3:1) around inputs, selects and outlined buttons, and `text-secondary` for the strong rule under a table header.
- Text runs `text-primary` → `text-secondary` → `text-muted`. Every text token clears 4.5:1 on the surfaces its note names, in both themes.
- Grey text is a text token, never a slate hex: Tailwind `.text-slate-300/400` map to `text-muted` and `.text-slate-500` to `text-secondary` (outside the sidebar), because slate-400 is 2.56:1 and slate-300 1.5:1 on white.
- A label ON a coloured fill (heatmap cells, bar segments, priority chips) takes `inkOn(fill)`: white or `#0f172a`, whichever contrasts more; an empty or near-zero cell on the pale `#f8fafc` ground uses `text-muted`. A filled button or active segment with white text uses the hue's `-700` shade (`-800` on hover); the `-500`/`-600` hues are marks, 2.1–4.1:1 under white.
- Charts cycle `chart-1` … `chart-12`: muted, deep hues that each reach 3:1 on both the light and the dark card. A branch hashes to a fixed slot and keeps its colour across charts.
- `navy` / `navy-deep` belong to decks and printed reports. `salmon`, `salmon-deep` and `teal` are an optional marketing extension, never used in the dashboard.

### Typography

- One family: **KIRA UI** (Thai + Latin; a rebuild of Noto Sans + Noto Sans Thai under the SIL Open Font License), weights 400/500/600/700, shipped as local WOFF2 with its licence. Heavier requests resolve to 700. Fallback `'Noto Sans Thai', system-ui`.
- **Symbols come from Noto too.** KIRA UI has no arrows, math signs, shapes or dingbats, so → − ≤ ≥ ≈ ▲ ▼ ● ■ ✓ ✗ ★ ⚠ ☐ ① ⓘ would fall back to whatever the device has. Three small subsets fill the gap (16 KB in all, SIL OFL): **Noto Sans Math** (arrows, − ≤ ≥ ≈ ≠ ∞ √), **Noto Sans Symbols 2** (▲ ▼ ▶ ● ■ ◆ ✓ ✗ ★ ⚠ ☐ ☑) and **Noto Sans Symbols** (① … ⑩ ⓘ ⚑). In the app they are declared under the family `'KIRA UI'` itself, with a `unicode-range` each and once per weight (400–700), so every stack picks them up and a file loads only when a page uses one of its characters; ● ○ ◉ ■ □ ◆ ◇ get `size-adjust: 60%` so they sit at bullet size (Noto draws them 0.73–1.0em). Here they follow KIRA UI in the `sans` stack instead. Reports and PDFs embed the same subsets, so a printed ▲ or ✓ is Noto, not Arial.
- It stacks Thai tone marks correctly and has tabular figures, so figures line up in columns with `font-variant-numeric: tabular-nums`.
- The scale is small and dense: `body` 13px, `control` 12.5px, `small` 12px, `caption` 11px. Figures use weight 700, never 800+: `kpi-value` 26px and `kpi-hero` 27px.
- Labels (`kpi-label`, `section-label`, `table-head`, `nav-section`) are UPPERCASE at 10–11px, weight 700, tracked 0.05–0.1em. Thai text in those slots stays as written.
- Every column of figures is `font-variant-numeric: tabular-nums` and right-aligned (`.num`).

### Layout and spacing

- Desktop shell: 224px navy sidebar (64px collapsed), sticky top bar with a bottom rule, content padded `space-content` (28px); 12px on phones.
- Cards sit in a grid with a `space-4` (16px) gap; `.card.pad` is 16px. Grids of 3–5 columns fall to 2 under 1000px and 1 under 640px.
- Tables pad cells 8 × 12px. Header over a strong rule, hairlines between rows, no zebra striping.
- Under 700px the desktop app is replaced by the mobile owner summary (navy header, stacked KPI cards, bordered chips).

### Depth, motion and focus

- `shadow-none` on every resting surface. `shadow-popover` for menus, `shadow-modal` for the drill-down panel over the `scrim`.
- Motion is limited to colour and border changes (0.12s) and a gentle pulse on loading skeletons. Honour `prefers-reduced-motion`.
- Focus: a 2px solid `accent` outline with a 2px offset on every focusable element; inputs add the `focus-halo` ring. It reaches 3:1 on every surface in both themes.

### Charts

Chart.js 4 defaults, set once in `setupChartDefaults()` and themed per chart by `applyChartTheme()`:

- Font KIRA UI 11.5px. Legends and titles use `chart-ink`, axis ticks `chart-tick`, gridlines `chart-grid`, all per theme; toggling the theme rebuilds every chart. Don't rely on the global `Chart.defaults.color`. Legend, title and axis-title colours hard-coded to a light-mode slate are rewritten to the theme ink, so a chart written for light mode still reads in dark.
- Bars: 2px corners (`r-xs`), max 46px thick. Lines: width 2, tension 0.25; points 2px at rest (scatter charts need them), 6px on hover.
- Legends: square markers, 8px, 14px padding.
- Tooltip: `tooltip-bg` card with a 3px corner and no border glow; title 12.5px/600 in `tooltip-title`, body 12px in `tooltip-body`; values thousand-separated, 2 decimals only under 100.
- On-chart data labels are rounded to 4 decimals so float dust never prints.

## Iconography

- 49 stroke icons in `assets/Icons/`, one per page (named by page id: `home`, `dailysales`, `pnlbill` …): a 24 grid, 1.75 stroke, round caps and joins, no fill. Shown at 16px in the nav and inside 32px icon buttons.
- In the app they are inline `<svg class="nav-icon">` using `currentColor`: `nav-ink` at rest, `nav-active-icon` when hovered or active. The files here are stroked in navy so they show as images; drop the stroke attribute when inlining.
- Top-bar tools (theme, refresh, search) use stroke icons in the same style, never emoji.
- Table sort state uses ⇅ ▲ ▼; direction in figures uses ▲ ▼.

## Logo

- **App mark:** the hiragana お in navy KIRA UI 700 on a 32px white tile with a 3px corner, beside "OSHINEI" (14px/700, tracked 0.08em, `sidebar-title`) and "Sales & Cost Analysis". It is CSS, not an image; see the LogoMark card.
- **Deck roundel:** `assets/Logos/oshinei-roundel.svg`, a `navy` disc with a white hairline ring, OSHINEI over おしねい and a rising sparkline. Use it on white for decks, printed reports and document corners. Its text is live SVG text set in Prompt (the deck face), so it needs that font on the page when inlined; otherwise it falls back to a system sans.
