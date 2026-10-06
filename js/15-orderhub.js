// 15-orderhub.js — ศูนย์สั่งของ (BoM Center Phase 2 · S9): Sales Support's order queue,
// Ops' ordering compliance and the monthly usage-variance summary. Shares the global
// scope of slices 01-14 (uses _bmcJson / _bmcChip / _bmcNum / _bmcStatus / _bmcQ from
// 13); every name here starts with `ohb`.

let _ohbSub = 'queue';
let _ohbStatus = 'submitted';
let _ohbQ = null;             // /api/bom-center/hub/queue
let _ohbOrder = null;         // the order open in the detail panel
const _OHB_RND = { '1': 'รอบ 1', '2': 'รอบ 2', salmon: 'แซลมอน' };
const _OHB_ST = [['submitted', 'รอดำเนินการ'], ['processed', 'ดำเนินการแล้ว'], ['received', 'สาขารับแล้ว'], ['all', 'ทั้งหมด']];
function _ohbCanWork() { return hasModule('supply.ordering') || hasModule('bom.center'); }
function _ohbIso(d) { return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); }
function _ohbBtn(label, on, primary) {
  return `<button onclick="${on}" style="border:${primary ? '0' : '1px solid var(--border)'};border-radius:var(--r-sm);padding:${primary ? '7px 14px' : '3px 10px'};font-size:${primary ? 13 : 12}px;cursor:pointer;background:${primary ? 'var(--accent)' : 'transparent'};color:${primary ? 'var(--on-accent)' : 'var(--text-primary)'}">${label}</button>`;
}

function loadOrderHub() {
  const t = new Date();
  const set = (id, v) => { const el = document.getElementById(id); if (el && !el.value) el.value = v; };
  set('ohbFrom', _ohbIso(new Date(t.getTime() - 7 * 864e5)));
  set('ohbTo', _ohbIso(new Date(t.getTime() + 7 * 864e5)));
  set('ohbCFrom', _ohbIso(new Date(t.getTime() - 13 * 864e5)));
  set('ohbCTo', _ohbIso(t));
  set('ohbMonth', _ohbIso(new Date(t.getFullYear(), t.getMonth() - 1, 15)).slice(0, 7));
  ohbSetSub(_ohbSub);
}
function ohbSetSub(k) {
  _ohbSub = k;
  document.querySelectorAll('#tab-orderhub [data-ohbsub]').forEach(b => b.classList.toggle('active', b.dataset.ohbsub === k));
  ['queue', 'comp', 'month'].forEach(p => { const el = document.getElementById('ohb-pane-' + p); if (el) el.style.display = p === k ? '' : 'none'; });
  if (k === 'queue') ohbLoadQueue();
  if (k === 'comp') ohbLoadComp();
  if (k === 'month') ohbLoadMonth();
}

// ── queue ────────────────────────────────────────────────────────────────────
function ohbSetStatus(s) { _ohbStatus = s; ohbLoadQueue(); }
function _ohbRange() { return `date_from=${document.getElementById('ohbFrom').value}&date_to=${document.getElementById('ohbTo').value}`; }
async function ohbLoadQueue() {
  document.getElementById('ohbStatusChips').innerHTML = _OHB_ST.map(([k, t]) => {
    const on = k === _ohbStatus;
    return `<button onclick="ohbSetStatus('${k}')" style="border:1px solid ${on ? 'var(--accent)' : 'var(--border)'};border-radius:999px;padding:5px 12px;font-size:13px;cursor:pointer;background:${on ? 'var(--accent)' : 'transparent'};color:${on ? 'var(--on-accent)' : 'var(--text-primary)'}">${t}</button>`;
  }).join('');
  const box = document.getElementById('ohbQueue');
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</span>';
  try {
    _ohbQ = await _bmcJson(`${API}/api/bom-center/hub/queue?status=${_ohbStatus}&${_ohbRange()}`);
    const byDay = {};
    _ohbQ.orders.forEach(o => { (byDay[o.delivery] = byDay[o.delivery] || []).push(o); });
    const days = Object.keys(byDay).sort();
    box.innerHTML = days.length ? days.map(d => `<div class="text-xs font-semibold mt-2 mb-1" style="color:var(--text-primary)">ส่งถึง ${esc(d)} · ${byDay[d].length} ออเดอร์ · ฿${_bmcNum(byDay[d].reduce((a, o) => a + (o.value || 0), 0), 0)}</div>` +
      byDay[d].map(o => `<div style="border-top:1px solid var(--border);padding:6px 0;display:flex;gap:8px;flex-wrap:wrap;align-items:baseline" class="text-xs">
        <a href="#" onclick="ohbOpen(${o.order_id});return false" style="color:var(--accent);font-weight:600">#${o.order_id} ${esc(o.branch)}</a>
        <span>${esc(_OHB_RND[o.round] || o.round)} · สั่ง ${esc(o.cycle_date)}</span><span>${o.lines} รายการ · ฿${_bmcNum(o.value, 0)}</span>
        ${o.branch_changes ? _bmcChip(`สาขาแก้ ${o.branch_changes}`, 'var(--amber-ink)') : ''}
        ${o.stock_deducted ? '' : _bmcChip('ไม่หักสต๊อก', 'var(--text-muted)')}
        ${o.submitted_on_time ? '' : _bmcChip('ส่งหลัง 10:00', 'var(--amber-ink)')}
        ${o.processed_on_time === false ? _bmcChip('ดำเนินการเกิน 14:00', 'var(--bad-ink)') : ''}
        ${o.status === 'processed' ? _bmcChip(`ดำเนินการแล้ว${o.po_ref ? ' · ' + o.po_ref : ''}`, 'var(--good-ink)') : ''}
        ${o.status === 'received' ? _bmcChip('สาขารับแล้ว', 'var(--good-ink)') : ''}</div>`).join('')).join('')
      : '<span class="text-xs" style="color:var(--good-ink)">✓ ไม่มีออเดอร์ในช่วงนี้</span>';
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}

async function ohbOpen(id) {
  const box = document.getElementById('ohbDetail');
  box.style.display = '';
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</span>';
  try {
    _ohbOrder = await _bmcJson(`${API}/api/bom-center/hub/order/${id}`);
    ohbRenderDetail();
    box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function ohbRenderDetail() {
  const o = _ohbOrder;
  const work = _ohbCanWork() && ['submitted', 'processed'].includes(o.status);
  const reasons = (_ohbQ && _ohbQ.reasons) || [];
  const rows = o.lines.filter(l => (l.confirmed_units || 0) > 0 || (l.suggested_units || 0) > 0 || (l.processed_units || 0) > 0).map(l => {
    const sent = l.processed_units != null ? l.processed_units : l.confirmed_units;
    return `<tr style="border-top:1px solid var(--border)">
      <td class="py-1 pr-2">${esc(l.name || l.code)}<div class="text-[10.5px]" style="color:var(--text-muted)">${esc(l.code)} · ${l.order_unit === 'base' ? 'หน่วยฐาน' : 'แพ็ค'}</div></td>
      <td class="py-1 pr-2 text-right">${_bmcNum(l.suggested_units)}</td>
      <td class="py-1 pr-2 text-right"><b>${_bmcNum(l.confirmed_units)}</b>${l.reason_th ? `<div class="text-[10.5px]" style="color:var(--amber-ink)">${esc(l.reason_th)}${l.note ? ' · ' + esc(l.note) : ''}</div>` : ''}</td>
      <td class="py-1 pr-2 text-right">${work ? `<input inputmode="decimal" id="ohbP_${esc(l.code)}" value="${esc(String(sent ?? ''))}" style="width:64px;text-align:center;border:1px solid var(--border-control);border-radius:6px;padding:3px;background:var(--bg-input);color:var(--text-primary)">` : _bmcNum(sent)}</td>
      <td class="py-1">${work ? `<select id="ohbR_${esc(l.code)}" style="font-size:11px;border:1px solid var(--border-control);border-radius:6px;background:var(--bg-input);color:var(--text-primary)"><option value="">— ถ้าส่งไม่ตรง —</option>${reasons.map(r => `<option value="${r.code}"${l.ss_reason === r.code ? ' selected' : ''}>${esc(r.label)}</option>`).join('')}</select>
        <input id="ohbN_${esc(l.code)}" value="${esc(l.ss_note || '')}" placeholder="หมายเหตุ" style="width:110px;font-size:11px;border:1px solid var(--border-control);border-radius:6px;padding:2px 4px;background:var(--bg-input);color:var(--text-primary)">`
        : (l.ss_reason_th ? `<span class="text-[11px]" style="color:var(--amber-ink)">${esc(l.ss_reason_th)}${l.ss_note ? ' · ' + esc(l.ss_note) : ''}</span>` : '')}
        ${l.received_units != null ? `<div class="text-[10.5px]" style="color:var(--text-muted)">รับจริง ${_bmcNum(l.received_units)}</div>` : ''}</td></tr>`;
  }).join('');
  document.getElementById('ohbDetail').innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap">
      <div class="text-sm font-semibold" style="color:var(--text-primary)">#${o.order_id} ${esc(o.branch || '')} · ${esc(_OHB_RND[o.round] || o.round)} · สั่ง ${esc(o.cycle_date)} · ส่งถึง ${esc(o.delivery)}</div>
      ${_ohbBtn('ปิด', "document.getElementById('ohbDetail').style.display='none'")}</div>
    <div class="text-xs mt-1" style="color:var(--text-secondary)">${_bmcChip(o.status_th, o.status === 'submitted' ? 'var(--amber-ink)' : 'var(--good-ink)')}
      ${o.stock_count_id ? 'สาขาหักสต๊อกแล้ว' : 'สาขาไม่ได้นับ — ยอดแนะนำไม่หักสต๊อก'}${o.note ? ' · ' + esc(o.note) : ''}${o.processed_by ? ` · ดำเนินการโดย ${esc(o.processed_by)}` : ''}</div>
    <div style="overflow-x:auto" class="mt-2"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left">
      <th class="py-1 pr-2">วัตถุดิบ</th><th class="py-1 pr-2 text-right">แนะนำ</th><th class="py-1 pr-2 text-right">สาขายืนยัน</th><th class="py-1 pr-2 text-right">ส่งจริง</th><th class="py-1">เหตุผล (ส่วนกลาง)</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    ${work ? `<div class="flex flex-wrap items-center gap-2 mt-2 text-xs">เลขที่ PO <input id="ohbPo" value="${esc(o.po_ref || '')}" style="width:140px;border:1px solid var(--border-control);border-radius:var(--r-sm);padding:5px 8px;font-size:13px;background:var(--bg-input);color:var(--text-primary)">
      ${_ohbBtn(o.status === 'processed' ? 'บันทึกการแก้' : 'ดำเนินการแล้ว', 'ohbProcess()', true)}<span id="ohbDStatus"></span></div>` : ''}`;
}
async function ohbProcess() {
  const o = _ohbOrder;
  const lines = o.lines.map(l => {
    const q = document.getElementById('ohbP_' + l.code);
    const r = document.getElementById('ohbR_' + l.code);
    const n = document.getElementById('ohbN_' + l.code);
    return { code: l.code, processed_units: q && q.value !== '' ? Number(q.value) : null, reason: r ? r.value || null : null, note: n ? n.value || null : null };
  });
  try {
    const r = await _bmcJson(`${API}/api/bom-center/hub/process/${o.order_id}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lines, po_ref: document.getElementById('ohbPo').value || null }) });
    await ohbOpen(o.order_id);
    _bmcStatus('ohbDStatus', `✓ ดำเนินการแล้ว · ฿${_bmcNum(r.value, 0)}`, 'var(--good-ink)');
    ohbLoadQueue();
  } catch (e) { _bmcStatus('ohbDStatus', 'ไม่สำเร็จ — ' + String(e.message || e).slice(0, 220), 'var(--bad-ink)'); }
}
async function ohbShowConsolidated() {
  const box = document.getElementById('ohbConsolidated');
  box.style.display = '';
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังรวม…</span>';
  try {
    const c = await _bmcJson(`${API}/api/bom-center/hub/consolidated?${_ohbRange()}`);
    box.innerHTML = `<div style="display:flex;justify-content:space-between"><div class="text-sm font-semibold" style="color:var(--text-primary)">รวมต่อซัพพลายเออร์ × วันส่ง (รอดำเนินการ + ดำเนินการแล้ว)</div>${_ohbBtn('ปิด', "document.getElementById('ohbConsolidated').style.display='none'")}</div>` +
      (c.groups.length ? c.groups.map(g => `<div class="text-xs font-semibold mt-3" style="color:var(--text-primary)">${esc(g.supplier)} · ส่งถึง ${esc(g.delivery)}</div>
        <div style="overflow-x:auto"><table class="w-full text-xs"><tbody>${g.items.map(it => `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2">${esc(it.name)} <span style="color:var(--text-muted)">${esc(it.code)}</span></td>
          <td class="py-1 pr-2 text-right"><b>${_bmcNum(it.total)}</b> ${esc(it.unit || '')}</td>
          <td class="py-1" style="color:var(--text-muted)">${Object.entries(it.by_branch).map(([b, q]) => `${esc(b)} ${_bmcNum(q)}`).join(' · ')}</td></tr>`).join('')}</tbody></table></div>`).join('')
        : '<div class="text-xs mt-2" style="color:var(--text-muted)">ไม่มีออเดอร์ในช่วงนี้</div>');
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function ohbDownloadConsolidated() { window.location.href = `${API}/api/bom-center/hub/consolidated.xlsx?${_ohbRange()}`; }

// ── compliance ───────────────────────────────────────────────────────────────
const _OHB_CS = { ok: ['ตามแนะนำ', 'var(--good-ink)'], over: ['สั่งเกิน', 'var(--amber-ink)'], under: ['สั่งขาด', 'var(--amber-ink)'],
  not_ordered: ['ไม่ได้สั่ง', 'var(--bad-ink)'], not_submitted: ['ยังไม่ส่ง', 'var(--bad-ink)'], upcoming: ['ยังไม่ถึงวัน', 'var(--text-muted)'] };
function _ohbCRange() { return `date_from=${document.getElementById('ohbCFrom').value}&date_to=${document.getElementById('ohbCTo').value}`; }
async function ohbLoadComp() {
  const box = document.getElementById('ohbComp');
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</span>';
  try {
    const c = await _bmcJson(`${API}/api/bom-center/hub/compliance?${_ohbCRange()}`);
    document.getElementById('ohbCChips').innerHTML = Object.entries(_OHB_CS).filter(([k]) => c.counts[k]).map(([k, [t, ink]]) => _bmcChip(`${t} ${c.counts[k]}`, ink)).join(' ');
    const pct = v => v == null ? '' : `${v > 0 ? '+' : ''}${_bmcNum(v, 1)}%`;
    box.innerHTML = c.rows.length ? `<div style="overflow-x:auto"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left">
        <th class="py-1 pr-2">วันสั่ง</th><th class="py-1 pr-2">สาขา</th><th class="py-1 pr-2">รอบ</th><th class="py-1 pr-2">สถานะ</th>
        <th class="py-1 pr-2 text-right">แนะนำ ฿</th><th class="py-1 pr-2 text-right">สาขายืนยัน ฿</th><th class="py-1 pr-2 text-right">ส่วนกลาง ฿</th><th class="py-1 pr-2 text-right">รับจริง ฿</th><th class="py-1">เวลา</th></tr></thead><tbody>` +
      c.rows.map(r => { const [t, ink] = _OHB_CS[r.status] || [r.status, 'var(--text-muted)'];
        return `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2">${esc(r.cycle_date)}</td><td class="py-1 pr-2">${esc(r.branch || '')}</td><td class="py-1 pr-2">${esc(_OHB_RND[r.round] || r.round)}</td>
          <td class="py-1 pr-2">${_bmcChip(t, ink)}</td>
          <td class="py-1 pr-2 text-right">${r.suggested != null ? _bmcNum(r.suggested, 0) : ''}</td>
          <td class="py-1 pr-2 text-right">${r.confirmed != null ? _bmcNum(r.confirmed, 0) : ''} <span style="color:var(--text-muted)">${pct(r.confirmed_vs_suggested_pct)}</span></td>
          <td class="py-1 pr-2 text-right">${r.processed != null ? _bmcNum(r.processed, 0) : ''} <span style="color:var(--text-muted)">${pct(r.centre_change_pct)}</span></td>
          <td class="py-1 pr-2 text-right">${r.received != null ? _bmcNum(r.received, 0) : ''} <span style="color:var(--text-muted)">${pct(r.delivery_gap_pct)}</span></td>
          <td class="py-1">${r.order_id ? (r.submitted_on_time ? '' : _bmcChip('ส่งช้า', 'var(--amber-ink)')) + (r.processed_on_time === false ? _bmcChip('ดำเนินการช้า', 'var(--bad-ink)') : '') : ''}</td></tr>`; }).join('') + '</tbody></table></div>'
      : '<span class="text-xs" style="color:var(--text-muted)">ยังไม่มีสาขาที่เริ่มนับสต๊อก/สั่งผ่านระบบ — รายการจะขึ้นเมื่อสาขาเริ่มใช้</span>';
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function ohbDownloadComp() { window.location.href = `${API}/api/bom-center/hub/compliance.xlsx?${_ohbCRange()}`; }

// ── monthly ──────────────────────────────────────────────────────────────────
async function ohbLoadMonth() {
  const box = document.getElementById('ohbMonthBody');
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังคำนวณ…</span>';
  try {
    const m = await _bmcJson(`${API}/api/bom-center/hub/monthly?month=${document.getElementById('ohbMonth').value}`);
    const pct = v => v == null ? '—' : `${v > 0 ? '+' : ''}${_bmcNum(v, 1)}%`;
    const t = m.total;
    box.innerHTML = m.branches.length ? `<div class="text-sm" style="color:var(--text-primary)">${esc(m.month)} · ตามสูตร ฿${_bmcNum(t.bom_value, 0)} · ผลต่าง <b style="color:${t.gap_value > 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">฿${_bmcNum(t.gap_value, 0)} (${pct(t.gap_pct)})</b> · ใช้เกิน ฿${_bmcNum(t.over_value, 0)}
        ${m.previous && m.previous.total.bom_value ? ` <span class="text-xs" style="color:var(--text-muted)">เดือนก่อน ${pct(m.previous.total.gap_pct)}</span>` : ''}</div>
      <div style="overflow-x:auto" class="mt-2"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left"><th class="py-1 pr-2">สาขา</th><th class="py-1 pr-2 text-right">รอบ</th>
        <th class="py-1 pr-2 text-right">ตามสูตร ฿</th><th class="py-1 pr-2 text-right">ผลต่าง ฿</th><th class="py-1 pr-2 text-right">%</th><th class="py-1 pr-2 text-right">A / B / C ฿</th><th class="py-1 pr-2 text-right">เดือนก่อน</th><th class="py-1">ค้าง</th></tr></thead><tbody>` +
      m.branches.map(b => `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2 font-semibold">${esc(b.branch)}</td><td class="py-1 pr-2 text-right">${b.periods}</td>
        <td class="py-1 pr-2 text-right">${_bmcNum(b.bom_value, 0)}</td><td class="py-1 pr-2 text-right" style="color:${b.gap_value > 0 ? 'var(--bad-ink)' : 'inherit'}">${_bmcNum(b.gap_value, 0)}</td>
        <td class="py-1 pr-2 text-right">${pct(b.gap_pct)}</td><td class="py-1 pr-2 text-right">${['A', 'B', 'C'].map(k => _bmcNum(b.by_class[k], 0)).join(' / ')}</td>
        <td class="py-1 pr-2 text-right" style="color:var(--text-muted)">${pct(b.prev_gap_pct)}</td>
        <td class="py-1">${b.needs_reason ? _bmcChip(`ไม่มีเหตุผล ${b.needs_reason}`, 'var(--amber-ink)') : ''}${b.complete < b.periods ? _bmcChip('รับของยังไม่ยืนยัน', 'var(--text-muted)') : ''}</td></tr>`).join('') + '</tbody></table></div>' +
      (m.top_reasons.length ? `<div class="text-xs font-semibold mt-3" style="color:var(--text-primary)">เหตุผลหลัก</div>` + m.top_reasons.map(r => `<div class="text-xs" style="border-top:1px solid var(--border);padding:3px 0">${esc(r.label)} · ${r.items} รายการ · ฿${_bmcNum(r.value, 0)}</div>`).join('') : '')
      : `<span class="text-xs" style="color:var(--text-muted)">ยังไม่มีรอบนับสต๊อกที่ปิดในเดือน ${esc(m.month)}</span>`;
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
