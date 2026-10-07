/* recon frontend — 26-signoff.js: the manager's sign-off sheet (ปิดเดือน) and the reopen sheet (เปิดงวดใหม่).
   Shows what is being signed — branch · month · preparer · sent for review — and a checklist built from the
   branch payload (documents + coverage, channels, accepted variances, manual adjustments from the override
   register, pending discrepancies), then signs with the step-up: login ON = the signed-in manager's own
   password (PATCH field `password`); login OFF = approver name + manager code (X-Manager-Token).
   Plain scripts sharing one global scope, loaded in order by index.html (00 → 90);
   a slice may call any other slice's functions, but top-level code only uses earlier slices. */

/* ── the checklist (pure: r = /api/v2/reconcile payload, reg = /api/reports/override-register or null) ── */
function signoffChecks(r,reg,month){
  const out=[];
  // 1. documents: uploaded n/N + how far the strips run unbroken
  const docs=r.documents||[], nUp=docs.filter(d=>d.status==='uploaded').length;
  const strips=docs.filter(d=>Array.isArray(d.days_covered));
  const gaps=strips.map(d=>({d,t:covText(d.days_covered,covDue(d,month))})).filter(x=>x.t!=='ครบ');
  const thr=strips.length?Math.min(...strips.map(d=>covThrough(d.days_covered))):null;
  out.push({key:'docs',ok:nUp===docs.length&&!gaps.length,title:'เอกสารครบ',
    detail:`อัปโหลดแล้ว ${nUp}/${docs.length}`+(thr!=null?(thr?` · ครอบคลุมต่อเนื่องถึงวันที่ ${thr}`:' · ยังไม่ครอบคลุมวันที่ 1'):''),
    items:gaps.map(x=>`${x.d.label}: ${x.t}`),link:'DOCS'});
  // 2. channels matched or accepted with a reason
  const chs=r.channels||[], good=chs.filter(c=>c.accepted||c.status==='ok'||c.status==='no_source');
  out.push({key:'ch',ok:good.length===chs.length,title:'ช่องทางตรงกันหรือรับทราบแล้ว',
    detail:`${good.length}/${chs.length} ช่องทาง`,
    items:chs.filter(c=>!good.includes(c)).map(c=>`${c.label}: ${(CH_ST[c.status]||['',c.status])[1]}${c.detail?' — '+c.detail:''}`)});
  // 3. accepted variances: whole-channel accepts, day acks, payout acks — each with its reason
  const acc=[];
  chs.forEach(c=>{
    if(c.accepted)acc.push({what:`${c.label} (ทั้งช่อง)`,amt:c.key==='COUPON'?null:(c.residual!=null?c.residual:c.variance),note:c.accepted.note,by:c.accepted.by});
    (c.days||[]).forEach(d=>{ if(d.ack)acc.push({what:`${c.label} วันที่ ${+d.day.slice(8)}`,amt:d.variance,note:d.ack.note,by:d.ack.by}); });
  });
  (r.payout_issues||[]).forEach(p=>{ if(p.accepted)acc.push({what:`ยอดโอน ${p.label}`,amt:p.variance,note:p.accepted.note,by:p.accepted.by}); });
  const accTot=acc.reduce((a,x)=>a+Math.abs(x.amt||0),0);
  out.push({key:'acc',ok:acc.every(x=>(x.note||'').trim()),title:'ผลต่างที่รับทราบพร้อมเหตุผล',
    detail:acc.length?`${acc.length} รายการ · รวม ฿${fmt(accTot)}`:'ไม่มี',
    items:acc.map(x=>`${x.what}${x.amt!=null?' '+_sgn(x.amt):''} — "${x.note||'(ไม่มีเหตุผล)'}"${x.by?' · '+x.by:''}`)});
  // 4. manual adjustments (override register): manual matches, channel moves, lane adjustments
  if(reg){
    const n=k=>(reg.rows||[]).filter(x=>x.kind_key===k).length;
    const lane=Math.max(0,n('lane_adj')-chs.filter(c=>c.accepted).length);   // accepts are listed above
    out.push({key:'adj',ok:!reg.missing_reason,title:'การปรับปรุงด้วยมือ',
      detail:`จับคู่ด้วยมือ ${n('manual_match')} · ย้ายช่อง ${n('channel_move')} · ปรับยอดช่อง ${lane}`
        +(reg.missing_reason?` · ไม่มีเหตุผล ${reg.missing_reason} รายการ`:''),items:[],reg:true});
  } else out.push({key:'adj',ok:false,title:'การปรับปรุงด้วยมือ',detail:'โหลดทะเบียนไม่สำเร็จ — เปิดทะเบียนการปรับปรุงเพื่อตรวจ',items:[],reg:true});
  // 5. what still blocks the close: to-dos and pending discrepancies must both be 0
  const todo=(r.todo||[]).length, pend=(r.session&&r.session.pending_discrepancies)||0;
  out.push({key:'todo',ok:!todo,title:'เรื่องค้างก่อนปิดเดือน',detail:todo?`เหลือ ${todo} รายการ`:'ไม่มี',
    items:(r.todo||[]).map(t=>t.label),link:'CLOSE'});
  out.push({key:'disc',ok:!pend,title:'ข้อแตกต่างที่ยังไม่ได้ตัดสิน',detail:pend?`ค้าง ${pend} รายการ (ต้องเป็น 0)`:'0',items:[],disc:true});
  return out;
}

/* ── shared sheet pieces ── */
const _SO_MAX_ITEMS=8;
function _soHead(icon,kicker,r){
  return `<div class="so-h"><i class="ti ti-${icon} so-hi" aria-hidden="true"></i>
    <div class="fx-1"><div class="lbl">${kicker}</div><h2 id="so-title">${esc(r.branch_name)} · ${esc(monthTh(MONTH))}</h2></div>
    <button class="btn btn-sm" type="button" onclick="closeSheet()" aria-label="ปิด"><i class="ti ti-x" aria-hidden="true"></i></button></div>`;
}
function _soFacts(pairs){
  return `<dl class="so-facts">${pairs.map(([k,v])=>`<div><dt>${k}</dt><dd>${esc(v||'—')}</dd></div>`).join('')}</dl>`;
}
function _soList(checks){
  const bid=DATA.branch_id;
  return `<ul class="so-list">${checks.map(c=>{
    const more=c.items.length>_SO_MAX_ITEMS?`<li>… อีก ${c.items.length-_SO_MAX_ITEMS} รายการ</li>`:'';
    const link=c.link?` <a href="#b/${bid}/${c.link}" onclick="closeSheet()">ไปที่รายการ ›</a>`
      :c.disc?` <a href="#discrepancies?bid=${bid}" onclick="closeSheet()">ดูข้อแตกต่าง ›</a>`:'';
    return `<li class="so-item ${c.ok?'so-ok':'so-bad'}"><span class="so-ic" aria-hidden="true">${c.ok?'✓':'!'}</span>
      <div class="so-b"><div><b>${c.title}</b><span class="sr-only">${c.ok?' — ผ่าน':' — ต้องตรวจ'}</span> <span class="lbl">${esc(c.detail)}</span>${c.ok?'':link}</div>
      ${c.items.length?`<ul class="so-sub">${c.items.slice(0,_SO_MAX_ITEMS).map(x=>`<li>${esc(x)}</li>`).join('')}${more}</ul>`:''}</div></li>`;
  }).join('')}</ul>`;
}
function _soLinks(bid){
  return `<div class="so-links">
    <a class="btn btn-sm" href="/api/reports/recon-certificate.print?period_month=${MONTH}&branch_id=${bid}" target="_blank" rel="noopener"><i class="ti ti-certificate" aria-hidden="true"></i> ดูใบรับรอง <span class="lbl">↗</span></a>
    <a class="btn btn-sm" href="/api/reports/override-register.print?period_month=${MONTH}&branch_id=${bid}" target="_blank" rel="noopener"><i class="ti ti-writing" aria-hidden="true"></i> ทะเบียนการปรับปรุง <span class="lbl">↗</span></a></div>`;
}
// who signs: the login (name shown + own password) or, login off, a typed name + the manager code
function _soWho(verb,askName){
  if(ME.login_enabled){
    const u=ME.user||{};
    return `<div class="so-sign"><div>${verb}โดย <b>${esc(u.display_name||u.username||'')}</b> <span class="lbl">(จากการเข้าสู่ระบบ)</span></div>
      <label class="fld mt-8"><span class="lbl">รหัสผ่านของคุณ — ยืนยันตัวตนอีกครั้งก่อน${verb}</span>
        <input id="so-pw" class="ctl" type="password" autocomplete="current-password" onkeydown="_soEnter(event)"></label></div>`;
  }
  let code='';try{code=sessionStorage.getItem('mgr_code')||'';}catch(e){}
  return `<div class="so-sign">
    ${askName?`<label class="fld"><span class="lbl">ชื่อผู้จัดการที่${verb}</span><input id="so-who" class="ctl" autocomplete="name"></label>`:''}
    <label class="fld"><span class="lbl">รหัสผู้จัดการ</span><input id="so-code" class="ctl" type="password" autocomplete="off" value="${esc(code)}" onkeydown="_soEnter(event)"></label></div>`;
}
// Enter in the password / code field = press the sheet's main button
function _soEnter(e){ if(e.key==='Enter'){e.preventDefault();const b=$('so-go');if(b&&!b.disabled)b.click();} }
// read + check the approver fields → {pw, who, code} or null (the error is shown in #so-err)
function _soCreds(askName){
  const e=$('so-err');
  if(ME.login_enabled){
    const pw=($('so-pw')||{}).value||'';
    if(!pw){e.textContent='ใส่รหัสผ่านของคุณเพื่อยืนยันตัวตน';$('so-pw').focus();return null;}
    return {pw};
  }
  const who=askName?((($('so-who')||{}).value)||'').trim():'';
  if(askName&&!who){e.textContent='ใส่ชื่อผู้จัดการที่อนุมัติ';$('so-who').focus();return null;}
  return {who,code:((($('so-code')||{}).value)||'').trim()};
}
function _soPatch(sid,fd,c){
  if(ME.login_enabled)fd.append('password',c.pw);
  return api(`/api/sessions/${sid}/status`,{method:'PATCH',body:fd,quiet:true,   // the sheet shows the refusal inline
    headers:(!ME.login_enabled&&c.code)?{'X-Manager-Token':c.code}:{}});
}
function _soFail(e){
  const el=$('so-err'); if(el)el.textContent=e.message||String(e);
  const pw=$('so-pw')||$('so-code'); if(pw){pw.value='';pw.focus();}
  hint403(e);
}

/* ── ปิดเดือน (ผู้จัดการ) ── */
async function closeMonth(bid,btn){
  if(!DATA||DATA.branch_id!==bid)return;
  await busy('signoff',btn,async()=>{
    let reg=null;
    try{reg=await api(`/api/reports/override-register?period_month=${MONTH}&branch_id=${bid}`);}catch(e){reg=null;}
    if(!DATA||DATA.branch_id!==bid)return;
    const r=DATA, ses=r.session||{}, checks=signoffChecks(r,reg,MONTH);
    const why=closeBlock(r);
    const me=((ME.user&&ME.user.username)||'').toLowerCase();
    const sod=ME.login_enabled&&me&&me===String(ses.created_by||'').toLowerCase();
    showSheet(`<div role="dialog" aria-modal="true" aria-labelledby="so-title">
      ${_soHead('signature','ลงนามปิดเดือน',r)}
      ${_soFacts([['ผู้จัดทำ',ses.created_by],['ส่งตรวจเมื่อ',ses.sent_for_review_at?tsBkk(ses.sent_for_review_at)+(ses.sent_for_review_by?' · '+ses.sent_for_review_by:''):''],
        ['ยอด POS',`฿${fmt((r.channels||[]).reduce((a,c)=>a+(c.pos||0),0))}`],['ตรงแล้ว',`${r.progress_pct}%`]])}
      <div class="sec-h">สิ่งที่คุณกำลังรับรอง</div>
      ${_soList(checks)}
      ${_soLinks(bid)}
      <div class="so-note lbl">การลงนาม = บันทึกผลถาวร ล็อกการแก้ไขทั้งเดือน และแช่แข็งหลักฐาน (Excel + ลายนิ้วมือไฟล์) ·
        ถ้าต้องแก้ภายหลัง ผู้จัดการกด "เปิดงวดใหม่" พร้อมเหตุผล</div>
      ${why?`<div class="close-why mt-8"><i class="ti ti-alert-circle" aria-hidden="true"></i> ยังปิดไม่ได้: ${esc(why)}</div>`:''}
      ${sod?`<div class="close-why mt-8"><i class="ti ti-alert-circle" aria-hidden="true"></i> คุณเป็นผู้จัดทำเดือนนี้ — ตามหลักแบ่งแยกหน้าที่ (SoD) ต้องให้ผู้จัดการคนอื่นลงนาม</div>`:''}
      ${why||sod?'':_soWho('อนุมัติ',true)}
      <div id="so-err" class="login-err" role="alert" aria-live="polite"></div>
      <div class="row jc-end gap-8 so-act">
        <button class="btn" type="button" onclick="closeSheet()">ยกเลิก</button>
        <button class="btn btn-pri" type="button" id="so-go" onclick="submitSignoff(${bid},this)"${why||sod?' disabled':''}><i class="ti ti-signature" aria-hidden="true"></i> ลงนามปิดเดือน</button>
      </div></div>`,'sheet-sign');
    _soFocus($('so-pw')||$('so-who'));
  });
}
async function submitSignoff(bid,btn){
  const c=_soCreds(true); if(!c)return;
  $('so-err').textContent='';
  await busy('status',btn,async()=>{
    try{
      const sid=await ensureSession(bid);
      await api(`/api/sessions/${sid}/run-match`,{method:'POST'});   // the snapshot the close freezes
      await _soPatch(sid,stForm('approved',c.who),c);
      if(!ME.login_enabled&&c.code){try{sessionStorage.setItem('mgr_code',c.code);}catch(e){}}
      closeSheet();toast('ลงนามปิดเดือนแล้ว — ล็อกเดือนและแช่แข็งหลักฐานเรียบร้อย');route();
    }catch(e){_soFail(e);}
  });
}

/* ── เปิดงวดใหม่ (approved/closed → กำลังทำ): เหตุผล ≥10 ตัวอักษร + รหัสผ่าน (login) / รหัสผู้จัดการ ── */
function reopenMonth(bid){
  if(!DATA||!DATA.session){toast('ไม่พบงวดของสาขานี้');return;}
  const r=DATA, ses=r.session;
  showSheet(`<div role="dialog" aria-modal="true" aria-labelledby="so-title">
    ${_soHead('lock-open','เปิดงวดใหม่',r)}
    ${_soFacts([['ปิดโดย',ses.approved_by],['ปิดเมื่อ',tsBkk(ses.approved_at)],['ผู้จัดทำ',ses.created_by]])}
    <div class="lbl mt-8">ปลดล็อกเดือนที่ปิดแล้วให้กลับมาแก้ไขได้ (สถานะกลับเป็น "กำลังทำ") — เหตุผลจะบันทึกในประวัติการแก้ไข ·
      แก้เสร็จต้องส่งตรวจและลงนามปิดเดือนใหม่ ระบบจะแช่แข็งหลักฐานชุดใหม่ให้</div>
    <label class="fld mt-10"><span class="lbl">เหตุผลที่ต้องเปิดงวดใหม่ (อย่างน้อย 10 ตัวอักษร)</span>
      <textarea id="reopen-note" class="sheet-ta m-0" placeholder="เช่น พบสเตทเมนต์ KBANK วันที่ 30 อัปไม่ครบ"></textarea></label>
    ${_soWho('เปิดงวด',false)}
    <div id="so-err" class="login-err" role="alert" aria-live="polite"></div>
    <div class="row jc-end gap-8 so-act">
      <button class="btn" type="button" onclick="closeSheet()">ยกเลิก</button>
      <button class="btn btn-pri" type="button" id="so-go" onclick="submitReopen(${bid},this)"><i class="ti ti-lock-open" aria-hidden="true"></i> เปิดงวดใหม่</button>
    </div></div>`,'sheet-sign');
  _soFocus($('reopen-note'));
}
// desktop: straight to the field · phone: start at the top (read the checklist first, no keyboard popping up)
function _soFocus(field){
  if(field&&window.innerWidth>640){field.focus();return;}
  const h=$('so-title'); if(h){h.setAttribute('tabindex','-1');h.focus({preventScroll:true});}
}
async function submitReopen(bid,btn){
  const reason=(($('reopen-note')||{}).value||'').trim();
  if(reason.length<10){$('so-err').textContent='ต้องใส่เหตุผลอย่างน้อย 10 ตัวอักษร';$('reopen-note').focus();return;}
  const c=_soCreds(false); if(!c)return;
  const sid=DATA&&DATA.session&&DATA.session.session_id; if(!sid)return;
  $('so-err').textContent='';
  await busy('status',btn,async()=>{
    try{
      await _soPatch(sid,stForm('draft','',reason),c);
      if(!ME.login_enabled&&c.code){try{sessionStorage.setItem('mgr_code',c.code);}catch(e){}}
      closeSheet();toast('เปิดงวดใหม่แล้ว — แก้ไขได้ แล้วส่งตรวจ/ลงนามปิดเดือนอีกครั้ง');route();
    }catch(e){_soFail(e);}
  });
}
// Esc closes an open sheet (sign-off, reopen and every other bottom sheet)
document.addEventListener('keydown',e=>{
  if(e.key!=='Escape')return;
  const o=$('sheet'); if(o&&o.style.display!=='none')closeSheet();
});
