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
/* The reply state of one review (item or recent-negative entry): a ✍ ตอบ link,
   or where the queued reply stands. Only fetched reviews (external_id) can be answered. */
function _fbReplyChip(r) {
  if (!r.external_id) return '';
  const s = r.reply_status;
  const chip = (t, fg, bg, title) => `<span class="text-[10px] px-1.5 py-0.5 rounded" style="background:${bg};color:${fg}" title="${_fbEsc(title || '')}">${t}</span>`;
  const link = (t, fn) => `<a href="#" class="text-[11px]" style="color:#0369a1" onclick="${fn}(${r.id});return false;">${t}</a>`;
  if (s === 'pending') return chip('รอส่ง (รอบ 09:00)', '#92400e', '#fef3c7') + ' ' + link('ยกเลิก', 'cancelFeedbackReply');
  if (s === 'sent' || r.reply_text || r.replied) return chip('ตอบแล้ว ✓', '#047857', '#ecfdf5');
  if (s === 'failed') return chip('ส่งไม่สำเร็จ', '#b91c1c', '#fef2f2', r.reply_error) + ' ' + link('ลองใหม่', 'openFeedbackReply');
  if (s === 'manual') return chip('ตอบเองในแอป', '#0369a1', '#eff6ff', 'ตอบผ่านระบบไม่ได้ — คัดลอกข้อความที่บันทึกไว้ไปตอบใน Google Maps / Facebook') + ' ' + link('ดูข้อความ', 'openFeedbackReply');
  return link('✍ ตอบ', 'openFeedbackReply');
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

/* review = on the chain's own listing/page · comment = under the chain's own post/video ·
   mention = anywhere else on the web (X, YouTube, news, blogs). '' = every kind. */
let _fbKind = '';
const _FB_KINDS = [['', 'ทั้งหมด'], ['review', 'รีวิว'], ['comment', 'คอมเมนต์'], ['mention', 'การพูดถึง']];
function setFeedbackKind(k) { _fbKind = k || ''; loadFeedback(true); }
function _fbRenderKindChips(kinds) {
  const el = document.getElementById('fbKindChips');
  if (!el) return;
  const total = Object.values(kinds || {}).reduce((a, b) => a + b, 0);
  el.innerHTML = _FB_KINDS.map(([k, label]) => {
    const n = k ? ((kinds || {})[k] || 0) : total, on = k === _fbKind;
    return `<button class="text-[11px] px-2 py-0.5 rounded-full" style="border:1px solid ${on ? '#0369a1' : 'var(--border)'};background:${on ? '#eff6ff' : 'transparent'};color:${on ? '#0369a1' : 'var(--text-secondary)'}" onclick="setFeedbackKind('${k}')">${label} ${n}</button>`;
  }).join(' ');
}

async function loadFeedback(force) {
  await _fbFillBranches();
  const fk = _filterKey() + '|' + _fbKind;
  if (!force && _tabLastKey.feedback === fk) return;
  const st = document.getElementById('fbStatus');
  if (st) st.textContent = 'กำลังโหลด…';
  const qp = new URLSearchParams();
  if (getBranch())    qp.set('branch', getBranch());
  if (getMonthFrom()) qp.set('month_from', getMonthFrom());
  if (getMonthTo())   qp.set('month_to', getMonthTo());
  if (_fbKind)        qp.set('kind', _fbKind);
  let d;
  try { d = await api(`${API}/api/feedback/summary?${qp}`); }
  catch (e) { if (st) st.textContent = 'โหลดข้อมูลไม่สำเร็จ'; return; }
  _tabLastKey.feedback = fk;
  _fbRenderKindChips(d.kinds);
  renderFeedback(d);
  loadFeedbackItems(qp);
  loadFeedbackSources();
}

async function loadFeedbackSources() {
  const el = document.getElementById('fbSources');
  if (!el) return;
  let d;
  try { d = await api(`${API}/api/feedback/sources`); } catch (e) { el.textContent = 'โหลดแหล่งข้อมูลไม่สำเร็จ'; return; }
  const rows = d.sources || [];
  if (!rows.length) {
    el.innerHTML = 'ยังไม่มีรีวิวจากแหล่งใด — ตั้งค่าการดึงอัตโนมัติตาม <a href="/docs/FEEDBACK_SOURCES.html" target="_blank" style="color:#0369a1;text-decoration:underline">คู่มือ</a> หรืออัปโหลด/วางรีวิวด้านล่าง';
    return;
  }
  el.innerHTML = `<table class="text-xs w-full"><thead><tr class="text-left" style="color:var(--text-muted)"><th class="py-1">แหล่ง</th><th class="text-right">รีวิว</th><th class="text-right">ดึงอัตโนมัติ</th><th>รีวิวล่าสุด</th><th class="text-right">รอสาขาตอบ</th><th>ดึงล่าสุด</th><th>ผล</th></tr></thead><tbody>` +
    rows.map(s => {
      const lr = s.last_run, ok = lr && lr.status === 'ok';
      const result = lr ? (ok ? `✓ ใหม่ ${lr.inserted} · อัปเดต ${lr.updated}${lr.message ? ' · ' + _fbEsc(lr.message) : ''}` : '✗ ' + _fbEsc(lr.message || 'ล้มเหลว')) : '';
      return `<tr class="border-b" style="border-color:var(--border)"><td class="py-1">${_fbEsc(s.source)}</td><td class="text-right">${(s.n || 0).toLocaleString()}</td><td class="text-right">${(s.fetched || 0).toLocaleString()}</td><td>${_fbEsc(s.latest || '—')}</td><td class="text-right" style="color:${s.unreplied ? '#b91c1c' : 'inherit'}">${s.unreplied || 0}</td><td class="whitespace-nowrap">${lr ? _fbEsc(lr.ran_at || '') : '<span style="color:var(--text-muted)">อัปโหลดเอง</span>'}</td><td style="color:${lr ? (ok ? '#047857' : '#b91c1c') : 'inherit'}">${result}</td></tr>`;
    }).join('') + '</tbody></table>';
  const q = d.reply_queue || {};
  if ((q.pending || 0) + (q.failed || 0) + (q.manual || 0) + (q.sent || 0) > 0) {
    el.innerHTML += `<p class="text-[11px] mt-2" style="color:var(--text-muted)">คิวตอบรีวิว: <b style="color:#92400e">รอส่ง ${q.pending || 0}</b> (ส่งในรอบดึง 09:00) · ส่งแล้ว ${q.sent || 0}${q.failed ? ` · <b style="color:#b91c1c">ส่งไม่สำเร็จ ${q.failed}</b>` : ''}${q.manual ? ` · ตอบเองในแอป ${q.manual}` : ''}</p>`;
  }
}

function renderFeedback(d) {
  const st = document.getElementById('fbStatus');
  const eng = Object.entries(d.analysed_by || {}).map(([k, v]) => `${k} ${v}`).join(' · ');
  if (st) st.textContent = d.total ? `${d.total.toLocaleString()} รีวิว · วิเคราะห์โดย ${eng || '—'}${d.unreplied ? ` · รอสาขาตอบ ${d.unreplied}` : ''}` : 'ยังไม่มีรีวิวในช่วงที่เลือก — วางหรืออัปโหลดด้านบน';
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
    `<div class="text-xs py-1.5 border-b" style="border-color:var(--border)"><div style="color:var(--text-muted)">${_fbEsc(r.date || '')} · ${_fbEsc(r.branch || '')} · ${_fbEsc(r.source || '')}${r.rating != null ? ' · ' + r.rating + '★' : ''}${r.url ? ` · <a href="${_fbEsc(r.url)}" target="_blank" rel="noopener" style="color:#0369a1">เปิดรีวิว ↗</a>` : ''}${r.needs_reply ? ' · <span style="color:#b91c1c;font-weight:600">รอสาขาตอบ</span>' : ''} · ${_fbReplyChip(r)}</div>
       <div>${r.text ? _fbEsc(r.text) : '<i style="color:var(--text-muted)">(ให้ดาวอย่างเดียว)</i>'}</div><div class="mt-0.5">${(r.themes || []).map(t => `<span class="text-[10px] px-1.5 py-0.5 rounded" style="background:#fef2f2;color:#b91c1c">${_fbEsc(t)}</span> `).join('')}${(r.menus || []).map(m => `<span class="text-[10px] px-1.5 py-0.5 rounded" style="background:#eff6ff;color:#0369a1">${_fbEsc(m)}</span> `).join('')}</div></div>`).join('') :
    '<p class="text-xs" style="color:var(--text-muted)">ไม่มีรีวิวเชิงลบในช่วงนี้</p>';
}

async function loadFeedbackItems(qp, extra) {
  const p = qp ? new URLSearchParams(qp) : (() => { const q = new URLSearchParams(); if (getBranch()) q.set('branch', getBranch()); if (getMonthFrom()) q.set('month_from', getMonthFrom()); if (getMonthTo()) q.set('month_to', getMonthTo()); if (_fbKind) q.set('kind', _fbKind); return q; })();
  Object.entries(extra || {}).forEach(([k, v]) => p.set(k, v));
  p.set('limit', '100');
  let d;
  try { d = await api(`${API}/api/feedback/items?${p}`); } catch (e) { return; }
  const el = document.getElementById('fbItems');
  el.innerHTML = (d.items || []).length ?
    (extra && extra.menu ? `<p class="text-xs mb-2" style="color:var(--text-muted)">กรองเมนู: <b>${_fbEsc(extra.menu)}</b> · <a href="#" onclick="loadFeedbackItems();return false;">ล้างตัวกรอง</a></p>` : '') +
    `<table class="text-xs w-full"><thead><tr class="text-left" style="color:var(--text-muted)"><th class="py-1">วันที่</th><th>สาขา</th><th>ช่องทาง</th><th>★</th><th>ความรู้สึก</th><th>ข้อความ</th><th>ตอบกลับ</th><th></th></tr></thead><tbody>` +
    d.items.map(r => `<tr class="border-b align-top" style="border-color:var(--border)"><td class="py-1 whitespace-nowrap">${_fbEsc(r.rated_at || '')}</td><td>${_fbEsc(r.branch_name || '')}</td><td class="whitespace-nowrap">${_fbEsc(r.source || '')}${r.url ? ` <a href="${_fbEsc(r.url)}" target="_blank" rel="noopener" title="เปิดรีวิวต้นทาง" style="color:#0369a1">↗</a>` : ''}</td><td>${r.rating != null ? r.rating : ''}</td><td>${_fbSentChip(r.sentiment)}</td><td>${r.text ? _fbEsc(r.text) : '<i style="color:var(--text-muted)">(ให้ดาวอย่างเดียว)</i>'}<div>${(r.themes || []).map(t => `<span class="text-[10px] px-1 rounded" style="background:#f3f4f6">${_fbEsc(t)}</span> `).join('')}${(r.menus || []).map(m => `<span class="text-[10px] px-1 rounded" style="background:#eff6ff;color:#0369a1">${_fbEsc(m)}</span> `).join('')}</div></td>
      <td>${r.reply_text ? `<span title="${_fbEsc(r.replied_at || '')}" style="color:#047857">✓ ${_fbEsc(r.reply_text.slice(0, 80))}</span>` : `${r.needs_reply ? '<span style="color:#b91c1c">รอสาขาตอบ</span> ' : ''}${_fbReplyChip(r)}`}</td>
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
    st.textContent = `นำเข้า ${r.inserted} รีวิว (อัปเดต ${r.updated || 0} · ซ้ำ ${r.duplicates}) · วิเคราะห์โดย ${r.analysed_by}` +
      ((r.unmatched || []).length ? ` · ไม่พบสาขาสำหรับ: ${r.unmatched.join(', ')}` : '');
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

/* ── replies: draft in the panel, queue it, the nightly fetcher posts it ── */
async function openFeedbackReply(id) {
  const p = document.getElementById('fbReplyPanel');
  if (!p) return;
  p.style.display = '';
  p.innerHTML = '<p class="text-xs" style="color:var(--text-muted)">กำลังร่างคำตอบ…</p>';
  let d;
  try { d = await api(`${API}/api/feedback/${id}/reply-draft`); if (d.detail) throw new Error(d.detail); }
  catch (e) { p.innerHTML = `<p class="text-xs" style="color:#b91c1c">ร่างคำตอบไม่สำเร็จ: ${_fbEsc(e.message)}</p>`; return; }
  const manual = d.channel === 'manual';
  const target = d.note || '';
  p.innerHTML = `<div class="flex items-start justify-between gap-2">
      <h3 class="text-sm font-semibold">✍ ตอบรีวิว · ${_fbEsc(d.branch || '-')} · ${_fbEsc(d.source || '')}${d.rating != null ? ' · ' + d.rating + '★' : ''}${d.date ? ' · ' + _fbEsc(d.date) : ''}${d.url ? ` · <a href="${_fbEsc(d.url)}" target="_blank" rel="noopener" style="color:#0369a1;font-weight:400">เปิดรีวิว ↗</a>` : ''}</h3>
      <a href="#" onclick="closeFeedbackReply();return false;" style="color:var(--text-muted)">✕</a></div>
    <p class="text-xs my-1 px-2 py-1 rounded" style="background:var(--bg-input);color:var(--text-secondary)">${d.text ? _fbEsc(d.text) : '<i>(ให้ดาวอย่างเดียว)</i>'}</p>
    ${d.reply_status === 'failed' ? `<p class="text-xs" style="color:#b91c1c">ครั้งก่อนส่งไม่สำเร็จ: ${_fbEsc(d.reply_error || '')}</p>` : ''}
    <textarea id="fbReplyText" rows="4" class="w-full mt-1 rounded border px-2 py-1 text-sm" style="border-color:var(--border);background:var(--bg-input);color:var(--text-primary)" oninput="_fbReplyCount(${d.max_len || 4000})">${_fbEsc(d.draft || '')}</textarea>
    <div class="flex items-center gap-3 mt-2 flex-wrap">
      ${d.channel === 'none' ? '' : `<button class="btn-primary text-sm px-3 py-1.5 rounded" onclick="sendFeedbackReply(${id})">${manual ? 'บันทึกข้อความ' : 'ส่งในรอบถัดไป'}</button>`}
      <span id="fbReplyCount" class="text-[11px]" style="color:var(--text-muted)"></span>
      <span class="text-[11px]" style="color:var(--text-muted)">ร่างโดย ${d.engine === 'claude' ? 'Claude' : (d.engine === 'saved' ? 'ข้อความที่บันทึกไว้' : 'แม่แบบ')} · แก้ไขได้ก่อนส่ง · ${_fbEsc(target)}</span>
      <span id="fbReplyStatus" class="text-[11px]" style="color:var(--text-muted)"></span>
    </div>`;
  _fbReplyCount(d.max_len || 4000);
  p.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function _fbReplyCount(max) {
  const ta = document.getElementById('fbReplyText'), c = document.getElementById('fbReplyCount');
  if (!ta || !c) return;
  const n = ta.value.length;
  c.textContent = `${n}/${max} ตัวอักษร`;
  c.style.color = n > max ? '#b91c1c' : 'var(--text-muted)';
}

function closeFeedbackReply() {
  const p = document.getElementById('fbReplyPanel');
  if (p) { p.style.display = 'none'; p.innerHTML = ''; }
}

async function sendFeedbackReply(id) {
  const st = document.getElementById('fbReplyStatus');
  const text = (document.getElementById('fbReplyText') || {}).value || '';
  if (!text.trim()) { if (st) st.textContent = 'ใส่ข้อความก่อน'; return; }
  try {
    const r = await api(`${API}/api/feedback/${id}/reply`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: text.trim() }) });
    if (r.detail) throw new Error(r.detail);
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
