# Sidebar

The navy navigation rail: the logo block, uppercase section headings, one stroke-icon entry per area of the app with each icon in its group's hue, and a 4px marker in that hue on the active entry.

- `.sidebar` > `.sidebar-logo` (LogoMark + name) and `.sidebar-nav` with `.nav-section` headings and `.nav-item` rows (`svg.nav-icon` + `.nav-label`), each carrying `data-g` (its group). `.active` marks the current group and stays lit on every view of it, not only its first page.
- Order (2026-10-01): หน้าแรก · รายงานผู้บริหาร · **วิเคราะห์** ยอดขาย · ลูกค้า & บุฟเฟ่ต์ · เสียงลูกค้า · เมนู · ต้นทุน · กำไร · **ควบคุม & ปฏิบัติการ** ส่วนลด & เงิน · ปฏิบัติการ · จัดซื้อ · **เครื่องมือ** คู่มือ · ตั้งค่า. An entry opens its group's first view; the others are on that page's tab bar.
- Labels are words only; the stroke icon is the only pictogram. No emoji in labels or headings.
- Text on the rail: `nav-ink` at rest (7.3:1), `nav-active-ink` hovered or active, `nav-section-ink` (5.6:1) for headings. Icons: `group-*-side` (5:1 on the rail); the active entry adds a 22% wash of its hue.
