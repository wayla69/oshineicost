/* recon frontend — 20-branch.js: #b/<id> — branch page: channel buckets, documents/upload, accept, move, reclass.
   Plain scripts sharing one global scope, loaded in order by index.html (00 → 90);
   a slice may call any other slice's functions, but top-level code only uses earlier slices. */
/* ── หน้าสาขา ── */
/* ── Phase 2: bucket each channel for the branch inbox ── */
function _uploadChKeys(r){      // channel keys that have a genuine missing-doc upload task
  const keys=new Set();
  (r.todo||[]).forEach(t=>{ if(t.type==='upload' && t.channel) keys.add(t.channel); });
  return keys;                  // upload todos always carry .channel (reconcile_v2)
}
function classifyCh(ch,uploadKeys,missingStmtBanks){   // 'problem' | 'coin' | 'waiting' | 'green'
  if(ch.accepted)return 'green';
  if(ch.status==='no_source')return 'green';
  if(ch.status==='setup'||ch.status==='diff')return 'problem';
  if(ch.status==='waiting_doc'){
    if(uploadKeys.has(ch.key))return 'problem';          // missing doc that carries this channel → act
    // a fully-missing BANK STATEMENT backing this channel is actionable today,
    // not "รอเงินเข้าตามรอบ" — stmt docs carry channel=None so uploadKeys misses them
    if(missingStmtBanks&&(ch.banks||[]).some(b=>missingStmtBanks.has(b.bank)))return 'problem';
    return 'waiting';                                     // in-transit on schedule → timing drawer
  }
  // COUPON variance is loyalty points, not baht — never file it as ฿ "coin residue"
  if(ch.key!=='COUPON' && Math.abs(ch.variance)>0 && Math.abs(ch.variance)<=COIN_TOL) return 'coin';
  return 'green';
}
async function loadBranch(bid){
  const t=++_ROUTE_SEQ;
  $('app').innerHTML=header()+loadingState();
  let r;try{r=await api(`/api/v2/reconcile?branch_id=${bid}&month=${MONTH}`);}catch(e){if(_cur(t))$('app').innerHTML=header()+`<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`;return;}
  if(!_cur(t))return;
  let moves;try{moves=await api(`/api/channel-move?branch_id=${bid}&period_month=${MONTH}`);}catch(e){moves=[];}
  if(!_cur(t))return;              // ผู้ใช้เปลี่ยนหน้า/เดือนระหว่างรอ — ไม่แตะ DATA ของหน้าใหม่
  DATA=r; DATA.moves=moves; _DAYS={}; _MMS={};
  const ses=r.session,st=(ses&&ses.status)||'draft';
  const closed=isClosedSt(st);
  DATA.closed=closed;
  let html=header(`<a href="#" onclick="location.hash='';return false" class="lbl">← ทุกสาขา</a>`);
  html+=`<div class="card row mb-12">
    <div><div class="lbl">สาขา</div><h2>${esc(r.branch_name)} · ${MONTH}</h2></div>
    <span class="chip ${closed?'c-ok':st==='in_review'?'c-warn':'c-mut'}">${esc(SES_ST[st]||st)}</span>
    ${closed?`<span class="lbl">โดย ${esc(ses.approved_by||'ผู้จัดการ')} · ${esc((ses.approved_at||'').slice(0,16))}</span>`:''}
    ${ses?`<a class="btn btn-sm" href="/api/sessions/${ses.session_id}/export.xlsx">Excel</a>`:''}
    ${closed?`<a class="btn btn-sm" href="/api/reports/recon-certificate.print?period_month=${MONTH}&branch_id=${bid}" target="_blank"><i class="ti ti-certificate" aria-hidden="true"></i> ใบรับรอง</a>`:''}
    <a class="btn btn-sm" href="/api/monthly-report?branch_id=${bid}&period_month=${MONTH}" target="_blank" title="รายงานการจับคู่ทุกช่องทาง ไล่รายวันทั้งเดือน — ตรวจว่าจับคู่ถูกครบ · พิมพ์ PDF ได้"><i class="ti ti-printer" aria-hidden="true"></i> ตรวจการจับคู่รายวัน</a>
    <a class="btn btn-sm" href="/api/monthly-report?branch_id=${bid}&period_month=${MONTH}&detail=1" target="_blank" title="ฉบับละเอียดสำหรับผู้ตรวจสอบ — เพิ่มหลักฐานระดับบรรทัด: รหัส POS, ยอดบัตรราย acquirer, ทุกบรรทัดเงินเข้า, ค่าธรรมเนียม, การจับคู่มือ"><i class="ti ti-files" aria-hidden="true"></i> ฉบับตรวจสอบ (ละเอียด)</a>
    <a class="btn btn-sm" href="#payout?bid=${bid}" title="ตรวจยอดสุทธิ ↔ เงินโอนธนาคาร แยกรายวัน — Grab, Line Man, บัตร, BAY/KTC Point · สถานะ matched/รอเข้า"><i class="ti ti-credit-card" aria-hidden="true"></i> เงินเข้าธนาคาร</a>
    ${ses?`<a class="btn btn-sm" href="#discrepancies?bid=${bid}" title="จัดการข้อแตกต่าง — กรอง/จัดเรียง/bulk-resolve/มอบหมาย/drill รายวัน"><i class="ti ti-settings" aria-hidden="true"></i> ข้อแตกต่าง</a>`:''}
    <div class="row ml-auto fx-nowrap">
      <div class="prog ${progCls(r.progress_pct)}" style="width:140px;flex:none"><div style="width:${r.progress_pct}%"></div></div>
      <span class="mono fs-14 fw-600">${r.progress_pct}%</span>
      <span class="lbl">ตรงแล้ว</span>
    </div></div>`;

  if(closed) html+=`<div class="card c-ok row mb-12 bg-ok"><span style="flex:1;min-width:220px"><i class="ti ti-lock" aria-hidden="true"></i> เดือนนี้ปิดแล้ว — ตัวเลขถูกบันทึกถาวร แก้ไขไม่ได้ · ถ้าต้องแก้ ผู้จัดการกด <b>เปิดงวดใหม่</b> (ใส่รหัสผู้จัดการ + เหตุผล)</span>
    <button class="btn btn-sm" onclick="reopenMonth(${bid})"><i class="ti ti-lock-open" aria-hidden="true"></i> เปิดงวดใหม่ (ผู้จัดการ)</button></div>`;

  // ⇄ เงินอยู่ผิดช่อง — การ์ดงาน #1 อยู่บนสุด (2 แตะ): เล่าเป็นเรื่อง + ปุ่มเดียวจบ
  if(!closed)(r.reclass_suggestions||[]).forEach((s,i)=>{ html+=reclassHero(s,i); });

  // เอกสาร — chip ที่ขาดกดอัปโหลดได้เลย + แผงจัดการไฟล์ที่อัปแล้ว (ยกมาจากหน้าเดิม)
  html+=`<div class="card mb-12"><div class="row">
    <b class="fs-135">เอกสาร</b>`;
  r.documents.forEach((d,di)=>{
    const cls=d.status==='uploaded'?'c-ok':d.status==='partial'?'c-warn':'c-err';
    // C8 + Phase 4 #3: uploaded → "· ถึง NN"; partial → "ถึงวันที่ N — ขาด N+1–<horizon>".
    // Cap the missing range to the reconcilable horizon (today for the current month)
    // so an in-progress month doesn't ask staff to upload days that haven't happened.
    const _eom=new Date(+MONTH.slice(0,4), +MONTH.slice(5,7), 0).getDate();
    const _now=new Date();
    const _horizon=(MONTH===ymdLocal(_now).slice(0,7))?Math.min(_eom,_now.getDate()):_eom;
    const _covTo=d.coverage_to?+d.coverage_to.slice(8):0;
    const cov=(d.status==='uploaded'&&d.coverage_to)?' · ถึง '+esc(d.coverage_to.slice(8))
      :(d.status==='partial'&&_covTo&&_covTo<_horizon)?` · ถึงวันที่ ${_covTo} — ขาด ${_covTo+1}–${_horizon}`
      :(d.status==='partial'&&_covTo)?` · ถึงวันที่ ${_covTo}`:'';
    const tip=d.hint?esc(d.hint):'คลิกเพื่ออัปโหลด';                                              // C2
    if(d.status==='uploaded'||closed)
      html+=`<span class="chip ${cls}" title="${tip}">${d.status==='uploaded'?'✓ ':''}${esc(d.label)}${d.detail?' · '+esc(d.detail):''}${cov}</span>`;
    else
      html+=`<button class="chip ${cls}" title="${tip}" onclick="pickDoc(${di})">↑ ${esc(d.label)}${cov||(d.detail?' · '+esc(d.detail):'')}</button>`;
  });
  if(!closed) html+=`
    <input type="file" id="bulk-input" multiple style="display:none" onchange="bulkUpload('bulk-input')">
    <input type="file" id="bulk-folder-input" webkitdirectory multiple style="display:none" onchange="bulkUpload('bulk-folder-input')">
    <button class="btn btn-sm ml-auto" title="เลือกหลายไฟล์พร้อมกัน — ระบบแยกประเภทให้เอง (zip แตกอัตโนมัติ)" onclick="$('bulk-input').click()">หลายไฟล์</button>
    <button class="btn btn-sm" title="เลือกทั้งโฟลเดอร์ของสาขานี้" onclick="$('bulk-folder-input').click()"><i class="ti ti-folder" aria-hidden="true"></i> ทั้งโฟลเดอร์</button>`;
  html+=`<a href="#" class="lbl${closed?' ml-auto':''}" onclick="toggleDocs();return false" id="docs-toggle">จัดการไฟล์ ▾</a>
  </div><div id="up-panel"></div><div id="docs-panel" style="display:none"></div></div>`;

  // คำถามตั้งค่า (unmapped codes) — ตอบจากคำแนะนำเอกสาร หรือตั้งค่าเองตรงนี้เลย
  r.setup_questions.forEach((q,qi)=>{
    html+=`<div class="card c-setup mb-12 bg-info">
      <div class="row">รหัสชำระ <b>${esc(q.code)}</b> (${fmt(q.amount)} บาท) ยังไม่รู้ว่าเข้าธนาคารไหน
      ${q.suggestion?` — เอกสารชี้ว่าเป็น <b>${esc(q.suggestion.category)}·${esc(q.suggestion.bank)}</b> (ตรง ${q.suggestion.hits} วัน)
        ${closed?'':`<button class="btn btn-sm" onclick="applySugg(${qi},this)">ใช้คำตอบนี้</button>`}`:''}
      ${closed?'':`<a href="#" class="lbl ml-auto" onclick="toggleMapper(${qi});return false">ตั้งค่าเอง ▾</a>`}</div>
      <div id="mapper-${qi}" style="display:none;margin-top:8px" class="row">
        <select id="map-cat-${qi}" style="padding:5px;border:1px solid var(--line);border-radius:var(--radius-sm)">
          ${['QR','TRANSFER','CREDIT','CASH','GRAB','LINEMAN','COUPON','ENTERTAIN','OTHER'].map(c=>`<option>${c}</option>`).join('')}
        </select>
        <select id="map-bank-${qi}" style="padding:5px;border:1px solid var(--line);border-radius:var(--radius-sm)">
          ${['KTB','KBANK','KTC','BAY','SCB','BBL','NONE'].map(b=>`<option>${b}</option>`).join('')}
        </select>
        <button class="btn btn-sm btn-pri" onclick="applyMapper(${qi},this)">บันทึก</button>
        <span class="lbl">เงินสด/Grab/Line Man/คูปอง เลือกธนาคาร NONE</span>
      </div></div>`;
  });

  // ยอดโอนเข้าก้อนใหญ่ที่น่าจะเป็น "การโอนกันเอง" (ไม่ใช่ยอดขาย) — ตัดออกคลิกเดียว
  const xfers=r.internal_transfer_suggestions||[];
  if(!closed&&xfers.length){
    const sum=xfers.reduce((a,x)=>a+x.amount,0);
    html+=`<div class="card mb-12 bg-warn bd-warn">
      <div class="row"><b class="fs-135">⇄ พบยอดโอนเข้าที่น่าจะเป็น "การโอนกันเอง" ${xfers.length} รายการ (รวม ${fmt(sum)})</b>
        ${xfers.length>1?`<button class="btn btn-sm ml-auto" onclick="excludeAllXfers()">ตัดออกทั้งหมด</button>`:''}</div>
      <div class="muted" style="margin:2px 0 8px">ยอดก้อนใหญ่ + เลขกลม + ไม่มีคำว่า "ขาย" ในรายละเอียด — ตัดออกแล้วจะไม่ถูกนำมากระทบยอด (ย้ายไปกล่อง "โอนภายใน" · ย้อนกลับได้)</div>
      ${xfers.map(x=>`<div class="todo-item">
        <span class="fx-1">${esc(x.date)} · <b class="mono">${fmt(x.amount)}</b>
          <span class="lbl">${esc(x.description||'')}${x.channel?' · '+esc(x.channel):''}</span>
          <span class="chip ${x.reason==='code'?'c-warn':'c-mut'}" title="${x.reason==='code'?'รหัสโอนระหว่างบัญชี/ระหว่างธนาคาร (IORSDT/NBSDT) — ไม่ใช่ยอดขาย':'ก้อนใหญ่ เลขกลม ไม่มีคำว่าขาย'}">${x.reason==='code'?'รหัสโอนภายใน':'ก้อนใหญ่เลขกลม'}</span></span>
        <button class="btn btn-sm" onclick="excludeXfer(${x.id})">ตัดออก (ไม่ใช่ยอดขาย)</button>
      </div>`).join('')}</div>`;
  }

  // (การ์ด "ยอดอยู่ผิดช่อง" ย้ายขึ้นบนสุดเป็น hero card — ดู reclassHero ด้านล่าง)

  // ── Phase 2: ช่องทาง = กล่องงานเฉพาะที่มีปัญหาจริง + ลิ้นชักเขียว/รอรอบ + เศษเหรียญ ──
  const _upKeys=_uploadChKeys(r);
  const _missStmt=new Set((r.documents||[]).filter(d=>d.source==='bank'&&d.status==='missing').map(d=>d.provider));
  const _bucket={problem:[],coin:[],waiting:[],green:[]};
  r.channels.forEach((ch,i)=>{ _bucket[classifyCh(ch,_upKeys,_missStmt)].push(i); });

  // (A) การ์ดปัญหา — diff / setup / เอกสารหายจริง
  _bucket.problem.forEach(i=>{ html+=chProblemCard(r.channels[i],i,closed); });

  // (A2) ยอดโอนจากผู้ให้บริการ ≠ รายงานสุทธิ — เดิมอยู่แค่ใน #discrepancies ทำให้ "พร้อมปิด" แต่ปิดไม่ได้
  html+=payoutIssuesCard(r,bid,closed);

  // (B) เศษเหรียญ ≤฿100 — รวมเป็นการ์ดเดียว กดรับทราบทั้งหมด
  if(_bucket.coin.length && !closed){
    const sum=_bucket.coin.reduce((a,i)=>a+Math.abs(r.channels[i].variance),0);
    const names=_bucket.coin.map(i=>`<span class="lbl coin-name" role="button" tabindex="0" onclick="toggleDays(${i})" title="ดูรายวันของ ${esc(r.channels[i].label)} ก่อนรับทราบ">${esc(r.channels[i].label)}</span>`).join(', ');
    html+=`<div class="card coin-card mb-12">
      <div class="row"><b class="fs-135"><i class="ti ti-coin" aria-hidden="true"></i> ต่างเล็กน้อยรวม ฿${fmt(sum)} จาก ${_bucket.coin.length} ช่องทาง</b>
        <button class="btn btn-sm btn-pri ml-auto" onclick="acceptCoinAll(${jsa(_bucket.coin)},this)">✓ รับทราบทั้งหมด</button></div>
      <div class="muted" style="margin-top:3px">${names} — ผลต่างไม่เกิน ${COIN_TOL} บาท (ปัดเศษ/ค่าธรรมเนียม) · กดชื่อช่องทางเพื่อดูรายวัน หรือกดปุ่มรับทราบทั้งหมด</div>`;
    _bucket.coin.forEach(i=>{ html+=`<div id="days-${i}" style="display:none"></div>`; });
    html+=`</div>`;
  } else if(_bucket.coin.length){            // closed: read-only problem cards (still drillable)
    _bucket.coin.forEach(i=>{ html+=chProblemCard(r.channels[i],i,closed); });
  }

  // (C) ลิ้นชัก "รอเงินเข้าตามรอบ" — ห้ามแดง
  if(_bucket.waiting.length){
    html+=`<div class="card drawer mb-12 bg-info">
      <a href="#" class="drawer-h" onclick="toggleDrawer('wait');return false" id="dh-wait">
        <i class="ti ti-hourglass-high" aria-hidden="true"></i> รอเงินเข้าตามรอบ — ไม่ต้องทำอะไร (${_bucket.waiting.length}) ▾</a>
      <div id="dw-wait" style="display:none;margin-top:8px">`;
    _bucket.waiting.forEach(i=>{ html+=chDrawerRow(r.channels[i],i,'wait'); });
    html+=`</div></div>`;
  }

  // (D) ลิ้นชักเขียว "เรียบร้อยแล้ว"
  if(_bucket.green.length){
    html+=`<div class="card drawer mb-12 bg-ok">
      <a href="#" class="drawer-h" onclick="toggleDrawer('green');return false" id="dh-green">
        ✓ เรียบร้อยแล้ว — ${_bucket.green.length} ช่องทางตรงหมด ▾</a>
      <div id="dw-green" style="display:none;margin-top:8px">`;
    _bucket.green.forEach(i=>{ html+=chDrawerRow(r.channels[i],i,'green'); });
    html+=`</div></div>`;
  }

  // โอนภายใน
  if(r.non_revenue.count){
    html+=`<div class="card row mb-12 fs-135 t-sub">
      ⇄ เงินเข้าที่ไม่ใช่ยอดขาย: โอนภายใน ${r.non_revenue.count} รายการ ${fmt(r.non_revenue.amount)} บาท (ตัดออกแล้ว)
      <a href="#" class="lbl ml-auto" onclick="toggleNR();return false">ดูรายการ</a></div>
    <div id="nr" class="card" style="display:none;margin-bottom:12px;padding:0"><table>
      ${r.non_revenue.lines.map(l=>`<tr><td>${esc(l.date)}</td><td class="tl">${esc(l.ref)}</td><td class="mono">${fmt(l.amount)}</td></tr>`).join('')}
    </table></div>`;
  }

  // จอสรุปผู้จัดการ — เห็นทุกอย่างที่ staff ทำ/รับทราบ ก่อนใส่รหัสปิดเดือน
  if(st==='in_review'&&!closed){
    html+=`<div class="card mb-12 bd-brand">
      <b class="fs-135"><i class="ti ti-clipboard-list" aria-hidden="true"></i> สรุปสำหรับผู้จัดการ — ตรวจก่อนปิดเดือน</b>
      <table class="mt-8">
        <tr><th>ช่องทาง</th><th>POS</th><th>เงินเข้าจริง</th><th>ต่าง</th><th class="tl">สถานะ / เหตุผลที่ staff รับทราบ</th></tr>
        ${r.channels.map(c=>{
          const [cls,lab]=c.accepted?['c-mut','รับทราบ']:(CH_ST[c.status]||['c-mut',c.status]);
          return `<tr><td>${esc(c.label)}</td><td class="mono">${fmt(c.pos)}</td>
            <td class="mono">${c.ext?fmt(c.ext):'—'}</td>
            <td class="mono" style="color:${Math.abs(c.variance)<=100?'inherit':'var(--err)'}">${c.variance>0?'+':''}${fmt(c.variance)}</td>
            <td class="tl"><span class="chip ${cls}">${lab}</span>
              ${c.accepted?` <span class="lbl">"${esc(c.accepted.note)}" — ${esc(c.accepted.by||'staff')}</span>`
                :c.detail?` <span class="lbl">${esc(c.detail)}</span>`:''}</td></tr>`;}).join('')}
        ${(r.payout_issues||[]).map(p=>`<tr><td>ยอดโอน ${esc(p.label)}</td><td class="mono">${fmt(p.report_net)}</td>
          <td class="mono">${fmt(p.bank_payout)}</td>
          <td class="mono t-err">${p.variance>0?'+':''}${fmt(p.variance)}</td>
          <td class="tl">${p.accepted?`<span class="chip c-mut">รับทราบ</span> <span class="lbl">"${esc(p.accepted.note)}" — ${esc(p.accepted.by||'staff')}</span>`
            :'<span class="chip c-err">ยังไม่ตรง</span>'}</td></tr>`).join('')}
        ${r.non_revenue.count?`<tr><td class="t-sub">โอนภายใน (ตัดออก)</td><td></td>
          <td class="mono t-sub">${fmt(r.non_revenue.amount)}</td><td></td>
          <td class="lbl tl">${r.non_revenue.count} รายการ — ไม่นับเป็นยอดขาย</td></tr>`:''}
      </table>
      <div class="row mt-8 gap-8 fx-wrap">
        <a class="btn btn-sm" href="/api/reports/override-register.print?period_month=${MONTH}&branch_id=${bid}" target="_blank"><i class="ti ti-writing" aria-hidden="true"></i> ทะเบียนการปรับปรุงด้วยมือ</a>
        <a class="btn btn-sm" href="/api/reports/recon-certificate.print?period_month=${MONTH}&branch_id=${bid}" target="_blank"><i class="ti ti-certificate" aria-hidden="true"></i> ดูใบรับรอง (ก่อนเซ็น)</a>
      </div>
      <div class="lbl mt-6">ปิดเดือนแล้วเหตุผลทั้งหมดจะติดไปในไฟล์ Excel · แก้ไขไม่ได้จนกว่าผู้จัดการจะกด "เปิดงวดใหม่"</div>
    </div>`;
  }

  // ⇄ บันทึกผิดช่อง (ย้ายช่อง) — กระทบสองยอดพร้อมกัน
  html+=channelMovePanel(bid,closed);

  // สิ่งที่เหลือ (พร้อมปุ่มรับทราบ) + ปุ่มสถานะ
  html+=`<div class="card">`;
  if(r.todo.length){
    html+=`<b class="fs-135">เหลือ ${r.todo.length} เรื่องก่อนปิดเดือน</b><div style="margin:6px 0 10px">`;
    r.todo.forEach((t,i)=>{
      const acceptable=!closed && (t.channel||t.type==='diff');
      const drillable=t.channel && r.channels.some(c=>c.key===t.channel);
      html+=`<div class="todo-item"><span class="fx-1">• ${esc(t.label)}</span>
        ${drillable?`<button class="btn btn-sm" title="กางช่องทางนี้ดูรายวัน + บรรทัด statement ที่ยังจับคู่ไม่ได้" onclick="drillTodo(${jsa(t.channel)})"><i class="ti ti-zoom-in" aria-hidden="true"></i> ดูรายการ</button>`:''}
        ${t.type==='upload'&&!closed?`<button class="btn btn-sm" onclick="pickDoc(${DATA.documents.findIndex(d=>d.key===t.doc)})">อัปโหลด</button>`:''}
        ${acceptable?`<button class="btn btn-sm" onclick="acceptTodo(${i})">✓ รับทราบ + เหตุผล</button>`:''}
      </div>`;
    });
    html+=`</div><div class="muted">"รับทราบ" = ยอมรับผลต่างโดยใส่เหตุผลจริง (เช่น ผลต่างจากค่าธรรมเนียมที่กระทบยอดแล้ว) — เหตุผลจะติดไปในไฟล์ Excel ให้ผู้จัดการเห็นตอนอนุมัติ</div>`;
  } else {
    html+=`<span class="chip c-ok">✓ ครบทุกช่องทาง พร้อมปิดเดือน</span>`;
  }
  html+=`<div class="row mt-10 jc-end">
    ${closed?'':st==='in_review'
      ?`<button class="btn" onclick="setSt(${bid},'draft',this)">ส่งกลับแก้</button>
         <button class="btn btn-pri" onclick="closeMonth(${bid},this)" ${r.todo.length?'disabled title="ยังมีเรื่องค้าง — จัดการหรือรับทราบให้ครบก่อน"':''}>ปิดเดือน (ผู้จัดการ)</button>`
      :`<button class="btn btn-pri" onclick="setSt(${bid},'in_review',this)">ส่งตรวจ</button>`}
  </div></div>`;
  $('app').innerHTML=html;

  // มาจากการคลิกช่องทางในหน้าภาพรวม → กางช่องทางนั้นและเลื่อนไปหา (กางลิ้นชักให้ด้วย)
  if(PENDING_CH){ const k=PENDING_CH; PENDING_CH=null; openEvidence(k); }
}

/* ── Phase 2 render helpers ── */
function chProblemCard(ch,i,closed){
  const acc=ch.accepted;
  const [cls,lab]=acc?['c-mut','รับทราบแล้ว']:(CH_ST[ch.status]||['c-mut',ch.status]);
  const dirTxt=(ch.key!=='COUPON'&&ch.status==='diff'&&Math.abs(ch.variance)>0)
    ? (ch.variance<0?`เงินเข้าจริงน้อยกว่า POS ฿${fmt(-ch.variance)}`
                    :`เงินเข้าจริงเกิน POS ฿${fmt(ch.variance)}`)
    : esc(ch.detail||lab);   // COUPON (variance=แต้ม) ใช้ detail เดิม ไม่แปลงเป็นบาท
  const side=ch.status==='diff'?'var(--err)':ch.status==='setup'?'var(--info)':'var(--warn)';
  const bg  =ch.status==='diff'?'var(--err-bg)':ch.status==='setup'?'var(--info-bg)':'var(--warn-bg)';
  // suppress the POS·ext subline for COUPON (its variance is points, pos/ext are gross — misleading)
  const sub=ch.key==='COUPON'?'' :
    `<div class="lbl mono">POS ${fmt(ch.pos)} · เงินเข้าจริง ${ch.ext?fmt(ch.ext):'—'}${ch.days_total?` · ${ch.days_done}/${ch.days_total} วัน ✓`:''}</div>`;
  return `<div class="card ch-prob" style="margin-bottom:12px;background:${bg};border:1px solid ${side}">
    <div class="row pointer" role="button" tabindex="0" onclick="toggleDays(${i})">
      <div class="fx-1"><b class="fs-14">${esc(ch.label)}</b>
        <div class="muted">${dirTxt}</div>${sub}</div>
      <span class="chip ${cls}">${lab}</span>
      <button class="btn btn-sm" onclick="event.stopPropagation();toggleDays(${i})"><i class="ti ti-zoom-in" aria-hidden="true"></i> ดูรายการ ▾</button>
    </div>
    ${acc?`<div class="lbl mt-4" title="${esc(acc.note)}">"${esc(acc.note)}" ${closed?'':`<a href="#" onclick="event.stopPropagation();unaccept(${acc.id});return false">เลิกรับทราบ</a>`}</div>`:''}
    <div id="days-${i}" style="display:none;margin-top:8px;border-top:1px solid var(--line)"></div>
  </div>`;
}
function chDrawerRow(ch,i,which){
  const acc=ch.accepted;
  const [cls,lab]=acc?['c-mut','รับทราบแล้ว']
    :which==='wait'?['c-warn',esc(ch.detail||'รอเงินเข้าตามรอบ')]
    :(CH_ST[ch.status]||['c-mut',ch.status]);
  // COUPON: pos/ext are gross discount amounts, not bank money — don't show as เงินเข้า
  const isCoupon=ch.key==='COUPON';
  const posCell=isCoupon?'<span class="lbl">แต้ม</span>':fmt(ch.pos);
  const extCell=isCoupon?'<span class="t-mut">—</span>':(ch.ext?fmt(ch.ext):'<span class="t-mut">—</span>');
  return `<div class="grid-ch bg-none" role="button" tabindex="0" onclick="toggleDays(${i})">
    <div><span class="ch-name fs-14">${esc(ch.label)}</span>${ch.days_total?`<div class="lbl">${ch.days_done}/${ch.days_total} วัน ✓</div>`:''}</div>
    <div class="num t-sub">${posCell}</div>
    <div class="num">${extCell}</div>
    <div class="tr"><span class="chip ${cls}">${lab}</span>
      ${acc&&!DATA.closed?`<div class="lbl"><a href="#" onclick="event.stopPropagation();unaccept(${acc.id});return false">เลิกรับทราบ</a></div>`:''}</div>
    <span class="lbl">▾</span></div>
  <div id="days-${i}" style="display:none;border-bottom:1px solid var(--line)"></div>`;
}
function toggleDrawer(name){
  const body=$('dw-'+name), h=$('dh-'+name); if(!body)return;
  const open=body.style.display!=='none';
  body.style.display=open?'none':'';
  h.textContent=h.textContent.replace(open?'▴':'▾',open?'▾':'▴');
}
/* เศษเหรียญ ≤฿100: รับทราบทุกช่องทางในคราวเดียว (auto-reason ≥10 ตัวอักษร, AUD-8) */
async function acceptCoinAll(idxs,btn){
  if(!idxs||!idxs.length)return;
  const note=`ผลต่างเล็กน้อยไม่เกิน ${COIN_TOL} บาท (ปัดเศษ/ค่าธรรมเนียม)`;
  if(!confirm(`รับทราบผลต่างเล็กน้อย ${idxs.length} ช่องทาง พร้อมเหตุผล "${note}"?`))return;
  await busy('coin',btn,async()=>{
    let okN=0, lastErr='';
    for(const i of idxs){
      const ch=DATA.channels[i]; if(!ch)continue;
      try{await api('/api/lanes/adj',{method:'POST',body:{branch_id:DATA.branch_id,period_month:MONTH,
          category:ch.key,bank:'ALL',adj_pos:0,adj_ext:0,note:note,adj_type:'accept'}}); okN++; }
      catch(e){ lastErr=e.message; hint403(e); }
    }
    toast(okN===idxs.length?`รับทราบแล้ว ${okN} ช่องทาง`:`รับทราบ ${okN}/${idxs.length} — บางช่องไม่สำเร็จ${lastErr?': '+lastErr:''}`);
    route();
  });
}

function toggleNR(){const e=$('nr');e.style.display=e.style.display==='none'?'':'none';}

/* ── อัปโหลดเอกสารในหน้า ── */
// จำนวนบรรทัดซ้ำที่ระบบข้าม (ชื่อคีย์ยืดหยุ่น — รองรับหลายเวอร์ชันของ backend)
const _DUP_KEYS=['duplicates_skipped','skipped_duplicate_lines','duplicate_lines_skipped','skipped_duplicates','duplicate_lines','dup_skipped'];
function dupCount(o){if(!o)return 0;for(const k of _DUP_KEYS){const n=+o[k];if(n>0)return n;}return 0;}
function pickDoc(i){ pickFile(DATA&&DATA.documents?DATA.documents[i]:null); }
function pickFile(doc){
  if(!doc)return;
  UP_DOC=doc;
  $('file-input').value='';
  $('file-input').onchange=()=>previewUpload();
  $('file-input').click();
}
async function previewUpload(){
  const f=$('file-input').files[0];
  if(!f||!UP_DOC)return;
  const panel=$('up-panel');
  panel.innerHTML=`<div class="muted mt-10">กำลังอ่าน ${esc(f.name)}…</div>`;
  const fd=new FormData();
  fd.append('file',f);fd.append('branch_id',DATA.branch_id);
  fd.append('period_month',MONTH);fd.append('provider',UP_DOC.provider||'');
  let p;
  try{p=await api(`/api/uploads/${UP_DOC.source}/preview`,{method:'POST',body:fd});}
  catch(e){panel.innerHTML=`<div class="chip c-err mt-10">อ่านไฟล์ไม่ได้: ${esc(e.message)}</div>`;return;}
  const wrongBranch=p.detected_branch_id&&p.detected_branch_id!==DATA.branch_id;
  const zeroRow=p.row_count===0&&!p.empty_statement;            // A1/C7: nothing usable parsed
  const block=wrongBranch||zeroRow;                              // can't confirm these
  panel.innerHTML=`<div class="card mt-10 bg-subtle">
    <div class="row"><b class="fs-135">${esc(UP_DOC.label)}</b>
      <span class="lbl">${esc(f.name)} · ตรวจพบ: ${esc(p.provider||'ไม่ระบุ')} · ${p.row_count} รายการ${p.skipped_rows?` (ข้าม ${p.skipped_rows})`:''}${dupCount(p)?` · ซ้ำกับที่มีอยู่ ${dupCount(p)} รายการ (จะข้าม)`:''}</span></div>
    ${p.empty_statement?`<div class="chip c-warn mt-6">เอกสารว่าง (No Data) — ยืนยันได้ ระบบจะบันทึกว่าเดือนนี้ไม่มีรายการ</div>`:''}
    ${zeroRow?`<div class="chip c-err mt-6"><i class="ti ti-alert-triangle" aria-hidden="true"></i> อ่านได้ 0 รายการ — น่าจะเป็นไฟล์ผิดประเภท/รูปแบบที่ยังไม่รองรับ ยืนยันไม่ได้</div>`:''}
    ${(p.generic_fallback&&!zeroRow)?`<div class="chip c-warn mt-6">อ่านแบบทั่วไป (ไม่รู้จักรูปแบบเฉพาะของผู้ให้บริการ) — โปรดตรวจตัวเลขก่อนยืนยัน</div>`:''}
    ${p.ocr_used?`<div class="chip c-warn mt-6"><i class="ti ti-zoom-in" aria-hidden="true"></i> อ่านด้วย OCR (ไฟล์ PDF ไม่มีตัวอักษร) — ยอดผ่านการตรวจสอบยอดคงเหลือแล้ว แต่โปรดตรวจยอดรวมอีกครั้งก่อนยืนยัน</div>`:''}
    ${wrongBranch?`<div class="chip c-err mt-6"><i class="ti ti-alert-triangle" aria-hidden="true"></i> เอกสารนี้เป็นของสาขา ${esc(p.detected_branch_name)} — ยืนยันไม่ได้</div>`:''}
    <div class="row mt-10">
      <button class="btn btn-pri" ${block?'disabled':''} onclick="confirmUpload(this)">✓ ยืนยันบันทึก</button>
      <button class="btn" onclick="$('up-panel').innerHTML=''">ยกเลิก</button>
    </div></div>`;
}
async function confirmUpload(btn){
  const f=$('file-input').files[0];
  if(!f||!UP_DOC)return;
  const fd=new FormData();
  fd.append('file',f);fd.append('branch_id',DATA.branch_id);
  fd.append('period_month',MONTH);fd.append('provider',UP_DOC.provider||'');
  await busy('upload',btn,async()=>{
    try{const r=await api(`/api/uploads/${UP_DOC.source}/confirm`,{method:'POST',body:fd});
        const dup=dupCount(r);
        toast(`บันทึกแล้ว ${r.rows_saved} รายการ`+(dup?` · ข้ามรายการซ้ำ ${dup}`:'')+(r.skipped_rows?` · ข้ามแถวที่อ่านไม่ได้ ${r.skipped_rows}`:''));route();}
    catch(e){toast('ไม่สำเร็จ: '+e.message);hint403(e);}
  });
}

/* ── อัปโหลดหลายไฟล์/ทั้งโฟลเดอร์ในครั้งเดียว (B1) ── */
async function bulkUpload(inputId){
  const inp=$(inputId); const files=[...(inp.files||[])]; if(!files.length)return;
  const panel=$('up-panel');
  panel.innerHTML=`<div class="muted mt-10">กำลังอัปโหลด ${files.length} ไฟล์… (อาจใช้เวลาสักครู่)</div>`;
  const fd=new FormData();
  for(const f of files) fd.append('files',f);
  fd.append('branch_id',DATA.branch_id); fd.append('period_month',MONTH); fd.append('source_type','auto');
  let r;
  try{ r=await api('/api/uploads/bulk',{method:'POST',body:fd}); }
  catch(e){ panel.innerHTML=`<div class="chip c-err mt-10">อัปโหลดไม่สำเร็จ: ${esc(e.message)}</div>`; inp.value=''; hint403(e); return; }
  const s=r.summary||{};
  const dupAll=dupCount(s)||(r.files||[]).reduce((a,f)=>a+dupCount(f),0);
  const stCls=x=>x==='ok'?'c-ok':x==='skipped'?'c-warn':'c-err';
  const stTh=x=>x==='ok'?'สำเร็จ':x==='skipped'?'ข้าม':'ผิดพลาด';
  panel.innerHTML=`<div class="card mt-10 bg-subtle">
    <div class="row"><b class="fs-135">ผลอัปโหลด ${s.total} ไฟล์</b>
      <span class="lbl">✓ สำเร็จ ${s.saved} · ข้าม ${s.skipped} · ผิดพลาด ${s.errored} · รวม ${fmt(s.rows)} รายการ${dupAll?` · ข้ามบรรทัดซ้ำ ${fmt(dupAll)}`:''}</span>
      <button class="btn btn-sm btn-pri ml-auto" onclick="route()">รีเฟรชหน้า</button></div>
    <div style="max-height:340px;overflow:auto;margin-top:6px"><table class="w-full">
      <tr><th class="tl">ไฟล์</th><th>ประเภท</th><th>สถานะ</th><th>รายการ</th><th class="tl">หมายเหตุ</th></tr>
      ${(r.files||[]).map(f=>`<tr>
        <td class="tl">${esc(f.file)}</td>
        <td>${esc(f.source_type||'-')}</td>
        <td><span class="chip ${stCls(f.status)}">${stTh(f.status)}</span></td>
        <td class="mono">${f.rows_saved||''}</td>
        <td class="lbl tl">${esc(detailText(f.reason))}${dupCount(f)?`${f.reason?' · ':''}ข้ามบรรทัดซ้ำ ${dupCount(f)}`:''}</td></tr>`).join('')}
    </table></div></div>`;
  inp.value='';
}

/* ── แผงจัดการไฟล์ที่อัปโหลดแล้ว (ยกความสามารถหน้า Uploads เดิมเข้ามา) ── */
const SRC_LABEL={bank:'สเตทเมนต์',credit_card:'สรุปยอดบัตร',grab:'Grab',lineman:'Line Man'};
const LINE_ST={matched:['c-ok','✓ จับคู่แล้ว'],manual:['c-ok','<i class="ti ti-link" aria-hidden="true"></i> จับคู่เอง'],unmatched:['c-warn','<i class="ti ti-hourglass-low" aria-hidden="true"></i> ยังไม่จับคู่'],
               excluded:['c-mut','⊘ ตัดออก'],informational:['c-mut','· ข้อมูลทั่วไป']};
async function toggleDocs(){
  const panel=$('docs-panel'), tg=$('docs-toggle');
  if(panel.style.display!=='none'){panel.style.display='none';tg.textContent='จัดการไฟล์ ▾';return;}
  tg.textContent='จัดการไฟล์ ▴';
  panel.style.display='';
  panel.innerHTML='<div class="muted mt-10">กำลังโหลด…</div>';
  let ups;try{ups=await api(`/api/uploads?branch_id=${DATA.branch_id}&period_month=${MONTH}`);}
  catch(e){panel.innerHTML=`<div class="chip c-err">โหลดไม่ได้: ${esc(e.message)}</div>`;return;}
  _UPS=ups;
  if(!ups.length){panel.innerHTML='<div class="muted mt-10">ยังไม่มีไฟล์ของสาขา/เดือนนี้</div>';return;}
  panel.innerHTML=`<div class="card p-0 mt-10 ovx-auto"><table>
    <tr><th>ไฟล์</th><th>ประเภท</th><th>รายการ</th><th style="min-width:130px">จับคู่แล้ว</th><th>อัปเมื่อ</th><th></th></tr>
    ${ups.map(u=>{
      const tot=u.match_total||0, done=u.match_done||0;
      const pct=tot?Math.round(100*done/tot):0;
      const bar=tot?`<div class="row"><div class="prog" style="min-width:60px"><div style="width:${pct}%"></div></div>
                     <span class="lbl">${done}/${tot}</span></div>`
        :(u.error_msg?`<span class="chip c-mut">${esc(u.error_msg)}</span>`:'<span class="lbl">—</span>');
      return `<tr>
        <td style="text-align:left;max-width:230px;overflow:hidden;text-overflow:ellipsis" title="${esc(u.file_name)}">${esc(u.file_name)}</td>
        <td>${esc(SRC_LABEL[u.source_type]||u.source_type)} ${esc(u.provider||'')}</td>
        <td class="mono">${u.row_count}</td>
        <td>${bar}</td>
        <td class="lbl">${esc((u.uploaded_at||'').slice(5,16))}</td>
        <td class="nowrap">
          <button class="btn btn-sm" onclick="upLines(${u.upload_id},this)">รายบรรทัด</button>
          ${DATA.closed?'':`<button class="btn btn-sm" onclick="moveUpload(${u.upload_id})" title="เอกสารอยู่ผิดสาขา">ย้ายสาขา</button>
          <button class="btn btn-sm t-err" onclick="delUpload(${u.upload_id},this)">ลบ</button>`}
        </td></tr>
        <tr id="uplines-${u.upload_id}" style="display:none"><td colspan="6" class="tl bg-subtle"></td></tr>`;
    }).join('')}
  </table></div>
  <div class="muted mt-4">% จับคู่คำนวณสดจากเอกสารจริง — ลบไฟล์แล้วอัปใหม่ได้ถ้าอัปผิด (ระบบกันไฟล์ซ้ำให้)</div>`;
}
async function upLines(uid,btn){
  const row=$('uplines-'+uid);
  if(row.style.display!=='none'){row.style.display='none';return;}
  row.style.display='';
  const cell=row.firstElementChild;
  cell.innerHTML='<span class="muted">กำลังโหลด…</span>';
  let d;try{d=await api(`/api/uploads/${uid}/lines`);}
  catch(e){cell.innerHTML=`<span class="chip c-err">${esc(e.message)}</span>`;return;}
  cell.innerHTML=`<div style="max-height:280px;overflow:auto"><table>
    <tr><th>วันที่</th><th>รายละเอียด</th><th>ยอด</th><th>สถานะ</th></tr>
    ${d.lines.map(l=>{const [cls,lab]=LINE_ST[l.status]||['c-mut',l.status];
      return `<tr><td>${esc(l.date)}</td><td class="tl">${esc(l.ref||'')}</td>
        <td class="mono">${fmt(l.amount)}</td><td><span class="chip ${cls}">${lab}</span></td></tr>`;}).join('')}
  </table></div>
  <div class="lbl mt-4">จับคู่ ${d.matched}/${d.count} รายการที่ต้องกระทบยอด (อีก ${d.total_lines-d.count} รายการเป็นข้อมูลทั่วไป/ตัดออก)</div>`;
}
async function moveUpload(uid){
  let brs;try{brs=await api('/api/branches');}catch(e){toast(e.message);return;}
  const opts=brs.map(b=>`${b.branch_id}=${b.name}`).join('  ');
  const pick=prompt(`ย้ายไฟล์ไปสาขาไหน? พิมพ์หมายเลข:\n${opts}`);
  if(pick===null)return;
  const bid=parseInt(pick.trim());
  if(!brs.some(b=>b.branch_id===bid)){toast('หมายเลขสาขาไม่ถูกต้อง');return;}
  try{const r=await api(`/api/uploads/${uid}/branch`,{method:'PATCH',body:{branch_id:bid}});
      toast(`ย้ายแล้ว (${r.moved_lines} รายการ)`);route();}catch(e){toast('ไม่สำเร็จ: '+e.message);}
}
let _UPS=[];
async function delUpload(uid,btn){
  const u=_UPS.find(x=>x.upload_id===uid);
  if(!confirm(`ลบไฟล์ "${u?u.file_name:'#'+uid}" และรายการทั้งหมดของมัน? (อัปใหม่ได้ภายหลัง)`))return;
  await busy('del-'+uid,btn,async()=>{
    try{await api(`/api/uploads/${uid}`,{method:'DELETE'});toast('ลบแล้ว');route();}
    catch(e){toast('ไม่สำเร็จ: '+e.message);hint403(e);}
  });
}

/* ── รับทราบผลต่าง — เลือกเหตุผลสำเร็จรูป (แทน prompt) ── */
const ACCEPT_REASONS=[
  'ผลต่างจากค่าธรรมเนียม/ปัดเศษ ที่กระทบยอดแล้ว — ทบทวนซ้ำเดือนหน้า',
  'เศษสตางค์/ปัดเศษจากระบบ',
  'ค่าธรรมเนียมธนาคาร/ค่ายบัตรหักก่อนโอน',
  'เงินเข้าข้ามรอบ — วันสุดท้ายฝากเดือนถัดไป',
  'ลูกค้าโอนผิดบัญชี/คืนเงินภายหลัง',
];
let _acceptCtx=null;
function payoutIssuesCard(r,bid,closed){
  const ps=r.payout_issues||[]; if(!ps.length)return '';
  const open=ps.filter(p=>!p.accepted).length;
  return `<div class="card" style="margin-bottom:12px;${open?'background:var(--err-bg);border:1px solid var(--err)':''}">
    <div class="row"><b class="fs-14"><i class="ti ti-building-bank" aria-hidden="true"></i> ยอดโอนจากผู้ให้บริการไม่ตรงรายงาน${open?` · ค้าง ${open}`:''}</b>
      <a class="btn btn-sm ml-auto" href="#payout?bid=${bid}"><i class="ti ti-zoom-in" aria-hidden="true"></i> ดูรายการโอนรายวัน</a></div>
    <div class="muted my-2-6">เทียบยอดสุทธิในรายงาน Grab / Line Man / บัตร กับเงินที่โอนเข้าธนาคารจริง — ต้องตรวจหรือรับทราบพร้อมเหตุผลก่อนปิดเดือน</div>
    ${ps.map(p=>`<div class="todo-item"><span class="fx-1"><b>${esc(p.label)}</b>
        <span class="lbl mono">รายงานสุทธิ ${fmt(p.report_net)} · เงินเข้า ${fmt(p.bank_payout)} · ต่าง ${p.variance>0?'+':''}${fmt(p.variance)}</span>
        ${p.accepted?`<br><span class="lbl">รับทราบแล้ว: "${esc(p.accepted.note)}" — ${esc(p.accepted.by||'staff')}</span>`:''}</span>
      ${closed?'':p.accepted
        ?`<button class="btn btn-sm" onclick="unacceptPayout(${+p.accepted.id},this)">เลิกรับทราบ</button>`
        :`<button class="btn btn-sm" onclick="acceptPayout(${jsa(p.category)})">✓ รับทราบ + เหตุผล</button>`}
    </div>`).join('')}</div>`;
}
function acceptPayout(cat){
  const p=((DATA&&DATA.payout_issues)||[]).find(x=>x.category===cat); if(!p)return;
  _acceptCtx={channel:cat,label:'ยอดโอน '+p.label+' ต่าง '+fmt(p.variance)+' บาท',payout:true};
  _acceptSheet(_acceptCtx.label);
}
async function unacceptPayout(id,btn){
  await busy('unaccept-payout',btn,async()=>{
    try{await api(`/api/payout-ack/${id}`,{method:'DELETE'});toast('เลิกรับทราบแล้ว');route();}
    catch(e){toast('ไม่สำเร็จ: '+e.message);}
  });
}
function acceptTodo(i){
  const t=((DATA&&DATA.todo)||[])[i];
  const channel=t&&t.channel, label=(t&&t.label)||'';
  if(!channel){toast('ข้อนี้รับทราบไม่ได้ — ต้องอัปโหลดเอกสาร');return;}
  if(t.type==='payout'){acceptPayout(channel);return;}
  _acceptCtx={channel,label};
  _acceptSheet(label);
}
function _acceptSheet(label){
  const opts=ACCEPT_REASONS.map((rsn,k)=>
    `<button class="btn rc-reason" onclick="pickAcceptReason(${k})">${esc(rsn)}</button>`).join('');
  showSheet(`<div class="sheet-h">รับทราบผลต่าง: ${esc(label)}</div>
    <div class="lbl mb-10">เลือกเหตุผล (จะแสดงในไฟล์ Excel ให้ผู้จัดการเห็นตอนอนุมัติ) หรือพิมพ์เอง</div>
    <div class="rc-reasons">${opts}</div>
    <textarea id="accept-note" class="sheet-ta" placeholder="พิมพ์เหตุผลเอง (อย่างน้อย 10 ตัวอักษร)"></textarea>
    <div class="row mt-10 gap-8">
      <button class="btn btn-pri" onclick="submitAccept(this)">บันทึกรับทราบ</button>
      <button class="btn" onclick="closeSheet()">ยกเลิก</button>
    </div>`);
}
function pickAcceptReason(k){const ta=$('accept-note');if(ta){ta.value=ACCEPT_REASONS[k];ta.focus();}}
async function submitAccept(btn){
  const note=(($('accept-note')||{}).value||'').trim();
  if(note.length<10){toast('ต้องใส่เหตุผลอย่างน้อย 10 ตัวอักษร');return;}
  const ctx=_acceptCtx;if(!ctx)return;
  await busy('accept',btn,async()=>{
    try{if(ctx.payout){await ensureSession(DATA.branch_id);
          await api('/api/payout-ack',{method:'POST',body:{branch_id:DATA.branch_id,period_month:MONTH,category:ctx.channel,note:note}});}
        else await api('/api/lanes/adj',{method:'POST',body:{branch_id:DATA.branch_id,period_month:MONTH,
        category:ctx.channel,bank:'ALL',adj_pos:0,adj_ext:0,note:note,adj_type:'accept'}});
        closeSheet();toast('รับทราบแล้ว');route();}catch(e){toast('ไม่สำเร็จ: '+e.message);hint403(e);}
  });
}
async function unaccept(adjId){
  try{await api(`/api/lanes/adj/${adjId}`,{method:'DELETE'});toast('เลิกรับทราบแล้ว');route();}
  catch(e){toast('ไม่สำเร็จ: '+e.message);}
}

/* ── ⇄ บันทึกผิดช่อง (ย้ายช่อง) — ย้ายยอดที่บันทึกผิดไปช่องที่ถูก กระทบสองยอดพร้อมกัน ── */
const CH_LABELS={CASH:'เงินสด',QR:'QR พร้อมเพย์',TRANSFER:'เงินโอน',CREDIT:'บัตรเครดิต',
                 GRAB:'Grab',LINEMAN:'Line Man',COUPON:'คูปอง'};
function chLaneLabel(cat,bank){
  const base=CH_LABELS[cat]||cat;
  return (bank&&bank!=='NONE'&&bank!=='ALL')?`${base}·${bank}`:base;
}
function _moveLaneOpts(){
  const opts=[];
  (DATA.channels||[]).forEach(c=>{
    const banks=(c.banks&&c.banks.length)?c.banks:[{bank:'NONE'}];
    banks.forEach(b=>opts.push({cat:c.key,bank:b.bank||'NONE',label:chLaneLabel(c.key,b.bank)}));
  });
  return opts;
}
function channelMovePanel(bid,closed){
  const moves=(DATA.moves||[]);
  let body='';
  if(moves.length) body=`<table class="mv-tbl"><tbody>`+moves.map(m=>`<tr>
      <td>${esc(chLaneLabel(m.from_category,m.from_bank))} <span class="lbl">→</span> ${esc(chLaneLabel(m.to_category,m.to_bank))}</td>
      <td class="mono">฿${fmt2(m.amount)}</td>
      <td class="lbl">${esc(m.note||'')}</td>
      <td class="tr">${closed?'':`<button class="btn btn-sm" onclick="undoMove(${m.id})">↩ ยกเลิก</button>`}</td>
    </tr>`).join('')+`</tbody></table>`;
  return `<div class="card mb-12">
    <div class="row"><b class="fs-135">⇄ บันทึกผิดช่อง (ย้ายช่อง)</b>
      ${closed?'':`<button class="btn btn-sm btn-pri ml-auto" onclick="showChannelMove(${bid})">+ บันทึกผิดช่อง</button>`}</div>
    <div class="muted" style="margin:4px 0 ${moves.length?'8px':'0'}">ยอดถูกบันทึกผิดช่อง (เช่น QR แต่กดเป็นเงินสด)? ย้ายไปช่องที่ถูก — โปรแกรมจะลดช่องผิด+เพิ่มช่องถูกพร้อมกัน · ย้อนกลับได้</div>
    ${body}</div>`;
}
function showChannelMove(bid,presetFrom,presetTo){
  const opts=_moveLaneOpts();
  if(opts.length<2){toast('ต้องมีช่องทางอย่างน้อย 2 ช่องในเดือนนี้ก่อน');return;}
  window._mvOpts=opts; window._mvBid=bid;
  const pf=presetFrom?opts.findIndex(o=>o.cat===presetFrom):-1;
  const pt=presetTo?opts.findIndex(o=>o.cat===presetTo):-1;
  const optHtml=sel=>opts.map((o,i)=>`<option value="${i}" ${i===sel?'selected':''}>${esc(o.label)}</option>`).join('');
  showSheet(`<div class="sheet-h">⇄ บันทึกผิดช่อง — ย้ายยอดไปช่องที่ถูก</div>
    <div class="lbl mb-8">โปรแกรมจะกระทบทั้งสองช่องพร้อมกัน (ลดช่องผิด เพิ่มช่องถูก) · ย้อนกลับได้</div>
    <div class="row gap-10 ai-end fx-wrap">
      <div><div class="lbl">จากช่อง (บันทึกผิด)</div><select id="mv-from" class="mv-sel">${optHtml(pf)}</select></div>
      <div class="as-end fs-20 pb-4">→</div>
      <div><div class="lbl">ไปช่อง (ที่ถูก)</div><select id="mv-to" class="mv-sel">${optHtml(pt)}</select></div>
      <div><div class="lbl">จำนวนเงิน (฿)</div><input id="mv-amt" type="number" min="0" step="0.01" class="mv-sel" placeholder="0.00"></div>
    </div>
    <textarea id="mv-note" class="sheet-ta" placeholder="เหตุผล/หมายเหตุ (เช่น ลูกค้าสแกน QR แต่พนักงานกดเป็นเงินสด)"></textarea>
    <div class="row mt-10 gap-8">
      <button class="btn btn-pri" onclick="submitMove(this)">บันทึกการย้าย</button>
      <button class="btn" onclick="closeSheet()">ยกเลิก</button>
    </div>`);
}
async function submitMove(btn){
  const opts=window._mvOpts||[], bid=window._mvBid;
  const f=opts[+(($('mv-from')||{}).value)], t=opts[+(($('mv-to')||{}).value)];
  const amt=parseFloat((($('mv-amt')||{}).value)||'0');
  const note=(($('mv-note')||{}).value||'').trim();
  if(!f||!t){toast('เลือกช่องไม่ถูกต้อง');return;}
  if(f.cat===t.cat&&f.bank===t.bank){toast('ช่องต้นทางและปลายทางต้องไม่ใช่ช่องเดียวกัน');return;}
  if(!(amt>0)){toast('ใส่จำนวนเงินมากกว่า 0');return;}
  if(note.length<10){toast('กรุณาระบุเหตุผลการย้ายช่อง (อย่างน้อย 10 ตัวอักษร)');const ta=$('mv-note');if(ta)ta.focus();return;}
  await busy('move',btn,async()=>{
    try{await api('/api/channel-move',{method:'POST',body:{branch_id:bid,period_month:MONTH,
        from_category:f.cat,from_bank:f.bank,to_category:t.cat,to_bank:t.bank,amount:amt,note:note}});
      closeSheet();toast('ย้ายช่องแล้ว — กระทบสองยอดเรียบร้อย');route();}
    catch(e){toast('ไม่สำเร็จ: '+e.message);hint403(e);}
  });
}
async function undoMove(id){
  if(!confirm('ยกเลิกการย้ายช่องนี้? (ยอดจะกลับไปช่องเดิม)'))return;
  try{await api(`/api/channel-move/${id}`,{method:'DELETE'});toast('ยกเลิกการย้ายแล้ว');route();}
  catch(e){toast('ไม่สำเร็จ: '+e.message);hint403(e);}
}

/* ── ตัดยอดโอนกันเองออกจากการกระทบยอด (ใช้ ignore endpoint เดิม — ย้อนกลับได้) ── */
async function excludeXfer(id,silent){
  try{await api(`/api/bank-lines/${id}/ignore`,{method:'PATCH',
        body:{ignored:true,note:'โอนกันเอง (ก้อนใหญ่ ไม่ใช่ยอดขาย)'}});
      if(!silent){toast('ตัดออกแล้ว — ย้ายไปกล่องโอนภายใน');route();}}
  catch(e){if(!silent){toast('ไม่สำเร็จ: '+e.message);hint403(e);}throw e;}
}
async function excludeAllXfers(){
  const xfers=(DATA.internal_transfer_suggestions||[]);
  if(!xfers.length)return;
  if(!confirm(`ตัด ${xfers.length} รายการโอนกันเองออกจากการกระทบยอด? (ย้อนกลับได้ที่กล่อง "โอนภายใน")`))return;
  try{const r=await api('/api/bank-lines/bulk-ignore',{method:'POST',body:{line_ids:xfers.map(x=>x.id),ignored:true,note:'v2: internal transfer (bulk)'}});
      const n=(r.ignored||[]).length, sk=(r.skipped||[]).length;
      toast(sk?`ตัดออก ${n} รายการ · ข้าม ${sk} รายการ (${detailText(r.skipped[0].reason)})`:`ตัดออก ${n} รายการแล้ว`);route();}
  catch(e){toast('ไม่สำเร็จ: '+e.message);hint403(e);route();}
}

/* ── จับคู่: การ์ดแนะนำ + เลือกเองในวัน ── */
async function applyReclass(i,btn){
  const s=DATA.reclass_suggestions[i];if(!s)return;
  // key ต่อการ์ด: ปุ่ม "ใช่" กับ "ยืนยันย้าย" ของการ์ดเดียวกันใช้ key เดียวกัน → ส่งได้ครั้งเดียว
  await busy('reclass-'+i,btn,async()=>{
    try{await api('/api/manual',{method:'POST',body:{branch_id:s.branch_id,period_month:s.period_month,
        category:s.category,bank:s.bank,pos_days:s.pos_days,ext:s.ext,note:s.note}});
        toast('จับคู่แล้ว — ย้ายเข้าช่องที่ถูกต้องเรียบร้อย');route();}catch(e){toast('ไม่สำเร็จ: '+e.message);hint403(e);}
  });
}

/* ── เงินอยู่ผิดช่อง: hero card (§3) — เล่าเป็นเรื่อง + แตะเดียวจบ ── */
function reclassHero(s,i){
  const day=(s.pos_day||'').slice(8).replace(/^0/,'');
  const amt=fmt(s.amount);
  const posTh=esc(s.pos_cat_th||s.pos_cat||'');
  const extTh=esc(s.ext_cat_th||s.ext_cat||'');
  const extBank=s.ext_bank_th?` (${esc(s.ext_bank_th)})`:'';
  const hi=s.confidence==='high';
  const pill=hi?['c-ok','ตรงกันพอดี — มั่นใจสูง']:['c-warn','ยอดเท่ากันแต่คนละธนาคาร — น่าจะใช่'];
  return `<div class="card hero-rc mb-12" id="rc-${i}">
    <div class="rc-head">⇄ เงินอาจถูกบันทึกผิดช่อง</div>
    <div class="rc-story">วันที่ <b>${day}</b> — <b>${posTh}</b> เกินมา ฿${amt} แต่ <b>${extTh}</b> ขาดไป ฿${amt} <span class="lbl">เท่ากันพอดี</span></div>
    <div class="lbl" style="margin:4px 0 2px">น่าจะเป็นเงินก้อนเดียวกัน — เข้าบัญชีจริงเป็น<b>${extTh}</b> แต่ในเครื่อง POS กดเป็น<b>${posTh}</b></div>
    <div class="rc-ev">
      <span class="rc-chip">ในเครื่อง POS: ${posTh} <b class="mono">+฿${amt}</b></span>
      <span class="rc-arrow">→</span>
      <span class="rc-chip">เข้าบัญชีจริง: ${extTh} <b class="mono">฿${amt}</b>${extBank}</span>
      <span class="chip ${pill[0]} ml-auto">${pill[1]}</span>
    </div>
    <div class="row" style="margin-top:11px;gap:8px">
      <button class="btn btn-pri" onclick="confirmReclass(${i},this)"><i class="ti ti-circle-check" aria-hidden="true"></i> ใช่ เป็นเงินก้อนเดียวกัน</button>
      <button class="btn btn-sm" onclick="drillReclass(${i})"><i class="ti ti-zoom-in" aria-hidden="true"></i> ขอดูรายการ</button>
      <a href="#" class="lbl ml-auto" onclick="dismissReclass(${i});return false">ไม่ใช่</a>
    </div>
    <div id="rc-med-${i}" style="display:none;margin-top:11px;padding-top:10px;border-top:1px dashed var(--line)">
      <div class="lbl" style="margin-bottom:7px">ยอดเท่ากันแต่คนละธนาคาร อาจบังเอิญตรงกัน — ยืนยันว่าเป็นเงินก้อนเดียวกันจริงไหม?</div>
      <div class="row gap-8">
        <span class="chip c-mut">เหตุผล: แคชเชียร์กดผิดช่อง</span>
        <button class="btn btn-pri btn-sm" onclick="applyReclass(${i},this)">ยืนยันย้าย</button>
        <a href="#" class="lbl" onclick="$('rc-med-${i}').style.display='none';return false">ยกเลิก</a>
      </div>
    </div>
  </div>`;
}
function confirmReclass(i,btn){
  const s=DATA.reclass_suggestions[i];
  if(s&&s.confidence!=='high'){const m=$('rc-med-'+i);if(m)m.style.display='block';return;}
  applyReclass(i,btn);   // มั่นใจสูง — ย้ายได้เลย ไม่ต้องถามซ้ำ
}
function drillReclass(i){
  const s=DATA.reclass_suggestions[i];if(!s)return;
  drillTodo(s.pos_cat);   // กางช่องทาง POS ที่เกินมา ดูรายวัน + บรรทัด statement
}
function dismissReclass(i){
  const el=$('rc-'+i);if(el)el.style.display='none';
}


/* moreMenu() removed in เฟส 6 (2026-06-19): the ⋯ sheet was superseded by the
   4-zone nav (เฟส 5) + Reports Hub (#reports). All its links live there now. */

const FEE_SOURCE={CREDIT:'credit_card',GRAB:'grab',LINEMAN:'lineman'};
const C3_LEG={matched:['c-ok','✓ ตรง'],mismatch:['c-err','≠ ต่าง'],pos_only:['c-warn','รอสรุปยอดบัตร'],
              ext_only:['c-warn','ไม่มียอด POS'],awaiting:['c-warn','รอเงินเข้า'],no_bank:['c-mut','—'],none:['c-mut','—']};
function _legChip(s){const [c,t]=C3_LEG[s]||['c-mut',s];return `<span class="chip ${c}">${t}</span>`;}

function toggleDays(i){
  const el=$('days-'+i);
  if(el.style.display!=='none'){el.style.display='none';return;}
  const ch=DATA.channels[i];
  // ที่มาของยอด POS — แตกเป็นรหัสชำระ ให้ตรวจสอบได้ ไม่ใช่เลขลอย ๆ
  const codes=(ch.codes||[]).map((c,k)=>
    `<button class="chip c-mut mono" title="ดูยอดรายวันของ ${esc(c.code)} (${esc(c.bank)})"
       onclick="event.stopPropagation();toggleCodeDaily(${i},${k})">${esc(c.code)} ${fmt(c.amount)} ▾</button>`).join(' ');
  // B7: TRANSFER/ANY — show chips for every bank the lane touches so staff see both statements at a glance
  const xferBanks=ch.key==='TRANSFER'?[...new Set(ch.days.flatMap(d=>d.banks||[]))].sort():[];
  const xferBankRow=xferBanks.length>1?`<span class="lbl">ตรวจกับ:</span> ${xferBanks.map(b=>`<span class="chip c-mut">${esc(b)}</span>`).join(' ')}`:'';
  // บัตรเครดิต: ขั้น 1 = สรุปยอดบัตร↔POS (gross) · ขั้น 2 = ยอดสุทธิ↔เงินเข้า (สอดคล้อง GRAB/LM)
  const c3=(ch.key==='CREDIT'&&ch.three_way)?`<div class="c3">${ch.three_way
    .filter(r=>r.pos_gross>0||r.settle_gross>0||r.bank_credit>0).map(r=>`
    <div class="card"><b>บัตร ${esc(r.acquirer)}</b>
      <div class="row jc-between">ยอดรูด POS <span class="mono">${fmt(r.pos_gross)}</span></div>
      <div class="row jc-between"><span><span class="lbl">ขั้น 1</span> สรุปยอดบัตร ↔ POS</span> <span class="mono">${fmt(r.settle_gross)}</span> ${_legChip(r.pos_leg)}</div>
      <div class="row jc-between">− ค่าธรรมเนียม+VAT <span class="mono">${fmt(r.settle_fee)}</span></div>
      <div class="row jc-between"><span><span class="lbl">ขั้น 2</span> ยอดสุทธิ ↔ เงินเข้า</span> <span class="mono">${fmt(r.bank_credit)}</span> ${_legChip(r.bank_leg)}</div>
    </div>`).join('')}</div>`:'';
  const pt=payoutTraceHtml(ch);
  const cash3=cashThreeWayHtml(ch, i);
  const cpCards=couponCardsHtml(ch);
  const feeBtn=FEE_SOURCE[ch.key]&&!DATA.closed?
    `<button class="btn btn-sm" onclick="event.stopPropagation();loadFees(${i})">ค่าธรรมเนียม & VAT</button>`:'';
  const mmBtn=!DATA.closed&&ch.key!=='COUPON'?
    `<button class="btn btn-sm" onclick="event.stopPropagation();openMatcher(${i})"><i class="ti ti-tool" aria-hidden="true"></i> จับคู่ละเอียด</button>`:'';
  // คูปอง: แต้ม KTC/BAY ตรวจอัตโนมัติแล้ว — ลิงก์วิเคราะห์คูปองซีรีส์ (in-app #coupon)
  const cpBtn=ch.key==='COUPON'?
    `<a class="btn btn-sm" href="#coupon" onclick="event.stopPropagation()"><i class="ti ti-ticket" aria-hidden="true"></i> รายละเอียดคูปอง</a>`:'';
  const worklist=dayWorklistHtml(ch,i);
  el.innerHTML=`
  ${codes||xferBankRow||feeBtn||mmBtn||cpBtn?`<div class="row" style="padding:10px 14px 0">${codes?`<span class="lbl">ที่มา POS:</span> ${codes}`:''} ${xferBankRow} ${feeBtn} ${mmBtn} ${cpBtn}</div>`:''}
  <div id="codedaily-${i}" style="padding:0 14px"></div>
  ${worklist}${c3}${pt}${cash3}${cpCards}
  <div id="fees-${i}"></div>
  <div id="matcher-${i}"></div>
  <div class="daygrid">${ch.days.map(d=>`
    <div class="daycell d-${d.ack?'ok':esc(d.status)}" role="button" tabindex="0" onclick="event.stopPropagation();openDay(${i},${jsa(d.day)})">
      <b>${+d.day.slice(8)}</b> <span class="lbl">${d.ack?'✓':({ok:'✓',diff:'≠',waiting:'<i class="ti ti-hourglass-high" aria-hidden="true"></i>',setup:'?',no_source:'·'}[d.status]||'')}</span>
      <div class="mono fs-11">${fmt(d.pos)}</div>
      ${d.status==='diff'&&!d.ack?`<div class="mono fs-11 t-err">${d.variance>0?'+':''}${fmt(d.variance)}</div>`:''}
      ${d.ack_stale?`<div class="lbl fs-10 t-warn" title="รับทราบไว้ที่ ${fmt(d.ack_stale.variance)} — ต้องรับทราบใหม่">${esc(d.detail||'ยอดเปลี่ยนหลังรับทราบ')}</div>`:''}
    </div>`).join('')}</div>
  <div id="daylines-${i}" style="padding:0 12px 12px"></div>`;
  el.style.display='';
}
