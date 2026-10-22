// 17-franchise.js — ร้านของฉัน (PR-L, 2026-10-10): the franchise owner's page. One branch (the account is tied to it by
// the server), one month, read only, from GET /api/franchise/summary (backend/routers/franchise.py).
// Built phone-first on the cards of the owner's phone summary (09c-mobile.js: ms-section, ms-kpi, ms-row, msPchip,
// msColumns, msBar). Shares the global scope of slices 01-16; every name starts with `frc`.
// There is no branch picker here: the branch is text (stripBranchPickers / pinnedBranchName, 01-core.js).
// PR-F1 (2026-10-22): the daily table (finished days only), the 12-month trend (each month on its own, never a sum), the sales
// target, the food-cost floor chip and one download (GET /api/franchise/summary.xlsx: สรุป / รายวัน / 12 เดือน / เมนูขายดี).
// A franchise login lands here (_landingTab, 01-core.js; phBoot / phShowHome, 09c-mobile.js). Paper: the print stylesheet in index.html.
// PR-F2 (2026-10-22): below the fold, from GET /api/franchise/insight (loaded when it nears the screen): สัดส่วนการขาย, ช่วงเวลาขายดี,
// การควบคุม (discounts / voided bills / cash, counts and baht only), เสียงลูกค้า; and the รับแจ้งเตือนทุกเช้า card (kind "franchise").
// PR-F3 (2026-10-22): วัตถุดิบจากส่วนกลาง after the branch-discipline card, from GET /api/franchise/supply (loaded when it nears the
// screen): delivery rounds (ordered / delivered baht, status), purchases against sales vs last month, claims, late orders, the five
// lines used most over the recipes. Before the branch started the supply module: one neutral line.

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
  if (d.available) { frcInsightWatch(gen); frcSupplyWatch(gen); }
  frcPushInit();
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
// U3 (owner 2026-10-22): the month is written as the header, the month list and the Excel file write it ("ต.ค. 2026", the management
// report's label), never the phone's short "ต.ค. 69"
const _FRC_MON = ['ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.', 'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.'];
function _frcMonthLabel(m) {
  if (m && m.label) return String(m.label);
  const x = /^(\d{4})-(\d{2})/.exec(String((m && m.month) || ''));
  return x && _FRC_MON[+x[2] - 1] ? `${_FRC_MON[+x[2] - 1]} ${x[1]}` : String((m && m.month) || '');
}
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
    + _frcTrend(d.trend_12m) + _frcIngredients(d.ingredients) + _frcDiscipline(d.discipline)
    + `<div id="frcSupply"><div class="ms-section"><div class="ms-card"><div class="ms-row" style="${_FRC_MUTED};font-size:13px">กำลังโหลดวัตถุดิบจากส่วนกลาง…</div></div></div></div>`
    + _frcMenus(d.top_menus)
    + `<div id="frcInsight"><div class="ms-section"><div class="ms-card"><div class="ms-row" style="${_FRC_MUTED};font-size:13px">กำลังโหลดสัดส่วนการขาย การควบคุม และเสียงลูกค้า…</div></div></div></div>`;
}

// ── PR-F2 (2026-10-22): สัดส่วนการขาย · ช่วงเวลาขายดี · การควบคุม · เสียงลูกค้า, from GET /api/franchise/insight. Loaded once the
// cards above are drawn and the placeholder comes near the screen (IntersectionObserver; at once where it is missing). ──
let _frcInsGen = 0;
function frcInsightWatch(gen) {
  const box = document.getElementById('frcInsight');
  if (!box) return;
  const go = () => { if (gen === _frcGen && _frcInsGen !== gen) { _frcInsGen = gen; frcLoadInsight(gen); } };
  if (!('IntersectionObserver' in window)) { go(); return; }
  const io = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) { io.disconnect(); go(); } }, { rootMargin: '600px 0px' });
  io.observe(box);
}
async function frcLoadInsight(gen) {
  const box = document.getElementById('frcInsight');
  const qs = _frcMonth ? '?month=' + encodeURIComponent(_frcMonth) : '';
  let d;
  try {
    const r = await fetch(`${API}/api/franchise/insight${qs}`);
    const t = await r.text();
    let j = null; try { j = JSON.parse(t); } catch (e) { /* not json */ }
    if (!r.ok) throw new Error((j && typeof j.detail === 'string' && j.detail) || 'โหลดข้อมูลไม่สำเร็จ');
    d = j;
  } catch (e) {
    if (gen !== _frcGen || !box) return;
    box.innerHTML = `<div class="ms-section"><div class="ms-card"><div class="ms-row" style="color:var(--bad-ink)">${esc(e.message || 'โหลดข้อมูลไม่สำเร็จ')}</div></div></div>`;
    return;
  }
  if (gen !== _frcGen || !box) return;
  box.innerHTML = d.available ? _frcMix(d.mix) + _frcPeak(d.peak) + _frcControls(d.controls) + _frcVoice(d.voice) : _frcVoice(d.voice);
}

// a card: title + "?" + one-line subtitle
function _frcCard(title, key, help, sub, inner) {
  return `<div class="ms-section"><div class="ms-section-title frc-title">${esc(title)} ${uiQ('frc:' + key, title, help)}</div>`
    + (sub ? `<p class="frc-sub">${esc(sub)}</p>` : '') + inner + `</div>`;
}
function _frcShareRow(label, share, prev, sub, col) {
  const w = Math.max(0, Math.min(100, share || 0));
  const d = share != null && prev != null ? Math.round((share - prev) * 10) / 10 : null;
  return `<div class="ms-row" style="flex-wrap:wrap;gap:4px 10px"><span style="flex:1 1 55%;min-width:0;font-size:13px;color:var(--text-primary)">${esc(label)}`
    + (sub ? `<br><span style="font-size:12px;${_FRC_MUTED}">${sub}</span>` : '') + `</span>`
    + `<span style="font-size:14px;font-weight:600;font-variant-numeric:tabular-nums;color:var(--text-primary)">${_frcPct(share)}</span>`
    + `<span style="flex:1 1 100%;display:flex;align-items:center;gap:8px">${msBar(w, col || 'var(--navy-ink)')}`
    + `<span style="font-size:12px;white-space:nowrap;${_FRC_MUTED}">เดือนก่อน ${_frcPct(prev)}${d != null && d !== 0 ? ` (${d > 0 ? '+' : ''}${d} จุด)` : ''}</span></span></div>`;
}

// ── สัดส่วนการขาย ───────────────────────────────────────────────────────────
function _frcMix(m) {
  if (!m) return '';
  const help = '<p>ยอดขายแยกตามประเภท คิดจากยอดขายรายเมนูในช่วงวันเดียวกับยอดขายด้านบน และเทียบกับช่วงวันเดียวกันของเดือนก่อน</p>'
    + '<p>บุฟเฟ่ต์ = แพ็กเกจบุฟเฟ่ต์ (จำนวนลูกค้า = จำนวนแพ็กเกจที่ขาย) · อาหารตามสั่ง = เมนูที่ขายแยกจาน · เครื่องดื่มและอื่น ๆ = เครื่องดื่ม เมนูเดลิเวอรี และรายการอื่น</p>'
    + '<p>ระดับราคาบุฟเฟ่ต์มาจากรายงานผู้บริหาร นับเฉพาะบิลบุฟเฟ่ต์ · ช่องทางขายมาจากประเภทลูกค้าที่บันทึกใน POS (ทั้งเดือน)</p>';
  const lines = (m.lines || []).map(x => _frcShareRow(x.label, x.sales_share, x.sales_share_prev,
    `฿${fmtN(x.sales || 0)}${x.covers != null ? ` · ลูกค้า ${fmtN(x.covers)} คน (${_frcPctText(x.covers_share)} ของลูกค้าบุฟเฟ่ต์)` : ''}`)).join('');
  const tiers = (m.tiers || []).length ? `<div class="frc-sub2">ระดับราคาบุฟเฟ่ต์</div><div class="frc-tbl-wrap"><table class="frc-tbl"><thead><tr><th>ราคา</th>`
    + `<th class="n">ลูกค้า</th><th class="n">สัดส่วน</th><th class="n">เดือนก่อน</th><th class="n">ต่อหัว (฿)</th></tr></thead><tbody>`
    + m.tiers.map(t => `<tr><td>${esc(t.label || t.tier)}</td><td class="n">${t.covers == null ? _FRC_NA : fmtN(t.covers)}</td>`
      + `<td class="n">${_frcPct(t.share)}</td><td class="n">${_frcPct(t.share_prev)}</td><td class="n">${t.per_head == null ? _FRC_NA : fmtN(t.per_head)}</td></tr>`).join('')
    + `</tbody></table></div>` : '';
  const ch = (m.channels || []).length ? `<div class="frc-sub2">ช่องทางขาย (ทั้งเดือน)</div><div class="ms-card">`
    + m.channels.map(c => _frcShareRow(c.type, c.sales_share, c.sales_share_prev, c.delivery ? 'เดลิเวอรี' : '', c.delivery ? 'var(--amber-ink)' : 'var(--navy-ink)')).join('')
    + `</div>` : '';
  if (!lines && !tiers && !ch) return '';
  return _frcCard('สัดส่วนการขาย', 'mix', help, 'สัดส่วนของยอดขายตามประเภท ระดับราคาบุฟเฟ่ต์ และช่องทางขาย เทียบเดือนก่อน',
    (lines ? `<div class="frc-sub2" style="margin-top:0">ประเภทการขาย</div><div class="ms-card">${lines}</div>` : '') + tiers + ch);
}

// ── ช่วงเวลาขายดี ────────────────────────────────────────────────────────────
const _FRC_DOW = [[1, 'จ.'], [2, 'อ.'], [3, 'พ.'], [4, 'พฤ.'], [5, 'ศ.'], [6, 'ส.'], [0, 'อา.']];    // strftime %w: Sunday = 0
const _FRC_DOW_FULL = { 0: 'วันอาทิตย์', 1: 'วันจันทร์', 2: 'วันอังคาร', 3: 'วันพุธ', 4: 'วันพฤหัสบดี', 5: 'วันศุกร์', 6: 'วันเสาร์' };
function _frcK(n) { return n >= 1000 ? (Math.round(n / 100) / 10).toString().replace(/\.0$/, '') + 'K' : String(n); }
function _frcHour(h) { return String(h).padStart(2, '0') + ':00'; }
function _frcPeak(p) {
  if (!p || !(p.hours || []).length) return '';
  const help = '<p>แท่งแสดงยอดขาย (ไม่รวม VAT) ของแต่ละชั่วโมงรวมทั้งช่วงเดือน ตัวเลขใต้แท่งคือจำนวนลูกค้า</p>'
    + '<p>ตารางสีแสดงยอดขายเฉลี่ยต่อวันของแต่ละวันในสัปดาห์และชั่วโมง สีเข้มคือช่วงที่ขายได้มาก ใช้วางกำลังคนและการเตรียมของ</p>';
  const hrs = p.hours, max = Math.max(1, ...hrs.map(h => h.sales || 0));
  const bars = `<div class="frc-hours" role="img" aria-label="ยอดขายตามชั่วโมง ชั่วโมงที่ขายดีที่สุด ${esc(_frcHour(p.peak_hour))}">`
    + hrs.map(h => `<div class="frc-hcol${h.hour === p.peak_hour ? ' pk' : ''}" title="${esc(_frcHour(h.hour))} · ฿${fmtN(h.sales || 0)} · ลูกค้า ${fmtN(h.covers || 0)} คน">`
      + `<span class="bar" style="height:${Math.max(2, Math.round(100 * (h.sales || 0) / max))}%"></span><span class="hl">${h.hour}</span>`
      + `<span class="hc">${_frcK(h.covers || 0)}</span></div>`).join('') + `</div>`;
  const dp = (p.dayparts || []).map(x => `<span class="frc-pill">${esc(x.name)} ${_frcPct(x.share)}</span>`).join('');
  const head = `<div class="ms-row" style="flex-wrap:wrap;gap:6px"><span style="flex:1 1 100%;font-size:13px;color:var(--text-primary)">ชั่วโมงที่ขายดีที่สุด <b>${esc(_frcHour(p.peak_hour))}</b>`
    + ` (฿${fmtN(p.peak_hour_sales || 0)} ทั้งเดือน)</span>${dp}</div>`;
  const cells = p.heat || [];
  let heat = '';
  if (cells.length) {
    const hs = [...new Set(cells.map(c => c.hour))].sort((a, b) => a - b);
    const h0 = hs[0], h1 = hs[hs.length - 1];
    const hours = []; for (let h = h0; h <= h1; h++) hours.push(h);
    const by = {}; cells.forEach(c => { by[c.dow + ':' + c.hour] = c.sales; });
    const hmax = Math.max(1, ...cells.map(c => c.sales || 0));
    const b = p.heat_best;
    heat = `<div class="frc-sub2">ยอดขายเฉลี่ยต่อวัน ตามวันและชั่วโมง</div><div class="frc-heat" style="grid-template-columns:28px repeat(${hours.length},1fr)">`
      + `<span></span>` + hours.map(h => `<span class="hh">${h % 2 === 0 ? h : ''}</span>`).join('')
      + _FRC_DOW.map(([dw, lbl]) => `<span class="hd">${lbl}</span>` + hours.map(h => {
        const v = by[dw + ':' + h];
        const a = v ? Math.round(12 + 88 * v / hmax) : 0;
        return `<span class="hx" style="background:${a ? `color-mix(in srgb, var(--navy-ink) ${a}%, transparent)` : 'var(--bg-subtle)'}" title="${esc(_FRC_DOW_FULL[dw])} ${esc(_frcHour(h))} · ${v ? '฿' + fmtN(v) + ' ต่อวัน' : 'ไม่มียอดขาย'}"></span>`;
      }).join('')).join('') + `</div>`
      + (b ? `<p class="frc-sub" style="margin-top:6px">ช่วงที่ขายดีที่สุด: ${esc(_FRC_DOW_FULL[b.dow])} ${esc(_frcHour(b.hour))} เฉลี่ย ฿${fmtN(b.sales || 0)} ต่อวัน</p>` : '');
  }
  return _frcCard('ช่วงเวลาขายดี', 'peak', help, 'ยอดขายและจำนวนลูกค้าตามชั่วโมง และตามวันในสัปดาห์ของเดือนนี้',
    `<div class="ms-card">${head}<div style="padding:4px 2px 2px">${bars}</div></div>` + heat);
}

// ── การควบคุม: discounts against the ceiling, voided bills, cash over / short - counts and baht only ─────────────────
function _frcFlagBox(f) {
  if (!f) return '';
  const items = f.items || [];
  if (!items.length) return `<div class="ms-row" style="gap:10px"><span style="flex:0 0 44px;font-size:13px;font-weight:700;color:var(--good-ink)">ดี</span>`
    + `<span style="font-size:13px;color:var(--text-primary)">ส่วนลด บิลยกเลิก และเงินสดอยู่ในเกณฑ์</span></div>`;
  return [['fix', 'ต้องแก้', 'var(--bad-ink)'], ['watch', 'ควรดู', 'var(--amber-ink)']].map(([k, lbl, ink]) => {
    const xs = items.filter(i => i.level === k);
    if (!xs.length) return '';
    return `<div class="ms-row" style="align-items:flex-start;gap:10px"><span style="flex:0 0 44px;font-size:13px;font-weight:700;color:${ink}">${lbl}</span>`
      + `<span style="flex:1;min-width:0;font-size:13px;line-height:1.5;color:var(--text-primary)">${xs.map(i => `<span style="display:block;margin-bottom:3px">${esc(i.text)}</span>`).join('')}</span></div>`;
  }).join('');
}
function _frcMoneyList(title, rows, name, count, unit) {
  if (!rows || !rows.length) return '';
  return `<div class="frc-sub2">${esc(title)}</div><div class="ms-card">` + rows.map(r => _frcRow(r[name] || '-', '฿' + fmtN(r.baht || 0),
    `${fmtN(r[count] || 0)} ${unit}${r.share != null ? ' · ' + _frcPctText(r.share) + ' ของรายการนี้' : ''}`)).join('') + `</div>`;
}
function _frcControls(c) {
  if (!c) return '';
  const help = '<p>ส่วนลด: ส่วนลดทั้งหมดเทียบยอดขายก่อนหักส่วนลด เทียบกับเพดานที่บริษัทกำหนด ส่วนลดที่ใช้คูปองจะนับเป็น "ไม่ระบุ" ในรายการตามเหตุผล เพราะ POS บันทึกคูปองแยกจากเหตุผล</p>'
    + '<p>บิลยกเลิก: บิลที่ถูกยกเลิกหรือลบทั้งบิล เกณฑ์ปกติต่ำกว่าร้อยละ 2 ของบิลทั้งหมด เกินร้อยละ 3 ควรตรวจสอบเหตุผล</p>'
    + '<p>เงินสด: ผลการนับเงินสดปิดวัน วันที่ขาดตั้งแต่ 2,000 บาทขึ้นไปควรตรวจสอบ</p>'
    + '<p>แสดงเป็นจำนวนและจำนวนเงินเท่านั้น ไม่แสดงเลขบิลหรือชื่อผู้ใช้งาน</p>';
  const D = c.discount || {}, V = c.voids || {}, C = c.cash || {};
  const over = D.pct != null && D.policy_pct != null && D.pct > D.policy_pct;
  const disc = `<div class="ms-card">` + _frcRow('ส่วนลด', `<span style="color:${over ? 'var(--bad-ink)' : 'var(--text-primary)'}">${_frcPct(D.pct)}</span>`,
      `เพดาน ${_frcPctText(D.policy_pct)} · ฿${fmtN(D.baht || 0)}${D.over_policy_baht ? ' · เกินเพดาน ฿' + fmtN(D.over_policy_baht) : ''}`)
    + _frcRow('บิลยกเลิก', V.bills ? `${fmtN(V.bills)} ใบ` : '0 ใบ', V.bills ? `฿${fmtN(V.baht || 0)} · ${_frcPctText(V.rate_pct)} ของบิลทั้งหมด` : 'ไม่มีบิลยกเลิกในเดือนนี้')
    + _frcRow('เงินสดขาด/เกิน สุทธิ', C.net == null ? _FRC_NA : `<span style="color:${C.net < 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">${C.net < 0 ? '−' : ''}฿${fmtN(Math.abs(C.net))}</span>`,
      C.days_counted ? `นับเงินสด ${fmtN(C.days_counted)} วัน · ขาด ${fmtN(C.short_days || 0)} วัน` : 'ยังไม่มีข้อมูลการนับเงินสด')
    + `</div>`;
  const cashDays = (C.days || []).filter(x => x.over_short);
  const cash = cashDays.length ? `<div class="frc-sub2">วันที่เงินสดไม่ตรง</div><div class="frc-tbl-wrap"><table class="frc-tbl"><thead><tr><th>วันที่</th><th class="n">ขาด/เกิน (฿)</th></tr></thead><tbody>`
    + cashDays.slice().reverse().map(x => `<tr><td>${esc(_frcDay(x.date))}</td><td class="n" style="color:${x.over_short < 0 ? 'var(--bad-ink)' : 'var(--good-ink)'}">${x.over_short > 0 ? '+' : ''}${fmtN(x.over_short)}</td></tr>`).join('')
    + `</tbody></table></div>` : '';
  return _frcCard('การควบคุม', 'controls', help, 'ส่วนลดเทียบเพดาน บิลยกเลิก และเงินสดขาดเกินของเดือนนี้',
    `<div class="ms-card">${_frcFlagBox(c.flags)}</div>` + disc
    + _frcMoneyList('ส่วนลดตามคูปองและโปรโมชัน', D.by_coupon, 'name', 'uses', 'ครั้ง')
    + _frcMoneyList('ส่วนลดตามเหตุผล', D.by_reason, 'reason', 'bills', 'บิล')
    + _frcMoneyList('บิลยกเลิกตามเหตุผล', V.by_reason, 'reason', 'bills', 'ใบ') + cash);
}

// ── เสียงลูกค้า: the branch's public reviews (stars, count, the latest ones) ─────────────────────────────────────────
function _frcStars(n) {
  if (n == null) return '';
  const k = Math.max(0, Math.min(5, Math.round(n)));
  return `<span class="frc-stars" aria-label="${k} ดาว">${'★'.repeat(k)}<span style="opacity:.3">${'★'.repeat(5 - k)}</span></span>`;
}
function _frcVoice(v) {
  if (!v) return '';
  const help = '<p>รีวิวสาธารณะของสาขาจาก Google, Facebook และช่องทางอื่นที่ระบบเก็บไว้ แสดงคะแนนเฉลี่ยและจำนวนรีวิวของเดือนนี้เทียบเดือนก่อน และรีวิวล่าสุด 5 รายการ</p>'
    + '<p>ชื่อบุคคลที่ระบบพบในข้อความจะแสดงเป็น "พนักงาน"</p>';
  if (!v.available) return _frcCard('เสียงลูกค้า', 'voice', help, 'คะแนนและรีวิวล่าสุดของสาขา',
    `<div class="ms-card"><div class="ms-row" style="${_FRC_MUTED};font-size:13px">ยังไม่มีรีวิวของสาขาในระบบ</div></div>`);
  const ch = v.rating_change;
  const head = `<div style="${_FRC_TILES};margin-top:0">`
    + msKpiTile('คะแนนเฉลี่ยเดือนนี้', v.rating_avg == null ? '—' : Number(v.rating_avg).toFixed(1), '#f59e0b',
      ch != null ? `${msPchip(Math.round(ch * 100) / 100, true, '')} <span style="font-size:12px;${_FRC_MUTED}">เดือนก่อน ${Number(v.rating_avg_prev).toFixed(1)}</span>` : 'เดือนก่อนยังไม่มีรีวิว')
    + msKpiTile('จำนวนรีวิวเดือนนี้', fmtN(v.reviews_month || 0), '#0891b2', v.reviews_prev != null ? `เดือนก่อน ${fmtN(v.reviews_prev)}` : '')
    + `</div>`;
  const list = (v.latest || []).length ? `<div class="frc-sub2">รีวิวล่าสุด</div><div class="ms-card">` + v.latest.map(r => `<div class="ms-row" style="flex-direction:column;align-items:stretch;gap:4px">`
      + `<span style="display:flex;gap:8px;align-items:center;font-size:12px;${_FRC_MUTED}">${_frcStars(r.stars)}<span>${esc(_frcDay(r.date))} · ${esc(r.source || '')}</span></span>`
      + `<span style="font-size:13px;line-height:1.5;color:var(--text-primary);white-space:normal">${r.text ? esc(r.text) : `<span style="${_FRC_MUTED}">(ให้คะแนนโดยไม่เขียนข้อความ)</span>`}</span></div>`).join('') + `</div>` : '';
  return _frcCard('เสียงลูกค้า', 'voice', help, 'คะแนนเฉลี่ย จำนวนรีวิว และรีวิวล่าสุดของสาขา', head + list);
}

// ── รับแจ้งเตือนทุกเช้า: the push card of this page (kind "franchise", the owner's own devices). The functions are the phone
// summary's (09c-mobile.js msPush*, id prefix frcPush); hidden where push is not available or the server has no key. ──
async function frcPushInit() {
  const card = document.getElementById('frcPush');
  if (!card) return;
  const hide = () => { card.hidden = true; };
  if (!(window.AUTH && window.AUTH.enabled) || window.AUTH.role !== 'franchise') return hide();
  if (typeof _msPushSupported !== 'function' || !_msPushSupported()) return hide();
  if (!await msPushVapid()) return hide();
  card.hidden = false;
  await msPushRender(undefined, 'frcPush');
}

// ── PR-F3 (2026-10-22): วัตถุดิบจากส่วนกลาง, from GET /api/franchise/supply. Loaded like the F2 cards: once the cards above are drawn
// and the placeholder comes near the screen. Figures are the supply module's own (the order value of the branch's order screens). ──
let _frcSupGen = 0;
function frcSupplyWatch(gen) {
  const box = document.getElementById('frcSupply');
  if (!box) return;
  const go = () => { if (gen === _frcGen && _frcSupGen !== gen) { _frcSupGen = gen; frcLoadSupply(gen); } };
  if (!('IntersectionObserver' in window)) { go(); return; }
  const io = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) { io.disconnect(); go(); } }, { rootMargin: '600px 0px' });
  io.observe(box);
}
async function frcLoadSupply(gen) {
  const box = document.getElementById('frcSupply');
  const qs = _frcMonth ? '?month=' + encodeURIComponent(_frcMonth) : '';
  let d;
  try {
    const r = await fetch(`${API}/api/franchise/supply${qs}`);
    const t = await r.text();
    let j = null; try { j = JSON.parse(t); } catch (e) { /* not json */ }
    if (!r.ok) throw new Error((j && typeof j.detail === 'string' && j.detail) || 'โหลดข้อมูลไม่สำเร็จ');
    d = j;
  } catch (e) {
    if (gen !== _frcGen || !box) return;
    box.innerHTML = `<div class="ms-section"><div class="ms-card"><div class="ms-row" style="color:var(--bad-ink)">${esc(e.message || 'โหลดข้อมูลไม่สำเร็จ')}</div></div></div>`;
    return;
  }
  if (gen !== _frcGen || !box) return;
  box.innerHTML = _frcSupply(d);
}

function _frcSupply(d) {
  if (!d) return '';
  const title = 'วัตถุดิบจากส่วนกลาง';
  const help = '<p>ยอดสั่ง = มูลค่าที่สาขาสั่งในแต่ละรอบ · ยอดที่คลังจัดส่ง = มูลค่าที่คลังส่งจริง คิดแบบเดียวกับหน้าสั่งสินค้าของสาขา นับรอบที่วันส่งอยู่ในเดือนนี้ ไม่นับใบที่ยังเป็นร่างหรือยกเลิก</p>'
    + '<p>ยอดซื้อเทียบยอดขาย = ยอดที่คลังจัดส่ง ÷ ยอดขายไม่รวม VAT ของเดือนเดียวกัน ระหว่างเดือนนับถึงวันล่าสุดที่ปิดการขาย และเทียบกับทั้งเดือนของเดือนก่อน</p>'
    + '<p>ส่งใบสั่งซื้อหลังเวลาตัดรอบมีค่าปรับ หน้านี้แสดงเฉพาะจำนวนใบ · วัตถุดิบที่ใช้เกินสูตรมาจากรอบตรวจนับล่าสุด เรียงตามมูลค่า</p>';
  if (!d.started) {
    return _frcCard(title, 'supply', help, '', `<div class="ms-card"><div class="ms-row" style="${_FRC_MUTED};font-size:13px">${esc(d.not_started_th || 'สาขายังไม่เริ่มใช้ระบบสั่งวัตถุดิบ')}</div></div>`);
  }
  const P = d.purchases || {}, L = d.late || {}, C = d.claims || {};
  const ch = P.change_pp;
  const ratioSub = P.pct_prev != null
    ? `${ch != null && ch !== 0 ? msPchip(Math.round(ch * 10) / 10, false, ' จุด') + ' ' : ''}<span style="font-size:12px;${_FRC_MUTED}">เดือนก่อน ${_frcPctText(P.pct_prev)}</span>`
    : `<span style="font-size:12px;${_FRC_MUTED}">เดือนก่อนยังไม่มีข้อมูล</span>`;
  const upto = P.month_complete ? 'ทั้งเดือน' : (P.through ? 'ถึง ' + _frcDay(P.through) : '');
  const tiles = `<div style="${_FRC_TILES};margin-top:0">`
    + msKpiTile('ยอดสั่งจากส่วนกลาง', _frcBaht(P.ordered), '#4a70b0', `${fmtN(P.rounds || 0)} รอบส่ง`)
    + msKpiTile('ยอดที่คลังจัดส่ง', _frcBaht(P.delivered), '#0891b2', `จัดส่งแล้ว ${fmtN(P.rounds_delivered || 0)} รอบ`)
    + msKpiTile('ยอดซื้อเทียบยอดขาย' + (upto ? ` (${esc(upto)})` : ''), _frcPct(P.pct), '#7a66b8', ratioSub)
    + msKpiTile('ส่งหลังเวลาตัดรอบ', `${fmtN(L.count || 0)} ใบ`, L.count ? '#f59e0b' : '#10b981', L.count ? '(มีค่าปรับ)' : 'ทันเวลาทุกใบ')
    + `</div>`;
  const rounds = d.rounds || [];
  const tbl = rounds.length ? `<div class="frc-sub2">รอบส่งของเดือนนี้</div><div class="frc-tbl-wrap"><table class="frc-tbl"><thead><tr><th>รอบ · สถานะ</th>`
    + `<th class="n">สั่ง (฿)</th><th class="n">จัดส่ง (฿)</th></tr></thead><tbody>`
    + rounds.map(r => `<tr><td>${esc(r.round_th)}<br><span class="frc-dow">${esc(r.status_th || '')}</span>`
      + `${r.late ? ' <span class="frc-dow" style="color:var(--amber-ink)">· ส่งหลังเวลาตัดรอบ</span>' : ''}</td>`
      + `<td class="n">${r.ordered == null ? _FRC_NA : fmtN(r.ordered)}</td><td class="n">${r.delivered == null ? _FRC_NA : fmtN(r.delivered)}</td></tr>`).join('')
    + `</tbody></table></div>`
    : `<div class="ms-card" style="margin-top:10px"><div class="ms-row" style="${_FRC_MUTED};font-size:13px">ยังไม่มีรอบส่งในเดือนนี้</div></div>`;
  return _frcCard(title, 'supply', help, 'ยอดสั่งและยอดที่คลังจัดส่งของแต่ละรอบ ยอดซื้อเทียบยอดขาย เคลมสินค้า และวัตถุดิบที่ใช้เกินสูตร',
    tiles + tbl + _frcClaims(C) + _frcOveruse(d.overuse));
}

function _frcQty(v) { return v == null ? "—" : Number(v).toLocaleString("th-TH", { maximumFractionDigits: 2 }); }
function _frcClaimInk(st) {
  return st === 'approved' ? 'var(--good-ink)' : st === 'rejected' ? 'var(--text-secondary)' : 'var(--amber-ink)';
}
function _frcClaims(c) {
  if (!c) return '';
  const pills = (c.by_status || []).map(x => `<span class="frc-pill">${esc(x.status_th)} ${fmtN(x.count || 0)}</span>`).join(' ');
  const head = `<div class="ms-row" style="flex-wrap:wrap;gap:6px"><span style="flex:1 1 100%;font-size:13px;color:var(--text-primary)">แจ้งเคลมในเดือนนี้ <b>${fmtN(c.count || 0)}</b> ครั้ง`
    + `${c.approved_value ? ` · มูลค่าที่อนุมัติ ฿${fmtN(c.approved_value)}` : ''}</span>${pills}</div>`;
  const list = (c.latest || []).map(x => `<div class="ms-row" style="flex-direction:column;align-items:stretch;gap:3px">`
    + `<span style="display:flex;gap:8px;font-size:12px;${_FRC_MUTED}"><span>${esc(_frcDay(x.date))}${x.round_th ? ' · ' + esc(x.round_th) : ''}</span>`
    + `<span style="margin-left:auto;font-weight:600;color:${_frcClaimInk(x.status)}">${esc(x.status_th)}</span></span>`
    + (x.items || []).map(i => `<span style="font-size:13px;color:var(--text-primary)">${esc(i.name)} · ${i.qty == null ? '' : esc(_frcQty(i.qty))} ${esc(i.unit || '')} <span style="${_FRC_MUTED}">· ${esc(i.reason_th || '')}</span></span>`).join('')
    + (x.decision_note ? `<span style="font-size:12px;color:var(--text-secondary)">ผลพิจารณา: ${esc(x.decision_note)}</span>` : '')
    + `</div>`).join('');
  return `<div class="frc-sub2">เคลมสินค้า</div><div class="ms-card">${head}${list}</div>`;
}

function _frcOveruse(o) {
  if (!o) return '';
  const rows = o.rows || [];
  const when = `รอบตรวจนับ ${_frcDay(o.begin)} – ${_frcDay(o.end)}`;
  const t = '<div class="frc-sub2">วัตถุดิบที่ใช้เกินสูตร 5 อันดับ</div>';
  if (!rows.length) return t + `<div class="ms-card"><div class="ms-row" style="${_FRC_MUTED};font-size:13px">${esc(when)} · ไม่มีวัตถุดิบที่ใช้เกินสูตร</div></div>`;
  return t + `<p class="frc-sub" style="margin-top:0">${esc(when)} · เรียงตามมูลค่า</p>`
    + `<div class="frc-tbl-wrap"><table class="frc-tbl"><thead><tr><th>วัตถุดิบ</th><th class="n">ใช้เกิน</th><th class="n">มูลค่า (฿)</th><th class="n">เกินสูตร</th></tr></thead><tbody>`
    + rows.map(r => `<tr><td>${esc(r.name)}</td><td class="n">${r.qty == null ? _FRC_NA : _frcQty(r.qty)} ${esc(r.unit || '')}</td>`
      + `<td class="n">${r.value == null ? _FRC_NA : fmtN(r.value)}</td><td class="n">${_frcPct(r.pct)}</td></tr>`).join('')
    + `</tbody></table></div>`;
}
