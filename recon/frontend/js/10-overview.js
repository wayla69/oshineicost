/* recon frontend — 10-overview.js: #branches (and the landing for viewers / no login) — chain overview (the inbox of every branch).
   Plain scripts sharing one global scope, loaded in order by index.html (00 → 90);
   a slice may call any other slice's functions, but top-level code only uses earlier slices. */
/* ── ภาพรวมทุกสาขา (กล่องงานทั้งเครือ) ── */
async function loadOverview(){
  const t=++_ROUTE_SEQ;
  $('app').innerHTML=header()+loadingState();
  let ov;try{ov=await api(`/api/v2/overview?month=${MONTH}`);}
  catch(e){if(_cur(t))$('app').innerHTML=header()+`<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`;return;}
  if(!_cur(t))return;

  const branches=(ov.branches||[]).slice();
  const totalTodo=branches.reduce((a,b)=>a+(b.todo_count||0),0);   // งานที่บล็อกการปิดเดือน (reclass เป็นคำแนะนำ ไม่บล็อก)
  const _w=b=>Math.max(b.pos_total||0,b.ext_total||0,0);           // ถ่วงน้ำหนักด้วยฝั่งที่ใหญ่กว่า (ตรงกับ backend)
  const wsum=branches.reduce((a,b)=>a+_w(b),0);
  const chainPct=branches.length
      ? Math.round( wsum>0
          ? branches.reduce((a,b)=>a+(b.progress_pct||0)*_w(b),0)/wsum
          : branches.reduce((a,b)=>a+(b.progress_pct||0),0)/branches.length )
      : 100;
  const rcCount=ov.reclass_count_total||0, rcAmt=ov.reclass_amount_total||0;
  const open=branches.filter(b=>branchOpen(b)>0)
      .sort((a,b)=>branchOpen(b)-branchOpen(a) || (a.progress_pct||0)-(b.progress_pct||0));
  const done=branches.filter(b=>branchOpen(b)===0 && b.status!=='in_review')   // in_review ไม่ใช่ "เรียบร้อย" — อยู่ใน review-bar
      .sort((a,b)=>a.branch_name.localeCompare(b.branch_name,'th'));
  const review=branches.filter(b=>b.status==='in_review');

  let html=header();

  // hero line + chain progress (guard the empty-DB case: no ✓/100% over zero data)
  // UX phase 2: the Close Board (#close) is the matrix view of the same month — one tap away
  const cbLink=`<a href="#close" class="ov-cb-link"><i class="ti ti-calendar-check" aria-hidden="true"></i> Close Board — ตารางปิดเดือนทุกสาขา ›</a>`;
  if(!branches.length){
    html+=`<div class="card ov-hero mb-12"><div class="ov-hero-line">เดือน <b>${esc(monthTh(ov.period_month))}</b></div>${cbLink}</div>`;
  } else {
    html+=`<div class="card ov-hero mb-12">
      ${cbLink}
      <div class="ov-hero-line">เดือน <b>${esc(monthTh(ov.period_month))}</b>
        ${totalTodo
          ? `· เหลือ <b>${totalTodo}</b> เรื่อง ก่อนปิดได้ทั้งเครือ`
          : `· <span class="chip c-ok">✓ ครบทุกสาขา พร้อมปิดทั้งเครือ</span>`}</div>
      <div class="row" style="flex-wrap:nowrap;margin-top:9px">
        <div class="prog ${progCls(chainPct)}"><div style="width:${chainPct}%"></div></div>
        <span class="lbl mono" style="min-width:40px">${chainPct}%</span>
      </div></div>`;
  }

  // Phase-4 #1: manager attention bar (links into the in_review branches)
  if(review.length){
    html+=`<div class="card review-bar mb-12">
      <span class="chip c-warn"><i class="ti ti-hourglass-high" aria-hidden="true"></i> รอฉันตรวจ ${review.length}</span>
      <span class="muted">สาขาที่ส่งตรวจแล้ว รอผู้จัดการปิดเดือน —</span>
      ${review.map(b=>`<a href="#" class="rb-link" onclick="location.hash='b/${b.branch_id}';return false">${esc(b.branch_name)}</a>`).join('')}
    </div>`;
  }

  // chain reclass banner
  if(rcCount>0){
    html+=`<div class="card ov-reclass mb-12">
      <i class="ti ti-arrows-shuffle" aria-hidden="true"></i> เจอเงินที่น่าจะ<b>อยู่ผิดช่อง ${rcCount} คู่</b> (รวม ฿${fmt(rcAmt)})
      <span class="lbl"> — เปิดสาขาที่เกี่ยวข้องเพื่อยืนยันย้ายให้ถูกช่อง (กดเดียวจบ)</span></div>`;
  }

  // open branches (list, not table)
  if(open.length){
    html+=`<div class="card p-0 mb-12">`;
    const chLabels=ov.channel_labels||{};
    for(const b of open){
      const rc=b.reclass_count||0;
      // คลิกช่องทางที่ยังไม่ตรง → เด้งเข้าสาขา + เปิดหลักฐานช่องนั้น (goChannel)
      const probCh=Object.entries(b.channels||{})
        .filter(([k,c])=>['diff','waiting_doc','setup'].includes(c.status));
      const chChips=probCh.map(([k,c])=>{
        const tip=c.status==='waiting_doc'?'รอ/ขาดเอกสาร':c.status==='setup'?'ต้องตั้งค่า':'มีผลต่าง';
        return `<span class="chip c-warn ov-mini pointer" role="button" tabindex="0"
          onclick="event.stopPropagation();goChannel(${b.branch_id},${jsa(k)})"
          title="${tip} — กดเพื่อดูช่องทางนี้ในสาขา">${esc(chLabels[k]||k)} ›</span>`;}).join(' ');
      html+=`<div class="ov-row" role="button" tabindex="0" onclick="location.hash='b/${b.branch_id}'">
        <div class="ov-row-main">
          <div class="ov-bname">${esc(b.branch_name)}
            ${b.status==='in_review'?'<span class="chip c-warn ov-mini">รอตรวจ</span>':''}</div>
          <div class="ov-sum">${esc(openSummary(b))}
            ${rc>0?'<span class="chip c-warn ov-mini"><i class="ti ti-arrows-shuffle" aria-hidden="true"></i> ผิดช่อง '+rc+'</span>':''}</div>
          ${chChips?`<div class="ov-sum" style="margin-top:4px;gap:5px">${chChips}</div>`:''}
        </div>
        <div class="row" style="flex-wrap:nowrap;flex:none;width:150px">
          <div class="prog ${progCls(b.progress_pct)}"><div style="width:${b.progress_pct}%"></div></div>
          <span class="lbl mono" style="min-width:34px">${b.progress_pct}%</span>
        </div>
        <span class="ov-chev">›</span>
      </div>`;
    }
    html+=`</div>`;
  } else if(branches.length){
    html+=`<div class="card c-ok mb-12 bg-ok">
      ✓ ทุกสาขากระทบยอดครบแล้วสำหรับเดือนนี้ — พร้อมให้ผู้จัดการปิดเดือน</div>`;
  } else {
    html+=emptyState('building-store','ยังไม่มีข้อมูลสาขาสำหรับเดือนนี้','เลือกเดือนอื่น หรือซิงก์ข้อมูล POS ของเดือนนี้ก่อน');
  }

  // done branches — collapsed section
  if(done.length){
    html+=`<div class="card p-0">
      <a href="#" class="ov-done-toggle" onclick="toggleDone();return false" id="ov-done-toggle">
        สาขาที่เรียบร้อยแล้ว (${done.length}) <span id="ov-done-caret">▾</span></a>
      <div id="ov-done" style="display:none">`;
    for(const b of done){
      html+=`<div class="ov-row ov-row-done" role="button" tabindex="0" onclick="location.hash='b/${b.branch_id}'">
        <div class="ov-row-main">
          <div class="ov-bname">${esc(b.branch_name)}</div>
          <div class="ov-sum"><span class="chip c-ok ov-mini">✓ ${esc(SES_ST[b.status]||'ครบ')}</span></div>
        </div>
        <span class="ov-chev">›</span>
      </div>`;
    }
    html+=`</div></div>`;
  }

  $('app').innerHTML=html;
  checkReclassPattern(t);
}
// B: warn (manager) when reclass suggestions look systematic (a mis-mapped statement
// code) rather than scattered human error — the SDCH fingerprint. Non-blocking.
async function checkReclassPattern(t){
  let d;try{d=await api(`/api/diagnostics/reclass-pattern?month=${MONTH}`);}catch(e){return;}
  if(!_cur(t))return;              // ผู้ใช้ออกจากหน้าภาพรวมไปแล้ว — ห้ามแปะแถบลงหน้าอื่น
  if(!d||!d.warning)return;
  const app=$('app');if(!app)return;
  const bar=document.createElement('div');
  bar.className='card c-warn';bar.style.cssText='margin-bottom:12px;border:1px solid var(--warn)';
  bar.innerHTML=`<div class="row ai-start gap-8">
    <span class="fs-18"><i class="ti ti-search" aria-hidden="true"></i></span>
    <div><b>รูปแบบน่าสงสัย — อาจไม่ใช่คนกดผิด</b>
      <div class="lbl mt-2 lh-15">${esc(d.warning)}</div>
      <a href="/api/statement-codes.html" target="_blank" class="lbl fw-600">→ เปิดพจนานุกรมรหัสในสเตทเมนต์</a></div>
  </div>`;
  // insert just under the header (first child after the header row)
  const hdr=app.querySelector('.row');
  if(hdr&&hdr.nextSibling)app.insertBefore(bar,hdr.nextSibling);else app.insertBefore(bar,app.firstChild);
}
function toggleDone(){
  const e=$('ov-done'),c=$('ov-done-caret');
  if(!e)return;
  const show=e.style.display==='none';
  e.style.display=show?'':'none';
  if(c)c.textContent=show?'▴':'▾';
}
