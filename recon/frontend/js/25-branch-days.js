/* recon frontend — 25-branch-days.js: #b/<id>/<CHANNEL> — a channel's days: payout trace, day worklist, cash 3-way, coupons, fees, day drill (POS bills ↔ statement lines), session status.
   Plain scripts sharing one global scope, loaded in order by index.html (00 → 90);
   a slice may call any other slice's functions, but top-level code only uses earlier slices. */
/* ── ขั้น 2 ของ Grab/Line Man: ยอดสุทธิรายวัน ↔ เงินเข้าธนาคาร (รวมวันได้) ── */
function payoutTraceHtml(ch){
  if(!(ch.key==='GRAB'||ch.key==='LINEMAN')||!ch.payout_trace)return '';
  const p=ch.payout_trace;
  if(!p.payouts||!p.payouts.length)return '';
  return `<div class="pad-ch">
    <div class="lbl mb-4"><b>ขั้น 1</b>: สรุปยอด ${esc(ch.label)} ↔ POS (gross ก่อนค่าธรรมเนียม+VAT — ดูตัวเลขด้านบน) ·
      <b>ขั้น 2</b>: ยอดสุทธิ ↔ เงินเข้า KBANK (โอนวันถัดไป อาจรวม 2 วัน)</div>
    <div class="card" style="padding:0;box-shadow:none;max-height:250px;overflow:auto"><table>
      <tr><th>วันขาย</th><th>ยอดสุทธิ</th><th>เงินเข้า</th><th>วันที่เงินเข้า</th><th>สถานะ</th></tr>
      ${p.payouts.map(x=>`<tr>
        <td class="tl">${x.days.map(d=>+d.day.slice(8)).join('+')}
          ${x.combined?`<span class="chip c-mut">รวม ${x.days.length} วัน</span>`:''}</td>
        <td class="mono">${fmt(x.group_net)}</td>
        <td class="mono">${x.amount?fmt(x.amount):'—'}</td>
        <td>${x.payout_date?x.payout_date.slice(5):'—'}${x.lag?` <span class="lbl">ช้า ${x.lag} วัน</span>`:''}</td>
        <td>${x.status==='matched'?'<span class="chip c-ok">✓ ตรง</span>':'<span class="chip c-warn">รอเงินเข้า</span>'}</td>
      </tr>`).join('')}
    </table></div>
    <div class="lbl" style="margin-top:3px">สุทธิรวม ${fmt(p.net_total)} · เข้าบัญชีแล้ว ${fmt(p.payout_total)}${p.awaiting_total>0?` · รอเข้า ${fmt(p.awaiting_total)}`:''}</div>
  </div>`;
}

/* ── ตรวจผลต่าง "รายวัน": รับทราบทีละวัน (reconcile BY DATE) ── */
function dayWorklistHtml(ch,i){
  if(ch.key==='COUPON')return '';
  const vdays=(ch.days||[]).filter(d=>Math.abs(d.variance)>0.5);
  if(!vdays.length)return '';
  const openCount=vdays.filter(d=>!d.ack).length;
  const rows=vdays.map(d=>{
    const acked=d.ack;
    const st=!acked&&d.ack_stale;   // acked once, then the day's figure moved > ฿50 → re-ack
    const chip=acked?'<span class="chip c-mut">✓ รับทราบ</span>'
      :(d.status==='diff'?'<span class="chip c-err">≠ ต่าง</span>':'<span class="chip c-warn">ต่างเล็กน้อย</span>')
       +(st?` <span class="chip c-warn">${esc(d.detail||'ยอดเปลี่ยนหลังรับทราบ')}</span>`:'');
    const act=DATA.closed?''
      :acked?`<a href="#" onclick="event.stopPropagation();unackDay(${acked.id});return false">เลิกรับทราบ</a>`
            :`<button class="btn btn-sm" onclick="event.stopPropagation();ackDay(${i},${jsa(d.day)},${+d.variance||0})">รับทราบวันนี้</button>`;
    return `<tr${acked?' class="dim"':''}><td><b>${+d.day.slice(8)}</b></td>
      <td class="mono">${fmt(d.pos)}</td>
      <td class="mono">${d.ext?fmt(d.ext):'—'}</td>
      <td class="mono ${acked?'t-mut':'t-err'}">${d.variance>0?'+':''}${fmt(d.variance)}</td>
      <td>${chip}</td><td class="tr">${act}</td></tr>
      ${acked?`<tr class="dim"><td></td><td colspan="5" class="lbl tl">"${esc(acked.note||'')}"${acked.by?` — ${esc(acked.by)}`:''}</td></tr>`:''}
      ${st?`<tr><td></td><td colspan="5" class="lbl tl t-warn">เคยรับทราบไว้ที่ ${fmt(st.variance)} ("${esc(st.note||'')}") — ยอดวันนี้เปลี่ยนแล้ว ต้องรับทราบใหม่</td></tr>`:''}`;
  }).join('');
  return `<div class="pad-ch">
    <div class="lbl mb-4"><b>ตรวจผลต่างรายวัน</b> — กระทบยอดทีละวัน รับทราบพร้อมเหตุผล (ติดไปในไฟล์ Excel ให้ผู้จัดการเห็น)${openCount?` · เหลือ ${openCount} วัน`:' · ครบแล้ว'}</div>
    <div class="card" style="padding:0;box-shadow:none;max-height:320px;overflow:auto"><table>
      <tr><th>วัน</th><th>POS</th><th>เงินเข้า</th><th>ต่าง</th><th>สถานะ</th><th></th></tr>
      ${rows}
    </table></div></div>`;
}
let _ackDayCtx=null;
function ackDay(i,day,variance){
  const ch=DATA.channels[i];
  _ackDayCtx={channel:ch.key,day,variance};
  const opts=ACCEPT_REASONS.map((rsn,k)=>`<button class="btn rc-reason" onclick="pickAckDayReason(${k})">${esc(rsn)}</button>`).join('');
  showSheet(`<div class="sheet-h">รับทราบผลต่างวัน ${esc(day)} — ${esc(ch.label)}</div>
    <div class="lbl mb-10">ผลต่างวันนี้ ${variance>0?'+':''}${fmt(variance)} บาท — เลือกหรือพิมพ์เหตุผล (อย่างน้อย 10 ตัวอักษร)</div>
    <div class="rc-reasons">${opts}</div>
    <textarea id="ackday-note" class="sheet-ta" placeholder="พิมพ์เหตุผลเอง (อย่างน้อย 10 ตัวอักษร)"></textarea>
    <div class="row mt-10 gap-8">
      <button class="btn btn-pri" onclick="submitAckDay(this)">บันทึกรับทราบ</button>
      <button class="btn" onclick="closeSheet()">ยกเลิก</button>
    </div>`);
}
function pickAckDayReason(k){const ta=$('ackday-note');if(ta){ta.value=ACCEPT_REASONS[k];ta.focus();}}
async function submitAckDay(btn){
  const note=(($('ackday-note')||{}).value||'').trim();
  if(note.length<10){toast('ต้องใส่เหตุผลอย่างน้อย 10 ตัวอักษร');return;}
  const ctx=_ackDayCtx;if(!ctx)return;
  // variance ส่งไปเพื่อเข้ากันได้กับ backend เดิม — เซิร์ฟเวอร์คำนวณผลต่างเองจากข้อมูลจริง
  await busy('ackday',btn,async()=>{
    try{await api('/api/day-ack',{method:'POST',body:{branch_id:DATA.branch_id,period_month:MONTH,
        category:ctx.channel,day:ctx.day,variance:ctx.variance,note:note}});
        closeSheet();toast('รับทราบวันนี้แล้ว');route();}catch(e){toast('ไม่สำเร็จ: '+e.message);hint403(e);}
  });
}
async function unackDay(ackId){
  try{await api(`/api/day-ack/${ackId}`,{method:'DELETE'});toast('เลิกรับทราบแล้ว');route();}
  catch(e){toast('ไม่สำเร็จ: '+e.message);}
}

/* ── เงินสด 3 ทาง: POS ↔ สลิปนำฝาก (พิมพ์ยอด) ↔ เงินเข้าธนาคาร ── */
function cashThreeWayHtml(ch, chIdx){
  if(ch.key!=='CASH'||!(ch.cash_three_way||[]).length)return '';
  const SLIP_CHIP={matched:r=>`<span class="chip c-ok">✓${r.slip_variance?` ต่าง ${r.slip_variance>0?'+':''}${fmt(r.slip_variance)}`:''}</span>`,
    variance:r=>`<span class="chip c-err">ต่าง ${r.slip_variance>0?'+':''}${fmt(r.slip_variance)}</span>`,
    slip_only:()=>'<span class="chip c-warn">ไม่มียอด POS</span>',
    no_slip:()=>'<span class="chip c-mut">ยังไม่ใส่</span>'};
  const BANK_CHIP={matched:()=>'<span class="chip c-ok">✓ เข้าแล้ว</span>',
    variance:r=>`<span class="chip c-err">ต่าง ${r.bank_variance>0?'+':''}${fmt(r.bank_variance)}</span>`,
    pending:()=>'<span class="chip c-warn">รอเงินเข้า</span>',none:()=>'<span class="lbl">—</span>'};
  return `<div class="pad-ch">
    <div class="lbl mb-4">3 ทาง: POS เงินสด ↔ สลิปนำฝาก (พิมพ์ยอดจากสลิป) ↔ เงินเข้าธนาคาร — ฝากวันถัดไปก่อน 13:00</div>
    <div class="card p-0 flat maxh-300 ov-auto"><table>
      <tr><th>วัน</th><th>POS เงินสด</th><th>สลิปนำฝาก</th><th>เงินเข้า</th><th>ฝากเมื่อ</th><th>สถานะเงินเข้า</th><th><i class="ti ti-camera" aria-hidden="true"></i></th></tr>
      ${ch.cash_three_way.map(r=>{
        const slipCell=DATA.closed
          ?`<span class="mono">${r.slip_amount!=null?fmt(r.slip_amount):'—'}</span>`
          :`<input class="fee-in" type="number" step="0.01" min="0" value="${r.slip_amount!=null?esc(r.slip_amount):''}"
              placeholder="ยอดสลิป" data-slip="${esc(r.day)}" onchange="saveSlip(${chIdx},${jsa(r.day)},this)">`;
        const photoBtn=`<label class="btn btn-sm" title="แนบรูปสลิปนำฝาก" style="cursor:pointer;padding:2px 6px"><i class="ti ti-camera" aria-hidden="true"></i><input type="file" accept="image/*,.pdf" style="display:none" onchange="attachSlip(${jsa(r.day)},this)"></label>`;
        return `<tr><td><b>${+r.day.slice(8)}</b></td>
          <td class="mono">${fmt(r.pos_cash)}</td>
          <td>${slipCell} ${(SLIP_CHIP[r.slip_leg]||(()=>''))(r)}</td>
          <td class="mono">${r.bank_deposit!=null?fmt(r.bank_deposit):'—'}</td>
          <td>${r.deposit_date?r.deposit_date.slice(8)+(r.deposit_combined?' <span class="lbl">(ฝากรวมวัน)</span>':''):'—'}</td>
          <td>${(BANK_CHIP[r.bank_leg]||(()=>''))(r)}</td>
          <td>${photoBtn}</td></tr>`;}).join('')}
    </table></div>
    <div class="lbl" style="margin-top:3px">สลิปต่างจาก POS ได้ไม่เกิน ±฿10 — พิมพ์แล้วบันทึกอัตโนมัติ · ไม่ใส่สลิปไม่บล็อกการปิดเดือน</div>
  </div>`;
}
async function saveSlip(i,day,input){
  if(input.value==='')return;
  const amt=parseFloat(input.value);
  if(isNaN(amt)||amt<=0){toast('ยอดสลิปไม่ถูกต้อง');return;}
  const bid=DATA.branch_id, t=_ROUTE_SEQ;
  try{await api('/api/deposit-slips',{method:'POST',body:{branch_id:bid,sale_day:day,amount:amt}});
      toast('บันทึกสลิปแล้ว');}
  catch(e){toast('ไม่สำเร็จ: '+e.message);hint403(e);return;}
  // อัปเดตเฉพาะแผงเงินสดนี้ — ไม่โหลดทั้งหน้า (แผงไม่ยุบ โฟกัสช่องถัดไปยังอยู่)
  let r;try{r=await api(`/api/v2/reconcile?branch_id=${bid}&month=${MONTH}`);}catch(e){return;}
  if(!_cur(t)||!DATA||DATA.branch_id!==bid)return;
  const fresh=(r.channels||[])[i];
  if(!fresh||!DATA.channels[i]||fresh.key!==DATA.channels[i].key){route();return;}
  DATA.channels[i]=fresh;
  const el=$('days-'+i); if(!el)return;
  const ae=document.activeElement, focusDay=ae&&ae.dataset?ae.dataset.slip:null;
  el.innerHTML=chDaysHtml(i);
  if(focusDay){const nx=el.querySelector(`input[data-slip="${focusDay}"]`); if(nx)nx.focus();}
}
async function attachSlip(day,input){
  const file=input.files[0];
  if(!file)return;
  const fd=new FormData();
  fd.append('branch_id',DATA.branch_id);fd.append('day',day);fd.append('file',file);
  try{await api('/api/attachments',{method:'POST',body:fd});toast('แนบรูปสลิปแล้ว');}
  catch(e){toast('แนบรูปไม่สำเร็จ: '+e.message);hint403(e);}
  input.value='';
}

/* ── คูปอง: การ์ดแต้ม KTC/BAY (วิธี issuer_points ตามตารางเจ้าของ) ── */
const IP_CHIP={matched:'<span class="chip c-ok">✓ ตรง</span>',mismatch:'<span class="chip c-err">≠ ต่าง</span>',
  waiting_doc:'<span class="chip c-warn">รอใบสรุปคะแนน</span>',awaiting_payout:'<span class="chip c-warn">รอเงินเข้า</span>',
  ext_only:'<span class="chip c-warn">ไม่มียอด POS</span>'};
function bayPayoutTraceHtml(p){
  if(!p||!(p.payouts||[]).length)return '';
  const rows=p.payouts.map(x=>`<tr>
    <td class="tl">${x.days.map(d=>+d.day.slice(8)).join('+')}${x.combined?` <span class="chip c-mut">รวม ${x.days.length} วัน</span>`:''}</td>
    <td class="mono">${fmt(x.group_net)}</td>
    <td class="mono">${x.amount?fmt(x.amount):'—'}</td>
    <td>${x.payout_date?x.payout_date.slice(5):'—'}${x.lag?` <span class="lbl">ช้า ${x.lag} วัน</span>`:''}</td>
    <td>${x.status==='matched'?'<span class="chip c-ok">✓ ตรง</span>':'<span class="chip c-warn">รอเงินเข้า</span>'}</td>
  </tr>`).join('');
  return `<div class="card" style="padding:0;box-shadow:none;border-color:var(--line-soft);margin-top:6px;max-height:200px;overflow:auto"><table>
    <tr><th>วันขาย</th><th>ยอดสรุป</th><th>เงินเข้า KBANK</th><th>วันที่</th><th>สถานะ</th></tr>
    ${rows}</table></div>
  <div class="lbl" style="margin-top:3px">สุทธิรวม ${fmt(p.net_total)} · เข้าบัญชีแล้ว ${fmt(p.payout_total)}${p.awaiting_total>0?` · รอเข้า ${fmt(p.awaiting_total)}`:''}</div>`;
}
function couponCardsHtml(ch){
  if(ch.key!=='COUPON'||!(ch.issuer_points||[]).length)return '';
  const bayTrace=ch.bay_payout_trace||null;
  return `<div class="c3">${ch.issuer_points.map(r=>`
    <div class="card"><b>แต้มบัตร ${esc(r.issuer)}</b>
      <div class="row jc-between">ใช้แต้มบน POS <span class="mono">${fmt(r.pos_point)}</span></div>
      <div class="row jc-between">ใบสรุปคะแนน <span class="mono">${fmt(r.settle_gross)}</span> ${IP_CHIP[r.status]||''}</div>
      ${r.bank_in_credit
        ?'<div class="lbl">เงินคืนแต้มรวมอยู่ในยอดฝากบัตร KTC (ดูช่องบัตรเครดิต)</div>'
        :`<div class="row jc-between">เงินเข้า KBANK <span class="mono">${fmt(r.bank_payout)}</span></div>
          ${r.issuer==='BAY'?bayPayoutTraceHtml(bayTrace):''}`}
    </div>`).join('')}</div>`;
}

/* ── ค่าธรรมเนียม & VAT (บัตร / Grab / Line Man) ── */
async function loadFees(i){
  const ch=DATA.channels[i], src=FEE_SOURCE[ch.key];
  const box=$('fees-'+i);
  if(box.innerHTML){box.innerHTML='';return;}
  box.innerHTML='<div class="muted pad-8-14">กำลังโหลด…</div>';
  let lines;try{lines=await api(`/api/lines/${src}?branch_id=${DATA.branch_id}&period_month=${MONTH}`);}
  catch(e){box.innerHTML=`<div class="chip c-err m-8-14">โหลดไม่ได้: ${esc(e.message)}</div>`;return;}
  if(!lines.length){box.innerHTML='<div class="muted pad-8-14">ยังไม่มีรายการ (อัปโหลดเอกสารก่อน)</div>';return;}
  box.innerHTML=`<div class="pad-8-12"><div class="card p-0 maxh-300 ov-auto"><table>
    <tr><th>วันที่</th><th>อ้างอิง</th><th>ยอดเต็ม</th><th>ค่าธรรมเนียม</th><th>VAT</th><th>สุทธิ</th></tr>
    ${lines.map(l=>`<tr data-gross="${l.gross}" data-other="${l.other||0}">
      <td>${esc(l.date)}</td><td class="tl">${esc(l.ref)}</td>
      <td class="mono">${fmt(l.gross)}</td>
      <td><input class="fee-in" type="number" step="0.01" value="${esc(l.fee!=null?l.fee:l.fee_parsed)}"
           onchange="saveFee('${src}',${l.id},this)"></td>
      <td><input class="fee-in" type="number" step="0.01" value="${esc(l.vat!=null?l.vat:l.vat_parsed)}"
           onchange="saveFee('${src}',${l.id},this)"></td>
      <td class="mono" id="net-${src}-${l.id}">${fmt(l.net)}</td>
    </tr>`).join('')}
  </table></div>
  <div class="muted mt-4">แก้แล้วบันทึกอัตโนมัติ — ยอดสุทธิ (หลังหักค่าธรรมเนียม) คือตัวที่เทียบกับเงินเข้าบัญชี</div></div>`;
}
// บันทึกทีละครั้งต่อแถว (คิว) — แก้ค่าธรรมเนียมแล้ว VAT ติดกัน คำขอไม่แซงกันจนค่าเก่าทับค่าใหม่
const _FEE_Q={};
function saveFee(src,id,input){
  const row=input.closest('tr'), key=src+'-'+id;
  const run=async()=>{
    const ins=row.querySelectorAll('input');
    const fee=parseFloat(ins[0].value), vat=parseFloat(ins[1].value);   // อ่านค่าล่าสุดตอนถึงคิว
    if(isNaN(fee)||isNaN(vat)||fee<0||vat<0){toast('ตัวเลขไม่ถูกต้อง');return;}
    try{await api(`/api/lines/${src}/${id}/fee-vat`,{method:'PATCH',body:{fee_manual:fee,vat_manual:vat}});
        const net=(+row.dataset.gross)-fee-vat-(+row.dataset.other);
        const nc=$('net-'+key); if(nc)nc.textContent=fmt(net);
        toast('บันทึกค่าธรรมเนียมแล้ว');}
    catch(e){toast('ไม่สำเร็จ: '+e.message);hint403(e);}
  };
  _FEE_Q[key]=(_FEE_Q[key]||Promise.resolve()).then(run);
  return _FEE_Q[key];
}

async function openDay(i,day){
  const ch=DATA.channels[i];
  const box=$('daylines-'+i); if(!box)return;
  WS.openDay={key:ch.key,day};
  box.innerHTML=loadingState();
  const d=ch.days.find(x=>x.day===day);
  let lines=[]; const errs=[];
  for(const b of d.banks){
    try{const r=await api(`/api/v2/lane?branch_id=${DATA.branch_id}&month=${MONTH}&category=${ch.key}&bank=${encodeURIComponent(b)}&day=${day}`);
        lines.push(...r.lines.map(l=>({...l,bank:b})));}catch(e){errs.push(`${b}: ${e.message}`);}
  }
  // ฝั่ง POS รายบิล — แตกยอดขายของวันนี้เป็นใบเสร็จ (จาก sale_payment, tie-out ตรงกับ d.pos)
  let pos=null, posErr='';
  try{pos=await api(`/api/v2/pos-lines?branch_id=${DATA.branch_id}&month=${MONTH}&category=${ch.key}&day=${day}`);}catch(e){posErr=e.message;}
  if(!$('daylines-'+i)||!WS.openDay||WS.openDay.key!==ch.key||WS.openDay.day!==day)return;   // ผู้ใช้เปิดวัน/รายการอื่นไปแล้ว
  // Phase 4 #4: hint pairs — unmatched bank line equal to an unmatched POS-code amount (±฿1).
  // Advisory chip only (no auto-tick); matching itself happens in the matcher panel.
  const _posAmts=(pos&&pos.by_code?pos.by_code:[]).map(b=>Math.round(b.amount));
  const _hintJ=new Set();
  lines.forEach((l,j)=>{
    if(l.match_status==='manual'||l.match_status==='matched')return;
    if(_posAmts.some(a=>Math.abs(a-Math.round(l.amount))<=1)) _hintJ.add(j);
  });
  _DAYS[i]={i,day,lines,key:ch.key};
  const varCls=Math.abs(d.variance)<=100?'t-ok':'t-err';
  const LST={matched:['c-ok','จับคู่แล้ว'],manual:['c-ok','จับคู่เอง'],excluded:['c-mut','ตัดออก'],unmatched:['c-warn','ยังไม่จับคู่']};
  const mxBtn=!DATA.closed&&ch.key!=='COUPON'
    ?`<button class="btn btn-sm btn-pri ml-auto" onclick="mxOpen(${jsa(ch.key)},{day:${jsa(day)}})"><i class="ti ti-link" aria-hidden="true"></i> จับคู่วันนี้ในตัวจับคู่</button>`:'';
  box.innerHTML=`<div class="card bg-subtle">
    <div class="row mb-6"><b class="fs-135">${esc(day)} · ${esc(ch.label)}</b>
      <span class="lbl">POS ${fmt(d.pos)}${pos&&pos.count?` · ${pos.count} บิล`:''} · เข้าจริง ${fmt(d.ext)} ·
        ต่าง <span class="fw-600 ${varCls}">${d.variance>0?'+':''}${fmt(d.variance)}</span></span>
      ${mxBtn}<button class="btn btn-sm" onclick="closeDay(${i})" aria-label="ปิดรายละเอียดวัน">ปิด</button></div>
    ${posErr?`<div class="card c-err my-2-6 pad-8-12">โหลดยอด POS รายบิลไม่สำเร็จ: ${esc(posErr)}</div>`:posDayHtml(pos,i)}
    <div class="lbl mt-10 mb-4"><b>② เงินเข้าธนาคาร</b> (สเตทเมนต์) ของวันนี้ — จับคู่ด้วยมือได้ที่ "จับคู่วันนี้ในตัวจับคู่"</div>
    ${errs.length?`<div class="card c-err mb-6 pad-8-12">โหลดบรรทัดเงินเข้าไม่สำเร็จ — ${errs.map(esc).join(' · ')}</div>`:''}
    ${lines.length?`<div class="ovx-auto"><table>${lines.map((l,j)=>{const [lc,lt]=LST[l.match_status]||['c-mut',l.match_status||'—'];
      return `<tr>
      <td class="tl">${esc(l.ref||'')}<span class="lbl"> ${esc(l.bank)}</span>${_hintJ.has(j)?` <span class="chip c-ok ov-mini" title="ยอดตรงกับยอดขาย POS วันนี้ — เปิดตัวจับคู่เพื่อยืนยัน">น่าจะคู่กัน</span>`:''}</td>
      <td>${esc(l.date)}</td>
      <td class="mono">${fmt(l.amount)}</td>
      <td>${l.match_status?`<span class="chip ${lc}">${lt}</span>`:''}</td>
      <td class="w-90">${!DATA.closed&&l.id?`<button class="btn btn-sm" onclick="ignoreLine(${+l.id})">ตัดออก</button>`:''}</td>
    </tr>`;}).join('')}</table></div>`
    :(errs.length?'':'<div class="muted">ไม่มีบรรทัดเงินเข้าของวันนี้ (รอเอกสาร หรือยอดถูกฝากรวมวันอื่น)</div>')}
  </div>`;
  box.scrollIntoView({block:'nearest',behavior:_scrollBehavior()});
}
function closeDay(i){const b=$('daylines-'+i);if(b)b.innerHTML='';WS.openDay=null;}

/* ── ฝั่ง POS รายบิล: จัดกลุ่มตามรหัสชำระ คลิกขยายดูใบเสร็จรายใบ ── */
function posDayHtml(pos,i){
  if(!pos||!pos.count){
    // Distinguish "POS is synced but this lane genuinely has no bills today"
    // (normal for an ext_only / internal-transfer lane) from "POS truly not
    // synced". The old copy always said "not synced" and triggered false alarms.
    return DATA.pos_daily_available
      ? '<div class="lbl my-2-4"><b>① ยอดขาย POS</b> — ไม่มีบิล POS ของช่องทางนี้ในวันนี้ (เงินเข้าโดยไม่มียอดขาย POS)</div>'
      : '<div class="lbl my-2-4"><b>① ยอดขาย POS</b> — ยังไม่ได้ sync ข้อมูล POS</div>';
  }
  const rows=pos.by_code.map((b,k)=>{
    const recs=pos.lines.filter(l=>l.code===b.code);
    return `<tr class="grid-ch" style="display:table-row;cursor:pointer" role="button" tabindex="0" onclick="togglePosCode(${i},${k})">
        <td class="tl"><span class="lbl" id="poscar-${i}-${k}">▸</span> ${esc(b.code)}
          <span class="lbl">${esc(b.bank)}</span></td>
        <td class="mono">${fmt(b.amount)}</td><td class="lbl">${b.count} บิล</td></tr>
      <tr id="poscode-${i}-${k}" style="display:none"><td colspan="3" class="p-0 bg-card">
        <div style="max-height:180px;overflow:auto"><table>${recs.map(l=>`<tr>
          <td class="tl fs-12 t-sub">${esc(l.receipt_no)}</td>
          <td class="mono fs-12">${fmt(l.amount)}</td></tr>`).join('')}</table></div></td></tr>`;
  }).join('');
  return `<div class="lbl my-2-4"><b>① ยอดขาย POS รายบิล</b> — รวม ${fmt(pos.total)}${pos.truncated?' · แสดงบางส่วน':''} (คลิกรหัสเพื่อดูใบเสร็จ)</div>
    <div class="card" style="padding:0;box-shadow:none;max-height:240px;overflow:auto"><table>
      <tr><th>รหัสชำระ</th><th>ยอด</th><th>จำนวนบิล</th></tr>${rows}</table></div>`;
}
function togglePosCode(i,k){
  const e=$(`poscode-${i}-${k}`),car=$(`poscar-${i}-${k}`);
  if(!e)return;
  const show=e.style.display==='none';
  e.style.display=show?'':'none';
  if(car)car.textContent=show?'▾':'▸';
}

/* ── คลิกชิปรหัส POS → ยอดรายวันของรหัสนั้นทั้งเดือน (by_day ไม่ถูก truncate) ── */
async function toggleCodeDaily(i,k){
  const c=((DATA.channels[i]||{}).codes||[])[k]; if(!c)return;
  const code=c.code, box=$('codedaily-'+i);
  if(box.dataset.code===String(k)){box.innerHTML='';box.dataset.code='';return;}
  box.dataset.code=String(k);
  box.innerHTML='<div class="muted pad-6-0">กำลังโหลด…</div>';
  let r;try{r=await api(`/api/v2/pos-lines?branch_id=${DATA.branch_id}&month=${MONTH}&category=${DATA.channels[i].key}&code=${encodeURIComponent(code)}`);}
  catch(e){box.innerHTML=`<div class="chip c-err my-6">${esc(e.message)}</div>`;return;}
  const days=r.by_day||[];
  if(!days.length){box.innerHTML='<div class="muted pad-6-0">ไม่มียอดของรหัสนี้</div>';return;}
  box.innerHTML=`<div class="card" style="padding:8px 10px;margin:6px 0;box-shadow:none;background:var(--subtle)">
    <div class="row mb-4"><b class="fs-13">${esc(code)} · ยอดรายวัน</b>
      <span class="lbl">รวม ${fmt(r.total)} · ${r.count} บิล${r.truncated?' (ใบเสร็จแสดงบางส่วน)':''}</span>
      <a href="#" class="lbl ml-auto" onclick="toggleCodeDaily(${i},${k});return false">ปิด</a></div>
    <div class="maxh-220 ov-auto"><table><tr><th>วัน</th><th>ยอด</th></tr>
    ${days.map(x=>`<tr><td>${+String(x.day).slice(8)}</td><td class="mono">${fmt(x.amount)}</td></tr>`).join('')}</table></div></div>`;
}
let _DAYS={};                       // the day drill open per channel index

async function ignoreLine(id){
  if(!confirm('ยืนยัน: บรรทัดนี้ไม่ใช่ยอดขาย (เช่น โอนภายใน)?'))return;
  try{await api(`/api/bank-lines/${id}/ignore`,{method:'PATCH',body:{ignored:true,note:'v2: not revenue'}});
      toast('ตัดออกแล้ว');route();}catch(e){toast('ไม่สำเร็จ: '+e.message);}
}

async function applySugg(qi,btn){
  const q=((DATA&&DATA.setup_questions)||[])[qi], s=q&&q.suggestion; if(!s)return;
  await busy('map-'+qi,btn,async()=>{
    try{await api(`/api/payment-map/${s.map_id}`,{method:'PATCH',body:{category:s.category,bank:s.bank}});
        toast('บันทึกแล้ว');route();}catch(e){toast('ไม่สำเร็จ: '+e.message);hint403(e);}
  });
}
function toggleMapper(qi){const e=$('mapper-'+qi);e.style.display=e.style.display==='none'?'flex':'none';}
async function applyMapper(qi,btn){
  const q=((DATA&&DATA.setup_questions)||[])[qi]; if(!q)return;
  const code=q.code, cat=$('map-cat-'+qi).value, bank=$('map-bank-'+qi).value;
  await busy('map-'+qi,btn,async()=>{
    try{
      // มีแถวใน map แล้ว (เช่น bank=UNKNOWN) → PATCH; ยังไม่มีเลย → POST สร้างใหม่
      const all=await api('/api/payment-map');
      const hit=all.find(m=>m.payment_code_norm===code);
      if(hit) await api(`/api/payment-map/${hit.id}`,{method:'PATCH',body:{category:cat,bank:bank}});
      else    await api('/api/payment-map',{method:'POST',body:{payment_code_norm:code,category:cat,bank:bank,reconcile:1}});
      toast(`ตั้งค่า ${code} → ${cat}·${bank} แล้ว`);route();
    }catch(e){toast('ไม่สำเร็จ: '+e.message);hint403(e);}
  });
}

/* ── สถานะ session: ส่งตรวจ → ปิดเดือน ── */
async function ensureSession(bid){
  if(DATA.session)return DATA.session.session_id;
  const s=await api('/api/sessions',{method:'POST',body:{branch_id:bid,period_month:MONTH}});
  return s.session_id;
}
function stForm(status,by,reason){const fd=new FormData();fd.append('status',status);if(by)fd.append('approved_by',by);if(reason)fd.append('reason',reason);return fd;}
async function setSt(bid,st,btn){
  await busy('status',btn,async()=>{
    try{const sid=await ensureSession(bid);
        await api(`/api/sessions/${sid}/status`,{method:'PATCH',body:stForm(st)});
        toast(st==='in_review'?'ส่งตรวจแล้ว':'ส่งกลับแก้แล้ว');route();}catch(e){toast('ไม่สำเร็จ: '+e.message);hint403(e);}
  });
}
async function closeMonth(bid,btn){
  const who=prompt('ชื่อผู้จัดการที่อนุมัติ:');
  if(who===null)return;
  if(!who.trim()){toast('ต้องใส่ชื่อผู้อนุมัติ');return;}
  if(!confirm('ปิดเดือน: บันทึกผลถาวร ออกไฟล์ Excel ได้ และล็อกการแก้ไขทั้งเดือนนี้ ยืนยัน?'))return;
  await busy('status',btn,async()=>{
    try{
      const sid=await ensureSession(bid);
      await api(`/api/sessions/${sid}/run-match`,{method:'POST'});   // snapshot at close
      await approveWithManagerCode(sid,who.trim());
      toast('ปิดเดือนแล้ว');route();
    }catch(e){toast('ไม่สำเร็จ: '+e.message);hint403(e);}
  });
}
// PATCH สถานะที่ต้องใช้รหัสผู้จัดการ (ปิดเดือน / เปิดงวดใหม่): ลองรหัสที่จำไว้ก่อน ถ้าเซิร์ฟเวอร์ขอ (403) ค่อยถามแล้วลองใหม่
async function statusWithManagerCode(sid,mkForm,askMsg){
  let code=sessionStorage.getItem('mgr_code')||'';
  for(let attempt=0;attempt<2;attempt++){
    try{
      await api(`/api/sessions/${sid}/status`,
        {method:'PATCH',body:mkForm(),headers:code?{'X-Manager-Token':code}:{}});
      if(code)sessionStorage.setItem('mgr_code',code);
      return;
    }catch(e){
      if(!String(e.message).includes('ผู้จัดการ')||attempt===1)throw e;
      code=prompt(askMsg)||'';
      if(!code)throw new Error('ยกเลิก — ไม่ได้ใส่รหัสผู้จัดการ');
    }
  }
}
function approveWithManagerCode(sid,who){
  return statusWithManagerCode(sid,()=>stForm('approved',who),'ใส่รหัสผู้จัดการเพื่อปิดเดือน:');
}
/* เปิดงวดที่ปิดแล้วใหม่ (approved/closed → กำลังทำ) — ผู้จัดการเท่านั้น: รหัสผู้จัดการ + เหตุผล ≥10 ตัวอักษร (ลง audit log) */
function reopenMonth(bid){
  if(!DATA||!DATA.session){toast('ไม่พบงวดของสาขานี้');return;}
  showSheet(`<div class="sheet-h"><i class="ti ti-lock-open" aria-hidden="true"></i> เปิดงวดใหม่ — ${esc(DATA.branch_name||'')} · ${esc(monthTh(MONTH))}</div>
    <div class="lbl mb-10">ปลดล็อกเดือนที่ปิดแล้วให้กลับมาแก้ไขได้ (สถานะกลับเป็น "กำลังทำ") — ต้องใช้<b>รหัสผู้จัดการ</b> และ<b>เหตุผล</b> ที่จะบันทึกในประวัติการแก้ไข · แก้เสร็จต้องส่งตรวจและปิดเดือนใหม่ ระบบจะสร้างหลักฐานแช่แข็งชุดใหม่ให้</div>
    <textarea id="reopen-note" class="sheet-ta" placeholder="เหตุผลที่ต้องเปิดงวดใหม่ (อย่างน้อย 10 ตัวอักษร) เช่น พบสเตทเมนต์ KBANK วันที่ 30 อัปไม่ครบ"></textarea>
    <div class="row mt-10 gap-8">
      <button class="btn btn-pri" onclick="submitReopen(${bid},this)">เปิดงวดใหม่</button>
      <button class="btn" onclick="closeSheet()">ยกเลิก</button>
    </div>`);
  const ta=$('reopen-note'); if(ta)ta.focus();
}
async function submitReopen(bid,btn){
  const reason=(($('reopen-note')||{}).value||'').trim();
  if(reason.length<10){toast('ต้องใส่เหตุผลอย่างน้อย 10 ตัวอักษร');const ta=$('reopen-note');if(ta)ta.focus();return;}
  const sid=DATA&&DATA.session&&DATA.session.session_id; if(!sid)return;
  await busy('status',btn,async()=>{
    try{
      await statusWithManagerCode(sid,()=>stForm('draft','',reason),'ใส่รหัสผู้จัดการเพื่อเปิดงวดใหม่:');
      closeSheet();toast('เปิดงวดใหม่แล้ว — แก้ไขได้ แล้วส่งตรวจ/ปิดเดือนอีกครั้ง');route();
    }catch(e){toast('ไม่สำเร็จ: '+e.message);hint403(e);}
  });
}
