/* ═══════════════════════════════════════════════════════════════════
   ✅ Go-live checklist (feedback rollout) — the plan comes from
   /api/checklist/{name}/plan, the shared state from /api/checklist/{name};
   every tick is POSTed and attributed to the login by the server. The
   "ปิดสมบูรณ์" button only succeeds when the server sees every step done.
   ═══════════════════════════════════════════════════════════════════ */
const CL_NAME = 'feedback_rollout';
let _clPlan = null, _clState = null, _clMe = null, _clHideDone = false, _clBuilt = false;

function _clEsc(s) { return String(s === undefined || s === null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function _clFmt(iso) { if (!iso) return ''; const d = new Date(String(iso).replace(' ', 'T')); return isNaN(d) ? String(iso) : d.toLocaleString('th-TH', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }); }
function _clWho() { return _clMe && _clMe.username ? _clMe.username : ((document.getElementById('clWho') || {}).value || '').trim(); }
function _clBody(extra) { const b = Object.assign({}, extra || {}); if (!(_clMe && _clMe.username)) b.by = _clWho(); return b; }
async function _clPost(path, body) {
  const r = await fetch(`${API}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  let j = {}; try { j = await r.json(); } catch (e) {}
  if (!r.ok) { const d = j.detail; const err = new Error(typeof d === 'string' ? d : (d && d.message) || `HTTP ${r.status}`); err.detail = d; err.status = r.status; throw err; }
  return j;
}

async function loadChecklist(force) {
  const line = document.getElementById('clLine');
  try {
    if (!_clMe) { try { const r = await fetch(`${API}/api/auth/me`); _clMe = r.ok ? await r.json() : null; } catch (e) { _clMe = null; } }
    const wrap = document.getElementById('clWhoWrap');
    if (wrap) { wrap.style.display = _clMe && _clMe.username ? 'none' : ''; }
    const who = document.getElementById('clWho');
    if (who && !who.value) { try { who.value = localStorage.getItem('cl_who') || ''; } catch (e) {} who.onchange = () => { try { localStorage.setItem('cl_who', who.value.trim()); } catch (e) {} }; }
    const me = document.getElementById('clMe');
    if (me) me.textContent = _clMe && _clMe.username ? `ติ๊กในชื่อ ${_clMe.username}${_clMe.role ? ' (' + _clMe.role + ')' : ''}` : 'ไม่ได้บังคับล็อกอิน: ใส่ชื่อในช่องด้านซ้าย';
    if (!_clPlan || force) _clPlan = await getJSON(`/api/checklist/${CL_NAME}/plan`);
    _clState = await getJSON(`/api/checklist/${CL_NAME}`);
  } catch (e) { if (line) line.textContent = 'โหลดไม่สำเร็จ: ' + e.message; return; }
  if (!_clBuilt) { _clRenderPlan(); _clBuilt = true; }
  _clApplyState();
}

/* ── build the static DOM once from the plan ── */
function _clRenderPlan() {
  const host = document.getElementById('clPhases');
  const t = document.getElementById('clTitle'); if (t && _clPlan.title) t.textContent = '✅ ' + _clPlan.title;
  const statuses = _clPlan.statuses || {};
  host.innerHTML = _clPlan.phases.map(ph => `
    <section class="card p-4 mt-4" id="cl-${ph.id}" style="border-top:3px solid #0f766e">
      <div class="flex items-baseline gap-3 flex-wrap"><h3 class="text-base font-bold">${_clEsc(ph.name)}</h3><span class="text-sm font-bold" style="color:#0f766e" id="clpct-${ph.id}">0%</span>
        <span class="text-xs" style="color:var(--text-muted)">ผู้รับผิดชอบ: ${_clEsc(ph.owner)} · วันที่ ${ph.days[0] + 1}–${ph.days[1]} ของโครงการ${ph.wait ? ' (รวมช่วงรออนุมัติ)' : ''}</span></div>
      <p class="text-xs mt-1" style="color:var(--text-secondary)">${_clEsc(ph.why)}</p>
      ${ph.tasks.map(tk => `
        <article class="mt-3 rounded border" style="border-color:var(--border)" id="cltask-${tk.id}">
          <div class="flex flex-wrap items-center gap-3 px-3 py-2" style="background:var(--bg-input);border-bottom:1px solid var(--border)">
            <h4 class="text-sm font-semibold flex-1">${_clEsc(tk.name)} <span class="text-xs font-normal" style="color:var(--text-muted)">· ${_clEsc(tk.est)}</span></h4>
            <label class="text-[11px]" style="color:var(--text-muted)">สถานะ <select class="text-xs rounded border px-1 py-0.5" style="border-color:var(--border);background:var(--bg-input);color:var(--text-primary)" data-cltask="${tk.id}" onchange="setChecklistTask('${tk.id}', {status: this.value})">${Object.entries(statuses).map(([k, v]) => `<option value="${k}">${_clEsc(v)}</option>`).join('')}</select></label>
            <span class="text-[11px]" style="color:var(--text-muted)" id="cltstat-${tk.id}"></span>
          </div>
          <div class="px-3 py-2" style="border-bottom:1px solid var(--border)"><textarea data-clnote="${tk.id}" rows="1" class="w-full text-xs rounded border px-2 py-1" style="border-color:var(--border);background:var(--bg-input);color:var(--text-primary)" placeholder="หมายเหตุของงานนี้ (ค่าที่จด ใครทำ ติดอะไร — ไม่ใส่ความลับ)" onblur="setChecklistTask('${tk.id}', {note: this.value})"></textarea></div>
          ${tk.steps.map(s => `
            <div class="px-3 py-2 grid gap-1" style="grid-template-columns:1.4rem 1fr;border-bottom:1px solid var(--border)" id="clstep-${s.id}">
              <input type="checkbox" id="clcb-${s.id}" style="width:1.1rem;height:1.1rem;margin-top:.15rem;accent-color:#0f766e" onchange="setChecklistStep('${s.id}', this.checked)" aria-label="${_clEsc(s.t)}">
              <div><label for="clcb-${s.id}" class="text-sm font-semibold cl-title">${_clEsc(s.t)}</label> <span class="text-[11px]" style="color:var(--text-muted)" id="clby-${s.id}"></span>
                <div class="grid gap-x-3 gap-y-1 mt-1 text-xs" style="grid-template-columns:6.5rem 1fr">
                  ${s.how && s.how.length ? `<b class="text-[10px] uppercase tracking-wide" style="color:var(--text-muted)">ทำอย่างไร</b><ol class="list-decimal pl-4">${s.how.map(h => `<li>${_clEsc(h)}</li>`).join('')}</ol>` : ''}
                  ${s.why ? `<b class="text-[10px] uppercase tracking-wide" style="color:var(--text-muted)">ทำไม</b><div class="rounded px-2 py-0.5" style="background:#eff6ff">${_clEsc(s.why)}</div>` : ''}
                  ${s.check ? `<b class="text-[10px] uppercase tracking-wide" style="color:var(--text-muted)">สำเร็จเมื่อ</b><div class="rounded px-2 py-0.5" style="background:#ecfdf5;color:#065f46">${_clEsc(s.check)}</div>` : ''}
                  ${s.stuck ? `<b class="text-[10px] uppercase tracking-wide" style="color:var(--text-muted)">ถ้าติด</b><div class="rounded px-2 py-0.5" style="background:#fef2f2;color:#991b1b">${_clEsc(s.stuck)}</div>` : ''}
                </div></div>
            </div>`).join('')}
        </article>`).join('')}
    </section>`).join('');
}

function _clPhaseStats(ph) {
  let total = 0, done = 0, blocked = false;
  ph.tasks.forEach(tk => { if (((_clState.tasks || {})[tk.id] || {}).status === 'blocked') blocked = true;
    tk.steps.forEach(s => { total++; if (((_clState.steps || {})[s.id] || {}).done) done++; }); });
  return { total, done, pct: total ? Math.round(done / total * 100) : 0, blocked, complete: total > 0 && done === total };
}

/* ── paint the state onto the DOM (called after every load and every action) ── */
function _clApplyState() {
  const st = _clState, locked = !!st.locked;
  const prog = st.progress || { done: 0, total: 0, pct: 0 };
  document.getElementById('clPct').textContent = prog.pct + '%';
  document.getElementById('clBar').style.width = prog.pct + '%';
  const log = st.log || [];
  document.getElementById('clLine').textContent = `${prog.done} จาก ${prog.total} ขั้นตอน` + (log.length ? ` · ล่าสุด ${log[0].by || ''} ${_clFmt(log[0].at)}: ${log[0].detail || log[0].action}` : '');
  const startEl = document.getElementById('clStart');
  if (startEl && document.activeElement !== startEl) startEl.value = (st.meta || {}).start || '';
  startEl.disabled = locked;
  startEl.onchange = () => setChecklistStart(startEl.value);
  // complete banner + button
  const done = document.getElementById('clDone');
  const canReopen = !(_clMe && _clMe.username) || (_clMe.role === 'management');
  if (locked) {
    done.style.display = '';
    done.innerHTML = `<div class="flex flex-wrap items-center gap-3"><span class="text-lg">🎉</span><div class="flex-1"><b>ปิดสมบูรณ์แล้ว</b> โดย ${_clEsc(st.complete.by || '-')} เมื่อ ${_clEsc(_clFmt(st.complete.at || st.complete.value))} · ทุกขั้นตอน ${prog.total} รายการเสร็จครบ — รายการถูกล็อก แก้ไขไม่ได้</div>${canReopen ? `<button class="text-xs px-2 py-1 rounded border" style="border-color:#6ee7b7" onclick="reopenChecklist()">เปิดแก้ไขอีกครั้ง</button>` : '<span class="text-[11px]" style="color:var(--text-muted)">เปิดแก้ไขอีกครั้งได้เฉพาะผู้บริหาร</span>'}</div>`;
  } else { done.style.display = 'none'; done.innerHTML = ''; }
  const btn = document.getElementById('clComplete');
  btn.disabled = locked || prog.pct !== 100;
  btn.title = locked ? 'ปิดสมบูรณ์แล้ว' : (prog.pct === 100 ? 'ครบทุกขั้นตอน กดเพื่อปิดสมบูรณ์' : `ต้องติ๊กครบทุกขั้นตอนก่อน (เหลือ ${prog.total - prog.done})`);
  btn.textContent = locked ? '✅ ปิดสมบูรณ์แล้ว' : `✅ ปิดสมบูรณ์ (Complete)${prog.pct === 100 ? '' : ' · เหลือ ' + (prog.total - prog.done)}`;
  // phases / tasks / steps
  _clPlan.phases.forEach(ph => {
    const ps = _clPhaseStats(ph);
    const pe = document.getElementById('clpct-' + ph.id); if (pe) pe.textContent = ps.pct + '%';
    ph.tasks.forEach(tk => {
      const ts = (st.tasks || {})[tk.id] || {};
      const sel = document.querySelector(`select[data-cltask="${tk.id}"]`); if (sel) { sel.value = ts.status || 'todo'; sel.disabled = locked; sel.style.color = ts.status === 'blocked' ? '#b91c1c' : (ts.status === 'done' ? '#047857' : 'inherit'); }
      const stat = document.getElementById('cltstat-' + tk.id); if (stat) stat.textContent = ts.at ? `${ts.by || ''} · ${_clFmt(ts.at)}` : '';
      const ta = document.querySelector(`textarea[data-clnote="${tk.id}"]`); if (ta && document.activeElement !== ta) { ta.value = ts.note || ''; ta.disabled = locked; }
      const allDone = tk.steps.length && tk.steps.every(s => ((st.steps || {})[s.id] || {}).done);
      const art = document.getElementById('cltask-' + tk.id); if (art) art.style.display = (_clHideDone && allDone) ? 'none' : '';
      tk.steps.forEach(s => {
        const v = (st.steps || {})[s.id] || {};
        const cb = document.getElementById('clcb-' + s.id); if (cb) { cb.checked = !!v.done; cb.disabled = locked; }
        const row = document.getElementById('clstep-' + s.id);
        if (row) { row.style.display = (_clHideDone && v.done) ? 'none' : ''; row.querySelector('.cl-title').style.textDecoration = v.done ? 'line-through' : 'none'; row.querySelector('.cl-title').style.color = v.done ? 'var(--text-muted)' : ''; }
        const by = document.getElementById('clby-' + s.id); if (by) by.textContent = v.done ? `✓ ${v.by || ''} · ${_clFmt(v.at)}` : '';
      });
    });
  });
  _clDrawGantt((st.meta || {}).start || _clPlan.default_start);
  document.getElementById('clLog').innerHTML = log.length ? log.map(l => `<div class="py-0.5" style="border-bottom:1px solid var(--border)"><span style="color:var(--text-muted)">${_clEsc(_clFmt(l.at))}</span> · <b>${_clEsc(l.by || '')}</b> · ${_clEsc({tick:'ติ๊ก', untick:'ยกเลิกติ๊ก', complete:'ปิดสมบูรณ์', reopen:'เปิดแก้ไขอีกครั้ง', start:'ตั้งวันเริ่ม', note:'หมายเหตุ'}[l.action] || l.action)} · ${_clEsc(l.detail || l.item_id || '')}</div>`).join('') : '<span style="color:var(--text-muted)">ยังไม่มีการเปลี่ยนแปลง</span>';
}

/* ── actions ── */
async function _clAct(fn, undo) {
  try { await fn(); _clState = await getJSON(`/api/checklist/${CL_NAME}`); _clApplyState(); }
  catch (e) { if (undo) undo(); notifyError('บันทึกไม่สำเร็จ: ' + e.message); if (e.status === 409) loadChecklist(true); }
}
function setChecklistStep(id, done) { const cb = document.getElementById('clcb-' + id); _clAct(() => _clPost(`/api/checklist/${CL_NAME}/step/${id}`, _clBody({ done })), () => { if (cb) cb.checked = !done; }); }
function setChecklistTask(id, fields) { _clAct(() => _clPost(`/api/checklist/${CL_NAME}/task/${id}`, _clBody(fields))); }
function setChecklistStart(v) { if (!v) return; _clAct(() => _clPost(`/api/checklist/${CL_NAME}/meta`, _clBody({ start: v }))); }
function toggleChecklistDone() { _clHideDone = !_clHideDone; document.getElementById('clToggleDone').textContent = _clHideDone ? 'แสดงทั้งหมด' : 'ซ่อนที่เสร็จแล้ว'; _clApplyState(); }

async function completeChecklist() {
  if (!confirm('ยืนยันปิดสมบูรณ์? ระบบจะตรวจว่าทุกขั้นตอนถูกติ๊กครบ แล้วล็อกรายการนี้ (ผู้บริหารเปิดแก้ไขอีกครั้งได้)')) return;
  try {
    const r = await _clPost(`/api/checklist/${CL_NAME}/complete`, _clBody({}));
    _clState = await getJSON(`/api/checklist/${CL_NAME}`); _clApplyState();
    if (typeof notifySuccess === 'function') notifySuccess(r.already ? 'ปิดสมบูรณ์อยู่แล้ว' : 'ปิดสมบูรณ์แล้ว 🎉');
  } catch (e) {
    const d = e.detail || {};
    const missing = d.missing || [];
    notifyError((d.message || e.message) + (missing.length ? ' — ' + missing.slice(0, 3).map(m => m.title).join(' · ') + (missing.length > 3 ? ' …' : '') : ''));
    if (missing.length) { _clHideDone = false; _clApplyState(); const row = document.getElementById('clstep-' + missing[0].id); if (row) { row.scrollIntoView({ behavior: 'smooth', block: 'center' }); row.style.outline = '2px solid #b45309'; setTimeout(() => { row.style.outline = ''; }, 4000); } }
  }
}
async function reopenChecklist() {
  const reason = prompt('เหตุผลที่เปิดแก้ไขอีกครั้ง (บันทึกในประวัติ)') || '';
  _clAct(() => _clPost(`/api/checklist/${CL_NAME}/reopen`, _clBody({ reason })));
}

/* ── Gantt ── */
function _clDrawGantt(startIso) {
  const host = document.getElementById('clGantt'); if (!host || !startIso) return;
  const HOR = _clPlan.horizon_days || 36, left = 300, dayW = 22, rowH = 34, top = 34, rows = _clPlan.phases.length;
  const W = left + HOR * dayW + 20, H = top + rows * rowH + 10;
  const start = new Date(startIso + 'T00:00:00'), today = new Date(); today.setHours(0, 0, 0, 0);
  const todayDay = Math.round((today.getTime() - start.getTime()) / 86400000);
  const addDays = n => { const d = new Date(start); d.setDate(d.getDate() + n); return d; };
  const th = d => d.toLocaleDateString('th-TH', { day: 'numeric', month: 'short' });
  const lbl = document.getElementById('clToday'); if (lbl) lbl.textContent = todayDay < 0 ? `วันนี้ ${th(today)} · โครงการเริ่ม ${th(start)} (อีก ${-todayDay} วัน)` : `วันนี้ ${th(today)} = วันที่ ${todayDay + 1} ของโครงการ (เริ่ม ${th(start)})`;
  let g = `<svg viewBox="0 0 ${W} ${H}" style="min-width:760px;width:100%;height:auto;display:block" role="img" aria-label="ไทม์ไลน์"><defs><pattern id="clhatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="3" height="6" fill="#b45309"/></pattern></defs>`;
  for (let d = 0; d <= HOR; d += 7) { const x = left + d * dayW; g += `<line x1="${x}" y1="${top - 6}" x2="${x}" y2="${H - 6}" stroke="var(--border)"/><text x="${x + 3}" y="${top - 12}" font-size="11" fill="var(--text-muted)">${th(addDays(d))}</text>`; }
  _clPlan.phases.forEach((ph, i) => {
    const y = top + i * rowH, ps = _clPhaseStats(ph), [a, b] = ph.days, x1 = left + a * dayW, w = Math.max(dayW * .6, (b - a) * dayW);
    const late = !ps.complete && todayDay > b;
    const fill = ps.complete ? '#047857' : (ps.blocked ? '#b91c1c' : '#0f766e');
    const parts = ph.name.split(' · ');
    g += `<text x="8" y="${y + 21}" font-size="12.5" fill="var(--text-primary)" font-weight="600">${_clEsc(parts[0])}</text><text x="86" y="${y + 21}" font-size="12" fill="var(--text-secondary)">${_clEsc((parts[1] || '').slice(0, 34))}</text>`;
    g += `<rect x="${x1}" y="${y + 7}" width="${w}" height="18" rx="4" fill="${fill}" opacity=".22"/><rect x="${x1}" y="${y + 7}" width="${w * ps.pct / 100}" height="18" rx="4" fill="${fill}"/>`;
    if (ph.wait) g += `<rect x="${left + ph.wait[0] * dayW}" y="${y + 7}" width="${(ph.wait[1] - ph.wait[0]) * dayW}" height="18" rx="4" fill="url(#clhatch)" opacity=".9"/>`;
    if (late) g += `<rect x="${x1 - 1}" y="${y + 6}" width="${w + 2}" height="20" rx="5" fill="none" stroke="#b45309" stroke-width="2"/>`;
    g += `<text x="${x1 + w + 6}" y="${y + 21}" font-size="11.5" fill="var(--text-secondary)">${ps.done}/${ps.total}${ps.blocked ? ' ⚠' : ''}</text>`;
  });
  if (todayDay >= 0 && todayDay <= HOR) { const tx = left + todayDay * dayW; g += `<line x1="${tx}" y1="${top - 8}" x2="${tx}" y2="${H - 6}" stroke="#b91c1c" stroke-width="1.5" stroke-dasharray="4 3"/><text x="${tx + 4}" y="${H - 8}" font-size="11" fill="#b91c1c">วันนี้</text>`; }
  host.innerHTML = g + '</svg>';
}
