/* recon frontend — 85-users.js: #setup ⑥ ผู้ใช้งานระบบ — recon's own accounts (manager only): list, add, change role,
   deactivate / reactivate, reset password. Backend: /api/users (recon/routers/auth.py) — it re-checks the manager role.
   Plain scripts sharing one global scope, loaded in order by index.html (00 → 90);
   a slice may call any other slice's functions, but top-level code only uses earlier slices. */
let _USERS=null;

// the ⑥ card inside #setup (80-setup.js calls this while building the page, then loadUsersAdmin())
function usersCardHtml(){
  if(ME.user&&ME.user.role==='manager')
    return `<div class="card mb-12" id="users-card"><div class="row mb-6">
      <h2 class="m-0 fs-15"><i class="ti ti-users" aria-hidden="true"></i> ⑥ ผู้ใช้งานระบบ</h2>
      <span class="lbl">บัญชีเข้าใช้ของระบบกระทบยอด (แยกจากแดชบอร์ด)</span>
      <button class="btn btn-sm btn-pri ml-auto" onclick="openAddUser()"><i class="ti ti-user-plus" aria-hidden="true"></i> เพิ่มผู้ใช้</button></div>
      <div class="muted mb-8 fs-12"><b>ผู้จัดการ</b> = ทุกอย่าง รวมปิดเดือน/เปิดงวด/ตั้งค่าขั้นสูง/จัดการผู้ใช้ · <b>บัญชี</b> = อัปโหลด จับคู่ รับทราบ ส่งตรวจ (ปิดเดือนไม่ได้) · <b>ดูอย่างเดียว</b> = ดูหน้าจอและรายงาน แก้ไขอะไรไม่ได้ ·
        ผู้ใช้ใหม่และผู้ที่ถูกรีเซ็ตรหัส ต้องตั้งรหัสผ่านของตัวเองเมื่อเข้าสู่ระบบครั้งแรก${ME.login_enabled?'':' · <b>ระบบยังไม่บังคับเข้าสู่ระบบ</b> (เปิดเมื่อตั้ง RECON_LOGIN=1)'}</div>
      <div id="users-body" class="muted">กำลังโหลด…</div></div>`;
  if(!ME.user&&ME.login_ready&&!ME.login_enabled)        // pre-rollout: the owner signs in here to create accounts
    return `<div class="card mb-12"><div class="row">
      <h2 class="m-0 fs-15"><i class="ti ti-users" aria-hidden="true"></i> ⑥ ผู้ใช้งานระบบ</h2>
      <span class="lbl">เข้าสู่ระบบในฐานะผู้จัดการเพื่อเพิ่ม/แก้ผู้ใช้</span>
      <a class="btn btn-sm ml-auto" href="#login"><i class="ti ti-login" aria-hidden="true"></i> เข้าสู่ระบบ</a></div></div>`;
  return '';
}

function _fmtTs(s){
  if(!s)return '<span class="lbl">ยังไม่เคย</span>';
  const d=new Date(String(s).replace(' ','T')+'Z');           // server stores UTC
  if(isNaN(+d))return esc(s);
  return esc(d.toLocaleString('th-TH',{day:'numeric',month:'short',year:'2-digit',hour:'2-digit',minute:'2-digit'}));
}
async function loadUsersAdmin(){
  const b=$('users-body'); if(!b)return;
  try{_USERS=await api('/api/users');}
  catch(e){b.innerHTML=`<span class="t-err">โหลดไม่สำเร็จ: ${esc(e.message)}</span>`;return;}
  if(!$('users-body'))return;
  const roleSel=(u)=>`<select class="ctl-sm fs-12" aria-label="สิทธิ์ของ ${esc(u.username)}" onchange="userSetRole(${u.id},this)">
      ${['manager','accountant','viewer'].map(r=>`<option value="${r}" ${u.role===r?'selected':''}>${ROLE_TH[r]}</option>`).join('')}</select>`;
  b.innerHTML=`<div class="setup-scroll"><table class="setup-tbl users-tbl">
    <tr><th>ชื่อ</th><th>ชื่อผู้ใช้</th><th>สิทธิ์</th><th>สถานะ</th><th>เข้าใช้ล่าสุด</th><th></th></tr>
    ${_USERS.map(u=>`<tr class="${u.active?'':'dim'}">
      <td class="fs-12 fw-600">${esc(u.display_name||u.username)}${ME.user&&u.username.toLowerCase()===ME.user.username.toLowerCase()?' <span class="chip c-setup fs-11">คุณ</span>':''}</td>
      <td class="ff-mono fs-12">${esc(u.username)}</td>
      <td>${roleSel(u)}</td>
      <td class="fs-12">${u.active?'<span class="chip c-ok fs-11">ใช้งาน</span>':'<span class="chip c-mut fs-11">ปิดบัญชี</span>'}
        ${u.locked?' <span class="chip c-err fs-11">ล็อก 15 นาที</span>':''}${u.must_change_pw&&u.active?' <span class="chip c-warn fs-11">รอตั้งรหัสใหม่</span>':''}</td>
      <td class="fs-12">${_fmtTs(u.last_login_at)}</td>
      <td class="nowrap"><button class="btn btn-sm" onclick="openResetPw(${u.id})"><i class="ti ti-key" aria-hidden="true"></i> ตั้งรหัสใหม่</button>
        <button class="btn btn-sm ${u.active?'t-err':''}" onclick="userSetActive(${u.id},${u.active?0:1},this)">${u.active?'ปิดบัญชี':'เปิดใช้'}</button></td>
    </tr>`).join('')||'<tr><td colspan="6" class="muted pad-14-9">ยังไม่มีผู้ใช้</td></tr>'}
  </table></div>`;
}
function _userById(id){return (_USERS||[]).find(u=>u.id===id);}
async function _userPatch(id,body,okMsg){
  try{await api(`/api/users/${id}`,{method:'PATCH',body});toast(okMsg);}
  catch(e){toast('ไม่สำเร็จ: '+e.message);}
  loadUsersAdmin();
}
function userSetRole(id,sel){
  const u=_userById(id); if(!u)return;
  if(!confirm(`เปลี่ยนสิทธิ์ ${u.display_name||u.username} เป็น "${ROLE_TH[sel.value]}" ?`)){sel.value=u.role;return;}
  _userPatch(id,{role:sel.value},'เปลี่ยนสิทธิ์แล้ว — มีผลทันที');
}
function userSetActive(id,active,btn){
  const u=_userById(id); if(!u)return;
  if(!active&&!confirm(`ปิดบัญชี ${u.display_name||u.username}? — ออกจากระบบทันทีทุกเครื่อง และเข้าใช้ไม่ได้จนกว่าจะเปิดใหม่ (ประวัติการทำงานยังอยู่ครบ)`))return;
  busy('user-active-'+id,btn,()=>_userPatch(id,{active:!!active},active?'เปิดใช้บัญชีแล้ว':'ปิดบัญชีแล้ว'));
}
function openAddUser(){
  showSheet(`<div class="sheet-h"><i class="ti ti-user-plus" aria-hidden="true"></i> เพิ่มผู้ใช้</div>
    <form onsubmit="submitAddUser(event)" novalidate>
      <label class="fld"><span class="lbl">ชื่อที่แสดง (เช่น ชื่อ-นามสกุล)</span><input id="nu-name" class="ctl" autocomplete="off"></label>
      <label class="fld"><span class="lbl">ชื่อผู้ใช้สำหรับเข้าสู่ระบบ (อังกฤษ/ตัวเลข . _ - ไม่มีเว้นวรรค)</span>
        <input id="nu-user" class="ctl" autocomplete="off" autocapitalize="none" spellcheck="false"></label>
      <label class="fld"><span class="lbl">สิทธิ์</span><select id="nu-role" class="ctl">
        <option value="accountant">บัญชี — ทำงานกระทบยอด (ปิดเดือนไม่ได้)</option>
        <option value="viewer">ดูอย่างเดียว</option>
        <option value="manager">ผู้จัดการ — ทุกอย่าง</option></select></label>
      <label class="fld"><span class="lbl">รหัสผ่านชั่วคราว (อย่างน้อย 8 ตัว — ผู้ใช้ต้องเปลี่ยนเมื่อเข้าครั้งแรก)</span>
        <input id="nu-pw" class="ctl" type="text" autocomplete="off" spellcheck="false"></label>
      <div id="nu-err" class="login-err" role="alert" aria-live="polite"></div>
      <div class="row gap-8"><button class="btn btn-pri" id="nu-btn" type="submit">เพิ่มผู้ใช้</button>
        <button class="btn" type="button" onclick="closeSheet()">ยกเลิก</button></div>
    </form>`);
  const n=$('nu-name'); if(n)n.focus();
}
async function submitAddUser(ev){
  if(ev)ev.preventDefault();
  const body={display_name:$('nu-name').value.trim(),username:$('nu-user').value.trim(),
              role:$('nu-role').value,password:$('nu-pw').value};
  const e=$('nu-err');
  if(!body.username){e.textContent='ใส่ชื่อผู้ใช้';return;}
  if(body.password.length<8){e.textContent='รหัสผ่านชั่วคราวต้องยาวอย่างน้อย 8 ตัวอักษร';return;}
  await busy('add-user',$('nu-btn'),async()=>{
    try{await api('/api/users',{method:'POST',body});}
    catch(err){e.textContent=err.message;return;}
    closeSheet();toast(`เพิ่ม ${body.username} แล้ว — แจ้งรหัสชั่วคราวให้ผู้ใช้ทางช่องทางส่วนตัว`);loadUsersAdmin();
  });
}
function openResetPw(id){
  const u=_userById(id); if(!u)return;
  showSheet(`<div class="sheet-h"><i class="ti ti-key" aria-hidden="true"></i> ตั้งรหัสผ่านใหม่ — ${esc(u.display_name||u.username)}</div>
    <div class="lbl mb-10">ผู้ใช้จะออกจากระบบทุกเครื่องทันที ปลดล็อกบัญชี และต้องตั้งรหัสของตัวเองเมื่อเข้าครั้งถัดไป</div>
    <form onsubmit="submitResetPw(event,${id})" novalidate>
      <label class="fld"><span class="lbl">รหัสผ่านชั่วคราว (อย่างน้อย 8 ตัว)</span>
        <input id="rp-pw" class="ctl" type="text" autocomplete="off" spellcheck="false"></label>
      <div id="rp-err" class="login-err" role="alert" aria-live="polite"></div>
      <div class="row gap-8"><button class="btn btn-pri" id="rp-btn" type="submit">ตั้งรหัสใหม่</button>
        <button class="btn" type="button" onclick="closeSheet()">ยกเลิก</button></div>
    </form>`);
  const i=$('rp-pw'); if(i)i.focus();
}
async function submitResetPw(ev,id){
  if(ev)ev.preventDefault();
  const pw=$('rp-pw').value, e=$('rp-err');
  if(pw.length<8){e.textContent='รหัสผ่านต้องยาวอย่างน้อย 8 ตัวอักษร';return;}
  await busy('reset-pw',$('rp-btn'),async()=>{
    try{await api(`/api/users/${id}/reset-password`,{method:'POST',body:{password:pw}});}
    catch(err){e.textContent=err.message;return;}
    closeSheet();toast('ตั้งรหัสใหม่แล้ว — แจ้งผู้ใช้ทางช่องทางส่วนตัว');
    if(ME.user&&_userById(id)&&_userById(id).username.toLowerCase()===ME.user.username.toLowerCase()){await loadMe();route();return;}
    loadUsersAdmin();
  });
}
