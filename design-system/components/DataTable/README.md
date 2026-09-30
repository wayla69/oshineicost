# DataTable

The workhorse, ruled like a financial statement: an uppercase header on `bg-subtle` over a strong 1px rule, 8×12px cells, hairline row rules, no zebra.

- Right-align figures with `.num` (tabular numerals). Sortable headers: `th.th-sortable`, with `.sorted-desc` / `.sorted-asc` on the active one.
- A row far outside its column gets `tr.mi-outlier` (bad) or `tr.mi-outlier-good`; flag the cell with an outlined `.mi-flag.bad|good|warn` holding a word, not a symbol alone.
- Branch names use `.branch-drillable` to open the branch drill-down.
