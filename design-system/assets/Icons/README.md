# Icons

The sidebar's 49 page icons, one per page, named by the page id used in `switchTab('<id>')` (e.g. `home`, `dailysales`, `pnlbill`, `ordering`). 24×24 grid, 1.75 stroke, round caps and joins, no fill.

- Single ink: these files are stroked in `indigo` (#4f46e5) so they display as images. In the app they are inline `<svg class="nav-icon">` with `stroke: currentColor` — `nav-ink` at rest, `nav-active-icon` on hover and when active. Remove the stroke attribute when inlining.
- Show at 17px in navigation (`nav-icon-size`); never fill them.
