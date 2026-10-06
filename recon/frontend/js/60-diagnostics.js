/* recon frontend — 60-diagnostics.js: #audit #trend #bankcov #board.
   Plain scripts sharing one global scope, loaded in order by index.html (00 → 90);
   a slice may call any other slice's functions, but top-level code only uses earlier slices. */
/* ════════════════════════════════════════════════════════════════════════
   § DIAGNOSTIC VIEWS (Phase 1 consolidation)
   Routes: #audit · #trend · #bankcov · #board
   All read-only — no backend changes.
   ════════════════════════════════════════════════════════════════════════ */

/* ── #audit — Audit log (global, read-only) ─────────────────────────────── */
let _AUDIT_ENTITY = '';

async function loadAudit(){
  const t=++_ROUTE_SEQ;
  $('app').innerHTML = header() + loadingState();
  const ACTION_CLS = {create:'c-ok', update:'c-mut', delete:'c-err'};
  const entityOpts = ['','session','discrepancy','manual_match','day_ack','channel_move','payment_map']
    .map(e => `<option value="${e}" ${_AUDIT_ENTITY===e?'selected':''}>${e||'ทุก entity'}</option>`).join('');

  let q = `/api/audit?limit=200`;
  if(_AUDIT_ENTITY) q += `&entity=${encodeURIComponent(_AUDIT_ENTITY)}`;
  let data = [];
  try{ data = await api(q); }
  catch(e){ if(_cur(t)) $('app').innerHTML = header() + `<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`; return; }
  if(!_cur(t)) return;

  const rows = data.length
    ? data.map(r => {
        const changeHtml = r.field
          ? `<span class="t-err strike fs-12">${esc(String(r.old_val??'—'))}</span>`
            +`<span style="color:var(--mut);margin:0 4px">→</span>`
            +`<span class="t-ok fs-12">${esc(String(r.new_val??'—'))}</span>`
          : (r.old_val||r.new_val ? esc(String(r.old_val??''))+' '+esc(String(r.new_val??'')) : '—');
        return `<tr>
          <td class="lbl nowrap">${esc(r.ts||'')}</td>
          <td><span class="chip c-mut fs-11">${esc(r.entity||'')}</span></td>
          <td class="lbl">${esc(String(r.entity_id??''))}</td>
          <td><span class="chip ${ACTION_CLS[r.action]||'c-mut'} fs-11">${esc(r.action||'')}</span></td>
          <td class="lbl mono">${esc(r.field??'—')}</td>
          <td style="font-size:12px;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${changeHtml}</td>
          <td class="lbl">${esc(r.by||'')}</td>
        </tr>`;
      }).join('')
    : `<tr><td colspan="7" class="cell-empty">ยังไม่มีรายการ</td></tr>`;

  $('app').innerHTML = header() + `
    <div class="row mb-10">
      <h2 class="fs-16"><i class="ti ti-clipboard-list" aria-hidden="true"></i> Audit Log — ประวัติการแก้ไข</h2>
      <select onchange="_AUDIT_ENTITY=this.value;loadAudit()" title="กรอง entity"
        class="ctl ml-auto">${entityOpts}</select>
    </div>
    <div class="muted mb-10">ทุกการเปลี่ยนแปลงในระบบ — บันทึกอัตโนมัติ · แสดงล่าสุด 200 รายการ · กรองด้วย entity ด้านขวา</div>
    <div class="card p-0 ov-auto">
      <table>
        <thead><tr>
          <th>เวลา</th><th>Entity</th><th>ID</th><th>Action</th><th>Field</th><th>เปลี่ยนจาก → ไปเป็น</th><th>โดย</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

/* ── #trend — Trend heatmap (session-scoped: branch dropdown) ───────────── */
let _TREND_BID = 0;

async function loadTrend(){
  const t=++_ROUTE_SEQ;
  $('app').innerHTML = header() + loadingState();
  const branches = await _diagBranches();
  if(!_cur(t)) return;
  if(!_TREND_BID && branches.length) _TREND_BID = branches[0].branch_id;

  let sid;
  try{ sid = await _resolveSession(_TREND_BID, MONTH); }
  catch(e){ if(_cur(t)) $('app').innerHTML = header() + `<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`; return; }
  if(!_cur(t)) return;
  const picker = _diagBranchPicker(branches, _TREND_BID, '_TREND_BID=+this.value;loadTrend()');

  if(!sid){
    $('app').innerHTML = header() + `
      <div class="row mb-10">
        <h2 class="fs-16"><i class="ti ti-trending-up" aria-hidden="true"></i> Trend Heatmap — ความคืบหน้ารายวัน</h2>
        ${picker}
      </div>
      `+emptyState('chart-grid-dots','ยังไม่มี session ของเดือนนี้','เปิดหน้าสาขาเพื่อเริ่มกระทบยอดก่อน แล้ว Trend จะแสดงความคืบหน้ารายวัน',`<a href="#b/${_TREND_BID}" class="btn btn-pri btn-sm">เปิดหน้าสาขา</a>`);
    return;
  }

  let data;
  try{ data = await api(`/api/sessions/${sid}/trend`); }
  catch(e){
    if(_cur(t)) $('app').innerHTML = header() + `<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`;
    return;
  }
  if(!_cur(t)) return;

  const brName = (branches.find(b=>b.branch_id==_TREND_BID)||{}).name||'';

  // weekly table
  const weekly = (data.weekly||[]);
  const pctBar = (pct) => {
    const col = pct>=99?'var(--ok)':pct>=60?'var(--warn)':'var(--err)';
    return `<div class="d-flex ai-center gap-8">
      <div class="prog ${pct>=99?'':pct>=60?'p-warn':'p-err'}" style="flex:1;min-width:80px"><div style="width:${pct}%"></div></div>
      <span style="color:${col};font-weight:700;min-width:32px;font-size:13px">${pct}%</span>
    </div>`;
  };
  const weekRows = weekly.map(w => `<tr>
    <td class="fw-600">${esc(w.week)}</td>
    <td class="mono">${fmt(w.pos)}</td>
    <td class="mono">${fmt(w.ext)}</td>
    <td class="minw-160">${pctBar(w.lane_pct)}</td>
    <td class="minw-160">${pctBar(w.cov_pct)}</td>
  </tr>`).join('');

  // daily heatmap cells
  const daily = (data.daily||[]);
  const cells = daily.map(d => {
    const pct = d.lane_pct;
    const bg  = pct>=99?'var(--ok-bg)':pct>=60?'var(--warn-bg)':d.total_lanes?'var(--err-bg)':'var(--line-soft)';
    const col = pct>=99?'var(--ok)':pct>=60?'var(--warn)':d.total_lanes?'var(--err)':'var(--mut)';
    const dayN = d.day.slice(8);
    const tip = `${d.day}\nLane match: ${pct}% (${d.matched}/${d.total_lanes})\nCoverage: ${d.cov_pct}%\nPOS: ${fmt(d.pos)} | Ext: ${fmt(d.ext)}`;
    return `<div class="daycell" title="${esc(tip)}"
      style="background:${bg};color:${col};border-color:${bg};cursor:default;text-align:center;padding:8px 4px">
      <b class="fs-14">${dayN}</b>
      <div class="fs-11 mt-2 fw-700">${pct}%</div>
      <div class="lbl fs-10">${d.matched}/${d.total_lanes}</div>
    </div>`;
  }).join('');

  $('app').innerHTML = header() + `
    <div class="row mb-10">
      <h2 class="fs-16"><i class="ti ti-trending-up" aria-hidden="true"></i> Trend Heatmap — ความคืบหน้ารายวัน</h2>
      ${picker}
    </div>
    <div class="muted mb-10">${esc(brName)} · ${MONTH} · สีเขียว=ตรงครบ สีเหลือง=ตรงบางส่วน สีแดง=ยังขาด · คลิก "เปิดหน้าสาขา" เพื่อแก้ไข</div>
    <div class="card mb-14">
      <div class="row mb-8">
        <b class="fs-135">สรุปรายสัปดาห์</b>
        <a class="btn btn-sm ml-auto" href="#b/${_TREND_BID}">→ เปิดหน้าสาขา</a>
      </div>
      ${weekly.length
        ? `<div class="ovx-auto"><table><thead><tr>
            <th>สัปดาห์</th><th>POS</th><th>เงินเข้า</th>
            <th class="minw-160">Lane match</th>
            <th class="minw-160">Coverage</th>
           </tr></thead><tbody>${weekRows}</tbody></table></div>`
        : '<div class="lbl tc pad-16">ยังไม่มีข้อมูลรายวัน</div>'}
    </div>
    <div class="card">
      <b class="fs-135 d-block mb-10">ปฏิทินรายวัน (% lane match)</b>
      ${daily.length
        ? `<div class="daygrid">${cells}</div>`
        : '<div class="lbl tc pad-16">ยังไม่มีข้อมูลรายวัน</div>'}
    </div>`;
}

/* ── #bankcov — Bank coverage (session-scoped: branch dropdown) ─────────── */
let _BANKCOV_BID = 0;
let _BANKCOV_FILTER = 'unmatched';
let _BANKCOV_DATA = null;
let _BANKCOV_CTX = null;           // {branches,picker,sid} ของหน้าที่แสดงอยู่ — ปุ่มกรองใช้วาดใหม่
const _BC_LANE = {CASH:'เงินสด/Cash',QR:'QR',TRANSFER:'โอน/Transfer',CREDIT:'บัตร/Credit',
                  GRAB:'Grab',LINEMAN:'Line Man',OTHER:'อื่นๆ/Other',excluded:'ตัดออก/Ignored'};
const _BC_LINE_ST = {matched:['c-ok','✓'],unmatched:['c-warn','<i class="ti ti-hourglass-low" aria-hidden="true"></i>'],excluded:['c-mut','⊘'],informational:['c-mut','·']};
const _BC_LEG = {matched:['c-ok','✓'],mismatch:['c-err','≠'],pos_only:['c-warn','POS'],
                 ext_only:['c-warn','+'],awaiting:['c-warn','…'],no_bank:['c-mut','—'],none:['c-mut','·']};
function _bcLegChip(st){ const [c,s]=_BC_LEG[st]||['c-mut','·']; return `<span class="chip ${c} fs-11">${s}</span>`; }

async function loadBankCov(){
  const t=++_ROUTE_SEQ;
  $('app').innerHTML = header() + loadingState();
  const branches = await _diagBranches();
  if(!_cur(t)) return;
  if(!_BANKCOV_BID && branches.length) _BANKCOV_BID = branches[0].branch_id;

  let sid;
  try{ sid = await _resolveSession(_BANKCOV_BID, MONTH); }
  catch(e){ if(_cur(t)) $('app').innerHTML = header() + `<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`; return; }
  if(!_cur(t)) return;
  const picker = _diagBranchPicker(branches, _BANKCOV_BID, '_BANKCOV_BID=+this.value;loadBankCov()');
  _BANKCOV_DATA = null; _BANKCOV_CTX = null;   // ไม่ใช้ข้อมูลของเดือน/สาขาก่อนหน้า

  if(!sid){
    $('app').innerHTML = header() + `
      <div class="row mb-10">
        <h2 class="fs-16"><i class="ti ti-building-bank" aria-hidden="true"></i> Bank Coverage — สถานะครอบคลุมสเตทเมนต์</h2>
        ${picker}
      </div>
      `+emptyState('building-bank','ยังไม่มี session ของเดือนนี้','เปิดหน้าสาขาเพื่อเริ่มกระทบยอดก่อน แล้วสถานะครอบคลุมสเตทเมนต์จะแสดงที่นี่',`<a href="#b/${_BANKCOV_BID}" class="btn btn-pri btn-sm">เปิดหน้าสาขา</a>`);
    return;
  }

  let data;
  try{ data = await api(`/api/sessions/${sid}/bank-coverage`); }
  catch(e){
    if(_cur(t)) $('app').innerHTML = header() + `<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`;
    return;
  }
  if(!_cur(t)) return;
  _BANKCOV_DATA = data; _BANKCOV_CTX = {branches, picker, sid};
  _renderBankCov(branches, picker, sid);
}
function _bcSetFilter(k){
  _BANKCOV_FILTER = k;
  const c = _BANKCOV_CTX; if(c) _renderBankCov(c.branches, c.picker, c.sid);
}

function _renderBankCov(branches, picker, sid){
  const d = _BANKCOV_DATA||{};
  const tot = d.totals||{};
  const pct = tot.pct_matched ?? 100;
  const hCol = pct>=99?'c-ok':pct>=70?'c-warn':'c-err';
  const brName = (branches.find(b=>b.branch_id==_BANKCOV_BID)||{}).name||'';

  // filter buttons
  const fOpts = [
    ['unmatched',`<i class="ti ti-hourglass-low" aria-hidden="true"></i> ยังรอ`,tot.unmatched||0],
    ['matched',`✓ ตรงแล้ว`,tot.matched||0],
    ['excluded',`⊘ ตัดออก`,tot.excluded||0],
    ['all','ทั้งหมด',(tot.matched||0)+(tot.unmatched||0)+(tot.excluded||0)+(tot.informational||0)]
  ].filter(([k,,n])=>n>0||k==='all')
   .map(([k,lbl,n])=>`<button onclick="_bcSetFilter('${k}')"
     class="btn btn-sm ${_BANKCOV_FILTER===k?'btn-pri':''}">${lbl} ${n}</button>`).join('');

  // CREDIT 3-way cards
  const line=(lbl,val)=>`<div class="row jc-between fs-13"><span class="t-sub">${lbl}</span><span class="mono">${fmt(val)}</span></div>`;
  const ccRows = (d.credit_three_way||[]).filter(r=>r.pos_gross>0||r.settle_gross>0||r.bank_credit>0);
  const ccHtml = ccRows.length ? ccRows.map(r=>`
    <div class="card mb-8">
      <div class="fw-600 mb-6">CREDIT · ${esc(r.bank)}</div>
      ${line('ยอดรูด POS',r.pos_gross)}
      ${line('สรุปยอดบัตร (gross)',r.settle_gross)}
      <div class="row jc-between fs-12 pt-4 mt-4 bdt-soft">
        <span class="lbl">ขั้น 1: POS ↔ สรุปยอด</span>${_bcLegChip(r.pos_leg)}</div>
      ${line('− ค่าธรรมเนียม+VAT',r.settle_fee)}
      ${line('ยอดสุทธิ',r.settle_net)}
      ${line('เงินเข้าธนาคาร',r.bank_credit)}
      <div class="row jc-between fs-12 pt-4 mt-4 bdt-soft">
        <span class="lbl">ขั้น 2: ยอดสุทธิ ↔ เงินเข้า</span>${_bcLegChip(r.bank_leg)}</div>
      ${r.settle_gross===0&&r.pos_gross>0?`<div class="chip c-warn mt-6 fs-11"><i class="ti ti-alert-triangle" aria-hidden="true"></i> ยังไม่อัปสรุปยอดบัตร</div>`:''}
      ${r.settle_net>100&&r.bank_credit===0?`<div class="chip c-warn mt-6 fs-11"><i class="ti ti-alert-triangle" aria-hidden="true"></i> ยังไม่พบเงินเข้าธนาคาร</div>`:''}
    </div>`).join('')
    : '<div class="lbl">— ไม่มีบัตรเครดิตในเดือนนี้</div>';

  // per-lane chips
  const grpHtml = Object.entries(d.groups||{})
    .map(([k,g])=>`<span class="chip ${g.unmatched?'c-warn':'c-ok'} mr-4 mb-4">${esc(_BC_LANE[k]||k)}: ${g.matched}/${g.count}</span>`)
    .join('');

  // statement lines
  let lines = (d.lines||[]);
  if(_BANKCOV_FILTER!=='all') lines=lines.filter(l=>l.status===_BANKCOV_FILTER);
  const linesHtml = lines.length
    ? lines.map(l=>{
        const [cls,sym]=_BC_LINE_ST[l.status]||['c-mut','·'];
        const lane=(_BC_LANE[l.category]||l.category)+(l.bank&&l.bank!=='NONE'&&l.bank!=='UNKNOWN'?'·'+l.bank:'');
        const dim=l.status==='excluded'||l.status==='informational'?'opacity:.5':'';
        return `<tr style="${dim}">
          <td class="lbl nowrap">${esc(l.date||'—')}</td>
          <td class="lbl">${esc(lane)}</td>
          <td class="lbl ellipsis maxw-220">${esc(l.ref||'')}</td>
          <td class="mono">${fmt(l.amount)}</td>
          <td><span class="chip ${cls} fs-11">${sym}</span></td>
        </tr>`;
      }).join('')
    : `<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--mut)">${_BANKCOV_FILTER==='unmatched'?'ทุกบรรทัดตรงแล้ว ✓':'—'}</td></tr>`;

  $('app').innerHTML = header() + `
    <div class="row mb-10">
      <h2 class="fs-16"><i class="ti ti-building-bank" aria-hidden="true"></i> Bank Coverage — สถานะครอบคลุมสเตทเมนต์</h2>
      ${picker}
    </div>
    <div class="muted mb-10">${esc(brName)} · ${MONTH} · ตรวจทุกบรรทัดสเตทเมนต์ว่ากระทบยอดครบ</div>
    <div class="card mb-12">
      <div class="row mb-8">
        <span class="chip ${hCol} fs-14">${tot.matched||0}/${tot.lines_total||0} ตรงแล้ว · ${pct}%</span>
        <div class="lbl ml-8">✓ ${fmt(tot.amount_matched)} · <i class="ti ti-hourglass-low" aria-hidden="true"></i> ${fmt(tot.amount_unmatched)}${tot.excluded?` · ⊘ ${fmt(tot.amount_excluded)}`:''}</div>
      </div>
      <div class="fx-wrap d-flex gap-4">${grpHtml||'—'}</div>
    </div>
    ${ccRows.length?`<div class="card mb-12">
      <b class="fs-135 d-block mb-8">3-ขั้นบัตรเครดิต (POS ↔ สรุปยอด ↔ เงินเข้า)</b>
      ${ccHtml}</div>`:''}
    <div class="card p-0">
      <div class="row pad-12-16 bdb-soft fx-wrap gap-6">
        <b class="fs-135">บรรทัดสเตทเมนต์</b>
        <div class="row ml-auto gap-6 fx-wrap">${fOpts}</div>
      </div>
      <div style="overflow:auto;max-height:480px">
        <table><thead><tr>
          <th>วันที่</th><th>ช่องทาง</th><th>อ้างอิง</th><th>จำนวน</th><th>สถานะ</th>
        </tr></thead><tbody>${linesHtml}</tbody></table>
      </div>
    </div>`;
}

/* ── #board — Daily Board (global, all branches, one day) ───────────────── */
let _BOARD_DATE = null;
let _BOARD_DATES = null;   // {min_day, max_day} cached once

const _BOARD_CELL = {matched:'var(--ok-bg)',variance:'var(--err-bg)',pos_only:'var(--warn-bg)',ext_only:'var(--warn-bg)',no_source:'var(--line-soft)',manual:'var(--info-bg)'};
const _BOARD_FG   = {matched:'var(--ok)',variance:'var(--err)',pos_only:'var(--warn)',ext_only:'var(--warn)',no_source:'var(--mut)',manual:'var(--info)'};

async function loadBoard(){
  const t=++_ROUTE_SEQ;
  $('app').innerHTML = header() + loadingState();

  // fetch date bounds once
  if(!_BOARD_DATES){
    try{ _BOARD_DATES = await api('/api/daily-board/dates'); }catch(e){ _BOARD_DATES={}; }
    if(!_cur(t)) return;
  }
  if(!_BOARD_DATE) _BOARD_DATE = _BOARD_DATES.max_day || MONTH+'-01';

  let data;
  try{ data = await api('/api/daily-board?day='+encodeURIComponent(_BOARD_DATE)); }
  catch(e){
    if(_cur(t)) $('app').innerHTML = header() + `<div class="card c-err">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`;
    return;
  }
  if(!_cur(t)) return;
  _renderBoard(data);
}

// ◀/▶ ทีละวันตามปฏิทินเครื่อง (เดิมใช้ toISOString → ในไทย ▶ ไม่ขยับ ◀ ข้ามวัน)
function _boardStep(delta){
  _BOARD_DATE = addDays(_BOARD_DATE||MONTH+'-01', delta);
  loadBoard();
}
function _boardPickDate(v){ if(v){ _BOARD_DATE=v; loadBoard(); } }

function _renderBoard(d){
  const cats = d.categories||[];
  const meta = d.column_meta||{};
  const lbl  = c=>(meta[c]&&meta[c].label)||c;
  const varTxt2 = v=>{ const n=+v||0; return Math.abs(n)<1?'✓':`${n>0?'+':''}${fmt(n)}`; };
  const varCls2 = v=>{ const n=+v||0; return Math.abs(n)<1?'c-ok':n<0?'c-err':'c-warn'; };

  // summary strip
  const tot = d.totals||{};

  // header row
  const thCats = cats.map(c=>`<th>${esc(lbl(c))}</th>`).join('');

  // body rows
  const bodyRows = d.branches.map(b=>{
    const cells = cats.map(c=>{
      const cell = b.cells[c];
      if(!cell) return '<td class="tr t-mut">·</td>';
      const cm = meta[c]||{};
      const tip = `${esc(b.branch_name)} · ${esc(lbl(c))}\nPOS ${fmt(cell.pos)} / เงินเข้า ${fmt(cell.ext)}\nต่าง ${varTxt2(cell.variance)} (${cell.status})`;
      return `<td class="tr"><span title="${tip}"
        style="display:inline-block;min-width:60px;padding:2px 7px;border-radius:var(--radius-sm);cursor:default;
               font-variant-numeric:tabular-nums;font-size:12px;font-weight:600;
               background:${_BOARD_CELL[cell.status]||'var(--line-soft)'};color:${_BOARD_FG[cell.status]||'var(--sub)'}"
        >${fmt(cell.pos)}</span></td>`;
    }).join('');
    return `<tr>
      <td class="fw-500 nowrap">${esc(b.branch_name)}${b.issues?` <span class="chip c-err fs-10">${b.issues}</span>`:''}</td>
      ${cells}
      <td class="mono">${fmt(b.pos_total)}</td>
      <td class="mono t-sub">${fmt(b.ext_total)}</td>
      <td><span class="chip ${varCls2(b.variance)} fs-11">${varTxt2(b.variance)}</span></td>
      <td class="tr nowrap">
        <a class="lbl" href="/api/daily-report?day=${esc(_BOARD_DATE)}&branch_id=${b.branch_id}" target="_blank" title="รายงาน PDF"><i class="ti ti-file" aria-hidden="true"></i></a>
      </td>
    </tr>`;
  }).join('');

  // footer totals
  const bc = tot.by_category||{};
  const footCats = cats.map(c=>`<td class="mono">${fmt((bc[c]||{}).pos)}</td>`).join('');

  // unmapped notice
  const unmapped = d.unmapped&&d.unmapped.length
    ? `<div class="card c-warn mb-10">
        <b><i class="ti ti-alert-triangle" aria-hidden="true"></i> Unmapped POS codes (ไม่ถูกรวมในตาราง):</b>
        ${d.unmapped.map(u=>`<span class="chip c-warn ml-6">${esc(u.code)}: ${fmt(u.amount)}</span>`).join('')}
        — <a href="#setup">ตั้งค่ารหัส</a>
      </div>` : '';

  const emptyMsg = !d.branches.length
    ? emptyState('calendar-off',`ยังไม่มีรายการวันที่ ${esc(_BOARD_DATE)}`,'เลือกวันอื่น หรือซิงก์ข้อมูล POS')
    : '';

  $('app').innerHTML = header() + `
    <div class="row mb-10 fx-wrap gap-8">
      <h2 class="fs-16"><i class="ti ti-calendar-event" aria-hidden="true"></i> Daily Board — ทุกสาขาในวันเดียว</h2>
      <div class="row gap-6 ml-auto fx-nowrap">
        <button class="btn btn-sm" onclick="_boardStep(-1)">◀</button>
        <input type="date" value="${esc(_BOARD_DATE)}"
          min="${esc(_BOARD_DATES&&_BOARD_DATES.min_day||'')}"
          max="${esc(_BOARD_DATES&&_BOARD_DATES.max_day||'')}"
          onchange="_boardPickDate(this.value)"
          class="ctl-sm">
        <button class="btn btn-sm" onclick="_boardStep(1)">▶</button>
        <a class="btn btn-sm" href="/api/daily-report?day=${esc(_BOARD_DATE)}" target="_blank" title="รายงาน PDF ทุกสาขา"><i class="ti ti-printer" aria-hidden="true"></i> PDF ทุกสาขา</a>
      </div>
    </div>
    <div class="muted mb-10">ยอด POS ต่อช่องทาง · สี: <span class="legend-tag bg-ok t-ok">✓ ตรง</span> <span class="legend-tag bg-err t-err">≠ ต่าง</span> <span class="legend-tag bg-warn t-warn">ขาดเอกสาร</span></div>
    <div class="card mb-12">
      <div class="row fx-wrap gap-16">
        <div><div class="lbl">POS รวมทุกสาขา</div><b class="mono fs-18">${fmt(tot.pos)}</b></div>
        <div><div class="lbl">เงินเข้าจริง</div><b class="mono fs-18 t-sub">${fmt(tot.ext)}</b></div>
        <div><div class="lbl">ผลต่าง</div><b class="mono fs-18"><span class="chip ${varCls2(tot.variance)}">${varTxt2(tot.variance)}</span></b></div>
        <div><div class="lbl">สาขารายงาน</div><b class="fs-18">${tot.branches_reporting||0}${tot.branches_with_issues?` · <span class="t-warn">${tot.branches_with_issues}<i class="ti ti-alert-triangle" aria-hidden="true"></i></span>`:''}</b></div>
      </div>
    </div>
    ${unmapped}
    ${emptyMsg}
    ${d.branches.length?`<div class="card p-0 ov-auto">
      <table>
        <thead><tr>
          <th class="tl">สาขา</th>
          ${thCats}
          <th>POS รวม</th><th>เงินเข้า</th><th>ต่าง</th><th></th>
        </tr></thead>
        <tbody>${bodyRows}</tbody>
        <tfoot><tr class="bg-soft fw-600">
          <td>รวมทุกสาขา</td>
          ${footCats}
          <td class="mono">${fmt(tot.pos)}</td>
          <td class="mono t-sub">${fmt(tot.ext)}</td>
          <td><span class="chip ${varCls2(tot.variance)} fs-11">${varTxt2(tot.variance)}</span></td>
          <td></td>
        </tr></tfoot>
      </table>
    </div>`:''}`;
}
