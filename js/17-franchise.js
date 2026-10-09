// 17-franchise.js — ร้านของฉัน (PR-L, 2026-10-10): the franchise owner's page. One branch (the account is tied to it by
// the server), one month, read only, from GET /api/franchise/summary (backend/routers/franchise.py).
// Built phone-first on the cards of the owner's phone summary (09c-mobile.js: ms-section, ms-kpi, ms-row, msPchip,
// msColumns, msBar). Shares the global scope of slices 01-16; every name starts with `frc`.
// There is no branch picker here: the branch is text (stripBranchPickers / pinnedBranchName, 01-core.js).

let _frcMonth = '';
let _frcGen = 0;

const _FRC_MUTED = 'color:var(--text-muted)';
const _FRC_NA = `<span style="${_FRC_MUTED}">—</span>`;

function _frcBaht(v) { return v == null ? _FRC_NA : '฿' + fmtN(v); }
function _frcPct(v, d = 1) { return v == null ? _FRC_NA : Number(v).toFixed(d).replace(/\.0+$/, '') + '%'; }
function _frcDay(iso) { return iso ? msPeriodLabel(iso, 'day') : ''; }

async function loadFranchise() {
  const gen = ++_frcGen;
  const nm = document.getElementById('frcBranchName');
  if (nm) nm.textContent = pinnedBranchName() || nm.textContent;
  const st = document.getElementById('frcStatus'), body = document.getElementById('frcBody');
  if (st) st.textContent = 'กำลังโหลด…';
  const qs = new URLSearchParams();
  if (_frcMonth) qs.set('month', _frcMonth);
  let d;
  try {
    const r = await fetch(`${API}/api/franchise/summary${qs.toString() ? '?' + qs : ''}`);
    const t = await r.text();
    let j = null; try { j = JSON.parse(t); } catch (e) { /* not json */ }
    if (!r.ok) throw new Error((j && typeof j.detail === 'string' && j.detail) || 'โหลดข้อมูลไม่สำเร็จ');
    d = j;
  } catch (e) {
    if (gen !== _frcGen) return;
    if (st) st.textContent = '';
    if (body) body.innerHTML = `<div class="ms-section"><div class="ms-card"><div class="ms-row" style="color:var(--bad-ink)">${esc(e.message || 'โหลดข้อมูลไม่สำเร็จ')}</div></div></div>`;
    return;
  }
  if (gen !== _frcGen) return;
  if (nm) nm.textContent = d.branch || nm.textContent;
  _frcMonth = d.month || '';
  const sel = document.getElementById('frcMonth');
  if (sel) {
    sel.innerHTML = (d.months || []).map(m => `<option value="${esc(m.month)}">${esc(m.label || m.month)}</option>`).join('')
      || `<option value="${esc(d.month || '')}">${esc(d.month || '')}</option>`;
    sel.value = d.month || '';
  }
  if (st) st.textContent = d.label || '';
  if (body) body.innerHTML = _frcRender(d);
}

function frcMonthChanged() {
  const sel = document.getElementById('frcMonth');
  _frcMonth = sel ? sel.value : '';
  loadFranchise();
}

// ── drawing ──────────────────────────────────────────────────────────────────
function _frcSection(title, inner) {
  return `<div class="ms-section"><div class="ms-section-title">${esc(title)}</div>${inner}</div>`;
}
function _frcRow(label, value, sub) {
  return `<div class="ms-row"><span style="flex:1;min-width:0;font-size:13px;color:var(--text-primary)">${esc(label)}${sub ? `<br><span style="font-size:11px;${_FRC_MUTED}">${esc(sub)}</span>` : ''}</span>`
    + `<span style="font-size:14px;font-weight:600;text-align:right;font-variant-numeric:tabular-nums;color:var(--text-primary)">${value}</span></div>`;
}
const _FRC_TILES = 'display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px';

function _frcVerdict(v) {
  const groups = [['good', 'ดี', 'var(--good-ink)'], ['watch', 'ควรดู', 'var(--amber-ink)'], ['fix', 'ต้องแก้', 'var(--bad-ink)']];
  const rows = groups.map(([k, lbl, ink]) => {
    const items = (v && v[k]) || [];
    return `<div class="ms-row" style="align-items:flex-start;gap:10px"><span style="flex:0 0 44px;font-size:13px;font-weight:700;color:${ink}">${lbl}</span>`
      + `<span style="flex:1;min-width:0;font-size:13px;line-height:1.5;color:var(--text-primary)">`
      + (items.length ? items.map(t => `<span style="display:block;margin-bottom:3px">${esc(t)}</span>`).join('') : `<span style="${_FRC_MUTED}">ไม่มีรายการ</span>`)
      + `</span></div>`;
  }).join('');
  const note = v && v.note ? `<div class="ms-row" style="${_FRC_MUTED};font-size:12px">${esc(v.note)}</div>` : '';
  return _frcSection('สรุปของเดือนนี้', `<div class="ms-card">${rows}${note}</div>`);
}

function _frcSales(s, disc) {
  if (!s) return _frcSection('ยอดขาย', `<div class="ms-card"><div class="ms-row" style="${_FRC_MUTED}">ยังไม่มีข้อมูลยอดขายของเดือนนี้</div></div>`);
  const series = s.series_30d || [];
  const vals = series.map(x => x.revenue);
  const cols = vals.filter(v => v != null).length >= 3
    ? `<div style="margin-top:10px">${msColumns(vals, { partial: -1, labels: series.map(x => _frcDay(x.day)), fmt, grain: 'day', height: 92,
        weekend: series.map(x => [0, 6].includes(msDow(x.day))), aria: 'ยอดขายรายวัน 30 วันล่าสุด' })}</div>` : '';
  const yoy = s.mtd_vs_last_year_pct != null ? ` <span style="font-size:12px;${_FRC_MUTED}">· ปีก่อน</span> ${msPchip(s.mtd_vs_last_year_pct)}` : '';
  const basis = s.month_complete ? '' : ' (เทียบช่วงวันเดียวกัน ถ่วงตามวันในสัปดาห์)';
  const hero = `<div class="ms-kpi"><p class="lbl">ยอดขาย${s.month_complete ? 'ทั้งเดือน' : 'สะสมถึง ' + _frcDay(s.through)} (ไม่รวม VAT)</p>`
    + `<p class="val" style="color:var(--navy-ink)">${_frcBaht(s.mtd)}</p>`
    + `<p class="sub">${msPchip(s.mtd_vs_last_month_pct)} <span style="font-size:12px;${_FRC_MUTED}">เทียบเดือนก่อน${esc(basis)}</span>${yoy}</p>${cols}</div>`;
  const ld = s.last_day || {};
  const tiles = `<div style="${_FRC_TILES}">`
    + msKpiTile(`ยอดวันล่าสุด (${_frcDay(ld.date)})`, _frcBaht(ld.revenue), '#4a70b0', ld.bills != null ? `${fmtN(ld.bills)} บิล` : '')
    + msKpiTile('จำนวนบิล', s.bills != null ? fmtN(s.bills) : '—', '#0891b2', s.customers != null ? `ลูกค้า ${fmtN(s.customers)} คน` : '')
    + msKpiTile('เฉลี่ยต่อบิล', _frcBaht(s.per_bill), '#7a66b8', '')
    + msKpiTile('ส่วนลด', _frcPct(disc), '#ef4444', 'ของยอดขาย')
    + `</div>`;
  return _frcSection('ยอดขาย', hero + tiles);
}

function _frcGp(g) {
  if (!g || g.gp_pct == null) return _frcSection('กำไรขั้นต้นและต้นทุนอาหาร', `<div class="ms-card"><div class="ms-row" style="${_FRC_MUTED}">ยังไม่มีข้อมูลต้นทุนของเดือนนี้</div></div>`);
  const delta = g.target_pct != null ? Math.round((g.gp_pct - g.target_pct) * 10) / 10 : null;
  const ref = g.target_basis === 'last_year' ? 'เป้า = เดือนเดียวกันปีก่อน' : 'เป้า = เดือนก่อน';
  return _frcSection('กำไรขั้นต้นและต้นทุนอาหาร', `<div style="${_FRC_TILES};margin-top:0">`
    + msKpiTile('กำไรขั้นต้น (GP)', _frcPct(g.gp_pct), '#10b981', delta != null ? `${msPchip(delta, true, ' จุด')} <span style="font-size:12px;${_FRC_MUTED}">${ref} ${_frcPct(g.target_pct)}</span>` : '')
    + msKpiTile('ต้นทุนอาหาร', _frcPct(g.food_cost_pct), '#f59e0b', 'ของยอดขาย (ตามสูตร ไม่รวมเมนูที่ยังไม่มีต้นทุน)')
    + `</div>`);
}

function _frcIngredients(i) {
  if (!i) return '';
  const lp = i.latest_period;
  const period = lp
    ? _frcRow('รอบตรวจนับล่าสุด', `${_frcDay(lp.begin)} – ${_frcDay(lp.end)}`, lp.reconciled ? 'ยืนยันรับสินค้าครบแล้ว' : 'ยังไม่ยืนยันรับสินค้าครบ')
      + _frcRow('ส่วนต่างการใช้วัตถุดิบเทียบสูตร', lp.diff_pct == null ? _FRC_NA : `${lp.diff_pct > 0 ? '+' : ''}${_frcPct(lp.diff_pct)} (${lp.diff_value > 0 ? '+' : ''}${_frcBaht(lp.diff_value)})`,
          lp.diff_pct == null ? '' : (lp.diff_pct > 0 ? 'ใช้เกินสูตร' : lp.diff_pct < 0 ? 'ใช้น้อยกว่าสูตร' : 'ตรงสูตร'))
    : _frcRow('รอบตรวจนับล่าสุด', _FRC_NA, 'ยังไม่มีรอบตรวจนับที่เปรียบเทียบได้');
  return _frcSection('วัตถุดิบ', `<div class="ms-card">${period}`
    + _frcRow('ของเสียในเดือนนี้', _frcPct(i.waste_pct_month), 'สัดส่วนต่อมูลค่าวัตถุดิบที่เบิกใช้')
    + _frcRow('เบิกเกินสูตรติดต่อกันหลายวัน', i.streak_flag ? `<span style="color:var(--bad-ink)">พบ ${fmtN(i.streak_days)} วัน</span>` : `<span style="color:var(--good-ink)">ไม่พบ</span>`)
    + `</div>`);
}

function _frcDiscipline(d) {
  if (!d) return '';
  if (!d.module_active) return _frcSection('วินัยการทำงานของสาขา', `<div class="ms-card"><div class="ms-row" style="${_FRC_MUTED}">สาขายังไม่ได้เริ่มใช้ระบบตัดเบิกและสั่งสินค้า</div></div>`);
  return _frcSection('วินัยการทำงานของสาขา', `<div class="ms-card">`
    + _frcRow('ใบตัดเบิกที่ยืนยันแล้ว', _frcPct(d.slip_coverage_pct, 0), 'ต่อจำนวนวันที่เปิดขาย')
    + _frcRow('ใบตัดเบิกที่ยืนยันตรงวัน', _frcPct(d.slip_on_time_pct, 0), 'ต่อจำนวนใบที่ยืนยันแล้ว')
    + _frcRow('ใบสั่งซื้อที่ส่งทันเวลาตัดรอบ', _frcPct(d.orders_on_time_pct, 0))
    + _frcRow('การบันทึกรับสินค้าทันกำหนด', _frcPct(d.receipts_on_time, 0), 'ภายในวันถัดจากวันส่ง')
    + _frcRow('การแจ้งเคลมสินค้าในเดือนนี้', d.claims_month == null ? _FRC_NA : `${fmtN(d.claims_month)} ครั้ง`)
    + `</div>`);
}

function _frcMenus(list) {
  if (!list || !list.length) return _frcSection('เมนูขายดี 10 อันดับ', `<div class="ms-card"><div class="ms-row" style="${_FRC_MUTED}">ยังไม่มีข้อมูลเมนูของเดือนนี้</div></div>`);
  const maxQ = Math.max(1, ...list.map(x => x.plates || 0));
  return _frcSection('เมนูขายดี 10 อันดับ (จำนวนจาน)', `<div class="ms-card">` + list.map((x, i) => `<div class="ms-row">
      <span style="width:18px;font-size:12px;${_FRC_MUTED}">${i + 1}</span>
      <span style="flex:1;min-width:0"><span style="font-size:13px;color:var(--text-primary);display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(x.name)}</span>
      <span style="display:flex;align-items:center;gap:6px;margin-top:3px">${msBar(Math.round(100 * (x.plates || 0) / maxQ), '#0891b2')}</span></span>
      <span style="width:64px;text-align:right;flex-shrink:0;font-size:13px;font-weight:600;font-variant-numeric:tabular-nums">${fmtN(x.plates || 0)}</span>
    </div>`).join('') + `</div>`);
}

function _frcRender(d) {
  if (!d.available) {
    return _frcSection('ร้านของฉัน', `<div class="ms-card"><div class="ms-row" style="${_FRC_MUTED}">ยังไม่มีข้อมูลของเดือนนี้ เลือกเดือนก่อนหน้าได้จากช่องเดือนด้านบน</div></div>`);
  }
  return _frcVerdict(d.verdict) + _frcSales(d.sales, d.discount_pct) + _frcGp(d.gp) + _frcIngredients(d.ingredients)
    + _frcDiscipline(d.discipline) + _frcMenus(d.top_menus);
}
