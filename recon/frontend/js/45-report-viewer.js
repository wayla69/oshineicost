/* recon frontend — 45-report-viewer.js: the report registry (RPT_PACKS · RPT_GROUPS · RPTS — the ONE list the #reports hub
   and the viewer both read) and the in-app report viewer #report/<key> (a server HTML report in a same-origin iframe).
   Plain scripts sharing one global scope, loaded in order by index.html (00 → 90);
   a slice may call any other slice's functions, but top-level code only uses earlier slices.
   Read-only: nothing here writes. Reports are grouped by WHO receives them (UX phase 3, part 6). */

/* RPT-REGISTRY:BEGIN — recon/tests/test_report_packs.py reads this block: every /api path must be a GET route,
   every pack/group must hold an entry, every screen must be a route() target. */
const RPT_PACKS=[
  {key:'branch', icon:'building-store', title:'ส่งสาขา', reader:'ผู้จัดการสาขา · แคชเชียร์',
   note:'ส่งให้สาขาเซ็นรับทราบ แก้ไข และใช้โค้ชพนักงาน'},
  {key:'manager', icon:'user-check', title:'ผู้จัดการ', reader:'ผู้จัดการ — ก่อนและหลังปิดเดือน',
   note:'ตรวจก่อนเซ็นปิดเดือน และติดตามหลังปิด'},
  {key:'acct', icon:'calculator', title:'บัญชี', reader:'ฝ่ายบัญชี — ลงบัญชี',
   note:'ยอดเต็ม · ส่วนลด · ค่าธรรมเนียม · VAT · ร่างบันทึกบัญชี (Dr/Cr)'},
  {key:'auditor', icon:'shield-check', title:'ผู้สอบบัญชี', reader:'ผู้สอบบัญชี · ผู้ตรวจสอบภายใน',
   note:'หลักฐานการควบคุม อ่านอย่างเดียว ส่งได้ทั้งชุด'},
];
const RPT_GROUPS=[
  {key:'tools', icon:'tool', title:'งานประจำ / เครื่องมือ', note:'หน้าจอทำงานและเอกสารอ้างอิง — ไม่ใช่รายงานส่งออก'},
  {key:'diag', icon:'activity', title:'ตรวจระบบ', note:'เครื่องมือตรวจเชิงลึกสำหรับผู้ดูแล — เปิดจาก Admin ได้ด้วย'},
];
// ?period_month=…[&branch_id=…]…  — p comes from rptParams(): p.branch is '' unless the entry takes a branch
const _rq=(p,extra)=>`?period_month=${encodeURIComponent(p.month||'')}${p.branch?`&branch_id=${encodeURIComponent(p.branch)}`:''}${extra||''}`;
/* entry: pack · icon · title · desc · branch ('opt' = ทุกสาขา or one · 'req' = one branch needed · omitted = no branch)
   · url(p) = server HTML shown in the viewer · xlsx(p) / zip(p) = plain download links (same origin, cookie carried)
   · session:true = the download needs the branch's session id (p.session) · screen = an in-app screen (no iframe)
   · month:false = not month-scoped · roles = only these roles see it (none yet: every role can open every report) */
const RPTS={
  // ── ส่งสาขา ──
  wrong:{pack:'branch', icon:'arrows-exchange', title:'เงินบันทึกผิดช่อง + ใบเซ็นรับทราบ', branch:'opt', screen:'#wrong',
    desc:'ข้อผิดพลาดทุกประเภทของเดือน + ใบให้สาขาเซ็นรับทราบ หน้าละสาขา',
    url:p=>'/api/reports/wrong-channel.print'+_rq(p), xlsx:p=>'/api/reports/wrong-channel.xlsx'+_rq(p)},
  errors:{pack:'branch', icon:'clipboard-list', title:'ข้อผิดพลาดการบันทึก (รายสาขา/พนักงาน)', branch:'opt', screen:'#errors',
    desc:'ช่องทางที่สาขากระทบยอดไม่ตรงจนต้องรับทราบ · ใช้ประเมิน/โค้ชพนักงาน',
    url:p=>'/api/reports/branch-errors.print'+_rq(p), xlsx:p=>'/api/reports/branch-errors.xlsx'+_rq(p)},
  // ── ผู้จัดการ ──
  certificate:{pack:'manager', icon:'certificate', title:'ใบรับรองการกระทบยอด', branch:'opt', screen:'#certificate',
    desc:'POS ↔ เงินเข้า ↔ ผลต่างคงเหลือ · ผู้จัดทำ/ผู้สอบทาน · ทุกสาขา = หน้าละสาขาสำหรับเซ็น',
    url:p=>'/api/reports/recon-certificate.print'+_rq(p)},
  override:{pack:'manager', icon:'writing', title:'ทะเบียนการปรับปรุงด้วยมือ', branch:'opt', screen:'#override',
    desc:'ทุกการแก้ด้วยมือ · ใคร·เมื่อ·เหตุผล · รายการไม่มีเหตุผลเน้นแดง',
    url:p=>'/api/reports/override-register.print'+_rq(p), xlsx:p=>'/api/reports/override-register.xlsx'+_rq(p)},
  closereg:{pack:'manager', icon:'calendar-check', title:'ทะเบียนการปิดเดือน', branch:'opt', screen:'#closereg',
    desc:'ทุกสาขา × 6 เดือน · ปิด/อนุมัติ/ยังเปิด · ใครอนุมัติ · มีหลักฐานแช่แข็งไหม',
    url:p=>'/api/reports/close-register.print'+_rq(p,'&months=6'), xlsx:p=>'/api/reports/close-register.xlsx'+_rq(p,'&months=6')},
  aging:{pack:'manager', icon:'hourglass-high', title:'อายุรายการค้าง', branch:'opt', screen:'#aging',
    desc:'รายการยังไม่ปิด แยก 0–7 / 8–30 / เกิน 30 วัน · ย้อนหลัง 2 เดือน',
    url:p=>'/api/reports/open-items-aging.print'+_rq(p,'&lookback=2'), xlsx:p=>'/api/reports/open-items-aging.xlsx'+_rq(p,'&lookback=2')},
  accepted:{pack:'manager', icon:'receipt-off', title:'ผลต่างที่รับทราบ (เดือนนี้)', branch:'opt', screen:'#accepted',
    desc:'Write-off แยกตามเหตุผล·สาขา · เน้นรายการเกิน ฿1,000',
    url:p=>'/api/reports/accepted-variance.print'+_rq(p,'&threshold=1000'), xlsx:p=>'/api/reports/accepted-variance.xlsx'+_rq(p,'&threshold=1000')},
  // ── บัญชี ──
  daybook:{pack:'acct', icon:'calendar', title:'สรุปบัญชีรายวัน (Dr/Cr)', branch:'opt', screen:'#daybook',
    desc:'1 วัน = 1 ใบ · ยอดเต็ม/ส่วนลด/ค่าธรรมเนียม/VAT + ร่างบันทึกบัญชีของวัน · ทุกสาขา = หน้าละสาขา',
    url:p=>'/api/daily-accounts.print'+_rq(p)},
  matching:{pack:'acct', icon:'list-check', title:'รายงานการจับคู่รายวัน', branch:'opt',
    desc:'ทุกวัน × ช่องทาง POS เทียบเอกสาร + สรุปรับเงินทั้งเดือน + ร่างบันทึกบัญชี (Dr/Cr)',
    url:p=>'/api/monthly-report'+_rq(p)},
  closexlsx:{pack:'acct', icon:'file-spreadsheet', title:'Excel ทั้งเดือน (กระดาษทำการปิดเดือน)', branch:'req', session:true,
    desc:'ไฟล์ Excel ทั้งเดือนของสาขา — ชุดเดียวกับที่แช่แข็งตอนปิดเดือน',
    xlsx:p=>`/api/sessions/${encodeURIComponent(p.session)}/export.xlsx`},
  // ── ผู้สอบบัญชี ──
  auditorpack:{pack:'auditor', icon:'package', title:'ชุดเอกสารผู้สอบ (Auditor Pack)', branch:'opt',
    desc:'ZIP เดียว: ใบรับรอง · ทะเบียนปรับปรุง · อายุรายการค้าง · ทะเบียนปิดเดือน · ผลต่างที่รับทราบ · ประวัติการแก้ไข',
    zip:p=>'/api/reports/auditor-pack.zip'+_rq(p)},
  audittrail:{pack:'auditor', icon:'history', title:'ประวัติการแก้ไข (Audit Trail)', branch:'opt', screen:'#auditlog',
    desc:'ใคร·เมื่อ·แก้อะไร ทุกการเปลี่ยนแปลง · เพิ่มอย่างเดียว แก้/ลบไม่ได้',
    url:p=>'/api/reports/audit-trail.print'+_rq(p), xlsx:p=>'/api/reports/audit-trail.xlsx'+_rq(p)},
  acceptedyear:{pack:'auditor', icon:'receipt-tax', title:'ผลต่างที่รับทราบทั้งปี', branch:'opt',
    desc:'มกราคม → เดือนที่เลือก · รายเดือน แยกสาขา แยกเหตุผล · เน้นรายการเกิน ฿1,000',
    url:p=>'/api/reports/accepted-variance-year.print'+_rq(p,'&threshold=1000'), xlsx:p=>'/api/reports/accepted-variance-year.xlsx'+_rq(p,'&threshold=1000')},
  matchdetail:{pack:'auditor', icon:'files', title:'รายงานการจับคู่ ฉบับตรวจสอบ (ละเอียด)', branch:'req',
    desc:'หลักฐานระดับบรรทัด: รหัส POS · บัตรราย acquirer · ทุกบรรทัดเงินเข้า · การจับคู่ด้วยมือ',
    url:p=>'/api/monthly-report'+_rq(p,'&detail=1')},
  // ── งานประจำ / เครื่องมือ ──
  dashboard:{pack:'tools', icon:'chart-bar', title:'Dashboard รายได้รายวัน', screen:'#dashboard',
    desc:'ทุกสาขา × ช่องทางในวันเดียว · เลือกวันจากปฏิทิน'},
  outstanding:{pack:'tools', icon:'inbox', title:'ติดตามเอกสารที่ยังขาด', branch:'opt',
    desc:'รายสาขา · เอกสารที่ต้องอัป + ยอดที่รอเอกสาร · มีช่องติ๊ก/เซ็นให้สาขา',
    url:p=>'/api/reports/outstanding-docs.print'+_rq(p)},
  coupon:{pack:'tools', icon:'ticket', title:'คูปองซีรีส์', screen:'#coupon',
    desc:'ส่วนลดแยกตามซีรีส์คูปอง · MoM · รายสาขา/รายวัน'},
  codes:{pack:'tools', icon:'book', title:'พจนานุกรมรหัสสเตทเมนต์', month:false,
    desc:'รหัสธนาคาร → ความหมาย → ช่องทาง · ใบให้ทีมตรวจ',
    url:()=>'/api/statement-codes.html', xlsx:()=>'/api/statement-codes.xlsx'},
  uat:{pack:'tools', icon:'circle-check', title:'ใบทดสอบ + รับรองการใช้งาน (UAT)',
    desc:'เช็กลิสต์ทดสอบทีละข้อ + ช่องเซ็นรับรอง',
    url:p=>'/api/uat-signoff'+_rq(p)},
  // ── ตรวจระบบ (ย้ายออกจากแถบนำทาง — เปิดจากหน้านี้และจาก Admin) ──
  board:{pack:'diag', icon:'layout-grid', title:'Daily Board', screen:'#board',
    desc:'ทุกสาขา × ช่องทางในวันเดียว + รายงานหลักฐานรายวัน (PDF)'},
  trend:{pack:'diag', icon:'calendar-stats', title:'Trend Heatmap', screen:'#trend',
    desc:'% จับคู่รายวันของสาขา · สรุปรายสัปดาห์'},
  audit:{pack:'diag', icon:'list-details', title:'Audit Log (ระบบ)', screen:'#audit',
    desc:'200 รายการล่าสุด · กรองตาม entity · ค่าเดิม → ใหม่'},
  bankcov:{pack:'diag', icon:'building-bank', title:'Bank Coverage', screen:'#bankcov',
    desc:'ทุกบรรทัดสเตทเมนต์: ตรงแล้ว / ยังรอ / ตัดออก'},
};
/* RPT-REGISTRY:END */

// the params an entry's builders see: no branch for an entry that takes none, no month for month:false
function rptParams(e,month,branch,session){
  return {month:e.month===false?'':(month||''), branch:(e.branch==='opt'||e.branch==='req')?(branch||''):'', session:session||''};
}
function rptLinks(key,month,branch,session){
  const e=RPTS[key], p=rptParams(e,month,branch,session);
  return {url:e.url?e.url(p):'', xlsx:e.xlsx&&(!e.session||p.session)?e.xlsx(p):'', zip:e.zip?e.zip(p):''};
}
function rptAllowed(e){ return !e.roles||!(window.ME&&ME.login_enabled)||e.roles.includes(_role()); }
function rptKeys(pack){ return Object.keys(RPTS).filter(k=>RPTS[k].pack===pack&&rptAllowed(RPTS[k])); }
const rptPackOf=k=>RPT_PACKS.find(x=>x.key===k)||RPT_GROUPS.find(x=>x.key===k)||{title:'',reader:''};

/* the branch chosen once for every report ('' = ทุกสาขา) — a per-viewer convenience, so browser storage */
let RPT_BR=(()=>{try{return localStorage.getItem('rpt_branch')||'';}catch(e){return '';}})();
function setRptBranch(v){ RPT_BR=v||''; try{localStorage.setItem('rpt_branch',RPT_BR);}catch(e){} route(); }
function _rptCheckBranch(brs){ if(RPT_BR&&brs.length&&!brs.some(b=>String(b.branch_id)===String(RPT_BR)))RPT_BR=''; }
const _rptBrName=brs=>{const b=brs.find(x=>String(x.branch_id)===String(RPT_BR));return b?b.name:'';};
function rptBranchSel(brs,req){
  return `<select class="ctl" onchange="setRptBranch(this.value)" aria-label="สาขา" title="สาขา">
    <option value="">${req?'— เลือกสาขา —':'ทุกสาขา'}</option>${brs.map(b=>`<option value="${b.branch_id}" ${String(b.branch_id)===String(RPT_BR)?'selected':''}>${esc(b.name)}</option>`).join('')}</select>`;
}

// a session-scoped download (Excel ปิดเดือน): resolve the branch's session for the month, then follow the link
async function rptDownload(key,btn){
  const e=RPTS[key];
  if(!RPT_BR){toast('เลือกสาขาก่อน');return;}
  await busy('rpt-dl-'+key,btn,async()=>{
    let sid=null;
    try{sid=await _resolveSession(+RPT_BR,MONTH);}catch(err){toast('โหลดไม่สำเร็จ: '+err.message);return;}
    if(!sid){toast('สาขานี้ยังไม่มีงวดของ '+monthTh(MONTH)+' — เปิดหน้าสาขาเพื่อเริ่มงวดก่อน');return;}
    const l=rptLinks(key,MONTH,RPT_BR,sid);
    if(l.xlsx)location.href=l.xlsx;
    else if(e&&e.url)location.hash='report/'+key;
  });
}

/* ── #report/<key> — in-app viewer ── */
function _rvActions(key,e,l){
  const dl=[];
  if(l.xlsx)dl.push(`<a class="btn btn-sm" href="${esc(l.xlsx)}" download><i class="ti ti-file-spreadsheet" aria-hidden="true"></i> Excel</a>`);
  if(l.zip)dl.push(`<a class="btn btn-sm" href="${esc(l.zip)}" download><i class="ti ti-file-zip" aria-hidden="true"></i> ZIP</a>`);
  return (l.url?`<button class="btn btn-sm btn-pri" onclick="rvPrint()"><i class="ti ti-printer" aria-hidden="true"></i> พิมพ์ / PDF</button>
      <a class="btn btn-sm" href="${esc(l.url)}" target="_blank" rel="noopener"><i class="ti ti-external-link" aria-hidden="true"></i> เปิดในแท็บใหม่</a>`:'')
    +dl.join('')
    +(e.screen?`<a class="btn btn-sm" href="${esc(e.screen)}" title="หน้าจอแบบโต้ตอบในแอป"><i class="ti ti-layout-dashboard" aria-hidden="true"></i> หน้าจอโต้ตอบ</a>`:'');
}
function _rvBar(key,e,brs,l){
  const pk=rptPackOf(e.pack);
  const scope=[pk.title+(pk.reader?' · ผู้รับ: '+pk.reader:''),e.month===false?'':monthTh(MONTH),e.branch?(_rptBrName(brs)||(e.branch==='req'?'':'ทุกสาขาหลัก')):''].filter(Boolean).join(' · ');
  return `<div class="card rv-bar">
    <a class="btn btn-sm" href="#reports"><i class="ti ti-arrow-left" aria-hidden="true"></i> รายงาน</a>
    <div class="rv-t"><h2 class="rv-title"><i class="ti ti-${e.icon}" aria-hidden="true"></i> ${esc(e.title)}</h2><div class="lbl">${esc(scope)}</div></div>
    <div class="rv-ctl">
      ${e.month===false?'':`<input type="month" class="ctl" value="${esc(MONTH)}" onchange="setMonth(this.value)" aria-label="เดือน" title="เดือน">`}
      ${e.branch?rptBranchSel(brs,e.branch==='req'):''}
    </div>
    <div class="rv-act">${_rvActions(key,e,l)}</div>
  </div>`;
}
async function loadReportViewer(key){
  const e=RPTS[key];
  if(!e||!rptAllowed(e)){$('app').innerHTML=header()+emptyState('file-off','ไม่พบรายงานนี้','ลิงก์อาจเก่า — เลือกรายงานจากหน้ารายงาน',`<a class="btn" href="#reports">← หน้ารายงาน</a>`);return;}
  if(e.screen&&!e.url&&!e.xlsx&&!e.zip){location.replace(e.screen);return;}     // an in-app screen, not a document
  const t=++_ROUTE_SEQ;
  $('app').innerHTML=header()+loadingState();
  const brs=e.branch?await _diagBranches():[];
  if(!_cur(t))return;
  _rptCheckBranch(brs);
  let sid='';
  if(e.session&&RPT_BR){
    try{sid=await _resolveSession(+RPT_BR,MONTH)||'';}catch(err){sid='';}
    if(!_cur(t))return;
  }
  const l=(e.branch==='req'&&!RPT_BR)?{url:'',xlsx:'',zip:''}:rptLinks(key,MONTH,RPT_BR,sid);   // one branch first, then the links
  let body;
  if(e.branch==='req'&&!RPT_BR)body=emptyState('building-store','เลือกสาขาเพื่อเปิดรายงานนี้','รายงานนี้ออกทีละสาขา — เลือกสาขาจากช่องด้านบน');
  else if(e.session&&!sid)body=emptyState('calendar-off','สาขานี้ยังไม่มีงวดของ '+esc(monthTh(MONTH)),'เปิดหน้าสาขาเพื่อเริ่มงวดก่อน แล้วกลับมาดาวน์โหลด');
  else if(!l.url)body=`<div class="card rv-dl"><div class="row gap-10"><i class="ti ti-${e.icon} rv-dl-i" aria-hidden="true"></i>
      <div class="fx-1 minw-200"><b>${esc(e.title)}</b><div class="muted">${esc(e.desc||'')}</div></div></div>
      ${key==='auditorpack'?`<ol class="rv-dl-list"><li>ใบรับรองการกระทบยอด (HTML เซ็นได้)</li><li>ทะเบียนการปรับปรุงด้วยมือ</li><li>อายุรายการค้าง</li>
        <li>ทะเบียนการปิดเดือน</li><li>สรุปผลต่างที่รับทราบ</li><li>ประวัติการแก้ไข</li><li>README อธิบายแต่ละไฟล์</li></ol>`:''}
      <div class="row gap-8 mt-6">${l.zip?`<a class="btn btn-pri" href="${esc(l.zip)}" download><i class="ti ti-download" aria-hidden="true"></i> ดาวน์โหลด ZIP</a>`:''}
        ${l.xlsx?`<a class="btn btn-pri" href="${esc(l.xlsx)}" download><i class="ti ti-download" aria-hidden="true"></i> ดาวน์โหลด Excel</a>`:''}</div></div>`;
  else body=`<div class="rv-frame" id="rv-wrap"><div class="loading" id="rv-load"><span class="spin" aria-hidden="true"></span>กำลังเปิดรายงาน…</div>
      <iframe id="rv-frame" title="${esc(e.title)}" src="${esc(l.url)}"></iframe></div>`;
  $('app').innerHTML=header()+_rvBar(key,e,brs,l)+body;
  const f=$('rv-frame');
  if(f)f.addEventListener('load',()=>{ if(_cur(t))_rvLoaded(f); });
}
const _rvPhone=()=>!!(window.matchMedia&&matchMedia('(max-width:640px)').matches);
// the report's own height (body, not the viewport-stretched root) — the frame grows to it; phones keep a fixed frame that scrolls inside
function _rvFit(f){
  if(_rvPhone()){f.style.height='';return;}
  try{
    const d=f.contentDocument, b=d.body, cs=f.contentWindow.getComputedStyle(b);
    let h=Math.ceil(b.getBoundingClientRect().height+parseFloat(cs.marginTop||'0')+parseFloat(cs.marginBottom||'0'));
    if(d.documentElement.scrollWidth>d.documentElement.clientWidth)h+=18;     // room for the sideways scrollbar
    f.style.height=Math.max(360,h)+'px';
  }catch(err){}
}
function _rvLoaded(f){
  const ld=$('rv-load'); if(ld)ld.remove();
  let d=null; try{d=f.contentDocument;}catch(err){}
  if(!d||!d.body)return;
  const ct=d.contentType||'';
  if(!ct.includes('html')){                       // an error answer (JSON detail) instead of the report
    let msg=(d.body.innerText||'').slice(0,400);
    try{msg=detailText(JSON.parse(d.body.innerText).detail)||msg;}catch(err){}
    const w=$('rv-wrap'); if(w)w.innerHTML=`<div class="card c-err">เปิดรายงานไม่สำเร็จ: ${esc(msg)}</div>`;
    if(window.ME&&ME.login_enabled)loadMe().then(()=>{if(!ME.user)route();});    // session ended → the login screen
    return;
  }
  // inside the app the viewer's toolbar replaces the report's own (pickers + print button); the paper floats on the app background
  const st=d.createElement('style');
  st.textContent='.toolbar{display:none!important}body{background:transparent!important;padding:10px 0 14px!important}'
    +(_isDark()?':root{color-scheme:dark}':'')+'@media print{:root{color-scheme:light}body{padding:0!important}}';
  (d.head||d.body).appendChild(st);
  _rvFit(f);
  try{const RO=f.contentWindow.ResizeObserver; if(RO)new RO(()=>_rvFit(f)).observe(d.body);}catch(err){}
  try{f.contentWindow.addEventListener('load',()=>_rvFit(f));}catch(err){}
}
function rvPrint(){
  const f=$('rv-frame'); if(!f)return;
  try{f.contentWindow.focus();f.contentWindow.print();}catch(err){window.open(f.src,'_blank','noopener');}
}

/* the ตรวจระบบ links as a compact card (Admin → ตั้งค่าระบบ, and the hub's last group) */
function rptDiagCardHtml(){
  return `<div class="card mb-12 rpt-diag"><div class="row gap-8">
    <b class="fs-14"><i class="ti ti-activity" aria-hidden="true"></i> ตรวจระบบ</b>
    <span class="lbl">เครื่องมือตรวจเชิงลึก (ย้ายจากแถบนำทาง)</span>
    <div class="row gap-6 ml-auto">${rptKeys('diag').map(k=>`<a class="btn btn-sm" href="${esc(RPTS[k].screen)}"><i class="ti ti-${RPTS[k].icon}" aria-hidden="true"></i> ${esc(RPTS[k].title)}</a>`).join('')}</div>
  </div></div>`;
}
