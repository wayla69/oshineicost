# -*- coding: utf-8 -*-
# PART 1 — สถานการณ์ต้นทุนบุฟเฟ่ต์ (Buffet Cost Situation). Shares generator globals.

PAL = ["#13595A","#3E8385","#B0472D","#8A6D1F","#6E8A8B","#A9B7B7","#4A6A6B","#C9BDA0"]

# ---- 1.0 opener ----
page(); toc_sec("ส่วนที่ 1 · สถานการณ์ต้นทุนบุฟเฟ่ต์")
w('<div class="sec-no">ส่วนที่ 1</div>')
w('<h2>สถานการณ์ต้นทุน: เงินรั่วอยู่ที่ไหน</h2>')
w('<p class="lead">ก่อนลดต้นทุน ต้องรู้ว่าต้นทุน “อยู่ที่ไหน” อย่างแม่นยำ. ส่วนนี้ใช้ '
  '<b>ยอดขายรายเมนูจริง</b> จากระบบ ERP ช่วง ม.ค.–พ.ค. 2569 (26 สาขา) '
  'วิเคราะห์ <b>เฉพาะธุรกิจบุฟเฟ่ต์</b> — ต้นทุนอาหารที่ลูกค้าบริโภคในบุฟเฟ่ต์ เทียบกับรายได้แพ็กเกจบุฟเฟ่ต์ '
  '(ตัดเครื่องดื่ม/ค่าบริการ/à‑la‑carte ออก) เพื่อให้เห็นต้นทุนบุฟเฟ่ต์ที่แท้จริง.</p>')
toc_sub("1.1 ภาพรวม & เทียบมาตรฐาน")
w('<h3>1.1 ภาพรวมต้นทุนบุฟเฟ่ต์ และการเทียบมาตรฐาน</h3>')
stat_cards([
  {"n":m(REV5),"ncls":"teal","l":"รายได้แพ็กเกจบุฟเฟ่ต์ (5 ด.)","h":"ไม่รวม VAT"},
  {"n":m(COGS5),"ncls":"r","l":"ต้นทุนอาหารที่บริโภค","h":"ม.ค.–พ.ค. 2569"},
  {"n":f"{FOODPCT:.1f}%","ncls":"r","l":"ต้นทุน / รายได้บุฟเฟ่ต์","h":"สูงกว่ามาตรฐาน 20+ จุด"},
  {"n":f"฿{COST_PER_COVER:.0f}","ncls":"","l":"ต้นทุนอาหาร/หัว","h":f"รายได้ ฿{REV_PER_COVER:.0f}/หัว"},
])
w('<h4>ต้นทุนอาหารบุฟเฟ่ต์ของ Oshinei เทียบกรอบอุตสาหกรรม</h4>')
lo, hi = 25, 70
def _pos(v): return max(0, min(100, 100*(v-lo)/(hi-lo)))
w('<div style="margin:1.1em 0">')
w('<div style="position:relative;height:26px;border:1px solid var(--line);overflow:hidden;'
  'background:linear-gradient(90deg,#cfe3d9 0%,#cfe3d9 22%,#efe2c2 22%,#efe2c2 44%,#eeccbf 44%,#e3b3a6 100%)"></div>')
w(f'<div style="position:relative;height:0">'
  f'<div style="position:absolute;left:{_pos(FOODPCT):.1f}%;top:-44px;transform:translateX(-50%);'
  f'background:var(--ink);color:#fff;padding:3px 9px;font-size:11px;font-weight:700;white-space:nowrap">'
  f'Oshinei {FOODPCT:.1f}% ▾</div></div>')
w('<div style="display:flex;justify-content:space-between;font-size:10.5px;color:var(--muted);margin-top:6px">'
  '<span>28–35% ดีเยี่ยม</span><span>35–45% รับได้</span><span>45%+ ต้องแก้ไข</span></div>')
w('</div>')
w(f'<p>ที่ <b class="accent">{FOODPCT:.1f}%</b> ธุรกิจบุฟเฟ่ต์อยู่ในโซน “ต้องแก้ไข” — '
  'ไม่แปลกสำหรับบุฟเฟ่ต์ปลาดิบพรีเมียมที่ใช้แซลมอนเป็นจุดขาย แต่ <b>มีพื้นที่ปรับปรุงชัดเจน</b> '
  f'โดยเฉพาะเมื่อสาขาที่ดีที่สุดของเครือเองทำได้ถึง {BR_BEST["food_cost_pct"]:.0f}%. '
  f'เป้าหมายของแผนนี้คือดึงค่าเฉลี่ยเข้าสู่ ~55% ระยะแรก และ <b>50% เป็นเป้าหมายหลัก</b> โดยไม่ลดคุณค่าที่ลูกค้าสัมผัสได้.</p>')
w(f'<p class="small">หมายเหตุ: “{FOODPCT:.1f}%” คือต้นทุนอาหาร ÷ รายได้บุฟเฟ่ต์ล้วน. '
  f'หากรวมรายได้เครื่องดื่ม/ค่าบริการ (เป็นศูนย์กำไรแยกที่ช่วยอุดหนุน) สัดส่วนต่อรายได้รวมจะเท่ากับ {BLEND_FC:.1f}%.</p>')
endpage()

# ---- 1.2 category breakdown ----
page()
toc_sub("1.2 ต้นทุนกระจุกที่หมวดไหน")
w('<h3>1.2 ต้นทุนกระจุกตัวที่หมวดไหน</h3>')
w('<p>แยกต้นทุนอาหารบุฟเฟ่ต์ตามหมวดเมนู เห็นการกระจุกตัวชัดเจน: '
  f'<b>หมวดปลาดิบ (SASHIMI) กินต้นทุนถึง {SASHIMI_SHARE:.0f}%</b> และเมื่อรวม 4 หมวดแรก '
  '(ปลาดิบ + สเต็ก + ชาบู + ของดอง) ก็เกิน 65% ของต้นทุนบุฟเฟ่ต์ทั้งหมด.</p>')
cats = DATA["categories"][:7]
seg = []
for i, c in enumerate(cats):
    nm = c["category"].replace("Oshinei ", "").replace("Special ", "")
    seg.append((nm, c["total_cost"], PAL[i % len(PAL)]))
other = sum(c["total_cost"] for c in DATA["categories"][7:])
if other > 0: seg.append(("อื่นๆ", other, PAL[7]))
donut(seg)
w('<table><thead><tr><th>หมวดเมนู</th><th class="r">ต้นทุน 5 เดือน</th><th class="r">ต่อปี</th>'
  '<th class="r">% ต้นทุน</th><th class="r">เสิร์ฟ</th></tr></thead><tbody>')
cum = 0
for c in DATA["categories"][:9]:
    nm = c["category"].replace("Oshinei ", "").replace("Special ", "")
    cum += c["cost_share_pct"]
    hl = ' class="hl"' if c["cost_share_pct"] >= 10 else ''
    w(f'<tr{hl}><td>{esc(nm)}</td><td class="r">{m(c["total_cost"])}</td>'
      f'<td class="r">{m(c["total_cost"]*ANN)}</td><td class="r">{c["cost_share_pct"]:.1f}%</td>'
      f'<td class="r">{b(c["qty"])}</td></tr>')
w(f'<tr class="tot"><td>รวม 9 หมวดแรก</td><td class="r">—</td><td class="r">—</td>'
  f'<td class="r">{cum:.1f}%</td><td class="r">—</td></tr></tbody></table>')
w('<p class="tbl-cap">ตาราง 1.2 — ต้นทุนอาหารบุฟเฟ่ต์แยกตามหมวด. แถวที่แรเงา = เกิน 10% ของต้นทุน.</p>')
w('<div class="callout"><div class="ct">สิ่งที่ตารางนี้บอก</div>'
  '<p>การลดต้นทุนต้อง <b>โฟกัสที่ปลาดิบ</b> เป็นอันดับแรก. ความพยายามในหมวดเล็ก (สลัด โซบะ) '
  'แม้ทำได้ 100% ก็แทบไม่ขยับภาพรวม. กฎ 80/20 ใช้เต็มที่ — โฟกัส 4 หมวดแรกคือโฟกัสที่เงินจริง.</p></div>')
endpage()

# ---- 1.3 top cost drivers ----
page()
toc_sub("1.3 20 เมนูที่กินต้นทุนสูงสุด")
w('<h3>1.3 20 เมนูที่กินต้นทุนสูงสุด</h3>')
w('<p>ลงลึกระดับเมนู — นี่คือ 20 เมนูบุฟเฟ่ต์ที่สร้างต้นทุนรวมสูงสุด (เสิร์ฟ × ต้นทุน/หน่วย). '
  f'สังเกตว่า <b>“แซลมอนซาชิมิ 1 ชิ้น” รายการเดียวกินต้นทุน ~{SALMON_SASHIMI_SHARE:.0f}%</b> ของบุฟเฟ่ต์ทั้งหมด '
  'เพราะถูกเสิร์ฟกว่า 4.3 ล้านชิ้นใน 5 เดือน แม้ต้นทุน/ชิ้นเพียง ~฿12.8.</p>')
top = DATA["top_cost_drivers"][:20]
mx = top[0]["total_cost"]
w('<div class="bar-wrap">')
for i, it in enumerate(top[:12]):
    pct = 100*it["total_cost"]/mx
    acc = ' acc' if i == 0 else ''
    w(f'<div class="bar-row{acc}"><div class="lab">{clean(it["description"])[:24]}</div>'
      f'<div class="bar-track"><div class="bar-fill" style="width:{max(3,pct):.1f}%"></div></div>'
      f'<div class="bar-val">{m(it["total_cost"])}</div></div>')
w('</div>')
w('<table><thead><tr><th>#</th><th>เมนู</th><th class="r">เสิร์ฟ</th><th class="r">฿/หน่วย</th>'
  '<th class="r">ต้นทุนรวม</th><th class="r">%</th></tr></thead><tbody>')
for i, it in enumerate(top, 1):
    hl = ' class="hl"' if i <= 3 else ''
    w(f'<tr{hl}><td>{i}</td><td>{clean(it["description"])}</td><td class="r">{b(it["qty"])}</td>'
      f'<td class="r">฿{it["unit_cost"]:.1f}</td><td class="r">{m(it["total_cost"])}</td>'
      f'<td class="r">{it["cost_share_pct"]:.1f}%</td></tr>')
w('</tbody></table>')
w('<p class="tbl-cap">ตาราง 1.3 — 20 เมนูบุฟเฟ่ต์ต้นทุนสูงสุด. (ตารางเต็ม 60 อันดับในภาคผนวก ก.)</p>')
endpage()

# ---- 1.4 salmon deep dive ----
page()
toc_sub(f"1.4 เจาะลึกแซลมอน {SALMON_SHARE:.0f}%")
w(f'<h3>1.4 เจาะลึก: แซลมอน = {SALMON_SHARE:.0f}% ของต้นทุนบุฟเฟ่ต์</h3>')
salc = FAM["salmon"]["cost"]
w('<p>รวมทุกเมนูที่มีแซลมอนเป็นวัตถุดิบหลัก (ซาชิมิ ดอง ยำ สเต็ก ย่าง ค็อกเทล ชุดรวม) '
  f'แซลมอนกินต้นทุน <b>{m(salc)} ใน 5 เดือน (~{m(salc*ANN)}/ปี)</b> = '
  f'<b class="accent">{SALMON_SHARE:.0f}% ของต้นทุนบุฟเฟ่ต์ทั้งหมด</b>. '
  f'บวกกุ้งอีก {SHRIMP_SHARE:.0f}% → วัตถุดิบ 2 ตระกูลกินต้นทุนกว่าครึ่ง.</p>')
fam = [("แซลมอน (ทุกเมนู)", SALMON_SHARE, "#B0472D"), ("กุ้ง (ทุกเมนู)", SHRIMP_SHARE, "#8A6D1F"),
       ("ทุเรียน", FAM["durian"]["share"], "#1F7A4D"), ("ทูน่า", FAM["tuna"]["share"], "#3E8385"),
       ("วัตถุดิบอื่นๆ", round(100-SALMON_SHARE-SHRIMP_SHARE-FAM["durian"]["share"]-FAM["tuna"]["share"],1), "#13595A")]
donut([(n, v, c) for n, v, c in fam])
w('<h4>โครงสร้างต้นทุนแซลมอน — แยกตามประเภทเมนู</h4>')
sal = [it for it in (DATA["buffet_items"]+DATA["top_cost_drivers"]) if "แซลมอ" in it["description"]]
seen = {}; [seen.setdefault(it["sku_code"], it) for it in sal]
sal = sorted(seen.values(), key=lambda x: -x["total_cost"])[:8]
w('<table><thead><tr><th>เมนูแซลมอน</th><th class="r">เสิร์ฟ</th><th class="r">฿/หน่วย</th>'
  '<th class="r">ต้นทุน 5 ด.</th><th class="c">ประเภท</th></tr></thead><tbody>')
for it in sal:
    typ = ("ดิบ (โชว์)" if it["unit_cost"] < 25 and "ดอง" not in it["description"]
           else ("ดอง/ยำ" if ("ดอง" in it["description"] or "ยำ" in it["description"]) else "ปรุงสุก"))
    w(f'<tr><td>{clean(it["description"])}</td><td class="r">{b(it["qty"])}</td>'
      f'<td class="r">฿{it["unit_cost"]:.1f}</td><td class="r">{m(it["total_cost"])}</td>'
      f'<td class="c"><span class="tag s">{typ}</span></td></tr>')
w('</tbody></table>')
w('<div class="callout s"><div class="ct">นัยเชิงกลยุทธ์</div>'
  '<p>แซลมอน <b>ซาชิมิดิบ</b> (ต้นทุน/ชิ้นต่ำแต่ปริมาณมหาศาล) ต้องแก้ที่ <b>วิธีซื้อ–วิธีตัด (yield)</b> '
  '— ดูส่วนที่ 4. ส่วนแซลมอน <b>ดอง/ยำ/ปรุงสุก</b> (ต้นทุน/หน่วยสูง) คือเป้าหมายของการ <b>ดัดแปลงเมนู</b> '
  '— ดูส่วนที่ 3 ซึ่งเป็นหัวใจของรายงาน.</p></div>')
endpage()

# ---- 1.5 branch variance ----
page()
toc_sub(f"1.5 ความต่างระหว่างสาขา {BR_SPREAD} จุด")
w(f'<h3>1.5 ความแตกต่างระหว่างสาขา: ช่องว่าง {BR_SPREAD} จุด</h3>')
w(f'<p>ต้นทุนอาหารบุฟเฟ่ต์ต่อรายได้ของแต่ละสาขาต่างกันถึง <b>~{BR_SPREAD} จุด</b> — '
  f'จาก <b>{BR_BEST["food_cost_pct"]:.0f}% ({esc(BR_BEST["branch"])})</b> ถึง '
  f'<b>{BR_WORST["food_cost_pct"]:.0f}% ({esc(BR_WORST["branch"])})</b>. '
  'ทุกสาขาใช้สูตร/เมนูเดียวกัน — ความต่างนี้สะท้อน <b>วินัยการคุมพอร์ชัน การตัด และของเสีย</b> '
  'มากกว่าตัวสูตร จึงเป็นโอกาสโค้ชชิ่งที่ชัดเจน. (สาขาที่ดีที่สุดพิสูจน์ว่า “ทำได้”)</p>')
brs = DATA["branches"]
worst = brs[:7]; best = brs[-5:]
w('<div class="two">')
w('<div><h4 class="redc">7 สาขาต้นทุนสูงสุด</h4><table><thead><tr><th>สาขา</th>'
  '<th class="r">ต้นทุน%</th><th class="c">ประเภท</th></tr></thead><tbody>')
for x in worst:
    w(f'<tr><td>{esc(x["branch"])}</td><td class="r neg">{x["food_cost_pct"]:.1f}%</td>'
      f'<td class="c small">{"แม่" if x["branch_type"]=="master" else "แฟรนไชส์"}</td></tr>')
w('</tbody></table></div>')
w('<div><h4 class="greenc">5 สาขาต้นทุนต่ำสุด</h4><table><thead><tr><th>สาขา</th>'
  '<th class="r">ต้นทุน%</th><th class="c">ประเภท</th></tr></thead><tbody>')
for x in best:
    w(f'<tr><td>{esc(x["branch"])}</td><td class="r pos">{x["food_cost_pct"]:.1f}%</td>'
      f'<td class="c small">{"แม่" if x["branch_type"]=="master" else "แฟรนไชส์"}</td></tr>')
w('</tbody></table></div></div>')
med = sorted(x["food_cost_pct"] for x in brs)[len(brs)//2]
w(f'<p>ค่ากลาง (median) ของเครืออยู่ที่ราว <b>{med:.0f}%</b>. หากดึงสาขาที่สูงผิดปกติเข้าใกล้ค่ากลาง '
  'จะประหยัด ~฿7–12 ล้าน/ปี โดยไม่ต้องแตะสูตรหรือเมนูเลย — เป็นเพียงวินัยปฏิบัติการ.</p>')
w('<div class="callout g"><div class="ct">ข้อสังเกตเรื่องกำลังซื้อรายภาค</div>'
  '<p>สาขาต้นทุนสูงหลายแห่งอยู่ในจังหวัดกำลังซื้อปานกลาง ทำให้รายได้/หัวต่ำลงแต่ต้นทุนวัตถุดิบเท่าเดิม '
  '→ ต้นทุน% จึงสูง. แก้ได้ทั้งฝั่งต้นทุน (พอร์ชัน/ของเสีย) และฝั่งราคา (tier ตามกำลังซื้อรายภาค — ดูส่วนที่ 8).</p></div>')
endpage()

# ---- 1.6 cost per head ----
page()
toc_sub("1.6 ต้นทุนต่อหัว (per-cover)")
w('<h3>1.6 ต้นทุนต่อหัว (per-cover) — เงินที่ลูกค้า 1 คน “กิน”</h3>')
PH_REV = REV_PER_COVER; PH_COST = COST_PER_COVER; PH_GM = PH_REV - PH_COST
w(f'<p>ลูกค้าบุฟเฟ่ต์ 1 คนสร้างรายได้แพ็กเกจเฉลี่ย <b>฿{PH_REV:.0f}/หัว</b> '
  f'แต่ “กิน” ต้นทุนอาหารเฉลี่ย <b class="accent">฿{PH_COST:.0f}/หัว</b> '
  f'เหลือกำไรขั้นต้น (ก่อนค่าแรง/ค่าเช่า/ค่าไฟ) เพียง <b>฿{PH_GM:.0f}/หัว ({PH_GM/PH_REV*100:.0f}%)</b>. '
  'ทุกบาทที่ลดต้นทุน/หัวได้ คือกำไรที่ไหลลงบรรทัดสุดท้ายเกือบเต็มจำนวน.</p>')
stat_cards([
  {"n":f"฿{PH_REV:.0f}","ncls":"teal","l":"รายได้/หัว","h":"แพ็กเกจบุฟเฟ่ต์ (ไม่รวม VAT)"},
  {"n":f"฿{PH_COST:.0f}","ncls":"r","l":"ต้นทุนอาหาร/หัว","h":f"{FOODPCT:.1f}% ของรายได้"},
  {"n":f"฿{PH_GM:.0f}","ncls":"green","l":"กำไรขั้นต้น/หัว","h":"ก่อนค่าแรง/เช่า/ไฟ"},
  {"n":f"{CV['covers']/1e6:.2f}M","ncls":"","l":"ลูกค้า 5 เดือน","h":f"~{COVERS_Y/1e6:.1f}M/ปี"},
])
w('<h4>ต้นทุน/หัว แตกตามตระกูลวัตถุดิบ — ฿407 หายไปไหน</h4>')
fam_ph = [
  ("เทราต์/“แซลมอน” (ทุกเมนู)", SALMON_SHARE, "#B0472D"),
  ("กุ้ง (ทุกเมนู)", SHRIMP_SHARE, "#8A6D1F"),
  ("ทุเรียน", FAM["durian"]["share"], "#1F7A4D"),
  ("ทูน่า", FAM["tuna"]["share"], "#3E8385"),
  ("วัตถุดิบอื่นๆ", round(100-SALMON_SHARE-SHRIMP_SHARE-FAM["durian"]["share"]-FAM["tuna"]["share"],1), "#13595A"),
]
w('<table><thead><tr><th>ตระกูลวัตถุดิบ</th><th class="r">% ต้นทุน</th>'
  '<th class="r">฿/หัว</th><th class="r">฿/หัว/ปี (×ลูกค้า)</th></tr></thead><tbody>')
for nm,sh,_ in fam_ph:
    ph = PH_COST*sh/100
    w(f'<tr><td>{nm}</td><td class="r">{sh:.1f}%</td><td class="r neg">฿{ph:.0f}</td>'
      f'<td class="r">{m(ph*COVERS_Y)}</td></tr>')
w(f'<tr class="tot"><td>รวมต้นทุนอาหาร/หัว</td><td class="r">100%</td>'
  f'<td class="r">฿{PH_COST:.0f}</td><td class="r">{m(COGS_Y)}</td></tr></tbody></table>')
w('<div class="callout s"><div class="ct">นัยสำคัญ</div>'
  f'<p>ลูกค้า 1 คน “กิน” ตระกูลปลาเนื้อส้ม (เทราต์/แซลมอน) ถึง <b>~฿{PH_COST*SALMON_SHARE/100:.0f}/หัว</b> — '
  'มากกว่าทุกตระกูลรวมกันที่เหลือเกือบเท่าตัว. การลดต้นทุน/หัวจึงต้องเริ่มที่ปลาเนื้อส้ม '
  '(วิธีซื้อ-ตัด + ดัดแปลงเมนู) เป็นอันดับแรกเสมอ. เป้าหมาย: ดึง ฿'
  f'{PH_COST:.0f}/หัว ลงสู่ ~฿{REV_PER_COVER*0.50:.0f}/หัว (ต้นทุน 50%).</p></div>')
endpage()

# ---- 1.7 pareto of consumption ----
page()
toc_sub("1.7 Pareto การกินรายเมนู (80/20)")
w('<h3>1.7 Pareto การกินรายเมนู — 13% ของเมนู = 80% ของต้นทุน</h3>')
_items = sorted(DATA["buffet_items"], key=lambda x:-x["total_cost"])
_N = len(_items); _tot = sum(i["total_cost"] for i in _items)
def _milestone(frac):
    cum=0
    for i,it in enumerate(_items,1):
        cum+=it["total_cost"]
        if cum/_tot>=frac: return i
    return _N
m50=_milestone(0.5); m80=_milestone(0.8); m90=_milestone(0.9)
w(f'<p>เมนูบุฟเฟ่ต์มีทั้งหมด <b>{_N} รายการ</b> แต่ต้นทุนกระจุกตัวสุดขั้วแบบ Pareto: '
  f'<b>เพียง {m50} เมนูแรก ({m50/_N*100:.0f}%) = 50% ของต้นทุน</b> และ '
  f'<b class="accent">{m80} เมนูแรก ({m80/_N*100:.0f}%) = 80% ของต้นทุน</b>. '
  f'นี่ “ชันกว่า” กฎ 80/20 ปกติ — แปลว่าการดัดแปลงเพียงไม่กี่สิบเมนูหัวแถวก็ขยับภาพรวมได้จริง.</p>')
# cumulative bar by milestones
w('<table><thead><tr><th>กลุ่มเมนู (เรียงต้นทุนมาก→น้อย)</th><th class="r">จำนวนเมนู</th>'
  '<th class="r">% ของเมนู</th><th class="r">ต้นทุนสะสม</th><th class="r">% ต้นทุนสะสม</th></tr></thead><tbody>')
prev=0; cumc=0
for label,k in [("Top 5",5),("Top 12",12),("Top 20",20),(f"Top {m80}",m80),("Top 60",60),(f"ทั้งหมด {_N}",_N)]:
    cumc=sum(it["total_cost"] for it in _items[:k])
    hl=' class="hl"' if k==m80 else ''
    w(f'<tr{hl}><td>{label}</td><td class="r">{k}</td><td class="r">{k/_N*100:.0f}%</td>'
      f'<td class="r">{m(cumc)}</td><td class="r">{cumc/_tot*100:.0f}%</td></tr>')
w('</tbody></table>')
w('<h4>ต้นทุน/หัว ของ “หัวขบวน Pareto” (฿ ที่ลูกค้า 1 คนกินต่อเมนู)</h4>')
w('<table><thead><tr><th>#</th><th>เมนู</th><th class="r">฿/หัว</th><th class="r">% ต้นทุน</th>'
  '<th class="c">เป้าดัดแปลง?</th></tr></thead><tbody>')
_xform_keys = ["แซลมอน ซาชิมิ","แซลมอนดอง","ทุเรียน","กุ้งสด (5","ชุดปลาดิบรวม","ยำแซลมอนน้ำปลาร้า",
               "กุ้งสดดอง","กุ้งอาเจนติน่า","ซูพรีม อูนิ","ปลาฮามาจิ"]
for i,it in enumerate(_items[:10],1):
    ph = it["total_cost"]/CV["covers"]
    is_x = any(k in it["description"] for k in _xform_keys)
    tag = '<span class="tag green">มี</span>' if is_x else '<span class="tag">yield/พอร์ชัน</span>'
    w(f'<tr><td>{i}</td><td>{clean(it["description"])}</td><td class="r neg">฿{ph:.1f}</td>'
      f'<td class="r">{it["cost_share_pct"]:.1f}%</td><td class="c">{tag}</td></tr>')
w('</tbody></table>')
w('<p class="tbl-cap">ตาราง 1.7 — “฿/หัว” = ต้นทุนเมนูนั้นทั้งเครือ ÷ จำนวนลูกค้า. '
  'แซลมอนซาชิมิชิ้นเดียวกิน ~฿69/หัว — มากที่สุดในเมนูทั้งหมด.</p>')
w('<div class="callout green"><div class="ct">ทำไม Pareto นี้คือ “ข่าวดี”</div>'
  f'<p>เพราะต้นทุน 80% มาจากแค่ {m80} เมนู การโฟกัส <b>20 เมนูดัดแปลง (ส่วนที่ 3)</b> '
  'จึงครอบคลุม “หัวขบวน” ของต้นทุนได้เกือบทั้งหมด — ไม่ต้องยุ่งกับเมนูปลายแถวนับร้อยที่แทบไม่มีผล. '
  'ส่วนถัดไป (3.6) จำลองว่า “ถ้าเพิ่ม 20 เมนูใหม่แล้ว Pareto/ต้นทุนต่อหัวจะขยับอย่างไร พร้อมแถบความน่าจะเป็น P5–P95”.</p></div>')
endpage()
