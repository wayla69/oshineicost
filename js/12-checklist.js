/* ═══════════════════════════════════════════════════════════════════
   Go-live checklist (feedback rollout) — a working screen:
   • header: progress ring, phase chips (state-coloured), the Complete button
     (enabled only at 100 %; the server re-checks), start date, who you are
   • rail: one phase at a time (default = the first phase with open steps),
     or every phase / only what is still open
   • "ทำต่อเลย": the next open steps and the blocked tasks, each a jump link
   • tasks: status as a segmented control, note, steps that expand to their
     how / why / done-when / if-stuck; commands carry a copy button; the first
     open step of the phase is expanded for you
   • ticking the last open step of a task marks the task done; unticking
     in a done task puts it back to "doing"
   Plan from /api/checklist/{name}/plan, state from /api/checklist/{name};
   every write is attributed to the login by the server.
   ═══════════════════════════════════════════════════════════════════ */
const CL_PLANS = [
  { name: 'feedback_rollout', short: 'เปิดใช้งานระบบ (Go-live)', pdf: '/docs/FEEDBACK_ROLLOUT_CHECKLIST.pdf' },
  { name: 'feedback_marketing_prep', short: 'การตลาด: ไปขอข้อมูล', pdf: '/docs/FEEDBACK_MARKETING_PREP_CHECKLIST.pdf' },
];
let CL_NAME = 'feedback_rollout';
let _clPlan = null, _clState = null, _clMe = null, _clHideDone = false;
let _clView = 'auto';                 // 'auto' | phase id | 'all' | 'open'
let _clOpen = new Set();              // expanded step ids
let _clAutoOpened = '';               // the phase whose first open step was auto-expanded
let _clGanttOpen = window.innerWidth >= 900;

function _clEsc(s) { return String(s === undefined || s === null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function _clFmt(iso) { if (!iso) return ''; const d = new Date(String(iso).replace(' ', 'T')); return isNaN(d.getTime()) ? String(iso) : d.toLocaleString('th-TH', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }); }
function _clWho() { return _clMe && _clMe.username ? _clMe.username : ((document.getElementById('clWho') || {}).value || '').trim(); }
function _clBody(extra) { const b = Object.assign({}, extra || {}); if (!(_clMe && _clMe.username)) b.by = _clWho(); return b; }
async function _clPost(path, body) {
  const r = await fetch(`${API}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  let j = {}; try { j = await r.json(); } catch (e) {}
  if (!r.ok) { const d = j.detail; const err = new Error(typeof d === 'string' ? d : (d && d.message) || `HTTP ${r.status}`); err.detail = d; err.status = r.status; throw err; }
  return j;
}

/* ── highlight rules: commands and files (with copy), warnings, figures ── */
const _CL_WARN = [/ห้าม[^\s,·]*(?:\s[^\s,·]+){0,3}/g, /สำคัญมาก/g, /อย่าลืม/g, /ไม่มีย้อนหลัง/g, /หมดอายุทุก 7 วัน/g, /ไม่หมดอายุ/g, /ทางวิกฤต[^\s,·]*/g, /เก็บเป็นความลับ/g, /จะเห็นครั้งเดียว/g];
const _CL_VAL = [/~?\$\d[\d,]*(?:\/เดือน)?/g, /\d+[–-]\d+ สัปดาห์/g, /\d+ (?:สัปดาห์|วัน|นาที|ชั่วโมง)/g, /ไม่เกิน \d+ ตัวอักษร/g, /09:00|07:30/g];
function _clHl(s) {
  let t = _clEsc(s);
  const cmd = (m) => `<code class="cl-cmd">${m}</code><button class="cl-copy" type="button" onclick="clCopy(this, event)" data-copy="${m.replace(/&quot;/g, '"')}">คัดลอก</button>`;
  t = t.replace(/(py backend\\fetch_feedback\.py[^→(]*?)(?=\s*(?:→|\(|ต้อง|ดู|แล้ว|กด|ใส่|$))/g, (m, c) => cmd(c.trim()));
  t = t.replace(/(flyctl secrets (?:set|list)[^→(]*?)(?=\s*(?:→|\(|$))/g, (m, c) => cmd(c.trim()));
  t = t.replace(/(https?:\/\/[^\s"<>)]+)/g, '<code class="cl-cmd">$1</code>');
  t = t.replace(/(?<![\w\/">])((?:C:\\|backend\\|OneDrive\\)[^\s"&,()]+(?:\s[A-Za-z]+\.(?:bat|log|txt|json))?)/g, '<code class="cl-cmd">$1</code>');
  t = t.replace(/(?<![\w\/">])([\w.-]+\.(?:json|env|bat|log|txt|zip|py|html|pdf)\b)/g, (m, f) => m.includes('cl-cmd') ? m : `<code class="cl-cmd">${f}</code>`);
  _CL_WARN.forEach(re => { t = t.replace(re, m => `<mark class="cl-warn">${m}</mark>`); });
  _CL_VAL.forEach(re => { t = t.replace(re, m => `<span class="cl-val">${m}</span>`); });
  return t;
}
function clCopy(btn, ev) {
  if (ev) ev.stopPropagation();
  const text = btn.getAttribute('data-copy') || '';
  const done = () => { const old = btn.textContent; btn.textContent = 'คัดลอกแล้ว ✓'; setTimeout(() => { btn.textContent = old; }, 1500); };
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, () => alert(text));
  else alert(text);
}

/* ── load ── */
async function loadChecklist(force) {
  const line = document.getElementById('clLine');
  try {
    if (!_clMe) { try { const r = await fetch(`${API}/api/auth/me`); _clMe = r.ok ? await r.json() : null; } catch (e) { _clMe = null; } }
    const wrap = document.getElementById('clWhoWrap');
    if (wrap) wrap.style.display = _clMe && _clMe.username ? 'none' : '';
    const who = document.getElementById('clWho');
    if (who && !who.value) { try { who.value = localStorage.getItem('cl_who') || ''; } catch (e) {} who.onchange = () => { try { localStorage.setItem('cl_who', who.value.trim()); } catch (e) {} }; }
    const me = document.getElementById('clMe');
    if (me) me.textContent = _clMe && _clMe.username ? `ติ๊กในชื่อ ${_clMe.username}${_clMe.role ? ' · ' + _clMe.role : ''}` : 'ไม่ได้บังคับล็อกอิน: ใส่ชื่อของคุณด้านบน';
    if (!_clPlan || force) _clPlan = await getJSON(`/api/checklist/${CL_NAME}/plan`);
    _clState = await getJSON(`/api/checklist/${CL_NAME}`);
  } catch (e) { if (line) line.textContent = 'โหลดไม่สำเร็จ: ' + e.message; return; }
  _clRenderPicker();
  _clRender();
}

/* ── which plan: a small picker above the header, same tab for every plan ── */
function _clRenderPicker() {
  const host = document.getElementById('clPicker');
  if (!host) return;
  host.innerHTML = CL_PLANS.map(p => `<button type="button" class="cl-ghost${p.name === CL_NAME ? ' on' : ''}" onclick="clSwitchPlan('${p.name}')">${_clEsc(p.short)}</button>`).join('');
  const pdf = document.getElementById('clPdfLink');
  if (pdf) { const cur = CL_PLANS.find(p => p.name === CL_NAME); pdf.href = (cur || CL_PLANS[0]).pdf; }
}
function clSwitchPlan(name) {
  if (name === CL_NAME) return;
  CL_NAME = name; _clPlan = null; _clState = null; _clView = 'auto'; _clOpen = new Set(); _clAutoOpened = '';
  loadChecklist(true);
}

/* ── derived state ── */
function _clStepDone(id) { return !!(((_clState || {}).steps || {})[id] || {}).done; }
function _clTaskState(tk) { const st = ((_clState || {}).tasks || {})[tk.id] || {}; const total = tk.steps.length, done = tk.steps.filter(s => _clStepDone(s.id)).length; return { status: st.status || 'todo', note: st.note || '', by: st.by, at: st.at, total, done, allDone: total > 0 && done === total }; }
function _clPhaseStats(ph) {
  let total = 0, done = 0, blocked = false;
  ph.tasks.forEach(tk => { const ts = _clTaskState(tk); if (ts.status === 'blocked') blocked = true; total += ts.total; done += ts.done; });
  return { total, done, pct: total ? Math.round(done / total * 100) : 0, blocked, complete: total > 0 && done === total };
}
function _clDayInfo() {
  const startIso = ((_clState || {}).meta || {}).start || (_clPlan || {}).default_start;
  const start = new Date(startIso + 'T00:00:00'), today = new Date(); today.setHours(0, 0, 0, 0);
  return { startIso, start, today, todayDay: Math.round((today.getTime() - start.getTime()) / 86400000) };
}
function _clPhaseMode(ph, di) {
  const ps = _clPhaseStats(ph);
  if (ps.complete) return 'done';
  if (ps.blocked) return 'blocked';
  if (ph.wait && di.todayDay >= ph.wait[0] && di.todayDay < ph.wait[1] && ps.done > 0) return 'waiting';
  if (ps.done > 0) return 'doing';
  return 'todo';
}
function _clCurrentPhase() { return (_clPlan.phases.find(ph => !_clPhaseStats(ph).complete) || _clPlan.phases[0]).id; }
function _clShort(ph) { return ph.short || (ph.name.split(' · ')[1] || ph.name).split(' (')[0].slice(0, 22); }
function _clVisiblePhases() {
  if (_clView === 'all' || _clView === 'open') return _clPlan.phases;
  const id = _clView === 'auto' ? _clCurrentPhase() : _clView;
  return _clPlan.phases.filter(ph => ph.id === id);
}

/* ── render everything from state ── */
function _clRender() {
  const st = _clState, locked = !!st.locked, prog = st.progress || { done: 0, total: 0, pct: 0 };
  const di = _clDayInfo();
  // header
  document.getElementById('clPct').textContent = prog.pct + '%';
  document.getElementById('clRingArc').setAttribute('stroke-dasharray', `${(prog.pct * 97.4 / 100).toFixed(1)} 100`);
  const t = document.getElementById('clTitle'); if (t && _clPlan.title) t.textContent = '✓ ' + _clPlan.title;
  const log = st.log || [];
  const doneCount = _clPlan.phases.filter(ph => _clPhaseStats(ph).complete).length;
  document.getElementById('clLine').textContent = `${prog.done}/${prog.total} ขั้นตอน · เฟสเสร็จ ${doneCount}/${_clPlan.phases.length}` + (log.length ? ` · ล่าสุด ${log[0].by || ''} ${_clFmt(log[0].at)}: ${log[0].detail || log[0].action}` : ' · ยังไม่มีใครเริ่ม');
  const startEl = document.getElementById('clStart');
  if (startEl && document.activeElement !== startEl) startEl.value = di.startIso || '';
  startEl.disabled = locked; startEl.onchange = () => setChecklistStart(startEl.value);
  // phase chips (header) + rail nav
  const chips = _clPlan.phases.map((ph, i) => { const ps = _clPhaseStats(ph), mode = _clPhaseMode(ph, di), late = !ps.complete && di.todayDay > ph.days[1];
    const cur = (_clView === 'auto' ? _clCurrentPhase() : _clView) === ph.id;
    return `<button type="button" class="cl-chip ${mode}${late ? ' late' : ''}${cur ? ' active' : ''}" onclick="clSelectPhase('${ph.id}')" title="${_clEsc(ph.name)} · ${ps.done}/${ps.total}"><b>${ps.complete ? '✓' : i}</b>${_clEsc(_clShort(ph))} <span style="opacity:.75">${ps.done}/${ps.total}</span></button>`; }).join('');
  document.getElementById('clPhaseChips').innerHTML = chips;
  document.getElementById('clNav').innerHTML = _clPlan.phases.map(ph => { const ps = _clPhaseStats(ph), mode = _clPhaseMode(ph, di);
    const cur = (_clView === 'auto' ? _clCurrentPhase() : _clView) === ph.id;
    return `<button type="button" class="${cur ? 'active' : ''}" onclick="clSelectPhase('${ph.id}')" title="${_clEsc(ph.name)}"><span>${_clEsc(ph.name.split(' · ')[0])} <small>${_clEsc(_clShort(ph))}</small></span><small>${ps.done}/${ps.total}${mode === 'blocked' ? ' ⚠' : mode === 'waiting' ? '' : mode === 'done' ? ' ✓' : ''}</small><span class="bar ${mode}"><i style="width:${ps.pct}%"></i></span></button>`; }).join('');
  document.getElementById('clViews').innerHTML = [['all', 'ทุกเฟส (ยาว)'], ['open', 'เฉพาะที่ยังค้าง']].map(([k, l]) => `<button type="button" class="${_clView === k ? 'active' : ''}" onclick="clSelectPhase('${k}')"><span>${l}</span></button>`).join('');
  // sign-off banner + button
  const done = document.getElementById('clDone');
  const canReopen = !(_clMe && _clMe.username) || _clMe.role === 'management';
  if (locked) {
    done.style.display = '';
    done.innerHTML = `<div class="flex flex-wrap items-center gap-3"><span class="text-2xl"></span><div class="flex-1"><b>ปิดสมบูรณ์แล้ว</b> โดย ${_clEsc(st.complete.by || '-')} เมื่อ ${_clEsc(_clFmt(st.complete.at || st.complete.value))} · ทุกขั้นตอน ${prog.total} รายการเสร็จครบ — รายการถูกล็อก แก้ไขไม่ได้</div>${canReopen ? `<button class="cl-ghost" onclick="reopenChecklist()">เปิดแก้ไขอีกครั้ง</button>` : '<span class="text-[11px]" style="color:var(--text-muted)">เปิดแก้ไขอีกครั้งได้เฉพาะผู้บริหาร</span>'}</div>`;
  } else { done.style.display = 'none'; done.innerHTML = ''; }
  const btn = document.getElementById('clComplete');
  btn.disabled = locked || prog.pct !== 100;
  btn.textContent = locked ? 'ปิดสมบูรณ์แล้ว' : (prog.pct === 100 ? 'ปิดสมบูรณ์ — ครบทุกขั้นตอน' : `ปิดสมบูรณ์ · เหลืออีก ${prog.total - prog.done}`);
  btn.title = locked ? 'ปิดสมบูรณ์แล้ว' : (prog.pct === 100 ? 'ครบทุกขั้นตอน กดเพื่อปิดสมบูรณ์' : 'ต้องติ๊กครบทุกขั้นตอนก่อน — กด "เฉพาะที่ยังค้าง" เพื่อดูรายการ');
  _clRenderNext(di, locked);
  _clRenderPhases(di, locked);
  _clDrawGantt(di);
  document.getElementById('clGanttWrap').style.display = _clGanttOpen ? '' : 'none';
  document.getElementById('clGanttHint').textContent = _clGanttOpen ? '(คลิกเพื่อซ่อน)' : '(คลิกเพื่อแสดง)';
  document.getElementById('clLog').innerHTML = log.length ? log.map(l => `<div><span style="color:var(--text-muted)">${_clEsc(_clFmt(l.at))}</span> · <b>${_clEsc(l.by || '')}</b> · ${_clEsc({ tick: 'ติ๊ก', untick: 'ยกเลิกติ๊ก', complete: 'ปิดสมบูรณ์', reopen: 'เปิดแก้ไขอีกครั้ง', start: 'ตั้งวันเริ่ม', note: 'หมายเหตุ' }[l.action] || (l.action || '').replace('status:', 'สถานะ → '))} · ${_clEsc(l.detail || l.item_id || '')}</div>`).join('') : '<span class="cl-empty">ยังไม่มีการเปลี่ยนแปลง</span>';
}

function _clRenderNext(di, locked) {
  const host = document.getElementById('clNext');
  if (locked) { host.innerHTML = '<h3 class="text-sm font-semibold">ทำต่อเลย</h3><p class="cl-empty mt-1">ปิดสมบูรณ์แล้ว ไม่มีงานค้าง</p>'; return; }
  const next = [];
  for (const ph of _clPlan.phases) { for (const tk of ph.tasks) { for (const s of tk.steps) { if (!_clStepDone(s.id)) next.push({ ph, tk, s }); if (next.length >= 4) break; } if (next.length >= 4) break; } if (next.length >= 4) break; }
  const blocked = [];
  _clPlan.phases.forEach(ph => ph.tasks.forEach(tk => { const ts = _clTaskState(tk); if (ts.status === 'blocked') blocked.push({ ph, tk, ts }); }));
  const waiting = _clPlan.phases.filter(ph => _clPhaseMode(ph, di) === 'waiting');
  host.innerHTML = `<div class="flex items-baseline justify-between flex-wrap gap-2"><h3 class="text-sm font-semibold">ทำต่อเลย</h3><span class="text-[11px]" style="color:var(--text-muted)">ขั้นตอนถัดไปตามลำดับแผน${next.length ? ` · เฟสปัจจุบัน ${_clEsc(next[0].ph.name.split(' · ')[0])}` : ''}</span></div>` +
    (next.length ? next.map(n => `<div class="cl-next-item"><span class="k">${_clEsc(n.ph.name.split(' · ')[0])}</span><span><b>${_clEsc(n.s.t)}</b><br><span class="k">${_clEsc(n.tk.name)} · ${_clEsc(n.ph.owner)}</span></span><button class="cl-ghost" onclick="clJump('${n.ph.id}','${n.s.id}')">ไปทำ →</button></div>`).join('') : '<p class="cl-empty mt-1">ติ๊กครบทุกขั้นตอนแล้ว — กดปิดสมบูรณ์ได้</p>') +
    (blocked.length ? `<p class="text-xs mt-2" style="color:#991b1b"><b>ติดปัญหา ${blocked.length} งาน:</b> ${blocked.map(b => `<a href="#" onclick="clJump('${b.ph.id}','${b.tk.steps[0] ? b.tk.steps[0].id : ''}');return false;" style="color:#991b1b;text-decoration:underline">${_clEsc(b.tk.name)}</a>${b.ts.note ? ' (' + _clEsc(b.ts.note.slice(0, 60)) + ')' : ''}`).join(' · ')}</p>` : '') +
    (waiting.length ? `<p class="text-xs mt-2" style="color:#92400e">รอฝ่ายภายนอกอนุมัติ: ${waiting.map(ph => _clEsc(_clShort(ph))).join(', ')} — ทำเฟสอื่นระหว่างรอ</p>` : '');
}

function _clRenderPhases(di, locked) {
  const host = document.getElementById('clPhases');
  const statuses = _clPlan.statuses || {};
  const phases = _clVisiblePhases();
  const onlyOpen = _clView === 'open' || _clHideDone;
  const curId = phases.length === 1 ? phases[0].id : '';
  if (curId && _clAutoOpened !== curId) {                     // expand the first open step of a freshly selected phase
    const first = phases[0].tasks.flatMap(tk => tk.steps).find(s => !_clStepDone(s.id));
    if (first) _clOpen.add(first.id);
    _clAutoOpened = curId;
  }
  host.innerHTML = phases.map(ph => {
    const ps = _clPhaseStats(ph), mode = _clPhaseMode(ph, di);
    const tasks = ph.tasks.filter(tk => !(onlyOpen && _clTaskState(tk).allDone));
    if (onlyOpen && !tasks.length) return '';
    return `<section class="card p-4 mt-4" id="cl-${ph.id}" style="border-top:3px solid ${ps.complete ? '#047857' : mode === 'blocked' ? '#b91c1c' : '#0f766e'}">
      <div class="cl-phase-head"><h3 class="text-base font-bold">${_clEsc(ph.name)}</h3><span class="text-sm font-bold" style="color:#0f766e">${ps.done}/${ps.total}</span>
        <span class="text-xs" style="color:var(--text-muted)">ผู้รับผิดชอบ: ${_clEsc(ph.owner)} · วันที่ ${ph.days[0] + 1}–${ph.days[1]} ของโครงการ${mode === 'waiting' ? ' · <b style="color:#92400e">อยู่ในช่วงรออนุมัติ</b>' : ''}</span></div>
      <p class="text-xs mt-1" style="color:var(--text-secondary)">${_clHl(ph.why)}</p>
      ${tasks.map(tk => {
        const ts = _clTaskState(tk);
        const steps = tk.steps.filter(s => !(onlyOpen && _clStepDone(s.id)));
        return `<article class="cl-task ${ts.status}" id="cltask-${tk.id}">
          <div class="cl-task-h"><h4>${_clEsc(tk.name)} <span class="text-xs font-normal" style="color:var(--text-muted)">· ${_clEsc(tk.est)} · ${ts.done}/${ts.total}</span></h4>
            <div class="cl-seg" role="group" aria-label="สถานะงาน">${Object.entries(statuses).map(([k, v]) => `<button type="button" class="${k}${ts.status === k ? ' on' : ''}" ${locked ? 'disabled' : ''} onclick="setChecklistTask('${tk.id}', {status: '${k}'})">${_clEsc(v)}</button>`).join('')}</div>
            <span class="text-[11px]" style="color:var(--text-muted)">${ts.at ? `${_clEsc(ts.by || '')} · ${_clEsc(_clFmt(ts.at))}` : ''}</span></div>
          <div class="cl-note"><textarea rows="1" ${locked ? 'disabled' : ''} placeholder="หมายเหตุ: ใครทำ ติดอะไร ค่าที่จด (ไม่ใส่ความลับ)" onblur="setChecklistTask('${tk.id}', {note: this.value})">${_clEsc(ts.note)}</textarea></div>
          ${steps.map(s => {
            const v = ((_clState.steps || {})[s.id] || {}), open = _clOpen.has(s.id);
            return `<div class="cl-step${v.done ? ' done' : ''}${open ? ' open' : ''}" id="clstep-${s.id}">
              <div class="cl-step-row" onclick="clToggleStep('${s.id}', event)">
                <input type="checkbox" id="clcb-${s.id}" ${v.done ? 'checked' : ''} ${locked ? 'disabled' : ''} onclick="event.stopPropagation()" onchange="setChecklistStep('${s.id}', this.checked)" aria-label="${_clEsc(s.t)}">
                <div><span class="t">${_clEsc(s.t)}</span><span class="by" id="clby-${s.id}">${v.done ? `✓ ${_clEsc(v.by || '')} · ${_clEsc(_clFmt(v.at))}` : ''}</span></div>
                <span class="chev">▶</span></div>
              <div class="cl-detail">
                <div>${s.how && s.how.length ? `<b class="l">ทำอย่างไร</b><ol>${s.how.map(h => `<li>${_clHl(h)}</li>`).join('')}</ol>` : ''}</div>
                <div class="cl-side">${s.why ? `<div class="cl-why"><b class="l">ทำไม</b>${_clHl(s.why)}</div>` : ''}${s.check ? `<div class="cl-check"><b class="l">สำเร็จเมื่อ</b>${_clHl(s.check)}</div>` : ''}${s.stuck ? `<div class="cl-stuck"><b class="l">ถ้าติด</b>${_clHl(s.stuck)}</div>` : ''}</div>
              </div></div>`; }).join('')}
        </article>`; }).join('')}
    </section>`; }).join('') || '<section class="card p-4 mt-4"><p class="cl-empty">ไม่มีรายการค้างในมุมมองนี้</p></section>';
}

/* ── interactions ── */
function clSelectPhase(id) { _clView = id; _clAutoOpened = ''; _clRender(); const el = document.getElementById('clNext'); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
function clToggleStep(id, ev) { if (ev && ev.target && ev.target.tagName === 'BUTTON') return; if (_clOpen.has(id)) _clOpen.delete(id); else _clOpen.add(id); const row = document.getElementById('clstep-' + id); if (row) row.classList.toggle('open', _clOpen.has(id)); }
function clJump(phaseId, stepId) {
  _clView = phaseId; _clAutoOpened = phaseId; if (stepId) _clOpen.add(stepId); _clRender();
  const row = document.getElementById('clstep-' + stepId) || document.getElementById('cl-' + phaseId);
  if (row) { row.scrollIntoView({ behavior: 'smooth', block: 'center' }); row.classList.add('hot'); setTimeout(() => row.classList.remove('hot'), 2500); }
}
function toggleChecklistDone() { _clHideDone = !_clHideDone; document.getElementById('clToggleDone').textContent = _clHideDone ? 'แสดงทั้งหมด' : 'ซ่อนที่เสร็จแล้ว'; document.getElementById('clToggleDone').classList.toggle('on', _clHideDone); _clRender(); }
function toggleChecklistGantt() { _clGanttOpen = !_clGanttOpen; _clRender(); }

async function _clAct(fn) {
  try { await fn(); _clState = await getJSON(`/api/checklist/${CL_NAME}`); _clRender(); }
  catch (e) { notifyError('บันทึกไม่สำเร็จ: ' + e.message); loadChecklist(true); }
}
function setChecklistStep(id, done) {
  const tk = _clPlan.phases.flatMap(ph => ph.tasks).find(t => t.steps.some(s => s.id === id));
  _clAct(async () => {
    await _clPost(`/api/checklist/${CL_NAME}/step/${id}`, _clBody({ done }));
    if (!tk) return;
    const ts = ((_clState.tasks || {})[tk.id] || {}).status || 'todo';
    const others = tk.steps.filter(s => s.id !== id).every(s => _clStepDone(s.id));
    if (done && others && ts !== 'done') await _clPost(`/api/checklist/${CL_NAME}/task/${tk.id}`, _clBody({ status: 'done' }));     // the last open step closes the task
    else if (!done && ts === 'done') await _clPost(`/api/checklist/${CL_NAME}/task/${tk.id}`, _clBody({ status: 'doing' }));
  });
}
function setChecklistTask(id, fields) {
  if (fields.note !== undefined) { const cur = ((_clState.tasks || {})[id] || {}).note || ''; if (fields.note === cur) return; }
  _clAct(() => _clPost(`/api/checklist/${CL_NAME}/task/${id}`, _clBody(fields)));
}
function setChecklistStart(v) { if (!v) return; _clAct(() => _clPost(`/api/checklist/${CL_NAME}/meta`, _clBody({ start: v }))); }

async function completeChecklist() {
  if (!confirm('ยืนยันปิดสมบูรณ์? ระบบจะตรวจว่าทุกขั้นตอนถูกติ๊กครบ แล้วล็อกรายการนี้ (ผู้บริหารเปิดแก้ไขอีกครั้งได้)')) return;
  try {
    const r = await _clPost(`/api/checklist/${CL_NAME}/complete`, _clBody({}));
    _clState = await getJSON(`/api/checklist/${CL_NAME}`); _clRender();
    if (typeof notifySuccess === 'function') notifySuccess(r.already ? 'ปิดสมบูรณ์อยู่แล้ว' : 'ปิดสมบูรณ์แล้ว');
  } catch (e) {
    const d = e.detail || {}, missing = d.missing || [];
    notifyError((d.message || e.message) + (missing.length ? ' — ' + missing.slice(0, 3).map(m => m.title).join(' · ') + (missing.length > 3 ? ' …' : '') : ''));
    if (missing.length) { _clView = 'open'; _clRender(); const row = document.getElementById('clstep-' + missing[0].id); if (row) { row.scrollIntoView({ behavior: 'smooth', block: 'center' }); row.classList.add('hot'); setTimeout(() => row.classList.remove('hot'), 4000); } }
  }
}
async function reopenChecklist() {
  const reason = prompt('เหตุผลที่เปิดแก้ไขอีกครั้ง (บันทึกในประวัติ)') || '';
  _clAct(() => _clPost(`/api/checklist/${CL_NAME}/reopen`, _clBody({ reason })));
}

/* ── Gantt ── */
function _clDrawGantt(di) {
  const host = document.getElementById('clGantt'); if (!host || !di.startIso) return;
  const HOR = _clPlan.horizon_days || 36, left = 300, dayW = 22, rowH = 34, top = 34, rows = _clPlan.phases.length;
  const W = left + HOR * dayW + 20, H = top + rows * rowH + 10;
  const addDays = n => { const d = new Date(di.start); d.setDate(d.getDate() + n); return d; };
  const th = d => d.toLocaleDateString('th-TH', { day: 'numeric', month: 'short' });
  const lbl = document.getElementById('clToday'); if (lbl) lbl.textContent = di.todayDay < 0 ? `วันนี้ ${th(di.today)} · โครงการเริ่ม ${th(di.start)} (อีก ${-di.todayDay} วัน)` : `วันนี้ ${th(di.today)} = วันที่ ${di.todayDay + 1} ของโครงการ (เริ่ม ${th(di.start)})`;
  let g = `<svg viewBox="0 0 ${W} ${H}" style="min-width:760px;width:100%;height:auto;display:block" role="img" aria-label="ไทม์ไลน์"><defs><pattern id="clhatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="3" height="6" fill="#b45309"/></pattern></defs>`;
  for (let d = 0; d <= HOR; d += 7) { const x = left + d * dayW; g += `<line x1="${x}" y1="${top - 6}" x2="${x}" y2="${H - 6}" stroke="var(--border)"/><text x="${x + 3}" y="${top - 12}" font-size="11" fill="var(--text-muted)">${th(addDays(d))}</text>`; }
  _clPlan.phases.forEach((ph, i) => {
    const y = top + i * rowH, ps = _clPhaseStats(ph), [a, b] = ph.days, x1 = left + a * dayW, w = Math.max(dayW * .6, (b - a) * dayW);
    const late = !ps.complete && di.todayDay > b, fill = ps.complete ? '#047857' : (ps.blocked ? '#b91c1c' : '#0f766e'), parts = ph.name.split(' · ');
    g += `<text x="8" y="${y + 21}" font-size="12.5" fill="var(--text-primary)" font-weight="600" style="cursor:pointer" onclick="clSelectPhase('${ph.id}')">${_clEsc(parts[0])}</text><text x="86" y="${y + 21}" font-size="12" fill="var(--text-secondary)"><title>${_clEsc(ph.name)}</title>${_clEsc(_clShort(ph))}</text>`;
    g += `<rect x="${x1}" y="${y + 7}" width="${w}" height="18" rx="4" fill="${fill}" opacity=".22"/><rect x="${x1}" y="${y + 7}" width="${w * ps.pct / 100}" height="18" rx="4" fill="${fill}"/>`;
    if (ph.wait) g += `<rect x="${left + ph.wait[0] * dayW}" y="${y + 7}" width="${(ph.wait[1] - ph.wait[0]) * dayW}" height="18" rx="4" fill="url(#clhatch)" opacity=".9"/>`;
    if (late) g += `<rect x="${x1 - 1}" y="${y + 6}" width="${w + 2}" height="20" rx="5" fill="none" stroke="#b45309" stroke-width="2"/>`;
    g += `<text x="${x1 + w + 6}" y="${y + 21}" font-size="11.5" fill="var(--text-secondary)">${ps.done}/${ps.total}${ps.blocked ? ' ⚠' : ''}</text>`;
  });
  if (di.todayDay >= 0 && di.todayDay <= HOR) { const tx = left + di.todayDay * dayW; g += `<line x1="${tx}" y1="${top - 8}" x2="${tx}" y2="${H - 6}" stroke="#b91c1c" stroke-width="1.5" stroke-dasharray="4 3"/><text x="${tx + 4}" y="${H - 8}" font-size="11" fill="#b91c1c">วันนี้</text>`; }
  host.innerHTML = g + '</svg>';
}
