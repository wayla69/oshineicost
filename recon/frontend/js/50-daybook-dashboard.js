/* recon frontend — 50-daybook-dashboard.js: #daybook (one card per day) and #dashboard (all branches, one day).
   Plain scripts sharing one global scope, loaded in order by index.html (00 → 90);
   a slice may call any other slice's functions, but top-level code only uses earlier slices. */
/* ── <i class="ti ti-calendar" aria-hidden="true"></i> สรุปบัญชีรายวัน (รายใบ) — 1 วัน = 1 การ์ด, เลือกทีละสาขา ── */
let DB=null, DB_BR=null;
const fmt2=n=>(n==null?'—':Number(n).toLocaleString('th-TH',{minimumFractionDigits:2,maximumFractionDigits:2}));
const dbDed=n=>(Math.abs(+n||0)>0.005?'−'+fmt2(n):'—');   // ส่วนลด/ค่าธรรมเนียม/VAT แสดงเป็นยอดหัก
async function loadDaybook(){
  const t=++_ROUTE_SEQ;
  $('app').innerHTML=header()+loadingState();
  let r;
  try{ r=await api(`/api/daily-accounts?period_month=${MONTH}`+(DB_BR?`&branch_id=${DB_BR}`:'')); }
  catch(e){ if(_cur(t))$('app').innerHTML=header()+`<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`; return; }
  if(!_cur(t))return;
  DB=r; DB_BR=r.branch_id; renderDaybook();
}
function setDbBranch(v){ DB_BR=+v; loadDaybook(); }
/* ร่างบันทึกบัญชี (Dr/Cr) ของวันนั้น — เอาไปลงบัญชีรายวันได้เลย */
function dbJournal(t){
  const A=(DB&&DB.je_accounts)||{};
  const code=s=>(A[s]&&A[s][0])?` <span class="lbl">${esc(A[s][0])}</span>`:'';
  const dr=(s,label,v)=>`<tr><td>${label}${code(s)}</td><td class="mono">${fmt2(v)}</td><td class="mono">—</td></tr>`;
  const lines=[dr('BANK','เงินสด/ธนาคาร (สุทธิ)',t.net)];
  if(Math.abs(+t.discount||0)>0.005)lines.push(dr('DISCOUNT','ส่วนลด/โปรโมชัน',t.discount));
  if(Math.abs(+t.fee||0)>0.005)     lines.push(dr('FEE','ค่าธรรมเนียมรับชำระเงิน',t.fee));
  if(Math.abs(+t.vat||0)>0.005)     lines.push(dr('VAT','ภาษีซื้อ–VAT ค่าธรรมเนียม',t.vat));
  lines.push(`<tr><td>รายได้จากการขาย (เต็ม)${code('REVENUE')}</td><td class="mono">—</td><td class="mono">${fmt2(t.gross)}</td></tr>`);
  const dtot=(+t.net||0)+(+t.discount||0)+(+t.fee||0)+(+t.vat||0);
  return `<details class="db-je">
    <summary><i class="ti ti-notebook" aria-hidden="true"></i> ร่างบันทึกบัญชีวันนี้ (Dr/Cr)</summary>
    <table class="db-tbl"><thead><tr><th>บัญชี</th><th>เดบิต</th><th>เครดิต</th></tr></thead>
      <tbody>${lines.join('')}</tbody>
      <tfoot><tr><td>รวม</td><td class="mono"><b>${fmt2(dtot)}</b></td><td class="mono"><b>${fmt2(t.gross)}</b></td></tr></tfoot>
    </table></details>`;
}
function dbDayCard(d){
  const t=d.totals;
  const row=c=>`<tr>
    <td>${esc(c.ch_th)}${c.code?` <span class="lbl">${esc(c.code)}</span>`:''}</td>
    <td class="mono">${fmt2(c.gross)}</td>
    <td class="mono db-disc">${dbDed(c.discount)}</td>
    <td class="mono">${dbDed(c.fee)}</td>
    <td class="mono">${dbDed(c.vat)}</td>
    <td class="mono"><b>${fmt2(c.net)}</b></td></tr>`;
  return `<div class="card db-card">
    <div class="db-head"><div class="db-date">วันที่ ${d.dom}</div>
      <div class="db-net">สุทธิ ฿${fmt2(t.net)}</div></div>
    <table class="db-tbl"><thead><tr><th>ช่องทาง</th><th>ยอดเต็ม</th><th>ส่วนลด</th>
      <th>ค่าธรรมเนียม</th><th>VAT</th><th>สุทธิ</th></tr></thead>
      <tbody>${d.channels.map(row).join('')}</tbody>
      <tfoot><tr><td>รวมวันนี้</td><td class="mono">${fmt2(t.gross)}</td>
        <td class="mono db-disc">${dbDed(t.discount)}</td><td class="mono">${dbDed(t.fee)}</td>
        <td class="mono">${dbDed(t.vat)}</td><td class="mono"><b>${fmt2(t.net)}</b></td></tr></tfoot>
    </table>${dbJournal(t)}</div>`;
}
function renderDaybook(){
  const r=DB, t=r.totals||{};
  const pm=`?period_month=${MONTH}&branch_id=${r.branch_id}`;
  const brOpts=(r.branches||[]).map(b=>`<option value="${b.branch_id}" ${b.branch_id===r.branch_id?'selected':''}>${esc(b.name)}</option>`).join('');
  let html=header()+`<div class="row mb-6">
    <h2 class="fs-16"><i class="ti ti-calendar" aria-hidden="true"></i> สรุปบัญชีรายวัน (รายใบ) — ${esc(monthTh(MONTH))}</h2>
    <select onchange="setDbBranch(this.value)" title="เลือกสาขา" class="ctl ml-auto">${brOpts}</select>
    <a class="btn btn-sm btn-pri" href="/api/daily-accounts.print${pm}" target="_blank"><i class="ti ti-printer" aria-hidden="true"></i> พิมพ์/PDF</a></div>
  <div class="muted mb-12">แต่ละวัน = <b>1 ใบบันทึกบัญชี</b> · บันทึกเป็น <b>ยอดเต็ม (gross)</b> แล้ว <b>แยกบรรทัดส่วนลด</b> · สุทธิ = ยอดเต็ม − ส่วนลด − ค่าธรรมเนียม − VAT · รหัสบัญชีตั้งที่ <a href="#setup"><i class="ti ti-settings" aria-hidden="true"></i> ตั้งค่าระบบ</a></div>`;
  if(!(r.days||[]).length){ $('app').innerHTML=html+emptyState('calendar-off','ยังไม่มีรายการบัญชีของสาขานี้ในเดือนนี้','ต้องมีข้อมูล POS/เอกสารของเดือนนี้ก่อน'); return; }
  html+=`<div class="card db-sum">
    <div><div class="lbl">ยอดเต็มรวม</div><b>฿${fmt2(t.gross)}</b></div>
    <div><div class="lbl">ส่วนลดรวม</div><b class="db-disc">฿${fmt2(t.discount)}</b></div>
    <div><div class="lbl">ค่าธรรมเนียม</div><b>฿${fmt2(t.fee)}</b></div>
    <div><div class="lbl">VAT</div><b>฿${fmt2(t.vat)}</b></div>
    <div><div class="lbl">สุทธิเข้าบัญชี</div><b>฿${fmt2(t.net)}</b></div>
    <div class="lbl ml-auto as-center">${r.days.length} ใบ (วัน)</div></div>`;
  html+=`<div class="db-grid">${r.days.map(dbDayCard).join('')}</div>`;
  $('app').innerHTML=html;
}

/* ── <i class="ti ti-chart-bar" aria-hidden="true"></i> Dashboard รายได้ตามช่องทางรายวัน (ทุกสาขาในวันเดียว · เลือกวันจากปฏิทิน) ── */
// ค่าเริ่มต้น = เมื่อวาน (วันที่ปิดแล้ว) ตามเวลาเครื่อง — วันนี้ยังขายอยู่ ตัวเลขยังไม่ครบ
let DASH=null, DASH_DAY=addDays(ymdLocal(),-1), DASH_MODE='acct';  // acct | pos | cmp
const CH_COLOR={CASH:'#1f3a6b',QR:'#2563eb',TRANSFER:'#0891b2',CREDIT:'#7c3aed',
                GRAB:'#16a34a',LINEMAN:'#ea580c',COUPON:'#ca8a04',DISCOUNT:'#dc2626'};
const _dvarCol=v=>(Math.abs(+v||0)<=1?'var(--mut)':(+v<0?'var(--err)':'var(--brand-deep)'));
const _dvar=v=>{const n=+v||0; return Math.abs(n)<=1?'<span class="lbl">✓</span>':`<span style="color:${_dvarCol(n)}">${n>0?'+':''}${fmt2(n)}</span>`;};
async function loadDashboard(){
  const t=++_ROUTE_SEQ;
  $('app').innerHTML=header()+loadingState();
  let r;
  try{ r=await api(`/api/daily-dashboard?day=${DASH_DAY}`); }
  catch(e){ if(_cur(t))$('app').innerHTML=header()+`<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`; return; }
  if(!_cur(t))return;
  DASH=r; renderDashboard();
}
function setDashDay(v){ if(!v)return; DASH_DAY=v; loadDashboard(); }
function setDashMode(m){ DASH_MODE=m; renderDashboard(); }   // client-side — data already loaded
function _dashDot(ch){ return `<span class="dash-dot" style="background:${CH_COLOR[ch]||'var(--mut)'}"></span>`; }
function dashBranchCard(b){
  const empty=(rows)=>`<div class="card db-card dash-empty"><div class="db-head"><div class="db-date">${esc(b.name)}</div>
      <div class="db-net lbl">— ไม่มีรายการวันนี้</div></div></div>`;
  if(DASH_MODE==='acct'){
    const t=b.totals||{};
    if(!(b.channels||[]).length) return empty();
    const row=c=>`<tr><td>${_dashDot(c.ch)}${esc(c.ch_th)}</td>
      <td class="mono">${fmt2(c.gross)}</td><td class="mono db-disc">${dbDed(c.discount)}</td>
      <td class="mono"><b>${fmt2(c.net)}</b></td></tr>`;
    return `<div class="card db-card">
      <div class="db-head"><div class="db-date">${esc(b.name)}</div><div class="db-net">฿${fmt2(t.net)}</div></div>
      <table class="db-tbl"><thead><tr><th>ช่องทาง</th><th>ยอดเต็ม</th><th>ส่วนลด</th><th>สุทธิ</th></tr></thead>
        <tbody>${b.channels.map(row).join('')}</tbody>
        <tfoot><tr><td>รวมสาขา</td><td class="mono">${fmt2(t.gross)}</td>
          <td class="mono db-disc">${dbDed(t.discount)}</td><td class="mono"><b>${fmt2(t.net)}</b></td></tr></tfoot>
      </table></div>`;
  }
  // POS-sourced modes
  const pc=b.pos_channels||[], pt=b.pos_totals||{pos:0,money:0,variance:0};
  if(!pc.length) return empty();
  if(DASH_MODE==='pos'){
    const row=c=>`<tr><td>${_dashDot(c.ch)}${esc(c.ch_th)}</td><td class="mono"><b>${fmt2(c.pos)}</b></td></tr>`;
    return `<div class="card db-card">
      <div class="db-head"><div class="db-date">${esc(b.name)}</div><div class="db-net">฿${fmt2(pt.pos)}</div></div>
      <table class="db-tbl"><thead><tr><th>ช่องทาง</th><th>ยอด POS</th></tr></thead>
        <tbody>${pc.map(row).join('')}</tbody>
        <tfoot><tr><td>รวมสาขา</td><td class="mono"><b>${fmt2(pt.pos)}</b></td></tr></tfoot>
      </table></div>`;
  }
  // cmp: POS ↔ เงินเข้าจริง + ผลต่าง
  const row=c=>`<tr><td>${_dashDot(c.ch)}${esc(c.ch_th)}</td>
    <td class="mono">${fmt2(c.pos)}</td><td class="mono">${fmt2(c.money)}</td>
    <td class="mono">${_dvar(c.variance)}</td></tr>`;
  return `<div class="card db-card">
    <div class="db-head"><div class="db-date">${esc(b.name)}</div><div class="db-net" style="color:${_dvarCol(pt.variance)}">ต่าง ${pt.variance>0?'+':''}${fmt2(pt.variance)}</div></div>
    <table class="db-tbl"><thead><tr><th>ช่องทาง</th><th>POS</th><th>เงินเข้า</th><th>ต่าง</th></tr></thead>
      <tbody>${pc.map(row).join('')}</tbody>
      <tfoot><tr><td>รวมสาขา</td><td class="mono">${fmt2(pt.pos)}</td>
        <td class="mono">${fmt2(pt.money)}</td><td class="mono">${_dvar(pt.variance)}</td></tr></tfoot>
    </table></div>`;
}
function renderDashboard(){
  const r=DASH, g=r.grand_totals||{}, pg=r.pos_grand||{pos:0,money:0,variance:0};
  const acct=DASH_MODE==='acct';
  const brs=(r.branches||[]).slice().sort((a,b)=> acct
    ? (((b.totals||{}).net||0)-((a.totals||{}).net||0))
    : (((b.pos_totals||{}).pos||0)-((a.pos_totals||{}).pos||0)));
  const active=brs.filter(b=> acct ? (b.channels||[]).length : (b.pos_channels||[]).length);
  const seg=(m,label)=>`<button class="btn btn-sm ${DASH_MODE===m?'btn-pri':''}" onclick="setDashMode('${m}')">${label}</button>`;
  let html=header()+`<div class="row mb-6 ai-center">
    <h2 class="fs-16"><i class="ti ti-chart-bar" aria-hidden="true"></i> รายได้ตามช่องทาง — รายวัน (ทุกสาขา)</h2>
    <input type="date" value="${esc(DASH_DAY)}" onchange="setDashDay(this.value)" title="เลือกวันจากปฏิทิน"
      class="ctl-wide ml-auto">
  </div>
  <div class="row gap-6 mb-10">${seg('acct','เงินเข้าจริง (บัญชี)')}${seg('pos','ยอด POS')}${seg('cmp','ประกบ POS ↔ เงินเข้า')}</div>
  <div class="muted mb-12">${acct
    ? 'บันทึก <b>ยอดเต็ม</b> แล้วแยกบรรทัด <b>ส่วนลด</b> · คูปอง KTC/BAY (แต้ม) นับเป็นเงินรับจริง — คูปองส่วนลดอื่น (ผู้สูงอายุ/เด็ก/ฯลฯ) เป็นยอดหัก'
    : DASH_MODE==='pos' ? 'ยอดที่เครื่อง <b>POS บันทึก</b> แยกช่องทาง (ก่อนกระทบกับเงินเข้าจริง)'
    : 'ประกบ <b>ยอด POS</b> กับ <b>เงินเข้าจริง</b> (เงินที่เข้าบัญชี/ค่ายบัตรตาม statement·settlement) · <b>ต่าง</b> = เงินเข้า − POS (ช่องที่ยังไม่ได้อัปเอกสารจะเข้า = 0 → ต่างติดลบเท่ายอด POS)'}</div>`;
  if(!active.length){ $('app').innerHTML=html+emptyState('calendar-off',`ยังไม่มีรายการของวันที่ ${esc(DASH_DAY)}`,'เลือกวันอื่น หรือซิงก์ข้อมูล POS ของวันนั้นก่อน'); return; }
  if(acct){
    html+=`<div class="card db-sum">
      <div><div class="lbl">ยอดเต็มรวมทุกสาขา</div><b>฿${fmt2(g.gross)}</b></div>
      <div><div class="lbl">ส่วนลดรวม</div><b class="db-disc">฿${fmt2(g.discount)}</b></div>
      <div><div class="lbl">ค่าธรรมเนียม</div><b>฿${fmt2(g.fee)}</b></div>
      <div><div class="lbl">VAT</div><b>฿${fmt2(g.vat)}</b></div>
      <div><div class="lbl">สุทธิเข้าบัญชีรวม</div><b>฿${fmt2(g.net)}</b></div>
      <div class="lbl ml-auto as-center">${active.length}/${brs.length} สาขามีรายได้</div></div>`;
  } else {
    html+=`<div class="card db-sum">
      <div><div class="lbl">ยอด POS รวมทุกสาขา</div><b>฿${fmt2(pg.pos)}</b></div>
      <div><div class="lbl">เงินเข้าจริงรวม</div><b>฿${fmt2(pg.money)}</b></div>
      <div><div class="lbl">ผลต่างรวม</div><b style="color:${_dvarCol(pg.variance)}">${pg.variance>0?'+':''}฿${fmt2(pg.variance)}</b></div>
      <div class="lbl ml-auto as-center">${active.length}/${brs.length} สาขา</div></div>`;
  }
  html+=`<div class="db-grid">${brs.map(dashBranchCard).join('')}</div>`;
  $('app').innerHTML=html;
}
