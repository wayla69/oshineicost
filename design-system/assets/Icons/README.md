# Icons

The sidebar's 49 page icons, one per page, named by the page id used in `switchTab('<id>')` (e.g. `home`, `dailysales`, `pnlbill`, `ordering`). 24×24 grid, 1.75 stroke, round caps and joins, no fill.

- Single ink: these files are stroked in navy `accent` (#1f3a6b) so they display as images. In the app they are inline `<svg class="nav-icon">` with `stroke: currentColor` — `nav-ink` at rest, `nav-active-icon` on hover and when active. Remove the stroke attribute when inlining.
- Show at 16px in navigation and inside 32px icon buttons (`nav-icon-size`); never fill them.
