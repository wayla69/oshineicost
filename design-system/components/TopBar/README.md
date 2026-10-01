# TopBar

The sticky bar over the content: page title on the left, scope controls (branch, period) and tools on the right, on `bg-topbar` with a 1px bottom rule.

- `.top-bar` > `.page-title` + `.controls`. The controls wrap to a second line instead of overlapping.
- Scope controls sit left of tools; the connection badge and icon buttons come last.
- Inside a page group (`[data-group]`) the bar carries a 3px stripe of the group hue along its bottom edge and the title a 4px marker in it, so the area of the app reads before any content.
