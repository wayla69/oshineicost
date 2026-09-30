# KpiCard

A headline figure: uppercase label, a 26px/800 value, a delta pill and a footnote, under a 3px top rule that states direction.

- Markup: `.card.kpi-card` + `.kpi-up` (green rule), `.kpi-down` (red) or `.kpi-neu` (indigo, the default).
- Supply `.kpi-card-top` (`.kpi-label` + optional pill), `.kpi-value`, and `.kpi-card-bottom` (`.kpi-sub` + pill).
- Show the latest period and its movement, never an all-time total. Lay four to five in a `.grid.g-4`.
