// 14-branch.js — สาขาของฉัน (BoM Center Phase 2: S6 นับสต๊อก · S7 รับของ + ผลต่างของฉัน · S8 สั่งของ).
// Mobile first (375 px). Shares the global scope of slices 01-13; every name here
// starts with `brn`. A branch login is pinned to its own branch by the server
// (auth.scope_branch); central roles pick a branch from the list.

let _brnMe = null;            // /api/bom-center/stock/me
let _brnSheet = null;         // /api/bom-center/stock/sheet
let _brnZone = null;          // the zone on screen
let _brnQty = {};             // code -> what was typed (string) — the working copy
let _brnDirty = false;
let _brnTimer = null;

function _brnBid() {
  const sel = document.getElementById('brnBranch');
  if (_brnMe && _brnMe.scoped) return null;              // the server knows the branch
  return sel && sel.value ? Number(sel.value) : null;
}
function _brnQS() { const b = _brnBid(); return b ? `branch_id=${b}` : ''; }
function _brnCanLayout() { return hasModule('bom.center') || hasModule('supply.ordering'); }

async function loadBranchStock() {
  try {
    _brnMe = await _bmcJson(`${API}/api/bom-center/stock/me`);
    const sel = document.getElementById('brnBranch');
    const nm = document.getElementById('brnBranchName');
    if (_brnMe.scoped) {
      sel.style.display = 'none';
      nm.textContent = _brnMe.branch || '';
    } else {
      const keep = sel.value;
      sel.innerHTML = '<option value="">— เลือกสาขา —</option>' +
        _brnMe.branches.map(b => `<option value="${b.branch_id}">${esc(b.name)}</option>`).join('');
      if (keep) sel.value = keep;
      sel.style.display = '';
      nm.textContent = '';
    }
    await brnBranchChanged();
  } catch (e) {
    document.getElementById('brnList').innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`;
  }
}

async function brnLoadSheet() {
  const list = document.getElementById('brnList');
  if (!(_brnMe && _brnMe.scoped) && !_brnBid()) {
    list.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กรุณาเลือกสาขาด้านบน</span>';
    document.getElementById('brnZones').innerHTML = '';
    document.getElementById('brnHistory').innerHTML = '';
    return;
  }
  list.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลดรายการ…</span>';
  try {
    _brnSheet = await _bmcJson(`${API}/api/bom-center/stock/sheet?${_brnQS()}`);
    _brnQty = {};
    _brnSheet.zones.forEach(z => z.items.forEach(it => { if (it.draft_qty != null) _brnQty[it.code] = String(it.draft_qty); }));
    _brnDirty = false;
    if (!_brnZone || !_brnSheet.zones.some(z => z.zone === _brnZone)) _brnZone = (_brnSheet.zones[0] || {}).zone || null;
    const s = _brnSheet;
    document.getElementById('brnKind').innerHTML = _bmcChip(s.next_kind === 'begin' ? 'รอบนี้: ตรวจนับต้นงวด (ครั้งแรก)' : 'รอบนี้: ตรวจนับปลายงวด (ใช้เป็นต้นงวดรอบถัดไป)',
      s.next_kind === 'begin' ? 'var(--navy-ink)' : 'var(--good-ink)');
    document.getElementById('brnInfo').textContent =
      (s.last_count ? `ตรวจนับล่าสุด ${s.last_count.count_date} (${s.last_count.kind === 'begin' ? 'ต้นงวด' : 'ปลายงวด'}) โดย ${s.last_count.counted_by || '-'}` : 'ยังไม่มีผลตรวจนับ') +
      (s.draft ? ` · มีร่างที่ยังไม่ส่ง (${String(s.draft.updated_at || '').slice(0, 16).replace('T', ' ')})` : '') +
      ` · ${s.items} รายการ (บังคับ ${s.required}) คัดจากการใช้จริง 4 สัปดาห์ ถึงวันที่ ${s.as_of}`;
    brnRender();
    brnLoadHistory();
  } catch (e) { list.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}

function brnSetZone(z) { _brnZone = z; brnRender(); window.scrollTo({ top: 0 }); }

function brnRender() {
  const s = _brnSheet;
  if (!s) return;
  const counted = c => _brnQty[c] !== undefined && _brnQty[c] !== '';
  const all = s.zones.flatMap(z => z.items);
  const req = all.filter(i => i.required);
  document.getElementById('brnProgress').innerHTML =
    `ตรวจนับแล้ว <b style="color:var(--text-primary)">${all.filter(i => counted(i.code)).length}/${all.length}</b> · บังคับ <b style="color:${req.every(i => counted(i.code)) ? 'var(--good-ink)' : 'var(--amber-ink)'}">${req.filter(i => counted(i.code)).length}/${req.length}</b>`;
  document.getElementById('brnZones').innerHTML = s.zones.map(z => {
    const done = z.items.filter(i => counted(i.code)).length;
    const on = z.zone === _brnZone;
    return `<button onclick="brnSetZone('${z.zone}')" style="border:1px solid ${on ? 'var(--accent)' : 'var(--border)'};border-radius:999px;padding:6px 12px;font-size:13px;cursor:pointer;background:${on ? 'var(--accent)' : 'transparent'};color:${on ? 'var(--on-accent)' : 'var(--text-primary)'}">${esc(z.label)} ${done}/${z.items.length}</button>`;
  }).join('');
  const zone = s.zones.find(z => z.zone === _brnZone) || { items: [] };
  const q = (document.getElementById('brnQ').value || '').trim().toLowerCase();
  const reqOnly = document.getElementById('brnRequiredOnly').checked;
  const items = (q ? all : zone.items).filter(i => (!reqOnly || i.required) && (!q || (i.code + ' ' + i.name).toLowerCase().includes(q)));
  const zoneSel = it => !_brnCanLayout() ? '' : `<select onchange="brnSetLayout(${_bmcQ(it.code)}, this.value)" title="โซนจัดเก็บ (กำหนดโดยส่วนกลาง)" style="font-size:11px;border:1px solid var(--border);border-radius:6px;background:var(--bg-input);color:var(--text-muted);padding:1px 2px">${(_brnMe.zones || []).map(z => `<option value="${z.zone}"${z.zone === (s.zones.find(zz => zz.items.includes(it)) || {}).zone ? ' selected' : ''}>${esc(z.label)}</option>`).join('')}</select>`;
  document.getElementById('brnList').innerHTML = items.length ? items.map(it => {
    const v = _brnQty[it.code] ?? '';
    const last = it.last_qty != null ? `ครั้งก่อน ${_bmcNum(it.last_qty, 2)} ${esc(it.last_unit || it.count_unit || '')}` : '';
    return `<div style="display:flex;flex-wrap:wrap;gap:6px 8px;align-items:center;border-top:1px solid var(--border);padding:8px 0">
      <div style="flex:1 1 220px;min-width:0">
        <div class="text-sm" style="color:var(--text-primary);font-weight:600;overflow-wrap:anywhere">${esc(it.name)}${it.required ? ' ' + _bmcChip('บังคับ', 'var(--amber-ink)') : ''}</div>
        <div class="text-[11px]" style="color:var(--text-muted)">${esc(it.code)}${it.per_count_unit && it.count_unit !== it.base_unit ? ` · 1 ${esc(it.count_unit)} = ${_bmcNum(it.per_count_unit)} ${esc(it.base_unit || '')}` : ''}${last ? ' · ' + last : ''} ${zoneSel(it)}</div>
      </div>
      <div style="display:flex;gap:6px;align-items:center;margin-left:auto">
      <button onclick="brnStep(${_bmcQ(it.code)}, -1)" aria-label="ลด" style="width:36px;height:36px;border:1px solid var(--border);border-radius:10px;background:transparent;color:var(--text-primary);font-size:18px;cursor:pointer">−</button>
      <input inputmode="decimal" value="${esc(v)}" placeholder="${it.last_qty != null ? esc(String(it.last_qty)) : '0'}" oninput="brnSet(${_bmcQ(it.code)}, this.value)"
             style="width:68px;height:36px;text-align:center;font-size:16px;border:1px solid ${v !== '' ? 'var(--accent)' : 'var(--border-control)'};border-radius:10px;background:var(--bg-input);color:var(--text-primary)">
      <button onclick="brnStep(${_bmcQ(it.code)}, 1)" aria-label="เพิ่ม" style="width:36px;height:36px;border:1px solid var(--border);border-radius:10px;background:transparent;color:var(--text-primary);font-size:18px;cursor:pointer">+</button>
      <span class="text-xs" style="width:64px;color:var(--text-secondary);overflow-wrap:anywhere">${esc(it.count_unit || it.base_unit || '')}</span>
      </div>
    </div>`;
  }).join('') : '<span class="text-xs" style="color:var(--text-muted)">ไม่มีรายการในโซนนี้</span>';
}

function _brnItem(code) { return _brnSheet.zones.flatMap(z => z.items).find(i => i.code === code); }
function brnSet(code, v) {
  _brnQty[code] = String(v || '').replace(',', '.').trim();
  _brnDirty = true;
  clearTimeout(_brnTimer);
  _brnTimer = setTimeout(() => brnSave(false, true), 2500);      // autosave the draft — a lost signal loses nothing
}
function brnStep(code, d) {
  const cur = Number(_brnQty[code] || 0) || 0;
  brnSet(code, String(Math.max(0, Math.round((cur + d) * 100) / 100)));
  brnRender();
}

function _brnLines() {
  return Object.entries(_brnQty).map(([code, v]) => {
    const it = _brnItem(code);
    return { code, qty: v === '' ? null : Number(v), unit: it ? (it.count_unit || it.base_unit) : null };
  }).filter(l => l.qty === null || !isNaN(l.qty));
}

async function brnSave(submit, quiet) {
  if (!_brnSheet) return;
  clearTimeout(_brnTimer);
  if (submit) {
    const req = _brnSheet.zones.flatMap(z => z.items).filter(i => i.required);
    const missing = req.filter(i => _brnQty[i.code] === undefined || _brnQty[i.code] === '');
    const msg = (missing.length ? `ยังไม่ได้ตรวจนับรายการบังคับ ${missing.length} รายการ (เช่น ${missing.slice(0, 3).map(i => i.name).join(', ')})\n` : '')
      + `ยืนยันส่งผลตรวจนับเป็น${_brnSheet.next_kind === 'begin' ? 'ต้นงวด' : 'ปลายงวด'}?\nเมื่อส่งแล้วจะแก้ไขไม่ได้ หากต้องการแก้ไข กรุณาตรวจนับใหม่เป็นครั้งใหม่`;
    if (!confirm(msg)) return;
  } else if (!_brnDirty && quiet) return;
  try {
    const r = await _bmcJson(`${API}/api/bom-center/stock/count?${_brnQS()}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lines: _brnLines(), submit }) });
    _brnDirty = false;
    if (submit) {
      _bmcStatus('brnStatus', `✓ ส่งผลตรวจนับแล้ว ${r.lines} รายการ`, 'var(--good-ink)');
      await brnLoadSheet();
    } else {
      _bmcStatus('brnStatus', `✓ บันทึกร่าง ${r.lines} รายการ ${new Date().toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' })}`
        + (r.unconverted && r.unconverted.length ? ` · แปลงหน่วยไม่สำเร็จ ${r.unconverted.length} รายการ` : ''), r.unconverted && r.unconverted.length ? 'var(--amber-ink)' : 'var(--text-muted)');
    }
  } catch (e) { _bmcStatus('brnStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}

async function brnDiscard() {
  if (!confirm('ยืนยันล้างร่างผลตรวจนับทั้งหมด?')) return;
  try {
    await _bmcJson(`${API}/api/bom-center/stock/draft?${_brnQS()}`, { method: 'DELETE' });
    await brnLoadSheet();
    _bmcStatus('brnStatus', 'ล้างร่างแล้ว', 'var(--text-muted)');
  } catch (e) { _bmcStatus('brnStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}

async function brnLoadHistory() {
  const box = document.getElementById('brnHistory');
  try {
    const d = await _bmcJson(`${API}/api/bom-center/stock/counts?${_brnQS()}`);
    box.innerHTML = d.counts.length ? d.counts.map(c => `<div style="border-top:1px solid var(--border);padding:4px 0">
        ${esc(c.count_date)} · ${c.status === 'draft' ? _bmcChip('ร่าง', 'var(--amber-ink)') : (c.kind === 'begin' ? 'ต้นงวด' : 'ปลายงวด')} · ${_bmcNum(c.lines, 0)} รายการ · ${esc(c.counted_by || '')}</div>`).join('')
      : 'ยังไม่มีข้อมูล';
  } catch (e) { box.textContent = String(e.message || e); }
}

async function brnSetLayout(code, zone) {
  try {
    await _bmcJson(`${API}/api/bom-center/stock/layout`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify([{ code, zone }]) });
    if (_brnDirty) await brnSave(false, true);
    await brnLoadSheet();
  } catch (e) { _bmcStatus('brnStatus', 'กำหนดโซนไม่สำเร็จ: ' + String(e.message || e).slice(0, 160), 'var(--bad-ink)'); }
}

// ── sub-tabs (S7): นับสต๊อก · รับของ · ผลต่างของฉัน ───────────────────────────
let _brnSub = 'count';
let _brnRecv = [];            // lines waiting to be saved: {code, name, qty, unit}
let _brnVar = null;           // /api/bom-center/variance/period

function brnSetSub(k) {
  _brnSub = k;
  document.querySelectorAll('#tab-branchstock [data-brnsub]').forEach(b => b.classList.toggle('active', b.dataset.brnsub === k));
  ['count', 'order', 'recv', 'var'].forEach(p => { const el = document.getElementById('brn-pane-' + p); if (el) el.style.display = p === k ? '' : 'none'; });
  if (k === 'order') brnLoadOrder();
  if (k === 'recv') brnLoadRecv();
  if (k === 'var') brnLoadVarPane();
}
async function brnBranchChanged() {
  _brnVar = null;
  _brnRecv = [];
  _brnPlan = null;
  await brnLoadSheet();
  if (_brnSub === 'order') brnLoadOrder();
  if (_brnSub === 'recv') brnLoadRecv();
  if (_brnSub === 'var') brnLoadVarPane();
}
function _brnNeedBranch(boxId) {
  if (!(_brnMe && _brnMe.scoped) && !_brnBid()) {
    const el = document.getElementById(boxId);
    if (el) el.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กรุณาเลือกสาขาด้านบน</span>';
    return true;
  }
  return false;
}

// ── รับของ ───────────────────────────────────────────────────────────────────
function _brnAllItems() { return _brnSheet ? _brnSheet.zones.flatMap(z => z.items) : []; }
async function brnLoadRecv() {
  const d = document.getElementById('brnRecvDate');
  if (d && !d.value) d.value = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  const dl = document.getElementById('brnItemList');
  if (dl) dl.innerHTML = _brnAllItems().map(i => `<option value="${esc(i.code)} — ${esc(i.name)}"></option>`).join('');
  brnRenderRecvLines();
  if (_brnNeedBranch('brnRecvHistory')) return;
  try {
    const r = await _bmcJson(`${API}/api/bom-center/stock/receipts?${_brnQS()}`);
    document.getElementById('brnRecvHistory').innerHTML = r.receipts.length ? r.receipts.map(x => `<div style="border-top:1px solid var(--border);padding:5px 0">
        <b>${esc(x.receipt_date)}</b>${x.ref ? ' · ' + esc(x.ref) : ''} · ${x.lines.length} รายการ · ${esc(x.entered_by || '')}
        ${x.locked ? _bmcChip('อยู่ในรอบที่ปิดแล้ว', 'var(--text-muted)') : `<button onclick="brnRecvDelete(${x.receipt_id})" style="font-size:11px;color:var(--bad-ink);background:none;border:0;cursor:pointer">ลบ</button>`}
        <div style="color:var(--text-muted)">${x.lines.map(l => `${esc(l.name || l.code)} ${_bmcNum(l.qty)} ${esc(l.unit || '')}`).join(' · ')}</div></div>`).join('')
      : 'ยังไม่มีข้อมูล';
  } catch (e) { document.getElementById('brnRecvHistory').textContent = String(e.message || e); }
}
function _brnPicked() {
  const v = (document.getElementById('brnRecvItem').value || '').trim();
  const code = v.split(' — ')[0].trim();
  return _brnAllItems().find(i => i.code === code || i.name === v);
}
function brnRecvPicked() {
  const it = _brnPicked();
  document.getElementById('brnRecvUnit').textContent = it ? (it.count_unit || it.base_unit || '') : '';
}
function brnRecvAdd() {
  const it = _brnPicked();
  const q = Number(String(document.getElementById('brnRecvQty').value || '').replace(',', '.'));
  if (!it) { _bmcStatus('brnRecvStatus', 'กรุณาเลือกวัตถุดิบจากรายการ', 'var(--amber-ink)'); return; }
  if (!(q > 0)) { _bmcStatus('brnRecvStatus', 'กรุณาระบุจำนวนมากกว่า 0', 'var(--amber-ink)'); return; }
  const unit = it.count_unit || it.base_unit;
  const same = _brnRecv.find(l => l.code === it.code && l.unit === unit);
  if (same) same.qty += q; else _brnRecv.push({ code: it.code, name: it.name, qty: q, unit });
  document.getElementById('brnRecvItem').value = '';
  document.getElementById('brnRecvQty').value = '';
  document.getElementById('brnRecvUnit').textContent = '';
  _bmcStatus('brnRecvStatus', '');
  brnRenderRecvLines();
  document.getElementById('brnRecvItem').focus();
}
function brnRenderRecvLines() {
  const box = document.getElementById('brnRecvLines');
  if (!box) return;
  box.innerHTML = _brnRecv.length ? _brnRecv.map((l, i) => `<div style="display:flex;gap:8px;align-items:center;border-top:1px solid var(--border);padding:5px 0">
      <span style="flex:1">${esc(l.name)} <span class="text-[11px]" style="color:var(--text-muted)">${esc(l.code)}</span></span>
      <b>${_bmcNum(l.qty)}</b> <span class="text-xs" style="color:var(--text-secondary)">${esc(l.unit || '')}</span>
      <button onclick="_brnRecv.splice(${i},1);brnRenderRecvLines()" style="font-size:11px;color:var(--bad-ink);background:none;border:0;cursor:pointer">ลบ</button></div>`).join('')
    : '<span class="text-xs" style="color:var(--text-muted)">ยังไม่มีรายการ · เลือกวัตถุดิบ ระบุจำนวน แล้วกด “+ เพิ่ม”</span>';
}
async function brnRecvSave() {
  if (!_brnRecv.length) { _bmcStatus('brnRecvStatus', 'ยังไม่มีรายการ', 'var(--amber-ink)'); return; }
  try {
    const r = await _bmcJson(`${API}/api/bom-center/stock/receipt?${_brnQS()}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ receipt_date: document.getElementById('brnRecvDate').value, ref: document.getElementById('brnRecvRef').value || null,
        lines: _brnRecv.map(l => ({ code: l.code, qty: l.qty, unit: l.unit })) }) });
    _brnRecv = [];
    document.getElementById('brnRecvRef').value = '';
    _bmcStatus('brnRecvStatus', `✓ บันทึกรับสินค้า ${r.lines} รายการ`, 'var(--good-ink)');
    brnLoadRecv();
  } catch (e) { _bmcStatus('brnRecvStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
async function brnRecvDelete(id) {
  if (!confirm('ยืนยันลบบันทึกรับสินค้านี้?')) return;
  try {
    await _bmcJson(`${API}/api/bom-center/stock/receipt/${id}?${_brnQS()}`, { method: 'DELETE' });
    brnLoadRecv();
  } catch (e) { _bmcStatus('brnRecvStatus', 'ลบไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}

// ── ผลต่างของฉัน ─────────────────────────────────────────────────────────────
const _BRN_VS = { over: ['ใช้เกินสูตร', 'var(--bad-ink)'], under: ['ใช้น้อยกว่าสูตร', 'var(--amber-ink)'],
  negative: ['คงเหลือมากกว่าที่ควรมี', 'var(--bad-ink)'], no_bom: ['มีการใช้แต่ไม่มีในสูตร', 'var(--amber-ink)'],
  not_counted: ['ตรวจนับไม่ครบ', 'var(--text-muted)'], ok: ['ตามสูตร', 'var(--good-ink)'] };

async function brnLoadVarPane() {
  const ov = document.getElementById('brnVarOverview');
  if (_brnMe && !_brnMe.scoped && (hasModule('supply.ordering') || hasModule('bom.center') || hasModule('ops.report'))) {
    try {
      const d = await _bmcJson(`${API}/api/bom-center/variance/overview`);
      ov.style.display = '';
      ov.innerHTML = `<div class="text-xs font-semibold mb-1" style="color:var(--text-primary)">ผลต่างรอบล่าสุดของสาขาที่ตรวจนับแล้ว</div>` + (d.branches.length ? d.branches.map(b => `<div style="border-top:1px solid var(--border);padding:4px 0;display:flex;gap:8px;flex-wrap:wrap;align-items:baseline" class="text-xs">
          <a href="#" onclick="brnPickBranch(${b.branch_id});return false" style="color:var(--accent);font-weight:600">${esc(b.branch)}</a>
          ${b.end_date ? `<span>${esc(b.begin_date)} → ${esc(b.end_date)}</span><span>ผลต่าง <b style="color:${(b.gap_value || 0) > 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">฿${_bmcNum(b.gap_value, 0)}</b>${b.gap_pct != null ? ` (${b.gap_pct > 0 ? '+' : ''}${_bmcNum(b.gap_pct, 1)}%)` : ''}</span>
            <span>เกินเกณฑ์ ${b.flagged} · ยังไม่ระบุเหตุผล ${b.needs_reason}</span>${b.complete ? _bmcChip('รับสินค้าครบ', 'var(--good-ink)') : _bmcChip('ยังไม่สมบูรณ์', 'var(--amber-ink)')}`
            : `<span style="color:var(--text-muted)">ตรวจนับแล้ว 1 ครั้ง (${esc(b.last_count || '')}) · รอตรวจนับปลายงวด</span>`}</div>`).join('') : '<span class="text-xs" style="color:var(--text-muted)">ยังไม่มีสาขาที่ตรวจนับ</span>');
    } catch (e) { ov.style.display = 'none'; }
  } else if (ov) ov.style.display = 'none';
  if (_brnNeedBranch('brnVarList')) { document.getElementById('brnVarHead').innerHTML = ''; return; }
  try {
    const d = await _bmcJson(`${API}/api/bom-center/variance/periods?${_brnQS()}`);
    const sel = document.getElementById('brnVarPeriod');
    sel.innerHTML = d.periods.map(p => `<option value="${p.end_count_id}">${esc(p.begin_date)} → ${esc(p.end_date)} (${p.days} วัน)${p.complete ? '' : ' · ยังไม่สมบูรณ์'}</option>`).join('');
    if (!d.periods.length) {
      document.getElementById('brnVarHead').innerHTML = '';
      document.getElementById('brnVarList').innerHTML = '<span class="text-xs" style="color:var(--text-muted)">ยังไม่มีรอบสำหรับเปรียบเทียบ ต้องตรวจนับอย่างน้อย 2 ครั้ง (ต้นงวดและปลายงวด)</span>';
      return;
    }
    await brnLoadVariance(sel.value);
  } catch (e) { document.getElementById('brnVarList').innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function brnPickBranch(id) {
  const sel = document.getElementById('brnBranch');
  sel.value = String(id);
  brnBranchChanged();
}
async function brnLoadVariance(endId) {
  try {
    _brnVar = await _bmcJson(`${API}/api/bom-center/variance/period?${_brnQS()}${endId ? '&end_count_id=' + endId : ''}`);
    brnRenderVariance();
  } catch (e) { document.getElementById('brnVarList').innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function brnRenderVariance() {
  const v = _brnVar;
  if (!v) return;
  document.getElementById('brnVarHead').innerHTML = `<div style="color:var(--text-secondary)">${esc(v.begin_date)} → ${esc(v.end_date)} · วันขาย ${v.trading_days} วัน · ราคาเดือน ${esc(v.price_month)} ·
      รับสินค้า ${v.receipts} ใบ · ตามสูตร ฿${_bmcNum(v.bom_value, 0)}</div>
    <div class="mt-1 text-sm">ผลต่างรวม <b style="color:${v.gap_value > 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">฿${_bmcNum(v.gap_value, 0)}</b>${v.gap_pct != null ? ` (${v.gap_pct > 0 ? '+' : ''}${_bmcNum(v.gap_pct, 1)}% ของยอดตามสูตร)` : ''} ·
      ใช้เกินสูตร ฿${_bmcNum(v.over_value, 0)} · เกินเกณฑ์ ${v.flagged} · ยังไม่ระบุเหตุผล <b style="color:${v.needs_reason ? 'var(--amber-ink)' : 'var(--good-ink)'}">${v.needs_reason}</b>
      ${v.not_counted ? ` · ตรวจนับไม่ครบ ${v.not_counted}` : ''}</div>
    <div class="mt-2">${v.complete
      ? `${_bmcChip('รับสินค้าครบแล้ว', 'var(--good-ink)')} <span style="color:var(--text-muted)">โดย ${esc(v.receipts_confirmed_by || '')}</span> <button onclick="brnConfirmRecv(true)" style="font-size:11px;color:var(--text-muted);background:none;border:0;cursor:pointer">ยกเลิก</button>`
      : `${_bmcChip('ยังไม่สมบูรณ์', 'var(--amber-ink)')} <span style="color:var(--text-muted)">หากบันทึกรับสินค้าไม่ครบ ผลจะแสดงเป็น “ใช้น้อยกว่าสูตร”</span>
         <button onclick="brnConfirmRecv(false)" style="border:1px solid var(--border);border-radius:var(--r-sm);padding:3px 10px;font-size:12px;cursor:pointer;background:transparent;color:var(--text-primary)">ยืนยันบันทึกรับสินค้ารอบนี้ครบแล้ว</button>`}
      <span id="brnVarStatus" class="text-xs"></span></div>`;
  const all = document.getElementById('brnVarAll').checked;
  const rows = v.rows.filter(r => all || r.status !== 'ok').slice(0, all ? 500 : 60);
  const opts = r => v.reasons.map(o => `<option value="${o.code}"${r.reason === o.code ? ' selected' : ''}>${esc(o.label)}</option>`).join('');
  const qty = (x, u) => x == null ? '—' : `${_bmcNum(x, 0)} ${esc(u || '')}`;
  document.getElementById('brnVarList').innerHTML = rows.length ? rows.map(r => {
    const [label, ink] = _BRN_VS[r.status];
    const flag = ['over', 'under', 'negative', 'no_bom'].includes(r.status);
    return `<div style="border-top:1px solid var(--border);padding:8px 0">
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:baseline"><b class="text-sm" style="color:var(--text-primary)">${esc(r.name)}</b>
        ${_bmcChip(label, ink)} <span class="text-[11px]" style="color:var(--text-muted)">${esc(r.code)} · ชั้น ${r.class} (เกณฑ์ ±${v.thresholds[r.class]}%)</span>
        <span style="margin-left:auto" class="text-sm">${r.gap_value != null ? `<b style="color:${r.gap_value > 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">฿${_bmcNum(r.gap_value, 0)}</b>` : ''}</span></div>
      <div class="text-xs mt-0.5" style="color:var(--text-secondary)">ต้น ${qty(r.begin, r.base_unit)} + รับ ${qty(r.received, r.base_unit)} − ปลาย ${qty(r.end, r.base_unit)} = ใช้จริง <b>${qty(r.actual, r.base_unit)}</b> · ตามสูตร <b>${qty(r.bom, r.base_unit)}</b>
        ${r.gap != null ? ` · ผลต่าง ${r.gap > 0 ? '+' : ''}${_bmcNum(r.gap, 0)}${r.gap_pct != null ? ` (${r.gap_pct > 0 ? '+' : ''}${_bmcNum(r.gap_pct, 1)}%)` : ''}` : ''}</div>
      ${flag ? `<div class="flex flex-wrap items-center gap-2 mt-1 text-xs">
        <select id="brnRs_${esc(r.code)}" style="border:1px solid ${r.needs_reason ? 'var(--amber-ink)' : 'var(--border-control)'};border-radius:var(--r-sm);padding:4px 6px;font-size:12px;background:var(--bg-input);color:var(--text-primary)"><option value="">— เหตุผล —</option>${opts(r)}</select>
        <input id="brnRn_${esc(r.code)}" value="${esc(r.reason_note || '')}" placeholder="หมายเหตุ" style="flex:1 1 140px;border:1px solid var(--border-control);border-radius:var(--r-sm);padding:4px 6px;font-size:12px;background:var(--bg-input);color:var(--text-primary)">
        <button onclick="brnSaveReason(${_bmcQ(r.code)})" style="border:1px solid var(--border);border-radius:var(--r-sm);padding:3px 10px;font-size:12px;cursor:pointer;background:transparent;color:var(--text-primary)">บันทึก</button>
        ${r.reason_by ? `<span style="color:var(--text-muted)">${esc(r.reason_by)}</span>` : ''}</div>` : ''}
    </div>`;
  }).join('') : '<span class="text-xs" style="color:var(--good-ink)">✓ ทุกรายการอยู่ในเกณฑ์</span>';
}
async function brnSaveReason(code) {
  const reason = document.getElementById('brnRs_' + code).value;
  const note = document.getElementById('brnRn_' + code).value;
  if (!reason) { _bmcStatus('brnVarStatus', 'กรุณาเลือกเหตุผล', 'var(--amber-ink)'); return; }
  try {
    await _bmcJson(`${API}/api/bom-center/variance/reason?${_brnQS()}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ end_count_id: _brnVar.end_count_id, code, reason, note }) });
    await brnLoadVariance(_brnVar.end_count_id);
    _bmcStatus('brnVarStatus', '✓ บันทึกเหตุผลแล้ว', 'var(--good-ink)');
  } catch (e) { _bmcStatus('brnVarStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
async function brnConfirmRecv(undo) {
  if (!undo && !confirm('ยืนยันว่าได้บันทึกรับสินค้าทุกรายการในรอบนี้ครบแล้ว?')) return;
  try {
    await _bmcJson(`${API}/api/bom-center/variance/confirm-receipts?${_brnQS()}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ end_count_id: _brnVar.end_count_id, undo: !!undo }) });
    await brnLoadVariance(_brnVar.end_count_id);
  } catch (e) { _bmcStatus('brnVarStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}

// ── สั่งของ (S8) ─────────────────────────────────────────────────────────────
let _brnRound = '1';
let _brnPlan = null;          // /api/bom-center/order/plan
let _brnOrdQty = {};          // code -> confirmed units (string)
let _brnOrdReason = {};       // code -> {reason_code, note}
let _brnRecvMode = false;     // receiving an order that arrived
const _BRN_ROUNDS = [['1', 'รอบ 1 (อ./พ.)'], ['2', 'รอบ 2 (ศ./ส.)'], ['salmon', 'แซลมอนสด']];

async function brnLoadOrder() {
  document.getElementById('brnOrdRounds').innerHTML = _BRN_ROUNDS.map(([k, t]) => {
    const on = k === _brnRound;
    return `<button onclick="brnSetRound('${k}')" style="border:1px solid ${on ? 'var(--accent)' : 'var(--border)'};border-radius:999px;padding:6px 12px;font-size:13px;cursor:pointer;background:${on ? 'var(--accent)' : 'transparent'};color:${on ? 'var(--on-accent)' : 'var(--text-primary)'}">${t}</button>`;
  }).join('');
  if (_brnNeedBranch('brnOrdList')) { document.getElementById('brnOrdHead').innerHTML = ''; return; }
  document.getElementById('brnOrdList').innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังคำนวณยอดแนะนำ…</span>';
  _bmcStatus('brnOrdStatus', '');
  try {
    _brnPlan = await _bmcJson(`${API}/api/bom-center/order/plan?round=${_brnRound}${_brnQS() ? '&' + _brnQS() : ''}`);
    _brnOrdQty = {}; _brnOrdReason = {}; _brnRecvMode = false;
    const o = _brnPlan.order;
    _brnPlan.rows.forEach(r => { _brnOrdQty[r.code] = String(r.order_units); });
    if (o) o.lines.forEach(l => {
      _brnOrdQty[l.code] = l.confirmed_units == null ? '' : String(l.confirmed_units);
      if (l.reason_code) _brnOrdReason[l.code] = { reason_code: l.reason_code, note: l.note || '' };
    });
    brnRenderOrder();
    brnLoadOrderHistory();
  } catch (e) { document.getElementById('brnOrdList').innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function brnSetRound(k) { _brnRound = k; brnLoadOrder(); }
function _brnOrdNeedsReason(sugg, conf) {
  const s = Number(sugg || 0), c = Number(conf || 0);
  if (s === c) return false;
  if (s === 0) return c > 0;
  return Math.abs(c - s) > Math.max(1, s * (_brnPlan.deviation_pct || 20) / 100);
}
function _brnUnitLabel(r) { return r.order_unit === 'pack' ? (r.recv_unit || 'แพ็ค') : (r.base_unit || ''); }

// S11: page 1 = the centre's list (sent to Sales Support) · page 2 = local suppliers (the branch's own list)
let _brnOrdPage = 'central';
function brnSetOrdPage(k) { _brnOrdPage = k; brnRenderOrder(); }
function _brnRenderPages(p) {
  const box = document.getElementById('brnOrdPages');
  if (!p.central_list) { box.innerHTML = ''; _brnOrdPage = 'central'; return; }
  const nLocal = (p.local_rows || []).filter(r => r.order_units > 0).length;
  const b = (k, t) => { const on = k === _brnOrdPage;
    return `<button onclick="brnSetOrdPage('${k}')" style="border:1px solid ${on ? 'var(--accent)' : 'var(--border)'};border-radius:var(--r-sm);padding:6px 12px;font-size:13px;cursor:pointer;background:${on ? 'var(--accent-soft, transparent)' : 'transparent'};color:var(--text-primary);font-weight:${on ? 600 : 400}">${t}</button>`; };
  box.innerHTML = b('central', `สั่งจากส่วนกลาง (${p.rows.filter(r => r.order_units > 0).length})`) + b('local', `จัดซื้อในพื้นที่ (${nLocal})`);
}
function _brnRenderLocal(p) {
  const q = (document.getElementById('brnOrdQ').value || '').trim().toLowerCase();
  const only = document.getElementById('brnOrdOnly').checked;
  const rows = (p.local_rows || []).filter(r => (!only || r.order_units > 0) && (!q || (r.code + ' ' + r.name).toLowerCase().includes(q)));
  document.getElementById('brnOrdList').innerHTML = `<div class="text-xs mb-1" style="color:var(--text-muted)">สินค้านอกบัญชีส่วนกลาง สาขาจัดซื้อจากผู้จำหน่ายในพื้นที่ · ยอดแนะนำคำนวณจากพยากรณ์ + สต๊อกสำรอง − คงเหลือ · รายการนี้ไม่ส่งไปยังส่วนกลาง</div>` +
    (rows.map(r => `<div style="border-top:1px solid var(--border);padding:8px 0;display:flex;flex-wrap:wrap;gap:6px 8px;align-items:baseline">
      <div style="flex:1 1 220px;min-width:0"><div class="text-sm" style="color:var(--text-primary);font-weight:600">${esc(r.name)}
        ${r.kind === 'prepared' ? _bmcChip('ของเตรียม: ยังไม่มีสูตรกึ่งสำเร็จ จึงคำนวณเป็นวัตถุดิบไม่ได้', 'var(--amber-ink)') : ''}</div>
        <div class="text-[11px]" style="color:var(--text-muted)">${esc(r.code)} · ชั้น ${r.class} · ต้องใช้ ${_bmcNum(r.need, 0)} ${esc(r.base_unit || '')}${r.on_hand != null ? '' : ' (ไม่มีผลตรวจนับ จึงไม่หัก)'}</div></div>
      <div class="text-sm" style="margin-left:auto;color:var(--text-primary)">แนะนำ <b>${_bmcNum(r.order_units, 0)}</b> ${esc(_brnUnitLabel(r))}</div></div>`).join('')
      || '<span class="text-xs" style="color:var(--good-ink)">✓ รอบนี้ไม่มีรายการที่ต้องจัดซื้อเอง</span>');
  document.getElementById('brnOrdActions').innerHTML = `<span class="text-xs" style="color:var(--text-muted)">รายการนี้ใช้ประกอบการจัดซื้อของสาขาเท่านั้น ไม่ส่งไปยังส่วนกลาง</span>`;
}

function brnRenderOrder() {
  const p = _brnPlan;
  if (!p) return;
  _brnRenderPages(p);
  if (_brnOrdPage === 'local' && p.central_list) { _brnRenderLocal(p); return; }
  const o = p.order;
  const editable = !o || ['suggested', 'confirmed'].includes(o.status);
  const stockLine = p.stock.deducted
    ? `${_bmcChip('หักยอดคงเหลือแล้ว', 'var(--good-ink)')} จากผลตรวจนับวันที่ ${esc(p.stock.basis.date)} (บวกรับเข้า หักการใช้ตามยอดขาย)`
    : `${_bmcChip('ไม่หักยอดคงเหลือ', 'var(--amber-ink)')} ยังไม่มีผลตรวจนับ ยอดแนะนำการสั่งจึงเท่ากับยอดพยากรณ์ <a href="#" onclick="brnSetSub('count');return false" style="color:var(--accent)">ตรวจนับก่อนสั่ง →</a>`;
  const byCode = {};
  p.rows.forEach(r => { byCode[r.code] = r; });
  const extra = o ? o.lines.filter(l => !byCode[l.code]) : [];
  const total = Object.entries(_brnOrdQty).reduce((a, [c, v]) => {
    const r = byCode[c];
    if (!r || !r.price_per_base || !(Number(v) > 0)) return a;
    return a + Number(v) * (r.order_unit === 'pack' ? (r.qty_per_recv || 1) : 1) * r.price_per_base;
  }, 0);
  document.getElementById('brnOrdHead').innerHTML = `<div style="color:var(--text-primary)" class="text-sm">วันสั่ง <b>${esc(p.order_day)}</b> (${esc(p.order_weekday)}) · วันส่ง ${esc(p.delivery)} ·
      ใช้ได้ ${esc(p.cover_from)} ถึง ${esc(p.cover_to)} (${p.cover_days} วัน)</div>
    <div class="mt-1" style="color:var(--text-secondary)">${stockLine}</div>
    <div class="mt-1" style="color:var(--text-secondary)">${o ? `ใบสั่งซื้อ #${o.order_id} ${_bmcChip(o.status_th, o.status === 'received' ? 'var(--good-ink)' : (o.status === 'submitted' || o.status === 'processed') ? 'var(--navy-ink)' : 'var(--amber-ink)')}` : _bmcChip('ยังไม่มีใบสั่งซื้อรอบนี้', 'var(--text-muted)')}
      · มูลค่าที่สั่ง ≈ <b>฿${_bmcNum(total, 0)}</b>${p.calendar_filled ? '' : ' · <span style="color:var(--amber-ink)">ใช้วันสั่งค่าเริ่มต้น · Sales Support กำหนดวันสั่งได้ที่ BoM Center</span>'}</div>`;
  const q = (document.getElementById('brnOrdQ').value || '').trim().toLowerCase();
  const only = document.getElementById('brnOrdOnly').checked;
  const rows = p.rows.filter(r => (!only || r.order_units > 0 || Number(_brnOrdQty[r.code] || 0) > 0)
    && (!q || (r.code + ' ' + r.name).toLowerCase().includes(q)));
  const line = o ? Object.fromEntries(o.lines.map(l => [l.code, l])) : {};
  const reasonSel = (code) => {
    const cur = _brnOrdReason[code] || {};
    return `<div class="flex flex-wrap gap-2 mt-1 text-xs"><select onchange="_brnOrdReason[${_bmcQ(code)}]=Object.assign(_brnOrdReason[${_bmcQ(code)}]||{}, {reason_code:this.value})" ${editable ? '' : 'disabled'}
        style="border:1px solid var(--amber-ink);border-radius:var(--r-sm);padding:4px 6px;font-size:12px;background:var(--bg-input);color:var(--text-primary)"><option value="">— เหตุผลที่สั่งต่างจากยอดแนะนำ —</option>${p.reasons.map(x => `<option value="${x.code}"${cur.reason_code === x.code ? ' selected' : ''}>${esc(x.label)}</option>`).join('')}</select>
      <input value="${esc(cur.note || '')}" placeholder="หมายเหตุ" oninput="_brnOrdReason[${_bmcQ(code)}]=Object.assign(_brnOrdReason[${_bmcQ(code)}]||{}, {note:this.value})" ${editable ? '' : 'disabled'}
        style="flex:1 1 120px;border:1px solid var(--border-control);border-radius:var(--r-sm);padding:4px 6px;font-size:12px;background:var(--bg-input);color:var(--text-primary)"></div>`;
  };
  const html = rows.map(r => {
    const sugg = line[r.code] ? line[r.code].suggested_units : r.order_units;
    const v = _brnOrdQty[r.code] ?? '';
    const dev = _brnOrdNeedsReason(sugg, v);
    const detail = `พยากรณ์ ${_bmcNum(r.forecast, 0)} + สำรอง ${_bmcNum(r.safety, 0)}` +
      (r.on_hand != null ? ` − คงเหลือคาดการณ์ ${_bmcNum(Math.max(r.projected, 0), 0)}` : ' (ไม่มีผลตรวจนับ จึงไม่หัก)') +
      (r.on_order ? ` (รวมค้างส่ง ${_bmcNum(r.on_order, 0)})` : '') + ` = ${_bmcNum(r.need, 0)} ${esc(r.base_unit || '')}`;
    const recvCell = _brnRecvMode ? `<input inputmode="decimal" id="brnRcv_${esc(r.code)}" value="${esc(String(line[r.code] ? (line[r.code].processed_units ?? line[r.code].confirmed_units ?? '') : ''))}"
        style="width:68px;height:36px;text-align:center;font-size:16px;border:1px solid var(--good-ink);border-radius:10px;background:var(--bg-input);color:var(--text-primary)" title="จำนวนรับจริง">` : '';
    return `<div style="border-top:1px solid var(--border);padding:8px 0">
      <div style="display:flex;flex-wrap:wrap;gap:6px 8px;align-items:center">
        <div style="flex:1 1 220px;min-width:0"><div class="text-sm" style="color:var(--text-primary);font-weight:600">${esc(r.name)}
          ${r.monitored ? _bmcChip('บังคับ', 'var(--amber-ink)') : ''} ${(r.via || []).length ? _bmcChip('ใช้ใน ' + r.via.slice(0, 2).join(', '), 'var(--text-muted)') : ''}</div>
          <div class="text-[11px]" style="color:var(--text-muted)">${esc(r.code)} · ชั้น ${r.class} · ${detail}</div></div>
        <div style="display:flex;gap:6px;align-items:center;margin-left:auto">
          <span class="text-xs" style="color:var(--text-muted)">แนะนำ <b style="color:var(--text-primary)">${_bmcNum(sugg, 0)}</b></span>
          <input inputmode="decimal" value="${esc(v)}" ${editable ? '' : 'disabled'} oninput="_brnOrdQty[${_bmcQ(r.code)}]=this.value;brnRenderOrderDeferred()"
            style="width:68px;height:36px;text-align:center;font-size:16px;border:1px solid ${dev ? 'var(--amber-ink)' : 'var(--border-control)'};border-radius:10px;background:var(--bg-input);color:var(--text-primary)">
          ${recvCell}
          <span class="text-xs" style="width:64px;color:var(--text-secondary);overflow-wrap:anywhere">${esc(_brnUnitLabel(r))}</span></div></div>
      ${dev ? reasonSel(r.code) : ''}</div>`;
  }).join('') + extra.map(l => `<div style="border-top:1px solid var(--border);padding:8px 0" class="text-sm">${esc(l.name || l.code)} · สั่งเพิ่มนอกยอดแนะนำ ${_bmcNum(l.confirmed_units)} ${esc(l.order_unit === 'base' ? '' : 'แพ็ค')} ${l.reason_th ? '· ' + esc(l.reason_th) : ''}</div>`).join('');
  document.getElementById('brnOrdList').innerHTML = html || '<span class="text-xs" style="color:var(--good-ink)">✓ รอบนี้ไม่มีรายการที่ต้องสั่ง (คงเหลือเพียงพอ)</span>';
  const btn = (label, on, primary) => `<button onclick="${on}" style="border:${primary ? '0' : '1px solid var(--border)'};border-radius:var(--r-sm);padding:8px 14px;font-size:14px;cursor:pointer;background:${primary ? 'var(--accent)' : 'transparent'};color:${primary ? 'var(--on-accent)' : 'var(--text-primary)'}">${label}</button>`;
  let acts = '';
  if (editable) acts = btn('บันทึกร่าง', 'brnOrderSave(false)') + btn('ส่งให้ส่วนกลาง', 'brnOrderSave(true)', true) + (o ? btn('ยกเลิกใบสั่งซื้อ', 'brnOrderAct("cancel")') : '');
  else if (o.status === 'submitted') acts = btn('เปิดแก้ไข', 'brnOrderAct("reopen")') + (_brnRecvMode ? btn('บันทึกรับสินค้า', 'brnOrderReceive()', true) : btn('รับสินค้าที่มาถึง', 'brnStartReceive()', true));
  else if (o.status === 'processed') acts = _brnRecvMode ? btn('บันทึกรับสินค้า', 'brnOrderReceive()', true) : btn('รับสินค้าที่มาถึง', 'brnStartReceive()', true);
  document.getElementById('brnOrdActions').innerHTML = acts;
}
let _brnOrdTimer = null;
function brnRenderOrderDeferred() { clearTimeout(_brnOrdTimer); _brnOrdTimer = setTimeout(brnRenderOrder, 700); }

function _brnOrdLines() {
  return Object.entries(_brnOrdQty).map(([code, v]) => ({ code, confirmed_units: v === '' ? null : Number(v),
    reason_code: (_brnOrdReason[code] || {}).reason_code || null, note: (_brnOrdReason[code] || {}).note || null }))
    .filter(l => l.confirmed_units === null || !isNaN(l.confirmed_units));
}
async function brnOrderSave(submit) {
  if (submit && !confirm('ยืนยันส่งใบสั่งซื้อให้ส่วนกลาง (Sales Support)?\nแก้ไขได้จนกว่าส่วนกลางจะเริ่มดำเนินการ')) return;
  try {
    const r = await _bmcJson(`${API}/api/bom-center/order/save${_brnQS() ? '?' + _brnQS() : ''}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ round: _brnRound, order_day: _brnPlan.order_day, lines: _brnOrdLines(), submit }) });
    await brnLoadOrder();
    _bmcStatus('brnOrdStatus', submit ? `✓ ส่งใบสั่งซื้อ #${r.order_id} แล้ว · ฿${_bmcNum(r.value, 0)}` :
      `✓ บันทึกร่าง${r.needs_reason.length ? ` · ต้องระบุเหตุผล ${r.needs_reason.length} รายการก่อนส่ง` : ''}`, r.needs_reason.length && !submit ? 'var(--amber-ink)' : 'var(--good-ink)');
  } catch (e) { _bmcStatus('brnOrdStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 220), 'var(--bad-ink)'); }
}
async function brnOrderAct(what) {
  const o = _brnPlan && _brnPlan.order;
  if (!o) return;
  if (what === 'cancel' && !confirm('ยืนยันยกเลิกใบสั่งซื้อนี้?')) return;
  try {
    await _bmcJson(`${API}/api/bom-center/order/${o.order_id}/${what}${_brnQS() ? '?' + _brnQS() : ''}`, { method: 'POST' });
    await brnLoadOrder();
  } catch (e) { _bmcStatus('brnOrdStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
function brnStartReceive() { _brnRecvMode = true; document.getElementById('brnOrdOnly').checked = true; brnRenderOrder(); _bmcStatus('brnOrdStatus', 'กรุณากรอกจำนวนที่รับจริงในช่องขอบสีเขียว แล้วกด “บันทึกรับสินค้า”', 'var(--text-muted)'); }
async function brnOrderReceive() {
  const o = _brnPlan.order;
  const lines = o.lines.map(l => { const el = document.getElementById('brnRcv_' + l.code);
    return { code: l.code, received_units: el ? Number(el.value || 0) : Number(l.processed_units ?? l.confirmed_units ?? 0) }; });
  if (!confirm('ยืนยันบันทึกรับสินค้าตามจำนวนที่กรอก?\nระบบจะบันทึกเป็นรับเข้าสำหรับคำนวณผลต่างโดยอัตโนมัติ')) return;
  try {
    await _bmcJson(`${API}/api/bom-center/order/${o.order_id}/receive${_brnQS() ? '?' + _brnQS() : ''}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lines }) });
    await brnLoadOrder();
    _bmcStatus('brnOrdStatus', '✓ บันทึกรับสินค้าเรียบร้อย', 'var(--good-ink)');
  } catch (e) { _bmcStatus('brnOrdStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
async function brnLoadOrderHistory() {
  try {
    const d = await _bmcJson(`${API}/api/bom-center/orders${_brnQS() ? '?' + _brnQS() : ''}`);
    document.getElementById('brnOrdHistory').innerHTML = d.orders.length ? d.orders.map(x => `<div style="border-top:1px solid var(--border);padding:4px 0">
        #${x.order_id} · ${esc(x.cycle_date)} · ${esc((_BRN_ROUNDS.find(r => r[0] === x.round) || [0, x.round])[1])} · ${esc(x.status_th)} · ฿${_bmcNum(x.value, 0)}</div>`).join('') : 'ยังไม่มีข้อมูล';
  } catch (e) { document.getElementById('brnOrdHistory').textContent = String(e.message || e); }
}
