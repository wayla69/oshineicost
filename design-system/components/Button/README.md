# Button

Buttons are outlined by default; the `accent` fill is kept for the one action the view exists for.

- Primary: `.retry-btn` (or `bg-indigo-600 text-white` in the app's Tailwind markup, remapped to `accent`). Label in `on-accent`; hover to `accent-hover`.
- Secondary: `.upload-btn` — `bg-input` with a `border-control` edge; hover turns the edge and label `accent`. Admin actions (upload, export) are always secondary.
- Icon: `.topbar-icon-btn`, 32×32, holding a 16px stroke icon (1.75 stroke, like the nav icons), never an emoji. Give it an `aria-label`.
- In-panel suggestion: `.dive-action-btn`, full-width, left-aligned; fills `accent` on hover.
- Label with a verb: "โหลดใหม่", "Export Excel".
