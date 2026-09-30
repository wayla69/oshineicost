# KpiCard

A headline figure: uppercase label, a 26px/700 value, a delta tag and a footnote, under a 3px top rule that states direction.

- `.card.kpi-card` + `.kpi-up` (`good` rule), `.kpi-down` (`bad`) or `.kpi-neu` (`accent`, the default).
- Supply `.kpi-card-top` (`.kpi-label`), `.kpi-value`, and `.kpi-card-bottom` (`.kpi-sub` + a `.pill` tag).
- Show the latest period and its movement, never an all-time total. Lay four to five in a row.
- A figure that states direction in its text uses `.up` / `.down` (the inks). If the card sits on a light tint in dark mode, give it `.on-light` so the inks stay dark enough to read.
