# Card

The base surface: a white (dark: `bg-card`) panel with a 16px radius, a faint border and `shadow-card`.

- Use `.card` for every grouped block of content; add `.pad` for the standard 18px padding.
- Cards lift 1px on hover. Don't nest cards; use `bg-subtle` wells inside a card instead.
- The consumer supplies the content and, usually, a `.section-title` as the first child.
