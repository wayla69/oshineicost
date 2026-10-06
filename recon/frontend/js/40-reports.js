/* recon frontend — 40-reports.js: #reports hub, #errors, #wrong and the internal-control reports (#override #aging #certificate #closereg #accepted #auditlog).
   Plain scripts sharing one global scope, loaded in order by index.html (00 → 90);
   a slice may call any other slice's functions, but top-level code only uses earlier slices. */
/* ── รายงานข้อผิดพลาดการบันทึก รายสาขา (สำหรับประเมินพนักงาน) ── */
async function loadErrorReport(){
  const t=++_ROUTE_SEQ;
  $('app').innerHTML=header()+loadingState();
  let r;try{r=await api('/api/reports/branch-errors');}
  catch(e){if(_cur(t))$('app').innerHTML=header()+`<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`;return;}
  if(!_cur(t))return;
  let html=header()+`<div class="row mb-6">
    <h2 class="fs-16"><i class="ti ti-chart-bar" aria-hidden="true"></i> รายงานข้อผิดพลาดการบันทึก — รายสาขา</h2>
    <a class="btn btn-sm ml-auto" href="/api/reports/branch-errors.xlsx">ดาวน์โหลด Excel</a></div>
  <div class="muted mb-10">นับจากรายการ <b>"รับทราบผลต่าง"</b> ที่บันทึกไว้ (มีเหตุผล + ผู้บันทึก) — ใช้ดูว่าสาขาไหนกระทบยอดไม่ตรงบ่อย เพื่อประเมิน/โค้ชพนักงาน · ยอดผลต่าง = มูลค่าที่ยอมรับไว้</div>`;
  if(!r.summary.length){ $('app').innerHTML=html+emptyState('checklist','ยังไม่มีรายการ “รับทราบผลต่าง”','รายงานจะขึ้นเมื่อมีการกดรับทราบผลต่างในการกระทบยอด'); return; }
  html+=`<div class="card p-0 ovx-auto"><table>
    <tr><th>สาขา</th><th>จำนวนครั้ง</th><th>จำนวนเดือน</th><th>ยอดผลต่างรวม</th></tr>
    ${r.summary.map(s=>`<tr>
      <td><b>${esc(s.branch_name)}</b></td>
      <td class="mono">${s.count}</td>
      <td class="mono">${s.months}</td>
      <td class="mono t-err fw-600">${fmt(s.total_abs_variance)}</td></tr>`).join('')}
  </table></div>
  <h3 class="sec-h">รายการทั้งหมด (${r.items.length})</h3>
  <div class="card p-0 ovx-auto"><table>
    <tr><th>สาขา</th><th>เดือน</th><th>ช่องทาง</th><th>ผลต่าง</th><th class="tl">เหตุผล</th><th>โดย</th><th>เมื่อ</th></tr>
    ${r.items.map(it=>`<tr>
      <td>${esc(it.branch_name)}</td><td class="mono">${esc(it.period_month)}</td><td>${esc(it.category)}</td>
      <td class="mono">${fmt(it.variance)}</td><td class="tl">${esc(it.reason)}</td>
      <td>${esc(it.by)}</td><td class="lbl">${esc((it.at||'').slice(0,16))}</td></tr>`).join('')}
  </table></div>`;
  $('app').innerHTML=html;
}

/* ── รายงานเงินบันทึกผิดช่อง (log + ใบเซ็นรับทราบส่งสาขา) — เลือกสาขาได้ ── */
let WC=null, WC_BR='all', WC_BRS=[];
async function loadWrongChannel(){
  const t=++_ROUTE_SEQ;
  $('app').innerHTML=header()+loadingState();
  let wc,brs;
  try{[wc,brs]=await Promise.all([api(`/api/reports/wrong-channel?period_month=${MONTH}`),_diagBranches()]);}
  catch(e){if(_cur(t))$('app').innerHTML=header()+`<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`;return;}
  if(!_cur(t))return;
  WC=wc;
  // ทุกสาขาเลือกได้ (ไม่ใช่แค่สาขาที่มีรายการผิดช่อง) — ข้อผิดพลาดประเภทอื่นอาจอยู่สาขาที่ไม่มีผิดช่อง
  const seen=new Set();WC_BRS=[];
  (brs||[]).forEach(b=>{seen.add(b.branch_id);WC_BRS.push({branch_id:b.branch_id,name:b.name});});
  [...(WC.summary||[]),...(WC.errors||[])].forEach(s=>{if(s.branch_id!=null&&!seen.has(s.branch_id)){seen.add(s.branch_id);WC_BRS.push({branch_id:s.branch_id,name:s.branch_name});}});
  if(WC_BR!=='all' && !seen.has(WC_BR))WC_BR='all';
  renderWrongChannel();
}
function setWcBranch(v){WC_BR=(v==='all'?'all':+v);renderWrongChannel();}
function renderWrongChannel(){
  const all=WC_BR==='all';
  const summary=all?WC.summary:WC.summary.filter(s=>s.branch_id===WC_BR);
  const items  =all?WC.items  :WC.items.filter(it=>it.branch_id===WC_BR);
  const amt=items.reduce((a,it)=>a+(it.amount||0),0);
  const pm=`?period_month=${MONTH}`+(all?'':`&branch_id=${WC_BR}`);
  const errN={};(WC.errors||[]).forEach(e=>errN[e.branch_id]=(errN[e.branch_id]||0)+1);
  const wcN={};(WC.summary||[]).forEach(s=>wcN[s.branch_id]=s.count||0);
  const brOpts=`<option value="all">ทุกสาขา</option>`+
    WC_BRS.map(b=>{const n=(errN[b.branch_id]||0)+(wcN[b.branch_id]||0);
      return `<option value="${b.branch_id}" ${b.branch_id===WC_BR?'selected':''}>${esc(b.name)}${n?` (${n})`:''}</option>`;}).join('');
  const dirChip=d=>d==='down'?'<span class="chip c-ok ov-mini">✓ ดีขึ้น</span>'
    :d==='up'?'<span class="chip c-warn ov-mini">▲ ผิดมากขึ้น</span>'
    :'<span class="chip c-mut ov-mini">— ทรงตัว</span>';
  const errsAll=(WC.errors||[]);
  const errs=all?errsAll:errsAll.filter(e=>e.branch_id===WC_BR);
  const typeTh=WC.error_type_th||{};
  const ST={open:['c-err','ค้าง'],acknowledged:['c-mut','รับทราบแล้ว'],resolved:['c-ok','แก้ไขแล้ว']};
  const nOpen=errs.filter(e=>e.status==='open').length;
  const errAmt=errs.reduce((a,e)=>a+Math.abs(e.amount||0),0);
  let html=header()+`<div class="row mb-6">
    <h2 class="fs-16"><i class="ti ti-alert-triangle" aria-hidden="true"></i> รายงานข้อผิดพลาดประจำเดือน — ${esc(monthTh(MONTH))}</h2>
    <select onchange="setWcBranch(this.value)" title="เลือกสาขา" class="ctl ml-auto">${brOpts}</select>
    <a class="btn btn-sm btn-pri" href="/api/reports/wrong-channel.print${pm}" target="_blank"><i class="ti ti-printer" aria-hidden="true"></i> พิมพ์/PDF</a>
    <a class="btn btn-sm" href="/api/reports/wrong-channel.xlsx${pm}">Excel</a></div>
  <div class="muted mb-10">ข้อผิดพลาดทุกประเภทที่พบในเดือนนี้ — ยอดต่าง, ขาดเอกสาร, เงินเข้าเกิน, เงินโอนไม่ครบ, รหัสยังไม่ตั้งธนาคาร และการบันทึกผิดช่อง · <b>ค้าง</b> = ยังต้องจัดการก่อนปิดเดือน</div>`;
  if(!errs.length && !items.length){ $('app').innerHTML=html+`<div class="card c-ok"><i class="ti ti-confetti" aria-hidden="true"></i> ไม่พบข้อผิดพลาด${all?'ในเดือนนี้':'ของสาขานี้'} — ทุกช่องทางตรง เอกสารครบ</div>`; return; }

  // ── ข้อผิดพลาดทั้งหมด (ทุกประเภท) ──
  if(errs.length){
    const counts={};errs.forEach(e=>counts[e.type]=(counts[e.type]||0)+1);
    const chips=Object.keys(counts).map(t=>`<span class="chip c-mut">${esc(typeTh[t]||t)}: ${counts[t]}</span>`).join(' ');
    html+=`<div class="card kpi-row">
      <div><div class="lbl">ข้อผิดพลาดทั้งหมด</div><div class="kpi-v">${errs.length}</div></div>
      <div><div class="lbl">ค้าง (ต้องจัดการ)</div><div class="kpi-v t-err">${nOpen}</div></div>
      <div><div class="lbl">มูลค่ารวม (abs)</div><div class="kpi-v">฿${fmt(errAmt)}</div></div>
      <div class="fx-1 minw-200 as-center tr">${chips}</div>
    </div>`;
    html+=`<div class="card p-0 ovx-auto mb-14"><table>
      <tr><th>ประเภท</th>${all?'<th>สาขา</th>':''}<th>ช่องทาง/รหัส</th><th class="tl">รายละเอียด</th><th>ยอด</th><th>สถานะ</th><th class="tl">เหตุผล/หมายเหตุ</th></tr>
      ${errs.map(e=>{const st=ST[e.status]||['c-mut',e.status];return `<tr>
        <td>${esc(typeTh[e.type]||e.type)}</td>${all?`<td>${esc(e.branch_name)}</td>`:''}
        <td>${esc(e.channel_th||'—')}</td><td class="tl">${esc(e.detail||'')}</td>
        <td class="mono">${e.amount?fmt(e.amount):'—'}</td>
        <td><span class="chip ${st[0]} ov-mini">${st[1]}</span></td>
        <td class="lbl tl">${esc(e.reason||'—')}</td></tr>`;}).join('')}
    </table></div>`;
  }

  if(!items.length){ $('app').innerHTML=html; return; }   // errors shown; no wrong-channel detail
  html+=`<h3 class="sec-h">⇄ รายละเอียดการบันทึกผิดช่อง (${items.length})</h3>
  <div class="muted mb-8">รายการที่ยืนยันว่า <b>"เป็นเงินก้อนเดียวกัน"</b> — เงินเข้าถูกต้องครบ แต่ POS กดผิดช่องทาง · ใบให้สาขาเซ็นรับทราบและแก้ไข</div>`;
  html+=`<div class="card kpi-row">
    <div><div class="lbl">จำนวนรายการ</div><div class="kpi-v">${items.length}</div></div>
    <div><div class="lbl">มูลค่ารวม</div><div class="kpi-v">฿${fmt(amt)}</div></div>
    <div><div class="lbl">${all?'สาขาที่เกี่ยวข้อง':'สาขา'}</div><div class="kpi-v">${all?summary.length:esc(summary[0].branch_name)}</div></div>
  </div>`;
  html+=`<div class="card p-0 ovx-auto"><table>
    <tr><th>สาขา</th><th>จำนวนครั้ง</th><th>ยอดรวม</th><th>แนวโน้ม</th><th>ใบเซ็นรับทราบ</th></tr>
    ${summary.map(s=>`<tr>
      <td><b>${esc(s.branch_name)}</b></td>
      <td class="mono">${s.count}</td>
      <td class="mono t-warn fw-600">${fmt(s.total)}</td>
      <td>${dirChip(s.direction)}</td>
      <td><a class="lbl" href="/api/reports/wrong-channel.print?period_month=${MONTH}&branch_id=${s.branch_id}" target="_blank"><i class="ti ti-printer" aria-hidden="true"></i> พิมพ์เฉพาะสาขานี้</a></td></tr>`).join('')}
  </table></div>
  <h3 class="sec-h">แนวทางพัฒนารายสาขา</h3>
  ${summary.map(s=>`<div class="card mb-10">
    <div class="row mb-6">
      <b>${esc(s.branch_name)}</b> ${dirChip(s.direction)}
      <span class="lbl ml-auto"><i class="ti ti-target" aria-hidden="true"></i> เป้าเดือนหน้า ≤ ${s.target} ครั้ง (เดือนนี้ ${s.count})</span></div>
    ${(s.patterns||[]).length?`<div class="lbl" style="margin-bottom:3px">ช่องที่สับสนบ่อย:</div>
      <div class="row" style="gap:6px;flex-wrap:wrap;margin-bottom:7px">
      ${s.patterns.map(p=>`<span class="chip c-warn ov-mini">${esc(p.from_th)} → ${esc(p.to_th)} · ${p.count} ครั้ง</span>`).join('')}</div>`:''}
    ${(s.tips||[]).length?`<ul style="margin:2px 0 0;padding-left:18px;font-size:13px;line-height:1.55">
      ${s.tips.map(t=>`<li>${esc(t)}</li>`).join('')}</ul>`:''}
  </div>`).join('')}
  <h3 class="sec-h">รายการทั้งหมด (${items.length})</h3>
  <div class="card p-0 ovx-auto"><table>
    <tr><th>สาขา</th><th>วันที่</th><th>บันทึกผิดเป็น</th><th>ที่จริงเข้า</th><th>ยอด</th><th class="tl">เหตุผล/หมายเหตุ</th><th>โดย</th></tr>
    ${items.map(it=>{
      const days=(it.days||[]).map(d=>(+d.slice(8))).join(', ')||'—';
      const toTh=esc(it.to_th)+(it.to_bank_th?` (${esc(it.to_bank_th)})`:'');
      const srcTag=it.source==='move'?` <span class="chip c-mut ov-mini">ย้ายด้วยมือ</span>`:'';
      return `<tr>
        <td>${esc(it.branch_name)}</td><td class="mono">${days}</td>
        <td>${esc(it.from_th)}${srcTag}</td><td><span class="t-warn">→</span> ${toTh}</td>
        <td class="mono">${fmt(it.amount)}</td><td class="tl">${esc(it.reason||'—')}</td>
        <td class="lbl">${esc(it.by||'—')}</td></tr>`;}).join('')}
  </table></div>`;
  $('app').innerHTML=html;
}

function loadReports(){
  const el=$('app');
  const pfx=`/api`;
  el.innerHTML=header()+`
  <div class="mb-14">
    <h2 class="fs-16 mb-4">② รายงาน</h2>
    <div class="lbl">รายงานและเครื่องมือทั้งหมด — เปิดในแอปหรือพิมพ์ PDF</div>
  </div>

  <div class="lbl grp-h">งานประจำ / Operations</div>
  <div class="rpt-grid mb-16">
    <a class="rpt-item" href="#dashboard">
      <span class="rpt-icon"><i class="ti ti-chart-bar" aria-hidden="true"></i></span>
      <div class="rpt-body">
        <div class="rpt-title">Dashboard รายได้รายวัน</div>
        <div class="rpt-desc">ทุกสาขา × ช่องทาง แยกตามวัน · เลือกวันจากปฏิทิน</div>
        <span class="rpt-badge">in-app</span>
      </div>
    </a>
    <a class="rpt-item" href="#daybook">
      <span class="rpt-icon"><i class="ti ti-calendar" aria-hidden="true"></i></span>
      <div class="rpt-body">
        <div class="rpt-title">สรุปบัญชีรายวัน (รายใบ)</div>
        <div class="rpt-desc">แต่ละวัน = 1 ใบ · ยอดเต็ม/ส่วนลดแยกบรรทัด · เลือกสาขา · พิมพ์ PDF</div>
        <span class="rpt-badge">in-app</span>
      </div>
    </a>
    <a class="rpt-item" href="${pfx}/monthly-report?period_month=${MONTH}" target="_blank" rel="noopener">
      <span class="rpt-icon"><i class="ti ti-printer" aria-hidden="true"></i></span>
      <div class="rpt-body">
        <div class="rpt-title">รายงานการจับคู่รายวัน</div>
        <div class="rpt-desc">ไล่ทุกวัน × ช่องทาง · ตรวจจับคู่ถูกครบ · ทุกสาขา</div>
        <span class="rpt-badge ext">PDF ↗</span>
      </div>
    </a>
    <a class="rpt-item" href="${pfx}/reports/outstanding-docs.print?period_month=${MONTH}" target="_blank" rel="noopener">
      <span class="rpt-icon"><i class="ti ti-inbox" aria-hidden="true"></i></span>
      <div class="rpt-body">
        <div class="rpt-title">ติดตามเอกสารที่ยังขาด</div>
        <div class="rpt-desc">รายสาขา/เดือน · เอกสารต้องอัป + ยอดที่รอ</div>
        <span class="rpt-badge ext">PDF ↗</span>
      </div>
    </a>
  </div>

  <div class="lbl grp-h">การตรวจสอบ / Review</div>
  <div class="rpt-grid mb-16">
    <a class="rpt-item" href="#wrong">
      <span class="rpt-icon">⇄</span>
      <div class="rpt-body">
        <div class="rpt-title">เงินบันทึกผิดช่อง</div>
        <div class="rpt-desc">สรุป + ใบเซ็นรับทราบส่งสาขา · แยกประเภทข้อผิดพลาด</div>
        <span class="rpt-badge">in-app</span>
      </div>
    </a>
    <a class="rpt-item" href="#errors">
      <span class="rpt-icon"><i class="ti ti-clipboard-list" aria-hidden="true"></i></span>
      <div class="rpt-body">
        <div class="rpt-title">ข้อผิดพลาดการบันทึก</div>
        <div class="rpt-desc">รายสาขา/เดือน · ประเมินพนักงาน</div>
        <span class="rpt-badge">in-app</span>
      </div>
    </a>
    <a class="rpt-item" href="#coupon">
      <span class="rpt-icon"><i class="ti ti-ticket" aria-hidden="true"></i></span>
      <div class="rpt-body">
        <div class="rpt-title">คูปองซีรีส์</div>
        <div class="rpt-desc">ส่วนลดแยกตามซีรีส์คูปอง · MoM · ดูรายสาขา/รายวัน</div>
        <span class="rpt-badge">in-app</span>
      </div>
    </a>
  </div>

  <div class="row" style="margin:0 0 6px 2px;align-items:center">
    <div class="lbl fw-700 ls-wide">การควบคุมภายใน / Audit &amp; Controls</div>
    <a class="btn btn-sm btn-pri ml-auto" href="${pfx}/reports/auditor-pack.zip?period_month=${MONTH}"><i class="ti ti-package" aria-hidden="true"></i> ชุดเอกสารผู้สอบ (ZIP) — งวด ${MONTH}</a>
  </div>
  <div class="rpt-grid mb-16">
    <a class="rpt-item" href="#certificate">
      <span class="rpt-icon"><i class="ti ti-certificate" aria-hidden="true"></i></span>
      <div class="rpt-body">
        <div class="rpt-title">ใบรับรองการกระทบยอด</div>
        <div class="rpt-desc">รายสาขา/เดือน · POS vs เงินเข้า · ผู้จัดทำ/ผู้สอบทาน · พิมพ์เซ็น</div>
        <span class="rpt-badge">in-app</span>
      </div>
    </a>
    <a class="rpt-item" href="#override">
      <span class="rpt-icon"><i class="ti ti-writing" aria-hidden="true"></i></span>
      <div class="rpt-body">
        <div class="rpt-title">ทะเบียนการปรับปรุงด้วยมือ</div>
        <div class="rpt-desc">ทุกการแก้ด้วยมือ · ใคร·เมื่อ·เหตุผล · รายการแรกที่ผู้สอบขอ</div>
        <span class="rpt-badge">in-app</span>
      </div>
    </a>
    <a class="rpt-item" href="#aging">
      <span class="rpt-icon"><i class="ti ti-hourglass-high" aria-hidden="true"></i></span>
      <div class="rpt-body">
        <div class="rpt-title">อายุรายการค้าง</div>
        <div class="rpt-desc">รายการยังไม่ปิด แยกช่วงอายุ 0-7 / 8-30 / เกิน 30 วัน</div>
        <span class="rpt-badge">in-app</span>
      </div>
    </a>
    <a class="rpt-item" href="#closereg">
      <span class="rpt-icon"><i class="ti ti-calendar-check" aria-hidden="true"></i></span>
      <div class="rpt-body">
        <div class="rpt-title">ทะเบียนการปิดเดือน</div>
        <div class="rpt-desc">ทุกสาขา × 6 เดือน · ปิด/อนุมัติ/ยังเปิด · ใครเซ็น · มีหลักฐานไหม</div>
        <span class="rpt-badge">in-app</span>
      </div>
    </a>
    <a class="rpt-item" href="#accepted">
      <span class="rpt-icon"><i class="ti ti-receipt-off" aria-hidden="true"></i></span>
      <div class="rpt-body">
        <div class="rpt-title">สรุปผลต่างที่รับทราบ</div>
        <div class="rpt-desc">Write-off แยกตามเหตุผล·สาขา · เน้นรายการเกินเกณฑ์</div>
        <span class="rpt-badge">in-app</span>
      </div>
    </a>
    <a class="rpt-item" href="#auditlog">
      <span class="rpt-icon"><i class="ti ti-history" aria-hidden="true"></i></span>
      <div class="rpt-body">
        <div class="rpt-title">ประวัติการแก้ไข (Audit Trail)</div>
        <div class="rpt-desc">ใคร·เมื่อ·แก้อะไร ทุกการเปลี่ยนแปลง · เพิ่มอย่างเดียว แก้ไม่ได้</div>
        <span class="rpt-badge">in-app</span>
      </div>
    </a>
  </div>

  <div class="lbl grp-h">เอกสาร & เครื่องมือ / Docs & Tools</div>
  <div class="rpt-grid mb-16">
    <a class="rpt-item" href="${pfx}/statement-codes.html" target="_blank" rel="noopener">
      <span class="rpt-icon"><i class="ti ti-book" aria-hidden="true"></i></span>
      <div class="rpt-body">
        <div class="rpt-title">พจนานุกรมรหัสสเตทเมนต์</div>
        <div class="rpt-desc">รหัสธนาคาร → ความหมาย → ช่องทาง · ให้ทีมตรวจ</div>
        <span class="rpt-badge ext">PDF ↗</span>
      </div>
    </a>
    <a class="rpt-item" href="${pfx}/uat-signoff?period_month=${MONTH}" target="_blank" rel="noopener">
      <span class="rpt-icon"><i class="ti ti-circle-check" aria-hidden="true"></i></span>
      <div class="rpt-body">
        <div class="rpt-title">ใบทดสอบ + รับรองการใช้งาน (UAT)</div>
        <div class="rpt-desc">เช็กลิสต์ทดสอบ + ช่องเซ็นรับรอง · เดือน ${MONTH}</div>
        <span class="rpt-badge ext">PDF ↗</span>
      </div>
    </a>
    <a class="rpt-item" href="/app/help.html" target="_blank" rel="noopener">
      <span class="rpt-icon"><i class="ti ti-books" aria-hidden="true"></i></span>
      <div class="rpt-body">
        <div class="rpt-title">คู่มือการใช้งาน</div>
        <div class="rpt-desc">ทุกเมนู ทุกเอกสาร + Q&amp;A · พิมพ์ได้</div>
        <span class="rpt-badge ext">เปิดใหม่ ↗</span>
      </div>
    </a>
  </div>`;
}
/* ════════════════════════════════════════════════════════════════════════
   § Internal-control reports (audit evidence) — READ-ONLY, frontend-only
   Routes: #override (ทะเบียนปรับปรุงด้วยมือ) · #aging (อายุรายการค้าง)
           #certificate (ใบรับรองการกระทบยอด).  No backend logic touched.
   ════════════════════════════════════════════════════════════════════════ */
function _icBranchSel(brs,sel,fn,includeAll){
  const all=includeAll===false?'':`<option value="">ทุกสาขา</option>`;
  return `<select onchange="${fn}(this.value)" title="เลือกสาขา"
    class="ctl ml-auto">
    ${all}${brs.map(b=>`<option value="${b.branch_id}" ${String(sel)===String(b.branch_id)?'selected':''}>${esc(b.name)}</option>`).join('')}</select>`;
}

/* #override — ทะเบียนการปรับปรุงด้วยมือ (Management Override Register) */
let _OVR_BR='';
function setOvrBranch(v){_OVR_BR=v||'';loadOverrideRegister();}
async function loadOverrideRegister(){
  const t=++_ROUTE_SEQ;
  $('app').innerHTML=header()+loadingState();
  const q=`?period_month=${MONTH}${_OVR_BR?`&branch_id=${_OVR_BR}`:''}`;
  let brs,r;
  try{[brs,r]=await Promise.all([api('/api/branches'),api('/api/reports/override-register'+q)]);}
  catch(e){if(_cur(t))$('app').innerHTML=header()+`<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`;return;}
  if(!_cur(t))return;
  let html=header()+`<div class="row mb-6">
    <h2 class="fs-16"><i class="ti ti-writing" aria-hidden="true"></i> ทะเบียนการปรับปรุงด้วยมือ — ${esc(monthTh(MONTH))}</h2>
    ${_icBranchSel(brs,_OVR_BR,'setOvrBranch')}
    <a class="btn btn-sm btn-pri" href="/api/reports/override-register.print${q}" target="_blank"><i class="ti ti-printer" aria-hidden="true"></i> พิมพ์/PDF</a>
    <a class="btn btn-sm" href="/api/reports/override-register.xlsx${q}">Excel</a></div>
  <div class="muted mb-10">ทุกการแก้ไขด้วยมือในเดือนนี้ (จับคู่เอง · ย้ายช่อง · รับทราบผลต่าง · ปรับ lane) พร้อมผู้ทำ · เวลา · เหตุผล — รายการแรกที่ผู้สอบบัญชีขอดู · รายการไม่มีเหตุผลถูกเน้นแดง</div>`;
  if(!r.rows.length){$('app').innerHTML=html+emptyState('checklist','ไม่มีการปรับปรุงด้วยมือ','ระบบจับคู่อัตโนมัติทั้งหมดในเดือนนี้');return;}
  const warn=r.missing_reason?`<span class="chip c-err">${r.missing_reason} รายการไม่มีเหตุผล</span>`:`<span class="chip c-ok">ทุกรายการมีเหตุผล ✓</span>`;
  html+=`<div class="card" style="padding:12px 16px;margin-bottom:12px;display:flex;gap:28px;flex-wrap:wrap;align-items:center">
    <div><div class="lbl">รวมรายการ</div><div class="kpi-v">${r.count}</div></div>
    <div><div class="lbl">มูลค่ารวม</div><div class="kpi-v">${fmt(r.total_abs_amount)}</div></div>
    <div>${warn}</div></div>
  <div class="card p-0 ovx-auto"><table>
    <tr><th class="tl">เวลา</th><th class="tl">สาขา</th><th class="tl">ประเภท</th><th class="tl">ช่องทาง / รายการ</th><th>จำนวน</th><th class="tl">โดย</th><th class="tl">เหตุผล</th></tr>
    ${r.rows.map(x=>`<tr>
      <td class="lbl tl">${esc((x.at||'').slice(0,16))}</td>
      <td class="tl">${esc(x.branch_name)}</td>
      <td class="tl">${esc(x.kind)}</td>
      <td class="tl">${esc(x.lane)}<div class="lbl">${esc(x.detail)}</div></td>
      <td class="mono">${fmt(Math.abs(x.amount))}</td>
      <td class="tl">${esc(x.by)}</td>
      <td class="tl">${x.has_reason?esc(x.reason):'<span class="t-err fw-600">— ไม่ระบุ —</span>'}</td></tr>`).join('')}
  </table></div>`;
  $('app').innerHTML=html;
}

/* #aging — อายุรายการค้าง (Aging of Open Items) */
let _AGE_BR='';
function setAgeBranch(v){_AGE_BR=v||'';loadAging();}
async function loadAging(){
  const t=++_ROUTE_SEQ;
  $('app').innerHTML=header()+loadingState();
  const q=`?period_month=${MONTH}${_AGE_BR?`&branch_id=${_AGE_BR}`:''}`;
  let brs,r;
  try{[brs,r]=await Promise.all([api('/api/branches'),api('/api/reports/open-items-aging'+q+'&lookback=2')]);}
  catch(e){if(_cur(t))$('app').innerHTML=header()+`<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`;return;}
  if(!_cur(t))return;
  const b=r.buckets;
  let html=header()+`<div class="row mb-6">
    <h2 class="fs-16"><i class="ti ti-hourglass-high" aria-hidden="true"></i> อายุรายการค้าง — ณ ${esc(r.as_of)}</h2>
    ${_icBranchSel(brs,_AGE_BR,'setAgeBranch')}
    <a class="btn btn-sm btn-pri" href="/api/reports/open-items-aging.print${q}&lookback=2" target="_blank"><i class="ti ti-printer" aria-hidden="true"></i> พิมพ์/PDF</a>
    <a class="btn btn-sm" href="/api/reports/open-items-aging.xlsx${q}&lookback=2">Excel</a></div>
  <div class="muted mb-10">รายการกระทบยอดที่ยังไม่ปิด นับอายุจากสิ้นงวด · ตรวจ ${esc(MONTH)} ย้อนหลัง 2 เดือน (ไม่รวมงวดที่อนุมัติ/ปิดแล้ว) — ของค้างเกิน 30 วันต้องมีคำอธิบาย</div>
  <div class="card" style="padding:12px 16px;margin-bottom:12px;display:flex;gap:32px;flex-wrap:wrap">
    <div><div class="lbl">0–7 วัน</div><div class="kpi-v t-ok">${b['0-7']}</div></div>
    <div><div class="lbl">8–30 วัน</div><div class="kpi-v t-warn">${b['8-30']}</div></div>
    <div><div class="lbl">เกิน 30 วัน</div><div class="kpi-v t-err">${b['>30']}</div></div></div>`;
  if(!r.items.length){$('app').innerHTML=html+emptyState('checklist','ไม่มีรายการค้าง','ทุกงวดที่ตรวจปิดครบหรืออยู่ในเกณฑ์ที่รับทราบแล้ว');return;}
  const bkChip={'0-7':'c-ok','8-30':'c-warn','>30':'c-err'};
  const tyTh={upload:'อัปโหลดเอกสาร',setup:'ตั้งค่า',diff:'ตรวจผลต่าง',waiting:'รอเอกสาร'};
  html+=`<div class="card p-0 ovx-auto"><table>
    <tr><th>อายุ</th><th class="tl">ช่วง</th><th class="tl">งวด</th><th class="tl">สาขา</th><th class="tl">ประเภท</th><th class="tl">รายการที่ต้องทำ</th></tr>
    ${r.items.map(it=>`<tr>
      <td class="mono">${it.age_days}</td>
      <td class="tl"><span class="chip ${bkChip[it.bucket]}">${esc(it.bucket)} วัน</span></td>
      <td class="lbl tl">${esc(it.period_month)}</td>
      <td class="tl">${esc(it.branch_name)}</td>
      <td class="tl">${esc(tyTh[it.type]||it.type||'')}</td>
      <td class="tl">${esc(it.label)}</td></tr>`).join('')}
  </table></div>`;
  $('app').innerHTML=html;
}

/* #certificate — ใบรับรองการกระทบยอด (Reconciliation Sign-off Certificate) */
let _CERT_BR=null;
function setCertBranch(v){_CERT_BR=v?+v:null;loadCertificate();}
async function loadCertificate(){
  const t=++_ROUTE_SEQ;
  $('app').innerHTML=header()+loadingState();
  let brs;
  try{brs=await api('/api/branches');}
  catch(e){if(_cur(t))$('app').innerHTML=header()+`<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`;return;}
  if(!_cur(t))return;
  if(!brs.length){$('app').innerHTML=header()+emptyState('certificate','ไม่มีสาขา','');return;}
  if(_CERT_BR===null)_CERT_BR=brs[0].branch_id;
  let c;
  try{c=await api(`/api/reports/recon-certificate?branch_id=${_CERT_BR}&period_month=${MONTH}`);}
  catch(e){if(_cur(t))$('app').innerHTML=header()+`<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`;return;}
  if(!_cur(t))return;
  const sess=c.session||{}, st=sess.status||'draft';
  const stTh={draft:'ร่าง (ยังไม่สอบทาน)',in_review:'รอสอบทาน',approved:'อนุมัติแล้ว',closed:'ปิดงวดแล้ว'};
  const stChip=(st==='approved'||st==='closed')?'c-ok':(st==='in_review'?'c-warn':'c-mut');
  const chStTh={ok:'ตรงกัน',diff:'มีผลต่าง',waiting_doc:'รอเอกสาร',no_source:'ไม่มีคู่เทียบ',setup:'รอตั้งค่า'};
  const chStCls={ok:'c-ok',diff:'c-err',waiting_doc:'c-warn',no_source:'c-mut',setup:'c-warn'};
  const residCls=Math.abs(c.residual_total)>100?'color:var(--err);font-weight:700':'color:var(--ok);font-weight:700';
  let html=header()+`<div class="row mb-6">
    <h2 class="fs-16"><i class="ti ti-certificate" aria-hidden="true"></i> ใบรับรองการกระทบยอด — ${esc(monthTh(MONTH))}</h2>
    ${_icBranchSel(brs,_CERT_BR,'setCertBranch',false)}
    <a class="btn btn-sm btn-pri" href="/api/reports/recon-certificate.print?period_month=${MONTH}&branch_id=${_CERT_BR}" target="_blank"><i class="ti ti-printer" aria-hidden="true"></i> พิมพ์สาขานี้</a>
    <a class="btn btn-sm" href="/api/reports/recon-certificate.print?period_month=${MONTH}" target="_blank">พิมพ์ทุกสาขา</a></div>
  <div class="card pad-14-18 mb-12">
    <div class="row ai-base gap-10"><b class="fs-15">${esc(c.branch_name)}</b>
      <span class="chip ${stChip}">${esc(stTh[st]||st)}</span>
      <span class="lbl">กระทบยอดแล้ว ${c.progress_pct}%${c.todo_count?` · เหลือ ${c.todo_count} เรื่อง`:''}</span></div>
    <div style="display:flex;gap:30px;flex-wrap:wrap;margin-top:10px">
      <div><div class="lbl">POS รวม</div><div class="kpi-v18">${fmt(c.pos_total)}</div></div>
      <div><div class="lbl">เงินเข้าจริงรวม</div><div class="kpi-v18">${fmt(c.ext_total)}</div></div>
      <div><div class="lbl">ผลต่างคงเหลือ (หลังรับทราบ)</div><div style="font-size:18px;${residCls}">${c.residual_total>0?'+':''}${fmt(c.residual_total)}</div></div>
      <div><div class="lbl">รายการรับทราบ</div><div class="kpi-v18">${c.ack_count} <span class="lbl">(${fmt(c.ack_amount)})</span></div></div>
      <div><div class="lbl">แก้ด้วยมือทั้งหมด</div><div class="kpi-v18">${c.override_count}</div></div>
    </div></div>
  <div class="card p-0 ovx-auto mb-12"><table>
    <tr><th class="tl">ช่องทาง</th><th>POS</th><th>เงินเข้าจริง</th><th>ผลต่าง</th><th>คงเหลือ</th><th class="tl">สถานะ</th></tr>
    ${c.channels.map(ch=>{
      const isCp=ch.key==='COUPON';
      const rtxt=isCp?'<span class="lbl">—</span>':`${ch.residual>0?'+':''}${fmt(ch.residual)}`;
      const rcls=(!isCp&&Math.abs(ch.residual)>100)?'color:var(--err);font-weight:600':'';
      const lab=ch.accepted?'รับทราบแล้ว':(chStTh[ch.status]||ch.status);
      const cls=ch.accepted?'c-mut':(chStCls[ch.status]||'c-mut');
      return `<tr><td class="tl"><b>${esc(ch.label)}</b></td>
        <td class="mono">${fmt(ch.pos)}</td><td class="mono">${fmt(ch.ext)}</td>
        <td class="mono">${ch.variance>0?'+':''}${fmt(ch.variance)}</td>
        <td class="mono" style="${rcls}">${rtxt}</td>
        <td class="tl"><span class="chip ${cls}">${esc(lab)}</span></td></tr>`;}).join('')}
    <tr style="border-top:2px solid var(--line)"><td class="tl"><b>รวม</b></td>
      <td class="mono"><b>${fmt(c.pos_total)}</b></td><td class="mono"><b>${fmt(c.ext_total)}</b></td>
      <td></td><td class="mono"><b>${c.residual_total>0?'+':''}${fmt(c.residual_total)}</b></td><td></td></tr>
  </table></div>
  <div class="card pad-14-18">
    <div class="lbl mb-8">${c.snapshot?`หลักฐานแช่แข็ง (SHA-256): <span class="mono">${esc((c.snapshot.sha256||'').slice(0,16))}…</span> · ${esc((c.snapshot.created_at||'').slice(0,16))} · ผูกเอกสารต้นทาง ${c.snapshot.source_doc_count||0} ไฟล์`:'<span class="t-warn">ยังไม่มีหลักฐานแช่แข็ง — อนุมัติงวดเพื่อสร้าง snapshot</span>'}</div>
    <div style="display:flex;gap:40px;flex-wrap:wrap;margin-top:6px">
      <div><div class="lbl">ผู้จัดทำ</div><div class="fw-600">${esc(sess.created_by||'—')}</div></div>
      <div><div class="lbl">ผู้สอบทาน / อนุมัติ</div><div class="fw-600">${esc(sess.approved_by||'—')}</div></div>
      <div><div class="lbl">วันที่อนุมัติ</div><div class="fw-600">${esc((sess.approved_at||'').slice(0,16)||'—')}</div></div>
    </div></div>`;
  $('app').innerHTML=html;
}

/* #closereg — ทะเบียนการปิดเดือน (Close Register) */
let _CLR_BR='';
function setClrBranch(v){_CLR_BR=v||'';loadCloseRegister();}
async function loadCloseRegister(){
  const t=++_ROUTE_SEQ;
  $('app').innerHTML=header()+loadingState();
  const q=`?period_month=${MONTH}&months=6${_CLR_BR?`&branch_id=${_CLR_BR}`:''}`;
  let brs,r;
  try{[brs,r]=await Promise.all([api('/api/branches'),api('/api/reports/close-register'+q)]);}
  catch(e){if(_cur(t))$('app').innerHTML=header()+`<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`;return;}
  if(!_cur(t))return;
  const stTh={draft:'ร่าง',in_review:'รอสอบทาน',approved:'อนุมัติแล้ว',closed:'ปิดงวดแล้ว'};
  const stCls={draft:'c-mut',in_review:'c-warn',approved:'c-ok',closed:'c-ok'};
  let html=header()+`<div class="row mb-6">
    <h2 class="fs-16"><i class="ti ti-calendar-check" aria-hidden="true"></i> ทะเบียนการปิดเดือน — 6 เดือนล่าสุด</h2>
    ${_icBranchSel(brs,_CLR_BR,'setClrBranch')}
    <a class="btn btn-sm btn-pri" href="/api/reports/close-register.print${q}" target="_blank"><i class="ti ti-printer" aria-hidden="true"></i> พิมพ์/PDF</a>
    <a class="btn btn-sm" href="/api/reports/close-register.xlsx${q}">Excel</a></div>
  <div class="muted mb-10">สถานะการปิดงวดของทุกสาขาหลัก · ใครอนุมัติ · เมื่อไหร่ · กระทบยอดแล้วกี่ % · มีหลักฐานแช่แข็งหรือยัง — <b>ยังไม่ปิดรวม ${r.open_total} สาขา-เดือน</b></div>`;
  if(!r.rows.length){$('app').innerHTML=html+emptyState('calendar','ไม่มีข้อมูลช่วงนี้','');return;}
  for(const mth of r.months){
    const mr=r.month_rollup[mth];if(!mr)continue;
    const rows=r.rows.filter(x=>x.period_month===mth);
    html+=`<div class="card pad-12-16 mb-10">
      <div class="row ai-base gap-10 mb-6"><b class="fs-14">${esc(mth)}</b>
        <span class="lbl">ปิดแล้ว ${mr.closed} · อนุมัติ ${mr.approved} · <span class="t-warn">ยังเปิด ${mr.open}</span> / ${mr.total} สาขา</span></div>
      <div class="ovx-auto"><table>
        <tr><th class="tl">สาขา</th><th class="tl">สถานะ</th><th>กระทบยอด</th><th>คงค้าง</th><th class="tl">ผู้อนุมัติ</th><th class="tl">วันที่</th><th>หลักฐาน</th></tr>
        ${rows.map(x=>`<tr>
          <td class="tl"><b>${esc(x.branch_name)}</b></td>
          <td class="tl"><span class="chip ${stCls[x.status]||'c-mut'}">${esc(stTh[x.status]||x.status)}</span></td>
          <td class="mono">${x.progress_pct}%</td>
          <td class="mono">${x.todo_count||''}</td>
          <td class="tl">${esc(x.approved_by||'—')}</td>
          <td class="lbl tl">${esc((x.approved_at||'').slice(0,16)||'—')}</td>
          <td>${x.has_snapshot?'<span class="t-ok">✔</span>':'—'}</td></tr>`).join('')}
      </table></div></div>`;
  }
  $('app').innerHTML=html;
}

/* #accepted — สรุปผลต่างที่รับทราบ (Accepted-Variance / Write-off) */
let _AV_BR='', _AV_TH=1000;
function setAvBranch(v){_AV_BR=v||'';loadAcceptedVariance();}
function setAvTh(v){const n=parseFloat(v);if(!isNaN(n)&&n>=0){_AV_TH=n;loadAcceptedVariance();}}
async function loadAcceptedVariance(){
  const t=++_ROUTE_SEQ;
  $('app').innerHTML=header()+loadingState();
  const q=`?period_month=${MONTH}&threshold=${_AV_TH}${_AV_BR?`&branch_id=${_AV_BR}`:''}`;
  let brs,s;
  try{[brs,s]=await Promise.all([api('/api/branches'),api('/api/reports/accepted-variance'+q)]);}
  catch(e){if(_cur(t))$('app').innerHTML=header()+`<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`;return;}
  if(!_cur(t))return;
  let html=header()+`<div class="row mb-6">
    <h2 class="fs-16"><i class="ti ti-receipt-off" aria-hidden="true"></i> สรุปผลต่างที่รับทราบ (Write-off) — ${esc(monthTh(MONTH))}</h2>
    ${_icBranchSel(brs,_AV_BR,'setAvBranch')}
    <a class="btn btn-sm btn-pri" href="/api/reports/accepted-variance.print${q}" target="_blank"><i class="ti ti-printer" aria-hidden="true"></i> พิมพ์/PDF</a>
    <a class="btn btn-sm" href="/api/reports/accepted-variance.xlsx${q}">Excel</a></div>
  <div class="row mb-10 ai-center gap-8">
    <span class="muted">ยอดผลต่างที่ “รับทราบ” แล้วปล่อยผ่าน (ทั้งช่อง + รายวัน) · เกณฑ์เน้นต่อรายการ</span>
    <input type="number" min="0" step="500" value="${_AV_TH}" onchange="setAvTh(this.value)" style="width:110px;padding:5px 8px;border:1px solid var(--line);border-radius:var(--radius-sm);font:inherit" title="เกณฑ์ (บาท/รายการ)">
    <span class="muted">บาท</span></div>`;
  if(!s.items.length){$('app').innerHTML=html+emptyState('checklist','ไม่มีผลต่างที่รับทราบในงวดนี้','ทุกช่องทางตรงตามจริง');return;}
  const over=s.over_count?`<span class="chip c-err">เกินเกณฑ์ ${s.over_count} รายการ</span>`:`<span class="chip c-ok">ไม่มีรายการเกินเกณฑ์ ✓</span>`;
  html+=`<div class="card" style="padding:12px 16px;margin-bottom:12px;display:flex;gap:30px;flex-wrap:wrap;align-items:center">
    <div><div class="lbl">ยอดรับทราบรวม</div><div class="kpi-v t-err">${fmt(s.grand_total_abs)}</div></div>
    <div><div class="lbl">จำนวนรายการ</div><div class="kpi-v">${s.count}</div></div>
    <div>${over}</div></div>
  <div class="c2 mb-12">
    <div class="card"><b>แยกตามเหตุผล</b><div class="ovx-auto mt-6"><table>
      <tr><th class="tl">เหตุผล</th><th>ครั้ง</th><th>ยอดรวม</th></tr>
      ${s.by_reason.map(r=>`<tr><td class="tl">${esc(r.reason)}</td><td class="mono">${r.count}</td><td class="mono">${fmt(r.total_abs)}</td></tr>`).join('')}
    </table></div></div>
    <div class="card"><b>แยกตามสาขา</b><div class="ovx-auto mt-6"><table>
      <tr><th class="tl">สาขา</th><th>ครั้ง</th><th>ยอดรวม</th></tr>
      ${s.by_branch.map(r=>`<tr><td class="tl"><b>${esc(r.branch_name)}</b></td><td class="mono">${r.count}</td><td class="mono">${fmt(r.total_abs)}</td></tr>`).join('')}
    </table></div></div></div>
  <div class="card p-0 ovx-auto"><table>
    <tr><th class="tl">สาขา</th><th class="tl">ช่องทาง / วัน</th><th>ยอด</th><th class="tl">เหตุผล</th><th class="tl">โดย</th></tr>
    ${s.items.map(it=>{const big=Math.abs(it.amount)>s.threshold;return `<tr>
      <td class="tl">${esc(it.branch_name)}</td>
      <td class="tl">${esc(it.scope)}</td>
      <td class="mono" style="${big?'color:var(--err);font-weight:700':''}">${it.amount>0?'+':''}${fmt(it.amount)}</td>
      <td class="tl">${it.reason?esc(it.reason):'<span class="t-err">— ไม่ระบุ —</span>'}</td>
      <td class="tl">${esc(it.by)}</td></tr>`;}).join('')}
  </table></div>`;
  $('app').innerHTML=html;
}

/* #auditlog — ประวัติการแก้ไข (Audit Trail) */
let _AT_BR='';
function setAtBranch(v){_AT_BR=v||'';loadAuditTrail();}
async function loadAuditTrail(){
  const t=++_ROUTE_SEQ;
  $('app').innerHTML=header()+loadingState();
  const q=`?period_month=${MONTH}${_AT_BR?`&branch_id=${_AT_BR}`:''}`;
  let brs,r;
  try{[brs,r]=await Promise.all([api('/api/branches'),api('/api/reports/audit-trail'+q)]);}
  catch(e){if(_cur(t))$('app').innerHTML=header()+`<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`;return;}
  if(!_cur(t))return;
  const actCls={'สร้าง':'c-ok','แก้ไข':'c-warn','ลบ':'c-err','รับทราบ':'c-mut'};
  let html=header()+`<div class="row mb-6">
    <h2 class="fs-16"><i class="ti ti-history" aria-hidden="true"></i> ประวัติการแก้ไข — ${esc(monthTh(MONTH))}</h2>
    ${_icBranchSel(brs,_AT_BR,'setAtBranch')}
    <a class="btn btn-sm btn-pri" href="/api/reports/audit-trail.print${q}" target="_blank"><i class="ti ti-printer" aria-hidden="true"></i> พิมพ์/PDF</a>
    <a class="btn btn-sm" href="/api/reports/audit-trail.xlsx${q}">Excel</a></div>
  <div class="muted mb-10">ทุกการเปลี่ยนแปลงที่ระบบบันทึก (ใคร·เมื่อ·แก้อะไร) — บันทึกแบบเพิ่มอย่างเดียว แก้/ลบไม่ได้ · แสดง ${r.shown} จาก ${r.count} รายการ</div>`;
  if(!r.rows.length){$('app').innerHTML=html+emptyState('history','ไม่มีการเปลี่ยนแปลงที่บันทึกไว้','ยังไม่มีการแก้ไขในงวดนี้');return;}
  html+=`<div class="card p-0 ovx-auto"><table>
    <tr><th class="tl">เวลา</th><th class="tl">สาขา</th><th class="tl">ประเภท</th><th class="tl">การกระทำ</th><th class="tl">รายละเอียด</th><th class="tl">โดย</th></tr>
    ${r.rows.map(x=>`<tr>
      <td class="lbl tl">${esc((x.ts||'').slice(0,16))}</td>
      <td class="tl">${esc(x.branch_name)}</td>
      <td class="tl">${esc(x.entity_th)}</td>
      <td class="tl"><span class="chip ${actCls[x.action_th]||'c-mut'}">${esc(x.action_th)}</span></td>
      <td class="tl">${esc(x.detail)}</td>
      <td class="tl">${esc(x.by)}</td></tr>`).join('')}
  </table></div>`;
  $('app').innerHTML=html;
}

/* ════════════════ END PHASE 5 ════════════════ */
