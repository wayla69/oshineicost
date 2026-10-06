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

const _BMC_KIND = { raw: 'ดิบ', prepared: 'กึ่งสำเร็จ', consumable: 'วัสดุสิ้นเปลือง', zero_by_design: 'ตั้งใจเป็น ฿0' };
const _BMC_SRC = { purchase_avg: 'ซื้อจริง', carry_forward: 'ยกมา', manual: 'แก้เอง', list_ref: 'ใบเสนอ', snapshot: 'สูตรเดิม' };
const _BMC_SRC_INK = { purchase_avg: 'var(--good-ink)', carry_forward: 'var(--amber-ink)', manual: 'var(--navy-ink)',
                       list_ref: 'var(--text-muted)', snapshot: 'var(--text-muted)' };

function _bmcMonth() {
  const el = document.getElementById('bmcMonth');
  if (el && !el.value) { const d = new Date(); el.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; }
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
}
function _bmcChip(text, ink) {
  return `<span style="display:inline-block;padding:1px 6px;border-radius:999px;border:1px solid var(--border);color:${ink};font-size:10.5px;white-space:nowrap">${esc(text)}</span>`;
}

function bmcSetSub(k) {
  _bmcSub = k;
  document.querySelectorAll('#tab-bomcenter [data-bmcsub]').forEach(b => b.classList.toggle('active', b.dataset.bmcsub === k));
  ['overview', 'ing', 'price', 'conv'].forEach(p => {
    const el = document.getElementById('bmc-pane-' + p);
    if (el) el.style.display = p === k ? '' : 'none';
  });
  if (k === 'overview') bmcLoadOverview();
  if (k === 'ing') bmcLoadIng();
  if (k === 'price') bmcLoadPrices();
  if (k === 'conv') bmcLoadConv();
}
function bmcMonthChanged() { _bmcIng = []; _bmcPrices = []; bmcLoadOverview(); }

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
      tile('ราคาซื้อจริงเดือนนี้', _bmcNum((s.price_sources || {}).purchase_avg || 0, 0), `ยกมา ${(s.price_sources || {}).carry_forward || 0} · ใบเสนอ ${(s.price_sources || {}).list_ref || 0}`),
      tile('ราคาเก่ากว่า 2 เดือน', _bmcNum(s.stale, 0), 'ควรมีการซื้อใหม่หรือยืนยันราคา', s.stale ? 'var(--amber-ink)' : 'var(--good-ink)'),
      tile('บังคับสั่ง (ใบคลัง)', _bmcNum(s.monitored, 0), 'ติดตามในแผนสั่ง'),
    ].join('');
    document.getElementById('bmcOverviewNote').textContent = s.last_import
      ? `นำเข้าราคาล่าสุด: เดือน ${s.last_import.month} โดย ${s.last_import.who} (${String(s.last_import.at).slice(0, 16).replace('T', ' ')})`
      : 'ยังไม่เคยนำเข้าราคาซื้อจริง';
    const setup = document.getElementById('bmcSetupCard');
    if (!s.ingredients) {
      setup.innerHTML = `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">เริ่มต้นใช้งาน</div>
        <p class="text-xs mb-2" style="color:var(--text-secondary)">ทะเบียนยังว่าง — กด “เติมจากข้อมูลในระบบ” เพื่อสร้างจากสูตรที่มีอยู่ ขนาดบรรจุ และใบคลังบังคับสั่ง
        จากนั้นนำเข้า sheet DATAP (ไฟล์ COST-RW) เพื่อเติมสูญเสีย % และผู้ขาย แล้วนำเข้าราคาซื้อจริงของเดือนล่าสุด</p>
        ${_bmcCanWrite() ? '<button onclick="bmcSeed()" style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:6px 14px;font-size:13px;cursor:pointer">เติมจากข้อมูลในระบบ</button>' : ''}`;
    } else {
      const steps = [
        [s.recipe_codes_missing === 0, `รหัสวัตถุดิบที่สูตรใช้อยู่ในทะเบียนครบ`, `สูตรใช้ ${s.recipe_codes_missing} รหัสที่ยังไม่อยู่ในทะเบียน — กด “เติมจากข้อมูลในระบบ”`],
        [s.no_base_unit === 0, 'ทุกวัตถุดิบมีหน่วยฐาน', `${s.no_base_unit} รายการยังไม่มีหน่วยฐาน`],
        [s.raw_without_pack === 0, 'วัตถุดิบดิบมีขนาดบรรจุครบ', `${s.raw_without_pack} รายการดิบยังไม่มีขนาดบรรจุ (แปลง แพ็ค/ถุง ไม่ได้)`],
        [(s.price_sources || {}).purchase_avg > 0, `มีราคาซื้อจริงของเดือน ${s.month}`, `ยังไม่มีราคาซื้อจริงของเดือน ${s.month} — นำเข้าที่แท็บ ราคารายเดือน`],
      ];
      setup.innerHTML = `<div class="text-sm font-semibold mb-2" style="color:var(--text-primary)">ความพร้อมของข้อมูล</div>` +
        steps.map(([ok, good, bad]) => `<div class="text-xs py-0.5" style="color:${ok ? 'var(--good-ink)' : 'var(--amber-ink)'}">${ok ? '✓' : '•'} ${esc(ok ? good : bad)}</div>`).join('');
    }
    const iss = document.getElementById('bmcIssuesCard');
    iss.innerHTML = s.recipe_codes_missing
      ? `<div class="text-xs font-semibold mb-1" style="color:var(--text-primary)">รหัสที่สูตรใช้แต่ไม่อยู่ในทะเบียน (${s.recipe_codes_missing})</div>
         <div class="text-[11px]" style="color:var(--text-muted)">${s.recipe_codes_missing_list.map(esc).join(' · ')}</div>`
      : '';
    iss.style.display = s.recipe_codes_missing ? '' : 'none';
  } catch (e) {
    document.getElementById('bmcKpis').innerHTML = `<div class="card p-3 text-xs" style="color:var(--bad-ink)">โหลดไม่สำเร็จ — ${esc(String(e.message || e))}</div>`;
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
  const rows = _bmcIng.filter(r => (!kind || r.kind === kind) && (!only || r.problems.length)
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
    </tr>`).join('') || '<tr><td colspan="12" class="py-3" style="color:var(--text-muted)">ไม่มีรายการ — ถ้าทะเบียนว่าง กด “เติมจากข้อมูลในระบบ”</td></tr>';
  const nProb = _bmcIng.filter(r => r.problems.length).length;
  if (!Object.keys(_bmcIngEdits).length) {
    _bmcStatus('bmcIngSummary', `${rows.length} จาก ${_bmcIng.length} รายการ · มีปัญหา ${nProb}` + (rows.length > shown.length ? ` · แสดง ${shown.length} แรก (ค้นหาเพื่อกรอง)` : ''));
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
    _bmcStatus('bmcIngSummary', 'บันทึกไม่สำเร็จ — ' + String(e.message || e).slice(0, 160), 'var(--bad-ink)');
  }
}
async function bmcSeed() {
  try {
    const pre = await _bmcJson(`${API}/api/bom-center/ingredients/seed?dry_run=true`, { method: 'POST' });
    const msg = `เติมทะเบียนจากข้อมูลในระบบ\n\nรหัสใหม่ ${pre.inserted} · เติมช่องว่าง ${pre.updated} · ไม่เปลี่ยน ${pre.unchanged}\n`
      + `ราคาใบเสนออ้างอิง ${pre.ref_prices} · รหัสคลังที่โยงกับรหัสสูตร ${pre.aliases}\n`
      + (pre.multi_unit_codes.length ? `รหัสที่สูตรใช้หลายหน่วย ${pre.multi_unit_codes.length} (ใช้หน่วยที่ใช้มากสุด — ตรวจที่แท็บแปลงหน่วย)\n` : '')
      + `\nไม่ทับค่าที่มีคนตั้งไว้ · บันทึกเลยไหม?`;
    if (!confirm(msg)) return;
    const d = await _bmcJson(`${API}/api/bom-center/ingredients/seed?dry_run=false`, { method: 'POST' });
    _bmcStatus('bmcIngStatus', `✓ ทะเบียน ${d.before} → ${d.after} รายการ`, 'var(--good-ink)');
    _bmcIng = [];
    bmcSetSub(_bmcSub === 'overview' ? 'overview' : 'ing');
  } catch (e) {
    _bmcStatus('bmcIngStatus', 'ไม่สำเร็จ — ' + String(e.message || e).slice(0, 160), 'var(--bad-ink)');
  }
}
async function bmcImportIng(input) {
  const file = input?.files?.[0];
  if (!file) return;
  const send = dry => { const fd = new FormData(); fd.append('file', file, file.name);
    return _bmcJson(`${API}/api/bom-center/ingredients/import?dry_run=${dry}`, { method: 'POST', body: fd }); };
  try {
    _bmcStatus('bmcIngStatus', 'กำลังอ่านไฟล์… (ไฟล์ COST-RW ใหญ่ ใช้เวลาสักครู่)');
    const pre = await send(true);
    document.getElementById('bmcIngReport').innerHTML = `<div class="card p-3 text-xs" style="color:var(--text-secondary)">
      sheet <b>${esc(pre.sheet)}</b> · อ่าน ${pre.rows} แถว · ใหม่ ${pre.inserted} · แก้ ${pre.updated} · เท่าเดิม ${pre.unchanged} · ราคาใบเสนอ ${pre.ref_prices}
      ${pre.skipped.length ? `<div class="mt-1" style="color:var(--amber-ink)">ข้าม ${pre.skipped.length} แถว: ${pre.skipped.slice(0, 8).map(s => `แถว ${s.row}${s.code ? ' ' + esc(s.code) : ''} (${esc(s.why)})`).join(' · ')}</div>` : ''}</div>`;
    if (!(pre.inserted + pre.updated + pre.ref_prices)) { _bmcStatus('bmcIngStatus', 'ไม่มีอะไรเปลี่ยน'); return; }
    if (!confirm(`นำเข้า ${file.name}: ใหม่ ${pre.inserted} · แก้ ${pre.updated} รายการ\nค่าในไฟล์จะทับค่าเดิม · บันทึกเลยไหม?`)) { _bmcStatus('bmcIngStatus', 'ยกเลิก'); return; }
    const d = await send(false);
    _bmcStatus('bmcIngStatus', `✓ นำเข้าแล้ว ใหม่ ${d.inserted} · แก้ ${d.updated}`, 'var(--good-ink)');
    bmcLoadIng(true);
  } catch (e) {
    _bmcStatus('bmcIngStatus', 'นำเข้าไม่สำเร็จ — ' + String(e.message || e).slice(0, 160), 'var(--bad-ink)');
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
    const rm = await _bmcJson(`${API}/api/bom-center/prices/requisition-months`).catch(() => ({ months: [] }));
    const btn = document.getElementById('bmcFromReqBtn');
    if (btn) {
      const has = (rm.months || []).includes(_bmcMonth());
      btn.disabled = !has;
      btn.style.opacity = has ? 1 : 0.5;
      btn.title = has ? `ใช้รายงานจ่ายสินค้าภายในเดือน ${_bmcMonth()} ที่อัปโหลดไว้ในระบบ`
        : `ยังไม่มีรายงานจ่ายสินค้าภายในเดือน ${_bmcMonth()} ในระบบ` + (rm.months?.length ? ` (มี: ${rm.months.join(', ')})` : '');
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
function _bmcSuggestedHtml(list) {
  _bmcLastSuggested = list || [];
  if (!_bmcLastSuggested.length || !_bmcCanWrite()) return '';
  return `<div class="mt-3 p-3" style="background:var(--bg-subtle);border-radius:var(--r-sm)">
    <div class="text-xs font-semibold" style="color:var(--text-primary)">กฎหน่วยที่ระบบเสนอ (${_bmcLastSuggested.length}) — ขนาดแพ็คในรายงานบัญชีไม่ตรงกับทะเบียน</div>
    <div class="text-[11px] mb-2" style="color:var(--text-muted)">คำนวณจากราคาล่าสุดของวัตถุดิบนั้น · ติ๊กไว้ให้เฉพาะที่ทุกบรรทัดให้ค่าตรงกัน · แก้ตัวเลขได้ก่อนบันทึก · บันทึกแล้วอัปโหลดไฟล์เดิมอีกครั้ง</div>
    <div class="overflow-x-auto"><table class="text-[11.5px] w-full" style="font-variant-numeric:tabular-nums"><tbody>
    ${_bmcLastSuggested.map((s, i) => `<tr class="border-b" style="border-color:var(--border-soft)">
      <td class="pr-2"><input type="checkbox" id="bmcSug${i}" ${s.consistent ? 'checked' : ''}></td>
      <td class="pr-2 whitespace-nowrap">${esc(s.code)}</td><td class="pr-2">${esc(s.name || '')}</td>
      <td class="pr-2 whitespace-nowrap">1 ${esc(s.unit)} =</td>
      <td class="pr-2"><input id="bmcSugF${i}" type="number" step="any" value="${s.factor}" style="width:90px;border:1px solid var(--border);border-radius:4px;padding:1px 4px;text-align:right;background:var(--bg-input);color:var(--text-primary)"> ${esc(s.base_unit || '')}</td>
      <td class="pr-2 text-right">${s.lines} บรรทัด · ฿${_bmcNum(s.value, 0)}</td>
      <td>${s.consistent ? '' : _bmcChip('ค่าไม่สม่ำเสมอ — ตรวจก่อน', 'var(--amber-ink)')}</td></tr>`).join('')}
    </tbody></table></div>
    <button onclick="bmcApplySuggested()" class="mt-2" style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:5px 14px;font-size:12px;cursor:pointer">บันทึกกฎที่ติ๊กไว้</button>
  </div>`;
}
async function bmcApplySuggested() {
  const rules = _bmcLastSuggested.map((s, i) => ({ s, on: document.getElementById('bmcSug' + i)?.checked,
    f: Number(document.getElementById('bmcSugF' + i)?.value) }))
    .filter(x => x.on && x.f > 0).map(x => ({ code: x.s.code, unit: x.s.unit, factor_to_base: x.f }));
  if (!rules.length) { _bmcStatus('bmcPriceStatus', 'ยังไม่ได้ติ๊กกฎ'); return; }
  try {
    await _bmcJson(`${API}/api/bom-center/units`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(rules) });
    _bmcStatus('bmcPriceStatus', `✓ บันทึกกฎหน่วย ${rules.length} รายการ — อัปโหลดไฟล์เดิมอีกครั้งเพื่อคำนวณราคาใหม่`, 'var(--good-ink)');
    _bmcIng = [];
  } catch (e) { _bmcStatus('bmcPriceStatus', 'ไม่สำเร็จ — ' + String(e.message || e), 'var(--bad-ink)'); }
}
function _bmcPreviewHtml(p, applied) {
  const flagInk = { flag: 'var(--bad-ink)', warn: 'var(--amber-ink)', new: 'var(--navy-ink)', ok: 'var(--text-muted)' };
  const flagTh = { flag: 'เปลี่ยน ≥25%', warn: 'เปลี่ยน ≥10%', new: 'ใหม่', ok: 'ปกติ' };
  const top = p.rows.filter(r => r.flag !== 'ok').concat(p.rows.filter(r => r.flag === 'ok')).slice(0, 60);
  return `<div class="card p-4">
    <div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">${applied ? '✓ บันทึกแล้ว' : 'ตรวจก่อนบันทึก'} — ราคาซื้อจริงเดือน ${esc(p.month)}</div>
    <div class="text-xs mb-2" style="color:var(--text-secondary)">${esc(p.source?.format || '')} · ${p.lines} บรรทัด · คิดราคาได้ ${p.priced} วัตถุดิบ (มูลค่า ฿${_bmcNum(p.value_total, 0)}) ·
      เปลี่ยน ≥25% <b style="color:var(--bad-ink)">${p.counts.flag}</b> · ≥10% <b style="color:var(--amber-ink)">${p.counts.warn}</b> · ใหม่ ${p.counts.new} ·
      ยกราคาเดิมมา ${p.carry_forward} · ยังไม่เคยมีราคา ${p.never_priced}
      ${p.added_codes?.length ? ` · <b style="color:var(--navy-ink)">${applied ? 'เพิ่ม' : 'จะเพิ่ม'}รหัสใหม่ในทะเบียน ${p.added_codes.length}</b>` : ''}
      ${p.unknown_codes?.length ? ` · <b style="color:var(--amber-ink)">รหัสที่ไม่อยู่ในทะเบียน ${p.unknown_codes.length} (฿${_bmcNum(p.unknown_value, 0)}) — ติ๊ก “เพิ่มรหัสที่ยังไม่มีในทะเบียน”</b>` : ''}
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
        <td>${_bmcChip(flagTh[r.flag], flagInk[r.flag])}${r.manual_kept ? ' ' + _bmcChip('คงราคาแก้เอง', 'var(--navy-ink)') : ''}</td></tr>`).join('')}</tbody></table></div>
    ${p.rows.length > top.length ? `<div class="text-[11px] mt-1" style="color:var(--text-muted)">แสดง ${top.length} จาก ${p.rows.length} (เรียงที่เปลี่ยนมากก่อน)</div>` : ''}
    ${p.issues.length ? `<div class="mt-3 text-xs font-semibold" style="color:var(--amber-ink)">บรรทัดที่ใช้ไม่ได้ — แก้ทะเบียนหรือกฎหน่วยแล้วนำเข้าใหม่</div>
      <div class="overflow-x-auto"><table class="text-[11px] w-full"><tbody>${p.issues.slice(0, 80).map(i => `<tr class="border-b" style="border-color:var(--border-soft)">
        <td class="pr-2">${esc(i.code)}</td><td class="pr-2">${esc(i.name || '')}</td><td class="pr-2 text-right">${_bmcNum(i.qty)} ${esc(i.unit || '')}</td>
        <td class="pr-2 text-right">฿${_bmcNum(i.value, 0)}</td><td style="color:var(--amber-ink)">${esc(i.why)}
        </td></tr>`).join('')}</tbody></table></div>` : ''}
    ${_bmcSuggestedHtml(p.suggested_rules)}
  </div>`;
}
async function _bmcRunPriceImport(send, label) {
  const box = document.getElementById('bmcPricePreview');
  try {
    _bmcStatus('bmcPriceStatus', 'กำลังคำนวณ…');
    const pre = await send(true);
    box.innerHTML = _bmcPreviewHtml(pre, false);
    if (!pre.priced) { _bmcStatus('bmcPriceStatus', 'ไม่มีบรรทัดที่ใช้คิดราคาได้', 'var(--bad-ink)'); return; }
    if (pre.month !== _bmcMonth()) { const el = document.getElementById('bmcMonth'); if (el) el.value = pre.month; }
    if (!confirm(`${label}\nเดือน ${pre.month}: บันทึกราคาซื้อจริง ${pre.priced} วัตถุดิบ` +
        (pre.added_codes?.length ? `\nเพิ่มรหัสใหม่ในทะเบียน ${pre.added_codes.length} (ตรวจหน่วยฐานภายหลัง)` : '') +
        (pre.counts.flag ? `\n⚠ ${pre.counts.flag} รายการราคาเปลี่ยน ≥25% — ตรวจในตารางแล้ว` : '') +
        (pre.issues.length ? `\n${pre.issues.length} บรรทัดใช้ไม่ได้ จะถูกข้าม` : '') + `\n\nบันทึกเลยไหม?`)) {
      _bmcStatus('bmcPriceStatus', 'ยังไม่บันทึก — ตรวจผลด้านล่าง'); return;
    }
    const d = await send(false);
    box.innerHTML = _bmcPreviewHtml(d, true);
    _bmcStatus('bmcPriceStatus', `✓ บันทึก ${d.applied.written} · ยกมา ${d.applied.carried_forward} · คงราคาแก้เอง ${d.applied.kept_manual}`, 'var(--good-ink)');
    _bmcPrices = []; _bmcIng = [];
    bmcLoadPrices(true);
  } catch (e) {
    _bmcStatus('bmcPriceStatus', 'ไม่สำเร็จ — ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)');
  }
}
async function bmcImportPrices(input) {
  const file = input?.files?.[0];
  if (!file) return;
  const send = dry => { const fd = new FormData(); fd.append('file', file, file.name);
    return _bmcJson(`${API}/api/bom-center/prices/import?dry_run=${dry}&add_unknown=${_bmcAddUnknown()}`, { method: 'POST', body: fd }); };
  await _bmcRunPriceImport(send, `ไฟล์ ${file.name}`);
  input.value = '';
}
async function bmcPricesFromReq() {
  const m = _bmcMonth();
  await _bmcRunPriceImport(dry => _bmcJson(`${API}/api/bom-center/prices/from-requisition?month=${m}&dry_run=${dry}&add_unknown=${_bmcAddUnknown()}`, { method: 'POST' }),
    'รายงานจ่ายสินค้าภายในที่อัปโหลดไว้');
}
async function bmcCarry() {
  const m = _bmcMonth();
  if (!confirm(`ยกราคาล่าสุดของทุกวัตถุดิบมาใช้ในเดือน ${m}?\n(รายการที่มีราคาซื้อจริงหรือแก้เองแล้วจะไม่ถูกแตะ)`)) return;
  try {
    const d = await _bmcJson(`${API}/api/bom-center/prices/carry-forward?month=${m}`, { method: 'POST' });
    _bmcStatus('bmcPriceStatus', `✓ ยกราคามา ${d.carried_forward} รายการ`, 'var(--good-ink)');
    bmcLoadPrices(true);
  } catch (e) { _bmcStatus('bmcPriceStatus', 'ไม่สำเร็จ — ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcManual(code) {
  const r = _bmcPrices.find(x => x.code === code) || {};
  const v = prompt(`ราคาต่อ ${r.base_unit || 'หน่วยฐาน'} ของ ${code} ${r.name || ''} เดือน ${_bmcMonth()}\n(เว้นว่าง = ถอนราคาที่แก้เอง)`, r.source === 'manual' ? r.price_per_base : '');
  if (v === null) return;
  let note = '';
  if (v.trim() !== '') {
    note = prompt('เหตุผล (จำเป็น) เช่น ซัพพลายเออร์แจ้งขึ้นราคา');
    if (!note) return;
  }
  try {
    await _bmcJson(`${API}/api/bom-center/prices/manual`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, month: _bmcMonth(), price_per_base: v.trim() === '' ? null : Number(v), note }) });
    _bmcStatus('bmcPriceStatus', `✓ บันทึกราคา ${code}`, 'var(--good-ink)');
    bmcLoadPrices(true);
  } catch (e) { _bmcStatus('bmcPriceStatus', 'ไม่สำเร็จ — ' + String(e.message || e), 'var(--bad-ink)'); }
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
  if (!r) { box.innerHTML = '<span style="color:var(--text-muted)">ใส่รหัสด้านบนเพื่อดูกฎหน่วยของวัตถุดิบนั้น</span>'; return; }
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
  if (!code || !unit) { out.textContent = 'ใส่รหัสและหน่วย'; return; }
  try {
    const d = await _bmcJson(`${API}/api/bom-center/convert?code=${encodeURIComponent(code)}&qty=${qty}&unit=${encodeURIComponent(unit)}&month=${_bmcMonth()}`);
    out.innerHTML = d.qty_base == null
      ? `<span style="color:var(--amber-ink)">แปลงไม่ได้ — ${esc(d.how)}</span>`
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
  if (!code || !unit || !(f > 0)) { _bmcStatus('bmcRuleStatus', 'ใส่รหัส หน่วย และตัวคูณที่มากกว่า 0', 'var(--bad-ink)'); return; }
  try { await _bmcPostRule(code, unit, f); _bmcStatus('bmcRuleStatus', `✓ 1 ${unit} = ${f}`, 'var(--good-ink)'); }
  catch (e) { _bmcStatus('bmcRuleStatus', 'ไม่สำเร็จ — ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcDelRule(code, unit) {
  if (!confirm(`ลบกฎ 1 ${unit} ของ ${code}?`)) return;
  try { await _bmcPostRule(code, unit, null); } catch (e) { _bmcStatus('bmcRuleStatus', String(e.message || e), 'var(--bad-ink)'); }
}
