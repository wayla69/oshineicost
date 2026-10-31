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
// PR-F4 (2026-10-22): ติดต่อสำนักงานใหญ่ - the owner's questions to head office and the replies (frcMsg*, the card #frcMsg at the top of the
// page, GET/POST /api/franchise/messages*), a "สอบถามเรื่องนี้" button beside each ควรดู / ต้องแก้ line of the verdict. Head office's inbox
// is js/19-fmsg.js, which draws with the pure helpers here (_fmRowHtml, _fmMessagesHtml, ...). Every string a person typed is drawn through
// esc() - never innerHTML of raw text.
// สรุปประจำวัน (owner 2026-10-27, mockup B): the card at the top of the page (#frcDay), from GET /api/franchise/daily - the branch's last
// FINISHED day (navy band: net sales against the same weekday last week; tiles: customers, per head, food cost against the ONE target,
// discount against the ceiling) and its month so far (against last month, last year and the target; voided bills of the month). It does not
// follow the top bar's period: it is always the last finished day. No other branch, no chain figure.
// Phone B for the owner (owner 2026-10-31, "เริ่มเลย"; docs/PHONE_PLAN.md §11): the page is OVERVIEW CARDS first (#frcCards: เมื่อวาน ·
// เดือนนี้ · เป้า · ต้นทุนอาหาร · ควรดู · การควบคุม · เมนู · เสียงลูกค้า · ติดต่อสำนักงานใหญ่; a card with nothing to show is left out), and a
// card opens its DETAIL SCREEN in place: the page's own sections (.frc-sec, each naming the details it belongs to in data-d) are shown only
// under the open detail (#frcDetail.d-<name>, CSS in index.html). Opening pushes a history entry so the back arrow of the head and the
// phone's back button return to the cards where they were left (popstate). One code path for the phone and the desktop (3 columns there).
// The figures come from ONE call, GET /api/franchise/overview (summary + insight + เรื่องที่ควรดู + the ควรดู findings, each finding with
// the detail and the row it goes to: frcGo + uiFlash); สรุปประจำวัน keeps its own call. ฉัน holds แจ้งเตือนทุกเช้า and ดาวน์โหลดและพิมพ์
// (#frcMe, moved into the phone's ฉัน view; under the cards on a desktop).

let _frcMonth = '';
let _frcGen = 0;
let _frcBranch = '';
let _frcS = { sum: null, ins: null, dec: null, find: null, day: null, err: '' };     // the payloads the cards and the details are drawn from

const _FRC_MUTED = 'color:var(--text-muted)';
const _FRC_NA = `<span style="${_FRC_MUTED}">—</span>`;

function _frcBaht(v) { return v == null ? _FRC_NA : '฿' + fmtN(v); }
function _frcPct(v, d = 1) { return v == null ? _FRC_NA : Number(v).toFixed(d).replace(/\.0+$/, '') + '%'; }
function _frcDay(iso) { return iso ? msPeriodLabel(iso, 'day') : ''; }

// DV6 (owner 2026-10-25: "อย่าลืมให้ filter ได้ด้วย"): the page follows the top bar's ONE period (TABS.franchise.follow.period, kinds
// สัปดาห์ · เดือน; on a phone the period chip by the page title opens it). The month cards read the month that holds the end of that
// period; เรื่องที่ควรดู reads the period itself. The period of the anchor (the default, to date) sends no month / pick, so the server
// answers on the branch's own last closed day.
function _frcPeriod() {
  const g = typeof gPeriodGet === 'function' ? gPeriodGet() : { kind: 'month', pick: '' };
  const r = typeof gPeriodResolve === 'function' ? gPeriodResolve() : null;
  const past = !!(g.pick && r && r.complete);
  return { kind: g.kind === 'week' ? 'week' : 'month', pick: past ? r.cur[1] : '', month: past ? r.cur[1].slice(0, 7) : '' };
}
async function loadFranchise() {
  const gen = ++_frcGen;
  const nm = document.getElementById('frcBranchName');
  if (nm) nm.textContent = pinnedBranchName() || nm.textContent;
  _frcBranch = (nm && nm.textContent) || '';
  const st = document.getElementById('frcStatus'), body = document.getElementById('frcBody');
  if (st) st.textContent = '';
  const per = _frcPeriod();
  _frcMonth = per.month;
  _frcMeMount();
  _frcHead();
  if (!_frcS.sum) _frcCardsRender();             // the first load: grey cards (uiSkel) until the figures come
  frcShowDay(gen, _frcGetJson('/api/franchise/daily'));      // สรุปประจำวัน: the last finished day, whatever the period (its own slot)
  frcPeriodChip(null);
  frcPushInit();
  if (_fmView.mode === 'list' || !_fmData) frcMsgLoad();
  // ONE call for the cards: the month (summary + insight) and เรื่องที่ควรดู of the top bar's period, and the ควรดู findings
  const qs = new URLSearchParams({ kind: per.kind });
  if (per.pick) qs.set('pick', per.pick);
  if (_frcMonth) qs.set('month', _frcMonth);
  let d;
  try { d = await _frcGetJson('/api/franchise/overview?' + qs); }
  catch (e) {
    if (gen !== _frcGen) return;
    _frcS = Object.assign(_frcS, { sum: null, ins: null, dec: null, find: null, err: (e && e.message) || 'โหลดข้อมูลไม่สำเร็จ' });
    if (body) body.innerHTML = '';
    _frcCardsRender();
    return;
  }
  if (gen !== _frcGen) return;
  const s = d.summary || {};
  _frcS = Object.assign(_frcS, { sum: s, ins: d.insight || null, dec: d.decide || null, find: d.findings || [], err: '' });
  if (nm) nm.textContent = s.branch || nm.textContent;
  _frcBranch = (nm && nm.textContent) || _frcBranch;
  _frcMonth = s.month || '';
  if (st) st.textContent = s.label || '';
  const dl = document.getElementById('frcDownload');
  if (dl) dl.disabled = !s.available;
  if (body) body.innerHTML = _frcRender(s, d.insight, d.decide, d.findings || []);
  const P = d.decide && d.decide.period;
  if (P && typeof periodChip === 'function') periodChip(P, true);    // the chip shows the server's own period
  frcPeriodChip(P || null);
  if (s.available) frcSupplyWatch(gen);          // วัตถุดิบจากส่วนกลาง loads when the ต้นทุนอาหาร detail opens (it is hidden until then)
  _frcCardsRender();
  _frcHead();
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


// ── drawing ──────────────────────────────────────────────────────────────────
function _frcSection(title, inner) {
  return `<div class="ms-section"><div class="ms-section-title">${esc(title)}</div>${inner}</div>`;
}
function _frcRow(label, value, sub, id) {
  return `<div class="ms-row"${id ? ` id="${id}"` : ''}><span style="flex:1;min-width:0;font-size:13px;color:var(--text-primary)">${esc(label)}${sub ? `<br><span style="font-size:11px;${_FRC_MUTED}">${esc(sub)}</span>` : ''}</span>`
    + `<span style="font-size:14px;font-weight:600;text-align:right;font-variant-numeric:tabular-nums;color:var(--text-primary)">${value}</span></div>`;
}
const _FRC_TILES = 'display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px';

let _frcVerdictLast = null;     // PR-F4: the lines a "สอบถามเรื่องนี้" button refers to
function _frcVerdict(v) {
  _frcVerdictLast = v || null;
  const ask = franchiseLogin();     // only the owner writes to head office
  const groups = [['good', 'ดี', 'var(--good-ink)'], ['watch', 'ควรดู', 'var(--amber-ink)'], ['fix', 'ต้องแก้', 'var(--bad-ink)']];
  const rows = groups.map(([k, lbl, ink]) => {
    const items = (v && v[k]) || [];
    // PL3 (RV3 F21): short lines, not paragraphs - each item is one sentence, the repeated "สอบถามเรื่องนี้" is a one-word "ถาม" chip,
    // and the note under the list is behind the "?" of the heading
    const btn = i => ask && k !== 'good' ? `<button type="button" class="frc-ask" aria-label="สอบถามเรื่องนี้" title="สอบถามเรื่องนี้" onclick="frcAskAbout('${k}',${i})">ถาม</button>` : '';
    return `<div class="ms-row" style="align-items:flex-start;gap:10px"><span style="flex:0 0 44px;font-size:13px;font-weight:700;color:${ink}">${lbl}</span>`
      + `<span style="flex:1;min-width:0;font-size:13px;line-height:1.5;color:var(--text-primary)">`
      + (items.length ? items.map((t, i) => `<span style="display:flex;align-items:flex-start;gap:8px;margin-bottom:3px"><span style="flex:1;min-width:0">${esc(t)}</span>${btn(i)}</span>`).join('') : `<span style="${_FRC_MUTED}">ไม่มีรายการ</span>`)
      + `</span></div>`;
  }).join('');
  const q = v && v.note ? ' ' + uiQ('frc:verdict', 'สรุปของเดือนนี้', `<p>${esc(v.note)}</p>`) : '';
  return `<div class="ms-section"><div class="ms-section-title">สรุปของเดือนนี้${q}</div><div class="ms-card">${rows}</div></div>`;
}

function _frcSales(s, disc) {
  if (!s) return _frcSection('ยอดขาย', `<div class="ms-card"><div class="ms-row" style="${_FRC_MUTED}">ยังไม่มีข้อมูลยอดขายของเดือนนี้</div></div>`);
  const series = s.series_30d || [];
  const vals = series.map(x => x.revenue);
  const cols = vals.filter(v => v != null).length >= 3
    ? `<div style="margin-top:10px">${msColumns(vals, { partial: -1, labels: series.map(x => _frcDay(x.day)), fmt, grain: 'day', height: 92,
        weekend: series.map(x => [0, 6].includes(msDow(x.day))), aria: 'ยอดขายรายวัน 30 วันล่าสุด' })}</div>` : '';
  const yoy = s.mtd_vs_last_year_pct != null ? ` <span style="font-size:12px;${_FRC_MUTED}">· ปีก่อน</span> ${msPchip(s.mtd_vs_last_year_pct)}` : '';
  const basis = s.month_complete ? '' : ' (เทียบวันที่ 1 ถึงวันเดียวกัน)';
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
    + msKpiTile('ต้นทุนอาหาร', _frcPct(g.food_cost_pct), '#f59e0b', stkWord('ของยอดขาย ตามสูตร', 'ของยอดขาย ตามต้นทุนต่อจาน'))
    + `</div>` + _frcCoverage(g.coverage));
}

// the food-cost floor: a dish with no costed recipe counts ฿0, so the food cost above understates (rules.coverage_sums_sql)
function _frcCoverage(c) {
  if (!c || !c.floor) return '';
  const plates = c.uncosted_plates_pct, sales = c.uncosted_sales_pct;
  const help = '<p>' + stkWord('ต้นทุนอาหารคำนวณจากสูตรของแต่ละเมนู', 'ต้นทุนอาหารคิดจากต้นทุนต่อจานที่ฝ่ายวิเคราะห์นำเข้าของแต่ละเมนู') + ' เมนูที่ยังไม่มีต้นทุนนับเป็น 0 บาท ต้นทุนอาหารที่แสดงจึงเป็นค่าต่ำสุด และกำไรขั้นต้นอาจสูงกว่าความเป็นจริง</p>'
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
    + '<p>เทียบปกติ = ยอดขายของวันนั้นเทียบกับค่ากลางของวันเดียวกันใน 8 สัปดาห์ก่อน เช่น วันจันทร์เทียบค่ากลางของวันจันทร์ 8 สัปดาห์ '
    + 'วันที่ร้านไม่มียอดขายไม่นำมาคิด ถ้ามีข้อมูลน้อยกว่า 2 สัปดาห์จะยังไม่เทียบ</p>';
  const body = rows.map(x => {
    const we = ['เสาร์', 'อาทิตย์'].includes(x.weekday);
    return `<tr id="frcD-${esc(x.date)}"${we ? ' class="frc-we"' : ''}><td>${esc(_frcDay(x.date))} <span class="frc-dow">${esc(x.weekday || '')}</span></td>`
      + `<td class="n">${x.revenue == null ? _FRC_NA : fmtN(x.revenue)}</td><td class="n">${_frcDelta(x.vs_normal_pct)}</td>`
      + `<td class="n">${x.bills == null ? _FRC_NA : fmtN(x.bills)}</td><td class="n">${x.customers == null ? _FRC_NA : fmtN(x.customers)}</td>`
      + `<td class="n">${x.per_bill == null ? _FRC_NA : fmtN(x.per_bill)}</td></tr>`;
  }).join('');
  return `<div class="ms-section"><div class="ms-section-title frc-title">ยอดขายรายวัน ${uiQ('frc:daily', 'ยอดขายรายวัน', help)}</div>`
    + `<div class="frc-tbl-wrap"><table class="frc-tbl"><thead><tr><th>วันที่</th><th class="n">ยอดขาย (฿)</th><th class="n">เทียบปกติ</th><th class="n">บิล</th>`
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
    + `<p class="sub">${msPchip(L.mom_pct)} <span style="font-size:12px;${_FRC_MUTED}">เทียบเดือนก่อน${(L.mom_basis === 'to_date' || L.mom_basis === 'same_days') ? ' (วันที่ 1 ถึงวันเดียวกัน)' : ''}</span>${yoy}</p>${chart}</div>`;
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
  const stock = BC_MODE === 'stock';
  let period;
  if (stock) {
    // F5: no recipes in stock mode - the gap is the system stock against the month-end count
    period = lp
      ? _frcRow('รอบตรวจนับล่าสุด', `${_frcDay(lp.begin)} – ${_frcDay(lp.end)}`, lp.reconciled ? 'บันทึกรับของครบแล้ว' : 'ยังไม่ได้แจ้งว่าบันทึกรับของครบ')
        + _frcRow('ส่วนต่างการใช้วัตถุดิบ', lp.diff_pct == null ? _FRC_NA : `${lp.diff_pct > 0 ? '+' : ''}${_frcPct(lp.diff_pct)} (${lp.diff_value > 0 ? '+' : ''}${_frcBaht(lp.diff_value)})`,
            lp.diff_pct == null ? '' : (lp.diff_pct > 0 ? 'นับได้มากกว่าระบบ' : lp.diff_pct < 0 ? 'นับได้น้อยกว่าระบบ' : 'ตรงกับระบบ'))
      : _frcRow('ส่วนต่างการใช้วัตถุดิบ', _FRC_NA, 'ยังไม่มีผลนับสิ้นเดือน');
  } else {
    period = lp
      ? _frcRow('รอบตรวจนับล่าสุด', `${_frcDay(lp.begin)} – ${_frcDay(lp.end)}`, lp.reconciled ? 'บันทึกรับของครบแล้ว' : 'ยังไม่ได้แจ้งว่าบันทึกรับของครบ')
        + _frcRow('ส่วนต่างการใช้วัตถุดิบเทียบสูตร', lp.diff_pct == null ? _FRC_NA : `${lp.diff_pct > 0 ? '+' : ''}${_frcPct(lp.diff_pct)} (${lp.diff_value > 0 ? '+' : ''}${_frcBaht(lp.diff_value)})`,
            lp.diff_pct == null ? '' : (lp.diff_pct > 0 ? 'ใช้เกินสูตร' : lp.diff_pct < 0 ? 'ใช้น้อยกว่าสูตร' : 'ตรงสูตร'))
      : _frcRow('รอบตรวจนับล่าสุด', _FRC_NA, 'ยังไม่มีรอบตรวจนับที่เปรียบเทียบได้');
  }
  const help = '<p>ส่วนต่างการใช้วัตถุดิบ = มูลค่าที่นับได้จริงลบยอดคงเหลือตามระบบของรอบสิ้นเดือนล่าสุด เทียบเป็นร้อยละของมูลค่าวัตถุดิบที่เบิกใช้ในรอบนั้น · ค่าติดลบคือนับได้น้อยกว่าระบบ</p>';
  const inner = `<div class="ms-card">${period}`
    + _frcRow('ของเสียในเดือนนี้', _frcPct(i.waste_pct_month), 'สัดส่วนต่อมูลค่าวัตถุดิบที่เบิกใช้')
    + _frcRow(stkWord('เบิกเกินสูตรติดต่อกันหลายวัน', 'เบิกเกินปกติติดต่อกันหลายวัน'), i.streak_flag ? `<span style="color:var(--bad-ink)">พบ ${fmtN(i.streak_days)} วัน</span>` : `<span style="color:var(--good-ink)">ไม่พบ</span>`)
    + `</div>`;
  return stock ? _frcCard('วัตถุดิบ', 'ingredients', help, '', inner) : _frcSection('วัตถุดิบ', inner);
}

function _frcDiscipline(d) {
  if (!d) return '';
  if (!d.module_active) return _frcSection('วินัยการทำงานของสาขา', `<div class="ms-card"><div class="ms-row" style="${_FRC_MUTED}">สาขายังไม่ได้เริ่มใช้ระบบตัดเบิกและสั่งของ</div></div>`);
  return _frcSection('วินัยการทำงานของสาขา', `<div class="ms-card">`
    + _frcRow('ใบตัดเบิกที่ส่งแล้ว', _frcPct(d.slip_coverage_pct, 0), 'ต่อจำนวนวันที่เปิดขาย')
    + _frcRow('ใบตัดเบิกที่ส่งตรงวัน', _frcPct(d.slip_on_time_pct, 0), 'ต่อจำนวนใบที่ส่งแล้ว')
    + _frcRow('ใบสั่งซื้อที่ส่งทันเวลาตัดรอบ', _frcPct(d.orders_on_time_pct, 0))
    + _frcRow('การบันทึกรับของทันกำหนด', _frcPct(d.receipts_on_time, 0), 'ภายในวันถัดจากวันส่ง')
    + _frcRow('การแจ้งเคลมสินค้าในเดือนนี้', d.claims_month == null ? _FRC_NA : `${fmtN(d.claims_month)} ครั้ง`)
    + `</div>`);
}

// phone review item 3: the top 10 as two-line rows (uiRow2) - the dish and its plates, a bar against the first
function _frcMenus(list) {
  if (!list || !list.length) return '';
  const maxQ = Math.max(1, ...list.map(x => x.plates || 0));
  return _frcSection('เมนูขายดี 10 อันดับ (จำนวนจาน)', `<div class="ms-card">` + list.map((x, i) => uiRow2({
    title: `${i + 1}. ${esc(x.name)}`, val: `${fmtN(x.plates || 0)} จาน`, bar: { pct: 100 * (x.plates || 0) / maxQ, col: 'var(--navy-ink)' },
    fact: i === 0 ? 'ขายดีที่สุดของเดือน' : `${_frcPctText(100 * (x.plates || 0) / maxQ)} ของอันดับ 1` })).join('') + `</div>`);
}

// ── phone B (owner 2026-10-31): the sections of the details ─────────────────────────────────────────────────────────
// A section names the detail screens it is shown on (data-d, space separated); #frcDetail.d-<name> shows them (index.html).
function _frcId(v) { return String(v == null ? '' : v).replace(/[^\w-]/g, ''); }      // a detail name / element id inside an onclick
function _frcSec(d, html, id) { return html ? `<div class="frc-sec" data-d="${d}"${id ? ` id="${id}"` : ''}>${html}</div>` : ''; }
// a block that answers a second question folds (phone review item 11: one chart per question)
function _frcFold(label, html) { return html ? `<details class="ph-more frc-fold"><summary>${esc(label)}</summary>${html}</details>` : ''; }
function _frcRender(d, ins, dec, find) {
  ins = ins || {}; dec = dec || {};
  _frcDecVs = dec.available ? _frcDecLabel(dec.period) : '';
  if (!dec.available) dec = {};
  if (!d.available) {
    return _frcSec('watch month', uiEmpty({ t: 'ยังไม่มีข้อมูลของเดือนนี้ เลือกช่วงเวลาอื่นได้จากช่วงเวลาด้านบน', icon: 'inbox' }));
  }
  const ok = !!ins.available;
  return _frcSec('watch', _frcFindings(find)) + _frcSec('watch', _frcVerdict(d.verdict))
    + _frcSec('month', _frcSales(d.sales, d.discount_pct)) + _frcSec('yesterday month', _frcDaily(d.sales))
    + _frcSec('month', _frcFold('แนวโน้ม 12 เดือน และ 6 สัปดาห์ล่าสุด', _frcTrend(d.trend_12m) + _frcWeeks(dec.weeks)))
    + _frcSec('target', _frcTarget(d.target))
    + _frcSec('cost', _frcGp(d.gp), 'frcGp') + _frcSec('cost', _frcFoodCost(dec.food_cost) + _frcCostly(dec.costly))
    + _frcSec('cost', _frcIngredients(d.ingredients))
    + `<div class="frc-sec" data-d="cost" id="frcSupply">${uiSkel(3)}</div>`
    + _frcSec('controls', ok ? _frcControls(ins.controls) : '') + _frcSec('controls', _frcDiscipline(d.discipline))
    + _frcSec('menu', _frcMenus(d.top_menus)) + _frcSec('menu', _frcMenusUp(dec.menus))
    + _frcSec('menu', ok ? _frcFold('สัดส่วนการขาย และช่วงเวลาขายดี', _frcMix(ins.mix) + _frcPeak(ins.peak)) : '')
    + _frcSec('voice', _frcVoice(ins.voice), 'frcVoice');
}

// ควรดู: the findings of THIS branch (GET /api/franchise/overview findings, built on the server) as two-line rows that GO where the
// work is - a tap opens the detail and lights the row it is about (frcGo → uiFlash); ▾ reads the full sentence
function _frcFindings(list) {
  const help = '<p>เรื่องที่ควรดูของสาขาในเดือนนี้ เรียงเรื่องที่ต้องแก้ก่อน แตะแถวเพื่อไปยังตัวเลขของเรื่องนั้น แตะ ▾ เพื่ออ่านประโยคเต็ม</p>'
    + '<p>เกณฑ์: ส่วนลดเกินเพดาน · บิลยกเลิกเกินร้อยละ 0.5 ของบิล (เกินร้อยละ 1 ต้องแก้) · บิลที่มีส่วนลดและพิมพ์ซ้ำ · เงินสดขาด · '
    + 'ต้นทุนอาหารเกินเป้า · ของแพงต่อลูกค้าสูงกว่าเฉลี่ยทุกสาขาตั้งแต่ร้อยละ 10 · วันที่ยอดต่ำกว่าปกติตั้งแต่ร้อยละ 8 · รีวิวที่ยังไม่มีคำตอบหรือรีวิวติ</p>';
  const rows = (list || []).map(f => uiRow2({
    tone: f.level === 'fix' ? 'bad' : 'watch', title: esc(f.title), val: esc(f.val), valTone: f.level === 'fix' ? 'bad' : '',
    chip: { t: f.chip, tone: f.level === 'fix' ? 'bad' : 'watch' }, fact: esc(f.fact || ''), more: f.more ? esc(f.more) : '',
    onGo: `frcGo('${_frcId((f.go || {}).d)}','${_frcId((f.go || {}).el)}')`, attrs: ` data-kind="${esc(f.kind)}"` }));
  return `<div class="ms-section" id="frcFind"><div class="ms-section-title frc-title">เรื่องที่ควรดู ${uiQ('frc:find', 'เรื่องที่ควรดู', help)}</div>`
    + (rows.length ? `<div class="ms-card">${rows.join('')}</div>` : uiEmpty({ t: 'ไม่มีเรื่องที่ต้องดูในเดือนนี้', icon: 'check' })) + `</div>`;
}

// ── the overview cards (#frcCards). A card with nothing to show is left out; the order is the owner's question order. ──
const _FRC_DT = { yesterday: 'เมื่อวาน', month: 'เดือนนี้', target: 'เป้ายอดขาย', cost: 'ต้นทุนอาหาร', watch: 'ควรดู', controls: 'การควบคุม',
  menu: 'เมนู', voice: 'เสียงลูกค้า', msg: 'ติดต่อสำนักงานใหญ่' };
let _frcDecVs = '';
let _frcDetail = '', _frcPushed = false, _frcOvY = [0, 0];
// where the cards were left: the window and the app's own scroller (.content-area, the phone page view scrolls inside it)
function _frcScrollY() { const ca = document.querySelector('.content-area'); return [window.scrollY, ca ? ca.scrollTop : 0]; }
// one cause line under เมื่อวาน: what pulled the day - food cost over the target, then the discount over the ceiling, else per head
function _frcDayCause(D) {
  if (D.food_cost_pct != null && D.food_cost_target_pct != null && D.food_cost_pct > D.food_cost_target_pct) {
    return `<b class="is-bad">ต้นทุนอาหาร ${_frcPctText(D.food_cost_pct)} เกินเป้า</b>`;
  }
  if (D.discount_level === 'fix' || D.discount_level === 'watch') return `<b class="is-bad">ส่วนลด ${_frcPctText(D.discount_pct)} เกินเพดาน</b>`;
  return D.per_head != null ? `<span>ต่อหัว ฿${fmtN(D.per_head)}</span>` : '';
}
// S = _frcS; M = the messages ({ on, unread, threads }). Pure: no DOM, no call (backend/test_franchise_b.py runs it under node).
function _frcCardsHtml(S, M) {
  const cards = [];
  const card = (d, tone, label, val, sub, o) => cards.push(`<div class="ph-ov-card${tone ? ' is-' + tone : ''}${o && o.wide ? ' ph-ov-wide' : ''}" role="button" tabindex="0" data-d="${d}" id="frcCard-${d}"`
    + ` onclick="frcOpen('${d}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();frcOpen('${d}')}">`
    + `<span class="ph-ov-l">${label}</span><span class="ph-ov-v${o && o.ink ? ' is-' + o.ink : ''}${o && o.sm ? ' frc-ov-sm' : ''}">${val}</span>${sub ? `<span class="ph-ov-s">${sub}</span>` : ''}</div>`);
  const safe = fn => { try { fn(); } catch (e) { console.error('franchise card', e); } };   // one card that fails never blanks the others
  const day = S.day && S.day.available && S.day.day;
  safe(() => {
    if (!day) return;
    const D = day;
    const pill = (D.normal_weeks || 0) >= 2 ? `${msPchip(D.vs_normal_pct)}<span>${esc(normalLbl(D.normal_weeks, true))}</span>` : `<span>${esc(normalLbl(D.normal_weeks, true))}</span>`;
    card('yesterday', D.vs_normal_pct == null ? '' : D.vs_normal_pct < 0 ? 'bad' : 'good', `เมื่อวาน · ${esc(D.weekday || '')} ${esc(_frcDay(D.date))}`, _frcBaht(D.revenue),
      `${pill}<span>ลูกค้า ${fmtN(D.customers || 0)} คน · ${fmtN(D.bills || 0)} บิล</span>${_frcDayCause(D)}`, { wide: true });
  });
  const s = S.sum;
  if (!s || !s.available) {
    if (S.err) return cards.join('') + `<div class="ph-ov-wide">${uiEmpty({ t: S.err, icon: 'inbox' })}</div>`;
    if (!s) return cards.join('') + `<div class="ph-ov-wide">${uiSkel(4, 'kpi')}</div>`;
    return cards.join('') + `<div class="ph-ov-wide">${uiEmpty({ t: 'ยังไม่มีข้อมูลของเดือนนี้ เลือกช่วงเวลาอื่นได้จากช่วงเวลาด้านบน', icon: 'inbox' })}</div>`;
  }
  const sl = s.sales || {};
  safe(() => {
    if (sl.mtd == null) return;
    card('month', '', sl.month_complete ? `ทั้งเดือน ${esc(s.label || '')}` : 'เดือนนี้ (สะสม)', _frcBaht(sl.mtd),
      `${msPchip(sl.mtd_vs_last_month_pct)}<span>เทียบเดือนก่อน</span>`);
  });
  safe(() => {
    const t = s.target;
    if (!t || t.source === 'none' || t.month_target == null) return;          // no target = no card
    const behind = t.vs_to_date_pct != null && t.vs_to_date_pct < 0, tone = t.vs_to_date_pct == null ? '' : behind ? 'bad' : 'good';
    const sub = t.need_per_day != null
      ? (t.need_per_day <= 0 ? '<b class="is-good">ถึงเป้าแล้ว</b>'
        : `<span>ต้องขายวันละ</span><b class="${t.need_per_day > (t.normal_per_day || 0) * 1.02 ? 'is-bad' : 'is-good'}">฿${fmtN(t.need_per_day)}</b>`)
      : `<span>${t.month_complete ? 'ของเป้าทั้งเดือน' : 'ของเป้าเดือนนี้'}</span>`;
    card('target', tone, 'เป้า', t.of_month_pct != null ? Math.round(t.of_month_pct) + '%' : '—', sub, { ink: tone });
  });
  safe(() => {
    const g = s.gp || {};
    if (g.food_cost_pct == null) return;
    const tgt = g.food_cost_target_pct, over = tgt != null && g.food_cost_pct > tgt;
    card('cost', tgt == null ? '' : over ? 'bad' : 'good', 'ต้นทุนอาหาร', _frcPctText(g.food_cost_pct),
      tgt == null ? '<span>ของยอดขาย</span>' : `<b class="${over ? 'is-bad' : 'is-good'}">${over ? 'เกินเป้า' : 'ในเป้า'}</b><span>เป้า ≤${_frcPctText(tgt)}</span>`, { ink: over ? 'bad' : '' });
  });
  safe(() => {
    const F = S.find || [], fix = F.filter(f => f.level === 'fix').length;
    // red bar when any: the count of findings; with none the card stays (สรุปของเดือนนี้ and its "ถาม" sit on the same detail)
    card('watch', fix ? 'bad' : F.length ? 'watch' : 'good', 'ควรดู', F.length ? `${fmtN(F.length)} เรื่อง` : 'ไม่มี',
      F.length ? `<span>${esc(F.slice(0, 2).map(f => f.title).join(' · '))}</span>` : '<span>สรุปของเดือนนี้</span>', { ink: fix ? 'bad' : '' });
  });
  const ins = S.ins || {}, C = ins.available ? ins.controls : null;
  safe(() => {
    if (!C) return;
    const D = C.discount || {}, V = C.voids || {}, R = C.reprints || {}, lv = (C.flags || {}).level;
    const parts = [V.rate_pct != null ? `ยกเลิก ${_frcPctText(V.rate_pct)}` : '', R.bills ? `พิมพ์ซ้ำ ${fmtN(R.bills)} บิล` : ''].filter(Boolean);
    card('controls', lv === 'fix' ? 'bad' : lv === 'watch' ? 'watch' : 'good', 'การควบคุม', `ส่วนลด ${_frcPctText(D.pct)}`,
      parts.map(p => `<span>${p}</span>`).join('') || '<span>ส่วนลด บิลยกเลิก เงินสด</span>');
  });
  safe(() => {
    const top = (s.top_menus || [])[0];
    if (!top) return;
    card('menu', '', 'เมนู', esc(top.name), `<span>${fmtN(top.plates || 0)} จาน · อันดับ 1</span><b>ดูทั้งหมด</b>`, { sm: true });
  });
  safe(() => {
    const v = ins.voice;
    if (!v || !v.available) return;
    const un = v.unreplied || 0;
    card('voice', un ? 'watch' : '', 'เสียงลูกค้า', un ? `${fmtN(un)} รอตอบ` : (v.rating_avg != null ? `${Number(v.rating_avg).toFixed(1)} ดาว` : `${fmtN(v.reviews_month || 0)} รีวิว`),
      `<span>รีวิวเดือนนี้ ${fmtN(v.reviews_month || 0)}</span>${v.negative ? `<b class="is-bad">ติ ${fmtN(v.negative)}</b>` : ''}`);
  });
  safe(() => {
    if (!M || !M.on) return;
    card('msg', M.unread ? 'watch' : '', 'ติดต่อสำนักงานใหญ่', M.unread ? `ใหม่ ${fmtN(M.unread)}` : `${fmtN(M.threads || 0)} หัวข้อ`,
      M.unread ? '<span>คำตอบที่ยังไม่ได้อ่าน</span>' : '<span>ส่งคำถามถึงสำนักงานใหญ่</span>');
  });
  return cards.join('');
}
function _frcCardsRender() {
  const box = document.getElementById('frcCards'); if (!box) return;
  const M = { on: franchiseLogin(), unread: (_fmData && _fmData.unread) || 0, threads: ((_fmData && _fmData.threads) || []).filter(t => t.status !== 'closed').length };
  box.innerHTML = _frcCardsHtml(_frcS, M);
}

// ── a detail screen: the head's back arrow and the phone's back button return to the cards (history entry {frd}) ──
function frcOpen(d) {
  if (!_FRC_DT[d]) return;
  if (!_frcDetail) _frcOvY = _frcScrollY();
  try {
    if (_frcPushed && history.state && history.state.frd) history.replaceState({ frd: d }, '');
    else { history.pushState({ frd: d }, ''); _frcPushed = true; }
  } catch (e) { /* no history (a sandboxed frame): the back arrow still closes */ }
  _frcShow(d);
  window.scrollTo(0, 0);
  const ca = document.querySelector('.content-area'); if (ca) ca.scrollTop = 0;
}
function _frcShow(d) {
  _frcDetail = d || '';
  const tab = document.getElementById('tab-franchise'), dt = document.getElementById('frcDetail');
  if (tab) tab.classList.toggle('frc-in-detail', !!_frcDetail);
  if (dt) dt.className = 'frc-dt' + (_frcDetail ? ' d-' + _frcDetail : '');
  _frcHead();
  if (_frcDetail === 'msg' && !_fmData) frcMsgLoad();
}
function frcBack() {
  if (!_frcDetail) return;
  if (_frcPushed && history.state && history.state.frd) { history.back(); return; }
  _frcToCards();
}
function _frcToCards() {
  _frcPushed = false;
  _frcShow('');
  const y = _frcOvY;
  requestAnimationFrame(() => { window.scrollTo(0, y[0]); const ca = document.querySelector(".content-area"); if (ca) ca.scrollTop = y[1]; });
}
// หน้าแรก of the bottom bar (phOpenFranchise) while a detail is open: back to the cards
function frcHome() { if (_frcDetail) frcBack(); }
window.addEventListener('popstate', e => {
  if (typeof activeTab === 'undefined' || activeTab !== 'franchise') return;
  const d = e.state && e.state.frd;
  if (d && _FRC_DT[d]) { if (!_frcDetail) _frcOvY = _frcScrollY(); _frcPushed = true; _frcShow(d); return; }
  if (_frcDetail) _frcToCards(); else _frcPushed = false;
});
// a ควรดู row: open the detail where the work is and light the row it is about
function frcGo(d, el) {
  frcOpen(d);
  setTimeout(() => {
    const x = el && document.getElementById(el);
    if (!x) return;
    const fold = x.closest('details'); if (fold) fold.open = true;
    uiFlash(x);
  }, 80);
}
// the one head of the page: ร้านของฉัน + the branch on the cards; ‹ + the detail's name on a detail
function _frcHead() {
  const bk = document.getElementById('frcBack'), k = document.getElementById('frcKicker'), t = document.getElementById('frcDtTitle'), nm = document.getElementById('frcBranchName');
  const det = !!_frcDetail;
  if (bk) bk.hidden = !det;
  if (k) k.textContent = det ? ['ร้านของฉัน', _frcBranch].filter(Boolean).join(' · ') : 'ร้านของฉัน';
  if (t) { t.hidden = !det; t.textContent = det ? _FRC_DT[_frcDetail] : ''; }
  if (nm) nm.hidden = det;
}
// ฉัน: แจ้งเตือนทุกเช้า and ดาวน์โหลดและพิมพ์ (#frcMe) sit in the phone's ฉัน view, before ออกจากระบบ; on a desktop under the cards
function _frcMeMount() {
  const me = document.getElementById('frcMe'); if (!me) return;
  const ph = typeof phOn === 'function' && phOn();
  const host = ph ? document.querySelector('#phv-me .ph-body') : document.getElementById('frcMeHome');
  if (!host || me.parentNode === host) return;
  const out = ph ? document.getElementById('phMeOut') : null;
  host.insertBefore(me, out && out.parentNode === host ? out : null);
}
// พิมพ์: the whole month on A4 (index.html prints every section, folded blocks opened); from ฉัน the page comes back first
async function frcPrint() {
  if (typeof phOn === 'function' && phOn() && typeof _phView !== 'undefined' && _phView !== 'page' && typeof phOpenFranchise === 'function') await phOpenFranchise();
  document.querySelectorAll('#tab-franchise details.frc-fold').forEach(x => { x.open = true; });
  setTimeout(() => window.print(), 300);
}

// ── สรุปประจำวัน (owner 2026-10-27, mockup B): the last finished day and its month so far ─────────────────────────────
// #frcDay is its own slot in tabs/56-franchise.html, above ติดต่อสำนักงานใหญ่ and the month cards: it loads on its own and stays when the
// month summary fails. The loading line shows only on the first load (a reload keeps the card until the new one is drawn).
const _FRC_DAY_LOADING = `<div class="ms-section">${uiSkel(3, 'kpi')}</div>`;
const _FRC_LEVEL_INK = { good: 'var(--good-ink)', watch: 'var(--amber-ink)', fix: 'var(--bad-ink)' };
async function _frcGetJson(url) {
  const r = await fetch(`${API}${url}`);
  const t = await r.text();
  let j = null; try { j = JSON.parse(t); } catch (e) { /* not json */ }
  if (!r.ok) throw new Error((j && typeof j.detail === 'string' && j.detail) || 'โหลดข้อมูลไม่สำเร็จ');
  return j;
}
async function frcShowDay(gen, p) {
  const first = document.getElementById('frcDay');
  if (first && !first.innerHTML.trim()) first.innerHTML = _FRC_DAY_LOADING;
  let d = null, err = '';
  try { d = await p; } catch (e) { err = (e && e.message) || 'โหลดข้อมูลไม่สำเร็จ'; }
  const box = document.getElementById('frcDay');
  if (gen !== _frcGen || !box) return;
  box.innerHTML = err ? `<div class="ms-section"><div class="ms-card"><div class="ms-row" style="color:var(--bad-ink)">${esc(err)}</div></div></div>` : _frcDayCard(d);
  _frcS.day = err ? null : d;                    // phone B: the เมื่อวาน card on the overview
  _frcCardsRender();
}
// a band word on its own ink (ในเป้า / ไม่เกินเพดาน / ต้องแก้ ...)
function _frcLevelChip(level, text) {
  if (!level || !text) return '';
  return `<span class="frc-lv" style="color:${_FRC_LEVEL_INK[level] || 'var(--text-secondary)'}">${esc(text)}</span>`;
}
// the change on the navy band: a light green / red on the fixed dark fill
function _frcHeroChip(label, v) {
  const val = v == null ? '—' : `${v > 0 ? '▲' : v < 0 ? '▼' : ''} ${Math.abs(v).toFixed(1).replace(/\.0$/, '')}%`;
  return `<span class="frc-hchip">${esc(label)} <b class="${v == null || v === 0 ? '' : v > 0 ? 'up' : 'dn'}">${val}</b></span>`;
}
function _frcDayTile(label, value, acc, sub, ink) {
  return `<div class="frc-tile" style="--acc:${acc}"><div class="k">${esc(label)}</div><div class="n"${ink ? ` style="color:${ink}"` : ''}>${value}</div><div class="s">${sub || ''}</div></div>`;
}
function _frcDayCard(d) {
  if (!d || !d.available || !d.day) return '';
  const D = d.day;
  const help = '<p>วันที่แสดงคือวันล่าสุดที่ปิดการขายแล้ว วันที่ยังเปิดขายอยู่ไม่นำมาแสดง และไม่เปลี่ยนตามช่วงเวลาที่เลือกด้านบน</p>'
    + '<p>ยอดขายไม่รวม VAT และไม่รวมค่าบริการ · เทียบปกติ = ค่ากลางของวันเดียวกันใน 8 สัปดาห์ก่อน เช่น วันพฤหัสบดีเทียบค่ากลางของวันพฤหัสบดี 8 สัปดาห์ วันที่ร้านไม่มียอดขายไม่นำมาคิด</p>'
    + '<p>ต้นทุนอาหารของวันเทียบเป้าของบริษัท เมนูที่ยังไม่มีต้นทุนนับเป็น 0 บาท ตัวเลขจึงเป็นค่าต่ำสุด · ส่วนลดคิดจากยอดก่อนหักส่วนลด เทียบเพดานส่วนลด</p>'
    + '<p>ยอดเดือนนี้เทียบเดือนก่อนและปีก่อนในช่วงวันเดียวกัน · เป้า = เป้าที่กำหนดไว้ หรือยอดเดือนเดียวกันของปีก่อน เส้นตั้งบนแถบคือเป้าถึงวันนี้ · บิลยกเลิกมีข้อมูลเป็นรายเดือน</p>';
  const late = d.skipped_day ? `<span class="frc-hchip">ข้อมูล ${esc(_frcDay(d.skipped_day))} ยังเข้าไม่ครบ</span>` : '';
  const hero = `<div class="frc-hero"><div class="lbl">${esc(D.weekday || '')} ${esc(_frcDay(D.date))} · ยอดขาย (ไม่รวม VAT และค่าบริการ)</div>`
    + `<div class="num">${_frcBaht(D.revenue)}</div>`
    + `<div class="cmp">${_frcHeroChip(normalLbl(D.normal_weeks, true), D.vs_normal_pct)}${late}</div></div>`;
  const fc = D.food_cost_pct;
  const fcLevel = fc == null ? null : fc <= D.food_cost_target_pct ? 'good' : 'fix';
  const fcWord = fcLevel === 'good' ? `ในเป้า ${_frcPctText(D.food_cost_target_pct)}` : D.food_cost_status_th;
  const pol = _frcPctText(D.discount_policy_pct);
  const discWord = { good: `ไม่เกิน ${pol}`, watch: `เกินเพดาน ${pol}`, fix: `เกินเพดาน ${pol} มาก` };
  const tiles = `<div class="frc-tiles">`
    + _frcDayTile('ลูกค้า', D.customers != null ? `${fmtN(D.customers)} คน` : _FRC_NA, '#0891b2', `<span style="${_FRC_MUTED}">${fmtN(D.bills || 0)} บิล</span>`)
    + _frcDayTile('เฉลี่ยต่อหัว', _frcBaht(D.per_head), '#7a66b8', D.per_bill != null ? `<span style="${_FRC_MUTED}">ต่อบิล ${_frcBaht(D.per_bill)}</span>` : '')
    + _frcDayTile('ต้นทุนอาหาร', _frcPct(fc), fcLevel === 'fix' ? '#dc2626' : '#10b981',
        fc == null ? `<span style="${_FRC_MUTED}">${esc(D.food_cost_reason || 'ยังไม่มีต้นทุน')}</span>` : _frcLevelChip(fcLevel, fcWord),
        fcLevel ? _FRC_LEVEL_INK[fcLevel] : '')
    + _frcDayTile('ส่วนลด', _frcPct(D.discount_pct), D.discount_level === 'fix' ? '#dc2626' : D.discount_level === 'watch' ? '#d97706' : '#10b981',
        _frcLevelChip(D.discount_level, discWord[D.discount_level]))
    + `</div>`;
  return `<div class="ms-section"><div class="ms-section-title frc-title">สรุปประจำวัน ${uiQ('frc:day', 'สรุปประจำวัน', help)}</div>`
    + hero + tiles + _frcDayMonth(d.month) + `</div>`;
}
function _frcDayMonth(M) {
  if (!M) return '';
  const head = M.complete ? `ทั้งเดือน ${_frcMonthLabel({ month: M.month })}` : `เดือนนี้ถึง ${_frcDay(M.through)}`;
  const yoy = M.vs_last_year_pct != null ? `${msPchip(M.vs_last_year_pct)} <span class="frc-cmpl">ปีก่อน</span>` : '';
  let target = '';
  const t = M.target;
  if (t && t.source !== 'none' && t.month_target != null) {
    const share = Math.max(0, Math.min(100, t.of_month_pct || 0)), mark = Math.max(0, Math.min(100, t.elapsed_share_pct || 0));
    target = `<div class="ms-row frc-dtarget"><div class="frc-dtl"><span>ได้ <b>${_frcPct(t.of_month_pct)}</b> ของเป้าเดือน</span>`
      + `<span>${t.vs_to_date_pct != null ? msPchip(t.vs_to_date_pct) + ` <span class="frc-cmpl">${t.month_complete ? 'เทียบเป้าเดือน' : 'เทียบเป้าถึงวันนี้'}</span>` : ''}</span></div>`
      + `<span class="frc-meter" role="img" aria-label="ยอดขาย ${esc(_frcPctText(t.of_month_pct))} ของเป้าเดือน"><span style="width:${share}%"></span>${t.month_complete ? '' : `<i style="left:${mark}%"></i>`}</span>`
      + `<div class="frc-cmpl">เป้าเดือน ${_frcBaht(t.month_target)}</div></div>`;
  }
  const v = M.voids;
  const vWord = { good: 'ปกติ', watch: 'ควรดู', fix: 'ต้องแก้' };
  const voids = v && v.rate_pct != null
    ? `<div class="ms-row"><span style="flex:1;min-width:0;font-size:13px;color:var(--text-primary)">บิลยกเลิก (เดือนนี้)</span>`
      + `<span style="font-size:14px;font-weight:700;font-variant-numeric:tabular-nums;color:var(--text-primary)">${_frcPctText(v.rate_pct)} ${_frcLevelChip(v.level, vWord[v.level])}</span></div>` : '';
  return `<div class="ms-card frc-dmonth"><div class="ms-row" style="flex-wrap:wrap;gap:6px">`
    + `<div style="flex:1 1 100%"><div class="frc-cmpl">${esc(head)}</div><div class="frc-dmtd">${_frcBaht(M.mtd)}</div></div>`
    + `${msPchip(M.vs_last_month_pct)} <span class="frc-cmpl">เดือนก่อน</span> ${yoy}</div>`
    + target + voids + `</div>`;
}

// ── DV6 (phone table review 2026-10-25, owner: "อะไรที่มีประโยชน์ขึ้นหน้าจอของเจ้าของสาขาด้วย"): เรื่องที่ควรดู, from GET
// /api/franchise/decide for the top bar's period - ต้นทุนอาหาร against เฉลี่ยทุกสาขา (with the baht), ของแพง per customer (plates
// only, with the main cause: ลูกค้า / กินต่อหัว), the menus customers ordered more of per head and the new / returning menus, and the
// last 6 weeks' sales against the branch's own average (a red-green strip, heatRG). Own branch only; the chain appears only as
// เฉลี่ยทุกสาขา. Branch words (docs/BRANCH_WORDS.md); the explanations sit behind "?". ──
// Phone B (2026-10-31): the payload comes with the overview (GET /api/franchise/overview decide); its cards sit on the details they answer -
// ต้นทุนอาหาร + ของแพง on ต้นทุนอาหาร, เมนูที่ลูกค้าสั่งเพิ่มขึ้น on เมนู, 6 สัปดาห์ folded on เดือนนี้ - each naming the period it reads (_frcDecVs).
function _frcDecLabel(P) {
  if (!P) return '';
  return P.has_prev && P.prev_label_th ? `${P.label_th} เทียบ ${P.prev_label_th}` : (P.label_th || '');
}
// the phone's period control of this page (#frcPeriodChip): the franchise login has no top bar on a phone, so the chip sits in the page head
function frcPeriodChip(per) {
  const c = document.getElementById('frcPeriodChip'); if (!c) return;
  const on = typeof phOn === 'function' && phOn();
  c.hidden = !on;
  if (!on) return;
  const r = per && per.label_th ? per : (typeof gPeriodResolve === 'function' ? gPeriodResolve() : null);
  const kind = typeof getPeriodKind === 'function' && getPeriodKind() === 'week' ? 'สัปดาห์' : 'เดือน';
  const txt = r && typeof periodChipShort === 'function' ? periodChipShort(r) : '';
  const l = c.querySelector('.frc-pchip-l'); if (l) l.textContent = `${kind} · ${txt}`;
  c.setAttribute('aria-label', `ช่วงเวลา ${kind} ${r && r.label_th ? r.label_th : ''} · แตะเพื่อเปลี่ยน`);
}
const _frcSignPct = (v, unit = '%') => v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(1).replace(/\.0$/, '')}${unit}`;
const _frcInk = (v, upBad) => v == null || v === 0 ? 'var(--text-secondary)' : (v > 0) === !!upBad ? 'var(--bad-ink)' : 'var(--good-ink)';
// (c) ต้นทุนอาหาร: % of sales now / the period before, against เฉลี่ยทุกสาขา, and the baht that gap is
function _frcFoodCost(f) {
  if (!f) return '';
  const help = '<p>ต้นทุนอาหาร = ต้นทุนของเมนูที่ขาย ÷ ยอดขาย (ไม่รวม VAT) ' + stkWord('ตามสูตรของแต่ละเมนู', 'ตามต้นทุนต่อจานของแต่ละเมนู') + '</p>'
    + '<p>เป้า = เป้าต้นทุนอาหารของบริษัท · เฉลี่ยทุกสาขา = ต้นทุนอาหารรวมของทุกสาขา ÷ ยอดขายรวมของทุกสาขา ในช่วงเดียวกัน</p>'
    + '<p>บาท = ส่วนที่ต่างจากเป้าหรือจากเฉลี่ยทุกสาขา × ยอดขายของสาขาในช่วงนี้</p>';
  if (f.pct == null) return _frcCard('ต้นทุนอาหาร', 'dfood', help, '', `<div class="ms-card"><div class="ms-row" style="${_FRC_MUTED};font-size:13px">${esc(f.reason || 'ยังไม่มีต้นทุนของช่วงนี้')}</div></div>`);
  const gap = f.vs_avg_pp, gb = f.gap_baht;
  const t = typeof fcTargetSet === 'function' ? fcTargetSet(f.target_pct) : f.target_pct;      // the ONE target (rules.food_cost_target)
  const over = f.target_pct != null && f.pct > t, tb = f.target_gap_baht;
  const bahtTxt = v => v ? ` · ประมาณ ฿${fmtN(Math.abs(v))}` : '';
  const gapTxt = gap == null ? '' : gap > 0 ? `สูงกว่าเฉลี่ยทุกสาขา ${_frcSignPct(gap, ' จุด').replace(/^\+/, '')}${bahtTxt(gb)}`
    : gap < 0 ? `ต่ำกว่าเฉลี่ยทุกสาขา ${_frcSignPct(-gap, ' จุด').replace(/^\+/, '')}${bahtTxt(gb)}` : 'เท่ากับเฉลี่ยทุกสาขา';
  const tgtTxt = f.target_pct == null ? '' : over ? `เกินเป้า ${t}% อยู่ ${(f.pct - t).toFixed(1)} จุด${bahtTxt(tb)}` : `ในเป้า ${t}%`;
  // a plain card (not the coloured hero tile): the figure's colour says in / over the target
  const hero = `<div class="ms-card" style="padding:12px 14px"><p style="font-size:12px;margin:0;${_FRC_MUTED}">ต้นทุนอาหาร (% ของยอดขาย)</p>`
    + `<p style="font-size:30px;font-weight:800;line-height:1.15;margin:2px 0;font-variant-numeric:tabular-nums;color:${f.target_pct == null ? 'var(--text-primary)' : over ? 'var(--bad-ink)' : 'var(--good-ink)'}">${_frcPct(f.pct)}</p>`
    + (tgtTxt ? `<p style="font-size:13px;font-weight:600;margin:0;color:${over ? 'var(--bad-ink)' : 'var(--good-ink)'}">${esc(tgtTxt)}</p>` : '')
    + `<p style="font-size:13px;margin:4px 0 0">${f.change_pp != null ? `<b style="color:${_frcInk(f.change_pp, true)}">${_frcSignPct(f.change_pp, ' จุด')}</b> <span style="font-size:12px;${_FRC_MUTED}">เทียบช่วงก่อน (${_frcPct(f.prev_pct)})</span>` : `<span style="font-size:12px;${_FRC_MUTED}">ช่วงก่อนยังไม่มีต้นทุน</span>`}</p>`
    + `</div>`;
  const rows = `<div class="ms-card" style="margin-top:10px">`
    + _frcRow('เฉลี่ยทุกสาขา', _frcPct(f.avg_pct), f.avg_change_pp != null ? `เทียบช่วงก่อน ${_frcSignPct(f.avg_change_pp, ' จุด')}` : '')
    + (gapTxt ? `<div class="ms-row" style="font-size:13px;font-weight:600;color:${_frcInk(gap, true)}">${esc(gapTxt)}</div>` : '')
    + (f.coverage_pct != null && f.coverage_pct < 99.5 ? _frcRow('จานที่มีต้นทุนแล้ว', _frcPct(f.coverage_pct), 'เมนูที่ยังไม่มีต้นทุนนับเป็น 0 บาท') : '')
    + `</div>`;
  return _frcCard('ต้นทุนอาหาร เทียบเฉลี่ยทุกสาขา', 'dfood', help, _frcDecVs, hero + rows);
}
// (a) ของแพง: plates per customer of the 8 high-value groups, now / before / เฉลี่ยทุกสาขา, and why the plates moved
function _frcCostly(c) {
  if (!c) return '';
  const help = '<p>ของแพง 8 กลุ่ม (แซลมอน ทูน่า ฮามาจิ กุ้งหวาน หอยเชลล์ ปลาไหล หอยนางรม เนื้อ) นับเป็นจำนวนจานที่ออก ÷ จำนวนลูกค้า</p>'
    + '<p>สาเหตุ: จานที่เปลี่ยนแยกเป็นสองส่วน คือ ลูกค้ามากขึ้นหรือน้อยลง และกินต่อหัวมากขึ้นหรือน้อยลง ส่วนที่ใหญ่กว่าคือสาเหตุหลัก</p>'
    + '<p>เทียบเฉลี่ยทุกสาขา: จานเกิน = (จานต่อลูกค้าของสาขา − เฉลี่ยทุกสาขา) × ลูกค้าของสาขา เรียงจากจานเกินมากที่สุด</p>';
  const gs = c.groups || [];
  if (!gs.length) return '';                     // an empty sub-section is hidden (phone review item 7)
  const cause = { customers: 'ลูกค้า', plates_per_head: 'กินต่อหัว' };
  const sub = [_frcDecVs, c.customers != null ? `ลูกค้า ${fmtN(c.customers)} คน${c.customers_change_pct != null ? ` (${_frcSignPct(c.customers_change_pct)} เทียบช่วงก่อน)` : ''}` : ''].filter(Boolean).join(' · ');
  // two-line rows (uiRow2): the group + plates per customer; the chip says above / below เฉลี่ยทุกสาขา; ▾ = before / average / the cause
  const rows = gs.map(g => {
    const vsA = g.vs_avg_pct, ch = g.per_head_change_pct, hi = vsA != null && vsA >= 10;
    const why = g.plates_change != null && g.main
      ? `จาน ${_frcSignPct(g.plates_change, '')} · สาเหตุหลัก <b>${cause[g.main] || ''}</b> (ลูกค้า ${_frcSignPct(g.by_customers, '')} · กินต่อหัว ${_frcSignPct(g.by_per_head, '')})` : '';
    const more = `ช่วงก่อน ${g.prev_per_head != null ? Number(g.prev_per_head).toFixed(2) : '—'} · เฉลี่ยทุกสาขา ${g.avg_per_head != null ? Number(g.avg_per_head).toFixed(2) : '—'} จาน/คน`
      + (ch != null ? ` · ${_frcSignPct(ch)} จากช่วงก่อน` : '') + (why ? `<br>${why}` : '');
    return uiRow2({ tone: hi ? 'watch' : vsA != null && vsA <= 0 ? 'good' : '', title: esc(g.label),
      val: `${g.per_head != null ? Number(g.per_head).toFixed(2) : '—'} จาน/คน`,
      chip: vsA == null ? null : { t: vsA > 0 ? 'สูงกว่า' : 'ต่ำกว่า', tone: hi ? 'watch' : vsA <= 0 ? 'good' : 'neu' },
      fact: vsA != null ? `${_frcSignPct(vsA)} จากเฉลี่ยทุกสาขา${g.over_avg_plates > 0 ? ` · เกิน ${fmtN(g.over_avg_plates)} จาน` : ''}` : 'ยังไม่มีเฉลี่ย',
      more, attrs: ` id="frcCostly-${_frcId(g.slug)}"` });
  }).join('');
  return _frcCard('ของแพง (จานต่อลูกค้า)', 'dcostly', help, sub, `<div class="ms-card">${rows}</div>`);
}
// (b) the menus ordered more per customer than their normal (the 3 weeks before), and the new / returning menus
function _frcMenusUp(m) {
  if (!m) return '';
  const help = '<p>เมนูที่ลูกค้าสั่งเพิ่มขึ้น: จำนวนจานต่อลูกค้า 100 คนของช่วงนี้ เทียบปกติ (3 สัปดาห์ก่อนหน้า ถ่วงตามวันในสัปดาห์) เรียงตามจานที่เกินปกติ</p>'
    + '<p>เมนูใหม่: เริ่มขายที่สาขาไม่เกิน 4 สัปดาห์ · กลับมาขาย: เมนูที่หยุดขายตั้งแต่ 3 สัปดาห์แล้วกลับมาขายใหม่</p>';
  // two-line rows (uiRow2): the dish + its extra plates; line 2 = per 100 customers against normal / the day it started
  const up = (m.rising || []).map((x, i) => uiRow2({ tone: 'watch', title: `${i + 1}. ${esc(x.menu)}`, val: `+${fmtN(x.extra_plates || 0)} จาน`,
    chip: x.x != null ? { t: '×' + x.x.toFixed(1), tone: 'watch' } : null,
    fact: `ต่อลูกค้า 100 คน ${x.rate != null ? fmtN(Math.round(x.rate)) : '—'} · ปกติ ${x.normal_rate != null ? fmtN(Math.round(x.normal_rate)) : '—'}` })).join('');
  const nw = (m.new || []).map(x => uiRow2({ title: esc(x.menu), val: `${fmtN(x.plates || 0)} จาน`,
    chip: { t: x.returning ? 'กลับมา' : 'ใหม่', tone: 'neu' },
    fact: `${x.returning ? 'กลับมาขาย' : 'เริ่มขาย'} ${esc(x.first_sold_th || '')}${x.rate != null ? ` · ต่อลูกค้า 100 คน ${fmtN(Math.round(x.rate))}` : ''}` })).join('');
  if (!up && !nw) return '';
  const more = (n, shown) => n > shown ? `<div class="ms-row" style="${_FRC_MUTED};font-size:12px;border:0">ทั้งหมด ${fmtN(n)} เมนู · แสดง ${shown} อันดับแรก</div>` : '';
  const sub = [_frcDecVs, m.normal_from ? `เทียบปกติ ${_frcDay(m.normal_from)} – ${_frcDay(m.normal_to)}` : ''].filter(Boolean).join(' · ');
  return _frcCard('เมนูที่ลูกค้าสั่งเพิ่มขึ้น (ต่อลูกค้า)', 'dmenus', help, sub,
    (up ? `<div class="ms-card">${up}${more(m.rising_count || 0, (m.rising || []).length)}</div>` : '')
    + (nw ? `<div class="frc-sub2">เมนูใหม่และเมนูที่กลับมาขาย</div><div class="ms-card">${nw}${more(m.new_count || 0, (m.new || []).length)}</div>` : ''));
}
// (d) the last 6 whole weeks' sales against the branch's own average of them: one red-green strip (heatRG, js/01-core.js)
function _frcWeeks(w) {
  const ws = (w && w.weeks) || [];
  if (!ws.some(x => x.revenue)) return '';
  const help = '<p>ยอดขาย (ไม่รวม VAT) ของ 6 สัปดาห์ล่าสุดที่ครบสัปดาห์ (จันทร์–อาทิตย์) เทียบค่าเฉลี่ยของ 6 สัปดาห์นี้ของสาขา</p>'
    + '<p>สีแดง = ต่ำกว่าค่าเฉลี่ย สีเขียว = สูงกว่าค่าเฉลี่ย สีเข้มขึ้นเมื่อต่างมากขึ้น</p>';
  const cells = ws.map(x => {
    const bg = x.vs_avg_pct != null && typeof heatRG === 'function' ? heatRG(x.vs_avg_pct) : null;
    const ink = bg && typeof inkOn === 'function' ? inkOn(bg) : 'var(--text-primary)';
    return `<div class="frc-wk" style="background:${bg || 'var(--bg-subtle)'};color:${ink}" title="${esc(x.label)} · ${x.revenue != null ? '฿' + fmtN(x.revenue) : 'ไม่มียอดขาย'}">`
      + `<span class="frc-wk-l">${esc(x.label)}</span><b class="frc-wk-v">${x.revenue != null ? fmt(x.revenue) : '—'}</b>`
      + `<span class="frc-wk-p">${x.vs_avg_pct != null ? _frcSignPct(x.vs_avg_pct) : ''}</span></div>`;
  }).join('');
  return _frcCard('ยอดขาย 6 สัปดาห์ เทียบค่าเฉลี่ยของสาขา', 'dweeks', help, w.avg ? `ค่าเฉลี่ยต่อสัปดาห์ ฿${fmtN(Math.round(w.avg))}` : '',
    `<div class="frc-wks" role="img" aria-label="ยอดขาย 6 สัปดาห์ล่าสุด เทียบค่าเฉลี่ยของสาขา">${cells}</div>`);
}

// ── PR-F2 (2026-10-22): สัดส่วนการขาย · ช่วงเวลาขายดี · การควบคุม · เสียงลูกค้า (the insight payload). Phone B (2026-10-31): it comes with
// the overview (GET /api/franchise/overview insight) - the การควบคุม and เสียงลูกค้า cards need it on the first screen. ──

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
  return _frcCard('สัดส่วนการขาย', 'mix', help, '',
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
  return _frcCard('ช่วงเวลาขายดี', 'peak', help, '',
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
  const max = Math.max(1, ...rows.map(r => r.baht || 0));
  return `<div class="frc-sub2">${esc(title)}</div><div class="ms-card">` + rows.map(r => uiRow2({ title: esc(r[name] || '-'), val: '฿' + fmtN(r.baht || 0),
    fact: `${fmtN(r[count] || 0)} ${unit}${r.share != null ? ' · ' + _frcPctText(r.share) + ' ของรายการนี้' : ''}`,
    bar: { pct: 100 * (r.baht || 0) / max, col: 'var(--navy-ink)' } })).join('') + `</div>`;
}
function _frcControls(c) {
  if (!c) return '';
  const help = '<p>ส่วนลด: ส่วนลดทั้งหมดเทียบยอดขายก่อนหักส่วนลด เทียบกับเพดานที่บริษัทกำหนด ส่วนลดที่ใช้คูปองจะนับเป็น "ไม่ระบุ" ในรายการตามเหตุผล เพราะ POS บันทึกคูปองแยกจากเหตุผล</p>'
    + '<p>บิลยกเลิก: บิลที่ถูกยกเลิกหรือลบทั้งบิล เกินร้อยละ 0.5 ของบิลทั้งหมดควรดู เกินร้อยละ 1 ต้องแก้ ควรตรวจสอบเหตุผล</p>'
    + '<p>บิลพิมพ์ซ้ำที่มีส่วนลด: บิลที่มีส่วนลดและพิมพ์ใบเสร็จมากกว่าหนึ่งครั้ง ควรตรวจสอบเหตุผลการพิมพ์ซ้ำ</p>'
    + '<p>เงินสด: ผลการนับเงินสดปิดวัน วันที่ขาดตั้งแต่ 2,000 บาทขึ้นไปควรตรวจสอบ</p>'
    + '<p>แสดงเป็นจำนวนและจำนวนเงินเท่านั้น ไม่แสดงเลขบิลหรือชื่อผู้ใช้งาน</p>';
  const D = c.discount || {}, V = c.voids || {}, C = c.cash || {}, R = c.reprints || {};
  const over = D.pct != null && D.policy_pct != null && D.pct > D.policy_pct;
  // each line carries the id a ควรดู row goes to (frcGo: frcCtlDisc / frcCtlVoids / frcCtlReprint / frcCtlCash)
  const disc = `<div class="ms-card">` + _frcRow('ส่วนลด', `<span style="color:${over ? 'var(--bad-ink)' : 'var(--text-primary)'}">${_frcPct(D.pct)}</span>`,
      `เพดาน ${_frcPctText(D.policy_pct)} · ฿${fmtN(D.baht || 0)}${D.over_policy_baht ? ' · เกินเพดาน ฿' + fmtN(D.over_policy_baht) : ''}`, 'frcCtlDisc')
    + _frcVoidRow(V)
    + (R.available ? _frcRow('บิลพิมพ์ซ้ำที่มีส่วนลด', `<span style="color:${R.bills ? 'var(--amber-ink)' : 'var(--text-primary)'}">${fmtN(R.bills || 0)} บิล</span>`,
      R.bills ? `ส่วนลดรวม ฿${fmtN(R.baht || 0)}` : 'ไม่มีบิลพิมพ์ซ้ำที่มีส่วนลด', 'frcCtlReprint') : '')
    + _frcRow('เงินสดขาด/เกิน สุทธิ', C.net == null ? _FRC_NA : `<span style="color:${C.net < 0 ? 'var(--bad-ink)' : 'var(--text-primary)'}">${C.net < 0 ? '−' : ''}฿${fmtN(Math.abs(C.net))}</span>`,
      C.days_counted ? `นับเงินสด ${fmtN(C.days_counted)} วัน · ขาด ${fmtN(C.short_days || 0)} วัน` : 'ยังไม่มีข้อมูลการนับเงินสด', 'frcCtlCash')
    + `</div>`;
  const cashDays = (C.days || []).filter(x => x.over_short);
  const cash = cashDays.length ? `<div class="frc-sub2">วันที่เงินสดไม่ตรง</div><div class="frc-tbl-wrap"><table class="frc-tbl"><thead><tr><th>วันที่</th><th class="n">ขาด/เกิน (฿)</th></tr></thead><tbody>`
    + cashDays.slice().reverse().map(x => `<tr><td>${esc(_frcDay(x.date))}</td><td class="n" style="color:${x.over_short < 0 ? 'var(--bad-ink)' : 'var(--good-ink)'}">${x.over_short > 0 ? '+' : ''}${fmtN(x.over_short)}</td></tr>`).join('')
    + `</tbody></table></div>` : '';
  return _frcCard('การควบคุม', 'controls', help, '',
    `<div class="ms-card">${_frcFlagBox(c.flags)}</div>` + disc
    + `<div id="frcVoidList" hidden></div>`
    + _frcMoneyList('ส่วนลดตามคูปองและโปรโมชัน', D.by_coupon, 'name', 'uses', 'ครั้ง')
    + _frcMoneyList('ส่วนลดตามเหตุผล', D.by_reason, 'reason', 'bills', 'บิล')
    + _frcMoneyList('บิลยกเลิกตามเหตุผล', V.by_reason, 'reason', 'bills', 'ใบ') + cash);
}

// ── บิลยกเลิก / พิมพ์ซ้ำ of this branch, bill by bill (owner 2026-10-31, "ทำเลย"): the การควบคุม row opens the list under it ──
// GET /api/franchise/voids (the management drill-down's level 2, pinned to the owner's branch, the month of the period chip).
// Classes as the management phone: ไม่ออกใหม่ (the alert's basis, the default) · ออกใหม่แล้ว · บิลเปล่า (฿0) · ทั้งหมด. A bill row folds
// its full detail (▾); a reprinted bill fetches its lines (/api/franchise/voids/bill). No sheet: the owner's slice has none.
let _frcVoidData = null, _frcVoidCls = 'open', _frcVoidSeq = 0;
const _FRC_VCLS = [['open', 'ไม่ออกใหม่'], ['zero', 'บิลเปล่า'], ['re', 'ออกใหม่แล้ว'], ['all', 'ทั้งหมด']];
function _frcVoidRow(V) {
  const open = V.voids_open != null;
  const n = open ? V.voids_open : V.bills || 0;
  const fact = !V.bills ? 'ไม่มีบิลยกเลิกในเดือนนี้'
    : open ? `฿${fmtN(V.amount_open || 0)} · ${_frcPctText(V.rate_open_pct)} ของบิล · รวม ${fmtN(V.bills)} ใบ`
    : `฿${fmtN(V.baht || 0)} · ${_frcPctText(V.rate_pct)} ของบิล`;
  return uiRow2({ title: open ? 'บิลยกเลิกแล้วไม่ออกใหม่' : 'บิลยกเลิก', val: `${fmtN(n)} ใบ`, fact: esc(fact), chip: V.bills ? { t: 'ดูรายบิล' } : null,
    onGo: 'frcVoidsOpen()', attrs: ' id="frcCtlVoids"' });
}
function frcVoidsOpen() {
  const box = document.getElementById('frcVoidList'); if (!box) return;
  if (!box.hidden && _frcVoidData) { box.hidden = true; return; }      // a second tap folds the list
  box.hidden = false;
  frcVoidsLoad();
}
function frcVoidsCls(k) {
  _frcVoidCls = _FRC_VCLS.some(([x]) => x === k) ? k : 'open';
  const box = document.getElementById('frcVoidList');
  if (box && _frcVoidData) box.innerHTML = _frcVoidsHtml(_frcVoidData);
}
async function frcVoidsLoad() {
  const box = document.getElementById('frcVoidList'); if (!box) return;
  const seq = ++_frcVoidSeq;
  const mon = _frcPeriod().month, q = mon ? '?month=' + mon : '';
  box.innerHTML = `<div class="ms-card">${uiSkel(5)}</div>`;
  let j;
  try { j = await _frcGetJson('/api/franchise/voids' + q); }
  catch (e) { if (seq === _frcVoidSeq) box.innerHTML = `<div class="ms-card">${uiEmpty({ t: e.message || 'โหลดไม่สำเร็จ', act: 'ลองอีกครั้ง', go: 'frcVoidsLoad()' })}</div>`; return; }
  if (seq !== _frcVoidSeq) return;
  if (!j || !j.available || j.level !== 2) { box.innerHTML = `<div class="ms-card">${uiEmpty({ t: (j && j.line) || 'ยังไม่มีข้อมูลบิลยกเลิก' })}</div>`; return; }
  _frcVoidData = j;
  box.innerHTML = _frcVoidsHtml(j);
  const flash = document.getElementById('frcVoidHead'); if (flash) uiFlash(flash);
}
const _frcHm = at => String(at || '').slice(11, 16);
function _frcVoidsHtml(j) {
  const all = j.bills || [], reps = j.reprints || [], H = j.header || {};
  const cls = b => b.zero ? 'zero' : b.reissued_as ? 're' : 'open';
  const n = { all: all.length, open: all.filter(b => cls(b) === 'open').length, zero: all.filter(b => cls(b) === 'zero').length, re: all.filter(b => cls(b) === 're').length };
  const k = _frcVoidCls, rows = all.filter(b => k === 'all' || cls(b) === k);
  const help = uiQ('frc:voids', 'บิลยกเลิก / พิมพ์ซ้ำ', '<p><b>ไม่ออกใหม่</b> = ยกเลิกแล้วไม่พบบิลใหม่ที่ร้านภายใน ' + (j.reissue_min || 30) + ' นาที (จำนวนลูกค้าเท่ากัน หรือยอดต่างไม่เกิน 20%) · เป็นฐานของการเตือน</p>'
    + '<p><b>ออกใหม่แล้ว</b> = มีบิลใหม่แทน (เป็นการแก้บิล) · <b>บิลเปล่า</b> = บิลยกเลิกยอด ฿0 ไม่ใช่เงินที่หาย · สองกลุ่มนี้ไม่นับในการเตือน</p>'
    + '<p><b>บิลพิมพ์ซ้ำ</b> = บิลที่มีส่วนลดและพิมพ์ใบเสร็จมากกว่า 1 ครั้ง</p><p>บิลยกเลิกไม่มีรายการอาหาร เพราะระบบ POS ไม่ส่งรายการของบิลที่ยกเลิก</p>');
  const chips = `<div class="ms-vw-chips ms-vd-cls-chips" role="group" aria-label="ประเภทบิลยกเลิก">` + _FRC_VCLS.filter(([x]) => x === 'all' || x === 'open' || n[x]).map(([x, t]) =>
    `<button type="button" class="ms-vw-chip" aria-pressed="${x === k}" onclick="frcVoidsCls('${x}')">${t} ${fmtN(n[x])}</button>`).join('') + '</div>';
  const r = H.rate_open_pct != null ? H.rate_open_pct : H.rate_pct;
  const head = `<div class="frc-sub2" id="frcVoidHead">บิลยกเลิก · ${esc(j.label || '')} ${help}</div>`
    + `<p class="ms-vd-sum">ยกเลิกแล้วไม่ออกใหม่ <b>${r != null ? _frcPct(r, 2) : _FRC_NA}</b> · ${fmtN(n.open)} บิล · ฿${fmtN(H.amount_open != null ? H.amount_open : H.amount || 0)}`
    + ` · <span class="ms-vd-all">รวมยกเลิกทั้งหมด ${fmtN(n.all)} บิล</span> · พิมพ์ซ้ำ ${fmtN(reps.length)} บิล</p>`;
  const billRow = b => {
    const c = cls(b);
    const chip = c === 'zero' ? '<span class="ms-vd-cls">บิลเปล่า ฿0</span>' : c === 're' ? `<span class="ms-vd-cls">ออกใหม่แล้ว → ${esc(b.reissued_no || b.reissued_as)}</span>` : '<span class="ms-vd-cls is-open">ไม่ออกใหม่</span>';
    return uiRow2({ title: `${esc(_frcDay(String(b.at || '').slice(0, 10)))} ${_frcHm(b.at)}`, val: '฿' + fmtN(b.amount || 0), valTone: c === 'open' && b.amount ? 'bad' : '',
      chips: chip, fact: esc(b.reason || '') + (b.user ? ' · ' + esc(b.user) : ''),
      more: `เลขที่บิล ${esc(b.receipt || 'ไม่มีเลขที่บิล')}${b.discount > 0 ? ' · ส่วนลด ฿' + fmtN(b.discount) : ''}` });
  };
  const bills = rows.length ? rows.map(billRow).join('') : uiEmpty({ t: k === 'open' ? 'ไม่มีบิลยกเลิกที่ไม่ออกใหม่ในเดือนนี้' : 'ไม่มีบิลในกลุ่มนี้' });
  const more = j.bills_more ? `<p class="ms-vd-note">แสดง ${fmtN(all.length)} บิลล่าสุด · อีก ${fmtN(j.bills_more)} บิลอยู่ในรายงานผู้บริหาร</p>` : '';
  const repRows = reps.map((b, i) => uiRow2({ title: `${esc(_frcDay(String(b.at || '').slice(0, 10)))} ${_frcHm(b.at)} · ${esc(b.no || b.receipt)}`, val: '฿' + fmtN(b.total || 0),
    fact: `ส่วนลด ฿${fmtN(b.discount || 0)} · พิมพ์ ${fmtN(b.prints || 0)} ครั้ง${b.cashier ? ' · ' + esc(b.cashier) : ''}`, onGo: `frcVoidBill(${i})`, attrs: ` id="frcRep${i}"` })).join('');
  const repBox = reps.length ? `<div class="frc-sub2">บิลพิมพ์ซ้ำที่มีส่วนลด</div><div class="ms-card">${repRows}</div>` : '';
  return head + `<div class="ms-card">${chips}${bills}</div>` + more + repBox;
}
// a reprinted bill's lines and payments, folded open under its row
async function frcVoidBill(i) {
  const j = _frcVoidData, b = j && (j.reprints || [])[i], row = document.getElementById('frcRep' + i); if (!b || !row) return;
  let x = row.querySelector('.ms-r2-x');
  if (x) { row.classList.toggle('is-open'); return; }
  x = document.createElement('div'); x.className = 'ms-r2-x'; x.innerHTML = uiSkel(3); row.appendChild(x); row.classList.add('is-open');
  let d;
  try { d = await _frcGetJson('/api/franchise/voids/bill?' + new URLSearchParams({ receipt: b.receipt })); }
  catch (e) { x.innerHTML = esc(e.message || 'โหลดไม่สำเร็จ'); return; }
  if (!d || !d.available) { x.innerHTML = esc((d && d.line) || 'ไม่พบบิลนี้'); return; }
  const lines = (d.lines || []).map(l => `<div>${esc(l.menu)} × ${fmtN(l.qty)} · ฿${fmtN(l.rev)}</div>`).join('');
  const pays = (d.payments || []).map(p => `${esc(p.code)} ฿${fmtN(p.amount)}`).join(' · ');
  x.innerHTML = `<div>โต๊ะ ${esc(d.table || '-')}${d.covers ? ' · ลูกค้า ' + fmtN(d.covers) + ' คน' : ''}</div>` + (lines || '<div>ไม่มีรายการอาหารของบิลนี้ในระบบ</div>') + (pays ? `<div>ชำระ: ${pays}</div>` : '');
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
  if (!v.available) return _frcCard('เสียงลูกค้า', 'voice', help, '',
    `<div class="ms-card"><div class="ms-row" style="${_FRC_MUTED};font-size:13px">ยังไม่มีรีวิวของสาขาในระบบ</div></div>`);
  const ch = v.rating_change;
  const head = `<div style="${_FRC_TILES};margin-top:0">`
    + msKpiTile('คะแนนเฉลี่ยเดือนนี้', v.rating_avg == null ? '—' : Number(v.rating_avg).toFixed(1), '#f59e0b',
      ch != null ? `${msPchip(Math.round(ch * 100) / 100, true, '')} <span style="font-size:12px;${_FRC_MUTED}">เดือนก่อน ${Number(v.rating_avg_prev).toFixed(1)}</span>` : 'เดือนก่อนยังไม่มีรีวิว')
    + msKpiTile('จำนวนรีวิวเดือนนี้', fmtN(v.reviews_month || 0), '#0891b2', [v.reviews_prev != null ? `เดือนก่อน ${fmtN(v.reviews_prev)}` : '',
      v.unreplied ? `<b style="color:var(--amber-ink)">รอตอบ ${fmtN(v.unreplied)}</b>` : '', v.negative ? `<b style="color:var(--bad-ink)">ติ ${fmtN(v.negative)}</b>` : ''].filter(Boolean).join(' · '))
    + `</div>`;
  const list = (v.latest || []).length ? `<div class="frc-sub2">รีวิวล่าสุด</div><div class="ms-card">` + v.latest.map(r => `<div class="ms-row" style="flex-direction:column;align-items:stretch;gap:4px">`
      + `<span style="display:flex;gap:8px;align-items:center;font-size:12px;${_FRC_MUTED}">${_frcStars(r.stars)}<span>${esc(_frcDay(r.date))} · ${esc(r.source || '')}</span></span>`
      + `<span style="font-size:13px;line-height:1.5;color:var(--text-primary);white-space:normal">${r.text ? esc(r.text) : `<span style="${_FRC_MUTED}">(ให้คะแนนโดยไม่เขียนข้อความ)</span>`}</span></div>`).join('') + `</div>` : '';
  return _frcCard('เสียงลูกค้า', 'voice', help, '', head + list);
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
  const help = '<p>ยอดสั่ง = มูลค่าที่สาขาสั่งในแต่ละรอบ · ยอดที่คลังจัดส่ง = มูลค่าที่คลังส่งจริง คิดแบบเดียวกับหน้าสั่งของที่สาขา นับรอบที่วันส่งอยู่ในเดือนนี้ ไม่นับใบที่ยังเป็นร่างหรือยกเลิก</p>'
    + '<p>ยอดซื้อเทียบยอดขาย = ยอดที่คลังจัดส่ง ÷ ยอดขายไม่รวม VAT ของเดือนเดียวกัน ระหว่างเดือนนับถึงวันล่าสุดที่ปิดการขาย และเทียบกับทั้งเดือนของเดือนก่อน</p>'
    + '<p>ส่งใบสั่งซื้อหลังเวลาส่งสุดท้ายมีค่าปรับ หน้านี้แสดงเฉพาะจำนวนใบ · วัตถุดิบที่ใช้เกินสูตรมาจากรอบตรวจนับล่าสุด เรียงตามมูลค่า</p>';
  const helpStock = help.replace('วัตถุดิบที่ใช้เกินสูตรมาจากรอบตรวจนับล่าสุด เรียงตามมูลค่า', 'วัตถุดิบที่นับได้น้อยกว่าระบบมาจากรอบนับของสิ้นเดือนล่าสุด เรียงตามมูลค่า');
  if (!d.started) {
    return _frcCard(title, 'supply', help, '', `<div class="ms-card"><div class="ms-row" style="${_FRC_MUTED};font-size:13px">${esc(d.not_started_th || 'สาขายังไม่เริ่มใช้ระบบสั่งวัตถุดิบ')}</div></div>`);
  }
  const P = d.purchases || {}, L = d.late || {}, C = d.claims || {};
  const ch = P.change_pp;
  const ratioSub = P.pct_prev != null
    ? `${ch != null && ch !== 0 ? msPchip(Math.round(ch * 10) / 10, false, ' จุด') + ' ' : ''}<span style="font-size:12px;${_FRC_MUTED}"${P.prev_range ? ` title="${esc(periodLabelTh(P.prev_range[0], P.prev_range[1]))}"` : ''}>${P.basis === 'to_date' ? 'ช่วงเดียวกันเดือนก่อน' : 'เดือนก่อน'} ${_frcPctText(P.pct_prev)}</span>`
    : `<span style="font-size:12px;${_FRC_MUTED}">เดือนก่อนยังไม่มีข้อมูล</span>`;
  const upto = P.month_complete ? 'ทั้งเดือน' : (P.through ? 'ถึง ' + _frcDay(P.through) : '');
  const tiles = `<div style="${_FRC_TILES};margin-top:0">`
    + msKpiTile('ยอดสั่งจากส่วนกลาง', _frcBaht(P.ordered), '#4a70b0', `${fmtN(P.rounds || 0)} รอบส่ง`)
    + msKpiTile('ยอดที่คลังจัดส่ง', _frcBaht(P.delivered), '#0891b2', `จัดส่งแล้ว ${fmtN(P.rounds_delivered || 0)} รอบ`)
    + msKpiTile('ยอดซื้อเทียบยอดขาย' + (upto ? ` (${esc(upto)})` : ''), _frcPct(P.pct), '#7a66b8', ratioSub)
    + msKpiTile('ส่งหลังเวลาส่งสุดท้าย', `${fmtN(L.count || 0)} ใบ`, L.count ? '#f59e0b' : '#10b981', L.count ? '(มีค่าปรับ)' : 'ทันเวลาทุกใบ')
    + `</div>`;
  const rounds = d.rounds || [];
  const tbl = rounds.length ? `<div class="frc-sub2">รอบส่งของเดือนนี้</div><div class="frc-tbl-wrap"><table class="frc-tbl"><thead><tr><th>รอบ · สถานะ</th>`
    + `<th class="n">สั่ง (฿)</th><th class="n">จัดส่ง (฿)</th></tr></thead><tbody>`
    + rounds.map(r => `<tr><td>${esc(r.round_th)}<br><span class="frc-dow">${esc(r.status_th || '')}</span>`
      + `${r.late ? ' <span class="frc-dow" style="color:var(--amber-ink)">· ส่งหลังเวลาส่งสุดท้าย</span>' : ''}</td>`
      + `<td class="n">${r.ordered == null ? _FRC_NA : fmtN(r.ordered)}</td><td class="n">${r.delivered == null ? _FRC_NA : fmtN(r.delivered)}</td></tr>`).join('')
    + `</tbody></table></div>`
    : `<div class="ms-card" style="margin-top:10px"><div class="ms-row" style="${_FRC_MUTED};font-size:13px">ยังไม่มีรอบส่งในเดือนนี้</div></div>`;
  return _frcCard(title, 'supply', BC_MODE === 'stock' ? helpStock : help, '',
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
  const stock = BC_MODE === 'stock';
  const rows = o.rows || [];
  const when = `รอบตรวจนับ ${_frcDay(o.begin)} – ${_frcDay(o.end)}`;
  const t = `<div class="frc-sub2">${stock ? 'วัตถุดิบที่นับได้น้อยกว่าระบบ 5 อันดับ' : 'วัตถุดิบที่ใช้เกินสูตร 5 อันดับ'}</div>`;
  if (!rows.length) return t + `<div class="ms-card"><div class="ms-row" style="${_FRC_MUTED};font-size:13px">${esc(when)} · ${stock ? 'ไม่มีวัตถุดิบที่นับได้น้อยกว่าระบบ' : 'ไม่มีวัตถุดิบที่ใช้เกินสูตร'}</div></div>`;
  return t + `<p class="frc-sub" style="margin-top:0">${esc(when)} · เรียงตามมูลค่า</p>`
    + `<div class="ms-card">` + rows.map(r => `<div class="ms-row" style="flex-direction:column;align-items:stretch;gap:3px">`
      + `<span style="font-size:13px;color:var(--text-primary);overflow-wrap:anywhere">${esc(r.name)}</span>`
      + `<span style="font-size:12px;${_FRC_MUTED}">${stock ? 'ขาด' : 'ใช้เกิน'} ${r.qty == null ? _FRC_NA : _frcQty(r.qty)} ${esc(r.unit || '')} · ${stock ? 'ต่ำกว่าระบบ' : 'เกินสูตร'} ${_frcPct(r.pct)} · ${r.value == null ? _FRC_NA : '฿' + fmtN(r.value)}</span></div>`).join('')
    + `</div>`;
}

// ── PR-F4 (2026-10-22): ติดต่อสำนักงานใหญ่ (the owner) ────────────────────────────────────────────────────────────────────
// The card #frcMsg: the branch's own threads (open first; closed ones behind the finished-work filter), the unread mark, a thread with its
// messages and a reply box, and "ส่งคำถามใหม่" (topic + text, optionally the verdict line it is about). The server pins the branch.
/** @type {any} */
let _fmData = null;
/** @type {{mode: string, tid: number, ctx: any, topic: string}} */
let _fmView = { mode: 'list', tid: 0, ctx: null, topic: '' };
/** @type {any} */
let _fmThread = null;
let _fmBusy = false;
const _FM_TOPICS = [['sales', 'ยอดขาย'], ['cost', 'ต้นทุนและวัตถุดิบ'], ['orders', 'ใบสั่งซื้อและเคลม'], ['controls', 'การควบคุม'], ['other', 'อื่น ๆ']];
const _FM_FILTER = [['todo', 'ยังไม่ปิด'], ['done', 'ปิดแล้ว'], ['all', 'ทั้งหมด']];
const _FM_TH_MON = ['ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.', 'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.'];

async function _fmFetch(url, opt) {
  const r = await fetch(`${API}${url}`, opt);
  const t = await r.text();
  let j = null; try { j = JSON.parse(t); } catch (e) { /* not json */ }
  if (!r.ok) throw new Error((j && typeof j.detail === 'string' && j.detail) || 'ดำเนินการไม่สำเร็จ ลองใหม่อีกครั้ง');
  return j;
}
function _fmPost(url, body) {
  return _fmFetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}
// "2026-10-22 14:05:09" -> "22 ต.ค. 14:05" (the year, as on the rest of ร้านของฉัน (U3): ค.ศ., only when it is not this year's)
function _fmWhen(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(String(iso || ''));
  if (!m) return '';
  const y = +m[1], yNow = new Date().getFullYear();
  return `${+m[3]} ${_FM_TH_MON[+m[2] - 1] || ''}${y !== yNow ? ' ' + y : ''} ${m[4]}:${m[5]}`;
}
function _fmMonthTh(ym) {
  const m = /^(\d{4})-(\d{2})$/.exec(String(ym || ''));
  return m ? `${_FM_TH_MON[+m[2] - 1] || ''} ${+m[1]}` : '';
}
// the verdict line a question refers to: "ควรดู · ต.ค. 2026 · <line>" - text only (the caller escapes it)
function _fmCtxText(c) {
  if (!c) return '';
  return [c.level_th || ({ watch: 'ควรดู', fix: 'ต้องแก้', good: 'ดี' })[c.level] || '', _fmMonthTh(c.month), c.line || ''].filter(Boolean).join(' · ');
}
// a first guess of the topic from the words of the verdict line (the owner can change it)
function _fmGuessTopic(line) {
  const t = String(line || '');
  if (/ส่วนลด|ยกเลิก|เงินสด|ใบตัดเบิก|ควบคุม/.test(t)) return 'controls';
  if (/ใบสั่งซื้อ|เคลม|รับสินค้า|รับของ|จัดส่ง|ตัดรอบ/.test(t)) return 'orders';
  if (/ต้นทุน|วัตถุดิบ|กำไร|ของเสีย|ยอดซื้อ/.test(t)) return 'cost';
  if (/ยอดขาย|ลูกค้า|เป้า/.test(t)) return 'sales';
  return 'other';
}
// the messages of a thread for the reader of `side` ('owner' | 'hq'): the reader's own side on the right. Pure (esc + _fmWhen).
function _fmMessagesHtml(messages, side) {
  return (messages || []).map(m => {
    const mine = m.side === side;
    return `<div class="fm-msg${mine ? ' fm-mine' : ''}"><div class="fm-meta">${esc(m.author || (m.side === 'hq' ? 'สำนักงานใหญ่' : 'เจ้าของสาขา'))} · ${esc(_fmWhen(m.created_at))}</div>`
      + `<div class="fm-body">${esc(m.body)}</div></div>`;
  }).join('');
}
// one row of a thread list. Pure. `onclick` names the opener; the branch is shown on head office's list only.
function _fmRowHtml(t, onclick, showBranch) {
  const last = t.last ? `<span class="fm-snip">${esc((t.last.side === 'hq' ? 'สำนักงานใหญ่: ' : '') + t.last.body)}</span>` : '';
  const closed = t.status === 'closed' ? `<span class="fm-chip">ปิดแล้ว</span>` : '';
  const dot = t.unread ? `<span class="fm-dot" role="img" aria-label="มีข้อความใหม่"></span>` : '';
  return `<button type="button" class="fm-row${t.unread ? ' fm-unread' : ''}" data-tid="${Number(t.id) || 0}" onclick="${onclick}(${Number(t.id) || 0})">`
    + `<span class="fm-row-main"><span class="fm-row-top">${dot}<span class="fm-topic">${showBranch ? esc(t.branch || '') + ' · ' : ''}${esc(t.topic_th || '')}</span>${closed}`
    + `<span class="fm-when">${esc(_fmWhen(t.last_at))}</span></span>${last}</span></button>`;
}
function _fmComposer(id, max, placeholder) {
  return `<textarea id="${id}" class="fm-text" rows="4" maxlength="${Number(max) || 1000}" placeholder="${esc(placeholder)}" oninput="fmCount('${id}',${Number(max) || 1000})"></textarea>`
    + `<div class="fm-count" id="${id}Count">0 / ${fmtN(max)}</div>`;
}
function fmCount(id, max) {
  const el = document.getElementById(id), c = document.getElementById(id + 'Count');
  if (el && c) c.textContent = `${fmtN(String(el.value || '').length)} / ${fmtN(max)}`;
}
// Audit 2026-10: a re-render of the same view (the foreground refresh, a reload of the inbox) must not erase what a person is typing.
// Call before box.innerHTML = …; the returned function puts back the values (and the caret of the focused box) of `ids` when the box
// still shows the same `key` (a thread id, the new-question form). _fmForget(box) after a send, or when a fresh form is wanted.
function _fmKeep(box, key, ids) {
  const same = !!box && box.dataset.fmKey === key;
  const act = document.activeElement;
  const saved = same ? ids.map(id => {
    const el = /** @type {HTMLTextAreaElement|null} */ (document.getElementById(id));
    return el ? { id, v: el.value, f: act === el, s: el.selectionStart, e: el.selectionEnd } : null;
  }).filter(Boolean) : [];
  return () => {
    if (box) box.dataset.fmKey = key;
    saved.forEach(k => {
      const el = document.getElementById(k.id);
      if (!el) return;
      el.value = k.v;
      if (el.tagName === 'TEXTAREA') fmCount(k.id, Number(el.maxLength) > 0 ? Number(el.maxLength) : 1000);
      if (k.f) { try { el.focus({ preventScroll: true }); if (k.s != null && el.setSelectionRange) el.setSelectionRange(k.s, k.e); } catch (e) { /* no focus */ } }
    });
  };
}
function _fmForget(box) { if (box) delete box.dataset.fmKey; }

async function frcMsgLoad() {
  const box = document.getElementById('frcMsg');
  if (!box) return;
  if (!franchiseLogin()) { box.hidden = true; return; }
  box.hidden = false;
  try { _fmData = await _fmFetch('/api/franchise/messages'); }
  catch (e) {
    box.innerHTML = `<div class="ms-section-title">ติดต่อสำนักงานใหญ่</div><div class="ms-card"><div class="ms-row" style="color:var(--bad-ink)">${esc(e.message)}</div></div>`;
    return;
  }
  const p = _fmsgPending;
  // a tapped "สำนักงานใหญ่ตอบ" notification: the thread opens on the ติดต่อสำนักงานใหญ่ detail
  if (p && p.go === 'franchise') { _fmsgPending = null; if (p.t) { if (_frcDetail !== 'msg') frcOpen('msg'); await frcMsgOpen(p.t, true); return; } }
  if (_fmView.mode === 'thread' && _fmView.tid) { await frcMsgOpen(_fmView.tid); return; }
  frcMsgRender();
}
function frcMsgRender() {
  const box = document.getElementById('frcMsg');
  if (!box || !_fmData) return;
  const help = '<p>ส่งคำถามหรือข้อสังเกตถึงสำนักงานใหญ่ ฝ่ายบริหารและฝ่ายปฏิบัติการเป็นผู้ตอบ คำตอบแสดงในการ์ดนี้ และแจ้งเตือนที่เครื่องเมื่อเปิดรับการแจ้งเตือนไว้</p>'
    + '<p>ข้อความที่ส่งแล้วแก้ไขหรือลบไม่ได้ ส่งได้ไม่เกินวันละ 20 ข้อความ ข้อความละไม่เกิน 1,000 ตัวอักษร หัวข้อที่สำนักงานใหญ่ปิดแล้วดูได้จากตัวกรอง "ปิดแล้ว"</p>';
  const unread = _fmData.unread ? `<span class="fm-badge">ใหม่ ${fmtN(_fmData.unread)}</span>` : '';
  const head = `<div class="ms-section-title frc-title">ติดต่อสำนักงานใหญ่ ${uiQ('frc:msg', 'ติดต่อสำนักงานใหญ่', help)}${unread}</div>`;
  let inner;
  if (_fmView.mode === 'new') inner = _fmNewHtml();
  else if (_fmView.mode === 'thread' && _fmThread) inner = _fmThreadHtml(_fmThread);
  else inner = _fmListHtml();
  const keep = _fmKeep(box, _fmView.mode === 'thread' && _fmThread ? 'thread:' + _fmThread.id : _fmView.mode,
    ['frcMsgTopic', 'frcMsgText', 'frcMsgReply']);
  box.innerHTML = head + inner;
  keep();
  _frcCardsRender();                             // the ติดต่อสำนักงานใหญ่ card follows the unread count
  if (_fmView.mode !== 'new' && _fmView.mode !== 'thread') {
    const sp = uiStateFilter.split(_fmData.threads || [], t => t.status === 'closed' && !t.unread);
    uiStateFilter.bar('frcMsgFilter', { counts: sp.counts, f: uiStateFilter.get(['todo', 'done', 'all']), pick: 'frcMsgFilter', opts: _FM_FILTER });
  }
}
function _fmListHtml() {
  const f = uiStateFilter.get(['todo', 'done', 'all']);
  // RV-M: a closed thread with a reply the owner has not read stays in the default list (the card said "ใหม่ 1" over an empty list)
  const sp = uiStateFilter.split(_fmData.threads || [], t => t.status === 'closed' && !t.unread);
  const rows = sp[f] || sp.todo;
  const list = rows.length ? rows.map(t => _fmRowHtml(t, 'frcMsgOpen', false)).join('')
    : (sp.counts.all ? uiStateFilter.empty(f, 'frcMsgFilter', 'หัวข้อ') : `<div class="ms-row" style="${_FRC_MUTED};font-size:13px">ยังไม่มีคำถาม</div>`);
  return `<div class="ms-card fm-card"><div class="fm-actions"><button type="button" class="fm-btn fm-primary" onclick="frcMsgNew()">ส่งคำถามใหม่</button>`
    + `<div id="frcMsgFilter"></div></div>${list}</div>`;
}
function frcMsgFilter(f) { uiStateFilter.set(f); frcMsgRender(); }
function _fmNewHtml() {
  const v = _fmView, max = (_fmData && _fmData.max_body) || 1000;
  const ctx = v.ctx ? `<div class="fm-ctx"><span class="fm-ctx-t">เรื่องที่อ้างถึง: ${esc(_fmCtxText(v.ctx))}</span>`
    + `<button type="button" class="fm-x" aria-label="ไม่อ้างถึงเรื่องนี้" onclick="frcMsgDropCtx()">×</button></div>` : '';
  const opts = ((_fmData && _fmData.topics) || _FM_TOPICS.map(([id, label]) => ({ id, label })))
    .map(t => `<option value="${esc(t.id)}"${t.id === v.topic ? ' selected' : ''}>${esc(t.label)}</option>`).join('');
  return `<div class="ms-card fm-card"><div class="fm-sub">ส่งคำถามใหม่</div>${ctx}`
    + `<label class="fm-label" for="frcMsgTopic">หัวข้อ</label><select id="frcMsgTopic" class="fm-select"><option value="">เลือกหัวข้อ</option>${opts}</select>`
    + `<label class="fm-label" for="frcMsgText">ข้อความ</label>${_fmComposer('frcMsgText', max, 'พิมพ์คำถามหรือข้อสังเกต')}`
    + `<div class="fm-actions"><button type="button" class="fm-btn fm-primary" id="frcMsgSend" onclick="frcMsgSendNew()">ส่ง</button>`
    + `<button type="button" class="fm-btn" onclick="frcMsgBack()">ยกเลิก</button></div></div>`;
}
function _fmThreadHtml(t) {
  const max = t.max_body || 1000;
  const ctx = t.context ? `<div class="fm-ctx"><span class="fm-ctx-t">เรื่องที่อ้างถึง: ${esc(_fmCtxText(t.context))}</span></div>` : '';
  const foot = t.status === 'open'
    ? `<label class="fm-label" for="frcMsgReply">ตอบกลับ</label>${_fmComposer('frcMsgReply', max, 'พิมพ์ข้อความ')}`
      + `<div class="fm-actions"><button type="button" class="fm-btn fm-primary" id="frcMsgSend" onclick="frcMsgSendReply(${Number(t.id) || 0})">ส่ง</button></div>`
    : `<div class="fm-closed">หัวข้อนี้ปิดแล้ว</div><div class="fm-actions"><button type="button" class="fm-btn fm-primary" onclick="frcMsgNew()">ส่งคำถามใหม่</button></div>`;
  return `<div class="ms-card fm-card"><div class="fm-actions"><button type="button" class="fm-btn" onclick="frcMsgBack()">‹ กลับ</button>`
    + `<span class="fm-topic fm-grow">${esc(t.topic_th || '')}</span>${t.status === 'closed' ? '<span class="fm-chip">ปิดแล้ว</span>' : ''}</div>`
    + ctx + `<div class="fm-thread">${_fmMessagesHtml(t.messages, 'owner')}</div>${foot}</div>`;
}
async function frcMsgOpen(tid, scroll) {
  try { _fmThread = await _fmFetch(`/api/franchise/messages/${Number(tid) || 0}`); }
  catch (e) { uiToast(e.message, 'error'); _fmView = { mode: 'list', tid: 0, ctx: null, topic: '' }; frcMsgRender(); return; }
  _fmView = { mode: 'thread', tid: _fmThread.id, ctx: null, topic: '' };
  const row = ((_fmData && _fmData.threads) || []).find(x => x.id === _fmThread.id);
  if (row && row.unread) { row.unread = 0; _fmData.unread = Math.max(0, (_fmData.unread || 0) - 1); }
  frcMsgRender();
  if (scroll !== false) _fmScrollIn();
  try { navBadgesRefresh(true); } catch (e) { /* no launcher slice: no badge to redraw */ }
}
function frcMsgNew() { _fmView = { mode: 'new', tid: 0, ctx: null, topic: '' }; _fmForget(document.getElementById('frcMsg')); frcMsgRender(); _fmScrollIn('frcMsgTopic'); }
function frcMsgBack() { _fmView = { mode: 'list', tid: 0, ctx: null, topic: '' }; _fmThread = null; frcMsgLoad(); }
function frcMsgDropCtx() {
  const topic = (document.getElementById('frcMsgTopic') || {}).value || _fmView.topic, text = (document.getElementById('frcMsgText') || {}).value || '';
  _fmView = Object.assign({}, _fmView, { ctx: null, topic });
  frcMsgRender();
  const ta = document.getElementById('frcMsgText'); if (ta) { ta.value = text; fmCount('frcMsgText', (_fmData && _fmData.max_body) || 1000); }
}
// "สอบถามเรื่องนี้" beside a ควรดู / ต้องแก้ line: a new question that names the line and the month
function frcAskAbout(level, i) {
  const line = _frcVerdictLast && _frcVerdictLast[level] ? _frcVerdictLast[level][i] : '';
  if (!line) return;
  _fmView = { mode: 'new', tid: 0, ctx: { level, month: _frcMonth || '', line: String(line).slice(0, 300) }, topic: _fmGuessTopic(line) };
  _fmForget(document.getElementById('frcMsg'));
  frcOpen('msg');                                // phone B: the question is written on the ติดต่อสำนักงานใหญ่ detail
  if (!_fmData) { frcMsgLoad().then(() => { frcMsgRender(); _fmScrollIn('frcMsgText'); }); return; }
  frcMsgRender();
  _fmScrollIn('frcMsgText');
}
function _fmScrollIn(focusId) {
  const box = document.getElementById('frcMsg');
  if (box) { try { box.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) { box.scrollIntoView(); } }
  if (focusId) setTimeout(() => { const el = document.getElementById(focusId); if (el) try { el.focus({ preventScroll: true }); } catch (e) { /* no focus */ } }, 300);
}
async function frcMsgSendNew() {
  if (_fmBusy) return;
  const topic = (document.getElementById('frcMsgTopic') || {}).value || '', text = String((document.getElementById('frcMsgText') || {}).value || '').trim();
  if (!topic) { uiToast('เลือกหัวข้อ', 'warn'); return; }
  if (!text) { uiToast('พิมพ์ข้อความ', 'warn'); return; }
  _fmBusy = true;
  const btn = document.getElementById('frcMsgSend'); if (btn) btn.disabled = true;
  try {
    const ctx = _fmView.ctx ? { level: _fmView.ctx.level, month: _fmView.ctx.month, line: _fmView.ctx.line } : null;
    const j = await _fmPost('/api/franchise/messages', { topic, body: text, context: ctx });
    uiToast('ส่งคำถามถึงสำนักงานใหญ่แล้ว', 'success');
    _fmView = { mode: 'thread', tid: j.thread.id, ctx: null, topic: '' };
    await frcMsgLoad();
  } catch (e) {
    uiToast(e.message, 'error');
    if (btn) btn.disabled = false;
  } finally { _fmBusy = false; }
}
async function frcMsgSendReply(tid) {
  if (_fmBusy) return;
  const text = String((document.getElementById('frcMsgReply') || {}).value || '').trim();
  if (!text) { uiToast('พิมพ์ข้อความ', 'warn'); return; }
  _fmBusy = true;
  const btn = document.getElementById('frcMsgSend'); if (btn) btn.disabled = true;
  try {
    _fmThread = await _fmPost(`/api/franchise/messages/${Number(tid) || 0}/reply`, { body: text });
    uiToast('ส่งข้อความแล้ว', 'success');
    _fmForget(document.getElementById('frcMsg'));      // the reply is sent: the box starts empty
    frcMsgRender();
  } catch (e) {
    uiToast(e.message, 'error');
    if (btn) btn.disabled = false;
  } finally { _fmBusy = false; }
}

