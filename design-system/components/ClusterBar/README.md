# ClusterBar

The row of views that belong to the open page's group, on a soft band of the group hue: the group name in its `-ink`, then one chip per view with the open one filled `-soft` and edged in the hue.

- `.cluster-bar` > `.cluster-label` + `.cluster-chip` (+ `.on`). Place it under the top bar, above the page's own subtabs; it sits inside a `[data-group]`.
- One chip per page of the group, in the order of TAB_CLUSTERS; a sub-view of a page (Top 60 under ต้นทุน/หัว, Menu Engineering under วิเคราะห์เมนู) lights its page's chip instead of adding one.
- Chips the role cannot open are left out, never shown disabled. Labels are Thai words, no emoji.
