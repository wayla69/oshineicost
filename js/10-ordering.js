// 10-ordering.js — 🧺 จัดซื้อ/วัตถุดิบ (order plan · pack sizes ·
// sourcing · issued cycles · festival calendar). Loaded last; shares the
// global scope of slices 01-09 (see index.html).
// ═══════════════════════════════════════════════════════════════════════════
// 🧺 แผนสั่งวัตถุดิบรายสาขา (Ingredient Order Plan)
// Demand forecast: real daily sales × BoM. Deliberately blind to on-hand stock
// (the app has no inventory feed), so every surface repeats that caveat rather
// than letting the number read as a ready-to-send purchase order.
// ═══════════════════════════════════════════════════════════════════════════
let _opBasis = 'avg';
let _opFiltersReady = false;

const OP_TYPE_TH = { protein:'โปรตีน', carb:'คาร์โบไฮเดรต', veg:'ผัก',
  sauce:'ซอส', oil:'น้ำมัน', seasoning:'เครื่องปรุง', other:'อื่นๆ' };

function opSetBasis(b) {
  _opBasis = b;
  document.querySelectorAll('[data-ob]').forEach(el =>
    el.classList.toggle('gran-active', el.dataset.ob === b));
  loadOrderPlan(true);
}

async function _opInitFilters() {
  if (_opFiltersReady) return;
  const sel = document.getElementById('opBranch');
  if (!sel) return;
  try {
    const branches = await fetch(`${API}/api/branches`).then(r => r.json());
    sel.innerHTML = '<option value="">— ทุกสาขา (รวมเครือ) —</option>' +
      (branches || []).filter(b => b.active !== false && b.name !== 'ALL')
        .map(b => `<option value="${esc(b.name)}">${esc(b.name)}</option>`).join('');
    const cur = (typeof getBranch === 'function') ? getBranch() : '';
    if (cur && (branches || []).some(b => b.name === cur)) sel.value = cur;
  } catch (e) { sel.innerHTML = '<option value="">— ทุกสาขา —</option>'; }
  _opFiltersReady = true;
}

function _opParams() {
  const p = new URLSearchParams();
  const br = document.getElementById('opBranch')?.value || '';
  if (br) p.set('branch', br);
  if (!br && typeof getGroup === 'function' && getGroup()) p.set('group', getGroup());
  p.set('days', document.getElementById('opDays')?.value || '28');
  p.set('basis', _opBasis);
  p.set('buffer_pct', document.getElementById('opBuffer')?.value || '10');
  const t = document.getElementById('opType')?.value || '';
  if (t) p.set('type', t);
  const q = document.getElementById('opQ')?.value.trim() || '';
  if (q) p.set('q', q);
  if (document.getElementById('opFamily')?.checked) p.set('group_family', 'true');
  const inc = document.getElementById('opInclude')?.value || 'all';
  if (inc !== 'all') p.set('include', inc);
  if (document.getElementById('opSeason')?.checked) p.set('season', 'auto');
  const cs = document.getElementById('opCycleStart')?.value || '';
  if (cs) p.set('cycle_start', cs);
  return p;
}

async function loadOrderPlan(force) {
  await _opInitFilters();
  const p = _opParams();
  const key = p.toString();
  if (!force && _tabLastKey.ordering === key) return;
  _tabLastKey.ordering = key;
  const st = document.getElementById('opStatus');
  if (st) st.textContent = 'กำลังคำนวณ…';
  let d;
  try { d = await fetch(`${API}/api/ingredients/order-plan?${p}`).then(r => r.json()); }
  catch (e) { if (st) st.textContent = 'โหลดข้อมูลไม่สำเร็จ'; return; }
  if (!d || d.available === false) {
    if (st) st.textContent = 'ไม่มีข้อมูลยอดขายรายวันสำหรับคำนวณ';
    document.getElementById('opBody').innerHTML = '';
    return;
  }
  const m = d.meta || {}, t = d.totals || {}, cov = m.coverage || {};
  const brLabel = m.branch || 'ทุกสาขา';
  // a branch that opened mid-window is averaged over the days it actually sold —
  // say so, otherwise "28 วัน" looks like it undercounted
  const partial = (m.branch && m.trading_days && m.trading_days < m.days)
    ? ` · ⓘ ${m.branch} เปิดขายจริง ${m.trading_days}/${m.days} วัน (เฉลี่ยจากวันที่ขายจริง)` : '';
  if (st) st.textContent = `อ้างอิงยอดขายจริง ${m.date_from} ถึง ${m.date_to} (${m.days} วัน) · `
    + `ฐาน ${m.basis === 'recent' ? '7 วันล่าสุด' : 'เฉลี่ยทั้งช่วง'} · เผื่อ ${m.buffer_pct}%` + partial;
  document.getElementById('opScopeNote').textContent = `· ${brLabel} · ${m.date_from} → ${m.date_to}`;

  document.getElementById('opKpis').innerHTML = [
    { l:'รายการวัตถุดิบ', v:fmtN(t.ingredients||0), s:'ที่มีสูตร BoM รองรับ', c:'#0f766e' },
    { l:'มูลค่าสั่ง/สัปดาห์', v:'฿'+fmt(t.value_week||0), s:brLabel, c:'#0891b2' },
    { l:'มูลค่าสั่ง/เดือน',  v:'฿'+fmt(t.value_month||0), s:'รวมเผื่อ '+(m.buffer_pct||0)+'%', c:'#7c3aed' },
    { l:'ครอบคลุมสูตร',     v:(cov.pct!=null?cov.pct+'%':'—'), s:'ของจำนวนจานที่ขาย', c:(cov.pct>=70?'#10b981':'#f59e0b') },
  ].concat(m.local_count ? [{ l:'สาขาซื้อเอง', v:fmtN(m.local_count), s:'ไม่อยู่ในจดหมายสั่งของ', c:'#64748b' }] : [])
   .map(k => `<div class="card p-4" style="border-top:3px solid ${k.c}"><p class="text-[11px] uppercase tracking-wide" style="color:var(--text-muted)">${k.l}</p><p class="text-2xl font-bold" style="color:${k.c}">${k.v}</p><p class="text-[11px]" style="color:var(--text-muted)">${esc(k.s)}</p></div>`).join('');

  const miss = cov.top_missing || [];
  document.getElementById('opCoverage').innerHTML = miss.length
    ? `<div class="card p-3 text-[11px]" style="background:#fffbeb;border-left:3px solid #f59e0b;color:#92400e">
        <b>เมนูที่ยังไม่มีสูตร BoM จึงไม่อยู่ในแผนสั่งนี้</b> (${cov.pct}% ของจานที่ขายมีสูตรแล้ว) —
        ${miss.slice(0,5).map(x => `${esc(x.name || x.code)} (${fmtN(x.qty)} จาน)`).join(' · ')}
       </div>` : '';

  const sn = m.season || {};
  const snEl = document.getElementById('opSeasonNote');
  if (snEl) snEl.innerHTML = (sn.factor && Math.abs(sn.factor - 1) > 0.001)
    ? `<div class="card p-3 text-xs" style="background:#fff7ed;border-left:3px solid #ea580c;color:#9a3412">
         🎉 <b>ปรับตามเทศกาล ×${sn.factor}</b>${sn.event ? ' — ' + esc(sn.event) : ''}
         <span style="color:#a16207"> · ${esc(sn.reason || '')}</span></div>`
    : (sn.mode === 'auto-nodata'
       ? `<div class="card p-3 text-xs" style="background:var(--bg-subtle);color:var(--text-muted)">🎉 เปิดปรับตามเทศกาลไว้ แต่${esc(sn.reason || '')}</div>`
       : '');
  opLoadCycles();
  opLoadAccuracy();

  const brs = d.branches || [];
  const brCard = document.getElementById('opBranchCard');
  if (brs.length && !m.branch) {
    brCard.style.display = '';
    document.getElementById('opBranchBody').innerHTML = brs.map(b => `
      <tr class="border-b" style="border-color:var(--border)">
        <td class="py-1.5 pr-2"><a href="#" onclick="opJumpBranch('${jsAttr(b.branch)}');return false" style="color:#0f766e;font-weight:600">${esc(b.branch)}</a></td>
        <td class="py-1.5 pr-2 text-right">${fmtN(b.ingredients)}</td>
        <td class="py-1.5 pr-2 text-right">฿${fmtN(b.value_week)}</td>
        <td class="py-1.5 text-right font-semibold">฿${fmtN(b.value_month)}</td>
      </tr>`).join('');
  } else { brCard.style.display = 'none'; }

  document.getElementById('opBody').innerHTML = (d.rows || []).map(r => {
    const tr = r.trend_pct;
    const trHtml = tr == null ? '<span style="color:var(--text-muted)">—</span>'
      : tr >= 10 ? `<span style="color:#dc2626;font-weight:600">▲ ${tr.toFixed(0)}%</span>`
      : tr <= -10 ? `<span style="color:#059669;font-weight:600">▼ ${Math.abs(tr).toFixed(0)}%</span>`
      : `<span style="color:var(--text-muted)">${tr > 0 ? '+' : ''}${tr.toFixed(0)}%</span>`;
    const central = r.sourcing !== 'local';
    return `<tr class="border-b" style="border-color:var(--border);${central ? '' : 'opacity:.55'}">
      <td class="py-1.5 pr-2 text-center"><input type="checkbox" ${central ? 'checked' : ''}
          onchange="opSetSourcing('${jsAttr(r.code)}', this.checked)"
          title="${central ? 'แนะนำให้สั่งผ่านส่วนกลาง' : 'สาขาซื้อเอง — ไม่อยู่ในจดหมาย'}"></td>
      <td class="py-1.5 pr-2" style="font-family:monospace;font-size:10.5px;color:var(--text-muted)">${esc(r.code)}</td>
      <td class="py-1.5 pr-2">${esc(r.name)}${r.n_variants > 1 ? ` <span style="color:var(--text-muted)">(${r.n_variants} ชื่อ)</span>` : ''}</td>
      <td class="py-1.5 pr-2" style="color:var(--text-muted)">${esc(OP_TYPE_TH[r.type] || r.type)}</td>
      <td class="py-1.5 pr-2 text-right">${esc(r.per_day_disp)}</td>
      <td class="py-1.5 pr-2 text-right" style="color:var(--text-muted)" title="${r.pack_known ? esc('1 ' + r.order_unit + ' = ' + fmtN(r.qty_per_order) + ' ' + r.unit) : 'ยังไม่ได้ตั้งขนาดบรรจุ — ใช้หน่วยมาตรฐานไปก่อน'}">${r.pack_known ? '' : '<span style="color:#b45309">◇</span> '}${esc(r.order_unit || r.unit)}</td>
      <td class="py-1.5 pr-2 text-right font-semibold" title="ใช้จริง ${esc(r.order_week_disp)}">${esc(r.order_week_units_disp || r.order_week_disp)}</td>
      <td class="py-1.5 pr-2 text-right font-semibold" style="color:#0f766e" title="ใช้จริง ${esc(r.order_month_disp)}">${esc(r.order_month_units_disp || r.order_month_disp)}</td>
      <td class="py-1.5 pr-2 text-right">${fmtN(r.value_month)}</td>
      <td class="py-1.5 pr-2 text-right" style="color:var(--text-muted)">${fmtN(r.n_menus)}</td>
      <td class="py-1.5 text-right">${trHtml}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="11" class="py-4 text-center" style="color:var(--text-muted)">ไม่พบข้อมูลตามเงื่อนไขที่เลือก</td></tr>';
}

function opJumpBranch(name) {
  const sel = document.getElementById('opBranch');
  if (sel) { sel.value = name; loadOrderPlan(true); }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function opPrintLetter() {
  const p = _opParams();
  p.delete('type'); p.delete('q'); p.delete('group_family');
  p.set('horizon', document.getElementById('opHorizon')?.value === 'month' ? 'month' : 'week');
  p.set('top', '40');
  window.open(`${API}/api/ingredients/order-letter.print?${p}`, '_blank');
}

function opExportXlsx() {
  const p = _opParams();
  p.delete('type'); p.delete('q'); p.delete('group_family');
  window.open(`${API}/api/ingredients/order-plan.xlsx?${p}`, '_blank');
}


// Toggle one ingredient between 'สั่งส่วนกลาง' and 'สาขาซื้อเอง'. With a branch
// selected the change applies to that branch only (posting mode for that branch);
// with "ทุกสาขา" it sets the chain-wide default.
async function opSetSourcing(code, central) {
  if (!code) return;
  const branch = document.getElementById('opBranch')?.value || '';
  const st = document.getElementById('opStatus');
  try {
    const r = await fetch(`${API}/api/ingredients/sourcing`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },   // the global fetch
      // wrapper (top of this file) attaches X-Upload-Token to every write
      body: JSON.stringify([{ ingredient_code: code, branch: branch,
                              mode: central ? 'central' : 'local' }]),
    });
    if (!r.ok) throw new Error(await r.text());
    await loadOrderPlan(true);          // reload first — it rewrites opStatus
    if (st) st.textContent = (central ? '✓ ตั้งเป็น "สั่งส่วนกลาง"' : '✓ ตั้งเป็น "สาขาซื้อเอง"')
      + ` · ${code}` + (branch ? ` (เฉพาะ ${branch})` : ' (ทุกสาขา)');
  } catch (e) {
    if (st) st.textContent = 'บันทึกไม่สำเร็จ — ' + String(e).slice(0, 120);
    loadOrderPlan(true);          // put the checkbox back where the server has it
  }
}


// ═══════════════════════════════════════════════════════════════════════════
// ⚙️ Order setup — pack sizes + festival calendar
// ═══════════════════════════════════════════════════════════════════════════
let _osPacks = [], _osEdits = {}, _osSub = 'pack';

function osSetSub(k) {
  _osSub = k;
  document.querySelectorAll('[data-osub]').forEach(b => b.classList.toggle('active', b.dataset.osub === k));
  document.getElementById('os-pane-pack').style.display = k === 'pack' ? '' : 'none';
  document.getElementById('os-pane-cal').style.display  = k === 'cal'  ? '' : 'none';
  if (k === 'cal') osLoadCalendar();
}

async function loadOrderSetup(force) {
  if (!force && _osPacks.length) { osRenderPacks(); return; }
  try {
    const d = await fetch(`${API}/api/ingredients/order-units`).then(r => r.json());
    _osPacks = d.rows || []; _osEdits = {};
    const s = d.summary || {};
    document.getElementById('osPackSummary').textContent =
      `ทั้งหมด ${fmtN(s.total)} รายการ · ตั้งขนาดบรรจุแล้ว ${fmtN(s.with_pack)} · ยังไม่ตั้ง ${fmtN(s.fallback)}`;
  } catch (e) { _osPacks = []; }
  osRenderPacks();
  osLoadCalendar();
}

function osRenderPacks() {
  const q = (document.getElementById('osPackQ')?.value || '').trim().toLowerCase();
  const onlyMissing = document.getElementById('osPackOnlyMissing')?.checked;
  const rows = _osPacks.filter(r =>
    (!onlyMissing || !r.pack_known) &&
    (!q || r.code.toLowerCase().includes(q) || (r.name || '').toLowerCase().includes(q)));
  const inp = (code, f, v, w) =>
    `<input value="${v == null ? '' : esc(v)}" data-osf="${f}" data-oscode="${jsAttr(code)}"
       oninput="osEdit(this)" style="width:${w}px;border:1px solid var(--border);border-radius:4px;
       padding:3px 6px;font-size:11.5px;background:var(--bg-input);color:var(--text-primary);text-align:right">`;
  document.getElementById('osPackBody').innerHTML = rows.map(r => `
    <tr class="border-b" style="border-color:var(--border)">
      <td class="py-1 pr-2" style="font-family:monospace;font-size:10.5px;color:var(--text-muted)">${esc(r.code)}</td>
      <td class="py-1 pr-2">${esc(r.name)}</td>
      <td class="py-1 pr-2" style="color:var(--text-muted)">${esc(r.base_unit)}</td>
      <td class="py-1 pr-2 text-right" style="color:var(--text-muted)">${fmtN(r.n_lines)}</td>
      <td class="py-1 pr-2"><input value="${esc(r.order_unit)}" data-osf="order_unit" data-oscode="${jsAttr(r.code)}"
          oninput="osEdit(this)" style="width:120px;border:1px solid var(--border);border-radius:4px;padding:3px 6px;
          font-size:11.5px;background:var(--bg-input);color:var(--text-primary)"></td>
      <td class="py-1 pr-2 text-right">${inp(r.code, 'qty_per_order', r.qty_per_order, 80)}</td>
      <td class="py-1 pr-2 text-right">${inp(r.code, 'price_per_order', r.price_per_order, 80)}</td>
      <td class="py-1 pr-2 text-right">${inp(r.code, 'round_step', r.round_step, 55)}</td>
      <td class="py-1 pr-2 text-right">${inp(r.code, 'moq', r.moq, 55)}</td>
      <td class="py-1">${r.pack_known
        ? '<span style="color:#059669">✓ ตั้งแล้ว</span>'
        : '<span style="color:#b45309">◇ ใช้หน่วยมาตรฐาน</span>'}</td>
    </tr>`).join('') ||
    '<tr><td colspan="10" class="py-4 text-center" style="color:var(--text-muted)">ไม่พบรายการ</td></tr>';
}

function osEdit(el) {
  const code = el.dataset.oscode, f = el.dataset.osf;
  const base = _osPacks.find(r => r.code === code);
  if (!base) return;
  const e = _osEdits[code] || (_osEdits[code] = Object.assign({}, base));
  e[f] = (f === 'order_unit') ? el.value : (el.value === '' ? null : parseFloat(el.value));
  el.style.borderColor = '#4f46e5';
}

async function osSavePacks() {
  const rows = Object.values(_osEdits).map(e => ({
    ingredient_code: e.code, base_unit: e.base_unit, order_unit: e.order_unit || e.base_unit,
    qty_per_order: Number(e.qty_per_order) || 0, price_per_order: e.price_per_order,
    moq: Number(e.moq) || 1, round_step: Number(e.round_step) || 1,
  }));
  if (!rows.length) { document.getElementById('osPackSummary').textContent = 'ยังไม่มีรายการที่แก้ไข'; return; }
  const bad = rows.filter(r => !(r.qty_per_order > 0));
  if (bad.length) {
    alert('ขนาดบรรจุต้องมากกว่า 0 — ตรวจสอบ: ' + bad.map(b => b.ingredient_code).join(', '));
    return;
  }
  try {
    const r = await fetch(`${API}/api/ingredients/order-units`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(rows) });
    if (!r.ok) throw new Error(await r.text());
    document.getElementById('osPackSummary').textContent = `✓ บันทึก ${rows.length} รายการแล้ว`;
    loadOrderSetup(true);
  } catch (e) {
    document.getElementById('osPackSummary').textContent = 'บันทึกไม่สำเร็จ — ' + String(e).slice(0, 120);
  }
}

async function osLoadCalendar() {
  const sel = document.getElementById('osCalBranch');
  if (sel && !sel.options.length) {
    try {
      const bs = await fetch(`${API}/api/branches`).then(r => r.json());
      sel.innerHTML = '<option value="">— ทุกสาขา —</option>' +
        (bs || []).filter(b => b.active !== false && b.name !== 'ALL')
          .map(b => `<option value="${esc(b.name)}">${esc(b.name)}</option>`).join('');
    } catch (e) {}
  }
  let d;
  try { d = await fetch(`${API}/api/ingredients/demand-calendar`).then(r => r.json()); } catch (e) { return; }
  document.getElementById('osCalBody').innerHTML = (d.rows || []).map(r => `
    <tr class="border-b" style="border-color:var(--border)">
      <td class="py-1.5 pr-2 font-semibold">${esc(r.name)}</td>
      <td class="py-1.5 pr-2">${esc(r.date_from)}</td>
      <td class="py-1.5 pr-2">${esc(r.date_to)}</td>
      <td class="py-1.5 pr-2">${esc(r.branch || 'ทุกสาขา')}</td>
      <td class="py-1.5 pr-2 text-right">${r.factor ? '×' + r.factor : '<span style="color:var(--text-muted)">คิดจากปีก่อน</span>'}</td>
      <td class="py-1.5 pr-2" style="color:var(--text-muted)">${esc(r.note || '')}</td>
      <td class="py-1.5 text-right"><a href="#" onclick="osDelEvent(${r.event_id});return false" style="color:#b91c1c">ลบ</a></td>
    </tr>`).join('') ||
    '<tr><td colspan="7" class="py-4 text-center" style="color:var(--text-muted)">ยังไม่มีช่วงเทศกาล/โปรโมชัน</td></tr>';
}

async function osAddEvent() {
  const name = document.getElementById('osCalName').value.trim();
  const from = document.getElementById('osCalFrom').value;
  const to   = document.getElementById('osCalTo').value;
  const st   = document.getElementById('osCalStatus');
  if (!name || !from || !to) { st.textContent = 'กรอกชื่อและช่วงวันที่ให้ครบ'; return; }
  const fRaw = document.getElementById('osCalFactor').value;
  try {
    const r = await fetch(`${API}/api/ingredients/demand-calendar`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify([{ name, date_from: from, date_to: to,
        branch: document.getElementById('osCalBranch').value || '',
        factor: fRaw === '' ? null : parseFloat(fRaw) }]) });
    if (!r.ok) throw new Error(await r.text());
    st.textContent = '✓ เพิ่มแล้ว';
    document.getElementById('osCalName').value = '';
    document.getElementById('osCalFactor').value = '';
    osLoadCalendar();
  } catch (e) { st.textContent = 'บันทึกไม่สำเร็จ — ' + String(e).slice(0, 110); }
}

async function osDelEvent(id) {
  if (!confirm('ลบช่วงนี้ออกจากปฏิทิน?')) return;
  try { await fetch(`${API}/api/ingredients/demand-calendar/${id}`, { method: 'DELETE' }); } catch (e) {}
  osLoadCalendar();
}

// ═══════════════════════════════════════════════════════════════════════════
// 📋 Order cycles — issue, record what was actually ordered, warn
// ═══════════════════════════════════════════════════════════════════════════
let _opCycleId = null;

async function opIssueCycle() {
  const branch = document.getElementById('opBranch')?.value || '';
  const st = document.getElementById('opCycleStatus');
  const scope = branch || 'ทุกสาขา';
  if (!confirm(`ออกรอบสั่งสำหรับ ${scope}?\n\nระบบจะบันทึกตัวเลขที่แนะนำ ณ ตอนนี้ไว้เป็นหลักฐาน `
             + `เพื่อใช้เทียบกับที่สาขาสั่งจริง (ออกซ้ำรอบเดิม = เขียนทับของเดิม)`)) return;
  st.textContent = 'กำลังออกรอบสั่ง…';
  try {
    const r = await fetch(`${API}/api/ingredients/order-plan/issue`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        branch: branch || null,
        horizon: document.getElementById('opHorizon')?.value || 'week',
        period_start: document.getElementById('opCycleStart')?.value || null,
        days: parseInt(document.getElementById('opDays')?.value || '28', 10),
        basis: _opBasis, buffer_pct: parseFloat(document.getElementById('opBuffer')?.value || '10'),
        top: 40 }) });
    if (!r.ok) throw new Error(await r.text());
    const d = await r.json();
    st.textContent = `✓ ออกรอบสั่งแล้ว ${d.issued.length} สาขา · ${d.period_start} ถึง ${d.period_end}`;
    opLoadCycles();
  } catch (e) { st.textContent = 'ออกรอบสั่งไม่สำเร็จ — ' + String(e).slice(0, 130); }
}

async function opLoadCycles() {
  const branch = document.getElementById('opBranch')?.value || '';
  const tol = document.getElementById('opTolerance')?.value || '20';
  const lbl = document.getElementById('opTolLabel'); if (lbl) lbl.textContent = tol;
  let d;
  try {
    d = await fetch(`${API}/api/ingredients/order-cycles?limit=40`
      + (branch ? `&branch=${encodeURIComponent(branch)}` : '')).then(r => r.json());
  } catch (e) { return; }
  const rows = d.rows || [];
  document.getElementById('opCycleBody').innerHTML = rows.map(r => {
    const c = r.compliance || {};
    const badge = c.compliant
      ? '<span style="color:#059669;font-weight:600">✓ ครบตามแผน</span>'
      : `<span style="color:#b91c1c;font-weight:600">⚠️ ต้องแจ้งเตือน</span>`;
    return `<tr class="border-b" style="border-color:var(--border)">
      <td class="py-1.5 pr-2"><a href="#" onclick="opOpenCycle(${r.issue_id});return false" style="color:#0f766e;font-weight:600">${esc(r.branch)}</a></td>
      <td class="py-1.5 pr-2">${esc(r.period_start)} → ${esc(r.period_end)}</td>
      <td class="py-1.5 pr-2" style="color:var(--text-muted)">${esc(String(r.issued_at || '').slice(0, 16))}</td>
      <td class="py-1.5 pr-2 text-right">${fmtN(c.lines)}</td>
      <td class="py-1.5 pr-2 text-right" style="color:${c.not_ordered ? '#b91c1c' : 'var(--text-muted)'}">${fmtN(c.not_ordered)}</td>
      <td class="py-1.5 pr-2 text-right" style="color:${c.under ? '#b45309' : 'var(--text-muted)'}">${fmtN(c.under)}</td>
      <td class="py-1.5 pr-2 text-right">${c.gap_value ? '฿' + fmtN(c.gap_value) : '—'}</td>
      <td class="py-1.5">${badge}</td></tr>`;
  }).join('') ||
  '<tr><td colspan="8" class="py-4 text-center" style="color:var(--text-muted)">ยังไม่มีรอบสั่งที่ออกไว้ — กด “ออกรอบสั่ง”</td></tr>';
}

async function opOpenCycle(issueId) {
  _opCycleId = issueId;
  const tol = document.getElementById('opTolerance')?.value || '20';
  const el = document.getElementById('opCycleDetail');
  el.style.display = ''; el.innerHTML = '<p class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</p>';
  let d;
  try { d = await fetch(`${API}/api/ingredients/order-compliance?issue_id=${issueId}&tolerance_pct=${tol}`).then(r => r.json()); }
  catch (e) { el.innerHTML = '<p class="text-xs" style="color:#b91c1c">โหลดไม่สำเร็จ</p>'; return; }
  const i = d.issue || {}, s = d.summary || {};
  const cls = { not_ordered: '#b91c1c', under: '#b45309', ok: '#059669' };
  const lab = { not_ordered: 'ไม่ได้สั่ง', under: 'ต่ำกว่าแผน', ok: 'ครบ' };
  el.innerHTML = `
    <div class="flex items-center justify-between flex-wrap gap-2 mb-2">
      <h3 class="text-sm font-bold" style="color:var(--text-primary)">
        ${esc(i.branch)} · รอบ ${esc(i.period_start)} → ${esc(i.period_end)}</h3>
      <div class="flex items-center gap-2">
        <span class="text-xs" style="color:var(--text-muted)">
          ไม่ได้สั่ง ${fmtN(s.not_ordered)} · ต่ำกว่าแผน ${fmtN(s.under)} · ครบ ${fmtN(s.ok)}</span>
        <button onclick="opSaveActuals()" style="background:#0f766e;color:#fff;border:0;border-radius:var(--r-sm);padding:5px 12px;font-size:12.5px;font-weight:600;cursor:pointer">💾 บันทึกที่สั่งจริง</button>
        <button onclick="window.open('${API}/api/ingredients/compliance-letter.print?issue_id=${issueId}&tolerance_pct=${tol}','_blank')"
          style="background:#b91c1c;color:#fff;border:0;border-radius:var(--r-sm);padding:5px 12px;font-size:12.5px;font-weight:600;cursor:pointer">⚠️ จดหมายแจ้งเตือนสาขานี้</button>
      </div>
    </div>
    <div class="overflow-x-auto"><table class="text-xs w-full">
      <thead><tr class="text-left border-b" style="color:var(--text-muted)">
        <th class="py-1.5 pr-2">รหัส</th><th class="py-1.5 pr-2">วัตถุดิบ</th>
        <th class="py-1.5 pr-2 text-right">แผนแจ้งให้สั่ง</th><th class="py-1.5 pr-2 text-right">สั่งจริง</th>
        <th class="py-1.5 pr-2 text-right">% ของแผน</th><th class="py-1.5 pr-2 text-right">ส่วนที่ขาด</th>
        <th class="py-1.5">สถานะ</th></tr></thead>
      <tbody>${(d.rows || []).map(r => `
        <tr class="border-b" style="border-color:var(--border)">
          <td class="py-1 pr-2" style="font-family:monospace;font-size:10.5px;color:var(--text-muted)">${esc(r.ingredient_code)}</td>
          <td class="py-1 pr-2">${esc(r.name)}</td>
          <td class="py-1 pr-2 text-right">${r.rec_units.toLocaleString()} ${esc(r.order_unit)}</td>
          <td class="py-1 pr-2 text-right"><input type="number" min="0" step="any" value="${r.act_units || 0}"
              data-opcode="${jsAttr(r.ingredient_code)}" style="width:80px;border:1px solid var(--border);
              border-radius:4px;padding:3px 6px;font-size:11.5px;text-align:right;background:var(--bg-input);color:var(--text-primary)"></td>
          <td class="py-1 pr-2 text-right">${r.act_units ? r.pct_of_rec + '%' : '0%'}</td>
          <td class="py-1 pr-2 text-right">${r.status === 'ok' ? '—' : esc(r.gap_disp)}</td>
          <td class="py-1" style="color:${cls[r.status]};font-weight:600">${lab[r.status]}</td>
        </tr>`).join('')}</tbody></table></div>`;
}

async function opSaveActuals() {
  if (!_opCycleId) return;
  const rows = [...document.querySelectorAll('#opCycleDetail input[data-opcode]')].map(el => ({
    ingredient_code: el.dataset.opcode, qty_units: parseFloat(el.value || '0') || 0 }));
  const st = document.getElementById('opCycleStatus');
  try {
    const r = await fetch(`${API}/api/ingredients/order-actual`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ issue_id: _opCycleId, rows }) });
    if (!r.ok) throw new Error(await r.text());
    st.textContent = `✓ บันทึกจำนวนที่สั่งจริง ${rows.length} รายการ`;
    opLoadCycles(); opOpenCycle(_opCycleId);
  } catch (e) { st.textContent = 'บันทึกไม่สำเร็จ — ' + String(e).slice(0, 130); }
}

function opWarnLetter() {
  const tol = document.getElementById('opTolerance')?.value || '20';
  const hz = document.getElementById('opHorizon')?.value || 'week';
  // a field, not prompt() — prompt() is unsupported in the app shell and threw
  const due = (document.getElementById('opDueDate')?.value || '').trim();
  const p = new URLSearchParams({ horizon: hz, tolerance_pct: tol });
  if (due) p.set('due_date', due);
  const cs = document.getElementById('opCycleStart')?.value || '';
  if (cs) p.set('period_start', cs);
  window.open(`${API}/api/ingredients/compliance-letter.print?${p}`, '_blank');
}


// ── forecast accuracy ──────────────────────────────────────────────────────
// Compliance answers "did they order what we said". This answers "was what we
// said right" — the frozen recommendation vs what the branch actually consumed
// in that cycle. The suggested buffer is a SERVICE LEVEL, not a mean: ordering
// the average leaves you short half the time.
let _opAccSuggested = null;

async function opLoadAccuracy() {
  const branch = document.getElementById('opBranch')?.value || '';
  const sl = document.getElementById('opServiceLevel')?.value || '80';
  const head = document.getElementById('opAccHead');
  const body = document.getElementById('opAccBody');
  if (!head) return;
  let d;
  try {
    d = await fetch(`${API}/api/ingredients/order-accuracy?service_level=${sl}`
      + (branch ? `&branch=${encodeURIComponent(branch)}` : '')).then(r => r.json());
  } catch (e) { return; }
  if (!d || d.available === false) {
    _opAccSuggested = null;
    head.innerHTML = `<div class="card p-3 text-xs" style="background:var(--bg-subtle);color:var(--text-muted)">
      ${esc(d?.reason || 'ยังประเมินไม่ได้')} — ออกรอบสั่งแล้วรอให้รอบนั้นผ่านไปก่อน จึงจะเทียบกับการใช้จริงได้</div>`;
    body.innerHTML = '';
    return;
  }
  const h = d.headline || {};
  _opAccSuggested = h.suggested_buffer_pct;
  const drift = h.median_ratio != null ? (h.median_ratio - 1) * 100 : null;
  head.innerHTML = `<div class="grid grid-cols-2 md:grid-cols-4 gap-3">` + [
    { l:'รอบที่ประเมินได้', v:fmtN(h.cycles_evaluated), s:`${fmtN(h.lines_evaluated)} บรรทัด · ข้อมูลถึง ${esc(h.data_through)}`, c:'#64748b' },
    { l:'ใช้จริง vs พยากรณ์', v:(drift==null?'—':(drift>0?'+':'')+drift.toFixed(1)+'%'),
      s:(drift==null?'':drift>0?'พยากรณ์ต่ำไป':'พยากรณ์สูงไป'), c:(drift>5?'#dc2626':drift<-5?'#0891b2':'#059669') },
    { l:'% เผื่อที่ใช้อยู่', v:(h.buffer_used_median??'—')+'%', s:'ค่ากลางของรอบที่ผ่านมา', c:'#64748b' },
    { l:'% เผื่อที่ควรใช้', v:(h.suggested_buffer_pct??'—')+'%', s:`ครอบคลุม ${h.service_level_pct}% ของรอบ`, c:'#7c3aed' },
  ].map(k => `<div class="card p-3" style="border-top:3px solid ${k.c}"><p class="text-[10.5px] uppercase" style="color:var(--text-muted)">${k.l}</p><p class="text-xl font-bold" style="color:${k.c}">${k.v}</p><p class="text-[10.5px]" style="color:var(--text-muted)">${esc(k.s)}</p></div>`).join('') + `</div>`;

  const vlab = { under:['พยากรณ์ต่ำไป','#dc2626'], over:['พยากรณ์สูงไป','#0891b2'], ok:['พอดี','#059669'] };
  body.innerHTML = (d.by_ingredient || []).map(e => {
    const [txt, col] = vlab[e.verdict] || vlab.ok;
    return `<tr class="border-b" style="border-color:var(--border)">
      <td class="py-1 pr-2" style="font-family:monospace;font-size:10.5px;color:var(--text-muted)">${esc(e.code)}</td>
      <td class="py-1 pr-2">${esc(e.name)}</td>
      <td class="py-1 pr-2 text-right">${esc(e.rec_disp)}</td>
      <td class="py-1 pr-2 text-right">${esc(e.used_disp)}</td>
      <td class="py-1 pr-2 text-right font-semibold" style="color:${col}">×${e.ratio_median ?? '—'}</td>
      <td class="py-1 pr-2 text-right" style="color:var(--text-muted)">${fmtN(e.n)}</td>
      <td class="py-1 pr-2 text-right">${e.suggested_buffer_pct == null ? '<span style="color:var(--text-muted)">รอบยังน้อย</span>' : e.suggested_buffer_pct + '%'}</td>
      <td class="py-1" style="color:${col};font-weight:600">${txt}</td></tr>`;
  }).join('') || '<tr><td colspan="8" class="py-4 text-center" style="color:var(--text-muted)">ยังไม่มีรอบที่ปิดแล้ว</td></tr>';
}

function opApplySuggestedBuffer() {
  if (_opAccSuggested == null) { alert('ยังไม่มีค่าที่แนะนำ — ต้องมีรอบสั่งที่ปิดแล้วอย่างน้อย 1 รอบ'); return; }
  const el = document.getElementById('opBuffer');
  if (el) { el.value = _opAccSuggested; loadOrderPlan(true); }
}
