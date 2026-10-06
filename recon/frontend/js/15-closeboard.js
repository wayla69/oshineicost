/* recon frontend — 15-closeboard.js: #close — the Close Board: every branch × channel for one month, the
   close deadline, and four KPIs. Landing page for accountants and managers (see _landing in 00-core).
   Plain scripts sharing one global scope, loaded in order by index.html (00 → 90);
   a slice may call any other slice's functions, but top-level code only uses earlier slices. */

// คอลัมน์ของตาราง = ช่องทาง POS (ชื่อสั้นให้พอดีจอ) · ลำดับเดียวกับ backend CHANNELS
const BOARD_COLS=[['QR','QR'],['CASH','เงินสด'],['TRANSFER','เงินโอน'],['CREDIT','บัตร'],
                  ['GRAB','Grab'],['LINEMAN','Line Man'],['COUPON','คูปอง']];

/* ── กำหนดปิดเดือน: เดือน M ต้องปิดภายในวันที่ CLOSE_DAY ของเดือน M+1 (วันที่ตามเวลาไทย) — owner 2026-10-06: วันที่ 5 ── */
const CLOSE_DAY=5;
function _bkkToday(){
  try{return new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Bangkok',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());}
  catch(e){return ymdLocal();}
}
function closeDeadline(ym){
  let y=+ym.slice(0,4), m=+ym.slice(5,7)+1;
  if(m>12){m=1;y++;}
  return `${y}-${String(m).padStart(2,'0')}-${String(CLOSE_DAY).padStart(2,'0')}`;
}
function _daysBetween(a,b){       // b − a in whole days (YYYY-MM-DD, calendar dates — no clock, no DST)
  const [y1,m1,d1]=a.split('-').map(Number),[y2,m2,d2]=b.split('-').map(Number);
  return Math.round((Date.UTC(y2,m2-1,d2)-Date.UTC(y1,m1-1,d1))/86400000);
}
// {cls,text,overdue,days}: chip class + "กำหนดปิด 5 พฤศจิกายน 2569 (อีก 7 วัน)" — red once past CLOSE_DAY
function deadlineInfo(ym,allClosed){
  const dl=closeDeadline(ym), n=_daysBetween(_bkkToday(),dl);
  const dTh=`${CLOSE_DAY} ${TH_MONTHS[+dl.slice(5,7)]} ${+dl.slice(0,4)+543}`;
  if(allClosed)return {cls:'c-ok',text:`กำหนดปิด ${dTh} · ปิดครบแล้ว`,overdue:false,days:n};
  if(n<0)return {cls:'c-err',text:`กำหนดปิด ${dTh} (เกิน ${-n} วัน)`,overdue:true,days:n};
  if(n===0)return {cls:'c-warn',text:`กำหนดปิด ${dTh} (วันนี้วันสุดท้าย)`,overdue:false,days:n};
  return {cls:n<=5?'c-warn':'c-mut',text:`กำหนดปิด ${dTh} (อีก ${n} วัน)`,overdue:false,days:n};
}
const _sgn=v=>(v>0?'+':v<0?'−':'')+'฿'+fmt(Math.abs(v));      // −฿1,200 / +฿300 (true minus sign)

/* one matrix cell → a link into the branch workspace on that channel */
function _cbCell(b,key,label){
  const c=(b.channels||{})[key];
  const href=`#b/${b.branch_id}/${key}`;
  const nm=esc(b.branch_name)+' · '+esc(label);
  if(!c)return `<td><a class="cb-cell cb-none" href="${href}" aria-label="${nm}: ไม่มีข้อมูล" title="ไม่มียอดช่องทางนี้">—</a></td>`;
  let cls,txt,tip;
  if(c.accepted){cls='cb-acc';txt='✓ รับทราบ';tip='รับทราบผลต่างพร้อมเหตุผลแล้ว';}
  else if(c.status==='diff'){const o=+c.open_amount||0;cls='cb-diff';
    // days that differ but net to ~0 over the month are still open — say so instead of "฿0"
    txt=Math.abs(o)>=1?_sgn(o):'ต่างรายวัน';tip='ยังอธิบายไม่ได้ (หลังหักวันที่รับทราบแล้ว และไม่นับเงินที่รอเข้าตามรอบ)';}
  else if(c.status==='waiting_doc'&&c.missing_doc){cls='cb-miss';txt='ขาด '+esc(c.missing_doc);tip='ยังไม่ได้อัปโหลด / มีไม่ครบเดือน';}
  else if(c.status==='waiting_doc'){cls='cb-wait';txt='รอรอบ';tip=c.waiting?`รอเงินเข้าตามรอบ ฿${fmt(c.waiting)}`:'รอเงินเข้าตามรอบ';}
  else if(c.status==='setup'){cls='cb-setup';txt='ตั้งค่า';tip='รหัสชำระใหม่ ต้องระบุธนาคาร';}
  else if(c.status==='no_source'){cls='cb-none';txt='—';tip='ไม่ต้องตรวจ (ไม่มีเอกสารให้เทียบ)';}
  else {cls='cb-ok';txt='✓';tip=Math.abs(c.variance||0)>0.5&&key!=='COUPON'?`ตรง (ต่างเล็กน้อย ${_sgn(c.variance)})`:'ตรงกันแล้ว';}
  return `<td><a class="cb-cell ${cls}" href="${href}" aria-label="${nm}: ${esc(tip)}" title="${esc(tip)}">${txt}</a></td>`;
}
function _cbStatus(b){
  const href=`#b/${b.branch_id}/CLOSE`;
  if(isClosedSt(b.status)){
    const when=(b.approved_at||'').slice(0,10);
    return `<td class="cb-st"><a class="cb-st-link" href="${href}"><span class="chip c-ok">ปิดแล้ว</span>
      <span class="lbl">${b.approved_by?'โดย '+esc(b.approved_by):''}${when?' · '+esc(when):''}</span></a></td>`;
  }
  if(b.status==='in_review')return `<td class="cb-st"><a class="cb-st-link" href="${href}"><span class="chip c-warn">รอผู้จัดการ</span>
      <span class="lbl mono">${b.progress_pct}%</span></a></td>`;
  return `<td class="cb-st"><a class="cb-st-link" href="${href}"><span class="chip c-mut">กำลังทำ</span>
      <div class="prog cb-prog ${progCls(b.progress_pct)}"><div style="width:${b.progress_pct}%"></div></div>
      <span class="lbl mono">${b.progress_pct}%</span></a></td>`;
}

async function loadCloseBoard(){
  const t=++_ROUTE_SEQ;
  $('app').innerHTML=header()+loadingState();
  let ov;try{ov=await api(`/api/v2/overview?month=${MONTH}`);}
  catch(e){if(_cur(t))$('app').innerHTML=header()+`<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`;return;}
  if(!_cur(t))return;

  const branches=ov.branches||[];
  const open=branches.filter(b=>!isClosedSt(b.status));          // KPI: เฉพาะสาขาที่ยังไม่ปิด
  const nClosed=branches.length-open.length, nReview=branches.filter(b=>b.status==='in_review').length;
  const unexplained=open.reduce((a,b)=>a+(b.unexplained_total||0),0);
  const waiting=open.reduce((a,b)=>a+(b.waiting_total||0),0);
  const docs=open.flatMap(b=>b.missing_docs||[]);
  const byLabel={};docs.forEach(d=>{byLabel[d.label]=(byLabel[d.label]||0)+1;});
  const docBreak=Object.entries(byLabel).sort((a,b)=>b[1]-a[1]).map(([l,n])=>`${esc(l)} ${n}`).join(' · ');
  const nUnexpl=open.filter(b=>(b.unexplained_total||0)>0.5).length;
  const showPayout=branches.some(b=>(b.payout_issue_count||0)>0);
  const dl=deadlineInfo(ov.period_month||MONTH,branches.length>0&&!open.length);

  let html=header();
  html+=`<div class="card cb-head">
    <div class="cb-title"><div class="lbl">Close Board · ปิดเดือน</div>
      <h2>เดือน ${esc(monthTh(ov.period_month||MONTH))}</h2></div>
    <span class="chip ${dl.cls}" id="cb-deadline"><i class="ti ti-calendar-due" aria-hidden="true"></i> ${esc(dl.text)}</span>
    <a href="#branches" class="cb-alt ml-auto">ดูรายการสาขา ›</a>
  </div>`;

  if(!branches.length){
    $('app').innerHTML=html+emptyState('building-store','ยังไม่มีข้อมูลสาขาสำหรับเดือนนี้','เลือกเดือนอื่น หรือซิงก์ข้อมูล POS ของเดือนนี้ก่อน');
    return;
  }

  html+=`<div class="cb-kpis">
    <div class="card cb-kpi"><div class="lbl">ยังอธิบายไม่ได้</div>
      <div class="kv ${unexplained>0.5?'t-err':'t-ok'}">฿${fmt(unexplained)}</div>
      <div class="kd">${nUnexpl?`${nUnexpl} สาขา · ผลต่างที่ยังไม่รับทราบ + ยอดโอนที่ไม่ตรง`:'ไม่มีผลต่างค้าง'}</div></div>
    <div class="card cb-kpi"><div class="lbl">เอกสารที่ยังขาด</div>
      <div class="kv ${docs.length?'t-warn':'t-ok'}">${docs.length} ไฟล์</div>
      <div class="kd">${docBreak||'ครบทุกสาขา'}</div></div>
    <div class="card cb-kpi"><div class="lbl">รอเงินเข้าตามรอบ</div>
      <div class="kv">฿${fmt(waiting)}</div>
      <div class="kd">ยอด POS ของวันที่เงินยังไม่ถึงรอบโอน — ไม่ต้องทำอะไร</div></div>
    <div class="card cb-kpi"><div class="lbl">ปิดแล้ว</div>
      <div class="kv">${nClosed} / ${branches.length}</div>
      <div class="kd">${nReview?`รอผู้จัดการตรวจ ${nReview} สาขา`:'สาขาที่ผู้จัดการปิดเดือนแล้ว'}</div></div>
  </div>`;

  const head=BOARD_COLS.map(([,l])=>`<th scope="col">${esc(l)}</th>`).join('')
    +(showPayout?'<th scope="col" title="ยอดสุทธิในรายงาน Grab / Line Man / บัตร ↔ เงินที่โอนเข้าธนาคาร">ยอดโอน</th>':'')
    +'<th scope="col" class="cb-st">สถานะ</th>';
  const rows=branches.map(b=>{
    let pc='';
    if(showPayout){
      const href=`#b/${b.branch_id}/PAYOUT`, nm=esc(b.branch_name)+' · ยอดโอน';
      pc=(b.payout_open_count||0)>0
        ?`<td><a class="cb-cell cb-diff" href="${href}" aria-label="${nm}: ยอดโอนไม่ตรงรายงาน" title="ยอดโอนไม่ตรงรายงาน ${b.payout_open_count} รายการ">−฿${fmt(b.payout_open_total)}</a></td>`
        :(b.payout_issue_count||0)>0
          ?`<td><a class="cb-cell cb-acc" href="${href}" aria-label="${nm}: รับทราบแล้ว" title="รับทราบพร้อมเหตุผลแล้ว">✓ รับทราบ</a></td>`
          :`<td><span class="cb-cell cb-none" title="ไม่มียอดโอนที่ไม่ตรง">—</span></td>`;
    }
    return `<tr><th scope="row"><a href="#b/${b.branch_id}" class="cb-bname">${esc(b.branch_name)}</a></th>
      ${BOARD_COLS.map(([k,l])=>_cbCell(b,k,l)).join('')}${pc}${_cbStatus(b)}</tr>`;
  }).join('');
  html+=`<div class="card p-0 cb-card">
    <div class="cb-scroll"><table class="cb-tbl">
      <caption class="cb-cap">ตารางปิดเดือน — แถว = สาขา · คอลัมน์ = ช่องทาง · กดช่องเพื่อเปิดสาขาที่ช่องทางนั้น</caption>
      <thead><tr><th scope="col">สาขา</th>${head}</tr></thead>
      <tbody>${rows}</tbody></table></div>
    <div class="cb-legend">
      <span><span class="cb-cell cb-ok cb-key">✓</span> ตรง / รับทราบแล้ว</span>
      <span><span class="cb-cell cb-diff cb-key">−฿</span> ยังอธิบายไม่ได้</span>
      <span><span class="cb-cell cb-miss cb-key">ขาด</span> เอกสารยังไม่ครบ</span>
      <span><span class="cb-cell cb-wait cb-key">รอรอบ</span> รอเงินเข้าตามรอบ</span>
      <span><span class="cb-cell cb-none cb-key">—</span> ไม่มีข้อมูล</span>
    </div></div>`;
  $('app').innerHTML=html;
}
