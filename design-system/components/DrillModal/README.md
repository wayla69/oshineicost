# DrillModal

The drill-down: a dark `scrim` with a centred bordered panel holding the detail behind any clickable figure.

- `.drill-modal` (fixed, `scrim`) > `.drill-panel` (`bg-card`, 1px `border`, 4px corner, `shadow-modal`). Inside, `.dive-section-title` over an `accent` rule, then `.dive-metric` rows (`.lbl` / `.val`, `.val.good|bad`).
- Close on Esc and scrim click. The dive-in variant is full-screen with the chart left and the analysis in a 380px column right.
