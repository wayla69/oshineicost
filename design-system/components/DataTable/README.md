# DataTable

The workhorse: sticky uppercase header on `bg-subtle`, 10×14px cells, soft row rules, faint zebra, indigo hover.

- Right-align figures with `.num` (tabular numerals). Make headers sortable with `th.th-sortable`; the sorted one gets `.sorted-desc` or `.sorted-asc`.
- Mark a row far outside its column with `tr.mi-outlier` (bad) or `tr.mi-outlier-good`, and flag a cell with `.mi-flag.bad|good|warn` (⚠ ★ 🔥).
- Branch names use `.branch-drillable` to open the branch drill-down.
- Wrap wide tables in a card that scrolls horizontally on phones.
