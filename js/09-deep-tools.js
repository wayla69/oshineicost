// ─── 09-deep-tools.js — slice 9/9 of the former app.js (P2 structure refactor) ───
// Sequential load order matters: index.html loads 01→09; classic scripts
// share one global scope, so this equals the original single file.

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

  const d = await fetch(`${API}/api/menu/engineering?${params}`).then(r => r.json());
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
    { key: 'star',      label: '⭐ Stars',      color: '#10b981', bg: '#f0fdf4', advice: 'Maintain & promote' },
    { key: 'plowhorse', label: '🐴 Plowhorses', color: '#f59e0b', bg: '#fffbeb', advice: 'Reprice or reduce cost' },
    { key: 'puzzle',    label: '❓ Puzzles',    color: '#6366f1', bg: '#eef2ff', advice: 'Reposition or bundle' },
    { key: 'dog',       label: '🐕 Dogs',       color: '#ef4444', bg: '#fef2f2', advice: 'Review or discontinue' },
  ];
  const costedItems = _engData.filter(i => i.has_cost);
  document.getElementById('engSummaryCards').innerHTML = QUADS.map(q => {
    const items = costedItems.filter(i => i.quadrant === q.key);
    const rev = items.reduce((s, i) => s + (i.revenue || 0), 0);
    return `<div style="background:${q.bg};border-radius:8px;padding:10px 12px">
      <div class="flex justify-between items-center">
        <span style="font-weight:600;color:${q.color}">${q.label}</span>
        <span style="font-size:18px;font-weight:700;color:${q.color}">${items.length}</span>
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
    { key: 'star',      label: '⭐ Stars',      color: '#10b981' },
    { key: 'plowhorse', label: '🐴 Plowhorses', color: '#f59e0b' },
    { key: 'puzzle',    label: '❓ Puzzles',    color: '#6366f1' },
    { key: 'dog',       label: '🐕 Dogs',       color: '#ef4444' },
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
    star: '⭐ ดาวเด่น', plowhorse: '🐴 ม้างาน', puzzle: '🔮 เพชรในตม', dog: '🐕 ตัวถ่วง', null: '—', undefined: '—'
  };
  const QUAD_COLORS = { star: '#059669', plowhorse: '#d97706', puzzle: '#4f46e5', dog: '#dc2626' };
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
          <span style="font-weight:600;color:${QUAD_COLORS[i.quadrant]||'#94a3b8'}">${QUAD_BADGE[i.quadrant] || (i.has_cost ? '—' : 'ไม่มีต้นทุน')}</span>
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
  title.textContent = '🧾 เมนูที่สั่ง · ' + (row.receipt || '');
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
      ['ส่วนลดบิล', disc ? `<span style="color:#f59e0b">−${fmt(disc)}</span>` : '—'],
      ['รายได้สุทธิ', `<span style="color:#4f46e5">${fmt(net)}</span>`],
      ['COGS (BoM)', fmt(cogs)],
      [gp >= 0 ? 'กำไรขั้นต้น' : 'ขาดทุนสุทธิ',
       `<span style="color:${gp >= 0 ? '#10b981' : '#ef4444'}">${fmt(Math.abs(gp))}</span>`],
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
      <td class="py-1.5 text-right tabular-nums font-semibold" style="color:${mCol}">${fmt(it.margin)}</td></tr>`;
  }).join('');
  const tot = items.reduce((a, it) => ({ rev: a.rev + it.rev, cogs: a.cogs + it.cogs, margin: a.margin + it.margin }), { rev: 0, cogs: 0, margin: 0 });
  body.innerHTML = `<table class="w-full text-xs"><thead><tr class="text-left text-slate-500 border-b border-slate-200">
      <th class="py-2 pr-3">เมนู</th><th class="py-2 pr-3">SKU</th>
      <th class="py-2 pr-3 text-right">จำนวน</th><th class="py-2 pr-3 text-right">@BoM (ทุน/หน่วย)</th>
      <th class="py-2 pr-3 text-right">รายได้</th>
      <th class="py-2 pr-3 text-right">COGS (BoM)</th><th class="py-2 text-right">กำไรขั้นต้น</th></tr></thead>
    <tbody>${rows}</tbody>
    <tfoot><tr class="border-t-2 border-slate-200 font-bold">
      <td class="py-2 pr-3" colspan="4">รวม ${fmtN(items.length)} เมนู</td>
      <td class="py-2 pr-3 text-right tabular-nums">${fmt(tot.rev)}</td>
      <td class="py-2 pr-3 text-right tabular-nums">${fmt(tot.cogs)}</td>
      <td class="py-2 text-right tabular-nums" style="color:${tot.margin < 0 ? '#ef4444' : '#10b981'}">${fmt(tot.margin)}</td></tr></tfoot></table>
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
  title.textContent = '🔍 ' + (opts.title || (cashier ? `บิลส่วนลด · ${cashier}` : 'บิลส่วนลด'));
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
    ? `<span class="ml-2 text-xs font-bold text-orange-600 bg-orange-50 border border-orange-200 rounded px-2 py-0.5">🖨️ Reprint ${fmtN(s.reprinted_bills)} บิล</span>` : '';
  const payFilter = codes.length ? `<div class="flex items-center gap-2 mb-2 flex-wrap">
      <span class="text-xs font-medium text-slate-500">💳 วิธีจ่าย:</span>
      <select onchange="discBillsSetPayment(this.value)" class="text-xs border border-slate-300 rounded-lg px-2 py-1" style="max-width:220px">
        <option value="">ทั้งหมด (${fmtN(codes.length)} วิธี)</option>
        ${codes.map(c=>`<option value="${esc(c)}" ${opts.payment===c?'selected':''}>${esc(c)}</option>`).join('')}
      </select>
      ${opts.payment?`<button onclick="discBillsSetPayment('')" class="text-xs text-red-600 underline">ล้างตัวกรอง</button>`:''}
      <button onclick="openDiscountBills({..._discBillsCtx, reprinted_only:${!opts.reprinted_only}, sort:'reprints', title:'บิลที่พิมพ์ซ้ำ + มีส่วนลด'})"
        class="text-xs px-2 py-1 rounded-lg border ${opts.reprinted_only?'bg-orange-100 border-orange-400 text-orange-700 font-bold':'border-slate-300 text-slate-500 hover:bg-slate-50'}">
        🖨️ ${opts.reprinted_only?'ดูทั้งหมด':'เฉพาะ Reprint'}
      </button>
    </div>` : '';
  const curSort = opts.sort || 'discount';
  const sortBar = `<div class="flex items-center gap-1 mb-2 flex-wrap">
    <span class="text-xs text-slate-400 mr-1">เรียงโดย:</span>
    ${[['date','📅 วันที่'],['discount','฿ ส่วนลด'],['pct','% ของบิล'],['total','ยอดบิล']].map(([k,lbl])=>
      `<button onclick="openDiscountBills({..._discBillsCtx, sort:'${k}'})"
        class="text-xs px-2.5 py-1 rounded-lg border ${curSort===k?'font-bold text-white':'text-slate-600 hover:bg-slate-50'}"
        style="${curSort===k?'background:var(--color-primary);border-color:var(--color-primary)':'border-color:#e2e8f0'}">${lbl}</button>`
    ).join('')}
  </div>`;
  sumEl.innerHTML = payFilter + sortBar + `<div class="grid grid-cols-2 md:grid-cols-5 gap-3 mb-1">
    ${[['บิลมีส่วนลด', fmtN(s.bills||0)],['ส่วนลดรวม', fmt(s.total_discount||0)],['เฉลี่ย/บิล', fmt(s.avg_discount||0)],['สูงสุด', fmt(s.max_discount||0)],['พิมพ์ซ้ำ (>1x)', `<span class="${(s.reprinted_bills||0)>0?'text-orange-600 font-bold':''}">${fmtN(s.reprinted_bills||0)}</span>`]]
      .map(([k,v])=>`<div class="p-3 rounded-lg bg-slate-50 text-center"><p class="text-xs text-slate-500">${k}</p><p class="text-lg font-bold text-slate-800">${v}</p></div>`).join('')}
  </div>
  <p class="text-xs text-slate-400 mb-2">ไฮไลต์แดง = ส่วนลดเกิน p95 (${fmt(s.p95_discount||0)}) · ไฮไลต์ส้ม = Reprint · แสดง ${fmtN(s.returned||0)} บิลแรก${opts.payment?` · กรองเฉพาะ <b style="color:#dc2626">${esc(opts.payment)}</b>`:''}${opts.reprinted_only?' · <b style="color:#ea580c">เฉพาะ Reprint</b>':''}</p>`;
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
    <th class="py-2 text-right" title="จำนวนครั้งที่พิมพ์ใบเสร็จ (Easy POS PrintTimes)">🖨️ พิมพ์</th>
    </tr></thead><tbody>`;
  const rows = bills.map(b => {
    const hot = (b.discount||0) >= p95 && p95 > 0;
    const base = b.bill_base != null ? b.bill_base : (b.total_inc_vat||0) + (b.discount||0);
    const dtype = b.coupon_name
      ? `<span class="text-indigo-600">🎟️ ${esc(b.coupon_name)}</span>`
      : (b.reason_desc ? esc(b.reason_desc)
        : '<span class="text-slate-400">ไม่ระบุประเภท</span>');
    const payCell = b.payment_code
      ? `${esc(b.payment_code)}${(b.payment_n||0) > 1 ? ` <span class="text-[9px] text-slate-400" title="จ่ายหลายแบบในบิลเดียว">+${b.payment_n-1}</span>` : ''}`
      : '<span class="text-slate-300">—</span>';
    const pt = b.print_times || 0;
    const printCell = pt > 1
      ? `<span class="font-bold text-red-600" title="พิมพ์ซ้ำ ${pt} ครั้ง — ตรวจสอบ">${pt}x 🚩</span>`
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
      return `<span class="font-semibold" style="color:${col}" title="รายได้สุทธิ ${fmtN(net)} − ทุน BoM ${fmtN(Math.round(b.cogs_bom))}">${pl < 0 ? '−' : '+'}${fmtN(Math.abs(pl))}</span>`;
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
    const db = await fetch(`${API}/api/analysis/discount_bills?${bp}`).then(r => r.json()).catch(() => null);
    const el = document.getElementById('staff-abnormal-bills');
    if (!db || !db.bills || !db.bills.length) {
      el.innerHTML = '<p class="text-sm text-slate-400 py-4 text-center">ไม่มีบิลส่วนลดในช่วงนี้</p>'; return;
    }
    el.innerHTML = renderDiscBillsTable(db.bills, db.summary?.p95_discount || 0, true, !getBranch());
  })();

  const d = await fetch(`${API}/api/analysis/cashiers?${cashierParams}`).then(r => r.json()).catch(() => []);

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
    { label: 'Total Staff Revenue', value: `${fmt(totalRev)}`, sub: _staffByBranch ? `${d.length} บัญชี×สาขา` : `${d.length} บัญชีแคชเชียร์`, color: '#4f46e5' },
    { label: 'Top Cashier', value: top.cashier || '—', sub: `${fmt(top.revenue || 0)}`, color: '#059669' },
    { label: 'Network Avg Ticket', value: `${fmt(d.length ? d.reduce((s,r)=>s+(r.avg_ticket||0),0)/d.length : 0)}`, sub: 'ต่อใบเสร็จ', color: '#0891b2' },
    { label: 'Network Discount Rate', value: `${avgDisc.toFixed(1)}%`, sub: 'of transactions with discount', color: avgDisc > 15 ? '#ef4444' : '#f59e0b' },
  ].map(k => `<div class="card p-4"><p class="kpi-label">${k.label}</p><p class="kpi-value" style="color:${k.color}">${k.value}</p><p class="text-xs mt-1" style="color:var(--text-muted)">${k.sub}</p></div>`).join('');

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
      datasets: [{ label: 'Discount Rate %', data: top10.map(r => r.discount_rate || 0),
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
      <td class="py-2 pr-3 font-medium text-indigo-700">${esc(r.cashier)} 🔍</td>
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
  const d = await fetch(`${API}/api/buffet/child${getBranch()?'?branch='+encodeURIComponent(getBranch()):''}`).then(r => r.json()).catch(() => null);
  if (!d || !d.by_branch) return;
  const bb = d.by_branch;
  const momChip = p => p == null ? '<span class="text-slate-300 text-xs">—</span>'
    : `<span class="text-xs font-semibold" style="color:${p>=0?'#059669':'#dc2626'}">${p>=0?'▲':'▼'} ${Math.abs(p).toFixed(1)}%</span>`;

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
      <p class="text-2xl font-bold" style="color:${accent}">${value}</p>
      <p class="text-[11px] text-slate-400">${sub||''}</p></div>`;
  document.getElementById('childKpiRow').innerHTML =
    kpi('covers เด็ก (เดือนล่าสุด)', fmtN(totCovers), momChip(momCovers), '#ec4899', `${d.latest_month||''} · เทียบ ${d.prev_month||'—'}`) +
    kpi('รายได้บุฟเฟ่ต์เด็ก', fmt(totRev), momChip(momRev), '#8b5cf6', 'เดือนล่าสุด (ex-VAT)') +
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
        { label: 'covers เด็ก', data: ytd.map(t => t.covers), borderColor: '#ec4899',
          backgroundColor: 'rgba(236,72,153,0.10)', borderWidth: 2.5, pointRadius: 3, fill: true, tension: 0.3, yAxisID: 'y' },
        { label: 'รายได้', data: ytd.map(t => t.revenue), borderColor: '#8b5cf6',
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
    const c = (b.mom_pct == null) ? '#94a3b8' : b.mom_pct >= 0 ? '#059669' : '#dc2626';
    return `<div class="rounded-xl border p-3" style="border-color:#ec489933;background:#fdf2f80a">
      <p class="text-sm font-semibold text-slate-700 truncate" title="${esc(b.branch)}">${esc(b.branch)}</p>
      <p class="text-2xl font-extrabold mt-1 leading-none" style="color:#db2777">${fmtN(b.covers)}</p>
      <p class="text-[11px] text-slate-400 mt-0.5">covers เด็ก · ${b.pct_of_buffet != null ? b.pct_of_buffet.toFixed(1) + '% ของบุฟเฟ่ต์' : '—'}</p>
      <p class="text-xs font-semibold mt-1" style="color:${c}">${b.mom_pct == null ? '<span class="text-slate-400">ไม่มีเดือนก่อน</span>' : (b.mom_pct >= 0 ? '▲' : '▼') + ' ' + Math.abs(b.mom_pct).toFixed(1) + '% MoM'}</p>
      <p class="text-[11px] text-slate-400 mt-0.5">รายได้ ${fmt(b.revenue)}</p>
    </div>`;
  }).join('');
}

let DISC_REASON_DATA = [];
function exportDiscountReasons() {
  const s = DISC_REASON_DATA || [];
  if (!s.length) return alert('ไม่มีข้อมูลให้ส่งออก');
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
  let d; try { d = await fetch(`${API}/api/analysis/discount-reasons?${params}`).then(r => r.json()); }
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
    { l: '⚠️ ไม่ระบุเหตุผล', v: (d.untyped_pct != null ? d.untyped_pct + '%' : '—'), s: untyped ? `${fmt(untyped.amount)} · ${fmtN(untyped.bills)} บิล` : 'ไม่มี', c: '#f59e0b' },
    { l: '✓ ระบุเหตุผล', v: (d.total_amount ? (100 - (d.untyped_pct || 0)).toFixed(1) + '%' : '—'), s: fmt(typedAmt), c: '#10b981' },
  ].map(k => `<div class="ms-kpi" style="border-left:4px solid ${k.c}"><p class="text-[11px]" style="color:var(--text-muted)">${k.l}</p><p class="text-2xl font-bold" style="color:${k.c}">${k.v}</p><p class="text-[11px]" style="color:var(--text-muted)">${k.s}</p></div>`).join('');
  // typed-vs-untyped split bar — instant read of the policy gap
  const splitEl = document.getElementById('disc-reason-split');
  if (splitEl) {
    const up = d.untyped_pct || 0, tp = Math.max(0, 100 - up);
    splitEl.innerHTML = (d.total_amount ? `
      <div class="flex items-center justify-between text-[11px] mb-1">
        <span style="color:#f59e0b;font-weight:700">⚠️ ไม่ระบุเหตุผล ${up.toFixed(0)}%</span>
        <span style="color:#10b981;font-weight:700">ระบุเหตุผล ${tp.toFixed(0)}% ✓</span>
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
      <td class="py-1.5 pr-2 font-medium" style="${unty ? 'color:#f59e0b' : ''}">${unty ? '⚠️ ' : ''}${esc(r.reason)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${fmtN(r.bills)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:#ef4444">${fmt(r.amount)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${r.pct}%</td>
      <td class="py-1.5 pr-2" style="min-width:90px"><div style="background:var(--bg-subtle);border-radius:3px;overflow:hidden"><div style="width:${w}%;min-width:2px;height:8px;background:${unty ? '#f59e0b' : '#6366f1'}"></div></div></td>
    </tr>`;
  }).join('') || '<tr><td colspan="5" class="py-3 text-center text-slate-400">ไม่มีส่วนลดในช่วงนี้</td></tr>';
}

async function loadCouponSeries(params, gen) {
  const body = document.getElementById('coupon-series-body');
  const cards = document.getElementById('coupon-series-cards');
  const note = document.getElementById('coupon-series-note');
  if (!body) return;
  body.innerHTML = '<tr><td colspan="9" class="py-3 text-center text-slate-400">กำลังโหลด…</td></tr>';
  let d; try { d = await fetch(`${API}/api/analysis/coupon-series?${params}`).then(r => r.json()); }
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
    { l: 'จำนวนชุดที่ใช้', v: fmtN(d.series_count || 0), s2: 'series', c: '#6366f1' },
    { l: 'ชุดที่ลดมากสุด', v: top ? top.pct + '%' : '—', s2: top ? esc(top.name) : '—', c: '#ef4444' },
    { l: 'เฉลี่ย/ครั้ง', v: fmt(d.total_uses ? Math.round(d.total_amount / d.total_uses) : 0), s2: 'ต่อการใช้ 1 ครั้ง', c: '#10b981' },
  ].map(k => `<div class="ms-kpi" style="border-left:4px solid ${k.c}"><p class="text-[11px]" style="color:var(--text-muted)">${k.l}</p><p class="text-2xl font-bold" style="color:${k.c}">${k.v}</p><p class="text-[11px] truncate" style="color:var(--text-muted)">${k.s2}</p></div>`).join('');
  const maxAmt = Math.max(1, ...s.map(r => r.amount || 0));
  const momCell = v => v == null ? '<span class="text-slate-300">—</span>'
    : `<span style="color:${v === 0 ? '#94a3b8' : (v > 0 ? '#ef4444' : '#10b981')};font-weight:600">${v > 0 ? '▲' : v < 0 ? '▼' : ''} ${Math.abs(v).toFixed(1)}%</span>`;
  body.innerHTML = s.map((r, i) => {
    const w = Math.round(100 * (r.amount || 0) / maxAmt);
    const inactive = !r.active;
    return `<tr class="border-t border-slate-100 cursor-pointer hover:bg-amber-50/50" onclick="openCouponDrill(${r.id}, ${JSON.stringify(r.name).replace(/"/g, '&quot;')})">
      <td class="num tabular text-slate-400">${i + 1}</td>
      <td class="font-medium">${esc(r.name)}${inactive ? ' <span class="text-[10px] text-slate-400">(ปิดใช้)</span>' : ''}${r.code && r.code !== r.name ? `<div class="text-[10px] text-slate-400">${esc(r.code)}</div>` : ''}</td>
      <td class="num tabular text-slate-500">${fmtN(r.uses)}</td>
      <td class="num tabular font-semibold" style="color:#f59e0b">${fmt(r.amount)}</td>
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
  if (!s.length) return alert('ไม่มีข้อมูลให้ส่งออก');
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
  let d; try { d = await fetch(`${API}/api/analysis/coupon-series/drill?${qp}`).then(r => r.json()); }
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
      <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:#f59e0b">${fmt(r.amount)}</td>
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
  const d = await fetch(`${API}/api/analysis/discount_alerts?${params}`).then(r => r.json()).catch(() => null);
  if (gen != null && gen !== loadGen) return;
  if (!d) { el.innerHTML = '<p class="text-sm text-slate-400 py-3 text-center">โหลดไม่ได้</p>'; if (meta) meta.textContent = ''; return; }
  const flags = d.flags || [];
  if (meta) meta.textContent = flags.length ? `🔴 ${d.counts.high} ด่วน · 🟠 ${d.counts.med} เฝ้าระวัง` : '';
  // threshold config form (populated from the current/effective thresholds)
  const cfgEl = document.getElementById('disc-alert-config');
  if (cfgEl && d.thresholds) {
    const T = d.thresholds;
    const F = [['cash_pct','เงินสด+ลดหนัก ≥ (%)'],['cash_min','เตือนเมื่อรวม ≥ (฿)'],
               ['untyped_pct','ไม่ระบุเหตุผล ≥ (%)'],['cashier_floor','แคชเชียร์ลด ≥ (%)'],
               ['repeat_min','ลดซ้ำ ≥ (ครั้ง)'],['late_from','ลดหลังเวลา (ชม.)']];
    cfgEl.innerHTML = `<p class="text-xs font-semibold text-slate-600 mb-2">⚙ เกณฑ์เตือน (ปรับแล้วมีผลทุกสาขา)</p>
      <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
        ${F.map(([k,lbl])=>`<label class="text-[11px] text-slate-500">${lbl}<input id="th_${k}" type="number" step="any" value="${T[k]}" class="w-full text-xs border border-slate-300 rounded px-2 py-1 mt-0.5"></label>`).join('')}
      </div>
      <div class="flex items-center gap-2 mt-2">
        <button onclick="discAlertSaveThresholds()" class="text-xs text-white rounded px-3 py-1" style="background:#4f46e5">บันทึกเกณฑ์</button>
        <span id="th_saved" class="text-[11px] text-green-600"></span>
        <a href="/docs/POS_DISCOUNT_CONTROLS.html" target="_blank" class="text-[11px] text-indigo-600 underline" style="margin-left:auto">📋 แนวทางตั้งค่า POS เพื่อป้องกัน</a>
      </div>`;
  }
  if (!flags.length) {
    el.innerHTML = '<div class="px-3 py-2 rounded-lg text-sm" style="background:#f0fdf4;border-left:4px solid #22c55e;color:#166534">✓ ไม่พบสัญญาณส่วนลดผิดปกติในช่วงนี้</div>';
    return;
  }
  const ICON = { cash_heavy: '💵', untyped: '🏷️', cashier: '🧑‍💼', repeat: '🔁', late: '🌙', drift: '📈', void: '🚫' };
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
      ${f.drill ? '<span class="text-[10px] text-slate-300" style="margin-top:3px">🔍</span>' : ''}
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
  const d = await fetch(`${API}/api/analysis/payment_discount?${params}`).then(r => r.json()).catch(() => null);
  if (gen != null && gen !== loadGen) return;
  if (!d) { chEl.innerHTML = mixEl.innerHTML = '<p class="text-sm text-slate-400 py-3 text-center">โหลดไม่ได้</p>'; return; }
  if (!d.available) {
    sumEl.className = 'mb-4 text-sm px-3 py-2 rounded-lg';
    sumEl.style.cssText = 'background:#fffbeb;border-left:4px solid #f59e0b;color:#92400e';
    sumEl.textContent = 'ยังไม่มีข้อมูลวิธีจ่ายรายบิล (sale_payment) — รัน sync รอบใหม่';
    chEl.innerHTML = mixEl.innerHTML = '';
    return;
  }
  const ch = d.cash_heavy || {};
  sumEl.className = 'mb-4 text-sm px-3 py-2 rounded-lg';
  sumEl.style.cssText = ch.bills
    ? 'background:#fef2f2;border-left:4px solid #ef4444;color:#991b1b'
    : 'background:#f0fdf4;border-left:4px solid #22c55e;color:#166534';
  sumEl.innerHTML = ch.bills
    ? `🚩 <b>เงินสด + ลดหนัก ≥${d.heavy_pct}%</b>: ${fmtN(ch.bills)} บิล · <b>${fmt(ch.discount)}</b> (${ch.share_of_discount}% ของเงินลดทั้งหมด) — กระจุกที่ <b>${esc(ch.top_branch||'—')}</b> ${fmt(ch.top_branch_discount||0)}`
    : `✓ ไม่พบบิลเงินสด + ลดหนัก ≥${d.heavy_pct}%`;

  const chb = ch.branches || [];
  const maxc = Math.max(1, ...chb.map(b => b.discount || 0));
  chEl.innerHTML = chb.length ? chb.map(b => {
    const w = Math.max(3, Math.round(100 * (b.discount || 0) / maxc));
    return `<div class="flex items-center gap-2 py-1 px-1 rounded cursor-pointer hover:bg-red-50/60"
        onclick="openDiscountBills({branch:'${jsAttr(b.branch)}', payment:'Cash', sort:'discount', title:'บิลส่วนลด · เงินสด · ${jsAttr(b.branch)}'})" title="คลิกดูบิล (เฉพาะเงินสด)">
      <span class="text-xs truncate" style="width:80px">${esc(b.branch)}</span>
      <div class="flex-1" style="background:#fee2e2;border-radius:4px;overflow:hidden"><div style="width:${w}%;height:16px;background:#ef4444;border-radius:4px"></div></div>
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
      <div class="flex-1" style="background:#eef2ff;border-radius:4px;overflow:hidden"><div style="width:${w}%;height:16px;background:${isCash?'#ef4444':'#6366f1'};border-radius:4px"></div></div>
      <span class="text-xs tabular-nums" style="width:96px;text-align:right">${fmtN(m.bills)} · <b style="color:${heavy?'#dc2626':'#64748b'}">${m.disc_pct}%</b></span>
    </div>`;
  }).join('');
}

async function loadDiscountLeakage(params, gen) {
  const wrap  = document.getElementById('disc-leak-boxes');
  const sumEl = document.getElementById('disc-leak-summary');
  const meta  = document.getElementById('disc-leak-meta');
  if (!wrap) return;
  wrap.innerHTML = '<div class="col-span-full text-center text-slate-400 py-4 text-sm">กำลังโหลด…</div>';
  const d = await fetch(`${API}/api/analysis/discount_leakage?${params}`).then(r => r.json()).catch(() => null);
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
    ? 'background:#fef2f2;border-left:4px solid #ef4444;color:#991b1b'
    : 'background:#f0fdf4;border-left:4px solid #22c55e;color:#166534';
  sumEl.textContent = d.summary || '';

  meta.textContent = `ค่าเฉลี่ยทุกสาขา ${avg.toFixed(2)}% · กลาง ${(chain.median_branch_pct||0).toFixed(2)}% · เกณฑ์ผิดปกติ ≥ ${(chain.threshold_pct||0).toFixed(2)}% · ${chain.branch_count||0} สาขา`
    + (d.reasons_staged ? '' : ' · (ยังไม่มีข้อมูลประเภทส่วนลด)');

  wrap.innerHTML = d.branches.map(b => {
    const abn = b.is_abnormal;
    const pct = b.disc_pct || 0;
    const pctColor = abn ? '#dc2626' : (pct >= avg ? '#d97706' : '#16a34a');
    const vs = b.vs_avg_pts || 0;
    const vsColor = vs > 0 ? '#dc2626' : '#16a34a';
    const vsTxt = (vs > 0 ? '▲ +' : '▼ ') + Math.abs(vs).toFixed(2) + ' pts · ×' + (b.ratio_vs_chain != null ? b.ratio_vs_chain.toFixed(2) : '–') + ' ค่าเฉลี่ย';
    const reason = b.top_reason ? esc(b.top_reason) : '—';
    const untyped = b.top_reason && b.top_reason.indexOf('ไม่ระบุ') >= 0;
    const reasonColor = untyped ? '#b45309' : '#475569';
    const reasonPct = b.top_reason_pct ? ` (${Math.round(b.top_reason_pct)}%)` : '';
    return `<div class="card p-3 cursor-pointer hover:ring-2 hover:ring-indigo-200 ${abn ? 'ring-2 ring-red-300' : ''}"
        ${abn ? 'style="background:#fff7f7"' : ''}
        onclick="openDiscountBills({branch:'${jsAttr(b.branch)}', title:'บิลส่วนลด · ${jsAttr(b.branch)}'})" title="คลิกดูบิลส่วนลดของสาขานี้">
      <div class="flex items-center justify-between">
        <span class="font-semibold text-sm truncate">${abn ? '🚩 ' : ''}${esc(b.branch)}</span>
        <span class="text-[10px] text-slate-300">🔍</span>
      </div>
      <p class="mt-1 font-bold" style="color:${pctColor};font-size:1.4rem;line-height:1.1">${pct.toFixed(2)}<span style="font-size:.8rem">%</span></p>
      <p class="text-xs text-slate-500">ส่วนลดรวม <b>${fmt(b.total_discount||0)}</b> · ${fmtN(b.discount_tx||0)} บิล</p>
      <p class="text-[11px] mt-0.5" style="color:${vsColor}">${vsTxt}</p>
      <p class="text-[11px] mt-1 truncate" style="color:${reasonColor}" title="${reason}${reasonPct}">ประเภทเยอะสุด: <b>${reason}</b>${reasonPct}</p>
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
  const d = await fetch(`${API}/api/analysis/discounts?${params}`).then(r => r.json()).catch(() => ({ overall: {}, trend: [], by_branch: [], top_cashiers: [] }));
  if (gen !== loadGen) return;
  const o = d.overall || {};

  const discRate = o.total_tx ? (o.discount_tx / o.total_tx * 100).toFixed(1) : '0';
  // Carry the tab's active day/range into the KPI drill so the bill list matches the
  // KPI scope (not the global month). Empty when on the month fallback.
  const drillDate = hasDay ? `date_from:'${dFrom}',date_to:'${dTo}',` : '';
  document.getElementById('disc-kpi-row').innerHTML = [
    { label: 'Total Discounted', value: `${fmt(o.total_discount||0)}`, sub: `${discRate}% of all transactions`, color: '#ef4444', drill: `openDiscountBills({${drillDate}title:'บิลส่วนลดทั้งหมด (ตามตัวกรอง)'})` },
    { label: 'Discount Transactions', value: fmtN(o.discount_tx||0), sub: `of ${fmtN(o.total_tx||0)} total`, color: '#f59e0b', drill: `openDiscountBills({${drillDate}sort:'date', title:'บิลที่มีส่วนลด (ล่าสุด)'})` },
    { label: 'Avg Discount/Tx', value: `${fmt(o.avg_discount||0)}`, sub: 'when discount given', color: '#7c3aed' },
    { label: 'Total Transactions', value: fmtN(o.total_tx||0), sub: 'in period', color: '#64748b' },
  ].map(k => `<div class="card p-4 ${k.drill?'cursor-pointer hover:ring-2 hover:ring-indigo-200':''}" ${k.drill?`onclick="${k.drill}"`:''}><p class="kpi-label">${k.label}${k.drill?' 🔍':''}</p><p class="kpi-value" style="color:${k.color}">${k.value}</p><p class="text-xs mt-1" style="color:var(--text-muted)">${k.sub}</p></div>`).join('');

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
    const hc = heavyPct>=40 ? '#dc2626' : heavyPct>=20 ? '#d97706' : '#16a34a';
    const hi = heavyPct>=40 ? '🔻' : heavyPct>=20 ? '⚠️' : '✓';
    const seg = rows.map(r => { const w=(r.baht||0)/tot*100, m=META[r.grp]||{c:'#94a3b8'};
      return w<=0 ? '' : `<div title="${esc(r.grp)}: ${fmt(r.baht)} (${w.toFixed(0)}%)" style="width:${w}%;background:${m.c}"></div>`; }).join('');
    el.innerHTML =
      `<div class="px-3 py-2 rounded-lg text-sm mb-3" style="background:${heavyPct>=20?'#fef2f2':'#f0fdf4'};color:${hc};font-weight:600;line-height:1.4">
        ${hi} เงินลด <b>${heavyPct.toFixed(0)}%</b> (${fmt(heavyBaht)}) ไปกับบิลที่ <b>ลดหนัก ≥20%</b> ของยอด${heavyPct>=20?' — ควรตรวจสอบ':''}</div>
      <div class="flex w-full rounded-full overflow-hidden mb-3" style="height:14px;background:#f1f5f9">${seg}</div>`
      + rows.map(r => { const m=META[r.grp]||{c:'#94a3b8'}, share=(r.baht||0)/tot*100;
        return `<div class="flex items-center gap-2 py-1.5 px-1 rounded cursor-pointer hover:bg-indigo-50/60 border-b border-slate-50" ${_discPatternDrillAttr(r.grp)}>
          <span style="width:11px;height:11px;border-radius:3px;background:${m.c};flex:none"></span>
          <span class="text-sm ${m.heavy?'font-semibold':''}" style="color:${m.heavy?m.c:'#334155'}">${esc(r.grp)}</span>
          <span class="text-[10px] text-slate-400">${fmtN(r.bills)} บิล</span>
          <span class="ml-auto text-sm font-bold tabular-nums" style="color:${m.c}">${share.toFixed(0)}%</span>
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
        💡 ส่วนลด <b>${fmtN(t.amount)} ฿</b> ใช้บ่อยสุด <b>${fmtN(t.bills)} ครั้ง</b> — น่าจะเป็นคูปอง/โปรมาตรฐาน</div>`
      + amts.map(r => { const w=Math.max(3, Math.round(100*(r.bills||0)/maxB));
        return `<div class="flex items-center gap-2 py-1 px-1 rounded cursor-pointer hover:bg-indigo-50/60"
            onclick="openDiscountBills({amount:${r.amount}, title:'บิลส่วนลด ${fmtN(r.amount)} ฿ พอดี'})">
          <span class="text-sm font-semibold text-indigo-700 tabular-nums" style="width:62px">${fmtN(r.amount)} ฿</span>
          <div class="flex-1" style="background:#f1f5f9;border-radius:4px;overflow:hidden"><div style="width:${w}%;height:18px;background:#6366f1;border-radius:4px"></div></div>
          <span class="text-xs text-slate-600 tabular-nums" style="width:118px;text-align:right">${fmtN(r.bills)} ครั้ง · ${fmt(r.baht)}</span>
        </div>`; }).join('');
  })();

  // Outlier callout
  const cashiers = d.top_cashiers || [];
  const outliers = cashiers.filter(c => c.is_outlier);
  const note = document.getElementById('disc-outlier-note');
  if (outliers.length) {
    note.classList.remove('hidden');
    note.innerHTML = `🚩 <b>${outliers.length} แคชเชียร์</b>ให้ส่วนลดเกิน <b>${d.outlier_threshold_pct}%</b> ของยอดขาย (เกณฑ์ = 2× ค่ากลาง) — ` +
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
      <td class="py-2 pr-3 font-medium ${r.cashier?'text-indigo-700':''}">${r.is_outlier?'🚩 ':''}${esc(r.cashier || '—')}${r.cashier?' 🔍':''}</td>
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
    const flag = bt.disc_pct_of_sales != null && bt.disc_pct_of_sales >= (_discData.outlier_threshold_pct||99);
    const header = `<tr style="background:#eef2ff"><td colspan="5" class="py-1.5 pr-3 font-bold text-indigo-800">
        ${flag?'🚩 ':'📍 '}${esc(br)} <span class="font-normal text-slate-500">· ${rows.length} แคชเชียร์</span></td>
      <td class="py-1.5 pr-3 text-right font-bold ${flag?'text-red-600':'text-indigo-800'}">${bt.disc_pct_of_sales!=null?bt.disc_pct_of_sales.toFixed(1)+'%':'—'}</td>
      <td class="py-1.5 pr-3 text-right font-bold text-indigo-800">${fmt(bt.total_discount||0)}</td>
      <td class="py-1.5"></td></tr>`;
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
  title.textContent = `🔍 ชั่วโมง ${hour}:00–${(hour+1)%24}:00`;
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
        <td class="py-1.5 pr-3 font-medium">${esc(r.branch)}<div style="height:3px;background:#6366f1;border-radius:2px;margin-top:2px;width:${Math.round((r.revenue||0)/branchMax*100)}%"></div></td>
        <td class="py-1.5 pr-3 text-right">${fmtN(r.revenue||0)}</td>
        <td class="py-1.5 pr-3 text-right text-slate-500">${fmtN(Math.round((r.revenue||0)/days))}</td>
        <td class="py-1.5 pr-3 text-right">${fmtN(r.tx_count||0)}</td>
        <td class="py-1.5 text-right">${fmtN(r.customer_count||0)}</td></tr>`).join('') || '<tr><td colspan="5" class="py-4 text-center text-slate-400">—</td></tr>'}</tbody>
    </table></div>
    <h4 class="text-sm font-bold text-slate-600 mb-2 mt-4">รายได้ชั่วโมงนี้ · รายวันในสัปดาห์</h4>
    <div class="grid grid-cols-7 gap-1.5">${[1,2,3,4,5,6,0].map(dw => { const r = dowMap[dw]||{revenue:0,tx_count:0}; const h = Math.round((r.revenue||0)/dowMax*60); return `
      <div class="text-center">
        <div style="height:64px;display:flex;align-items:flex-end;justify-content:center"><div style="width:60%;height:${Math.max(2,h)}px;background:${DOW_COLORS?.[dw]||'#6366f1'};border-radius:3px 3px 0 0"></div></div>
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
    fetch(`${API}/api/analysis/daypart_grid?${params}`).then(r => r.json()),
    fetch(`${API}/api/analysis/hourly_open${qp()}`).then(r => r.json()).catch(() => []),
    fetch(`${API}/api/analysis/dow${qp()}`).then(r => r.json()).catch(() => []),
    fetch(`${API}/api/analysis/branch_peak?${bpParams}`).then(r => r.json()).catch(() => ({ branches: [] })),
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
      <p class="text-xl font-bold" style="color:var(--indigo)">${h.hour}:00–${(h.hour+1)%24}:00 🔍</p>
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
  const metricLabel = { revenue:'Revenue', tx_count:'Transactions', customer_count:'Customers', avg_ticket:'Avg Ticket' }[_timeflowMetric] || 'Revenue';
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
      return `<div title="${i+10}:00 ${fmtN(v)}" style="flex:1;height:${Math.max(2, pct * 0.32)}px;background:${isPeak ? '#4f46e5' : '#c7d2fe'};border-radius:1px;align-self:flex-end"></div>`;
    }).join('');
    return `<div onclick="tfDrillBranch('${jsAttr(b.branch)}', this)"
      class="rounded-xl p-3 cursor-pointer transition-all border"
      style="background:${isActive ? '#eef2ff' : 'var(--bg-card)'};border-color:${isActive ? '#6366f1' : 'var(--border)'}">
      <p class="text-[11px] font-semibold truncate mb-1" style="color:${isActive?'#4f46e5':'var(--text-primary)'}">${esc(b.branch)}</p>
      <p class="text-xs" style="color:var(--text-muted)">พีค <span class="font-bold" style="color:#4f46e5">${peakH}</span> · <span class="font-bold" style="color:#0891b2">${peakD}</span></p>
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
        backgroundColor: hours.map(h => h === peakHour ? '#4f46e5' : '#c7d2fe'),
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
  const tv = await fetch(`${API}/api/analysis/table-turnover?view=dwell&${params}`).then(r=>r.json()).catch(()=>null);
  const tvEl = document.getElementById('tfDrillTurnover');
  if (tvEl) {
    const br = tv && tv.by_branch && tv.by_branch.find(x => x.branch === branchName);
    tvEl.innerHTML = br
      ? `<div class="flex gap-4 flex-wrap">
           <span class="px-3 py-1.5 rounded-lg text-xs" style="background:#eef2ff;color:#4f46e5">หมุนเวียน <b>${br.avg_turns_per_day?.toFixed(1)||'—'}</b> รอบ/โต๊ะ/วัน</span>
           <span class="px-3 py-1.5 rounded-lg text-xs" style="background:#f0fdf4;color:#059669">Dwell time ≈ <b>${br.est_dwell_min||'—'} min</b></span>
           <span class="px-3 py-1.5 rounded-lg text-xs" style="background:#fffbeb;color:#d97706">โต๊ะที่ใช้ <b>${br.avg_tables_per_day?.toFixed(0)||'—'}</b> โต๊ะ/วัน</span>
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
  const d = await fetch(`${API}/api/analysis/table-turnover?view=dwell&${params}`).then(r => r.json()).catch(() => null);
  if (!d || !d.available) {
    document.getElementById('tableTurnoverKpis').innerHTML = '<p class="text-xs text-slate-400 col-span-4">No table data available. Requires table_no field in transactions.</p>';
    return;
  }
  const o = d.overall;
  document.getElementById('tableTurnoverKpis').innerHTML = [
    { label: 'Avg Turns/Table/Day', value: o.avg_turns_per_day?.toFixed(1) || '—', sub: 'network avg', color: '#4f46e5' },
    { label: 'Est. Dwell Time', value: o.est_dwell_min ? `${o.est_dwell_min} min` : '—', sub: 'median gap between turns', color: '#0891b2' },
    { label: 'Best Turnover', value: d.by_branch[0]?.branch || '—', sub: `${d.by_branch[0]?.avg_turns_per_day?.toFixed(1)} turns/day`, color: '#059669' },
    { label: 'Branches Tracked', value: d.by_branch.length, sub: 'with table data', color: '#64748b' },
  ].map(k => `<div class="card p-3 text-center"><p class="text-xs text-slate-500">${k.label}</p><p class="text-base font-bold" style="color:${k.color}">${k.value}</p><p class="text-xs text-slate-400 mt-0.5">${k.sub}</p></div>`).join('');

  mkChart('chartTableTurnover', {
    type: 'bar',
    data: {
      labels: d.by_branch.map(b => b.branch),
      datasets: [
        { label: 'Avg Turns/Day', data: d.by_branch.map(b => b.avg_turns_per_day), backgroundColor: PALETTE_6[0] + 'cc', borderRadius: 4, yAxisID: 'turns' },
        { label: 'Est. Dwell (min)', data: d.by_branch.map(b => b.est_dwell_min), type: 'line', borderColor: PALETTE_6[1], backgroundColor: 'transparent', tension: 0.3, pointRadius: 4, yAxisID: 'dwell' },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom', labels: { font: { size: 10 } } }, datalabels: { display: false } },
      scales: {
        turns: { type: 'linear', position: 'left',  title: { display: true, text: 'Turns/day' } },
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

  // Red (quiet) → amber → green (busiest); white text on saturated cells
  const cellColor = intensity => {
    if (!intensity || intensity < 0.01) return { bg: '#f8fafc', fg: '#cbd5e1' };
    return { bg: heatColor(intensity, false), fg: '#fff' };
  };

  const cellW = Math.max(38, Math.floor((gridEl.offsetWidth - 50) / openHours.length));

  let html = `<table style="border-collapse:separate;border-spacing:3px 3px;font-size:11px;width:100%">
    <thead><tr>
      <th style="width:42px;text-align:left"></th>
      ${openHours.map(h => `<th style="text-align:center;font-size:10px;color:#64748b;font-weight:600;padding-bottom:4px">${h}</th>`).join('')}
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
    html += `<tr><td style="font-size:11px;color:#475569;padding:2px 6px 2px 0;font-weight:600;white-space:nowrap">${DOW_LABELS[dow]}</td>`;
    for (const h of openHours) {
      const v = cellMap[`${dow}_${h}`] || 0;
      const { bg, fg } = cellColor(v / maxVal);
      const rank = rankMap[`${dow}_${h}`];
      html += `<td
        onclick="tfCellClick(${dow},${h},${v},${rank})"
        title="${DOW_LABELS[dow]} ${h}:00 · ${v > 0 ? (isMoney ? '' + v.toLocaleString() : v.toLocaleString()) : '—'} · Rank #${rank}"
        style="background:${bg};color:${fg};height:36px;min-width:${cellW}px;text-align:center;cursor:pointer;border-radius:5px;font-size:10px;font-weight:${v>0?600:400};transition:transform .12s;vertical-align:middle"
        onmouseover="this.style.transform='scale(1.1)';this.style.zIndex=10;this.style.position='relative'"
        onmouseout="this.style.transform='';this.style.zIndex='';this.style.position=''">
        ${fmtCell(v)}
      </td>`;
    }
    html += '</tr>';
  }

  html += `</tbody></table>
  <div style="display:flex;align-items:center;gap:6px;margin-top:10px;font-size:10px;color:#64748b">
    <span style="opacity:.7">น้อย</span>
    ${[0,0.25,0.5,0.75,1].map(t=>`<div style="width:28px;height:8px;background:${heatColor(t,false)};border-radius:3px"></div>`).join('')}
    <span style="opacity:.7">มาก</span>
    <span style="margin-left:auto;opacity:.6">Open hours: ${_tfOpenFrom}:00–${_tfOpenTo}:00 · configurable in Settings → Hours</span>
  </div>`;

  gridEl.innerHTML = html;
}

function tfCellClick(dow, hour, value, rank) {
  const DOW_LABELS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  const isMoney = _timeflowMetric === 'revenue' || _timeflowMetric === 'avg_ticket';
  const metricName = { revenue:'Revenue', tx_count:'บิล', customer_count:'Customers', avg_ticket:'Avg Ticket' }[_timeflowMetric];
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
          <p class="text-xs text-slate-500">Rank</p>
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
          <div class="flex-1 bg-slate-100 rounded-full h-2"><div style="width:${pct}%;background:#4f46e5" class="h-2 rounded-full"></div></div>
          <span class="text-slate-600 font-semibold w-20 text-right">${fmtV(c.value)}</span>
          <span class="text-slate-400 w-10 text-right">${pct}%</span>
        </div>`;
      }).join('')}
    </div>`;
}

// ── Error capture (visible on-screen for file:// debugging) ────
(function() {
  const _errors = [];
  function showErrBanner(msg) {
    let b = document.getElementById('_errBanner');
    if (!b) {
      b = document.createElement('div');
      b.id = '_errBanner';
      b.style.cssText = 'position:fixed;bottom:12px;left:12px;right:12px;z-index:99999;background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;padding:10px 14px;font-size:12px;color:#991b1b;max-height:140px;overflow:auto;box-shadow:0 4px 12px rgba(0,0,0,.15)';
      document.body.appendChild(b);
    }
    _errors.push(msg);
    b.innerHTML = '<strong>JS errors (close to ignore):</strong><br>' + _errors.map(e => '• ' + e).join('<br>') +
      '<button onclick="this.parentNode.remove()" style="float:right;background:none;border:none;cursor:pointer;font-size:14px">✕</button>';
  }
  // Errors we silently log (cosmetic only — Chart.js datalabel formatters that hit null/undefined values)
  const _IGNORE_RE = /toFixed is not a function|Cannot read propert(y|ies) of (null|undefined).*toFixed/;
  window.addEventListener('error', function(e) {
    const msg = e.message || 'Unknown error';
    if (_IGNORE_RE.test(msg)) { console.warn('[ignored]', msg, e.filename, e.lineno); return; }
    showErrBanner(msg + (e.filename ? ' @ '+e.filename.split('/').pop()+':'+e.lineno : ''));
  });
  window.addEventListener('unhandledrejection', function(e) {
    if (e.reason === STALE) return;                                   // expected — stale nav, already toasted
    const msg = (e.reason && e.reason.message) ? e.reason.message : String(e.reason);
    if (_IGNORE_RE.test(msg) || /abort/i.test(msg)) { console.warn('[ignored promise]', msg); return; }
    showErrBanner('Unhandled promise: ' + msg);
  });
  // Check vendor load failures
  window.addEventListener('load', function() {
    if (window.__vendorErr && __vendorErr.length) {
      showErrBanner('Vendor script(s) failed to load: ' + __vendorErr.join(', '));
    }
  });
})();

// ── Cost Coverage tab (Analysis #3) ────────────────────────────
async function loadCostCoverage() {
  const gen = loadGen;
  const p = new URLSearchParams();
  if (getBranch())    p.set('branch', getBranch());
  if (getMonthFrom()) p.set('month_from', getMonthFrom());
  if (getMonthTo())   p.set('month_to', getMonthTo());
  let d;
  try { d = await getJSON('/api/cost/uncosted-coverage?' + p.toString(), { gen }); }
  catch(e){ if (e === STALE) return; throw e; }
  if (gen !== loadGen) return;
  const ch = d.chain || {};
  document.getElementById('cov-kpis').innerHTML = [
    ['รายได้รวม (ex VAT)', fmt(ch.total_revenue), '#4f46e5'],
    ['มีต้นทุนแล้ว',        fmt(ch.costed_revenue), '#10b981'],
    ['ยังไม่มีต้นทุน',       fmt(ch.uncosted_revenue), '#ef4444'],
    ['% ยังไม่มีต้นทุน',     (ch.pct_uncosted != null ? ch.pct_uncosted.toFixed(1)+'%' : '—'), '#f59e0b'],
  ].map(([l,v,c]) => `<div class="card p-4" style="border-top:3px solid ${c}">
      <div class="text-xs text-slate-500">${esc(l)}</div>
      <div class="text-2xl font-bold" style="color:${c}">${v}</div></div>`).join('');
  const tr = d.monthly_trend || [];
  mkChart('cov-trend', {
    type:'line',
    data:{ labels: tr.map(r=>r.period), datasets:[{
      label:'% ไม่มีต้นทุน', data: tr.map(r=>r.pct_uncosted),
      borderColor:'#ef4444', backgroundColor:'rgba(239,68,68,.12)', fill:true }]},
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{display:false}, datalabels:{display:false} },
      scales:{ y:{ beginAtZero:true, ticks:{ callback:v=>v+'%' } } } }
  });
  document.getElementById('cov-branches').innerHTML = (d.branches||[]).map(b=>`
    <tr class="border-b border-slate-50">
      <td class="py-1.5 pr-3">${esc(b.branch)}</td>
      <td class="py-1.5 pr-3 text-right">${fmt(b.total_revenue)}</td>
      <td class="py-1.5 pr-3 text-right" style="color:#e11d48">${fmt(b.uncosted_revenue)}</td>
      <td class="py-1.5 pr-3 text-right font-semibold">${(b.pct_uncosted??0).toFixed(1)}%</td>
      <td class="py-1.5 text-right">${b.uncosted_skus}</td>
    </tr>`).join('') || '<tr><td colspan="5" class="py-3 text-slate-400 text-center">—</td></tr>';
  // 🛠️ No-cost fixer worklist — editable unit_cost inputs
  renderUncostedFixer(d.top_uncosted || []);
  // Top cost-driver ingredients (grouped families) + peak hours
  loadCoverageExtras();
}

// Render the editable "fill missing cost" worklist into #cov-fixer.
function renderUncostedFixer(rows) {
  const tb = document.getElementById('cov-fixer');
  if (!tb) return;
  document.getElementById('cov-fixer-msg').textContent = '';
  tb.innerHTML = rows.map(r => {
    const price = (r.qty > 0) ? (r.revenue / r.qty) : 0;          // ex-VAT avg selling price = ceiling for cost
    const sku   = jsAttr(r.sku_code);
    return `<tr data-sku="${esc(r.sku_code)}" data-price="${price.toFixed(2)}" class="border-b border-slate-50">
      <td class="py-1.5 pr-2 font-mono text-xs text-slate-500">${esc(r.sku_code)}</td>
      <td class="py-1.5 pr-2">${esc((r.description||'').replace(/^\((?:A|B|T)\)\s*/,''))}</td>
      <td class="py-1.5 pr-2 text-xs text-slate-400">${esc(r.category||'')}</td>
      <td class="py-1.5 pr-2 text-right">${fmt(r.revenue)}</td>
      <td class="py-1.5 pr-2 text-right text-slate-400">${fmtN(r.qty)}</td>
      <td class="py-1.5 pr-2 text-right text-slate-500">${price>0?fmtN(price):'—'}</td>
      <td class="py-1.5 pr-2 text-right">
        <input type="number" min="0" step="0.01" placeholder="0.00"
               class="cov-cost-inp w-24 text-right border border-slate-200 rounded px-2 py-1 text-sm"
               onkeydown="if(event.key==='Enter')saveUncostedRow('${sku}')"></td>
      <td class="py-1.5 text-center"><button onclick="saveUncostedRow('${sku}')"
            class="text-xs px-2 py-1 rounded border border-indigo-200 text-indigo-600 hover:bg-indigo-50">💾</button></td>
    </tr>`;
  }).join('') || '<tr><td colspan="8" class="py-4 text-center text-emerald-600">🎉 ไม่มี SKU ค้างต้นทุนในช่วงนี้</td></tr>';
}

// POST one or more {sku_code, unit_cost} to /api/cost, then surface result + reload.
async function _postCosts(entries, msgEl) {
  try {
    const res = await fetch(`${API}/api/cost`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(entries),
    });
    const out = await res.json();
    if (!res.ok) { msgEl.innerHTML = `<span class="text-red-500">บันทึกไม่สำเร็จ: ${esc(out.detail||res.status)}</span>`; return out; }
    return out;
  } catch(e){ msgEl.innerHTML = `<span class="text-red-500">บันทึกไม่สำเร็จ: ${esc(String(e.message||e))}</span>`; return null; }
}

async function saveUncostedRow(skuCode) {
  const msg = document.getElementById('cov-fixer-msg');
  const row = document.querySelector(`#cov-fixer tr[data-sku="${CSS.escape(skuCode)}"]`);
  if (!row) return;
  const inp = row.querySelector('.cov-cost-inp');
  const val = parseFloat(inp.value);
  if (isNaN(val) || val < 0) { msg.innerHTML = '<span class="text-amber-600">กรอกต้นทุนเป็นตัวเลข ≥ 0 ก่อน</span>'; inp.focus(); return; }
  const out = await _postCosts([{ sku_code: skuCode, unit_cost: val }], msg);
  if (!out) return;
  if (out.skipped_price_as_cost) {
    msg.innerHTML = `<span class="text-amber-600">⚠ ${esc(skuCode)}: ค่าที่ใส่ใกล้เคียง "ราคาขาย×1.07" — น่าจะใส่ราคาขายแทนต้นทุน กรุณาใส่ต้นทุนจริง</span>`;
    inp.classList.add('border-amber-400'); return;
  }
  if (out.updated > 0) {
    row.style.background = '#ecfdf5';
    row.querySelector('button').textContent = '✓';
    msg.innerHTML = `<span class="text-emerald-600">บันทึก ${esc(skuCode)} = ${val} แล้ว · กำลังรีเฟรช…</span>`;
    loadCostCoverage();
  }
}

async function saveAllUncosted() {
  const msg = document.getElementById('cov-fixer-msg');
  const entries = [];
  document.querySelectorAll('#cov-fixer tr[data-sku]').forEach(row => {
    const v = parseFloat(row.querySelector('.cov-cost-inp')?.value);
    if (!isNaN(v) && v >= 0) entries.push({ sku_code: row.dataset.sku, unit_cost: v });
  });
  if (!entries.length) { msg.innerHTML = '<span class="text-amber-600">ยังไม่ได้กรอกต้นทุนช่องไหนเลย</span>'; return; }
  const out = await _postCosts(entries, msg);
  if (!out) return;
  let txt = `บันทึก ${out.updated||0} รายการแล้ว`;
  if (out.skipped_price_as_cost) txt += ` · ข้าม ${out.skipped_price_as_cost} รายการ (ค่าดูเหมือนราคาขาย ไม่ใช่ต้นทุน)`;
  msg.innerHTML = `<span class="${out.skipped_price_as_cost?'text-amber-600':'text-emerald-600'}">${esc(txt)} · กำลังรีเฟรช…</span>`;
  loadCostCoverage();
}

async function loadCoverageExtras() {
  const ip = new URLSearchParams({ group_family:'true', limit:'15' });
  if (getMonthFrom()) ip.set('month_from', getMonthFrom());
  if (getMonthTo())   ip.set('month_to', getMonthTo());
  if (getBranch())    ip.set('branch', getBranch());
  fetch(`${API}/api/bom/consumption-analysis?${ip}`).then(r=>r.json()).then(d=>{
    const ings = d.ingredients||[];
    document.getElementById('cov-ingredients').innerHTML = ings.map((r,i)=>`
      <tr class="border-b border-slate-50">
        <td class="py-1.5 pr-3 text-slate-400">${i+1}</td>
        <td class="py-1.5 pr-3">${esc(r.name)}</td>
        <td class="py-1.5 pr-3 text-right text-red-500 font-medium">${fmtN(r.total_cost)}</td>
        <td class="py-1.5 text-right font-semibold">${(r.pct||0).toFixed(1)}%</td>
      </tr>`).join('') || '<tr><td colspan="4" class="py-3 text-center text-slate-400">—</td></tr>';
  }).catch(()=>{});

  const hp = new URLSearchParams();
  if (getBranch())    hp.set('branch', getBranch());
  if (getMonthFrom()) hp.set('month_from', getMonthFrom());
  if (getMonthTo())   hp.set('month_to', getMonthTo());
  fetch(`${API}/api/analysis/hourly_open?${hp}`).then(r=>r.json()).then(d=>{
    const all = (d.hourly||d||[]).filter(h=>h.hour!=null);
    // ตัดเวลาร้านปิดออก: ใช้ closed_hours config ถ้ามี ไม่งั้น fallback ตัดชั่วโมงที่รายได้ = 0
    const hasClosed = all.some(h=>h.is_open===false);
    const hrs = all.filter(h => hasClosed ? h.is_open!==false : (h.avg_daily_revenue ?? h.revenue ?? 0) > 0);
    const rev = hrs.map(h=>h.avg_daily_revenue ?? h.revenue ?? 0);
    const mx = Math.max(...rev, 1);
    mkChart('cov-peak', { type:'bar',
      data:{ labels: hrs.map(h=>h.hour+':00'),
        datasets:[{ data: rev, backgroundColor: hrs.map((h,i)=> rev[i]===mx?'#ef4444':colorForHour(h.hour)), borderRadius:3 }] },
      options:{ responsive:true, maintainAspectRatio:false,
        plugins:{ legend:{display:false}, datalabels:{display:false},
          tooltip:{callbacks:{label:ctx=>fmtN(ctx.raw)+' avg/hr'}} },
        scales:{ x:{ticks:{font:{size:9}}}, y:{ticks:{callback:v=>fmt(v)}} } } });
  }).catch(()=>{});
}

// 🏭 Prime Cost = Food% + Labor% per branch (the biggest controllable costs).
let _primeKey = null;
async function loadPrimeCost() {
  const msel = document.getElementById('pnlMonthSel');
  const month = (msel && msel.value) || getMonthTo() || getMonthFrom() || '';
  const _fk = month + '|' + (getGroup() || '');
  if (_primeKey === _fk) return; _primeKey = _fk;
  const p = new URLSearchParams();
  if (month) p.set('month', month);
  if (getGroup()) p.set('group', getGroup());
  let d; try { d = await fetch(`${API}/api/finance/prime-cost?${p}`).then(r => r.json()); } catch (e) { return; }
  const ch = d.chain || {}; const band = d.band || { good: 70, high: 78 };
  const rows = d.branches || [];
  const pcCol = pp => pp == null ? '#94a3b8' : pp > band.high ? '#ef4444' : pp > band.good ? '#f59e0b' : '#10b981';
  const cw = document.getElementById('primeChain');
  if (cw) cw.textContent = `เดือน ${d.month || '—'} · เฉลี่ยเครือ Food ${ch.food_pct != null ? ch.food_pct + '%' : '—'}${ch.labor_pct != null ? ' + Labor ' + ch.labor_pct + '% = Prime ' + ch.prime_pct + '%' : ''}`;
  const bn = document.getElementById('primeBanner');
  if (bn) {
    if (!ch.branches_with_labor) {
      bn.innerHTML = `<div class="card p-3 text-xs" style="background:#fffbeb;border-left:3px solid #f59e0b">💼 ยังไม่ได้ใส่ <b>ค่าแรง</b> — Food% แสดงจริงแล้ว แต่ <b>Prime%/Labor%</b> จะว่างจนกว่าจะกรอก · ไปที่ <button onclick="switchTab('settings');switchSettingsTab('fixedcosts')" class="underline font-semibold" style="color:#92400e">Settings → ค่าแรง + ค่าใช้จ่ายคงที่</button> (กรอกครั้งเดียว ~27 ช่อง)</div>`;
    } else if (ch.branches_with_labor < ch.branch_count) {
      bn.innerHTML = `<div class="text-xs" style="color:var(--text-muted)">ใส่ค่าแรงแล้ว ${ch.branches_with_labor}/${ch.branch_count} สาขา — ที่เหลือแสดงเฉพาะ Food%</div>`;
    } else { bn.innerHTML = ''; }
  }
  document.getElementById('primeBody').innerHTML = rows.map(r => `<tr class="border-t border-slate-100 hover:bg-indigo-50/40">
    <td class="py-1.5 pr-2 font-medium">${esc(r.branch)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${fmt(r.rev)}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums" style="color:${r.food_pct > 55 ? '#ef4444' : r.food_pct > 50 ? '#f59e0b' : '#10b981'}">${r.food_pct != null ? r.food_pct + '%' : '—'}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums">${r.labor_pct != null ? r.labor_pct + '%' : '<span class="text-slate-300">—</span>'}</td>
    <td class="py-1.5 pr-2 text-right tabular-nums font-bold" style="color:${pcCol(r.prime_pct)}">${r.prime_pct != null ? r.prime_pct + '%' : '<span class="text-slate-300">—</span>'}</td>
    <td class="py-1.5 text-right tabular-nums" style="color:${r.operating_pct == null ? '#94a3b8' : r.operating_pct < 0 ? '#ef4444' : '#10b981'}">${r.operating_pct != null ? r.operating_pct + '%' : '—'}</td>
  </tr>`).join('') || '<tr><td colspan="6" class="py-4 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
}

// ── Branch P&L tab (Analysis #1, BoM-based COGS) ───────────────
async function loadPnlBranch() {
  loadPrimeCost();
  const gen = loadGen;
  // populate month dropdown once (from selMonthFrom options)
  const msel = document.getElementById('pnlMonthSel');
  if (msel && msel.options.length <= 1) {
    const src = document.getElementById('selMonthTo');
    if (src) [...src.options].filter(o=>o.value).forEach(o=>{
      const opt=document.createElement('option'); opt.value=o.value; opt.textContent=o.textContent; msel.appendChild(opt);
    });
  }
  _syncExtraMonth('pnlMonthSel', 'pnlbranch');   // follow the global period (snap to its end month)
  const pnlMonth = msel ? msel.value : '';
  const p = new URLSearchParams();
  if (getBranch())    p.set('branch', getBranch());
  if (pnlMonth) { p.set('month_from', pnlMonth); p.set('month_to', pnlMonth); }
  else {
    if (getMonthFrom()) p.set('month_from', getMonthFrom());
    if (getMonthTo())   p.set('month_to', getMonthTo());
  }
  _pnlActiveMonth = pnlMonth || getMonthTo() || getMonthFrom() || '';
  let d;
  try { d = await getJSON('/api/pnl/branch?' + p.toString(), { gen }); }
  catch(e){ if (e === STALE) return; throw e; }
  if (gen !== loadGen) return;
  const c = d.chain || {};
  document.getElementById('pnl-kpis').innerHTML = [
    ['รายได้ (ex VAT)',      fmt(c.revenue),          '#4f46e5'],
    ['ต้นทุน BoM',           fmt(c.cogs),             '#f59e0b'],
    ['กำไรขั้นต้น',          fmt(c.gross_profit) + (c.gp_margin_pct!=null?` · ${c.gp_margin_pct}%`:''), '#10b981'],
    ['กำไรดำเนินงาน',        fmt(c.operating_profit) + (c.op_margin_pct!=null?` · ${c.op_margin_pct}%`:''), (c.operating_profit>=0?'#0891b2':'#ef4444')],
  ].map(([l,v,col]) => `<div class="card p-4" style="border-top:3px solid ${col}">
      <div class="text-xs text-slate-500">${esc(l)}</div>
      <div class="text-xl font-bold" style="color:${col}">${v}</div></div>`).join('');
  const note = document.getElementById('pnl-note');
  if (!c.fixed_costs_set || (c.fixed_cost_period||0) === 0) {
    note.classList.remove('hidden');
    note.textContent = '⚠ ยังไม่ได้ตั้งค่าใช้จ่ายคงที่ (rent/labor/utilities) → กำไรดำเนินงาน = กำไรขั้นต้น. ตั้งค่าได้ที่ ตั้งค่า → ค่าใช้จ่ายคงที่รายสาขา';
  } else { note.classList.add('hidden'); }
  _pnlBranchRows = {}; (d.branches||[]).forEach(b => { _pnlBranchRows[b.branch] = b; });
  document.getElementById('pnl-branches').innerHTML = (d.branches||[]).map(b => {
    const opCol = (b.operating_profit>=0) ? '#0891b2' : '#e11d48';
    return `<tr class="border-b border-slate-50 hover:bg-indigo-50">
      <td class="py-1.5 pr-3 font-medium"><a onclick="openPnlDrill('${jsAttr(b.branch)}')" class="text-indigo-600 hover:underline cursor-pointer">${esc(b.branch)} 🔍</a></td>
      <td class="py-1.5 pr-3 text-right">${fmt(b.revenue)}</td>
      <td class="py-1.5 pr-3 text-right" style="color:#b45309">${fmt(b.cogs)}</td>
      <td class="py-1.5 pr-3 text-right" style="color:#047857">${fmt(b.gross_profit)}</td>
      <td class="py-1.5 pr-3 text-right">${b.gp_margin_pct!=null?b.gp_margin_pct+'%':'—'}</td>
      <td class="py-1.5 pr-3 text-right text-slate-500">${b.fixed_cost_period?fmt(b.fixed_cost_period):'—'}</td>
      <td class="py-1.5 pr-3 text-right font-semibold" style="color:${opCol}">${fmt(b.operating_profit)}</td>
      <td class="py-1.5 pr-3 text-right" style="color:${opCol}">${b.op_margin_pct!=null?b.op_margin_pct+'%':'—'}</td>
      <td class="py-1.5 pr-3 text-right text-slate-500">${fmtN(b.covers)}</td>
      <td class="py-1.5 text-right text-slate-500">${b.breakeven_covers?fmtN(b.breakeven_covers):'—'}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="10" class="py-3 text-slate-400 text-center">—</td></tr>';
}

// ── P&L branch×month drill-down (click a branch name) ──────────────────────
let _pnlActiveMonth = '';
let _pnlBranchRows = {};

// Full P&L waterfall (Revenue → COGS → GP → Fixed → Operating Profit) from a branch row
function _pnlWaterfallHtml(b, scopeLabel) {
  if (!b) return '';
  const rev = b.revenue || 0;
  const pct = v => rev ? (v / rev * 100) : 0;
  const fc  = b.fixed_cost_period || 0;
  const row = (label, val, color, opts={}) => {
    const w = Math.max(0, Math.min(100, Math.abs(pct(val))));
    const sign = opts.minus ? '−' : (opts.eq ? '=' : '');
    return `<div class="flex items-center gap-2 py-1 ${opts.bold?'font-bold':''}">
      <span class="w-28 text-xs ${opts.bold?'text-slate-800':'text-slate-500'}">${sign} ${label}</span>
      <div class="flex-1 bg-slate-100 rounded-full h-3"><div style="width:${w}%;background:${color};height:100%;border-radius:9999px"></div></div>
      <span class="w-28 text-right text-sm" style="color:${color}">${fmtN(val)}</span>
      <span class="w-12 text-right text-xs text-slate-400">${pct(val).toFixed(0)}%</span>
    </div>`;
  };
  const opCol = (b.operating_profit||0) >= 0 ? '#0891b2' : '#e11d48';
  const beCovers = b.breakeven_covers ? `จุดคุ้มทุน ~${fmtN(b.breakeven_covers)} ที่นั่ง/ลูกค้า (จริง ${fmtN(b.covers||0)})` : '';
  return `<div class="card p-4 mb-4">
    <h4 class="text-sm font-bold text-slate-700 mb-2">งบกำไรขาดทุน (P&L Waterfall) <span class="font-normal text-xs text-slate-400">· ${esc(scopeLabel||'')}</span></h4>
    ${row('รายได้ (ex-VAT)', rev, '#4f46e5', {bold:true})}
    ${row('ต้นทุน BoM', b.cogs||0, '#f59e0b', {minus:true})}
    ${row('กำไรขั้นต้น', b.gross_profit||0, '#10b981', {eq:true, bold:true})}
    ${row('ค่าใช้จ่ายคงที่', fc, '#a855f7', {minus:true})}
    ${row('กำไรดำเนินงาน', b.operating_profit||0, opCol, {eq:true, bold:true})}
    <p class="text-[11px] text-slate-400 mt-2">${beCovers}${b.fixed_cost_period? '' : ' · ยังไม่ได้ตั้งค่าใช้จ่ายคงที่ → OP = GP'}</p>
  </div>`;
}

function closePnlDrill() { const m = document.getElementById('pnlDrillModal'); if (m) { m.style.display = 'none'; m.classList.add('hidden'); } }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closePnlDrill(); });

async function openPnlDrill(branch) {
  const modal = document.getElementById('pnlDrillModal');
  const body  = document.getElementById('pnlDrillBody');
  const title = document.getElementById('pnlDrillTitle');
  // pick the month: tab dropdown → range bounds → else fall back to the latest
  // month available in the dropdown (avoids a dead-end in All-Time / "ทุกเดือน" mode)
  let month = _pnlActiveMonth || getMonthTo() || getMonthFrom();
  if (!month) {
    const msel = document.getElementById('pnlMonthSel');
    if (msel) month = [...msel.options].map(o => o.value).find(v => v) || '';
  }
  if (!month) { alert('ยังไม่มีข้อมูลเดือนให้แสดง — โหลดข้อมูลก่อน'); return; }
  title.textContent = `🔍 ${branch} · ${typeof fmtPeriod==='function'?fmtPeriod(month):month}`;
  body.innerHTML = '<p class="text-sm text-slate-400 py-8 text-center">กำลังโหลด…</p>';
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  let d;
  try {
    const r = await fetch(`${API}/api/pnl/branch-month-detail?branch=${encodeURIComponent(branch)}&month=${month}&limit=40`);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    d = await r.json();
  } catch(e){ body.innerHTML = '<p class="text-sm text-red-500 py-6 text-center">โหลดไม่ได้: ' + esc(String(e.message||e)) + '</p>'; return; }
  const s = d.summary || {};
  const revDelta = s.rev_delta_pct;
  const deltaChip = revDelta==null ? '' :
    `<span style="color:${revDelta>=0?'#10b981':'#ef4444'}">${revDelta>=0?'▲':'▼'} ${Math.abs(revDelta)}% vs ${fmtPeriod(d.prev_month)}</span>`;
  const kpi = (l,v,c)=>`<div class="card p-3" style="border-top:3px solid ${c}"><div class="text-[11px] text-slate-500">${l}</div><div class="text-lg font-bold" style="color:${c}">${v}</div></div>`;
  const costRows = (d.cost_breakdown||[]).slice(0,40).map((r,i)=>`
    <tr class="border-b border-slate-50">
      <td class="py-1 pr-2 text-slate-400">${i+1}</td>
      <td class="py-1 pr-2">${esc((r.description||'').replace(/^\((?:A|B)\)\s*/,''))}</td>
      <td class="py-1 pr-2 text-right">${fmtN(r.servings)}</td>
      <td class="py-1 pr-2 text-right text-red-500">${fmtN(r.cogs)}</td>
      <td class="py-1 text-right font-semibold">${r.cost_pct}%</td>
    </tr>`).join('');
  const ingRows = (d.top_ingredients||[]).map((r,i)=>`
    <tr class="border-b border-slate-50"><td class="py-1 pr-2 text-slate-400">${i+1}</td>
    <td class="py-1 pr-2">${esc(r.name)}</td><td class="py-1 pr-2 text-right text-red-500">${fmtN(r.cost)}</td>
    <td class="py-1 text-right font-semibold">${r.pct}%</td></tr>`).join('');
  const moverRow = (m,up)=>{
    const pct = m.is_new?'ใหม่':(m.delta_pct==null?'—':(m.delta_pct>0?'+':'')+m.delta_pct+'%');
    return `<div class="flex justify-between gap-2 py-0.5">
      <span class="truncate">${esc((m.description||'').replace(/^\((?:A|B)\)\s*/,''))}</span>
      <span class="flex-shrink-0 font-semibold" style="color:${up?'#10b981':'#ef4444'}">${up?'▲':'▼'}${fmtN(Math.abs(m.delta))} (${pct})</span>
    </div>`;
  };
  body.innerHTML = `
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
      ${kpi('รายได้ '+deltaChip, fmtN(s.revenue), '#4f46e5')}
      ${kpi('ต้นทุน BoM', fmtN(s.cogs), '#f59e0b')}
      ${kpi('กำไรขั้นต้น', fmtN(s.gross_profit)+' · '+(s.gp_margin_pct??'—')+'%', '#10b981')}
      ${kpi('Food Cost %', (s.food_cost_pct??'—')+'%', '#ef4444')}
    </div>
    ${_pnlWaterfallHtml(_pnlBranchRows[branch], (function(){ const ps=document.getElementById('pnlMonthSel')?.value; return ps?fmtPeriod(ps):(getMonthFrom()?`${fmtMonth(getMonthFrom())}–${fmtMonth(getMonthTo()||getMonthFrom())}`:'ทุกเดือนรวม'); })())}
    <div class="card p-4 mb-4">
      <h4 class="text-sm font-bold text-slate-700 mb-2">แนวโน้มกำไรรายเดือน (ทุกเดือนของสาขานี้)</h4>
      <div style="height:200px"><canvas id="pnlDrillTrend"></canvas></div>
    </div>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
      <div>
        <h4 class="text-sm font-semibold text-slate-700 mb-2">🍽️ ต้นทุน BoM ประกอบด้วยเมนู (Top 40 · % ของต้นทุนเดือนนี้)</h4>
        <div style="max-height:340px;overflow:auto"><table class="w-full text-xs">
          <thead class="sticky top-0 bg-white"><tr class="text-left text-slate-400 border-b">
            <th class="py-1 pr-2">#</th><th class="py-1 pr-2">เมนู</th><th class="py-1 pr-2 text-right">เสิร์ฟ</th>
            <th class="py-1 pr-2 text-right">ต้นทุน</th><th class="py-1 text-right">%</th></tr></thead>
          <tbody>${costRows||'<tr><td colspan=5 class="py-3 text-center text-slate-400">—</td></tr>'}</tbody>
        </table></div>
      </div>
      <div>
        <h4 class="text-sm font-semibold text-slate-700 mb-2">🥩 วัตถุดิบหลักที่ขับต้นทุน</h4>
        <div style="max-height:150px;overflow:auto;margin-bottom:14px"><table class="w-full text-xs">
          <tbody>${ingRows||'<tr><td class="py-3 text-center text-slate-400">—</td></tr>'}</tbody></table></div>
        <h4 class="text-sm font-semibold text-emerald-700 mb-2">📈 เมนูคนนิยมมากขึ้น (vs ${fmtPeriod(d.prev_month)})</h4>
        <div class="text-xs mb-3">${(d.rising_dishes||[]).slice(0,8).map(m=>moverRow(m,true)).join('')||'<span class="text-slate-400">—</span>'}</div>
        <h4 class="text-sm font-semibold text-red-600 mb-2">📉 เมนูคนนิยมน้อยลง</h4>
        <div class="text-xs">${(d.falling_dishes||[]).slice(0,6).map(m=>moverRow(m,false)).join('')||'<span class="text-slate-400">—</span>'}</div>
      </div>
    </div>
    <p class="text-[11px] text-slate-400 mt-4">รวม ${s.dish_count||0} เมนูในเดือนนี้ · ต้นทุนใช้สูตร BoM เดียวกับทุกหน้า</p>`;

  // Monthly profit trend for this branch (all months)
  try {
    const mt = await fetch(`${API}/api/pnl/monthly?branch=${encodeURIComponent(branch)}`).then(r => r.json()).catch(() => []);
    const arr = (Array.isArray(mt) ? mt : []).sort((a,b)=>(a.period_month||'').localeCompare(b.period_month||''));
    if (arr.length) mkChart('pnlDrillTrend', {
      type: 'bar',
      data: {
        labels: arr.map(r => fmtMonth(r.period_month)),
        datasets: [
          { label: 'รายได้', data: arr.map(r=>r.revenue||0), backgroundColor: '#4f46e533', borderColor:'#4f46e5', borderWidth:1, order:3 },
          { label: 'กำไรขั้นต้น', data: arr.map(r=>r.gross_profit||0), backgroundColor: '#10b98155', borderColor:'#10b981', borderWidth:1, order:2 },
          { label: 'GP %', data: arr.map(r=>r.gp_margin_pct||0), type:'line', borderColor:'#f59e0b', backgroundColor:'transparent', tension:0.3, yAxisID:'pct', order:1 },
        ]
      },
      options: {
        responsive:true, maintainAspectRatio:false,
        plugins:{ legend:{ position:'bottom', labels:{font:{size:10}} }, datalabels:{display:false} },
        scales:{ y:{ ticks:{ callback:v=>fmt(v) } }, pct:{ position:'right', grid:{drawOnChartArea:false}, ticks:{ callback:v=>v+'%' } } }
      }
    });
  } catch(e) {}
}

// ── More Analytics tab (#4 daypart, #5 mix-shift, #7 turnover, #9 forecast) ──
// ════════════════════════════════════════════════════════════════
// MOBILE OWNER SUMMARY
// ════════════════════════════════════════════════════════════════
function showFullDashboard() {
  document.body.classList.add('force-desktop');
  try { if (!activeTab) switchTab('home'); else loadActiveTab(); } catch (e) {}
  window.scrollTo(0, 0);
}
function showMobileSummary() {
  document.body.classList.remove('force-desktop');
  window.scrollTo(0, 0);
  loadMobileSummary(true);
}

// ── shared state + helpers ──
let _msView = 'summary', _msMonth = null, _msBranchesFilled = false,
    _msSalesGran = 'month', _msBranchSort = 'rev';
function msBranch() { return document.getElementById('msBranch')?.value || ''; }
function msPchip(v, goodUp = true) {
  if (v == null) return '<span class="ms-chip" style="background:#f1f5f9;color:#94a3b8">—</span>';
  const good = goodUp ? v >= 0 : v <= 0;
  const c = v === 0 ? '#64748b' : good ? '#10b981' : '#ef4444';
  const bg = v === 0 ? '#f1f5f9' : good ? '#dcfce7' : '#fee2e2';
  return `<span class="ms-chip" style="background:${bg};color:${c}">${v > 0 ? '▲' : v < 0 ? '▼' : ''} ${Math.abs(v)}%</span>`;
}
function msBar(w, col) {
  return `<div style="flex:1;background:var(--bg-subtle);border-radius:3px;overflow:hidden"><div style="width:${w}%;min-width:2px;height:8px;background:${col}"></div></div>`;
}

function msSwitchView(v) {
  _msView = v;
  ['summary', 'sales', 'branches', 'menu', 'profit'].forEach(x => {
    const el = document.getElementById('msv-' + x); if (el) el.style.display = x === v ? '' : 'none';
    document.getElementById('msnav-' + x)?.classList.toggle('active', x === v);
  });
  window.scrollTo(0, 0);
  msRenderActive();
}
function msRenderActive() {
  if (!_msMonth) { loadMobileSummary(true); return; }
  if (_msView === 'summary')      msRenderSummary();
  else if (_msView === 'branches') msRenderBranches();
  else if (_msView === 'sales')    msLoadSales();
  else if (_msView === 'menu')     msLoadMenu();
  else if (_msView === 'profit')   msLoadProfit();
}

// ── entry point: fetch shared month payload, then render the active view ──
async function loadMobileSummary(force) {
  const status = document.getElementById('msStatus');
  if (status) status.textContent = 'กำลังโหลด…';
  const br = msBranch();
  const mq = new URLSearchParams({ gran: 'month' }); if (br) mq.set('branch', br);
  try { _msMonth = await fetch(`${API}/api/sales/daily?${mq}`).then(r => r.json()); }
  catch (e) { if (status) status.textContent = 'โหลดไม่สำเร็จ — ลองรีเฟรช'; return; }
  const m = _msMonth;

  if (!_msBranchesFilled && (m.branches || []).length) {
    const sel = document.getElementById('msBranch');
    if (sel) {
      m.branches.slice().sort((a, b) => a.branch.localeCompare(b.branch, 'th'))
        .forEach(b => { const o = document.createElement('option'); o.value = b.branch; o.textContent = b.branch; sel.appendChild(o); });
      _msBranchesFilled = true;
    }
  }
  document.getElementById('msSubtitle').textContent =
    (br || 'ทุกสาขา') + ' · ' + (m.mtd ? `MTD ถึงวันที่ ${m.mtd_cutoff_day}` : 'เดือนล่าสุด') + ` (${(m.focus || '').slice(0, 7)})`;
  if (status) status.textContent = `${m.mom_basis === 'dow' ? 'เทียบเดือนก่อนถ่วงตามวันในสัปดาห์ (weekday-fair)' : 'เทียบเดือนก่อนแบบ MTD-fair'} · อัปเดต ${(m.focus || '').slice(0, 10)}`;
  msRenderActive();
}

// ── VIEW: สรุป ──
function msRenderSummary() {
  const m = _msMonth; if (!m) return;
  const t = m.total || {}, branches = m.branches || [];
  document.getElementById('msKpis').innerHTML = [
    { l: 'รายได้ (MTD)', v: fmt(t.revenue || 0), chip: msPchip(t.mom_pct), span: true, c: '#4338ca' },
    { l: 'ลูกค้า', v: fmtN(t.customers || 0), chip: msPchip(t.cust_mom_pct), c: '#0891b2' },
    { l: 'รายได้/หัว', v: t.rev_per_head != null ? fmt(t.rev_per_head) : '—', chip: msPchip(t.rph_mom_pct), c: '#7c3aed' },
  ].map(k => `<div class="ms-kpi" style="${k.span ? 'grid-column:1/-1;' : ''}">
      <p class="lbl">${k.l}</p><p class="val" style="color:${k.c}">${k.v}</p>
      <p class="sub">${k.chip} <span style="color:var(--text-muted,#94a3b8);font-size:11px">vs เดือนก่อน</span></p>
    </div>`).join('');

  const sig = m.anomalies || [];
  const sw = document.getElementById('msSignalsWrap');
  if (sig.length) {
    sw.style.display = '';
    document.getElementById('msSignals').innerHTML = sig.slice(0, 6).map(a => {
      const bad = (a.pct || 0) < 0;
      return `<div class="ms-row"><span style="font-size:16px">${bad ? '🔴' : '🟢'}</span>
        <span style="flex:1;font-size:13px;color:var(--text-primary,#1e293b)">${esc(a.msg)}</span></div>`;
    }).join('');
  } else { sw.style.display = 'none'; }

  const withMom = branches.filter(b => b.mom_pct != null);
  const best = withMom.slice().sort((a, b) => b.mom_pct - a.mom_pct)[0];
  const worst = withMom.slice().sort((a, b) => a.mom_pct - b.mom_pct)[0];
  const bwCard = (b, good) => !b ? '' : `<div class="ms-kpi" style="border-left:4px solid ${good ? '#10b981' : '#ef4444'}">
      <p class="lbl">${good ? '🏆 โตสุด' : '⚠️ ตกสุด'}</p>
      <p class="val" style="font-size:17px;color:var(--text-primary,#1e293b)">${esc(b.branch)}</p>
      <p class="sub">${msPchip(b.mom_pct)} · ${fmt(b.revenue)}</p></div>`;
  document.getElementById('msBestWorst').innerHTML = bwCard(best, true) + bwCard(worst, false);
}

// ── VIEW: สาขา ──
function msSetBranchSort(s) {
  _msBranchSort = s;
  ['rev', 'mom', 'rph'].forEach(x => document.getElementById('msSort' + x[0].toUpperCase() + x.slice(1))?.classList.toggle('ms-seg-on', x === s));
  msRenderBranches();
}
function msRenderBranches() {
  const m = _msMonth; if (!m) return;
  let branches = (m.branches || []).slice();
  if (_msBranchSort === 'rev') branches.sort((a, b) => (b.revenue || 0) - (a.revenue || 0));
  else if (_msBranchSort === 'mom') branches.sort((a, b) => (b.mom_pct ?? -1e9) - (a.mom_pct ?? -1e9));
  else if (_msBranchSort === 'rph') branches.sort((a, b) => (b.rev_per_head ?? -1e9) - (a.rev_per_head ?? -1e9));
  const maxRev = Math.max(1, ...branches.map(b => b.revenue || 0));
  document.getElementById('msBranches').innerHTML = branches.map(b => {
    const right = _msBranchSort === 'rph'
      ? `<span style="font-size:13px;color:var(--text-secondary,#475569);font-variant-numeric:tabular-nums">${b.rev_per_head != null ? fmt(b.rev_per_head) : '—'}/หัว</span>`
      : `<span style="font-size:13px;color:var(--text-secondary,#475569);font-variant-numeric:tabular-nums">${fmt(b.revenue)}</span>${msPchip(b.mom_pct)}`;
    return `<div class="ms-row">
      <span style="flex:1;min-width:0"><span style="font-size:13px;font-weight:500;color:var(--text-primary,#1e293b);display:block">${esc(b.branch)}</span>
      <span style="display:flex;align-items:center;gap:6px;margin-top:3px">${msBar(Math.round(100 * (b.revenue || 0) / maxRev), '#6366f1')}</span></span>
      <span style="display:flex;align-items:center;gap:6px;flex-shrink:0">${right}</span>
    </div>`;
  }).join('') || '<div class="ms-row">ไม่มีข้อมูล</div>';
}

// ── VIEW: ยอดขาย (trend) ──
function msSetSalesGran(g) {
  _msSalesGran = g;
  document.getElementById('msGranDay')?.classList.toggle('ms-seg-on', g === 'day');
  document.getElementById('msGranMonth')?.classList.toggle('ms-seg-on', g === 'month');
  msLoadSales();
}
async function msLoadSales() {
  const wrap = document.getElementById('msSalesTrend');
  wrap.innerHTML = '<div class="ms-row">กำลังโหลด…</div>';
  const q = new URLSearchParams({ gran: _msSalesGran, trend: '12' });
  const br = msBranch(); if (br) q.set('branch', br);
  let d; try { d = await fetch(`${API}/api/sales/daily?${q}`).then(r => r.json()); } catch (e) { wrap.innerHTML = '<div class="ms-row">โหลดไม่สำเร็จ</div>'; return; }
  // latest day/month FIRST (owner request 2026-07-20) — the API returns the
  // trend oldest→newest; reverse for display so index 0 = the newest period.
  const tr = (d.trend || []).slice().reverse();
  const maxV = Math.max(1, ...tr.map(x => x.revenue || 0));
  const lblOf = x => _msSalesGran === 'month' ? (x.start || '').slice(0, 7) : (x.start || '').slice(5, 10);
  wrap.innerHTML = tr.map((x, i) => {
    const w = Math.round(100 * (x.revenue || 0) / maxV);
    const latest = i === 0;
    return `<div class="ms-row">
      <span style="width:58px;font-size:11px;font-weight:${latest ? '700' : '400'};color:${latest ? 'var(--text-primary,#1e293b)' : 'var(--text-muted,#64748b)'}">${lblOf(x)}</span>
      ${msBar(w, latest ? '#4338ca' : '#a5b4fc')}
      <span style="width:64px;text-align:right;font-size:12px;font-weight:${latest ? '700' : '400'};font-variant-numeric:tabular-nums;color:var(--text-primary,#1e293b)">${fmt(x.revenue || 0)}</span>
    </div>`;
  }).join('') || '<div class="ms-row">ไม่มีข้อมูล</div>';
}

// ── VIEW: เมนู ──
async function msLoadMenu() {
  const wrap = document.getElementById('msMenu');
  const rWrap = document.getElementById('msMenuRisers');
  wrap.innerHTML = '<div class="ms-row">กำลังโหลด…</div>';
  if (rWrap) rWrap.innerHTML = '<div class="ms-row">กำลังโหลด…</div>';
  // limit=100: one payload feeds BOTH lists — top-20 by qty (below) and the
  // MoM risers. The top-100-by-qty pool doubles as a volume floor so a tiny-
  // base SKU can't fake a huge % (debug-mantra rule 5); qty_mom_pct itself is
  // MTD-fair (same day-range both months) and the food filter is the canonical
  // _FOOD_CATEGORY_FILTER incl. the durian whitelist (rule 23).
  const q = new URLSearchParams({ sort: 'qty', limit: '100' });
  const br = msBranch(); if (br) q.set('branch', br);
  const mon = (_msMonth?.focus || '').slice(0, 7); if (mon) q.set('month', mon);
  let d; try { d = await fetch(`${API}/api/sales/top_ordered?${q}`).then(r => r.json()); } catch (e) { wrap.innerHTML = '<div class="ms-row">โหลดไม่สำเร็จ</div>'; if (rWrap) rWrap.innerHTML = ''; return; }
  const pool = (d.items || []);

  // ── 📈 มาแรงเดือนนี้: biggest positive qty-MoM among meaningful-volume menus ──
  if (rWrap) {
    const MIN_QTY = 1000;
    const risers = pool
      .filter(x => (x.qty || 0) >= MIN_QTY && x.qty_mom_pct != null && x.qty_mom_pct > 0)
      .sort((a, b) => b.qty_mom_pct - a.qty_mom_pct)
      .slice(0, 8);
    const maxM = Math.max(1, ...risers.map(x => x.qty_mom_pct || 0));
    const capNote = d.mom_basis === 'mtd'
      ? `เทียบช่วงวันที่ 1–${d.mtd_cutoff_day || ''} ของเดือนก่อน (แฟร์กับเดือนที่ยังไม่จบ)`
      : 'เทียบเดือนก่อนหน้า';
    rWrap.innerHTML = (risers.map((x, i) => {
      const nm = (x.description || x.sku_code || '').replace(/^\((?:A|B)\)\s*/, '');
      return `<div class="ms-row">
        <span style="width:18px;font-size:11px;color:var(--text-muted,#94a3b8)">${i + 1}</span>
        <span style="flex:1;min-width:0"><span style="font-size:13px;color:var(--text-primary,#1e293b);display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(nm)}</span>
        <span style="display:flex;align-items:center;gap:6px;margin-top:3px">${msBar(Math.round(100 * (x.qty_mom_pct || 0) / maxM), '#10b981')}</span></span>
        <span style="width:84px;text-align:right;flex-shrink:0">${msPchip(Math.round(x.qty_mom_pct * 10) / 10)}<br><span style="font-size:11px;color:var(--text-muted,#64748b);font-variant-numeric:tabular-nums">${fmtN(x.qty || 0)} ชิ้น</span></span>
      </div>`;
    }).join('') || '<div class="ms-row">เดือนนี้ยังไม่มีเมนูยอดนิยมที่เพิ่มขึ้นชัดเจน</div>')
      + `<div class="ms-row" style="border:0;padding-top:6px"><span style="font-size:10px;color:var(--text-muted,#94a3b8)">${capNote} · เฉพาะเมนูยอด ≥ ${fmtN(MIN_QTY)} ชิ้น</span></div>`;
  }

  // ── 🍣 เมนูขายดี top-20 (unchanged view; first 20 of the qty-sorted pool) ──
  const items = pool.slice(0, 20);
  const maxQ = Math.max(1, ...items.map(x => x.qty || 0));
  wrap.innerHTML = items.map((x, i) => {
    const nm = (x.description || x.sku_code || '').replace(/^\((?:A|B)\)\s*/, '');
    return `<div class="ms-row">
      <span style="width:18px;font-size:11px;color:var(--text-muted,#94a3b8)">${i + 1}</span>
      <span style="flex:1;min-width:0"><span style="font-size:13px;color:var(--text-primary,#1e293b);display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(nm)}</span>
      <span style="display:flex;align-items:center;gap:6px;margin-top:3px">${msBar(Math.round(100 * (x.qty || 0) / maxQ), '#0891b2')}</span></span>
      <span style="width:74px;text-align:right;flex-shrink:0"><span style="font-size:13px;font-weight:600;font-variant-numeric:tabular-nums">${fmtN(x.qty || 0)}</span><br>${msPchip(x.qty_mom_pct)}</span>
    </div>`;
  }).join('') || '<div class="ms-row">ไม่มีข้อมูล</div>';
}

// ── VIEW: กำไร/ต้นทุน ──
async function msLoadProfit() {
  const kp = document.getElementById('msProfitKpis'), br = document.getElementById('msProfitBranches');
  kp.innerHTML = ''; br.innerHTML = '<div class="ms-row">กำลังโหลด…</div>';
  const q = new URLSearchParams({ gran: 'month' });
  const b = msBranch(); if (b) q.set('branch', b);
  let d; try { d = await fetch(`${API}/api/sales/cost-per-head?${q}`).then(r => r.json()); } catch (e) { br.innerHTML = '<div class="ms-row">โหลดไม่สำเร็จ</div>'; return; }
  const t = d.total || {};
  const cpColor = cp => cp == null ? '#94a3b8' : cp >= 70 ? '#ef4444' : cp >= 55 ? '#f59e0b' : '#10b981';
  kp.innerHTML = [
    { l: 'รายได้/หัว', v: t.rev_per_head != null ? fmt(t.rev_per_head) : '—', c: '#0891b2' },
    { l: 'ต้นทุน/หัว (BoM)', v: t.cost_per_head != null ? fmt(t.cost_per_head) : '—', c: '#ef4444' },
    { l: 'GP/หัว', v: t.gp_per_head != null ? fmt(t.gp_per_head) : '—', c: '#10b981' },
    { l: 'Food cost %', v: t.cost_pct != null ? t.cost_pct + '%' : '—', c: cpColor(t.cost_pct) },
  ].map(k => `<div class="ms-kpi"><p class="lbl">${k.l}</p><p class="val" style="font-size:20px;color:${k.c}">${k.v}</p></div>`).join('');
  const branches = (d.branches || []).filter(r => r.cost_pct != null);
  br.innerHTML = branches.map(r => `<div class="ms-row">
      <span style="flex:1;font-size:13px;color:var(--text-primary,#1e293b)">${esc(r.branch)}</span>
      <span style="font-size:12px;color:var(--text-muted,#64748b);font-variant-numeric:tabular-nums">${r.cost_per_head != null ? fmt(r.cost_per_head) : '—'}/หัว</span>
      <span class="ms-chip" style="background:${cpColor(r.cost_pct)}22;color:${cpColor(r.cost_pct)}">${r.cost_pct}%</span>
    </div>`).join('') || '<div class="ms-row">ไม่มีข้อมูล</div>';
}

async function loadMoreAnalytics() {
  const gen = loadGen;
  const p = new URLSearchParams();
  if (getBranch())    p.set('branch', getBranch());
  if (getMonthFrom()) p.set('month_from', getMonthFrom());
  if (getMonthTo())   p.set('month_to', getMonthTo());
  const q = p.toString();
  const bq = getBranch() ? ('branch=' + encodeURIComponent(getBranch())) : '';
  // Turnover is ALWAYS all-branches (comparison).
  const monthQ = new URLSearchParams();
  if (getMonthFrom()) monthQ.set('month_from', getMonthFrom());
  if (getMonthTo())   monthQ.set('month_to', getMonthTo());
  const [dp, turn, fc] = await Promise.all([
    getJSON('/api/analysis/daypart-economics?' + q, { gen }).catch(()=>null),
    getJSON('/api/analysis/table-turnover?' + monthQ.toString(), { gen }).catch(()=>null),
    getJSON('/api/forecast?mode=trend&' + bq, { gen }).catch(()=>null),
  ]);
  if (gen !== loadGen) return;

  const dayparts = (dp && dp.dayparts) || [];
  if (dp) {
    const maxPct = Math.max(1, ...dayparts.map(x=>x.revenue_pct||0));
    const maxGp = Math.max(...dayparts.map(x=>x.est_gp_margin_pct||0));
    const minGp = Math.min(...dayparts.map(x=>x.est_gp_margin_pct==null?999:x.est_gp_margin_pct));
    const gpCol = m => m==null ? '#94a3b8' : m>=55 ? '#10b981' : m>=45 ? '#0891b2' : '#f59e0b';
    document.getElementById('more-daypart').innerHTML = dayparts.map(x=>{
      const w = Math.round(100*(x.revenue_pct||0)/maxPct);
      const isTopGp = x.est_gp_margin_pct!=null && x.est_gp_margin_pct===maxGp;
      return `<tr class="border-b border-slate-50">
        <td class="py-1.5 pr-2 font-medium">${esc(x.label)}</td>
        <td class="py-1.5 pr-2 text-right tabular-nums">${fmt(x.revenue)}</td>
        <td class="py-1.5 pr-2" style="min-width:96px">
          <div class="flex items-center gap-1.5">
            <div class="flex-1" style="background:var(--bg-subtle);border-radius:3px;overflow:hidden"><div style="width:${w}%;min-width:2px;height:8px;background:#6366f1"></div></div>
            <span class="text-[10px] text-slate-400 tabular-nums" style="width:30px">${x.revenue_pct}%</span>
          </div>
        </td>
        <td class="py-1.5 pr-2 text-right tabular-nums">${fmtN(x.covers)}</td>
        <td class="py-1.5 pr-2 text-right tabular-nums">${x.rev_per_cover!=null?fmtN(x.rev_per_cover):'—'}</td>
        <td class="py-1.5 pr-2 text-right tabular-nums">${x.avg_bill!=null?fmtN(x.avg_bill):'—'}</td>
        <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${x.drink_pct}%</td>
        <td class="py-1.5 text-right tabular-nums font-semibold" style="color:${gpCol(x.est_gp_margin_pct)}">${fmt(x.est_gross_profit)}${x.est_gp_margin_pct!=null?' · '+x.est_gp_margin_pct+'%':''}${isTopGp?' 👑':''}</td>
      </tr>`;
    }).join('');
    const topRev = dayparts.slice().sort((a,b)=>(b.revenue||0)-(a.revenue||0))[0];
    document.getElementById('more-daypart-note').textContent = topRev ? `ช่วงพีค: ${topRev.label}` : '';
  }

  if (fc) {
    document.getElementById('more-forecast-note').textContent =
      fc.trend ? `เทรนด์ ${fmt(fc.trend.slope_per_month)}/เดือน · R²=${fc.trend.r_squared}` : (fc.note||'');
    const slope = fc.trend ? fc.trend.slope_per_month : null;
    const fb = document.getElementById('more-forecast-badge');
    if (fb) {
      if (slope == null) { fb.textContent = ''; }
      else if (slope > 0) { fb.textContent = '▲ ขาขึ้น'; fb.style.color = '#10b981'; }
      else if (slope < 0) { fb.textContent = '▼ ขาลง'; fb.style.color = '#ef4444'; }
      else { fb.textContent = '→ ทรงตัว'; fb.style.color = '#64748b'; }
    }
    const hist = fc.history||[], fore = fc.forecast||[];
    const labels = [...hist.map(h=>h.period), ...fore.map(f=>f.period)];
    const histData = [...hist.map(h=>h.revenue), ...fore.map(()=>null)];
    const foreData = hist.map(()=>null);
    if (hist.length) foreData[hist.length-1] = hist[hist.length-1].revenue;  // connect
    fore.forEach(f=>foreData.push(f.revenue));
    const loData = [...hist.map(()=>null), ...fore.map(f=>f.lo)];
    const hiData = [...hist.map(()=>null), ...fore.map(f=>f.hi)];
    mkChart('more-forecast', { type:'line',
      data:{ labels, datasets:[
        { label:'จริง', data:histData, borderColor:'#4f46e5', spanGaps:false },
        { label:'พยากรณ์', data:foreData, borderColor:'#0891b2', borderDash:[6,4], spanGaps:true },
        { label:'hi', data:hiData, borderColor:'transparent', backgroundColor:'rgba(8,145,178,.12)', fill:'+1', pointRadius:0 },
        { label:'lo', data:loData, borderColor:'transparent', backgroundColor:'rgba(8,145,178,.12)', fill:false, pointRadius:0 },
      ]},
      options:{ responsive:true, maintainAspectRatio:false,
        plugins:{ legend:{ display:true, labels:{ filter:li=>li.text==='จริง'||li.text==='พยากรณ์' } }, datalabels:{display:false} },
        scales:{ y:{ ticks:{ callback:v=>fmt(v) } } } } });
  }

  const tbr = (turn && turn.branches) || [];
  if (turn) {
    const turns = tbr.filter(b=>b.avg_turn_min!=null).map(b=>b.avg_turn_min);
    const maxT = Math.max(1, ...turns), minT = Math.min(...turns);
    const med = turns.length ? turns.slice().sort((a,b)=>a-b)[Math.floor(turns.length/2)] : 0;
    const medal = i => i===0?'🥇':i===1?'🥈':i===2?'🥉':(i+1);
    document.getElementById('more-turnover').innerHTML = tbr.map((b,i)=>{
      const t = b.avg_turn_min;
      const fast = t!=null && t<=med;
      const col = t==null ? '#94a3b8' : fast ? '#10b981' : '#f59e0b';
      const bg  = t==null ? '#f8fafc' : fast ? '#ecfdf5' : '#fffbeb';
      return `<div class="rounded-lg p-2.5 text-center" style="background:${bg};border:1px solid ${col}33">
        <div class="text-[11px] font-medium text-slate-500 truncate" title="${esc(b.branch)}">${medal(i)} ${esc(b.branch)}</div>
        <div class="font-bold tabular-nums leading-tight" style="color:${col};font-size:1.35rem">${t!=null?t:'—'}<span class="text-[10px] font-normal text-slate-400"> น./รอบ</span></div>
        <div class="text-[10px] text-slate-400 tabular-nums mt-0.5">${fmtN(b.turns)} รอบ · ${b.avg_covers_per_turn!=null?b.avg_covers_per_turn:'—'} คน/รอบ</div>
      </div>`;
    }).join('') || '<div class="col-span-full py-3 text-center text-slate-400 text-sm">—</div>';
  }

  // ── auto-generated highlight chips (from the three datasets above) ──
  const chips = [];
  if (dayparts.length) {
    const topRev = dayparts.slice().sort((a,b)=>(b.revenue||0)-(a.revenue||0))[0];
    chips.push({ icon:'🕒', label:'ช่วงทำเงินสูงสุด', val:esc(topRev.label),
                 sub:`${fmt(topRev.revenue)} · ${topRev.revenue_pct}% ของรายได้`, c:'#6366f1' });
    const wGp = dayparts.filter(x=>x.est_gp_margin_pct!=null).sort((a,b)=>b.est_gp_margin_pct-a.est_gp_margin_pct)[0];
    if (wGp) chips.push({ icon:'💰', label:'กำไร(margin)ดีสุด', val:esc(wGp.label),
                 sub:`GP ${wGp.est_gp_margin_pct}% · ดื่ม ${wGp.drink_pct}%`, c:'#10b981' });
  }
  if (tbr.length) {
    const fastest = tbr.filter(b=>b.avg_turn_min!=null).sort((a,b)=>a.avg_turn_min-b.avg_turn_min)[0];
    if (fastest) chips.push({ icon:'⚡', label:'หมุนโต๊ะเร็วสุด', val:esc(fastest.branch),
                 sub:`${fastest.avg_turn_min} นาที/รอบ`, c:'#0891b2' });
  }
  if (fc && fc.trend) {
    const up = fc.trend.slope_per_month > 0;
    chips.push({ icon: up?'📈':'📉', label:'เทรนด์รายได้', val: up?'ขาขึ้น':'ขาลง',
                 sub:`${fmt(fc.trend.slope_per_month)}/เดือน · R²=${fc.trend.r_squared}`, c: up?'#10b981':'#ef4444' });
  }
  const hc = document.getElementById('more-highlights');
  if (hc) hc.innerHTML = chips.map(c=>`
    <div class="card p-3" style="border-left:4px solid ${c.c}">
      <p class="text-[11px] text-slate-400">${c.icon} ${c.label}</p>
      <p class="text-lg font-bold leading-tight" style="color:${c.c}">${c.val}</p>
      <p class="text-[11px] text-slate-500 mt-0.5">${c.sub}</p>
    </div>`).join('');
}

// ── Boot ───────────────────────────────────────────────────────
// Register plugins + apply global chart defaults if the vendor scripts are
// already loaded. If they're still loading (async), ensureChartSetup() no-ops
// and waitForCharts() below retries — so no noisy "not loaded" errors.
ensureChartSetup();

// Idempotent: registers plugins + applies global defaults the first time it can.
// Called from boot AND from mkChart, so it works regardless of the vendor-load
// race (the inline script may parse before chart.umd.min.js finishes loading).
function ensureChartSetup() {
  if (_chartSetupDone || typeof Chart === 'undefined') return;
  if (typeof ChartDataLabels !== 'undefined') Chart.register(ChartDataLabels);
  if (typeof ChartAnnotation !== 'undefined') Chart.register(ChartAnnotation);
  setupChartDefaults();
  _chartSetupDone = true;
}

// Global Chart.js defaults — one place that makes EVERY chart more legible.
// Per-chart options still override these; this only fills the gaps (typography,
// tooltips, rounded bars, tidy points/legends).
function setupChartDefaults() {
  if (typeof Chart === 'undefined') return;
  const FONT = "'Prompt','Noto Sans Thai',-apple-system,'Segoe UI',sans-serif";
  Chart.defaults.font.family = FONT;
  Chart.defaults.font.size = 11.5;
  Chart.defaults.color = '#475569';
  Chart.defaults.devicePixelRatio = Math.min(window.devicePixelRatio || 1, 2); // crisp lines

  // Rounded, cleaner bars
  Chart.defaults.elements.bar.borderRadius = 6;
  Chart.defaults.elements.bar.borderSkipped = false;
  Chart.defaults.elements.bar.maxBarThickness = 46;

  // Smoother lines, smaller resting points that grow on hover (less clutter)
  Chart.defaults.elements.line.tension = 0.32;
  Chart.defaults.elements.line.borderWidth = 2.5;
  Chart.defaults.elements.point.radius = 2.2;     // visible (keeps scatter charts alive) but small
  Chart.defaults.elements.point.hoverRadius = 6;
  Chart.defaults.elements.point.hitRadius = 10;

  // Tidier legends
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.boxWidth = 8;
  Chart.defaults.plugins.legend.labels.boxHeight = 8;
  Chart.defaults.plugins.legend.labels.padding = 14;

  // Readable tooltips: dark card, generous padding, bold title, thousand-separated
  // numbers. A custom per-chart tooltip.callbacks.label still overrides this.
  const tt = Chart.defaults.plugins.tooltip;
  tt.backgroundColor = 'rgba(15,23,42,0.94)';
  tt.titleColor = '#f8fafc';
  tt.bodyColor = '#e2e8f0';
  tt.titleFont = { family: FONT, size: 12.5, weight: '600' };
  tt.bodyFont = { family: FONT, size: 12 };
  tt.padding = 11;
  tt.cornerRadius = 9;
  tt.boxPadding = 5;
  tt.usePointStyle = true;
  tt.borderColor = 'rgba(148,163,184,0.25)';
  tt.borderWidth = 1;
  tt.callbacks.label = function(ctx) {
    try {
      let label = ctx.dataset.label ? ctx.dataset.label + ': ' : '';
      let v = ctx.parsed;
      if (v && typeof v === 'object') v = (v.y ?? v.r ?? v.x);
      if (typeof v !== 'number' || !isFinite(v)) return label + ctx.formattedValue;
      const dp = Math.abs(v) > 0 && Math.abs(v) < 100 ? 2 : 0;
      return label + v.toLocaleString('en-US', { maximumFractionDigits: dp });
    } catch (e) { return ctx.formattedValue; }
  };

  // Datalabels (the on-chart numbers): strip IEEE-754 dust, change NOTHING else.
  // The plugin's default formatter prints the RAW value, so any unrounded
  // arithmetic leaks float error onto the chart (menu drill 2026-08: pen
  // 0.5777 × 100 → "57.769999999999996"). Rounding to 4 dp kills the dust
  // while keeping every intentionally-precise label (qty/head "1.327" stays
  // "1.327") and adding no thousand separators. null still returns null (label
  // hidden), and a per-chart `datalabels.formatter` still overrides this.
  if (Chart.defaults.plugins.datalabels) {
    Chart.defaults.plugins.datalabels.formatter = function (v) {
      return (typeof v === 'number' && isFinite(v)) ? String(parseFloat(v.toFixed(4))) : v;
    };
  }
}
init().catch(console.error);

// Vendor-load race recovery: if any chart was requested before Chart.js was ready,
// wait for it to load, then re-render the active tab once so the charts appear.
(function waitForCharts(tries) {
  if (typeof Chart !== 'undefined') {
    ensureChartSetup();
    if (_chartsPending) { _chartsPending = false; try { loadActiveTab(); } catch(e){} }
    return;
  }
  if (tries > 0) setTimeout(() => waitForCharts(tries - 1), 150);
})(40);   // up to ~6s

// ════════════════════════════════════════════════════════════════
// Phase 4 — Ingredient-level BoM Composition Deep-dive
// ════════════════════════════════════════════════════════════════
const BOM_TYPE_COLORS = {
  protein:  '#ef4444',
  carb:     '#f59e0b',
  veg:      '#22c55e',
  sauce:    '#a855f7',
  oil:      '#eab308',
  seasoning:'#64748b',
  other:    '#94a3b8',
};
const BOM_TYPE_LABEL_TH = {
  protein:'โปรตีน', carb:'คาร์บ', veg:'ผัก', sauce:'ซอส', oil:'น้ำมัน/ไขมัน', seasoning:'เครื่องปรุง', other:'อื่นๆ',
};
let _bomInited = false;
let _bomLastConsKey = '';

async function initBomDeepDive() {
  if (!_bomInited) {
    _bomInited = true;
    try {
      const res = await fetch(API + '/api/bom/sku-list');
      const data = await res.json();
      const sel = document.getElementById('bomSkuPicker');
      if (sel) {
        if (!data.skus || !data.skus.length) {
          sel.innerHTML = '<option value="">— ไม่มีข้อมูล BoM —</option>';
        } else {
          sel.innerHTML = data.skus.map(s =>
            `<option value="${esc(s.sku_code)}">${esc(s.sku_code)} · ${esc(s.description || '')} (${(s.total_cost||0).toFixed(2)})</option>`
          ).join('');
          sel.value = data.skus[0].sku_code;
          loadBomComposition();
        }
      }
    } catch (e) { console.error('initBomDeepDive sku-list failed:', e); }
  }
  // Always (re)load period consumption when subtab is opened — filters may have changed
  const fk = _filterKey();
  if (fk !== _bomLastConsKey) {
    _bomLastConsKey = fk;
    loadBomConsumption();
  }
}

async function loadBomComposition() {
  const sel = document.getElementById('bomSkuPicker');
  const sku = sel && sel.value;
  if (!sku) return;
  try {
    const res = await fetch(API + '/api/bom/composition?sku=' + encodeURIComponent(sku));
    if (!res.ok) {
      document.getElementById('bomCompHeader').innerHTML = '<span class="text-red-600">ไม่พบข้อมูล BoM สำหรับ ' + sku + '</span>';
      return;
    }
    const d = await res.json();
    renderBomComposition(d);
  } catch (e) {
    console.error('loadBomComposition failed:', e);
  }
}

function renderBomComposition(d) {
  document.getElementById('bomCompHeader').innerHTML =
    `<span class="font-semibold text-slate-700">${esc(d.sku_code)}</span> · ${esc(d.description || '')} · ` +
    `<span class="font-semibold text-orange-700">ต้นทุนรวม ${(d.total_cost||0).toFixed(2)}</span> · ${d.ingredients.length} วัตถุดิบ`;

  // Bar chart (descending cost)
  const sorted = [...d.ingredients].sort((a,b) => b.cost - a.cost);
  const barCanvas = document.getElementById('bomCompBar');
  if (chartInstances.bomCompBar) { try { chartInstances.bomCompBar.destroy(); } catch(e){} }
  chartInstances.bomCompBar = new Chart(barCanvas, {
    type: 'bar',
    data: {
      labels: sorted.map(x => (x.name || x.code).slice(0, 24)),
      datasets: [{
        label: 'ต้นทุน ()',
        data: sorted.map(x => x.cost),
        backgroundColor: sorted.map(x => BOM_TYPE_COLORS[x.type] || BOM_TYPE_COLORS.other),
        borderRadius: 4,
      }],
    },
    options: {
      indexAxis: 'y',
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        datalabels: { display: false },
        tooltip: { callbacks: { label: ctx => ''  + ctx.parsed.x.toFixed(2) + ' (' + (sorted[ctx.dataIndex].pct||0) + '%)' } },
      },
      scales: {
        x: { ticks: { color:'#94a3b8', font:{size:10}, callback: v => ''+v.toFixed(1) }, grid:{color:'#f1f5f9'} },
        y: { ticks: { color:'#334155', font:{size:10} }, grid:{display:false} },
      },
    },
  });

  // Donut by type
  const tEntries = Object.entries(d.by_type || {});
  const donut = document.getElementById('bomCompDonut');
  if (chartInstances.bomCompDonut) { try { chartInstances.bomCompDonut.destroy(); } catch(e){} }
  chartInstances.bomCompDonut = new Chart(donut, {
    type: 'doughnut',
    data: {
      labels: tEntries.map(([k]) => BOM_TYPE_LABEL_TH[k] || k),
      datasets: [{
        data: tEntries.map(([_,v]) => v),
        backgroundColor: tEntries.map(([k]) => BOM_TYPE_COLORS[k] || BOM_TYPE_COLORS.other),
        borderWidth: 2, borderColor: '#fff',
      }],
    },
    options: {
      responsive:true, maintainAspectRatio:false, cutout:'55%',
      plugins: {
        legend: { position:'right', labels:{color:'#334155', font:{size:11}, boxWidth:12} },
        datalabels: { color:'#fff', font:{weight:'bold', size:10}, formatter: v => (Number(v)||0).toFixed(1)+'%' },
        tooltip: { callbacks: { label: ctx => ctx.label + ': ' + (Number(ctx.parsed)||0).toFixed(1) + '%' } },
      },
    },
  });

  // Table
  const tbody = document.getElementById('bomCompTable');
  tbody.innerHTML = sorted.map((x,i) => `
    <tr class="border-b border-slate-100 hover:bg-orange-50/40">
      <td class="py-1.5 pr-2 text-slate-400">${i+1}</td>
      <td class="py-1.5 pr-2 font-mono text-[11px] text-slate-500">${x.code || '—'}</td>
      <td class="py-1.5 pr-2 text-slate-700">${x.name || '—'}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${(x.qty||0).toFixed(2)}</td>
      <td class="py-1.5 pr-2 text-slate-500">${x.unit || ''}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-semibold">${(x.cost||0).toFixed(2)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${(x.pct||0).toFixed(1)}%</td>
      <td class="py-1.5 pr-2"><span class="px-1.5 py-0.5 rounded text-[10px] text-white" style="background:${BOM_TYPE_COLORS[x.type]||BOM_TYPE_COLORS.other}">${BOM_TYPE_LABEL_TH[x.type]||x.type}</span></td>
    </tr>
  `).join('');
}

async function loadBomConsumption() {
  const params = new URLSearchParams();
  if (getMonthFrom()) params.set('month_from', getMonthFrom());
  if (getMonthTo())   params.set('month_to',   getMonthTo());
  if (getBranch())    params.set('branch',     getBranch());
  params.set('limit', '20');
  if (document.getElementById('bomConsGroupFamily')?.checked) params.set('group_family', 'true');
  try {
    const res = await fetch(API + '/api/bom/consumption-analysis?' + params.toString());
    const d = await res.json();
    renderBomConsumption(d);
  } catch (e) {
    console.error('loadBomConsumption failed:', e);
  }
}

function renderBomConsumption(d) {
  // KPI chips
  const kpis = document.getElementById('bomConsKpis');
  kpis.innerHTML = `
    <div class="px-3 py-1.5 bg-cyan-50 rounded-lg border border-cyan-100 text-xs">
      <span class="text-slate-500">ต้นทุน BoM รวม:</span>
      <span class="font-semibold text-cyan-800 ml-1">${fmt(d.grand_total_cost || 0)}</span>
    </div>
    <div class="px-3 py-1.5 bg-slate-50 rounded-lg border border-slate-200 text-xs">
      <span class="text-slate-500">วัตถุดิบที่ใช้:</span>
      <span class="font-semibold text-slate-800 ml-1">${d.n_ingredients_total || 0} รายการ</span>
    </div>
    <div class="px-3 py-1.5 bg-slate-50 rounded-lg border border-slate-200 text-xs">
      <span class="text-slate-500">ช่วงเวลา:</span>
      <span class="font-semibold text-slate-800 ml-1">${d.month_from || '—'} → ${d.month_to || '—'}</span>
    </div>
  `;

  // Top-20 bar
  const top = (d.ingredients || []).slice(0, 20);
  const canvas = document.getElementById('bomConsTopBar');
  if (chartInstances.bomConsTopBar) { try { chartInstances.bomConsTopBar.destroy(); } catch(e){} }
  chartInstances.bomConsTopBar = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: top.map(x => (x.name || x.code).slice(0, 28)),
      datasets: [{
        label: 'ต้นทุนรวม ()',
        data: top.map(x => x.total_cost),
        backgroundColor: top.map(x => BOM_TYPE_COLORS[x.type] || BOM_TYPE_COLORS.other),
        borderRadius: 4,
      }],
    },
    options: {
      indexAxis: 'y',
      responsive:true, maintainAspectRatio:false,
      plugins: {
        legend: { display: false },
        datalabels: { display: false },
        tooltip: { callbacks: {
          label: ctx => {
            const r = top[ctx.dataIndex];
            return [''  + fmt(r.total_cost) + ' (' + r.pct + '%)',
                    'qty: ' + fmt(r.total_qty) + ' ' + (r.unit||''),
                    'in ' + r.n_menus + ' menus'];
          } } },
      },
      scales: {
        x: { ticks: { color:'#94a3b8', font:{size:10}, callback: v => ''+fmt(v) }, grid:{color:'#f1f5f9'} },
        y: { ticks: { color:'#334155', font:{size:10} }, grid:{display:false} },
      },
    },
  });

  // Type donut
  const tEntries = Object.entries(d.by_type || {});
  const donut = document.getElementById('bomConsTypeDonut');
  if (chartInstances.bomConsTypeDonut) { try { chartInstances.bomConsTypeDonut.destroy(); } catch(e){} }
  chartInstances.bomConsTypeDonut = new Chart(donut, {
    type: 'doughnut',
    data: {
      labels: tEntries.map(([k]) => BOM_TYPE_LABEL_TH[k] || k),
      datasets: [{
        data: tEntries.map(([_,v]) => v),
        backgroundColor: tEntries.map(([k]) => BOM_TYPE_COLORS[k] || BOM_TYPE_COLORS.other),
        borderWidth: 2, borderColor:'#fff',
      }],
    },
    options: {
      responsive:true, maintainAspectRatio:false, cutout:'55%',
      plugins: {
        legend: { position:'right', labels:{color:'#334155', font:{size:11}, boxWidth:12} },
        datalabels: { color:'#fff', font:{weight:'bold', size:10}, formatter: v => (Number(v)||0).toFixed(1)+'%' },
      },
    },
  });

  // Benchmark badges
  const bench = (d.benchmarks && d.benchmarks.ayce_buffet) || {};
  const status = d.benchmark_status || {};
  const benchLines = [
    `<p class="text-[11px] font-semibold text-slate-600">เทียบ benchmark: Top AYCE buffet (Japan/KR)</p>`,
  ];
  ['protein','carb','veg','sauce','oil','seasoning','other'].forEach(t => {
    const r = bench[t];
    const s = status[t];
    if (!r) return;
    const actual = s ? s.pct : 0;
    let color = 'bg-slate-200 text-slate-700';
    let icon = '—';
    if (s && s.status === 'in')    { color = 'bg-emerald-100 text-emerald-700 border border-emerald-200'; icon = '✓'; }
    else if (s && s.status === 'over')  { color = 'bg-amber-100 text-amber-700 border border-amber-200'; icon = '↑'; }
    else if (s && s.status === 'under') { color = 'bg-amber-100 text-amber-700 border border-amber-200'; icon = '↓'; }
    benchLines.push(`
      <div class="flex items-center justify-between text-[11px] px-2 py-1 rounded ${color}">
        <span class="font-medium">${icon} ${BOM_TYPE_LABEL_TH[t]||t}</span>
        <span class="tabular-nums">${actual.toFixed(1)}% <span class="text-slate-500">(target ${r[0]}–${r[1]}%)</span></span>
      </div>
    `);
  });
  benchLines.push(`<p class="text-[10px] text-slate-400 mt-1 italic">Fine-dining Japanese: protein 50–60%, carb 5–10%, veg 15–20%</p>`);
  document.getElementById('bomConsBench').innerHTML = benchLines.join('');
}

// Reload BoM consumption when global filters change
const _origLoadActiveTab = typeof loadActiveTab === 'function' ? loadActiveTab : null;
if (_origLoadActiveTab) {
  window.loadActiveTab = function() {
    _bomLastConsKey = '';   // invalidate so next switch into bomdeep reloads
    return _origLoadActiveTab.apply(this, arguments);
  };
}

// ════════════════════════════════════════════════════════════════════════════
// Phase 11 — Cost Recalibration Tool
// ════════════════════════════════════════════════════════════════════════════
let _recalibData = null;

async function loadRecalibration() {
  const target = parseFloat(document.getElementById('recalibTarget').value || '65');
  const limit  = parseInt(document.getElementById('recalibLimit').value || '50', 10);
  const mf = getMonthFrom(), mt = getMonthTo();
  const params = new URLSearchParams({ target_pct: target, limit });
  if (mf) params.set('month_from', mf);
  if (mt) params.set('month_to',   mt);
  const body = document.getElementById('recalibBody');
  body.innerHTML = '<tr><td colspan="9" class="py-6 text-center text-slate-400">⏳ Loading…</td></tr>';
  try {
    const data = await fetch(`${API}/api/cost/recalibration?${params}`).then(r => r.json());
    _recalibData = data;
    _renderRecalibSummary();
    _renderRecalibTable();
  } catch (e) {
    body.innerHTML = `<tr><td colspan="9" class="py-6 text-center text-red-500">โหลดล้มเหลว: ${e.message}</td></tr>`;
  }
}

function _renderRecalibSummary() {
  const d = _recalibData;
  if (!d) return;
  const cur = d.current_food_cost_pct, tgt = d.target_food_cost_pct;
  const inRange = cur >= 55 && cur <= 70;
  const curColor = inRange ? '#10b981' : cur > 70 ? '#ef4444' : '#f59e0b';
  document.getElementById('recalibSummary').innerHTML = `
    <div class="bg-white rounded-lg p-2.5 border border-amber-200 text-center">
      <p class="text-[10px] text-slate-500">รายได้ (period)</p>
      <p class="text-sm font-bold text-slate-800">${(d.current_revenue/1e6).toFixed(1)}M</p>
    </div>
    <div class="bg-white rounded-lg p-2.5 border border-amber-200 text-center">
      <p class="text-[10px] text-slate-500">COGS ปัจจุบัน</p>
      <p class="text-sm font-bold text-rose-600">${(d.current_cogs/1e6).toFixed(1)}M</p>
    </div>
    <div class="bg-white rounded-lg p-2.5 border border-amber-200 text-center">
      <p class="text-[10px] text-slate-500">Food cost %</p>
      <p class="text-base font-bold" style="color:${curColor}">${cur.toFixed(1)}%</p>
    </div>
    <div class="bg-white rounded-lg p-2.5 border border-amber-200 text-center">
      <p class="text-[10px] text-slate-500">Target COGS</p>
      <p class="text-sm font-bold text-emerald-600">${(d.target_cogs/1e6).toFixed(1)}M @ ${tgt}%</p>
    </div>
    <div class="bg-white rounded-lg p-2.5 border border-amber-200 text-center">
      <p class="text-[10px] text-slate-500">ต้องลด COGS</p>
      <p class="text-sm font-bold text-amber-700">${(d.cogs_reduction_needed/1e6).toFixed(0)}M (×${d.scale_factor.toFixed(3)})</p>
    </div>
  `;
}

function _renderRecalibTable() {
  const d = _recalibData;
  if (!d || !d.skus.length) {
    document.getElementById('recalibBody').innerHTML = '<tr><td colspan="9" class="py-6 text-center text-slate-400">ไม่มีข้อมูล</td></tr>';
    return;
  }
  const fmtN = n => Number(n).toLocaleString('th-TH');
  const rows = d.skus.map(s => {
    const flagBadge = s.flag
      ? `<span class="px-1.5 py-0.5 rounded text-[10px] bg-rose-100 text-rose-700">${s.flag}</span>`
      : '';
    return `
      <tr class="border-b border-slate-100 hover:bg-amber-50">
        <td class="py-1.5 px-2 font-mono text-[11px] text-slate-700">${s.sku_code}</td>
        <td class="py-1.5 px-2 text-slate-700" title="${esc(s.description)}">${esc(s.description.length>40?s.description.slice(0,40)+'…':s.description)}</td>
        <td class="py-1.5 px-2 text-right text-slate-600">${fmtN(s.qty)}</td>
        <td class="py-1.5 px-2 text-right text-slate-500">${s.avg_sell_per_unit.toFixed(2)}</td>
        <td class="py-1.5 px-2 text-right font-semibold text-rose-600">${s.current_unit_cost.toFixed(2)}</td>
        <td class="py-1.5 px-2 text-right" style="background:#fef3c7">
          <input type="number" min="0" step="0.01"
            data-sku="${s.sku_code}"
            data-orig="${s.current_unit_cost}"
            data-suggested="${s.suggested_unit_cost}"
            placeholder="${s.suggested_unit_cost}"
            class="recalib-input w-20 text-right border border-amber-300 rounded px-1.5 py-0.5 font-semibold text-emerald-700">
        </td>
        <td class="py-1.5 px-2 text-right text-slate-500">${(s.total_cogs/1e6).toFixed(1)}M</td>
        <td class="py-1.5 px-2 text-right text-slate-500">${s.pct_of_total_cogs.toFixed(2)}%</td>
        <td class="py-1.5 px-2">${flagBadge}</td>
      </tr>`;
  }).join('');
  document.getElementById('recalibBody').innerHTML = rows;
}

function recalibApplyAllSuggested() {
  document.querySelectorAll('.recalib-input').forEach(inp => {
    inp.value = inp.dataset.suggested;
  });
  document.getElementById('recalibSaveStatus').textContent = 'พร้อม save → กด 💾';
  document.getElementById('recalibSaveStatus').className = 'text-xs text-amber-700 font-semibold';
}

function recalibResetEdits() {
  document.querySelectorAll('.recalib-input').forEach(inp => { inp.value = ''; });
  document.getElementById('recalibSaveStatus').textContent = '';
}

async function recalibSave() {
  const updates = [];
  document.querySelectorAll('.recalib-input').forEach(inp => {
    const v = inp.value.trim();
    if (v === '') return;
    const num = parseFloat(v);
    if (isFinite(num) && num >= 0 && num.toFixed(2) !== parseFloat(inp.dataset.orig).toFixed(2)) {
      updates.push({ sku_code: inp.dataset.sku, unit_cost: num });
    }
  });
  const status = document.getElementById('recalibSaveStatus');
  if (!updates.length) {
    status.textContent = 'ไม่มีการแก้ไข';
    status.className = 'text-xs text-slate-500';
    return;
  }
  if (!confirm(`บันทึก ${updates.length} SKU? unit_cost เดิมจะถูกแทนที่`)) return;
  status.textContent = '⏳ saving…';
  status.className = 'text-xs text-amber-700';
  try {
    const res = await fetch(`${API}/api/cost/bulk-update`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    }).then(r => r.json());
    status.textContent = `✅ บันทึก ${res.written} SKU` + (res.unknown_skus?.length ? ` · unknown: ${res.unknown_skus.join(',')}` : '');
    status.className = 'text-xs text-emerald-700 font-semibold';
    setTimeout(loadRecalibration, 700);   // refresh
  } catch (e) {
    status.textContent = `❌ ${e.message}`;
    status.className = 'text-xs text-red-600';
  }
}

// ════════════════════════════════════════════════════════════════
// MONTHLY REVIEW PACK — Export dialog (Excel / PDF)
// ════════════════════════════════════════════════════════════════
function openMonthlyPackDialog() {
  // Default month = current global filter, else today's month
  let defMonth = '';
  const gMt = document.getElementById('globalMonthTo') || document.getElementById('bbomMonthTo') || document.getElementById('gpbMonthTo');
  if (gMt && gMt.value) defMonth = gMt.value;
  if (!defMonth) {
    const now = new Date();
    defMonth = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}`;
  }
  let dlg = document.getElementById('monthlyPackDlg');
  if (dlg) dlg.remove();
  dlg = document.createElement('div');
  dlg.id = 'monthlyPackDlg';
  dlg.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,0.45);z-index:9999;display:flex;align-items:center;justify-content:center';
  dlg.innerHTML = `
    <div style="background:var(--bg-card,#fff);color:var(--text-primary,#0f172a);border-radius:12px;padding:24px;min-width:380px;max-width:92vw;box-shadow:0 20px 60px rgba(15,23,42,0.3)">
      <h3 style="font-size:18px;font-weight:700;margin:0 0 4px;color:#1F4E79">📥 Export Monthly Review Pack</h3>
      <p style="font-size:12px;color:#64748b;margin:0 0 16px">รายงานสรุปรายเดือน — ภาพรวม, P&L รายสาขา, Top SKU, วัตถุดิบ, alerts, action items</p>
      <label style="display:block;font-size:12px;color:#64748b;margin-bottom:4px">เดือน (YYYY-MM)</label>
      <input id="mpackMonth" type="month" value="${defMonth}"
             style="width:100%;padding:8px 10px;border:1px solid var(--border,#cbd5e1);border-radius:6px;font-size:13px;background:var(--bg-input,#fff);color:inherit;margin-bottom:14px">
      <label style="display:block;font-size:12px;color:#64748b;margin-bottom:6px">รูปแบบไฟล์</label>
      <div style="display:flex;gap:10px;margin-bottom:18px">
        <label style="flex:1;display:flex;align-items:center;gap:6px;padding:10px;border:1px solid var(--border,#cbd5e1);border-radius:6px;cursor:pointer">
          <input type="radio" name="mpackFmt" value="xlsx" checked> <span style="font-size:13px">📊 Excel (.xlsx)</span>
        </label>
        <label style="flex:1;display:flex;align-items:center;gap:6px;padding:10px;border:1px solid var(--border,#cbd5e1);border-radius:6px;cursor:pointer">
          <input type="radio" name="mpackFmt" value="pdf"> <span style="font-size:13px">📄 PDF</span>
        </label>
      </div>
      <div style="display:flex;justify-content:flex-end;gap:8px">
        <button onclick="document.getElementById('monthlyPackDlg').remove()"
                style="padding:8px 16px;border:1px solid var(--border,#cbd5e1);background:transparent;color:inherit;border-radius:6px;font-size:13px;cursor:pointer">ยกเลิก</button>
        <button onclick="downloadMonthlyPack()"
                style="padding:8px 18px;border:none;background:#1F4E79;color:white;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer">⬇ ดาวน์โหลด</button>
      </div>
    </div>`;
  document.body.appendChild(dlg);
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.remove(); });
}

function downloadMonthlyPack() {
  const month = document.getElementById('mpackMonth').value;
  if (!month || month.length !== 7) {
    alert('กรุณาเลือกเดือน (YYYY-MM)');
    return;
  }
  const fmt = document.querySelector('input[name="mpackFmt"]:checked')?.value || 'xlsx';
  const url = `${API}/api/export/monthly-pack?month=${encodeURIComponent(month)}&format=${fmt}`;
  // Trigger a download via a temporary anchor — works cross-origin if server sets CD header.
  const a = document.createElement('a');
  a.href = url;
  a.download = `oshinei_monthly_pack_${month}.${fmt}`;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => a.remove(), 1000);
  document.getElementById('monthlyPackDlg')?.remove();
}

// ═════════════════════════════════════════════════════════════════════════════
// 🏷️ business_type badge / inline editor (Phase 17c)
// ═════════════════════════════════════════════════════════════════════════════
const BIZ_TYPE_META = {
  buffet:      { icon:'🍱', bg:'bg-emerald-100', text:'text-emerald-700',  label:'buffet' },
  buffet_tier: { icon:'💎', bg:'bg-fuchsia-100', text:'text-fuchsia-700',  label:'buffet_tier' },
  alacarte:    { icon:'🍣', bg:'bg-sky-100',     text:'text-sky-700',      label:'alacarte' },
  beverage:    { icon:'🥤', bg:'bg-amber-100',   text:'text-amber-700',    label:'beverage' },
  takeaway:    { icon:'📦', bg:'bg-slate-200',   text:'text-slate-700',    label:'takeaway' },
  fee:         { icon:'📋', bg:'bg-zinc-200',    text:'text-zinc-700',     label:'fee' },
  other:       { icon:'⚪', bg:'bg-neutral-100', text:'text-neutral-600',  label:'other' },
};
const BIZ_TYPES = Object.keys(BIZ_TYPE_META);

function renderBizTypeCell(skuCode, bt) {
  const m = BIZ_TYPE_META[bt] || BIZ_TYPE_META.other;
  return `<select onchange="updateBizType('${skuCode}', this.value, this)"
            class="text-xs ${m.bg} ${m.text} font-medium border-0 rounded-full px-2 py-0.5 cursor-pointer focus:outline-none focus:ring-2 focus:ring-indigo-400">
    ${BIZ_TYPES.map(t => `<option value="${t}" ${t===bt?'selected':''}>${BIZ_TYPE_META[t].icon} ${BIZ_TYPE_META[t].label}</option>`).join('')}
  </select>`;
}

async function updateBizType(skuCode, newType, el) {
  try {
    const r = await fetch(`${API}/api/skus/${encodeURIComponent(skuCode)}/business-type`, {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({business_type:newType})
    });
    if (!r.ok) throw new Error(await r.text());
    const m = BIZ_TYPE_META[newType] || BIZ_TYPE_META.other;
    if (el) { el.className = `text-xs ${m.bg} ${m.text} font-medium border-0 rounded-full px-2 py-0.5 cursor-pointer focus:outline-none focus:ring-2 focus:ring-indigo-400`; }
  } catch(e) {
    alert('Failed to update: ' + e.message);
  }
}

// ═════════════════════════════════════════════════════════════════════════════
// 🔬 BoM Cost Analysis — 4-mode tab (Phase 17d)
// ═════════════════════════════════════════════════════════════════════════════
let bcMode = 'overview';
let bcOverviewData = null;
let bcFoodMomData  = null;
let bcSimBase = null;
let bcSimOverrides = {};   // {sku_code: unit_cost}
let bcSimQty = {};         // {sku_code: new serving qty}
let bcSimDisabled = {};    // {sku_code: true}  → menu closed/discontinued
let bcCompareSelection = [];

function bcSetMode(mode) {
  bcMode = mode;
  // 'simulator' moved to the unified 🎮 Simulators hub — no longer a mode here
  ['overview','branch','compare'].forEach(m => {
    document.getElementById('bc-mode-' + m)?.classList.toggle('active', m === mode);
    document.getElementById('bc-pane-' + m)?.classList.toggle('hidden', m !== mode);
  });
  loadBomCostTab();
}

function bcPeriod() {
  const mf = document.getElementById('bcMonthFrom')?.value || '';
  const mt = document.getElementById('bcMonthTo')?.value   || '';
  const gr = document.getElementById('bcGranularity')?.value || 'month';
  return { mf, mt, gr };
}

// ════════════════════════════════════════════════════════════════
// 🎮 UNIFIED SIMULATORS HUB
// Combines BoM Cost, Cost Sensitivity, Tier Upgrade, and What-If into
// one tab. The three LIVE simulators are "adopted" — their existing DOM
// node is physically moved into a hub host so all their original JS keeps
// working untouched (no duplicate IDs). The previously-orphaned Tier
// Upgrade simulator (markup had been deleted; loader never called) is
// rebuilt fresh here. All four are driven by the shared top-bar filter.
// ════════════════════════════════════════════════════════════════
let _simhubMode = 'bomcost';
let _simhubTierBuilt = false;
function simhubAdopt(hostId, nodeId) {
  const host = document.getElementById(hostId);
  const node = document.getElementById(nodeId);
  if (host && node && node.parentNode !== host) host.appendChild(node);
  if (node) node.classList.remove('hidden');
  return node;
}
function simhubBuildTier() {
  if (_simhubTierBuilt) return;
  const host = document.getElementById('sh-host-tier');
  if (!host) return;
  _simhubTierBuilt = true;
  host.innerHTML = `
    <div class="card p-5">
      <div class="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div>
          <h2 class="text-base font-bold" style="color:#6d28d9">🍣 Tier Upgrade Simulator</h2>
          <p class="text-xs text-slate-400 mt-0.5">จำลองการดันลูกค้าขึ้น tier สูงกว่า (590/799) แล้วดู GP ที่เพิ่ม — ต้องมี unit_cost ของ tier 590 และ 799</p>
        </div>
        <label class="text-xs flex items-center gap-2 whitespace-nowrap">ดัน % ลูกค้าขึ้น tier:
          <input id="simShiftPct" type="number" min="0" max="100" value="10" step="5"
            onchange="loadUpgradeSimulator()" class="w-20 border border-slate-300 rounded-lg px-2 py-1">
          <button onclick="loadUpgradeSimulator()" class="px-3 py-1 bg-indigo-600 text-white rounded-lg font-medium">จำลอง</button>
        </label>
      </div>
      <div id="simKpis" class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4"></div>
      <div id="simTierTable" class="overflow-x-auto mb-4"></div>
      <div style="height:260px"><canvas id="chartSimCompare"></canvas></div>
    </div>`;
}
function simhubLoadMode(mode) {
  try {
    if (mode === 'bomcost') {
      simhubAdopt('sh-host-bomcost', 'bc-pane-simulator');
      // drive the BoM simulator off the shared top-bar month filter
      const bf = document.getElementById('bcMonthFrom'), bt = document.getElementById('bcMonthTo');
      if (bf) bf.value = getMonthFrom() || '';
      if (bt) bt.value = getMonthTo() || '';
      if (typeof bcLoadSimulator === 'function') bcLoadSimulator();
    } else if (mode === 'sensitivity') {
      simhubAdopt('sh-host-sensitivity', 'sensCard');
      _lastSimKey = null;   // force a fresh render in the hub context
      if (typeof loadCostSensitivity === 'function') loadCostSensitivity();
    } else if (mode === 'tier') {
      simhubBuildTier();
      if (typeof loadUpgradeSimulator === 'function') loadUpgradeSimulator();
    } else if (mode === 'whatif') {
      simhubAdopt('sh-host-whatif', 'opsub-whatif');
      if (typeof initWhatIf === 'function') initWhatIf();
    }
  } catch (e) { console.error('simhub mode load failed:', e); }
}
function simhubSetMode(mode) {
  _simhubMode = mode;
  ['bomcost','sensitivity','tier','whatif'].forEach(m => {
    document.getElementById('sh-mode-' + m)?.classList.toggle('active', m === mode);
    document.getElementById('sh-host-' + m)?.classList.toggle('hidden', m !== mode);
  });
  simhubLoadMode(mode);
}
function loadSimhub() { simhubSetMode(_simhubMode); }

// ════════════════════════════════════════════════════════════════
// 🆚 MENU COMPARE A/B — Top menus across two arbitrary periods + dive-in
// ════════════════════════════════════════════════════════════════
let _mcLast = null, _mcDrillData = null, _mcBomSku = null;
function _mcDefaults() {
  const d = new Date(), ym = (y, m) => `${y}-${String(m).padStart(2, '0')}`;
  const cur = ym(d.getFullYear(), d.getMonth() + 1);
  const pm = d.getMonth() === 0 ? ym(d.getFullYear() - 1, 12) : ym(d.getFullYear(), d.getMonth());
  return { cur, pm };
}
function mcToggleMode(P) {
  const mode = document.getElementById('mc' + P + 'mode').value;
  document.getElementById('mc' + P + 'month').classList.toggle('hidden', mode !== 'month');
  const dt = document.getElementById('mc' + P + 'date');
  dt.classList.toggle('hidden', mode !== 'date'); dt.classList.toggle('flex', mode === 'date');
}
function _mcPeriodParams(P, prefix) {
  const q = {};
  if (document.getElementById('mc' + P + 'mode').value === 'month') {
    const f = document.getElementById('mc' + P + 'mf').value, t = document.getElementById('mc' + P + 'mt').value;
    if (f) q[prefix + '_from'] = f; if (f || t) q[prefix + '_to'] = t || f;
  } else {
    const f = document.getElementById('mc' + P + 'df').value, t = document.getElementById('mc' + P + 'dt').value;
    if (f) q[prefix + '_date_from'] = f; if (t) q[prefix + '_date_to'] = t;
  }
  return q;
}
async function loadMenuCompare() {
  const { cur, pm } = _mcDefaults();
  ['Amf', 'Amt'].forEach(id => { const e = document.getElementById('mc' + id); if (e && !e.value) e.value = pm; });
  ['Bmf', 'Bmt'].forEach(id => { const e = document.getElementById('mc' + id); if (e && !e.value) e.value = cur; });
  const qp = { ..._mcPeriodParams('A', 'a'), ..._mcPeriodParams('B', 'b'), limit: 80 };
  if (getBranch()) qp.branch = getBranch();
  if (getGroup()) qp.group = getGroup();
  const st = document.getElementById('mcStatus'); if (st) st.textContent = 'กำลังโหลด…';
  let d; try { d = await fetch(`${API}/api/sales/menu-compare?${new URLSearchParams(qp)}`).then(r => r.json()); }
  catch (e) { if (st) st.textContent = 'ผิดพลาด'; return; }
  _mcLast = { items: d.items || [], totals: d.totals || {}, covers_a: d.covers_a || 0,
              covers_b: d.covers_b || 0, a_range: d.a_range, b_range: d.b_range, qp };
  if (st) st.textContent = `A: ${(d.a_range || []).filter(Boolean).join(' – ') || '—'}   ·   B: ${(d.b_range || []).filter(Boolean).join(' – ') || '—'}`;
  mcRenderTable();
  mcRenderPareto();
}

// Total ↔ per-head (ต่อหัว) cost view. Per-head = COGS ÷ covers, so a drop in
// total cost that's only caused by fewer customers stops looking like a "win".
let _mcPerHead = false;
function mcSetCostMode(perHead) {
  _mcPerHead = perHead;
  document.getElementById('mcModeTotal')?.classList.toggle('gran-active', !perHead);
  document.getElementById('mcModeHead')?.classList.toggle('gran-active', perHead);
  mcRenderTable();
  // Keep the dive-in in sync: the main toggle also drives the per-branch view.
  _mcDrillPerHead = perHead;
  const dm = document.getElementById('mcDrillModal');
  if (dm && !dm.classList.contains('hidden') && _mcDrillData) mcRenderBranch();
}
// ── Menu-compare table state ────────────────────────────────────────────────
// Table 1 follows the total↔per-head toggle; Table 2 is always per-head (added
// alongside, not replacing). Both share one category filter + one sort order so
// a given row sits at the same position in both — easy side-by-side reading.
const MC_NOISE_QTY = 50;                       // Δ% on volumes below this = noise → dimmed
let _mcCatSel = '';                            // category filter ('' = all)
let _mcSort = { key: 'vol', dir: -1 };         // sort key + direction (-1 desc, 1 asc)
const _dpRaw  = (a, b) => b ? (a - b) / b * 100 : null;                 // raw Δ% A vs B
function _mcTotals(items) {
  const s = k => items.reduce((a, r) => a + (r[k] || 0), 0);
  return { qty_a: s('qty_a'), qty_b: s('qty_b'), cogs_a: s('cogs_a'), cogs_b: s('cogs_b') };
}
function mcSetCat(v) { _mcCatSel = v; mcRenderTable(); }
function mcSort(key) {
  if (_mcSort.key === key) _mcSort.dir *= -1; else _mcSort = { key, dir: -1 };
  mcRenderTable();
}
function _mcApplySort(items) {
  const k = _mcSort.key, d = _mcSort.dir;
  const val = r => ({ vol: r._vol, qty_a: r.qty_a, qty_b: r.qty_b, qd: r._qd, mix: r._mix_b,
    cogs_a: r.cogs_a, cogs_b: r.cogs_b, cd: r._cd }[k]);
  items.sort((a, b) => { const x = val(a), y = val(b);
    if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
    return (x - y) * d; });
}
function _mcRenderCats(allItems) {
  const sel = document.getElementById('mcCatFilter'); if (!sel) return;
  const cats = [...new Set(allItems.map(r => r.category).filter(Boolean))].sort();
  if (_mcCatSel && !cats.includes(_mcCatSel)) _mcCatSel = '';   // stale filter from a prior period
  const want = `<option value="">ทั้งหมด</option>` + cats.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
  if (sel.dataset.sig !== want) { sel.dataset.sig = want; sel.innerHTML = want; }
  sel.value = _mcCatSel;
}
function _mcRenderInsights(items) {
  const el = document.getElementById('mcInsights'); if (!el) return;
  const nm = r => esc((r.description || '').replace(/^\((?:A|B|T)\)\s*/, '')).slice(0, 26);
  const movers = items.filter(r => !r.only_in_a && !r.only_in_b && !r._noise && r._qd != null);
  const up   = movers.filter(r => r._qd > 0).sort((a, b) => b._qd - a._qd).slice(0, 3);
  const down = movers.filter(r => r._qd < 0).sort((a, b) => a._qd - b._qd).slice(0, 3);
  const neu  = items.filter(r => r.only_in_b).sort((a, b) => b.qty_b - a.qty_b).slice(0, 3);
  const gone = items.filter(r => r.only_in_a).sort((a, b) => b.qty_a - a.qty_a).slice(0, 3);
  const card = (title, color, rows, fmt) =>
    `<div class="card p-3"><div class="text-xs font-bold mb-1.5" style="color:${color}">${title}</div>` +
    (rows.length ? rows.map(fmt).join('') : '<div class="text-[11px] text-slate-400">—</div>') + '</div>';
  el.innerHTML =
    card('🔼 มาแรง', '#16a34a', up, r => `<div class="text-[11px] truncate" title="${esc(r.description || '')}">${nm(r)} <span style="color:#16a34a;font-weight:600">▲${Math.abs(r._qd).toFixed(0)}%</span></div>`) +
    card('🔽 ร่วงแรง', '#dc2626', down, r => `<div class="text-[11px] truncate" title="${esc(r.description || '')}">${nm(r)} <span style="color:#dc2626;font-weight:600">▼${Math.abs(r._qd).toFixed(0)}%</span></div>`) +
    card('🆕 เมนูใหม่ <span class="font-normal text-slate-400">(มีใน B)</span>', '#4f46e5', neu, r => `<div class="text-[11px] truncate" title="${esc(r.description || '')}">${nm(r)} <span class="text-slate-400">${fmtN(r.qty_b)}</span></div>`) +
    card('⛔ หายไป <span class="font-normal text-slate-400">(เหลือใน A)</span>', '#d97706', gone, r => `<div class="text-[11px] truncate" title="${esc(r.description || '')}">${nm(r)} <span class="text-slate-400">${fmtN(r.qty_a)}</span></div>`);
}
function mcRenderTable() {
  const L = _mcLast; if (!L) return;
  const Ca = L.covers_a || 0, Cb = L.covers_b || 0;
  _mcRenderCats(L.items || []);
  let items = (L.items || []).filter(r => !_mcCatSel || r.category === _mcCatSel);
  const T = _mcTotals(items);
  items.forEach(r => {
    r._mix_a = T.qty_a ? r.qty_a / T.qty_a : 0; r._mix_b = T.qty_b ? r.qty_b / T.qty_b : 0;
    r._vol = (r.qty_a || 0) + (r.qty_b || 0);
    r._qd = _dpRaw(r.qty_a, r.qty_b); r._cd = _dpRaw(r.cogs_a, r.cogs_b);
    r._noise = Math.min(r.qty_a || 0, r.qty_b || 0) < MC_NOISE_QTY;  // small base ⇒ Δ% unstable
  });
  _mcApplySort(items);
  _mcRenderInsights(items);
  mcRenderMcInto('mcHead', 'mcBody', 'mcFoot', _mcPerHead, items, T, Ca, Cb);
  mcRenderMcInto('mcHead2', 'mcBody2', 'mcFoot2', true, items, T, Ca, Cb);
  const catNote = _mcCatSel ? ` · กรองหมวด: ${esc(_mcCatSel)}` : '';
  const note = document.getElementById('mcCoversNote');
  if (note) note.textContent = (_mcPerHead
    ? `ลูกค้า A ${fmtN(Ca)} คน · B ${fmtN(Cb)} คน — /หัว = ยอด ÷ ลูกค้า (ตัดผลของยอดขายที่เปลี่ยน)`
    : `แสดง ${fmtN(items.length)} เมนู · สัดส่วน = ส่วนแบ่งใน Top ที่แสดง`) + catNote;
  const note2 = document.getElementById('mcCoversNote2');
  if (note2) note2.textContent = `ลูกค้า A ${fmtN(Ca)} คน · B ${fmtN(Cb)} คน — ทุกคอลัมน์หารด้วยจำนวนลูกค้า (สัดส่วนเป็นอัตราส่วน จึงเท่ากับตารางที่ 1)` + catNote;
}
// ph divides qty/cost by covers for the per-head table; mix-share is a ratio so
// identical in both. Δ% is recomputed from displayed values (per-head deltas
// differ from raw because covers differ between periods).
function mcRenderMcInto(headId, bodyId, footId, ph, items, T, Ca, Cb) {
  const head = document.getElementById(headId), body = document.getElementById(bodyId), foot = document.getElementById(footId);
  if (!head || !body || !foot) return;
  const hq = ph ? ['จำนวน/คน A', 'จำนวน/คน B'] : ['จำนวน A', 'จำนวน B'];
  const hc = ph ? ['ต้นทุน/หัว A', 'ต้นทุน/หัว B'] : ['ต้นทุน A', 'ต้นทุน B'];
  const ind = k => _mcSort.key === k ? (_mcSort.dir < 0 ? ' ▾' : ' ▴') : '';
  const sth = (lbl, k) => `<th class="num" style="cursor:pointer;user-select:none" onclick="mcSort('${k}')" title="คลิกเพื่อเรียง">${lbl}${ind(k)}</th>`;
  head.innerHTML = `<tr><th class="num">#</th><th>SKU</th><th>เมนู</th>
    ${sth(hq[0], 'qty_a')}${sth(hq[1], 'qty_b')}${sth('Δ%<span class="sub">จำนวน</span>', 'qd')}
    ${sth('สัดส่วน<span class="sub">B · Δpp</span>', 'mix')}
    ${sth(hc[0], 'cogs_a')}${sth(hc[1], 'cogs_b')}${sth('Δ%<span class="sub">ต้นทุน</span>', 'cd')}<th></th></tr>`;
  if (!items.length) {
    body.innerHTML = '<tr><td colspan="11" class="py-4 text-center text-slate-400">ไม่มีข้อมูลในช่วง/หมวดที่เลือก</td></tr>';
    foot.innerHTML = ''; return;
  }
  const vD = (x, C) => ph ? (C ? x / C : null) : x;          // divide by covers when per-head
  const nQ = v => v == null ? '—' : (ph ? v.toFixed(2) : fmtN(v));
  const nM = v => v == null ? '—' : (ph ? '฿' + v.toFixed(2) : fmt(v));
  const dp = (a, b) => (a != null && b) ? (a - b) / b * 100 : null;
  const badge = r => r.only_in_b
    ? '<span style="font-size:9px;background:var(--indigo-soft);color:var(--indigo);padding:1px 5px;border-radius:6px;margin-left:5px;font-weight:700">ใหม่</span>'
    : r.only_in_a
      ? '<span style="font-size:9px;background:#fef3c7;color:#d97706;padding:1px 5px;border-radius:6px;margin-left:5px;font-weight:700">หาย</span>' : '';
  body.innerHTML = items.map((r, i) => {
    const qa = vD(r.qty_a, Ca), qb = vD(r.qty_b, Cb);
    const ca = vD(r.cogs_a, Ca), cb = vD(r.cogs_b, Cb);
    const dpp = (r._mix_b - r._mix_a) * 100;
    const dimQ = r._noise ? ' style="opacity:.45" title="ปริมาณช่วงใดช่วงหนึ่งน้อย (<' + MC_NOISE_QTY + ' ชิ้น) — % ผันผวนง่าย"' : '';
    return `<tr class="cursor-pointer" onclick="mcOpenDrill('${jsAttr(r.sku_code)}')" title="คลิกดูเชิงลึก">
    <td class="num tabular text-slate-400">${i + 1}</td>
    <td class="font-mono text-slate-400 text-[10px]">${esc(r.sku_code)}</td>
    <td class="max-w-xs truncate" title="${esc(r.description || '')}">${esc((r.description || '').replace(/^\((?:A|B|T)\)\s*/, ''))}${badge(r)}</td>
    <td class="num tabular font-semibold">${nQ(qa)}</td>
    <td class="num tabular font-semibold">${nQ(qb)}</td>
    <td class="num tabular"${dimQ}>${_momSpan(dp(qa, qb), true)}</td>
    <td class="num tabular"><div>${(r._mix_b * 100).toFixed(1)}%</div><div class="text-[10px]" style="color:${dpp >= 0 ? '#16a34a' : '#dc2626'}">${dpp >= 0 ? '+' : ''}${dpp.toFixed(1)}pp</div></td>
    <td class="num tabular text-slate-500">${nM(ca)}</td>
    <td class="num tabular text-slate-500">${nM(cb)}</td>
    <td class="num tabular">${_momSpan(dp(ca, cb), false)}</td>
    <td class="num text-indigo-500 text-[11px] whitespace-nowrap">ดูเชิงลึก ›</td></tr>`;
  }).join('');
  const tqa = vD(T.qty_a, Ca), tqb = vD(T.qty_b, Cb), tca = vD(T.cogs_a, Ca), tcb = vD(T.cogs_b, Cb);
  foot.innerHTML = `<tr><td></td><td></td><td>รวม ${fmtN(items.length)} เมนู</td>
    <td class="num tabular">${nQ(tqa)}</td><td class="num tabular">${nQ(tqb)}</td>
    <td class="num tabular">${_momSpan(dp(tqa, tqb), true)}</td>
    <td class="num tabular">100%</td>
    <td class="num tabular">${nM(tca)}</td><td class="num tabular">${nM(tcb)}</td>
    <td class="num tabular">${_momSpan(dp(tca, tcb), false)}</td><td></td></tr>`;
}
// Cumulative cost-share Pareto: each period sorted by its own COGS desc, plotted
// as cumulative % of that period's total. Concentration is share-based, so it's
// identical whether you read total or per-head cost — hence not tied to the toggle.
let _mcParetoMode = 'concentration';   // 'concentration' (each sorted by own cost) | 'aligned' (shared menu order)
function mcSetParetoMode(mode) {
  _mcParetoMode = mode;
  document.getElementById('mcParetoModeConc')?.classList.toggle('gran-active', mode === 'concentration');
  document.getElementById('mcParetoModeAlign')?.classList.toggle('gran-active', mode === 'aligned');
  mcRenderPareto();
}
function mcRenderPareto() {
  const L = _mcLast, card = document.getElementById('mcParetoCard'); if (!L || !card) return;
  const items = L.items || [];
  if (items.length < 2) { card.style.display = 'none'; return; }
  card.style.display = 'block';
  const totA = items.reduce((s, r) => s + (r.cogs_a || 0), 0) || 1;
  const totB = items.reduce((s, r) => s + (r.cogs_b || 0), 0) || 1;
  const aligned = _mcParetoMode === 'aligned';
  let ca, cb, names;
  if (aligned) {
    // SAME menu order for both periods (by combined cost) → compare item-by-item.
    const ord = [...items].sort((a, b) => ((b.cogs_a || 0) + (b.cogs_b || 0)) - ((a.cogs_a || 0) + (a.cogs_b || 0)));
    let ra = 0, rb = 0; ca = []; cb = []; names = [];
    ord.forEach(r => { ra += (r.cogs_a || 0); rb += (r.cogs_b || 0); ca.push(ra / totA * 100); cb.push(rb / totB * 100);
      names.push((r.description || r.sku_code || '').replace(/^\((?:A|B|T)\)\s*/, '')); });
  } else {
    // concentration: each period sorted by its OWN cost desc.
    const cum = (key, tot) => { const sv = items.map(r => r[key] || 0).filter(v => v > 0).sort((a, b) => b - a);
      let run = 0; return sv.map(v => { run += v; return run / tot * 100; }); };
    ca = cum('cogs_a', totA); cb = cum('cogs_b', totB);
  }
  const n = Math.max(ca.length, cb.length);
  const labels = Array.from({ length: n }, (_, i) => i + 1);
  const rank80 = a => { const i = a.findIndex(v => v >= 80); return i < 0 ? a.length : i + 1; };
  const topN = (a, k) => a.length ? a[Math.min(k, a.length) - 1].toFixed(0) : '—';
  const sum = document.getElementById('mcParetoSummary');
  if (sum) sum.textContent = aligned
    ? `Top 10 เมนู(ชุดเดียวกัน) = A ${topN(ca, 10)}% · B ${topN(cb, 10)}% ของต้นทุน`
    : `80% ของต้นทุน: A = ${rank80(ca)} เมนู · B = ${rank80(cb)} เมนู   |   Top 10 = A ${topN(ca, 10)}% · B ${topN(cb, 10)}%`;
  const hint = document.getElementById('mcParetoHint');
  if (hint) hint.textContent = aligned
    ? 'เรียงเมนูชุดเดียวกันทั้ง A/B (ตามต้นทุนรวม) — อันดับ N = เมนูชุดเดียวกัน ดูว่าเมนูเดิมกินสัดส่วนต้นทุนต่างกันแค่ไหนในแต่ละช่วง · เส้นประ = 80%'
    : 'เส้นสะสม % ของต้นทุน เทียบกับจำนวนเมนู (เรียงต้นทุนมาก→น้อยในแต่ละช่วง) · เส้นที่ชันกว่า = ต้นทุนกระจุกในเมนูจำนวนน้อยกว่า · เส้นประ = 80%';
  const aLbl = 'A (' + ((L.a_range || []).filter(Boolean).join('–') || 'A') + ')';
  const bLbl = 'B (' + ((L.b_range || []).filter(Boolean).join('–') || 'B') + ')';
  mkChart('mcParetoChart', {
    type: 'line',
    data: {
      labels, datasets: [
        { label: aLbl, data: ca, borderColor: '#6366f1', backgroundColor: 'transparent', tension: .2, pointRadius: 0, borderWidth: 2 },
        { label: bLbl, data: cb, borderColor: '#0891b2', backgroundColor: 'transparent', tension: .2, pointRadius: 0, borderWidth: 2 },
        // 80% guideline (hidden from legend + tooltip via filters below)
        { label: '_80', data: labels.map(() => 80), borderColor: 'rgba(148,163,184,0.8)', borderDash: [5, 4], borderWidth: 1, pointRadius: 0, fill: false, tension: 0 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'top', labels: { boxWidth: 12, font: { size: 11 }, filter: it => it.text !== '_80' } },
        datalabels: { display: false },
        tooltip: {
          filter: it => it.dataset.label !== '_80',
          callbacks: {
            title: items => aligned && names ? `#${items[0].label} ${names[items[0].dataIndex] || ''}` : `${items[0].label} เมนู`,
            label: c => `${c.dataset.label}: ${c.parsed.y.toFixed(1)}% สะสม`,
          }
        },
      },
      scales: {
        x: { title: { display: true, text: aligned ? 'อันดับเมนู (ชุดเดียวกันทั้ง A/B)' : 'จำนวนเมนู (เรียงต้นทุนมาก→น้อย)' }, ticks: { maxTicksLimit: 12 } },
        y: { title: { display: true, text: '% ต้นทุนสะสม' }, min: 0, max: 100, ticks: { callback: v => v + '%' } },
      },
    },
  });
}
async function mcOpenDrill(sku) {
  _mcBomSku = null;
  const it = (_mcLast?.items || []).find(x => x.sku_code === sku) || { description: sku };
  const _mcName = (it.description || sku).replace(/^\((?:A|B|T)\)\s*/, '');   // strip channel prefix like the table
  document.getElementById('mcDrillTitle').textContent = '🆚 ' + _mcName + ' · ' + sku;
  const _mcm = document.getElementById('mcDrillModal'); _mcm.style.display = 'block'; _mcm.classList.remove('hidden');
  _mcm.scrollIntoView({ behavior: 'smooth', block: 'start' });
  mcDrillTab('branch');
  const bEl = document.getElementById('mcDrillBranch');
  bEl.innerHTML = '<p class="text-sm text-slate-400 py-6 text-center">กำลังโหลด…</p>';
  const q = { sku_code: sku, ..._mcPeriodParams('A', 'a'), ..._mcPeriodParams('B', 'b') };
  if (getGroup()) q.group = getGroup();
  let d; try { d = await fetch(`${API}/api/sales/menu-compare/drill?${new URLSearchParams(q)}`).then(r => r.json()); }
  catch (e) { bEl.innerHTML = '<p class="text-sm text-red-500 py-6 text-center">โหลดไม่ได้</p>'; return; }
  _mcDrillData = d;
  _mcDrillPerHead = _mcPerHead;   // open the dive-in in the same mode as the main table
  mcRenderBranch();
}

let _mcDrillPerHead = false;
function mcSetBranchMode(perHead) { _mcDrillPerHead = perHead; mcRenderBranch(); }
function mcRenderBranch() {
  const el = document.getElementById('mcDrillBranch'); if (!el || !_mcDrillData) return;
  const br = _mcDrillData.branches || [], ph = _mcDrillPerHead;
  const toggle = `<div class="flex items-center gap-2 mb-3 flex-wrap"><span class="text-xs text-slate-500">มุมมอง:</span>
    <div style="display:inline-flex;background:var(--bg-subtle);border:1px solid var(--border);border-radius:8px;padding:3px;gap:2px">
      <button class="gran-btn ${ph?'':'gran-active'}" onclick="mcSetBranchMode(false)">รวม</button>
      <button class="gran-btn ${ph?'gran-active':''}" onclick="mcSetBranchMode(true)">ต่อคน</button>
    </div>${ph?'<span class="text-[11px] text-slate-400">จำนวน/คน = จำนวนเมนู ÷ ลูกค้าของสาขา · ต้นทุน/คน = COGS ÷ ลูกค้า</span>':''}</div>`;
  if (!br.length) { el.innerHTML = toggle + '<p class="text-sm text-slate-400 py-6 text-center">ไม่มีข้อมูลรายสาขา</p>'; return; }
  const nq = v => v == null ? '—' : (ph ? v.toFixed(2) : fmtN(v));
  const nc = v => v == null ? '—' : (ph ? v.toFixed(2) : fmt(v));
  const rows = br.map(b => {
    let qa, qb, ca, cb;
    if (ph) {
      qa = b.covers_a ? b.qty_a / b.covers_a : null; qb = b.covers_b ? b.qty_b / b.covers_b : null;
      ca = b.covers_a ? b.cogs_a / b.covers_a : null; cb = b.covers_b ? b.cogs_b / b.covers_b : null;
    } else { qa = b.qty_a; qb = b.qty_b; ca = b.cogs_a; cb = b.cogs_b; }
    const qd = (qa != null && qb) ? (qa - qb) / qb * 100 : null;
    const cd = (ca != null && cb) ? (ca - cb) / cb * 100 : null;
    return `<tr><td>${esc(b.branch)}</td>
      <td class="num tabular">${nq(qa)}</td><td class="num tabular">${nq(qb)}</td>
      <td class="num tabular">${_momSpan(qd, true)}</td>
      <td class="num tabular text-slate-500">${nc(ca)}</td><td class="num tabular text-slate-500">${nc(cb)}</td>
      <td class="num tabular">${_momSpan(cd, false)}</td></tr>`;
  }).join('');
  const L = ph ? ['จำนวน/คน A', 'จำนวน/คน B', 'ต้นทุน/คน A', 'ต้นทุน/คน B'] : ['จำนวน A', 'จำนวน B', 'ต้นทุน A', 'ต้นทุน B'];
  el.innerHTML = toggle + `<table class="tbl-top60 w-full"><thead><tr>
    <th>สาขา</th><th class="num">${L[0]}</th><th class="num">${L[1]}</th><th class="num">Δ%</th>
    <th class="num">${L[2]}</th><th class="num">${L[3]}</th><th class="num">Δ%</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}
function mcDrillTab(t) {
  ['branch', 'trend', 'bom'].forEach(x => {
    const el = document.getElementById('mcDrill' + x.charAt(0).toUpperCase() + x.slice(1));
    if (el) el.classList.toggle('hidden', x !== t);
  });
  document.querySelectorAll('#mcDrillTabs .gran-btn').forEach((b, i) => b.classList.toggle('gran-active', ['branch', 'trend', 'bom'][i] === t));
  if (t === 'trend') mcRenderTrend();
  if (t === 'bom') mcLoadBom();
}
function mcRenderTrend() {
  const tr = _mcDrillData?.trend || [];
  if (!tr.length || typeof mkChart !== 'function') return;
  mkChart('mcDrillChart', { type: 'line', data: { labels: tr.map(x => x.month), datasets: [
    { label: 'จำนวนขาย', data: tr.map(x => x.qty), borderColor: '#6366f1', backgroundColor: 'transparent', tension: 0.3, pointRadius: 2, yAxisID: 'y' },
    { label: 'ต้นทุน (COGS)', data: tr.map(x => x.cogs), borderColor: '#ef4444', backgroundColor: 'transparent', tension: 0.3, pointRadius: 2, yAxisID: 'y1' },
  ] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { font: { size: 10 } } }, datalabels: { display: false } },
    scales: { y: { position: 'left', ticks: { callback: v => fmtN(v) } }, y1: { position: 'right', grid: { display: false }, ticks: { callback: v => fmt(v) } } } } });
}
async function mcLoadBom() {
  const sku = _mcDrillData?.sku_code, el = document.getElementById('mcDrillBom');
  if (!sku || !el || _mcBomSku === sku) return;
  el.innerHTML = '<p class="text-sm text-slate-400 py-6 text-center">กำลังโหลด BoM…</p>';
  let d; try { d = await fetch(`${API}/api/bom-cost/recipe/${encodeURIComponent(sku)}`).then(r => r.json()); }
  catch (e) { el.innerHTML = '<p class="text-sm text-red-500 py-6 text-center">โหลด BoM ไม่ได้</p>'; return; }
  _mcBomSku = sku;
  const ings = d.ingredients || [];
  if (!d.has_recipe || !ings.length) { el.innerHTML = '<p class="text-sm text-amber-600 py-6 text-center">เมนูนี้ไม่มีสูตร BoM (เช่น เครื่องดื่ม/บุฟเฟต์ tier)</p>'; return; }
  el.innerHTML = `<table class="tbl-top60 w-full"><thead><tr><th>วัตถุดิบ</th><th class="num">ปริมาณ/เสิร์ฟ</th><th class="num">ราคา/หน่วย</th><th class="num">ต้นทุน</th></tr></thead>
    <tbody>${ings.map(g => `<tr><td>${esc(g.ingredient_name || g.ingredient_code || '—')}</td>
      <td class="num tabular">${g.qty_per_serving != null ? fmtN(g.qty_per_serving) : ''} ${esc(g.unit || '')}</td>
      <td class="num tabular text-slate-500">${g.price_per_unit != null ? fmt(g.price_per_unit) : '—'}</td>
      <td class="num tabular">${fmt(g.ingredient_cost || 0)}</td></tr>`).join('')}</tbody>
    <tfoot><tr><td colspan="3">รวมต้นทุน/เสิร์ฟ</td><td class="num tabular">${fmt(d.total_cost || 0)}</td></tr></tfoot></table>`;
}
function mcCloseDrill() { const m = document.getElementById('mcDrillModal'); m.style.display = 'none'; m.classList.add('hidden'); }

// ─── Global "count service charge as revenue" toggle (persisted, all analysis pages) ───
function inclService() { return localStorage.getItem('inclService') === '1'; }
function setInclService(on) {
  localStorage.setItem('inclService', on ? '1' : '0');
  // keep every visible toggle checkbox in sync
  document.querySelectorAll('input[id$="InclServiceChk"]').forEach(c => { c.checked = on; });
  // reload only the analysis view that is actually on screen (tabs hide via display:none)
  const visible = id => { const el = document.getElementById(id); return el && el.offsetParent !== null; };
  if (typeof loadBomCostTab === 'function' && visible('tab-bomcost')) loadBomCostTab();
  if (typeof loadBranchBom === 'function' && visible('tab-branchbom')) loadBranchBom(true);
}
// reflect persisted state on any checkbox when the page/tab renders
function _syncInclServiceChecks() {
  const on = inclService();
  document.querySelectorAll('input[id$="InclServiceChk"]').forEach(c => { c.checked = on; });
}

function bcFmtN(n) {
  if (n == null || isNaN(n)) return '—';
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
}
function bcFmtPct(n, digits=1) {
  if (n == null || isNaN(n)) return '—';
  return Number(n).toFixed(digits) + '%';
}
function bcFoodPctColor(p) {
  if (p == null) return 'text-slate-400';
  if (p < 55)  return 'text-blue-600';
  if (p <= 70) return 'text-emerald-600';
  if (p <= 85) return 'text-amber-600';
  return 'text-red-600';
}

async function loadBomCostTab() {
  // Follow the GLOBAL month range (bcMonthFrom/To are <input type=month>, so set the
  // value directly; a manual local pick still wins until the global range changes).
  (function () {
    const gf = getMonthFrom() || '', gt = getMonthTo() || '', key = gf + '|' + gt;
    if (key !== _extraLastGlobal['bomcost_r']) {
      const bf = document.getElementById('bcMonthFrom'), bt = document.getElementById('bcMonthTo');
      if (bf) bf.value = gf;
      if (bt) bt.value = gt;
      _extraLastGlobal['bomcost_r'] = key;
    }
  })();
  const el = document.getElementById('bcStatus');
  if (el) el.textContent = 'กำลังโหลด...';
  try {
    if (bcMode === 'overview')       await bcLoadOverview();
    else if (bcMode === 'branch')    await bcLoadBranch();
    else if (bcMode === 'compare')   await bcLoadCompare();
    else if (bcMode === 'simulator') await bcLoadSimulator();
    if (el) el.textContent = '';
  } catch(e) {
    console.error(e);
    if (el) el.textContent = 'ผิดพลาด: ' + e.message;
  }
}

// Shared Top-N cost-driver table — used by overview / branch / compare so they
// look identical. Total row at the bottom + MoM (MTD-aware). Cost ↑ = red (bad).
function bcCostDriverTable(cd, scopeLabel, extraNote) {
  const items = cd.items || []; const tt = cd.total || {};
  const momNote = cd.mom_basis === 'mtd' ? `เทียบ 1–${cd.mtd_cutoff_day} วันเท่ากัน (MTD)` : 'เทียบเดือนก่อน';
  const momCell = (v, prev) => v == null ? '<span class="text-slate-300">—</span>'
    : `<span title="ช่วงก่อน ${prev != null ? bcFmtN(prev) : '—'}" style="color:${v <= 0 ? '#10b981' : '#ef4444'};font-weight:600">${v > 0 ? '▲' : '▼'} ${Math.abs(v).toFixed(1)}%</span>`;
  const pctLabel = cd.scope === 'branch' ? '% สาขา' : '% chain';
  const rows = items.map((r, i) => {
    const m = (typeof BIZ_TYPE_META !== 'undefined' ? (BIZ_TYPE_META[r.business_type] || BIZ_TYPE_META.other) : {icon:'',label:r.business_type,bg:'bg-slate-100',text:'text-slate-600'});
    return `<tr class="border-t border-slate-100">
      <td class="py-1.5 pr-2 text-slate-400 tabular-nums">${i + 1}</td>
      <td class="py-1.5 pr-2 font-mono text-indigo-600">${esc(r.sku_code)}</td>
      <td class="py-1.5 pr-2 truncate max-w-xs" title="${esc(r.description)}">${esc(r.description || '')}</td>
      <td class="py-1.5 pr-2"><span class="${m.bg} ${m.text} text-[10px] font-medium px-2 py-0.5 rounded-full">${m.icon} ${m.label}</span></td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${bcFmtN(r.qty)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${bcFmtN(r.unit_cost)}</td>
      <td class="py-1.5 pr-2 text-right font-semibold tabular-nums">${bcFmtN(r.total_cogs)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${bcFmtPct(r.pct_of_total, 1)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${momCell(r.mom_pct, r.prev_cogs)}</td>
      <td class="py-1.5 text-center"><button onclick="bcShowRecipe('${esc(r.sku_code)}')" class="text-indigo-600 hover:underline text-[10px]">🔍</button></td>
    </tr>`;
  }).join('');
  const totalRow = `<tr class="border-t-2 border-slate-300 font-semibold" style="background:var(--bg-subtle)">
      <td colspan="6" class="py-2 pr-2 text-right">รวม Top ${items.length}</td>
      <td class="py-2 pr-2 text-right tabular-nums">${bcFmtN(tt.shown_cogs)}</td>
      <td class="py-2 pr-2 text-right tabular-nums">${bcFmtPct(tt.shown_pct, 1)}</td>
      <td class="py-2 pr-2 text-right tabular-nums">${momCell(tt.mom_pct, tt.prev_cogs)}</td><td></td></tr>
    <tr style="background:var(--bg-subtle)"><td colspan="6" class="py-1.5 pr-2 text-right text-slate-500">รวมทั้งหมด (ทุก SKU)</td>
      <td class="py-1.5 pr-2 text-right font-semibold tabular-nums">${bcFmtN(tt.total_cogs)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums text-slate-400">100%</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${momCell(tt.mom_pct, tt.prev_cogs)}</td><td></td></tr>`;
  return `<div class="card p-4 mb-5">
      <div class="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 class="font-semibold">Top ${items.length} Cost-Driver SKUs ${scopeLabel || ''}</h3>
        <span class="text-xs text-slate-500">MoM ${momNote} · ต้นทุน↑=แดง (ผิดปกติ)${extraNote ? ' · ' + extraNote : ''}</span>
      </div>
      <div class="overflow-x-auto" style="max-height:520px;overflow-y:auto">
      <table class="text-xs w-full">
        <thead class="sticky top-0 bg-white"><tr class="text-left text-slate-500 border-b border-slate-200">
          <th class="py-2 pr-2">#</th><th class="py-2 pr-2">SKU</th><th class="py-2 pr-2">Description</th>
          <th class="py-2 pr-2">ประเภท</th><th class="py-2 pr-2 text-right">Qty</th>
          <th class="py-2 pr-2 text-right">Unit cost</th><th class="py-2 pr-2 text-right">Total COGS</th>
          <th class="py-2 pr-2 text-right">${pctLabel}</th><th class="py-2 pr-2 text-right">MoM</th><th class="py-2 text-center">Recipe</th>
        </tr></thead>
        <tbody>${rows}${totalRow}</tbody>
      </table>
      </div>
    </div>`;
}

// ─── MODE 1: Overview ────────────────────────────────────────────────────────
async function bcLoadOverview() {
  _syncInclServiceChecks();
  const { mf, mt, gr } = bcPeriod();
  const svc = inclService();
  const qp = new URLSearchParams({ granularity: gr });
  if (mf) qp.set('month_from', mf);
  if (mt) qp.set('month_to',   mt);
  if (svc) qp.set('incl_service', 'true');
  const momQp = svc ? '?incl_service=true' : '';
  const cdQp = new URLSearchParams({ scope: 'chain', limit: 60 });
  if (mf) cdQp.set('month_from', mf); if (mt) cdQp.set('month_to', mt);
  const [data, mom, cd] = await Promise.all([
    fetch(`${API}/api/bom-cost/overview?${qp}`).then(r => r.json()),
    fetch(`${API}/api/bom-cost/branch-foodcost-mom${momQp}`).then(r => r.json()).catch(() => ({branches:[]})),
    fetch(`${API}/api/bom-cost/cost-drivers?${cdQp}`).then(r => r.json()).catch(() => null),
  ]);
  bcOverviewData = data;
  bcFoodMomData  = mom;
  const t = data.totals || {};
  const pane = document.getElementById('bc-pane-overview');
  const fc = t.food_cost_pct;
  const con = data.concentration || {};
  const svcOn = !!t.incl_service;
  const revSub = svcOn ? `ex-VAT + service ${bcFmtN(t.service_charge)}` : 'ex-VAT';
  const kpis = `
    <div class="grid grid-cols-2 md:grid-cols-5 gap-3 mb-5">
      ${bcKpiCard('Total Revenue',  bcFmtN(t.revenue),       revSub,                   '#0284c7')}
      ${bcKpiCard('Total COGS',     bcFmtN(t.cogs),          'BoM/Unit Cost',          '#dc2626')}
      ${bcKpiCard('Total GP',       bcFmtN(t.gp),            `${bcFmtPct(t.gp_pct)}`,  t.gp>=0?'#10b981':'#ef4444')}
      ${bcKpiCard('GP %',           bcFmtPct(t.gp_pct),      '',                        '#10b981')}
      ${bcKpiCard('Food Cost %',    bcFmtPct(t.food_cost_pct), svcOn?'(เป้า 55–70% · รวม service)':'(เป้า 55–70%)',
                                    (fc!=null && fc>70)?'#dc2626':(fc!=null && fc>=55)?'#10b981':'#f59e0b')}
    </div>`;

  // Top 60 cost-driver table (shared renderer · total row · MTD MoM)
  const concNote = `Concentration · Top 5: ${bcFmtPct(con.top_5_pct,1)} · Top 10: ${bcFmtPct(con.top_10_pct,1)} · Top 20: ${bcFmtPct(con.top_20_pct,1)} · Top 50: ${bcFmtPct(con.top_50_pct,1)}`;
  const topTbl = cd ? bcCostDriverTable(cd, '(chain-wide)', concNote)
    : '<div class="card p-4 mb-5 text-sm text-slate-400">ไม่มีข้อมูล cost-driver</div>';

  // Heatmap (HTML table coloring)
  const hm = data.heatmap || [];
  const branches = [...new Set(hm.map(r => r.branch))].sort();
  const months   = [...new Set(hm.map(r => r.month))].sort();
  const cellMap = {};
  hm.forEach(r => { cellMap[`${r.branch}|${r.month}`] = r; });
  const cellColor = (fcv) => {
    if (fcv == null) return '#f1f5f9';
    if (fcv < 55)  return '#dbeafe';
    if (fcv <= 70) return '#bbf7d0';
    if (fcv <= 85) return '#fde68a';
    if (fcv <= 100) return '#fdba74';
    return '#fca5a5';
  };
  const heatmapHtml = `
    <div class="card p-4 mb-5">
      <h3 class="font-semibold mb-3">Heatmap · สาขา × เดือน · Food Cost %</h3>
      <div class="overflow-x-auto">
      <table class="text-[10px]">
        <thead><tr><th class="px-2 py-1 text-left text-slate-500">สาขา</th>
          ${months.map(m => `<th class="px-1 py-1 text-slate-500" style="writing-mode:vertical-rl;transform:rotate(180deg)">${m}</th>`).join('')}
        </tr></thead>
        <tbody>
          ${branches.map(b => `<tr>
            <td class="px-2 py-1 text-slate-700 font-medium whitespace-nowrap">${b}</td>
            ${months.map(m => {
              const c = cellMap[`${b}|${m}`];
              const fcv = c?.food_cost_pct;
              return `<td class="text-center" style="background:${cellColor(fcv)};min-width:40px"
                title="${b} · ${m}\\nRev: ${bcFmtN(c?.revenue)}\\nCOGS: ${bcFmtN(c?.cogs)}\\nGP: ${bcFmtN(c?.gp)}\\nFood%: ${bcFmtPct(fcv)}">${fcv!=null?fcv.toFixed(0):''}</td>`;
            }).join('')}
          </tr>`).join('')}
        </tbody>
      </table>
      </div>
      <p class="text-[10px] text-slate-400 mt-2">เขียว 55–70% (เป้า) · เหลือง/ส้ม > เป้า · แดง > 100% (ขาดทุน)</p>
    </div>`;

  // Branch breakdown — colored KPI boxes (latest month + MoM, click to drill down)
  const brHtml = bcRenderFoodCostBoxes(mom);

  pane.innerHTML = kpis + topTbl + heatmapHtml + brHtml;
}

// Hex color for the Food-Cost zone (matches bcFoodPctColor zones)
function bcFoodPctHex(p) {
  if (p == null)  return '#94a3b8';
  if (p < 55)     return '#2563eb';   // below target (too low)
  if (p <= 70)    return '#059669';   // on target
  if (p <= 85)    return '#d97706';   // above target
  return '#dc2626';                   // far above target
}

// Renders the per-branch Food-Cost KPI grid + a shared drill-down panel.
function bcRenderFoodCostBoxes(mom) {
  bcFoodDrillOpen = null;   // panel is rebuilt empty — keep toggle state in sync
  const brs = (mom && mom.branches) || [];
  const monLbl = mom && mom.month ? mom.month : '';
  const prevLbl = mom && mom.prev_month ? mom.prev_month : '';
  if (!brs.length) {
    return `<div class="card p-4 mb-5"><h3 class="font-semibold mb-1">Branch Breakdown · Food Cost %</h3>
      <p class="text-xs text-slate-400">ไม่มีข้อมูล</p></div>`;
  }
  const boxes = brs.map((r, i) => {
    const fcv  = r.food_cost_pct;
    const hex  = bcFoodPctHex(fcv);
    const dp   = r.delta_pp;
    let momHtml = '<span class="text-[10px] text-slate-400">— ไม่มีเดือนก่อน</span>';
    if (dp != null) {
      // Food cost: lower = better → a drop is good (green), a rise is bad (red)
      const good = dp < 0;
      const col  = dp === 0 ? '#64748b' : (good ? '#059669' : '#dc2626');
      const arr  = dp === 0 ? '→' : (dp > 0 ? '▲' : '▼');
      momHtml = `<span class="text-[11px] font-semibold" style="color:${col}">${arr} ${dp>0?'+':''}${dp.toFixed(1)}pp</span>
             <span class="text-[10px] text-slate-400"> vs ${prevLbl}</span>`;
    }
    return `<button type="button" onclick="bcDrillBranch(${i})"
        class="card p-3 text-left hover:shadow-md transition" data-bcbox="${i}"
        style="border-left:4px solid ${hex};cursor:pointer">
      <div class="text-xs font-medium text-slate-600 truncate" title="${esc(r.branch)}">${esc(r.branch)}</div>
      <div class="flex items-baseline gap-1 mt-0.5">
        <span class="text-2xl font-bold" style="color:${hex}">${bcFmtPct(fcv)}</span>
      </div>
      <div class="mt-0.5">${momHtml}</div>
      <div class="text-[10px] text-slate-400 mt-1">COGS ${bcFmtN(r.cogs)} · ${monLbl}</div>
    </button>`;
  }).join('');

  return `<div class="card p-4 mb-5">
    <div class="flex items-center justify-between mb-1 flex-wrap gap-2">
      <h3 class="font-semibold">Branch Breakdown · Food Cost % <span class="text-xs font-normal text-slate-400">(เป้า 55–70%)</span></h3>
      <span class="text-xs text-slate-500">${monLbl}${prevLbl?` เทียบ ${prevLbl}`:''} · คลิกสาขาเพื่อดูรายละเอียด</span>
    </div>
    <p class="text-[10px] text-slate-400 mb-3">
      <span style="color:#2563eb">◼</span> ต่ำกว่าเป้า ·
      <span style="color:#059669">◼</span> ตามเป้า 55–70% ·
      <span style="color:#d97706">◼</span> สูงกว่าเป้า ·
      <span style="color:#dc2626">◼</span> สูงมาก · ▼ ลด = ดี · ▲ เพิ่ม = ต้องระวัง
    </p>
    <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">${boxes}</div>
    <div id="bcFoodDrill" class="mt-4"></div>
  </div>`;
}

// Drill-down: show one branch's MoM detail + top menu cost movers.
let bcFoodDrillOpen = null;
function bcDrillBranch(idx) {
  const host = document.getElementById('bcFoodDrill');
  const r = bcFoodMomData && bcFoodMomData.branches && bcFoodMomData.branches[idx];
  if (!host || !r) return;
  // toggle: clicking the open one again closes it
  if (bcFoodDrillOpen === idx) { host.innerHTML = ''; bcFoodDrillOpen = null; _bcHighlightBox(null); return; }
  bcFoodDrillOpen = idx;
  _bcHighlightBox(idx);

  const monLbl = bcFoodMomData.month || '';
  const prevLbl = bcFoodMomData.prev_month || '';
  const dpTxt = r.delta_pp == null ? '—'
    : `${r.delta_pp>0?'+':''}${r.delta_pp.toFixed(1)}pp`;
  const dpCol = r.delta_pp == null ? '#64748b' : (r.delta_pp < 0 ? '#059669' : (r.delta_pp>0?'#dc2626':'#64748b'));
  const cogsDeltaCol = r.cogs_delta > 0 ? '#dc2626' : (r.cogs_delta < 0 ? '#059669' : '#64748b');

  const moverRow = (m) => {
    const up = m.delta > 0;
    const col = up ? '#dc2626' : '#059669';
    const badge = m.is_new  ? '<span class="text-[9px] px-1 rounded bg-blue-100 text-blue-700 ml-1">ใหม่</span>'
              : (m.is_gone  ? '<span class="text-[9px] px-1 rounded bg-slate-200 text-slate-600 ml-1">หยุดขาย</span>' : '');
    return `<div class="flex items-center justify-between gap-2 py-1 border-b border-slate-100 text-xs">
      <div class="truncate min-w-0">
        <span class="font-mono text-indigo-600">${esc(m.sku_code||'')}</span>
        <span class="text-slate-600">${esc(m.description||'')}</span>${badge}
      </div>
      <div class="text-right whitespace-nowrap" style="flex:none">
        <span class="font-semibold" style="color:${col}">${up?'+':''}${bcFmtN(m.delta)}</span>
        <span class="text-[10px] text-slate-400 ml-1">${bcFmtN(m.prev_cogs)}→${bcFmtN(m.cogs)}</span>
      </div>
    </div>`;
  };
  const ups   = (r.movers_up   || []);
  const downs = (r.movers_down || []);
  const empty = '<p class="text-[11px] text-slate-400 py-2">— ไม่มี</p>';

  host.innerHTML = `
    <div class="rounded-lg border border-slate-200 p-4" style="background:#f8fafc">
      <div class="flex items-center justify-between flex-wrap gap-2 mb-3">
        <h4 class="font-semibold">${esc(r.branch)} · ${monLbl} เทียบ ${prevLbl||'—'}</h4>
        <button onclick="bcDrillBranch(${idx})" class="text-xs text-slate-400 hover:text-slate-600">✕ ปิด</button>
      </div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4 text-center">
        <div class="bg-white rounded p-2 border border-slate-100">
          <div class="text-[10px] text-slate-400">Food Cost %</div>
          <div class="text-lg font-bold" style="color:${bcFoodPctHex(r.food_cost_pct)}">${bcFmtPct(r.food_cost_pct)}</div>
          <div class="text-[10px] text-slate-400">ก่อน ${bcFmtPct(r.prev_food_cost_pct)}</div>
        </div>
        <div class="bg-white rounded p-2 border border-slate-100">
          <div class="text-[10px] text-slate-400">MoM</div>
          <div class="text-lg font-bold" style="color:${dpCol}">${dpTxt}</div>
          <div class="text-[10px] text-slate-400">เป้า 55–70%</div>
        </div>
        <div class="bg-white rounded p-2 border border-slate-100">
          <div class="text-[10px] text-slate-400">COGS</div>
          <div class="text-base font-bold">${bcFmtN(r.cogs)}</div>
          <div class="text-[10px]" style="color:${cogsDeltaCol}">${r.cogs_delta>0?'+':''}${bcFmtN(r.cogs_delta)} MoM</div>
        </div>
        <div class="bg-white rounded p-2 border border-slate-100">
          <div class="text-[10px] text-slate-400">รายได้ฐานคิดต้นทุน (costed)</div>
          <div class="text-base font-bold">${bcFmtN(r.costed_revenue)}</div>
          <div class="text-[10px] text-slate-400">ทั้งหมด ex-VAT ${bcFmtN(r.revenue)}</div>
        </div>
      </div>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <h5 class="text-xs font-semibold mb-1" style="color:#dc2626">▲ เมนูที่ต้นทุนเพิ่มมากสุด (Top ${ups.length})</h5>
          ${ups.length ? ups.map(moverRow).join('') : empty}
        </div>
        <div>
          <h5 class="text-xs font-semibold mb-1" style="color:#059669">▼ เมนูที่ต้นทุนลดมากสุด (Top ${downs.length})</h5>
          ${downs.length ? downs.map(moverRow).join('') : empty}
        </div>
      </div>
      <p class="text-[10px] text-slate-400 mt-3">ตัวเลข = ส่วนต่าง COGS ระหว่าง ${prevLbl||'เดือนก่อน'} → ${monLbl} (บาท) · เรียงตามขนาดการเปลี่ยนแปลง</p>
    </div>`;
  host.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function _bcHighlightBox(idx) {
  document.querySelectorAll('[data-bcbox]').forEach(b => {
    b.style.outline = (idx != null && +b.dataset.bcbox === idx) ? '2px solid #6366f1' : 'none';
  });
}

function bcKpiCard(label, val, sub, color) {
  return `<div class="card p-3" style="border-left:3px solid ${color}">
    <div class="text-[10px] text-slate-500 font-medium uppercase">${label}</div>
    <div class="text-xl font-bold mt-0.5" style="color:${color}">${val}</div>
    <div class="text-[10px] text-slate-400 mt-0.5">${sub||''}</div>
  </div>`;
}

// ─── MODE 2: Per-branch ──────────────────────────────────────────────────────
async function bcLoadBranch() {
  const pane = document.getElementById('bc-pane-branch');
  const { mf, mt, gr } = bcPeriod();
  // Branch selector
  const brs = await fetch(`${API}/api/branches`).then(r => r.json());
  const brList = Array.isArray(brs) ? brs : (brs.items || []);
  const sel = pane.querySelector('#bcBranchSel');
  const currBranch = sel ? sel.value : (brList[0]?.name || '');
  const branchOptions = brList.map(b => `<option value="${esc(b.name)}" ${b.name===currBranch?'selected':''}>${esc(b.name)}</option>`).join('');
  if (!sel) {
    pane.innerHTML = `
      <div class="card p-3 mb-4 flex flex-wrap items-center gap-3 text-xs">
        <label>สาขา <select id="bcBranchSel" onchange="bcLoadBranch()" class="border border-slate-300 rounded-lg px-2 py-1 min-w-[200px]">
          <option value="ALL">ALL (รวมเครือ)</option>
          ${branchOptions}
        </select></label>
      </div>
      <div id="bcBranchBody"></div>`;
  }
  const useBranch = pane.querySelector('#bcBranchSel').value || 'ALL';
  const qp = new URLSearchParams({ branch: useBranch, granularity: gr, top_n: 50 });
  if (mf) qp.set('month_from', mf);
  if (mt) qp.set('month_to',   mt);
  const cdQp = new URLSearchParams({ scope: 'branch', branch: useBranch, limit: 60 });
  if (mf) cdQp.set('month_from', mf); if (mt) cdQp.set('month_to', mt);
  const [data, cd] = await Promise.all([
    fetch(`${API}/api/branch-bom/analysis?${qp}`).then(r => r.json()),
    (useBranch && useBranch !== 'ALL')
      ? fetch(`${API}/api/bom-cost/cost-drivers?${cdQp}`).then(r => r.json()).catch(() => null)
      : fetch(`${API}/api/bom-cost/cost-drivers?scope=chain&limit=60${mf?'&month_from='+mf:''}${mt?'&month_to='+mt:''}`).then(r => r.json()).catch(() => null),
  ]);
  const body = pane.querySelector('#bcBranchBody');
  const t = data.totals || {};
  body.innerHTML = `
    <div class="grid grid-cols-2 md:grid-cols-5 gap-3 mb-5">
      ${bcKpiCard('Revenue',      bcFmtN(t.revenue),       'ex-VAT', '#0284c7')}
      ${bcKpiCard('COGS',         bcFmtN(t.cogs),          '',        '#dc2626')}
      ${bcKpiCard('GP',           bcFmtN(t.gp),            bcFmtPct(t.gp_pct), t.gp>=0?'#10b981':'#ef4444')}
      ${bcKpiCard('Food Cost %',  bcFmtPct(t.food_cost_pct), '(เป้า 55–70%)', '#f59e0b')}
      ${bcKpiCard('Menus',        bcFmtN(t.menus_with_cost), `/ ${bcFmtN(t.menus_total)} ทั้งหมด`, '#6366f1')}
    </div>
    ${cd ? bcCostDriverTable(cd, '· ' + esc(useBranch)) : '<div class="card p-4 mb-5 text-sm text-slate-400">ไม่มีข้อมูล cost-driver</div>'}
    <div class="card p-4 mb-5">
      <h3 class="font-semibold mb-3">Top 25 Ingredients</h3>
      <div class="overflow-x-auto">
        <table class="text-xs w-full">
          <thead><tr class="text-left text-slate-500 border-b">
            <th class="py-1 pr-2">Code</th><th class="py-1 pr-2">Name</th>
            <th class="py-1 pr-2">Type</th>
            <th class="py-1 pr-2 text-right">Qty</th><th class="py-1 pr-2 text-right">Cost</th>
            <th class="py-1 pr-2 text-right">% branch</th>
          </tr></thead>
          <tbody>
            ${(data.top_ingredients_by_cost||[]).map(r => `<tr class="border-t border-slate-100">
              <td class="py-1 pr-2 font-mono">${r.ingredient_code||'—'}</td>
              <td class="py-1 pr-2">${r.ingredient_name}</td>
              <td class="py-1 pr-2">${r.ingredient_type}</td>
              <td class="py-1 pr-2 text-right">${bcFmtN(r.total_qty)}</td>
              <td class="py-1 pr-2 text-right font-semibold">${bcFmtN(r.total_cost)}</td>
              <td class="py-1 pr-2 text-right">${bcFmtPct(r.pct_of_branch_cogs)}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>
    </div>
    <div class="card p-4 mb-5">
      <h3 class="font-semibold mb-3">Cost by Ingredient Type</h3>
      <div class="flex flex-wrap gap-2">
        ${Object.entries(data.cost_by_ingredient_type||{}).sort((a,b)=>b[1]-a[1]).map(([k,v]) =>
          `<span class="px-3 py-1 bg-slate-100 rounded-full text-xs"><b>${k}</b>: ${bcFmtN(v)}</span>`).join('')}
      </div>
      <p class="text-[10px] text-slate-400 mt-2">Coverage: ${t.menus_with_bom_recipe||0} of ${t.menus_in_period||0} menus มี BoM recipe</p>
    </div>`;
}

async function bcShowRecipe(skuCode) {
  const data = await fetch(`${API}/api/bom-cost/recipe/${encodeURIComponent(skuCode)}`).then(r => r.json());
  const html = `
    <div onclick="this.remove()" class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div onclick="event.stopPropagation()" class="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[80vh] overflow-auto p-5">
        <div class="flex items-center justify-between mb-3">
          <div><div class="font-mono text-indigo-600 text-sm">${data.sku_code}</div>
               <div class="font-semibold">${data.description||''}</div></div>
          <button onclick="this.closest('.fixed').remove()" class="text-slate-400 hover:text-slate-700 text-2xl">✕</button>
        </div>
        ${data.has_recipe ? `
          <div class="text-xs text-slate-500 mb-3">Total recipe cost / serving: <b class="text-base text-slate-900">${bcFmtN(data.total_cost)}</b></div>
          <table class="text-xs w-full">
            <thead><tr class="text-left text-slate-500 border-b"><th class="py-1">Ingredient</th><th class="py-1">Type</th><th class="py-1 text-right">Qty/serv</th><th class="py-1">Unit</th><th class="py-1 text-right">Price</th><th class="py-1 text-right">Cost</th></tr></thead>
            <tbody>${(data.ingredients||[]).map(r => `<tr class="border-t border-slate-100">
              <td class="py-1">${r.ingredient_name}</td>
              <td class="py-1 text-slate-500">${r.ingredient_type||''}</td>
              <td class="py-1 text-right">${r.qty_per_serving!=null?r.qty_per_serving:''}</td>
              <td class="py-1">${r.unit||''}</td>
              <td class="py-1 text-right">${r.price_per_unit!=null?bcFmtN(r.price_per_unit):''}</td>
              <td class="py-1 text-right font-semibold">${bcFmtN(r.ingredient_cost)}</td>
            </tr>`).join('')}</tbody>
          </table>` : `<p class="text-amber-700 bg-amber-50 p-3 rounded">ไม่พบ BoM recipe สำหรับ SKU นี้</p>`}
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
}

// ─── MODE 3: Compare branches ────────────────────────────────────────────────
async function bcLoadCompare() {
  const pane = document.getElementById('bc-pane-compare');
  if (!pane.querySelector('#bcCmpSel')) {
    const brs = await fetch(`${API}/api/branches`).then(r => r.json());
    const brList = Array.isArray(brs) ? brs : (brs.items || []);
    pane.innerHTML = `
      <div class="card p-3 mb-4">
        <label class="text-xs"><b>เลือกสาขา (2–5):</b></label>
        <select id="bcCmpSel" multiple size="6" class="border border-slate-300 rounded-lg px-2 py-1 text-xs w-full mt-2" onchange="bcCmpSelectionChanged()">
          ${brList.map(b => `<option value="${esc(b.name)}">${esc(b.name)}</option>`).join('')}
        </select>
        <button onclick="bcLoadCompareRun()" class="mt-2 px-3 py-1.5 bg-indigo-600 text-white rounded-lg text-xs font-medium">เปรียบเทียบ</button>
      </div>
      <div id="bcCmpBody" class="text-sm text-slate-500">เลือก 2–5 สาขาแล้วกด "เปรียบเทียบ"</div>`;
  }
}
function bcCmpSelectionChanged() {
  const sel = document.getElementById('bcCmpSel');
  bcCompareSelection = Array.from(sel.selectedOptions).map(o => o.value);
}
async function bcLoadCompareRun() {
  const sel = document.getElementById('bcCmpSel');
  bcCompareSelection = Array.from(sel.selectedOptions).map(o => o.value);
  if (bcCompareSelection.length < 2 || bcCompareSelection.length > 5) {
    alert('โปรดเลือก 2–5 สาขา'); return;
  }
  const { mf, mt } = bcPeriod();
  const qp = new URLSearchParams({ branches: bcCompareSelection.join(',') });
  if (mf) qp.set('month_from', mf);
  if (mt) qp.set('month_to', mt);
  const data = await fetch(`${API}/api/bom-cost/compare?${qp}`).then(r => r.json());
  const body = document.getElementById('bcCmpBody');
  const res = data.results || [];
  const variance = data.variance || {};
  const outlierBranches = (metric) => {
    const out = (variance[metric]?.outliers || []).map(o => o.branch);
    return new Set(out);
  };
  const ofFc = outlierBranches('food_cost_pct');
  const ofGp = outlierBranches('gp_pct');
  const ofCogs = outlierBranches('cogs');
  const ofRev = outlierBranches('revenue');
  const flag = (set, br) => set.has(br) ? 'bg-amber-50' : '';

  body.innerHTML = `
    <div class="card p-4 mb-4 overflow-x-auto">
      <table class="text-xs w-full">
        <thead><tr class="border-b"><th class="text-left py-2 pr-3">Metric</th>
          ${res.map(r => `<th class="text-right py-2 pr-3">${esc(r.branch)}</th>`).join('')}
        </tr></thead>
        <tbody>
          <tr class="border-t"><td class="py-1 pr-3 text-slate-500">Revenue</td>
            ${res.map(r => `<td class="py-1 pr-3 text-right ${flag(ofRev, r.branch)}">${bcFmtN(r.revenue)}</td>`).join('')}</tr>
          <tr class="border-t"><td class="py-1 pr-3 text-slate-500">COGS</td>
            ${res.map(r => `<td class="py-1 pr-3 text-right ${flag(ofCogs, r.branch)}">${bcFmtN(r.cogs)}</td>`).join('')}</tr>
          <tr class="border-t"><td class="py-1 pr-3 text-slate-500">GP</td>
            ${res.map(r => `<td class="py-1 pr-3 text-right">${bcFmtN(r.gp)}</td>`).join('')}</tr>
          <tr class="border-t"><td class="py-1 pr-3 text-slate-500">GP %</td>
            ${res.map(r => `<td class="py-1 pr-3 text-right ${flag(ofGp, r.branch)}">${bcFmtPct(r.gp_pct)}</td>`).join('')}</tr>
          <tr class="border-t"><td class="py-1 pr-3 text-slate-500">Food Cost %</td>
            ${res.map(r => `<td class="py-1 pr-3 text-right ${flag(ofFc, r.branch)} ${bcFoodPctColor(r.food_cost_pct)}">${bcFmtPct(r.food_cost_pct)}</td>`).join('')}</tr>
          <tr class="border-t"><td class="py-1 pr-3 text-slate-500">Uncosted SKUs</td>
            ${res.map(r => `<td class="py-1 pr-3 text-right">${bcFmtN(r.uncosted_skus)}</td>`).join('')}</tr>
          ${[0,1,2].map(i => `
            <tr class="border-t"><td class="py-1 pr-3 text-slate-500">Top menu #${i+1}</td>
              ${res.map(r => {
                const m = r.top_menus[i];
                return `<td class="py-1 pr-3 text-right text-[10px]">${m?`<span class="font-mono text-indigo-600">${m.sku_code}</span> · ${bcFmtN(m.total_cogs)} (${bcFmtPct(m.pct_of_branch_cogs,1)})`:'—'}</td>`;
              }).join('')}
            </tr>`).join('')}
        </tbody>
      </table>
      <p class="text-[10px] text-slate-400 mt-2">เซลล์สีเหลือง = ค่าเบี่ยงเบนจากค่าเฉลี่ยกลุ่ม ≥ 10%</p>
    </div>
    <div class="card p-4 mb-4">
      <h3 class="font-semibold mb-3">Cost by Ingredient Type (per branch)</h3>
      <div class="overflow-x-auto">
        <table class="text-xs w-full">
          <thead><tr class="border-b"><th class="text-left py-1 pr-3">Type</th>
            ${res.map(r => `<th class="text-right py-1 pr-3">${esc(r.branch)}</th>`).join('')}
          </tr></thead>
          <tbody>
            ${(()=>{
              const types = new Set();
              res.forEach(r => Object.keys(r.cost_by_ingredient_type||{}).forEach(t => types.add(t)));
              return [...types].sort().map(t => `<tr class="border-t">
                <td class="py-1 pr-3 font-medium">${t}</td>
                ${res.map(r => `<td class="py-1 pr-3 text-right">${bcFmtN(r.cost_by_ingredient_type?.[t]||0)}</td>`).join('')}
              </tr>`).join('');
            })()}
          </tbody>
        </table>
      </div>
    </div>`;

  // Per-branch shared cost-driver tables (Top 60 + total + MoM) — same as
  // overview/branch modes, so all three look identical.
  const cds = await Promise.all(bcCompareSelection.map(br => {
    const q = new URLSearchParams({ scope: 'branch', branch: br, limit: 60 });
    if (mf) q.set('month_from', mf); if (mt) q.set('month_to', mt);
    return fetch(`${API}/api/bom-cost/cost-drivers?${q}`).then(r => r.json()).catch(() => null);
  }));
  let cdHtml = '<h3 class="font-semibold mb-2 mt-2">Top 60 Cost-Driver SKUs · แยกรายสาขา</h3>';
  bcCompareSelection.forEach((br, i) => { if (cds[i]) cdHtml += bcCostDriverTable(cds[i], '· ' + esc(br)); });
  body.insertAdjacentHTML('beforeend', cdHtml);
}

// ─── MODE 4: Simulator ───────────────────────────────────────────────────────
async function bcLoadSimulator() {
  const pane = document.getElementById('bc-pane-simulator');
  const { mf, mt } = bcPeriod();
  const qp = new URLSearchParams({ limit: 2000 });   // ALL menus that have BoM
  if (mf) qp.set('month_from', mf);
  if (mt) qp.set('month_to', mt);
  const data = await fetch(`${API}/api/bom-cost/simulator-base?${qp}`).then(r => r.json());
  bcSimBase = data;
  // scenarios list
  const scens = await fetch(`${API}/api/bom-cost/scenarios`).then(r => r.json());
  const scenList = scens.scenarios || [];
  pane.innerHTML = `
    <div class="flex flex-wrap items-center gap-3 mb-4">
      <label class="text-xs">Scenario:
        <select id="bcScenSel" onchange="bcLoadScenario(this.value)" class="border border-slate-300 rounded-lg px-2 py-1 text-xs min-w-[180px]">
          <option value="">— Baseline —</option>
          ${scenList.map(s => `<option value="${s.id}">${esc(s.name)} (${s.n_items})</option>`).join('')}
        </select>
      </label>
      <button onclick="bcSaveScenario()" class="px-3 py-1.5 text-xs bg-emerald-600 text-white rounded-lg font-medium">💾 Save current</button>
      <button onclick="bcDeleteScenario()" class="px-3 py-1.5 text-xs bg-rose-100 text-rose-700 rounded-lg font-medium">🗑 Delete</button>
      <button onclick="bcAdoptScenario()" class="px-3 py-1.5 text-xs bg-violet-600 text-white rounded-lg font-medium" title="บันทึกว่าใช้จริง แล้วติดตามเงินที่ประหยัดได้สะสม">📌 Adopt & Track ROI</button>
      <button onclick="bcShowRoi()" class="px-3 py-1.5 text-xs bg-violet-100 text-violet-700 rounded-lg font-medium" title="ดูผลประหยัดสะสมของ scenario ที่ adopt">💰 ROI</button>
      <button onclick="bcResetOverrides()" class="px-3 py-1.5 text-xs bg-slate-100 text-slate-700 rounded-lg">↺ Reset</button>
      <span class="border-l border-slate-200 h-5 mx-1"></span>
      <button onclick="bcExportTemplate()" class="px-3 py-1.5 text-xs bg-sky-600 text-white rounded-lg font-medium" title="ดาวน์โหลด CSV เปิดใน Excel แก้ตัวเลขแล้ว upload กลับ">⬇ Export template</button>
      <button onclick="document.getElementById('bcTplFile').click()" class="px-3 py-1.5 text-xs bg-indigo-600 text-white rounded-lg font-medium">⬆ Upload template</button>
      <input type="file" id="bcTplFile" accept=".csv,text/csv" style="display:none" onchange="bcImportTemplate(this)">
      <span class="text-xs text-slate-400 ml-auto" id="bcSimMeta">SKUs ในตาราง: ${data.skus.length}</span>
    </div>
    <div id="bcRoiPanel" class="card p-4 mb-4 hidden" style="border-left:4px solid #7c3aed"></div>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div class="card p-4">
        <h3 class="font-semibold mb-3">Editor — ปรับต้นทุน · จำนวนเสิร์ฟ · ปิดเมนู</h3>
        <div class="flex flex-wrap gap-2 mb-3 text-xs">
          <input id="bcSimSearch" placeholder="🔎 Search SKU/description" oninput="bcRenderSimEditor()" class="border border-slate-300 rounded-lg px-2 py-1 flex-1 min-w-[120px]">
          <select id="bcSimBizFilter" onchange="bcRenderSimEditor()" class="border border-slate-300 rounded-lg px-2 py-1">
            <option value="">ทั้งหมด</option>
            ${BIZ_TYPES.map(t => `<option value="${t}">${BIZ_TYPE_META[t].icon} ${t}</option>`).join('')}
          </select>
        </div>
        <div class="flex flex-wrap gap-2 mb-3 text-xs">
          <button onclick="bcApplyPct(-5)" class="px-2 py-1 bg-amber-100 text-amber-700 rounded">−5% all</button>
          <button onclick="bcApplyPct(-10)" class="px-2 py-1 bg-amber-100 text-amber-700 rounded">−10% all</button>
          <button onclick="bcApplyPct(10)" class="px-2 py-1 bg-rose-100 text-rose-700 rounded">+10% all</button>
          <button onclick="bcApplyTargetFood()" class="px-2 py-1 bg-indigo-100 text-indigo-700 rounded">🎯 ตั้งเป้า Food% =
            <input id="bcTargetFood" type="number" value="65" class="w-12 border border-indigo-300 rounded px-1" onclick="event.stopPropagation()"></button>
        </div>
        <div id="bcSimEditor" style="max-height:520px;overflow-y:auto"></div>
      </div>
      <div class="card p-4">
        <h3 class="font-semibold mb-3">Live KPI Impact</h3>
        <div id="bcSimKpis"></div>
      </div>
    </div>`;
  bcRenderSimEditor();
  bcRecomputeKpis();
}

function bcRenderSimEditor() {
  if (!bcSimBase) return;
  const q   = (document.getElementById('bcSimSearch')?.value || '').toLowerCase();
  const bt  = document.getElementById('bcSimBizFilter')?.value || '';
  const rows = (bcSimBase.skus||[]).filter(r => {
    if (bt && r.business_type !== bt) return false;
    if (q && !(`${r.sku_code} ${r.description}`.toLowerCase().includes(q))) return false;
    return true;
  });
  const el = document.getElementById('bcSimEditor');
  el.innerHTML = `<table class="text-xs w-full">
    <thead class="sticky top-0 bg-white"><tr class="border-b text-slate-500">
      <th class="text-left py-1 pr-2">SKU</th><th class="text-left py-1 pr-2">Description</th>
      <th class="text-right py-1 pr-2">ต้นทุน/หน่วย<br><span class="font-normal text-[9px]">เดิม → ใหม่</span></th>
      <th class="text-right py-1 pr-2">จำนวนเสิร์ฟ<br><span class="font-normal text-[9px]">เดิม → ใหม่</span></th>
      <th class="text-center py-1">ปิด</th></tr></thead>
    <tbody>
      ${rows.map(r => {
        const ov = bcSimOverrides[r.sku_code];
        const qv = bcSimQty[r.sku_code];
        const off = !!bcSimDisabled[r.sku_code];
        return `<tr class="border-t border-slate-100 ${off?'opacity-40':''}">
          <td class="py-1 pr-2 font-mono text-indigo-600">${r.sku_code}</td>
          <td class="py-1 pr-2 truncate max-w-[160px]" title="${esc(r.description)}">${esc(r.description)}</td>
          <td class="py-1 pr-2 text-right whitespace-nowrap">
            <span class="text-slate-400">${bcFmtN(r.current_unit_cost)}</span>
            <input type="number" min="0" step="0.01" value="${ov!=null?ov:r.current_unit_cost}"
              data-sku="${r.sku_code}" data-base="${r.current_unit_cost}" oninput="bcOnOverrideEdit(this)" ${off?'disabled':''}
              class="w-16 border border-slate-200 rounded px-1 py-0.5 text-right ${ov!=null?'bg-amber-50 border-amber-300':''}">
          </td>
          <td class="py-1 pr-2 text-right whitespace-nowrap">
            <span class="text-slate-400">${bcFmtN(r.qty)}</span>
            <input type="number" min="0" step="1" value="${qv!=null?qv:Math.round(r.qty)}"
              data-sku="${r.sku_code}" data-baseqty="${r.qty}" oninput="bcOnQtyEdit(this)" ${off?'disabled':''}
              class="w-20 border border-slate-200 rounded px-1 py-0.5 text-right ${qv!=null?'bg-sky-50 border-sky-300':''}">
          </td>
          <td class="py-1 text-center">
            <input type="checkbox" ${off?'checked':''} data-sku="${r.sku_code}" onchange="bcOnDisableToggle(this)" title="ปิดการขายเมนูนี้ (discontinue)">
          </td>
        </tr>`;
      }).join('')}
    </tbody></table>`;
}
function bcOnQtyEdit(inp) {
  const sku = inp.dataset.sku, base = parseFloat(inp.dataset.baseqty), v = parseFloat(inp.value);
  if (!isFinite(v) || Math.abs(v - base) < 0.5) { delete bcSimQty[sku]; inp.classList.remove('bg-sky-50','border-sky-300'); }
  else { bcSimQty[sku] = v; inp.classList.add('bg-sky-50','border-sky-300'); }
  bcRecomputeKpis();
}
function bcOnDisableToggle(chk) {
  const sku = chk.dataset.sku;
  if (chk.checked) bcSimDisabled[sku] = true; else delete bcSimDisabled[sku];
  bcRenderSimEditor(); bcRecomputeKpis();
}
function bcOnOverrideEdit(inp) {
  const sku = inp.dataset.sku;
  const base = parseFloat(inp.dataset.base);
  const v = parseFloat(inp.value);
  if (!isFinite(v) || Math.abs(v - base) < 1e-9) {
    delete bcSimOverrides[sku];
    inp.classList.remove('bg-amber-50','border-amber-300');
  } else {
    bcSimOverrides[sku] = v;
    inp.classList.add('bg-amber-50','border-amber-300');
  }
  bcRecomputeKpis();
}
function bcRecomputeKpis() {
  if (!bcSimBase) return;
  const b = bcSimBase.baseline;
  let cogsDelta = 0, revDelta = 0;
  (bcSimBase.skus||[]).forEach(r => {
    const baseCogs = r.period_cogs || 0;
    const baseRev  = r.period_revenue || 0;          // à la carte; buffet dishes = 0
    let newCogs, newRev;
    if (bcSimDisabled[r.sku_code]) {                 // menu closed → no cost, no revenue
      newCogs = 0; newRev = 0;
    } else {
      const unit = bcSimOverrides[r.sku_code] != null ? bcSimOverrides[r.sku_code] : r.current_unit_cost;
      const qty  = bcSimQty[r.sku_code] != null ? bcSimQty[r.sku_code] : r.qty;
      newCogs = qty * unit;
      newRev  = r.qty > 0 ? baseRev * (qty / r.qty) : baseRev;   // revenue scales with servings
    }
    cogsDelta += (newCogs - baseCogs);
    revDelta  += (newRev  - baseRev);
  });
  const scenCogs   = b.cogs + cogsDelta;
  const scenCosted = b.costed_revenue + revDelta;
  const scenRev    = b.revenue + revDelta;
  const scenGp     = scenCosted - scenCogs;
  const scenGpPct  = scenCosted > 0 ? (scenGp / scenCosted * 100) : null;
  const scenFood   = scenCosted > 0 ? (scenCogs / scenCosted * 100) : null;
  const dR = scenRev - b.revenue, dC = scenCogs - b.cogs, dG = scenGp - b.gp;
  const nOv = Object.keys(bcSimOverrides).length, nQ = Object.keys(bcSimQty).length, nD = Object.keys(bcSimDisabled).length;
  document.getElementById('bcSimKpis').innerHTML = `
    <table class="text-sm w-full">
      <thead><tr class="text-xs text-slate-500 border-b"><th class="text-left py-1">Metric</th><th class="text-right py-1">Baseline</th><th class="text-right py-1">Scenario</th><th class="text-right py-1">Δ</th></tr></thead>
      <tbody>
        <tr class="border-t"><td class="py-1">Revenue</td><td class="text-right">${bcFmtN(b.revenue)}</td><td class="text-right font-semibold">${bcFmtN(scenRev)}</td><td class="text-right ${dR>=0?'text-emerald-600':'text-red-600'}">${Math.abs(dR)<1?'—':(dR>0?'+':'')+bcFmtN(dR)}</td></tr>
        <tr class="border-t"><td class="py-1">COGS</td><td class="text-right">${bcFmtN(b.cogs)}</td><td class="text-right font-semibold">${bcFmtN(scenCogs)}</td><td class="text-right ${dC>0?'text-red-600':'text-emerald-600'}">${Math.abs(dC)<1?'—':(dC>0?'+':'')+bcFmtN(dC)}</td></tr>
        <tr class="border-t"><td class="py-1">GP</td><td class="text-right">${bcFmtN(b.gp)}</td><td class="text-right font-semibold">${bcFmtN(scenGp)}</td><td class="text-right ${dG>=0?'text-emerald-600':'text-red-600'}">${Math.abs(dG)<1?'—':(dG>=0?'+':'')+bcFmtN(dG)}</td></tr>
        <tr class="border-t"><td class="py-1">GP %</td><td class="text-right">${bcFmtPct(b.gp_pct)}</td><td class="text-right font-semibold">${bcFmtPct(scenGpPct)}</td><td class="text-right">${scenGpPct!=null&&b.gp_pct!=null?((scenGpPct-b.gp_pct>=0?'+':'')+(scenGpPct-b.gp_pct).toFixed(2)+'pp'):''}</td></tr>
        <tr class="border-t"><td class="py-1">Food Cost %</td><td class="text-right ${bcFoodPctColor(b.food_cost_pct)}">${bcFmtPct(b.food_cost_pct)}</td><td class="text-right font-semibold ${bcFoodPctColor(scenFood)}">${bcFmtPct(scenFood)}</td><td class="text-right">${scenFood!=null&&b.food_cost_pct!=null?((scenFood-b.food_cost_pct>=0?'+':'')+(scenFood-b.food_cost_pct).toFixed(2)+'pp'):''}</td></tr>
      </tbody>
    </table>
    <p class="text-[10px] text-slate-400 mt-3">ปรับใช้: ต้นทุน ${nOv} · จำนวนเสิร์ฟ ${nQ} · ปิดเมนู ${nD} · preview client-side · 🔴 COGS=BoM เท่านั้น</p>
    <p class="text-[10px] text-slate-400 mt-1">หมายเหตุ: เมนูบุฟเฟ่ต์รายได้/จาน = 0 (ปิดเมนู = ประหยัดต้นทุน รายได้ค่าบุฟเฟ่ต์คงเดิม) · à la carte รายได้ปรับตามจำนวนเสิร์ฟ</p>`;
}
function bcApplyPct(p) {
  (bcSimBase?.skus||[]).forEach(r => {
    if (r.current_unit_cost > 0) bcSimOverrides[r.sku_code] = +(r.current_unit_cost * (1 + p/100)).toFixed(4);
  });
  bcRenderSimEditor(); bcRecomputeKpis();
}
function bcApplyTargetFood() {
  const target = parseFloat(document.getElementById('bcTargetFood').value);
  if (!isFinite(target) || target <= 0) return;
  const b = bcSimBase.baseline;
  if (!(b.food_cost_pct > 0)) return;
  const scale = target / b.food_cost_pct;
  (bcSimBase?.skus||[]).forEach(r => {
    if (r.current_unit_cost > 0) bcSimOverrides[r.sku_code] = +(r.current_unit_cost * scale).toFixed(4);
  });
  bcRenderSimEditor(); bcRecomputeKpis();
}
function bcResetOverrides() {
  bcSimOverrides = {}; bcSimQty = {}; bcSimDisabled = {};   // คืนค่าทั้งหมด (ต้นทุน + จำนวน + ปิดเมนู)
  bcRenderSimEditor(); bcRecomputeKpis();
}
// ── Template export/import (edit 337 menus in Excel instead of inline) ──
function bcExportTemplate() {
  if (!bcSimBase) return;
  const hdr = ['sku_code','description','current_unit_cost','current_qty','new_unit_cost','new_qty','close(1=ปิดเมนู)'];
  const rows = [hdr.join(',')];
  (bcSimBase.skus||[]).forEach(r => {
    const newCost = bcSimOverrides[r.sku_code] != null ? bcSimOverrides[r.sku_code] : r.current_unit_cost;
    const newQty  = bcSimQty[r.sku_code] != null ? bcSimQty[r.sku_code] : Math.round(r.qty);
    const close   = bcSimDisabled[r.sku_code] ? 1 : 0;
    const desc = '"' + String(r.description||'').replace(/"/g,'""') + '"';
    rows.push([r.sku_code, desc, r.current_unit_cost, Math.round(r.qty), newCost, newQty, close].join(','));
  });
  downloadCSV(rows.join('\n'), `simulator_template_${new Date().toISOString().slice(0,10)}.csv`);
}
function _parseCSV(text) {
  // minimal RFC-4180 parser (handles quoted fields, commas, "" escapes, CRLF)
  const rows = []; let row = [], cur = '', q = false;
  text = text.replace(/^﻿/, '');
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i+1] === '"') { cur += '"'; i++; } else q = false; }
      else cur += c;
    } else if (c === '"') q = true;
    else if (c === ',') { row.push(cur); cur = ''; }
    else if (c === '\n') { row.push(cur); rows.push(row); row = []; cur = ''; }
    else if (c === '\r') { /* skip */ }
    else cur += c;
  }
  if (cur.length || row.length) { row.push(cur); rows.push(row); }
  return rows;
}
function bcImportTemplate(input) {
  const file = input.files && input.files[0];
  if (!file || !bcSimBase) return;
  const reader = new FileReader();
  reader.onload = e => {
    try {
      const rows = _parseCSV(String(e.target.result));
      if (rows.length < 2) { alert('ไฟล์ว่างหรือรูปแบบไม่ถูกต้อง'); return; }
      const hdr = rows[0].map(h => h.trim().toLowerCase());
      const ix = n => hdr.findIndex(h => h.startsWith(n));
      const iSku = ix('sku_code'), iCost = ix('new_unit_cost'), iQty = ix('new_qty'), iClose = ix('close');
      if (iSku < 0 || iCost < 0) { alert('ไม่พบคอลัมน์ sku_code / new_unit_cost — กรุณาใช้ template ที่ export'); return; }
      const cur = {}; (bcSimBase.skus||[]).forEach(r => { cur[r.sku_code] = r; });
      let nCost = 0, nQty = 0, nClose = 0, unknown = 0;
      bcSimOverrides = {}; bcSimQty = {}; bcSimDisabled = {};   // template replaces current scenario
      for (let i = 1; i < rows.length; i++) {
        const r = rows[i]; if (!r || !r[iSku]) continue;
        const sku = String(r[iSku]).trim(); const base = cur[sku];
        if (!base) { unknown++; continue; }
        const nc = parseFloat(r[iCost]);
        if (isFinite(nc) && Math.abs(nc - base.current_unit_cost) > 1e-9) { bcSimOverrides[sku] = nc; nCost++; }
        if (iQty >= 0) { const nq = parseFloat(r[iQty]); if (isFinite(nq) && Math.abs(nq - base.qty) >= 0.5) { bcSimQty[sku] = nq; nQty++; } }
        if (iClose >= 0) { const cl = String(r[iClose]).trim(); if (cl === '1' || cl.toLowerCase() === 'true' || cl === 'ปิด') { bcSimDisabled[sku] = true; nClose++; } }
      }
      bcRenderSimEditor(); bcRecomputeKpis();
      alert(`อัปโหลดสำเร็จ ✓\nปรับต้นทุน ${nCost} · จำนวนเสิร์ฟ ${nQty} · ปิดเมนู ${nClose}` + (unknown ? `\n(ข้าม ${unknown} แถวที่ไม่รู้จัก SKU)` : ''));
    } catch (err) { alert('อ่านไฟล์ไม่สำเร็จ: ' + err.message); }
    finally { input.value = ''; }
  };
  reader.readAsText(file, 'utf-8');
}
async function bcSaveScenario() {
  const name = prompt('ชื่อ Scenario:', 'My scenario');
  if (!name) return;
  const desc = prompt('คำอธิบาย (optional):', '');
  // union of all changed SKUs (cost / qty / disabled). unit_cost is always sent
  // (current cost when only qty/disable changed) because the column is NOT NULL.
  const curCost = {}; (bcSimBase?.skus || []).forEach(r => { curCost[r.sku_code] = r.current_unit_cost; });
  const codes = new Set([...Object.keys(bcSimOverrides), ...Object.keys(bcSimQty), ...Object.keys(bcSimDisabled)]);
  const overrides = [...codes].map(sku_code => ({
    sku_code,
    unit_cost: bcSimOverrides[sku_code] != null ? bcSimOverrides[sku_code] : (curCost[sku_code] || 0),
    qty: bcSimQty[sku_code] != null ? bcSimQty[sku_code] : null,
    disabled: !!bcSimDisabled[sku_code],
  }));
  if (!overrides.length) { alert('ยังไม่มีการปรับ (ต้นทุน/จำนวน/ปิดเมนู)'); return; }
  const r = await fetch(`${API}/api/bom-cost/scenarios`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name, description: desc, overrides})
  }).then(r => r.json());
  alert(`บันทึก Scenario #${r.id} สำเร็จ (${r.items_inserted} items)`);
  bcLoadSimulator();
}
async function bcLoadScenario(id) {
  if (!id) { bcSimOverrides = {}; bcSimQty = {}; bcSimDisabled = {}; bcRenderSimEditor(); bcRecomputeKpis(); return; }
  const r = await fetch(`${API}/api/bom-cost/scenarios/${id}`).then(r => r.json());
  bcSimOverrides = {}; bcSimQty = {}; bcSimDisabled = {};
  const curCost = {}; (bcSimBase?.skus || []).forEach(r => { curCost[r.sku_code] = r.current_unit_cost; });
  (r.items||[]).forEach(it => {
    // only treat unit_cost as an override if it actually differs from current
    if (it.unit_cost != null && (curCost[it.sku_code] == null || Math.abs(it.unit_cost - curCost[it.sku_code]) > 1e-9))
      bcSimOverrides[it.sku_code] = it.unit_cost;
    if (it.new_qty != null) bcSimQty[it.sku_code] = it.new_qty;
    if (it.disabled) bcSimDisabled[it.sku_code] = true;
  });
  bcRenderSimEditor(); bcRecomputeKpis();
}
async function bcDeleteScenario() {
  const id = document.getElementById('bcScenSel')?.value;
  if (!id) { alert('เลือก scenario ก่อน'); return; }
  if (!confirm('ลบ scenario นี้?')) return;
  await fetch(`${API}/api/bom-cost/scenarios/${id}`, {method:'DELETE'});
  bcLoadSimulator();
}

// ── Phase B: adopt a scenario + show its realized-savings (ROI) ledger ─────────
async function bcAdoptScenario() {
  const id = document.getElementById('bcScenSel')?.value;
  if (!id) { alert('เลือก scenario ที่บันทึกไว้ก่อน (หรือกด 💾 Save current)'); return; }
  if (!confirm('บันทึกว่า "ใช้จริง" (adopt)?\nระบบจะจับภาพต้นทุนปัจจุบันเป็น baseline แล้วเริ่มนับเงินที่ประหยัดได้จริงตั้งแต่เดือนล่าสุดเป็นต้นไป')) return;
  const r = await fetch(`${API}/api/bom-cost/scenarios/${id}/adopt`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({})
  });
  if (!r.ok) { alert('Adopt ไม่สำเร็จ'); return; }
  bcShowRoi();
}
async function bcUnadopt(id) {
  if (!confirm('ยกเลิกการ adopt? (ล้าง baseline และผลประหยัดสะสม)')) return;
  await fetch(`${API}/api/bom-cost/scenarios/${id}/unadopt`, { method: 'POST' });
  bcShowRoi();
}
async function bcShowRoi() {
  const id = document.getElementById('bcScenSel')?.value;
  const panel = document.getElementById('bcRoiPanel'); if (!panel) return;
  if (!id) { alert('เลือก scenario ก่อน'); return; }
  panel.classList.remove('hidden');
  panel.innerHTML = '<p class="text-sm text-slate-400 py-4 text-center">กำลังโหลด…</p>';
  let d; try { d = await fetch(`${API}/api/bom-cost/scenarios/${id}/savings`).then(r => r.json()); }
  catch (e) { panel.innerHTML = '<p class="text-sm text-red-500 py-4 text-center">โหลดไม่สำเร็จ</p>'; return; }
  bcRenderRoi(d, id);
}
function bcRenderRoi(d, id) {
  const panel = document.getElementById('bcRoiPanel'); if (!panel) return;
  const close = `<button onclick="document.getElementById('bcRoiPanel').classList.add('hidden')" class="topbar-icon-btn" aria-label="ปิด">✕</button>`;
  if (!d.adopted) {
    panel.innerHTML = `<div class="flex items-center justify-between"><h3 class="text-sm font-bold" style="color:#7c3aed">💰 ผลประหยัดจริง (ROI)</h3>${close}</div>
      <p class="text-sm text-slate-500 mt-2">scenario นี้ยังไม่ได้ adopt — กด <b>📌 Adopt &amp; Track ROI</b> เพื่อเริ่มจับ baseline แล้วติดตามเงินที่ประหยัดได้จริง</p>`;
    return;
  }
  const t = d.totals || {}, sc = d.scenario || {};
  const prog = t.progress_pct == null ? 0 : Math.max(0, Math.min(100, t.progress_pct));
  const rows = (d.items || []).map(i => {
    const pp = i.unit_progress_pct;
    return `<tr class="border-b" style="border-color:var(--border)">
      <td class="py-1.5 pr-2 font-mono text-[10px] text-slate-400">${esc(i.sku_code)}</td>
      <td class="py-1.5 pr-2 max-w-xs truncate" title="${esc(i.description||'')}">${esc((i.description||'').replace(/^\((?:A|B|T)\)\s*/,''))}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${fmt(i.baseline_cost)}→${fmt(i.current_cost)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums text-slate-400">${fmt(i.target_cost)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums">${fmtN(i.qty_since_adopt)}</td>
      <td class="py-1.5 pr-2 text-right tabular-nums font-semibold" style="color:${i.realized_saving>0?'#059669':'var(--text-muted)'}">${fmt(i.realized_saving)}</td>
      <td class="py-1.5 text-right tabular-nums text-slate-400">${pp==null?'—':pp.toFixed(0)+'%'}</td></tr>`;
  }).join('');
  panel.innerHTML = `
    <div class="flex items-center justify-between flex-wrap gap-2">
      <h3 class="text-sm font-bold" style="color:#7c3aed">💰 ผลประหยัดจริง (ROI) — ${esc(sc.name||'')}</h3>
      <div class="flex items-center gap-2">
        <span class="text-[11px] text-slate-400">adopt ${esc((sc.adopted_at||'').slice(0,10))} · baseline ${esc(sc.baseline_month||'')} · ${t.months_tracked||0} เดือน</span>
        <button onclick="bcUnadopt('${id}')" class="text-[11px] border border-slate-200 rounded px-2 py-0.5" style="color:var(--text-muted)">ยกเลิก adopt</button>
        ${close}
      </div>
    </div>
    <div class="flex flex-wrap items-end gap-6 mt-3 mb-2">
      <div><div class="text-[11px] text-slate-400">ประหยัดได้จริงสะสม</div>
        <div class="text-2xl font-bold" style="color:#059669">${fmt(t.realized_saving)}</div></div>
      <div><div class="text-[11px] text-slate-400">เป้าหมายเต็ม (ถ้าทำได้ครบ)</div>
        <div class="text-lg font-semibold text-slate-500">${fmt(t.potential_saving)}</div></div>
      <div class="flex-1 min-w-[160px]">
        <div class="text-[11px] text-slate-400 mb-1">ความคืบหน้า ${t.progress_pct==null?'—':t.progress_pct.toFixed(0)+'%'}</div>
        <div style="height:10px;background:var(--bg-subtle);border-radius:6px;overflow:hidden">
          <div style="height:100%;width:${prog}%;background:linear-gradient(90deg,#7c3aed,#059669)"></div></div>
      </div>
    </div>
    <p class="text-[11px] text-slate-400 mb-3">ประหยัด "จริง" = (ต้นทุน/หน่วยตอน adopt − ต้นทุน/หน่วยปัจจุบัน) × จำนวนที่ขายตั้งแต่ adopt — จะขึ้นเมื่อต้นทุนจริงลดลงตามที่วางแผน</p>
    <div class="overflow-auto" style="max-height:300px">
      <table class="text-xs w-full"><thead><tr style="color:var(--text-muted)" class="border-b">
        <th class="py-1 pr-2 text-left">SKU</th><th class="py-1 pr-2 text-left">เมนู</th>
        <th class="py-1 pr-2 text-right">baseline→ปัจจุบัน</th><th class="py-1 pr-2 text-right">เป้า</th>
        <th class="py-1 pr-2 text-right">ขาย(หลัง adopt)</th><th class="py-1 pr-2 text-right">ประหยัดจริง</th><th class="py-1 text-right">คืบหน้า</th>
      </tr></thead><tbody>${rows || '<tr><td colspan="7" class="py-3 text-center text-slate-400">ไม่มีรายการ</td></tr>'}</tbody></table>
    </div>`;
}
