// Generates Oshinei-Business-Deck.pptx — minimal/professional, white pages,
// navy line edge decoration, mock OSHINEI logo (swap the real PNG later).
const pptxgen = require("pptxgenjs");

const NAVY = "1F3A6B", NAVY_DEEP = "16294D", INK = "0F172A", MUTED = "64748B", LINE = "1F3A6B";
const HEAD = "Georgia", BODY = "Calibri";
const W = 13.333, H = 7.5;

const pres = new pptxgen();
pres.defineLayout({ name: "WIDE", width: W, height: H });
pres.layout = "WIDE";
pres.author = "Oshinei";
pres.title = "Oshinei — Sales & Cost Analysis";

// ── edge decoration: thin frame + 4 corner brackets ──
function edges(slide) {
  // subtle full frame
  slide.addShape(pres.shapes.RECTANGLE, { x: 0.32, y: 0.32, w: W - 0.64, h: H - 0.64,
    fill: { type: "none" }, line: { color: LINE, width: 0.75, transparency: 80 } });
  const L = 0.62, t = 2.0;  // bracket arm length / thickness(pt)
  const mk = (x, y, hx, hy) => {
    // horizontal arm
    slide.addShape(pres.shapes.LINE, { x, y, w: hx * L, h: 0, line: { color: LINE, width: t } });
    // vertical arm
    slide.addShape(pres.shapes.LINE, { x, y, w: 0, h: hy * L, line: { color: LINE, width: t } });
  };
  mk(0.26, 0.26,  1,  1);              // tl
  mk(W - 0.26, 0.26, -1,  1);          // tr
  mk(0.26, H - 0.26, 1, -1);           // bl
  mk(W - 0.26, H - 0.26, -1, -1);      // br
}

// ── mock OSHINEI logo (navy disc + white ring + text) ──
function logo(slide, cx, cy, d) {
  const x = cx - d / 2, y = cy - d / 2;
  slide.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: NAVY } });
  const ringPad = d * 0.05;
  slide.addShape(pres.shapes.OVAL, { x: x + ringPad, y: y + ringPad, w: d - 2 * ringPad, h: d - 2 * ringPad,
    fill: { type: "none" }, line: { color: "FFFFFF", width: 1, transparency: 15 } });
  slide.addText("OSHINEI", { x, y: cy - d * 0.18, w: d, h: d * 0.22, align: "center", valign: "middle",
    fontFace: HEAD, bold: true, color: "FFFFFF", fontSize: d * 9, charSpacing: 1, margin: 0 });
  slide.addText("おしねい", { x, y: cy + d * 0.02, w: d, h: d * 0.18, align: "center", valign: "middle",
    fontFace: BODY, color: "FFFFFF", fontSize: d * 6, margin: 0 });
  // mountain stroke
  slide.addShape(pres.shapes.LINE, { x: x + d * 0.22, y: y + d * 0.74, w: d * 0.18, h: -d * 0.10, line: { color: "FFFFFF", width: 1, transparency: 25 } });
  slide.addShape(pres.shapes.LINE, { x: x + d * 0.40, y: y + d * 0.64, w: d * 0.16, h: d * 0.10, line: { color: "FFFFFF", width: 1, transparency: 25 } });
  slide.addShape(pres.shapes.LINE, { x: x + d * 0.56, y: y + d * 0.74, w: d * 0.16, h: -d * 0.12, line: { color: "FFFFFF", width: 1, transparency: 25 } });
}

function cornerLogo(slide) {
  logo(slide, W - 1.0, 0.95, 0.62);
  slide.addText("OSHINEI", { x: W - 2.7, y: 0.78, w: 1.35, h: 0.35, align: "right", valign: "middle",
    fontFace: HEAD, bold: true, color: NAVY, fontSize: 11, charSpacing: 2, margin: 0 });
}

function eyebrow(slide, text, x, y, w, align) {
  slide.addText(text.toUpperCase(), { x, y, w, h: 0.3, align: align || "center",
    fontFace: BODY, bold: true, color: NAVY, fontSize: 12, charSpacing: 4, margin: 0 });
}

// ════════ Slide 1 · Cover ════════
let s = pres.addSlide(); s.background = { color: "FFFFFF" }; edges(s);
logo(s, W / 2, 2.45, 1.55);
eyebrow(s, "Oshinei Enterprise", 0, 3.55, W);
s.addText("Sales & Cost Analysis", { x: 0, y: 3.85, w: W, h: 1.0, align: "center",
  fontFace: HEAD, bold: true, color: INK, fontSize: 46, margin: 0 });
s.addText("A unified analytics dashboard for the salmon-buffet chain — revenue, gross profit, menu engineering, and BoM cost in one place.",
  { x: W / 2 - 4, y: 4.95, w: 8, h: 0.9, align: "center", fontFace: BODY, color: MUTED, fontSize: 16, margin: 0 });
s.addText("Business Overview · 2026", { x: 0, y: H - 0.95, w: W, h: 0.3, align: "center", fontFace: BODY, color: "94A3B8", fontSize: 12 });

// ════════ Slide 2 · Overview (icon + text rows) ════════
s = pres.addSlide(); s.background = { color: "FFFFFF" }; edges(s); cornerLogo(s);
eyebrow(s, "Overview", 0.95, 1.15, 4, "left");
s.addText("One dashboard, every cost question answered", { x: 0.9, y: 1.45, w: 9.5, h: 0.8,
  fontFace: HEAD, bold: true, color: INK, fontSize: 30, margin: 0 });
const feats = [
  ["01", "Revenue & GP", "Ex-VAT revenue, gross profit and net profit across 26 active branches — daily to yearly."],
  ["02", "Menu Engineering", "BCG analysis sorts every dish into Stars, Plowhorses, Puzzles and Dogs with clear actions."],
  ["03", "BoM Cost + Simulator", "Bill-of-materials costing with a live what-if simulator for ingredient and price changes."],
  ["04", "Compare & Forecast", "A/B period comparison and month-end projection with on-track branch indicators."],
];
feats.forEach((f, i) => {
  const col = i % 2, row = Math.floor(i / 2);
  const x = 0.95 + col * 6.0, y = 2.9 + row * 1.85;
  // formal: a square navy-outlined number tile, no emoji
  s.addShape(pres.shapes.RECTANGLE, { x, y, w: 0.62, h: 0.62, fill: { color: "FFFFFF" }, line: { color: NAVY, width: 1.25 } });
  s.addText(f[0], { x, y, w: 0.62, h: 0.62, align: "center", valign: "middle", fontFace: HEAD, bold: true, color: NAVY, fontSize: 14, margin: 0 });
  s.addText(f[1], { x: x + 0.85, y: y - 0.02, w: 4.8, h: 0.4, fontFace: HEAD, bold: true, color: INK, fontSize: 17, margin: 0 });
  s.addText(f[2], { x: x + 0.85, y: y + 0.42, w: 4.85, h: 1.1, fontFace: BODY, color: MUTED, fontSize: 13, margin: 0 });
});

// ════════ Slide 3 · Numbers (big stat callouts) ════════
s = pres.addSlide(); s.background = { color: "FFFFFF" }; edges(s);
eyebrow(s, "At a glance · May 2026", 0, 1.5, W);
s.addText("The business in five figures", { x: 0, y: 1.85, w: W, h: 0.7, align: "center",
  fontFace: HEAD, bold: true, color: INK, fontSize: 30, margin: 0 });
const stats1 = [["฿9.4M", "Revenue (ex-VAT)"], ["39.7%", "Gross Profit"], ["26", "Active branches"]];
const stats2 = [["฿629", "Avg / bill"], ["41.1K", "Customers / mo"]];
function statRow(arr, y) {
  const gap = 3.4, totalW = arr.length * gap, startX = (W - totalW) / 2 + gap / 2;
  arr.forEach((st, i) => {
    const cx = startX + i * gap;
    s.addText(st[0], { x: cx - 1.6, y, w: 3.2, h: 0.9, align: "center", fontFace: HEAD, bold: true, color: NAVY, fontSize: 46, margin: 0 });
    s.addText(st[1], { x: cx - 1.6, y: y + 0.92, w: 3.2, h: 0.4, align: "center", fontFace: BODY, color: MUTED, fontSize: 14, margin: 0 });
  });
}
statRow(stats1, 3.05);
statRow(stats2, 4.85);

// ════════ Slide 4 · Brand palette (swatches) ════════
s = pres.addSlide(); s.background = { color: "FFFFFF" }; edges(s); cornerLogo(s);
eyebrow(s, "Brand", 0.95, 1.15, 4, "left");
s.addText("A tight, purposeful palette", { x: 0.9, y: 1.45, w: 9.5, h: 0.7, fontFace: HEAD, bold: true, color: INK, fontSize: 30, margin: 0 });
s.addText("Navy accent with semantic green / red / amber, and one hue per area of the app — built for clarity in a B2B analytics tool.",
  { x: 0.95, y: 2.25, w: 9, h: 0.5, fontFace: BODY, color: MUTED, fontSize: 14, margin: 0 });
const core = [["1F3A6B", "Navy"], ["16294D", "Sidebar"], ["10B981", "Good"], ["EF4444", "Bad"], ["F59E0B", "Warn"]];
const ext = [["F97066", "Salmon"], ["E8685A", "Salmon-deep"], ["0D9488", "Teal"]];
function swatchRow(arr, y) {
  arr.forEach((c, i) => {
    const x = 0.95 + i * 1.55;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w: 1.25, h: 1.25, rectRadius: 0.12, fill: { color: c[0] } });
    s.addText(c[1], { x, y: y + 1.3, w: 1.25, h: 0.3, align: "center", fontFace: BODY, bold: true, color: INK, fontSize: 12, margin: 0 });
    s.addText("#" + c[0], { x, y: y + 1.58, w: 1.25, h: 0.25, align: "center", fontFace: BODY, color: "94A3B8", fontSize: 10, margin: 0 });
  });
}
swatchRow(core, 3.0);
s.addText("OPTIONAL EXTENSION · NOT IN SOURCE PRODUCT", { x: 0.95, y: 5.15, w: 6, h: 0.3,
  fontFace: BODY, bold: true, color: "B45309", fontSize: 11, charSpacing: 1, fill: { color: "FEF3C7" }, align: "center", margin: 4 });
swatchRow(ext, 5.6);

// ════════ Slide 5 · Close ════════
s = pres.addSlide(); s.background = { color: "FFFFFF" }; edges(s);
logo(s, W / 2, 2.7, 1.4);
s.addText("Thank you", { x: 0, y: 3.75, w: W, h: 0.8, align: "center", fontFace: HEAD, bold: true, color: INK, fontSize: 36, margin: 0 });
s.addText("Oshinei — Sales & Cost Analysis  ·  oshinei-dashboard.fly.dev",
  { x: 0, y: 4.7, w: W, h: 0.4, align: "center", fontFace: BODY, color: MUTED, fontSize: 15, margin: 0 });

pres.writeFile({ fileName: "Oshinei-Business-Deck.pptx" }).then(f => console.log("wrote", f));
