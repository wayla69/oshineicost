// 13-bom-center.js — BoM Center (docs/BOM_CENTER_PLAN.md, Sprint S1):
// ทะเบียนวัตถุดิบ · ราคาซื้อจริงรายเดือน · แปลงหน่วย. Loaded after 12; shares the
// global scope of slices 01-12. Every name here starts with `bmc` — the BoM Cost
// tab (09e) already owns the `bc` prefix in this shared scope.
// Every import is shown first (dry_run=true) and only written after a confirm.

let _bmcSub = 'overview';
let _bmcIng = [];            // rows from /api/bom-center/ingredients
let _bmcIngEdits = {};       // code -> {field: value}
let _bmcPrices = [];         // rows from /api/bom-center/prices
let _bmcSummary = null;
// The วัตถุดิบและใบสั่งซื้อ bar (01-core.js) reads, sets and shows this tab's panes through here.
_SUPPLY_PANE.bomcenter = {
  get: () => _bmcSub, set: k => { _bmcSub = k; }, show: k => bmcSetSub(k),
  month: m => { const el = document.getElementById('bmcMonth'); if (el && el.value !== m) { el.value = m; _bmcIng = []; _bmcPrices = []; } },
};

const _BMC_KIND = { raw: 'ดิบ', prepared: 'กึ่งสำเร็จ', consumable: 'วัสดุสิ้นเปลือง', zero_by_design: '฿0 โดยเจตนา' };
const _BMC_SRC = { purchase_avg: 'ซื้อจริง', carry_forward: 'ยกมา', manual: 'กำหนดเอง', list_ref: 'ใบเสนอราคา', snapshot: 'สูตรเดิม' };
const _BMC_SRC_INK = { purchase_avg: 'var(--good-ink)', carry_forward: 'var(--amber-ink)', manual: 'var(--navy-ink)',
                       list_ref: 'var(--text-muted)', snapshot: 'var(--text-muted)' };

function _bmcMonth() {
  const el = document.getElementById('bmcMonth');
  if (el && !el.value) {   // the module's shared month first (supplyCtx), else this calendar month
    const d = new Date();
    el.value = supplyCtx().month || `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
  }
  return el ? el.value : '';
}
function _bmcAddUnknown() { const el = document.getElementById('bmcAddUnknown'); return !!(el && el.checked); }
function _bmcCanWrite() { return hasModule('bom.center') || hasModule('bom.edit'); }
function _bmcNum(v, d = 2) {
  if (v === null || v === undefined || v === '' || isNaN(v)) return '—';
  return Number(v).toLocaleString('th-TH', { minimumFractionDigits: 0, maximumFractionDigits: d });
}
function _bmcPrice(v) {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  return n.toLocaleString('th-TH', { maximumFractionDigits: n < 1 ? 4 : 2 });
}
async function _bmcJson(url, opt) {
  const r = await fetch(url, opt);
  const t = await r.text();
  let j = null;
  try { j = JSON.parse(t); } catch (e) { /* not json */ }
  if (!r.ok) throw new Error((j && j.detail) || t.slice(0, 200));
  return j;
}
function _bmcStatus(id, msg, ink) {
  const el = document.getElementById(id);
  if (el) { el.textContent = msg; el.style.color = ink || 'var(--text-muted)'; }
  // a save that succeeded (every one reports "✓ …"): หน้างาน says what comes next (16-supplyhome.js)
  if (msg && String(msg).startsWith('✓') && typeof shmAfterStatus === 'function') shmAfterStatus(id);
}
function _bmcChip(text, ink) {
  return `<span style="display:inline-block;padding:1px 6px;border-radius:999px;border:1px solid var(--border);color:${ink};font-size:10.5px;white-space:nowrap">${esc(text)}</span>`;
}

function bmcSetSub(k) {
  _bmcSub = k;
  ['overview', 'rec', 'prep', 'rel', 'fc', 'cal', 'ing', 'price', 'conv'].forEach(p => {
    const el = document.getElementById('bmc-pane-' + p);
    if (el) el.style.display = p === k ? '' : 'none';
  });
  if (k === 'overview') bmcLoadOverview();
  if (k === 'rec') bmcLoadReleases();
  if (k === 'rel') bmcLoadReleasePane();
  if (k === 'fc') bmcLoadForecastPane();
  if (k === 'cal') bmcLoadCalendar();
  if (k === 'prep') bmcLoadPrep();
  if (k === 'ing') bmcLoadIng();
  if (k === 'price') bmcLoadPrices();
  if (k === 'conv') bmcLoadConv();
  try { _updateClusterBar(activeTab); } catch (e) { /* no bar on this page */ }
}
// The month strip sits above every pane: a change reloads the pane that is open.
function bmcMonthChanged() { _bmcIng = []; _bmcPrices = []; supplyCtxSet({ month: _bmcMonth() }); bmcSetSub(_bmcSub); }

async function loadBomCenter() {
  _bmcMonth();
  document.querySelectorAll('#tab-bomcenter .bc-write').forEach(el => { el.style.display = _bmcCanWrite() ? '' : 'none'; });
  bmcSetSub(_bmcSub);
}

// ── overview ────────────────────────────────────────────────────────────────
async function bmcLoadOverview() {
  const m = _bmcMonth();
  try {
    const s = _bmcSummary = await _bmcJson(`${API}/api/bom-center/summary?month=${m}`);
    const tile = (label, val, sub, ink) => `<div class="card p-3"><div class="text-[11px]" style="color:var(--text-muted)">${label}</div>
      <div class="text-xl font-bold" style="color:${ink || 'var(--text-primary)'}">${val}</div><div class="text-[11px]" style="color:var(--text-muted)">${sub || ''}</div></div>`;
    const k = s.kinds || {};
    document.getElementById('bmcKpis').innerHTML = [
      tile('วัตถุดิบในทะเบียน', _bmcNum(s.ingredients, 0), `ดิบ ${k.raw || 0} · กึ่งสำเร็จ ${k.prepared || 0} · สิ้นเปลือง ${k.consumable || 0}`),
      tile('มีราคาใช้ได้เดือนนี้', _bmcNum(s.priced, 0), `ไม่มีราคา ${s.unpriced}`, s.unpriced ? 'var(--amber-ink)' : 'var(--good-ink)'),
      tile('ราคาซื้อจริงเดือนนี้', _bmcNum((s.price_sources || {}).purchase_avg || 0, 0), `ยกมา ${(s.price_sources || {}).carry_forward || 0} · ใบเสนอราคา ${(s.price_sources || {}).list_ref || 0}`),
      tile('ราคาเก่ากว่า 2 เดือน', _bmcNum(s.stale, 0), 'ควรมีการซื้อใหม่หรือยืนยันราคา', s.stale ? 'var(--amber-ink)' : 'var(--good-ink)'),
      tile('บังคับสั่ง (คลัง)', _bmcNum(s.monitored, 0), 'ติดตามในแผนสั่ง'),
    ].join('');
    document.getElementById('bmcOverviewNote').textContent = s.last_import
      ? `นำเข้าราคาล่าสุด: เดือน ${s.last_import.month} โดย ${s.last_import.who} (${String(s.last_import.at).slice(0, 16).replace('T', ' ')})`
      : 'ยังไม่เคยนำเข้าราคาซื้อจริง';
    const setup = document.getElementById('bmcSetupCard');
    if (!s.ingredients) {
      setup.innerHTML = `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">เริ่มต้นใช้งาน</div>
        <p class="text-xs mb-2" style="color:var(--text-secondary)">ทะเบียนยังไม่มีข้อมูล กรุณากด “เติมจากข้อมูลในระบบ” เพื่อสร้างทะเบียนจากสูตร ขนาดบรรจุ และรายการบังคับสั่งของคลัง
        จากนั้นนำเข้า sheet DATAP (ไฟล์ COST-RW) เพื่อระบุสูญเสีย % และผู้จำหน่าย แล้วนำเข้าราคาซื้อจริงของเดือนล่าสุด</p>
        ${_bmcCanWrite() ? '<button onclick="bmcSeed()" style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:6px 14px;font-size:13px;cursor:pointer">เติมจากข้อมูลในระบบ</button>' : ''}`;
    } else {
      // each open item links to the list where it is fixed
      const steps = [
        [s.recipe_codes_missing === 0, `รหัสวัตถุดิบที่สูตรใช้อยู่ในทะเบียนครบ`, `สูตรใช้รหัสที่ยังไม่อยู่ในทะเบียน ${s.recipe_codes_missing} รหัส`, 'bmcGoIssues()', 'ตรวจรายการและรหัสที่แนะนำ'],
        [s.no_base_unit === 0, 'ทุกวัตถุดิบมีหน่วยฐาน', `${s.no_base_unit} รายการยังไม่มีหน่วยฐาน`, "bmcGoIng('ไม่มีหน่วยฐาน')", 'เปิดรายการ'],
        [s.raw_without_pack === 0, 'วัตถุดิบดิบมีขนาดบรรจุครบ', `${s.raw_without_pack} รายการดิบยังไม่มีขนาดบรรจุ (จึงแปลงหน่วยแพ็คหรือถุงไม่ได้)`, "bmcGoIng('ไม่มีขนาดบรรจุ')", 'เปิดรายการ'],
        [(s.price_sources || {}).purchase_avg > 0, `มีราคาซื้อจริงของเดือน ${s.month}`, `ยังไม่มีราคาซื้อจริงของเดือน ${s.month}`, "bmcSetSub('price')", 'นำเข้าที่ ราคารายเดือน'],
      ];
      const go = (on, label) => ` <a href="#" onclick="${on};return false" style="color:var(--accent);white-space:nowrap">${label} →</a>`;
      setup.innerHTML = `<div class="text-sm font-semibold mb-2" style="color:var(--text-primary)">ความพร้อมของข้อมูล</div>` +
        steps.map(([ok, good, bad, on, label]) => `<div class="text-xs py-0.5" style="color:${ok ? 'var(--good-ink)' : 'var(--amber-ink)'}">${ok ? '✓' : '•'} ${esc(ok ? good : bad)}${ok ? '' : go(on, label)}</div>`).join('') +
        '<div id="bmcSetupCr"></div>';
    }
    bmcLoadCodeReview(s);
  } catch (e) {
    document.getElementById('bmcKpis').innerHTML = `<div class="card p-3 text-xs" style="color:var(--bad-ink)">โหลดข้อมูลไม่สำเร็จ: ${esc(String(e.message || e))}</div>`;
  }
}

// ── code check: old-recipe codes vs the master (codes.py) ───────────────────
let _bmcCr = null;            // /api/bom-center/code-review
let _bmcCrView = 'todo';      // todo | done | all
let _bmcCrGroups = [];       // [{code, names}] behind the buttons of the rendered rows
const _BMC_CR = {
  missing: ['ไม่อยู่ในทะเบียน', 'var(--amber-ink)'], conflict: ['ชื่อไม่ตรง', 'var(--bad-ink)'],
  same: ['วัตถุดิบเดียวกัน', 'var(--good-ink)'], remap: ['ใช้รหัสอื่นแทน', 'var(--navy-ink)'], ok: ['ชื่อตรง', 'var(--text-muted)'],
};
async function bmcLoadCodeReview(s) {
  const iss = document.getElementById('bmcIssuesCard');
  if (!iss) return;
  iss.style.display = '';
  if (s && !s.ingredients) {
    // an empty master makes every recipe code "missing" — say what fixes it instead of listing them
    iss.innerHTML = s.recipe_codes_missing
      ? `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">รหัสที่สูตรเดิมใช้แต่ยังไม่อยู่ในทะเบียน: ${_bmcNum(s.recipe_codes_missing, 0)} รหัส</div>
         <p class="text-xs" style="color:var(--text-secondary)">ตัวเลขนี้เกิดจากทะเบียนยังไม่มีข้อมูล ไม่จำเป็นต้องเพิ่มทีละรหัส กรุณากด “เติมจากข้อมูลในระบบ” ด้านบนหนึ่งครั้ง
         ระบบจะเพิ่มทั้ง ${_bmcNum(s.recipe_codes_missing, 0)} รหัสพร้อมชื่อ หน่วย และขนาดบรรจุ จากนั้นการ์ดนี้จะแสดงเฉพาะรายการ
         <b>“รหัสเดียวกันแต่ชื่อไม่ตรง”</b> ที่ต้องตรวจสอบ</p>`
      : '';
    iss.style.display = s.recipe_codes_missing ? '' : 'none';
    return;
  }
  iss.innerHTML = '<div class="text-xs" style="color:var(--text-muted)">กำลังตรวจรหัสสูตรเดิม…</div>';
  try {
    _bmcCr = await _bmcJson(`${API}/api/bom-center/code-review?all=false`);
    bmcRenderCodeReview();
  } catch (e) {
    iss.innerHTML = `<div class="text-xs" style="color:var(--bad-ink)">ตรวจรหัสไม่สำเร็จ: ${esc(String(e.message || e))}</div>`;
  }
}
function bmcCrView(v) { _bmcCrView = v; bmcRenderCodeReview(); }
function bmcGoIssues() {
  bmcSetSub('overview');
  _bmcCrView = 'todo';
  setTimeout(() => { const el = document.getElementById('bmcIssuesCard'); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 300);
}
let _bmcIngProb = '';         // the ingredient tab shows only rows with this problem ('' = any)
function bmcGoIng(prob) {
  _bmcIngProb = prob || '';
  bmcSetSub('ing');
  const cb = document.getElementById('bmcIngIssues');
  if (cb) cb.checked = !!prob;
  bmcLoadIng(false);
}
// master items for "รหัสอื่น…": names close to the recipe's names first (the master is not
// cleaned yet, so an exact match is rare — character pairs shared, Dice coefficient)
let _bmcMasterAll = null;
function _bmcPairs(t) {
  const k = String(t || '').toLowerCase().replace(/[\s\d().,/\-]+/g, '');
  const out = new Set();
  for (let i = 0; i < k.length - 1; i++) out.add(k.slice(i, i + 2));
  return out;
}
function _bmcSim(a, b) {
  const x = _bmcPairs(a), y = _bmcPairs(b);
  if (!x.size || !y.size) return 0;
  let n = 0; x.forEach(p => { if (y.has(p)) n++; });
  return 2 * n / (x.size + y.size);
}
async function _bmcCodeChoices(names, exclude) {
  if (!_bmcMasterAll) {
    const d = await _bmcJson(`${API}/api/bom-center/ingredients?month=${_bmcMonth()}`);
    _bmcMasterAll = (d.rows || []).map(r => ({ code: r.code, name: r.name_th || '' }));
  }
  return _bmcMasterAll.filter(m => m.code !== exclude)
    .map(m => ({ m, s: Math.max(0, ...names.map(n => _bmcSim(n, m.name))) }))
    .sort((a, b) => b.s - a.s || a.m.code.localeCompare(b.m.code))
    .map(({ m, s }) => ({ value: m.code, label: `${m.name}${s >= 0.3 ? ` · ชื่อใกล้เคียง ${Math.round(s * 100)}%` : ''}` }));
}
function _bmcPickedCode(v, choices) {
  if (!v) return '';
  const t = v.trim();
  if (choices.some(c => c.value === t)) return t;
  const byName = choices.find(c => c.label.split(' · ')[0].trim() === t);   // typed a name exactly
  return byName ? byName.value : t;
}
function _bmcQ(v) { return JSON.stringify(v).replace(/"/g, '&quot;'); }
function bmcRenderCodeReview() {
  const iss = document.getElementById('bmcIssuesCard');
  const d = _bmcCr;
  if (!iss || !d) return;
  const c = d.counts || {};
  const todo = (c.conflict || 0) + (c.missing || 0);
  const setupCr = document.getElementById('bmcSetupCr');
  if (setupCr) setupCr.innerHTML = todo
    ? `<div class="text-xs py-0.5" style="color:var(--amber-ink)">• รหัสในสูตรเดิมที่ชื่อไม่ตรงกับทะเบียน ${_bmcNum(c.conflict || 0, 0)} รหัส${c.missing ? ` · ไม่อยู่ในทะเบียน ${_bmcNum(c.missing, 0)} รหัส` : ''} <a href="#" onclick="bmcGoIssues();return false" style="color:var(--accent);white-space:nowrap">ตรวจและจับคู่รหัส →</a></div>`
    : `<div class="text-xs py-0.5" style="color:var(--good-ink)">✓ รหัสในสูตรเดิมตรงกับทะเบียนหรือตรวจแล้วทั้งหมด</div>`;
  const done = (d.rows || []).filter(r => (r.names || []).some(n => n.status === 'same' || n.status === 'remap')).length;
  const hasDone = r => (r.names || []).some(n => n.status === 'same' || n.status === 'remap');
  const rows = (d.rows || []).filter(r => _bmcCrView === 'all' || (_bmcCrView === 'todo'
    ? (r.groups || []).length > 0 : hasDone(r)));
  const w = _bmcCanWrite();
  const btn = (label, on, active) => `<button onclick="${on}" style="border:1px solid var(--border);border-radius:var(--r-sm);padding:2px 10px;font-size:12px;cursor:pointer;background:${active ? 'var(--accent)' : 'transparent'};color:${active ? 'var(--on-accent)' : 'var(--text-primary)'}">${label}</button>`;
  const small = (label, on, ink) => `<button onclick="${on}" style="border:1px solid var(--border);border-radius:var(--r-sm);padding:1px 8px;font-size:11px;cursor:pointer;background:transparent;color:${ink || 'var(--text-primary)'};white-space:nowrap">${label}</button>`;
  // each wrong-name group gets its own decision; names travel through _bmcCrGroups, not the onclick text
  _bmcCrGroups = [];
  const gid = (code, names) => { _bmcCrGroups.push({ code, names }); return _bmcCrGroups.length - 1; };
  const dishes = (r, nm) => ((r.names || []).find(x => x.name === nm) || { dishes: 0 }).dishes;
  const nameList = (r, names, ink) => names.map(nm =>
    `<span style="color:${ink}">${esc(nm || '(ไม่มีชื่อ)')}</span> <span style="color:var(--text-muted);white-space:nowrap">(${_bmcNum(dishes(r, nm), 0)} เมนู)</span>`).join(' · ');
  const okLine = r => {
    const okN = (r.names || []).filter(n => n.status === 'ok');
    return okN.length ? `<div class="text-[10.5px] mt-0.5" style="color:var(--text-muted)">ชื่อที่ตรงกับทะเบียนแล้ว (ไม่เปลี่ยน): ${okN.slice(0, 3).map(n => esc(n.name)).join(', ')}${okN.length > 3 ? ` +${okN.length - 3}` : ''} (${_bmcNum(okN.reduce((a, n) => a + n.dishes, 0), 0)} เมนู)</div>` : '';
  };
  const head = (r, st) => `<div style="display:flex;gap:8px;align-items:baseline;flex-wrap:wrap">
      <b style="color:var(--text-primary)">${esc(r.code)}</b>${_bmcChip(_BMC_CR[st][0], _BMC_CR[st][1])}
      <span class="text-[11px]" style="color:var(--text-secondary)">ในทะเบียน: ${esc(r.master_name || 'ไม่มีในทะเบียน')}${r.unit ? ` · หน่วย ${esc(r.unit)}` : ''}</span></div>`;
  const block = inner => `<div style="border-top:1px solid var(--border);padding:8px 0">${inner}</div>`;
  const groupRows = r => (r.groups || []).map((g, i) => {
    const id = gid(r.code, g.names);
    const sg = (g.suggest || [])[0];
    const acts = !w ? '' : `<div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:4px">`
      + (r.status === 'missing' ? small('เติมทะเบียน', 'bmcSeed()') : small('วัตถุดิบเดียวกัน', `bmcCrDecideG(${id}, 'same')`, 'var(--good-ink)'))
      + (sg ? small('ใช้ ' + esc(sg.code), `bmcCrDecideG(${id}, 'remap', ${_bmcQ(sg.code)})`, 'var(--navy-ink)') : '')
      + small('รหัสอื่น…', `bmcCrDecideG(${id}, 'remap', null)`) + '</div>';
    return block((i === 0 ? head(r, r.status === 'missing' ? 'missing' : 'conflict') : `<div class="text-[11px]" style="color:var(--text-muted)">${esc(r.code)} · กลุ่มชื่ออื่น</div>`)
      + `<div class="text-xs mt-1">ชื่อในสูตรเดิม: ${nameList(r, g.names, 'var(--bad-ink)')}</div>`
      + `<div class="text-[11px] mt-0.5" style="color:var(--text-secondary)">รหัสที่แนะนำ: ${(g.suggest || []).slice(0, 2).map(x => `<b>${esc(x.code)}</b> ${esc(x.name || '')}`).join(' · ') || 'ไม่พบ กรุณาเลือกด้วยปุ่ม “รหัสอื่น…”'}</div>`
      + (i === 0 ? okLine(r) : '') + acts);
  }).join('');
  const doneRow = r => {
    const dn = (r.names || []).filter(n => n.status === 'same' || n.status === 'remap');
    if (!dn.length) return '';
    const id = gid(r.code, dn.map(n => n.name));
    const st = dn.some(n => n.status === 'remap') ? 'remap' : 'same';
    return block(head(r, st) + dn.map(n => `<div class="text-xs mt-1"><span style="color:${n.status === 'remap' ? 'var(--navy-ink)' : 'var(--good-ink)'}">${esc(n.name)}</span>
        <span style="color:var(--text-muted)">(${_bmcNum(n.dishes, 0)} เมนู)</span>
        ${n.status === 'remap' ? `→ <b>${esc(n.use_code || '')}</b> ${esc(n.use_name || '')}` : '· วัตถุดิบเดียวกัน'}
        <span class="text-[10.5px]" style="color:var(--text-muted)">${esc(n.who || '')}${n.note ? ' · ' + esc(n.note) : ''}</span></div>`).join('')
      + (w ? `<div style="margin-top:4px">${small('ยกเลิกการตัดสินใจ', `bmcCrDecideG(${id}, 'clear')`, 'var(--text-muted)')}</div>` : ''));
  };
  const body = rows.map(r => (_bmcCrView === 'done' ? '' : groupRows(r)) + (_bmcCrView === 'todo' ? '' : doneRow(r))).join('');
  iss.innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:baseline">
      <div class="text-sm font-semibold" style="color:var(--text-primary)">ตรวจรหัสสูตรเดิม: รหัสเดียวกันแต่ชื่อไม่ตรง</div>
      <a href="${API}/api/bom-center/code-review.xlsx?all=true" class="text-xs" style="color:var(--accent)">ดาวน์โหลด Excel (ทุกรหัส)</a></div>
    <p class="text-xs mt-1 mb-2" style="color:var(--text-secondary)">สูตรเดิมใช้ ${_bmcNum(d.used, 0)} รหัส · ชื่อตรงกับทะเบียน ${_bmcNum(c.ok, 0)} ·
      <b style="color:var(--bad-ink)">ชื่อไม่ตรง ${_bmcNum(c.conflict, 0)}</b> · ไม่อยู่ในทะเบียน ${_bmcNum(c.missing, 0)} · ตรวจแล้ว ${_bmcNum(done, 0)} ·
      “ชื่อไม่ตรง” หมายถึงบางบรรทัดในสูตรเดิมใช้รหัสนี้กับวัตถุดิบคนละชนิดกับบัญชีสินค้า (เช่น สูตรระบุหัวไชเท้า แต่บัญชีสินค้าระบุมะนาว) แผนสั่งจะสั่งวัตถุดิบผิดจนกว่าจะตรวจสอบ ·
      กด <b>วัตถุดิบเดียวกัน</b> หากเป็นเพียงชื่อที่เขียนต่างกัน หรือกด <b>ใช้รหัสแนะนำ</b> หากเป็นวัตถุดิบคนละชนิด · การตัดสินใจมีผลเฉพาะ<b>ชื่อสีแดง</b> บรรทัดที่ชื่อตรงอยู่แล้วไม่เปลี่ยน ·
      มีผลกับแผนสั่งเท่านั้น ไม่เปลี่ยนต้นทุนย้อนหลัง</p>
    <div style="display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap">${btn(`ต้องตรวจ (${todo})`, "bmcCrView('todo')", _bmcCrView === 'todo')}${btn(`ตรวจแล้ว (${done})`, "bmcCrView('done')", _bmcCrView === 'done')}${btn('ทุกรายการที่มีปัญหา', "bmcCrView('all')", _bmcCrView === 'all')}</div>
    <div id="bmcCrStatus" class="text-xs mb-1"></div>
    ${rows.length ? `<div>${body}</div>`
      : `<div class="text-xs" style="color:var(--good-ink)">${_bmcCrView === 'todo' ? '✓ ไม่มีรหัสที่ต้องตรวจ' : 'ยังไม่มีรายการ'}</div>`}`;
}
function bmcCrDecideG(i, status, useCode) {
  const g = _bmcCrGroups[i];
  return g ? bmcCrDecide(g.code, status, useCode, g.names) : null;
}
async function bmcCrDecide(code, status, useCode, names) {
  let note = null;
  if (status === 'remap' && !useCode) {          // "รหัสอื่น…": pick a master code, closest names first
    const choices = await _bmcCodeChoices(names || [], code);
    useCode = _bmcPickedCode(await uiPrompt(`กรุณาระบุรหัสในทะเบียนที่ใช้แทนรหัส ${code} ของสูตรเดิม\nพิมพ์รหัสหรือชื่อ ระบบจะแสดงรายการในทะเบียนที่ชื่อใกล้เคียงที่สุดก่อน`, '',
      { title: 'เลือกรหัสที่ใช้แทน', list: choices, placeholder: 'เช่น V-007 หรือ ต้นหอม', required: true }), choices);
    if (!useCode) return;
    note = await uiPrompt(`ใช้ ${useCode} แทน ${code}\nหมายเหตุ (ไม่บังคับ)`, '') || null;
  }
  try {
    await _bmcJson(`${API}/api/bom-center/code-review`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, status, use_code: useCode || null, note, names: names || null }) });
    _bmcCr = await _bmcJson(`${API}/api/bom-center/code-review?all=false`);
    bmcRenderCodeReview();
    _bmcStatus('bmcCrStatus', `✓ บันทึก ${code} แล้ว`, 'var(--good-ink)');
  } catch (e) {
    _bmcStatus('bmcCrStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 160), 'var(--bad-ink)');
  }
}

// ── ingredient master ───────────────────────────────────────────────────────
async function bmcLoadIng(force) {
  if (_bmcIng.length && !force) { bmcRenderIng(); return; }
  document.getElementById('bmcIngBody').innerHTML = '<tr><td colspan="12" class="py-3" style="color:var(--text-muted)">กำลังโหลด…</td></tr>';
  try {
    const d = await _bmcJson(`${API}/api/bom-center/ingredients?month=${_bmcMonth()}`);
    _bmcIng = d.rows || [];
    _bmcIngEdits = {};
    bmcRenderIng();
  } catch (e) {
    document.getElementById('bmcIngBody').innerHTML = `<tr><td colspan="12" class="py-3" style="color:var(--bad-ink)">${esc(String(e.message || e))}</td></tr>`;
  }
}
function _bmcIngVal(r, f) { const e = _bmcIngEdits[r.code]; return e && f in e ? e[f] : r[f]; }
function bmcIngEdit(el) {
  const code = el.dataset.code, f = el.dataset.f;
  let v = el.type === 'checkbox' ? (el.checked ? 1 : 0) : el.value;
  if (['qty_per_recv', 'loss_pct'].includes(f)) v = v === '' ? null : Number(v);
  (_bmcIngEdits[code] = _bmcIngEdits[code] || {})[f] = v;
  el.style.background = 'var(--accent-soft)';
  _bmcStatus('bmcIngSummary', `แก้ไขแล้ว ${Object.keys(_bmcIngEdits).length} รายการ (ยังไม่บันทึก)`, 'var(--amber-ink)');
}
function bmcRenderIng() {
  const q = (document.getElementById('bmcIngQ').value || '').toLowerCase();
  const kind = document.getElementById('bmcIngKind').value;
  const only = document.getElementById('bmcIngIssues').checked;
  const bar = document.getElementById('bmcIngProbBar');
  if (bar) bar.innerHTML = _bmcIngProb && only
    ? `${_bmcChip('แสดงเฉพาะ: ' + _bmcIngProb, 'var(--amber-ink)')} <a href="#" onclick="_bmcIngProb='';bmcRenderIng();return false" style="color:var(--accent)">แสดงทุกปัญหา</a>` : '';
  const rows = _bmcIng.filter(r => (!kind || r.kind === kind) && (!only || r.problems.length)
    && (!only || !_bmcIngProb || r.problems.includes(_bmcIngProb))
    && (!q || `${r.code} ${r.name_th || ''}`.toLowerCase().includes(q)));
  const w = _bmcCanWrite();
  const inp = (r, f, width, type) => w
    ? `<input data-code="${esc(r.code)}" data-f="${f}" ${type ? `type="${type}" step="any"` : ''} value="${esc(_bmcIngVal(r, f) ?? '')}" onchange="bmcIngEdit(this)"
         style="width:${width}px;border:1px solid var(--border);border-radius:4px;padding:2px 4px;font-size:11.5px;background:var(--bg-input);color:var(--text-primary);${type ? 'text-align:right' : ''}">`
    : esc(_bmcIngVal(r, f) ?? '—');
  const kindSel = r => w
    ? `<select data-code="${esc(r.code)}" data-f="kind" onchange="bmcIngEdit(this)" style="border:1px solid var(--border);border-radius:4px;font-size:11.5px;background:var(--bg-input);color:var(--text-primary)">
        ${Object.entries(_BMC_KIND).map(([k, t]) => `<option value="${k}" ${_bmcIngVal(r, 'kind') === k ? 'selected' : ''}>${t}</option>`).join('')}</select>`
    : (_BMC_KIND[r.kind] || r.kind);
  const shown = rows.slice(0, 600);
  document.getElementById('bmcIngBody').innerHTML = shown.map(r => `<tr class="border-b" style="border-color:var(--border-soft)">
      <td class="py-1 pr-2 whitespace-nowrap">${esc(r.code)}${r.aliases.length ? `<div class="text-[10px]" style="color:var(--text-muted)">รหัสคลัง: ${r.aliases.map(a => esc(a.alias)).join(', ')}</div>` : ''}</td>
      <td class="py-1 pr-2">${inp(r, 'name_th', 210)}</td>
      <td class="py-1 pr-2">${kindSel(r)}</td>
      <td class="py-1 pr-2">${inp(r, 'base_unit', 70)}</td>
      <td class="py-1 pr-2">${inp(r, 'recv_unit', 90)}</td>
      <td class="py-1 pr-2 text-right">${inp(r, 'qty_per_recv', 80, 'number')}</td>
      <td class="py-1 pr-2 text-right">${inp(r, 'loss_pct', 55, 'number')}</td>
      <td class="py-1 pr-2 text-right">${_bmcPrice(r.price_per_base)}${r.price_source ? `<div>${_bmcChip(_BMC_SRC[r.price_source] || r.price_source, _BMC_SRC_INK[r.price_source] || 'var(--text-muted)')}</div>` : ''}</td>
      <td class="py-1 pr-2 text-right">${_bmcPrice(r.price_per_recv)}</td>
      <td class="py-1 pr-2 text-right">${r.used_in_menus || ''}</td>
      <td class="py-1 pr-2 text-center">${w ? `<input type="checkbox" data-code="${esc(r.code)}" data-f="monitored" ${_bmcIngVal(r, 'monitored') ? 'checked' : ''} onchange="bmcIngEdit(this)">` : (r.monitored ? '✓' : '')}</td>
      <td class="py-1">${r.problems.length ? r.problems.map(p => _bmcChip(p, 'var(--amber-ink)')).join(' ') : _bmcChip('พร้อม', 'var(--good-ink)')}</td>
    </tr>`).join('') || '<tr><td colspan="12" class="py-3" style="color:var(--text-muted)">ไม่มีรายการ หากทะเบียนยังไม่มีข้อมูล กรุณากด “เติมจากข้อมูลในระบบ”</td></tr>';
  const nProb = _bmcIng.filter(r => r.problems.length).length;
  if (!Object.keys(_bmcIngEdits).length) {
    _bmcStatus('bmcIngSummary', `${rows.length} จาก ${_bmcIng.length} รายการ · มีปัญหา ${nProb}` + (rows.length > shown.length ? ` · แสดง ${shown.length} รายการแรก (ใช้ช่องค้นหาเพื่อกรอง)` : ''));
  }
}
async function bmcSaveIng() {
  const rows = Object.entries(_bmcIngEdits).map(([code, e]) => ({ code, ...e }));
  if (!rows.length) { _bmcStatus('bmcIngSummary', 'ยังไม่มีรายการที่แก้ไข'); return; }
  try {
    const d = await _bmcJson(`${API}/api/bom-center/ingredients`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(rows) });
    _bmcStatus('bmcIngSummary', `✓ บันทึก ${d.updated + d.inserted} รายการแล้ว`, 'var(--good-ink)');
    bmcLoadIng(true);
  } catch (e) {
    _bmcStatus('bmcIngSummary', 'บันทึกไม่สำเร็จ:' + String(e.message || e).slice(0, 160), 'var(--bad-ink)');
  }
}
async function bmcSeed() {
  try {
    const pre = await _bmcJson(`${API}/api/bom-center/ingredients/seed?dry_run=true`, { method: 'POST' });
    const msg = `เติมทะเบียนจากข้อมูลในระบบ\n\nรหัสใหม่ ${pre.inserted} · เติมช่องที่ว่าง ${pre.updated} · ไม่เปลี่ยน ${pre.unchanged}\n`
      + `ราคาใบเสนอราคาอ้างอิง ${pre.ref_prices} · รหัสคลังที่จับคู่กับรหัสสูตร ${pre.aliases}\n`
      + (pre.multi_unit_codes.length ? `รหัสที่สูตรใช้หลายหน่วย ${pre.multi_unit_codes.length} (ใช้หน่วยที่ใช้มากที่สุด กรุณาตรวจสอบที่ หน่วยและการแปลง)\n` : '')
      + `\nไม่เขียนทับค่าที่ตั้งไว้แล้ว · ยืนยันการบันทึก?`;
    if (!await uiConfirm(msg)) return;
    const d = await _bmcJson(`${API}/api/bom-center/ingredients/seed?dry_run=false`, { method: 'POST' });
    _bmcStatus('bmcIngStatus', `✓ ทะเบียน ${d.before} → ${d.after} รายการ`, 'var(--good-ink)');
    _bmcIng = [];
    bmcSetSub(_bmcSub === 'overview' ? 'overview' : 'ing');
  } catch (e) {
    _bmcStatus('bmcIngStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 160), 'var(--bad-ink)');
  }
}
async function bmcImportIng(input) {
  const file = input?.files?.[0];
  if (!file) return;
  const send = dry => { const fd = new FormData(); fd.append('file', file, file.name);
    return _bmcJson(`${API}/api/bom-center/ingredients/import?dry_run=${dry}`, { method: 'POST', body: fd }); };
  try {
    _bmcStatus('bmcIngStatus', 'กำลังอ่านไฟล์… (ไฟล์ COST-RW มีขนาดใหญ่ อาจใช้เวลาสักครู่)');
    const pre = await send(true);
    document.getElementById('bmcIngReport').innerHTML = `<div class="card p-3 text-xs" style="color:var(--text-secondary)">
      sheet <b>${esc(pre.sheet)}</b> · อ่าน ${pre.rows} แถว · ใหม่ ${pre.inserted} · แก้ไข ${pre.updated} · ไม่เปลี่ยน ${pre.unchanged} · ราคาใบเสนอราคา ${pre.ref_prices}
      ${pre.skipped.length ? `<div class="mt-1" style="color:var(--amber-ink)">ข้าม ${pre.skipped.length} แถว: ${pre.skipped.slice(0, 8).map(s => `แถว ${s.row}${s.code ? ' ' + esc(s.code) : ''} (${esc(s.why)})`).join(' · ')}</div>` : ''}</div>`;
    if (!(pre.inserted + pre.updated + pre.ref_prices)) { _bmcStatus('bmcIngStatus', 'ไม่มีรายการเปลี่ยนแปลง'); return; }
    if (!await uiConfirm(`ยืนยันการนำเข้า ${file.name}: ใหม่ ${pre.inserted} · แก้ไข ${pre.updated} รายการ\nค่าในไฟล์จะเขียนทับค่าเดิม`)) { _bmcStatus('bmcIngStatus', 'ยกเลิกแล้ว'); return; }
    const d = await send(false);
    _bmcStatus('bmcIngStatus', `✓ นำเข้าแล้ว ใหม่ ${d.inserted} · แก้ไข ${d.updated}`, 'var(--good-ink)');
    bmcLoadIng(true);
  } catch (e) {
    _bmcStatus('bmcIngStatus', 'นำเข้าไม่สำเร็จ:' + String(e.message || e).slice(0, 160), 'var(--bad-ink)');
  } finally { input.value = ''; }
}

// ── monthly prices ──────────────────────────────────────────────────────────
async function bmcLoadPrices(force) {
  document.getElementById('bmcPriceMonthLbl').textContent = _bmcMonth();
  if (_bmcPrices.length && !force) { bmcRenderPrices(); return; }
  try {
    const d = await _bmcJson(`${API}/api/bom-center/prices?month=${_bmcMonth()}`);
    _bmcPrices = (d.rows || []).filter(r => r.active);
    bmcRenderPrices();
    // the requisition reports already in the system: pick a month here, no detour to change the price month first
    const rm = await _bmcJson(`${API}/api/bom-center/prices/requisition-months`).catch(() => ({ months: [] }));
    const sel = document.getElementById('bmcReqMonth'), btn = document.getElementById('bmcFromReqBtn');
    if (sel && btn) {
      const months = rm.months || [];
      sel.innerHTML = months.map(m => `<option value="${m}">${m}</option>`).join('') || '<option value="">ยังไม่มีรายงานในระบบ</option>';
      sel.value = months.includes(_bmcMonth()) ? _bmcMonth() : (months[0] || '');
      btn.disabled = !months.length;
      btn.style.opacity = months.length ? 1 : 0.5;
    }
  } catch (e) {
    document.getElementById('bmcPriceBody').innerHTML = `<tr><td colspan="9" style="color:var(--bad-ink)">${esc(String(e.message || e))}</td></tr>`;
  }
}
function bmcRenderPrices() {
  const q = (document.getElementById('bmcPriceQ').value || '').toLowerCase();
  const src = document.getElementById('bmcPriceSrc').value;
  const rows = _bmcPrices.filter(r => (!src || (r.source || 'none') === src) && (!q || `${r.code} ${r.name || ''}`.toLowerCase().includes(q)));
  const w = _bmcCanWrite();
  document.getElementById('bmcPriceBody').innerHTML = rows.slice(0, 800).map(r => `<tr class="border-b" style="border-color:var(--border-soft)">
      <td class="py-1 pr-2 whitespace-nowrap">${esc(r.code)}</td><td class="py-1 pr-2">${esc(r.name || '')}</td>
      <td class="py-1 pr-2">${esc(r.base_unit || '')}</td>
      <td class="py-1 pr-2 text-right font-semibold">${_bmcPrice(r.price_per_base)}</td>
      <td class="py-1 pr-2">${r.source ? _bmcChip(_BMC_SRC[r.source] || r.source, _BMC_SRC_INK[r.source] || 'var(--text-muted)') : _bmcChip('ไม่มีราคา', 'var(--bad-ink)')}</td>
      <td class="py-1 pr-2">${esc(r.priced_month || '')}</td>
      <td class="py-1 pr-2 text-right" style="color:${(r.age_months || 0) > 2 ? 'var(--amber-ink)' : 'inherit'}">${r.age_months ?? ''}</td>
      <td class="py-1 pr-2 text-right">${r.branches || ''}</td>
      <td class="py-1 whitespace-nowrap"><button onclick="bmcHistory('${esc(r.code)}')" style="font-size:11px;color:var(--accent);background:none;border:0;cursor:pointer">ประวัติ</button>
        ${w ? `<button onclick="bmcManual('${esc(r.code)}')" style="font-size:11px;color:var(--accent);background:none;border:0;cursor:pointer">แก้ราคา</button>` : ''}</td>
    </tr>`).join('') || '<tr><td colspan="9" class="py-3" style="color:var(--text-muted)">ไม่มีรายการ</td></tr>';
  const by = {};
  _bmcPrices.forEach(r => { const k = r.source || 'none'; by[k] = (by[k] || 0) + 1; });
  _bmcStatus('bmcPriceSummary', `${rows.length} รายการ · ` + Object.entries(by).map(([k, n]) => `${_BMC_SRC[k] || (k === 'none' ? 'ไม่มีราคา' : k)} ${n}`).join(' · '));
}
let _bmcLastSuggested = [];
let _bmcPriceSend = null, _bmcPriceLabel = '', _bmcPricePre = null, _bmcChosenRules = [];
function _bmcSuggestedHtml(list) {
  _bmcLastSuggested = list || [];
  if (!_bmcLastSuggested.length || !_bmcCanWrite()) return '';
  return `<div class="mt-3 p-3" style="background:var(--bg-subtle);border-radius:var(--r-sm)">
    <div class="text-xs font-semibold" style="color:var(--text-primary)">กฎหน่วยที่ระบบเสนอ (${_bmcLastSuggested.length}): ขนาดแพ็คในรายงานบัญชีไม่ตรงกับทะเบียน</div>
    <div class="text-[11px] mb-2" style="color:var(--text-muted)">คำนวณจากราคาล่าสุดของวัตถุดิบนั้น · ระบบเลือกไว้เฉพาะรายการที่ทุกบรรทัดให้ค่าตรงกัน · แก้ไขตัวเลขได้ · กฎที่เลือกจะบันทึกพร้อมราคาในการยืนยันเดียวกัน ไม่ต้องอัปโหลดไฟล์ซ้ำ</div>
    <div class="overflow-x-auto"><table class="text-[11.5px] w-full" style="font-variant-numeric:tabular-nums"><tbody>
    ${_bmcLastSuggested.map((s, i) => `<tr class="border-b" style="border-color:var(--border-soft)">
      <td class="pr-2"><input type="checkbox" id="bmcSug${i}" ${s.consistent ? 'checked' : ''}></td>
      <td class="pr-2 whitespace-nowrap">${esc(s.code)}</td><td class="pr-2">${esc(s.name || '')}</td>
      <td class="pr-2 whitespace-nowrap">1 ${esc(s.unit)} =</td>
      <td class="pr-2"><input id="bmcSugF${i}" type="number" step="any" value="${s.factor}" style="width:90px;border:1px solid var(--border);border-radius:4px;padding:1px 4px;text-align:right;background:var(--bg-input);color:var(--text-primary)"> ${esc(s.base_unit || '')}</td>
      <td class="pr-2 text-right">${s.lines} บรรทัด · ฿${_bmcNum(s.value, 0)}</td>
      <td>${s.consistent ? '' : _bmcChip('ค่าไม่สม่ำเสมอ ควรตรวจสอบ', 'var(--amber-ink)')}</td></tr>`).join('')}
    </tbody></table></div>
    <button onclick="bmcPriceRepreview()" class="mt-2" style="background:var(--bg-input);color:var(--text-primary);border:1px solid var(--border-control);border-radius:var(--r-sm);padding:5px 14px;font-size:12px;cursor:pointer">คำนวณใหม่ด้วยกฎที่เลือก</button>
  </div>`;
}
// the pack guard's suggestions the person ticked (+ those already applied in a re-preview)
function _bmcSelectedRules() {
  return _bmcLastSuggested.map((s, i) => ({ s, on: document.getElementById('bmcSug' + i)?.checked, f: Number(document.getElementById('bmcSugF' + i)?.value) }))
    .filter(x => x.on && x.f > 0).map(x => ({ code: x.s.code, unit: x.s.unit, factor_to_base: x.f }));
}
function _bmcRulesToSend() {
  const sel = _bmcSelectedRules(), seen = new Set(sel.map(r => r.code + '|' + r.unit));
  return sel.concat(_bmcChosenRules.filter(r => !seen.has(r.code + '|' + r.unit)));
}
async function bmcPriceRepreview() {
  if (!_bmcPriceSend) return;
  _bmcChosenRules = _bmcRulesToSend();
  try {
    _bmcStatus('bmcPriceStatus', 'กำลังคำนวณใหม่…');
    _bmcPricePre = await _bmcPriceSend(true, _bmcChosenRules);
    document.getElementById('bmcPricePreview').innerHTML = _bmcPreviewHtml(_bmcPricePre, false);
    _bmcStatus('bmcPriceStatus', `คำนวณด้วยกฎหน่วย ${_bmcChosenRules.length} รายการแล้ว ตรวจสอบผล แล้วกด “บันทึกราคา”`);
  } catch (e) { _bmcStatus('bmcPriceStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
async function bmcPriceCommit() {
  const pre = _bmcPricePre;
  if (!pre || !_bmcPriceSend) return;
  const rules = _bmcRulesToSend();
  if (!pre.priced && !rules.length) { _bmcStatus('bmcPriceStatus', 'ไม่มีบรรทัดที่ใช้คิดราคาได้', 'var(--bad-ink)'); return; }
  if (!await uiConfirm(`${_bmcPriceLabel}\nเดือน ${pre.month}: บันทึกราคาซื้อจริง ${pre.priced} วัตถุดิบ` +
      (rules.length ? `\nพร้อมกฎหน่วย ${rules.length} รายการ (บรรทัดที่ติดกฎจะคิดราคาได้ในการบันทึกนี้)` : '') +
      (pre.added_codes?.length ? `\nเพิ่มรหัสใหม่ในทะเบียน ${pre.added_codes.length} (กรุณาตรวจสอบหน่วยฐานภายหลัง)` : '') +
      (pre.counts.flag ? `\n⚠ ${pre.counts.flag} รายการราคาเปลี่ยน ≥25% กรุณาตรวจสอบในตารางก่อนยืนยัน` : '') +
      (pre.issues.length ? `\n${pre.issues.length} บรรทัดใช้ไม่ได้และจะถูกข้าม` : '') + `\n\nยืนยันการบันทึก?`)) {
    _bmcStatus('bmcPriceStatus', 'ยังไม่บันทึก กรุณาตรวจสอบผลด้านล่าง'); return;
  }
  try {
    const d = await _bmcPriceSend(false, rules);
    document.getElementById('bmcPricePreview').innerHTML = _bmcPreviewHtml(d, true);
    _bmcStatus('bmcPriceStatus', `✓ บันทึก ${d.applied.written} · ยกมา ${d.applied.carried_forward} · คงราคาที่กำหนดเอง ${d.applied.kept_manual}` + (d.rules_applied ? ` · กฎหน่วย ${d.rules_applied}` : ''), 'var(--good-ink)');
    _bmcPrices = []; _bmcIng = []; _bmcPricePre = null; _bmcChosenRules = [];
    bmcLoadPrices(true);
  } catch (e) { _bmcStatus('bmcPriceStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
function _bmcPreviewHtml(p, applied) {
  const flagInk = { flag: 'var(--bad-ink)', warn: 'var(--amber-ink)', new: 'var(--navy-ink)', ok: 'var(--text-muted)' };
  const flagTh = { flag: 'เปลี่ยน ≥25%', warn: 'เปลี่ยน ≥10%', new: 'ใหม่', ok: 'ปกติ' };
  const top = p.rows.filter(r => r.flag !== 'ok').concat(p.rows.filter(r => r.flag === 'ok')).slice(0, 60);
  return `<div class="card p-4">
    <div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">${applied ? '✓ บันทึกแล้ว' : 'ตรวจสอบก่อนบันทึก'}: ราคาซื้อจริงเดือน ${esc(p.month)}</div>
    <div class="text-xs mb-2" style="color:var(--text-secondary)">${esc(p.source?.format || '')} · ${p.lines} บรรทัด · คิดราคาได้ ${p.priced} วัตถุดิบ (มูลค่า ฿${_bmcNum(p.value_total, 0)}) ·
      เปลี่ยน ≥25% <b style="color:var(--bad-ink)">${p.counts.flag}</b> · ≥10% <b style="color:var(--amber-ink)">${p.counts.warn}</b> · ใหม่ ${p.counts.new} ·
      ยกราคาเดิมมา ${p.carry_forward} · ยังไม่เคยมีราคา ${p.never_priced}
      ${p.added_codes?.length ? ` · <b style="color:var(--navy-ink)">${applied ? 'เพิ่ม' : 'จะเพิ่ม'}รหัสใหม่ในทะเบียน ${p.added_codes.length}</b>` : ''}
      ${p.unknown_codes?.length ? ` · <b style="color:var(--amber-ink)">รหัสที่ไม่อยู่ในทะเบียน ${p.unknown_codes.length} (฿${_bmcNum(p.unknown_value, 0)}) กรุณาเลือก “เพิ่มรหัสที่ยังไม่มีในทะเบียน”</b>` : ''}
      ${p.issues.length ? ` · <b style="color:var(--amber-ink)">ใช้ไม่ได้ ${p.issues.length} บรรทัด (฿${_bmcNum(p.value_unused, 0)})</b>` : ''}</div>
    <div class="overflow-x-auto"><table class="text-xs w-full" style="font-variant-numeric:tabular-nums">
      <thead><tr class="text-left border-b" style="color:var(--text-muted)"><th class="pr-2">รหัส</th><th class="pr-2">วัตถุดิบ</th>
        <th class="pr-2 text-right">ปริมาณ (หน่วยฐาน)</th><th class="pr-2 text-right">มูลค่า</th><th class="pr-2 text-right">ราคาใหม่</th>
        <th class="pr-2 text-right">ราคาก่อน</th><th class="pr-2 text-right">เปลี่ยน</th><th class="pr-2">แปลงหน่วย</th><th></th></tr></thead>
      <tbody>${top.map(r => `<tr class="border-b" style="border-color:var(--border-soft)"><td class="pr-2">${esc(r.code)}</td><td class="pr-2">${esc(r.name || '')}</td>
        <td class="pr-2 text-right">${_bmcNum(r.qty_base)} ${esc(r.base_unit || '')}</td><td class="pr-2 text-right">${_bmcNum(r.value, 0)}</td>
        <td class="pr-2 text-right font-semibold">${_bmcPrice(r.price_per_base)}</td><td class="pr-2 text-right">${_bmcPrice(r.prev_price)}</td>
        <td class="pr-2 text-right" style="color:${flagInk[r.flag]}">${r.change_pct == null ? '' : (r.change_pct > 0 ? '+' : '') + r.change_pct + '%'}</td>
        <td class="pr-2 text-[10.5px]" style="color:var(--text-muted)">${esc(r.converted_by.join(' / '))}</td>
        <td>${_bmcChip(flagTh[r.flag], flagInk[r.flag])}${r.manual_kept ? ' ' + _bmcChip('คงราคาที่กำหนดเอง', 'var(--navy-ink)') : ''}</td></tr>`).join('')}</tbody></table></div>
    ${p.rows.length > top.length ? `<div class="text-[11px] mt-1" style="color:var(--text-muted)">แสดง ${top.length} จาก ${p.rows.length} (เรียงจากรายการที่เปลี่ยนมากที่สุด)</div>` : ''}
    ${p.issues.length ? `<div class="mt-3 text-xs font-semibold" style="color:var(--amber-ink)">บรรทัดที่ใช้ไม่ได้: กรุณาแก้ไขทะเบียนหรือกฎหน่วย แล้วนำเข้าใหม่</div>
      <div class="overflow-x-auto"><table class="text-[11px] w-full"><tbody>${p.issues.slice(0, 80).map(i => `<tr class="border-b" style="border-color:var(--border-soft)">
        <td class="pr-2">${esc(i.code)}</td><td class="pr-2">${esc(i.name || '')}</td><td class="pr-2 text-right">${_bmcNum(i.qty)} ${esc(i.unit || '')}</td>
        <td class="pr-2 text-right">฿${_bmcNum(i.value, 0)}</td><td style="color:var(--amber-ink)">${esc(i.why)}
        </td></tr>`).join('')}</tbody></table></div>` : ''}
    ${_bmcSuggestedHtml(p.suggested_rules)}
    ${applied || !_bmcCanWrite() ? '' : `<div class="flex flex-wrap items-center gap-2 mt-3"><button onclick="bmcPriceCommit()" style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:6px 16px;font-size:13px;font-weight:600;cursor:pointer">บันทึกราคา${(p.suggested_rules || []).length || _bmcChosenRules.length ? 'พร้อมกฎที่เลือก' : ''}</button>
      <span class="text-[11px]" style="color:var(--text-muted)">ยังไม่มีการบันทึกจนกว่าจะยืนยัน</span></div>`}
  </div>`;
}
// One upload: preview → (tick the pack guard's suggestions, recalculate if wanted) → one confirm
// saves the prices AND the rules. Without suggestions the confirm comes straight after the preview.
async function _bmcRunPriceImport(send, label) {
  const box = document.getElementById('bmcPricePreview');
  _bmcPriceSend = send; _bmcPriceLabel = label; _bmcChosenRules = []; _bmcPricePre = null;
  try {
    _bmcStatus('bmcPriceStatus', 'กำลังคำนวณ…');
    const pre = await send(true, []);
    _bmcPricePre = pre;
    box.innerHTML = _bmcPreviewHtml(pre, false);
    if (pre.month !== _bmcMonth()) { const el = document.getElementById('bmcMonth'); if (el) el.value = pre.month; supplyCtxSet({ month: pre.month }); }
    if ((pre.suggested_rules || []).length) {
      _bmcStatus('bmcPriceStatus', 'ระบบเสนอกฎหน่วย เลือกแล้วกด “คำนวณใหม่ด้วยกฎที่เลือก” เพื่อดูผล หรือกด “บันทึกราคาพร้อมกฎที่เลือก” ได้เลย', 'var(--amber-ink)');
      return;
    }
    if (!pre.priced) { _bmcStatus('bmcPriceStatus', 'ไม่มีบรรทัดที่ใช้คิดราคาได้', 'var(--bad-ink)'); return; }
    await bmcPriceCommit();
  } catch (e) {
    _bmcStatus('bmcPriceStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)');
  }
}
async function bmcImportPrices(input) {
  const file = input?.files?.[0];
  if (!file) return;
  const send = (dry, rules) => { const fd = new FormData(); fd.append('file', file, file.name);
    if (rules && rules.length) fd.append('rules', JSON.stringify(rules));
    return _bmcJson(`${API}/api/bom-center/prices/import?dry_run=${dry}&add_unknown=${_bmcAddUnknown()}`, { method: 'POST', body: fd }); };
  await _bmcRunPriceImport(send, `ไฟล์ ${file.name}`);
  input.value = '';
}
async function bmcPricesFromReq() {
  const sel = document.getElementById('bmcReqMonth');
  const m = (sel && sel.value) || _bmcMonth();
  if (!m) { _bmcStatus('bmcPriceStatus', 'ยังไม่มีรายงานจ่ายสินค้าภายในในระบบ', 'var(--amber-ink)'); return; }
  await _bmcRunPriceImport((dry, rules) => _bmcJson(`${API}/api/bom-center/prices/from-requisition?month=${m}&dry_run=${dry}&add_unknown=${_bmcAddUnknown()}`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rules: rules || [] }) }), `รายงานจ่ายสินค้าภายในเดือน ${m} ที่อัปโหลดไว้`);
}
async function bmcCarry() {
  const m = _bmcMonth();
  if (!await uiConfirm(`ยืนยันการใช้ราคาล่าสุดของทุกวัตถุดิบในเดือน ${m}\n(รายการที่มีราคาซื้อจริงหรือราคาที่กำหนดเองแล้วจะไม่เปลี่ยน)`)) return;
  try {
    const d = await _bmcJson(`${API}/api/bom-center/prices/carry-forward?month=${m}`, { method: 'POST' });
    _bmcStatus('bmcPriceStatus', `✓ ยกราคามา ${d.carried_forward} รายการ`, 'var(--good-ink)');
    bmcLoadPrices(true);
  } catch (e) { _bmcStatus('bmcPriceStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcManual(code) {
  const r = _bmcPrices.find(x => x.code === code) || {};
  const v = await uiPrompt(`ราคาต่อ ${r.base_unit || 'หน่วยฐาน'} ของ ${code} ${r.name || ''} เดือน ${_bmcMonth()}\n(เว้นว่างเพื่อยกเลิกราคาที่กำหนดเอง)`, r.source === 'manual' ? r.price_per_base : '');
  if (v === null) return;
  let note = '';
  if (v.trim() !== '') {
    note = await uiPrompt('กรุณาระบุเหตุผล (จำเป็น) เช่น ผู้จำหน่ายแจ้งปรับราคา');
    if (!note) return;
  }
  try {
    await _bmcJson(`${API}/api/bom-center/prices/manual`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, month: _bmcMonth(), price_per_base: v.trim() === '' ? null : Number(v), note }) });
    _bmcStatus('bmcPriceStatus', `✓ บันทึกราคา ${code}`, 'var(--good-ink)');
    bmcLoadPrices(true);
  } catch (e) { _bmcStatus('bmcPriceStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcHistory(code) {
  const box = document.getElementById('bmcHistory');
  box.style.display = '';
  box.innerHTML = 'กำลังโหลด…';
  try {
    const d = await _bmcJson(`${API}/api/bom-center/prices/history?code=${encodeURIComponent(code)}`);
    const r0 = _bmcPrices.find(x => x.code === code) || {};
    box.innerHTML = `<div class="flex items-center mb-2"><div class="text-sm font-semibold" style="color:var(--text-primary)">ประวัติราคา ${esc(code)} ${esc(r0.name || '')} (ต่อ ${esc(r0.base_unit || 'หน่วยฐาน')})</div>
      <button onclick="document.getElementById('bmcHistory').style.display='none'" class="ml-auto" style="font-size:12px;background:none;border:0;color:var(--text-muted);cursor:pointer">ปิด</button></div>
      <table class="text-xs w-full" style="font-variant-numeric:tabular-nums"><thead><tr class="text-left border-b" style="color:var(--text-muted)"><th class="pr-2">เดือน</th>
      <th class="pr-2 text-right">ราคา</th><th class="pr-2">แหล่ง</th><th class="pr-2 text-right">ปริมาณซื้อ</th><th class="pr-2 text-right">มูลค่า</th><th class="pr-2">รายสาขา</th><th>หมายเหตุ</th></tr></thead>
      <tbody>${d.rows.map(h => `<tr class="border-b" style="border-color:var(--border-soft)"><td class="pr-2">${esc(h.month)}</td>
        <td class="pr-2 text-right font-semibold">${_bmcPrice(h.price_per_base)}</td><td class="pr-2">${_bmcChip(_BMC_SRC[h.source] || h.source, _BMC_SRC_INK[h.source] || 'var(--text-muted)')}</td>
        <td class="pr-2 text-right">${_bmcNum(h.qty_bought)}</td><td class="pr-2 text-right">${_bmcNum(h.value_bought, 0)}</td>
        <td class="pr-2 text-[10.5px]">${h.branches.map(b => `${esc(b.branch)} ${_bmcPrice(b.price_per_base)}`).join(' · ')}</td>
        <td class="text-[10.5px]" style="color:var(--text-muted)">${esc(h.note || '')}${h.by ? ' · ' + esc(h.by) : ''}</td></tr>`).join('') || '<tr><td colspan="7">ยังไม่มีราคา</td></tr>'}</tbody></table>`;
    box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (e) { box.innerHTML = `<span style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}

// ── Phase 1.5: sub-recipes of prepared items (prep.py) ──────────────────────
let _bmcPrep = null;          // /api/bom-center/prep
let _bmcPrepView = 'todo';    // todo | all | ok | none
let _bmcPrepEdit = null;      // the sub-recipe open in the editor
const _BMC_PREP = {
  incomplete: ['สูตรไม่ครบ', 'var(--bad-ink)'], check: ['ต่างจากราคาซื้อ > 5%', 'var(--amber-ink)'],
  none: ['ยังไม่มีสูตร', 'var(--text-muted)'], ok: ['ใช้ต้นทุนจากสูตร', 'var(--good-ink)'],
};
const _bmcInput = (attrs, w) => `<input ${attrs} style="border:1px solid var(--border-control);border-radius:var(--r-sm);padding:3px 6px;font-size:12px;width:${w};background:var(--bg-input);color:var(--text-primary)">`;
const _bmcBtn = (label, on, primary) => `<button onclick="${on}" style="border:${primary ? '0' : '1px solid var(--border)'};border-radius:var(--r-sm);padding:${primary ? '6px 14px' : '2px 10px'};font-size:${primary ? 13 : 12}px;cursor:pointer;background:${primary ? 'var(--accent)' : 'transparent'};color:${primary ? 'var(--on-accent)' : 'var(--text-primary)'}">${label}</button>`;

async function bmcLoadPrep() {
  const box = document.getElementById('bmcPrepList');
  box.innerHTML = '<span style="color:var(--text-muted)">กำลังโหลด…</span>';
  try {
    _bmcPrep = await _bmcJson(`${API}/api/bom-center/prep`);
    if (!_bmcIng.length) bmcLoadIng(true).then(() => {
      const dl = document.getElementById('bmcCodeList');
      if (dl && !dl.children.length) dl.innerHTML = _bmcIng.map(r => `<option value="${esc(r.code)}">${esc(r.name_th || '')}</option>`).join('');
    }).catch(() => {});
    bmcRenderPrep();
  } catch (e) { box.innerHTML = `<span style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function bmcPrepView(v) { _bmcPrepView = v; bmcRenderPrep(); }
function bmcRenderPrep() {
  const d = _bmcPrep;
  if (!d) return;
  const c = d.counts || {};
  const n = k => c[k] || 0;
  const chip = (label, v) => _bmcBtn(label, `bmcPrepView('${v}')`, false).replace('background:transparent', `background:${_bmcPrepView === v ? 'var(--accent-soft, var(--bg-hover))' : 'transparent'}`);
  document.getElementById('bmcPrepChips').innerHTML = chip(`ต้องดำเนินการ (${n('incomplete') + n('check')})`, 'todo')
    + chip(`ยังไม่มีสูตร (${n('none')})`, 'none') + chip(`ใช้ต้นทุนจากสูตร (${n('ok')})`, 'ok') + chip(`ทั้งหมด (${d.rows.length})`, 'all');
  document.getElementById('bmcPrepMonth').textContent = `ใช้ราคาเดือน ${d.month}`;
  const q = (document.getElementById('bmcPrepQ').value || '').trim().toLowerCase();
  const rows = d.rows.filter(r => (_bmcPrepView === 'all' || (_bmcPrepView === 'todo' ? (r.status === 'incomplete' || r.status === 'check') : r.status === _bmcPrepView))
    && (!q || (r.code + ' ' + (r.name || '')).toLowerCase().includes(q)));
  document.getElementById('bmcPrepList').innerHTML = rows.length ? rows.map(r => {
    const [label, ink] = _BMC_PREP[r.status];
    const cost = r.has_recipe
      ? `1 สูตร = ${_bmcNum(r.yield_qty)} ${esc(r.yield_unit || '')} · ${_bmcNum(r.lines, 0)} วัตถุดิบ · จากสูตร <b>${r.per_base != null ? '฿' + _bmcPrice(r.per_base) : '—'}</b>/${esc(r.base_unit || '')}`
        + ` · ราคาซื้อ ${r.own_price != null ? '฿' + _bmcPrice(r.own_price) : '—'}${r.diff_pct != null ? ` (<b style="color:${Math.abs(r.diff_pct) > d.tolerance_pct ? 'var(--amber-ink)' : 'var(--good-ink)'}">${r.diff_pct > 0 ? '+' : ''}${_bmcNum(r.diff_pct, 1)}%</b>)` : ''}`
      : `ใช้ราคาซื้อ ${r.own_price != null ? '฿' + _bmcPrice(r.own_price) + '/' + esc(r.base_unit || '') : 'ไม่มีราคา'}${r.qty_per_recv ? ` · ขนาดบรรจุ ${_bmcNum(r.qty_per_recv)} ${esc(r.base_unit || '')}` : ''}`;
    return `<div style="border-top:1px solid var(--border);padding:7px 0">
      <div style="display:flex;gap:8px;align-items:baseline;flex-wrap:wrap"><b style="color:var(--text-primary)">${esc(r.code)}</b>
        <span style="color:var(--text-primary)">${esc(r.name || '')}</span>${_bmcChip(label, ink)}
        <span style="margin-left:auto">${_bmcBtn(r.has_recipe ? 'เปิดสูตร' : (_bmcCanWrite() ? 'เพิ่มสูตร' : 'ดู'), `bmcOpenPrep(${_bmcQ(r.code)})`)}</span></div>
      <div class="mt-0.5" style="color:var(--text-secondary)">${cost}</div>
      ${(r.problems || []).length ? `<div class="mt-0.5" style="color:var(--bad-ink)">${r.problems.slice(0, 3).map(esc).join(' · ')}${r.n_problems > 3 ? ` +${r.n_problems - 3}` : ''}</div>` : ''}
      ${r.source_ref ? `<div class="text-[10.5px]" style="color:var(--text-muted)">ที่มา: ${esc(r.source_ref)}${r.updated_by ? ' · ' + esc(r.updated_by) : ''}</div>` : ''}
    </div>`;
  }).join('') : `<div style="color:var(--good-ink)">${_bmcPrepView === 'todo' ? '✓ ไม่มีสูตรที่ต้องดำเนินการ' : 'ไม่มีรายการ'}</div>`;
}

async function bmcOpenPrep(code) {
  const box = document.getElementById('bmcPrepDetail');
  box.style.display = '';
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</span>';
  try {
    const d = await _bmcJson(`${API}/api/bom-center/prep/${encodeURIComponent(code)}`);
    _bmcPrepEdit = { code, yield_qty: d.yield_qty || d.qty_per_recv || '', yield_unit: d.yield_unit || d.base_unit || '',
      lines: (d.lines || []).map(l => ({ ingredient_code: l.ingredient_code || '', qty: l.qty, unit: l.unit || '', note: l.note || '',
        ingredient_name: l.ingredient_name, cost: l.cost, price_per_base: l.price_per_base, source: l.source, problem: l.problem })), d };
    bmcRenderPrepEdit();
    box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function bmcRenderPrepEdit() {
  const e = _bmcPrepEdit, d = e.d, w = _bmcCanWrite();
  const box = document.getElementById('bmcPrepDetail');
  const lines = e.lines.map((l, i) => `<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;border-top:1px solid var(--border);padding:5px 0">
      <span style="width:18px;color:var(--text-muted)">${i + 1}</span>
      ${w ? _bmcInput(`list="bmcCodeList" value="${esc(l.ingredient_code)}" placeholder="รหัส" onchange="_bmcPrepEdit.lines[${i}].ingredient_code=this.value.trim()"`, '110px')
          + _bmcInput(`type="number" step="any" value="${l.qty ?? ''}" onchange="_bmcPrepEdit.lines[${i}].qty=this.value"`, '80px')
          + _bmcInput(`value="${esc(l.unit)}" placeholder="หน่วย" onchange="_bmcPrepEdit.lines[${i}].unit=this.value"`, '80px')
        : `<b>${esc(l.ingredient_code || '—')}</b> ${_bmcNum(l.qty)} ${esc(l.unit)}`}
      <span style="color:var(--text-secondary);min-width:120px">${esc(l.ingredient_name || l.note || '')}</span>
      <span style="color:var(--text-muted)">${l.cost != null ? '฿' + _bmcNum(l.cost, 2) + (l.source === 'sub_recipe' ? ' (จากสูตรย่อย)' : '') : ''}</span>
      ${l.problem ? `<span style="color:var(--bad-ink)">${esc(l.problem)}</span>` : ''}
      ${w ? `<button onclick="_bmcPrepEdit.lines.splice(${i},1);bmcRenderPrepEdit()" style="margin-left:auto;font-size:11px;color:var(--bad-ink);background:none;border:0;cursor:pointer">ลบ</button>` : ''}
    </div>`).join('');
  const raw = (d.raw_per_base || []).slice(0, 12).map(x => `<b>${esc(x.code)}</b> ${esc(x.name || '')} ${_bmcNum(x.qty_per_base, 4)}`).join(' · ');
  box.innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap">
      <div class="text-sm font-semibold" style="color:var(--text-primary)">${esc(d.code)} ${esc(d.name || '')}</div>
      ${_bmcBtn('ปิด', "document.getElementById('bmcPrepDetail').style.display='none'")}</div>
    <div class="text-xs mt-1" style="color:var(--text-secondary)">หน่วยฐาน ${esc(d.base_unit || '—')}${d.qty_per_recv ? ` · ขนาดบรรจุในทะเบียน ${_bmcNum(d.qty_per_recv)}` : ''} ·
      ราคาซื้อ ${d.own_price != null ? '฿' + _bmcPrice(d.own_price) : '—'} · จากสูตร ${d.per_base != null ? '<b>฿' + _bmcPrice(d.per_base) + '</b>' : '—'}
      ${d.batch_cost != null && d.has_recipe ? ` · ต้นทุนต่อ 1 สูตร ฿${_bmcNum(d.batch_cost, 2)}` : ''}${d.source_cost != null ? ` (ไฟล์ครัว ฿${_bmcNum(d.source_cost, 2)})` : ''} · ราคาเดือน ${esc(d.month)}</div>
    <div class="flex flex-wrap items-center gap-2 mt-2 text-xs" style="color:var(--text-muted)">1 สูตร ได้
      ${w ? _bmcInput(`type="number" step="any" value="${e.yield_qty}" onchange="_bmcPrepEdit.yield_qty=this.value"`, '100px') + _bmcInput(`value="${esc(e.yield_unit)}" onchange="_bmcPrepEdit.yield_unit=this.value"`, '90px')
        : `<b>${_bmcNum(e.yield_qty)} ${esc(e.yield_unit)}</b>`}</div>
    <div class="text-xs mt-2">${lines || '<span style="color:var(--text-muted)">ยังไม่มีวัตถุดิบในสูตร</span>'}</div>
    ${w ? `<div class="flex flex-wrap gap-2 mt-2">${_bmcBtn('+ เพิ่มวัตถุดิบ', "_bmcPrepEdit.lines.push({ingredient_code:'',qty:'',unit:'',note:''});bmcRenderPrepEdit()")}
      ${_bmcBtn('บันทึกสูตร', 'bmcSavePrep()', true)}<span id="bmcPrepEditStatus" class="text-xs"></span></div>` : ''}
    ${(d.problems || []).length ? `<div class="text-xs mt-2" style="color:var(--bad-ink)">ยังคิดต้นทุนจากสูตรไม่ได้: ${d.problems.slice(0, 5).map(esc).join(' · ')} · ระหว่างนี้ระบบใช้ราคาซื้อ</div>` : ''}
    ${raw ? `<div class="text-[11px] mt-2" style="color:var(--text-secondary)">ใน 1 ${esc(d.base_unit || 'หน่วย')} มีวัตถุดิบดิบ (แผนสั่งใช้ตัวเลขนี้): ${raw}</div>` : ''}`;
}
async function bmcSavePrep() {
  const e = _bmcPrepEdit;
  try {
    await _bmcJson(`${API}/api/bom-center/prep/${encodeURIComponent(e.code)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ yield_qty: Number(e.yield_qty), yield_unit: e.yield_unit,
        lines: e.lines.map(l => ({ ingredient_code: l.ingredient_code || null, qty: l.qty === '' ? null : Number(l.qty), unit: l.unit || null, note: l.note || null })) }) });
    await bmcOpenPrep(e.code);
    _bmcStatus('bmcPrepEditStatus', '✓ บันทึกแล้ว', 'var(--good-ink)');
    bmcLoadPrep();
  } catch (err) { _bmcStatus('bmcPrepEditStatus', 'ดำเนินการไม่สำเร็จ: ' + String(err.message || err).slice(0, 200), 'var(--bad-ink)'); }
}

let _bmcPrepFile = null;
async function bmcPrepImportSemi(input) {
  const file = input?.files?.[0];
  if (!file) return;
  _bmcPrepFile = file;
  input.value = '';
  await bmcPrepPreview();
}
async function bmcPrepPreview() {
  const box = document.getElementById('bmcPrepImport');
  box.style.display = '';
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังอ่านไฟล์…</span>';
  try {
    const fd = new FormData(); fd.append('file', _bmcPrepFile, _bmcPrepFile.name);
    const p = await _bmcJson(`${API}/api/bom-center/prep/import-semi?dry_run=true`, { method: 'POST', body: fd });
    const methodTh = { 'name+yield': 'ชื่อ + ปริมาณต่อสูตรตรง', name: 'ชื่อตรง', map: 'จับคู่ด้วยตนเอง' };
    const writes = p.writes.map(w => `<div style="border-top:1px solid var(--border);padding:5px 0">
        <b>${esc(w.prepared_code)}</b> ${esc(w.prepared_name || '')} ← <span style="color:var(--text-secondary)">แถว ${w.row}: ${esc(w.src_code)} ${esc(w.src_name)} (${esc(w.yield_text || '')})</span>
        ${_bmcChip(methodTh[w.method] || w.method, w.score >= 1.1 || w.method === 'map' ? 'var(--good-ink)' : 'var(--amber-ink)')}
        ${w.replaces ? _bmcChip('แทนสูตรเดิม', 'var(--text-muted)') : ''}
        ${w.unresolved ? `<span style="color:var(--bad-ink)"> · ไม่พบรหัส ${w.unresolved}:${w.lines.filter(l => !l.ingredient_code).map(l => esc(l.note)).join(', ')}</span>` : ''}</div>`).join('');
    const um = p.unmatched.map(u => `<div style="border-top:1px solid var(--border);padding:5px 0;display:flex;gap:6px;flex-wrap:wrap;align-items:center">
        <span>แถว ${u.row}: <b>${esc(u.src_code)} ${esc(u.name)}</b> <span style="color:var(--text-muted)">${esc(u.yield_text || '')}</span></span>
        ${_bmcCanWrite() ? _bmcInput(`list="bmcCodeList" placeholder="รหัสในทะเบียน" id="bmcPrepMap_${u.row}"`, '120px')
          + _bmcBtn('จับคู่', `bmcPrepMap(${_bmcQ(u.name)}, document.getElementById('bmcPrepMap_${u.row}').value)`)
          + _bmcBtn('ไม่ใช่รายการของร้าน', `bmcPrepMap(${_bmcQ(u.name)}, '-')`) : ''}</div>`).join('');
    box.innerHTML = `<div class="text-sm font-semibold" style="color:var(--text-primary)">ตรวจสอบก่อนบันทึก: ${esc(_bmcPrepFile.name)} · sheet ${esc(p.sheet || '')}</div>
      <p class="text-xs mt-1" style="color:var(--text-secondary)">สูตรในไฟล์ ${p.rows} สูตร · บันทึกได้ <b>${p.writes.length}</b> รหัสในทะเบียน (จับคู่ด้วยชื่อและปริมาณที่ได้ต่อสูตร ไม่ใช้รหัสในไฟล์เนื่องจากเป็นชุดรหัสเก่า) ·
        ไม่พบรหัสวัตถุดิบ ${p.unresolved_lines} บรรทัด (บันทึกได้ และระบุรหัสภายหลังในหน้าสูตร) · สูตรในไฟล์ที่ยังจับคู่รหัสไม่ได้ ${p.unmatched.length} ·
        รหัสกึ่งสำเร็จที่ยังไม่มีสูตร ${p.prepared_without.length}</p>
      ${_bmcCanWrite() ? `<div class="flex gap-2 my-2">${_bmcBtn(`บันทึก ${p.writes.length} สูตร`, 'bmcPrepApply()', true)}${_bmcBtn('ยกเลิก', "document.getElementById('bmcPrepImport').style.display='none'")}<span id="bmcPrepApplyStatus" class="text-xs"></span></div>` : ''}
      ${um ? `<div class="text-xs font-semibold mt-2" style="color:var(--text-primary)">สูตรในไฟล์ที่ยังจับคู่รหัสไม่ได้: กรุณาระบุรหัส หรือกด “ไม่ใช่รายการของร้าน”</div><div class="text-xs">${um}</div>` : ''}
      <div class="text-xs font-semibold mt-3" style="color:var(--text-primary)">รายการที่จะบันทึก (สีเหลือง = ชื่อไม่ตรงกันทั้งหมด ควรตรวจสอบ)</div><div class="text-xs">${writes}</div>`;
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
async function bmcPrepMap(name, code) {
  code = (code || '').trim();
  if (!code) return;
  try {
    await _bmcJson(`${API}/api/bom-center/prep/map`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ src_name: name, prepared_code: code }) });
    await bmcPrepPreview();
  } catch (e) { alert('จับคู่ไม่สำเร็จ: ' + String(e.message || e)); }
}
async function bmcPrepApply() {
  try {
    const fd = new FormData(); fd.append('file', _bmcPrepFile, _bmcPrepFile.name);
    const r = await _bmcJson(`${API}/api/bom-center/prep/import-semi?dry_run=false`, { method: 'POST', body: fd });
    document.getElementById('bmcPrepImport').style.display = 'none';
    _bmcStatus('bmcPrepStatus', `✓ บันทึกสูตรกึ่งสำเร็จ ${r.saved} รหัส`, 'var(--good-ink)');
    bmcLoadPrep();
  } catch (e) { _bmcStatus('bmcPrepApplyStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
function bmcPrepTemplate() { window.location.href = `${API}/api/bom-center/prep/template.xlsx`; }
async function bmcPrepImportFlat(input) {
  const file = input?.files?.[0];
  if (!file) return;
  input.value = '';
  const send = dry => { const fd = new FormData(); fd.append('file', file, file.name);
    return _bmcJson(`${API}/api/bom-center/prep/import-flat?dry_run=${dry}`, { method: 'POST', body: fd }); };
  try {
    const pre = await send(true);
    if (pre.errors.length) { _bmcStatus('bmcPrepStatus', 'ไฟล์มีปัญหา: ' + pre.errors.slice(0, 3).join(' · '), 'var(--bad-ink)'); return; }
    if (!await uiConfirm(`ยืนยันการนำเข้าแม่แบบ: สูตรของ ${pre.codes} รหัสจะถูกแทนที่`)) return;
    const r = await send(false);
    _bmcStatus('bmcPrepStatus', `✓ บันทึก ${r.saved} รหัส`, 'var(--good-ink)');
    bmcLoadPrep();
  } catch (e) { _bmcStatus('bmcPrepStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}

// ── unit converter ──────────────────────────────────────────────────────────
async function bmcLoadConv() {
  if (!_bmcIng.length) await bmcLoadIng(true);
  const dl = document.getElementById('bmcCodeList');
  if (dl && !dl.children.length) dl.innerHTML = _bmcIng.map(r => `<option value="${esc(r.code)}">${esc(r.name_th || '')}</option>`).join('');
  bmcRenderRules();
}
function bmcRenderRules() {
  const code = (document.getElementById('bmcConvCode').value || '').trim();
  const r = _bmcIng.find(x => x.code === code);
  const box = document.getElementById('bmcRuleList');
  if (!r) { box.innerHTML = '<span style="color:var(--text-muted)">กรุณาระบุรหัสด้านบนเพื่อดูกฎหน่วยของวัตถุดิบ</span>'; return; }
  box.innerHTML = `<div style="color:var(--text-secondary)"><b>${esc(r.code)}</b> ${esc(r.name_th || '')} · หน่วยฐาน <b>${esc(r.base_unit || '—')}</b>` +
    (r.qty_per_recv ? ` · 1 ${esc(r.recv_unit || 'หน่วยรับ')} = ${_bmcNum(r.qty_per_recv)} ${esc(r.base_unit || '')}` : ' · ยังไม่มีขนาดบรรจุ') + `</div>` +
    (r.unit_rules.length ? r.unit_rules.map(u => `<div class="py-0.5">1 ${esc(u.unit)} = ${_bmcNum(u.factor, 4)} ${esc(r.base_unit || '')}
      ${_bmcCanWrite() ? `<button onclick="bmcDelRule('${esc(r.code)}','${esc(u.unit)}')" style="font-size:11px;color:var(--bad-ink);background:none;border:0;cursor:pointer">ลบ</button>` : ''}</div>`).join('')
      : '<div style="color:var(--text-muted)">ยังไม่มีกฎหน่วยครัว</div>');
}
async function bmcConvert() {
  const code = (document.getElementById('bmcConvCode').value || '').trim();
  const qty = document.getElementById('bmcConvQty').value || 1;
  const unit = (document.getElementById('bmcConvUnit').value || '').trim();
  const out = document.getElementById('bmcConvOut');
  bmcRenderRules();
  if (!code || !unit) { out.textContent = 'กรุณาระบุรหัสและหน่วย'; return; }
  try {
    const d = await _bmcJson(`${API}/api/bom-center/convert?code=${encodeURIComponent(code)}&qty=${qty}&unit=${encodeURIComponent(unit)}&month=${_bmcMonth()}`);
    out.innerHTML = d.qty_base == null
      ? `<span style="color:var(--amber-ink)">แปลงหน่วยไม่ได้: ${esc(d.how)}</span>`
      : `${_bmcNum(d.qty)} ${esc(d.unit)} = <b>${_bmcNum(d.qty_base, 3)} ${esc(d.base_unit)}</b> <span style="color:var(--text-muted)">(${esc(d.how)})</span>` +
        (d.cost != null ? ` · ต้นทุน <b>฿${_bmcNum(d.cost, 3)}</b> ที่ราคา ${_bmcPrice(d.price_per_base)}/${esc(d.base_unit)} เดือน ${esc(d.month)}` : ' · ยังไม่มีราคา');
  } catch (e) { out.innerHTML = `<span style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
async function _bmcPostRule(code, unit, factor) {
  await _bmcJson(`${API}/api/bom-center/units`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify([{ code, unit, factor_to_base: factor }]) });
  await bmcLoadIng(true);
  bmcRenderRules();
}
async function bmcSaveRule() {
  const code = (document.getElementById('bmcConvCode').value || '').trim();
  const unit = (document.getElementById('bmcRuleUnit').value || '').trim();
  const f = Number(document.getElementById('bmcRuleFactor').value);
  if (!code || !unit || !(f > 0)) { _bmcStatus('bmcRuleStatus', 'กรุณาระบุรหัส หน่วย และตัวคูณที่มากกว่า 0', 'var(--bad-ink)'); return; }
  try { await _bmcPostRule(code, unit, f); _bmcStatus('bmcRuleStatus', `✓ 1 ${unit} = ${f}`, 'var(--good-ink)'); }
  catch (e) { _bmcStatus('bmcRuleStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcDelRule(code, unit) {
  if (!await uiConfirm(`ยืนยันการลบกฎ 1 ${unit} ของ ${code}`)) return;
  try { await _bmcPostRule(code, unit, null); } catch (e) { _bmcStatus('bmcRuleStatus', String(e.message || e), 'var(--bad-ink)'); }
}

// ── recipes by tier (Sprint S2) ─────────────────────────────────────────────
let _bmcRel = null;          // the release being viewed
let _bmcRels = [];
let _bmcTier = 0;            // 0 = every dish; else the basket of that tier (min_tier <= T)
let _bmcRecView = null;      // /api/bom-center/recipes
let _bmcEdit = null;         // {sku, d, lines}
const _BMC_TIERS = [459, 629, 789, 999, 1299];

function _bmcEditableFor(rel) { return !!(rel && ['draft', 'review'].includes(rel.status) && _bmcCanWrite()); }
function _bmcRelEditable() { return _bmcEditableFor(_bmcRel); }
function _bmcEdRel() { return (_bmcEdit && _bmcEdit.rel) || _bmcRel; }   // the release the open editor belongs to

async function bmcLoadReleases(keepId) {
  try {
    const d = await _bmcJson(`${API}/api/bom-center/releases`);
    _bmcRels = d.releases || [];
    window._bmcRelSuggest = d.suggest || {};
    // the Cost-RW card's months: pre-filled with the suggestion, edited in place (no prompts before the file)
    const eff = document.getElementById('bmcCrwEff'), pm = document.getElementById('bmcCrwPm');
    if (eff && !eff.value) eff.value = (d.suggest || {}).effective_month || '';
    if (pm && !pm.value) pm.value = (d.suggest || {}).price_month || '';
    const sel = document.getElementById('bmcRel');
    const cur = keepId || Number(sel.value) || (_bmcRels.find(r => r.kind === 'monthly') || _bmcRels[0] || {}).release_id;
    const st = { draft: 'ฉบับร่าง', review: 'รอตรวจ', released: 'ปล่อยแล้ว', retired: 'เลิกใช้' };
    sel.innerHTML = _bmcRels.map(r => `<option value="${r.release_id}" ${r.release_id === cur ? 'selected' : ''}>${esc(r.label)} · ${st[r.status] || r.status}`
      + (r.kind === 'baseline' ? ' (ระบบเดิม)' : ` · มีผล ${esc(r.effective_month)} · ราคา ${esc(r.price_month || '-')}`) + `</option>`).join('')
      || '<option value="">ยังไม่มีเวอร์ชัน กรุณานำเข้าไฟล์ Cost-RW</option>';
    document.getElementById('bmcTierChips').innerHTML = [0, ..._BMC_TIERS].map(t =>
      `<button data-bmctier="${t}" onclick="bmcSetTier(${t})" style="font-size:12px;padding:3px 10px;border-radius:3px;border:1px solid var(--border);background:var(--bg-card);color:var(--text-secondary);cursor:pointer">${t ? '฿' + t.toLocaleString() : 'ทุกเมนู'}</button>`).join('');
    await bmcLoadRecipes();
  } catch (e) { _bmcStatus('bmcRecStatus', 'โหลดข้อมูลไม่สำเร็จ:' + String(e.message || e), 'var(--bad-ink)'); }
}
function bmcSetTier(t) { _bmcTier = t; bmcRenderRecipes(); }
async function bmcLoadRecipes() {
  const id = Number(document.getElementById('bmcRel').value);
  _bmcRel = _bmcRels.find(r => r.release_id === id) || null;
  document.getElementById('bmcEditor').style.display = 'none';
  if (!_bmcRel) { document.getElementById('bmcRecBody').innerHTML = '<tr><td colspan="10" class="py-3" style="color:var(--text-muted)">ยังไม่มีสูตร กรุณากด “นำเข้าสูตรจากไฟล์ Cost-RW”</td></tr>'; return; }
  document.getElementById('bmcRecBody').innerHTML = '<tr><td colspan="10" class="py-3" style="color:var(--text-muted)">กำลังคำนวณต้นทุน…</td></tr>';
  try {
    _bmcRecView = await _bmcJson(`${API}/api/bom-center/recipes?release_id=${id}`);
    bmcRenderRecipes();
    bmcRenderUnmatched();
  } catch (e) { document.getElementById('bmcRecBody').innerHTML = `<tr><td colspan="10" style="color:var(--bad-ink)">${esc(String(e.message || e))}</td></tr>`; }
}
function _bmcRecIssues(r) {
  const out = [];
  if (r.problems) out.push(['คิดต้นทุนไม่ได้ ' + r.problems + ' บรรทัด', 'var(--bad-ink)']);
  if (r.sheet_diff != null && Math.abs(r.sheet_diff) > 0.01) out.push(['ไม่ตรงไฟล์ต้นทาง', 'var(--amber-ink)']);
  if (r.v0_diff_pct != null && Math.abs(r.v0_diff_pct) >= 20) out.push(['ต่างจากระบบเดิม ≥20%', 'var(--amber-ink)']);
  if ((r.source_ref || '').includes('จับคู่โดย')) out.push(['จับคู่ด้วยตนเอง', 'var(--navy-ink)']);
  return out;
}
function bmcRenderRecipes() {
  document.querySelectorAll('#bmcTierChips [data-bmctier]').forEach(b => {
    const on = Number(b.dataset.bmctier) === _bmcTier;
    b.style.background = on ? 'var(--accent)' : 'var(--bg-card)'; b.style.color = on ? 'var(--on-accent)' : 'var(--text-secondary)';
  });
  if (!_bmcRecView) return;
  const q = (document.getElementById('bmcRecQ').value || '').toLowerCase();
  const only = document.getElementById('bmcRecIssues').checked;
  const rows = _bmcRecView.rows.filter(r => (!_bmcTier || !r.min_tier || r.min_tier <= _bmcTier)
    && (!q || `${r.sku_code} ${r.name || ''} ${r.name_en || ''}`.toLowerCase().includes(q))
    && (!only || _bmcRecIssues(r).length));
  document.getElementById('bmcRecBody').innerHTML = rows.map(r => {
    const own = _bmcTier && r.min_tier === _bmcTier;
    const iss = _bmcRecIssues(r);
    const dv = r.v0_diff_pct;
    return `<tr class="border-b" style="border-color:var(--border-soft);${_bmcTier && !own ? 'opacity:.72' : ''}">
      <td class="py-1 pr-2">${r.min_tier ? _bmcChip('฿' + r.min_tier.toLocaleString(), own || !_bmcTier ? 'var(--accent)' : 'var(--text-muted)') : ''}</td>
      <td class="py-1 pr-2"><a href="javascript:void(0)" onclick="bmcOpenRecipe('${esc(r.sku_code)}')" style="color:var(--accent)">${esc(r.name || '')}</a>
        ${r.name_en ? `<div class="text-[10.5px]" style="color:var(--text-muted)">${esc(r.name_en)}</div>` : ''}</td>
      <td class="py-1 pr-2 whitespace-nowrap">${esc(r.sku_code)}</td>
      <td class="py-1 pr-2 text-[11px]">${esc(r.serve || '')}</td>
      <td class="py-1 pr-2 text-right font-semibold">${_bmcNum(r.cost)}</td>
      <td class="py-1 pr-2 text-right">${_bmcNum(r.sheet_cost)}</td>
      <td class="py-1 pr-2 text-right">${_bmcNum(r.v0_cost)}</td>
      <td class="py-1 pr-2 text-right" style="color:${dv == null ? 'inherit' : Math.abs(dv) >= 20 ? 'var(--amber-ink)' : 'var(--text-muted)'}">${dv == null ? '' : (dv > 0 ? '+' : '') + dv + '%'}</td>
      <td class="py-1 pr-2 text-right">${r.lines}</td>
      <td class="py-1">${iss.length ? iss.map(([t, c]) => _bmcChip(t, c)).join(' ') : _bmcChip('พร้อม', 'var(--good-ink)')}</td></tr>`;
  }).join('') || '<tr><td colspan="10" class="py-3" style="color:var(--text-muted)">ไม่มีเมนู</td></tr>';
  const v = _bmcRecView;
  const avg = rows.length ? rows.reduce((s, r) => s + (r.cost || 0), 0) / rows.length : 0;
  _bmcStatus('bmcRecSummary', `${rows.length} เมนู${_bmcTier ? ` ในตะกร้า ฿${_bmcTier.toLocaleString()} (รวมเมนู Tier ต่ำกว่า)` : ''} · เฉลี่ย ฿${_bmcNum(avg)}/เสิร์ฟ · `
    + `ต้องตรวจ ${v.rows.filter(r => _bmcRecIssues(r).length).length} · ยังไม่จับคู่ ${v.unmatched.length}`
    + (_bmcRel ? ` · คิดราคาเดือน ${_bmcRel.price_month || '-'}` : ''));
}
function bmcRenderUnmatched() {
  const box = document.getElementById('bmcUnmatched');
  const u = (_bmcRecView && _bmcRecView.unmatched) || [];
  if (!u.length) { box.innerHTML = ''; return; }
  const w = _bmcRelEditable();
  box.innerHTML = `<div class="card p-4">
    <div class="text-sm font-semibold" style="color:var(--text-primary)">สูตรที่ยังไม่ได้จับคู่รหัส POS (${u.length})</div>
    <div class="text-[11px] mb-2" style="color:var(--text-muted)">ไฟล์ Cost-RW ไม่มีรหัส POS ในบล็อกสูตร ระบบจึงจับคู่จากชื่อ รายการต่อไปนี้ชื่อไม่ตรงกับเมนูใน POS ·
      กรุณาเลือกรหัสแล้วกด “จับคู่” (ระบบจะจดจำไว้สำหรับการนำเข้าครั้งถัดไป) · หากเมนูยังไม่วางขาย กด “ข้าม (ไม่วางขาย)”</div>
    <datalist id="bmcSkuList"></datalist>
    <div class="overflow-x-auto"><table class="text-xs w-full"><tbody>${u.map((x, i) => `<tr class="border-b" style="border-color:var(--border-soft)">
      <td class="pr-2 py-1">${_bmcChip('฿' + Number(x.tier).toLocaleString(), 'var(--accent)')}</td>
      <td class="pr-2">${esc(x.th || '')}<div class="text-[10.5px]" style="color:var(--text-muted)">${esc(x.en || '')} · ${x.n_lines} บรรทัด · ไฟล์ ฿${_bmcNum(x.total)}${x.why ? ' · ' + esc(x.why) : ''}</div></td>
      <td class="pr-2">${w ? `<input id="bmcMap${i}" list="bmcSkuList" placeholder="รหัส OTHBF…" style="width:150px;border:1px solid var(--border-control);border-radius:4px;padding:2px 6px;font-size:12px;background:var(--bg-input);color:var(--text-primary)">` : ''}</td>
      <td class="whitespace-nowrap">${w ? `<button onclick="bmcMapBlock(${i})" style="font-size:11.5px;background:var(--accent);color:var(--on-accent);border:0;border-radius:4px;padding:2px 10px;cursor:pointer">จับคู่</button>
        <button onclick="bmcMapBlock(${i}, true)" style="font-size:11.5px;background:none;color:var(--text-secondary);border:1px solid var(--border-control);border-radius:4px;padding:2px 8px;cursor:pointer">ข้าม (ไม่วางขาย)</button>` : ''}</td></tr>`).join('')}</tbody></table></div></div>`;
  if (w) _bmcJson(`${API}/api/bom-center/buffet-skus?release_id=${_bmcRel.release_id}`).then(d => {
    document.getElementById('bmcSkuList').innerHTML = d.rows.filter(r => !r.has_recipe)
      .map(r => `<option value="${esc(r.sku_code)}">${r.tier ? '฿' + r.tier + ' · ' : ''}${esc(r.name || '')}</option>`).join('');
  }).catch(() => {});
}
async function bmcMapBlock(i, skip) {
  const x = _bmcRecView.unmatched[i];
  const sku = skip ? '-' : (document.getElementById('bmcMap' + i).value || '').trim();
  if (!sku) { _bmcStatus('bmcRecStatus', 'กรุณาเลือกรหัส POS', 'var(--bad-ink)'); return; }
  try {
    await _bmcJson(`${API}/api/bom-center/recipes/map`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ release_id: _bmcRel.release_id, ref: x.ref, sku_code: sku }) });
    _bmcStatus('bmcRecStatus', skip ? `✓ ${x.th}: บันทึกว่าไม่วางขาย` : `✓ ${x.th} → ${sku}`, 'var(--good-ink)');
    bmcLoadRecipes();
  } catch (e) { _bmcStatus('bmcRecStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}

// recipe editor ---------------------------------------------------------------
async function bmcOpenRecipe(sku, opt) {   // opt {rel, host}: the release pane opens a recipe in its own card
  const rel = (opt && opt.rel) || _bmcRel, host = (opt && opt.host) || 'bmcEditor';
  const box = document.getElementById(host);
  box.style.display = ''; box.innerHTML = 'กำลังโหลด…';
  try {
    const d = await _bmcJson(`${API}/api/bom-center/recipes/${encodeURIComponent(sku)}?release_id=${rel.release_id}`);
    _bmcEdit = { sku, d, lines: d.lines.map(l => ({ ...l })), rel, host };
    if (!_bmcIng.length) bmcLoadIng(true).then(() => _bmcFillIngList());
    else _bmcFillIngList();
    bmcRenderEditor();
    box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) { box.innerHTML = `<span style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function _bmcFillIngList() {
  let dl = document.getElementById('bmcIngList');
  if (!dl) { dl = document.createElement('datalist'); dl.id = 'bmcIngList'; document.body.appendChild(dl); }
  dl.innerHTML = _bmcIng.map(r => `<option value="${esc(r.code)}">${esc(r.name_th || '')} (${esc(r.base_unit || '')})</option>`).join('');
}
function bmcRenderEditor() {
  const e = _bmcEdit, d = e.d, w = _bmcEditableFor(e.rel || _bmcRel), host = e.host || 'bmcEditor';
  const inp = (i, f, width, type) => w
    ? `<input ${f === 'ingredient_code' ? 'list="bmcIngList"' : ''} ${type ? `type="${type}" step="any"` : ''} value="${esc(e.lines[i][f] ?? '')}" onchange="bmcEdLine(${i},'${f}',this)"
        style="width:${width}px;border:1px solid var(--border);border-radius:4px;padding:2px 4px;font-size:11.5px;background:var(--bg-input);color:var(--text-primary);${type ? 'text-align:right' : ''}">`
    : esc(e.lines[i][f] ?? '');
  const total = e.lines.reduce((s, l) => s + (l.cost || 0), 0);
  document.getElementById(host).innerHTML = `
    <div class="flex flex-wrap items-start gap-2 mb-2"><div>
      <div class="text-sm font-semibold" style="color:var(--text-primary)">${esc(d.dish?.name_th || d.pos_name || e.sku)} <span class="text-xs" style="color:var(--text-muted)">${esc(e.sku)} · ${esc(d.dish?.serve || '')}</span></div>
      <div class="text-[11px]" style="color:var(--text-muted)">${d.dish?.min_tier ? 'Tier ฿' + d.dish.min_tier.toLocaleString() + ' · ' : ''}${esc(d.source_ref || '')} · ราคาเดือน ${esc(d.release?.price_month || '-')}
        ${w ? '' : ' · <b>อ่านอย่างเดียว</b> (แก้ไขได้เฉพาะสูตรฉบับร่าง)'}</div></div>
      <div class="ml-auto text-right"><div class="text-[11px]" style="color:var(--text-muted)">ต้นทุน/เสิร์ฟ</div><div class="text-xl font-bold" style="color:var(--text-primary)">฿${_bmcNum(total, 2)}</div>
        ${d.source_cost != null ? `<div class="text-[11px]" style="color:var(--text-muted)">ไฟล์ต้นทาง ฿${_bmcNum(d.source_cost, 2)}</div>` : ''}</div>
      <button onclick="document.getElementById('${host}').style.display='none'" style="font-size:12px;background:none;border:0;color:var(--text-muted);cursor:pointer">ปิด</button></div>
    <div class="overflow-x-auto"><table class="text-xs w-full" style="font-variant-numeric:tabular-nums">
      <thead><tr class="text-left border-b" style="color:var(--text-muted)"><th class="pr-2">#</th><th class="pr-2">รหัสวัตถุดิบ</th><th class="pr-2">วัตถุดิบ</th>
        <th class="pr-2 text-right">ปริมาณ</th><th class="pr-2">หน่วย</th><th class="pr-2 text-right">สูญเสีย %</th><th class="pr-2">฿0 โดยเจตนา</th>
        <th class="pr-2 text-right">ราคา/หน่วยฐาน</th><th class="pr-2 text-right">ต้นทุน</th><th class="pr-2">การเตรียม</th><th></th></tr></thead>
      <tbody>${e.lines.map((l, i) => `<tr class="border-b" style="border-color:var(--border-soft)">
        <td class="pr-2">${i + 1}</td><td class="pr-2">${inp(i, 'ingredient_code', 95)}</td>
        <td class="pr-2">${inp(i, 'ingredient_name', 170)}${l.master_name && l.master_name !== l.ingredient_name ? `<div class="text-[10px]" style="color:var(--text-muted)">${esc(l.master_name)}</div>` : ''}</td>
        <td class="pr-2 text-right">${inp(i, 'qty', 70, 'number')}</td><td class="pr-2">${inp(i, 'unit', 70)}</td>
        <td class="pr-2 text-right">${inp(i, 'loss_pct', 55, 'number')}</td>
        <td class="pr-2 text-center">${w ? `<input type="checkbox" ${l.zero_by_design ? 'checked' : ''} onchange="bmcEdLine(${i},'zero_by_design',this)">` : (l.zero_by_design ? '✓' : '')}</td>
        <td class="pr-2 text-right">${_bmcPrice(l.price_per_base)}</td>
        <td class="pr-2 text-right font-semibold">${l.problem ? `<span style="color:var(--bad-ink)">${esc(l.problem)}</span>` : _bmcNum(l.cost, 3)}</td>
        <td class="pr-2">${inp(i, 'prep_note', 150)}</td>
        <td>${w ? `<button onclick="bmcEdDel(${i})" style="font-size:11px;color:var(--bad-ink);background:none;border:0;cursor:pointer">ลบ</button>` : ''}</td></tr>`).join('')}</tbody></table></div>
    ${w ? `<div class="flex flex-wrap items-center gap-2 mt-2">
      <button onclick="bmcEdAdd()" style="font-size:12px;background:var(--bg-input);color:var(--text-primary);border:1px solid var(--border-control);border-radius:4px;padding:3px 10px;cursor:pointer">+ เพิ่มบรรทัด</button>
      <button onclick="bmcEdPaste()" style="font-size:12px;background:var(--bg-input);color:var(--text-primary);border:1px solid var(--border-control);border-radius:4px;padding:3px 10px;cursor:pointer"
              title="คัดลอกช่วง 3 คอลัมน์จาก Excel: รหัส · ปริมาณ · หน่วย">วางจาก Excel</button>
      <button onclick="bmcEdSave()" class="ml-auto" style="font-size:13px;background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:5px 16px;font-weight:600;cursor:pointer">บันทึกสูตร</button>
      <span id="bmcEdStatus" class="text-[11px]" style="color:var(--text-muted)"></span></div>
      <textarea id="bmcPasteBox" placeholder="วางข้อมูลที่นี่ บรรทัดละ รหัส[แท็บ]ปริมาณ[แท็บ]หน่วย แล้วกด “วางจาก Excel” อีกครั้ง" style="display:none;width:100%;height:80px;margin-top:6px;border:1px solid var(--border-control);border-radius:4px;font-size:12px;background:var(--bg-input);color:var(--text-primary)"></textarea>` : ''}`;
}
async function _bmcPreviewLine(i) {        // cost one edited line at once: converter + the release's price month
  const l = _bmcEdit.lines[i];
  l.cost = 0; l.problem = null; l.price_per_base = null;
  if (l.zero_by_design) { bmcRenderEditor(); return; }
  if (!l.ingredient_code) { l.problem = 'ไม่มีรหัสวัตถุดิบ'; bmcRenderEditor(); return; }
  try {
    const c = await _bmcJson(`${API}/api/bom-center/convert?code=${encodeURIComponent(l.ingredient_code)}&qty=${Number(l.qty) || 0}&unit=${encodeURIComponent(l.unit || '')}&month=${_bmcEdRel().price_month || ''}`);
    if (c.qty_base == null) l.problem = c.how;
    else if (c.price_per_base == null) l.problem = 'ไม่มีราคา';
    else {
      const m = _bmcIng.find(r => r.code === l.ingredient_code);
      const loss = (l.loss_pct !== null && l.loss_pct !== '' && l.loss_pct !== undefined) ? Number(l.loss_pct) : (m?.loss_pct || 0);
      l.price_per_base = c.price_per_base; l.cost = c.qty_base * c.price_per_base / (1 - loss / 100);
    }
  } catch (e) { l.problem = String(e.message || e); }
  bmcRenderEditor();
}
function bmcEdLine(i, f, el) {
  const l = _bmcEdit.lines[i];
  l[f] = el.type === 'checkbox' ? el.checked : (['qty', 'loss_pct'].includes(f) ? (el.value === '' ? null : Number(el.value)) : el.value);
  if (f === 'ingredient_code') { const m = _bmcIng.find(r => r.code === el.value); if (m) { l.ingredient_name = l.ingredient_name || m.name_th; l.unit = l.unit || m.base_unit; } }
  if (['ingredient_code', 'qty', 'unit', 'loss_pct', 'zero_by_design'].includes(f)) _bmcPreviewLine(i);
}
function bmcEdAdd() { _bmcEdit.lines.push({ ingredient_code: '', ingredient_name: '', qty: 0, unit: '', loss_pct: null, zero_by_design: false, cost: 0 }); bmcRenderEditor(); }
function bmcEdDel(i) { _bmcEdit.lines.splice(i, 1); bmcRenderEditor(); }
async function bmcEdPaste() {
  const ta = document.getElementById('bmcPasteBox');
  if (ta.style.display === 'none') { ta.style.display = ''; ta.focus(); return; }
  const add = ta.value.split(/\r?\n/).map(r => r.split('\t')).filter(c => (c[0] || '').trim());
  const n0 = _bmcEdit.lines.length;
  for (const c of add) {
    const code = c[0].trim(), m = _bmcIng.find(r => r.code === code);
    _bmcEdit.lines.push({ ingredient_code: code, ingredient_name: m?.name_th || '', qty: Number(String(c[1] || '0').replace(/,/g, '')) || 0,
      unit: (c[2] || m?.base_unit || '').trim(), loss_pct: null, zero_by_design: false, cost: 0 });
  }
  for (let i = n0; i < _bmcEdit.lines.length; i++) await _bmcPreviewLine(i);
  bmcRenderEditor();
}
async function bmcEdSave() {
  const lines = _bmcEdit.lines.map(l => ({ ingredient_code: l.ingredient_code || null, ingredient_name: l.ingredient_name || null,
    qty: Number(l.qty) || 0, unit: l.unit || null, loss_pct: (l.loss_pct === '' || l.loss_pct == null) ? null : Number(l.loss_pct),
    zero_by_design: !!l.zero_by_design, prep_note: l.prep_note || null }));
  try {
    const r = await _bmcJson(`${API}/api/bom-center/recipes/${encodeURIComponent(_bmcEdit.sku)}?release_id=${_bmcEdRel().release_id}`,
      { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(lines) });
    const sku = _bmcEdit.sku, opt = { rel: _bmcEdit.rel, host: _bmcEdit.host };
    if (opt.host === 'bmcRlEditor') await bmcLoadChecks();        // edited from a finding: the checks read the fix at once
    else await bmcLoadRecipes();
    await bmcOpenRecipe(sku, opt);
    _bmcStatus('bmcEdStatus', `✓ บันทึกแล้ว · ต้นทุน ฿${_bmcNum(r.total, 2)}` + (r.problems ? ` · ยังมีปัญหา ${r.problems} บรรทัด` : ''), r.problems ? 'var(--amber-ink)' : 'var(--good-ink)');
  } catch (e) { _bmcStatus('bmcEdStatus', 'บันทึกไม่สำเร็จ:' + String(e.message || e), 'var(--bad-ink)'); }
}

// import / export ------------------------------------------------------------
function _bmcCostRwReport(p, applied) {
  return `<div class="card p-4"><div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">${applied ? '✓ นำเข้าแล้ว' : 'ตรวจสอบก่อนบันทึก'}: ${esc(p.label)}</div>
    <div class="text-xs" style="color:var(--text-secondary)">มีผล ${esc(p.effective_month)} · คิดราคาเดือน ${esc(p.price_month)} ·
      ${p.sheets.map(s => `${esc(s.sheet)} ${s.blocks}`).join(' · ')}</div>
    <div class="text-xs mt-1" style="color:var(--text-secondary)">บล็อกสูตร ${p.blocks} · จับคู่รหัส POS ได้ <b>${p.matched}</b>
      (ชื่อตรง ${p.by_method.exact || 0} · ต่าง tier ${p.by_method['exact-anytier'] || 0} · จากการจับคู่ครั้งก่อน ${p.by_method.map || 0} · ชื่อใกล้เคียง ${p.by_method.fuzzy || 0}) ·
      <b style="color:${p.unmatched.length ? 'var(--amber-ink)' : 'inherit'}">ยังไม่จับคู่ ${p.unmatched.length}</b> · ข้าม (ไม่ขาย) ${p.skipped_not_on_sale.length} ·
      บรรทัด ${p.lines} · ฿0 โดยเจตนา ${p.zero_lines} · เซลล์ error ในไฟล์ ${p.error_cells}</div>
    <div class="text-xs mt-1" style="color:var(--text-secondary)">ต้นทุนที่ระบบคำนวณ: ${p.costed} เมนู · คิดไม่ได้ ${p.with_problems} · ไม่ตรงยอดในไฟล์ (>฿0.01) <b>${p.sheet_mismatch}</b></div>
    ${p.fuzzy.length ? `<div class="text-[11px] mt-2" style="color:var(--navy-ink)">จับคู่จากชื่อใกล้เคียง (ควรตรวจสอบ): ${p.fuzzy.map(f => `${esc(f.th)} → ${esc(f.sku_code)}`).join(' · ')}</div>` : ''}
    ${p.mismatch_rows.length ? `<div class="text-[11px] mt-1" style="color:var(--amber-ink)">ไม่ตรงไฟล์: ${p.mismatch_rows.slice(0, 12).map(r => `${esc(r.name)} ระบบ ฿${_bmcNum(r.cost)} / ไฟล์ ฿${_bmcNum(r.sheet_cost)}`).join(' · ')}</div>` : ''}
  </div>`;
}
async function bmcImportCostRw(input) {
  const file = input?.files?.[0];
  if (!file) return;
  const sg = window._bmcRelSuggest || {};
  const eff = document.getElementById('bmcCrwEff')?.value || sg.effective_month || '';
  const pm = document.getElementById('bmcCrwPm')?.value || sg.price_month || '';
  if (!eff || !pm) { _bmcStatus('bmcRecStatus', 'กรุณาระบุเดือนที่มีผลและเดือนราคาในช่องข้างปุ่มก่อนเลือกไฟล์', 'var(--amber-ink)'); input.value = ''; return; }
  const send = dry => { const fd = new FormData(); fd.append('file', file, file.name);
    return _bmcJson(`${API}/api/bom-center/recipes/import-cost-rw?dry_run=${dry}&effective_month=${eff}&price_month=${pm}`, { method: 'POST', body: fd }); };
  try {
    _bmcStatus('bmcRecStatus', 'กำลังอ่านไฟล์ Cost-RW… (ไฟล์มีขนาดใหญ่ อาจใช้เวลาสักครู่)');
    const pre = await send(true);
    document.getElementById('bmcRecReport').innerHTML = _bmcCostRwReport(pre, false);
    if (!await uiConfirm(`ยืนยันการนำเข้าสูตรบุฟเฟ่ต์ ${pre.matched} เมนู เข้า “${pre.label}”\nยังไม่จับคู่ ${pre.unmatched.length} รายการ (จับคู่ต่อได้บนหน้าจอ)\nสูตรเดิมในฉบับร่างนี้ของเมนูที่อยู่ในไฟล์จะถูกแทนที่`)) {
      _bmcStatus('bmcRecStatus', 'ยังไม่บันทึก กรุณาตรวจสอบผลด้านบน'); return;
    }
    const d = await send(false);
    document.getElementById('bmcRecReport').innerHTML = _bmcCostRwReport(d, true);
    _bmcStatus('bmcRecStatus', `✓ นำเข้า ${d.matched} เมนู`, 'var(--good-ink)');
    await bmcLoadReleases(d.release_id);
  } catch (e) { _bmcStatus('bmcRecStatus', 'นำเข้าไม่สำเร็จ:' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
  finally { input.value = ''; }
}
function bmcExportRecipes() {
  if (!_bmcRel) return;
  window.open(`${API}/api/bom-center/recipes-export.xlsx?release_id=${_bmcRel.release_id}`, '_blank');
}
async function bmcImportFlat(input) {
  const file = input?.files?.[0];
  if (!file || !_bmcRel) { if (input) input.value = ''; return; }
  const send = dry => { const fd = new FormData(); fd.append('file', file, file.name);
    return _bmcJson(`${API}/api/bom-center/recipes/import-flat?release_id=${_bmcRel.release_id}&dry_run=${dry}`, { method: 'POST', body: fd }); };
  try {
    const pre = await send(true);
    if (!await uiConfirm(`ยืนยันการแทนที่สูตร ${pre.recipes} เมนู (${pre.lines} บรรทัด) ใน “${_bmcRel.label}”` + (pre.errors.length ? `\nข้าม ${pre.errors.length} แถว: ` + pre.errors.slice(0, 4).map(e => e.why).join(', ') : ''))) return;
    const d = await send(false);
    _bmcStatus('bmcRecStatus', `✓ แทนที่สูตร ${d.recipes} เมนู`, 'var(--good-ink)');
    bmcLoadRecipes();
  } catch (e) { _bmcStatus('bmcRecStatus', 'นำเข้าไม่สำเร็จ:' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
  finally { input.value = ''; }
}

// ── review & release (Sprint S3) ────────────────────────────────────────────
// The analyst runs the checks and releases (bom.release); management reads the
// comparison and acknowledges, or rolls back (bom.audit). Releasing does not yet
// change report COGS — that is the S4 cut-over; the page says so.
let _bmcRlRel = null;
const _BMC_STATUS_TH = { draft: 'ฉบับร่าง', review: 'รอตรวจ', released: 'ปล่อยแล้ว', retired: 'เลิกใช้/ย้อนกลับ' };

async function bmcLoadReleasePane(keepId) {
  try {
    const d = await _bmcJson(`${API}/api/bom-center/releases`);
    _bmcRels = d.releases || [];
    window._bmcRelSuggest = d.suggest || {};
    const sel = document.getElementById('bmcRlSel');
    const monthly = _bmcRels.filter(r => r.kind === 'monthly');
    const cur = keepId || Number(sel.value) || (monthly[0] || {}).release_id;
    sel.innerHTML = monthly.map(r => `<option value="${r.release_id}" ${r.release_id === cur ? 'selected' : ''}>${esc(r.label)} · ${_BMC_STATUS_TH[r.status] || r.status} · มีผล ${esc(r.effective_month)}</option>`).join('')
      || '<option value="">ยังไม่มีเวอร์ชันรายเดือน กรุณานำเข้าสูตรที่ สูตรตาม Tier</option>';
    await bmcLoadRelease();
  } catch (e) { _bmcStatus('bmcRlStatus', 'โหลดข้อมูลไม่สำเร็จ:' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcLoadRelease() {
  const id = Number(document.getElementById('bmcRlSel').value);
  _bmcRlRel = _bmcRels.find(r => r.release_id === id) || null;
  ['bmcRlHead', 'bmcRlChecks', 'bmcRlCompare', 'bmcRlHistory'].forEach(k => { document.getElementById(k).innerHTML = ''; });
  if (!_bmcRlRel) return;
  bmcRenderRelHead();
  await Promise.all([bmcLoadChecks(), bmcLoadCompare(), bmcLoadRelHistory()]);
}
function bmcRenderRelHead(checks) {
  const r = _bmcRlRel;
  const can = m => hasModule(m) || hasModule('bom.edit');
  const btn = (label, fn, primary, title) => `<button onclick="${fn}" title="${esc(title || '')}" style="font-size:12.5px;${primary
    ? 'background:var(--accent);color:var(--on-accent);border:0' : 'background:var(--bg-input);color:var(--text-primary);border:1px solid var(--border-control)'};border-radius:var(--r-sm);padding:5px 14px;cursor:pointer">${label}</button>`;
  const acts = [];
  if (['draft', 'review'].includes(r.status)) {
    if (can('bom.center') || can('bom.release')) acts.push(btn('บันทึกผลตรวจ', 'bmcRunChecks()', false, 'ผลตรวจคำนวณใหม่ทุกครั้งที่เปิดหน้า ปุ่มนี้บันทึกผลชุดนี้ไว้ในระบบ (หน้างานอ่านสถานะจากที่บันทึก)'));
    if (can('bom.release')) acts.push(btn('ปล่อยเวอร์ชันนี้', 'bmcDoRelease()', !!(checks && checks.can_release),
      checks && !checks.can_release ? 'ยังมีข้อที่ต้องแก้ไข' : 'analyst ปล่อยสูตร และ management รับทราบภายหลัง'));
  }
  if (r.status === 'released') {
    if (!r.acknowledged_by && (hasModule('bom.audit'))) acts.push(btn('รับทราบ (management)', 'bmcAck()', true));
    if (hasModule('bom.audit') || hasModule('bom.release')) acts.push(btn('ย้อนกลับเวอร์ชันนี้', 'bmcRetire()', false, 'เวอร์ชันก่อนหน้ากลับมามีผลแทน'));
    if (can('bom.center')) acts.push(btn('สร้างฉบับร่างเดือนถัดไปจากเวอร์ชันนี้', 'bmcNewDraft()', false));
  }
  const stInk = { draft: 'var(--amber-ink)', review: 'var(--amber-ink)', released: 'var(--good-ink)', retired: 'var(--text-muted)' }[r.status];
  document.getElementById('bmcRlHead').innerHTML = `
    <div class="flex flex-wrap items-start gap-3">
      <div><div class="text-base font-bold" style="color:var(--text-primary)">${esc(r.label)} ${_bmcChip(_BMC_STATUS_TH[r.status] || r.status, stInk)}</div>
        <div class="text-xs mt-1" style="color:var(--text-secondary)">มีผลเดือน <b>${esc(r.effective_month)}</b> · คิดต้นทุนด้วยราคาเดือน <b>${esc(r.price_month || '-')}</b> ·
          ${r.recipes} สูตร${r.unmatched ? ` · ยังไม่จับคู่ ${r.unmatched}` : ''}</div>
        <div class="text-xs mt-1" style="color:var(--text-secondary)">สร้างโดย ${esc(r.created_by || '-')}
          ${r.released_by ? ` · ปล่อยโดย <b>${esc(r.released_by)}</b> ${esc(String(r.released_at || '').slice(0, 16).replace('T', ' '))}` : ''}
          ${r.status === 'released' ? (r.acknowledged_by ? ` · <span style="color:var(--good-ink)">management รับทราบแล้ว: ${esc(r.acknowledged_by)} ${esc(String(r.acknowledged_at || '').slice(0, 16).replace('T', ' '))}</span>`
            : ' · <span style="color:var(--amber-ink)">รอ management รับทราบ</span>') : ''}</div></div>
      <div class="ml-auto flex flex-wrap gap-2">${acts.join('')}</div>
    </div>
    ${r.status === 'released' && !r.acknowledged_by ? `<div class="text-xs mt-3 p-2" style="border-left:3px solid var(--accent);background:var(--bg-subtle);color:var(--text-primary)">ขั้นตอนถัดไป: ผู้บริหาร (management) อ่านผลกระทบด้านล่าง แล้วกด “รับทราบ” — เปิดได้จากหน้างาน</div>` : ''}
    <div class="text-[11px] mt-3 p-2" style="background:var(--bg-subtle);border-radius:var(--r-sm);color:var(--text-muted)">
      ลำดับงาน: (1) ผลตรวจ 12 ข้อแสดงทันทีเมื่อเปิดหน้า (2) แก้ไขข้อที่ต้องแก้ได้ในหน้านี้ (“เปิดสูตร”) หรือยืนยันข้อที่ยอมรับได้ทีละรายการหรือทั้งกลุ่มพร้อมเหตุผล (3) ปล่อยสูตรภายในวันที่ 25 ของเดือนก่อนเดือนที่มีผล (4) management อ่านผลกระทบด้านล่าง แล้วรับทราบหรือย้อนกลับ ·
      เมื่อปล่อยแล้ว ต้นทุนในรายงานตั้งแต่เดือนที่มีผลจะเปลี่ยนตามเวอร์ชันนี้ทันที (เดือนก่อน ก.ย. 2569 ใช้ต้นทุนระบบเดิมเสมอ)</div>`;
}
async function bmcLoadChecks() {
  try {
    // a draft: the checks are computed afresh on open — nothing is written, no token is asked
    const fresh = ['draft', 'review'].includes(_bmcRlRel.status);
    const d = await _bmcJson(`${API}/api/bom-center/release-checks?release_id=${_bmcRlRel.release_id}${fresh ? '&recompute=true' : ''}`);
    bmcRenderChecks(d);
  } catch (e) { document.getElementById('bmcRlChecks').innerHTML = `<span style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function bmcRenderChecks(d) {
  bmcRenderRelHead(d);
  const box = document.getElementById('bmcRlChecks');
  if (!d.rows.length) {
    box.innerHTML = `<div class="text-sm font-semibold" style="color:var(--text-primary)">รายการตรวจ 12 ข้อ</div>
      <div class="text-xs mt-1" style="color:var(--text-muted)">${['draft', 'review'].includes(_bmcRlRel.status) ? 'ไม่พบข้อที่ต้องตรวจ' : 'ไม่มีข้อค้างตรวจ'}</div>`;
    return;
  }
  const canAccept = (hasModule('bom.release') || hasModule('bom.edit')) && ['draft', 'review'].includes(_bmcRlRel.status);
  window._bmcChecks = d;
  // one group per check code, open ones first; an ACCEPTABLE group takes one reason for all its open rows
  const groups = {};
  d.rows.forEach((r, i) => { (groups[r.check_code] = groups[r.check_code] || { code: r.check_code, title: r.title, severity: r.severity, acceptable: r.acceptable, rows: [] }).rows.push({ r, i }); });
  const order = d.by_code.map(k => k.code).filter(c => groups[c]);
  const btnS = 'font-size:11px;background:none;color:var(--accent);border:1px solid var(--border-control);border-radius:4px;padding:1px 8px;cursor:pointer';
  const html = order.map(c => {
    const g = groups[c], open = g.rows.filter(x => !x.r.resolved_by).length;
    const ink = g.severity === 'block' && open ? 'var(--bad-ink)' : g.severity === 'block' ? 'var(--good-ink)' : 'var(--amber-ink)';
    return `<details ${open ? 'open' : ''} class="mb-1"><summary class="text-xs cursor-pointer py-1" style="color:var(--text-primary)">${_bmcChip(g.code, ink)} <b>${esc(g.title)}</b> · ค้าง ${open} จาก ${g.rows.length}
        ${g.acceptable && open && canAccept ? `<button onclick="event.preventDefault();bmcAcceptGroup('${esc(g.code)}')" style="${btnS};margin-left:8px">ยืนยันทั้งกลุ่มพร้อมเหตุผล (${open})</button>` : ''}</summary>
      <div class="overflow-x-auto" style="max-height:300px;overflow-y:auto"><table class="text-xs w-full"><tbody>${g.rows.slice(0, 200).map(({ r, i }) => `<tr class="border-b" style="border-color:var(--border-soft)">
        <td class="pr-2 py-1">${esc(r.message || '')}${r.resolved_by ? `<div class="text-[10.5px]" style="color:var(--good-ink)">ยืนยันโดย ${esc(r.resolved_by)}</div>` : ''}</td>
        <td class="whitespace-nowrap">${r.acceptable && !r.resolved_by && canAccept ? `<button onclick="bmcAccept(${i})" style="${btnS}">ยืนยันพร้อมเหตุผล</button>` : ''}
          ${/^OTH/.test(r.ref) ? `<button onclick="bmcOpenRecipe('${esc(r.ref.split('#')[0])}', {rel: _bmcRlRel, host: 'bmcRlEditor'})" style="font-size:11px;background:none;color:var(--accent);border:0;cursor:pointer">เปิดสูตร</button>` : ''}</td></tr>`).join('')}</tbody></table>
      ${g.rows.length > 200 ? `<div class="text-[11px] mt-1" style="color:var(--text-muted)">แสดง 200 จาก ${g.rows.length}</div>` : ''}</div></details>`;
  }).join('');
  box.innerHTML = `<div class="flex flex-wrap items-center gap-2 mb-2"><div class="text-sm font-semibold" style="color:var(--text-primary)">รายการตรวจ</div>
      ${d.fresh ? _bmcChip('คำนวณใหม่เมื่อเปิดหน้า', 'var(--text-muted)') : ''}
      ${d.can_release ? _bmcChip('ไม่มีข้อที่ต้องแก้ไข พร้อมปล่อย', 'var(--good-ink)') : _bmcChip(`ต้องแก้ไข ${d.open_blocks} ข้อก่อนปล่อย`, 'var(--bad-ink)')}
      ${_bmcChip(`ข้อสังเกต ${d.warnings}`, 'var(--amber-ink)')}</div>${html}`;
}
async function bmcAcceptGroup(code) {
  const d = window._bmcChecks;
  if (!d) return;
  const refs = d.rows.filter(r => r.check_code === code && !r.resolved_by && r.acceptable).map(r => r.ref);
  if (!refs.length) return;
  const reason = await uiPrompt(`ยืนยันข้อ ${code} ทั้งกลุ่ม ${refs.length} รายการ\n\nกรุณาระบุเหตุผล (จำเป็น) เหตุผลเดียวใช้กับทุกรายการในกลุ่ม`);
  if (!reason) return;
  try {
    const out = await _bmcJson(`${API}/api/bom-center/release-act/accept-many`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ release_id: _bmcRlRel.release_id, check_code: code, refs, reason }) });
    bmcRenderChecks(out);
    _bmcStatus('bmcRlStatus', `✓ ยืนยันข้อ ${code} แล้ว ${refs.length} รายการ`, 'var(--good-ink)');
  } catch (e) { _bmcStatus('bmcRlStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcRunChecks() {
  _bmcStatus('bmcRlStatus', 'กำลังตรวจ…');
  try {
    const d = await _bmcJson(`${API}/api/bom-center/release-act/check?release_id=${_bmcRlRel.release_id}`, { method: 'POST' });
    bmcRenderChecks(d);
    _bmcStatus('bmcRlStatus', d.can_release ? '✓ ตรวจแล้ว พร้อมปล่อย' : `ตรวจแล้ว ต้องแก้ไข ${d.open_blocks} ข้อ`, d.can_release ? 'var(--good-ink)' : 'var(--amber-ink)');
    bmcLoadCompare();
  } catch (e) { _bmcStatus('bmcRlStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcAccept(i) {
  const r = window._bmcChecks.rows[i];
  const reason = await uiPrompt(`ยืนยันข้อ ${r.check_code}\n${r.message}\n\nกรุณาระบุเหตุผล (จำเป็น) เช่น ปรับสูตรตามที่ครัวแจ้ง`);
  if (!reason) return;
  try {
    const d = await _bmcJson(`${API}/api/bom-center/release-act/accept`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ release_id: _bmcRlRel.release_id, check_code: r.check_code, ref: r.ref, reason }) });
    bmcRenderChecks(d);
  } catch (e) { _bmcStatus('bmcRlStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcDoRelease() {
  const r = _bmcRlRel;
  const now = new Date(), cur = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  let backdate_reason = null;
  if (r.effective_month <= cur) {
    // the month has started: releasing RE-COSTS sales already made (from ${r.effective_month} on)
    backdate_reason = await uiPrompt(`เวอร์ชันนี้มีผลเดือน ${r.effective_month} ซึ่งเริ่มแล้ว\nการปล่อยจะคำนวณต้นทุนยอดขายตั้งแต่ ${r.effective_month} ใหม่ทั้งหมด`
      + ` (เดือนก่อน ก.ย. 2569 จะไม่เปลี่ยน)\n\nกรุณาระบุเหตุผลของการมีผลย้อนหลัง (จำเป็น):`);
    if (!backdate_reason) return;
  } else if (!await uiConfirm(`ยืนยันการปล่อย “${r.label}” มีผลเดือน ${r.effective_month}\n\nหลังปล่อยแล้วจะแก้ไขสูตรในเวอร์ชันนี้ไม่ได้ (ต้องสร้างฉบับร่างใหม่) · management จะเห็นผลกระทบและรับทราบ`)) return;
  try {
    const d = await _bmcJson(`${API}/api/bom-center/release-act/release?release_id=${r.release_id}`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ backdate_reason }) });
    _bmcStatus('bmcRlStatus', '✓ ปล่อยแล้ว' + (d.late ? ' · ' + d.late : '') + (d.warnings ? ` · ข้อสังเกต ${d.warnings}` : ''), d.late ? 'var(--amber-ink)' : 'var(--good-ink)');
    bmcLoadReleasePane(r.release_id);
  } catch (e) { _bmcStatus('bmcRlStatus', 'ปล่อยสูตรไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcAck() {
  const note = await uiPrompt(`รับทราบ “${_bmcRlRel.label}”\nหมายเหตุ (ไม่บังคับ)`, '');
  if (note === null) return;
  try {
    await _bmcJson(`${API}/api/bom-center/release-act/acknowledge`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ release_id: _bmcRlRel.release_id, note }) });
    _bmcStatus('bmcRlStatus', '✓ รับทราบแล้ว', 'var(--good-ink)');
    bmcLoadReleasePane(_bmcRlRel.release_id);
  } catch (e) { _bmcStatus('bmcRlStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcRetire() {
  const reason = await uiPrompt(`ยืนยันการย้อนกลับ “${_bmcRlRel.label}” เวอร์ชันก่อนหน้าจะกลับมามีผลแทน\nกรุณาระบุเหตุผล (จำเป็น)`);
  if (!reason) return;
  try {
    await _bmcJson(`${API}/api/bom-center/release-act/retire`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ release_id: _bmcRlRel.release_id, note: reason }) });
    _bmcStatus('bmcRlStatus', '✓ ย้อนกลับแล้ว', 'var(--good-ink)');
    bmcLoadReleasePane(_bmcRlRel.release_id);
  } catch (e) { _bmcStatus('bmcRlStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcNewDraft() {
  const sg = window._bmcRelSuggest || {};
  const eff = await uiPrompt('กรุณาระบุเดือนที่ฉบับร่างใหม่มีผล (YYYY-MM)', sg.effective_month || '');
  if (!eff) return;
  const pm = await uiPrompt('กรุณาระบุเดือนของราคาที่ใช้คิดต้นทุน (YYYY-MM)', sg.price_month || '');
  if (!pm) return;
  try {
    const d = await _bmcJson(`${API}/api/bom-center/release-act/new-draft`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from_release_id: _bmcRlRel.release_id, effective_month: eff, price_month: pm }) });
    _bmcStatus('bmcRlStatus', `✓ สร้าง ${d.label}`, 'var(--good-ink)');
    bmcLoadReleasePane(d.release_id);
  } catch (e) { _bmcStatus('bmcRlStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcLoadCompare() {
  const box = document.getElementById('bmcRlCompare');
  box.innerHTML = '<div class="text-xs" style="color:var(--text-muted)">กำลังคำนวณผลกระทบ…</div>';
  try {
    const d = await _bmcJson(`${API}/api/bom-center/release-compare?release_id=${_bmcRlRel.release_id}`);
    const sgn = v => (v > 0 ? '+' : '') + _bmcNum(v, 0);
    const ink = v => v > 0.5 ? 'var(--bad-ink)' : v < -0.5 ? 'var(--good-ink)' : 'var(--text-muted)';
    box.innerHTML = `<div class="text-sm font-semibold" style="color:var(--text-primary)">ผลกระทบหากใช้เวอร์ชันนี้ (เทียบกับยอดขายเดือน ${esc(d.month)})</div>
      <div class="text-xs mb-3" style="color:var(--text-secondary)">ต้นทุนเปลี่ยน ${d.changed} เมนู · COGS เปลี่ยนรวม <b style="color:${ink(d.total_delta)}">฿${sgn(d.total_delta)}</b> ต่อเดือน
        (เทียบกับต้นทุนที่ใช้อยู่ตอนนี้ของแต่ละเมนู)</div>
      ${d.tiers.length ? `<div class="text-xs font-semibold mb-1" style="color:var(--text-primary)">Food cost % ตาม Tier ของลูกค้า (คำนวณจากเมนูที่ลูกค้าแต่ละ Tier สั่งจริงต่อบิล)</div>
      <div class="overflow-x-auto mb-3"><table class="text-xs w-full" style="font-variant-numeric:tabular-nums"><thead><tr class="text-left border-b" style="color:var(--text-muted)">
        <th class="pr-2">Tier</th><th class="pr-2 text-right">ลูกค้า</th><th class="pr-2 text-right">รายได้</th><th class="pr-2 text-right">COGS เดิม</th><th class="pr-2 text-right">COGS ใหม่</th>
        <th class="pr-2 text-right">% เดิม</th><th class="pr-2 text-right">% ใหม่</th><th class="pr-2 text-right">ต้นทุน/หัว เดิม → ใหม่</th></tr></thead>
        <tbody>${d.tiers.map(t => `<tr class="border-b" style="border-color:var(--border-soft)"><td class="pr-2">${/^\d+$/.test(t.tier) ? '฿' + Number(t.tier).toLocaleString() : esc(t.tier)}</td>
          <td class="pr-2 text-right">${_bmcNum(t.pax, 0)}</td><td class="pr-2 text-right">${_bmcNum(t.rev, 0)}</td><td class="pr-2 text-right">${_bmcNum(t.cogs_old, 0)}</td>
          <td class="pr-2 text-right">${_bmcNum(t.cogs_new, 0)}</td><td class="pr-2 text-right">${t.food_pct_old}%</td>
          <td class="pr-2 text-right font-semibold" style="color:${ink(t.food_pct_new - t.food_pct_old)}">${t.food_pct_new}%</td>
          <td class="pr-2 text-right">${_bmcNum(t.head_old)} → ${_bmcNum(t.head_new)}</td></tr>`).join('')}</tbody></table></div>` : ''}
      <div class="grid gap-4" style="grid-template-columns:repeat(auto-fit,minmax(300px,1fr))">
        <div style="min-width:0"><div class="text-xs font-semibold mb-1" style="color:var(--text-primary)">รายสาขา</div>
          <div class="overflow-x-auto"><table class="text-xs w-full" style="font-variant-numeric:tabular-nums"><tbody>${d.branches.map(b => `<tr class="border-b" style="border-color:var(--border-soft)">
            <td class="pr-2">${esc(b.branch)}</td><td class="pr-2 text-right">${_bmcNum(b.plates, 0)} จาน</td><td class="text-right" style="color:${ink(b.delta)}">฿${sgn(b.delta)}</td></tr>`).join('')}</tbody></table></div></div>
        <div style="min-width:0"><div class="text-xs font-semibold mb-1" style="color:var(--text-primary)">เมนูที่กระทบมากสุด</div>
          <div class="overflow-x-auto"><table class="text-xs w-full" style="font-variant-numeric:tabular-nums"><tbody>${d.dishes.slice(0, 25).map(x => `<tr class="border-b" style="border-color:var(--border-soft)">
            <td class="pr-2">${esc(x.name || x.sku_code)}</td><td class="pr-2 text-right whitespace-nowrap">${_bmcNum(x.old)} → ${_bmcNum(x.new)}</td>
            <td class="pr-2 text-right">${x.pct == null ? '' : (x.pct > 0 ? '+' : '') + x.pct + '%'}</td><td class="text-right" style="color:${ink(x.impact)}">฿${sgn(x.impact)}</td></tr>`).join('')}</tbody></table></div></div>
      </div>`;
  } catch (e) { box.innerHTML = `<span style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
async function bmcLoadRelHistory() {
  try {
    const d = await _bmcJson(`${API}/api/bom-center/release-history?release_id=${_bmcRlRel.release_id}`);
    const th = { import_cost_rw: 'นำเข้า Cost-RW', edit_recipe: 'แก้สูตร', accept_check: 'ยืนยันข้อตรวจ', release: 'ปล่อย', acknowledge: 'รับทราบ',
      retire: 'ย้อนกลับ', new_draft: 'สร้างฉบับร่าง' };
    document.getElementById('bmcRlHistory').innerHTML = `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">ประวัติ</div>` +
      (d.rows.map(h => `<div class="text-[11.5px] py-0.5 border-b" style="border-color:var(--border-soft);color:var(--text-secondary)">
        ${esc(String(h.at).slice(0, 16).replace('T', ' '))} · <b>${esc(h.who || '-')}</b> · ${esc(th[h.action] || h.action)}
        ${h.key && h.key !== String(_bmcRlRel.release_id) ? ' · ' + esc(String(h.key).split('|').slice(1).join(' ')) : ''}</div>`).join('')
        || '<div class="text-xs" style="color:var(--text-muted)">ยังไม่มีประวัติ</div>');
  } catch (e) { /* history is a nicety */ }
}

// ── forecast: suggested order per round (Sprint S4b) ────────────────────────
// Sales Support (supply.ordering) runs it and fills the order calendar; the
// numbers are expected use + safety stock, NOT yet net of what is in the fridge
// (branches start counting stock in Phase 2) — the page says so every time.
let _bmcFc = null;
let _bmcCal = null;
const _BMC_WD = ['อาทิตย์', 'จันทร์', 'อังคาร', 'พุธ', 'พฤหัส', 'ศุกร์', 'เสาร์'];
function _bmcFcCanWrite() { return hasModule('supply.ordering') || hasModule('bom.center') || hasModule('bom.edit'); }

async function bmcLoadForecastPane() {
  document.querySelectorAll('#tab-bomcenter .bmc-fc-write').forEach(el => { el.style.display = _bmcFcCanWrite() ? '' : 'none'; });
  const day = document.getElementById('bmcFcDay');
  if (!day.value) day.value = new Date().toISOString().slice(0, 10);
  await bmcLoadCalendar();
  const sel = document.getElementById('bmcFcBranch');
  if (!sel.options.length && _bmcCal) {
    sel.innerHTML = _bmcCal.rows.map(r => `<option value="${r.branch_id}">${esc(r.branch)}</option>`).join('');
    const ctx = supplyCtx().branch_id;   // the module's shared branch, when this list has it
    if (ctx && _bmcCal.rows.some(r => r.branch_id === ctx)) sel.value = String(ctx);
  }
  bmcLoadRuns();
}
async function bmcRunForecast() {
  const bid = document.getElementById('bmcFcBranch').value;
  if (!bid) return;
  const rnd = document.getElementById('bmcFcRound').value, day = document.getElementById('bmcFcDay').value;
  const season = document.getElementById('bmcFcSeason').checked ? 'auto' : 'off';
  _bmcStatus('bmcFcStatus', 'กำลังคำนวณ…');
  try {
    _bmcFc = await _bmcJson(`${API}/api/bom-center/forecast?branch_id=${bid}&round=${rnd}&order_day=${day}&season=${season}`);
    bmcRenderForecast();
    _bmcStatus('bmcFcStatus', '');
  } catch (e) { _bmcStatus('bmcFcStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
function bmcRenderForecast() {
  const f = _bmcFc;
  const kInk = { A: 'var(--bad-ink)', B: 'var(--amber-ink)', C: 'var(--text-muted)' };
  const fmtQ = (v, u) => v == null ? '—' : (u === 'กรัม' && v >= 1000 ? _bmcNum(v / 1000, 2) + ' กก.' : u === 'มิลลิลิตร' && v >= 1000 ? _bmcNum(v / 1000, 2) + ' ลิตร' : _bmcNum(v, 1) + ' ' + (u || ''));
  document.getElementById('bmcFcHead').innerHTML = `<div class="card p-4">
    <div class="flex flex-wrap gap-4 items-baseline">
      <div class="text-sm font-semibold" style="color:var(--text-primary)">${esc(f.branch)} · ${f.round === 'salmon' ? 'แซลมอนสด' : 'รอบ ' + f.round}</div>
      <div class="text-xs" style="color:var(--text-secondary)">สั่ง <b>${f.order_weekday} ${esc(f.order_day)}</b> · สินค้าถึง <b>${esc(f.delivery)}</b> · ต้องเพียงพอสำหรับ <b>${esc(f.cover_from)} ถึง ${esc(f.cover_to)}</b> (${f.cover_days} วัน)</div>
      <div class="ml-auto text-right"><div class="text-[11px]" style="color:var(--text-muted)">มูลค่าแนะนำ</div><div class="text-lg font-bold" style="color:var(--text-primary)">฿${_bmcNum(f.value_total, 0)}</div></div>
    </div>
    <div class="text-[11px] mt-2" style="color:var(--text-muted)">ใช้ยอดขาย ${esc(f.history_from)} ถึง ${esc(f.history_to)} (${f.trading_days} วันที่มีการขาย · เฉพาะช่วงเมนูใหม่) ·
      สูตร: ${f.stats.release_skus ? `เวอร์ชันที่ปล่อยแล้ว (${f.stats.release_skus} เมนู) + ` : ''}สูตรระบบเดิมสำหรับเมนูที่เหลือ ·
      เทศกาล ×${(f.season?.factor || 1).toFixed(2)}${f.season?.reason ? ' (' + esc(f.season.reason) + ')' : ''}
      ${f.calendar_filled ? '' : ' · <b style="color:var(--amber-ink)">ยังไม่ได้ตั้งรอบสั่งของสาขานี้ ระบบใช้ค่าเริ่มต้น</b> <a href="#" onclick="supplyGo({tab:\'bomcenter\',pane:\'cal\'});return false" style="color:var(--amber-ink);text-decoration:underline">ตั้งค่ารอบสั่ง →</a>'}</div>
    <div class="text-[11px] mt-1 p-2" style="background:var(--bg-subtle);border-radius:var(--r-sm);color:var(--amber-ink)">
      ยอดแนะนำการสั่ง = ปริมาณที่คาดว่าจะใช้ + สต๊อกสำรองกันขาด <b>ยังไม่หักสต๊อกคงเหลือในสาขา</b> (สาขาเริ่มตรวจนับสต๊อกใน Phase 2) · ใช้เป็นตัวเลขตั้งต้น ไม่ใช่ใบสั่งซื้อที่พร้อมใช้</div>
    ${f.stats.missed_lines ? `<details class="mt-2"><summary class="text-[11px] cursor-pointer" style="color:var(--amber-ink)">มี ${f.stats.missed_lines} บรรทัดสูตรในเมนูที่สาขานี้ขายที่ยังคำนวณไม่ได้ (แปลงหน่วยไม่ได้) <a href="#" onclick="event.stopPropagation();supplyGo({tab:'bomcenter',pane:'conv'});return false" style="color:var(--amber-ink);text-decoration:underline">แก้ไขที่ หน่วยและการแปลง →</a></summary>
      <div class="text-[11px] mt-1" style="color:var(--text-secondary)">${f.stats.missed.map(m => `${esc(m.code)} (${esc(m.unit)}) · ${_bmcNum(m.plates, 0)} จาน · ${esc(m.why)}`).join('<br>')}</div></details>` : ''}
  </div>`;
  const box = document.getElementById('bmcFcTable');
  box.style.display = '';
  box.innerHTML = `<table class="text-xs w-full" style="font-variant-numeric:tabular-nums"><thead><tr class="text-left border-b" style="color:var(--text-muted)">
      <th class="pr-2">รหัส</th><th class="pr-2">วัตถุดิบ</th><th class="pr-2">ชั้น</th><th class="pr-2 text-right">ใช้/วัน</th>
      <th class="pr-2 text-right">ต้องใช้ทั้งรอบ</th><th class="pr-2 text-right">กันขาด</th><th class="pr-2 text-right">แนะนำสั่ง</th>
      <th class="pr-2 text-right">มูลค่า</th><th class="pr-2 text-right">แนวโน้ม</th><th></th></tr></thead>
    <tbody>${f.rows.map(r => `<tr class="border-b" style="border-color:var(--border-soft)">
      <td class="pr-2 py-1 whitespace-nowrap">${esc(r.code)}</td><td class="pr-2">${esc(r.name || '')}</td>
      <td class="pr-2">${_bmcChip(r.class, kInk[r.class])}</td>
      <td class="pr-2 text-right">${fmtQ(r.per_day, r.base_unit)}</td><td class="pr-2 text-right">${fmtQ(r.forecast, r.base_unit)}</td>
      <td class="pr-2 text-right">${fmtQ(r.safety, r.base_unit)}</td>
      <td class="pr-2 text-right font-semibold">${r.suggested_units != null ? `${_bmcNum(r.suggested_units, 0)} ${esc(r.recv_unit || '')}` : fmtQ(r.need, r.base_unit) + ' ' + _bmcChip('ไม่มีขนาดบรรจุ', 'var(--amber-ink)')}</td>
      <td class="pr-2 text-right">${_bmcNum(r.value, 0)}</td>
      <td class="pr-2 text-right" style="color:${r.trend > 1.05 ? 'var(--amber-ink)' : r.trend < 0.95 ? 'var(--good-ink)' : 'var(--text-muted)'}">${r.trend > 1 ? '+' : ''}${Math.round((r.trend - 1) * 100)}%</td>
      <td>${r.monitored ? _bmcChip('บังคับสั่ง', 'var(--navy-ink)') : ''} ${r.sourcing === 'local' ? _bmcChip('สาขาจัดซื้อเอง', 'var(--text-muted)') : ''}</td></tr>`).join('')
      || '<tr><td colspan="10" class="py-3" style="color:var(--text-muted)">ไม่มีวัตถุดิบในรอบนี้</td></tr>'}</tbody></table>`;
}
async function bmcSaveForecast() {
  const bid = Number(document.getElementById('bmcFcBranch').value);
  const body = { branch_id: bid, round: document.getElementById('bmcFcRound').value, order_day: document.getElementById('bmcFcDay').value,
    season: document.getElementById('bmcFcSeason').checked ? 'auto' : 'off' };
  try {
    const d = await _bmcJson(`${API}/api/bom-center/forecast/run`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    _bmcStatus('bmcFcStatus', `✓ บันทึกรอบสั่ง #${d.run_id} · ${d.lines} รายการ · ฿${_bmcNum(d.value_total, 0)}`, 'var(--good-ink)');
    bmcLoadRuns();
  } catch (e) { _bmcStatus('bmcFcStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcBacktest() {
  const bid = document.getElementById('bmcFcBranch').value;
  _bmcStatus('bmcFcStatus', 'กำลังทดสอบความแม่นยำย้อนหลัง…');
  try {
    const b = await _bmcJson(`${API}/api/bom-center/forecast/backtest?branch_id=${bid}&weeks=3`);
    const c = b.mape_by_class || {};
    _bmcStatus('bmcFcStatus', b.available ? `ทดสอบย้อนหลัง 3 สัปดาห์ (ถึง ${b.as_of}) ความคลาดเคลื่อนรายสัปดาห์: A ${c.A ?? '—'}% · B ${c.B ?? '—'}% · C ${c.C ?? '—'}% (เป้า A ≤ ${b.target_a}%)` : 'ข้อมูลไม่พอ',
      c.A != null && c.A <= b.target_a ? 'var(--good-ink)' : 'var(--amber-ink)');
  } catch (e) { _bmcStatus('bmcFcStatus', 'ดำเนินการไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcLoadCalendar() {
  try { _bmcCal = await _bmcJson(`${API}/api/bom-center/order-calendar`); } catch (e) { return; }
  const w = _bmcFcCanWrite();
  const sel = (bid, rnd, field, val, opts) => w
    ? `<select data-bid="${bid}" data-rnd="${rnd}" data-f="${field}" onchange="this.style.background='var(--accent-soft)'" style="border:1px solid var(--border);border-radius:4px;font-size:11.5px;background:var(--bg-input);color:var(--text-primary)">
        ${opts.map(o => `<option value="${o}" ${Number(val) === o ? 'selected' : ''}>${_BMC_WD[o]}</option>`).join('')}</select>`
    : _BMC_WD[val];
  const lead = (bid, rnd, val) => w ? `<input data-bid="${bid}" data-rnd="${rnd}" data-f="lead_days" type="number" min="0" max="14" value="${val ?? ''}" onchange="this.style.background='var(--accent-soft)'"
        style="width:46px;border:1px solid var(--border);border-radius:4px;font-size:11.5px;text-align:right;background:var(--bg-input);color:var(--text-primary)">` : (val ?? '');
  document.getElementById('bmcFcCal').innerHTML = `<div class="flex items-center mb-2"><div class="text-sm font-semibold" style="color:var(--text-primary)">รอบสั่งสินค้าของแต่ละสาขา</div>
      ${w ? `<button onclick="bmcSaveCalendar()" class="ml-auto" style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:5px 14px;font-size:12.5px;cursor:pointer">บันทึกรอบสั่ง</button>` : ''}</div>
    <div class="text-[11px] mb-2" style="color:var(--text-muted)">ฝ่ายขายกรอกข้อมูลรายสาขา: รอบ 1 สั่งวันอังคารหรือพุธ · รอบ 2 สั่งวันศุกร์หรือเสาร์ · แซลมอนสดสั่งล่วงหน้าสัปดาห์ละครั้ง (วันส่งถึง = วันสั่ง + ระยะส่ง)</div>
    <div class="overflow-x-auto"><table class="text-xs w-full"><thead><tr class="text-left border-b" style="color:var(--text-muted)">
      <th class="pr-2">สาขา</th><th class="pr-2">รอบ 1</th><th class="pr-2">ระยะส่ง (วัน)</th><th class="pr-2">รอบ 2</th><th class="pr-2">ระยะส่ง</th>
      <th class="pr-2">แซลมอนสด</th><th class="pr-2">ระยะส่ง</th><th>ตั้งโดย</th></tr></thead>
    <tbody>${_bmcCal.rows.map(r => `<tr class="border-b" style="border-color:var(--border-soft)"><td class="pr-2 py-1">${esc(r.branch)}</td>
      <td class="pr-2">${sel(r.branch_id, '1', 'order_weekday', r.r1.order_weekday, [2, 3])}</td><td class="pr-2">${lead(r.branch_id, '1', r.r1.lead_days)}</td>
      <td class="pr-2">${sel(r.branch_id, '2', 'order_weekday', r.r2.order_weekday, [5, 6])}</td><td class="pr-2">${lead(r.branch_id, '2', r.r2.lead_days)}</td>
      <td class="pr-2">${sel(r.branch_id, 'salmon', 'order_weekday', r.rsalmon.order_weekday, [0, 1, 2, 3, 4, 5, 6])}</td><td class="pr-2">${lead(r.branch_id, 'salmon', r.rsalmon.lead_days)}</td>
      <td class="text-[10.5px]" style="color:var(--text-muted)">${[r.r1, r.r2, r.rsalmon].some(x => x.filled) ? esc([r.r1, r.r2, r.rsalmon].map(x => x.filled_by).filter(Boolean)[0] || '') : '<span style="color:var(--amber-ink)">ค่าเริ่มต้น</span>'}</td></tr>`).join('')}</tbody></table></div>`;
}
async function bmcSaveCalendar() {
  const rows = {};
  document.querySelectorAll('#bmcFcCal [data-bid]').forEach(el => {
    const k = el.dataset.bid + '|' + el.dataset.rnd;
    rows[k] = rows[k] || { branch_id: Number(el.dataset.bid), round: el.dataset.rnd };
    rows[k][el.dataset.f] = el.value === '' ? null : Number(el.value);
  });
  try {
    await _bmcJson(`${API}/api/bom-center/order-calendar`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.values(rows)) });
    _bmcStatus('bmcCalStatus', '✓ บันทึกรอบสั่งแล้ว', 'var(--good-ink)');
    _bmcStatus('bmcFcStatus', '✓ บันทึกรอบสั่งแล้ว', 'var(--good-ink)');
    bmcLoadCalendar();
  } catch (e) {
    _bmcStatus('bmcCalStatus', 'บันทึกไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)');
    _bmcStatus('bmcFcStatus', 'บันทึกไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)');
  }
}
async function bmcLoadRuns() {
  try {
    const d = await _bmcJson(`${API}/api/bom-center/forecast/runs?limit=20`);
    document.getElementById('bmcFcRuns').innerHTML = `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">รอบสั่งที่บันทึกไว้</div>` +
      (d.rows.map(r => `<div class="text-[11.5px] py-0.5 border-b" style="border-color:var(--border-soft);color:var(--text-secondary)">#${r.run_id} · ${esc(r.branch || '')} ·
        ${r.round === 'salmon' ? 'แซลมอน' : 'รอบ ' + r.round} · สั่ง ${esc(r.order_day)} · ใช้ ${esc(r.cover_from)}–${esc(r.cover_to)} · ${r.lines} รายการ · ฿${_bmcNum(r.value, 0)} ·
        ${esc(r.created_by || '-')} ${esc(String(r.created_at || '').slice(0, 16).replace('T', ' '))}</div>`).join('')
        || '<div class="text-xs" style="color:var(--text-muted)">ยังไม่มีรอบสั่งที่บันทึกไว้</div>');
  } catch (e) { /* list is a nicety */ }
}
