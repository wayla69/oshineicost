/* recon frontend — 05-login.js: recon's OWN login (RECON_LOGIN, recon/auth.py): login screen, password change
   (forced after first login / reset, or from the user menu), header user chip + menu, and hiding write buttons by role.
   Plain scripts sharing one global scope, loaded in order by index.html (00 → 90);
   a slice may call any other slice's functions, but top-level code only uses earlier slices.
   window.ME (00-core.js) is the contract: {login_enabled, login_ready, user:{username,display_name,role,must_change_pw}|null}. */
const ROLE_TH={manager:'ผู้จัดการ',accountant:'บัญชี',viewer:'ดูอย่างเดียว'};
// what a manager-only action needs, in the screen's words: the shared code (login off) or the manager ROLE (login on)
const mgrWord=()=>ME.login_enabled?'สิทธิ์ผู้จัดการ':'รหัสผู้จัดการ';

/* ── role-based UI: the backend is the real gate, this only hides what would answer 403 ── */
// onclick handlers that write (viewer: hidden)
const _RO_HIDE=['setSt(','closeMonth(','submitSignoff(','reopenMonth(','submitReopen(','pickDoc(','dzPick(','openMatcher(','mmSubmit(','mmDelete(',
  'mxOpen(','mxSubmit(','mxUndo(','acceptChannel(',
  'manualMatchDay(','undoMove(','unacceptPayout(','acceptPayout(','submitMove(','showChannelMove(','submitAckDay(',
  'pickAckDayReason(','ackDay(','unackDay(','unaccept(','submitAccept(','pickAcceptReason(','acceptTodo(','acceptCoinAll(','moveUpload(','delUpload(',
  'confirmUpload(','ignoreLine(','excludeXfer(','excludeAllXfers(','confirmReclass(','applyReclass(','applySugg(',
  'applyMapper(','toggleMapper(','_setupEditLane(','_setupSaveLane(','_setupSaveCode(','_setupRevertCode(',
  '_setupSeedRequirement(','_setupReseedPos(','_setupDelMid(','_setupDelAccount(','_setupAddMid(','_setupAddAccount(',
  '_discBulk(',"-input').click("];   // the last one = the bulk-upload file pickers; dzPick( = the month's drop zone
// inputs whose onchange writes (viewer: greyed out, not clickable)
const _RO_LOCK=['_setupSaveMap(','_discAssign(','_discResolve(','saveFee(','saveSlip(','attachSlip(','bulkUpload('];
// manager-only actions (accountant: hidden — approve/close/reopen + the manager setup)
const _ACCT_HIDE=['closeMonth(','submitSignoff(','reopenMonth(','submitReopen(','_setupEditLane(','_setupSaveLane(','_setupSaveCode(',
  '_setupRevertCode(','_setupSeedRequirement('];
function _roleCss(){
  const sel=(scope,attr,list)=>list.map(f=>`body.${scope} [${attr}*="${f}"]`).join(',');
  return `${sel('role-viewer','onclick',_RO_HIDE)}{display:none !important}
${sel('role-viewer','onchange',_RO_LOCK)}{pointer-events:none !important;opacity:.5 !important}
${sel('role-accountant','onclick',_ACCT_HIDE)}{display:none !important}`;
}
function _applyRoleUi(){
  const b=document.body, role=(ME.login_enabled&&ME.user)?ME.user.role:'';
  b.classList.toggle('auth-on',!!ME.login_enabled);
  for(const r of ['manager','accountant','viewer'])b.classList.toggle('role-'+r,role===r);
  if(!$('role-ui-css')){const s=document.createElement('style');s.id='role-ui-css';s.textContent=_roleCss();document.head.appendChild(s);}
}

/* ── screens ── */
function _loginShell(inner){
  ++_ROUTE_SEQ;                                   // a page still loading must not paint over this screen
  closeSheet();
  document.body.classList.add('login-screen');
  $('app').innerHTML=`<div class="login-wrap">
    <div class="row jc-end mb-8">${_themeBtnHtml()}</div>
    <div class="card login-card">
      <div class="row fx-nowrap mb-14">
        <div class="logo"><i class="ti ti-scale" aria-hidden="true"></i></div>
        <div><h1>กระทบยอด — Oshinei</h1><div class="lbl">POS ↔ เงินเข้าบัญชีจริง</div></div>
      </div>
      ${inner}
    </div></div>`;
}
function _leaveLoginShell(){document.body.classList.remove('login-screen');}
function renderLogin(msg){
  const notReady=!ME.login_ready?`<div class="login-err" role="alert">ระบบเข้าสู่ระบบยังไม่พร้อม (เซิร์ฟเวอร์ยังไม่ได้ตั้งค่า RECON_SESSION_SECRET) — ติดต่อผู้ดูแลระบบ</div>`:'';
  _loginShell(`<form id="login-form" onsubmit="doLogin(event)" novalidate>
      <label class="fld"><span class="lbl">ชื่อผู้ใช้</span>
        <input id="lg-user" class="ctl" name="username" autocomplete="username" autocapitalize="none" spellcheck="false"></label>
      <label class="fld"><span class="lbl">รหัสผ่าน</span>
        <input id="lg-pw" class="ctl" type="password" name="password" autocomplete="current-password"></label>
      <div id="lg-err" class="login-err" role="alert" aria-live="polite">${esc(msg||'')}</div>
      <button class="btn btn-pri w-full" id="lg-btn" type="submit"><i class="ti ti-login" aria-hidden="true"></i> เข้าสู่ระบบ</button>
    </form>
    ${notReady}
    <div class="lbl mt-12">ลืมรหัสผ่านหรือบัญชีถูกล็อก — ติดต่อผู้จัดการให้ตั้งรหัสผ่านใหม่</div>
    ${ME.login_enabled?'':`<div class="lbl mt-6"><a href="#">← กลับหน้าหลัก</a> · ระบบยังไม่บังคับเข้าสู่ระบบ</div>`}`);
  const u=$('lg-user'); if(u)u.focus();
}
async function _postJson(path,body){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
  let j={};try{j=await r.json();}catch(e){}
  if(!r.ok){const err=new Error(detailText(j.detail)||('HTTP '+r.status));err.status=r.status;throw err;}
  return j;
}
async function doLogin(ev){
  if(ev)ev.preventDefault();
  const u=($('lg-user').value||'').trim(), p=$('lg-pw').value||'', e=$('lg-err');
  if(!u||!p){e.textContent='กรอกชื่อผู้ใช้และรหัสผ่าน';return;}
  await busy('login',$('lg-btn'),async()=>{
    try{const j=await _postJson('/api/auth/login',{username:u,password:p}); ME.user=j.user;}
    catch(err){e.textContent=err.message; const pw=$('lg-pw'); if(pw){pw.value='';pw.focus();} return;}
    if(ME.user&&ME.user.must_change_pw)return renderChangePw(true);
    _afterAuth();
  });
}
function _afterAuth(){
  _leaveLoginShell(); _applyRoleUi();
  if(location.hash==='#login')location.hash='';   // hashchange → route()
  else route();
}
function _pwFormHtml(forced){
  return `<form id="pw-form" onsubmit="doChangePw(event,${forced?1:0})" novalidate>
    <label class="fld"><span class="lbl">${forced?'รหัสผ่านปัจจุบัน (ที่ได้รับ)':'รหัสผ่านปัจจุบัน'}</span>
      <input id="pw-old" class="ctl" type="password" autocomplete="current-password"></label>
    <label class="fld"><span class="lbl">รหัสผ่านใหม่ (อย่างน้อย 8 ตัวอักษร)</span>
      <input id="pw-new" class="ctl" type="password" autocomplete="new-password"></label>
    <label class="fld"><span class="lbl">ยืนยันรหัสผ่านใหม่</span>
      <input id="pw-new2" class="ctl" type="password" autocomplete="new-password"></label>
    <div id="pw-err" class="login-err" role="alert" aria-live="polite"></div>
    <div class="row gap-8">
      <button class="btn btn-pri" id="pw-btn" type="submit">บันทึกรหัสผ่านใหม่</button>
      ${forced?'<button class="btn" type="button" onclick="doLogout()">ออกจากระบบ</button>'
              :'<button class="btn" type="button" onclick="closeSheet()">ยกเลิก</button>'}
    </div></form>`;
}
function renderChangePw(forced){
  if(forced){
    _loginShell(`<div class="sheet-h mb-4"><i class="ti ti-key" aria-hidden="true"></i> ตั้งรหัสผ่านใหม่ก่อนเริ่มใช้งาน</div>
      <div class="lbl mb-10">สวัสดี ${esc((ME.user&&ME.user.display_name)||'')} — รหัสผ่านที่ได้รับเป็นรหัสชั่วคราว กรุณาตั้งรหัสของคุณเอง (ไม่มีใครเห็นรหัสนี้ รวมถึงผู้จัดการ)</div>
      ${_pwFormHtml(true)}`);
  }else{
    showSheet(`<div class="sheet-h"><i class="ti ti-key" aria-hidden="true"></i> เปลี่ยนรหัสผ่าน</div>${_pwFormHtml(false)}`);
  }
  const o=$('pw-old'); if(o)o.focus();
}
async function doChangePw(ev,forced){
  if(ev)ev.preventDefault();
  const o=$('pw-old').value||'', n=$('pw-new').value||'', n2=$('pw-new2').value||'', e=$('pw-err');
  if(n.length<8){e.textContent='รหัสผ่านใหม่ต้องยาวอย่างน้อย 8 ตัวอักษร';return;}
  if(n!==n2){e.textContent='รหัสผ่านใหม่สองช่องไม่ตรงกัน';return;}
  await busy('chpw',$('pw-btn'),async()=>{
    try{const j=await _postJson('/api/auth/change-password',{old:o,new:n}); ME.user=j.user;}
    catch(err){e.textContent=err.message;return;}
    toast('เปลี่ยนรหัสผ่านแล้ว');
    if(forced)_afterAuth(); else closeSheet();
  });
}
async function doLogout(){
  try{await _postJson('/api/auth/logout',{});}catch(e){}
  ME.user=null; closeSheet(); _applyRoleUi();
  if(ME.login_enabled)renderLogin(); else {_leaveLoginShell(); route();}
}

/* ── header: user chip + menu (logged in) · the write-token key button stays only while the login is off ── */
function _authHdrHtml(){
  let h='';
  if(ME.user){
    const u=ME.user;
    h+=`<button class="btn btn-sm user-chip" onclick="openUserMenu()" aria-haspopup="dialog" title="${esc(u.username)}">
      <i class="ti ti-user-circle" aria-hidden="true"></i><span class="uc-name">${esc(u.display_name||u.username)}</span>
      <span class="uc-role">· ${esc(ROLE_TH[u.role]||u.role)}</span></button>`;
  }
  if(!ME.login_enabled)
    h+=`<button class="btn btn-sm" onclick="setToken()" title="ตั้งรหัสสำหรับบันทึกข้อมูล (จำเป็นบนเซิร์ฟเวอร์จริง)"><i class="ti ti-key" aria-hidden="true"></i> รหัส</button>`;
  return h;
}
function openUserMenu(){
  const u=ME.user; if(!u)return;
  showSheet(`<div class="sheet-h"><i class="ti ti-user-circle" aria-hidden="true"></i> ${esc(u.display_name||u.username)}</div>
    <div class="lbl mb-10">ชื่อผู้ใช้ <b>${esc(u.username)}</b> · สิทธิ์ <b>${esc(ROLE_TH[u.role]||u.role)}</b></div>
    <div class="more-menu">
      <a class="more-item" href="#" onclick="renderChangePw(false);return false"><span><i class="ti ti-key" aria-hidden="true"></i> เปลี่ยนรหัสผ่าน</span></a>
      ${u.role==='manager'?`<a class="more-item" href="#setup" onclick="closeSheet()"><span><i class="ti ti-users" aria-hidden="true"></i> จัดการผู้ใช้งาน</span><span class="lbl">ตั้งค่าระบบ → ผู้ใช้งานระบบ</span></a>`:''}
      <a class="more-item" href="#" onclick="doLogout();return false"><span><i class="ti ti-logout" aria-hidden="true"></i> ออกจากระบบ</span></a>
    </div>
    <div class="row mt-10"><button class="btn" onclick="closeSheet()">ปิด</button></div>`);
}
