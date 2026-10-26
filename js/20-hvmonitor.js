// ══ High Value Product Monitor (owner 2026-10-25: "high value product monitor") ══════════════════════════════════════════════
// Eight high-value ingredients at every branch, two views on one page (one list, a toggle - phone table review 2026-10-25 §3.2, top-10 #1 + #6):
//   บาท (cost roles, /api/cost/ingredient-groups, backend/ingredient_groups.py): the answer line first ("แซลมอน +฿132.0K: ราคา +฿1.28M ·
//     กิน −฿426.9K · ลูกค้า −฿718.2K", the LMDI split of the change), then ทั้งหมด = one table of the 8 ingredients in baht (change ฿, the
//     main cause, cost now, share of the food cost, สาขาที่ดัน) sorted by the change, or one ingredient = the branches sorted by the change
//     in baht with the cause in column 2, สะสม, and the per-head figures; rows under ฿1,000 fold behind "ดูทั้งหมด (N)".
//   จำนวนจาน (every role that reaches the page; DV4 moved it here from the menu-spike card, /api/sales/ingredient-groups-plates, no
//     cost): plates, plates per head and customers, the change in plates split ลูกค้า · กินต่อหัว, sorted by the plates from eating more;
//     the W-vs-W week picker (Mon–Sun, ISO numbers; a week by default) and "เดิม" under each figure.
// Both follow the top bar's branch; the top bar's period drives บาท always and จำนวนจาน when its picker reads ตามแถบด้านบน (a period change
// on the top bar sets it so). A row of one ingredient opens the sheet (now / prev / ly and the menus).
let _hvm = { slug: 'salmon', sort: 'chg', dir: -1, d: null, seq: 0, key: '', pk: '', view: 'baht', more: false, full: false, fq: '' };
const HVM_FLOOR = 1000;       // a branch row whose change is under ฿1,000 either way folds behind ดูทั้งหมด
const HVP_FLOOR = 20;         // the plates view: a branch whose plates from eating moved under 20 either way folds
const HVM_CAUSE = { customers: 'ลูกค้า', plates_per_head: 'กินต่อหัว', cost_per_plate: 'ต้นทุน/จาน' };
const HVM_CAUSE_SHORT = { customers: 'ลูกค้า', plates_per_head: 'กิน', cost_per_plate: 'ราคา' };
const HVM_PARTS = ['cost_per_plate', 'plates_per_head', 'customers'];        // ราคา · กิน · ลูกค้า: the order of the answer line
try { const s = localStorage.getItem('oshinei_hvm_slug'); if (s) _hvm.slug = s; } catch (e) { /* no storage */ }
try { const v = localStorage.getItem('oshinei_hvm_view'); if (v === 'plates' || v === 'baht') _hvm.view = v; } catch (e) { /* no storage */ }

const _hvmB = v => v == null || !isFinite(Number(v)) ? '—'
  : '฿' + Number(v).toLocaleString('th-TH', { minimumFractionDigits: Math.abs(v) < 100 ? 1 : 0, maximumFractionDigits: Math.abs(v) < 100 ? 1 : 0 });
const _hvmN1 = v => v == null || !isFinite(Number(v)) ? '—' : Number(v).toLocaleString('th-TH', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const _hvmPct = v => v == null || !isFinite(Number(v)) ? '—' : Number(v).toFixed(1) + '%';
const _hvmSigned = v => v == null || !isFinite(Number(v)) ? '—' : (v > 0 ? '+' : v < 0 ? '−' : '') + '฿' + Math.abs(Math.round(v)).toLocaleString('th-TH');
// a compact signed baht: +฿1.28M · −฿426.9K · ±฿0
const _hvmK = v => v == null || !isFinite(Number(v)) ? '—' : Math.round(v) === 0 ? '±฿0' : dvBaht(v);
// cost up = bad ink, down = good; the customers part is neither (more customers is not a fault)
const _hvmInk = (v, neutral) => v == null || !v || neutral ? 'var(--text-secondary)' : v > 0 ? 'var(--bad-ink)' : 'var(--good-ink)';

// may this role see the baht view? (the page is open to the plates roles too: they get จำนวนจาน only, no cost request)
function hvmCostView() { return canSeeCost(); }
function _hvmView() { return hvmCostView() ? _hvm.view : 'plates'; }

async function loadHvMonitor(force) {
  const q = gPeriodQuery();
  // the period part of the top bar (not the branch): a change of it sets the plates view to follow the bar
  const pk = q.split('&').filter(x => !/^(branch|group)=/.test(x)).join('&');
  if (_hvm.pk && pk !== _hvm.pk && _hvpW.a) { _hvpW = { a: '', b: '' }; _hvpSaveW(); }
  // a phone's full list (ดูทั้งหมด) stays open across the chips, the views and a revisit; a new period or branch starts short again
  if (_hvm.fq && q !== _hvm.fq) _hvm.full = false;
  _hvm.pk = pk; _hvm.fq = q;
  hvmRenderHead();
  if (_hvmView() === 'plates') { hvpLoad(force); return; }
  if (!force && _hvm.key === q && _hvm.d) { hvmRender(); return; }
  _hvm.key = q;
  const seq = ++_hvm.seq;
  const body = document.getElementById('hvmBody');
  if (body && !_hvm.d) body.innerHTML = '<p class="hvm-empty">กำลังโหลด…</p>';
  let d;
  try { d = await api(`${API}/api/cost/ingredient-groups?${q}`); } catch (e) { return; }
  if (seq !== _hvm.seq) return;
  _hvm.d = d;
  _hvm.more = !!_hvm.full;
  if (_hvmView() !== 'baht') return;
  periodChip(periodOf(d), true);
  hvmRender();
}

function hvmSetGroup(slug) {
  _hvm.slug = slug;
  try { localStorage.setItem('oshinei_hvm_slug', slug); } catch (e) { /* no storage */ }
  _hvm.sort = 'chg'; _hvm.dir = -1; _hvm.more = !!_hvm.full;    // a phone's full list (ดูทั้งหมด) stays open across the chips and the views
  if (_hvmView() === 'plates') hvpRender(); else hvmRender();
}
function hvmSetView(v) {
  if (!hvmCostView() || (v !== 'baht' && v !== 'plates') || v === _hvm.view) return;
  _hvm.view = v; _hvm.more = !!_hvm.full;
  try { localStorage.setItem('oshinei_hvm_view', v); } catch (e) { /* no storage */ }
  loadHvMonitor();
}
function hvmSort(k) {
  if (_hvm.sort === k) _hvm.dir = -_hvm.dir; else { _hvm.sort = k; _hvm.dir = k === 'name' ? 1 : -1; }
  hvmRender();
}
function hvmMore() { _hvm.more = !_hvm.more; if (_hvmView() === 'plates') hvpRender(); else hvmRender(); }

// ── a phone (both modes; owner 2026-10-26 "หน้านี้ค้าง ทำให้ดูได้ในหน้าเดียวได้ไหม"): no 28-row x 6-column table on the first screen. The page is
// the answer strip and, per ingredient, ONE compact list: the chain row, the 5 largest increases and the 5 largest decreases of the view's
// figure (บาท: the change in baht; จำนวนจาน: the change in plates), one line each - สาขา · the figure · the main cause (ราคา / กิน / ลูกค้า).
// A row opens its sheet (every column); "ดูทั้งหมด (N)" opens the full list (cards in แตะง่าย, the table in พอดีจอ, every row). ทั้งหมด = one
// line per ingredient (tap = that ingredient). The full table laid out three times per fit (natural width, scaled, the table box) and, wider
// than the frame, scrolled inside a nested box: the freeze. The list is marked ph-tz-unit, so พอดีจอ draws it at the touch size.
const HVC_SIDE = 5;
function _hvmPhone() { return typeof phOn === 'function' && phOn(); }
function _hvmCompactOn() { return _hvmPhone() && !_hvm.full; }
function hvmFull(on) {
  _hvm.full = !!on; _hvm.more = !!on;
  if (_hvmView() === 'plates') hvpRender(); else hvmRender();
  // scrolled to once the frame has fitted the new rows (its re-fit runs 250 ms after a change): scrolling now would lay the whole table
  // out once more at the old width (172 ms of 4x CPU)
  setTimeout(() => { const el = document.getElementById(on ? 'hvmBody' : 'hvmCompact');
    if (el) try { el.scrollIntoView({ block: 'start' }); } catch (e) { /* off screen */ } }, document.body.classList.contains('ph-fit') ? 400 : 0);
}
const _hvmBackBtn = () => _hvmPhone() && _hvm.full ? '<button type="button" class="hvm-more hvc-back ph-tz-unit" onclick="hvmFull(false)">ย่อรายการ</button>' : '';
function _hvcRow(r) {
  const lab = `${r.name} ${r.fig}${r.word ? ' · ' + r.word : ''}`;
  return `<button type="button" class="hvc-r${r.chain ? ' hvc-chain' : ''}" role="listitem" onclick="${r.on}" aria-label="${esc(lab)}"><span class="hvc-n">${esc(r.name)}</span>`
    + `<b class="hvc-v" style="color:${r.ink}">${esc(r.fig)}</b><span class="hvc-w">${esc(r.word || '')}</span><span class="hvc-go" aria-hidden="true">›</span></button>`;
}
// head = [the row's name, the figure]; chain = the top row (or null); items = the rest ({v} = the sort figure); split = the 5 up + 5 down
function _hvcHtml(head, chain, items, split, n) {
  let h = `<div id="hvmCompact" class="hvc ph-tz-unit" role="list"><div class="hvc-h" aria-hidden="true"><span>${head[0]}</span><span>${head[1]}</span><span>สาเหตุ</span></div>`;
  if (chain) h += _hvcRow(chain);
  if (split) {
    const up = items.filter(x => x.v > 0).sort((a, b) => b.v - a.v).slice(0, HVC_SIDE), dn = items.filter(x => x.v < 0).sort((a, b) => a.v - b.v).slice(0, HVC_SIDE);
    if (up.length) h += '<div class="hvc-sec">เพิ่มขึ้นมากสุด</div>' + up.map(_hvcRow).join('');
    if (dn.length) h += '<div class="hvc-sec">ลดลงมากสุด</div>' + dn.map(_hvcRow).join('');
  } else h += items.map(_hvcRow).join('');
  return h + `</div><button type="button" class="hvm-more hvc-all ph-tz-unit" onclick="hvmFull(true)">ดูทั้งหมด (${n})</button>`;
}
// บาท: the change in baht and its main cause
function hvmCompactHtml(d, slug) {
  const mk = (row, i, chain) => { const c = _hvmCauseOf(row.cells[slug]);
    return { name: chain && i < 0 ? 'ทั้งเครือ' : row.branch, v: c.total, fig: c.total == null ? '—' : _hvmK(c.total), word: c.main ? HVM_CAUSE_SHORT[c.main] : '',
      ink: _hvmInk(c.total), on: `hvmOpenRow(${i})`, chain }; };
  if (slug === 'all') {
    const items = _hvmRankGroups(d).map(s => { const c = _hvmCauseOf(d.chain.cells[s]);
      return { name: _hvmLabel(s), v: c.total, fig: _hvmK(c.total), word: c.main ? HVM_CAUSE_SHORT[c.main] : '', ink: _hvmInk(c.total), on: `hvmSetGroup('${esc(s)}')` }; });
    return _hvcHtml(['วัตถุดิบ', 'เปลี่ยน (฿)'], null, items, false, _hvmGroups().length);
  }
  if (_hvmOneBranch(d)) return _hvcHtml(['สาขา', 'เปลี่ยน (฿)'], mk(d.branches[0], 0, true), [], false, 1);
  return _hvcHtml(['สาขา', 'เปลี่ยน (฿)'], mk(d.chain, -1, true), d.branches.map((b, i) => mk(b, i, false)).filter(x => x.v != null), true, d.branches.length);
}
// จำนวนจาน: the change in plates and its main cause (more plates from eating = bad ink; from customers = no ink)
const _hvcPl = v => v == null || !isFinite(Number(v)) ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : '±'}${fmtN(Math.abs(v))} จาน`;
const _hvcPlInk = k => !k || !k.total || k.main === 'customers' ? 'var(--text-primary)' : k.total > 0 ? 'var(--bad-ink)' : 'var(--good-ink)';
function hvpCompactHtml(d, slug) {
  const groups = d.groups || HVP_GROUPS;
  const mk = (row, i, chain) => { const k = ((row.cells || {})[slug] || {}).cause || {};
    return { name: chain && i < 0 ? 'ทั้งเครือ' : row.branch, v: k.total, fig: _hvcPl(k.total), word: k.main ? _HVP_SHORT[k.main] : '', ink: _hvcPlInk(k), on: `hvpOpenRow(${i})`, chain }; };
  if (slug === 'all') {
    const row = _hvpOne(d) ? d.branches[0] : d.chain, tot = s => (((row.cells || {})[s] || {}).cause || {}).total;
    const items = groups.map(g => g.slug).filter(s => tot(s) != null)
      .sort((a, b) => { const x = tot(a), y = tot(b); return (y > 0 ? 1 : 0) - (x > 0 ? 1 : 0) || Math.abs(y) - Math.abs(x); })
      .map(s => { const k = row.cells[s].cause; return { name: _hvmLabel(s), v: k.total, fig: _hvcPl(k.total), word: k.main ? _HVP_SHORT[k.main] : '', ink: _hvcPlInk(k), on: `hvmSetGroup('${esc(s)}')` }; });
    return _hvcHtml(['วัตถุดิบ', 'จานที่เปลี่ยน'], null, items, false, groups.length);
  }
  if (_hvpOne(d)) return _hvcHtml(['สาขา', 'จานที่เปลี่ยน'], mk(d.branches[0], 0, true), [], false, 1);
  return _hvcHtml(['สาขา', 'จานที่เปลี่ยน'], mk(d.chain, -1, true), d.branches.map((b, i) => mk(b, i, false)).filter(x => x.v != null), true, d.branches.length);
}

function _hvmGroups() { return (_hvm.d && _hvm.d.groups) || (_hvp.d && _hvp.d.groups) || HVP_GROUPS; }
function _hvmLabel(slug) { const g = _hvmGroups().find(x => x.slug === slug); return g ? g.label : slug; }
function _hvmRange() { return periodOf(_hvm.d); }
// the compared windows of a change: '1–19 ต.ค. 2569 เทียบ 1–19 ก.ย. 2569' (+ why there is no base)
function _hvmVs() {
  const r = _hvmRange(); if (!r) return '';
  const why = (r.reasons || {}).prev;
  return `${r.label_th} เทียบ ${r.prev_label_th}${why ? ' · ' + why : ''}`;
}
const _hvmDelta = v => periodPctDelta(v, { upGood: false, title: _hvmVs() });
// owner 2026-10-25 · the base value after the change ("▲3.2% · เดิม ฿15.0"): the same days of the period before
const _hvmWas = (v, f) => v == null || !isFinite(Number(v)) ? '' : `<span class="hvm-b"> · เดิม ${f(v)}</span>`;

// ── the head: the title and its help follow the view; the view toggle (cost roles); the 9 chips + CSV ──
function hvmRenderHead() {
  const pl = _hvmView() === 'plates';
  const t = document.getElementById('hvmTitle');
  if (t) t.textContent = pl ? 'วัตถุดิบมูลค่าสูง · จำนวนจาน' : 'ต้นทุนวัตถุดิบหลักต่อหัว · ทุกสาขา';
  const q = document.getElementById('hvmHelpQ');
  if (q) q.setAttribute('data-help', pl ? 'hlp-hvmonitor-plates' : 'hlp-hvmonitor');
  const v = document.getElementById('hvmView');
  if (v) {
    v.hidden = !hvmCostView();
    v.innerHTML = [['baht', 'บาท'], ['plates', 'จำนวนจาน']].map(([k, l]) =>
      `<button type="button" class="gran-btn${_hvmView() === k ? ' gran-active' : ''}" data-v="${k}" aria-pressed="${_hvmView() === k}" onclick="hvmSetView('${k}')">${l}</button>`).join('');
  }
}
function hvmRenderChips() {
  const el = document.getElementById('hvmChips'); if (!el) return;
  const opts = [['all', 'ทั้งหมด']].concat(_hvmGroups().map(g => [g.slug, g.label]));
  el.innerHTML = opts.map(([k, l]) => `<button type="button" class="gran-btn${_hvm.slug === k ? ' gran-active' : ''}" data-g="${esc(k)}" aria-pressed="${_hvm.slug === k}" onclick="hvmSetGroup('${esc(k)}')">${esc(l)}</button>`).join('');
  hvChipsInView(el);
}
// a phone scrolls the chip row sideways (one row): keep the chosen chip in view
function hvChipsInView(track) {
  const sc = track && track.parentElement, on = track && track.querySelector('.gran-active');
  if (!sc || !on || sc.scrollWidth <= sc.clientWidth + 1) return;
  const l = /** @type {HTMLElement} */ (on).offsetLeft - track.offsetLeft;
  if (l < sc.scrollLeft || l + /** @type {HTMLElement} */ (on).offsetWidth > sc.scrollLeft + sc.clientWidth) sc.scrollLeft = Math.max(0, l - 8);
}
function _hvmAfterDraw() { if (typeof phCardsSchedule === 'function') phCardsSchedule(); }

function hvmRender() {
  const d = _hvm.d, body = document.getElementById('hvmBody');
  if (!body || !d || _hvmView() !== 'baht') return;
  if (!_hvmGroups().some(g => g.slug === _hvm.slug) && _hvm.slug !== 'all') _hvm.slug = 'salmon';
  hvmRenderHead();
  hvmRenderChips();
  const wk = document.getElementById('hvpWeeks'); if (wk) wk.innerHTML = '';
  const r = _hvmRange();
  const q = document.getElementById('hvmRangeQ');
  if (q) q.innerHTML = r ? periodQ(r, '%') : '';
  const ans = document.getElementById('hvmAnswer');
  if (!d.chain || !(d.branches || []).length) {
    const why = (d.reasons || {}).cur;
    if (ans) ans.innerHTML = '';
    body.innerHTML = `<p class="hvm-empty">${esc(why || 'ไม่มีข้อมูลในช่วงที่เลือก')}</p>`;
    pageAnswer('hvmonitor', []);
    return;
  }
  if (ans) ans.innerHTML = hvmAnswerHtml(d);
  body.innerHTML = _hvmCompactOn() ? hvmCompactHtml(d, _hvm.slug) : _hvmBackBtn() + (_hvm.slug === 'all' ? hvmAllHtml(d) : hvmTableHtml(d, _hvm.slug));
  pageAnswer('hvmonitor', hvmAnswerLines(d));
  _hvmAfterDraw();
}

// ── the cause of one cell (vs the period before) ──
function _hvmCauseOf(cell) { return (cell && cell.cause && cell.cause.prev) || {}; }
function _hvmMainVal(cell) { const c = _hvmCauseOf(cell); return c.main ? c[c.main] : null; }
function _hvmOneBranch(d) { return (d.branches || []).length === 1; }
function _hvmScopeName(d) { return _hvmOneBranch(d) ? d.branches[0].branch : 'ทั้งเครือ'; }
// "ราคา +฿1.28M · กิน −฿426.9K · ลูกค้า −฿718.2K" (each part in its ink)
function _hvmPartsHtml(c) {
  if (HVM_PARTS.some(k => c[k] == null)) return '';
  return HVM_PARTS.map(k => `<span class="hvm-part" style="color:${_hvmInk(c[k], k === 'customers')}">${HVM_CAUSE_SHORT[k]} ${_hvmK(c[k])}</span>`).join('<span class="hvm-dot"> · </span>');
}

// ── the answer line (the first thing on the page): the ingredient, the change in baht and its three causes ──
// the ingredients ranked by the change: increases first (largest first), then the decreases (largest first)
function _hvmRankGroups(d) {
  const tot = s => _hvmCauseOf((d.chain.cells || {})[s]).total;
  return _hvmGroups().map(g => g.slug).filter(s => tot(s) != null)
    .sort((a, b) => { const x = tot(a), y = tot(b); return (y > 0 ? 1 : 0) - (x > 0 ? 1 : 0) || Math.abs(y) - Math.abs(x); });
}
// how many branches pay more per plate (>= 10 %): a price, not a branch, when most of them do
function _hvmPriceUp(d, slug) {
  if (_hvmOneBranch(d)) return '';
  const bs = d.branches.filter(b => ((b.cells[slug] || {}).change || {}).prev);
  const up = bs.filter(b => (((b.cells[slug] || {}).change.prev || {}).cost_per_plate || 0) >= 10).length;
  return up >= 3 ? `ต้นทุน/จานขึ้นตั้งแต่ 10% ที่ ${up} จาก ${bs.length} สาขา` : '';
}
function _hvmAnswerOne(d, slug, big) {
  const cell = (d.chain.cells || {})[slug] || {}, c = _hvmCauseOf(cell);
  if (c.total == null) return '';
  const scope = _hvmOneBranch(d) ? `${esc(_hvmScopeName(d))} · ` : '';
  const pct = c.total_pct != null ? ` <span class="hvm-ans-pct">(${c.total_pct > 0 ? '+' : c.total_pct < 0 ? '−' : ''}${Math.abs(c.total_pct).toFixed(1)}%)</span>` : '';
  const parts = _hvmPartsHtml(c);
  const price = c.main === 'cost_per_plate' ? _hvmPriceUp(d, slug) : '';
  const sub = [`ต้นทุน ${_hvmK(cell.cost).replace(/^\+/, '')}`, cell.share_of_food_cost != null ? `${_hvmPct(cell.share_of_food_cost)} ของต้นทุนอาหาร` : '', price]
    .filter(Boolean).join(' · ');
  return `<div class="hvm-ans-row${big ? '' : ' hvm-ans-2'}" data-slug="${esc(slug)}"><div class="hvm-ans-l"><span class="hvm-ans-k">${scope}${esc(_hvmLabel(slug))}</span> `
    + `<b class="hvm-ans-v" style="color:${_hvmInk(c.total)}">${_hvmK(c.total)}</b>${pct}${parts ? '<span class="hvm-ans-colon">:</span>' : ''}</div>`
    + (parts ? `<div class="hvm-ans-parts">${parts}</div>` : '') + `<div class="hvm-ans-s">${esc(sub)}</div></div>`;
}
function hvmAnswerHtml(d) {
  const rk = _hvmRankGroups(d);
  if (!rk.length) return '';
  const first = _hvm.slug === 'all' ? rk[0] : _hvm.slug;
  const second = _hvm.slug === 'all' ? rk[1] : null;
  const a = _hvmAnswerOne(d, first, true), b = second ? _hvmAnswerOne(d, second, false) : '';
  if (!a) return '';
  return `<div class="hvm-ans" role="status">${a}${b}<div class="hvm-ans-vs">${esc(_hvmVs())}</div></div>`;
}
// DV5 · the phone answer strip (pageAnswer, js/01-core.js): the biggest baht item, the second, the trend. On a phone the strip carries the
// answer, so the page's own answer box hides there (tabs/60-hvmonitor.html); the desktop keeps the box.
const _hvmPartsTxt = c => HVM_PARTS.filter(k => c[k] != null).map(k => `${HVM_CAUSE_SHORT[k]} ${_hvmK(c[k])}`).join(' · ');
function hvmAnswerLines(d) {
  if (!d || !d.chain) return [];
  const rk = _hvmRankGroups(d), one = _hvmOneBranch(d), out = [];
  const gl = (s, k) => { const cell = d.chain.cells[s] || {}, c = _hvmCauseOf(cell);
    return c.total == null ? null : ansLine(k, `${_hvmLabel(s)} ${_hvmK(c.total)}`, _hvmPartsTxt(c), ansTone(c.total, false), _hvm.slug === 'all' ? '#hvmCompact, #hvmAll' : '#hvmCompact, #hvmTable'); };
  if (_hvm.slug === 'all') {
    out.push(gl(rk[0], one ? `${_hvmScopeName(d)} · เปลี่ยนมากสุด` : 'ของแพงที่เปลี่ยนมากสุด'), rk[1] ? gl(rk[1], 'อันดับถัดไป') : null);
  } else {
    const s = _hvm.slug, c = _hvmCauseOf(d.chain.cells[s]);
    out.push(gl(s, one ? `${_hvmScopeName(d)} · ต้นทุนที่เปลี่ยน` : 'ทั้งเครือ · ต้นทุนที่เปลี่ยน'));
    const top = one ? null : d.branches.slice().sort((x, y) => (_hvmCauseOf(y.cells[s]).total || 0) - (_hvmCauseOf(x.cells[s]).total || 0))[0];
    const tc = top ? _hvmCauseOf(top.cells[s]) : {};
    const topV = top && tc.total > 0 ? `${top.branch} ${_hvmK(tc.total)}` : '', topS = topV && tc.main ? `${HVM_CAUSE_SHORT[tc.main]} ${_hvmK(tc[tc.main])}` : '';
    const m = (c.main === 'cost_per_plate' ? _hvmPriceUp(d, s) : '').match(/(\d+) จาก (\d+) สาขา/);
    if (m) out.push(ansLine('ต้นทุน/จานขึ้นตั้งแต่ 10%', `${m[1]} จาก ${m[2]} สาขา`, topV ? `เพิ่มมากสุด ${topV}${topS ? ' · ' + topS : ''}` : '', 'warn', '#hvmCompact, #hvmTable'));
    else if (topV) out.push(ansLine('สาขาที่เพิ่มมากสุด', topV, topS, 'bad', '#hvmCompact, #hvmTable'));
  }
  const ch = (d.chain.food_change || {}).prev, fp = d.chain.food_cost_per_head, pv = d.chain.prev_food_cost_per_head;
  if (fp != null) out.push(ansLine('ต้นทุนอาหาร/หัว', `${_hvmB(fp)}${ch != null ? ' · ' + ansPct(ch) : ''}`, pv != null ? `เทียบช่วงก่อน ${_hvmB(pv)}` : '', ch != null ? ansTone(ch, false) : ''));
  return out.filter(Boolean).slice(0, 3);
}
function hvpAnswerLines(d) {
  if (!d || !d.chain) return [];
  const one = _hvpOne(d), row = one ? d.branches[0] : d.chain;
  const eat = (r, s) => (((r.cells || {})[s] || {}).cause || {}).plates_per_head;
  const slug = _hvm.slug !== 'all' ? _hvm.slug : HVP_GROUPS.map(g => g.slug).filter(s => eat(row, s) != null).sort((a, b) => eat(row, b) - eat(row, a))[0];
  const c = (row.cells || {})[slug] || {}, k = c.cause || {};
  if (!slug || k.total == null) return [];
  const n = v => v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : '±'}${fmtN(Math.abs(v))} จาน`;
  const out = [ansLine(`${one ? row.branch + ' · ' : ''}${_hvmLabel(slug)} · จานที่ออก`, `${fmtN(c.plates)} จาน${(c.change || {}).plates != null ? ' · ' + ansPct(c.change.plates) : ''}`,
    `ลูกค้า ${n(k.customers)} · กิน ${n(k.plates_per_head)}`, ansTone(k.plates_per_head, false), _hvm.slug === 'all' ? '#hvmCompact, #hvpMatrix' : '#hvmCompact, #hvpTable')];
  if (!one) {
    const top = d.branches.slice().sort((a, b) => (eat(b, slug) || 0) - (eat(a, slug) || 0))[0], tv = top ? eat(top, slug) : null;
    if (tv > 0) { const tc = top.cells[slug] || {};
      out.push(ansLine('กินต่อหัวเพิ่มมากสุด', `${top.branch} ${n(tv)}`, `จาน/หัว ${_hvpH(tc.plates_per_head)}${tc.prev ? ` · เดิม ${_hvpH(tc.prev.plates_per_head)}` : ''}`, 'bad', '#hvmCompact, #hvpTable')); }
  }
  const h = (c.change || {}).plates_per_head;
  out.push(ansLine('จาน/หัว', `${_hvpH(c.plates_per_head)}${h != null ? ' · ' + ansPct(h) : ''}`, `${c.prev ? `เดิม ${_hvpH(c.prev.plates_per_head)} · ` : ''}${_hvpVs(d).txt}`, h != null ? ansTone(h, false) : ''));
  return out.filter(Boolean).slice(0, 3);
}

// ── ทั้งหมด: one table of the 8 ingredients in baht, sorted by the change ──
// สาขาที่ดัน: the 3 branches with the largest change of the same sign as the chain's, "+N" more
function _hvmPushers(d, slug, sign) {
  if (_hvmOneBranch(d) || !sign) return '';
  const bs = d.branches.map(b => ({ n: b.branch, v: _hvmCauseOf(b.cells[slug]).total })).filter(x => x.v != null && Math.sign(x.v) === sign)
    .sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
  if (!bs.length) return '';
  return `<div class="hvm-push">${sign > 0 ? 'สาขาที่ดัน' : 'สาขาที่ลด'}: ${bs.slice(0, 3).map(x => `${esc(x.n)} ${_hvmK(x.v)}`).join(' · ')}${bs.length > 3 ? ` · +${bs.length - 3}` : ''}</div>`;
}
function hvmAllHtml(d) {
  const ch = d.chain, slugs = _hvmRankGroups(d).concat(_hvmGroups().map(g => g.slug).filter(s => !_hvmRankGroups(d).includes(s)));
  const rows = slugs.map(s => {
    const cell = ch.cells[s] || {}, c = _hvmCauseOf(cell), p = cell.prev || {};
    const main = c.main ? `<div class="hvm-v" data-ph-line="" style="color:${_hvmInk(c[c.main], c.main === 'customers')}">${HVM_CAUSE_SHORT[c.main]} ${_hvmK(c[c.main])}</div>`
      + `<div class="hvm-d" data-ph-line=""><span style="color:var(--text-secondary)">${HVM_PARTS.filter(k => k !== c.main && c[k] != null).map(k => `${HVM_CAUSE_SHORT[k]} ${_hvmK(c[k])}`).join(' · ')}</span></div>`
      : `<div class="hvm-v" data-ph-line=""><span class="hvm-muted" title="${esc(c.reason || _hvmVs())}">—</span></div>`;
    return `<tr onclick="hvmSetGroup('${esc(s)}')" data-slug="${esc(s)}">
      <td class="hvm-name"><div>${esc(_hvmLabel(s))}</div>${_hvmPushers(d, s, Math.sign(c.total || 0))}</td>
      <td class="hvm-num"><div class="hvm-v" data-ph-line="" style="color:${_hvmInk(c.total)}">${_hvmK(c.total)}</div><div class="hvm-d" data-ph-line="">${_hvmDelta((cell.change && cell.change.prev || {}).cost_per_head)}<span class="hvm-unit"> ต่อหัว</span></div></td>
      <td class="hvm-num hvm-main">${main}</td>
      <td class="hvm-num"><div class="hvm-v" data-ph-line="">${_hvmK(cell.cost).replace(/^\+/, '')}</div><div class="hvm-d" data-ph-line="">${_hvmWas(p.cost != null ? p.cost : null, v => _hvmK(v).replace(/^\+/, ''))}</div></td>
      <td class="hvm-num"><div class="hvm-v" data-ph-line="">${_hvmPct(cell.share_of_food_cost)}</div><div class="hvm-d" data-ph-line="">${_hvmWas(p.share_of_food_cost, _hvmPct)}</div></td>
    </tr>`;
  }).join('');
  return `<div class="hvm-wrap"><table id="hvmAll" class="hvm-tbl wt"><thead><tr><th class="hvm-name">วัตถุดิบ</th><th class="hvm-num">เปลี่ยน</th>
      <th class="hvm-num">สาเหตุหลัก</th><th class="hvm-num">ต้นทุน</th><th class="hvm-num">สัดส่วนในต้นทุนอาหาร</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

// ── one ingredient: the branches sorted by the change in baht, the cause in column 2 ──
function _hvmSortVal(row, slug, k) {
  const c = row.cells[slug] || {};
  if (k === 'name') return row.branch || '';
  if (k === 'cph') return c.cost_per_head;
  if (k === 'pph') return c.plates_per_head;
  if (k === 'cpp') return c.cost_per_plate;
  if (k === 'share') return c.share_of_food_cost;
  if (k === 'cause') return _hvmMainVal(c);
  return _hvmCauseOf(c).total;
}
function _hvmSorted(rows, valOf, dir) {
  dir = dir || _hvm.dir;
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
// the full cause (the sheet): สาเหตุหลัก, the total and the three parts
function hvmCauseHtml(cell) {
  const c = _hvmCauseOf(cell);
  if (c.total == null) return `<span class="hvm-muted" title="${esc(c.reason || _hvmVs())}">—</span>`;
  const keys = ['customers', 'plates_per_head', 'cost_per_plate'];
  const hasParts = keys.every(k => c[k] != null);
  const maxPos = hasParts ? keys.filter(k => c[k] > 0).sort((a, b) => c[b] - c[a])[0] : null;
  const ink = c.total > 0 ? 'var(--bad-ink)' : c.total < 0 ? 'var(--good-ink)' : 'var(--text-secondary)';
  const arrow = c.total > 0 ? '▲' : c.total < 0 ? '▼' : '';
  let h = c.main ? `<div class="hvm-cause-main">สาเหตุหลัก: ${HVM_CAUSE[c.main]}</div>` : '';
  h += `<div class="hvm-cause-tot" style="color:${ink}" title="${esc(_hvmVs())}">${arrow} ${_hvmSigned(c.total).replace(/^[+−]/, '')}${c.total_pct != null ? ` (${Math.abs(c.total_pct).toFixed(1)}%)` : ''}</div>`;
  if (hasParts) h += keys.map(k => `<div class="hvm-cause-part${k === maxPos ? ' hvm-cause-up' : ''}">${HVM_CAUSE[k]} ${_hvmSigned(c[k])}</div>`).join('');
  else if (c.reason) h += `<div class="hvm-cause-part hvm-muted">${esc(c.reason)}</div>`;
  return h;
}
// column 2: the change in baht, then the main cause ("ราคา +฿43.9K")
function _hvmChgCell(cell) {
  const c = _hvmCauseOf(cell);
  if (c.total == null) return `<div class="hvm-v" data-ph-line=""><span class="hvm-muted" title="${esc(c.reason || _hvmVs())}">—</span></div>`;
  const tip = HVM_PARTS.filter(k => c[k] != null).map(k => `${HVM_CAUSE[k]} ${_hvmSigned(c[k])}`).join(' · ');
  return `<div class="hvm-v" data-ph-line="" style="color:${_hvmInk(c.total)}" title="${esc(_hvmVs() + (tip ? ' · ' + tip : ''))}">${_hvmK(c.total)}</div>`
    + (c.main ? `<div class="hvm-d" data-ph-line=""><span style="color:${_hvmInk(c[c.main], c.main === 'customers')}">${HVM_CAUSE_SHORT[c.main]} ${_hvmK(c[c.main])}</span></div>` : '');
}
// สะสม: the running share of the increases, top down (the reader stops where it reaches ~80 %)
function _hvmCum(rows, valOf) {
  const pos = rows.map(valOf).filter(v => v != null && v > 0), sum = pos.reduce((a, b) => a + b, 0);
  let run = 0;
  return rows.map(r => { const v = valOf(r); if (v == null || v <= 0 || !sum) return null; run += v; return run / sum * 100; });
}
const _hvmCumTd = v => `<td class="hvm-num"><div class="hvm-v hvm-cum" data-ph-line="">${v == null ? '<span class="hvm-muted">—</span>' : Math.round(v) + '%'}</div></td>`;
function _hvmMoreBtn(n) {
  if (!n || _hvmPhone()) return '';                // a phone: the compact list is the short form, ดูทั้งหมด there opens every row
  return `<button type="button" class="hvm-more" onclick="hvmMore()" aria-expanded="${_hvm.more}">${_hvm.more ? 'ซ่อนแถวที่เปลี่ยนน้อย' : `ดูทั้งหมด (${n})`}</button>`;
}
function hvmTableHtml(d, slug) {
  const one = _hvmOneBranch(d);
  const rows = _hvmSorted(d.branches, x => _hvmSortVal(x, slug, _hvm.sort));
  const cum = _hvm.sort === 'chg' && _hvm.dir < 0 ? _hvmCum(rows, r => _hvmCauseOf(r.cells[slug]).total) : rows.map(() => null);
  const small = r => { const t = _hvmCauseOf(r.cells[slug]).total; return !one && (t == null || Math.abs(t) < HVM_FLOOR); };
  const nSmall = rows.filter(small).length;
  const val = (v, f, delta, was) => `<div class="hvm-v" data-ph-line="">${f(v)}</div><div class="hvm-d" data-ph-line="">${delta}${_hvmWas(was, f)}</div>`;
  const tr = (row, isChain, i, k) => {
    const c = row.cells[slug] || {}, chg = (c.change && c.change.prev) || {}, p = c.prev || {};
    const unc = c.uncosted_plates ? ` title="จานที่ไม่มีต้นทุน ${fmtN(c.uncosted_plates)} จาน"` : '';
    const hide = !isChain && !_hvm.more && small(row) ? ' hidden' : '';
    return `<tr class="${isChain ? 'hvm-chain' : ''}"${hide} onclick="hvmOpenRow(${isChain ? -1 : i})">
      <td class="hvm-name">${isChain ? 'ทั้งเครือ' : esc(row.branch)}</td>
      <td class="hvm-num hvm-chg">${_hvmChgCell(c)}</td>
      ${isChain ? '<td class="hvm-num"><div class="hvm-v" data-ph-line=""><span class="hvm-muted">—</span></div></td>' : _hvmCumTd(cum[k])}
      <td class="hvm-num" style="color:var(--text-primary)">${val(c.cost_per_head, _hvmB, _hvmDelta(chg.cost_per_head), p.cost_per_head)}</td>
      <td class="hvm-num" style="color:var(--text-primary)">${val(c.plates_per_head, _hvmN1, _hvmDelta(chg.plates_per_head), p.plates_per_head)}</td>
      <td class="hvm-num" style="color:var(--text-primary)"${unc}>${val(c.cost_per_plate, _hvmB, _hvmDelta(chg.cost_per_plate), p.cost_per_plate)}</td>
      <td class="hvm-num"><div class="hvm-v" data-ph-line="">${_hvmPct(c.share_of_food_cost)}</div><div class="hvm-d" data-ph-line="">${_hvmWas(p.share_of_food_cost, _hvmPct)}</div></td>
    </tr>`;
  };
  const idx = new Map(d.branches.map((b, i) => [b, i]));
  const body = one ? tr(d.branches[0], false, 0, 0) : tr(d.chain, true, -1, -1) + rows.map((b, k) => tr(b, false, idx.get(b), k)).join('');
  return `<div class="hvm-wrap"><table id="hvmTable" class="hvm-tbl wt"><thead><tr>
      ${_hvmTh('name', 'สาขา', 'hvm-name')}${_hvmTh('chg', 'เปลี่ยน', 'hvm-num')}<th class="hvm-num">สะสม</th>${_hvmTh('cph', 'ต้นทุน/หัว', 'hvm-num')}${_hvmTh('pph', 'จาน/หัว', 'hvm-num')}
      ${_hvmTh('cpp', 'ต้นทุน/จาน', 'hvm-num')}${_hvmTh('share', 'สัดส่วนในต้นทุนอาหาร', 'hvm-num')}
    </tr></thead><tbody>${body}</tbody></table></div>${_hvmMoreBtn(nSmall)}`;
}

// ── the sheet of a row: every figure now / prev / ly, the cause, the menus ──
function _hvmSheet(title, html) {
  if (typeof uiHelp !== 'function') return;
  uiHelp(title, html);
  const w = document.getElementById('uiHelpDlg');
  if (w) w.classList.add('hvm-sheet');
}
function _hvmRowOf(i) { return i < 0 ? _hvm.d.chain : _hvm.d.branches[i]; }
function hvmOpenRow(i) {
  if (!_hvm.d || _hvm.slug === 'all') return;
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
  // สะสม (the table's column): the running share of the increases, branches sorted by the change in baht
  let cum = '';
  if (i >= 0 && !_hvmOneBranch(_hvm.d)) {
    const tot = b => _hvmCauseOf(b.cells[slug]).total, rs = _hvmSorted(_hvm.d.branches, tot, -1), at = rs.indexOf(row), v = at >= 0 ? _hvmCum(rs, tot)[at] : null;
    if (v != null) cum = `<div class="hvm-cause-cum">สะสม (ส่วนที่เพิ่มขึ้น) ${Math.round(v)}%</div>`;
  }
  const cause = `<div class="hvm-sh-sec"><div class="hvm-sh-h">สาเหตุที่เปลี่ยน · เทียบช่วงก่อน</div><div class="hvm-cause">${hvmCauseHtml(c)}${cum}</div></div>`;
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

// ══ จำนวนจาน (DV4: moved here from the menu-spike card of ยอดขายรายวัน) ══════════════════════════════════════════════════════════
// /api/sales/ingredient-groups-plates: plates only (no cost key). ตามแถบด้านบน = the top bar's period (kind / pick); a week pair =
// period=range + base_from / base_to (Mon–Sun weeks; a week still running compares the SAME weekdays of the other one, to date).
// The first visit opens on the last week vs the week before (the review: one day swings too much); the device remembers the choice.
const _HVP_WLS = 'oshinei_hvp_weeks';
const _HVP_WD = ['จ.', 'อ.', 'พ.', 'พฤ.', 'ศ.', 'ส.', 'อา.'];
const _HVP_MON = ['ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.', 'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.'];
const _HVP_CAUSE = { customers: 'ลูกค้า', plates_per_head: 'กินต่อหัว' };
const _HVP_SHORT = { customers: 'ลูกค้า', plates_per_head: 'กิน' };
// the 8 groups of backend/ingredient_groups.py GROUPS, until the first answer names them
const HVP_GROUPS = [['salmon', 'แซลมอน'], ['tuna', 'ทูน่า'], ['hamachi', 'ฮามาจิ'], ['amaebi', 'กุ้งหวาน'], ['scallop', 'หอยเชลล์'], ['eel', 'ปลาไหล'], ['oyster', 'หอยนางรม'], ['beef', 'เนื้อ']].map(([slug, label]) => ({ slug, label }));
let _hvp = { d: null, key: '', seq: 0, err: '', weeks: [], latest: '', auto: false };
// the week pair: a = the Monday of the chosen week, b = the Monday of the week it is compared with; a = '' -> ตามแถบด้านบน
let _hvpW = { a: '', b: '' };
const _hvpD = iso => new Date(iso + 'T00:00:00Z');
const _hvpIso = d => d.toISOString().slice(0, 10);
const _hvpAdd = (iso, n) => { const d = _hvpD(iso); d.setUTCDate(d.getUTCDate() + n); return _hvpIso(d); };
const _hvpMonday = iso => _hvpAdd(iso, -((_hvpD(iso).getUTCDay() + 6) % 7));
// "13–19 ต.ค." · "29 ก.ย.–5 ต.ค."
function _hvpSpan(a, b) {
  const x = _hvpD(a), y = _hvpD(b || a), m = d => _HVP_MON[d.getUTCMonth()];
  if (a === (b || a)) return `${x.getUTCDate()} ${m(x)}`;
  return x.getUTCMonth() === y.getUTCMonth() ? `${x.getUTCDate()}–${y.getUTCDate()} ${m(y)}` : `${x.getUTCDate()} ${m(x)}–${y.getUTCDate()} ${m(y)}`;
}
// ISO week number (W1 = the week with the year's first Thursday; Monday–Sunday) - the backend's periods.iso_weeks
function _hvpIsoWeek(iso) {
  const d = _hvpD(iso), day = (d.getUTCDay() + 6) % 7;
  d.setUTCDate(d.getUTCDate() - day + 3);
  const y = d.getUTCFullYear(), j4 = new Date(Date.UTC(y, 0, 4));
  return { y, w: 1 + Math.round(((d.getTime() - j4.getTime()) / 864e5 - 3 + (j4.getUTCDay() + 6) % 7) / 7) };
}
function _hvpLatest() { return _hvp.latest || gPeriodAnchor(); }
function _hvpSaveW() { try { localStorage.setItem(_HVP_WLS, JSON.stringify(_hvpW.a ? _hvpW : { follow: 1 })); } catch (e) { /* no storage */ } }
try {
  const w = JSON.parse(localStorage.getItem(_HVP_WLS) || 'null'), ok = x => typeof x === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(x);
  if (w && ok(w.a) && ok(w.b) && w.a !== w.b) _hvpW = { a: w.a, b: w.b };
  else if (!w || !w.follow) _hvpW = { a: '*', b: '' };             // nothing chosen yet: the last week vs the week before (set on first load)
} catch (e) { /* no storage */ }
// the default pair: the last finished Mon–Sun week vs the week before (a week still running would compare a day or two)
function _hvpDefault() {
  if (_hvpW.a !== '*') return;
  const la = _hvpLatest();
  if (!la) return;
  const m = _hvpMonday(la), a = _hvpAdd(m, 6) === la ? m : _hvpAdd(m, -7);
  _hvpW = { a, b: _hvpAdd(a, -7) };
  _hvp.auto = true;                                                  // worked out, not chosen: the server's last closed day may move it
}
function _hvpPair() {
  _hvpDefault();
  const { a, b } = _hvpW, la = _hvpLatest();
  if (!a || !b || a === b || !la) return null;
  const days = m => Math.min(7, Math.round((_hvpD(la).getTime() - _hvpD(m).getTime()) / 864e5) + 1);
  const n = Math.min(days(a), days(b));
  if (n < 1) return null;
  return { f: a, t: _hvpAdd(a, n - 1), bf: b, bt: _hvpAdd(b, n - 1), n, toDate: n < 7, wa: _hvpIsoWeek(a).w, wb: _hvpIsoWeek(b).w, after: b > a };
}
// "W43 (จ.–ศ.) เทียบ W42 (จ.–ศ.)" (to date) · "W43 (19–25 ต.ค.) เทียบ W37 (7–13 ก.ย.)"
function _hvpPairLabel(pr) {
  const tag = (w, s, e) => `W${w} (${pr.toDate ? (pr.n > 1 ? `จ.–${_HVP_WD[pr.n - 1]}` : 'จ.') : _hvpSpan(s, e)})`;
  return `${tag(pr.wa, pr.f, pr.t)} เทียบ ${tag(pr.wb, pr.bf, pr.bt)}`;
}
function _hvpQuery() {
  const pr = _hvpPair();
  if (!pr) return gPeriodQuery();
  const q = new URLSearchParams();
  q.set('period', 'range'); q.set('date_from', pr.f); q.set('date_to', pr.t); q.set('base_from', pr.bf); q.set('base_to', pr.bt);
  if (getBranch()) q.set('branch', getBranch());
  if (getGroup()) q.set('group', getGroup());
  return q.toString();
}
function hvpLoad(force) {
  const q = _hvpQuery(), pr = _hvpPair();
  if (!force && q === _hvp.key && (_hvp.d || _hvp.err)) { hvpRender(); return; }
  _hvp.key = q; _hvp.d = null; _hvp.err = ''; _hvm.more = !!_hvm.full;
  const seq = ++_hvp.seq;
  hvpRender();
  api(`${API}/api/sales/ingredient-groups-plates?${q}`).then(d => {
    if (seq !== _hvp.seq) return;
    if (d && Array.isArray(d.weeks) && d.weeks.length) _hvp.weeks = d.weeks;
    const first = !_hvp.latest;
    if (d && d.latest_day) _hvp.latest = d.latest_day;
    if (first && _hvp.auto) _hvpW = { a: '*', b: '' };                // the default pair again, on the server's last closed day
    // the first answer names the last closed day: a pair worked out from the client's guess is asked again on the right days
    const np = first && _hvpW.a ? _hvpPair() : null;
    if (np && (!pr || np.t !== pr.t || np.bt !== pr.bt)) { hvpLoad(); return; }
    if (!d || typeof d.detail === 'string') _hvp.err = (d && d.detail) || 'โหลดไม่สำเร็จ'; else _hvp.d = d;
    if (_hvp.d && !pr) periodChip(periodOf(_hvp.d), true);
    hvpRender();
  }).catch(() => { if (seq === _hvp.seq) { _hvp.err = 'โหลดไม่สำเร็จ'; hvpRender(); } });
}
// the week before the chosen one (else the one after) as its first base
function _hvpDefB(a) {
  const ws = _hvp.weeks || [], i = ws.findIndex(w => w.from === a);
  if (i >= 0 && ws[i + 1]) return ws[i + 1].from;
  if (i > 0) return ws[i - 1].from;
  return _hvpAdd(a, -7);
}
function hvpWeek(which, v) {
  // a new week compares with the week before it (then any other); ตามแถบด้านบน drops the pair
  if (which === 'a') _hvpW = v ? { a: v, b: _hvpDefB(v) } : { a: '', b: '' };
  else if (v && v !== _hvpW.a) _hvpW.b = v;
  _hvp.auto = false;
  _hvpSaveW();
  hvpLoad();
}
// the weeks the select offers: the server's list (Mon–Sun, ISO numbers), else 16 worked out from the last closed day
function _hvpWeeks() {
  if (_hvp.weeks && _hvp.weeks.length) return _hvp.weeks.slice();
  const la = _hvpLatest(); if (!la) return [];
  let m = _hvpMonday(la);
  const out = [];
  for (let i = 0; i < 16; i++, m = _hvpAdd(m, -7)) {
    const t = _hvpAdd(m, 6), ct = t > la ? la : t;
    out.push({ from: m, label: `W${_hvpIsoWeek(m).w} · ${_hvpSpan(m, ct)}${ct !== t ? ' (ถึงวันที่ปิด)' : ''}` });
  }
  return out;
}
// สัปดาห์ [ตามแถบด้านบน | W43 · 19–25 ต.ค. ▾] เทียบ [W42 · 12–18 ต.ค. ▾]
function _hvpWeekCtl() {
  _hvpDefault();
  const ws = _hvpWeeks(), { a, b } = _hvpW;
  [a, b].forEach(m => { if (m && !ws.some(w => w.from === m)) ws.push({ from: m, label: `W${_hvpIsoWeek(m).w} · ${_hvpSpan(m, _hvpAdd(m, 6))}` }); });
  ws.sort((x, y) => y.from.localeCompare(x.from));
  const opt = (w, sel, dis) => `<option value="${esc(w.from)}"${sel ? ' selected' : ''}${dis ? ' disabled' : ''}>${esc(w.label)}</option>`;
  const sa = `<select id="hvpWa" class="hvp-sel" aria-label="สัปดาห์" onchange="hvpWeek('a', this.value)"><option value=""${a ? '' : ' selected'}>ตามแถบด้านบน</option>`
    + ws.map(w => opt(w, w.from === a, false)).join('') + '</select>';
  const sb = a ? `<label class="hvp-wg"><span class="hvp-wl">เทียบ</span><select id="hvpWb" class="hvp-sel" aria-label="เทียบกับสัปดาห์" onchange="hvpWeek('b', this.value)">`
    + ws.map(w => opt(w, w.from === b, w.from === a)).join('') + '</select></label>' : '';
  return `<div class="hvp-wk ph-tz-unit"><label class="hvp-wg"><span class="hvp-wl">สัปดาห์</span>${sa}</label>${sb}</div>`;
}
const _hvpH = v => v == null || !isFinite(Number(v)) ? '—' : Number(v).toLocaleString('th-TH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const _hvpS = v => v == null || !isFinite(Number(v)) ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : '±'}${fmtN(Math.abs(v))} จาน`;
// one change under a figure, then the base value ("▲0.9% · เดิม 193,477"; เทียบ when the base week is the later one):
// up = 'good' (customers) | 'bad' (plates per head of a costly ingredient) | '' (plates: neither). Two parts = two children (the phone card's figure / sub).
function _hvpDelta(v, up, title, was, word) {
  const t = title ? ` title="${esc(title)}"` : '';
  const b = was == null ? '' : `<span class="hvm-b"> · ${word || 'เดิม'} ${was}</span>`;
  if (v == null || !isFinite(Number(v))) return `<div class="hvm-d" data-ph-line=""${t}><span style="color:var(--text-muted)">—</span>${b}</div>`;
  v = Number(v);
  const ink = v === 0 || !up ? 'var(--text-secondary)' : (v > 0) === (up === 'good') ? 'var(--good-ink)' : 'var(--bad-ink)';
  return `<div class="hvm-d" data-ph-line=""${t}><span style="color:${ink}">${v > 0 ? '▲' : v < 0 ? '▼' : ''}${Math.abs(v).toFixed(1)}%</span>${b}</div>`;
}
function _hvpVs(d) {
  const per = d && d.period, base = d && d.base, pr = _hvpPair();
  const full = per && base ? `${per.label_th} เทียบ ${base.label_th}` : '';
  const why = per && per.reasons && per.reasons.prev ? ` · ${per.reasons.prev}` : '';
  return { txt: (pr ? _hvpPairLabel(pr) : full) + why, full };
}
// the plates answer line: "แซลมอน 27,363 จาน ▼14.1%: ลูกค้า −3,900 จาน · กิน −40 จาน"
function _hvpAnswerHtml(d, slug) {
  const row = _hvpOne(d) ? d.branches[0] : d.chain;
  const pick = slug === 'all' ? HVP_GROUPS.map(g => g.slug).filter(s => ((row.cells[s] || {}).cause || {}).plates_per_head != null)
    .sort((a, b) => (row.cells[b].cause.plates_per_head || 0) - (row.cells[a].cause.plates_per_head || 0))[0] : slug;
  const c = (row.cells || {})[pick] || {}, k = c.cause || {};
  if (k.total == null) return '';
  const scope = _hvpOne(d) ? `${esc(row.branch)} · ` : '';
  const pct = (c.change || {}).plates;
  const parts = ['customers', 'plates_per_head'].filter(x => k[x] != null)
    .map(x => `<span class="hvm-part" style="color:${x === 'customers' || !k[x] ? 'var(--text-secondary)' : k[x] > 0 ? 'var(--bad-ink)' : 'var(--good-ink)'}">${_HVP_SHORT[x]} ${_hvpS(k[x])}</span>`).join('<span class="hvm-dot"> · </span>');
  const sub = `จาน/หัว ${_hvpH(c.plates_per_head)}${c.prev ? ` · เดิม ${_hvpH(c.prev.plates_per_head)}` : ''} · ลูกค้า ${fmtN(row.customers)}`;
  return `<div class="hvm-ans" role="status"><div class="hvm-ans-row" data-slug="${esc(pick)}"><div class="hvm-ans-l"><span class="hvm-ans-k">${scope}${esc(_hvmLabel(pick))}</span> `
    + `<b class="hvm-ans-v">${fmtN(c.plates)} จาน</b>${pct != null ? ` <span class="hvm-ans-pct">(${pct > 0 ? '▲' : pct < 0 ? '▼' : ''}${Math.abs(pct).toFixed(1)}%)</span>` : ''}${parts ? '<span class="hvm-ans-colon">:</span>' : ''}</div>`
    + (parts ? `<div class="hvm-ans-parts">${parts}</div>` : '') + `<div class="hvm-ans-s">${esc(sub)}</div></div>`
    + `<div class="hvm-ans-vs" id="hvpVs"${_hvpVs(d).full !== _hvpVs(d).txt ? ` title="${esc(_hvpVs(d).full)}"` : ''}>${esc(_hvpVs(d).txt)}</div></div>`;
}
function _hvpOne(d) { return (d.branches || []).length === 1; }
// the rows: the chain on top (one branch in scope: its row only), the branches by the plates from eating more
function _hvpRows(d, slug) {
  const br = (d.branches || []).slice();
  if (slug && slug !== 'all') br.sort((a, b) => (((b.cells[slug] || {}).cause || {}).plates_per_head || 0) - (((a.cells[slug] || {}).cause || {}).plates_per_head || 0));
  return br.length === 1 ? br : [Object.assign({ branch: 'ทั้งเครือ', _chain: true }, d.chain)].concat(br);
}
function _hvpCause(c) {
  const k = c && c.cause;
  if (!k || k.total == null) return `<div class="hvm-v" data-ph-line=""><span style="color:var(--text-muted)">${esc((k && k.reason) || '—')}</span></div>`;
  // the question of this view (review §3.1 ง): which branch serves more per head - line 1 = the plates from eating more or less per head
  // (the sort key; more = bad ink), line 2 = the plates from customers and the whole change; สาเหตุหลัก in the tooltip and the answer line
  const n = v => v == null || !isFinite(Number(v)) ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : '±'}${fmtN(Math.abs(v))}`;
  const v = k.plates_per_head, ink = !v ? 'var(--text-primary)' : v > 0 ? 'var(--bad-ink)' : 'var(--good-ink)';
  const pct = k.total_pct != null ? ` (${k.total_pct > 0 ? '+' : k.total_pct < 0 ? '−' : ''}${Math.abs(k.total_pct).toFixed(1)}%)` : '';
  const tip = `${k.main ? `สาเหตุหลัก: ${_HVP_CAUSE[k.main]} · ` : ''}${['plates_per_head', 'customers'].map(x => `${_HVP_CAUSE[x]} ${_hvpS(k[x])}`).join(' · ')} · รวม ${_hvpS(k.total)}${pct}`;
  return `<div class="hvm-v hvp-main" data-ph-line="" style="color:${ink}" title="${esc(tip)}">${n(v)}</div>`
    + `<div class="hvm-d" data-ph-line=""><span class="hvp-part" style="color:var(--text-secondary)">ลูกค้า ${n(k.customers)} · รวม ${n(k.total)}${pct}</span></div>`;
}
// the sheet of a plates row (the phone's compact list): every column of the table - now / the compared window / the change, the cause, สะสม
function hvpOpenRow(i) {
  const d = _hvp.d, slug = _hvm.slug;
  if (!d || !d.chain || slug === 'all') return;
  const row = i < 0 ? d.chain : d.branches[i]; if (!row) return;
  const c = (row.cells || {})[slug] || {}, p = c.prev || {}, k = c.cause || {}, ch = c.change || {}, base = d.base || {};
  const bw = d.period && d.period.cur && base.from > d.period.cur[1] ? 'ช่วงที่เทียบ' : 'ช่วงก่อน';
  const pct = v => v == null || !isFinite(Number(v)) ? '—' : `${v > 0 ? '▲' : v < 0 ? '▼' : ''}${Math.abs(v).toFixed(1)}%`;
  const fig = [['จานที่ออก', fmtN(c.plates), c.prev ? fmtN(p.plates) : '—', pct(ch.plates)],
    ['จาน/หัว', _hvpH(c.plates_per_head), c.prev ? _hvpH(p.plates_per_head) : '—', pct(ch.plates_per_head)],
    ['ลูกค้า', fmtN(row.customers), row.prev_customers == null ? '—' : fmtN(row.prev_customers), pct((row.change || {}).customers)]];
  const figs = `<table class="hvm-fig"><thead><tr><th></th><th>ช่วงนี้</th><th title="${esc(base.label_th || '')}">${bw}</th><th>เปลี่ยน</th></tr></thead><tbody>`
    + fig.map(([l, a, b, x]) => `<tr><th>${l}</th><td>${a}</td><td>${b}</td><td>${x}</td></tr>`).join('') + '</tbody></table>';
  let cum = null;
  if (i >= 0 && !_hvpOne(d)) {
    const eat = r => (((r.cells || {})[slug] || {}).cause || {}).plates_per_head;
    const rs = d.branches.slice().sort((a, b) => (eat(b) || 0) - (eat(a) || 0)), cs = _hvmCum(rs, eat), at = rs.indexOf(row);
    cum = at >= 0 ? cs[at] : null;
  }
  const cause = k.total == null ? `<p class="hvm-muted">${esc(k.reason || '—')}</p>`
    : `<div class="hvm-cause">${k.main ? `<div class="hvm-cause-main">สาเหตุหลัก: ${_HVP_CAUSE[k.main]}</div>` : ''}<div class="hvm-cause-tot">จานที่เปลี่ยน ${_hvpS(k.total)}${k.total_pct != null ? ` (${k.total_pct > 0 ? '+' : k.total_pct < 0 ? '−' : ''}${Math.abs(k.total_pct).toFixed(1)}%)` : ''}</div>`
      + ['plates_per_head', 'customers'].map(x => `<div class="hvm-cause-part">จาก${_HVP_CAUSE[x]} ${_hvpS(k[x])}</div>`).join('')
      + (cum != null ? `<div class="hvm-cause-cum">สะสม (จานส่วนเกินจากกินต่อหัว) ${Math.round(cum)}%</div>` : '') + '</div>';
  _hvmSheet(`${i < 0 ? 'ทั้งเครือ' : row.branch} · ${_hvmLabel(slug)}`,
    `<p class="hvm-sh-range">${esc(_hvpVs(d).txt)}</p>${figs}<div class="hvm-sh-sec"><div class="hvm-sh-h">สาเหตุที่เปลี่ยน · เทียบ${bw}</div>${cause}</div>`);
}
function hvpRender() {
  const body = document.getElementById('hvmBody');
  if (!body || _hvmView() !== 'plates') return;
  hvmRenderHead();
  const d = _hvp.d, groups = (d && d.groups) || HVP_GROUPS;
  if (_hvm.slug !== 'all' && !groups.some(g => g.slug === _hvm.slug)) _hvm.slug = 'salmon';
  hvmRenderChips();
  const slug = _hvm.slug;
  const wk = document.getElementById('hvpWeeks'); if (wk) wk.innerHTML = _hvpWeekCtl();
  const vs = _hvpVs(d);
  const rq = document.getElementById('hvmRangeQ'); if (rq) rq.innerHTML = '';
  const ans = document.getElementById('hvmAnswer');
  if (ans) ans.innerHTML = '';
  if (_hvp.err) { body.innerHTML = `<p class="hvm-empty" style="color:var(--bad-ink)">${esc(_hvp.err)}</p>`; return; }
  if (!d) { body.innerHTML = '<p class="hvm-empty">กำลังโหลด…</p>'; return; }
  if (!d.chain || !(d.branches || []).length) { body.innerHTML = `<p class="hvm-empty">${esc((d.reasons || {}).cur || 'ไม่มีข้อมูลในช่วงที่เลือก')}</p>`; pageAnswer('hvmonitor', []); return; }
  if (ans) ans.innerHTML = _hvpAnswerHtml(d, slug);
  pageAnswer('hvmonitor', hvpAnswerLines(d), _hvpPair() ? 'branch' : '');     // a week pair is not the top bar's period: it follows the branch only
  const rows = _hvpRows(d, slug), base = d.base || {};
  const bvs = `เทียบ ${base.label_th || ''}`;
  // the base is the later week (W vs W in any order): its value reads "เทียบ", not "เดิม"
  const bw = d.period && d.period.cur && base.from > d.period.cur[1] ? 'เทียบ' : 'เดิม';
  const nameTd = r => `<td class="hvm-name">${esc(r.branch)}</td>`;
  const custTd = r => `<td class="hvm-num" title="${esc(bvs)}"><div class="hvm-v" data-ph-line="">${fmtN(r.customers)}</div>${_hvpDelta((r.change || {}).customers, 'good', bvs, r.prev_customers == null ? null : fmtN(r.prev_customers), bw)}</td>`;
  const cls = r => r._chain ? ' class="hvm-chain"' : '';
  // the compared windows: under the answer line (its last line); the table repeats it only when there is no answer line
  const vsLine = ans && ans.innerHTML ? '' : `<p class="hvp-vs">${esc(vs.txt)}</p>`;
  if (_hvmCompactOn()) { body.innerHTML = vsLine + hvpCompactHtml(d, slug); _hvmAfterDraw(); return; }
  if (slug === 'all') {
    const word = c => c && c.cause && c.cause.main ? `<div class="hvp-cw">${_HVP_SHORT[c.cause.main]}</div>` : '';
    body.innerHTML = vsLine + _hvmBackBtn() + `<div class="hvm-wrap"><table class="hvm-tbl hvp-mx wt" id="hvpMatrix"><thead><tr>
      <th class="hvm-name hvm-stick">สาขา</th>${groups.map(g => `<th class="hvm-num">${esc(g.label)} <span class="hvp-unit">(จาน/หัว)</span></th>`).join('')}<th class="hvm-num">ลูกค้า</th></tr></thead><tbody>`
      + rows.map(r => `<tr${cls(r)}><td class="hvm-name hvm-stick">${esc(r.branch)}</td>${groups.map(g => { const c = r.cells[g.slug] || {};
        return `<td class="hvm-num" title="${esc(`${g.label} · ${fmtN(c.plates)} จาน · ${bvs}`)}"><div class="hvm-v" data-ph-line="">${_hvpH(c.plates_per_head)}</div>${_hvpDelta((c.change || {}).plates_per_head, 'bad', bvs, c.prev ? _hvpH(c.prev.plates_per_head) : null, bw)}${word(c)}</td>`; }).join('')}${custTd(r)}</tr>`).join('')
      + '</tbody></table></div>';
    _hvmAfterDraw();
    return;
  }
  const brs = rows.filter(r => !r._chain);
  const cum = _hvmCum(brs, r => ((r.cells[slug] || {}).cause || {}).plates_per_head);
  const small = r => { const v = ((r.cells[slug] || {}).cause || {}).plates_per_head; return !_hvpOne(d) && !r._chain && (v == null || Math.abs(v) < HVP_FLOOR); };
  const nSmall = brs.filter(small).length;
  let k = -1;
  body.innerHTML = vsLine + _hvmBackBtn() + `<div class="hvm-wrap"><table class="hvm-tbl wt" id="hvpTable"><thead><tr>
    <th class="hvm-name">สาขา</th><th class="hvm-num hvp-cause" title="จานที่เปลี่ยนเพราะกินต่อหัวเปลี่ยน (จำนวนลูกค้าคงที่)">จากกินต่อหัว</th><th class="hvm-num">สะสม</th><th class="hvm-num">จานที่ออก</th><th class="hvm-num">จาน/หัว</th><th class="hvm-num">ลูกค้า</th></tr></thead><tbody>`
    + rows.map(r => { const c = r.cells[slug] || {}, p = c.prev || {}; if (!r._chain) k++;
      return `<tr${cls(r)}${!_hvm.more && small(r) ? ' hidden' : ''}>${nameTd(r)}<td class="hvm-num hvp-cause">${_hvpCause(c)}</td>
        ${r._chain ? '<td class="hvm-num"><div class="hvm-v" data-ph-line=""><span class="hvm-muted">—</span></div></td>' : _hvmCumTd(cum[k])}
        <td class="hvm-num" title="${esc(bvs)}"><div class="hvm-v" data-ph-line="">${fmtN(c.plates)}</div>${_hvpDelta((c.change || {}).plates, '', bvs, c.prev ? fmtN(p.plates) : null, bw)}</td>
        <td class="hvm-num" title="${esc(bvs)}"><div class="hvm-v" data-ph-line="">${_hvpH(c.plates_per_head)}</div>${_hvpDelta((c.change || {}).plates_per_head, 'bad', bvs, c.prev ? _hvpH(p.plates_per_head) : null, bw)}</td>
        ${custTd(r)}</tr>`; }).join('')
    + `</tbody></table></div>${_hvmMoreBtn(nSmall)}`;
  _hvmAfterDraw();
}

// ── CSV of the view on screen ──
function hvmExport() {
  if (_hvmView() === 'plates') return hvpExport();
  const d = _hvm.d; if (!d || !d.chain) return;
  const q = s => `"${String(s == null ? '' : s).replace(/"/g, '""')}"`;
  const n = v => v == null ? '' : v;
  const r = _hvmRange() || {};
  const all = [{ name: 'ทั้งเครือ', row: d.chain }].concat(d.branches.map(b => ({ name: b.branch, row: b })));
  let lines;
  if (_hvm.slug === 'all') {
    // ทั้งหมด: every branch x every group (the detail behind the 8-ingredient table)
    const gs = _hvmGroups();
    lines = [['สาขา'].concat(gs.flatMap(g => [`${g.label} ต้นทุน/หัว`, `${g.label} เทียบช่วงก่อน %`, `${g.label} ต้นทุนเปลี่ยน ฿`, `${g.label} สาเหตุหลัก`]), ['ต้นทุนอาหาร/หัว', 'ต้นทุนอาหาร/หัว เทียบช่วงก่อน %']).map(q).join(',')]
      .concat(all.map(({ name, row }) => [q(name)].concat(gs.flatMap(g => { const c = row.cells[g.slug] || {}, cs = _hvmCauseOf(c);
        return [n(c.cost_per_head), n((c.change && c.change.prev || {}).cost_per_head), n(cs.total), q(cs.main ? HVM_CAUSE[cs.main] : '')]; }),
      [n(row.food_cost_per_head), n((row.food_change || {}).prev)]).join(',')));
  } else {
    const s = _hvm.slug;
    lines = [['สาขา', 'ต้นทุนเปลี่ยน ฿', 'จากลูกค้า ฿', 'จากกินต่อหัว ฿', 'จากต้นทุนต่อจาน ฿', 'สาเหตุหลัก', 'ต้นทุน/หัว', 'ต้นทุน/หัว ช่วงก่อน', 'เทียบช่วงก่อน %',
      'จาน/หัว', 'จาน/หัว ช่วงก่อน', 'เทียบช่วงก่อน %', 'ต้นทุน/จาน', 'ต้นทุน/จาน ช่วงก่อน', 'เทียบช่วงก่อน %', 'สัดส่วนในต้นทุนอาหาร %', 'ต้นทุนอาหาร/หัว', 'จาน',
      'ต้นทุนรวม', 'จานที่ไม่มีต้นทุน'].map(q).join(',')]
      .concat(all.map(({ name, row }) => { const c = row.cells[s] || {}, p = c.prev || {}, ch = (c.change && c.change.prev) || {}, cs = _hvmCauseOf(c);
        return [q(name), n(cs.total), n(cs.customers), n(cs.plates_per_head), n(cs.cost_per_plate), q(cs.main ? HVM_CAUSE[cs.main] : ''), n(c.cost_per_head), n(p.cost_per_head),
          n(ch.cost_per_head), n(c.plates_per_head), n(p.plates_per_head), n(ch.plates_per_head), n(c.cost_per_plate), n(p.cost_per_plate), n(ch.cost_per_plate),
          n(c.share_of_food_cost), n(row.food_cost_per_head), n(c.plates), n(c.cost), n(c.uncosted_plates)].join(','); }));
  }
  const tag = (r.cur || []).join('_');
  downloadCSV(lines.join('\n'), `high_value_${_hvm.slug}_${tag}.csv`);
}
function hvpExport() {
  const d = _hvp.d;
  if (!d || !d.chain || !(d.groups || []).length) { uiNotice('ไม่มีรายการให้ส่งออก'); return; }
  const q = s => `"${String(s == null ? '' : s).replace(/"/g, '""')}"`;
  const head = ['วัตถุดิบ', 'สาขา', 'จานที่ออก', 'จานที่ออก ช่วงเทียบ', 'จานที่ออก เปลี่ยน (%)', 'จาน/หัว', 'จาน/หัว ช่วงเทียบ', 'จาน/หัว เปลี่ยน (%)',
    'ลูกค้า', 'ลูกค้า ช่วงเทียบ', 'ลูกค้า เปลี่ยน (%)', 'จานที่เปลี่ยน', 'จากลูกค้า (จาน)', 'จากกินต่อหัว (จาน)', 'สาเหตุหลัก', 'ช่วงนี้', 'ช่วงเทียบ'];
  const out = [head.map(q).join(',')];
  _hvpRows(d, '').forEach(r => d.groups.forEach(g => {
    const c = r.cells[g.slug] || {}, p = c.prev || {}, k = c.cause || {}, ch = c.change || {};
    out.push([q(g.label), q(r.branch), c.plates, c.prev ? p.plates : '', ch.plates, c.plates_per_head, c.prev ? p.plates_per_head : '',
      ch.plates_per_head, r.customers, r.prev_customers, (r.change || {}).customers, k.total, k.customers, k.plates_per_head, q(k.main ? _HVP_CAUSE[k.main] : ''),
      q(d.period ? d.period.label_th : ''), q(d.base ? d.base.label_th : '')].map(v => v == null ? '' : v).join(','));
  }));
  const tag = d.period && d.period.cur ? d.period.cur.join('_') : '';
  downloadCSV(out.join('\n'), `high_value_plates_${_hvm.slug}_${tag}.csv`);
}
