/* recon frontend — 27-matcher.js: the one matcher side panel (drawer from the right; full screen on phones).
   Left = POS days of one lane (category · bank) with what is still unmatched, right = that lane's statement
   lines sorted by date distance + amount fit, middle = live difference meter + note + "จับคู่ N วัน ↔ M บรรทัด".
   POST/DELETE /api/manual (the server re-checks every amount and line). One state object: MX.
   Plain scripts sharing one global scope, loaded in order by index.html (00 → 90);
   a slice may call any other slice's functions, but top-level code only uses earlier slices. */
let MX=null;
const _MX_THM=['','ม.ค.','ก.พ.','มี.ค.','เม.ย.','พ.ค.','มิ.ย.','ก.ค.','ส.ค.','ก.ย.','ต.ค.','พ.ย.','ธ.ค.'];
const _mxD=s=>{const t=String(s||'');return t.length>=10?`${+t.slice(8,10)} ${_MX_THM[+t.slice(5,7)]||''}`:esc(t);};
const _mxDayNum=s=>{const [y,m,d]=String(s).slice(0,10).split('-').map(Number);return Date.UTC(y,m-1,d)/86400000;};
const _mxSign=v=>(v>0?'+':v<0?'−':'')+'฿'+fmt2(Math.abs(v));

/* เปิดตัวจับคู่ของช่องทาง key — opts.day = วันที่เปิดมาจาก (ติ๊กยอด POS วันนั้นไว้ให้), opts.bank = lane */
function mxOpen(key,opts){
  if(!DATA)return; opts=opts||{};
  const ch=(DATA.channels||[]).find(c=>c.key===key); if(!ch||key==='COUPON')return;
  const banks=[...new Set((ch.banks||[]).map(b=>b.bank).filter(Boolean))];
  if(!banks.length)banks.push('NONE');
  let bank=opts.bank&&banks.includes(opts.bank)?opts.bank:null;
  if(!bank&&opts.day){const d=(ch.days||[]).find(x=>x.day===opts.day);bank=((d&&d.banks)||[]).find(b=>banks.includes(b))||null;}
  if(!bank){const bs=(ch.banks||[]).slice().sort((a,b)=>Math.abs(b.variance||0)-Math.abs(a.variance||0));bank=(bs[0]&&bs[0].bank)||banks[0];}
  const ret=(MX&&MX.ret)||document.activeElement;
  MX={key,label:ch.label,banks,bank,day:opts.day||null,bid:DATA.branch_id,bname:DATA.branch_name,closed:!!DATA.closed,
      pos:[],ext:[],matches:[],posAvailable:true,tickP:{},tickE:new Set(),note:'',loading:true,err:'',seeded:false,ret};
  _mxMount();
  _mxLoad();
}
function mxBank(bank){
  const M=MX; if(!M||bank===M.bank)return;
  M.bank=bank; M.tickP={}; M.tickE=new Set(); M.seeded=false;
  _mxLoad();
}
function mxClose(){
  const o=$('mx-overlay');
  if(!MX&&(!o||o.hidden))return;
  const ret=MX&&MX.ret; MX=null;
  if(o)o.hidden=true;
  document.body.classList.remove('mx-lock');
  // focus back where the panel was opened from; after an in-place refresh that button is a new node
  const t=(ret&&ret.isConnected&&ret!==document.body)?ret
    :(document.querySelector('#ws-detail [onclick^="mxOpen"]')||$('ws-detail'));
  if(t&&t.focus)t.focus({preventScroll:true});
}
function _mxMount(){
  let o=$('mx-overlay');
  if(!o){
    o=document.createElement('div');o.id='mx-overlay';o.className='mx-overlay';o.hidden=true;
    o.innerHTML=`<div class="mx-backdrop" onclick="mxClose()"></div>
      <aside class="mx-panel" id="mx-panel" role="dialog" aria-modal="true" aria-labelledby="mx-title" tabindex="-1"><div id="mx-body" class="mx-inner"></div></aside>`;
    document.body.appendChild(o);
  }
  o.hidden=false; document.body.classList.add('mx-lock');
  _mxRender();
  const p=$('mx-panel'); if(p)p.focus();
}
async function _mxLoad(){
  const M=MX; if(!M)return;
  M.loading=true; M.err=''; _mxRender();
  let d;
  try{d=await api(`/api/manual?branch_id=${M.bid}&category=${encodeURIComponent(M.key)}&bank=${encodeURIComponent(M.bank)}&period_month=${MONTH}`);}
  catch(e){if(MX!==M)return;M.loading=false;M.err=e.message;_mxRender();return;}
  if(MX!==M)return;                 // closed / reopened on another lane while loading
  M.loading=false;
  M.posAvailable=d.pos_available!==false;
  M.pos=(d.pos_days||[]).map(p=>({day:p.day,total:+p.total||0,remaining:+p.remaining||0}));
  M.ext=(d.ext_lines||[]).map(l=>({...l,_k:l.table+':'+l.id}));
  M.matches=d.matches||[];
  // keep ticks that still make sense after a reload
  for(const k of Object.keys(M.tickP)){const p=M.pos.find(x=>x.day===k);
    if(!p||p.remaining<=0.5)delete M.tickP[k];else if(M.tickP[k]>p.remaining)M.tickP[k]=p.remaining;}
  for(const k of [...M.tickE]){const l=M.ext.find(x=>x._k===k);if(!l||_mxBlock(l))M.tickE.delete(k);}
  if(M.day&&!M.seeded){const p=M.pos.find(x=>x.day===M.day);if(p&&p.remaining>0.5)M.tickP[p.day]=p.remaining;}
  M.seeded=true;
  _mxRender();
}
// why a statement line cannot be ticked ('' = it can) — the server refuses these anyway
function _mxBlock(l){
  if(l.branch_id!=null&&+l.branch_id!==+MX.bid)return 'ของสาขาอื่น';
  if(l.match_status==='manual')return 'จับคู่ด้วยมือแล้ว';
  if(l.match_status==='excluded')return 'ตัดออกแล้ว — ไม่ใช่รายได้';
  if(l.match_status==='matched')return 'จับคู่อัตโนมัติแล้ว';
  return '';
}
const _mxOpenPos=()=>MX.pos.filter(p=>p.remaining>0.5);
const _mxId=l=>'mx-e-'+String(l.table||'').replace(/^recon_/,'').replace(/[^a-z_]/gi,'')+'-'+(+l.id);
function _mxSum(){
  const M=MX;
  const p=Object.values(M.tickP).reduce((a,v)=>a+(+v||0),0);
  const ls=M.ext.filter(l=>M.tickE.has(l._k));
  return {p,e:ls.reduce((a,l)=>a+(+l.amount||0),0),np:Object.keys(M.tickP).length,ne:ls.length};
}
function _mxSortedExt(){
  const M=MX, s=_mxSum(), need=s.p-s.e;
  const ref=(Object.keys(M.tickP).length?Object.keys(M.tickP):(M.day?[M.day]:[])).map(_mxDayNum);
  const dist=l=>ref.length?Math.min(...ref.map(r=>Math.abs(_mxDayNum(l.date)+(l.next_month?31:0)-r))):0;   // next-month window lines sit after day 31
  // score = days away + up to 3 "days" for a poor amount fit (an exact amount a few days off still ranks high)
  const fit=l=>need>0.5?Math.min(1,Math.abs(l.amount-need)/need):0;
  return M.ext.map(l=>({l,blk:_mxBlock(l),on:M.tickE.has(l._k),sc:dist(l)+3*fit(l),need}))
    .sort((a,b)=>(a.blk?1:0)-(b.blk?1:0)||(b.on?1:0)-(a.on?1:0)||a.sc-b.sc||String(a.l.date).localeCompare(String(b.l.date)));
}
function _mxPosHtml(){
  const M=MX, pos=_mxOpenPos();
  if(!M.posAvailable)return '<div class="muted pad-8-12">ยังไม่ได้ sync ยอด POS รายวัน — จับคู่ไม่ได้จนกว่าจะ sync</div>';
  if(!pos.length)return '<div class="muted pad-8-12">ไม่มียอด POS ค้างในช่องทางนี้</div>';
  return `<table class="mx-tbl"><thead><tr><th><span class="sr-only">เลือก</span></th><th class="tl">วัน</th><th>คงเหลือ</th><th>ใช้</th></tr></thead><tbody>${pos.map((p,j)=>{
    const on=Object.prototype.hasOwnProperty.call(M.tickP,p.day);
    return `<tr id="mx-pr-${j}"${on?' class="mx-on"':''}>
      <td><input type="checkbox" id="mx-p-${j}"${on?' checked':''}${M.closed?' disabled':''} onchange="mxTickP(${jsa(p.day)},this.checked)"></td>
      <td class="tl nowrap"><label for="mx-p-${j}">${_mxD(p.day)}</label>${p.day===M.day?' <span class="chip c-mut ov-mini">วันที่เปิดมา</span>':''}</td>
      <td class="mono">${fmt2(p.remaining)}</td>
      <td><input class="fee-in mx-amt" type="number" step="0.01" min="0" max="${p.remaining}" id="mx-pa-${j}"
        value="${on?M.tickP[p.day]:p.remaining}" aria-label="ยอดที่ใช้ของวันที่ ${+p.day.slice(8)}"${on&&!M.closed?'':' disabled'}
        oninput="mxAmt(${jsa(p.day)},this,false)" onchange="mxAmt(${jsa(p.day)},this,true)"></td></tr>`;}).join('')}</tbody></table>`;
}
function _mxExtHtml(){
  const M=MX, rows=_mxSortedExt();
  if(!rows.length)return '<div class="muted pad-8-12">ไม่มีบรรทัดเงินเข้าของช่องทางนี้ในเดือนนี้ (อัปโหลดเอกสารก่อน)</div>';
  return `<table class="mx-tbl"><thead><tr><th><span class="sr-only">เลือก</span></th><th class="tl">วันที่</th><th class="tl">รายละเอียด</th><th>ยอด</th></tr></thead><tbody>${rows.map(({l,blk,on,need})=>{
    const id=_mxId(l), fit=!blk&&need>0.5&&Math.abs(l.amount-need)<=1;
    return `<tr id="${id}-r" class="${blk?'mx-off':on?'mx-on':''}">
      <td>${blk?'':`<input type="checkbox" id="${id}"${on?' checked':''}${M.closed?' disabled':''} onchange="mxTickE(${jsa(l._k)},this.checked)">`}</td>
      <td class="tl nowrap">${blk?_mxD(l.date):`<label for="${id}">${_mxD(l.date)}</label>`}</td>
      <td class="tl mx-ref"><span class="mx-ref-t" title="${esc(l.ref||'')}">${esc(l.ref||l.detail||'—')}</span>
        ${l.next_month?'<span class="chip c-mut ov-mini">เดือนถัดไป</span>':''}
        ${blk?`<span class="chip c-mut ov-mini">${esc(blk)}</span>`:fit?'<span class="chip c-ok ov-mini">ยอดพอดี</span>':''}</td>
      <td class="mono">${fmt2(l.amount)}</td></tr>`;}).join('')}</tbody></table>`;
}
function _mxMeterHtml(){
  const {p,e,np,ne}=_mxSum(), d=p-e;
  const st=(!np||!ne)?'none':Math.abs(d)<=1?'ok':'bad';
  const mx=Math.max(p,e,1);
  return `<div class="mx-meter-row"><span>POS ที่เลือก (${np} วัน)</span><b class="mono">฿${fmt2(p)}</b></div>
    <div class="mx-bar" aria-hidden="true"><span class="mx-bar-p" style="width:${(p/mx*100).toFixed(1)}%"></span></div>
    <div class="mx-meter-row"><span>เงินเข้าที่เลือก (${ne} บรรทัด)</span><b class="mono">฿${fmt2(e)}</b></div>
    <div class="mx-bar" aria-hidden="true"><span class="mx-bar-e" style="width:${(e/mx*100).toFixed(1)}%"></span></div>
    <div class="mx-diff ${st}">${st==='none'?'เลือกทั้งสองฝั่ง':st==='ok'?'ตรงกัน ✓ ต่าง '+_mxSign(d):'ต่าง '+_mxSign(d)}</div>
    <div class="lbl tc">ต่าง = POS ที่เลือก − เงินเข้าที่เลือก · เขียวเมื่อห่างไม่เกิน ฿1</div>`;
}
function _mxGoText(){const s=_mxSum();return `จับคู่ ${s.np} วัน ↔ ${s.ne} บรรทัด`;}
function _mxUpdate(){
  const m=$('mx-meter'); if(m)m.innerHTML=_mxMeterHtml();
  const g=$('mx-go'); if(g){const s=_mxSum();g.disabled=!(s.np&&s.ne)||!(s.p>0);g.textContent=_mxGoText();}
  const live=$('mx-live'); if(live){const s=_mxSum(),d=s.p-s.e;live.textContent=s.np&&s.ne?(Math.abs(d)<=1?'ตรงกัน':'ต่าง '+_mxSign(d)):'';}
}
function _mxRender(){
  const M=MX, b=$('mx-body'); if(!M||!b)return;
  const tabs=M.banks.length>1?`<div class="mx-banks" role="group" aria-label="เลือกธนาคาร / ผู้รับบัตร">${M.banks.map(x=>
    `<button class="btn btn-sm${x===M.bank?' btn-pri':''}" aria-pressed="${x===M.bank}" onclick="mxBank(${jsa(x)})">${esc(x)}</button>`).join('')}</div>`:'';
  const head=`<header class="mx-h"><div class="fx-1 mx-h-t"><div class="lbl">ตัวจับคู่ · ${esc(M.bname)} · ${esc(monthTh(MONTH))}</div>
      <h2 id="mx-title">${esc(M.label)} · ${esc(M.bank)}</h2></div>${tabs}
      <button class="btn btn-sm" onclick="mxClose()" aria-label="ปิดตัวจับคู่ (Esc)"><i class="ti ti-x" aria-hidden="true"></i> ปิด</button></header>`;
  if(M.loading){b.innerHTML=head+`<div class="mx-area">${loadingState()}</div>`;return;}
  if(M.err){b.innerHTML=head+`<div class="mx-area"><div class="card c-err">โหลดไม่สำเร็จ: ${esc(M.err)}</div></div>`;return;}
  const s=_mxSum();
  const mid=M.closed
    ?`<div class="muted">เดือนนี้ปิดแล้ว — ดูได้อย่างเดียว</div>`
    :`<label class="lbl" for="mx-note">หมายเหตุ (ติดไปในทะเบียนการปรับปรุงด้วยมือ)</label>
      <textarea id="mx-note" class="sheet-ta mx-note" rows="2" placeholder="เช่น เงินเข้ารวม 2 วัน" oninput="if(MX)MX.note=this.value">${esc(M.note)}</textarea>
      <button class="btn btn-pri w-full" id="mx-go"${(s.np&&s.ne&&s.p>0)?'':' disabled'} onclick="mxSubmit(this)">${_mxGoText()}</button>`;
  const matches=M.matches.length?M.matches.map(m=>`<div class="mx-match">
      <i class="ti ti-link" aria-hidden="true"></i>
      <span class="fx-1"><b class="mono">POS ฿${fmt2(m.pos_amount)}</b> ↔ <b class="mono">เงินเข้า ฿${fmt2(m.ext_amount)}</b>
        <span class="lbl">วันที่ ${(m.pos_days||[]).map(x=>+String(x.day).slice(8)).join(', ')} · ${(m.lines||[]).length} บรรทัด${m.note?' · '+esc(m.note):''}${m.matched_by?' · '+esc(m.matched_by):''}</span></span>
      ${M.closed?'':`<button class="btn btn-sm" onclick="mxUndo(${+m.match_id},this)">ยกเลิก</button>`}</div>`).join('')
    :'<div class="muted">ยังไม่มีคู่ที่จับเองในช่องทางนี้</div>';
  b.innerHTML=head+`<div class="mx-area">
    <div class="mx-grid">
      <section class="mx-col mx-pos" aria-labelledby="mx-pos-h"><h3 id="mx-pos-h">① ยอด POS ที่ยังไม่จับคู่</h3>
        <div class="lbl mb-4">ติ๊กวัน — แก้ยอดที่ใช้ได้ถ้าจับแค่บางส่วน</div><div class="mx-scroll" id="mx-pos-list">${_mxPosHtml()}</div></section>
      <section class="mx-mid" aria-label="ผลต่างของที่เลือก">
        <div id="mx-meter">${_mxMeterHtml()}</div>${mid}
        <span class="sr-only" id="mx-live" role="status" aria-live="polite"></span></section>
      <section class="mx-col mx-ext" aria-labelledby="mx-ext-h"><h3 id="mx-ext-h">② บรรทัดเงินเข้า</h3>
        <div class="lbl mb-4">เรียงวันที่ใกล้ + ยอดที่พอดีก่อน · บรรทัดสีจางเลือกไม่ได้ (บอกเหตุผลไว้)</div><div class="mx-scroll" id="mx-ext-list">${_mxExtHtml()}</div></section>
    </div>
    <section class="mx-done" aria-labelledby="mx-done-h"><h3 id="mx-done-h">คู่ที่จับเองแล้วในช่องทางนี้ (${M.matches.length})</h3>${matches}</section>
  </div>`;
}
function mxTickP(day,on){
  const M=MX; if(!M||M.closed)return;
  const pos=_mxOpenPos(), j=pos.findIndex(x=>x.day===day); if(j<0)return;
  if(on)M.tickP[day]=pos[j].remaining;else delete M.tickP[day];
  const row=$('mx-pr-'+j), inp=$('mx-pa-'+j);
  if(row)row.classList.toggle('mx-on',on);
  if(inp){inp.disabled=!on;inp.value=pos[j].remaining;}
  const el=$('mx-ext-list'); if(el)el.innerHTML=_mxExtHtml();   // re-sort lines around the ticked days
  _mxUpdate();
}
function mxAmt(day,inp,commit){
  const M=MX; if(!M)return;
  const p=_mxOpenPos().find(x=>x.day===day); if(!p||!Object.prototype.hasOwnProperty.call(M.tickP,day))return;
  let v=parseFloat(inp.value); if(isNaN(v)||v<0)v=0; if(v>p.remaining)v=p.remaining;
  M.tickP[day]=Math.round(v*100)/100;
  if(commit)inp.value=M.tickP[day];
  _mxUpdate();
}
function mxTickE(k,on){
  const M=MX; if(!M||M.closed)return;
  const l=M.ext.find(x=>x._k===k); if(!l||_mxBlock(l))return;
  if(on)M.tickE.add(k);else M.tickE.delete(k);
  const r=$(_mxId(l)+'-r'); if(r)r.classList.toggle('mx-on',on);
  _mxUpdate();
}
async function mxSubmit(btn){
  const M=MX; if(!M||M.closed)return;
  const pos=Object.entries(M.tickP).filter(([,v])=>v>0).map(([day,amount])=>({day,amount:Math.round(amount*100)/100}));
  const ext=M.ext.filter(l=>M.tickE.has(l._k)&&!_mxBlock(l)).map(l=>({table:l.table,id:l.id,amount:l.amount}));
  if(!pos.length||!ext.length)return;
  const note=(M.note||'').trim()||'จับคู่ด้วยตัวจับคู่';
  await busy('mx',btn,async()=>{
    try{
      await api('/api/manual',{method:'POST',body:{branch_id:M.bid,period_month:MONTH,category:M.key,bank:M.bank,
        pos_days:pos,ext:ext,note:note}});
      toast(`จับคู่แล้ว ${pos.length} วัน ↔ ${ext.length} บรรทัด`);
      if(MX!==M)return;
      M.tickP={}; M.tickE=new Set(); M.note='';
      await _mxLoad();
      wsRefresh();
    }catch(e){toast('ไม่สำเร็จ: '+e.message);hint403(e);}
  });
}
async function mxUndo(mid,btn){
  const M=MX; if(!M||M.closed)return;
  if(!confirm('ยกเลิกการจับคู่นี้? (บรรทัดเงินเข้าจะกลับไปเป็น "ยังไม่จับคู่")'))return;
  await busy('mx-undo-'+mid,btn,async()=>{
    try{await api(`/api/manual/${mid}`,{method:'DELETE'});toast('ยกเลิกการจับคู่แล้ว');
      if(MX!==M)return;
      await _mxLoad(); wsRefresh();}
    catch(e){toast('ไม่สำเร็จ: '+e.message);hint403(e);}
  });
}
// Esc closes · Tab stays inside the panel while it is open
document.addEventListener('keydown',e=>{
  if(!MX)return;
  if(e.key==='Escape'){e.preventDefault();mxClose();return;}
  if(e.key!=='Tab')return;
  const p=$('mx-panel'); if(!p)return;
  const f=[...p.querySelectorAll('button:not([disabled]),input:not([disabled]),textarea:not([disabled]),a[href],select:not([disabled])')]
    .filter(x=>x.offsetParent!==null);
  if(!f.length)return;
  const first=f[0], last=f[f.length-1], a=document.activeElement;
  if(e.shiftKey&&(a===first||a===p||!p.contains(a))){e.preventDefault();last.focus();}
  else if(!e.shiftKey&&(a===last||!p.contains(a))){e.preventDefault();first.focus();}
});
