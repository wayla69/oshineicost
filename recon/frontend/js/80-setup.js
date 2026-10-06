/* recon frontend — 80-setup.js: #setup — payment map, lanes, bank accounts, MIDs, statement codes.
   Plain scripts sharing one global scope, loaded in order by index.html (00 → 90);
   a slice may call any other slice's functions, but top-level code only uses earlier slices. */
/* ════════════════ PHASE 4 — #setup (ตั้งค่าระบบ in-app) ════════════════ */
const _SETUP_CATS=['CASH','QR','TRANSFER','CREDIT','GRAB','LINEMAN','COUPON','ENTERTAIN','OTHER'];
const _SETUP_BANKS=['KTB','KBANK','KTC','BAY','SCB','BBL','TTB','PROMPTPAY','ANY','NONE','UNKNOWN'];
const _SETUP_ST_CHIP={ok:['c-ok','✓ พร้อม'],needs_bank:['c-err','<i class="ti ti-alert-triangle" aria-hidden="true"></i> ระบุธนาคาร'],dormant:['c-mut','พักใช้ (฿0)'],off:['c-mut','ไม่กระทบยอด']};
const _SETUP_DOC_TH={deposit_slip:'สลิปนำฝาก',cc_summary:'สรุปยอดบัตร',grab_report:'รายงาน Grab',
  lineman_report:'รายงาน Line Man',points_summary:'สรุปคะแนน',
  'bank_stmt_KTB':'สเตทเมนต์ KTB','bank_stmt_KBANK':'สเตทเมนต์ KBANK','bank_stmt_*':'สเตทเมนต์ธนาคาร'};
const _SETUP_PARSE_CH_TH={QR:'QR พร้อมเพย์',TRANSFER:'เงินโอน',CASH_DEP:'เงินสด (ฝากเข้าบัญชี)',
  CC_SETTLE:'บัตรเครดิต',GRAB_PAYOUT:'Grab',LINEMAN_PAYOUT:'Line Man',
  FEE:'ค่าธรรมเนียม (ไม่ใช่รายได้)',OTHER:'ไม่นับ / ไม่ใช่รายได้'};

let _SETUP_M=null, _SETUP_FILTER='all', _SETUP_CODES=null, _SETUP_CODE_OPTS=[];

async function loadSetup(){
  const t=++_ROUTE_SEQ;
  const el=$('app');
  el.innerHTML=header()+'<div class="muted" style="padding:20px 0">กำลังโหลดการตั้งค่า…</div>';
  let m;
  try{m=await api(`/api/setup/master?period_month=${MONTH}`);}
  catch(e){if(_cur(t))el.innerHTML=header()+`<div class="card c-err mt-10">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`;return;}
  if(!_cur(t))return;
  _SETUP_M=m;
  _setupRender();
}

function _setupSetFilter(f){_SETUP_FILTER=f;_setupRender();}
function _setupChangeMonth(m){MONTH=m;localStorage.setItem('v2_month',m);loadSetup();}

function _setupDocChips(r){
  const m=r.method, ds=r.doc_status||{};
  const chips=[];
  for(const k of ['doc1','doc2']){
    if(!m[k])continue;
    const up=ds[k]==='uploaded';
    chips.push(`<span style="font-size:11px;padding:1px 7px;border-radius:var(--radius-sm);display:inline-block;margin:1px 2px 1px 0;background:${up?'var(--ok-bg)':'var(--warn-bg)'};color:${up?'var(--ok)':'var(--warn)'}">${up?'✓':'…'} ${esc(_SETUP_DOC_TH[m[k]]||m[k])}</span>`);
  }
  return chips.join('')||'<span class="lbl">—</span>';
}
function _setupParamTxt(m){
  if(!m.method)return '<span class="lbl">—</span>';
  const p=[];
  if(m.tol_abs_day!=null)p.push(`±฿${fmt(m.tol_abs_day)}/วัน`);
  if(m.tol_abs!=null)p.push(`±฿${fmt(m.tol_abs)}/เดือน`);
  if(m.lag_days!=null)p.push(`เงินเข้าช้าได้ ${m.lag_days} วัน`);
  if(m.merge_days!=null&&m.merge_days>1)p.push(`รวมได้ ${m.merge_days} วัน`);
  if(m.fee_vat_expected)p.push('<span class="chip c-warn">มีค่าธรรมเนียม+VAT</span>');
  return p.join(' · ')||'<span class="lbl">ค่ามาตรฐาน</span>';
}

function _setupRender(){
  const rows=_SETUP_M.rows.filter(r=>{
    if(_SETUP_FILTER==='attention')return r.status==='needs_bank';
    if(_SETUP_FILTER==='active')return r.status==='ok';
    return true;
  });

  let html=header();
  // page title card
  html+=`<div class="card mb-12"><div class="row">
    <div class="logo-sm"><i class="ti ti-settings" aria-hidden="true"></i></div>
    <div><h2 class="m-0 fs-16">ตั้งค่าระบบกระทบยอด</h2>
      <div class="lbl">ตารางจับคู่รหัสรับเงิน · วิธีกระทบยอด · บัญชีธนาคาร · MID · รหัสสเตทเมนต์</div></div>
    <input type="month" value="${esc(MONTH)}" onchange="_setupChangeMonth(this.value)"
      class="ctl-lg ml-auto">
    <a href="#" onclick="location.hash='';return false" class="btn btn-sm">← กลับ</a>
  </div></div>`;

  // ① payment map
  html+=`<div class="card mb-12"><div class="row mb-8">
    <h2 class="m-0 fs-15">① ตารางจับคู่รหัสรับเงิน</h2>
    <span class="lbl">${_SETUP_M.rows.length} รหัส · เดือน ${MONTH}</span>
    <div class="row ml-auto gap-6">
      ${['all|ทั้งหมด','attention|<i class="ti ti-alert-triangle" aria-hidden="true"></i> ต้องระบุธนาคาร','active|ใช้งานอยู่'].map(x=>{
        const [k,t]=x.split('|');
        return `<button class="btn btn-sm" style="${_SETUP_FILTER===k?'background:var(--brand-soft);border-color:var(--brand)':''}" onclick="_setupSetFilter('${k}')">${t}</button>`;}).join('')}
      <button class="btn btn-sm" onclick="_setupReseedPos()">Seed จากข้อมูล POS</button>
      <button class="btn btn-sm" onclick="_setupSeedRequirement()" title="ใส่ค่าตามตารางเจ้าของ (ไม่ทับค่าที่แก้เอง)">ใช้ค่าตามตารางเจ้าของ</button>
    </div></div>
  <div class="setup-scroll"><table class="setup-tbl">
    <tr><th>รหัส POS</th><th>ประเภท</th><th>ธนาคารปลายทาง</th><th>เอกสารที่ใช้ตรวจ</th>
        <th>วิธีกระทบยอด</th><th>พารามิเตอร์</th><th class="tr">ยอด POS</th>
        <th>กระทบ</th><th>สถานะ</th></tr>
    ${rows.map(r=>{
      const [cls,lab]=_SETUP_ST_CHIP[r.status]||['c-mut',r.status];
      return `<tr>
        <td class="fw-600 ff-mono fs-12">${esc(r.payment_code_norm)}</td>
        <td><select onchange="_setupSaveMap(${r.id},this,'category')" class="fs-12">
          ${_SETUP_CATS.map(c=>`<option ${c===r.category?'selected':''}>${c}</option>`).join('')}</select></td>
        <td><select onchange="_setupSaveMap(${r.id},this,'bank')" style="font-size:12px;${r.status==='needs_bank'?'border-color:var(--err)':''}">
          ${_SETUP_BANKS.map(b=>`<option ${b===r.bank?'selected':''}>${b}</option>`).join('')}</select></td>
        <td>${_setupDocChips(r)}</td>
        <td class="fs-12">${r.method.method?`<span title="${esc(r.method.method)}">${esc(r.method.method_label||r.method.method)}</span>
          <div class="lbl">lane ${esc(r.category)}·${esc(r.method.lane_bank||'*')}
          ${r.method.id?`<a href="#" onclick="_setupEditLane(${r.method.id});return false" class="fs-11">แก้ไข ▸</a>`:''}</div>`
          :'<span class="lbl">—</span>'}</td>
        <td class="fs-12">${_setupParamTxt(r.method)}</td>
        <td class="tr tnum fs-12" title="${r.pos_txn} รายการ">${fmt(r.pos_amount)}</td>
        <td class="tc"><input type="checkbox" ${r.reconcile?'checked':''} onchange="_setupSaveMap(${r.id},this,'reconcile')"
             title="ติ๊กออก = ไม่นำมากระทบยอด (เช่น Entertain)"></td>
        <td><span class="chip ${cls} fs-11">${lab}</span></td>
      </tr>`;}).join('')}
  </table></div>
  <div class="muted mt-6 fs-12">แก้ประเภท/ธนาคาร = สิทธิ์พนักงาน (<i class="ti ti-key" aria-hidden="true"></i>) · แก้วิธี/พารามิเตอร์ lane = ต้องรหัสผู้จัดการ · มีผลกับทุกรหัสใน lane เดียวกัน</div>
  <div id="setup-lane-editor"></div></div>`;

  // ② lane method configs
  html+=`<div class="card mb-12"><h2 class="fs-15 mb-8">② วิธีกระทบยอดราย lane (ประเภท × ธนาคาร)</h2>
  <div class="setup-scroll"><table class="setup-tbl">
    <tr><th>Lane</th><th>เอกสาร 1</th><th>เอกสาร 2</th><th>วิธี</th>
        <th class="tr">tol/วัน</th><th class="tr">tol/เดือน</th>
        <th class="tr">lag</th><th class="tr">รวมวัน</th>
        <th>fee+VAT</th><th>หมายเหตุ</th><th></th></tr>
    ${_SETUP_M.method_configs.map(c=>`<tr>
      <td class="fw-600 ff-mono fs-12">${esc(c.category)}·${esc(c.bank)}</td>
      <td class="fs-12">${esc(_SETUP_DOC_TH[c.doc1]||c.doc1||'—')}</td>
      <td class="fs-12">${esc(_SETUP_DOC_TH[c.doc2]||c.doc2||'—')}</td>
      <td class="fs-12" title="${esc(c.method)}">${esc(c.method)}</td>
      <td class="tr tnum fs-12">${c.tol_abs_day==null?'—':fmt(c.tol_abs_day)}</td>
      <td class="tr tnum fs-12">${c.tol_abs==null?'—':fmt(c.tol_abs)}</td>
      <td class="tr fs-12">${c.lag_days==null?'—':c.lag_days}</td>
      <td class="tr fs-12">${c.merge_days==null?'—':c.merge_days}</td>
      <td>${c.fee_vat_expected?'<span class="chip c-warn fs-11">มี</span>':'<span class="lbl">—</span>'}</td>
      <td class="lbl maxw-220 fs-12">${esc(c.notes||'')}</td>
      <td><button class="btn btn-sm" onclick="_setupEditLane(${c.id})">แก้ไข</button></td>
    </tr>`).join('')}
  </table></div>
  <div class="muted mt-6 fs-12">tol ว่าง = ค่ากลาง (±฿${fmt(_SETUP_M.globals.TOL_ABS_DAY)}/วัน หรือ ${_SETUP_M.globals.TOL_PCT_DAY}% · ±฿${fmt(_SETUP_M.globals.TOL_ABS)}/เดือน หรือ ${_SETUP_M.globals.TOL_PCT}%) · ${_SETUP_M.manager_required?'ต้องใช้รหัสผู้จัดการ':'เซิร์ฟเวอร์นี้ยังไม่ตั้งรหัสผู้จัดการ (แก้ได้เลย)'}</div>
  </div>`;

  // ③ bank accounts
  html+=`<div class="card mb-12"><div class="row mb-6">
    <h2 class="m-0 fs-15">③ บัญชีธนาคารของสาขา</h2>
    <button class="btn btn-sm ml-auto" onclick="_setupAddAccount()">+ เพิ่มบัญชี</button></div>
  <div class="setup-scroll"><table class="setup-tbl">
    <tr><th>สาขา</th><th>ธนาคาร</th><th>เลขบัญชี</th><th>ชื่อบัญชี</th><th>ใช้งาน</th><th></th></tr>
    ${_SETUP_M.bank_accounts.map(a=>`<tr>
      <td class="fs-12">${esc(a.branch_name)}</td>
      <td class="fs-12">${esc(a.bank)}</td>
      <td class="ff-mono fs-12">${esc(a.account_no)}</td>
      <td class="fs-12">${esc(a.account_name||'')}</td>
      <td class="fs-12">${a.active?'✓':'—'}</td>
      <td><button class="btn btn-sm t-err" onclick="_setupDelAccount(${a.account_id})">ลบ</button></td>
    </tr>`).join('')||'<tr><td colspan="6" class="muted pad-14-9">ยังไม่มีบัญชี — ระบบใช้เลขบัญชีจับเอกสารเข้าสาขาอัตโนมัติ</td></tr>'}
  </table></div></div>`;

  // ④ MIDs
  html+=`<div class="card mb-12"><div class="row mb-6">
    <h2 class="m-0 fs-15">④ MID เครื่องรูดบัตร / คะแนน</h2>
    <button class="btn btn-sm ml-auto" onclick="_setupAddMid()">+ เพิ่ม MID</button></div>
  <div class="setup-scroll"><table class="setup-tbl">
    <tr><th>สาขา</th><th>ผู้รับบัตร</th><th>Merchant ID</th><th>Terminal</th><th>ใช้งาน</th><th></th></tr>
    ${_SETUP_M.acquirer_mids.map(m=>`<tr>
      <td class="fs-12">${esc(m.branch_name)}</td>
      <td class="fs-12">${esc(m.acquirer)}</td>
      <td class="ff-mono fs-12">${esc(m.merchant_id||'')}</td>
      <td class="ff-mono fs-12">${esc(m.terminal_id||'')}</td>
      <td class="fs-12">${m.active?'✓':'—'}</td>
      <td><button class="btn btn-sm t-err" onclick="_setupDelMid(${m.mid_id})">ลบ</button></td>
    </tr>`).join('')||'<tr><td colspan="6" class="muted pad-14-9">ยังไม่มี MID</td></tr>'}
  </table></div>
  <div class="muted mt-6 fs-12">MID แบบ KTCPOINT / BAYPOINT ใช้แยกแถวแต้มออกจากใบสรุปบัตร — จำเป็นต่อช่องทางคูปอง (แต้ม KTC/BAY)</div></div>`;

  // ⑤ statement-code remap
  html+=`<div class="card mb-12"><div class="row mb-6">
    <h2 class="m-0 fs-15">⑤ รหัสในสเตทเมนต์ → ช่องทาง</h2>
    <a href="/api/statement-codes.html" target="_blank" class="lbl ml-auto"><i class="ti ti-printer" aria-hidden="true"></i> พิมพ์ใบให้ทีมตรวจ</a>
    <a href="/api/statement-codes.xlsx" class="lbl ml-12">Excel</a></div>
  <div class="muted mb-8 fs-12">แก้การจัดประเภทรหัสในสเตทเมนต์เองได้เมื่อทีมตรวจพบว่าผิด (เช่น <b>SDCH</b>=นำเงินสดไปฝาก ไม่ใช่ QR) — มีผลทันทีทั้งข้อมูลเดิมและไฟล์ที่อัปใหม่ · ใช้ <b>OTHER</b> สำหรับรายการที่ไม่ใช่รายได้ · ต้องใช้รหัสผู้จัดการ</div>
  <div id="setup-codes-body" class="muted">กำลังโหลด…</div></div>`;

  $('app').innerHTML=html;
  _setupLoadCodes();
}

/* ── ⑤ statement-code remap ── */
async function _setupLoadCodes(){
  const b=$('setup-codes-body'); if(!b)return;
  let j;try{j=await api('/api/statement-codes');}
  catch(e){b.innerHTML='โหลดไม่สำเร็จ: '+esc(e.message);return;}
  _SETUP_CODES=j.codes; _SETUP_CODE_OPTS=j.channel_options||Object.keys(_SETUP_PARSE_CH_TH);
  b.innerHTML=`<div class="setup-scroll"><table class="setup-tbl">
    <tr><th>รหัส / คำ</th><th>ความหมาย</th><th>ระบบจัดเป็น</th><th>แก้เป็น</th><th></th></tr>
    ${_SETUP_CODES.map((c,i)=>{
      const eff=c.effective_channel;
      const badge=c.overridden?'<span class="chip c-ok fs-11">ทีมแก้แล้ว</span>'
        :c.status==='review'?'<span class="chip c-warn fs-11"><i class="ti ti-alert-triangle" aria-hidden="true"></i> รอตรวจ</span>':'<span class="chip c-mut fs-11">✓</span>';
      return `<tr>
        <td class="ff-mono fs-12">${esc(c.code)}<br>${badge}</td>
        <td class="lbl">${esc(c.meaning_th)}</td>
        <td class="fs-12">${esc(c.effective_channel_th||c.channel_th)}${c.overridden?` <span class="lbl">(เดิม ${esc(c.channel_th)})</span>`:''}</td>
        <td><select id="setup-cc-${i}" class="fs-12">${_SETUP_CODE_OPTS.map(o=>`<option value="${esc(o)}" ${o===eff?'selected':''}>${esc(_SETUP_PARSE_CH_TH[o]||o)}</option>`).join('')}</select></td>
        <td><button class="btn btn-sm btn-pri" onclick="_setupSaveCode(${i})">บันทึก</button>
          ${c.overridden?` <button class="btn btn-sm" onclick="_setupRevertCode(${i},this)">คืนค่าเดิม</button>`:''}</td>
      </tr>`;}).join('')}
  </table></div>`;
}
async function _setupSaveCode(i){
  if(!_SETUP_CODES)return;
  const c=_SETUP_CODES[i]; const ch=$('setup-cc-'+i).value;
  const token=(c.override_code||c.code||'').toString().split(/[\s/|]/)[0];
  if(!token){toast('ไม่พบรหัส');return;}
  try{
    const r=await mgrApi('/api/statement-codes/override',{method:'POST',
      body:{code:token,channel:ch,note:c.meaning_th||'',by:'manager'}});
    toast(`บันทึก ${token} → ${_SETUP_PARSE_CH_TH[ch]||ch} (แก้ ${r.lines_reclassified} บรรทัด)`);
    _setupLoadCodes();
  }catch(e){toast('ไม่สำเร็จ: '+e.message);}
}
async function _setupRevertCode(i,btn){
  const c=(_SETUP_CODES||[])[i]; if(!c)return;
  const code=c.override_code||c.code;
  if(btn)btn.disabled=true;
  try{
    const r=await mgrApi('/api/statement-codes/override/'+encodeURIComponent(code),{method:'DELETE'});
    toast(`คืนค่าเดิม ${code} (แก้ ${r.lines_reclassified} บรรทัด)`);
    _setupLoadCodes();
  }catch(e){toast('ไม่สำเร็จ: '+e.message);if(btn&&btn.isConnected)btn.disabled=false;}
}

/* ── ① payment map ── */
async function _setupSaveMap(id,el,field){
  const r=_SETUP_M.rows.find(x=>x.id===id);
  const body={category:r.category,bank:r.bank,reconcile:r.reconcile,notes:r.notes||''};
  body[field]=field==='reconcile'?(el.checked?1:0):el.value;
  try{
    await api(`/api/payment-map/${id}`,{method:'PATCH',body});
    toast(`บันทึก ${r.payment_code_norm} แล้ว`);
    loadSetup();
  }catch(e){toast('ไม่สำเร็จ: '+e.message);loadSetup();}
}

/* ── ② lane editor (inline) ── */
function _setupEditLane(cfgId){
  const c=_SETUP_M.method_configs.find(x=>x.id===cfgId);
  if(!c)return;
  const box=$('setup-lane-editor'); if(!box)return;
  const docs1=['','deposit_slip','cc_summary','grab_report','lineman_report','points_summary'];
  const docs2=['','bank_stmt_KTB','bank_stmt_KBANK','bank_stmt_*'];
  const methods=['direct','two_stage','cash_3way','itemized','issuer_points'];
  box.innerHTML=`<div class="card bg-subtle bdc-info mt-10">
    <div class="row"><b>แก้ lane ${esc(c.category)}·${esc(c.bank)}</b>
      <span class="chip c-setup">มีผลกับทุกรหัสใน lane นี้ · ต้องใช้รหัสผู้จัดการ</span>
      <a href="#" class="lbl ml-auto" onclick="$('setup-lane-editor').innerHTML='';return false">ปิด</a></div>
    <div class="row" style="margin-top:8px;gap:14px">
      <label class="lbl">เอกสาร 1<br><select id="sle-doc1">${docs1.map(d=>`<option value="${d}" ${d===(c.doc1||'')?'selected':''}>${d?(_SETUP_DOC_TH[d]||d):'—'}</option>`).join('')}</select></label>
      <label class="lbl">เอกสาร 2<br><select id="sle-doc2">${docs2.map(d=>`<option value="${d}" ${d===(c.doc2||'')?'selected':''}>${d?(_SETUP_DOC_TH[d]||d):'—'}</option>`).join('')}</select></label>
      <label class="lbl">วิธี<br><select id="sle-method">${methods.map(m=>`<option ${m===c.method?'selected':''}>${m}</option>`).join('')}</select></label>
      <label class="lbl">tol/วัน (บาท)<br><input type="number" id="sle-tday" step="1" min="0" value="${c.tol_abs_day==null?'':c.tol_abs_day}" placeholder="ค่ากลาง" class="w-80"></label>
      <label class="lbl">tol/เดือน (บาท)<br><input type="number" id="sle-tmon" step="1" min="0" value="${c.tol_abs==null?'':c.tol_abs}" placeholder="ค่ากลาง" class="w-80"></label>
      <label class="lbl">เงินเข้าช้าได้ (วัน 0–14)<br><input type="number" id="sle-lag" step="1" min="0" max="14" value="${c.lag_days==null?'':c.lag_days}" class="w-60"></label>
      <label class="lbl">รวมได้กี่วัน (1–7)<br><input type="number" id="sle-merge" step="1" min="1" max="7" value="${c.merge_days==null?'':c.merge_days}" class="w-60"></label>
      <label class="lbl">มีค่าธรรมเนียม+VAT<br><input type="checkbox" id="sle-fee" ${c.fee_vat_expected?'checked':''}></label>
    </div>
    <div class="row mt-10">
      <input type="text" id="sle-notes" value="${esc(c.notes||'')}" placeholder="หมายเหตุ"
        style="flex:1;min-width:200px;font-family:inherit;font-size:13px;padding:6px 9px;border:1px solid var(--line);border-radius:var(--radius-sm);background:var(--input);color:var(--txt)">
      <button class="btn btn-pri btn-sm" onclick="_setupSaveLane(${c.id})">บันทึก (ผู้จัดการ)</button>
    </div></div>`;
  box.scrollIntoView({behavior:_scrollBehavior(),block:'center'});
}
async function _setupSaveLane(cfgId){
  const c=_SETUP_M.method_configs.find(x=>x.id===cfgId);
  const numVal=id=>{const x=$(id).value.trim();return x===''?null:+x;};
  const body={category:c.category,bank:c.bank,
    doc1:$('sle-doc1').value||null, doc2:$('sle-doc2').value||null,
    method:$('sle-method').value,
    tol_abs_day:numVal('sle-tday'), tol_abs:numVal('sle-tmon'),
    lag_days:numVal('sle-lag'), merge_days:numVal('sle-merge'),
    fee_vat_expected:$('sle-fee').checked?1:0,
    notes:$('sle-notes').value};
  try{
    await mgrApi(`/api/method-config/${cfgId}`,{method:'PATCH',body});
    toast('บันทึก lane แล้ว');
    loadSetup();
  }catch(e){toast('ไม่สำเร็จ: '+e.message);}
}

/* ── seeds ── */
async function _setupReseedPos(){
  try{const r=await api('/api/payment-map/seed',{method:'POST'});
      toast(`เพิ่มรหัสใหม่ ${r.inserted} รหัส`);loadSetup();}
  catch(e){toast('ไม่สำเร็จ: '+e.message);}
}
async function _setupSeedRequirement(){
  if(!confirm('ใส่ค่าตามตารางเจ้าของ (Requirement)?\nรหัส/lane ที่แก้เองไว้จะไม่ถูกทับ'))return;
  try{const r=await mgrApi('/api/setup/seed-requirement',{method:'POST'});
      toast(`อัปเดต ${r.codes.upgraded+r.codes.inserted} รหัส, ${r.methods.inserted} lane`);loadSetup();}
  catch(e){toast('ไม่สำเร็จ: '+e.message);}
}

/* ── ③ bank accounts ── */
async function _setupAddAccount(){
  let brs;try{brs=await api('/api/branches');}catch(e){toast(e.message);return;}
  const pick=prompt(`สาขา? พิมพ์หมายเลข:\n${brs.map(b=>`${b.branch_id}=${b.name}`).join('  ')}`);
  if(pick===null)return;
  const bid=parseInt(pick.trim());
  if(!brs.some(b=>b.branch_id===bid)){toast('หมายเลขสาขาไม่ถูกต้อง');return;}
  const bank=prompt('ธนาคาร (KTB หรือ KBANK):','KBANK'); if(bank===null)return;
  const no=prompt('เลขบัญชี:'); if(!no)return;
  const name=prompt('ชื่อบัญชี (ไม่บังคับ):')||'';
  try{
    await api('/api/bank-accounts',{method:'POST',
      body:{branch_id:bid,bank:bank.trim().toUpperCase(),account_no:no.trim(),account_name:name}});
    toast('เพิ่มบัญชีแล้ว');loadSetup();
  }catch(e){toast('ไม่สำเร็จ: '+e.message);}
}
async function _setupDelAccount(id){
  if(!confirm('ลบบัญชีนี้?'))return;
  try{await api(`/api/bank-accounts/${id}`,{method:'DELETE'});toast('ลบแล้ว');loadSetup();}
  catch(e){toast('ไม่สำเร็จ: '+e.message);}
}

/* ── ④ MIDs ── */
async function _setupAddMid(){
  let brs;try{brs=await api('/api/branches');}catch(e){toast(e.message);return;}
  const pick=prompt(`สาขา? พิมพ์หมายเลข:\n${brs.map(b=>`${b.branch_id}=${b.name}`).join('  ')}`);
  if(pick===null)return;
  const bid=parseInt(pick.trim());
  if(!brs.some(b=>b.branch_id===bid)){toast('หมายเลขสาขาไม่ถูกต้อง');return;}
  const acq=prompt('ผู้รับบัตร (KTB/KBANK/KTC/BAY/SCB/BBL/KTCPOINT/BAYPOINT):','KTC'); if(acq===null)return;
  const mid=prompt('Merchant ID:')||'';
  const term=prompt('Terminal ID (ไม่บังคับ):')||'';
  try{
    await api('/api/acquirer-mids',{method:'POST',
      body:{branch_id:bid,acquirer:acq.trim().toUpperCase(),merchant_id:mid.trim(),terminal_id:term.trim()}});
    toast('เพิ่ม MID แล้ว');loadSetup();
  }catch(e){toast('ไม่สำเร็จ: '+e.message);}
}
async function _setupDelMid(id){
  if(!confirm('ลบ MID นี้?'))return;
  try{await api(`/api/acquirer-mids/${id}`,{method:'DELETE'});toast('ลบแล้ว');loadSetup();}
  catch(e){toast('ไม่สำเร็จ: '+e.message);}
}
/* ════════════════ END PHASE 4 ════════════════ */
