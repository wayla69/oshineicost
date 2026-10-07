/* recon frontend — 22-docs.js: #b/<id>/DOCS — the month's documents: one drop zone for every file of the
   branch-month (files, zip or a folder → POST /api/uploads/bulk, auto-classified) with a per-file result list,
   and a coverage strip per expected document (one square per day, from reconcile_v2 documents[].days_covered).
   Plain scripts sharing one global scope, loaded in order by index.html (00 → 90);
   a slice may call any other slice's functions, but top-level code only uses earlier slices. */

/* ── pure helpers (node harness: recon/tests/test_frontend_helpers.py) ── */
// today in Bangkok as 'YYYY-MM-DD', whatever the device's own time zone
function bkkYmd(d){
  try{return new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Bangkok',year:'numeric',month:'2-digit',day:'2-digit'}).format(d||new Date());}
  catch(e){return ymdLocal(d||new Date());}
}
// [1,2,3,5,9,10] → [[1,3],[5,5],[9,10]]
function covRanges(days){
  const s=[...new Set(days||[])].sort((a,b)=>a-b), out=[];
  for(const d of s){const last=out[out.length-1]; if(last&&d===last[1]+1)last[1]=d; else out.push([d,d]);}
  return out;
}
// days 1..due that no uploaded document covers
function covMissing(covered,due){
  const have=new Set(covered||[]), out=[];
  for(let d=1;d<=due;d++)if(!have.has(d))out.push(d);
  return out;
}
const covRangeTxt=([a,b])=>a===b?String(a):`${a}–${b}`;
// the pill: 'ยังไม่มี' / 'ครบ' / 'ขาด 27–30' (at most three ranges, then …)
function covText(covered,due){
  if(!covered||!covered.length)return 'ยังไม่มี';
  const rs=covRanges(covMissing(covered,due));
  if(!rs.length)return 'ครบ';
  return 'ขาด '+rs.slice(0,3).map(covRangeTxt).join(', ')+(rs.length>3?' …':'');
}
// the last day of the unbroken run from the 1st (0 = the 1st itself is missing)
function covThrough(covered){
  const have=new Set(covered||[]); let d=0;
  while(have.has(d+1))d++;
  return d;
}
// one square: 'on' covered · 'miss' due but not covered · 'fut' not due yet (future / after the cut-off)
const covCls=(d,have,due)=>have.has(d)?'on':d<=due?'miss':'fut';

/* ── coverage strip ── */
// the last day a document can be late for: the backend's cut-off, never past today (Bangkok)
function covDue(doc,month){
  const eom=new Date(+month.slice(0,4),+month.slice(5,7),0).getDate();
  const t=bkkYmd(), cur=t.slice(0,7);
  const today=month===cur?+t.slice(8):month>cur?0:eom;
  return Math.min(doc.days_due||eom,today,eom);
}
function covStripHtml(doc,month){
  if(!Array.isArray(doc.days_covered))return '';
  const eom=new Date(+month.slice(0,4),+month.slice(5,7),0).getDate();
  const due=covDue(doc,month), have=new Set(doc.days_covered);
  const word={on:'มีเอกสาร',miss:'ขาด',fut:'ยังไม่ถึงกำหนด'};
  let cells='';
  for(let d=1;d<=eom;d++){const c=covCls(d,have,due);cells+=`<span class="cov-d cov-${c}" title="วันที่ ${d}: ${word[c]}"></span>`;}
  return `<div class="cov" role="img" aria-label="${esc(doc.label)} รายวัน: ${esc(covText(doc.days_covered,due))}" style="--n:${eom}">
    <div class="cov-row">${cells}</div>
    <div class="cov-scale" aria-hidden="true"><span>1</span><span>${eom}</span></div></div>`;
}
// the doc's pill: from the strip when it has one, else from its status (monthly point summaries)
function covPill(doc,month){
  if(!Array.isArray(doc.days_covered)){
    return doc.status==='uploaded'?['c-ok','ครบ']:['c-err',doc.detail||'ยังไม่มี'];
  }
  const t=covText(doc.days_covered,covDue(doc,month));
  return [t==='ครบ'?'c-ok':t==='ยังไม่มี'?'c-err':'c-warn',t];
}

/* ── the DOCS pane ── */
let _DZ_LAST=null;              // the last drop's result, kept across the refresh that follows it
function wsDocsHtml(){
  const r=DATA, closed=DATA.closed, docs=r.documents||[];
  const nUp=docs.filter(d=>d.status==='uploaded').length;
  const rows=docs.map((d,di)=>{
    const [cls,txt]=covPill(d,MONTH);
    const need=d.status!=='uploaded'||cls!=='c-ok';
    return `<div class="doc-row"><div class="fx-1"><b class="fs-14">${esc(d.label)}</b>${d.hint?`<div class="lbl">${esc(d.hint)}</div>`:''}</div>
      <span class="chip ${cls}">${esc(txt)}</span>
      ${need&&!closed?`<button class="btn btn-sm" onclick="pickDoc(${di})" title="อัปโหลดเฉพาะเอกสารนี้ (เลือกประเภทให้แล้ว)">↑ อัปโหลด</button>`:''}
      ${covStripHtml(d,MONTH)}</div>`;
  }).join('');
  const last=_DZ_LAST&&_DZ_LAST.bid===r.branch_id&&_DZ_LAST.month===MONTH?_DZ_LAST.html:'';
  return `<div class="card ws-pane">
    <div class="ws-dh"><h3>เอกสารประจำเดือน</h3><span class="lbl ml-auto">อัปโหลดแล้ว ${nUp}/${docs.length}</span></div>
    ${closed?'':dropZoneHtml(r)}
    <div id="up-panel">${last}</div>
    ${rows?`<div class="cov-legend lbl" aria-hidden="true"><span><span class="cov-d cov-on"></span> มีเอกสาร</span>
      <span><span class="cov-d cov-miss"></span> ขาด</span><span><span class="cov-d cov-fut"></span> ยังไม่ถึงกำหนด</span></div>`:''}
    ${rows||'<div class="muted">เดือนนี้ไม่มีเอกสารที่ต้องใช้</div>'}
    <div class="row mt-10"><a href="#" class="lbl" onclick="toggleDocs();return false" id="docs-toggle">จัดการไฟล์ ▾</a></div>
    <div id="docs-panel" style="display:none"></div>
  </div>`;
}
function dropZoneHtml(r){
  return `<div class="dz" id="dz" role="button" tabindex="0" aria-describedby="dz-h"
      onclick="dzPick(event)" onkeydown="dzKey(event)" ondragover="dzOver(event)" ondragleave="dzLeave(event)" ondrop="dzDrop(event)">
    <i class="ti ti-cloud-upload dz-i" aria-hidden="true"></i>
    <div class="dz-t">วางไฟล์ทั้งหมดของ <b>${esc(r.branch_name)}</b> · <b>${esc(monthTh(MONTH))}</b> ที่นี่</div>
    <div class="lbl" id="dz-h">สเตทเมนต์ · สรุปยอดบัตร · Grab · Line Man — หลายไฟล์, zip หรือทั้งโฟลเดอร์ · ระบบแยกประเภทจากชื่อไฟล์ให้เอง ข้ามไฟล์/บรรทัดที่ซ้ำ</div>
    <div class="row dz-btns gap-8 mt-8">
      <button class="btn btn-sm" type="button" onclick="event.stopPropagation();$('bulk-input').click()"><i class="ti ti-files" aria-hidden="true"></i> เลือกไฟล์</button>
      <button class="btn btn-sm" type="button" onclick="event.stopPropagation();$('bulk-folder-input').click()"><i class="ti ti-folder" aria-hidden="true"></i> เลือกทั้งโฟลเดอร์</button>
    </div>
    <input type="file" id="bulk-input" multiple hidden accept=".pdf,.xlsx,.xls,.csv,.zip" onchange="bulkUpload('bulk-input')">
    <input type="file" id="bulk-folder-input" webkitdirectory multiple hidden onchange="bulkUpload('bulk-folder-input')">
  </div>`;
}
// a file dropped NEXT TO the zone must not make the browser open it (and leave the app)
document.addEventListener('dragover',e=>{ if($('dz'))e.preventDefault(); });
document.addEventListener('drop',e=>{ if($('dz')&&!(e.target&&e.target.closest&&e.target.closest('#dz')))e.preventDefault(); });
function dzPick(e){ if(e&&e.target&&e.target.closest&&e.target.closest('button,input'))return; $('bulk-input').click(); }
function dzKey(e){ if(e.key==='Enter'||e.key===' '){e.preventDefault();$('bulk-input').click();} }
function dzOver(e){ e.preventDefault(); if(e.dataTransfer)e.dataTransfer.dropEffect='copy'; e.currentTarget.classList.add('dz-on'); }
function dzLeave(e){ if(!e.currentTarget.contains(e.relatedTarget))e.currentTarget.classList.remove('dz-on'); }
async function dzDrop(e){
  e.preventDefault(); e.currentTarget.classList.remove('dz-on');
  const dt=e.dataTransfer; if(!dt)return;
  const entries=[...(dt.items||[])].map(it=>it.webkitGetAsEntry&&it.webkitGetAsEntry()).filter(Boolean);
  let files;
  if(entries.some(en=>en.isDirectory)){                 // a dropped folder: walk it
    files=[]; for(const en of entries)await _dzWalk(en,files);
  } else files=[...(dt.files||[])];
  bulkFiles(files);
}
async function _dzWalk(entry,out){
  if(entry.isFile){await new Promise(res=>entry.file(f=>{out.push(f);res();},()=>res()));return;}
  if(!entry.isDirectory)return;
  const rd=entry.createReader();
  for(;;){                                              // readEntries returns the folder in chunks
    const batch=await new Promise(res=>rd.readEntries(res,()=>res([])));
    if(!batch.length)break;
    for(const en of batch)await _dzWalk(en,out);
  }
}

/* ── bulk upload (drop zone + the two pickers) ── */
async function bulkUpload(inputId){
  const inp=$(inputId); const files=[...(inp.files||[])];
  inp.value='';
  bulkFiles(files);
}
async function bulkFiles(files){
  files=(files||[]).filter(f=>f&&f.size!==undefined&&!/^\./.test(f.name));   // skip hidden files of a folder
  if(!files.length||!DATA)return;
  const bid=DATA.branch_id, month=MONTH, panel=$('up-panel');
  await busy('bulk',null,async()=>{
    if(panel)panel.innerHTML=loadingState(`กำลังอัปโหลด ${files.length} ไฟล์… (อาจใช้เวลาสักครู่)`);
    const fd=new FormData();
    for(const f of files)fd.append('files',f,f.name);
    fd.append('branch_id',bid); fd.append('period_month',month); fd.append('source_type','auto');
    let r;
    try{ r=await api('/api/uploads/bulk',{method:'POST',body:fd}); }
    catch(e){ const p=$('up-panel'); if(p)p.innerHTML=`<div class="chip c-err mt-10" role="alert">อัปโหลดไม่สำเร็จ: ${esc(e.message)}</div>`; hint403(e); return; }
    _DZ_LAST={bid,month,html:dzResultHtml(r)};
    if(DATA&&DATA.branch_id===bid&&MONTH===month){
      const p=$('up-panel'); if(p)p.innerHTML=_DZ_LAST.html;
      if((r.summary||{}).saved)wsRefresh();             // new rows → statuses + strips move
    }
  });
}
const _MONTH_SHORT=['','ม.ค.','ก.พ.','มี.ค.','เม.ย.','พ.ค.','มิ.ย.','ก.ค.','ส.ค.','ก.ย.','ต.ค.','พ.ย.','ธ.ค.'];
const _monShort=ym=>(_MONTH_SHORT[+String(ym).slice(5,7)]||ym);
function dzResultHtml(r){
  const s=r.summary||{}, files=r.files||[];
  const dupAll=s.duplicate_lines||files.reduce((a,f)=>a+dupCount(f),0);
  const other=s.other_month_rows||0;
  const st={ok:['c-ok','บันทึกแล้ว'],skipped:['c-warn','ข้าม'],error:['c-err','ไม่รับ']};
  const items=files.map(f=>{
    const [cls,lab]=st[f.status]||['c-mut',f.status];
    const bits=[];
    if(f.status==='ok'){
      bits.push(`บันทึก ${fmt(f.rows_saved)} รายการ`);
      if(dupCount(f))bits.push(`ข้ามบรรทัดซ้ำ ${fmt(dupCount(f))}`);
      if(f.other_month_rows){const om=(f.row_months||[]).filter(m=>m!==MONTH).map(_monShort).join(', ');
        bits.push(`เป็นของเดือนอื่น ${fmt(f.other_month_rows)} รายการ${om?' ('+om+')':''}`);}
    } else bits.push(detailText(f.reason)||'—');
    return `<li class="dz-f"><div class="dz-fh"><span class="dz-fn ellipsis" title="${esc(f.file)}">${esc(f.file)}</span>
        ${f.source_type?`<span class="lbl">${esc(SRC_LABEL[f.source_type]||f.source_type)}${f.provider?' '+esc(f.provider):''}</span>`:''}
        <span class="chip ${cls}">${lab}</span></div>
      <div class="lbl dz-fd">${esc(bits.join(' · '))}</div></li>`;
  }).join('');
  const sum=[`บันทึก ${s.saved||0} ไฟล์ (${fmt(s.rows||0)} รายการ)`];
  if(s.skipped)sum.push(`ข้าม ${s.skipped}`);
  if(s.errored)sum.push(`ไม่รับ ${s.errored}`);
  if(dupAll)sum.push(`ข้ามบรรทัดซ้ำ ${fmt(dupAll)}`);
  if(other)sum.push(`รายการของเดือนอื่น ${fmt(other)}`);
  return `<div class="card dz-res mt-10" role="status">
    <div class="row"><b class="fs-135">ผลอัปโหลด ${s.total||files.length} ไฟล์</b>
      <span class="lbl">${esc(sum.join(' · '))}</span>
      <button class="btn btn-sm ml-auto" type="button" onclick="dzClear()">ปิด</button></div>
    <ul class="dz-list">${items}</ul>
    ${s.skipped||s.errored?`<div class="lbl mt-6">ไฟล์ที่ข้าม/ไม่รับ: อ่านเหตุผลในแต่ละแถว — ไฟล์ที่ระบบไม่รู้ประเภท ใช้ปุ่ม "↑ อัปโหลด" ของเอกสารนั้นด้านล่าง</div>`:''}
  </div>`;
}
function dzClear(){ _DZ_LAST=null; const p=$('up-panel'); if(p)p.innerHTML=''; }
