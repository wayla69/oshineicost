/* ═══════════════════════════════════════════════════════════════════
   เสียงลูกค้า (Customer Feedback) — a working screen in three layers:
   PULSE   KPI tiles with the move against the previous window, a trend chart,
           the mix of kinds / sources / sentiment
   QUEUE   "กล่องงาน": what must be answered, ordered by urgency (unanswered →
           past the SLA → pinned → worst stars → oldest), with reply / handled /
           pin actions and the reply panel inline
   INSIGHT themes (Δ vs before, example quotes, which branches drive them),
           the branch league (Δ ★ and Δ neg %, unanswered, pinned, main pain),
           menu mentions, positive quotes to pass on, the full list + CSV
   Data: /api/feedback/summary (+prev), /api/feedback/items, /api/feedback/sources.
   Follows the global branch + month range; a branch clicked here narrows further.
   ═══════════════════════════════════════════════════════════════════ */
const _FB_KINDS = [['', 'ทั้งหมด'], ['review', 'รีวิว'], ['comment', 'คอมเมนต์'], ['mention', 'การพูดถึง'], ['message', 'ข้อความ Inbox'], ['survey', 'แบบสอบถาม']];
const _FB_QUEUE = [['open', 'ต้องตอบ'], ['sla', 'เกิน SLA'], ['flag', 'ปักหมุด'], ['neg', 'เชิงลบทั้งหมด']];
const _FB_KIND_LABEL = { review: 'รีวิว', comment: 'คอมเมนต์', mention: 'พูดถึง', message: 'Inbox', survey: 'แบบสอบถาม' };
let _fbKind = '', _fbBranchLocal = '', _fbQueueFilter = 'open', _fbTheme = '', _fbSummary = null, _fbBranchSort = 'neg_pct', _fbItemFilter = {};

function _fbEsc(s) { return String(s === undefined || s === null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
/* survey.contact is a guest's phone OR LINE id (backend/routers/feedback.py) — link only what's dialable */
function _fbContactHtml(c) {
  const digits = String(c || '').replace(/[\s\-().]/g, '');
  const tel = /^\+?\d{8,15}$/.test(digits) ? 'tel:' + digits : null;
  return tel ? `<a href="${tel}" style="color:inherit;text-decoration:underline" onclick="event.stopPropagation()" title="โทรออก">${_fbEsc(c)}</a>` : _fbEsc(c);
}
function _fbSentChip(s) {
  const m = { pos: ['บวก', '#047857', '#ecfdf5'], neg: ['ลบ', '#b91c1c', '#fef2f2'], neu: ['กลาง', '#5f6c80', '#f3f4f6'] }[s] || ['—', '#5f6c80', '#f3f4f6'];
  return `<span class="text-[11px] px-2 py-0.5 rounded-full" style="color:${m[1]};background:${m[2]}">${m[0]}</span>`;
}
function _fbQp() {
  const qp = new URLSearchParams();
  const b = _fbBranchLocal || getBranch(); if (b) qp.set('branch', b);
  if (getMonthFrom()) qp.set('month_from', getMonthFrom());
  if (getMonthTo()) qp.set('month_to', getMonthTo());
  if (_fbKind) qp.set('kind', _fbKind);
  return qp;
}
async function _fbPost(path, body) {
  const r = await fetch(`${API}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  let j = {}; try { j = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : (j.detail && j.detail.message) || `HTTP ${r.status}`);
  return j;
}
/* The reply state of one voice: a ตอบ link, or where the queued reply stands. */
function _fbReplyChip(r, big) {
  if (!r.external_id) return '';
  const s = r.reply_status;
  if (r.kind === 'mention' && !s && !r.reply_text) return '';   // a mention has nowhere to reply to
  const chip = (t, fg, bg, title) => `<span class="text-[10px] px-1.5 py-0.5 rounded" style="background:${bg};color:${fg}" title="${_fbEsc(title || '')}">${t}</span>`;
  const link = (t, fn) => `<a href="#" class="text-[11px]" style="color:var(--sky-ink)" onclick="${fn}(${r.id});return false;">${t}</a>`;
  if (s === 'pending') return chip('รอส่ง (รอบ 09:00)', '#92400e', '#fef3c7') + ' ' + link('ยกเลิก', 'cancelFeedbackReply');
  if (s === 'sent' || r.reply_text || r.replied) return chip('ตอบแล้ว ✓', '#047857', '#ecfdf5');
  if (s === 'failed') return chip('ส่งไม่สำเร็จ', '#b91c1c', '#fef2f2', r.reply_error) + ' ' + link('ลองใหม่', 'openFeedbackReply');
  if (s === 'manual') return chip('ตอบเองในแอป', '#0369a1', '#eff6ff', 'ตอบผ่านระบบไม่ได้ — คัดลอกข้อความที่บันทึกไว้ไปตอบในแพลตฟอร์ม') + ' ' + link('ดูข้อความ', 'openFeedbackReply');
  return big ? `<button class="fb-btn" onclick="openFeedbackReply(${r.id})">ตอบ</button>` : link('ตอบ', 'openFeedbackReply');
}

async function _fbFillBranches() {
  const sel = document.getElementById('fbBranch');
  if (!sel || sel.dataset.loaded) return;
  try {
    const bs = await api(`${API}/api/branches`);
    (bs || []).filter(b => b.active !== false).forEach(b => { const o = document.createElement('option'); o.value = b.name; o.textContent = b.name; sel.appendChild(o); });
    sel.dataset.loaded = '1';
  } catch (e) {}
}

/* ── load ── */
function _fbScope() { return (window.AUTH && window.AUTH.branch) || ''; }
async function loadFeedback(force) {
  if (_fbScope()) _fbBranchLocal = _fbScope();          // E4: this login sees its own branch only
  await _fbFillBranches();
  const fk = _filterKey() + '|' + _fbKind + '|' + _fbBranchLocal;
  if (!force && _tabLastKey.feedback === fk) return;
  const st = document.getElementById('fbStatus');
  if (st) st.textContent = 'กำลังโหลด…';
  let d;
  try { d = await api(`${API}/api/feedback/summary?${_fbQp()}`); }
  catch (e) { if (st) st.textContent = 'โหลดข้อมูลไม่สำเร็จ'; return; }
  _tabLastKey.feedback = fk;
  _fbSummary = d;
  renderFeedback(d);
  loadFeedbackQueue();
  loadFeedbackItems(_fbQp());
  loadFeedbackSources();
  loadFeedbackActions();
  if (document.getElementById('fbBiz')?.open) loadFeedbackBusiness();     // already-open sections follow the filter change too, not just first open
}
function setFeedbackKind(k) { _fbKind = k || ''; loadFeedback(true); }
function setFeedbackBranch(name) { if (_fbScope()) return; _fbBranchLocal = name || ''; _fbTheme = ''; loadFeedback(true); }

/* ── PULSE ── */
function _fbDelta(cur, prev, { pct = false, goodUp = true, digits = 1, unit = '' } = {}) {
  if (cur === null || cur === undefined || prev === null || prev === undefined) return '<span class="fb-delta flat">ไม่มีช่วงเทียบ</span>';
  const d = cur - prev;
  if (Math.abs(d) < (pct ? 0.05 : 0.005)) return `<span class="fb-delta flat">เท่าเดิม</span>`;
  const good = goodUp ? d > 0 : d < 0;
  return `<span class="fb-delta ${good ? 'good' : 'bad'}">${d > 0 ? '▲' : '▼'} ${Math.abs(d).toFixed(digits)}${unit}</span>`;
}
function renderFeedback(d) {
  const prev = (d.prev && d.prev.total) ? d.prev : null, resp = d.response || {};
  const pl = document.getElementById('fbPeriod');
  const b = _fbBranchLocal || getBranch();
  if (pl) pl.textContent = `${getMonthFrom() || '…'} → ${getMonthTo() || '…'}${b ? ' · ' + b : ' · ทุกสาขา'}${prev ? ` · เทียบกับ ${prev.range[0]} → ${prev.range[1]}` : ''}`;
  const eng = Object.entries(d.analysed_by || {}).map(([k, v]) => `${k} ${v}`).join(' · ');
  const st = document.getElementById('fbStatus');
  if (st) st.textContent = d.total ? `${d.total.toLocaleString()} เสียง · วิเคราะห์โดย ${eng || '—'}${d.unreplied ? ` · รอสาขาตอบ ${d.unreplied}` : ' · ไม่มีรายการรอตอบ'}${d.sla_breached ? ` (เกิน ${d.sla_days} วัน ${d.sla_breached})` : ''}${d.flagged ? ` · ⚑ ${d.flagged}` : ''}` : 'ยังไม่มีเสียงลูกค้าในช่วงที่เลือก';
  // kind chips + local branch chip
  const kinds = d.kinds || {}, total = Object.values(kinds).reduce((a, x) => a + x, 0);
  document.getElementById('fbKindChips').innerHTML = _FB_KINDS.map(([k, label]) => `<button type="button" class="fb-chip${k === _fbKind ? ' on' : ''}" onclick="setFeedbackKind('${k}')">${label} ${k ? (kinds[k] || 0) : total}</button>`).join('');
  document.getElementById('fbBranchChip').innerHTML = _fbScope() ? `<span class="fb-chip on" title="บัญชีนี้เห็นเฉพาะสาขาของตน">สาขาของคุณ: ${_fbEsc(_fbScope())}</span>` :
    (_fbBranchLocal ? `<button type="button" class="fb-chip on" onclick="setFeedbackBranch('')" title="ล้างตัวกรองสาขา">สาขา: ${_fbEsc(_fbBranchLocal)} ✕</button>` : '');
  // empty state
  const empty = document.getElementById('fbEmpty');
  if (!d.total) {
    empty.style.display = '';
    empty.innerHTML = `<div class="fb-empty"><div class="text-3xl"></div><p class="font-semibold mt-1">ยังไม่มีเสียงลูกค้าในช่วงนี้</p><p class="text-xs mt-1">เปิดการดึงอัตโนมัติจาก Google / Facebook / Instagram ตาม Checklist หรือวาง/อัปโหลดรีวิวเองด้านล่าง${_fbBranchLocal || _fbKind ? ' · หรือลองล้างตัวกรองสาขา/ประเภท' : ''}</p><div class="flex justify-center gap-2 mt-3"><button class="fb-btn" onclick="switchTab('checklist')">ไปที่ Checklist เปิดใช้</button><button class="fb-ghost" onclick="toggleFeedbackImport()">นำเข้าเอง</button></div></div>`;
  } else { empty.style.display = 'none'; empty.innerHTML = ''; }
  // KPI tiles
  const s = d.sentiment || {};
  const tile = (label, value, delta, sub, color) => `<div class="card fb-kpi" style="border-top-color:${color}"><div class="l">${label}</div><div class="v" style="color:${textInk(color)}">${value}</div><div class="d">${delta}</div>${sub ? `<div class="d">${sub}</div>` : ''}</div>`;
  document.getElementById('fbKpis').innerHTML =
    tile('คะแนนเฉลี่ย', d.avg_rating != null ? d.avg_rating.toFixed(2) + ' ★' : '—', _fbDelta(d.avg_rating, prev && prev.avg_rating, { digits: 2, goodUp: true }), 'จากรีวิวที่มีดาว', '#ca8a04') +
    tile('รีวิวทั้งหมด', (d.total || 0).toLocaleString(), _fbDelta(d.total, prev && prev.total, { digits: 0, goodUp: true }), `บวก ${s.pos || 0} · กลาง ${s.neu || 0} · ลบ ${s.neg || 0}`, '#0369a1') +
    tile('เชิงลบ', `${d.neg_pct}%`, _fbDelta(d.neg_pct, prev && prev.neg_pct, { pct: true, goodUp: false, unit: ' pt' }), `${s.neg || 0} เสียง`, '#b91c1c') +
    tile('อัตราตอบกลับ', resp.rate != null ? resp.rate + '%' : '—', _fbDelta(resp.rate, prev && prev.response_rate, { pct: true, goodUp: true, digits: 0, unit: ' pt' }), resp.answerable ? `ตอบแล้ว ${resp.answered}/${resp.answerable}${resp.median_days != null ? ` · กลาง ${resp.median_days} วัน` : ''}` : 'ยังไม่มีเสียงลบที่ตอบผ่านระบบได้', '#047857') +
    tile('รอสาขาตอบ', `${d.unreplied || 0}`, _fbDelta(d.sla_breached, prev && prev.sla_breached, { digits: 0, goodUp: false }), (d.sla_breached ? `<span style="color:var(--bad-ink);font-weight:600">เกิน ${d.sla_days} วัน ${d.sla_breached}</span>` : `ทุกรายการยังอยู่ใน SLA ${d.sla_days} วัน`) + (d.severe ? ` · <span style="color:var(--bad-ink);font-weight:600">ร้ายแรง ${d.severe}</span>` : ''), (d.sla_breached || d.severe) ? '#b91c1c' : '#0f766e');
  document.getElementById('fbSlaDays').textContent = d.sla_days || 1;
  _fbTrend(d);
  // mix
  const srcs = Object.entries(d.sources || {}).sort((a, x) => x[1] - a[1]);
  const mx = Math.max(1, ...srcs.map(x => x[1]));
  const tot = d.total || 1;
  document.getElementById('fbMix').innerHTML =
    `<div class="mb-2"><div class="flex justify-between text-[11px]" style="color:var(--text-muted)"><span>ความรู้สึก</span><span>${d.total} เสียง</span></div>
       <div class="flex rounded overflow-hidden mt-1" style="height:12px;background:#e5e7eb"><div style="width:${100 * (s.pos || 0) / tot}%;background:#047857"></div><div style="width:${100 * (s.neu || 0) / tot}%;background:#9ca3af"></div><div style="width:${100 * (s.neg || 0) / tot}%;background:#b91c1c"></div></div>
       <div class="flex justify-between text-[11px] mt-0.5"><span style="color:var(--good-ink)">บวก ${(100 * (s.pos || 0) / tot).toFixed(0)}%</span><span style="color:var(--text-muted)">กลาง ${(100 * (s.neu || 0) / tot).toFixed(0)}%</span><span style="color:var(--bad-ink)">ลบ ${(100 * (s.neg || 0) / tot).toFixed(0)}%</span></div></div>` +
    (srcs.length ? `<div class="text-[11px] mb-1" style="color:var(--text-muted)">ช่องทาง</div>` + srcs.map(([k, v]) => `<div class="flex items-center gap-2 text-xs mb-1"><span style="width:88px">${_fbEsc(k)}</span><div class="fb-bar" style="flex:1"><i style="width:${100 * v / mx}%;background:#0369a1"></i></div><span style="width:34px;text-align:right">${v}</span></div>`).join('') : '') +
    `<div class="text-[11px] mt-2" style="color:var(--text-muted)">วิเคราะห์โดย ${eng || '—'}${(d.analysed_by || {}).claude ? '' : ' · <span title="ตั้ง ANTHROPIC_API_KEY บน Fly เพื่อให้ Claude อ่านประชด/บริบทซับซ้อนได้">พจนานุกรม: ตั้ง Claude ได้ใน Checklist เฟส 7</span>'}</div>`;
  _fbThemes(d);
  _fbBranches(d);
  // menus
  document.getElementById('fbMenus').innerHTML = (d.menus || []).length ?
    `<table class="fb-t"><thead><tr><th>เมนู</th><th style="text-align:right">พูดถึง</th><th style="text-align:right">บวก</th><th style="text-align:right">ลบ</th><th></th></tr></thead><tbody>` +
    (d.menus || []).map(m => `<tr class="row" onclick="loadFeedbackItems(null,{menu:'${_fbEsc(m.menu).replace(/'/g, "\\'")}'});document.getElementById('fbAllCard').open=true;"><td>${_fbEsc(m.menu)}</td><td style="text-align:right">${m.n}</td><td style="text-align:right;color:var(--good-ink)">${m.pos}</td><td style="text-align:right;color:var(--bad-ink)">${m.neg}</td><td class="text-[11px]" style="color:var(--text-muted)">${m.neg > m.pos ? '⚠ ลบมากกว่าบวก' : ''}</td></tr>`).join('') + '</tbody></table>' :
    '<p class="text-xs" style="color:var(--text-muted)">ยังไม่พบเมนูในเสียงลูกค้า (จับจากชื่อเมนูในระบบ)</p>';
  // staff named in the voices
  const staffEl = document.getElementById('fbStaff');
  if (staffEl) staffEl.innerHTML = (d.staff || []).length ?
    `<table class="fb-t"><thead><tr><th>ชื่อ</th><th>สาขา</th><th style="text-align:right">พูดถึง</th><th style="text-align:right">ชม</th><th style="text-align:right">บ่น</th></tr></thead><tbody>` +
    d.staff.map(s => `<tr class="row" title="${_fbEsc((s.examples || []).map(e => e.text).join(' | '))}"><td><b>${_fbEsc(s.name)}</b></td><td class="text-[11px]" style="color:var(--text-muted)">${_fbEsc(s.branch || '')}</td><td style="text-align:right">${s.n}</td><td style="text-align:right;color:var(--good-ink)">${s.pos}</td><td style="text-align:right;color:var(--bad-ink)">${s.neg}</td></tr>`).join('') + '</tbody></table>' :
    '<p class="text-xs" style="color:var(--text-muted)">ยังไม่พบชื่อพนักงานในเสียงลูกค้า (จับจาก "พนักงานชื่อ…", "น้อง…", "คุณ…")</p>';
  // positives
  document.getElementById('fbPos').innerHTML = (d.recent_positive || []).length ? (d.recent_positive || []).map(r =>
    `<div class="fb-quote"><div>${_fbEsc(r.text)}</div><div class="m">${_fbEsc(r.branch || '')} · ${_fbEsc(r.source || '')} · ${_fbEsc(r.date || '')}${r.rating != null ? ' · ' + r.rating + '★' : ''} <button class="fb-ghost" style="padding:0 .4rem;font-size:10px" onclick="fbCopy(this, ${JSON.stringify(`${r.text} — ลูกค้า ${r.branch || ''} (${r.source || ''} ${r.date || ''})`).replace(/"/g, '&quot;')})">คัดลอก</button></div></div>`).join('') :
    '<p class="text-xs" style="color:var(--text-muted)">ยังไม่มีเสียงชมในช่วงนี้</p>';
}

function _fbTrend(d) {
  const days = d.by_day || [], months = d.by_month || [];
  const useDays = days.length && days.length <= 70;
  const pts = useDays ? days : months.filter(m => m.month !== 'ไม่ระบุ');
  const hint = document.getElementById('fbTrendHint');
  if (!pts.length) { hint.textContent = ''; mkChart('fbTrend', { type: 'bar', data: { labels: [], datasets: [] }, options: {} }); return; }
  hint.textContent = useDays ? 'รายวัน · แท่ง = จำนวนเสียง · เส้น = % เชิงลบ' : 'รายเดือน · แท่ง = จำนวนเสียง · เส้น = % เชิงลบ';
  const labels = pts.map(p => useDays ? p.day.slice(5) : p.month);
  mkChart('fbTrend', {
    type: 'bar',
    data: { labels, datasets: [
      { type: 'bar', label: 'เสียง', data: pts.map(p => p.n), backgroundColor: 'rgba(15,118,110,.35)', borderColor: '#0f766e', borderWidth: 1, yAxisID: 'y', order: 2, maxBarThickness: 44, datalabels: { display: true, anchor: 'end', align: 'end', color: '#0f766e', font: { size: 10 } } },
      { type: 'line', label: '% เชิงลบ', data: pts.map(p => p.n ? Math.round(100 * p.neg / p.n) : null), borderColor: '#b91c1c', backgroundColor: '#b91c1c', tension: .3, pointRadius: 2, yAxisID: 'y1', order: 1, spanGaps: true, datalabels: { display: false } }] },
    options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: true, labels: { boxWidth: 10, font: { size: 10 } } } },
      scales: { y: { beginAtZero: true, ticks: { precision: 0 } }, y1: { position: 'right', min: 0, max: 100, grid: { drawOnChartArea: false }, ticks: { callback: v => v + '%' } } } }
  });
}

/* ── INSIGHT: themes ── */
function _fbThemes(d) {
  const prev = (d.prev && d.prev.themes) || null;
  const themes = d.themes || [];
  const maxT = Math.max(1, ...themes.map(t => t.n));
  document.getElementById('fbThemes').innerHTML = themes.length ? themes.map(t => {
    const pos = t.n ? Math.round(100 * (t.n - t.neg) / t.n) : 50;
    const dl = prev ? _fbDelta(t.n, prev[t.theme] || 0, { digits: 0, goodUp: false }) : '';
    return `<div class="fb-theme${_fbTheme === t.theme ? ' on' : ''}" onclick="showFeedbackTheme('${_fbEsc(t.theme).replace(/'/g, "\\'")}')"><span>${_fbEsc(t.theme)}</span><div class="fb-bar" style="--pos:${pos}%"><i style="width:${(100 * t.n / maxT).toFixed(0)}%"></i></div><span style="width:96px;text-align:right;white-space:nowrap;color:var(--text-muted)">${t.n} · ลบ ${t.neg_pct}%</span><span style="width:78px;text-align:right">${dl}</span></div>`; }).join('') :
    '<p class="text-xs" style="color:var(--text-muted)">ยังไม่มีข้อมูล</p>';
  showFeedbackTheme(_fbTheme, true);
}
function showFeedbackTheme(name, keep) {
  if (!keep) _fbTheme = (_fbTheme === name) ? '' : name;
  const box = document.getElementById('fbThemeDetail');
  const t = ((_fbSummary || {}).themes || []).find(x => x.theme === _fbTheme);
  document.querySelectorAll('#fbThemes .fb-theme').forEach(el => el.classList.toggle('on', el.textContent.trim().startsWith(_fbTheme) && !!_fbTheme));
  if (!t) { box.innerHTML = ''; return; }
  box.innerHTML = `<div class="rounded p-3" style="background:var(--bg-input)"><div class="flex items-baseline justify-between flex-wrap gap-2"><b class="text-sm">${_fbEsc(t.theme)}</b><span class="text-[11px]" style="color:var(--text-muted)">${t.n} เสียง · ลบ ${t.neg} · บวก ${t.pos || 0}${(t.branches || []).length ? ' · สาขาที่พูดถึงมากสุด: ' + t.branches.map(_fbEsc).join(', ') : ''}</span></div>` +
    ((t.examples || []).length ? t.examples.map(e => `<div class="text-xs mt-1.5 pl-2" style="border:1px solid #fca5a5">"${_fbEsc(e.text)}" <span style="color:var(--text-muted)">— ${_fbEsc(e.branch || '')} ${_fbEsc(e.date || '')}</span></div>`).join('') : '<p class="text-xs mt-1" style="color:var(--text-muted)">ไม่มีเสียงลบในหัวข้อนี้</p>') +
    `<div class="mt-2 flex gap-2 flex-wrap"><button class="fb-btn" onclick="openActionForm({theme:'${_fbEsc(t.theme).replace(/'/g, "\\'")}', branch:'${_fbEsc(_fbBranchLocal || (t.branches || [])[0] || '').replace(/'/g, "\\'")}'})">สร้างงานแก้</button><button class="fb-ghost" onclick="loadFeedbackItems(null,{theme:'${_fbEsc(t.theme).replace(/'/g, "\\'")}'});document.getElementById('fbAllCard').open=true;document.getElementById('fbAllCard').scrollIntoView({behavior:'smooth'})">ดูทุกเสียงในหัวข้อนี้ →</button>${(t.branches || []).length ? `<button class="fb-ghost" onclick="setFeedbackBranch('${_fbEsc(t.branches[0]).replace(/'/g, "\\'")}')">เจาะสาขา ${_fbEsc(t.branches[0])}</button>` : ''}</div></div>`;
}

/* ── INSIGHT: branch league ── */
function _fbBranches(d) {
  const prev = (d.prev && d.prev.branches) || {};
  let rows = (d.branches || []).slice();
  const key = _fbBranchSort;
  rows.sort((a, b) => key === 'branch' ? String(a.branch).localeCompare(String(b.branch), 'th') : ((b[key] == null ? -1e9 : b[key]) - (a[key] == null ? -1e9 : a[key])));
  const th = (k, label, align) => `<th style="text-align:${align || 'left'}${key === k ? ';color:var(--sky-ink)' : ''}" onclick="_fbBranchSort='${k}';_fbBranches(_fbSummary)">${label}${key === k ? ' ▾' : ''}</th>`;
  document.getElementById('fbBranches').innerHTML = rows.length ?
    `<table class="fb-t"><thead><tr>${th('branch', 'สาขา')}${th('n', 'เสียง', 'right')}${th('avg_rating', '★', 'right')}${th('neg_pct', 'ลบ %', 'right')}${th('unreplied', 'รอตอบ', 'right')}${th('flagged', '⚑', 'right')}<th>ปัญหาหลัก</th></tr></thead><tbody>` +
    rows.map(b => { const p = prev[b.branch] || null;
      return `<tr class="row${_fbBranchLocal === b.branch ? ' on' : ''}" onclick="setFeedbackBranch('${_fbEsc(b.branch).replace(/'/g, "\\'")}')"><td>${_fbEsc(b.branch)}</td><td style="text-align:right">${b.n}</td><td style="text-align:right">${b.avg_rating != null ? b.avg_rating.toFixed(2) : '—'} ${p ? _fbDelta(b.avg_rating, p.avg_rating, { digits: 2, goodUp: true }) : ''}</td><td style="text-align:right;color:${b.neg_pct >= 30 ? 'var(--bad-ink)' : 'inherit'}">${b.neg_pct}% ${p ? _fbDelta(b.neg_pct, p.neg_pct, { pct: true, goodUp: false, unit: 'pt' }) : ''}</td><td style="text-align:right;${b.unreplied ? 'color:var(--bad-ink);font-weight:600' : ''}">${b.unreplied || 0}</td><td style="text-align:right">${b.flagged || ''}</td><td class="text-[11px]" style="color:var(--text-secondary)">${_fbEsc(b.top_theme || '')}</td></tr>`; }).join('') + '</tbody></table>' :
    '<p class="text-xs" style="color:var(--text-muted)">ยังไม่มีข้อมูล</p>';
}

/* ── QUEUE ── */
function setFeedbackQueueFilter(f) { _fbQueueFilter = f; loadFeedbackQueue(); }
async function loadFeedbackQueue() {
  const host = document.getElementById('fbNeg');
  document.getElementById('fbQueueFilters').innerHTML = _FB_QUEUE.map(([k, l]) => `<button type="button" class="fb-chip${_fbQueueFilter === k ? ' on' : ''}" onclick="setFeedbackQueueFilter('${k}')">${l}</button>`).join('');
  const p = _fbQp(); p.set('sort', 'priority'); p.set('limit', '80');
  if (_fbQueueFilter === 'open') p.set('needs_reply', '1');
  if (_fbQueueFilter === 'sla') p.set('sla', '1');
  if (_fbQueueFilter === 'flag') p.set('flagged', '1');
  if (_fbQueueFilter === 'neg') p.set('sentiment', 'neg');
  let d; try { d = await api(`${API}/api/feedback/items?${p}`); } catch (e) { host.innerHTML = '<p class="text-xs" style="color:var(--bad-ink)">โหลดกล่องงานไม่สำเร็จ</p>'; return; }
  const items = d.items || [];
  if (!items.length) { host.innerHTML = `<p class="text-xs py-3" style="color:var(--text-muted)">${{ open: 'ไม่มีรายการรอตอบ', sla: 'ไม่มีรายการเกิน SLA', flag: 'ไม่มีรายการปักหมุด', neg: 'ไม่มีเสียงลบในช่วงนี้' }[_fbQueueFilter]}</p>`; return; }
  host.innerHTML = items.map(r => `<div class="fb-queue-item${r.flag ? ' hot' : ''}" id="fbq-${r.id}">
      <div>
        <div class="meta"><span class="fb-src">${_fbEsc(r.source || '')}</span><b style="color:var(--text-primary)">${_fbEsc(r.branch_name || '(ไม่ระบุสาขา)')}</b>${r.rating != null ? `<span>${'★'.repeat(Math.round(r.rating))}<span style="opacity:.35">${'★'.repeat(5 - Math.round(r.rating))}</span></span>` : ''}<span>${_FB_KIND_LABEL[r.kind] || r.kind}</span><span class="fb-age${r.sla_breached ? ' late' : ''}">${r.age_days == null ? '' : r.age_days === 0 ? 'วันนี้' : r.age_days + ' วัน'}${r.sla_breached ? ' · เกิน SLA' : ''}</span>${r.severity === 'high' ? `<span class="fb-tag flag" style="background:#7f1d1d;color:#fff" title="ตรวจพบเรื่องร้ายแรง">${_fbEsc(r.severity_note || 'ร้ายแรง')}</span>` : ''}${r.flag ? `<span class="fb-tag flag" title="${_fbEsc(r.flag_note || '')}">⚑ ${_fbEsc((r.flag_note || 'ปักหมุด').slice(0, 40))}</span>` : ''}${(r.staff || []).length ? `<span class="fb-tag" title="พนักงานที่ถูกพูดถึง">${_fbEsc(r.staff.join(', '))}</span>` : ''}${r.location ? `<span>${_fbEsc(r.location.slice(0, 40))}</span>` : ''}${r.contact ? `<span class="fb-tag flag" title="ลูกค้าฝากช่องทางติดต่อกลับ">${_fbContactHtml(r.contact)}</span>` : ''}${r.contacted_at ? `<span class="fb-tag" style="background:#dcfce7;color:#166534" title="${_fbEsc(r.contacted_by ? 'โทรกลับโดย ' + r.contacted_by : '')}">โทรกลับแล้ว</span>` : ''}</div>
        <div class="txt">${r.text ? _fbEsc(r.text) : '<i style="color:var(--text-muted)">(ให้ดาวอย่างเดียว)</i>'}</div>
        <div class="mt-1">${(r.themes || []).map(t => `<span class="fb-tag neg">${_fbEsc(t)}</span> `).join('')}${(r.menus || []).map(m => `<span class="fb-tag menu">${_fbEsc(m)}</span> `).join('')}${r.reply_text ? `<div class="text-[11px] mt-0.5" style="color:var(--good-ink)">↳ ${_fbEsc(r.reply_text.slice(0, 120))}</div>` : ''}</div>
      </div>
      <div class="acts">
        <span>${_fbReplyChip(r, true)}</span>
        ${r.needs_reply ? `<button class="fb-ghost" onclick="markFeedbackHandled(${r.id})" title="สาขาตอบไปแล้วในแอป/โทรแล้ว">✓ ตอบแล้ว</button>` : ''}
        <button class="fb-ghost${r.flag ? ' on' : ''}" onclick="flagFeedback(${r.id}, ${r.flag ? 'false' : 'true'})">${r.flag ? 'ยกเลิกหมุด' : 'ปักหมุด'}</button>
        <button class="fb-ghost" onclick="openActionForm({from_id:${r.id}, branch:${JSON.stringify(r.branch_name || '').replace(/"/g, '&quot;')}, theme:${JSON.stringify((r.themes || [])[0] || '').replace(/"/g, '&quot;')}})" title="สร้างงานแก้จากเสียงนี้">งานแก้</button>
        ${r.contact ? `<button class="fb-ghost${r.contacted_at ? ' on' : ''}" onclick="toggleContacted(${r.id}, ${r.contacted_at ? 'false' : 'true'})" title="บันทึกว่าโทรกลับลูกค้าแล้วหรือยัง">${r.contacted_at ? 'ยกเลิกโทรกลับแล้ว' : 'โทรกลับแล้ว'}</button>` : ''}
        ${r.url ? `<a class="fb-ghost" href="${_fbEsc(r.url)}" target="_blank" rel="noopener">เปิดต้นทาง ↗</a>` : ''}
      </div></div>`).join('') + (items.length >= 80 ? '<p class="text-[11px] mt-2" style="color:var(--text-muted)">แสดง 80 รายการแรก — ตอบให้หมดแล้วรีเฟรช</p>' : '');
}
async function markFeedbackHandled(id) {
  const note = prompt('ตอบลูกค้าอย่างไร (สรุปสั้น ๆ บันทึกไว้เป็นคำตอบ) — เว้นว่างได้');
  if (note === null) return;
  try { await _fbPost(`/api/feedback/${id}/handled`, { note }); loadFeedback(true); }
  catch (e) { notifyError('บันทึกไม่สำเร็จ: ' + e.message); }
}
async function flagFeedback(id, on) {
  let note = '';
  if (on) { note = prompt('ปักหมุดเรื่องร้ายแรง — ระบุเหตุผล (เช่น อาหารเป็นพิษ ทุจริต คุกคาม)') || ''; if (!note.trim()) return; }
  try { await _fbPost(`/api/feedback/${id}/flag`, { flag: on, note }); loadFeedback(true); }
  catch (e) { notifyError('บันทึกไม่สำเร็จ: ' + e.message); }
}
async function toggleContacted(id, on) {
  const by = on ? (prompt('ใครโทรกลับ (ชื่อ/ตำแหน่ง) — เว้นว่างได้') || '') : '';
  try { await _fbPost(`/api/feedback/${id}/contacted`, { contacted: on, by }); loadFeedback(true); }
  catch (e) { notifyError('บันทึกไม่สำเร็จ: ' + e.message); }
}

/* ── the full list ── */
function _fbItemChips() {
  const opts = [['', 'ทั้งหมด'], ['neg', 'ลบ'], ['pos', 'บวก'], ['flag', '⚑']];
  document.getElementById('fbItemFilters').innerHTML = opts.map(([k, l]) => { const on = k === '' ? !_fbItemFilter.sentiment && !_fbItemFilter.flagged && !_fbItemFilter.theme && !_fbItemFilter.menu : (k === 'flag' ? !!_fbItemFilter.flagged : _fbItemFilter.sentiment === k);
    return `<button type="button" class="fb-chip${on ? ' on' : ''}" onclick="loadFeedbackItems(null, ${k === 'flag' ? '{flagged:1}' : k ? `{sentiment:'${k}'}` : '{}'}, true)">${l}</button>`; }).join('') +
    (_fbItemFilter.theme ? `<span class="fb-chip on">หัวข้อ: ${_fbEsc(_fbItemFilter.theme)}</span>` : '') + (_fbItemFilter.menu ? `<span class="fb-chip on">เมนู: ${_fbEsc(_fbItemFilter.menu)}</span>` : '');
}
async function loadFeedbackItems(qp, extra, replace) {
  _fbItemFilter = replace ? Object.assign({}, extra || {}) : Object.assign({}, extra || {});
  const p = qp ? new URLSearchParams(qp) : _fbQp();
  Object.entries(_fbItemFilter).forEach(([k, v]) => p.set(k, v));
  p.set('limit', '150');
  _fbItemChips();
  let d;
  try { d = await api(`${API}/api/feedback/items?${p}`); } catch (e) { return; }
  const el = document.getElementById('fbItems');
  document.getElementById('fbItemsHint').textContent = `(${(d.items || []).length}${(d.items || []).length >= 150 ? '+' : ''} รายการ)`;
  el.innerHTML = (d.items || []).length ?
    `<table class="fb-t"><thead><tr><th>วันที่</th><th>สาขา</th><th>ช่องทาง</th><th>★</th><th>ความรู้สึก</th><th>ข้อความ</th><th>ตอบกลับ</th><th></th></tr></thead><tbody>` +
    d.items.map(r => `<tr class="align-top"><td class="whitespace-nowrap">${_fbEsc(r.rated_at || '')}${r.flag ? ' ⚑' : ''}</td><td>${_fbEsc(r.branch_name || '')}</td><td class="whitespace-nowrap">${_fbEsc(r.source || '')}<span class="text-[10px]" style="color:var(--text-muted)"> ${_FB_KIND_LABEL[r.kind] || ''}</span>${r.url ? ` <a href="${_fbEsc(r.url)}" target="_blank" rel="noopener" title="เปิดต้นทาง" style="color:var(--sky-ink)">↗</a>` : ''}</td><td>${r.rating != null ? r.rating : ''}</td><td>${_fbSentChip(r.sentiment)}</td><td>${r.text ? _fbEsc(r.text) : '<i style="color:var(--text-muted)">(ให้ดาวอย่างเดียว)</i>'}<div>${(r.themes || []).map(t => `<span class="fb-tag">${_fbEsc(t)}</span> `).join('')}${(r.menus || []).map(m => `<span class="fb-tag menu">${_fbEsc(m)}</span> `).join('')}</div></td>
      <td>${r.reply_text ? `<span title="${_fbEsc(r.replied_at || '')}" style="color:var(--good-ink)">✓ ${_fbEsc(r.reply_text.slice(0, 80))}</span>` : `${r.needs_reply ? '<span style="color:var(--bad-ink)">รอสาขาตอบ</span> ' : ''}${_fbReplyChip(r)}`}</td>
      <td><button class="text-[11px]" style="color:var(--bad-ink)" title="ลบรายการนี้" onclick="deleteFeedback(${r.id})">✕</button></td></tr>`).join('') + '</tbody></table>' :
    '<p class="text-xs" style="color:var(--text-muted)">ไม่มีรายการ</p>';
}
async function exportFeedbackCsv() {
  const p = _fbQp(); p.set('limit', '2000');
  let d; try { d = await api(`${API}/api/feedback/items?${p}`); } catch (e) { notifyError('ส่งออกไม่สำเร็จ'); return; }
  const cols = ['rated_at', 'branch_name', 'source', 'kind', 'rating', 'sentiment', 'text', 'themes', 'menus', 'reply_text', 'replied_at', 'reply_status', 'flag', 'flag_note', 'url'];
  const q = v => `"${String(v === null || v === undefined ? '' : Array.isArray(v) ? v.join('; ') : v).replace(/"/g, '""')}"`;
  const csv = '\ufeff' + cols.join(',') + '\n' + (d.items || []).map(r => cols.map(c => q(r[c])).join(',')).join('\n');
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })); a.download = `feedback_${getMonthFrom() || 'all'}_${getMonthTo() || 'all'}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
function toggleFeedbackImport() { const el = document.getElementById('fbImport'); el.open = true; el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
function fbCopy(btn, text) { const done = () => { const o = btn.textContent; btn.textContent = 'คัดลอกแล้ว ✓'; setTimeout(() => { btn.textContent = o; }, 1500); }; if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, () => alert(text)); else alert(text); }

/* ── sources card ── */
async function loadFeedbackSources() {
  const el = document.getElementById('fbSources');
  if (!el) return;
  let d;
  try { d = await api(`${API}/api/feedback/sources`); } catch (e) { el.textContent = 'โหลดแหล่งข้อมูลไม่สำเร็จ'; return; }
  const rows = d.sources || [];
  if (!rows.length) { el.innerHTML = 'ยังไม่มีรีวิวจากแหล่งใด — ตั้งค่าการดึงอัตโนมัติตาม <a href="#" onclick="switchTab(\'checklist\');return false;" style="color:var(--sky-ink);text-decoration:underline">Checklist เปิดใช้</a> หรืออัปโหลด/วางรีวิวด้านบน'; return; }
  el.innerHTML = `<table class="fb-t"><thead><tr><th>แหล่ง</th><th style="text-align:right">รีวิว</th><th style="text-align:right">ดึงอัตโนมัติ</th><th>รีวิวล่าสุด</th><th style="text-align:right">รอสาขาตอบ</th><th>ดึงล่าสุด</th><th>ผล</th></tr></thead><tbody>` +
    rows.map(s => { const lr = s.last_run, ok = lr && lr.status === 'ok';
      const result = lr ? (ok ? `✓ ใหม่ ${lr.inserted} · อัปเดต ${lr.updated}${lr.message ? ' · ' + _fbEsc(lr.message) : ''}` : '✗ ' + _fbEsc(lr.message || 'ล้มเหลว')) : '';
      return `<tr><td>${_fbEsc(s.source)}</td><td style="text-align:right">${(s.n || 0).toLocaleString()}</td><td style="text-align:right">${(s.fetched || 0).toLocaleString()}</td><td>${_fbEsc(s.latest || '—')}</td><td style="text-align:right;color:${s.unreplied ? 'var(--bad-ink)' : 'inherit'}">${s.unreplied || 0}</td><td class="whitespace-nowrap">${lr ? _fbEsc(lr.ran_at || '') : '<span style="color:var(--text-muted)">อัปโหลดเอง</span>'}</td><td style="color:${lr ? (ok ? 'var(--good-ink)' : 'var(--bad-ink)') : 'inherit'}">${result}</td></tr>`; }).join('') + '</tbody></table>';
  const q = d.reply_queue || {};
  if ((q.pending || 0) + (q.failed || 0) + (q.manual || 0) + (q.sent || 0) > 0) {
    el.innerHTML += `<p class="text-[11px] mt-2" style="color:var(--text-muted)">คิวตอบรีวิว: <b style="color:var(--amber-ink)">รอส่ง ${q.pending || 0}</b> (ส่งในรอบดึง 09:00) · ส่งแล้ว ${q.sent || 0}${q.failed ? ` · <b style="color:var(--bad-ink)">ส่งไม่สำเร็จ ${q.failed}</b>` : ''}${q.manual ? ` · ตอบเองในแอป ${q.manual}` : ''}</p>`;
  }
}

/* ── upload / delete ── */
async function uploadFeedback() {
  const st = document.getElementById('fbUploadStatus');
  const fd = new FormData();
  const txt = document.getElementById('fbText').value.trim();
  const file = document.getElementById('fbFile').files[0];
  if (!txt && !file) { st.textContent = 'วางรีวิวหรือเลือกไฟล์ก่อน'; return; }
  if (txt) fd.append('text', txt);
  if (file) fd.append('file', file);
  fd.append('branch', document.getElementById('fbBranch').value || '');
  fd.append('source', document.getElementById('fbSource').value || '');
  fd.append('date', document.getElementById('fbDate').value || '');
  st.textContent = 'กำลังวิเคราะห์…';
  try {
    const r = await api(`${API}/api/feedback/upload`, { method: 'POST', body: fd });
    if (r.detail) throw new Error(r.detail);
    st.textContent = `นำเข้า ${r.inserted} รีวิว (อัปเดต ${r.updated || 0} · ซ้ำ ${r.duplicates}) · วิเคราะห์โดย ${r.analysed_by}` + ((r.unmatched || []).length ? ` · ไม่พบสาขาสำหรับ: ${r.unmatched.join(', ')}` : '');
    document.getElementById('fbText').value = '';
    document.getElementById('fbFile').value = '';
    loadFeedback(true);
  } catch (e) { st.textContent = 'ไม่สำเร็จ: ' + e.message; }
}
async function deleteFeedback(id) {
  if (!confirm('ลบรายการนี้?')) return;
  try {
    const r = await api(`${API}/api/feedback/${id}`, { method: 'DELETE' });
    if (r.detail) throw new Error(r.detail);
    loadFeedback(true);
  } catch (e) { alert('ไม่สำเร็จ: ' + e.message); }
}

/* ── replies: draft in the panel, queue it, the nightly fetcher posts it ── */
async function openFeedbackReply(id) {
  const p = document.getElementById('fbReplyPanel');
  if (!p) return;
  p.style.display = '';
  p.innerHTML = '<p class="text-xs" style="color:var(--text-muted)">กำลังร่างคำตอบ…</p>';
  p.scrollIntoView({ behavior: 'smooth', block: 'center' });
  let d;
  try { d = await api(`${API}/api/feedback/${id}/reply-draft`); if (d.detail) throw new Error(d.detail); }
  catch (e) { p.innerHTML = `<p class="text-xs" style="color:var(--bad-ink)">ร่างคำตอบไม่สำเร็จ: ${_fbEsc(e.message)}</p>`; return; }
  const manual = d.channel === 'manual';
  p.innerHTML = `<div class="flex items-start justify-between gap-2">
      <h3 class="text-sm font-semibold">ตอบรีวิว · ${_fbEsc(d.branch || '-')} · ${_fbEsc(d.source || '')}${d.rating != null ? ' · ' + d.rating + '★' : ''}${d.date ? ' · ' + _fbEsc(d.date) : ''}${d.url ? ` · <a href="${_fbEsc(d.url)}" target="_blank" rel="noopener" style="color:var(--sky-ink);font-weight:400">เปิดรีวิว ↗</a>` : ''}</h3>
      <a href="#" onclick="closeFeedbackReply();return false;" style="color:var(--text-muted)">✕</a></div>
    <p class="text-xs my-1 px-2 py-1 rounded" style="background:var(--bg-input);color:var(--text-secondary)">${d.text ? _fbEsc(d.text) : '<i>(ให้ดาวอย่างเดียว)</i>'}</p>
    ${d.reply_status === 'failed' ? `<p class="text-xs" style="color:var(--bad-ink)">ครั้งก่อนส่งไม่สำเร็จ: ${_fbEsc(d.reply_error || '')}</p>` : ''}
    <textarea id="fbReplyText" rows="4" class="w-full mt-1 rounded border px-2 py-1 text-sm" style="border-color:var(--border);background:var(--bg-input);color:var(--text-primary)" oninput="_fbReplyCount(${d.max_len || 4000})">${_fbEsc(d.draft || '')}</textarea>
    <div class="flex items-center gap-3 mt-2 flex-wrap">
      ${d.channel === 'none' ? '' : `<button class="fb-btn" onclick="sendFeedbackReply(${id})">${manual ? 'บันทึกข้อความ' : 'ส่งในรอบถัดไป'}</button>`}
      ${manual ? `<button class="fb-ghost" onclick="fbCopy(this, document.getElementById('fbReplyText').value)">คัดลอกข้อความ</button>` : ''}
      <span id="fbReplyCount" class="text-[11px]" style="color:var(--text-muted)"></span>
      <span class="text-[11px]" style="color:var(--text-muted)">ร่างโดย ${d.engine === 'claude' ? 'Claude' : (d.engine === 'saved' ? 'ข้อความที่บันทึกไว้' : 'แม่แบบ')} · แก้ไขได้ก่อนส่ง · ${_fbEsc(d.note || '')}</span>
    </div>`;
  _fbReplyCount(d.max_len || 4000);
}
function _fbReplyCount(max) {
  const ta = document.getElementById('fbReplyText'), c = document.getElementById('fbReplyCount');
  if (!ta || !c) return;
  c.textContent = `${ta.value.length}/${max} ตัวอักษร`;
  c.style.color = ta.value.length > max ? 'var(--bad-ink)' : 'var(--text-muted)';
}
function closeFeedbackReply() { const p = document.getElementById('fbReplyPanel'); if (p) { p.style.display = 'none'; p.innerHTML = ''; } }
async function sendFeedbackReply(id) {
  const st = document.getElementById('fbReplyCount');
  const text = (document.getElementById('fbReplyText') || {}).value || '';
  if (!text.trim()) { if (st) st.textContent = 'ใส่ข้อความก่อน'; return; }
  try {
    const r = await api(`${API}/api/feedback/${id}/reply`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: text.trim() }) });
    if (r.detail) throw new Error(typeof r.detail === 'string' ? r.detail : r.detail.message);
    closeFeedbackReply();
    loadFeedback(true);
  } catch (e) { if (st) st.textContent = 'ไม่สำเร็จ: ' + e.message; }
}
async function cancelFeedbackReply(id) {
  try {
    const r = await api(`${API}/api/feedback/${id}/reply`, { method: 'DELETE' });
    if (r.detail) throw new Error(r.detail);
    loadFeedback(true);
  } catch (e) { alert('ไม่สำเร็จ: ' + e.message); }
}

/* ── the chain's own channel: a QR survey per branch (routers/survey.py) ── */
async function loadSurveyLinks() {
  const el = document.getElementById('fbSurveyLinks');
  if (!el) return;
  let d;
  try { d = await api(`${API}/api/feedback/survey-links`); } catch (e) { el.textContent = 'โหลดไม่สำเร็จ'; return; }
  const links = d.links || [], made = links.filter(l => l.code).length;
  document.getElementById('fbSurveyDays').textContent = d.contact_days;
  document.getElementById('fbSurveyHint').textContent = made ? `· ${made}/${links.length} สาขามีลิงก์แล้ว` : '· ยังไม่มีสาขาใดสร้างลิงก์';
  const sm = 'padding:0 .4rem;font-size:10px';
  el.innerHTML = (made < links.length ? `<div class="mb-2"><button class="fb-btn" onclick="createSurveyLinksAll()">สร้างลิงก์ให้ทุกสาขาที่ยังไม่มี (${links.length - made})</button></div>` : '') + `<table class="fb-t"><thead><tr><th>สาขา</th><th>ลิงก์ (รหัส 8 ตัว)</th><th style="text-align:right">สแกน</th><th style="text-align:right">ตอบ</th><th style="text-align:right">30 วันล่าสุด</th><th>ลิงก์รีวิว Google</th><th></th></tr></thead><tbody>` +
    links.map(l => `<tr${l.code && !l.active ? ' style="opacity:.55"' : ''}><td>${_fbEsc(l.branch)}</td>
      <td>${l.code ? `<code>${_fbEsc(l.code)}</code> <button class="fb-ghost" style="${sm}" onclick="fbCopy(this, ${JSON.stringify(l.url).replace(/"/g, '&quot;')})">คัดลอกลิงก์</button> <a class="fb-ghost" style="${sm}" href="${_fbEsc(l.print_url)}" target="_blank" rel="noopener">โปสเตอร์ ↗</a>${l.active ? '' : ' <span class="fb-tag">ปิดใช้</span>'}` : `<button class="fb-ghost" style="${sm}" onclick="createSurveyLink(${l.branch_id})">สร้างลิงก์</button>`}</td>
      <td style="text-align:right">${l.scans || 0}</td><td style="text-align:right">${l.responses || 0}</td>
      <td style="text-align:right">${l.n30 ? `${l.n30} · ${l.avg30 != null ? l.avg30.toFixed(2) + '★' : ''}${l.neg30 ? ` · <span style="color:var(--bad-ink)">ลบ ${l.neg30}</span>` : ''}` : '—'}</td>
      <td>${l.code ? `<input id="fbGurl-${l.branch_id}" value="${_fbEsc(l.google_review_url)}" placeholder="https://g.page/r/…/review" class="rounded border px-1 text-[11px]" style="width:210px;border-color:var(--border);background:var(--bg-input);color:var(--text-primary)"> <button class="fb-ghost" style="${sm}" onclick="saveSurveyUrl(${l.branch_id})">บันทึก</button>` : ''}</td>
      <td>${l.code ? `<button class="fb-ghost" style="${sm}" onclick="toggleSurveyLink(${l.branch_id}, ${l.active ? 'false' : 'true'})">${l.active ? 'ปิดใช้' : 'เปิดใช้'}</button>` : ''}</td></tr>`).join('') + '</tbody></table>';
}
async function _fbSurveySave(body) {
  try { await _fbPost('/api/feedback/survey-links', body); loadSurveyLinks(); }
  catch (e) { notifyError('บันทึกไม่สำเร็จ: ' + e.message); }
}
function createSurveyLink(bid) { _fbSurveySave({ branch_id: bid }); }
function createSurveyLinksAll() { _fbSurveySave({ all: true }); }
function saveSurveyUrl(bid) { _fbSurveySave({ branch_id: bid, google_review_url: (document.getElementById('fbGurl-' + bid) || {}).value || '' }); }
function toggleSurveyLink(bid, on) {
  if (!on && !confirm('ปิดลิงก์นี้? QR ที่พิมพ์ไว้จะใช้ไม่ได้จนกว่าจะเปิดใหม่')) return;
  _fbSurveySave({ branch_id: bid, active: on });
}

/* ── the fix loop: a theme on a branch becomes an action; the guests verify it (routers/feedback_actions.py) ── */
let _fbActionFilter = 'active';
const _FB_ACT_STATUS = { open: ['เปิด', '#0369a1'], doing: ['กำลังทำ', '#b45309'], done: ['เสร็จ', 'var(--good-ink)'], dropped: ['ยกเลิก', '#6b7280'] };
const _FB_ACT_RESULT = { better: ['✓ ดีขึ้น', 'var(--good-ink)'], same: ['= เท่าเดิม', '#6b7280'], worse: ['✗ แย่ลง', 'var(--bad-ink)'], no_data: ['ไม่มีข้อมูล', '#9ca3af'] };
function setFeedbackActionFilter(f) { _fbActionFilter = f; loadFeedbackActions(); }
async function loadFeedbackActions() {
  const el = document.getElementById('fbActions');
  if (!el) return;
  const p = new URLSearchParams();
  const b = _fbBranchLocal || getBranch(); if (b) p.set('branch', b);
  let d; try { d = await api(`${API}/api/feedback/actions?${p}`); } catch (e) { el.innerHTML = '<p class="text-xs" style="color:var(--bad-ink)">โหลดงานแก้ไม่สำเร็จ</p>'; return; }
  const c = d.counts || {};
  document.getElementById('fbActionsHint').textContent = `· ค้าง ${(c.open || 0) + (c.doing || 0)}${c.overdue ? ` (เกินกำหนด ${c.overdue})` : ''} · เสร็จ ${c.done || 0}${(c.better || c.same || c.worse) ? ` · พิสูจน์แล้ว ดีขึ้น ${c.better || 0} / เท่าเดิม ${c.same || 0} / แย่ลง ${c.worse || 0}` : ''}`;
  document.getElementById('fbActionFilters').innerHTML = [['active', 'ค้างอยู่'], ['done', 'เสร็จ/พิสูจน์'], ['all', 'ทั้งหมด']].map(([k, l]) => `<button type="button" class="fb-chip${_fbActionFilter === k ? ' on' : ''}" onclick="setFeedbackActionFilter('${k}')">${l}</button>`).join('');
  let items = d.actions || [];
  if (_fbActionFilter === 'active') items = items.filter(a => a.status === 'open' || a.status === 'doing');
  if (_fbActionFilter === 'done') items = items.filter(a => a.status === 'done');
  if (!items.length) { el.innerHTML = `<p class="text-xs py-2" style="color:var(--text-muted)">${_fbActionFilter === 'active' ? 'ไม่มีงานค้าง — สร้างจากหัวข้อที่ลูกค้าพูดถึง หรือจากรายการในกล่องงาน' : 'ยังไม่มีรายการ'}</p>`; return; }
  const today = d.today;
  el.innerHTML = `<table class="fb-t"><thead><tr><th>งาน</th><th>สาขา · หัวข้อ</th><th>ผู้รับผิดชอบ</th><th>กำหนด</th><th>สถานะ</th><th style="text-align:right">ก่อน → หลัง (ลบ/100 เสียง)</th><th>ผล</th><th></th></tr></thead><tbody>` +
    items.map(a => { const st = _FB_ACT_STATUS[a.status] || [a.status, '#6b7280'];
      const seg = ['open', 'doing', 'done'].map(s => `<button type="button" class="fb-chip${a.status === s ? ' on' : ''}" style="padding:0 .4rem;font-size:10px" onclick="setActionStatus(${a.id}, '${s}')">${_FB_ACT_STATUS[s][0]}</button>`).join('');
      const res = a.result ? `<span class="fb-tag" style="color:${textInk(_FB_ACT_RESULT[a.result][1])};background:transparent;border:1px solid ${_FB_ACT_RESULT[a.result][1]}">${_FB_ACT_RESULT[a.result][0]}</span>` : (a.status === 'done' ? `<span class="text-[11px]" style="color:var(--amber-ink)">รอครบ 28 วัน${a.after_days_left ? ` (อีก ${a.after_days_left})` : ''}</span>` : '');
      const before = a.baseline_rate != null ? `${a.baseline_rate}` : '—', after = a.after_rate != null ? `${a.after_rate}` : (a.status === 'done' ? '…' : '');
      return `<tr class="align-top${a.status === 'dropped' ? '" style="opacity:.55' : ''}"><td><b>${_fbEsc(a.title)}</b>${a.detail ? `<div class="text-[11px]" style="color:var(--text-muted)">${_fbEsc(a.detail.slice(0, 160))}</div>` : ''}${a.note ? `<div class="text-[11px]" style="color:var(--good-ink)">↳ ${_fbEsc(a.note.slice(0, 160))}</div>` : ''}</td>
        <td>${_fbEsc(a.branch_name || 'ทุกสาขา')}${a.theme ? `<div><span class="fb-tag">${_fbEsc(a.theme)}</span></div>` : ''}</td>
        <td>${_fbEsc(a.owner || '—')}</td><td class="whitespace-nowrap${a.overdue ? '" style="color:var(--bad-ink);font-weight:600' : ''}">${_fbEsc(a.due || '—')}${a.overdue ? ' ⚠' : ''}</td>
        <td>${a.status === 'dropped' ? `<span style="color:${textInk(st[1])}">${st[0]}</span>` : `<div class="flex gap-1">${seg}</div>`}</td>
        <td style="text-align:right;white-space:nowrap" title="ฐาน ${_fbEsc(a.baseline_from || '')} → ${_fbEsc(a.baseline_to || '')}: ${a.baseline_neg || 0} ลบ / ${a.baseline_total || 0} เสียง${a.after ? ` · หลัง: ${a.after.neg} ลบ / ${a.after.total} เสียง` : ''}">${before} → ${after}</td>
        <td>${res}</td>
        <td class="whitespace-nowrap"><button class="fb-ghost" style="padding:0 .4rem;font-size:10px" onclick="editFeedbackAction(${a.id})">แก้ไข</button> ${a.status !== 'dropped' ? `<button class="fb-ghost" style="padding:0 .4rem;font-size:10px" onclick="setActionStatus(${a.id}, 'dropped')">ยกเลิก</button>` : ''}</td></tr>`; }).join('') + '</tbody></table>' +
    `<p class="text-[11px] mt-1" style="color:var(--text-muted)">วันนี้ ${_fbEsc(today || '')} · หน้าต่างวัด ${d.window_days} วัน · ตัดสินจากเสียงลบต่อ 100 เสียงในสาขา (ลดลง ≥20% = ดีขึ้น, เพิ่มขึ้น ≥20% = แย่ลง)</p>`;
}
function openActionForm(o) {
  const f = document.getElementById('fbActionForm');
  if (!f) return;
  const themes = ((_fbSummary || {}).themes || []).map(t => t.theme);
  const branches = ((_fbSummary || {}).branches || []).map(b => b.branch).filter(b => b && b !== '(ไม่ระบุสาขา)');
  const opt = (list, cur) => `<option value="">— ไม่ระบุ —</option>` + list.map(x => `<option value="${_fbEsc(x)}"${x === cur ? ' selected' : ''}>${_fbEsc(x)}</option>`).join('') + (cur && !list.includes(cur) ? `<option value="${_fbEsc(cur)}" selected>${_fbEsc(cur)}</option>` : '');
  if (o.branch === '(ไม่ระบุสาขา)') o.branch = '';
  const title = o.title || (o.theme ? `แก้เรื่อง "${o.theme}"${o.branch ? ' ที่ ' + o.branch : ''}` : '');
  const inp = 'class="rounded border px-2 py-1 text-sm" style="border-color:var(--border);background:var(--bg-input);color:var(--text-primary)"';
  f.style.display = '';
  f.innerHTML = `<div class="flex items-baseline justify-between"><b class="text-sm">งานแก้ใหม่</b><a href="#" onclick="closeActionForm();return false;" style="color:var(--text-muted)">✕</a></div>
    <div class="grid grid-cols-1 md:grid-cols-2 gap-2 mt-2 text-xs">
      <label>ชื่องาน <input id="faTitle" ${inp} class="w-full rounded border px-2 py-1 text-sm" value="${_fbEsc(title)}" placeholder="เช่น เพิ่มคนเติมไลน์ซาชิมิช่วง 18–20 น."></label>
      <label>รายละเอียด <input id="faDetail" ${inp} class="w-full rounded border px-2 py-1 text-sm" placeholder="ทำอะไร อย่างไร"></label>
      <label>สาขา <select id="faBranch" ${inp} class="w-full rounded border px-2 py-1 text-sm">${opt(branches, o.branch || _fbBranchLocal || '')}</select></label>
      <label>หัวข้อที่วัดผล <select id="faTheme" ${inp} class="w-full rounded border px-2 py-1 text-sm">${opt(themes, o.theme || '')}</select></label>
      <label>ผู้รับผิดชอบ <input id="faOwner" ${inp} class="w-full rounded border px-2 py-1 text-sm" placeholder="ชื่อผู้จัดการ"></label>
      <label>กำหนดเสร็จ <input id="faDue" type="date" ${inp} class="w-full rounded border px-2 py-1 text-sm"></label>
    </div>
    <div class="flex items-center gap-2 mt-2"><button class="fb-btn" onclick="saveFeedbackAction(${o.from_id || 'null'})">บันทึกงาน</button><span class="text-[11px]" style="color:var(--text-muted)">ระบบเก็บฐาน 28 วันก่อนหน้าของหัวข้อ/สาขานี้ให้ทันที</span></div>`;
  f.scrollIntoView({ behavior: 'smooth', block: 'center' });
  document.getElementById('faTitle').focus();
}
function closeActionForm() { const f = document.getElementById('fbActionForm'); if (f) { f.style.display = 'none'; f.innerHTML = ''; } }
async function saveFeedbackAction(fromId) {
  const g = id => (document.getElementById(id) || {}).value || '';
  const body = { title: g('faTitle'), detail: g('faDetail'), branch: g('faBranch'), theme: g('faTheme'), owner: g('faOwner'), due: g('faDue'), from_id: fromId || undefined };
  if (!body.title.trim()) { notifyError('ใส่ชื่องานก่อน'); return; }
  try { await _fbPost('/api/feedback/actions', body); closeActionForm(); _fbActionFilter = 'active'; loadFeedbackActions(); notifySuccess('บันทึกงานแล้ว'); }
  catch (e) { notifyError('บันทึกไม่สำเร็จ: ' + e.message); }
}
async function setActionStatus(id, status) {
  if (status === 'dropped' && !confirm('ยกเลิกงานนี้?')) return;
  let note;
  if (status === 'done') { note = prompt('เสร็จแล้ว — สรุปสั้น ๆ ว่าทำอะไรไป (เว้นว่างได้)'); if (note === null) return; }
  try { await _fbPost(`/api/feedback/actions/${id}`, note !== undefined ? { status, note } : { status }); loadFeedbackActions(); }
  catch (e) { notifyError('บันทึกไม่สำเร็จ: ' + e.message); }
}
async function editFeedbackAction(id) {
  const owner = prompt('ผู้รับผิดชอบ (เว้นว่าง = ไม่เปลี่ยน)'); if (owner === null) return;
  const due = prompt('กำหนดเสร็จ YYYY-MM-DD (เว้นว่าง = ไม่เปลี่ยน)'); if (due === null) return;
  const note = prompt('บันทึกความคืบหน้า (เว้นว่าง = ไม่เปลี่ยน)'); if (note === null) return;
  const body = {}; if (owner.trim()) body.owner = owner.trim(); if (due.trim()) body.due = due.trim(); if (note.trim()) body.note = note.trim();
  if (!Object.keys(body).length) return;
  try { await _fbPost(`/api/feedback/actions/${id}`, body); loadFeedbackActions(); }
  catch (e) { notifyError('บันทึกไม่สำเร็จ: ' + e.message); }
}

/* ── AI depth (E5): the narrative and "ask the data" — Claude when the key is set, rules / search otherwise ── */
async function loadFeedbackNarrative() {
  const el = document.getElementById('fbNarrative'), eng = document.getElementById('fbAiEngine');
  if (!el) return;
  el.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังสรุป…</span>';
  const p = new URLSearchParams({ days: (document.getElementById('fbAiDays') || {}).value || '7' });
  const b = _fbBranchLocal || getBranch(); if (b) p.set('branch', b);
  let d; try { d = await api(`${API}/api/feedback/narrative?${p}`); } catch (e) { el.textContent = 'สรุปไม่สำเร็จ'; return; }
  eng.textContent = `· ${d.range[0]} → ${d.range[1]} · เขียนโดย ${d.engine === 'claude' ? 'Claude' : 'กฎ (ตั้ง ANTHROPIC_API_KEY เพื่อใช้ Claude)'}`;
  el.innerHTML = `<p>${_fbEsc(d.overview)}</p>${d.fix ? `<p class="mt-1"><b>ต้องแก้:</b> ${_fbEsc(d.fix)}</p>` : ''}${d.praise ? `<p class="mt-1"><b>ทำได้ดี:</b> ${_fbEsc(d.praise)}</p>` : ''}` +
    ((d.actions || []).length ? `<ol class="mt-1 pl-5 list-decimal">${d.actions.map(a => `<li>${_fbEsc(a)}</li>`).join('')}</ol>` : '') +
    `<div class="mt-1"><button class="fb-ghost" onclick="fbCopy(this, ${JSON.stringify([d.overview, d.fix, d.praise, ...(d.actions || []).map((a, i) => `${i + 1}. ${a}`)].filter(Boolean).join('\n')).replace(/"/g, '&quot;')})">คัดลอกส่งกลุ่ม</button></div>`;
}
async function askFeedback() {
  const q = (document.getElementById('fbAskQ') || {}).value || '', out = document.getElementById('fbAskA');
  if (!q.trim() || !out) return;
  out.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังหาคำตอบ…</span>';
  const body = { question: q.trim(), days: +((document.getElementById('fbAiDays') || {}).value || 7) * 4 };
  const b = _fbBranchLocal || getBranch(); if (b) body.branch = b;
  let d; try { d = await _fbPost('/api/feedback/ask', body); } catch (e) { out.innerHTML = `<span class="text-xs" style="color:var(--bad-ink)">ถามไม่สำเร็จ: ${_fbEsc(e.message)}</span>`; return; }
  out.innerHTML = `<div class="rounded p-3" style="background:var(--bg-input)"><p class="text-sm">${_fbEsc(d.answer)}</p><p class="text-[11px] mt-1" style="color:var(--text-muted)">${d.range[0]} → ${d.range[1]} · ${d.engine === 'claude' ? 'ตอบโดย Claude' : 'ค้นหาคำ (ตั้ง ANTHROPIC_API_KEY เพื่อให้ AI ตอบเป็นประโยค)'}${d.matches != null ? ` · ตรงกัน ${d.matches} เสียง` : ''}</p>` +
    ((d.cites || []).length ? d.cites.map(c => `<div class="text-xs mt-1 pl-2" style="border:1px solid ${c.sentiment === 'neg' ? '#fca5a5' : '#a7f3d0'}">"${_fbEsc(c.text)}" <span style="color:var(--text-muted)">— ${_fbEsc(c.branch || '')} ${_fbEsc(c.date || '')}${c.rating != null ? ' · ' + c.rating + '★' : ''}</span></div>`).join('') : '') + '</div>';
}

/* ── voices against the till (E2): busy-day share, dish sales rank / unit cost, branch revenue / discount / spend per head ── */
async function loadFeedbackBusiness() {
  const notes = document.getElementById('fbBizNotes');
  if (!notes) return;
  notes.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังอ่านข้อมูลขาย…</span>';
  const p = new URLSearchParams();
  const b = _fbBranchLocal || getBranch(); if (b) p.set('branch', b);
  // follows the global Day/Week/Month picker like the rest of the app (see qp() in 01-core.js):
  // daily/weekly (incl. a custom day range via #selDateFrom/#selDateTo) sends date_from/date_to,
  // otherwise the month range — the backend resolves whichever pair is given.
  const g = (typeof currentGranularity !== 'undefined') ? currentGranularity : 'monthly';
  if (g === 'daily' || g === 'weekly') {
    const df = document.getElementById('selDateFrom')?.value, dt = document.getElementById('selDateTo')?.value;
    if (df) p.set('date_from', df);
    if (dt) p.set('date_to', dt);
  } else {
    if (getMonthFrom()) p.set('month_from', getMonthFrom());
    if (getMonthTo()) p.set('month_to', getMonthTo());
  }
  let d; try { d = await api(`${API}/api/feedback/business?${p}`); } catch (e) { notes.textContent = 'อ่านไม่สำเร็จ'; return; }
  document.getElementById('fbBizHint').textContent = `— ${d.range[0]} → ${d.range[1]} · ${d.voices} เสียง · วันแน่น ${d.load.busy_days}/${d.load.days} วันสาขา`;
  notes.innerHTML = (d.notes || []).map(n => `<p>• ${_fbEsc(n)}</p>`).join('');
  const vcol = v => v.startsWith('เกิดตอนแน่น') ? '#b45309' : v.startsWith('เกิดตอนไม่แน่น') ? 'var(--bad-ink)' : 'var(--text-muted)';
  document.getElementById('fbBizLoad').innerHTML = `<table class="fb-t"><thead><tr><th>หัวข้อ</th><th style="text-align:right">เสียง</th><th style="text-align:right">ในวันแน่น</th><th>อ่านว่า</th></tr></thead><tbody>` +
    d.load.themes.map(t => `<tr><td>${_fbEsc(t.theme)}</td><td style="text-align:right">${t.matched}${t.n !== t.matched ? ` <span style="color:var(--text-muted)">/${t.n}</span>` : ''}</td><td style="text-align:right">${t.busy_share != null ? t.busy_share + '%' : '—'}</td><td style="color:${textInk(vcol(t.verdict))}">${_fbEsc(t.verdict)}</td></tr>`).join('') + '</tbody></table>' +
    ((d.load.branches || []).length ? `<div class="mt-1" style="color:var(--text-muted)">รอนาน รายสาขา: ${d.load.branches.slice(0, 6).map(x => `${_fbEsc(x.branch)} ${x.busy_share != null ? x.busy_share + '%' : '—'} (${x.n})`).join(' · ')}</div>` : '');
  document.getElementById('fbBizMenus').innerHTML = (d.menus || []).length ? `<table class="fb-t"><thead><tr><th>เมนู</th><th style="text-align:right">พูดถึง</th><th style="text-align:right">ลบ</th><th style="text-align:right">ขาย (ชิ้น)</th><th style="text-align:right">อันดับ</th><th style="text-align:right">ต้นทุน/ชิ้น</th><th>สัญญาณ</th></tr></thead><tbody>` +
    d.menus.slice(0, 12).map(m => `<tr><td>${_fbEsc(m.menu)}${m.kind ? ` <span style="color:var(--text-muted)">(${m.kind})</span>` : ''}</td><td style="text-align:right">${m.mentions}</td><td style="text-align:right;color:var(--bad-ink)">${m.neg}</td><td style="text-align:right">${m.qty != null ? m.qty.toLocaleString() : '—'}</td><td style="text-align:right">${m.rank ? `#${m.rank}${m.rank_pct != null ? ` <span style="color:var(--text-muted)">top ${m.rank_pct}%</span>` : ''}` : '—'}</td><td style="text-align:right">${m.cost_per_unit != null ? '฿' + m.cost_per_unit.toFixed(2) : '—'}</td><td>${(m.signals || []).map(s => `<span class="fb-tag${s.startsWith('ขายดีแต่') || s.startsWith('ต้นทุน') ? ' neg' : ''}">${_fbEsc(s)}</span>`).join(' ')}</td></tr>`).join('') + '</tbody></table>' +
    `<div class="mt-1" style="color:var(--text-muted)">ขาย ${d.dishes_sold.toLocaleString()} เมนูในช่วงนี้${d.cost_p80 != null ? ` · ต้นทุน/ชิ้น เส้น top 20% = ฿${d.cost_p80}` : ''} · (B) = บุฟเฟ่ต์ ไม่มีรายได้ต่อจาน</div>` :
    '<p style="color:var(--text-muted)">ยังไม่มีเมนูที่ถูกพูดถึงในช่วงนี้</p>';
  const pd = v => v == null ? '—' : `<span style="color:${v > 0 ? 'var(--good-ink)' : v < 0 ? 'var(--bad-ink)' : 'inherit'}">${v > 0 ? '+' : ''}${v}%</span>`;
  document.getElementById('fbBizBranches').innerHTML = (d.branches || []).length ? `<table class="fb-t"><thead><tr><th>สาขา</th><th style="text-align:right">เสียง</th><th style="text-align:right">ลบ %</th><th style="text-align:right">★</th><th style="text-align:right">ยอดขาย</th><th style="text-align:right">Δ ยอด</th><th style="text-align:right">Δ ลูกค้า</th><th style="text-align:right">ส่วนลด %</th><th style="text-align:right">ยอด/หัว</th><th>สัญญาณ</th></tr></thead><tbody>` +
    d.branches.map(x => `<tr class="row" onclick="setFeedbackBranch('${_fbEsc(x.branch).replace(/'/g, "\\'")}')"><td>${_fbEsc(x.branch)}</td><td style="text-align:right">${x.voices}</td><td style="text-align:right;color:${(x.neg_pct || 0) >= 30 ? 'var(--bad-ink)' : 'inherit'}">${x.neg_pct != null ? x.neg_pct + '%' : '—'}</td><td style="text-align:right">${x.avg_rating != null ? x.avg_rating.toFixed(2) : '—'}</td><td style="text-align:right">฿${(x.rev || 0).toLocaleString()}</td><td style="text-align:right">${pd(x.rev_delta_pct)}</td><td style="text-align:right">${pd(x.pax_delta_pct)}</td><td style="text-align:right;color:${d.chain.disc_share_median && x.disc_share >= 1.5 * d.chain.disc_share_median && x.disc_share >= 3 ? 'var(--bad-ink)' : 'inherit'}">${x.disc_share != null ? x.disc_share + '%' : '—'}</td><td style="text-align:right">${x.spend_head != null ? '฿' + x.spend_head.toLocaleString() : '—'}</td><td>${(x.signals || []).map(s => `<span class="fb-tag neg">${_fbEsc(s)}</span>`).join(' ')}</td></tr>`).join('') + '</tbody></table>' +
    `<div class="mt-1" style="color:var(--text-muted)">ทั้งเครือ: ส่วนลด ${d.chain.disc_share != null ? d.chain.disc_share + '%' : '—'} (ค่ากลางสาขา ${d.chain.disc_share_median != null ? d.chain.disc_share_median + '%' : '—'}) · ยอด/หัว ค่ากลาง ${d.chain.spend_head_median != null ? '฿' + d.chain.spend_head_median.toLocaleString() : '—'} · Δ เทียบช่วงก่อนหน้าที่ยาวเท่ากัน (${d.prev_range[0]} → ${d.prev_range[1]}) · ยอดขายรวม VAT จากบิล</div>` :
    '<p style="color:var(--text-muted)">ไม่มีข้อมูลขายในช่วงนี้</p>';
}

/* ── the competitors around each branch (E6): the monthly Google Places snapshot ── */
async function loadFeedbackCompetitors(branch) {
  const el = document.getElementById('fbCompBody'), hint = document.getElementById('fbCompHint');
  if (!el) return;
  el.innerHTML = '<span style="color:var(--text-muted)">กำลังโหลด…</span>';
  const p = new URLSearchParams();
  const b = branch || _fbBranchLocal || getBranch(); if (b) p.set('branch', b);
  let d; try { d = await api(`${API}/api/feedback/competitors?${p}`); } catch (e) { el.textContent = 'โหลดไม่สำเร็จ'; return; }
  if (!d.available) {
    hint.textContent = '— ยังไม่มีภาพถ่ายคู่แข่ง';
    el.innerHTML = `<p style="color:var(--text-secondary)">ยังไม่มีข้อมูล — ใส่ Google Places API key ใน backend\\.feedback_config.json ("places": {"api_key": …}) แล้วรัน <code>py backend\\fetch_feedback.py places</code> ครั้งแรก หลังจากนั้นระบบถ่ายภาพเองเดือนละครั้ง (คู่มือ §20)</p>`;
    return;
  }
  hint.textContent = `— ภาพถ่าย ${d.month}${d.prev_month ? ` · เทียบ ${d.prev_month}` : ''}`;
  const dl = (cur, prev, digits) => (cur == null || prev == null) ? '' : (Math.abs(cur - prev) < (digits ? 0.005 : 0.5) ? '' : `<span class="fb-delta ${cur > prev ? 'good' : 'bad'}">${cur > prev ? '▲' : '▼'}${Math.abs(cur - prev).toFixed(digits)}</span>`);
  const rows = d.branches || [];
  el.innerHTML = rows.length ? `<table class="fb-t"><thead><tr><th>สาขา</th><th>ลิสติ้งของเรา</th><th style="text-align:right">★ เรา</th><th style="text-align:right">รีวิว</th><th style="text-align:right">★ กลางคู่แข่ง</th><th style="text-align:right">ห่าง</th><th style="text-align:right">อันดับ</th><th>คู่แข่งที่ดีที่สุด</th><th></th></tr></thead><tbody>` +
    rows.map(r => { const me = r.self || {}, pv = r.prev || {};
      return `<tr class="row" onclick="loadFeedbackCompetitors('${_fbEsc(r.branch).replace(/'/g, "\\'")}')"><td><b>${_fbEsc(r.branch)}</b></td><td class="text-[11px]" style="color:var(--text-muted)">${_fbEsc(me.name || '—')}</td>
        <td style="text-align:right;font-weight:600">${me.rating != null ? me.rating.toFixed(1) : '—'} ${dl(me.rating, pv.rating, 1)}</td><td style="text-align:right">${(me.ratings_total || 0).toLocaleString()} ${dl(me.ratings_total, pv.ratings_total, 0)}</td>
        <td style="text-align:right">${r.median_rating != null ? r.median_rating.toFixed(1) : '—'} <span style="color:var(--text-muted)">(${r.rated_competitors})</span></td>
        <td style="text-align:right;color:${r.gap == null ? 'inherit' : r.gap >= 0.2 ? 'var(--good-ink)' : r.gap <= -0.2 ? 'var(--bad-ink)' : 'inherit'}">${r.gap != null ? (r.gap > 0 ? '+' : '') + r.gap.toFixed(2) : '—'}</td>
        <td style="text-align:right">${r.rank ? `#${r.rank}/${r.of}` : '—'}</td><td class="text-[11px]">${r.best ? `${_fbEsc(r.best.name)} ★${r.best.rating.toFixed(1)} (${r.best.ratings_total.toLocaleString()}) · ${(r.best.distance_m / 1000).toFixed(1)} กม.` : '—'}</td><td class="text-[11px]" style="color:var(--sky-ink)">${(r.list || []).length ? '' : 'ดูรายชื่อ →'}</td></tr>` +
        ((r.list || []).length ? `<tr><td colspan="9"><div class="pl-3" style="color:var(--text-secondary)">${r.list.map(c => `${_fbEsc(c.name)} ★${c.rating != null ? c.rating.toFixed(1) : '—'} (${(c.ratings_total || 0).toLocaleString()}) · ${(c.distance_m / 1000).toFixed(1)} กม.${c.price_level != null ? ' · ' + '฿'.repeat(Math.max(1, c.price_level)) : ''}`).join('<br>')}</div></td></tr>` : ''); }).join('') + '</tbody></table>' +
    `<div class="mt-1" style="color:var(--text-muted)">★ กลางคู่แข่ง = ค่ากลางของร้านที่มีรีวิว ≥ 20 ในรัศมี · ห่าง = ★ เรา − ★ กลางคู่แข่ง · อันดับนับเฉพาะร้านที่มีรีวิว ≥ 20 · คลิกสาขาเพื่อดูรายชื่อ</div>` :
    '<p style="color:var(--text-muted)">ไม่มีข้อมูลสำหรับสาขาที่เลือก</p>';
}

/* ── competitor search settings (radius/keywords) — chain-wide or per-branch override, api_key stays file-only ── */
async function toggleCompSettings() {
  const el = document.getElementById('fbCompSettings');
  if (!el) return;
  const show = el.style.display === 'none';
  el.style.display = show ? '' : 'none';
  if (show && !el.dataset.loaded) await loadCompSettings();
}
async function _fbCompFillBranches(sel) {
  if (sel.dataset.loaded) return;
  try {
    const bs = await api(`${API}/api/branches`);
    (bs || []).filter(b => b.active !== false).forEach(b => { const o = document.createElement('option'); o.value = b.name; o.textContent = b.name; sel.appendChild(o); });
    sel.dataset.loaded = '1';
  } catch (e) {}
}
async function loadCompSettings() {
  const el = document.getElementById('fbCompSettings');
  if (!el) return;
  el.innerHTML = '<span style="color:var(--text-muted)">กำลังโหลด…</span>';
  el.dataset.loaded = '1';
  el.innerHTML = `<div class="flex items-center gap-2 mb-2"><label class="text-[11px]" style="color:var(--text-muted)">ตั้งค่าให้</label>
    <select id="fbCompBranchSel" class="rounded border px-2 py-1 text-xs" style="border-color:var(--border);background:var(--bg-input);color:var(--text-primary)" onchange="loadCompSettingsForBranch()"><option value="">ทุกสาขา (ค่าเริ่มต้นของเครือ)</option></select></div>
    <div id="fbCompSettingsBody"></div>`;
  await _fbCompFillBranches(document.getElementById('fbCompBranchSel'));
  await loadCompSettingsForBranch();
}
async function loadCompSettingsForBranch() {
  const body = document.getElementById('fbCompSettingsBody');
  const branch = (document.getElementById('fbCompBranchSel') || {}).value || '';
  if (!body) return;
  body.innerHTML = '<span style="color:var(--text-muted)">กำลังโหลด…</span>';
  const p = new URLSearchParams(); if (branch) p.set('branch', branch);
  let d; try { d = await api(`${API}/api/feedback/competitors/settings?${p}`); } catch (e) { body.innerHTML = 'โหลดไม่สำเร็จ'; return; }
  const hint = branch
    ? (d.overridden ? `กำหนดเฉพาะสาขานี้ — ถ้าไม่ตั้ง จะใช้ค่าเริ่มต้นของเครือ: รัศมี ${d.radius_m_default} ม. · คำค้น ${_fbEsc((d.keywords_default || []).join(', '))}`
                    : `ยังไม่ได้กำหนดเฉพาะสาขานี้ — ค่าด้านล่างคือค่าเริ่มต้นของเครือที่ใช้อยู่ แก้แล้วบันทึกเพื่อตั้งเฉพาะสาขานี้`)
    : `ค่านี้ใช้กับทุกสาขาที่ไม่ได้ตั้งเฉพาะของตัวเอง${(d.branches_overridden || []).length ? ` — ตั้งเฉพาะแล้ว: ${d.branches_overridden.map(_fbEsc).join(', ')}` : ''} — มีผลกับการถ่ายภาพครั้งถัดไป (เดือนละครั้ง) ไม่กระทบภาพที่ถ่ายไปแล้ว`;
  body.innerHTML = `
    <div class="flex flex-wrap items-end gap-3">
      <label>รัศมีค้นหา (เมตร)<br><input id="fbCompRadius" type="number" min="500" max="10000" step="100" value="${d.radius_m}" class="rounded border px-2 py-1 text-sm mt-0.5" style="width:110px;border-color:var(--border);background:var(--bg-input);color:var(--text-primary)"></label>
      <label class="flex-1" style="min-width:220px">คำค้น (คั่นด้วยจุลภาค)<br><input id="fbCompKeywords" type="text" value="${_fbEsc((d.keywords || []).join(', '))}" class="rounded border px-2 py-1 text-sm mt-0.5 w-full" style="border-color:var(--border);background:var(--bg-input);color:var(--text-primary)"></label>
      <button class="fb-btn" onclick="saveCompSettings()">บันทึก</button>
      ${branch && d.overridden ? '<button class="fb-ghost" onclick="clearCompSettings()">ใช้ค่าเริ่มต้นของเครือ</button>' : ''}
      <span id="fbCompSetStatus" class="text-[11px]" style="color:var(--text-muted)"></span>
    </div>
    <p class="text-[11px] mt-1" style="color:var(--text-muted)">${hint}</p>`;
}
async function saveCompSettings() {
  const status = document.getElementById('fbCompSetStatus');
  const branch = (document.getElementById('fbCompBranchSel') || {}).value || '';
  const radius_m = parseInt((document.getElementById('fbCompRadius') || {}).value, 10);
  const keywords = ((document.getElementById('fbCompKeywords') || {}).value || '').split(',').map(s => s.trim()).filter(Boolean);
  if (status) status.textContent = 'กำลังบันทึก…';
  try {
    const p = new URLSearchParams(); if (branch) p.set('branch', branch);
    await _fbPost(`/api/feedback/competitors/settings?${p}`, { radius_m, keywords });
    await loadCompSettingsForBranch();
    if (typeof notifySuccess === 'function') notifySuccess(branch ? `บันทึกการตั้งค่าคู่แข่งของ${branch}แล้ว` : 'บันทึกการตั้งค่าคู่แข่งของเครือแล้ว');
  } catch (e) {
    if (status) status.textContent = '';
    notifyError('บันทึกไม่สำเร็จ: ' + e.message);
  }
}
async function clearCompSettings() {
  const branch = (document.getElementById('fbCompBranchSel') || {}).value || '';
  if (!branch || !confirm(`ล้างค่าเฉพาะของ ${branch} แล้วกลับไปใช้ค่าเริ่มต้นของเครือ?`)) return;
  try {
    const p = new URLSearchParams({ branch });
    await _fbPost(`/api/feedback/competitors/settings?${p}`, { clear: true });
    await loadCompSettingsForBranch();
    if (typeof notifySuccess === 'function') notifySuccess(`ล้างค่าเฉพาะของ ${branch} แล้ว`);
  } catch (e) {
    notifyError('ล้างค่าไม่สำเร็จ: ' + e.message);
  }
}

/* ── daily report (marketing): one calendar day, rendered in-app + a print edition ── */
function _fbDrStar(v) { return v != null ? ` · ${v}★` : ''; }
async function _fbDrFillBranches() {
  const sel = document.getElementById('fbDrBranch');
  if (!sel || sel.dataset.loaded) return;
  try {
    const bs = await api(`${API}/api/branches`);
    (bs || []).filter(b => b.active !== false).forEach(b => { const o = document.createElement('option'); o.value = b.name; o.textContent = b.name; sel.appendChild(o); });
    sel.dataset.loaded = '1';
  } catch (e) {}
}
/* items grouped by branch (descending count) — the ทุกสาขา view groups the negative/positive
   lists per branch instead of one blended list; a single-branch view returns [[branch, items]] */
function _fbDrGroupByBranch(items) {
  if (!items.length) return [];
  const groups = {};
  items.forEach(it => { const b = it.branch || '(ไม่ระบุสาขา)'; (groups[b] = groups[b] || []).push(it); });
  return Object.entries(groups).sort((a, b) => b[1].length - a[1].length);
}
/* d.toISOString() converts to UTC first — in UTC+7 (Thailand) that silently steps local
   midnight back a day. Format from the local getFullYear/Month/Date instead. */
function _fbIsoLocal(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }
/* วันนี้/สัปดาห์นี้/เดือนนี้ quick-fill — sets both date inputs then generates */
function setDailyReportPeriod(kind) {
  const dateEl = document.getElementById('fbDrDate'), toEl = document.getElementById('fbDrDateTo');
  const base = dateEl.value ? new Date(dateEl.value + 'T00:00:00') : new Date();
  const iso = _fbIsoLocal;
  if (kind === 'day') { dateEl.value = iso(new Date()); toEl.value = ''; }
  else if (kind === 'week') {
    const dow = (base.getDay() + 6) % 7;                       // Monday-start week
    const mon = new Date(base); mon.setDate(base.getDate() - dow);
    const sun = new Date(mon); sun.setDate(mon.getDate() + 6);
    dateEl.value = iso(mon); toEl.value = iso(sun);
  } else if (kind === 'month') {
    const first = new Date(base.getFullYear(), base.getMonth(), 1);
    const last = new Date(base.getFullYear(), base.getMonth() + 1, 0);
    dateEl.value = iso(first); toEl.value = iso(last);
  }
  loadDailyReport();
}
async function loadDailyReport() {
  const host = document.getElementById('fbDailyReport'), printLink = document.getElementById('fbDrPrint'), xlsxLink = document.getElementById('fbDrXlsx');
  const dateEl = document.getElementById('fbDrDate'), toEl = document.getElementById('fbDrDateTo');
  if (!dateEl.value) dateEl.value = _fbIsoLocal(new Date());
  const date = dateEl.value, dateTo = toEl.value && toEl.value !== date ? toEl.value : '';
  await _fbDrFillBranches();
  const branchSel = document.getElementById('fbDrBranch');
  if (branchSel && !branchSel.dataset.touched) branchSel.value = _fbBranchLocal || getBranch() || '';
  host.innerHTML = '<span class="text-xs" style="color:var(--text-muted)">กำลังสร้างรายงาน…</span>';
  const p = new URLSearchParams({ date }); if (dateTo) p.set('date_to', dateTo);
  const b = branchSel ? branchSel.value : (_fbBranchLocal || getBranch()); if (b) p.set('branch', b);
  let d; try { d = await api(`${API}/api/feedback/daily-report?${p}`); } catch (e) { host.innerHTML = 'สร้างรายงานไม่สำเร็จ: ' + _fbEsc(e.message); return; }
  printLink.href = `${API}/api/feedback/daily-report.print?${p}`;
  printLink.style.display = '';
  xlsxLink.href = `${API}/api/feedback/daily-report.xlsx?${p}`;
  xlsxLink.style.display = '';
  const oneDay = d.date_from === d.date_to;
  const pw = oneDay ? 'วันนี้' : 'ช่วงนี้';                       // matches _period_word() server-side
  if (!d.total) { host.innerHTML = `<p class="text-sm" style="color:var(--text-secondary)">${_fbEsc(d.headline)}</p>`; return; }
  const kpi = (l, v, cls) => `<div class="fb-kpi" style="border-top-color:#0f766e"><div class="l">${l}</div><div class="v"${cls ? ` style="color:${textInk(cls)}"` : ''}>${v}</div></div>`;
  const kpis = kpi('เสียงทั้งหมด', d.total) + kpi('คะแนนเฉลี่ย', d.avg_rating != null ? d.avg_rating.toFixed(2) + '★' : '—') +
    kpi('เชิงลบ', d.neg_pct + '%', d.neg_pct >= 30 ? 'var(--bad-ink)' : '') + kpi('รอตอบ', d.unreplied, d.unreplied ? '#b45309' : '') +
    kpi('เกิน SLA', d.sla_breached, d.sla_breached ? 'var(--bad-ink)' : '') + kpi('ร้ายแรง', d.severe, d.severe ? 'var(--bad-ink)' : '');
  const pp = d.prev_period, ppLabel = pp.date_from === pp.date_to ? pp.date_from : `${pp.date_from} – ${pp.date_to}`;
  const cmp = pp.total ? ` · เทียบช่วงก่อนหน้าที่ยาวเท่ากัน (${ppLabel}): ${pp.total} เสียง` : '';
  const dateBit = it => oneDay ? '' : _fbEsc((it.date || '').slice(0, 10)) + ' · ';
  const negCard = it => `<div class="fb-quote" style="border-left-color:#fca5a5;background:#fef2f2">“${_fbEsc(it.text)}”${it.severity === 'high' ? ' <span class="fb-tag flag">ร้ายแรง</span>' : ''}<div class="m">${dateBit(it)}${d.branch ? '' : _fbEsc(it.branch || '-') + ' · '}${_fbEsc(it.source || '')}${_fbDrStar(it.rating)} · ${it.replied ? 'ตอบแล้ว' : it.sla_breached ? 'เกิน SLA' : 'รอตอบ'}${it.contact ? ` · ${_fbContactHtml(it.contact)}` : ''}${it.contacted ? ' · <span style="color:#166534">โทรกลับแล้ว</span>' : ''}</div></div>`;
  const posCard = q => `<div class="fb-quote">“${_fbEsc(q.text)}”<div class="m">— ${dateBit(q)}${d.branch ? '' : _fbEsc(q.branch || '-') + ' · '}${_fbEsc(q.source || '')}${_fbDrStar(q.rating)}</div></div>`;
  const grouped = (items, card) => d.branch ? items.map(card).join('') :
    _fbDrGroupByBranch(items).map(([br, its]) => `<div class="text-xs font-semibold mt-2" style="color:var(--teal-ink)">${_fbEsc(br)} (${its.length})</div>${its.map(card).join('')}`).join('');
  const negItems = (d.recent_negative || []).length ? grouped(d.recent_negative, negCard) : `<p class="text-xs" style="color:var(--text-muted)">ไม่มีรายการเชิงลบที่ต้องติดตาม${pw}</p>`;
  const posQuotes = (d.recent_positive || []).length ? grouped(d.recent_positive, posCard) : '<p class="text-xs" style="color:var(--text-muted)">ยังไม่มีเสียงชมที่หยิบไปใช้ได้</p>';
  const themeRows = (d.themes || []).length ? `<table class="fb-t"><thead><tr><th>หัวข้อ</th><th style="text-align:right">พูดถึง</th><th style="text-align:right">ลบ</th><th>สาขา</th><th>ตัวอย่าง</th></tr></thead><tbody>${d.themes.slice(0, 12).map(t => `<tr><td>${_fbEsc(t.theme)}</td><td style="text-align:right">${t.n}</td><td style="text-align:right;color:var(--bad-ink)">${t.neg}</td><td class="text-[11px]" style="color:var(--text-muted)">${(t.branches || []).map(_fbEsc).join(', ')}</td><td class="text-[11px] italic" style="color:var(--text-muted)">${(t.examples || [])[0] ? '“' + _fbEsc(t.examples[0].text) + '”' : ''}</td></tr>`).join('')}</tbody></table>` :
    `<p class="text-xs" style="color:var(--text-muted)">ยังไม่มีหัวข้อที่ถูกพูดถึง${pw}</p>`;
  const menuRows = (d.menus || []).length ? `<table class="fb-t"><thead><tr><th>เมนู</th><th style="text-align:right">พูดถึง</th><th style="text-align:right">บวก</th><th style="text-align:right">ลบ</th></tr></thead><tbody>${d.menus.slice(0, 12).map(m => `<tr><td>${_fbEsc(m.menu)}</td><td style="text-align:right">${m.n}</td><td style="text-align:right;color:var(--good-ink)">${m.pos}</td><td style="text-align:right;color:var(--bad-ink)">${m.neg}</td></tr>`).join('')}</tbody></table>` :
    `<p class="text-xs" style="color:var(--text-muted)">ยังไม่มีเมนูที่ถูกพูดถึง${pw}</p>`;
  const sourcesRows = Object.entries(d.sources || {}).sort((a, b) => b[1] - a[1]).map(([s, n]) => `<div class="flex justify-between"><span>${_fbEsc(s)}</span><b>${n}</b></div>`).join('') || '<p class="text-xs" style="color:var(--text-muted)">—</p>';
  const r = d.response || {};
  const responseRows = `<div class="flex justify-between"><span>ตอบได้ทั้งหมด</span><b>${r.answerable || 0}</b></div><div class="flex justify-between"><span>ตอบแล้ว</span><b>${r.answered || 0}</b></div>` +
    `<div class="flex justify-between"><span>อัตราตอบกลับ</span><b>${r.rate != null ? r.rate + '%' : '—'}</b></div><div class="flex justify-between"><span>ตอบไวเฉลี่ย</span><b>${r.median_days != null ? r.median_days + ' วัน' : '—'}</b></div>`;
  const staffRows = (d.staff || []).length ? d.staff.slice(0, 8).map(s => `<div class="flex justify-between"><span>${_fbEsc(s.name)} <span class="text-[11px]" style="color:var(--text-muted)">(${_fbEsc(s.branch)})</span></span><b>${s.pos} ${s.neg}</b></div>`).join('') :
    `<p class="text-xs" style="color:var(--text-muted)">ยังไม่มีพนักงานถูกระบุชื่อ${pw}</p>`;
  const newIssues = (d.new_issues || []).length ? d.new_issues.slice(0, 12).map(it => {
    const branchArg = JSON.stringify(it.branch || '').replace(/"/g, '&quot;'), themeArg = JSON.stringify(it.theme || '').replace(/"/g, '&quot;');
    return `<div class="fb-quote" style="border-left-color:#fca5a5;background:#fef2f2;display:flex;justify-content:space-between;align-items:flex-start;gap:.5rem">
      <div>${d.branch ? '' : _fbEsc(it.branch || '-') + ' · '}<b>${_fbEsc(it.theme)}</b> (${it.n} เสียงลบ)${it.examples && it.examples[0] ? ` — “${_fbEsc(it.examples[0])}”` : ''}</div>
      <button class="fb-ghost" style="white-space:nowrap" onclick="openActionForm({branch:${branchArg}, theme:${themeArg}})" title="สร้างงานแก้สำหรับปัญหานี้">มอบหมาย</button>
    </div>`;
  }).join('') : `<p class="text-xs" style="color:var(--text-muted)">ไม่มีปัญหาใหม่ที่ยังไม่มีคนตาม${pw}</p>`;
  const openIssues = (d.open_issues || []).length ? d.open_issues.slice(0, 15).map(it => `<div class="fb-quote" style="border-left-color:#fca5a5;background:#fef2f2">${d.branch ? '' : _fbEsc(it.branch || '-') + ' · '}<b>${_fbEsc(it.title)}</b>${it.overdue ? ' <span class="fb-tag flag">เกินกำหนด</span>' : ''}<div class="m">${_fbEsc(it.theme || '-')} · ${it.status === 'doing' ? 'กำลังทำ' : 'ยังไม่เริ่ม'} · ${_fbEsc(it.owner || 'ยังไม่มอบหมาย')}${it.due ? ` · กำหนด ${_fbEsc(it.due)}` : ''}${it.days_open != null ? ` · เปิดมา ${it.days_open} วัน` : ''}</div></div>`).join('') :
    `<p class="text-xs" style="color:var(--text-muted)">ไม่มีปัญหาที่ค้างอยู่</p>`;
  const RESULT_TH = { better: 'ดีขึ้น', same: 'เท่าเดิม', worse: 'แย่ลง', no_data: 'รอผล' };
  const resolvedIssues = (d.resolved_issues || []).length ? d.resolved_issues.slice(0, 15).map(it => {
    const rate = it.after_rate != null ? ` (ก่อน ${it.baseline_rate != null ? it.baseline_rate + '/100' : '—'} → หลัง ${it.after_rate}/100)` : '';
    return `<div class="fb-quote">${d.branch ? '' : _fbEsc(it.branch || '-') + ' · '}<b>${_fbEsc(it.title)}</b><div class="m">${_fbEsc(it.theme || '-')} · ปิดเมื่อ ${_fbEsc(it.done_at)} · ใช้เวลาแก้ ${it.days_to_fix != null ? it.days_to_fix + ' วัน' : '—'} · ผล ${RESULT_TH[it.result] || (it.after_days_left != null ? 'รอผล' : '—')}${rate}</div></div>`;
  }).join('') : `<p class="text-xs" style="color:var(--text-muted)">ไม่มีปัญหาที่ปิดในช่วงนี้</p>`;
  const chronicClaimed = new Set((d.open_issues || []).map(it => `${it.branch || ''} ${it.theme || ''}`));
  const chronicTable = (d.chronic_branches || []).length ? `<table class="fb-t"><thead><tr><th>สาขา</th><th>หัวข้อที่ซ้ำ</th><th>ความถี่</th><th></th></tr></thead><tbody>${d.chronic_branches.slice(0, 15).map(it => {
    const branchArg = JSON.stringify(it.branch || '').replace(/"/g, '&quot;'), themeArg = JSON.stringify(it.theme || '').replace(/"/g, '&quot;');
    const already = chronicClaimed.has(`${it.branch || ''} ${it.theme || ''}`);
    const act = already ? '<span class="text-[11px]" style="color:var(--text-muted)">มีงานอยู่แล้ว</span>' :
      `<button class="fb-ghost" onclick="openActionForm({branch:${branchArg}, theme:${themeArg}})" title="สร้างงานแก้สำหรับปัญหาที่วนซ้ำนี้">มอบหมาย</button>`;
    return `<tr><td>${_fbEsc(it.branch || '-')}</td><td>${_fbEsc(it.theme)}</td><td>${it.periods}/${it.of_periods} ช่วง</td><td>${act}</td></tr>`;
  }).join('')}</tbody></table>` : `<p class="text-xs" style="color:var(--text-muted)">ไม่มีสาขาที่ปัญหาเดิมวนซ้ำในช่วงล่าสุด</p>`;
  const branchTable = (!d.branch && (d.branches || []).length) ? `<h4 class="text-xs font-semibold mt-3 mb-1">รายสาขาที่มีเสียง${pw} (${d.branches.length})</h4>
    <table class="fb-t"><thead><tr><th>สาขา</th><th style="text-align:right">เสียง</th><th style="text-align:right">ลบ%</th><th style="text-align:right">★</th><th style="text-align:right">รอตอบ</th><th style="text-align:right">ปักหมุด</th><th>หัวข้อเด่น</th></tr></thead><tbody>${d.branches.map(x => `<tr class="row" onclick="document.getElementById('fbDrBranch').value='${_fbEsc(x.branch).replace(/'/g, "\\'")}';document.getElementById('fbDrBranch').dataset.touched='1';loadDailyReport()"><td>${_fbEsc(x.branch)}</td><td style="text-align:right">${x.n}</td><td style="text-align:right;color:${x.neg_pct >= 30 ? 'var(--bad-ink)' : 'inherit'}">${x.neg_pct}%</td><td style="text-align:right">${x.avg_rating != null ? x.avg_rating.toFixed(2) : '—'}</td><td style="text-align:right">${x.unreplied}</td><td style="text-align:right">${x.flagged}</td><td class="text-[11px]">${_fbEsc(x.top_theme || '—')}</td></tr>`).join('')}</tbody></table>` : '';
  const showTrend = !oneDay && (d.by_day || []).length > 1;
  host.innerHTML = `
    <div class="rounded p-2.5" style="background:#f0fdfa;border:1px solid #99f6e4"><b>${_fbEsc(d.headline)}</b><span style="color:var(--text-muted)">${_fbEsc(cmp)}</span></div>
    <div class="fb-kpis mt-3">${kpis}</div>
    ${showTrend ? `<div class="mt-3"><h4 class="text-xs font-semibold mb-1">แนวโน้มความรู้สึกรายวัน</h4><div style="height:160px"><canvas id="fbDrTrendChart"></canvas></div></div>` : ''}
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-3">
      <div><h4 class="text-xs font-semibold mb-1">หัวข้อที่ถูกพูดถึง${pw}</h4>${themeRows}</div>
      <div><h4 class="text-xs font-semibold mb-1">เมนูที่ถูกพูดถึง${pw}</h4>${menuRows}</div>
    </div>
    <h4 class="text-xs font-semibold mt-3 mb-1">ต้องติดตาม${pw} (${(d.recent_negative || []).length})</h4>${negItems}
    <h4 class="text-xs font-semibold mt-3 mb-1">ปัญหาใหม่${pw} ยังไม่มีคนตาม (${(d.new_issues || []).length})</h4>${newIssues}
    <h4 class="text-xs font-semibold mt-3 mb-1">ปัญหาที่ยังไม่ได้แก้ (${(d.open_issues || []).length})</h4>${openIssues}
    <h4 class="text-xs font-semibold mt-3 mb-1">ปัญหาที่แก้แล้วช่วงนี้ (${(d.resolved_issues || []).length})</h4>${resolvedIssues}
    <h4 class="text-xs font-semibold mt-3 mb-1">สาขาที่มีความผิดซ้ำสม่ำเสมอ (${(d.chronic_branches || []).length})</h4>${chronicTable}
    <h4 class="text-xs font-semibold mt-3 mb-1">เสียงชมที่หยิบไปใช้ได้ (${(d.recent_positive || []).length})</h4>${posQuotes}
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4 mt-3 text-xs">
      <div><h4 class="text-xs font-semibold mb-1">ช่องทางที่มา${pw}</h4>${sourcesRows}</div>
      <div><h4 class="text-xs font-semibold mb-1">อัตราตอบกลับ</h4>${responseRows}</div>
      <div><h4 class="text-xs font-semibold mb-1">พนักงานที่ถูกพูดถึง</h4>${staffRows}</div>
    </div>
    ${branchTable}`;
  if (showTrend) {
    const days = d.by_day || [];
    mkChart('fbDrTrendChart', {
      type: 'bar',
      data: {
        labels: days.map(x => x.day.slice(5)),
        datasets: [
          { label: 'ลบ', data: days.map(x => x.neg), backgroundColor: '#f87171', stack: 's' },
          { label: 'อื่นๆ', data: days.map(x => Math.max(0, x.n - x.neg)), backgroundColor: '#5eead4', stack: 's' },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: true, position: 'top', labels: { boxWidth: 10, font: { size: 10 } } } },
        scales: { x: { stacked: true, ticks: { font: { size: 9 } } }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
  }
}
