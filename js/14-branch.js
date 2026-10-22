// 14-branch.js — สาขาของฉัน (BoM Center Phase 2: ตัดเบิก (ใบตัดเบิกประจำวัน, PR-J2) · สั่งสินค้า (on the system stock) · S7 รับของ + ผลต่างของฉัน · ตรวจนับสิ้นเดือน).
// Mobile first (375 px). Shares the global scope of slices 01-13; every name here
// starts with `brn`. A branch login is pinned to its own branch by the server
// (auth.scope_branch); central roles pick a branch from the list.

let _brnMe = null;            // /api/bom-center/stock/me
let _brnSheet = null;         // /api/bom-center/stock/sheet
let _brnCountPlan = null;     // /api/bom-center/stock/plan - which count is due now (batch 4b, 3.4)
let _brnCountMode = null;     // 'full' | 'cycle' - the count on screen; null = the one the plan says is due
let _brnCountKey = null;      // the branch the mode was chosen for
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
// batch 4b (3.4): the count calls carry the count on screen - the weekly count of the class-A items is `mode=cycle`
function _brnQSM() { return [_brnQS(), _brnCountMode === 'cycle' ? 'mode=cycle' : ''].filter(Boolean).join('&'); }
function _brnCanLayout() { return hasModule('bom.center') || hasModule('supply.ordering'); }
// the server's write rules (auth.py): counts, manual receipts and variance = the branch or bom.center;
// an order may also be worked by Sales Support. Other roles see the pages read-only.
function _brnCanCount() { return hasModule('branch.stock') || hasModule('bom.center'); }
function _brnCanOrder() { return hasModule('branch.stock') || hasModule('bom.center') || hasModule('supply.ordering'); }
function _brnCanVariance() { return hasModule('branch.stock') || hasModule('bom.center') || hasModule('supply.ordering'); }   // close step 5 (owner 2026-10-07)
const _BRN_READONLY = 'บทบาทของคุณดูหน้านี้ได้ แต่บันทึกแทนสาขาไม่ได้';

// 4.1 · a branch screen writes the SHORT name when the register has one and the full name otherwise. The payloads carry `short_name` beside
// `name` (fmt.add_short). U3 (owner 2026-10-22): a small "ดูที่มา" on the row opens what the row does not already say, in plain words - the
// full name (when a short one is shown) and the row's own `facts` (the unit ladder, the last count, why the item is on the list); the code
// only comes last, beside them. A row with nothing more to say has no link at all (a bare code tells the floor nothing).
function _brnNm(x) { return String((x && (x.short_name || x.name || x.code)) || ''); }
function _brnSrc(x, facts) {
  const lines = [];
  if (x && x.short_name && x.name && x.short_name !== x.name) lines.push(String(x.name));
  (facts || []).forEach(f => { if (f) lines.push(String(f)); });
  if (!lines.length) return '';
  if (x && x.code) lines.push('รหัส ' + x.code);
  return `<button type="button" class="brn-why brn-srcbtn" onclick="brnSrc(this)" aria-expanded="false">ดูที่มา</button><span class="brn-srcv" hidden> ${lines.map(t => esc(t)).join(' · ')}</span>`;
}
// the parts of a row's small line, joined by " · " (an empty part - e.g. no "ดูที่มา" - leaves no stray separator)
function _brnLine(...parts) { return parts.filter(Boolean).join(' · '); }
function brnSrc(btn) {
  const v = btn.nextElementSibling;
  if (!v) return;
  v.hidden = !v.hidden;
  btn.setAttribute('aria-expanded', v.hidden ? 'false' : 'true');
}
// 4.2 · an order is named by its round and delivery day, never by a number: "รอบ 2 (ส่ง 10/10)" - _bmcRoundDay (13-bom-center.js)
// 3.1 · a quantity read off the system or off the last count: one decimal at most, never 0.005; a figure between 0 and 1 of a unit is
// "น้อยกว่า 1 แพ็ค". A placeholder uses the same figure, so it never shows more than the box would hold.
function _brnNice(v) {
  if (v === null || v === undefined || v === '' || isNaN(v)) return '';
  const x = Number(v), n = Math.round(x * 10) / 10;
  if (n === 0 && x > 0) return '<0.1';
  return _bmcNum(n === 0 ? 0 : n, 1);
}
function _brnPh(v) { const n = _brnNice(v); return n === '' || n === '<0.1' ? '0' : n; }      // a placeholder: the same figure, "0" for a dust of a unit
function _brnUnitW(unit) { return String(unit || '').replace(/\s*\([^)]*\)/g, '').trim().split(/\s+/)[0] || ''; }      // "กล่อง (10 แพ็ค)" -> กล่อง · "แพ็ค 1 กก." -> แพ็ค
// U3 (owner 2026-10-22): a unit named with its contents reads the same on the box, the stock figure and the ladder - "แพ็ค 10 กก." is written
// "แพ็ค (10 กก.)", so "4 แพ็ค (10 กก.)" can never be read as 4 packs of 1 kg. A unit already in brackets ("กล่อง (10 แพ็ค)") or a bare word stays.
// Display only: the register keeps its unit text.
function _brnUnitFull(unit) {
  const u = String(unit || '').trim();
  if (u.includes('(')) return u;
  const m = u.match(/^(\S+)\s+(\d[\s\S]*)$/);
  return m ? `${m[1]} (${m[2].trim()})` : u;
}
function _brnSysTxt(q, unit) {
  if (q === null || q === undefined || q === '' || isNaN(q)) return '';
  const w = _brnUnitFull(unit), n = Number(q);
  if (n > 0 && n < 1) return `น้อยกว่า 1 ${w}`.trim();
  return `${_brnNice(n)} ${w}`.trim();
}

// PR-M7 · ตัวอักษรใหญ่: the branch's phone reads at x1.15 (html.brn-big, index.html). The choice is remembered on this device.
const _BRN_BIG_KEY = 'oshinei_brn_big';
function brnBigFontGet() { try { return localStorage.getItem(_BRN_BIG_KEY) === '1'; } catch (e) { return false; } }
function brnBigFontApply(on) {
  document.documentElement.classList.toggle('brn-big', !!on && document.body.classList.contains('supply-phone'));   // the branch login only
  const b = document.getElementById('brnBigBtn');
  if (b) b.setAttribute('aria-pressed', on ? 'true' : 'false');
}
function brnBigFontToggle() {
  const on = !brnBigFontGet();
  try { localStorage.setItem(_BRN_BIG_KEY, on ? '1' : '0'); } catch (e) { /* the choice then lasts until the page is closed */ }
  brnBigFontApply(on);
}

async function loadBranchStock() {
  try {
    brnBigFontApply(brnBigFontGet());                       // the toggle shows the device's choice (the class itself is set at login)
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
  _brnShowSheet(true);
  const doneBox = document.getElementById('brnDone');
  if (doneBox) doneBox.style.display = 'none';
  if (!(_brnMe && _brnMe.scoped) && !_brnBid()) {
    list.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">เลือกสาขาด้านบน</span>';
    document.getElementById('brnZones').innerHTML = '';
    document.getElementById('brnHistory').innerHTML = '';
    return;
  }
  list.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลดรายการ…</span>';
  try {
    let plan = null;
    try { plan = await _bmcJson(`${API}/api/bom-center/stock/plan?${_brnQS()}`); } catch (e) { plan = null; }
    _brnCountPlan = plan;
    const ck = String(_brnBid() || 'me');
    if (_brnCountKey !== ck || !_brnCountMode) {            // another branch, or the first look: open the count that is due
      _brnCountKey = ck;
      _brnCountMode = plan && plan.cycle_counts && !plan.first ? plan.mode : 'full';
    }
    if (!(plan && plan.cycle_counts && !plan.first)) _brnCountMode = 'full';
    _brnSheet = await _bmcJson(`${API}/api/bom-center/stock/sheet?${_brnQSM()}`);
    _brnQty = {}; _brnQty2 = {};
    _brnSheet.zones.forEach(z => z.items.forEach(it => { if (it.draft_qty != null) _brnQty[it.code] = String(it.draft_qty); }));
    _brnDirty = false;
    if (!_brnZone || !_brnSheet.zones.some(z => z.zone === _brnZone)) _brnZone = (_brnSheet.zones[0] || {}).zone || null;
    const s = _brnSheet;
    const cyc = s.mode === 'cycle';
    // PR-U1: a month-end count was just sent and the next one is not due yet (or nobody has started it): the pane says what was done and
    // when the next count is - never an empty sheet of every item. The sheet opens with one button once the next count is due.
    const done = _brnCountDone(s, plan);
    _brnShowSheet(!done);
    document.getElementById('brnKind').innerHTML = (done ? _bmcChip(`นับ${plan.last_full.kind_th}แล้ว วันที่ ${fmtDay(plan.last_full.date)}`, 'var(--good-ink)')
      : cyc ? _bmcChip('ครั้งนี้: ตรวจนับหมุนเวียน (ไม่ใช่การปิดงวด)', 'var(--navy-ink)')
      : _bmcChip(s.next_kind === 'begin' ? 'ครั้งนี้: ต้นงวด (ครั้งแรก) · นับครบทุกรายการ' : 'ครั้งนี้: ปลายงวด · นับครบทุกรายการ', s.next_kind === 'begin' ? 'var(--navy-ink)' : 'var(--good-ink)'))
      + _brnModeSwitch();
    const gap = s.not_countable && hasModule('bom.center') ? ` · ไม่อยู่ในใบตรวจนับ ${s.not_countable} รายการ (ยังไม่มีหน่วยฐาน)` : '';
    document.getElementById('brnInfo').textContent = done
      ? `นับครั้งถัดไป: ปลายงวด วันที่ ${fmtDay(plan.next_full_date)}${gap}`
      : (s.last_count ? `ตรวจนับล่าสุด ${fmtDay(s.last_count.count_date)} (${s.last_count.kind === 'begin' ? 'ต้นงวด' : s.last_count.kind === 'cycle' ? 'หมุนเวียน' : 'ปลายงวด'}) โดย ${whoTh(s.last_count.counted_by)}` : 'ยังไม่มีผลตรวจนับ') +
      (s.draft ? ` · มีร่างที่ยังไม่ส่ง (${fmtAt(s.draft.updated_at)})` : '') +
      ` · ${s.items} รายการ${cyc ? '' : ' (ปิดงวดต้องนับครบทุกรายการ)'}` + gap;
    const box = document.getElementById('brnDone');
    if (box) {
      box.style.display = done && plan.next_full_due && _brnCanCount() ? '' : 'none';
      box.innerHTML = done && plan.next_full_due ? `<button onclick="brnCountStart()" style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:9px 16px;font-size:14px;cursor:pointer;min-height:44px">เริ่มตรวจนับปลายงวด</button>` : '';
    }
    if (done) { brnLoadHistory(); return; }
    brnRender();
    brnLoadHistory();
  } catch (e) { list.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}

// PR-U1 · the finished month-end count: shown as done until the person starts the next one (a draft or a started count shows the sheet)
let _brnCountStarted = null;          // the branch key the person pressed "เริ่มตรวจนับปลายงวด" for
function _brnCountDone(s, plan) {
  if (!s || s.mode === 'cycle' || s.draft || !plan || !plan.last_full || !plan.next_full_date) return false;
  return _brnCountStarted !== String(_brnBid() || 'me');
}
function _brnShowSheet(on) {
  ['brnSheetBar', 'brnList', 'brnSheetActs'].forEach(id => { const el = document.getElementById(id); if (el) el.style.display = on ? '' : 'none'; });
}
function brnCountStart() { _brnCountStarted = String(_brnBid() || 'me'); brnLoadSheet(); }

// 3.4 · the optional weekly check of the class-A items (switch in ตั้งค่า, off by default). It never closes a period: a month ends only with the
// full count of every item (owner decision 2026-10-13).
function _brnModeSwitch() {
  const p = _brnCountPlan;
  if (!p || !p.cycle_counts || p.first) return '';
  const btn = (m, t) => `<button onclick="brnCountModeSet('${m}')" aria-pressed="${_brnCountMode === m}" style="min-height:36px;border:1px solid ${_brnCountMode === m ? 'var(--accent)' : 'var(--border)'};border-radius:999px;padding:4px 12px;font-size:13px;cursor:pointer;margin-left:6px;background:${_brnCountMode === m ? 'var(--accent)' : 'transparent'};color:${_brnCountMode === m ? 'var(--on-accent)' : 'var(--text-primary)'}">${t}</button>`;
  const why = p.cycle_due ? 'สัปดาห์นี้ยังไม่ได้นับกลุ่มมูลค่าสูง · ตรวจเสริม ไม่ใช่การปิดงวด' : 'สัปดาห์นี้นับกลุ่มมูลค่าสูงแล้ว · ปิดงวดสิ้นเดือนต้องตรวจนับครบทุกรายการ';
  return `<div class="mt-2">${btn('cycle', 'ตรวจนับหมุนเวียน (ไม่ใช่การปิดงวด)')}${btn('full', 'ตรวจนับครบทุกรายการ (ปิดงวด)')} ${uiQ('brn:cyc', 'ตรวจนับหมุนเวียน', '<p>' + esc(why) + '</p>')}</div>`;
}
async function brnCountModeSet(m) {
  if (m === _brnCountMode) return;
  if (_brnDirty) await brnSave(false, true);
  _brnCountMode = m;
  await brnLoadSheet();
}
function brnSetZone(z) { _brnZone = z; brnRender(); window.scrollTo({ top: 0 }); }
// an item is counted once it has a number ≥ 0 (a 0 is a count; a word is not)
function _brnCounted(code) {
  const v = _brnQty[code];
  return v !== undefined && v !== '' && Number.isFinite(Number(v)) && Number(v) >= 0;
}

// 3.1 · month-end count: an item that comes in a box of packs has two boxes (กล่อง + แพ็ค) that add up in the entry unit (and, on the
// server, in base units: the line carries both parts). The working copy of the total stays `_brnQty` (the entry unit); `_brnQty2` keeps the
// two figures as typed so a half-typed box does not jump.
let _brnQty2 = {};            // code -> {big, small} (strings)
function _brnSplit(total, per) {      // 23 packs in a box of 10 -> {big: '2', small: '3'}
  const t = Number(total);
  if (!Number.isFinite(t) || !(per > 1)) return { big: '', small: '' };
  const big = Math.floor(t / per + 1e-9), small = Math.round((t - big * per) * 1000) / 1000;
  return { big: big > 0 ? String(big) : '', small: small > 0 || big === 0 ? String(small) : '' };
}
function _brnPair(it) {
  const q = _brnQty2[it.code];
  if (q) return q;
  const v = _brnQty[it.code];
  return v === undefined || v === '' ? { big: '', small: '' } : _brnSplit(v, it.unit_big.per);
}
function _brnTotal2(it, p) {          // the two boxes in the entry unit: '' when both are empty
  const a = String(p.big).trim(), b = String(p.small).trim();
  if (a === '' && b === '') return '';
  return String(Math.round(((Number(a) || 0) * it.unit_big.per + (Number(b) || 0)) * 1000) / 1000);
}
function brnSet2(code, f, v) {
  const it = _brnItem(code);
  if (!it || !it.unit_big) return;
  const p = Object.assign({}, _brnPair(it));
  p[f] = String(v || '').replace(',', '.').trim();
  _brnQty2[code] = p;
  const t = _brnTotal2(it, p);
  brnSet(code, t);
  const el = document.getElementById('brnTot_' + code);
  if (el) el.textContent = t === '' ? '' : `รวม ${_brnSysTxt(Number(t), it.count_unit)}`;
}
function brnRender() {
  const s = _brnSheet;
  if (!s) return;
  const counted = _brnCounted;
  const all = s.zones.flatMap(z => z.items);
  const nDone = all.filter(i => counted(i.code)).length;
  document.getElementById('brnProgress').innerHTML =
    `นับแล้ว <b style="color:${nDone === all.length ? 'var(--good-ink)' : 'var(--amber-ink)'}">${nDone}/${all.length}</b> รายการ${s.mode === 'cycle' ? '' : ' · ปิดงวดต้องนับครบ'}`;
  // a zone chip: the number counted in it, and ✓ once every item of the zone has a quantity (0 counts, blank does not)
  document.getElementById('brnZones').innerHTML = s.zones.map(z => {
    const done = z.items.filter(i => counted(i.code)).length;
    const ok = z.items.length > 0 && z.items.every(i => counted(i.code));
    const on = z.zone === _brnZone;
    return `<button onclick="brnSetZone('${z.zone}')" ${ok ? 'title="โซนนี้ครบ"' : ''} style="border:1px solid ${on ? 'var(--accent)' : 'var(--border)'};border-radius:999px;padding:6px 12px;font-size:13px;cursor:pointer;background:${on ? 'var(--accent)' : 'transparent'};color:${on ? 'var(--on-accent)' : 'var(--text-primary)'}">${esc(z.label)}${done ? ' · ' + done : ''}${ok ? ` <span style="color:${on ? 'inherit' : 'var(--good-ink)'}" aria-label="โซนนี้ครบ">✓</span>` : ''}</button>`;
  }).join('');
  const zone = s.zones.find(z => z.zone === _brnZone) || { items: [] };
  const q = (document.getElementById('brnQ').value || '').trim().toLowerCase();
  const reqOnly = document.getElementById('brnRequiredOnly').checked;
  const items = (q ? all : zone.items).filter(i => (!reqOnly || !counted(i.code)) && (!q || (i.code + ' ' + i.name + ' ' + (i.short_name || '')).toLowerCase().includes(q)));
  const zoneSel = it => !_brnCanLayout() ? '' : `<select onchange="brnSetLayout(${_bmcQ(it.code)}, this.value)" title="โซนจัดเก็บ" style="font-size:11px;border:1px solid var(--border);border-radius:6px;background:var(--bg-input);color:var(--text-muted);padding:1px 2px">${(_brnMe.zones || []).map(z => `<option value="${z.zone}"${z.zone === (s.zones.find(zz => zz.items.includes(it)) || {}).zone ? ' selected' : ''}>${esc(z.label)}</option>`).join('')}</select>`;
  document.getElementById('brnList').innerHTML = items.length ? items.map(it => {
    const v = _brnQty[it.code] ?? '';
    const last = it.last_qty != null ? `ครั้งก่อน ${_brnNice(it.last_qty)} ${esc(_brnUnitFull(it.last_unit || it.count_unit || ''))}` : '';
    const two = !!it.unit_big;                                    // 3.1: a box of packs has a box for the boxes and one for the packs
    const pair = two ? _brnPair(it) : null, word = _brnUnitW(it.count_unit);
    const bigW = two ? _brnUnitW(it.unit_big.label) : '';
    const box2 = (f, val, cap, ph) => `<div class="brn-box"><input class="brn-qty" inputmode="decimal" autocomplete="off" value="${esc(val)}" placeholder="${ph}" aria-label="${esc(cap)} ${esc(_brnNm(it))}"
          oninput="brnSet2(${_bmcQ(it.code)}, '${f}', this.value)" style="border-color:${val !== '' ? 'var(--accent)' : 'var(--border-control)'}"><span class="brn-cap">${esc(cap)}</span></div>`;
    const lastBig = it.last_qty != null && two ? _brnSplit(it.last_qty, it.unit_big.per) : null;
    return `<div class="brn-row" style="display:flex;flex-wrap:wrap;gap:6px 8px;align-items:center;border-top:1px solid var(--border);padding:8px 0">
      <div class="brn-namecol" style="flex:1 1 220px;min-width:0">
        <div class="text-sm" style="color:var(--text-primary);font-weight:600;overflow-wrap:anywhere">${esc(_brnNm(it))}</div>
        <div class="text-[11px]" style="color:var(--text-muted)">${_brnLine(_brnSrc(it), _brnLadderTxt(it), two ? `1 ${esc(bigW)} = ${_bmcNum(it.unit_big.per)} ${esc(word)}` : '', last)} ${zoneSel(it)}${two ? ` <span id="brnTot_${esc(it.code)}" class="text-[11px]" style="color:var(--text-secondary)">${_brnTotalTxt(it, pair)}</span>` : ''}</div>
      </div>
      ${two ? `<div class="brn-boxes" style="margin-left:auto">${box2('big', pair.big, bigW, lastBig ? _brnPh(lastBig.big) : '0')}${box2('small', pair.small, word, lastBig ? _brnPh(lastBig.small) : '0')}</div>` : `<div style="display:flex;gap:6px;align-items:center;margin-left:auto">
      <button onclick="brnStep(${_bmcQ(it.code)}, -1)" aria-label="ลด" style="width:36px;height:36px;border:1px solid var(--border);border-radius:10px;background:transparent;color:var(--text-primary);font-size:18px;cursor:pointer">−</button>
      <input inputmode="decimal" value="${esc(v)}" placeholder="${it.last_qty != null ? _brnPh(it.last_qty) : '0'}" oninput="brnSet(${_bmcQ(it.code)}, this.value)"
             style="width:68px;height:36px;text-align:center;font-size:16px;border:1px solid ${v !== '' ? 'var(--accent)' : 'var(--border-control)'};border-radius:10px;background:var(--bg-input);color:var(--text-primary)">
      <button onclick="brnStep(${_bmcQ(it.code)}, 1)" aria-label="เพิ่ม" style="width:36px;height:36px;border:1px solid var(--border);border-radius:10px;background:transparent;color:var(--text-primary);font-size:18px;cursor:pointer">+</button>
      <span class="text-xs brn-unit" style="width:64px;color:var(--text-secondary);overflow-wrap:anywhere">${esc(_brnUnitFull(it.count_unit || it.base_unit || ''))}</span>
      </div>`}
    </div>`;
  }).join('') : '<span class="text-xs" style="color:var(--text-muted)">ไม่มีรายการในโซนนี้</span>';
}

// PR-U1: the ladder of a count row ("1 แพ็ค (1 กก.) = 1,000 กรัม") only when it says something - both units named and different (plain text; _brnLadderTxt escapes it)
function _brnLadderPlain(it) {
  const cu = String(it.count_unit || '').trim(), bu = String(it.base_unit || '').trim();
  if (!cu || !bu || cu === bu || !(it.per_count_unit > 0)) return '';
  if (_brnUnitW(cu) === bu && Number(it.per_count_unit) === 1) return '';
  return `1 ${_brnUnitFull(cu)} = ${_bmcNum(it.per_count_unit)} ${bu}`;
}
function _brnLadderTxt(it) { return esc(_brnLadderPlain(it)); }
function _brnItem(code) { return _brnSheet.zones.flatMap(z => z.items).find(i => i.code === code); }
function _brnTotalTxt(it, p) {
  const t = _brnTotal2(it, p);
  return t === '' ? '' : esc(`รวม ${_brnSysTxt(Number(t), it.count_unit)}`);
}
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
    const l = { code, qty: v === '' ? null : Number(v), unit: it ? (it.count_unit || it.base_unit) : null };
    const p = it && it.unit_big ? _brnQty2[code] : null;            // 3.1: the boxes as typed; the server adds them up in base units
    if (p && v !== '') l.parts = [{ qty: p.big === '' ? null : Number(p.big), unit: it.unit_big.unit }, { qty: p.small === '' ? null : Number(p.small), unit: it.count_unit || it.base_unit }];
    return l;
  }).filter(l => l.qty === null || !isNaN(l.qty));
}

async function brnSave(submit, quiet) {
  if (!_brnCanCount()) { if (!quiet) _bmcStatus('brnStatus', _BRN_READONLY, 'var(--amber-ink)'); return; }
  if (!_brnSheet) return;
  clearTimeout(_brnTimer);
  if (submit) {
    // owner decision 2026-10-13 "ปิดงวดสิ้นเดือนต้องนับ 100%": a month-end count is refused while ANY item has no quantity (0 is a quantity;
    // the server refuses it too). The optional weekly check (cycle) holds only the class-A items, all of them required.
    const missing = _brnSheet.zones.flatMap(z => z.items).filter(i => !_brnCounted(i.code));
    if (missing.length) {
      const first = missing[0];
      await uiNotice(`ยังไม่ได้นับ ${missing.length} รายการ ${_brnSheet.mode === 'cycle' ? 'ตรวจนับหมุนเวียนต้องนับครบกลุ่มมูลค่าสูง' : 'ปิดงวดต้องตรวจนับครบทุกรายการ'} (เช่น ${missing.slice(0, 5).map(i => _brnNm(i)).join(', ')})`,
        { title: 'ส่งผลตรวจนับไม่ได้', ok: 'กลับไปนับ' });
      const z = _brnSheet.zones.find(zz => zz.items.some(i => i.code === first.code));      // go to the first one left
      if (z) { document.getElementById('brnQ').value = ''; document.getElementById('brnRequiredOnly').checked = false; _brnZone = z.zone; brnRender(); }
      return;
    }
    const msg = _brnSheet.mode === 'cycle' ? 'ส่งผลตรวจนับหมุนเวียนกลุ่มมูลค่าสูง? (ตรวจเสริม ไม่ใช่การปิดงวด)\nส่งแล้วแก้ไม่ได้ ต้องการแก้ให้ตรวจนับใหม่'
      : `ส่งผลตรวจนับสิ้นเดือนเป็น${_brnSheet.next_kind === 'begin' ? 'ต้นงวด' : 'ปลายงวด'}?\nส่งแล้วแก้ไม่ได้ ต้องการแก้ให้ตรวจนับใหม่`;
    if (!await uiConfirm(msg, { title: 'ยืนยันการส่งผลตรวจนับ', ok: 'ส่งผลตรวจนับ', cancel: 'กลับไปแก้' })) return;
  } else if (!_brnDirty && quiet) return;
  try {
    const r = await _bmcJson(`${API}/api/bom-center/stock/count?${_brnQSM()}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lines: _brnLines(), submit }) });
    _brnDirty = false;
    if (submit) {
      _brnCountStarted = null;                                  // the count is done: the pane shows it as done again
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
    await _bmcJson(`${API}/api/bom-center/stock/draft?${_brnQSM()}`, { method: 'DELETE' });
    await brnLoadSheet();
    _bmcStatus('brnStatus', 'ล้างร่างแล้ว', 'var(--text-muted)');
  } catch (e) { _bmcStatus('brnStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}

// PR-D6 · a history list of this page (counts, slips, orders, receipts, claims): what is finished is hidden by default, the rows still open are the list; the
// shared filter (uiStateFilter, 01-core.js) shows the finished ones. _brnHist(boxId, rows, isDone, rowHtml, noun, noneText) draws one box and remembers it
// so that a click on the chips of any box redraws all of them (the choice is one for the page).
const _brnHistReg = {};
function _brnHistDraw(id) {
  const h = _brnHistReg[id], box = document.getElementById(id);
  if (!h || !box) return;
  const f = uiStateFilter.get(), sp = uiStateFilter.split(h.rows, h.isDone), list = f === 'all' ? sp.all : sp[f];
  box.innerHTML = `<div id="${id}F"></div>` + (list.length ? list.map(h.row).join('') : uiStateFilter.empty(f, '_brnHistPick', h.noun));
  uiStateFilter.bar(id + 'F', { counts: sp.counts, f, pick: '_brnHistPick' });
}
function _brnHistPick(f) { uiStateFilter.set(f); Object.keys(_brnHistReg).forEach(_brnHistDraw); }
function _brnHist(id, rows, isDone, row, noun, none) {
  const box = document.getElementById(id);
  if (!box) return;
  if (!rows.length) { delete _brnHistReg[id]; box.innerHTML = none || 'ยังไม่มีข้อมูล'; return; }
  _brnHistReg[id] = { rows, isDone, row, noun };
  _brnHistDraw(id);
}
async function brnLoadHistory() {
  const box = document.getElementById('brnHistory');
  try {
    const d = await _bmcJson(`${API}/api/bom-center/stock/counts?kind=${_brnCountMode === 'cycle' ? 'cycle' : 'month'}&${_brnQS()}`);
    const KIND = { begin: 'ต้นงวด', end: 'ปลายงวด', spot: 'นับก่อนสั่ง (ระบบเดิม)', cycle: 'นับหมุนเวียน' };
    _brnHist('brnHistory', d.counts, c => c.status !== 'draft', c => `<div style="border-top:1px solid var(--border);padding:4px 0">
        ${esc(fmtDay(c.count_date))} · ${c.status === 'draft' ? _bmcChip('ร่าง', 'var(--amber-ink)') : (KIND[c.kind] || esc(c.kind))}${c.partial ? ' ' + _bmcChip('นับหมุนเวียนแทนการปิดงวด', 'var(--navy-ink)') : ''} · ${_bmcNum(c.lines, 0)} รายการ · ${esc(whoTh(c.counted_by) === '-' ? '' : whoTh(c.counted_by))}</div>`, 'ใบตรวจนับ');
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
let _brnIssAppend = false;    // PR-M7 · เบิกเพิ่ม: the confirmed slip of today takes more lines; the boxes are the quantities to ADD
let _brnIssKeep = null;       // the slip's own quantities while the append boxes are on screen
let _brnIssFill = null;       // PR-M7 · เบิกเหมือนเมื่อวาน: {date, n} of the last fill, named in the status line until the day changes
const _BRN_ISS_WD = ['อา.', 'จ.', 'อ.', 'พ.', 'พฤ.', 'ศ.', 'ส.'];
function _brnIssDm(iso) { return `${Number(String(iso).slice(8, 10))}/${Number(String(iso).slice(5, 7))}`; }
// 2.4: the days of a backlog as ranges: ['2026-10-08', … '2026-10-11'] -> "8–11/10"; apart: "8–9/10, 11/10"
function _brnIssRunTxt(a, b) {
  if (a === b) return _brnIssDm(a);
  return a.slice(5, 7) === b.slice(5, 7) ? `${Number(a.slice(8, 10))}–${_brnIssDm(b)}` : `${_brnIssDm(a)}–${_brnIssDm(b)}`;
}
function _brnIssRuns(days) {
  const runs = [];
  [...days].sort().forEach(d => {
    const last = runs[runs.length - 1];
    if (last && Date.parse(d + 'T00:00:00Z') - Date.parse(last[last.length - 1] + 'T00:00:00Z') === 864e5) last.push(d); else runs.push([d]);
  });
  const rest = runs.slice(4).reduce((n, r) => n + r.length, 0);
  return runs.slice(0, 4).map(r => _brnIssRunTxt(r[0], r[r.length - 1])).join(', ') + (rest ? ` และอีก ${rest} วัน` : '');
}
function _brnIssWd(iso) { return _BRN_ISS_WD[new Date(String(iso) + 'T12:00:00Z').getUTCDay()]; }
function _brnIssTime(iso) {      // a stored UTC stamp as the branch's clock (Bangkok) HH:MM (a naive stamp is UTC too, M1)
  const c = iso ? _bkkClock(iso) : null;
  return c ? c.hhmm : '';
}
function _brnIssCanReopen() { return hasModule('supply.ordering') || hasModule('bom.center'); }
function _brnIssEditable() { return !!(_brnIss && (_brnIss.editable || _brnIssAppend)) && _brnCanOrder(); }
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
  _brnIssAppend = false; _brnIssKeep = null;              // a load always leaves the เบิกเพิ่ม boxes
  if (!opts.quiet) {
    document.getElementById('brnIssList').innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลดรายการ…</span>';
    _bmcStatus('brnIssStatus', '');                       // the previous day's "บันทึกร่างแล้ว" does not belong to this one
    _brnIssFill = null;
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
    brnFormMount('iss');                              // PR-SI: the Excel form under the day strip
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
  box.innerHTML = out.join('');            // PR-D6: no "ยืนยันครบ N วัน" fold - the confirmed days are under เสร็จแล้ว in ประวัติใบตัดเบิก below
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

// 3.2 · the short slip (owner decision 2026-10-12): the list a branch opens with holds the items it still works with (`in_default`:
// used in the last 14 days, system stock above 0, ต้องนับ, or already typed on this slip); "แสดงทั้งหมด (N รายการ)" shows the rest.
// The zone chips follow the list; a search always looks at every item. The choice is remembered on this device.
const _BRN_ISS_ALL_KEY = 'oshinei_brn_iss_all';
let _brnIssAll = (() => { try { return localStorage.getItem(_BRN_ISS_ALL_KEY) === '1'; } catch (e) { return false; } })();
function _brnIssAllOn() { return _brnIssAll || !(_brnIss && _brnIss.items_default > 0); }       // a branch with nothing in the short list sees everything
function _brnIssShow(it) { return _brnIssAllOn() || it.in_default !== false || _brnIssAny(it.code); }
function _brnIssZonesShown() {
  const s = _brnIss;
  return s ? s.zones.map(z => Object.assign({}, z, { items: z.items.filter(_brnIssShow) })).filter(z => z.items.length) : [];
}
function brnIssToggleAll() {
  _brnIssAll = !_brnIssAll;
  try { localStorage.setItem(_BRN_ISS_ALL_KEY, _brnIssAll ? '1' : '0'); } catch (e) { /* the choice then lasts until the page is closed */ }
  brnIssRender();
}
function _brnIssFilterBar() {
  const box = document.getElementById('brnIssFilter');
  if (!box || !_brnIss) return;
  const all = _brnIss.zones.flatMap(z => z.items), nShort = all.filter(i => i.in_default !== false || _brnIssAny(i.code)).length;
  if (nShort >= all.length || !(_brnIss.items_default > 0)) { box.innerHTML = ''; return; }
  const btn = t => `<button type="button" class="brn-link" onclick="brnIssToggleAll()" style="min-height:44px">${t}</button>`;
  box.innerHTML = _brnIssAll
    ? `<span style="color:var(--text-muted)">แสดงทุกรายการ ${all.length} รายการ</span> ${btn(`แสดงเฉพาะรายการที่ใช้ (${nShort} รายการ)`)}`
    : `<span style="color:var(--text-muted)" title="ซ่อนรายการที่ไม่ได้ใช้ใน 14 วัน ไม่มีคงเหลือตามระบบ และไม่ต้องนับ">แสดง ${nShort} จาก ${all.length} รายการ</span> ${btn(`แสดงทั้งหมด (${all.length} รายการ)`)}`;
}

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
// U3 (owner 2026-10-22) · why an item is on the slip a branch opens with (issues.default_list), in plain words, for its "ดูที่มา"
function _brnIssWhy(it) {
  const out = [];
  if (it.recent) out.push('เบิกใน 14 วันที่ผ่านมา');
  else if (it.used_recent) out.push(stkWord('สูตรอาหารใช้ใน 14 วันที่ผ่านมา', 'ใช้ใน 14 วันที่ผ่านมา'));
  if (Number(it.system_qty) > 0) out.push('มีคงเหลือตามระบบ');
  if (it.required) out.push(it.monitored ? 'รายการบังคับสั่ง ต้องนับทุกงวด' : 'กลุ่มมูลค่าสูง ต้องนับทุกงวด');
  return out;
}
// "ต้องนับ" on a slip row only when it is the reason the row is there: an item the branch has not issued or used lately and holds no stock
// of. On every other row (most of a branch's stocked items are class A or บังคับสั่ง) the chip said nothing; the reason sits in "ดูที่มา".
function _brnIssCountChip(it) {
  return !!it.required && !it.recent && !it.used_recent && !(Number(it.system_qty) > 0) && !_brnIssAny(it.code);
}
function _brnIssZoneChips() {
  const s = _brnIss;
  if (!s) return;
  const searching = !!((document.getElementById('brnIssQ') || {}).value || '').trim();
  const shown = _brnIssZonesShown();
  if (shown.length && !shown.some(z => z.zone === _brnIssZone)) _brnIssZone = shown[0].zone;       // the zone on screen is one the short list still has
  document.getElementById('brnIssZones').innerHTML = shown.map(z => {
    const n = z.items.filter(i => _brnIssAny(i.code)).length;
    const ok = !_brnIssAppend && _brnIssZoneDone(z);
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
    `${_brnIssAppend ? 'เบิกเพิ่ม' : 'กรอกแล้ว'} <b style="color:var(--text-primary)">${all.filter(i => _brnIssAny(i.code)).length}</b> รายการ · ของเสีย <b style="color:${nWaste ? 'var(--amber-ink)' : 'var(--text-primary)'}">${nWaste}</b>`;
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
    (st.appends ? ' ' + _bmcChip(`เบิกเพิ่ม ${st.appends} ครั้ง`, 'var(--navy-ink)') : '') +
    (s.slip && s.slip.reopen_note && !confirmed ? ` <span class="text-xs" style="color:var(--amber-ink)">เปิดแก้ไขโดย ${esc(whoTh(s.slip.reopened_by))}: ${esc(s.slip.reopen_note)}</span>` : '');
  // a banner = ONE short line (the state) + a "?" with the reason - [ink, line, the reason as html]
  const parts = [];
  if (s.backdated && !confirmed) parts.push(['var(--amber-ink)', 'ใบย้อนหลังนับเป็นล่าช้า']);
  if (_brnIssAppend) parts.push(['var(--navy-ink)', 'ที่กรอกจะบวกเพิ่มในใบของวันนี้']);
  else if (confirmed) parts.push(['var(--text-secondary)', 'ยืนยันแล้ว แก้ไขไม่ได้',
    '<p>ยืนยันแล้ว แก้ไขไม่ได้ · ต้องการแก้ แจ้งประสานงานขาย' + (s.can_append && _brnCanOrder() ? ' · ลืมรายการ กด “เบิกเพิ่ม” เพื่อบวกเพิ่มในใบของวันนี้' : '') + '</p>']);
  const md = s.missing_days || [];
  const nEst = (s.estimated_days || []).length;                          // 1.3 · the days without a slip that were taken out at the BoM of the sales
  if (md.length) parts.push(['var(--amber-ink)', `ใบที่ค้าง ${md.length} วัน (${_brnIssRuns(md)})`,
    '<p>เลือกวันที่ยังไม่ทำจากแถบวันที่</p>' + (nEst ? `<p>คงเหลือตามระบบหัก${_brnEstHow(s.estimate_basis)} ${nEst} วัน (${esc(_brnIssRuns(s.estimated_days))}) ไว้ก่อน เมื่อยืนยันใบของวันนั้น ตัวเลขในใบจะใช้แทน</p>` : '')]);
  const banner = document.getElementById('brnIssBanner');
  banner.style.display = parts.length ? '' : 'none';
  banner.innerHTML = parts.map(([ink, t, why], i) => `<div class="text-xs mb-1" style="border:1px solid ${ink};border-radius:var(--r-sm);padding:6px 10px;color:${ink}">${esc(t)}${why ? ' ' + uiQ('brn:iss' + i, 'ใบตัดเบิก', why) : ''}</div>`).join('');
  _brnIssZoneChips();
  _brnIssProgress();
  _brnIssFilterBar();
  const all = s.zones.flatMap(z => z.items);
  const zone = _brnIssZonesShown().find(z => z.zone === _brnIssZone) || { items: [] };
  const q = (document.getElementById('brnIssQ').value || '').trim().toLowerCase();
  const usedOnly = document.getElementById('brnIssUsedOnly').checked;
  const items = (q ? all : zone.items).filter(i => (!usedOnly || _brnIssAny(i.code)) && (!q || (i.code + ' ' + i.name + ' ' + (i.short_name || '')).toLowerCase().includes(q)));
  // one box: the caption under it names the box and its unit; an empty box hints at the previous confirmed slip ("เมื่อวาน 3")
  const prevWord = s.prev_date && s.prev_date === new Date(new Date(s.date + 'T12:00:00Z').getTime() - 86400000).toISOString().slice(0, 10) ? 'เมื่อวาน' : 'ครั้งก่อน';
  // PR-M7 · เบิกเหมือนเมื่อวาน: one button in the head card while the slip is a draft and a previous confirmed slip has quantities
  const quick = document.getElementById('brnIssQuick');
  if (quick) {
    const nPrev = ed && !_brnIssAppend ? all.filter(i => i.prev_used || i.prev_waste).length : 0;
    quick.style.display = nPrev ? '' : 'none';
    quick.innerHTML = nPrev ? `<button type="button" id="brnIssFillBtn" onclick="brnIssFillPrev()">เบิกเหมือน${prevWord === 'เมื่อวาน' ? 'เมื่อวาน' : 'ใบครั้งก่อน'}</button>
      <span class="text-xs" style="color:var(--text-muted)">ใบวันที่ ${esc(_brnIssDm(s.prev_date))} · ${nPrev} รายการ · เติมเฉพาะช่องที่ยังว่าง</span>` : '';
  }
  const box = (it, f, label, ink, v, prev, unit) => {
    const id = (f === 'used' ? 'brnIssU_' : 'brnIssW_') + esc(it.code);
    const bad = _brnIssBadVal(v);
    return `<div class="brn-box"><input id="${id}" class="brn-qty" inputmode="decimal" autocomplete="off" value="${esc(v)}" ${ed ? '' : 'disabled'} aria-label="${label} ${esc(_brnNm(it))}"
        placeholder="${_brnIssAppend ? 'เพิ่ม' : prev ? prevWord + ' ' + _brnPh(prev) : ''}" oninput="brnIssSetField(${_bmcQ(it.code)}, '${f}', this.value)" ${_brnIssAppend ? `onchange="brnIssWholeUp(${_bmcQ(it.code)}, '${f}', this)"` : ''} ${bad ? 'aria-invalid="true"' : ''}
        style="border-color:${bad ? 'var(--bad-ink)' : (v !== '' ? ink : 'var(--border-control)')}"><span class="brn-cap">${label} · ${esc(_brnUnitFull(unit))}</span></div>`;
  };
  document.getElementById('brnIssList').innerHTML = items.length ? items.map(it => {
    const v = _brnIssQty[it.code] || { used: '', waste: '' };
    const unit = it.count_unit || it.base_unit || '', unitF = _brnUnitFull(unit);
    const chip = _brnIssCountChip(it) ? _bmcChip('ต้องนับ', 'var(--amber-ink)') : '';
    const src = _brnSrc(it, [_brnIssWhy(it).join(' · '), _brnLadderPlain(it),
      it.last_qty != null ? `นับล่าสุด ${_brnNice(it.last_qty)} ${_brnUnitFull(it.last_unit || unit)}` : '']);
    const lead = [chip, src].filter(Boolean).join(' ');
    const sys = it.system_qty == null ? ''
      : `${lead ? '<span class="brn-sys-sep"> · </span>' : ''}<span class="brn-sys">คงเหลือตามระบบ <b style="white-space:nowrap;color:${it.system_qty < 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">${esc(_brnSysTxt(it.system_qty, unit))}</b></span>`
        + (it.estimate_matters && nEst ? ' ' + _bmcChip(`${_brnEstWord(s.estimate_basis)} ${nEst} วัน`, 'var(--amber-ink)') : '');
    const avg = it.normal_qty != null ? `<span class="brn-avg">${lead || sys ? ' · ' : ''}ปกติ ${_brnNice(it.normal_qty)} ${esc(unitF)}</span>` : '';
    // เบิกเพิ่ม: what the confirmed slip already holds for the item, so the box holds only the quantity to add
    const have = _brnIssAppend && (it.used > 0 || it.waste > 0)
      ? `<div class="text-[11px]" style="color:var(--navy-ink)">ในใบแล้ว${it.used > 0 ? ` เบิกใช้ ${_brnNice(it.used)}` : ''}${it.waste > 0 ? ` ของเสีย ${_brnNice(it.waste)}` : ''} ${esc(unitF)}</div>` : '';
    return `<div class="brn-row" style="border-top:1px solid var(--border);padding:8px 0">
      <div style="display:flex;gap:8px;align-items:flex-start">
        <div class="brn-namecol" style="flex:1 1 0;min-width:0">
          <div class="brn-name" title="${esc(it.name)}" style="color:var(--text-primary);font-weight:600">${esc(_brnNm(it))}</div>
          <div class="brn-sub text-[11px]" style="color:var(--text-muted)">${lead}${sys}${avg}</div>${have}</div>
        <div class="brn-boxes">${box(it, 'used', 'เบิกใช้', 'var(--accent)', v.used, it.prev_used, unit)}${box(it, 'waste', 'ของเสีย', 'var(--amber-ink)', v.waste, it.prev_waste, unit)}</div></div>
      <div id="brnIssE_${esc(it.code)}" class="brn-err" role="alert">${_brnIssBad(it.code) ? _BRN_QTY_ERR : ''}</div>
      <div id="brnIssX_${esc(it.code)}">${_brnIssReasonBox(it.code)}</div></div>`;
  }).join('') : `<span class="text-xs" style="color:var(--text-muted)">${usedOnly ? 'ยังไม่มีรายการที่กรอก' : 'ไม่มีรายการในโซนนี้'}</span>`;
  brnIssActions();
}

// เบิกเพิ่ม: the quantity to add is always a whole number, rounded UP (3.2 → 4); float dust such as 3.0000001 stays 3
function _brnWholeUp(v) {
  const n = Number(String(v || '').replace(',', '.'));
  return Number.isFinite(n) && n > 0 ? String(Math.ceil(Math.round(n * 1e6) / 1e6)) : String(v || '');
}
function brnIssWholeUp(code, field, el) {
  if (!_brnIssAppend) return;
  const w = _brnWholeUp(el.value);
  if (w !== el.value) { el.value = w; brnIssSetField(code, field, w); }
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
  if (_brnIssAppend) return;                                            // the เบิกเพิ่ม boxes are sent by their own button, never autosaved
  _brnIssDirty = true;
  clearTimeout(_brnIssTimer);
  _brnIssTimer = setTimeout(() => brnIssSave(false, true), 2500);       // autosave the draft — a lost signal loses nothing
}

// PR-M7 · เบิกเหมือนเมื่อวาน: every EMPTY box takes the quantity of the previous confirmed slip (a box already typed is left
// alone); the waste comes with its reason. Nothing is sent here: the normal autosave stores the draft, and every box stays editable.
function brnIssFillPrev() {
  const s = _brnIss;
  if (!s || !_brnIssEditable() || _brnIssAppend) return;
  const dm = _brnIssDm(s.prev_date);
  let n = 0;
  s.zones.flatMap(z => z.items).forEach(it => {
    if (!(it.prev_used > 0) && !(it.prev_waste > 0)) return;
    const q = _brnIssQty[it.code] || (_brnIssQty[it.code] = { used: '', waste: '', reason: '', note: '' });
    let did = false;
    if (it.prev_used > 0 && q.used === '') { q.used = String(it.prev_used); did = true; }
    if (it.prev_waste > 0 && q.waste === '') {
      q.waste = String(it.prev_waste); did = true;
      if (!q.reason && it.prev_waste_reason) q.reason = it.prev_waste_reason;
      if (!q.note && it.prev_note) q.note = it.prev_note;
    }
    if (did) n++;
    else if (!_brnIssAny(it.code) && q.used === '' && q.waste === '') delete _brnIssQty[it.code];
  });
  if (!n) { _bmcStatus('brnIssStatus', 'ไม่มีช่องว่างที่ต้องเติม · ช่องที่กรอกแล้วไม่ถูกเปลี่ยน', 'var(--text-muted)'); return; }
  _brnIssFill = { dm, n };
  brnIssRender();
  _bmcStatus('brnIssStatus', `เติมจากใบวันที่ ${dm} แล้ว ${n} รายการ · แก้เฉพาะรายการที่ต่าง`, 'var(--good-ink)');
  _brnIssDirty = true;
  clearTimeout(_brnIssTimer);
  _brnIssTimer = setTimeout(() => brnIssSave(false, true), 2500);
}
const _brnIssFillNote = () => (_brnIssFill ? ` · เติมจากใบวันที่ ${_brnIssFill.dm} แล้ว ${_brnIssFill.n} รายการ · แก้เฉพาะรายการที่ต่าง` : '');

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
    _bmcStatus('brnIssStatus', invalid.length ? `${_BRN_QTY_ERR}: ${_brnNm(it)}`
      : q.reason === 'other' ? `ระบุคำอธิบายของสาเหตุ “อื่น ๆ”: ${_brnNm(it)}` : `เลือกสาเหตุของของเสีย: ${_brnNm(it)}`,
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
    // 1.4 · the high-value items the sales of the day say were used and the slip leaves blank (8 at most, the most valuable first)
    let blankTxt = '';
    try {
      const qs0 = _brnQS();
      const b = await _bmcJson(`${API}/api/bom-center/issues/blank${qs0 ? '?' + qs0 : ''}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date: _brnIss.date, codes: nAny.map(l => l.code) }) });
      if (b && b.items && b.items.length) blankTxt = `\nรายการที่ขายแล้วแต่ยังว่าง: ${b.items.map(_brnNm).join(', ')}${b.total > b.items.length ? ` และอีก ${b.total - b.items.length} รายการ` : ''}`;
    } catch (e) { /* the list is a help: the confirm goes on without it */ }
    if (!await uiConfirm((late ? 'ใบย้อนหลังนับเป็นล่าช้า\n' : '')
        + `ยืนยันใบตัดเบิก ${fmtDayShort(_brnIss.date)}\nกรอก ${nAny.length} รายการ · ของเสีย ${nWaste} รายการ\nยังไม่ได้กรอก ${Math.max(nAll - nAny.length, 0)} รายการ ถือว่าไม่ได้เบิก (คงเหลือตามระบบไม่ลดลง)${blankTxt}\nยืนยันแล้วแก้ไขไม่ได้ ต้องการแก้ แจ้งประสานงานขาย`,
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
      else _bmcStatus('brnIssStatus', `บันทึกร่างแล้ว ${clock()}${_brnIssFillNote()}`, 'var(--good-ink)');
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
  else if (_brnIssAppend) box.innerHTML = btn('บันทึกการเบิกเพิ่ม', 'brnIssAppendSave()', true) + btn('ยกเลิก', 'brnIssAppendStop()');
  else if (confirmed) box.innerHTML = _bmcChip(_BRN_ISS_ST_LONG[st.status], st.status === 'late' ? 'var(--amber-ink)' : 'var(--good-ink)')
    + (_brnIss.can_append ? btn('เบิกเพิ่ม', 'brnIssAppendStart()', true) : '')
    + `<span class="text-xs" style="color:var(--text-secondary)">${_brnIss.can_append ? 'แก้รายการเดิม แจ้งประสานงานขาย' : 'ต้องการแก้ แจ้งประสานงานขายให้เปิดแก้ไข'}</span>`
    + (_brnIssCanReopen() ? btn('เปิดแก้ไข', 'brnIssReopen()') : '');
  else box.innerHTML = btn('บันทึกร่าง', 'brnIssSave(false)') + btn('ยืนยันใบตัดเบิก', 'brnIssSave(true)', true);
}

// PR-M7 · เบิกเพิ่ม: the confirmed slip of TODAY takes more lines without a reopen. The list switches to empty boxes (the quantities to ADD),
// the sticky row is "บันทึกการเบิกเพิ่ม" + "ยกเลิก", and the save adds the quantities to the slip on the server (logged as เบิกเพิ่ม).
function brnIssAppendStart() {
  if (!_brnIss || !_brnIss.can_append || !_brnCanOrder()) return;
  _brnIssKeep = _brnIssQty;
  _brnIssQty = {};
  _brnIssAppend = true;
  _bmcStatus('brnIssStatus', '');
  brnIssRender();
  window.scrollTo({ top: 0 });
}
function brnIssAppendStop() {
  if (!_brnIssAppend) return;
  _brnIssAppend = false;
  _brnIssQty = _brnIssKeep || {};
  _brnIssKeep = null;
  _bmcStatus('brnIssStatus', '');
  brnIssRender();
}
async function brnIssAppendSave() {
  if (!_brnIssAppend || !_brnIss) return;
  Object.values(_brnIssQty).forEach(q => { q.used = _brnWholeUp(q.used); q.waste = _brnWholeUp(q.waste); });   // a box still in focus is rounded too
  const { lines, incomplete, invalid } = _brnIssLines();
  if (invalid.length || incomplete.length) {                       // say which row to fix and go to it
    const code = invalid[0] || incomplete[0];
    const it = _brnIssItem(code), q = _brnIssQty[code] || {};
    _bmcStatus('brnIssStatus', invalid.length ? `${_BRN_QTY_ERR}: ${_brnNm(it)}`
      : q.reason === 'other' ? `ระบุคำอธิบายของสาเหตุ “อื่น ๆ”: ${_brnNm(it)}` : `เลือกสาเหตุของของเสีย: ${_brnNm(it)}`,
      invalid.length ? 'var(--bad-ink)' : 'var(--amber-ink)');
    const el = document.getElementById(invalid.length ? 'brnIssU_' + code : 'brnIssR_' + code) || document.getElementById('brnIssW_' + code) || document.getElementById('brnIssU_' + code);
    if (el) { el.scrollIntoView({ block: 'center' }); el.focus(); }
    return;
  }
  const add = lines.filter(l => l.qty_used > 0 || l.qty_waste > 0);
  if (!add.length) { _bmcStatus('brnIssStatus', 'ใส่จำนวนที่เบิกเพิ่มอย่างน้อย 1 รายการ', 'var(--amber-ink)'); return; }
  if (!await uiConfirm(`บันทึกการเบิกเพิ่ม ${add.length} รายการ ในใบตัดเบิก ${_brnIssDm(_brnIss.date)}?\nจำนวนที่กรอกจะบวกเพิ่มในใบที่ยืนยันแล้ว และบันทึกไว้ในประวัติของใบ`,
      { title: 'ยืนยันการเบิกเพิ่ม', ok: 'บันทึก', cancel: 'ยกเลิก' })) return;
  _bmcStatus('brnIssStatus', 'กำลังบันทึก…', 'var(--text-muted)');
  try {
    const qs = _brnQS();
    const r = await _bmcJson(`${API}/api/bom-center/issues/append${qs ? '?' + qs : ''}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date: _brnIss.date, lines: add }) });
    _brnPlan = null;                                              // the system stock moved: the order suggestion is calculated again
    await brnIssLoad({ quiet: true });
    _bmcStatus('brnIssStatus', `✓ เบิกเพิ่มแล้ว ${r.added} รายการ`, 'var(--good-ink)');
  } catch (e) { _bmcStatus('brnIssStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 220), 'var(--bad-ink)'); }
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
    _brnHist('brnIssHistory', d.slips, x => x.status !== 'draft', x => {
      const reopens = x.status === 'draft' ? x.confirm_count : Math.max(x.confirm_count - 1, 0);
      const ink = x.status === 'confirmed' ? 'var(--good-ink)' : 'var(--amber-ink)';
      return `<div style="border-top:1px solid var(--border);padding:4px 0"><a href="#" onclick="brnIssSetDate('${x.date}');return false" style="color:var(--accent);font-weight:600">${esc(fmtDay(x.date))}</a>
        · ${_bmcChip(x.status_th, ink)} · ${_bmcNum(x.lines, 0)} รายการ · ของเสีย ${_bmcNum(x.waste_lines, 0)}
        ${x.confirmed_by && x.status !== 'draft' ? ' · โดย ' + esc(whoTh(x.confirmed_by)) : ''}${x.confirmed_at && x.status !== 'draft' ? ' · เวลา ' + esc(_brnIssTime(x.confirmed_at)) : ''}
        ${x.late && x.status !== 'late' ? ' ' + _bmcChip('ยืนยันล่าช้า', 'var(--amber-ink)') : ''}${reopens ? ' ' + _bmcChip(`เปิดแก้ไข ${reopens} ครั้ง`, 'var(--text-muted)') : ''}</div>`;
    }, 'ใบ');
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
  _brnCountMode = null;                                  // the count that is due for THIS branch
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
  const preview = x.lines.slice(0, 3).map(l => esc(_brnNm(l))).join(', ') + (x.lines.length > 3 ? ` และอีก ${x.lines.length - 3} รายการ` : '');
  const rows = x.lines.map(l => `<tr style="border-top:1px solid var(--border)">
      <td class="py-1 pr-2" style="color:var(--text-primary)">${esc(_brnNm(l))}${_brnSrc(l) ? `<div class="text-[10.5px]" style="color:var(--text-muted)">${_brnSrc(l)}</div>` : ''}</td>
      <td class="py-1 pr-2 text-right" style="white-space:nowrap;color:var(--text-primary)">${_bmcNum(l.qty)}</td>
      <td class="py-1" style="color:var(--text-secondary)">${esc(l.unit_word || l.unit || '')}${l.pieces ? ` · ${_bmcNum(l.pieces, 0)} ${esc(l.pieces_unit || '')}` : ''}${l.qty_base == null ? ' ' + _bmcChip('แปลงหน่วยไม่ได้', 'var(--bad-ink)') : ''}</td></tr>`).join('');
  // PR-U2: what the analyst answered to "ขนาดบรรจุไม่ตรง" on this order (the reason is read here when the answer is no)
  const packs = (x.pack_reports || []).map(r => `<div class="text-xs mt-1" style="color:${r.status === 'rejected' ? 'var(--amber-ink)' : r.status === 'applied' ? 'var(--good-ink)' : 'var(--text-secondary)'}">${
    r.status === 'rejected' ? `แจ้งขนาดบรรจุ: ไม่ใช้ — ${esc(r.decision_note || '')}` : `แจ้งขนาดบรรจุ: ${esc(r.name || r.ingredient_code)} ${esc(r.text)} · ${esc(r.status_th)}`}</div>`).join('');
  const del = x.source === 'claim' ? _bmcChip('ยกเลิกไม่ได้ (เกิดจากการอนุมัติเคลม)', 'var(--text-muted)')
    : x.locked ? _bmcChip('อยู่ในงวดที่ปิดแล้ว', 'var(--text-muted)')
    : `<button onclick="brnRecvDelete(${x.receipt_id})" style="border:1px solid var(--bad-ink);color:var(--bad-ink);background:transparent;border-radius:var(--r-sm);padding:4px 12px;font-size:12px;cursor:pointer;white-space:nowrap">ยกเลิกใบรับสินค้า</button>`;
  return `<div style="border:1px solid var(--border);border-radius:var(--r-md, 12px);padding:10px 12px;margin-bottom:8px;background:var(--bg-card, transparent)">
    <div style="display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap">
      <div style="flex:1 1 200px;min-width:0">
        <div class="text-sm" style="color:var(--text-primary);font-weight:600">${esc(fmtDay(x.receipt_date))} ${x.ref_th ? '' : _bmcChip(src[0], src[1])}</div>
        <div class="text-xs mt-0.5" style="color:var(--text-secondary)">${x.ref_th ? esc(x.ref_th) + ' · ' : x.ref ? esc(x.ref) + ' · ' : ''}<b>${x.lines.length}</b> รายการ${x.entered_by && x.entered_by !== '-' ? ' · บันทึกโดย ' + esc(whoTh(x.entered_by)) : ''}
          ${bad ? ' · ' + _bmcChip(bad + ' รายการแปลงหน่วยไม่ได้', 'var(--bad-ink)') : ''}</div>
        <div class="text-xs mt-1" style="color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${preview}</div>
        ${packs}
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
  if (dl) dl.innerHTML = _brnAllItems().map(i => `<option value="${esc(i.code)} — ${esc(_brnNm(i))}"></option>`).join('');
  brnRenderRecvLines();
  if (_brnNeedBranch('brnRecvHistory')) return;
  brnLoadRecvOpen();
  brnLoadClaims();
  try {
    const r = await _bmcJson(`${API}/api/bom-center/stock/receipts?${_brnQS()}`);
    // PR-D6: a receipt is finished unless a line of it could not be converted to a unit (shared filter)
    _brnHist('brnRecvHistory', r.receipts, x => !x.lines.some(l => l.qty_base == null), _brnRecvCard, 'ใบรับสินค้า', '<div class="text-xs" style="color:var(--text-muted)">ยังไม่มีบันทึกรับสินค้า</div>');
  } catch (e) { document.getElementById('brnRecvHistory').textContent = String(e.message || e); }
}
function _brnPicked() {
  const v = (document.getElementById('brnRecvItem').value || '').trim();
  const code = v.split(' — ')[0].trim();
  return _brnAllItems().find(i => i.code === code || i.name === v || (i.short_name && i.short_name === v));
}
function brnRecvPicked() {
  const it = _brnPicked();
  document.getElementById('brnRecvUnit').textContent = it ? (it.count_unit || it.base_unit || '') : '';
  // PR-U2: goods sold by weight whose pieces differ — the pieces counted may be given (optional here)
  const w = document.getElementById('brnRecvPcsW');
  if (w) { w.style.display = it && it.pieces_unit ? '' : 'none'; document.getElementById('brnRecvPcsU').textContent = it && it.pieces_unit ? it.pieces_unit : ''; }
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
  let pcs = null;
  const pEl = document.getElementById('brnRecvPcs'), pRaw = pEl ? String(pEl.value || '').trim().replace(',', '.') : '';
  if (it.pieces_unit && pRaw !== '') {
    pcs = Number(pRaw);
    if (!Number.isInteger(pcs) || pcs < 1) { bad(pEl, `จำนวน${it.pieces_unit}ต้องเป็นจำนวนเต็มตั้งแต่ 1 ขึ้นไป`); return; }
  }
  const same = _brnRecv.find(l => l.code === it.code && l.unit === unit);
  if (same) { same.qty += q; if (pcs) same.pieces = (same.pieces || 0) + pcs; }
  else _brnRecv.push({ code: it.code, name: _brnNm(it), qty: q, unit, pieces: pcs, pieces_unit: it.pieces_unit || null });
  document.getElementById('brnRecvItem').value = '';
  document.getElementById('brnRecvQty').value = '';
  if (pEl) pEl.value = '';
  document.getElementById('brnRecvUnit').textContent = '';
  brnRecvPicked();
  _bmcStatus('brnRecvStatus', '');
  brnRenderRecvLines();
  document.getElementById('brnRecvItem').focus();
}
function brnRenderRecvLines() {
  const box = document.getElementById('brnRecvLines');
  if (!box) return;
  box.innerHTML = _brnRecv.length ? _brnRecv.map((l, i) => `<div style="display:flex;gap:8px;align-items:center;border-top:1px solid var(--border);padding:5px 0">
      <span style="flex:1">${esc(l.name)} <span class="text-[11px]" style="color:var(--text-muted)">${_brnSrc(l)}</span></span>
      <b>${_bmcNum(l.qty)}</b> <span class="text-xs" style="color:var(--text-secondary)">${esc(l.unit || '')}${l.pieces ? ` · ${_bmcNum(l.pieces, 0)} ${esc(l.pieces_unit || '')}` : ''}</span>
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
        lines: _brnRecv.map(l => ({ code: l.code, qty: l.qty, unit: l.unit, pieces: l.pieces || null })) }) });
    _brnRecv = [];
    document.getElementById('brnRecvRef').value = '';
    _bmcStatus('brnRecvStatus', `✓ บันทึกรับสินค้า ${r.lines} รายการ`, 'var(--good-ink)');
    brnLoadRecv();
  } catch (e) { _bmcStatus('brnRecvStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
// 3.6 · cancelling a receipt needs a reason (at least 3 characters); the server keeps the whole receipt with it and Sales Support reads
// the list of the last 7 days (ใบรับสินค้าที่สาขายกเลิก)
async function brnRecvDelete(id) {
  const reason = await uiPrompt('ยกเลิกใบรับสินค้านี้ คงเหลือตามระบบลดลงเท่าจำนวนที่รับในใบ\nประสานงานขายอ่านเหตุผลที่ระบุได้ · เหตุผล (จำเป็น)', '',
    { title: 'ยกเลิกใบรับสินค้า', okText: 'ยกเลิกใบรับสินค้า', required: true, placeholder: 'เช่น บันทึกซ้ำ หรือบันทึกผิดวัน' });
  if (reason == null) return;
  if (reason.length < 3) { _bmcStatus('brnRecvStatus', 'ต้องระบุเหตุผลอย่างน้อย 3 ตัวอักษร', 'var(--bad-ink)'); return; }
  try {
    await _bmcJson(`${API}/api/bom-center/stock/receipt/${id}?${_brnQS()}`, { method: 'DELETE', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }) });
    brnLoadRecv();
  } catch (e) { _bmcStatus('brnRecvStatus', 'ยกเลิกไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}

// ── receiving an order (S8, reworked 2026-10-07; PR-M4 2026-10-10): the orders still to be received are listed on the
// รับสินค้า pane, whatever today's cycle is. The form opens with ONE question — "รับครบตามที่คลังส่งทุกรายการ?":
//   รับครบ            → one line "รับครบ N รายการ" + the acknowledgement + บันทึกรับสินค้า
//   มีรายการไม่ครบ     → search and pick the lines that are short; only those open (รับจริง · ส่วนต่าง · สาเหตุ · หมายเหตุ)
// The server contract is unchanged: every line is sent, a line that was not picked at the quantity the warehouse sent.
let _brnRecvOrd = null;
let _brnRcv = { mode: null, picked: [], vals: {}, q: '', pcs: {}, pk: {}, wts: {}, auto: [], autoMode: false };      // the form's working state: the answer, the picked lines, what was typed, the pieces counted, the pack-size reports, the weights weighed (PR-W) and the lines picked by a weight alone
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
// PR-W: the unit WORD of the item's receiving unit (the payload's `unit_word`: "กล่อง (10 แพ็ค)" -> กล่อง, "กิโลกรัม", "กระสอบ"), never a fixed "แพ็ค"
function _brnRcvUnit(l) { return _brnRcvF(l) > 1 ? (l.show_unit || l.unit_word || '') : (l.unit_word || (l.order_unit === 'base' ? '' : 'แพ็ค')); }
// PR-U2: a line with no pack size is typed and read in กิโลกรัม / ลิตร (bmcTypeF); a line received by weight keeps its own boxes
function _brnRcvF(l) { return l && !_brnWtLine(l) ? bmcTypeF(l) : 1; }
function _brnRcvQ(l, q) { const f = _brnRcvF(l); return _bmcNum(Number(q || 0) / f, f > 1 ? 3 : 2); }
function _brnRcvLines() { return _brnRecvOrd ? _brnRecvOrd.lines.filter(l => _brnRcvSent(l) > 0 || l.received_units) : []; }
function _brnRcvLine(code) { return _brnRcvLines().find(l => l.code === code); }
function _brnRcvRaw(code) { return String(((_brnRcv.vals[code] || {}).q) ?? '').trim().replace(',', '.'); }
// the "รับจริง" of a line: the quantity typed on a picked line, the quantity sent on any other; null when it is empty or not a number
function _brnRcvTyped(l) {
  if (_brnWtLine(l)) {                                           // PR-W: a line received by weight - the weight weighed IS the quantity (in the order unit)
    const w = _brnWtVal(l);
    return w === null ? null : Math.round(w * (l.pieces_parent_factor || 1) / (l.base_per_order_unit || 1) * 1e6) / 1e6;
  }
  if (!_brnRcv.picked.includes(l.code)) return _brnRcvSent(l);
  const raw = _brnRcvRaw(l.code);
  if (raw === '') return null;
  const q = bmcFromTyped(raw, _brnRcvF(l));
  return Number.isFinite(q) ? q : null;
}
// the gap of a line = sent − received; below zero it is an over-delivery (a note only, nothing to claim)
function _brnRcvGap(l) {
  const q = _brnRcvTyped(l);
  if (q === null) return 0;
  if (_brnWtLine(l) && _brnWtShort(l).within) return 0;           // PR-W: up to the tolerance below the weight sent is no shortage
  return Math.round((_brnRcvSent(l) - q) * 1e6) / 1e6;
}

// ── PR-W: the weight weighed at receiving ──────────────────────────────────────────────────────────────────
// A line sold by weight whose pieces differ (a whole salmon) is received BY WEIGHT: the branch weighs what arrived (the box is
// prefilled with the weight the warehouse sent) and counts the pieces. The weight IS the received quantity (converted to the line's
// order unit), so the average weight per piece learned from receipts is the branch's own. A shortfall of up to the tolerance
// (3 % of the weight sent, orders.WEIGHT_TOLERANCE_PCT) is accepted with no claim; a bigger one makes the line a short line
// (reason + claim). More than was sent is accepted. The server checks the same (orders.receive).
function _brnWtLine(l) { return !!l.pieces_unit && l.sent_weight != null; }
function _brnWtTol() { return _brnRecvOrd && _brnRecvOrd.weight_tolerance_pct != null ? Number(_brnRecvOrd.weight_tolerance_pct) : 3; }
function _brnWtRaw(l) {
  const v = _brnRcv.wts[l.code];
  return String(v === undefined ? +Number(l.sent_weight).toFixed(6) : v).trim().replace(',', '.');
}
function _brnWtVal(l) {
  const raw = _brnWtRaw(l);
  if (raw === '') return null;
  const w = Number(raw);
  return Number.isFinite(w) && w >= 0 ? w : null;
}
// how far below the weight sent: {pct (> 0 = below), within (a shortfall up to the tolerance), beyond (more than the tolerance)}
function _brnWtShort(l) {
  const w = _brnWtVal(l), s = Number(l.sent_weight);
  if (w === null || !(s > 0)) return { pct: 0, within: false, beyond: false };
  const pct = (s - w) / s * 100, tol = _brnWtTol();
  return { pct, within: pct > 1e-9 && pct <= tol + 1e-9, beyond: pct > tol + 1e-9 };
}

// ── PR-U2: goods sold by weight whose pieces differ (a whole salmon, ตัว ≈ 4.5 กก.) ─────────────────────────────
// The branch counts the pieces it received next to the weight: a whole number >= 1 for every such line with a quantity > 0.
// The server is the gate (orders.receive); this mirrors it so the save button says what is missing.
const _BRN_SHORT_UNIT = { กิโลกรัม: 'กก.', มิลลิลิตร: 'มล.' };
function _brnShortUnit(u) { return _BRN_SHORT_UNIT[u] || u || ''; }
function _brnNeedsPcs(l) { return !!l.pieces_unit && (_brnRcvTyped(l) || 0) > 0; }
function _brnPcsRaw(code) { return String(_brnRcv.pcs[code] ?? '').trim().replace(',', '.'); }
function _brnPcsVal(code) {
  const raw = _brnPcsRaw(code);
  if (raw === '') return null;
  const n = Number(raw);
  return Number.isInteger(n) && n >= 1 ? n : null;
}
function _brnPcsMissing() { return _brnRcvLines().filter(l => _brnNeedsPcs(l) && _brnPcsVal(l.code) === null); }
// what one piece weighs = the weight received ÷ the pieces counted, in the unit the average is stated in; amber outside the range
function _brnPcsHint(l) {
  const n = _brnPcsVal(l.code), q = _brnRcvTyped(l);
  if (n === null || !(q > 0)) return { text: '', warn: false };
  const per = q * (l.base_per_order_unit || 1) / (l.pieces_parent_factor || 1) / n;
  const rg = l.pieces_range, out = !!(rg && (per < rg[0] - 1e-9 || per > rg[1] + 1e-9));
  return { text: `≈ ${_bmcNum(per, 2)} ${_brnShortUnit(l.pieces_parent_unit)} ต่อ${l.pieces_unit}` + (out ? ` · นอกช่วง ${_bmcNum(rg[0], 2)}–${_bmcNum(rg[1], 2)}` : ''), warn: out };
}
function brnRcvPcsUpdate(code) {
  const l = _brnRcvLine(code); if (!l || !l.pieces_unit) return;
  const raw = _brnPcsRaw(code), ok = _brnPcsVal(code) !== null, need = _brnNeedsPcs(l);
  const inp = document.getElementById('brnRcvP_' + code), h = document.getElementById('brnRcvPH_' + code), w = document.getElementById('brnRcvPW_' + code);
  if (w) w.style.display = need ? '' : 'none';
  if (inp) { inp.style.borderColor = !need ? 'var(--border-control)' : ok ? 'var(--good-ink)' : raw === '' ? 'var(--amber-ink)' : 'var(--bad-ink)'; inp.setAttribute('aria-invalid', need && raw !== '' && !ok ? 'true' : 'false'); }
  const ht = need ? _brnPcsHint(l) : { text: '', warn: false };
  if (h) { const msg = need && raw !== '' && !ok ? `กรอกจำนวน${l.pieces_unit}เป็นจำนวนเต็มตั้งแต่ 1 ขึ้นไป` : ht.text; h.textContent = (h.dataset.sep && msg ? ' · ' : '') + msg; h.style.color = (need && raw !== '' && !ok) ? 'var(--bad-ink)' : ht.warn ? 'var(--amber-ink)' : 'var(--text-secondary)'; }
}
function brnRcvPcsTyped(code, v) { _brnRcv.pcs[code] = String(v || ''); brnRcvPcsUpdate(code); brnRecvAckChanged(); }
// the weight box and the pieces box of a line sold by weight (PR-W): one row under the name, in the "รับครบ" block and in a picked row alike
function _brnWtBoxes(l) {
  const c = esc(l.code), cq = _bmcQ(l.code), nm = esc(_brnNm(l)), pu = esc(l.pieces_unit), wu = esc(_brnShortUnit(l.pieces_parent_unit));
  return `<div style="display:flex;gap:8px;align-items:flex-start;margin-top:6px">
      <div class="brn-box" style="width:124px;flex:0 0 124px"><input id="brnRcvW_${c}" class="brn-qty" style="height:44px" inputmode="decimal" autocomplete="off" value="${esc(_brnWtRaw(l))}"
          aria-label="น้ำหนักที่ชั่งได้ (${wu}) ${nm}" oninput="brnRcvWtTyped(${cq}, this.value)"><span class="brn-cap">น้ำหนักที่ชั่งได้ (${wu})</span></div>
      <div class="brn-box" id="brnRcvPW_${c}"><input id="brnRcvP_${c}" class="brn-qty" style="height:44px" inputmode="numeric" autocomplete="off" value="${esc(_brnRcv.pcs[l.code] ?? '')}"
          aria-label="จำนวน${pu} ${nm}" oninput="brnRcvPcsTyped(${cq}, this.value)"><span class="brn-cap">จำนวน${pu}</span></div></div>
    <div id="brnRcvWM_${c}" class="text-xs" role="status" style="margin-top:4px"></div>`;
}
// the box follows what is typed: the quantity under the name, the red/amber/muted note, the pieces box and the gap block
function brnRcvWtTyped(code, v) {
  _brnRcv.wts[code] = String(v ?? '');
  brnRecvDiff(code);
  brnRecvAckChanged();
  clearTimeout(_brnWtTimers[code]);
  _brnWtTimers[code] = setTimeout(() => brnRcvWtSettle(code), 650);       // the line follows the weight once typing pauses
}
const _brnWtTimers = {};
// a weight below the tolerance turns the line into a short line (reason + claim); back within it, a line picked by the weight alone goes back
function brnRcvWtSettle(code) {
  const l = _brnRcvLine(code);
  if (!l || !_brnRecvOrd || !_brnWtLine(l)) return;
  const sh = _brnWtShort(l), st = _brnRcv, picked = st.picked.includes(code);
  const el0 = /** @type {HTMLInputElement|null} */ (document.getElementById('brnRcvW_' + code));
  const had = !!el0 && document.activeElement === el0, caret = el0 ? el0.selectionStart : null;
  if (sh.beyond && !picked) {
    if (st.mode !== 'some') { st.mode = 'some'; st.autoMode = true; }
    st.picked.push(code); st.auto.push(code);
    st.vals[code] = { q: bmcToTyped(_brnRcvSent(l), _brnRcvF(l)), r: 'short', n: '' };
  } else if (!sh.beyond && picked && st.auto.includes(code)) {
    st.picked = st.picked.filter(c => c !== code); st.auto = st.auto.filter(c => c !== code);
    if (!st.picked.length && st.autoMode) { st.mode = 'all'; st.autoMode = false; }
  } else return;
  brnRcvRender();
  if (had) { const el = document.getElementById('brnRcvW_' + code); if (el) { el.focus(); try { el.setSelectionRange(caret, caret); } catch (e) { /* not a text box */ } } }
}
// the lines NOT picked as short that are sold by weight: weight + pieces, under the answer (a picked line has them in its own row)
function brnRcvPcsRender() {
  const box = document.getElementById('brnRcvPcs');
  if (!box) return;
  const st = _brnRcv;
  const lines = st.mode ? _brnRcvLines().filter(l => l.pieces_unit && !st.picked.includes(l.code) && _brnRcvSent(l) > 0) : [];
  if (!lines.length) { box.innerHTML = ''; return; }
  const us = [...new Set(lines.map(l => l.pieces_unit))].join(' / ');
  box.innerHTML = `<div class="text-sm" style="font-weight:600;color:var(--text-primary)">ชั่งน้ำหนักและนับจำนวน${esc(us)}ของรายการที่ขายเป็น${esc(us)}</div>`
    + lines.map(l => {
      const c = esc(l.code);
      return `<div class="brn-row" style="border-top:1px solid var(--border);padding:8px 0;margin-top:6px">
        <div class="brn-name" style="color:var(--text-primary);font-weight:600">${esc(_brnNm(l))}</div>
        <div class="brn-sub text-[11px]" style="color:var(--text-muted)">รับ <b id="brnRcvWQ_${c}" style="color:var(--text-primary)"></b> ${esc(_brnShortUnit(l.pieces_parent_unit))}<span id="brnRcvPH_${c}" data-sep="1"></span></div>
        ${_brnWtBoxes(l)}
        ${st.mode === 'some' ? brnPkBtn(l.code) : ''}</div>`;
    }).join('');
  lines.forEach(l => { brnRecvDiff(l.code); brnPkRender(l.code); });
}

// ── PR-U2: "ขนาดบรรจุไม่ตรง" — the branch tells the analyst a pack is not the registered size; the receipt is not touched ──
function brnPkBtn(code) {
  const l = _brnRcvLine(code);
  if (!l || !l.pack || !(l.pack.units || []).length) return `<div id="brnPk_${esc(code)}"></div>`;
  return `<button type="button" id="brnPkB_${esc(code)}" class="brn-link" style="font-size:12px;min-height:32px" onclick="brnPkToggle(${_bmcQ(code)})">ขนาดบรรจุไม่ตรง</button><div id="brnPk_${esc(code)}"></div>`;
}
function brnPkToggle(code) {
  const p = _brnRcv.pk[code] || (_brnRcv.pk[code] = {});
  if (p.sent) return;
  p.open = !p.open;
  brnPkRender(code);
  if (p.open) setTimeout(() => { const el = document.getElementById('brnPkQ_' + code); if (el) el.focus(); }, 30);
}
function brnPkField(code, key, v) {
  const p = _brnRcv.pk[code] || (_brnRcv.pk[code] = {});
  p[key] = String(v ?? '');
  if (key === 'unit') { p.parent = ''; brnPkRender(code); }
}
function brnPkRender(code) {
  const box = document.getElementById('brnPk_' + code);
  if (!box) return;
  const p = _brnRcv.pk[code], l = _brnRcvLine(code);
  if (!p || !l || !l.pack) { box.innerHTML = ''; return; }
  if (p.sent) { box.innerHTML = `<div class="text-xs" style="color:var(--good-ink);margin-top:4px" role="status">${esc(p.msg)}</div>`; return; }
  if (!p.open) { box.innerHTML = ''; return; }
  const units = l.pack.units || [];
  if (!p.unit || !units.find(u => u.unit === p.unit)) p.unit = (units.find(u => u.unit === l.pack.recv_unit) || units[0]).unit;
  const cur = units.find(u => u.unit === p.unit);
  // the reference unit starts at the NEXT UNIT DOWN of the item's ladder (กล่อง -> แพ็ค), else กิโลกรัม / ลิตร - never the bare base unit
  if (!p.parent) p.parent = cur.default_parent || cur.parent;
  const sel = 'border:1px solid var(--border-control);border-radius:var(--r-sm);padding:6px 4px;font-size:16px;min-height:44px;background:var(--bg-input);color:var(--text-primary)';
  const c = esc(code), cq = _bmcQ(code);
  const opt = (arr, v) => arr.map(o => `<option value="${esc(o)}"${o === v ? ' selected' : ''}>${esc(o)}</option>`).join('');
  box.innerHTML = `<div style="margin-top:6px;padding:8px 10px;border:1px solid var(--border);border-radius:var(--r-sm);background:var(--bg-input)">
    <div class="text-xs" style="color:var(--text-secondary)">ในระบบ: <b style="color:var(--text-primary)">${esc(cur.current)}</b>${cur.label ? ` <span style="color:var(--text-muted)">(ทะเบียนเขียนว่า “${esc(cur.label)}”)</span>` : ''}</div>
    <div style="display:flex;gap:6px;flex-wrap:nowrap;align-items:center;margin-top:6px;min-width:0">
      <span>1</span><select aria-label="หน่วยที่ขนาดไม่ตรง" style="${sel};flex:1 1 0;min-width:0" onchange="brnPkField(${cq}, 'unit', this.value)">${opt(units.map(u => u.unit), p.unit)}</select>
      <span style="white-space:nowrap">มีจริง</span><input id="brnPkQ_${c}" inputmode="decimal" autocomplete="off" value="${esc(p.qty || '')}" aria-label="จำนวนที่มีจริง" oninput="brnPkField(${cq}, 'qty', this.value)" style="${sel};flex:0 0 56px;width:56px;min-width:0;padding:6px 2px;text-align:center">
      <select aria-label="หน่วยที่ใช้เทียบ" style="${sel};flex:1 1 0;min-width:0" onchange="brnPkField(${cq}, 'parent', this.value)">${opt(l.pack.parents || [], p.parent)}</select></div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:6px">
      <input value="${esc(p.note || '')}" placeholder="หมายเหตุ (ถ้ามี)" aria-label="หมายเหตุ" oninput="brnPkField(${cq}, 'note', this.value)" style="${sel};padding:6px 8px;flex:1 1 150px;min-width:0;box-sizing:border-box">
      <span style="display:flex;gap:6px;flex:0 0 auto"><button type="button" class="brn-act" id="brnPkGo_${c}" onclick="brnPkSend(${cq})">ส่งให้ฝ่ายวิเคราะห์</button>
        <button type="button" class="brn-act brn-act-line" onclick="brnPkToggle(${cq})">ยกเลิก</button></span>
      <span id="brnPkM_${c}" class="text-xs" role="alert" style="color:var(--bad-ink);flex:1 1 100%"></span></div></div>`;
}
async function brnPkSend(code) {
  const o = _brnRecvOrd, p = _brnRcv.pk[code];
  const msg = document.getElementById('brnPkM_' + code), btn = document.getElementById('brnPkGo_' + code);
  if (!o || !p || !msg) return;
  const qty = Number(String(p.qty || '').trim().replace(',', '.'));
  if (!(qty > 0)) { msg.textContent = 'จำนวนที่มีจริงต้องเป็นตัวเลขที่มากกว่า 0'; return; }
  if (p.unit === p.parent) { msg.textContent = 'หน่วยที่แจ้งกับหน่วยที่ใช้เทียบต้องเป็นคนละหน่วย'; return; }
  msg.textContent = '';
  if (btn) btn.disabled = true;
  try {
    const r = await _bmcJson(`${API}/api/bom-center/units/pack-report${_brnQS() ? '?' + _brnQS() : ''}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order_id: o.order_id, code, unit: p.unit, qty, parent: p.parent, note: String(p.note || '').trim() || null }) });
    p.sent = true; p.msg = r.message || '✓ แจ้งขนาดบรรจุแล้ว';
    brnPkRender(code);
    const b = document.getElementById('brnPkB_' + code); if (b) b.style.display = 'none';
  } catch (e) { msg.textContent = String(e.message || e).slice(0, 220); if (btn) btn.disabled = false; }
}

async function brnOpenReceive(id, quiet) {
  const box = document.getElementById('brnRecvOrder');
  if (!box) return;
  try {
    _brnRecvOrd = await _bmcJson(`${API}/api/bom-center/order/${id}`);
    const o = _brnRecvOrd;
    _brnRcv = { mode: null, picked: [], vals: {}, q: '', pcs: {}, pk: {}, wts: {}, auto: [], autoMode: false };
    const today =new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    const day = o.delivery && o.delivery <= today ? o.delivery : today;
    const sel = 'border:1px solid var(--border-control);border-radius:var(--r-sm);padding:6px 8px;font-size:16px;background:var(--bg-input);color:var(--text-primary)';
    box.style.display = '';
    box.innerHTML = `<div class="text-sm font-semibold" style="color:var(--text-primary)">รับสินค้า · ${esc(_brnRoundLabel(o.round))} · ส่ง ${esc(fmtDay(o.delivery || o.cycle_date))} ${_bmcChip(o.status_th, 'var(--navy-ink)')}</div>
      <label class="text-xs mt-2 block" style="color:var(--text-muted)">วันที่รับ <input type="date" id="brnRecvOrdDate" value="${day}" style="${sel}"></label>
      <div id="brnRecvForm" class="mt-2"></div>
      <div class="brn-q mt-3">รับครบตามที่คลังส่งทุกรายการ?</div>
      <div class="brn-qbtns mt-2">
        <button type="button" id="brnRcvAll" class="brn-qbtn" aria-pressed="false" onclick="brnRcvMode('all')">รับครบ</button>
        <button type="button" id="brnRcvSome" class="brn-qbtn" aria-pressed="false" onclick="brnRcvMode('some')">มีรายการไม่ครบ</button></div>
      <div id="brnRcvBody" class="mt-2"></div>
      <div id="brnRcvPcs" class="mt-3"></div>
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
    brnFormMount('rcv');                                  // PR-SI: the Excel form of this receipt
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
  _brnRcv.autoMode = false;
  if (m === 'all') { _brnRcv.picked = []; _brnRcv.auto = []; _brnRcv.wts = {}; }      // everything arrived: nothing to adjust (the weights go back to the weight sent)
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
    const hasPack = lines.some(l => l.pack && (l.pack.units || []).length);
    body.innerHTML = `<div class="text-sm" style="color:var(--text-primary);font-weight:600">รับครบ ${lines.length} รายการ</div>
      ${hasPack ? '<div class="text-xs" style="color:var(--text-muted);margin-top:2px">ขนาดบรรจุไม่ตรง? เปิด “ดูรายการที่คลังส่ง” แล้วแตะที่รายการนั้น</div>' : ''}
      <details class="mt-1"><summary class="text-xs" style="color:var(--accent);cursor:pointer;min-height:32px">ดูรายการที่คลังส่ง</summary>
        <div style="max-height:260px;overflow:auto">${lines.map(l => `<div class="text-xs" style="border-top:1px solid var(--border);padding:4px 0"><div style="display:flex;gap:8px"><span style="flex:1 1 0;min-width:0;overflow-wrap:anywhere">${esc(_brnNm(l))}</span><span style="white-space:nowrap;color:var(--text-secondary)">${_brnRcvQ(l, _brnRcvSent(l))} ${esc(_brnRcvUnit(l))}</span></div>${brnPkBtn(l.code)}</div>`).join('')}</div></details>`;
    lines.forEach(l => brnPkRender(l.code));
  } else {
    body.innerHTML = `<input id="brnRcvQ" type="search" autocomplete="off" placeholder="ค้นหารายการที่ไม่ครบ" value="${esc(st.q)}" oninput="brnRcvSearch(this.value)"
        style="width:100%;box-sizing:border-box;min-height:44px;border:1px solid var(--border-control);border-radius:var(--r-sm);padding:6px 10px;font-size:16px;background:var(--bg-input);color:var(--text-primary)">
      <div id="brnRcvRes" class="mt-1"></div><div id="brnRcvPicked"></div><div id="brnRcvRest" class="text-sm mt-2" style="color:var(--text-primary)"></div>`;
    brnRcvResults(); brnRcvPickedRender();
  }
  brnRcvPcsRender();
  brnRecvDiffAll();
  brnRecvAckChanged();
}
function brnRcvSearch(v) { _brnRcv.q = String(v || ''); brnRcvResults(); }
// the lines not picked yet that match the search: at most 8 (a form of 87 lines is never listed whole)
function brnRcvResults() {
  const box = document.getElementById('brnRcvRes');
  if (!box) return;
  const q = _brnRcv.q.trim().toLowerCase();
  const hits = _brnRcvLines().filter(l => !_brnRcv.picked.includes(l.code) && (!q || ((l.code || '') + ' ' + (l.name || '') + ' ' + (l.short_name || '')).toLowerCase().includes(q)));
  if (!q && hits.length > 8) { box.innerHTML = `<div class="text-xs" style="color:var(--text-muted);padding:4px 2px">พิมพ์ชื่อวัตถุดิบเพื่อเลือกรายการที่ไม่ครบ (ทั้งหมด ${hits.length} รายการ)</div>`; return; }
  box.innerHTML = hits.slice(0, 8).map(l => `<button type="button" class="brn-pick" onclick="brnRcvPick(${_bmcQ(l.code)})"><span style="flex:1 1 0;min-width:0;text-align:left;overflow-wrap:anywhere">${esc(_brnNm(l))}</span><span class="text-xs" style="color:var(--text-secondary);white-space:nowrap">ส่ง ${_brnRcvQ(l, _brnRcvSent(l))} ${esc(_brnRcvUnit(l))}</span><span style="color:var(--accent);font-weight:600">เลือก</span></button>`).join('')
    + (hits.length > 8 ? `<div class="text-xs" style="color:var(--text-muted);padding:4px 2px">แสดง 8 จาก ${hits.length} รายการ · พิมพ์ชื่อเพิ่มเพื่อค้นหา</div>`
      : !hits.length && _brnRcv.picked.length < _brnRcvLines().length ? '<div class="text-xs" style="color:var(--text-muted);padding:4px 2px">ไม่พบรายการที่ค้นหา</div>' : '');
}
function brnRcvPick(code) {
  const l = _brnRcvLine(code);
  if (!l || _brnRcv.picked.includes(code)) return;
  _brnRcv.picked.push(code);
  _brnRcv.vals[code] = { q: bmcToTyped(_brnRcvSent(l), _brnRcvF(l)), r: 'short', n: '' };
  _brnRcv.q = '';
  const qi = document.getElementById('brnRcvQ'); if (qi) qi.value = '';
  brnRcvResults(); brnRcvPickedRender(); brnRcvPcsRender(); brnRecvDiff(code); brnRecvAckChanged();
  setTimeout(() => { const el = /** @type {HTMLInputElement|null} */ (document.getElementById((_brnWtLine(l) ? 'brnRcvW_' : 'brnRcvO_') + code)); if (el) { el.focus(); el.select(); } }, 30);
}
function brnRcvUnpick(code) {
  _brnRcv.picked = _brnRcv.picked.filter(c => c !== code);
  _brnRcv.auto = _brnRcv.auto.filter(c => c !== code);
  delete _brnRcv.wts[code];                                // taking a line out of the short lines puts its weight back to the weight sent
  brnRcvResults(); brnRcvPickedRender(); brnRcvPcsRender(); brnRecvDiffAll(); brnRecvAckChanged();
}
// the picked lines, expanded: รับจริง (44 px) · the gap · the reason · a note
function brnRcvPickedRender() {
  const box = document.getElementById('brnRcvPicked'), rest = document.getElementById('brnRcvRest');
  if (!box) return;
  const reasons = (_brnRecvOrd && _brnRecvOrd.claim_reasons) || [];
  const sel = 'border:1px solid var(--border-control);border-radius:var(--r-sm);padding:6px 8px;font-size:16px;background:var(--bg-input);color:var(--text-primary)';
  box.innerHTML = _brnRcv.picked.map(code => {
    const l = _brnRcvLine(code); if (!l) return '';
    const c = esc(code), v = _brnRcv.vals[code] || {}, unit = esc(_brnRcvUnit(l)), cq = _bmcQ(code), wl = _brnWtLine(l);
    return `<div class="brn-row" style="border-top:1px solid var(--border);padding:8px 0">
      <div style="display:flex;gap:8px;align-items:flex-start">
        <div class="brn-namecol" style="flex:1 1 0;min-width:0">
          <div class="brn-name" style="color:var(--text-primary);font-weight:600">${esc(_brnNm(l))}</div>
          <div class="brn-sub text-[11px]" style="color:var(--text-muted)">${_brnLine(_brnSrc(l), 'ส่งจริง')} <b style="color:var(--text-primary)">${_brnRcvQ(l, _brnRcvSent(l))}</b> ${unit}${wl && Math.abs(Number(l.sent_weight) - _brnRcvSent(l)) > 1e-9 ? ` (${_bmcNum(l.sent_weight, 3)} ${esc(_brnShortUnit(l.pieces_parent_unit))})` : ''}${l.ss_reason_th ? ' · คลัง: ' + esc(l.ss_reason_th) : ''}</div></div>
        ${wl ? '' : `<div class="brn-box"><input id="brnRcvO_${c}" class="brn-qty" inputmode="decimal" autocomplete="off" value="${esc(v.q ?? '')}" aria-label="รับจริง ${esc(_brnNm(l))}" oninput="brnRcvTyped(${cq}, this.value)"
            style="border-color:var(--good-ink)"><span class="brn-cap">รับจริง · ${unit || 'หน่วยฐาน'}</span></div>`}
        ${l.pieces_unit && !wl ? `<div class="brn-box" id="brnRcvPW_${c}"><input id="brnRcvP_${c}" class="brn-qty" style="height:44px" inputmode="numeric" autocomplete="off" value="${esc(_brnRcv.pcs[code] ?? '')}"
            aria-label="จำนวน${esc(l.pieces_unit)} ${esc(_brnNm(l))}" oninput="brnRcvPcsTyped(${cq}, this.value)"><span class="brn-cap">จำนวน${esc(l.pieces_unit)}</span></div>` : ''}
        <button type="button" class="brn-x" aria-label="เอาออก ${esc(_brnNm(l))}" onclick="brnRcvUnpick(${cq})">✕</button></div>
      ${wl ? _brnWtBoxes(l) : ''}
      <div id="brnRcvE_${c}" class="brn-err" role="alert"></div>
      ${l.pieces_unit ? `<span id="brnRcvPH_${c}" class="text-xs" style="display:block"></span>` : ''}
      ${brnPkBtn(code)}
      <span id="brnRcvOver_${c}" class="text-xs" style="display:none;color:var(--navy-ink)"></span>
      <div id="brnRcvD_${c}" style="display:none;margin-top:6px;padding:6px 8px;border-left:3px solid var(--amber-ink);background:var(--bg-input);border-radius:var(--r-sm)">
        <div class="text-xs" style="color:var(--text-primary)">ส่วนต่าง <b id="brnRcvDn_${c}">0</b> ${unit} ที่ยังไม่ได้รับ</div>
        <div class="brn-reason" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:4px">
          <select id="brnRcvR_${c}" aria-label="สาเหตุของส่วนต่าง" onchange="brnRcvField(${cq}, 'r', this.value)" style="${sel};max-width:100%">${reasons.map(r => `<option value="${esc(r.code)}"${(v.r || 'short') === r.code ? ' selected' : ''}>${esc(r.label)}</option>`).join('')}</select>
          <input id="brnRcvN_${c}" value="${esc(v.n || '')}" oninput="brnRcvField(${cq}, 'n', this.value)" placeholder="หมายเหตุ (จำเป็นเมื่อเลือก “อื่น ๆ”)" style="${sel};flex:1 1 200px;min-width:0"></div></div></div>`;
  }).join('');
  _brnRcv.picked.forEach(code => { brnRcvPcsUpdate(code); brnPkRender(code); });
  if (rest) {
    const n = _brnRcvLines().length - _brnRcv.picked.length;
    const k = _brnRcv.picked.length;      // U3: never "รับครบ 0 รายการ" - every line picked reads "ปรับ N รายการด้านบน" alone
    rest.innerHTML = `${n ? `รับครบ ${n} รายการ` : ''}${k ? ` <span style="color:var(--text-muted)">${n ? '· ' : ''}ปรับ ${k} รายการด้านบน</span>` : ''}`;
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
    const wl = _brnWtLine(l);
    const raw = wl ? _brnWtRaw(l) : _brnRcvRaw(code), q = _brnRcvTyped(l);
    const bad = raw === '' || q === null || q < 0;                 // empty, a word or a negative number: red at once, the note under the row
    const inp = document.getElementById((wl ? 'brnRcvW_' : 'brnRcvO_') + code), er = document.getElementById('brnRcvE_' + code);
    if (inp) { inp.style.borderColor = bad ? 'var(--bad-ink)' : 'var(--good-ink)'; inp.setAttribute('aria-invalid', bad ? 'true' : 'false'); }
    if (er) er.textContent = bad && !wl ? 'จำนวนต้องเป็นตัวเลข ≥ 0' : '';          // a weight's note is the one under its box
    if (wl) {                                                      // PR-W: the quantity under the name and the note about the tolerance
      const qt = document.getElementById('brnRcvWQ_' + code), ms = document.getElementById('brnRcvWM_' + code), sh = _brnWtShort(l), tol = _bmcNum(_brnWtTol(), 1);
      if (qt) qt.textContent = bad ? '—' : _bmcNum(_brnWtVal(l), 3);
      if (ms) {
        ms.style.color = bad ? 'var(--bad-ink)' : sh.beyond ? 'var(--amber-ink)' : 'var(--text-muted)';
        ms.textContent = bad ? 'น้ำหนักต้องเป็นตัวเลข ≥ 0' : sh.beyond ? `น้อยกว่าที่คลังส่งเกิน ${tol}% · เลือกสาเหตุของส่วนต่าง`
          : sh.within ? `น้อยกว่าที่คลังส่ง ${_bmcNum(sh.pct, 1)}% ไม่เกิน ${tol}% จึงไม่ต้องแจ้งเคลม` : '';
      }
    }
    brnRcvPcsUpdate(code);                                         // the pieces box follows the quantity: no quantity, no count to give
    const gap = bad ? 0 : _brnRcvGap(l);
    const d = document.getElementById('brnRcvD_' + code), ov = document.getElementById('brnRcvOver_' + code), n = document.getElementById('brnRcvDn_' + code);
    if (d) d.style.display = gap > 1e-9 ? '' : 'none';
    if (n) n.textContent = _brnRcvQ(l, Math.max(gap, 0));
    if (ov) { ov.style.display = gap < -1e-9 ? '' : 'none'; ov.textContent = gap < -1e-9 ? `รับเกินที่ส่ง +${_brnRcvQ(l, -gap)}` : ''; }
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
  else if (_brnPcsMissing().length) {
    const miss = _brnPcsMissing();
    why = miss.length === 1 ? `กรอกจำนวน${miss[0].pieces_unit}ของ ${_brnNm(miss[0])}` : `กรอกจำนวน${[...new Set(miss.map(m => m.pieces_unit))].join(' / ')}ให้ครบ ${miss.length} รายการ`;
  } else if (!a.checked) why = 'ติ๊ก “ตรวจสภาพและจำนวนครบแล้ว” ก่อนบันทึก';
  b.disabled = !!why;
  if (w) {
    w.style.color = why ? 'var(--amber-ink)' : 'var(--text-secondary)';
    w.textContent = why || (st.mode === 'all' ? `รับครบ ${n} รายการ` : `${n - st.picked.length ? `รับครบ ${n - st.picked.length} รายการ · ` : ''}ปรับ ${st.picked.length} รายการ`);
  }
}
// U3 (owner 2026-10-22) · what the receive confirm says it records, never "· 0 รายการ": every line short -> "รับไม่ครบทุกรายการ";
// nothing received -> "ไม่ได้รับสินค้า"; else the lines received; the lines claimed follow (they go to ประสานงานขาย with the receipt)
function _brnRcvHead(nAll, nRecv, nShort) {
  const claim = nShort ? ` · แจ้งเคลม ${nShort} รายการ` : '';
  if (nShort && nShort >= nAll) return `รับไม่ครบทุกรายการ${claim}`;
  if (!nRecv) return `ไม่ได้รับสินค้า${claim}`;
  return `รับ ${nRecv} รายการ${claim}`;
}
async function brnReceiveOrder() {
  const o = _brnRecvOrd;
  if (!o) return;
  const warn = m => _bmcStatus('brnRecvOrdStatus', m, 'var(--amber-ink)');
  if (!_brnRcv.mode) { warn('เลือก “รับครบ” หรือ “มีรายการไม่ครบ” ก่อน'); return; }
  if (!document.getElementById('brnRecvAck').checked) { warn('ทำเครื่องหมายยืนยันการตรวจสินค้าก่อน'); return; }
  const lines = [];
  let short = 0, over = 0, pcsN = 0, within = 0;
  const pcsUnits = new Set();
  for (const l of _brnRcvLines()) {
    const q = _brnRcvTyped(l), nm = _brnNm(l);
    if (q === null || q < 0) {
      warn(`${nm}: จำนวนต้องเป็นตัวเลขและไม่ติดลบ`);
      const el = document.getElementById((_brnWtLine(l) ? 'brnRcvW_' : 'brnRcvO_') + l.code); if (el) { el.scrollIntoView({ block: 'center' }); el.focus(); }
      return;
    }
    const row = { code: l.code, received_units: q };
    if (_brnWtLine(l)) { row.weight = _brnWtVal(l); if (_brnWtShort(l).within) within++; }      // PR-W: the weight weighed; the server derives the quantity from it
    if (q > 0 && l.pieces_unit) {                                  // PR-U2: the pieces counted, a whole number >= 1
      const pc = _brnPcsVal(l.code);
      if (pc === null) {
        warn(`${nm}: กรอกจำนวน${l.pieces_unit}ที่รับ (จำนวนเต็ม)`);
        const el = document.getElementById('brnRcvP_' + l.code); if (el) { el.scrollIntoView({ block: 'center' }); el.focus(); }
        return;
      }
      row.pieces = pc; pcsN++; pcsUnits.add(l.pieces_unit);
    }
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
  if (!await uiConfirm(`บันทึกรับสินค้า${_bmcRoundDay(o)} · ${_brnRcvHead(lines.length, n, short)} · วันที่ ${fmtDay(day)}`
      + (_brnRcv.mode === 'all' ? '\nรับครบทุกรายการ' : '')
      + (pcsN ? `\nชั่งน้ำหนักและนับจำนวน${[...pcsUnits].join(' / ')} ${pcsN} รายการ` : '')
      + (within ? `\nน้ำหนักน้อยกว่าที่ส่งไม่เกิน ${_bmcNum(_brnWtTol(), 1)}% ${within} รายการ รับตามน้ำหนักที่ชั่ง ไม่แจ้งเคลม` : '')
      + (short ? '\nเคลมส่วนต่างส่งถึงประสานงานขาย' : '') + (over ? `\nรับเกินที่ส่ง ${over} รายการ` : ''), { title: 'ยืนยันการบันทึกรับสินค้า', ok: 'บันทึกรับสินค้า', cancel: 'กลับไปแก้' })) return;
  try {
    const r = await _bmcJson(`${API}/api/bom-center/order/${o.order_id}/receive${_brnQS() ? '?' + _brnQS() : ''}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lines, receipt_date: day || null, ack: true, claim_note: claimNote || null }) });
    _brnRecvOrd = null;
    document.getElementById('brnRecvOrder').style.display = 'none';
    if (r.claim_id) {
      _bmcStatus('brnRecvOrdDone', `✓ บันทึกรับสินค้าแล้ว · แจ้งเคลม ${r.claim_lines} รายการให้ประสานงานขายแล้ว`, 'var(--good-ink)');
    } else {
      _bmcStatus('brnRecvOrdDone', `✓ บันทึกรับสินค้าแล้ว ${n} รายการ`, 'var(--good-ink)');
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
// 5.2 / string 4: a count unit that carries its contents ("แพ็ค 1 กก.") is written as the word beside each quantity and the contents once
// per line: "0 แพ็ค · 25.2 แพ็ค (แพ็คละ 1 กก.)" -> [word, "แพ็คละ 1 กก." | '']
function _brnCountUnitParts(it) {
  const raw = String((it || {}).count_unit || (it || {}).base_unit || '').replace(/\s*\([^)]*\)/g, '').trim();
  const m = raw.match(/^(\S+)\s+(\d[\s\S]*)$/);
  return m ? [m[1], `${m[1]}ละ ${m[2].trim()}`] : [raw, ''];
}
function brnQtyUnit(qBase, item, dec) {
  if (qBase === null || qBase === undefined || qBase === '' || isNaN(qBase)) return '—';
  const it = item || {};
  const f = Number(it.per_count_unit) > 0 ? Number(it.per_count_unit) : 1;
  const unit = _brnCountUnitParts(it)[0];
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
// PR-S4 · mode stock: the period tab (used against the recipes) is not offered; a link that names it opens the daily tab
function _brnVtList() { return BC_MODE === 'stock' ? _BRN_VT.filter(([p]) => p !== 'period') : _BRN_VT; }
function brnVarTab(k, quiet) {
  if (BC_MODE === 'stock' && k === 'period') k = 'daily';
  _brnVarTab = k;
  _BRN_VT.forEach(([p]) => { const el = document.getElementById('brnVar-' + p); if (el) el.style.display = p === k ? '' : 'none'; });
  const bar = document.getElementById('brnVarTabs');
  if (bar) bar.innerHTML = _brnVtList().map(([p, t]) => {
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
          ${b.end_date ? `<span>${esc(fmtDay(b.begin_date))} → ${esc(fmtDay(b.end_date))}</span><span>${bmcGapHtml(b.gap_value, b.gap_pct, 'use')}</span>
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
  const per = _brnCountUnitParts(r)[1], note = per ? ` (${esc(per)})` : '';
  if (r.actual == null) return `ตามสูตร <b>${q(r.bom)}</b>${note} · เทียบไม่ได้ เพราะต้นงวดหรือปลายงวดไม่มีผลตรวจนับ`;
  let g = '';
  if (r.gap != null && Math.abs(r.gap) > 1e-9) {                 // 4.6: the words carry the direction, so the % has no sign
    const w = bmcGapParts(r.gap, r.gap_pct, 'use');
    g = ` · ${w.label} <b style="color:${w.ink}">${q(Math.abs(r.gap))}</b>${w.pct ? ` (${w.pct})` : ''}`;
  }
  return `ใช้จริง <b>${q(r.actual)}</b> · ตามสูตร <b>${q(r.bom)}</b>${note}${g}`;
}
function _brnVarName(r, withSel) {
  const [label, ink] = _BRN_VS[r.status] || [r.status, 'var(--text-muted)'];
  return `${withSel ? `<label class="brn-chk" aria-label="เลือก ${esc(_brnNm(r))}"><input type="checkbox" ${_brnVarSel.has(r.code) ? 'checked' : ''} onchange="brnVarSel(${_bmcQ(r.code)}, this.checked)"></label>` : ''}
    <div class="brn-namecol" style="flex:1 1 0;min-width:0"><div class="brn-name" style="color:var(--text-primary);font-weight:600">${esc(_brnNm(r))}</div>
      <div class="brn-sub text-[11px]" style="color:var(--text-muted)">${_bmcChip(label, ink)}${r.class === 'A' ? ' ' + _bmcChip('กลุ่มมูลค่าสูง', 'var(--navy-ink)') : ''}${r.data_incomplete ? ' ' + _bmcChip(r.incomplete_th || 'ข้อมูลไม่ครบ', 'var(--text-muted)') : ''}${r.system_basis ? ' ' + _bmcChip(r.basis_th, 'var(--navy-ink)') : ''} ${_brnSrc(r)}</div></div>
    <div class="text-sm" style="white-space:nowrap">${r.gap_value != null && bmcGapDir(r.gap_value) !== 0 ? `<b style="color:${bmcGapParts(r.gap_value, null, 'use').ink}">${bmcGapParts(r.gap_value, null, 'use').baht}</b>` : ''}</div>`;
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
      <select id="brnRs_${c}" aria-label="เหตุผล ${esc(_brnNm(r))}" style="flex:1 1 100%;border:1px solid ${r.needs_reason ? 'var(--amber-ink)' : 'var(--border-control)'};border-radius:var(--r-sm);padding:4px 6px;background:var(--bg-input);color:var(--text-primary)"><option value="">— เหตุผล —</option>${opts}</select>
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
  const gp = bmcGapParts(v.gap_value, pct, 'use');                  // 4.6: "ใช้น้อยกว่าสูตร ฿16,197 (8.1%)" - the figure big, the words under it
  document.getElementById('brnVarHead').innerHTML = `<div style="color:var(--text-secondary)">วันขาย ${v.trading_days} วัน · ราคาเดือน${esc(fmtMonthTh(v.price_month))}</div>
    <div class="brn-v3 mt-2">${tile('ใช้จริง', _brnBaht(v.actual_value), 'var(--text-primary)')}${tile('ตามสูตร', _brnBaht(v.bom_value_counted), 'var(--text-primary)')}${tile('เทียบสูตร', gp.dir ? gp.baht : '฿0', gp.ink, esc(gp.label) + (gp.dir && gp.pct ? ` (${gp.pct})` : ''))}</div>
    <div class="flex flex-wrap items-center gap-2 mt-2">
      ${open ? `<button class="brn-chipbtn" onclick="brnVarJump()">ต้องให้เหตุผล ${open} รายการ</button>` : _bmcChip('✓ ให้เหตุผลครบแล้ว', 'var(--good-ink)')}
      ${v.totals && v.totals.incomplete ? _bmcChip(`ข้อมูลไม่ครบ ${v.totals.incomplete} รายการ (ไม่ถามเหตุผล)`, 'var(--text-muted)') : ''}
      ${v.partial ? _bmcChip(`ตรวจนับหมุนเวียน · ไม่ได้นับ ${v.system_basis} รายการ`, 'var(--navy-ink)') + ' ' + uiQ('brn:partial', 'ตรวจนับหมุนเวียน', `<p>ปิดงวดด้วยการตรวจนับหมุนเวียน (รายการเดิม) · คงเหลือตามระบบ (ไม่ได้นับ) ${v.system_basis} รายการ</p>`) : ''}
      ${v.complete ? `${_bmcChip('รับสินค้าครบแล้ว', 'var(--good-ink)')} <span class="text-xs" style="color:var(--text-muted)">โดย ${esc(whoTh(v.receipts_confirmed_by))}</span> <button class="brn-link" onclick="brnConfirmRecv(true)">ยกเลิกการยืนยัน</button>`
        : `${_bmcChip('ยังไม่ยืนยันรับสินค้าครบ', 'var(--amber-ink)')} <button class="brn-act brn-act-line" onclick="brnConfirmRecv(false)">ยืนยันรับสินค้าครบ</button>`}</div>`;
  const ask = v.rows.filter(r => r.ask_reason).sort((a, b) => (Number(b.needs_reason) - Number(a.needs_reason)) || (Math.abs(b.gap_value || 0) - Math.abs(a.gap_value || 0)));
  const flaggedAll = v.rows.filter(r => _BRN_FLAG.includes(r.status) && !r.ask_reason);
  const incompleteRows = flaggedAll.filter(r => r.data_incomplete), flaggedOther = flaggedAll.filter(r => !r.data_incomplete);   // missing data is unfinished work, never done
  const inRange = v.rows.filter(r => !r.ask_reason && !_BRN_FLAG.includes(r.status));
  const askOpen = ask.filter(r => r.needs_reason), askDone = ask.filter(r => !r.needs_reason);
  const vf = uiStateFilter.get();               // PR-D6: ยังไม่เสร็จ (the reasons still to give + the lines whose data is incomplete) · เสร็จแล้ว (answered, or a line nobody is asked about) · ทั้งหมด
  _brnVarSel = new Set([..._brnVarSel].filter(c => askOpen.some(r => r.code === c)));
  const multi = askOpen.length >= 2 && _brnCanVariance() ? `<div class="text-xs" style="color:var(--text-muted)">ติ๊กหน้ารายการหลายรายการ เพื่อใช้เหตุผลเดียวกัน</div><div class="brn-multi" id="brnVarMulti" style="display:none">
      <div class="text-xs" style="color:var(--text-secondary)"><span id="brnVarSelN">เลือก 0 รายการ</span></div>
      <div class="brn-reason" style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px">
        <select id="brnVarMultiR" aria-label="เหตุผลที่ใช้กับรายการที่เลือก" style="flex:1 1 150px;border:1px solid var(--border-control);border-radius:var(--r-sm);padding:4px 6px;background:var(--bg-input);color:var(--text-primary)"><option value="">— เหตุผล —</option>${v.reasons.map(o => `<option value="${esc(o.code)}">${esc(o.label)}</option>`).join('')}</select>
        <input id="brnVarMultiN" placeholder="หมายเหตุ (จำเป็นเมื่อเลือก “อื่น ๆ”)" style="flex:1 1 120px;min-width:0;border:1px solid var(--border-control);border-radius:var(--r-sm);padding:4px 6px;background:var(--bg-input);color:var(--text-primary)">
        <button id="brnVarMultiBtn" class="brn-act" onclick="brnVarMultiSave()" disabled>ใช้เหตุผลเดียวกันกับที่เลือก</button></div>
      <div id="brnVarMultiE" class="brn-err" role="alert"></div></div>` : '';
  const doneRows = askDone.map(r => _brnVarAskRow(r, v.reasons)).concat(flaggedOther.map(_brnVarOtherRow), vf === 'all' ? inRange.slice(0, 200).map(_brnVarOtherRow) : []);
  document.getElementById('brnVarList').innerHTML = '<div id="brnVarFilter"></div>'
    + (vf !== 'done' ? (askOpen.length || incompleteRows.length ? (askOpen.length ? multi + askOpen.map(r => _brnVarAskRow(r, v.reasons)).join('') : '') + incompleteRows.map(_brnVarOtherRow).join('')
      : (vf === 'todo' ? '<div class="text-sm" style="color:var(--good-ink)">✓ ไม่มีรายการที่ต้องให้เหตุผล</div>' : '')) : '')
    + (vf !== 'todo' ? (doneRows.join('') || uiStateFilter.empty(vf, 'brnVarPick', 'รายการ')) : '')
    + (v.reason_min_baht ? `<div class="text-[11px] mt-1" style="color:var(--text-muted)">ถามเหตุผลเฉพาะรายการที่เกินเกณฑ์และต่างตั้งแต่ ฿${_bmcNum(v.reason_min_baht, 0)} ขึ้นไป</div>` : '');
  uiStateFilter.bar('brnVarFilter', { counts: { todo: askOpen.length + incompleteRows.length, done: askDone.length + flaggedOther.length, all: v.rows.length }, f: vf, pick: 'brnVarPick' });
  brnVarMultiBar();
}
function brnVarPick(f) { uiStateFilter.set(f); brnRenderVariance(); }
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
  // PR-U1: no day judged yet (today, or no sales in yet): no verdict in the head either
  const anyJudged = d.rows.some(r => r.judged ?? r.confirmed);
  if (d.stock) return _brnDailyCardsStock(d, openFn, anyJudged);
  const head = `<div class="text-xs" style="color:var(--text-secondary);margin-bottom:6px">${anyJudged
    ? `รวมวันที่ยืนยันแล้ว · ยอดเบิก <b>${_brnBaht(t.issued_value)}</b> · ตามสูตร <b>${_brnBaht(t.bom_value)}</b> · <b>${bmcGapHtml(t.var_value, t.var_pct, 'issue')}</b>`
    : 'ยังไม่มีวันที่เทียบสูตรได้'}</div>`;
  return head + d.rows.slice().reverse().map(r => {
    const ink = _ISR_SLIP_INK[r.slip_status] || 'var(--text-muted)';
    const bom = r.trading ? _brnBaht(r.bom_value) : '—';
    return `<button type="button" class="brn-daycard" onclick="${openFn}('${r.date}')" style="border-left-color:${ink}">
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><b style="color:var(--text-primary)">${esc(fmtDayShort(r.date))}</b> ${_bmcChip(r.slip_status_th, ink)}${r.flagged ? ' ' + _bmcChip('เบิกเกินต่อเนื่อง', 'var(--bad-ink)') : ''}</div>
      <div class="text-xs" style="color:var(--text-secondary);margin-top:2px">${r.confirmed && (r.judged ?? true)
        ? `ยอดเบิก <b>${_brnBaht(r.issued_value)}</b> · ตามสูตร <b>${bom}</b> · <b>${bmcGapHtml(r.var_value, r.var_pct, 'issue')}</b>`
        : r.confirmed ? `ยอดเบิก <b>${_brnBaht(r.issued_value)}</b> · ${esc(_isrNoVerdict(r))}`      // PR-U1: no formula value, or today: no verdict
        : `ยังไม่มียอดเบิกให้เทียบ · ตามสูตร <b>${bom}</b>`}</div></button>`;
  }).join('');
}
// PR-S4 · mode stock: the cards compare the day with the branch's normal and the all-branch median
function _brnDailyCardsStock(d, openFn, anyJudged) {
  const t = d.totals;
  const head = `<div class="text-xs" style="color:var(--text-secondary);margin-bottom:6px">${anyJudged
    ? `รวมวันที่ยืนยันแล้ว · ยอดเบิก <b>${_brnBaht(t.issued_value)}</b> · ปกติของสาขา <b>${_brnBaht(t.normal_value)}</b> · <b>${stkGapHtml(t.var_value, t.var_pct, 'issue_n')}</b> · ค่ากลางทุกสาขา <b>${_brnBaht(t.median_value)}</b> · <b>${stkGapHtml(t.median_var_value, t.median_var_pct, 'issue_m')}</b>`
    : 'ยังไม่มีวันที่เทียบกับปกติได้'}</div>`;
  return head + d.rows.slice().reverse().map(r => {
    const ink = _ISR_SLIP_INK[r.slip_status] || 'var(--text-muted)';
    const judged = r.confirmed && (r.judged ?? true);
    const nrm = r.trading && r.normal_value != null ? _brnBaht(r.normal_value) : '—';
    const med = r.trading && r.median_value != null ? _brnBaht(r.median_value) : '—';
    return `<button type="button" class="brn-daycard" onclick="${openFn}('${r.date}')" style="border-left-color:${ink}">
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><b style="color:var(--text-primary)">${esc(fmtDayShort(r.date))}</b> ${_bmcChip(r.slip_status_th, ink)}${r.flagged ? ' ' + _bmcChip('เบิกเกินต่อเนื่อง', 'var(--bad-ink)') : ''}${r.under_flagged ? ' ' + _bmcChip('เบิกต่ำกว่าปกติต่อเนื่อง', 'var(--amber-ink)') : ''}</div>
      <div class="text-xs" style="color:var(--text-secondary);margin-top:2px">${judged
        ? `ยอดเบิก <b>${_brnBaht(r.issued_value)}</b> · ปกติของสาขา <b>${nrm}</b>${r.var_pct != null ? ' · <b>' + stkGapHtml(r.var_value, r.var_pct, 'issue_n') + '</b>' : ''} · ค่ากลาง <b>${med}</b>${r.median_var_pct != null ? ' · <b>' + stkGapHtml(r.median_var_value, r.median_var_pct, 'issue_m') + '</b>' : ''}`
        : r.confirmed ? `ยอดเบิก <b>${_brnBaht(r.issued_value)}</b> · ${esc(_isrNoVerdict(r))}`
        : `ยังไม่มียอดเบิกให้เทียบ · ปกติของสาขา <b>${nrm}</b> · ค่ากลาง <b>${med}</b>`}</div></button>`;
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
  const stk = !!d.stock;                                      // PR-S4: mode stock - the normal of the branch and the median instead of the recipe
  const note = conf ? '' : `<div class="text-xs mt-2" style="color:var(--amber-ink)">ใบตัดเบิกวันนั้นยังไม่ยืนยัน · แสดงเฉพาะปริมาณ${stk ? 'ปกติของสาขา' : 'ตามสูตร'}</div>`;
  const flags = d.flags.length ? `<div class="flex flex-wrap gap-1 mt-2">${d.flags.map(f => stk ? _isrFlagChip(f, false) : _bmcChip(`${f.name} เบิกเกินสูตร ${f.days} วันติดต่อกัน`, 'var(--bad-ink)')).join(' ')}</div>` : '';
  const rows = d.rows.map(r => {
    return `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2"><b>${esc(_brnNm(r))}</b>${r.signal ? ' ' + _bmcChip(r.signal_th, r.signal === 'streak' ? 'var(--bad-ink)' : 'var(--amber-ink)') : ''}
        <div class="text-[10.5px]" style="color:var(--text-muted)">${_brnLine(_brnSrc(r), r.waste_reason_th ? 'ของเสีย: ' + esc(r.waste_reason_th) + (r.note ? ' (' + esc(r.note) + ')' : '') : '')}</div></td>
      <td class="py-1 pr-2 text-right">${r.issued == null ? '—' : brnQtyUnit(r.issued, r)}<div class="text-[10.5px]" style="color:var(--text-muted)">/ ${stk ? (r.normal == null ? '—' : brnQtyUnit(r.normal, r)) + (r.median == null ? '' : ' · ' + brnQtyUnit(r.median, r)) : brnQtyUnit(r.bom, r)}</div></td>
      <td class="py-1 text-right">${stkGapHtml(r.var_value, r.var_pct, stk ? 'issue_n' : 'issue', true)}</td></tr>`;
  }).join('');
  return head + note + flags + (d.rows.length ? `<div style="overflow-x:auto" class="mt-2"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left">
      <th class="py-1 pr-2">รายการ</th><th class="py-1 pr-2 text-right">${stk ? 'เบิก / ปกติของสาขา · ค่ากลาง' : 'เบิก / ตามสูตร'}</th><th class="py-1 text-right">${stk ? 'เทียบปกติ' : 'เทียบสูตร'}</th></tr></thead><tbody>${rows}</tbody></table></div>`
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
  bmcDownload(`${API}/api/bom-center/issue-reports/reconcile.xlsx?${_brnQS()}&end_count_id=${_brnRecon.period.end_count_id}`, 'กระทบยอด.xlsx', 'brnRcStatus');
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
let _brnBuf = { pct: 0, reason: '', note: '', sales: null };   // PR-19: the branch's expectation for the cover period; PR-V: `sales` = the baht it expects (a new branch: the figure the order is built on)
let _brnOrdEdited = new Set();                    // codes the person typed in this session (kept when the buffer changes)
let _brnCovers = null;                            // /api/bom-center/order/covers
const _BRN_ROUNDS = [['1', 'รอบ 1'], ['2', 'รอบ 2'], ['salmon', 'แซลมอนสด']];

// PR-M3 · the coming deliveries, ONE line each: "รอบ 2 · ส. 10/10 · ส่งใบสั่งซื้อภายใน พฤ. 8/10 เวลา 13:15" and a state chip
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
    return `<button onclick="brnSetDelivery('${d.round}','${d.delivery}')" style="display:flex;flex-wrap:wrap;gap:2px 8px;align-items:center;justify-content:space-between;border:1px solid ${on ? 'var(--accent)' : 'var(--border)'};border-radius:12px;padding:6px 12px;min-height:44px;font-size:13px;cursor:pointer;text-align:left;background:${on ? 'var(--accent)' : 'transparent'};color:${on ? 'var(--on-accent)' : 'var(--text-primary)'}">
      <span>${esc(d.round_th)} · ${esc(fmtDayShort(d.delivery))} · ส่งใบสั่งซื้อภายใน ${esc(fmtCutoff(cut))}</span>
      <span style="display:inline-block;padding:1px 8px;border-radius:999px;border:1px solid ${on ? 'currentColor' : 'var(--border)'};font-size:11px;white-space:nowrap;color:${on ? 'inherit' : ink}">${st}</span></button>`;
  }).join('');
}
let _brnOrdWhy = new Set();   // the rows whose "ดูที่มา" is open
function brnOrdWhy(code) { if (_brnOrdWhy.has(code)) _brnOrdWhy.delete(code); else _brnOrdWhy.add(code); brnRenderOrder(); }
let _brnBufOpen = false;      // "ยอดขายรอบนี้ต่างจากปกติ?" is folded until asked for
function brnToggleBuffer() { _brnBufOpen = !_brnBufOpen; _brnRenderBuffer(_brnPlan, true); }
function _brnBufQS() { return [_brnBuf.pct ? 'buffer_pct=' + encodeURIComponent(_brnBuf.pct) : '', _brnBuf.sales ? 'expected_sales=' + encodeURIComponent(_brnBuf.sales) : ''].filter(Boolean).join('&'); }
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
    _brnOrdQty = {}; _brnOrdReason = {}; _brnOrdEdited = new Set(); _brnOrdTyped = {};
    const o = _brnPlan.order;
    _brnPlan.rows.forEach(r => { _brnOrdQty[r.code] = _brnSugStr(r); });
    if (o) o.lines.forEach(l => {
      _brnOrdQty[l.code] = l.confirmed_units == null ? '' : String(l.confirmed_units);
      if (l.reason_code) _brnOrdReason[l.code] = { reason_code: l.reason_code, note: l.note || '' };
    });
    if (kept) {                                  // a new buffer: untouched lines take the new suggestion, typed ones stay
      _brnPlan.rows.forEach(r => { if (!kept.edited.has(r.code)) _brnOrdQty[r.code] = _brnSugStr(r); });
      kept.edited.forEach(c => { if (c in kept.qty) _brnOrdQty[c] = kept.qty[c]; if (kept.reason[c]) _brnOrdReason[c] = kept.reason[c]; });
      _brnOrdEdited = kept.edited;
    } else { _brnOrdDirty = false; }
    const pb = _brnPlan.buffer || {};
    _brnBuf = { pct: Number(pb.pct) || 0, reason: o && o.buffer_reason ? o.buffer_reason : (opts.withBuffer ? _brnBuf.reason : ''),
                note: o && o.buffer_note ? o.buffer_note : (opts.withBuffer ? _brnBuf.note : ''),
                sales: opts.withBuffer ? _brnBuf.sales : (o && o.buffer_sales ? Number(o.buffer_sales) : null) };
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
// PR-S3 (O1 ก): a row with `suggested === null` has no suggestion at all - the box starts empty and no reason is asked for what is typed
function _brnNoSug(r) { return r.suggested === null; }
function _brnSugStr(r) { return _brnNoSug(r) ? '' : String(r.order_units); }
// PR-U1: the rows of the draft whose quantity differs from the suggestion beyond the limit and that have no reason (or "อื่น ๆ" with no
// note) - the same rule as orders.save on the server, read off the same suggestion the row shows
function _brnOrdMissingReasons(p) {
  const o = p.order, editable = !o || ['suggested', 'confirmed'].includes(o.status);
  if (!editable) return [];
  return (p.rows || []).filter(r => {
    const v = _brnOrdQty[r.code];
    if (v === undefined || v === '') return false;
    const why = _brnOrdReason[r.code] || {};
    if (why.reason_code === 'other' && !String(why.note || '').trim()) return true;
    return !_brnNoSug(r) && _brnOrdNeedsReason(r.order_units, v) && !why.reason_code;
  });
}
function _brnOrdFocusRow(code) {
  const q = document.getElementById('brnOrdQ'), only = document.getElementById('brnOrdOnly');
  if (q && q.value) { q.value = ''; }
  if (only && only.checked && !(Number(_brnOrdQty[code]) > 0)) only.checked = false;
  if (_brnOrdPage !== 'central') _brnOrdPage = 'central';
  brnRenderOrder();
  const row = document.getElementById('brnOrdRow_' + code);
  if (!row) return;
  row.classList.add('brn-need');
  row.scrollIntoView({ behavior: 'smooth', block: 'center' });
  const sel = row.querySelector('.brn-reason select');
  if (sel) { try { sel.focus({ preventScroll: true }); } catch (e) { /* focus is a convenience */ } }
}
function _brnUnitLabel(r) { return r.order_unit === 'pack' ? (r.recv_unit || 'แพ็ค') : (r.base_unit || ''); }
// PR-U1 · a row ordered in base units (no pack size): the box takes the bigger unit (กิโลกรัม for กรัม); the working copy `_brnOrdQty` and
// everything saved stay in base units. `_brnOrdTyped` keeps the text as typed so a half-typed "18." does not jump.
let _brnOrdTyped = {};
function _brnOrdF(r) { return bmcTypeF(r); }
// PR-U2: the system stock of a row with no pack size in the unit its order is typed in ("-63.0 กิโลกรัม", not "-62,995 กรัม")
function _brnOrdSysTxt(r) {
  const unit = r.system_unit || r.count_unit || r.base_unit || '', f = _brnOrdF(r);
  if (f > 1 && (!r.system_unit || r.system_unit === r.base_unit)) {
    const v = Number(r.system_qty) / f;
    return `${_bmcNum(v, Math.abs(v) >= 10 ? 1 : 2)} ${_brnOrdUnitShow(r)}`;
  }
  return _brnSysTxt(r.system_qty, unit);
}
function _brnOrdUnitShow(r) { return _brnUnitFull(_brnOrdF(r) > 1 ? (r.show_unit || _brnUnitLabel(r)) : _brnUnitLabel(r)); }
function _brnOrdBoxVal(r, v) {
  const f = _brnOrdF(r);
  if (f === 1 || v === '' || v == null || isNaN(Number(v))) return v ?? '';
  const t = _brnOrdTyped[r.code];
  if (t !== undefined && t !== '' && Math.abs(Number(t) * f - Number(v)) < 1e-6) return t;
  return String(Math.round(Number(v) / f * 1000) / 1000);
}
function brnOrdInput(code, raw) {
  const r = ((_brnPlan || {}).rows || []).find(x => x.code === code), f = _brnOrdF(r);
  if (f === 1) _brnOrdQty[code] = raw;
  else {
    const t = String(raw || '').replace(',', '.').trim();
    _brnOrdTyped[code] = t;
    _brnOrdQty[code] = t === '' || isNaN(Number(t)) ? t : String(Math.round(Number(t) * f * 1000) / 1000);
  }
  _brnOrdEdited.add(code);
  brnRenderOrderDeferred();
}

// S11: page 1 = the centre's list (sent to Sales Support) · page 2 = local suppliers (the branch's own list)
let _brnOrdPage = 'central';
function brnSetOrdPage(k) { _brnOrdPage = k; brnRenderOrder(); }
function _brnRenderPages(p) {
  const box = document.getElementById('brnOrdPages');
  if (!p.central_list) { box.innerHTML = ''; _brnOrdPage = 'central'; return; }
  const nLocal = (p.local_rows || []).filter(r => r.order_units > 0).length;
  const b = (k, t) => { const on = k === _brnOrdPage;
    return `<button onclick="brnSetOrdPage('${k}')" style="border:1px solid ${on ? 'var(--accent)' : 'var(--border)'};border-radius:var(--r-sm);padding:6px 12px;font-size:13px;cursor:pointer;background:${on ? 'var(--accent-soft, transparent)' : 'transparent'};color:var(--text-primary);font-weight:${on ? 600 : 400}">${t}</button>`; };
  box.innerHTML = b('central', `สั่งจากไดกิโตะ กรุ๊ป (${p.rows.filter(r => r.order_units > 0).length})`) + b('local', `ซื้อเอง (${nLocal})`);
}
// a base quantity in the unit the branch counts in (กก. · แพ็ค …), whole numbers when it is large
function _brnInCount(r, v) { return _bmcNum(Number(v || 0) / (r.per_count_unit || 1), 1); }
function _brnCountUnit(r) { return r.count_unit || r.base_unit || ''; }
function _brnRenderLocal(p) {
  const q = (document.getElementById('brnOrdQ').value || '').trim().toLowerCase();
  const only = document.getElementById('brnOrdOnly').checked;
  const rows = (p.local_rows || []).filter(r => (!only || r.order_units > 0) && (!q || (r.code + ' ' + r.name + ' ' + (r.short_name || '')).toLowerCase().includes(q)));
  const analyst = hasModule('bom.center');                 // a missing sub-recipe is the analyst's work, not the branch's
  document.getElementById('brnOrdList').innerHTML = `<div class="text-xs mb-1" style="color:var(--text-muted)">วัตถุดิบที่ไดกิโตะ กรุ๊ปไม่ได้จัดส่ง สาขาซื้อเอง · ไม่ส่งเข้าใบสั่งซื้อ</div>` +
    (rows.map(r => `<div style="border-top:1px solid var(--border);padding:8px 0;display:flex;flex-wrap:wrap;gap:6px 8px;align-items:baseline">
      <div style="flex:1 1 220px;min-width:0"><div class="text-sm" style="color:var(--text-primary);font-weight:600">${esc(_brnNm(r))}
        ${analyst && r.kind === 'prepared' && BC_MODE !== 'stock' ? _bmcChip('กึ่งสำเร็จ: ยังไม่มีสูตร', 'var(--amber-ink)') : ''}</div>
        <div class="text-[11px]" style="color:var(--text-muted)">${_brnLine(_brnSrc(r), `ต้องใช้ ${_brnInCount(r, r.need)} ${esc(_brnUnitFull(_brnCountUnit(r)))}`)}</div></div>
      <div class="text-sm" style="margin-left:auto;color:var(--text-primary)">แนะนำ <b>${_bmcNum(r.order_units, 0)}</b> ${esc(_brnUnitLabel(r))}</div></div>`).join('')
      || '<span class="text-xs" style="color:var(--good-ink)">✓ ไม่มีรายการที่ต้องซื้อเอง</span>');
  document.getElementById('brnOrdActions').innerHTML = '';
  _brnOrdQuick(false);
}

// PR-M7 · สั่งตามยอดแนะนำทั้งหมด: one button above the list of an editable draft
function _brnOrdQuick(show) {
  const q = document.getElementById('brnOrdQuick');
  if (!q) return;
  q.style.display = show ? '' : 'none';
  q.innerHTML = show ? `<button type="button" id="brnOrdFillBtn" onclick="brnOrdFillAll()">สั่งตามยอดแนะนำทั้งหมด</button>
    <span class="text-xs" style="color:var(--text-muted)">ใส่ยอดแนะนำให้ทุกรายการในรอบนี้ · แก้เฉพาะรายการที่ต่าง</span>` : '';
}
async function brnOrdFillAll() {
  const p = _brnPlan;
  if (!p || !p.rows || !_brnCanOrder()) return;
  if (p.order && !['suggested', 'confirmed'].includes(p.order.status)) return;      // sent already: nothing to fill
  // a row the person (or the saved draft) holds at another figure than the live suggestion is an edited row
  const edited = p.rows.filter(r => !_brnNoSug(r) && Number(_brnOrdQty[r.code] || 0) !== Number(r.order_units));
  if (edited.length && !await uiConfirm(`ยืนยันการใส่ยอดแนะนำทับ ${edited.length} รายการที่แก้ไว้?`,
      { title: 'ใส่ยอดแนะนำทั้งหมด', ok: 'ใส่ยอดแนะนำ', cancel: 'ยกเลิก' })) return;
  p.rows.forEach(r => { if (!_brnNoSug(r)) _brnOrdQty[r.code] = String(r.order_units); });
  edited.forEach(r => { delete _brnOrdReason[r.code]; });                         // the reason belonged to the old figure
  _brnOrdEdited.clear();
  brnRenderOrder();
  _brnOrdTouched();                                                               // the draft saves itself like any other change
  _bmcStatus('brnOrdStatus', 'ใส่ยอดแนะนำให้ทุกรายการแล้ว · แก้เฉพาะรายการที่ต่าง', 'var(--good-ink)');
}

let _brnMissLater = false;     // 2.4: "ทำภายหลัง" hides the backlog line until the page is reloaded
function brnMissLater() { _brnMissLater = true; brnRenderOrder(); }
// PR-B4a · why a suggested figure is not the plain forecast, one chip each (1.3 estimate · 1.5 shelf life · 1.6 no price · 1.7 round down · 1.8 lead time and short shipments)
// PR-S3: what a missing slip day is estimated from - the branch's own mean of that weekday in stock mode, the recipes of the sales otherwise
function _brnEstWord(basis) { return basis === 'slips' ? 'ประมาณจากใบตัดเบิกเฉลี่ย' : 'ประมาณจากยอดขาย'; }
function _brnEstHow(basis) { return basis === 'slips' ? 'ตามค่าเฉลี่ยใบตัดเบิกของสาขาในวันเดียวกันของสัปดาห์ 8 สัปดาห์ก่อน' : 'ตามสูตรจากยอดขาย'; }
function _brnRowFlags(r, nEst) {
  const c = [];
  if (r.estimate_changes && nEst) c.push(_bmcChip(`${_brnEstWord(((_brnPlan || {}).stock || {}).estimate_basis)} ${nEst} วัน`, 'var(--amber-ink)'));   // only when it changes the packs ordered; the head says it once
  if (r.shelf_cap_days) c.push(_bmcChip(`จำกัดตามอายุการเก็บ ${r.shelf_cap_days} วัน`, 'var(--navy-ink)'));
  if (r.round_down) c.push(_bmcChip('ปัดลง (เหลือพอ)', 'var(--navy-ink)'));
  if (r.price_note) c.push(_bmcChip(r.price_note, 'var(--text-muted)'));
  if (r.lead_extra_days) c.push(_bmcChip(`สั่งให้พอเพิ่ม ${r.lead_extra_days} วัน (ระยะส่ง ${r.lead_days} วัน)`, 'var(--navy-ink)'));
  if (r.short_ship_skipped) c.push(_bmcChip(`ไม่นับ ${r.short_ship_skipped} วันที่คลังส่งไม่ครบ`, 'var(--text-muted)'));
  return c.length ? `<div class="flex flex-wrap gap-1 mt-0.5">${c.join('')}</div>` : '';
}
// PR-S3 · where the suggestion comes from: the confirmed slips (with how much of the window they cover), or - while the branch has fewer than
// 14 confirmed days - the sentence the server gives (no suggestion / the benchmark)
function _brnSrcLine(p) {
  const s = p.source || {}, f = p.fallback;
  const t = f ? f.message : (s.kind === 'slips' ? `ยอดแนะนำจากใบตัดเบิก ${s.window_days} วัน (ยืนยัน ${s.confirmed_days}/${s.window_days} วัน)` : '');
  return t ? `<div class="mt-1" style="color:${f ? 'var(--amber-ink)' : 'var(--text-secondary)'}">${esc(t)}</div>` : '';
}
function brnRenderOrder() {
  const p = _brnPlan;
  if (!p) return;
  _brnRenderPages(p);
  if (_brnOrdPage === 'local' && p.central_list) { _brnRenderLocal(p); return; }
  const o = p.order;
  const editable = !o || ['suggested', 'confirmed'].includes(o.status);
  _brnOrdQuick(editable && p.rows.some(r => r.order_units > 0));
  _brnRenderBuffer(p, editable);
  brnFormMount('ord', editable);                          // PR-SI: the Excel form, only while the order can still be changed
  const up = 1 + ((p.buffer || {}).pct || 0) / 100;
  const stk = p.stock || {};
  const slip = stk.slip_today || {};
  const slipOk = _brnSlipOk(p);
  const md = stk.missing_days || [];
  const nEst = (stk.estimated_days || []).length;                         // 1.3 · missing slips taken out at the BoM of the sales
  const stockLine = (stk.deducted
    ? `${_bmcChip('หักคงเหลือตามระบบแล้ว', 'var(--good-ink)')}${nEst ? ' ' + _bmcChip(`${_brnEstWord(stk.estimate_basis)} ${nEst} วัน (${_brnIssRuns(stk.estimated_days)})`, 'var(--amber-ink)') : ''}${slipOk ? ` · ใบตัดเบิกวันนี้: ยืนยันแล้ว เวลา ${esc(_brnIssTime(slip.confirmed_at))}` : ''}`
    : `${_bmcChip('ไม่มีผลตรวจนับสิ้นเดือน', 'var(--text-muted)')} ${uiQ('brn:nostock', 'ไม่มีผลตรวจนับสิ้นเดือน', '<p>ยังไม่มีผลตรวจนับสิ้นเดือน ยอดแนะนำจึงไม่หักคงเหลือ</p>')}${slipOk ? ` · ใบตัดเบิกวันนี้: ยืนยันแล้ว เวลา ${esc(_brnIssTime(slip.confirmed_at))}` : ''}`);
  // the one thing that blocks sending is an action card of its own above the list (not a line inside the header)
  // 2.4: only TODAY's slip blocks the order; the days behind it can be made later ("ทำภายหลัง" hides that line for this session)
  const missLine = md.length && !_brnMissLater
    ? `<div class="text-xs mt-1" style="color:var(--amber-ink)">ใบที่ค้าง ${md.length} วัน (${esc(_brnIssRuns(md))}) · <a href="#" onclick="brnMissLater();return false" style="text-decoration:underline;color:inherit">ทำภายหลัง</a>${nEst ? ' ' + uiQ('brn:miss', 'ใบที่ค้าง', `<p>ทำภายหลังได้</p><p>คงเหลือตามระบบหัก${_brnEstHow(stk.estimate_basis)} ${nEst} วันไว้ก่อน เมื่อยืนยันใบของวันนั้น ตัวเลขในใบจะใช้แทน</p>`) : ''}</div>` : '';
  const gate = document.getElementById('brnOrdGate');
  if (gate) {
    const show = !slipOk && editable;
    gate.style.display = show ? '' : 'none';
    gate.innerHTML = show ? `<div class="flex flex-wrap items-center gap-3" style="border-left:4px solid var(--amber-ink);padding-left:10px">
        <div style="flex:1 1 220px;min-width:0"><div class="text-sm font-semibold" style="color:var(--text-primary)">ยืนยันใบตัดเบิกวันนี้แล้วส่งใบสั่งซื้อได้</div>
          ${missLine}</div>
        <button onclick="brnIssGoToday()" style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:9px 16px;font-size:14px;cursor:pointer;white-space:nowrap">ไปที่ตัดเบิก</button></div>` : '';
  }
  const gapLine = slipOk && editable ? missLine : '';
  const byCode = {};
  p.rows.forEach(r => { byCode[r.code] = r; });
  const extra = o ? o.lines.filter(l => !byCode[l.code]) : [];
  const total = Object.entries(_brnOrdQty).reduce((a, [c, v]) => {
    const r = byCode[c];
    if (!r || !r.price_per_base || !(Number(v) > 0)) return a;
    return a + Number(v) * (r.order_unit === 'pack' ? (r.qty_per_recv || 1) : 1) * r.price_per_base;
  }, 0);
  const left = p.hours_left == null || !(p.open && editable) ? '' : ` (${fmtLeft(p.hours_left)})`;
  document.getElementById('brnOrdHead').innerHTML = `<div style="color:var(--text-primary)" class="text-sm">รอบส่ง <b>${esc(fmtDayShort(p.delivery))}</b> · ส่งใบสั่งซื้อภายใน <b>${esc(p.cutoff_th)}</b>${left} ·
      สั่งให้พอใช้ ${esc(_brnIssRunTxt(p.cover_from, p.cover_to))} (${p.cover_days} วัน)</div>
    <div class="mt-1" style="color:var(--text-secondary)">${stockLine}</div>${_brnSrcLine(p)}${gapLine}
    <div class="mt-1" style="color:var(--text-secondary)">${o ? `ใบสั่งซื้อ ${_bmcRoundDay({ round: p.round || _brnRound, delivery: p.delivery })} ${_bmcChip(o.status_th, o.status === 'received' ? 'var(--good-ink)' : (o.status === 'submitted' || o.status === 'processed') ? 'var(--navy-ink)' : 'var(--amber-ink)')}${o.late ? ' ' + _bmcChip('ส่งล่าช้า (มีค่าปรับ)', 'var(--bad-ink)') : ''}` : _bmcChip('ยังไม่มีใบสั่งซื้อรอบนี้', 'var(--text-muted)')}
      · ${o && !editable   // a sent order: the two values come from the server (the same numbers as the list and the queue)
        ? `${VALUE_ORDERED_TH} <b>฿${_bmcNum(o.value_ordered, 0)}</b>${o.value_sent != null ? ` · ${VALUE_SENT_TH} <b>฿${_bmcNum(o.value_sent, 0)}</b>` : ''}`
        : `${VALUE_ORDERED_TH} ≈ <b>฿${_bmcNum(total, 0)}</b>`}${p.calendar_filled ? '' : ' · <span style="color:var(--amber-ink)">ใช้วันส่งค่าเริ่มต้น</span>'}</div>`;
  const banner = document.getElementById('brnOrdBanner');
  if (banner) {                                    // past the cutoff and still a draft: the late fee, in red
    const showLate = !!p.late && editable;
    banner.style.display = showLate ? '' : 'none';
    banner.innerHTML = showLate ? `<div class="text-xs" style="border:1px solid var(--bad-ink);border-radius:var(--r-sm);padding:8px 10px;color:var(--bad-ink)">เลยเวลาตัดรอบ (${esc(p.cutoff_th)}) ส่งตอนนี้มีค่าปรับล่าช้า ${uiQ('brn:late', 'ส่งหลังเวลาตัดรอบ', '<p>ส่งใบสั่งซื้อหลังเวลาตัดรอบได้ แต่มีค่าปรับล่าช้า และใบสั่งซื้อติดป้าย “ส่งล่าช้า (มีค่าปรับ)”</p><p>เวลาตัดรอบ: ก่อน 13:15 สองวันก่อนวันส่ง</p>')}</div>` : '';
  }
  const q = (document.getElementById('brnOrdQ').value || '').trim().toLowerCase();
  const only = document.getElementById('brnOrdOnly').checked;
  const rows = p.rows.filter(r => (!only || _brnNoSug(r) || r.order_units > 0 || Number(_brnOrdQty[r.code] || 0) > 0)
    && (!q || (r.code + ' ' + r.name + ' ' + (r.short_name || '')).toLowerCase().includes(q)));
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
    // a draft follows the live plan (the system stock moves with every confirmed slip; the save refreshes the
    // stored suggestion the same way) — only a submitted order keeps the suggestion it was sent with
    const frozen = line[r.code] && !editable;
    const base = frozen ? line[r.code].suggested_units : (r.order_units_base ?? r.order_units);
    const sugg = frozen ? (up === 1 ? base : Math.round(base * up)) : r.order_units;
    const v = _brnOrdQty[r.code] ?? '';
    const noSug = _brnNoSug(r) || (frozen && line[r.code].suggested_units == null);     // PR-S3 (O1 ก): nothing was suggested, so nothing to explain
    const dev = !noSug && _brnOrdNeedsReason(sugg, v);
    const F = _brnOrdF(r);
    // where the suggestion comes from, in the unit the branch counts in (the engine works in grams / base units)
    const cu = esc(_brnCountUnit(r));
    const why = `${r.class ? `กลุ่มมูลค่า${{ A: 'สูง', B: 'กลาง', C: 'ต่ำ' }[r.class] || ''} · ` : ''}ยอดขายที่คาดไว้ใช้ ${_brnInCount(r, r.forecast)} + สำรอง ${_brnInCount(r, r.safety)}` +
      (r.on_hand != null ? ` − คงเหลือคาดการณ์ ${_brnInCount(r, Math.max(r.projected, 0))}` : '') +
      (r.on_order ? ` (รวมค้างส่ง ${_brnInCount(r, r.on_order)})` : '') + ` = ต้องการ ${_brnInCount(r, r.need)} ${cu}` +
      (analyst && (r.via || []).length ? ` · ใช้ใน ${esc(r.via.slice(0, 2).join(', '))}` : '');
    const open = _brnOrdWhy.has(r.code);
    // PR-V: the second reading of the same history - what this item normally uses per ฿100,000 of sales
    const ps = r.per_sales;
    const perSales = ps ? `<div class="mt-1">ปกติใช้ <b>${_bmcNum(Number(ps.per_100k) / (r.per_count_unit || 1), Number(ps.per_100k) / (r.per_count_unit || 1) >= 10 ? 1 : 2)}</b> ${cu} ต่อยอดขาย ฿100,000${ps.steady === false ? ` · <span style="color:var(--amber-ink)">ใช้ไม่สม่ำเสมอ (แกว่ง ±${_bmcNum(ps.swing_pct, 0)}% ต่อสัปดาห์)</span>` : ''}${r.ref_label ? ` · ${esc(r.ref_label)}` : ''}</div>` : '';
    // PR-U: an item bought by weight but used by the whole piece (ปลาแซลมอนทั้งตัว) shows the pieces too, as an estimate
    const approxPcs = (r.approx_unit && r.approx_per_unit > 0 && sugg > 0)
      ? ` · ≈ ${(pcs => _bmcNum(pcs, pcs >= 10 ? 0 : 1))(sugg * (r.order_unit === 'pack' ? (r.qty_per_recv || 1) : 1) / r.approx_per_unit)} ${esc(r.approx_unit)}` : '';   // PR-W: a whole number from 10 up, one decimal below
    const sysBox = r.system_qty == null ? '' : ` · คงเหลือตามระบบ <b style="color:${r.system_qty < 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">${esc(_brnOrdSysTxt(r))}</b>`;
    const lastBox = (!noSug || r.last_order_qty == null) ? '' : ` · สั่งครั้งก่อน <b style="color:var(--text-primary)">${_bmcNum(r.last_order_qty / F, F > 1 ? 2 : 0)}</b> ${esc(_brnOrdUnitShow(r))}`;   // PR-S3 (O1 ก)
    const needWhy = dev && editable && !((_brnOrdReason[r.code] || {}).reason_code);
    return `<div class="brn-row${needWhy ? ' brn-need' : ''}" id="brnOrdRow_${esc(r.code)}" style="border-top:1px solid var(--border);padding:8px 0">
      <div style="display:flex;gap:8px;align-items:flex-start">
        <div class="brn-namecol" style="flex:1 1 0;min-width:0"><div class="brn-name" title="${esc(r.name)}" style="color:var(--text-primary);font-weight:600">${esc(_brnNm(r))}${r.monitored ? ' ' + _bmcChip('ต้องนับ', 'var(--amber-ink)') : ''}</div>
          <div class="text-xs" style="color:var(--text-secondary);line-height:1.4">${noSug ? 'แนะนำ <b style="color:var(--text-muted)">–</b>' : `แนะนำ <b style="color:var(--text-primary)">${_bmcNum(sugg / F, F > 1 ? 2 : 0)}</b> ${esc(_brnOrdUnitShow(r))}${up !== 1 ? ` <span class="text-[10.5px]">(ปกติ ${_bmcNum(base / F, F > 1 ? 2 : 0)})</span>` : ''}${approxPcs}`}${sysBox}${lastBox}</div>
          ${r.no_pack ? `<div class="mt-1">${bmcNoPackChip(r)}</div>` : ''}
          ${frozen ? '' : _brnRowFlags(r, nEst)}
          ${r.ref_label ? `<div class="text-[10.5px]" style="color:var(--text-muted)">${esc(r.ref_label)}</div>` : ''}
          ${noSug ? '' : `<button type="button" class="brn-why" aria-expanded="${open}" onclick="brnOrdWhy(${_bmcQ(r.code)})">${open ? 'ซ่อนที่มา ▴' : 'ดูที่มา ▾'}</button>`}</div>
        <div class="brn-box"><input id="brnOrd_${esc(r.code)}" class="brn-qty" inputmode="decimal" autocomplete="off" value="${esc(_brnOrdBoxVal(r, v))}" ${editable ? '' : 'disabled'} aria-label="สั่ง ${esc(_brnNm(r))}" oninput="brnOrdInput(${_bmcQ(r.code)}, this.value)"
            style="border-color:${dev ? 'var(--amber-ink)' : 'var(--border-control)'}"><span class="brn-cap">สั่ง · ${esc(_brnOrdUnitShow(r))}</span></div></div>
      ${open && !noSug ? `<div class="text-[11px] mt-1" style="color:var(--text-muted)">${esc(r.code)}${r.short_name && r.name && r.short_name !== r.name ? ' · ' + esc(r.name) : ''} · ${why}${perSales}</div>` : ''}
      ${dev ? reasonSel(r.code) : ''}</div>`;
  }).join('') + extra.map(l => `<div style="border-top:1px solid var(--border);padding:8px 0" class="text-sm">${esc(_brnNm(l))} · สั่งเพิ่มนอกยอดแนะนำ ${esc(bmcShowQtyTxt(l.confirmed_units, l, l.unit_word || (l.order_unit === 'base' ? '' : 'แพ็ค')))} ${l.reason_th ? '· ' + esc(l.reason_th) : ''}${l.no_pack ? ' ' + bmcNoPackChip(l) : ''}</div>`).join('');
  const needSales = !!(p.ref_mode && p.ref_mode.needs_sales);                 // PR-V: a new branch that has not said what it expects to sell
  document.getElementById('brnOrdList').innerHTML = needSales ? `<span class="text-xs" style="color:var(--amber-ink)">${esc(p.ref_mode.message || '')}</span>`
    : html || '<span class="text-xs" style="color:var(--good-ink)">✓ รอบนี้ไม่มีรายการที่ต้องสั่ง (คงเหลือเพียงพอ)</span>';
  const btn = (label, on, primary) => `<button onclick="${on}" style="border:${primary ? '0' : '1px solid var(--border)'};border-radius:var(--r-sm);padding:8px 14px;font-size:14px;cursor:pointer;background:${primary ? 'var(--accent)' : 'transparent'};color:${primary ? 'var(--on-accent)' : 'var(--text-primary)'}">${label}</button>`;
  let acts = '';
  if (editable && needSales) acts = '';
  else if (editable) {
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
// PR-V · baht -> % (mirrors per_sales.pct_from_sales): (expected / normal - 1) x 100, held to the buffer's range
function _brnPctFromSales(expected, normal) {
  const e = Number(String(expected).replace(/,/g, '')), n = Number(normal);
  if (!isFinite(e) || e < 0 || String(expected).trim() === '') return { pct: null, msg: 'ยอดขายที่คาดต้องเป็นตัวเลขและไม่ติดลบ' };
  if (!(n > 0)) return { pct: null, msg: 'ยังไม่มียอดขายปกติของช่วงนี้ให้เทียบ · ใส่ % เอง' };
  const b = (_brnPlan && _brnPlan.buffer) || {}, lo = b.min ?? -50, hi = b.max ?? 100;
  const raw = (e / n - 1) * 100, pct = Math.max(lo, Math.min(hi, raw));
  const clamped = Math.abs(pct - raw) > 1e-9, sg = v => (v > 0 ? '+' : '') + _bmcNum(v, 0) + '%';
  return { pct: Math.round(pct * 10) / 10, clamped, raw,
           msg: clamped ? `ต่างจากปกติ ${sg(raw)} เกินช่วง (${lo}% ถึง +${hi}%) · ใช้ ${sg(pct)} แทน` : '' };
}
// PR-W: the baht box shows thousands separators while typing (digits and one decimal point); the request keeps the raw number
function _brnBahtFmt(v) {
  const t = String(v ?? '').replace(/[^0-9.]/g, '');
  const i = t.indexOf('.'), ip = (i < 0 ? t : t.slice(0, i)).replace(/^0+(?=\d)/, ''), dp = i < 0 ? '' : '.' + t.slice(i + 1).replace(/\./g, '').slice(0, 2);
  return ip.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + dp;
}
function brnBufBahtInput(init) {
  const el = /** @type {HTMLInputElement|null} */ (document.getElementById('brnBufBaht')), pe = /** @type {HTMLInputElement|null} */ (document.getElementById('brnBufPct')), hint = document.getElementById('brnBufCalc');
  if (!el || !pe || !hint) return;
  const digitsBefore = String(el.value).slice(0, el.selectionStart ?? 0).replace(/[^0-9.]/g, '').length;      // keep the caret after the same digit
  const f = _brnBahtFmt(el.value);
  if (f !== el.value) {
    el.value = f;
    let n = 0, pos = f.length;
    for (let k = 0; k < f.length; k++) { if (n === digitsBefore) { pos = k; break; } if (/[0-9.]/.test(f[k])) n++; }
    try { el.setSelectionRange(pos, pos); } catch (e) { /* not a text box */ }
  }
  if (String(el.value).trim() === '') { hint.textContent = ''; return; }
  const r = _brnPctFromSales(el.value, (_brnPlan || {}).sales_normal);
  if (r.pct === null) { hint.style.color = 'var(--amber-ink)'; hint.textContent = r.msg; return; }
  if (!init) pe.value = r.pct;                    // the % box follows the baht typed; on opening the card it keeps what was applied
  // = ลด 56% จากปกติ · ระบบใช้ −50%   /   = เพิ่ม 20% จากปกติ
  const rw = r.raw ?? r.pct, nn = v => _bmcNum(v, v >= 10 ? 0 : 1), sg = v => (v > 0 ? '+' : v < 0 ? '−' : '') + nn(Math.abs(v)) + '%';
  hint.style.color = r.clamped ? 'var(--amber-ink)' : 'var(--text-secondary)';
  hint.textContent = !(Math.abs(rw) > 0) ? '= เท่ากับปกติ (0%)' : `= ${rw > 0 ? 'เพิ่ม' : 'ลด'} ${nn(Math.abs(rw))}% จากปกติ` + (r.clamped ? ` · ระบบใช้ ${sg(r.pct)}` : '');
}
function brnBufPctInput() {                       // a % typed by hand is no longer the baht typed before
  const el = document.getElementById('brnBufBaht'), hint = document.getElementById('brnBufCalc');
  if (el) el.value = '';
  if (hint) hint.textContent = '';
}
// PR-V · a new branch (fewer than 14 trading days) with a reference branch: the order is built from that branch's usage per
// ฿100,000 x the sales this branch expects, so the card asks for the baht instead of a %
function _brnRenderRefCard(box, p, editable) {
  const rm = p.ref_mode, cur = _brnBuf;
  const refTxt = rm.benchmark ? esc(rm.name) : `สาขาอ้างอิง ${esc(rm.name)}`, refOf = rm.benchmark ? esc(rm.name) : `สาขา${esc(rm.name)}`;   // PR-X: the benchmark names no branch
  const inp = 'border:1px solid var(--border-control);border-radius:var(--r-sm);padding:5px 8px;font-size:16px;background:var(--bg-input);color:var(--text-primary)';
  if (!editable) {
    box.innerHTML = `<div class="text-xs" style="color:var(--text-secondary)">${p.fallback ? 'ใบตัดเบิกยังไม่ครบ' : 'สาขาใหม่'} · คำนวณจาก<b>${refTxt}</b>${cur.sales ? ` · ยอดขายที่คาด <b>฿${_bmcNum(cur.sales, 0)}</b>` : ''}</div>`;
    return;
  }
  const own = p.fallback ? `ใบตัดเบิกที่ยืนยันแล้วเพียง ${rm.own_days} วัน (ต่ำกว่า ${rm.min_days} วัน)` : `สาขานี้มียอดขายเพียง ${rm.own_days} วัน (ต่ำกว่า ${rm.min_days} วัน)`;
  box.innerHTML = `<div class="text-sm" style="font-weight:600;color:var(--text-primary)">${p.fallback ? 'ใบตัดเบิกยังไม่ครบ' : 'สาขาใหม่'} · คำนวณจาก${refTxt}</div>
    <div class="text-[11px] mt-1" style="color:var(--text-muted)">${own} ${uiQ('brn:ref', p.fallback ? 'คำนวณจากค่ากลางอย่างไร' : 'สาขาใหม่คำนวณอย่างไร', `<p>${own} จึงใช้ปริมาณวัตถุดิบต่อยอดขาย ฿100,000 ของ${refOf} คูณยอดขายที่คาดของรอบนี้</p>`)}</div>
    <div class="flex flex-wrap items-center gap-2 mt-2 text-xs">
      <label style="color:var(--text-secondary)">ยอดขายที่คาดของรอบนี้ (บาท)
        <input id="brnRefSales" type="text" inputmode="numeric" autocomplete="off" value="${rm.expected_sales ? esc(String(rm.expected_sales)) : ''}" style="width:130px;height:36px;margin-left:6px;text-align:right;${inp}"></label>
      <button onclick="brnApplyRefSales()" style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:8px 14px;font-size:13px;cursor:pointer;min-height:36px">คำนวณยอดแนะนำ</button>
    </div>
    <div class="text-[11px] mt-1" style="color:var(--text-muted)">ยอดขายไม่รวมภาษีมูลค่าเพิ่ม${rm.ref_sales_normal ? ` · ${rm.benchmark ? 'ค่ากลางสาขาของบริษัท' : 'สาขา' + esc(rm.name)}ขายปกติในช่วงนี้ ≈ ฿${_bmcNum(rm.ref_sales_normal, 0)}` : ''}</div>
    ${rm.needs_sales || rm.message ? `<div class="text-xs mt-1" style="color:var(--amber-ink)">${esc(rm.message || '')}</div>` : ''}`;
}
async function brnApplyRefSales() {
  const raw = String((document.getElementById('brnRefSales') || {}).value || '').replace(/,/g, '').trim();
  const v = Number(raw);
  if (!raw || !isFinite(v) || v <= 0) { _bmcStatus('brnOrdStatus', 'ยังไม่มีประวัติการขายพอ กรอกยอดขายที่คาดของรอบนี้ (ตัวเลขมากกว่า 0)', 'var(--amber-ink)'); return; }
  _brnBuf = Object.assign({}, _brnBuf, { pct: 0, reason: '', note: '', sales: v });
  await brnLoadOrder({ withBuffer: true, keepEdits: true });
  _bmcStatus('brnOrdStatus', `คำนวณยอดแนะนำจากยอดขายที่คาด ฿${_bmcNum(v, 0)} แล้ว`, 'var(--good-ink)');
  _brnOrdTouched();
}
function _brnRenderBuffer(p, editable) {
  const box = document.getElementById('brnOrdBuffer');
  if (!box) return;
  if (p.ref_mode && p.ref_mode.active) { _brnRenderRefCard(box, p, editable); return; }
  const b = p.buffer || { pct: 0, reasons: [] };
  const cur = _brnBuf;
  const sign = v => (Number(v) > 0 ? '+' : '') + _bmcNum(v, 0) + '%';
  if (!editable) {
    box.innerHTML = cur.pct ? `<div class="text-xs" style="color:var(--text-secondary)">สาขาปรับยอดพยากรณ์ <b>${sign(cur.pct)}</b>${cur.sales ? ` (คาดยอดขาย ฿${_bmcNum(cur.sales, 0)})` : ''} · ${esc(_brnBufReasonTh(cur.reason))}${cur.note ? ' · ' + esc(cur.note) : ''}</div>` : '';
    return;
  }
  const inp = 'border:1px solid var(--border-control);border-radius:var(--r-sm);padding:5px 8px;font-size:16px;background:var(--bg-input);color:var(--text-primary)';
  // folded by default: most rounds go as the system suggests. The line says whether an adjustment is in use.
  const head = `<button type="button" onclick="brnToggleBuffer()" aria-expanded="${_brnBufOpen}" style="display:flex;align-items:center;gap:8px;width:100%;min-height:44px;border:0;background:none;padding:0;text-align:left;cursor:pointer;color:var(--text-primary);font-size:13px;font-weight:600">
      <span>ยอดขายรอบนี้ต่างจากปกติ?</span>${cur.pct ? _bmcChip(`ใช้อยู่ ${sign(cur.pct)}`, 'var(--navy-ink)') : ''}<span style="margin-left:auto;color:var(--text-muted)">${_brnBufOpen ? '▴' : '▾'}</span></button>`;
  if (!_brnBufOpen) { box.innerHTML = head; return; }
  // PR-W: the usual sales of the period sit right above the baht box, so the figure to compare with is in front of the person typing
  box.innerHTML = head + `<div class="text-[11px] mb-1" style="color:var(--text-muted)">มีงานหรือโปรโมชัน ใส่ % หรือยอดขายที่คาด ${uiQ('brn:buf', 'ยอดขายรอบนี้ต่างจากปกติ', '<p>มีงานหรือโปรโมชัน ใส่ % ที่คาดว่าจะเพิ่มหรือลด หรือใส่ยอดขายที่คาดของรอบนี้เป็นบาท</p>')}</div>
    <div class="text-xs mb-1" style="color:var(--text-secondary)">${p.sales_normal ? `ยอดขายปกติของช่วงนี้ ≈ <b style="color:var(--text-primary)">฿${_bmcNum(p.sales_normal, 0)}</b> (ไม่รวมภาษี)` : 'ยังไม่มียอดขายปกติของช่วงนี้ให้เทียบ'}</div>
    <div class="flex flex-wrap items-start gap-2 text-xs">
      <input id="brnBufPct" type="number" inputmode="numeric" step="5" min="${b.min ?? -50}" max="${b.max ?? 100}" value="${cur.pct || 0}" oninput="brnBufPctInput()" style="width:72px;height:34px;text-align:center;${inp}"><span style="color:var(--text-secondary)">%</span>
      <span style="color:var(--text-muted)">หรือ</span>
      <div style="flex:1 1 220px;min-width:0"><label style="color:var(--text-secondary)">คาดยอดขายรอบนี้ (บาท)
        <input id="brnBufBaht" type="text" inputmode="numeric" autocomplete="off" value="${cur.sales ? esc(_brnBahtFmt(cur.sales)) : ''}" oninput="brnBufBahtInput()" style="width:130px;height:34px;margin-left:6px;text-align:right;${inp}"></label>
        <div id="brnBufCalc" class="text-[11px] mt-1" style="color:var(--text-secondary)"></div></div></div>
    <div class="flex flex-wrap items-center gap-2 mt-2 text-xs">
      <select id="brnBufReason" style="${inp}"><option value="">— เหตุผล —</option>${(b.reasons || []).map(r => `<option value="${r.code}" ${r.code === cur.reason ? 'selected' : ''}>${esc(r.label)}</option>`).join('')}</select>
      <input id="brnBufNote" value="${esc(cur.note || '')}" placeholder="หมายเหตุ" style="flex:1 1 120px;min-width:0;${inp}">
      <button onclick="brnApplyBuffer()" style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:7px 12px;font-size:13px;cursor:pointer">ใช้กับยอดแนะนำ</button>
      <button onclick="brnToggleCovers()" style="border:1px solid var(--border);border-radius:var(--r-sm);padding:7px 12px;font-size:13px;cursor:pointer;background:transparent;color:var(--text-primary)">คำนวณจากจำนวนลูกค้า</button>
    </div>
    ${_brnCovPhone() ? '' : '<div id="brnCovers" class="mt-2" style="display:none"></div>'}`;
  if (cur.sales) brnBufBahtInput(true);               // the change the baht stands for, next to the box, from the first look
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
  const bahtRaw = String((document.getElementById('brnBufBaht') || {}).value || '').replace(/,/g, '').trim();
  const sales = bahtRaw && isFinite(Number(bahtRaw)) && Number(bahtRaw) > 0 ? Number(bahtRaw) : null;
  _brnBuf = { pct, reason: pct ? reason : '', note: pct ? note : '', sales };
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
  box.innerHTML = `<div class="text-[11px] mb-1" style="color:var(--text-muted)">กรอกจำนวนลูกค้าที่คาด แล้วแปลงเป็น % ${uiQ('brn:cov', 'จำนวนลูกค้าที่คาด', `<p>ปกติ = ค่าเฉลี่ยวันเดียวกันของสัปดาห์ ${esc(fmtDay(c.history_from))} ถึง ${esc(fmtDay(c.history_to))}</p>`)}</div>
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
// 1.9 · what the confirm dialog says before an order goes: "88 รายการ · ฿281,292 · รอบก่อน ฿262,700 (+7%)" and the 3 lines that changed
// most against the previous order of the same round (p.previous, from the server). Every figure is a quantity ordered at the month's price.
function _brnOrdSummary(p, qty) {
  const rows = (p.rows || []).filter(r => Number(qty[r.code]) > 0);
  const val = r => Number(qty[r.code]) * (r.order_unit === 'pack' ? (r.qty_per_recv || 1) : 1) * (r.price_per_base || 0);
  const value = rows.reduce((a, r) => a + val(r), 0);
  const prev = p.previous || null;
  let head = `${rows.length} รายการ · ฿${_bmcNum(value, 0)}`;
  if (prev && prev.value > 0) {
    const pct = (value - prev.value) / prev.value * 100;
    head += ` · รอบก่อน ฿${_bmcNum(prev.value, 0)} (${pct > 0 ? '+' : pct < 0 ? '−' : ''}${_bmcNum(Math.abs(pct), Math.abs(pct) >= 10 ? 0 : 1)}%)`;
  } else head += prev ? ' · รอบก่อนยังไม่มีมูลค่า' : '';
  const diffs = [];
  const seen = new Set();
  rows.forEach(r => {
    const old = prev && (prev.lines || []).find(l => l.code === r.code);
    seen.add(r.code);
    const f = r.no_pack && Number(r.show_factor) > 1 ? Number(r.show_factor) : 1;      // PR-U1: a base-unit line in the bigger unit, as the row shows it
    diffs.push({ name: _brnNm(r), from: (old ? old.units : 0) / f, to: Number(qty[r.code]) / f, unit: f > 1 ? (r.show_unit || '') : (_brnUnitW(_brnUnitLabel(r)) || (old ? old.unit_word : '')), d: val(r) - (old ? old.value : 0) });
  });
  ((prev && prev.lines) || []).forEach(l => { if (!seen.has(l.code)) diffs.push({ name: _brnNm(l), from: l.units, to: 0, unit: l.unit_word, d: -l.value }); });
  const top = prev ? diffs.filter(x => Math.abs(x.d) >= 1 || x.from !== x.to).sort((a, b) => Math.abs(b.d) - Math.abs(a.d)).slice(0, 3) : [];
  const lines = top.map(x => `• ${x.name}: ${_bmcNum(x.from, 1)} → ${_bmcNum(x.to, 1)} ${x.unit} (${x.d >= 0 ? '+' : '−'}฿${_bmcNum(Math.abs(x.d), 0)})`);
  return { n: rows.length, value, head, top, text: head + (lines.length ? `\nเปลี่ยนจากรอบก่อนมากที่สุด:\n${lines.join('\n')}` : '') };
}
async function brnOrderSave(submit, quiet, ackLate) {
  clearTimeout(_brnOrdAuto);
  if (!_brnCanOrder()) { if (!quiet) _bmcStatus('brnOrdStatus', _BRN_READONLY, 'var(--amber-ink)'); return; }
  if (!_brnPlan || !_brnPlan.rows) return;
  const cur = _brnPlan.order;
  if (cur && !['suggested', 'confirmed'].includes(cur.status)) return;      // sent already: nothing to save
  const p = _brnPlan;
  if (p.ref_mode && p.ref_mode.needs_sales) {                              // PR-V: a new branch orders from the sales it expects
    if (!quiet) _bmcStatus('brnOrdStatus', p.ref_mode.message || 'ยังไม่มีประวัติการขายพอ กรอกยอดขายที่คาดของรอบนี้', 'var(--amber-ink)');
    return;
  }
  if (submit && !ackLate) {
    if (!_brnSlipOk(p)) {                                                   // the order is made from the stock after today's issue
      _bmcStatus('brnOrdStatus', 'ต้องยืนยันใบตัดเบิกของวันนี้ก่อนส่งใบสั่งซื้อ', 'var(--amber-ink)');
      return;
    }
    // PR-U1: a line that needs a reason is found BEFORE the confirm dialog (the server refuses it too): no dialog, the first such row is
    // brought into view and outlined
    const need = _brnOrdMissingReasons(p);
    if (need.length) {
      _bmcStatus('brnOrdStatus', `ต้องระบุเหตุผล ${need.length} รายการก่อนส่ง: ${need.slice(0, 3).map(_brnNm).join(', ')}${need.length > 3 ? ' …' : ''}`, 'var(--bad-ink)');
      _brnOrdFocusRow(need[0].code);
      return;
    }
    const late = !!p.late;
    if (!await uiConfirm((late ? _BRN_LATE_MSG(p.cutoff_th) + '\n' : '')
        + `ยืนยันส่งใบสั่งซื้อรอบส่ง${p.delivery_th}?\n${_brnOrdSummary(p, _brnOrdQty).text}\nแก้ไขได้จนกว่าคลังยืนยันยอดส่ง`, { title: 'ยืนยันการส่งใบสั่งซื้อ', ok: 'ส่งใบสั่งซื้อ', cancel: 'ยกเลิก' })) return;
    ackLate = late;
  }
  try {
    const r = await _bmcJson(`${API}/api/bom-center/order/save${_brnQS() ? '?' + _brnQS() : ''}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ round: _brnRound, delivery: p.delivery, lines: _brnOrdLines(), submit,
                             buffer: p.ref_mode && p.ref_mode.active ? { pct: 0, sales: _brnBuf.sales }
                               : (_brnBuf.pct ? { pct: _brnBuf.pct, reason: _brnBuf.reason, note: _brnBuf.note, sales: _brnBuf.sales } : { pct: 0, sales: _brnBuf.sales }),
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
    _bmcStatus('brnOrdStatus', submit ? `✓ ส่งใบสั่งซื้อ ${_bmcRoundDay({ round: p.round || _brnRound, delivery: p.delivery })} แล้ว · ฿${_bmcNum(r.value, 0)}${r.late ? ' · ส่งล่าช้า มีค่าปรับ' : ''}` :
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
    // PR-D6: an order is finished once the branch received it (or it was cancelled); the ones still on their way are the list (shared filter)
    _brnHist('brnOrdHistory', d.orders, x => x.status === 'received' || x.status === 'cancelled', x => `<div style="border-top:1px solid var(--border);padding:4px 0;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center">
        <span><a href="#" onclick="brnOpenOrder(${x.order_id});return false" style="color:var(--accent);font-weight:600">${esc(_bmcRoundDay(x))}</a> · ตัดรอบ ${esc(fmtDay(x.cycle_date))} · ${esc(x.status_th)} · ${esc(x.value_label || 'มูลค่า')} ฿${_bmcNum(x.value, 0)}${x.late ? ' ' + _bmcChip('ส่งล่าช้า (มีค่าปรับ)', 'var(--bad-ink)') : ''}</span>
        ${x.status === 'processed' ? `<a href="#" onclick="brnSetSub('recv');setTimeout(() => brnOpenReceive(${x.order_id}), 200);return false" style="margin-left:auto;color:var(--accent)">รับสินค้าที่มาถึง →</a>` : ''}</div>`, 'ใบสั่งซื้อ');
  } catch (e) { document.getElementById('brnOrdHistory').textContent = String(e.message || e); }
}
// one order, read-only: what was suggested, confirmed, sent and received
async function brnOpenOrder(id) {
  const box = document.getElementById('brnOrdDetail');
  try {
    const o = await _bmcJson(`${API}/api/bom-center/order/${id}`);
    box.style.display = '';
    box.innerHTML = `<div class="flex flex-wrap items-center gap-2"><div class="text-sm font-semibold" style="color:var(--text-primary)">ใบสั่งซื้อ ${esc(_bmcRoundDay(o))} · ตัดรอบ ${esc(fmtDay(o.cycle_date))} ${_bmcChip(o.status_th, 'var(--navy-ink)')}${o.late ? ' ' + _bmcChip('ส่งล่าช้า (มีค่าปรับ)', 'var(--bad-ink)') : ''}</div>
        <button onclick="document.getElementById('brnOrdDetail').style.display='none'" class="ml-auto" style="border:0;background:none;color:var(--text-muted);font-size:12px;cursor:pointer">ปิด</button></div>
      <div class="text-[11px] mt-1" style="color:var(--text-muted)">${o.po_ref ? 'PO ' + esc(o.po_ref) + ' · ' : ''}${o.processed_by ? 'คลังยืนยันยอดส่งโดย ' + esc(whoTh(o.processed_by)) : ''}</div>
      <table class="w-full text-xs mt-2"><thead><tr class="text-left" style="color:var(--text-muted)"><th class="pr-2">รายการ</th><th class="pr-2 text-right">แนะนำ</th><th class="pr-2 text-right">สั่ง</th><th class="pr-2 text-right">ส่งจริง</th><th class="text-right">รับจริง</th></tr></thead>
      <tbody>${o.lines.filter(l => l.confirmed_units || l.processed_units || l.received_units).map(l => {
        const f = _brnOrdF(l), n = v => _bmcNum(Number(v) / f, f > 1 ? 2 : 0), uw = f > 1 ? l.show_unit : l.unit_word;   // PR-U1: no pack size -> กิโลกรัม
        return `<tr style="border-top:1px solid var(--border-soft)"><td class="pr-2 py-1">${esc(_brnNm(l))}${uw ? ` <span class="text-[10.5px]" style="color:var(--text-muted)">· หน่วย ${esc(uw)}</span>` : ''}${l.no_pack ? ' ' + bmcNoPackChip(l) : ''}${l.reason_th ? ` <span style="color:var(--amber-ink)">· ${esc(l.reason_th)}</span>` : ''}</td>
        <td class="pr-2 text-right">${n(l.suggested_units)}</td><td class="pr-2 text-right">${n(l.confirmed_units)}</td><td class="pr-2 text-right">${l.processed_units != null ? n(l.processed_units) : '–'}</td><td class="text-right">${l.received_units != null ? n(l.received_units) : '–'}</td></tr>`;
      }).join('')}</tbody></table>`;
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
          <div class="text-xs" style="color:var(--text-secondary)">รับแล้ว ${esc(fmtDayAt(o.received_at))} · ${o.lines} รายการ</div></div>
        ${_brnCanClaim() ? `<button class="brn-act brn-act-line" onclick="brnClaimOpen(${o.order_id})">แจ้งเคลม</button>` : ''}</div>`).join('');
    // PR-D6: a claim is finished once it is decided (approved, not approved, cancelled); the ones waiting for the decision are shown (shared filter)
    box.innerHTML = (recent || '<div class="text-xs" style="color:var(--text-muted)">ยังไม่มีใบสั่งซื้อที่บันทึกรับสินค้าแล้ว</div>')
      + (cl.claims.length ? `<div class="text-xs font-semibold mt-3" style="color:var(--text-primary)">เคลมที่แจ้งไว้</div><div id="brnClaimMine"></div>` : '');
    _brnHist('brnClaimMine', cl.claims, c => c.status !== 'submitted', c => { const st = _BRN_CLAIM_ST[c.status] || [c.status, 'var(--text-muted)'];
      return `<a href="#" class="brn-claimrow" onclick="brnClaimView(${c.claim_id});return false" style="display:block;border-top:1px solid var(--border);padding:8px 0;text-decoration:none">
        <div class="text-sm" style="color:var(--text-primary);font-weight:600">${esc(_brnRoundLabel(c.round))} · ส่ง ${esc(_brnIssDm(c.delivery || c.cycle_date))} · ${c.lines} รายการ · ${esc(_bmcClaimWhen(c))}</div>
        <div class="text-xs mt-0.5">${_bmcChip(c.origin_th || '', c.origin === 'receive' ? 'var(--navy-ink)' : 'var(--text-muted)')} ${_bmcChip(st[0], st[1])}</div></a>`; }, 'เคลม');
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}

function _brnClLines() { return _brnClaimOrd ? _brnClaimOrd.lines.filter(l => l.remaining_units > 0) : []; }
function _brnClLine(code) { return _brnClLines().find(l => l.code === code); }
function _brnClUnit(l) { return bmcTypeF(l) > 1 ? (l.show_unit || l.unit_word || '') : (l.unit_word || (l.order_unit === 'base' ? 'หน่วยฐาน' : 'แพ็ค')); }      // PR-W: the item's receiving unit word; PR-U2: กิโลกรัม for a line with no pack size
function _brnClQ(l, q) { const f = bmcTypeF(l); return _bmcNum(Number(q || 0) / f, f > 1 ? 3 : 2); }
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
  const hits = _brnClLines().filter(l => !_brnCl.picked.includes(l.code) && (!q || ((l.code || '') + ' ' + (l.name || '') + ' ' + (l.short_name || '')).toLowerCase().includes(q)));
  if (!q && hits.length > 8) { box.innerHTML = `<div class="text-xs" style="color:var(--text-muted);padding:4px 2px">พิมพ์ชื่อวัตถุดิบเพื่อเลือกรายการที่เคลม (ทั้งหมด ${hits.length} รายการ)</div>`; return; }
  box.innerHTML = hits.slice(0, 8).map(l => `<button type="button" class="brn-pick" onclick="brnClPick(${_bmcQ(l.code)})"><span style="flex:1 1 0;min-width:0;text-align:left;overflow-wrap:anywhere">${esc(_brnNm(l))}</span><span class="text-xs" style="color:var(--text-secondary);white-space:nowrap">รับแล้ว ${_brnClQ(l, l.received_units)} ${esc(_brnClUnit(l))}</span><span style="color:var(--accent);font-weight:600">เลือก</span></button>`).join('')
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
          <div class="brn-name" style="color:var(--text-primary);font-weight:600">${esc(_brnNm(l))}</div>
          <div class="brn-sub text-[11px]" style="color:var(--text-muted)">${_brnLine(_brnSrc(l), `ส่งจริง ${_brnClQ(l, l.sent_units)}`)} · รับแล้ว ${_brnClQ(l, l.received_units)}${l.claimed_units ? ' · เคลมไปแล้ว ' + _brnClQ(l, l.claimed_units) : ''} · เคลมได้อีก <b style="color:var(--text-primary)">${_brnClQ(l, l.remaining_units)}</b> ${unit}</div></div>
        <div class="brn-box"><input id="brnCq_${c}" class="brn-qty" inputmode="decimal" autocomplete="off" value="${esc(v.q || '')}" aria-label="จำนวนที่เคลม ${esc(_brnNm(l))}" oninput="brnClField(${cq}, 'q', this.value)"><span class="brn-cap">เคลม · ${unit}</span></div>
        <button type="button" class="brn-x" aria-label="เอาออก ${esc(_brnNm(l))}" onclick="brnClUnpick(${cq})">✕</button></div>
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
    const v = _brnCl.vals[code] || {}, raw = String(v.q || '').trim().replace(',', '.'), q = raw === '' ? NaN : bmcFromTyped(raw, bmcTypeF(l));
    const reason = v.r || '', note = String(v.n || '').trim();
    if (raw === '' || !Number.isFinite(q) || !(q > 0)) { bad(code, 'จำนวนที่เคลมต้องมากกว่า 0'); return; }
    if (q > l.remaining_units + 1e-9) { bad(code, `เคลมได้ไม่เกิน ${_brnClQ(l, l.remaining_units)} ${_brnClUnit(l)}`); return; }
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
    box.innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap"><div class="text-sm font-semibold" style="color:var(--text-primary)">${esc(_brnRoundLabel(c.round))} · ส่ง ${esc(_brnIssDm(c.delivery || c.cycle_date))} · ${esc(_bmcClaimWhen(c))} ${_bmcChip(c.origin_th || '', c.origin === 'receive' ? 'var(--navy-ink)' : 'var(--text-muted)')} ${_bmcChip(st[0], st[1])}</div>
        <button onclick="brnClaimClose()" style="border:1px solid var(--border);border-radius:var(--r-sm);padding:4px 12px;font-size:12px;cursor:pointer;background:transparent;color:var(--text-primary)">ปิด</button></div>
      ${c.decision_note ? `<div class="text-xs mt-1" style="color:var(--text-secondary)">ผลพิจารณา${c.decided_by ? ' โดย ' + esc(whoTh(c.decided_by)) : ''}: ${esc(c.decision_note)}</div>` : ''}
      ${_brnClaimEffect(c)}
      <div style="overflow-x:auto" class="mt-2"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left"><th class="pr-2">วัตถุดิบ</th><th class="pr-2 text-right">ส่งจริง</th><th class="pr-2 text-right">รับแล้ว</th><th class="pr-2 text-right">เคลม</th><th class="pr-2 text-right">อนุมัติ</th><th>สาเหตุ</th></tr></thead><tbody>
        ${c.lines.map(l => `<tr style="border-top:1px solid var(--border)"><td class="py-1 pr-2">${esc(_brnNm(l))}<div class="text-[10.5px]" style="color:var(--text-muted)">${_brnLine(_brnSrc(l), l.unit ? 'หน่วย ' + esc(_brnUnitFull(l.unit)) : '')}</div></td>
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

// ── PR-SI · แบบฟอร์ม Excel: ใบตัดเบิก · ใบสั่งซื้อ · รับสินค้า ─────────────────────────────────────────────────────
// Keying in the app stays the default. The same page can hand out an Excel form and read it back: the file is checked first (a dry run: a table,
// nothing written) and saved on the person's say-so. The server runs the very save the app uses (bom_center/branch_forms.py); this block only
// shows the check and sends the file. Every word of the forms is in _BRN_FORM so the wording is changed in one place.
const _BRN_FORM = {
  download: 'ดาวน์โหลดแบบฟอร์ม', pick: 'นำเข้าไฟล์', days: 'จำนวนวัน', draft: 'บันทึกร่าง', confirmSlip: 'ยืนยันใบตัดเบิก', sendOrder: 'ส่งใบสั่งซื้อ',
  saveRecv: 'บันทึกรับสินค้า', close: 'ปิด', ack: 'ตรวจสภาพและจำนวนครบแล้ว', claimNote: 'ข้อความถึงประสานงานขาย (ถ้ามี)',
  help: {
    iss: 'กรอกในแอปหรือใช้แบบฟอร์ม Excel ก็ได้ ผลเหมือนกัน · แบบฟอร์มกรอกได้ครั้งละไม่เกิน 7 วัน',
    ord: 'กรอกในแอปหรือใช้แบบฟอร์ม Excel ก็ได้ ผลเหมือนกัน',
    rcv: 'กรอกในแอปหรือใช้แบบฟอร์ม Excel ก็ได้ ผลเหมือนกัน',
  },
  state: {
    new: ['ใหม่', 'var(--navy-ink)'], draft: ['มีร่างอยู่', 'var(--navy-ink)'], confirmed: ['ยืนยันแล้ว', 'var(--bad-ink)'],
    future: ['วันล่วงหน้า', 'var(--bad-ink)'], too_old: ['เกิน 60 วัน', 'var(--bad-ink)'], posted: ['งวดล็อก', 'var(--bad-ink)'],
  },
};
const _BRN_FORM_BOX = { iss: 'brnIssForm', ord: 'brnOrdForm', rcv: 'brnRecvForm' };
let _brnFormKind = null;       // the pane that asked for the file picker
let _brnFormSt = null;         // {kind, file, pre}: the file under check and what the server said

function _brnFormAddDays(iso, n) { const d = new Date(String(iso) + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); }
function _brnFormInput() {
  let el = /** @type {HTMLInputElement|null} */ (document.getElementById('brnFormFile'));
  if (!el) {
    el = document.createElement('input');
    el.type = 'file'; el.id = 'brnFormFile'; el.accept = '.xlsx'; el.style.display = 'none';
    el.addEventListener('change', () => brnFormPicked(el));
    document.body.appendChild(el);
  }
  return el;
}
// the row under the page's own controls: the download, the days (slip only) and the import; `show` false hides it (an order already sent)
function brnFormMount(kind, show) {
  const box = document.getElementById(_BRN_FORM_BOX[kind]);
  if (!box) return;
  const on = _brnCanOrder() && show !== false;
  box.style.display = on ? '' : 'none';
  if (!on) { box.innerHTML = ''; return; }
  if (box.querySelector('.brn-formrow')) return;
  const sel = 'border:1px solid var(--border-control);border-radius:var(--r-sm);padding:6px 8px;font-size:16px;min-height:44px;background:var(--bg-input);color:var(--text-primary)';
  box.innerHTML = `<div class="brn-formrow" style="display:flex;flex-wrap:wrap;align-items:center;gap:8px">
      <button type="button" class="brn-act brn-act-line" onclick="brnFormDownload('${kind}')">${_BRN_FORM.download}</button>
      ${kind === 'iss' ? `<label class="text-xs" style="color:var(--text-secondary)">${_BRN_FORM.days} <select id="brnIssFormDays" style="${sel}">${[1, 2, 3, 4, 5, 6, 7].map(n => `<option value="${n}">${n}</option>`).join('')}</select></label>` : ''}
      <button type="button" class="brn-act brn-act-line" onclick="brnFormPick('${kind}')">${_BRN_FORM.pick}</button>
      ${uiQ('brn:form:' + kind, _BRN_FORM.download, `<p>${esc(_BRN_FORM.help[kind])}</p>`)}
      <span id="brnFormStatus_${kind}" class="brn-status" role="status"></span></div>
    <div class="brn-formpre mt-2"></div>`;
}
function brnFormDownload(kind) {
  const qs = _brnQS();
  const stat = 'brnFormStatus_' + kind;
  if (kind === 'iss') {
    if (!_brnIss) return;
    const n = Number((document.getElementById('brnIssFormDays') || { value: 1 }).value || 1);
    const start = _brnFormAddDays(_brnIss.date, -(n - 1));            // the days end on the day on screen
    bmcDownload(`${API}/api/bom-center/issues/template.xlsx?${[qs, 'date=' + start, 'days=' + n].filter(Boolean).join('&')}`, 'แบบฟอร์ม_ใบตัดเบิก.xlsx', stat);
  } else if (kind === 'ord') {
    const p = _brnPlan;
    if (!p) return;
    bmcDownload(`${API}/api/bom-center/order/template.xlsx?${[qs, 'round=' + (p.round || _brnRound), 'delivery=' + p.delivery].filter(Boolean).join('&')}`, 'แบบฟอร์ม_ใบสั่งซื้อ.xlsx', stat);
  } else if (_brnRecvOrd) {
    bmcDownload(`${API}/api/bom-center/order/${_brnRecvOrd.order_id}/receive-template.xlsx${qs ? '?' + qs : ''}`, 'แบบฟอร์ม_รับสินค้า.xlsx', stat);
  }
}
function brnFormPick(kind) {
  _brnFormKind = kind;
  const el = _brnFormInput();
  el.value = '';
  el.click();
}
// the URL of the import of a kind; `o` carries what the call needs beyond the branch
function _brnFormUrl(kind, dry, o) {
  const qs = _brnQS(), q = (a) => a.filter(Boolean).join('&');
  if (kind === 'iss') return `${API}/api/bom-center/issues/import?${q([qs, 'dry_run=' + dry, 'submit=' + !!o.submit])}`;
  if (kind === 'ord') {
    const p = _brnPlan;
    return `${API}/api/bom-center/order/import?${q([qs, 'round=' + (p.round || _brnRound), 'delivery=' + p.delivery, 'dry_run=' + dry, 'submit=' + !!o.submit, 'ack_late=' + !!o.ackLate])}`;
  }
  const day = (document.getElementById('brnRecvOrdDate') || { value: '' }).value;
  return `${API}/api/bom-center/order/${_brnRecvOrd.order_id}/receive-import?${q([qs, 'dry_run=' + dry, 'ack=' + !!o.ack, day ? 'receipt_date=' + day : ''])}`;
}
function _brnFormBody(file, extra) {
  const fd = new FormData();
  fd.append('file', file, file.name);
  Object.entries(extra || {}).forEach(([k, v]) => { if (v) fd.append(k, v); });
  return fd;
}
async function brnFormPicked(el) {
  const file = el.files && el.files[0];
  const kind = _brnFormKind;
  if (!file || !kind) return;
  const stat = 'brnFormStatus_' + kind;
  _bmcStatus(stat, 'กำลังตรวจไฟล์…', 'var(--text-muted)');
  try {
    const pre = await _bmcJson(_brnFormUrl(kind, true, {}), { method: 'POST', body: _brnFormBody(file) });
    _brnFormSt = { kind, file, pre };
    _bmcStatus(stat, '');
    brnFormShow();
  } catch (e) { _bmcStatus(stat, 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 220), 'var(--bad-ink)'); }
}
function brnFormClose() {
  const st = _brnFormSt;
  _brnFormSt = null;
  if (!st) return;
  const box = document.querySelector('#' + _BRN_FORM_BOX[st.kind] + ' .brn-formpre');
  if (box) box.innerHTML = '';
}
function _brnFormProbs(errs) {
  const list = (errs || []).slice(0, 3).map(e => (e.row ? `แถว ${e.row}: ` : '') + e.message);
  const more = (errs || []).length > 3 ? ` และอีก ${errs.length - 3} รายการ` : '';
  return list.map(esc).join('<br>') + more;
}
function _brnFormBtn(label, on, primary, off) {
  return `<button type="button" class="brn-act${primary ? '' : ' brn-act-line'}" onclick="${on}"${off ? ' disabled' : ''}>${label}</button>`;
}
const _BRN_FORM_TABLE = 'width:100%;border-collapse:collapse';
function _brnFormPreIss(d) {
  const head = ['วันที่', 'รายการ', 'เบิกใช้', 'ของเสีย'];
  const rows = d.dates.map(x => {                       // the problems of a date sit on a line of their own (a phone has no room for a fifth column)
    const [t, ink] = _BRN_FORM.state[x.state] || [x.state, 'var(--text-muted)'];
    return `<tr style="border-top:1px solid var(--border-soft);vertical-align:top"><td class="py-1 pr-2">${esc(fmtDayShort(x.date))} ${_bmcChip(t, ink)}</td>
      <td class="py-1 pr-2 text-right">${x.lines}</td><td class="py-1 pr-2 text-right">${x.used_lines}</td><td class="py-1 pr-2 text-right">${x.waste_lines}</td></tr>`
      + (x.errors.length ? `<tr><td colspan="4" class="pb-1" style="color:var(--bad-ink)">ปัญหา: ${_brnFormProbs(x.errors)}</td></tr>` : '');
  }).join('');
  const fe = (d.file_errors || []).length ? `<div class="text-xs mb-1" style="color:var(--bad-ink)">${_brnFormProbs(d.file_errors)}</div>` : '';
  const none = !d.dates.some(x => !x.errors.length) || (d.file_errors || []).length > 0;
  return `${fe}<div style="overflow-x:auto"><table class="text-xs" style="${_BRN_FORM_TABLE}"><thead><tr class="text-left" style="color:var(--text-muted)">${head.map((h, i) => `<th class="pr-2${i ? ' text-right' : ''}">${h}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>
    <div class="flex flex-wrap items-center gap-2 mt-2">${_brnFormBtn(_BRN_FORM.draft, 'brnFormApplyIss(false)', false, none)}${_brnFormBtn(_BRN_FORM.confirmSlip, 'brnFormApplyIss(true)', true, none)}${_brnFormBtn(_BRN_FORM.close, 'brnFormClose()', false)}</div>`;
}
function _brnFormPreOrd(d) {
  const p = _brnPlan || {};
  const slipOk = _brnSlipOk(p);
  const bad = d.errors.length > 0 || !d.lines;
  const chips = [_bmcChip(`${d.lines} รายการ`, 'var(--navy-ink)')];
  if (d.needs_reason_names && d.needs_reason_names.length) chips.push(_bmcChip(`ต้องระบุเหตุผล ${d.needs_reason_names.length} รายการก่อนส่ง`, 'var(--amber-ink)'));
  if (!slipOk) chips.push(_bmcChip('ต้องยืนยันใบตัดเบิกของวันนี้ก่อนส่ง', 'var(--amber-ink)'));
  if (p.late) chips.push(_bmcChip('ส่งล่าช้า (มีค่าปรับ)', 'var(--bad-ink)'));
  const names = d.needs_reason_names && d.needs_reason_names.length ? `<div class="text-xs mt-1" style="color:var(--amber-ink)">${esc(d.needs_reason_names.slice(0, 5).join(', '))}${d.needs_reason_names.length > 5 ? ' …' : ''}</div>` : '';
  const errs = d.errors.length ? `<div class="text-xs mt-1" style="color:var(--bad-ink)">${_brnFormProbs(d.errors)}</div>` : '';
  const cannotSend = bad || !slipOk || (d.needs_reason_names || []).length > 0;
  return `<div class="flex flex-wrap items-center gap-2">${chips.join(' ')}</div>${names}${errs}
    <div class="flex flex-wrap items-center gap-2 mt-2">${_brnFormBtn(_BRN_FORM.draft, 'brnFormApplyOrd(false)', false, bad)}${_brnFormBtn(_BRN_FORM.sendOrder, 'brnFormApplyOrd(true)', true, cannotSend)}${_brnFormBtn(_BRN_FORM.close, 'brnFormClose()', false)}</div>`;
}
function _brnFormPreRcv(d) {
  const chips = [_bmcChip(`${d.lines} รายการ`, 'var(--navy-ink)')];
  if (d.claim_lines) chips.push(_bmcChip(`เคลม ${d.claim_lines} รายการ`, 'var(--amber-ink)'));
  if ((d.over || []).length) chips.push(_bmcChip(`รับเกินที่ส่ง ${d.over.length} รายการ`, 'var(--navy-ink)'));
  const errs = d.errors.length ? `<div class="text-xs mt-1" style="color:var(--bad-ink)">${_brnFormProbs(d.errors)}</div>` : '';
  const ok = d.errors.length === 0;
  const sel = 'border:1px solid var(--border-control);border-radius:var(--r-sm);padding:6px 8px;font-size:16px;background:var(--bg-input);color:var(--text-primary)';
  return `<div class="flex flex-wrap items-center gap-2">${chips.join(' ')}</div>${errs}
    ${d.claim_lines ? `<textarea id="brnFormClaimNote" rows="2" placeholder="${_BRN_FORM.claimNote}" style="${sel};width:100%;margin-top:6px"></textarea>` : ''}
    <label class="mt-2" style="display:flex;gap:10px;align-items:center;min-height:44px;color:var(--text-primary);cursor:pointer;font-size:15px">
      <input type="checkbox" id="brnFormAck" onchange="document.getElementById('brnFormRcvSave').disabled = !this.checked || ${!ok}" style="width:24px;height:24px;flex:none">
      <span>${_BRN_FORM.ack}</span></label>
    <button type="button" class="brn-link" onclick="brnRcvTerms()">เงื่อนไขการเคลม</button>
    <div class="flex flex-wrap items-center gap-2 mt-2"><button type="button" id="brnFormRcvSave" class="brn-act" onclick="brnFormApplyRcv()" disabled>${_BRN_FORM.saveRecv}</button>${_brnFormBtn(_BRN_FORM.close, 'brnFormClose()', false)}</div>`;
}
function brnFormShow() {
  const st = _brnFormSt;
  if (!st) return;
  const box = document.querySelector('#' + _BRN_FORM_BOX[st.kind] + ' .brn-formpre');
  if (!box) return;
  box.innerHTML = `<div style="border:1px solid var(--border);border-radius:var(--r-md);padding:10px 12px"><div class="text-xs mb-1" style="color:var(--text-muted)">${esc(st.file.name)}</div>`
    + (st.kind === 'iss' ? _brnFormPreIss(st.pre) : st.kind === 'ord' ? _brnFormPreOrd(st.pre) : _brnFormPreRcv(st.pre)) + '</div>';
}
async function brnFormApplyIss(submit) {
  const st = _brnFormSt;
  if (!st || st.kind !== 'iss') return;
  const ok = st.pre.dates.filter(x => !x.errors.length);
  if (!ok.length) return;
  if (submit && !await uiConfirm((ok.some(x => x.late) ? 'ใบย้อนหลังนับเป็นล่าช้า\n' : '')
      + `ยืนยันใบตัดเบิก ${ok.length} วัน: ${ok.map(x => fmtDayShort(x.date)).join(', ')}\nยืนยันแล้วแก้ไขไม่ได้ ต้องการแก้ แจ้งประสานงานขาย`,
      { title: 'ยืนยันใบตัดเบิก', ok: 'ยืนยัน', cancel: 'กลับไปแก้' })) return;
  _bmcStatus('brnFormStatus_iss', 'กำลังบันทึก…', 'var(--text-muted)');
  try {
    const r = await _bmcJson(_brnFormUrl('iss', false, { submit }), { method: 'POST', body: _brnFormBody(st.file) });
    brnFormClose();
    _bmcStatus('brnFormStatus_iss', '');
    _brnPlan = null;                                  // the order suggestion follows the slips
    _brnIssDirty = false;
    await brnIssLoad({ quiet: true });
    const n = submit ? r.confirmed : r.saved;
    _bmcStatus('brnIssStatus', `✓ ${submit ? 'ยืนยันใบตัดเบิก' : 'บันทึกร่าง'} ${n} วัน` + (r.refused ? ` · ไม่บันทึก ${r.refused} วัน` : ''), r.refused ? 'var(--amber-ink)' : 'var(--good-ink)');
  } catch (e) { _bmcStatus('brnFormStatus_iss', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 220), 'var(--bad-ink)'); }
}
async function brnFormApplyOrd(submit, ackLate) {
  const st = _brnFormSt;
  const p = _brnPlan;
  if (!st || st.kind !== 'ord' || !p) return;
  if (submit && !ackLate) {
    if (!await uiConfirm((p.late ? _BRN_LATE_MSG(p.cutoff_th) + '\n' : '')
        + `ยืนยันส่งใบสั่งซื้อรอบส่ง${p.delivery_th}?\n${st.pre.lines} รายการ จากไฟล์\nแก้ไขได้จนกว่าคลังยืนยันยอดส่ง`, { title: 'ยืนยันการส่งใบสั่งซื้อ', ok: _BRN_FORM.sendOrder, cancel: 'ยกเลิก' })) return;
    ackLate = !!p.late;
  }
  _bmcStatus('brnFormStatus_ord', 'กำลังบันทึก…', 'var(--text-muted)');
  try {
    const r = await _bmcJson(_brnFormUrl('ord', false, { submit, ackLate }), { method: 'POST', body: _brnFormBody(st.file) });
    brnFormClose();
    _bmcStatus('brnFormStatus_ord', '');
    await brnLoadOrder();
    _bmcStatus('brnOrdStatus', submit ? `✓ ส่งใบสั่งซื้อ ${_bmcRoundDay({ round: p.round || _brnRound, delivery: p.delivery })} แล้ว · ฿${_bmcNum(r.value, 0)}${r.late ? ' · ส่งล่าช้า มีค่าปรับ' : ''}`
      : `✓ บันทึกร่าง${r.needs_reason.length ? ` · ต้องระบุเหตุผล ${r.needs_reason.length} รายการก่อนส่ง` : ''}`, r.needs_reason.length && !submit ? 'var(--amber-ink)' : 'var(--good-ink)');
  } catch (e) {
    if (e.detail && e.detail.code === 'late' && !ackLate) {       // the server's clock says the cut-off has passed: ask once, then send with the late fee
      if (await uiConfirm(_BRN_LATE_MSG(e.detail.cutoff_th) + '\nส่งใบสั่งซื้อนี้ต่อหรือไม่?', { title: 'ส่งใบสั่งซื้อล่าช้า', ok: _BRN_FORM.sendOrder, cancel: 'ไม่ส่ง' })) return brnFormApplyOrd(submit, true);
      return;
    }
    _bmcStatus('brnFormStatus_ord', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 220), 'var(--bad-ink)');
  }
}
async function brnFormApplyRcv() {
  const st = _brnFormSt;
  const o = _brnRecvOrd;
  const ack = /** @type {HTMLInputElement|null} */ (document.getElementById('brnFormAck'));
  if (!st || st.kind !== 'rcv' || !o || !ack || !ack.checked) return;       // the acknowledgement is the person's own tick, never assumed
  const d = st.pre;
  const day = (document.getElementById('brnRecvOrdDate') || { value: '' }).value;
  if (!await uiConfirm(`บันทึกรับสินค้า${_bmcRoundDay(o)} · ${d.lines} รายการ · วันที่ ${fmtDay(day)}`
      + (d.claim_lines ? '\nเคลมส่วนต่างส่งถึงประสานงานขาย' : '') + ((d.over || []).length ? `\nรับเกินที่ส่ง ${d.over.length} รายการ` : ''),
      { title: 'ยืนยันการบันทึกรับสินค้า', ok: _BRN_FORM.saveRecv, cancel: 'กลับไปแก้' })) return;
  const note = String((document.getElementById('brnFormClaimNote') || { value: '' }).value || '').trim();
  try {
    const r = await _bmcJson(_brnFormUrl('rcv', false, { ack: true }), { method: 'POST', body: _brnFormBody(st.file, { claim_note: note }) });
    brnFormClose();
    _brnRecvOrd = null;
    document.getElementById('brnRecvOrder').style.display = 'none';
    _bmcStatus('brnRecvOrdDone', r.claim_id ? `✓ บันทึกรับสินค้าแล้ว · แจ้งเคลม ${r.claim_lines} รายการให้ประสานงานขายแล้ว` : `✓ บันทึกรับสินค้าแล้ว ${d.lines} รายการ`, 'var(--good-ink)');
    if (!r.claim_id) brnClaimHint(o.order_id);
    await brnLoadRecv();
  } catch (e) { _bmcStatus('brnFormStatus_rcv', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 220), 'var(--bad-ink)'); }
}
