# -*- coding: utf-8 -*-
# APPENDICES — ภาคผนวก (data tables, methodology, sources)

# ---- Appendix A: full top-60 cost drivers ----
def cost_table(items, start):
    w('<table><thead><tr><th>#</th><th>เมนู</th><th>หมวด</th><th class="r">เสิร์ฟ</th>'
      '<th class="r">฿/หน่วย</th><th class="r">ต้นทุนรวม</th><th class="r">%%</th></tr></thead><tbody>')
    for i,it in enumerate(items, start):
        cat=(it.get("category") or "").replace("Oshinei ","").replace("Special ","")
        w(f'<tr><td>{i}</td><td>{clean(it["description"])}</td><td class="small">{esc(cat)}</td>'
          f'<td class="r">{b(it["qty"])}</td><td class="r">฿{it["unit_cost"]:.1f}</td>'
          f'<td class="r">{m(it["total_cost"])}</td><td class="r">{it["cost_share_pct"]:.1f}%</td></tr>')
    w('</tbody></table>')

# ---- Appendix (assumptions & limitations — read first) ----
page(); toc_sec("ภาคผนวก")
toc_sub("สมมุติฐานและข้อจำกัด (อ่านก่อน)")
w('<div class="kicker">ภาคผนวก — อ่านก่อน</div>')
w('<h2>สมมุติฐานและข้อจำกัดของการวิเคราะห์</h2>')
w('<p class="lead">เพื่อความโปร่งใส ตารางนี้รวม “สิ่งที่เป็นข้อมูลจริง” กับ “สิ่งที่เป็นการประเมิน/ข้อเสนอ” '
  'ไว้จุดเดียว ควรอ่านประกอบทุกตัวเลขในรายงาน.</p>')
w('<table><thead><tr><th style="width:26%">หัวข้อ</th><th class="c" style="width:14%">สถานะ</th>'
  '<th>รายละเอียด / ข้อควรระวัง</th></tr></thead><tbody>')
_asm = [
 ("ต้นทุนต่อหน่วย/ยอดขายรายเมนู","ข้อมูลจริง","ดึงจากระบบ ERP ม.ค.–พ.ค. 2569 (26 สาขา); บางรายการต้นทุนเป็นค่าที่เจ้าของบันทึก/ประเมิน อาจไม่อัปเดตทุกเดือน.","green"),
 ("ชนิดปลา = เทราต์","ข้อมูลจัดซื้อ","วัตถุดิบจริงเป็นเทราต์ (ดู 4.5); ชื่อเมนู/ERP ยังเป็น “แซลมอน”.","green"),
 ("ส่วนลดเทราต์ ~10–20%","ประเมิน (ตลาด)","ค่าช่วงตลาด ขึ้นกับฤดู/แหล่ง/ผู้นำเข้า — ต้องยืนยันกับใบเสนอราคาจริง.","g"),
 ("ราคาปลา 2024–2026","ประเมิน (สาธารณะ)","ค่ารายครึ่งปี ประกอบจากแหล่งสาธารณะ (NSC/Fish Pool/SSB) ไม่ใช่ feed สด.","g"),
 ("ราคา 2H2026–1H2027","Forecast","ค่าคาดการณ์ตามมุมมองซัพพลายตึงตัว — มีความไม่แน่นอนสูง.","red"),
 ("BOM 20 เมนู (สูตรเสนอ)","ข้อเสนอ","ปริมาณ/ต้นทุนเป็นค่าออกแบบ ต้องยืนยันด้วยครัวทดสอบ (test-kitchen costing) ก่อนใช้จริง.","g"),
 ("ประหยัด/ปี","ประเมินช่วง","ฐาน 5 เดือน ×2.4; เป็นช่วงอนุรักษนิยม–บวก ไม่ใช่การรับประกันผล.","g"),
 ("yield 55%→90%","ประเมิน (วิจัย)","มาจากการซื้อ saku/FAS; <b>pre-rigor ไม่ได้เพิ่ม yield</b> (ช่วยล็อกต้นทุน/ลด gaping/คงความสด).","g"),
 ("ความเสี่ยง pre-rigor","วิจัย salmonid","contraction/thaw-rigor เป็นความเสี่ยงจริง ต้องคุมที่ครัวกลาง+นำร่องวัดผล (ดู 4.3).","red"),
 ("Monte Carlo / P5–P95","แบบจำลอง","triangular, seed คงที่ (ทำซ้ำได้); สมมุติฐาน adoption/realization/leakage/overlap/exec ปรับได้เมื่อมีข้อมูลนำร่อง. P95 = เปอร์เซ็นไทล์ 95.","g"),
 ("ต้นทุน/หัว & Pareto","ข้อมูลจริง (รวม)","เป็นค่ารวม 5 เดือน ÷ จำนวนลูกค้า; ไม่มี time-series รายเดือน — “แนวโน้ม” = เทียบก่อน/หลังเพิ่มเมนู.","green"),
]
for h,st,de,cl in _asm:
    w(f'<tr><td><b>{h}</b></td><td class="c"><span class="tag {cl}">{st}</span></td>'
      f'<td class="small">{de}</td></tr>')
w('</tbody></table>')
w('<div class="callout"><div class="ct">สรุป</div>'
  '<p>ตัวเลข <b>ต้นทุน/ยอดขายเป็นข้อมูลจริงจากระบบ</b>; ส่วน <b>ราคาปลาในอนาคต, ส่วนลดเทราต์, '
  'สูตร BOM, และแถบ P-value</b> เป็น “การประเมิน/ข้อเสนอ” ที่ออกแบบให้ตรวจสอบและปรับได้ '
  '— ควร <b>นำร่อง + วัดผลจริงก่อนขยาย</b> เสมอ.</p></div>')
endpage()

# ---- Appendix A: full top-60 cost drivers ----
page()
toc_sub("ก — 60 เมนูต้นทุนสูงสุด")
w('<div class="kicker">ภาคผนวก ก</div>')
w('<h2>60 เมนูที่กินต้นทุนสูงสุด (เต็ม)</h2>')
w('<p class="small">ม.ค.–พ.ค. 2569 · source=Easy · ไม่รวมรายการ tier บุฟเฟ่ต์ (นับต้นทุนที่ระดับรายการ). '
  'อันดับ 1–30.</p>')
cost_table(DATA["top_cost_drivers"][:30], 1)
endpage()

page()
w('<div class="kicker">ภาคผนวก ก (ต่อ)</div>')
w('<h2>60 เมนูต้นทุนสูงสุด — อันดับ 31–60</h2>')
cost_table(DATA["top_cost_drivers"][30:60], 31)
w('<p class="small">รวม 60 อันดับแรกคิดเป็นสัดส่วนใหญ่ของต้นทุนอาหารทั้งเครือ — สะท้อนการกระจุกตัวที่ปลาดิบ/ซีฟู้ด.</p>')
endpage()

# ---- Appendix B: category table ----
page()
w('<div class="kicker">ภาคผนวก ข</div>')
w('<h2>ต้นทุนแยกตามหมวดเมนู (เต็ม)</h2>')
w('<table><thead><tr><th>หมวด</th><th class="r">ต้นทุน 5 ด.</th><th class="r">ต่อปี</th>'
  '<th class="r">%% ต้นทุน</th><th class="r">เสิร์ฟ</th><th class="r">#เมนู</th></tr></thead><tbody>')
for c in DATA["categories"]:
    if c["total_cost"] < 1000: continue
    nm=c["category"].replace("Oshinei ","").replace("Special ","")
    w(f'<tr><td>{esc(nm)}</td><td class="r">{m(c["total_cost"])}</td><td class="r">{m(c["total_cost"]*ANN)}</td>'
      f'<td class="r">{c["cost_share_pct"]:.1f}%</td><td class="r">{b(c["qty"])}</td><td class="r">{c["n"]}</td></tr>')
w('</tbody></table>')
w('<p class="small">หมายเหตุ: หมวด Special XXX = เมนูเฉพาะสาขา/ภูมิภาค. ต้นทุนรวมไม่ตรง 100% พอดี '
  'เพราะปัดเศษและการจัดหมวดของบางรายการ.</p>')

# ---- Appendix C: branch table ----
w('<div class="kicker" style="margin-top:1.2em">ภาคผนวก ค</div>')
w('<h2 style="font-size:21px">ต้นทุนอาหาร %% แยกตามสาขา (ทั้งหมด)</h2>')
w('<table><thead><tr><th>สาขา</th><th class="c">ประเภท</th><th class="r">ยอดขาย 5 ด.</th>'
  '<th class="r">ต้นทุน</th><th class="r">ต้นทุน %%</th></tr></thead><tbody>')
for x in DATA["branches"]:
    cls="neg" if x["food_cost_pct"]>=57 else ("pos" if x["food_cost_pct"]<=52 else "")
    w(f'<tr><td>{esc(x["branch"])}</td><td class="c small">{"แม่" if x["branch_type"]=="master" else "แฟรนไชส์"}</td>'
      f'<td class="r">{m(x["rev"])}</td><td class="r">{m(x["cogs"])}</td>'
      f'<td class="r {cls}">{x["food_cost_pct"]:.1f}%</td></tr>')
w('</tbody></table>')
w('<p class="small">แดง = ≥57% (สูงกว่าค่าเฉลี่ยเครือ) · เขียว = ≤52% (ดีกว่าค่าเฉลี่ย). '
  'โคราช2 ไม่แสดง (ปิดดำเนินการตั้งแต่ ธ.ค. 2568).</p>')
endpage()

# ---- Appendix D: methodology ----
page()
w('<div class="kicker">ภาคผนวก ง</div>')
w('<h2>ระเบียบวิธีและข้อจำกัดของข้อมูล</h2>')
w('<h4>แหล่งข้อมูลและช่วงเวลา</h4>')
w('<ul>'
  '<li><b>ฐานข้อมูล:</b> oshinei.db (ระบบ ERP ของบริษัท) — ตาราง sales_sku_monthly (ยอดขายรายเมนู/สาขา/เดือน), '
  'sku_effective_cost (ต้นทุนต่อหน่วย), sales_transaction (บิล/ลูกค้า).</li>'
  '<li><b>ช่วงวิเคราะห์หลัก:</b> ม.ค.–พ.ค. 2569 (5 เดือนเต็มที่ข้อมูลครบ 25–26 สาขา) — '
  'เลือกช่วงนี้เพราะ มิ.ย. 2569 ยังไม่จบเดือน และก่อน ม.ค. มีสาขาน้อยกว่า.</li>'
  '<li><b>การทำเป็นรายปี:</b> ตัวเลข “ต่อปี” = ×2.4 (12÷5) จากฐาน 5 เดือน — เป็นค่าประเมินเชิงเส้น '
  'อาจคลาดเคลื่อนตามฤดูกาล.</li></ul>')
w('<h4>การคำนวณต้นทุน (COGS)</h4>')
w('<ul>'
  '<li>ใช้สูตรเดียวกับระบบบัญชีของบริษัท (_unified_cogs_expr): ต้นทุน = จำนวนเสิร์ฟ × ต้นทุนต่อหน่วย '
  '(จากตารางต้นทุน sku_cost ก่อน, สำรองด้วยสูตร BoM).</li>'
  '<li><b>รายการ tier บุฟเฟ่ต์</b> (BUFFET350/500/590/799/CHILD) ถูกตั้งต้นทุน = 0 '
  'เพราะการบริโภคถูกบันทึกที่ระดับรายการ (B0xxx) แล้ว — เพื่อไม่นับซ้ำ.</li>'
  '<li><b>รายได้บุฟเฟ่ต์</b> = รายได้แพ็กเกจ (รายการ tier) ไม่รวม VAT; '
  f'<b>ต้นทุนอาหารบุฟเฟ่ต์ {FOODPCT:.1f}%</b> = ต้นทุนอาหารที่บริโภค ÷ รายได้แพ็กเกจบุฟเฟ่ต์.</li>'
  f'<li><b>“แซลมอน {SALMON_SHARE:.0f}%”</b> = ผลรวมต้นทุนทุกเมนูที่ชื่อมีคำว่า “แซลมอ/แซลมอล” '
  'หารด้วยต้นทุนอาหารบุฟเฟ่ต์ทั้งหมด.</li>'
  '</ul>')
w('<h4>ข้อจำกัดที่ควรทราบ</h4>')
w('<ul>'
  '<li>ต้นทุนต่อหน่วยบางรายการเป็นค่าที่เจ้าของบันทึก/ประเมิน อาจไม่อัปเดตทุกเดือน.</li>'
  '<li>รายงานนี้วิเคราะห์ <b>เฉพาะธุรกิจบุฟเฟ่ต์</b> — จำแนกอาหารบุฟเฟ่ต์จากพฤติกรรมรายได้ '
  '(เสิร์ฟแต่รายได้ ≈ 0 = บริโภคในบุฟเฟ่ต์) และตัดหมวดเครื่องดื่ม/ค่าบริการ/เดลิเวอรี/à‑la‑carte ออก.</li>'
  '<li>ตัวเลขประหยัด/ปี เป็น <b>ค่าประเมินช่วง</b> (อนุรักษนิยม–บวก) ไม่ใช่การรับประกันผล '
  'ขึ้นกับการบังคับใช้ ราคาวัตถุดิบ และการตอบรับของลูกค้า. ควรนำร่อง+วัดผลจริงก่อนขยาย.</li>'
  '<li>yield 55%→90% อ้างอิงงานวิจัยอุตสาหกรรม (whole vs saku) — ผลจริงขึ้นกับซัพพลายเออร์/วิธีตัดของแต่ละสาขา. '
  'หมายเหตุ: <b>pre-rigor ไม่ได้เพิ่ม yield</b> แต่ช่วยล็อกต้นทุน/ลด gaping/คงความสด (ดูส่วนที่ 4.3).</li>'
  '</ul>')
endpage()

# ---- Appendix E: sources ----
page()
w('<div class="kicker">ภาคผนวก จ</div>')
w('<h2>แหล่งอ้างอิงภายนอก (เทรนด์/ราคา/รีวิว)</h2>')
w('<p class="small">รวบรวมจากการค้นข้อมูลเว็บ มิ.ย. 2569. บางตัวเลขเป็นค่าประเมิน/แหล่งเดียว ได้กำกับในเนื้อหา.</p>')
src_groups = [
 ("ขนาดตลาด & อุตสาหกรรม (ส่วนที่ 7.1)", [
   "Kasikorn Research — Restaurant Industry 2025, ฿646bn/+2.8% (kasikornresearch.com, IAO132)",
   "SCB EIC — ตลาดร้านอาหาร ฿674bn (2025) / ฿697bn (2026) (Nation Thailand 40057028)",
   "JETRO — Japanese restaurants survey: 5,916 (2024) → 5,781 (2025) (jetro.go.jp/_551667)",
   "Nation Thailand — kaiten sushi ~฿8bn; ร้านปิด/นิติบุคคล −43% (40062777, 40051294)",
   "Bangkok Post — shabu/suki segment ฿35–40bn (3201900)",
 ]),
 ("คู่แข่ง & ผลประกอบการ (ส่วนที่ 7.2–7.3)", [
   "Thairath — Sushiro Thailand FY2025 รายได้ ฿4.73bn/+63%, กำไร ฿728m/+97% (en.thairath 2908264)",
   "Marketeer — Food Selection Group / Shinkanzen 47 สาขา / Katsu Midori (marketeeronline 400251)",
   "MarketScreener — MK Restaurant Group FY2025 ฿15.42bn / กำไร ฿838m (−41.87%)",
   "Nation Thailand — กำไรเชน Q1/2026: MK −30%, สุกี้ตี๋น้อย −37%, Food Passion −47% (40066892)",
   "Nation Thailand — สงครามราคา: Bonus Suki ฿219, สุกี้ตี๋น้อย ฿117 (40052505)",
   "Oishi Group — Shabushi (oishigroup.com)",
 ]),
 ("ผู้บริโภค & ดีมานด์แซลมอน (ส่วนที่ 7.4)", [
   "LINE MAN Wongnai — Restaurant Outlook 2025 (เมนู <฿500 ทนทาน; closure rate) (lmwn.com)",
   "NielsenIQ — Mid-Year Consumer Outlook (casual dining 59%) (Nation 40042834)",
   "Baramizi Lab — Future Food Business Trends 2025 (n=800) (baramizilab.co.th)",
   "SalmonBusiness — Thailand #2 Asia, Norway >73% share, +8% (salmonbusiness.com)",
   "Norwegian Seafood Council / Koktail — 56% กินแซลมอนทุกสัปดาห์; ตลาด ฿6.5bn (koktailmagazine.com)",
 ]),
 ("แซลมอน & ราคาวัตถุดิบ", [
   "Norwegian Seafood Council — 2025 record exports / Asia #2 market",
   "fishfarmingexpert, SSB (Statistics Norway), Trading Economics — salmon spot price 2025–2026",
   "SeafoodSource / Kontali / Rabobank — 2026 supply tightening outlook",
   "IndexBox — global salmon price outlook & Thailand import price",
   "Tridge — Thailand salmon / durian / shrimp / scallop / uni wholesale",
   "kitchencost.app, Chefs-Resources, Reluctant Gourmet — salmon cut-yield 45–60% vs saku 90–95%",
   "Nippon.com, Japan Times/Nikkei — eel & Hokkaido scallop price pressure",
 ]),
 ("แซลมอน pre-rigor & การแช่แข็ง (ส่วนที่ 4.3)", [
   "Einen et al. — “Freezing of pre-rigor fillets of Atlantic salmon”, Aquaculture 212 (2002) 129–140 (Nofima)",
   "Nofima — “How the state of rigor influences the freezing of fish” (nofima.com)",
   "ScienceDirect/LWT (2022) — “Thaw rigor in Atlantic salmon fillets, as affected by thawing rate and frozen storage time”",
   "Tandfonline — “Effects of pre-, in-, or post-rigor filleting of live chilled Atlantic salmon” (gaping/yield)",
 ]),
 ("ต้นทุนดำเนินงาน (แรงงาน/พลังงาน)", [
   "Khaosod English / Ministry of Labour — ฿400 minimum wage (1 Jul 2025)",
   "ERC (กกพ.) — electricity tariff 2025–2026",
 ]),
 ("เสียงลูกค้า & รีวิว (Google/Wongnai/Pantip/Facebook)", [
   "Wongnai — Oshinei รีวิวสาขา โคราช/อุดร/อุบล/ขอนแก่น/เชียงใหม่",
   "บล็อก panasm — Oshinei โคราช (ปลาไม่สดมาก/คุ้ม)",
   "TopNews — กรณีค่าปรับของเหลือ สาขาขอนแก่น (ก.ค. 2568)",
   "Pantip — แซลมอนบุฟเฟ่ต์/Shinkanzen/เทราต์ vs แซลมอน",
   "Hilight Kapook / Sanook / Thairath — ดราม่าค่าปรับ & ‘ห่อกลับ’ (Shabu Smile)",
   "Lemon8 — Katsu Midori / Sushiro (พอร์ชัน/เมนูใหม่หมุนเวียน)",
   "Wongnai — Sushi Seki (แซลมอนชิ้นหนา)",
 ]),
 ("แนวปฏิบัติลดต้นทุน / วิศวกรรมเมนู", [
   "Toast, meez, Foodics, CrunchTime — menu engineering matrix (Stars/Plowhorses/Puzzles/Dogs)",
   "BusinessDojo / StartupFinancialProjection — buffet food-cost benchmark & waste <180g/cover",
   "Restaurant365 / CloudKitchens / Supy — central kitchen / commissary",
 ]),
]
for grp,items in src_groups:
    w(f'<h4>{grp}</h4><ul style="font-size:12.5px">')
    for s in items:
        w(f'<li>{esc(s)}</li>')
    w('</ul>')
w('<hr>')
w('<p class="small">รายงานนี้จัดทำโดย Invisible Consulting สำหรับ Oshinei Enterprise — '
  'ข้อมูลภายในเพื่อการพัฒนากลยุทธ์. ตัวเลขต้นทุนจากระบบ ERP จริง; เทรนด์/ราคา/รีวิวจากแหล่งสาธารณะข้างต้น. '
  '20 มิถุนายน 2569.</p>')
endpage()
