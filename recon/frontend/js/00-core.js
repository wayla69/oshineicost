/* recon frontend — 00-core.js: shared core: helpers ($ fmt esc jsa api mgrApi busy toast), route + route sequence token, header, theme, bottom sheet, nav, branch pickers.
   Plain scripts sharing one global scope, loaded in order by index.html (00 → 90);
   a slice may call any other slice's functions, but top-level code only uses earlier slices. */
const $=id=>document.getElementById(id);
const fmt=n=>(n==null?'—':Math.round(n).toLocaleString('th-TH'));
const progCls=p=>p>=95?'':p>=70?'p-warn':'p-err';
const TH_MONTHS=['','มกราคม','กุมภาพันธ์','มีนาคม','เมษายน','พฤษภาคม','มิถุนายน',
                 'กรกฎาคม','สิงหาคม','กันยายน','ตุลาคม','พฤศจิกายน','ธันวาคม'];
function monthTh(ym){            // 'YYYY-MM' -> 'พฤษภาคม 2569' (Buddhist year +543)
  if(!ym||ym.length<7)return ym||'';
  return (TH_MONTHS[+ym.slice(5,7)]||'')+' '+(+ym.slice(0,4)+543);
}
const COIN_TOL=100;             // ฿ tolerance for "coin residue"; single source of truth
const esc=s=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
// ค่าที่ต้องส่งเป็นอาร์กิวเมนต์ JS ใน onclick="…" — JSON ให้เป็น string literal ที่ถูกต้อง แล้ว esc กันหลุด attribute
// (esc อย่างเดียวไม่พอ: เบราว์เซอร์ถอด &#39; กลับเป็น ' ก่อนรัน JS) · ใช้: onclick="f(${jsa(v)})"
const jsa=v=>esc(JSON.stringify(v==null?null:v));
// วันที่ตามเวลาเครื่อง (ไม่ใช่ UTC) — toISOString() ในไทย (UTC+7) ได้วันก่อนหน้าตอนก่อน 07:00 และตอนเที่ยงคืน
const ymdLocal=(d=new Date())=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
const addDays=(ymd,n)=>{const [y,m,d]=ymd.split('-').map(Number);return ymdLocal(new Date(y,m-1,d+n));};
let _toastT=null;
function toast(m,ms){const t=$('toast');t.textContent=m;t.classList.add('show');clearTimeout(_toastT);
  _toastT=setTimeout(()=>t.classList.remove('show'),ms||Math.min(8000,Math.max(3000,String(m).length*70)));}
// กันกดซ้ำ: งานเดียวกันวิ่งได้ทีละครั้ง + ปิดปุ่มระหว่างรอเซิร์ฟเวอร์ (เปิดคืนเมื่อผิดพลาด/ยังอยู่บนหน้า)
const _BUSY=new Set();
async function busy(key,btn,fn){
  if(_BUSY.has(key))return; _BUSY.add(key); if(btn)btn.disabled=true;
  try{return await fn();}finally{_BUSY.delete(key); if(btn&&btn.isConnected)btn.disabled=false;}
}
// เลขลำดับหน้า: ทุก load* จำเลขตอนเริ่ม ถ้าระหว่างรอผู้ใช้เปลี่ยนหน้า/เดือนไปแล้ว → ทิ้งผลเก่า ไม่ทับหน้าใหม่
let _ROUTE_SEQ=0;
const _cur=t=>t===_ROUTE_SEQ;
// detail จาก FastAPI: string หรือ list ของ {loc,msg} (422) → ข้อความอ่านได้ ไม่ใช่ [object Object]
function detailText(d){
  if(d==null)return '';
  if(typeof d==='string')return d;
  if(Array.isArray(d))return d.map(x=>x&&typeof x==='object'
    ?((Array.isArray(x.loc)&&x.loc.length?x.loc[x.loc.length-1]+': ':'')+(x.msg||JSON.stringify(x))):String(x)).join(' · ');
  if(typeof d==='object')return d.msg||d.message||d.detail||JSON.stringify(d);
  return String(d);
}
function emptyState(icon,title,hint,action){return `<div class="card empty"><i class="ti ti-${icon} ei" aria-hidden="true"></i><div class="et">${title}</div>${hint?`<div class="eh">${hint}</div>`:''}${action||''}</div>`;}
function loadingState(msg){return `<div class="loading"><span class="spin" aria-hidden="true"></span>${msg||'กำลังโหลด…'}</div>`;}
function tokenHdr(){const t=(localStorage.getItem('oshinei_token')||'').trim();return t?{'X-Upload-Token':t}:{};}
function setToken(){
  let t=prompt('รหัสสำหรับบันทึกข้อมูล (Write token) — เว้นว่างเพื่อล้าง:',localStorage.getItem('oshinei_token')||'');
  if(t===null)return; t=t.trim();
  t?localStorage.setItem('oshinei_token',t):localStorage.removeItem('oshinei_token');
  toast(t?'บันทึกรหัสแล้ว':'ล้างรหัสแล้ว');
}
// ถ้าเซิร์ฟเวอร์ตอบ 403 เพราะยังไม่ได้ใส่รหัส → ชวนตั้งรหัสทันที
function hint403(e){ if(String(e.message||'').includes('write token')||String(e.message).includes('403')) toast('ต้องตั้งรหัสบันทึกก่อน — กดปุ่มรหัส มุมบนขวา'); }
async function api(path,opts={}){
  opts.headers={...(opts.headers||{}),...tokenHdr()};
  if(opts.body && !(opts.body instanceof FormData)){opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(opts.body);}
  const r=await fetch(path,opts);
  if(!r.ok){let d='';try{d=detailText((await r.json()).detail)}catch(e){};
    const err=new Error(d||('HTTP '+r.status));err.status=r.status;throw err;}
  return r.json();
}
function _mgrHdr(){const c=sessionStorage.getItem('mgr_code')||'';return c?{'X-Manager-Token':c}:{};}
async function mgrApi(path,opts={}){
  for(let attempt=0;attempt<2;attempt++){
    try{return await api(path,{...opts,headers:{...(opts.headers||{}),..._mgrHdr()}});}
    catch(e){
      if(!String(e.message).includes('ผู้จัดการ')||attempt===1)throw e;
      const code=prompt('การตั้งค่าขั้นสูงต้องใช้รหัสผู้จัดการ:')||'';
      if(!code)throw new Error('ยกเลิก — ไม่ได้ใส่รหัสผู้จัดการ');
      sessionStorage.setItem('mgr_code',code);
    }
  }
}

const CH_ST={ok:['c-ok','ตรงกันแล้ว'],diff:['c-err','ยอดไม่ตรง'],waiting_doc:['c-warn','รอเอกสาร'],
              no_source:['c-mut','ไม่ต้องตรวจ'],setup:['c-setup','รหัสใหม่ ต้องตั้งค่า']};
const SES_ST={draft:'กำลังทำ',in_review:'รอผู้จัดการตรวจ',approved:'ปิดเดือนแล้ว',closed:'ปิดงวดแล้ว'};
const isClosedSt=st=>st==='approved'||st==='closed';

let MONTH=localStorage.getItem('v2_month')||ymdLocal().slice(0,7);
let DATA=null, UP_DOC=null, PENDING_CH=null;

/* คลิกช่องทางในหน้าภาพรวม → เข้าสาขาแล้วกางช่องทางนั้นทันที */
function goChannel(bid,key){PENDING_CH=key;location.hash='b/'+bid;}
/* shared drill: expand a channel's day evidence (works in any Phase-2 bucket) */
function openEvidence(channelKey){
  if(!DATA||!DATA.channels)return;
  const idx=DATA.channels.findIndex(c=>c.key===channelKey);
  if(idx<0)return;
  const el=$('days-'+idx);
  // if the channel sits inside a collapsed drawer (เรียบร้อย/รอรอบ), open it first
  const dw=el&&el.closest('[id^="dw-"]');
  if(dw&&dw.style.display==='none') toggleDrawer(dw.id.slice(3));
  if(el && el.style.display==='none') toggleDays(idx);
  setTimeout(()=>{const e=$('days-'+idx); if(e) e.scrollIntoView({behavior:_scrollBehavior(),block:'center'});},50);
}
function drillTodo(key){ openEvidence(key); }   // back-compat: todo card + drillReclass call this

function route(){
  ++_ROUTE_SEQ;                    // ผลของหน้าก่อนที่ยังโหลดค้างจะถูกทิ้ง (ดู _cur)
  const h=location.hash.slice(1);
  _setNavActive(h);
  if(h==='reports')return loadReports();
  if(h==='errors')return loadErrorReport();
  if(h==='wrong')return loadWrongChannel();
  if(h==='daybook')return loadDaybook();
  if(h==='dashboard')return loadDashboard();
  if(h==='audit')return loadAudit();
  if(h==='trend')return loadTrend();
  if(h==='bankcov')return loadBankCov();
  if(h==='board')return loadBoard();
  if(h==='coupon')return loadCoupon();
  if(h==='override')return loadOverrideRegister();
  if(h==='aging')return loadAging();
  if(h==='certificate')return loadCertificate();
  if(h==='closereg')return loadCloseRegister();
  if(h==='accepted')return loadAcceptedVariance();
  if(h==='auditlog')return loadAuditTrail();
  if(h==='setup')return loadSetup();
  if(h.startsWith('payout'))return loadPayout(h);
  if(h.startsWith('discrepancies'))return loadDiscrepancies(h);
  const m=h.match(/^b\/(\d+)/);
  if(m)loadBranch(+m[1]);else loadOverview();
}
window.addEventListener('hashchange',route);
// คีย์บอร์ด: แถว/ช่องที่กดได้ (role="button" tabindex="0") — Enter/Space = คลิก (ตัวจัดการเดียวทั้งแอป)
document.addEventListener('keydown',e=>{
  if(e.key!=='Enter'&&e.key!==' ')return;
  const el=e.target;
  if(!el||!el.getAttribute||el.getAttribute('role')!=='button'||/^(BUTTON|A|INPUT|SELECT|TEXTAREA)$/.test(el.tagName))return;
  e.preventDefault();el.click();
});

function header(extra){
  const h=location.hash.slice(1);
  return `<div class="row mb-8">
    <div class="row" style="gap:9px;cursor:pointer;flex-wrap:nowrap" onclick="location.hash=''">
      <div class="logo"><i class="ti ti-scale" aria-hidden="true"></i></div>
      <div><h1 style="line-height:1.2">กระทบยอดรายเดือน</h1>
        <div class="lbl">Oshinei · POS ↔ เงินเข้าบัญชีจริง</div></div>
    </div>
    <input type="month" value="${esc(MONTH)}" onchange="setMonth(this.value)"
      style="padding:7px 10px;border:1px solid var(--line);border-radius:var(--radius-sm);background:var(--input);box-shadow:var(--shadow)">
    ${extra||''}
    ${_themeBtnHtml('ml-auto')}
    <button class="btn btn-sm" onclick="setToken()" title="ตั้งรหัสสำหรับบันทึกข้อมูล (จำเป็นบนเซิร์ฟเวอร์จริง)"><i class="ti ti-key" aria-hidden="true"></i> รหัส</button>
  </div>
  ${_topNavHtml(h)}`;
}
function setMonth(m){MONTH=m;localStorage.setItem('v2_month',m);route();}
/* โหมดมืด/สว่าง — ปุ่มไอคอนบนหัวหน้า · จำไว้ใน localStorage คีย์เดียวกับแดชบอร์ด ('oshinei_dark')
   ยังไม่เคยเลือก = ตามระบบ (prefers-color-scheme) */
const _isDark=()=>document.body.classList.contains('dark');
function _themeBtnHtml(cls){
  const d=_isDark();
  return `<button class="btn btn-sm theme-btn ${cls||''}" id="theme-btn" onclick="toggleTheme()"
    aria-label="สลับโหมดมืด/สว่าง" title="สลับโหมดมืด/สว่าง" aria-pressed="${d}"><i class="ti ti-${d?'sun':'moon'}" aria-hidden="true"></i></button>`;
}
function _applyTheme(dark){
  document.body.classList.toggle('dark',dark);
  const b=$('theme-btn');
  if(b){b.setAttribute('aria-pressed',String(dark));b.innerHTML=`<i class="ti ti-${dark?'sun':'moon'}" aria-hidden="true"></i>`;}
}
function toggleTheme(){
  const dark=!_isDark();
  _applyTheme(dark);
  try{localStorage.setItem('oshinei_dark',dark?'1':'0');}catch(e){}
}
// ยังไม่เคยเลือกเอง → เปลี่ยนตามระบบทันทีเมื่อผู้ใช้สลับธีมของเครื่อง
try{
  const mq=window.matchMedia&&matchMedia('(prefers-color-scheme: dark)');
  if(mq&&mq.addEventListener)mq.addEventListener('change',e=>{
    let d=null;try{d=localStorage.getItem('oshinei_dark');}catch(_){}
    if(d!=='1'&&d!=='0')_applyTheme(e.matches);
  });
}catch(e){}
// เลื่อนหน้าแบบนุ่ม เว้นแต่ระบบขอให้ลดการเคลื่อนไหว (prefers-reduced-motion)
const _scrollBehavior=()=>(window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches)?'auto':'smooth';

// จำนวน "เรื่องที่ต้องทำ" ของสาขา = todo + คู่ผิดช่อง (กันสาขาที่ดูเหมือนครบแต่ยังมีเงินผิดช่อง)
function branchOpen(b){return (b.todo_count||0)+(b.reclass_count||0);}
function openSummary(b){
  const td=b.todo_count||0, rc=b.reclass_count||0;
  if(td===0&&rc===0)return '';
  if(td===0&&rc>0)return rc===1?'เหลือ 1 เรื่อง: ยอดอยู่ผิดช่อง':'เหลือ '+rc+' เรื่อง: ยอดอยู่ผิดช่อง';
  let s='เหลือ '+(td+rc)+' เรื่อง';
  if(rc>0)s+=' · มีเงินอาจอยู่ผิดช่อง '+rc+' คู่';
  return s;
}

/* ── bottom sheet กลาง (ใช้กับเหตุผลรับทราบ) ── */
function showSheet(html){
  let o=$('sheet');
  if(!o){o=document.createElement('div');o.id='sheet';o.className='sheet-overlay';document.body.appendChild(o);}
  o.innerHTML=`<div class="sheet-box">${html}</div>`;
  o.onclick=(e)=>{if(e.target===o)closeSheet();};
  o.style.display='flex';
}
function closeSheet(){const o=$('sheet');if(o)o.style.display='none';}

/* ════════════════ PHASE 5 — Responsive Nav + Reports Hub ════════════════ */
const _NAV_TABS=[
  ['home','<i class="ti ti-home" aria-hidden="true"></i>','หน้าหลัก','#'],
  ['reports','<i class="ti ti-report" aria-hidden="true"></i>','รายงาน','#reports'],
  ['admin','<i class="ti ti-settings" aria-hidden="true"></i>','Admin','#setup'],
  ['diag','<i class="ti ti-activity" aria-hidden="true"></i>','วินิจฉัย','#board'],
];
function _navZone(h){
  if(!h||h.match(/^b\//)||h==='daybook'||h==='dashboard')return 'home';
  if(h==='reports'||h==='wrong'||h==='errors'||h==='coupon')return 'reports';
  if(h==='setup'||h==='audit'||h==='bankcov'||h.startsWith('payout')||h.startsWith('discrepancies'))return 'admin';
  if(h==='board'||h==='trend')return 'diag';
  return 'home';
}
function _topNavHtml(h){
  const z=_navZone(h);
  return `<div class="top-nav" role="tablist">${_NAV_TABS.map(([k,ic,lbl,href])=>
    `<a class="tnt${z===k?' active':''}" href="${href}" role="tab" aria-selected="${z===k}">${ic} ${lbl}</a>`
  ).join('')}</div>`;
}
function _initNav(){
  const n=$('main-nav'); if(!n)return;
  n.innerHTML=_NAV_TABS.map(([k,ic,lbl,href])=>
    `<a class="nav-tab" id="mnt-${k}" href="${href}" aria-label="${lbl}"><span class="ni" aria-hidden="true">${ic}</span>${lbl}</a>`
  ).join('');
}
function _setNavActive(h){
  const z=_navZone(h);
  document.querySelectorAll('#main-nav .nav-tab').forEach(el=>{
    el.classList.toggle('active',el.id==='mnt-'+z);
  });
}

/* ── helpers shared by session-scoped views (#trend, #bankcov) ─────────── */

// Cache of branches loaded for session-scoped pickers (master branches only).
let _DIAG_BRANCHES = null;
async function _diagBranches(){
  if(_DIAG_BRANCHES) return _DIAG_BRANCHES;
  try{ _DIAG_BRANCHES = await api('/api/branches'); }catch(e){ _DIAG_BRANCHES=[]; }
  return _DIAG_BRANCHES;
}

// Resolve session_id for a branch+month without creating one.
// Returns null if no session exists yet; THROWS on a load error (caller shows an error
// card — a failed request must not read as "ยังไม่มี session").
async function _resolveSession(branchId, month){
  if(!branchId) return null;
  const list = await api(`/api/sessions?branch_id=${branchId}&period_month=${month}`);
  return (list && list.length) ? list[0].session_id : null;
}

// Render a branch picker for session-scoped diagnostic views.
// onChange calls loadFn() after updating the branch var referenced by varSetter.
function _diagBranchPicker(branches, currentBid, onchangeExpr){
  const opts = branches.map(b =>
    `<option value="${b.branch_id}" ${b.branch_id==currentBid?'selected':''}>${esc(b.name)}</option>`
  ).join('');
  return `<select onchange="${onchangeExpr}" title="เลือกสาขา"
    class="ctl">${opts}</select>`;
}
