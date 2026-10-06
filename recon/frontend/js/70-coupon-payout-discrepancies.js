/* recon frontend — 70-coupon-payout-discrepancies.js: #coupon, #payout?bid=, #discrepancies?bid=.
   Plain scripts sharing one global scope, loaded in order by index.html (00 → 90);
   a slice may call any other slice's functions, but top-level code only uses earlier slices. */
/* ════════════════════════════════════════════════════════════════════════
   § COUPON SERIES VIEW (Phase 2 consolidation)
   Route: #coupon
   Read-only — no backend changes. Source: pos_coupon_day via /api/coupon-series.
   Replaces the link that previously sent users to /app/classic.html#coupon.
   ════════════════════════════════════════════════════════════════════════ */

let _CP_GRAN = 'month';          // 'month' | 'week' | 'day'
let _CP_BID  = '';               // branch_id filter ('' = all master branches)
let _CP_DATE = '';               // date for week/day grain
let _CP_BRANCHES_LOADED = false; // flag so we only populate branch picker once

const _cpEsc=esc;                 // เดิมไม่ escape เครื่องหมายคำพูด — ใช้ esc ตัวเดียวกันทั้งแอป
function _cpName(r){ return r.series_name
  ? `${_cpEsc(r.series_name)} <span class="lbl fs-11">#${r.series_id}</span>`
  : `Series ${r.series_id}`; }
// MoM: more coupon discount (up) = more given away → show red; down = green
function _cpMom(pct){
  if(pct==null) return '<span class="chip c-mut fs-11">ใหม่</span>';
  const [cls,sym]=pct>0?['c-err','▲']:pct<0?['c-ok','▼']:['c-mut',''];
  return `<span class="chip ${cls} fs-11">${sym} ${Math.abs(pct).toFixed(1)}%</span>`;
}

function _cpQuery(){
  if(_CP_GRAN==='month') return `?gran=month&period_month=${MONTH}`;
  const d = _CP_DATE || (MONTH+'-01');
  return `?gran=${_CP_GRAN}&day=${d}`;
}

async function loadCoupon(){
  const t=++_ROUTE_SEQ;
  $('app').innerHTML = header() + loadingState();

  // Fetch branch list for picker (master branches only — same as other diag views)
  const branches = await _diagBranches();
  if(!_cur(t)) return;

  let d = null;
  const q = _cpQuery() + (_CP_BID ? `&branch_id=${encodeURIComponent(_CP_BID)}` : '');
  try{ d = await api('/api/coupon-series' + q); }
  catch(e){ if(_cur(t)) $('app').innerHTML = header() + `<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`; return; }
  if(!_cur(t)) return;
  d = d || {series:[], by_branch:[], total:0, total_used:0, label:MONTH};

  const granLabel = {month:'เดือน',week:'อาทิตย์',day:'วัน'}[_CP_GRAN];

  // Gran toggle
  const granBtns = ['month','week','day'].map(g =>
    `<button class="btn btn-sm${_CP_GRAN===g?' btn-pri':''}" onclick="_CP_GRAN='${g}';loadCoupon()">${{month:'เดือน',week:'สัปดาห์',day:'วัน'}[g]}</button>`
  ).join('');

  // Date picker (shown only for week/day)
  const dateInput = _CP_GRAN !== 'month'
    ? `<input type="date" value="${esc(_CP_DATE||MONTH+'-01')}"
         onchange="_CP_DATE=this.value;loadCoupon()"
         style="padding:5px 9px;border:1px solid var(--line);border-radius:var(--radius-sm);background:var(--input);font:inherit">`
    : '';

  // Branch picker
  const branchOpts = [
    `<option value="" ${_CP_BID===''?'selected':''}>ทุกสาขา</option>`,
    ...branches.map(b => `<option value="${b.branch_id}" ${_CP_BID==b.branch_id?'selected':''}>${esc(b.name)}</option>`)
  ].join('');
  const branchPicker = `<select onchange="_CP_BID=this.value;loadCoupon()"
    title="เลือกสาขา"
    class="ctl">${branchOpts}</select>`;

  const tot = d.total || 0;
  const series = d.series || [];

  // Series rows
  const seriesRows = series.length
    ? series.map(r => `<tr class="pointer" role="button" tabindex="0" onclick="_cpDrill(${+r.series_id})" title="คลิกดูแยกรายสาขา/รายวัน">
        <td>${_cpName(r)} <span class="lbl fs-11">›</span></td>
        <td class="mono tr">${fmt(r.used_count)}</td>
        <td class="mono tr">${fmt(r.discount_amount)}</td>
        <td class="tr">${_cpMom(r.mom_pct)}</td>
        <td class="mono tr t-sub">${tot?(r.discount_amount/tot*100).toFixed(1):0}%</td>
      </tr>`).join('')
    : `<tr><td colspan="5" class="cell-empty">ไม่มีข้อมูลคูปองในช่วงนี้</td></tr>`;

  // By-branch table (when not filtered to a single branch)
  const byBranch = d.by_branch || [];
  const branchRows = (!_CP_BID && byBranch.length)
    ? byBranch.map(r => `<tr>
        <td>${esc(r.branch_name)}</td>
        <td class="mono tr">${fmt(r.used_count)}</td>
        <td class="mono tr">${fmt(r.discount_amount)}</td>
      </tr>`).join('')
    : '';

  $('app').innerHTML = header() + `
    <div class="row mb-10 fx-wrap gap-8">
      <h2 class="fs-16"><i class="ti ti-ticket" aria-hidden="true"></i> วิเคราะห์คูปองซีรีส์</h2>
      <div class="row gap-6 fx-wrap ml-auto">
        ${granBtns}
        ${dateInput}
        ${branchPicker}
      </div>
    </div>
    <div class="muted mb-10">
      ${granLabel} · ${esc(d.label||MONTH)}
      · ส่วนลดคูปองจาก pos_coupon_day (master สาขาเท่านั้น)
      · คลิกซีรีส์เพื่อดูแยกรายสาขา/รายวัน
    </div>

    <div class="card mb-12">
      <div class="row fx-wrap gap-16">
        <div><div class="lbl">ส่วนลดรวม</div><b class="mono fs-18">${fmt(tot)}</b></div>
        <div><div class="lbl">จำนวนใบ</div><b class="mono fs-18">${fmt(d.total_used||0)}</b></div>
        <div><div class="lbl">ซีรีส์</div><b class="fs-18">${series.length}</b></div>
        ${d.prev_total!=null?`<div><div class="lbl">ช่วงก่อนหน้า</div><b class="mono fs-18 t-sub">${fmt(d.prev_total)}</b></div>`:''}
      </div>
    </div>

    <div class="card p-0 mb-12">
      <div class="pad-12-16 bdb-soft">
        <b class="fs-135">แยกตามซีรีส์คูปอง</b>
      </div>
      <div class="ov-auto">
        <table>
          <thead><tr>
            <th class="tl">ซีรีส์</th>
            <th class="tr">จำนวนใช้</th>
            <th class="tr">มูลค่าส่วนลด</th>
            <th class="tr">MoM</th>
            <th class="tr">% รวม</th>
          </tr></thead>
          <tbody>${seriesRows}</tbody>
          ${series.length?`<tfoot><tr class="bg-soft fw-600">
            <td>รวม</td><td class="mono tr">${fmt(d.total_used||0)}</td>
            <td class="mono tr">${fmt(tot)}</td><td></td><td class="mono tr">100%</td>
          </tr></tfoot>`:''}
        </table>
      </div>
    </div>

    <div id="cp-drill"></div>

    ${branchRows?`<div class="card p-0">
      <div class="pad-12-16 bdb-soft">
        <b class="fs-135">แยกรายสาขา (รวมทุกซีรีส์)</b>
      </div>
      <div class="ov-auto">
        <table>
          <thead><tr>
            <th class="tl">สาขา</th>
            <th class="tr">จำนวนใช้</th>
            <th class="tr">มูลค่าส่วนลด</th>
          </tr></thead>
          <tbody>${branchRows}</tbody>
        </table>
      </div>
    </div>`:''}`;
}

async function _cpDrill(seriesId){
  const box = $('cp-drill');
  if(!box) return;
  box.innerHTML = `<div class="card mb-12"><div class="muted">กำลังโหลด Series ${seriesId}…</div></div>`;
  box.scrollIntoView({behavior:_scrollBehavior(), block:'nearest'});

  const q = _cpQuery() + `&series_id=${seriesId}` + (_CP_BID ? `&branch_id=${encodeURIComponent(_CP_BID)}` : '');
  let d = null;
  try{ d = await api('/api/coupon-series' + q); }
  catch(e){
    box.innerHTML = `<div class="card c-err mb-12">โหลด Series ${seriesId} ไม่สำเร็จ: ${esc(e.message)}
      <button class="btn btn-sm ml-8" onclick="$('cp-drill').innerHTML=''">✕ ปิด</button></div>`;
    return;
  }

  const brows = (d&&d.by_branch)||[];
  const drows = (d&&d.by_day)||[];
  const nm    = (d&&d.series&&d.series[0]&&d.series[0].series_name) ? _cpEsc(d.series[0].series_name) : '';
  const mom   = (d&&d.series&&d.series[0]) ? d.series[0].mom_pct : null;

  const subTable = (title, rows, keyFn) => rows.length ? `
    <div style="font-weight:600;font-size:13px;margin:12px 0 6px">${title}</div>
    <div class="ov-auto">
      <table>
        <thead><tr>
          <th class="tl">รายการ</th>
          <th class="tr">จำนวนใช้</th>
          <th class="tr">มูลค่า</th>
        </tr></thead>
        <tbody>${rows.map(r=>`<tr>
          <td>${esc(keyFn(r))}</td>
          <td class="mono tr">${fmt(r.used_count)}</td>
          <td class="mono tr">${fmt(r.discount_amount)}</td>
        </tr>`).join('')}</tbody>
      </table>
    </div>` : '';

  box.innerHTML = `<div class="card mb-12">
    <div class="row mb-8">
      <div>
        <b><i class="ti ti-ticket" aria-hidden="true"></i> ${nm?nm+' ':''}<span class="lbl">#${seriesId}</span> · ${esc((d&&d.label)||MONTH)}</b>
        <span class="lbl ml-8">รวม ${fmt((d&&d.total)||0)} · ${fmt((d&&d.total_used)||0)} ใบ · MoM ${_cpMom(mom)}</span>
      </div>
      <button class="btn btn-sm ml-auto" onclick="$('cp-drill').innerHTML=''">✕ ปิด</button>
    </div>
    ${(brows.length||drows.length)
      ? subTable('แยกรายสาขา', brows, r=>r.branch_name) + subTable('แยกรายวัน', drows, r=>r.day)
      : '<div class="muted">ไม่มีข้อมูลสำหรับ series นี้</div>'}
  </div>`;
  box.scrollIntoView({behavior:_scrollBehavior(), block:'nearest'});
}

/* ════════════════════════════════════════════════════════════════════════
   § PAYOUT MATCHING VIEW — Phase 3a consolidation
   Route: #payout?bid=XXX  (reads MONTH global)
   Replaces classic.html openPayoutMatch + renderPayouts for ALL categories.
   Read-only: no backend changes. Uses /api/payout-matching (existing endpoint).
   ════════════════════════════════════════════════════════════════════════ */

const _PCAT_LBL   = {BAYPOINT:'BAY Point',KTCPOINT:'KTC Point',GRAB:'Grab',LINEMAN:'Line Man',CREDIT:'บัตรเครดิต'};
const _PCAT_ORDER = ['CREDIT','GRAB','LINEMAN','BAYPOINT','KTCPOINT'];

async function loadPayout(h){
  const params = new URLSearchParams(h.includes('?')?h.slice(h.indexOf('?')):'');
  const bid = parseInt(params.get('bid')||'0');
  if(!bid){
    $('app').innerHTML=header()+'<div class="card c-err mt-12">ไม่พบสาขา — กลับ <a href="#">หน้าแรก</a></div>';
    return;
  }
  const t=++_ROUTE_SEQ;
  $('app').innerHTML=header(`<a href="#b/${bid}" class="lbl">← สาขา</a>`)+loadingState();

  let d=null;
  try{ d=await api(`/api/payout-matching?branch_id=${bid}&period_month=${MONTH}`); }
  catch(e){
    if(_cur(t)) $('app').innerHTML=header(`<a href="#b/${bid}" class="lbl">← สาขา</a>`)
      +`<div class="card c-err mt-12">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`;
    return;
  }
  if(!_cur(t)) return;

  const cats = _PCAT_ORDER.filter(c=>d&&d[c]&&(d[c].payouts||[]).length);
  if(!cats.length){
    $('app').innerHTML=header(`<a href="#b/${bid}" class="lbl">← สาขา</a>`)
      +`<h2 class="my-14-10"><i class="ti ti-credit-card" aria-hidden="true"></i> ตรวจเงินเข้าธนาคาร · ${MONTH}</h2>
        `+emptyState('cash-banknote-off','ไม่มีข้อมูล payout ในเดือนนี้','ยังไม่มีเงินโอนเข้าธนาคารที่ต้องตรวจสำหรับสาขา/เดือนนี้');
    return;
  }

  let netTot=0,payTot=0,awaitTot=0;
  cats.forEach(c=>{ const cd=d[c]; netTot+=cd.net_total||0; payTot+=cd.payout_total||0; awaitTot+=cd.awaiting_total||0; });

  const cards = cats.map(cat=>{
    const cd=d[cat];
    const lbl=_PCAT_LBL[cat]||cat;

    if(cd.bank_in_credit){
      const pointTot=cd.pos_point_total||0, netV=cd.net_total||0;
      const diff=netV-pointTot;
      return `<div class="card mb-10">
        <div class="row mb-8">
          <b>${lbl}</b>
          <span class="chip c-ok ml-8">รวมในยอดบัตร</span>
        </div>
        <div class="row gap-24 fx-wrap">
          <div><div class="lbl">POS แต้ม</div><div class="mono">${fmt(pointTot)}</div></div>
          <div><div class="lbl">ยอดสุทธิรายงาน</div><div class="mono">${fmt(netV)}</div></div>
          <div><div class="lbl">ต่าง</div><div class="mono" style="color:${Math.abs(diff)>1?'var(--err)':'var(--ok)'}">${diff>0?'+':''}${fmt(diff)}</div></div>
        </div>
        <div class="lbl mt-6">แต้ม KTC จ่ายผ่าน KTC โดยตรง — ตรวจในยอดสรุปบัตรเครดิต KTC (ไม่มีการโอนแยก)</div>
      </div>`;
    }

    const awaitBadge = cd.awaiting_total>0
      ? `<span class="chip c-warn ml-8">รอ ${fmt(cd.awaiting_total)}</span>` : '';
    const payRows = (cd.payouts||[]).map(p=>{
      const dayList=p.days.map(x=>`${+x.day.slice(8)}`).join('+');
      const netSum=(p.days||[]).reduce((a,x)=>a+(x.net||0),0);
      if(p.status==='awaiting'){
        return `<tr>
          <td><span class="chip c-warn">รอเงินเข้า</span></td>
          <td class="mono tr">${fmt(netSum)}</td>
          <td class="t-mut tr">—</td>
          <td class="t-mut">—</td>
          <td><span class="lbl">วัน ${dayList}</span></td>
        </tr>`;
      }
      return `<tr>
        <td><span class="chip c-ok">✓ ตรง</span></td>
        <td class="mono tr">${fmt(netSum)}</td>
        <td class="mono tr">${p.amount?fmt(p.amount):'—'}</td>
        <td>${p.payout_date?p.payout_date.slice(5):'—'}${p.lag?` <span class="lbl">ช้า ${p.lag} วัน</span>`:''}</td>
        <td><span class="lbl">วัน ${dayList}${p.combined?` (รวม ${p.days.length} วัน)`:''}</span></td>
      </tr>`;
    }).join('');

    return `<div class="card mb-10">
      <div class="row mb-8">
        <b>${lbl}</b>${awaitBadge}
        <span class="lbl ml-auto">สุทธิ ${fmt(cd.net_total||0)} · เข้าแล้ว ${fmt(cd.payout_total||0)}</span>
      </div>
      <div class="ov-auto">
        <table>
          <thead><tr>
            <th>สถานะ</th>
            <th class="tr">ยอดสุทธิ</th>
            <th class="tr">เงินโอน</th>
            <th>วันที่โอน</th>
            <th>วันขาย</th>
          </tr></thead>
          <tbody>${payRows}</tbody>
        </table>
      </div>
    </div>`;
  }).join('');

  $('app').innerHTML = header(`<a href="#b/${bid}" class="lbl">← สาขา</a>`) + `
    <h2 class="my-14-10"><i class="ti ti-credit-card" aria-hidden="true"></i> ตรวจเงินเข้าธนาคาร · ${MONTH}</h2>
    <div class="card row" style="margin-bottom:14px;flex-wrap:wrap;gap:20px">
      <div><div class="lbl">ยอดสุทธิรวม</div><div class="mono fs-18">${fmt(netTot)}</div></div>
      <div><div class="lbl">เข้าบัญชีแล้ว</div><div class="mono fs-18 t-ok">${fmt(payTot)}</div></div>
      ${awaitTot>0?`<div><div class="lbl">รอเงินเข้า</div><div class="mono fs-18 t-warn">${fmt(awaitTot)}</div></div>`:''}
    </div>
    ${cards}`;
}

/* ════════════════════════════════════════════════════════════════════════
   § DISCREPANCIES ADMIN VIEW — Phase 3b consolidation
   Route: #discrepancies?bid=XXX  (reads MONTH global)
   Migrates classic.html loadDiscrepancies (~2915): filter/sort/bulk-resolve/
   per-row resolve+assign/drill-per-day.  Exists ALONGSIDE dayWorklistHtml
   (day-ack per channel in branch page) — not a replacement.
   Write ops use api() → auto-attaches X-Upload-Token (write token).
   ════════════════════════════════════════════════════════════════════════ */

let _DISC_SID  = null;
let _DISC_BID  = 0;
let _DISC_DATA = [];
let _DISC_SEL  = new Set();
let _DISC_FLT  = {resolution:'pending',category:'',bank:''};
let _DISC_SORT = {field:'variance',dir:-1};

async function loadDiscrepancies(h){
  const params = new URLSearchParams(h.includes('?')?h.slice(h.indexOf('?')):'');
  _DISC_BID = parseInt(params.get('bid')||'0');
  _DISC_FLT = {resolution:'pending',category:'',bank:''};
  _DISC_SORT = {field:'variance',dir:-1};
  if(!_DISC_BID){
    $('app').innerHTML=header()+'<div class="card c-err mt-12">ไม่พบสาขา — กลับ <a href="#">หน้าแรก</a></div>';
    return;
  }
  const t=++_ROUTE_SEQ;
  $('app').innerHTML=header(`<a href="#b/${_DISC_BID}" class="lbl">← สาขา</a>`)+loadingState();

  let sid;
  try{ sid = await _resolveSession(_DISC_BID, MONTH); }
  catch(e){ if(_cur(t)) $('app').innerHTML=header(`<a href="#b/${_DISC_BID}" class="lbl">← สาขา</a>`)+`<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`; return; }
  if(!_cur(t)) return;
  _DISC_SID = sid;
  if(!_DISC_SID){
    $('app').innerHTML=header(`<a href="#b/${_DISC_BID}" class="lbl">← สาขา</a>`)
      +emptyState('adjustments-off',`ยังไม่มี session สำหรับเดือน ${MONTH}`,'เปิดหน้าสาขาเพื่อเริ่มกระทบยอดก่อน แล้วจัดการข้อแตกต่างได้ที่นี่',`<a href="#b/${_DISC_BID}" class="btn btn-pri btn-sm">เปิดหน้าสาขาก่อน</a>`);
    return;
  }
  await _discFetch(t);
}

async function _discFetch(t){
  if(!_DISC_SID) return;
  if(t==null) t=++_ROUTE_SEQ;      // เรียกจากตัวกรองบนหน้าเดียวกัน = รอบโหลดใหม่
  let q=`/api/sessions/${_DISC_SID}/discrepancies?resolution=${encodeURIComponent(_DISC_FLT.resolution||'all')}`;
  if(_DISC_FLT.category) q+=`&category=${encodeURIComponent(_DISC_FLT.category)}`;
  if(_DISC_FLT.bank)     q+=`&bank=${encodeURIComponent(_DISC_FLT.bank)}`;
  let data;
  try{ data=await api(q); }
  catch(e){
    // โหลดไม่สำเร็จ ≠ ไม่มีรายการ — แสดงข้อผิดพลาด + ลองใหม่
    if(_cur(t)) $('app').innerHTML=header(`<a href="#b/${_DISC_BID}" class="lbl">← สาขา</a>`)
      +`<div class="card c-err mt-12">โหลดข้อแตกต่างไม่สำเร็จ: ${esc(e.message)}
        <button class="btn btn-sm ml-8" onclick="_discFetch()">ลองใหม่</button></div>`;
    return;
  }
  if(!_cur(t)) return;
  _DISC_DATA=data;
  _DISC_SEL.clear();
  _discRender();
}

function _discRender(){
  const sorted=[..._DISC_DATA].sort((a,b)=>{
    const fa=Math.abs(a[_DISC_SORT.field]??0), fb=Math.abs(b[_DISC_SORT.field]??0);
    return _DISC_SORT.dir*(fa<fb?-1:fa>fb?1:0);
  });
  const pendingN=_DISC_DATA.filter(d=>d.resolution==='pending').length;
  const varCls=v=>Math.abs(v)>100?'var(--err)':Math.abs(v)>5?'var(--warn)':'var(--ok)';
  const resCls=r=>r==='pending'?'c-warn':r==='accepted'?'c-ok':'c-mut';
  const srtArrow=f=>_DISC_SORT.field===f?(_DISC_SORT.dir>0?' ▲':' ▼'):'';
  const th=(f,lbl)=>`<th class="pointer no-select nowrap" role="button" tabindex="0" onclick="_discSortBy('${f}')">${lbl}${srtArrow(f)}</th>`;

  const rows=sorted.length?sorted.map(d=>{
    const isPending=d.resolution==='pending';
    const resSelect=isPending
      ?`<select style="font-size:12px;padding:2px 5px;border:1px solid var(--line);border-radius:var(--radius-sm);background:var(--input)"
           onchange="_discResolve(${d.id},this.value,this)">
           <option value="">แก้ไข…</option>
           <option value="accepted">✓ ยอมรับ</option>
           <option value="adjusted">~ ปรับแล้ว</option>
           <option value="rejected">✕ ปฏิเสธ</option>
         </select>`
      :`<span class="chip ${resCls(d.resolution)} fs-11">${esc(d.resolution)}</span>`;
    return `<tr data-disc-id="${d.id}">
      <td style="text-align:center;padding:6px 4px 6px 10px">
        <input type="checkbox" class="disc-chk" value="${d.id}" ${_DISC_SEL.has(d.id)?'checked':''}
          onchange="_discToggle(${d.id},this.checked)"></td>
      <td class="fw-500 nowrap">${esc(d.category||'—')}</td>
      <td class="t-sub fs-12">${d.bank&&d.bank!=='NONE'?esc(d.bank):'—'}</td>
      <td class="fs-11 t-mut">${esc(d.disc_type||'—')}</td>
      <td class="mono" style="text-align:right;cursor:pointer;user-select:none;color:${varCls(d.variance||0)}"
          role="button" tabindex="0" onclick="_discDrillDays(${d.id},this)" title="คลิกดูแยกรายวัน">▾ ${d.variance>0?'+':''}${fmt(d.variance||0)}</td>
      <td class="mono tr t-sub">${fmt(d.amount_external||0)}</td>
      <td class="mono tr">${fmt(d.amount_pos||0)}</td>
      <td><input style="font-size:12px;padding:3px 6px;border:1px solid var(--line);border-radius:var(--radius-sm);background:var(--input);width:90px"
           placeholder="มอบหมาย…" value="${esc(d.assigned_to||'')}"
           onchange="_discAssign(${d.id},this.value)"></td>
      <td>${resSelect}</td>
    </tr>`;
  }).join('')
  :`<tr><td colspan="9" class="cell-empty">
      ไม่มีรายการ${_DISC_FLT.resolution!=='all'?' ('+esc(_DISC_FLT.resolution)+')':''}
    </td></tr>`;

  const html=`
    <div class="row my-14-10 fx-wrap gap-8">
      <h2 class="fs-16"><i class="ti ti-settings" aria-hidden="true"></i> จัดการข้อแตกต่าง · ${MONTH}</h2>
      ${pendingN?`<span class="chip c-warn">${pendingN} รอแก้ไข</span>`:'<span class="chip c-ok">ครบแล้ว</span>'}
      <a href="#b/${_DISC_BID}" class="btn btn-sm ml-auto">← สาขา</a>
    </div>
    <div class="card mb-10 pad-10-14">
      <div class="row gap-8 fx-wrap ai-center">
        <span class="lbl">แสดง:</span>
        <select class="ctl-sm"
          onchange="_DISC_FLT.resolution=this.value;_discFetch()">
          <option value="pending" ${_DISC_FLT.resolution==='pending'?'selected':''}>รอแก้ไข</option>
          <option value="accepted" ${_DISC_FLT.resolution==='accepted'?'selected':''}>ยอมรับแล้ว</option>
          <option value="adjusted" ${_DISC_FLT.resolution==='adjusted'?'selected':''}>ปรับแล้ว</option>
          <option value="rejected" ${_DISC_FLT.resolution==='rejected'?'selected':''}>ปฏิเสธ</option>
          <option value="all" ${_DISC_FLT.resolution==='all'?'selected':''}>ทุกสถานะ</option>
        </select>
        <input class="ctl-sm w-110"
          placeholder="ช่องทาง…" value="${esc(_DISC_FLT.category)}"
          oninput="_DISC_FLT.category=this.value"
          onkeydown="if(event.key==='Enter')_discFetch()">
        <input class="ctl-sm w-90"
          placeholder="ธนาคาร…" value="${esc(_DISC_FLT.bank)}"
          oninput="_DISC_FLT.bank=this.value"
          onkeydown="if(event.key==='Enter')_discFetch()">
        <button class="btn btn-sm" onclick="_discFetch()">ค้นหา</button>
        ${(_DISC_FLT.category||_DISC_FLT.bank)?`<button class="btn btn-sm" onclick="_DISC_FLT.category='';_DISC_FLT.bank='';_discFetch()">✕ ล้าง</button>`:''}
      </div>
    </div>
    <div class="card" style="padding:0;overflow:auto;margin-bottom:80px">
      <table>
        <thead><tr>
          <th class="pl-10"><input type="checkbox" id="disc-chk-all" onchange="_discToggleAll(this.checked)"></th>
          <th class="tl">ช่องทาง</th>
          <th class="tl">ธนาคาร</th>
          <th class="tl">ประเภท</th>
          ${th('variance','ต่าง (฿)')}
          <th class="tr">เงินเข้า</th>
          <th class="tr">POS</th>
          <th>มอบหมาย</th>
          <th>การแก้ไข</th>
        </tr></thead>
        <tbody id="disc-tbody">${rows}</tbody>
      </table>
    </div>
    <div id="disc-bulk-bar" style="position:fixed;left:0;right:0;background:var(--brand);color:var(--on-brand);padding:10px 16px;display:none;z-index:200">
      <div class="row" style="gap:10px;max-width:900px;margin:0 auto;flex-wrap:wrap">
        <span id="disc-bulk-count" class="fw-600 as-center"></span>
        <button class="btn bulk-btn" onclick="_discBulk('accepted',this)">✓ ยอมรับทั้งหมด</button>
        <button class="btn bulk-btn" onclick="_discBulk('adjusted',this)">~ ปรับแล้ว</button>
        <button class="btn bulk-btn-q" onclick="_discBulk('rejected',this)">✕ ปฏิเสธ</button>
        <button class="btn btn-sm bulk-btn-x ml-auto" onclick="_discClearSel()">ยกเลิก</button>
      </div>
    </div>`;

  $('app').innerHTML=header(`<a href="#b/${_DISC_BID}" class="lbl">← สาขา</a>`)+html;
  _discUpdateBulkBar();
}

function _discSortBy(field){
  if(_DISC_SORT.field===field) _DISC_SORT.dir*=-1;
  else { _DISC_SORT.field=field; _DISC_SORT.dir=-1; }
  _discRender();
}

function _discToggle(id,checked){
  if(checked) _DISC_SEL.add(id); else _DISC_SEL.delete(id);
  _discUpdateBulkBar();
}
function _discToggleAll(checked){
  document.querySelectorAll('.disc-chk').forEach(cb=>{
    cb.checked=checked;
    const id=+cb.value;
    if(checked) _DISC_SEL.add(id); else _DISC_SEL.delete(id);
  });
  _discUpdateBulkBar();
}
function _discClearSel(){
  _DISC_SEL.clear();
  document.querySelectorAll('.disc-chk').forEach(cb=>cb.checked=false);
  const all=$('disc-chk-all'); if(all) all.checked=false;
  _discUpdateBulkBar();
}
function _discUpdateBulkBar(){
  const bar=$('disc-bulk-bar'); if(!bar) return;
  const cnt=$('disc-bulk-count');
  if(_DISC_SEL.size){ bar.style.display='block'; if(cnt) cnt.textContent=`${_DISC_SEL.size} รายการที่เลือก`; }
  else bar.style.display='none';
}

// ผู้แก้ไข (resolved_by) ไม่ส่งจากหน้าเว็บ — เซิร์ฟเวอร์บันทึกผู้ใช้ที่ล็อกอินอยู่เอง
async function _discBulk(resolution,btn){
  if(!_DISC_SEL.size||!_DISC_SID) return;
  const ids=[..._DISC_SEL];
  await busy('disc-bulk',btn,async()=>{
    try{
      const r=await api(`/api/sessions/${_DISC_SID}/discrepancies/bulk-resolve`,
        {method:'POST',body:{ids,resolution}});
      toast(`${r.updated??ids.length} รายการ → "${resolution}"`);
      _DISC_FLT.resolution='pending';
      await _discFetch();
    }catch(e){ toast('ไม่สำเร็จ: '+e.message); hint403(e); }
  });
}

async function _discResolve(id,res,sel){
  if(!res) return;
  await busy('disc-'+id,sel,async()=>{
    try{
      await api(`/api/discrepancies/${id}/resolve`,{method:'PATCH',body:{resolution:res}});
      await _discFetch();
    }catch(e){ toast('ไม่สำเร็จ: '+e.message); hint403(e); if(sel&&sel.isConnected)sel.value=''; }
  });
}

async function _discAssign(id,to){
  // backend รับเป็นฟอร์ม (Form assigned_to) — ส่ง JSON จะได้ 422
  const fd=new FormData(); fd.append('assigned_to',to);
  try{
    await api(`/api/discrepancies/${id}/assign`,{method:'PATCH',body:fd});
    toast(to?`มอบหมายให้ ${to} แล้ว`:'ล้างผู้รับผิดชอบแล้ว');
  }catch(e){ toast('บันทึกไม่สำเร็จ: '+e.message); hint403(e); }
}

async function _discDrillDays(discId,cell){
  const tbody=$('disc-tbody'); if(!tbody) return;
  const parentTr=cell.closest('tr');
  const expandId=`disc-days-${discId}`;
  const existing=document.getElementById(expandId);
  if(existing){ existing.remove(); return; }

  const colspan=parentTr.children.length;
  const expTr=document.createElement('tr');
  expTr.id=expandId;
  expTr.style.background='var(--subtle)';
  expTr.innerHTML=`<td colspan="${colspan}" style="padding:10px 24px;color:var(--mut);font-size:13px">กำลังโหลด…</td>`;
  parentTr.after(expTr);

  try{
    const r=await api(`/api/discrepancies/${discId}/days`);
    const days=r.days||[];
    if(!days.length){
      expTr.querySelector('td').innerHTML='<span class="t-mut">ไม่มีข้อมูลรายวัน</span>';
      return;
    }
    const dayRows=days.map(d=>{
      const clr=Math.abs(d.variance||0)>0.5?'var(--err)':'var(--ok)';
      const stCls=d.status==='matched'?'c-ok':d.status==='diff'?'c-err':'c-warn';
      return `<tr class="bdt-soft">
        <td class="pad-4-10 ff-mono fs-12 t-sub">${esc(d.day)}</td>
        <td class="pad-4-10 tr ff-mono fs-12">${fmt(d.pos||0)}</td>
        <td class="pad-4-10 tr ff-mono fs-12 t-sub">${fmt(d.ext||0)}</td>
        <td style="padding:4px 10px;text-align:right;font-family:monospace;font-size:12px;color:${clr}">${(d.variance||0)>0?'+':''}${fmt(d.variance||0)}</td>
        <td class="pad-4-10"><span class="chip ${stCls} fs-11">${esc(d.status||'—')}</span></td>
      </tr>`;
    }).join('');
    expTr.innerHTML=`<td colspan="${colspan}" class="p-0">
      <table class="w-full">
        <thead><tr class="bg-hover fs-11 t-mut">
          <th class="pad-4-10 tl fw-500">วันที่</th>
          <th class="pad-4-10 tr fw-500">POS</th>
          <th class="pad-4-10 tr fw-500">เงินเข้า</th>
          <th class="pad-4-10 tr fw-500">ต่าง</th>
          <th class="pad-4-10 fw-500">สถานะ</th>
        </tr></thead>
        <tbody>${dayRows}</tbody>
      </table></td>`;
  }catch(e){ expTr.querySelector('td').innerHTML=`<span class="t-err">${esc(e.message)}</span>`; }
}
