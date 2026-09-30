# DrillModal

The drill-down: a blurred navy scrim with a centred panel holding the detail behind any clickable figure.

- `.drill-modal` (fixed, `scrim`, 8px blur) > `.drill-panel` (`bg-card`, 12px radius, `shadow-modal`). Inside, use `.dive-section-title` headings and `.dive-metric` rows (`.lbl` / `.val`, `.val.good|bad`).
- Open with a 0.96 → 1 scale; close on Esc and scrim click. The dive-in variant is full-screen with the chart left and the analysis in a 380px column right.
