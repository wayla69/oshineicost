// 13-bom-center.js — BoM Center (docs/BOM_CENTER_PLAN.md, Sprint S1):
// ทะเบียนวัตถุดิบ · ราคาซื้อจริงรายเดือน · แปลงหน่วย. Loaded after 12; shares the
// global scope of slices 01-12. Every name here starts with `bmc` — the BoM Cost
// tab (09e) already owns the `bc` prefix in this shared scope.
// Every import is shown first (dry_run=true) and only written after a confirm.

let _bmcSub = 'overview';
let _bmcIng = [];            // rows from /api/bom-center/ingredients
let _bmcIngEdits = {};       // code -> {field: value}
let _bmcZones = [];          // the storage zones [{code, label, items, fallback}] (ตั้งค่า → โซนจัดเก็บ)
let _bmcPrices = [];         // rows from /api/bom-center/prices
let _bmcSummary = null;
// The วัตถุดิบและใบสั่งซื้อ bar (01-core.js) reads, sets and shows this tab's panes through here.
_SUPPLY_PANE.bomcenter = {
  get: () => _bmcSub, set: k => { _bmcSub = k; }, show: k => bmcSetSub(k),
  month: m => { const el = document.getElementById('bmcMonth'); if (el && el.value !== m) { supplyMonthPut(el, m); _bmcIng = []; _bmcPrices = []; } },
};

// the two values of an order (backend hub.with_values): what the branch ordered, what the warehouse sent
const VALUE_ORDERED_TH = 'มูลค่าที่สั่ง', VALUE_SENT_TH = 'มูลค่าที่จัดส่ง';
const _BMC_KIND = { raw: 'ดิบ', prepared: 'กึ่งสำเร็จ', consumable: 'วัสดุสิ้นเปลือง', zero_by_design: 'ตั้งใจเป็น 0' };
const _BMC_SRC = { purchase_avg: 'ซื้อจริง', carry_forward: 'ยกมา', manual: 'กำหนดเอง', list_ref: 'ใบเสนอราคา', snapshot: 'สูตรเดิม' };
const _BMC_SRC_INK = { purchase_avg: 'var(--good-ink)', carry_forward: 'var(--amber-ink)', manual: 'var(--navy-ink)',
                       list_ref: 'var(--text-muted)', snapshot: 'var(--text-muted)' };

function _bmcMonth() {
  const el = document.getElementById('bmcMonth');
  if (el && !el.value) supplyMonthPick(el, _bmcSub === 'issue');   // the module's shared month first (supplyCtx), else this month (ใบสรุปตัดเบิก: the previous one)
  return el ? el.value : '';
}
// opening a pane: take the shared month; with no choice yet the pane's own default (ใบสรุปตัดเบิก starts at the previous month)
function _bmcMonthSync(k) {
  const el = document.getElementById('bmcMonth');
  if (!el) return;
  const before = el.value;
  supplyMonthPick(el, k === 'issue');
  if (before && el.value !== before) { _bmcIng = []; _bmcPrices = []; }
}
// the server's write rules (auth.py): the centre's data = bom.center; the monthly price
// (and the unit rules a price file needs) = bom.prices too; recipes and sub-recipes = bom.recipes too
function _bmcCanWrite() { return hasModule('bom.center'); }
function _bmcCanPrices() { return hasModule('bom.center') || hasModule('bom.prices'); }
function _bmcCanRecipes() { return hasModule('bom.center') || hasModule('bom.recipes'); }
function _bmcNum(v, d = 2) {
  if (v === null || v === undefined || v === '' || isNaN(v)) return '—';
  return Number(v).toLocaleString('th-TH', { minimumFractionDigits: 0, maximumFractionDigits: d });
}
// 4.6 · a gap in words (mirrors bom_center/fmt.py gap_th). "ผลต่าง −฿16,197 −8.1%" is read the wrong way round as often as the right
// one, so the words carry the direction: "ใช้น้อยกว่าสูตร ฿16,197 (8.1%)". kind: use = what the branch used against the recipes, issue =
// what the slip issued against the recipes, count = the physical count against the system stock. Excel keeps the signed numbers.
const _BMC_GAP_WORDS = { use: ['ใช้มากกว่าสูตร', 'ใช้น้อยกว่าสูตร', 'ใช้ตามสูตร'], issue: ['เบิกมากกว่าสูตร', 'เบิกน้อยกว่าสูตร', 'เบิกตามสูตร'],
  count: ['นับได้มากกว่าระบบ', 'นับได้น้อยกว่าระบบ', 'นับตรงกับระบบ'] };
const _BMC_GAP_SHORT = ['มากกว่า', 'น้อยกว่า', 'ตรง'];
function bmcGapDir(value, pct) {                      // +1 more · -1 less · 0 the same (under ฿0.5 and 0.05 %); the baht gap wins when both are given
  const v = value === null || value === undefined || value === '' ? null : Number(value);
  const p = pct === null || pct === undefined || pct === '' ? null : Number(pct);
  if (v !== null && !isNaN(v)) {
    if (Math.abs(v) < 0.5 && (p === null || isNaN(p) || Math.abs(p) < 0.05)) return 0;
    if (v !== 0) return v > 0 ? 1 : -1;
  }
  if (p === null || isNaN(p) || Math.abs(p) < 0.05) return 0;
  return p > 0 ? 1 : -1;
}
// the pieces of the sentence and the ink of its direction: {dir, label, baht ('฿16,197' or ''), pct ('8.1%' or ''), ink, text}
function bmcGapParts(value, pct, kind, short) {
  const k = _BMC_GAP_WORDS[kind] ? kind : 'use';
  const d = bmcGapDir(value, pct);
  const w = short ? _BMC_GAP_SHORT : _BMC_GAP_WORDS[k];
  const label = d > 0 ? w[0] : d < 0 ? w[1] : w[2];
  const hasV = value !== null && value !== undefined && value !== '' && !isNaN(value), hasP = pct !== null && pct !== undefined && pct !== '' && !isNaN(pct);
  const baht = d !== 0 && hasV && Math.abs(Number(value)) >= 0.5 ? `฿${_bmcNum(Math.abs(Number(value)), 0)}` : '';
  const pc = d !== 0 && hasP && Math.abs(Number(pct)) >= 0.05 ? `${_bmcNum(Math.abs(Number(pct)), Math.abs(Number(pct)) >= 10 ? 0 : 1)}%` : '';
  // the ink: more than the recipes (or less than the system stock) is the loss; the other direction is amber; the same is good
  const bad = k === 'count' ? d < 0 : d > 0;
  const ink = d === 0 ? 'var(--good-ink)' : bad ? 'var(--bad-ink)' : 'var(--amber-ink)';
  const text = d === 0 ? label : label + (baht ? ' ' + baht : '') + (pc ? (baht ? ` (${pc})` : ' ' + pc) : '');
  return { dir: d, label, baht, pct: pc, ink, text };
}
function bmcGapWords(value, pct, kind, short) { return bmcGapParts(value, pct, kind, short).text; }
function bmcGapHtml(value, pct, kind, short) { const g = bmcGapParts(value, pct, kind, short); return `<span style="color:${g.ink}">${esc(g.text)}</span>`; }
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
  if (!r.ok) {
    const d = j && j.detail;                       // a string, or an object such as the late-order {code, message, …}
    throw Object.assign(new Error(typeof d === 'string' ? d : (d && d.message) || t.slice(0, 200)), { detail: d });
  }
  return j;
}
function _bmcStatus(id, msg, ink) {
  const el = document.getElementById(id);
  if (el) { el.textContent = msg; el.style.color = ink || 'var(--text-muted)'; }
  // a save that succeeded (every one reports "✓ …"): หน้างาน says what comes next (16-supplyhome.js)
  // (an autosaved draft is not a step done: no toast until the person submits)
  if (msg && String(msg).startsWith('✓') && !/ร่าง/.test(String(msg)) && typeof shmAfterStatus === 'function') shmAfterStatus(id);
}
// ── PR-M2: ONE status line for every save and download ──────────────────────────
// bmcStatus(el, ok, text): el = an element or its id · ok true = "✓ …" in green, false = "ไม่สำเร็จ: …" in red,
// 'warn' = amber (a field to fix), anything else = grey progress. A save or a download always ends in one of the first two.
function bmcStatus(el, ok, text) {
  const node = typeof el === 'string' ? document.getElementById(el) : el;
  if (!node) return;
  let t = String(text == null ? '' : text);
  if (t && ok === true && !t.startsWith('✓')) t = '✓ ' + t;
  if (t && ok === false && !/^(ไม่สำเร็จ|ไม่ได้|ใส่|ระบุ|เลือก)/.test(t)) t = 'ไม่สำเร็จ: ' + t;
  const ink = ok === true ? 'var(--good-ink)' : ok === false ? 'var(--bad-ink)' : ok === 'warn' ? 'var(--amber-ink)' : 'var(--text-muted)';
  node.setAttribute('role', ok === false ? 'alert' : 'status');
  if (node.id) _bmcStatus(node.id, t, ink); else { node.textContent = t; node.style.color = ink; }
}
// a file from the server saved in the browser, with the outcome on the status line (the server's reason when it refuses)
async function bmcDownload(url, name, statusEl) {
  bmcStatus(statusEl, null, 'กำลังสร้างไฟล์…');
  try {
    const r = await fetch(url);
    if (!r.ok) {
      let m = '';
      try { const j = await r.json(); m = typeof j.detail === 'string' ? j.detail : ''; } catch (e) { /* not json */ }
      throw new Error(m || 'ดาวน์โหลดไม่สำเร็จ');
    }
    let fname = name || 'bom_center.xlsx';
    const cd = r.headers.get('content-disposition') || '';
    const m = /filename\*=UTF-8''([^;]+)/i.exec(cd);
    if (m) { try { fname = decodeURIComponent(m[1]); } catch (e) { /* keep the given name */ } }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(await r.blob());
    a.download = fname;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    bmcStatus(statusEl, true, 'ดาวน์โหลดแล้ว · ' + fname);
  } catch (e) { bmcStatus(statusEl, 'warn', String(e.message || e).slice(0, 220)); }
}
// the help of a page: folded, at the bottom (never a paragraph above the controls). `html` is trusted markup of <p>s.
function bmcHelpHtml(html) {
  return `<details class="bmc-help"><summary>วิธีใช้หน้านี้</summary><div>${html}</div></details>`;
}
// a version's name as people read it: the stored label may still end with the old "(draft)" tag (the status chip says it)
function _bmcLbl(l) { return String(l == null ? '' : l).replace(/\s*\((?:draft|ร่าง)\)\s*$/i, ''); }
// 4.2 (centre) · how a screen names an order and a claim: by branch, round and day, never by a number. An order is
// "ขอนแก่น · รอบ 2 (ส่ง 17/10)" (salmon: "แซลมอนสด (ส่ง 19/10)"); a claim is "เคลมของ ขอนแก่น · รอบ 2 (ส่ง 17/10) · แจ้ง 12/10 14:05"
// (Bangkok clock) and, when its order has 2 or more claims, "· ครั้งที่ 2". The numbers stay in the API, the audit log and the
// Excel files that key into accounting.
const _BMC_ROUND_TH = { '1': 'รอบ 1', '2': 'รอบ 2', salmon: 'แซลมอนสด' };
function _bmcDm(iso) { const p = _isoParts(iso); return p ? `${p[2]}/${p[1]}` : ''; }
function _bmcRoundDay(o) { const d = o.delivery || o.cycle_date; return `${_BMC_ROUND_TH[o.round] || o.round || ''}${d ? ` (ส่ง ${_bmcDm(d)})` : ''}`; }
function _bmcOrderTitle(o) { return [o.branch, _bmcRoundDay(o)].filter(Boolean).join(' · '); }
function _bmcClaimWhen(c) {
  const k = _bkkClock(c.created_at);
  return [k ? `แจ้ง ${k.d}/${k.mo} ${k.hhmm}` : '', c.of_order > 1 ? `ครั้งที่ ${c.nth}` : ''].filter(Boolean).join(' · ');
}
function _bmcClaimTitle(c) { return [`เคลมของ ${c.branch || ''}`.trim(), _bmcRoundDay(c), _bmcClaimWhen(c)].filter(Boolean).join(' · '); }
function _bmcChip(text, ink) {
  return `<span class="bmc-chip" style="display:inline-block;padding:1px 8px;border-radius:999px;border:1px solid var(--border);color:${ink};font-size:12px;line-height:1.5;white-space:nowrap;vertical-align:middle">${esc(text)}</span>`;
}
// One row per branch (2026-10-19, owner: no run-on "✓ อุดรธานี: … · ✓ อุบลราชธานี: …" lines): สาขา · สถานะ · รายละเอียด · the link.
// `branches` = [{branch, status, detail, ...}] as /todo gives them; `linkFn(b)` returns the row's action html (or '').
const _BMC_BR_ST = { done: ['เสร็จแล้ว', 'var(--good-ink)'], todo: ['รอทำ', 'var(--amber-ink)'],
                     blocked: ['รอขั้นตอนก่อนหน้า', 'var(--text-muted)'], na: ['ไม่มีงาน', 'var(--text-muted)'] };
// numbers as tiles instead of "a 12 · b 3 · c 0" sentences: [[label, value, ink?], ...]; a null value is left out
function bmcStats(list) {
  return `<div class="wt-stats">${list.filter(x => x && x[1] != null).map(([l, v, ink]) => `<div class="wt-stat"><div class="wt-stat-l">${l}</div>
    <div class="wt-stat-v"${ink ? ` style="color:${ink}"` : ''}>${typeof v === 'number' ? _bmcNum(v, 0) : v}</div></div>`).join('')}</div>`;
}
// a list inside a table cell: one item per line instead of "a · b · c"; pairs = [[label, value]] with the value right-aligned
function bmcLines(items, max) {
  const list = (items || []).filter(x => x != null && x !== '');
  const cut = max && list.length > max ? list.slice(0, max) : list;
  return cut.map(x => `<div class="wt-li">${x}</div>`).join('') + (cut.length < list.length ? `<div class="wt-li wt-muted">และอีก ${list.length - cut.length} รายการ</div>` : '');
}
function bmcPairs(pairs, max) {
  return bmcLines((pairs || []).map(([l, v]) => `<span class="wt-pair"><span>${l}</span><span class="wt-pv">${v}</span></span>`), max);
}
function bmcBranchTable(branches, linkFn) {
  const list = branches || [];
  if (!list.length) return '';
  const acts = list.map(b => (linkFn && b.status !== 'done' ? linkFn(b) || '' : ''));
  const anyAct = acts.some(Boolean);
  return `<div class="wt-wrap mt-2" style="max-height:360px"><table class="wt"><thead><tr><th>สาขา</th><th>สถานะ</th><th>รายละเอียด</th>${anyAct ? '<th></th>' : ''}</tr></thead><tbody>${list.map((b, i) => {
    const [lbl, ink] = _BMC_BR_ST[b.status] || _BMC_BR_ST.todo;
    return `<tr><td data-l="สาขา" style="font-weight:600;white-space:nowrap">${esc(b.branch || '')}</td>
      <td data-l="สถานะ"><span class="wt-tag" style="color:${ink}">${lbl}</span></td>
      <td data-l="รายละเอียด" style="color:var(--text-secondary)">${esc(b.detail || '')}</td>${anyAct ? `<td class="wt-act">${acts[i]}</td>` : ''}</tr>`;
  }).join('')}</tbody></table></div>`;
}

function bmcSetSub(k) {
  _bmcSub = k;
  _bmcMonthSync(k);
  ['overview', 'rec', 'prep', 'rel', 'fc', 'cal', 'events', 'zones', 'issue', 'ing', 'price', 'pricefile', 'conv', 'rcheck', 'codereq'].forEach(p => {
    const el = document.getElementById('bmc-pane-' + p);
    if (el) el.style.display = p === k ? '' : 'none';
  });
  if (k === 'overview') bmcLoadOverview();
  if (k === 'rec') bmcLoadReleases();
  if (k === 'rel') bmcLoadReleasePane();
  if (k === 'fc') bmcLoadForecastPane();
  if (k === 'cal') bmcLoadCalendar();
  if (k === 'events') bmcLoadEvents();
  if (k === 'zones') bmcLoadZones();
  if (k === 'issue') bmcLoadIssue();
  if (k === 'prep') bmcLoadPrep();
  if (k === 'ing') { bmcLoadIng(); bmcLoadCodeReqCard(); }
  if (k === 'price') bmcLoadPrices();
  if (k === 'pricefile') bmcLoadPriceFile();
  if (k === 'conv') bmcLoadConv();
  if (k === 'rcheck') bmcLoadRcheck();
  if (k === 'codereq') bmcLoadCodeReq();
  const strip = document.getElementById('bmcMonthStrip');            // PR-X: the recipe check has a month list of its own (months with purchases); a code request has none
  if (strip) strip.style.display = (k === 'rcheck' || k === 'codereq' || k === 'events') ? 'none' : '';
  try { _updateClusterBar(activeTab); } catch (e) { /* no bar on this page */ }
}
// The month strip sits above every pane: a change reloads the pane that is open.
function bmcMonthChanged() { _bmcIng = []; _bmcPrices = []; supplyCtxSet({ month: _bmcMonth() }); bmcSetSub(_bmcSub); }

async function loadBomCenter() {
  _bmcMonth();
  document.querySelectorAll('#tab-bomcenter .bc-write').forEach(el => {
    const can = el.closest('#bmc-pane-price, #bmc-pane-conv') ? _bmcCanPrices() : el.closest('#bmc-pane-rec, #bmc-pane-prep, #bmc-pane-rel') ? _bmcCanRecipes() : _bmcCanWrite();
    el.style.display = can ? '' : 'none';
  });
  // Sales Support (and the analyst) send / follow the month's price file (PR-D)
  const ssPrice = hasModule('supply.ordering') || _bmcCanPrices();
  document.querySelectorAll('#tab-bomcenter .bmc-ss-only').forEach(el => { el.style.display = ssPrice ? '' : 'none'; });
  bmcSetSub(_bmcSub);
}

// ── overview ────────────────────────────────────────────────────────────────
// ── batch 4b (3.5) · งานแก้ข้อมูลค้างเรียงตามผลกระทบ: the top 10 by the plates each item touches (backlog.py) ──
// Shared by หน้างาน (16-supplyhome.js) and the readiness page below. `b` = {top | items, note, counts, total, unimpacted}.
function bmcBacklogHtml(b, n) {
  const rows = (b.top || b.items || []).slice(0, n || 10);
  if (!rows.length) return '<span class="text-xs" style="color:var(--good-ink)">✓ ไม่มีงานแก้ข้อมูลค้าง</span>';
  return (b.note ? `<div class="wt-note mb-1">${esc(b.note)}</div>` : '')
    + `<div class="wt-wrap"><table class="wt"><thead><tr><th class="wt-num">#</th><th>งาน</th><th>ประเภท</th><th>ผลกระทบ</th><th></th></tr></thead><tbody>${rows.map((d, i) => `<tr>
      <td class="wt-num wt-muted" data-l="ลำดับ">${i + 1}</td>
      <td data-l="งาน" style="font-weight:600">${esc(d.title)}${d.detail ? `<div class="wt-muted" style="font-size:13px;font-weight:400">${esc(d.detail)}</div>` : ''}</td>
      <td data-l="ประเภท">${_bmcChip(d.kind_th, 'var(--text-muted)')}</td>
      <td data-l="ผลกระทบ" style="color:var(--text-secondary)">${esc(d.impact_th)}</td>
      <td class="wt-act"><button type="button" class="wt-btn is-primary" onclick="_shmOpen(${esc(JSON.stringify(d.go))})">ไปแก้ →</button></td></tr>`).join('')}</tbody></table></div>`
    + ((b.total || 0) > rows.length ? `<div class="wt-note">และอีก ${b.total - rows.length} รายการที่กระทบน้อยกว่า${b.unimpacted ? ` · ไม่กระทบยอดขายใน 30 วันล่าสุด ${b.unimpacted} รายการ` : ''}</div>` : '');
}
async function bmcLoadBacklog() {
  const box = document.getElementById('bmcBacklogCard');
  if (!box) return;
  try {
    const b = await _bmcJson(`${API}/api/bom-center/backlog`);
    box.innerHTML = `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">งานแก้ข้อมูลค้างเรียงตามผลกระทบ <span class="text-xs font-normal" style="color:var(--text-muted)">${b.total} รายการ</span></div>
      <div class="flex flex-wrap gap-1 mb-1">${Object.entries(b.counts || {}).filter(([, n]) => n).map(([k, n]) => _bmcChip(`${(b.kind_th || {})[k] || k} ${n}`, 'var(--text-muted)')).join(' ')}</div>
      ${bmcBacklogHtml(b, 10)}`;
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}

async function bmcLoadOverview() {
  const m = _bmcMonth();
  bmcLoadBacklog();
  try {
    const s = _bmcSummary = await _bmcJson(`${API}/api/bom-center/summary?month=${m}`);
    const tile = (label, val, sub, ink) => `<div class="card p-3"><div class="text-[11px]" style="color:var(--text-muted)">${label}</div>
      <div class="text-xl font-bold" style="color:${ink || 'var(--text-primary)'}">${val}</div><div class="text-[11px]" style="color:var(--text-muted)">${sub || ''}</div></div>`;
    const k = s.kinds || {};
    document.getElementById('bmcKpis').innerHTML = [
      tile('วัตถุดิบในทะเบียน', _bmcNum(s.ingredients, 0), `ดิบ ${k.raw || 0} · กึ่งสำเร็จ ${k.prepared || 0} · สิ้นเปลือง ${k.consumable || 0}`),
      tile('มีราคาใช้ได้เดือนนี้', _bmcNum(s.priced, 0), `ไม่มีราคา ${s.unpriced}`, s.unpriced ? 'var(--amber-ink)' : 'var(--good-ink)'),
      tile('ราคาซื้อจริงเดือนนี้', _bmcNum((s.price_sources || {}).purchase_avg || 0, 0), `ยกมา ${(s.price_sources || {}).carry_forward || 0} · ใบเสนอราคา ${(s.price_sources || {}).list_ref || 0}`),
      tile('ราคาเก่ากว่า 2 เดือน', _bmcNum(s.stale, 0), 'ควรซื้อใหม่หรือยืนยันราคา', s.stale ? 'var(--amber-ink)' : 'var(--good-ink)'),
      tile('บังคับสั่ง (คลัง)', _bmcNum(s.monitored, 0), 'ติดตามในแผนสั่ง'),
    ].join('');
    document.getElementById('bmcOverviewNote').textContent = s.last_import
      ? `นำเข้าราคาล่าสุด: เดือน${fmtMonthTh(s.last_import.month)} โดย ${esc(whoTh(s.last_import.who))} (${fmtAt(s.last_import.at)})`
      : 'ยังไม่เคยนำเข้าราคาซื้อจริง';
    const setup = document.getElementById('bmcSetupCard');
    if (!s.ingredients) {
      setup.innerHTML = `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">เริ่มต้นใช้งาน</div>
        <p class="text-xs mb-2" style="color:var(--text-secondary)">ทะเบียนว่าง: กด “เติมจากข้อมูลในระบบ” แล้วนำเข้าทะเบียนเดิม (DATAP ในไฟล์ Cost-RW) และราคาซื้อจริง</p>
        ${_bmcCanWrite() ? '<button onclick="bmcSeed()" style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:6px 14px;font-size:13px;cursor:pointer">เติมจากข้อมูลในระบบ</button>' : ''}`;
    } else {
      // each open item links to the list where it is fixed
      const steps = [
        [s.recipe_codes_missing === 0, `รหัสวัตถุดิบที่สูตรใช้อยู่ในทะเบียนครบ`, `สูตรใช้รหัสที่ยังไม่อยู่ในทะเบียน ${s.recipe_codes_missing} รหัส`, 'bmcGoIssues()', 'ตรวจรายการและรหัสที่แนะนำ'],
        [s.no_base_unit === 0, 'ทุกวัตถุดิบมีหน่วยฐาน', `${s.no_base_unit} รายการยังไม่มีหน่วยฐาน`, "bmcGoIng('ไม่มีหน่วยฐาน')", 'เปิดรายการ'],
        [s.raw_without_pack === 0, 'วัตถุดิบดิบมีขนาดบรรจุครบ', `${s.raw_without_pack} รายการดิบยังไม่มีขนาดบรรจุ (จึงแปลงหน่วยแพ็คหรือถุงไม่ได้)`, "bmcGoIng('ไม่มีขนาดบรรจุ')", 'เปิดรายการ'],
        [(s.price_sources || {}).purchase_avg > 0, `มีราคาซื้อจริงของเดือน${fmtMonthTh(s.month)}`, `ยังไม่มีราคาซื้อจริงของเดือน${fmtMonthTh(s.month)}`, "bmcSetSub('price')", 'นำเข้าที่ ราคารายเดือน'],
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
         <p class="text-xs" style="color:var(--text-secondary)">กด “เติมจากข้อมูลในระบบ” ด้านบนครั้งเดียว ทุกรหัสจะถูกเพิ่มพร้อมชื่อ หน่วย และขนาดบรรจุ</p>`
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
let _bmcIngGap = '';          // 1.5 · … or only rows with this blank field the order logic reads (fresh_no_shelf | no_moq | loss_zero)
const _BMC_GAP_TH = { fresh_no_shelf: 'ของสด / แช่เย็นที่ยังไม่มีอายุการเก็บ', no_moq: 'ยังไม่มีขั้นต่ำการสั่ง', loss_zero: 'เนื้อสัตว์ / ผักที่สูญเสีย 0%' };
const _BMC_ROW_GAP_TH = { fresh_no_shelf: 'ไม่มีอายุเก็บ', no_moq: 'ไม่มีขั้นต่ำสั่ง', loss_zero: 'สูญเสียเป็น 0' };     // QA L2: softer flags, apart from the problems
function bmcGoIng(prob) {
  _bmcIngProb = prob || '';
  _bmcIngGap = '';
  bmcSetSub('ing');
  const cb = document.getElementById('bmcIngIssues');
  if (cb) cb.checked = !!prob;
  bmcLoadIng(false);
}
function bmcGoIngGap(gap) {
  _bmcIngProb = '';
  _bmcIngGap = gap || '';
  bmcSetSub('ing');
  const cb = document.getElementById('bmcIngIssues');
  if (cb) cb.checked = false;
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
  // each wrong-name group gets its own decision; names travel through _bmcCrGroups, not the onclick text
  _bmcCrGroups = [];
  const gid = (code, names) => { _bmcCrGroups.push({ code, names }); return _bmcCrGroups.length - 1; };
  const dishes = (r, nm) => ((r.names || []).find(x => x.name === nm) || { dishes: 0 }).dishes;
  // 2026-10-19: one table row per code group (no "·"-joined names): รหัส · ชื่อในสูตรเดิม (one per line) · รหัสที่แนะนำ / ที่เลือก · ปุ่ม
  const nameList = (r, names, ink) => names.map(nm =>
    `<div><span style="color:${ink}">${esc(nm || '(ไม่มีชื่อ)')}</span> <span class="wt-muted" style="white-space:nowrap">(${_bmcNum(dishes(r, nm), 0)} เมนู)</span></div>`).join('');
  const okLine = r => {
    const okN = (r.names || []).filter(n => n.status === 'ok');
    return okN.length ? `<div class="wt-muted mt-1" style="font-size:13px">ชื่อที่ตรงกับทะเบียนแล้ว ${okN.length} ชื่อ (${_bmcNum(okN.reduce((a, n) => a + n.dishes, 0), 0)} เมนู) ไม่ต้องเปลี่ยน</div>` : '';
  };
  const codeCell = (r, st, sub) => `<td data-l="รหัส"><div class="wt-code">${esc(r.code)}</div><div class="mt-1">${_bmcChip(_BMC_CR[st][0], _BMC_CR[st][1])}</div>
      <div class="wt-muted mt-1" style="font-size:13px">ในทะเบียน: ${esc(r.master_name || 'ไม่มีในทะเบียน')}${r.unit ? ` (${esc(r.unit)})` : ''}</div>${sub ? `<div class="wt-muted" style="font-size:13px">${sub}</div>` : ''}</td>`;
  const groupRows = r => (r.groups || []).map((g, i) => {
    const id = gid(r.code, g.names);
    const sg = (g.suggest || [])[0];
    const acts = !w ? '' : `<div style="display:flex;flex-direction:column;gap:6px;align-items:stretch">`
      + (r.status === 'missing' ? `<button type="button" class="wt-btn" onclick="bmcSeed()">เติมทะเบียน</button>` : `<button type="button" class="wt-btn" style="color:var(--good-ink)" onclick="bmcCrDecideG(${id}, 'same')">วัตถุดิบเดียวกัน</button>`)
      + (sg ? `<button type="button" class="wt-btn is-primary" onclick="bmcCrDecideG(${id}, 'remap', ${_bmcQ(sg.code)})">ใช้ ${esc(sg.code)}</button>` : '')
      + `<button type="button" class="wt-btn" onclick="bmcCrDecideG(${id}, 'remap', null)">รหัสอื่น…</button></div>`;
    return `<tr>${codeCell(r, r.status === 'missing' ? 'missing' : 'conflict', i > 0 ? 'กลุ่มชื่ออื่นของรหัสเดียวกัน' : '')}
      <td data-l="ชื่อในสูตรเดิม">${nameList(r, g.names, 'var(--bad-ink)')}${i === 0 ? okLine(r) : ''}</td>
      <td data-l="รหัสที่แนะนำ">${(g.suggest || []).slice(0, 2).map(x => `<div><span class="wt-code">${esc(x.code)}</span> ${esc(x.name || '')}</div>`).join('') || '<span class="wt-muted">ไม่พบ · เลือก “รหัสอื่น…”</span>'}</td>
      <td class="wt-act" data-l="">${acts}</td></tr>`;
  }).join('');
  const doneRow = r => {
    const dn = (r.names || []).filter(n => n.status === 'same' || n.status === 'remap');
    if (!dn.length) return '';
    const id = gid(r.code, dn.map(n => n.name));
    const st = dn.some(n => n.status === 'remap') ? 'remap' : 'same';
    return `<tr>${codeCell(r, st)}
      <td data-l="ชื่อในสูตรเดิม">${dn.map(n => `<div><span style="color:${n.status === 'remap' ? 'var(--navy-ink)' : 'var(--good-ink)'}">${esc(n.name)}</span> <span class="wt-muted">(${_bmcNum(n.dishes, 0)} เมนู)</span></div>`).join('')}</td>
      <td data-l="ที่เลือกไว้">${dn.map(n => `<div>${n.status === 'remap' ? `ใช้ <span class="wt-code">${esc(n.use_code || '')}</span> ${esc(n.use_name || '')}` : 'วัตถุดิบเดียวกัน'}
        <span class="wt-muted" style="font-size:13px">${esc(whoTh(n.who) === '-' ? '' : whoTh(n.who))}${n.note ? ' · ' + esc(n.note) : ''}</span></div>`).join('')}</td>
      <td class="wt-act" data-l="">${w ? `<button type="button" class="wt-btn is-bad" onclick="bmcCrDecideG(${id}, 'clear')">ยกเลิกการตัดสินใจ</button>` : ''}</td></tr>`;
  };
  const body = rows.map(r => (_bmcCrView === 'done' ? '' : groupRows(r)) + (_bmcCrView === 'todo' ? '' : doneRow(r))).join('');
  const seg = [['todo', 'ต้องตรวจ', todo], ['done', 'ตรวจแล้ว', done], ['all', 'ทุกรายการที่มีปัญหา', null]]
    .map(([k, l, n]) => `<button type="button" class="${_bmcCrView === k ? 'is-on' : ''}" onclick="bmcCrView('${k}')">${l}${n != null ? `<span class="wt-n">${n}</span>` : ''}</button>`).join('');
  iss.innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:baseline">
      <div class="text-sm font-semibold" style="color:var(--text-primary)">ตรวจรหัสสูตรเดิม: รหัสเดียวกันแต่ชื่อไม่ตรง</div>
      <a href="${API}/api/bom-center/code-review.xlsx?all=true" class="text-xs" style="color:var(--accent)">ดาวน์โหลด Excel (ทุกรหัส)</a></div>
    ${bmcStats([['รหัสที่สูตรเดิมใช้', d.used], ['ชื่อตรงกับทะเบียน', c.ok, 'var(--good-ink)'], ['ชื่อไม่ตรง', c.conflict, c.conflict ? 'var(--bad-ink)' : null],
      ['ไม่อยู่ในทะเบียน', c.missing, c.missing ? 'var(--amber-ink)' : null], ['ตรวจแล้ว', done]])}
    <div class="wt-note mb-2">ชื่อไม่ตรง = รหัสเดียวกันแต่เป็นวัตถุดิบคนละชนิด · เลือก <b>วัตถุดิบเดียวกัน</b> หรือ <b>ใช้รหัสแนะนำ</b> · มีผลกับแผนสั่งเท่านั้น</div>
    <div class="wt-bar"><div class="wt-seg">${seg}</div></div>
    <div id="bmcCrStatus" class="text-xs mb-1"></div>
    ${rows.length ? `<div class="wt-wrap" style="max-height:640px"><table class="wt"><thead><tr><th>รหัส</th><th>ชื่อในสูตรเดิม</th><th>${_bmcCrView === 'done' ? 'ที่เลือกไว้' : 'รหัสที่แนะนำ / ที่เลือกไว้'}</th><th></th></tr></thead><tbody>${body}</tbody></table></div>`
      : `<div class="wt-empty" style="color:var(--good-ink)">${_bmcCrView === 'todo' ? '✓ ไม่มีรหัสที่ต้องตรวจ' : 'ยังไม่มีรายการ'}</div>`}`;
}
function bmcCrDecideG(i, status, useCode) {
  const g = _bmcCrGroups[i];
  return g ? bmcCrDecide(g.code, status, useCode, g.names) : null;
}
async function bmcCrDecide(code, status, useCode, names) {
  let note = null;
  if (status === 'remap' && !useCode) {          // "รหัสอื่น…": pick a master code, closest names first
    const choices = await _bmcCodeChoices(names || [], code);
    useCode = _bmcPickedCode(await uiPrompt(`รหัสที่ใช้แทน ${code} (พิมพ์รหัสหรือชื่อ)`, '',
      { title: 'เลือกรหัสที่ใช้แทน', okText: 'เลือกรหัสนี้', list: choices, placeholder: 'เช่น V-007 หรือ ต้นหอม', required: true }), choices);
    if (!useCode) return;
    note = await uiPrompt(`ใช้ ${useCode} แทน ${code}\nหมายเหตุ (ไม่บังคับ)`, '', { title: 'หมายเหตุการแทนรหัส', okText: 'บันทึกรหัสที่ใช้แทน' }) || null;
  }
  try {
    await _bmcJson(`${API}/api/bom-center/code-review`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, status, use_code: useCode || null, note, names: names || null }) });
    _bmcCr = await _bmcJson(`${API}/api/bom-center/code-review?all=false`);
    bmcRenderCodeReview();
    _bmcStatus('bmcCrStatus', `✓ บันทึก ${code} แล้ว`, 'var(--good-ink)');
  } catch (e) {
    _bmcStatus('bmcCrStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 160), 'var(--bad-ink)');
  }
}

// ── ingredient master ───────────────────────────────────────────────────────
async function bmcLoadIng(force) {
  if (_bmcIng.length && !force) { bmcRenderIng(); return; }
  document.getElementById('bmcIngBody').innerHTML = '<tr><td colspan="18" class="py-3" style="color:var(--text-muted)">กำลังโหลด…</td></tr>';
  try {
    const d = await _bmcJson(`${API}/api/bom-center/ingredients?month=${_bmcMonth()}`);
    try { _bmcZones = (await _bmcJson(`${API}/api/bom-center/zones`)).zones || []; } catch (e) { _bmcZones = _bmcZones || []; }
    _bmcIng = d.rows || [];
    _bmcIngEdits = {}; _bmcIngErr = {}; _bmcIngPage = 0;
    bmcRenderIng();
  } catch (e) {
    document.getElementById('bmcIngBody').innerHTML = `<tr><td colspan="18" class="py-3" style="color:var(--bad-ink)">${esc(String(e.message || e))}</td></tr>`;
  }
}
function _bmcIngVal(r, f) { const e = _bmcIngEdits[r.code]; return e && f in e ? e[f] : r[f]; }
let _bmcIngErr = {};         // code -> messages of the last save the screen refused (shown in red under the row's status)
// what is wrong with one row's edits: the name is not empty and has a letter, the numbers are numbers and not below 0
function _bmcIngProblems(e) {
  const out = [];
  if ('name_th' in e) {
    const n = String(e.name_th == null ? '' : e.name_th).trim();
    if (!n) out.push('ชื่อต้องไม่ว่าง');
    else if (!/[A-Za-zก-๙]/.test(n)) out.push('ชื่อต้องมีตัวอักษร');
  }
  if ('short_name' in e && String(e.short_name == null ? '' : e.short_name).trim().length > 40) out.push('ชื่อสั้นต้องไม่เกิน 40 ตัวอักษร');
  for (const [f, lbl] of [['qty_per_recv', 'ขนาดบรรจุ'], ['loss_pct', 'สูญเสีย %'], ['moq', 'ขั้นต่ำการสั่ง'], ['shelf_days', 'อายุการเก็บ'], ['lead_days', 'ระยะส่ง']]) {
    if (!(f in e) || e[f] === null) continue;
    if (typeof e[f] !== 'number' || !Number.isFinite(e[f])) out.push(`${lbl}ต้องเป็นตัวเลข`);
    else if (e[f] < 0) out.push(`${lbl}ต้องไม่ติดลบ`);
    else if (f === 'qty_per_recv' && e[f] === 0) out.push('ขนาดบรรจุต้องมากกว่า 0');
    else if (f === 'loss_pct' && e[f] >= 100) out.push('สูญเสีย % ต้องน้อยกว่า 100');
  }
  return out;
}
// PR-M5: the register is read 100 rows a page; the search and the filters work on all rows, an edit made on one page stays when the page turns
let _bmcIngPage = 0;
const BMC_ING_PAGE = 100;
function bmcIngFilter() { _bmcIngPage = 0; bmcRenderIng(); }
function bmcIngPageGo(n) { _bmcIngPage = n; bmcRenderIng(); const t = document.getElementById('bmcIngPagerTop'); if (t) t.scrollIntoView({ block: 'start' }); }
function _bmcPagerHtml(page, pages, from, to, total) {
  const b = (label, n, off) => `<button ${off ? 'disabled aria-disabled="true"' : ''} onclick="bmcIngPageGo(${n})" style="border:1px solid var(--border-control);border-radius:var(--r-sm);padding:4px 12px;font-size:12px;background:var(--bg-input);color:${off ? 'var(--text-muted)' : 'var(--text-primary)'};opacity:${off ? .5 : 1};cursor:${off ? 'not-allowed' : 'pointer'}">${label}</button>`;
  return `<div class="flex flex-wrap items-center gap-2 text-xs" style="color:var(--text-secondary)">${pages > 1 ? b('‹ ก่อนหน้า', page - 1, page === 0) : ''}
    <span>${total ? `แสดง ${from}–${to} จาก ${total} รายการ${pages > 1 ? ` · หน้า ${page + 1} จาก ${pages}` : ''}` : 'ไม่มีรายการ'}</span>${pages > 1 ? b('ถัดไป ›', page + 1, page >= pages - 1) : ''}</div>`;
}
function bmcIngEdit(el) {
  const code = el.dataset.code, f = el.dataset.f;
  let v = el.type === 'checkbox' ? (el.checked ? 1 : 0) : el.value;
  if (['qty_per_recv', 'loss_pct', 'moq', 'shelf_days', 'lead_days'].includes(f)) v = v === '' ? null : Number(v);
  (_bmcIngEdits[code] = _bmcIngEdits[code] || {})[f] = v;
  const bad = _bmcIngProblems({ [f]: v }).length > 0;          // the field itself turns red as soon as it is wrong
  el.style.background = bad ? 'var(--bad-soft, #fde8e8)' : 'var(--accent-soft)';
  el.style.borderColor = bad ? 'var(--bad-ink)' : 'var(--border)';
  delete _bmcIngErr[code];
  bmcStatus('bmcIngSummary', 'warn', `แก้ไขแล้ว ${Object.keys(_bmcIngEdits).length} รายการ (ยังไม่บันทึก)`);
}
function bmcRenderIng() {
  const q = (document.getElementById('bmcIngQ').value || '').toLowerCase();
  const kind = document.getElementById('bmcIngKind').value;
  const only = document.getElementById('bmcIngIssues').checked;
  const bar = document.getElementById('bmcIngProbBar');
  if (bar) bar.innerHTML = _bmcIngGap
    ? `${_bmcChip('แสดงเฉพาะ: ' + (_BMC_GAP_TH[_bmcIngGap] || _bmcIngGap), 'var(--amber-ink)')} <a href="#" onclick="_bmcIngGap='';bmcRenderIng();return false" style="color:var(--accent)">แสดงทุกรายการ</a>`
    : _bmcIngProb && only
    ? `${_bmcChip('แสดงเฉพาะ: ' + _bmcIngProb, 'var(--amber-ink)')} <a href="#" onclick="_bmcIngProb='';bmcRenderIng();return false" style="color:var(--accent)">แสดงทุกปัญหา</a>` : '';
  const rows = _bmcIng.filter(r => (!kind || r.kind === kind) && (!only || r.problems.length)
    && (!_bmcIngGap || (r.gaps || []).includes(_bmcIngGap))
    && (!only || !_bmcIngProb || r.problems.includes(_bmcIngProb))
    && (!q || `${r.code} ${r.name_th || ''} ${r.short_name || ''} ${(r.aliases || []).map(a => a.alias).join(' ')}`.toLowerCase().includes(q)));
  const w = _bmcCanWrite();
  const inp = (r, f, width, type) => w
    ? `<input data-code="${esc(r.code)}" data-f="${f}" ${type ? `type="${type}" step="any"` : ''} value="${esc(_bmcIngVal(r, f) ?? '')}" onchange="bmcIngEdit(this)"
         style="width:${width}px;border:1px solid var(--border);border-radius:4px;padding:2px 4px;font-size:11.5px;background:var(--bg-input);color:var(--text-primary);${type ? 'text-align:right' : ''}">`
    : esc(_bmcIngVal(r, f) ?? '—');
  const kindSel = r => w
    ? `<select data-code="${esc(r.code)}" data-f="kind" onchange="bmcIngEdit(this)" style="border:1px solid var(--border);border-radius:4px;font-size:11.5px;background:var(--bg-input);color:var(--text-primary)">
        ${Object.entries(_BMC_KIND).map(([k, t]) => `<option value="${k}" ${_bmcIngVal(r, 'kind') === k ? 'selected' : ''}>${t}</option>`).join('')}</select>`
    : (_BMC_KIND[r.kind] || r.kind);
  // the storage zone is picked from the centre's zone list (ตั้งค่า → โซนจัดเก็บ); blank = the system's guess
  const zoneSel = r => {
    const cur = _bmcIngVal(r, 'storage_zone') || '';
    const known = _bmcZones.some(z => z.code === cur);
    const opts = [`<option value="">(เดาจากชื่อ)</option>`].concat(_bmcZones.map(z => `<option value="${esc(z.code)}" ${z.code === cur ? 'selected' : ''}>${esc(z.label)}</option>`),
      cur && !known ? [`<option value="${esc(cur)}" selected>${esc(cur)}</option>`] : []);
    return w ? `<select data-code="${esc(r.code)}" data-f="storage_zone" onchange="bmcIngEdit(this)" style="border:1px solid var(--border);border-radius:4px;font-size:11.5px;background:var(--bg-input);color:var(--text-primary);max-width:120px">${opts.join('')}</select>`
      : esc((_bmcZones.find(z => z.code === cur) || {}).label || '—');
  };
  const pages = Math.max(1, Math.ceil(rows.length / BMC_ING_PAGE));
  if (_bmcIngPage >= pages) _bmcIngPage = pages - 1;
  if (_bmcIngPage < 0) _bmcIngPage = 0;
  const from = _bmcIngPage * BMC_ING_PAGE, shown = rows.slice(from, from + BMC_ING_PAGE);
  const pager = _bmcPagerHtml(_bmcIngPage, pages, rows.length ? from + 1 : 0, from + shown.length, rows.length);
  ['bmcIngPagerTop', 'bmcIngPager'].forEach(id => { const el = document.getElementById(id); if (el) el.innerHTML = pager; });
  document.getElementById('bmcIngBody').innerHTML = shown.map(r => `<tr class="border-b" style="border-color:var(--border-soft)">
      <td class="py-1 pr-2 whitespace-nowrap">${esc(r.code)}<div class="text-[10px]"><a href="#" onclick="bmcAlToggle(${_bmcQ(r.code)});return false" style="color:var(--accent)">รหัสในระบบอื่น${r.aliases.length ? ` (${r.aliases.length})` : ''}</a></div>${r.aliases.length ? `<div class="text-[10px]" style="color:var(--text-muted)">${r.aliases.map(a => a.in_use ? `<b>${esc(a.alias)}</b>` : esc(a.alias)).join(', ')}</div>` : ''}</td>
      <td class="py-1 pr-2">${inp(r, 'name_th', 210)}</td>
      <td class="py-1 pr-2">${w ? inp(r, 'short_name', 130).replace('<input ', '<input maxlength="40" placeholder="ชื่อเต็ม" ') : esc(r.short_name || '—')}</td>
      <td class="py-1 pr-2">${kindSel(r)}</td>
      <td class="py-1 pr-2">${inp(r, 'base_unit', 70)}</td>
      <td class="py-1 pr-2">${inp(r, 'recv_unit', 90)}</td>
      <td class="py-1 pr-2 text-right">${inp(r, 'qty_per_recv', 80, 'number')}</td>
      <td class="py-1 pr-2 text-right">${inp(r, 'loss_pct', 55, 'number')}</td>
      <td class="py-1 pr-2 text-right">${inp(r, 'moq', 55, 'number')}</td>
      <td class="py-1 pr-2 text-right">${inp(r, 'shelf_days', 55, 'number')}</td>
      <td class="py-1 pr-2 text-right">${inp(r, 'lead_days', 55, 'number')}</td>
      <td class="py-1 pr-2">${zoneSel(r)}</td>
      <td class="py-1 pr-2">${inp(r, 'count_unit', 80)}</td>
      <td class="py-1 pr-2 text-right">${_bmcPrice(r.price_per_base)}${r.price_source ? `<div>${_bmcChip(_BMC_SRC[r.price_source] || r.price_source, _BMC_SRC_INK[r.price_source] || 'var(--text-muted)')}</div>` : ''}</td>
      <td class="py-1 pr-2 text-right">${_bmcPrice(r.price_per_recv)}</td>
      <td class="py-1 pr-2 text-right">${r.used_in_menus || ''}</td>
      <td class="py-1 pr-2 text-center">${w ? `<input type="checkbox" data-code="${esc(r.code)}" data-f="monitored" ${_bmcIngVal(r, 'monitored') ? 'checked' : ''} onchange="bmcIngEdit(this)">` : (r.monitored ? '✓' : '')}</td>
      <td class="py-1">${r.problems.length ? r.problems.map(p => _bmcChip(p, 'var(--amber-ink)')).join(' ') : _bmcChip('พร้อม', 'var(--good-ink)')}
        ${(r.notes || []).concat((r.gaps || []).map(g => _BMC_ROW_GAP_TH[g] || g)).map(n => _bmcChip(n, 'var(--text-muted)')).join(' ')}
        ${(_bmcIngErr[r.code] || []).map(m => `<div class="text-[11px] bmc-rownote" role="alert" style="color:var(--bad-ink)">${esc(m)}</div>`).join('')}</td>
    </tr>${_bmcAlOpen === r.code ? '<tr><td colspan="18" id="bmcAlCell" class="pb-3" style="color:var(--text-muted)">กำลังโหลด…</td></tr>' : ''}`).join('') || '<tr><td colspan="18" class="py-3" style="color:var(--text-muted)">ไม่มีรายการ · ทะเบียนว่าง: กด “เติมจากข้อมูลในระบบ”</td></tr>';
  if (_bmcAlOpen && document.getElementById('bmcAlCell')) { if (_bmcAlState && _bmcAlState.code === _bmcAlOpen) bmcAlRender(_bmcAlState); else bmcAlLoad(_bmcAlOpen); }
  const nProb = _bmcIng.filter(r => r.problems.length).length;
  if (!Object.keys(_bmcIngEdits).length) {
    _bmcStatus('bmcIngSummary', `${rows.length} จาก ${_bmcIng.length} รายการ · มีปัญหา ${nProb}`);
  }
}
async function bmcSaveIng() {
  const rows = Object.entries(_bmcIngEdits).map(([code, e]) => ({ code, ...e }));
  if (!rows.length) { bmcStatus('bmcIngSummary', 'warn', 'ยังไม่มีรายการที่แก้ไข'); return; }
  // validate first: a row with a wrong value is shown in red and nothing is saved until every row is right
  _bmcIngErr = {};
  rows.forEach(r => { const p = _bmcIngProblems(r); if (p.length) _bmcIngErr[r.code] = p; });
  const bad = Object.keys(_bmcIngErr);
  if (bad.length) {
    bmcRenderIng();
    bmcStatus('bmcIngSummary', 'warn', `ไม่ได้บันทึก · แก้ ${bad.length} รายการที่มีข้อความสีแดง: ${bad.slice(0, 3).map(c => c + ' ' + _bmcIngErr[c][0]).join(' · ')}${bad.length > 3 ? ' …' : ''}`);
    const note = document.querySelector('#bmcIngBody .bmc-rownote');
    if (note) note.scrollIntoView({ behavior: 'smooth', block: 'center' });
    return;
  }
  const codes = rows.map(r => r.code);
  if (!await uiConfirm(`บันทึกการแก้ไข ${rows.length} รายการ?\n${codes.slice(0, 10).join(', ')}${codes.length > 10 ? ` และอีก ${codes.length - 10} รายการ` : ''}`,
      { title: 'ยืนยันการบันทึกทะเบียนวัตถุดิบ', ok: 'บันทึกการแก้ไข', cancel: 'ยกเลิก' })) { bmcStatus('bmcIngSummary', null, 'ยังไม่บันทึก'); return; }
  try {
    const d = await _bmcJson(`${API}/api/bom-center/ingredients`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(rows) });
    await bmcLoadIng(true);                         // the reload writes its own count line: say the result after it
    bmcStatus('bmcIngSummary', true, `บันทึก ${d.updated + d.inserted} รายการแล้ว`);
  } catch (e) {
    bmcStatus('bmcIngSummary', false, String(e.message || e).slice(0, 160));
  }
}
// ── storage zones (ตั้งค่า → โซนจัดเก็บ): add · rename · delete · reorder ───────────────────
// The list is edited here and saved in one call (POST /api/bom-center/zones, in the order shown). A zone's code
// never changes (the ingredients point at it); a new zone gets its code from the server. A zone with ingredients
// cannot be deleted: the button says why, and the server refuses it too.
let _bmcZoneDrag = null;
function _bmcZoneCanWrite() { return hasModule('bom.center') || hasModule('supply.ordering'); }
async function bmcLoadZones() {
  document.querySelectorAll('#tab-bomcenter .bmc-zone-write').forEach(el => { el.style.display = _bmcZoneCanWrite() ? '' : 'none'; });
  try {
    _bmcZones = ((await _bmcJson(`${API}/api/bom-center/zones`)).zones || []).map(z => ({ ...z }));
    bmcRenderZones();
    _bmcStatus('bmcZoneStatus', '');
  } catch (e) { document.getElementById('bmcZoneList').innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function bmcRenderZones() {
  const w = _bmcZoneCanWrite();
  const btn = (t, onclick, dis, title) => `<button onclick="${onclick}" ${dis ? 'disabled' : ''} title="${esc(title || '')}" style="border:1px solid var(--border);border-radius:var(--r-sm);padding:2px 9px;font-size:13px;cursor:${dis ? 'not-allowed' : 'pointer'};background:transparent;color:${dis ? 'var(--text-muted)' : 'var(--text-primary)'};opacity:${dis ? .5 : 1}">${t}</button>`;
  document.getElementById('bmcZoneList').innerHTML = _bmcZones.map((z, i) => {
    const why = z.fallback ? 'ลบไม่ได้ เพราะเป็นโซนสำรอง' : (z.items ? `ลบไม่ได้ เพราะยังมีวัตถุดิบ ${z.items} รายการ` : '');
    return `<div class="bmc-zone-row" ${w ? 'draggable="true"' : ''} ondragstart="_bmcZoneDrag=${i}" ondragover="event.preventDefault()" ondrop="bmcZoneDrop(${i})"
        style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;border-top:1px solid var(--border-soft);padding:6px 0">
      <span aria-hidden="true" style="cursor:${w ? 'grab' : 'default'};color:var(--text-muted)" title="ลากเพื่อเรียงลำดับ">⠿</span>
      <span class="text-xs" style="width:22px;color:var(--text-muted)">${i + 1}.</span>
      ${w ? `<input value="${esc(z.label)}" maxlength="40" aria-label="ชื่อโซน" oninput="_bmcZones[${i}].label=this.value;_bmcStatus('bmcZoneStatus','แก้ไขแล้ว ยังไม่บันทึก','var(--amber-ink)')"
              style="border:1px solid var(--border-control);border-radius:var(--r-sm);padding:5px 8px;font-size:13px;background:var(--bg-input);color:var(--text-primary);width:200px">`
        : `<b class="text-sm" style="color:var(--text-primary)">${esc(z.label)}</b>`}
      <span class="text-[11px]" style="color:var(--text-muted)">${z.code ? `รหัส ${esc(z.code)} · ` : 'โซนใหม่ · '}${z.items ? z.items + ' รายการ' : 'ไม่มีวัตถุดิบ'}</span>
      ${w ? `<span style="margin-left:auto;display:flex;gap:4px">${btn('▲', `bmcZoneMove(${i},-1)`, i === 0, 'เลื่อนขึ้น')}${btn('▼', `bmcZoneMove(${i},1)`, i === _bmcZones.length - 1, 'เลื่อนลง')}${btn('ลบ', `bmcZoneDel(${i})`, !!why, why)}</span>` : ''}
    </div>`;
  }).join('') || '<span class="text-xs" style="color:var(--text-muted)">ยังไม่มีโซน</span>';
}
function bmcZoneAdd() {
  const el = document.getElementById('bmcZoneNew'), v = (el.value || '').trim();
  if (!v) return;
  if (_bmcZones.some(z => z.label === v)) { _bmcStatus('bmcZoneStatus', `มีโซน “${v}” แล้ว`, 'var(--amber-ink)'); return; }
  _bmcZones.push({ code: null, label: v, items: 0, fallback: false });
  el.value = '';
  bmcRenderZones();
  _bmcStatus('bmcZoneStatus', 'เพิ่มโซนแล้ว ยังไม่บันทึก', 'var(--amber-ink)');
}
function bmcZoneMove(i, d) {
  const j = i + d;
  if (j < 0 || j >= _bmcZones.length) return;
  [_bmcZones[i], _bmcZones[j]] = [_bmcZones[j], _bmcZones[i]];
  bmcRenderZones();
  _bmcStatus('bmcZoneStatus', 'เรียงลำดับใหม่แล้ว ยังไม่บันทึก', 'var(--amber-ink)');
}
function bmcZoneDrop(to) {
  const from = _bmcZoneDrag;
  _bmcZoneDrag = null;
  if (from === null || from === to) return;
  _bmcZones.splice(to, 0, _bmcZones.splice(from, 1)[0]);
  bmcRenderZones();
  _bmcStatus('bmcZoneStatus', 'เรียงลำดับใหม่แล้ว ยังไม่บันทึก', 'var(--amber-ink)');
}
async function bmcZoneDel(i) {
  const z = _bmcZones[i];
  if (!z || !await uiConfirm(`ลบโซน “${z.label}”?`, { title: 'ยืนยันการลบโซน', ok: 'ลบโซน', cancel: 'ไม่ลบ' })) return;
  _bmcZones.splice(i, 1);
  bmcRenderZones();
  _bmcStatus('bmcZoneStatus', 'ลบโซนแล้ว ยังไม่บันทึก', 'var(--amber-ink)');
}
async function bmcZonesSave() {
  try {
    const d = await _bmcJson(`${API}/api/bom-center/zones`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_bmcZones.map(z => ({ code: z.code || null, label: (z.label || '').trim() }))) });
    _bmcZones = (d.zones || []).map(z => ({ ...z }));
    _bmcIng = [];                                    // the ingredient list shows the zones too
    bmcRenderZones();
    _bmcStatus('bmcZoneStatus', `✓ บันทึกโซนจัดเก็บแล้ว ${_bmcZones.length} โซน`, 'var(--good-ink)');
  } catch (e) { _bmcStatus('bmcZoneStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}

async function bmcSeed() {
  try {
    const pre = await _bmcJson(`${API}/api/bom-center/ingredients/seed?dry_run=true`, { method: 'POST' });
    // a question with three numbers (the rest of the preview is told after it is done)
    const msg = `เพิ่มรหัสใหม่ ${pre.inserted} · เติมช่องว่าง ${pre.updated} · ไม่เปลี่ยน ${pre.unchanged} รายการ — ดำเนินการต่อหรือไม่?\nค่าที่ตั้งไว้แล้วไม่ถูกเขียนทับ`;
    if (!await uiConfirm(msg, { title: 'ยืนยันการเติมทะเบียน', ok: 'เติมทะเบียน', cancel: 'ยกเลิก' })) return;
    const d = await _bmcJson(`${API}/api/bom-center/ingredients/seed?dry_run=false`, { method: 'POST' });
    _bmcStatus('bmcIngStatus', `✓ ทะเบียน ${d.before} → ${d.after} รายการ`, 'var(--good-ink)');
    const rep = document.getElementById('bmcIngReport');
    if (rep) rep.innerHTML = pre.multi_unit_codes.length ? `<div class="card p-3 text-xs" style="color:var(--amber-ink)">รหัสที่สูตรใช้หลายหน่วย ${pre.multi_unit_codes.length} รายการ ใช้หน่วยที่ใช้มากที่สุด · ตรวจที่หน่วยและการแปลง</div>` : '';
    _bmcIng = [];
    bmcSetSub(_bmcSub === 'overview' ? 'overview' : 'ing');
  } catch (e) {
    _bmcStatus('bmcIngStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 160), 'var(--bad-ink)');
  }
}
async function bmcImportIng(input) {
  const file = input?.files?.[0];
  if (!file) return;
  const send = (dry, choices, sys) => { const fd = new FormData(); fd.append('file', file, file.name);
    if (choices) { fd.append('code_choices', JSON.stringify(choices)); fd.append('alias_system', sys || 'acc'); }
    return _bmcJson(`${API}/api/bom-center/ingredients/import?dry_run=${dry}`, { method: 'POST', body: fd }); };
  try {
    _bmcStatus('bmcIngStatus', 'กำลังอ่านไฟล์ Cost-RW…');
    bmcUcReset('bmcUcIng');
    const pre = await send(true, null);
    document.getElementById('bmcIngReport').innerHTML = `<div class="card p-3 text-xs" style="color:var(--text-secondary)">
      แผ่นงาน <b>${esc(pre.sheet)}</b>
      ${bmcStats([['อ่าน (แถว)', pre.rows], ['ใหม่', pre.inserted], ['แก้ไข', pre.updated], ['ไม่เปลี่ยน', pre.unchanged], ['ราคาใบเสนอราคา', pre.ref_prices],
        ['ข้าม (แถว)', pre.skipped.length, pre.skipped.length ? 'var(--amber-ink)' : null]])}
      ${pre.skipped.length ? `<div class="wt-wrap mt-2" style="max-height:260px"><table class="wt"><thead><tr><th class="wt-num">แถว</th><th>รหัส</th><th>เหตุที่ข้าม</th></tr></thead><tbody>
        ${pre.skipped.slice(0, 50).map(s => `<tr><td class="wt-num" data-l="แถว">${s.row}</td><td class="wt-code" data-l="รหัส">${esc(s.code || '')}</td><td data-l="เหตุที่ข้าม" style="color:var(--amber-ink)">${esc(s.why)}</td></tr>`).join('')}</tbody></table></div>
        ${pre.skipped.length > 50 ? `<div class="wt-note">แสดง 50 จาก ${pre.skipped.length} แถว</div>` : ''}` : ''}
      <div id="bmcUcIng" class="mt-2" style="display:none"></div></div>`;
    if (!(pre.inserted + pre.updated + pre.ref_prices)) { _bmcStatus('bmcIngStatus', 'ไม่มีรายการเปลี่ยนแปลง'); return; }
    // PR-Y: a code the register does not know may be a new code of an item it has - the person answers per code before anything is added
    const fresh = (pre.new_codes || []).filter(n => (n.suggestions || []).length);
    let choices = null, sys = 'acc';
    if (fresh.length) {
      let go = null;
      const footer = _bmcBtn('นำเข้า', 'window._bmcIngGo(true)', true) + _bmcBtn('ยกเลิก', 'window._bmcIngGo(false)');
      await bmcUcRender('bmcUcIng', pre.new_codes, { mode: 'master', needAll: true, prefillNew: true, footer });
      _bmcStatus('bmcIngStatus', `มีรหัสที่ยังไม่อยู่ในทะเบียน ${pre.new_codes.length} รหัส และ ${fresh.length} รหัสมีวัตถุดิบเดิมที่ใกล้เคียง: เลือกให้ครบ แล้วกด “นำเข้า”`, 'var(--amber-ink)');
      for (;;) {                                   // wait for the person; a press with codes still unanswered says which and waits again
        go = await new Promise(res => { window._bmcIngGo = res; });
        if (!go) { _bmcStatus('bmcIngStatus', 'ยกเลิกแล้ว'); return; }
        const open = bmcUcOpen('bmcUcIng');
        if (!open.length) break;
        _bmcStatus('bmcIngStatus', `ยังไม่ได้เลือก ${open.length} รหัส: ${open.slice(0, 4).join(', ')}${open.length > 4 ? ' …' : ''}`, 'var(--amber-ink)');
      }
      choices = bmcUcChoices('bmcUcIng'); sys = bmcUcSystem('bmcUcIng');
    }
    const cs = choices ? bmcUcSummary('bmcUcIng') : { alias: 0, fresh: 0 };
    if (!await uiConfirm(`นำเข้า ${file.name}: ใหม่ ${pre.inserted - cs.alias} · แก้ไข ${pre.updated} รายการ\nค่าในไฟล์จะเขียนทับค่าเดิม` + (cs.alias ? `\nบันทึกเป็นรหัสใหม่ของวัตถุดิบเดิม ${cs.alias} รหัส` : ''), { title: 'ยืนยันการนำเข้าทะเบียน', ok: 'นำเข้า', cancel: 'ยกเลิก' })) { _bmcStatus('bmcIngStatus', 'ยกเลิกแล้ว'); return; }
    const d = await send(false, choices, sys);
    _bmcStatus('bmcIngStatus', `✓ นำเข้าแล้ว ใหม่ ${d.inserted} · แก้ไข ${d.updated}` + (d.aliased ? ` · รหัสใหม่ของวัตถุดิบเดิม ${d.aliased}` : '') + (d.requested ? ` · ส่งคำขอให้ฝ่ายวิเคราะห์ ${d.requested}` : ''), 'var(--good-ink)');
    bmcLoadIng(true);
  } catch (e) {
    _bmcStatus('bmcIngStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 160), 'var(--bad-ink)');
  } finally { input.value = ''; }
}

// ── monthly prices ──────────────────────────────────────────────────────────
async function bmcLoadPrices(force) {
  document.getElementById('bmcPriceMonthLbl').textContent = fmtMonthTh(_bmcMonth());
  bmcLoadPriceSubs();
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
      sel.innerHTML = months.map(m => `<option value="${m}">${fmtMonthTh(m)}</option>`).join('') || '<option value="">ยังไม่มีรายงาน</option>';
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
  const w = _bmcCanPrices();
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
  if (!_bmcLastSuggested.length || !_bmcCanPrices()) return '';
  return `<div class="mt-3 p-3" style="background:var(--bg-subtle);border-radius:var(--r-sm)">
    <div class="text-xs font-semibold" style="color:var(--text-primary)">กฎหน่วยที่แนะนำ (${_bmcLastSuggested.length}): ขนาดแพ็คไม่ตรงทะเบียน</div>
    <div class="text-[11px] mb-2" style="color:var(--text-muted)">เลือกกฎที่ต้องการ แล้วบันทึกพร้อมราคาได้ทันที · แก้ตัวเลขได้</div>
    <div class="overflow-x-auto"><table class="text-[11.5px] w-full" style="font-variant-numeric:tabular-nums"><tbody>
    ${_bmcLastSuggested.map((s, i) => `<tr class="border-b" style="border-color:var(--border-soft)">
      <td class="pr-2"><input type="checkbox" id="bmcSug${i}" ${s.consistent ? 'checked' : ''}></td>
      <td class="pr-2 whitespace-nowrap">${esc(s.code)}</td><td class="pr-2">${esc(s.name || '')}</td>
      <td class="pr-2 whitespace-nowrap">1 ${esc(s.unit)} =</td>
      <td class="pr-2"><input id="bmcSugF${i}" type="number" step="any" value="${s.factor}" style="width:90px;border:1px solid var(--border);border-radius:4px;padding:1px 4px;text-align:right;background:var(--bg-input);color:var(--text-primary)"> ${esc(s.base_unit || '')}</td>
      <td class="pr-2 text-right">${s.lines} บรรทัด · ฿${_bmcNum(s.value, 0)}</td>
      <td>${s.consistent ? '' : _bmcChip('ค่าไม่ตรงกัน ควรตรวจ', 'var(--amber-ink)')}</td></tr>`).join('')}
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
// PR-Y: the answers to "which unknown code is a new code of an existing item"; null = none given
function _bmcPriceChoices() { const c = bmcUcChoices('bmcUcPrice'); return Object.keys(c).length ? c : null; }
function _bmcShowPrice(p, applied) {
  document.getElementById('bmcPricePreview').innerHTML = _bmcPreviewHtml(p, applied);
  if (applied) { bmcUcReset('bmcUcPrice'); return; }
  bmcUcRender('bmcUcPrice', p.unknown_items || [], { mode: 'price', onRecalc: 'bmcPriceRepreview()' });
}
async function bmcPriceRepreview() {
  if (!_bmcPriceSend) return;
  _bmcChosenRules = _bmcRulesToSend();
  try {
    _bmcStatus('bmcPriceStatus', 'กำลังคำนวณใหม่…');
    _bmcPricePre = await _bmcPriceSend(true, _bmcChosenRules, _bmcPriceChoices());
    _bmcShowPrice(_bmcPricePre, false);
    _bmcStatus('bmcPriceStatus', `คำนวณด้วยกฎหน่วย ${_bmcChosenRules.length} รายการและรหัสที่เลือกแล้ว · ตรวจผล แล้วกด “บันทึกราคา”`);
  } catch (e) { _bmcStatus('bmcPriceStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
async function bmcPriceCommit() {
  const pre = _bmcPricePre;
  if (!pre || !_bmcPriceSend) return;
  const rules = _bmcRulesToSend(), choices = _bmcPriceChoices(), cs = bmcUcSummary('bmcUcPrice'), open = bmcUcOpen('bmcUcPrice').length;
  if (!pre.priced && !rules.length && !choices) { _bmcStatus('bmcPriceStatus', 'ไม่มีบรรทัดที่ใช้คิดราคาได้', 'var(--bad-ink)'); return; }
  if (!await uiConfirm(`${_bmcPriceLabel}\nเดือน${fmtMonthTh(pre.month)}: บันทึกราคาซื้อจริง ${pre.priced} วัตถุดิบ` +
      (rules.length ? `\nพร้อมกฎหน่วย ${rules.length} รายการ (บรรทัดที่ติดกฎจะคิดราคาได้ในการบันทึกนี้)` : '') +
      (cs.alias ? `\nบันทึกเป็นรหัสใหม่ของวัตถุดิบเดิม ${cs.alias} รหัส (สูตรไม่ต้องแก้)` : '') +
      (cs.fresh ? `\nเพิ่มเป็นวัตถุดิบใหม่ ${cs.fresh} รหัส (ตรวจหน่วยฐานภายหลัง)` : '') +
      (open ? `\nรหัสที่ยังไม่เลือก ${open} รหัส ไม่ถูกนำเข้าในครั้งนี้` : '') +
      (pre.added_codes?.length ? `\nเพิ่มรหัสใหม่ในทะเบียน ${pre.added_codes.length} (ตรวจหน่วยฐานภายหลัง)` : '') +
      (pre.counts.flag ? `\nราคาเปลี่ยน ≥25% ${pre.counts.flag} รายการ · ตรวจในตารางก่อน` : '') +
      (pre.issues.length ? `\n${pre.issues.length} บรรทัดใช้ไม่ได้และจะถูกข้าม` : ''), { title: 'ยืนยันการนำเข้าราคา', ok: 'บันทึกราคา', cancel: 'ยกเลิก' })) {
    _bmcStatus('bmcPriceStatus', 'ยังไม่บันทึก'); return;
  }
  try {
    const d = await _bmcPriceSend(false, rules, choices);
    _bmcShowPrice(d, true);
    const dc = d.codes_decided || {};
    _bmcStatus('bmcPriceStatus', `✓ บันทึก ${d.applied.written} · ยกมา ${d.applied.carried_forward} · คงราคาที่กำหนดเอง ${d.applied.kept_manual}` + (d.rules_applied ? ` · กฎหน่วย ${d.rules_applied}` : '')
      + ((dc.aliased || []).length ? ` · รหัสใหม่ของวัตถุดิบเดิม ${dc.aliased.length}` : '') + ((dc.requested || []).length ? ` · ส่งคำขอให้ฝ่ายวิเคราะห์ ${dc.requested.length}` : ''), 'var(--good-ink)');
    _bmcIng = [];
    _bmcPrices = []; _bmcIng = []; _bmcPricePre = null; _bmcChosenRules = [];
    bmcLoadPrices(true);                      // (also reloads the files ประสานงานขาย sent: one just became “นำเข้าแล้ว”)
  } catch (e) { _bmcStatus('bmcPriceStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
function _bmcPreviewHtml(p, applied) {
  const flagInk = { flag: 'var(--bad-ink)', warn: 'var(--amber-ink)', new: 'var(--navy-ink)', ok: 'var(--text-muted)' };
  const flagTh = { flag: 'เปลี่ยน ≥25%', warn: 'เปลี่ยน ≥10%', new: 'ใหม่', ok: 'ปกติ' };
  const top = p.rows.filter(r => r.flag !== 'ok').concat(p.rows.filter(r => r.flag === 'ok')).slice(0, 60);
  return `<div class="card p-4">
    <div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">${applied ? '✓ บันทึกแล้ว' : 'ตัวอย่างก่อนบันทึก'}: ราคาซื้อจริงเดือน${fmtMonthTh(p.month)}</div>
    <div class="text-xs mb-2" style="color:var(--text-secondary)">${esc(p.source?.format || '')} · ${p.lines} บรรทัด · คิดราคาได้ ${p.priced} วัตถุดิบ (มูลค่า ฿${_bmcNum(p.value_total, 0)}) ·
      เปลี่ยน ≥25% <b style="color:var(--bad-ink)">${p.counts.flag}</b> · ≥10% <b style="color:var(--amber-ink)">${p.counts.warn}</b> · ใหม่ ${p.counts.new} ·
      ยกราคาเดิมมา ${p.carry_forward} · ยังไม่เคยมีราคา ${p.never_priced}
      ${p.added_codes?.length ? ` · <b style="color:var(--navy-ink)">${applied ? 'เพิ่ม' : 'จะเพิ่ม'}รหัสใหม่ในทะเบียน ${p.added_codes.length}</b>` : ''}
      ${p.unknown_codes?.length ? ` · <b style="color:var(--amber-ink)">รหัสที่ไม่อยู่ในทะเบียน ${p.unknown_codes.length} (฿${_bmcNum(p.unknown_value, 0)}) · เลือกรายการละรหัสด้านล่าง</b>` : ''}
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
    ${p.issues.length ? `<div class="mt-3 text-xs font-semibold" style="color:var(--amber-ink)">บรรทัดที่ใช้ไม่ได้: แก้ไขทะเบียนหรือกฎหน่วย แล้วนำเข้าใหม่</div>
      <div class="overflow-x-auto"><table class="text-[11px] w-full"><tbody>${p.issues.slice(0, 80).map(i => `<tr class="border-b" style="border-color:var(--border-soft)">
        <td class="pr-2">${esc(i.code)}</td><td class="pr-2">${esc(i.name || '')}</td><td class="pr-2 text-right">${_bmcNum(i.qty)} ${esc(i.unit || '')}</td>
        <td class="pr-2 text-right">฿${_bmcNum(i.value, 0)}</td><td style="color:var(--amber-ink)">${esc(i.why)}
        </td></tr>`).join('')}</tbody></table></div>` : ''}
    ${applied ? '' : '<div id="bmcUcPrice" class="mt-3" style="display:none"></div>'}
    ${_bmcSuggestedHtml(p.suggested_rules)}
    ${applied || !_bmcCanPrices() ? '' : `<div class="flex flex-wrap items-center gap-2 mt-3"><button onclick="bmcPriceCommit()" style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:6px 16px;font-size:13px;font-weight:600;cursor:pointer">บันทึกราคา${(p.suggested_rules || []).length || _bmcChosenRules.length ? 'พร้อมกฎที่เลือก' : ''}</button>
      <span class="text-[11px]" style="color:var(--text-muted)">ยังไม่บันทึก</span></div>`}
  </div>`;
}
// One upload: preview → (tick the pack guard's suggestions, recalculate if wanted) → one confirm
// saves the prices AND the rules. Without suggestions the confirm comes straight after the preview.
async function _bmcRunPriceImport(send, label) {
  const box = document.getElementById('bmcPricePreview');
  _bmcPriceSend = send; _bmcPriceLabel = label; _bmcChosenRules = []; _bmcPricePre = null; bmcUcReset('bmcUcPrice');
  try {
    _bmcStatus('bmcPriceStatus', 'กำลังคำนวณ…');
    const pre = await send(true, [], null);
    _bmcPricePre = pre;
    _bmcShowPrice(pre, false);
    if (pre.month !== _bmcMonth()) { const el = document.getElementById('bmcMonth'); if (el) supplyMonthPut(el, pre.month); supplyCtxSet({ month: pre.month }); }
    if ((pre.suggested_rules || []).length) {
      _bmcStatus('bmcPriceStatus', 'มีกฎหน่วยที่แนะนำ: กด “คำนวณใหม่ด้วยกฎที่เลือก” หรือ “บันทึกราคาพร้อมกฎที่เลือก”', 'var(--amber-ink)');
      return;
    }
    if ((pre.unknown_items || []).length) {      // PR-Y: nothing is added on its own - the person answers per code first
      _bmcStatus('bmcPriceStatus', `มีรหัสที่ระบบไม่รู้จัก ${pre.unknown_items.length} รหัส: เลือกรายการละรหัสด้านล่าง แล้วกด “บันทึกราคา” (รหัสที่ไม่เลือกจะไม่ถูกนำเข้า)`, 'var(--amber-ink)');
      return;
    }
    if (!pre.priced) { _bmcStatus('bmcPriceStatus', 'ไม่มีบรรทัดที่ใช้คิดราคาได้', 'var(--bad-ink)'); return; }
    await bmcPriceCommit();
  } catch (e) {
    _bmcStatus('bmcPriceStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)');
  }
}
async function bmcImportPrices(input) {
  const file = input?.files?.[0];
  if (!file) return;
  const send = (dry, rules, choices) => { const fd = new FormData(); fd.append('file', file, file.name);
    if (rules && rules.length) fd.append('rules', JSON.stringify(rules));
    if (choices) { fd.append('code_choices', JSON.stringify(choices)); fd.append('alias_system', bmcUcSystem('bmcUcPrice')); }
    return _bmcJson(`${API}/api/bom-center/prices/import?dry_run=${dry}`, { method: 'POST', body: fd }); };
  await _bmcRunPriceImport(send, `ไฟล์ ${file.name}`);
  input.value = '';
}

// ── the price file Sales Support sends to the analyst (price_submissions.py) ──
async function bmcSubmitPriceFile(input) {
  const file = input?.files?.[0];
  if (!file) return;
  const m = _bmcMonth();
  try {
    if (!await uiConfirm(`ส่งไฟล์ ${file.name} สำหรับเดือน${fmtMonthTh(m)} ให้ฝ่ายวิเคราะห์?`, { title: 'ยืนยันการส่งไฟล์ราคา', ok: 'ส่งไฟล์ราคา', cancel: 'ยกเลิก' })) {
      _bmcStatus('bmcSubStatus', 'ยังไม่ได้ส่งไฟล์');
      return;
    }
    _bmcStatus('bmcSubStatus', 'กำลังตรวจไฟล์…');
    const fd = new FormData();
    fd.append('file', file, file.name);
    fd.append('month', m);
    const note = (document.getElementById('bmcSubNote')?.value || '').trim();
    if (note) fd.append('note', note);
    const sent = await _bmcJson(`${API}/api/bom-center/prices/submit`, { method: 'POST', body: fd });
    const n = document.getElementById('bmcSubNote');
    if (n) n.value = '';
    const unk = sent.unknown_items || [];
    _bmcStatus('bmcSubStatus', '✓ ส่งไฟล์ราคาแล้ว · รอฝ่ายวิเคราะห์ตรวจ' + (unk.length ? ` · มีรหัสที่ระบบไม่รู้จัก ${unk.length} รหัส ดูด้านล่าง` : ''), 'var(--good-ink)');
    bmcUcReset('bmcSubUnknown');
    if (unk.length) bmcUcRender('bmcSubUnknown', unk, { mode: 'request', system: 'acc' });
    bmcLoadPriceSubs();
  } catch (e) {
    _bmcStatus('bmcSubStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)');
  } finally { input.value = ''; }
}
const _BMC_SUB_ST = { pending: 'var(--amber-ink)', imported: 'var(--good-ink)', rejected: 'var(--bad-ink)' };
// PR-M2: two views of the same list. ไฟล์ราคา (ประสานงานขาย): every file she sent, with its verdict, no buttons.
// ราคารายเดือน (ฝ่ายวิเคราะห์): only the files waiting for a decision, with ดูตัวอย่าง / เปิดไฟล์ / ส่งกลับ.
function _bmcSubRow(s, withActions) {
  const p = s.preview || {}, at = x => esc(fmtAt(x));
  const verdict = s.status === 'imported' ? `นำเข้าแล้ว เมื่อ ${at(s.decided_at)} โดย ${esc(whoTh(s.decided_by))}`
    : s.status === 'rejected' ? `ส่งกลับ: ${esc(s.decision_note || '-')}` : 'รอฝ่ายวิเคราะห์ตรวจ';
  return `<div class="py-2 border-b" style="border-color:var(--border-soft)">
    <div class="flex flex-wrap items-center gap-2">
      <span class="text-xs font-semibold" style="color:var(--text-primary)">${fmtMonthTh(s.month)} · ${esc(s.filename || '')}</span>
      <span class="text-[11px]" style="color:var(--text-muted)">${s.size < 1024 ? _bmcNum(s.size, 0) + ' ไบต์' : _bmcNum(s.size / 1024, 0) + ' KB'} · ส่งโดย ${esc(whoTh(s.submitted_by))} เมื่อ ${at(s.submitted_at)}</span>
      ${_bmcChip(s.status_th || s.status, _BMC_SUB_ST[s.status] || 'var(--text-muted)')}
      ${withActions && s.status === 'pending' ? `<span class="ml-auto flex flex-wrap gap-2">
        <button onclick="bmcSubPreview(${s.sub_id})" style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:4px 12px;font-size:12px;cursor:pointer">ดูตัวอย่าง</button>
        <button onclick="window.open(API + '/api/bom-center/prices/submissions/${s.sub_id}/file','_blank')" style="background:var(--bg-input);color:var(--text-primary);border:1px solid var(--border-control);border-radius:var(--r-sm);padding:4px 12px;font-size:12px;cursor:pointer">เปิดไฟล์</button>
        <button onclick="bmcSubReject(${s.sub_id})" style="background:var(--bg-input);color:var(--bad-ink);border:1px solid var(--border-control);border-radius:var(--r-sm);padding:4px 12px;font-size:12px;cursor:pointer">ส่งกลับ</button></span>` : ''}
    </div>
    <div class="text-[11px] mt-0.5" style="color:var(--text-secondary)">สรุป: ${_bmcNum(p.lines, 0)} บรรทัด คิดราคาได้ ${_bmcNum(p.priced, 0)} รายการ ใช้ไม่ได้ ${_bmcNum(p.issues, 0)}
      · <span style="color:${_BMC_SUB_ST[s.status] || 'inherit'}">${verdict}</span>${s.note ? ' · หมายเหตุ: ' + esc(s.note) : ''}</div></div>`;
}
async function bmcLoadPriceSubs() {
  const pending = document.getElementById('bmcPriceSubList'), all = document.getElementById('bmcPriceFileList');
  if (!pending && !all) return;
  const canSee = hasModule('supply.ordering') || _bmcCanPrices();
  if (!canSee) { [pending, all].forEach(b => { if (b) b.style.display = 'none'; }); return; }
  try {
    const d = await _bmcJson(`${API}/api/bom-center/prices/submissions?limit=20`);
    const subs = d.submissions || [];
    if (all) {
      all.innerHTML = `<div class="text-xs font-semibold mb-1" style="color:var(--text-primary)">ไฟล์ที่ส่งแล้ว</div>`
        + (subs.map(s => _bmcSubRow(s, false)).join('') || '<div class="text-[11px]" style="color:var(--text-muted)">ยังไม่มีไฟล์ที่ส่งมา</div>');
      all.style.display = '';
    }
    if (pending) {
      const wait = subs.filter(s => s.status === 'pending');
      pending.innerHTML = `<div class="text-xs font-semibold mb-1" style="color:var(--text-primary)">ไฟล์ราคาที่รอตรวจ (${wait.length})</div>`
        + wait.map(s => _bmcSubRow(s, _bmcCanPrices())).join('');
      pending.style.display = wait.length && _bmcCanPrices() ? '' : 'none';
    }
  } catch (e) {
    [pending, all].forEach(b => { if (b) { b.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; b.style.display = ''; } });
  }
}
function bmcLoadPriceFile() {
  const sml = document.getElementById('bmcSubMonthLbl');
  if (sml) sml.textContent = fmtMonthTh(_bmcMonth());
  bmcLoadPriceSubs();
}
async function bmcSubPreview(id) {
  const send = (dry, rules, choices) => { const fd = new FormData(); fd.append('rules', JSON.stringify(rules || []));
    if (choices) { fd.append('code_choices', JSON.stringify(choices)); fd.append('alias_system', bmcUcSystem('bmcUcPrice')); }
    return _bmcJson(`${API}/api/bom-center/prices/submissions/${id}/import?dry_run=${dry}`, { method: 'POST', body: fd }); };
  await _bmcRunPriceImport(send, `ไฟล์ที่ประสานงานขายส่งมา (ลำดับที่ ${id})`);
}
async function bmcSubReject(id) {
  const note = await uiPrompt('เหตุผลที่ส่งกลับ (จำเป็น)', '', { title: 'ส่งไฟล์ราคากลับ', okText: 'ส่งกลับ' });
  if (!note || !note.trim()) return;
  try {
    await _bmcJson(`${API}/api/bom-center/prices/submissions/${id}/reject`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note: note.trim() }) });
    _bmcStatus('bmcPriceStatus', '✓ ส่งกลับให้ประสานงานขายแล้ว', 'var(--good-ink)');
    bmcLoadPriceSubs();
  } catch (e) { _bmcStatus('bmcPriceStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
async function bmcPricesFromReq() {
  const sel = document.getElementById('bmcReqMonth');
  const m = (sel && sel.value) || _bmcMonth();
  if (!m) { _bmcStatus('bmcPriceStatus', 'ยังไม่มีรายงานจ่ายสินค้าภายใน', 'var(--amber-ink)'); return; }
  await _bmcRunPriceImport((dry, rules, choices) => _bmcJson(`${API}/api/bom-center/prices/from-requisition?month=${m}&dry_run=${dry}`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rules: rules || [], code_choices: choices || null, alias_system: bmcUcSystem('bmcUcPrice') }) }), `รายงานจ่ายสินค้าภายในเดือน${fmtMonthTh(m)} ที่นำเข้าไว้`);
}
async function bmcCarry() {
  const m = _bmcMonth();
  if (!await uiConfirm(`ใช้ราคาล่าสุดของทุกวัตถุดิบสำหรับเดือน${fmtMonthTh(m)}?\n(รายการที่มีราคาซื้อจริงหรือราคาที่กำหนดเองแล้วไม่เปลี่ยน)`, { title: 'ยืนยันการยกราคาเดือนก่อน', ok: 'ยกราคามาใช้', cancel: 'ยกเลิก' })) return;
  try {
    const d = await _bmcJson(`${API}/api/bom-center/prices/carry-forward?month=${m}`, { method: 'POST' });
    _bmcStatus('bmcPriceStatus', `✓ ยกราคามา ${d.carried_forward} รายการ`, 'var(--good-ink)');
    bmcLoadPrices(true);
  } catch (e) { _bmcStatus('bmcPriceStatus', 'ไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcManual(code) {
  const r = _bmcPrices.find(x => x.code === code) || {};
  const v = await uiPrompt(`ราคาต่อ ${r.base_unit || 'หน่วยฐาน'} ของ ${code} ${r.name || ''} เดือน${fmtMonthTh(_bmcMonth())}\n(เว้นว่างเพื่อยกเลิกราคาที่กำหนดเอง)`, r.source === 'manual' ? r.price_per_base : '', { title: `แก้ราคา ${code}`, okText: 'บันทึกราคา' });
  if (v === null) return;
  let note = '';
  if (v.trim() !== '') {
    note = await uiPrompt('เหตุผล (จำเป็น) เช่น ไดกิโตะ กรุ๊ปแจ้งปรับราคา', '', { title: 'เหตุผลที่แก้ราคา', okText: 'ตกลง' });
    if (!note) return;
  }
  try {
    await _bmcJson(`${API}/api/bom-center/prices/manual`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, month: _bmcMonth(), price_per_base: v.trim() === '' ? null : Number(v), note }) });
    _bmcStatus('bmcPriceStatus', `✓ บันทึกราคา ${code}`, 'var(--good-ink)');
    bmcLoadPrices(true);
  } catch (e) { _bmcStatus('bmcPriceStatus', 'ไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
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
      <tbody>${d.rows.map(h => `<tr class="border-b" style="border-color:var(--border-soft)"><td class="pr-2">${fmtMonthTh(h.month)}</td>
        <td class="pr-2 text-right font-semibold">${_bmcPrice(h.price_per_base)}</td><td class="pr-2">${_bmcChip(_BMC_SRC[h.source] || h.source, _BMC_SRC_INK[h.source] || 'var(--text-muted)')}</td>
        <td class="pr-2 text-right">${_bmcNum(h.qty_bought)}</td><td class="pr-2 text-right">${_bmcNum(h.value_bought, 0)}</td>
        <td class="pr-2" style="min-width:180px">${bmcPairs(h.branches.map(b => [esc(b.branch), _bmcPrice(b.price_per_base)]))}</td>
        <td style="color:var(--text-muted)">${bmcLines([esc(h.note || ''), h.by ? esc(whoTh(h.by)) : ''])}</td></tr>`).join('') || '<tr><td colspan="7">ยังไม่มีราคา</td></tr>'}</tbody></table>`;
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
      bmcPortInit();
    }).catch(() => {});
    bmcRenderPrep();
    bmcPortInit();
  } catch (e) { box.innerHTML = `<span style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function bmcPrepView(v) { _bmcPrepView = v; bmcRenderPrep(); }
function bmcRenderPrep() {
  const d = _bmcPrep;
  if (!d) return;
  const c = d.counts || {};
  const n = k => c[k] || 0;
  const chip = (label, v) => _bmcBtn(label, `bmcPrepView('${v}')`, false).replace('background:transparent', `background:${_bmcPrepView === v ? 'var(--accent-soft, var(--bg-hover))' : 'transparent'}`);
  document.getElementById('bmcPrepChips').innerHTML = chip(`ต้องทำ (${n('incomplete') + n('check')})`, 'todo')
    + chip(`ยังไม่มีสูตร (${n('none')})`, 'none') + chip(`ใช้ต้นทุนจากสูตร (${n('ok')})`, 'ok') + chip(`ทั้งหมด (${d.rows.length})`, 'all');
  document.getElementById('bmcPrepMonth').textContent = `ใช้ราคาเดือน${fmtMonthTh(d.month)}`;
  const q = (document.getElementById('bmcPrepQ').value || '').trim().toLowerCase();
  const rows = d.rows.filter(r => (_bmcPrepView === 'all' || (_bmcPrepView === 'todo' ? (r.status === 'incomplete' || r.status === 'check') : r.status === _bmcPrepView))
    && (!q || (r.code + ' ' + (r.name || '')).toLowerCase().includes(q)));
  // 2026-10-19: a table — each figure in its own column instead of one "· "-joined sentence
  const u = r => esc(r.base_unit || '');
  const diff = r => r.diff_pct == null ? '' : `<b style="color:${Math.abs(r.diff_pct) > d.tolerance_pct ? 'var(--amber-ink)' : 'var(--good-ink)'}">${r.diff_pct > 0 ? '+' : ''}${_bmcNum(r.diff_pct, 1)}%</b>`;
  document.getElementById('bmcPrepList').innerHTML = rows.length ? `<div class="wt-wrap" style="max-height:640px"><table class="wt"><thead><tr>
      <th>รหัส</th><th>วัตถุดิบเตรียม</th><th>สูตร</th><th class="wt-num">ต้นทุนจากสูตร</th><th class="wt-num">ราคาซื้อ</th><th class="wt-num">ต่างกัน</th><th>ต้องแก้</th><th></th></tr></thead><tbody>${rows.map(r => {
    const [label, ink] = _BMC_PREP[r.status];
    return `<tr><td class="wt-code" data-l="รหัส">${esc(r.code)}</td>
      <td data-l="วัตถุดิบเตรียม"><div>${esc(r.name || '')}</div><div class="mt-1" style="display:flex;gap:4px;flex-wrap:wrap">${_bmcChip(label, ink)}${r.mode === 'portion' ? _bmcChip('แบ่งชิ้น', 'var(--navy-ink)') : ''}${r.approx ? _bmcChip('ต้นทุนโดยประมาณ', 'var(--amber-ink)') : ''}</div>
        ${r.source_ref ? `<div class="wt-muted mt-1" style="font-size:13px">ที่มา: ${esc(r.source_ref)}${r.updated_by ? ' (' + esc(whoTh(r.updated_by)) + ')' : ''}</div>` : ''}</td>
      <td data-l="สูตร">${r.has_recipe ? bmcLines([`${r.mode === 'portion' ? 'แบ่งได้' : '1 สูตร ='} ${_bmcNum(r.yield_qty)} ${esc(r.yield_unit || '')}`, `${_bmcNum(r.lines, 0)} วัตถุดิบ`])
        : `<span class="wt-muted">ยังไม่มีสูตร</span>${r.qty_per_recv ? `<div class="wt-muted" style="font-size:13px">ขนาดบรรจุ ${_bmcNum(r.qty_per_recv)} ${u(r)}</div>` : ''}`}</td>
      <td class="wt-num" data-l="ต้นทุนจากสูตร">${r.has_recipe && r.per_base != null ? `${r.approx ? '≈ ' : ''}฿${_bmcPrice(r.per_base)}<span class="wt-muted">/${u(r)}</span>` : '—'}</td>
      <td class="wt-num" data-l="ราคาซื้อ">${r.own_price != null ? `฿${_bmcPrice(r.own_price)}<span class="wt-muted">/${u(r)}</span>` : '<span class="wt-muted">ไม่มีราคา</span>'}</td>
      <td class="wt-num" data-l="ต่างกัน">${r.has_recipe ? diff(r) : ''}</td>
      <td data-l="ต้องแก้" style="color:var(--bad-ink)">${bmcLines((r.problems || []).slice(0, 3).map(esc))}${r.n_problems > 3 ? `<div class="wt-muted">และอีก ${r.n_problems - 3} เรื่อง</div>` : ''}</td>
      <td class="wt-act">${_bmcBtn(r.has_recipe ? 'เปิดสูตร' : (_bmcCanRecipes() ? 'เพิ่มสูตร' : 'ดู'), `bmcOpenPrep(${_bmcQ(r.code)})`)}</td></tr>`;
  }).join('')}</tbody></table></div>` : `<div class="wt-empty" style="color:var(--good-ink)">${_bmcPrepView === 'todo' ? '✓ ไม่มีสูตรที่ต้องทำ' : 'ไม่มีรายการ'}</div>`;
}

async function bmcOpenPrep(code) {
  const box = document.getElementById('bmcPrepDetail');
  box.style.display = '';
  box.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</span>';
  try {
    const d = await _bmcJson(`${API}/api/bom-center/prep/${encodeURIComponent(code)}`);
    _bmcPrepEdit = { code, yield_qty: d.yield_qty || d.qty_per_recv || '', yield_unit: d.yield_unit || d.base_unit || '',
      mode: d.mode === 'portion' ? 'portion' : null, note: d.recipe_note || null,
      lines: (d.lines || []).map(l => ({ ingredient_code: l.ingredient_code || '', qty: l.qty, unit: l.unit || '', note: l.note || '', no_loss: l.no_loss ? 1 : 0, approx: l.approx,
        ingredient_name: l.ingredient_name, cost: l.cost, price_per_base: l.price_per_base, source: l.source, problem: l.problem })), d };
    bmcRenderPrepEdit();
    box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function bmcRenderPrepEdit() {
  const e = _bmcPrepEdit, d = e.d, w = _bmcCanRecipes();
  const box = document.getElementById('bmcPrepDetail');
  const lines = e.lines.map((l, i) => `<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;border-top:1px solid var(--border);padding:5px 0">
      <span style="width:18px;color:var(--text-muted)">${i + 1}</span>
      ${w ? _bmcInput(`list="bmcCodeList" value="${esc(l.ingredient_code)}" placeholder="รหัส" onchange="_bmcPrepEdit.lines[${i}].ingredient_code=this.value.trim()"`, '110px')
          + _bmcInput(`type="number" step="any" value="${l.qty ?? ''}" onchange="_bmcPrepEdit.lines[${i}].qty=this.value"`, '80px')
          + _bmcInput(`value="${esc(l.unit)}" placeholder="หน่วย" onchange="_bmcPrepEdit.lines[${i}].unit=this.value"`, '80px')
        : `<b>${esc(l.ingredient_code || '—')}</b> ${_bmcNum(l.qty)} ${esc(l.unit)}`}
      <span style="color:var(--text-secondary);min-width:120px">${esc(l.ingredient_name || l.note || '')}</span>
      <span style="color:var(--text-muted)">${l.cost != null ? (l.approx ? '≈ ' : '') + '฿' + _bmcNum(l.cost, 2) + (l.source === 'sub_recipe' ? ' (จากสูตรย่อย)' : '') : ''}</span>
      ${l.problem ? `<span style="color:var(--bad-ink)">${esc(l.problem)}</span>` : ''}
      ${w ? `<button onclick="_bmcPrepEdit.lines.splice(${i},1);bmcRenderPrepEdit()" style="margin-left:auto;font-size:11px;color:var(--bad-ink);background:none;border:0;cursor:pointer">ลบ</button>` : ''}
    </div>`).join('');
  const rawRows = d.raw_per_base || [];
  const raw = rawRows.length ? `<div class="wt-wrap mt-1" style="max-height:320px"><table class="wt"><thead><tr><th>รหัส</th><th>วัตถุดิบดิบ</th><th class="wt-num">ปริมาณต่อ 1 ${esc(d.base_unit || 'หน่วย')}</th></tr></thead><tbody>
    ${rawRows.map(x => `<tr><td class="wt-code" data-l="รหัส">${esc(x.code)}</td><td data-l="วัตถุดิบดิบ">${esc(x.name || '')}</td><td class="wt-num" data-l="ปริมาณ">${_bmcNum(x.qty_per_base, 4)}</td></tr>`).join('')}</tbody></table></div>` : '';
  box.innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap">
      <div class="text-sm font-semibold" style="color:var(--text-primary)">${esc(d.code)} ${esc(d.name || '')}</div>
      ${_bmcBtn('ปิด', "document.getElementById('bmcPrepDetail').style.display='none'")}</div>
    <div class="text-sm mt-1" style="color:var(--text-secondary)">ราคาเดือน${fmtMonthTh(d.month)}</div>
    ${bmcStats([['หน่วยฐาน', esc(d.base_unit || '—')], ['ขนาดบรรจุในทะเบียน', d.qty_per_recv ? _bmcNum(d.qty_per_recv) : null],
      ['ราคาซื้อ', d.own_price != null ? '฿' + _bmcPrice(d.own_price) : '—'], ['ต้นทุนจากสูตร', d.per_base != null ? '฿' + _bmcPrice(d.per_base) : '—'],
      ['ต้นทุนต่อ 1 สูตร', d.batch_cost != null && d.has_recipe ? '฿' + _bmcNum(d.batch_cost, 2) : null], ['ไฟล์ครัว', d.source_cost != null ? '฿' + _bmcNum(d.source_cost, 2) : null]])}
    ${e.mode === 'portion' ? `<div class="text-xs mt-1" style="color:var(--text-secondary)">${_bmcChip('แบ่งชิ้น', 'var(--navy-ink)')} จำนวนชิ้นที่ได้นับเฉพาะชิ้นที่ใช้ได้ ต้นทุนจึงไม่บวกส่วนสูญเสียซ้ำ · หัว หนัง และเศษจากวัตถุดิบทั้งตัวไม่คิดต้นทุน ต้นทุนทั้งหมดอยู่ที่ชิ้นที่ได้${e.note ? ' · หมายเหตุ: ' + esc(e.note) : ''}</div>` : ''}
    <div class="flex flex-wrap items-center gap-2 mt-2 text-xs" style="color:var(--text-muted)">${e.mode === 'portion' ? 'แบ่งได้' : '1 สูตร ได้'}
      ${w ? _bmcInput(`type="number" step="any" value="${e.yield_qty}" onchange="_bmcPrepEdit.yield_qty=this.value"`, '100px') + _bmcInput(`value="${esc(e.yield_unit)}" onchange="_bmcPrepEdit.yield_unit=this.value"`, '90px')
        : `<b>${_bmcNum(e.yield_qty)} ${esc(e.yield_unit)}</b>`}</div>
    <div class="text-xs mt-2">${lines || '<span style="color:var(--text-muted)">ยังไม่มีวัตถุดิบในสูตร</span>'}</div>
    ${w ? `<div class="flex flex-wrap gap-2 mt-2">${e.mode === 'portion' ? '' : _bmcBtn('+ เพิ่มวัตถุดิบ', "_bmcPrepEdit.lines.push({ingredient_code:'',qty:'',unit:'',note:''});bmcRenderPrepEdit()")}
      ${_bmcBtn('บันทึกสูตร', 'bmcSavePrep()', true)}<span id="bmcPrepEditStatus" class="text-xs"></span></div>` : ''}
    ${(d.problems || []).length ? `<div class="text-sm font-semibold mt-3" style="color:var(--bad-ink)">ยังคิดต้นทุนจากสูตรไม่ได้ (ใช้ราคาซื้อแทน)</div><div style="color:var(--bad-ink)">${bmcLines(d.problems.slice(0, 5).map(esc))}</div>` : ''}
    ${raw ? `<div class="text-sm font-semibold mt-3" style="color:var(--text-primary)">ใน 1 ${esc(d.base_unit || 'หน่วย')} มีวัตถุดิบดิบ (แผนสั่งใช้ตัวเลขนี้)</div>${raw}` : ''}`;
}
async function bmcSavePrep() {
  const e = _bmcPrepEdit;
  try {
    await _bmcJson(`${API}/api/bom-center/prep/${encodeURIComponent(e.code)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ yield_qty: Number(e.yield_qty), yield_unit: e.yield_unit, mode: e.mode || null, note: e.note || null,
        lines: e.lines.map(l => ({ ingredient_code: l.ingredient_code || null, qty: l.qty === '' ? null : Number(l.qty), unit: l.unit || null, note: l.note || null, no_loss: l.no_loss ? 1 : 0 })) }) });
    await bmcOpenPrep(e.code);
    _bmcStatus('bmcPrepEditStatus', '✓ บันทึกแล้ว', 'var(--good-ink)');
    bmcLoadPrep();
  } catch (err) { _bmcStatus('bmcPrepEditStatus', 'ไม่สำเร็จ: ' + String(err.message || err).slice(0, 200), 'var(--bad-ink)'); }
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
    // 2026-10-19: both lists are tables, the counts are tiles
    const writes = p.writes.length ? `<div class="wt-wrap mt-1" style="max-height:480px"><table class="wt"><thead><tr><th>รหัสในทะเบียน</th><th>จากไฟล์</th><th>วิธีจับคู่</th><th>หมายเหตุ</th></tr></thead><tbody>${p.writes.map(w => `<tr>
        <td data-l="รหัสในทะเบียน"><span class="wt-code">${esc(w.prepared_code)}</span> ${esc(w.prepared_name || '')}</td>
        <td data-l="จากไฟล์">แถว ${w.row}: ${esc(w.src_code)} ${esc(w.src_name)}${w.yield_text ? `<div class="wt-muted" style="font-size:13px">${esc(w.yield_text)}</div>` : ''}</td>
        <td data-l="วิธีจับคู่">${_bmcChip(methodTh[w.method] || w.method, w.score >= 1.1 || w.method === 'map' ? 'var(--good-ink)' : 'var(--amber-ink)')}${w.replaces ? ' ' + _bmcChip('แทนสูตรเดิม', 'var(--text-muted)') : ''}</td>
        <td data-l="หมายเหตุ">${bmcLines([w.portion_skip ? _bmcChip('ข้าม', 'var(--amber-ink)') + ' <span style="color:var(--amber-ink)">' + esc(w.note_th || '') + '</span>' : '',
          w.unresolved ? `<span style="color:var(--bad-ink)">ไม่พบรหัส ${w.unresolved} บรรทัด: ${w.lines.filter(l => !l.ingredient_code).map(l => esc(l.note)).join(', ')}</span>` : ''])}</td></tr>`).join('')}</tbody></table></div>` : '';
    const um = p.unmatched.length ? `<div class="wt-wrap mt-1" style="max-height:360px"><table class="wt"><thead><tr><th>แถว</th><th>สูตรในไฟล์</th><th></th></tr></thead><tbody>${p.unmatched.map(u => `<tr>
        <td class="wt-num" data-l="แถว">${u.row}</td><td data-l="สูตรในไฟล์"><span class="wt-code">${esc(u.src_code)}</span> ${esc(u.name)}${u.yield_text ? `<div class="wt-muted" style="font-size:13px">${esc(u.yield_text)}</div>` : ''}</td>
        <td class="wt-act">${_bmcCanRecipes() ? `<div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;align-items:center">${_bmcInput(`list="bmcCodeList" placeholder="รหัสในทะเบียน" id="bmcPrepMap_${u.row}"`, '130px')}
          ${_bmcBtn('จับคู่', `bmcPrepMap(${_bmcQ(u.name)}, document.getElementById('bmcPrepMap_${u.row}').value)`)}${_bmcBtn('ไม่ใช่รายการของร้าน', `bmcPrepMap(${_bmcQ(u.name)}, '-')`)}</div>` : ''}</td></tr>`).join('')}</tbody></table></div>` : '';
    box.innerHTML = `<div class="text-sm font-semibold" style="color:var(--text-primary)">ดูตัวอย่างก่อนบันทึก: ${esc(_bmcPrepFile.name)} · แผ่นงาน ${esc(p.sheet || '')}</div>
      ${bmcStats([['สูตรในไฟล์', p.rows], ['บันทึกได้ (รหัสในทะเบียน)', p.writes.length, 'var(--good-ink)'], ['ไม่พบรหัสวัตถุดิบ (บรรทัด)', p.unresolved_lines, p.unresolved_lines ? 'var(--amber-ink)' : null],
        ['สูตรที่ยังจับคู่รหัสไม่ได้', p.unmatched.length, p.unmatched.length ? 'var(--amber-ink)' : null], ['รหัสกึ่งสำเร็จที่ยังไม่มีสูตร', p.prepared_without.length]])}
      <div class="wt-note">จับคู่ด้วยชื่อและปริมาณที่ได้ต่อสูตร ไม่ใช้รหัสในไฟล์เนื่องจากเป็นชุดรหัสเก่า · บรรทัดที่ไม่พบรหัสวัตถุดิบบันทึกได้ และระบุรหัสภายหลังในหน้าสูตร</div>
      ${_bmcCanRecipes() ? `<div class="flex gap-2 my-2">${_bmcBtn(`บันทึก ${p.writes.filter(w => !w.portion_skip).length} สูตร`, 'bmcPrepApply()', true)}${_bmcBtn('ยกเลิก', "document.getElementById('bmcPrepImport').style.display='none'")}<span id="bmcPrepApplyStatus" class="text-xs"></span></div>` : ''}
      ${um ? `<div class="text-sm font-semibold mt-3" style="color:var(--text-primary)">สูตรในไฟล์ที่ยังจับคู่รหัสไม่ได้: ระบุรหัส หรือกด “ไม่ใช่รายการของร้าน”</div>${um}` : ''}
      <div class="text-sm font-semibold mt-3" style="color:var(--text-primary)">รายการที่จะบันทึก (ป้ายสีเหลือง = ชื่อไม่ตรงกันทั้งหมด ควรตรวจ)</div>${writes}`;
  } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
async function bmcPrepMap(name, code) {
  code = (code || '').trim();
  if (!code) return;
  try {
    await _bmcJson(`${API}/api/bom-center/prep/map`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ src_name: name, prepared_code: code }) });
    await bmcPrepPreview();
  } catch (e) { alert('ไม่สำเร็จ: ' + String(e.message || e)); }
}
async function bmcPrepApply() {
  try {
    const fd = new FormData(); fd.append('file', _bmcPrepFile, _bmcPrepFile.name);
    const r = await _bmcJson(`${API}/api/bom-center/prep/import-semi?dry_run=false`, { method: 'POST', body: fd });
    document.getElementById('bmcPrepImport').style.display = 'none';
    _bmcStatus('bmcPrepStatus', `✓ บันทึกสูตรกึ่งสำเร็จ ${r.saved} รหัส` + ((r.skipped || []).length ? ` · ข้ามสูตรแบ่งชิ้น ${r.skipped.length} รหัส (แก้ไขที่การ์ดสูตรแบ่งชิ้น)` : ''), 'var(--good-ink)');
    bmcLoadPrep();
  } catch (e) { _bmcStatus('bmcPrepApplyStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
function bmcPrepTemplate() { bmcDownload(`${API}/api/bom-center/prep/template.xlsx`, 'สูตรกึ่งสำเร็จ.xlsx', 'bmcPrepStatus'); }
async function bmcPrepImportFlat(input) {
  const file = input?.files?.[0];
  if (!file) return;
  input.value = '';
  const send = dry => { const fd = new FormData(); fd.append('file', file, file.name);
    return _bmcJson(`${API}/api/bom-center/prep/import-flat?dry_run=${dry}`, { method: 'POST', body: fd }); };
  try {
    const pre = await send(true);
    if (pre.errors.length) { _bmcStatus('bmcPrepStatus', 'ไฟล์มีปัญหา: ' + pre.errors.slice(0, 3).join(' · '), 'var(--bad-ink)'); return; }
    const sk = pre.skipped || [];
    if (!await uiConfirm(`นำเข้าแม่แบบ: สูตรของ ${pre.codes - sk.length} รหัสจะถูกแทนที่`
      + (sk.length ? `\n${sk.length} รหัสเป็นสูตรแบ่งชิ้น จะข้าม (${sk.map(x => x.code).join(', ')})\n${sk[0].note}` : ''),
      { title: 'ยืนยันการนำเข้าแม่แบบสูตรกึ่งสำเร็จ', ok: 'นำเข้า', cancel: 'ยกเลิก' })) return;
    const r = await send(false);
    _bmcStatus('bmcPrepStatus', `✓ บันทึก ${r.saved} รหัส` + (sk.length ? ` · ข้ามสูตรแบ่งชิ้น ${sk.length} รหัส` : ''), 'var(--good-ink)');
    bmcLoadPrep();
  } catch (e) { _bmcStatus('bmcPrepStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}

// ── unit converter ──────────────────────────────────────────────────────────
// The unit ladder (PR-U): up to 3 units above the base unit, each stated against the one below it. The editor shows the
// factor of every row live; the server (units.check_ladder / climb) is the authority and its reasons are shown as they are.
let _bmcLad = null;           // {code, name, base_unit, recv_unit, qty_per_recv, rows:[{unit, qty, parent, kind, rmin, rmax}], problems:[]}
const _BMC_METRIC = {          // the universal steps of a base unit (mirrors units._METRIC; the server decides)
  กรัม: { กิโลกรัม: 1000, ขีด: 100 }, มิลลิลิตร: { ลิตร: 1000 }, กิโลกรัม: { กรัม: 0.001 }, ลิตร: { มิลลิลิตร: 0.001 }, ขีด: { กิโลกรัม: 10 } };
const _BMC_MAX_STEPS = 3;

async function bmcLoadConv() {
  if (!_bmcIng.length) await bmcLoadIng(true);
  const dl = document.getElementById('bmcCodeList');
  if (dl && !dl.children.length) dl.innerHTML = _bmcIng.map(r => `<option value="${esc(r.code)}">${esc(r.name_th || '')}</option>`).join('');
  bmcLoadLadder();
  bmcLoadAvg();
  bmcLoadPack();
}
function _bmcLadCode() {
  const t = (document.getElementById('bmcConvCode').value || '').trim();
  if (!t) return '';
  const hit = _bmcIng.find(x => x.code === t) || _bmcIng.find(x => (x.name_th || '') === t);
  return hit ? hit.code : t;
}
async function bmcLoadLadder() {
  const box = document.getElementById('bmcLadder');
  const code = _bmcLadCode();
  if (!code) { _bmcLad = null; box.innerHTML = '<span style="color:var(--text-muted)">ระบุรหัสหรือชื่อวัตถุดิบด้านบนเพื่อดูและแก้บันไดหน่วย</span>'; return; }
  try {
    const d = await _bmcJson(`${API}/api/bom-center/units/ladder?code=${encodeURIComponent(code)}`);
    _bmcLad = { code: d.code, name: d.name, base_unit: d.base_unit, recv_unit: d.recv_unit, recv_word: d.recv_word, qty_per_recv: d.qty_per_recv,
      rows: d.ladder.map(r => ({ unit: r.unit, qty: r.qty_in_parent, parent: r.parent_unit === d.base_unit ? '' : r.parent_unit,
        kind: r.kind, rmin: r.range_min ?? '', rmax: r.range_max ?? '', how: r.how, approx: r.approx })),
      problems: d.problems || [], server: '' };
    bmcRenderLadder();
  } catch (e) { _bmcLad = null; box.innerHTML = `<span style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
// the factor of one row to the base unit, worked out in the browser while typing: {f, approx} or null
function _bmcLadFactor(L, i, depth = 0) {
  const r = L.rows[i];
  const q = Number(r.qty);
  if (!(q > 0) || depth > _BMC_MAX_STEPS) return null;
  const pu = (r.parent || '').trim();
  const avg = r.kind === 'avg';
  if (!pu || pu === L.base_unit) return { f: q, approx: avg };
  const j = L.rows.findIndex((x, k) => k !== i && (x.unit || '').trim() === pu);
  if (j >= 0) { const p = _bmcLadFactor(L, j, depth + 1); return p ? { f: q * p.f, approx: avg || p.approx } : null; }
  const m = (_BMC_METRIC[L.base_unit] || {})[pu];
  return m ? { f: q * m, approx: avg } : null;
}
function bmcRenderLadder() {
  const L = _bmcLad, box = document.getElementById('bmcLadder');
  if (!L) return;
  const w = _bmcCanPrices();
  const metric = Object.keys(_BMC_METRIC[L.base_unit] || {});
  const sel = (i, cur) => {
    const opts = [L.base_unit, ...metric, ...L.rows.filter((x, k) => k !== i && (x.unit || '').trim()).map(x => x.unit.trim())];
    return `<select ${w ? '' : 'disabled'} onchange="_bmcLad.rows[${i}].parent=this.value===_bmcLad.base_unit?'':this.value;bmcRenderLadder()"
      style="border:1px solid var(--border-control);border-radius:var(--r-sm);padding:3px 6px;font-size:12px;background:var(--bg-input);color:var(--text-primary)">${
      [...new Set(opts)].map(o => `<option value="${esc(o)}"${(cur || L.base_unit) === o ? ' selected' : ''}>${esc(o)}${o === L.base_unit ? ' (หน่วยฐาน)' : ''}</option>`).join('')}</select>`;
  };
  const rows = L.rows.map((r, i) => {
    const fx = _bmcLadFactor(L, i);
    const avg = r.kind === 'avg';
    return `<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;border-top:1px solid var(--border);padding:6px 0">
      ${w ? _bmcInput(`value="${esc(r.unit)}" placeholder="หน่วย เช่น ตัว" onchange="_bmcLad.rows[${i}].unit=this.value.trim();bmcRenderLadder()"`, '90px') : `<b>${esc(r.unit)}</b>`}
      <span style="color:var(--text-muted)">1 หน่วยนี้ =</span>
      ${w ? _bmcInput(`type="number" step="any" min="0" value="${r.qty ?? ''}" onchange="_bmcLad.rows[${i}].qty=this.value;bmcRenderLadder()"`, '80px') : _bmcNum(r.qty, 4)}
      ${sel(i, r.parent)}
      <select ${w ? '' : 'disabled'} onchange="_bmcLad.rows[${i}].kind=this.value;bmcRenderLadder()" style="border:1px solid var(--border-control);border-radius:var(--r-sm);padding:3px 6px;font-size:12px;background:var(--bg-input);color:var(--text-primary)">
        <option value="exact"${avg ? '' : ' selected'}>แน่นอน</option><option value="avg"${avg ? ' selected' : ''}>เฉลี่ย</option></select>
      ${avg ? `<span style="color:var(--text-muted)">ช่วง</span>${w ? _bmcInput(`type="number" step="any" min="0" value="${r.rmin ?? ''}" placeholder="ต่ำสุด" onchange="bmcLadRange(${i},'rmin',this.value)"`, '70px')
          + '–' + _bmcInput(`type="number" step="any" min="0" value="${r.rmax ?? ''}" placeholder="สูงสุด" onchange="bmcLadRange(${i},'rmax',this.value)"`, '70px')
          : `${_bmcNum(r.rmin, 4)}–${_bmcNum(r.rmax, 4)}`}` : ''}
      <span style="color:var(--text-primary)">${fx ? `= <b>${fx.approx ? '≈ ' : ''}${_bmcNum(fx.f, 4)}</b> ${esc(L.base_unit || '')}` : '<span style="color:var(--text-muted)">= —</span>'}</span>
      ${w ? `<button onclick="_bmcLad.rows.splice(${i},1);bmcRenderLadder()" style="margin-left:auto;font-size:11px;color:var(--bad-ink);background:none;border:0;cursor:pointer">ลบ</button>` : ''}
    </div>`;
  }).join('');
  // PR-W: the register's receiving unit may carry its contents ("กล่อง (10 แพ็ค)"); the unit in the ladder is its first word ("กล่อง")
  const recvWord = (L.recv_word || L.recv_unit || '').trim(), recvLbl = (L.recv_unit || '').trim();
  const recvHit = L.rows.findIndex(r => (r.unit || '').trim() && (r.unit.trim() === recvWord || r.unit.trim() === recvLbl));
  const problems = [...(L.problems || []), ...(L.server ? [L.server] : [])];
  box.innerHTML = `<div style="color:var(--text-secondary)"><b>${esc(L.code)}</b> ${esc(L.name || '')} · หน่วยฐาน <b>${esc(L.base_unit || '—')}</b>` +
    (L.qty_per_recv ? ` · 1 ${esc(recvWord || 'หน่วยรับ')} = ${_bmcNum(L.qty_per_recv)} ${esc(L.base_unit || '')}${recvWord && recvLbl && recvWord !== recvLbl ? ` <span style="color:var(--text-muted)">(ทะเบียนเขียนว่า “${esc(recvLbl)}”)</span>` : ''}` : ' · ยังไม่มีขนาดบรรจุ') + `</div>` +
    (L.rows.length ? rows : '<div style="color:var(--text-muted)" class="mt-1">ยังไม่มีบันไดหน่วย · กิโลกรัมเป็นกรัม และลิตรเป็นมิลลิลิตร แปลงเองอยู่แล้ว</div>') +
    (recvHit >= 0 ? `<div class="mt-1" style="color:var(--text-muted)">หน่วยรับ “${esc(recvWord)}” อยู่ในบันได ขนาดบรรจุในทะเบียนจะปรับตามเมื่อบันทึก</div>` : '') +
    (problems.length ? `<div class="mt-2" style="color:var(--bad-ink)" role="alert">${problems.map(esc).join('<br>')}</div>` : '') +
    (w ? `<div class="flex flex-wrap items-center gap-2 mt-2">
      ${L.rows.length < _BMC_MAX_STEPS ? _bmcBtn('เพิ่มขั้น', "bmcLadAdd()") : ''}
      ${_bmcBtn('บันทึกบันไดหน่วย', 'bmcLadSave()', true)}<span id="bmcLadStatus" class="text-xs"></span></div>` : '');
}
function bmcLadAdd() {
  if (_bmcLad.rows.length >= _BMC_MAX_STEPS) return;
  _bmcLad.rows.push({ unit: '', qty: '', parent: '', kind: 'exact', rmin: '', rmax: '' });
  bmcRenderLadder();
}
// a range typed for an average fills the average with its midpoint while the average is still empty
function bmcLadRange(i, key, v) {
  const r = _bmcLad.rows[i];
  r[key] = v;
  const a = Number(r.rmin), b = Number(r.rmax);
  if ((r.qty === '' || r.qty == null) && r.rmin !== '' && r.rmax !== '' && a > 0 && b > 0) r.qty = String((a + b) / 2);
  bmcRenderLadder();
}
async function bmcLadSave() {
  const L = _bmcLad;
  if (!L) return;
  const rows = L.rows.filter(r => (r.unit || '').trim() || r.qty !== '').map(r => ({
    unit: (r.unit || '').trim(), qty_in_parent: r.qty === '' ? null : Number(r.qty), parent_unit: r.parent || null, conv_kind: r.kind || 'exact',
    range_min: r.kind === 'avg' && r.rmin !== '' ? Number(r.rmin) : null, range_max: r.kind === 'avg' && r.rmax !== '' ? Number(r.rmax) : null }));
  const lines = rows.map(r => `1 ${r.unit} = ${r.qty_in_parent ?? '?'} ${r.parent_unit || L.base_unit}${r.conv_kind === 'avg' ? ' (เฉลี่ย)' : ''}`);
  if (!await uiConfirm(`บันทึกบันไดหน่วยของ ${L.code} ${L.name || ''}\n${lines.join('\n') || 'ไม่มีหน่วย (ลบบันไดหน่วยทั้งหมด)'}`,
    { title: 'ยืนยันการบันทึกบันไดหน่วย', ok: 'บันทึก', cancel: 'ยกเลิก' })) return;
  try {
    const r = await _bmcJson(`${API}/api/bom-center/units/ladder`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: L.code, rows }) });
    await bmcLoadIng(true);
    await bmcLoadLadder();
    bmcStatus('bmcLadStatus', true, 'บันทึกบันไดหน่วยแล้ว' + (r.qty_per_recv_change ? ` · ขนาดบรรจุปรับเป็น ${_bmcNum(r.qty_per_recv_change.to, 4)}` : ''));
  } catch (e) {
    L.server = String(e.message || e);
    bmcRenderLadder();
  }
}
async function bmcConvert() {
  const code = _bmcLadCode();
  const qty = document.getElementById('bmcConvQty').value || 1;
  const unit = (document.getElementById('bmcConvUnit').value || '').trim();
  const out = document.getElementById('bmcConvOut');
  if (!code || !unit) { out.textContent = 'ระบุรหัสและหน่วย'; return; }
  try {
    const d = await _bmcJson(`${API}/api/bom-center/convert?code=${encodeURIComponent(code)}&qty=${qty}&unit=${encodeURIComponent(unit)}&month=${_bmcMonth()}`);
    const ap = d.approx ? '≈ ' : '';
    let back = '';
    if (d.qty_base != null && d.qty_base > 0) {                          // and the way back: this much of the base unit in each unit of the ladder
      const L = _bmcLad && _bmcLad.code === code ? _bmcLad : null;
      const f = x => `${_bmcNum(d.qty_base / x, 4)}`;
      if (L) back = L.rows.map((r, i) => { const fx = _bmcLadFactor(L, i); return fx && r.unit !== d.unit ? ` · ${fx.approx ? '≈ ' : ''}${f(fx.f)} ${esc(r.unit)}` : ''; }).join('');
    }
    out.innerHTML = d.qty_base == null
      ? `<span style="color:var(--amber-ink)">แปลงหน่วยไม่ได้: ${esc(d.how)}</span>`
      : `${_bmcNum(d.qty)} ${esc(d.unit)} = <b>${ap}${_bmcNum(d.qty_base, 3)} ${esc(d.base_unit)}</b>${d.range_base ? ` <span style="color:var(--text-muted)">(ช่วง ${_bmcNum(d.range_base[0], 1)}–${_bmcNum(d.range_base[1], 1)})</span>` : ''}${back ? `<span style="color:var(--text-secondary)">${back}</span>` : ''}
        <div class="text-xs" style="color:var(--text-muted)">${esc(d.how)}</div>` +
        (d.cost != null ? `<div>ต้นทุน <b>${ap}฿${_bmcNum(d.cost, 3)}</b> ที่ราคา ${_bmcPrice(d.price_per_base)}/${esc(d.base_unit)} เดือน${fmtMonthTh(d.month)}</div>` : '<div>ยังไม่มีราคา</div>');
  } catch (e) { out.innerHTML = `<span style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}

// ── PR-U2 · ค่าเฉลี่ยจากการรับจริง: the average weight per piece the branches' receipts show ─────────────────
// The branch counts the pieces it receives next to the weight; the server works out Σ weight ÷ Σ pieces (90 days, every branch) and
// proposes it when there are enough receipts and it differs. The analyst uses it or keeps the old value with a reason — nothing changes silently.
let _bmcAvgRows = {};
const _BMC_SHORT = { กิโลกรัม: 'กก.', มิลลิลิตร: 'มล.' };
async function bmcLoadAvg() {
  const box = document.getElementById('bmcAvgBody'), hist = document.getElementById('bmcAvgHist');
  if (!box) return;
  try {
    const d = await _bmcJson(`${API}/api/bom-center/units/avg-proposals?days=90`);
    const w = _bmcCanPrices();
    _bmcAvgRows = {};
    d.rows.forEach(r => { _bmcAvgRows[r.code + '|' + r.unit] = r; });
    const su = u => _BMC_SHORT[u] || u || '';
    const rows = d.rows.map(r => {
      const rg = r.range ? ` (ช่วง ${_bmcNum(r.range[0], 2)}–${_bmcNum(r.range[1], 2)})` : '';
      const obs = r.observed == null
        ? '<span style="color:var(--text-muted)">ยังไม่มีการนับจำนวนตอนรับ</span>'
        : `<b>≈ ${_bmcNum(r.observed, 2)} ${esc(su(r.parent))}</b><div style="color:var(--text-muted)">${r.n_receipts} ครั้ง · ${_bmcNum(r.n_pieces, 0)} ${esc(r.unit)} · ต่อครั้ง ${_bmcNum(r.min_receipt_avg, 2)}–${_bmcNum(r.max_receipt_avg, 2)}</div>`;
      const state = r.proposal ? '' : !r.enough ? `<div style="color:var(--amber-ink)">${esc(r.need_text)}</div>`
        : r.state === 'decided' ? '<div style="color:var(--text-muted)">ตัดสินใจแล้ว รอการรับครั้งใหม่</div>' : '<div style="color:var(--text-muted)">ใกล้เคียงค่าที่ใช้</div>';
      const diff = r.diff_pct == null ? '—' : `<b style="color:${r.proposal ? 'var(--amber-ink)' : 'var(--text-secondary)'}">${Math.abs(r.diff_pct) < 0.05 ? '0' : (r.diff_pct > 0 ? '+' : '') + _bmcNum(r.diff_pct, 1)}%</b>`;
      const btn = r.proposal && w ? `<div class="flex flex-wrap gap-1">${_bmcBtn('ใช้ค่านี้', `bmcAvgDecide(${_bmcQ(r.code)},${_bmcQ(r.unit)},'applied')`, true)}${_bmcBtn('คงค่าเดิม', `bmcAvgDecide(${_bmcQ(r.code)},${_bmcQ(r.unit)},'kept')`)}</div>` : '';
      return `<tr style="border-top:1px solid var(--border);vertical-align:top"${r.proposal ? ' class="bmc-avg-proposal"' : ''}>
        <td class="py-1 pr-2"><b style="color:var(--text-primary)">${esc(r.name || r.code)}</b><div style="color:var(--text-muted)">${esc(r.code)}</div></td>
        <td class="py-1 pr-2">${esc(r.unit)}</td>
        <td class="py-1 pr-2">${_bmcNum(r.current, 2)} ${esc(su(r.parent))}${rg}</td>
        <td class="py-1 pr-2">${obs}${state}</td><td class="py-1 pr-2">${diff}</td><td class="py-1">${btn}</td></tr>`;
    }).join('');
    box.innerHTML = d.rows.length
      ? `<div class="mb-2">${d.proposals ? `มี ${d.proposals} รายการที่ค่าจากการรับจริงต่างจากค่าที่ใช้ รอฝ่ายวิเคราะห์ตัดสินใจ` : 'ยังไม่มีรายการที่ต้องตัดสินใจ'} <span id="bmcAvgStatus" class="ml-2"></span></div>
        <div style="overflow-x:auto"><table class="w-full" style="min-width:560px"><thead><tr class="text-left" style="color:var(--text-muted)">
          <th class="pr-2">วัตถุดิบ</th><th class="pr-2">หน่วย</th><th class="pr-2">ค่าที่ใช้ (ช่วง)</th><th class="pr-2">จากการรับจริง</th><th class="pr-2">ต่าง %</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`
      : 'ยังไม่มีวัตถุดิบที่ตั้งหน่วยแบบเฉลี่ย (เช่น 1 ตัว ≈ 4.5 กิโลกรัม) · ตั้งที่บันไดหน่วยด้านบน แล้วให้สาขานับจำนวนตัวตอนรับสินค้า';
    if (hist) hist.innerHTML = d.history.length ? `<div class="wt-wrap" style="max-height:360px"><table class="wt"><thead><tr><th>วันที่</th><th>วัตถุดิบ</th><th>หน่วย</th><th>ผล</th><th class="wt-num">ค่าที่ใช้ → รับจริง</th><th>จำนวนที่รับ</th><th>ผู้ตัดสิน</th></tr></thead><tbody>
      ${d.history.map(h => `<tr><td data-l="วันที่" style="white-space:nowrap">${esc(fmtDayAt(h.decided_at || ''))}</td><td data-l="วัตถุดิบ" style="font-weight:600">${esc(h.name || h.ingredient_code)}</td><td data-l="หน่วย">${esc(h.unit)}</td>
        <td data-l="ผล">${h.decision === 'applied' ? _bmcChip('ใช้ค่าที่รับจริง', 'var(--good-ink)') : _bmcChip('คงค่าเดิม', 'var(--text-muted)')}${h.note ? `<div class="wt-muted" style="font-size:13px">${esc(h.note)}</div>` : ''}</td>
        <td class="wt-num" data-l="ค่าที่ใช้ → รับจริง">${_bmcNum(h.current_avg, 2)} → ≈ ${_bmcNum(h.observed_avg, 2)}</td><td data-l="จำนวนที่รับ">${h.n_receipts} ครั้ง · ${_bmcNum(h.n_pieces, 0)} ${esc(h.unit)}</td>
        <td class="wt-muted" data-l="ผู้ตัดสิน">${esc(whoTh(h.decided_by))}</td></tr>`).join('')}</tbody></table></div>`
      : '<span style="color:var(--text-muted)">ยังไม่มีการตัดสินใจ</span>';
  } catch (e) { box.innerHTML = `<span style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
async function bmcAvgDecide(code, unit, decision) {
  const r = _bmcAvgRows[code + '|' + unit];
  if (!r) return;
  let note = null;
  if (decision === 'kept') {
    note = await uiPrompt(`เหตุผลที่คงค่าเดิมของ ${r.name || code} (จำเป็น)`, '', { title: 'คงค่าเดิม', okText: 'คงค่าเดิม' });
    if (!note || !String(note).trim()) return;
  } else if (!await uiConfirm(`ใช้ค่าที่รับจริงของ ${r.name || code}\n1 ${unit} จาก ${_bmcNum(r.current, 2)} เป็น ${_bmcNum(r.observed, 2)} ${_BMC_SHORT[r.parent] || r.parent}\nต้นทุนของเดือนที่ยังไม่ประกาศใช้เปลี่ยนตาม · เดือนที่ประกาศใช้แล้วไม่เปลี่ยน`,
    { title: 'ยืนยันการใช้ค่าเฉลี่ยใหม่', ok: 'ใช้ค่านี้', cancel: 'ยกเลิก' })) return;
  try {
    const out = await _bmcJson(`${API}/api/bom-center/units/avg-decision`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, unit, decision, note }) });
    await bmcLoadAvg();
    bmcStatus('bmcAvgStatus', true, String(out.message || '').replace(/^✓\s*/, ''));
    if (decision === 'applied') { await bmcLoadIng(true); bmcLoadLadder(); }
  } catch (e) { bmcStatus('bmcAvgStatus', false, String(e.message || e).slice(0, 220)); }
}

// ── PR-U2 · ขนาดบรรจุที่สาขาแจ้ง: "ขนาดบรรจุไม่ตรง" reported from the receive form, grouped by item × unit × reported size ──
async function bmcLoadPack() {
  const box = document.getElementById('bmcPackBody'), hist = document.getElementById('bmcPackHist');
  if (!box) return;
  try {
    const [p, a] = await Promise.all([_bmcJson(`${API}/api/bom-center/units/pack-reports?status=pending`), _bmcJson(`${API}/api/bom-center/units/pack-reports?status=all`)]);
    const w = _bmcCanPrices();
    const rows = p.groups.map(g => `<tr style="border-top:1px solid var(--border);vertical-align:top">
        <td class="py-1 pr-2"><b style="color:var(--text-primary)">${esc(g.name || g.ingredient_code)}</b><div style="color:var(--text-muted)">${esc(g.ingredient_code)}</div></td>
        <td class="py-1 pr-2">${esc(g.system_text)}</td><td class="py-1 pr-2"><b>${esc(g.reported_text)}</b></td>
        <td class="py-1 pr-2">${g.n_branches} สาขา / ${g.n_reports} ครั้ง</td>
        <td class="py-1 pr-2">${g.branches.map(b => `${esc(b.branch)} ${esc(fmtDay(b.date))}`).join('<br>')}</td>
        <td class="py-1 pr-2">${g.notes.length ? g.notes.map(n => `${esc(n.branch)}: ${esc(n.note)}`).join('<br>') : '—'}</td>
        <td class="py-1">${w ? `<div class="flex flex-wrap gap-1">${_bmcBtn('ใช้ค่านี้', `bmcPackDecide(${_bmcQ(g.report_ids)},'applied',${_bmcQ(g.name || g.ingredient_code)},${_bmcQ(g.reported_text)})`, true)}${_bmcBtn('ไม่ใช้', `bmcPackDecide(${_bmcQ(g.report_ids)},'rejected',${_bmcQ(g.name || g.ingredient_code)},${_bmcQ(g.reported_text)})`)}</div>` : ''}</td></tr>`).join('');
    box.innerHTML = p.groups.length
      ? `<div class="mb-2">รอตัดสินใจ ${p.groups.length} เรื่อง · ตรวจกับฉลากที่สาขาถ่ายส่งทางไลน์ แล้วเลือกใช้ค่าที่แจ้งหรือไม่ใช้ <span id="bmcPackStatus" class="ml-2"></span></div>
        <div style="overflow-x:auto"><table class="w-full" style="min-width:680px"><thead><tr class="text-left" style="color:var(--text-muted)">
          <th class="pr-2">วัตถุดิบ</th><th class="pr-2">ในระบบ</th><th class="pr-2">สาขาแจ้ง</th><th class="pr-2">จำนวน</th><th class="pr-2">สาขา วันที่</th><th class="pr-2">หมายเหตุ</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`
      : '<span id="bmcPackStatus"></span>ไม่มีเรื่องที่รอตัดสินใจ · สาขาแจ้งได้จากฟอร์มรับสินค้า ปุ่ม “ขนาดบรรจุไม่ตรง”';
    const done = a.groups.filter(g => g.status !== 'pending');
    if (hist) hist.innerHTML = done.length ? `<div class="wt-wrap" style="max-height:360px"><table class="wt"><thead><tr><th>วัตถุดิบ</th><th>ในระบบ</th><th>สาขาแจ้ง</th><th>ผล</th><th class="wt-num">สาขา</th></tr></thead><tbody>
      ${done.slice(0, 40).map(g => `<tr><td data-l="วัตถุดิบ" style="font-weight:600">${esc(g.name || g.ingredient_code)}</td><td data-l="ในระบบ">${esc(g.system_text)}</td><td data-l="สาขาแจ้ง">${esc(g.reported_text)}</td>
        <td data-l="ผล">${g.status === 'applied' ? _bmcChip('ใช้ค่าที่แจ้ง', 'var(--good-ink)') : _bmcChip('ไม่ใช้', 'var(--text-muted)')}${g.decision_note ? `<div class="wt-muted" style="font-size:13px">${esc(g.decision_note)}</div>` : ''}</td>
        <td class="wt-num" data-l="สาขา">${g.n_branches}</td></tr>`).join('')}</tbody></table></div>`
      : '<span style="color:var(--text-muted)">ยังไม่มีเรื่องที่ตัดสินใจแล้ว</span>';
  } catch (e) { box.innerHTML = `<span style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
async function bmcPackDecide(ids, decision, name, text) {
  let note = null;
  if (decision === 'rejected') {
    note = await uiPrompt(`เหตุผลที่ไม่ใช้ค่าที่แจ้งของ ${name} (จำเป็น · สาขาอ่านได้ที่ประวัติรับสินค้า)`, '', { title: 'ไม่ใช้ค่าที่สาขาแจ้ง', okText: 'ไม่ใช้' });
    if (!note || !String(note).trim()) return;
  } else if (!await uiConfirm(`ปรับทะเบียนของ ${name} เป็น ${text}\nบันไดหน่วยของรายการนี้เปลี่ยน ต้นทุนของเดือนที่ยังไม่ประกาศใช้เปลี่ยนตาม`,
    { title: 'ยืนยันการปรับขนาดบรรจุ', ok: 'ใช้ค่านี้', cancel: 'ยกเลิก' })) return;
  try {
    const out = await _bmcJson(`${API}/api/bom-center/units/pack-report/decide`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ report_ids: ids, decision, note }) });
    await bmcLoadPack();
    bmcStatus('bmcPackStatus', true, String(out.message || '').replace(/^✓\s*/, ''));
    if (decision === 'applied') { await bmcLoadIng(true); bmcLoadLadder(); }
  } catch (e) { bmcStatus('bmcPackStatus', false, String(e.message || e).slice(0, 220)); }
}

// ── the unit ladder as an Excel file ────────────────────────────────────────
let _bmcUnitsFile = null;
function bmcUnitsTemplate(all) { bmcDownload(`${API}/api/bom-center/units/template.xlsx?all=${all ? 1 : 0}`, 'แม่แบบหน่วย.xlsx', 'bmcUnitsStatus'); }
async function bmcUnitsImportPick(input) {
  const file = input?.files?.[0];
  if (!file) return;
  _bmcUnitsFile = file;
  input.value = '';
  const box = document.getElementById('bmcUnitsImport');
  box.style.display = '';
  box.innerHTML = '<span style="color:var(--text-muted)">กำลังอ่านไฟล์…</span>';
  try {
    const fd = new FormData(); fd.append('file', file, file.name);
    const p = await _bmcJson(`${API}/api/bom-center/units/import?dry_run=true`, { method: 'POST', body: fd });
    const lad = p.ladders.length ? `<div class="wt-wrap mt-2" style="max-height:480px"><table class="wt"><thead><tr><th>รหัส</th><th>วัตถุดิบ</th><th>การเปลี่ยนแปลง</th><th>บันไดหน่วย</th><th>ต้องแก้</th></tr></thead><tbody>${p.ladders.map(c => `<tr>
        <td class="wt-code" data-l="รหัส">${esc(c.code)}${c.file_code ? `<div class="wt-muted" style="font-size:13px;font-weight:400">ในไฟล์ ${esc(c.file_code)}</div>` : ''}</td>
        <td data-l="วัตถุดิบ">${esc(c.name || '')}<div class="wt-muted" style="font-size:13px">หน่วยฐาน ${esc(c.base_unit || '—')}</div></td>
        <td data-l="การเปลี่ยนแปลง"><div style="display:flex;gap:4px;flex-wrap:wrap">${c.changes.map(x => _bmcChip({ add: 'เพิ่ม', edit: 'แก้', remove: 'ลบ' }[x.change] + ' ' + x.unit, 'var(--text-muted)')).join('')}
          ${c.qty_per_recv_change ? _bmcChip(`ขนาดบรรจุ ${_bmcNum(c.qty_per_recv_change.from, 4)} → ${_bmcNum(c.qty_per_recv_change.to, 4)}`, 'var(--amber-ink)') : ''}</div></td>
        <td data-l="บันไดหน่วย">${bmcLines(c.ladder.map(r => `${esc(r.how)} = ${r.approx ? '≈ ' : ''}${_bmcNum(r.factor_to_base, 4)} ${esc(c.base_unit || '')}`))}</td>
        <td data-l="ต้องแก้" style="color:var(--bad-ink)">${bmcLines(c.problems.map(esc))}</td></tr>`).join('')}</tbody></table></div>` : '';
    const port = p.portion_rows.length ? `<div class="wt-wrap mt-1" style="max-height:400px"><table class="wt"><thead><tr><th class="wt-num">แถว</th><th>รหัสที่ได้</th><th>จากวัตถุดิบ</th><th>ได้</th><th>ชิ้นละ / ต้นทุน</th></tr></thead><tbody>${p.portion_rows.map(c => `<tr>
        <td class="wt-num" data-l="แถว">${c.row}</td><td data-l="รหัสที่ได้"><span class="wt-code">${esc(c.prepared_code)}</span> ${esc(c.prepared_name || '')}${c.created ? ' ' + _bmcChip('สร้างรหัสใหม่', 'var(--navy-ink)') : ''}${c.replaced ? ' ' + _bmcChip('แทนสูตรเดิม', 'var(--text-muted)') : ''}</td>
        <td data-l="จากวัตถุดิบ"><span class="wt-code">${esc(c.raw_code)}</span> ${_bmcNum(c.qty, 4)} ${esc(c.unit)}</td><td data-l="ได้">${_bmcNum(c.pieces, 4)} ${esc(c.out_unit)}</td>
        <td data-l="ชิ้นละ / ต้นทุน">${c.problems.length ? `<span style="color:var(--bad-ink)">${bmcLines(c.problems.map(esc))}</span>`
          : bmcLines([`${c.approx ? '≈ ' : ''}${_bmcNum(c.base_per_piece, 2)} ${esc(c.base_unit || '')}`, c.cost_per_piece != null ? `${c.approx ? '≈ ' : ''}฿${_bmcNum(c.cost_per_piece, 2)}` : '—'])}</td></tr>`).join('')}</tbody></table></div>` : '';
    const ok = !p.problems;
    box.innerHTML = `<div class="text-sm font-semibold" style="color:var(--text-primary)">ดูตัวอย่างก่อนบันทึก: ${esc(file.name)}</div>
      ${bmcStats([['วัตถุดิบในไฟล์ (รหัส)', p.codes], ['ใช้ได้', p.codes_ok, 'var(--good-ink)'], ['สูตรแบ่งชิ้น', p.portions], ['ต้องแก้ไข', p.problems || 0, p.problems ? 'var(--bad-ink)' : null]])}
      <div class="text-sm" style="color:${ok ? 'var(--text-secondary)' : 'var(--bad-ink)'}">${ok ? 'บันทึกได้ บันไดหน่วยของรหัสที่อยู่ในไฟล์จะถูกแทนที่ทั้งรายการ' : 'มีรายการที่ต้องแก้ไข จึงยังไม่บันทึก · แก้ไฟล์แล้วนำเข้าใหม่'}</div>
      ${(p.warnings || []).map(x => `<div style="color:var(--amber-ink)">${esc(x)}</div>`).join('')}
      <div class="flex gap-2 my-2">${ok ? _bmcBtn('บันทึก', 'bmcUnitsImportApply()', true) : ''}${_bmcBtn('ยกเลิก', "document.getElementById('bmcUnitsImport').style.display='none'")}<span id="bmcUnitsApplyStatus" class="text-xs"></span></div>
      ${lad}${port ? `<div class="font-semibold mt-3" style="color:var(--text-primary)">สูตรแบ่งชิ้น</div>${port}` : ''}`;
  } catch (e) { box.innerHTML = `<span style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
async function bmcUnitsImportApply() {
  if (!await uiConfirm('บันไดหน่วยของรหัสที่อยู่ในไฟล์จะถูกแทนที่ทั้งรายการ', { title: 'ยืนยันการนำเข้าบันไดหน่วย', ok: 'นำเข้า', cancel: 'ยกเลิก' })) return;
  try {
    const fd = new FormData(); fd.append('file', _bmcUnitsFile, _bmcUnitsFile.name);
    const r = await _bmcJson(`${API}/api/bom-center/units/import?dry_run=false`, { method: 'POST', body: fd });
    document.getElementById('bmcUnitsImport').style.display = 'none';
    bmcStatus('bmcUnitsStatus', true, `บันทึกบันไดหน่วย ${r.codes} รหัส` + (r.portions ? ` และสูตรแบ่งชิ้น ${r.portions} รายการ` : ''));
    await bmcLoadIng(true);
    bmcLoadLadder();
  } catch (e) { bmcStatus('bmcUnitsApplyStatus', false, String(e.message || e).slice(0, 220)); }
}

// ── สูตรแบ่งชิ้นจากวัตถุดิบทั้งตัว (the card on the sub-recipe page) ─────────
let _bmcPortTimer = null;
function bmcPortInit() {
  const sel = document.getElementById('bmcPortSel');
  if (!sel) return;
  const keep = sel.value;
  const prepared = ((_bmcPrep && _bmcPrep.rows) || []).map(r => ({ code: r.code, name: r.name, mode: r.mode }));
  sel.innerHTML = '<option value="">เลือกสินค้ากึ่งสำเร็จ</option><option value="__new">+ สร้างรหัสใหม่</option>'
    + prepared.map(r => `<option value="${esc(r.code)}">${esc(r.code)} ${esc(r.name || '')}${r.mode === 'portion' ? ' · แบ่งชิ้น' : ''}</option>`).join('');
  if (keep) sel.value = keep;
  // raw items: the ones with an average ladder unit first (a salmon bought by weight, used by the piece)
  const raws = _bmcIng.filter(r => r.kind !== 'prepared' && r.active !== 0);
  const avgOf = r => (r.unit_rules || []).find(u => u.approx);
  raws.sort((a, b) => (avgOf(b) ? 1 : 0) - (avgOf(a) ? 1 : 0) || a.code.localeCompare(b.code));
  const dl = document.getElementById('bmcPortRawList');
  if (dl) dl.innerHTML = raws.map(r => `<option value="${esc(r.code)}">${esc(r.name_th || '')}${avgOf(r) ? ` · มีหน่วยเฉลี่ย (${esc(avgOf(r).unit)})` : ''}</option>`).join('');
  bmcPortChanged();
}
function bmcPortChanged() {
  const isNew = document.getElementById('bmcPortSel').value === '__new';
  document.querySelectorAll('#bmcPortion .bmc-port-new').forEach(el => { el.style.display = isNew ? '' : 'none'; });
  bmcPortPreview();
}
function _bmcPortRawCode() {
  const t = (document.getElementById('bmcPortRaw').value || '').trim();
  const hit = _bmcIng.find(x => x.code === t) || _bmcIng.find(x => (x.name_th || '') === t);
  return hit ? hit.code : t;
}
// the units a person may pick for the whole piece: the item's ladder units first, then the base unit and its metric units
function bmcPortRawChanged() {
  const code = _bmcPortRawCode();
  const r = _bmcIng.find(x => x.code === code);
  const u = document.getElementById('bmcPortUnit');
  if (!r) { u.innerHTML = ''; bmcPortPreview(); return; }
  const units = [...(r.unit_rules || []).map(x => x.unit), r.base_unit, ...Object.keys(_BMC_METRIC[r.base_unit] || {})].filter(Boolean);
  u.innerHTML = [...new Set(units)].map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join('');
  const avg = (r.unit_rules || []).find(x => x.approx);
  if (avg) u.value = avg.unit;
  bmcPortPreview();
}
function bmcPortPreview() {
  clearTimeout(_bmcPortTimer);
  _bmcPortTimer = setTimeout(async () => {
    const box = document.getElementById('bmcPortPreview');
    if (!box) return;
    const raw = _bmcPortRawCode(), qty = document.getElementById('bmcPortQty').value, unit = document.getElementById('bmcPortUnit').value;
    const pieces = document.getElementById('bmcPortPieces').value;
    if (!raw || !unit || !(Number(qty) > 0) || !(Number(pieces) > 0)) { box.innerHTML = '<span style="color:var(--text-muted)">ใส่วัตถุดิบ จำนวนที่ใช้ และจำนวนชิ้นที่ได้ เพื่อดูต้นทุนต่อชิ้น</span>'; return; }
    try {
      const d = await _bmcJson(`${API}/api/bom-center/prep/portion/preview?raw_code=${encodeURIComponent(raw)}&qty=${qty}&unit=${encodeURIComponent(unit)}&pieces=${pieces}`);
      const ap = d.approx ? '≈ ' : '', sh = u => ({ กิโลกรัม: 'กก.', มิลลิลิตร: 'มล.' }[u] || u);
      // ONE plain line: 1 ตัว ≈ 4.5 กก. (4–5 กก.) → 225 ชิ้น · ชิ้นละ ≈ 20 กรัม · ต้นทุนชิ้นละ ≈ ฿7.43 (ราคาเดือนตุลาคม 2026); the whole chain is the tooltip
      const lad = ((_bmcIng.find(x => x.code === raw) || {}).unit_rules || []).find(x => x.unit === d.unit);
      const per = lad && lad.parent_unit ? ` ${d.approx ? '≈' : '='} ${_bmcNum(lad.qty_in_parent, 2)} ${esc(sh(lad.parent_unit))}` : '';
      const lr = lad && lad.range_min != null && lad.range_max != null ? ` (${_bmcNum(lad.range_min, 2)}–${_bmcNum(lad.range_max, 2)} ${esc(sh(lad.parent_unit))})` : '';
      const head = lad ? `${_bmcNum(d.qty, 2)} ${esc(d.unit)}${per}${lr}` : `${_bmcNum(d.qty, 2)} ${esc(d.unit)} = ${ap}${_bmcNum(d.qty_base, 2)} ${esc(sh(d.base_unit))}`;
      const full = `${d.how} = ${ap}${_bmcNum(d.qty_base, 2)} ${d.base_unit}` + (d.range ? ` · ชิ้นละ ช่วง ${_bmcNum(d.range.per_piece_min, 1)}–${_bmcNum(d.range.per_piece_max, 1)} ${d.base_unit}` : '');
      box.innerHTML = `<div title="${esc(full)}">${head} <b>→ ${_bmcNum(d.pieces, 2)} ชิ้น</b> · ชิ้นละ ${ap}${_bmcNum(d.base_per_piece, 2)} ${esc(sh(d.base_unit))} ·
        ต้นทุนชิ้นละ ${d.cost_per_piece != null ? `<b>${ap}฿${Number(d.cost_per_piece).toLocaleString('th-TH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</b> (ราคาเดือน${fmtMonthTh(d.price_month)})` : '<span style="color:var(--amber-ink)">ยังไม่มีราคา</span>'}</div>`;
    } catch (e) { box.innerHTML = `<span style="color:var(--amber-ink)">${esc(String(e.message || e))}</span>`; }
  }, 250);
}
async function bmcPortSave() {
  const isNew = document.getElementById('bmcPortSel').value === '__new';
  const code = isNew ? (document.getElementById('bmcPortNewCode').value || '').trim() : document.getElementById('bmcPortSel').value;
  const name = (document.getElementById('bmcPortNewName').value || '').trim();
  const raw = _bmcPortRawCode(), qty = Number(document.getElementById('bmcPortQty').value), unit = document.getElementById('bmcPortUnit').value;
  const pieces = Number(document.getElementById('bmcPortPieces').value), note = (document.getElementById('bmcPortNote').value || '').trim();
  const st = 'bmcPortStatus';
  if (!code) return bmcStatus(st, false, 'เลือกสินค้ากึ่งสำเร็จ หรือสร้างรหัสใหม่');
  if (isNew && !name) return bmcStatus(st, false, 'ใส่ชื่อของสินค้ากึ่งสำเร็จที่สร้างใหม่');
  if (!raw) return bmcStatus(st, false, 'เลือกวัตถุดิบทั้งตัวที่นำมาแบ่ง');
  if (!(qty > 0) || !(pieces > 0)) return bmcStatus(st, false, 'จำนวนที่ใช้และจำนวนชิ้นที่ได้ต้องมากกว่า 0');
  const had = ((_bmcPrep && _bmcPrep.rows) || []).find(r => r.code === code && r.has_recipe);
  if (had && !await uiConfirm(`${code} มีสูตรอยู่แล้ว การบันทึกจะแทนที่สูตรเดิมทั้งสูตร`, { title: 'ยืนยันการแทนที่สูตร', ok: 'แทนที่สูตร', cancel: 'ยกเลิก' })) return;
  try {
    await _bmcJson(`${API}/api/bom-center/prep/portion`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, name: isNew ? name : null, raw_code: raw, qty, unit, pieces, out_unit: 'ชิ้น', note: note || null }) });
    bmcStatus(st, true, 'บันทึกสูตรแบ่งชิ้นแล้ว');
    await bmcLoadIng(true);
    await bmcLoadPrep();
  } catch (e) { bmcStatus(st, false, String(e.message || e).slice(0, 220)); }
}

// ── recipes by tier (Sprint S2) ─────────────────────────────────────────────
let _bmcRel = null;          // the release being viewed
let _bmcRels = [];
let _bmcTier = 0;            // 0 = every dish; else the basket of that tier (min_tier <= T)
let _bmcRecView = null;      // /api/bom-center/recipes
let _bmcEdit = null;         // {sku, d, lines}
const _BMC_TIERS = [459, 629, 789, 999, 1299];

// a draft is the kitchen's (bom.recipes) or the analyst's; once submitted (review) only the analyst's (bom.center) — recipes.assert_editable
function _bmcCanEditRel(rel) {
  if (!rel) return false;
  if (rel.status === 'draft') return _bmcCanRecipes();
  if (rel.status === 'review') return hasModule('bom.center');
  return false;
}
function _bmcEditableFor(rel) { return _bmcCanEditRel(rel); }
function _bmcRelEditable() { return _bmcEditableFor(_bmcRel); }
function _bmcEdRel() { return (_bmcEdit && _bmcEdit.rel) || _bmcRel; }   // the release the open editor belongs to

async function bmcLoadReleases(keepId) {
  try {
    const d = await _bmcJson(`${API}/api/bom-center/releases`);
    _bmcRels = d.releases || [];
    window._bmcRelSuggest = d.suggest || {};
    // the Cost-RW card's months: pre-filled with the suggestion, edited in place (no prompts before the file)
    const eff = document.getElementById('bmcCrwEff'), pm = document.getElementById('bmcCrwPm');
    const initSel = (el, v) => { if (el && !el.dataset.init) { supplyMonthPut(el, v); el.dataset.init = '1'; } };   // once: after that the person's choice stands
    initSel(eff, (d.suggest || {}).effective_month || supplyMonthAdd(supplyMonthNow(), 1));
    initSel(pm, (d.suggest || {}).price_month || supplyMonthDefault(true));
    const sel = document.getElementById('bmcRel');
    const cur = keepId || Number(sel.value) || (_bmcRels.find(r => r.kind === 'monthly') || _bmcRels[0] || {}).release_id;
    const st = { draft: 'ร่าง', review: 'รอตรวจ', released: 'ประกาศใช้แล้ว', retired: 'ยกเลิกใช้' };
    sel.innerHTML = _bmcRels.map(r => `<option value="${r.release_id}" ${r.release_id === cur ? 'selected' : ''}>${esc(_bmcLbl(r.label))} · ${st[r.status] || r.status}`
      + (r.kind === 'baseline' ? ' (ระบบเดิม)' : ` · มีผล ${fmtMonthTh(r.effective_month)} · ราคา ${fmtMonthTh(r.price_month || '-')}`) + `</option>`).join('')
      || '<option value="">ยังไม่มีเวอร์ชัน · นำเข้าไฟล์ Cost-RW</option>';
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
  bmcRenderRecBanner();
  if (!_bmcRel) { document.getElementById('bmcRecBody').innerHTML = '<tr><td colspan="10" class="py-3" style="color:var(--text-muted)">ยังไม่มีสูตร · กด “นำเข้าสูตรจากไฟล์ Cost-RW”</td></tr>'; return; }
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
  if (r.sheet_diff != null && Math.abs(r.sheet_diff) > 0.01) out.push(['ไม่ตรงไฟล์ครัว', 'var(--amber-ink)']);
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
  _bmcStatus('bmcRecSummary', `${rows.length} เมนู${_bmcTier ? ` ในตะกร้า ฿${_bmcTier.toLocaleString()} (รวมเมนูระดับราคาต่ำกว่า)` : ''} · เฉลี่ย ฿${_bmcNum(avg)}/เสิร์ฟ · `
    + `ต้องตรวจ ${v.rows.filter(r => _bmcRecIssues(r).length).length} · ยังไม่จับคู่ ${v.unmatched.length}`
    + (_bmcRel ? ` · คิดราคาเดือน${fmtMonthTh(_bmcRel.price_month || '-')}` : ''));
}
function bmcRenderUnmatched() {
  const box = document.getElementById('bmcUnmatched');
  const u = (_bmcRecView && _bmcRecView.unmatched) || [];
  if (!u.length) { box.innerHTML = ''; return; }
  const w = _bmcRelEditable();
  box.innerHTML = `<div class="card p-4">
    <div class="text-sm font-semibold" style="color:var(--text-primary)">สูตรที่ยังไม่ได้จับคู่รหัส POS (${u.length})</div>
    <div class="text-[11px] mb-2" style="color:var(--text-muted)">ชื่อในไฟล์ Cost-RW ไม่ตรงกับเมนูใน POS · เลือกรหัสแล้วกด “จับคู่” (จำไว้สำหรับการนำเข้าครั้งถัดไป) · เมนูที่ไม่ขาย กด “ข้าม (ไม่วางขาย)”</div>
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
  if (!sku) { _bmcStatus('bmcRecStatus', 'เลือกรหัส POS', 'var(--bad-ink)'); return; }
  try {
    await _bmcJson(`${API}/api/bom-center/recipes/map`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ release_id: _bmcRel.release_id, ref: x.ref, sku_code: sku }) });
    _bmcStatus('bmcRecStatus', skip ? `✓ ${x.th}: บันทึกว่าไม่วางขาย` : `✓ ${x.th} → ${sku}`, 'var(--good-ink)');
    bmcLoadRecipes();
  } catch (e) { _bmcStatus('bmcRecStatus', 'ไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
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
      <div class="text-[11px]" style="color:var(--text-muted)">${d.dish?.min_tier ? 'ระดับราคา ฿' + d.dish.min_tier.toLocaleString() + ' · ' : ''}${esc(d.source_ref || '')} · ราคาเดือน${fmtMonthTh(d.release?.price_month || '-')}
        ${w ? '' : ' · <b>อ่านอย่างเดียว</b> (แก้ไขได้เฉพาะสูตรร่าง)'}</div></div>
      <div class="ml-auto text-right"><div class="text-[11px]" style="color:var(--text-muted)">ต้นทุน/เสิร์ฟ</div><div class="text-xl font-bold" style="color:var(--text-primary)">฿${_bmcNum(total, 2)}</div>
        ${d.source_cost != null ? `<div class="text-[11px]" style="color:var(--text-muted)">ไฟล์ครัว ฿${_bmcNum(d.source_cost, 2)}</div>` : ''}</div>
      <button onclick="document.getElementById('${host}').style.display='none'" style="font-size:12px;background:none;border:0;color:var(--text-muted);cursor:pointer">ปิด</button></div>
    <div class="overflow-x-auto"><table class="text-xs w-full" style="font-variant-numeric:tabular-nums">
      <thead><tr class="text-left border-b" style="color:var(--text-muted)"><th class="pr-2">#</th><th class="pr-2">รหัสวัตถุดิบ</th><th class="pr-2">วัตถุดิบ</th>
        <th class="pr-2 text-right">ปริมาณ</th><th class="pr-2">หน่วย</th><th class="pr-2 text-right">สูญเสีย %</th><th class="pr-2">ตั้งใจเป็น 0</th>
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
  const amber = n => (n ? 'var(--amber-ink)' : null);
  return `<div class="card p-4"><div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">${applied ? '✓ นำเข้าแล้ว' : 'ดูตัวอย่างก่อนบันทึก'}: ${esc(_bmcLbl(p.label))}</div>
    <div class="text-sm" style="color:var(--text-secondary)">มีผล ${fmtMonthTh(p.effective_month)} · คิดราคาเดือน${fmtMonthTh(p.price_month)}</div>
    ${p.sheets.length ? `<div class="wt-note">แผ่นงาน (จำนวนสูตร): ${p.sheets.map(s => `${esc(s.sheet)} (${s.blocks})`).join(', ')}</div>` : ''}
    ${bmcStats([['สูตรในไฟล์', p.blocks], ['จับคู่รหัส POS ได้', p.matched, 'var(--good-ink)'], ['ยังไม่จับคู่', p.unmatched.length, amber(p.unmatched.length)],
      ['ข้าม (ไม่ขาย)', p.skipped_not_on_sale.length], ['บรรทัด', p.lines], ['ตั้งใจเป็น 0', p.zero_lines], ['เซลล์ผิดพลาดในไฟล์', p.error_cells, amber(p.error_cells)],
      ['คำนวณต้นทุนได้ (เมนู)', p.costed], ['คิดไม่ได้', p.with_problems, amber(p.with_problems)], ['ไม่ตรงยอดในไฟล์', p.sheet_mismatch, amber(p.sheet_mismatch)]])}
    <div class="wt-note">วิธีจับคู่: ชื่อตรง ${p.by_method.exact || 0} · ต่างระดับราคา ${p.by_method['exact-anytier'] || 0} · จากการจับคู่ครั้งก่อน ${p.by_method.map || 0} · ชื่อใกล้เคียง ${p.by_method.fuzzy || 0} · ไม่ตรงยอด = ต่างเกิน ฿0.01</div>
    ${p.fuzzy.length ? `<div class="text-sm font-semibold mt-3" style="color:var(--navy-ink)">จับคู่จากชื่อใกล้เคียง (ควรตรวจ)</div>
      <div class="wt-wrap mt-1" style="max-height:300px"><table class="wt"><thead><tr><th>ชื่อในไฟล์</th><th>รหัส POS</th></tr></thead><tbody>
      ${p.fuzzy.map(f => `<tr><td data-l="ชื่อในไฟล์">${esc(f.th)}</td><td class="wt-code" data-l="รหัส POS">${esc(f.sku_code)}</td></tr>`).join('')}</tbody></table></div>` : ''}
    ${p.mismatch_rows.length ? `<div class="text-sm font-semibold mt-3" style="color:var(--amber-ink)">ต้นทุนไม่ตรงกับไฟล์</div>
      <div class="wt-wrap mt-1" style="max-height:300px"><table class="wt"><thead><tr><th>เมนู</th><th class="wt-num">คำนวณได้</th><th class="wt-num">ในไฟล์</th></tr></thead><tbody>
      ${p.mismatch_rows.slice(0, 50).map(r => `<tr><td data-l="เมนู">${esc(r.name)}</td><td class="wt-num" data-l="คำนวณได้">฿${_bmcNum(r.cost)}</td><td class="wt-num" data-l="ในไฟล์">฿${_bmcNum(r.sheet_cost)}</td></tr>`).join('')}</tbody></table></div>` : ''}
  </div>`;
}
async function bmcImportCostRw(input) {
  const file = input?.files?.[0];
  if (!file) return;
  const sg = window._bmcRelSuggest || {};
  const eff = document.getElementById('bmcCrwEff')?.value || sg.effective_month || '';
  const pm = document.getElementById('bmcCrwPm')?.value || sg.price_month || '';
  if (!eff || !pm) { _bmcStatus('bmcRecStatus', 'เลือกเดือนที่มีผลและเดือนราคาก่อน', 'var(--amber-ink)'); input.value = ''; return; }
  const send = dry => { const fd = new FormData(); fd.append('file', file, file.name);
    return _bmcJson(`${API}/api/bom-center/recipes/import-cost-rw?dry_run=${dry}&effective_month=${eff}&price_month=${pm}`, { method: 'POST', body: fd }); };
  try {
    _bmcStatus('bmcRecStatus', 'กำลังอ่านไฟล์ Cost-RW…');
    const pre = await send(true);
    document.getElementById('bmcRecReport').innerHTML = _bmcCostRwReport(pre, false);
    if (!await uiConfirm(`นำเข้าสูตรบุฟเฟ่ต์ ${pre.matched} เมนู เข้า “${_bmcLbl(pre.label)}”?\nยังไม่จับคู่ ${pre.unmatched.length} รายการ (จับคู่ต่อได้ในหน้านี้)\nสูตรเดิมในร่างนี้ของเมนูที่อยู่ในไฟล์ถูกแทนที่`, { title: 'ยืนยันการนำเข้าสูตร', ok: 'นำเข้าสูตร', cancel: 'ยกเลิก' })) {
      _bmcStatus('bmcRecStatus', 'ยังไม่บันทึก'); return;
    }
    const d = await send(false);
    document.getElementById('bmcRecReport').innerHTML = _bmcCostRwReport(d, true);
    _bmcStatus('bmcRecStatus', `✓ นำเข้า ${d.matched} เมนู`, 'var(--good-ink)');
    await bmcLoadReleases(d.release_id);
  } catch (e) { _bmcStatus('bmcRecStatus', 'นำเข้าไม่สำเร็จ:' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
  finally { input.value = ''; }
}
// what the version waits for, and the kitchen's two buttons (สร้างฉบับร่าง / ส่งตรวจ) — the banner sits first in #bmcRecReport
// the analyst's last reason for sending a draft back ("ส่งกลับแก้ไขโดย … : reason" is appended to the notes), or ''
function _bmcSendBackLine(r) {
  if (!r || r.status !== 'draft') return '';
  const n = String(r.notes || ''), i = n.lastIndexOf('ส่งกลับแก้ไขโดย');
  if (i < 0) return '';
  // the note stores the login of the analyst; show the name people know
  const seg = n.slice(i).trim().replace(/^ส่งกลับแก้ไขโดย\s+([^:\s]+)\s*:/, (m, u) => `ส่งกลับแก้ไขโดย ${whoTh(u)}:`);
  return `<div class="text-xs mt-1" style="color:var(--amber-ink)">ส่งกลับแก้ไข: ${esc(seg)}</div>`;
}
function bmcRenderRecBanner() {
  const host = document.getElementById('bmcRecReport');
  if (!host) return;
  let box = document.getElementById('bmcRecBanner');
  if (!box || box.parentNode !== host) { box = document.createElement('div'); box.id = 'bmcRecBanner'; host.insertBefore(box, host.firstChild); }
  const r = _bmcRel, sg = window._bmcRelSuggest || {};
  const monthly = !!(r && r.kind === 'monthly');
  const hasDraft = _bmcRels.some(x => x.kind === 'monthly' && ['draft', 'review'].includes(x.status));
  const sb = document.getElementById('bmcSubmitBtn'), nb = document.getElementById('bmcNewDraftBtn');
  if (sb) sb.style.display = (monthly && r.status === 'draft' && _bmcCanRecipes()) ? '' : 'none';
  if (nb) { nb.style.display = (!hasDraft && _bmcCanRecipes()) ? '' : 'none'; nb.textContent = `สร้างร่างเดือน${fmtMonthTh(sg.effective_month || '')}`.trim(); }
  const card = (ink, html) => `<div class="text-xs p-2 mb-2" style="border-left:3px solid ${ink};background:var(--bg-subtle);color:var(--text-primary)">${html}</div>`;
  let html = '';
  if (monthly && r.status === 'review') {
    html = card('var(--amber-ink)', `<b>รอตรวจ</b> (ฝ่ายวิเคราะห์) · ส่งตรวจโดย ${esc(whoTh(r.submitted_by))} เมื่อ ${esc(fmtAt(r.submitted_at) || '-')}`
      + (hasModule('bom.center') ? '' : ' · ระหว่างการตรวจ ครัวแก้ไขสูตรไม่ได้ จนกว่าฝ่ายวิเคราะห์จะส่งกลับ'));
  } else if (monthly && r.status === 'draft') {
    html = card('var(--accent)', '<b>ร่าง</b> · ครัวสร้างสูตรให้ครบ แล้วกด “ส่งตรวจ” ให้ฝ่ายวิเคราะห์' + _bmcSendBackLine(r));
  } else if (!hasDraft && _bmcCanRecipes()) {
    html = card('var(--accent)', `ยังไม่มีร่างของเดือน${fmtMonthTh(sg.effective_month || '')} · ดาวน์โหลดแม่แบบ กรอกสูตรหลายเมนูแล้วนำเข้า หรือสร้างร่างก่อน`);
  }
  box.innerHTML = html;
}
function bmcDownloadTemplate() {
  bmcDownload(`${API}/api/bom-center/recipes/template.xlsx` + (_bmcRel ? `?release_id=${_bmcRel.release_id}` : ''), 'แม่แบบสูตร.xlsx', 'bmcRecStatus');
}
async function bmcCreateDraft() {
  const sg = window._bmcRelSuggest || {};
  const eff = document.getElementById('bmcCrwEff')?.value || sg.effective_month || '';
  const pm = document.getElementById('bmcCrwPm')?.value || sg.price_month || '';
  if (!eff || !pm) { _bmcStatus('bmcRecStatus', 'เลือกเดือนที่มีผลและเดือนราคาก่อน', 'var(--amber-ink)'); return; }
  if (!await uiConfirm(`สร้างร่างสูตร มีผลเดือน${fmtMonthTh(eff)} คิดต้นทุนด้วยราคาเดือน${fmtMonthTh(pm)}?`, { title: 'ยืนยันการสร้างร่างสูตร', ok: 'สร้างร่าง', cancel: 'ยกเลิก' })) return;
  try {
    const d = await _bmcJson(`${API}/api/bom-center/release-act/new-draft`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ effective_month: eff, price_month: pm }) });
    _bmcStatus('bmcRecStatus', `✓ สร้างร่าง ${_bmcLbl(d.label)}`, 'var(--good-ink)');
    await bmcLoadReleases(d.release_id);
  } catch (e) { _bmcStatus('bmcRecStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
// the kitchen hands a finished draft to the analyst (from the recipes pane, or from the release pane's header)
async function bmcSubmitReview(rel, statusId) {
  const r = rel || _bmcRel, sid = statusId || 'bmcRecStatus';
  if (!r) return;
  if (!await uiConfirm(`ส่งร่าง “${_bmcLbl(r.label)}” ให้ฝ่ายวิเคราะห์ตรวจ?\nระหว่างตรวจ ครัวแก้ไขสูตรไม่ได้จนกว่าฝ่ายวิเคราะห์จะส่งกลับ`, { title: 'ยืนยันการส่งตรวจ', ok: 'ส่งตรวจ', cancel: 'ยกเลิก' })) return;
  try {
    await _bmcJson(`${API}/api/bom-center/release-act/submit-review`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ release_id: r.release_id }) });
    _bmcStatus(sid, '✓ ส่งตรวจแล้ว', 'var(--good-ink)');
    if (sid === 'bmcRlStatus') await bmcLoadReleasePane(r.release_id); else await bmcLoadReleases(r.release_id);
  } catch (e) { _bmcStatus(sid, 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
async function bmcSendBack() {
  const r = _bmcRlRel;
  if (!r) return;
  const reason = await uiPrompt('เหตุผลที่ส่งกลับให้ครัว (จำเป็น)', '', { title: 'ส่งกลับให้ครัวแก้ไข', okText: 'ส่งกลับ' });
  if (!reason) return;
  try {
    await _bmcJson(`${API}/api/bom-center/release-act/send-back`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ release_id: r.release_id, note: reason }) });
    _bmcStatus('bmcRlStatus', '✓ ส่งกลับให้แก้ไขแล้ว', 'var(--good-ink)');
    bmcLoadReleasePane(r.release_id);
  } catch (e) { _bmcStatus('bmcRlStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)'); }
}
function bmcExportRecipes() {
  if (!_bmcRel) return;
  bmcDownload(`${API}/api/bom-center/recipes-export.xlsx?release_id=${_bmcRel.release_id}`, 'สูตร.xlsx', 'bmcRecStatus');
}
async function bmcImportFlat(input) {
  const file = input?.files?.[0];
  if (!file || !_bmcRel) { if (input) input.value = ''; return; }
  const send = dry => { const fd = new FormData(); fd.append('file', file, file.name);
    return _bmcJson(`${API}/api/bom-center/recipes/import-flat?release_id=${_bmcRel.release_id}&dry_run=${dry}`, { method: 'POST', body: fd }); };
  try {
    const pre = await send(true);
    if (!await uiConfirm(`แทนที่สูตร ${pre.recipes} เมนู (${pre.lines} บรรทัด) ใน “${_bmcLbl(_bmcRel.label)}”?` + (pre.errors.length ? `\nข้าม ${pre.errors.length} แถว: ` + pre.errors.slice(0, 4).map(e => e.why).join(', ') : ''), { title: 'ยืนยันการแทนที่สูตร', ok: 'แทนที่สูตร', cancel: 'ยกเลิก' })) return;
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
const _BMC_STATUS_TH = { draft: 'ร่าง', review: 'รอตรวจ', released: 'ประกาศใช้แล้ว', retired: 'ยกเลิกใช้' };

async function bmcLoadReleasePane(keepId) {
  try {
    const d = await _bmcJson(`${API}/api/bom-center/releases`);
    _bmcRels = d.releases || [];
    window._bmcRelSuggest = d.suggest || {};
    const sel = document.getElementById('bmcRlSel');
    const monthly = _bmcRels.filter(r => r.kind === 'monthly');
    const cur = keepId || Number(sel.value) || (monthly[0] || {}).release_id;
    sel.innerHTML = monthly.map(r => `<option value="${r.release_id}" ${r.release_id === cur ? 'selected' : ''}>${esc(_bmcLbl(r.label))} · ${_BMC_STATUS_TH[r.status] || r.status} · มีผล ${fmtMonthTh(r.effective_month)}</option>`).join('')
      || '<option value="">ยังไม่มีเวอร์ชันรายเดือน · นำเข้าสูตรที่ สูตรตามระดับราคา</option>';
    await bmcLoadRelease();
  } catch (e) { _bmcStatus('bmcRlStatus', 'ไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
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
  const can = m => hasModule(m);                       // the server's rule (auth.py): release, accept and send back = bom.release
  const btn = (label, fn, primary, title) => `<button onclick="${fn}" title="${esc(title || '')}" style="font-size:12.5px;${primary
    ? 'background:var(--accent);color:var(--on-accent);border:0' : 'background:var(--bg-input);color:var(--text-primary);border:1px solid var(--border-control)'};border-radius:var(--r-sm);padding:5px 14px;cursor:pointer">${label}</button>`;
  const acts = [];
  if (['draft', 'review'].includes(r.status)) {
    if (r.status === 'draft' && _bmcCanRecipes()) acts.push(btn('ส่งตรวจ', "bmcSubmitReview(_bmcRlRel, 'bmcRlStatus')", false, 'ส่งร่างนี้ให้ฝ่ายวิเคราะห์ตรวจ'));
    if (r.status === 'review' && can('bom.release')) acts.push(btn('ส่งกลับแก้ไข', 'bmcSendBack()', false, 'ส่งกลับให้ครัวแก้ไขพร้อมเหตุผล'));
    if (can('bom.center') || can('bom.release')) acts.push(btn('บันทึกผลตรวจ', 'bmcRunChecks()', false, 'บันทึกผลตรวจให้หน้างานใช้'));
    // PR-M2: while a check item is open the button is off and says how many to fix (the server refuses it too)
    if (can('bom.release')) acts.push(checks && !checks.can_release
      ? `<button disabled aria-disabled="true" title="" style="font-size:12.5px;background:var(--bg-input);color:var(--text-muted);border:1px solid var(--border-control);border-radius:var(--r-sm);padding:5px 14px;cursor:not-allowed;opacity:.8">ประกาศใช้ไม่ได้ · ต้องแก้ไข ${checks.open_blocks} ข้อ</button>`
      : btn('ประกาศใช้เวอร์ชันนี้', 'bmcDoRelease()', !!(checks && checks.can_release), 'ฝ่ายวิเคราะห์ประกาศใช้ ผู้บริหารรับทราบภายหลัง'));
  }
  if (r.status === 'released') {
    if (!r.acknowledged_by && (hasModule('bom.audit'))) acts.push(btn('รับทราบ (ผู้บริหาร)', 'bmcAck()', true));
    if (hasModule('bom.audit') || hasModule('bom.release')) acts.push(btn('ยกเลิกใช้เวอร์ชันนี้', 'bmcRetire()', false, 'เวอร์ชันก่อนหน้ากลับมามีผลแทน'));
    if (can('bom.center')) acts.push(btn('สร้างร่างเดือนถัดไป', 'bmcNewDraft()', false));
  }
  const stInk = { draft: 'var(--amber-ink)', review: 'var(--amber-ink)', released: 'var(--good-ink)', retired: 'var(--text-muted)' }[r.status];
  document.getElementById('bmcRlHead').innerHTML = `
    <div class="flex flex-wrap items-start gap-3">
      <div><div class="text-base font-bold" style="color:var(--text-primary)">${esc(_bmcLbl(r.label))} ${_bmcChip(_BMC_STATUS_TH[r.status] || r.status, stInk)}</div>
        <div class="text-xs mt-1" style="color:var(--text-secondary)">มีผลเดือน <b>${fmtMonthTh(r.effective_month)}</b> · คิดต้นทุนด้วยราคาเดือน <b>${fmtMonthTh(r.price_month || '-')}</b> ·
          ${r.recipes} สูตร${r.unmatched ? ` · ยังไม่จับคู่ ${r.unmatched}` : ''}</div>
        <div class="text-xs mt-1" style="color:var(--text-secondary)">สร้างโดย ${esc(whoTh(r.created_by))}
          ${r.released_by ? ` · ประกาศใช้โดย <b>${esc(whoTh(r.released_by))}</b> ${esc(fmtAt(r.released_at))}` : ''}
          ${r.status === 'released' ? (r.acknowledged_by ? ` · <span style="color:var(--good-ink)">ผู้บริหารรับทราบแล้ว: ${esc(whoTh(r.acknowledged_by))} ${esc(fmtAt(r.acknowledged_at))}</span>`
            : ' · <span style="color:var(--amber-ink)">รอผู้บริหารรับทราบ</span>') : ''}</div></div>
      <div class="ml-auto flex flex-wrap gap-2">${acts.join('')}</div>
    </div>
    ${r.status === 'review' ? `<div class="text-xs mt-3 p-2" style="border-left:3px solid var(--accent);background:var(--bg-subtle);color:var(--text-primary)">ขั้นตอนถัดไป: ฝ่ายวิเคราะห์ตรวจรายการ แก้หรือยอมรับข้อที่ติด แล้วประกาศใช้ หรือส่งกลับให้ครัว</div>` : ''}
    ${r.status === 'draft' ? `<div class="text-xs mt-3 p-2" style="border-left:3px solid var(--accent);background:var(--bg-subtle);color:var(--text-primary)">ขั้นตอนถัดไป: ครัวสร้างสูตรให้ครบ แล้วกด “ส่งตรวจ”${_bmcSendBackLine(r)}</div>` : ''}
    ${r.status === 'released' && !r.acknowledged_by ? `<div class="text-xs mt-3 p-2" style="border-left:3px solid var(--accent);background:var(--bg-subtle);color:var(--text-primary)">ขั้นตอนถัดไป: ผู้บริหารอ่านผลกระทบด้านล่าง แล้วกด “รับทราบ”</div>` : ''}`;
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
    box.innerHTML = `<div class="text-sm font-semibold" style="color:var(--text-primary)">รายการตรวจ</div>
      <div class="text-xs mt-1" style="color:var(--text-muted)">${['draft', 'review'].includes(_bmcRlRel.status) ? 'ไม่พบข้อที่ต้องตรวจ' : 'ไม่มีข้อค้างตรวจ'}</div>`;
    return;
  }
  const canAccept = hasModule('bom.release') && ['draft', 'review'].includes(_bmcRlRel.status);
  window._bmcChecks = d;
  // one group per check code, open ones first; an ACCEPTABLE group takes one reason for all its open rows
  const groups = {};
  d.rows.forEach((r, i) => { (groups[r.check_code] = groups[r.check_code] || { code: r.check_code, title: r.title, severity: r.severity, acceptable: r.acceptable, rows: [] }).rows.push({ r, i }); });
  const order = d.by_code.map(k => k.code).filter(c => groups[c]);
  const btnS = 'font-size:11px;background:none;color:var(--accent);border:1px solid var(--border-control);border-radius:4px;padding:1px 8px;cursor:pointer';
  const html = order.map(c => {
    const g = groups[c], open = g.rows.filter(x => !x.r.resolved_by).length;
    const ink = g.severity === 'block' && open ? 'var(--bad-ink)' : g.severity === 'block' ? 'var(--good-ink)' : 'var(--amber-ink)';
    return `<details ${open ? 'open' : ''} class="mb-1" data-check="${esc(g.code)}"><summary class="text-xs cursor-pointer py-1" style="color:var(--text-primary)"><b>${esc(g.title)}</b> <span class="bmc-code">· ${esc(g.code)}</span> ${_bmcChip(`ค้าง ${open} จาก ${g.rows.length}`, ink)}
        ${g.acceptable && open && canAccept ? `<button onclick="event.preventDefault();bmcAcceptGroup('${esc(g.code)}')" style="${btnS};margin-left:8px">ยอมรับทั้งกลุ่มพร้อมเหตุผล (${open})</button>` : ''}</summary>
      <div class="overflow-x-auto" style="max-height:300px;overflow-y:auto"><table class="text-xs w-full"><tbody>${g.rows.slice(0, 200).map(({ r, i }) => `<tr class="border-b" style="border-color:var(--border-soft)">
        <td class="pr-2 py-1">${esc(r.message || '')}${r.resolved_by ? `<div class="text-[10.5px]" style="color:var(--good-ink)">ยอมรับโดย ${esc(whoTh(r.resolved_by))}</div>` : ''}</td>
        <td class="whitespace-nowrap">${r.acceptable && !r.resolved_by && canAccept ? `<button onclick="bmcAccept(${i})" style="${btnS}">ยอมรับพร้อมเหตุผล</button>` : ''}
          ${/^OTH/.test(r.ref) ? `<button onclick="bmcOpenRecipe('${esc(r.ref.split('#')[0])}', {rel: _bmcRlRel, host: 'bmcRlEditor'})" style="font-size:11px;background:none;color:var(--accent);border:0;cursor:pointer">เปิดสูตร</button>` : ''}</td></tr>`).join('')}</tbody></table>
      ${g.rows.length > 200 ? `<div class="text-[11px] mt-1" style="color:var(--text-muted)">แสดง 200 จาก ${g.rows.length}</div>` : ''}</div></details>`;
  }).join('');
  box.innerHTML = `<div class="flex flex-wrap items-center gap-2 mb-2"><div class="text-sm font-semibold" style="color:var(--text-primary)">รายการตรวจ</div>
      ${d.fresh ? _bmcChip('คำนวณใหม่เมื่อเปิดหน้า', 'var(--text-muted)') : ''}
      ${d.can_release ? _bmcChip('ไม่มีข้อที่ต้องแก้ไข พร้อมประกาศใช้', 'var(--good-ink)') : _bmcChip(`ต้องแก้ไข ${d.open_blocks} ข้อก่อนประกาศใช้`, 'var(--bad-ink)')}
      ${_bmcChip(`ข้อสังเกต ${d.warnings}`, 'var(--amber-ink)')}</div>${html}`;
}
async function bmcAcceptGroup(code) {
  const d = window._bmcChecks;
  if (!d) return;
  const refs = d.rows.filter(r => r.check_code === code && !r.resolved_by && r.acceptable).map(r => r.ref);
  if (!refs.length) return;
  const title = (d.rows.find(r => r.check_code === code) || {}).title || code;
  const reason = await uiPrompt(`ยอมรับรายการตรวจ “${title}” ทั้งกลุ่ม ${refs.length} รายการ\nเหตุผลเดียวใช้กับทุกรายการในกลุ่ม (จำเป็น)`, '', { title: 'ยอมรับทั้งกลุ่มพร้อมเหตุผล', okText: 'ยอมรับ' });
  if (!reason) return;
  try {
    const out = await _bmcJson(`${API}/api/bom-center/release-act/accept-many`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ release_id: _bmcRlRel.release_id, check_code: code, refs, reason }) });
    bmcRenderChecks(out);
    _bmcStatus('bmcRlStatus', `✓ ยอมรับ “${title}” แล้ว ${refs.length} รายการ`, 'var(--good-ink)');
  } catch (e) { _bmcStatus('bmcRlStatus', 'ไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcRunChecks() {
  _bmcStatus('bmcRlStatus', 'กำลังตรวจ…');
  try {
    const d = await _bmcJson(`${API}/api/bom-center/release-act/check?release_id=${_bmcRlRel.release_id}`, { method: 'POST' });
    bmcRenderChecks(d);
    _bmcStatus('bmcRlStatus', d.can_release ? '✓ ตรวจแล้ว พร้อมประกาศใช้' : `ตรวจแล้ว ต้องแก้ไข ${d.open_blocks} ข้อ`, d.can_release ? 'var(--good-ink)' : 'var(--amber-ink)');
    bmcLoadCompare();
  } catch (e) { _bmcStatus('bmcRlStatus', 'ไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcAccept(i) {
  const r = window._bmcChecks.rows[i];
  const reason = await uiPrompt(`ยอมรับรายการตรวจ “${r.title || r.check_code}”\n${r.message}\nเหตุผล (จำเป็น) เช่น ปรับสูตรตามที่ครัวแจ้ง`, '', { title: 'ยอมรับพร้อมเหตุผล', okText: 'ยอมรับ' });
  if (!reason) return;
  try {
    const d = await _bmcJson(`${API}/api/bom-center/release-act/accept`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ release_id: _bmcRlRel.release_id, check_code: r.check_code, ref: r.ref, reason }) });
    bmcRenderChecks(d);
  } catch (e) { _bmcStatus('bmcRlStatus', 'ไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcDoRelease() {
  const r = _bmcRlRel;
  const now = new Date(), cur = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  let backdate_reason = null;
  if (r.effective_month <= cur) {
    // the month has started: releasing RE-COSTS sales already made (from ${r.effective_month} on)
    backdate_reason = await uiPrompt(`เวอร์ชันนี้มีผลเดือน${fmtMonthTh(r.effective_month)} ซึ่งเริ่มแล้ว\nการประกาศใช้คำนวณต้นทุนยอดขายตั้งแต่เดือน${fmtMonthTh(r.effective_month)} ใหม่ทั้งหมด (ก่อนหน้านั้นไม่เปลี่ยน)\nเหตุผลที่มีผลย้อนหลัง (จำเป็น)`, '', { title: 'ประกาศใช้ย้อนหลัง', okText: 'ประกาศใช้' });
    if (!backdate_reason) return;
  } else if (!await uiConfirm(`ประกาศใช้ “${_bmcLbl(r.label)}” มีผลเดือน${fmtMonthTh(r.effective_month)}?\nประกาศใช้แล้วแก้ไขสูตรในเวอร์ชันนี้ไม่ได้ (ต้องสร้างร่างใหม่) · ผู้บริหารจะรับทราบผลกระทบ`, { title: 'ยืนยันการประกาศใช้เวอร์ชัน', ok: 'ประกาศใช้', cancel: 'ยกเลิก' })) return;
  try {
    const d = await _bmcJson(`${API}/api/bom-center/release-act/release?release_id=${r.release_id}`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ backdate_reason }) });
    _bmcStatus('bmcRlStatus', '✓ ประกาศใช้แล้ว' + (d.late ? ' · ' + d.late : '') + (d.warnings ? ` · ข้อสังเกต ${d.warnings}` : ''), d.late ? 'var(--amber-ink)' : 'var(--good-ink)');
    bmcLoadReleasePane(r.release_id);
  } catch (e) { _bmcStatus('bmcRlStatus', 'ไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcAck() {
  const note = await uiPrompt(`รับทราบ “${_bmcLbl(_bmcRlRel.label)}”\nหมายเหตุ (ไม่บังคับ)`, '', { title: 'รับทราบเวอร์ชัน', okText: 'รับทราบ' });
  if (note === null) return;
  try {
    await _bmcJson(`${API}/api/bom-center/release-act/acknowledge`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ release_id: _bmcRlRel.release_id, note }) });
    _bmcStatus('bmcRlStatus', '✓ รับทราบแล้ว', 'var(--good-ink)');
    bmcLoadReleasePane(_bmcRlRel.release_id);
  } catch (e) { _bmcStatus('bmcRlStatus', 'ไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcRetire() {
  const reason = await uiPrompt(`ยกเลิกใช้ “${_bmcLbl(_bmcRlRel.label)}”? เวอร์ชันก่อนหน้ากลับมามีผลแทน\nเหตุผล (จำเป็น)`, '', { title: 'ยกเลิกใช้เวอร์ชัน', okText: 'ยกเลิกใช้' });
  if (!reason) return;
  try {
    await _bmcJson(`${API}/api/bom-center/release-act/retire`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ release_id: _bmcRlRel.release_id, note: reason }) });
    _bmcStatus('bmcRlStatus', '✓ ยกเลิกใช้แล้ว', 'var(--good-ink)');
    bmcLoadReleasePane(_bmcRlRel.release_id);
  } catch (e) { _bmcStatus('bmcRlStatus', 'ไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcNewDraft() {
  const sg = window._bmcRelSuggest || {};
  const eff = await uiPrompt('เดือนที่ร่างใหม่มีผล (เช่น 2026-11)', sg.effective_month || '', { title: 'สร้างร่างเดือนถัดไป', okText: 'ต่อไป' });
  if (!eff) return;
  const pm = await uiPrompt('เดือนของราคาที่ใช้คิดต้นทุน (เช่น 2026-10)', sg.price_month || '', { title: 'สร้างร่างเดือนถัดไป', okText: 'สร้างร่าง' });
  if (!pm) return;
  try {
    const d = await _bmcJson(`${API}/api/bom-center/release-act/new-draft`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from_release_id: _bmcRlRel.release_id, effective_month: eff, price_month: pm }) });
    _bmcStatus('bmcRlStatus', `✓ สร้าง ${_bmcLbl(d.label)}`, 'var(--good-ink)');
    bmcLoadReleasePane(d.release_id);
  } catch (e) { _bmcStatus('bmcRlStatus', 'ไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcLoadCompare() {
  const box = document.getElementById('bmcRlCompare');
  box.innerHTML = '<div class="text-xs" style="color:var(--text-muted)">กำลังคำนวณผลกระทบ…</div>';
  try {
    const d = await _bmcJson(`${API}/api/bom-center/release-compare?release_id=${_bmcRlRel.release_id}`);
    const sgn = v => (v > 0 ? '+' : '') + _bmcNum(v, 0);
    const ink = v => v > 0.5 ? 'var(--bad-ink)' : v < -0.5 ? 'var(--good-ink)' : 'var(--text-muted)';
    box.innerHTML = `<div class="text-sm font-semibold" style="color:var(--text-primary)">ผลกระทบหากใช้เวอร์ชันนี้ (เทียบกับยอดขายเดือน${fmtMonthTh(d.month)})</div>
      <div class="text-xs mb-3" style="color:var(--text-secondary)">ต้นทุนเปลี่ยน ${d.changed} เมนู · ต้นทุนขายเปลี่ยนรวม <b style="color:${ink(d.total_delta)}">฿${sgn(d.total_delta)}</b> ต่อเดือน
        (เทียบกับต้นทุนที่ใช้อยู่ตอนนี้ของแต่ละเมนู)</div>
      ${d.tiers.length ? `<div class="text-xs font-semibold mb-1" style="color:var(--text-primary)">% ต้นทุนอาหารตามระดับราคา</div>
      <div class="overflow-x-auto mb-3"><table class="text-xs w-full" style="font-variant-numeric:tabular-nums"><thead><tr class="text-left border-b" style="color:var(--text-muted)">
        <th class="pr-2">ระดับราคา</th><th class="pr-2 text-right">ลูกค้า</th><th class="pr-2 text-right">รายได้</th><th class="pr-2 text-right">ต้นทุนขายเดิม</th><th class="pr-2 text-right">ต้นทุนขายใหม่</th>
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
    const th = { import_cost_rw: 'นำเข้า Cost-RW', edit_recipe: 'แก้สูตร', accept_check: 'ยอมรับข้อตรวจ', release: 'ประกาศใช้', acknowledge: 'รับทราบ',
      retire: 'ยกเลิกใช้', new_draft: 'สร้างร่าง' };
    document.getElementById('bmcRlHistory').innerHTML = `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">ประวัติ</div>` +
      (d.rows.map(h => `<div class="text-[11.5px] py-0.5 border-b" style="border-color:var(--border-soft);color:var(--text-secondary)">
        ${esc(fmtAt(h.at))} · <b>${esc(whoTh(h.who))}</b> · ${esc(th[h.action] || h.action)}
        ${h.key && h.key !== String(_bmcRlRel.release_id) ? ' · ' + esc(String(h.key).split('|').slice(1).join(' ')) : ''}</div>`).join('')
        || '<div class="text-xs" style="color:var(--text-muted)">ยังไม่มีประวัติ</div>');
  } catch (e) { /* history is a nicety */ }
}

// ── forecast: suggested order per round (Sprint S4b) ────────────────────────
// Sales Support (supply.ordering) runs it and fills the order calendar; the
// numbers are expected use + safety stock, NOT yet net of what is in the fridge
// (branches start counting stock in Phase 2) — the page says so every time.
let _bmcFc = null;
let _bmcFcKey = '';            // the choice (branch, round, day, season) the calculation on screen was made for
let _bmcCal = null;
let _bmcCalPilotOnly = true;   // the calendar page: the pilot branches alone until the sales team asks for all
const _BMC_WD = ['อาทิตย์', 'จันทร์', 'อังคาร', 'พุธ', 'พฤหัส', 'ศุกร์', 'เสาร์'];
function _bmcFcCanWrite() { return hasModule('supply.ordering') || hasModule('bom.center'); }

async function bmcLoadForecastPane() {
  document.querySelectorAll('#tab-bomcenter .bmc-fc-write').forEach(el => { el.style.display = _bmcFcCanWrite() ? '' : 'none'; });
  const day = document.getElementById('bmcFcDay');
  if (!day.value) day.value = new Date(Date.now() + 7 * 3600e3).toISOString().slice(0, 10);   // Bangkok's date
  await bmcLoadCalendar();
  const sel = document.getElementById('bmcFcBranch');
  if (!sel) return;                          // a pinned login has no picker (and never reaches this centre page)
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
    _bmcFcKey = `${bid}|${rnd}|${day}|${season}`;
    bmcRenderForecast();
    _bmcStatus('bmcFcStatus', '');
  } catch (e) { _bmcStatus('bmcFcStatus', 'ไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
// the figure in one line: the sales of the weeks × the calendar multiplier × the last-year multiplier (a multiplier that does not apply is ×1.00)
function _bmcFcFormula(f) {
  const se = f.season || {}, fac = Number(se.factor || 1);
  const cal = se.mode === 'calendar-manual' ? fac : 1, ly = se.mode === 'auto' ? fac : 1;
  const weeks = Math.max(1, Math.round(((new Date(f.history_to).getTime() - new Date(f.history_from).getTime()) / 864e5 + 1) / 7));
  const d = se.detail || {};
  const src = se.mode === 'calendar-manual' ? `ปฏิทินตั้งไว้สำหรับ${se.event ? ' ' + se.event : 'ช่วงนี้'}`
    : se.mode === 'auto' && d.ly_from ? `${se.event ? se.event + ' · ' : ''}ปีก่อน ${fmtDay(d.ly_from)} – ${fmtDay(d.ly_to)} ลูกค้าเฉลี่ย ${_bmcNum(d.ly_pax_per_day, 0)} คน/วัน เทียบวันปกติในวันเดียวกันของสัปดาห์ ${_bmcNum(d.baseline_pax_per_day, 0)} คน/วัน`
    : (se.reason ? String(se.reason).replace(/[()]/g, '') : '');
  return `ยอดแนะนำ = ยอดขาย ${weeks} สัปดาห์ × ปฏิทิน ×${cal.toFixed(2)} × ปีก่อน ×${ly.toFixed(2)}`
    + (src ? `<span style="color:var(--text-muted)"> · ${esc(src)}</span>` : '');
}
function bmcRenderForecast() {
  const f = _bmcFc;
  const kInk = { A: 'var(--bad-ink)', B: 'var(--amber-ink)', C: 'var(--text-muted)' };
  const fmtQ = (v, u) => v == null ? '—' : (u === 'กรัม' && v >= 1000 ? _bmcNum(v / 1000, 2) + ' กก.' : u === 'มิลลิลิตร' && v >= 1000 ? _bmcNum(v / 1000, 2) + ' ลิตร' : _bmcNum(v, 1) + ' ' + (u || ''));
  // 2026-10-19: the round as tiles, the sources folded, and the table leads with แนะนำสั่ง; a search box filters the rows
  document.getElementById('bmcFcHead').innerHTML = `<div class="card p-4">
    <div class="text-sm font-semibold" style="color:var(--text-primary)">${esc(f.branch)} · ${f.round === 'salmon' ? 'แซลมอนสด' : 'รอบ ' + f.round}</div>
    ${bmcStats([['ตัดรอบ', `${f.order_weekday} ${esc(fmtDay(f.order_day))}`], ['สินค้าถึง', esc(fmtDay(f.delivery))], ['ต้องพอใช้', `${f.cover_days} วัน`],
      ['รายการ', f.rows.length], ['มูลค่าแนะนำ', '฿' + _bmcNum(f.value_total, 0)]])}
    <div class="text-sm" style="color:var(--text-secondary)">ใช้ ${esc(fmtDay(f.cover_from))} ถึง ${esc(fmtDay(f.cover_to))}</div>
    ${f.calendar_filled ? '' : `<div class="text-sm mt-1" style="color:var(--amber-ink)"><b>ยังไม่ได้ตั้งรอบส่งของสาขานี้ (ใช้ค่าเริ่มต้น)</b> <a href="#" onclick="supplyGo({tab:'bomcenter',pane:'cal'});return false" style="color:var(--amber-ink);text-decoration:underline">ตั้งค่ารอบส่ง →</a></div>`}
    <div id="bmcFcFormula" class="text-sm mt-2" style="color:var(--text-primary)">${_bmcFcFormula(f)}</div>
    <div class="text-sm mt-2 p-2" style="background:var(--bg-subtle);border-radius:var(--r-sm);color:var(--amber-ink)">ยอดแนะนำ = ปริมาณที่คาดว่าจะใช้ + กันขาด · <b>ยังไม่หักคงเหลือของสาขา</b> (หน้าสั่งสินค้าหักให้)</div>
    <details class="mt-2"><summary class="text-sm cursor-pointer" style="color:var(--text-secondary)">ที่มาของตัวเลข</summary>
      <div class="text-sm mt-1" style="color:var(--text-secondary)">${bmcLines([`ยอดขาย ${esc(fmtDay(f.history_from))} ถึง ${esc(fmtDay(f.history_to))} (${f.trading_days} วันที่มีการขาย, เฉพาะช่วงเมนูใหม่)`,
        `สูตร: ${f.stats.release_skus ? `เวอร์ชันที่ประกาศใช้แล้ว ${f.stats.release_skus} เมนู + ` : ''}สูตรระบบเดิมสำหรับเมนูที่เหลือ`])}</div></details>
    ${f.stats.missed_lines ? `<details class="mt-2"><summary class="text-sm cursor-pointer" style="color:var(--amber-ink)">แปลงหน่วยไม่ได้ ${f.stats.missed_lines} บรรทัด <a href="#" onclick="event.stopPropagation();supplyGo({tab:'bomcenter',pane:'conv'});return false" style="color:var(--amber-ink);text-decoration:underline">แก้ไขที่ หน่วยและการแปลง →</a></summary>
      <div class="wt-wrap mt-1" style="max-height:280px"><table class="wt"><thead><tr><th>รหัส</th><th>หน่วย</th><th class="wt-num">จาน</th><th>สาเหตุ</th></tr></thead><tbody>
      ${f.stats.missed.map(m => `<tr><td class="wt-code" data-l="รหัส">${esc(m.code)}</td><td data-l="หน่วย">${esc(m.unit)}</td><td class="wt-num" data-l="จาน">${_bmcNum(m.plates, 0)}</td><td data-l="สาเหตุ">${esc(m.why)}</td></tr>`).join('')}</tbody></table></div></details>` : ''}
  </div>`;
  bmcRenderFcTable();
}
let _bmcFcFind = '';
function bmcFcFind(q) { _bmcFcFind = q; bmcRenderFcTable(true); }
function bmcRenderFcTable(keepFocus) {
  const f = _bmcFc, box = document.getElementById('bmcFcTable');
  if (!f || !box) return;
  const kInk = { A: 'var(--bad-ink)', B: 'var(--amber-ink)', C: 'var(--text-muted)' };
  const fmtQ = (v, u) => v == null ? '—' : (u === 'กรัม' && v >= 1000 ? _bmcNum(v / 1000, 2) + ' กก.' : u === 'มิลลิลิตร' && v >= 1000 ? _bmcNum(v / 1000, 2) + ' ลิตร' : _bmcNum(v, 1) + ' ' + (u || ''));
  const q = _bmcFcFind.trim().toLowerCase();
  const rows = f.rows.filter(r => !q || (r.code + ' ' + (r.name || '')).toLowerCase().includes(q));
  const notes = r => [r.monitored ? _bmcChip('บังคับสั่ง', 'var(--navy-ink)') : '', r.sourcing === 'local' ? _bmcChip('สาขาซื้อเอง', 'var(--text-muted)') : '',
    r.shelf_cap_days ? _bmcChip(`จำกัดตามอายุการเก็บ ${r.shelf_cap_days} วัน`, 'var(--navy-ink)') : '', r.round_down ? _bmcChip('ปัดลง (เหลือพอ)', 'var(--navy-ink)') : '',
    r.price_note ? _bmcChip(r.price_note, 'var(--text-muted)') : '', r.lead_extra_days ? _bmcChip(`สั่งให้พอเพิ่ม ${r.lead_extra_days} วัน (ระยะส่ง ${r.lead_days} วัน)`, 'var(--navy-ink)') : '',
    r.short_ship_skipped ? _bmcChip(`ไม่นับ ${r.short_ship_skipped} วันที่คลังส่งไม่ครบ`, 'var(--text-muted)') : ''].filter(Boolean).join(' ');
  box.style.display = '';
  box.innerHTML = `<div class="wt-bar"><input id="bmcFcFindBox" class="wt-find" type="search" placeholder="ค้นหารหัสหรือชื่อวัตถุดิบ" value="${esc(_bmcFcFind)}" oninput="bmcFcFind(this.value)">
      <span class="text-sm" style="color:var(--text-muted)">${q ? `พบ ${rows.length} จาก ` : ''}${f.rows.length} รายการ · กลุ่ม A = มูลค่าสูง</span></div>
    <div class="wt-wrap" style="max-height:70vh"><table class="wt"><thead><tr>
      <th>รหัส</th><th>วัตถุดิบ</th><th>กลุ่ม</th><th class="wt-num">แนะนำสั่ง</th><th class="wt-num">มูลค่า ฿</th>
      <th class="wt-num">ใช้/วัน</th><th class="wt-num">ทั้งรอบ</th><th class="wt-num">กันขาด</th><th class="wt-num">แนวโน้ม</th></tr></thead>
    <tbody>${rows.map(r => `<tr>
      <td class="wt-code" data-l="รหัส">${esc(r.code)}</td><td data-l="วัตถุดิบ" style="min-width:200px">${esc(r.name || '')}${notes(r) ? `<div class="mt-1" style="display:flex;gap:4px;flex-wrap:wrap">${notes(r)}</div>` : ''}</td>
      <td data-l="กลุ่ม">${_bmcChip(r.class, kInk[r.class])}</td>
      <td class="wt-num" data-l="แนะนำสั่ง" style="font-weight:700;font-size:15px">${r.suggested_units != null ? `${_bmcNum(r.suggested_units, 0)} <span style="font-weight:500;font-size:13px">${esc(r.recv_unit || '')}</span>` : fmtQ(r.need, r.base_unit) + `<div>${_bmcChip('ไม่มีขนาดบรรจุ', 'var(--amber-ink)')}</div>`}</td>
      <td class="wt-num" data-l="มูลค่า ฿">${_bmcNum(r.value, 0)}</td>
      <td class="wt-num wt-muted" data-l="ใช้/วัน">${fmtQ(r.per_day, r.base_unit)}</td><td class="wt-num wt-muted" data-l="ทั้งรอบ">${fmtQ(r.forecast, r.base_unit)}</td>
      <td class="wt-num wt-muted" data-l="กันขาด">${fmtQ(r.safety, r.base_unit)}</td>
      <td class="wt-num" data-l="แนวโน้ม" style="color:${r.trend > 1.05 ? 'var(--amber-ink)' : r.trend < 0.95 ? 'var(--good-ink)' : 'var(--text-muted)'}">${r.trend > 1 ? '+' : ''}${Math.round((r.trend - 1) * 100)}%</td></tr>`).join('')
      || `<tr><td colspan="9" class="wt-empty">${q ? 'ไม่พบรายการที่ตรงกับคำค้น' : 'ไม่มีวัตถุดิบในรอบนี้'}</td></tr>`}</tbody></table></div>`;
  if (keepFocus) { const el = document.getElementById('bmcFcFindBox'); if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); } }
}
async function bmcSaveForecast() {
  const bid = Number(document.getElementById('bmcFcBranch').value);
  if (!bid) { bmcStatus('bmcFcStatus', 'warn', 'เลือกสาขาก่อนบันทึก'); return; }
  const body = { branch_id: bid, round: document.getElementById('bmcFcRound').value, order_day: document.getElementById('bmcFcDay').value,
    season: document.getElementById('bmcFcSeason').checked ? 'auto' : 'off' };
  // the question carries the numbers of what is on the screen; without a calculation for this choice, calculate it first
  const key = `${bid}|${body.round}|${body.order_day}|${body.season}`;
  if (!_bmcFc || _bmcFcKey !== key) await bmcRunForecast();
  if (!_bmcFc || _bmcFcKey !== key) return;
  const f = _bmcFc;
  if (!await uiConfirm(`บันทึกยอดแนะนำเป็นรอบสั่ง?\n${f.branch} · ${f.round === 'salmon' ? 'แซลมอนสด' : 'รอบ ' + f.round} · ตัดรอบ ${f.order_weekday} ${fmtDay(f.order_day)} · ${f.rows.length} รายการ · ฿${_bmcNum(f.value_total, 0)}\nใช้เป็นฐานเทียบในหน้าสั่งสินค้าของสาขาและหน้าตรวจสอบการสั่ง`,
      { title: 'ยืนยันการบันทึกยอดแนะนำ', ok: 'บันทึกเป็นรอบสั่ง', cancel: 'ยังไม่บันทึก' })) { bmcStatus('bmcFcStatus', null, 'ยังไม่บันทึก'); return; }
  try {
    const d = await _bmcJson(`${API}/api/bom-center/forecast/run`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    bmcStatus('bmcFcStatus', true, `บันทึกยอดแนะนำแล้ว · ${d.lines} รายการ · ฿${_bmcNum(d.value_total, 0)}`);
    bmcLoadRuns();
  } catch (e) { bmcStatus('bmcFcStatus', false, String(e.message || e)); }
}
async function bmcBacktest() {
  const bid = document.getElementById('bmcFcBranch').value;
  _bmcStatus('bmcFcStatus', 'กำลังทดสอบความแม่นยำย้อนหลัง…');
  try {
    const b = await _bmcJson(`${API}/api/bom-center/forecast/backtest?branch_id=${bid}&weeks=3`);
    const c = b.mape_by_class || {};
    _bmcStatus('bmcFcStatus', b.available ? `ทดสอบย้อนหลัง 3 สัปดาห์ (ถึง ${fmtDay(b.as_of)}) ความคลาดเคลื่อนรายสัปดาห์: A ${c.A ?? '—'}% · B ${c.B ?? '—'}% · C ${c.C ?? '—'}% (เป้า A ≤ ${b.target_a}%)` : 'ข้อมูลไม่พอ',
      c.A != null && c.A <= b.target_a ? 'var(--good-ink)' : 'var(--amber-ink)');
  } catch (e) { _bmcStatus('bmcFcStatus', 'ไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)'); }
}
async function bmcLoadCalendar() {
  try { _bmcCal = await _bmcJson(`${API}/api/bom-center/order-calendar`); } catch (e) { return; }
  const w = _bmcFcCanWrite();
  // the sales team sets the DELIVERY weekday only; the cutoff (13:15, two days before — a Tuesday delivery: Saturday) is derived
  const sel = (bid, rnd, val) => w
    ? `<select data-bid="${bid}" data-rnd="${rnd}" data-f="delivery_weekday" onchange="this.style.background='var(--accent-soft)';this.dataset.dirty='1';bmcCalCutoff(this)" aria-label="วันส่ง" style="border:1px solid var(--border-control);border-radius:8px;font-size:14px;min-height:34px;padding:0 6px;background:var(--bg-input);color:var(--text-primary)">
        ${_BMC_WD.map((n, o) => `<option value="${o}" ${Number(val) === o ? 'selected' : ''}>${n}</option>`).join('')}</select>`
    : _BMC_WD[val];
  const cut = (bid, rnd, th) => `<div class="wt-muted" style="font-size:13px;white-space:nowrap">ตัดรอบ <span id="bmcCut_${bid}_${rnd}">${esc(th || '')}</span></div>`;
  // PR-V · สาขาอ้างอิงสำหรับสาขาใหม่: saved as soon as it is chosen (it is not one of the delivery days)
  const refSel = (r) => w
    ? `<select onchange="bmcSetRefBranch(${r.branch_id}, this)" aria-label="สาขาอ้างอิงของ${esc(r.branch)}" style="border:1px solid var(--border-control);border-radius:8px;font-size:14px;min-height:34px;background:var(--bg-input);color:var(--text-primary);max-width:200px">
        <option value="benchmark" ${r.ref_mode === 'benchmark' ? 'selected' : ''}>ค่ากลางสาขาของบริษัท</option><option value="none" ${r.ref_mode === 'none' ? 'selected' : ''}>ไม่ใช้</option>${_bmcCal.rows.filter(x => x.branch_id !== r.branch_id).map(x => `<option value="${x.branch_id}" ${Number(r.ref_branch_id) === x.branch_id ? 'selected' : ''}>${esc(x.branch)}</option>`).join('')}</select>`
    : (r.ref_mode === 'benchmark' ? 'ค่ากลางสาขาของบริษัท' : r.ref_mode === 'none' ? 'ไม่ใช้' : esc(((_bmcCal.rows.find(x => x.branch_id === r.ref_branch_id) || {}).branch) || '–'));
  const pilot = _bmcCal.pilot || [];
  const calRows = (_bmcCalPilotOnly && pilot.length) ? _bmcCal.rows.filter(r => pilot.includes(r.branch)) : _bmcCal.rows;
  // 2026-10-19: one cell per round (วันส่ง, ตัดรอบ under it), the explanation folded, the save button on the title row
  const rnd = (r, k, x) => `<td data-l="${k === '1' ? 'รอบ 1' : k === '2' ? 'รอบ 2' : 'แซลมอนสด'}">${sel(r.branch_id, k, x.delivery_weekday)}${cut(r.branch_id, k, x.cutoff_th)}</td>`;
  document.getElementById('bmcFcCal').innerHTML = `<div class="flex items-center flex-wrap gap-3 mb-2"><div class="text-sm font-semibold" style="color:var(--text-primary)">รอบส่งของแต่ละสาขา</div>
      ${pilot.length ? `<label class="text-sm flex items-center gap-2" style="color:var(--text-secondary)"><input type="checkbox" ${_bmcCalPilotOnly ? 'checked' : ''} onchange="_bmcCalPilotOnly=this.checked;bmcLoadCalendar()" style="width:18px;height:18px"> เฉพาะสาขานำร่อง (${pilot.length})</label>` : ''}
      <span class="text-sm" style="color:var(--text-muted)">${calRows.length} จาก ${_bmcCal.rows.length} สาขา</span>
      ${w ? `<button type="button" class="wt-btn is-primary ml-auto" onclick="bmcSaveCalendar()">บันทึกรอบส่ง</button>` : ''}</div>
    ${w ? '<div class="wt-note mb-2">เลือกวันส่ง แล้วกด “บันทึกรอบส่ง” (บันทึกเฉพาะแถวที่แก้ไข) · เวลาตัดรอบคำนวณให้เอง · สาขาอ้างอิงบันทึกทันทีที่เลือก</div>' : ''}
    <div class="wt-wrap" style="max-height:70vh"><table class="wt"><thead><tr>
      <th>สาขา</th><th>รอบ 1</th><th>รอบ 2</th><th>แซลมอนสด</th><th>สาขาอ้างอิง (สาขาใหม่)</th><th>ตั้งโดย</th></tr></thead>
    <tbody>${calRows.map(r => `<tr><td data-l="สาขา" style="font-weight:600;white-space:nowrap">${esc(r.branch)}</td>
      ${rnd(r, '1', r.r1)}${rnd(r, '2', r.r2)}${rnd(r, 'salmon', r.rsalmon)}
      <td data-l="สาขาอ้างอิง">${refSel(r)}</td>
      <td data-l="ตั้งโดย" class="wt-muted" style="white-space:nowrap">${[r.r1, r.r2, r.rsalmon].some(x => x.filled) ? esc(whoTh([r.r1, r.r2, r.rsalmon].map(x => x.filled_by).filter(Boolean)[0] || '')) : '<span style="color:var(--amber-ink)">ค่าเริ่มต้น</span>'}</td></tr>`).join('')}</tbody></table></div>
    <details class="mt-2"><summary class="text-sm cursor-pointer" style="color:var(--text-secondary)">สาขาอ้างอิงคืออะไร</summary>
      <div class="text-sm mt-1" style="color:var(--text-secondary)">สาขาที่ขายไม่ถึง ${_bmcCal.new_branch_days || 14} วัน คำนวณยอดแนะนำจากปริมาณวัตถุดิบต่อยอดขาย ฿100,000 ของสาขาอ้างอิง คูณยอดขายที่สาขากรอก · ค่าเริ่มต้นคือค่ากลางสาขาของบริษัท (มัธยฐานของสาขาที่เลือกไว้ในกรอบด้านล่าง)</div></details>`;
  const bulk = document.getElementById('bmcCalBulk');          // the file import is the sales team's, like the table above
  if (bulk) bulk.style.display = w ? '' : 'none';
  bmcLoadBenchmark();
  bmcLoadCloseSettings();
}
// ── batch 4b (3.3 · 3.4) · เกณฑ์ผลต่างและการตรวจนับ: the baht floor of a variance reason, and the weekly-count switch ──
let _bmcCs = null;
async function bmcLoadCloseSettings() {
  const box = document.getElementById('bmcCloseCard');
  if (!box) return;
  try { _bmcCs = await _bmcJson(`${API}/api/bom-center/close-settings`); } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; return; }
  const c = _bmcCs, w = hasModule('bom.center');
  box.innerHTML = `<div class="text-sm font-semibold" style="color:var(--text-primary)">เกณฑ์ผลต่างและการตรวจนับ</div>
    <p class="text-xs mt-1 mb-2" style="color:var(--text-secondary)">สาขาให้เหตุผลผลต่างเฉพาะรายการที่เกินเกณฑ์ร้อยละของกลุ่มมูลค่า และต่างตั้งแต่จำนวนบาทนี้ขึ้นไป · รายการที่ใช้จริงเป็น 0 หรืองวดที่ไม่มีใบรับสินค้าหรือขาดใบตัดเบิก ติดป้าย “ข้อมูลไม่ครบ” และไม่ถาม</p>
    <div class="flex flex-wrap items-end gap-3">
      <label class="text-xs" style="color:var(--text-muted)">ผลต่างขั้นต่ำที่ต้องให้เหตุผล (บาท)<br>
        <input id="bmcCsBaht" type="number" min="${c.reason_min_baht_min}" max="${c.reason_min_baht_max}" step="50" value="${c.reason_min_baht}" ${w ? '' : 'disabled'} style="border:1px solid var(--border-control);border-radius:var(--r-sm);padding:5px 8px;font-size:13px;width:120px;background:var(--bg-input);color:var(--text-primary)"></label>
      <label class="text-xs flex items-center gap-2" style="color:var(--text-primary)"><input id="bmcCsCycle" type="checkbox" ${c.cycle_counts ? 'checked' : ''} ${w ? '' : 'disabled'}>
        ตรวจนับหมุนเวียน (ไม่ใช่การปิดงวด): กลุ่มมูลค่าสูงนับเสริมทุกสัปดาห์ · ปิดงวดสิ้นเดือนต้องนับครบทุกรายการเสมอ</label>
      ${w ? `<button onclick="bmcSaveCloseSettings()" style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:6px 16px;font-size:13px;cursor:pointer">บันทึก</button>` : ''}
      <span id="bmcCsStatus" class="text-[11px]" style="color:var(--text-muted)"></span></div>
    <div class="text-[11px] mt-2" style="color:var(--text-muted)">ค่าเริ่มต้น ${_bmcNum(c.reason_min_baht_default, 0)} บาท (ตั้งได้ ${_bmcNum(c.reason_min_baht_min, 0)} ถึง ${_bmcNum(c.reason_min_baht_max, 0)}; 0 = ไม่มีเกณฑ์บาท) · ปิดงวดสิ้นเดือนต้องตรวจนับครบทุกรายการ ไม่ว่าจะเปิดหรือปิดการตรวจนับหมุนเวียน (มติเจ้าของ 13/10/2026)</div>`;
}
async function bmcSaveCloseSettings() {
  const baht = Number(document.getElementById('bmcCsBaht').value), cyc = document.getElementById('bmcCsCycle').checked;
  if (!(baht >= _bmcCs.reason_min_baht_min && baht <= _bmcCs.reason_min_baht_max)) { bmcStatus('bmcCsStatus', 'warn', `เกณฑ์บาทต้องอยู่ระหว่าง ${_bmcNum(_bmcCs.reason_min_baht_min, 0)} ถึง ${_bmcNum(_bmcCs.reason_min_baht_max, 0)}`); return; }
  if (!await uiConfirm(`บันทึกเกณฑ์ผลต่างขั้นต่ำ ฿${_bmcNum(baht, 0)} และ${cyc ? 'เปิด' : 'ปิด'}การตรวจนับหมุนเวียนเสริม (ไม่ใช่การปิดงวด)?\nผลต่างที่ยังไม่ได้ให้เหตุผลจะนับใหม่ตามเกณฑ์นี้`,
    { title: 'ยืนยันการบันทึกเกณฑ์', ok: 'บันทึก', cancel: 'ยังไม่บันทึก' })) { bmcStatus('bmcCsStatus', null, 'ยังไม่บันทึก'); return; }
  try {
    _bmcCs = await _bmcJson(`${API}/api/bom-center/close-settings`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason_min_baht: baht, cycle_counts: cyc }) });
    bmcStatus('bmcCsStatus', true, `บันทึกแล้ว · ผลต่างขั้นต่ำ ฿${_bmcNum(baht, 0)} · ตรวจนับหมุนเวียน${cyc ? 'เปิด' : 'ปิด'}`);
  } catch (e) { bmcStatus('bmcCsStatus', false, String(e.message || e)); }
}
// ── PR-B4a (1.8) · ปฏิทินเทศกาลและวันหยุด: the events (promotions, festivals) the forecast reads, and the public holidays, read only ──
let _bmcEv = null, _bmcEvEditId = null;
function _bmcEvCanWrite() { return hasModule('supply.ordering') || hasModule('bom.center'); }
function _bmcEvPctTxt(e) { return e.uplift_pct == null ? 'ใช้ยอดลูกค้าปีก่อน' : `${e.uplift_pct > 0 ? '+' : ''}${_bmcNum(e.uplift_pct, 0)}%`; }
async function bmcLoadEvents() {
  const form = document.getElementById('bmcEvForm');
  if (form) form.style.display = _bmcEvCanWrite() ? '' : 'none';
  try { _bmcEv = await _bmcJson(`${API}/api/bom-center/event-calendar`); }
  catch (e) { document.getElementById('bmcEvList').innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; return; }
  const sel = document.getElementById('bmcEvBranch');
  if (sel && !sel.options.length) sel.innerHTML = '<option value="">ทุกสาขา</option>' + _bmcEv.branches.map(b => `<option value="${b.branch_id}">${esc(b.name)}</option>`).join('');
  const w = _bmcEvCanWrite();
  document.getElementById('bmcEvList').innerHTML = _bmcEv.events.length
    ? `<div class="overflow-x-auto"><table class="text-xs w-full"><thead><tr class="text-left border-b" style="color:var(--text-muted)"><th class="pr-2">ชื่อ</th><th class="pr-2">ตั้งแต่</th><th class="pr-2">ถึง</th><th class="pr-2">สาขา</th><th class="pr-2">ลูกค้าเพิ่มจากวันปกติ</th><th class="pr-2">หมายเหตุ</th><th></th></tr></thead><tbody>${
      _bmcEv.events.map(e => `<tr class="border-b" style="border-color:var(--border-soft)"><td class="pr-2 py-1 font-semibold">${esc(e.name)}</td><td class="pr-2">${esc(fmtDay(e.date_from))}</td><td class="pr-2">${esc(fmtDay(e.date_to))}</td>
        <td class="pr-2">${esc(e.branch || 'ทุกสาขา')}</td><td class="pr-2">${esc(_bmcEvPctTxt(e))}</td><td class="pr-2" style="color:var(--text-muted)">${esc(e.note || '')}</td>
        <td class="text-right whitespace-nowrap">${w ? `<a href="#" onclick="bmcEvEdit(${e.event_id});return false" style="color:var(--accent)">แก้ไข</a> · <a href="#" onclick="bmcEvDel(${e.event_id});return false" style="color:var(--bad-ink)">ลบ</a>` : ''}</td></tr>`).join('')}</tbody></table></div>`
    : '<span class="text-xs" style="color:var(--text-muted)">ยังไม่มีช่วงเทศกาลหรือโปรโมชันในช่วงนี้</span>';
  const byMonth = {};
  _bmcEv.holidays.forEach(h => { (byMonth[h.date.slice(0, 7)] = byMonth[h.date.slice(0, 7)] || []).push(h); });
  document.getElementById('bmcEvHol').innerHTML = Object.keys(byMonth).length
    ? Object.entries(byMonth).map(([m, hs]) => `<div class="mb-2"><div class="text-[11px] font-semibold" style="color:var(--text-secondary)">${esc(fmtMonthTh(m))}</div>
        <div class="flex flex-wrap gap-x-4 gap-y-1">${hs.map(h => `<span class="text-xs" style="color:var(--text-primary)">${esc(fmtDayShort(h.date))} ${esc(h.weekday_th)} · ${esc(h.name)} ${_bmcChip('วันหยุด', 'var(--navy-ink)')}</span>`).join('')}</div></div>`).join('')
    : '<span class="text-xs" style="color:var(--text-muted)">ไม่มีวันหยุดราชการในช่วงนี้ (ตารางวันหยุดของระบบครอบคลุม พ.ศ. 2567–2570)</span>';
}
function bmcEvReset() {
  _bmcEvEditId = null;
  ['bmcEvName', 'bmcEvFrom', 'bmcEvTo', 'bmcEvPct', 'bmcEvNote'].forEach(id => { document.getElementById(id).value = ''; });
  document.getElementById('bmcEvBranch').value = '';
  document.getElementById('bmcEvFormTitle').textContent = 'เพิ่มช่วงเทศกาลหรือโปรโมชัน';
  document.getElementById('bmcEvSave').textContent = 'เพิ่ม';
  document.getElementById('bmcEvCancel').style.display = 'none';
}
function bmcEvEdit(id) {
  const e = (_bmcEv.events || []).find(x => x.event_id === id);
  if (!e) return;
  _bmcEvEditId = id;
  document.getElementById('bmcEvName').value = e.name;
  document.getElementById('bmcEvFrom').value = e.date_from;
  document.getElementById('bmcEvTo').value = e.date_to;
  document.getElementById('bmcEvBranch').value = e.branch_id == null ? '' : String(e.branch_id);
  document.getElementById('bmcEvPct').value = e.uplift_pct == null ? '' : e.uplift_pct;
  document.getElementById('bmcEvNote').value = e.note || '';
  document.getElementById('bmcEvFormTitle').textContent = 'แก้ไขช่วงเทศกาลหรือโปรโมชัน';
  document.getElementById('bmcEvSave').textContent = 'บันทึกการแก้ไข';
  document.getElementById('bmcEvCancel').style.display = '';
  document.getElementById('bmcEvName').scrollIntoView({ block: 'center' });
}
async function bmcEvSave() {
  const name = document.getElementById('bmcEvName').value.trim(), from = document.getElementById('bmcEvFrom').value, to = document.getElementById('bmcEvTo').value;
  if (!name || !from || !to) { bmcStatus('bmcEvStatus', 'warn', 'กรอกชื่อและช่วงวันที่ให้ครบ'); return; }
  if (to < from) { bmcStatus('bmcEvStatus', 'warn', 'วันที่สิ้นสุดต้องไม่ก่อนวันที่เริ่ม'); return; }
  const pctRaw = document.getElementById('bmcEvPct').value.trim();
  const pct = pctRaw === '' ? null : Number(pctRaw);
  if (pct !== null && !(pct >= -50 && pct <= 200)) { bmcStatus('bmcEvStatus', 'warn', '% ที่เพิ่มต้องอยู่ระหว่าง -50 ถึง +200'); return; }
  const bid = document.getElementById('bmcEvBranch').value;
  try {
    await _bmcJson(`${API}/api/bom-center/event-calendar`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event_id: _bmcEvEditId, name, date_from: from, date_to: to, branch_id: bid ? Number(bid) : null, uplift_pct: pct,
        note: document.getElementById('bmcEvNote').value.trim() || null }) });
    bmcEvReset();
    bmcStatus('bmcEvStatus', true, 'บันทึกแล้ว · ยอดแนะนำที่คำนวณใหม่ใช้ช่วงนี้');
    bmcLoadEvents();
  } catch (e) { bmcStatus('bmcEvStatus', false, 'ไม่ได้บันทึก: ' + String(e.message || e).slice(0, 160)); }
}
async function bmcEvDel(id) {
  const e = (_bmcEv.events || []).find(x => x.event_id === id);
  if (!await uiConfirm(`ลบช่วง “${e ? e.name : ''}” ออกจากปฏิทิน?`, { title: 'ลบช่วงเทศกาล', ok: 'ลบ', cancel: 'ไม่ลบ' })) return;
  try { await _bmcJson(`${API}/api/bom-center/event-calendar/${id}`, { method: 'DELETE' }); bmcLoadEvents(); }
  catch (er) { bmcStatus('bmcEvStatus', false, 'ไม่ได้ลบ: ' + String(er.message || er).slice(0, 160)); }
}
// ── PR-X · สาขาของบริษัทที่ใช้เป็นค่ากลาง: which branches make the benchmark, and the temporary stock buffer ──
let _bmcBm = null;
async function bmcLoadBenchmark() {
  const box = document.getElementById('bmcBmCard');
  if (!box) return;
  try { _bmcBm = await _bmcJson(`${API}/api/bom-center/benchmark`); } catch (e) { box.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; return; }
  const b = _bmcBm, w = _bmcFcCanWrite();
  const typ = t => t === 'master' ? 'สาขาของบริษัท' : 'แฟรนไชส์';
  box.innerHTML = `<div class="text-sm font-semibold" style="color:var(--text-primary)">สาขาของบริษัทที่ใช้เป็นค่ากลาง</div>
    <p class="text-xs mt-1 mb-2" style="color:var(--text-secondary)">ปริมาณที่ควรสั่งอ้างอิงสาขาที่บริษัทดำเนินการเอง เพราะสาขาแฟรนไชส์อาจซื้อวัตถุดิบจากภายนอกด้วย · ค่ากลางคือมัธยฐานของสาขาที่เลือก (ต้องมีข้อมูลอย่างน้อย ${b.min_branches} สาขา) ใช้เป็นเกณฑ์เทียบในรายงานต่อยอดขาย ฿100,000 ในหน้าตรวจสูตรด้วยยอดซื้อ และเป็นค่าอ้างอิงเริ่มต้นของสาขาใหม่</p>
    <div id="bmcBmList" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:6px 16px">${b.branches.filter(x => x.active).map(x => `<label class="text-xs flex items-center gap-2" style="color:var(--text-primary);min-height:32px">
      <input type="checkbox" value="${x.branch_id}" ${x.selected ? 'checked' : ''} ${w ? '' : 'disabled'} style="width:18px;height:18px"> ${esc(x.name)} <span class="text-[10.5px]" style="color:var(--text-muted)">${typ(x.branch_type)}</span></label>`).join('')}</div>
    <div class="flex flex-wrap items-end gap-3 mt-3">
      <label class="text-xs" style="color:var(--text-muted)">ช่วงยอมรับสำหรับสต็อกต้นงวดและปลายงวด (%)<br>
        <input id="bmcBmBuf" type="number" min="${b.buffer_min}" max="${b.buffer_max}" step="1" value="${b.buffer_pct}" ${w ? '' : 'disabled'} style="border:1px solid var(--border-control);border-radius:var(--r-sm);padding:5px 8px;font-size:13px;width:90px;background:var(--bg-input);color:var(--text-primary)"></label>
      ${w ? `<button onclick="bmcSaveBenchmark()" style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:6px 16px;font-size:13px;cursor:pointer">บันทึกค่ากลาง</button>` : ''}
      <span id="bmcBmStatus" class="text-[11px]" style="color:var(--text-muted)"></span></div>
    <div class="text-[11px] mt-2" style="color:var(--text-muted)">${esc(b.buffer_note)} · สาขาที่มีผลตรวจนับต้นงวดและปลายงวดครบจะใช้ยอดซื้อปรับด้วยส่วนต่างของสต็อก และเกณฑ์ผลต่างปกติแทนช่วงยอมรับนี้${b.is_default ? ' · ขณะนี้ใช้ค่าเริ่มต้น: ' + esc(b.names_default.join(' · ')) : ''}</div>`;
}
async function bmcSaveBenchmark() {
  const ids = [...document.querySelectorAll('#bmcBmList input:checked')].map(x => Number(x.value));
  const buf = Number(document.getElementById('bmcBmBuf').value);
  if (ids.length < ((_bmcBm && _bmcBm.min_branches) || 2)) { bmcStatus('bmcBmStatus', 'warn', `เลือกสาขาอย่างน้อย ${(_bmcBm && _bmcBm.min_branches) || 2} สาขา`); return; }
  if (!(buf >= 0 && buf <= 50)) { bmcStatus('bmcBmStatus', 'warn', 'ช่วงยอมรับต้องอยู่ระหว่าง 0 ถึง 50'); return; }
  if (!await uiConfirm(`บันทึกสาขาที่ใช้เป็นค่ากลาง ${ids.length} สาขา และช่วงยอมรับ ±${buf}%?\nรายงานต่อยอดขาย ฿100,000 และหน้าตรวจสูตรด้วยยอดซื้อจะคำนวณใหม่ตามนี้`,
    { title: 'ยืนยันการบันทึกค่ากลาง', ok: 'บันทึกค่ากลาง', cancel: 'ยังไม่บันทึก' })) { bmcStatus('bmcBmStatus', null, 'ยังไม่บันทึก'); return; }
  try {
    _bmcBm = await _bmcJson(`${API}/api/bom-center/benchmark`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ branch_ids: ids, buffer_pct: buf }) });
    bmcStatus('bmcBmStatus', true, `บันทึกค่ากลางแล้ว · ${ids.length} สาขา · ช่วงยอมรับ ±${buf}%`);
    bmcLoadBenchmark();
  } catch (e) { bmcStatus('bmcBmStatus', false, String(e.message || e)); }
}
// ── PR-X · ตรวจสูตรและหน่วยด้วยยอดซื้อจริง (analyst / management): what the company branches bought against what the recipes say ──
let _bmcRc = null, _bmcRcFilter = 'all', _bmcRcAll = false;
const _BMC_RC_ST = [['x10', 'ต่างกันเกิน 10 เท่า · ตรวจหน่วย', 'var(--bad-ink)'], ['no_recipe', 'มีการซื้อ แต่ไม่มีในสูตร', 'var(--amber-ink)'],
  ['no_buy', 'มีในสูตร แต่ไม่มีการซื้อ · ตรวจรหัส', 'var(--amber-ink)'], ['more', 'ซื้อมากกว่าสูตร', 'var(--amber-ink)'],
  ['less', 'ซื้อน้อยกว่าสูตร', 'var(--amber-ink)'], ['ok', 'ในเกณฑ์', 'var(--good-ink)']];
function _bmcRcMonthEl() { return document.getElementById('bmcRcMonth'); }
async function bmcLoadRcheck(month) {
  const body = document.getElementById('bmcRcBody');
  if (!body) return;
  const el = _bmcRcMonthEl();
  const m = month || (el && el.value) || '';
  body.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังคำนวณ…</span>';
  try {
    _bmcRc = await _bmcJson(`${API}/api/bom-center/recipe-check${m ? '?month=' + encodeURIComponent(m) : ''}`);
  } catch (e) { body.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; return; }
  const r = _bmcRc;
  if (el) {
    el.innerHTML = (r.months || []).map(x => `<option value="${x.month}" ${x.month === r.month ? 'selected' : ''}>${esc(fmtMonthTh(x.month))} · ${x.branches} สาขา${x.complete ? '' : ' (เดือนยังไม่จบ)'}</option>`).join('')
      || '<option value="">ยังไม่มียอดซื้อรายสาขา</option>';
  }
  bmcRenderRcheck();
}
function bmcRcSetFilter(k) { _bmcRcFilter = k; _bmcRcAll = false; bmcRenderRcheck(); }
function bmcRcOpenUnit(code) {
  supplyGo({ tab: 'bomcenter', pane: 'conv' });
  setTimeout(() => { const el = document.getElementById('bmcConvCode'); if (el) { el.value = code; bmcLoadLadder(); } }, 500);
}
function bmcRcOpenIng(code) {
  supplyGo({ tab: 'bomcenter', pane: 'ing' });
  setTimeout(() => { const q = document.getElementById('bmcIngQ'); if (q) { q.value = code; if (typeof bmcIngFilter === 'function') bmcIngFilter(); } }, 700);
}
function bmcRcXlsx() {
  const m = (_bmcRcMonthEl() || {}).value || '';
  bmcDownload(`${API}/api/bom-center/recipe-check.xlsx${m ? '?month=' + encodeURIComponent(m) : ''}`, 'ตรวจสูตรด้วยยอดซื้อ.xlsx', 'bmcRcStatus');
}
function bmcRenderRcheck() {
  const r = _bmcRc, body = document.getElementById('bmcRcBody'), chips = document.getElementById('bmcRcChips'), notes = document.getElementById('bmcRcNotes');
  if (!r || !body) return;
  if (!r.month || !r.enough) {
    chips.innerHTML = ''; notes.innerHTML = '';
    body.innerHTML = `<span class="text-xs" style="color:var(--text-muted)">${esc(r.message || 'ยังไม่มีข้อมูลพอ')}${(r.skipped_branches || []).length ? ' · ' + r.skipped_branches.map(x => esc((x.branch || '') + ': ' + x.why)).join(' · ') : ''}</span>`;
    return;
  }
  const c = r.counts || {};
  chips.innerHTML = [['all', 'ทั้งหมด', r.rows.length, 'var(--navy-ink)']].concat(_BMC_RC_ST.map(([k, t, ink]) => [k, t, c[k] || 0, ink])).map(([k, t, n, ink]) =>
    `<button onclick="bmcRcSetFilter('${k}')" style="border:1px solid ${_bmcRcFilter === k ? ink : 'var(--border)'};background:${_bmcRcFilter === k ? 'var(--accent-soft)' : 'transparent'};color:${ink};border-radius:999px;padding:4px 14px;font-size:14px;min-height:34px;cursor:pointer">${esc(t)} <b>${n}</b></button>`).join(' ');
  const lines = [];
  lines.push(`ค่ากลางสาขาของบริษัท = มัธยฐานของ ${r.branches.map(b => esc(b.branch)).join(', ')}`);
  lines.push('ยอดขายไม่รวมภาษีมูลค่าเพิ่มและค่าบริการ · หน่วยเป็นหน่วยฐาน');
  if (r.scaled) lines.push(`<b>ปรับยอดซื้อตามวันที่มียอดขายรายวัน</b>: ไฟล์ราคาเป็นยอดซื้อทั้งเดือน แต่ยอดขายรายวันมีไม่ครบทุกวัน จึงคูณยอดซื้อด้วย (วันที่มียอดขาย ÷ วันในเดือน) · ${r.branches.filter(b => b.scaled).map(b => `${esc(b.branch)} ${b.sales_days}/${b.month_days} วัน`).join(' · ')}`);
  if (r.partial_month) lines.push('เดือนนี้ยังไม่สิ้นสุด ยอดซื้อและยอดขายยังไม่ครบเดือน ผลตรวจอาจคลาดเคลื่อน');
  lines.push(`ช่วงยอมรับ ±${_bmcNum(r.buffer_pct, 0)}% ใช้ชั่วคราวจนกว่าจะมีผลตรวจนับปลายงวดครบทุกสาขา${r.stock_adjusted_branches ? ` · ${r.stock_adjusted_branches} สาขามีผลตรวจนับครบ ใช้ยอดซื้อปรับด้วยสต็อกและเกณฑ์ผลต่างปกติ` : ''}`);
  if (r.skipped_branches.length) lines.push('ไม่นับ: ' + r.skipped_branches.map(x => esc((x.branch || '') + ' (' + x.why + ')')).join(' · '));
  if (r.skipped_items) lines.push(`ไม่แสดง ${r.skipped_items} รายการที่มีข้อมูลไม่ถึง 2 สาขา`);
  notes.innerHTML = `<ul style="margin:0;padding-left:20px;list-style:disc">${lines.map(l => `<li style="margin-top:2px">${l}</li>`).join('')}</ul>`;
  let rows = _bmcRcFilter === 'all' ? r.rows : r.rows.filter(x => x.status === _bmcRcFilter);
  if (window._bmcRcItem) {                                  // 2.6 · a task "ตรวจสูตร: …" opens this page on that one item
    const it = window._bmcRcItem;
    rows = r.rows.filter(x => x.code === it);
    notes.innerHTML += `<div style="color:var(--text-primary)"><b>แสดงเฉพาะ ${esc((rows[0] || {}).name || it)}</b> ${rows.length ? '' : '· รายการนี้ไม่อยู่ในผลตรวจของเดือนนี้ (มีข้อมูลซื้อไม่ถึง 2 สาขา)'} <a href="#" onclick="window._bmcRcItem=null;bmcRenderRcheck();return false" style="color:var(--accent)">แสดงทั้งหมด</a></div>`;
  }
  const total = rows.length;
  if (!_bmcRcAll) rows = rows.slice(0, 80);
  const num = v => v == null ? '<span style="color:var(--text-muted)">–</span>' : _bmcNum(v, v >= 100 ? 0 : v >= 10 ? 1 : 2);
  const ink = Object.fromEntries(_BMC_RC_ST.map(([k, , i]) => [k, i]));
  // 2026-10-19: one line per branch in the cell (full name · ซื้อ / ตามสูตร), not a "·"-joined row of 4-letter names
  const per = x => bmcPairs(x.branches.map(b => [esc(b.branch || ''), `${b.bought == null ? '–' : num(b.bought)} / ${b.recipe == null ? '–' : num(b.recipe)}${b.adjusted ? '*' : ''}`]));
  body.innerHTML = `<div class="wt-wrap" style="max-height:680px"><table class="wt"><thead><tr>
      <th>วัตถุดิบ</th><th>หน่วยฐาน</th><th class="wt-num">ซื้อจริง (ค่ากลาง)</th><th class="wt-num">ตามสูตร (ค่ากลาง)</th>
      <th class="wt-num">ส่วนต่าง</th><th>ผลตรวจ</th><th>รายสาขา ซื้อ / สูตร</th><th></th></tr></thead>
    <tbody>${rows.map(x => `<tr><td data-l="วัตถุดิบ">${esc(x.name)}<div class="wt-muted" style="font-size:13px">${esc(x.code)}</div></td>
      <td data-l="หน่วยฐาน" style="color:var(--text-secondary)">${esc(x.base_unit || '')}</td><td class="wt-num" data-l="ซื้อจริง (ค่ากลาง)" style="font-weight:600">${num(x.bought)}</td><td class="wt-num" data-l="ตามสูตร (ค่ากลาง)">${num(x.recipe)}</td>
      <td class="wt-num" data-l="ส่วนต่าง">${x.diff_pct == null ? '<span class="wt-muted">–</span>' : (x.diff_pct > 0 ? '+' : '') + _bmcNum(x.diff_pct, 1) + '%'}</td>
      <td data-l="ผลตรวจ">${_bmcChip(x.status_th, ink[x.status])}</td><td data-l="รายสาขา ซื้อ / สูตร" style="min-width:200px;color:var(--text-secondary)">${per(x)}</td>
      <td class="wt-act"><div style="display:flex;flex-direction:column;gap:6px"><button type="button" class="wt-btn" onclick="bmcRcOpenUnit('${esc(x.code)}')">หน่วยและการแปลง</button><button type="button" class="wt-btn" onclick="bmcRcOpenIng('${esc(x.code)}')">ทะเบียน</button></div></td></tr>`).join('')
      || '<tr><td colspan="8" class="wt-empty">ไม่มีรายการในกลุ่มนี้</td></tr>'}</tbody></table></div>
    ${total > 80 ? `<div class="mt-2 text-xs"><a href="#" onclick="_bmcRcAll=!_bmcRcAll;bmcRenderRcheck();return false" style="color:var(--accent)">${_bmcRcAll ? 'แสดงเฉพาะ 80 รายการแรก' : 'แสดงทั้งหมด ' + total + ' รายการ'}</a></div>` : ''}
    <div class="wt-note">เรียงตามมูลค่าส่วนต่างต่อยอดขาย ฿100,000 · เครื่องหมาย * = สาขานั้นใช้ยอดซื้อปรับด้วยสต็อกต้นงวดและปลายงวด</div>`;
}
// ── bulk delivery days from a file: template → preview (dry run) → save ──
let _bmcCalBulkFile = null;
function bmcCalTemplate() { bmcDownload(`${API}/api/bom-center/order-calendar/template.xlsx`, 'รอบส่งของสาขา.xlsx', 'bmcCalStatus'); }
function _bmcCalBulkClear() {
  _bmcCalBulkFile = null;
  const f = document.getElementById('bmcCalBulkFile'); if (f) f.value = '';
  const p = document.getElementById('bmcCalBulkPreview'); if (p) p.innerHTML = '';
}
async function bmcCalBulkPick(input) {
  const file = input && input.files && input.files[0];
  const box = document.getElementById('bmcCalBulkPreview');
  if (!file || !box) return;
  _bmcCalBulkFile = file;
  box.innerHTML = '<div class="text-xs" style="color:var(--text-muted)">กำลังตรวจไฟล์…</div>';
  try {
    const fd = new FormData(); fd.append('file', file, file.name);
    const d = await _bmcJson(`${API}/api/bom-center/order-calendar/import?dry_run=true`, { method: 'POST', body: fd });
    const cell = c => c.changed
      ? `<span style="background:var(--accent-soft);color:var(--text-primary);border-radius:4px;padding:1px 6px;font-weight:600">${esc(c.was_th)} → ${esc(c.now_th)}</span> <span class="text-[10.5px]" style="color:var(--text-muted)">ตัดรอบ ${esc(c.cutoff_th)}</span>`
      : `${esc(c.now_th)}`;
    const errs = d.errors.length
      ? `<div class="text-xs mb-2" style="color:var(--bad-ink)"><b>พบ ${d.errors.length} รายการที่ไม่ถูกต้อง</b> (แก้ไขไฟล์แล้วเลือกใหม่ จึงจะบันทึกได้)<ul class="list-disc pl-5">${
          d.errors.slice(0, 50).map(e => `<li>รายการที่ ${e.line} · ${esc(e.branch)} — ${esc(e.why)}</li>`).join('')}</ul></div>` : '';
    const rows = d.rows.filter(r => r.change);
    box.innerHTML = `${errs}<div class="text-xs mb-2" style="color:var(--text-secondary)">ไฟล์มี ${d.branches} สาขา · เปลี่ยน <b>${d.changed_branches}</b> สาขา (${d.changed_rounds} รอบ) · ที่เหลือเหมือนเดิม</div>
      ${rows.length ? `<div class="overflow-x-auto"><table class="text-xs w-full"><thead><tr class="text-left border-b" style="color:var(--text-muted)">
        <th class="pr-2">สาขา</th><th class="pr-2">รอบ 1 วันส่ง</th><th class="pr-2">รอบ 2 วันส่ง</th><th class="pr-2">แซลมอนสด วันส่ง</th></tr></thead>
        <tbody>${rows.map(r => `<tr class="border-b" style="border-color:var(--border-soft)"><td class="pr-2 py-1">${esc(r.branch)}</td>
          <td class="pr-2">${cell(r.r1)}</td><td class="pr-2">${cell(r.r2)}</td><td class="pr-2">${cell(r.rsalmon)}</td></tr>`).join('')}</tbody></table></div>` : ''}
      <div class="mt-3 flex items-center gap-2">
        <button onclick="bmcCalBulkSave()" ${(d.errors.length || !d.changed_branches) ? 'disabled' : ''} style="background:var(--accent);color:var(--on-accent);border:0;border-radius:var(--r-sm);padding:5px 14px;font-size:12.5px;cursor:pointer;${(d.errors.length || !d.changed_branches) ? 'opacity:.5;cursor:not-allowed' : ''}">บันทึก ${d.changed_branches} สาขา</button>
        <button onclick="_bmcCalBulkClear()" style="background:var(--bg-input);color:var(--text-primary);border:1px solid var(--border-control);border-radius:var(--r-sm);padding:5px 14px;font-size:12.5px;cursor:pointer">ยกเลิก</button></div>`;
  } catch (e) {
    box.innerHTML = `<div class="text-xs" style="color:var(--bad-ink)">ตรวจไฟล์ไม่สำเร็จ: ${esc(String(e.message || e).slice(0, 200))}</div>`;
  }
}
async function bmcCalBulkSave() {
  if (!_bmcCalBulkFile) return;
  try {
    const fd = new FormData(); fd.append('file', _bmcCalBulkFile, _bmcCalBulkFile.name);
    const d = await _bmcJson(`${API}/api/bom-center/order-calendar/import?dry_run=false`, { method: 'POST', body: fd });
    const msg = `✓ บันทึกรอบส่งแล้ว ${d.changed_branches} สาขา (${d.saved} รอบ)`;
    _bmcStatus('bmcCalStatus', msg, 'var(--good-ink)');
    _bmcStatus('bmcFcStatus', msg, 'var(--good-ink)');
    _bmcCalBulkClear();
    bmcLoadCalendar();
  } catch (e) {
    _bmcStatus('bmcCalStatus', 'ไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)');
  }
}
async function bmcSetRefBranch(bid, el) {
  // PR-X: 'benchmark' (the default: the median of the company branches) · 'none' · one branch
  const mode = el.value === 'none' ? 'none' : el.value === 'benchmark' || el.value === '' ? 'benchmark' : 'branch';
  const v = mode === 'branch' ? Number(el.value) : null;
  try {
    await _bmcJson(`${API}/api/bom-center/order-calendar/ref-branch`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ branch_id: bid, ref_branch_id: v, ref_mode: mode === 'none' ? 'none' : null }) });
    const row = (_bmcCal.rows || []).find(x => x.branch_id === bid);
    if (row) { row.ref_branch_id = v; row.ref_mode = mode; }
    const msg = `✓ บันทึกสาขาอ้างอิงแล้ว${mode === 'none' ? ' (ไม่ใช้สาขาอ้างอิง)' : mode === 'benchmark' ? ' (ค่ากลางสาขาของบริษัท)' : ''}`;
    _bmcStatus('bmcCalStatus', msg, 'var(--good-ink)');
    _bmcStatus('bmcFcStatus', msg, 'var(--good-ink)');
  } catch (e) {
    _bmcStatus('bmcCalStatus', 'บันทึกไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)');
    _bmcStatus('bmcFcStatus', 'บันทึกไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)');
    bmcLoadCalendar();
  }
}
// the read-only ตัดรอบ cell follows the weekday picked (the server derives the same: delivery − 2 days, a Sunday moves to Saturday)
function _bmcCutoffTh(wd) {
  const cw = (Number(wd) + 5) % 7;
  return `วัน${_BMC_WD[cw === 0 ? 6 : cw]} ${(_bmcCal && _bmcCal.cutoff_time) || '13:15'}`;
}
function bmcCalCutoff(el) {
  const c = document.getElementById(`bmcCut_${el.dataset.bid}_${el.dataset.rnd}`);
  if (c) c.textContent = _bmcCutoffTh(el.value);
}
async function bmcSaveCalendar() {
  // only the (branch, round) groups someone touched are sent — the others keep
  // whatever they have (a default stays a default, not "set by me")
  const rows = {}, dirty = {};
  document.querySelectorAll('#bmcFcCal [data-bid]').forEach(el => {
    const k = el.dataset.bid + '|' + el.dataset.rnd;
    rows[k] = rows[k] || { branch_id: Number(el.dataset.bid), round: el.dataset.rnd };
    rows[k][el.dataset.f] = el.value === '' ? null : Number(el.value);
    if (el.dataset.dirty === '1') dirty[k] = true;
  });
  const changed = Object.keys(rows).filter(k => dirty[k]).map(k => rows[k]);
  if (!changed.length) {
    _bmcStatus('bmcCalStatus', 'ไม่มีรายการที่แก้ไข', 'var(--text-muted)');
    _bmcStatus('bmcFcStatus', 'ไม่มีรายการที่แก้ไข', 'var(--text-muted)');
    return;
  }
  try {
    const r = await _bmcJson(`${API}/api/bom-center/order-calendar`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changed) });
    const msg = `✓ บันทึกรอบส่งแล้ว ${(r && r.rows) || changed.length} รายการ`;
    _bmcStatus('bmcCalStatus', msg, 'var(--good-ink)');
    _bmcStatus('bmcFcStatus', msg, 'var(--good-ink)');
    bmcLoadCalendar();
  } catch (e) {
    _bmcStatus('bmcCalStatus', 'บันทึกไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)');
    _bmcStatus('bmcFcStatus', 'บันทึกไม่สำเร็จ: ' + String(e.message || e), 'var(--bad-ink)');
  }
}
async function bmcLoadRuns() {
  try {
    const d = await _bmcJson(`${API}/api/bom-center/forecast/runs?limit=20`);
    document.getElementById('bmcFcRuns').innerHTML = `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">ยอดแนะนำที่บันทึกไว้</div>` +
      (d.rows.length ? `<div class="wt-wrap" style="max-height:360px"><table class="wt"><thead><tr><th>สาขา</th><th>รอบ</th><th>ตัดรอบ</th><th>ช่วงที่ใช้</th><th class="wt-num">รายการ</th><th class="wt-num">มูลค่า</th><th>ผู้บันทึก</th></tr></thead><tbody>
        ${d.rows.map(r => `<tr><td data-l="สาขา" style="font-weight:600">${esc(r.branch || '')}</td><td data-l="รอบ">${r.round === 'salmon' ? 'แซลมอนสด' : 'รอบ ' + esc(r.round)}</td>
          <td data-l="ตัดรอบ">${esc(fmtDay(r.order_day))}</td><td data-l="ช่วงที่ใช้">${esc(fmtDay(r.cover_from))}–${esc(fmtDay(r.cover_to))}</td>
          <td class="wt-num" data-l="รายการ">${r.lines}</td><td class="wt-num" data-l="มูลค่า">฿${_bmcNum(r.value, 0)}</td>
          <td class="wt-muted" data-l="ผู้บันทึก">${esc(whoTh(r.created_by))} ${esc(fmtAt(r.created_at))}</td></tr>`).join('')}</tbody></table></div>`
        : '<div class="wt-empty">ยังไม่มียอดแนะนำที่บันทึกไว้</div>');
  } catch (e) { /* list is a nicety */ }
}

// ── ใบสรุปตัดเบิก (ปิดงวดเดือน → ใบสรุปตัดเบิก): Accounting downloads it and ticks "posted" ─────
let _bmcIssPeriods = null;    // /issue-summary/periods → { branches: [{branch_id, name, periods: […]}] }
let _bmcIssSum = null;        // the summary on screen
let _bmcIssRecon = null;      // issue-reports/reconcile of the same period (system stock vs the count) — PR-J4
// the server's write rule (auth.py): cost.analysis or bom.center. The class is not `.bc-write`: loadBomCenter hides that
// one unless the user holds bom.center, and Accounting does not.
function _bmcIssCanPost() { return hasModule('cost.analysis') || hasModule('bom.center'); }
function _bmcIssSel() {
  const b = document.getElementById('bmcIssBranch'), p = document.getElementById('bmcIssPeriod');
  return { branch_id: b && b.value ? Number(b.value) : null, end_count_id: p && p.value ? Number(p.value) : null };
}
async function bmcLoadIssue() {
  document.querySelectorAll('#tab-bomcenter .bmc-iss-write').forEach(el => { el.style.display = _bmcIssCanPost() ? '' : 'none'; });
  try {
    _bmcIssPeriods = await _bmcJson(`${API}/api/bom-center/issue-summary/periods`);
  } catch (e) {
    _bmcStatus('bmcIssStatus', 'โหลดรายการงวดไม่สำเร็จ: ' + String(e.message || e).slice(0, 200), 'var(--bad-ink)');
    return;
  }
  const bs = (_bmcIssPeriods.branches || []).filter(b => b.periods.length);
  const sel = document.getElementById('bmcIssBranch');
  if (!sel) return;                          // a pinned login has no picker (and never reaches this centre page)
  if (!bs.length) {
    sel.innerHTML = '';
    document.getElementById('bmcIssPeriod').innerHTML = '';
    document.getElementById('bmcIssChips').innerHTML = '';
    document.getElementById('bmcIssTable').innerHTML = '<div class="text-xs" style="color:var(--text-muted)">ยังไม่มีสาขาที่ตรวจนับต้นงวดและปลายงวดครบ จึงยังไม่มีใบสรุปตัดเบิก</div>';
    _bmcStatus('bmcIssStatus', '');
    return;
  }
  const want = supplyCtx().branch_id;
  sel.innerHTML = bs.map(b => `<option value="${b.branch_id}">${esc(b.name)}</option>`).join('');
  sel.value = String(bs.some(b => b.branch_id === want) ? want : bs[0].branch_id);
  bmcIssBranchChanged();
}
function _bmcIssPeriodOf(endId) {
  const b = ((_bmcIssPeriods && _bmcIssPeriods.branches) || []).find(x => x.branch_id === _bmcIssSel().branch_id);
  return ((b && b.periods) || []).find(p => p.end_count_id === endId) || null;
}
function _bmcIssFillPeriods() {
  const bid = _bmcIssSel().branch_id;
  const b = ((_bmcIssPeriods && _bmcIssPeriods.branches) || []).find(x => x.branch_id === bid);
  const ps = b ? b.periods : [];                       // newest first
  const tag = p => p.accounting_posted_at ? ' · บันทึกบัญชีแล้ว' : (p.receipts_confirmed ? ' · ยืนยันรับสินค้าครบแล้ว' : ' · ยังไม่ยืนยันรับสินค้าครบ');
  // "กันยายน 2026 · 1/9/2026 – 30/9/2026 · บันทึกบัญชีแล้ว"
  document.getElementById('bmcIssPeriod').innerHTML = ps.map(p =>
    `<option value="${p.end_count_id}">${esc(fmtMonthTh(p.month))} · ${esc(fmtDay(p.begin_date))} – ${esc(fmtDay(p.end_date))}${tag(p)}</option>`).join('');
  return ps;
}
let _bmcIssFallback = false;
function bmcIssBranchChanged() {
  const bid = _bmcIssSel().branch_id;
  if (bid != null) supplyCtxSet({ branch_id: bid });
  const ps = _bmcIssFillPeriods();
  // the period of the month in the control (the previous month until somebody chooses): the one waiting for Accounting, else the newest of it;
  // a month the branch has no period in falls back to the one waiting for Accounting, else the newest
  const m = _bmcMonth(), inM = ps.filter(p => p.month === m), waiting = p => p.receipts_confirmed && !p.accounting_posted_at;
  const pick = inM.find(waiting) || inM[0] || ps.find(waiting) || ps[0];
  _bmcIssFallback = !!(pick && !inM.length && ps.length && supplyCtx().month);
  if (pick) document.getElementById('bmcIssPeriod').value = String(pick.end_count_id);
  bmcIssLoadSummary();
}
// the person chose a period: its month is the month of the whole tab
function bmcIssPeriodChanged() {
  const p = _bmcIssPeriodOf(_bmcIssSel().end_count_id);
  if (p) { supplyCtxSet({ month: p.month }); const el = document.getElementById('bmcMonth'); if (el && el.value !== p.month) { supplyMonthPut(el, p.month); _bmcIng = []; _bmcPrices = []; } }
  _bmcIssFallback = false;
  bmcIssLoadSummary();
}
// the posting: a primary button until it is done, then who did it and a secondary link to take it back; the reason it is off is written under it
function bmcIssRenderPost(st) {
  const btn = document.getElementById('bmcIssPostBtn'), un = document.getElementById('bmcIssUnpost'), note = document.getElementById('bmcIssPostNote');
  if (!btn) return;
  const posted = !!st.accounting_posted_at;
  btn.style.display = posted ? 'none' : '';
  un.style.display = posted ? '' : 'none';
  btn.disabled = !posted && !st.receipts_confirmed;
  btn.style.opacity = btn.disabled ? .5 : 1; btn.style.cursor = btn.disabled ? 'not-allowed' : 'pointer';
  note.textContent = posted ? `บันทึกเข้าระบบบัญชีแล้ว เมื่อ ${fmtDayAt(st.accounting_posted_at)} โดย ${whoTh(st.accounting_posted_by)} · งวดนี้ล็อกอยู่`
    : st.receipts_confirmed ? 'กดเมื่อคีย์ใบสรุปเข้าระบบบัญชีแล้ว · เมื่อกดแล้วงวดนี้ล็อก'
    : 'ต้องยืนยันรับสินค้าครบของงวดนี้ก่อน จึงบันทึกเข้าระบบบัญชีได้';
}
async function bmcIssLoadSummary() {
  const s = _bmcIssSel();
  if (s.branch_id == null || s.end_count_id == null) return;
  const box = document.getElementById('bmcIssTable');
  box.innerHTML = '<div class="text-xs" style="color:var(--text-muted)">กำลังคำนวณ…</div>';
  const rbox = document.getElementById('bmcIssRecon');
  rbox.innerHTML = '<div class="text-xs" style="color:var(--text-muted)">กำลังคำนวณ…</div>';
  let d, rc = null;
  try {
    const q = `end_count_id=${s.end_count_id}&branch_id=${s.branch_id}`;
    [d, rc] = await Promise.all([_bmcJson(`${API}/api/bom-center/issue-summary?${q}`),
                                 _bmcJson(`${API}/api/bom-center/issue-reports/reconcile?${q}`).catch(() => null)]);
    _bmcIssSum = d;
    _bmcIssRecon = rc;
  } catch (e) {
    _bmcIssSum = null;
    _bmcIssRecon = null;
    rbox.innerHTML = '';
    box.innerHTML = `<div class="text-xs" style="color:var(--bad-ink)">โหลดใบสรุปตัดเบิกไม่สำเร็จ: ${esc(String(e.message || e).slice(0, 200))}</div>`;
    return;
  }
  const st = d.status, t = d.totals;
  const allBtn = document.getElementById('bmcIssAllBtn'); if (allBtn) allBtn.title = 'ใบสรุปของทุกสาขา เดือน' + fmtMonthTh(d.period.month);
  const mel = document.getElementById('bmcMonth');                       // the control shows the month of the period on screen
  if (mel && mel.value !== d.period.month) { supplyMonthPut(mel, d.period.month); _bmcIng = []; _bmcPrices = []; }
  const chips = [st.receipts_confirmed ? _bmcChip('ยืนยันรับสินค้าครบแล้ว', 'var(--good-ink)') : _bmcChip('ยังไม่ยืนยันรับสินค้าครบ', 'var(--amber-ink)')];
  const sl = d.slips;                                  // the slip coverage of the period: a warning, never a lock
  if (sl) {
    chips.push(_bmcChip(`ใบตัดเบิกยืนยันแล้ว ${sl.confirmed_days}/${sl.trading_days} วัน`, sl.trading_days && sl.confirmed_days >= sl.trading_days ? 'var(--good-ink)' : 'var(--amber-ink)'));
    if (sl.missing_days.length) chips.push(_bmcChip(`ขาดใบตัดเบิก ${sl.missing_days.length} วัน`, 'var(--bad-ink)'));
    if (sl.draft_days.length) chips.push(_bmcChip(`ยังไม่ยืนยัน ${sl.draft_days.length} วัน`, 'var(--amber-ink)'));
  }
  if (rc) chips.push(rc.totals.flagged ? _bmcChip(`ผลต่างเกินเกณฑ์ ${rc.totals.flagged} รายการ`, 'var(--bad-ink)') : _bmcChip('ผลต่างอยู่ในเกณฑ์ทุกรายการ', 'var(--good-ink)'));
  if (st.accounting_posted_at) chips.push(_bmcChip(`บัญชีบันทึกแล้ว เมื่อ ${fmtDayAt(st.accounting_posted_at)} โดย ${whoTh(st.accounting_posted_by)}`, 'var(--navy-ink)'));
  if (t.unpriced) chips.push(_bmcChip(`ไม่มีราคา ${t.unpriced} รายการ`, 'var(--amber-ink)'));
  if (d.period.partial) chips.push(_bmcChip(`ปิดงวดด้วยการตรวจนับหมุนเวียน (รายการเดิม) · คงเหลือตามระบบ (ไม่ได้นับ) ${t.system_basis} รายการ`, 'var(--navy-ink)'));
  document.getElementById('bmcIssChips').innerHTML = chips.join('');
  bmcIssRenderRecon();
  bmcIssRenderPost(st);
  if (_bmcIssFallback) bmcStatus('bmcIssStatus', 'warn', 'สาขานี้ไม่มีงวดของเดือนที่เลือก · แสดงงวดล่าสุดแทน');
  else _bmcStatus('bmcIssStatus', '');
  const heads = ['รหัส', 'วัตถุดิบ', 'หน่วย', 'ยกมา (ต้นงวด)', 'รับเข้า', 'เคลม/ปรับ', 'นับจริง (ปลายงวด)', 'ตัดเบิก (ใช้จริง)', 'ราคาต่อหน่วย', 'มูลค่าตัดเบิก', 'ที่มาราคา'].concat(d.period.partial ? ['ที่มาของคงเหลือปลายงวด'] : []);
  const num = (v, dp) => (v === null || v === undefined) ? '—' : _bmcNum(v, dp);
  const rows = d.lines.map(l => {
    const bad = l.status === 'negative';
    const ink = bad ? 'color:var(--bad-ink)' : '';
    const used = l.used === null || l.used === undefined
      ? `<span title="${esc(l.note || '')}" style="color:var(--amber-ink)">—</span>` : `<b style="${ink}">${num(l.used, 3)}</b>`;
    return `<tr class="border-b" style="border-color:var(--border-soft)"><td class="pr-2 whitespace-nowrap">${esc(l.code)}</td><td class="pr-2">${esc(l.name || '')}</td>
      <td class="pr-2">${esc(l.base_unit || '')}</td><td class="pr-2 text-right">${num(l.begin, 3)}</td><td class="pr-2 text-right">${num(l.received, 3)}</td>
      <td class="pr-2 text-right">${num(l.claims, 3)}</td><td class="pr-2 text-right">${num(l.end, 3)}</td><td class="pr-2 text-right">${used}</td>
      <td class="pr-2 text-right">${l.unit_price === null ? '—' : _bmcPrice(l.unit_price)}</td>
      <td class="pr-2 text-right" style="${ink}">${num(l.value, 2)}</td><td class="pr-2 text-[10.5px]" style="color:var(--text-muted)">${esc(l.price_source || 'ไม่มีราคา')}</td>${d.period.partial ? `<td class="pr-2 text-[10.5px]" style="color:${l.end_basis === 'system' ? 'var(--navy-ink)' : 'var(--text-muted)'}">${esc(l.basis_th || '')}</td>` : ''}</tr>`;
  }).join('');
  box.innerHTML = `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">ใบสรุปตัดเบิก สาขา${esc(d.branch.name || '')} · ${esc(fmtDay(d.period.begin_date))} ถึง ${esc(fmtDay(d.period.end_date))} (${d.period.days} วัน · เดือน${fmtMonthTh(d.period.month)})</div>
    ${d.period.partial_th ? `<div class="text-xs mb-2" style="color:var(--text-secondary)">${esc(d.period.partial_th)}</div>` : ''}
    ${d.lines.length ? `<table class="text-xs w-full" style="font-variant-numeric:tabular-nums">
      <thead><tr class="text-left border-b" style="color:var(--text-muted)">${heads.map((h, i) => `<th class="pr-2 ${i >= 3 && i <= 9 ? 'text-right' : ''}">${esc(h)}</th>`).join('')}</tr></thead>
      <tbody>${rows}</tbody></table>` : '<div class="text-xs" style="color:var(--text-muted)">ไม่มีรายการเคลื่อนไหวในงวดนี้</div>'}
    <div class="mt-2 text-sm" style="color:var(--text-primary)">รวมมูลค่าตัดเบิก <b>฿${_bmcNum(t.used_value, 2)}</b>
      <span class="text-[11px]" style="color:var(--text-muted)"> · มูลค่ารับเข้า ฿${_bmcNum(t.received_value, 2)} · เคลม/ปรับ ฿${_bmcNum(t.claims_value, 2)}</span></div>
    ${d.excluded.length ? `<div class="text-[11px] mt-1" style="color:var(--text-muted)">ไม่มีการเคลื่อนไหว ${d.excluded.length} รายการ (ไม่แสดงในใบสรุป)</div>` : ''}`;
}
// the reconciliation (system stock vs the count) — the same renderers as the branch page and the centre
function bmcIssRenderRecon() {
  const rbox = document.getElementById('bmcIssRecon'), rc = _bmcIssRecon;
  if (!rbox) return;
  if (!rc) { rbox.innerHTML = '<div class="text-xs" style="color:var(--amber-ink)">ยังไม่มีรายงานกระทบยอดของงวดนี้</div>'; return; }
  const all = !!(document.getElementById('bmcIssAll') && document.getElementById('bmcIssAll').checked);
  rbox.innerHTML = `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">กระทบยอด สาขา${esc(rc.branch.name || '')} · คงเหลือตามระบบเทียบนับจริง</div>${isrReconHead(rc)}
    <label class="text-xs flex items-center gap-1 mt-2" style="color:var(--text-secondary)"><input type="checkbox" id="bmcIssAll" onchange="bmcIssRenderRecon()"${all ? ' checked' : ''}> แสดงทุกรายการ (${rc.rows.length})</label>
    <div class="mt-2">${isrReconTable(rc, { all, pfx: 'bmcIssRc', qty: brnQtyUnit, brief: true })}</div>`;
}
function bmcIssSlipsXlsx() {         // every confirmed daily slip of the period's month, all branches
  const d = _bmcIssSum;
  if (!d) return;
  _ohbIsDownload(`${API}/api/bom-center/issues/export.xlsx?month=${encodeURIComponent(d.period.month)}`,
    `bom_center_issue_slips_${d.period.month}.xlsx`, 'bmcIssStatus');
}
function bmcIssXlsx() {
  const d = _bmcIssSum;
  if (!d) return;
  if (!d.status.receipts_confirmed) { _bmcStatus('bmcIssStatus', 'งวดนี้ยังไม่ยืนยันรับสินค้าครบ จึงยังดาวน์โหลดไม่ได้', 'var(--amber-ink)'); return; }
  bmcDownload(`${API}/api/bom-center/issue-summary.xlsx?end_count_id=${d.period.end_count_id}&branch_id=${d.branch.branch_id}`,
    `bom_center_issue_summary_${d.period.end_count_id}.xlsx`, 'bmcIssStatus');
}
function bmcIssXlsxAll() {
  const d = _bmcIssSum;
  if (!d) return;
  bmcDownload(`${API}/api/bom-center/issue-summary-all.xlsx?month=${encodeURIComponent(d.period.month)}`,
    `bom_center_issue_summary_${d.period.month}.xlsx`, 'bmcIssStatus');
}
function bmcDownloadPriceTemplate(statusId) { bmcDownload(`${API}/api/bom-center/prices/template.xlsx`, 'แม่แบบยอดซื้อรายบรรทัด.xlsx', statusId || 'bmcPriceStatus'); }
function bmcDownloadIngTemplate() { bmcDownload(`${API}/api/bom-center/ingredients/template.xlsx`, 'ทะเบียนวัตถุดิบ.xlsx', 'bmcIngStatus'); }
async function bmcIssPost(want) {
  const d = _bmcIssSum;
  if (!d) return;
  // the question says what is posted (branch, period, baht, lines) and what the lock does; the lock is lifted by the same box
  const what = `สาขา${d.branch.name || ''} · ${fmtDay(d.period.begin_date)} ถึง ${fmtDay(d.period.end_date)} · ${d.lines.length} รายการ · มูลค่าตัดเบิก ฿${_bmcNum(d.totals.used_value, 2)}`;
  // QA L4h: a period posted with confirmed slips missing says so in the question ("ขาดใบตัดเบิก N วัน") - a warning, never a block
  const sl = d.slips || {}, nMiss = (sl.missing_days || []).length + (sl.draft_days || []).length;
  const warn = want && nMiss ? `\nขาดใบตัดเบิก ${nMiss} วัน (${[...(sl.missing_days || []), ...(sl.draft_days || [])].sort().slice(0, 3).map(fmtDay).join(', ')}${nMiss > 3 ? ' …' : ''}) ผลต่างของวันเหล่านี้จึงยังไม่ครบ` : '';
  const ok = want
    ? await uiConfirm(`บันทึกใบสรุปตัดเบิกเข้าระบบบัญชีแล้ว?\n${what}${warn}\nเมื่อบันทึกแล้วงวดนี้ล็อก: เพิ่มใบรับสินค้า แก้ใบตัดเบิก และยกเลิกการยืนยันรับสินค้าครบไม่ได้`,
      { title: 'ยืนยันการบันทึกเข้าระบบบัญชี', ok: 'บันทึกเข้าระบบบัญชีแล้ว', cancel: 'ยังไม่บันทึก' })
    : await uiConfirm(`ยกเลิกการบันทึกบัญชีของงวดนี้?\n${what}\nงวดนี้จะปลดล็อก และแก้ไขใบรับสินค้าและใบตัดเบิกได้อีก`,
      { title: 'ยืนยันการยกเลิกการบันทึกบัญชี', ok: 'ยกเลิกการบันทึกบัญชี', cancel: 'ไม่ยกเลิก' });
  if (!ok) { bmcStatus('bmcIssStatus', null, 'ยังไม่เปลี่ยนสถานะ'); return; }
  try {
    await _bmcJson(`${API}/api/bom-center/issue-summary/posted`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ end_count_id: d.period.end_count_id, posted: want }) });
    const b = ((_bmcIssPeriods && _bmcIssPeriods.branches) || []).find(x => x.branch_id === d.branch.branch_id);
    const p = b && b.periods.find(x => x.end_count_id === d.period.end_count_id);
    if (p) { p.accounting_posted_at = want ? new Date().toISOString() : null; }
    _bmcIssFillPeriods();
    document.getElementById('bmcIssPeriod').value = String(d.period.end_count_id);
    await bmcIssLoadSummary();
    bmcStatus('bmcIssStatus', true, want ? 'บันทึกเข้าระบบบัญชีแล้ว · งวดนี้ล็อก' : 'ยกเลิกการบันทึกบัญชีแล้ว · งวดนี้ปลดล็อก');
  } catch (e) {
    bmcStatus('bmcIssStatus', false, String(e.message || e).slice(0, 200));          // the server did not take it: the button stays as it was
  }
}
// ── PR-Y · รหัสใหม่ของวัตถุดิบเดิม ──────────────────────────────────────────────
// The code the recipes use never changes: a code from another system (accounting, warehouse, supplier) is an alias of the register
// item. Sales Support reports it, the analyst decides; an import lists the codes it does not know with the register items they could
// belong to, and the person chooses per code. Nothing in the register changes without the analyst.
const _BMC_SYS = [['acc', 'ระบบบัญชี'], ['warehouse', 'ระบบคลัง'], ['supplier', 'ผู้จำหน่าย'], ['other', 'ระบบอื่น']];
const _BMC_SYS_TH = Object.fromEntries(_BMC_SYS);
const _bmcSysSel = (id, cur, extra) => `<select id="${id}" ${extra || ''} style="border:1px solid var(--border-control);border-radius:var(--r-sm);padding:4px 8px;font-size:12px;background:var(--bg-input);color:var(--text-primary)">${
  _BMC_SYS.map(([k, t]) => `<option value="${k}"${cur === k ? ' selected' : ''}>${t}</option>`).join('')}</select>`;
const _BMC_FIELD = 'border:1px solid var(--border-control);border-radius:var(--r-sm);padding:5px 8px;font-size:12px;background:var(--bg-input);color:var(--text-primary)';

// the register items for the pickers (the list the register page already holds, else read it once)
async function _bmcUcRegister() {
  if (!_bmcIng.length) {
    try { _bmcIng = (await _bmcJson(`${API}/api/bom-center/ingredients`)).rows || []; } catch (e) { /* the pickers still work from the suggestions */ }
  }
  let dl = document.getElementById('bmcUcList');
  if (!dl) { dl = document.createElement('datalist'); dl.id = 'bmcUcList'; document.body.appendChild(dl); }
  if (_bmcIng.length && dl.children.length !== _bmcIng.length) dl.innerHTML = _bmcIng.map(r => `<option value="${esc(r.code)}">${esc(r.name_th || '')}</option>`).join('');
}
function _bmcItemOf(text) {                                   // a typed code or exact name -> the register code, else ''
  const t = String(text || '').trim();
  if (!t) return '';
  const hit = _bmcIng.find(x => x.code === t) || _bmcIng.find(x => (x.name_th || '') === t);
  return hit ? hit.code : '';
}
function _bmcItemName(code) { const h = _bmcIng.find(x => x.code === code); return h ? (h.name_th || '') : ''; }

// ── the unknown codes of an import: one answer per code ─────────────────────────────────────────
// _bmcUc[box] = {items: [{code, name, unit, lines, value, suggestions}], choice: {code: {as: 'alias'|'new', item, other}}, opts}
const _bmcUc = {};
function bmcUcReset(boxId) { delete _bmcUc[boxId]; const b = document.getElementById(boxId); if (b) { b.innerHTML = ''; b.style.display = 'none'; } }
async function bmcUcRender(boxId, items, opts) {
  const box = document.getElementById(boxId);
  if (!box) return;
  const st = _bmcUc[boxId] = Object.assign(_bmcUc[boxId] || { choice: {} }, { items: items || [], opts: opts || {} });
  const o = st.opts;
  if (!st.items.length) {                         // everything answered (a recalculation resolved the codes): keep the answers in view
    const got = Object.entries(bmcUcChoices(boxId));
    box.style.display = got.length ? '' : 'none';
    box.innerHTML = got.length ? `<div class="p-2" style="background:var(--bg-subtle);border-radius:var(--r-sm);color:var(--text-secondary)"><b>รหัสที่เลือกไว้</b>${
      bmcLines(got.map(([c, a]) => a.as === 'new' ? `<span class="wt-code">${esc(c)}</span> → วัตถุดิบใหม่` : `<span class="wt-code">${esc(c)}</span> → รหัสใหม่ของ ${esc(a.item)} ${esc(_bmcItemName(a.item))}`))}</div>` : '';
    return;
  }
  await _bmcUcRegister();
  if (o.prefillNew) st.items.forEach(u => { if (!st.choice[u.code] && !(u.suggestions || []).length) st.choice[u.code] = { as: 'new' }; });
  const decide = o.mode !== 'request' && _bmcCanPrices();
  const ans = u => st.choice[u.code];
  const row = u => {
    const c = ans(u), sg = u.suggestions || [];
    const cur = !c ? '' : c.as === 'new' ? 'new' : c.other ? 'alias:*' : 'alias:' + c.item;
    const opt = (v, t) => `<option value="${esc(v)}"${cur === v ? ' selected' : ''}>${esc(t)}</option>`;
    return `<tr>
      <td class="wt-code" data-l="รหัส">${esc(u.code)}</td>
      <td data-l="ชื่อ">${esc(u.name || '—')}<div class="wt-muted" style="font-size:13px">${[u.unit ? esc(u.unit) : '', u.lines ? u.lines + ' บรรทัด' : '', u.value ? '฿' + _bmcNum(u.value, 0) : ''].filter(Boolean).join(' · ')}</div></td>
      <td data-l="เลือก">
        <select aria-label="เลือกว่ารหัส ${esc(u.code)} เป็นอะไร" onchange="bmcUcPick(${_bmcQ(boxId)},${_bmcQ(u.code)},this.value)" style="${_BMC_FIELD};max-width:360px">
          <option value="">เลือก…</option>
          ${sg.map(s => opt('alias:' + s.code, `เป็นรหัสใหม่ของวัตถุดิบเดิม: ${s.code} ${s.name || ''}${s.same_unit === false ? ' (หน่วยต่างกัน)' : ''}`)).join('')}
          ${opt('alias:*', 'เป็นรหัสใหม่ของวัตถุดิบเดิม: เลือกรายการอื่น…')}
          ${o.mode === 'request' ? '' : opt('new', 'เป็นวัตถุดิบใหม่')}
        </select>
        ${c && c.as === 'alias' && c.other ? `<div class="mt-1"><input list="bmcUcList" placeholder="รหัสหรือชื่อวัตถุดิบเดิม" value="${esc(c.item || '')}" onchange="bmcUcOther(${_bmcQ(boxId)},${_bmcQ(u.code)},this.value)" style="${_BMC_FIELD};width:260px">
          ${c.item ? `<span style="color:var(--good-ink)"> ${esc(_bmcItemName(c.item))}</span>` : ''}${c.bad ? '<span style="color:var(--bad-ink)"> ไม่พบรหัสหรือชื่อนี้ในทะเบียน</span>' : ''}</div>` : ''}
      </td></tr>`;
  };
  const left = st.items.filter(u => { const c = ans(u); return !c || (c.as === 'alias' && !c.item); }).length;
  const hint = o.mode === 'request'
    ? 'เลือกวัตถุดิบเดิมของแต่ละรหัสที่เป็นรหัสใหม่ แล้วส่งคำขอให้ฝ่ายวิเคราะห์ · ทะเบียนเปลี่ยนเมื่อฝ่ายวิเคราะห์อนุมัติเท่านั้น และสูตรไม่ต้องแก้'
    : (o.needAll ? 'เลือกให้ครบทุกรหัสก่อนบันทึก · ' : 'รหัสที่ยังไม่เลือกจะไม่ถูกนำเข้า · ')
      + (decide ? '“เป็นรหัสใหม่ของวัตถุดิบเดิม” บันทึกรหัสนี้เป็นรหัสในระบบอื่นของวัตถุดิบเดิมทันที สูตรไม่ต้องแก้' : '“เป็นรหัสใหม่ของวัตถุดิบเดิม” ส่งเป็นคำขอให้ฝ่ายวิเคราะห์ตรวจ และรหัสนั้นยังไม่ถูกนำเข้าในครั้งนี้');
  box.style.display = '';
  box.innerHTML = `<div class="p-3" style="background:var(--bg-subtle);border-radius:var(--r-sm)">
    <div class="text-xs font-semibold" style="color:var(--amber-ink)">รหัสที่ระบบไม่รู้จัก (${st.items.length} รหัส)${left ? ` · ยังไม่ได้เลือก ${left}` : ' · เลือกครบแล้ว'}</div>
    <div class="text-[11px] mb-2" style="color:var(--text-muted)">${hint}</div>
    <div class="flex flex-wrap items-center gap-2 mb-2">
      ${o.mode === 'request' ? '' : `<label class="text-[11px]" style="color:var(--text-muted)">ระบบของรหัสใหม่ ${_bmcSysSel(boxId + 'Sys', st.system || 'acc', `onchange="_bmcUc[${_bmcQ(boxId)}].system=this.value"`)}</label>`}
      ${o.mode === 'request' ? '' : _bmcBtn('เป็นวัตถุดิบใหม่ทั้งหมดที่ไม่มีรายการใกล้เคียง', `bmcUcAllNew(${_bmcQ(boxId)})`)}
      ${o.onRecalc ? _bmcBtn('คำนวณใหม่ด้วยรหัสที่เลือก', o.onRecalc) : ''}
    </div>
    <div class="wt-wrap" style="max-height:480px"><table class="wt"><thead><tr><th>รหัส</th><th>ชื่อในไฟล์</th><th>รหัสนี้คือ</th></tr></thead><tbody>${st.items.map(row).join('')}</tbody></table></div>
    ${o.mode === 'request' ? `<div class="flex flex-wrap items-center gap-2 mt-2">${_bmcBtn('ส่งคำขอให้ฝ่ายวิเคราะห์', `bmcUcSendRequests(${_bmcQ(boxId)})`, true)}<span id="${boxId}Status" class="text-[11px]"></span></div>` : ''}
    ${o.footer ? `<div class="flex flex-wrap items-center gap-2 mt-2">${o.footer}</div>` : ''}
  </div>`;
}
function bmcUcPick(boxId, code, v) {
  const st = _bmcUc[boxId];
  if (!st) return;
  if (!v) delete st.choice[code];
  else if (v === 'new') st.choice[code] = { as: 'new' };
  else if (v === 'alias:*') st.choice[code] = { as: 'alias', item: '', other: true };
  else st.choice[code] = { as: 'alias', item: v.slice(6) };
  bmcUcRender(boxId, st.items, st.opts);
}
function bmcUcOther(boxId, code, text) {
  const st = _bmcUc[boxId];
  if (!st) return;
  const item = _bmcItemOf(text);
  st.choice[code] = { as: 'alias', item, other: true, bad: !item && !!String(text || '').trim() };
  bmcUcRender(boxId, st.items, st.opts);
}
function bmcUcAllNew(boxId) {
  const st = _bmcUc[boxId];
  if (!st) return;
  st.items.forEach(u => { if (!st.choice[u.code] && !(u.suggestions || []).length) st.choice[u.code] = { as: 'new' }; });
  bmcUcRender(boxId, st.items, st.opts);
}
function bmcUcChoices(boxId) {                    // the complete answers, as the server takes them ({} = none yet)
  const st = _bmcUc[boxId], out = {};
  if (!st) return out;
  Object.entries(st.choice).forEach(([c, a]) => { if (a.as === 'new') out[c] = { as: 'new' }; else if (a.item) out[c] = { as: 'alias', item: a.item }; });
  return out;
}
function bmcUcOpen(boxId) {                        // the unknown codes with no complete answer
  const st = _bmcUc[boxId];
  if (!st) return [];
  const got = bmcUcChoices(boxId);
  return st.items.filter(u => !got[u.code]).map(u => u.code);
}
function bmcUcSystem(boxId) { const el = document.getElementById(boxId + 'Sys'); return (el && el.value) || 'acc'; }
function bmcUcSummary(boxId) {
  const got = Object.values(bmcUcChoices(boxId));
  return { alias: got.filter(a => a.as === 'alias').length, fresh: got.filter(a => a.as === 'new').length };
}
// Sales Support: the answers become requests for the analyst (nothing is added or changed)
async function bmcUcSendRequests(boxId) {
  const st = _bmcUc[boxId], got = bmcUcChoices(boxId);
  const mine = (st ? st.items : []).filter(u => got[u.code] && got[u.code].as === 'alias');
  if (!mine.length) { bmcStatus(boxId + 'Status', 'warn', 'เลือกวัตถุดิบเดิมของรหัสที่เป็นรหัสใหม่อย่างน้อย 1 รหัสก่อน'); return; }
  if (!await uiConfirm(`ส่งคำขอให้ฝ่ายวิเคราะห์ ${mine.length} รหัส\n${mine.slice(0, 6).map(u => `${u.code} เป็นรหัสใหม่ของ ${got[u.code].item} ${_bmcItemName(got[u.code].item)}`).join('\n')}`,
      { title: 'ยืนยันการแจ้งเปลี่ยนรหัสสินค้า', ok: 'ส่งคำขอ', cancel: 'ยกเลิก' })) return;
  let ok = 0, fail = [];
  for (const u of mine) {
    try {
      await _bmcJson(`${API}/api/bom-center/codes/request`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_code: u.code, ingredient_code: got[u.code].item, system: st.opts.system || 'acc', name: u.name || null, note: 'จากไฟล์ราคาที่ส่ง' }) });
      ok++;
    } catch (e) { fail.push(`${u.code}: ${String(e.message || e).slice(0, 120)}`); }
  }
  if (ok) mine.forEach(u => { delete st.choice[u.code]; });
  st.items = st.items.filter(u => !(got[u.code] && got[u.code].as === 'alias' && !fail.some(f => f.startsWith(u.code + ':'))));
  await bmcUcRender(boxId, st.items, st.opts);
  bmcStatus(boxId + 'Status', fail.length ? 'warn' : true, `ส่งคำขอแล้ว ${ok} รหัส${fail.length ? ' · ไม่สำเร็จ: ' + fail.join(' · ') : ''}`);
  if (typeof bmcLoadCodeReq === 'function') bmcLoadCodeReq();
}

// ── the register row: รหัสในระบบอื่น ────────────────────────────────────────────────────────────
let _bmcAlOpen = '';                                // the register code whose detail is open
let _bmcAlState = null;                             // its last answer (the table redraws from it without asking again)
function bmcAlToggle(code) { _bmcAlOpen = _bmcAlOpen === code ? '' : code; _bmcAlState = null; bmcRenderIng(); }
async function bmcAlLoad(code) {
  const cell = document.getElementById('bmcAlCell');
  if (!cell) return;
  try { _bmcAlState = await _bmcJson(`${API}/api/bom-center/ingredients/${encodeURIComponent(code)}/aliases`); bmcAlRender(_bmcAlState); }
  catch (e) { cell.innerHTML = `<span style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function bmcAlRender(d) {
  const cell = document.getElementById('bmcAlCell');
  if (!cell || d.code !== _bmcAlOpen) return;
  const w = _bmcCanWrite();
  const chip = a => !a.active ? _bmcChip('เลิกใช้แล้ว', 'var(--amber-ink)') : a.in_use ? _bmcChip('ใช้อยู่', 'var(--good-ink)') : _bmcChip('รหัสสำรอง', 'var(--text-muted)');
  const act = a => !w ? '' : !a.active
    ? _bmcBtn('ใช้อีกครั้ง', `bmcAlAct(${_bmcQ(d.code)},{action:'add',alias:${_bmcQ(a.alias)},system:${_bmcQ(a.system)},valid_from:${_bmcQ(a.valid_from)},make_current:false},'ใช้รหัสนี้อีกครั้งแล้ว')`)
    : `<span class="flex flex-wrap gap-1">${a.in_use ? '' : _bmcBtn('ตั้งเป็นรหัสปัจจุบัน', `bmcAlAct(${_bmcQ(d.code)},{action:'current',alias:${_bmcQ(a.alias)}},'ตั้งเป็นรหัสปัจจุบันแล้ว')`)}${_bmcBtn('เลิกใช้', `bmcAlRetire(${_bmcQ(d.code)},${_bmcQ(a.alias)})`)}</span>`;
  const sysLine = d.current.map(c => `<span class="mr-3">${esc(c.system_th)}: ใช้รหัส <b>${esc(c.code)}</b>${c.is_alias ? '' : ' (รหัสในทะเบียน)'}${c.is_alias && w ? ` ${_bmcBtn('ใช้รหัสในทะเบียนแทน', `bmcAlAct(${_bmcQ(d.code)},{action:'register',system:${_bmcQ(c.system)}},'กลับไปใช้รหัสในทะเบียนแล้ว')`)}` : ''}</span>`).join('');
  cell.innerHTML = `<div class="p-3" style="background:var(--bg-subtle);border-radius:var(--r-sm)">
    <div class="text-xs font-semibold" style="color:var(--text-primary)">รหัสในระบบอื่นของ ${esc(d.code)} ${esc(d.name || '')}</div>
    <div class="text-[11px] mb-2" style="color:var(--text-muted)">รหัสในทะเบียนไม่เปลี่ยน และสูตรใช้รหัสนี้ต่อไป · รหัสในระบบอื่นใช้รับไฟล์เข้าและแสดงในไฟล์ที่ส่งให้ระบบนั้น</div>
    <div class="text-[11.5px] mb-2">${sysLine}</div>
    ${d.aliases.length ? `<div style="overflow-x:auto"><table class="text-[11.5px] w-full" style="min-width:520px"><thead><tr class="text-left" style="color:var(--text-muted)">
      <th class="pr-2">ระบบ</th><th class="pr-2">รหัส</th><th class="pr-2">เริ่มใช้</th><th class="pr-2">สถานะ</th><th class="pr-2">หมายเหตุ</th><th></th></tr></thead><tbody>${d.aliases.map(a => `
      <tr style="border-top:1px solid var(--border);vertical-align:top"><td class="py-1 pr-2">${esc(a.system_th)}</td><td class="py-1 pr-2"><b>${esc(a.alias)}</b></td>
      <td class="py-1 pr-2">${a.valid_from ? esc(fmtDay(a.valid_from)) : '—'}</td><td class="py-1 pr-2">${chip(a)}</td>
      <td class="py-1 pr-2">${esc(a.note || '')}${a.created_by ? `<div style="color:var(--text-muted)">${esc(whoTh(a.created_by))}</div>` : ''}</td><td class="py-1">${act(a)}</td></tr>`).join('')}</tbody></table></div>`
      : '<div class="text-[11.5px]" style="color:var(--text-muted)">ยังไม่มีรหัสในระบบอื่น</div>'}
    ${w ? `<div class="flex flex-wrap items-end gap-2 mt-3">
      <label class="text-[11px]" style="color:var(--text-muted)">รหัสใหม่<br><input id="bmcAlNew" maxlength="40" style="${_BMC_FIELD};width:150px"></label>
      <label class="text-[11px]" style="color:var(--text-muted)">ระบบ<br>${_bmcSysSel('bmcAlSys', 'acc')}</label>
      <label class="text-[11px]" style="color:var(--text-muted)">เริ่มใช้วันที่ (ถ้ามี)<br><input id="bmcAlFrom" type="date" style="${_BMC_FIELD}"></label>
      <label class="text-[11px]" style="color:var(--text-muted)">หมายเหตุ<br><input id="bmcAlNote" maxlength="200" style="${_BMC_FIELD};width:200px"></label>
      <label class="text-[11px] flex items-center gap-1" style="color:var(--text-secondary);padding-bottom:6px"><input type="checkbox" id="bmcAlCur" checked> ตั้งเป็นรหัสปัจจุบันของระบบนี้</label>
      ${_bmcBtn('เพิ่มรหัส', `bmcAlAdd(${_bmcQ(d.code)})`, true)}</div>` : ''}
    <div id="bmcAlStatus" class="text-[11px] mt-2" role="status"></div></div>`;
}
async function bmcAlAct(code, body, okText) {
  try {
    const out = await _bmcJson(`${API}/api/bom-center/ingredients/${encodeURIComponent(code)}/aliases`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    _bmcAlState = out.state;
    await bmcLoadIng(true);                          // the register list shows the codes in use; the open detail redraws from the answer
    bmcStatus('bmcAlStatus', true, okText || 'บันทึกแล้ว');
  } catch (e) { bmcStatus('bmcAlStatus', false, String(e.message || e).slice(0, 220)); }
}
async function bmcAlAdd(code) {
  const alias = (document.getElementById('bmcAlNew').value || '').trim();
  if (!alias) { bmcStatus('bmcAlStatus', 'warn', 'ระบุรหัสใหม่'); return; }
  const sys = document.getElementById('bmcAlSys').value, cur = document.getElementById('bmcAlCur').checked;
  if (!await uiConfirm(`บันทึก ${alias} เป็นรหัสในระบบอื่นของ ${code} ${_bmcItemName(code)}\n${_BMC_SYS_TH[sys]}${cur ? ' · ตั้งเป็นรหัสปัจจุบัน' : ''}\nรหัสในทะเบียนและสูตรไม่เปลี่ยน`,
      { title: 'ยืนยันการเพิ่มรหัสในระบบอื่น', ok: 'เพิ่มรหัส', cancel: 'ยกเลิก' })) return;
  await bmcAlAct(code, { action: 'add', alias, system: sys, valid_from: document.getElementById('bmcAlFrom').value || null,
    note: (document.getElementById('bmcAlNote').value || '').trim() || null, make_current: cur }, `เพิ่มรหัส ${alias} แล้ว`);
}
async function bmcAlRetire(code, alias) {
  if (!await uiConfirm(`เลิกใช้รหัส ${alias} ของ ${code}\nรหัสนี้ไม่ถูกใช้รับไฟล์เข้าอีก · ข้อมูลยังเก็บไว้และใช้อีกครั้งได้`,
      { title: 'ยืนยันการเลิกใช้รหัส', ok: 'เลิกใช้', cancel: 'ยกเลิก' })) return;
  await bmcAlAct(code, { action: 'retire', alias }, `เลิกใช้รหัส ${alias} แล้ว`);
}

// ── แจ้งเปลี่ยนรหัสสินค้า (ประสานงานขาย) ────────────────────────────────────────────────────────
async function bmcLoadCodeReq() {
  const form = document.getElementById('bmcCrqForm');
  if (!form) return;
  form.style.display = (hasModule('supply.ordering') || hasModule('bom.center')) ? '' : 'none';
  _bmcUcRegister();
  const box = document.getElementById('bmcCrqList');
  try {
    const d = await _bmcJson(`${API}/api/bom-center/codes/requests?status=all`);
    box.innerHTML = d.requests.length ? `<div style="overflow-x:auto"><table class="text-[11.5px] w-full" style="min-width:560px"><thead><tr class="text-left" style="color:var(--text-muted)">
        <th class="pr-2">วันที่แจ้ง</th><th class="pr-2">วัตถุดิบ</th><th class="pr-2">รหัสเดิม → รหัสใหม่</th><th class="pr-2">ระบบ</th><th class="pr-2">เริ่มใช้</th><th class="pr-2">สถานะ</th></tr></thead><tbody>${
      d.requests.slice(0, 60).map(r => `<tr style="border-top:1px solid var(--border);vertical-align:top">
        <td class="py-1 pr-2">${esc(fmtAt(r.requested_at))}<div style="color:var(--text-muted)">${esc(whoTh(r.requested_by))}</div></td>
        <td class="py-1 pr-2"><b>${esc(r.item_name || r.ingredient_code)}</b><div style="color:var(--text-muted)">${esc(r.ingredient_code)}</div></td>
        <td class="py-1 pr-2">${esc(r.old_code || '—')} → <b>${esc(r.new_code)}</b>${r.note ? `<div style="color:var(--text-muted)">${esc(r.note)}</div>` : ''}</td>
        <td class="py-1 pr-2">${esc(r.system_th)}</td><td class="py-1 pr-2">${r.valid_from ? esc(fmtDay(r.valid_from)) : '—'}</td>
        <td class="py-1 pr-2">${_bmcChip(r.status_th, r.status === 'applied' ? 'var(--good-ink)' : r.status === 'rejected' ? 'var(--bad-ink)' : 'var(--amber-ink)')}${r.decision_note ? `<div style="color:var(--text-muted)">${esc(r.decision_note)}</div>` : ''}</td></tr>`).join('')}</tbody></table></div>`
      : '<span style="color:var(--text-muted)">ยังไม่มีคำขอ</span>';
  } catch (e) { box.innerHTML = `<span style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
async function bmcCrqSuggest() {
  const name = (document.getElementById('bmcCrqName').value || '').trim(), box = document.getElementById('bmcCrqSug');
  if (!name) { box.innerHTML = '<span style="color:var(--text-muted)">ใส่ชื่อตามระบบอื่นก่อน แล้วกดค้นหาวัตถุดิบที่ใกล้เคียง</span>'; return; }
  try {
    const d = await _bmcJson(`${API}/api/bom-center/codes/suggest?name=${encodeURIComponent(name)}&code=${encodeURIComponent((document.getElementById('bmcCrqNew').value || '').trim())}`);
    box.innerHTML = d.suggestions.length ? 'วัตถุดิบที่ใกล้เคียง: ' + d.suggestions.map(s => `<button type="button" onclick="document.getElementById('bmcCrqItem').value=${_bmcQ(s.code)};bmcCrqPicked()" style="border:1px solid var(--border);border-radius:var(--r-sm);padding:2px 8px;margin:2px;font-size:11px;cursor:pointer;background:transparent;color:var(--text-primary)">${esc(s.code)} ${esc(s.name || '')}</button>`).join('')
      : '<span style="color:var(--text-muted)">ไม่พบวัตถุดิบที่ชื่อใกล้เคียง · พิมพ์รหัสหรือชื่อในช่องวัตถุดิบเดิมเพื่อค้นหา</span>';
  } catch (e) { box.innerHTML = `<span style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
function bmcCrqPicked() {                           // the chosen item: show its name, and the code in use as the old code
  const el = document.getElementById('bmcCrqItem'), code = _bmcItemOf(el.value);
  document.getElementById('bmcCrqItemName').textContent = code ? `${_bmcItemName(code)}` : (el.value.trim() ? 'ไม่พบรหัสหรือชื่อนี้ในทะเบียน' : '');
  if (code) el.value = code;
  const old = document.getElementById('bmcCrqOld');
  if (old && !old.dataset.touched) old.value = code || '';
}
async function bmcCrqSend() {
  const v = id => (document.getElementById(id).value || '').trim();
  const item = _bmcItemOf(v('bmcCrqItem'));
  if (!v('bmcCrqNew')) { bmcStatus('bmcCrqStatus', 'warn', 'ระบุรหัสใหม่'); return; }
  if (!item) { bmcStatus('bmcCrqStatus', 'warn', 'เลือกวัตถุดิบเดิมในทะเบียน'); return; }
  const sys = document.getElementById('bmcCrqSys').value;
  if (!await uiConfirm(`แจ้งว่า ${item} ${_bmcItemName(item)} ใช้รหัสใหม่ ${v('bmcCrqNew')} ใน${_BMC_SYS_TH[sys]}\nฝ่ายวิเคราะห์ตรวจก่อนบันทึก · รหัสในทะเบียนและสูตรไม่เปลี่ยน`,
      { title: 'ยืนยันการแจ้งเปลี่ยนรหัสสินค้า', ok: 'ส่งให้ฝ่ายวิเคราะห์', cancel: 'ยกเลิก' })) return;
  try {
    const r = await _bmcJson(`${API}/api/bom-center/codes/request`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
      new_code: v('bmcCrqNew'), ingredient_code: item, system: sys, old_code: v('bmcCrqOld') || null, name: v('bmcCrqName') || null,
      valid_from: document.getElementById('bmcCrqFrom').value || null, note: v('bmcCrqNote') || null }) });
    ['bmcCrqNew', 'bmcCrqName', 'bmcCrqItem', 'bmcCrqOld', 'bmcCrqNote', 'bmcCrqFrom'].forEach(id => { const e = document.getElementById(id); if (e) { e.value = ''; delete e.dataset.touched; } });
    document.getElementById('bmcCrqSug').innerHTML = ''; document.getElementById('bmcCrqItemName').textContent = '';
    bmcStatus('bmcCrqStatus', true, String(r.message || 'ส่งคำขอแล้ว').replace(/^✓\s*/, ''));
    bmcLoadCodeReq();
  } catch (e) { bmcStatus('bmcCrqStatus', false, String(e.message || e).slice(0, 220)); }
}

// the analyst: รหัสใหม่ที่รออนุมัติ (at the top of ทะเบียนวัตถุดิบ)
async function bmcLoadCodeReqCard() {
  const card = document.getElementById('bmcCodeReqCard');
  if (!card) return;
  if (!_bmcCanPrices()) { card.style.display = 'none'; return; }
  try {
    const d = await _bmcJson(`${API}/api/bom-center/codes/requests?status=pending`);
    if (!d.requests.length) { card.style.display = 'none'; card.innerHTML = ''; return; }
    card.style.display = '';
    card.innerHTML = `<div class="text-sm font-semibold mb-1" style="color:var(--text-primary)">รหัสใหม่ที่รออนุมัติ (${d.requests.length})</div>
      <div class="text-[11px] mb-2" style="color:var(--text-muted)">ประสานงานขายแจ้งว่าวัตถุดิบเดิมมีรหัสใหม่ในระบบอื่น · “ใช้” บันทึกเป็นรหัสในระบบอื่นและตั้งเป็นรหัสปัจจุบันของระบบนั้น สูตรไม่ต้องแก้ · “ไม่ใช้” ต้องระบุเหตุผล <span id="bmcCodeReqStatus" class="ml-2"></span></div>
      <div style="overflow-x:auto"><table class="text-[11.5px] w-full" style="min-width:620px"><thead><tr class="text-left" style="color:var(--text-muted)">
        <th class="pr-2">วัตถุดิบ</th><th class="pr-2">รหัสเดิม → รหัสใหม่</th><th class="pr-2">ระบบ</th><th class="pr-2">เริ่มใช้</th><th class="pr-2">แจ้งโดย</th><th></th></tr></thead><tbody>${
      d.requests.map(r => `<tr style="border-top:1px solid var(--border);vertical-align:top">
        <td class="py-1 pr-2"><b style="color:var(--text-primary)">${esc(r.item_name || r.ingredient_code)}</b><div style="color:var(--text-muted)">${esc(r.ingredient_code)}</div></td>
        <td class="py-1 pr-2">${esc(r.old_code || '—')} → <b>${esc(r.new_code)}</b>${r.name ? `<div style="color:var(--text-muted)">ชื่อตามระบบอื่น: ${esc(r.name)}</div>` : ''}${r.note ? `<div style="color:var(--text-muted)">${esc(r.note)}</div>` : ''}</td>
        <td class="py-1 pr-2">${esc(r.system_th)}</td><td class="py-1 pr-2">${r.valid_from ? esc(fmtDay(r.valid_from)) : '—'}</td>
        <td class="py-1 pr-2">${esc(whoTh(r.requested_by))}<div style="color:var(--text-muted)">${esc(fmtAt(r.requested_at))}</div></td>
        <td class="py-1"><div class="flex flex-wrap gap-1">${_bmcBtn('ใช้', `bmcCodeReqDecide(${r.request_id},'applied',${_bmcQ(r.new_code)},${_bmcQ(r.item_name || r.ingredient_code)},${_bmcQ(r.system_th)})`, true)}${_bmcBtn('ไม่ใช้', `bmcCodeReqDecide(${r.request_id},'rejected',${_bmcQ(r.new_code)},${_bmcQ(r.item_name || r.ingredient_code)},${_bmcQ(r.system_th)})`)}</div></td></tr>`).join('')}</tbody></table></div>`;
  } catch (e) { card.style.display = ''; card.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">${esc(String(e.message || e))}</span>`; }
}
async function bmcCodeReqDecide(id, decision, code, name, system) {
  let note = null;
  if (decision === 'rejected') {
    note = await uiPrompt(`เหตุผลที่ไม่ใช้รหัส ${code} ของ ${name} (จำเป็น · ประสานงานขายอ่านได้)`, '', { title: 'ไม่ใช้รหัสที่แจ้ง', okText: 'ไม่ใช้' });
    if (!note || !String(note).trim()) return;
  } else if (!await uiConfirm(`บันทึก ${code} เป็นรหัสใหม่ของ ${name} ใน${system}\nรหัสในทะเบียนและสูตรไม่เปลี่ยน · ไฟล์ที่ส่งให้ระบบนั้นแสดงรหัสใหม่`,
    { title: 'ยืนยันการใช้รหัสใหม่', ok: 'ใช้รหัสนี้', cancel: 'ยกเลิก' })) return;
  try {
    const out = await _bmcJson(`${API}/api/bom-center/codes/request/decide`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request_id: id, decision, note }) });
    _bmcIng = [];
    await bmcLoadCodeReqCard();
    bmcStatus('bmcCodeReqStatus', true, String(out.message || '').replace(/^✓\s*/, ''));
    if (decision === 'applied') bmcLoadIng(true);
  } catch (e) { bmcStatus('bmcCodeReqStatus', false, String(e.message || e).slice(0, 220)); }
}

// the menus of the recipes toolbar (นำเข้า ▾ · ดาวน์โหลด ▾) close on a click outside, on a chosen file and on a chosen download
document.addEventListener('click', e => {
  document.querySelectorAll('details.bmc-menu[open]').forEach(d => { if (!d.contains(e.target)) d.open = false; });
  const it = e.target && e.target.closest && e.target.closest('a.bmc-menu-item');
  if (it) { const d = it.closest('details.bmc-menu'); if (d) d.open = false; }
});
document.addEventListener('change', e => {
  if (e.target && e.target.type === 'file') { const d = e.target.closest && e.target.closest('details.bmc-menu'); if (d) d.open = false; }
});
