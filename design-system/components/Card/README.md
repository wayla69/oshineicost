# Card

The base surface: `bg-card` with a 1px `border` and a 4px corner. No shadow, no hover lift.

- Use `.card` for every grouped block; `.pad` gives the standard 16px padding.
- Separate cards by the grid gap, never by shadow. Don't nest cards; use a `bg-subtle` well inside one.
- The consumer supplies the content and, usually, a `.section-title` as the first child.
