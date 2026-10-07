/* recon frontend — 20-branch.js: #b/<id>[/<KEY>] — the branch month workspace: sticky header + reports menu,
   stepper 1–4, split view (left = work list, right = detail of the selected item); documents/upload, accept, move, reclass.
   Plain scripts sharing one global scope, loaded in order by index.html (00 → 90);
   a slice may call any other slice's functions, but top-level code only uses earlier slices. */
/* ── หน้าสาขา = พื้นที่ทำงานของเดือน (UX phase 2) ──
   ซ้าย: รายการงาน 4 กลุ่ม (ต้องจัดการ / รอเงินเข้าตามรอบ / ตรงแล้ว / อื่น ๆ) · ขวา: รายละเอียดของรายการที่เลือก
   deep link #b/<id>/<KEY> — KEY = รหัสช่องทาง (QR CASH TRANSFER CREDIT GRAB LINEMAN COUPON)
   หรือ DOCS PAYOUT SETUP RECLASS XFER NONREV MOVES CLOSE · ไม่มี KEY = รายการแรกที่ต้องจัดการ */
function _uploadChKeys(r){      // channel keys that have a genuine missing-doc upload task
  const keys=new Set();
  (r.todo||[]).forEach(t=>{ if(t.type==='upload' && t.channel) keys.add(t.channel); });
  return keys;                  // upload todos always carry .channel (reconcile_v2)
}
// banks whose whole statement is missing (stmt docs carry channel=None, so _uploadChKeys misses them)
function _missStmtBanks(r){
  return new Set((r.documents||[]).filter(d=>d.source==='bank'&&d.status==='missing').map(d=>d.provider));
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
    return 'waiting';                                     // in-transit on schedule → timing group
  }
  // COUPON variance is loyalty points, not baht — never file it as ฿ "coin residue"
  if(ch.key!=='COUPON' && Math.abs(ch.variance)>0 && Math.abs(ch.variance)<=COIN_TOL) return 'coin';
  return 'green';
}
// a stored UTC stamp ('YYYY-MM-DD HH:MM:SS' or ISO 'T') → Bangkok 'YYYY-MM-DD HH:MM'
function tsBkk(ts){
  if(!ts)return '';
  const d=new Date(String(ts).replace(' ','T').slice(0,19)+'Z');
  if(isNaN(d))return String(ts).slice(0,16);
  const b=new Date(d.getTime()+7*3600e3);
  return b.toISOString().slice(0,16).replace('T',' ');
}
const _baht=v=>(v<0?'−฿':'฿')+fmt(Math.abs(v));       // ฿1,200 / −฿300 (true minus sign)
// money still due on the next cycle: POS − money-in over the days still 'waiting' (not acked)
const _chPend=ch=>(ch.days||[]).filter(d=>d.status==='waiting'&&!d.ack).reduce((a,d)=>a+d.pos-d.ext,0);
// what is still unexplained on a channel (same rule as the Close Board's open_amount): the residual after
// per-day acks, less the money in transit (COUPON: the points gap)
const _chOpen=ch=>ch.key==='COUPON'?ch.variance:((ch.residual!=null?ch.residual:ch.variance)+_chPend(ch));

/* ── รายการงานด้านซ้าย ── */
let WS={sel:null,items:[],openDay:null};
const WS_GROUPS=[['act','ต้องจัดการ'],['wait','รอเงินเข้าตามรอบ'],['ok','ตรงแล้ว'],['more','อื่น ๆ']];
const _WS_RANK={act:{docs:0,setup:1,ch:2,payout:3,reclass:4,xfer:5},ok:{ch:0,payout:1,docs:2}};
function _chLine(ch,b){
  if(ch.accepted)return 'รับทราบ: '+(ch.accepted.note||'');
  if(ch.key==='COUPON')return ch.detail||'แต้ม KTC / BAY';
  if(ch.status==='diff'){const o=_chOpen(ch);
    return (Math.abs(o)>=1?`ยังต่าง ${_sgn(o)}`:'ต่างรายวัน (รวมทั้งเดือนใกล้ ฿0)')+(ch.open_var_days?` · ${ch.open_var_days} วัน`:'');}
  if(ch.status==='waiting_doc'||ch.status==='setup')return ch.detail||'';
  if(b==='coin')return `POS ฿${fmt(ch.pos)} · ต่าง ${_sgn(ch.variance)}`;
  return `POS ฿${fmt(ch.pos)}`+(ch.days_total?` · ${ch.days_done}/${ch.days_total} วัน`:'');
}
function _chPill(ch,b){
  if(ch.accepted)return ['c-mut','รับทราบ'];
  if(b==='coin')return ['c-warn','ต่างเล็กน้อย'];
  if(ch.status==='waiting_doc')return b==='problem'?['c-warn','ขาดเอกสาร']:['c-warn','รอรอบ'];
  const short={ok:'ตรง',diff:'ต่าง',no_source:'ไม่ต้องตรวจ',setup:'ตั้งค่า'};
  return [(CH_ST[ch.status]||['c-mut'])[0],short[ch.status]||ch.status];
}
function wsItems(r){
  const closed=!!DATA.closed, items=[];
  const st=(r.session&&r.session.status)||'draft';
  const docs=r.documents||[], nDoc=docs.length, nUp=docs.filter(d=>d.status==='uploaded').length;
  const docNeed=!closed&&nUp<nDoc;
  items.push({key:'DOCS',kind:'docs',group:docNeed?'act':'ok',label:'เอกสาร',
    line:nDoc?`อัปโหลดแล้ว ${nUp}/${nDoc}`:'ไม่มีเอกสารที่ต้องใช้',pill:docNeed?['c-err','ขาด '+(nDoc-nUp)]:['c-ok','ครบ']});
  const sq=r.setup_questions||[];
  if(sq.length)items.push({key:'SETUP',kind:'setup',group:'act',label:'รหัสชำระที่ยังไม่ตั้งค่า',
    line:sq.map(q=>q.code).join(', '),pill:['c-setup',sq.length+' รหัส']});
  const up=_uploadChKeys(r), ms=_missStmtBanks(r);
  r.channels.forEach((ch,i)=>{
    const b=classifyCh(ch,up,ms);
    items.push({key:ch.key,kind:'ch',idx:i,group:b==='problem'?'act':b==='waiting'?'wait':'ok',coin:b==='coin',
      label:ch.label,line:_chLine(ch,b),pill:_chPill(ch,b)});
  });
  const ps=r.payout_issues||[];
  if(ps.length){
    const open=ps.filter(p=>!p.accepted);
    items.push({key:'PAYOUT',kind:'payout',group:open.length&&!closed?'act':'ok',label:'ยอดโอนจากผู้ให้บริการ',
      line:open.length?`ไม่ตรงรายงาน ${open.length} รายการ · ต่าง ฿${fmt(open.reduce((a,p)=>a+Math.abs(p.variance),0))}`
                      :`รับทราบแล้ว ${ps.length} รายการ`,
      pill:open.length?['c-err','ไม่ตรง']:['c-mut','รับทราบ']});
  }
  const rc=r.reclass_suggestions||[];
  if(rc.length&&!closed)items.push({key:'RECLASS',kind:'reclass',group:'act',label:'เงินอาจอยู่ผิดช่อง',
    line:`${rc.length} คู่ · รวม ฿${fmt(rc.reduce((a,s)=>a+(s.amount||0),0))}`,pill:['c-warn','แนะนำ']});
  const xf=r.internal_transfer_suggestions||[];
  if(xf.length&&!closed)items.push({key:'XFER',kind:'xfer',group:'act',label:'โอนกันเอง (ไม่ใช่ยอดขาย)',
    line:`${xf.length} รายการ · รวม ฿${fmt(xf.reduce((a,x)=>a+x.amount,0))}`,pill:['c-warn','แนะนำตัดออก']});
  if(r.non_revenue&&r.non_revenue.count)items.push({key:'NONREV',kind:'nonrev',group:'more',label:'โอนภายใน (ตัดออกแล้ว)',
    line:`${r.non_revenue.count} รายการ · ฿${fmt(r.non_revenue.amount)}`,pill:null});
  const mv=DATA.moves||[];
  items.push({key:'MOVES',kind:'moves',group:'more',label:'บันทึกผิดช่อง (ย้ายช่อง)',
    line:mv.length?`ย้ายแล้ว ${mv.length} รายการ`:'ย้ายยอดที่กดผิดช่องไปช่องที่ถูก',pill:null});
  const nTodo=(r.todo||[]).length;
  items.push({key:'CLOSE',kind:'close',group:'more',
    label:closed?'ปิดเดือนแล้ว':st==='in_review'?'สรุปก่อนปิดเดือน':'ส่งตรวจ / ปิดเดือน',
    line:closed?`โดย ${(r.session&&r.session.approved_by)||'ผู้จัดการ'}`:nTodo?`เหลือ ${nTodo} เรื่องก่อนปิด`:'ครบแล้ว พร้อมส่งตรวจ / ปิดเดือน',
    pill:closed?['c-ok','ปิดแล้ว']:st==='in_review'?['c-warn','รอผู้จัดการ']:nTodo?null:['c-ok','พร้อม']});
  const gi=Object.fromEntries(WS_GROUPS.map(([g],k)=>[g,k]));
  const rank=x=>((_WS_RANK[x.group]||{})[x.kind]??9);
  return items.map((x,k)=>({x,k})).sort((a,b)=>gi[a.x.group]-gi[b.x.group]||rank(a.x)-rank(b.x)||a.k-b.k).map(o=>o.x);
}
function wsDefault(items){ const a=items.find(x=>x.group==='act'); return a?a.key:'CLOSE'; }
function wsListHtml(items,sel){
  const bid=DATA.branch_id; let h='';
  for(const [g,gl] of WS_GROUPS){
    const its=items.filter(x=>x.group===g); if(!its.length)continue;
    const coins=g==='ok'?its.filter(x=>x.coin):[];
    h+=`<div class="ws-grp"><span id="wsg-${g}">${gl}</span>${g==='more'?'':`<span class="ws-grp-n">${its.length}</span>`}
      ${coins.length&&!DATA.closed?`<button class="btn btn-sm ml-auto" onclick="acceptCoinAll(${jsa(coins.map(x=>x.idx))},this)"
        title="รับทราบผลต่างเล็กน้อย (ไม่เกิน ฿${COIN_TOL}) ทุกช่องทางในครั้งเดียว">รับทราบเศษ ${coins.length}</button>`:''}</div>
      <ul class="ws-ul" aria-labelledby="wsg-${g}">${its.map(x=>`<li><a class="ws-item" href="#b/${bid}/${x.key}" data-key="${x.key}"${x.key===sel?' aria-current="true"':''}>
        <span class="ws-item-main"><span class="ws-item-t">${esc(x.label)}</span><span class="ws-item-s">${esc(x.line||'')}</span></span>
        ${x.pill?`<span class="chip ${x.pill[0]}">${esc(x.pill[1])}</span>`:''}<span class="ws-chev" aria-hidden="true">›</span></a></li>`).join('')}</ul>`;
  }
  return h;
}

/* ── หัวตรึง + ขั้นตอน ── */
// why the month can't be closed yet ('' = closable): open to-dos, then pending discrepancies (the server's 409)
function closeBlock(r){
  const n=(r.todo||[]).length, p=(r.session&&r.session.pending_discrepancies)||0, w=[];
  if(n)w.push(`ยังเหลือ ${n} รายการ`);
  if(p)w.push(`มีข้อแตกต่างค้าง ${p}`);
  return w.join(' · ');
}
function _wsPrimary(r){
  const bid=r.branch_id, st=(r.session&&r.session.status)||'draft';
  if(isClosedSt(st))return `<button class="btn btn-pri" onclick="reopenMonth(${bid})"><i class="ti ti-lock-open" aria-hidden="true"></i> เปิดงวดใหม่ (ผู้จัดการ)</button>`;
  if(st==='in_review'){
    const why=closeBlock(r);
    return `<button class="btn" onclick="setSt(${bid},'draft',this)">ส่งกลับแก้</button>
    <button class="btn btn-pri" onclick="closeMonth(${bid},this)"${why?` disabled title="${esc(why)}"`:''}><i class="ti ti-signature" aria-hidden="true"></i> ปิดเดือน (ผู้จัดการ)</button>${why&&isManager()?`<span class="close-why"><i class="ti ti-alert-circle" aria-hidden="true"></i> ${esc(why)}</span>`:''}`;
  }
  return `<button class="btn btn-pri" onclick="setSt(${bid},'in_review',this)">ส่งตรวจ</button>`;
}
function _wsReportLinks(r){
  const bid=r.branch_id, ses=r.session, st=(ses&&ses.status)||'draft';
  const L=[];
  if(ses)L.push([`/api/sessions/${ses.session_id}/export.xlsx`,'file-spreadsheet','Excel ปิดเดือน','']);
  if(isClosedSt(st)||st==='in_review')L.push([`/api/reports/recon-certificate.print?period_month=${MONTH}&branch_id=${bid}`,'certificate',isClosedSt(st)?'ใบรับรอง':'ใบรับรอง (ก่อนเซ็น)','_blank']);
  L.push([`/api/monthly-report?branch_id=${bid}&period_month=${MONTH}`,'printer','ตรวจการจับคู่รายวัน','_blank']);
  L.push([`/api/monthly-report?branch_id=${bid}&period_month=${MONTH}&detail=1`,'files','ฉบับตรวจสอบ (ละเอียด)','_blank']);
  L.push([`#payout?bid=${bid}`,'credit-card','เงินเข้าธนาคาร','']);
  if(ses)L.push([`#discrepancies?bid=${bid}`,'adjustments','ข้อแตกต่าง','']);
  L.push([`/api/reports/override-register.print?period_month=${MONTH}&branch_id=${bid}`,'writing','ทะเบียนการปรับปรุงด้วยมือ','_blank']);
  return L.map(([href,ic,lbl,tg])=>`<a href="${href}"${tg?` target="${tg}" rel="noopener"`:''}><i class="ti ti-${ic}" aria-hidden="true"></i> ${esc(lbl)}${tg?' <span class="lbl">↗</span>':''}</a>`).join('');
}
function wsHeadHtml(r){
  const ses=r.session, st=(ses&&ses.status)||'draft', closed=isClosedSt(st);
  const dl=closed?null:deadlineInfo(MONTH,false);
  const who=ses?(ses.created_by||'—'):'ยังไม่เริ่ม';
  return `<div class="card ws-head" id="ws-head">
    <div class="ws-title">
      <div class="ws-name"><div class="lbl">สาขา · เดือน</div><h2>${esc(r.branch_name)} · ${esc(monthTh(MONTH))}</h2></div>
      <span class="chip ${closed?'c-ok':st==='in_review'?'c-warn':'c-mut'}">${esc(SES_ST[st]||st)}</span>
      ${dl?`<span class="chip ${dl.cls}">${esc(dl.text)}</span>`:''}
      <span class="lbl ws-who">ผู้ทำ: ${esc(who)}${closed?` · ปิดโดย ${esc(ses.approved_by||'ผู้จัดการ')} ${esc(tsBkk(ses.approved_at))}`:''}</span>
    </div>
    <div class="ws-act">
      <div class="ws-prog" title="ตรงแล้ว ${r.progress_pct}% (ถ่วงด้วยยอดเงิน)"><div class="prog ${progCls(r.progress_pct)}"><div style="width:${r.progress_pct}%"></div></div>
        <span class="mono fs-13 fw-600">${r.progress_pct}%</span></div>
      <details class="menu" id="ws-rep"><summary class="btn btn-sm">รายงาน ▾</summary>
        <div class="menu-pop" role="group" aria-label="รายงานของสาขา">${_wsReportLinks(r)}</div></details>
      ${_wsPrimary(r)}
    </div></div>`;
}
function _wsStep3Target(){
  const it=WS.items.find(x=>x.group==='act'&&(x.kind==='ch'||x.kind==='payout'||x.kind==='reclass'));
  return it?it.key:'CLOSE';
}
function wsStepperHtml(r){
  const bid=r.branch_id, st=(r.session&&r.session.status)||'draft', closed=isClosedSt(st);
  const docs=r.documents||[], nUp=docs.filter(d=>d.status==='uploaded').length;
  const sq=(r.setup_questions||[]).length;
  const left=(r.todo||[]).filter(t=>t.type!=='upload'&&t.type!=='setup').length;
  const s1=closed||nUp===docs.length, s2=closed||(s1&&!sq), s3=closed||left===0;
  const done=[s1,s2,s3,closed];
  const cur=closed?-1:done.findIndex(x=>!x);
  const firstCh=(WS.items.find(x=>x.kind==='ch')||{}).key||'CLOSE';
  const steps=[
    ['DOCS','เอกสาร',`อัปโหลดแล้ว ${nUp}/${docs.length}`],
    [sq?'SETUP':firstCh,'จับคู่อัตโนมัติ',sq?`ตั้งค่ารหัส ${sq} รหัส`:`ตรงแล้ว ${r.progress_pct}%`],
    [_wsStep3Target(),'อธิบายผลต่าง',left?`เหลือ ${left} เรื่อง`:'ครบแล้ว'],
    ['CLOSE',st==='in_review'?'ปิดเดือน':'ส่งตรวจ / ปิดเดือน',closed?'ปิดแล้ว':st==='in_review'?'รอผู้จัดการ':(r.todo||[]).length?'ยังมีเรื่องค้าง':'พร้อมส่งตรวจ'],
  ];
  return `<ol class="stepper" aria-label="ขั้นตอนปิดเดือน">${steps.map((s,k)=>`<li><a class="step${done[k]?' done':''}${k===cur?' cur':''}" href="#b/${bid}/${s[0]}"${k===cur?' aria-current="step"':''}>
    <span class="st-n" aria-hidden="true">${done[k]?'✓':k+1}</span>
    <span class="st-b"><span class="st-t">${s[1]}</span><span class="st-s">${esc(s[2])}${done[k]?'<span class="sr-only"> (เสร็จแล้ว)</span>':''}</span></span></a></li>`).join('')}</ol>`;
}

/* ── วาดพื้นที่ทำงาน / สลับรายการ ── */
function _wsSyncTop(){
  const h=$('ws-head'); if(!h)return;
  document.documentElement.style.setProperty('--ws-top',(h.offsetHeight+12)+'px');
}
window.addEventListener('resize',()=>{ if($('ws-head'))_wsSyncTop(); });
function renderWorkspace(selKey,keep){
  const r=DATA, items=wsItems(r);
  WS.items=items;
  const valid=k=>k&&items.some(x=>x.key===k);
  const sel=valid(selKey)?selKey:(keep&&valid(WS.sel))?WS.sel:wsDefault(items);
  if(sel!==WS.sel)WS.openDay=null;
  WS.sel=sel;
  const y=keep?window.scrollY:0;
  const lst=keep&&document.querySelector('.ws-list'), ly=lst?lst.scrollTop:0;
  $('app').innerHTML=header(`<a href="#" class="lbl" onclick="location.hash='';return false">← หน้าแรก</a>`)
    +wsHeadHtml(r)+wsStepperHtml(r)
    +`<div class="ws-split" id="ws-root" data-view="${selKey?'detail':'list'}">
        <nav class="card ws-list" aria-label="รายการงานของเดือนนี้">${wsListHtml(items,sel)}</nav>
        <section class="ws-detail" id="ws-detail" aria-label="รายละเอียด" tabindex="-1">${wsDetailHtml(sel)}</section>
      </div>`;
  _wsSyncTop();
  wsAfterDetail(sel);
  if(keep){window.scrollTo(0,y);const l2=document.querySelector('.ws-list');if(l2)l2.scrollTop=ly;}
}
// hash changed inside the same workspace (a list click, a stepper click, the back button) → swap the detail only
function wsSelect(key){
  const items=WS.items, sel=(key&&items.some(x=>x.key===key))?key:wsDefault(items);
  if(sel!==WS.sel)WS.openDay=null;
  WS.sel=sel; _DAYS={};
  const root=$('ws-root'); if(!root)return;
  root.dataset.view=key?'detail':'list';
  document.querySelectorAll('#ws-root .ws-item').forEach(a=>{
    if(a.dataset.key===sel)a.setAttribute('aria-current','true');else a.removeAttribute('aria-current');});
  const d=$('ws-detail');
  d.innerHTML=wsDetailHtml(sel);
  wsAfterDetail(sel);
  if(key){
    if(d.getBoundingClientRect().top<0||window.innerWidth<900)d.scrollIntoView({block:'start',behavior:_scrollBehavior()});
    d.focus({preventScroll:true});
  } else {
    const a=document.querySelector('#ws-root .ws-item[aria-current]');
    if(a&&window.innerWidth<900)a.scrollIntoView({block:'center'});
  }
}
function wsAfterDetail(sel){
  const it=WS.items.find(x=>x.key===sel); if(!it)return;
  if(it.kind==='docs'){const p=$('docs-panel');if(p&&p.style.display==='none')toggleDocs();}
  if(it.kind==='ch'&&WS.openDay&&WS.openDay.key===it.key)openDay(it.idx,WS.openDay.day);
}
// re-read the branch and redraw it in place (selection, scroll and an open day drill are kept)
function wsRefresh(){
  if(!DATA)return;
  const m=location.hash.slice(1).match(_WS_RE);
  return loadBranch(DATA.branch_id,m&&m[2]?m[2].toUpperCase():null);
}
async function loadBranch(bid,key){
  const t=++_ROUTE_SEQ;
  const inPlace=!!(DATA&&DATA.branch_id===bid&&DATA.period_month===MONTH&&$('ws-root'));
  if(!inPlace)$('app').innerHTML=header()+loadingState();
  let r;try{r=await api(`/api/v2/reconcile?branch_id=${bid}&month=${MONTH}`);}
  catch(e){if(_cur(t)){if(inPlace)toast('โหลดไม่สำเร็จ: '+e.message);else $('app').innerHTML=header()+`<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`;}return;}
  if(!_cur(t))return;
  let moves;try{moves=await api(`/api/channel-move?branch_id=${bid}&period_month=${MONTH}`);}catch(e){moves=[];}
  if(!_cur(t))return;              // ผู้ใช้เปลี่ยนหน้า/เดือนระหว่างรอ — ไม่แตะ DATA ของหน้าใหม่
  if(!inPlace)WS={sel:null,items:[],openDay:null};
  DATA=r; DATA.moves=moves; _DAYS={};
  DATA.closed=isClosedSt((r.session&&r.session.status)||'draft');
  renderWorkspace(key,inPlace);
}

// "รายงาน ▾" (<details class="menu">): closes on a click outside it and on Esc (focus back to its button)
document.addEventListener('click',e=>{
  document.querySelectorAll('details.menu[open]').forEach(d=>{ if(!d.contains(e.target))d.removeAttribute('open'); });
});
document.addEventListener('keydown',e=>{
  if(e.key!=='Escape')return;
  document.querySelectorAll('details.menu[open]').forEach(d=>{ d.removeAttribute('open'); const s=d.querySelector('summary'); if(s)s.focus(); });
});

/* ── แผงขวา: รายละเอียดของรายการที่เลือก ── */
function wsDetailHtml(key){
  const it=WS.items.find(x=>x.key===key);
  const back=`<a class="btn btn-sm ws-back" href="#b/${DATA.branch_id}"><i class="ti ti-arrow-left" aria-hidden="true"></i> รายการงาน</a>`;
  if(!it)return back+emptyState('list-check','เลือกรายการทางซ้าย','');
  const f={ch:()=>wsChannelHtml(it.idx),docs:wsDocsHtml,setup:wsSetupHtml,payout:()=>payoutIssuesCard(DATA,DATA.branch_id,DATA.closed),
    reclass:()=>(DATA.reclass_suggestions||[]).map((s,i)=>reclassHero(s,i)).join(''),xfer:wsXferHtml,nonrev:wsNonrevHtml,
    moves:()=>channelMovePanel(DATA.branch_id,DATA.closed),close:wsCloseHtml}[it.kind];
  return back+(f?f():'');
}
function wsBridgeHtml(ch){
  const sum=(a,k)=>(a||[]).reduce((s,x)=>s+(+x[k]||0),0);
  const st=[];
  if(ch.key==='COUPON'){
    const ip=ch.issuer_points||[]; if(!ip.length)return '';
    st.push(['ใช้แต้มบน POS',sum(ip,'pos_point')],['ใบสรุปคะแนน',sum(ip,'settle_gross')],['เงินคืนแต้มเข้า',sum(ip,'bank_payout')]);
  } else if(ch.key==='CREDIT'&&(ch.three_way||[]).length){
    const tw=ch.three_way;
    st.push(['ยอดรูดบัตร POS',ch.pos],['สรุปยอดบัตร',sum(tw,'settle_gross')],['หักค่าธรรมเนียม + VAT',-sum(tw,'settle_fee')],['เงินเข้าธนาคาร',sum(tw,'bank_credit')]);
  } else if((ch.key==='GRAB'||ch.key==='LINEMAN')&&ch.payout_trace){
    const p=ch.payout_trace;
    st.push(['ยอดขาย POS',ch.pos],['รายงาน '+ch.label+' (ยอดเต็ม)',ch.ext],['ยอดสุทธิหลังหักค่าธรรมเนียม',p.net_total],['เงินเข้าธนาคาร',p.payout_total]);
    if(p.awaiting_total>0)st.push(['รอเข้ารอบถัดไป',p.awaiting_total]);
  } else {
    st.push(['ยอดขาย POS',ch.pos],[(ch.key==='GRAB'||ch.key==='LINEMAN')?'รายงาน '+ch.label:'เงินเข้าธนาคาร',ch.ext]);
  }
  const res=_chOpen(ch), clear=!!ch.accepted||Math.abs(res)<=1;
  const cmp={CREDIT:'สรุปยอดบัตร − POS',GRAB:'รายงาน − POS',LINEMAN:'รายงาน − POS',COUPON:'ใบสรุปคะแนน − แต้ม POS'}[ch.key]||'เงินเข้า − POS';
  const pend=ch.key==='COUPON'?0:_chPend(ch);
  const parts=[];
  if(ch.accepted)parts.push('รับทราบพร้อมเหตุผลแล้ว');
  else{
    if(ch.key!=='COUPON'&&Math.abs(ch.acked_var||0)>0.5)parts.push(`รับทราบรายวันแล้ว ${_sgn(ch.acked_var)}`);
    if(pend>0.5)parts.push(`รอเงินเข้าตามรอบ ฿${fmt(pend)}`);
    parts.push(clear?'ไม่มีผลต่างค้าง':`ยังอธิบายไม่ได้ ${_sgn(res)}`);
  }
  const sub=parts.join(' · ');
  return `<div class="bridge" role="group" aria-label="ที่มาของตัวเลข ${esc(ch.label)}">
    ${st.map(([l,v],k)=>`${k?'<span class="br-arrow" aria-hidden="true">→</span>':''}<div class="br-step"><span class="lbl">${esc(l)}</span><b>${_baht(v)}</b></div>`).join('')}
    <span class="br-arrow" aria-hidden="true">=</span>
    <div class="br-step br-diff ${clear?'ok':'bad'}"><span class="lbl">ผลต่าง (${esc(cmp)})</span><b>${_sgn(ch.variance)}</b><span class="lbl">${esc(sub)}</span></div>
  </div>`;
}
function wsChannelHtml(i){
  const ch=DATA.channels[i], closed=DATA.closed, acc=ch.accepted;
  const it=WS.items.find(x=>x.key===ch.key)||{};
  const pill=it.pill||['c-mut',''];
  const dirTxt=(ch.key!=='COUPON'&&(ch.status==='diff'||it.coin)&&Math.abs(ch.variance)>0)
    ?(ch.variance<0?`เงินเข้าจริงน้อยกว่า POS ฿${fmt(-ch.variance)}`:`เงินเข้าจริงเกิน POS ฿${fmt(ch.variance)}`)
    :(ch.detail||'');
  const canAccept=!closed&&!acc&&(ch.status==='diff'||ch.status==='waiting_doc'||it.coin);
  const tools=[
    !closed&&ch.key!=='COUPON'?`<button class="btn btn-sm btn-pri" onclick="mxOpen(${jsa(ch.key)})"><i class="ti ti-link" aria-hidden="true"></i> เปิดตัวจับคู่</button>`:'',
    canAccept?`<button class="btn btn-sm" onclick="acceptChannel(${jsa(ch.key)})">✓ รับทราบ + เหตุผล</button>`:'',
    acc&&!closed?`<button class="btn btn-sm" onclick="unaccept(${+acc.id})">เลิกรับทราบ</button>`:'',
    !closed?`<button class="btn btn-sm" onclick="showChannelMove(${DATA.branch_id},${jsa(ch.key)})">⇄ ย้ายยอดไปช่องอื่น</button>`:'',
  ].join('');
  const moves=(DATA.moves||[]).filter(m=>m.from_category===ch.key||m.to_category===ch.key);
  return `<div class="card ws-pane">
    <div class="ws-dh"><h3>${esc(ch.label)}</h3><span class="chip ${pill[0]}">${esc(pill[1])}</span></div>
    ${dirTxt?`<div class="muted">${esc(dirTxt)}</div>`:''}
    ${acc?`<div class="lbl mt-4">รับทราบแล้ว: "${esc(acc.note)}" — ${esc(acc.by||'staff')}</div>`:''}
    ${wsBridgeHtml(ch)}
    ${tools?`<div class="row gap-8">${tools}</div>`:''}
    ${moves.length?`<div class="lbl mt-8">ย้ายช่องที่เกี่ยวกับช่องทางนี้: ${moves.map(m=>`${esc(chLaneLabel(m.from_category,m.from_bank))} → ${esc(chLaneLabel(m.to_category,m.to_bank))} ฿${fmt2(m.amount)}`).join(' · ')} — <a href="#b/${DATA.branch_id}/MOVES">จัดการ</a></div>`:''}
  </div>
  <div class="card p-0 ws-pane mt-12"><div id="days-${i}" class="ws-days">${chDaysHtml(i)}</div></div>`;
}
function acceptChannel(key){
  const ch=((DATA&&DATA.channels)||[]).find(c=>c.key===key); if(!ch)return;
  const res=_chOpen(ch);
  _acceptCtx={channel:key,label:ch.label+(Math.abs(res)>0.5?` ต่าง ${fmt(res)} บาท`:'')};
  _acceptSheet(_acceptCtx.label);
}
// wsDocsHtml (the DOCS pane: drop zone + coverage strips) lives in 22-docs.js
function wsSetupHtml(){
  const closed=DATA.closed;
  return `<div class="card ws-pane"><div class="ws-dh"><h3>รหัสชำระที่ยังไม่รู้ว่าเข้าธนาคารไหน</h3></div>
    <div class="muted mb-8">ตอบครั้งเดียว ระบบจำไว้ทุกเดือน — ใช้คำตอบจากเอกสาร หรือตั้งค่าเอง</div>`
  +(DATA.setup_questions||[]).map((q,qi)=>`<div class="card c-setup mb-8 bg-info">
      <div class="row">รหัสชำระ <b>${esc(q.code)}</b> (${fmt(q.amount)} บาท)
      ${q.suggestion?` — เอกสารชี้ว่าเป็น <b>${esc(q.suggestion.category)}·${esc(q.suggestion.bank)}</b> (ตรง ${q.suggestion.hits} วัน)
        ${closed?'':`<button class="btn btn-sm" onclick="applySugg(${qi},this)">ใช้คำตอบนี้</button>`}`:''}
      ${closed?'':`<a href="#" class="lbl ml-auto" onclick="toggleMapper(${qi});return false">ตั้งค่าเอง ▾</a>`}</div>
      <div id="mapper-${qi}" style="display:none" class="row mt-8">
        <select id="map-cat-${qi}" class="ctl-sm" aria-label="ช่องทาง">
          ${['QR','TRANSFER','CREDIT','CASH','GRAB','LINEMAN','COUPON','ENTERTAIN','OTHER'].map(c=>`<option>${c}</option>`).join('')}
        </select>
        <select id="map-bank-${qi}" class="ctl-sm" aria-label="ธนาคาร">
          ${['KTB','KBANK','KTC','BAY','SCB','BBL','NONE'].map(b=>`<option>${b}</option>`).join('')}
        </select>
        <button class="btn btn-sm btn-pri" onclick="applyMapper(${qi},this)">บันทึก</button>
        <span class="lbl">เงินสด/Grab/Line Man/คูปอง เลือกธนาคาร NONE</span>
      </div></div>`).join('')+`</div>`;
}
function wsXferHtml(){
  const xfers=DATA.internal_transfer_suggestions||[];
  const sum=xfers.reduce((a,x)=>a+x.amount,0);
  return `<div class="card ws-pane bg-warn bd-warn">
    <div class="row"><b class="fs-135">⇄ พบยอดโอนเข้าที่น่าจะเป็น "การโอนกันเอง" ${xfers.length} รายการ (รวม ${fmt(sum)})</b>
      ${xfers.length>1?`<button class="btn btn-sm ml-auto" onclick="excludeAllXfers()">ตัดออกทั้งหมด</button>`:''}</div>
    <div class="muted my-2-6">ยอดก้อนใหญ่ + เลขกลม + ไม่มีคำว่า "ขาย" ในรายละเอียด — ตัดออกแล้วจะไม่ถูกนำมากระทบยอด (ย้ายไปกล่อง "โอนภายใน" · ย้อนกลับได้)</div>
    ${xfers.map(x=>`<div class="todo-item">
      <span class="fx-1">${esc(x.date)} · <b class="mono">${fmt(x.amount)}</b>
        <span class="lbl">${esc(x.description||'')}${x.channel?' · '+esc(x.channel):''}</span>
        <span class="chip ${x.reason==='code'?'c-warn':'c-mut'}" title="${x.reason==='code'?'รหัสโอนระหว่างบัญชี/ระหว่างธนาคาร (IORSDT/NBSDT) — ไม่ใช่ยอดขาย':'ก้อนใหญ่ เลขกลม ไม่มีคำว่าขาย'}">${x.reason==='code'?'รหัสโอนภายใน':'ก้อนใหญ่เลขกลม'}</span></span>
      <button class="btn btn-sm" onclick="excludeXfer(${+x.id})">ตัดออก (ไม่ใช่ยอดขาย)</button>
    </div>`).join('')}</div>`;
}
function wsNonrevHtml(){
  const nr=DATA.non_revenue||{count:0,amount:0,lines:[]};
  return `<div class="card ws-pane">
    <div class="ws-dh"><h3>⇄ เงินเข้าที่ไม่ใช่ยอดขาย: โอนภายใน</h3></div>
    <div class="muted mb-8">${nr.count} รายการ ${fmt(nr.amount)} บาท — ตัดออกจากการกระทบยอดแล้ว</div>
    <div class="ovx-auto"><table>
      <tr><th>วันที่</th><th class="tl">รายละเอียด</th><th>ยอด</th></tr>
      ${(nr.lines||[]).map(l=>`<tr><td>${esc(l.date)}</td><td class="tl">${esc(l.ref)}</td><td class="mono">${fmt(l.amount)}</td></tr>`).join('')}
    </table></div></div>`;
}
// todo → the work-list item that resolves it
function _todoKey(t){
  if(t.type==='upload')return 'DOCS';
  if(t.type==='setup')return 'SETUP';
  if(t.type==='payout')return 'PAYOUT';
  return t.channel||'CLOSE';
}
function wsCloseHtml(){
  const r=DATA, bid=r.branch_id, ses=r.session, st=(ses&&ses.status)||'draft', closed=isClosedSt(st);
  let h='';
  if(closed)h+=`<div class="card c-ok mb-12 bg-ok"><i class="ti ti-lock" aria-hidden="true"></i> เดือนนี้ปิดแล้ว
      โดย ${esc(ses.approved_by||'ผู้จัดการ')} · ${esc(tsBkk(ses.approved_at))} — ตัวเลขถูกบันทึกถาวร แก้ไขไม่ได้ ·
      ถ้าต้องแก้ ผู้จัดการกด <b>เปิดงวดใหม่</b> (เหตุผล + ${ME.login_enabled?'รหัสผ่านของตัวเอง':'รหัสผู้จัดการ'})
      <div class="row mt-8 gap-8"><button class="btn btn-sm" onclick="reopenMonth(${bid})"><i class="ti ti-lock-open" aria-hidden="true"></i> เปิดงวดใหม่ (ผู้จัดการ)</button>
      <a class="btn btn-sm" href="/api/reports/recon-certificate.print?period_month=${MONTH}&branch_id=${bid}" target="_blank" rel="noopener"><i class="ti ti-certificate" aria-hidden="true"></i> ใบรับรอง</a></div></div>`;
  else if(r.todo.length){
    h+=`<div class="card mb-12"><b class="fs-135">เหลือ ${r.todo.length} เรื่องก่อนปิดเดือน</b><div class="my-6">`;
    r.todo.forEach((t,i)=>{
      const acceptable=t.channel||t.type==='diff';
      h+=`<div class="todo-item"><span class="fx-1">• ${esc(t.label)}</span>
        <a class="btn btn-sm" href="#b/${bid}/${_todoKey(t)}">ไปที่รายการ ›</a>
        ${t.type==='upload'?`<button class="btn btn-sm" onclick="pickDoc(${DATA.documents.findIndex(d=>d.key===t.doc)})">อัปโหลด</button>`:''}
        ${acceptable?`<button class="btn btn-sm" onclick="acceptTodo(${i})">✓ รับทราบ + เหตุผล</button>`:''}
      </div>`;
    });
    h+=`</div><div class="muted">"รับทราบ" = ยอมรับผลต่างโดยใส่เหตุผลจริง (เช่น ผลต่างจากค่าธรรมเนียมที่กระทบยอดแล้ว) — เหตุผลจะติดไปในไฟล์ Excel ให้ผู้จัดการเห็นตอนอนุมัติ</div>
      <div id="up-panel"></div></div>`;
  } else if((ses&&ses.pending_discrepancies)>0)h+=`<div class="card mb-12 bd-warn bg-warn"><b class="fs-135">มีข้อแตกต่างที่ยังไม่ได้ตัดสิน ${ses.pending_discrepancies} รายการ</b>
      <div class="muted my-2-6">ต้องยอมรับ/ปรับปรุง/ปฏิเสธให้ครบก่อน จึงจะลงนามปิดเดือนได้</div>
      <a class="btn btn-sm" href="#discrepancies?bid=${bid}"><i class="ti ti-adjustments" aria-hidden="true"></i> ไปที่ข้อแตกต่าง ›</a></div>`;
  else h+=`<div class="card mb-12"><span class="chip c-ok">✓ ครบทุกช่องทาง พร้อมปิดเดือน</span></div>`;

  // จอสรุปผู้จัดการ — เห็นทุกอย่างที่ staff ทำ/รับทราบ ก่อนใส่รหัสปิดเดือน (ขั้นที่ 4)
  const title=closed?'สรุปผลที่ปิดไว้':st==='in_review'?'สรุปสำหรับผู้จัดการ — ตรวจก่อนปิดเดือน':'สรุปที่ผู้จัดการจะเห็นเมื่อส่งตรวจ';
  h+=`<div class="card mb-12${st==='in_review'?' bd-brand':''}" id="mgr-summary">
    <b class="fs-135"><i class="ti ti-clipboard-list" aria-hidden="true"></i> ${title}</b>
    <div class="ovx-auto"><table class="mt-8">
      <tr><th>ช่องทาง</th><th>POS</th><th>เงินเข้าจริง</th><th>ต่าง</th><th class="tl">สถานะ / เหตุผลที่ staff รับทราบ</th></tr>
      ${r.channels.map(c=>{
        const [cls,lab]=c.accepted?['c-mut','รับทราบ']:(CH_ST[c.status]||['c-mut',c.status]);
        return `<tr><td>${esc(c.label)}</td><td class="mono">${fmt(c.pos)}</td>
          <td class="mono">${c.ext?fmt(c.ext):'—'}</td>
          <td class="mono ${Math.abs(c.variance)<=100?'':'t-err'}">${c.variance>0?'+':''}${fmt(c.variance)}</td>
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
    </table></div>
    <div class="row mt-8 gap-8 fx-wrap">
      <a class="btn btn-sm" href="/api/reports/override-register.print?period_month=${MONTH}&branch_id=${bid}" target="_blank" rel="noopener"><i class="ti ti-writing" aria-hidden="true"></i> ทะเบียนการปรับปรุงด้วยมือ</a>
      ${closed?'':`<a class="btn btn-sm" href="/api/reports/recon-certificate.print?period_month=${MONTH}&branch_id=${bid}" target="_blank" rel="noopener"><i class="ti ti-certificate" aria-hidden="true"></i> ดูใบรับรอง (ก่อนเซ็น)</a>`}
    </div>
    <div class="lbl mt-6">ปิดเดือนแล้วเหตุผลทั้งหมดจะติดไปในไฟล์ Excel · แก้ไขไม่ได้จนกว่าผู้จัดการจะกด "เปิดงวดใหม่"</div>
  </div>`;
  if(!closed)h+=`<div class="row jc-end gap-8">${_wsPrimary(r)}</div>`;
  return h;
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

/* bulk upload (drop zone, many files, zip, a folder) → 22-docs.js bulkFiles() */

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

// a channel's evidence: POS codes, by-date worklist, card/payout/cash/coupon traces, fees, day calendar, day drill
function chDaysHtml(i){
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
  // คูปอง: แต้ม KTC/BAY ตรวจอัตโนมัติแล้ว — ลิงก์วิเคราะห์คูปองซีรีส์ (in-app #coupon)
  const cpBtn=ch.key==='COUPON'?
    `<a class="btn btn-sm" href="#coupon"><i class="ti ti-ticket" aria-hidden="true"></i> รายละเอียดคูปอง</a>`:'';
  const worklist=dayWorklistHtml(ch,i);
  return `
  ${codes||xferBankRow||feeBtn||cpBtn?`<div class="row ws-days-tools">${codes?`<span class="lbl">ที่มา POS:</span> ${codes}`:''} ${xferBankRow} ${feeBtn} ${cpBtn}</div>`:''}
  <div id="codedaily-${i}" class="ws-pad-x"></div>
  ${worklist}${c3}${pt}${cash3}${cpCards}
  <div id="fees-${i}"></div>
  <div class="lbl ws-pad-x mt-8"><b>ปฏิทินรายวัน</b> — กดวันเพื่อดูบิล POS เทียบบรรทัดเงินเข้าของวันนั้น</div>
  <div class="daygrid">${ch.days.map(d=>`
    <div class="daycell d-${d.ack?'ok':esc(d.status)}" role="button" tabindex="0" aria-label="วันที่ ${+d.day.slice(8)} POS ${fmt(d.pos)}${d.status==='diff'&&!d.ack?' ต่าง '+fmt(d.variance):''}" onclick="event.stopPropagation();openDay(${i},${jsa(d.day)})">
      <b>${+d.day.slice(8)}</b> <span class="lbl">${d.ack?'✓':({ok:'✓',diff:'≠',waiting:'<i class="ti ti-hourglass-high" aria-hidden="true"></i>',setup:'?',no_source:'·'}[d.status]||'')}</span>
      <div class="mono fs-11">${fmt(d.pos)}</div>
      ${d.status==='diff'&&!d.ack?`<div class="mono fs-11 t-err">${d.variance>0?'+':''}${fmt(d.variance)}</div>`:''}
      ${d.ack_stale?`<div class="lbl fs-10 t-warn" title="รับทราบไว้ที่ ${fmt(d.ack_stale.variance)} — ต้องรับทราบใหม่">${esc(d.detail||'ยอดเปลี่ยนหลังรับทราบ')}</div>`:''}
    </div>`).join('')}</div>
  <div id="daylines-${i}" class="ws-daylines"></div>`;
}
