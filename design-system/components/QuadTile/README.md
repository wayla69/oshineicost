# QuadTile

The four menu-engineering quadrants as clickable filter tiles: Star, Plowhorse, Puzzle, Dog.

- `.quad-tile` + `.quad-star|quad-plow|quad-puzzle|quad-dog`, holding `.qicon`, `.qname`, `.qcount`, `.qdesc`. `.active` outlines the selected one in its own colour.
- Always all four, in that order. Buffet (B) and à-la-carte (A) SKUs are analysed separately.
- Each quadrant's ink has a light and a dark value (`quad-star-ink` …), so the text stays legible on its tint in both themes. Descriptions use the full ink, not a faded one.
