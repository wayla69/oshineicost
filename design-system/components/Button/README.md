# Button

Buttons are quiet by default; indigo fill is kept for the one action the view exists for.

- Primary: `.retry-btn` (or `bg-indigo-600 text-white` in Tailwind markup, remapped to `indigo`). Label in `on-indigo`: white in light, near-black in dark. Lifts on hover; the Tailwind hover goes to `indigo-mid` in light and `border-hover` in dark.
- Secondary: `.upload-btn` — `bg-input`, `border`, `text-secondary`; hover turns indigo. Admin actions (upload, export) are always secondary so they don't out-shout the data.
- Icon: `.topbar-icon-btn`, 32×32, for top-bar tools (theme, refresh, search).
- In-panel suggestion: `.dive-action-btn`, full-width, left-aligned, turns indigo and nudges right on hover.
- All buttons press to 0.97 scale. Label with a verb: "โหลดใหม่", "Export Excel".
