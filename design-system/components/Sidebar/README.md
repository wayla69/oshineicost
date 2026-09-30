# Sidebar

The navy navigation rail: logo block, emoji-labelled sections, stroke-icon items, and a 3px gradient marker on the active item.

- `.sidebar` > `.sidebar-logo` (LogoMark + name) and `.sidebar-nav` with `.nav-section` headings and `.nav-item` rows (`svg.nav-icon` + `.nav-label`). `.active` marks the current page.
- 224px wide, 64px collapsed (labels hidden). On tablets it turns into a horizontal strip; on phones the mobile summary replaces it.
- Text on the rail: `nav-ink` at rest (5.8:1), `nav-active-ink` hovered or active, `nav-section-ink` (5.0:1) for section headings — headings stand apart by uppercase 700, not by being dimmer.
