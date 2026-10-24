// 10-ordering.js — จัดซื้อ/วัตถุดิบ (order plan · pack sizes ·
// sourcing · issued cycles · festival calendar). Loaded last; shares the
// global scope of slices 01-09 (see index.html).
// ═══════════════════════════════════════════════════════════════════════════
// แผนสั่งวัตถุดิบรายสาขา (Ingredient Order Plan)
// Demand forecast: real daily sales × BoM. Deliberately blind to on-hand stock
// (the app has no inventory feed), so every surface repeats that caveat rather
// than letting the number read as a ready-to-send purchase order.
// ═══════════════════════════════════════════════════════════════════════════
let _opBasis = 'avg';
let _opFiltersReady = false;
// The rows exactly as the table is showing them, kept so the bulk controls act
// on what the user can see rather than on a second, differently-filtered fetch.
let _opLastRows = [];
// The meta of the last successful load — the range pickers and the quick-range
// buttons seed themselves from it rather than guessing at today's date.
let _opLastMeta = null;

const OP_TYPE_TH = { protein:'โปรตีน', carb:'คาร์โบไฮเดรต', veg:'ผัก',
  sauce:'ซอส', oil:'น้ำมัน', seasoning:'เครื่องปรุง', other:'อื่นๆ' };

function opSetBasis(b) {
  _opBasis = b;
  document.querySelectorAll('[data-ob]').forEach(el =>
    el.classList.toggle('gran-active', el.dataset.ob === b));
  loadOrderPlan(true);
}

async function _opInitFilters() {
  if (_opFiltersReady) return;
  const sel = document.getElementById('opBranch');
  if (!sel) return;
  try {
    const branches = await api(`${API}/api/branches`);
    sel.innerHTML = '<option value="">— ทุกสาขา (รวมเครือ) —</option>' +
      (branches || []).filter(b => b.active !== false && b.name !== 'ALL')
        .map(b => `<option value="${esc(b.name)}">${esc(b.name)}</option>`).join('');
    const cur = (typeof getBranch === 'function') ? getBranch() : '';
    if (cur && (branches || []).some(b => b.name === cur)) sel.value = cur;
  } catch (e) { sel.innerHTML = '<option value="">— ทุกสาขา —</option>'; }
  _opFiltersReady = true;
}

// The window has two shapes. "ย้อนหลัง N วัน" rolls with the data and is right
// for a standing view; an explicit range is right for an ORDER CYCLE, which has
// a start and an end that have nothing to do with when the last bill landed.
function opToggleRange() {
  const on = !!document.getElementById('opUseRange')?.checked;
  const dw = document.getElementById('opDaysWrap'), rw = document.getElementById('opRangeWrap');
  if (dw) dw.style.display = on ? 'none' : '';
  if (rw) rw.style.display = on ? '' : 'none';
  // First switch: seed the pickers from the window the server just used, so
  // turning it on doesn't blank the tab.
  const f = document.getElementById('opFrom'), t = document.getElementById('opTo');
  if (on && f && t && !f.value && _opLastMeta) { f.value = _opLastMeta.date_from; t.value = _opLastMeta.date_to; }
  loadOrderPlan(true);
}

function opQuickRange(kind) {
  const f = document.getElementById('opFrom'), t = document.getElementById('opTo');
  if (!f || !t || !_opLastMeta) return;
  const dataTo = _opLastMeta.date_to;
  if (kind === 'menu') {
    f.value = _opLastMeta.menu_era_from || dataTo;
    t.value = dataTo;
  } else {                                    // the last COMPLETE month of data
    const d = new Date(dataTo + 'T00:00:00');
    const first = new Date(d.getFullYear(), d.getMonth(), 1);
    const prevLast = new Date(first.getTime() - 86400000);
    f.value = new Date(prevLast.getFullYear(), prevLast.getMonth(), 1).toISOString().slice(0, 10);
    t.value = prevLast.toISOString().slice(0, 10);
  }
  loadOrderPlan(true);
}

function _opParams() {
  const p = new URLSearchParams();
  const br = document.getElementById('opBranch')?.value || '';
  if (br) p.set('branch', br);
  if (!br && typeof getGroup === 'function' && getGroup()) p.set('group', getGroup());
  const useRange = !!document.getElementById('opUseRange')?.checked;
  const df = document.getElementById('opFrom')?.value || '';
  const dt = document.getElementById('opTo')?.value || '';
  if (useRange && df) p.set('date_from', df);
  if (useRange && dt) p.set('date_to', dt);
  p.set('days', document.getElementById('opDays')?.value || '28');
  p.set('basis', _opBasis);
  p.set('buffer_pct', document.getElementById('opBuffer')?.value || '10');
  const t = document.getElementById('opType')?.value || '';
  if (t) p.set('type', t);
  const q = document.getElementById('opQ')?.value.trim() || '';
  if (q) p.set('q', q);
  if (document.getElementById('opFamily')?.checked) p.set('group_family', 'true');
  const inc = document.getElementById('opInclude')?.value || 'all';
  if (inc !== 'all') p.set('include', inc);
  // Per-branch columns only mean something for the chain view — with one branch
  // picked the table IS that branch.
  if (!br && document.getElementById('opSplit')?.checked) p.set('split_branches', 'true');
  if (document.getElementById('opSeason')?.checked) p.set('season', 'auto');
  const cs = document.getElementById('opCycleStart')?.value || '';
  if (cs) p.set('cycle_start', cs);
  return p;
}

async function loadOrderPlan(force) {
  await _opInitFilters();
  const p = _opParams();
  const key = p.toString();
  if (!force && _tabLastKey.ordering === key) return;
  _tabLastKey.ordering = key;
  const st = document.getElementById('opStatus');
  if (st) st.textContent = 'กำลังคำนวณ…';
  let d;
  try { d = await api(`${API}/api/ingredients/order-plan?${p}`); }
  catch (e) { if (st) st.textContent = 'โหลดข้อมูลไม่สำเร็จ'; return; }
  if (!d || d.available === false) {
    if (st) st.textContent = 'ไม่มีข้อมูลยอดขายรายวันสำหรับคำนวณ';
    document.getElementById('opBody').innerHTML = '';
    return;
  }
  const m = d.meta || {}, t = d.totals || {}, cov = m.coverage || {};
  _opLastMeta = m;
  const brLabel = m.branch || 'ทุกสาขา';
  // A window reaching across the menu changeover averages two different recipe
  // books — say it where the numbers are, with the fix one click away.
  const eraEl = document.getElementById('opEraNote');
  if (eraEl) eraEl.innerHTML = m.era && m.era.straddles
    ? `<div class="card p-3 text-xs flex flex-wrap items-center gap-2" style="background:#fff7ed;border:1px solid #ea580c;color:#9a3412">
         <span>⚠ <b>ช่วงข้อมูลคร่อมวันเปลี่ยนเมนู (${esc(m.era.menu_era_from)})</b> ${uiQ('op:era', 'ช่วงข้อมูลคร่อมวันเปลี่ยนเมนู', '<p>ปริมาณที่ได้เป็นค่าเฉลี่ยของสูตรเมนูเก่ากับเมนูใหม่ปนกัน จึงไม่ตรงกับรอบที่จะสั่งจริง</p>')}</span>
         <button onclick="opUseEraFrom()" style="background:#c2410c;color:#fff;border:0;border-radius:var(--r-sm);padding:4px 12px;font-size:11.5px;font-weight:600;cursor:pointer">ใช้เฉพาะเมนูใหม่ (เริ่ม ${esc(m.era.suggest_from)})</button>
       </div>`
    : '';
  // a branch that opened mid-window is averaged over the days it actually sold —
  // say so, otherwise "28 วัน" looks like it undercounted
  const partial = (m.branch && m.trading_days && m.trading_days < m.days)
    ? ` · ⓘ ${m.branch} เปิดขายจริง ${m.trading_days}/${m.days} วัน (เฉลี่ยจากวันที่ขายจริง)` : '';
  if (st) st.textContent = `อ้างอิงยอดขายจริง ${fmtDay(m.date_from)} ถึง ${fmtDay(m.date_to)} (${m.days} วัน) · `
    + `ฐาน ${m.basis === 'recent' ? '7 วันล่าสุด' : 'เฉลี่ยทั้งช่วง'} · เผื่อ ${m.buffer_pct}%` + partial;
  document.getElementById('opScopeNote').textContent = `· ${brLabel} · ${fmtDay(m.date_from)} → ${fmtDay(m.date_to)}`;

  document.getElementById('opKpis').innerHTML = [
    { l:'รายการวัตถุดิบ', v:fmtN(t.ingredients||0), s:'ที่มีสูตรรองรับ', c:'#0f766e' },
    { l:'มูลค่าสั่ง/สัปดาห์', v:'฿'+fmt(t.value_week||0), s:brLabel, c:'#0891b2' },
    { l:'มูลค่าสั่ง/เดือน',  v:'฿'+fmt(t.value_month||0), s:'รวมเผื่อ '+(m.buffer_pct||0)+'%', c:'#7a66b8' },
    { l:'ครอบคลุมสูตร',     v:(cov.pct!=null?cov.pct+'%':'—'), s:'ของจำนวนจานที่ขาย', c:(cov.pct>=70?'#10b981':'#f59e0b') },
  ].concat(m.local_count ? [{ l:'สาขาซื้อเอง', v:fmtN(m.local_count), s:'ไม่อยู่ในจดหมายสั่งของ', c:'#64748b' }] : [])
   .map(k => `<div class="card p-4" style="border-top:3px solid ${k.c}"><p class="text-[11px] uppercase tracking-wide" style="color:var(--text-muted)">${k.l}</p><p class="text-2xl font-bold" style="color:${textInk(k.c)}">${k.v}</p><p class="text-[11px]" style="color:var(--text-muted)">${esc(k.s)}</p></div>`).join('');

  const miss = cov.top_missing || [];
  document.getElementById('opCoverage').innerHTML = miss.length
    ? `<div class="card p-3 text-[11px]" style="background:#fffbeb;border:1px solid #f59e0b;color:#92400e">
        <b>เมนูที่ยังไม่มีสูตรไม่อยู่ในแผนสั่งนี้</b> (${cov.pct}% ของจานที่ขายมีสูตรแล้ว)
        <ul style="margin:4px 0 0;padding-left:20px;list-style:disc">${miss.slice(0,5).map(x => `<li>${esc(x.name || x.code)} (${fmtN(x.qty)} จาน)</li>`).join('')}</ul>
       </div>` : '';

  const sn = m.season || {};
  const snEl = document.getElementById('opSeasonNote');
  if (snEl) snEl.innerHTML = (sn.factor && Math.abs(sn.factor - 1) > 0.001)
    ? `<div class="card p-3 text-xs" style="background:#fff7ed;border:1px solid #ea580c;color:#9a3412">
         <b>ปรับตามเทศกาล ×${sn.factor}</b>${sn.event ? ' — ' + esc(sn.event) : ''}
         <span style="color:var(--amber-ink)"> · ${esc(sn.reason || '')}</span></div>`
    : (sn.mode === 'auto-nodata'
       ? `<div class="card p-3 text-xs" style="background:var(--bg-subtle);color:var(--text-muted)">เปิดปรับตามเทศกาลไว้ แต่${esc(sn.reason || '')}</div>`
       : '');
  opLoadCycles();
  opLoadAccuracy();
  opLoadForcedNote(m.forced);

  const brs = d.branches || [];
  const brCard = document.getElementById('opBranchCard');
  if (brs.length && !m.branch) {
    brCard.style.display = '';
    document.getElementById('opBranchBody').innerHTML = brs.map(b => `
      <tr class="border-b" style="border-color:var(--border)">
        <td class="py-1.5 pr-2"><a href="#" onclick="opJumpBranch('${jsAttr(b.branch)}');return false" style="color:var(--teal-ink);font-weight:600">${esc(b.branch)}</a></td>
        <td class="py-1.5 pr-2 text-right">${fmtN(b.ingredients)}</td>
        <td class="py-1.5 pr-2 text-right">฿${fmtN(b.value_week)}</td>
        <td class="py-1.5 text-right font-semibold">฿${fmtN(b.value_month)}</td>
      </tr>`).join('');
  } else { brCard.style.display = 'none'; }

  _opLastRows = d.rows || [];
  opRenderBulkNote();
  // Per-branch columns: one line per ingredient, every branch's own quantity
  // beside it. Only the branches that actually use the ingredient are non-zero,
  // so a blank cell means "this branch doesn't order it", not "no data".
  const splitOn = !m.branch && !!document.getElementById('opSplit')?.checked;
  const bnames = splitOn ? (m.branch_names || []) : [];
  const headRow = document.querySelector('#opTable thead tr');
  if (headRow) {
    headRow.querySelectorAll('.op-br-th').forEach(th => th.remove());
    bnames.forEach(b => {
      const th = document.createElement('th');
      th.className = 'py-1.5 pr-2 text-right op-br-th';
      th.style.color = 'var(--teal-ink)';
      th.title = `ปริมาณสั่ง/เดือน ของ ${b}`;
      th.textContent = b;
      headRow.appendChild(th);
    });
  }
  document.getElementById('opBody').innerHTML = (d.rows || []).map(r => {
    const tr = r.trend_pct;
    const trHtml = tr == null ? '<span style="color:var(--text-muted)">—</span>'
      : tr >= 10 ? `<span style="color:var(--bad-ink);font-weight:600">▲ ${tr.toFixed(0)}%</span>`
      : tr <= -10 ? `<span style="color:var(--good-ink);font-weight:600">▼ ${Math.abs(tr).toFixed(0)}%</span>`
      : `<span style="color:var(--text-muted)">${tr > 0 ? '+' : ''}${tr.toFixed(0)}%</span>`;
    const central = r.sourcing !== 'local';
    return `<tr class="border-b" style="border-color:var(--border);${central ? '' : 'opacity:.55'}">
      <td class="py-1.5 pr-2 text-center"><input type="checkbox" ${central ? 'checked' : ''}
          onchange="opSetSourcing('${jsAttr(r.code)}', this.checked)"
          title="${central ? 'แนะนำให้สั่งจากไดกิโตะ' : 'สาขาซื้อเอง — ไม่อยู่ในจดหมาย'}"></td>
      <td class="py-1.5 pr-2" style="font-family:monospace;font-size:10.5px;color:var(--text-muted)">${esc(r.code)}</td>
      <td class="py-1.5 pr-2">${esc(r.name)}${r.n_variants > 1 ? ` <span style="color:var(--text-muted)">(${r.n_variants} ชื่อ)</span>` : ''}${opForcedBadge(r)}${r.no_usage ? ' <span style="font-size:10px;padding:1px 6px;border-radius:999px;background:var(--bg-subtle);color:var(--text-muted);white-space:nowrap">ไม่มีการใช้ในช่วงนี้</span>' : ''}${
        (r.forced_name && r.forced_name !== r.name) ? `<div style="font-size:10px;color:var(--text-muted)">ใบคลัง: ${esc(r.forced_name)}${r.forced_as ? ` (${esc(r.forced_as)})` : ''}</div>` : ''}</td>
      <td class="py-1.5 pr-2" style="color:var(--text-muted)">${esc(OP_TYPE_TH[r.type] || r.type)}</td>
      <td class="py-1.5 pr-2 text-right">${r.no_usage ? '<span style="color:var(--text-muted)">—</span>' : esc(r.per_day_disp)}</td>
      <td class="py-1.5 pr-2 text-right" style="color:var(--text-muted)" title="${r.pack_known ? esc('1 ' + r.order_unit + ' = ' + fmtN(r.qty_per_order) + ' ' + r.unit) : 'ยังไม่ได้ตั้งขนาดบรรจุ — ใช้หน่วยมาตรฐานไปก่อน'}">${(r.pack_known || r.no_usage) ? '' : '<span style="color:var(--amber-ink)">◇</span> '}${esc(r.order_unit || r.unit)}</td>
      <td class="py-1.5 pr-2 text-right font-semibold" title="ใช้จริง ${esc(r.order_week_disp)}">${r.no_usage ? '<span style="color:var(--text-muted)">0</span>' : esc(r.order_week_units_disp || r.order_week_disp)}</td>
      <td class="py-1.5 pr-2 text-right font-semibold" style="color:var(--teal-ink)" title="ใช้จริง ${esc(r.order_month_disp)}">${r.no_usage ? '<span style="color:var(--text-muted)">0</span>' : esc(r.order_month_units_disp || r.order_month_disp)}</td>
      <td class="py-1.5 pr-2 text-right">${fmtN(r.value_month)}</td>
      <td class="py-1.5 pr-2 text-right" style="color:var(--text-muted)">${fmtN(r.n_menus)}</td>
      <td class="py-1.5 text-right">${trHtml}</td>
      ${bnames.map(b => {
        const c = (r.by_branch || {})[b];
        const v = c ? (c.order_month_units != null ? c.order_month_units : c.order_month) : null;
        return `<td class="py-1.5 pr-2 text-right tabular-nums" data-sort="${v == null ? '' : v}"
          style="color:${v ? 'var(--text-primary)' : 'var(--text-muted)'}"
          title="${c ? esc(b + ' · สั่ง/สัปดาห์ ' + (c.order_week_units != null ? c.order_week_units : c.order_week)) : esc(b + ' ไม่ได้ใช้วัตถุดิบนี้')}">${v == null ? '—' : fmtN(v)}</td>`;
      }).join('')}
    </tr>`;
  }).join('') || `<tr><td colspan="${11 + bnames.length}" class="py-4 text-center" style="color:var(--text-muted)">ไม่พบข้อมูลตามเงื่อนไขที่เลือก</td></tr>`;
  makeSortable('opTable');
}

// The era banner's one-click fix: switch to an explicit range that starts at the
// changeover and keeps the end where it is.
function opUseEraFrom() {
  const from = _opLastMeta?.era?.suggest_from;
  if (!from) return;
  const box = document.getElementById('opUseRange');
  const f = document.getElementById('opFrom'), t = document.getElementById('opTo');
  if (!box || !f || !t) return;
  box.checked = true;
  f.value = from;
  if (!t.value) t.value = _opLastMeta.date_to;
  opToggleRange();
}

function opJumpBranch(name) {
  const sel = document.getElementById('opBranch');
  if (sel) { sel.value = name; loadOrderPlan(true); }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function opPrintLetter(preview) {
  const p = _opParams();
  p.delete('type'); p.delete('q'); p.delete('group_family'); p.delete('split_branches');
  p.set('horizon', document.getElementById('opHorizon')?.value === 'month' ? 'month' : 'week');
  p.set('top', '40');
  // §3 of the letter cites the franchise agreement. The clause number is typed
  // here or left blank for hand-writing — never guessed by the app.
  const cref = (document.getElementById('opContractRef')?.value || '').trim();
  if (cref) p.set('contract_ref', cref);
  const rby = (document.getElementById('opReplyBy')?.value || '').trim();
  if (rby) p.set('reply_by', rby);
  const url = `${API}/api/ingredients/order-letter.print?${p}`;
  if (!preview) { window.open(url, '_blank'); return; }
  const w = window.open('', '_blank');
  if (!w) return;
  w.document.write('<p style="font-family:sans-serif;padding:24px">กำลังสร้างร่างจดหมาย…</p>');
  fetch(url).then(r => r.text()).then(html => {
    w.document.open(); w.document.write(html); w.document.close();
  }).catch(e => {
    w.document.open();
    w.document.write('<p style="font-family:sans-serif;padding:24px;color:#b91c1c">สร้างร่างไม่สำเร็จ: '
                     + String(e).slice(0, 200) + '</p>');
    w.document.close();
  });
}

// One sheet per branch, whole numbers rounded up - what the owner hands a branch
// ("ปริมาณที่ควรสั่ง รายวัตถุดิบ ต้องทำเป็นรายงานแยกสาขา", 2026-09-22). The tab's
// "ทุกรายการ" filter is deliberately NOT carried over: a branch is advised on what
// it must order through the warehouse, so the default is the central list.
function opExportByBranch() {
  const p = _opParams();
  p.delete('type'); p.delete('q'); p.delete('group_family'); p.delete('split_branches');
  const inc = document.getElementById('opInclude')?.value || 'all';
  p.set('include', inc === 'all' ? 'central' : inc);
  window.open(`${API}/api/ingredients/order-plan-by-branch.xlsx?${p}`, '_blank');
}

function opExportXlsx() {
  const p = _opParams();
  p.delete('type'); p.delete('q'); p.delete('group_family');
  window.open(`${API}/api/ingredients/order-plan.xlsx?${p}`, '_blank');
}


// Toggle one ingredient between 'สั่งจากไดกิโตะ' and 'สาขาซื้อเอง'. With a branch
// selected the change applies to that branch only (posting mode for that branch);
// with "ทุกสาขา" it sets the chain-wide default.
async function opSetSourcing(code, central) {
  if (!code) return;
  const branch = document.getElementById('opBranch')?.value || '';
  const st = document.getElementById('opStatus');
  try {
    const r = await fetch(`${API}/api/ingredients/sourcing`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },   // the global fetch
      // wrapper (top of this file) attaches X-Upload-Token to every write
      body: JSON.stringify([{ ingredient_code: code, branch: branch,
                              mode: central ? 'central' : 'local' }]),
    });
    if (!r.ok) throw new Error(await r.text());
    await loadOrderPlan(true);          // reload first — it rewrites opStatus
    if (st) st.textContent = (central ? '✓ ตั้งเป็น "สั่งจากไดกิโตะ"' : '✓ ตั้งเป็น "สาขาซื้อเอง"')
      + ` · ${code}` + (branch ? ` (เฉพาะ ${branch})` : ' (ทุกสาขา)');
  } catch (e) {
    if (st) st.textContent = 'บันทึกไม่สำเร็จ — ' + String(e).slice(0, 120);
    loadOrderPlan(true);          // put the checkbox back where the server has it
  }
}


// ── forced-order list (บังคับสั่ง) ────────────────────────────────────────
// backend/data/forced_order_*.csv = the central warehouse's own sheet. A listed
// ingredient defaults to "สั่งจากไดกิโตะ" (monitored), everything else to "สาขาซื้อเอง";
// the tick box still overrides either way. The badge says WHY a row is ticked,
// and the note names the listed items no recipe uses - those cannot be monitored.
function opForcedBadge(r) {
  if (!r.forced) return '';
  const imp = r.forced_group === 'import';
  const tip = (imp ? 'ซื้อต่างประเทศ — ส่วนกลางสั่งล็อตใหญ่ สาขาต้องสั่งจากไดกิโตะ' : 'รายการบังคับสั่งจากไดกิโตะ')
    + (r.forced_name ? ` · ในใบคลัง: ${r.forced_name}` : '') + (r.forced_as ? ` (รหัส ${r.forced_as})` : '');
  return ` <span title="${esc(tip)}" style="font-size:10px;padding:1px 6px;border-radius:999px;white-space:nowrap;${imp
    ? 'background:#fef2f2;color:#b91c1c;border:1px solid #fecaca' : 'background:#f0fdfa;color:#0f766e;border:1px solid #99f6e4'}">บังคับสั่ง${imp ? ' · ตปท.' : ''}</span>`;
}

async function opLoadForcedNote(meta) {
  const anchor = document.getElementById('opCoverage'); if (!anchor) return;
  let el = document.getElementById('opForcedNote');
  if (!el) { el = document.createElement('div'); el.id = 'opForcedNote'; anchor.parentNode.insertBefore(el, anchor); }
  if (!meta || !meta.items) { el.innerHTML = ''; return; }
  let d = null;
  try { d = await api(`${API}/api/ingredients/forced-order`); } catch(e) { el.innerHTML = ''; return; }
  const sm = d.summary || {}, miss = (d.items || []).filter(i => !i.in_recipes);
  el.innerHTML = `<div class="card p-3 text-xs mb-3" style="background:#f0fdfa;border:1px solid #0f766e;color:#134e4a">
    <b>รายการบังคับสั่ง ${fmtN(sm.items)} รายการ</b> (ซื้อต่างประเทศ ${fmtN(sm.import)}) ตามใบคลัง ${uiQ('op:forced', 'รายการบังคับสั่ง', '<p>รายการตามใบคลังติ๊ก “สั่งจากไดกิโตะ” ให้อัตโนมัติ ส่วนวัตถุดิบอื่นถือเป็น “สาขาซื้อเอง” และไม่อยู่ในจดหมาย/การตรวจการสั่ง (ติ๊กเพิ่มหรือเอาออกเองได้ ระบบจำไว้)</p>')}
    · ติดตามได้ <b>${fmtN(sm.monitorable)}</b>${sm.via_alias ? ` (ในนี้ ${fmtN(sm.via_alias)} รายการจับคู่ผ่านรหัสในสูตร)` : ''}
    ${miss.length ? `<div class="mt-1" style="color:var(--amber-ink)">⚠ ยังไม่มีในสูตรใดเลย ${miss.length} รายการ ระบบจึงคำนวณและติดตามให้ไม่ได้<ul style="margin:4px 0 0;padding-left:20px;list-style:disc">${miss.map(i => `<li><b>${esc(i.code)}</b> ${esc(i.name)}</li>`).join('')}</ul></div>` : ''}
    <div class="mt-1" style="color:var(--text-muted)">ไฟล์: ${esc(d.file || '')}</div></div>`;
}

// ── bulk sourcing ───────────────────────────────────────────────────────────
// All 256 ingredients default to 'สั่งจากไดกิโตะ', so "monitor only the main
// ones" meant unticking some 200 boxes one at a time. These set many at once.
//
// Family mode is refused throughout: rolling up by name blanks `code`, and the
// API silently skips a row with an empty ingredient_code — the user would get a
// success message and no change.

function opRenderBulkNote() {
  const el = document.getElementById('opBulkNote');
  if (!el) return;
  const fam = document.getElementById('opFamily')?.checked;
  const br  = document.getElementById('opBranch')?.value || '';
  const n   = _opLastRows.length;
  const nLocal = _opLastRows.filter(r => r.sourcing === 'local').length;
  const nCodes = new Set(_opLastRows.filter(r => r.code).map(r => r.code)).size;
  el.innerHTML = fam
    ? '<span style="color:var(--amber-ink)">ปิด “รวมชื่อคล้ายกัน” ก่อนจึงจะเลือกเป็นชุดได้</span>'
    : 'กำลังแสดง <b>' + n + '</b> แถว (<b>' + nCodes + '</b> รหัส) · สาขาซื้อเอง <b>' + nLocal + '</b>'
      + (br ? ' · ตั้งเฉพาะ <b>' + esc(br) + '</b>' : ' · ตั้งเป็นค่าเริ่มต้น <b>ทุกสาขา</b>');
}

async function _opPostSourcing(rows, label) {
  const st = document.getElementById('opStatus');
  if (st) st.textContent = 'กำลังบันทึก…';
  try {
    const r = await fetch(`${API}/api/ingredients/sourcing`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(rows),
    });
    if (!r.ok) throw new Error(await r.text());
    await loadOrderPlan(true);            // reload first — it rewrites opStatus
    if (st) st.textContent = '✓ ' + label + ' · ' + rows.length + ' รายการ';
  } catch (e) {
    if (st) st.textContent = 'บันทึกไม่สำเร็จ — ' + String(e).slice(0, 120);
    loadOrderPlan(true);
  }
}

// One entry per ingredient CODE, value summed across its rows.
//
// The table lists a row per (code, unit) — code 03-01-060 appears as both
// "หอยนางรม/ตัว" at ฿932k and "หอยนางรมญี่ปุ่น/ชิ้น" at ฿18k. But
// ingredient_sourcing is keyed by (code, branch), one setting per code. Ranking
// the ROWS would put the first inside a top-30 and the second outside it, post
// the same code as central and then local, and the later write would win —
// silently handing the ฿932k oyster to the branches. Four of the top thirty do
// this today. Summing first also ranks correctly: a code split across units is
// worth the sum of its parts, not its largest part.
function _opBulkGuard() {
  if (document.getElementById('opFamily')?.checked) {
    uiNotice('โหมด “รวมชื่อคล้ายกัน” รวมหลายรหัสเข้าเป็นบรรทัดเดียว จึงไม่มีรหัสให้บันทึก\n'
        + 'ปิดโหมดนี้ก่อนจึงเลือกเป็นชุดได้');
    return null;
  }
  const by = new Map();
  for (const r of _opLastRows) {
    if (!r.code) continue;
    const e = by.get(r.code);
    if (e) { e.value_month += (r.value_month || 0); e.units++; }
    else by.set(r.code, { code: r.code, name: r.name,
                          value_month: r.value_month || 0, units: 1 });
  }
  const rows = [...by.values()].sort((a, b) => b.value_month - a.value_month);
  if (!rows.length) { uiNotice('ไม่มีรายการให้ตั้งค่า'); return null; }
  return rows;
}

// Apply to every row currently listed — the type / search / include filters
// above the table are how you scope this to a subset.
async function opBulkSourcing(central) {
  const rows = _opBulkGuard();
  if (!rows) return;
  const branch = document.getElementById('opBranch')?.value || '';
  const word = central ? 'สั่งจากไดกิโตะ (ติดตาม)' : 'สาขาซื้อเอง (ไม่ติดตาม)';
  const scope = branch ? 'เฉพาะสาขา ' + branch : 'ทุกสาขา';
  if (!await uiConfirm('ตั้ง ' + rows.length + ' วัตถุดิบที่แสดงอยู่ เป็น “' + word + '”\n'
             + 'ขอบเขต: ' + scope + '\n\nยืนยันหรือไม่?')) return;
  _opPostSourcing(rows.map(r => ({ ingredient_code: r.code, branch,
                                   mode: central ? 'central' : 'local' })),
                  'ตั้งเป็น “' + word + '”');
}

// Keep the N biggest by monthly value, hand the rest to the branches. The table
// arrives sorted by ฿/เดือน descending, so "top N" is the first N rows — but
// only while nothing is filtering them, or the ranking would be over a subset
// and the tail silently spared.
async function opKeepTopN() {
  const rows = _opBulkGuard();
  if (!rows) return;
  const inc = document.getElementById('opInclude')?.value || 'all';
  const q   = (document.getElementById('opQ')?.value || '').trim();
  const typ = document.getElementById('opType')?.value || '';
  if (inc !== 'all' || q || typ) {
    uiNotice('อันดับต้องคิดจากวัตถุดิบทั้งหมด\n'
        + 'ตั้งเป็น “ทุกรายการ” และล้างตัวกรองประเภทกับคำค้นก่อน');
    return;
  }
  const n = parseInt(document.getElementById('opTopN')?.value, 10);
  if (!n || n < 1) { uiNotice('ใส่จำนวนรายการที่ต้องการติดตาม'); return; }
  if (n >= rows.length) {
    uiNotice('มีวัตถุดิบทั้งหมด ' + rows.length + ' รายการ — ใส่จำนวนที่น้อยกว่านี้');
    return;
  }
  const branch = document.getElementById('opBranch')?.value || '';
  const keep = rows.slice(0, n), drop = rows.slice(n);
  const kv = keep.reduce((a, r) => a + (r.value_month || 0), 0);
  const tv = rows.reduce((a, r) => a + (r.value_month || 0), 0);
  const share = tv ? Math.round(kv / tv * 100) : 0;
  const split = rows.filter(r => r.units > 1).length;
  if (!await uiConfirm('ติดตาม ' + keep.length + ' วัตถุดิบที่มูลค่าสูงสุด (คิดเป็น ' + share + '% ของมูลค่าสั่งทั้งหมด)\n'
             + 'อีก ' + drop.length + ' รายการตั้งเป็น “สาขาซื้อเอง”\n'
             + (split ? 'มี ' + split + ' รหัสที่แยกหลายหน่วยในตาราง — รวมเป็นรายการเดียวแล้ว\n' : '')
             + 'ขอบเขต: ' + (branch ? 'เฉพาะสาขา ' + branch : 'ทุกสาขา') + '\n\nยืนยันหรือไม่?')) return;
  _opPostSourcing(
    keep.map(r => ({ ingredient_code: r.code, branch, mode: 'central' }))
      .concat(drop.map(r => ({ ingredient_code: r.code, branch, mode: 'local' }))),
    'ติดตาม ' + keep.length + ' รายการหลัก (' + share + '% ของมูลค่า)');
}


// ═══════════════════════════════════════════════════════════════════════════
// Order setup — pack sizes + festival calendar
// ═══════════════════════════════════════════════════════════════════════════
let _osPacks = [], _osEdits = {}, _osSub = 'pack';
// The วัตถุดิบและใบสั่งซื้อ bar (01-core.js) reads, sets and shows this tab's panes through here.
_SUPPLY_PANE.ordersetup = { get: () => _osSub, set: k => { _osSub = k; }, show: k => osSetSub(k) };

function osSetSub(k, quiet) {   // quiet: only show the pane (the caller loads it)
  _osSub = k;
  document.getElementById('os-pane-pack').style.display = k === 'pack' ? '' : 'none';
  document.getElementById('os-pane-cal').style.display  = k === 'cal'  ? '' : 'none';
  if (k === 'cal' && !quiet) osLoadCalendar();
  try { _updateClusterBar(activeTab); } catch (e) { /* no bar on this page */ }
}

async function loadOrderSetup(force) {
  osSetSub(_osSub, true);          // reopen the remembered pane (the bar may have preset it)
  if (!force && _osPacks.length) { osRenderPacks(); return; }
  try {
    const d = await api(`${API}/api/ingredients/order-units`);
    _osPacks = d.rows || []; _osEdits = {};
    const s = d.summary || {};
    document.getElementById('osPackSummary').textContent =
      `ทั้งหมด ${fmtN(s.total)} รายการ · ตั้งขนาดบรรจุแล้ว ${fmtN(s.with_pack)} · ยังไม่ตั้ง ${fmtN(s.fallback)}`;
  } catch (e) { _osPacks = []; }
  osRenderPacks();
  osLoadCalendar();
}

function osRenderPacks() {
  const q = (document.getElementById('osPackQ')?.value || '').trim().toLowerCase();
  const onlyMissing = document.getElementById('osPackOnlyMissing')?.checked;
  const rows = _osPacks.filter(r =>
    (!onlyMissing || !r.pack_known) &&
    (!q || r.code.toLowerCase().includes(q) || (r.name || '').toLowerCase().includes(q)));
  const inp = (code, f, v, w) =>
    `<input value="${v == null ? '' : esc(v)}" data-osf="${f}" data-oscode="${jsAttr(code)}"
       oninput="osEdit(this)" style="width:${w}px;border:1px solid var(--border);border-radius:var(--r-md);
       padding:3px 6px;font-size:11.5px;background:var(--bg-input);color:var(--text-primary);text-align:right">`;
  document.getElementById('osPackBody').innerHTML = rows.map(r => `
    <tr class="border-b" style="border-color:var(--border)">
      <td class="py-1 pr-2" style="font-family:monospace;font-size:10.5px;color:var(--text-muted)">${esc(r.code)}</td>
      <td class="py-1 pr-2">${esc(r.name)}</td>
      <td class="py-1 pr-2" style="color:var(--text-muted)">${esc(r.base_unit)}</td>
      <td class="py-1 pr-2 text-right" style="color:var(--text-muted)">${fmtN(r.n_lines)}</td>
      <td class="py-1 pr-2"><input value="${esc(r.order_unit)}" data-osf="order_unit" data-oscode="${jsAttr(r.code)}"
          oninput="osEdit(this)" style="width:120px;border:1px solid var(--border);border-radius:var(--r-md);padding:3px 6px;
          font-size:11.5px;background:var(--bg-input);color:var(--text-primary)"></td>
      <td class="py-1 pr-2 text-right">${inp(r.code, 'qty_per_order', r.qty_per_order, 80)}</td>
      <td class="py-1 pr-2 text-right">${inp(r.code, 'price_per_order', r.price_per_order, 80)}</td>
      <td class="py-1 pr-2 text-right">${inp(r.code, 'round_step', r.round_step, 55)}</td>
      <td class="py-1 pr-2 text-right">${inp(r.code, 'moq', r.moq, 55)}</td>
      <td class="py-1">${r.pack_known
        ? '<span style="color:var(--good-ink)">✓ ตั้งแล้ว</span>'
        : '<span style="color:var(--amber-ink)">◇ ใช้หน่วยมาตรฐาน</span>'}</td>
    </tr>`).join('') ||
    '<tr><td colspan="10" class="py-4 text-center" style="color:var(--text-muted)">ไม่พบรายการ</td></tr>';
}

function osEdit(el) {
  const code = el.dataset.oscode, f = el.dataset.osf;
  const base = _osPacks.find(r => r.code === code);
  if (!base) return;
  const e = _osEdits[code] || (_osEdits[code] = Object.assign({}, base));
  e[f] = (f === 'order_unit') ? el.value : (el.value === '' ? null : parseFloat(el.value));
  el.style.borderColor = '#4a70b0';
}

async function osSavePacks() {
  const rows = Object.values(_osEdits).map(e => ({
    ingredient_code: e.code, base_unit: e.base_unit, order_unit: e.order_unit || e.base_unit,
    qty_per_order: Number(e.qty_per_order) || 0, price_per_order: e.price_per_order,
    moq: Number(e.moq) || 1, round_step: Number(e.round_step) || 1,
  }));
  if (!rows.length) { document.getElementById('osPackSummary').textContent = 'ยังไม่มีรายการที่แก้ไข'; return; }
  const bad = rows.filter(r => !(r.qty_per_order > 0));
  if (bad.length) {
    uiNotice('ขนาดบรรจุต้องมากกว่า 0 — ตรวจสอบ: ' + bad.map(b => b.ingredient_code).join(', '));
    return;
  }
  try {
    const r = await fetch(`${API}/api/ingredients/order-units`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(rows) });
    if (!r.ok) throw new Error(await r.text());
    document.getElementById('osPackSummary').textContent = `✓ บันทึก ${rows.length} รายการแล้ว`;
    loadOrderSetup(true);
  } catch (e) {
    document.getElementById('osPackSummary').textContent = 'บันทึกไม่สำเร็จ — ' + String(e).slice(0, 120);
  }
}

// Setting a few hundred pack sizes one input at a time is not a thing anyone
// does on a screen — the sheet goes out already carrying what is set, comes back
// edited, and the rows it does not mention are left alone.
function osPackTemplate(onlyMissing) {
  const p = new URLSearchParams();
  if (onlyMissing) p.set('only_missing', 'true');
  window.open(`${API}/api/ingredients/order-units.template.xlsx?${p}`, '_blank');
}

async function osImportPacks(input) {
  await _opImportFile(input, `${API}/api/ingredients/order-units/import`,
                      'osImportStatus', 'osImportReport', () => loadOrderSetup(true));
}

async function osLoadCalendar() {
  const sel = document.getElementById('osCalBranch');
  if (sel && !sel.options.length) {
    try {
      const bs = await api(`${API}/api/branches`);
      sel.innerHTML = '<option value="">— ทุกสาขา —</option>' +
        (bs || []).filter(b => b.active !== false && b.name !== 'ALL')
          .map(b => `<option value="${esc(b.name)}">${esc(b.name)}</option>`).join('');
    } catch (e) {}
  }
  let d;
  try { d = await api(`${API}/api/ingredients/demand-calendar`); } catch (e) { return; }
  document.getElementById('osCalBody').innerHTML = (d.rows || []).map(r => `
    <tr class="border-b" style="border-color:var(--border)">
      <td class="py-1.5 pr-2 font-semibold">${esc(r.name)}</td>
      <td class="py-1.5 pr-2">${esc(fmtDay(r.date_from))}</td>
      <td class="py-1.5 pr-2">${esc(fmtDay(r.date_to))}</td>
      <td class="py-1.5 pr-2">${esc(r.branch || 'ทุกสาขา')}</td>
      <td class="py-1.5 pr-2 text-right">${r.factor ? '×' + r.factor : '<span style="color:var(--text-muted)">คิดจากปีก่อน</span>'}</td>
      <td class="py-1.5 pr-2" style="color:var(--text-muted)">${esc(r.note || '')}</td>
      <td class="py-1.5 text-right"><a href="#" onclick="osDelEvent(${r.event_id});return false" style="color:var(--bad-ink)">ลบ</a></td>
    </tr>`).join('') ||
    '<tr><td colspan="7" class="py-4 text-center" style="color:var(--text-muted)">ยังไม่มีช่วงเทศกาล/โปรโมชัน</td></tr>';
}

async function osAddEvent() {
  const name = document.getElementById('osCalName').value.trim();
  const from = document.getElementById('osCalFrom').value;
  const to   = document.getElementById('osCalTo').value;
  const st   = document.getElementById('osCalStatus');
  if (!name || !from || !to) { st.textContent = 'กรอกชื่อและช่วงวันที่ให้ครบ'; return; }
  const fRaw = document.getElementById('osCalFactor').value;
  try {
    const r = await fetch(`${API}/api/ingredients/demand-calendar`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify([{ name, date_from: from, date_to: to,
        branch: document.getElementById('osCalBranch').value || '',
        factor: fRaw === '' ? null : parseFloat(fRaw) }]) });
    if (!r.ok) throw new Error(await r.text());
    st.textContent = '✓ เพิ่มแล้ว';
    document.getElementById('osCalName').value = '';
    document.getElementById('osCalFactor').value = '';
    osLoadCalendar();
  } catch (e) { st.textContent = 'บันทึกไม่สำเร็จ — ' + String(e).slice(0, 110); }
}

async function osDelEvent(id) {
  if (!await uiConfirm('ลบช่วงนี้ออกจากปฏิทิน?')) return;
  try { await fetch(`${API}/api/ingredients/demand-calendar/${id}`, { method: 'DELETE' }); } catch (e) {}
  osLoadCalendar();
}

// ═══════════════════════════════════════════════════════════════════════════
// Order cycles — issue, record what was actually ordered, warn
// ═══════════════════════════════════════════════════════════════════════════
let _opCycleId = null;

async function opIssueCycle() {
  const branch = document.getElementById('opBranch')?.value || '';
  const st = document.getElementById('opCycleStatus');
  const scope = branch || 'ทุกสาขา';
  if (!await uiConfirm(`ออกรอบสั่งสำหรับ ${scope}?\n\nจะบันทึกตัวเลขที่แนะนำ ณ ตอนนี้ไว้เป็นหลักฐาน `
             + `เพื่อใช้เทียบกับที่สาขาสั่งจริง (ออกซ้ำรอบเดิม = เขียนทับของเดิม)`)) return;
  st.textContent = 'กำลังออกรอบสั่ง…';
  try {
    const r = await fetch(`${API}/api/ingredients/order-plan/issue`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        branch: branch || null,
        horizon: document.getElementById('opHorizon')?.value || 'week',
        period_start: document.getElementById('opCycleStart')?.value || null,
        days: parseInt(document.getElementById('opDays')?.value || '28', 10),
        basis: _opBasis, buffer_pct: parseFloat(document.getElementById('opBuffer')?.value || '10'),
        top: 40 }) });
    if (!r.ok) throw new Error(await r.text());
    const d = await r.json();
    st.textContent = `✓ ออกรอบสั่งแล้ว ${d.issued.length} สาขา · ${d.period_start} ถึง ${d.period_end}`;
    opLoadCycles();
  } catch (e) { st.textContent = 'ออกรอบสั่งไม่สำเร็จ — ' + String(e).slice(0, 130); }
}

async function opLoadCycles() {
  const branch = document.getElementById('opBranch')?.value || '';
  const tol = document.getElementById('opTolerance')?.value || '20';
  const lbl = tplById('opTolLabel'); if (lbl) lbl.textContent = tol;
  let d;
  try {
    d = await api(`${API}/api/ingredients/order-cycles?limit=40`
      + (branch ? `&branch=${encodeURIComponent(branch)}` : ''));
  } catch (e) { return; }
  const rows = d.rows || [];
  document.getElementById('opCycleBody').innerHTML = rows.map(r => {
    const c = r.compliance || {};
    const badge = c.compliant
      ? '<span style="color:var(--good-ink);font-weight:600">✓ ครบตามแผน</span>'
      : `<span style="color:var(--bad-ink);font-weight:600">⚠ ต้องแจ้งเตือน</span>`;
    return `<tr class="border-b" style="border-color:var(--border)">
      <td class="py-1.5 pr-2"><a href="#" onclick="opOpenCycle(${r.issue_id});return false" style="color:var(--teal-ink);font-weight:600">${esc(r.branch)}</a></td>
      <td class="py-1.5 pr-2">${esc(fmtDay(r.period_start))} → ${esc(fmtDay(r.period_end))}</td>
      <td class="py-1.5 pr-2" style="color:var(--text-muted)">${esc(String(r.issued_at || '').slice(0, 16))}</td>
      <td class="py-1.5 pr-2 text-right">${fmtN(c.lines)}</td>
      <td class="py-1.5 pr-2 text-right" style="color:${c.not_ordered ? 'var(--bad-ink)' : 'var(--text-muted)'}">${fmtN(c.not_ordered)}</td>
      <td class="py-1.5 pr-2 text-right" style="color:${c.under ? 'var(--amber-ink)' : 'var(--text-muted)'}">${fmtN(c.under)}</td>
      <td class="py-1.5 pr-2 text-right">${c.gap_value ? '฿' + fmtN(c.gap_value) : '—'}</td>
      <td class="py-1.5">${badge}</td></tr>`;
  }).join('') ||
  '<tr><td colspan="8" class="py-4 text-center" style="color:var(--text-muted)">ยังไม่มีรอบสั่งที่ออกไว้ — กด “ออกรอบสั่ง”</td></tr>';
}

async function opOpenCycle(issueId) {
  _opCycleId = issueId;
  const tol = document.getElementById('opTolerance')?.value || '20';
  const el = document.getElementById('opCycleDetail');
  el.style.display = ''; el.innerHTML = '<p class="text-xs" style="color:var(--text-muted)">กำลังโหลด…</p>';
  let d;
  try { d = await api(`${API}/api/ingredients/order-compliance?issue_id=${issueId}&tolerance_pct=${tol}`); }
  catch (e) { el.innerHTML = '<p class="text-xs" style="color:var(--bad-ink)">โหลดไม่สำเร็จ</p>'; return; }
  const i = d.issue || {}, s = d.summary || {};
  const cls = { not_ordered: 'var(--bad-ink)', under: '#b45309', ok: 'var(--good-ink)' };
  const lab = { not_ordered: 'ไม่ได้สั่ง', under: 'ต่ำกว่าแผน', ok: 'ครบ' };
  el.innerHTML = `
    <div class="flex items-center justify-between flex-wrap gap-2 mb-2">
      <h3 class="text-sm font-bold" style="color:var(--text-primary)">
        ${esc(i.branch)} · รอบ ${esc(fmtDay(i.period_start))} → ${esc(fmtDay(i.period_end))}</h3>
      <div class="flex items-center gap-2">
        <span class="text-xs" style="color:var(--text-muted)">
          ไม่ได้สั่ง ${fmtN(s.not_ordered)} · ต่ำกว่าแผน ${fmtN(s.under)} · ครบ ${fmtN(s.ok)}</span>
        <button onclick="opSaveActuals()" style="background:#0f766e;color:#fff;border:0;border-radius:var(--r-sm);padding:5px 12px;font-size:12.5px;font-weight:600;cursor:pointer">บันทึกที่สั่งจริง</button>
        <button onclick="window.open('${API}/api/ingredients/compliance-letter.print?issue_id=${issueId}&tolerance_pct=${tol}','_blank')"
          style="background:#b91c1c;color:#fff;border:0;border-radius:var(--r-sm);padding:5px 12px;font-size:12.5px;font-weight:600;cursor:pointer">⚠ จดหมายแจ้งเตือนสาขานี้</button>
      </div>
    </div>
    <div class="overflow-x-auto"><table class="text-xs w-full">
      <thead><tr class="text-left border-b" style="color:var(--text-muted)">
        <th class="py-1.5 pr-2">รหัส</th><th class="py-1.5 pr-2">วัตถุดิบ</th>
        <th class="py-1.5 pr-2 text-right">แผนแจ้งให้สั่ง</th><th class="py-1.5 pr-2 text-right">สั่งจริง</th>
        <th class="py-1.5 pr-2 text-right">% ของแผน</th><th class="py-1.5 pr-2 text-right">ส่วนที่ขาด</th>
        <th class="py-1.5">สถานะ</th></tr></thead>
      <tbody>${(d.rows || []).map(r => `
        <tr class="border-b" style="border-color:var(--border)">
          <td class="py-1 pr-2" style="font-family:monospace;font-size:10.5px;color:var(--text-muted)">${esc(r.ingredient_code)}</td>
          <td class="py-1 pr-2">${esc(r.name)}</td>
          <td class="py-1 pr-2 text-right">${r.rec_units.toLocaleString()} ${esc(r.order_unit)}</td>
          <td class="py-1 pr-2 text-right"><input type="number" min="0" step="any" value="${r.act_units || 0}"
              data-opcode="${jsAttr(r.ingredient_code)}" style="width:80px;border:1px solid var(--border);
              border-radius:var(--r-md);padding:3px 6px;font-size:11.5px;text-align:right;background:var(--bg-input);color:var(--text-primary)"></td>
          <td class="py-1 pr-2 text-right">${r.act_units ? r.pct_of_rec + '%' : '0%'}</td>
          <td class="py-1 pr-2 text-right">${r.status === 'ok' ? '—' : esc(r.gap_disp)}</td>
          <td class="py-1" style="color:${textInk(cls[r.status])};font-weight:600">${lab[r.status]}</td>
        </tr>`).join('')}</tbody></table></div>`;
}

async function opSaveActuals() {
  if (!_opCycleId) return;
  const rows = [...document.querySelectorAll('#opCycleDetail input[data-opcode]')].map(el => ({
    ingredient_code: el.dataset.opcode, qty_units: parseFloat(el.value || '0') || 0 }));
  const st = document.getElementById('opCycleStatus');
  try {
    const r = await fetch(`${API}/api/ingredients/order-actual`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ issue_id: _opCycleId, rows }) });
    if (!r.ok) throw new Error(await r.text());
    st.textContent = `✓ บันทึกจำนวนที่สั่งจริง ${rows.length} รายการ`;
    opLoadCycles(); opOpenCycle(_opCycleId);
  } catch (e) { st.textContent = 'บันทึกไม่สำเร็จ — ' + String(e).slice(0, 130); }
}

function opWarnLetter() {
  const tol = document.getElementById('opTolerance')?.value || '20';
  const hz = document.getElementById('opHorizon')?.value || 'week';
  // a field, not prompt() — prompt() is unsupported in the app shell and threw
  const due = (document.getElementById('opDueDate')?.value || '').trim();
  const p = new URLSearchParams({ horizon: hz, tolerance_pct: tol });
  if (due) p.set('due_date', due);
  const cs = document.getElementById('opCycleStart')?.value || '';
  if (cs) p.set('period_start', cs);
  window.open(`${API}/api/ingredients/compliance-letter.print?${p}`, '_blank');
}

// The same notice, read before it is sent. A warning letter names a branch and
// asks it to answer for a shortfall, so seeing WHO is on it and WHY — before
// anything is printed or handed over — is the difference between a review step
// and an accident.
function opDraftWarnLetter() {
  const tol = document.getElementById('opTolerance')?.value || '20';
  const hz = document.getElementById('opHorizon')?.value || 'week';
  const due = (document.getElementById('opDueDate')?.value || '').trim();
  const p = new URLSearchParams({ horizon: hz, tolerance_pct: tol, preview: '1' });
  if (due) p.set('due_date', due);
  const cs = document.getElementById('opCycleStart')?.value || '';
  if (cs) p.set('period_start', cs);
  const w = window.open('', '_blank');
  if (!w) return;
  w.document.write('<p style="font-family:sans-serif;padding:24px">กำลังสร้างร่างจดหมาย…</p>');
  fetch(`${API}/api/ingredients/compliance-letter.print?${p}`)
    .then(r => r.text())
    .then(html => {
      w.document.open();
      w.document.write(html);
      w.document.close();
    })
    .catch(e => {
      w.document.open();
      w.document.write('<p style="font-family:sans-serif;padding:24px;color:#b91c1c">สร้างร่างไม่สำเร็จ: '
                       + String(e).slice(0, 200) + '</p>');
      w.document.close();
    });
}


// ── ปริมาณที่สั่งจริง: Excel out, Excel back in ────────────────────────────
function opActualTemplate() {
  const p = new URLSearchParams();
  const br = document.getElementById('opBranch')?.value || '';
  if (br) p.set('branch', br);
  const cs = document.getElementById('opCycleStart')?.value || '';
  if (cs) p.set('period_start', cs);
  window.open(`${API}/api/ingredients/order-actual.template.xlsx?${p}`, '_blank');
}

async function opImportActual(input) {
  await _opImportFile(input, `${API}/api/ingredients/order-actual/import`,
                      'opImportStatus', 'opImportReport', () => {
                        opLoadCycles();
                        opLoadAccuracy();
                      });
}

// Shared by every "upload the filled sheet" button on this tab. Always runs a
// dry run FIRST and shows what would happen: an import that silently drops the
// rows it could not read is worse than one that refuses, and the operator is the
// only one who can tell a typo from a genuinely new ingredient.
async function _opImportFile(input, url, statusId, reportId, after) {
  const file = input?.files?.[0];
  const st = document.getElementById(statusId);
  const rep = document.getElementById(reportId);
  if (!file) return;
  const show = (msg, color) => { if (st) { st.textContent = msg; st.style.color = color || 'var(--text-muted)'; } };
  const send = async (dry) => {
    const fd = new FormData();
    fd.append('file', file, file.name);
    const r = await fetch(`${url}?dry_run=${dry ? 'true' : 'false'}`, { method: 'POST', body: fd });
    const text = await r.text();
    let j;
    try { j = JSON.parse(text); } catch (e) { j = null; }
    if (!r.ok) throw new Error((j && j.detail) || text.slice(0, 200));
    return j;
  };
  try {
    show('กำลังตรวจไฟล์…');
    const pre = await send(true);
    const n = pre.would_save || 0;
    const errs = pre.errors || [];
    if (rep) rep.innerHTML = _opImportReportHtml(pre, true);
    if (!n) {
      show(`ไม่มีแถวที่นำเข้าได้ (อ่านได้ ${pre.rows_read || 0} แถว)`, 'var(--bad-ink)');
      input.value = '';
      return;
    }
    if (errs.length && !await uiConfirm(`อ่านไฟล์ได้ ${n} แถว แต่มี ${errs.length} แถวที่มีปัญหาและจะถูกข้าม\n\n`
        + errs.slice(0, 5).map(e => `• แถว ${e.row || '-'}: ${e.why}`).join('\n')
        + `\n\nต้องการนำเข้า ${n} แถวที่เหลือหรือไม่?`)) {
      show('ยกเลิกการนำเข้า');
      input.value = '';
      return;
    }
    show('กำลังนำเข้า…');
    const res = await send(false);
    if (rep) rep.innerHTML = _opImportReportHtml(res, false);
    show(`✓ นำเข้าแล้ว ${res.saved || 0} แถว` + (res.blank ? ` · เว้นว่าง ${res.blank}` : ''), 'var(--good-ink)');
    if (typeof after === 'function') after();
  } catch (e) {
    show('นำเข้าไม่สำเร็จ — ' + String(e.message || e).slice(0, 160), 'var(--bad-ink)');
  } finally {
    input.value = '';        // let the same file be picked again after a fix
  }
}

function _opImportReportHtml(d, dry) {
  const errs = d.errors || [];
  const head = dry
    ? `ตรวจไฟล์ <b>${esc(d.sheet || '')}</b> · อ่านได้ ${fmtN(d.rows_read || 0)} แถว · นำเข้าได้ ${fmtN(d.would_save || 0)} แถว`
    : `นำเข้าแล้ว <b>${fmtN(d.saved || 0)}</b> แถว จากไฟล์ <b>${esc(d.sheet || '')}</b>`;
  const blank = d.blank ? ` · เว้นว่าง ${fmtN(d.blank)} แถว (ยังไม่กรอก — ไม่ถูกแก้ไข)` : '';
  if (!errs.length) {
    return `<div class="card p-3 text-[11px]" style="background:#f0fdf4;border:1px solid #16a34a;color:#166534">
      ✓ ${head}${blank}</div>`;
  }
  return `<div class="card p-3 text-[11px]" style="background:#fffbeb;border:1px solid #f59e0b;color:#92400e">
    ${head}${blank}<br><b>แถวที่มีปัญหา (${errs.length}) — ถูกข้าม ไม่ได้บันทึก:</b>
    <ul style="margin:.35em 0 0;padding-left:1.2em">
      ${errs.slice(0, 12).map(e => `<li>แถว ${esc(e.row || e.issue_id || '-')}${e.code || e.branch ? ' · ' + esc(e.code || e.branch) : ''} — ${esc(e.why)}</li>`).join('')}
      ${errs.length > 12 ? `<li>… และอีก ${errs.length - 12} แถว</li>` : ''}
    </ul></div>`;
}


// ── forecast accuracy ──────────────────────────────────────────────────────
// Compliance answers "did they order what we said". This answers "was what we
// said right" — the frozen recommendation vs what the branch actually consumed
// in that cycle. The suggested buffer is a SERVICE LEVEL, not a mean: ordering
// the average leaves you short half the time.
let _opAccSuggested = null;

async function opLoadAccuracy() {
  const branch = document.getElementById('opBranch')?.value || '';
  const sl = document.getElementById('opServiceLevel')?.value || '80';
  const head = document.getElementById('opAccHead');
  const body = document.getElementById('opAccBody');
  if (!head) return;
  let d;
  try {
    d = await api(`${API}/api/ingredients/order-accuracy?service_level=${sl}`
      + (branch ? `&branch=${encodeURIComponent(branch)}` : ''));
  } catch (e) { return; }
  if (!d || d.available === false) {
    _opAccSuggested = null;
    head.innerHTML = `<div class="card p-3 text-xs" style="background:var(--bg-subtle);color:var(--text-muted)">
      ${esc(d?.reason || 'ยังประเมินไม่ได้')} — ออกรอบสั่งแล้วรอให้รอบนั้นผ่านไปก่อน จึงจะเทียบกับการใช้จริงได้</div>`;
    body.innerHTML = '';
    return;
  }
  const h = d.headline || {};
  _opAccSuggested = h.suggested_buffer_pct;
  const drift = h.median_ratio != null ? (h.median_ratio - 1) * 100 : null;
  head.innerHTML = `<div class="grid grid-cols-2 md:grid-cols-4 gap-3">` + [
    { l:'รอบที่ประเมินได้', v:fmtN(h.cycles_evaluated), s:`${fmtN(h.lines_evaluated)} บรรทัด · ข้อมูลถึง ${esc(h.data_through)}`, c:'#64748b' },
    { l:'ใช้จริง vs พยากรณ์', v:(drift==null?'—':(drift>0?'+':'')+drift.toFixed(1)+'%'),
      s:(drift==null?'':drift>0?'พยากรณ์ต่ำไป':'พยากรณ์สูงไป'), c:(drift>5?'#dc2626':drift<-5?'#0891b2':'#059669') },
    { l:'% เผื่อที่ใช้อยู่', v:(h.buffer_used_median??'—')+'%', s:'ค่ากลางของรอบที่ผ่านมา', c:'#64748b' },
    { l:'% เผื่อที่ควรใช้', v:(h.suggested_buffer_pct??'—')+'%', s:`ครอบคลุม ${h.service_level_pct}% ของรอบ`, c:'#7a66b8' },
  ].map(k => `<div class="card p-3" style="border-top:3px solid ${k.c}"><p class="text-[10.5px] uppercase" style="color:var(--text-muted)">${k.l}</p><p class="text-xl font-bold" style="color:${textInk(k.c)}">${k.v}</p><p class="text-[10.5px]" style="color:var(--text-muted)">${esc(k.s)}</p></div>`).join('') + `</div>`;

  const vlab = { under:['พยากรณ์ต่ำไป','var(--bad-ink)'], over:['พยากรณ์สูงไป','#0891b2'], ok:['พอดี','var(--good-ink)'] };
  body.innerHTML = (d.by_ingredient || []).map(e => {
    const [txt, col] = vlab[e.verdict] || vlab.ok;
    return `<tr class="border-b" style="border-color:var(--border)">
      <td class="py-1 pr-2" style="font-family:monospace;font-size:10.5px;color:var(--text-muted)">${esc(e.code)}</td>
      <td class="py-1 pr-2">${esc(e.name)}</td>
      <td class="py-1 pr-2 text-right">${esc(e.rec_disp)}</td>
      <td class="py-1 pr-2 text-right">${esc(e.used_disp)}</td>
      <td class="py-1 pr-2 text-right font-semibold" style="color:${textInk(col)}">×${e.ratio_median ?? '—'}</td>
      <td class="py-1 pr-2 text-right" style="color:var(--text-muted)">${fmtN(e.n)}</td>
      <td class="py-1 pr-2 text-right">${e.suggested_buffer_pct == null ? '<span style="color:var(--text-muted)">รอบยังน้อย</span>' : e.suggested_buffer_pct + '%'}</td>
      <td class="py-1" style="color:${textInk(col)};font-weight:600">${txt}</td></tr>`;
  }).join('') || '<tr><td colspan="8" class="py-4 text-center" style="color:var(--text-muted)">ยังไม่มีรอบที่ปิดแล้ว</td></tr>';
}

function opApplySuggestedBuffer() {
  if (_opAccSuggested == null) { uiNotice('ยังไม่มีค่าที่แนะนำ — ต้องมีรอบสั่งที่ปิดแล้วอย่างน้อย 1 รอบ'); return; }
  const el = document.getElementById('opBuffer');
  if (el) { el.value = _opAccSuggested; loadOrderPlan(true); }
}


// ═══════════════════════════════════════════════════════════════════════════
// ยอดซื้อวัตถุดิบ เทียบ ยอดขาย รายสาขา (รายสัปดาห์)
// What each branch SPENT on ingredients comes from the purchasing system as a
// spreadsheet; sales for the same week are ours. The pair gives the ratio ops
// actually manage to. Deliberately NOT the BoM food cost: that says what the
// recipes should have consumed, this says what was bought — buying runs ahead
// of selling, so a single week swings and the period average is the honest read.
// ═══════════════════════════════════════════════════════════════════════════
let _ipReady = false, _ipLast = null, _ipChartPct = null, _ipChartAmt = null, _ipChartBranch = null;

async function _ipInitFilters() {
  if (_ipReady) return;
  const sel = document.getElementById('ipBranch');
  if (!sel) return;
  try {
    const branches = await api(`${API}/api/branches`);
    sel.innerHTML = '<option value="">— ทุกสาขา —</option>' +
      (branches || []).filter(b => b.active !== false && b.name !== 'ALL')
        .map(b => `<option value="${esc(b.name)}">${esc(b.name)}</option>`).join('');
    const cur = (typeof getBranch === 'function') ? getBranch() : '';
    if (cur && (branches || []).some(b => b.name === cur)) sel.value = cur;
  } catch (e) { sel.innerHTML = '<option value="">— ทุกสาขา —</option>'; }
  _ipReady = true;
}

function _ipParams() {
  const p = new URLSearchParams();
  const br = document.getElementById('ipBranch')?.value || '';
  if (br) p.set('branch', br);
  if (!br && typeof getGroup === 'function' && getGroup()) p.set('group', getGroup());
  const f = document.getElementById('ipFrom')?.value || '';
  const t = document.getElementById('ipTo')?.value || '';
  if (f) p.set('date_from', f);
  if (t) p.set('date_to', t);
  return p;
}

async function loadIngPurchase(force) {
  await _ipInitFilters();
  const p = _ipParams();
  const key = p.toString();
  if (!force && _tabLastKey.ingpurchase === key) return;
  _tabLastKey.ingpurchase = key;
  const st = document.getElementById('ipStatus');
  if (st) st.textContent = 'กำลังโหลด…';
  let d;
  try { d = await api(`${API}/api/ingredients/purchases?${p}`); }
  catch (e) { if (st) st.textContent = 'โหลดข้อมูลไม่สำเร็จ'; return; }
  _ipLast = d;
  const m = d.meta || {}, t = d.totals || {};
  // Seed the pickers from the window the server resolved, so they show the same
  // range the numbers are for instead of staying blank.
  const fEl = document.getElementById('ipFrom'), tEl = document.getElementById('ipTo');
  if (fEl && !fEl.value) fEl.value = m.date_from;
  if (tEl && !tEl.value) tEl.value = m.date_to;
  if (st) st.textContent = `สัปดาห์ ${fmtDay(m.week_from)} → ${fmtDay(m.week_to)} · ${m.branch || 'ทุกสาขา'} · `
    + `นำเข้าแล้ว ${fmtN(m.imported_weeks || 0)} ช่อง · ยังไม่นำเข้า ${fmtN(m.missing_weeks || 0)} ช่อง`;

  const pctCol = v => v == null ? '#94a3b8' : v >= 40 ? '#dc2626' : v >= 33 ? '#f59e0b' : '#059669';
  document.getElementById('ipKpis').innerHTML = [
    { l:'ยอดซื้อวัตถุดิบรวม', v:'฿'+fmt(t.amount||0), s:'เฉพาะสัปดาห์ที่นำเข้าแล้ว', c:'#7a66b8' },
    { l:'ยอดขายรวม (ex-VAT)', v:'฿'+fmt(t.revenue||0), s:'ช่วงเดียวกัน', c:'#0891b2' },
    { l:'% ยอดซื้อต่อยอดขาย', v:(t.pct!=null?t.pct+'%':'—'), s:'ทั้งช่วง (ค่าที่ควรใช้ตัดสิน)', c:pctCol(t.pct) },
    { l:'สัปดาห์ที่ยังไม่นำเข้า', v:fmtN(m.missing_weeks||0), s:'ช่อง สาขา × สัปดาห์', c:(m.missing_weeks?'#f59e0b':'#10b981') },
  ].map(k => `<div class="card p-4" style="border-top:3px solid ${k.c}"><p class="text-[11px] uppercase tracking-wide" style="color:var(--text-muted)">${k.l}</p><p class="text-2xl font-bold" style="color:${textInk(k.c)}">${k.v}</p><p class="text-[11px]" style="color:var(--text-muted)">${esc(k.s)}</p></div>`).join('');

  // The gaps ARE the finding: a ratio computed over half the weeks is not a
  // small ratio, it is an unknown one.
  const gaps = (d.rows || []).filter(r => r.amount == null);
  document.getElementById('ipGaps').innerHTML = gaps.length
    ? `<div class="card p-3 text-[11px]" style="background:#fffbeb;border:1px solid #f59e0b;color:#92400e">
        <b>ยังไม่ได้นำเข้ายอดซื้อ ${gaps.length} ช่อง</b> ${uiQ('ip:gaps', 'สัปดาห์ที่ยังไม่นำเข้า', '<p>สัปดาห์เหล่านี้มียอดขายแล้วแต่ไม่มียอดซื้อ จึงไม่ถูกนำมาคิด % (ไม่ได้นับเป็น 0)</p>')} · ล่าสุด
        <ul style="margin:4px 0 0;padding-left:20px;list-style:disc">${gaps.slice(0, 6).map(g => `<li>${esc(g.branch)} สัปดาห์ ${esc(fmtDay(g.week_start))}</li>`).join('')}${gaps.length > 6 ? `<li>และอีก ${gaps.length - 6} ช่อง</li>` : ''}</ul>
       </div>` : '';

  // ── per-branch summary ──
  const brs = d.branches || [];
  const bOut = _miOutlierSet(brs.map(b => b.pct));
  document.getElementById('ipBranchBody').innerHTML = brs.map((b, i) => {
    const odd = bOut.has(i);
    return `<tr class="border-b${odd ? ' mi-outlier' : ''}" style="border-color:var(--border)">
      <td class="py-1.5 pr-2 font-semibold" data-sort="${esc(b.branch)}">${esc(b.branch)}${odd ? ' <span class="mi-flag bad" title="% ต่อยอดขายหลุดกรอบเทียบสาขาอื่น">⚠ ผิดปกติ</span>' : ''}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">฿${fmtN(b.amount)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">฿${fmtN(b.revenue)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-bold" style="color:${textInk(pctCol(b.pct))}">${b.pct == null ? '—' : b.pct + '%'}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${b.per_cover == null ? '—' : '฿' + fmtN(b.per_cover)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums" style="color:var(--text-muted)">${fmtN(b.weeks)}</td>
      <td class="py-1.5 text-right tabular-nums" style="color:${b.weeks_missing ? 'var(--amber-ink)' : 'var(--text-muted)'}">${fmtN(b.weeks_missing)}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="7" class="py-4 text-center" style="color:var(--text-muted)">ยังไม่มีข้อมูล — ดาวน์โหลดแม่แบบ แล้วนำเข้ายอดซื้อ</td></tr>';

  // ── week × branch, with the amount editable in place ──
  document.getElementById('ipWeekBody').innerHTML = (d.rows || []).map(r => `
    <tr class="border-b" style="border-color:var(--border)">
      <td class="py-1.5 pr-2 tabular-nums">${esc(fmtDay(r.week_start))}</td>
      <td class="py-1.5 pr-2 font-medium" data-sort="${esc(r.branch)}">${esc(r.branch)}</td>
      <td class="py-1.5 pr-2 text-right" data-sort="${r.amount == null ? '' : r.amount}">
        <input type="number" step="0.01" min="0" value="${r.amount == null ? '' : r.amount}"
               placeholder="ยังไม่นำเข้า"
               onkeydown="if(event.key==='Enter')ipSaveOne(this)" onblur="ipSaveOne(this)"
               data-ipbranch="${jsAttr(r.branch)}" data-ipweek="${jsAttr(r.week_start)}"
               data-ipwas="${r.amount == null ? '' : r.amount}"
               style="width:110px;text-align:right;border:1px solid var(--border);border-radius:var(--r-sm);padding:3px 6px;font-size:11.5px;background:var(--bg-input);color:var(--text-primary)"></td>
      <td class="py-1.5 pr-2 text-right tabular-nums" style="color:var(--text-muted)">฿${fmtN(r.revenue)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:${textInk(pctCol(r.pct))}">${r.pct == null ? '—' : r.pct + '%'}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${r.per_cover == null ? '—' : '฿' + fmtN(r.per_cover)}</td>
      <td class="py-1.5" style="color:var(--text-muted)">${r.source === 'excel' ? 'Excel' : r.source === 'manual' ? 'กรอกเอง' : '—'}</td>
    </tr>`).join('') || '<tr><td colspan="7" class="py-4 text-center" style="color:var(--text-muted)">ไม่มีสัปดาห์ในช่วงที่เลือก</td></tr>';

  ['ipBranchTbl', 'ipWeekTbl'].forEach(makeSortable);
  _ipRenderCharts(d);
}

function _ipRenderCharts(d) {
  const weeks = (d.meta || {}).weeks || [];
  const kill = () => {
    if (_ipChartPct) { _ipChartPct.destroy(); _ipChartPct = null; }
    if (_ipChartAmt) { _ipChartAmt.destroy(); _ipChartAmt = null; }
    if (_ipChartBranch) { _ipChartBranch.destroy(); _ipChartBranch = null; }
  };
  const heat = document.getElementById('ipHeat');
  if (!weeks.length) { kill(); if (heat) heat.innerHTML = ''; return; }
  const rows = d.rows || [];
  const PCT_COL = v => v == null ? '#94a3b8' : v >= 40 ? '#dc2626' : v >= 33 ? '#f59e0b' : '#059669';
  const PCT_BG  = v => v == null ? 'var(--bg-subtle)' : v >= 40 ? '#fee2e2' : v >= 33 ? '#fef3c7' : '#dcfce7';
  const wkLbl = w => { const [y, m, dd] = String(w).split('-').map(Number); return `${dd}/${m}`; };
  const imported = rows.filter(r => r.amount != null);
  const empty = !imported.length;

  // ── A: which branch buys heavy — one bar per branch, whole period ──
  const brs = (d.branches || []).filter(b => b.pct != null).sort((x, y) => y.pct - x.pct);
  const wrap = document.getElementById('ipChartBranchWrap');
  if (wrap) wrap.style.height = Math.max(200, 24 * brs.length + 70) + 'px';
  const chainPct = (d.totals || {}).pct;
  if (_ipChartBranch) { _ipChartBranch.destroy(); _ipChartBranch = null; }
  _ipChartBranch = mkChart('ipChartBranch', {
    type: 'bar',
    data: { labels: brs.map(b => b.branch), datasets: [{
      label: '% ยอดซื้อต่อยอดขาย', data: brs.map(b => b.pct), backgroundColor: brs.map(b => PCT_COL(b.pct)),
      maxBarThickness: 18,
      datalabels: { anchor: 'end', align: 'end', color: '#334155', font: { weight: '700', size: 11 }, formatter: v => v == null ? '' : v.toFixed(1) + '%' } }] },
    options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      scales: { x: { title: { display: true, text: '% ของยอดขาย' }, grace: '14%', beginAtZero: true } },
      plugins: { legend: { display: false }, glance: { depth: false },
        tooltip: { callbacks: { label: c => { const b = brs[c.dataIndex];
          return `${c.parsed.x}% · ซื้อ ฿${fmt(b.amount)} / ขาย ฿${fmt(b.revenue)} · ${fmtN(b.weeks)} สัปดาห์${b.weeks_missing ? ` (ขาดไฟล์ ${fmtN(b.weeks_missing)})` : ''}`; } } } } },
    plugins: [{ id: 'ipChainLine', afterDatasetsDraw(ch) {   // dashed line = the chain's own %
      if (chainPct == null) return;
      const xs = ch.scales.x, a = ch.chartArea, x = xs.getPixelForValue(chainPct);
      if (!(x >= a.left && x <= a.right)) return;
      const ctx = ch.ctx; ctx.save(); ctx.setLineDash([4, 3]); ctx.strokeStyle = '#1f3a6b'; ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.moveTo(x, a.top); ctx.lineTo(x, a.bottom); ctx.stroke();
      ctx.setLineDash([]); ctx.fillStyle = '#1f3a6b'; ctx.font = '700 10px ' + Chart.defaults.font.family; ctx.textAlign = 'left';
      ctx.fillText(`เฉลี่ย ${chainPct}%`, x + 4, a.top + 10); ctx.restore(); } }] });

  // ── B: the chain week by week — ONE series, coloured by the same bands ──
  const wkAmt = weeks.map(w => imported.filter(r => r.week_start === w).reduce((s2, r) => s2 + r.amount, 0));
  const wkRevImp = weeks.map(w => imported.filter(r => r.week_start === w).reduce((s2, r) => s2 + (r.revenue || 0), 0));
  const wkRevAll = weeks.map(w => rows.filter(r => r.week_start === w).reduce((s2, r) => s2 + (r.revenue || 0), 0));
  const wkN = weeks.map(w => imported.filter(r => r.week_start === w).length);
  const wkAll = weeks.map(w => rows.filter(r => r.week_start === w).length);
  const wkPct = weeks.map((w, i) => wkRevImp[i] ? Math.round(wkAmt[i] / wkRevImp[i] * 1000) / 10 : null);
  if (_ipChartPct) { _ipChartPct.destroy(); _ipChartPct = null; }
  _ipChartPct = mkChart('ipChartPct', {
    type: 'bar',
    data: { labels: weeks.map(wkLbl), datasets: [{ label: '% ยอดซื้อต่อยอดขาย', data: wkPct,
      backgroundColor: wkPct.map(PCT_COL), maxBarThickness: 40,
      datalabels: { anchor: 'end', align: 'end', color: '#334155', font: { weight: '700', size: 10.5 }, formatter: v => v == null ? '' : v.toFixed(0) + '%' } }] },
    options: { responsive: true, maintainAspectRatio: false,
      scales: { y: { title: { display: true, text: '% ของยอดขาย' }, beginAtZero: true, grace: '18%' } },
      plugins: { legend: { display: false }, glance: { depth: false },
        tooltip: { callbacks: { label: c => { const i = c.dataIndex;
          return `${c.parsed.y}% · ซื้อ ฿${fmt(wkAmt[i])} / ขาย ฿${fmt(wkRevImp[i])} · นำเข้าแล้ว ${wkN[i]}/${wkAll[i]} สาขา`; } } } } } });

  // ── C: baht — sales beside purchases, same scale, a missing bar is a missing file ──
  if (_ipChartAmt) { _ipChartAmt.destroy(); _ipChartAmt = null; }
  _ipChartAmt = mkChart('ipChartAmt', {
    type: 'bar',
    data: { labels: weeks.map(wkLbl), datasets: [
      { label: 'ยอดขาย (ไม่รวม VAT)', data: wkRevAll, backgroundColor: '#0891b2', maxBarThickness: 26 },
      { label: 'ยอดซื้อวัตถุดิบ (นำเข้า)', data: weeks.map((w, i) => wkN[i] ? wkAmt[i] : null), backgroundColor: '#7a66b8', maxBarThickness: 26 }] },
    options: { responsive: true, maintainAspectRatio: false,
      scales: { y: { title: { display: true, text: '฿' }, beginAtZero: true, ticks: { callback: v => fmt(v) } } },
      plugins: { datalabels: { display: false }, legend: { labels: { boxWidth: 10 } },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: ฿${fmt(c.parsed.y)}` } } } } });

  // ── D: heatmap — the only honest way to show 28 branches × N weeks at once ──
  if (heat) {
    if (empty) {
      heat.innerHTML = `<p class="text-sm py-6 text-center" style="color:var(--text-muted)">ยังไม่มียอดซื้อที่นำเข้าในช่วงนี้ — กด <b>ดาวน์โหลดแม่แบบ</b> กรอกยอดซื้อรายสัปดาห์ แล้ว <b>นำเข้าไฟล์</b> กราฟและแผนที่ความร้อนจะขึ้นทันที</p>`;
    } else {
      const at = {}; rows.forEach(r => { at[r.branch + '|' + r.week_start] = r; });
      const order = (d.branches || []).slice().sort((x, y) => (y.pct ?? -1) - (x.pct ?? -1));
      const cell = r => (!r || r.amount == null)
        ? `<td class="text-center" style="background:var(--bg-subtle);color:var(--text-muted);font-size:10px">—</td>`
        : `<td class="text-center tabular-nums" style="background:${PCT_BG(r.pct)};color:#0f172a;font-weight:${r.pct >= 40 ? 700 : 500}" title="${esc(r.branch)} ${esc(fmtDay(r.week_start))} · ซื้อ ฿${fmtN(r.amount)} / ขาย ฿${fmtN(r.revenue)}">${r.pct == null ? '—' : r.pct.toFixed(0)}</td>`;
      heat.innerHTML = `<table class="text-[11px]" style="border-collapse:separate;border-spacing:2px;min-width:100%"><thead><tr style="color:var(--text-muted)">
          <th class="text-left pr-2" style="position:sticky;left:0;background:var(--bg-card)">สาขา</th>${weeks.map(w => `<th class="text-center px-1 font-normal">${wkLbl(w)}</th>`).join('')}<th class="text-center pl-2">ทั้งช่วง</th></tr></thead><tbody>${
        order.map(b => `<tr><td class="text-left pr-2 font-medium whitespace-nowrap" style="position:sticky;left:0;background:var(--bg-card)">${esc(b.branch)}</td>${
          weeks.map(w => cell(at[b.branch + '|' + w])).join('')}<td class="text-center tabular-nums font-bold" style="background:${PCT_BG(b.pct)};color:${textInk(PCT_COL(b.pct))}">${b.pct == null ? '—' : b.pct.toFixed(1) + '%'}</td></tr>`).join('')}
        </tbody></table>`;
    }
  }
}

function ipTemplate() {
  const p = new URLSearchParams();
  const br = document.getElementById('ipBranch')?.value || '';
  if (br) p.set('branch', br);
  p.set('weeks', document.getElementById('ipWeeks')?.value || '8');
  const t = document.getElementById('ipTo')?.value || '';
  if (t) p.set('date_to', t);
  window.open(`${API}/api/ingredients/purchases.template.xlsx?${p}`, '_blank');
}

async function ipImport(input) {
  await _opImportFile(input, `${API}/api/ingredients/purchases/import`,
                      'ipStatus', 'ipImportReport', () => loadIngPurchase(true));
}

// Single-cell edit. Only writes when the value actually changed, so tabbing
// through the table doesn't rewrite every row (and stamp them all 'manual').
async function ipSaveOne(el) {
  const was = el.dataset.ipwas || '';
  const now = (el.value || '').trim();
  if (now === was) return;
  const st = document.getElementById('ipStatus');
  if (now === '') { el.value = was; return; }   // clearing is a delete — not from here
  try {
    const r = await fetch(`${API}/api/ingredients/purchases?source=manual`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify([{ branch: el.dataset.ipbranch, week_start: el.dataset.ipweek,
                              amount: parseFloat(now) }]) });
    if (!r.ok) throw new Error(await r.text());
    const j = await r.json();
    if (j.errors && j.errors.length) throw new Error(j.errors[0].why);
    el.dataset.ipwas = now;
    await loadIngPurchase(true);
    if (st) st.textContent = `✓ บันทึก ${el.dataset.ipbranch} สัปดาห์ ${el.dataset.ipweek} แล้ว`;
  } catch (e) {
    el.value = was;
    if (st) { st.textContent = 'บันทึกไม่สำเร็จ — ' + String(e.message || e).slice(0, 140); st.style.color = 'var(--bad-ink)'; }
  }
}
