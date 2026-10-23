// ─── 09-analysis-tabs.js — Menu engineering · staff scorecard · discounts & leakage (bills drill, coupon series, reasons) · time & flow ───
// Slice 1/5 of the former 09-deep-tools.js (split 2026-09-05 by contiguous range, so every
// statement keeps its load order). Shared global scope: index.html loads js/*.js in name order.

// ════════════════════════════════════════════════════════════════
// MENU ENGINEERING
// ════════════════════════════════════════════════════════════════
let _engData = [];
let _engFilter = 'all';

async function loadEngineering() {
  const _fk = _filterKey(); if (_tabLastKey.engineering === _fk) return; _tabLastKey.engineering = _fk;
  const params = new URLSearchParams();
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to', getMonthTo());
  const cat = document.getElementById('engCategory')?.value;
  if (cat) params.set('category', cat);

  const d = await api(`${API}/api/menu/engineering?${params}`);
  _engData = d.items || [];

  // Populate category filter (first load only)
  const catSel = document.getElementById('engCategory');
  if (catSel && catSel.options.length <= 1) {
    const cats = [...new Set(_engData.map(i => i.category).filter(Boolean))].sort();
    cats.forEach(c => {
      const o = document.createElement('option');
      o.value = c; o.textContent = c;
      catSel.appendChild(o);
    });
  }

  // Quadrant summary cards
  const QUADS = [
    { key: 'star',      label: 'ดาวเด่น',      color: '#10b981', bg: '#f0fdf4', advice: 'Maintain & promote' },
    { key: 'plowhorse', label: 'ม้างาน', color: '#f59e0b', bg: '#fffbeb', advice: 'Reprice or reduce cost' },
    { key: 'puzzle',    label: 'เพชรในตม',    color: '#4a70b0', bg: '#e8eef7', advice: 'Reposition or bundle' },
    { key: 'dog',       label: 'ตัวถ่วง',       color: '#ef4444', bg: '#fef2f2', advice: 'Review or discontinue' },
  ];
  const costedItems = _engData.filter(i => i.has_cost);
  document.getElementById('engSummaryCards').innerHTML = QUADS.map(q => {
    const items = costedItems.filter(i => i.quadrant === q.key);
    const rev = items.reduce((s, i) => s + (i.revenue || 0), 0);
    return `<div class="on-light" style="background:${q.bg};border-radius:var(--r-md);padding:10px 12px">
      <div class="flex justify-between items-center">
        <span style="font-weight:600;color:${textInk(q.color)}">${q.label}</span>
        <span style="font-size:18px;font-weight:700;color:${textInk(q.color)}">${items.length}</span>
      </div>
      <div class="text-xs mt-0.5" style="color:var(--text-secondary)">${fmt(rev)} revenue</div>
      <div class="text-xs mt-0.5" style="color:var(--text-muted);font-style:italic">${q.advice}</div>
    </div>`;
  }).join('');

  document.getElementById('engCoverageNote').textContent =
    `${d.costed_skus || 0} จาก ${d.total_skus || 0} เมนูมีข้อมูลต้นทุน · เมนูที่ไม่มีต้นทุนแสดงเป็นสีเทา`;

  // ── Action board (the easy-to-read primary view) ─────────────────────
  renderMenuEngActionBoard(_engData, 'engActionBoard', 'engBoardNote');

  // Scatter chart
  const QUADS_MAP = [
    { key: 'star',      label: 'ดาวเด่น',      color: '#10b981' },
    { key: 'plowhorse', label: 'ม้างาน', color: '#f59e0b' },
    { key: 'puzzle',    label: 'เพชรในตม',    color: '#4a70b0' },
    { key: 'dog',       label: 'ตัวถ่วง',       color: '#ef4444' },
  ];
  const datasets = [
    ...QUADS_MAP.map(q => ({
      label: q.label,
      data: costedItems.filter(i => i.quadrant === q.key).map(i => ({
        x: i.popularity_pct, y: i.margin_per_unit,
        description: i.description, sku: i.sku_code,
      })),
      backgroundColor: q.color + 'cc',
      pointRadius: 6, pointHoverRadius: 9,
    })),
    {
      label: 'ไม่มีข้อมูลต้นทุน',
      data: _engData.filter(i => !i.has_cost).map(i => ({
        x: i.popularity_pct, y: 0,
        description: i.description, sku: i.sku_code,
      })),
      backgroundColor: '#94a3b888',
      pointRadius: 4,
    }
  ];

  const medPop = d.median_popularity_pct || 50;
  const medMarg = d.median_margin || 0;

  mkChart('chartEngMatrix', {
    type: 'scatter',
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { font: { size: 10 } } },
        datalabels: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => {
              const p = ctx.raw;
              return `${p.description || p.sku} (pop: ${(p.x||0).toFixed(1)}%, margin: ${(p.y||0).toFixed(0)})`;
            }
          }
        },
      },
      scales: {
        x: { title: { display: true, text: 'Popularity (% of total qty)' }, min: 0 },
        y: { title: { display: true, text: 'Margin per Unit ' } }
      }
    }
  });

  renderEngTable();
}

function filterEng(q) {
  _engFilter = q;
  ['all','star','plowhorse','puzzle','dog'].forEach(k => {
    document.getElementById('engFilter-' + k)?.classList.toggle('active', k === q);
  });
  renderEngTable();
}

function renderEngTable() {
  const items = _engFilter === 'all' ? _engData
    : _engData.filter(i => i.quadrant === _engFilter);
  const QUAD_BADGE = {
    star: 'ดาวเด่น', plowhorse: 'ม้างาน', puzzle: 'เพชรในตม', dog: 'ตัวถ่วง', null: '—', undefined: '—'
  };
  const QUAD_COLORS = { star: 'var(--good-ink)', plowhorse: '#d97706', puzzle: '#4a70b0', dog: 'var(--bad-ink)' };
  document.getElementById('engTableBody').innerHTML = [...items]
    .sort((a, b) => (b.revenue || 0) - (a.revenue || 0))
    .map(i => `
      <tr class="border-t border-slate-100 hover:bg-slate-50">
        <td class="py-2 pr-3 font-mono text-indigo-600 text-xs">${i.sku_code}</td>
        <td class="py-2 pr-3 max-w-xs truncate" title="${i.description}">${i.description || '—'}</td>
        <td class="py-2 pr-3 text-slate-500">${i.category || '—'}</td>
        <td class="py-2 pr-3 text-right">${fmtN(i.qty)}</td>
        <td class="py-2 pr-3 text-right">${fmt(i.revenue)}</td>
        <td class="py-2 pr-3 text-right">${i.margin_per_unit != null ? '' + i.margin_per_unit.toFixed(1) : '—'}</td>
        <td class="py-2 pr-3 text-right">${i.margin_pct != null ? i.margin_pct.toFixed(1) + '%' : '—'}</td>
        <td class="py-2 text-right">
          <span style="font-weight:600;color:${textInk(QUAD_COLORS[i.quadrant]||'var(--text-muted)')}">${QUAD_BADGE[i.quadrant] || (i.has_cost ? '—' : 'ไม่มีต้นทุน')}</span>
        </td>
      </tr>`).join('');
}

// ════════════════════════════════════════════════════════════════
// STAFF SCORECARD
// ════════════════════════════════════════════════════════════════

// ── Discount-bills drill-down (shared by Staff / Discounts / Report §9) ──
function closeDiscountBills() {
  const m = document.getElementById('discBillsModal');
  if (m) m.style.display = 'none';
}
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  // If the per-bill P&L modal is stacked on top, let it close first (one Esc each).
  const bi = document.getElementById('billItemsModal');
  if (bi && bi.style.display !== 'none') return;
  closeDiscountBills();
});

// ── Per-bill menu drill-down (loss bills): what the customer actually ordered ──
function closeBillItems() {
  const m = document.getElementById('billItemsModal');
  if (m) m.style.display = 'none';
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeBillItems(); });

async function openBillItems(row = {}) {
  const modal = document.getElementById('billItemsModal');
  const title = document.getElementById('billItemsTitle');
  const sumEl = document.getElementById('billItemsSummary');
  const body  = document.getElementById('billItemsBody');
  title.textContent = 'เมนูที่สั่ง · ' + (row.receipt || '');
  const renderSummary = (rw) => {
    // rw.rev = total_inc_vat / 1.07. Sale.Total is POST-discount, so this is ALREADY the
    // NET ex-VAT revenue actually received — not the pre-discount value. Pre-discount =
    // net + discount. Gross profit = net − COGS (the SAME definition as บิลขาดทุน / buffet
    // economics). Do NOT subtract the discount again — that double-counted the markdown
    // and overstated every bill's loss by exactly the discount amount.
    const net = rw.rev || 0, disc = rw.disc || 0, cogs = rw.cogs || 0;
    const gross = net + disc;                          // reconstructed pre-discount ex-VAT
    const gp    = Math.round(net - cogs);              // gross profit; negative = loss
    sumEl.innerHTML = `<div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3 mb-1">
    ${[
      ['สาขา', esc(rw.branch || '—')],
      ['ลูกค้า', fmtN(rw.cust || 0)],
      ['รายได้ก่อนลด', fmt(gross)],
      ['ส่วนลดบิล', disc ? `<span style="color:var(--amber-ink)">−${fmt(disc)}</span>` : '—'],
      ['รายได้สุทธิ', `<span style="color:var(--accent)">${fmt(net)}</span>`],
      ['COGS (BoM)', fmt(cogs)],
      [gp >= 0 ? 'กำไรขั้นต้น' : 'ขาดทุนสุทธิ',
       `<span style="color:${gp >= 0 ? 'var(--good-ink)' : 'var(--bad-ink)'}">${fmt(Math.abs(gp))}</span>`],
    ].map(([k, v]) => `<div class="p-3 rounded-lg bg-slate-50 text-center"><p class="text-xs text-slate-500">${k}</p><p class="text-base font-bold text-slate-800">${v}</p></div>`).join('')}
  </div>`;
  };
  renderSummary(row);
  body.innerHTML = '<p class="text-sm text-slate-400 py-8 text-center">กำลังโหลด…</p>';
  modal.style.display = 'flex';
  let d;
  try {
    const r = await fetch(`${API}/api/reports/pnl-bill-items?receipt=${encodeURIComponent(row.receipt || '')}`);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    d = await r.json();
  } catch (e) { body.innerHTML = `<p class="text-sm text-red-500 py-6 text-center">โหลดไม่ได้: ${esc(String(e.message || e))}</p>`; return; }
  // server header carries live-recomputed COGS (cost-management table) + discount incl. sp_discount
  if (d.header) renderSummary({ ...row, ...d.header });
  if (!d.synced) {
    body.innerHTML = '<p class="text-sm text-amber-600 py-8 text-center">ยังไม่มีรายละเอียดเมนูต่อบิล — ตาราง <code>sale_bill_item</code> จะถูกสร้างหลังการ sync ข้อมูลครั้งถัดไป</p>';
    return;
  }
  const items = d.items || [];
  if (!items.length) { body.innerHTML = '<p class="text-sm text-slate-400 py-8 text-center">ไม่พบรายการเมนูสำหรับบิลนี้</p>'; return; }
  const rows = items.map(it => {
    const mCol = it.margin < 0 ? '#ef4444' : it.margin === 0 ? '#94a3b8' : '#10b981';
    const uc = it.unit_cost != null ? it.unit_cost : (it.qty ? it.cogs / it.qty : 0);
    return `<tr class="border-b border-slate-100 ${it.margin < 0 ? 'bg-rose-50/40' : ''}">
      <td class="py-1.5 pr-3">${esc(it.menu || '—')}</td>
      <td class="py-1.5 pr-3 text-slate-400 font-mono text-[10.5px]">${esc(it.sku_code || '')}</td>
      <td class="py-1.5 pr-3 text-right tabular-nums">${fmtN(it.qty)}</td>
      <td class="py-1.5 pr-3 text-right tabular-nums text-slate-500">${uc ? uc.toFixed(2) : '—'}</td>
      <td class="py-1.5 pr-3 text-right tabular-nums">${fmt(it.rev)}</td>
      <td class="py-1.5 pr-3 text-right tabular-nums text-slate-500">${fmt(it.cogs)}</td>
      <td class="py-1.5 text-right tabular-nums font-semibold" style="color:${textInk(mCol)}">${fmt(it.margin)}</td></tr>`;
  }).join('');
  const tot = items.reduce((a, it) => ({ rev: a.rev + it.rev, cogs: a.cogs + it.cogs, margin: a.margin + it.margin }), { rev: 0, cogs: 0, margin: 0 });
  body.innerHTML = `<table class="w-full text-xs"><thead><tr class="text-left text-slate-500 border-b border-slate-200">
      <th class="py-2 pr-3">เมนู</th><th class="py-2 pr-3">SKU</th>
      <th class="py-2 pr-3 text-right">จำนวน</th><th class="py-2 pr-3 text-right">@BoM (ต้นทุน/หน่วย)</th>
      <th class="py-2 pr-3 text-right">รายได้</th>
      <th class="py-2 pr-3 text-right">ต้นทุนขาย (BoM)</th><th class="py-2 text-right">กำไรขั้นต้น</th></tr></thead>
    <tbody>${rows}</tbody>
    <tfoot><tr class="border-t-2 border-slate-200 font-bold">
      <td class="py-2 pr-3" colspan="4">รวม ${fmtN(items.length)} เมนู</td>
      <td class="py-2 pr-3 text-right tabular-nums">${fmt(tot.rev)}</td>
      <td class="py-2 pr-3 text-right tabular-nums">${fmt(tot.cogs)}</td>
      <td class="py-2 text-right tabular-nums" style="color:${tot.margin < 0 ? 'var(--bad-ink)' : 'var(--good-ink)'}">${fmt(tot.margin)}</td></tr></tfoot></table>
    <p class="text-[11px] text-slate-400 mt-3">รายได้ต่อเมนู = ยอด ex-VAT ก่อนหักส่วนลดระดับบิล — ผลรวมจึงอาจสูงกว่า "รายได้สุทธิ" ของบิลด้านบนตามส่วนลด${row.disc ? ` (ส่วนลดบิลนี้ ${fmt(row.disc)})` : ''}. ทุน/หน่วย = ตาราง Cost Management ก่อน แล้วจึง BoM (buffet tier = 0).</p>`;
}

let _discBillsCtx = {};   // remembered drill context so the วิธีจ่าย dropdown can re-query
function discBillsSetPayment(v) { openDiscountBills({ ..._discBillsCtx, payment: v || undefined }); }
async function openDiscountBills(opts = {}) {
  const modal = document.getElementById('discBillsModal');
  const title = document.getElementById('discBillsTitle');
  const sumEl = document.getElementById('discBillsSummary');
  const body  = document.getElementById('discBillsBody');
  const branch     = opts.branch     !== undefined ? opts.branch     : getBranch();
  const cashier    = opts.cashier    || '';
  // A day/range (date_from/date_to) takes precedence over the global month — mirrors
  // the backend's _apply_period_filter, so a drill from a day/range KPI shows that scope.
  const date_from = opts.date_from || '';
  const date_to   = opts.date_to   || '';
  const useDate    = !!(date_from || date_to);
  const month_from = useDate ? '' : (opts.month_from !== undefined ? opts.month_from : getMonthFrom());
  const month_to   = useDate ? '' : (opts.month_to   !== undefined ? opts.month_to   : getMonthTo());
  const p = new URLSearchParams();
  if (branch)     p.set('branch', branch);
  if (cashier)    p.set('cashier', cashier);
  if (date_from)  p.set('date_from', date_from);
  if (date_to)    p.set('date_to', date_to);
  if (month_from) p.set('month_from', month_from);
  if (month_to)   p.set('month_to', month_to);
  p.set('sort', opts.sort || 'discount');
  p.set('limit', opts.limit || 200);
  if (opts.min_discount)   p.set('min_discount', opts.min_discount);
  if (opts.amount != null) p.set('amount', opts.amount);
  if (opts.min_pct != null) p.set('min_pct', opts.min_pct);
  if (opts.max_pct != null) p.set('max_pct', opts.max_pct);
  if (opts.gross_zero)     p.set('gross_zero', 'true');
  if (opts.payment)           p.set('payment', opts.payment);
  if (opts.reprinted_only)    p.set('reprinted_only', 'true');
  // remember context so the payment dropdown and reprint toggle can re-query the same drill
  _discBillsCtx = { branch, cashier, date_from, date_to, month_from, month_to, sort: opts.sort,
                    amount: opts.amount, min_pct: opts.min_pct, max_pct: opts.max_pct,
                    gross_zero: opts.gross_zero, min_discount: opts.min_discount, title: opts.title,
                    payment: opts.payment, reprinted_only: opts.reprinted_only };
  title.textContent = '' + (opts.title || (cashier ? `บิลส่วนลด · ${cashier}` : 'บิลส่วนลด'));
  sumEl.innerHTML = '';
  body.innerHTML = '<p class="text-sm text-slate-400 py-8 text-center">กำลังโหลด…</p>';
  modal.style.display = 'flex';
  let d;
  try {
    const r = await fetch(`${API}/api/analysis/discount_bills?${p}`);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    d = await r.json();
  } catch(e) { body.innerHTML = `<p class="text-sm text-red-500 py-6 text-center">โหลดไม่ได้: ${esc(String(e.message||e))}</p>`; return; }
  const s = d.summary || {};
  const codes = d.payment_codes || [];
  const reprintBadge = (s.reprinted_bills||0) > 0
    ? `<span class="ml-2 text-xs font-bold text-orange-600 bg-orange-50 border border-orange-200 rounded px-2 py-0.5">Reprint ${fmtN(s.reprinted_bills)} บิล</span>` : '';
  const payFilter = codes.length ? `<div class="flex items-center gap-2 mb-2 flex-wrap">
      <span class="text-xs font-medium text-slate-500">วิธีจ่าย:</span>
      <select onchange="discBillsSetPayment(this.value)" class="text-xs border border-slate-300 rounded-lg px-2 py-1" style="max-width:220px">
        <option value="">ทั้งหมด (${fmtN(codes.length)} วิธี)</option>
        ${codes.map(c=>`<option value="${esc(c)}" ${opts.payment===c?'selected':''}>${esc(c)}</option>`).join('')}
      </select>
      ${opts.payment?`<button onclick="discBillsSetPayment('')" class="text-xs text-red-600 underline">ล้างตัวกรอง</button>`:''}
      <button onclick="openDiscountBills({..._discBillsCtx, reprinted_only:${!opts.reprinted_only}, sort:'reprints', title:'บิลที่พิมพ์ซ้ำ + มีส่วนลด'})"
        class="text-xs px-2 py-1 rounded-lg border ${opts.reprinted_only?'bg-orange-100 border-orange-400 text-orange-700 font-bold':'border-slate-300 text-slate-500 hover:bg-slate-50'}">
        ${opts.reprinted_only?'ดูทั้งหมด':'เฉพาะ Reprint'}
      </button>
    </div>` : '';
  const curSort = opts.sort || 'discount';
  const sortBar = `<div class="flex items-center gap-1 mb-2 flex-wrap">
    <span class="text-xs text-slate-400 mr-1">เรียงโดย:</span>
    ${[['date','วันที่'],['discount','฿ ส่วนลด'],['pct','% ของบิล'],['total','ยอดบิล']].map(([k,lbl])=>
      `<button onclick="openDiscountBills({..._discBillsCtx, sort:'${k}'})"
        class="text-xs px-2.5 py-1 rounded-lg border ${curSort===k?'font-bold text-white':'text-slate-600 hover:bg-slate-50'}"
        style="${curSort===k?'background:var(--color-primary);border-color:var(--color-primary)':'border-color:#e2e8f0'}">${lbl}</button>`
    ).join('')}
  </div>`;
  sumEl.innerHTML = payFilter + sortBar + `<div class="grid grid-cols-2 md:grid-cols-5 gap-3 mb-1">
    ${[['บิลมีส่วนลด', fmtN(s.bills||0)],['ส่วนลดรวม', fmt(s.total_discount||0)],['เฉลี่ย/บิล', fmt(s.avg_discount||0)],['สูงสุด', fmt(s.max_discount||0)],['พิมพ์ซ้ำ (>1x)', `<span class="${(s.reprinted_bills||0)>0?'text-orange-600 font-bold':''}">${fmtN(s.reprinted_bills||0)}</span>`]]
      .map(([k,v])=>`<div class="p-3 rounded-lg bg-slate-50 text-center"><p class="text-xs text-slate-500">${k}</p><p class="text-lg font-bold text-slate-800">${v}</p></div>`).join('')}
  </div>
  <p class="text-xs text-slate-400 mb-2">ไฮไลต์แดง = ส่วนลดเกิน p95 (${fmt(s.p95_discount||0)}) · ไฮไลต์ส้ม = Reprint · แสดง ${fmtN(s.returned||0)} บิลแรก${opts.payment?` · กรองเฉพาะ <b style="color:var(--bad-ink)">${esc(opts.payment)}</b>`:''}${opts.reprinted_only?' · <b style="color:var(--orange-ink)">เฉพาะ Reprint</b>':''}</p>`;
  if (!d.bills.length) { body.innerHTML = '<p class="text-sm text-slate-400 py-6 text-center">ไม่มีบิลส่วนลดในขอบเขตนี้</p>'; return; }
  body.innerHTML = renderDiscBillsTable(d.bills, s.p95_discount||0, !cashier, !branch);
}

function renderDiscBillsTable(bills, p95, showCashier=true, showBranch=true) {
  const head = `<table class="w-full text-xs"><thead><tr class="text-left text-slate-500 border-b border-slate-200">
    <th class="py-2 pr-3">ใบเสร็จ</th><th class="py-2 pr-3">วันเวลา</th>
    ${showBranch?'<th class="py-2 pr-3">สาขา</th>':''}${showCashier?'<th class="py-2 pr-3">แคชเชียร์</th>':''}
    <th class="py-2 pr-3">โต๊ะ</th>
    <th class="py-2 pr-3">ประเภทส่วนลด/คูปอง</th>
    <th class="py-2 pr-3 text-right">ยอดก่อนลด</th>
    <th class="py-2 pr-3 text-right">ส่วนลด</th>
    <th class="py-2 pr-3 text-right">จ่ายจริง</th>
    <th class="py-2 pr-3 text-right" title="กำไร/ขาดทุนขั้นต้นรายบิล = รายได้สุทธิ (จ่ายจริง ÷ 1.07) − ทุนอาหาร BoM · เขียว = กำไร · แดง = ขาดทุน · — = ยังไม่มีต้นทุนรายบิล (รอ sync)">กำไร/ขาดทุน</th>
    <th class="py-2 pr-3">วิธีจ่าย</th>
    <th class="py-2 pr-3 text-right">% ของบิล</th>
    <th class="py-2 text-right" title="จำนวนครั้งที่พิมพ์ใบเสร็จ (Easy POS PrintTimes)">พิมพ์</th>
    </tr></thead><tbody>`;
  const rows = bills.map(b => {
    const hot = (b.discount||0) >= p95 && p95 > 0;
    const base = b.bill_base != null ? b.bill_base : (b.total_inc_vat||0) + (b.discount||0);
    const dtype = b.coupon_name
      ? `<span class="text-indigo-600">${esc(b.coupon_name)}</span>`
      : (b.reason_desc ? esc(b.reason_desc)
        : '<span class="text-slate-400">ไม่ระบุประเภท</span>');
    const payCell = b.payment_code
      ? `${esc(b.payment_code)}${(b.payment_n||0) > 1 ? ` <span class="text-[9px] text-slate-400" title="จ่ายหลายแบบในบิลเดียว">+${b.payment_n-1}</span>` : ''}`
      : '<span class="text-slate-300">—</span>';
    const pt = b.print_times || 0;
    const printCell = pt > 1
      ? `<span class="font-bold text-red-600" title="พิมพ์ซ้ำ ${pt} ครั้ง — ตรวจสอบ">${pt}x ⚑</span>`
      : (pt === 1 ? `<span class="text-slate-400">1x</span>` : `<span class="text-slate-300">—</span>`);
    // Per-bill P&L cell. Net ex-VAT revenue = จ่ายจริง ÷ 1.07 (total_inc_vat is already
    // post-discount), so gross profit = net − BoM COGS — identical to the drill's tile and
    // to บิลขาดทุน GP. cogs_bom is NULL until the next sync stages this bill's lines → "—".
    const plCell = (() => {
      if (b.cogs_bom == null)
        return '<span class="text-slate-300" title="ยังไม่มีต้นทุนรายบิลของบิลนี้ — จะปรากฏหลัง sync รอบถัดไป">—</span>';
      const net = Math.round((b.total_inc_vat || 0) / 1.07);
      const pl  = net - Math.round(b.cogs_bom);
      const col = pl < 0 ? '#ef4444' : '#10b981';
      return `<span class="font-semibold" style="color:${textInk(col)}" title="รายได้สุทธิ ${fmtN(net)} − ทุน BoM ${fmtN(Math.round(b.cogs_bom))}">${pl < 0 ? '−' : '+'}${fmtN(Math.abs(pl))}</span>`;
    })();
    // Click a row → open the per-bill P&L (เมนูที่สั่ง + ทุน/กำไรรายบิล). Server re-derives
    // the header; these values just seed the summary before the fetch resolves.
    const pnlRow = JSON.stringify({ receipt: b.receipt_no, branch: b.branch,
      rev: Math.round((b.total_inc_vat || 0) / 1.07), disc: b.discount || 0,
      cogs: b.cogs_bom != null ? Math.round(b.cogs_bom) : 0,
      cust: b.customer_count || 0 }).replace(/'/g, '&#39;');
    return `<tr class="border-b border-slate-100 cursor-pointer hover:bg-indigo-50/50 ${pt>1?'bg-orange-50':hot?'bg-red-50':''}" title="คลิกดู P&amp;L รายบิล — เมนูที่สั่ง · ทุน · กำไรขั้นต้น" onclick='openBillItems(${pnlRow})'>
      <td class="py-1.5 pr-3 font-mono text-slate-600">${esc(b.receipt_no||'—')}</td>
      <td class="py-1.5 pr-3 text-slate-500">${esc((b.sold_at||'').replace('T',' ').slice(0,16))}</td>
      ${showBranch?`<td class="py-1.5 pr-3">${esc(b.branch||'—')}</td>`:''}
      ${showCashier?`<td class="py-1.5 pr-3">${esc(b.cashier||'—')}</td>`:''}
      <td class="py-1.5 pr-3 text-slate-500">${esc(b.table_no||'—')}</td>
      <td class="py-1.5 pr-3">${dtype}</td>
      <td class="py-1.5 pr-3 text-right text-slate-600">${fmtN(base)}</td>
      <td class="py-1.5 pr-3 text-right font-semibold ${hot?'text-red-600':'text-amber-600'}">${fmtN(b.discount||0)}</td>
      <td class="py-1.5 pr-3 text-right text-slate-500">${fmtN(b.total_inc_vat||0)}</td>
      <td class="py-1.5 pr-3 text-right">${plCell}</td>
      <td class="py-1.5 pr-3 text-slate-600">${payCell}</td>
      <td class="py-1.5 pr-3 text-right">${b.disc_pct!=null?b.disc_pct.toFixed(1)+'%':'—'}</td>
      <td class="py-1.5 text-right">${printCell}</td></tr>`;
  }).join('');
  return head + rows + '</tbody></table>';
}

// Map a discount pattern-group label → openDiscountBills() drill options
function _discPatternDrillAttr(grp) {
  let opts;
  if (grp.includes('ไม่มียอดบิล'))        opts = { gross_zero: true };
  else if (grp.includes('100%'))           opts = { min_pct: 100 };
  else if (grp.includes('50% ขึ้นไป'))     opts = { min_pct: 50, max_pct: 100 };
  else if (grp.includes('20–50') || grp.includes('20-50')) opts = { min_pct: 20, max_pct: 50 };
  else if (grp.includes('10–20') || grp.includes('10-20')) opts = { min_pct: 10, max_pct: 20 };
  else if (grp.includes('ต่ำกว่า 10'))     opts = { max_pct: 10 };
  else opts = {};
  opts.sort = 'pct';
  opts.title = 'บิลส่วนลดกลุ่ม · ' + grp;
  return `onclick='openDiscountBills(${JSON.stringify(opts).replace(/'/g, "&#39;")})'`;
}

let _staffByBranch = true;   // default: split each cashier per branch (no merging)
function setStaffByBranch(on) {
  _staffByBranch = on;
  document.getElementById('staffGrpSplit')?.classList.toggle('active', on);
  document.getElementById('staffGrpMerge')?.classList.toggle('active', !on);
  delete _tabLastKey.staff;
  loadStaff();
}

async function loadStaff() {
  const _fk = _filterKey() + '|bb=' + _staffByBranch;
  if (_tabLastKey.staff === _fk) return; _tabLastKey.staff = _fk;
  const params = new URLSearchParams();
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to', getMonthTo());
  params.set('limit', '50');
  const cashierParams = new URLSearchParams(params);
  if (_staffByBranch) cashierParams.set('by_branch', 'true');

  // Loading states
  document.getElementById('staffBody').innerHTML =
    '<tr><td colspan="10" class="py-6 text-center text-slate-400 text-xs">กำลังโหลด…</td></tr>';
  document.getElementById('staff-abnormal-bills').innerHTML =
    '<p class="text-sm text-slate-400 py-4 text-center">กำลังโหลด…</p>';

  // Abnormal-discount bills panel (top by discount, current filters)
  (async () => {
    const bp = new URLSearchParams(params); bp.set('limit', '8'); bp.set('sort', 'discount');
    const db = await api(`${API}/api/analysis/discount_bills?${bp}`).catch(() => null);
    const el = document.getElementById('staff-abnormal-bills');
    if (!db || !db.bills || !db.bills.length) {
      el.innerHTML = '<p class="text-sm text-slate-400 py-4 text-center">ไม่มีบิลส่วนลดในช่วงนี้</p>'; return;
    }
    el.innerHTML = renderDiscBillsTable(db.bills, db.summary?.p95_discount || 0, true, !getBranch());
  })();

  const d = await api(`${API}/api/analysis/cashiers?${cashierParams}`).catch(() => []);

  if (!Array.isArray(d) || !d.length) {
    document.getElementById('staff-kpi-row').innerHTML = '';
    document.getElementById('staffBody').innerHTML =
      '<tr><td colspan="10" class="py-8 text-center text-slate-400 text-sm">ไม่มีข้อมูลแคชเชียร์ในช่วงเวลา/สาขาที่เลือก — ลองขยายช่วงเวลา หรือเลือก "ทุกสาขา"</td></tr>';
    return;
  }

  // KPI row
  const top = d[0] || {};
  const totalRev = d.reduce((s, r) => s + (r.revenue || 0), 0);
  const avgDisc = d.length ? d.reduce((s, r) => s + (r.discount_rate || 0), 0) / d.length : 0;
  document.getElementById('staff-kpi-row').innerHTML = [
    { label: 'ยอดขายรวมของพนักงาน', value: `${fmt(totalRev)}`, sub: _staffByBranch ? `${d.length} บัญชี×สาขา` : `${d.length} บัญชีแคชเชียร์`, color: '#4a70b0' },
    { label: 'Top Cashier', value: top.cashier || '—', sub: `${fmt(top.revenue || 0)}`, color: 'var(--good-ink)' },
    { label: 'ยอดเฉลี่ยต่อบิลทั้งเครือ', value: `${fmt(d.length ? d.reduce((s,r)=>s+(r.avg_ticket||0),0)/d.length : 0)}`, sub: 'ต่อใบเสร็จ', color: '#0891b2' },
    { label: 'Network Discount Rate', value: `${avgDisc.toFixed(1)}%`, sub: 'of transactions with discount', color: avgDisc > 15 ? '#ef4444' : '#f59e0b' },
  ].map(k => `<div class="card p-4"><p class="kpi-label">${k.label}</p><p class="kpi-value" style="color:${textInk(k.color)}">${k.value}</p><p class="text-xs mt-1" style="color:var(--text-muted)">${k.sub}</p></div>`).join('');

  const top10 = d.slice(0, 10);

  mkChart('chartStaffRevenue', {
    type: 'bar',
    data: {
      labels: top10.map(r => r.cashier),
      datasets: [{ label: 'รายได้', data: top10.map(r => r.revenue), backgroundColor: PALETTE_6, borderRadius: 4 }]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, datalabels: { display: false } },
      scales: { x: { ticks: { callback: v => fmt(v) } } }
    }
  });

  mkChart('chartStaffDiscount', {
    type: 'bar',
    data: {
      labels: top10.map(r => r.cashier),
      datasets: [{ label: 'อัตราส่วนลด %', data: top10.map(r => r.discount_rate || 0),
        backgroundColor: top10.map(r => (r.discount_rate||0) > 20 ? '#ef4444' : (r.discount_rate||0) > 10 ? '#f59e0b' : '#10b981'),
        borderRadius: 4 }]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, datalabels: { display: false } },
      scales: { x: { beginAtZero: true, ticks: { callback: v => v + '%' } } }
    }
  });

  document.getElementById('staffBody').innerHTML = d.map((r, i) => {
    const branchLabel = r.primary_branch
      ? (r.branch_count > 1
          ? `${r.primary_branch} <span class="text-[10px] text-slate-400">+${r.branch_count - 1}</span>`
          : r.primary_branch)
      : '<span class="text-slate-300">—</span>';
    const branchTitle = (r.branches || []).join(', ');
    const drillBranch = _staffByBranch && r.primary_branch ? `, branch:'${jsAttr(r.primary_branch)}'` : '';
    return `
    <tr class="border-t border-slate-100 hover:bg-indigo-50 cursor-pointer"
        onclick="openDiscountBills({cashier:'${jsAttr(r.cashier)}'${drillBranch}, title:'บิลส่วนลด · ${jsAttr(r.cashier)}${_staffByBranch&&r.primary_branch?' @ '+jsAttr(r.primary_branch):''}'})"
        title="คลิกเพื่อดูบิลส่วนลดของ ${esc(r.cashier)}">
      <td class="py-2 pr-3 text-slate-400 font-medium">${i + 1}</td>
      <td class="py-2 pr-3 font-medium text-indigo-700">${esc(r.cashier)}</td>
      <td class="py-2 pr-3 text-slate-600" title="${esc(branchTitle)}">${branchLabel}</td>
      <td class="py-2 pr-3 text-right">${fmt(r.revenue)}</td>
      <td class="py-2 pr-3 text-right">${fmtN(r.tx_count)}</td>
      <td class="py-2 pr-3 text-right">${fmt(r.avg_ticket)}</td>
      <td class="py-2 pr-3 text-right">${fmtN(r.customer_count)}</td>
      <td class="py-2 pr-3 text-right">${fmtN(r.discount_tx)}</td>
      <td class="py-2 pr-3 text-right">${fmt(r.total_discount)}</td>
      <td class="py-2 text-right"><span class="${(r.discount_rate||0)>20?'text-red-600 font-bold':(r.discount_rate||0)>10?'text-amber-600':''}">${(r.discount_rate||0).toFixed(1)}%</span></td>
    </tr>`;
  }).join('');
}

// ════════════════════════════════════════════════════════════════
// DISCOUNTS & LEAKAGE
// ════════════════════════════════════════════════════════════════
async function loadChildBuffet() {
  const d = await api(`${API}/api/buffet/child${getBranch()?'?branch='+encodeURIComponent(getBranch()):''}`).catch(() => null);
  if (!d || !d.by_branch) return;
  const bb = d.by_branch;
  const momChip = p => p == null ? '<span class="text-slate-300 text-xs">—</span>'
    : `<span class="text-xs font-semibold" style="color:${p>=0?'var(--good-ink)':'var(--bad-ink)'}">${p>=0?'▲':'▼'} ${Math.abs(p).toFixed(1)}%</span>`;

  // Chain summary KPIs (latest vs prev from the trend)
  const tr = d.trend || [];
  const last = tr[tr.length - 1], prev = tr.length >= 2 ? tr[tr.length - 2] : null;
  const totCovers = bb.reduce((s, b) => s + (b.covers || 0), 0);
  const totRev    = bb.reduce((s, b) => s + (b.revenue || 0), 0);
  const totPrev   = bb.reduce((s, b) => s + (b.prev_covers || 0), 0);
  const momCovers = totPrev > 0 ? (totCovers - totPrev) / totPrev * 100 : null;
  const momRev    = (prev && prev.revenue > 0) ? ((last.revenue - prev.revenue) / prev.revenue * 100) : null;
  // % of buffet (chain): weighted by covers
  const wPct = bb.reduce((s, b) => s + (b.pct_of_buffet || 0) * (b.covers || 0), 0) / (totCovers || 1);
  const kpi = (label, value, chip, accent, sub) =>
    `<div class="card p-4" style="border-top:3px solid ${accent}">
      <div class="flex items-center justify-between gap-2"><p class="text-[11px] text-slate-500 uppercase tracking-wide">${label}</p>${chip||''}</div>
      <p class="text-2xl font-bold" style="color:${textInk(accent)}">${value}</p>
      <p class="text-[11px] text-slate-400">${sub||''}</p></div>`;
  document.getElementById('childKpiRow').innerHTML =
    kpi('covers เด็ก (เดือนล่าสุด)', fmtN(totCovers), momChip(momCovers), '#ec4899', `${d.latest_month||''} · เทียบ ${d.prev_month||'—'}`) +
    kpi('รายได้บุฟเฟ่ต์เด็ก', fmt(totRev), momChip(momRev), '#7a66b8', 'เดือนล่าสุด (ex-VAT)') +
    kpi('% ของบุฟเฟ่ต์', wPct.toFixed(1) + '%', '', '#0891b2', 'covers เด็ก ÷ covers บุฟเฟ่ต์รวม') +
    kpi('สาขาที่มีเด็กมากสุด', bb[0] ? esc(bb[0].branch) : '—', '', '#f59e0b', bb[0] ? fmtN(bb[0].covers) + ' covers' : '');

  // YTD trend (current year only)
  const curYear = (d.latest_month || '').slice(0, 4);
  const ytd = tr.filter(t => (t.month || '').slice(0, 4) === curYear);
  const noteEl = document.getElementById('childTrendNote');
  if (noteEl) noteEl.textContent = `รายเดือนปี ${curYear} · ${ytd.length} เดือน`;
  mkChart('chartChildYtd', {
    type: 'line',
    data: {
      labels: ytd.map(t => fmtMonth(t.month)),
      datasets: [
        { label: 'ลูกค้าเด็ก', data: ytd.map(t => t.covers), borderColor: '#ec4899',
          backgroundColor: 'rgba(236,72,153,0.10)', borderWidth: 2.5, pointRadius: 3, fill: true, tension: 0.3, yAxisID: 'y' },
        { label: 'รายได้', data: ytd.map(t => t.revenue), borderColor: '#7a66b8',
          borderWidth: 2, borderDash: [5, 4], pointRadius: 2, fill: false, tension: 0.3, yAxisID: 'yRev' },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { font: { size: 10 } } }, datalabels: { display: false },
        tooltip: { callbacks: { label: ctx => ctx.dataset.label + ': ' + (ctx.dataset.yAxisID === 'yRev' ? fmt(ctx.raw) : fmtN(ctx.raw)) } } },
      scales: {
        y:    { position: 'left',  beginAtZero: true, ticks: { font: { size: 9 }, callback: v => fmtN(v) }, title: { display: true, text: 'covers', font: { size: 9 } } },
        yRev: { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, ticks: { font: { size: 9 }, callback: v => fmt(v) }, title: { display: true, text: 'รายได้', font: { size: 9 } } },
        x:    { ticks: { font: { size: 9 } } },
      }
    }
  });

  // Per-branch boxes (latest month)
  document.getElementById('childBranchSub').textContent =
    `${bb.length} สาขา · ${d.latest_month || ''}${d.prev_month ? ` · MoM เทียบ ${d.prev_month}` : ''}`;
  document.getElementById('childBranchBoxes').innerHTML = bb.map(b => {
    const c = (b.mom_pct == null) ? '#94a3b8' : b.mom_pct >= 0 ? 'var(--good-ink)' : 'var(--bad-ink)';
    return `<div class="rounded-xl border p-3" style="border-color:#ec489933;background:#fdf2f80a">
      <p class="text-sm font-semibold text-slate-700 truncate" title="${esc(b.branch)}">${esc(b.branch)}</p>
      <p class="text-2xl font-extrabold mt-1 leading-none" style="color:var(--pink-ink)">${fmtN(b.covers)}</p>
      <p class="text-[11px] text-slate-400 mt-0.5">covers เด็ก · ${b.pct_of_buffet != null ? b.pct_of_buffet.toFixed(1) + '% ของบุฟเฟ่ต์' : '—'}</p>
      <p class="text-xs font-semibold mt-1" style="color:${textInk(c)}">${b.mom_pct == null ? '<span class="text-slate-400">ไม่มีเดือนก่อน</span>' : (b.mom_pct >= 0 ? '▲' : '▼') + ' ' + Math.abs(b.mom_pct).toFixed(1) + '% MoM'}</p>
      <p class="text-[11px] text-slate-400 mt-0.5">รายได้ ${fmt(b.revenue)}</p>
    </div>`;
  }).join('');
}

let DISC_REASON_DATA = [];
function exportDiscountReasons() {
  const s = DISC_REASON_DATA || [];
  if (!s.length) return uiNotice('ไม่มีข้อมูลให้ส่งออก');
  const rows = [['เหตุผล', 'บิล', 'มูลค่าลด', '%ของเงินลด'].join(',')];
  s.forEach(r => {
    const cells = [r.reason, r.bills, r.amount, r.pct];
    rows.push(cells.map(v => { v = String(v ?? '').replace(/"/g, '""'); return /[,"\n]/.test(v) ? `"${v}"` : v; }).join(','));
  });
  downloadCSV(rows.join('\n'), 'discount_reasons.csv');
}

async function loadDiscountReasons(params, gen) {
  const body = document.getElementById('disc-reason-body');
  const cards = document.getElementById('disc-reason-cards');
  const note = document.getElementById('disc-reason-note');
  if (!body) return;
  body.innerHTML = '<tr><td colspan="5" class="py-3 text-center text-slate-400">กำลังโหลด…</td></tr>';
  let d; try { d = await api(`${API}/api/analysis/discount-reasons?${params}`); }
  catch (e) { body.innerHTML = '<tr><td colspan="5" class="py-3 text-center text-slate-400">โหลดไม่สำเร็จ</td></tr>'; return; }
  if (gen != null && gen !== loadGen) return;
  if (d.staged === false) {
    if (note) note.textContent = '';
    cards.innerHTML = '';
    body.innerHTML = '<tr><td colspan="5" class="py-3 text-center text-slate-400">ยังไม่มีข้อมูล — รัน sync รอบใหม่เพื่อเก็บประเภทส่วนลด</td></tr>';
    return;
  }
  const reasons = d.reasons || [];
  DISC_REASON_DATA = reasons;
  if (note) note.textContent = `${fmtN(d.total_bills || 0)} บิลมีส่วนลด · รวม ${fmt(d.total_amount || 0)}`;
  // leakage banner: high "ไม่ระบุเหตุผล" share = cashiers discounting off-policy
  const ra = document.getElementById('disc-reason-alert');
  if (ra) {
    const up = d.untyped_pct;
    const u = reasons.find(r => r.reason_id === 0);
    if (up != null && up >= 15 && u) {
      ra.classList.remove('hidden');
      ra.innerHTML = `⚠ <b>ส่วนลด "ไม่ระบุเหตุผล" สูง ${up}%</b> (${fmt(u.amount)} · ${fmtN(u.bills)} บิล) — จุดรั่วที่ควรกำหนดนโยบายให้พนักงานเลือกเหตุผลทุกครั้ง`;
    } else ra.classList.add('hidden');
  }
  const untyped = reasons.find(r => r.reason_id === 0);
  const typedAmt = (d.total_amount || 0) - ((untyped && untyped.amount) || 0);
  cards.innerHTML = [
    { l: 'มูลค่าลดทั้งหมด', v: fmt(d.total_amount || 0), s: `${fmtN(d.total_bills || 0)} บิล`, c: '#ef4444' },
    { l: '⚠ ไม่ระบุเหตุผล', v: (d.untyped_pct != null ? d.untyped_pct + '%' : '—'), s: untyped ? `${fmt(untyped.amount)} · ${fmtN(untyped.bills)} บิล` : 'ไม่มี', c: '#f59e0b' },
    { l: '✓ ระบุเหตุผล', v: (d.total_amount ? (100 - (d.untyped_pct || 0)).toFixed(1) + '%' : '—'), s: fmt(typedAmt), c: '#10b981' },
  ].map(k => `<div class="ms-kpi" style="border:1px solid ${k.c}"><p class="text-[11px]" style="color:var(--text-muted)">${k.l}</p><p class="text-2xl font-bold" style="color:${textInk(k.c)}">${k.v}</p><p class="text-[11px]" style="color:var(--text-muted)">${k.s}</p></div>`).join('');
  // typed-vs-untyped split bar — instant read of the policy gap
  const splitEl = document.getElementById('disc-reason-split');
  if (splitEl) {
    const up = d.untyped_pct || 0, tp = Math.max(0, 100 - up);
    splitEl.innerHTML = (d.total_amount ? `
      <div class="flex items-center justify-between text-[11px] mb-1">
        <span style="color:var(--amber-ink);font-weight:700">⚠ ไม่ระบุเหตุผล ${up.toFixed(0)}%</span>
        <span style="color:var(--good-ink);font-weight:700">ระบุเหตุผล ${tp.toFixed(0)}% ✓</span>
      </div>
      <div class="flex w-full rounded-full overflow-hidden" style="height:14px;background:#e2e8f0">
        <div title="ไม่ระบุเหตุผล ${up.toFixed(1)}%" style="width:${up}%;background:#f59e0b"></div>
        <div title="ระบุเหตุผล ${tp.toFixed(1)}%" style="width:${tp}%;background:#10b981"></div>
      </div>` : '');
  }
  const maxAmt = Math.max(1, ...reasons.map(r => r.amount || 0));
  body.innerHTML = reasons.map(r => {
    const unty = r.reason_id === 0;
    const w = Math.round(100 * (r.amount || 0) / maxAmt);
    return `<tr class="border-t border-slate-100">
      <td class="py-1.5 pr-2 font-medium" style="${unty ? 'color:var(--amber-ink)' : ''}">${unty ? '⚠ ' : ''}${esc(r.reason)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${fmtN(r.bills)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:var(--bad-ink)">${fmt(r.amount)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${r.pct}%</td>
      <td class="py-1.5 pr-2" style="min-width:90px"><div style="background:var(--bg-subtle);border-radius:3px;overflow:hidden"><div style="width:${w}%;min-width:2px;height:8px;background:${unty ? '#f59e0b' : '#4a70b0'}"></div></div></td>
    </tr>`;
  }).join('') || '<tr><td colspan="5" class="py-3 text-center text-slate-400">ไม่มีส่วนลดในช่วงนี้</td></tr>';
}

async function loadCouponSeries(params, gen) {
  const body = document.getElementById('coupon-series-body');
  const cards = document.getElementById('coupon-series-cards');
  const note = document.getElementById('coupon-series-note');
  if (!body) return;
  body.innerHTML = '<tr><td colspan="9" class="py-3 text-center text-slate-400">กำลังโหลด…</td></tr>';
  let d; try { d = await api(`${API}/api/analysis/coupon-series?${params}`); }
  catch (e) { body.innerHTML = '<tr><td colspan="9" class="py-3 text-center text-slate-400">โหลดไม่สำเร็จ</td></tr>'; return; }
  if (gen != null && gen !== loadGen) return;
  if (d.staged === false) {
    if (note) note.textContent = '';
    cards.innerHTML = '';
    body.innerHTML = '<tr><td colspan="9" class="py-3 text-center text-slate-400">ยังไม่มีข้อมูล — รัน sync รอบใหม่เพื่อเก็บชื่อชุดคูปอง</td></tr>';
    return;
  }
  const s = d.series || [];
  COUPON_SERIES_DATA = s; COUPON_SERIES_MOM = d.mom || null;
  COUPON_DRILL_PARAMS = params.toString();   // remember filter for the drill modal
  // ── leakage alert: coupon series whose discount spiked MoM (≥+50%, material) ──
  const alertEl = document.getElementById('coupon-series-alert');
  const spikes = s.filter(r => r.mom_pct != null && r.mom_pct >= 50 && (r.pct || 0) >= 1)
                  .sort((a, b) => b.mom_pct - a.mom_pct).slice(0, 4);
  if (alertEl) {
    if (spikes.length) {
      alertEl.classList.remove('hidden');
      alertEl.innerHTML = `⚠ <b>คูปองที่พุ่งผิดปกติ (MoM ≥ +50%)</b> — ควรตรวจสอบการใช้: `
        + spikes.map(r => `${esc(r.name)} <b>▲${r.mom_pct.toFixed(0)}%</b> (${fmt(r.amount)})`).join(' · ');
    } else alertEl.classList.add('hidden');
  }
  const momNote = d.mom ? ` · MoM ${d.mom.cur} vs ${d.mom.prev}` : '';
  if (note) note.textContent = `${fmtN(d.series_count || 0)} ชุด · ใช้ ${fmtN(d.total_uses || 0)} ครั้ง · รวม ${fmt(d.total_amount || 0)}${momNote}`;
  const top = s[0];
  cards.innerHTML = [
    { l: 'มูลค่าคูปองรวม', v: fmt(d.total_amount || 0), s2: `${fmtN(d.total_uses || 0)} ครั้ง`, c: '#f59e0b' },
    { l: 'จำนวนชุดที่ใช้', v: fmtN(d.series_count || 0), s2: 'series', c: '#4a70b0' },
    { l: 'ชุดที่ลดมากสุด', v: top ? top.pct + '%' : '—', s2: top ? esc(top.name) : '—', c: '#ef4444' },
    { l: 'เฉลี่ย/ครั้ง', v: fmt(d.total_uses ? Math.round(d.total_amount / d.total_uses) : 0), s2: 'ต่อการใช้ 1 ครั้ง', c: '#10b981' },
  ].map(k => `<div class="ms-kpi" style="border:1px solid ${k.c}"><p class="text-[11px]" style="color:var(--text-muted)">${k.l}</p><p class="text-2xl font-bold" style="color:${textInk(k.c)}">${k.v}</p><p class="text-[11px] truncate" style="color:var(--text-muted)">${k.s2}</p></div>`).join('');
  const maxAmt = Math.max(1, ...s.map(r => r.amount || 0));
  const momCell = v => v == null ? '<span class="text-slate-300">—</span>'
    : `<span style="color:${v === 0 ? 'var(--text-muted)' : (v > 0 ? 'var(--bad-ink)' : 'var(--good-ink)')};font-weight:600">${v > 0 ? '▲' : v < 0 ? '▼' : ''} ${Math.abs(v).toFixed(1)}%</span>`;
  body.innerHTML = s.map((r, i) => {
    const w = Math.round(100 * (r.amount || 0) / maxAmt);
    const inactive = !r.active;
    return `<tr class="border-t border-slate-100 cursor-pointer hover:bg-amber-50/50" onclick="openCouponDrill(${r.id}, ${JSON.stringify(r.name).replace(/"/g, '&quot;')})">
      <td class="num tabular text-slate-400">${i + 1}</td>
      <td class="font-medium">${esc(r.name)}${inactive ? ' <span class="text-[10px] text-slate-400">(ปิดใช้)</span>' : ''}${r.code && r.code !== r.name ? `<div class="text-[10px] text-slate-400">${esc(r.code)}</div>` : ''}</td>
      <td class="num tabular text-slate-500">${fmtN(r.uses)}</td>
      <td class="num tabular font-semibold" style="color:var(--amber-ink)">${fmt(r.amount)}</td>
      <td class="num tabular text-slate-500">${fmt(r.avg)}</td>
      <td class="num tabular">${r.pct}%</td>
      <td class="num tabular">${momCell(r.mom_pct)}</td>
      <td style="min-width:90px"><div style="background:var(--bg-subtle);border-radius:3px;overflow:hidden"><div style="width:${w}%;min-width:2px;height:8px;background:#f59e0b"></div></div></td>
      <td class="text-slate-300">›</td>
    </tr>`;
  }).join('') || '<tr><td colspan="9" class="py-3 text-center text-slate-400">ไม่มีคูปองในช่วงนี้</td></tr>';
}

// ── coupon series export ──
let COUPON_SERIES_DATA = [], COUPON_SERIES_MOM = null;
function exportCouponSeries() {
  const s = COUPON_SERIES_DATA || [];
  if (!s.length) return uiNotice('ไม่มีข้อมูลให้ส่งออก');
  const momLbl = COUPON_SERIES_MOM ? `MoM%_${COUPON_SERIES_MOM.cur}_vs_${COUPON_SERIES_MOM.prev}` : 'MoM%';
  const rows = [['อันดับ', 'ชุดคูปอง', 'รหัส', 'ใช้งานอยู่', 'ครั้งที่ใช้', 'มูลค่าลด', 'เฉลี่ย/ครั้ง', '%ของคูปอง', momLbl].join(',')];
  s.forEach((r, i) => {
    const cells = [i + 1, r.name, r.code || '', r.active ? 'ใช้' : 'ปิด', r.uses, r.amount, r.avg, r.pct, (r.mom_pct == null ? '' : r.mom_pct)];
    rows.push(cells.map(v => { v = String(v ?? '').replace(/"/g, '""'); return /[,"\n]/.test(v) ? `"${v}"` : v; }).join(','));
  });
  downloadCSV(rows.join('\n'), 'coupon_series.csv');
}

// ── coupon series drill (by branch / by day) ──
let COUPON_DRILL_PARAMS = '';
let COUPON_DRILL = { id: null, name: '', by: 'branch' };
function openCouponDrill(id, name) {
  COUPON_DRILL = { id, name, by: 'branch' };
  document.getElementById('couponDrillTitle').textContent = name;
  document.getElementById('couponDrillModal').style.display = 'flex';
  setCouponDrillBy('branch');
}
function closeCouponDrill() { document.getElementById('couponDrillModal').style.display = 'none'; }
function setCouponDrillBy(by) {
  COUPON_DRILL.by = by;
  document.getElementById('cdTabBranch').classList.toggle('active', by === 'branch');
  document.getElementById('cdTabDay').classList.toggle('active', by === 'day');
  loadCouponDrill();
}
async function loadCouponDrill() {
  const body = document.getElementById('couponDrillBody');
  const head = document.getElementById('couponDrillHead');
  const by = COUPON_DRILL.by;
  head.textContent = by === 'day' ? 'วันที่' : 'สาขา';
  body.innerHTML = '<tr><td colspan="4" class="py-3 text-center text-slate-400">กำลังโหลด…</td></tr>';
  const qp = new URLSearchParams(COUPON_DRILL_PARAMS);
  qp.set('series_id', COUPON_DRILL.id); qp.set('by', by);
  let d; try { d = await api(`${API}/api/analysis/coupon-series/drill?${qp}`); }
  catch (e) { body.innerHTML = '<tr><td colspan="4" class="py-3 text-center text-slate-400">โหลดไม่สำเร็จ</td></tr>'; return; }
  if (d.available === false) {
    body.innerHTML = '<tr><td colspan="4" class="py-4 text-center text-slate-400">ยังไม่มีข้อมูลรายวัน — รัน sync รอบใหม่เพื่อเก็บ pos_coupon_day</td></tr>';
    return;
  }
  const rows = d.rows || [];
  const tot = rows.reduce((s, r) => s + (r.amount || 0), 0);
  const maxA = Math.max(1, ...rows.map(r => r.amount || 0));
  document.getElementById('couponDrillSum').textContent = `${fmtN(rows.length)} ${by === 'day' ? 'วัน' : 'สาขา'} · ใช้ ${fmtN(rows.reduce((s,r)=>s+(r.uses||0),0))} ครั้ง · รวม ${fmt(tot)}`;
  body.innerHTML = rows.map(r => {
    const w = Math.round(100 * (r.amount || 0) / maxA);
    return `<tr class="border-t border-slate-100">
      <td class="py-1.5 pr-2">${esc(r.label)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${fmtN(r.uses)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:var(--amber-ink)">${fmt(r.amount)}</td>
      <td class="py-1.5" style="min-width:120px"><div style="background:var(--bg-subtle);border-radius:3px;overflow:hidden"><div style="width:${w}%;min-width:2px;height:8px;background:#f59e0b"></div></div></td>
    </tr>`;
  }).join('') || '<tr><td colspan="4" class="py-3 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
}

// Per-branch Discounts & Leakage KPI boxes + abnormal-usage summary.
// disc% uses the reconstructed pre-discount base (matches discount_bills), so it
// is accurate for buffet bills and intentionally differs from the legacy chart's %.
async function discAlertSaveThresholds() {
  const keys = ['cash_pct','cash_min','untyped_pct','cashier_floor','repeat_min','late_from'];
  const obj = {};
  keys.forEach(k => { const el = document.getElementById('th_' + k); if (el && el.value !== '') obj[k] = parseFloat(el.value); });
  const s = document.getElementById('th_saved');
  try {
    const r = await fetch(`${API}/api/settings`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ discount_alert_thresholds: JSON.stringify(obj) }) });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    if (s) { s.textContent = '✓ บันทึกแล้ว'; setTimeout(() => s.textContent = '', 2500); }
    const p = new URLSearchParams();
    if (getMonthFrom()) p.set('month_from', getMonthFrom());
    if (getMonthTo())   p.set('month_to', getMonthTo());
    loadDiscountAlerts(p);
  } catch (e) { if (s) { s.textContent = 'บันทึกไม่สำเร็จ'; s.className = 'text-[11px] text-red-600'; } }
}

// Discount risk Alert Center — prioritized flags, each drillable to the matching bills
async function loadDiscountAlerts(params, gen) {
  const el = document.getElementById('disc-alert-list');
  const meta = document.getElementById('disc-alert-meta');
  if (!el) return;
  el.innerHTML = '<p class="text-sm text-slate-400 py-3 text-center">กำลังตรวจ…</p>';
  const d = await api(`${API}/api/analysis/discount_alerts?${params}`).catch(() => null);
  if (gen != null && gen !== loadGen) return;
  if (!d) { el.innerHTML = '<p class="text-sm text-slate-400 py-3 text-center">โหลดไม่ได้</p>'; if (meta) meta.textContent = ''; return; }
  const flags = d.flags || [];
  if (meta) meta.textContent = flags.length ? `● ${d.counts.high} ด่วน · ● ${d.counts.med} เฝ้าระวัง` : '';
  // threshold config form (populated from the current/effective thresholds)
  const cfgEl = document.getElementById('disc-alert-config');
  if (cfgEl && d.thresholds) {
    const T = d.thresholds;
    const F = [['cash_pct','เงินสด+ลดหนัก ≥ (%)'],['cash_min','เตือนเมื่อรวม ≥ (฿)'],
               ['untyped_pct','ไม่ระบุเหตุผล ≥ (%)'],['cashier_floor','แคชเชียร์ลด ≥ (%)'],
               ['repeat_min','ลดซ้ำ ≥ (ครั้ง)'],['late_from','ลดหลังเวลา (ชม.)']];
    cfgEl.innerHTML = `<p class="text-xs font-semibold text-slate-600 mb-2">เกณฑ์เตือน (ปรับแล้วมีผลทุกสาขา)</p>
      <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
        ${F.map(([k,lbl])=>`<label class="text-[11px] text-slate-500">${lbl}<input id="th_${k}" type="number" step="any" value="${T[k]}" class="w-full text-xs border border-slate-300 rounded px-2 py-1 mt-0.5"></label>`).join('')}
      </div>
      <div class="flex items-center gap-2 mt-2">
        <button onclick="discAlertSaveThresholds()" class="text-xs text-white rounded px-3 py-1" style="background:var(--accent)">บันทึกเกณฑ์</button>
        <span id="th_saved" class="text-[11px] text-green-600"></span>
        <a href="/docs/POS_DISCOUNT_CONTROLS.html" target="_blank" class="text-[11px] text-indigo-600 underline" style="margin-left:auto">แนวทางตั้งค่า POS เพื่อป้องกัน</a>
      </div>`;
  }
  if (!flags.length) {
    el.innerHTML = '<div class="px-3 py-2 rounded-lg text-sm" style="background:#f0fdf4;border:1px solid #22c55e;color:#166534">✓ ไม่พบสัญญาณส่วนลดผิดปกติในช่วงนี้</div>';
    return;
  }
  const ICON = { cash_heavy: '', untyped: '', cashier: '', repeat: '', late: '', drift: '▲', void: '✗' };
  el.innerHTML = flags.map(f => {
    const dot = f.severity === 'high' ? '#dc2626' : '#f59e0b';
    const opts = Object.assign({ sort: 'discount' }, f.drill || {});
    if (!opts.title) opts.title = 'บิลส่วนลด · ' + (opts.branch || '');
    const drillAttr = f.drill ? `onclick='openDiscountBills(${JSON.stringify(opts).replace(/'/g, "&#39;")})'` : '';
    const clk = f.drill ? 'cursor-pointer hover:bg-slate-50' : '';
    return `<div class="flex items-start gap-2 py-2 px-2 border-b border-slate-50 rounded ${clk}" ${drillAttr}>
      <span style="width:9px;height:9px;border-radius:50%;background:${dot};flex:none;margin-top:5px"></span>
      <span style="flex:none">${ICON[f.type] || '•'}</span>
      <span class="text-sm text-slate-700" style="flex:1">${esc(f.text)}</span>
      ${f.drill ? '<span class="text-[10px] text-slate-300" style="margin-top:3px"></span>' : ''}
    </div>`;
  }).join('');
}

// Payment method × discount — cash+heavy leakage flag + payment mix on discount bills
async function loadPaymentDiscount(params, gen) {
  const sumEl = document.getElementById('paydisc-summary');
  const chEl  = document.getElementById('paydisc-cashheavy');
  const mixEl = document.getElementById('paydisc-mix');
  if (!chEl) return;
  chEl.innerHTML = mixEl.innerHTML = '<p class="text-sm text-slate-400 py-3 text-center">กำลังโหลด…</p>';
  const d = await api(`${API}/api/analysis/payment_discount?${params}`).catch(() => null);
  if (gen != null && gen !== loadGen) return;
  if (!d) { chEl.innerHTML = mixEl.innerHTML = '<p class="text-sm text-slate-400 py-3 text-center">โหลดไม่ได้</p>'; return; }
  if (!d.available) {
    sumEl.className = 'mb-4 text-sm px-3 py-2 rounded-lg';
    sumEl.style.cssText = 'background:#fffbeb;border:1px solid #f59e0b;color:#92400e';
    sumEl.textContent = 'ยังไม่มีข้อมูลวิธีจ่ายรายบิล (sale_payment) — รัน sync รอบใหม่';
    chEl.innerHTML = mixEl.innerHTML = '';
    return;
  }
  const ch = d.cash_heavy || {};
  sumEl.className = 'mb-4 text-sm px-3 py-2 rounded-lg';
  sumEl.style.cssText = ch.bills
    ? 'background:#fef2f2;border:1px solid #ef4444;color:#991b1b'
    : 'background:#f0fdf4;border:1px solid #22c55e;color:#166534';
  sumEl.innerHTML = ch.bills
    ? `⚑ <b>เงินสด + ลดหนัก ≥${d.heavy_pct}%</b>: ${fmtN(ch.bills)} บิล · <b>${fmt(ch.discount)}</b> (${ch.share_of_discount}% ของเงินลดทั้งหมด) — กระจุกที่ <b>${esc(ch.top_branch||'—')}</b> ${fmt(ch.top_branch_discount||0)}`
    : `✓ ไม่พบบิลเงินสด + ลดหนัก ≥${d.heavy_pct}%`;

  const chb = ch.branches || [];
  const maxc = Math.max(1, ...chb.map(b => b.discount || 0));
  chEl.innerHTML = chb.length ? chb.map(b => {
    const w = Math.max(3, Math.round(100 * (b.discount || 0) / maxc));
    return `<div class="flex items-center gap-2 py-1 px-1 rounded cursor-pointer hover:bg-red-50/60"
        onclick="openDiscountBills({branch:'${jsAttr(b.branch)}', payment:'Cash', sort:'discount', title:'บิลส่วนลด · เงินสด · ${jsAttr(b.branch)}'})" title="คลิกดูบิล (เฉพาะเงินสด)">
      <span class="text-xs truncate" style="width:80px">${esc(b.branch)}</span>
      <div class="flex-1" style="background:#fee2e2;border-radius:var(--r-md);overflow:hidden"><div style="width:${w}%;height:16px;background:#ef4444;border-radius:var(--r-md)"></div></div>
      <span class="text-xs text-slate-600 tabular-nums" style="width:104px;text-align:right">${fmtN(b.bills)} บิล · ${fmt(b.discount)}</span>
    </div>`;
  }).join('') : '<p class="text-sm text-slate-400 py-3 text-center">— ไม่พบ —</p>';

  const mix = (d.mix || []).slice(0, 10);
  const maxb = Math.max(1, ...mix.map(m => m.bills || 0));
  mixEl.innerHTML = mix.map(m => {
    const w = Math.max(3, Math.round(100 * (m.bills || 0) / maxb));
    const isCash = /cash/i.test(m.code || '');
    const heavy = (m.disc_pct || 0) >= 20;
    return `<div class="flex items-center gap-2 py-1">
      <span class="text-xs truncate ${isCash?'font-semibold text-red-600':'text-slate-600'}" style="width:108px" title="${esc(m.code)}">${esc(m.code)}</span>
      <div class="flex-1" style="background:var(--accent-soft);border-radius:var(--r-md);overflow:hidden"><div style="width:${w}%;height:16px;background:${isCash?'#ef4444':'#4a70b0'};border-radius:var(--r-md)"></div></div>
      <span class="text-xs tabular-nums" style="width:96px;text-align:right">${fmtN(m.bills)} · <b style="color:${heavy?'var(--bad-ink)':'var(--text-muted)'}">${m.disc_pct}%</b></span>
    </div>`;
  }).join('');
}

async function loadDiscountLeakage(params, gen) {
  const wrap  = document.getElementById('disc-leak-boxes');
  const sumEl = document.getElementById('disc-leak-summary');
  const meta  = document.getElementById('disc-leak-meta');
  if (!wrap) return;
  wrap.innerHTML = '<div class="col-span-full text-center text-slate-400 py-4 text-sm">กำลังโหลด…</div>';
  const d = await api(`${API}/api/analysis/discount_leakage?${params}`).catch(() => null);
  if (gen != null && gen !== loadGen) return;
  if (!d || !Array.isArray(d.branches) || !d.branches.length) {
    wrap.innerHTML = '<div class="col-span-full text-center text-slate-400 py-4 text-sm">ไม่มีข้อมูลส่วนลดในช่วงนี้</div>';
    if (sumEl) { sumEl.removeAttribute('style'); sumEl.innerHTML = ''; }
    if (meta) meta.textContent = '';
    return;
  }
  const chain = d.chain || {};
  const avg = chain.disc_pct || 0;

  // Abnormal-usage summary banner — red if any branch flagged, green otherwise.
  const hasAbn = (d.abnormal_branches || []).length > 0;
  sumEl.className = 'mb-4 text-sm px-3 py-2 rounded-lg';
  sumEl.style.cssText = hasAbn
    ? 'background:#fef2f2;border:1px solid #ef4444;color:#991b1b'
    : 'background:#f0fdf4;border:1px solid #22c55e;color:#166534';
  sumEl.textContent = d.summary || '';

  meta.textContent = `ค่าเฉลี่ยทุกสาขา ${avg.toFixed(2)}% · กลาง ${(chain.median_branch_pct||0).toFixed(2)}% · เกณฑ์ผิดปกติ ≥ ${(chain.threshold_pct||0).toFixed(2)}% · ${chain.branch_count||0} สาขา`
    + (d.reasons_staged ? '' : ' · (ยังไม่มีข้อมูลประเภทส่วนลด)');

  wrap.innerHTML = d.branches.map(b => {
    const abn = b.is_abnormal;
    const pct = b.disc_pct || 0;
    const pctColor = abn ? 'var(--bad-ink)' : (pct >= avg ? '#d97706' : 'var(--good-ink)');
    const vs = b.vs_avg_pts || 0;
    const vsColor = vs > 0 ? 'var(--bad-ink)' : 'var(--good-ink)';
    const vsTxt = (vs > 0 ? '▲ +' : '▼ ') + Math.abs(vs).toFixed(2) + ' pts · ×' + (b.ratio_vs_chain != null ? b.ratio_vs_chain.toFixed(2) : '–') + ' ค่าเฉลี่ย';
    const reason = b.top_reason ? esc(b.top_reason) : '—';
    const untyped = b.top_reason && b.top_reason.indexOf('ไม่ระบุ') >= 0;
    const reasonColor = untyped ? 'var(--amber-ink)' : 'var(--text-secondary)';
    const reasonPct = b.top_reason_pct ? ` (${Math.round(b.top_reason_pct)}%)` : '';
    return `<div class="card p-3 cursor-pointer hover:ring-2 hover:ring-indigo-200 ${abn ? 'ring-2 ring-red-300' : ''}"
        ${abn ? 'style="background:#fff7f7"' : ''}
        onclick="openDiscountBills({branch:'${jsAttr(b.branch)}', title:'บิลส่วนลด · ${jsAttr(b.branch)}'})" title="คลิกดูบิลส่วนลดของสาขานี้">
      <div class="flex items-center justify-between">
        <span class="font-semibold text-sm truncate">${abn ? '⚑ ' : ''}${esc(b.branch)}</span>
        <span class="text-[10px] text-slate-300"></span>
      </div>
      <p class="mt-1 font-bold" style="color:${textInk(pctColor)};font-size:1.4rem;line-height:1.1">${pct.toFixed(2)}<span style="font-size:.8rem">%</span></p>
      <p class="text-xs text-slate-500">ส่วนลดรวม <b>${fmt(b.total_discount||0)}</b> · ${fmtN(b.discount_tx||0)} บิล</p>
      <p class="text-[11px] mt-0.5" style="color:${textInk(vsColor)}">${vsTxt}</p>
      <p class="text-[11px] mt-1 truncate" style="color:${textInk(reasonColor)}" title="${reason}${reasonPct}">ประเภทเยอะสุด: <b>${reason}</b>${reasonPct}</p>
    </div>`;
  }).join('');

  // Paired chart: % เงินลดต่อยอดขาย รายสาขา — same (correct-base) metric as the KPI boxes
  if (typeof mkChart === 'function' && document.getElementById('chartDiscPctBranch')) {
    const cr = [...d.branches].sort((a, b) => (b.disc_pct || 0) - (a.disc_pct || 0));
    mkChart('chartDiscPctBranch', {
      type: 'bar',
      data: {
        labels: cr.map(b => b.branch),
        datasets: [{ label: '% เงินลดต่อยอดขาย', data: cr.map(b => b.disc_pct || 0),
          backgroundColor: cr.map(b => b.is_abnormal ? '#dc2626' : ((b.disc_pct||0) >= avg ? '#f59e0b' : '#22c55e')), borderRadius: 4 }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        onClick: (e, els) => { if (els.length) { const br = cr[els[0].index]; if (br) openDiscountBills({ branch: br.branch, title: 'บิลส่วนลด · ' + br.branch }); } },
        plugins: { legend: { display: false }, datalabels: { display: false },
          tooltip: { callbacks: {
            label: c => ` ${c.parsed.y.toFixed(2)}% ของยอด` + (cr[c.dataIndex].ratio_vs_chain != null ? ` · ×${cr[c.dataIndex].ratio_vs_chain.toFixed(2)} ค่าเฉลี่ย` : ''),
            afterLabel: () => 'คลิกเพื่อดูบิล' } } },
        scales: { y: { beginAtZero: true, title: { display: true, text: '% เงินลดต่อยอดขาย', font: { size: 10 } }, ticks: { callback: v => v + '%' } } }
      }
    });
  }
}

async function loadDiscountsTab() {
  // Day/range filter: from+to → range; only one set → that single day; none → global month.
  const fromV = (document.getElementById('disc-day-from') || {}).value || '';
  const toV   = (document.getElementById('disc-day-to')   || {}).value || '';
  let dFrom = '', dTo = '';
  if (fromV && toV)   { dFrom = (fromV <= toV) ? fromV : toV; dTo = (fromV <= toV) ? toV : fromV; }
  else if (fromV)     { dFrom = dTo = fromV; }
  else if (toV)       { dFrom = dTo = toV; }
  const hasDay = !!dFrom;
  const _fk = _filterKey() + '|d:' + dFrom + '~' + dTo;
  if (_tabLastKey.discounts === _fk) return; _tabLastKey.discounts = _fk;
  const gen = loadGen;

  const noteEl = document.getElementById('disc-day-note');
  const params = new URLSearchParams();
  if (getBranch()) params.set('branch', getBranch());
  if (hasDay) {
    params.set('date_from', dFrom);
    params.set('date_to', dTo);
    if (noteEl) noteEl.classList.remove('hidden');
  } else {
    if (getMonthFrom()) params.set('month_from', getMonthFrom());
    if (getMonthTo())   params.set('month_to', getMonthTo());
    if (noteEl) noteEl.classList.add('hidden');
  }

  loadDiscountReasons(new URLSearchParams(params), gen);
  loadCouponSeries(new URLSearchParams(params), gen);
  // Per-branch leakage view is a cross-branch comparison by design → ignore the
  // branch filter, honour only the period range.
  const _leakP = new URLSearchParams();
  if (hasDay) {
    _leakP.set('date_from', dFrom);
    _leakP.set('date_to', dTo);
  } else {
    if (getMonthFrom()) _leakP.set('month_from', getMonthFrom());
    if (getMonthTo())   _leakP.set('month_to', getMonthTo());
  }
  loadDiscountLeakage(_leakP, gen);
  loadPaymentDiscount(new URLSearchParams(_leakP), gen);
  loadDiscountAlerts(new URLSearchParams(_leakP), gen);
  const d = await api(`${API}/api/analysis/discounts?${params}`).catch(() => ({ overall: {}, trend: [], by_branch: [], top_cashiers: [] }));
  if (gen !== loadGen) return;
  const o = d.overall || {};

  const discRate = o.total_tx ? (o.discount_tx / o.total_tx * 100).toFixed(1) : '0';
  // Carry the tab's active day/range into the KPI drill so the bill list matches the
  // KPI scope (not the global month). Empty when on the month fallback.
  const drillDate = hasDay ? `date_from:'${dFrom}',date_to:'${dTo}',` : '';
  document.getElementById('disc-kpi-row').innerHTML = [
    { label: 'ส่วนลดรวม', value: `${fmt(o.total_discount||0)}`, sub: `${discRate}% ของบิลทั้งหมด`, color: '#ef4444', drill: `openDiscountBills({${drillDate}title:'บิลส่วนลดทั้งหมด (ตามตัวกรอง)'})` },
    { label: 'จำนวนบิลที่ลด', value: fmtN(o.discount_tx||0), sub: `จากทั้งหมด ${fmtN(o.total_tx||0)} บิล`, color: '#f59e0b', drill: `openDiscountBills({${drillDate}sort:'date', title:'บิลที่มีส่วนลด (ล่าสุด)'})` },
    { label: 'Avg Discount/Tx', value: `${fmt(o.avg_discount||0)}`, sub: 'when discount given', color: '#7a66b8' },
    { label: `เกินเพดานส่วนลด ${o.policy_pct != null ? o.policy_pct : (d.policy_pct != null ? d.policy_pct : 2.5)}%`, value: `${fmt(o.excess_baht||0)}`,
      sub: `${o.branches_over_policy||0} สาขาเกินเพดาน · ${fmtN(o.total_tx||0)} บิล · ตั้งเพดานที่ Settings`, color: (o.excess_baht||0) > 0 ? 'var(--bad-ink)' : 'var(--good-ink)' },
  ].map(k => `<div class="card p-4 ${k.drill?'cursor-pointer hover:ring-2 hover:ring-indigo-200':''}" ${k.drill?`onclick="${k.drill}"`:''}><p class="kpi-label">${k.label}${k.drill?'':''}</p><p class="kpi-value" style="color:${textInk(k.color)}">${k.value}</p><p class="text-xs mt-1" style="color:var(--text-muted)">${k.sub}</p></div>`).join('');

  // Trend chart
  const trend = d.trend || [];
  mkChart('chartDiscTrend', {
    type: 'line',
    data: {
      labels: trend.map(t => t.period_month),
      datasets: [
        { label: '% บิลที่มีส่วนลด', data: trend.map(t => t.rate), borderColor: '#ef4444', backgroundColor: '#ef444422', tension: 0.3, fill: true, yAxisID: 'rate' },
        { label: 'ส่วนลดรวม', data: trend.map(t => t.total_discount), borderColor: '#f59e0b', backgroundColor: 'transparent', tension: 0.3, borderDash: [4,4], yAxisID: 'amt' },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom', labels: { font: { size: 10 } } }, datalabels: { display: false } },
      scales: {
        rate: { type: 'linear', position: 'left', ticks: { callback: v => v + '%' } },
        amt:  { type: 'linear', position: 'right', grid: { drawOnChartArea: false }, ticks: { callback: v => fmt(v) } }
      }
    }
  });

  // % บิลที่มีส่วนลด รายสาขา — frequency view (sorted desc; click a bar to drill)
  const byBranch = [...(d.by_branch || [])].sort((a, b) => (b.rate || 0) - (a.rate || 0));
  mkChart('chartDiscBranch', {
    type: 'bar',
    data: {
      labels: byBranch.map(b => b.branch),
      datasets: [{ label: '% บิลที่มีส่วนลด', data: byBranch.map(b => b.rate || 0),
        backgroundColor: byBranch.map(b => (b.rate||0) > 20 ? '#ef4444' : '#f59e0b'), borderRadius: 4 }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      onClick: (e, els) => { if (els.length) { const br = byBranch[els[0].index]; if (br) openDiscountBills({ branch: br.branch, title: 'บิลส่วนลด · ' + br.branch }); } },
      plugins: { legend: { display: false }, datalabels: { display: false },
        tooltip: { callbacks: { label: c => ` ${c.parsed.y}% ของบิลมีส่วนลด`, afterLabel: () => 'คลิกเพื่อดูบิล' } } },
      scales: { y: { beginAtZero: true, title: { display: true, text: '% บิลที่มีส่วนลด', font: { size: 10 } }, ticks: { callback: v => v + '%' } } }
    }
  });

  // ── ลักษณะการลด: visual "where the discount money goes" ──────────────────
  (() => {
    const el = document.getElementById('disc-pattern');
    if (!el) return;
    const pat = d.pattern_groups || [];
    if (!pat.length) { el.innerHTML = '<p class="text-sm text-slate-400 text-center py-6">— ไม่มีส่วนลดในช่วงนี้ —</p>'; return; }
    const META = {
      '100% (ฟรี/เท่ายอดบิล)':      { c:'#991b1b', heavy:true },
      '50% ขึ้นไป':                 { c:'#dc2626', heavy:true },
      '20–50%':                     { c:'#f97316', heavy:true },
      '10–20%':                     { c:'#eab308', heavy:false },
      'ต่ำกว่า 10%':                { c:'#22c55e', heavy:false },
      'ไม่มียอดบิล (เฉพาะส่วนลด)':  { c:'#64748b', heavy:true },
    };
    const order = ['100% (ฟรี/เท่ายอดบิล)','50% ขึ้นไป','20–50%','10–20%','ต่ำกว่า 10%','ไม่มียอดบิล (เฉพาะส่วนลด)'];
    const byG = {}; pat.forEach(r => byG[r.grp] = r);
    const rows = order.map(g => byG[g]).filter(Boolean);
    const tot = rows.reduce((s,r)=>s+(r.baht||0),0) || 1;
    const heavyBaht = rows.filter(r => (META[r.grp]||{}).heavy).reduce((s,r)=>s+(r.baht||0),0);
    const heavyPct = heavyBaht/tot*100;
    const hc = heavyPct>=40 ? '#b91c1c' : heavyPct>=20 ? '#d97706' : '#047857';
    const hi = heavyPct>=40 ? '▼' : heavyPct>=20 ? '⚠' : '✓';
    const seg = rows.map(r => { const w=(r.baht||0)/tot*100, m=META[r.grp]||{c:'#94a3b8'};
      return w<=0 ? '' : `<div title="${esc(r.grp)}: ${fmt(r.baht)} (${w.toFixed(0)}%)" style="width:${w}%;background:${m.c}"></div>`; }).join('');
    el.innerHTML =
      `<div class="px-3 py-2 rounded-lg text-sm mb-3" style="background:${heavyPct>=20?'#fef2f2':'#f0fdf4'};color:${hc};font-weight:600;line-height:1.4">
        ${hi} เงินลด <b>${heavyPct.toFixed(0)}%</b> (${fmt(heavyBaht)}) ไปกับบิลที่ <b>ลดหนัก ≥20%</b> ของยอด${heavyPct>=20?' — ควรตรวจสอบ':''}</div>
      <div class="flex w-full rounded-full overflow-hidden mb-3" style="height:14px;background:#f1f5f9">${seg}</div>`
      + rows.map(r => { const m=META[r.grp]||{c:'#94a3b8'}, share=(r.baht||0)/tot*100;
        return `<div class="flex items-center gap-2 py-1.5 px-1 rounded cursor-pointer hover:bg-indigo-50/60 border-b border-slate-50" ${_discPatternDrillAttr(r.grp)}>
          <span style="width:11px;height:11px;border-radius:3px;background:${m.c};flex:none"></span>
          <span class="text-sm ${m.heavy?'font-semibold':''}" style="color:${m.heavy?textInk(m.c):'var(--text-primary)'}">${esc(r.grp)}</span>
          <span class="text-[10px] text-slate-400">${fmtN(r.bills)} บิล</span>
          <span class="ml-auto text-sm font-bold tabular-nums" style="color:${textInk(m.c)}">${share.toFixed(0)}%</span>
          <span class="text-xs text-slate-500 tabular-nums" style="width:74px;text-align:right">${fmt(r.baht)}</span>
        </div>`; }).join('');
  })();

  // ── ค่าลดยอดฮิต: ranked bar list ─────────────────────────────────────────
  (() => {
    const el = document.getElementById('disc-amounts');
    if (!el) return;
    const amts = (d.common_amounts||[]).slice(0, 8);
    if (!amts.length) { el.innerHTML = '<p class="text-sm text-slate-400 text-center py-6">— ไม่มีส่วนลดในช่วงนี้ —</p>'; return; }
    const maxB = Math.max(1, ...amts.map(r=>r.bills||0));
    const t = amts[0];
    el.innerHTML =
      `<div class="px-3 py-2 rounded-lg text-sm mb-3" style="background:#eff6ff;color:#1e40af;font-weight:600;line-height:1.4">
        ส่วนลด <b>${fmtN(t.amount)} ฿</b> ใช้บ่อยสุด <b>${fmtN(t.bills)} ครั้ง</b> — น่าจะเป็นคูปอง/โปรมาตรฐาน</div>`
      + amts.map(r => { const w=Math.max(3, Math.round(100*(r.bills||0)/maxB));
        return `<div class="flex items-center gap-2 py-1 px-1 rounded cursor-pointer hover:bg-indigo-50/60"
            onclick="openDiscountBills({amount:${r.amount}, title:'บิลส่วนลด ${fmtN(r.amount)} ฿ พอดี'})">
          <span class="text-sm font-semibold text-indigo-700 tabular-nums" style="width:62px">${fmtN(r.amount)} ฿</span>
          <div class="flex-1" style="background:#f1f5f9;border-radius:var(--r-md);overflow:hidden"><div style="width:${w}%;height:18px;background:var(--accent);border-radius:var(--r-md)"></div></div>
          <span class="text-xs text-slate-600 tabular-nums" style="width:118px;text-align:right">${fmtN(r.bills)} ครั้ง · ${fmt(r.baht)}</span>
        </div>`; }).join('');
  })();

  // Outlier callout
  const cashiers = d.top_cashiers || [];
  const outliers = cashiers.filter(c => c.is_outlier);
  const note = document.getElementById('disc-outlier-note');
  if (outliers.length) {
    note.classList.remove('hidden');
    note.innerHTML = `⚑ <b>${outliers.length} แคชเชียร์</b>ให้ส่วนลดเกิน <b>${d.outlier_threshold_pct}%</b> ของยอดขาย (เกณฑ์ = 2× ค่ากลาง) — ` +
      outliers.slice(0,5).map(c=>`${esc(c.cashier)} (${esc(c.branch)}) ${(c.disc_pct_of_sales||0).toFixed(1)}%`).join(' · ');
  } else { note.classList.add('hidden'); }

  // Top cashiers table — store data, render in current (flat | grouped) mode
  _discData = d;
  renderDiscCashiers();
}

let _discData = null;
let _discGroupByBranch = true;   // default: split by branch (don't merge cashiers)

function setDiscGroup(on) {
  _discGroupByBranch = on;
  document.getElementById('discGrpFlat').classList.toggle('active', !on);
  document.getElementById('discGrpBranch').classList.toggle('active', on);
  renderDiscCashiers();
}

function _discCashierRow(r) {
  const drill = r.cashier
    ? `onclick="openDiscountBills({cashier:'${jsAttr(r.cashier)}', branch:'${jsAttr(r.branch||'')}', title:'บิลส่วนลด · ${jsAttr(r.cashier)}'})" title="คลิกดูบิลส่วนลด"`
    : '';
  return `<tr class="border-t border-slate-100 hover:bg-indigo-50 ${r.cashier?'cursor-pointer':''} ${r.is_outlier?'bg-red-50':''}" ${drill}>
      <td class="py-2 pr-3 font-medium ${r.cashier?'text-indigo-700':''}">${r.is_outlier?'⚑ ':''}${esc(r.cashier || '—')}${r.cashier?'':''}</td>
      <td class="py-2 pr-3 text-xs text-slate-500">${esc(r.branch || '—')}</td>
      <td class="py-2 pr-3 text-right">${fmtN(r.total_tx)}</td>
      <td class="py-2 pr-3 text-right">${fmtN(r.discount_tx)}</td>
      <td class="py-2 pr-3 text-right">${(r.rate||0).toFixed(1)}%</td>
      <td class="py-2 pr-3 text-right"><span class="${r.is_outlier?'text-red-600 font-bold':''}">${(r.disc_pct_of_sales!=null?r.disc_pct_of_sales.toFixed(1)+'%':'—')}</span></td>
      <td class="py-2 pr-3 text-right">${fmt(r.total_discount)}</td>
      <td class="py-2 text-right">${fmt(r.avg_discount)}</td>
    </tr>`;
}

function renderDiscCashiers() {
  const body = document.getElementById('discCashierBody');
  if (!body || !_discData) return;
  const cashiers = _discData.top_cashiers || [];

  if (!_discGroupByBranch) {
    body.innerHTML = cashiers.map(_discCashierRow).join('') ||
      '<tr><td colspan="8" class="py-3 text-center text-slate-400">—</td></tr>';
    return;
  }

  // Grouped by branch: use full branch totals (from by_branch) as group headers,
  // list each branch's top cashiers underneath.
  const bTot = {};
  (_discData.by_branch || []).forEach(b => { bTot[b.branch] = b; });
  const groups = {};
  cashiers.forEach(c => { (groups[c.branch] = groups[c.branch] || []).push(c); });
  const branches = Object.keys(groups).sort((a,b) =>
    ((bTot[b]?.total_discount)||0) - ((bTot[a]?.total_discount)||0));

  body.innerHTML = branches.map(br => {
    const bt = bTot[br] || {};
    const rows = groups[br].sort((x,y)=>(y.total_discount||0)-(x.total_discount||0));
    const over = !!bt.over_policy;
    const flag = over || (bt.disc_pct_of_sales != null && bt.disc_pct_of_sales >= (_discData.outlier_threshold_pct||99));
    const header = `<tr style="background:var(--accent-soft)"><td colspan="5" class="py-1.5 pr-3 font-bold text-indigo-800">
        ${flag?'⚑ ':''}${esc(br)} <span class="font-normal text-slate-500">· ${rows.length} แคชเชียร์</span></td>
      <td class="py-1.5 pr-3 text-right font-bold ${flag?'text-red-600':'text-indigo-800'}">${bt.disc_pct_of_sales!=null?bt.disc_pct_of_sales.toFixed(1)+'%':'—'}</td>
      <td class="py-1.5 pr-3 text-right font-bold text-indigo-800">${fmt(bt.total_discount||0)}</td>
      <td class="py-1.5 text-right text-xs ${over?'text-red-600 font-semibold':'text-slate-400'}" title="ส่วนลดที่ให้เกินเพดานนโยบาย ${_discData.policy_pct != null ? _discData.policy_pct : ''}% ของยอดขายก่อนลด">${over ? 'เกินเพดาน ' + fmt(bt.excess_baht||0) : ''}</td></tr>`;
    return header + rows.map(_discCashierRow).join('');
  }).join('') || '<tr><td colspan="8" class="py-3 text-center text-slate-400">—</td></tr>';
}

// ════════════════════════════════════════════════════════════════
// TIME & FLOW — Daypart × DOW Heatmap
// ════════════════════════════════════════════════════════════════
let _timeflowMetric = 'revenue';
let _tfOpenFrom = 10, _tfOpenTo = 22;  // default open hours; updated from hourly_open data
let _timeGranularity = 'daily';  // daily|weekly|monthly for Branch Ops → Time tab

let _timeflowData = [];

function setTimeflowMetric(m) {
  _timeflowMetric = m;
  ['revenue','tx_count','customer_count','avg_ticket'].forEach(k => {
    document.getElementById('tf-metric-' + k)?.classList.toggle('active', k === m);
  });
  loadTimeflow();
}

let _tfHourly = [];
let _tfHourMode = 'avg';   // 'avg' = per-day average · 'total' = sum over the period
function setTfHourMode(m) {
  _tfHourMode = m;
  document.getElementById('tfHourAvg')?.classList.toggle('active', m === 'avg');
  document.getElementById('tfHourTotal')?.classList.toggle('active', m === 'total');
  renderTfHourlyChart();
}
function renderTfHourlyChart() {
  const hrs = _tfHourly || [];
  const avg = _tfHourMode === 'avg';
  const val = h => avg ? (h.avg_daily_revenue ?? h.revenue) : (h.revenue ?? 0);
  mkChart('chartTfHourly', {
    type: 'bar',
    data: {
      labels: hrs.map(h => `${h.hour}:00`),
      datasets: [{ label: avg ? 'Avg Daily Revenue' : 'Total Revenue', data: hrs.map(val),
        backgroundColor: hrs.map(h => val(h) > 0 ? PALETTE_6[0] + 'cc' : '#e2e8f0'), borderRadius: 4 }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      onClick: (e, els) => { if (els.length) { const h = hrs[els[0].index]; if (h) openHourDetail(h.hour); } },
      plugins: { legend: { display: false }, datalabels: { display: false },
        tooltip: { callbacks: { label: ctx => avg
          ? ` ${fmtN(ctx.raw)} เฉลี่ย/วัน · ${fmtN(hrs[ctx.dataIndex]?.tx_count ?? 0)} บิลรวม`
          : ` ${fmtN(ctx.raw)} รวม · ${fmtN(hrs[ctx.dataIndex]?.tx_count ?? 0)} บิล`,
          afterLabel: () => 'คลิกเพื่อเจาะลึก' } } },
      scales: { y: { beginAtZero: true, ticks: { callback: v => fmt(v) } } }
    }
  });
}

function closeHourDetail() { const m = document.getElementById('hourDetailModal'); if (m) m.style.display = 'none'; }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeHourDetail(); });

async function openHourDetail(hour) {
  const modal = document.getElementById('hourDetailModal');
  const title = document.getElementById('hourDetailTitle');
  const body  = document.getElementById('hourDetailBody');
  const p = new URLSearchParams({ hour });
  if (getBranch())    p.set('branch', getBranch());
  if (getMonthFrom()) p.set('month_from', getMonthFrom());
  if (getMonthTo())   p.set('month_to', getMonthTo());
  title.textContent = `ชั่วโมง ${hour}:00–${(hour+1)%24}:00`;
  body.innerHTML = '<p class="text-sm text-slate-400 py-8 text-center">กำลังโหลด…</p>';
  modal.style.display = 'flex';
  let d;
  try {
    const r = await fetch(`${API}/api/analysis/hour_detail?${p}`);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    d = await r.json();
  } catch(e) { body.innerHTML = `<p class="text-sm text-red-500 py-6 text-center">โหลดไม่ได้: ${esc(String(e.message||e))}</p>`; return; }
  const days = d.total_days || 1;
  const dowTh = ['อา','จ','อ','พ','พฤ','ศ','ส'];
  const dowMap = {}; (d.by_dow||[]).forEach(r => dowMap[r.dow] = r);
  const dowMax = Math.max(1, ...(d.by_dow||[]).map(r => r.revenue||0));
  const branchMax = Math.max(1, ...(d.by_branch||[]).map(r => r.revenue||0));
  body.innerHTML = `
    <div class="grid grid-cols-3 gap-3 mb-4">
      <div class="p-3 rounded-lg bg-slate-50 text-center"><p class="text-xs text-slate-500">รายได้รวม (ชม.นี้)</p><p class="text-lg font-bold text-indigo-700">${fmt(d.grand_revenue||0)}</p></div>
      <div class="p-3 rounded-lg bg-slate-50 text-center"><p class="text-xs text-slate-500">เฉลี่ย/วัน</p><p class="text-lg font-bold text-emerald-600">${fmt((d.grand_revenue||0)/days)}</p></div>
      <div class="p-3 rounded-lg bg-slate-50 text-center"><p class="text-xs text-slate-500">จำนวนวันที่มีชม.นี้</p><p class="text-lg font-bold text-slate-700">${fmtN(days)}</p></div>
    </div>
    <h4 class="text-sm font-bold text-slate-600 mb-2">รายได้ชั่วโมงนี้ · รายสาขา</h4>
    <div class="overflow-auto" style="max-height:340px"><table class="w-full text-xs">
      <thead class="sticky top-0 bg-white"><tr class="text-left text-slate-500 border-b border-slate-200">
        <th class="py-2 pr-3">สาขา</th><th class="py-2 pr-3 text-right">รายได้รวม</th>
        <th class="py-2 pr-3 text-right">เฉลี่ย/วัน</th><th class="py-2 pr-3 text-right">บิล</th><th class="py-2 text-right">ลูกค้า</th></tr></thead>
      <tbody>${(d.by_branch||[]).map(r => `<tr class="border-b border-slate-50">
        <td class="py-1.5 pr-3 font-medium">${esc(r.branch)}<div style="height:3px;background:var(--accent);border-radius:2px;margin-top:2px;width:${Math.round((r.revenue||0)/branchMax*100)}%"></div></td>
        <td class="py-1.5 pr-3 text-right">${fmtN(r.revenue||0)}</td>
        <td class="py-1.5 pr-3 text-right text-slate-500">${fmtN(Math.round((r.revenue||0)/days))}</td>
        <td class="py-1.5 pr-3 text-right">${fmtN(r.tx_count||0)}</td>
        <td class="py-1.5 text-right">${fmtN(r.customer_count||0)}</td></tr>`).join('') || '<tr><td colspan="5" class="py-4 text-center text-slate-400">—</td></tr>'}</tbody>
    </table></div>
    <h4 class="text-sm font-bold text-slate-600 mb-2 mt-4">รายได้ชั่วโมงนี้ · รายวันในสัปดาห์</h4>
    <div class="grid grid-cols-7 gap-1.5">${[1,2,3,4,5,6,0].map(dw => { const r = dowMap[dw]||{revenue:0,tx_count:0}; const h = Math.round((r.revenue||0)/dowMax*60); return `
      <div class="text-center">
        <div style="height:64px;display:flex;align-items:flex-end;justify-content:center"><div style="width:60%;height:${Math.max(2,h)}px;background:${DOW_COLORS?.[dw]||'#4a70b0'};border-radius:3px 3px 0 0"></div></div>
        <p class="text-[10px] text-slate-500 mt-1">${dowTh[dw]}</p>
        <p class="text-[10px] font-semibold">${fmt(r.revenue||0)}</p>
      </div>`; }).join('')}</div>`;
}

async function loadTimeflow() {
  const _fk = _filterKey() + '|' + _timeflowMetric; if (_tabLastKey.timeflow === _fk) return; _tabLastKey.timeflow = _fk;
  const params = new URLSearchParams({ metric: _timeflowMetric });
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to', getMonthTo());

  const bpParams = new URLSearchParams({ metric: _timeflowMetric });
  if (getMonthFrom()) bpParams.set('month_from', getMonthFrom());
  if (getMonthTo())   bpParams.set('month_to',   getMonthTo());

  const [grid, hourly, dow, branchPeak] = await Promise.all([
    api(`${API}/api/analysis/daypart_grid?${params}`),
    api(`${API}/api/analysis/hourly_open${qp()}`).catch(() => []),
    api(`${API}/api/analysis/dow${qp()}`).catch(() => []),
    api(`${API}/api/analysis/branch_peak?${bpParams}`).catch(() => ({ branches: [] })),
  ]);

  _timeflowData = grid;

  // Derive open hours range only if the DB has actual closed-hours config
  // (if every hour is marked open, no config exists → keep JS defaults 10/22)
  const hasClosedConfig = (hourly || []).some(h => h.is_open === false);
  if (hasClosedConfig) {
    const openHrs = (hourly || []).filter(h => h.is_open !== false).map(h => h.hour);
    if (openHrs.length) { _tfOpenFrom = Math.min(...openHrs); _tfOpenTo = Math.max(...openHrs); }
  }

  if (grid.peak) {
    const peakLabel = `พีค: ${DOW_LABELS_TH[grid.peak.dow]} ${grid.peak.hour}:00`;
    const el = document.getElementById('tf-peak-label');
    if (el) el.textContent = peakLabel;
  }

  renderTimeflowGrid();

  // Hourly bar chart — open hours only (avg/day or total via toggle)
  const hrs = (hourly || []).filter(h => h.is_open !== false);
  _tfHourly = hrs;
  renderTfHourlyChart();

  // DOW chart — Mon-first, per-day colours
  const _tfDowOrder = [1,2,3,4,5,6,0];
  const _tfDowSorted = _tfDowOrder.map(d => (dow||[]).find(r => r.dow === d) || { dow:d, label:'', revenue:0, avg_revenue:0, day_count:0 });
  const _tfDowMax = Math.max(..._tfDowSorted.map(r => r.avg_revenue ?? r.revenue ?? 0));
  const _tfDowColors = _tfDowSorted.map(r => DOW_COLORS[r.dow] || SEMANTIC.revenue);
  mkChart('chartTfDow', {
    type: 'bar',
    data: {
      labels: ['จ','อ','พ','พฤ','ศ','ส','อา'],
      datasets: [{ label: 'รายได้เฉลี่ย/วัน', data: _tfDowSorted.map(d => d.avg_revenue ?? d.revenue ?? 0), backgroundColor: _tfDowColors, borderRadius: 4 }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, datalabels: { display: false },
        tooltip: { callbacks: { label: (ctx) => { const d = _tfDowSorted[ctx.dataIndex]; const dayTh = DOW_LABELS_TH[d?.dow] || ''; return ` ${fmtN(ctx.raw)} เฉลี่ย · ${d?.day_count??0} วัน${dayTh}ในช่วงนี้`; } } } },
      scales: { y: { beginAtZero: true, ticks: { callback: v => fmt(v) } } }
    }
  });

  // Peak summary — richer cards, clickable to drill into the hour
  const dayTotalRev = hrs.reduce((s, h) => s + (h.avg_daily_revenue ?? h.revenue ?? 0), 0) || 1;
  const topHours = [...hrs].sort((a, b) => (b.avg_daily_revenue ?? b.revenue) - (a.avg_daily_revenue ?? a.revenue)).slice(0, 4);
  document.getElementById('tfPeakSummary').innerHTML = topHours.map((h, i) => {
    const rev = h.avg_daily_revenue ?? h.revenue ?? 0;
    const share = rev / dayTotalRev * 100;
    const aov = (h.tx_count ? (h.revenue / h.tx_count) : 0);
    const custDay = h.total_days ? Math.round((h.customer_count || 0) / h.total_days) : (h.customer_count || 0);
    return `
    <div class="card p-3 cursor-pointer hover:ring-2 hover:ring-indigo-200 transition" onclick="openHourDetail(${h.hour})" title="คลิกดูรายละเอียดชั่วโมง ${h.hour}:00">
      <div class="flex items-center justify-between">
        <p class="text-xs text-slate-500">Peak #${i+1}</p>
        <span class="text-[10px] px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-600">${share.toFixed(0)}% ของวัน</span>
      </div>
      <p class="text-xl font-bold" style="color:var(--indigo)">${h.hour}:00–${(h.hour+1)%24}:00</p>
      <p class="text-sm font-semibold">${fmt(rev)}<span class="font-normal text-slate-400 text-xs">/วัน</span></p>
      <div class="text-[11px] text-slate-400 mt-1 space-y-0.5">
        <div>${fmtN(Math.round(h.avg_daily_tx ?? h.tx_count))} บิล/วัน · ${fmtN(custDay)} ลูกค้า/วัน</div>
        <div>AOV ${fmtN(Math.round(aov))} ฿</div>
      </div>
    </div>`;
  }).join('');
  // ── Per-branch peak cards
  _tfBranchPeakData = branchPeak.branches || [];
  renderTfBranchCards();

  loadTableTurnover();
}

let _tfBranchPeakData = [];
let _tfDrillBranch = null;
const _DOW_TH = ['อา','จ','อ','พ','พฤ','ศ','ส'];

function renderTfBranchCards() {
  const el = document.getElementById('tfBranchCards');
  if (!el) return;
  const metricLabel = { revenue:'ยอดขาย', tx_count:'จำนวนบิล', customer_count:'จำนวนลูกค้า', avg_ticket:'ยอดเฉลี่ยต่อบิล' }[_timeflowMetric] || 'ยอดขาย';
  el.innerHTML = _tfBranchPeakData.map(b => {
    const isActive = _tfDrillBranch === b.branch;
    const peakH = b.peak_hour != null ? `${b.peak_hour}:00` : '—';
    const peakD = b.peak_dow != null ? _DOW_TH[b.peak_dow] : '—';
    const maxV  = b.max_val || 1;
    // Mini sparkline: hourly bars (open hours 10-22 typically)
    const hSlice = b.hourly.slice(10, 23);
    const hMax   = Math.max(...hSlice, 1);
    const bars   = hSlice.map((v, i) => {
      const pct = Math.round(v / hMax * 100);
      const isPeak = (i + 10) === b.peak_hour;
      return `<div title="${i+10}:00 ${fmtN(v)}" style="flex:1;height:${Math.max(2, pct * 0.32)}px;background:${isPeak ? '#4a70b0' : '#c9d5e8'};border-radius:1px;align-self:flex-end"></div>`;
    }).join('');
    return `<div onclick="tfDrillBranch('${jsAttr(b.branch)}', this)"
      class="rounded-xl p-3 cursor-pointer transition-all border"
      style="background:${isActive ? 'var(--accent-soft)' : 'var(--bg-card)'};border-color:${isActive ? '#4a70b0' : 'var(--border)'}">
      <p class="text-[11px] font-semibold truncate mb-1" style="color:${isActive?'#4a70b0':'var(--text-primary)'}">${esc(b.branch)}</p>
      <p class="text-xs" style="color:var(--text-muted)">พีค <span class="font-bold" style="color:var(--accent)">${peakH}</span> · <span class="font-bold" style="color:var(--cyan-ink)">${peakD}</span></p>
      <div class="flex gap-px items-end mt-2" style="height:20px">${bars}</div>
    </div>`;
  }).join('');
}

async function tfDrillBranch(branchName, cardEl) {
  if (_tfDrillBranch === branchName) { tfClearDrill(); return; }
  _tfDrillBranch = branchName;
  renderTfBranchCards();

  const drill = document.getElementById('tfBranchDrill');
  if (drill) drill.classList.remove('hidden');
  const title = document.getElementById('tfDrillTitle');
  if (title) title.textContent = branchName;

  const b = _tfBranchPeakData.find(x => x.branch === branchName);
  if (!b) return;

  // Hourly chart (open range 10-22)
  const hours = Array.from({length:13}, (_,i) => i+10);
  const hourVals = hours.map(h => b.hourly[h] || 0);
  const peakHour = b.peak_hour;
  mkChart('chartDrillHourly', {
    type: 'bar',
    data: {
      labels: hours.map(h => `${h}:00`),
      datasets: [{ label: 'Avg/day', data: hourVals,
        backgroundColor: hours.map(h => h === peakHour ? '#4a70b0' : '#c9d5e8'),
        borderRadius: 3 }]
    },
    options: { responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{display:false}, datalabels:{display:false} },
      scales:{ y:{ beginAtZero:true, ticks:{callback:v=>fmt(v)} } } }
  });

  // DOW chart Mon-first
  const dowOrder = [1,2,3,4,5,6,0];
  const dowVals = dowOrder.map(d => b.dow[d] || 0);
  const peakDow = b.peak_dow;
  mkChart('chartDrillDow', {
    type: 'bar',
    data: {
      labels: ['จ','อ','พ','พฤ','ศ','ส','อา'],
      datasets: [{ label: 'Avg/day', data: dowVals,
        backgroundColor: dowOrder.map(d => d === peakDow ? '#0891b2' : '#a5f3fc'),
        borderRadius: 3 }]
    },
    options: { responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{display:false}, datalabels:{display:false} },
      scales:{ y:{ beginAtZero:true, ticks:{callback:v=>fmt(v)} } } }
  });

  // Turnover — fetch per branch
  const params = new URLSearchParams({ branch: branchName });
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to',   getMonthTo());
  const tv = await api(`${API}/api/analysis/table-turnover?view=branches&${params}`).catch(()=>null);
  const tvEl = document.getElementById('tfDrillTurnover');
  if (tvEl) {
    const br = tv && tv.branches && tv.branches.find(x => x.branch === branchName);
    tvEl.innerHTML = br
      ? `<div class="flex gap-4 flex-wrap">
           <span class="px-3 py-1.5 rounded-lg text-xs" style="background:#f0fdf4;color:#047857">Turn time ≈ <b>${br.avg_turn_min ?? '—'} min</b></span>
           <span class="px-3 py-1.5 rounded-lg text-xs" style="background:var(--accent-soft);color:var(--accent)">รอบโต๊ะ <b>${br.turns?.toLocaleString()||'—'}</b> ครั้ง</span>
           <span class="px-3 py-1.5 rounded-lg text-xs" style="background:#fffbeb;color:#a44a08">เฉลี่ย <b>${br.avg_covers_per_turn ?? '—'}</b> คน/รอบ</span>
         </div>`
      : '<p style="color:var(--text-muted)">ไม่มีข้อมูลหมุนเวียนโต๊ะสำหรับสาขานี้</p>';
  }
}

function tfClearDrill() {
  _tfDrillBranch = null;
  renderTfBranchCards();
  const drill = document.getElementById('tfBranchDrill');
  if (drill) drill.classList.add('hidden');
}

async function loadTableTurnover() {
  const params = new URLSearchParams();
  if (getBranch())    params.set('branch', getBranch());
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to',   getMonthTo());
  // view=dwell needs sales_transaction.table_no, which the MSSQL sync leaves
  // empty — view=branches reads the real POS turn-time feed (pos_table_turn_day)
  // instead, same source the More tab already relies on.
  const d = await api(`${API}/api/analysis/table-turnover?view=branches&${params}`).catch(() => null);
  if (!d || !d.branches || !d.branches.length) {
    document.getElementById('tableTurnoverKpis').innerHTML = '<p class="text-xs text-slate-400 col-span-4">ไม่มีข้อมูลหมุนเวียนโต๊ะสำหรับช่วงที่เลือก</p>';
    return;
  }
  const byBranch = d.branches;
  const validTurn = byBranch.filter(b => b.avg_turn_min != null);
  const avgTurnMin = validTurn.length ? validTurn.reduce((s, b) => s + b.avg_turn_min, 0) / validTurn.length : null;
  const totalTurns = byBranch.reduce((s, b) => s + (b.turns || 0), 0);
  const fastest = validTurn.slice().sort((a, b) => a.avg_turn_min - b.avg_turn_min)[0];
  document.getElementById('tableTurnoverKpis').innerHTML = [
    { label: 'Avg Turn Time', value: avgTurnMin != null ? `${avgTurnMin.toFixed(0)} min` : '—', sub: 'network avg, POS-measured', color: '#0891b2' },
    { label: 'Total Table Occasions', value: totalTurns.toLocaleString(), sub: 'across selected branches', color: '#4a70b0' },
    { label: 'Fastest Turnover', value: fastest?.branch || '—', sub: fastest ? `${fastest.avg_turn_min} min/turn` : '—', color: 'var(--good-ink)' },
    { label: 'Branches Tracked', value: byBranch.length, sub: 'with table data', color: '#64748b' },
  ].map(k => `<div class="card p-3 text-center"><p class="text-xs text-slate-500">${k.label}</p><p class="text-base font-bold" style="color:${textInk(k.color)}">${k.value}</p><p class="text-xs text-slate-400 mt-0.5">${k.sub}</p></div>`).join('');

  mkChart('chartTableTurnover', {
    type: 'bar',
    data: {
      labels: byBranch.map(b => b.branch),
      datasets: [
        { label: 'Table Occasions', data: byBranch.map(b => b.turns), backgroundColor: PALETTE_6[0] + 'cc', borderRadius: 4, yAxisID: 'turns' },
        { label: 'เวลานั่งเฉลี่ย (นาที)', data: byBranch.map(b => b.avg_turn_min), type: 'line', borderColor: PALETTE_6[1], backgroundColor: 'transparent', tension: 0.3, pointRadius: 4, yAxisID: 'dwell' },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom', labels: { font: { size: 10 } } }, datalabels: { display: false } },
      scales: {
        turns: { type: 'linear', position: 'left',  title: { display: true, text: 'Table occasions' } },
        dwell: { type: 'linear', position: 'right', title: { display: true, text: 'Min' },
                 grid: { drawOnChartArea: false } }
      }
    }
  });
}

function renderTimeflowGrid() {
  const DOW_LABELS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  const gridEl = document.getElementById('timeflowGrid');
  if (!gridEl || !_timeflowData.cells) return;

  const cells = _timeflowData.cells || [];
  const maxVal = _timeflowData.max_value || Math.max(...cells.map(c => c.value || 0), 1);
  const isMoney = _timeflowMetric === 'revenue' || _timeflowMetric === 'avg_ticket';

  const cellMap = {};
  cells.forEach(c => cellMap[`${c.dow}_${c.hour}`] = c.value || 0);

  // Show only open hours
  const openHours = [];
  for (let h = _tfOpenFrom; h <= _tfOpenTo; h++) openHours.push(h);

  const fmtCell = v => {
    if (!v) return '';
    if (isMoney) return v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1000 ? Math.round(v/1000)+'k' : Math.round(v);
    return v >= 1000 ? (v/1000).toFixed(1)+'k' : Math.round(v);
  };

  // One navy scale, darker = busier (a quiet hour is not "bad"); the label takes white / navy
  const cellColor = intensity => {
    if (!intensity || intensity < 0.01) return { bg: 'var(--bg-subtle)', fg: 'var(--text-muted)' };
    const bg = heatSeq(intensity); return { bg, fg: inkOn(bg) };
  };

  const cellW = Math.max(38, Math.floor((gridEl.offsetWidth - 50) / openHours.length));

  let html = `<table style="border-collapse:separate;border-spacing:3px 3px;font-size:11px;width:100%">
    <thead><tr>
      <th style="width:42px;text-align:left"></th>
      ${openHours.map(h => `<th style="text-align:center;font-size:10px;color:var(--text-muted);font-weight:600;padding-bottom:4px">${h}</th>`).join('')}
    </tr></thead>
    <tbody>`;

  let rankData = [];
  for (let dow = 0; dow < 7; dow++)
    for (const h of openHours)
      rankData.push({ dow, h, v: cellMap[`${dow}_${h}`] || 0 });
  rankData.sort((a,b) => b.v - a.v);
  const rankMap = {};
  rankData.forEach((r,i) => rankMap[`${r.dow}_${r.h}`] = i + 1);

  for (let dow = 0; dow < 7; dow++) {
    html += `<tr><td style="font-size:11px;color:var(--text-secondary);padding:2px 6px 2px 0;font-weight:600;white-space:nowrap">${DOW_LABELS[dow]}</td>`;
    for (const h of openHours) {
      const v = cellMap[`${dow}_${h}`] || 0;
      const { bg, fg } = cellColor(v / maxVal);
      const rank = rankMap[`${dow}_${h}`];
      html += `<td
        onclick="tfCellClick(${dow},${h},${v},${rank})"
        title="${DOW_LABELS[dow]} ${h}:00 · ${v > 0 ? (isMoney ? '' + v.toLocaleString() : v.toLocaleString()) : '—'} · Rank #${rank}"
        style="background:${bg};color:${fg};height:36px;min-width:${cellW}px;text-align:center;cursor:pointer;border-radius:var(--r-md);font-size:10px;font-weight:${v>0?600:400};transition:transform .12s;vertical-align:middle"
        onmouseover="this.style.transform='scale(1.1)';this.style.zIndex=10;this.style.position='relative'"
        onmouseout="this.style.transform='';this.style.zIndex='';this.style.position=''">
        ${fmtCell(v)}
      </td>`;
    }
    html += '</tr>';
  }

  html += `</tbody></table>
  <div style="display:flex;align-items:center;gap:6px;margin-top:10px;font-size:10px;color:var(--text-muted)">
    <span style="opacity:.7">น้อย</span>
    ${[0.1,0.3,0.5,0.7,0.9].map(t=>`<div style="width:28px;height:8px;background:${heatSeq(t)};border-radius:2px"></div>`).join('')}
    <span style="opacity:.7">มาก</span>
    <span style="margin-left:auto;opacity:.6">Open hours: ${_tfOpenFrom}:00–${_tfOpenTo}:00 · configurable in Settings → Hours</span>
  </div>`;

  gridEl.innerHTML = html;
}

function tfCellClick(dow, hour, value, rank) {
  const DOW_LABELS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  const isMoney = _timeflowMetric === 'revenue' || _timeflowMetric === 'avg_ticket';
  const metricName = { revenue:'ยอดขาย', tx_count:'บิล', customer_count:'ลูกค้า', avg_ticket:'ยอดเฉลี่ยต่อบิล' }[_timeflowMetric];
  const valFmt = isMoney ? '' + value.toLocaleString() : value.toLocaleString();
  const pctOfPeak = _timeflowData.max_value ? (value / _timeflowData.max_value * 100).toFixed(1) : '—';
  const totalCells = 7 * (_tfOpenTo - _tfOpenFrom + 1);

  // update detail panel
  const el = document.getElementById('tfCellDetail');
  if (el) {
    el.classList.remove('hidden');
    el.innerHTML = `
      <div class="flex items-start justify-between gap-4">
        <div>
          <p class="font-semibold text-slate-700">${DOW_LABELS[dow]} ${hour}:00</p>
          <p class="text-2xl font-bold mt-1" style="color:var(--indigo)">${valFmt}</p>
          <p class="text-xs text-slate-400 mt-0.5">${metricName}</p>
        </div>
        <div class="text-right">
          <p class="text-xs text-slate-500">อันดับ</p>
          <p class="text-xl font-bold text-amber-600">#${rank} <span class="text-xs font-normal text-slate-400">of ${totalCells}</span></p>
          <p class="text-xs text-slate-400 mt-0.5">${pctOfPeak}% of peak</p>
        </div>
      </div>`;
  }
}

function tfDiveIn() {
  const DOW_LABELS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  const cells = _timeflowData.cells || [];
  if (!cells.length) return;
  const isMoney = _timeflowMetric === 'revenue' || _timeflowMetric === 'avg_ticket';
  const fmtV = v => isMoney ? '' + Math.round(v).toLocaleString() : v.toLocaleString();
  const totalVal = cells.reduce((s,c) => s + (c.value||0), 0);

  // Top 5 cells
  const top5 = [...cells].sort((a,b)=>b.value-a.value).slice(0,5);
  // Revenue by DOW
  const byDow = Array.from({length:7}, (_,d) => ({ dow:d, v: cells.filter(c=>c.dow===d).reduce((s,c)=>s+(c.value||0),0) }));
  // Revenue by hour
  const openHours = [];
  for (let h = _tfOpenFrom; h <= _tfOpenTo; h++) openHours.push(h);
  const byHour = openHours.map(h => ({ h, v: cells.filter(c=>c.hour===h).reduce((s,c)=>s+(c.value||0),0) }));
  const peakHour = byHour.reduce((mx,x)=>x.v>mx.v?x:mx, {h:0,v:0});
  const peakDow  = byDow.reduce((mx,x)=>x.v>mx.v?x:mx, {dow:0,v:0});

  const el = document.getElementById('tfCellDetail');
  if (!el) return;
  el.classList.remove('hidden');
  el.innerHTML = `
    <div class="flex items-center justify-between mb-3">
      <p class="font-semibold text-slate-700">Heatmap Analysis</p>
      <button onclick="document.getElementById('tfCellDetail').classList.add('hidden')" class="text-slate-400 hover:text-slate-600 text-lg leading-none">✕</button>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
      <div class="bg-white p-3 rounded-lg text-center shadow-sm"><p class="text-xs text-slate-500">Peak Day</p><p class="text-base font-bold text-indigo-600">${DOW_LABELS[peakDow.dow]}</p><p class="text-xs text-slate-400">${fmtV(peakDow.v)}</p></div>
      <div class="bg-white p-3 rounded-lg text-center shadow-sm"><p class="text-xs text-slate-500">Peak Hour</p><p class="text-base font-bold text-indigo-600">${peakHour.h}:00</p><p class="text-xs text-slate-400">${fmtV(peakHour.v)}</p></div>
      <div class="bg-white p-3 rounded-lg text-center shadow-sm"><p class="text-xs text-slate-500">Top Cell</p><p class="text-base font-bold text-amber-600">${DOW_LABELS[top5[0]?.dow]} ${top5[0]?.hour}:00</p><p class="text-xs text-slate-400">${fmtV(top5[0]?.value||0)}</p></div>
      <div class="bg-white p-3 rounded-lg text-center shadow-sm"><p class="text-xs text-slate-500">Concentration</p><p class="text-base font-bold text-emerald-600">${totalVal>0?((top5[0]?.value||0)/totalVal*100).toFixed(1):0}%</p><p class="text-xs text-slate-400">in single slot</p></div>
    </div>
    <p class="text-xs font-semibold text-slate-500 mb-2">Top 5 Slots</p>
    <div class="space-y-1">
      ${top5.map((c,i)=>{
        const pct = totalVal > 0 ? (c.value/totalVal*100).toFixed(1) : 0;
        return `<div class="flex items-center gap-3 text-xs">
          <span class="text-slate-400 w-4">#${i+1}</span>
          <span class="font-medium text-slate-700 w-20">${DOW_LABELS[c.dow]} ${c.hour}:00</span>
          <div class="flex-1 bg-slate-100 rounded-full h-2"><div style="width:${pct}%;background:var(--accent)" class="h-2 rounded-full"></div></div>
          <span class="text-slate-600 font-semibold w-20 text-right">${fmtV(c.value)}</span>
          <span class="text-slate-400 w-10 text-right">${pct}%</span>
        </div>`;
      }).join('')}
    </div>`;
}
