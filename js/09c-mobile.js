// ─── 09c-mobile.js — The mobile owner summary (ms* views) and the app BOOT: ensureChartSetup(), init(), waitForCharts() ───
// Slice 3/5 of the former 09-deep-tools.js (split 2026-09-05 by contiguous range, so every
// statement keeps its load order). Shared global scope: index.html loads js/*.js in name order.

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
  if (v == null) return '<span class="ms-chip" style="background:#f1f5f9;color:#5f6c80">—</span>';
  const good = goodUp ? v >= 0 : v <= 0;
  const c = v === 0 ? '#5f6c80' : good ? '#047857' : '#b91c1c';   // on a fixed light chip
  const bg = v === 0 ? '#f1f5f9' : good ? '#dcfce7' : '#fee2e2';
  return `<span class="ms-chip" style="background:${bg};color:${c}">${v > 0 ? '▲' : v < 0 ? '▼' : ''} ${Math.abs(v)}%</span>`;
}
// avgW (0-100, optional): a tick at the list's average, so above/below reads at a glance.
function msBar(w, col, avgW) {
  const tick = avgW == null ? '' : `<span style="position:absolute;top:0;bottom:0;left:${Math.max(0, Math.min(99, avgW))}%;width:2px;background:var(--text-primary,#1e293b);opacity:.55"></span>`;
  return `<div style="flex:1;position:relative;background:var(--bg-subtle);border-radius:3px;overflow:hidden"><div style="width:${w}%;min-width:2px;height:8px;background:${col}"></div>${tick}</div>`;
}

// ── at-a-glance pieces for the phone. The dashboard's charts get the same from
// the glance layer in js/01-core.js; the sentences come from the same builders
// (glanceTrendSentence / glanceRangeSentence), so both say things one way. ──
const _MS_TH_MON = ['ม.ค.','ก.พ.','มี.ค.','เม.ย.','พ.ค.','มิ.ย.','ก.ค.','ส.ค.','ก.ย.','ต.ค.','พ.ย.','ธ.ค.'];
const _MS_TH_DOW = ['อา.','จ.','อ.','พ.','พฤ.','ศ.','ส.'];
const _msYmd = iso => String(iso || '').slice(0, 10).split('-').map(Number);
function msPeriodLabel(iso, gran) {
  const [y, m, d] = _msYmd(iso);
  return gran === 'month' ? `${_MS_TH_MON[m - 1]} ${String(y + 543).slice(2)}` : `${d} ${_MS_TH_MON[m - 1]}`;
}
const msDow = iso => { const [y, m, d] = _msYmd(iso); return new Date(y, m - 1, d).getDay(); };
// Index (oldest→newest) of the period still in progress, plus "12/30 วัน".
function msPartial(trend, gran) {
  const last = trend.length - 1;
  if (last < 0) return { i: -1, note: '' };
  const [y, m, d] = _msYmd(trend[last].start);
  if (gran === 'month') {
    const ref = _glanceLastDate(), dim = new Date(y, m, 0).getDate();
    return (y === ref.y && m === ref.m && ref.d < dim) ? { i: last, note: `${ref.d}/${dim} วัน` } : { i: -1, note: '' };
  }
  const t = new Date(Date.now() + 7 * 3600e3);   // a day still trading = today, Bangkok
  return (y === t.getUTCFullYear() && m === t.getUTCMonth() + 1 && d === t.getUTCDate()) ? { i: last, note: '' } : { i: -1, note: '' };
}
const msGlanceLine = html => html ? `<p style="font-size:12px;line-height:1.45;color:var(--text-secondary,#475569);margin:2px 0 8px">${html}</p>` : '';

// A small column chart in SVG (no Chart.js on the phone): oldest→newest, the
// latest complete period in the accent, the period in progress hollow + dashed,
// weekends tinted, a dashed average line, value labels on the latest and the peak.
function msColumns(vals, o) {
  const n = vals.length;
  if (n < 2) return '';
  const W = 320, H = o.height || 100, top = 16, bottom = 14, plotH = H - top - bottom;
  const P = o.partial == null ? -1 : o.partial;
  const max = Math.max(1, ...vals.filter(v => v != null));
  const step = W / n, bw = Math.max(3, Math.min(22, step * 0.64));
  const yOf = v => top + plotH * (1 - Math.max(0, v) / max);
  const comp = vals.map((v, i) => i).filter(i => vals[i] != null && i !== P);
  const last = comp.length ? comp[comp.length - 1] : -1;
  const hi = comp.reduce((b, i) => (b < 0 || vals[i] > vals[b]) ? i : b, -1);
  const avg = comp.length >= 3 ? comp.reduce((a, i) => a + vals[i], 0) / comp.length : null;
  const cx = i => i * step + step / 2;
  const placed = [];
  const txt = (t, x, y, size, weight, color, check) => {
    const w = t.length * size * 0.56;
    x = Math.max(w / 2 + 1, Math.min(W - w / 2 - 1, x));
    if (check) {
      if (placed.some(p => Math.abs(p.x - x) < (p.w + w) / 2 + 3 && Math.abs(p.y - y) < size + 2)) return '';
      placed.push({ x, y, w });
    }
    return `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="middle" font-size="${size}" font-weight="${weight}" style="fill:${color}">${esc(t)}</text>`;
  };
  let g = '';
  (o.weekend || []).forEach((w, i) => { if (w) g += `<rect x="${(i * step).toFixed(1)}" y="${top - 4}" width="${step.toFixed(1)}" height="${plotH + 4}" style="fill:var(--bg-subtle,#f1f5f9)"/>`; });
  if (avg != null) {
    const ya = yOf(avg).toFixed(1);
    g += `<line x1="0" x2="${W}" y1="${ya}" y2="${ya}" stroke-width="1" stroke-dasharray="3 3" style="stroke:var(--text-muted,#94a3b8)"/>`;
  }
  vals.forEach((v, i) => {
    if (v == null) return;
    const x = (cx(i) - bw / 2).toFixed(1), y = yOf(v), h = Math.max(1, top + plotH - y);
    if (i === P) g += `<rect x="${x}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" stroke-width="1.2" stroke-dasharray="3 2" style="fill:none;stroke:var(--accent,#1f3a6b)"/>`;
    else g += `<rect x="${x}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="1.5" style="fill:var(--accent,#1f3a6b);opacity:${i === last ? 1 : 0.38}"/>`;
  });
  const lab = (i, t) => txt(t ?? o.fmt(vals[i]), cx(i), yOf(vals[i]) - 4, 10, 600, 'var(--text-primary,#1e293b)', true);
  // priority: the latest complete value, then the period in progress (just its
  // tag - the figure is in the list/tile), then the peak
  if (last >= 0) g += lab(last);
  if (P >= 0 && vals[P] != null) g += lab(P, o.grain === 'day' ? 'วันนี้' : 'MTD');
  if (hi >= 0 && hi !== last) g += lab(hi);
  if (avg != null) g += `<text x="2" y="${(yOf(avg) - 3).toFixed(1)}" font-size="9" style="fill:var(--text-muted,#94a3b8)">เฉลี่ย ${esc(o.fmt(avg))}</text>`;
  if (o.labels) {
    g += txt(o.labels[0], cx(0), H - 2, 9, 400, 'var(--text-muted,#94a3b8)');
    g += txt(o.labels[n - 1], cx(n - 1), H - 2, 9, 400, 'var(--text-muted,#94a3b8)');
  }
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block;font-family:inherit" role="img" aria-label="${esc(o.aria || '')}">${g}</svg>`;
}
// Trend block = sentence + columns, for a trend array oldest→newest from /api/sales/daily.
function msTrendBlock(trend, gran, height) {
  const vals = trend.map(x => x.revenue ?? null);
  const labels = trend.map(x => msPeriodLabel(x.start, gran));
  const part = msPartial(trend, gran);
  const sentence = glanceTrendSentence(labels, vals, { partial: part.i, note: part.note, unit: gran === 'month' ? 'เดือน' : 'วัน', fmt });
  return msGlanceLine(sentence) + msColumns(vals, {
    partial: part.i, labels, fmt, grain: gran, height,
    weekend: gran === 'day' ? trend.map(x => [0, 6].includes(msDow(x.start))) : null,
    aria: 'รายได้ ' + (gran === 'month' ? 'รายเดือน' : 'รายวัน'),
  });
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
  try { _msMonth = await api(`${API}/api/sales/daily?${mq}`); }
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
  msLoadVoice();
}

// ── the customer-voice queue on the phone (E4): what waits for an answer, the
// branch's own when the login is scoped, else the selected branch / the chain ──
async function msLoadVoice() {
  const wrap = document.getElementById('msVoiceWrap'), box = document.getElementById('msVoice');
  if (!wrap || !box) return;
  if (window.AUTH && window.AUTH.enabled && !(window.AUTH.modules || []).includes('customer.feedback')) { wrap.style.display = 'none'; return; }
  const p = new URLSearchParams({ needs_reply: '1', sort: 'priority', limit: '5' });
  const br = (window.AUTH && window.AUTH.branch) || msBranch(); if (br) p.set('branch', br);
  let d;
  try { const r = await fetch(`${API}/api/feedback/items?${p}`); if (!r.ok) { wrap.style.display = 'none'; return; } d = await r.json(); }
  catch (e) { wrap.style.display = 'none'; return; }
  const items = d.items || [];
  wrap.style.display = '';
  document.getElementById('msVoiceCount').textContent = items.length ? `(${items.length}${items.length >= 5 ? '+' : ''})` : '';
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  box.innerHTML = items.length ? items.map(r => `<div style="padding:8px 0;border-bottom:1px solid var(--border,#e2e8f0)">
      <div style="font-size:11px;color:var(--text-muted,#64748b)">${esc(r.source)} · <b style="color:var(--text-primary,#1e293b)">${esc(r.branch_name || '')}</b>${r.rating != null ? ` · ${r.rating}★` : ''} · ${r.age_days === 0 ? 'วันนี้' : (r.age_days || 0) + ' วัน'}${r.sla_breached ? ' <span style="color:var(--bad-ink);font-weight:600">เกิน SLA</span>' : ''}${r.contact ? ` · ${esc(r.contact)}` : ''}</div>
      <div style="font-size:13px;margin:3px 0">${r.text ? esc(r.text.slice(0, 160)) : '<i style="color:var(--text-muted,#64748b)">(ให้ดาวอย่างเดียว)</i>'}</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap"><button onclick="msVoiceHandled(${r.id})" style="font-size:11px;padding:4px 10px;border-radius:4px;border:1px solid #0f766e;background:#0f766e;color:#fff">✓ ตอบแล้ว</button>${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener" style="font-size:11px;padding:4px 10px;border-radius:4px;border:1px solid var(--border,#e2e8f0);color:var(--text-primary,#1e293b);text-decoration:none">เปิดต้นทาง ↗</a>` : ''}<button onclick="showFullDashboard(); switchTab('feedback')" style="font-size:11px;padding:4px 10px;border-radius:4px;border:1px solid var(--border,#e2e8f0);background:transparent;color:var(--text-primary,#1e293b)">ตอบในแอป</button></div>
    </div>`).join('') : `<div style="font-size:12px;color:var(--text-muted,#64748b);padding:6px 0">ไม่มีรายการรอตอบ</div>`;
}
async function msVoiceHandled(id) {
  const note = prompt('ตอบลูกค้าอย่างไร (สรุปสั้น ๆ) — เว้นว่างได้');
  if (note === null) return;
  try {
    const r = await fetch(`${API}/api/feedback/${id}/handled`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note }) });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    msLoadVoice();
  } catch (e) { alert('บันทึกไม่สำเร็จ: ' + e.message); }
}

// ── VIEW: สรุป ──
function msRenderSummary() {
  const m = _msMonth; if (!m) return;
  const t = m.total || {}, branches = m.branches || [];
  document.getElementById('msKpis').innerHTML = [
    { l: 'รายได้ (MTD)', v: fmt(t.revenue || 0), chip: msPchip(t.mom_pct), span: true, c: '#4a70b0' },   // -> --navy-ink, readable in both themes
    { l: 'ลูกค้า', v: fmtN(t.customers || 0), chip: msPchip(t.cust_mom_pct), c: '#0891b2' },
    { l: 'รายได้/หัว', v: t.rev_per_head != null ? fmt(t.rev_per_head) : '—', chip: msPchip(t.rph_mom_pct), c: '#7a66b8' },
  ].map(k => `<div class="ms-kpi" style="${k.span ? 'grid-column:1/-1;' : ''}">
      <p class="lbl">${k.l}</p><p class="val" style="color:${textInk(k.c)}">${k.v}</p>
      <p class="sub">${k.chip} <span style="color:var(--text-muted,#94a3b8);font-size:11px">vs เดือนก่อน</span></p>
      ${k.span && (m.trend || []).length >= 3 ? `<div style="margin-top:10px">${msTrendBlock(m.trend, 'month', 92)}</div>` : ''}
    </div>`).join('');

  const sig = m.anomalies || [];
  const sw = document.getElementById('msSignalsWrap');
  if (sig.length) {
    sw.style.display = '';
    document.getElementById('msSignals').innerHTML = sig.slice(0, 6).map(a => {
      const bad = (a.pct || 0) < 0;
      return `<div class="ms-row"><span style="font-size:16px">${bad ? '●' : '●'}</span>
        <span style="flex:1;font-size:13px;color:var(--text-primary,#1e293b)">${esc(a.msg)}</span></div>`;
    }).join('');
  } else { sw.style.display = 'none'; }

  const withMom = branches.filter(b => b.mom_pct != null);
  const best = withMom.slice().sort((a, b) => b.mom_pct - a.mom_pct)[0];
  const worst = withMom.slice().sort((a, b) => a.mom_pct - b.mom_pct)[0];
  const bwCard = (b, good) => !b ? '' : `<div class="ms-kpi" style="border:1px solid ${good ? '#10b981' : '#ef4444'}">
      <p class="lbl">${good ? 'โตสุด' : '⚠ ตกสุด'}</p>
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
  const revs = branches.map(b => b.revenue).filter(v => v != null);
  const avgW = revs.length >= 3 ? Math.round(100 * revs.reduce((a, v) => a + v, 0) / revs.length / maxRev) : null;
  const metric = _msBranchSort === 'mom' ? { v: b => b.mom_pct, o: { pct: true, fmt: v => `${v > 0 ? '+' : ''}${v}%` } }
               : _msBranchSort === 'rph' ? { v: b => b.rev_per_head || null, o: { fmt: v => fmt(v) + '/หัว', below: true } }
               : { v: b => b.revenue || null, o: { fmt, below: true } };   // 0 = not trading (e.g. a closed branch), not "the lowest"
  const sentence = glanceRangeSentence(branches.map(b => b.branch), branches.map(b => metric.v(b) ?? null), metric.o);
  document.getElementById('msBranches').innerHTML = (sentence ? `<div style="padding-top:8px">${msGlanceLine(sentence)}</div>` : '') + branches.map(b => {
    const right = _msBranchSort === 'rph'
      ? `<span style="font-size:13px;color:var(--text-secondary,#475569);font-variant-numeric:tabular-nums">${b.rev_per_head != null ? fmt(b.rev_per_head) : '—'}/หัว</span>`
      : `<span style="font-size:13px;color:var(--text-secondary,#475569);font-variant-numeric:tabular-nums">${fmt(b.revenue)}</span>${msPchip(b.mom_pct)}`;
    return `<div class="ms-row">
      <span style="flex:1;min-width:0"><span style="font-size:13px;font-weight:500;color:var(--text-primary,#1e293b);display:block">${esc(b.branch)}</span>
      <span style="display:flex;align-items:center;gap:6px;margin-top:3px">${msBar(Math.round(100 * (b.revenue || 0) / maxRev), '#4a70b0', avgW)}</span></span>
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
  let d; try { d = await api(`${API}/api/sales/daily?${q}`); } catch (e) { wrap.innerHTML = '<div class="ms-row">โหลดไม่สำเร็จ</div>'; return; }
  // latest day/month FIRST (owner request 2026-07-20) — the API returns the
  // trend oldest→newest; reverse for display so index 0 = the newest period.
  const gran = _msSalesGran, asc = d.trend || [];
  const part = msPartial(asc, gran);
  const tr = asc.slice().reverse();
  const maxV = Math.max(1, ...tr.map(x => x.revenue || 0));
  const done = asc.filter((x, i) => i !== part.i && x.revenue != null);
  const avgW = done.length >= 3 ? Math.round(100 * done.reduce((a, x) => a + x.revenue, 0) / done.length / maxV) : null;
  const lblOf = x => gran === 'month' ? msPeriodLabel(x.start, 'month') : `${msPeriodLabel(x.start, 'day')} ${_MS_TH_DOW[msDow(x.start)]}`;
  const rows = tr.map((x, i) => {
    const w = Math.round(100 * (x.revenue || 0) / maxV);
    const ai = asc.length - 1 - i, inProg = ai === part.i;
    const latest = i === (part.i >= 0 ? 1 : 0);   // the newest complete period
    const wkend = gran === 'day' && [0, 6].includes(msDow(x.start));
    const tag = inProg ? ` <span class="ms-chip" style="padding:1px 6px;font-size:10px;background:var(--bg-subtle);color:var(--text-secondary,#475569)">${gran === 'day' ? 'วันนี้' : 'MTD'}</span>` : '';
    return `<div class="ms-row" style="${wkend ? 'background:var(--bg-subtle);margin:0 -16px;padding-left:16px;padding-right:16px' : ''}">
      <span style="width:${gran === 'day' ? 76 : 58}px;font-size:11px;font-weight:${latest ? '700' : '400'};color:${latest ? 'var(--text-primary,#1e293b)' : 'var(--text-muted,#64748b)'}">${lblOf(x)}</span>
      ${msBar(w, inProg ? 'var(--border,#cbd5e1)' : latest ? 'var(--accent,#1f3a6b)' : 'color-mix(in srgb, var(--accent,#1f3a6b) 38%, transparent)', avgW)}
      <span style="width:${inProg ? 98 : 64}px;text-align:right;font-size:12px;font-weight:${latest ? '700' : '400'};font-variant-numeric:tabular-nums;color:var(--text-primary,#1e293b)">${fmt(x.revenue || 0)}${tag}</span>
    </div>`;
  }).join('');
  wrap.innerHTML = rows
    ? `<div style="padding:10px 0 8px">${msTrendBlock(asc, gran, 110)}</div>
       <p style="font-size:10px;color:var(--text-muted,#64748b);margin:0 0 2px">ขีดในแถบ = ค่าเฉลี่ย${gran === 'day' ? ' · แถบเทา = เสาร์-อาทิตย์' : ''}</p>${rows}`
    : '<div class="ms-row">ไม่มีข้อมูล</div>';
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
  let d; try { d = await api(`${API}/api/sales/top_ordered?${q}`); } catch (e) { wrap.innerHTML = '<div class="ms-row">โหลดไม่สำเร็จ</div>'; if (rWrap) rWrap.innerHTML = ''; return; }
  const pool = (d.items || []);

  // ── มาแรงเดือนนี้: biggest positive qty-MoM among meaningful-volume menus ──
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

  // ── เมนูขายดี top-20 (unchanged view; first 20 of the qty-sorted pool) ──
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
  let d; try { d = await api(`${API}/api/sales/cost-per-head?${q}`); } catch (e) { br.innerHTML = '<div class="ms-row">โหลดไม่สำเร็จ</div>'; return; }
  const t = d.total || {};
  const cpColor = cp => cp == null ? '#94a3b8' : cp >= 70 ? '#ef4444' : cp >= 55 ? '#f59e0b' : '#10b981';
  kp.innerHTML = [
    { l: 'รายได้/หัว', v: t.rev_per_head != null ? fmt(t.rev_per_head) : '—', c: '#0891b2' },
    { l: 'ต้นทุน/หัว (BoM)', v: t.cost_per_head != null ? fmt(t.cost_per_head) : '—', c: '#ef4444' },
    { l: 'GP/หัว', v: t.gp_per_head != null ? fmt(t.gp_per_head) : '—', c: '#10b981' },
    { l: 'Food cost %', v: t.cost_pct != null ? t.cost_pct + '%' : '—', c: cpColor(t.cost_pct) },
  ].map(k => `<div class="ms-kpi"><p class="lbl">${k.l}</p><p class="val" style="font-size:20px;color:${textInk(k.c)}">${k.v}</p></div>`).join('');
  const branches = (d.branches || []).filter(r => r.cost_pct != null).sort((a, b) => b.cost_pct - a.cost_pct);   // worst first
  const over = branches.filter(r => r.cost_pct >= 55).length;
  const sentence = glanceRangeSentence(branches.map(r => r.branch), branches.map(r => r.cost_pct), { pct: true, fmt: v => v + '%' });
  br.innerHTML = (sentence ? `<div style="padding-top:8px">${msGlanceLine(`Food cost ${sentence}${over ? ` · <b style="color:var(--bad-ink)">${over} สาขา ≥ 55%</b>` : ''}`)}</div>` : '') + branches.map(r => `<div class="ms-row">
      <span style="flex:1;font-size:13px;color:var(--text-primary,#1e293b)">${esc(r.branch)}</span>
      <span style="font-size:12px;color:var(--text-muted,#64748b);font-variant-numeric:tabular-nums">${r.cost_per_head != null ? fmt(r.cost_per_head) : '—'}/หัว</span>
      <span class="ms-chip" style="background:${cpColor(r.cost_pct)}22;color:${textInk(cpColor(r.cost_pct))}">${r.cost_pct}%</span>
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
            <div class="flex-1" style="background:var(--bg-subtle);border-radius:3px;overflow:hidden"><div style="width:${w}%;min-width:2px;height:8px;background:var(--accent)"></div></div>
            <span class="text-[10px] text-slate-400 tabular-nums" style="width:30px">${x.revenue_pct}%</span>
          </div>
        </td>
        <td class="py-1.5 pr-2 text-right tabular-nums">${fmtN(x.covers)}</td>
        <td class="py-1.5 pr-2 text-right tabular-nums">${x.rev_per_cover!=null?fmtN(x.rev_per_cover):'—'}</td>
        <td class="py-1.5 pr-2 text-right tabular-nums">${x.avg_bill!=null?fmtN(x.avg_bill):'—'}</td>
        <td class="py-1.5 pr-2 text-right tabular-nums text-slate-500">${x.drink_pct}%</td>
        <td class="py-1.5 text-right tabular-nums font-semibold" style="color:${textInk(gpCol(x.est_gp_margin_pct))}">${fmt(x.est_gross_profit)}${x.est_gp_margin_pct!=null?' · '+x.est_gp_margin_pct+'%':''}${isTopGp?'':''}</td>
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
      else if (slope > 0) { fb.textContent = '▲ ขาขึ้น'; fb.style.color = 'var(--good-ink)'; }
      else if (slope < 0) { fb.textContent = '▼ ขาลง'; fb.style.color = 'var(--bad-ink)'; }
      else { fb.textContent = '→ ทรงตัว'; fb.style.color = 'var(--text-muted)'; }
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
        { label:'จริง', data:histData, borderColor:'#4a70b0', spanGaps:false },
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
    const medal = i => i===0?'1.':i===1?'2.':i===2?'3.':(i+1);
    document.getElementById('more-turnover').innerHTML = tbr.map((b,i)=>{
      const t = b.avg_turn_min;
      const fast = t!=null && t<=med;
      const col = t==null ? '#94a3b8' : fast ? '#10b981' : '#f59e0b';
      const bg  = t==null ? '#f8fafc' : fast ? '#ecfdf5' : '#fffbeb';
      return `<div class="rounded-lg p-2.5 text-center on-light" style="background:${bg};border:1px solid ${col}33">
        <div class="text-[11px] font-medium text-slate-500 truncate" title="${esc(b.branch)}">${medal(i)} ${esc(b.branch)}</div>
        <div class="font-bold tabular-nums leading-tight" style="color:${textInk(col)};font-size:1.35rem">${t!=null?t:'—'}<span class="text-[10px] font-normal text-slate-400"> น./รอบ</span></div>
        <div class="text-[10px] text-slate-400 tabular-nums mt-0.5">${fmtN(b.turns)} รอบ · ${b.avg_covers_per_turn!=null?b.avg_covers_per_turn:'—'} คน/รอบ</div>
      </div>`;
    }).join('') || '<div class="col-span-full py-3 text-center text-slate-400 text-sm">—</div>';
  }

  // ── auto-generated highlight chips (from the three datasets above) ──
  const chips = [];
  if (dayparts.length) {
    const topRev = dayparts.slice().sort((a,b)=>(b.revenue||0)-(a.revenue||0))[0];
    chips.push({ icon:'', label:'ช่วงทำเงินสูงสุด', val:esc(topRev.label),
                 sub:`${fmt(topRev.revenue)} · ${topRev.revenue_pct}% ของรายได้`, c:'#4a70b0' });
    const wGp = dayparts.filter(x=>x.est_gp_margin_pct!=null).sort((a,b)=>b.est_gp_margin_pct-a.est_gp_margin_pct)[0];
    if (wGp) chips.push({ icon:'', label:'กำไร(margin)ดีสุด', val:esc(wGp.label),
                 sub:`GP ${wGp.est_gp_margin_pct}% · ดื่ม ${wGp.drink_pct}%`, c:'#10b981' });
  }
  if (tbr.length) {
    const fastest = tbr.filter(b=>b.avg_turn_min!=null).sort((a,b)=>a.avg_turn_min-b.avg_turn_min)[0];
    if (fastest) chips.push({ icon:'', label:'หมุนโต๊ะเร็วสุด', val:esc(fastest.branch),
                 sub:`${fastest.avg_turn_min} นาที/รอบ`, c:'#0891b2' });
  }
  if (fc && fc.trend) {
    const up = fc.trend.slope_per_month > 0;
    chips.push({ icon: up?'▲':'▼', label:'เทรนด์รายได้', val: up?'ขาขึ้น':'ขาลง',
                 sub:`${fmt(fc.trend.slope_per_month)}/เดือน · R²=${fc.trend.r_squared}`, c: up?'#10b981':'#ef4444' });
  }
  const hc = document.getElementById('more-highlights');
  if (hc) hc.innerHTML = chips.map(c=>`
    <div class="card p-3" style="border:1px solid ${c.c}">
      <p class="text-[11px] text-slate-400">${c.icon} ${c.label}</p>
      <p class="text-lg font-bold leading-tight" style="color:${textInk(c.c)}">${c.val}</p>
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
  Chart.register(GLANCE_PLUGIN);   // js/01-core.js: depth + latest/peak/avg labels on every chart
  setupChartDefaults();
  _chartSetupDone = true;
}

// Global Chart.js defaults — one place that makes EVERY chart more legible.
// Per-chart options still override these; this only fills the gaps (typography,
// tooltips, rounded bars, tidy points/legends).
function setupChartDefaults() {
  if (typeof Chart === 'undefined') return;
  const FONT = "'KIRA UI','Noto Sans Thai',-apple-system,'Segoe UI',sans-serif";
  Chart.defaults.font.family = FONT;
  Chart.defaults.font.size = 11.5;
  Chart.defaults.color = '#475569';
  Chart.defaults.devicePixelRatio = Math.min(window.devicePixelRatio || 1, 2); // crisp lines

  // Rounded, cleaner bars
  Chart.defaults.elements.bar.borderRadius = 4;      // formal theme: a firm, lightly rounded tip...
  Chart.defaults.elements.bar.borderSkipped = 'start'; // ...sitting flush on the baseline
  Chart.defaults.elements.bar.maxBarThickness = 46;

  // Smoother lines, smaller resting points that grow on hover (less clutter)
  Chart.defaults.elements.line.tension = 0.25;
  Chart.defaults.elements.line.borderWidth = 2;
  Chart.defaults.elements.point.radius = 2.2;     // visible (keeps scatter charts alive) but small
  Chart.defaults.elements.point.hoverRadius = 6;
  Chart.defaults.elements.point.hitRadius = 10;

  // Tidier legends
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.pointStyle = 'rect';   // square legend markers
  Chart.defaults.plugins.legend.labels.boxWidth = 8;
  Chart.defaults.plugins.legend.labels.boxHeight = 8;
  Chart.defaults.plugins.legend.labels.padding = 14;

  // Readable tooltips: dark card, generous padding, bold title, thousand-separated
  // numbers. A custom per-chart tooltip.callbacks.label still overrides this.
  const tt = Chart.defaults.plugins.tooltip;
  tt.backgroundColor = 'rgba(15,23,42,0.96)';
  tt.titleColor = '#f8fafc';
  tt.bodyColor = '#e2e8f0';
  tt.titleFont = { family: FONT, size: 12.5, weight: '600' };
  tt.bodyFont = { family: FONT, size: 12 };
  tt.padding = 11;
  tt.cornerRadius = 3;
  tt.boxPadding = 5;
  tt.usePointStyle = true;
  tt.pointStyle = 'rect';
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
