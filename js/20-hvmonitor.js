// ══ High Value Product Monitor (owner 2026-10-25: "high value product monitor") ══════════════════════════════════════════════
// The cost per head of eight high-value ingredients at every branch (/api/cost/ingredient-groups, backend/ingredient_groups.py):
// one group = the comparison table (ต้นทุน/หัว · จาน/หัว · ต้นทุน/จาน · สัดส่วนในต้นทุนอาหาร · ต้นทุนอาหาร/หัว · สาเหตุที่เปลี่ยน), ทั้งหมด =
// the branch x group matrix with a heat by the ratio to the chain. The period is the top bar's (one control per page); every change
// compares with เทียบช่วงก่อน (the same days of the period before). A row opens the sheet: every figure now / prev / ly and the menus.
let _hvm = { slug: 'salmon', sort: 'cph', dir: -1, d: null, seq: 0, key: '' };
const HVM_HOT = 1.2;          // a branch whose cost / head is this much above the chain's: bad ink, bold
const HVM_CAUSE = { customers: 'ลูกค้า', plates_per_head: 'กินต่อหัว', cost_per_plate: 'ต้นทุน/จาน' };
const HVM_CAUSE_SHORT = { customers: 'ลูกค้า', plates_per_head: 'กิน', cost_per_plate: 'ราคา' };
try { const s = localStorage.getItem('oshinei_hvm_slug'); if (s) _hvm.slug = s; } catch (e) { /* no storage */ }

const _hvmB = v => v == null || !isFinite(Number(v)) ? '—'
  : '฿' + Number(v).toLocaleString('th-TH', { minimumFractionDigits: Math.abs(v) < 100 ? 1 : 0, maximumFractionDigits: Math.abs(v) < 100 ? 1 : 0 });
const _hvmN1 = v => v == null || !isFinite(Number(v)) ? '—' : Number(v).toLocaleString('th-TH', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const _hvmPct = v => v == null || !isFinite(Number(v)) ? '—' : Number(v).toFixed(1) + '%';
const _hvmSigned = v => v == null || !isFinite(Number(v)) ? '—' : (v > 0 ? '+' : v < 0 ? '−' : '') + '฿' + Math.abs(Math.round(v)).toLocaleString('th-TH');

async function loadHvMonitor(force) {
  const q = gPeriodQuery();
  if (!force && _hvm.key === q && _hvm.d) { hvmRender(); return; }
  _hvm.key = q;
  const seq = ++_hvm.seq;
  const body = document.getElementById('hvmBody');
  if (body && !_hvm.d) body.innerHTML = '<p class="hvm-empty">กำลังโหลด…</p>';
  let d;
  try { d = await api(`${API}/api/cost/ingredient-groups?${q}`); } catch (e) { return; }
  if (seq !== _hvm.seq) return;
  _hvm.d = d;
  periodChip(periodOf(d), true);
  hvmRender();
}

function hvmSetGroup(slug) {
  _hvm.slug = slug;
  try { localStorage.setItem('oshinei_hvm_slug', slug); } catch (e) { /* no storage */ }
  _hvm.sort = 'cph'; _hvm.dir = -1;
  hvmRender();
}
function hvmSort(k) {
  if (_hvm.sort === k) _hvm.dir = -_hvm.dir; else { _hvm.sort = k; _hvm.dir = k === 'name' ? 1 : -1; }
  hvmRender();
}

function _hvmGroups() { return (_hvm.d && _hvm.d.groups) || []; }
function _hvmLabel(slug) { const g = _hvmGroups().find(x => x.slug === slug); return g ? g.label : slug; }
function _hvmRange() { return periodOf(_hvm.d); }
// the compared windows of a change: '1–19 ต.ค. 2569 เทียบ 1–19 ก.ย. 2569' (+ why there is no base)
function _hvmVs() {
  const r = _hvmRange(); if (!r) return '';
  const why = (r.reasons || {}).prev;
  return `${r.label_th} เทียบ ${r.prev_label_th}${why ? ' · ' + why : ''}`;
}
const _hvmDelta = v => periodPctDelta(v, { upGood: false, title: _hvmVs() });

function hvmRenderChips() {
  const el = document.getElementById('hvmChips'); if (!el) return;
  const opts = [['all', 'ทั้งหมด']].concat(_hvmGroups().map(g => [g.slug, g.label]));
  el.innerHTML = opts.map(([k, l]) => `<button type="button" class="gran-btn${_hvm.slug === k ? ' gran-active' : ''}" data-g="${esc(k)}" aria-pressed="${_hvm.slug === k}" onclick="hvmSetGroup('${esc(k)}')">${esc(l)}</button>`).join('');
}

function hvmRender() {
  const d = _hvm.d, body = document.getElementById('hvmBody');
  if (!body || !d) return;
  if (!_hvmGroups().some(g => g.slug === _hvm.slug) && _hvm.slug !== 'all') _hvm.slug = 'salmon';
  hvmRenderChips();
  const r = _hvmRange();
  const q = document.getElementById('hvmRangeQ');
  if (q) q.innerHTML = r ? periodQ(r, '%') : '';
  if (!d.chain || !(d.branches || []).length) {
    const why = (d.reasons || {}).cur;
    body.innerHTML = `<p class="hvm-empty">${esc(why || 'ไม่มีข้อมูลในช่วงที่เลือก')}</p>`;
    return;
  }
  body.innerHTML = _hvm.slug === 'all' ? hvmMatrixHtml(d) : hvmTableHtml(d, _hvm.slug);
}

// ── one group: the comparison table ──
function _hvmCauseOf(cell) { return (cell && cell.cause && cell.cause.prev) || {}; }
function _hvmMainVal(cell) { const c = _hvmCauseOf(cell); return c.main ? c[c.main] : null; }
function _hvmSortVal(row, slug, k) {
  const c = row.cells[slug] || {};
  if (k === 'name') return row.branch || '';
  if (k === 'cph') return c.cost_per_head;
  if (k === 'pph') return c.plates_per_head;
  if (k === 'cpp') return c.cost_per_plate;
  if (k === 'share') return c.share_of_food_cost;
  if (k === 'food') return row.food_cost_per_head;
  if (k === 'cause') return _hvmMainVal(c);
  return c.cost_per_head;
}
function _hvmSorted(rows, valOf) {
  const dir = _hvm.dir;
  return rows.slice().sort((a, b) => {
    const x = valOf(a), y = valOf(b);
    if (typeof x === 'string' || typeof y === 'string') return dir * String(x).localeCompare(String(y), 'th');
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    return dir * (x - y);
  });
}
function _hvmTh(k, label, cls) {
  const on = _hvm.sort === k, arrow = on ? (_hvm.dir > 0 ? ' ↑' : ' ↓') : '';
  return `<th class="${cls || ''}" aria-sort="${on ? (_hvm.dir > 0 ? 'ascending' : 'descending') : 'none'}"><button type="button" class="hvm-sort" onclick="hvmSort('${k}')">${label}${arrow}</button></th>`;
}
function hvmCauseHtml(cell) {
  const c = _hvmCauseOf(cell);
  if (c.total == null) return `<span class="hvm-muted" title="${esc(c.reason || _hvmVs())}">—</span>`;
  const keys = ['customers', 'plates_per_head', 'cost_per_plate'];
  const parts = keys.map(k => c[k]);
  const hasParts = parts.every(v => v != null);
  const maxPos = hasParts ? keys.filter(k => c[k] > 0).sort((a, b) => c[b] - c[a])[0] : null;
  const ink = c.total > 0 ? 'var(--bad-ink)' : c.total < 0 ? 'var(--good-ink)' : 'var(--text-secondary)';
  const arrow = c.total > 0 ? '▲' : c.total < 0 ? '▼' : '';
  const tip = esc(_hvmVs());
  let h = c.main ? `<div class="hvm-cause-main">สาเหตุหลัก: ${HVM_CAUSE[c.main]}</div>` : '';
  h += `<div class="hvm-cause-tot" style="color:${ink}" title="${tip}">${arrow} ${_hvmSigned(c.total).replace(/^[+−]/, '')}${c.total_pct != null ? ` (${Math.abs(c.total_pct).toFixed(1)}%)` : ''}</div>`;
  if (hasParts) h += keys.map(k => `<div class="hvm-cause-part${k === maxPos ? ' hvm-cause-up' : ''}">${HVM_CAUSE[k]} ${_hvmSigned(c[k])}</div>`).join('');
  else if (c.reason) h += `<div class="hvm-cause-part hvm-muted">${esc(c.reason)}</div>`;
  return h;
}
function hvmTableHtml(d, slug) {
  const ch = d.chain, chainCph = (ch.cells[slug] || {}).cost_per_head;
  const rows = _hvmSorted(d.branches, x => _hvmSortVal(x, slug, _hvm.sort));
  const val = (v, f, delta) => `<div class="hvm-v">${f(v)}</div><div class="hvm-d">${delta}</div>`;
  const tr = (row, isChain, i) => {
    const c = row.cells[slug] || {}, chg = (c.change && c.change.prev) || {};
    const hot = !isChain && c.cost_per_head != null && chainCph ? c.cost_per_head >= chainCph * HVM_HOT : false;
    const unc = c.uncosted_plates ? ` title="จานที่ไม่มีต้นทุน ${fmtN(c.uncosted_plates)} จาน"` : '';
    return `<tr class="${isChain ? 'hvm-chain' : ''}" onclick="hvmOpenRow(${isChain ? -1 : i})">
      <td class="hvm-name">${isChain ? 'ทั้งเครือ' : esc(row.branch)}</td>
      <td class="hvm-num${hot ? ' hvm-hot' : ''}" style="color:${hot ? 'var(--bad-ink)' : 'var(--text-primary)'}"${hot ? ` title="สูงกว่าทั้งเครือ ${((c.cost_per_head / chainCph - 1) * 100).toFixed(0)}%"` : ''}>${val(c.cost_per_head, _hvmB, _hvmDelta(chg.cost_per_head))}</td>
      <td class="hvm-num" style="color:var(--text-primary)">${val(c.plates_per_head, _hvmN1, _hvmDelta(chg.plates_per_head))}</td>
      <td class="hvm-num" style="color:var(--text-primary)"${unc}>${val(c.cost_per_plate, _hvmB, _hvmDelta(chg.cost_per_plate))}</td>
      <td class="hvm-num"><div class="hvm-v">${_hvmPct(c.share_of_food_cost)}</div></td>
      <td class="hvm-num" style="color:var(--text-primary)">${val(row.food_cost_per_head, _hvmB, _hvmDelta((row.food_change || {}).prev))}</td>
      <td class="hvm-cause">${hvmCauseHtml(c)}</td>
    </tr>`;
  };
  const idx = new Map(d.branches.map((b, i) => [b, i]));
  return `<div class="hvm-wrap"><table id="hvmTable" class="hvm-tbl wt"><thead><tr>
      ${_hvmTh('name', 'สาขา', 'hvm-name')}${_hvmTh('cph', 'ต้นทุน/หัว', 'hvm-num')}${_hvmTh('pph', 'จาน/หัว', 'hvm-num')}${_hvmTh('cpp', 'ต้นทุน/จาน', 'hvm-num')}
      ${_hvmTh('share', 'สัดส่วนในต้นทุนอาหาร', 'hvm-num')}${_hvmTh('food', 'ต้นทุนอาหาร/หัว', 'hvm-num')}${_hvmTh('cause', 'สาเหตุที่เปลี่ยน', 'hvm-cause')}
    </tr></thead><tbody>${tr(ch, true, -1)}${rows.map(b => tr(b, false, idx.get(b))).join('')}</tbody></table></div>`;
}

// ── ทั้งหมด: branches x groups ──
function _hvmHeat(v, base) {
  if (v == null || !base) return '';
  const r = v / base;
  if (r >= 1.2) return 'hvm-h3';
  if (r >= 1.1) return 'hvm-h2';
  if (r >= 1.03) return 'hvm-h1';
  if (r <= 0.9) return 'hvm-g1';
  return '';
}
function _hvmCauseWord(cell) {
  const c = _hvmCauseOf(cell);
  if (!c.main) return '';
  const tip = `${_hvmVs()} · ${['customers', 'plates_per_head', 'cost_per_plate'].map(k => `${HVM_CAUSE[k]} ${_hvmSigned(c[k])}`).join(' · ')}`;
  return `<div class="hvm-cw" title="${esc(tip)}">${HVM_CAUSE_SHORT[c.main]}</div>`;
}
function hvmMatrixHtml(d) {
  const gs = _hvmGroups(), ch = d.chain;
  const sortVal = row => _hvm.sort === 'name' ? row.branch : _hvm.sort === 'food' ? row.food_cost_per_head
    : ((row.cells[gs.some(g => g.slug === _hvm.sort) ? _hvm.sort : 'salmon'] || {}).cost_per_head);
  const rows = _hvmSorted(d.branches, sortVal);
  const idx = new Map(d.branches.map((b, i) => [b, i]));
  const cell = (row, slug, isChain) => {
    const c = row.cells[slug] || {}, base = (ch.cells[slug] || {}).cost_per_head;
    const h = isChain ? '' : _hvmHeat(c.cost_per_head, base);
    return `<td class="hvm-num ${h}" style="color:${h === 'hvm-h3' ? 'var(--bad-ink)' : 'var(--text-primary)'}"><div class="hvm-v">${_hvmB(c.cost_per_head)}</div><div class="hvm-d">${_hvmDelta((c.change && c.change.prev || {}).cost_per_head)}</div>${_hvmCauseWord(c)}</td>`;
  };
  const tr = (row, isChain, i) => `<tr class="${isChain ? 'hvm-chain' : ''}" onclick="hvmOpenRow(${isChain ? -1 : i}, true)">
      <td class="hvm-name hvm-stick">${isChain ? 'ทั้งเครือ' : esc(row.branch)}</td>${gs.map(g => cell(row, g.slug, isChain)).join('')}
      <td class="hvm-num ${isChain ? '' : _hvmHeat(row.food_cost_per_head, ch.food_cost_per_head)}"><div class="hvm-v">${_hvmB(row.food_cost_per_head)}</div><div class="hvm-d">${_hvmDelta((row.food_change || {}).prev)}</div></td></tr>`;
  return `<div class="hvm-wrap hvm-mx-wrap"><table id="hvmMatrix" class="hvm-tbl hvm-mx wt"><thead><tr>
      ${_hvmTh('name', 'สาขา', 'hvm-name hvm-stick')}${gs.map(g => _hvmTh(g.slug, esc(g.label), 'hvm-num')).join('')}${_hvmTh('food', 'ต้นทุนอาหาร/หัว', 'hvm-num')}
    </tr></thead><tbody>${tr(ch, true, -1)}${rows.map(b => tr(b, false, idx.get(b))).join('')}</tbody></table></div>`;
}

// ── the sheet of a row: every figure now / prev / ly, the cause, the menus ──
function _hvmSheet(title, html) {
  if (typeof uiHelp !== 'function') return;
  uiHelp(title, html);
  const w = document.getElementById('uiHelpDlg');
  if (w) w.classList.add('hvm-sheet');
}
function _hvmRowOf(i) { return i < 0 ? _hvm.d.chain : _hvm.d.branches[i]; }
function hvmOpenRow(i, matrix) {
  if (!_hvm.d) return;
  if (matrix || _hvm.slug === 'all') return hvmOpenBranch(i);
  return hvmOpenCell(i, _hvm.slug);
}
function _hvmFigTable(rows) {
  const r = _hvmRange() || {};
  return `<table class="hvm-fig"><thead><tr><th></th><th>ช่วงนี้</th><th title="${esc(r.prev_label_th || '')}">ช่วงก่อน</th><th title="${esc(r.ly_label_th || '')}">ปีก่อน</th></tr></thead><tbody>`
    + rows.map(([l, a, b, c]) => `<tr><th>${l}</th><td>${a}</td><td>${b}</td><td>${c}</td></tr>`).join('') + '</tbody></table>';
}
function hvmOpenCell(i, slug) {
  const row = _hvmRowOf(i), c = row.cells[slug] || {}, p = c.prev || {}, y = c.ly || {}, r = _hvmRange() || {};
  const name = i < 0 ? 'ทั้งเครือ' : row.branch;
  const figs = _hvmFigTable([
    ['ต้นทุน/หัว', _hvmB(c.cost_per_head), _hvmB(p.cost_per_head), _hvmB(y.cost_per_head)],
    ['จาน/หัว', _hvmN1(c.plates_per_head), _hvmN1(p.plates_per_head), _hvmN1(y.plates_per_head)],
    ['ต้นทุน/จาน', _hvmB(c.cost_per_plate), _hvmB(p.cost_per_plate), _hvmB(y.cost_per_plate)],
    ['สัดส่วนในต้นทุนอาหาร', _hvmPct(c.share_of_food_cost), _hvmPct(p.share_of_food_cost), _hvmPct(y.share_of_food_cost)],
    ['จาน', fmtN(c.plates), fmtN(p.plates), fmtN(y.plates)],
    ['ต้นทุนรวม', _hvmB(c.cost), _hvmB(p.cost), _hvmB(y.cost)],
    ['จานที่ไม่มีต้นทุน', fmtN(c.uncosted_plates), fmtN(p.uncosted_plates), fmtN(y.uncosted_plates)],
    ['ต้นทุนอาหาร/หัว', _hvmB(row.food_cost_per_head), _hvmB(row.prev_food_cost_per_head), _hvmB(row.ly_food_cost_per_head)],
    ['ลูกค้า', fmtN(row.customers), fmtN(row.prev_customers), fmtN(row.ly_customers)],
  ]);
  const why = r.reasons || {};
  const notes = ['prev', 'ly'].filter(k => why[k]).map(k => `<p class="hvm-muted">${k === 'prev' ? 'ช่วงก่อน' : 'ปีก่อน'} · ${esc(why[k])}</p>`).join('');
  const cause = `<div class="hvm-sh-sec"><div class="hvm-sh-h">สาเหตุที่เปลี่ยน · เทียบช่วงก่อน</div><div class="hvm-cause">${hvmCauseHtml(c)}</div></div>`;
  const html = `<p class="hvm-sh-range">${esc(periodSpan(r))}</p>${figs}${notes}${cause}<div class="hvm-sh-sec"><div class="hvm-sh-h">เมนูของกลุ่มนี้</div><div id="hvmMenus"><p class="hvm-muted">กำลังโหลด…</p></div></div>`;
  _hvmSheet(`${name} · ${_hvmLabel(slug)}`, html);
  hvmLoadMenus(i < 0 ? '' : row.branch, slug);
}
async function hvmLoadMenus(branch, slug) {
  const p = new URLSearchParams(gPeriodQuery());
  p.delete('branch');
  if (branch) p.set('branch', branch); else if (getBranch()) p.set('branch', getBranch());
  p.set('slug', slug);
  let d;
  try { d = await api(`${API}/api/cost/ingredient-groups/menus?${p}`); } catch (e) { return; }
  const el = document.getElementById('hvmMenus'); if (!el) return;
  const ms = (d && d.menus) || [];
  if (!ms.length) { el.innerHTML = '<p class="hvm-muted">ไม่มีเมนูของกลุ่มนี้ในช่วงที่เลือก</p>'; return; }
  el.innerHTML = `<table class="hvm-fig hvm-menus"><thead><tr><th>เมนู</th><th>จาน</th><th>ต้นทุน</th><th>ต้นทุน/จาน</th><th>จานช่วงก่อน</th></tr></thead><tbody>`
    + ms.map(m => `<tr><th>${esc(m.name)}${m.mixed ? ` <span class="hvm-mixed" title="นับในกลุ่ม ${esc((m.groups || []).map(_hvmLabel).join(' · '))}">หลายกลุ่ม</span>` : ''}${m.uncosted_plates ? ` <span class="hvm-unc">ไม่มีต้นทุน ${fmtN(m.uncosted_plates)} จาน</span>` : ''}<div class="hvm-code">${esc(m.sku_code)}</div></th>`
      + `<td>${fmtN(m.plates)}</td><td>${_hvmB(m.cost)}</td><td>${_hvmB(m.cost_per_plate)}</td><td>${m.prev_plates == null ? '—' : fmtN(m.prev_plates)}</td></tr>`).join('')
    + '</tbody></table>';
}
function hvmOpenBranch(i) {
  const row = _hvmRowOf(i), name = i < 0 ? 'ทั้งเครือ' : row.branch;
  const gs = _hvmGroups();
  const figs = _hvmFigTable(gs.map(g => { const c = row.cells[g.slug] || {}; return [
    `<button type="button" class="hvm-link" onclick="hvmOpenCell(${i}, '${g.slug}')">${esc(g.label)}</button>`,
    _hvmB(c.cost_per_head), _hvmB((c.prev || {}).cost_per_head), _hvmB((c.ly || {}).cost_per_head)]; })
    .concat([['ต้นทุนอาหาร/หัว', _hvmB(row.food_cost_per_head), _hvmB(row.prev_food_cost_per_head), _hvmB(row.ly_food_cost_per_head)],
      ['ลูกค้า', fmtN(row.customers), fmtN(row.prev_customers), fmtN(row.ly_customers)]]));
  _hvmSheet(`${name} · ต้นทุน/หัว`, `<p class="hvm-sh-range">${esc(periodSpan(_hvmRange()))}</p>${figs}`);
}

// ── CSV of the view on screen ──
function hvmExport() {
  const d = _hvm.d; if (!d || !d.chain) return;
  const q = s => `"${String(s == null ? '' : s).replace(/"/g, '""')}"`;
  const n = v => v == null ? '' : v;
  const r = _hvmRange() || {};
  const all = [{ name: 'ทั้งเครือ', row: d.chain }].concat(d.branches.map(b => ({ name: b.branch, row: b })));
  let lines;
  if (_hvm.slug === 'all') {
    const gs = _hvmGroups();
    lines = [['สาขา'].concat(gs.flatMap(g => [`${g.label} ต้นทุน/หัว`, `${g.label} เทียบช่วงก่อน %`, `${g.label} สาเหตุหลัก`]), ['ต้นทุนอาหาร/หัว', 'ต้นทุนอาหาร/หัว เทียบช่วงก่อน %']).map(q).join(',')]
      .concat(all.map(({ name, row }) => [q(name)].concat(gs.flatMap(g => { const c = row.cells[g.slug] || {}, cs = _hvmCauseOf(c);
        return [n(c.cost_per_head), n((c.change && c.change.prev || {}).cost_per_head), q(cs.main ? HVM_CAUSE[cs.main] : '')]; }),
      [n(row.food_cost_per_head), n((row.food_change || {}).prev)]).join(',')));
  } else {
    const s = _hvm.slug;
    lines = [['สาขา', 'ต้นทุน/หัว', 'ต้นทุน/หัว ช่วงก่อน', 'เทียบช่วงก่อน %', 'จาน/หัว', 'จาน/หัว ช่วงก่อน', 'เทียบช่วงก่อน %', 'ต้นทุน/จาน', 'ต้นทุน/จาน ช่วงก่อน',
      'เทียบช่วงก่อน %', 'สัดส่วนในต้นทุนอาหาร %', 'ต้นทุนอาหาร/หัว', 'จาน', 'ต้นทุนรวม', 'จานที่ไม่มีต้นทุน', 'ต้นทุนเปลี่ยน ฿', 'จากลูกค้า ฿', 'จากกินต่อหัว ฿',
      'จากต้นทุนต่อจาน ฿', 'สาเหตุหลัก'].map(q).join(',')]
      .concat(all.map(({ name, row }) => { const c = row.cells[s] || {}, p = c.prev || {}, ch = (c.change && c.change.prev) || {}, cs = _hvmCauseOf(c);
        return [q(name), n(c.cost_per_head), n(p.cost_per_head), n(ch.cost_per_head), n(c.plates_per_head), n(p.plates_per_head), n(ch.plates_per_head),
          n(c.cost_per_plate), n(p.cost_per_plate), n(ch.cost_per_plate), n(c.share_of_food_cost), n(row.food_cost_per_head), n(c.plates), n(c.cost),
          n(c.uncosted_plates), n(cs.total), n(cs.customers), n(cs.plates_per_head), n(cs.cost_per_plate), q(cs.main ? HVM_CAUSE[cs.main] : '')].join(','); }));
  }
  const tag = (r.cur || []).join('_');
  downloadCSV(lines.join('\n'), `high_value_${_hvm.slug}_${tag}.csv`);
}
