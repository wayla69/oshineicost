# -*- coding: utf-8 -*-
"""Draw the swim-lane chart of WP-SCM-001 §6.3 on a fixed grid and splice it into the SOP HTML.

Every box sits inside one lane x one column; every arrow runs either straight inside a column or through the
gap ON a column boundary, so no line crosses a box. The script checks its own geometry (no segment or label
through a box that is not its end) and, in the browser, that every text line fits inside its box.

Run from the repo root:  py -3.14 scripts\\sop_swimlane.py      (then py -3.14 scripts\\render_sop.py)
"""
import io, os, re, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "docs", "sop", "WP-SCM-001_ingredient_order_procedure.html")

NAVY, INK, AMBER, GREEN = "#1f3a6b", "#1b2a41", "#b45309", "#15803d"
W = 1000
LABEL_W = 118                      # lane-name column
X0, COLW = 124, 146                # six time columns: 124 … 1000
BOXW = 128                         # 9 px clear of each column boundary (where the elbows run)
HEAD_H, TOP = 30, 34               # the chart + legend must fit ONE A4 landscape page: keep H ≤ ~545
FS, LH = 9.2, 13                   # box text size and line height
COLS = ["① ตั้งต้น (รายเดือน)", "② ทุกวันที่เปิดขาย", "③ ก่อนตัดรอบ 13:15", "④ วันตัดรอบ → วันส่ง", "⑤ หลังรับสินค้า", "⑥ ปิดงวดเดือน"]
LANES = [("kitchen", ["ครัว /", "ฝ่ายวิเคราะห์"], 118), ("ss", ["ประสานงานขาย"], 78), ("branch", ["สาขา"], 170),
         ("wh", ["คลัง"], 70), ("acc", ["บัญชี / ปฏิบัติการ", "/ ผู้บริหาร"], 70)]
LANE_Y = {}
_y = TOP
for key, _n, h in LANES:
    LANE_Y[key] = (_y, _y + h)
    _y += h
H = _y + 4


def cx(c):
    return X0 + c * COLW + COLW / 2


def bound(c):                       # x of the boundary on the RIGHT of column c
    return X0 + (c + 1) * COLW


def box_h(lines):
    return 14 + LH * len(lines)


# ── the boxes: id → (lane, column, lines, y offset in lane or None = centred, kind) ──
B = {}


def box(bid, lane, col, lines, y=None, kind="step"):
    y0, y1 = LANE_Y[lane]
    h = box_h(lines)
    top = y0 + y if y is not None else (y0 + y1 - h) / 2
    w = BOXW if kind != "end" else BOXW + 12
    B[bid] = {"x": cx(col) - w / 2, "y": top, "w": w, "h": h, "lines": lines, "kind": kind, "col": col}


box("k_new", "kitchen", 0, ["ครัว: สร้างสูตรจากแม่แบบ", "แล้วส่งตรวจ"], y=6)
box("k_rel", "kitchen", 0, ["ฝ่ายวิเคราะห์: ตรวจ 14 ข้อ", "แก้ไข / ส่งกลับ → ประกาศใช้", "(ผู้บริหารรับทราบ)"], y=60)
box("k_ver", "kitchen", 5, ["ฝ่ายวิเคราะห์: สูตรเวอร์ชัน", "ที่มีผลเดือนนั้น (ขั้น 1)"])
box("s_price", "ss", 0, ["ส่งไฟล์ราคาซื้อจริง", "รายเดือนของไดกิโตะ กรุ๊ป"], y=22)
box("s_follow", "ss", 1, ["ติดตามสาขาที่ยังไม่ยืนยัน", "เปิดแก้ไขเมื่อสาขาแจ้ง", "พร้อมเหตุผล (บันทึก)"])
box("s_key", "ss", 3, ["ดาวน์โหลด Excel ใบสั่งซื้อ", "บันทึกเข้าระบบบัญชี", "แล้วทำเครื่องหมายคีย์แล้ว"])
box("s_claim", "ss", 4, ["พิจารณาเคลม", "อนุมัติ / ไม่อนุมัติ (เหตุผล)", "→ ปรับสต๊อกสาขา"])
box("s_close", "ss", 5, ["นำเข้ารายงานไดกิโตะ กรุ๊ป", "จับคู่รหัส · บันทึกรับครบ", "ยืนยันรับสินค้าครบ"])
box("b_slip", "branch", 1, ["ใบตัดเบิกรายวัน", "เบิกใช้ + ของเสีย (สาเหตุ)", "→ ยืนยัน (ล็อก)", "เบิกเพิ่มได้ในวันเดียวกัน"], y=32)
box("b_order", "branch", 2, ["สั่งสินค้าตามยอดแนะนำ", "เหตุผลเมื่อต่าง ±20%", "ส่งก่อน 13:15 (ล่าช้ามีค่าปรับ)"], y=111)
box("b_recv", "branch", 3, ["รับสินค้า: รับครบ / ไม่ครบ", "+ สาเหตุ → เคลมอัตโนมัติ", "ยืนยันตรวจสภาพ"], y=40)
box("b_var", "branch", 4, ["ผลต่างรายวันเทียบสูตร", "(ระบบคำนวณจากใบตัดเบิก)"], y=46.5)
box("b_count", "branch", 5, ["ตรวจนับสิ้นเดือน วันที่ 1–3", "ครบรายการบังคับ → ส่งผล", "เหตุผลผลต่าง (A / 20 อันดับ)"], y=40)
box("w_conf", "wh", 3, ["ยืนยันยอดส่ง + เหตุผล + PO", "ภายใน 14:00 ของวันตัดรอบ", "→ คลังจัดส่งแล้ว"])
box("a_ops", "acc", 4, ["ฝ่ายปฏิบัติการ: ตรวจสัญญาณ", "เบิกเกิน 20% ติดต่อกัน 3 วัน"])
box("a_end", "acc", 5, ["บัญชี: ใบสรุปตัดเบิก (Excel)", "บันทึกเข้าระบบบัญชี → ล็อกงวด", "ผู้บริหาร: สรุปผลต่างรายเดือน"], kind="end")

# the decision: centred in column 2 of the branch lane, level with the slip box
_slip = B["b_slip"]
D = {"cx": cx(2), "cy": _slip["y"] + _slip["h"] / 2, "hw": 58, "hh": 26, "lines": ["ยืนยันใบตัดเบิก", "วันนี้แล้ว?"]}


def mid(b, side):
    x, y, w, h = b["x"], b["y"], b["w"], b["h"]
    return {"top": (x + w / 2, y), "bottom": (x + w / 2, y + h), "left": (x, y + h / 2), "right": (x + w, y + h / 2)}[side]


# ── connectors: (points, dashed, label, label position (x, y, anchor), ends = boxes it may touch) ──
C = []


def line(pts, ends, dashed=False, label=None, lpos=None, arrow=True):
    C.append({"pts": pts, "dashed": dashed, "label": label, "lpos": lpos, "ends": set(ends), "arrow": arrow})


k_new, k_rel = B["k_new"], B["k_rel"]
line([mid(k_new, "bottom"), mid(k_rel, "top")], ["k_new", "k_rel"])
sp = B["s_price"]
line([mid(sp, "top"), mid(k_rel, "bottom")], ["s_price", "k_rel"], label="ตรวจ · นำเข้า", lpos=(cx(0) + 6, (sp["y"] + k_rel["y"] + k_rel["h"]) / 2 + 3, "start"))
sl, sf = B["b_slip"], B["s_follow"]
line([mid(sl, "top"), mid(sf, "bottom")], ["b_slip", "s_follow"], dashed=True, label="ยังไม่ยืนยัน / ขอแก้ไข",
     lpos=(cx(1) - 6, (sl["y"] + sf["y"] + sf["h"]) / 2 + 3, "end"))
# slip → decision; "ไม่" goes back over the top of the decision into the slip
line([mid(sl, "right"), (D["cx"] - D["hw"], D["cy"])], ["b_slip", "dec"])
y_no = LANE_Y["branch"][0] + 16
line([(D["cx"], D["cy"] - D["hh"]), (D["cx"], y_no), (sl["x"] + sl["w"] - 24, y_no), (sl["x"] + sl["w"] - 24, sl["y"])], ["dec", "b_slip"],
     label="ไม่: ยังส่งใบสั่งซื้อไม่ได้", lpos=(D["cx"] - 4, y_no - 4, "end"))
bo = B["b_order"]
line([(D["cx"], D["cy"] + D["hh"]), mid(bo, "top")], ["dec", "b_order"], label="ใช่", lpos=(D["cx"] + 6, (D["cy"] + D["hh"] + bo["y"]) / 2 + 3, "start"))
# order → Sales Support (keys it into accounting) and → warehouse (confirms), both through the boundary of column 2
xb = bound(2)
sk, wc = B["s_key"], B["w_conf"]
line([mid(bo, "right"), (xb, mid(bo, "right")[1]), (xb, mid(sk, "left")[1]), mid(sk, "left")], ["b_order", "s_key"])
line([(xb, mid(bo, "right")[1]), (xb, mid(wc, "left")[1]), mid(wc, "left")], ["b_order", "w_conf"])
br = B["b_recv"]
line([mid(wc, "top"), mid(br, "bottom")], ["w_conf", "b_recv"], label="ส่งของ", lpos=(cx(3) + 6, (wc["y"] + br["y"] + br["h"]) / 2 + 3, "start"))
xr = bound(3)
sc = B["s_claim"]
line([mid(br, "right"), (xr, mid(br, "right")[1]), (xr, mid(sc, "left")[1]), mid(sc, "left")], ["b_recv", "s_claim"],
     label="ส่วนต่าง / เคลมภายหลัง", lpos=(xr + 5, LANE_Y["branch"][0] + 13, "start"))
bv, ao, bc = B["b_var"], B["a_ops"], B["b_count"]
line([mid(bv, "bottom"), mid(ao, "top")], ["b_var", "a_ops"], dashed=True, label="สัญญาณเบิกเกิน", lpos=(cx(4) + 6, (LANE_Y["wh"][0] + LANE_Y["wh"][1]) / 2 + 3, "start"))
line([mid(bv, "right"), mid(bc, "left")], ["b_var", "b_count"])
scl, ae = B["s_close"], B["a_end"]
line([mid(scl, "bottom"), mid(bc, "top")], ["s_close", "b_count"], label="ขั้น 2–4, 7", lpos=(cx(5) + 6, (scl["y"] + scl["h"] + bc["y"]) / 2 + 3, "start"))
line([mid(bc, "bottom"), mid(ae, "top")], ["b_count", "a_end"], label="ขั้น 5–7 เสร็จ", lpos=(cx(5) + 6, (LANE_Y["wh"][0] + LANE_Y["wh"][1]) / 2 + 3, "start"))


# ── geometry check (no browser needed) ──
def rect_of(bid):
    if bid == "dec":
        return (D["cx"] - D["hw"], D["cy"] - D["hh"], 2 * D["hw"], 2 * D["hh"])
    b = B[bid]
    return (b["x"], b["y"], b["w"], b["h"])


def seg_hits(p, q, r, pad=2.0):
    """axis-aligned segment p-q against rectangle r (shrunk by pad, so touching an edge is not a hit)"""
    x, y, w, h = r[0] + pad, r[1] + pad, r[2] - 2 * pad, r[3] - 2 * pad
    (x1, y1), (x2, y2) = p, q
    if abs(x1 - x2) < 1e-6:                                  # vertical
        return x < x1 < x + w and max(min(y1, y2), y) < min(max(y1, y2), y + h)
    if abs(y1 - y2) < 1e-6:                                  # horizontal
        return y < y1 < y + h and max(min(x1, x2), x) < min(max(x1, x2), x + w)
    return True                                              # diagonal lines are not allowed at all


def geometry_problems():
    bad = []
    ids = list(B) + ["dec"]
    for i, a in enumerate(ids):                              # boxes must not touch each other
        ra = rect_of(a)
        for b_ in ids[i + 1:]:
            rb = rect_of(b_)
            if ra[0] < rb[0] + rb[2] + 4 and rb[0] < ra[0] + ra[2] + 4 and ra[1] < rb[1] + rb[3] + 4 and rb[1] < ra[1] + ra[3] + 4:
                bad.append(f"boxes too close: {a} / {b_}")
    for k, (key, _n, _h) in enumerate(LANES):                # boxes stay inside their lane
        y0, y1 = LANE_Y[key]
    for bid, b in B.items():
        lane = next(k for k, (y0, y1) in LANE_Y.items() if y0 <= b["y"] < y1)
        y0, y1 = LANE_Y[lane]
        if b["y"] < y0 + 4 or b["y"] + b["h"] > y1 - 4:
            bad.append(f"box {bid} leaves its lane")
        if b["x"] < X0 + b["col"] * COLW + 3 or b["x"] + b["w"] > X0 + (b["col"] + 1) * COLW - 3:
            bad.append(f"box {bid} leaves its column")
    for c in C:
        pts = c["pts"]
        for p, q in zip(pts, pts[1:]):
            for bid in ids:
                if bid in c["ends"]:
                    continue
                if seg_hits(p, q, rect_of(bid)):
                    bad.append(f"line {c['ends']} crosses {bid}")
    return bad


# ── SVG ──
def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def texts(x, ymid, lines, size=FS, weight=None, fill=INK, cls="bt"):
    y = ymid - (len(lines) - 1) * LH / 2 + size * 0.36
    w = f' font-weight="{weight}"' if weight else ""
    spans = "".join(f'<tspan x="{x:.1f}" y="{y + i * LH:.1f}">{esc(t)}</tspan>' for i, t in enumerate(lines))
    return f'<text class="{cls}" text-anchor="middle" font-size="{size}"{w} fill="{fill}">{spans}</text>'


def svg():
    o = [f'<svg viewBox="0 0 {W} {H:.0f}" xmlns="http://www.w3.org/2000/svg" font-family="Leelawadee UI, Tahoma, sans-serif">',
         f'<defs><marker id="ah" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="{NAVY}"/></marker></defs>']
    for c, name in enumerate(COLS):                                         # header
        o.append(f'<rect x="{X0 + c * COLW}" y="0" width="{COLW}" height="{HEAD_H}" fill="{NAVY}" stroke="#ffffff" stroke-width="1"/>')
        o.append(texts(cx(c), HEAD_H / 2, [name], size=9.6, weight="700", fill="#ffffff", cls="ht"))
    for i, (key, name, h) in enumerate(LANES):                              # lanes
        y0, y1 = LANE_Y[key]
        o.append(f'<rect data-lane="{key}" x="0" y="{y0}" width="{W}" height="{h}" fill="{"#f5f8fc" if i % 2 == 0 else "#ffffff"}" stroke="#b9c4d6"/>')
        o.append(f'<rect x="0" y="{y0}" width="{LABEL_W}" height="{h}" fill="#e8eef7" stroke="#b9c4d6"/>')
        o.append(texts(LABEL_W / 2, (y0 + y1) / 2, name, size=10, weight="700", fill=NAVY, cls="lt"))
    for c in range(1, 6):                                                   # column guides
        o.append(f'<line x1="{X0 + c * COLW}" y1="{TOP}" x2="{X0 + c * COLW}" y2="{H - 4}" stroke="#d6dde8" stroke-dasharray="3 3"/>')
    for i, c in enumerate(C):                                               # connectors under the boxes
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in c["pts"])
        dash = ' stroke-dasharray="4 3"' if c["dashed"] else ""
        mk = ' marker-end="url(#ah)"' if c["arrow"] else ""
        o.append(f'<polyline data-c="{i}" points="{pts}" fill="none" stroke="{NAVY}" stroke-width="1.2"{dash}{mk}/>')
    o.append(f'<polygon points="{D["cx"]},{D["cy"] - D["hh"]} {D["cx"] + D["hw"]},{D["cy"]} {D["cx"]},{D["cy"] + D["hh"]} {D["cx"] - D["hw"]},{D["cy"]}" '
             f'fill="#fff8ec" stroke="{AMBER}" stroke-width="1.3"/>')
    o.append(texts(D["cx"], D["cy"], D["lines"], cls="dt"))
    for bid, b in B.items():
        end = b["kind"] == "end"
        o.append(f'<rect data-box="{bid}" x="{b["x"]:.1f}" y="{b["y"]:.1f}" width="{b["w"]}" height="{b["h"]}" rx="{b["h"] / 2 if end else 5:.1f}" '
                 f'fill="{"#e8f4ec" if end else "#ffffff"}" stroke="{GREEN if end else NAVY}" stroke-width="1.3"/>')
        o.append(texts(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2, b["lines"]).replace('class="bt"', f'class="bt" data-in="{bid}"'))
    for i, c in enumerate(C):                                               # labels last, on a white backing
        if c["label"]:
            x, y, anchor = c["lpos"]
            o.append(f'<text class="lb" data-c="{i}" x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-size="8" fill="{AMBER}" '
                     f'paint-order="stroke" stroke="#ffffff" stroke-width="3" stroke-linejoin="round">{esc(c["label"])}</text>')
    o.append("</svg>")
    return "\n".join(o)


def browser_problems(svg_text):
    """text of every box and the decision must sit inside its shape; labels must not cover a box"""
    from playwright.sync_api import sync_playwright
    js = """() => { const out = [];
      const r = el => el.getBBox();
      const boxes = [...document.querySelectorAll('rect[data-box]')].map(e => ({id: e.dataset.box, b: r(e)}));
      for (const t of document.querySelectorAll('text[data-in]')) {
        const b = boxes.find(x => x.id === t.dataset.in).b, tb = r(t);
        if (tb.x < b.x + 3 || tb.x + tb.width > b.x + b.width - 3 || tb.y < b.y + 2 || tb.y + tb.height > b.y + b.height - 2)
          out.push('text does not fit: ' + t.dataset.in + ' ' + Math.round(tb.width) + '/' + Math.round(b.width));
      }
      const d = document.querySelector('text.dt').getBBox(), p = document.querySelector('polygon').getBBox();
      if (d.width > p.width * 0.62 || d.height > p.height * 0.75) out.push('decision text too big ' + Math.round(d.width));
      for (const t of document.querySelectorAll('text.ht')) { const tb = r(t); if (tb.width > 140) out.push('header too wide ' + t.textContent); }
      for (const t of document.querySelectorAll('text.lb')) { const tb = r(t);
        for (const x of boxes) { const b = x.b;
          if (tb.x < b.x + b.width && b.x < tb.x + tb.width && tb.y < b.y + b.height && b.y < tb.y + tb.height) out.push('label "' + t.textContent + '" covers ' + x.id); }
        if (tb.y < p.y + p.height && p.y < tb.y + tb.height && tb.x < p.x + p.width && p.x < tb.x + tb.width) out.push('label "' + t.textContent + '" covers the decision');
        // no OTHER connector may pass through the label (2 units of air around it)
        const L = {x: tb.x - 2, y: tb.y - 1, w: tb.width + 4, h: tb.height + 2};
        for (const pl of document.querySelectorAll('polyline[data-c]')) {
          if (pl.dataset.c === t.dataset.c) continue;
          const pts = pl.getAttribute('points').trim().split(/\\s+/).map(s => s.split(',').map(Number));
          for (let k = 0; k + 1 < pts.length; k++) {
            const [x1, y1] = pts[k], [x2, y2] = pts[k + 1];
            const hit = x1 === x2 ? (x1 > L.x && x1 < L.x + L.w && Math.max(Math.min(y1, y2), L.y) < Math.min(Math.max(y1, y2), L.y + L.h))
                                  : (y1 > L.y && y1 < L.y + L.h && Math.max(Math.min(x1, x2), L.x) < Math.min(Math.max(x1, x2), L.x + L.w));
            if (hit) out.push('label "' + t.textContent + '" touches line ' + pl.dataset.c);
          }
        }
        // labels must not touch each other
        for (const u of document.querySelectorAll('text.lb')) { if (u === t) continue; const ub = r(u);
          if (tb.x < ub.x + ub.width + 3 && ub.x < tb.x + tb.width + 3 && tb.y < ub.y + ub.height + 2 && ub.y < tb.y + tb.height + 2) out.push('labels touch: ' + t.textContent + ' / ' + u.textContent); }
        // a label stays inside the chart body (not on a lane border)
        const lanes = [...document.querySelectorAll('rect[data-lane]')].map(e => r(e));
        if (!lanes.some(b => tb.y >= b.y + 1 && tb.y + tb.height <= b.y + b.height - 1)) out.push('label "' + t.textContent + '" sits on a lane border');
      }
      return out; }"""
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        pg.set_content(f"<!doctype html><meta charset='utf-8'><body style='margin:0'>{svg_text}</body>")
        pg.wait_for_timeout(300)
        out = pg.evaluate(js)
        b.close()
    return out


if __name__ == "__main__":
    s = svg()
    probs = geometry_problems() + browser_problems(s)
    for p in probs:
        print("!", p)
    if probs:
        sys.exit(1)
    html = io.open(HTML, encoding="utf-8").read()
    new, n = re.subn(r'(<div class="landscape">\s*<h3[^>]*>[^<]*</h3>\s*)<svg .*?</svg>', lambda m: m.group(1) + s, html, count=1, flags=re.S)
    assert n == 1, "swim-lane block not found"
    io.open(HTML, "w", encoding="utf-8", newline="").write(new)
    print("swim lane written:", len(B), "boxes,", len(C), "connectors, 0 problems")
