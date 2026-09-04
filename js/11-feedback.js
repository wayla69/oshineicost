/* ═══════════════════════════════════════════════════════════════════
   💬 เสียงลูกค้า (Customer Feedback) — pillar 3 of the menu programme.
   Upload/paste reviews → /api/feedback/upload; the tab summarises
   sentiment, themes, menus and branches from /api/feedback/summary.
   Follows the global branch + month range (registry entry in TABS).
   ═══════════════════════════════════════════════════════════════════ */

function _fbEsc(s) { return String(s === undefined || s === null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function _fbSentChip(s) {
  const m = { pos: ['😊 บวก', '#047857', '#ecfdf5'], neg: ['😟 ลบ', '#b91c1c', '#fef2f2'], neu: ['😐 กลาง', '#6b7280', '#f3f4f6'] }[s] || ['—', '#6b7280', '#f3f4f6'];
  return `<span class="text-[11px] px-2 py-0.5 rounded-full" style="color:${m[1]};background:${m[2]}">${m[0]}</span>`;
}

async function _fbFillBranches() {
  const sel = document.getElementById('fbBranch');
  if (!sel || sel.dataset.loaded) return;
  try {
    const bs = await api(`${API}/api/branches`);
    (bs || []).filter(b => b.active !== false).forEach(b => {
      const o = document.createElement('option'); o.value = b.name; o.textContent = b.name; sel.appendChild(o);
    });
    sel.dataset.loaded = '1';
  } catch (e) {}
}

async function loadFeedback(force) {
  await _fbFillBranches();
  const fk = _filterKey();
  if (!force && _tabLastKey.feedback === fk) return;
  const st = document.getElementById('fbStatus');
  if (st) st.textContent = 'กำลังโหลด…';
  const qp = new URLSearchParams();
  if (getBranch())    qp.set('branch', getBranch());
  if (getMonthFrom()) qp.set('month_from', getMonthFrom());
  if (getMonthTo())   qp.set('month_to', getMonthTo());
  let d;
  try { d = await api(`${API}/api/feedback/summary?${qp}`); }
  catch (e) { if (st) st.textContent = 'โหลดข้อมูลไม่สำเร็จ'; return; }
  _tabLastKey.feedback = fk;
  renderFeedback(d);
  loadFeedbackItems(qp);
}

function renderFeedback(d) {
  const st = document.getElementById('fbStatus');
  const eng = Object.entries(d.analysed_by || {}).map(([k, v]) => `${k} ${v}`).join(' · ');
  if (st) st.textContent = d.total ? `${d.total.toLocaleString()} รีวิว · วิเคราะห์โดย ${eng || '—'}` : 'ยังไม่มีรีวิวในช่วงที่เลือก — วางหรืออัปโหลดด้านบน';
  const s = d.sentiment || {};
  const kpi = (l, v, sub, c) => `<div class="card p-4" style="border-top:3px solid ${c}"><p class="text-[11px] uppercase tracking-wide" style="color:var(--text-muted)">${l}</p><p class="text-2xl font-bold" style="color:${c}">${v}</p><p class="text-[11px]" style="color:var(--text-muted)">${sub || ''}</p></div>`;
  document.getElementById('fbKpis').innerHTML =
    kpi('รีวิวทั้งหมด', (d.total || 0).toLocaleString(), Object.entries(d.sources || {}).map(([k, v]) => `${k} ${v}`).join(' · '), '#0369a1') +
    kpi('คะแนนเฉลี่ย', d.avg_rating != null ? d.avg_rating.toFixed(2) + ' ★' : '—', 'จากรีวิวที่มีคะแนน', '#ca8a04') +
    kpi('เชิงบวก', `${s.pos || 0}`, d.total ? `${(100 * (s.pos || 0) / d.total).toFixed(0)}%` : '', '#047857') +
    kpi('เชิงลบ', `${s.neg || 0}`, d.total ? `${d.neg_pct}% ของทั้งหมด` : '', '#b91c1c');
  const maxT = Math.max(1, ...(d.themes || []).map(t => t.n));
  document.getElementById('fbThemes').innerHTML = (d.themes || []).length ? (d.themes || []).map(t =>
    `<div class="flex items-center gap-2 text-xs mb-1.5"><span style="width:110px">${_fbEsc(t.theme)}</span>
       <div style="flex:1;height:10px;background:#e5e7eb;border-radius:5px;overflow:hidden"><div style="width:${(100 * t.n / maxT).toFixed(0)}%;height:100%;background:linear-gradient(90deg,#047857 ${100 - t.neg_pct}%,#b91c1c ${100 - t.neg_pct}%)"></div></div>
       <span style="width:90px;text-align:right;color:var(--text-muted)">${t.n} · ลบ ${t.neg_pct}%</span></div>`).join('') :
    '<p class="text-xs" style="color:var(--text-muted)">ยังไม่มีข้อมูล</p>';
  document.getElementById('fbMenus').innerHTML = (d.menus || []).length ?
    `<table class="text-xs w-full"><thead><tr class="text-left" style="color:var(--text-muted)"><th class="py-1">เมนู</th><th class="text-right">พูดถึง</th><th class="text-right">บวก</th><th class="text-right">ลบ</th></tr></thead><tbody>` +
    (d.menus || []).map(m => `<tr class="border-b" style="border-color:var(--border)"><td class="py-1"><a href="#" onclick="loadFeedbackItems(null,{menu:'${_fbEsc(m.menu).replace(/'/g, "\\'")}'});return false;">${_fbEsc(m.menu)}</a></td><td class="text-right">${m.n}</td><td class="text-right" style="color:#047857">${m.pos}</td><td class="text-right" style="color:#b91c1c">${m.neg}</td></tr>`).join('') + '</tbody></table>' :
    '<p class="text-xs" style="color:var(--text-muted)">ยังไม่พบเมนูในรีวิว (จับจากชื่อเมนูในระบบ)</p>';
  document.getElementById('fbBranches').innerHTML = (d.branches || []).length ?
    `<table class="text-xs w-full"><thead><tr class="text-left" style="color:var(--text-muted)"><th class="py-1">สาขา</th><th class="text-right">รีวิว</th><th class="text-right">คะแนน</th><th class="text-right">ลบ %</th></tr></thead><tbody>` +
    (d.branches || []).map(b => `<tr class="border-b" style="border-color:var(--border)"><td class="py-1">${_fbEsc(b.branch)}</td><td class="text-right">${b.n}</td><td class="text-right">${b.avg_rating != null ? b.avg_rating.toFixed(2) : '—'}</td><td class="text-right" style="color:${b.neg_pct >= 30 ? '#b91c1c' : 'inherit'}">${b.neg_pct}%</td></tr>`).join('') + '</tbody></table>' :
    '<p class="text-xs" style="color:var(--text-muted)">ยังไม่มีข้อมูล</p>';
  document.getElementById('fbNeg').innerHTML = (d.recent_negative || []).length ? (d.recent_negative || []).map(r =>
    `<div class="text-xs py-1.5 border-b" style="border-color:var(--border)"><div style="color:var(--text-muted)">${_fbEsc(r.date || '')} · ${_fbEsc(r.branch || '')} · ${_fbEsc(r.source || '')}${r.rating != null ? ' · ' + r.rating + '★' : ''}</div>
       <div>${_fbEsc(r.text)}</div><div class="mt-0.5">${(r.themes || []).map(t => `<span class="text-[10px] px-1.5 py-0.5 rounded" style="background:#fef2f2;color:#b91c1c">${_fbEsc(t)}</span> `).join('')}${(r.menus || []).map(m => `<span class="text-[10px] px-1.5 py-0.5 rounded" style="background:#eff6ff;color:#0369a1">${_fbEsc(m)}</span> `).join('')}</div></div>`).join('') :
    '<p class="text-xs" style="color:var(--text-muted)">ไม่มีรีวิวเชิงลบในช่วงนี้</p>';
}

async function loadFeedbackItems(qp, extra) {
  const p = qp ? new URLSearchParams(qp) : (() => { const q = new URLSearchParams(); if (getBranch()) q.set('branch', getBranch()); if (getMonthFrom()) q.set('month_from', getMonthFrom()); if (getMonthTo()) q.set('month_to', getMonthTo()); return q; })();
  Object.entries(extra || {}).forEach(([k, v]) => p.set(k, v));
  p.set('limit', '100');
  let d;
  try { d = await api(`${API}/api/feedback/items?${p}`); } catch (e) { return; }
  const el = document.getElementById('fbItems');
  el.innerHTML = (d.items || []).length ?
    (extra && extra.menu ? `<p class="text-xs mb-2" style="color:var(--text-muted)">กรองเมนู: <b>${_fbEsc(extra.menu)}</b> · <a href="#" onclick="loadFeedbackItems();return false;">ล้างตัวกรอง</a></p>` : '') +
    `<table class="text-xs w-full"><thead><tr class="text-left" style="color:var(--text-muted)"><th class="py-1">วันที่</th><th>สาขา</th><th>ช่องทาง</th><th>★</th><th>ความรู้สึก</th><th>ข้อความ</th><th></th></tr></thead><tbody>` +
    d.items.map(r => `<tr class="border-b align-top" style="border-color:var(--border)"><td class="py-1 whitespace-nowrap">${_fbEsc(r.rated_at || '')}</td><td>${_fbEsc(r.branch_name || '')}</td><td>${_fbEsc(r.source || '')}</td><td>${r.rating != null ? r.rating : ''}</td><td>${_fbSentChip(r.sentiment)}</td><td>${_fbEsc(r.text)}<div>${(r.themes || []).map(t => `<span class="text-[10px] px-1 rounded" style="background:#f3f4f6">${_fbEsc(t)}</span> `).join('')}${(r.menus || []).map(m => `<span class="text-[10px] px-1 rounded" style="background:#eff6ff;color:#0369a1">${_fbEsc(m)}</span> `).join('')}</div></td>
      <td><button class="text-[11px]" style="color:#b91c1c" title="ลบรีวิวนี้" onclick="deleteFeedback(${r.id})">✕</button></td></tr>`).join('') + '</tbody></table>' :
    '<p class="text-xs" style="color:var(--text-muted)">ไม่มีรายการ</p>';
}

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
    st.textContent = `นำเข้า ${r.inserted} รีวิว (ซ้ำ ${r.duplicates}) · วิเคราะห์โดย ${r.analysed_by}`;
    document.getElementById('fbText').value = '';
    document.getElementById('fbFile').value = '';
    loadFeedback(true);
  } catch (e) { st.textContent = 'ไม่สำเร็จ: ' + e.message; }
}

async function deleteFeedback(id) {
  if (!confirm('ลบรีวิวนี้?')) return;
  try {
    const r = await api(`${API}/api/feedback/${id}`, { method: 'DELETE' });
    if (r.detail) throw new Error(r.detail);
    loadFeedback(true);
  } catch (e) { alert('ไม่สำเร็จ: ' + e.message); }
}
