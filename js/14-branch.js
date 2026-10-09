// 14-branch.js — สาขาของฉัน (BoM Center Phase 2: ตัดเบิก (ใบตัดเบิกประจำวัน, PR-J2) · สั่งสินค้า (on the system stock) · S7 รับของ + ผลต่างของฉัน · ตรวจนับสิ้นเดือน).
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
// the server's write rules (auth.py): counts, manual receipts and variance = the branch or bom.center;
// an order may also be worked by Sales Support. Other roles see the pages read-only.
function _brnCanCount() { return hasModule('branch.stock') || hasModule('bom.center'); }
function _brnCanOrder() { return hasModule('branch.stock') || hasModule('bom.center') || hasModule('supply.ordering'); }
function _brnCanVariance() { return hasModule('branch.stock') || hasModule('bom.center') || hasModule('supply.ordering'); }   // close step 5 (owner 2026-10-07)
const _BRN_READONLY = 'บทบาทของคุณดูหน้านี้ได้ แต่บันทึกแทนสาขาไม่ได้';

async function loadBranchStock() {
  try {
    _brnMe = await _bmcJson(`${API}/api/bom-center/stock/me`);
    const sel = document.getElementById('brnBranch');
    const nm = document.getElementById('brnBranchName');
    if (_brnMe.scoped) {
      sel.style.display = 'none';
      nm.textContent = _brnMe.branch || '';
    } else {
      const keep = sel.value || String(supplyCtx().branch_id || '');   // the module's shared branch
      sel.innerHTML = '<option value="">— เลือกสาขา —</option>' +
        _brnMe.branches.map(b => `<option value="${b.branch_id}">${esc(b.name)}</option>`).join('');
      if (keep && _brnMe.branches.some(b => String(b.branch_id) === keep)) sel.value = keep;
      sel.style.display = '';
      nm.textContent = '';
    }
    brnSetSub(_brnSub, true);       // show the remembered pane; brnBranchChanged loads it
    await brnBranchChanged();
  } catch (e) {
    document.getElementById('brnList').innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`;
  }
}

async function brnLoadSheet() {
  document.querySelectorAll('#tab-branchstock .brn-write').forEach(el => { el.style.display = _brnCanCount() ? '' : 'none'; });
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
    document.getElementById('brnKind').innerHTML = _bmcChip(s.next_kind === 'begin' ? 'รอบนี้: ตรวจนับสิ้นเดือน ต้นงวด (ครั้งแรก)' : 'รอบนี้: ตรวจนับสิ้นเดือน ปลายงวด (ใช้เป็นต้นงวดของเดือนถัดไป)',
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
  if (!_brnCanCount()) { if (!quiet) _bmcStatus('brnStatus', _BRN_READONLY, 'var(--amber-ink)'); return; }
  if (!_brnSheet) return;
  clearTimeout(_brnTimer);
  if (submit) {
    const req = _brnSheet.zones.flatMap(z => z.items).filter(i => i.required);
    const missing = req.filter(i => _brnQty[i.code] === undefined || _brnQty[i.code] === '');
    const msg = (missing.length ? `ยังไม่ได้ตรวจนับรายการบังคับ ${missing.length} รายการ (เช่น ${missing.slice(0, 3).map(i => i.name).join(', ')})\n` : '')
      + `ยืนยันส่งผลตรวจนับสิ้นเดือนเป็น${_brnSheet.next_kind === 'begin' ? 'ต้นงวด' : 'ปลายงวด'}?\nเมื่อส่งแล้วจะแก้ไขไม่ได้ หากต้องแก้ไข กรุณาตรวจนับใหม่เป็นครั้งใหม่`;
    if (!await uiConfirm(msg)) return;
  } else if (!_brnDirty && quiet) return;
  try {
    const r = await _bmcJson(`${API}/api/bom-center/stock/count?${_brnQS()}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lines: _brnLines(), submit }) });
    _brnDirty = false;
    if (submit) {
      _bmcStatus('brnStatus', `✓ ส่งผลตรวจนับแล้ว ${r.lines} รายการ`, 'var(--good-ink)');
      // an end count closes a period: confirm the receipts right here, no pane to find
      const nx = document.getElementById('brnNext');
      if (nx) nx.innerHTML = r.kind === 'end' ? `<div class="card p-3 mb-3" style="border-left:4px solid var(--accent)">
          <div class="text-sm" style="color:var(--text-primary)">ขั้นตอนถัดไป: ยืนยันว่ารับสินค้าของรอบนี้บันทึกครบแล้ว แล้วระบุเหตุผลของรายการที่เกินเกณฑ์</div>
          <div class="flex flex-wrap gap-2 mt-2"><button onclick="brnConfirmRecvFor(${r.count_id})" style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:8px 14px;font-size:14px;cursor:pointer">ยืนยันรับสินค้าครบ</button>
            <button onclick="brnSetSub('var')" style="border:1px solid var(--border);border-radius:var(--r-sm);padding:8px 14px;font-size:14px;cursor:pointer;background:transparent;color:var(--text-primary)">ดูผลต่างก่อน →</button></div></div>` : '';
      await brnLoadSheet();
    } else {
      _bmcStatus('brnStatus', `✓ บันทึกร่าง ${r.lines} รายการ ${new Date().toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' })}`
        + (r.unconverted && r.unconverted.length ? ` · แปลงหน่วยไม่สำเร็จ ${r.unconverted.length} รายการ` : ''), r.unconverted && r.unconverted.length ? 'var(--amber-ink)' : 'var(--text-muted)');
    }
  } catch (e) { _bmcStatus('brnStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}

async function brnDiscard() {
  if (!await uiConfirm('ยืนยันล้างร่างผลตรวจนับทั้งหมด?')) return;
  try {
    await _bmcJson(`${API}/api/bom-center/stock/draft?${_brnQS()}`, { method: 'DELETE' });
    await brnLoadSheet();
    _bmcStatus('brnStatus', 'ล้างร่างแล้ว', 'var(--text-muted)');
  } catch (e) { _bmcStatus('brnStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}

async function brnLoadHistory() {
  const box = document.getElementById('brnHistory');
  try {
    const d = await _bmcJson(`${API}/api/bom-center/stock/counts?kind=month&${_brnQS()}`);
    const KIND = { begin: 'ต้นงวด', end: 'ปลายงวด', spot: 'นับก่อนสั่ง (ระบบเดิม)' };
    box.innerHTML = d.counts.length ? d.counts.map(c => `<div style="border-top:1px solid var(--border);padding:4px 0">
        ${esc(c.count_date)} · ${c.status === 'draft' ? _bmcChip('ร่าง', 'var(--amber-ink)') : (KIND[c.kind] || esc(c.kind))} · ${_bmcNum(c.lines, 0)} รายการ · ${esc(c.counted_by || '')}</div>`).join('')
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

// ── ตัดเบิก (ใบตัดเบิกประจำวัน, management 2026-10-09) ─────────────────────────────────────
// Each day the branch records, per item, what the kitchen USED (เบิกใช้) and what was thrown away (ของเสีย,
// with a reason), then confirms the slip once. A confirmed slip is locked: Sales Support reopens it with a
// reason. The system stock after the slips is the basis of the order suggestion (สั่งสินค้า).
let _brnIss = null;           // /api/bom-center/issues/sheet — the day on screen
let _brnIssStrip = [];        // /api/bom-center/issues/status — one status per day, the last 7
let _brnIssDate = null;       // ISO day on screen; null = today (the server picks)
let _brnIssDateKeep = false;  // a deep link chose the day: the next branch load must not reset it
let _brnIssZone = null;
let _brnIssQty = {};          // code -> {used, waste, reason, note} (strings) — the working copy
let _brnIssDirty = false;
let _brnIssTimer = null;
const _BRN_ISS_WD = ['อา.', 'จ.', 'อ.', 'พ.', 'พฤ.', 'ศ.', 'ส.'];
function _brnIssDm(iso) { return `${Number(String(iso).slice(8, 10))}/${Number(String(iso).slice(5, 7))}`; }
function _brnIssWd(iso) { return _BRN_ISS_WD[new Date(String(iso) + 'T12:00:00Z').getUTCDay()]; }
function _brnIssTime(iso) {      // a stored UTC stamp as the branch's clock (Bangkok) HH:MM
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d) ? '' : d.toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Bangkok' });
}
function _brnIssCanReopen() { return hasModule('supply.ordering') || hasModule('bom.center'); }
function _brnIssEditable() { return !!(_brnIss && _brnIss.editable) && _brnCanOrder(); }
const _BRN_ISS_ST = { confirmed: ['ยืนยันแล้ว', 'var(--good-ink)'], late: ['ล่าช้า', 'var(--amber-ink)'],
  draft: ['ร่าง', 'var(--amber-ink)'], missing: ['ยังไม่ทำ', 'var(--bad-ink)'] };
const _BRN_ISS_ST_LONG = { confirmed: 'ยืนยันแล้ว', late: 'ยืนยันล่าช้า', draft: 'ร่าง', missing: 'ยังไม่บันทึก' };

async function brnIssLoad(opts) {
  opts = opts || {};
  document.querySelectorAll('#brn-pane-issue .brn-write').forEach(el => { el.style.display = _brnCanOrder() ? '' : 'none'; });
  if (_brnNeedBranch('brnIssList')) {
    ['brnIssDays', 'brnIssHead', 'brnIssZones', 'brnIssActions', 'brnIssHistory', 'brnIssProgress'].forEach(id => { const el = document.getElementById(id); if (el) el.innerHTML = ''; });
    return;
  }
  if (!opts.quiet) document.getElementById('brnIssList').innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลดรายการ…</span>';
  try {
    const qs = _brnQS();
    const [st, sh] = await Promise.all([
      _bmcJson(`${API}/api/bom-center/issues/status${qs ? '?' + qs : ''}`),
      _bmcJson(`${API}/api/bom-center/issues/sheet?${[qs, _brnIssDate ? 'date=' + _brnIssDate : ''].filter(Boolean).join('&')}`)]);
    _brnIss = sh;
    _brnIssDate = sh.date;
    _brnIssStrip = st.days.slice();
    if (!_brnIssStrip.some(d => d.date === sh.date)) _brnIssStrip.unshift(Object.assign({}, sh.status));   // an older day opened from the history
    _brnIssQty = {};
    sh.zones.forEach(z => z.items.forEach(it => {
      if ((it.used || 0) > 0 || (it.waste || 0) > 0) {
        _brnIssQty[it.code] = { used: it.used > 0 ? String(it.used) : '', waste: it.waste > 0 ? String(it.waste) : '',
          reason: it.waste_reason || '', note: it.line_note || '' };
      }
    }));
    _brnIssDirty = false;
    if (!_brnIssZone || !sh.zones.some(z => z.zone === _brnIssZone)) _brnIssZone = (sh.zones[0] || {}).zone || null;
    brnIssRender();
    brnIssHistory();
  } catch (e) { document.getElementById('brnIssList').innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}

// the last 7 days as chips: the dot says what the slip of that day is
function _brnIssDayChips() {
  const box = document.getElementById('brnIssDays');
  if (!box) return;
  const today = _brnIss ? _brnIss.today : '';
  box.innerHTML = _brnIssStrip.map(d => {
    const on = d.date === _brnIssDate;
    const [t, ink] = _BRN_ISS_ST[d.status] || _BRN_ISS_ST.missing;
    return `<button onclick="brnIssSetDate('${d.date}')" style="border:1px solid ${on ? 'var(--accent)' : 'var(--border)'};border-radius:12px;padding:6px 12px;font-size:13px;cursor:pointer;text-align:center;min-width:64px;background:${on ? 'var(--accent)' : 'transparent'};color:${on ? 'var(--on-accent)' : 'var(--text-primary)'}">
      <span style="display:block">${esc(d.date === today ? 'วันนี้' : _brnIssWd(d.date))} ${esc(_brnIssDm(d.date))}</span>
      <span style="display:block;font-size:11px;color:${on ? 'inherit' : ink}">● ${esc(t)}</span></button>`;
  }).join('');
}
async function brnIssSetDate(d) {
  if (d === _brnIssDate) return;
  clearTimeout(_brnIssTimer);
  if (_brnIssDirty && _brnIssEditable()) { try { await brnIssSave(false, true); } catch (e) { /* the draft is re-saved on the next edit */ } }
  _brnIssDate = d;
  brnIssLoad();
  window.scrollTo({ top: 0 });
}
function brnIssSetZone(z) { _brnIssZone = z; brnIssRender(); window.scrollTo({ top: 0 }); }

function _brnIssAny(code) { const q = _brnIssQty[code]; return !!q && (Number(q.used) > 0 || Number(q.waste) > 0); }
function _brnIssReasonBox(code) {
  const q = _brnIssQty[code] || {};
  if (!(Number(q.waste) > 0)) return '';
  const ed = _brnIssEditable();
  const inp = 'border:1px solid var(--border-control);border-radius:var(--r-sm);padding:4px 6px;font-size:12px;background:var(--bg-input);color:var(--text-primary)';
  const bad = !q.reason || (q.reason === 'other' && !String(q.note || '').trim());
  return `<div class="flex flex-wrap gap-2 mt-1 text-xs"><select id="brnIssR_${esc(code)}" onchange="brnIssSetField(${_bmcQ(code)}, 'reason', this.value)" ${ed ? '' : 'disabled'}
      style="${inp};border-color:${bad ? 'var(--amber-ink)' : 'var(--border-control)'}"><option value="">— สาเหตุของของเสีย —</option>${((_brnIss && _brnIss.reasons) || []).map(r => `<option value="${esc(r.code)}"${q.reason === r.code ? ' selected' : ''}>${esc(r.label)}</option>`).join('')}</select>
    <input id="brnIssN_${esc(code)}" value="${esc(q.note || '')}" placeholder="${q.reason === 'other' ? 'ระบุคำอธิบาย (จำเป็น)' : 'หมายเหตุ'}" oninput="brnIssSetField(${_bmcQ(code)}, 'note', this.value)" ${ed ? '' : 'disabled'}
      style="flex:1 1 140px;${inp}"></div>`;
}
function _brnIssZoneChips() {
  const s = _brnIss;
  if (!s) return;
  document.getElementById('brnIssZones').innerHTML = s.zones.map(z => {
    const done = z.items.filter(i => _brnIssAny(i.code)).length;
    const on = z.zone === _brnIssZone;
    return `<button onclick="brnIssSetZone('${z.zone}')" style="border:1px solid ${on ? 'var(--accent)' : 'var(--border)'};border-radius:999px;padding:6px 12px;font-size:13px;cursor:pointer;background:${on ? 'var(--accent)' : 'transparent'};color:${on ? 'var(--on-accent)' : 'var(--text-primary)'}">${esc(z.label)} ${done}/${z.items.length}</button>`;
  }).join('');
}
function _brnIssProgress() {
  const s = _brnIss;
  if (!s) return;
  const all = s.zones.flatMap(z => z.items);
  const nUsed = all.filter(i => Number((_brnIssQty[i.code] || {}).used) > 0).length;
  const nWaste = all.filter(i => Number((_brnIssQty[i.code] || {}).waste) > 0).length;
  document.getElementById('brnIssProgress').innerHTML =
    `กรอกแล้ว <b style="color:var(--text-primary)">${all.filter(i => _brnIssAny(i.code)).length}/${all.length}</b> รายการ · เบิกใช้ <b style="color:var(--text-primary)">${nUsed}</b> · ของเสีย <b style="color:${nWaste ? 'var(--amber-ink)' : 'var(--text-primary)'}">${nWaste}</b>`;
}

function brnIssRender() {
  const s = _brnIss;
  if (!s) return;
  _brnIssDayChips();
  const st = s.status || {};
  const [stT, stInk] = _BRN_ISS_ST[st.status] || _BRN_ISS_ST.missing;
  const ed = _brnIssEditable();
  const confirmed = st.status === 'confirmed' || st.status === 'late';
  document.getElementById('brnIssHead').innerHTML =
    `<span style="color:var(--text-primary)">ใบตัดเบิกวันที่ <b>${esc(_brnIssDm(s.date))}</b></span> ${_bmcChip(_BRN_ISS_ST_LONG[st.status] || stT, stInk)}` +
    (confirmed ? ` <span class="text-xs" style="color:var(--text-secondary)">ยืนยันแล้ว เวลา ${esc(_brnIssTime(st.confirmed_at))} โดย ${esc(st.confirmed_by || '-')}</span>` : '') +
    (s.slip && s.slip.reopen_note && !confirmed ? ` <span class="text-xs" style="color:var(--amber-ink)">เปิดแก้ไขโดย ${esc(s.slip.reopened_by || '-')}: ${esc(s.slip.reopen_note)}</span>` : '');
  const parts = [];
  if (s.backdated && !confirmed) parts.push(['var(--amber-ink)', 'ใบตัดเบิกย้อนหลัง จะถูกบันทึกเป็นล่าช้า']);
  if (confirmed) parts.push(['var(--text-secondary)', 'ใบตัดเบิกนี้ยืนยันแล้ว แก้ไขไม่ได้ หากต้องแก้ไข กรุณาแจ้งประสานงานขาย']);
  const md = s.missing_days || [];
  if (md.length) parts.push(['var(--amber-ink)', `ใบตัดเบิกขาด ${md.length} วัน (${md.slice(0, 6).map(_brnIssDm).join(', ')}${md.length > 6 ? ` และอีก ${md.length - 6} วัน` : ''}) ยอดคงเหลือตามระบบอาจสูงกว่าจริง`]);
  const banner = document.getElementById('brnIssBanner');
  banner.style.display = parts.length ? '' : 'none';
  banner.innerHTML = parts.map(([ink, t]) => `<div class="text-xs mb-1" style="border:1px solid ${ink};border-radius:var(--r-sm);padding:6px 10px;color:${ink}">${esc(t)}</div>`).join('');
  _brnIssZoneChips();
  _brnIssProgress();
  const all = s.zones.flatMap(z => z.items);
  const zone = s.zones.find(z => z.zone === _brnIssZone) || { items: [] };
  const q = (document.getElementById('brnIssQ').value || '').trim().toLowerCase();
  const usedOnly = document.getElementById('brnIssUsedOnly').checked;
  const items = (q ? all : zone.items).filter(i => (!usedOnly || _brnIssAny(i.code)) && (!q || (i.code + ' ' + i.name).toLowerCase().includes(q)));
  const inp = (id, v, ink) => `<input id="${id}" inputmode="decimal" value="${esc(v)}" ${ed ? '' : 'disabled'}
      style="width:64px;height:36px;text-align:center;font-size:16px;border:1px solid ${v !== '' ? ink : 'var(--border-control)'};border-radius:10px;background:var(--bg-input);color:var(--text-primary)"`;
  document.getElementById('brnIssList').innerHTML = items.length ? items.map(it => {
    const v = _brnIssQty[it.code] || { used: '', waste: '' };
    const unit = it.count_unit || it.base_unit || '';
    const sys = it.system_qty == null ? '<span style="color:var(--text-muted)">ยังไม่มีผลตรวจนับสิ้นเดือน</span>'
      : `คงเหลือตามระบบ <b style="color:${it.system_qty < 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">${_bmcNum(it.system_qty, 2)}</b> ${esc(unit)}`;
    const avg = it.avg_used_7d != null ? ` · เฉลี่ย 7 วัน ${_bmcNum(it.avg_used_7d, 2)} ${esc(unit)}/วัน` : '';
    return `<div style="border-top:1px solid var(--border);padding:8px 0">
      <div style="display:flex;flex-wrap:wrap;gap:6px 8px;align-items:center">
        <div style="flex:1 1 220px;min-width:0">
          <div class="text-sm" style="color:var(--text-primary);font-weight:600;overflow-wrap:anywhere">${esc(it.name)}${it.required ? ' ' + _bmcChip('บังคับ', 'var(--amber-ink)') : ''}</div>
          <div class="text-[11px]" style="color:var(--text-muted)">${esc(it.code)} · ${sys}${avg}</div></div>
        <div style="display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;margin-left:auto">
          <label class="text-[11px]" style="display:flex;gap:4px;align-items:center;color:var(--text-muted)">เบิกใช้
            ${inp('brnIssU_' + esc(it.code), v.used, 'var(--accent)')} oninput="brnIssSetField(${_bmcQ(it.code)}, 'used', this.value)" title="จำนวนที่ครัวเบิกไปใช้ในวันนี้">
            <span class="text-xs" style="max-width:64px;color:var(--text-secondary);overflow-wrap:anywhere">${esc(unit)}</span></label>
          <label class="text-[11px]" style="display:flex;gap:4px;align-items:center;color:var(--text-muted)">ของเสีย
            ${inp('brnIssW_' + esc(it.code), v.waste, 'var(--amber-ink)')} oninput="brnIssSetField(${_bmcQ(it.code)}, 'waste', this.value)" title="จำนวนที่ทิ้ง เน่าเสีย หมดอายุ หรือเสียหาย">
            <span class="text-xs" style="max-width:64px;color:var(--text-secondary);overflow-wrap:anywhere">${esc(unit)}</span></label></div></div>
      <div id="brnIssX_${esc(it.code)}">${_brnIssReasonBox(it.code)}</div></div>`;
  }).join('') : `<span class="text-xs" style="color:var(--text-muted)">${usedOnly ? 'ยังไม่มีรายการที่กรอก' : 'ไม่มีรายการในโซนนี้'}</span>`;
  brnIssActions();
}

function _brnIssItem(code) { return _brnIss ? _brnIss.zones.flatMap(z => z.items).find(i => i.code === code) : null; }
// a box was typed in: update in place (the focus stays), open or close the waste-reason block, autosave the draft
function brnIssSetField(code, field, v) {
  if (!_brnIssEditable()) return;
  const q = _brnIssQty[code] || (_brnIssQty[code] = { used: '', waste: '', reason: '', note: '' });
  q[field] = String(v || '').replace(',', '.').trim();
  if (field === 'note') q.note = String(v || '');
  if (field === 'waste') {
    const x = document.getElementById('brnIssX_' + code);
    if (x && (Number(q.waste) > 0) !== !!x.firstElementChild) x.innerHTML = _brnIssReasonBox(code);
  }
  if (field === 'reason') {
    const x = document.getElementById('brnIssX_' + code);
    if (x) x.innerHTML = _brnIssReasonBox(code);
  }
  _brnIssZoneChips();
  _brnIssProgress();
  _brnIssDirty = true;
  clearTimeout(_brnIssTimer);
  _brnIssTimer = setTimeout(() => brnIssSave(false, true), 2500);       // autosave the draft — a lost signal loses nothing
}

// the lines to send. A line whose waste has no reason yet cannot be stored (the server refuses it): it is
// listed in `incomplete`, and the autosave leaves it out until the reason is chosen
function _brnIssLines() {
  const lines = [], incomplete = [];
  Object.entries(_brnIssQty).forEach(([code, q]) => {
    const it = _brnIssItem(code);
    if (!it) return;
    const used = q.used === '' ? 0 : Number(q.used), waste = q.waste === '' ? 0 : Number(q.waste);
    if (isNaN(used) || isNaN(waste) || used < 0 || waste < 0) { incomplete.push(code); return; }
    if (waste > 0 && (!q.reason || (q.reason === 'other' && !String(q.note || '').trim()))) { incomplete.push(code); return; }
    lines.push({ code, qty_used: used, qty_waste: waste, unit: it.count_unit || it.base_unit || null,
      waste_reason: waste > 0 ? q.reason : null, note: String(q.note || '').trim() || null });
  });
  return { lines, incomplete };
}

async function brnIssSave(submit, quiet) {
  if (!_brnCanOrder()) { if (!quiet) _bmcStatus('brnIssStatus', _BRN_READONLY, 'var(--amber-ink)'); return; }
  if (!_brnIss || !_brnIss.editable) return;
  clearTimeout(_brnIssTimer);
  if (quiet && !_brnIssDirty) return;
  const { lines, incomplete } = _brnIssLines();
  if (incomplete.length && !quiet) {
    const it = _brnIssItem(incomplete[0]), q = _brnIssQty[incomplete[0]] || {};
    _bmcStatus('brnIssStatus', Number(q.waste) > 0 && q.reason === 'other' ? `กรุณาระบุคำอธิบายของสาเหตุ “อื่น ๆ”: ${it.name}`
      : Number(q.waste) > 0 ? `กรุณาเลือกสาเหตุของของเสีย: ${it.name}` : `จำนวนไม่ถูกต้อง: ${it.name}`, 'var(--amber-ink)');
    const el = document.getElementById('brnIssR_' + incomplete[0]) || document.getElementById('brnIssU_' + incomplete[0]);
    if (el) { el.scrollIntoView({ block: 'center' }); el.focus(); }
    return;
  }
  const nAny = lines.filter(l => l.qty_used > 0 || l.qty_waste > 0);
  if (submit) {
    const nUsed = nAny.filter(l => l.qty_used > 0).length, nWaste = nAny.filter(l => l.qty_waste > 0).length;
    const late = _brnIss.backdated && !(_brnIss.status && _brnIss.status.confirm_count > 0);
    if (!await uiConfirm((late ? 'ใบตัดเบิกย้อนหลัง จะถูกบันทึกเป็นล่าช้า\n' : '')
        + `ยืนยันใบตัดเบิกวันที่ ${_brnIssDm(_brnIss.date)} · ${nAny.length} รายการ · เบิกใช้ ${nUsed} รายการ · ของเสีย ${nWaste} รายการ?\nเมื่อยืนยันแล้วจะแก้ไขไม่ได้ หากต้องแก้ไข กรุณาแจ้งประสานงานขาย`)) return;
  }
  try {
    const qs = _brnQS();
    const r = await _bmcJson(`${API}/api/bom-center/issues/save${qs ? '?' + qs : ''}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date: _brnIss.date, lines, submit: !!submit }) });
    if (quiet) {                                      // the autosave: the page stays where the person is
      _brnIssDirty = incomplete.length > 0;
      const d = _brnIssStrip.find(x => x.date === _brnIss.date);
      if (d && d.status === 'missing') { d.status = 'draft'; _brnIssDayChips(); }
      _bmcStatus('brnIssStatus', `✓ บันทึกร่าง ${r.lines} รายการ ${new Date().toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' })}`
        + (incomplete.length ? ` · รอเลือกสาเหตุของของเสีย ${incomplete.length} รายการ` : ''), incomplete.length ? 'var(--amber-ink)' : 'var(--text-muted)');
      return;
    }
    _brnIssDirty = false;
    _brnPlan = null;                                  // the order suggestion follows the slip: it is calculated again when that page opens
    await brnIssLoad({ quiet: true });
    _bmcStatus('brnIssStatus', submit ? `✓ ยืนยันใบตัดเบิกแล้ว ${r.lines} รายการ`
      : `✓ บันทึกร่าง ${r.lines} รายการ ${new Date().toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' })}`
        + (r.unconverted && r.unconverted.length ? ` · แปลงหน่วยไม่สำเร็จ ${r.unconverted.length} รายการ` : ''),
      submit ? 'var(--good-ink)' : (r.unconverted && r.unconverted.length ? 'var(--amber-ink)' : 'var(--text-muted)'));
  } catch (e) { _bmcStatus('brnIssStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 220), 'var(--bad-ink)'); }
}

function brnIssActions() {
  const box = document.getElementById('brnIssActions');
  if (!box || !_brnIss) return;
  const btn = (label, on, primary) => `<button onclick="${on}" style="border:${primary ? '0' : '1px solid var(--border)'};border-radius:var(--r-sm);padding:8px 14px;font-size:14px;cursor:pointer;background:${primary ? 'var(--accent)' : 'transparent'};color:${primary ? 'var(--on-accent)' : 'var(--text-primary)'}">${label}</button>`;
  const st = _brnIss.status || {};
  const confirmed = st.status === 'confirmed' || st.status === 'late';
  if (!_brnCanOrder()) box.innerHTML = `<span class="text-xs" style="color:var(--text-muted)">${esc(_BRN_READONLY)}</span>`;
  else if (confirmed) box.innerHTML = _bmcChip(_BRN_ISS_ST_LONG[st.status], st.status === 'late' ? 'var(--amber-ink)' : 'var(--good-ink)')
    + `<span class="text-xs" style="color:var(--text-secondary)">หากต้องแก้ไข กรุณาแจ้งประสานงานขายให้เปิดแก้ไข</span>`
    + (_brnIssCanReopen() ? btn('เปิดแก้ไข (ประสานงานขาย)', 'brnIssReopen()') : '');
  else box.innerHTML = btn('บันทึกร่าง', 'brnIssSave(false)') + btn('ยืนยันใบตัดเบิก', 'brnIssSave(true)', true);
}

// Sales Support (or the analyst) opens a confirmed slip for the branch to edit again; the reason is logged
async function brnIssReopen() {
  const st = _brnIss && _brnIss.status;
  if (!st || !st.slip_id) return;
  const note = await uiPrompt(`เปิดใบตัดเบิกวันที่ ${_brnIssDm(_brnIss.date)} ให้สาขาแก้ไข\nใบจะกลับเป็นฉบับร่าง และสาขาต้องยืนยันใหม่ กรุณาระบุเหตุผล (จำเป็น)`, '',
    { title: 'เปิดแก้ไขใบตัดเบิก', okText: 'เปิดแก้ไข', required: true, placeholder: 'เหตุผลในการเปิดแก้ไข' });
  if (note == null) return;
  try {
    await _bmcJson(`${API}/api/bom-center/issues/reopen`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slip_id: st.slip_id, note }) });
    _brnPlan = null;
    await brnIssLoad({ quiet: true });
    _bmcStatus('brnIssStatus', '✓ เปิดแก้ไขใบตัดเบิกแล้ว สาขาแก้ไขและยืนยันใหม่ได้', 'var(--good-ink)');
  } catch (e) { _bmcStatus('brnIssStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}

// the last 30 days of slips of the branch; a row opens that day
async function brnIssHistory() {
  const box = document.getElementById('brnIssHistory');
  if (!box) return;
  try {
    const qs = _brnQS();
    const to = _brnIss ? _brnIss.today : null;
    const from = to ? new Date(new Date(to + 'T12:00:00Z').getTime() - 30 * 86400000).toISOString().slice(0, 10) : null;
    const d = await _bmcJson(`${API}/api/bom-center/issues?${[qs, from ? 'date_from=' + from : '', to ? 'date_to=' + to : ''].filter(Boolean).join('&')}`);
    box.innerHTML = d.slips.length ? d.slips.map(x => {
      const reopens = x.status === 'draft' ? x.confirm_count : Math.max(x.confirm_count - 1, 0);
      const ink = x.status === 'confirmed' ? 'var(--good-ink)' : 'var(--amber-ink)';
      return `<div style="border-top:1px solid var(--border);padding:4px 0"><a href="#" onclick="brnIssSetDate('${x.date}');return false" style="color:var(--accent);font-weight:600">${esc(_brnIssDm(x.date))}</a>
        · ${_bmcChip(x.status_th, ink)} · ${_bmcNum(x.lines, 0)} รายการ · ของเสีย ${_bmcNum(x.waste_lines, 0)}
        ${x.confirmed_by && x.status !== 'draft' ? ' · โดย ' + esc(x.confirmed_by) : ''}${x.confirmed_at && x.status !== 'draft' ? ' · เวลา ' + esc(_brnIssTime(x.confirmed_at)) : ''}
        ${x.late ? ' ' + _bmcChip('ยืนยันล่าช้า', 'var(--amber-ink)') : ''}${reopens ? ' ' + _bmcChip(`เปิดแก้ไข ${reopens} ครั้ง`, 'var(--text-muted)') : ''}</div>`;
    }).join('') : 'ยังไม่มีข้อมูล';
  } catch (e) { box.textContent = String(e.message || e); }
}
// the link on the order page: the slip of today
function brnIssGoToday() { _brnIssDate = null; brnSetSub('issue'); }

// ── sub-tabs: ตัดเบิก · สั่งสินค้า · รับสินค้า · ผลต่างการใช้ · ตรวจนับสิ้นเดือน ─────────────────
let _brnSub = 'issue';
let _brnRecv = [];            // lines waiting to be saved: {code, name, qty, unit}
let _brnVar = null;           // /api/bom-center/variance/period
// The วัตถุดิบและใบสั่งซื้อ bar (01-core.js) reads, sets and shows this tab's panes through here.
_SUPPLY_PANE.branchstock = { get: () => _brnSub, set: k => { _brnSub = k; }, show: k => brnSetSub(k) };

function brnSetSub(k, quiet) {   // quiet: only show the pane (the caller loads it)
  _brnSub = k;
  ['issue', 'count', 'order', 'recv', 'var'].forEach(p => { const el = document.getElementById('brn-pane-' + p); if (el) el.style.display = p === k ? '' : 'none'; });
  const kind = document.getElementById('brnKind');          // begin / end is the month-end count's business only
  if (kind) kind.style.display = k === 'count' ? '' : 'none';
  if (!quiet) {
    if (k === 'issue') brnIssLoad();
    if (k === 'order') brnLoadOrder();
    if (k === 'recv') brnLoadRecv();
    if (k === 'var') brnLoadVarPane();
  }
  try { _updateClusterBar(activeTab); } catch (e) { /* no bar on this page */ }
}
async function brnBranchChanged() {
  supplyCtxSet({ branch_id: _brnBid() || supplyCtx().branch_id || null });
  _brnVar = null;
  _brnRecv = [];
  _brnPlan = null;
  if (_brnRoundKeep) _brnRoundKeep = false; else _brnRound = null;
  if (_brnDeliveryKeep) _brnDeliveryKeep = false; else _brnDelivery = null;
  clearTimeout(_brnIssTimer);
  _brnIss = null; _brnIssQty = {}; _brnIssDirty = false;
  if (_brnIssDateKeep) _brnIssDateKeep = false; else _brnIssDate = null;
  await brnLoadSheet();
  if (_brnSub === 'issue') brnIssLoad();
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
// one received-goods note as a card: what it is and the delete button up front, its lines folded away
// (a month's central delivery has ~100 lines; listing them inline buried the button)
const _BRN_RECV_SRC = { central: ['รายงานส่วนกลาง', 'var(--navy-ink)'], order: ['ตามใบสั่งซื้อ', 'var(--good-ink)'],
  branch: ['บันทึกเอง', 'var(--text-muted)'], import: ['นำเข้าจากไฟล์', 'var(--text-muted)'],
  claim: ['ปรับตามเคลม', 'var(--amber-ink)'] };
function _brnRecvCard(x) {
  const src = _BRN_RECV_SRC[x.source] || [x.source || '', 'var(--text-muted)'];
  const bad = x.lines.filter(l => l.qty_base == null).length;
  const preview = x.lines.slice(0, 3).map(l => esc(l.name || l.code)).join(', ') + (x.lines.length > 3 ? ` และอีก ${x.lines.length - 3} รายการ` : '');
  const rows = x.lines.map(l => `<tr style="border-top:1px solid var(--border)">
      <td class="py-1 pr-2" style="color:var(--text-primary)">${esc(l.name || l.code)}<div class="text-[10.5px]" style="color:var(--text-muted)">${esc(l.code)}</div></td>
      <td class="py-1 pr-2 text-right" style="white-space:nowrap;color:var(--text-primary)">${_bmcNum(l.qty)}</td>
      <td class="py-1" style="color:var(--text-secondary)">${esc(l.unit || '')}${l.qty_base == null ? ' ' + _bmcChip('แปลงหน่วยไม่ได้', 'var(--bad-ink)') : ''}</td></tr>`).join('');
  const del = x.source === 'claim' ? _bmcChip('ลบไม่ได้ (เกิดจากการอนุมัติเคลม)', 'var(--text-muted)')
    : x.locked ? _bmcChip('อยู่ในรอบที่ปิดแล้ว', 'var(--text-muted)')
    : `<button onclick="brnRecvDelete(${x.receipt_id})" style="border:1px solid var(--bad-ink);color:var(--bad-ink);background:transparent;border-radius:var(--r-sm);padding:4px 12px;font-size:12px;cursor:pointer;white-space:nowrap">ลบใบรับ</button>`;
  return `<div style="border:1px solid var(--border);border-radius:var(--r-md, 12px);padding:10px 12px;margin-bottom:8px;background:var(--bg-card, transparent)">
    <div style="display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap">
      <div style="flex:1 1 200px;min-width:0">
        <div class="text-sm" style="color:var(--text-primary);font-weight:600">${esc(x.receipt_date)} ${_bmcChip(src[0], src[1])}</div>
        <div class="text-xs mt-0.5" style="color:var(--text-secondary)">${x.ref ? esc(x.ref) + ' · ' : ''}<b>${x.lines.length}</b> รายการ${x.entered_by && x.entered_by !== '-' ? ' · บันทึกโดย ' + esc(x.entered_by) : ''}
          ${bad ? ' · ' + _bmcChip(bad + ' รายการแปลงหน่วยไม่ได้', 'var(--bad-ink)') : ''}</div>
        <div class="text-xs mt-1" style="color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${preview}</div>
      </div>
      <div>${del}</div>
    </div>
    <details class="mt-1">
      <summary class="text-xs" style="color:var(--accent);cursor:pointer">ดูรายการสินค้า (${x.lines.length})</summary>
      <div style="max-height:320px;overflow:auto;margin-top:6px"><table class="w-full text-xs">
        <thead><tr class="text-left" style="color:var(--text-muted)"><th class="pr-2">สินค้า</th><th class="pr-2 text-right">จำนวน</th><th>หน่วย</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
    </details></div>`;
}
async function brnLoadRecv() {
  const d = document.getElementById('brnRecvDate');
  if (d && !d.value) d.value = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  const dl = document.getElementById('brnItemList');
  if (dl) dl.innerHTML = _brnAllItems().map(i => `<option value="${esc(i.code)} — ${esc(i.name)}"></option>`).join('');
  brnRenderRecvLines();
  if (_brnNeedBranch('brnRecvHistory')) return;
  brnLoadRecvOpen();
  brnLoadClaims();
  try {
    const r = await _bmcJson(`${API}/api/bom-center/stock/receipts?${_brnQS()}`);
    document.getElementById('brnRecvHistory').innerHTML = r.receipts.length ? r.receipts.map(_brnRecvCard).join('')
      : '<div class="text-xs" style="color:var(--text-muted)">ยังไม่มีบันทึกรับสินค้า</div>';
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
  if (!_brnCanCount()) { _bmcStatus('brnRecvStatus', _BRN_READONLY, 'var(--amber-ink)'); return; }
  if (!_brnRecv.length) { _bmcStatus('brnRecvStatus', 'ยังไม่มีรายการ', 'var(--amber-ink)'); return; }
  if (!await uiConfirm(`ยืนยันบันทึกรับสินค้า ${_brnRecv.length} รายการ วันที่ ${document.getElementById('brnRecvDate').value}`)) return;
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
  if (!await uiConfirm('ยืนยันลบบันทึกรับสินค้านี้?')) return;
  try {
    await _bmcJson(`${API}/api/bom-center/stock/receipt/${id}?${_brnQS()}`, { method: 'DELETE' });
    brnLoadRecv();
  } catch (e) { _bmcStatus('brnRecvStatus', 'ลบไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}

// ── receiving an order (S8, reworked 2026-10-07): the orders still to be received are listed
// on the รับสินค้า pane, whatever today's cycle is; the centre's quantities are pre-filled.
async function brnLoadRecvOpen() {
  const box = document.getElementById('brnRecvOpen');
  if (!box) return;
  try {
    const d = await _bmcJson(`${API}/api/bom-center/orders?status=open&limit=20${_brnQS() ? '&' + _brnQS() : ''}`);
    box.style.display = d.orders.length ? '' : 'none';
    box.innerHTML = d.orders.length ? `<div class="text-xs font-semibold mb-1" style="color:var(--text-primary)">ใบสั่งซื้อที่รอรับสินค้า (${d.orders.length})</div>` + d.orders.map(x => `<div style="border-top:1px solid var(--border);padding:6px 0;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center" class="text-xs">
        <span><b>#${x.order_id}</b> · ${esc(_brnRoundLabel(x.round))} · ตัดรอบ ${esc(x.cycle_date)} · ส่งถึง ${esc(x.delivery || '-')} · ${x.lines} รายการ · ฿${_bmcNum(x.value, 0)} ${_bmcChip(x.status_th, x.status === 'processed' ? 'var(--navy-ink)' : 'var(--amber-ink)')}${x.late ? ' ' + _bmcChip('ส่งล่าช้า (ค่าปรับ)', 'var(--bad-ink)') : ''}</span>
        <button onclick="brnOpenReceive(${x.order_id})" style="margin-left:auto;background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:6px 12px;font-size:13px;cursor:pointer">รับสินค้า</button></div>`).join('') : '';
  } catch (e) { box.style.display = 'none'; }
}
let _brnRecvOrd = null;
// what the warehouse sent for a line (the centre's processed quantity, else what the branch confirmed) and its unit label
function _brnRcvSent(l) { return Number(l.processed_units ?? l.confirmed_units ?? 0); }
function _brnRcvUnit(l) { return l.order_unit === 'base' ? '' : 'แพ็ค'; }
// the typed "รับจริง" of a line: a number, or null when it is empty or not a number
function _brnRcvTyped(code) {
  const el = document.getElementById('brnRcvO_' + code);
  const raw = String(el ? el.value : '').trim().replace(',', '.');
  if (raw === '') return null;
  const q = Number(raw);
  return Number.isFinite(q) ? q : null;
}
async function brnOpenReceive(id) {
  const box = document.getElementById('brnRecvOrder');
  if (!box) return;
  try {
    _brnRecvOrd = await _bmcJson(`${API}/api/bom-center/order/${id}`);
    const o = _brnRecvOrd;
    const today = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    const day = o.delivery && o.delivery <= today ? o.delivery : today;
    const lines = o.lines.filter(l => _brnRcvSent(l) > 0 || l.received_units);
    const reasons = o.claim_reasons || [];
    const sel = 'border:1px solid var(--border-control);border-radius:var(--r-sm);padding:6px 8px;font-size:14px;background:var(--bg-input);color:var(--text-primary)';
    box.style.display = '';
    box.innerHTML = `<div class="text-sm font-semibold" style="color:var(--text-primary)">รับสินค้าตามใบสั่งซื้อ #${o.order_id} · ${esc(_brnRoundLabel(o.round))} · ตัดรอบ ${esc(o.cycle_date)} ${_bmcChip(o.status_th, 'var(--navy-ink)')}</div>
      <div class="text-[11px] mt-1" style="color:var(--text-muted)">จำนวนที่คลังจัดส่งกรอกไว้ให้แล้ว หากรับได้น้อยกว่าที่ส่ง ให้แก้ช่อง “รับจริง” และเลือกสาเหตุของส่วนต่าง ระบบจะบันทึกรับเข้าตามจำนวนที่รับจริงและแจ้งเคลมส่วนต่างให้ประสานงานขายในครั้งเดียว</div>
      <label class="text-xs mt-2 block" style="color:var(--text-muted)">วันที่รับ <input type="date" id="brnRecvOrdDate" value="${day}" style="${sel}"></label>
      <div class="mt-2">${lines.map(l => { const c = esc(l.code), sent = _brnRcvSent(l), unit = esc(_brnRcvUnit(l));
        return `<div style="border-top:1px solid var(--border);padding:6px 0">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <span style="flex:1 1 160px;min-width:0" class="text-sm">${esc(l.name)} <span class="text-[11px]" style="color:var(--text-muted)">${c}${l.ss_reason_th ? ' · ส่วนกลาง: ' + esc(l.ss_reason_th) : ''}</span></span>
          <span class="text-xs" style="color:var(--text-secondary)">ส่งจริง <b>${_bmcNum(sent)}</b> ${unit}</span>
          <label class="text-xs" style="color:var(--text-muted)">รับจริง
            <input inputmode="decimal" id="brnRcvO_${c}" value="${esc(String(l.received_units ?? sent))}" oninput="brnRecvDiff('${c}')" style="width:68px;height:36px;text-align:center;font-size:16px;border:1px solid var(--good-ink);border-radius:10px;background:var(--bg-input);color:var(--text-primary)"></label>
          <span class="text-xs" style="width:40px;color:var(--text-secondary)">${unit}</span>
          <span id="brnRcvOver_${c}" class="text-xs" style="display:none;color:var(--navy-ink)"></span></div>
        <div id="brnRcvD_${c}" style="display:none;margin-top:6px;padding:6px 8px;border-left:3px solid var(--amber-ink);background:var(--bg-input);border-radius:var(--r-sm)">
          <div class="text-xs" style="color:var(--text-primary)">ส่วนต่าง <b id="brnRcvDn_${c}">0</b> ${unit} ที่ยังไม่ได้รับ</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:4px">
            <select id="brnRcvR_${c}" aria-label="สาเหตุของส่วนต่าง" style="${sel};max-width:100%">${reasons.map(r => `<option value="${esc(r.code)}"${r.code === 'short' ? ' selected' : ''}>${esc(r.label)}</option>`).join('')}</select>
            <input id="brnRcvN_${c}" placeholder="หมายเหตุ (จำเป็นเมื่อเลือก “อื่น ๆ”)" style="${sel};flex:1 1 200px;min-width:0"></div></div></div>`; }).join('') || '<span class="text-xs" style="color:var(--text-muted)">ใบสั่งซื้อนี้ไม่มีรายการที่จัดส่ง</span>'}</div>
      <div id="brnRecvClaimBox" class="mt-2" style="display:none;padding:8px 10px;border:1px solid var(--amber-ink);border-radius:var(--r-sm)">
        <div class="text-xs font-semibold" style="color:var(--text-primary)" id="brnRecvClaimTitle"></div>
        <div class="text-[11px] mt-1" style="color:var(--text-muted)">ระบบส่งเคลมให้ประสานงานขายพิจารณาทันทีที่บันทึกรับสินค้า · ส่งรูปถ่ายสินค้าที่เคลมให้ประสานงานขายทางไลน์</div>
        <textarea id="brnRecvClaimNote" rows="2" placeholder="ข้อความถึงประสานงานขาย (ถ้ามี)" style="${sel};width:100%;margin-top:6px"></textarea></div>
      <label class="text-xs mt-2" style="display:flex;gap:8px;align-items:flex-start;color:var(--text-primary);cursor:pointer">
        <input type="checkbox" id="brnRecvAck" onchange="brnRecvAckChanged()" style="width:20px;height:20px;margin-top:1px;flex:none">
        <span>สาขาได้ตรวจสอบสภาพสินค้าและจำนวนหรือน้ำหนักของทุกรายการครบถ้วนแล้ว รายการที่บันทึกรับไว้โดยไม่ได้แจ้งเคลมในวันนี้ ถือว่าสาขาได้รับครบถ้วนในสภาพปกติ หากแจ้งเคลมภายหลัง สาขารับทราบว่าประสานงานขายอาจไม่สามารถดำเนินการเคลมได้</span></label>
      <div class="flex flex-wrap items-center gap-2 mt-2">
        <button id="brnRecvOrdSave" onclick="brnReceiveOrder()" disabled style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:8px 16px;font-size:14px;cursor:pointer">บันทึกรับสินค้า</button>
        <button onclick="document.getElementById('brnRecvOrder').style.display='none'" style="border:1px solid var(--border);border-radius:var(--r-sm);padding:8px 14px;font-size:14px;cursor:pointer;background:transparent;color:var(--text-primary)">ปิด</button>
        <span id="brnRecvOrdStatus" class="text-xs"></span></div>`;
    lines.forEach(l => brnRecvDiff(l.code));
    brnRecvAckChanged();
    box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) { box.style.display = ''; box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
// the gap of a line = sent − received; below zero it is an over-delivery (a note only, nothing to claim)
function _brnRcvGap(l) {
  const q = _brnRcvTyped(l.code);
  return q === null ? 0 : Math.round((_brnRcvSent(l) - q) * 1e6) / 1e6;
}
function brnRecvDiff(code) {
  const o = _brnRecvOrd;
  const l = o && o.lines.find(x => x.code === code);
  if (!l) return;
  const gap = _brnRcvGap(l);
  const d = document.getElementById('brnRcvD_' + code), ov = document.getElementById('brnRcvOver_' + code), n = document.getElementById('brnRcvDn_' + code);
  if (d) d.style.display = gap > 1e-9 ? '' : 'none';
  if (n) n.textContent = _bmcNum(Math.max(gap, 0));
  if (ov) { ov.style.display = gap < -1e-9 ? '' : 'none'; ov.textContent = gap < -1e-9 ? `รับเกินที่ส่ง +${_bmcNum(-gap)}` : ''; }
  const k = o.lines.filter(x => document.getElementById('brnRcvO_' + x.code) && _brnRcvGap(x) > 1e-9).length;
  const cb = document.getElementById('brnRecvClaimBox'), t = document.getElementById('brnRecvClaimTitle');
  if (cb) cb.style.display = k ? '' : 'none';
  if (t) t.textContent = `รายการที่จะแจ้งเคลมพร้อมการรับสินค้านี้ (${k} รายการ)`;
}
function brnRecvAckChanged() {
  const b = document.getElementById('brnRecvOrdSave'), a = document.getElementById('brnRecvAck');
  if (b && a) b.disabled = !a.checked;
}
async function brnReceiveOrder() {
  const o = _brnRecvOrd;
  if (!o) return;
  const warn = m => _bmcStatus('brnRecvOrdStatus', m, 'var(--amber-ink)');
  if (!document.getElementById('brnRecvAck').checked) { warn('กรุณายืนยันการตรวจสอบสินค้าก่อนบันทึก'); return; }
  const lines = [];
  let short = 0, over = 0;
  for (const l of o.lines) {
    if (!document.getElementById('brnRcvO_' + l.code)) continue;
    const q = _brnRcvTyped(l.code), nm = l.name || l.code;
    if (q === null || q < 0) { warn(`${nm}: จำนวนต้องเป็นตัวเลขและไม่ติดลบ`); return; }
    const row = { code: l.code, received_units: q };
    const gap = _brnRcvGap(l);
    if (gap > 1e-9) {
      const reason = (document.getElementById('brnRcvR_' + l.code) || {}).value || '';
      const note = String((document.getElementById('brnRcvN_' + l.code) || {}).value || '').trim();
      if (!reason) { warn(`${nm}: กรุณาเลือกสาเหตุของส่วนต่าง`); return; }
      if (reason === 'other' && !note) { warn(`${nm}: สาเหตุ “อื่น ๆ” ต้องระบุคำอธิบาย`); return; }
      row.reason = reason; row.note = note || null;
      short++;
    } else if (gap < -1e-9) over++;
    lines.push(row);
  }
  const n = lines.filter(l => l.received_units > 0).length;
  const day = document.getElementById('brnRecvOrdDate').value;
  const claimNote = String((document.getElementById('brnRecvClaimNote') || {}).value || '').trim();
  if (!await uiConfirm(`ยืนยันบันทึกรับสินค้าตามใบสั่งซื้อ #${o.order_id} · ${n} รายการ · วันที่ ${day}`
      + (short ? ` · แจ้งเคลมส่วนต่าง ${short} รายการให้ประสานงานขาย` : '') + (over ? ` · รับเกินที่ส่ง ${over} รายการ` : ''))) return;
  try {
    const r = await _bmcJson(`${API}/api/bom-center/order/${o.order_id}/receive${_brnQS() ? '?' + _brnQS() : ''}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lines, receipt_date: day || null, ack: true, claim_note: claimNote || null }) });
    _brnRecvOrd = null;
    document.getElementById('brnRecvOrder').style.display = 'none';
    if (r.claim_id) {
      _bmcStatus('brnRecvStatus', `✓ บันทึกรับสินค้าตามใบสั่งซื้อ #${o.order_id} แล้ว · แจ้งเคลม #${r.claim_id} (${r.claim_lines} รายการ) ส่งให้ประสานงานขายแล้ว`, 'var(--good-ink)');
    } else {
      _bmcStatus('brnRecvStatus', `✓ บันทึกรับสินค้าตามใบสั่งซื้อ #${o.order_id} แล้ว`, 'var(--good-ink)');
      brnClaimHint(o.order_id);
    }
    await brnLoadRecv();
    if (r.claim_id) {
      const el = document.getElementById('brnClaimMsg');
      if (el) { el.style.color = 'var(--amber-ink)'; el.textContent = `กรุณาส่งรูปถ่ายสินค้าที่เคลม (เคลม #${r.claim_id}) ให้ประสานงานขายทางไลน์ ผลการพิจารณาจะแสดงที่การ์ดนี้`; }
    }
  } catch (e) { _bmcStatus('brnRecvOrdStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
// the end count's next step, offered on the count pane itself
async function brnConfirmRecvFor(endId) {
  if (!await uiConfirm('ยืนยันว่าได้บันทึกรับสินค้าทุกรายการในรอบนี้ครบแล้ว?')) return;
  try {
    await _bmcJson(`${API}/api/bom-center/variance/confirm-receipts?${_brnQS()}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ end_count_id: endId }) });
    const nx = document.getElementById('brnNext'); if (nx) nx.innerHTML = '';
    _bmcStatus('brnStatus', '✓ ยืนยันรับสินค้าครบแล้ว', 'var(--good-ink)');
    brnSetSub('var');
  } catch (e) { _bmcStatus('brnStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
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
  if (!_brnCanVariance()) { _bmcStatus('brnVarStatus', _BRN_READONLY, 'var(--amber-ink)'); return; }
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
  if (!_brnCanVariance()) { _bmcStatus('brnVarStatus', _BRN_READONLY, 'var(--amber-ink)'); return; }
  if (!undo && !await uiConfirm('ยืนยันว่าได้บันทึกรับสินค้าทุกรายการในรอบนี้ครบแล้ว?')) return;
  try {
    await _bmcJson(`${API}/api/bom-center/variance/confirm-receipts?${_brnQS()}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ end_count_id: _brnVar.end_count_id, undo: !!undo }) });
    await brnLoadVariance(_brnVar.end_count_id);
    _bmcStatus('brnVarStatus', undo ? 'ยกเลิกการยืนยันแล้ว' : '✓ ยืนยันรับสินค้าครบแล้ว', 'var(--good-ink)');
  } catch (e) { _bmcStatus('brnVarStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}

// ── สั่งสินค้า (PR-A, changed by PR-J2) ───────────────────────────────────────────
// One page: choose the delivery → the suggestion is net of the SYSTEM stock (last month-end count + goods
// received − the confirmed daily issue slips, คงเหลือตามระบบ) → order → send before the cutoff (13:15, two
// days before the delivery day). The slip of today must be confirmed first; a send after the cutoff is
// accepted with a late-fee confirmation.
let _brnRound = null;         // null = the round of the nearest delivery still open (the server picks)
let _brnRoundKeep = false;    // a deep link chose the round: the next branch load must not reset it
let _brnDelivery = null;      // the delivery day (ISO) on screen; null = the server picks
let _brnDeliveryKeep = false; // a deep link chose the delivery day
let _brnPlan = null;          // /api/bom-center/order/plan
let _brnOrdQty = {};          // code -> confirmed units (string)
let _brnOrdReason = {};       // code -> {reason_code, note}
let _brnOrdDirty = false;     // anything unsaved on the order page
let _brnBuf = { pct: 0, reason: '', note: '' };   // PR-19: the branch's expectation for the cover period
let _brnOrdEdited = new Set();                    // codes the person typed in this session (kept when the buffer changes)
let _brnCovers = null;                            // /api/bom-center/order/covers
const _BRN_ROUNDS = [['1', 'รอบ 1'], ['2', 'รอบ 2'], ['salmon', 'แซลมอนสด']];

// the coming deliveries as chips: "รอบ 1 · ส่งวันพุธ 7/10" with "ตัดรอบ วันจันทร์ 13:15" under it (red once past)
function _brnDelivChips() {
  const box = document.getElementById('brnOrdDeliv');
  if (!box) return;
  const items = (_brnPlan && _brnPlan.deliveries) || [];
  box.innerHTML = items.map(d => {
    const on = d.round === _brnRound && d.delivery === _brnDelivery;
    const sub = on ? 'inherit' : (d.late ? 'var(--bad-ink)' : 'var(--text-muted)');
    return `<button onclick="brnSetDelivery('${d.round}','${d.delivery}')" style="border:1px solid ${on ? 'var(--accent)' : (d.late ? 'var(--bad-ink)' : 'var(--border)')};border-radius:12px;padding:6px 12px;font-size:13px;cursor:pointer;text-align:left;background:${on ? 'var(--accent)' : 'transparent'};color:${on ? 'var(--on-accent)' : 'var(--text-primary)'}">
      <span style="display:block">${esc(d.round_th)} · ส่ง${esc(d.delivery_th)}</span>
      <span style="display:block;font-size:11px;color:${sub}">ตัดรอบ ${esc(d.cutoff_th)}${d.late ? ' · เลยเวลาแล้ว' : ''}</span></button>`;
  }).join('');
}
function _brnBufQS() { return _brnBuf.pct ? 'buffer_pct=' + encodeURIComponent(_brnBuf.pct) : ''; }
function _brnBufReasonTh(code) { return ((((_brnPlan || {}).buffer || {}).reasons || []).find(r => r.code === code) || {}).label || code || ''; }
async function brnLoadOrder(opts) {
  opts = opts || {};
  _brnDelivChips();
  if (_brnNeedBranch('brnOrdList')) { document.getElementById('brnOrdHead').innerHTML = ''; return; }
  if (!opts.keepEdits) {                                       // a reload that keeps the typed values redraws in place
    document.getElementById('brnOrdList').innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังคำนวณยอดแนะนำ…</span>';
    _bmcStatus('brnOrdStatus', '');
  }
  try {
    // no delivery chosen yet: the server opens the nearest one whose cutoff has not passed
    const qs = [_brnRound ? 'round=' + _brnRound : '', _brnDelivery ? 'delivery=' + _brnDelivery : '', _brnQS(), opts.withBuffer ? _brnBufQS() : ''].filter(Boolean).join('&');
    _brnPlan = await _bmcJson(`${API}/api/bom-center/order/plan${qs ? '?' + qs : ''}`);
    _brnRound = _brnPlan.round || _brnRound || '1';
    _brnDelivery = _brnPlan.delivery;
    _brnDelivChips();
    const kept = opts.keepEdits ? { qty: _brnOrdQty, reason: _brnOrdReason, edited: _brnOrdEdited } : null;
    _brnOrdQty = {}; _brnOrdReason = {}; _brnOrdEdited = new Set();
    const o = _brnPlan.order;
    _brnPlan.rows.forEach(r => { _brnOrdQty[r.code] = String(r.order_units); });
    if (o) o.lines.forEach(l => {
      _brnOrdQty[l.code] = l.confirmed_units == null ? '' : String(l.confirmed_units);
      if (l.reason_code) _brnOrdReason[l.code] = { reason_code: l.reason_code, note: l.note || '' };
    });
    if (kept) {                                  // a new buffer: untouched lines take the new suggestion, typed ones stay
      _brnPlan.rows.forEach(r => { if (!kept.edited.has(r.code)) _brnOrdQty[r.code] = String(r.order_units); });
      kept.edited.forEach(c => { if (c in kept.qty) _brnOrdQty[c] = kept.qty[c]; if (kept.reason[c]) _brnOrdReason[c] = kept.reason[c]; });
      _brnOrdEdited = kept.edited;
    } else { _brnOrdDirty = false; }
    const pb = _brnPlan.buffer || {};
    _brnBuf = { pct: Number(pb.pct) || 0, reason: o && o.buffer_reason ? o.buffer_reason : (opts.withBuffer ? _brnBuf.reason : ''),
                note: o && o.buffer_note ? o.buffer_note : (opts.withBuffer ? _brnBuf.note : '') };
    brnRenderOrder();
    brnLoadOrderHistory();
  } catch (e) { document.getElementById('brnOrdList').innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
async function brnSetDelivery(round, delivery) {
  clearTimeout(_brnOrdAuto);
  if (_brnOrdDirty) { try { await brnOrderSave(false, true); } catch (e) { /* the draft is re-saved on the next edit */ } }
  _brnRound = round; _brnDelivery = delivery;
  brnLoadOrder();
}
// today's slip is confirmed (on time or late): the order may be sent
function _brnSlipOk(p) { const s = ((p || _brnPlan || {}).stock || {}).slip_today || {}; return s.status === 'confirmed' || s.status === 'late'; }
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
        <div class="text-[11px]" style="color:var(--text-muted)">${esc(r.code)} · ชั้น ${r.class} · ต้องใช้ ${_bmcNum(r.need, 0)} ${esc(r.base_unit || '')}</div></div>
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
  _brnRenderBuffer(p, editable);
  const up = 1 + ((p.buffer || {}).pct || 0) / 100;
  const stk = p.stock || {};
  const slip = stk.slip_today || {};
  const slipOk = _brnSlipOk(p);
  const md = stk.missing_days || [];
  const stockLine = (stk.deducted
    ? `${_bmcChip('หักคงเหลือตามระบบแล้ว', 'var(--good-ink)')} ยอดแนะนำหักคงเหลือตามระบบหลังตัดเบิกวันนี้แล้ว${slipOk ? ` · ใบตัดเบิกวันนี้: ยืนยันแล้ว เวลา ${esc(_brnIssTime(slip.confirmed_at))}` : ''}`
    : `${_bmcChip('ไม่มีผลตรวจนับสิ้นเดือน', 'var(--text-muted)')} ยังไม่มีผลตรวจนับสิ้นเดือน ยอดแนะนำจึงไม่หักคงเหลือ${slipOk ? ` · ใบตัดเบิกวันนี้: ยืนยันแล้ว เวลา ${esc(_brnIssTime(slip.confirmed_at))}` : ''}`)
    + (slipOk ? '' : `<div class="mt-1 text-xs" style="border:1px solid var(--amber-ink);border-radius:var(--r-sm);padding:6px 10px;color:var(--amber-ink)">ต้องยืนยันใบตัดเบิกของวันนี้ก่อนส่งใบสั่งซื้อ
        <a href="#" onclick="brnIssGoToday();return false" style="color:var(--accent);font-weight:600">→ ไปที่ตัดเบิก</a></div>`)
    + (md.length ? `<div class="mt-1 text-xs" style="color:var(--amber-ink)">ใบตัดเบิกขาด ${md.length} วัน (${md.slice(0, 6).map(_brnIssDm).join(', ')}${md.length > 6 ? ` และอีก ${md.length - 6} วัน` : ''}) ยอดคงเหลือตามระบบอาจสูงกว่าจริง</div>` : '');
  const byCode = {};
  p.rows.forEach(r => { byCode[r.code] = r; });
  const extra = o ? o.lines.filter(l => !byCode[l.code]) : [];
  const total = Object.entries(_brnOrdQty).reduce((a, [c, v]) => {
    const r = byCode[c];
    if (!r || !r.price_per_base || !(Number(v) > 0)) return a;
    return a + Number(v) * (r.order_unit === 'pack' ? (r.qty_per_recv || 1) : 1) * r.price_per_base;
  }, 0);
  const left = p.hours_left == null ? '' : (p.hours_left >= 1 ? `เหลือ ${Math.floor(p.hours_left)} ชม.` : `เหลือ ${Math.max(Math.round(p.hours_left * 60), 0)} นาที`);
  document.getElementById('brnOrdHead').innerHTML = `<div style="color:var(--text-primary)" class="text-sm">รอบส่ง<b>${esc(p.delivery_th)}</b> · ส่งภายใน <b>${esc(p.cutoff_th)}</b>${p.open && editable ? ' · ' + left : ''} ·
      ใช้ได้ ${esc(p.cover_from)} ถึง ${esc(p.cover_to)} (${p.cover_days} วัน)</div>
    <div class="mt-1" style="color:var(--text-secondary)">${stockLine}</div>
    <div class="mt-1" style="color:var(--text-secondary)">${o ? `ใบสั่งซื้อ #${o.order_id} ${_bmcChip(o.status_th, o.status === 'received' ? 'var(--good-ink)' : (o.status === 'submitted' || o.status === 'processed') ? 'var(--navy-ink)' : 'var(--amber-ink)')}${o.late ? ' ' + _bmcChip('ส่งล่าช้า (ค่าปรับ)', 'var(--bad-ink)') : ''}` : _bmcChip('ยังไม่มีใบสั่งซื้อรอบนี้', 'var(--text-muted)')}
      · มูลค่าที่สั่ง ≈ <b>฿${_bmcNum(total, 0)}</b>${p.calendar_filled ? '' : ' · <span style="color:var(--amber-ink)">ใช้วันส่งค่าเริ่มต้น · ประสานงานขายกำหนดวันส่งได้ที่ ตั้งค่า → รอบส่งของสาขา</span>'}</div>`;
  const banner = document.getElementById('brnOrdBanner');
  if (banner) {                                    // past the cutoff and still a draft: the late fee, in red
    const showLate = !!p.late && editable;
    banner.style.display = showLate ? '' : 'none';
    banner.innerHTML = showLate ? `<div class="text-xs" style="border:1px solid var(--bad-ink);border-radius:var(--r-sm);padding:8px 10px;color:var(--bad-ink)">เลยเวลาตัดรอบแล้ว (${esc(p.cutoff_th)}) หากส่งใบสั่งซื้อนี้ จะมีค่าปรับล่าช้าที่ต้องชำระให้คลังสินค้าภายนอก</div>` : '';
  }
  const q = (document.getElementById('brnOrdQ').value || '').trim().toLowerCase();
  const only = document.getElementById('brnOrdOnly').checked;
  const rows = p.rows.filter(r => (!only || r.order_units > 0 || Number(_brnOrdQty[r.code] || 0) > 0)
    && (!q || (r.code + ' ' + r.name).toLowerCase().includes(q)));
  const line = o ? Object.fromEntries(o.lines.map(l => [l.code, l])) : {};
  const reasonSel = (code) => {
    const cur = _brnOrdReason[code] || {};
    return `<div class="flex flex-wrap gap-2 mt-1 text-xs"><select onchange="_brnOrdReason[${_bmcQ(code)}]=Object.assign(_brnOrdReason[${_bmcQ(code)}]||{}, {reason_code:this.value});_brnOrdTouched()" ${editable ? '' : 'disabled'}
        style="border:1px solid var(--amber-ink);border-radius:var(--r-sm);padding:4px 6px;font-size:12px;background:var(--bg-input);color:var(--text-primary)"><option value="">— เหตุผลที่สั่งต่างจากยอดแนะนำ —</option>${p.reasons.map(x => `<option value="${x.code}"${cur.reason_code === x.code ? ' selected' : ''}>${esc(x.label)}</option>`).join('')}</select>
      <input value="${esc(cur.note || '')}" placeholder="หมายเหตุ" oninput="_brnOrdReason[${_bmcQ(code)}]=Object.assign(_brnOrdReason[${_bmcQ(code)}]||{}, {note:this.value});_brnOrdTouched()" ${editable ? '' : 'disabled'}
        style="flex:1 1 120px;border:1px solid var(--border-control);border-radius:var(--r-sm);padding:4px 6px;font-size:12px;background:var(--bg-input);color:var(--text-primary)"></div>`;
  };
  const html = rows.map(r => {
    const base = line[r.code] ? line[r.code].suggested_units : (r.order_units_base ?? r.order_units);
    const sugg = line[r.code] ? (up === 1 ? base : Math.round(base * up)) : r.order_units;
    const v = _brnOrdQty[r.code] ?? '';
    const dev = _brnOrdNeedsReason(sugg, v);
    const detail = `พยากรณ์ ${_bmcNum(r.forecast, 0)} + สำรอง ${_bmcNum(r.safety, 0)}` +
      (r.on_hand != null ? ` − คงเหลือคาดการณ์ ${_bmcNum(Math.max(r.projected, 0), 0)}` : '') +
      (r.on_order ? ` (รวมค้างส่ง ${_bmcNum(r.on_order, 0)})` : '') + ` = ${_bmcNum(r.need, 0)} ${esc(r.base_unit || '')}`;
    const sysBox = r.system_qty == null ? '' : `<span class="text-[11px]" style="color:var(--text-muted)">คงเหลือตามระบบ <b style="color:${r.system_qty < 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">${_bmcNum(r.system_qty, 2)}</b> ${esc(r.system_unit || r.count_unit || r.base_unit || '')}</span>`;
    return `<div style="border-top:1px solid var(--border);padding:8px 0">
      <div style="display:flex;flex-wrap:wrap;gap:6px 8px;align-items:center">
        <div style="flex:1 1 220px;min-width:0"><div class="text-sm" style="color:var(--text-primary);font-weight:600">${esc(r.name)}
          ${r.monitored ? _bmcChip('บังคับ', 'var(--amber-ink)') : ''} ${(r.via || []).length ? _bmcChip('ใช้ใน ' + r.via.slice(0, 2).join(', '), 'var(--text-muted)') : ''}</div>
          <div class="text-[11px]" style="color:var(--text-muted)">${esc(r.code)} · ชั้น ${r.class} · ${detail}</div></div>
        <div style="display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;margin-left:auto">
          ${sysBox}
          <span class="text-xs" style="color:var(--text-muted)">แนะนำ <b style="color:var(--text-primary)">${_bmcNum(sugg, 0)}</b>${up !== 1 ? ` <span class="text-[10.5px]">(ปกติ ${_bmcNum(base, 0)})</span>` : ''}</span>
          <label class="text-[11px]" style="display:flex;gap:4px;align-items:center;color:var(--text-muted)">สั่ง
          <input id="brnOrd_${esc(r.code)}" inputmode="decimal" value="${esc(v)}" ${editable ? '' : 'disabled'} oninput="_brnOrdQty[${_bmcQ(r.code)}]=this.value;_brnOrdEdited.add(${_bmcQ(r.code)});brnRenderOrderDeferred()"
            style="width:64px;height:36px;text-align:center;font-size:16px;border:1px solid ${dev ? 'var(--amber-ink)' : 'var(--border-control)'};border-radius:10px;background:var(--bg-input);color:var(--text-primary)">
          <span class="text-xs" style="max-width:64px;color:var(--text-secondary);overflow-wrap:anywhere">${esc(_brnUnitLabel(r))}</span></label></div></div>
      ${dev ? reasonSel(r.code) : ''}</div>`;
  }).join('') + extra.map(l => `<div style="border-top:1px solid var(--border);padding:8px 0" class="text-sm">${esc(l.name || l.code)} · สั่งเพิ่มนอกยอดแนะนำ ${_bmcNum(l.confirmed_units)} ${esc(l.order_unit === 'base' ? '' : 'แพ็ค')} ${l.reason_th ? '· ' + esc(l.reason_th) : ''}</div>`).join('');
  document.getElementById('brnOrdList').innerHTML = html || '<span class="text-xs" style="color:var(--good-ink)">✓ รอบนี้ไม่มีรายการที่ต้องสั่ง (คงเหลือเพียงพอ)</span>';
  const btn = (label, on, primary) => `<button onclick="${on}" style="border:${primary ? '0' : '1px solid var(--border)'};border-radius:var(--r-sm);padding:8px 14px;font-size:14px;cursor:pointer;background:${primary ? 'var(--accent)' : 'transparent'};color:${primary ? 'var(--on-accent)' : 'var(--text-primary)'}">${label}</button>`;
  let acts = '';
  if (editable) {
    const send = slipOk ? btn('ส่งให้ส่วนกลาง', 'brnOrderSave(true)', true)
      : `<button disabled title="ต้องยืนยันใบตัดเบิกของวันนี้ก่อน" style="border:0;border-radius:var(--r-sm);padding:8px 14px;font-size:14px;cursor:not-allowed;opacity:.5;background:var(--accent);color:var(--on-accent)">ส่งให้ส่วนกลาง</button>`;
    acts = btn('บันทึกร่าง', 'brnOrderSave(false)') + send + (o ? btn('ยกเลิกใบสั่งซื้อ', 'brnOrderAct("cancel")') : '');
  }
  else if (o.status === 'submitted') acts = btn('เปิดแก้ไข', 'brnOrderAct("reopen")') + btn('รับสินค้าที่มาถึง', 'brnStartReceive()', true);
  else if (o.status === 'processed') acts = btn('รับสินค้าที่มาถึง', 'brnStartReceive()', true);
  document.getElementById('brnOrdActions').innerHTML = acts;
}
// ── PR-19: the buffer card — "คาดว่ายอดขายรอบนี้จะต่างจากปกติหรือไม่" ─────────────
function _brnRenderBuffer(p, editable) {
  const box = document.getElementById('brnOrdBuffer');
  if (!box) return;
  const b = p.buffer || { pct: 0, reasons: [] };
  const cur = _brnBuf;
  const sign = v => (Number(v) > 0 ? '+' : '') + _bmcNum(v, 0) + '%';
  if (!editable) {
    box.innerHTML = cur.pct ? `<div class="text-xs" style="color:var(--text-secondary)">สาขาปรับยอดพยากรณ์ <b>${sign(cur.pct)}</b> · ${esc(_brnBufReasonTh(cur.reason))}${cur.note ? ' · ' + esc(cur.note) : ''}</div>` : '';
    return;
  }
  const inp = 'border:1px solid var(--border-control);border-radius:var(--r-sm);padding:5px 8px;font-size:13px;background:var(--bg-input);color:var(--text-primary)';
  const season = p.season && Number(p.season.factor) !== 1 && p.season.factor ? ` และปรับตามปฏิทิน/ปีก่อนแล้ว (×${_bmcNum(p.season.factor, 2)})` : '';
  box.innerHTML = `<div class="text-xs font-semibold" style="color:var(--text-primary)">คาดว่ายอดขายรอบนี้จะต่างจากปกติหรือไม่</div>
    <div class="text-[11px] mb-1" style="color:var(--text-muted)">ยอดแนะนำมาจากยอดขาย 6 สัปดาห์ล่าสุด${season} · หากมีงาน โปรโมชัน หรือลูกค้าจอง ให้ระบุ % ที่คาดว่าจะเพิ่มหรือลด ระบบคำนวณยอดแนะนำใหม่ทุกรายการ และส่วนที่เพิ่มตามนี้ไม่ต้องให้เหตุผลรายบรรทัด</div>
    <div class="flex flex-wrap items-center gap-2 text-xs">
      <input id="brnBufPct" type="number" inputmode="numeric" step="5" min="${b.min ?? -50}" max="${b.max ?? 100}" value="${cur.pct || 0}" style="width:72px;height:34px;text-align:center;font-size:15px;${inp}"><span style="color:var(--text-secondary)">%</span>
      <select id="brnBufReason" style="${inp}"><option value="">— เหตุผล —</option>${(b.reasons || []).map(r => `<option value="${r.code}" ${r.code === cur.reason ? 'selected' : ''}>${esc(r.label)}</option>`).join('')}</select>
      <input id="brnBufNote" value="${esc(cur.note || '')}" placeholder="หมายเหตุ" style="flex:1 1 120px;${inp}">
      <button onclick="brnApplyBuffer()" style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:7px 12px;font-size:13px;cursor:pointer">ใช้กับยอดแนะนำ</button>
      <button onclick="brnToggleCovers()" style="border:1px solid var(--border);border-radius:var(--r-sm);padding:7px 12px;font-size:13px;cursor:pointer;background:transparent;color:var(--text-primary)">คำนวณจากจำนวนลูกค้า</button>
    </div>
    ${cur.pct ? `<div class="text-[11px] mt-1" style="color:var(--navy-ink)">ใช้อยู่: ${sign(cur.pct)} · ${esc(_brnBufReasonTh(cur.reason))}</div>` : ''}
    <div id="brnCovers" class="mt-2" style="display:none"></div>`;
}
async function brnApplyBuffer() {
  const pct = Number(document.getElementById('brnBufPct').value || 0);
  const reason = document.getElementById('brnBufReason').value;
  const note = document.getElementById('brnBufNote').value.trim();
  const b = (_brnPlan && _brnPlan.buffer) || {};
  if (pct < (b.min ?? -50) || pct > (b.max ?? 100)) { _bmcStatus('brnOrdStatus', `% ต้องอยู่ระหว่าง ${b.min ?? -50} ถึง +${b.max ?? 100}`, 'var(--bad-ink)'); return; }
  if (pct && !reason) { _bmcStatus('brnOrdStatus', 'กรุณาเลือกเหตุผลที่คาดว่ายอดขายจะต่างจากปกติ', 'var(--bad-ink)'); return; }
  if (pct && reason === 'other' && !note) { _bmcStatus('brnOrdStatus', 'เหตุผล “อื่น ๆ” ต้องระบุหมายเหตุ', 'var(--bad-ink)'); return; }
  _brnBuf = { pct, reason: pct ? reason : '', note: pct ? note : '' };
  await brnLoadOrder({ withBuffer: true, keepEdits: true });
  _bmcStatus('brnOrdStatus', pct ? `ปรับยอดแนะนำตามที่คาด ${pct > 0 ? '+' : ''}${_bmcNum(pct, 0)}% แล้ว (${_brnBufReasonTh(reason)})` : 'กลับมาใช้ยอดพยากรณ์ปกติแล้ว', 'var(--good-ink)');
  _brnOrdTouched();                              // the draft keeps the buffer
}
async function brnToggleCovers() {
  const box = document.getElementById('brnCovers');
  if (!box) return;
  if (box.style.display !== 'none') { box.style.display = 'none'; return; }
  box.style.display = '';
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังดึงจำนวนลูกค้า…</span>';
  try {
    const qs = [_brnRound ? 'round=' + _brnRound : '', _brnPlan ? 'order_day=' + _brnPlan.order_day : '', _brnQS()].filter(Boolean).join('&');
    _brnCovers = await _bmcJson(`${API}/api/bom-center/order/covers${qs ? '?' + qs : ''}`);
    brnRenderCovers();
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function brnRenderCovers() {
  const c = _brnCovers, box = document.getElementById('brnCovers');
  if (!c || !box) return;
  if (!c.available) { box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">ยังไม่มีข้อมูลจำนวนลูกค้าของสาขานี้ จึงคำนวณให้ไม่ได้ กรุณาระบุ % เอง</span>'; return; }
  const inp = 'width:64px;height:30px;text-align:center;font-size:13px;border:1px solid var(--border-control);border-radius:6px;background:var(--bg-input);color:var(--text-primary)';
  box.innerHTML = `<div class="text-[11px] mb-1" style="color:var(--text-muted)">ลูกค้าต่อวันตามปกติ = ค่าเฉลี่ยวันเดียวกันของสัปดาห์ ${esc(c.history_from)} ถึง ${esc(c.history_to)} · กรอกจำนวนที่คาดในช่องขวา ระบบแปลงเป็น % ให้</div>
    <table class="text-xs w-full"><thead><tr class="text-left" style="color:var(--text-muted)"><th class="pr-2">วันที่</th><th class="pr-2 text-right">ปกติ (คน)</th><th class="pr-2 text-right">ปีก่อน</th><th class="text-right">คาดว่า (คน)</th></tr></thead>
    <tbody>${c.days.map((d, i) => `<tr style="border-top:1px solid var(--border-soft)"><td class="pr-2 py-1">${esc(d.weekday)} ${esc(d.date.slice(5))}</td><td class="pr-2 text-right">${_bmcNum(d.baseline_pax, 0)}</td>
      <td class="pr-2 text-right" style="color:var(--text-muted)">${d.last_year_pax == null ? '–' : _bmcNum(d.last_year_pax, 0)}</td>
      <td class="text-right"><input id="brnCov_${i}" inputmode="numeric" value="${d.baseline_pax}" oninput="brnCoversCalc()" style="${inp}"></td></tr>`).join('')}</tbody>
    <tfoot><tr style="border-top:1px solid var(--border)"><td class="pr-2 py-1 font-semibold">รวม</td><td class="pr-2 text-right font-semibold">${_bmcNum(c.baseline_total, 0)}</td>
      <td class="pr-2 text-right" style="color:var(--text-muted)">${c.last_year_total == null ? '–' : _bmcNum(c.last_year_total, 0)}</td><td class="text-right font-semibold" id="brnCovSum">${_bmcNum(c.baseline_total, 0)}</td></tr></tfoot></table>
    <div class="flex flex-wrap items-center gap-2 mt-2 text-xs"><span id="brnCovPct" style="color:var(--text-primary)">= เท่ากับปกติ (0%)</span>
      <button onclick="brnCoversUse()" style="border:1px solid var(--accent);color:var(--accent);background:transparent;border-radius:var(--r-sm);padding:5px 10px;font-size:12.5px;cursor:pointer">ใช้ค่านี้</button></div>`;
}
function _brnCoversPct() {
  const c = _brnCovers;
  if (!c || !c.available || !c.baseline_total) return 0;
  const sum = c.days.reduce((a, d, i) => a + (Number((document.getElementById('brnCov_' + i) || {}).value) || 0), 0);
  const el = document.getElementById('brnCovSum'); if (el) el.textContent = _bmcNum(sum, 0);
  return Math.round((sum / c.baseline_total - 1) * 100);
}
function brnCoversCalc() {
  const pct = _brnCoversPct();
  const el = document.getElementById('brnCovPct');
  if (el) el.textContent = pct ? `= ${pct > 0 ? 'เพิ่ม' : 'ลด'} ${Math.abs(pct)}% จากปกติ` : '= เท่ากับปกติ (0%)';
}
function brnCoversUse() {
  const pct = _brnCoversPct();
  const el = document.getElementById('brnBufPct');
  if (el) el.value = Math.max(-50, Math.min(100, pct));
  _bmcStatus('brnOrdStatus', `ใส่ ${pct > 0 ? '+' : ''}${pct}% ให้แล้ว เลือกเหตุผลแล้วกด “ใช้กับยอดแนะนำ”`, 'var(--text-muted)');
}

let _brnOrdTimer = null, _brnOrdAuto = null;
function brnRenderOrderDeferred() { clearTimeout(_brnOrdTimer); _brnOrdTimer = setTimeout(brnRenderOrder, 700); _brnOrdTouched(); }
// the draft saves itself 2.5 s after the last change (as the count sheet does), so nothing typed is lost
function _brnOrdTouched() { _brnOrdDirty = true; clearTimeout(_brnOrdAuto); _brnOrdAuto = setTimeout(() => brnOrderSave(false, true), 2500); }

function _brnOrdLines() {
  return Object.entries(_brnOrdQty).map(([code, v]) => ({ code, confirmed_units: v === '' ? null : Number(v),
    reason_code: (_brnOrdReason[code] || {}).reason_code || null, note: (_brnOrdReason[code] || {}).note || null }))
    .filter(l => l.confirmed_units === null || !isNaN(l.confirmed_units));
}
const _BRN_LATE_MSG = cut => `เลยเวลาตัดรอบแล้ว (${cut}) หากส่งใบสั่งซื้อนี้ จะมีค่าปรับล่าช้าที่ต้องชำระให้คลังสินค้าภายนอก`;
async function brnOrderSave(submit, quiet, ackLate) {
  clearTimeout(_brnOrdAuto);
  if (!_brnCanOrder()) { if (!quiet) _bmcStatus('brnOrdStatus', _BRN_READONLY, 'var(--amber-ink)'); return; }
  if (!_brnPlan || !_brnPlan.rows) return;
  const cur = _brnPlan.order;
  if (cur && !['suggested', 'confirmed'].includes(cur.status)) return;      // sent already: nothing to save
  const p = _brnPlan;
  if (submit && !ackLate) {
    if (!_brnSlipOk(p)) {                                                   // the order is made from the stock after today's issue
      _bmcStatus('brnOrdStatus', 'ต้องยืนยันใบตัดเบิกของวันนี้ก่อนส่งใบสั่งซื้อ', 'var(--amber-ink)');
      return;
    }
    const late = !!p.late;
    if (!await uiConfirm((late ? _BRN_LATE_MSG(p.cutoff_th) + '\n' : '')
        + `ยืนยันส่งใบสั่งซื้อรอบส่ง${p.delivery_th} ให้ส่วนกลาง (ประสานงานขาย)?\nแก้ไขได้จนกว่าส่วนกลางจะเริ่มดำเนินการ`)) return;
    ackLate = late;
  }
  try {
    const r = await _bmcJson(`${API}/api/bom-center/order/save${_brnQS() ? '?' + _brnQS() : ''}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ round: _brnRound, delivery: p.delivery, lines: _brnOrdLines(), submit,
                             buffer: _brnBuf.pct ? { pct: _brnBuf.pct, reason: _brnBuf.reason, note: _brnBuf.note } : { pct: 0 },
                             ack_late: !!ackLate }) });
    if (quiet) {                                 // the autosave: the page stays where the person is
      const created = !_brnPlan.order;
      if (created) _brnPlan.order = { order_id: r.order_id, status: 'confirmed', status_th: 'ร่าง (ยังไม่ส่ง)', lines: [] };
      _brnOrdDirty = false;
      _bmcStatus('brnOrdStatus', `บันทึกร่างอัตโนมัติ ${new Date().toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' })}`
        + (r.needs_reason.length ? ` · ต้องระบุเหตุผล ${r.needs_reason.length} รายการก่อนส่ง` : ''), r.needs_reason.length ? 'var(--amber-ink)' : 'var(--text-muted)');
      if (created) brnRenderOrder();
      return;
    }
    await brnLoadOrder();
    _bmcStatus('brnOrdStatus', submit ? `✓ ส่งใบสั่งซื้อ #${r.order_id} แล้ว · ฿${_bmcNum(r.value, 0)}${r.late ? ' · ส่งล่าช้า มีค่าปรับ' : ''}` :
      `✓ บันทึกร่าง${r.needs_reason.length ? ` · ต้องระบุเหตุผล ${r.needs_reason.length} รายการก่อนส่ง` : ''}`, r.needs_reason.length && !submit ? 'var(--amber-ink)' : 'var(--good-ink)');
  } catch (e) {
    if (e.detail && e.detail.code === 'late' && !ackLate) {        // the server's clock says the cutoff has passed: ask once, then send with the late fee
      if (await uiConfirm(_BRN_LATE_MSG(e.detail.cutoff_th) + '\nยืนยันส่งใบสั่งซื้อนี้หรือไม่?')) return brnOrderSave(submit, quiet, true);
      return;
    }
    _bmcStatus('brnOrdStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 220), 'var(--bad-ink)');
  }
}
async function brnOrderAct(what) {
  const o = _brnPlan && _brnPlan.order;
  if (!o) return;
  if (what === 'cancel' && !await uiConfirm('ยืนยันยกเลิกใบสั่งซื้อนี้?')) return;
  try {
    await _bmcJson(`${API}/api/bom-center/order/${o.order_id}/${what}${_brnQS() ? '?' + _brnQS() : ''}`, { method: 'POST' });
    await brnLoadOrder();
  } catch (e) { _bmcStatus('brnOrdStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
// receiving an order is done on the รับสินค้า page, in the one form that also carries the claim and the acknowledgement
function brnStartReceive() {
  const o = _brnPlan && _brnPlan.order;
  if (!o) return;
  brnSetSub('recv');
  setTimeout(() => brnOpenReceive(o.order_id), 200);
}
function _brnRoundLabel(r) { return (_BRN_ROUNDS.find(x => x[0] === r) || [0, r])[1]; }
async function brnLoadOrderHistory() {
  try {
    const d = await _bmcJson(`${API}/api/bom-center/orders${_brnQS() ? '?' + _brnQS() : ''}`);
    document.getElementById('brnOrdHistory').innerHTML = d.orders.length ? d.orders.map(x => `<div style="border-top:1px solid var(--border);padding:4px 0;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center">
        <span><a href="#" onclick="brnOpenOrder(${x.order_id});return false" style="color:var(--accent);font-weight:600">#${x.order_id}</a> · ตัดรอบ ${esc(x.cycle_date)} · ${esc(_brnRoundLabel(x.round))} · ${esc(x.status_th)} · ฿${_bmcNum(x.value, 0)}${x.late ? ' ' + _bmcChip('ส่งล่าช้า (ค่าปรับ)', 'var(--bad-ink)') : ''}</span>
        ${['submitted', 'processed'].includes(x.status) ? `<a href="#" onclick="brnSetSub('recv');setTimeout(() => brnOpenReceive(${x.order_id}), 200);return false" style="margin-left:auto;color:var(--accent)">รับสินค้า →</a>` : ''}</div>`).join('') : 'ยังไม่มีข้อมูล';
  } catch (e) { document.getElementById('brnOrdHistory').textContent = String(e.message || e); }
}
// one order, read-only: what was suggested, confirmed, sent and received
async function brnOpenOrder(id) {
  const box = document.getElementById('brnOrdDetail');
  try {
    const o = await _bmcJson(`${API}/api/bom-center/order/${id}`);
    box.style.display = '';
    box.innerHTML = `<div class="flex flex-wrap items-center gap-2"><div class="text-sm font-semibold" style="color:var(--text-primary)">ใบสั่งซื้อ #${o.order_id} · ${esc(_brnRoundLabel(o.round))} · ตัดรอบ ${esc(o.cycle_date)} · ส่งถึง ${esc(o.delivery || '-')} ${_bmcChip(o.status_th, 'var(--navy-ink)')}${o.late ? ' ' + _bmcChip('ส่งล่าช้า (ค่าปรับ)', 'var(--bad-ink)') : ''}</div>
        <button onclick="document.getElementById('brnOrdDetail').style.display='none'" class="ml-auto" style="border:0;background:none;color:var(--text-muted);font-size:12px;cursor:pointer">ปิด</button></div>
      <div class="text-[11px] mt-1" style="color:var(--text-muted)">${o.po_ref ? 'PO ' + esc(o.po_ref) + ' · ' : ''}${o.processed_by ? 'ส่วนกลางดำเนินการโดย ' + esc(o.processed_by) : ''}</div>
      <table class="w-full text-xs mt-2"><thead><tr class="text-left" style="color:var(--text-muted)"><th class="pr-2">รายการ</th><th class="pr-2 text-right">แนะนำ</th><th class="pr-2 text-right">สั่ง</th><th class="pr-2 text-right">ส่ง</th><th class="text-right">รับ</th></tr></thead>
      <tbody>${o.lines.filter(l => l.confirmed_units || l.processed_units || l.received_units).map(l => `<tr style="border-top:1px solid var(--border-soft)"><td class="pr-2 py-1">${esc(l.name)}${l.reason_th ? ` <span style="color:var(--amber-ink)">· ${esc(l.reason_th)}</span>` : ''}</td>
        <td class="pr-2 text-right">${_bmcNum(l.suggested_units, 0)}</td><td class="pr-2 text-right">${_bmcNum(l.confirmed_units, 0)}</td><td class="pr-2 text-right">${l.processed_units != null ? _bmcNum(l.processed_units, 0) : '–'}</td><td class="text-right">${l.received_units != null ? _bmcNum(l.received_units, 0) : '–'}</td></tr>`).join('')}</tbody></table>`;
  } catch (e) { box.style.display = ''; box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}

// ── แจ้งเคลมสินค้า (owner 2026-10-08, 2026-10-09) ────────────────────────────────
// A shortfall is claimed inside the receive form above (origin 'receive'). This card is for a LATER
// claim on a received order: the lines and a reason each. No photo is attached here: the branch
// sends the pictures to Sales Support by LINE. Sales Support decides (js/15-orderhub.js); a later
// claim, once approved, takes the goods out of what the branch "received" so the variance stays true.
let _brnClaimOrd = null;       // GET /claims/claimable — the order being claimed
const _BRN_CLAIM_ST = { submitted: ['รอประสานงานขายพิจารณา', 'var(--amber-ink)'], approved: ['อนุมัติรับเคลมแล้ว', 'var(--good-ink)'],
  rejected: ['ไม่อนุมัติ', 'var(--bad-ink)'], cancelled: ['ยกเลิกแล้ว', 'var(--text-muted)'] };
function _brnCanClaim() { return _brnCanOrder(); }

async function brnLoadClaims() {
  const box = document.getElementById('brnClaimList');
  if (!box) return;
  const q = _brnQS() ? '&' + _brnQS() : '';
  try {
    const [ords, cl] = await Promise.all([_bmcJson(`${API}/api/bom-center/orders?status=received&limit=6${q}`),
                                          _bmcJson(`${API}/api/bom-center/claims?limit=15${q}`)]);
    const recent = ords.orders.map(o => `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;border-top:1px solid var(--border);padding:6px 0" class="text-xs">
        <span><b>#${o.order_id}</b> · ${esc(_brnRoundLabel(o.round))} · ตัดรอบ ${esc(o.cycle_date)} · รับแล้ว ${esc(String(o.received_at || '').slice(0, 10))}</span>
        ${_brnCanClaim() ? `<button onclick="brnClaimOpen(${o.order_id})" style="margin-left:auto;border:1px solid var(--accent);color:var(--accent);background:transparent;border-radius:var(--r-sm);padding:6px 12px;font-size:13px;cursor:pointer">แจ้งเคลม</button>` : ''}</div>`).join('');
    const mine = cl.claims.map(c => { const st = _BRN_CLAIM_ST[c.status] || [c.status, 'var(--text-muted)'];
      return `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;border-top:1px solid var(--border);padding:6px 0" class="text-xs">
        <a href="#" onclick="brnClaimView(${c.claim_id});return false" style="color:var(--accent);font-weight:600">เคลม #${c.claim_id}</a>
        <span>ใบสั่งซื้อ #${c.order_id} · ${c.lines} รายการ · ${c.photos > 0 ? c.photos + ' รูป · ' : ''}${esc(String(c.created_at || '').slice(0, 10))}</span>${_bmcChip(c.origin_th || '', c.origin === 'receive' ? 'var(--navy-ink)' : 'var(--text-muted)')} ${_bmcChip(st[0], st[1])}</div>`; }).join('');
    box.innerHTML = `<div class="text-[11px] mb-1" style="color:var(--text-muted)">เลือกใบสั่งซื้อที่รับแล้ว แล้วกด “แจ้งเคลม” ระบุจำนวนและสาเหตุของรายการที่ชำรุด เสีย หรือส่งผิด · ส่งรูปถ่ายสินค้าที่เคลมให้ประสานงานขายทางไลน์</div>`
      + (recent || '<div class="text-xs" style="color:var(--text-muted)">ยังไม่มีใบสั่งซื้อที่บันทึกรับสินค้าแล้ว</div>')
      + (mine ? `<div class="text-xs font-semibold mt-3" style="color:var(--text-primary)">เคลมที่แจ้งไว้</div>${mine}` : '');
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}

async function brnClaimOpen(orderId) {
  const box = document.getElementById('brnClaimForm');
  if (!box) return;
  box.style.display = '';
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</span>';
  try {
    _brnClaimOrd = await _bmcJson(`${API}/api/bom-center/claims/claimable?order_id=${orderId}${_brnQS() ? '&' + _brnQS() : ''}`);
    const o = _brnClaimOrd;
    const lines = o.lines.filter(l => l.remaining_units > 0);
    const field = 'border:1px solid var(--border-control);border-radius:var(--r-sm);background:var(--bg-input);color:var(--text-primary)';
    box.innerHTML = `<div class="text-sm font-semibold" style="color:var(--text-primary)">แจ้งเคลมใบสั่งซื้อ #${o.order_id} · ${esc(_brnRoundLabel(o.round))} · ตัดรอบ ${esc(o.cycle_date)}</div>
      <div class="text-[11px] mt-1" style="color:var(--text-muted)">ใส่จำนวนเฉพาะรายการที่มีปัญหา (เคลมได้ไม่เกินจำนวนที่รับ) และเลือกสาเหตุ · ส่งรูปถ่ายสินค้าที่เคลมให้ประสานงานขายทางไลน์</div>
      <div class="mt-2">${lines.map(l => `<div style="border-top:1px solid var(--border);padding:8px 0">
        <div class="text-sm">${esc(l.name || l.code)} <span class="text-[11px]" style="color:var(--text-muted)">${esc(l.code)} · ส่งจริง ${_bmcNum(l.sent_units)} · รับแล้ว ${_bmcNum(l.received_units)}${l.claimed_units ? ' · เคลมไปแล้ว ' + _bmcNum(l.claimed_units) : ''} · เคลมได้อีก ${_bmcNum(l.remaining_units)} ${l.order_unit === 'base' ? '' : 'แพ็ค'}</span></div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px">
          <input inputmode="decimal" id="brnCq_${esc(l.code)}" placeholder="จำนวนที่เคลม" style="width:110px;height:38px;text-align:center;font-size:16px;${field}">
          <select id="brnCr_${esc(l.code)}" style="flex:1 1 150px;height:38px;font-size:14px;${field}"><option value="">— สาเหตุ —</option>${o.reasons.map(r => `<option value="${r.code}">${esc(r.label)}</option>`).join('')}</select>
          <input id="brnCn_${esc(l.code)}" placeholder="หมายเหตุ (ถ้ามี)" style="flex:1 1 140px;height:38px;font-size:14px;padding:0 8px;${field}"></div></div>`).join('')
        || '<span class="text-xs" style="color:var(--amber-ink)">ทุกรายการของใบสั่งซื้อนี้ถูกเคลมครบแล้ว</span>'}</div>
      <textarea id="brnClaimNote" rows="2" placeholder="ข้อความถึงประสานงานขาย (ถ้ามี)" style="width:100%;margin-top:10px;padding:8px;font-size:14px;${field}"></textarea>
      <div class="flex flex-wrap items-center gap-2 mt-2">
        <button onclick="brnClaimSend()" ${lines.length ? '' : 'disabled'} style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:9px 18px;font-size:14px;cursor:pointer">ส่งแจ้งเคลม</button>
        <button onclick="brnClaimClose()" style="border:1px solid var(--border);border-radius:var(--r-sm);padding:9px 14px;font-size:14px;cursor:pointer;background:transparent;color:var(--text-primary)">ยกเลิก</button>
        <span id="brnClaimStatus" class="text-xs"></span></div>`;
    box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function brnClaimClose() {
  _brnClaimOrd = null;
  const box = document.getElementById('brnClaimForm'); if (box) { box.style.display = 'none'; box.innerHTML = ''; }
}
async function brnClaimSend() {
  const o = _brnClaimOrd;
  if (!o) return;
  const lines = [];
  for (const l of o.lines.filter(x => x.remaining_units > 0)) {
    const qv = (document.getElementById('brnCq_' + l.code) || {}).value;
    if (qv === undefined || String(qv).trim() === '') continue;
    const q = Number(String(qv).replace(',', '.'));
    const reason = document.getElementById('brnCr_' + l.code).value, note = document.getElementById('brnCn_' + l.code).value.trim();
    if (!(q > 0)) { _bmcStatus('brnClaimStatus', `${l.name || l.code}: จำนวนต้องมากกว่า 0`, 'var(--amber-ink)'); return; }
    if (q > l.remaining_units + 1e-9) { _bmcStatus('brnClaimStatus', `${l.name || l.code}: เคลมได้ไม่เกิน ${_bmcNum(l.remaining_units)}`, 'var(--amber-ink)'); return; }
    if (!reason) { _bmcStatus('brnClaimStatus', `${l.name || l.code}: กรุณาเลือกสาเหตุ`, 'var(--amber-ink)'); return; }
    if (reason === 'other' && !note) { _bmcStatus('brnClaimStatus', `${l.name || l.code}: สาเหตุ “อื่น ๆ” ต้องระบุคำอธิบาย`, 'var(--amber-ink)'); return; }
    lines.push({ code: l.code, claim_units: q, reason, note: note || null });
  }
  if (!lines.length) { _bmcStatus('brnClaimStatus', 'กรุณาใส่จำนวนที่เคลมอย่างน้อย 1 รายการ', 'var(--amber-ink)'); return; }
  if (!await uiConfirm(`ส่งแจ้งเคลมใบสั่งซื้อ #${o.order_id} · ${lines.length} รายการ ให้ประสานงานขายพิจารณา?\nกรุณาส่งรูปถ่ายสินค้าที่เคลมทางไลน์`)) return;
  const fd = new FormData();
  fd.append('payload', JSON.stringify({ order_id: o.order_id, lines, note: (document.getElementById('brnClaimNote').value || '').trim() || null }));
  _bmcStatus('brnClaimStatus', 'กำลังส่ง…');
  try {
    const r = await _bmcJson(`${API}/api/bom-center/claims${_brnQS() ? '?' + _brnQS() : ''}`, { method: 'POST', body: fd });
    brnClaimClose();
    _bmcStatus('brnClaimMsg', `✓ ส่งแจ้งเคลมแล้ว (เคลม #${r.claim_id}) กรุณาส่งรูปถ่ายสินค้าที่เคลมให้ประสานงานขายทางไลน์ ผลการพิจารณาจะแสดงที่หน้านี้`, 'var(--good-ink)');
    brnLoadClaims();
  } catch (e) { _bmcStatus('brnClaimStatus', 'ส่งไม่สำเร็จ: ' + String(e.message || e).slice(0, 220), 'var(--bad-ink)'); }
}
async function brnClaimView(id) {
  const box = document.getElementById('brnClaimForm');
  if (!box) return;
  box.style.display = '';
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</span>';
  try {
    const c = await _bmcJson(`${API}/api/bom-center/claims/${id}${_brnQS() ? '?' + _brnQS() : ''}`);
    const st = _BRN_CLAIM_ST[c.status] || [c.status_th, 'var(--text-muted)'];
    box.innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap"><div class="text-sm font-semibold" style="color:var(--text-primary)">เคลม #${c.claim_id} · ใบสั่งซื้อ #${c.order_id} ${_bmcChip(c.origin_th || '', c.origin === 'receive' ? 'var(--navy-ink)' : 'var(--text-muted)')} ${_bmcChip(st[0], st[1])}</div>
        <button onclick="brnClaimClose()" style="border:1px solid var(--border);border-radius:var(--r-sm);padding:4px 12px;font-size:12px;cursor:pointer;background:transparent;color:var(--text-primary)">ปิด</button></div>
      ${c.decision_note ? `<div class="text-xs mt-1" style="color:var(--text-secondary)">ผลการพิจารณา${c.decided_by ? ' โดย ' + esc(c.decided_by) : ''}: ${esc(c.decision_note)}</div>` : ''}
      ${_brnClaimEffect(c)}
      <div style="overflow-x:auto" class="mt-2"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left"><th class="pr-2">สินค้า</th><th class="pr-2 text-right">ส่งจริง</th><th class="pr-2 text-right">รับแล้ว</th><th class="pr-2 text-right">เคลม</th><th class="pr-2 text-right">อนุมัติ</th><th>สาเหตุ</th></tr></thead><tbody>
        ${c.lines.map(l => `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2">${esc(l.name || l.code)}<div class="text-[10.5px]" style="color:var(--text-muted)">${esc(l.code)}</div></td>
          <td class="py-1 pr-2 text-right">${_bmcNum(l.sent_units)}</td><td class="py-1 pr-2 text-right">${_bmcNum(l.received_units)}</td>
          <td class="py-1 pr-2 text-right">${_bmcNum(l.claim_units)}</td><td class="py-1 pr-2 text-right">${l.approved_units == null ? '—' : _bmcNum(l.approved_units)}</td>
          <td class="py-1">${esc(l.reason_th)}${l.note ? ' · ' + esc(l.note) : ''}</td></tr>`).join('')}</tbody></table></div>
      ${c.photos.length ? `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">${c.photos.map(p => `<img src="${API}/api/bom-center/claims/${c.claim_id}/photo/${p.photo_id}" alt="รูปเคลม" loading="lazy" onclick="brnClaimLight(this.src)" style="width:84px;height:84px;object-fit:cover;border-radius:8px;border:1px solid var(--border);cursor:zoom-in">`).join('')}</div>`
        : '<div class="text-xs mt-2" style="color:var(--text-muted)">รูปถ่ายส่งทางไลน์</div>'}
      ${c.status === 'submitted' && _brnCanClaim() ? `<div class="mt-2"><button onclick="brnClaimCancel(${c.claim_id})" style="border:1px solid var(--bad-ink);color:var(--bad-ink);background:transparent;border-radius:var(--r-sm);padding:6px 14px;font-size:13px;cursor:pointer">ยกเลิกเคลมนี้</button> <span id="brnClaimStatus" class="text-xs"></span></div>` : ''}`;
    box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
// what the decision did to the branch's stock, in words (the claim's origin decides)
function _brnClaimEffect(c) {
  const line = (ink, t) => `<div class="text-xs mt-1" style="color:${ink}">${t}</div>`;
  if (c.status === 'approved') {
    if (c.origin === 'receive')
      return line('var(--good-ink)', 'ประสานงานขายอนุมัติแล้ว สินค้าที่รับได้บันทึกไว้ตามจำนวนที่ได้รับจริงอยู่แล้ว จึงไม่มีการปรับสินค้าเพิ่มเติม'
        + (c.adjust_receipt_id ? ' ส่วนที่ไม่อนุมัติ ระบบบันทึกเป็นรับเข้าเพิ่มให้สาขาแล้ว' : ''));
    return line('var(--good-ink)', 'ระบบปรับสินค้าของสาขาแล้ว: หักจำนวนที่อนุมัติออกจากสินค้าที่รับ ผลต่างการใช้จึงไม่รวมของที่เคลมได้');
  }
  if (c.status === 'rejected' && c.origin === 'receive')
    return line('var(--text-secondary)', 'ไม่อนุมัติ ระบบบันทึกจำนวนที่เคลมเป็นรับเข้าเพิ่มให้สาขาแล้ว เนื่องจากถือว่าสาขาได้รับสินค้านั้น');
  return '';
}
async function brnClaimCancel(id) {
  if (!await uiConfirm(`ยกเลิกเคลม #${id}? สาขาแจ้งใหม่ได้ภายหลัง`)) return;
  try {
    await _bmcJson(`${API}/api/bom-center/claims/${id}/cancel${_brnQS() ? '?' + _brnQS() : ''}`, { method: 'POST' });
    brnClaimClose(); brnLoadClaims();
  } catch (e) { _bmcStatus('brnClaimStatus', 'ยกเลิกไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
function brnClaimLight(src) {
  const d = document.createElement('div');
  d.style.cssText = 'position:fixed;inset:0;z-index:9998;background:rgba(0,0,0,.88);display:flex;align-items:center;justify-content:center;cursor:zoom-out';
  d.innerHTML = `<img src="${src}" alt="รูปเคลม" style="max-width:96vw;max-height:94vh;object-fit:contain">`;
  const done = () => { d.remove(); document.removeEventListener('keydown', key); };
  const key = e => { if (e.key === 'Escape') done(); };
  d.onclick = done; document.addEventListener('keydown', key); document.body.appendChild(d);
}
function brnClaimHint(orderId) {   // right after a receipt: a line to claim what arrived wrong
  const el = document.getElementById('brnClaimMsg');
  if (!el || !_brnCanClaim()) return;
  el.style.color = 'var(--text-secondary)';
  el.innerHTML = `รับสินค้าตามใบสั่งซื้อ #${orderId} แล้ว หากพบสินค้าชำรุด เสีย หรือส่งผิดในภายหลัง <a href="#" onclick="brnClaimOpen(${orderId});return false" style="color:var(--accent);font-weight:600;text-decoration:underline">แจ้งเคลม</a> (ส่งรูปถ่ายให้ประสานงานขายทางไลน์)`;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
}
