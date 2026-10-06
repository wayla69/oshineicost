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
  ['queue', 'comp', 'month', 'pilot', 'central'].forEach(p => { const el = document.getElementById('ohb-pane-' + p); if (el) el.style.display = p === k ? '' : 'none'; });
  if (k === 'queue') ohbLoadQueue();
  if (k === 'comp') ohbLoadComp();
  if (k === 'month') ohbLoadMonth();
  if (k === 'pilot') ohbLoadPilot();
  if (k === 'central') ohbLoadCentral();
}

// ── S11: the central list · a month's deliveries · a count from a file ──────
async function ohbLoadCentral() {
  try {
    const d = await _bmcJson(`${API}/api/bom-center/central`);
    document.getElementById('ohbCenStat').innerHTML = d.count
      ? `สินค้าส่วนกลาง <b>${d.count}</b> รายการ · สาขาจัดซื้อเอง ${d.local} รายการ${d.needs_factor ? ` · ${_bmcChip(d.needs_factor + ' รายการยังไม่ระบุอัตราแปลงหน่วย', 'var(--amber-ink)')}` : ''}`
      : _bmcChip('ยังไม่มีบัญชีสินค้าส่วนกลาง ระบบจะแนะนำให้สั่งทุกรายการจากส่วนกลาง', 'var(--amber-ink)');
    ohbLoadLinks();
    const sel = document.getElementById('ohbCntBranch');
    if (!sel.options.length) {
      const me = await _bmcJson(`${API}/api/bom-center/stock/me`);
      sel.innerHTML = me.branches.map(b => `<option value="${b.branch_id}">${esc(b.name)}</option>`).join('');
    }
  } catch (e) { document.getElementById('ohbCenStat').textContent = String(e.message || e); }
}
async function ohbLoadLinks() {
  const box = document.getElementById('ohbLinks');
  try {
    const d = await _bmcJson(`${API}/api/bom-center/central/links`);
    const kept = d.links.map(l => `<div class="text-xs" style="border-top:1px solid var(--border);padding:4px 0;color:var(--text-primary)">
        ${esc(l.old_code)} ${esc(l.old_name || '')} → <b>${esc(l.new_code)}</b> ${esc(l.new_name || '')}
        <span style="color:var(--text-muted)">· ${esc(l.who || '')}</span>
        <button onclick="ohbLinkSave([{old_code:${_bmcQ(l.old_code)},new_code:null}])" style="font-size:11px;color:var(--bad-ink);background:none;border:0;cursor:pointer">ยกเลิกการจับคู่</button></div>`).join('');
    const sugg = d.suggestions.map(s => `<div class="text-xs" style="border-top:1px solid var(--border);padding:5px 0;color:var(--text-primary)">
        รหัสในสูตร <b>${esc(s.old_code)}</b> ${esc(s.old_name || '')} (ไม่อยู่ในบัญชีส่วนกลาง) · รหัสส่วนกลางที่ใกล้เคียง:
        ${s.options.map(o => `<button onclick="ohbLinkSave([{old_code:${_bmcQ(s.old_code)},new_code:${_bmcQ(o.new_code)}}])" style="border:1px solid var(--border);border-radius:var(--r-sm);padding:2px 8px;margin:2px;font-size:11px;cursor:pointer;background:transparent;color:var(--text-primary)">
          ${esc(o.new_code)} ${esc((o.new_name || '').slice(0, 40))} <span style="color:var(--text-muted)">(${o.score})</span> · จับคู่</button>`).join('')}</div>`).join('');
    box.innerHTML = (d.links.length ? `<div class="text-xs font-semibold" style="color:var(--text-secondary)">จับคู่แล้ว ${d.links.length} รายการ</div>${kept}` : '') +
      (d.suggestions.length ? `<div class="text-xs font-semibold mt-2" style="color:var(--amber-ink)">รายการที่ระบบเสนอให้ตรวจสอบ ${d.suggestions.length} คู่</div>${sugg}` : '') +
      (d.orphans.length ? `<div class="text-[11px] mt-2" style="color:var(--text-muted)">สินค้าส่วนกลางที่ไม่มีสูตรใช้และไม่พบรหัสคู่ ${d.orphans.length} รายการ: ${d.orphans.slice(0, 20).map(o => esc(o.code)).join(', ')}${d.orphans.length > 20 ? ' …' : ''}</div>` : '') ||
      '<span class="text-xs" style="color:var(--text-muted)">ยังไม่มีบัญชีสินค้าส่วนกลาง</span>';
  } catch (e) { box.textContent = String(e.message || e); }
}
async function ohbLinkSave(rows) {
  try {
    await _bmcJson(`${API}/api/bom-center/central/links`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(rows) });
    _bmcStatus('ohbLinkStatus', '✓ บันทึกเรียบร้อย', 'var(--good-ink)');
    ohbLoadLinks();
  } catch (e) { _bmcStatus('ohbLinkStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
function _ohbFd(file) { const fd = new FormData(); fd.append('file', file, file.name); return fd; }
function _ohbCenRow(r) {
  const flag = r.status === 'skip' ? _bmcChip('ไม่นำเข้า', 'var(--text-muted)')
    : r.status === 'needs_factor' ? _bmcChip('ต้องระบุอัตราแปลง', 'var(--bad-ink)')
    : r.pack_change ? _bmcChip('ขนาดบรรจุต่างจากทะเบียน', 'var(--amber-ink)')
    : (r.factor_how || '').startsWith('จากทะเบียน') ? _bmcChip('ควรตรวจสอบ', 'var(--amber-ink)')
    : r.is_new ? _bmcChip('รหัสใหม่', 'var(--navy-ink)') : '';
  return `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2">${esc(r.code)}</td><td class="pr-2">${esc(r.name)}</td>
    <td class="pr-2">${esc(r.unit_label || '')}</td><td class="pr-2 text-right">${r.price_per_sale != null ? _bmcNum(r.price_per_sale, 0) : '–'}</td>
    <td class="pr-2">1 ${esc(r.sale_unit || '')} = ${r.base_per_sale != null ? _bmcNum(r.base_per_sale, 0) + ' ' + esc(r.base_unit || '') : '?'}
      ${r.old_per && r.base_per_sale && r.pack_change ? `<span style="color:var(--text-muted)">(ทะเบียน ${_bmcNum(r.old_per, 0)})</span>` : ''}</td>
    <td class="pr-2" style="color:var(--text-muted)">${esc(r.factor_how || '')}</td><td>${flag}</td></tr>`;
}
async function ohbCenImport(input) {
  const file = input?.files?.[0];
  if (!file) return;
  try {
    _bmcStatus('ohbCenStatus', 'กำลังอ่านไฟล์…');
    const pre = await _bmcJson(`${API}/api/bom-center/central/import?dry_run=true`, { method: 'POST', body: _ohbFd(file) });
    const look = pre.rows.filter(r => r.status !== 'ok' || r.pack_change || r.is_new || (r.factor_how || '').startsWith('จากทะเบียน'));
    document.getElementById('ohbCenReport').innerHTML = `<div class="text-xs" style="color:var(--text-secondary)">
      ${esc(pre.sheet)} (${esc(pre.month || '?')}) · <b>${pre.items}</b> รายการ · รหัสใหม่ ${pre.new} · ขนาดบรรจุต่างจากทะเบียน ${pre.pack_change} ·
      ใช้ขนาดบรรจุตามทะเบียน (ควรตรวจสอบ) ${pre.from_master} · ยังไม่ระบุอัตราแปลง ${pre.needs_factor} · ไม่มีราคา ${pre.no_price} · ไม่นำเข้า ${pre.skipped} ·
      <b>รหัสอื่นอีก ${pre.to_local} รายการจะกำหนดเป็น “สาขาจัดซื้อเอง”</b></div>
      <div class="overflow-x-auto mt-2" style="max-height:360px;overflow-y:auto"><table class="w-full text-xs" style="color:var(--text-primary)">
      <thead><tr style="color:var(--text-muted)" class="text-left"><th>รหัส</th><th>ชื่อสินค้า</th><th>หน่วยนับ</th><th class="text-right">ราคา</th><th>อัตราแปลงหน่วย</th><th>ที่มา</th><th></th></tr></thead>
      <tbody>${look.map(_ohbCenRow).join('')}</tbody></table></div>
      <div class="text-[11px] mt-1" style="color:var(--text-muted)">แสดงเฉพาะ ${look.length} รายการที่ควรตรวจสอบ · หากอัตราแปลงไม่ถูกต้อง ให้ดาวน์โหลดบัญชีสินค้าหลังบันทึก แก้คอลัมน์ “1 หน่วยขาย = หน่วยฐาน” แล้วนำเข้าอีกครั้ง</div>`;
    if (!confirm(`ยืนยันบันทึกบัญชีสินค้าส่วนกลาง ${pre.items} รายการ\n- รหัสอื่นอีก ${pre.to_local} รายการจะกำหนดเป็น “สาขาจัดซื้อเอง”\n- หน่วยสั่งในทะเบียนจะเปลี่ยนเป็นหน่วยขายของส่วนกลาง`)) { _bmcStatus('ohbCenStatus', 'ยกเลิกแล้ว ยังไม่มีการบันทึก'); return; }
    const d = await _bmcJson(`${API}/api/bom-center/central/import?dry_run=false`, { method: 'POST', body: _ohbFd(file) });
    _bmcStatus('ohbCenStatus', `✓ บันทึกบัญชีสินค้า ${d.saved} รายการ (รหัสใหม่ ${d.inserted}) · กำหนดเป็นสาขาจัดซื้อเอง ${d.set_local} รายการ`, 'var(--good-ink)');
    ohbLoadCentral();
  } catch (e) { _bmcStatus('ohbCenStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
  finally { input.value = ''; }
}
let _ohbDelFile = null;
async function ohbDelPick(input) {
  const file = input?.files?.[0];
  if (!file) return;
  _ohbDelFile = file;
  input.value = '';
  ohbDelPreview(null);
}
function _ohbDelIds() {
  return [...document.querySelectorAll('#ohbDelReport input[data-bid]')].filter(x => x.checked).map(x => x.dataset.bid);
}
async function ohbDelPreview(ids) {
  if (!_ohbDelFile) return;
  try {
    _bmcStatus('ohbDelStatus', 'กำลังอ่านไฟล์…');
    const q = ids ? `&branch_ids=${ids.join(',')}` : '';
    const pre = await _bmcJson(`${API}/api/bom-center/central/deliveries?dry_run=true${q}`, { method: 'POST', body: _ohbFd(_ohbDelFile) });
    const on = new Set(ids || pre.branches.filter(b => b.branch_id).map(b => String(b.branch_id)));
    document.getElementById('ohbDelReport').innerHTML = `<div class="text-xs" style="color:var(--text-secondary)">งวด <b>${esc(pre.month)}</b> · วันที่ใบรับ ${esc(pre.receipt_date)} · เลือกสาขาที่จะบันทึกรับสินค้า:</div>
      ${pre.branches.map(b => `<label class="flex flex-wrap items-center gap-2 text-xs mt-1" style="color:var(--text-primary)">
        <input type="checkbox" data-bid="${b.branch_id || ''}" ${b.branch_id && (on.has(String(b.branch_id)) || !ids) ? 'checked' : ''} ${b.branch_id ? '' : 'disabled'} onchange="ohbDelPreview(_ohbDelIds())">
        <b>${esc(b.code)} ${esc(b.branch || b.label)}</b> ${b.error ? _bmcChip(b.error, 'var(--bad-ink)') : ''}
        ${b.branch_id ? `· ${b.lines} รายการ · ฿${_bmcNum(b.value_central, 0)} (ราคาขายส่วนกลาง)` : ''}
        ${b.replaced ? _bmcChip('แทนที่ใบรับเดิม ' + b.replaced + ' ใบ', 'var(--amber-ink)') : ''}
        ${b.skipped && b.skipped.length ? _bmcChip(b.skipped.length + ' รายการแปลงหน่วยไม่ได้ (ไม่บันทึก)', 'var(--amber-ink)') : ''}</label>`).join('')}
      <button onclick="ohbDelSave()" class="mt-2" style="border:0;border-radius:var(--r-sm);padding:6px 14px;font-size:13px;cursor:pointer;background:var(--accent);color:var(--on-accent)">บันทึกรับสินค้าสาขาที่เลือก</button>`;
    _bmcStatus('ohbDelStatus', '');
  } catch (e) { _bmcStatus('ohbDelStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
async function ohbDelSave() {
  const ids = _ohbDelIds();
  if (!ids.length) { _bmcStatus('ohbDelStatus', 'กรุณาเลือกอย่างน้อย 1 สาขา', 'var(--amber-ink)'); return; }
  if (!confirm(`ยืนยันบันทึกรับสินค้าประจำเดือน ${ids.length} สาขา\nหากเคยนำเข้าเดือนนี้แล้ว ใบรับเดิมจะถูกแทนที่`)) return;
  try {
    const d = await _bmcJson(`${API}/api/bom-center/central/deliveries?dry_run=false&branch_ids=${ids.join(',')}`, { method: 'POST', body: _ohbFd(_ohbDelFile) });
    _bmcStatus('ohbDelStatus', '✓ ' + d.branches.map(b => `${b.branch || b.label}: ${b.receipt_id ? 'ใบรับเลขที่ ' + b.receipt_id + ' · ' + b.lines + ' รายการ' : (b.error || 'ไม่มีรายการรับ')}`).join(' · '), 'var(--good-ink)');
  } catch (e) { _bmcStatus('ohbDelStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
function _ohbCntQS() {
  return `branch_id=${document.getElementById('ohbCntBranch').value}&count_date=${document.getElementById('ohbCntDate').value}`;
}
function ohbCntTemplate() { window.location.href = `${API}/api/bom-center/stock/count-template.xlsx?${_ohbCntQS()}`; }
async function ohbCntImport(input) {
  const file = input?.files?.[0];
  if (!file) return;
  const day = document.getElementById('ohbCntDate').value;
  try {
    if (!day) throw new Error('กรุณาเลือกวันที่ตรวจนับ');
    const pre = await _bmcJson(`${API}/api/bom-center/stock/count-import?dry_run=true&${_ohbCntQS()}`, { method: 'POST', body: _ohbFd(file) });
    document.getElementById('ohbCntReport').innerHTML = `<div class="text-xs" style="color:var(--text-secondary)">วันที่ตรวจนับ ${esc(pre.count_date)} · ${pre.lines} รายการ
      ${pre.unknown.length ? `<div style="color:var(--bad-ink)">ไม่พบรหัสในทะเบียน: ${pre.unknown.slice(0, 12).map(esc).join(', ')}</div>` : ''}
      ${pre.unconverted.length ? `<div style="color:var(--bad-ink)">แปลงหน่วยไม่ได้: ${pre.unconverted.slice(0, 8).map(esc).join(' · ')}</div>` : ''}</div>`;
    if (!pre.ok) { _bmcStatus('ohbCntStatus', 'กรุณาแก้ไขไฟล์ก่อนนำเข้า', 'var(--bad-ink)'); return; }
    const name = document.getElementById('ohbCntBranch').selectedOptions[0]?.textContent || '';
    if (!confirm(`ยืนยันส่งผลตรวจนับ สาขา${name} วันที่ ${day} (${pre.lines} รายการ)\nเมื่อส่งแล้วจะแก้ไขไม่ได้ หากต้องนับใหม่ให้นำเข้าเป็นวันที่ใหม่`)) { _bmcStatus('ohbCntStatus', 'ยกเลิกแล้ว'); return; }
    const d = await _bmcJson(`${API}/api/bom-center/stock/count-import?dry_run=false&${_ohbCntQS()}`, { method: 'POST', body: _ohbFd(file) });
    _bmcStatus('ohbCntStatus', `✓ ส่งผลตรวจนับเลขที่ ${d.count_id} (${d.kind === 'begin' ? 'ต้นงวดแรก' : 'ปลายงวด'}) · ${d.lines} รายการ`, 'var(--good-ink)');
  } catch (e) { _bmcStatus('ohbCntStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
  finally { input.value = ''; }
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
    box.innerHTML = days.length ? days.map(d => `<div class="text-xs font-semibold mt-2 mb-1" style="color:var(--text-primary)">วันส่ง ${esc(d)} · ${byDay[d].length} ใบสั่งซื้อ · ฿${_bmcNum(byDay[d].reduce((a, o) => a + (o.value || 0), 0), 0)}</div>` +
      byDay[d].map(o => `<div style="border-top:1px solid var(--border);padding:6px 0;display:flex;gap:8px;flex-wrap:wrap;align-items:baseline" class="text-xs">
        <a href="#" onclick="ohbOpen(${o.order_id});return false" style="color:var(--accent);font-weight:600">#${o.order_id} ${esc(o.branch)}</a>
        <span>${esc(_OHB_RND[o.round] || o.round)} · วันสั่ง ${esc(o.cycle_date)}</span><span>${o.lines} รายการ · ฿${_bmcNum(o.value, 0)}</span>
        ${o.branch_changes ? _bmcChip(`สาขาแก้ไข ${o.branch_changes} รายการ`, 'var(--amber-ink)') : ''}
        ${o.stock_deducted ? '' : _bmcChip('ไม่หักยอดคงเหลือ', 'var(--text-muted)')}
        ${o.submitted_on_time ? '' : _bmcChip('ส่งหลัง 10:00', 'var(--amber-ink)')}
        ${o.processed_on_time === false ? _bmcChip('ดำเนินการหลัง 14:00', 'var(--bad-ink)') : ''}
        ${o.status === 'processed' ? _bmcChip(`ดำเนินการแล้ว${o.po_ref ? ' · ' + o.po_ref : ''}`, 'var(--good-ink)') : ''}
        ${o.status === 'received' ? _bmcChip('สาขารับแล้ว', 'var(--good-ink)') : ''}</div>`).join('')).join('')
      : '<span class="text-xs" style="color:var(--good-ink)">✓ ไม่มีใบสั่งซื้อในช่วงนี้</span>';
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
      <td class="py-1">${work ? `<select id="ohbR_${esc(l.code)}" style="font-size:11px;border:1px solid var(--border-control);border-radius:6px;background:var(--bg-input);color:var(--text-primary)"><option value="">— เหตุผล (หากส่งไม่ตรง) —</option>${reasons.map(r => `<option value="${r.code}"${l.ss_reason === r.code ? ' selected' : ''}>${esc(r.label)}</option>`).join('')}</select>
        <input id="ohbN_${esc(l.code)}" value="${esc(l.ss_note || '')}" placeholder="หมายเหตุ" style="width:110px;font-size:11px;border:1px solid var(--border-control);border-radius:6px;padding:2px 4px;background:var(--bg-input);color:var(--text-primary)">`
        : (l.ss_reason_th ? `<span class="text-[11px]" style="color:var(--amber-ink)">${esc(l.ss_reason_th)}${l.ss_note ? ' · ' + esc(l.ss_note) : ''}</span>` : '')}
        ${l.received_units != null ? `<div class="text-[10.5px]" style="color:var(--text-muted)">รับจริง ${_bmcNum(l.received_units)}</div>` : ''}</td></tr>`;
  }).join('');
  document.getElementById('ohbDetail').innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap">
      <div class="text-sm font-semibold" style="color:var(--text-primary)">#${o.order_id} ${esc(o.branch || '')} · ${esc(_OHB_RND[o.round] || o.round)} · วันสั่ง ${esc(o.cycle_date)} · วันส่ง ${esc(o.delivery)}</div>
      ${_ohbBtn('ปิด', "document.getElementById('ohbDetail').style.display='none'")}</div>
    <div class="text-xs mt-1" style="color:var(--text-secondary)">${_bmcChip(o.status_th, o.status === 'submitted' ? 'var(--amber-ink)' : 'var(--good-ink)')}
      ${o.stock_count_id ? 'ยอดแนะนำหักยอดคงเหลือแล้ว' : 'สาขาไม่ได้ตรวจนับ ยอดแนะนำจึงไม่หักยอดคงเหลือ'}${o.note ? ' · ' + esc(o.note) : ''}${o.processed_by ? ` · ดำเนินการโดย ${esc(o.processed_by)}` : ''}</div>
    <div style="overflow-x:auto" class="mt-2"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left">
      <th class="py-1 pr-2">วัตถุดิบ</th><th class="py-1 pr-2 text-right">ยอดแนะนำ</th><th class="py-1 pr-2 text-right">สาขายืนยัน</th><th class="py-1 pr-2 text-right">ส่งจริง</th><th class="py-1">เหตุผล (ส่วนกลาง)</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    ${work ? `<div class="flex flex-wrap items-center gap-2 mt-2 text-xs">เลขที่ PO <input id="ohbPo" value="${esc(o.po_ref || '')}" style="width:140px;border:1px solid var(--border-control);border-radius:var(--r-sm);padding:5px 8px;font-size:13px;background:var(--bg-input);color:var(--text-primary)">
      ${_ohbBtn(o.status === 'processed' ? 'บันทึกการแก้ไข' : 'ดำเนินการแล้ว', 'ohbProcess()', true)}<span id="ohbDStatus"></span></div>` : ''}`;
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
  } catch (e) { _bmcStatus('ohbDStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 220), 'var(--bad-ink)'); }
}
async function ohbShowConsolidated() {
  const box = document.getElementById('ohbConsolidated');
  box.style.display = '';
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังรวมข้อมูล…</span>';
  try {
    const c = await _bmcJson(`${API}/api/bom-center/hub/consolidated?${_ohbRange()}`);
    box.innerHTML = `<div style="display:flex;justify-content:space-between"><div class="text-sm font-semibold" style="color:var(--text-primary)">สรุปรวมตามผู้จำหน่ายและวันส่ง (รอดำเนินการและดำเนินการแล้ว)</div>${_ohbBtn('ปิด', "document.getElementById('ohbConsolidated').style.display='none'")}</div>` +
      (c.groups.length ? c.groups.map(g => `<div class="text-xs font-semibold mt-3" style="color:var(--text-primary)">${esc(g.supplier)} · วันส่ง ${esc(g.delivery)}</div>
        <div style="overflow-x:auto"><table class="w-full text-xs"><tbody>${g.items.map(it => `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2">${esc(it.name)} <span style="color:var(--text-muted)">${esc(it.code)}</span></td>
          <td class="py-1 pr-2 text-right"><b>${_bmcNum(it.total)}</b> ${esc(it.unit || '')}</td>
          <td class="py-1" style="color:var(--text-muted)">${Object.entries(it.by_branch).map(([b, q]) => `${esc(b)} ${_bmcNum(q)}`).join(' · ')}</td></tr>`).join('')}</tbody></table></div>`).join('')
        : '<div class="text-xs mt-2" style="color:var(--text-muted)">ไม่มีใบสั่งซื้อในช่วงนี้</div>');
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function ohbDownloadConsolidated() { window.location.href = `${API}/api/bom-center/hub/consolidated.xlsx?${_ohbRange()}`; }

// ── compliance ───────────────────────────────────────────────────────────────
const _OHB_CS = { ok: ['ตามยอดแนะนำ', 'var(--good-ink)'], over: ['สั่งเกินยอดแนะนำ', 'var(--amber-ink)'], under: ['สั่งต่ำกว่ายอดแนะนำ', 'var(--amber-ink)'],
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
        <th class="py-1 pr-2 text-right">ยอดแนะนำ ฿</th><th class="py-1 pr-2 text-right">สาขายืนยัน ฿</th><th class="py-1 pr-2 text-right">ส่วนกลาง ฿</th><th class="py-1 pr-2 text-right">รับจริง ฿</th><th class="py-1">เวลา</th></tr></thead><tbody>` +
      c.rows.map(r => { const [t, ink] = _OHB_CS[r.status] || [r.status, 'var(--text-muted)'];
        return `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2">${esc(r.cycle_date)}</td><td class="py-1 pr-2">${esc(r.branch || '')}</td><td class="py-1 pr-2">${esc(_OHB_RND[r.round] || r.round)}</td>
          <td class="py-1 pr-2">${_bmcChip(t, ink)}</td>
          <td class="py-1 pr-2 text-right">${r.suggested != null ? _bmcNum(r.suggested, 0) : ''}</td>
          <td class="py-1 pr-2 text-right">${r.confirmed != null ? _bmcNum(r.confirmed, 0) : ''} <span style="color:var(--text-muted)">${pct(r.confirmed_vs_suggested_pct)}</span></td>
          <td class="py-1 pr-2 text-right">${r.processed != null ? _bmcNum(r.processed, 0) : ''} <span style="color:var(--text-muted)">${pct(r.centre_change_pct)}</span></td>
          <td class="py-1 pr-2 text-right">${r.received != null ? _bmcNum(r.received, 0) : ''} <span style="color:var(--text-muted)">${pct(r.delivery_gap_pct)}</span></td>
          <td class="py-1">${r.order_id ? (r.submitted_on_time ? '' : _bmcChip('ส่งช้า', 'var(--amber-ink)')) + (r.processed_on_time === false ? _bmcChip('ดำเนินการช้า', 'var(--bad-ink)') : '') : ''}</td></tr>`; }).join('') + '</tbody></table></div>'
      : '<span class="text-xs" style="color:var(--text-muted)">ยังไม่มีสาขาที่ตรวจนับหรือสั่งสินค้าผ่านระบบ รายการจะแสดงเมื่อสาขาเริ่มใช้งาน</span>';
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
    box.innerHTML = m.branches.length ? `<div class="text-sm" style="color:var(--text-primary)">${esc(m.month)} · ตามสูตร ฿${_bmcNum(t.bom_value, 0)} · ผลต่าง <b style="color:${t.gap_value > 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">฿${_bmcNum(t.gap_value, 0)} (${pct(t.gap_pct)})</b> · ใช้เกินสูตร ฿${_bmcNum(t.over_value, 0)}
        ${m.previous && m.previous.total.bom_value ? ` <span class="text-xs" style="color:var(--text-muted)">เดือนก่อน ${pct(m.previous.total.gap_pct)}</span>` : ''}</div>
      <div style="overflow-x:auto" class="mt-2"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left"><th class="py-1 pr-2">สาขา</th><th class="py-1 pr-2 text-right">รอบ</th>
        <th class="py-1 pr-2 text-right">ตามสูตร ฿</th><th class="py-1 pr-2 text-right">ผลต่าง ฿</th><th class="py-1 pr-2 text-right">%</th><th class="py-1 pr-2 text-right">A / B / C ฿</th><th class="py-1 pr-2 text-right">เดือนก่อน</th><th class="py-1">ค้างดำเนินการ</th></tr></thead><tbody>` +
      m.branches.map(b => `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2 font-semibold">${esc(b.branch)}</td><td class="py-1 pr-2 text-right">${b.periods}</td>
        <td class="py-1 pr-2 text-right">${_bmcNum(b.bom_value, 0)}</td><td class="py-1 pr-2 text-right" style="color:${b.gap_value > 0 ? 'var(--bad-ink)' : 'inherit'}">${_bmcNum(b.gap_value, 0)}</td>
        <td class="py-1 pr-2 text-right">${pct(b.gap_pct)}</td><td class="py-1 pr-2 text-right">${['A', 'B', 'C'].map(k => _bmcNum(b.by_class[k], 0)).join(' / ')}</td>
        <td class="py-1 pr-2 text-right" style="color:var(--text-muted)">${pct(b.prev_gap_pct)}</td>
        <td class="py-1">${b.needs_reason ? _bmcChip(`ยังไม่ระบุเหตุผล ${b.needs_reason}`, 'var(--amber-ink)') : ''}${b.complete < b.periods ? _bmcChip('ยังไม่ยืนยันรับสินค้า', 'var(--text-muted)') : ''}</td></tr>`).join('') + '</tbody></table></div>' +
      (m.top_reasons.length ? `<div class="text-xs font-semibold mt-3" style="color:var(--text-primary)">เหตุผลหลัก</div>` + m.top_reasons.map(r => `<div class="text-xs" style="border-top:1px solid var(--border);padding:3px 0">${esc(r.label)} · ${r.items} รายการ · ฿${_bmcNum(r.value, 0)}</div>`).join('') : '')
      : `<span class="text-xs" style="color:var(--text-muted)">ยังไม่มีรอบตรวจนับที่ปิดในเดือน ${esc(m.month)}</span>`;
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}

// ── pilot (S10) ──────────────────────────────────────────────────────────────
async function ohbLoadPilot() {
  const rb = document.getElementById('ohbPilotReady'), sb = document.getElementById('ohbPilotScore');
  rb.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังตรวจสอบ…</span>';
  sb.innerHTML = '';
  try {
    const d = await _bmcJson(`${API}/api/bom-center/hub/pilot`);
    const R = d.readiness;
    const step = s => `<div class="text-xs py-0.5" style="color:${s.done ? 'var(--good-ink)' : 'var(--amber-ink)'}">${s.done ? '✓' : '•'} ${esc(s.todo)}</div>`;
    rb.innerHTML = `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">ความพร้อม</div>
      <div class="text-xs mb-1" style="color:var(--text-muted)">ส่วนกลาง: ทะเบียน ${_bmcNum(R.centre.ingredients, 0)} รายการ · ราคาเดือน ${esc((R.centre.price_months || []).join(', ') || '—')} · สูตรกึ่งสำเร็จ ${R.centre.sub_recipes} · release ที่เผยแพร่แล้ว ${R.centre.releases}</div>
      ${R.centre_steps.map(step).join('')}
      ${R.branches.map(b => `<div style="border-top:1px solid var(--border);padding:6px 0;margin-top:4px">
        <div class="text-sm"><b>${esc(b.branch)}</b> ${b.ready ? _bmcChip('พร้อม', 'var(--good-ink)') : _bmcChip('ยังไม่พร้อม', 'var(--amber-ink)')}
          <span class="text-xs" style="color:var(--text-muted)">${b.accounts.length ? 'บัญชี: ' + b.accounts.map(a => esc(a.username)).join(', ') : ''}${b.first_count ? ` · ตรวจนับครั้งแรก ${esc(b.first_count)} · ล่าสุด ${esc(b.last_count)} (${b.counts} ครั้ง)` : ''}${b.last_order ? ` · ใบสั่งซื้อล่าสุด ${esc(b.last_order)}` : ''}</span></div>
        ${b.steps.map(step).join('')}</div>`).join('')}
      ${R.missing_branches.length ? `<div class="text-xs mt-1" style="color:var(--bad-ink)">ไม่พบสาขาในระบบ: ${R.missing_branches.map(esc).join(', ')}</div>` : ''}`;
    const T = d.scorecard.targets;
    const cell = (v, ok, unit) => v == null ? '<span style="color:var(--text-muted)">—</span>'
      : `<b style="color:${ok === false ? 'var(--bad-ink)' : ok ? 'var(--good-ink)' : 'var(--text-primary)'}">${_bmcNum(v, 1)}${unit}</b>`;
    const vd = { pass: ['ผ่าน', 'var(--good-ink)'], fail: ['ยังไม่ผ่าน', 'var(--bad-ink)'], pending: ['รอข้อมูล', 'var(--text-muted)'], not_started: ['ยังไม่เริ่ม', 'var(--text-muted)'] };
    sb.innerHTML = `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">ผล Pilot ${d.scorecard.weeks} สัปดาห์ล่าสุด</div>
      <div style="overflow-x:auto"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left">
        <th class="py-1 pr-2">สาขา</th><th class="py-1 pr-2 text-right">ตรวจนับครบตามวันสั่ง (≥${T.count_coverage}%)</th><th class="py-1 pr-2 text-right">เวลาตรวจนับ (≤${T.count_minutes} นาที)</th>
        <th class="py-1 pr-2 text-right">ผลต่างมีเหตุผล (≥${T.reason_coverage}%)</th><th class="py-1 pr-2 text-right">พยากรณ์ชั้น A (≤${T.mape_a}%)</th><th class="py-1 pr-2 text-right">ส่งใบสั่งซื้อ</th><th class="py-1">สรุป</th></tr></thead><tbody>` +
      d.scorecard.branches.map(s => { const m = s.metrics, p = s.passes, [vt, vi] = vd[s.verdict];
        return `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2 font-semibold">${esc(s.branch)}<div class="text-[10.5px]" style="color:var(--text-muted)">${esc(s.from)} → ${esc(s.to)} · ${s.cycles} วันสั่ง · ตรวจนับ ${s.counts} ครั้ง</div></td>
          <td class="py-1 pr-2 text-right">${cell(m.count_coverage, p.count_coverage, '%')}</td><td class="py-1 pr-2 text-right">${cell(m.count_minutes, p.count_minutes, '')}</td>
          <td class="py-1 pr-2 text-right">${cell(m.reason_coverage, p.reason_coverage, '%')}</td><td class="py-1 pr-2 text-right">${cell(m.mape_a, p.mape_a, '%')}</td>
          <td class="py-1 pr-2 text-right">${cell(m.order_submission, null, '%')}</td><td class="py-1">${_bmcChip(vt, vi)}</td></tr>`; }).join('') + '</tbody></table></div>';
  } catch (e) { rb.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
