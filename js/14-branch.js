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
    const nm = document.getElementById('brnBranchName');
    if (isPinnedSession()) {
      // a pinned login: its branch is text, there is no picker and the branch list is never fetched (the server pins every call)
      _brnMe = { scoped: true, branch: pinnedBranchName(), branches: [], zones: [] };
      nm.textContent = _brnMe.branch;
    } else {
      _brnMe = await _bmcJson(`${API}/api/bom-center/stock/me`);
      const sel = document.getElementById('brnBranch');
      if (_brnMe.scoped) {                      // a branch login the session does not know about (cannot happen with auth on)
        if (sel) sel.remove();
        nm.textContent = _brnMe.branch || '';
      } else {
        const keep = sel.value || String(supplyCtx().branch_id || '');   // the module's shared branch
        sel.innerHTML = '<option value="">— เลือกสาขา —</option>' +
          _brnMe.branches.map(b => `<option value="${b.branch_id}">${esc(b.name)}</option>`).join('');
        if (keep && _brnMe.branches.some(b => String(b.branch_id) === keep)) sel.value = keep;
        sel.style.display = '';
        nm.textContent = '';
      }
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
    list.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">เลือกสาขาด้านบน</span>';
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
    document.getElementById('brnKind').innerHTML = _bmcChip(s.next_kind === 'begin' ? 'ครั้งนี้: ต้นงวด (ครั้งแรก)' : 'ครั้งนี้: ปลายงวด',
      s.next_kind === 'begin' ? 'var(--navy-ink)' : 'var(--good-ink)');
    document.getElementById('brnInfo').textContent =
      (s.last_count ? `ตรวจนับล่าสุด ${fmtDay(s.last_count.count_date)} (${s.last_count.kind === 'begin' ? 'ต้นงวด' : 'ปลายงวด'}) โดย ${whoTh(s.last_count.counted_by)}` : 'ยังไม่มีผลตรวจนับ') +
      (s.draft ? ` · มีร่างที่ยังไม่ส่ง (${fmtAt(s.draft.updated_at)})` : '') +
      ` · ${s.items} รายการ (บังคับ ${s.required})`;
    brnRender();
    brnLoadHistory();
  } catch (e) { list.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}

function brnSetZone(z) { _brnZone = z; brnRender(); window.scrollTo({ top: 0 }); }
// an item is counted once it has a number ≥ 0 (a 0 is a count; a word is not)
function _brnCounted(code) {
  const v = _brnQty[code];
  return v !== undefined && v !== '' && Number.isFinite(Number(v)) && Number(v) >= 0;
}

function brnRender() {
  const s = _brnSheet;
  if (!s) return;
  const counted = _brnCounted;
  const all = s.zones.flatMap(z => z.items);
  const req = all.filter(i => i.required);
  document.getElementById('brnProgress').innerHTML =
    `นับแล้ว <b style="color:var(--text-primary)">${all.filter(i => counted(i.code)).length}</b> รายการ · บังคับ <b style="color:${req.every(i => counted(i.code)) ? 'var(--good-ink)' : 'var(--amber-ink)'}">${req.filter(i => counted(i.code)).length}/${req.length}</b>`;
  // a zone chip: the number counted in it, and ✓ once every REQUIRED (บังคับ) item of the zone has a quantity (same rule as ตัดเบิก)
  document.getElementById('brnZones').innerHTML = s.zones.map(z => {
    const done = z.items.filter(i => counted(i.code)).length;
    const zreq = z.items.filter(i => i.required), ok = zreq.length > 0 && zreq.every(i => counted(i.code));
    const on = z.zone === _brnZone;
    return `<button onclick="brnSetZone('${z.zone}')" ${ok ? 'title="โซนนี้ครบ"' : ''} style="border:1px solid ${on ? 'var(--accent)' : 'var(--border)'};border-radius:999px;padding:6px 12px;font-size:13px;cursor:pointer;background:${on ? 'var(--accent)' : 'transparent'};color:${on ? 'var(--on-accent)' : 'var(--text-primary)'}">${esc(z.label)}${done ? ' · ' + done : ''}${ok ? ` <span style="color:${on ? 'inherit' : 'var(--good-ink)'}" aria-label="โซนนี้ครบ">✓</span>` : ''}</button>`;
  }).join('');
  const zone = s.zones.find(z => z.zone === _brnZone) || { items: [] };
  const q = (document.getElementById('brnQ').value || '').trim().toLowerCase();
  const reqOnly = document.getElementById('brnRequiredOnly').checked;
  const items = (q ? all : zone.items).filter(i => (!reqOnly || i.required) && (!q || (i.code + ' ' + i.name).toLowerCase().includes(q)));
  const zoneSel = it => !_brnCanLayout() ? '' : `<select onchange="brnSetLayout(${_bmcQ(it.code)}, this.value)" title="โซนจัดเก็บ" style="font-size:11px;border:1px solid var(--border);border-radius:6px;background:var(--bg-input);color:var(--text-muted);padding:1px 2px">${(_brnMe.zones || []).map(z => `<option value="${z.zone}"${z.zone === (s.zones.find(zz => zz.items.includes(it)) || {}).zone ? ' selected' : ''}>${esc(z.label)}</option>`).join('')}</select>`;
  document.getElementById('brnList').innerHTML = items.length ? items.map(it => {
    const v = _brnQty[it.code] ?? '';
    const last = it.last_qty != null ? `ครั้งก่อน ${_bmcNum(it.last_qty, 2)} ${esc(it.last_unit || it.count_unit || '')}` : '';
    return `<div style="display:flex;flex-wrap:wrap;gap:6px 8px;align-items:center;border-top:1px solid var(--border);padding:8px 0">
      <div class="brn-namecol" style="flex:1 1 220px;min-width:0">
        <div class="text-sm" style="color:var(--text-primary);font-weight:600;overflow-wrap:anywhere">${esc(it.name)}${it.required ? ' ' + _bmcChip('บังคับ', 'var(--amber-ink)') : ''}</div>
        <div class="text-[11px]" style="color:var(--text-muted)">${esc(it.code)}${it.per_count_unit && it.count_unit !== it.base_unit ? ` · 1 ${esc(it.count_unit)} = ${_bmcNum(it.per_count_unit)} ${esc(it.base_unit || '')}` : ''}${last ? ' · ' + last : ''} ${zoneSel(it)}</div>
      </div>
      <div style="display:flex;gap:6px;align-items:center;margin-left:auto">
      <button onclick="brnStep(${_bmcQ(it.code)}, -1)" aria-label="ลด" style="width:36px;height:36px;border:1px solid var(--border);border-radius:10px;background:transparent;color:var(--text-primary);font-size:18px;cursor:pointer">−</button>
      <input inputmode="decimal" value="${esc(v)}" placeholder="${it.last_qty != null ? esc(String(it.last_qty)) : '0'}" oninput="brnSet(${_bmcQ(it.code)}, this.value)"
             style="width:68px;height:36px;text-align:center;font-size:16px;border:1px solid ${v !== '' ? 'var(--accent)' : 'var(--border-control)'};border-radius:10px;background:var(--bg-input);color:var(--text-primary)">
      <button onclick="brnStep(${_bmcQ(it.code)}, 1)" aria-label="เพิ่ม" style="width:36px;height:36px;border:1px solid var(--border);border-radius:10px;background:transparent;color:var(--text-primary);font-size:18px;cursor:pointer">+</button>
      <span class="text-xs brn-unit" style="width:64px;color:var(--text-secondary);overflow-wrap:anywhere">${esc(it.count_unit || it.base_unit || '')}</span>
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
    // owner rule "ทุกรายการที่สั่งต้องนับ": the count is refused while a required (บังคับ) item has no quantity (the server refuses it too)
    const req = _brnSheet.zones.flatMap(z => z.items).filter(i => i.required);
    const missing = req.filter(i => !_brnCounted(i.code));
    if (missing.length) {
      const first = missing[0];
      await uiNotice(`ยังไม่นับรายการบังคับ ${missing.length} รายการ (เช่น ${missing.slice(0, 3).map(i => i.name).join(', ')}) นับให้ครบก่อนส่ง`,
        { title: 'ส่งผลตรวจนับไม่ได้', ok: 'กลับไปนับ' });
      const z = _brnSheet.zones.find(zz => zz.items.some(i => i.code === first.code));      // go to the first one left
      if (z) { document.getElementById('brnQ').value = ''; document.getElementById('brnRequiredOnly').checked = false; _brnZone = z.zone; brnRender(); }
      return;
    }
    const msg = `ส่งผลตรวจนับสิ้นเดือนเป็น${_brnSheet.next_kind === 'begin' ? 'ต้นงวด' : 'ปลายงวด'}?\nส่งแล้วแก้ไม่ได้ ต้องการแก้ให้ตรวจนับใหม่`;
    if (!await uiConfirm(msg, { title: 'ยืนยันการส่งผลตรวจนับ', ok: 'ส่งผลตรวจนับ', cancel: 'กลับไปแก้' })) return;
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
          <div class="text-sm" style="color:var(--text-primary)">ขั้นตอนถัดไป: ยืนยันรับสินค้าครบ และระบุเหตุผลผลต่าง</div>
          <div class="flex flex-wrap gap-2 mt-2"><button onclick="brnConfirmRecvFor(${r.count_id})" style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:8px 14px;font-size:14px;cursor:pointer">ยืนยันรับสินค้าครบ</button>
            <button onclick="brnSetSub('var')" style="border:1px solid var(--border);border-radius:var(--r-sm);padding:8px 14px;font-size:14px;cursor:pointer;background:transparent;color:var(--text-primary)">ดูผลต่างก่อน →</button></div></div>` : '';
      await brnLoadSheet();
    } else {
      _bmcStatus('brnStatus', `✓ บันทึกร่าง ${r.lines} รายการ ${new Date().toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' })}`
        + (r.unconverted && r.unconverted.length ? ` · แปลงหน่วยไม่สำเร็จ ${r.unconverted.length} รายการ` : ''), r.unconverted && r.unconverted.length ? 'var(--amber-ink)' : 'var(--text-muted)');
    }
  } catch (e) { _bmcStatus('brnStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}

async function brnDiscard() {
  if (!await uiConfirm('ล้างร่างผลตรวจนับทั้งหมด?', { title: 'ยืนยันการล้างร่าง', ok: 'ล้างร่าง', cancel: 'ไม่ล้าง' })) return;
  try {
    await _bmcJson(`${API}/api/bom-center/stock/draft?${_brnQS()}`, { method: 'DELETE' });
    await brnLoadSheet();
    _bmcStatus('brnStatus', 'ล้างร่างแล้ว', 'var(--text-muted)');
  } catch (e) { _bmcStatus('brnStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}

async function brnLoadHistory() {
  const box = document.getElementById('brnHistory');
  try {
    const d = await _bmcJson(`${API}/api/bom-center/stock/counts?kind=month&${_brnQS()}`);
    const KIND = { begin: 'ต้นงวด', end: 'ปลายงวด', spot: 'นับก่อนสั่ง (ระบบเดิม)' };
    box.innerHTML = d.counts.length ? d.counts.map(c => `<div style="border-top:1px solid var(--border);padding:4px 0">
        ${esc(fmtDay(c.count_date))} · ${c.status === 'draft' ? _bmcChip('ร่าง', 'var(--amber-ink)') : (KIND[c.kind] || esc(c.kind))} · ${_bmcNum(c.lines, 0)} รายการ · ${esc(whoTh(c.counted_by) === '-' ? '' : whoTh(c.counted_by))}</div>`).join('')
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
let _brnIssLastSig = null;    // the lines last stored (or loaded): the autosave sends nothing when they have not changed
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
const _BRN_ISS_ST = { confirmed: ['ยืนยันแล้ว', 'var(--good-ink)'], late: ['ยืนยันล่าช้า', 'var(--amber-ink)'],
  draft: ['ร่าง', 'var(--amber-ink)'], missing: ['ยังไม่บันทึก', 'var(--bad-ink)'] };
const _BRN_ISS_ST_LONG = { confirmed: 'ยืนยันแล้ว', late: 'ยืนยันล่าช้า', draft: 'ร่าง', missing: 'ยังไม่บันทึก' };

async function brnIssLoad(opts) {
  opts = opts || {};
  document.querySelectorAll('#brn-pane-issue .brn-write').forEach(el => { el.style.display = _brnCanOrder() ? '' : 'none'; });
  if (_brnNeedBranch('brnIssList')) {
    ['brnIssDays', 'brnIssHead', 'brnIssZones', 'brnIssActions', 'brnIssHistory', 'brnIssProgress'].forEach(id => { const el = document.getElementById(id); if (el) el.innerHTML = ''; });
    return;
  }
  if (!opts.quiet) {
    document.getElementById('brnIssList').innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลดรายการ…</span>';
    _bmcStatus('brnIssStatus', '');                       // the previous day's "บันทึกร่างแล้ว" does not belong to this one
  }
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
    _brnIssLastSig = JSON.stringify(_brnIssLines().lines);
    if (!_brnIssZone || !sh.zones.some(z => z.zone === _brnIssZone)) _brnIssZone = (sh.zones[0] || {}).zone || null;
    brnIssRender();
    brnIssHistory();
  } catch (e) { document.getElementById('brnIssList').innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}

// PR-M3 · the day strip, in one row: today, then ONLY the days that still need a slip (amber "ยังไม่ทำ", or "ร่าง"),
// and the days already confirmed folded into one grey chip "ยืนยันครบ N วัน" (it leads to the history list below).
// The day on screen always has its own chip.
function _brnIssDayChips() {
  const box = document.getElementById('brnIssDays');
  if (!box) return;
  const today = _brnIss ? _brnIss.today : '';
  const isOk = d => d.status === 'confirmed' || d.status === 'late';
  const strip = _brnIssStrip.slice().sort((a, b) => (a.date < b.date ? -1 : 1));
  const chip = (d, label, sub, ink) => {
    const on = d.date === _brnIssDate;
    return `<button onclick="brnIssSetDate('${d.date}')" style="flex:0 0 auto;border:1px solid ${on ? 'var(--accent)' : ink};border-radius:12px;padding:3px 10px;font-size:13px;line-height:1.25;cursor:pointer;text-align:center;min-width:72px;background:${on ? 'var(--accent)' : 'transparent'};color:${on ? 'var(--on-accent)' : 'var(--text-primary)'}">
      <span style="display:block">${esc(label)}</span><span style="display:block;font-size:11px;color:${on ? 'inherit' : ink}">${esc(sub)}</span></button>`;
  };
  const sub = (d, past) => d.status === 'missing' ? (past ? ['ยังไม่ทำ', 'var(--amber-ink)'] : ['ยังไม่บันทึก', 'var(--text-muted)'])
    : (_BRN_ISS_ST[d.status] || _BRN_ISS_ST.missing);
  const out = [];
  const t = strip.find(d => d.date === today);
  if (t) { const [s, ink] = sub(t, false); out.push(chip(t, 'วันนี้ ' + _brnIssDm(t.date), s, ink)); }
  strip.filter(d => d.date !== today && !isOk(d)).forEach(d => {      // the days to do, oldest first
    const [s, ink] = sub(d, true);
    out.push(chip(d, _brnIssWd(d.date) + ' ' + _brnIssDm(d.date), s, ink));
  });
  const oldOpen = strip.find(d => d.date === _brnIssDate && d.date !== today && isOk(d));   // a confirmed day opened from the history
  if (oldOpen) { const [s, ink] = sub(oldOpen, true); out.push(chip(oldOpen, _brnIssWd(oldOpen.date) + ' ' + _brnIssDm(oldOpen.date), s, ink)); }
  const nOk = strip.filter(d => d.date !== today && isOk(d)).length;
  if (nOk) out.push(`<button onclick="brnIssToHistory()" style="flex:0 0 auto;border:1px solid var(--border);border-radius:12px;padding:3px 10px;font-size:13px;line-height:1.25;cursor:pointer;text-align:center;min-width:72px;background:var(--bg-hover, transparent);color:var(--text-secondary)">
      <span style="display:block">ยืนยันครบ ${nOk} วัน</span><span style="display:block;font-size:11px;color:var(--text-muted)">ดูประวัติ ↓</span></button>`);
  box.innerHTML = out.join('');
}
function brnIssToHistory() { const el = document.getElementById('brnIssHistory'); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
async function brnIssSetDate(d) {
  if (d === _brnIssDate) return;
  clearTimeout(_brnIssTimer);
  if (_brnIssDirty && _brnIssEditable()) { try { await brnIssSave(false, true); } catch (e) { /* the draft is re-saved on the next edit */ } }
  _brnIssDate = d;
  brnIssLoad();
  window.scrollTo({ top: 0 });
}
function brnIssSetZone(z) { _brnIssZone = z; brnIssRender(); window.scrollTo({ top: 0 }); }

// a quantity box: empty, or a number ≥ 0 (anything else is refused on the spot and never sent)
const _BRN_QTY_OK = /^(\d+\.?\d*|\.\d+)$/;
function _brnIssBadVal(v) { return v !== '' && v != null && !_BRN_QTY_OK.test(String(v)); }
function _brnIssBad(code) { const q = _brnIssQty[code]; return !!q && (_brnIssBadVal(q.used) || _brnIssBadVal(q.waste)); }
const _BRN_QTY_ERR = 'จำนวนต้องเป็นตัวเลข ≥ 0';
function _brnIssAny(code) { const q = _brnIssQty[code]; return !!q && (Number(q.used) > 0 || Number(q.waste) > 0); }
// the border of one box and the note under the row follow what was typed (called on every keystroke)
function _brnIssMark(code) {
  const q = _brnIssQty[code] || {};
  [['used', 'brnIssU_', 'var(--accent)'], ['waste', 'brnIssW_', 'var(--amber-ink)']].forEach(([f, id, ink]) => {
    const el = document.getElementById(id + code);
    if (!el) return;
    const bad = _brnIssBadVal(q[f]);
    el.style.borderColor = bad ? 'var(--bad-ink)' : (q[f] !== '' && q[f] != null ? ink : 'var(--border-control)');
    el.setAttribute('aria-invalid', bad ? 'true' : 'false');
  });
  const e = document.getElementById('brnIssE_' + code);
  if (e) e.textContent = _brnIssBad(code) ? _BRN_QTY_ERR : '';
}
function _brnIssReasonBox(code) {
  const q = _brnIssQty[code] || {};
  if (!(Number(q.waste) > 0)) return '';
  const ed = _brnIssEditable();
  const inp = 'border:1px solid var(--border-control);border-radius:var(--r-sm);padding:4px 6px;font-size:16px;background:var(--bg-input);color:var(--text-primary)';
  const bad = !q.reason || (q.reason === 'other' && !String(q.note || '').trim());
  return `<div class="flex flex-wrap gap-2 mt-1 brn-reason"><select id="brnIssR_${esc(code)}" onchange="brnIssSetField(${_bmcQ(code)}, 'reason', this.value)" ${ed ? '' : 'disabled'}
      style="${inp};border-color:${bad ? 'var(--amber-ink)' : 'var(--border-control)'}"><option value="">— สาเหตุของของเสีย —</option>${((_brnIss && _brnIss.reasons) || []).map(r => `<option value="${esc(r.code)}"${q.reason === r.code ? ' selected' : ''}>${esc(r.label)}</option>`).join('')}</select>
    <input id="brnIssN_${esc(code)}" value="${esc(q.note || '')}" placeholder="${q.reason === 'other' ? 'ระบุคำอธิบาย (จำเป็น)' : 'หมายเหตุ'}" oninput="brnIssSetField(${_bmcQ(code)}, 'note', this.value)" ${ed ? '' : 'disabled'}
      style="flex:1 1 140px;min-width:0;${inp}"></div>`;
}
// "โซนนี้ครบ": the zone has required items (monitored, or class A) and every one of them is filled — a quantity above 0
// in เบิกใช้ or ของเสีย (a 0 is not stored, so it cannot count after a reload)
function _brnIssZoneDone(z) { const req = z.items.filter(i => i.required); return req.length > 0 && req.every(i => _brnIssAny(i.code)); }
function _brnIssZoneChips() {
  const s = _brnIss;
  if (!s) return;
  const searching = !!((document.getElementById('brnIssQ') || {}).value || '').trim();
  document.getElementById('brnIssZones').innerHTML = s.zones.map(z => {
    const n = z.items.filter(i => _brnIssAny(i.code)).length;
    const ok = _brnIssZoneDone(z);
    const on = !searching && z.zone === _brnIssZone;
    return `<button onclick="brnIssSetZone('${z.zone}')" ${ok ? 'title="โซนนี้ครบ"' : ''} style="border:1px solid ${on ? 'var(--accent)' : 'var(--border)'};border-radius:999px;padding:6px 12px;font-size:13px;cursor:pointer;background:${on ? 'var(--accent)' : 'transparent'};color:${on ? 'var(--on-accent)' : 'var(--text-primary)'}">${esc(z.label)}${n ? ' · ' + n : ''}${ok ? ` <span style="color:${on ? 'inherit' : 'var(--good-ink)'}" aria-label="โซนนี้ครบ">✓</span>` : ''}</button>`;
  }).join('');
}
function _brnIssProgress() {
  const s = _brnIss;
  if (!s) return;
  const all = s.zones.flatMap(z => z.items);
  const nWaste = all.filter(i => Number((_brnIssQty[i.code] || {}).waste) > 0).length;
  document.getElementById('brnIssProgress').innerHTML =
    `กรอกแล้ว <b style="color:var(--text-primary)">${all.filter(i => _brnIssAny(i.code)).length}</b> รายการ · ของเสีย <b style="color:${nWaste ? 'var(--amber-ink)' : 'var(--text-primary)'}">${nWaste}</b>`;
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
    `<span style="color:var(--text-primary)">ใบตัดเบิกวันที่ <b>${esc(fmtDay(s.date))}</b></span> ${_bmcChip(_BRN_ISS_ST_LONG[st.status] || stT, stInk)}` +
    (confirmed ? ` <span class="text-xs" style="color:var(--text-secondary)">เวลา ${esc(_brnIssTime(st.confirmed_at))} โดย ${esc(whoTh(st.confirmed_by))}</span>` : '') +
    (s.slip && s.slip.reopen_note && !confirmed ? ` <span class="text-xs" style="color:var(--amber-ink)">เปิดแก้ไขโดย ${esc(whoTh(s.slip.reopened_by))}: ${esc(s.slip.reopen_note)}</span>` : '');
  const parts = [];
  if (s.backdated && !confirmed) parts.push(['var(--amber-ink)', 'ใบย้อนหลังนับเป็นล่าช้า']);
  if (confirmed) parts.push(['var(--text-secondary)', 'ยืนยันแล้ว แก้ไขไม่ได้ · ต้องการแก้ แจ้งประสานงานขาย']);
  const md = s.missing_days || [];
  if (md.length) parts.push(['var(--amber-ink)', `ยังไม่ได้ทำใบตัดเบิก ${md.length} วัน (${md.slice(0, 6).map(_brnIssDm).join(', ')}${md.length > 6 ? ` และอีก ${md.length - 6} วัน` : ''}) · เลือกวันที่ยังไม่ทำจากแถบวันที่`]);
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
  // one box: the caption under it names the box and its unit; an empty box hints at the previous confirmed slip ("เมื่อวาน 3")
  const prevWord = s.prev_date && s.prev_date === new Date(new Date(s.date + 'T12:00:00Z').getTime() - 86400000).toISOString().slice(0, 10) ? 'เมื่อวาน' : 'ครั้งก่อน';
  const box = (it, f, label, ink, v, prev, unit) => {
    const id = (f === 'used' ? 'brnIssU_' : 'brnIssW_') + esc(it.code);
    const bad = _brnIssBadVal(v);
    return `<div class="brn-box"><input id="${id}" class="brn-qty" inputmode="decimal" autocomplete="off" value="${esc(v)}" ${ed ? '' : 'disabled'} aria-label="${label} ${esc(it.name)}"
        placeholder="${prev ? prevWord + ' ' + _bmcNum(prev, 2) : ''}" oninput="brnIssSetField(${_bmcQ(it.code)}, '${f}', this.value)" ${bad ? 'aria-invalid="true"' : ''}
        style="border-color:${bad ? 'var(--bad-ink)' : (v !== '' ? ink : 'var(--border-control)')}"><span class="brn-cap">${label} · ${esc(unit)}</span></div>`;
  };
  document.getElementById('brnIssList').innerHTML = items.length ? items.map(it => {
    const v = _brnIssQty[it.code] || { used: '', waste: '' };
    const unit = it.count_unit || it.base_unit || '';
    const sys = it.system_qty == null ? ''
      : `<span class="brn-sys-sep"> · </span><span class="brn-sys">คงเหลือ <b style="color:${it.system_qty < 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">${_bmcNum(it.system_qty, 2)}</b> ${esc(unit)}</span>`;
    const avg = it.avg_used_7d != null ? `<span class="brn-avg"> · เฉลี่ย 7 วัน ${_bmcNum(it.avg_used_7d, 2)} ${esc(unit)}/วัน</span>` : '';
    return `<div class="brn-row" style="border-top:1px solid var(--border);padding:8px 0">
      <div style="display:flex;gap:8px;align-items:flex-start">
        <div class="brn-namecol" style="flex:1 1 0;min-width:0">
          <div class="brn-name" title="${esc(it.name)}" style="color:var(--text-primary);font-weight:600">${esc(it.name)}</div>
          <div class="brn-sub text-[11px]" style="color:var(--text-muted)">${it.required ? _bmcChip('บังคับ', 'var(--amber-ink)') + ' ' : ''}${esc(it.code)}${sys}${avg}</div></div>
        <div class="brn-boxes">${box(it, 'used', 'เบิกใช้', 'var(--accent)', v.used, it.prev_used, unit)}${box(it, 'waste', 'ของเสีย', 'var(--amber-ink)', v.waste, it.prev_waste, unit)}</div></div>
      <div id="brnIssE_${esc(it.code)}" class="brn-err" role="alert">${_brnIssBad(it.code) ? _BRN_QTY_ERR : ''}</div>
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
  if (field === 'used' || field === 'waste') _brnIssMark(code);       // a wrong value turns red at once, with its note under the row
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
// listed in `incomplete`, and the autosave leaves it out until the reason is chosen. A negative or non-numeric
// quantity is listed in `invalid`: it is never sent
function _brnIssLines() {
  const lines = [], incomplete = [], invalid = [];
  Object.entries(_brnIssQty).forEach(([code, q]) => {
    const it = _brnIssItem(code);
    if (!it) return;
    if (_brnIssBad(code)) { invalid.push(code); return; }
    const used = q.used === '' ? 0 : Number(q.used), waste = q.waste === '' ? 0 : Number(q.waste);
    if (waste > 0 && (!q.reason || (q.reason === 'other' && !String(q.note || '').trim()))) { incomplete.push(code); return; }
    lines.push({ code, qty_used: used, qty_waste: waste, unit: it.count_unit || it.base_unit || null,
      waste_reason: waste > 0 ? q.reason : null, note: String(q.note || '').trim() || null });
  });
  return { lines, incomplete, invalid };
}

async function brnIssSave(submit, quiet) {
  if (!_brnCanOrder()) { if (!quiet) _bmcStatus('brnIssStatus', _BRN_READONLY, 'var(--amber-ink)'); return; }
  if (!_brnIss || !_brnIss.editable) return;
  clearTimeout(_brnIssTimer);
  if (quiet && !_brnIssDirty) return;
  const { lines, incomplete, invalid } = _brnIssLines();
  if ((invalid.length || incomplete.length) && !quiet) {            // a button was pressed: say which row to fix and go to it
    const code = invalid[0] || incomplete[0];
    const it = _brnIssItem(code), q = _brnIssQty[code] || {};
    _bmcStatus('brnIssStatus', invalid.length ? `${_BRN_QTY_ERR}: ${it.name}`
      : q.reason === 'other' ? `ระบุคำอธิบายของสาเหตุ “อื่น ๆ”: ${it.name}` : `เลือกสาเหตุของของเสีย: ${it.name}`,
      invalid.length ? 'var(--bad-ink)' : 'var(--amber-ink)');
    const el = document.getElementById(invalid.length ? 'brnIssU_' + code : 'brnIssR_' + code) || document.getElementById('brnIssW_' + code) || document.getElementById('brnIssU_' + code);
    if (el) { el.scrollIntoView({ block: 'center' }); el.focus(); }
    return;
  }
  // the autosave sends only what is NEW: a wrong value (or a waste still without its reason) changes nothing that can be
  // stored, so no request goes out for it — the status line says which rows are left out and why
  const sig = JSON.stringify(lines);
  if (quiet && (!lines.length || sig === _brnIssLastSig) && (invalid.length || incomplete.length)) {
    _bmcStatus('brnIssStatus', invalid.length ? `ไม่ได้บันทึก ${invalid.length} รายการ · ${_BRN_QTY_ERR}`
      : `ยังไม่ได้บันทึก ${incomplete.length} รายการ · เลือกสาเหตุของของเสีย`, invalid.length ? 'var(--bad-ink)' : 'var(--amber-ink)');
    return;
  }
  const nAny = lines.filter(l => l.qty_used > 0 || l.qty_waste > 0);
  if (submit) {
    const nWaste = nAny.filter(l => l.qty_waste > 0).length;
    const nAll = _brnIss.zones.reduce((a, z) => a + z.items.length, 0);
    const late = _brnIss.backdated && !(_brnIss.status && _brnIss.status.confirm_count > 0);
    if (!await uiConfirm((late ? 'ใบย้อนหลังนับเป็นล่าช้า\n' : '')
        + `ยืนยันใบตัดเบิก ${fmtDayShort(_brnIss.date)} · กรอก ${nAny.length} รายการ · ของเสีย ${nWaste} รายการ · ไม่ได้กรอก ${Math.max(nAll - nAny.length, 0)} รายการ (ถือว่าไม่ได้เบิก)\nยืนยันแล้วแก้ไขไม่ได้ ต้องการแก้ แจ้งประสานงานขาย`,
        { title: 'ยืนยันใบตัดเบิก', ok: 'ยืนยัน', cancel: 'กลับไปแก้' })) return;
  }
  const clock = () => new Date().toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' });
  _bmcStatus('brnIssStatus', 'กำลังบันทึก…', 'var(--text-muted)');
  try {
    const qs = _brnQS();
    const r = await _bmcJson(`${API}/api/bom-center/issues/save${qs ? '?' + qs : ''}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date: _brnIss.date, lines, submit: !!submit }) });
    _brnIssLastSig = sig;
    if (quiet) {                                      // the autosave: the page stays where the person is
      _brnIssDirty = incomplete.length > 0 || invalid.length > 0;
      const d = _brnIssStrip.find(x => x.date === _brnIss.date);
      if (d && d.status === 'missing') { d.status = 'draft'; _brnIssDayChips(); }
      // never "saved" in plain words after a line was left out: the left-out rows are named
      if (invalid.length) _bmcStatus('brnIssStatus', `ไม่ได้บันทึก ${invalid.length} รายการ · ${_BRN_QTY_ERR}`, 'var(--bad-ink)');
      else if (incomplete.length) _bmcStatus('brnIssStatus', `บันทึกร่างแล้ว ${clock()} · ยังไม่ได้บันทึก ${incomplete.length} รายการ (เลือกสาเหตุของของเสีย)`, 'var(--amber-ink)');
      else _bmcStatus('brnIssStatus', `บันทึกร่างแล้ว ${clock()}`, 'var(--good-ink)');
      return;
    }
    _brnIssDirty = false;
    _brnPlan = null;                                  // the order suggestion follows the slip: it is calculated again when that page opens
    await brnIssLoad({ quiet: true });
    _bmcStatus('brnIssStatus', submit ? `✓ ยืนยันใบตัดเบิกแล้ว ${r.lines} รายการ`
      : `✓ บันทึกร่างแล้ว ${clock()} · ${r.lines} รายการ`
        + (r.unconverted && r.unconverted.length ? ` · แปลงหน่วยไม่สำเร็จ ${r.unconverted.length} รายการ` : ''),
      submit ? 'var(--good-ink)' : (r.unconverted && r.unconverted.length ? 'var(--amber-ink)' : 'var(--good-ink)'));
  } catch (e) { _bmcStatus('brnIssStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 220), 'var(--bad-ink)'); }
}

function brnIssActions() {
  const box = document.getElementById('brnIssActions');
  if (!box || !_brnIss) return;
  const btn = (label, on, primary) => `<button onclick="${on}" style="border:${primary ? '0' : '1px solid var(--border)'};border-radius:var(--r-sm);padding:8px 14px;font-size:14px;cursor:pointer;background:${primary ? 'var(--accent)' : 'transparent'};color:${primary ? 'var(--on-accent)' : 'var(--text-primary)'}">${label}</button>`;
  const st = _brnIss.status || {};
  const confirmed = st.status === 'confirmed' || st.status === 'late';
  if (!_brnCanOrder()) box.innerHTML = `<span class="text-xs" style="color:var(--text-muted)">${esc(_BRN_READONLY)}</span>`;
  else if (confirmed) box.innerHTML = _bmcChip(_BRN_ISS_ST_LONG[st.status], st.status === 'late' ? 'var(--amber-ink)' : 'var(--good-ink)')
    + `<span class="text-xs" style="color:var(--text-secondary)">ต้องการแก้ แจ้งประสานงานขายให้เปิดแก้ไข</span>`
    + (_brnIssCanReopen() ? btn('เปิดแก้ไข', 'brnIssReopen()') : '');
  else box.innerHTML = btn('บันทึกร่าง', 'brnIssSave(false)') + btn('ยืนยันใบตัดเบิก', 'brnIssSave(true)', true);
}

// Sales Support (or the analyst) opens a confirmed slip for the branch to edit again; the reason is logged
async function brnIssReopen() {
  const st = _brnIss && _brnIss.status;
  if (!st || !st.slip_id) return;
  const note = await uiPrompt(`เปิดใบตัดเบิกวันที่ ${fmtDay(_brnIss.date)} ให้สาขาแก้ไข\nใบกลับเป็นร่าง สาขาต้องยืนยันใหม่ · เหตุผล (จำเป็น)`, '',
    { title: 'เปิดแก้ไขใบตัดเบิก', okText: 'เปิดแก้ไข', required: true, placeholder: 'เหตุผลในการเปิดแก้ไข' });
  if (note == null) return;
  try {
    await _bmcJson(`${API}/api/bom-center/issues/reopen`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slip_id: st.slip_id, note }) });
    _brnPlan = null;
    await brnIssLoad({ quiet: true });
    _bmcStatus('brnIssStatus', '✓ เปิดแก้ไขแล้ว · สาขาแก้ไขและยืนยันใหม่ได้', 'var(--good-ink)');
  } catch (e) { _bmcStatus('brnIssStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
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
      return `<div style="border-top:1px solid var(--border);padding:4px 0"><a href="#" onclick="brnIssSetDate('${x.date}');return false" style="color:var(--accent);font-weight:600">${esc(fmtDay(x.date))}</a>
        · ${_bmcChip(x.status_th, ink)} · ${_bmcNum(x.lines, 0)} รายการ · ของเสีย ${_bmcNum(x.waste_lines, 0)}
        ${x.confirmed_by && x.status !== 'draft' ? ' · โดย ' + esc(whoTh(x.confirmed_by)) : ''}${x.confirmed_at && x.status !== 'draft' ? ' · เวลา ' + esc(_brnIssTime(x.confirmed_at)) : ''}
        ${x.late && x.status !== 'late' ? ' ' + _bmcChip('ยืนยันล่าช้า', 'var(--amber-ink)') : ''}${reopens ? ' ' + _bmcChip(`เปิดแก้ไข ${reopens} ครั้ง`, 'var(--text-muted)') : ''}</div>`;
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
  _brnVar = null; _brnVarDay = null; _brnRecon = null;
  _brnRecv = [];
  _brnRecvOrd = null;                                    // another branch: its receive and claim forms are not this one's
  { const f = document.getElementById('brnRecvOrder'); if (f) f.style.display = 'none'; }
  if (typeof brnClaimClose === 'function') brnClaimClose();
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
    if (el) el.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">เลือกสาขาด้านบน</span>';
    return true;
  }
  return false;
}

// ── รับของ ───────────────────────────────────────────────────────────────────
function _brnAllItems() { return _brnSheet ? _brnSheet.zones.flatMap(z => z.items) : []; }
// one received-goods note as a card: what it is and the delete button up front, its lines folded away
// (a month's central delivery has ~100 lines; listing them inline buried the button)
const _BRN_RECV_SRC = { central: ['รายงานไดกิโตะ กรุ๊ป', 'var(--navy-ink)'], order: ['ตามใบสั่งซื้อ', 'var(--good-ink)'],
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
    : x.locked ? _bmcChip('อยู่ในงวดที่ปิดแล้ว', 'var(--text-muted)')
    : `<button onclick="brnRecvDelete(${x.receipt_id})" style="border:1px solid var(--bad-ink);color:var(--bad-ink);background:transparent;border-radius:var(--r-sm);padding:4px 12px;font-size:12px;cursor:pointer;white-space:nowrap">ลบใบรับ</button>`;
  return `<div style="border:1px solid var(--border);border-radius:var(--r-md, 12px);padding:10px 12px;margin-bottom:8px;background:var(--bg-card, transparent)">
    <div style="display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap">
      <div style="flex:1 1 200px;min-width:0">
        <div class="text-sm" style="color:var(--text-primary);font-weight:600">${esc(fmtDay(x.receipt_date))} ${_bmcChip(src[0], src[1])}</div>
        <div class="text-xs mt-0.5" style="color:var(--text-secondary)">${x.ref ? esc(x.ref) + ' · ' : ''}<b>${x.lines.length}</b> รายการ${x.entered_by && x.entered_by !== '-' ? ' · บันทึกโดย ' + esc(whoTh(x.entered_by)) : ''}
          ${bad ? ' · ' + _bmcChip(bad + ' รายการแปลงหน่วยไม่ได้', 'var(--bad-ink)') : ''}</div>
        <div class="text-xs mt-1" style="color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${preview}</div>
      </div>
      <div>${del}</div>
    </div>
    <details class="mt-1">
      <summary class="text-xs" style="color:var(--accent);cursor:pointer">ดูรายการ (${x.lines.length})</summary>
      <div style="max-height:320px;overflow:auto;margin-top:6px"><table class="w-full text-xs">
        <thead><tr class="text-left" style="color:var(--text-muted)"><th class="pr-2">วัตถุดิบ</th><th class="pr-2 text-right">จำนวน</th><th>หน่วย</th></tr></thead>
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
  const typed = (document.getElementById('brnRecvItem').value || '').trim();
  const qEl = document.getElementById('brnRecvQty'), raw = String(qEl.value || '').trim().replace(',', '.');
  const q = Number(raw);
  const bad = (el, msg) => { if (el) { el.style.borderColor = 'var(--bad-ink)'; el.focus(); } bmcStatus('brnRecvStatus', 'warn', msg); };
  document.getElementById('brnRecvItem').style.borderColor = qEl.style.borderColor = '';
  // say what is missing: the ingredient, or the quantity (a typed name with no quantity is a missing quantity)
  if (!typed) { bad(document.getElementById('brnRecvItem'), 'เลือกวัตถุดิบจากรายการ'); return; }
  if (raw === '') { bad(qEl, 'ใส่จำนวน'); return; }
  if (!Number.isFinite(q) || !(q > 0)) { bad(qEl, 'จำนวนต้องเป็นตัวเลขที่มากกว่า 0'); return; }
  if (!it) { bad(document.getElementById('brnRecvItem'), `ไม่พบวัตถุดิบ “${typed}” · เลือกจากรายการที่แสดง`); return; }
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
  if (!await uiConfirm(`บันทึกรับสินค้า ${_brnRecv.length} รายการ วันที่ ${fmtDay(document.getElementById('brnRecvDate').value)}?`, { title: 'ยืนยันการบันทึกรับสินค้า', ok: 'บันทึกรับสินค้า', cancel: 'ยกเลิก' })) return;
  try {
    const r = await _bmcJson(`${API}/api/bom-center/stock/receipt?${_brnQS()}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ receipt_date: document.getElementById('brnRecvDate').value, ref: document.getElementById('brnRecvRef').value || null,
        lines: _brnRecv.map(l => ({ code: l.code, qty: l.qty, unit: l.unit })) }) });
    _brnRecv = [];
    document.getElementById('brnRecvRef').value = '';
    _bmcStatus('brnRecvStatus', `✓ บันทึกรับสินค้า ${r.lines} รายการ`, 'var(--good-ink)');
    brnLoadRecv();
  } catch (e) { _bmcStatus('brnRecvStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
async function brnRecvDelete(id) {
  if (!await uiConfirm('ลบบันทึกรับสินค้านี้?', { title: 'ยืนยันการลบใบรับ', ok: 'ลบใบรับ', cancel: 'ไม่ลบ' })) return;
  try {
    await _bmcJson(`${API}/api/bom-center/stock/receipt/${id}?${_brnQS()}`, { method: 'DELETE' });
    brnLoadRecv();
  } catch (e) { _bmcStatus('brnRecvStatus', 'ลบไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}

// ── receiving an order (S8, reworked 2026-10-07; PR-M4 2026-10-10): the orders still to be received are listed on the
// รับสินค้า pane, whatever today's cycle is. The form opens with ONE question — "รับครบตามที่คลังส่งทุกรายการ?":
//   รับครบ            → one line "รับครบ N รายการ" + the acknowledgement + บันทึกรับสินค้า
//   มีรายการไม่ครบ     → search and pick the lines that are short; only those open (รับจริง · ส่วนต่าง · สาเหตุ · หมายเหตุ)
// The server contract is unchanged: every line is sent, a line that was not picked at the quantity the warehouse sent.
let _brnRecvOrd = null;
let _brnRcv = { mode: null, picked: [], vals: {}, q: '' };      // the form's working state: the answer, the picked lines, what was typed
const _brnRcvDismissed = new Set();                              // orders whose form was closed by hand: not opened again by itself
const _BRN_ACK_TERMS = 'ตรวจสภาพและจำนวนครบทุกรายการแล้ว รายการที่ไม่แจ้งเคลมวันนี้ถือว่ารับครบ เคลมภายหลังอาจไม่ได้รับพิจารณา';

async function brnLoadRecvOpen() {
  const box = document.getElementById('brnRecvOpen');
  if (!box) return;
  try {
    const d = await _bmcJson(`${API}/api/bom-center/orders?status=open&limit=20${_brnQS() ? '&' + _brnQS() : ''}`);
    box.style.display = d.orders.length ? '' : 'none';
    // only an order the warehouse has sent (คลังจัดส่งแล้ว) has the receive button; one that is only sent waits for the warehouse
    const row = (x, ready) => `<div style="border-top:1px solid var(--border);padding:8px 0;display:flex;gap:8px;align-items:center">
        <div style="flex:1 1 0;min-width:0"><div class="text-sm" style="color:var(--text-primary);font-weight:600">${esc(_brnRoundLabel(x.round))} · ส่ง ${esc(fmtDay(x.delivery || x.cycle_date))}</div>
          <div class="text-xs" style="color:var(--text-secondary)">${x.lines} รายการ · ${esc(x.value_label || 'มูลค่า')} ฿${_bmcNum(x.value, 0)} ${_bmcChip(x.status_th, ready ? 'var(--navy-ink)' : 'var(--amber-ink)')}${x.late ? ' ' + _bmcChip('ส่งล่าช้า (มีค่าปรับ)', 'var(--bad-ink)') : ''}</div></div>
        ${ready ? `<button class="brn-act" onclick="brnOpenReceive(${x.order_id})">รับสินค้า</button>` : ''}</div>`;
    const sent = d.orders.filter(x => x.status === 'processed'), waiting = d.orders.filter(x => x.status !== 'processed');
    box.innerHTML = d.orders.length
      ? (sent.length ? `<div class="text-xs font-semibold mb-1" style="color:var(--text-primary)">ใบสั่งซื้อที่คลังจัดส่งแล้ว (${sent.length})</div>${sent.map(x => row(x, true)).join('')}` : '')
        + (waiting.length ? `<div class="text-xs font-semibold mt-2 mb-1" style="color:var(--text-muted)">ส่งแล้ว รอคลังจัดส่ง (${waiting.length})</div>${waiting.map(x => row(x, false)).join('')}` : '')
      : '';
    // one order to receive = the page's one job: its form is already open (no scrolling, no extra tap)
    if (sent.length === 1 && !_brnRecvOrd && !_brnRcvDismissed.has(sent[0].order_id)) brnOpenReceive(sent[0].order_id, true);
  } catch (e) { box.style.display = 'none'; }
}
// what the warehouse sent for a line (the centre's processed quantity, else what the branch confirmed) and its unit label
function _brnRcvSent(l) { return Number(l.processed_units ?? l.confirmed_units ?? 0); }
function _brnRcvUnit(l) { return l.order_unit === 'base' ? '' : 'แพ็ค'; }
function _brnRcvLines() { return _brnRecvOrd ? _brnRecvOrd.lines.filter(l => _brnRcvSent(l) > 0 || l.received_units) : []; }
function _brnRcvLine(code) { return _brnRcvLines().find(l => l.code === code); }
function _brnRcvRaw(code) { return String(((_brnRcv.vals[code] || {}).q) ?? '').trim().replace(',', '.'); }
// the "รับจริง" of a line: the quantity typed on a picked line, the quantity sent on any other; null when it is empty or not a number
function _brnRcvTyped(l) {
  if (!_brnRcv.picked.includes(l.code)) return _brnRcvSent(l);
  const raw = _brnRcvRaw(l.code);
  if (raw === '') return null;
  const q = Number(raw);
  return Number.isFinite(q) ? q : null;
}
// the gap of a line = sent − received; below zero it is an over-delivery (a note only, nothing to claim)
function _brnRcvGap(l) {
  const q = _brnRcvTyped(l);
  return q === null ? 0 : Math.round((_brnRcvSent(l) - q) * 1e6) / 1e6;
}

async function brnOpenReceive(id, quiet) {
  const box = document.getElementById('brnRecvOrder');
  if (!box) return;
  try {
    _brnRecvOrd = await _bmcJson(`${API}/api/bom-center/order/${id}`);
    const o = _brnRecvOrd;
    _brnRcv = { mode: null, picked: [], vals: {}, q: '' };
    const today = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    const day = o.delivery && o.delivery <= today ? o.delivery : today;
    const sel = 'border:1px solid var(--border-control);border-radius:var(--r-sm);padding:6px 8px;font-size:16px;background:var(--bg-input);color:var(--text-primary)';
    box.style.display = '';
    box.innerHTML = `<div class="text-sm font-semibold" style="color:var(--text-primary)">รับสินค้า · ${esc(_brnRoundLabel(o.round))} · ส่ง ${esc(fmtDay(o.delivery || o.cycle_date))} ${_bmcChip(o.status_th, 'var(--navy-ink)')}</div>
      <label class="text-xs mt-2 block" style="color:var(--text-muted)">วันที่รับ <input type="date" id="brnRecvOrdDate" value="${day}" style="${sel}"></label>
      <div class="brn-q mt-3">รับครบตามที่คลังส่งทุกรายการ?</div>
      <div class="brn-qbtns mt-2">
        <button type="button" id="brnRcvAll" class="brn-qbtn" aria-pressed="false" onclick="brnRcvMode('all')">รับครบ</button>
        <button type="button" id="brnRcvSome" class="brn-qbtn" aria-pressed="false" onclick="brnRcvMode('some')">มีรายการไม่ครบ</button></div>
      <div id="brnRcvBody" class="mt-2"></div>
      <div id="brnRecvClaimBox" class="mt-2" style="display:none;padding:8px 10px;border:1px solid var(--amber-ink);border-radius:var(--r-sm)">
        <div class="text-xs font-semibold" style="color:var(--text-primary)" id="brnRecvClaimTitle"></div>
        <div class="text-[11px] mt-1" style="color:var(--text-muted)">เคลมส่งถึงประสานงานขายเมื่อบันทึก · ส่งรูปทางไลน์</div>
        <textarea id="brnRecvClaimNote" rows="2" placeholder="ข้อความถึงประสานงานขาย (ถ้ามี)" style="${sel};width:100%;margin-top:6px"></textarea></div>
      <label class="mt-3" style="display:flex;gap:10px;align-items:center;min-height:44px;color:var(--text-primary);cursor:pointer;font-size:15px">
        <input type="checkbox" id="brnRecvAck" onchange="brnRecvAckChanged()" style="width:24px;height:24px;flex:none">
        <span>ตรวจสภาพและจำนวนครบแล้ว</span></label>
      <button type="button" class="brn-link" onclick="brnRcvTerms()">เงื่อนไขการเคลม</button>
      <div id="brnRcvTerms" class="text-xs mt-1" style="display:none;color:var(--text-secondary)">${esc(_BRN_ACK_TERMS)}</div>
      <div class="brn-sticky-bottom brn-rcv-bar" style="position:sticky;bottom:0;z-index:5;margin-top:12px;padding-top:8px;background:var(--bg-card, #fff);border-top:1px solid var(--border)">
        <div class="flex flex-wrap items-center gap-2">
          <button id="brnRecvOrdSave" class="brn-act" onclick="brnReceiveOrder()" disabled>บันทึกรับสินค้า</button>
          <button class="brn-act brn-act-line" onclick="brnRecvClose()">ปิด</button></div>
        <span id="brnRecvWhy" class="brn-why-line" role="status"></span>
        <span id="brnRecvOrdStatus" class="brn-status" role="status"></span></div>`;
    brnRcvRender();
    if (!quiet) box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) { box.style.display = ''; box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function brnRecvClose() {
  if (_brnRecvOrd) _brnRcvDismissed.add(_brnRecvOrd.order_id);
  _brnRecvOrd = null;
  const box = document.getElementById('brnRecvOrder'); if (box) box.style.display = 'none';
}
function brnRcvTerms() { const t = document.getElementById('brnRcvTerms'); if (t) t.style.display = t.style.display === 'none' ? '' : 'none'; }
function brnRcvMode(m) {
  _brnRcv.mode = m;
  if (m === 'all') _brnRcv.picked = [];                    // everything arrived: nothing to adjust
  brnRcvRender();
}
function brnRcvRender() {
  const o = _brnRecvOrd;
  if (!o) return;
  const st = _brnRcv, lines = _brnRcvLines();
  [['brnRcvAll', 'all'], ['brnRcvSome', 'some']].forEach(([id, k]) => { const b = document.getElementById(id); if (b) b.setAttribute('aria-pressed', st.mode === k ? 'true' : 'false'); });
  const body = document.getElementById('brnRcvBody');
  if (!st.mode) body.innerHTML = '';
  else if (st.mode === 'all') {
    body.innerHTML = `<div class="text-sm" style="color:var(--text-primary);font-weight:600">รับครบ ${lines.length} รายการ</div>
      <details class="mt-1"><summary class="text-xs" style="color:var(--accent);cursor:pointer;min-height:32px">ดูรายการที่คลังส่ง</summary>
        <div style="max-height:260px;overflow:auto">${lines.map(l => `<div class="text-xs" style="display:flex;gap:8px;border-top:1px solid var(--border);padding:4px 0"><span style="flex:1 1 0;min-width:0;overflow-wrap:anywhere">${esc(l.name || l.code)}</span><span style="white-space:nowrap;color:var(--text-secondary)">${_bmcNum(_brnRcvSent(l))} ${esc(_brnRcvUnit(l))}</span></div>`).join('')}</div></details>`;
  } else {
    body.innerHTML = `<input id="brnRcvQ" type="search" autocomplete="off" placeholder="ค้นหารายการที่ไม่ครบ" value="${esc(st.q)}" oninput="brnRcvSearch(this.value)"
        style="width:100%;box-sizing:border-box;min-height:44px;border:1px solid var(--border-control);border-radius:var(--r-sm);padding:6px 10px;font-size:16px;background:var(--bg-input);color:var(--text-primary)">
      <div id="brnRcvRes" class="mt-1"></div><div id="brnRcvPicked"></div><div id="brnRcvRest" class="text-sm mt-2" style="color:var(--text-primary)"></div>`;
    brnRcvResults(); brnRcvPickedRender();
  }
  brnRecvDiffAll();
  brnRecvAckChanged();
}
function brnRcvSearch(v) { _brnRcv.q = String(v || ''); brnRcvResults(); }
// the lines not picked yet that match the search: at most 8 (a form of 87 lines is never listed whole)
function brnRcvResults() {
  const box = document.getElementById('brnRcvRes');
  if (!box) return;
  const q = _brnRcv.q.trim().toLowerCase();
  const hits = _brnRcvLines().filter(l => !_brnRcv.picked.includes(l.code) && (!q || ((l.code || '') + ' ' + (l.name || '')).toLowerCase().includes(q)));
  if (!q && hits.length > 8) { box.innerHTML = `<div class="text-xs" style="color:var(--text-muted);padding:4px 2px">พิมพ์ชื่อวัตถุดิบเพื่อเลือกรายการที่ไม่ครบ (ทั้งหมด ${hits.length} รายการ)</div>`; return; }
  box.innerHTML = hits.slice(0, 8).map(l => `<button type="button" class="brn-pick" onclick="brnRcvPick(${_bmcQ(l.code)})"><span style="flex:1 1 0;min-width:0;text-align:left;overflow-wrap:anywhere">${esc(l.name || l.code)}</span><span class="text-xs" style="color:var(--text-secondary);white-space:nowrap">ส่ง ${_bmcNum(_brnRcvSent(l))} ${esc(_brnRcvUnit(l))}</span><span style="color:var(--accent);font-weight:600">เลือก</span></button>`).join('')
    + (hits.length > 8 ? `<div class="text-xs" style="color:var(--text-muted);padding:4px 2px">แสดง 8 จาก ${hits.length} รายการ · พิมพ์ชื่อเพิ่มเพื่อค้นหา</div>`
      : !hits.length && _brnRcv.picked.length < _brnRcvLines().length ? '<div class="text-xs" style="color:var(--text-muted);padding:4px 2px">ไม่พบรายการที่ค้นหา</div>' : '');
}
function brnRcvPick(code) {
  const l = _brnRcvLine(code);
  if (!l || _brnRcv.picked.includes(code)) return;
  _brnRcv.picked.push(code);
  _brnRcv.vals[code] = { q: String(_brnRcvSent(l)), r: 'short', n: '' };
  _brnRcv.q = '';
  const qi = document.getElementById('brnRcvQ'); if (qi) qi.value = '';
  brnRcvResults(); brnRcvPickedRender(); brnRecvDiff(code); brnRecvAckChanged();
  setTimeout(() => { const el = /** @type {HTMLInputElement|null} */ (document.getElementById('brnRcvO_' + code)); if (el) { el.focus(); el.select(); } }, 30);
}
function brnRcvUnpick(code) {
  _brnRcv.picked = _brnRcv.picked.filter(c => c !== code);
  brnRcvResults(); brnRcvPickedRender(); brnRecvDiffAll(); brnRecvAckChanged();
}
// the picked lines, expanded: รับจริง (44 px) · the gap · the reason · a note
function brnRcvPickedRender() {
  const box = document.getElementById('brnRcvPicked'), rest = document.getElementById('brnRcvRest');
  if (!box) return;
  const reasons = (_brnRecvOrd && _brnRecvOrd.claim_reasons) || [];
  const sel = 'border:1px solid var(--border-control);border-radius:var(--r-sm);padding:6px 8px;font-size:16px;background:var(--bg-input);color:var(--text-primary)';
  box.innerHTML = _brnRcv.picked.map(code => {
    const l = _brnRcvLine(code); if (!l) return '';
    const c = esc(code), v = _brnRcv.vals[code] || {}, unit = esc(_brnRcvUnit(l)), cq = _bmcQ(code);
    return `<div class="brn-row" style="border-top:1px solid var(--border);padding:8px 0">
      <div style="display:flex;gap:8px;align-items:flex-start">
        <div class="brn-namecol" style="flex:1 1 0;min-width:0">
          <div class="brn-name" style="color:var(--text-primary);font-weight:600">${esc(l.name || code)}</div>
          <div class="brn-sub text-[11px]" style="color:var(--text-muted)">${c} · ส่งจริง <b style="color:var(--text-primary)">${_bmcNum(_brnRcvSent(l))}</b> ${unit}${l.ss_reason_th ? ' · คลัง: ' + esc(l.ss_reason_th) : ''}</div></div>
        <div class="brn-box"><input id="brnRcvO_${c}" class="brn-qty" inputmode="decimal" autocomplete="off" value="${esc(v.q ?? '')}" aria-label="รับจริง ${esc(l.name || code)}" oninput="brnRcvTyped(${cq}, this.value)"
            style="border-color:var(--good-ink)"><span class="brn-cap">รับจริง · ${unit || 'หน่วยฐาน'}</span></div>
        <button type="button" class="brn-x" aria-label="เอาออก ${esc(l.name || code)}" onclick="brnRcvUnpick(${cq})">✕</button></div>
      <div id="brnRcvE_${c}" class="brn-err" role="alert"></div>
      <span id="brnRcvOver_${c}" class="text-xs" style="display:none;color:var(--navy-ink)"></span>
      <div id="brnRcvD_${c}" style="display:none;margin-top:6px;padding:6px 8px;border-left:3px solid var(--amber-ink);background:var(--bg-input);border-radius:var(--r-sm)">
        <div class="text-xs" style="color:var(--text-primary)">ส่วนต่าง <b id="brnRcvDn_${c}">0</b> ${unit} ที่ยังไม่ได้รับ</div>
        <div class="brn-reason" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:4px">
          <select id="brnRcvR_${c}" aria-label="สาเหตุของส่วนต่าง" onchange="brnRcvField(${cq}, 'r', this.value)" style="${sel};max-width:100%">${reasons.map(r => `<option value="${esc(r.code)}"${(v.r || 'short') === r.code ? ' selected' : ''}>${esc(r.label)}</option>`).join('')}</select>
          <input id="brnRcvN_${c}" value="${esc(v.n || '')}" oninput="brnRcvField(${cq}, 'n', this.value)" placeholder="หมายเหตุ (จำเป็นเมื่อเลือก “อื่น ๆ”)" style="${sel};flex:1 1 200px;min-width:0"></div></div></div>`;
  }).join('');
  if (rest) {
    const n = _brnRcvLines().length - _brnRcv.picked.length;
    rest.innerHTML = `รับครบ ${n} รายการ${_brnRcv.picked.length ? ` <span style="color:var(--text-muted)">· ปรับ ${_brnRcv.picked.length} รายการด้านบน</span>` : ''}`;
  }
}
function brnRcvTyped(code, v) { (_brnRcv.vals[code] || (_brnRcv.vals[code] = {})).q = String(v || ''); brnRecvDiff(code); }
function brnRcvField(code, f, v) { (_brnRcv.vals[code] || (_brnRcv.vals[code] = {}))[f] = String(v || ''); }
function brnRecvDiffAll() { _brnRcv.picked.forEach(brnRecvDiff); brnRecvDiff(null); }
function brnRecvDiff(code) {
  const o = _brnRecvOrd;
  if (!o) return;
  const l = code && o.lines.find(x => x.code === code);
  if (l) {
    const raw = _brnRcvRaw(code), q = _brnRcvTyped(l);
    const bad = raw === '' || q === null || q < 0;                 // empty, a word or a negative number: red at once, the note under the row
    const inp = document.getElementById('brnRcvO_' + code), er = document.getElementById('brnRcvE_' + code);
    if (inp) { inp.style.borderColor = bad ? 'var(--bad-ink)' : 'var(--good-ink)'; inp.setAttribute('aria-invalid', bad ? 'true' : 'false'); }
    if (er) er.textContent = bad ? 'จำนวนต้องเป็นตัวเลข ≥ 0' : '';
    const gap = bad ? 0 : _brnRcvGap(l);
    const d = document.getElementById('brnRcvD_' + code), ov = document.getElementById('brnRcvOver_' + code), n = document.getElementById('brnRcvDn_' + code);
    if (d) d.style.display = gap > 1e-9 ? '' : 'none';
    if (n) n.textContent = _bmcNum(Math.max(gap, 0));
    if (ov) { ov.style.display = gap < -1e-9 ? '' : 'none'; ov.textContent = gap < -1e-9 ? `รับเกินที่ส่ง +${_bmcNum(-gap)}` : ''; }
  }
  const k = _brnRcv.picked.filter(c => { const x = _brnRcvLine(c); return x && _brnRcvGap(x) > 1e-9; }).length;
  const cb = document.getElementById('brnRecvClaimBox'), t = document.getElementById('brnRecvClaimTitle');
  if (cb) cb.style.display = k ? '' : 'none';
  if (t) t.textContent = `รายการที่จะแจ้งเคลมพร้อมการรับสินค้านี้ (${k} รายการ)`;
}
// the save button follows the answer and the tick; whatever keeps it off is written under it
function brnRecvAckChanged() {
  const b = document.getElementById('brnRecvOrdSave'), a = document.getElementById('brnRecvAck'), w = document.getElementById('brnRecvWhy');
  if (!b || !a) return;
  const st = _brnRcv, n = _brnRcvLines().length;
  let why = '';
  if (!st.mode) why = 'เลือก “รับครบ” หรือ “มีรายการไม่ครบ” ก่อน';
  else if (st.mode === 'some' && !st.picked.length) why = 'เลือกรายการที่ไม่ครบอย่างน้อย 1 รายการ หรือกด “รับครบ”';
  else if (!a.checked) why = 'ติ๊ก “ตรวจสภาพและจำนวนครบแล้ว” ก่อนบันทึก';
  b.disabled = !!why;
  if (w) {
    w.style.color = why ? 'var(--amber-ink)' : 'var(--text-secondary)';
    w.textContent = why || (st.mode === 'all' ? `รับครบ ${n} รายการ` : `รับครบ ${n - st.picked.length} รายการ · ปรับ ${st.picked.length} รายการ`);
  }
}
async function brnReceiveOrder() {
  const o = _brnRecvOrd;
  if (!o) return;
  const warn = m => _bmcStatus('brnRecvOrdStatus', m, 'var(--amber-ink)');
  if (!_brnRcv.mode) { warn('เลือก “รับครบ” หรือ “มีรายการไม่ครบ” ก่อน'); return; }
  if (!document.getElementById('brnRecvAck').checked) { warn('ทำเครื่องหมายยืนยันการตรวจสินค้าก่อน'); return; }
  const lines = [];
  let short = 0, over = 0;
  for (const l of _brnRcvLines()) {
    const q = _brnRcvTyped(l), nm = l.name || l.code;
    if (q === null || q < 0) {
      warn(`${nm}: จำนวนต้องเป็นตัวเลขและไม่ติดลบ`);
      const el = document.getElementById('brnRcvO_' + l.code); if (el) { el.scrollIntoView({ block: 'center' }); el.focus(); }
      return;
    }
    const row = { code: l.code, received_units: q };
    const gap = _brnRcvGap(l);
    if (gap > 1e-9) {
      const v = _brnRcv.vals[l.code] || {};
      const reason = v.r || '', note = String(v.n || '').trim();
      if (!reason) { warn(`${nm}: เลือกสาเหตุของส่วนต่าง`); return; }
      if (reason === 'other' && !note) { warn(`${nm}: สาเหตุ “อื่น ๆ” ต้องระบุคำอธิบาย`); return; }
      row.reason = reason; row.note = note || null;
      short++;
    } else if (gap < -1e-9) over++;
    lines.push(row);
  }
  const n = lines.filter(l => l.received_units > 0).length;
  const day = document.getElementById('brnRecvOrdDate').value;
  const claimNote = String((document.getElementById('brnRecvClaimNote') || {}).value || '').trim();
  if (!await uiConfirm(`บันทึกรับสินค้าตามใบสั่งซื้อ #${o.order_id} · ${n} รายการ · วันที่ ${fmtDay(day)}`
      + (_brnRcv.mode === 'all' ? ' · รับครบทุกรายการ' : '')
      + (short ? ` · แจ้งเคลมส่วนต่าง ${short} รายการให้ประสานงานขาย` : '') + (over ? ` · รับเกินที่ส่ง ${over} รายการ` : ''), { title: 'ยืนยันการบันทึกรับสินค้า', ok: 'บันทึกรับสินค้า', cancel: 'กลับไปแก้' })) return;
  try {
    const r = await _bmcJson(`${API}/api/bom-center/order/${o.order_id}/receive${_brnQS() ? '?' + _brnQS() : ''}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lines, receipt_date: day || null, ack: true, claim_note: claimNote || null }) });
    _brnRecvOrd = null;
    document.getElementById('brnRecvOrder').style.display = 'none';
    if (r.claim_id) {
      _bmcStatus('brnRecvStatus', `✓ บันทึกรับสินค้าแล้ว · แจ้งเคลม ${r.claim_lines} รายการให้ประสานงานขายแล้ว`, 'var(--good-ink)');
    } else {
      _bmcStatus('brnRecvStatus', `✓ บันทึกรับสินค้าแล้ว ${n} รายการ`, 'var(--good-ink)');
      brnClaimHint(o.order_id);
    }
    await brnLoadRecv();
    if (r.claim_id) {
      const el = document.getElementById('brnClaimMsg');
      if (el) { el.style.color = 'var(--amber-ink)'; el.textContent = 'ผลพิจารณาแสดงที่นี่'; }
    }
  } catch (e) { _bmcStatus('brnRecvOrdStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
// the end count's next step, offered on the count pane itself
async function brnConfirmRecvFor(endId) {
  if (!await uiConfirm('ยืนยันรับสินค้าครบของงวดนี้?', { title: 'ยืนยันรับสินค้าครบ', ok: 'ยืนยันรับครบ', cancel: 'ยังไม่ยืนยัน' })) return;
  try {
    await _bmcJson(`${API}/api/bom-center/variance/confirm-receipts?${_brnQS()}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ end_count_id: endId }) });
    const nx = document.getElementById('brnNext'); if (nx) nx.innerHTML = '';
    _bmcStatus('brnStatus', '✓ ยืนยันรับสินค้าครบแล้ว', 'var(--good-ink)');
    brnSetSub('var');
  } catch (e) { _bmcStatus('brnStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}

// ── ผลต่างของฉัน ─────────────────────────────────────────────────────────────
// PR-M4. Three tabs: รายงวด (what was used against the recipes) · รายวัน (the day's slip against the recipes) · สิ้นเดือน (the system
// stock against the physical count). Every quantity is read in the unit the kitchen counts in (brnQtyUnit); the period tab asks for a
// reason only on the high-value lines (the server marks them `ask_reason`: class A, or among the top 20 by ฿) — the rest are listed
// under "รายการอื่น" without a form. The renderers of the last two tabs are shared with the centre's pane (isr*, 15-orderhub.js).
const _BRN_VS = { over: ['ใช้เกินสูตร', 'var(--bad-ink)'], under: ['ใช้น้อยกว่าสูตร', 'var(--amber-ink)'],
  negative: ['คงเหลือมากกว่าที่ควรมี', 'var(--bad-ink)'], no_bom: ['มีการใช้แต่ไม่มีในสูตร', 'var(--amber-ink)'],
  not_counted: ['ตรวจนับไม่ครบ', 'var(--text-muted)'], ok: ['ตามสูตร', 'var(--good-ink)'] };
const _BRN_FLAG = ['over', 'under', 'negative', 'no_bom'];

// a quantity (in the item's base unit) in the unit it is counted in: 90 กก. · 12 แพ็ค. `item` = {base_unit, count_unit, per_count_unit}
// (the variance rows carry them). Grams show only when the count unit IS grams. Returns escaped HTML text.
function brnQtyUnit(qBase, item, dec) {
  if (qBase === null || qBase === undefined || qBase === '' || isNaN(qBase)) return '—';
  const it = item || {};
  const f = Number(it.per_count_unit) > 0 ? Number(it.per_count_unit) : 1;
  const unit = String(it.count_unit || it.base_unit || '').replace(/\s*\([^)]*\)/g, '').trim();
  const v = Number(qBase) / f, a = Math.abs(v);
  return `${_bmcNum(v, dec != null ? dec : (a >= 100 ? 0 : a >= 10 ? 1 : 2))} ${esc(unit)}`.trim();
}
function _brnPct(p) { return p == null ? '' : `${p > 0 ? '+' : p < 0 ? '−' : ''}${_bmcNum(Math.abs(p), Math.abs(p) >= 10 ? 0 : 1)}%`; }
function _brnBaht(v) { return v == null ? '—' : `${v < 0 ? '−' : ''}฿${_bmcNum(Math.abs(v), 0)}`; }

let _brnVarTab = 'period';
let _brnVarDay = null;        // /api/bom-center/issue-reports/daily
let _brnRecon = null;         // /api/bom-center/issue-reports/reconcile
let _brnVarSel = new Set();   // the lines ticked for "ใช้เหตุผลเดียวกัน"
let _brnVarShowAll = false;   // the list of other lines also shows the lines inside the threshold
let _brnVarOthersOpen = false;
const _BRN_VT = [['period', 'รายงวด'], ['daily', 'รายวัน'], ['recon', 'สิ้นเดือน']];
function brnVarTab(k, quiet) {
  _brnVarTab = k;
  _BRN_VT.forEach(([p]) => { const el = document.getElementById('brnVar-' + p); if (el) el.style.display = p === k ? '' : 'none'; });
  const bar = document.getElementById('brnVarTabs');
  if (bar) bar.innerHTML = _BRN_VT.map(([p, t]) => {
    const on = p === k;
    return `<button onclick="brnVarTab('${p}')" aria-pressed="${on}" style="flex:1 1 0;min-height:44px;border:1px solid ${on ? 'var(--accent)' : 'var(--border)'};border-radius:999px;padding:5px 12px;font-size:14px;cursor:pointer;background:${on ? 'var(--accent)' : 'transparent'};color:${on ? 'var(--on-accent)' : 'var(--text-primary)'}">${t}</button>`;
  }).join('');
  if (!quiet) brnLoadVarPane();
}
async function brnLoadVarPane() {
  brnVarTab(_brnVarTab, true);
  if (_brnVarTab === 'daily') return brnLoadVarDaily();
  if (_brnVarTab === 'recon') return brnLoadVarRecon();
  return brnLoadVarPeriod();
}
async function brnLoadVarPeriod() {
  const ov = document.getElementById('brnVarOverview');
  if (_brnMe && !_brnMe.scoped && (hasModule('supply.ordering') || hasModule('bom.center') || hasModule('ops.report'))) {
    try {
      const d = await _bmcJson(`${API}/api/bom-center/variance/overview`);
      ov.style.display = '';
      ov.innerHTML = `<div class="text-xs font-semibold mb-1" style="color:var(--text-primary)">ผลต่างงวดล่าสุดของสาขาที่ตรวจนับแล้ว</div>` + (d.branches.length ? d.branches.map(b => `<div style="border-top:1px solid var(--border);padding:4px 0;display:flex;gap:8px;flex-wrap:wrap;align-items:baseline" class="text-xs">
          <a href="#" onclick="brnPickBranch(${b.branch_id});return false" style="color:var(--accent);font-weight:600">${esc(b.branch)}</a>
          ${b.end_date ? `<span>${esc(fmtDay(b.begin_date))} → ${esc(fmtDay(b.end_date))}</span><span>ผลต่าง <b style="color:${(b.gap_value || 0) > 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">฿${_bmcNum(b.gap_value, 0)}</b>${b.gap_pct != null ? ` (${b.gap_pct > 0 ? '+' : ''}${_bmcNum(b.gap_pct, 1)}%)` : ''}</span>
            <span>เกินเกณฑ์ ${b.flagged} · ยังไม่ระบุเหตุผล ${b.needs_reason}</span>${b.complete ? _bmcChip('รับสินค้าครบ', 'var(--good-ink)') : _bmcChip('ยังไม่สมบูรณ์', 'var(--amber-ink)')}`
            : `<span style="color:var(--text-muted)">ตรวจนับแล้ว 1 ครั้ง (${esc(fmtDay(b.last_count || ''))}) · รอตรวจนับปลายงวด</span>`}</div>`).join('') : '<span class="text-xs" style="color:var(--text-muted)">ยังไม่มีสาขาที่ตรวจนับ</span>');
    } catch (e) { ov.style.display = 'none'; }
  } else if (ov) ov.style.display = 'none';
  if (_brnNeedBranch('brnVarList')) { document.getElementById('brnVarHead').innerHTML = ''; return; }
  try {
    const d = await _bmcJson(`${API}/api/bom-center/variance/periods?${_brnQS()}`);
    const sel = document.getElementById('brnVarPeriod');
    sel.innerHTML = d.periods.map(p => `<option value="${p.end_count_id}">${esc(fmtDay(p.begin_date))} → ${esc(fmtDay(p.end_date))} (${p.days} วัน)${p.complete ? '' : ' · ยังไม่สมบูรณ์'}</option>`).join('');
    if (!d.periods.length) {
      document.getElementById('brnVarHead').innerHTML = '';
      document.getElementById('brnVarList').innerHTML = '<span class="text-xs" style="color:var(--text-muted)">ยังเทียบไม่ได้ · ต้องมีผลตรวจนับต้นงวดและปลายงวด</span>';
      return;
    }
    await brnLoadVariance(sel.value);
  } catch (e) { document.getElementById('brnVarList').innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function brnPickBranch(id) {
  const sel = document.getElementById('brnBranch');
  if (!sel) return;                       // a pinned login has no picker (and no other branch to pick)
  sel.value = String(id);
  brnBranchChanged();
}
async function brnLoadVariance(endId) {
  try {
    _brnVar = await _bmcJson(`${API}/api/bom-center/variance/period?${_brnQS()}${endId ? '&end_count_id=' + endId : ''}`);
    brnRenderVariance();
  } catch (e) { document.getElementById('brnVarList').innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}

// "ใช้จริง 90 กก. · ตามสูตร 58 กก. · เกิน 32 กก. (+54%)" — in the unit the kitchen counts in
function _brnVarLine(r) {
  const q = v => brnQtyUnit(v, r);
  if (r.actual == null) return `ตามสูตร <b>${q(r.bom)}</b> · เทียบไม่ได้ เพราะต้นงวดหรือปลายงวดไม่มีผลตรวจนับ`;
  let g = '';
  if (r.gap != null && Math.abs(r.gap) > 1e-9) {
    g = ` · ${r.gap > 0 ? 'เกิน' : 'น้อยกว่า'} <b style="color:${r.gap > 0 ? 'var(--bad-ink)' : 'var(--amber-ink)'}">${q(Math.abs(r.gap))}</b>${r.gap_pct != null ? ` (${_brnPct(r.gap_pct)})` : ''}`;
  }
  return `ใช้จริง <b>${q(r.actual)}</b> · ตามสูตร <b>${q(r.bom)}</b>${g}`;
}
function _brnVarName(r, withSel) {
  const [label, ink] = _BRN_VS[r.status] || [r.status, 'var(--text-muted)'];
  return `${withSel ? `<label class="brn-chk" aria-label="เลือก ${esc(r.name)}"><input type="checkbox" ${_brnVarSel.has(r.code) ? 'checked' : ''} onchange="brnVarSel(${_bmcQ(r.code)}, this.checked)"></label>` : ''}
    <div class="brn-namecol" style="flex:1 1 0;min-width:0"><div class="brn-name" style="color:var(--text-primary);font-weight:600">${esc(r.name)}</div>
      <div class="brn-sub text-[11px]" style="color:var(--text-muted)">${_bmcChip(label, ink)}${r.class === 'A' ? ' ' + _bmcChip('กลุ่มมูลค่าสูง', 'var(--navy-ink)') : ''} ${esc(r.code)}</div></div>
    <div class="text-sm" style="white-space:nowrap">${r.gap_value != null ? `<b style="color:${r.gap_value > 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">${_brnBaht(r.gap_value)}</b>` : ''}</div>`;
}
// one line the branch is asked to explain: the numbers, then the reason (select) + a note + บันทึก; an error stays under THIS line
function _brnVarAskRow(r, reasons) {
  const c = esc(r.code), cq = _bmcQ(r.code);
  const opts = reasons.map(o => `<option value="${esc(o.code)}"${r.reason === o.code ? ' selected' : ''}>${esc(o.label)}</option>`).join('');
  const ed = _brnCanVariance();
  return `<div class="brn-vrow" data-code="${c}" data-open="${r.needs_reason ? 1 : 0}" style="border-top:1px solid var(--border);padding:8px 0">
    <div style="display:flex;gap:6px;align-items:flex-start">${_brnVarName(r, ed)}</div>
    <div class="text-xs mt-1" style="color:var(--text-secondary)">${_brnVarLine(r)}</div>
    ${ed ? `<div class="brn-reason" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:6px">
      <select id="brnRs_${c}" aria-label="เหตุผล ${esc(r.name)}" style="flex:1 1 100%;border:1px solid ${r.needs_reason ? 'var(--amber-ink)' : 'var(--border-control)'};border-radius:var(--r-sm);padding:4px 6px;background:var(--bg-input);color:var(--text-primary)"><option value="">— เหตุผล —</option>${opts}</select>
      <input id="brnRn_${c}" value="${esc(r.reason_note || '')}" placeholder="หมายเหตุ" style="flex:1 1 0;min-width:0;border:1px solid var(--border-control);border-radius:var(--r-sm);padding:4px 6px;background:var(--bg-input);color:var(--text-primary)">
      <button class="brn-act brn-act-line" onclick="brnSaveReason(${cq})">บันทึก</button></div>
      <div id="brnVarE_${c}" class="brn-err" role="alert"></div>
      ${r.reason_by ? `<div class="text-[11px]" style="color:var(--good-ink)">✓ บันทึกเหตุผลแล้ว · ${esc(whoTh(r.reason_by))}</div>` : ''}`
      : (r.reason_th ? `<div class="text-xs mt-1" style="color:var(--text-secondary)">เหตุผล: ${esc(r.reason_th)}${r.reason_note ? ' · ' + esc(r.reason_note) : ''}</div>` : '')}
  </div>`;
}
function _brnVarOtherRow(r) {
  return `<div style="border-top:1px solid var(--border);padding:6px 0">
    <div style="display:flex;gap:6px;align-items:flex-start">${_brnVarName(r, false)}</div>
    <div class="text-xs mt-1" style="color:var(--text-secondary)">${_brnVarLine(r)}${r.reason_th ? ` · เหตุผล: ${esc(r.reason_th)}` : ''}</div></div>`;
}
function brnRenderVariance() {
  const v = _brnVar;
  if (!v) return;
  const pct = v.bom_value_counted > 0 ? 100 * v.gap_value / v.bom_value_counted : null;
  const open = v.totals ? v.totals.ask_reason_open : v.needs_reason;
  const tile = (label, val, ink, sub) => `<div class="brn-v3t"><div class="brn-v3l">${label}</div><div class="brn-v3v" style="color:${ink}">${val}</div>${sub ? `<div class="brn-v3s" style="color:${ink}">${sub}</div>` : ''}</div>`;
  document.getElementById('brnVarHead').innerHTML = `<div style="color:var(--text-secondary)">วันขาย ${v.trading_days} วัน · ราคาเดือน${esc(fmtMonthTh(v.price_month))}</div>
    <div class="brn-v3 mt-2">${tile('ใช้จริง', _brnBaht(v.actual_value), 'var(--text-primary)')}${tile('ตามสูตร', _brnBaht(v.bom_value_counted), 'var(--text-primary)')}${tile('ผลต่าง', _brnBaht(v.gap_value), v.gap_value > 0 ? 'var(--bad-ink)' : 'var(--text-primary)', pct == null ? '' : _brnPct(pct))}</div>
    <div class="flex flex-wrap items-center gap-2 mt-2">
      ${open ? `<button class="brn-chipbtn" onclick="brnVarJump()">ต้องให้เหตุผล ${open} รายการ</button>` : _bmcChip('✓ ให้เหตุผลครบแล้ว', 'var(--good-ink)')}
      ${v.complete ? `${_bmcChip('รับสินค้าครบแล้ว', 'var(--good-ink)')} <span class="text-xs" style="color:var(--text-muted)">โดย ${esc(whoTh(v.receipts_confirmed_by))}</span> <button class="brn-link" onclick="brnConfirmRecv(true)">ยกเลิกการยืนยัน</button>`
        : `${_bmcChip('ยังไม่ยืนยันรับสินค้าครบ', 'var(--amber-ink)')} <button class="brn-act brn-act-line" onclick="brnConfirmRecv(false)">ยืนยันรับสินค้าครบ</button>`}</div>`;
  const ask = v.rows.filter(r => r.ask_reason).sort((a, b) => (Number(b.needs_reason) - Number(a.needs_reason)) || (Math.abs(b.gap_value || 0) - Math.abs(a.gap_value || 0)));
  const flaggedOther = v.rows.filter(r => _BRN_FLAG.includes(r.status) && !r.ask_reason);
  const others = _brnVarShowAll ? v.rows.filter(r => !r.ask_reason) : flaggedOther;
  _brnVarSel = new Set([..._brnVarSel].filter(c => ask.some(r => r.code === c)));
  const multi = ask.length >= 2 && _brnCanVariance() ? `<div class="text-xs" style="color:var(--text-muted)">ติ๊กหน้ารายการหลายรายการ เพื่อใช้เหตุผลเดียวกัน</div><div class="brn-multi" id="brnVarMulti" style="display:none">
      <div class="text-xs" style="color:var(--text-secondary)"><span id="brnVarSelN">เลือก 0 รายการ</span></div>
      <div class="brn-reason" style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px">
        <select id="brnVarMultiR" aria-label="เหตุผลที่ใช้กับรายการที่เลือก" style="flex:1 1 150px;border:1px solid var(--border-control);border-radius:var(--r-sm);padding:4px 6px;background:var(--bg-input);color:var(--text-primary)"><option value="">— เหตุผล —</option>${v.reasons.map(o => `<option value="${esc(o.code)}">${esc(o.label)}</option>`).join('')}</select>
        <input id="brnVarMultiN" placeholder="หมายเหตุ (จำเป็นเมื่อเลือก “อื่น ๆ”)" style="flex:1 1 120px;min-width:0;border:1px solid var(--border-control);border-radius:var(--r-sm);padding:4px 6px;background:var(--bg-input);color:var(--text-primary)">
        <button id="brnVarMultiBtn" class="brn-act" onclick="brnVarMultiSave()" disabled>ใช้เหตุผลเดียวกันกับที่เลือก</button></div>
      <div id="brnVarMultiE" class="brn-err" role="alert"></div></div>` : '';
  document.getElementById('brnVarList').innerHTML = (ask.length ? multi + ask.map(r => _brnVarAskRow(r, v.reasons)).join('')
      : '<div class="text-sm" style="color:var(--good-ink)">✓ ไม่มีรายการที่ต้องให้เหตุผล</div>')
    + `<details id="brnVarOthers" class="brn-others mt-2" ${_brnVarOthersOpen ? 'open' : ''} ontoggle="_brnVarOthersOpen = this.open">
        <summary>รายการอื่น ${flaggedOther.length} รายการ</summary>
        <label class="text-xs" style="display:flex;align-items:center;gap:8px;min-height:36px;color:var(--text-muted)"><input type="checkbox" id="brnVarAll" ${_brnVarShowAll ? 'checked' : ''} onchange="_brnVarShowAll = this.checked; brnRenderVariance()"> แสดงรายการที่อยู่ในเกณฑ์ด้วย</label>
        ${others.slice(0, 200).map(_brnVarOtherRow).join('') || '<div class="text-xs" style="color:var(--text-muted)">ไม่มีรายการ</div>'}</details>`;
  brnVarMultiBar();
}
function brnVarSel(code, on) { if (on) _brnVarSel.add(code); else _brnVarSel.delete(code); brnVarMultiBar(); }
function brnVarMultiBar() {
  const n = document.getElementById('brnVarSelN'), b = document.getElementById('brnVarMultiBtn'), m = document.getElementById('brnVarMulti');
  if (m) m.style.display = _brnVarSel.size ? '' : 'none';
  if (n) n.textContent = `เลือก ${_brnVarSel.size} รายการ`;
  if (b) b.disabled = !_brnVarSel.size;
}
function brnVarJump() {
  const el = document.querySelector('#brnVarList .brn-vrow[data-open="1"]');
  if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'center' }); const s = el.querySelector('select'); if (s) s.focus({ preventScroll: true }); }
}
async function _brnPostReason(endId, code, reason, note) {
  await _bmcJson(`${API}/api/bom-center/variance/reason?${_brnQS()}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ end_count_id: endId, code, reason, note }) });
}
// the same reason for every ticked line: one select, one save; a line the server refuses keeps its tick and its message under it
async function brnVarMultiSave() {
  const e = document.getElementById('brnVarMultiE');
  const say = m => { if (e) e.textContent = m || ''; };
  if (!_brnCanVariance()) { say(_BRN_READONLY); return; }
  const reason = document.getElementById('brnVarMultiR').value, note = document.getElementById('brnVarMultiN').value.trim();
  const codes = [..._brnVarSel];
  if (!codes.length) { say('ติ๊กรายการที่ต้องการก่อน'); return; }
  if (!reason) { say('เลือกเหตุผลก่อนกดบันทึก'); return; }
  if (reason === 'other' && !note) { say('เหตุผล “อื่น ๆ” ต้องระบุคำอธิบาย'); return; }
  say('');
  const errs = {}, endId = _brnVar.end_count_id;
  for (const code of codes) {
    try { await _brnPostReason(endId, code, reason, note); _brnVarSel.delete(code); }
    catch (err) { errs[code] = 'ไม่สำเร็จ: ' + String(err.message || err).slice(0, 160); }
  }
  await brnLoadVariance(endId);
  Object.entries(errs).forEach(([c, m]) => { const el = document.getElementById('brnVarE_' + c); if (el) el.textContent = m; });
  const bad = Object.keys(errs).length;
  bmcStatus('brnVarStatus', bad ? 'warn' : true, bad ? `บันทึกเหตุผล ${codes.length - bad} รายการแล้ว · ไม่สำเร็จ ${bad} รายการ (ดูข้อความใต้รายการ)` : `บันทึกเหตุผล ${codes.length} รายการแล้ว`);
}
async function brnSaveReason(code, recon) {      // recon: the month-end tab (its own ids and period)
  const errId = (recon ? 'brnRcE_' : 'brnVarE_') + code;
  const say = m => { const el = document.getElementById(errId); if (el) el.textContent = m || ''; };
  say('');
  if (!_brnCanVariance()) { say(_BRN_READONLY); return; }
  const reason = document.getElementById((recon ? 'brnRcRs_' : 'brnRs_') + code).value;
  const note = document.getElementById((recon ? 'brnRcRn_' : 'brnRn_') + code).value;
  if (!reason) { say('เลือกเหตุผลก่อนกดบันทึก'); return; }
  if (reason === 'other' && !String(note).trim()) { say('เหตุผล “อื่น ๆ” ต้องระบุคำอธิบาย'); return; }
  const endId = recon ? _brnRecon.period.end_count_id : _brnVar.end_count_id;
  try {
    await _brnPostReason(endId, code, reason, note);
    if (recon) await brnLoadVarRecon(endId); else await brnLoadVariance(endId);
    bmcStatus(recon ? 'brnRcStatus' : 'brnVarStatus', true, 'บันทึกเหตุผลแล้ว');
  } catch (e) { say('ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200)); }
}

// ── รายวัน: ใบตัดเบิก เทียบสูตร — one card per day; a tap opens the day's items (3 columns) ─────────────
function brnDailyCards(d, openFn) {
  if (!d.rows.length) return '<span class="text-xs" style="color:var(--text-muted)">ยังไม่มียอดขายหรือใบตัดเบิกในเดือนนี้</span>';
  const t = d.totals;
  const head = `<div class="text-xs" style="color:var(--text-secondary);margin-bottom:6px">รวมวันที่ยืนยันแล้ว · ยอดเบิก <b>${_brnBaht(t.issued_value)}</b> · ตามสูตร <b>${_brnBaht(t.bom_value)}</b> · ผลต่าง <b style="color:${t.var_value > 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">${_brnPct(t.var_pct) || '—'}</b></div>`;
  return head + d.rows.slice().reverse().map(r => {
    const ink = _ISR_SLIP_INK[r.slip_status] || 'var(--text-muted)';
    const vink = r.var_pct != null && r.var_pct > 0 ? 'var(--bad-ink)' : 'var(--text-primary)';
    const bom = r.trading ? _brnBaht(r.bom_value) : '—';
    return `<button type="button" class="brn-daycard" onclick="${openFn}('${r.date}')" style="border-left-color:${ink}">
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><b style="color:var(--text-primary)">${esc(fmtDayShort(r.date))}</b> ${_bmcChip(r.slip_status_th, ink)}${r.flagged ? ' ' + _bmcChip('เบิกเกินต่อเนื่อง', 'var(--bad-ink)') : ''}</div>
      <div class="text-xs" style="color:var(--text-secondary);margin-top:2px">${r.confirmed
        ? `ยอดเบิก <b>${_brnBaht(r.issued_value)}</b> · ตามสูตร <b>${bom}</b> · ผลต่าง <b style="color:${vink}">${_brnPct(r.var_pct) || '—'}</b>`
        : `ยังไม่มียอดเบิกให้เทียบ · ตามสูตร <b>${bom}</b>`}</div></button>`;
  }).join('');
}
// one day's items in three columns: item · issued / by the recipes (count units) · the gap %
function brnDayItemsHtml(d, closeJs) {
  const s = d.slip, conf = s.key === 'on_time' || s.key === 'late';
  const head = `<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:baseline">
      <b class="text-sm" style="color:var(--text-primary)">${esc(fmtDayShort(d.date))}</b>
      ${_bmcChip(s.status_th, _ISR_SLIP_INK[s.key] || 'var(--text-muted)')}
      ${conf ? `<span class="text-xs" style="color:var(--text-secondary)">ยืนยันเวลา ${esc(_brnIssTime(s.confirmed_at))} โดย ${esc(whoTh(s.confirmed_by))}</span>` : ''}
      ${closeJs ? `<button onclick="${closeJs}" class="brn-link" style="margin-left:auto">ปิด</button>` : ''}</div>`;
  const note = conf ? '' : '<div class="text-xs mt-2" style="color:var(--amber-ink)">ใบตัดเบิกวันนั้นยังไม่ยืนยัน · แสดงเฉพาะปริมาณตามสูตร</div>';
  const flags = d.flags.length ? `<div class="flex flex-wrap gap-1 mt-2">${d.flags.map(f => _bmcChip(`${f.name} เบิกเกินสูตร ${f.days} วันติดต่อกัน`, 'var(--bad-ink)')).join(' ')}</div>` : '';
  const rows = d.rows.map(r => {
    const ink = r.var_pct != null && r.var_pct > 0 ? 'var(--bad-ink)' : 'var(--text-primary)';
    return `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2"><b>${esc(r.name)}</b>${r.signal ? ' ' + _bmcChip(r.signal_th, r.signal === 'streak' ? 'var(--bad-ink)' : 'var(--amber-ink)') : ''}
        <div class="text-[10.5px]" style="color:var(--text-muted)">${esc(r.code)}${r.waste_reason_th ? ' · ของเสีย: ' + esc(r.waste_reason_th) + (r.note ? ' (' + esc(r.note) + ')' : '') : ''}</div></td>
      <td class="py-1 pr-2 text-right">${r.issued == null ? '—' : brnQtyUnit(r.issued, r)}<div class="text-[10.5px]" style="color:var(--text-muted)">/ ${brnQtyUnit(r.bom, r)}</div></td>
      <td class="py-1 text-right" style="color:${ink};white-space:nowrap">${_brnPct(r.var_pct) || '—'}</td></tr>`;
  }).join('');
  return head + note + flags + (d.rows.length ? `<div style="overflow-x:auto" class="mt-2"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left">
      <th class="py-1 pr-2">รายการ</th><th class="py-1 pr-2 text-right">เบิก / ตามสูตร</th><th class="py-1 text-right">ผลต่าง</th></tr></thead><tbody>${rows}</tbody></table></div>`
    : '<div class="text-xs mt-2" style="color:var(--text-muted)">ไม่มีรายการ</div>');
}
// PR-M5: the month of the daily variance is the module's month (supplyCtx), this month until somebody chooses
function brnVarDayMonthChanged() { supplyCtxSet({ month: document.getElementById('brnVarDayMonth').value }); brnLoadVarDaily(); }
async function brnLoadVarDaily() {
  const m = document.getElementById('brnVarDayMonth');
  supplyMonthPick(m, false);
  document.getElementById('brnVarDayDetail').style.display = 'none';
  const chips = document.getElementById('brnVarDayChips');
  if (_brnNeedBranch('brnVarDayList')) { chips.innerHTML = ''; return; }
  document.getElementById('brnVarDayList').innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังคำนวณ…</span>';
  try {
    _brnVarDay = await _bmcJson(`${API}/api/bom-center/issue-reports/daily?${_brnQS()}&month=${m.value}`);
    chips.innerHTML = isrFlagChips(_brnVarDay);
    document.getElementById('brnVarDayList').innerHTML = brnDailyCards(_brnVarDay, 'brnVarDayOpen');
  } catch (e) { document.getElementById('brnVarDayList').innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
async function brnVarDayOpen(day) {
  const box = document.getElementById('brnVarDayDetail');
  box.style.display = '';
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</span>';
  try {
    const d = await _bmcJson(`${API}/api/bom-center/issue-reports/daily/day?${_brnQS()}&day=${day}`);
    box.innerHTML = brnDayItemsHtml(d, "document.getElementById('brnVarDayDetail').style.display='none'");
    box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function brnVarDailyXlsx() {
  if (_brnNeedBranch('brnVarDayList')) return;
  bmcDownload(`${API}/api/bom-center/issue-reports/daily.xlsx?${_brnQS()}&month=${document.getElementById('brnVarDayMonth').value}`, 'ผลต่างตัดเบิกรายวัน.xlsx', 'brnVarDayStatus');
}

// ── สิ้นเดือน: คงเหลือตามระบบ เทียบ นับจริง (the shared renderer, in count units) ─────────────────────────
async function brnLoadVarRecon(endId) {
  if (_brnNeedBranch('brnRcList')) { document.getElementById('brnRcHead').innerHTML = ''; return; }
  const sel = document.getElementById('brnRcPeriod');
  try {
    if (!endId) {
      const d = await _bmcJson(`${API}/api/bom-center/variance/periods?${_brnQS()}`);
      sel.innerHTML = d.periods.map(p => `<option value="${p.end_count_id}">${esc(fmtDay(p.begin_date))} → ${esc(fmtDay(p.end_date))} (${p.days} วัน)</option>`).join('');
      if (!d.periods.length) {
        document.getElementById('brnRcHead').innerHTML = '';
        document.getElementById('brnRcList').innerHTML = '<span class="text-xs" style="color:var(--text-muted)">ยังเทียบไม่ได้ · ต้องมีผลตรวจนับต้นงวดและปลายงวด</span>';
        return;
      }
      endId = sel.value;
    }
    _brnRecon = await _bmcJson(`${API}/api/bom-center/issue-reports/reconcile?${_brnQS()}&end_count_id=${endId}`);
    brnRenderVarRecon();
  } catch (e) { document.getElementById('brnRcList').innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function brnRenderVarRecon() {
  const r = _brnRecon;
  if (!r) return;
  document.getElementById('brnRcHead').innerHTML = isrReconHead(r);
  document.getElementById('brnRcList').innerHTML = isrReconTable(r, { all: document.getElementById('brnRcAll').checked, edit: _brnCanVariance(),
    pfx: 'brnRc', save: 'brnSaveReason', qty: (v, x) => brnQtyUnit(v, x) });
}
function brnVarReconXlsx() {
  if (!_brnRecon) return;
  bmcDownload(`${API}/api/bom-center/issue-reports/reconcile.xlsx?${_brnQS()}&end_count_id=${_brnRecon.period.end_count_id}`, 'คงเหลือตามระบบเทียบนับจริง.xlsx', 'brnRcStatus');
}
async function brnConfirmRecv(undo) {
  if (!_brnCanVariance()) { _bmcStatus('brnVarStatus', _BRN_READONLY, 'var(--amber-ink)'); return; }
  if (!undo && !await uiConfirm('ยืนยันรับสินค้าครบของงวดนี้?', { title: 'ยืนยันรับสินค้าครบ', ok: 'ยืนยันรับครบ', cancel: 'ยังไม่ยืนยัน' })) return;
  try {
    await _bmcJson(`${API}/api/bom-center/variance/confirm-receipts?${_brnQS()}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ end_count_id: _brnVar.end_count_id, undo: !!undo }) });
    await brnLoadVariance(_brnVar.end_count_id);
    _bmcStatus('brnVarStatus', undo ? 'ยกเลิกการยืนยันแล้ว' : '✓ ยืนยันรับสินค้าครบแล้ว', 'var(--good-ink)');
  } catch (e) { _bmcStatus('brnVarStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
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

// PR-M3 · the coming deliveries, ONE line each: "รอบ 2 · ส่ง ส. 10/10 · สั่งภายใน พฤ. 13:15" and a state chip
// (เปิดรับ · เลยเวลา · สั่งแล้ว). Rounds still open first, those past their cutoff after them (they stay selectable:
// a late order is sent with the fee).
let _brnOrdSent = [];         // the branch's recent orders (brnLoadOrderHistory): which deliveries are already ordered
function _brnDelivState(d) {
  const sent = _brnOrdSent.some(x => x.round === d.round && x.cycle_date === d.cycle_date && ['submitted', 'processed', 'received'].includes(x.status))
    || (d.round === _brnRound && d.delivery === _brnDelivery && ['submitted', 'processed', 'received'].includes((((_brnPlan || {}).order) || {}).status));
  return sent ? ['สั่งแล้ว', 'var(--good-ink)'] : d.late ? ['เลยเวลา', 'var(--bad-ink)'] : ['เปิดรับ', 'var(--navy-ink)'];
}
function _brnDelivChips() {
  const box = document.getElementById('brnOrdDeliv');
  if (!box) return;
  const items = [...((_brnPlan && _brnPlan.deliveries) || [])].sort((a, b) => (a.late ? 1 : 0) - (b.late ? 1 : 0));
  box.innerHTML = items.map(d => {
    const on = d.round === _brnRound && d.delivery === _brnDelivery;
    const [st, ink] = _brnDelivState(d);
    const cut = String(d.cutoff_at || '');
    return `<button onclick="brnSetDelivery('${d.round}','${d.delivery}')" style="display:flex;flex-wrap:wrap;gap:2px 8px;align-items:center;justify-content:space-between;border:1px solid ${on ? 'var(--accent)' : 'var(--border)'};border-radius:12px;padding:6px 12px;min-height:40px;font-size:13px;cursor:pointer;text-align:left;background:${on ? 'var(--accent)' : 'transparent'};color:${on ? 'var(--on-accent)' : 'var(--text-primary)'}">
      <span>${esc(d.round_th)} · ส่ง ${esc(fmtDayShort(d.delivery))} · สั่งภายใน ${esc(_brnIssWd(cut.slice(0, 10)))} ${esc(cut.slice(11, 16))}</span>
      <span style="display:inline-block;padding:1px 8px;border-radius:999px;border:1px solid ${on ? 'currentColor' : 'var(--border)'};font-size:11px;white-space:nowrap;color:${on ? 'inherit' : ink}">${st}</span></button>`;
  }).join('');
}
let _brnOrdWhy = new Set();   // the rows whose "ดูที่มา" is open
function brnOrdWhy(code) { if (_brnOrdWhy.has(code)) _brnOrdWhy.delete(code); else _brnOrdWhy.add(code); brnRenderOrder(); }
let _brnBufOpen = false;      // "ยอดขายรอบนี้ต่างจากปกติ?" is folded until asked for
function brnToggleBuffer() { _brnBufOpen = !_brnBufOpen; _brnRenderBuffer(_brnPlan, true); }
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
  box.innerHTML = b('central', `สั่งจากไดกิโตะ (${p.rows.filter(r => r.order_units > 0).length})`) + b('local', `ซื้อเอง (${nLocal})`);
}
// a base quantity in the unit the branch counts in (กก. · แพ็ค …), whole numbers when it is large
function _brnInCount(r, v) { return _bmcNum(Number(v || 0) / (r.per_count_unit || 1), 1); }
function _brnCountUnit(r) { return r.count_unit || r.base_unit || ''; }
function _brnRenderLocal(p) {
  const q = (document.getElementById('brnOrdQ').value || '').trim().toLowerCase();
  const only = document.getElementById('brnOrdOnly').checked;
  const rows = (p.local_rows || []).filter(r => (!only || r.order_units > 0) && (!q || (r.code + ' ' + r.name).toLowerCase().includes(q)));
  const analyst = hasModule('bom.center');                 // a missing sub-recipe is the analyst's work, not the branch's
  document.getElementById('brnOrdList').innerHTML = `<div class="text-xs mb-1" style="color:var(--text-muted)">วัตถุดิบที่ไดกิโตะไม่ได้จัดส่ง สาขาซื้อเอง · ไม่ส่งเข้าใบสั่งซื้อ</div>` +
    (rows.map(r => `<div style="border-top:1px solid var(--border);padding:8px 0;display:flex;flex-wrap:wrap;gap:6px 8px;align-items:baseline">
      <div style="flex:1 1 220px;min-width:0"><div class="text-sm" style="color:var(--text-primary);font-weight:600">${esc(r.name)}
        ${analyst && r.kind === 'prepared' ? _bmcChip('กึ่งสำเร็จ: ยังไม่มีสูตร', 'var(--amber-ink)') : ''}</div>
        <div class="text-[11px]" style="color:var(--text-muted)">${esc(r.code)} · ต้องใช้ ${_brnInCount(r, r.need)} ${esc(_brnCountUnit(r))}</div></div>
      <div class="text-sm" style="margin-left:auto;color:var(--text-primary)">แนะนำ <b>${_bmcNum(r.order_units, 0)}</b> ${esc(_brnUnitLabel(r))}</div></div>`).join('')
      || '<span class="text-xs" style="color:var(--good-ink)">✓ ไม่มีรายการที่ต้องซื้อเอง</span>');
  document.getElementById('brnOrdActions').innerHTML = '';
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
    ? `${_bmcChip('หักคงเหลือตามระบบแล้ว', 'var(--good-ink)')}${slipOk ? ` · ใบตัดเบิกวันนี้: ยืนยันแล้ว เวลา ${esc(_brnIssTime(slip.confirmed_at))}` : ''}`
    : `${_bmcChip('ไม่มีผลตรวจนับสิ้นเดือน', 'var(--text-muted)')} ยังไม่มีผลตรวจนับสิ้นเดือน ยอดแนะนำจึงไม่หักคงเหลือ${slipOk ? ` · ใบตัดเบิกวันนี้: ยืนยันแล้ว เวลา ${esc(_brnIssTime(slip.confirmed_at))}` : ''}`);
  // the one thing that blocks sending is an action card of its own above the list (not a line inside the header)
  const gate = document.getElementById('brnOrdGate');
  if (gate) {
    const show = !slipOk && editable;
    gate.style.display = show ? '' : 'none';
    gate.innerHTML = show ? `<div class="flex flex-wrap items-center gap-3" style="border-left:4px solid var(--amber-ink);padding-left:10px">
        <div style="flex:1 1 220px;min-width:0"><div class="text-sm font-semibold" style="color:var(--text-primary)">ต้องยืนยันใบตัดเบิกวันนี้ก่อนส่งใบสั่งซื้อ</div>
          ${md.length ? `<div class="text-xs mt-1" style="color:var(--amber-ink)">ยังไม่ได้ทำใบตัดเบิก ${md.length} วัน (${md.slice(0, 6).map(_brnIssDm).join(', ')}${md.length > 6 ? ` และอีก ${md.length - 6} วัน` : ''}) · ทำใบของวันที่ขาดก่อน</div>` : ''}</div>
        <button onclick="brnIssGoToday()" style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:9px 16px;font-size:14px;cursor:pointer;white-space:nowrap">ไปที่ตัดเบิก</button></div>` : '';
  }
  const gapLine = md.length && slipOk && editable ? `<div class="mt-1 text-xs" style="color:var(--amber-ink)">ยังไม่ได้ทำใบตัดเบิก ${md.length} วัน (${md.slice(0, 6).map(_brnIssDm).join(', ')}${md.length > 6 ? ` และอีก ${md.length - 6} วัน` : ''}) · ทำใบของวันที่ขาดก่อน</div>` : '';
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
      ใช้ได้ ${esc(fmtDay(p.cover_from))} ถึง ${esc(fmtDay(p.cover_to))} (${p.cover_days} วัน)</div>
    <div class="mt-1" style="color:var(--text-secondary)">${stockLine}</div>${gapLine}
    <div class="mt-1" style="color:var(--text-secondary)">${o ? `ใบสั่งซื้อ #${o.order_id} ${_bmcChip(o.status_th, o.status === 'received' ? 'var(--good-ink)' : (o.status === 'submitted' || o.status === 'processed') ? 'var(--navy-ink)' : 'var(--amber-ink)')}${o.late ? ' ' + _bmcChip('ส่งล่าช้า (มีค่าปรับ)', 'var(--bad-ink)') : ''}` : _bmcChip('ยังไม่มีใบสั่งซื้อรอบนี้', 'var(--text-muted)')}
      · ${o && !editable   // a sent order: the two values come from the server (the same numbers as the list and the queue)
        ? `${VALUE_ORDERED_TH} <b>฿${_bmcNum(o.value_ordered, 0)}</b>${o.value_sent != null ? ` · ${VALUE_SENT_TH} <b>฿${_bmcNum(o.value_sent, 0)}</b>` : ''}`
        : `${VALUE_ORDERED_TH} ≈ <b>฿${_bmcNum(total, 0)}</b>`}${p.calendar_filled ? '' : ' · <span style="color:var(--amber-ink)">ใช้วันส่งค่าเริ่มต้น</span>'}</div>`;
  const banner = document.getElementById('brnOrdBanner');
  if (banner) {                                    // past the cutoff and still a draft: the late fee, in red
    const showLate = !!p.late && editable;
    banner.style.display = showLate ? '' : 'none';
    banner.innerHTML = showLate ? `<div class="text-xs" style="border:1px solid var(--bad-ink);border-radius:var(--r-sm);padding:8px 10px;color:var(--bad-ink)">เลยเวลาตัดรอบ (${esc(p.cutoff_th)}) ส่งตอนนี้มีค่าปรับล่าช้า</div>` : '';
  }
  const q = (document.getElementById('brnOrdQ').value || '').trim().toLowerCase();
  const only = document.getElementById('brnOrdOnly').checked;
  const rows = p.rows.filter(r => (!only || r.order_units > 0 || Number(_brnOrdQty[r.code] || 0) > 0)
    && (!q || (r.code + ' ' + r.name).toLowerCase().includes(q)));
  const line = o ? Object.fromEntries(o.lines.map(l => [l.code, l])) : {};
  const analyst = hasModule('bom.center');                 // the notes about recipes are the analyst's, not the branch's
  const reasonSel = (code) => {
    const cur = _brnOrdReason[code] || {};
    return `<div class="flex flex-wrap gap-2 mt-1 text-xs brn-reason"><select onchange="_brnOrdReason[${_bmcQ(code)}]=Object.assign(_brnOrdReason[${_bmcQ(code)}]||{}, {reason_code:this.value});_brnOrdTouched()" ${editable ? '' : 'disabled'}
        style="border:1px solid var(--amber-ink);border-radius:var(--r-sm);padding:4px 6px;font-size:16px;background:var(--bg-input);color:var(--text-primary)"><option value="">— เหตุผลที่สั่งต่างจากยอดแนะนำ —</option>${p.reasons.map(x => `<option value="${x.code}"${cur.reason_code === x.code ? ' selected' : ''}>${esc(x.label)}</option>`).join('')}</select>
      <input value="${esc(cur.note || '')}" placeholder="หมายเหตุ" oninput="_brnOrdReason[${_bmcQ(code)}]=Object.assign(_brnOrdReason[${_bmcQ(code)}]||{}, {note:this.value});_brnOrdTouched()" ${editable ? '' : 'disabled'}
        style="flex:1 1 120px;min-width:0;border:1px solid var(--border-control);border-radius:var(--r-sm);padding:4px 6px;font-size:16px;background:var(--bg-input);color:var(--text-primary)"></div>`;
  };
  const html = rows.map(r => {
    const base = line[r.code] ? line[r.code].suggested_units : (r.order_units_base ?? r.order_units);
    const sugg = line[r.code] ? (up === 1 ? base : Math.round(base * up)) : r.order_units;
    const v = _brnOrdQty[r.code] ?? '';
    const dev = _brnOrdNeedsReason(sugg, v);
    // where the suggestion comes from, in the unit the branch counts in (the engine works in grams / base units)
    const cu = esc(_brnCountUnit(r));
    const why = `${r.class ? `กลุ่มมูลค่า${{ A: 'สูง', B: 'กลาง', C: 'ต่ำ' }[r.class] || ''} · ` : ''}ยอดขายที่คาดไว้ใช้ ${_brnInCount(r, r.forecast)} + สำรอง ${_brnInCount(r, r.safety)}` +
      (r.on_hand != null ? ` − คงเหลือคาดการณ์ ${_brnInCount(r, Math.max(r.projected, 0))}` : '') +
      (r.on_order ? ` (รวมค้างส่ง ${_brnInCount(r, r.on_order)})` : '') + ` = ต้องการ ${_brnInCount(r, r.need)} ${cu}` +
      (analyst && (r.via || []).length ? ` · ใช้ใน ${esc(r.via.slice(0, 2).join(', '))}` : '');
    const open = _brnOrdWhy.has(r.code);
    const sysBox = r.system_qty == null ? '' : ` · คงเหลือ <b style="color:${r.system_qty < 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">${_bmcNum(r.system_qty, 2)}</b> ${esc(r.system_unit || r.count_unit || r.base_unit || '')}`;
    return `<div class="brn-row" style="border-top:1px solid var(--border);padding:8px 0">
      <div style="display:flex;gap:8px;align-items:flex-start">
        <div class="brn-namecol" style="flex:1 1 0;min-width:0"><div class="brn-name" title="${esc(r.name)}" style="color:var(--text-primary);font-weight:600">${esc(r.name)}${r.monitored ? ' ' + _bmcChip('บังคับ', 'var(--amber-ink)') : ''}</div>
          <div class="text-xs" style="color:var(--text-secondary);line-height:1.4">แนะนำ <b style="color:var(--text-primary)">${_bmcNum(sugg, 0)}</b> ${esc(_brnUnitLabel(r))}${up !== 1 ? ` <span class="text-[10.5px]">(ปกติ ${_bmcNum(base, 0)})</span>` : ''}${sysBox}</div>
          <button type="button" class="brn-why" aria-expanded="${open}" onclick="brnOrdWhy(${_bmcQ(r.code)})">${open ? 'ซ่อนที่มา ▴' : 'ดูที่มา ▾'}</button></div>
        <div class="brn-box"><input id="brnOrd_${esc(r.code)}" class="brn-qty" inputmode="decimal" autocomplete="off" value="${esc(v)}" ${editable ? '' : 'disabled'} aria-label="สั่ง ${esc(r.name)}" oninput="_brnOrdQty[${_bmcQ(r.code)}]=this.value;_brnOrdEdited.add(${_bmcQ(r.code)});brnRenderOrderDeferred()"
            style="border-color:${dev ? 'var(--amber-ink)' : 'var(--border-control)'}"><span class="brn-cap">สั่ง · ${esc(_brnUnitLabel(r))}</span></div></div>
      ${open ? `<div class="text-[11px] mt-1" style="color:var(--text-muted)">${esc(r.code)} · ${why}</div>` : ''}
      ${dev ? reasonSel(r.code) : ''}</div>`;
  }).join('') + extra.map(l => `<div style="border-top:1px solid var(--border);padding:8px 0" class="text-sm">${esc(l.name || l.code)} · สั่งเพิ่มนอกยอดแนะนำ ${_bmcNum(l.confirmed_units)} ${esc(l.order_unit === 'base' ? '' : 'แพ็ค')} ${l.reason_th ? '· ' + esc(l.reason_th) : ''}</div>`).join('');
  document.getElementById('brnOrdList').innerHTML = html || '<span class="text-xs" style="color:var(--good-ink)">✓ รอบนี้ไม่มีรายการที่ต้องสั่ง (คงเหลือเพียงพอ)</span>';
  const btn = (label, on, primary) => `<button onclick="${on}" style="border:${primary ? '0' : '1px solid var(--border)'};border-radius:var(--r-sm);padding:8px 14px;font-size:14px;cursor:pointer;background:${primary ? 'var(--accent)' : 'transparent'};color:${primary ? 'var(--on-accent)' : 'var(--text-primary)'}">${label}</button>`;
  let acts = '';
  if (editable) {
    // the reason a button is off is TEXT under it (a hover title does not exist on a phone)
    const send = slipOk ? btn('ส่งใบสั่งซื้อ', 'brnOrderSave(true)', true)
      : `<button disabled aria-describedby="brnOrdWhy" style="border:0;border-radius:var(--r-sm);padding:8px 14px;font-size:14px;cursor:not-allowed;opacity:.5;background:var(--accent);color:var(--on-accent)">ส่งใบสั่งซื้อ</button>`;
    acts = btn('บันทึกร่าง', 'brnOrderSave(false)') + send + (o ? btn('ยกเลิกใบสั่งซื้อ', "brnOrderAct('cancel')") : '')
      + (slipOk ? '' : `<div id="brnOrdWhy" class="w-full text-xs" style="color:var(--amber-ink)">ส่งใบสั่งซื้อไม่ได้ · ต้องยืนยันใบตัดเบิกวันนี้ก่อน</div>`);
  }
  // receiving belongs to an order the warehouse has sent (คลังจัดส่งแล้ว); one that is only sent waits for the warehouse
  else if (o.status === 'submitted') acts = btn('เปิดแก้ไข', "brnOrderAct('reopen')") + `<div class="w-full text-xs" style="color:var(--text-secondary)">รอคลังจัดส่ง · รับสินค้าได้เมื่อคลังจัดส่งแล้ว</div>`;
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
  const inp = 'border:1px solid var(--border-control);border-radius:var(--r-sm);padding:5px 8px;font-size:16px;background:var(--bg-input);color:var(--text-primary)';
  // folded by default: most rounds go as the system suggests. The line says whether an adjustment is in use.
  const head = `<button type="button" onclick="brnToggleBuffer()" aria-expanded="${_brnBufOpen}" style="display:flex;align-items:center;gap:8px;width:100%;min-height:40px;border:0;background:none;padding:0;text-align:left;cursor:pointer;color:var(--text-primary);font-size:13px;font-weight:600">
      <span>ยอดขายรอบนี้ต่างจากปกติ?</span>${cur.pct ? _bmcChip(`ใช้อยู่ ${sign(cur.pct)}`, 'var(--navy-ink)') : ''}<span style="margin-left:auto;color:var(--text-muted)">${_brnBufOpen ? '▴' : '▾'}</span></button>`;
  if (!_brnBufOpen) { box.innerHTML = head; return; }
  box.innerHTML = head + `<div class="text-[11px] mb-1" style="color:var(--text-muted)">มีงานหรือโปรโมชัน ใส่ % ที่คาดว่าจะเพิ่มหรือลด</div>
    <div class="flex flex-wrap items-center gap-2 text-xs">
      <input id="brnBufPct" type="number" inputmode="numeric" step="5" min="${b.min ?? -50}" max="${b.max ?? 100}" value="${cur.pct || 0}" style="width:72px;height:34px;text-align:center;${inp}"><span style="color:var(--text-secondary)">%</span>
      <select id="brnBufReason" style="${inp}"><option value="">— เหตุผล —</option>${(b.reasons || []).map(r => `<option value="${r.code}" ${r.code === cur.reason ? 'selected' : ''}>${esc(r.label)}</option>`).join('')}</select>
      <input id="brnBufNote" value="${esc(cur.note || '')}" placeholder="หมายเหตุ" style="flex:1 1 120px;min-width:0;${inp}">
      <button onclick="brnApplyBuffer()" style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:7px 12px;font-size:13px;cursor:pointer">ใช้กับยอดแนะนำ</button>
      <button onclick="brnToggleCovers()" style="border:1px solid var(--border);border-radius:var(--r-sm);padding:7px 12px;font-size:13px;cursor:pointer;background:transparent;color:var(--text-primary)">คำนวณจากจำนวนลูกค้า</button>
    </div>
    ${_brnCovPhone() ? '' : '<div id="brnCovers" class="mt-2" style="display:none"></div>'}`;
}
// PR-M3 · on a phone the customer-count helper opens as a bottom sheet over the page (a seven-row table does not fit inside a card)
function _brnCovPhone() { return document.body.classList.contains('supply-phone') && window.matchMedia('(max-width: 700px)').matches; }
function _brnCovSheet() {
  let s = document.getElementById('brnCovSheet');
  if (!s) {
    s = document.createElement('div');
    s.id = 'brnCovSheet'; s.className = 'brn-sheet'; s.setAttribute('role', 'dialog'); s.setAttribute('aria-modal', 'true'); s.setAttribute('aria-label', 'คำนวณจากจำนวนลูกค้า');
    s.innerHTML = `<div class="brn-sheet-scrim" onclick="brnCloseCovers()"></div><div class="brn-sheet-card">
      <div class="flex items-center gap-2 mb-2"><b class="text-sm" style="color:var(--text-primary)">คำนวณจากจำนวนลูกค้า</b>
        <button type="button" onclick="brnCloseCovers()" aria-label="ปิด" style="margin-left:auto;min-width:44px;min-height:44px;border:0;background:none;font-size:20px;cursor:pointer;color:var(--text-secondary)">✕</button></div>
      <div id="brnCovers"></div></div>`;
    (document.getElementById('tab-branchstock') || document.body).appendChild(s);
  }
  return s;
}
function brnCloseCovers() { const s = document.getElementById('brnCovSheet'); if (s) s.classList.remove('open'); const c = document.getElementById('brnCovers'); if (c && !_brnCovPhone()) c.style.display = 'none'; }
async function brnApplyBuffer() {
  const pct = Number(document.getElementById('brnBufPct').value || 0);
  const reason = document.getElementById('brnBufReason').value;
  const note = document.getElementById('brnBufNote').value.trim();
  const b = (_brnPlan && _brnPlan.buffer) || {};
  if (pct < (b.min ?? -50) || pct > (b.max ?? 100)) { _bmcStatus('brnOrdStatus', `% ต้องอยู่ระหว่าง ${b.min ?? -50} ถึง +${b.max ?? 100}`, 'var(--bad-ink)'); return; }
  if (pct && !reason) { _bmcStatus('brnOrdStatus', 'เลือกเหตุผลที่คาดว่ายอดขายจะต่างจากปกติ', 'var(--bad-ink)'); return; }
  if (pct && reason === 'other' && !note) { _bmcStatus('brnOrdStatus', 'เหตุผล “อื่น ๆ” ต้องระบุหมายเหตุ', 'var(--bad-ink)'); return; }
  _brnBuf = { pct, reason: pct ? reason : '', note: pct ? note : '' };
  await brnLoadOrder({ withBuffer: true, keepEdits: true });
  _bmcStatus('brnOrdStatus', pct ? `ปรับยอดแนะนำตามที่คาด ${pct > 0 ? '+' : ''}${_bmcNum(pct, 0)}% แล้ว (${_brnBufReasonTh(reason)})` : 'กลับมาใช้ยอดพยากรณ์ปกติแล้ว', 'var(--good-ink)');
  _brnOrdTouched();                              // the draft keeps the buffer
}
async function brnToggleCovers() {
  const phone = _brnCovPhone();
  let sheet = null;
  if (phone) { sheet = _brnCovSheet(); if (sheet.classList.contains('open')) { brnCloseCovers(); return; } }
  const box = document.getElementById('brnCovers');
  if (!box) return;
  if (!phone && box.style.display !== 'none') { box.style.display = 'none'; return; }
  box.style.display = '';
  if (sheet) sheet.classList.add('open');
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
  if (!c.available) { box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">ยังไม่มีข้อมูลจำนวนลูกค้าของสาขานี้ จึงคำนวณให้ไม่ได้ · ระบุ % เอง</span>'; return; }
  const inp = 'width:72px;height:36px;text-align:center;font-size:16px;border:1px solid var(--border-control);border-radius:6px;background:var(--bg-input);color:var(--text-primary)';
  box.innerHTML = `<div class="text-[11px] mb-1" style="color:var(--text-muted)">กรอกจำนวนลูกค้าที่คาด แล้วแปลงเป็น % (ปกติ = ค่าเฉลี่ยวันเดียวกันของสัปดาห์ ${esc(fmtDay(c.history_from))} ถึง ${esc(fmtDay(c.history_to))})</div>
    <table class="text-xs w-full"><thead><tr class="text-left" style="color:var(--text-muted)"><th class="pr-2">วันที่</th><th class="pr-2 text-right">ปกติ (คน)</th><th class="pr-2 text-right">ปีก่อน</th><th class="text-right">คาดว่า (คน)</th></tr></thead>
    <tbody>${c.days.map((d, i) => `<tr style="border-top:1px solid var(--border-soft)"><td class="pr-2 py-1">${esc(fmtDayShort(d.date))}</td><td class="pr-2 text-right">${_bmcNum(d.baseline_pax, 0)}</td>
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
  brnCloseCovers();
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
const _BRN_LATE_MSG = cut => `เลยเวลาตัดรอบ (${cut}) ส่งตอนนี้มีค่าปรับล่าช้า`;
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
        + `ยืนยันส่งใบสั่งซื้อรอบส่ง${p.delivery_th}?\nแก้ไขได้จนกว่าคลังยืนยันยอดส่ง`, { title: 'ยืนยันการส่งใบสั่งซื้อ', ok: 'ส่งใบสั่งซื้อ', cancel: 'ยกเลิก' })) return;
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
      _bmcStatus('brnOrdStatus', `บันทึกร่างแล้ว ${new Date().toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' })}`
        + (r.needs_reason.length ? ` · ต้องระบุเหตุผล ${r.needs_reason.length} รายการก่อนส่ง` : ''), r.needs_reason.length ? 'var(--amber-ink)' : 'var(--text-muted)');
      if (created) brnRenderOrder();
      return;
    }
    await brnLoadOrder();
    _bmcStatus('brnOrdStatus', submit ? `✓ ส่งใบสั่งซื้อ #${r.order_id} แล้ว · ฿${_bmcNum(r.value, 0)}${r.late ? ' · ส่งล่าช้า มีค่าปรับ' : ''}` :
      `✓ บันทึกร่าง${r.needs_reason.length ? ` · ต้องระบุเหตุผล ${r.needs_reason.length} รายการก่อนส่ง` : ''}`, r.needs_reason.length && !submit ? 'var(--amber-ink)' : 'var(--good-ink)');
  } catch (e) {
    if (e.detail && e.detail.code === 'late' && !ackLate) {        // the server's clock says the cutoff has passed: ask once, then send with the late fee
      if (await uiConfirm(_BRN_LATE_MSG(e.detail.cutoff_th) + '\nส่งใบสั่งซื้อนี้ต่อหรือไม่?', { title: 'ส่งใบสั่งซื้อล่าช้า', ok: 'ส่งใบสั่งซื้อ', cancel: 'ไม่ส่ง' })) return brnOrderSave(submit, quiet, true);
      return;
    }
    _bmcStatus('brnOrdStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 220), 'var(--bad-ink)');
  }
}
async function brnOrderAct(what) {
  const o = _brnPlan && _brnPlan.order;
  if (!o) return;
  if (what === 'cancel' && !await uiConfirm('ยกเลิกใบสั่งซื้อนี้?', { title: 'ยืนยันการยกเลิกใบสั่งซื้อ', ok: 'ยกเลิกใบสั่งซื้อ', cancel: 'ไม่ยกเลิก' })) return;
  try {
    await _bmcJson(`${API}/api/bom-center/order/${o.order_id}/${what}${_brnQS() ? '?' + _brnQS() : ''}`, { method: 'POST' });
    await brnLoadOrder();
  } catch (e) { _bmcStatus('brnOrdStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
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
    _brnOrdSent = d.orders || [];
    _brnDelivChips();                                   // "สั่งแล้ว" on the deliveries that already have an order
    document.getElementById('brnOrdHistory').innerHTML = d.orders.length ? d.orders.map(x => `<div style="border-top:1px solid var(--border);padding:4px 0;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center">
        <span><a href="#" onclick="brnOpenOrder(${x.order_id});return false" style="color:var(--accent);font-weight:600">#${x.order_id}</a> · ตัดรอบ ${esc(fmtDay(x.cycle_date))} · ${esc(_brnRoundLabel(x.round))} · ${esc(x.status_th)} · ${esc(x.value_label || 'มูลค่า')} ฿${_bmcNum(x.value, 0)}${x.late ? ' ' + _bmcChip('ส่งล่าช้า (มีค่าปรับ)', 'var(--bad-ink)') : ''}</span>
        ${x.status === 'processed' ? `<a href="#" onclick="brnSetSub('recv');setTimeout(() => brnOpenReceive(${x.order_id}), 200);return false" style="margin-left:auto;color:var(--accent)">รับสินค้าที่มาถึง →</a>` : ''}</div>`).join('') : 'ยังไม่มีข้อมูล';
  } catch (e) { document.getElementById('brnOrdHistory').textContent = String(e.message || e); }
}
// one order, read-only: what was suggested, confirmed, sent and received
async function brnOpenOrder(id) {
  const box = document.getElementById('brnOrdDetail');
  try {
    const o = await _bmcJson(`${API}/api/bom-center/order/${id}`);
    box.style.display = '';
    box.innerHTML = `<div class="flex flex-wrap items-center gap-2"><div class="text-sm font-semibold" style="color:var(--text-primary)">ใบสั่งซื้อ #${o.order_id} · ${esc(_brnRoundLabel(o.round))} · ตัดรอบ ${esc(fmtDay(o.cycle_date))} · ส่งถึง ${esc(fmtDay(o.delivery || '-'))} ${_bmcChip(o.status_th, 'var(--navy-ink)')}${o.late ? ' ' + _bmcChip('ส่งล่าช้า (มีค่าปรับ)', 'var(--bad-ink)') : ''}</div>
        <button onclick="document.getElementById('brnOrdDetail').style.display='none'" class="ml-auto" style="border:0;background:none;color:var(--text-muted);font-size:12px;cursor:pointer">ปิด</button></div>
      <div class="text-[11px] mt-1" style="color:var(--text-muted)">${o.po_ref ? 'PO ' + esc(o.po_ref) + ' · ' : ''}${o.processed_by ? 'คลังยืนยันยอดส่งโดย ' + esc(whoTh(o.processed_by)) : ''}</div>
      <table class="w-full text-xs mt-2"><thead><tr class="text-left" style="color:var(--text-muted)"><th class="pr-2">รายการ</th><th class="pr-2 text-right">แนะนำ</th><th class="pr-2 text-right">สั่ง</th><th class="pr-2 text-right">ส่งจริง</th><th class="text-right">รับจริง</th></tr></thead>
      <tbody>${o.lines.filter(l => l.confirmed_units || l.processed_units || l.received_units).map(l => `<tr style="border-top:1px solid var(--border-soft)"><td class="pr-2 py-1">${esc(l.name)}${l.reason_th ? ` <span style="color:var(--amber-ink)">· ${esc(l.reason_th)}</span>` : ''}</td>
        <td class="pr-2 text-right">${_bmcNum(l.suggested_units, 0)}</td><td class="pr-2 text-right">${_bmcNum(l.confirmed_units, 0)}</td><td class="pr-2 text-right">${l.processed_units != null ? _bmcNum(l.processed_units, 0) : '–'}</td><td class="text-right">${l.received_units != null ? _bmcNum(l.received_units, 0) : '–'}</td></tr>`).join('')}</tbody></table>`;
  } catch (e) { box.style.display = ''; box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}

// ── แจ้งเคลมสินค้า (owner 2026-10-08, 2026-10-09) ────────────────────────────────
// A shortfall is claimed inside the receive form above (origin 'receive'). This card is for a LATER
// claim on a received order: the lines and a reason each. No photo is attached here: the branch
// sends the pictures to Sales Support by LINE. Sales Support decides (js/15-orderhub.js); a later
// claim, once approved, takes the goods out of what the branch "received" so the variance stays true.
// PR-M4: the claim form lists nothing by itself — search the received lines, pick the ones that have a problem, and only those open
// (quantity · reason · a note for “อื่น ๆ”). The list of claims reads "round · delivery day · N lines" + where it was raised and
// what became of it (no ids). The photos go to ประสานงานขาย by LINE: the card says so once, at the top.
let _brnClaimOrd = null;       // GET /claims/claimable — the order being claimed
let _brnCl = { picked: [], vals: {}, q: '' };      // the claim form's state: the picked lines and what was typed
const _BRN_CLAIM_ST = { submitted: ['รอประสานงานขายพิจารณา', 'var(--amber-ink)'], approved: ['อนุมัติแล้ว', 'var(--good-ink)'],
  rejected: ['ไม่อนุมัติ', 'var(--bad-ink)'], cancelled: ['ยกเลิกแล้ว', 'var(--text-muted)'] };
function _brnCanClaim() { return _brnCanOrder(); }

async function brnLoadClaims() {
  const box = document.getElementById('brnClaimList');
  if (!box) return;
  const q = _brnQS() ? '&' + _brnQS() : '';
  try {
    const [ords, cl] = await Promise.all([_bmcJson(`${API}/api/bom-center/orders?status=received&limit=6${q}`),
                                          _bmcJson(`${API}/api/bom-center/claims?limit=15${q}`)]);
    const recent = ords.orders.map(o => `<div style="display:flex;gap:8px;align-items:center;border-top:1px solid var(--border);padding:8px 0">
        <div style="flex:1 1 0;min-width:0"><div class="text-sm" style="color:var(--text-primary);font-weight:600">${esc(_brnRoundLabel(o.round))} · ส่ง ${esc(fmtDay(o.delivery || o.cycle_date))}</div>
          <div class="text-xs" style="color:var(--text-secondary)">รับแล้ว ${esc(fmtDay(o.received_at))} · ${o.lines} รายการ</div></div>
        ${_brnCanClaim() ? `<button class="brn-act brn-act-line" onclick="brnClaimOpen(${o.order_id})">แจ้งเคลม</button>` : ''}</div>`).join('');
    const mine = cl.claims.map(c => { const st = _BRN_CLAIM_ST[c.status] || [c.status, 'var(--text-muted)'];
      return `<a href="#" class="brn-claimrow" onclick="brnClaimView(${c.claim_id});return false" style="display:block;border-top:1px solid var(--border);padding:8px 0;text-decoration:none">
        <div class="text-sm" style="color:var(--text-primary);font-weight:600">${esc(_brnRoundLabel(c.round))} · ส่ง ${esc(_brnIssDm(c.delivery || c.cycle_date))} · ${c.lines} รายการ</div>
        <div class="text-xs mt-0.5">${_bmcChip(c.origin_th || '', c.origin === 'receive' ? 'var(--navy-ink)' : 'var(--text-muted)')} ${_bmcChip(st[0], st[1])}</div></a>`; }).join('');
    box.innerHTML = (recent || '<div class="text-xs" style="color:var(--text-muted)">ยังไม่มีใบสั่งซื้อที่บันทึกรับสินค้าแล้ว</div>')
      + (mine ? `<div class="text-xs font-semibold mt-3" style="color:var(--text-primary)">เคลมที่แจ้งไว้</div>${mine}` : '');
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}

function _brnClLines() { return _brnClaimOrd ? _brnClaimOrd.lines.filter(l => l.remaining_units > 0) : []; }
function _brnClLine(code) { return _brnClLines().find(l => l.code === code); }
function _brnClUnit(l) { return l.order_unit === 'base' ? 'หน่วยฐาน' : 'แพ็ค'; }
async function brnClaimOpen(orderId) {
  const box = document.getElementById('brnClaimForm');
  if (!box) return;
  box.style.display = '';
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</span>';
  try {
    _brnClaimOrd = await _bmcJson(`${API}/api/bom-center/claims/claimable?order_id=${orderId}${_brnQS() ? '&' + _brnQS() : ''}`);
    const o = _brnClaimOrd;
    _brnCl = { picked: [], vals: {}, q: '' };
    const field = 'border:1px solid var(--border-control);border-radius:var(--r-sm);background:var(--bg-input);color:var(--text-primary)';
    const n = _brnClLines().length;
    box.innerHTML = `<div class="text-sm font-semibold" style="color:var(--text-primary)">แจ้งเคลม · ${esc(_brnRoundLabel(o.round))} · ตัดรอบ ${esc(fmtDay(o.cycle_date))}</div>
      ${n ? `<div class="text-xs mt-1" style="color:var(--text-secondary)">เลือกเฉพาะรายการที่มีปัญหา · เคลมได้ไม่เกินจำนวนที่รับ</div>
        <input id="brnClQ" type="search" autocomplete="off" placeholder="ค้นหารายการที่เคลม" oninput="brnClSearch(this.value)" class="mt-2"
          style="width:100%;box-sizing:border-box;min-height:44px;padding:6px 10px;font-size:16px;${field}">
        <div id="brnClRes" class="mt-1"></div><div id="brnClPicked"></div>`
        : '<div class="text-xs mt-2" style="color:var(--amber-ink)">ทุกรายการของใบสั่งซื้อนี้ถูกเคลมครบแล้ว</div>'}
      <textarea id="brnClaimNote" rows="2" placeholder="ข้อความถึงประสานงานขาย (ถ้ามี)" style="width:100%;margin-top:10px;padding:8px;font-size:16px;${field}"></textarea>
      <div class="flex flex-wrap items-center gap-2 mt-2">
        <button id="brnClaimSendBtn" class="brn-act" onclick="brnClaimSend()" disabled>ส่งแจ้งเคลม</button>
        <button class="brn-act brn-act-line" onclick="brnClaimClose()">ยกเลิก</button></div>
      <div id="brnClaimStatus" class="brn-status mt-1" role="status"></div>`;
    brnClResults(); brnClBar();
    box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function brnClSearch(v) { _brnCl.q = String(v || ''); brnClResults(); }
function brnClResults() {
  const box = document.getElementById('brnClRes');
  if (!box) return;
  const q = _brnCl.q.trim().toLowerCase();
  const hits = _brnClLines().filter(l => !_brnCl.picked.includes(l.code) && (!q || ((l.code || '') + ' ' + (l.name || '')).toLowerCase().includes(q)));
  if (!q && hits.length > 8) { box.innerHTML = `<div class="text-xs" style="color:var(--text-muted);padding:4px 2px">พิมพ์ชื่อวัตถุดิบเพื่อเลือกรายการที่เคลม (ทั้งหมด ${hits.length} รายการ)</div>`; return; }
  box.innerHTML = hits.slice(0, 8).map(l => `<button type="button" class="brn-pick" onclick="brnClPick(${_bmcQ(l.code)})"><span style="flex:1 1 0;min-width:0;text-align:left;overflow-wrap:anywhere">${esc(l.name || l.code)}</span><span class="text-xs" style="color:var(--text-secondary);white-space:nowrap">รับแล้ว ${_bmcNum(l.received_units)} ${esc(_brnClUnit(l))}</span><span style="color:var(--accent);font-weight:600">เลือก</span></button>`).join('')
    + (hits.length > 8 ? `<div class="text-xs" style="color:var(--text-muted);padding:4px 2px">แสดง 8 จาก ${hits.length} รายการ · พิมพ์ชื่อเพิ่มเพื่อค้นหา</div>`
      : !hits.length && _brnCl.picked.length < _brnClLines().length ? '<div class="text-xs" style="color:var(--text-muted);padding:4px 2px">ไม่พบรายการที่ค้นหา</div>' : '');
}
function brnClPick(code) {
  if (!_brnClLine(code) || _brnCl.picked.includes(code)) return;
  _brnCl.picked.push(code);
  _brnCl.vals[code] = { q: '', r: '', n: '' };
  _brnCl.q = '';
  const qi = document.getElementById('brnClQ'); if (qi) qi.value = '';
  brnClResults(); brnClPickedRender(); brnClBar();
  setTimeout(() => { const el = document.getElementById('brnCq_' + code); if (el) el.focus(); }, 30);
}
function brnClUnpick(code) {
  _brnCl.picked = _brnCl.picked.filter(c => c !== code);
  brnClResults(); brnClPickedRender(); brnClBar();
}
function brnClField(code, f, v) {
  (_brnCl.vals[code] || (_brnCl.vals[code] = {}))[f] = String(v || '');
  if (f === 'r') {                                         // the note box opens for “อื่น ๆ” only
    const nb = document.getElementById('brnCnBox_' + code); if (nb) nb.style.display = v === 'other' ? '' : 'none';
  }
}
function brnClBar() {
  const b = document.getElementById('brnClaimSendBtn'); if (b) b.disabled = !_brnCl.picked.length;
  const st = document.getElementById('brnClaimStatus');
  if (st && !_brnCl.picked.length) { st.textContent = 'เลือกรายการที่เคลมอย่างน้อย 1 รายการ'; st.style.color = 'var(--text-muted)'; }
  else if (st && _brnCl.picked.length && st.textContent === 'เลือกรายการที่เคลมอย่างน้อย 1 รายการ') st.textContent = '';
}
function brnClPickedRender() {
  const box = document.getElementById('brnClPicked');
  if (!box) return;
  const reasons = (_brnClaimOrd && _brnClaimOrd.reasons) || [];
  const field = 'border:1px solid var(--border-control);border-radius:var(--r-sm);background:var(--bg-input);color:var(--text-primary)';
  box.innerHTML = _brnCl.picked.map(code => {
    const l = _brnClLine(code); if (!l) return '';
    const c = esc(code), v = _brnCl.vals[code] || {}, cq = _bmcQ(code), unit = esc(_brnClUnit(l));
    return `<div class="brn-row" style="border-top:1px solid var(--border);padding:8px 0">
      <div style="display:flex;gap:8px;align-items:flex-start">
        <div class="brn-namecol" style="flex:1 1 0;min-width:0">
          <div class="brn-name" style="color:var(--text-primary);font-weight:600">${esc(l.name || code)}</div>
          <div class="brn-sub text-[11px]" style="color:var(--text-muted)">${c} · ส่งจริง ${_bmcNum(l.sent_units)} · รับแล้ว ${_bmcNum(l.received_units)}${l.claimed_units ? ' · เคลมไปแล้ว ' + _bmcNum(l.claimed_units) : ''} · เคลมได้อีก <b style="color:var(--text-primary)">${_bmcNum(l.remaining_units)}</b> ${unit}</div></div>
        <div class="brn-box"><input id="brnCq_${c}" class="brn-qty" inputmode="decimal" autocomplete="off" value="${esc(v.q || '')}" aria-label="จำนวนที่เคลม ${esc(l.name || code)}" oninput="brnClField(${cq}, 'q', this.value)"><span class="brn-cap">เคลม · ${unit}</span></div>
        <button type="button" class="brn-x" aria-label="เอาออก ${esc(l.name || code)}" onclick="brnClUnpick(${cq})">✕</button></div>
      <div class="brn-reason" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px">
        <select id="brnCr_${c}" aria-label="สาเหตุ" onchange="brnClField(${cq}, 'r', this.value)" style="flex:1 1 180px;${field};padding:6px 8px"><option value="">— สาเหตุ —</option>${reasons.map(r => `<option value="${esc(r.code)}"${v.r === r.code ? ' selected' : ''}>${esc(r.label)}</option>`).join('')}</select></div>
      <div id="brnCnBox_${c}" class="brn-reason" style="margin-top:6px;${v.r === 'other' ? '' : 'display:none'}"><input id="brnCn_${c}" value="${esc(v.n || '')}" oninput="brnClField(${cq}, 'n', this.value)" placeholder="ระบุคำอธิบาย (จำเป็นเมื่อเลือก “อื่น ๆ”)" style="width:100%;box-sizing:border-box;${field};padding:6px 8px"></div>
      <div id="brnCe_${c}" class="brn-err" role="alert"></div></div>`;
  }).join('');
}
function brnClaimClose() {
  _brnClaimOrd = null;
  _brnCl = { picked: [], vals: {}, q: '' };
  const box = document.getElementById('brnClaimForm'); if (box) { box.style.display = 'none'; box.innerHTML = ''; }
}
async function brnClaimSend() {
  const o = _brnClaimOrd;
  if (!o) return;
  const lines = [];
  const bad = (code, m) => {                                // the note goes under the line it is about
    document.querySelectorAll('#brnClPicked .brn-err').forEach(e => { e.textContent = ''; });
    const er = document.getElementById('brnCe_' + code); if (er) er.textContent = m;
    _bmcStatus('brnClaimStatus', 'แก้รายการที่ระบุสีแดงก่อนส่ง', 'var(--amber-ink)');
    const el = document.getElementById(m.includes('สาเหตุ') ? 'brnCr_' + code : 'brnCq_' + code); if (el) { el.scrollIntoView({ block: 'center' }); el.focus(); }
  };
  document.querySelectorAll('#brnClPicked .brn-err').forEach(e => { e.textContent = ''; });
  for (const code of _brnCl.picked) {
    const l = _brnClLine(code); if (!l) continue;
    const v = _brnCl.vals[code] || {}, raw = String(v.q || '').trim().replace(',', '.'), q = Number(raw);
    const reason = v.r || '', note = String(v.n || '').trim();
    if (raw === '' || !Number.isFinite(q) || !(q > 0)) { bad(code, 'จำนวนที่เคลมต้องมากกว่า 0'); return; }
    if (q > l.remaining_units + 1e-9) { bad(code, `เคลมได้ไม่เกิน ${_bmcNum(l.remaining_units)}`); return; }
    if (!reason) { bad(code, 'เลือกสาเหตุ'); return; }
    if (reason === 'other' && !note) { bad(code, 'สาเหตุ “อื่น ๆ” ต้องระบุคำอธิบาย'); return; }
    lines.push({ code, claim_units: q, reason, note: note || null });
  }
  if (!lines.length) { _bmcStatus('brnClaimStatus', 'เลือกรายการที่เคลมอย่างน้อย 1 รายการ', 'var(--amber-ink)'); return; }
  if (!await uiConfirm(`ส่งแจ้งเคลม ${lines.length} รายการ ให้ประสานงานขายพิจารณา?`, { title: 'ยืนยันการแจ้งเคลม', ok: 'ส่งแจ้งเคลม', cancel: 'กลับไปแก้' })) return;
  const fd = new FormData();
  fd.append('payload', JSON.stringify({ order_id: o.order_id, lines, note: (document.getElementById('brnClaimNote').value || '').trim() || null }));
  _bmcStatus('brnClaimStatus', 'กำลังส่ง…');
  try {
    await _bmcJson(`${API}/api/bom-center/claims${_brnQS() ? '?' + _brnQS() : ''}`, { method: 'POST', body: fd });
    brnClaimClose();
    _bmcStatus('brnClaimMsg', `✓ ส่งแจ้งเคลมแล้ว ${lines.length} รายการ · ผลพิจารณาแสดงที่หน้านี้`, 'var(--good-ink)');
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
    box.innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap"><div class="text-sm font-semibold" style="color:var(--text-primary)">${esc(_brnRoundLabel(c.round))} · ส่ง ${esc(_brnIssDm(c.delivery || c.cycle_date))} ${_bmcChip(c.origin_th || '', c.origin === 'receive' ? 'var(--navy-ink)' : 'var(--text-muted)')} ${_bmcChip(st[0], st[1])}</div>
        <button onclick="brnClaimClose()" style="border:1px solid var(--border);border-radius:var(--r-sm);padding:4px 12px;font-size:12px;cursor:pointer;background:transparent;color:var(--text-primary)">ปิด</button></div>
      ${c.decision_note ? `<div class="text-xs mt-1" style="color:var(--text-secondary)">ผลพิจารณา${c.decided_by ? ' โดย ' + esc(whoTh(c.decided_by)) : ''}: ${esc(c.decision_note)}</div>` : ''}
      ${_brnClaimEffect(c)}
      <div style="overflow-x:auto" class="mt-2"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left"><th class="pr-2">วัตถุดิบ</th><th class="pr-2 text-right">ส่งจริง</th><th class="pr-2 text-right">รับแล้ว</th><th class="pr-2 text-right">เคลม</th><th class="pr-2 text-right">อนุมัติ</th><th>สาเหตุ</th></tr></thead><tbody>
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
      return line('var(--good-ink)', 'อนุมัติแล้ว · รับเข้าตามจริงอยู่แล้ว'
        + (c.adjust_receipt_id ? ' · ส่วนที่ไม่อนุมัติบันทึกเป็นรับเข้าเพิ่มแล้ว' : ''));
    return line('var(--good-ink)', 'อนุมัติแล้ว · หักจำนวนที่อนุมัติออกจากรับเข้า');
  }
  if (c.status === 'rejected' && c.origin === 'receive')
    return line('var(--text-secondary)', 'ไม่อนุมัติ · บันทึกจำนวนที่เคลมเป็นรับเข้า');
  return '';
}
async function brnClaimCancel(id) {
  if (!await uiConfirm('ยกเลิกเคลมนี้? สาขาแจ้งใหม่ได้ภายหลัง', { title: 'ยืนยันการยกเลิกเคลม', ok: 'ยกเลิกเคลม', cancel: 'ไม่ยกเลิก' })) return;
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
  el.innerHTML = `บันทึกรับสินค้าแล้ว · พบของชำรุด เสีย หรือส่งผิดภายหลัง <a href="#" onclick="brnClaimOpen(${orderId});return false" style="color:var(--accent);font-weight:600;text-decoration:underline">แจ้งเคลม</a>`;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
}
