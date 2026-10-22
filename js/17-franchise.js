// 17-franchise.js — ร้านของฉัน (PR-L, 2026-10-10): the franchise owner's page. One branch (the account is tied to it by
// the server), one month, read only, from GET /api/franchise/summary (backend/routers/franchise.py).
// Built phone-first on the cards of the owner's phone summary (09c-mobile.js: ms-section, ms-kpi, ms-row, msPchip,
// msColumns, msBar). Shares the global scope of slices 01-16; every name starts with `frc`.
// There is no branch picker here: the branch is text (stripBranchPickers / pinnedBranchName, 01-core.js).
// PR-F1 (2026-10-22): the daily table (finished days only), the 12-month trend (each month on its own, never a sum), the sales
// target, the food-cost floor chip and one download (GET /api/franchise/summary.xlsx: สรุป / รายวัน / 12 เดือน / เมนูขายดี).
// A franchise login lands here (_landingTab, 01-core.js; phBoot / phShowHome, 09c-mobile.js). Paper: the print stylesheet in index.html.

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
  const dl = document.getElementById('frcDownload');
  if (dl) dl.hidden = !d.available;
  if (body) body.innerHTML = _frcRender(d);
}

// ดาวน์โหลดรายงานเดือนนี้: the month as an Excel file; the server's reason goes on a toast when it refuses
async function frcDownload() {
  const btn = document.getElementById('frcDownload');
  const qs = _frcMonth ? '?month=' + encodeURIComponent(_frcMonth) : '';
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(`${API}/api/franchise/summary.xlsx${qs}`);
    if (!r.ok) {
      let m = '';
      try { const j = await r.json(); m = typeof j.detail === 'string' ? j.detail : ''; } catch (e) { /* not json */ }
      throw new Error(m || 'ดาวน์โหลดไม่สำเร็จ');
    }
    let fname = 'ร้านของฉัน.xlsx';
    const m = /filename\*=UTF-8''([^;]+)/i.exec(r.headers.get('content-disposition') || '');
    if (m) { try { fname = decodeURIComponent(m[1]); } catch (e) { /* keep the plain name */ } }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(await r.blob());
    a.download = fname;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    uiToast('ดาวน์โหลดแล้ว · ' + fname, 'ok');
  } catch (e) {
    uiToast(String((e && e.message) || e).slice(0, 200), 'warn');
  } finally {
    if (btn) btn.disabled = false;
  }
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
    + msKpiTile('ต้นทุนอาหาร', _frcPct(g.food_cost_pct), '#f59e0b', 'ของยอดขาย ตามสูตร')
    + `</div>` + _frcCoverage(g.coverage));
}

// the food-cost floor: a dish with no costed recipe counts ฿0, so the food cost above understates (rules.coverage_sums_sql)
function _frcCoverage(c) {
  if (!c || !c.floor) return '';
  const plates = c.uncosted_plates_pct, sales = c.uncosted_sales_pct;
  const help = '<p>ต้นทุนอาหารคำนวณจากสูตรของแต่ละเมนู เมนูที่ยังไม่มีต้นทุนนับเป็น 0 บาท ต้นทุนอาหารที่แสดงจึงเป็นค่าต่ำสุด และกำไรขั้นต้นอาจสูงกว่าความเป็นจริง</p>'
    + `<p>เดือนนี้ยังไม่มีต้นทุน ${esc(_frcPctText(plates))} ของจำนวนจาน และ ${esc(_frcPctText(sales))} ของยอดขายเมนูที่ต้องมีต้นทุน `
    + '(ไม่นับแพ็กเกจบุฟเฟ่ต์ ค่าบริการ และส่วนลด) บุฟเฟ่ต์ใช้จำนวนจานเป็นหลัก เนื่องจากเมนูในบุฟเฟ่ต์ไม่มียอดขายรายจาน</p>';
  return `<div class="frc-chip-row"><span class="frc-chip">ต้นทุนอาหารเป็นค่าต่ำสุด · ยังไม่มีต้นทุน ${esc(_frcPctText(plates))} ของจำนวนจาน</span>`
    + uiQ('frc:coverage', 'ต้นทุนอาหารเป็นค่าต่ำสุด', help) + `</div>`;
}
function _frcPctText(v) { return v == null ? '—' : Number(v).toFixed(1).replace(/\.0$/, '') + '%'; }
function _frcDelta(v) {
  if (v == null) return _FRC_NA;
  const ink = v > 0 ? 'var(--good-ink)' : v < 0 ? 'var(--bad-ink)' : 'var(--text-muted)';
  return `<span style="color:${ink}">${v > 0 ? '+' : ''}${Number(v).toFixed(1).replace(/\.0$/, '')}%</span>`;
}

// ── เป้ายอดขาย: the month's target (targets.py) against the sales so far ──────────────────────────────────────────────
function _frcTarget(t) {
  if (!t) return '';
  const title = 'เป้ายอดขาย';
  if (t.source === 'none' || t.month_target == null) {
    return _frcSection(title, `<div class="ms-card"><div class="ms-row" style="${_FRC_MUTED};font-size:13px">ยังไม่มีเป้ายอดขายของเดือนนี้</div></div>`);
  }
  const src = t.source === 'manual' ? 'เป้าที่กำหนดไว้' : 'เป้า = ยอดขายเดือนเดียวกันของปีก่อน';
  const how = t.weighting === 'dow' ? 'ถ่วงตามวันในสัปดาห์' : t.weighting === 'days' ? 'ตามจำนวนวัน' : 'ทั้งเดือน';
  const share = Math.max(0, Math.min(100, t.of_month_pct || 0)), mark = Math.max(0, Math.min(100, t.elapsed_share_pct || 0));
  const help = '<p>เป้ายอดขายของเดือนคือเป้าที่กำหนดไว้ หากไม่ได้กำหนด ใช้ยอดขายของสาขาในเดือนเดียวกันของปีก่อน</p>'
    + '<p>เป้าถึงวันที่ข้อมูล = เป้าทั้งเดือน × สัดส่วนของเดือนที่ผ่านไป ระหว่างเดือนสัดส่วนนี้ถ่วงตามวันในสัปดาห์ตามรูปแบบยอดขายของเดือนก่อน '
    + 'วันเสาร์และวันอาทิตย์จึงมีน้ำหนักมากกว่าวันธรรมดา เส้นแนวตั้งบนแถบแสดงสัดส่วนนี้</p>';
  const rows = (t.month_complete ? '' : _frcRow('เป้าถึงวันที่ข้อมูล', _frcBaht(t.to_date), how))
    + _frcRow('ยอดขายเทียบเป้า' + (t.month_complete ? 'ทั้งเดือน' : 'ถึงวันที่ข้อมูล'), _frcDelta(t.vs_to_date_pct))
    + _frcRow('เป้าทั้งเดือน', _frcBaht(t.month_target), src);
  return `<div class="ms-section"><div class="ms-section-title frc-title">${title} ${uiQ('frc:target', title, help)}</div><div class="ms-card">`
    + `<div class="ms-row" style="flex-wrap:wrap;gap:6px"><span style="flex:1 1 100%;font-size:13px;color:var(--text-primary)">ยอดขายคิดเป็น <b>${_frcPct(t.of_month_pct)}</b> ของเป้าทั้งเดือน</span>`
    + `<span class="frc-meter" role="img" aria-label="ยอดขาย ${esc(_frcPctText(t.of_month_pct))} ของเป้าทั้งเดือน"><span style="width:${share}%"></span>${t.month_complete ? '' : `<i style="left:${mark}%"></i>`}</span></div>`
    + rows + `</div></div>`;
}

// ── ยอดขายรายวัน: every finished day of the month, newest first ──────────────────────────────────────────────────────
function _frcDaily(s) {
  const rows = ((s && s.daily) || []).slice().reverse();
  if (!rows.length) return '';
  const help = '<p>ยอดขายไม่รวม VAT และไม่รวมค่าบริการ แสดงเฉพาะวันที่ปิดการขายแล้ว วันที่ยังเปิดขายอยู่ไม่นำมาแสดง</p>'
    + '<p>เทียบสัปดาห์ก่อน = ยอดขายของวันนั้นเทียบกับวันเดียวกันของสัปดาห์ก่อนหน้า เช่น วันจันทร์เทียบวันจันทร์</p>';
  const body = rows.map(x => {
    const we = ['เสาร์', 'อาทิตย์'].includes(x.weekday);
    return `<tr${we ? ' class="frc-we"' : ''}><td>${esc(_frcDay(x.date))} <span class="frc-dow">${esc(x.weekday || '')}</span></td>`
      + `<td class="n">${x.revenue == null ? _FRC_NA : fmtN(x.revenue)}</td><td class="n">${_frcDelta(x.vs_last_week_pct)}</td>`
      + `<td class="n">${x.bills == null ? _FRC_NA : fmtN(x.bills)}</td><td class="n">${x.customers == null ? _FRC_NA : fmtN(x.customers)}</td>`
      + `<td class="n">${x.per_bill == null ? _FRC_NA : fmtN(x.per_bill)}</td></tr>`;
  }).join('');
  return `<div class="ms-section"><div class="ms-section-title frc-title">ยอดขายรายวัน ${uiQ('frc:daily', 'ยอดขายรายวัน', help)}</div>`
    + `<div class="frc-tbl-wrap"><table class="frc-tbl"><thead><tr><th>วันที่</th><th class="n">ยอดขาย (฿)</th><th class="n">เทียบสัปดาห์ก่อน</th><th class="n">บิล</th>`
    + `<th class="n">ลูกค้า</th><th class="n">ต่อบิล (฿)</th></tr></thead><tbody>${body}</tbody></table></div></div>`;
}

// ── แนวโน้ม 12 เดือน: each month on its own with its MoM / YoY; the month in progress is weekday-fair and labelled ─────
function _frcMonthLabel(m) { return msPeriodLabel(m.month + '-01', 'month'); }
function _frcTrend(t) {
  const months = (t && t.months) || [];
  if (months.length < 2) return '';
  const L = t.latest || months[months.length - 1];
  const help = '<p>แต่ละเดือนแสดงแยกกัน ไม่รวมยอดข้ามเดือน และเทียบกับเดือนก่อนและเดือนเดียวกันของปีก่อน</p>'
    + '<p>เดือนที่ยังไม่สิ้นสุดแสดงยอดสะสมถึงวันล่าสุดที่ปิดการขายแล้ว และเทียบกับเดือนก่อนในช่วงวันเดียวกัน ถ่วงตามวันในสัปดาห์</p>'
    + '<p>กำไรขั้นต้น (GP) คำนวณแบบเดียวกับรายงานผู้บริหาร</p>';
  const part = L.partial ? months.length - 1 : -1;
  const chart = `<div style="margin-top:10px">${msColumns(months.map(m => m.revenue), { partial: part, labels: months.map(_frcMonthLabel), fmt, grain: 'month',
    height: 96, aria: 'ยอดขายรายเดือน 12 เดือนล่าสุด' })}</div>`;
  const yoy = L.yoy_pct != null ? ` <span style="font-size:12px;${_FRC_MUTED}">· ปีก่อน</span> ${msPchip(L.yoy_pct)}` : '';
  const hero = `<div class="ms-kpi"><p class="lbl">ยอดขาย ${esc(_frcMonthLabel(L))}${L.partial && L.through ? ' (สะสมถึง ' + esc(_frcDay(L.through)) + ')' : ''}</p>`
    + `<p class="val" style="color:var(--navy-ink)">${_frcBaht(L.revenue)}</p>`
    + `<p class="sub">${msPchip(L.mom_pct)} <span style="font-size:12px;${_FRC_MUTED}">เทียบเดือนก่อน${L.mom_basis === 'dow' ? ' (ถ่วงตามวันในสัปดาห์)' : ''}</span>${yoy}</p>${chart}</div>`;
  const body = months.slice().reverse().map(m => `<tr${m.partial ? ' class="frc-part"' : ''}><td>${esc(_frcMonthLabel(m))}${m.partial ? ' <span class="frc-dow">สะสม</span>' : ''}</td>`
    + `<td class="n">${m.revenue == null ? _FRC_NA : fmtN(m.revenue)}</td><td class="n">${_frcDelta(m.mom_pct)}</td><td class="n">${_frcDelta(m.yoy_pct)}</td>`
    + `<td class="n">${_frcPct(m.gp_pct)}</td><td class="n">${m.customers == null ? _FRC_NA : fmtN(m.customers)}</td>`
    + `<td class="n">${m.per_bill == null ? _FRC_NA : fmtN(m.per_bill)}</td></tr>`).join('');
  return `<div class="ms-section"><div class="ms-section-title frc-title">แนวโน้ม 12 เดือน ${uiQ('frc:trend', 'แนวโน้ม 12 เดือน', help)}</div>${hero}`
    + `<div class="frc-tbl-wrap" style="margin-top:10px"><table class="frc-tbl"><thead><tr><th>เดือน</th><th class="n">ยอดขาย (฿)</th><th class="n">เทียบเดือนก่อน</th>`
    + `<th class="n">เทียบปีก่อน</th><th class="n">GP</th><th class="n">ลูกค้า</th><th class="n">ต่อบิล (฿)</th></tr></thead><tbody>${body}</tbody></table></div></div>`;
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
  return _frcVerdict(d.verdict) + _frcSales(d.sales, d.discount_pct) + _frcTarget(d.target) + _frcDaily(d.sales) + _frcGp(d.gp)
    + _frcTrend(d.trend_12m) + _frcIngredients(d.ingredients) + _frcDiscipline(d.discipline) + _frcMenus(d.top_menus);
}
