// ─── 01-core.js — slice 1/9 of the former app.js (P2 structure refactor) ───
// Sequential load order matters: index.html loads 01→09; classic scripts
// share one global scope, so this equals the original single file.

// Stroke icons for icon-only buttons (24 grid, 1.75 stroke, the nav icons' style). Formal theme:
// no emoji as icons. Style: .ui-icon in docs/ui-kit/theme-formal.css.
const _svgIcon = d => `<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
const ICON = {
  search: _svgIcon('<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>'),
  moon: _svgIcon('<path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"/>'),
  sun: _svgIcon('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
  key: _svgIcon('<circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.7 12.3L21 2M16 7l3 3M18 5l2 2"/>'),
  trash: _svgIcon('<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6M10 11v6M14 11v6M9 6V4a1 1 0 011-1h4a1 1 0 011 1v2"/>'),
  save: _svgIcon('<path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>'),
  phone: _svgIcon('<rect x="6" y="2" width="12" height="20" rx="3"/><line x1="11" y1="18" x2="13" y2="18"/>'),
  print: _svgIcon('<polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 01-2-2v-5a2 2 0 012-2h16a2 2 0 012 2v5a2 2 0 01-2 2h-2"/><rect x="6" y="14" width="12" height="8"/>'),
  download: _svgIcon('<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>'),
};

// Oshinei Dashboard — application JS (split out of index.html,
// lean-overhaul Phase 4, 2026-07-20). Served at /app.js with
// Cache-Control: no-cache so deploys can never leave stale JS cached.
// This file IS the app: index.html holds markup/CSS only.
// ── Config & globals ───────────────────────────────────────────
// When opened as a local file (file://) point API calls at the running backend
const API = location.protocol === 'file:' ? 'http://localhost:8000' : '';

// ── Write-auth: attach the owner token to mutating /api/ calls ────────────────
// Hosted deploys gate every data-writing endpoint behind X-Upload-Token (the
// same token as upload/seed). Local dev leaves OSHINEI_SEED_TOKEN unset, so the
// server allows writes without a token and this stays invisible. /api/upload
// manages its own token (see handleUpload) and is excluded here. Read-only POSTs
// like /api/bom-cost/simulate aren't gated server-side, so they never prompt.
// ── In-page dialogs ───────────────────────────────────────────────────────────
// window.prompt/confirm are not usable in embedded browsers (the Claude app's
// browser pane throws on prompt() and answers confirm() with "cancel" without
// showing anything), so a save there failed or was silently cancelled. Every
// question the app asks goes through uiDialog instead:
//   await uiConfirm(message, {title, ok, cancel}) -> true / false
//   await uiPrompt(message, value, {list, placeholder, required})  -> text or null
// `list` = [{value, label}] offered as suggestions while typing (a datalist).
window.uiDialog = function (opt) {
  return new Promise(resolve => {
    const o = opt || {};
    const wrap = document.createElement('div');
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-modal', 'true');
    wrap.setAttribute('data-ui-dialog', '1');           // a page's own drawer asks for this mark before it answers Esc
    wrap.style.cssText = 'position:fixed;inset:0;z-index:10000;background:rgba(15,23,42,.45);display:flex;align-items:center;justify-content:center;padding:16px';
    const field = 'width:100%;box-sizing:border-box;border:1px solid var(--border-control, var(--border));border-radius:var(--r-sm, 8px);padding:8px 10px;font-size:14px;background:var(--bg-input, #fff);color:var(--text-primary)';
    const listId = 'uiDlgList' + Date.now();
    const btnH = (window.matchMedia && window.matchMedia('(max-width: 700px), (pointer: coarse)').matches) ? 44 : 0;      // 4.4: a phone's buttons are 44 px tall
    wrap.innerHTML = `<form style="width:100%;max-width:440px;max-height:90vh;overflow:auto;background:var(--bg-card, var(--bg-input, #fff));color:var(--text-primary);border:1px solid var(--border);border-radius:var(--r-md, 12px);padding:16px;box-shadow:0 10px 30px rgba(0,0,0,.25)">
        <div data-el="title" style="font-weight:600;font-size:15px;margin-bottom:6px"></div>
        <div data-el="msg" style="font-size:13px;color:var(--text-secondary);margin-bottom:10px;white-space:pre-line"></div>
        ${o.input ? `<input data-el="in" type="${o.input.password ? 'password' : 'text'}" autocomplete="off" spellcheck="false" ${o.input.list ? `list="${listId}"` : ''} style="${field}">
          ${o.input.list ? `<datalist id="${listId}"></datalist>` : ''}
          <div data-el="err" style="font-size:12px;color:var(--bad-ink);margin-top:4px"></div>` : ''}
        ${o.unlock ? `<label style="display:block;font-size:12px;color:var(--text-secondary);margin-top:${o.input ? 10 : 0}px;margin-bottom:4px">รหัสปลดล็อก (ครั้งเดียวต่อเบราว์เซอร์)</label>
          <input data-el="tok" type="password" autocomplete="off" spellcheck="false" placeholder="รหัสปลดล็อก" style="${field}">
          <div data-el="tokerr" style="font-size:12px;color:var(--bad-ink);margin-top:4px"></div>` : ''}
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
          <button type="button" data-act="cancel" style="border:1px solid var(--border);border-radius:var(--r-sm, 8px);padding:6px 14px;min-height:${btnH}px;font-size:${btnH ? 14 : 13}px;cursor:pointer;background:transparent;color:var(--text-primary)"></button>
          <button type="submit" style="border:0;border-radius:var(--r-sm, 8px);padding:6px 14px;min-height:${btnH}px;font-size:${btnH ? 14 : 13}px;cursor:pointer;background:var(--accent);color:var(--on-accent, #fff)"></button>
        </div></form>`;
    wrap.querySelector('[data-el="title"]').textContent = o.title || 'ยืนยันการดำเนินการ';
    wrap.querySelector('[data-el="msg"]').textContent = o.message || '';
    wrap.querySelector('[data-act="cancel"]').textContent = o.cancelText || 'ยกเลิก';
    wrap.querySelector('[type="submit"]').textContent = o.okText || 'ยืนยัน';
    if (o.noCancel) wrap.querySelector('[data-act="cancel"]').style.display = 'none';      // a notice: one button
    const input = wrap.querySelector('[data-el="in"]');
    if (input) {
      input.value = o.input.value || '';
      input.placeholder = o.input.placeholder || '';
      const dl = wrap.querySelector('datalist');
      if (dl) dl.innerHTML = o.input.list.map(x => `<option value="${String(x.value).replace(/"/g, '&quot;')}">${String(x.label || '').replace(/</g, '&lt;')}</option>`).join('');
    }
    const tokIn = wrap.querySelector('[data-el="tok"]');
    const done = v => { wrap.remove(); document.removeEventListener('keydown', onKey); resolve(v); };
    const onKey = e => { if (e.key === 'Escape') done(input ? null : false); };
    let busy = false;
    wrap.querySelector('form').addEventListener('submit', async e => {
      e.preventDefault();
      if (busy) return;
      const v = input ? input.value.trim() : true;
      if (input && o.input.required && !v) { wrap.querySelector('[data-el="err"]').textContent = 'ต้องกรอกข้อมูล'; input.focus(); return; }
      // U2: the unlock code sits in the same dialog as the question and is checked here, so a wrong code is
      // corrected in place and the save that follows goes through on the first press
      if (tokIn) {
        const tokErr = wrap.querySelector('[data-el="tokerr"]');
        const t = tokIn.value.trim();
        if (!t) { tokErr.textContent = 'กรอกรหัสปลดล็อก'; tokIn.focus(); return; }
        busy = true; tokErr.textContent = '';
        const ok = await window.checkWriteToken(t);
        busy = false;
        if (!ok) { tokErr.textContent = 'รหัสปลดล็อกไม่ถูกต้อง'; tokIn.select(); return; }
      }
      done(v);
    });
    wrap.querySelector('[data-act="cancel"]').addEventListener('click', () => done(input ? null : false));
    document.addEventListener('keydown', onKey);
    document.body.appendChild(wrap);
    setTimeout(() => (input || tokIn || wrap.querySelector('[type="submit"]')).focus(), 0);
  });
};
// uiConfirm(message, {title, ok, cancel}): a specific title and button labels per question (PR-M1);
// without them the dialog keeps the generic title and ยืนยัน / ยกเลิก (okText / cancelText still work).
// U2: a question before a save carries the unlock field itself while this browser holds no code and the
// role's writes need one (writesLocked), so it is one dialog and one press; {write:false} marks a
// question that saves nothing on the server (a preset kept in this browser).
window.uiConfirm = (message, opt) => {
  const o = Object.assign({ message }, opt || {});
  if (o.ok != null && o.okText == null) o.okText = o.ok;
  if (o.cancel != null && o.cancelText == null) o.cancelText = o.cancel;
  if (o.write !== false && window.writesLocked()) o.unlock = true;
  return window.uiDialog(o);
};
// uiNotice(message, {title, ok}): a refusal with a single button (PR-M4: "นับให้ครบก่อนส่ง" → กลับไปนับ)
window.uiNotice = (message, opt) => {
  const o = opt || {};
  return window.uiDialog({ message, title: o.title || 'แจ้งเตือน', okText: o.ok || 'ตกลง', noCancel: true });
};
window.uiPrompt = (message, value, opt) => {
  const o = opt || {};
  return window.uiDialog({ title: o.title || 'กรอกข้อมูล', message, okText: o.okText || 'ตกลง',
    unlock: o.write !== false && window.writesLocked(),
    input: { value: value == null ? '' : String(value), list: o.list, placeholder: o.placeholder, required: o.required } });
};
// ── PR-D6 · toasts for results, "?" for explanations, one filter for finished work ────────────────────────────
// uiToast(text, kind, {action:{label, onClick}, ms}): the ONE result message of the app (it replaces the grey status lines
// that stayed beside the button, notifyError / notifySuccess and the ขั้นตอนถัดไป toast). kind = success | info | warn | error.
// Bottom-right on a desktop, above the bottom navigation on a phone; success and info close after 4 s, a warning after 6 s, an
// error stays until it is closed; at most 3 are stacked; a tap closes one. role="status" (polite); an error is role="alert".
// Returns {el, close(), next(text, label, fn)}: next() adds the "ขั้นตอนถัดไป" line + button to the same toast (shmAfter).
(function () {
  const MAX = 3, MS = { success: 4000, info: 4000, warn: 6000, error: 0 }, IC = { success: '✓', info: 'i', warn: '!', error: '✕' };
  const KIND = { ok: 'success', good: 'success', bad: 'error', err: 'error', warning: 'warn' };
  function root() {
    let r = document.getElementById('uiToasts');
    if (!r) {
      r = document.createElement('div');
      r.id = 'uiToasts';
      r.setAttribute('aria-live', 'polite');
      document.body.appendChild(r);
    }
    return r;
  }
  // a phone: above the page's sticky action bar (ส่งใบสั่งซื้อ, ยืนยันใบตัดเบิก, ...), whatever its height, so a toast never covers a
  // button or the first input; without such a bar the stylesheet keeps it above the bottom navigation
  function place(r) {
    r.style.removeProperty('bottom');
    let phone = false;
    try { phone = window.matchMedia('(max-width:700px)').matches; } catch (e) { /* no matchMedia */ }
    if (!phone) return;
    const bars = [...document.querySelectorAll('.brn-sticky-bottom, .brn-rcv-bar, .ohb-actbar')].filter(b => {
      const q = b.getBoundingClientRect();
      return q.height > 0 && q.top < window.innerHeight && q.bottom > 0;
    });
    if (!bars.length) return;
    const top = Math.min(...bars.map(b => b.getBoundingClientRect().top));
    r.style.setProperty('bottom', Math.round(window.innerHeight - top + 8) + 'px', 'important');
  }
  window.uiToast = function (text, kind, opt) {
    const o = opt || {};
    const k = KIND[kind] || (MS[kind] != null ? kind : 'info');
    const t = String(text == null ? '' : text).replace(/^\s*(✓|⚠)\s*/, '').trim();
    const r = root();
    // the same message twice in a row (two status lines written together) is one toast
    const dup = [...r.children].find(c => c.dataset.kind === k && c.dataset.text === t);
    if (dup) { dup._arm(); return dup._handle; }
    while (r.children.length >= MAX) r.firstElementChild._close();
    const el = document.createElement('div');
    el.className = 'ui-toast ui-toast-' + k;
    el.dataset.kind = k; el.dataset.text = t;
    el.setAttribute('role', k === 'error' ? 'alert' : 'status');
    el.innerHTML = '<span class="ui-toast-ic" aria-hidden="true"></span><div class="ui-toast-body"><div class="ui-toast-text"></div><div class="ui-toast-next"></div></div>' +
      '<button type="button" class="ui-toast-x" aria-label="ปิด">×</button>';
    el.querySelector('.ui-toast-ic').textContent = IC[k];
    el.querySelector('.ui-toast-text').textContent = t;
    let timer = null;
    // 4 s; a long message (a count line, a reason from the server) gets up to 4 s more so it can be read to the end
    const ms = o.ms != null ? o.ms : (MS[k] ? MS[k] + Math.min(4000, Math.max(0, t.length - 60) * 50) : 0);
    const arm = n => { clearTimeout(timer); if (n) timer = setTimeout(() => el._close(), n); };
    el._close = () => { clearTimeout(timer); el.remove(); place(r); };
    el._arm = () => arm(ms);
    el.addEventListener('click', e => { if (!e.target.closest('.ui-toast-act')) el._close(); });      // a tap closes it
    el.addEventListener('mouseenter', () => clearTimeout(timer));
    el.addEventListener('mouseleave', () => arm(Math.min(ms, 2000)));
    const handle = {
      el, close: () => el._close(),
      next(text2, label, fn) {
        const n = el.querySelector('.ui-toast-next');
        n.innerHTML = '<span class="ui-toast-nt"></span> <button type="button" class="ui-toast-act"></button>';
        n.querySelector('.ui-toast-nt').textContent = text2 || '';
        const b = n.querySelector('.ui-toast-act');
        if (label && fn) { b.textContent = label; b.addEventListener('click', e => { e.stopPropagation(); el._close(); fn(); }); } else b.remove();
        arm(Math.max(ms, 9000));       // long enough to read the next step and reach the button
        return handle;
      },
    };
    el._handle = handle;
    if (o.action && o.action.label) handle.next('', o.action.label, o.action.onClick);
    r.appendChild(el);
    arm(ms);
    place(r);
    return handle;
  };
})();
// A reason for the popover: uiHelp(title, html) opens a dialog with the full text of an explanation ("?" button; Esc or ปิด closes it and the
// focus returns to the button that opened it; Tab stays inside). uiQ(key, title, html) registers an explanation made by a script and returns the
// "?" button; a fragment keeps its text in <template id=...> and the button carries data-help="id" (title from the template's data-title).
window.uiHelp = function (title, html, opener) {
  const from = opener || document.activeElement;
  const old = document.getElementById('uiHelpDlg');
  if (old) old.remove();
  const wrap = document.createElement('div');
  wrap.id = 'uiHelpDlg';
  wrap.className = 'ui-help-back';
  wrap.setAttribute('role', 'dialog');
  wrap.setAttribute('aria-modal', 'true');
  wrap.setAttribute('aria-labelledby', 'uiHelpTitle');
  wrap.innerHTML = '<div class="ui-help-box" tabindex="-1"><div class="ui-help-head"><div id="uiHelpTitle" class="ui-help-title"></div>' +
    '<button type="button" class="ui-help-x" aria-label="ปิด">×</button></div><div class="ui-help-body"></div></div>';
  wrap.querySelector('.ui-help-title').textContent = title || 'คำอธิบาย';
  wrap.querySelector('.ui-help-body').innerHTML = html || '';
  const done = () => { wrap.remove(); document.removeEventListener('keydown', onKey, true); try { if (from && from.focus && document.contains(from)) from.focus(); } catch (e) { /* the opener is gone */ } };
  const onKey = e => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(); return; }
    if (e.key !== 'Tab') return;
    const f = [...wrap.querySelectorAll('button, a[href], input, select, textarea, summary, [tabindex]:not([tabindex="-1"])')].filter(x => x.offsetParent !== null);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === wrap.querySelector('.ui-help-box'))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  wrap.addEventListener('click', e => { if (e.target === wrap || e.target.closest('.ui-help-x')) done(); });
  document.addEventListener('keydown', onKey, true);
  document.body.appendChild(wrap);
  setTimeout(() => { const x = wrap.querySelector('.ui-help-x'); if (x) x.focus(); }, 0);
  return done;
};
window._uiHelpReg = window._uiHelpReg || {};
// PL3: a value a script fills in may sit inside a help <template> (the explanation moved behind its "?"): find it on the page or in a template's content
function tplById(id) {
  const e = document.getElementById(id);
  if (e) return e;
  for (const t of document.querySelectorAll('template')) { const x = /** @type {HTMLTemplateElement} */ (t).content.getElementById(id); if (x) return x; }
  return null;
}
window.uiQ = function (key, title, html, cls) {
  if (html != null) window._uiHelpReg[key] = { title, html };
  const esc2 = s => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
  return `<button type="button" class="ui-q${cls ? ' ' + cls : ''}" aria-label="คำอธิบาย" aria-haspopup="dialog" data-help-key="${esc2(key)}" title="${esc2(title || 'คำอธิบาย')}">?</button>`;
};
// The "?" in the header of a supply page (data-help-tab="bomcenter | orderhub | branchstock") opens the explanation of the pane that is showing:
// <template id="hlp-<tab>-<pane>"> in the fragment, or an explanation a script registered under "<tab>:<pane>" (the roles of a pane read different
// text), or - for a pane not moved yet - its folded .bmc-help block.
const _UI_PANE_PREFIX = { bomcenter: 'bmc-pane-', orderhub: 'ohb-pane-', branchstock: 'brn-pane-' };
// PR-S4 · a help text may hold <span data-recipe>…</span> (full mode only) and <span data-stock>…</span> (stock mode only)
function _uiHelpMode(html) {
  const box = document.createElement('div'); box.innerHTML = String(html).replace(/\[\[HW\]\]/g, String(BC_HIST_WEEKS));   // [[HW]] = the weeks of history (BC_HIST_WEEKS)
  box.querySelectorAll(BC_MODE === 'stock' ? '[data-recipe]' : '[data-stock]').forEach(e => e.remove());
  return box.innerHTML;
}
window.uiPaneHelp = function (tab, opener) {
  const root = document.getElementById('tab-' + tab), pre = _UI_PANE_PREFIX[tab] || '';
  const pane = root && [...root.children].find(el => el.id && el.id.indexOf(pre) === 0 && el.style.display !== 'none');
  const name = pane ? pane.id.slice(pre.length) : '';
  const tpl = document.getElementById('hlp-' + tab + '-' + name), reg = window._uiHelpReg[tab + ':' + name];
  if (tpl) return window.uiHelp(tpl.getAttribute('data-title') || 'วิธีใช้หน้านี้', _uiHelpMode(tpl.innerHTML), opener);
  if (reg) return window.uiHelp(reg.title, reg.html, opener);
  const det = pane && pane.querySelector(':scope > details.bmc-help');
  if (det) { const d = det.querySelector(':scope > div'); return window.uiHelp('วิธีใช้หน้านี้', d ? d.innerHTML : '', opener); }
  window.uiToast('หน้านี้ยังไม่มีคำอธิบาย', 'info');
};
document.addEventListener('click', e => {
  const b = e.target.closest && e.target.closest('.ui-q');
  if (!b) return;
  e.preventDefault();
  const tplId = b.getAttribute('data-help'), key = b.getAttribute('data-help-key'), tabKey = b.getAttribute('data-help-tab');
  if (tabKey) window.uiPaneHelp(tabKey, b);
  else if (tplId) {
    const tpl = document.getElementById(tplId);
    if (tpl) window.uiHelp(tpl.getAttribute('data-title') || 'คำอธิบาย', tpl.innerHTML, b);
  } else if (key && window._uiHelpReg[key]) {
    window.uiHelp(window._uiHelpReg[key].title, window._uiHelpReg[key].html, b);
  }
});
// One filter for finished work (PR-TF's control, shared): every work list hides done / processed / posted / not-needed rows by default and
// shows them through the chips ยังไม่เสร็จ (default) · เสร็จแล้ว · ทั้งหมด, each with its count. The choice is remembered in the one key หน้างาน
// uses (a value a list does not offer, such as รอตรวจ, reads as ยังไม่เสร็จ there).
//   uiStateFilter.get(['todo','done','all'])      -> the chosen key
//   uiStateFilter.set(k)
//   uiStateFilter.bar(el, {counts, f, pick:'fnName', opts:[[key,label],...]})   renders the chips; pick is a global function name called with the key
//   uiStateFilter.empty(f, pick)                  -> the empty-state HTML of a list (one line + ดูทั้งหมด)
//   uiStateFilter.split(rows, isDone)             -> {todo, done, all, counts}
window.uiStateFilter = (function () {
  const KEY = 'oshinei_task_filter';
  const LABEL = { all: 'ทั้งหมด', todo: 'ยังไม่เสร็จ', review: 'รอตรวจ', done: 'เสร็จแล้ว' };
  const DEFAULT = [['todo', LABEL.todo], ['done', LABEL.done], ['all', LABEL.all]];
  let mem = null;     // the choice when localStorage is blocked
  function get(keys) {
    let v = mem;
    try { v = localStorage.getItem(KEY) || v; } catch (e) { /* blocked: the choice made on this page stands */ }
    const ok = (keys || DEFAULT.map(x => x[0]));
    return ok.includes(v) ? v : 'todo';
  }
  function set(f) { mem = f; try { localStorage.setItem(KEY, f); } catch (e) { /* lasts until the page is reloaded */ } }
  function bar(el, o) {
    const node = typeof el === 'string' ? document.getElementById(el) : el;
    if (!node) return;
    const opts = o.opts || DEFAULT;
    if (o.counts && o.counts.all === 0) { node.innerHTML = ''; return; }          // nothing in the list at all: no chips either
    node.classList.add('tskf-seg');
    node.setAttribute('role', 'group');
    if (!node.getAttribute('aria-label')) node.setAttribute('aria-label', 'กรองตามสถานะ');
    node.innerHTML = opts.map(([k, t]) => `<button type="button" class="tskf-btn" data-f="${k}" aria-pressed="${o.f === k}" onclick="${o.pick}('${k}')">${t} <span class="tskf-n">(${(o.counts || {})[k] || 0})</span></button>`).join('');
  }
  // the empty state of a list: one line + one button (noun = what the list holds, "งาน" on หน้างาน)
  function empty(f, pick, noun) {
    const n = noun || 'รายการ';
    return f === 'all' ? `<div class="text-xs py-2" style="color:var(--text-muted)">ไม่มี${n}</div>`
      : `<div class="text-xs py-2 shm-empty" style="color:var(--text-muted)">${f === 'done' ? `ยังไม่มี${n}ที่เสร็จแล้ว` : `ไม่มี${n}ค้าง`} <button type="button" class="shm-showall" onclick="${pick}('all')">ดูทั้งหมด</button></div>`;
  }
  function split(rows, isDone) {
    const todo = [], done = [];
    (rows || []).forEach(r => (isDone(r) ? done : todo).push(r));
    return { todo, done, all: (rows || []).slice(), counts: { todo: todo.length, done: done.length, all: (rows || []).length } };
  }
  return { KEY, LABEL, DEFAULT, get, set, bar, empty, split };
})();
// the owner token: several saves can hit 403 together and all wait for the one dialog
window.askWriteToken = function (message) {
  if (window._askTokenPending) return window._askTokenPending;
  return (window._askTokenPending = window.uiDialog({ title: 'กรอกรหัสปลดล็อก', message, okText: 'ปลดล็อก',
    input: { password: true, placeholder: 'รหัสปลดล็อก' } })
    .then(v => v || '').finally(() => { window._askTokenPending = null; }));
};

(function () {
  const TOK_KEY = 'oshinei_upload_token';
  const _origFetch = window.fetch.bind(window);
  const isWrite = m => /^(POST|PUT|DELETE|PATCH)$/i.test(m || 'GET');
  // When an authenticated session expires (12h), every /api/ call starts 401-ing.
  // Catch it once and re-open the login overlay instead of letting the SPA break
  // silently into half-loaded charts + a generic error toast.
  const observe = (url, res) => {
    if (res && res.status === 401 && url.includes('/api/') && !url.includes('/api/auth/')
        && window.AUTH && window.AUTH.enabled) {
      try { onSessionExpired(); } catch (e) {}
    }
    return res;
  };
  window.fetch = function (input, init) {
    init = init || {};
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const method = init.method || (typeof input !== 'string' && input && input.method) || 'GET';
    if (!isWrite(method) || !url.includes('/api/') || url.includes('/api/upload')) {
      // PL3: a read the server would refuse for this role is answered here as the same 403, without a request
      if (!isWrite(method) && url.includes('/api/') && typeof pathAllowedForMe === 'function') {
        let pth = ''; try { pth = new URL(url, location.href).pathname; } catch (e) { /* not a URL */ }
        if (pth && !pathAllowedForMe(pth)) {
          return Promise.resolve(new Response(JSON.stringify({ detail: 'ไม่มีสิทธิ์ใช้ส่วนนี้' }), { status: 403, headers: { 'Content-Type': 'application/json', 'X-Denied': 'role' } }));
        }
      }
      return _origFetch(input, init).then(r => observe(url, r));
    }
    const send = (tok) => {
      const h = new Headers(init.headers || (typeof input !== 'string' && input ? input.headers : null) || {});
      if (tok) h.set('X-Upload-Token', tok);
      return _origFetch(input, Object.assign({}, init, { headers: h }));
    };
    const stored = localStorage.getItem(TOK_KEY) || '';
    // a preview (dry_run=true) passes the gate without a token, so its success proves nothing
    const gatedCall = !/[?&]dry_run=(1|true|yes|on)(&|$)/i.test(url);
    return send(stored).then(async res => {
      // A 403 means one of two things and the server says which (X-Denied): a
      // role refusal is not a token problem — never ask, never wipe a good token.
      if (res.status !== 403 || res.headers.get('X-Denied') === 'role') {
        if (res.ok && stored && gatedCall && window.writeToken) _writeTokenState(true);
        return observe(url, res);
      }
      const tok = await window.askWriteToken('ต้องกรอกรหัสปลดล็อกก่อนบันทึก (ครั้งเดียวต่อเบราว์เซอร์)');
      if (!tok) return res;                       // cancelled → surface the 403
      localStorage.setItem(TOK_KEY, tok);
      return send(tok).then(res2 => {
        if (res2.status === 403 && res2.headers.get('X-Denied') !== 'role') { localStorage.removeItem(TOK_KEY); _writeTokenState(false); }  // wrong token
        else if (res2.status !== 403) _writeTokenState(true);
        return res2;
      });
    });
  };
  window._rawFetch = _origFetch;    // for checks that must not trigger the token dialog
})();

// ── ปลดล็อกการบันทึก: does THIS browser hold a token the server accepts? ──────
// The chip in the วัตถุดิบและใบสั่งซื้อ bar shows it, so the token is entered once,
// on purpose, instead of surprising someone mid-save. verifyWriteToken() asks
// POST /api/auth/token-check (writes nothing); the fetch wrapper above keeps the
// state current as saves succeed or fail. gated=false (local dev) hides the chip.
window.writeToken = { gated: null, unlocked: null };   // null = not checked yet
function _writeTokenState(unlocked) {
  window.writeToken.unlocked = unlocked;
  if (unlocked !== null) window.writeToken.gated = true;
  try { _renderLockChip(); } catch (e) { /* no bar on this page */ }
}
// U2: does this role's write need the code at all? /api/auth/me says (write_gated), so a browser with no
// code stored never calls token-check just to be refused (no 403 on every page load).
function _writeTokenFromAuth() {
  const a = window.AUTH || {};
  if (a.write_gated == null) return;
  if (!a.write_gated) { window.writeToken.gated = false; window.writeToken.unlocked = null; }
  else {
    window.writeToken.gated = true;
    let tok = ''; try { tok = localStorage.getItem('oshinei_upload_token') || ''; } catch (e) {}
    window.writeToken.unlocked = tok ? window.writeToken.unlocked : false;
    // RV-M: a code is stored but not checked yet - ask once (ฉัน and the lock chip said อ่านอย่างเดียว while saves went through)
    if (tok && window.writeToken.unlocked == null && typeof verifyWriteToken === 'function') verifyWriteToken();
  }
  try { _renderLockChip(); } catch (e) { /* no bar yet */ }
}
// true while a save would be refused for want of the code: the role's writes are gated and nothing is stored
window.writesLocked = function () {
  const st = window.writeToken || {};
  if (st.gated !== true) return false;
  try { return !localStorage.getItem('oshinei_upload_token'); } catch (e) { return true; }
};
// checks a code with POST /api/auth/token-check (writes nothing); a good one is stored and the chip turns แก้ไขได้
window.checkWriteToken = async function (tok) {
  tok = String(tok || '').trim();
  if (!tok) return false;
  try {
    const r = await window._rawFetch(`${API}/api/auth/token-check`, { method: 'POST', headers: { 'X-Upload-Token': tok } });
    if (!r.ok) return false;
    try { localStorage.setItem('oshinei_upload_token', tok); } catch (e) {}
    _writeTokenState(true);
    return true;
  } catch (e) { return false; }
};
async function verifyWriteToken() {
  const tok = localStorage.getItem('oshinei_upload_token') || '';
  if (!tok && window.writeToken.gated !== null) {        // nothing to check: the state is known from /api/auth/me
    if (window.writeToken.gated) _writeTokenState(false);
    return window.writeToken;
  }
  try {
    const r = await window._rawFetch(`${API}/api/auth/token-check`, { method: 'POST', headers: tok ? { 'X-Upload-Token': tok } : {} });
    if (r.status === 403) {
      if (tok && r.headers.get('X-Denied') === 'token') localStorage.removeItem('oshinei_upload_token');
      window.writeToken.gated = true; _writeTokenState(false);
    } else if (r.ok) {
      const j = await r.json();
      window.writeToken.gated = !!j.gated; _writeTokenState(j.gated ? true : null);
    }
  } catch (e) { /* offline: leave the state as it is */ }
  return window.writeToken;
}
async function unlockWrites() {
  const tok = await askWriteToken('ต้องกรอกรหัสปลดล็อกก่อนบันทึก (ครั้งเดียวต่อเบราว์เซอร์)');
  if (!tok) return;
  if (await window.checkWriteToken(tok)) uiToast('ปลดล็อกแล้ว บันทึกข้อมูลได้', 'success');
  else uiToast('รหัสปลดล็อกไม่ถูกต้อง', 'error');
}
// The same state as a lock icon beside the user name — the branch phone hides the
// chip bar (the bottom navigation takes its place), so the icon sits in the top bar; on the phone shell (PR-D3) the lock is a row of ฉัน.
function _renderLockIcon(st, on) {
  const top = document.getElementById('supplyLockTop');
  if (!top) return;
  if (st.gated === false) { top.innerHTML = ''; return; }
  const label = on ? 'แก้ไขได้' : 'อ่านอย่างเดียว';
  top.innerHTML = `<button onclick="unlockWrites()" aria-label="${label}" title="${label}${on ? '' : ' · กดเพื่อกรอกรหัสปลดล็อก'}" style="color:${on ? 'var(--good-ink)' : 'var(--amber-ink)'}"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="11" width="16" height="10" rx="2"/>${on ? '<path d="M8 11V7a4 4 0 017.6-1.8"/>' : '<path d="M8 11V7a4 4 0 018 0v4"/>'}</svg></button>`;
}
function _renderLockChip() {
  const st = window.writeToken || {};
  const on = st.unlocked === true;
  _renderLockIcon(st, on);
  const els = ['supplyLock', 'deptLock'].map(id => document.getElementById(id)).filter(Boolean);   // the bar (a phone) and the department header (a desktop)
  if (!els.length) return;
  if (st.gated === false) { els.forEach(el => { el.innerHTML = ''; }); return; }
  const html = `<button onclick="unlockWrites()" title="${on
      ? 'บันทึกข้อมูลได้ · กดเพื่อเปลี่ยนรหัสปลดล็อก'
      : 'ดูตัวอย่างได้ทุกหน้า · บันทึกจริงต้องกรอกรหัสปลดล็อก · กดเพื่อกรอก'}"
    class="text-[11px] rounded-full px-2.5 py-0.5" style="border:1px solid ${on ? 'var(--good-ink)' : 'var(--amber-ink)'};color:${on ? 'var(--good-ink)' : 'var(--amber-ink)'};background:transparent;white-space:nowrap">${on ? 'แก้ไขได้' : 'อ่านอย่างเดียว'}</button>`;
  els.forEach(el => { el.innerHTML = html; });
}

// Session-expiry handler — re-open the login overlay (guarded so a burst of
// concurrent 401s only prompts once).
let _sessionExpiredShown = false;
function onSessionExpired() {
  if (_sessionExpiredShown) return;
  _sessionExpiredShown = true;
  if (window.AUTH) window.AUTH.authenticated = false;
  const err = document.getElementById('loginError');
  if (err) { err.textContent = 'หมดเวลาเข้าสู่ระบบ · เข้าสู่ระบบอีกครั้ง'; err.style.display = 'block'; }
  if (typeof showLogin === 'function') showLogin();
}

// ── Role-based authentication (client side; server enforces too) ─────────────
// window.AUTH = { enabled, authenticated, username, role, modules[] }. When the
// server has OSHINEI_AUTH off, /api/auth/me reports enabled:false + all modules,
// so nothing below hides anything (current behaviour preserved).
window.AUTH = { enabled: false, authenticated: true, role: 'management', modules: [] };

// PR-S1 · the system mode (app_settings bc_mode, GET /api/bom-center/mode): 'stock' hides every recipe page, task and reminder, 'full' is the
// whole app. Loaded once after sign-in (loadBcMode), before any menu is drawn; 'stock' is also the answer when the server cannot be asked.
let BC_MODE = 'stock';
let BC_HIST_WEEKS = 8;          // PL1 #15: the weeks of history behind every "ปกติ" / suggestion; the server's forecast.HISTORY_WEEKS arrives with the mode
async function loadBcMode() {
  try {
    const r = await fetch(`${API}/api/bom-center/mode`);
    const j = r.ok ? await r.json() : null;
    BC_MODE = j && j.mode === 'full' ? 'full' : 'stock';
    if (j && j.history_weeks > 0) BC_HIST_WEEKS = j.history_weeks;
  } catch (e) { BC_MODE = 'stock'; }
  if (BC_MODE === 'stock') { try { stkWordsStart(); } catch (e) { /* wording only */ } }
  return BC_MODE;
}

// ── PL3 stock words ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
// RV3 F7 (owner 2026-10-23): in stock mode the analytics pages (cost, GP, P&L, cost per head, coverage, the home and overall insights,
// the management report) say "ต้นทุนต่อจาน" / dish cost, never BoM or recipe. The cost figures themselves are the same in both modes; only
// the words change, and only while BC_MODE is 'stock' (full mode keeps its own words). The words come from many slices and from the server
// (the insight sentences), so one rewrite runs on the text of every analytics page as it is drawn (a MutationObserver) and on every chart
// label (mkChart). The supply, franchise, manual and settings pages are not touched. Rules run in order; the first ones are the specific
// phrases. test_pl3_stock_words.py runs this list over the text of the pages and fails on a BoM or recipe word left.
/** @type {Array<[RegExp, any]>} */
const _STK_WORDS = [
  [/BoM\s*\(ต้นทุน\)/g, 'ต้นทุน'],
  [/\s*\((?:COGS\s*·\s*)?BoM[^)]*\)/g, ''],                          // "(BoM)", "(BoM จริง)", "(COGS·BoM)"
  [/\s*\(BoM basis\)/gi, ''],
  [/BoM recipe food cost/gi, 'dish food cost'],
  [/BoM basis/gi, 'dish-cost basis'],
  [/BoM[ -]cost/gi, 'dish cost'],
  [/\s*·\s*BoM(?=\s*·|\s*$|\s*\))/g, ''],                              // "56% ของรายได้ · BoM · MTD"
  [/ต้นทุน\s*BoM\s*(พุ่ง|ลด)/g, 'ต้นทุนอาหาร$1'],
  [/(?:ใช้|ตาม|ด้วย|จาก)สูตร\s*BoM(?:\s*(?:ใหม่|เดียว|จริง))?/g, m => m.replace(/สูตร\s*BoM(?:\s*(?:ใหม่|เดียว|จริง))?/, 'ต้นทุนต่อจาน')],
  [/(?:มี|ครอบคลุม|เพิ่ม|ไม่มี|ไม่พบ)\s*(?:สูตร\s*)?BoM(?:\s*recipe)?/g, m => m.replace(/(?:สูตร\s*)?BoM(?:\s*recipe)?/, 'ต้นทุนต่อจาน')],
  [/ต้นทุน\s*\(BoM\)/g, 'ต้นทุน'],
  [/ต้นทุน\s*BoM\s*(?=[฀-๿])/g, 'ต้นทุน'],                    // "ต้นทุน BoM รวม" -> "ต้นทุนรวม"
  [/ต้นทุน\s*BoM/g, 'ต้นทุน'],
  [/ข้อมูล\s*BoM(?:\s*cost)?/g, 'ข้อมูลต้นทุนต่อจาน'],
  [/สูตร\s*\(BoM\)/g, 'ต้นทุนต่อจาน'],
  [/สูตร\s*BoM/g, 'ต้นทุนต่อจาน'],
  [/BoM\s*COGS/g, 'ต้นทุนขาย'],
  [/BoM\s*Center/g, 'ต้นทุนต่อจาน'],
  [/ที่ประกาศใช้/g, 'ที่ใช้'],
  [/สูตรเสนอ/g, 'ต้นทุนเสนอ'],
  [/(?:เปลี่ยน|ปรับ)สูตร\s*\/\s*(?:ลด\s*)?portion/g, 'ลดปริมาณเสิร์ฟ'],
  [/(?:เปลี่ยน|ปรับ)สูตร/g, 'ปรับเมนู'],
  [/ตรวจสูตร/g, 'ตรวจเมนู'],
  [/(?:ตาม|ใช้เกิน|เบิกเกิน|ตักเกิน|เกิน|น้อยกว่า)สูตร/g, m => m.replace('สูตร', 'ค่าที่คาด')],
  [/ไม่มีสูตร|ไม่อยู่ในสูตรใด/g, 'ไม่มีต้นทุนต่อจาน'],
  [/ทุกสูตร/g, 'ทุกเมนู'],
  [/สูตรมาตรฐานระบบ/g, 'ต้นทุนมาตรฐาน'],
  [/สูตรของเมนูใกล้เคียง/g, 'เมนูใกล้เคียง'],
  [/คีย์สูตร/g, 'คีย์ต้นทุน'],
  [/\s*\(สูตร\)/g, ''],
  [/\bvs\s*สูตร/g, 'vs ปกติ'],
  [/\bBoM\b/g, 'ต้นทุนต่อจาน'],
  [/สูตร/g, 'ต้นทุนต่อจาน'],                                           // whatever is left: the recipe is not a word of the stock mode
];
function stkText(s) {
  if (BC_MODE !== 'stock' || typeof s !== 'string' || (s.indexOf('BoM') < 0 && s.indexOf('สูตร') < 0)) return s;
  let o = s;
  for (const [re, to] of _STK_WORDS) o = o.replace(re, to);
  return o;
}
const _STK_SKIP = '#tab-bomcenter, #tab-orderhub, #tab-branchstock, #tab-supplyhome, #tab-ordering, #tab-ordersetup, #tab-ingpurchase, #tab-franchise, #tab-fmsg, #tab-manual, #tab-settings, #tab-checklist';
function _stkSkipped(el) { return !!(el && el.closest && el.closest(_STK_SKIP) && !el.closest('#bmc-pane-dishcost')); }   // the dish-cost page (ต้นทุนต่อจาน) sits in the supply centre but is an analytics page
let _stkObs = null;
function _stkNode(root) {
  if (!root || BC_MODE !== 'stock') return;
  const base = root.nodeType === 3 ? root.parentElement : root;
  if (!base || _stkSkipped(base)) return;
  if (root.nodeType === 3) {
    const t = stkText(root.nodeValue); if (t !== root.nodeValue) root.nodeValue = t;
    return;
  }
  if (root.nodeType !== 1 || /^(SCRIPT|STYLE|TEXTAREA|CANVAS)$/.test(root.tagName)) return;
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    const p = n.parentElement; if (!p || /^(SCRIPT|STYLE|TEXTAREA)$/.test(p.tagName) || _stkSkipped(p)) continue;
    const t = stkText(n.nodeValue); if (t !== n.nodeValue) n.nodeValue = t;
  }
  const els = root.querySelectorAll ? root.querySelectorAll('[title],[placeholder],[aria-label]') : [];
  els.forEach(e => {
    if (_stkSkipped(e)) return;
    ['title', 'placeholder', 'aria-label'].forEach(a => { const v = e.getAttribute(a); if (v) { const t = stkText(v); if (t !== v) e.setAttribute(a, t); } });
  });
}
function stkWordsStart() {
  if (_stkObs || typeof MutationObserver === 'undefined' || !document.body) return;
  _stkObs = new MutationObserver(muts => {
    for (const m of muts) {
      if (m.type === 'characterData') _stkNode(m.target);
      else m.addedNodes.forEach(n => _stkNode(n));
    }
  });
  _stkObs.observe(document.body, { childList: true, subtree: true, characterData: true });
  _stkNode(document.body);
}
// a chart's own words (dataset names, titles, axis titles) are canvas text, not DOM: the same rewrite, once, as the chart is made
function stkChartWords(config) {
  if (BC_MODE !== 'stock' || !config) return;
  try {
    ((config.data && config.data.datasets) || []).forEach(d => { if (typeof d.label === 'string') d.label = stkText(d.label); });
    const o = config.options || {}, pl = o.plugins || {};
    if (pl.title && typeof pl.title.text === 'string') pl.title.text = stkText(pl.title.text);
    Object.values(o.scales || {}).forEach(sc => { if (sc && sc.title && typeof sc.title.text === 'string') sc.title.text = stkText(sc.title.text); });
    if (config.data && Array.isArray(config.data.labels)) config.data.labels = config.data.labels.map(l => typeof l === 'string' ? stkText(l) : l);
  } catch (e) { /* wording only */ }
}
// ── end PL3 stock words

// Sidebar tab → module it requires. Tabs absent from the map (e.g. 'manual') are
// always allowed to any logged-in user. Mirrors MODULE_RULES in backend/auth.py.
const TAB_MODULE = {
  home:'sales.overview', overall:'sales.overview', action:'sales.overview',
  dailysales:'sales.overview', trend:'sales.overview', compare:'sales.overview', more:'sales.overview',
  buffetperf:'customer.buffet', childbuffet:'customer.buffet', guest:'customer.buffet', timeflow:'customer.buffet',
  menu:'menu.analysis', engineering:'menu.analysis', menucompare:'menu.analysis', menuefficiency:'cost.analysis',
  menuactionboard:'cost.analysis', menuopportunities:'menu.analysis', feedback:'customer.feedback', checklist:'customer.feedback',
  cost:'cost.analysis', bomcost:'cost.analysis', costhead:'cost.analysis', costheadtop60:'cost.analysis',
  hvmonitor:['cost.analysis','profit.pnl'],   // High Value Product Monitor (owner 2026-10-25): /api/cost/ingredient-groups is cost.analysis / profit.pnl
  menuimpact:'cost.analysis',
  heavyeater:'cost.analysis', coverage:'cost.analysis', branchbom:'cost.analysis',
  simhub:'cost.simulator',
  gpbranch:'profit.pnl', pnlbranch:'profit.pnl', pnlbill:'profit.pnl', profit:'profit.pnl', benchmark:'profit.pnl',
  pnl:'profit.pnl',   // P&L เชิงลึก - back in the กำไร bar 2026-10-01; /api/pnl is profit.pnl on the server too
  opsreport:'ops.report', ops:'ops.report',
  // จัดซื้อ/วัตถุดิบ: any ONE of the listed modules opens the tab (supply.ordering = Sales Support)
  ordering:['ops.report','supply.ordering'], ordersetup:['ops.report','supply.ordering'],
  ingpurchase:['cost.analysis','supply.ordering'],   // ยอดซื้อวัตถุดิบ vs ยอดขาย — เป็นตัวเลขต้นทุน
  bomcenter:['cost.analysis','supply.ordering','bom.center','bom.edit','bom.release','bom.audit','bom.prices','bom.recipes'],
  branchstock:['branch.stock','supply.ordering','bom.center','ops.report'],   // Phase 2: สาขาของฉัน — นับสต๊อก
  orderhub:['supply.ordering','ops.report','bom.center','supply.warehouse'],   // Phase 2 S9: ศูนย์สั่งของ — คิว Sales Support · compliance · สรุปเดือน   // BoM Center (S1): ทะเบียน ราคา หน่วย
  supplyhome:['branch.stock','supply.ordering','bom.center','bom.edit','bom.release','bom.audit','ops.report','cost.analysis','bom.prices','bom.recipes','supply.warehouse'],   // หน้างาน: any supply role (the server rule for /api/bom-center/todo lists the same)
  franchise:'franchise.view',   // ร้านของฉัน (PR-L): the franchise owner's page; the nav entry shows for that role only (applyPermissions)
  fmsg:'franchise.inbox',       // PR-F4: ข้อความจากเจ้าของสาขา - head office's inbox of the owners' questions (management + operation)
  staff:'ops.staff', discounts:'ops.discounts',
  cashctl:'ops.cash',
  custtype:'ops.posextra', modifiers:'ops.posextra', pricezone:'ops.posextra',
  cancelbill:'ops.posextra', closetime:'ops.posextra',
  report:'report.export', mgtreport:'report.export', settings:'system.settings',
};
// ── First-login start card: the three tabs each role should open first ──────
// Shown once, on the first login that has never opened a tab (/api/usage/me →
// first_time), on top of the home page; "เข้าใจแล้ว" dismisses it for good.
const ROLE_START = {
  management: [['home', 'ภาพรวมวันนี้ สัญญาณที่ต้องดู และคิวงานของวัน'], ['mgtreport', 'รายงานผู้บริหาร รายวัน / สัปดาห์ / เดือน พร้อม export'], ['gpbranch', 'กำไรขั้นต้นรายสาขา เทียบกันได้ทันที']],
  accounting: [['bomcenter', 'ดาวน์โหลดใบสรุปตัดเบิกรายสาขาหลังตรวจนับปลายงวด เพื่อบันทึกเข้าระบบบัญชี', 'issue'], ['discounts', 'ส่วนลด การรั่วไหล และ ฿ ที่เกินเพดานนโยบาย'], ['mgtreport', 'ตัวเลขสรุปสำหรับปิดงวด พร้อม Excel']],
  operation:  [['opsreport', 'ปฏิบัติการรายวัน: ชำระเงิน void โต๊ะ'], ['orderhub', 'ตรวจว่าสาขาสั่งตามยอดแนะนำ: แนะนำ · สาขายืนยัน · ส่งจริง · รับจริง', 'comp'], ['discounts', 'ส่วนลดของสาขาเทียบเพดาน']],
  marketing:  [['feedback', 'เสียงลูกค้าจากทุกช่องทาง + งานที่ต้องตอบ'], ['menu', 'เมนูขายดี / ขายไม่ออก'], ['buffetperf', 'ผลงานบุฟเฟ่ต์และยอดต่อหัว']],
  analyst:    [['overall', 'KPI ภาพรวมและ heatmap รายสาขา'], ['supplyhome', 'งานวันนี้และสถานะปิดงวดเดือน'], ['cost', 'ต้นทุนและ food cost']],
  innovation: [['menuactionboard', 'เมนูบุฟเฟ่ต์ที่ดันต้นทุนต่อหัว'], ['bomcenter', 'สร้างสูตรเดือนถัดไปจากแม่แบบ แล้วส่งตรวจ', 'rec'], ['menu', 'วิเคราะห์เมนูเชิงลึก']],
  salessupport: [['orderhub', 'ติดตามใบตัดเบิกที่สาขายังไม่ยืนยันหรือยืนยันล่าช้า', 'issues'], ['orderhub', 'พิจารณาเคลมที่สาขาแจ้ง', 'claims'], ['orderhub', 'คีย์ใบสั่งซื้อเข้าระบบบัญชี', 'queue'], ['bomcenter', 'คำนวณและบันทึกยอดแนะนำให้สาขา', 'fc'], ['bomcenter', 'กำหนดวันส่งของรอบ 1 รอบ 2 และแซลมอนสดของแต่ละสาขา', 'cal']],
  warehouse: [['orderhub', 'ระบุยอดส่งจริงภายใน 14:00 ของวันตัดรอบ แล้วกด “ยืนยันยอดส่ง”', 'queue']],
  branch: [['branchstock', 'บันทึกใบตัดเบิก (เบิกใช้และของเสีย) และส่งทุกวัน', 'issue'], ['branchstock', 'ส่งใบสั่งซื้อก่อน 13:15 สองวันก่อนวันส่งของ', 'order'], ['branchstock', 'บันทึกของที่รับเข้าร้านทุกครั้ง รวมของที่สาขาซื้อเอง', 'recv']],
  franchise: [['franchise', 'ผลงานเดือนนี้ของสาขา: ยอดขาย กำไรขั้นต้น วัตถุดิบ วินัยการทำงาน']],
};
function _startKey() { return 'startCard:' + ((window.AUTH && window.AUTH.username) || 'anon'); }
// An entry may name a pane of the วัตถุดิบและใบสั่งซื้อ module ([tab, why, pane]):
// it opens through supplyGo and is titled by the department that holds it (PR-D5: "<แผนก> → <หน้า>", the person's own department first), not by PAGE_TITLES.
function _startGo(t, pane) { return pane ? `supplyGo({tab:'${t}',pane:'${pane}'})` : `switchTab('${t}')`; }
function _startTitle(t, pane) {
  const dept = pane && typeof deptVisible === 'function' ? deptVisible().find(r => r.pages.some(x => x.p.tab === t && x.p.pane === pane)) : null;
  if (dept) return `${dept.dept.label} → ${dept.pages.find(x => x.p.tab === t && x.p.pane === pane).p.label}`;
  const hit = pane ? _supplyItemOf(t, pane) : null;
  return hit ? `${hit.chip.label} · ${hit.item.label}` : (PAGE_TITLES[t] || t);
}
// PR-S1: the start entries a role sees in the current mode - a recipe page is dropped in stock mode; the kitchen's recipe entry becomes the register
const _START_KITCHEN_STOCK = ['bomcenter', 'ดูทะเบียนวัตถุดิบ (อ่านอย่างเดียว ระหว่างขึ้นระบบสต๊อก)', 'ing'];
function _roleStart(role) {
  const list = ROLE_START[role] || ROLE_START.management;
  if (role === 'innovation') return list.map(e => bomHidden(e[0], e[2]) ? _START_KITCHEN_STOCK : e);
  return list.filter(e => !bomHidden(e[0], e[2]));
}
function showStartCard(role) {
  const items = _roleStart(role).filter(([t]) => tabAllowed(t)).slice(0, 5);   // every entry of a role shows (ประสานงานขาย has 5)
  // The branch role has no home page: the card sits on top of the page it landed on.
  // PR-D1: on a desktop the landing page is the launcher, so the card sits on it
  const host = document.getElementById(_launcherLanding() ? 'tab-launcher' : (tabAllowed('home') ? 'tab-home' : 'tab-' + activeTab)); if (!host || !items.length) return;
  let card = document.getElementById('homeStartCard');
  if (!card) { card = document.createElement('div'); card.id = 'homeStartCard'; host.insertBefore(card, host.firstChild); }
  card.className = 'card p-5 mb-5'; card.style.borderLeft = '4px solid var(--indigo)';
  card.innerHTML = `<div class="flex items-start justify-between gap-3"><div>
      <div class="text-base font-bold" style="color:var(--text-primary)">เริ่มต้นใช้งาน — ${esc(ROLE_LABEL_TH[role] || role)}</div>
      <div class="text-xs mt-1" style="color:var(--text-muted)">หน้าที่ใช้บ่อย · การ์ดนี้แสดงจนกว่าจะกด "เข้าใจแล้ว"</div></div>
      <button onclick="dismissStartCard()" class="text-xs px-3 py-1 rounded-lg whitespace-nowrap" style="border:1px solid var(--border);color:var(--text-secondary)">เข้าใจแล้ว ✕</button></div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-3 mt-4">${items.map(([t, why, pane], i) => `<button onclick="${_startGo(t, pane)}" class="text-left p-3 rounded-xl transition-colors hover:ring-2 hover:ring-indigo-200" style="background:var(--bg-input);border:1px solid var(--border)">
      <div class="text-[11px] font-semibold" style="color:var(--indigo)">ขั้นที่ ${i + 1}</div><div class="font-semibold text-sm mt-0.5" style="color:var(--text-primary)">${esc(_startTitle(t, pane))}</div><div class="text-xs mt-1" style="color:var(--text-muted)">${esc(why)}</div></button>`).join('')}</div>
    <div class="text-xs mt-3" style="color:var(--text-muted)">คู่มือฉบับเต็ม: <a href="#" onclick="switchTab('manual');return false" class="underline">คู่มือการใช้งาน</a></div>`;
  card.hidden = false;
}
function dismissStartCard() {
  try { localStorage.setItem(_startKey(), '1'); } catch(e) {}
  const c = document.getElementById('homeStartCard'); if (c) c.remove();
}
async function maybeShowStartCard() {
  if (!window.AUTH || !window.AUTH.enabled || !window.AUTH.authenticated) return;
  if (franchiseLogin()) return;                 // PR-F1: one page, opened already - no card of steps on top of it
  try { if (localStorage.getItem(_startKey())) return; } catch(e) {}
  let me = null;
  try { me = await api(`${API}/api/usage/me`); } catch(e) { return; }
  if (me && me.first_time) { if (_launcherLanding()) switchTab('launcher'); else if (tabAllowed('home')) switchTab('home'); showStartCard(window.AUTH.role || me.role); }
}

const ROLE_LABEL_TH = {
  management:'ผู้บริหาร', marketing:'การตลาด', operation:'ฝ่ายปฏิบัติการ',
  analyst:'ฝ่ายวิเคราะห์', accounting:'บัญชี', innovation:'ครัว',
  salessupport:'ประสานงานขาย',
  warehouse:'คลัง',
  branch:'สาขา',
  franchise:'เจ้าของสาขา (ดูผลการดำเนินงานของสาขาตนเอง)',
};
// Display-only mirror of backend ROLE_MODULES (auth.py) for the admin cheatsheet
// so the owner can see what each role unlocks before assigning it. Only a FALLBACK:
// the matrix loads the live (owner-customised) permissions from /api/auth/role-permissions;
// test_role_modules_mirror.py keeps this copy equal to auth.ROLE_MODULES.
const MODULE_LABEL_TH = {
  'sales.overview':'ยอดขายภาพรวม', 'customer.buffet':'บุฟเฟ่ต์/ลูกค้า',
  'menu.analysis':'วิเคราะห์เมนู', 'cost.analysis':'ต้นทุน', 'cost.simulator':'ตัวจำลองต้นทุน',
  'bom.edit':'แก้ไขสูตร (ระบบเดิม)', 'profit.pnl':'กำไร/P&L', 'ops.report':'รายงานปฏิบัติการ',
  'ops.discounts':'ส่วนลด', 'ops.cash':'เงินขาด/เกิน', 'ops.staff':'พนักงาน',
  'ops.posextra':'POS เชิงลึก', 'report.export':'ส่งออกรายงาน',
  'system.settings':'ตั้งค่า', 'system.admin':'จัดการผู้ใช้', 'data.upload':'อัปโหลดข้อมูล',
  'supply.ordering':'ประสานงานขาย: ใบสั่งซื้อ เคลม ใบตัดเบิก', 'customer.feedback':'เสียงลูกค้า',
  'bom.center':'ข้อมูลตั้งต้นและสูตร', 'bom.release':'สูตร: ประกาศใช้', 'bom.audit':'สูตร: รับทราบ/ยกเลิกใช้',
  'bom.prices':'ราคารายเดือน', 'bom.recipes':'สูตร: แก้ไข',
  'branch.stock':'สาขา: ตัดเบิก นับของ และสั่งของ',
  'supply.warehouse':'คลัง: ระบุยอดส่งจริง',
  'franchise.view':'ร้านของฉัน: ดูผลการดำเนินงานของสาขาตนเอง',
  'franchise.inbox':'ข้อความจากเจ้าของสาขา: อ่านและตอบ',
};
const ROLE_MODULES_TH = {
  management: Object.keys(MODULE_LABEL_TH),
  marketing:  ['sales.overview','customer.buffet','menu.analysis','report.export','customer.feedback'],
  operation:  ['sales.overview','ops.report','ops.discounts','ops.cash','ops.staff','ops.posextra','report.export','customer.feedback','franchise.inbox'],
  analyst:    ['sales.overview','customer.buffet','menu.analysis','cost.analysis','cost.simulator','profit.pnl','ops.discounts','ops.posextra','report.export','customer.feedback','bom.center','bom.release'],
  accounting: ['sales.overview','profit.pnl','cost.analysis','ops.cash','ops.discounts','report.export'],
  innovation: ['sales.overview','menu.analysis','cost.analysis','cost.simulator','bom.edit','system.settings','customer.feedback','bom.recipes'],
  salessupport: ['sales.overview','supply.ordering','report.export'],
  warehouse: ['supply.warehouse'],
  branch: ['branch.stock'],
  franchise: ['franchise.view'],
};

// PL3: the server's path rules (/api/auth/me "rules", the same MODULE_RULES list auth.path_allowed reads), copied here so that a page
// never asks for an answer the server will refuse - the browser would log it as a console error and the section would stay empty.
// Longest prefix wins; a path no rule names is open to a role that holds sales.overview (or to the open prefixes).
let _AUTH_RULES = null, _AUTH_OPEN = [];
function _authRulesFrom(j) {
  if (j && Array.isArray(j.rules)) { _AUTH_RULES = j.rules; _AUTH_OPEN = j.open_to_all || []; }
}
function pathAllowedForMe(path) {
  if (!window.AUTH || !window.AUTH.enabled || !_AUTH_RULES) return true;
  const mods = new Set(window.AUTH.modules || []);
  let best = -1, allowed = new Set();
  for (const [pfx, ms] of _AUTH_RULES) {
    if (!path.startsWith(pfx)) continue;
    if (pfx.length > best) { best = pfx.length; allowed = new Set(ms); } else if (pfx.length === best) ms.forEach(x => allowed.add(x));
  }
  if (best < 0) return mods.has('sales.overview') || _AUTH_OPEN.some(p => path.startsWith(p));
  for (const m of allowed) if (mods.has(m)) return true;
  return false;
}
function hasModule(m) {
  if (!window.AUTH || !window.AUTH.enabled) return true;   // auth off → everything
  return (window.AUTH.modules || []).includes(m);
}
// PL3: may this role see cost of goods, gross profit and food-cost figures? (the server removes them from the payload of a role that may not)
function canSeeCost() { return hasModule('cost.analysis') || hasModule('profit.pnl'); }
// PL3: a card marked data-needs="/api/path[,/api/other]" is shown only when the role may read that data (the same rule the server applies),
// so a page never shows an empty card for data its role cannot load. Called when a page opens and after sign-in.
function applyNeeds(root) {
  if (!document.getElementById('needsHiddenCss')) {
    const st = document.createElement('style'); st.id = 'needsHiddenCss'; st.textContent = '.needs-hidden{display:none!important}';
    document.head.appendChild(st);
  }
  (root || document).querySelectorAll('[data-needs]').forEach(el => {
    el.classList.toggle('needs-hidden', !el.dataset.needs.split(',').some(p => pathAllowedForMe(p.trim())));
  });
}
function tabAllowed(name) {
  if (bomHidden(name)) return false;                        // PR-S1: a whole recipe page (bomcost, branchbom, menuimpact) in stock mode
  const m = TAB_MODULE[name];
  if (Array.isArray(m)) return m.some(hasModule);           // any one of them opens it
  return !m || hasModule(m);                                // unmapped → always allowed
}
function firstAllowedTab() {
  const order = ['overall','home','action','dailysales','trend','compare','more',
    'menu','buffetperf','gpbranch','cost','simhub','opsreport','discounts','cashctl',
    'staff','custtype','supplyhome','ingpurchase','ordering','orderhub','branchstock','franchise','manual'];
  return order.find(tabAllowed) || 'manual';
}
// PR-D1: a desktop (wider than the phone breakpoint of the whole app, 700 px) lands on the department launcher, หน้าแรก, for every
// role; a phone keeps the phone-first boot it has today (the owner's summary, or the role's supply page) until D3 redraws it.
function _launcherLanding() { try { return !window.matchMedia('(max-width:700px)').matches; } catch (e) { return true; } }
// PR-D3: on a phone (<= 700 px) the person signs in to the phone shell instead: the department launcher, the bottom navigation and, behind
// them, the owner's summary and the pages (js/09c-mobile.js, "PHONE SHELL"). body.ph-on is set only there, so every rule keyed on it is
// inert on a desktop.
function phOn() { return document.body.classList.contains('ph-on'); }
// PR-F1: the franchise owner has ONE page, ร้านของฉัน - the login lands on it directly (desktop and phone), never on a launcher of one tile
function franchiseLogin() { return !!(window.AUTH && window.AUTH.enabled && window.AUTH.role === 'franchise'); }
function _landingTab() { return franchiseLogin() ? 'franchise' : _launcherLanding() ? 'launcher' : 'home'; }

// {login: name shown on screens} from /api/auth/me; whoTh() reads it (PR-M1)
async function _loadAuthNames() {
  try {
    const j = await (await fetch(`${API}/api/auth/me`)).json();
    if (window.AUTH && j) { _authRulesFrom(j); window.AUTH.display_name = j.display_name || null; window.AUTH.names = j.names || {};
      window.AUTH.role_th = j.role_th || null; window.AUTH.write_gated = j.write_gated; _writeTokenFromAuth(); }
  } catch (e) { /* the pages fall back to the login name */ }
}
async function bootstrapAuth() {
  try {
    const r = await fetch(`${API}/api/auth/me`);
    window.AUTH = await r.json();
    window.AUTH.modules = window.AUTH.modules || [];
    _authRulesFrom(window.AUTH);
    _writeTokenFromAuth();
  } catch (e) {
    // Network/health failure — don't hard-lock the UI; treat as auth-off.
    window.AUTH = { enabled: false, authenticated: true, role: 'management', modules: [] };
  }
}

function showLogin()  { const o=document.getElementById('loginOverlay'); if(o) o.style.display='flex';
                        // a phone keeps the login for a month unless unticked; a desktop asks again after 12 h
                        const r=document.getElementById('loginRemember'); if (r) { try { r.checked = window.matchMedia('(max-width:700px)').matches; } catch(e) {} }
                        setTimeout(()=>document.getElementById('loginUser')?.focus(),50); }
function hideLogin()  { const o=document.getElementById('loginOverlay'); if(o) o.style.display='none'; }

async function doLogin(ev) {
  if (ev) ev.preventDefault();
  const u = document.getElementById('loginUser').value.trim();
  const p = document.getElementById('loginPass').value;
  const errEl = document.getElementById('loginError');
  const btn = document.getElementById('loginBtn');
  errEl.style.display = 'none';
  btn.disabled = true; btn.textContent = 'กำลังเข้าสู่ระบบ…';
  try {
    const res = await fetch(`${API}/api/auth/login`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ username:u, password:p, remember: !!document.getElementById('loginRemember')?.checked }),
    });
    if (!res.ok) {
      const d = await res.json().catch(()=>({}));
      errEl.textContent = d.detail || 'เข้าสู่ระบบไม่สำเร็จ';
      errEl.style.display = 'block';
      return;
    }
    const data = await res.json();
    window.AUTH = { enabled:true, authenticated:true, username:data.username,
                    role:data.role, branch:data.branch || null, modules:data.modules || [] };
    _sessionExpiredShown = false;
    await _loadAuthNames();
    await loadBcMode();                       // PR-S1: before any menu is drawn
    hideLogin();
    applyPermissions();
    // Force a password change off the shared/temporary default before using the app.
    if (data.must_change) { openPasswordChange(true); return; }
    await startApp();
  } catch (e) {
    errEl.textContent = 'เชื่อมต่อเซิร์ฟเวอร์ไม่ได้';
    errEl.style.display = 'block';
  } finally {
    btn.disabled = false; btn.textContent = 'เข้าสู่ระบบ';
  }
}

async function doLogout() {
  try { await fetch(`${API}/api/auth/logout`, { method:'POST' }); } catch(e){}
  // the offline copy of this person's numbers goes with the session (sw.js)
  try { navigator.serviceWorker && navigator.serviceWorker.controller && navigator.serviceWorker.controller.postMessage({ type: 'clear-api-cache' }); } catch(e){}
  location.reload();
}

// ── Change password (forced on first login, or voluntary from the user chip) ──
let _pwForced = false;
function openPasswordChange(force) {
  _pwForced = !!force;
  const ov = document.getElementById('pwOverlay'); if (!ov) return;
  document.getElementById('pwCurrent').value = '';
  document.getElementById('pwNew').value = '';
  document.getElementById('pwConfirm').value = '';
  document.getElementById('pwError').style.display = 'none';
  document.getElementById('pwHint').textContent = force
    ? 'ตั้งรหัสผ่านใหม่ก่อนใช้งาน อย่างน้อย 10 ตัวอักษร · รหัสผ่านปัจจุบัน = รหัสที่ได้รับ'
    : 'ตั้งรหัสผ่านใหม่ (อย่างน้อย 10 ตัวอักษร)';
  // When forced, no cancel — the user must set a password before continuing.
  document.getElementById('pwCancel').style.display = force ? 'none' : '';
  ov.style.display = 'flex';
  setTimeout(() => document.getElementById('pwCurrent').focus(), 50);
}
function closePasswordChange() {
  const ov = document.getElementById('pwOverlay'); if (ov) ov.style.display = 'none';
}
async function submitPasswordChange(ev) {
  if (ev) ev.preventDefault();
  const cur = document.getElementById('pwCurrent').value;
  const nw  = document.getElementById('pwNew').value;
  const cf  = document.getElementById('pwConfirm').value;
  const err = document.getElementById('pwError');
  const btn = document.getElementById('pwSubmit');
  const fail = (m) => { err.textContent = m; err.style.display = 'block'; };
  err.style.display = 'none';
  if (nw.length < 10) return fail('รหัสผ่านใหม่ต้องยาวอย่างน้อย 10 ตัวอักษร');   // = auth.MIN_PASSWORD_LEN
  if (nw !== cf)      return fail('รหัสผ่านใหม่ทั้งสองช่องไม่ตรงกัน');
  btn.disabled = true; btn.textContent = 'กำลังบันทึก…';
  try {
    const res = await fetch(`${API}/api/auth/me/password`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ current_password: cur, new_password: nw }),
    });
    if (!res.ok) { const d = await res.json().catch(()=>({})); return fail(d.detail || 'เปลี่ยนรหัสผ่านไม่สำเร็จ'); }
    closePasswordChange();
    if (_pwForced) { _pwForced = false; await startApp(); }   // continue into the app
    else if (typeof notifySuccess === 'function') notifySuccess('เปลี่ยนรหัสผ่านแล้ว');
  } catch (e) {
    fail('เชื่อมต่อเซิร์ฟเวอร์ไม่ได้');
  } finally {
    btn.disabled = false; btn.textContent = 'บันทึกรหัสผ่านใหม่';
  }
}

function renderUserChip() {
  const chip = document.getElementById('userChip');
  if (!chip) return;
  if (window.AUTH && window.AUTH.enabled && window.AUTH.authenticated) {
    const role = window.AUTH.role || '';
    const nameEl = document.getElementById('userChipName');
    const _dn = window.AUTH.display_name || whoTh(window.AUTH.username);
    nameEl.textContent = isPinnedSession() || _dn === (ROLE_LABEL_TH[role] || role) ? _dn : `${_dn} · ${ROLE_LABEL_TH[role] || role}`;   // a pinned login's chip sits in the phone's tab strip: the name only
    nameEl.title = ROLE_LABEL_TH[role] || role;
    chip.style.display = 'flex';
  } else {
    chip.style.display = 'none';
  }
}

// Hide nav items / sections / topbar actions the current role can't use.
function applyPermissions() {
  renderUserChip();
  if (!window.AUTH || !window.AUTH.enabled) return;   // auth off → show everything
  // body.role-<role>: CSS trims the chrome a role never uses (the branch login's top bar)
  [...document.body.classList].filter(c => c.startsWith('role-')).forEach(c => document.body.classList.remove(c));
  document.body.classList.add('role-' + (window.AUTH.role || 'none'));
  // 1) Hide disallowed nav items (don't force-show ones intentionally display:none)
  Object.keys(TAB_MODULE).forEach(tab => {
    const el = document.getElementById('nav-' + tab);
    if (el && !tabAllowed(tab)) el.style.display = 'none';
  });
  // 1b) Cluster safety net: each cluster's sidebar entry is tabs[0]; when a
  // role can't open the entry but CAN open other members, reveal the first
  // allowed member's nav item so the cluster stays reachable for that role.
  TAB_CLUSTERS.forEach(cl => {
    if (tabAllowed(cl.tabs[0][0])) return;
    const alt = cl.tabs.find(([t]) => tabAllowed(t));
    if (alt) { const el = document.getElementById('nav-' + alt[0]); if (el) el.style.display = ''; }
  });
  // the franchise owner (PR-L) has ONE page, ร้านของฉัน: its nav entry is the only one left, and on a phone the sidebar
  // (a tab strip there) holds just that entry beside the user chip
  if (window.AUTH.role === 'franchise') {
    // PR-D1: on a desktop the sidebar is หน้าแรก + the department entry (.dept-entry, js/18-launcher.js); on a phone those are hidden by CSS
    document.querySelectorAll('.sidebar-nav .nav-item').forEach(el => { el.style.display = el.id === 'nav-franchise' || el.classList.contains('dept-entry') ? '' : 'none'; });
    document.body.classList.add('franchise-phone');
    // no branch selector anywhere (PR-K rule): the other pages are never opened, so their branch pickers go too
    document.querySelectorAll('#tabsRoot select').forEach(s => { if (/branch/i.test(s.id)) s.remove(); });
  }
  // the branch login's entry to วัตถุดิบและใบสั่งซื้อ is its own page: call it so
  if (window.AUTH.role === 'branch') { const l = document.querySelector('#nav-supplyhome .nav-label'); if (l) l.textContent = 'สาขาของฉัน'; }
  // ... and on a phone its pages sit under the phone shell's bottom navigation (#phNav, PR-D3) instead of the chip rows
  if (window.AUTH.role === 'branch') {
    document.body.classList.add('supply-phone'); phNavRender();
    try { brnBigFontApply(brnBigFontGet()); } catch (e) { /* ตัวอักษรใหญ่ (PR-M7) is read from this device's choice once the branch slice is loaded */ }
  }
  // 2) Collapse nav-section headers whose items are all hidden
  document.querySelectorAll('.nav-section').forEach(sec => {
    let n = sec.nextElementSibling, anyVisible = false;
    while (n && !n.classList.contains('nav-section')) {
      if (n.classList.contains('nav-item') && getComputedStyle(n).display !== 'none') anyVisible = true;
      n = n.nextElementSibling;
    }
    sec.style.display = anyVisible ? '' : 'none';
  });
  // 3) Topbar action buttons
  const costUp = document.querySelector('[onclick="openCostModal()"]');
  if (costUp) costUp.style.display = (hasModule('bom.edit') || hasModule('system.settings')) ? '' : 'none';
  const salesUp = document.querySelector('[onclick*="salesDataFile"]');
  if (salesUp) salesUp.style.display = hasModule('data.upload') ? '' : 'none';
  // The monthly review pack bundles P&L + top cost drivers → cost/profit roles only
  // (server gates /api/export/monthly-pack the same way; this avoids a raw-403 page).
  const packBtn = document.querySelector('[onclick="openMonthlyPackDialog()"]');
  if (packBtn) packBtn.style.display = (hasModule('cost.analysis') || hasModule('profit.pnl')) ? '' : 'none';
  // 4) Settings → ผู้ใช้งาน sub-tab visible to admins only
  const uBtn = document.getElementById('settabbtn-users');
  if (uBtn) uBtn.style.display = hasModule('system.admin') ? '' : 'none';
  const csBtn = document.getElementById('settabbtn-cloudsync');       // the cloud pull of Easy POS: Management only
  if (csBtn) csBtn.style.display = hasModule('system.admin') ? '' : 'none';
  const cbBtn = document.getElementById('settabbtn-cloudbackup');     // the cloud backup of the database: Management only
  if (cbBtn) cbBtn.style.display = hasModule('system.admin') ? '' : 'none';
  const cjBtn = document.getElementById('settabbtn-cloudjobs');       // every cloud job in one list: Management only
  if (cjBtn) cjBtn.style.display = hasModule('system.admin') ? '' : 'none';
  // 5) If the current/landing tab isn't allowed, jump to the first allowed one
  if (!tabAllowed(activeTab)) activeTab = firstAllowedTab();
  // 6) The phone summary is the owner's sales view. A role without sales.overview
  //    (the branch login) gets its own pages on the phone instead — they are built 375-px first.
  if (window.AUTH && window.AUTH.enabled && !hasModule('sales.overview')) document.body.classList.add('force-desktop', 'no-mobile-app');
  // QA 2026-10-13 M4 · a role whose daily work is a supply page (Sales Support, the kitchen, operation, accounting) opens on it on a phone too,
  // not on the owner's sales summary - unless the person chose the summary themselves (showMobileSummary). The summary stays one tap away
  // (the phone button) and carries a link back to หน้างาน (#msSupply). What the role may SEE is unchanged: only which page opens first.
  else if (_supplyPhoneRole() && _phoneModeSaved() !== 'summary') document.body.classList.add('force-desktop');
  const msSupply = document.getElementById('msSupply');
  if (msSupply) msSupply.style.display = _supplyPhoneRole() ? '' : 'none';
  // 7) A pinned login (role branch / franchise) is shown its branch as text; no branch picker is left on any page
  stripBranchPickers();
}

// PR-K (owner 2026-10-10): a pinned session — role `branch` or `franchise`, tied to one branch by the server —
// sees NO branch selector anywhere: not hidden, not disabled. The pickers in the page fragments carry
// data-branch-picker; here they are taken out of the DOM (the branch name stays as text) and the shell's own
// filter (#selBranch) becomes a hidden field holding the branch. The branch-list endpoints are never called for
// such a session (loadBranches, loadBranchStock and the hub panes check isPinnedSession first).
const PINNED_ROLES = ['branch', 'franchise'];
function isPinnedSession() { return !!(window.AUTH && window.AUTH.enabled && PINNED_ROLES.includes(window.AUTH.role)); }
function pinnedBranchName() { return isPinnedSession() ? (window.AUTH.branch || '') : ''; }
function stripBranchPickers() {
  if (!isPinnedSession()) return;
  const name = pinnedBranchName();
  // a label that only held the picker goes with it; the pages show the branch name as text where it matters
  document.querySelectorAll('select[data-branch-picker]').forEach(sel => (sel.closest('label') || sel).remove());
  const g = document.getElementById('selBranch');
  if (g && g.tagName === 'SELECT') {
    const h = document.createElement('input');
    h.type = 'hidden'; h.id = 'selBranch'; h.value = name; h.setAttribute('aria-hidden', 'true');
    g.replaceWith(h);
  }
  document.getElementById('msBranch')?.remove();                  // the owner's phone summary picker
  document.getElementById('gpBranchChip')?.remove();              // the phone's branch chip by the page title (and so its sheet)
  document.getElementById('phBranchSheet')?.remove();
  const nm = document.getElementById('brnBranchName');
  if (nm && !nm.textContent) nm.textContent = name;
}

// ── User management (Settings → ผู้ใช้งาน) ──────────────────────────────────
async function loadUsers() {
  // Populate the role <select> for the add-user form
  const sel = document.getElementById('newUserRole');
  if (sel && !sel.options.length) {
    Object.entries(ROLE_LABEL_TH).forEach(([k,v]) =>
      sel.add(new Option(`${v} (${k})`, k)));
  }
  const tb = document.getElementById('userTableBody');
  if (tb) tb.innerHTML = '<tr><td class="py-3 text-sm" style="color:var(--text-muted)">กำลังโหลด…</td></tr>';
  // E4: a login can be confined to one branch — the feedback module then shows only that branch
  if (!window._userBranches) {
    try { window._userBranches = ((await api(`${API}/api/branches`)) || []).filter(b => b.active !== false).map(b => b.name); }
    catch (e) { window._userBranches = []; }
    const bs = document.getElementById('newUserBranch');
    if (bs && bs.options.length <= 1) window._userBranches.forEach(b => bs.add(new Option(b, b)));
  }
  const branchOpts = cur => `<option value="">ทุกสาขา</option>` + (window._userBranches || []).map(b => `<option value="${b}" ${b === cur ? 'selected' : ''}>${b}</option>`).join('');
  let users = [];
  try { users = await api(`${API}/api/auth/users`); }
  catch(e) { if (tb) tb.innerHTML = '<tr><td class="py-3 text-sm text-rose-500">โหลดรายชื่อผู้ใช้ไม่ได้</td></tr>'; return; }
  if (!Array.isArray(users)) { if (tb) tb.innerHTML = '<tr><td class="py-3 text-sm text-rose-500">ไม่มีสิทธิ์ดูรายชื่อผู้ใช้</td></tr>'; return; }
  tb.innerHTML = users.map(u => {
    const opts = Object.entries(ROLE_LABEL_TH).map(([k,v]) =>
      `<option value="${k}" ${k===u.role?'selected':''}>${v} (${k})</option>`).join('');
    const me = window.AUTH && window.AUTH.username === u.username;
    return `<tr class="border-b" style="border-color:var(--border)">
      <td class="py-2 pr-4 font-medium" style="color:var(--text-primary)">${u.username}${me?' <span class=\"text-xs\" style=\"color:var(--text-muted)\">(คุณ)</span>':''}</td>
      <td class="py-2 pr-4"><select onchange="updateUserRole('${u.username}', this.value)" ${me?'disabled title="เปลี่ยนบทบาทตัวเองไม่ได้"':''} class="border rounded px-2 py-1 text-xs ${me?'opacity-50 cursor-not-allowed':''}" style="border-color:var(--border)">${opts}</select></td>
      <td class="py-2 pr-4"><select onchange="updateUserBranch('${u.username}', this.value)" class="border rounded px-2 py-1 text-xs" style="border-color:var(--border)" title="เสียงลูกค้าจะเห็นเฉพาะสาขานี้">${branchOpts(u.branch || '')}</select></td>
      <td class="py-2 pr-4">${u.active
        ? '<span class="text-xs px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700">ใช้งาน</span>'
        : '<span class="text-xs px-2 py-0.5 rounded-full bg-slate-200 text-slate-600">ปิด</span>'}</td>
      <td class="py-2 pr-4 text-xs" style="color:var(--text-muted)">${(u.created_at||'').slice(0,10)}</td>
      <td class="py-2 text-right whitespace-nowrap">
        <button onclick="resetUserPassword('${u.username}')" class="text-xs px-2 py-1 rounded border mr-1" style="border-color:var(--border)" title="ตั้งรหัสผ่านใหม่">${ICON.key}</button>
        ${me ? '' : `<button onclick="toggleUserActive('${u.username}', ${u.active?0:1})" class="text-xs px-2 py-1 rounded border mr-1" style="border-color:var(--border)">${u.active?'ปิดใช้':'เปิดใช้'}</button>`}
        ${me ? '' : `<button onclick="deleteUser('${u.username}')" class="text-xs px-2 py-1 rounded border text-rose-600" style="border-color:var(--border)" title="ลบผู้ใช้">${ICON.trash}</button>`}
      </td></tr>`;
  }).join('') || '<tr><td class="py-3 text-sm" style="color:var(--text-muted)">ยังไม่มีผู้ใช้</td></tr>';
}
let _rolePermsData = null;

async function loadRolePermissions() {
  const el = document.getElementById('rolePermMatrix');
  if (!el) return;
  const canEdit = window.AUTH && window.AUTH.enabled && window.AUTH.modules && window.AUTH.modules.includes('system.admin');
  const btn = document.getElementById('permSaveBtn');
  if (btn) btn.classList.toggle('hidden', !canEdit);

  // Try to load from API; fall back to local constants
  let roles, modules, permissions;
  try {
    const d = await fetch(`${API}/api/auth/role-permissions`).then(r => r.ok ? r.json() : null);
    if (d) {
      roles = d.roles;
      modules = d.modules;
      permissions = d.permissions;
    }
  } catch(e) {}
  if (!permissions) {
    roles = Object.keys(ROLE_LABEL_TH);
    modules = Object.keys(MODULE_LABEL_TH);
    permissions = {};
    roles.forEach(r => { permissions[r] = ROLE_MODULES_TH[r] || []; });
  }
  _rolePermsData = { roles, modules, permissions };
  _renderPermMatrix(canEdit);
}

function _renderPermMatrix(canEdit) {
  const el = document.getElementById('rolePermMatrix');
  if (!el || !_rolePermsData) return;
  const { roles, modules, permissions } = _rolePermsData;

  // Group modules for readability
  const MOD_GROUP = {
    'sales.overview':'ยอดขาย', 'customer.buffet':'ลูกค้า', 'menu.analysis':'เมนู',
    'cost.analysis':'ต้นทุน', 'cost.simulator':'ตัวจำลอง', 'bom.edit':'แก้สูตร (ระบบเดิม)',
    'profit.pnl':'กำไร/P&L', 'ops.report':'รายงานปฏิบัติการ', 'ops.discounts':'ส่วนลด',
    'ops.cash':'เงินขาด/เกิน', 'ops.staff':'พนักงาน', 'ops.posextra':'รายละเอียด POS',
    'report.export':'ส่งออกรายงาน', 'system.settings':'ตั้งค่า', 'system.admin':'ผู้ดูแลระบบ',
    'data.upload':'อัปโหลดข้อมูล', 'customer.feedback':'เสียงลูกค้า', 'supply.ordering':'วัตถุดิบ/ใบสั่งซื้อ', 'bom.center':'ข้อมูลตั้งต้นและสูตร', 'bom.release':'ประกาศใช้สูตร', 'bom.audit':'รับทราบสูตร',
    'branch.stock':'สาขา: ตัดเบิก นับของ สั่งของ', 'bom.prices':'ราคารายเดือน', 'bom.recipes':'สูตร', 'supply.warehouse':'คลัง: ยืนยันยอดส่ง', 'franchise.view':'ร้านของฉัน (เจ้าของสาขา)', 'franchise.inbox':'ข้อความเจ้าของสาขา',
  };

  const th_role = `<th class="py-2 pr-4 text-left text-xs font-semibold sticky left-0" style="background:var(--bg-subtle);color:var(--text-muted);min-width:120px">บทบาท</th>`;
  const th_mods = modules.map(m =>
    `<th class="py-2 px-2 text-center text-[10px] font-medium whitespace-nowrap" style="color:var(--text-muted)" title="${m}">${MOD_GROUP[m]||m}</th>`
  ).join('');

  const rows = roles.map(role => {
    const roleMods = new Set(permissions[role] || []);
    const cells = modules.map(m => {
      const checked = roleMods.has(m);
      const dis = (role === 'management' && m === 'system.admin') ? ' title="ผู้บริหารมีสิทธิ์นี้เสมอ"' : '';
      const inp = canEdit
        ? `<input type="checkbox" data-role="${role}" data-mod="${m}" ${checked?'checked':''} ${dis}
             onchange="_permToggle(this)" class="w-3.5 h-3.5 accent-indigo-600">`
        : `<span style="color:${checked?'#4a70b0':'#e2e8f0'};font-size:14px">${checked?'✓':'–'}</span>`;
      return `<td class="py-1.5 px-2 text-center">${inp}</td>`;
    }).join('');
    return `<tr class="border-b" style="border-color:var(--border)">
      <td class="py-1.5 pr-4 text-sm font-medium sticky left-0" style="background:var(--bg-card);color:var(--text-primary)" title="รหัสบทบาทในระบบ: ${role}">
        ${ROLE_LABEL_TH[role]||role}
      </td>${cells}</tr>`;
  }).join('');

  el.innerHTML = `<table class="text-sm w-full border-collapse">
    <thead><tr class="border-b" style="border-color:var(--border)">${th_role}${th_mods}</tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

function _permToggle(cb) {
  if (!_rolePermsData) return;
  const role = cb.dataset.role, mod = cb.dataset.mod;
  const perms = _rolePermsData.permissions;
  if (!perms[role]) perms[role] = [];
  if (cb.checked) { if (!perms[role].includes(mod)) perms[role].push(mod); }
  else { perms[role] = perms[role].filter(m => m !== mod); }
}

async function saveRolePermissions() {
  if (!_rolePermsData) return;
  const btn = document.getElementById('permSaveBtn');
  const status = document.getElementById('permSaveStatus');
  if (btn) { btn.disabled = true; btn.textContent = 'กำลังบันทึก…'; }
  try {
    const res = await fetch(`${API}/api/auth/role-permissions`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ permissions: _rolePermsData.permissions }),
    });
    if (!res.ok) {
      const d = await res.json().catch(()=>({}));
      if (status) { status.textContent = d.detail || 'บันทึกไม่สำเร็จ'; status.className = 'text-xs text-rose-600'; }
    } else {
      if (status) { status.textContent = 'บันทึกแล้ว ✓'; status.className = 'text-xs text-emerald-600'; }
      setTimeout(() => { if (status) status.textContent = ''; }, 3000);
      await loadRolePermissions();
    }
  } catch(e) {
    if (status) { status.textContent = 'เชื่อมต่อไม่ได้'; status.className = 'text-xs text-rose-600'; }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'บันทึกสิทธิ์'; }
  }
}

function _userStatus(msg, ok, ms) {
  const s = document.getElementById('userSaveStatus');
  if (!s) return;
  s.textContent = msg; s.className = 'text-sm ' + (ok ? 'text-emerald-600' : 'text-rose-600');
  setTimeout(() => { s.textContent = ''; }, ms || 4000);
}
async function createUser() {
  const username = document.getElementById('newUserName').value.trim();
  const password = document.getElementById('newUserPass').value;
  const role = document.getElementById('newUserRole').value;
  const branch = (document.getElementById('newUserBranch') || {}).value || '';
  if (!username) { _userStatus('กรอกชื่อผู้ใช้', false); return; }
  const res = await fetch(`${API}/api/auth/users`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ username, password, role, branch }) });
  if (res.ok) {
    document.getElementById('newUserName').value = '';
    document.getElementById('newUserPass').value = '';
    const d = await res.json().catch(()=>({}));
    // Blank password → server used the shared default; say so, the admin has to pass it on.
    _userStatus(d.default_password
      ? `เพิ่ม ${username} แล้ว — รหัสเริ่มต้น ${d.default_password} (ระบบบังคับเปลี่ยนตอนเข้าครั้งแรก)`
      : 'เพิ่มผู้ใช้แล้ว', true, 12000);
    loadUsers();
  } else {
    const d = await res.json().catch(()=>({})); _userStatus(d.detail || 'เพิ่มไม่สำเร็จ', false);
  }
}
async function _putUser(username, body, okMsg) {
  const res = await fetch(`${API}/api/auth/users/${encodeURIComponent(username)}`, {
    method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
  if (res.ok) { _userStatus(okMsg, true); loadUsers(); }
  else { const d = await res.json().catch(()=>({})); _userStatus(d.detail || 'บันทึกไม่สำเร็จ', false); }
}
function updateUserRole(username, role) { _putUser(username, { role }, `เปลี่ยนบทบาท ${username} แล้ว`); }
function updateUserBranch(username, branch) { _putUser(username, { branch }, branch ? `${username} เห็นเฉพาะสาขา ${branch}` : `${username} เห็นทุกสาขา`); }
function toggleUserActive(username, active) { _putUser(username, { active: !!active }, 'อัปเดตสถานะแล้ว'); }
function resetUserPassword(username) {
  const pw = window.prompt(`ตั้งรหัสผ่านใหม่สำหรับ ${username}
(เว้นว่างแล้วกด OK = กลับเป็นรหัสเริ่มต้น Oshinei@2026 และบังคับเปลี่ยนตอนเข้าครั้งถัดไป):`);
  if (pw === null) return;                                  // cancelled
  if (pw) _putUser(username, { password: pw }, 'ตั้งรหัสผ่านใหม่แล้ว');
  else _putUser(username, { reset_default: true }, `${username} กลับเป็นรหัสเริ่มต้น Oshinei@2026 แล้ว — ต้องเปลี่ยนตอนเข้าครั้งถัดไป`);
}
async function deleteUser(username) {
  if (!window.confirm(`ลบผู้ใช้ ${username}?`)) return;
  const res = await fetch(`${API}/api/auth/users/${encodeURIComponent(username)}`, { method:'DELETE' });
  if (res.ok) { _userStatus('ลบผู้ใช้แล้ว', true); loadUsers(); }
  else { const d = await res.json().catch(()=>({})); _userStatus(d.detail || 'ลบไม่สำเร็จ', false); }
}

// Formal theme: muted, deep hues that each reach 3:1 on BOTH the light and the dark card,
// so one palette serves both themes (design-system/tokens.json chart-1..12).
// Categorical series colours (owner 2026-10-01: the muted set read as dull). The
// first eight are the dataviz reference palette — adjacent pairs pass the CVD
// (deutan/protan/tritan) and normal-vision separation gates on a light surface —
// followed by four spares for the rare chart with more than eight series.
// VZ2 (2026-10-22): the glance layer re-colours these twelve slots to the theme-B set (B1_PAL below) at chart init, so legends
// and bars agree; the list stays as the key to that mapping.
// 2026-10-02 owner: "สีแท่งสดไปหน่อย … อยากได้ความพาสเทล" — the pastel is applied to EVERY
// bar by the glance layer's shade (see _glanceBarGrad, VZ2: tip lighter than the base, per bar; was tip 25% / base 50% toward the
// surface), so status-coloured bars (good/bad amber-green-red) soften with the
// series ones. The palette itself stays the validated reference set; legends and
// tooltips keep these true colours.
const PALETTE = ['#2a78d6','#eb6834','#1baf7a','#eda100','#e87ba4','#008300','#4a3aa7','#e34948','#1d9bd8','#c47b2a','#9b5fb8','#5a9f33'];

// ── Theme B charts (VZ2, 2026-10-22; owner chose variant B1 "gradient + meaning") ──────────────
// B1_PAL mirrors the --ch-* tokens in docs/ui-kit/tokens.css (backend/test_charts_b1.py keeps them equal); canvas charts cannot
// read a CSS variable, so the plugin below takes these. a/aw above the average (weekend brighter), b/bw below it, t the latest
// bar (gold, dark edge), c the 6 categorical series colours, spare 6 more for a chart with more series, s the 5-step sequential
// scale, avg the average line, gi gold as text. Colour-blind safe: teal/indigo vs lilac vs gold, never red against green.
const B1_PAL = {
  light: {
    a1: '#0c8f81', a2: '#3a2fb5', aw1: '#0f9d8e', aw2: '#5148d4', b1: '#8f78df', b2: '#7e62d2', bw1: '#9a84e6', bw2: '#8468d8',
    t1: '#f0a500', t2: '#b86e00', tstroke: '#7a4a00', avg: '#475569', gi: '#7a4500',
    c: ['#3a2fb5', '#0e8f87', '#c25e00', '#b01d62', '#1d78c9', '#6b7399'],
    spare: ['#6a62d8', '#38a89f', '#d9822b', '#d4577f', '#4a97dc', '#8a92b8'],
    s: ['#7686da', '#6a66d4', '#5b52cc', '#3f33a8', '#2b1f80'],
  },
  dark: {
    a1: '#2dd4bf', a2: '#818cf8', aw1: '#5eead4', aw2: '#a5b4fc', b1: '#8b78d8', b2: '#7a66c8', bw1: '#9a88e4', bw2: '#8975d6',
    t1: '#fcd34d', t2: '#f5b82e', tstroke: '#fff3c4', avg: '#c3cddb', gi: '#fcd34d',
    c: ['#9d98ff', '#2dd4bf', '#fbbf24', '#f472b6', '#60a5fa', '#b4b9e0'],
    spare: ['#b7b3ff', '#6fe3d3', '#fcd34d', '#f9a8d4', '#93c5fd', '#d3d7f0'],
    s: ['#5c6bc0', '#7d86d8', '#9d98e8', '#c0b8f6', '#e6e0ff'],
  },
};
const _B_OLD = PALETTE.map(c => c.toLowerCase());
// SEMANTIC.revenue (the main series blue of the sales lines) takes the first B colour
const _B_EXTRA = { '#4a70b0': 0 };
// A series colour the old PALETTE gave (optionally with an alpha suffix, '#2a78d633') becomes the B colour of the same slot.
function glanceBColour(c, dark) {
  if (typeof c !== 'string') return c;
  const m = c.toLowerCase().match(/^(#[0-9a-f]{6})([0-9a-f]{2})?$/);
  const i = m ? (m[1] in _B_EXTRA ? _B_EXTRA[m[1]] : _B_OLD.indexOf(m[1])) : -1;
  if (i < 0) return c;
  const p = dark ? B1_PAL.dark : B1_PAL.light;
  return (i < 6 ? p.c[i] : p.spare[i - 6]) + (m[2] || '');
}
// The B1 classifier for ONE series on a period axis (daily / weekly / monthly bars). vals: one number per period (null = none);
// o.partial: the index still in progress (left out of the average and never "latest"); o.weekend: one boolean per period.
// Returns null under 4 finished periods (an average of three says nothing), else
//   { avg, latest, best, kinds[i] = { tone: 'now' | 'hi' | 'lo', wk, part } }  (tone 'hi' = at or above the average).
function glanceB1Kinds(vals, o) {
  o = o || {};
  const P = o.partial == null ? -1 : o.partial;
  const comp = [];
  vals.forEach((v, i) => { if (v != null && i !== P) comp.push(i); });
  if (comp.length < 4) return null;
  const avg = comp.reduce((a, i) => a + vals[i], 0) / comp.length;
  const latest = comp[comp.length - 1];
  let best = comp[0];
  comp.forEach(i => { if (vals[i] > vals[best]) best = i; });
  const wk = o.weekend || [];
  const kinds = vals.map((v, i) => v == null ? null
    : { tone: i === latest ? 'now' : (v >= avg ? 'hi' : 'lo'), wk: !!wk[i], part: i === P });
  return { avg, latest, best, kinds };
}

// ── World-class semantic color system ─────────────────────────
const SEMANTIC = {
  revenue:    '#4a70b0',  // indigo
  customers:  '#10b981',  // emerald
  avgTicket:  '#06b6d4',  // cyan
  margin:     '#14b8a6',  // teal
  cost:       '#f97316',  // orange
  discount:   '#f59e0b',  // amber
  refund:     '#ef4444',  // red
  good:       '#10b981',
  bad:        '#ef4444',
  neutral:    '#64748b',
  // Daypart colors (restaurant timeline)
  morning:    '#f59e0b',
  lunch:      '#10b981',
  afternoon:  '#fbbf24',
  dinner:     '#4a70b0',
  late:       '#7a66b8',
  // Branch group
  master:     '#4a70b0',
  franchise:  '#f59e0b',
  easy:       '#4a70b0',
};

// Thai day-of-week color palette
const DOW_COLORS = {
  0: '#ef4444',  // Sun = red
  1: '#eab308',  // Mon = yellow
  2: '#ec4899',  // Tue = pink
  3: '#10b981',  // Wed = green
  4: '#f97316',  // Thu = orange
  5: '#3b82f6',  // Fri = blue
  6: '#7a66b8',  // Sat = purple
};
const DOW_LABELS_TH_SHORT = ['จ','อ','พ','พฤ','ศ','ส','อา'];
const DOW_LABELS_TH = ['อาทิตย์','จันทร์','อังคาร','พุธ','พฤหัสบดี','ศุกร์','เสาร์'];

// Deterministic, category-specific palette (anchored to common Thai-restaurant categories)
const CATEGORY_COLOR_MAP = {
  'BUFFET': '#4a70b0', 'BUFFET 350': '#1f3a6b', 'BUFFET 450': '#4a70b0',
  'A LA CARTE': '#10b981', 'FOOD': '#10b981',
  'DRINK': '#06b6d4', 'BEVERAGE': '#06b6d4', 'SOFT DRINK': '#0891b2',
  'ALCOHOL': '#9333ea', 'BEER': '#a855f7', 'WINE': '#7a66b8',
  'DESSERT': '#ec4899', 'SWEETS': '#f472b6',
  'APPETIZER': '#f59e0b', 'SIDE': '#fbbf24',
  'YAKINIKU': '#dc2626', 'BBQ': '#ef4444', 'GRILL': '#dc2626',
  'SUSHI': '#0d9488', 'SASHIMI': '#14b8a6',
  'NOODLE': '#f97316', 'RAMEN': '#ea580c', 'PASTA': '#fb923c',
  'RICE': '#84cc16', 'CURRY': '#fbbf24',
  'SET MENU': '#7a66b8',
  'OTHER': '#64748b', 'OTHERS': '#94a3b8',
  'SERVICE': '#94a3b8', 'SERVICECHARGE': '#94a3b8', 'SP': '#94a3b8',
};
// Hash-based fallback so unknown categories get a stable color
function _hashColor(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(h) % PALETTE.length];
}
function colorForCategory(name) {
  if (!name) return SEMANTIC.neutral;
  const up = name.toUpperCase().trim();
  // exact match
  if (CATEGORY_COLOR_MAP[up]) return CATEGORY_COLOR_MAP[up];
  // partial — look for any known token in name
  for (const k in CATEGORY_COLOR_MAP) {
    if (up.includes(k)) return CATEGORY_COLOR_MAP[k];
  }
  return _hashColor(up);
}
function colorForHour(h) {
  if (h >= 6  && h <= 10) return SEMANTIC.morning;
  if (h >= 11 && h <= 14) return SEMANTIC.lunch;
  if (h >= 15 && h <= 16) return SEMANTIC.afternoon;
  if (h >= 17 && h <= 21) return SEMANTIC.dinner;
  return SEMANTIC.late;
}
// Rank-tier colors (top tier emerald → middle indigo → bottom amber)
function colorForRank(idx, total) {
  const t = idx / Math.max(1, total - 1);
  if (t < 0.33) return SEMANTIC.good;        // top third
  if (t < 0.66) return SEMANTIC.revenue;     // middle third
  return SEMANTIC.discount;                  // bottom third
}
// ── Heat scales (heatmaps) ──
// The old red→amber→green scale painted VOLUME as good/bad: a small branch or a
// quiet hour came out "red" though nothing was wrong. Two scales instead:
//  - sequential = "how much": one navy hue, light → dark, in 5 steps so a cell
//    reads as a band rather than a shade to squint at;
//  - diverging  = "vs normal": red below, grey around, green above - only where
//    a baseline makes good/bad true (a cell vs its own branch's average).
// The fills are theme TOKENS (--heat-s1..5 / --heat-d1..5 with matching -ink
// label colours, index.html), so a heatmap follows the theme toggle without a
// re-render; inkOn() maps a token fill to its ink token.
// % vs normal edges: <=-20 | -20..-5 | ±5 | 5..20 | >=20
// t = value / max, in (0, 1]
function heatSeq(t) {
  if (t == null || !isFinite(t) || t <= 0) return null;
  return `var(--heat-s${Math.min(4, Math.floor(Math.min(1, t) * 5 - 1e-9)) + 1})`;
}
function heatDiv(pct) {
  if (pct == null || !isFinite(pct)) return null;
  return `var(--heat-d${(pct <= -20 ? 0 : pct < -5 ? 1 : pct <= 5 ? 2 : pct < 20 ? 3 : 4) + 1})`;
}
// A legend row: 5 swatches with their labels.
function heatLegend(kind, labels) {
  const cols = [1, 2, 3, 4, 5].map(i => `var(--heat-${kind === 'div' ? 'd' : 's'}${i})`);
  return `<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:10.5px;color:var(--text-muted)">${cols.map((c, i) =>
    `<span style="display:inline-flex;align-items:center;gap:4px"><span style="width:14px;height:10px;border-radius:2px;background:${c};border:1px solid var(--border)"></span>${labels[i] || ''}</span>`).join('')}</div>`;
}
// Owner 2026-10-25 ("heat map ทำให้เป็น heat map จริงๆแดงเขียวเลย"): a real red → neutral → green scale for a % against a baseline
// (a cell vs its own branch's normal, a change vs the period before). Symmetric, four steps a side, capped at ±30 %:
//   |pct| < 3 neutral · 3–10 · 10–20 · 20–30 · >= 30 (the darkest red / green). The fills are theme tokens --heat-rgm4..m1, --heat-rg0,
//   --heat-rgp1..p4 (index.html, each with an -ink token >= 4.9:1 in light and dark), so inkOn() gives the label colour.
const HEAT_RG_EDGES = [3, 10, 20, 30];
function heatRGStep(pct) {
  if (pct == null || !isFinite(pct)) return null;
  const a = Math.abs(pct), s = HEAT_RG_EDGES.filter(e => a >= e).length;
  return pct < 0 ? -s : s;
}
function heatRGToken(s) { return `var(--heat-rg${s < 0 ? 'm' + -s : s > 0 ? 'p' + s : '0'})`; }
function heatRG(pct) {
  const s = heatRGStep(pct);
  return s == null ? null : heatRGToken(s);
}
// the legend strip: ต่ำกว่าปกติ ← nine swatches → สูงกว่าปกติ, each swatch titled with its band
function heatRGLegend() {
  const E = HEAT_RG_EDGES, n = E.length;
  const band = s => {
    const a = Math.abs(s);
    if (!s) return `ปกติ ±${E[0]}%`;
    return (s < 0 ? 'ต่ำกว่า ' : 'สูงกว่า ') + (a === n ? `${E[a - 1]}% ขึ้นไป` : `${E[a - 1]}–${E[a]}%`);
  };
  const steps = [];
  for (let s = -n; s <= n; s++) steps.push(s);
  const sw = steps.map(s => `<span class="hrg-sw" title="${band(s)}" style="background:${heatRGToken(s)}"></span>`).join('');
  return `<div class="hrg-legend" role="img" aria-label="สีแดง ต่ำกว่าปกติ · สีเขียว สูงกว่าปกติ · ปกติ ±${E[0]}% · สีเข้มสุด ±${E[n - 1]}% ขึ้นไป">`
    + `<span class="hrg-end">ต่ำกว่าปกติ ←</span><span class="hrg-sws" aria-hidden="true">${sw}</span><span class="hrg-end">→ สูงกว่าปกติ</span>`
    + `<span class="hrg-note">±${E[0]}% = ปกติ · เข้มสุด ±${E[n - 1]}%</span></div>`;
}
// Label colour for text ON a data-driven fill (heat cells, bar segments, badges): white or
// the light-theme primary ink, whichever contrasts more. Fixed hex both themes, like the fill.
// White fails on the whole heat scale (#ef4444 3.76, #f59e0b 2.15, #16a34a 3.30); #0f172a clears it.
function inkOn(bg) {
  const tok = String(bg || '').match(/^var\(--heat-([sd][1-5]|rg(?:[mp][1-4]|0))\)$/);
  if (tok) return `var(--heat-${tok[1]}-ink)`;   // heat fills are theme tokens with their own ink
  const m = String(bg || '').trim().match(/^#(?:([0-9a-f]{6})(?:[0-9a-f]{2})?|([0-9a-f]{3}))(?![0-9a-f])|^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/i);
  if (!m) return '#fff';
  let rgb;
  if (m[1] || m[2]) { const h = m[1] || m[2].replace(/./g, c => c + c); rgb = [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16)); }
  else rgb = [m[3], m[4], m[5]].map(Number);
  const lin = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const L = 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2]);
  const onWhite = 1.05 / (L + 0.05), onNavy = (L + 0.05) / (0.0093 + 0.05);   // #0f172a L = 0.0093
  return onWhite >= onNavy ? '#fff' : '#0f172a';
}
// Deterministic, distinct colour per branch name (stable across reloads)
function colorForBranch(name) {
  if (!name) return SEMANTIC.neutral;
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(h) % PALETTE.length];
}

// Theme-aware axis / grid colors that respect dark mode
function chartTheme(force) {
  // force='light' → a chart inside a forced-light island (e.g. the printable
  // Management Report keeps a white-paper look in dark mode) themes as light.
  const dark = force ? force === 'dark' : document.body.classList.contains('dark');
  return {
    text:     dark ? '#dbe3ee' : '#64748b',      // dark: brighter axis text (owner 2026-10-19)
    textDim:  dark ? '#a8b4c6' : '#94a3b8',
    grid:     dark ? 'rgba(148,163,184,0.16)' : '#f1f5f9',
    labelStrong: dark ? '#f1f5f9' : '#1e293b',
    bg:       dark ? '#1e293b' : '#ffffff',
    // Legend / title ink. Chart.defaults.color is a fixed #475569, which is
    // 2.3:1 on the dark card, so each chart gets a theme-aware default.
    ink:      dark ? '#c3cddb' : '#475569',
  };
}
const _CHART_SLATE = ['#64748b', '#94a3b8', '#334155', '#475569'];
function applyChartTheme(cfg, force) {
  const t = chartTheme(force);
  if (!cfg.options) return;
  if (cfg.options.color === undefined) cfg.options.color = t.ink;
  if (cfg.options.scales) {
    Object.values(cfg.options.scales).forEach(ax => {
      if (!ax) return;
      ax.grid = ax.grid || {};
      ax.ticks = ax.ticks || {};
      if (ax.grid.color !== false && ax.grid.display !== false) ax.grid.color = t.grid;
      ax.ticks.color = ax.ticks.color && /^#[0-9a-fA-F]{6}$/.test(ax.ticks.color) ? t.text : ax.ticks.color || t.text;
      // overwrite hard-coded slate colors
      if (ax.ticks.color === '#94a3b8' || ax.ticks.color === '#64748b') ax.ticks.color = t.text;
      if (ax.title && _CHART_SLATE.includes(ax.title.color)) ax.title.color = t.text;
    });
  }
  // Legend / title text hard-coded to a light-mode slate: #64748b is 3.65:1 on
  // the dark card, #334155 1.8:1, and #94a3b8 is 2.56:1 on white. Theme ink instead.
  const lab = cfg.options.plugins?.legend?.labels;
  if (lab && _CHART_SLATE.includes(lab.color)) lab.color = t.ink;
  const ttl = cfg.options.plugins?.title;
  if (ttl && _CHART_SLATE.includes(ttl.color)) ttl.color = t.ink;
  if (cfg.options.plugins?.datalabels?.color === '#1e293b') {
    cfg.options.plugins.datalabels.color = t.labelStrong;
  }
}
// ── At-a-glance layer (every chart) ───────────────────────────────────────────
// The formal theme (#223) left every chart a flat block the reader had to hover
// to read. This one plugin, registered globally in ensureChartSetup(), puts the
// answer back on the chart with no per-chart code:
//   depth   bars + filled line areas get a gradient along the value axis (strong
//           at the tip, fading to the baseline), and a lone solid line gets a wash
//   labels  the latest value at the end of each line (<= 4 lines) and the peak of
//           the primary series; time-series bars also label the latest bar
//   avg     a hairline at the mean of a single primary series (>= 4 points)
//   centre  a doughnut shows its total (or its top share, when values are %)
//   headline one sentence above the chart: latest vs previous, highest/lowest
//           since, a run of rises/falls, vs the average (or best/worst + spread
//           for a category chart). Skipped when the card already states numbers.
//   MTD     the period still in progress (this month on a monthly axis, today on
//           a daily one) is drawn hollow/dashed, tagged MTD, and left out of the
//           peak, the average and every headline comparison.
//   weekend Sat-Sun bands behind a daily axis.
// Dates come from the x labels (2026-09-24, 09-24, Sep 2026, ก.ย. 69, ...) or
// from options.plugins.glance.dates (ISO strings) when the labels are bare days.
// A dataset the datalabels plugin already numbers is never labelled again.
// Opt out per chart with options.plugins.glance = false, or one part with
// glance: { depth:false, labels:false, avg:false, centre:false, headline:false,
// mtd:false, weekend:false }.
const _GLANCE_TIME_RE = /^\d{4}(-\d{2})?|^\d{1,2}[-\/]\d{1,2}|^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)|^(ม\.?ค|ก\.?พ|มี\.?ค|เม\.?ย|พ\.?ค|มิ\.?ย|ก\.?ค|ส\.?ค|ก\.?ย|ต\.?ค|พ\.?ย|ธ\.?ค)|^(w|wk)\s?\d|^สัปดาห์|^q[1-4]/i;
const _GLANCE_SKIP_RE = /band|\blo\b|\bhi\b|bound|range|ช่วง/i;
const _GLANCE_REF_RE  = /เฉลี่ย|avg|average|mean|เป้า|target/i;

function _glanceRgba(c) {
  if (typeof c !== 'string') return null;
  let m = c.trim().match(/^#([0-9a-f]{3}|[0-9a-f]{6})([0-9a-f]{2})?$/i);
  if (m) {
    let h = m[1];
    if (h.length === 3) h = h.split('').map(x => x + x).join('');
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16),
            m[2] ? parseInt(m[2], 16) / 255 : 1];
  }
  m = c.match(/^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:[\s,\/]+([\d.]+%?))?\s*\)$/i);
  if (m) return [+m[1], +m[2], +m[3], m[4] == null ? 1 : (m[4].endsWith('%') ? parseFloat(m[4]) / 100 : +m[4])];
  return null;
}
const _glanceCss = (r, a) => `rgba(${r[0]},${r[1]},${r[2]},${Math.max(0, Math.min(1, a)).toFixed(3)})`;

function _glanceCompact(v) {
  const a = Math.abs(v), s = v < 0 ? '-' : '';
  if (a >= 1e6) return s + (a / 1e6).toFixed(2) + 'M';
  if (a >= 1e4) return s + (a / 1e3).toFixed(1) + 'K';
  if (a >= 100) return s + Math.round(a).toLocaleString('en-US');
  if (a >= 1 || a === 0) return s + parseFloat(a.toFixed(1));
  return s + parseFloat(a.toFixed(2));
}

// A label in the axis's own unit: '%' and a '฿' prefix are read off the scale's
// tick callback; the number itself is always the compact form (a tick callback
// may round to the axis step, which is too coarse for the one value we print).
function _glanceFmt(scale, v) {
  let pre = '', suf = '';
  const cb = scale && scale.options && scale.options.ticks && scale.options.ticks.callback;
  if (typeof cb === 'function') {
    try {
      const s = String(cb.call(scale, v, 0, [{ value: v }]) ?? '');
      if (/%\s*$/.test(s)) suf = '%';
      if (/^\s*฿/.test(s)) pre = '฿';
    } catch (e) { /* keep the bare number */ }
  }
  return pre + _glanceCompact(v) + suf;
}

function _glanceTheme(chart) {
  const body = document.body.className;
  const g = chart.$glance || (chart.$glance = {});
  if (g.key !== body) {
    g.key = body;
    g.theme = chartTheme(chart.canvas.closest('[data-force-light]') ? 'light' : undefined);
    g.surface = g.theme.bg;
    for (let el = chart.canvas.parentElement; el && el !== document.documentElement; el = el.parentElement) {
      const bg = getComputedStyle(el).backgroundColor;
      const rgba = _glanceRgba(bg);
      if (rgba && rgba[3] > 0.9) { g.surface = bg; break; }
    }
  }
  return g;
}

function _glanceOpt(chart, part) {
  const o = chart.options.plugins && chart.options.plugins.glance;
  return !(o && o[part] === false);
}

function _glanceLabelled(chart, ds) {
  if (!Chart.registry.plugins.get('datalabels')) return false;
  if (ds.datalabels === false) return false;
  let d = ds.datalabels && ds.datalabels.display;
  if (d === undefined) {
    const po = chart.config.options && chart.config.options.plugins && chart.config.options.plugins.datalabels;
    if (po === false) return false;
    d = po && po.display;
  }
  if (d === undefined) d = Chart.defaults.plugins.datalabels && Chart.defaults.plugins.datalabels.display;
  return d !== false;
}

const _glanceIsLine = (chart, ds) => (ds.type || chart.config.type) === 'line';
const _glanceIsBar  = (chart, ds) => (ds.type || chart.config.type) === 'bar';
const _glanceFinite = v => {
  const n = (v && typeof v === 'object') ? v.y : v;
  return (typeof n === 'number' && isFinite(n)) ? n : null;
};

// Which visible datasets get labels, and which one is "the" series (peak + avg).
// `loose` (the headline) also takes small and horizontal charts.
function _glancePlan(chart, loose) {
  const type = chart.config.type;
  if (type !== 'line' && type !== 'bar') return null;
  const horizontal = chart.options.indexAxis === 'y';
  const area = chart.chartArea;
  if (!loose && (!area || area.bottom - area.top < 90)) return null;   // sparklines: depth only
  const metas = chart.getSortedVisibleDatasetMetas();
  const stacked = metas.some(m => m.vScale && m.vScale.options && m.vScale.options.stacked);
  if (stacked || (horizontal && !loose)) return null;
  const lines = [], bars = [];
  metas.forEach(m => {
    const ds = chart.data.datasets[m.index];
    if (!ds || _GLANCE_SKIP_RE.test(ds.label || '')) return;
    if (!(ds.data || []).some(v => _glanceFinite(v))) return;   // an empty / all-zero overlay is not a series
    if (m.type === 'line' && (ds.borderWidth == null || ds.borderWidth >= 1)) lines.push(m);
    else if (m.type === 'bar') bars.push(m);
  });
  let primary = null;
  if (bars.length === 1) primary = bars[0];
  else if (!bars.length) {
    const solid = lines.filter(m => !(chart.data.datasets[m.index].borderDash || []).length);
    if (solid.length === 1) primary = solid[0];
  }
  const labels = (chart.data.labels || []).map(l => Array.isArray(l) ? l.join(' ') : String(l ?? ''));
  const timeLike = !horizontal && labels.length > 1 && (labels.filter(l => _GLANCE_TIME_RE.test(l.trim())).length >= labels.length * 0.6
                                          || labels.every(l => /^\d{1,2}$/.test(l.trim())));   // day-of-month axis
  return { lines: lines.length <= 4 ? lines : [], primary, timeLike };
}

function _glanceText(ctx, text, x, y, align, color, surface, size, weight) {
  ctx.font = `${weight} ${size}px ${Chart.defaults.font.family}`;
  ctx.textAlign = align;
  ctx.textBaseline = 'middle';
  if (surface) {   // a surface-coloured halo keeps the text clear of grid + lines
    ctx.lineJoin = 'round';
    ctx.lineWidth = 3;
    ctx.strokeStyle = surface;
    ctx.strokeText(text, x, y);
  }
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
}

function _glanceDot(ctx, x, y, color, surface) {
  ctx.beginPath();
  ctx.arc(x, y, 4, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = surface;
  ctx.stroke();
}

// A see-through colour (many charts fade their non-highlighted bars) is flattened onto the card surface first, so it keeps
// its tone but nothing shows through it.
const _glanceMix = (c, t, k) => [0, 1, 2].map(i => Math.round(c[i] + (t[i] - c[i]) * k)).concat(1);
// Lift a colour in HSL: +dl lightness (clamped to 0.86 so it never turns white) and
// saturation × ks (clamped to 1) — the pastel that keeps its hue.
function _glanceLift(c, dl, ks) {
  const r = c[0] / 255, g = c[1] / 255, b = c[2] / 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
  let h = 0, sat = 0, l = (mx + mn) / 2;
  if (mx !== mn) {
    const d = mx - mn;
    sat = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
    h = mx === r ? ((g - b) / d + (g < b ? 6 : 0)) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    h /= 6;
  }
  l = Math.min(0.86, l + dl); sat = Math.min(1, sat * ks);
  const f = (p, q, t) => { t = (t + 1) % 1; return t < 1 / 6 ? p + (q - p) * 6 * t : t < 1 / 2 ? q : t < 2 / 3 ? p + (q - p) * (2 / 3 - t) * 6 : p; };
  if (sat === 0) { const v = Math.round(l * 255); return [v, v, v, 1]; }
  const q = l < 0.5 ? l * (1 + sat) : l + sat - l * sat, pp = 2 * l - q;
  return [Math.round(f(pp, q, h + 1 / 3) * 255), Math.round(f(pp, q, h) * 255), Math.round(f(pp, q, h - 1 / 3) * 255), 1];
}
// One bar's fill: the tip colour easing to the base colour along the bar (so a short bar is still clearly its colour),
// drawn per element because each bar has its own length and its own kind (B1) or series colour.
function _glanceBarGrad(ctx, el, horizontal, tip, base) {
  const t = horizontal ? el.x : el.y, b = el.base;
  if (!isFinite(t) || !isFinite(b) || t === b) return base;
  const g = horizontal ? ctx.createLinearGradient(t, 0, b, 0) : ctx.createLinearGradient(0, t, 0, b);
  g.addColorStop(0, tip);
  g.addColorStop(1, base);
  return g;
}

// ── Dates behind the x labels ──
const _GLANCE_EN_MON = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'];
const _GLANCE_TH_MON = ['ม.ค','ก.พ','มี.ค','เม.ย','พ.ค','มิ.ย','ก.ค','ส.ค','ก.ย','ต.ค','พ.ย','ธ.ค'];
const _glanceCfg = chart => (chart.config.options && chart.config.options.plugins && chart.config.options.plugins.glance) || {};

// The last day with data (set by initDataCoverageNote); until it arrives, yesterday.
function _glanceLastDate() {
  const s = window.__dataLastDate;
  if (typeof s === 'string' && /^\d{4}-\d{2}-\d{2}/.test(s)) {
    const [y, m, d] = s.slice(0, 10).split('-').map(Number);
    return { y, m, d };
  }
  const t = new Date(Date.now() + 7 * 3600e3 - 864e5);   // Bangkok yesterday
  return { y: t.getUTCFullYear(), m: t.getUTCMonth() + 1, d: t.getUTCDate() };
}
// 2-digit years: <= 50 is 20xx (CE), above is 25xx (BE, as in "ก.ย. 69").
function _glanceYear(s) { const n = +s; return s.length <= 2 ? (n > 50 ? 2500 + n - 543 : 2000 + n) : (n > 2400 ? n - 543 : n); }
function _glanceParseLabel(l, ref) {
  l = l.trim();
  let m, mo;
  if ((m = l.match(/^(\d{4})-(\d{2})-(\d{2})/))) return { y: +m[1], m: +m[2], d: +m[3] };
  if ((m = l.match(/^(\d{4})-(\d{2})$/))) return { y: +m[1], m: +m[2], d: null };
  if ((m = l.match(/^(\d{2})-(\d{2})$/))) { mo = +m[1]; return { y: mo > ref.m ? ref.y - 1 : ref.y, m: mo, d: +m[2] }; }
  if ((m = l.match(/^(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{2,4})$/)) && (mo = _GLANCE_EN_MON.indexOf(m[2].toLowerCase()) + 1))
    return { y: _glanceYear(m[3]), m: mo, d: +m[1] };
  if ((m = l.match(/^([A-Za-z]{3})[a-z]*\.?\s+(\d{2,4})$/)) && (mo = _GLANCE_EN_MON.indexOf(m[1].toLowerCase()) + 1))
    return { y: _glanceYear(m[2]), m: mo, d: null };
  if ((m = l.match(/^(?:(\d{1,2})\s*)?([ก-๙]{1,3}\.[ก-๙]{1,2})\.?\s*(\d{2,4})?$/)) && (mo = _GLANCE_TH_MON.indexOf(m[2]) + 1))
    return { y: m[3] ? _glanceYear(m[3]) : (mo > ref.m ? ref.y - 1 : ref.y), m: mo, d: m[1] ? +m[1] : null };
  return null;
}

// { grain:'day'|'month'|null, dates, partial: index of the period still in progress (-1), note }
function _glanceInfo(chart) {
  return glancePeriods((chart.config.data && chart.config.data.labels) || [], _glanceCfg(chart));
}
// The same, for any list of period labels (heatmaps, tables): which one is still in progress.
function glancePeriods(labels, go) {
  go = go || {};
  const none = { grain: null, dates: null, partial: -1, note: '' };
  if (labels.length < 2) return none;
  const ref = _glanceLastDate();
  let dates = null;
  if (Array.isArray(go.dates) && go.dates.length === labels.length) dates = go.dates.map(x => _glanceParseLabel(String(x), ref));
  if (!dates || !dates.every(Boolean)) dates = labels.map(l => _glanceParseLabel(Array.isArray(l) ? l.join(' ') : String(l ?? ''), ref));
  if (!dates.every(Boolean)) return none;
  let grain = dates.every(d => d.d != null) ? 'day' : dates.every(d => d.d == null) ? 'month' : null;
  let partial = -1, note = '';
  // Period START dates (a bar per week / month / quarter / year, labelled with its first day) are not days: tell them by the spacing,
  // and the period in progress is the one that holds the last day with data and is not yet over (VZ2: never compared as a full one).
  if (grain === 'day' && dates.length >= 2) {
    const ut = d => Date.UTC(d.y, d.m - 1, d.d);
    const gaps = dates.slice(1).map((d, i) => (ut(d) - ut(dates[i])) / 864e5).sort((x, y) => x - y);
    const g = gaps[Math.floor(gaps.length / 2)];
    const kind = g === 7 ? 'week' : (g >= 28 && g <= 31) ? 'month' : (g >= 89 && g <= 92) ? 'quarter' : (g >= 365 && g <= 366) ? 'year' : null;
    if (kind) {
      grain = kind;
      const endOf = d => kind === 'week' ? ut(d) + 7 * 864e5 : Date.UTC(d.y, d.m - 1 + (kind === 'month' ? 1 : kind === 'quarter' ? 3 : 12), d.d);
      const rt = Date.UTC(ref.y, ref.m - 1, ref.d);
      for (let i = dates.length - 1; i >= 0; i--) {
        if (ut(dates[i]) <= rt) {
          if (rt < endOf(dates[i]) - 864e5) {
            partial = i;
            if (kind === 'month') note = `${ref.d}/${new Date(ref.y, ref.m, 0).getDate()} วัน`;
          }
          break;
        }
      }
      if (go.mtd === false) partial = -1;
      return { grain, dates, partial, note };
    }
  }
  if (grain === 'month') {
    const dim = new Date(ref.y, ref.m, 0).getDate();
    const i = dates.findIndex(d => d.y === ref.y && d.m === ref.m);
    if (i >= 0 && ref.d < dim) { partial = i; note = `${ref.d}/${dim} วัน`; }
  } else if (grain === 'day') {
    const t = new Date(Date.now() + 7 * 3600e3);   // a day still trading = today, Bangkok
    partial = dates.findIndex(d => d.y === t.getUTCFullYear() && d.m === t.getUTCMonth() + 1 && d.d === t.getUTCDate());
  }
  if (go.mtd === false) partial = -1;
  return { grain, dates, partial, note };
}
const _glancePartial = chart => (chart.$gi && chart.$gi.partial >= 0) ? chart.$gi.partial : -1;
const _glanceActual = ds => !(ds.borderDash || []).length && !_GLANCE_REF_RE.test(ds.label || '');
const _glanceTag = chart => (chart.$gi && chart.$gi.grain === 'day') ? ' วันนี้' : ' ถึงวันนี้';

// ── Headline (one sentence above the chart) ──
const _glanceLab = (chart, i) => {
  const l = (chart.data.labels || [])[i];
  let s = Array.isArray(l) ? l.join(' ') : String(l ?? '');
  const d = chart.$gi && chart.$gi.dates && chart.$gi.dates[i];
  if (d && d.d != null && /^\d{1,2}$/.test(s.trim())) s = `${d.d} ${_GLANCE_TH_MON[d.m - 1]}.`;
  return s.length > 24 ? s.slice(0, 23) + '…' : s;
};
const _glanceB = t => `<b style="color:var(--text-primary);font-weight:600">${t}</b>`;

// The sentence itself, shared by the chart headlines and the phone views
// (js/09c-mobile.js). labels[i] are display strings, vals[i] numbers or null.
// o: { partial: index still in progress (-1), unit: 'เดือน'|'วัน'|'ช่วง', note,
//      pct: values are percentages (compare in pp), fmt: v => string }
function glanceTrendSentence(labels, vals, o) {
  const P = o.partial == null ? -1 : o.partial, f = v => esc(o.fmt(v)), lab = i => esc(labels[i] ?? '');
  const unit = o.unit || 'ช่วง', pct = !!o.pct;
  const idx = vals.map((v, i) => i).filter(i => vals[i] != null && i !== P);
  if (idx.length < 3) return '';
  const parts = [];
  const L = idx[idx.length - 1], Pv = idx[idx.length - 2], v = vals[L], pv = vals[Pv];
  parts.push(_glanceB(`${lab(L)} ${f(v)}`));
  if (pct) {
    const d = v - pv;
    parts.push(`${d >= 0 ? '▲' : '▼'} ${Math.abs(d).toFixed(1)} pp จาก ${lab(Pv)}`);
  } else if (pv > 0) {
    const d = (v / pv - 1) * 100;
    parts.push(`${d >= 0 ? '▲' : '▼'} ${Math.abs(d).toFixed(1)}% จาก ${lab(Pv)}`);
  }
  const cv = idx.map(i => vals[i]), hi = Math.max(...cv), lo = Math.min(...cv);
  if (v === hi) parts.push(_glanceB('สูงสุดในช่วงที่แสดง'));
  else if (v === lo) parts.push(_glanceB('ต่ำสุดในช่วงที่แสดง'));
  else {
    // "highest since X": the last earlier point at least as high, if >= 3 periods back
    const k = idx.length - 1;
    for (let j = k - 1; j >= 0; j--) {
      const w = vals[idx[j]];
      if (v > pv ? w >= v : w <= v) {
        if (k - j >= 3) parts.push(`${v > pv ? 'สูงสุด' : 'ต่ำสุด'}ตั้งแต่ ${lab(idx[j])}`);
        break;
      }
    }
  }
  let run = 0;
  for (let j = idx.length - 1; j > 0 && v !== pv && Math.sign(vals[idx[j]] - vals[idx[j - 1]]) === Math.sign(v - pv); j--) run++;
  if (run >= 3) parts.push(`${v > pv ? 'เพิ่มขึ้น' : 'ลดลง'} ${run} ${unit}ติดกัน`);
  if (idx.length >= 4 && parts.length < 4) {
    const avg = cv.reduce((a, b) => a + b, 0) / cv.length;
    if (pct && Math.abs(v - avg) >= 0.5) parts.push(`${v > avg ? 'สูงกว่า' : 'ต่ำกว่า'}ค่าเฉลี่ย ${Math.abs(v - avg).toFixed(1)} pp`);
    else if (!pct && avg > 0 && Math.abs(v / avg - 1) >= 0.05) parts.push(`${v > avg ? 'สูงกว่า' : 'ต่ำกว่า'}ค่าเฉลี่ย ${Math.abs((v / avg - 1) * 100).toFixed(0)}%`);
    else if (!pct && avg > 0) parts.push('ใกล้ค่าเฉลี่ย');
  }
  parts.splice(4);
  if (P >= 0 && vals[P] != null) {
    parts.push(`<span style="color:var(--text-muted)">${lab(P)} ยังไม่ครบ${unit}${o.note ? ` (${esc(o.note)})` : ''} · ไม่นำมาเทียบ</span>`);
  }
  return parts.join(' · ');
}

// Best / worst + spread for a category list (branches, dayparts, menu items).
// o: { pct, fmt, below: true adds "n of N below the average" }
function glanceRangeSentence(labels, vals, o) {
  const f = v => esc(o.fmt(v)), lab = i => esc(labels[i] ?? '');
  const idx = vals.map((v, i) => i).filter(i => vals[i] != null);
  if (idx.length < 3) return '';
  let hi = idx[0], lo = idx[0];
  idx.forEach(i => { if (vals[i] > vals[hi]) hi = i; if (vals[i] < vals[lo]) lo = i; });
  if (vals[hi] === vals[lo]) return '';
  const parts = [`สูงสุด ${_glanceB(`${lab(hi)} ${f(vals[hi])}`)}`, `ต่ำสุด ${_glanceB(`${lab(lo)} ${f(vals[lo])}`)}`];
  if (o.pct) parts.push(`ห่างกัน ${(vals[hi] - vals[lo]).toFixed(1)} pp`);
  // a ratio reads only in a sane range ("1913 เท่า" against a near-empty bucket says nothing)
  else if (vals[lo] > 0 && vals[hi] / vals[lo] >= 1.5 && vals[hi] / vals[lo] <= 20) parts.push(`ต่างกัน ${(vals[hi] / vals[lo]).toFixed(1)} เท่า`);
  if (o.below) {
    const avg = idx.reduce((a, i) => a + vals[i], 0) / idx.length;
    parts.push(`${idx.filter(i => vals[i] < avg).length} จาก ${idx.length} ต่ำกว่าค่าเฉลี่ย ${f(avg)}`);
  }
  return parts.join(' · ');
}

function _glanceHeadline(chart) {
  if (!_glanceOpt(chart, 'headline')) return '';
  const id = chart.canvas.id || '';
  if (/^(zoom|drill|mgt)/i.test(id) || chart.canvas.closest('[data-force-light]')) return '';
  const plan = _glancePlan(chart, true);
  if (!plan || !plan.primary) return '';
  const pm = plan.primary, ds = chart.data.datasets[pm.index];
  const vals = (ds.data || []).map(_glanceFinite);
  const labels = vals.map((v, i) => _glanceLab(chart, i));
  const o = { fmt: v => _glanceFmt(pm.vScale, v), pct: _glanceFmt(pm.vScale, 1).endsWith('%') };
  if (!plan.timeLike) return glanceRangeSentence(labels, vals, o);
  const grain = chart.$gi && chart.$gi.grain;
  return glanceTrendSentence(labels, vals, Object.assign(o, {
    partial: _glancePartial(chart), note: chart.$gi && chart.$gi.note,
    unit: grain === 'month' ? 'เดือน' : grain === 'day' ? 'วัน' : 'ช่วง',
  }));
}

// Does the card already state numbers above this chart (a hand-written summary,
// KPI tiles)? Then a headline would only repeat it.
function _glanceHasSummary(canvas, card) {
  const scope = card || (canvas.parentElement && canvas.parentElement.parentElement);
  if (!scope) return false;
  const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
  let txt = '';
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!(n.compareDocumentPosition(canvas) & Node.DOCUMENT_POSITION_FOLLOWING)) break;   // past the canvas
    const p = n.parentElement;
    if (p && p.closest('.glance-line, button, select, option, script, style')) continue;
    txt += n.nodeValue + ' ';
  }
  return /[▲▼]|\d[\d.,]*\s*(%|K\b|M\b)|฿\s?\d/.test(txt);
}

const _glanceIsRow = el => {
  if (!el) return false;
  const cs = getComputedStyle(el);
  if (cs.display.includes('flex')) return !cs.flexDirection.startsWith('column');
  if (cs.display.includes('grid')) return cs.gridTemplateColumns.trim().split(/\s+/).length > 1;
  return false;
};

function _glanceSetHeadline(chart, html) {
  const cv = chart.canvas, id = cv.id;
  let el = chart.$glanceLine && chart.$glanceLine.isConnected ? chart.$glanceLine
         : (id ? document.querySelector(`.glance-line[data-for="${CSS.escape(id)}"]`) : null);
  if (html && !el) {
    const card = cv.closest('.card');
    if (_glanceHasSummary(cv, card)) html = '';
    else {
      // Above the canvas wrapper, never as a new column of a flex/grid row.
      let anchor = cv.parentElement && cv.parentElement !== card ? cv.parentElement : cv;
      while (anchor.parentElement && anchor.parentElement !== card && _glanceIsRow(anchor.parentElement)) anchor = anchor.parentElement;
      if (!anchor.parentElement || _glanceIsRow(anchor.parentElement)) html = '';
      else {
        el = document.createElement('div');
        el.className = 'glance-line';
        if (id) el.dataset.for = id;
        el.style.cssText = 'font-size:12px;line-height:1.45;color:var(--text-secondary);margin:0 0 6px';
        anchor.parentElement.insertBefore(el, anchor);
      }
    }
  }
  chart.$glanceLine = el || null;
  if (!el) return;
  if (!html) { el.remove(); chart.$glanceLine = null; return; }
  if (el.innerHTML !== html) el.innerHTML = html;
}

// ── B1 (VZ2) ────────────────────────────────────────────────────────────────
// Config-time test for the one-series period bar chart that gets the B1 look: a vertical, unstacked bar chart with ONE visible
// dataset on a period axis (days, weeks, months) and at least 5 periods. Multi-series, stacked, horizontal and category charts keep
// the categorical palette; opt out with options.plugins.glance = { b1: false }.
function glanceB1Eligible(cfg) {
  const o = (cfg && cfg.options) || {}, go = o.plugins && o.plugins.glance;
  if (go === false || (go && go.b1 === false)) return false;
  if (!cfg || cfg.type !== 'bar' || o.indexAxis === 'y') return false;
  if (o.scales && Object.values(o.scales).some(s => s && s.stacked)) return false;
  const sets = ((cfg.data && cfg.data.datasets) || []).filter(ds => ds && !ds.hidden);
  if (sets.length !== 1 || (sets[0].type && sets[0].type !== 'bar') || _GLANCE_SKIP_RE.test(sets[0].label || '')) return false;
  if ((sets[0].data || []).filter(v => _glanceFinite(v) != null).length < 5) return false;
  const labels = ((cfg.data && cfg.data.labels) || []).map(l => Array.isArray(l) ? l.join(' ') : String(l ?? ''));
  return labels.length >= 5 && (labels.filter(l => _GLANCE_TIME_RE.test(l.trim())).length >= labels.length * 0.6
                              || labels.every(l => /^\d{1,2}$/.test(l.trim())));
}
// No gridlines; the category axis names only its first, middle and last period; tick text 12 px.
function _glanceB1Axes(o) {
  o.scales = o.scales || {};
  const keys = Object.keys(o.scales);
  const xk = keys.find(k => k === 'x' || (o.scales[k] && o.scales[k].axis === 'x')) || 'x';
  const yk = keys.find(k => k !== xk && (k === 'y' || (o.scales[k] && o.scales[k].axis === 'y'))) || 'y';
  const x = o.scales[xk] = o.scales[xk] || {}, y = o.scales[yk] = o.scales[yk] || {};
  [x, y].forEach(sc => {
    sc.grid = Object.assign({}, sc.grid, { display: false });
    sc.ticks = sc.ticks || {};
    const f = sc.ticks.font || {};
    sc.ticks.font = Object.assign({}, f, { size: Math.max(12, f.size || 12), weight: f.weight || 600 });
  });
  const base = x.ticks.callback;
  x.ticks.autoSkip = false;
  delete x.ticks.maxTicksLimit;
  x.ticks.maxRotation = 0;
  x.ticks.minRotation = 0;
  x.ticks.padding = Math.max(x.ticks.padding || 0, 12);   // room for the weekend dots between the bars and the labels
  x.ticks.callback = function (v, i, t) {
    const n = (t && t.length) || 0;
    const room = this && this.width ? this.width : 999;   // a narrow chart names only its first and last period
    if (n > 3 && i !== 0 && i !== n - 1 && (room < 260 || i !== Math.floor((n - 1) / 2))) return '';
    return base ? base.call(this, v, i, t) : this.getLabelForValue(v);
  };
}
// Old-palette series colours become the B colours (bars, lines and slices), legend swatches included.
function _glanceRecolour(cfg, dark) {
  if (!cfg || !['bar', 'line', 'doughnut', 'pie'].includes(cfg.type)) return;
  ((cfg.data && cfg.data.datasets) || []).forEach(ds => {
    ['backgroundColor', 'borderColor', 'pointBackgroundColor', 'pointBorderColor'].forEach(p => {
      const v = ds[p];
      if (typeof v === 'string') ds[p] = glanceBColour(v, dark);
      else if (Array.isArray(v)) ds[p] = v.map(c => glanceBColour(c, dark));
    });
    // theme B lines are 2.5 px (a line asked for 2 px, or left to the default, gets the B weight; a hairline or a thick one stays)
    if ((ds.type || cfg.type) === 'line' && (ds.borderWidth === 2 || ds.borderWidth === undefined)) ds.borderWidth = 2.5;
  });
}
const _glanceIsDark = chart => !(chart.canvas && chart.canvas.closest('[data-force-light]')) && document.body.classList.contains('dark');
// The B1 plan for one frame: the classifier over the series, its palette and the bar meta. Null when the chart is not B1 or has < 4 finished periods.
function _glanceB1Frame(chart, gi) {
  if (!chart.$b1) return null;
  const metas = chart.getSortedVisibleDatasetMetas();
  if (metas.length !== 1 || metas[0].type !== 'bar') return null;
  const vals = (chart.data.datasets[metas[0].index].data || []).map(_glanceFinite);
  // weekends only on a run of consecutive days (weekly bars labelled with their first day are not days)
  const ms = d => Date.UTC(d.y, d.m - 1, d.d);
  const daily = gi.grain === 'day' && gi.dates.length === vals.length && gi.dates.every((d, i) => i === 0 || ms(d) - ms(gi.dates[i - 1]) === 86400000);
  const wk = daily
    ? gi.dates.map(d => { const w = new Date(d.y, d.m - 1, d.d).getDay(); return w === 0 || w === 6; }) : [];
  const k = glanceB1Kinds(vals, { partial: _glancePartial(chart), weekend: wk });
  return k && Object.assign(k, { meta: metas[0], pal: _glanceIsDark(chart) ? B1_PAL.dark : B1_PAL.light });
}

// Where a chart label may go (pure, so it can be tested): `c` = { x, y, w, align }, `placed` = boxes already on the chart ({ x: centre, y, w }),
// `bounds` = { left, right }. The label is clamped INSIDE the bounds (never clipped at the edge); if it still overlaps a placed label
// (closer than a line of text vertically and than their half-widths horizontally) there is no spot and null comes back, so the caller
// tries its next candidate or drops it. Callers place the important labels first: latest (gold) > best > in-progress > average.
function glanceLabelSpot(placed, c, bounds) {
  let x = c.x;
  const w = c.w;
  if (c.align === 'center') x = Math.max(bounds.left + w / 2 + 2, Math.min(bounds.right - w / 2 - 2, x));
  else if (c.align === 'left') x = Math.max(bounds.left + 2, Math.min(bounds.right - w - 2, x));
  else x = Math.min(bounds.right - 2, Math.max(bounds.left + w + 2, x));
  const cx = c.align === 'left' ? x + w / 2 : c.align === 'right' ? x - w / 2 : x;
  if (placed.some(p => Math.abs(p.y - c.y) < 15 && Math.abs(p.x - cx) < (p.w + w) / 2 + 4)) return null;
  return { x, cx };
}

const GLANCE_PLUGIN = {
  id: 'glance',

  // Config-time: give a lone solid, unfilled line a wash (Filler needs `fill`
  // set before the first layout), and room on the right for end labels.
  beforeInit(chart) {
    const cfg = chart.config, o = cfg.options || {};
    const go = o.plugins && o.plugins.glance;
    if (go !== false) _glanceRecolour(cfg, _glanceIsDark(chart));
    chart.$b1 = glanceB1Eligible(cfg);
    if (chart.$b1) _glanceB1Axes(o);
    if (go === false || (cfg.type !== 'line' && cfg.type !== 'bar') || o.indexAxis === 'y') return;
    const sets = (cfg.data && cfg.data.datasets) || [];
    const yStacked = o.scales && o.scales.y && o.scales.y.stacked;
    if (cfg.type === 'line' && !(go && go.depth === false) && !yStacked) {
      const solid = sets.filter(ds => _glanceIsLine(chart, ds) && !ds.hidden && !(ds.borderDash || []).length
                                     && !_GLANCE_SKIP_RE.test(ds.label || ''));
      if (solid.length === 1 && solid[0].fill === undefined && typeof solid[0].borderColor === 'string') {
        const ds = solid[0];
        ds.fill = 'origin';
        if (ds.pointBackgroundColor === undefined) ds.pointBackgroundColor = ds.borderColor;
        if (ds.backgroundColor === undefined) ds.backgroundColor = ds.borderColor;
      }
    }
    if (go && go.labels === false) return;
    const ends = sets.filter(ds => _glanceIsLine(chart, ds) && !ds.hidden && !_GLANCE_SKIP_RE.test(ds.label || '')
                                   && !_glanceLabelled(chart, ds));
    if (!ends.length || ends.length > 4) return;
    const narrow = ((chart.canvas.parentElement && chart.canvas.parentElement.clientWidth) || 999) < 520;
    const n = (cfg.data.labels || []).length;
    const tag = _glanceInfo(chart).partial >= 0 ? ' ถึงวันนี้' : '';
    let need = 0;
    const ctx = chart.ctx;
    ctx.save();
    ctx.font = `700 12px ${Chart.defaults.font.family}`;
    (narrow ? ends.slice(0, 1) : ends).forEach(ds => {
      const d = ds.data || [];
      let i = d.length - 1;
      while (i >= 0 && _glanceFinite(d[i]) == null) i--;
      if (i < 0 || (n && i < n - 1)) return;   // ends before the last x: label sits above, no room needed
      need = Math.max(need, ctx.measureText(_glanceCompact(_glanceFinite(d[i])) + '%' + tag).width + 14);
    });
    ctx.restore();
    if (!need) return;
    o.layout = o.layout || {};
    const p = o.layout.padding;
    if (typeof p === 'number') o.layout.padding = { top: p, bottom: p, left: p, right: Math.max(p, need) };
    else if (p && typeof p === 'object') p.right = Math.max(p.right || 0, need);
    else o.layout.padding = { right: need };
  },

  // Dates + the period in progress; a line's segment into that point is dashed.
  beforeUpdate(chart) {
    chart.$gi = _glanceInfo(chart);
    const p = chart.$gi.partial;
    ((chart.config.data && chart.config.data.datasets) || []).forEach(ds => {
      if (!_glanceIsLine(chart, ds)) return;
      if (!('$glanceSeg' in ds)) Object.defineProperty(ds, '$glanceSeg', { value: ds.segment, enumerable: false });
      const own = ds.$glanceSeg;
      if (p < 1 || !_glanceActual(ds) || _glanceFinite((ds.data || [])[p]) == null) { ds.segment = own; return; }
      ds.segment = Object.assign({}, own, {
        borderDash: c => c.p1DataIndex === p ? [4, 3]
                       : (own && typeof own.borderDash === 'function' ? own.borderDash(c) : own && own.borderDash),
      });
    });
  },

  afterUpdate(chart) {
    try { _glanceSetHeadline(chart, _glanceHeadline(chart)); } catch (e) { /* the chart matters more than its caption */ }
  },

  afterDestroy(chart) {
    if (chart.$glanceLine) chart.$glanceLine.remove();
    chart.$glanceLine = null;
  },

  // Depth: swap flat fills for gradients for this frame only (restored after
  // the datasets draw, so tooltips/legends/hover keep the real colours).
  beforeDatasetsDraw(chart) {
    const restore = [];
    chart.$glanceRestore = restore;
    chart.$glanceHollow = [];
    chart.$glanceWeekend = [];
    const gi = chart.$gi || {};
    // B1 (one-series period bars): above / below the average, weekend, latest, best (see glanceB1Kinds).
    const b1 = _glanceB1Frame(chart, gi);
    chart.$glanceB1 = b1;
    // Weekend bands behind a daily axis (B1 marks weekends with a dot under the axis instead).
    if (!b1 && gi.grain === 'day' && gi.dates.length >= 7 && chart.options.indexAxis !== 'y' && _glanceOpt(chart, 'weekend')) {
      const xs = Object.values(chart.scales).find(s => s.axis === 'x');
      if (xs && xs.type === 'category') {
        const a = chart.chartArea, g = _glanceTheme(chart), ctx = chart.ctx;
        const step = Math.abs(xs.getPixelForValue(1) - xs.getPixelForValue(0));
        const wk = gi.dates.map(d => { const w = new Date(d.y, d.m - 1, d.d).getDay(); return w === 0 || w === 6; });
        ctx.save();
        for (let i = 0; i < wk.length; i++) {
          if (!wk[i]) continue;
          let j = i;
          while (j + 1 < wk.length && wk[j + 1]) j++;
          const x0 = Math.max(a.left, xs.getPixelForValue(i) - step / 2), x1 = Math.min(a.right, xs.getPixelForValue(j) + step / 2);
          ctx.fillStyle = g.key.includes('dark') ? 'rgba(148,163,184,0.09)' : 'rgba(100,116,139,0.07)';
          ctx.fillRect(x0, a.top, x1 - x0, a.bottom - a.top);
          if (x1 - x0 >= 22) chart.$glanceWeekend.push((x0 + x1) / 2);
          i = j;
        }
        ctx.restore();
      }
    }
    const P = _glancePartial(chart);
    if (_glanceOpt(chart, 'depth') || P >= 0) {
      const horizontal = chart.options.indexAxis === 'y';
      const seen = new Map();
      const swap = (opts, prop, val) => {
        if (!seen.has(opts)) seen.set(opts, new Map());
        const m = seen.get(opts);
        if (!m.has(prop)) { m.set(prop, opts[prop]); restore.push([opts, prop, opts[prop]]); }
        opts[prop] = val;
      };
      const depth = _glanceOpt(chart, 'depth');
      const surf = _glanceRgba(_glanceTheme(chart).surface) || [255, 255, 255, 1];
      chart.getSortedVisibleDatasetMetas().forEach(m => {
        const stacked = m.vScale && m.vScale.options && m.vScale.options.stacked;
        if (m.type === 'bar') {
          m.data.forEach((el, i) => {
            if (!el || el.active || !el.options) return;
            const own = el.options;
            const bk = b1 && m === b1.meta ? b1.kinds[i] : null;
            let rgba, cTip, cBase, edge = null;
            if (bk) {
              const key = (bk.tone === 'now' ? 't' : bk.tone === 'hi' ? 'a' : 'b') + (bk.tone !== 'now' && bk.wk ? 'w' : '');
              cTip = b1.pal[key + '1']; cBase = b1.pal[key + '2'];
              rgba = _glanceRgba(cBase);
              if (bk.tone === 'now') edge = b1.pal.tstroke;
            } else {
              rgba = _glanceRgba(own.backgroundColor);
              if (!rgba || !(rgba[3] > 0)) return;
              // theme B categorical bars: the series colour, a touch lighter at the tip (a see-through colour is flattened onto the card first)
              const solid = rgba[3] >= 0.999 ? rgba : _glanceMix(rgba, surf, 1 - rgba[3]);
              cTip = _glanceCss(_glanceLift(solid, 0.10, 1.05), 1); cBase = _glanceCss(solid, 1);
            }
            // A per-bar copy of the options: bars of one dataset share one options object, and every bar here has its own fill.
            const copy = Object.assign(Object.create(Object.getPrototypeOf(own)), own);
            restore.push([el, 'options', own]);
            el.options = copy;
            if (i === P) {
              // The period in progress: a pale fill + dashed outline (drawn after the bars).
              copy.backgroundColor = _glanceCss(rgba, rgba[3] * 0.18);
              chart.$glanceHollow.push({ el, color: _glanceCss(rgba, Math.max(rgba[3], 0.7)) });
              return;
            }
            if (depth || bk) copy.backgroundColor = _glanceBarGrad(chart.ctx, el, horizontal, cTip, cBase);
            if (edge) { copy.borderColor = edge; copy.borderWidth = 1.6; }
          });
        } else if (depth && m.type === 'line' && m.dataset && m.dataset.options) {
          const lo = m.dataset.options;
          if (lo.fill !== true && lo.fill !== 'origin' && lo.fill !== 'start') return;
          const rgba = _glanceRgba(lo.borderColor);
          if (!rgba) return;
          const grad = chart.ctx.createLinearGradient(0, chart.chartArea.top, 0, chart.chartArea.bottom);
          grad.addColorStop(0, _glanceCss(rgba, 0.24));
          grad.addColorStop(1, _glanceCss(rgba, 0.01));
          swap(lo, 'backgroundColor', grad);
        }
      });
    }
    // Average hairline sits under the marks.
    const plan = _glanceOpt(chart, 'avg') ? _glancePlan(chart) : null;
    chart.$glanceAvg = null;
    // Only on a time axis: the mean of histogram buckets or day-of-week slots says nothing.
    if (plan && plan.primary && plan.timeLike && !(chart.options.plugins.annotation && Object.keys(chart.options.plugins.annotation.annotations || {}).length)
        && !chart.data.datasets.some(ds => _GLANCE_REF_RE.test(ds.label || ''))) {
      const P = _glancePartial(chart);
      const vals = (chart.data.datasets[plan.primary.index].data || []).map(_glanceFinite).filter((v, i) => v != null && i !== P);
      if (vals.length >= 4) {
        const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
        const sc = plan.primary.vScale, y = sc.getPixelForValue(avg);
        const a = chart.chartArea;
        if (y > a.top + 2 && y < a.bottom - 2) {
          const g = _glanceTheme(chart), ctx = chart.ctx;
          ctx.save();
          ctx.beginPath();
          ctx.setLineDash([5, 3]);
          ctx.lineWidth = 1.4;
          ctx.strokeStyle = (_glanceIsDark(chart) ? B1_PAL.dark : B1_PAL.light).avg;
          ctx.moveTo(a.left, Math.round(y) + 0.5);
          ctx.lineTo(a.right, Math.round(y) + 0.5);
          ctx.stroke();
          ctx.restore();
          chart.$glanceAvg = { y, text: 'เฉลี่ย ' + _glanceFmt(sc, avg) };
        }
      }
    }
  },

  afterDatasetsDraw(chart) {
    const hollow = chart.$glanceHollow || [];
    if (hollow.length) {
      const ctx = chart.ctx;
      ctx.save();
      ctx.setLineDash([3, 3]);
      ctx.lineWidth = 1.25;
      hollow.forEach(({ el, color }) => {
        const p = el.getProps(['x', 'y', 'base', 'width', 'height', 'horizontal'], true);
        ctx.strokeStyle = color;
        if (p.horizontal) ctx.strokeRect(Math.min(p.base, p.x) + 0.5, p.y - p.height / 2 + 0.5, Math.abs(p.x - p.base) - 1, p.height - 1);
        else ctx.strokeRect(p.x - p.width / 2 + 0.5, Math.min(p.y, p.base) + 0.5, p.width - 1, Math.abs(p.base - p.y) - 1);
      });
      ctx.restore();
    }
    chart.$glanceHollow = null;
    (chart.$glanceRestore || []).forEach(([o, p, v]) => { o[p] = v; });
    chart.$glanceRestore = null;
    const wkCaps = chart.$glanceWeekend || [];
    if (!_glanceOpt(chart, 'labels') && !chart.$glanceAvg && !wkCaps.length && !chart.$glanceB1) return;
    const g = _glanceTheme(chart), ctx = chart.ctx, a = chart.chartArea;
    ctx.save();
    const placed = [];
    const b1 = chart.$glanceB1;
    if (b1) {   // B1: a dot under the axis for each weekend period
      ctx.fillStyle = g.theme.textDim;
      b1.kinds.forEach((k, i) => {
        const el = b1.meta.data[i];
        if (!k || !k.wk || !el || el.skip) return;
        ctx.beginPath();
        ctx.arc(el.x, a.bottom + 6, 2, 0, Math.PI * 2);
        ctx.fill();
      });
    }
    const plan = _glanceOpt(chart, 'labels') ? _glancePlan(chart) : null;
    // A narrow chart (tablet, the full dashboard on a phone) keeps one series'
    // labels and smaller type, so the end labels do not pile up.
    const narrow = chart.width < 520, fs = 12;
    {
      const put = (text, x, y, align, color, size, weight, halo = true) => {
        ctx.font = `${weight} ${size}px ${Chart.defaults.font.family}`;
        const w = ctx.measureText(text).width;
        // A label never spills over the y-axis ticks or off the canvas edge, and never lands on a more important one.
        const spot = glanceLabelSpot(placed, { x, y, w, align }, { left: a.left, right: chart.width });
        if (!spot) return false;
        placed.push({ x: spot.cx, y, w });
        _glanceText(ctx, text, spot.x, y, align, color, halo ? g.surface : null, size, weight);
        return true;
      };
      const n = (chart.data.labels || []).length;
      // 1) latest value at the end of each line
      let ends = plan ? plan.lines : [];
      if (narrow && ends.length > 1) ends = ends.filter(m => m === plan.primary).concat(plan.primary ? [] : ends.slice(0, 1));
      ends.forEach(m => {
        const ds = chart.data.datasets[m.index];
        if (_glanceLabelled(chart, ds)) return;
        let i = m.data.length - 1;
        while (i >= 0 && (_glanceFinite(ds.data[i]) == null || !m.data[i] || m.data[i].skip)) i--;
        if (i < 1) return;
        const el = m.data[i], v = _glanceFinite(ds.data[i]);
        const color = typeof ds.borderColor === 'string' ? ds.borderColor : g.theme.text;
        const text = _glanceFmt(m.vScale, v) + (i === _glancePartial(chart) && _glanceActual(ds) ? _glanceTag(chart) : '');
        const atEnd = !n || i >= n - 1;
        const ok = atEnd ? put(text, el.x + 8, el.y, 'left', g.theme.labelStrong, fs, 700)
                         : put(text, el.x, el.y - 12 < 9 ? el.y + 13 : el.y - 12, 'center', g.theme.labelStrong, fs, 700);
        if (ok) _glanceDot(ctx, el.x, el.y, color, g.surface);
      });
      // 2) the primary series' peak (+ the latest bar on a time axis)
      const pm = plan && plan.primary;
      if (pm && !_glanceLabelled(chart, chart.data.datasets[pm.index])) {
        const ds = chart.data.datasets[pm.index];
        const vals = (ds.data || []).map(_glanceFinite);
        let hi = -1;
        const P = _glancePartial(chart);
        vals.forEach((v, i) => { if (v != null && i !== P && (hi < 0 || v > vals[hi])) hi = i; });
        const marks = [];
        let last = vals.length - 1;
        while (last >= 0 && vals[last] == null) last--;
        if (pm.type === 'bar' && plan.timeLike) {
          if (b1 && b1.latest !== last) marks.push(b1.latest);   // the gold bar always carries its value
          else if (last >= 0) marks.push(last);
        }
        let lastLine = vals.length - 1;
        while (lastLine >= 0 && vals[lastLine] == null) lastLine--;
        if (hi >= 0 && vals[hi] > 0 && !(pm.type === 'line' && hi === lastLine) && !marks.includes(hi)) marks.push(hi);
        if (pm.type === 'bar' && plan.timeLike && b1 && b1.latest !== last && last >= 0 && !marks.includes(last)) marks.push(last);   // the period in progress comes after best
        marks.forEach(i => {
          const el = pm.data[i];
          if (!el || el.skip) return;
          const v = vals[i], text = _glanceFmt(pm.vScale, v) + (i === P ? _glanceTag(chart) : '');
          const up = v >= 0;
          if (pm.type === 'bar') {
            // A bar that reaches the canvas top carries its label inside the tip.
            const inside = up && el.y - 8 < 9;
            const fill = _glanceRgba(Array.isArray(ds.backgroundColor) ? ds.backgroundColor[i] : ds.backgroundColor);
            const lum = fill ? (0.299 * fill[0] + 0.587 * fill[1] + 0.114 * fill[2]) / 255 : 0;
            put(text, el.x, inside ? el.y + 11 : up ? el.y - 8 : el.y + 9, 'center',
                inside ? (fill && lum > 0.6 ? '#0f172a' : '#ffffff') : (b1 && i === b1.latest ? b1.pal.gi : g.theme.labelStrong), fs, 700, !inside);
          } else if (put(text, el.x, el.y - 12 < 9 ? el.y + 13 : el.y - 12, 'center', g.theme.labelStrong, fs, 700)) {
            _glanceDot(ctx, el.x, el.y, typeof ds.borderColor === 'string' ? ds.borderColor : g.theme.text, g.surface);
          }
        });
      }
      // 3) the average's value goes where it collides with nothing (left edge, then right edge, above its line, then below); else the headline says it
      if (chart.$glanceAvg) {
        ctx.font = `700 12px ${Chart.defaults.font.family}`;
        const txt = chart.$glanceAvg.text, ay = chart.$glanceAvg.y;
        for (const [x, y, al] of [[a.left + 4, ay - 8, 'left'], [a.left + 4, ay + 9, 'left'], [a.right - 4, ay - 8, 'right'], [a.right - 4, ay + 9, 'right']]) {
          if (put(txt, x, y, al, g.theme.text, 12, 700)) break;
        }
      }
      // 4) name the weekend bands once, wherever the caption does not cover a label
      for (const cx of wkCaps) if (put('ส-อา', cx, a.top + 7, 'center', g.theme.textDim, 12, 600, false)) break;
    }
    ctx.restore();
  },

  // Doughnut centre: the total, or the top slice's share when the values are
  // already percentages (they sum to ~100, so a "total" would say nothing).
  afterDraw(chart) {
    if (chart.config.type !== 'doughnut' || !_glanceOpt(chart, 'centre')) return;
    if ((chart.config.plugins || []).length) return;   // a chart with its own inline plugin draws its own centre
    const meta = chart.getDatasetMeta(0), arc = meta && meta.data && meta.data[0];
    if (!arc || !arc.innerRadius || arc.innerRadius < 30) return;
    const ds = chart.data.datasets[0], vals = (ds.data || []).map(_glanceFinite);
    let sum = 0, top = -1;
    vals.forEach((v, i) => {
      if (v == null || v <= 0 || !chart.getDataVisibility(i)) return;
      sum += v;
      if (top < 0 || v > vals[top]) top = i;
    });
    if (top < 0 || sum <= 0) return;
    const pctData = Math.abs(sum - 100) < 1.5 && vals.every(v => v == null || v <= 100);
    const big = pctData ? `${parseFloat((vals[top] / sum * 100).toFixed(1))}%` : _glanceCompact(sum);
    let sub = pctData ? String((chart.data.labels || [])[top] ?? '') : 'รวม';
    if (sub.length > 14) sub = sub.slice(0, 13) + '…';
    const g = _glanceTheme(chart), ctx = chart.ctx;
    const r = arc.innerRadius, size = Math.max(12, Math.min(22, Math.round(r * 0.38)));
    ctx.save();
    ctx.font = `700 ${size}px ${Chart.defaults.font.family}`;
    if (ctx.measureText(big).width > r * 1.7) { ctx.restore(); return; }
    _glanceText(ctx, big, arc.x, arc.y - (sub ? size * 0.32 : 0), 'center', g.theme.labelStrong, g.surface, size, 700);
    if (sub) _glanceText(ctx, sub, arc.x, arc.y + size * 0.62, 'center', g.theme.text, g.surface, Math.max(12, Math.round(size * 0.5)), 600);
    ctx.restore();
  },
};

let activeTab       = 'home';
let _lastTabLoadAt  = 0;      // the open tab's last load (foreground refresh on a phone, 03-tabs-main.js)

// ── Lazy slices (2026-10-08). index.html loads five slices eagerly — 01-core,
// 03-tabs-main, 06-features, 09b-cost-pnl, 09c-mobile: the login, the boot and the phone
// summary. The other seventeen (the desktop tabs' code, ~1.6 MB) sit in index.html as inert
// <script type="text/plain" class="lazy-slice" data-lazy="/js/…?v=hash"> tags, in the same
// order as before; this turns them into real scripts one after another (async=false keeps
// the order; the server's ?v= makes them immutable-cached). init() awaits it on a desktop
// and ensureDesktopBooted() on "เต็ม →", so no tab loader runs before its slice exists; a
// phone that stays on the summary never downloads or parses them (2.15 MB → 0.57 MB).
let _slicesP = null;
function ensureSlicesLoaded() {
  if (_slicesP) return _slicesP;
  _slicesP = (async () => {
    // init() runs from the end of 09c-mobile.js while the parser is still on the tags
    // after it — wait for the whole document, or the later tags are not in the DOM yet
    // (first try loaded 6 of 15 and feedback/bomcenter were "not defined")
    if (document.readyState === 'loading') await new Promise(r => document.addEventListener('DOMContentLoaded', r, { once: true }));
    const tags = [...document.querySelectorAll('script.lazy-slice[data-lazy]')];
    for (const t of tags) await _loadSliceTag(t);
    document.body.classList.add('slices-loaded');
  })().catch(e => { _slicesP = null; throw e; });   // a failed download is retried on the next call
  return _slicesP;
}
// One slice tag becomes one script, once: the whole load above and the phone shell (which wants js/18-launcher.js alone, 09c-mobile.js
// phEnsureLauncher) share this promise, so a slice is never run twice (its top-level `let`s would throw).
const _sliceLoads = new Map();
function _loadSliceTag(t) {
  const src = t.dataset.lazy;
  if (!src || t.dataset.loaded) return Promise.resolve();
  let p = _sliceLoads.get(src);
  if (!p) {
    p = new Promise((res, rej) => {
      const s = document.createElement('script');
      s.src = src; s.async = false;
      s.onload = () => { t.dataset.loaded = '1'; res(); };
      s.onerror = () => { _sliceLoads.delete(src); rej(new Error('slice failed to load: ' + src)); };
      document.head.appendChild(s);
    });
    _sliceLoads.set(src, p);
  }
  return p;
}
let activeSubtab    = 'mostOrdered';
let activeOpsSubtab = 'cohort';
let chartInstances = {};    // name → Chart instance
// allMonths is always an array — enforced by getter/setter
let _allMonths = [];
Object.defineProperty(window, 'allMonths', {
  get() { return _allMonths; },
  set(v) { _allMonths = Array.isArray(v) ? v : []; },
  configurable: true
});
let costModalSkuMap = {};
const _tabLastKey = {};   // tab name → last filter key used
function _filterKey() { return `${getBranch()}|${getMonthFrom()}|${getMonthTo()}`; }
let parsedCostRows  = [];

// cached data per tab
let cache = {};

// ── Security: HTML-escape any untrusted string before innerHTML interpolation.
// Branch names and SKU descriptions are user-entered / CSV-uploaded — never trust them.
// ── PR-M1: the shapes people read ─────────────────────────────────────────────
// One date format on screen: 9/10/2026, day chips พ. 9/10, months กันยายน 2026, times 13:15.
// ISO stays in the API, the Excel files and the database; these helpers only dress what a person reads.
const _TH_MONTHS = ['มกราคม','กุมภาพันธ์','มีนาคม','เมษายน','พฤษภาคม','มิถุนายน','กรกฎาคม','สิงหาคม','กันยายน','ตุลาคม','พฤศจิกายน','ธันวาคม'];
const _TH_WD_SHORT = ['อา.','จ.','อ.','พ.','พฤ.','ศ.','ส.'];
function _isoParts(iso) { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso == null ? '' : iso)); return m ? [+m[1], +m[2], +m[3]] : null; }
function fmtDay(iso) { const p = _isoParts(iso); return p ? `${p[2]}/${p[1]}/${p[0]}` : String(iso == null ? '' : iso); }
function fmtDayShort(iso) { const p = _isoParts(iso); return p ? `${_TH_WD_SHORT[new Date(Date.UTC(p[0], p[1] - 1, p[2])).getUTCDay()]} ${p[2]}/${p[1]}` : String(iso == null ? '' : iso); }
// 5.1: a cutoff written ONE way, after "ส่งใบสั่งซื้อภายใน": "พฤ. 15/10 เวลา 13:15" (the iso stamp's own clock is the branch's)
function fmtCutoff(iso) { const s = String(iso == null ? '' : iso); return /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(s) ? `${fmtDayShort(s.slice(0, 10))} เวลา ${s.slice(11, 16)}` : s; }
// 5.1: the time left: "อีก 2 วัน 19 ชม." from 24 hours up, hours only under 24 ("อีก 5 ชม."), minutes under an hour
function fmtLeft(hours) {
  const h = Math.max(Number(hours) || 0, 0);
  if (h < 1) return `อีก ${Math.round(h * 60)} นาที`;
  const whole = Math.floor(h), d = Math.floor(whole / 24), r = whole % 24;
  return d === 0 ? `อีก ${r} ชม.` : `อีก ${d} วัน${r ? ` ${r} ชม.` : ''}`;
}
// "17 ต.ค." in the current year, "17 ต.ค. 2025" otherwise (owner 2026-10-20: no raw 2026-10-17 on screen)
const _TH_MON_SHORT = ['ม.ค.','ก.พ.','มี.ค.','เม.ย.','พ.ค.','มิ.ย.','ก.ค.','ส.ค.','ก.ย.','ต.ค.','พ.ย.','ธ.ค.'];
function fmtDateTh(iso, withYear) {
  const p = _isoParts(iso);
  if (!p) return String(iso == null ? '' : iso);
  return `${p[2]} ${_TH_MON_SHORT[p[1] - 1]}${withYear || p[0] !== new Date().getFullYear() ? ' ' + p[0] : ''}`;
}
// "2026-W43" (ISO week) -> "19–25 ต.ค." / "28 ก.ย. – 4 ต.ค." (owner 2026-10-21: no week codes on screen)
function fmtWeekTh(code) {
  const m = /^(\d{4})-W(\d{1,2})$/.exec(String(code == null ? '' : code));
  if (!m) return String(code == null ? '' : code);
  const y = +m[1], w = +m[2];
  const jan4 = new Date(Date.UTC(y, 0, 4));
  const mon = new Date(jan4.getTime() + ((w - 1) * 7 - ((jan4.getUTCDay() + 6) % 7)) * 864e5);
  const sun = new Date(mon.getTime() + 6 * 864e5);
  const iso = d => d.toISOString().slice(0, 10);
  return mon.getUTCMonth() === sun.getUTCMonth()
    ? `${mon.getUTCDate()}–${fmtDateTh(iso(sun))}`
    : `${fmtDateTh(iso(mon))} – ${fmtDateTh(iso(sun))}`;
}
function fmtMonthTh(ym) { const m = /^(\d{4})-(\d{2})/.exec(String(ym == null ? '' : ym)); return m && _TH_MONTHS[+m[2] - 1] ? `${_TH_MONTHS[+m[2] - 1]} ${m[1]}` : String(ym == null ? '' : ym); }
// ── PR-PP1 · one period pattern (owner 2026-10-24, docs/PERIOD_PATTERN.md) ─────────────────────────────────────────
// <period-core> pure, no DOM: backend/test_periods.py runs this block in node beside backend/periods.py (parity)
//   periodRange(kind, anchorIso, {date_from, date_to, today}) = periods.resolve: kind day | week | month | year | range
//   ('mtd' = month). The anchor is the last closed day; a period running past it is to-date (its first day..the anchor,
//   n days) and both bases are their first n days: day -> 7 / 364 days back · week -> the week before / 364 days back ·
//   month -> 1..n of last month (clamped) / of the month a year back · year -> 1 Jan..the same date last year (both) ·
//   range -> the same number of days just before / the same dates a year back. A finished period compares whole.
//   -> {kind, cur:[f,t], prev:[f,t], ly:[f,t], days, complete, label_th, prev_label_th, ly_label_th, from, to, label}
//   (from/to/label = cur and its short label, the PR-SP shape). null for a bad date, an unknown kind or a period
//   that starts after the anchor. The PR-SP call periodRange('range', {from, to}) still works.
const _PR_MON = ['ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.', 'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.'];
const _PR_KINDS = ['day', 'week', 'month', 'year', 'range'];
function _prDay(s) { return /^\d{4}-\d{2}-\d{2}$/.test(String(s == null ? '' : s).slice(0, 10)) ? new Date(String(s).slice(0, 10) + 'T00:00:00Z') : null; }
function _prIso(d) { return d.toISOString().slice(0, 10); }
function _prAdd(d, n) { return new Date(d.getTime() + n * 864e5); }
function _prMlen(y, m) { return new Date(Date.UTC(y, m, 0)).getUTCDate(); }            // m = 1..12
function _prYmd(y, m, d) { return new Date(Date.UTC(y, m - 1, d)); }
function _prYearBack(d) { const y = d.getUTCFullYear() - 1, m = d.getUTCMonth() + 1; return _prYmd(y, m, Math.min(d.getUTCDate(), _prMlen(y, m))); }
function _prTh(d, yr) { return `${d.getUTCDate()} ${_PR_MON[d.getUTCMonth()]}` + (yr === false ? '' : ` ${d.getUTCFullYear() + 543}`); }
// '23 ต.ค. 2569' · '1–23 ต.ค. 2569' · '28 ก.ย.–3 ต.ค. 2569' · '29 ธ.ค. 2568–4 ม.ค. 2569' (Buddhist year, as periods.label_th)
function periodLabelTh(a, b) {
  const x = typeof a === 'string' ? _prDay(a) : a, y = typeof b === 'string' ? _prDay(b) : b;
  if (!x || !y) return '';
  if (x.getTime() === y.getTime()) return _prTh(y);
  if (x.getUTCFullYear() === y.getUTCFullYear() && x.getUTCMonth() === y.getUTCMonth()) return `${x.getUTCDate()}–${_prTh(y)}`;
  if (x.getUTCFullYear() === y.getUTCFullYear()) return `${_prTh(x, false)}–${_prTh(y)}`;
  return `${_prTh(x)}–${_prTh(y)}`;
}
function periodRange(kind, anchor, opts) {
  opts = opts || {};
  if (anchor && typeof anchor === 'object' && !(anchor instanceof Date)) {      // PR-SP: periodRange('range', {from, to})
    opts = Object.assign({}, opts, { date_from: anchor.from, date_to: anchor.to }); anchor = anchor.to;
  }
  if (kind === 'mtd') kind = 'month';
  if (!_PR_KINDS.includes(kind)) return null;
  let a = _prDay(anchor);
  if (!a) return null;
  if (opts.today) { const t = _prDay(opts.today); if (!t) return null; const y = _prAdd(t, -1); if (y < a) a = y; }
  let ps, pe;
  if (kind === 'range') {
    ps = _prDay(opts.date_from); pe = _prDay(opts.date_to);
    if (!ps || !pe || ps > pe) return null;
  } else {
    const p = opts.date_from != null && opts.date_from !== '' ? _prDay(opts.date_from) : a;
    if (!p) return null;
    const y = p.getUTCFullYear(), m = p.getUTCMonth() + 1;
    if (kind === 'day') { ps = pe = p; }
    else if (kind === 'week') { ps = _prAdd(p, -((p.getUTCDay() + 6) % 7)); pe = _prAdd(ps, 6); }
    else if (kind === 'month') { ps = _prYmd(y, m, 1); pe = _prYmd(y, m, _prMlen(y, m)); }
    else { ps = _prYmd(y, 1, 1); pe = _prYmd(y, 12, 31); }
  }
  if (ps > a) return null;
  const complete = pe <= a, cs = ps, ce = pe < a ? pe : a;
  const n = Math.round((ce.getTime() - cs.getTime()) / 864e5) + 1, len = Math.round((pe.getTime() - ps.getTime()) / 864e5) + 1;
  let prev, ly;
  if (kind === 'day' || kind === 'week') { prev = [_prAdd(cs, -7), _prAdd(ce, -7)]; ly = [_prAdd(cs, -364), _prAdd(ce, -364)]; }
  else if (kind === 'month') {
    const y = cs.getUTCFullYear(), m = cs.getUTCMonth() + 1, py = m > 1 ? y : y - 1, pm = m > 1 ? m - 1 : 12;
    prev = [_prYmd(py, pm, 1), _prYmd(py, pm, complete ? _prMlen(py, pm) : Math.min(n, _prMlen(py, pm)))];
    ly = [_prYmd(y - 1, m, 1), _prYmd(y - 1, m, complete ? _prMlen(y - 1, m) : Math.min(n, _prMlen(y - 1, m)))];
  } else if (kind === 'year') { prev = [_prYmd(cs.getUTCFullYear() - 1, 1, 1), _prYearBack(ce)]; ly = prev.slice(); }
  else { const p0 = _prAdd(ps, -len); prev = [p0, _prAdd(p0, n - 1)]; ly = [_prYearBack(cs), _prYearBack(ce)]; }
  const from = _prIso(cs), to = _prIso(ce);
  const short = (typeof fmtDateTh === 'function')
    ? (from === to ? fmtDateTh(to) : from.slice(0, 7) === to.slice(0, 7) ? `${+from.slice(8)}–${fmtDateTh(to)}` : `${fmtDateTh(from)}–${fmtDateTh(to)}`)
    : periodLabelTh(cs, ce);
  return { kind, cur: [from, to], prev: prev.map(_prIso), ly: ly.map(_prIso), days: n, complete,
    label_th: periodLabelTh(cs, ce), prev_label_th: periodLabelTh(prev[0], prev[1]), ly_label_th: periodLabelTh(ly[0], ly[1]),
    from, to, label: short };
}
// pct / pp = periods.pct / periods.pp: % change (null without a base above zero) · change of a ratio in points (จุด)
function periodPct(cur, base) {
  if (cur == null || base == null || cur === '' || base === '') return null;
  const c = Number(cur), b = Number(base);
  if (!isFinite(c) || !isFinite(b) || b <= 0) return null;
  return Math.round(((c - b) / b) * 100 * 10) / 10;
}
function periodPp(cur, base) {
  if (cur == null || base == null || cur === '' || base === '') return null;
  const c = Number(cur), b = Number(base);
  return isFinite(c) && isFinite(b) ? Math.round((c - b) * 10) / 10 : null;
}
// </period-core>

// periodBar(el, {kinds, value, onChange, range, key}) · the one period chip track: วัน · สัปดาห์ · เดือน · ปี · กำหนดเอง.
//   el     an element or its id · kinds  the kinds the figure supports (a month-only cost source passes ['month','year']:
//   the week chip is not drawn) · value  {kind, from, to} or a kind · range  true adds กำหนดเอง (จาก–ถึง + แสดง)
//   key    remembers the choice on this device (localStorage oshinei_period_<key>; a blocked storage is ignored)
//   onChange({kind, from, to}) runs on a chip tap and on แสดง. Returns {get(), set(value, silent), kinds}.
// 44 px targets and a scrolling track on a phone (.pbar, index.html). A TABS[].follow.period page follows the top bar's
// วัน / สัปดาห์ / เดือน / ปี (getPeriodKind, _syncExtraPeriod in js/07-home.js).
const _PBAR_LABEL = { day: 'วัน', week: 'สัปดาห์', month: 'เดือน', year: 'ปี', range: 'กำหนดเอง' };
const _pbars = {};
function periodBar(el, opt) {
  const box = typeof el === 'string' ? document.getElementById(el) : el;
  if (!box) return null;
  opt = opt || {};
  const kinds = (opt.kinds || ['day', 'week', 'month', 'year']).filter(k => k !== 'range' && _PR_KINDS.includes(k));
  if (opt.range) kinds.push('range');
  const lsKey = opt.key ? 'oshinei_period_' + opt.key : '';
  const norm = v => (typeof v === 'string' ? { kind: v, from: '', to: '' } : Object.assign({ kind: '', from: '', to: '' }, v || {}));
  let val = norm(opt.value);
  if (lsKey) { try { const s = JSON.parse(localStorage.getItem(lsKey) || 'null'); if (s && kinds.includes(s.kind)) val = norm(s); } catch (e) { /* storage blocked */ } }
  if (!kinds.includes(val.kind)) val.kind = kinds.includes('month') ? 'month' : kinds[0];
  const save = () => { if (lsKey) { try { localStorage.setItem(lsKey, JSON.stringify(val)); } catch (e) { /* storage blocked */ } } };
  const uid = 'pb' + Math.random().toString(36).slice(2, 8);
  box.classList.add('pbar-wrap');
  box.innerHTML = `<div class="pbar" role="group" aria-label="ช่วงเวลา">` +
    kinds.map(k => `<button type="button" class="gran-btn" data-k="${k}" aria-pressed="false">${_PBAR_LABEL[k]}</button>`).join('') + `</div>` +
    (opt.range ? `<div class="pbar-range" hidden><label for="${uid}f">จาก</label><input type="date" id="${uid}f" data-p="f"><label for="${uid}t">ถึง</label><input type="date" id="${uid}t" data-p="t"><button type="button" class="pbar-apply">แสดง</button><span class="pbar-err" role="alert"></span></div>` : '');
  const inp = w => /** @type {HTMLInputElement|null} */ (box.querySelector(`.pbar-range input[data-p="${w}"]`));
  const paint = () => {
    box.querySelectorAll('.pbar [data-k]').forEach(b => {
      const on = b.getAttribute('data-k') === val.kind;
      b.classList.toggle('gran-active', on); b.setAttribute('aria-pressed', on ? 'true' : 'false');
      const tr = /** @type {HTMLElement} */ (b.parentElement), bb = /** @type {HTMLElement} */ (b);   // a phone scrolls the track: keep the chosen chip in view
      if (on && tr && tr.scrollWidth > tr.clientWidth) tr.scrollLeft = Math.max(0, bb.offsetLeft - tr.offsetLeft - 8);
    });
    const rg = /** @type {HTMLElement|null} */ (box.querySelector('.pbar-range'));
    if (rg) {
      rg.hidden = val.kind !== 'range';
      const f = inp('f'), t = inp('t');
      if (f && val.from) f.value = val.from;
      if (t && val.to) t.value = val.to;
    }
  };
  const fire = () => { save(); paint(); if (typeof opt.onChange === 'function') opt.onChange(Object.assign({}, val)); };
  box.querySelectorAll('.pbar [data-k]').forEach(b => b.addEventListener('click', () => {
    val.kind = b.getAttribute('data-k') || val.kind;
    if (val.kind === 'range' && !(val.from && val.to)) { save(); paint(); return; }   // wait for จาก–ถึง
    fire();
  }));
  const ap = box.querySelector('.pbar-apply');
  if (ap) ap.addEventListener('click', () => {
    const f = inp('f')?.value || '', t = inp('t')?.value || '';
    const err = box.querySelector('.pbar-err');
    if (!f || !t) { if (err) err.textContent = 'ระบุวันที่ทั้งช่อง จาก และ ถึง'; return; }
    if (f > t) { if (err) err.textContent = 'วันที่ จาก ต้องไม่อยู่หลังวันที่ ถึง'; return; }
    if (err) err.textContent = '';
    val = { kind: 'range', from: f, to: t }; fire();
  });
  paint();
  const ctl = {
    get: () => Object.assign({}, val),
    set: (v, silent) => { const n = norm(v); if (!kinds.includes(n.kind)) return; val = n; if (silent) { save(); paint(); } else fire(); },
    kinds: kinds.slice(),
  };
  if (opt.key) _pbars[opt.key] = ctl;
  return ctl;
}
// periodCmpHtml(cur, prev, ly, {unit, upGood, range}) · the two comparison chips of one figure: เทียบช่วงก่อน · เทียบปีก่อน.
//   unit '%' (baht / qty: % change) | 'pp' (a ratio: the change in จุด) · upGood false for a cost (down = good ink)
//   range a periodRange() result: its compared windows go in the chip's tooltip and in the "?"; a year (prev = ly) shows one chip.
//   A base with no data shows "—" (never a made-up %).
function periodCmpHtml(cur, prev, ly, opt) {
  opt = opt || {};
  const unit = opt.unit === 'pp' ? 'pp' : '%', upGood = opt.upGood !== false, r = opt.range || null;
  const e2 = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
  const why = (r && r.reasons) || {};
  const chip = (label, base, span, k) => {
    const d = unit === 'pp' ? periodPp(cur, base) : periodPct(cur, base);
    const tip = r ? `${r.label_th} เทียบ ${span}` : '';
    if (d == null) return `<span class="pcmp pcmp-none"${tip ? ` title="${e2(tip + ' · ' + (why[k] || 'ไม่มีข้อมูลฐาน'))}"` : ''}>${label} —</span>`;
    const good = d === 0 ? null : (d > 0) === upGood;
    const ink = good == null ? 'var(--text-secondary)' : good ? 'var(--good-ink)' : 'var(--bad-ink)';
    const arrow = d > 0 ? '▲' : d < 0 ? '▼' : '';
    const num = `${arrow}${arrow ? ' ' : ''}${Math.abs(d).toLocaleString('th-TH', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}${unit === 'pp' ? ' จุด' : '%'}`;
    return `<span class="pcmp"${tip ? ` title="${e2(tip)}"` : ''}>${label} <b style="color:${ink}">${num}</b></span>`;
  };
  const sameBase = !!r && r.prev[0] === r.ly[0] && r.prev[1] === r.ly[1];
  const html = (sameBase ? '' : chip('เทียบช่วงก่อน', prev, r ? r.prev_label_th : '', 'prev')) + chip('เทียบปีก่อน', ly, r ? r.ly_label_th : '', 'ly');
  const q = (r && opt.q !== false) ? periodQ(r, unit) : '';
  return `<span class="pcmp-row">${html}${q}</span>`;
}
// periodQ(r, unit) · the "?" of a figure: the period on screen and the two compared windows (PR-PP2: also alone, beside a table)
function periodQ(r, unit) {
  if (!r || typeof uiQ !== 'function') return '';
  const e2 = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
  const sameBase = r.prev && r.ly && r.prev[0] === r.ly[0] && r.prev[1] === r.ly[1];
  // PR-PP3: a base that is not compared says why (r.reasons from the server: the data starts later - a year names the month,
  // 'ข้อมูลปีก่อนเริ่ม พ.ค. 2568' - or the day-level cost starts later), so "—" is never bare
  const why = r.reasons || {};
  const wl = k => why[k] ? ` · <span style="color:var(--text-secondary)">${e2(why[k])}</span>` : '';
  const txt = `<p>${e2(r.label_th)}${r.complete ? '' : ' (ถึงวันที่ปิดยอดล่าสุด)'}</p>` +
    (sameBase ? '' : `<p><b>เทียบช่วงก่อน</b> · ${e2(r.prev_label_th)}${wl('prev')}</p>`) + `<p><b>เทียบปีก่อน</b> · ${e2(r.ly_label_th)}${sameBase && !why.ly ? wl('prev') : wl('ly')}</p>` +
    `<p>${unit === 'pp' ? 'สัดส่วนเทียบเป็นจุด' : 'เทียบเป็นร้อยละ'} · ช่วงที่ยังไม่จบเทียบจำนวนวันเท่ากัน · ไม่มีข้อมูลฐานแสดง —</p>`;
  return uiQ('pcmp:' + r.kind + ':' + (r.cur || []).join('_') + ':' + (unit || '%') + ':' + Object.keys(why).join(''), 'ช่วงที่เทียบ', txt, 'pcmp-q');
}
// ── PR-PP2 · the sales pages read the server's `period` object ({kind, cur, prev, ly, days, complete, label_th,
// prev_label_th, ly_label_th, has_prev, has_ly}, backend periods.public) as the range of their chips.
// periodOf(d) · that object from a payload: d.period, or the older cur_range / prev_range / yoy_range trio.
function periodOf(d) {
  if (!d) return null;
  const p = d.period && typeof d.period === 'object' ? d.period : null;
  if (p && Array.isArray(p.cur)) return p;
  const cur = d.cur_range || d.range, prev = d.prev_range || d.comp_range, ly = d.yoy_range || d.ly_range;
  if (!Array.isArray(cur) || !Array.isArray(prev) || !Array.isArray(ly)) return null;
  return Object.assign({ kind: (p && p.kind) || 'range', complete: !!(p && p.complete) }, p || {}, {
    cur, prev, ly, label_th: periodLabelTh(cur[0], cur[1]), prev_label_th: periodLabelTh(prev[0], prev[1]), ly_label_th: periodLabelTh(ly[0], ly[1]) });
}
// periodSpan(r) · the period on screen: '1–19 ต.ค. 2569' + ' · ถึงวันที่ปิดยอดล่าสุด' while it is not finished
function periodSpan(r) { return r ? r.label_th + (r.complete ? '' : ' · ถึงวันที่ปิดยอดล่าสุด') : ''; }
// periodKpi({l, v, c, cur, prev, ly, unit, upGood, range, q, sub}) · a KPI tile: label, value, the two comparison chips
//   (prev / ly null = no base -> "—"). q: false leaves out the "?" (one "?" per row of tiles is enough).
function periodKpi(k) {
  const chips = periodCmpHtml(k.cur, k.prev, k.ly, { unit: k.unit, upGood: k.upGood, range: k.range, q: k.q });
  const ink = typeof textInk === 'function' ? textInk(k.c) : k.c;
  return `<div class="card p-4 pkpi" style="border-top:3px solid ${k.c}"><p class="text-[11px] uppercase tracking-wide" style="color:var(--text-muted)">${k.l}</p>` +
    `<p class="text-2xl font-bold" style="color:${ink}">${k.v}</p><div class="pkpi-cmp">${chips}</div>` +
    (k.sub ? `<p class="text-[11px] mt-1" style="color:var(--text-muted)">${k.sub}</p>` : '') + `</div>`;
}
// periodDelta(cur, base, {unit, upGood, title}) · one compact change for a table cell: ▲ 4.2% / ▼ 1.0 จุด / —
function periodDelta(cur, base, opt) {
  opt = opt || {};
  const d = opt.unit === 'pp' ? periodPp(cur, base) : periodPct(cur, base);
  const t = opt.title ? ` title="${String(opt.title).replace(/"/g, '&quot;')}"` : '';
  if (d == null) return `<span class="text-xs" style="color:var(--text-muted)"${t}>—</span>`;
  const good = d === 0 ? null : (d > 0) === (opt.upGood !== false);
  const ink = good == null ? 'var(--text-secondary)' : good ? 'var(--good-ink)' : 'var(--bad-ink)';
  return `<span class="text-xs font-semibold" style="color:${ink}"${t}>${d > 0 ? '▲' : d < 0 ? '▼' : ''} ${Math.abs(d).toFixed(1)}${opt.unit === 'pp' ? ' จุด' : '%'}</span>`;
}
// periodFmt(v, format) · a headline value: baht | int | pct | min | num
function periodFmt(v, f) {
  if (v == null || !isFinite(Number(v))) return '—';
  v = Number(v);
  if (f === 'baht') return fmt(v);
  if (f === 'int') return fmtN(Math.round(v));
  if (f === 'pct') return v.toFixed(1) + '%';
  if (f === 'min') return v.toFixed(0) + ' นาที';
  return v.toLocaleString('th-TH', { maximumFractionDigits: 2 });
}
// periodHeadline(hostId, page, {kinds, kind, pick}) · the headline figures of a page that had no comparison (PR-PP2): a period
//   chip track (periodBar, remembered per page as ph_<page>; TABS follow.period moves it with the top bar), the period on
//   screen, and one tile per figure with เทียบช่วงก่อน · เทียบปีก่อน (/api/period/headline). pick() = a day of the period
//   wanted (default: the last day of the top bar's period); a month-only source passes kinds ['month', 'year'].
const _phSeq = {};
async function periodHeadline(hostId, page) {
  const host = document.getElementById(hostId); if (!host) return null;
  if (!host.querySelector('.ph-tiles')) host.innerHTML = `<div class="ph-tiles grid grid-cols-2 md:grid-cols-4 gap-4 mb-4"></div><p class="ph-note text-xs mb-3" hidden></p>`;
  const g = gPeriodPick();                       // the top bar is the period (one control per page)
  const p = new URLSearchParams({ page, kind: g.kind });
  if (g.kind === 'range') { if (g.from) p.set('date_from', g.from); if (g.to) p.set('date_to', g.to); }
  else if (g.pick) p.set('pick', g.pick);
  if (getBranch()) p.set('branch', getBranch());
  if (getGroup()) p.set('group', getGroup());
  const seq = (_phSeq[page] = (_phSeq[page] || 0) + 1);
  let d;
  try { d = await api(`${API}/api/period/headline?${p}`); } catch (e) { return null; }
  if (seq !== _phSeq[page] || !d) return null;
  const r = d.period;
  periodChip(r, true);
  const note = host.querySelector('.ph-note');
  if (note) { note.hidden = !d.grain_note; note.innerHTML = d.grain_note && typeof uiQ === 'function'
    ? `<span style="color:var(--text-secondary)">ข้อมูลรายเดือน</span> ${uiQ('phn:' + page, 'ข้อมูลรายเดือน', `<p>${esc(d.grain_note)}</p>`, 'pcmp-q')}` : ''; }
  const tiles = host.querySelector('.ph-tiles');
  const COL = ['#4a70b0', '#0891b2', '#7a66b8', '#0d9488'];
  if (tiles) tiles.innerHTML = (d.figures || []).map((f, i) => periodKpi({ l: esc(f.label), v: periodFmt(f.cur, f.format), c: COL[i % COL.length],
    cur: f.cur, prev: f.prev, ly: f.ly, unit: f.unit, upGood: f.up_good, range: r, q: i === 0 })).join('');
  return d;
}
// periodPctDelta(pct, {upGood, title}) · the same for a change the server already worked out (null -> —)
function periodPctDelta(v, opt) {
  opt = opt || {};
  const t = opt.title ? ` title="${String(opt.title).replace(/"/g, '&quot;')}"` : '';
  if (v == null || !isFinite(Number(v))) return `<span class="text-xs" style="color:var(--text-muted)"${t}>—</span>`;
  v = Number(v);
  const good = v === 0 ? null : (v > 0) === (opt.upGood !== false);
  const ink = good == null ? 'var(--text-secondary)' : good ? 'var(--good-ink)' : 'var(--bad-ink)';
  return `<span class="text-xs font-semibold" style="color:${ink}"${t}>${v > 0 ? '▲' : v < 0 ? '▼' : ''} ${Math.abs(v).toFixed(1)}${opt.unit === 'pp' ? ' จุด' : '%'}</span>`;
}
// The top bar's period kind (the ONE period bar below): week | month | year | range
function getPeriodKind() { return _gp.kind; }

// ── PR-PP1 · the ONE period bar of the top bar (owner 2026-10-24): สัปดาห์ · เดือน · ปี · กำหนดเอง + ‹ ต.ค. 2569 › ──────────
// Replaces the วัน/สัปดาห์/เดือน/ไตรมาส/ปี toggle, the two date pickers, the two month pickers with 3ด./6ด./12ด. and the
// เลือกเร็ว row. The bar keeps {kind, pick, from, to}: pick = any day of the period wanted ('' = the period of the anchor,
// to date); from/to = the กำหนดเอง days. It WRITES the hidden fields the pages read (#gPeriodState: selMonthFrom / selMonthTo,
// selDateFrom / selDateTo) and currentGranularity (week -> weekly, กำหนดเอง -> daily, month / year -> monthly), so getMonthFrom,
// getMonthTo, qp(), summaryParams(), _filterKey and TABS[].follow keep working until PP2/PP3 move each page onto periodRange.
// The anchor is the last closed day with data (the coverage note's last day, never after yesterday on the Thai clock).
// 3 / 6 / 12 เดือน · ทั้งหมด is a chart option (ช่วงกราฟ, getTrendParams), shown only where a trend chart reads it.
// The bar's kind on a device that has not chosen one: the month to date (PR-PP3 - every sales and cost page now reads the bar's
// period; it was 'year' through PP1/PP2 so that no page that still read selMonthFrom / selMonthTo shrank on deploy).
const PERIOD_DEFAULT_KIND = 'month';
const _GP_LS = 'oshinei_gperiod', _GP_WIN_LS = 'oshinei_chartwin';
let _gp = (() => {
  try { const v = JSON.parse(localStorage.getItem(_GP_LS) || 'null'); if (v && ['day', 'week', 'month', 'year', 'range'].includes(v.kind)) return { kind: v.kind, pick: v.pick || '', from: v.from || '', to: v.to || '' }; } catch (e) { /* storage blocked */ }
  return { kind: PERIOD_DEFAULT_KIND, pick: '', from: '', to: '' };
})();
let _gpChartWin = (() => { try { const v = localStorage.getItem(_GP_WIN_LS); if (['3', '6', '12', 'all', 'q', 'y'].includes(v || '')) return v; } catch (e) { /* storage blocked */ } return '12'; })();
function _gpSave() { try { localStorage.setItem(_GP_LS, JSON.stringify(_gp)); localStorage.setItem(_GP_WIN_LS, _gpChartWin); } catch (e) { /* storage blocked */ } }
function _gpMonths() { return Array.isArray(window.allMonths) ? window.allMonths : []; }
function gPeriodAnchor() {
  const thToday = new Date(Date.now() + 7 * 3600e3).toISOString().slice(0, 10);
  const yday = _prIso(_prAdd(/** @type {Date} */ (_prDay(thToday)), -1));
  let a = String(window.__dataLastDate || '').slice(0, 10);
  if (!_prDay(a)) {
    const ms = _gpMonths(), last = ms[ms.length - 1];
    if (last) { const y = +last.slice(0, 4), m = +last.slice(5, 7); a = `${last}-${String(_prMlen(y, m)).padStart(2, '0')}`; }
  }
  return (!_prDay(a) || a > yday) ? yday : a;
}
function _gpFirstDay() { const ms = _gpMonths(); return ms.length ? ms[0] + '-01' : ''; }
// the windows of the bar's period (a periodRange result), null when it cannot be drawn (a range after the anchor)
function gPeriodResolve() {
  const a = gPeriodAnchor();
  if (_gp.kind === 'range') return (_gp.from && _gp.to) ? periodRange('range', a, { date_from: _gp.from, date_to: _gp.to }) : null;
  return periodRange(_gp.kind, a, _gp.pick ? { date_from: _gp.pick } : {}) || periodRange(_gp.kind, a);
}
function _gpSetSel(id, v) {
  const sel = /** @type {HTMLSelectElement|null} */ (document.getElementById(id)); if (!sel) return;
  if (v && ![...sel.options].some(o => o.value === v)) { const o = document.createElement('option'); o.value = v; o.textContent = fmtMonthTh(v); sel.appendChild(o); }
  sel.value = v;
}
function _gpSetVal(id, v) { const el = /** @type {HTMLInputElement|null} */ (document.getElementById(id)); if (el) el.value = v; }
// write the fields the pages read; no reload (gPeriodApply reloads)
function gPeriodWrite() {
  const r = gPeriodResolve();
  if (!r) return null;
  _gpSetVal('selDateFrom', r.cur[0]); _gpSetVal('selDateTo', r.cur[1]);
  const mf = r.cur[0].slice(0, 7), mt = r.cur[1].slice(0, 7), ms = _gpMonths().filter(m => m >= mf && m <= mt);
  _gpSetSel('selMonthFrom', ms[0] || mf); _gpSetSel('selMonthTo', ms[ms.length - 1] || mt);
  // eslint-disable-next-line no-global-assign
  currentGranularity = _gp.kind === 'week' ? 'weekly' : (_gp.kind === 'range' || _gp.kind === 'day') ? 'daily' : 'monthly';
  return r;
}
const _GP_MON_SHORT = ['ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.', 'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.'];
function gPeriodLabel(r) {
  if (!r) return '';
  if (_gp.kind === 'month') return `${_GP_MON_SHORT[+r.cur[0].slice(5, 7) - 1]} ${+r.cur[0].slice(0, 4) + 543}`;
  if (_gp.kind === 'year') return String(+r.cur[0].slice(0, 4) + 543);
  return r.label_th;
}
// กำหนดเอง tapped with no range yet: its จาก–ถึง is open while the pages stay on the period they show (until ใช้ช่วงนี้)
let _gpRangeOpen = false;
function gPeriodRender() {
  const r = gPeriodResolve(), shown = _gpRangeOpen ? 'range' : _gp.kind;
  document.querySelectorAll('#gPeriod .gp-kinds [data-k]').forEach(b => {
    const k = b.getAttribute('data-k'), on = k === shown;
    b.classList.toggle('gran-active', on); b.setAttribute('aria-pressed', on ? 'true' : 'false');
    /** @type {HTMLElement} */ (b).hidden = !_gpAllowed.includes(k || '');   // PR-PP2: only the kinds this page can show
  });
  if (!_gpChipServer) periodChip(r);
  const step = document.getElementById('gpStep'), rg = document.getElementById('gpRange');
  if (step) step.hidden = shown === 'range';
  if (rg) rg.hidden = shown !== 'range';
  const lab = document.getElementById('gpLabel');
  if (lab) {
    lab.textContent = gPeriodLabel(r);
    lab.title = r ? r.label_th + (r.complete ? '' : ' · ถึงวันที่ปิดยอดล่าสุด') : '';
  }
  const prev = /** @type {HTMLButtonElement|null} */ (document.getElementById('gpPrev')), next = /** @type {HTMLButtonElement|null} */ (document.getElementById('gpNext'));
  const first = _gpFirstDay();
  if (prev) prev.disabled = !r || (!!first && r.cur[0] <= first);
  if (next) next.disabled = !r || _gpShift(r, 1) === null;
  const f = /** @type {HTMLInputElement|null} */ (document.getElementById('gpFrom')), t = /** @type {HTMLInputElement|null} */ (document.getElementById('gpTo'));
  const a = gPeriodAnchor();
  [f, t].forEach(x => { if (x) { x.max = a; if (first) x.min = first; } });
  if (shown === 'range' && f && t && !f.value && !t.value) {
    const cur = r || periodRange('month', a);
    if (cur) { f.value = _gp.from || cur.cur[0]; t.value = _gp.to || cur.cur[1]; }
  }
  const sw = /** @type {HTMLSelectElement|null} */ (document.getElementById('selChartWin'));
  if (sw) sw.value = _gpChartWin;
}
// the pick of the period `d` steps away, '' when that is the anchor's own period, null when it starts after the anchor
function _gpShift(r, d) {
  const s = /** @type {Date} */ (_prDay(r.cur[0]));
  let n;
  if (_gp.kind === 'day') n = _prAdd(s, d);
  else if (_gp.kind === 'week') n = _prAdd(s, 7 * d);
  else if (_gp.kind === 'month') n = _prYmd(s.getUTCFullYear(), s.getUTCMonth() + 1 + d, 1);
  else n = _prYmd(s.getUTCFullYear() + d, 1, 1);
  const iso = _prIso(n), a = gPeriodAnchor();
  if (iso > a) return null;
  const nr = periodRange(_gp.kind, a, { date_from: iso });
  return nr && !nr.complete ? '' : iso;
}
function gPeriodApply() {
  _gpChipServer = false;                       // the page sets the chip again from its own answer (periodChip)
  _gpSave(); gPeriodWrite(); gPeriodRender();
  Object.keys(_tabLastKey).forEach(k => delete _tabLastKey[k]);
  if (typeof onFilterChange === 'function') onFilterChange();
}
function gPeriodKind(k) {
  if (!['day', 'week', 'month', 'year', 'range'].includes(k)) return;
  const r = gPeriodResolve();
  if (k === 'range') {                                   // open จาก–ถึง; the page moves on ใช้ช่วงนี้ (or at once to a range used before)
    if (_gp.kind === 'range') return;
    if (_gp.from && _gp.to) { _gpRangeOpen = false; _gp.kind = 'range'; gPeriodApply(); return; }
    _gpRangeOpen = true; gPeriodRender();
    const f = /** @type {HTMLInputElement|null} */ (document.getElementById('gpFrom')), t = /** @type {HTMLInputElement|null} */ (document.getElementById('gpTo'));
    if (f && t && r) { f.value = r.cur[0]; t.value = r.cur[1]; }
    if (f) f.focus();
    return;
  }
  _gpRangeOpen = false;
  // the same stretch of time in the new kind: the period that holds the end of the one on screen
  const end = r ? r.cur[1] : '';
  _gp.kind = k;
  const nr = end ? periodRange(k, gPeriodAnchor(), { date_from: end }) : null;
  _gp.pick = nr && nr.complete ? end : '';
  gPeriodApply();
}
function gPeriodStep(d) {
  const r = gPeriodResolve(); if (!r || _gp.kind === 'range') return;
  const n = _gpShift(r, d);
  if (n === null) return;
  const first = _gpFirstDay();
  if (d < 0 && first && r.cur[0] <= first) return;
  _gp.pick = n;
  gPeriodApply();
}
function gPeriodApplyRange() {
  const f = /** @type {HTMLInputElement|null} */ (document.getElementById('gpFrom'))?.value || '', t = /** @type {HTMLInputElement|null} */ (document.getElementById('gpTo'))?.value || '';
  const err = document.getElementById('gpErr');
  if (!f || !t) { if (err) err.textContent = 'ระบุวันที่ทั้งช่อง จาก และ ถึง'; return; }
  if (f > t) { if (err) err.textContent = 'วันที่ จาก ต้องไม่อยู่หลังวันที่ ถึง'; return; }
  if (f > gPeriodAnchor()) { if (err) err.textContent = 'ยังไม่มีข้อมูลในช่วงนี้'; return; }
  if (err) err.textContent = '';
  _gp = { kind: 'range', pick: '', from: f, to: t };
  _gpRangeOpen = false;
  gPeriodApply();
}
// set the bar from outside (a saved range, a deep link): {kind, pick, from, to}; reloads unless silent
function gPeriodSet(v, silent) {
  if (!v || !['day', 'week', 'month', 'year', 'range'].includes(v.kind)) return;
  _gp = { kind: v.kind, pick: v.pick || '', from: v.from || '', to: v.to || '' };
  _gpRangeOpen = false;
  if (silent) { _gpSave(); gPeriodWrite(); gPeriodRender(); } else gPeriodApply();
}
function gPeriodGet() { return Object.assign({}, _gp); }
// ── PR-PP2 (owner 2026-10-24: "ถ้ามีสองอันแบบนี้จะซ้ำซ้อนไหมครับ"): the top bar is the ONLY period control. A page declares the
// kinds it can show in TABS[tab].follow.period.kinds (default สัปดาห์ · เดือน · ปี · กำหนดเอง; วัน only where a page draws one day,
// a month-grain page เดือน only). Opening a page whose kinds exclude the bar's kind moves the bar to the nearest kind it has (the
// same stretch of time: the period that holds the end of the one on screen). The resolved range shows ONCE, as the chip beside the
// page title (#gpRangeChip); a page that resolved its windows on the server sets the chip from them (periodChip(d.period)).
const _GP_DEFAULT_KINDS = ['week', 'month', 'year', 'range'];
const _GP_NEAR = { day: ['week', 'month'], week: ['month', 'day'], month: ['year', 'week'], year: ['month'], range: ['month', 'year'] };
let _gpAllowed = _GP_DEFAULT_KINDS.slice(), _gpChipServer = false, _gpChipOn = false;
function gPeriodKindsOf(tab) {
  const f = TABS[tab] && TABS[tab].follow && TABS[tab].follow.period;
  return (f && typeof f === 'object' && Array.isArray(f.kinds)) ? f.kinds.slice() : _GP_DEFAULT_KINDS.slice();
}
// switchTab: the kinds of the page; the bar moves (silently, before the page loads) when its kind is not one of them
function gPeriodForTab(tab) {
  _gpAllowed = gPeriodKindsOf(tab);
  _gpChipServer = false;
  _gpChipOn = !!(TABS[tab] && TABS[tab].follow && TABS[tab].follow.period);
  if (_gpAllowed.length && !_gpAllowed.includes(_gp.kind)) {
    const k = (_GP_NEAR[_gp.kind] || []).find(x => _gpAllowed.includes(x)) || _gpAllowed[0];
    const r = gPeriodResolve(), end = r ? r.cur[1] : '';
    const nr = end ? periodRange(k, gPeriodAnchor(), { date_from: end }) : null;
    _gp = { kind: k, pick: nr && nr.complete ? end : '', from: _gp.from, to: _gp.to };
    _gpRangeOpen = false;
    _gpSave(); gPeriodWrite();
  }
  gPeriodRender();
}
// the range chip beside the page title: '1–19 ต.ค. 2569 · ถึงวันที่ปิดยอดล่าสุด' (a period object with label_th / complete)
// Owner 2026-10-25 ("ทำให้เลือกช่วงเวลาได้ด้วยนะครับ"): on a phone the chip is a one-line button ("1–24 ต.ค. 69 ▾", the year in two
// digits) that opens the top bar's own period control in a bottom sheet (phPeriodOpen, js/09c-mobile.js) - the same #gPeriod, moved
// into the sheet while it is open, so there is no second period state. A desktop shows the full span as before.
// a button on a phone only (role, focus, label); a desktop keeps the plain chip
function periodChipRole() {
  const el = document.getElementById('gpRangeChip'); if (!el) return;
  const on = phOn() && !!el.title;
  if (on) { el.setAttribute('role', 'button'); el.tabIndex = 0; el.setAttribute('aria-haspopup', 'dialog'); el.setAttribute('aria-label', `ช่วงเวลา ${el.title} · แตะเพื่อเปลี่ยน`); }
  else ['role', 'tabindex', 'aria-haspopup', 'aria-label'].forEach(a => el.removeAttribute(a));
}
// Owner 2026-10-25 ("หน้านี้อยากให้ filter รายสาขาได้ด้วย"): beside the range chip, a phone shows the branch of the page as a one-line chip
// ("ทุกสาขา ▾" / "นครปฐม ▾", ellipsis) that opens the top bar's branch filter in a sheet (phBranchOpen, js/09c-mobile.js). The chip only
// READS #selBranch - the one branch state; a pinned login (PR-K) never gets it: stripBranchPickers takes it out of the DOM.
function branchChipRender() {
  const el = document.getElementById('gpBranchChip'); if (!el) return;
  const sel = /** @type {HTMLSelectElement|null} */ (document.getElementById('selBranch'));
  const ok = typeof phOn === 'function' && phOn() && !isPinnedSession() && !!sel && sel.tagName === 'SELECT';
  el.hidden = !ok;
  if (!ok) return;
  const v = sel.value, g = getGroup();
  const name = v || (g === 'master' ? 'สาขาหลักทั้งหมด' : g === 'franchise' ? 'แฟรนไชส์ทั้งหมด' : 'ทุกสาขา');
  const l = el.querySelector('.gb-l'); if (l) l.textContent = name;
  el.classList.toggle('gb-on', !!v);
  el.title = `สาขา: ${name}`;
  el.setAttribute('aria-label', `สาขา ${name} · แตะเพื่อเปลี่ยน`);
}
function periodChipShort(r) { return r && r.label_th ? String(r.label_th).replace(/\b25(\d\d)\b/g, '$1') : ''; }
function periodChip(r, fromServer) {
  const el = document.getElementById('gpRangeChip');
  if (!el) return;
  if (fromServer) _gpChipServer = true;
  const txt = (_gpChipOn && r && r.label_th) ? periodSpan(r) : '';
  el.innerHTML = txt ? `<span class="gp-rc-l">${esc(txt)}</span><span class="gp-rc-s" aria-hidden="true">${esc(periodChipShort(r))}</span><span class="gp-rc-v" aria-hidden="true">▾</span>` : '';
  el.title = txt;
  periodChipRole();
  el.hidden = !txt;
  const sh = document.getElementById('phpRange');
  if (sh) sh.textContent = txt;
}
// the bar's period as the one a page asks the server for: kind + a day of it (the end of the period on screen)
function gPeriodPick() {
  const r = gPeriodResolve();
  return { kind: _gp.kind, pick: r ? r.cur[1] : '', from: _gp.kind === 'range' ? _gp.from : '', to: _gp.kind === 'range' ? _gp.to : '' };
}
// PR-PP2 · the top bar's period as query params for the period endpoints (/api/period/*): kind + pick (its first day) or
// date_from / date_to for กำหนดเอง; with the branch scope. The server resolves the windows on its own last closed day.
function gPeriodQuery(extra) {
  const r = gPeriodResolve(), p = new URLSearchParams();
  p.set('kind', _gp.kind);
  if (_gp.kind === 'range') { if (_gp.from) p.set('date_from', _gp.from); if (_gp.to) p.set('date_to', _gp.to); }
  else if (r) p.set('pick', r.cur[1]);
  if (getBranch()) p.set('branch', getBranch());
  if (getGroup()) p.set('group', getGroup());
  Object.entries(extra || {}).forEach(([k, v]) => p.set(k, v));
  return p.toString();
}
function gPeriodChartWin(v) {
  if (!['3', '6', '12', 'all', 'q', 'y'].includes(v)) return;
  _gpChartWin = v; _gpSave();
  Object.keys(_tabLastKey).forEach(k => delete _tabLastKey[k]);
  loadActiveTab();
}
// the bucket of the trend chart: ช่วงกราฟ q / y win; otherwise week -> weekly, a short กำหนดเอง -> daily, else monthly
function trendGranularity() {
  if (_gpChartWin === 'y') return 'yearly';
  if (_gpChartWin === 'q') return 'quarterly';
  if (_gp.kind === 'week') return 'weekly';
  if (_gp.kind === 'day') return 'daily';
  if (_gp.kind === 'range') { const r = gPeriodResolve(); return r && r.days <= 62 ? 'daily' : 'monthly'; }
  return 'monthly';
}
// the trend window: it ENDS at the bar's period and reaches back the ช่วงกราฟ months (a short กำหนดเอง: the range itself)
function gPeriodTrendWindow() {
  const r = gPeriodResolve(), g = trendGranularity();
  if (!r || g === 'yearly') return { g };
  const end = r.cur[1];
  if (g === 'daily') return { g, date_from: r.cur[0], date_to: end };
  const n = ({ '3': 3, '6': 6, '12': 12 })[_gpChartWin] || 0;          // 0 = all
  const e = /** @type {Date} */ (_prDay(end)), m0 = _prYmd(e.getUTCFullYear(), e.getUTCMonth() + 1 - (n - 1), 1);
  if (g === 'weekly') return { g, date_from: n ? _prIso(m0) : '', date_to: end };
  return { g, month_from: n ? _prIso(m0).slice(0, 7) : '', month_to: end.slice(0, 7) };
}
// a saved range of the ★ menu: new ones carry {period}; the older ones (month_from / month_to / granularity) still load
function gPeriodFromPreset(p) {
  if (p && p.period && p.period.kind) return p.period;
  const mf = (p && p.month_from) || '', mt = (p && p.month_to) || '', ms = _gpMonths();
  const last = ms[ms.length - 1] || '';
  const to = mt || last, from = mf || ms[0] || to;
  if (!to) return null;
  if (from === to) return { kind: 'month', pick: to + '-01' };
  const y = +to.slice(0, 4), m = +to.slice(5, 7);
  return { kind: 'range', from: from + '-01', to: `${to}-${String(_prMlen(y, m)).padStart(2, '0')}` };
}
// QA 2026-10-13 M1 · a stored stamp as the Bangkok clock. The tables hold UTC: "2026-10-09T06:15:42" (naive, as master.now() writes it) or
// "...+00:00" / "...Z" (aware, any offset is honoured); a stamp written with a SPACE ("2026-10-09 13:15", the reports' own format and the
// checklist's) already is the branch's clock and is kept as it is. -> {y, mo, d, h, mi, hhmm, iso} or null when there is no time part.
function _bkkClock(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})([T ])(\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?\s*(Z|[+-]\d{2}(?::?\d{2})?)?$/.exec(String(iso == null ? '' : iso).trim());
  if (!m) return null;
  let ms = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[5], +m[6], +(m[7] || 0));
  const z = m[8];
  if (z && z !== 'Z') { const dg = z.slice(1).replace(':', ''), off = (+dg.slice(0, 2)) * 60 + (+(dg.slice(2, 4) || 0)); ms -= (z[0] === '-' ? -1 : 1) * off * 60000; }
  if (z || m[4] === 'T') ms += 7 * 3600000;                 // UTC (or an explicit offset) -> Bangkok; a space-separated stamp is Bangkok already
  const t = new Date(ms), p2 = n => String(n).padStart(2, '0');
  const c = { y: t.getUTCFullYear(), mo: t.getUTCMonth() + 1, d: t.getUTCDate(), h: t.getUTCHours(), mi: t.getUTCMinutes() };
  c.hhmm = p2(c.h) + ':' + p2(c.mi);
  c.iso = `${c.y}-${p2(c.mo)}-${p2(c.d)}`;
  return c;
}
function fmtDayAt(iso) { const c = _bkkClock(iso); return c ? `${c.d}/${c.mo}/${c.y}` : fmtDay(iso); }   // the Bangkok day of a stored stamp
function fmtAt(iso) { const s = String(iso == null ? '' : iso); const c = _bkkClock(s); return c ? `${c.d}/${c.mo}/${c.y} ${c.hhmm}` : (_isoParts(s) ? fmtDay(s) : s); }
// the name a person recognises for a login (display name, else branch / role label); the login id itself is never shown
function whoTh(login) { if (login == null || login === '') return '-'; const n = window.AUTH && window.AUTH.names; return (n && n[login]) || String(login); }
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Safe interpolation into a single-quoted JS string inside an HTML attribute,
// e.g. onclick="f('${jsAttr(v)}')". JS-escape first, then HTML-escape.
const jsAttr = s => esc(String(s ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'"));

// ── Up/down TEXT ink. #059669 / #16a34a / #dc2626 are fine for a border or a dot
// but fail as text (3.4-4.4:1 on the page, and they never adapt to dark mode).
// A helper whose colour feeds both a mark and a figure keeps the hex for the
// mark and passes the text use through here (tokens.css --good-ink / --bad-ink).
// #1f7a4d / #b0472d are the menu-impact pair in 07-home: 5.3:1 on white but
// only 3.1-3.3 on the dark card. #047857 / #b91c1c are the light inks themselves.
// #10b981 / #ef4444 are 2.5 / 3.8:1 on white: marks only, never text.
// #22c55e / #84cc16 (2.3 / 2.0:1) and the dark #065f46 / #991b1b / #7f1d1d (2.1-2.3:1
// on the dark card) are the same good/bad families.
const _GOOD_HEX = ['#059669', '#16a34a', '#1f7a4d', '#047857', '#10b981', '#22c55e', '#84cc16', '#065f46', '#15803d'],
      _BAD_HEX  = ['#dc2626', '#b0472d', '#b91c1c', '#ef4444', '#991b1b', '#7f1d1d'];
// Teal and rose (categories, the ordering/checklist accent): #0d9488 3.74 and #f43f5e 3.67:1 on white.
const _TEAL_HEX = ['#0d9488', '#14b8a6', '#0f766e'], _ROSE_HEX = ['#f43f5e', '#e11d48', '#be123c'];
// Cyan and sky (margin %, delivery, links): #0891b2 3.68, #06b6d4 2.43, #0ea5e9 2.77:1 on white.
const _CYAN_HEX = ['#06b6d4', '#0891b2', '#0e7490'], _SKY_HEX = ['#0ea5e9', '#0284c7', '#0369a1'];
// Navy / violet / fuchsia / pink (chart series, quadrant labels, price tiers): #4a70b0 and #7a66b8
// pass on white but drop to 2.9-3.9 on tints and the dark card; #d946ef 3.46, #ec4899 3.53:1 on white.
const _NAVY_HEX = ['#4a70b0', '#4f46e5', '#6366f1', '#2563eb', '#1d4ed8'], _VIOLET_HEX = ['#7a66b8', '#8b5cf6', '#7c3aed'];
const _FUCHSIA_HEX = ['#d946ef', '#c026d3', '#a21caf'], _PINK_HEX = ['#ec4899', '#db2777', '#be185d'];
// Amber / yellow and orange (warnings, "near target"): #f59e0b 2.15, #d97706 3.19, #eab308 1.9, #facc15 1.5,
// #f97316 2.8:1 on white; the deep #b45309 / #92400e / #9a3412 fail on the dark card.
const _AMBER_HEX  = ['#f59e0b', '#d97706', '#b45309', '#92400e', '#78350f', '#eab308', '#facc15', '#ca8a04', '#a16207', '#854d0e'],
      _ORANGE_HEX = ['#f97316', '#ea580c', '#c2410c', '#9a3412'];
// Neutral greys as text: #94a3b8 2.56 and #cbd5e1 1.5:1 on white; #64748b / #6b7280 fail on
// tints and the dark card. They become the muted text token, which adapts.
const _MUTED_HEX = ['#94a3b8', '#cbd5e1', '#64748b', '#6b7280', '#9ca3af'];
const _INK_OF = [[_GOOD_HEX, '--good-ink'], [_BAD_HEX, '--bad-ink'], [_TEAL_HEX, '--teal-ink'], [_ROSE_HEX, '--rose-ink'],
                 [_CYAN_HEX, '--cyan-ink'], [_SKY_HEX, '--sky-ink'], [_NAVY_HEX, '--navy-ink'], [_VIOLET_HEX, '--violet-ink'],
                 [_FUCHSIA_HEX, '--fuchsia-ink'], [_PINK_HEX, '--pink-ink'], [_AMBER_HEX, '--amber-ink'],
                 [_ORANGE_HEX, '--orange-ink'], [_MUTED_HEX, '--text-muted']];
const textInk = c => { const k = _INK_OF.find(([hexes]) => hexes.includes(c)); return k ? `var(${k[1]})` : c; };

// ── ONE food-cost target (owner 2026-10-25: "food-cost target 58%") ──────────
// Settings → ค่าเริ่มต้น writes app_settings food_cost_target (backend rules.food_cost_target). Food cost % at or below it is
// good (ในเป้า), above it is bad (เกินเป้า x.x จุด). Every page, badge and chart reads it from here: startApp loads it from
// /api/settings, and a payload that carries `food_cost_target` refreshes it (fcTargetSet).
const FC_TARGET_DEFAULT = 58;
let _fcTarget = FC_TARGET_DEFAULT;
function fcTargetSet(v) { const n = Number(v); if (v != null && v !== '' && isFinite(n) && n >= 20 && n <= 90) _fcTarget = n; return _fcTarget; }
function fcTarget() { return _fcTarget; }
async function fcTargetLoad() {
  try { const s = await api(`${API}/api/settings`); fcTargetSet(s && s.food_cost_target); } catch (e) { /* the default stands */ }
  return _fcTarget;
}
// '58%' → '58', 57.5 → '57.5'
const fcTargetTxt = (t = _fcTarget) => `${+Number(t).toFixed(1)}`;
// is a food cost % within the target? (null when there is no figure)
const fcOk = (p, t = _fcTarget) => p == null || !isFinite(p) ? null : Number(p) <= t;
// 'ในเป้า' | 'เกินเป้า 5.5 จุด' | '—'
function fcStatusText(p, t = _fcTarget) {
  const ok = fcOk(p, t);
  return ok == null ? '—' : ok ? 'ในเป้า' : `เกินเป้า ${(Number(p) - t).toFixed(1)} จุด`;
}
// green within the target, red above it, grey without a figure
const fcTargetCol = (p, t = _fcTarget) => { const ok = fcOk(p, t); return ok == null ? '#94a3b8' : ok ? '#10b981' : '#ef4444'; };

// ── Formatters (guard against null / NaN / numeric-string from the API) ──────
const _num = n => { const v = Number(n); return (n == null || isNaN(v)) ? null : v; };
// PL3 (RV3 F25): one number format - a negative figure is shortened the same way as a positive one (it was printed whole: "-155931" beside "170.8K")
const fmt  = n => { const v=_num(n); if (v==null) return '—'; const a=Math.abs(v), s = v < 0 && a >= 0.5 ? '-' : ''; return s + (a >= 1e6 ? (a/1e6).toFixed(2)+'M' : a >= 1e3 ? (a/1e3).toFixed(1)+'K' : a.toFixed(0)); };
const fmtN = n => { const v=_num(n); return v==null ? '—' : v.toLocaleString('th-TH',{maximumFractionDigits:0}); };
const fmtP = n => { const v=_num(n); return v==null ? '—' : v.toFixed(1)+'%'; };
const fmtM = n => { const v=_num(n); return v==null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(1)+'%'; };
// ── DV2 (phone table review 2026-10-25) · a ranked list shows its first rows (and, for a two-sided list, its last rows); the rest are
// behind "ดูทั้งหมด (N)". The rows beyond the cap carry `hidden` + data-dv-more="<key>", so the phone cards (which skip hidden rows) follow;
// the button toggles every row of its key. dvMoreHidden(i, n, top, bottom): true when row i of n is folded.
function dvMoreHidden(i, n, top, bottom) { return n > top + (bottom || 0) && i >= top && i < n - (bottom || 0); }
function dvMoreAttr(key, i, n, top, bottom) { return dvMoreHidden(i, n, top, bottom) ? ` hidden data-dv-more="${esc(key)}"` : ''; }
function dvMoreBtn(key, n, top, bottom) {
  return n > top + (bottom || 0)
    ? `<div class="dv-more-wrap"><button type="button" class="shm-showall dv-more" data-dv-for="${esc(key)}" data-n="${n}" aria-expanded="false" onclick="dvMoreToggle(this)">ดูทั้งหมด (${fmtN(n)})</button></div>` : '';
}
function dvMoreToggle(btn) {
  const key = btn.getAttribute('data-dv-for'), open = btn.getAttribute('aria-expanded') !== 'true';
  document.querySelectorAll(`[data-dv-more="${CSS.escape(key)}"]`).forEach(r => { /** @type {HTMLElement} */ (r).hidden = !open; });
  btn.setAttribute('aria-expanded', String(open));
  btn.textContent = open ? 'ย่อ' : `ดูทั้งหมด (${fmtN(+btn.getAttribute('data-n'))})`;
}
// an editor on a phone (.desk-only, index.html): "แสดงบนโทรศัพท์" shows it for this visit
function deskShow(btn) {
  const note = btn.closest('.desk-note'); if (!note) return;
  note.classList.add('desk-force');
  const ed = note.nextElementSibling; if (ed && ed.classList.contains('desk-only')) ed.classList.add('desk-force');
}
// a signed baht figure: +฿193.0K / −฿537.0K
function dvBaht(v) { const x = _num(v); return x == null ? '—' : (x >= 0 ? '+' : '−') + '฿' + fmt(Math.abs(x)); }


// ── Fetch robustness (D3) + stale-response guard (D4) ───────────────────────
// loadGen increments on every tab switch / filter change. A loader captures the
// generation at start; if it has changed by the time a response arrives, the
// response is stale (the user moved on) and must NOT overwrite the new view.
let loadGen = 0;
let _chartsPending = false;   // set when mkChart was called before Chart.js finished loading
let _chartSetupDone = false;  // guards one-time plugin registration + global chart defaults
const STALE = Symbol('stale-response');

// Standard JSON fetch: checks response.ok and (optionally) drops stale responses.
// Pass the generation captured at loader start as `gen` to enable the stale guard.
// The one JSON call: fetch + r.json(), nothing else — deliberately identical to
// the `fetch(url).then(r => r.json())` it replaced at 200+ sites, so a non-OK
// JSON body still reaches the caller that checks `detail` / `available` today.
// The write token is attached by the global fetch wrapper above. This is the one
// place to add a timeout, a retry or an error toast for every call at once.
// The tab panels are not in the shell any more (tabs/NN-<name>.html, served as
// one bundle at /tabs.html). Load them into #tabsRoot before anything reads a
// panel id — init() awaits this first, so the DOM is complete at the same point
// of the boot it always was. The version comes from the shell, so a shell cached
// by the browser still fetches the bundle that matches it.
async function loadTabFragments() {
  const root = document.getElementById('tabsRoot');
  if (!root || root.childElementCount) return;
  const v = root.dataset.tabsV || '';
  const r = await fetch(`${API}/tabs.html${v ? '?v=' + v : ''}`);
  if (!r.ok) throw new Error('tabs.html ' + r.status);
  root.innerHTML = await r.text();
}

function api(url, opts) {
  // a payload that carries the ONE food-cost target (Settings → ค่าเริ่มต้น) keeps fcTarget() current
  return fetch(url, opts).then(r => r.json()).then(j => { if (j && typeof j === 'object' && 'food_cost_target' in j) fcTargetSet(j.food_cost_target); return j; });
}

async function getJSON(path, { gen = null, signal } = {}) {
  const url = (path.startsWith('http') || path.startsWith('/')) ? path : `${API}${path}`;
  const res = await fetch(url, { signal });
  if (gen !== null && gen !== loadGen) throw STALE;   // user navigated away mid-flight
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${path}`);
  const data = await res.json();
  if (gen !== null && gen !== loadGen) throw STALE;
  return data;
}

// Surface otherwise-silent fetch/render failures instead of leaving a half-built tab. PR-D6: both go through the one toast (uiToast).
function notifyError(msg) { uiToast(msg, 'error'); }
// The quiet counterpart: a toast for "saved / done".
function notifySuccess(msg) { uiToast(msg, 'success'); }

window.addEventListener('unhandledrejection', e => {
  if (e.reason === STALE) { e.preventDefault(); return; }            // expected — ignore
  const msg = (e.reason && e.reason.message) || String(e.reason || '');
  if (/abort/i.test(msg)) { e.preventDefault(); return; }            // expected on cancel
  console.error('Unhandled fetch/render error:', e.reason);
  notifyError('โหลดข้อมูลไม่สำเร็จ: ' + msg);
});

// chart registry alias used by zoom reset buttons
const charts = chartInstances;

function resetZoom(chartId) {
  const c = charts[chartId];
  if (c) c.resetZoom();
}

// ── Charts on a phone (2026-10-08). Every tab's chart is drawn for a wide screen: a
// 220–300 px wrapper, a legend across the top, every category tick. On a 375 px screen
// the ticks collide and the legend becomes a paragraph. Called from mkChart() when the
// screen is ≤700 px wide (the full dashboard after เต็ม →); it only fills in what a chart
// did not set itself, so an explicit legend / tick choice still wins:
//   · the wrapper grows to 320 px (not for pies / doughnuts) with maintainAspectRatio off
//   · the legend moves below, small; it is dropped when there are more than 6 series
//   · the category axis keeps ≤ 6 ticks, rotated as Chart.js sees fit (a horizontal bar chart keeps all its
//     branch names), value axes ≤ 5 ticks, 11 px tick font, no x-axis title
// The glance layer (GLANCE_PLUGIN) keeps drawing latest / peak / avg as before.
function _onPhoneScreen() {
  // PR-MA: a page in พอดีจอ (body.ph-fit) is the desktop layout scaled to the screen, so its charts are the desktop charts
  if (document.body.classList.contains('ph-fit')) return false;
  try { return window.innerWidth > 0 && window.matchMedia('(max-width:700px)').matches; } catch (e) { return false; }
}
// PR-MA: in พอดีจอ a chart is drawn at the frame's size and scaled down with it; a font under 12 px would read under 5.5 px on a 390 px screen,
// so every font the chart names (ticks, legend, titles, data labels) is at least 12 px. A chart's own larger sizes stay.
function _fitChartFonts(o, depth) {
  if (!o || typeof o !== 'object' || (depth || 0) > 6) return;
  Object.keys(o).forEach(k => {
    const v = o[k];
    if (k === 'font' && v && typeof v === 'object' && !Array.isArray(v)) { if (typeof v.size !== 'number' || v.size < 12) v.size = Math.max(12, typeof v.size === 'number' ? v.size : 12); }
    else if (k !== 'data' && v && typeof v === 'object' && !Array.isArray(v)) _fitChartFonts(v, (depth || 0) + 1);
  });
}
function _phoneChartTune(config, ctx) {
  const type = config.type;
  const o = config.options = config.options || {};
  const wrap = ctx.parentElement;
  const h = wrap ? parseInt(wrap.style.height || '', 10) : 0;
  if (h && h >= 150 && h < 320 && !['doughnut', 'pie', 'polarArea', 'radar'].includes(type)) {
    wrap.style.height = '320px';
    o.maintainAspectRatio = false;
  }
  o.plugins = o.plugins || {};
  const ds = (config.data && config.data.datasets) || [];
  const lg = o.plugins.legend = o.plugins.legend || {};
  if (lg.display === undefined) lg.display = ds.length <= 6;
  if (lg.display !== false) {
    if (!lg.position) lg.position = 'bottom';
    lg.labels = Object.assign({ boxWidth: 8, boxHeight: 8, padding: 10, font: { size: 12 } }, lg.labels || {});
  }
  if (!['bar', 'line', 'scatter', 'bubble'].includes(type)) return;
  const horizontal = o.indexAxis === 'y';
  o.scales = o.scales || {};
  // a default x / y axis only for a chart that names none of its own: a chart with yRev + yCust has no 'y', and the empty one added here
  // would draw its own 0 / 0.5 / 1.0 ticks beside the plot (LK)
  if (!Object.keys(o.scales).some(k => k.startsWith('x'))) o.scales.x = {};
  if (!Object.keys(o.scales).some(k => k.startsWith('y'))) o.scales.y = {};
  Object.entries(o.scales).forEach(([k, sc]) => {
    if (!sc || sc.display === false) return;
    const isX = k.startsWith('x'), isY = k.startsWith('y');
    const categoryAxis = horizontal ? isY : isX;
    sc.ticks = sc.ticks || {};
    if (categoryAxis && !horizontal) {
      // PR-MB: a category axis with more than 6 labels names its first, middle and last one (the B1 rule, _glanceB1Axes); fewer: every label
      let fml = false;
      if (sc.ticks.maxTicksLimit === undefined && sc.ticks.autoSkip === undefined) {
        const base = sc.ticks.callback;
        sc.ticks.autoSkip = false;
        sc.ticks.maxRotation = 0;
        sc.ticks.minRotation = 0;
        sc.ticks.callback = function (v, i, t) {
          const n = (t && t.length) || 0;
          if (n > 6 && i !== 0 && i !== n - 1 && i !== Math.floor((n - 1) / 2)) return '';
          return base ? base.call(this, v, i, t) : this.getLabelForValue(v);
        };
        fml = true;
      }
      if (sc.ticks.maxTicksLimit === undefined && !fml) sc.ticks.maxTicksLimit = 6;
      if (sc.ticks.autoSkip === undefined) sc.ticks.autoSkip = true;
      if (sc.title && sc.title.display) sc.title.display = false;
    } else if (!categoryAxis && sc.ticks.maxTicksLimit === undefined) {
      sc.ticks.maxTicksLimit = 5;
    }
    sc.ticks.font = Object.assign({ size: 12 }, sc.ticks.font || {});
  });
}

function mkChart(id, config) {
  // Vendor-load race: a tab loader can fire before chart.umd.min.js finishes
  // loading. Fail soft (the chart re-renders on the next tab/filter load) rather
  // than throwing a ReferenceError that aborts the whole loader.
  if (typeof Chart === 'undefined') { _chartsPending = true; return null; }
  ensureChartSetup();   // apply global defaults before the first chart is built
  if (chartInstances[id]) { chartInstances[id].destroy(); }
  const ctx = document.getElementById(id);
  if (!ctx) return null;
  // Charts are static by default — no mouse-wheel zoom (avoids accidental zoom while scrolling page).
  // Users who want zoom can click the dive-in button to open the full analysis modal.
  if (['line','bar','scatter'].includes(config.type)) {
    config.options = config.options || {};
    config.options.plugins = config.options.plugins || {};
    if (!config.options.plugins.zoom) {
      config.options.plugins.zoom = {
        zoom: { wheel: { enabled: false }, pinch: { enabled: false }, drag: { enabled: false }, mode: 'xy' },
        pan:  { enabled: false, mode: 'xy' },
      };
    }
  }
  stkChartWords(config);   // PL3: stock mode names no BoM on a chart either
  // A phone showing the full dashboard (เต็ม →) gets a chart cut for 375 px: see _phoneChartTune.
  if (_onPhoneScreen()) { try { _phoneChartTune(config, ctx); } catch (e) { console.warn('phone chart tune:', e); } }
  if (document.body.classList.contains('ph-fit')) {
    try {
      const o = config.options = config.options || {}; o.plugins = o.plugins || {};
      ['legend', 'title', 'subtitle', 'datalabels'].forEach(k => { if (o.plugins[k] !== false) { o.plugins[k] = o.plugins[k] || {}; if (k === 'legend') { o.plugins[k].labels = o.plugins[k].labels || {}; o.plugins[k].labels.font = o.plugins[k].labels.font || {}; } else o.plugins[k].font = o.plugins[k].font || {}; } });
      Object.values(o.scales || {}).forEach(sc => { if (sc) { sc.ticks = sc.ticks || {}; sc.ticks.font = sc.ticks.font || {}; } });
      _fitChartFonts(o);
    } catch (e) { console.warn('fit chart fonts:', e); }
  }
  // Apply theme-aware axis / grid / label colors (respects dark mode).
  // A canvas inside a [data-force-light] container (the printable Management
  // Report keeps a white-paper look in dark mode) always themes as light.
  applyChartTheme(config, ctx.closest('[data-force-light]') ? 'light' : undefined);
  chartInstances[id] = new Chart(ctx, config);
  // Auto-inject a "Dive in" zoom button on the chart canvas wrapper
  injectDiveBtn(id, ctx);
  return chartInstances[id];
}

// Centered moving-average smoother → a "trend curve" over a short series (the 8-day /
// 8-week revenue trend). The window clamps at the edges so the first/last points stay
// anchored to real data rather than drifting. Pure function of the input array.
function movingAvg(values, win = 3) {
  const n = values.length, half = Math.floor(win / 2);
  return values.map((_, i) => {
    let sum = 0, cnt = 0;
    for (let j = Math.max(0, i - half); j <= Math.min(n - 1, i + half); j++) {
      sum += values[j] || 0; cnt++;
    }
    return cnt ? sum / cnt : 0;
  });
}

function injectDiveBtn(chartId, canvas) {
  if (chartId.startsWith('zoom-') || chartId.startsWith('drill')) return; // skip modal charts
  const wrap = canvas.parentElement;
  if (!wrap || wrap.querySelector('.dive-btn[data-for="'+chartId+'"]')) return;
  wrap.style.position = wrap.style.position || 'relative';
  const btn = document.createElement('button');
  btn.className = 'dive-btn';
  btn.dataset.for = chartId;
  btn.title = 'Dive in (zoom + analysis)';
  btn.innerHTML = ICON.search;
  btn.onclick = () => diveInChart(chartId);
  wrap.appendChild(btn);
}

// ── Sortable tables ─────────────────────────────────────────────────────────
// makeSortable(id) turns the LAST header row of a table into click-to-sort
// columns (first click = high→low, click again = low→high, a third click
// restores the server's original order). Opt a column out with data-nosort.
//
// Cell values are read from data-sort when present, otherwise parsed out of the
// cell text — the tables here render "฿1,234", "12.3%", "+4.5", "−2 (Δ+1%)" and
// "-", so we take the FIRST numeric token and treat "-"/"" as missing (missing
// always sorts last, whichever direction, so empty rows never top the table).
function _tblSortVal(row, idx) {
  const cell = row.cells[idx];
  if (!cell) return { n: null, s: '' };
  const raw = cell.dataset.sort;
  const txt = (raw != null ? raw : cell.textContent).trim();
  if (txt === '' || txt === '-' || txt === '—') return { n: null, s: '' };
  // −  (U+2212, used for negatives in the reports) → ASCII minus
  const m = txt.replace(/−/g, '-').replace(/,/g, '').match(/-?\d+(\.\d+)?/);
  return { n: m ? parseFloat(m[0]) : null, s: txt.toLowerCase() };
}

function makeSortable(tableOrId) {
  const table = typeof tableOrId === 'string' ? document.getElementById(tableOrId) : tableOrId;
  if (!table || !table.tHead || !table.tBodies[0]) return;
  const headRow = table.tHead.rows[table.tHead.rows.length - 1];
  if (!headRow) return;
  const tbody = table.tBodies[0];
  // Remember the served order so a third click can put it back.
  Array.from(tbody.rows).forEach((r, i) => { if (!r.dataset.ord) r.dataset.ord = String(i); });
  Array.from(headRow.cells).forEach((th, idx) => {
    if (th.dataset.nosort != null || th.dataset.sortable) return;
    th.dataset.sortable = '1';
    th.classList.add('th-sortable');
    th.title = (th.title ? th.title + ' · ' : '') + 'คลิกเพื่อเรียง มาก→น้อย / น้อย→มาก';
    th.addEventListener('click', () => tblSort(th, idx));
  });
}

function tblSort(th, idx) {
  const table = th.closest('table');
  if (!table || !table.tBodies[0]) return;
  const headRow = th.parentElement;
  const dir = th.dataset.sortDir === 'desc' ? 'asc' : th.dataset.sortDir === 'asc' ? '' : 'desc';
  Array.from(headRow.cells).forEach(c => {
    delete c.dataset.sortDir;
    c.classList.remove('sorted-desc', 'sorted-asc');
  });
  if (dir) { th.dataset.sortDir = dir; th.classList.add('sorted-' + dir); }
  const tbody = table.tBodies[0];
  const rows = Array.from(tbody.rows);
  rows.sort((a, b) => {
    if (!dir) return (+a.dataset.ord || 0) - (+b.dataset.ord || 0);
    const va = _tblSortVal(a, idx), vb = _tblSortVal(b, idx);
    if (va.n == null && vb.n == null) return va.s.localeCompare(vb.s, 'th') * (dir === 'desc' ? -1 : 1);
    if (va.n == null) return 1;            // missing values sink, both directions
    if (vb.n == null) return -1;
    return dir === 'desc' ? vb.n - va.n : va.n - vb.n;
  });
  rows.forEach(r => tbody.appendChild(r));
  // Rank columns must count the CURRENT order, not the one they were built in.
  rows.forEach((r, i) => {
    const c = r.querySelector('.rank-cell');
    if (c) c.textContent = String(i + 1);
  });
}

function badge(pct) {
  if (pct == null) return '<span class="pill pill-neu">N/A</span>';
  const cls = pct > 0 ? 'pill-up' : pct < 0 ? 'pill-down' : 'pill-neu';
  const arrow = pct > 0 ? '▲' : pct < 0 ? '▼' : '—';
  return `<span class="pill ${cls}">${arrow} ${Math.abs(pct).toFixed(1)}%</span>`;
}

function trendBadge(pct) {
  if (pct == null) return '';
  const cls = pct > 0 ? 'pill-up' : pct < 0 ? 'pill-down' : 'pill-neu';
  const arrow = pct > 0 ? '▲' : pct < 0 ? '▼' : '—';
  return `<span class="pill ${cls}">${arrow} ${Math.abs(pct).toFixed(1)}%</span>`;
}

function mkSparkline(values, color, w=72, h=26) {
  if (!values || values.length < 2) return '';
  const mn = Math.min(...values), mx = Math.max(...values);
  const range = mx - mn || 1;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w;
    const y = h - ((v - mn) / range) * (h - 6) - 3;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" fill="none" style="opacity:0.75;flex-shrink:0"><polyline points="${pts}" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}

// ── Filter helpers ─────────────────────────────────────────────
const getBranch    = () => document.getElementById('selBranch').value;
const getGroup     = () => document.getElementById('selGroup')?.value || '';
const getMonthFrom = () => document.getElementById('selMonthFrom').value;
const getMonthTo   = () => document.getElementById('selMonthTo').value;
const getMonth     = () => getMonthTo() || getMonthFrom(); // backward compat for single-month contexts
const getVariant   = () => document.getElementById('selVariant')?.value || '';

// A per-dish margin / cost% is only meaningful for items that carry a real
// à-la-carte price. Buffet (B) dishes are included in the buffet package
// (revenue ≈ 0 per dish — the price lives on the buffet-package SKU), so their
// margin explodes to garbage (e.g. -29,709,896%). Exclude them from margin
// analysis; their ingredient cost is still counted in chain COGS elsewhere.
function _hasMenuPrice(r){
  if (!r) return false;
  const d = r.description || '';
  if (/^\(\s*B\s*\)/.test(d)) return false;                       // (B) = buffet-included dish
  if (/BUFFET/i.test((r.sku_code||'') + ' ' + (r.category||''))) return false;
  return (r.revenue_ex_vat ?? r.revenue ?? 0) > 0;
}
function qp(extra = {}) {
  const p = {};
  if (getBranch())    p.branch     = getBranch();
  if (getGroup())     p.group      = getGroup();
  if (getMonthFrom()) p.month_from = getMonthFrom();
  if (getMonthTo())   p.month_to   = getMonthTo();
  // Day-precise mode (daily/weekly granularity, incl. the custom day range): also
  // pass exact date_from/date_to. Day-capable endpoints prefer these; month-grain
  // endpoints ignore them and use the (enclosing) month range above.
  const g = (typeof currentGranularity !== 'undefined') ? currentGranularity : 'monthly';
  if (g === 'daily' || g === 'weekly') {
    const df = document.getElementById('selDateFrom')?.value;
    const dt = document.getElementById('selDateTo')?.value;
    if (df) p.date_from = df;
    if (dt) p.date_to   = dt;
  }
  if (getVariant())   p.variant    = getVariant();
  Object.assign(p, extra);
  const s = new URLSearchParams(p).toString();
  return s ? '?' + s : '';
}

// Params for the KPI summary cards. Same as qp(), but in daily/weekly mode the
// summary follows the date-range pickers instead of the month range (the month
// selects are hidden then, so their values are stale). /api/summary computes the
// window from sales_transaction when date_from/date_to are present.
function summaryParams() {
  const g = (typeof currentGranularity !== 'undefined') ? currentGranularity : 'monthly';
  if (g === 'daily' || g === 'weekly') {
    const p = {};
    if (getBranch()) p.branch = getBranch();
    if (getGroup())  p.group  = getGroup();
    const df = document.getElementById('selDateFrom')?.value;
    const dt = document.getElementById('selDateTo')?.value;
    if (df) p.date_from = df;
    if (dt) p.date_to   = dt;
    const s = new URLSearchParams(p).toString();
    return s ? '?' + s : '';
  }
  return qp();
}

// PR-PP1: the year select, ปีก่อน and เทียบปีก่อน of the old เลือกเร็ว row are gone (every figure shows เทียบปีก่อน itself);
// a caller that still asks for a calendar year moves the ONE period bar to it.
function setYearRange(year) {
  if (typeof gPeriodSet === 'function') gPeriodSet({ kind: 'year', pick: `${year}-01-01` });
}

// Phase 5 — click any branch name/box to drill into it globally.
// Calling twice on the same branch toggles back to "all branches".
function drillIntoBranch(branchName) {
  const sel = document.getElementById('selBranch');
  if (!sel) return;
  sel.value = (sel.value === branchName) ? '' : branchName;
  sel.style.transition = 'box-shadow 0.5s';
  sel.style.boxShadow = sel.value ? '0 0 0 3px rgba(31,58,107,0.45)' : '';
  setTimeout(() => { sel.style.boxShadow = ''; }, 700);
  if (typeof onFilterChange === 'function') onFilterChange();
}

// PR-PP1: the 3ด. / 6ด. / ปีนี้ / 12ด. buttons are gone: a trend window is the chart option ช่วงกราฟ, the period is the bar's.
function setPeriodPreset(preset) {
  if (preset === 'ytd') { gPeriodSet({ kind: 'year' }); return; }
  const w = { '3m': '3', '6m': '6', '12m': '12', all: 'all' }[preset];
  if (w) gPeriodChartWin(w);
}

async function onGroupChange() {
  // Filter the branch dropdown to show only branches in the selected group
  const grp = getGroup();
  const url = grp ? `${API}/api/branches?group=${grp}` : `${API}/api/branches`;
  const data = await api(url);
  const sel = document.getElementById('selBranch');
  const prev = sel.value;
  sel.innerHTML = '<option value="">ทุกสาขา</option>' +
    data.map(b => `<option value="${esc(b.name)}" data-type="${esc(b.branch_type || '')}">${esc(b.name)}</option>`).join('');
  // keep existing branch if still valid; else reset
  sel.value = data.some(b => b.name === prev) ? prev : '';
  onFilterChange();
}

// ── Tab switching ──────────────────────────────────────────────
// owner 2026-10-20: plain Thai titles - the English duplicates in brackets are gone; GP / P&L / BoM / SKU stay (staff use them)
const PAGE_TITLES = { hvmonitor:'High Value Product Monitor', franchise:'ร้านของฉัน', fmsg:'ข้อความจากเจ้าของสาขา', home:'สรุปประจำวัน', overall:'ภาพรวมยอดขาย', action:'ต้องดูวันนี้', buffetperf:'ผลงานบุฟเฟ่ต์', menu:'วิเคราะห์เมนู', trend:'แนวโน้ม', cost:'วิเคราะห์ต้นทุน', profit:'วิเคราะห์กำไร', pnl:'P&L เชิงลึก', compare:'เปรียบเทียบช่วงเวลา', report:'รายงานรายเดือน (ฉบับเต็ม)', mgtreport:'รายงานผู้บริหาร (รายวัน · รายสัปดาห์ · รายเดือน)', ops:'ปฏิบัติการเชิงลึก', settings:'ตั้งค่า', guest:'ลูกค้าและยอดต่อหัว', benchmark:'เทียบสาขา', engineering:'วิเคราะห์เมนู', staff:'ผลงานพนักงาน', discounts:'ส่วนลดและเงินรั่วไหล', childbuffet:'บุฟเฟ่ต์เด็ก', timeflow:'ช่วงเวลาและรอบโต๊ะ', gpbranch:'GP รายสาขา', branchbom:'BoM รายสาขา', bomcost:'ต้นทุนตามสูตร (BoM)', simhub:'ตัวจำลอง', menucompare:'เทียบเมนู A/B', menuefficiency:'เป้าลดต้นทุนเมนู', menuactionboard:'ปรับเมนู', menuopportunities:'โอกาสเมนู', feedback:'เสียงลูกค้า', checklist:'รายการตรวจ เสียงลูกค้า (เปิดใช้ · การตลาด)', coverage:'ความครอบคลุมต้นทุน', pnlbranch:'P&L รายสาขา', more:'ข้อสังเกตเพิ่มเติม', manual:'คู่มือการใช้งาน', dailysales:'ยอดขายรายวัน', costhead:'ต้นทุนต่อหัว รายสาขา', costheadtop60:'Top 60 ต้นทุนต่อหัว · สาขาของบริษัท (เทียบภาพรวม)', menuimpact:'วิเคราะห์วัตถุดิบ (มี เทียบ ไม่มี)', pnlbill:'P&L ต่อบิล', opsreport:'รายงานปฏิบัติการ (การชำระเงิน · บิลยกเลิก · โต๊ะ · โปรโมชัน)', cashctl:'เงินสดขาด/เกิน', custtype:'ประเภทลูกค้าและเดลิเวอรี', modifiers:'ความต้องการพิเศษ', pricezone:'ราคาและโซนโต๊ะ', cancelbill:'บิลยกเลิก/ลบ', closetime:'เวลาปิดร้านและความสดของข้อมูล', heavyeater:'ต่อหัวและนักกินหนัก', ordering:'แผนสั่งวัตถุดิบรายสาขา', ordersetup:'ตั้งค่าการสั่งซื้อ (ขนาดบรรจุ · ปฏิทินเทศกาล)', ingpurchase:'ยอดซื้อวัตถุดิบ เทียบ ยอดขาย รายสาขา (รายสัปดาห์)', bomcenter:'วัตถุดิบและใบสั่งซื้อ · ข้อมูลตั้งต้นและสูตร', branchstock:'วัตถุดิบและใบสั่งซื้อ · สาขาของฉัน', orderhub:'วัตถุดิบและใบสั่งซื้อ · ใบสั่งซื้อและปิดงวดเดือน', supplyhome:'วัตถุดิบและใบสั่งซื้อ · หน้างาน', launcher:'หน้าแรก', allpages:'หน้ารายงานทั้งหมด' };

// Every sidebar tab, once. `load` is what runs when the tab is opened (an arrow,
// because the loaders are defined in later slices and must resolve at call time) (or the
// global filter changes); a tab without one is static. switchTab() walks these
// keys to show/hide panels and loadActiveTab() dispatches through them — the
// 47-branch if-chain and the 48-name literal list this replaces had to be kept
// in step by hand, in two different files. TAB_MODULE (permissions) keys the
// same names.
const TABS = {
  overall: { load: () => loadOverall(), follow: { period: { kinds: ['week', 'month', 'year', 'range'] } } },
  action: { load: () => loadActionCenter() },
  menu: { load: () => { loadMenuSubtab(); renderMenuSalesNarrative(); } },
  trend: { load: () => loadTrend(), follow: { month: 'cmpMonth', period: { kinds: ['month'] }, prepare: () => { if (Array.isArray(window.allMonths) && allMonths.length && !document.getElementById('cmpMonth')?.options.length) populateCmpDropdowns(); } } },   // PR-PP2: A = the top bar's month
  cost: { load: () => loadCostAnalysis(), follow: { period: { kinds: ['month', 'year'] } } },   // PR-PP3
  profit: { load: () => loadProfitAnalysis() },
  pnl: { load: () => loadPnlTab(), follow: { period: { kinds: ['month', 'year', 'range'] } } },   // PR-PP3
  compare: { load: () => initCompare() },
  report: { load: () => loadReport() },
  mgtreport: { load: () => loadMgtReport() },
  ops: { load: () => loadOpsSubtab() },
  settings: { load: () => loadSettingsSubtab() },
  home: { load: () => loadHome() },
  dailysales: { load: () => loadDailySales(), follow: { period: { kinds: ['day', 'week', 'month', 'year'] } } },
  costhead: { load: () => loadCostHead(), follow: { period: { kinds: ['day', 'week', 'month', 'year'] } } },   // PR-PP3
  menuimpact: { load: () => loadMenuImpact() },
  pnlbill: { load: () => loadPnlBill(), follow: { period: { kinds: ['day', 'week', 'month'] } } },   // PR-PP3: per-bill COGS is a rolling window
  opsreport: { load: () => loadOpsReport(), follow: { month: 'opMonth', period: { kinds: ['week', 'month', 'range'] }, prepare: () => _opFillMonths() } },
  ordering: { load: () => loadOrderPlan() },
  ingpurchase: { load: () => loadIngPurchase(), follow: { period: { kinds: ['week', 'month', 'year', 'range'] } } },   // PR-PP3: purchases are weekly
  // ownFilters: the page has its own branch / month controls, so switchTab removes the
  // global filter bar from the layout (not just invisible: a phone then shows the page first).
  ordersetup: { load: () => loadOrderSetup(), ownFilters: true },
  bomcenter: { load: () => loadBomCenter(), ownFilters: true },
  branchstock: { load: () => loadBranchStock(), ownFilters: true },
  orderhub: { load: () => loadOrderHub(), ownFilters: true },
  supplyhome: { load: () => loadSupplyHome(), ownFilters: true },
  franchise: { load: () => loadFranchise(), ownFilters: true },
  fmsg: { load: () => loadFmsgInbox(), ownFilters: true },     // PR-F4: head office's inbox of the franchise owners' questions (js/17-franchise.js)
  cashctl: { load: () => loadCashCtl(), follow: { month: 'ccMonth', period: { kinds: ['month'] } } },
  custtype: { load: () => loadCustType(), follow: { month: 'ctMonth', period: { kinds: ['month'] } } },
  modifiers: { load: () => loadModifiers() },
  pricezone: { load: () => loadPriceZone(), follow: { month: 'pzMonth', period: { kinds: ['month'] } } },
  cancelbill: { load: () => loadCancelBill(), follow: { month: 'cbMonth', period: { kinds: ['month'] } } },
  closetime: { load: () => loadCloseTime(), follow: { date: 'ctmDate' } },
  guest: { load: () => loadGuest(), follow: { period: { kinds: ['month', 'year'] } } },
  benchmark: { load: () => loadBenchmark(), follow: { period: { kinds: ['week', 'month', 'year', 'range'] } } },
  engineering: { load: () => loadEngineering(), follow: { period: { kinds: ['month', 'year'] } } },
  buffetperf: { load: () => loadBuffetPerformance(), follow: { period: { kinds: ['month', 'year'] } } },
  staff: { load: () => loadStaff(), follow: { period: { kinds: ['month', 'year'] } } },
  discounts: { load: () => loadDiscountsTab() },
  childbuffet: { load: () => loadChildBuffet(), follow: { period: { kinds: ['week', 'month', 'year', 'range'] } } },
  timeflow: { load: () => loadTimeflow(), follow: { period: { kinds: ['month', 'year'] } } },
  gpbranch: { load: () => loadGpBranch(), follow: { period: { kinds: ['week', 'month', 'year', 'range'] } } },   // PR-PP3
  branchbom: { load: () => loadBranchBom(), follow: { period: { kinds: ['week', 'month', 'year', 'range'] }, prepare: () => _bbomInitFilters() } },   // PR-PP3
  bomcost: { load: () => loadBomCostTab(), follow: { period: { kinds: ['week', 'month', 'year', 'range'] } } },   // PR-PP3
  simhub: { load: () => loadSimhub() },
  menucompare: { load: () => loadMenuCompare() },
  menuefficiency: { load: () => loadMCE(), follow: { period: { kinds: ['month', 'year'] } } },   // PR-PP3
  menuactionboard: { load: () => loadMenuActionBoard(), follow: { period: { kinds: ['week', 'month', 'year', 'range'] } } },   // DV7: the buffet cost per head
  menuopportunities: { load: () => loadMenuOpportunities(), follow: { period: { kinds: ['week', 'month', 'year', 'range'] } } },
  feedback: { load: () => loadFeedback() },
  checklist: { load: () => loadChecklist() },
  coverage: { load: () => loadCostCoverage(), follow: { period: { kinds: ['month', 'year'] } } },   // PR-PP3
  pnlbranch: { load: () => loadPnlBranch(), follow: { period: { kinds: ['month', 'year', 'range'] } } },   // PR-PP3: the month select is hidden
  more: { load: () => loadMoreAnalytics() },
  heavyeater: { load: () => loadHeavyEaters(), follow: { dateRange: ['heFrom', 'heTo'] } },
  manual: { load: () => { if (typeof renderManualDeptMap === 'function') renderManualDeptMap(); } },   // PR-D5: the map of departments is drawn from DEPARTMENTS
  costheadtop60: { load: () => loadCostHeadTop60(), follow: { period: { kinds: ['month'] } } },   // PR-PP3: month-grain report
  hvmonitor: { load: () => loadHvMonitor(), follow: { period: { kinds: ['week', 'month', 'year', 'range'] } } },   // High Value Product Monitor (js/20-hvmonitor.js)
  // PR-D1: the department launcher (the desktop landing page) and the list of every page; neither has period or branch filters
  launcher: { load: () => loadLauncher(), ownFilters: true },
  allpages: { load: () => loadAllPages(), ownFilters: true },
};

function loadActiveTab() {
  if (!_desktopBooted && (_phoneFirst() || phOn())) return;   // the shell boots on "เต็ม →" or on the first page opened from the phone shell (03-tabs-main.js)
  if (document.body.classList.contains('ph-home')) return;   // a launcher view is on screen: the page behind it loads when it is opened (phOpenPage)
  _lastTabLoadAt = Date.now();
  loadGen++;   // invalidate any in-flight responses from a previous tab/filter
  cache = {};  // clear tab cache on filter change
  const t = TABS[activeTab];
  if (!t || !t.load) return;
  if (!t.follow) return t.load();
  const gen = loadGen;
  return _followGlobal(activeTab, t.follow).then(() => { if (gen === loadGen) return t.load(); });
}

// A tab that keeps its OWN picker declares in TABS how it follows the global
// period, and the registry snaps that picker before the loader runs. Until
// 2026-09 every such loader had to remember to call _syncExtra* itself, and a
// tab that forgot silently ignored the top bar (js/07-home.js keeps the
// snapping helpers). A hand pick on the local picker still wins until the
// global period changes again — the helpers remember the last global value.
//   month:      select id — one month, snapped to the global END month
//   date:       input id  — one day, snapped to the global period's end day
//   dateRange:  [from, to] input ids — the global day range
//   monthRange: [from, to] select ids — the global month range
//   period:     a periodBar key (PR-PP1) — its kind follows the top bar's
//               วัน / สัปดาห์ / เดือน / ปี (getPeriodKind)
//   prepare:    async fn that fills the picker's options first; a month select
//               without one is filled by _extraMonths (the /api/months list)
async function _followGlobal(tabKey, f) {
  if (f.prepare) await f.prepare();
  else if (f.month) await _extraMonths(f.month);
  if (typeof f.period === 'string') _syncExtraPeriod(f.period, tabKey);   // a page-own periodBar key (PR-PP1 shape)
  if (f.month) _syncExtraMonth(f.month, tabKey);
  if (f.date) _syncExtraDate(f.date, tabKey);
  if (f.dateRange) _syncExtraDateRange(f.dateRange[0], f.dateRange[1], tabKey);
  if (f.monthRange) _syncExtraMonthRange(f.monthRange[0], f.monthRange[1], tabKey);
}

function switchTab(name, direct) {
  // เมนูทำเงิน = the menu tab's Menu Engineering view (owner, 2026-10-01). The
  // standalone page (it adds a category filter) stays one click away from that
  // view: switchTab('engineering', true).
  if (name === 'engineering' && !direct && tabAllowed('menu')) {
    activeSubtab = 'engineering';
    switchTab('menu');
    switchSubtab('engineering');
    return;
  }
  // Permission guard — refuse tabs the current role can't access (server enforces too).
  // PR-S1: a recipe page in stock mode (a bookmark, an old link) opens the role's landing page instead - no error
  if (!tabAllowed(name)) { name = (bomHidden(name) && tabAllowed(_landingTab())) ? _landingTab() : firstAllowedTab(); }
  if (activeTab !== name && typeof _clearErrBanner === 'function') { try { _clearErrBanner(); } catch (e) { /* cosmetic */ } }   // PL3: an error banner stays with the page that raised it
  activeTab = name;
  try { applyNeeds(document.getElementById('tab-' + name) || undefined); } catch (e) { /* cosmetic */ }
  // Reading mode hides the app chrome via a <body> class for the Management
  // Report; without this it would follow the reader into every other tab.
  if (name !== 'mgtreport') { try { _mgtReadingOff(); } catch (e) {} }
  try { navigator.sendBeacon(API + '/api/usage/tab', JSON.stringify({tab: name})); } catch(e) {}
  Object.keys(TABS).forEach(t => {
    document.getElementById('nav-' + t)?.classList.toggle('active', t === name);
    const el = document.getElementById('tab-' + t);
    if (!el) return;
    if (t === name) {
      el.style.display = '';
      el.classList.remove('hidden', 'tab-active');
      void el.offsetWidth;
      el.classList.add('tab-active');
    } else {
      el.style.display = 'none';
      el.classList.add('hidden');
      el.classList.remove('tab-active');
    }
  });
  // Hide top-bar filters in Settings + Compare (Compare has its own pickers)
  const hideFilters = (name === 'settings' || name === 'compare');
  document.getElementById('selBranch').closest('div').classList.toggle('invisible', hideFilters);
  // Daily Brief is fixed to "yesterday + month-to-date" — it ignores the period
  // range entirely (only the branch filter applies). Hide the period controls and
  // the Quick-load bar here so they don't look broken when they have no effect.
  const hidePeriod = (name === 'home' || name === 'mgtreport');
  document.getElementById('gPeriod')?.classList.toggle('invisible', hidePeriod);
  gPeriodForTab(name);                                             // PR-PP2: the kinds of this page; the range chip
  branchChipRender();                                              // the phone's branch chip (owner 2026-10-25)
  const _gc = document.getElementById('gpChart');                 // ช่วงกราฟ: only the page whose trend chart reads it (PR-PP1)
  if (_gc) _gc.hidden = name !== 'overall';
  // Pages with their own branch / month controls (TABS[].ownFilters) get no chain filters at all,
  // removed from the layout (not just invisible) so a phone shows the page first
  const ownPage = !!(TABS[name] && TABS[name].ownFilters);
  document.getElementById('selBranch').closest('div').classList.toggle('hidden', ownPage);
  document.getElementById('gPeriod')?.classList.toggle('hidden', ownPage);
  document.getElementById('pageTitle').textContent = _noEmoji(PAGE_TITLES[name] || name);
  _updateClusterBar(name);
  // Reset buffet subtab to overview whenever user navigates to/from buffetperf
  if (name === 'buffetperf') switchBperfTab('overview');
  showLoadingBar();
  // Free charts that belong to the tab we just left. Chart.js instances are not
  // garbage-collected while registered, so without this they pile up (~80 live
  // after a full session), each holding a resize listener that fires on every
  // switch/resize and keeps the renderer busy. Each tab rebuilds its own charts
  // via mkChart() when re-opened (loadActiveTab below), so dropping them is safe.
  for (const [id, inst] of Object.entries(chartInstances)) {
    const panel = inst && inst.canvas && inst.canvas.closest('[id^="tab-"]');
    if (panel && panel.id !== 'tab-' + name) {
      try { inst.destroy(); } catch(e){}
      delete chartInstances[id];
    }
  }
  // Re-size remaining (now just this tab's) charts so any rendered while hidden
  // get correct dimensions.
  setTimeout(() => { Object.values(chartInstances).forEach(c => { try { c.resize(); } catch(e){} }); }, 30);
  // UX batch A (2026-10-20): past 1.5 s the page says it is still working (ปรับเมนู / พนักงาน take 10 s or more)
  const _slowMine = ++_slowSeq;
  const _slowTimer = setTimeout(() => { if (_slowMine === _slowSeq) _slowNotice(_noEmoji(PAGE_TITLES[name] || name)); }, 1500);
  Promise.resolve(loadActiveTab()).finally(() => {
    hideLoadingBar();
    clearTimeout(_slowTimer);
    if (_slowMine === _slowSeq) _slowNotice(null);
  });
}
let _slowSeq = 0;
function _slowNotice(title) {
  let el = document.getElementById('slowNotice');
  if (!title) { if (el) el.style.display = 'none'; return; }
  if (!el) {
    el = document.createElement('div');
    el.id = 'slowNotice';
    el.setAttribute('role', 'status');
    el.innerHTML = '<span class="sn-dot" aria-hidden="true"></span><span><span class="sn-t"></span> <span class="sn-sub">หน้านี้คำนวณข้อมูลมาก อาจใช้เวลาสักครู่</span></span>';
    document.body.appendChild(el);
  }
  el.querySelector('.sn-t').textContent = `กำลังโหลด ${title}…`;
  el.style.display = 'flex';
}

// ── วัตถุดิบและใบสั่งซื้อ (BoM Center): one entry, a two-level bar ──────────
// The supply cluster is the only one whose bar lists PANES, not tabs: the
// module's 17 panes sit in 3 tabs (bomcenter / branchstock / orderhub) plus 3
// legacy ordering tabs, and staff could not find them (owner, 2026-10-07).
// Level 1 = a question ("ข้อมูลตั้งต้น", "ใบสั่งซื้อ" …), level 2 = the panes
// that answer it. Every pane and every legacy tab lives in exactly one chip;
// nothing is hidden or deleted. Its own const, referenced by name from
// TAB_CLUSTERS, so scripts/usage_kill_list.py keeps parsing the cluster line.
// `pane` is the tab's own sub-view key (_bmcSub / _brnSub / _ohbSub / _osSub);
// an item without one is a whole tab.
// the centre's own panes: the warehouse (which only works the queue) must not be offered them
const _SUPPLY_CENTRE = ['supply.ordering', 'ops.report', 'bom.center'];
// PR-M2: each item also names the ROLES whose bar lists it (`roles`); `mods` stays the permission floor, so an item
// shows only when the role is listed AND the person holds a module that opens it. No `roles` = every role that can open it.
// The roles of the role-chip matrix test (backend/test_supply_chips_roles.py) are read from the words below.
const SUPPLY_CHIPS = [
  { key: 'home', label: 'หน้างาน', items: [
    // batch 3 (4.7): the entry "สาขานำร่อง 4 สาขา" is gone - หน้างาน carries a one-line summary of the all-branches grid instead,
    // and the pilot pane (ความพร้อมและผลนำร่อง) is opened from the foot of that grid
    { tab: 'supplyhome', label: 'งานของฉันและปิดงวดเดือน' }] },
  // ข้อมูลตั้งต้น, as the roles OTHER than the analyst / manager read it: the kitchen's register, Accounting's price page and Sales Support's price file
  // (PR-M7: for the analyst and the manager these pages sit in งานประจำเดือน and the second ข้อมูลตั้งต้น below)
  { key: 'master', label: 'ข้อมูลตั้งต้น', items: [
    { tab: 'bomcenter', pane: 'ing', label: 'ทะเบียนวัตถุดิบ', roles: ['innovation'] },
    { tab: 'bomcenter', pane: 'price', label: 'ราคารายเดือน', roles: ['accounting'] },
    { tab: 'bomcenter', pane: 'pricefile', label: 'ไฟล์ราคา', roles: ['salessupport'] }] },
  // PR-M7 · งานประจำเดือน (the analyst and the manager): what is done once a month, in the order it is done
  { key: 'monthly', label: 'งานประจำเดือน', items: [
    { tab: 'bomcenter', pane: 'pricefile', label: 'ไฟล์ราคา', roles: ['analyst', 'management'] },
    { tab: 'bomcenter', pane: 'price', label: 'ราคารายเดือน', roles: ['analyst', 'management'] },
    { tab: 'bomcenter', pane: 'dishcost', label: 'ต้นทุนต่อจาน', roles: ['analyst', 'management'] },
    { tab: 'bomcenter', pane: 'rec', bom: true, label: 'สูตรตามระดับราคา', roles: ['analyst', 'management'] },
    { tab: 'bomcenter', pane: 'prep', bom: true, label: 'สูตรกึ่งสำเร็จ', roles: ['analyst', 'management'] },
    { tab: 'bomcenter', pane: 'rel', bom: true, label: 'ตรวจและประกาศใช้', roles: ['analyst', 'management'] }] },
  { key: 'recipes', label: 'สูตรและการประกาศใช้', items: [
    { tab: 'bomcenter', pane: 'rec', bom: true, label: 'สูตรตามระดับราคา', roles: ['innovation'] },
    { tab: 'bomcenter', pane: 'prep', bom: true, label: 'สูตรกึ่งสำเร็จ', roles: ['innovation'] }] },
  { key: 'orders', label: 'ใบสั่งซื้อ', items: [
    { tab: 'orderhub', pane: 'queue', label: 'คิวใบสั่งซื้อ', roles: ['warehouse', 'salessupport', 'analyst', 'management'] },
    { tab: 'orderhub', pane: 'claims', label: 'เคลมสินค้า', mods: _SUPPLY_CENTRE, roles: ['salessupport', 'analyst', 'management'] },
    { tab: 'bomcenter', pane: 'fc', label: 'ยอดแนะนำรายรอบ', roles: ['salessupport', 'analyst', 'management'] },
    { tab: 'orderhub', pane: 'comp', label: 'ตรวจสอบการสั่ง', mods: _SUPPLY_CENTRE, roles: ['operation', 'analyst', 'management'] },
    { tab: 'orderhub', pane: 'issues', label: 'ใบตัดเบิกสาขา', mods: _SUPPLY_CENTRE, roles: ['salessupport', 'operation', 'analyst', 'management'] },
    { tab: 'orderhub', pane: 'issuevar', label: 'ผลต่างตัดเบิกรายวัน', mods: _SUPPLY_CENTRE, roles: ['salessupport', 'operation', 'analyst', 'management'] },
    { tab: 'orderhub', pane: 'persales', label: 'ใช้วัตถุดิบต่อยอดขาย', mods: _SUPPLY_CENTRE, roles: ['salessupport', 'operation', 'analyst', 'management'] },
    // batch 3 (4.7): every branch account on one grid, the last 7 days, worst first (replaces the หน้างาน entry "สาขานำร่อง 4 สาขา")
    { tab: 'orderhub', pane: 'adoption', label: 'การใช้งานทุกสาขา', mods: _SUPPLY_CENTRE, roles: ['salessupport', 'operation', 'analyst', 'management'] }] },
  { key: 'close', label: 'ปิดงวดเดือน', items: [
    { tab: 'orderhub', pane: 'central', label: 'นำเข้าข้อมูลปิดงวด', mods: _SUPPLY_CENTRE, roles: ['salessupport', 'analyst', 'management'] },
    { tab: 'orderhub', pane: 'month', bom: true, label: 'สรุปผลต่างรายเดือน', mods: _SUPPLY_CENTRE, roles: ['salessupport', 'operation', 'analyst', 'management'] },
    { tab: 'bomcenter', pane: 'issue', label: 'ใบสรุปตัดเบิก', mods: ['cost.analysis', 'bom.center', 'supply.ordering', 'ops.report'], roles: ['accounting', 'analyst', 'management'] }] },
  // PR-M7 · ข้อมูลตั้งต้น (the analyst and the manager): the register, the units and the readiness of the older codes - set up rarely
  { key: 'base', label: 'ข้อมูลตั้งต้น', items: [
    { tab: 'bomcenter', pane: 'ing', label: 'ทะเบียนวัตถุดิบ', roles: ['analyst', 'management'] },
    { tab: 'bomcenter', pane: 'conv', label: 'หน่วยและการแปลง', roles: ['analyst', 'management'] },
    { tab: 'bomcenter', pane: 'rcheck', bom: true, label: 'ตรวจสูตรด้วยยอดซื้อ', mods: ['bom.center'], roles: ['analyst', 'management'] },
    { tab: 'bomcenter', pane: 'overview', bom: true, label: 'ความพร้อมและรหัสสูตรเดิม', roles: ['analyst', 'management'] }] },
  { key: 'branch', label: 'สาขาของฉัน', items: [
    { tab: 'branchstock', pane: 'issue', label: 'ตัดเบิก', roles: ['branch', 'analyst', 'management'] },
    { tab: 'branchstock', pane: 'order', label: 'สั่งของ', roles: ['branch', 'analyst', 'management'] },
    { tab: 'branchstock', pane: 'recv', label: 'รับของ', roles: ['branch', 'analyst', 'management'] },
    { tab: 'branchstock', pane: 'var', label: 'ส่วนต่างการใช้', roles: ['branch', 'analyst', 'management'] },
    { tab: 'branchstock', pane: 'count', label: 'นับของสิ้นเดือน', roles: ['branch', 'analyst', 'management'] }] },
  // ตั้งค่า: the delivery calendar and the zones, then ONE level-2 group "หน้าระบบเดิม" holding the four pages of the
  // older ordering screens (owner 2026-10-10: regrouped, kept, never deleted)
  { key: 'setup', label: 'ตั้งค่า', items: [
    { tab: 'bomcenter', pane: 'cal', label: 'รอบส่งของสาขา', roles: ['salessupport', 'analyst', 'management'] },
    // PR-B4a (1.8): the festival / promotion editor moved here from หน้าระบบเดิม, with the public holidays beside it (read only)
    { tab: 'bomcenter', pane: 'events', label: 'ปฏิทินเทศกาลและวันหยุด', mods: ['bom.center', 'supply.ordering'], roles: ['salessupport', 'analyst', 'management'] },
    { tab: 'bomcenter', pane: 'zones', label: 'โซนจัดเก็บ', mods: ['bom.center', 'supply.ordering'], roles: ['salessupport', 'analyst', 'management'] },
    { tab: 'bomcenter', pane: 'codereq', label: 'แจ้งเปลี่ยนรหัสสินค้า', mods: ['bom.center', 'supply.ordering'], roles: ['salessupport', 'analyst', 'management'] },
    { tab: 'ordering', label: 'แผนสั่งวัตถุดิบ (ระบบเดิม)', group: 'legacy', roles: ['salessupport', 'analyst', 'management'] },
    { tab: 'ingpurchase', label: 'ยอดซื้อเทียบยอดขาย', group: 'legacy', roles: ['salessupport', 'analyst', 'management'] },
    { tab: 'ordersetup', pane: 'pack', label: 'ขนาดบรรจุ', group: 'legacy', roles: ['salessupport', 'analyst', 'management'] }] },
];
const SUPPLY_LEGACY_LABEL = 'หน้าระบบเดิม';
const SUPPLY_FLAT_MAX = 5;     // a role with this many pages or fewer gets them as one flat row of chips

// Lean-overhaul Phase 2: every analysis area is ONE sidebar entry + a cluster
// bar of related views on the page itself. tabs[0] of each cluster is its
// sidebar entry (applyPermissions' safety net relies on that ordering).
// Regrouped 2026-10-01 (owner): one entry per question - ยอดขาย / ลูกค้า &
// บุฟเฟ่ต์ / เมนู / ต้นทุน / กำไร / ส่วนลด & เงิน / ปฏิบัติการ / จัดซื้อ - and
// each group sits inside one permission area as far as it can, so a role sees
// whole groups instead of half-locked ones. A third element lists alias tabs
// that light the same chip (a sub-view of that page, not a chip of its own).
const TAB_CLUSTERS = [
  { key: 'sales',  label: 'ยอดขาย',          tabs: [['overall','ภาพรวมและตัวชี้วัด'],['dailysales','ยอดขายรายวัน'],['trend','แนวโน้ม'],['timeflow','ช่วงเวลา'],['custtype','ช่องทาง & ประเภทลูกค้า'],['compare','เทียบ A/B'],['more','ข้อสังเกตเพิ่มเติม']] },
  { key: 'buffet', label: 'ลูกค้า & บุฟเฟ่ต์', tabs: [['buffetperf','ผลงานบุฟเฟ่ต์'],['guest','ลูกค้า & ยอด/หัว'],['childbuffet','บุฟเฟ่ต์เด็ก']] },
  { key: 'menu',   label: 'เมนู',            tabs: [['menu','วิเคราะห์เมนู',['engineering']],['menuactionboard','ปรับเมนู'],['menuopportunities','โอกาสเมนู'],['menucompare','เทียบเมนู A/B']] },
  { key: 'cost',   label: 'ต้นทุน',           tabs: [['cost','ภาพรวม'],['bomcost','ต้นทุนตามสูตร'],['costhead','ต้นทุน/หัว',['costheadtop60']],['hvmonitor','High Value Product Monitor'],['heavyeater','นักกินหนัก'],['menuimpact','วิเคราะห์วัตถุดิบ'],['menuefficiency','เป้าลดต้นทุน'],['coverage','ความครอบคลุม'],['branchbom','BoM รายสาขา'],['simhub','ตัวจำลอง']] },
  { key: 'profit', label: 'กำไร',            tabs: [['gpbranch','GP รายสาขา'],['pnlbranch','P&L รายสาขา'],['pnlbill','P&L ต่อบิล'],['profit','กำไร SKU'],['benchmark','เทียบสาขา'],['pnl','P&L เชิงลึก']] },
  { key: 'money',  label: 'ส่วนลด & เงิน',    tabs: [['discounts','ส่วนลด'],['staff','พนักงาน'],['cashctl','เงินสดขาด/เกิน'],['cancelbill','บิลยกเลิก']] },
  { key: 'ops',    label: 'ปฏิบัติการ',       tabs: [['opsreport','รายงานปฏิบัติการ'],['ops','เชิงลึก'],['modifiers','ความต้องการพิเศษ'],['pricezone','ราคา/โซน'],['closetime','ปิดร้าน/ข้อมูล']] },
  { chips: SUPPLY_CHIPS, key: 'supply', label: 'วัตถุดิบและใบสั่งซื้อ', tabs: [['supplyhome','หน้างาน'],['bomcenter','ข้อมูลตั้งต้นและสูตร'],['orderhub','ใบสั่งซื้อและปิดงวดเดือน'],['branchstock','สาขาของฉัน'],['ordering','แผนสั่งวัตถุดิบ'],['ingpurchase','ยอดซื้อเทียบยอดขาย'],['ordersetup','ตั้งค่าการสั่งซื้อ']] },
];
const _inChip = (t, name) => t[0] === name || (t[2] || []).includes(name);
const clusterOf = name => TAB_CLUSTERS.find(c => c.tabs.some(t => _inChip(t, name)));

// ── Departments (PR-D1, 2026-10-21, owner: "แยกเป็น icon เมนู แต่ละแผนก") ───────────────────
// ONE registry that the launcher home (js/18-launcher.js), the sidebar and - in the next PRs - the department workspace (D2),
// the phone launcher (D3) and the red task badges (D4) all read. TAB_CLUSTERS and SUPPLY_CHIPS above STAY: the cluster bar and the
// supply chips still draw inside the pages, and หน้ารายงานทั้งหมด is built from them, so no page is ever lost.
//   key    short id; also the CSS suffix: --dept-<hue>-ink / -tint / -side (docs/ui-kit/tokens.css, light and dark)
//   label  the department's name; duty one line under it; icon a DEPT_ICONS key
//   roles  the roles whose HOME this is (listed first on their launcher); every role still sees any department it may open a page of
//   pages  { tab, pane?, label, icon, mods?, roles? } - the real tab, and for the supply tabs the pane key of SUPPLY_CHIPS.
//          A page appears only when the person may open it (js/18-launcher.js deptPageAllowed: tabAllowed, then the supply item's own
//          mods / roles, then this entry's `mods` / `roles` when it names any). A page may sit in several departments.
// 24x24 stroke paths (stroke 1.8, round caps) - the same set as the approved mockup; add more only in this style.
const DEPT_ICONS = {
  brief: 'M4 8h16v12H4z M9 8V5h6v3 M4 13h16',
  mega: 'M4 10v4h3l7 4V6L7 10z M18 9a3 3 0 0 1 0 6',
  clip: 'M9 4h6v3H9z M9 5H6v16h12V5h-3 M9 14l2 2 4-4',
  pie: 'M12 3v9h9 M20.5 15A9 9 0 1 1 9 3.5',
  calc: 'M6 3h12v18H6z M9 7h6 M9 11h.01 M12 11h.01 M15 11h.01 M9 15h.01 M12 15h.01 M15 15h.01 M9 18h6',
  chef: 'M7 21h10 M7 17h10 M7 17v-5a4 4 0 0 1-1-7.9A4 4 0 0 1 12 3a4 4 0 0 1 6 1.1A4 4 0 0 1 17 12v5',
  head: 'M4 14v-2a8 8 0 0 1 16 0v2 M4 14h3v5H4z M17 14h3v5h-3z M20 19a3 3 0 0 1-3 2h-3',
  truck: 'M3 6h11v10H3z M14 10h4l3 3v3h-7 M7 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4z M17 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4z',
  store: 'M4 10l8-6 8 6v10H4z M10 20v-6h4v6',
  key: 'M15 9a3 3 0 1 0 0-.01 M12.5 11.5L4 20 M7 17l2 2 M5 19l2 2',
  bar: 'M3 3v18h18 M7 16v-4 M11 16v-8 M15 16v-6 M19 16V6',
  trend: 'M3 17l6-6 4 4 8-8 M14 7h7v7',
  coin: 'M12 3v18 M17 7H9.5a3 3 0 0 0 0 6h5a3 3 0 0 1 0 6H6',
  doc: 'M14 3H6v18h12V7z M14 3v4h4 M8 12h8 M8 16h5',
  users: 'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M2 21v-1a6 6 0 0 1 12 0v1 M16 3.5a4 4 0 0 1 0 7 M22 21v-1a6 6 0 0 0-4-5.6',
  book: 'M4 4h11a3 3 0 0 1 3 3v13H7a3 3 0 0 1-3-3z M4 17a3 3 0 0 1 3-3h11',
  msg: 'M4 5h16v11H8l-4 4z M8 9h8 M8 12h5',
  pct: 'M6.5 9a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z M17.5 20a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z M19 5L5 19',
  cash: 'M3 7h18v12H3z M3 11h18 M16 15h2',
  cancel: 'M6 3h12v18l-3-2-3 2-3-2-3 2z M10 9l4 4 M14 9l-4 4',
  bowl: 'M3 11h18a9 7 0 0 1-18 0z M8 7c0-1 1-2 1-3 M12 7c0-1 1-2 1-3 M16 7c0-1 1-2 1-3',
  tag: 'M3 12V4h8l10 10-8 8z M7.5 8.5h.01',
  scale: 'M12 3v18 M5 21h14 M6 7h12 M6 7l-3 7a3 3 0 0 0 6 0z M18 7l-3 7a3 3 0 0 0 6 0z',
  db: 'M4 6a8 3 0 1 0 16 0 8 3 0 1 0-16 0 M4 6v6a8 3 0 0 0 16 0V6 M4 12v6a8 3 0 0 0 16 0v-6',
  shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z M9 12l2 2 4-4',
  inbox: 'M4 13h4l2 3h4l2-3h4 M4 13l2-8h12l2 8v6H4z',
  ret: 'M9 14L4 9l5-5 M4 9h10a6 6 0 0 1 0 12h-3',
  cal: 'M4 6h16v15H4z M4 10h16 M8 3v4 M16 3v4',
  box: 'M12 3l8 4.5v9L12 21l-8-4.5v-9z M12 12l8-4.5 M12 12v9 M12 12L4 7.5',
  cart: 'M3 4h2l2.5 11h11l2-8H6 M9 20a1 1 0 1 0 0-2 1 1 0 0 0 0 2z M18 20a1 1 0 1 0 0-2 1 1 0 0 0 0 2z',
  recv: 'M12 3v10 M8 9l4 4 4-4 M4 14v6h16v-6',
  diff: 'M4 18l5-6 4 3 7-9 M15 6h5v5',
  count: 'M9 6h11 M9 12h11 M9 18h11 M4 6l1 1 2-2 M4 12l1 1 2-2 M4 18l1 1 2-2',
  lock: 'M6 11h12v10H6z M8 11V7a4 4 0 0 1 8 0v4',
  upload: 'M12 15V3 M7 8l5-5 5 5 M4 15v6h16v-6',
  // added for pages the mockup did not draw, in the same style
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 7v5l3 2',
  gauge: 'M4 18a8 8 0 1 1 16 0 M12 18l4-6 M7 18h.01 M17 18h.01',
  target: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M12 12h.01',
  flask: 'M9 3h6 M10 3v6L4.5 19a2 2 0 0 0 1.8 3h11.4a2 2 0 0 0 1.8-3L14 9V3 M6.5 15h11',
  bulb: 'M9 18h6 M10 21h4 M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z',
  child: 'M12 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M6 21v-2a6 6 0 0 1 12 0v2',
  chat: 'M4 6h16v10H9l-5 4z M8 10h8',
  gem: 'M6 4h12l3 5-9 11L3 9z M3 9h18 M9 4l3 5 3-5 M12 9v11',
};
const DEPARTMENTS = [
  { key: 'mgmt', label: 'ผู้บริหาร', duty: 'ภาพรวม รายงาน และการรับทราบ', icon: 'brief', hue: 'mgmt', roles: ['management'], duty_stock: 'ภาพรวมและรายงาน', pages: [
    { tab: 'mgtreport', label: 'รายงานผู้บริหาร', icon: 'doc' },
    { tab: 'home', label: 'สรุปประจำวัน', icon: 'brief' },
    { tab: 'overall', label: 'ภาพรวมและตัวชี้วัด', icon: 'gauge' },
    { tab: 'dailysales', label: 'ยอดขายรายวัน', icon: 'cal' },
    { tab: 'trend', label: 'แนวโน้ม', icon: 'trend' },
    { tab: 'gpbranch', label: 'GP รายสาขา', icon: 'coin' },
    { tab: 'pnlbranch', label: 'P&L รายสาขา', icon: 'bar' },
    { tab: 'cost', label: 'ต้นทุน', icon: 'pie' },
    { tab: 'bomcenter', pane: 'rel', bom: true, label: 'รับทราบสูตร', icon: 'shield' },
    { tab: 'fmsg', label: 'ข้อความจากเจ้าของสาขา', icon: 'msg' }] },     // PR-F4: management and operation (franchise.inbox)
  { key: 'mkt', label: 'การตลาด', duty: 'ลูกค้า เมนู และเสียงลูกค้า', icon: 'mega', hue: 'mkt', roles: ['marketing'], pages: [
    { tab: 'buffetperf', label: 'ลูกค้าและบุฟเฟ่ต์', icon: 'users' },
    { tab: 'childbuffet', label: 'บุฟเฟ่ต์เด็ก', icon: 'child' },
    { tab: 'menu', label: 'วิเคราะห์เมนู', icon: 'book' },
    { tab: 'menuactionboard', label: 'ปรับเมนู', icon: 'count' },
    { tab: 'menuopportunities', label: 'โอกาสเมนู', icon: 'bulb' },
    { tab: 'menucompare', label: 'เทียบเมนู A/B', icon: 'diff' },
    { tab: 'feedback', label: 'เสียงลูกค้า', icon: 'msg' },
    { tab: 'custtype', label: 'ช่องทางและประเภทลูกค้า', icon: 'bar' },
    { tab: 'timeflow', label: 'ช่วงเวลา', icon: 'clock' }] },
  { key: 'ops', label: 'ปฏิบัติการ', duty: 'ควบคุมหน้าร้านและวินัยสาขา', icon: 'clip', hue: 'ops', roles: ['operation'], pages: [
    { tab: 'opsreport', label: 'รายงานปฏิบัติการ', icon: 'clip' },
    { tab: 'discounts', label: 'ส่วนลด', icon: 'pct' },
    { tab: 'cashctl', label: 'เงินสดขาด/เกิน', icon: 'cash' },
    { tab: 'cancelbill', label: 'บิลยกเลิก', icon: 'cancel' },
    { tab: 'staff', label: 'พนักงาน', icon: 'users' },
    { tab: 'modifiers', label: 'ความต้องการพิเศษ', icon: 'chat' },
    { tab: 'pricezone', label: 'ราคาและโซน', icon: 'tag' },
    { tab: 'closetime', label: 'ปิดร้านและข้อมูล', icon: 'clock' },
    { tab: 'orderhub', pane: 'comp', label: 'ตรวจสอบการสั่ง', icon: 'count' },
    { tab: 'orderhub', pane: 'issues', label: 'ใบตัดเบิกสาขา', icon: 'doc' },
    { tab: 'orderhub', pane: 'issuevar', label: 'ผลต่างตัดเบิกรายวัน', icon: 'diff' },
    { tab: 'orderhub', pane: 'adoption', label: 'การใช้งานทุกสาขา', icon: 'store' }] },
  { key: 'ana', label: 'ฝ่ายวิเคราะห์', duty: 'ราคา สูตร หน่วย และการประกาศใช้', icon: 'pie', hue: 'ana', roles: ['analyst'], duty_stock: 'ราคา ต้นทุนต่อจาน และหน่วย', pages: [
    { tab: 'bomcenter', pane: 'pricefile', label: 'ไฟล์ราคา', icon: 'upload' },
    { tab: 'bomcenter', pane: 'price', label: 'ราคารายเดือน', icon: 'tag' },
    { tab: 'bomcenter', pane: 'dishcost', label: 'ต้นทุนต่อจาน', icon: 'coin' },
    { tab: 'bomcenter', pane: 'rec', bom: true, label: 'สูตรตามระดับราคา', icon: 'book' },
    { tab: 'bomcenter', pane: 'prep', bom: true, label: 'สูตรกึ่งสำเร็จ', icon: 'bowl' },
    { tab: 'bomcenter', pane: 'rel', bom: true, label: 'ตรวจและประกาศใช้', icon: 'shield' },
    { tab: 'bomcenter', pane: 'ing', label: 'ทะเบียนวัตถุดิบ', icon: 'db' },
    { tab: 'bomcenter', pane: 'conv', label: 'หน่วยและการแปลง', icon: 'scale' },
    { tab: 'bomcenter', pane: 'rcheck', bom: true, label: 'ตรวจสูตรด้วยยอดซื้อ', icon: 'count' },
    { tab: 'bomcenter', pane: 'overview', bom: true, label: 'ความพร้อมและรหัสสูตรเดิม', icon: 'gauge' },
    { tab: 'bomcost', bom: true, label: 'ต้นทุนตามสูตร', icon: 'calc' },
    { tab: 'costhead', label: 'ต้นทุนต่อหัว', icon: 'coin' },
    { tab: 'hvmonitor', label: 'High Value Product Monitor', icon: 'gem' },
    { tab: 'coverage', label: 'ความครอบคลุมต้นทุน', icon: 'pie' },
    { tab: 'menuimpact', bom: true, label: 'วิเคราะห์วัตถุดิบ', icon: 'flask' },
    { tab: 'menuefficiency', label: 'เป้าลดต้นทุน', icon: 'target' },
    { tab: 'branchbom', bom: true, label: 'BoM รายสาขา', icon: 'store' },
    { tab: 'simhub', label: 'ตัวจำลอง', icon: 'calc' },
    { tab: 'profit', label: 'กำไร SKU', icon: 'coin' },
    { tab: 'benchmark', label: 'เทียบสาขา', icon: 'bar' },
    { tab: 'pnl', label: 'P&L เชิงลึก', icon: 'trend' },
    { tab: 'pnlbill', label: 'P&L ต่อบิล', icon: 'cancel' }] },
  { key: 'kit', label: 'ครัว', duty: 'สร้างสูตรและส่งตรวจ', icon: 'chef', hue: 'kit', roles: ['innovation'], duty_stock: 'ทะเบียนวัตถุดิบ (อ่านอย่างเดียว)', pages: [
    { tab: 'bomcenter', pane: 'rec', bom: true, label: 'สูตรตามระดับราคา', icon: 'book' },
    { tab: 'bomcenter', pane: 'prep', bom: true, label: 'สูตรกึ่งสำเร็จ', icon: 'bowl' },
    { tab: 'bomcenter', pane: 'ing', label: 'ทะเบียนวัตถุดิบ', icon: 'db' }] },
  { key: 'ss', label: 'ประสานงานขาย', duty: 'คิวใบสั่งซื้อ เคลม และรอบส่ง', icon: 'head', hue: 'ss', roles: ['salessupport'], pages: [
    { tab: 'orderhub', pane: 'queue', label: 'คิวใบสั่งซื้อ', icon: 'inbox' },
    { tab: 'orderhub', pane: 'claims', label: 'เคลมสินค้า', icon: 'ret' },
    { tab: 'bomcenter', pane: 'fc', label: 'ยอดแนะนำรายรอบ', icon: 'trend' },
    { tab: 'orderhub', pane: 'issues', label: 'ใบตัดเบิกสาขา', icon: 'doc' },
    { tab: 'orderhub', pane: 'issuevar', label: 'ผลต่างตัดเบิกรายวัน', icon: 'diff' },
    { tab: 'orderhub', pane: 'persales', label: 'ใช้วัตถุดิบต่อยอดขาย', icon: 'bar' },
    { tab: 'bomcenter', pane: 'pricefile', label: 'ไฟล์ราคา', icon: 'upload' },
    { tab: 'orderhub', pane: 'central', label: 'นำเข้าข้อมูลปิดงวด', icon: 'recv' },
    { tab: 'orderhub', pane: 'month', bom: true, label: 'สรุปผลต่างรายเดือน', icon: 'lock' },
    { tab: 'bomcenter', pane: 'cal', label: 'รอบส่งของสาขา', icon: 'cal' },
    { tab: 'bomcenter', pane: 'events', label: 'ปฏิทินเทศกาลและวันหยุด', icon: 'cal' },
    { tab: 'bomcenter', pane: 'zones', label: 'โซนจัดเก็บ', icon: 'box' },
    { tab: 'bomcenter', pane: 'codereq', label: 'แจ้งเปลี่ยนรหัสสินค้า', icon: 'ret' }] },
  { key: 'wh', label: 'คลัง', duty: 'ระบุยอดส่งจริงภายใน 14:00', icon: 'truck', hue: 'wh', roles: ['warehouse'], pages: [
    { tab: 'orderhub', pane: 'queue', label: 'คิวใบสั่งซื้อ', icon: 'inbox' }] },
  { key: 'acc', label: 'บัญชี', duty: 'ใบสรุปตัดเบิกและงบกำไรขาดทุน', icon: 'calc', hue: 'acc', roles: ['accounting'], pages: [
    { tab: 'bomcenter', pane: 'issue', label: 'ใบสรุปตัดเบิก', icon: 'doc' },
    { tab: 'pnlbranch', label: 'P&L รายสาขา', icon: 'coin' },
    { tab: 'gpbranch', label: 'GP รายสาขา', icon: 'bar' },
    { tab: 'cashctl', label: 'เงินสดขาด/เกิน', icon: 'cash' },
    { tab: 'discounts', label: 'ส่วนลด', icon: 'pct' },
    { tab: 'bomcenter', pane: 'price', label: 'ราคารายเดือน', icon: 'tag' }] },
  { key: 'brn', label: 'สาขา', duty: 'ตัดเบิก สั่ง รับ และนับของ', icon: 'store', hue: 'brn', roles: ['branch'], pages: [
    { tab: 'branchstock', pane: 'issue', label: 'ตัดเบิก', icon: 'box' },
    { tab: 'branchstock', pane: 'order', label: 'สั่งของ', icon: 'cart' },
    { tab: 'branchstock', pane: 'recv', label: 'รับของ', icon: 'recv' },
    { tab: 'branchstock', pane: 'var', label: 'ส่วนต่างการใช้', icon: 'diff' },
    { tab: 'branchstock', pane: 'count', label: 'นับของสิ้นเดือน', icon: 'count' }] },
  { key: 'own', label: 'เจ้าของสาขา', duty: 'สรุปผลของร้านตนเอง', icon: 'key', hue: 'own', roles: ['franchise'], pages: [
    // only the franchise login: the page reads ITS branch from the session, and for a central login the server answers 400 "name a branch"
    // (that is why its old sidebar entry was never shown to anyone else)
    { tab: 'franchise', label: 'ร้านของฉัน', icon: 'store', roles: ['franchise'] }] },
];
// Page search (PR-D4): the words people say for a page besides its label, space-separated, Thai and Latin ("tab" or "tab|pane" -> words).
// The search (js/18-launcher.js pgSearchResults) also reads the page's label, its department and its old cluster; scripts/check-departments.js
// fails when a key here is not a real page.
const PAGE_KEYWORDS = {
  'home': 'สรุป brief วันนี้ ต้องดู', 'overall': 'kpi ตัวชี้วัด ภาพรวม ยอดขาย', 'dailysales': 'ยอดขาย รายวัน sales daily', 'trend': 'แนวโน้ม trend เติบโต',
  'mgtreport': 'รายงาน mgt report ผู้บริหาร ประชุม', 'report': 'รายงาน รายเดือน monthly',
  'gpbranch': 'กำไร gp margin กำไรขั้นต้น', 'pnlbranch': 'กำไร p&l กำไรขาดทุน profit', 'pnl': 'กำไร p&l profit กำไรขาดทุน', 'pnlbill': 'กำไร p&l บิล bill',
  'profit': 'กำไร sku margin', 'benchmark': 'เทียบ สาขา benchmark', 'cost': 'ต้นทุน cost bom อาหาร', 'bomcost': 'ต้นทุน cost bom สูตร recipe',
  'costhead': 'ต้นทุน หัว cost per head', 'hvmonitor': 'high value product monitor ติดตามวัตถุดิบมูลค่าสูง วัตถุดิบหลัก แซลมอน ทูน่า ฮามาจิ กุ้งหวาน หอยเชลล์ ปลาไหล หอยนางรม เนื้อ ต้นทุนต่อหัว', 'coverage': 'ต้นทุน ครอบคลุม coverage', 'menuimpact': 'วัตถุดิบ ต้นทุน ingredient', 'menuefficiency': 'ลดต้นทุน เป้า target',
  'branchbom': 'bom สูตร สาขา ต้นทุน', 'simhub': 'จำลอง simulator what-if', 'heavyeater': 'นักกิน ต้นทุน heavy eater',
  'buffetperf': 'บุฟเฟ่ต์ ลูกค้า buffet customer', 'childbuffet': 'เด็ก บุฟเฟ่ต์ child', 'guest': 'ลูกค้า ยอด หัว guest', 'custtype': 'ช่องทาง ประเภทลูกค้า grab line man delivery',
  'timeflow': 'ช่วงเวลา ชั่วโมง peak time', 'menu': 'เมนู menu วิเคราะห์ ขายดี', 'menuactionboard': 'ปรับเมนู action ต้นทุนต่อหัว บุฟเฟ่ต์', 'menuopportunities': 'โอกาส เมนูใหม่ opportunity',
  'menucompare': 'เทียบเมนู ab compare', 'compare': 'เทียบ ab compare', 'feedback': 'เสียงลูกค้า รีวิว review ร้องเรียน', 'checklist': 'รีวิว checklist ตรวจ',
  'opsreport': 'ปฏิบัติการ ops report', 'ops': 'ปฏิบัติการ ops เชิงลึก', 'discounts': 'ส่วนลด discount คูปอง โปรโมชัน', 'cashctl': 'เงินสด ขาด เกิน cash',
  'cancelbill': 'ยกเลิก บิล void cancel', 'staff': 'พนักงาน แคชเชียร์ staff', 'modifiers': 'ความต้องการพิเศษ modifier', 'pricezone': 'ราคา โซน price zone',
  'closetime': 'ปิดร้าน เวลา closing ข้อมูล', 'action': 'ต้องดู วันนี้ action',
  'supplyhome': 'งาน หน้างาน todo task ปิดงวด', 'ordering': 'แผนสั่ง สั่งซื้อ plan order วัตถุดิบ', 'ingpurchase': 'ยอดซื้อ ซื้อ purchase เทียบยอดขาย', 'ordersetup|pack': 'ขนาดบรรจุ หน่วยสั่ง pack',
  'franchise': 'ร้าน เจ้าของ สรุป franchise', 'fmsg': 'ข้อความ คำถาม ตอบ เจ้าของสาขา แฟรนไชส์ ติดต่อ message franchise inbox', 'manual': 'คู่มือ วิธีใช้ help manual', 'settings': 'ตั้งค่า settings ผู้ใช้ user สิทธิ์',
  'orderhub|queue': 'ใบสั่งซื้อ po สั่งของ order คิว', 'orderhub|claims': 'เคลม คืนของ สินค้าเสียหาย claim return', 'orderhub|issues': 'ใบตัดเบิก ตัดเบิก ใบเบิก requisition',
  'orderhub|issuevar': 'ผลต่าง ตัดเบิก variance', 'orderhub|comp': 'ตรวจ สั่ง ใบสั่งซื้อ สั่งช้า compliance', 'orderhub|persales': 'ใช้วัตถุดิบ ยอดขาย usage',
  'orderhub|central': 'ปิดงวด นำเข้า ไดกิโตะ รายงานกลาง', 'orderhub|month': 'ผลต่าง รายเดือน ปิดเดือน monthly variance', 'orderhub|adoption': 'การใช้งาน adoption สาขา',
  'bomcenter|rel': 'ประกาศใช้ สูตร release รับทราบ ตรวจ', 'bomcenter|rec': 'สูตร bom recipe ระดับราคา tier', 'bomcenter|prep': 'กึ่งสำเร็จ ซอส น้ำจิ้ม sub-recipe',
  'bomcenter|ing': 'วัตถุดิบ ingredient ทะเบียน master', 'bomcenter|conv': 'หน่วย แปลง แปลงหน่วย unit', 'bomcenter|price': 'ราคา ราคาซื้อ price ต้นทุนวัตถุดิบ',
  'bomcenter|dishcost': 'ต้นทุนต่อจาน ราคาต้นทุน price list cost',
  'bomcenter|pricefile': 'ไฟล์ราคา นำเข้าราคา price file', 'bomcenter|issue': 'ใบสรุปตัดเบิก ตัดเบิก บัญชี issue summary', 'bomcenter|fc': 'ยอดแนะนำ forecast พยากรณ์ รอบสั่ง',
  'bomcenter|cal': 'รอบส่ง ปฏิทิน calendar', 'bomcenter|events': 'เทศกาล วันหยุด โปรโมชัน event', 'bomcenter|zones': 'โซน จัดเก็บ ตู้เย็น zone',
  'bomcenter|codereq': 'รหัสสินค้า เปลี่ยนรหัส code', 'bomcenter|rcheck': 'ตรวจสูตร ยอดซื้อ recipe check', 'bomcenter|overview': 'ความพร้อม รหัสเดิม overview',
  'branchstock|issue': 'ตัดเบิก ใช้วัตถุดิบ ใบตัดเบิก เบิกใช้ ของเสีย', 'branchstock|order': 'สั่งของ สั่งสินค้า po ใบสั่งซื้อ จำนวนที่ควรสั่ง ยอดแนะนำ', 'branchstock|recv': 'รับของ รับสินค้า receive แจ้งของขาด แจ้งของเสีย เคลม',
  'branchstock|var': 'ส่วนต่าง ผลต่าง variance ใช้จริง', 'branchstock|count': 'นับของ ตรวจนับ นับสต็อก stock count สิ้นเดือน ต้นงวด ปลายงวด',
};
// PR-MA · the phone display mode of every page ("tab" or "tab|pane" -> 'fit' | 'touch', or { view: mode } for a page with two views).
// 'touch' (แตะง่าย) = the phone layout: 12 px text floor, 44 px targets, stacked cards. 'fit' (พอดีจอ) = the page's desktop layout in a frame
// 760-900 px wide scaled to the screen width (js/09c-mobile.js, "PHONE DISPLAY MODES"). The person may switch per page (the ⤢ button of the
// phone top bar, remembered on the device). scripts/check-departments.js fails when a page of a department or of หน้ารายงานทั้งหมด has no entry.
// Analytics, reports and read-only tables: fit. Branch pages, หน้างาน, the launcher, ฉัน, ค้นหา, editors and import forms: touch.
const PAGE_PHONE = {
  launcher: 'touch', allpages: 'touch', home: 'touch', supplyhome: 'touch', franchise: 'touch', manual: 'touch', settings: 'touch',
  mgtreport: 'fit', report: 'fit', overall: 'fit', dailysales: 'fit', trend: 'fit', compare: 'fit', more: 'fit', action: 'fit', checklist: 'fit',
  gpbranch: 'fit', pnlbranch: 'fit', pnlbill: 'fit', profit: 'fit', benchmark: 'fit', pnl: 'fit',
  cost: 'fit', bomcost: 'fit', costhead: 'fit', hvmonitor: 'fit', coverage: 'fit', menuimpact: 'fit', menuefficiency: 'fit', branchbom: 'fit', simhub: 'fit', heavyeater: 'fit',
  buffetperf: 'fit', childbuffet: 'fit', guest: 'fit', custtype: 'fit', timeflow: 'fit', feedback: 'fit',
  menu: 'fit', menuactionboard: 'fit', menuopportunities: 'fit', menucompare: 'fit',
  opsreport: 'fit', ops: 'fit', discounts: 'fit', cashctl: 'fit', cancelbill: 'fit', staff: 'fit', modifiers: 'fit', pricezone: 'fit', closetime: 'fit',
  fmsg: 'fit', ordering: 'fit', ingpurchase: 'fit', 'ordersetup|pack': 'fit',
  'orderhub|queue': 'fit', 'orderhub|claims': 'fit', 'orderhub|comp': 'fit', 'orderhub|issues': 'fit', 'orderhub|issuevar': 'fit', 'orderhub|persales': 'fit',
  'orderhub|adoption': 'fit', 'orderhub|month': 'fit', 'orderhub|central': 'touch',
  'bomcenter|fc': 'fit', 'bomcenter|price': 'fit', 'bomcenter|issue': 'fit', 'bomcenter|rcheck': 'fit', 'bomcenter|overview': 'fit',
  'bomcenter|dishcost': { table: 'fit', import: 'touch' }, 'bomcenter|ing': { list: 'fit', edit: 'touch' },
  'bomcenter|pricefile': 'touch', 'bomcenter|rec': 'touch', 'bomcenter|prep': 'touch', 'bomcenter|rel': 'touch', 'bomcenter|conv': 'touch',
  'bomcenter|cal': 'touch', 'bomcenter|events': 'touch', 'bomcenter|zones': 'touch', 'bomcenter|codereq': 'touch',
  'branchstock|issue': 'touch', 'branchstock|order': 'touch', 'branchstock|recv': 'touch', 'branchstock|var': 'touch', 'branchstock|count': 'touch',
};
// the default mode of a page (and of one of its views); a page the registry does not name opens in touch, the phone's own layout
function phonePageDefault(tab, pane, view) {
  const e = PAGE_PHONE[tab + '|' + (pane || '')] || PAGE_PHONE[tab];
  if (e && typeof e === 'object') return e[view] || e[Object.keys(e)[0]];
  return e === 'fit' ? 'fit' : 'touch';
}
// PR-MB · the card lists of a phone in แตะง่าย: the tables people read daily, one card per row (phCardList, js/09c-mobile.js "CARD LISTS").
// Keyed like PAGE_PHONE; one entry per table:
//   sel      the table inside the page's panel (all: true = every match, e.g. one table per delivery day)
//   title    the column of the row's name; sub = the grey line (code / branch / date; subL: true prints each with its header)
//   keyCols  the 3 key figures, as the table's header prints them (start of the header is enough; [a, b] = a, else b); labels = short names
//   pill     the status / class column (its chip keeps its colour; tones = { regex: good | bad | warn | info } where the cell is plain text)
//   done     the pill words of a finished row: hidden by default behind the shared state filter (uiStateFilter). A page whose own filter
//            already hides finished work (the supply queues) has no `done` here: its filter stays above the cards.
//   rows     only these rows (a toggle row's sub-rows are left to the sheet); spark = the columns of a sparkline ('months' = the month columns);
//   sparkCell = a cell that already holds one; fields: 'table' = a writer's editable table stays the table; empty = { t, act } of an empty list
// backend/test_phone_content.py checks that every page of the MB list has its tables here, each with a title and 1-3 key columns.
const PAGE_CARDS = {
  menu: [
    { sel: '#orderedTable', title: 'เมนู', sub: ['SKU'], keyCols: ['จำนวนขาย', '% เทียบยอดรวม', 'เปลี่ยนแปลง %'], labels: ['จำนวนขาย', '% ของยอดรวม', 'เปลี่ยน %'], pill: 'ABC' },
    { sel: '#costHeadTable', title: 'เมนู', sub: ['SKU'], keyCols: ['ต้นทุน/หัว', '% ของต้นทุนรวม', 'เปลี่ยนแปลง %'], labels: ['ต้นทุน/หัว', '% ของต้นทุน', 'เปลี่ยน %'], pill: 'ABC' },
    { sel: 'table:has(#meTableBody)', title: 'เมนู', sub: ['หมวด'], keyCols: ['ยอดขาย', 'กำไร', '% กำไร'], pill: 'กลุ่ม' }],
  // DV7 · ปรับเมนู: the bucket boards are cards already; the à-la-carte table is one card per dish
  menuactionboard: [{ sel: '#mabAlcTable', title: 'เมนู', keyCols: ['GP (฿)', 'เทียบช่วงก่อน', 'GP %'] }],
  // DV7 · โอกาสเมนู: a cost role gets ลดได้ (฿) first (table.op-costed); the plates view leads with จานที่เกิน
  menuopportunities: [
    { sel: '#oppOverTable.op-costed', title: 'เมนู', keyCols: ['ลดได้', 'จานที่เกิน', 'สาขาที่เกิน'], labels: ['ลดได้ (฿)', 'จานที่เกิน', 'สาขาที่เกิน'] },
    { sel: '#oppOverTable:not(.op-costed)', title: 'เมนู', keyCols: ['จานที่เกิน', 'สาขาที่เกิน', 'สะสม'] },
    { sel: '#oppGapsTable', title: 'เมนู', keyCols: ['รายได้ที่เพิ่มได้', 'ราคาต่อจาน', 'สาขาที่ขายน้อย'] },
    { sel: '#oppReviveTable', title: 'เมนู', keyCols: ['รายได้ต่อเดือน', 'จานต่อเดือน', 'ขายเดือนสุดท้าย'] },
    { sel: '#oppWsTable', title: 'หมวด', keyCols: ['รายได้', 'รายได้ต่อเมนู', 'แนวโน้มต่อเดือน'], pill: 'คำแนะนำ',
      tones: { 'ขยาย': 'good', 'ควรลด': 'bad', 'ทรงตัว': 'warn' } }],
  profit: [{ sel: '#profitBandTable', title: 'ช่วง', sub: ['บิล', 'ลูกค้า'], subL: true, keyCols: ['รายได้ไม่รวม VAT', 'กำไร', 'อัตรากำไร %'], labels: ['รายได้', 'กำไร', 'อัตรากำไร'] }],
  benchmark: [{ sel: '#benchmarkTable', title: 'สาขา', keyCols: ['คะแนน', 'รายได้', 'ยอดบิลเฉลี่ย'] }],
  gpbranch: [{ sel: '#gpbTable', rows: 'tr.gpb-row', title: 'สาขา', sub: ['ต้นทุนอาหาร %'], subL: true, keyCols: ['เกินเป้า', 'GP (', 'GP %'], labels: ['เกินเป้า ฿', 'GP', 'GP %'], pill: 'สถานะ',
    tones: { 'ในเป้า': 'good', 'เกินเป้า': 'bad' } }],
  pnlbranch: [{ sel: '#pnlbTable', title: 'สาขา', sub: ['GP%', 'OP%'], subL: true, keyCols: ['รายได้', 'กำไรขั้นต้น', 'กำไรดำเนินงาน'] }],
  dailysales: [{ sel: 'table:has(#dsBody)', title: 'สาขา', sub: ['เทียบช่วงก่อน', 'เทียบปีก่อน'], subL: true, keyCols: ['บาทที่เปลี่ยน', 'ยอดขาย', 'ยอด/หัว'] },
    // PR-SC: a role that sees cost gets the ต้นทุนเพิ่ม column (table.sp-costed); PR-TD (owner 2026-10-25): it is the LAST column and the last figure
    { sel: '#spikeTable.sp-costed', title: 'เมนู', sub: ['เพิ่มจาก tier'], subLines: true, keyCols: [['ช่วงนี้', 'วันนี้'], 'สูงกว่า', 'ต้นทุนเพิ่ม'], labels: ['ต่อ 100 ลูกค้า', 'สูงกว่า', 'ต้นทุนเพิ่ม'] },
    { sel: '#spikeTable:not(.sp-costed)', title: 'เมนู', sub: ['เพิ่มจาก tier'], subLines: true, keyCols: [['ช่วงนี้', 'วันนี้'], ['ปกติ', 'ช่วงเทียบ'], 'สูงกว่า'], labels: ['ต่อ 100 ลูกค้า', 'ปกติ', 'สูงกว่า'] },
    { sel: '#spikePlatesTable', title: 'เมนู', keyCols: [['ช่วงนี้', 'วันนี้'], '+จาน', 'สูงกว่า'], labels: ['จาน', '+จาน', 'สูงกว่า'] },
    { sel: '#spikeNewTable', title: 'เมนู', keyCols: [['ช่วงนี้', 'วันนี้'], 'ต่อ 100 ลูกค้า', 'ขายมาแล้ว'], labels: ['จาน', 'ต่อ 100 ลูกค้า', 'ขายมาแล้ว (วัน)'] },
    // owner 2026-10-25 · วัตถุดิบมูลค่าสูง · จำนวนจาน: one group (plates only) and the branches x groups matrix
    { sel: '#spikeHvTable', title: 'สาขา', keyCols: ['จานที่ออก', 'จาน/หัว', 'ลูกค้า'], figSub: true },
    { sel: '#spikeHvMatrix', title: 'สาขา', keyCols: ['แซลมอน', 'ทูน่า', 'ลูกค้า'], figSub: true }],
  discounts: [
    { sel: 'table:has(#discCashierBody)', title: 'สาขา', keyCols: ['เกินเพดาน', '% ยอดขาย', 'ส่วนลดรวม'] },
    { sel: 'table:has(#disc-reason-body)', keyCols: [1, 2, 3] }],
  staff: [{ sel: '#staffAnomTable', title: 'บิล', keyCols: ['ส่วนลด', '% ของบิล', 'สาขา'] }],
  cancelbill: [
    { sel: 'table:has(#cbBranches)', title: 'สาขา', keyCols: ['มูลค่า', 'สัดส่วน', 'บิลยกเลิก'] },
    { sel: 'table:has(#cbReasons)', title: 'เหตุผล', keyCols: ['บิล', '%', 'มูลค่า'] }],
  cashctl: [
    { sel: 'table:has(#ccBody)', title: 'สาขา', keyCols: ['ขาด/เกิน', 'วันที่ขาด', '% ของยอด'] },
    { sel: 'table:has(#ccWorst)', title: 'สาขา', sub: ['วันที่'], keyCols: ['ขาด/เกิน', 'ยอดขาย', 'ลูกค้า'] }],
  costhead: [{ sel: 'table:has(#chBody)', title: 'สาขา', sub: ['vs เฉลี่ย'], subL: true, keyCols: ['ต้นทุน/หัว', 'ต้นทุน%', 'GP/หัว'] }],
  hvmonitor: [{ sel: '#hvmTable', title: 'สาขา', keyCols: ['ต้นทุน/หัว', 'จาน/หัว', 'ต้นทุน/จาน'], figSub: true },
    { sel: '#hvmMatrix', title: 'สาขา', keyCols: ['แซลมอน', 'ทูน่า', 'ต้นทุนอาหาร/หัว'], figSub: true }],
  coverage: [
    { sel: 'table:has(#cov-branches)', title: 'สาขา', sub: ['SKU ค้าง'], subL: true, keyCols: ['รายได้', 'ไม่มีต้นทุน', '% ไม่มี'] },
    { sel: 'table:has(#cov-review)', rows: 'tr[onclick]', title: 'เมนู', keyCols: ['ต้นทุน/ที่', 'จานเดือนนี้', 'ต้นทุนรวม/เดือน'], pill: 'สถานะ',
      tones: { 'ยืนยันแล้ว': 'good', 'ประมาณการ': 'warn', 'สูตรเสนอ': 'info' }, done: 'ยืนยันแล้ว' },
    { sel: 'table:has(#cov-fixer)', title: 'เมนู', sub: ['SKU', 'หมวด'], keyCols: ['รายได้', 'จำนวน', 'ราคาขาย/หน่วย'], fields: 'table' }],
  menuefficiency: [{ sel: 'table:has(#mce2Body)', title: 'เมนู', sub: ['หมวด', 'แหล่ง'], keyCols: ['ต้นทุนรวม', '%ต้นทุน', '฿/จาน'], pill: 'ABC' }],
  'orderhub|queue': [{ sel: '#ohbQueue table', all: true, title: 'ใบสั่งซื้อ', sub: ['ตัดรอบ'], subL: true, keyCols: ['มูลค่า', 'รายการ'], pill: 'สถานะ' }],
  'orderhub|claims': [{ sel: '#ohbCancelledReceipts table', title: 'สาขา', sub: ['ใบรับของ', 'เหตุผล'], keyCols: ['รายการ'] }],
  'orderhub|issues': [{ sel: '#ohbIsSummary table', title: 'สาขา', sub: ['ส่งช้า', 'ยังไม่ส่ง'], subL: true, keyCols: ['ตรงวัน %', 'มีใบ %', 'ไม่มีใบ'] }],
  'orderhub|issuevar': [{ sel: '#ohbIvList table', title: 'วันที่', keyCols: ['รวมเบิก', ['ตามสูตร', 'ที่สาขาใช้ปกติ'], ['เทียบสูตร', 'เทียบที่สาขาใช้ปกติ']], labels: ['รวมเบิก ฿', 'ปกติ ฿', 'เทียบ'], pill: 'ใบตัดเบิก' }],
  'orderhub|comp': [{ sel: '#ohbComp table.wt', title: 'สาขา', sub: ['ตัดรอบ'], subL: true, keyCols: ['สาขายืนยัน', 'ส่งจริง', 'รับจริง'], labels: ['สาขายืนยัน ฿', 'ส่งจริง ฿', 'รับจริง ฿'], pill: 'สถานะ' }],
  'orderhub|adoption': [{ sel: '#ohbAdList table', title: 'สาขา', sub: ['ใช้งานล่าสุด'], subL: true, keyCols: ['ใบตัดเบิกตรงวัน', 'ส่งใบสั่งซื้อตรงเวลา', 'รับของบันทึกแล้ว'],
    labels: ['ตัดเบิกตรงวัน', 'สั่งตรงเวลา', 'บันทึกรับของ'], pill: 'สถานะ', tones: { 'เขียว': 'good', 'เหลือง': 'warn', 'แดง': 'bad' } }],
  'orderhub|persales': [
    { sel: '#ohbPsBranches > .overflow-x-auto > table', title: 'สาขา', keyCols: [['เบี่ยงจากค่ากลาง', 'ยอดขาย'], 'มูลค่าวัตถุดิบต่อยอดขาย', 'การใช้เทียบค่ากลาง'], labels: ['เบี่ยงจากค่ากลาง ฿', 'วัตถุดิบต่อ ฿100,000', 'เทียบค่ากลาง'] },
    { sel: '#ohbPsList table', title: 'วัตถุดิบ', sub: ['หน่วย'], keyCols: [['เบี่ยงจากค่ากลาง', 'ต่อยอดขาย'], 'มูลค่า', ['การใช้เทียบค่ากลาง', 'เปลี่ยนแปลง']], labels: ['เบี่ยง ฿ / ต่อ ฿100,000', 'มูลค่า ฿', 'เทียบ'] }],
  'bomcenter|fc': [{ sel: '#bmcFcTable table', title: 'วัตถุดิบ', sub: ['รหัส'], keyCols: ['แนะนำสั่ง', 'มูลค่า', 'ใช้/วัน'], labels: ['แนะนำสั่ง', 'มูลค่า ฿', 'ใช้/วัน'], pill: 'กลุ่ม', sparkCell: 'แนวโน้ม' }],
  'bomcenter|price': [{ sel: 'table:has(#bmcPriceBody)', title: 'วัตถุดิบ', sub: ['รหัส', 'หน่วยฐาน'], keyCols: ['ผลเป็นบาท', 'เปลี่ยน', 'ราคา/หน่วยฐาน'], labels: ['ผลเป็นบาท', 'เปลี่ยน', 'ราคา/หน่วยฐาน'], pill: 'แหล่ง' }],
  'bomcenter|dishcost': [{ sel: '#bmcDcList table', title: 'เมนู', sub: ['รหัส POS'], keyCols: ['ผลเป็นบาท', 'ต้นทุนต่อจาน', 'เปลี่ยน'], pill: 'ที่มา' }],
  'bomcenter|ing': [{ sel: 'table:has(#bmcIngBody)', title: 'วัตถุดิบ', sub: ['รหัส', 'หน่วยฐาน'], keyCols: ['ราคา/หน่วยฐาน', 'ราคา/หน่วยรับ', 'ใช้ในเมนู'], pill: 'สถานะ', fields: 'table' }],
  'bomcenter|issue': [{ sel: '#bmcIssList table', title: 'สาขา', sub: ['งวด'], keyCols: ['มูลค่าตัดเบิก', 'ไม่มีราคา'], pill: 'สถานะ' }],
  feedback: [
    { sel: '#fbItems table', title: 'สาขา', sub: ['วันที่', 'ช่องทาง', 'ข้อความ'], keyCols: ['★', 'ตอบกลับ'], labels: ['ดาว', 'ตอบกลับ'], pill: 'ความรู้สึก',
      tones: { 'บวก|ชม|positive': 'good', 'ลบ|ติ|negative': 'bad', 'กลาง|neutral': 'neutral' } },
    { sel: '#fbBranches table', title: 'สาขา', sub: ['ปัญหาหลัก'], keyCols: ['เสียง', '★', 'ลบ %'], labels: ['เสียง', 'ดาว', 'ลบ %'] }],
};
// Pages that sit in no cluster or chip (the older single entries): listed in หน้ารายงานทั้งหมด under "อื่น ๆ".
const ALLPAGES_EXTRA = [
  { tab: 'home', label: 'สรุปประจำวัน' }, { tab: 'action', label: 'ต้องดูวันนี้' }, { tab: 'feedback', label: 'เสียงลูกค้า' },
  { tab: 'checklist', label: 'รายการตรวจเสียงลูกค้า' }, { tab: 'franchise', label: 'ร้านของฉัน', roles: ['franchise'] }, { tab: 'mgtreport', label: 'รายงานผู้บริหาร' },
  { tab: 'report', label: 'รายงานรายเดือน (ฉบับเต็ม)' }, { tab: 'manual', label: 'คู่มือการใช้งาน' }, { tab: 'settings', label: 'ตั้งค่า' },
];
// The model of หน้ารายงานทั้งหมด: every cluster tab (its first level, as the cluster bar lists them), every item of every supply chip and
// the pages above, as { group, tab, pane?, label }. `ok(entry)` filters (the live permission check; the check script passes () => true).
// Pure data in, data out - scripts/check-departments.js runs it to prove nothing in TAB_CLUSTERS or SUPPLY_CHIPS is left out.
function allPagesModel(ok) {
  const keep = ok || (() => true), groups = [], seen = new Set(), tabs = new Set();
  const add = (g, e) => {
    const k = e.tab + '|' + (e.pane || '');
    if (seen.has(k) || !keep(e)) return;
    seen.add(k); tabs.add(e.tab);
    let grp = groups.find(x => x.label === g);
    if (!grp) groups.push(grp = { label: g, entries: [] });
    grp.entries.push(e);
  };
  TAB_CLUSTERS.forEach(cl => {
    // a supply item keeps its `item`, so the live check can apply that item's own mods / roles (js/18-launcher.js)
    if (cl.chips) cl.chips.forEach(ch => ch.items.forEach(it => add(ch.key === 'home' ? cl.label : cl.label + ' · ' + ch.label, { tab: it.tab, pane: it.pane, label: it.label, item: it })));
    else cl.tabs.forEach(t => add(cl.label, { tab: t[0], label: t[1] }));
  });
  // a tab of the supply cluster that no chip lists (only the older ones could be) is still a link
  TAB_CLUSTERS.filter(cl => cl.chips).forEach(cl => cl.tabs.forEach(t => { if (!tabs.has(t[0])) add(cl.label, { tab: t[0], label: t[1] }); }));
  ALLPAGES_EXTRA.forEach(e => add('อื่น ๆ', e));
  return groups;
}
// PR-S1 · stock mode (BC_MODE, loadBcMode): an entry flagged `bom: true` in DEPARTMENTS or SUPPLY_CHIPS is a recipe page and is hidden while the
// mode is 'stock'. The flag names the PAGE: every occurrence of the same tab|pane (the department tile, the supply chip, the search words,
// หน้ารายงานทั้งหมด, the start card, a deep link) follows, because they all ask bomHidden / tabAllowed. An entry without `pane` hides the whole tab.
let _bomKeys = null;
function bomKeySet() {
  if (!_bomKeys) {
    const s = new Set();
    const add = e => { if (e && e.bom) s.add(e.tab + '|' + (e.pane || '')); };
    DEPARTMENTS.forEach(d => d.pages.forEach(add));
    SUPPLY_CHIPS.forEach(c => c.items.forEach(add));
    _bomKeys = s;
  }
  return _bomKeys;
}
// PR-S4 · the duty line of a department: `duty_stock` while the mode is 'stock' (a department whose recipe pages are hidden), else `duty`
// PR-S4 · one string, two modes: stkWord(full-mode text, stock-mode text)
function stkWord(full, stock) { return BC_MODE === 'stock' ? stock : full; }
function deptDuty(d) { return (BC_MODE === 'stock' && d.duty_stock) || d.duty; }
function bomHidden(tab, pane) {
  if (BC_MODE === 'full') return false;
  let s;
  try { s = bomKeySet(); } catch (e) { return false; }      // the registry is not defined yet (a call while the file loads)
  return s.has(tab + '|') || (!!pane && s.has(tab + '|' + pane));
}
// ── end page registry (scripts/check-departments.js evaluates everything from `const _SUPPLY_CENTRE` to this line)
// Group colours (docs/ui-kit/theme-groups.css): the group a tab belongs to -
// its cluster's key, else one of the stand-alone entries below.
const _GROUP_OF_LOOSE = { feedback: 'feedback', checklist: 'feedback' };
function _groupOf(name) { const cl = clusterOf(name); return cl ? cl.key : (_GROUP_OF_LOOSE[name] || 'home'); }
// Tag every sidebar item with its group once (the scripts load after the markup).
try {
  document.querySelectorAll('#sidebar .nav-item[id^="nav-"]').forEach(el => { el.dataset.g = _groupOf(el.id.slice(4)); });
} catch (e) { /* no sidebar on this page */ }
// ── Kill list — ON HOLD (owner's call, 2026-09-05) ─────────────────────────
// 47 days of usage telemetry (2026-07-20 → 09-05, Settings → การใช้งานจริง)
// found 17 tabs nobody opened (docs/LEAN_KILL_LIST_2026-09.html). The app is
// not yet used by everyone, so NOTHING is hidden or deleted: every tab shows in
// its cluster bar as before and the counters keep running. KILL_CANDIDATES is
// the list under watch (the Settings panel shows it with live counts);
// SOFT_KILL — what the cluster bar folds behind a "⋯" chip — stays EMPTY. To
// resume stage 1 later, move names from KILL_CANDIDATES into SOFT_KILL and set
// KILL_REVIEW_DATE. `action` has had no menu entry since before; `pnl` was brought back
// into the กำไร bar on 2026-10-01 (owner).
const KILL_CANDIDATES = ['action', 'pnl', 'profit', 'compare', 'costhead', 'pnlbill', 'modifiers', 'pricezone',
  'cancelbill', 'closetime', 'benchmark', 'staff', 'simhub', 'menuefficiency', 'menuopportunities', 'coverage',
  'costheadtop60'];
const SOFT_KILL = new Set([]);          // on hold — nothing folded
const KILL_REVIEW_DATE = '';            // no deletion date while on hold
// Formal theme: no emoji in the chrome (page title, cluster tabs). The labels in
// PAGE_TITLES / TAB_CLUSTERS still carry them; strip at render so the data stays put.
const _EMOJI_RE = /(?:[\u{1F000}-\u{1FAFF}\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF\u2190-\u21FF\u2139\u3030\u303D][\uFE0F\u200D]*)+/gu;
function _noEmoji(s) { return String(s).replace(_EMOJI_RE, '').replace(/\s{2,}/g, ' ').trim(); }

let _clusterMoreOpen = false;

function _updateClusterBar(name) {
  const bar = document.getElementById('clusterBar');
  if (!bar) return;
  const cl = clusterOf(name);
  document.body.dataset.group = _groupOf(name);   // theme-groups.css colours the page by group
  phNavRender();                                   // the phone's bottom navigation follows every switch (js/09c-mobile.js)
  if (typeof phFitSchedule === 'function') phFitSchedule();   // PR-MA: the page's phone display mode (พอดีจอ / แตะง่าย, js/09c-mobile.js)
  if (typeof updateDeptNav === 'function') updateDeptNav(name);   // the rail's department entry that holds this page lights up (js/18-launcher.js)
  if (typeof renderDeptHead === 'function') renderDeptHead(name);  // PR-D2: breadcrumb + the department's band and icon tabs (a desktop hides the bar below)
  // The group you are in stays lit in the sidebar, whichever of its views is open
  // (its entry is tabs[0], or the first member the role can open - applyPermissions).
  if (cl) {
    const entry = cl.tabs.map(([t]) => document.getElementById('nav-' + t))
      .find(el => el && getComputedStyle(el).display !== 'none');
    if (entry) entry.classList.add('active');
  }
  if (!cl) { bar.classList.add('hidden'); bar.classList.remove('flex'); bar.innerHTML = ''; return; }
  bar.classList.remove('hidden'); bar.classList.add('flex');
  bar.classList.toggle('supply-bar', !!cl.chips);       // the supply bar's chips read at 14px (index.html)
  if (cl.chips) { _renderSupplyBar(bar, cl, name); return; }   // วัตถุดิบและใบสั่งซื้อ: chips are panes
  const allowed = cl.tabs.filter(([t]) => tabAllowed(t));
  const shown = allowed.filter(([t]) => !SOFT_KILL.has(t));
  const folded = allowed.filter(([t]) => SOFT_KILL.has(t));
  const open = _clusterMoreOpen || folded.some(([t]) => t === name);   // the active tab is never hidden
  const chip = ([t, lbl, alias], dim) => {
    const on = t === name || (alias || []).includes(name);
    const title = dim && !on ? ` title="ไม่มีใครเปิดตั้งแต่ 2026-07-20 — ซ่อนไว้ก่อนลบ${KILL_REVIEW_DATE ? ' (ทบทวน ' + KILL_REVIEW_DATE + ')' : ''}"` : '';
    // the active chip wears the group colour (theme-groups.css); without that sheet the fallbacks are the old navy
    return `<button onclick="switchTab('${t}')"${title}${on ? ' aria-current="page"' : ''} class="text-xs rounded-lg px-3 py-1 transition-colors" style="${on
      ? 'background:var(--group-soft, var(--indigo));color:var(--group-ink, var(--on-indigo));border:1px solid var(--group, var(--indigo));font-weight:600'
      : 'background:var(--bg-input);color:var(--text-secondary);border:1px solid var(--border)'}${dim && !on ? ';opacity:.55' : ''}">${_noEmoji(lbl)}</button>`;
  };
  const more = !folded.length ? '' : open
    ? folded.map(c => chip(c, true)).join('') +
      `<button id="clusterFold" onclick="_clusterMoreOpen=false;_updateClusterBar('${name}')" title="ซ่อนแท็บที่ไม่ได้ใช้" class="text-xs rounded-lg px-2 py-1" style="background:transparent;color:var(--text-muted);border:1px dashed var(--border)">‹</button>`
    : `<button id="clusterMore" onclick="_clusterMoreOpen=true;_updateClusterBar('${name}')" title="แท็บที่ไม่มีใครเปิดตั้งแต่ 2026-07-20 (${folded.length}) — ซ่อนไว้ก่อนลบ${KILL_REVIEW_DATE ? ', ทบทวน ' + KILL_REVIEW_DATE : ''}" class="text-xs rounded-lg px-2 py-1" style="background:transparent;color:var(--text-muted);border:1px dashed var(--border)">⋯ ${folded.length}</button>`;
  bar.innerHTML = `<span class="text-xs font-semibold mr-1" style="color:var(--group-ink, var(--text-muted))">${_noEmoji(cl.label)}:</span>` +
    shown.map(c => chip(c, false)).join('') + more;
  // the phone shell draws the bar as one sideways row (index.html): keep the open page's chip in view
  const cur = phOn() ? /** @type {HTMLElement|null} */ (bar.querySelector('[aria-current="page"]')) : null;
  if (cur && bar.scrollWidth > bar.clientWidth + 1) bar.scrollLeft = Math.max(0, bar.scrollLeft + cur.getBoundingClientRect().left - bar.getBoundingClientRect().left - 24);
}

// ── วัตถุดิบและใบสั่งซื้อ: the two-level bar, deep links and the shared context ──
// The pane each tab remembers is a `let` in its own slice (_bmcSub, _brnSub,
// _ohbSub, _osSub); the slices register their accessors here as they load, so
// this file never reaches into a later one.
const _SUPPLY_PANE = {};     // tab → { get(), set(pane), show(pane), month?(m) }
let _supplyChip = null;      // the level-1 chip the user is in (derived from the open pane)
let _supplyLegacyOpen = false;   // the "หน้าระบบเดิม" group of ตั้งค่า is unfolded
function supplyLegacyToggle() { _supplyLegacyOpen = !_supplyLegacyOpen; _updateClusterBar(activeTab); }
const _SUPPLY_LANDING = {    // the entry click per role: the chip (and item) whose work the role does daily
  branch: { chip: 'branch', tab: 'branchstock', pane: 'issue' }, salessupport: { chip: 'orders' }, innovation: { chip: 'recipes' },
  warehouse: { chip: 'orders', tab: 'orderhub', pane: 'queue' },
  operation: { chip: 'orders', tab: 'orderhub', pane: 'comp' }, accounting: { chip: 'close', tab: 'bomcenter', pane: 'issue' },
};
// PR-S1: the kitchen's daily page in stock mode is the register (read only), not the recipes; every other landing is unchanged
function _supplyLanding(role) {
  if (role === 'innovation' && bomHidden('bomcenter', 'rec')) return { chip: 'master', tab: 'bomcenter', pane: 'ing' };
  return _SUPPLY_LANDING[role || ''] || {};
}
// a login whose _SUPPLY_LANDING names its daily supply page and who may open หน้างาน
function _supplyPhoneRole() {
  const r = window.AUTH && window.AUTH.enabled && window.AUTH.role;
  return !!(r && _SUPPLY_LANDING[r] && tabAllowed('supplyhome'));
}
function _phoneModeSaved() { try { return localStorage.getItem('oshinei_phone_mode') || ''; } catch (e) { return ''; } }
function _supplyLastKey() { return 'supply:last:' + ((window.AUTH && window.AUTH.username) || 'anon'); }
function _supplyLastGet() { try { return JSON.parse(localStorage.getItem(_supplyLastKey()) || '{}') || {}; } catch (e) { return {}; } }
// One month and one branch for the whole module (localStorage): every picker
// reads it when it fills and writes it back on change, so nobody re-selects.
// keyed by user: a shared or training phone must not hand one login another's branch
function _supplyCtxKey() { return 'oshinei_supply_ctx:' + ((window.AUTH && window.AUTH.username) || 'anon'); }
function supplyCtx() { try { return JSON.parse(localStorage.getItem(_supplyCtxKey()) || '{}') || {}; } catch (e) { return {}; } }
function supplyCtxSet(patch) {
  const c = Object.assign(supplyCtx(), patch || {});
  try { localStorage.setItem(_supplyCtxKey(), JSON.stringify(c)); } catch (e) {}
  return c;
}
// ── PR-M5: ONE month control for the whole module ────────────────────────────────
// A <select class="supply-month"> from the module's first month, at most 18 months back (U3; data-fwd="n" adds n months ahead), labelled "กันยายน 2026", value "2026-09".
// Every month control of the module (วัตถุดิบ & ราคา, ใบสั่งซื้อ, หน้างาน, ผลต่างของสาขา) starts from supplyCtx().month; when nobody has
// chosen yet it starts at this month on a working page and at the previous month on a page that closes a month
// (ปิดงวดเดือน, ใบสรุปตัดเบิก, สรุปผลต่างรายเดือน). A choice made on one of them is the choice on all of them.
const SUPPLY_MONTHS_BACK = 18;
function supplyMonthNow() { return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Bangkok' }).slice(0, 7); }
function supplyMonthAdd(ym, n) {
  const m = /^(\d{4})-(\d{2})$/.exec(String(ym || ''));
  if (!m) return '';
  const t = Number(m[1]) * 12 + Number(m[2]) - 1 + n;
  return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, '0')}`;
}
function supplyMonthDefault(prev) { return prev ? supplyMonthAdd(supplyMonthNow(), -1) : supplyMonthNow(); }
// U3 (owner 2026-10-22): the list starts at the first month the module has data in (`first`, GET …/first-month: a branch login's own start)
// instead of 18 months back - never later than this month, never more than 18 months; `fwd` months ahead and a kept older choice stay.
// The sales pages keep their own month lists.
function supplyMonthList(now, fwd, first, keep) {
  const list = [];
  for (let i = fwd; i > -SUPPLY_MONTHS_BACK; i--) {
    const m = supplyMonthAdd(now, i);
    if (i < 0 && first && m < first) break;
    list.push(m);
  }
  if (keep && /^\d{4}-\d{2}$/.test(keep) && !list.includes(keep)) { list.push(keep); list.sort().reverse(); }
  return list;
}
let _supplyFirstMonth = null, _supplyFirstAsked = false;
function _supplyFirstLoad() {
  if (_supplyFirstAsked) return;
  _supplyFirstAsked = true;
  const path = ['branch.stock', 'supply.ordering', 'bom.center', 'ops.report'].some(hasModule) ? '/api/bom-center/stock/first-month'
    : ['cost.analysis', 'bom.edit', 'bom.prices', 'bom.recipes'].some(hasModule) ? '/api/bom-center/first-month' : '';
  if (!path) return;
  _bmcJson(`${API}${path}`).then(d => {
    const m = d && d.first_month;
    if (!m || !/^\d{4}-\d{2}$/.test(m)) return;
    _supplyFirstMonth = m;
    document.querySelectorAll('select.supply-month').forEach(el => { if (el.options.length) supplyMonthFill(/** @type {HTMLSelectElement} */ (el)); });
  }).catch(() => { /* the 18 months stay */ });
}
function supplyMonthFill(el) {
  if (!el) return;
  _supplyFirstLoad();
  const keep = el.value, fwd = Number(el.dataset.fwd || 0);
  const list = supplyMonthList(supplyMonthNow(), fwd, _supplyFirstMonth, keep);
  el.innerHTML = list.map(m => `<option value="${m}">${fmtMonthTh(m)}</option>`).join('');
  if (keep && list.includes(keep)) el.value = keep;
}
// set the value of a month control (adding the month to its list when it is an older one); '' leaves it empty
function supplyMonthPut(el, m) {
  if (!el) return '';
  if (!el.options.length) supplyMonthFill(el);
  if (m && /^\d{4}-\d{2}$/.test(m) && ![...el.options].some(o => o.value === m)) el.add(new Option(fmtMonthTh(m), m));   // older than the list: still the choice
  el.value = m || '';
  return el.value;
}
// the month a control starts at: the shared choice, else the default of the page (prev = a page that closes a month)
function supplyMonthPick(el, prev) { return supplyMonthPut(el, supplyCtx().month || supplyMonthDefault(prev)); }
// the branches that have data (slips, orders, counts, receipts) in a month — the centre's branch selects list these only
const _supplyBrData = {};
async function supplyBranchesWithData(month) {
  // QA L4a: GET /api/bom-center/stock/* is open to these modules only - a role without one (the kitchen) never calls it (no console 403)
  if (!['branch.stock', 'supply.ordering', 'bom.center', 'ops.report'].some(hasModule)) throw new Error('no stock module');
  const hit = _supplyBrData[month];
  if (hit && Date.now() - hit.at < 60000) return hit.rows;
  const d = await _bmcJson(`${API}/api/bom-center/stock/me?month=${encodeURIComponent(month)}`);
  _supplyBrData[month] = { at: Date.now(), rows: d.branches || [] };
  return _supplyBrData[month].rows;
}
function supplyBranchOptions(rows, first) { return (first ? `<option value="">${first}</option>` : '') + rows.map(b => `<option value="${b.branch_id}">${esc(b.name)}</option>`).join(''); }
// "mine" by role (moved here from js/16-supplyhome.js in PR-D3: the phone shell draws its tasks before that slice is loaded)
// "mine" by role (owner_th names the department), not by module: management holds every
// module and would otherwise own every branch's count; a role without a word falls back to modules
const _SHM_ROLE_OWNERS = { branch: ['สาขา'], salessupport: ['ประสานงานขาย'], analyst: ['ฝ่ายวิเคราะห์'],
  accounting: ['บัญชี'], innovation: ['ครัว'], operation: ['ฝ่ายปฏิบัติการ'], management: ['ผู้บริหาร', 'ผู้ดูแลระบบ'],
  warehouse: ['คลัง'] };
// PR-U1 (2.7) · the viewer's own open tasks are decided ONCE, on the server (bom_center/nav_badges.mine_open - the rule of the red badges): the
// /todo answer carries their keys. Every "ยังไม่เสร็จ" of the launcher, หน้างาน and the phone keeps a todo task only when its key is there, so the
// number is the same on every screen. An answer without it (an old cache) keeps the old reading. A round whose cutoff is still to come
// (`upcoming`) is never a task of today: it is shown as "ถัดไป: …" and counts nowhere.
function shmOpenKeys(d) { return d && d.mine_open && Array.isArray(d.mine_open.keys) ? new Set(d.mine_open.keys) : null; }
function shmIsOpenTask(it, keys) {
  if (it.upcoming) return false;
  return keys ? keys.has(it.key) : true;
}
function _shmMine(it) {
  const role = window.AUTH && window.AUTH.enabled && window.AUTH.role;
  const words = role && _SHM_ROLE_OWNERS[role];
  if (words && it.owner_th) return words.some(w => it.owner_th.includes(w));
  return (it.owner_modules || []).some(hasModule);
}
function _supplyItemOf(tab, pane) {
  // PR-M7: a page can sit in two chips (one per role: the analyst's งานประจำเดือน, Accounting's ข้อมูลตั้งต้น), so the item the
  // person's role lists is looked for first; only then any item of the page
  for (const mine of [true, false]) {
    for (const ch of SUPPLY_CHIPS) {
      const it = ch.items.find(i => i.tab === tab && (!i.pane || i.pane === pane) && (!mine || _supplyRoleOk(i)));
      if (it) return { chip: ch, item: it };
    }
    for (const ch of SUPPLY_CHIPS) {        // a pane no chip lists: light the tab's first item
      const it = ch.items.find(i => i.tab === tab && (!mine || _supplyRoleOk(i)));
      if (it) return { chip: ch, item: it };
    }
  }
  return null;
}
// an item opens when its tab does and, if it names `mods`, the user holds one of them
// ... and, since PR-M2, when the person's role is one the item names (auth off: everything shows, as the role is management)
function _supplyRoleOk(it) { return !it.roles || it.roles.includes((window.AUTH && window.AUTH.enabled && window.AUTH.role) || 'management'); }   // auth off = the manager's bar
function _supplyItemAllowed(it) { return tabAllowed(it.tab) && !bomHidden(it.tab, it.pane) && (!it.mods || it.mods.some(hasModule)) && _supplyRoleOk(it); }   // PR-S1: a recipe pane is hidden in stock mode
function _supplyChipAllowed(ch) { return ch.items.some(_supplyItemAllowed); }

// Open one item of the module. The pane is remembered BEFORE switchTab so the
// tab's loader opens straight on it (one load, no flash of the previous pane);
// month / branch go into the shared context first so the pane reads them.
function supplyGo(o) {
  o = o || {};
  let tab = o.tab, pane = o.pane || null;
  if (!tab || !tabAllowed(tab) || bomHidden(tab, pane)) {       // PR-S1: a recipe pane in stock mode lands on the role's first visible page
    const first = SUPPLY_CHIPS.flatMap(c => c.items).find(_supplyItemAllowed);
    if (!first) return;
    tab = first.tab; pane = first.pane || null;
  }
  if (o.month) supplyCtxSet({ month: o.month });
  if (o.branch != null) supplyCtxSet({ branch_id: o.branch });
  const hit = _supplyItemOf(tab, pane);
  if (hit) {
    _supplyChip = hit.chip.key;
    const last = _supplyLastGet(); last[hit.chip.key] = { tab, pane };
    try { localStorage.setItem(_supplyLastKey(), JSON.stringify(last)); } catch (e) {}
  }
  const acc = _SUPPLY_PANE[tab];
  if (acc && o.month && acc.month) acc.month(o.month);
  if (acc && pane) acc.set(pane);
  if (activeTab === tab && acc && pane) acc.show(pane);   // same tab: move to the pane, don't reload the whole tab
  else switchTab(tab);
  if (o.anchor) setTimeout(() => { const el = document.getElementById(o.anchor); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 400);
}
// batch 3 (2.1) · a tap on a supply notification opens "/?go=supply&tab=<tab>&pane=<pane>" (routers/push_api.py, bom_center/reminders.py supply_url).
// The address is read once at load (09c-mobile.js keeps it here and drops it from the bar) or handed over by sw.js when the app is already open;
// it is applied when the shell has booted (ensureDesktopBooted). An unknown tab or pane falls back to หน้างาน; a role without the tab lands on its first page.
let _supplyPendingLink = '';
// PR-F4 · a tapped franchise-message notification: "/?go=fmsg&t=<thread>" (head office, the inbox) or "/?go=franchise&t=<thread>" (the owner,
// ร้านของฉัน). Kept here as {go, t} by 09c-mobile.js and opened by the page itself (loadFmsgInbox / frcMsgLoad, js/17-franchise.js).
/** @type {{go: string, t: number} | null} */
let _fmsgPending = null;
function fmsgParseLink(link) {
  let q;
  try { q = new URLSearchParams(String(link).includes('?') ? String(link).split('?')[1] : String(link)); } catch (e) { return null; }
  const go = q.get('go'), t = parseInt(q.get('t') || '', 10);
  if (go !== 'fmsg' && go !== 'franchise') return null;
  return { go, t: Number.isFinite(t) && t > 0 ? t : 0 };
}
// open what _fmsgPending names now (a notification tapped while the app is open); before the shell has booted the boot applies it
function fmsgOpenPending() {
  const p = _fmsgPending;
  if (!p) return;
  const tab = p.go === 'fmsg' ? 'fmsg' : 'franchise';
  if (!tabAllowed(tab)) { _fmsgPending = null; return; }
  const open = () => {
    if (activeTab !== tab) { switchTab(tab); return; }
    if (tab === 'fmsg' && typeof loadFmsgInbox === 'function') loadFmsgInbox();
    else if (tab === 'franchise' && typeof frcMsgLoad === 'function') frcMsgLoad();
  };
  if (phOn()) phOpenPage(open); else if (_desktopBooted) open();
}
function supplyDeepLink(link) {
  let q;
  try { q = new URLSearchParams(String(link).includes('?') ? String(link).split('?')[1] : String(link)); } catch (e) { return false; }
  // "?go=tab&tab=<tab>": the page that was open before a reload for a new version (appUpdateReload below)
  if (q.get('go') === 'tab') { const t = q.get('tab') || ''; if (!TABS[t] || !tabAllowed(t)) return false; switchTab(t); return true; }
  if (q.get('go') !== 'supply') return false;
  let tab = q.get('tab') || 'supplyhome', pane = q.get('pane') || null;
  if (!SUPPLY_CHIPS.some(c => c.items.some(i => i.tab === tab))) tab = 'supplyhome';
  if (pane && (!_supplyItemOf(tab, pane) || bomHidden(tab, pane))) pane = null;   // PR-S1: a recipe pane in stock mode opens the tab's own first page
  supplyGo({ tab, pane });
  return true;
}

// ── A new version after a deploy ──────────────────────────────────────────────────────────────────────────────────
// The shell carries its build (<meta name="app-build">, backend/main.py = sw.js BUILD). On return to the foreground
// (03-tabs-main.js visibilitychange) and every 10 min while visible, appUpdateCheck() asks GET /api/meta/build (no-store).
// A different build: registration.update() and wait for the new worker to take over (it deletes the old shell caches),
// then reload to the same page (supplyDeepLink: ?go=supply&tab&pane or ?go=tab&tab, kept in sessionStorage for one load).
// Unsaved input on the page (appHasUnsaved) or a second miss for the same build (the reload did not bring it) shows one
// persistent toast instead; a tap reloads once the saves in flight have finished. Never while offline.
const _APP_UPD = { build: '', at: 0, busy: false, toast: null, writes: 0, touched: new Set() };
try { const m = document.querySelector('meta[name="app-build"]'); _APP_UPD.build = (m && m.getAttribute('content')) || ''; } catch (e) { /* no shell meta */ }
try {
  const l = sessionStorage.getItem('oshinei_upd_link');
  if (l) { sessionStorage.removeItem('oshinei_upd_link'); if (!_supplyPendingLink) _supplyPendingLink = l; }
} catch (e) { /* no session storage */ }
// the saves in flight (POST/PUT/PATCH/DELETE): a tap on the toast waits for them
(function () {
  const f = window.fetch;
  window.fetch = function (input, init) {
    const m = (init && init.method) || (typeof input !== 'string' && input && input.method) || 'GET';
    if (!/^(POST|PUT|DELETE|PATCH)$/i.test(m)) return f.apply(this, arguments);
    _APP_UPD.writes++;
    let p;
    try { p = f.apply(this, arguments); } catch (e) { _APP_UPD.writes--; throw e; }
    return Promise.resolve(p).finally(() => { _APP_UPD.writes--; });
  };
})();
// what a person typed (not what a script filled in): a field still differing from what the page drew counts as unsaved
document.addEventListener('input', (e) => {
  const el = /** @type {any} */ (e.target);
  if (!el || !el.tagName) return;
  if (el.isContentEditable) { _APP_UPD.touched.add(el); return; }
  if (el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && !/^(search|checkbox|radio|file|button|submit|range|color|hidden|password)$/i.test(el.type || '')
      && !/search/i.test(el.id || ''))) _APP_UPD.touched.add(el);
}, true);
function appHasUnsaved() {
  try {
    if (_APP_UPD.writes > 0) return true;
    // the branch forms' own flags (js/14-branch.js): count, issue slip, order, receive
    if (typeof _brnDirty !== 'undefined' && _brnDirty) return true;
    if (typeof _brnIssDirty !== 'undefined' && _brnIssDirty) return true;
    if (typeof _brnOrdDirty !== 'undefined' && _brnOrdDirty) return true;
    if (typeof _brnRcv !== 'undefined' && _brnRcv) {
      const r = _brnRcv, box = document.getElementById('brnRecvOrder');
      const n = (r.picked || []).length + Object.keys(r.vals || {}).length + Object.keys(r.pcs || {}).length + Object.keys(r.wts || {}).length;
      if (n && box && box.offsetParent) return true;
    }
    if (document.querySelector('[data-dirty="1"], [data-ui-dialog]')) return true;      // a changed row (ตั้งค่าวันส่ง ...), an open dialog
    const a = /** @type {any} */ (document.activeElement);
    if (a && (a.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName || '')) && !/^(search|checkbox|radio|button|submit)$/i.test(a.type || '')) return true;
    for (const el of [..._APP_UPD.touched]) {
      if (!el.isConnected) { _APP_UPD.touched.delete(el); continue; }
      if (el.isContentEditable || el.value !== el.defaultValue) return true;
    }
    return false;
  } catch (e) { return true; }                 // unsure = unsaved
}
// the page open now, as a link the boot opens again (supplyDeepLink)
function _appUpdLink() {
  try {
    if (typeof _desktopBooted === 'undefined' || !_desktopBooted) return '';
    if (phOn() && typeof _phView !== 'undefined' && _phView !== 'page') return '';
    const tab = activeTab;
    if (!tab || tab === 'launcher' || tab === 'home' || !TABS[tab]) return '';
    if (SUPPLY_CHIPS.some(c => c.items.some(i => i.tab === tab))) {
      const acc = _SUPPLY_PANE[tab], pane = acc && acc.get ? acc.get() : '';
      return `?go=supply&tab=${encodeURIComponent(tab)}${pane ? '&pane=' + encodeURIComponent(pane) : ''}`;
    }
    return `?go=tab&tab=${encodeURIComponent(tab)}`;
  } catch (e) { return ''; }
}
// the new worker has activated (skipWaiting; its activate deletes the old shell caches) - at most 10 s
async function _appSwSettle() {
  if (!('serviceWorker' in navigator)) return;
  let reg = null;
  try { reg = await navigator.serviceWorker.getRegistration(); if (reg) await reg.update(); } catch (e) { return; }
  const w = reg && (reg.installing || reg.waiting);
  if (!w) return;
  await new Promise(res => {
    const t = setTimeout(res, 10000);
    const step = () => {
      if (w.state === 'activated' || w.state === 'redundant') { clearTimeout(t); res(); }
      else if (w.state === 'installed') w.postMessage({ type: 'skip-waiting' });
    };
    w.addEventListener('statechange', step);
    step();
  });
}
function appUpdateReload() {
  const l = _appUpdLink();
  try { if (l) sessionStorage.setItem('oshinei_upd_link', l); } catch (e) { /* the landing page then */ }
  location.reload();
}
function _appUpdToast() {
  if (_APP_UPD.toast && _APP_UPD.toast.el.isConnected) return;
  const h = uiToast('มีเวอร์ชันใหม่ · แตะเพื่อโหลด', 'info', { ms: 0 });
  _APP_UPD.toast = h;
  h.el.classList.add('ui-toast-update');
  h.el.addEventListener('click', async (e) => {
    if (/** @type {any} */ (e.target).closest('.ui-toast-x')) return;          // × only closes it
    for (let i = 0; i < 100 && _APP_UPD.writes > 0; i++) await new Promise(r => setTimeout(r, 200));   // the save finishes first
    if (_APP_UPD.writes > 0) { _appUpdToast(); return; }
    if (appHasUnsaved() && !(await uiConfirm('หน้านี้มีข้อมูลที่ยังไม่บันทึก หากโหลดตอนนี้ ข้อมูลนั้นจะหายไป',
      { title: 'โหลดเวอร์ชันใหม่', ok: 'โหลดเลย', cancel: 'กลับไปบันทึก', write: false }))) return;
    await _appSwSettle();
    appUpdateReload();
  });
}
async function appUpdateCheck() {
  if (!_APP_UPD.build || _APP_UPD.busy || document.hidden || navigator.onLine === false) return;
  if (Date.now() - _APP_UPD.at < 15000) return;          // the foreground hook and the timer in the same moment
  _APP_UPD.busy = true; _APP_UPD.at = Date.now();
  try {
    const r = await (window._rawFetch || fetch)(`${API}/api/meta/build`, { cache: 'no-store' });
    if (!r.ok) return;
    const b = String(((await r.json()) || {}).build || '');
    if (!b || b === _APP_UPD.build) return;
    let tried = '';
    try { tried = sessionStorage.getItem('oshinei_upd_build') || ''; } catch (e) { tried = b; }   // no session storage: no loop guard, so no auto-reload
    if (tried === b || appHasUnsaved()) { _appUpdToast(); return; }
    try { sessionStorage.setItem('oshinei_upd_build', b); } catch (e) { _appUpdToast(); return; }
    await _appSwSettle();
    if (appHasUnsaved() || !navigator.onLine) { _appUpdToast(); return; }   // typing started while the worker took over
    appUpdateReload();
  } catch (e) { /* offline or the server restarting: the next check */ }
  finally { _APP_UPD.busy = false; }
}
setInterval(() => { if (!document.hidden && Date.now() - _APP_UPD.at >= 10 * 60 * 1000) appUpdateCheck(); }, 60 * 1000);

// A level-1 chip: reopen the item last used there, else its first item the role can open.
function supplyChipOpen(key) {
  const ch = SUPPLY_CHIPS.find(c => c.key === key);
  if (!ch) return;
  const items = ch.items.filter(_supplyItemAllowed);
  const last = _supplyLastGet()[key];
  const it = (last && items.find(i => i.tab === last.tab && (i.pane || null) === (last.pane || null))) || items[0];
  if (it) supplyGo({ tab: it.tab, pane: it.pane });
}
// The sidebar entry (the module's nav items all call this; which one is visible is a permission matter).
function supplyOpen() {
  if (tabAllowed('supplyhome')) { supplyGo({ tab: 'supplyhome' }); return; }   // everyone lands on หน้างาน
  const want = _supplyLanding((window.AUTH && window.AUTH.role) || '');
  if (want.tab && tabAllowed(want.tab) && !bomHidden(want.tab, want.pane) && !_supplyLastGet()[want.chip]) { supplyGo({ tab: want.tab, pane: want.pane }); return; }
  const ch = (want.chip && SUPPLY_CHIPS.find(c => c.key === want.chip && _supplyChipAllowed(c))) || SUPPLY_CHIPS.find(_supplyChipAllowed);
  if (ch) supplyChipOpen(ch.key);
}
function _renderSupplyBar(bar, cl, name) {
  const acc = _SUPPLY_PANE[name];
  const hit = _supplyItemOf(name, acc ? acc.get() : null);
  const chips = cl.chips.filter(_supplyChipAllowed);
  if (!chips.length) { bar.classList.add('hidden'); bar.classList.remove('flex'); bar.innerHTML = ''; return; }
  const goJs = it => `supplyGo({tab:'${it.tab}'${it.pane ? `,pane:'${it.pane}'` : ''}})`;
  // PR-M2: the level-1 entries of THIS role. A chip holding one page is that page's own chip (named by the page), and a role with
  // few pages in all (≤ SUPPLY_FLAT_MAX: the warehouse, accounting, the kitchen, operations) gets one flat row of pages, no second row.
  // หน้างาน and สาขาของฉัน keep their own names.
  const vis = chips.map(c => ({ chip: c, items: c.items.filter(_supplyItemAllowed) }));
  const flat = vis.reduce((n, v) => n + v.items.length, 0) <= SUPPLY_FLAT_MAX;
  const entries = [];
  vis.forEach(v => {
    const keep = v.chip.key === 'home' || v.chip.key === 'branch';
    if (!keep && (flat || v.items.length === 1)) v.items.forEach(it => entries.push({ chip: v.chip, label: it.label, items: [it], page: it }));
    else entries.push({ chip: v.chip, label: v.chip.label, items: v.items, page: null });
  });
  // the lit entry is the one holding the open pane; _supplyChip only decides when none lists it
  let cur = hit ? entries.find(e => e.items.includes(hit.item)) : null;
  if (!cur) cur = entries.find(e => e.chip.key === _supplyChip) || entries[0];
  _supplyChip = cur.chip.key;
  const items = cur.items;
  const btn = (lbl, on, onclick, small) => `<button onclick="${onclick}" class="text-xs rounded-lg ${small ? 'px-2.5 py-0.5' : 'px-3 py-1'} transition-colors" style="${on
    ? 'background:var(--group-soft, var(--indigo));color:var(--group-ink, var(--on-indigo));border:1px solid var(--group, var(--indigo));font-weight:600'
    : 'background:var(--bg-input);color:var(--text-secondary);border:1px solid var(--border)'}">${_noEmoji(lbl)}</button>`;
  // a role with a single chip (the branch login) gets the level-2 row alone
  const lvl1 = entries.length > 1
    ? `<span class="text-xs font-semibold mr-1" style="color:var(--group-ink, var(--text-muted))">${_noEmoji(cl.label)}:</span>` +
      entries.map(e => btn(e.label, e === cur, e.page ? goJs(e.page) : `supplyChipOpen('${e.chip.key}')`, false)).join('')
    : '';
  // the ปลดล็อกการบันทึก chip sits at the right end of the first row there is
  const lock = '<span id="supplyLock" class="ml-auto"></span>';
  // PR-M2: the older pages sit in ONE group of their own ("หน้าระบบเดิม"), folded behind a single button of the level-2 row;
  // the group opens by itself when one of its pages is the open one
  const main = items.filter(it => it.group !== 'legacy'), legacy = items.filter(it => it.group === 'legacy');
  const legacyOn = !!(hit && legacy.includes(hit.item));
  const legacyShown = legacyOn || _supplyLegacyOpen;
  const legacyBtn = legacy.length
    ? `<button id="supplyLegacyBtn" aria-expanded="${legacyShown}" onclick="supplyLegacyToggle()" class="text-xs rounded-lg px-2.5 py-0.5 transition-colors" style="${legacyOn
        ? 'background:var(--group-soft, var(--indigo));color:var(--group-ink, var(--on-indigo));border:1px solid var(--group, var(--indigo));font-weight:600'
        : 'background:var(--bg-input);color:var(--text-secondary);border:1px dashed var(--border)'}">${SUPPLY_LEGACY_LABEL} ${legacyShown ? '▴' : '▾'}</button>`
    : '';
  const lvl2 = main.length + legacy.length > 1
    ? `<div class="flex flex-wrap items-center gap-1.5 w-full${entries.length > 1 ? ' mt-1' : ''}"><span class="text-[11px] mr-1" style="color:var(--text-muted)">${_noEmoji(cur.label)}:</span>${
        main.map(it => btn(it.label, !!(hit && it === hit.item), goJs(it), true)).join('')}${legacyBtn}${lvl1 ? '' : lock}</div>`
      + (legacy.length && legacyShown
        ? `<div id="supplyLegacyRow" class="flex flex-wrap items-center gap-1.5 w-full mt-1"><span class="text-[11px] mr-1" style="color:var(--text-muted)">${SUPPLY_LEGACY_LABEL}:</span>${
            legacy.map(it => btn(it.label, !!(hit && it === hit.item), goJs(it), true)).join('')}</div>` : '')
    : '';
  bar.innerHTML = lvl1 ? lvl1 + lock + lvl2 : (lvl2 || lock);
  _renderLockChip();
  if (window.writeToken && window.writeToken.unlocked === null && window.writeToken.gated === null) verifyWriteToken();
  const title = [cl.label, cur.page ? '' : cur.label, (items.length > 1 || cur.page) && hit ? hit.item.label : ''].filter(Boolean).map(_noEmoji).join(' · ');
  const pt = document.getElementById('pageTitle'); if (pt) pt.textContent = title;
}

function toggleSidebar() {
  const sb = document.getElementById('sidebar');
  sb.classList.toggle('collapsed');
  const icon = document.getElementById('toggleIcon');
  icon.innerHTML = sb.classList.contains('collapsed')
    ? '<path d="M9 18l6-6-6-6"/>'
    : '<path d="M15 18l-6-6 6-6"/>';
}

function switchSubtab(name) {
  activeSubtab = name;
  const subs = ['mostOrdered','mostCost','engineering','persona'];
  document.querySelectorAll('#tab-menu .subtab-btn').forEach((b, i) => {
    b.classList.toggle('active', subs[i] === name);
  });
  subs.forEach(s => {
    document.getElementById('sub-' + s)?.classList.toggle('hidden', s !== name);
  });
  loadMenuSubtab();
}

// ── Settings sub-tab state ─────────────────────────────────────────────────
let activeSettingsTab = 'targets';
let costPageOffset = 0;
const COST_PAGE_SIZE = 80;
let costSearchTimer = null;

function switchSettingsTab(name) {
  activeSettingsTab = name;
  document.querySelectorAll('#tab-settings .subtab-btn').forEach((b, i) => {
    const subs = ['targets','costs','fixedcosts','branches','hours','downtime','prefs','usage','deactivate','cloudsync','cloudbackup','cloudjobs','users'];
    b.classList.toggle('active', subs[i] === name);
  });
  ['targets','costs','fixedcosts','branches','hours','downtime','prefs','usage','deactivate','cloudsync','cloudbackup','cloudjobs','users'].forEach(s => {
    document.getElementById('settab-' + s).classList.toggle('hidden', s !== name);
  });
  loadSettingsSubtab();
}

// ── Buffet Performance sub-tab state ──────────────────────────────────────
let _activeBperfTab = 'overview';
const _BPERF_TABS = ['overview','tier','cover','gppnl','bomdeep','sim'];
// Phase 15: 4 main groups, each containing 1+ sub-tabs
const _BPERF_GROUPS = {
  overview:  { tabs: ['overview'],         default: 'overview' },
  customers: { tabs: ['cover'],            default: 'cover' },
  profit:    { tabs: ['gppnl'],            default: 'gppnl' },
  bomsim:    { tabs: ['bomdeep','sim'],    default: 'bomdeep' },
};
// reverse map for switchBperfTab to know which group owns a tab
const _BPERF_TAB_TO_GROUP = (() => {
  const m = {};
  for (const [g, info] of Object.entries(_BPERF_GROUPS)) info.tabs.forEach(t => m[t] = g);
  return m;
})();
let _activeBperfGroup = 'overview';
function switchBperfGroup(g) {
  if (!_BPERF_GROUPS[g]) return;
  _activeBperfGroup = g;
  // toggle level-1 group buttons
  document.querySelectorAll('#bperfGroupBar .subtab-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.bgroup === g));
  // show only this group's level-2 sub-nav (if any)
  ['customers','bomsim'].forEach(grp => {
    const nav = document.getElementById('bperfSubNav-' + grp);
    if (nav) nav.style.display = (grp === g) ? '' : 'none';
  });
  // activate the group's default (or last-active) sub-tab
  const tabs = _BPERF_GROUPS[g].tabs;
  const target = tabs.includes(_activeBperfTab) ? _activeBperfTab : _BPERF_GROUPS[g].default;
  switchBperfTab(target);
}

function switchBperfTab(name) {
  if (!_BPERF_TABS.includes(name)) return;
  _activeBperfTab = name;
  // Show active subtab div, hide others
  _BPERF_TABS.forEach(s => {
    const el = document.getElementById('bperftab-' + s);
    if (el) el.style.display = (s === name) ? 'block' : 'none';
  });
  // Update level-2 sub-tab button states (uses data-btab now, not index)
  document.querySelectorAll('#tab-buffetperf [data-btab]').forEach(b => {
    b.classList.toggle('active', b.dataset.btab === name);
  });
  // Also ensure the owning group is selected at level 1
  const ownerGroup = _BPERF_TAB_TO_GROUP[name];
  if (ownerGroup && _activeBperfGroup !== ownerGroup) {
    _activeBperfGroup = ownerGroup;
    document.querySelectorAll('#bperfGroupBar .subtab-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.bgroup === ownerGroup));
    ['customers','bomsim'].forEach(grp => {
      const nav = document.getElementById('bperfSubNav-' + grp);
      if (nav) nav.style.display = (grp === ownerGroup) ? '' : 'none';
    });
  }
  // Resize charts so anything rendered while hidden gets correct dimensions
  setTimeout(() => { Object.values(chartInstances).forEach(c => { try { c.resize(); } catch(e){} }); }, 30);
  // Re-run observer so lazy panels in newly visible subtab get picked up
  _observeBuffetPanels();
  // Phase 4: lazy-init BoM deep-dive panels
  if (name === 'bomdeep' && typeof initBomDeepDive === 'function') {
    initBomDeepDive();
  }
}

function loadSettingsSubtab() {
  if (activeSettingsTab === 'targets')    loadTargets();
  if (activeSettingsTab === 'costs')      { renderMenuPresetSelect(); loadCosts(); loadBomAudit(); }
  if (activeSettingsTab === 'fixedcosts') loadFixedCosts();
  if (activeSettingsTab === 'branches')   { loadBranchConfig(); if (typeof loadPzZonesRef === 'function') loadPzZonesRef(); }
  if (activeSettingsTab === 'deactivate') loadDeactivate();
  if (activeSettingsTab === 'hours')    loadClosedHours();
  if (activeSettingsTab === 'prefs')    loadPreferences();
  if (activeSettingsTab === 'users')    { loadUsers(); loadRolePermissions(); }
  if (activeSettingsTab === 'cloudsync') loadCloudSync();
  if (activeSettingsTab === 'cloudbackup') loadCloudBackup();
  if (activeSettingsTab === 'cloudjobs') loadCloudJobs();
  if (activeSettingsTab === 'usage')    loadUsageStats();
  if (activeSettingsTab === 'downtime') loadDowntime();
}

// ── Settings → วันที่สาขาปิด ────────────────────────────────────────────
// Recorded closures / pre-opening runs. The data-health check cannot tell these
// from a sync that lost a day (the POS has no "closed today" field), so without
// them it cries wolf for ever and a real gap drowns in the noise. Days recorded
// here are MOVED to a benign finding, never hidden — a wrong entry must stay
// visible rather than silently erase a genuine gap.
const DT_REASON_TH = { closed: 'ปิดชั่วคราว', pre_open: 'ก่อนเปิดขายจริง',
                       renovation: 'ปิดปรับปรุง', disaster: 'เหตุสุดวิสัย' };

async function loadDowntime() {
  const body = document.getElementById('dtBody');
  if (!body) return;
  body.innerHTML = '<tr><td colspan="8" class="py-3 text-sm" style="color:var(--text-muted)">กำลังโหลด…</td></tr>';
  const sel = document.getElementById('dtBranch');
  if (sel && !sel.options.length) {
    try {
      const bs = ((await api(`${API}/api/branches`)) || []).filter(b => b.active !== false);
      bs.forEach(b => sel.add(new Option(b.name, b.name)));
    } catch (e) { /* the add form simply stays empty; the list below still loads */ }
  }
  let d;
  try { d = await api(`${API}/api/branch-downtime`); }
  catch (e) { body.innerHTML = '<tr><td colspan="8" class="py-3 text-sm text-rose-500">โหลดไม่สำเร็จ</td></tr>'; return; }
  const rs = document.getElementById('dtReason');
  if (rs && !rs.options.length) (d.reasons || []).forEach(r => rs.add(new Option(DT_REASON_TH[r] || r, r)));
  const rows = d.rows || [];
  body.innerHTML = rows.length ? rows.map(r => `<tr class="border-t border-slate-100">
    <td class="py-2 pr-3 font-semibold">${esc(r.branch)}</td>
    <td class="py-2 pr-3 tabular-nums">${esc(r.date_from)}</td>
    <td class="py-2 pr-3 tabular-nums">${esc(r.date_to)}</td>
    <td class="py-2 pr-3 text-right tabular-nums font-semibold">${r.days}</td>
    <td class="py-2 pr-3">${esc(DT_REASON_TH[r.reason] || r.reason)}</td>
    <td class="py-2 pr-3 text-xs" style="color:var(--text-muted)">${esc(r.note || '')}</td>
    <td class="py-2 pr-3 text-xs" style="color:var(--text-muted)">${esc((r.created_at || '').slice(0, 10))}</td>
    <td class="py-2 text-right"><button onclick="deleteDowntime(${r.id})"
      class="text-xs px-2 py-1 rounded border" style="border-color:var(--border);color:var(--bad-ink)">ลบ</button></td></tr>`).join('')
    : '<tr><td colspan="8" class="py-3 text-sm" style="color:var(--text-muted)">ยังไม่มีการบันทึกช่วงปิดร้าน</td></tr>';
}

async function addDowntime() {
  const st = document.getElementById('dtStatus');
  const g = id => (document.getElementById(id) || {}).value || '';
  const payload = { branch: g('dtBranch'), date_from: g('dtFrom'), date_to: g('dtTo'),
                    reason: g('dtReason') || 'closed', note: g('dtNote') };
  if (!payload.branch || !payload.date_from || !payload.date_to) {
    st.textContent = 'กรอกสาขาและช่วงวันที่ให้ครบ'; st.style.color = 'var(--amber-ink)'; return;
  }
  st.textContent = 'กำลังบันทึก…'; st.style.color = 'var(--text-muted)';
  try {
    const r = await fetch(`${API}/api/branch-downtime`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      st.textContent = j.detail || `บันทึกไม่สำเร็จ (${r.status})`; st.style.color = 'var(--bad-ink)'; return;
    }
    st.textContent = '✓ บันทึกแล้ว'; st.style.color = 'var(--good-ink)';
    document.getElementById('dtNote').value = '';
    loadDowntime();
  } catch (e) { st.textContent = 'บันทึกไม่สำเร็จ'; st.style.color = 'var(--bad-ink)'; }
}

async function deleteDowntime(id) {
  // Deleting brings the days back as unexplained rather than losing them — say so,
  // so nobody reads this button as "delete the sales data".
  if (!await uiConfirm('ลบรายการนี้? วันเหล่านั้นจะกลับไปอยู่หมวด “ยังไม่ทราบสาเหตุ” ในการตรวจสุขภาพข้อมูล')) return;
  const st = document.getElementById('dtStatus');
  try {
    const r = await fetch(`${API}/api/branch-downtime/${id}`, { method: 'DELETE' });
    if (!r.ok) { st.textContent = `ลบไม่สำเร็จ (${r.status})`; st.style.color = 'var(--bad-ink)'; return; }
    st.textContent = '✓ ลบแล้ว'; st.style.color = 'var(--good-ink)';
    loadDowntime();
  } catch (e) { st.textContent = 'ลบไม่สำเร็จ'; st.style.color = 'var(--bad-ink)'; }
}

// ── Usage telemetry (Settings → การใช้งานจริง · lean-overhaul Phase 0) ──
async function loadUsageStats() {
  const el = document.getElementById('usageStatsBody');
  if (!el) return;
  el.textContent = 'กำลังโหลด…';
  const days = document.getElementById('usageDays')?.value || 30;
  let d = null, people = null;
  try { d = await api(`${API}/api/usage/summary?days=${days}`); } catch(e) {}
  if (!d) { el.textContent = 'โหลดข้อมูลไม่สำเร็จ'; return; }
  try { people = await api(`${API}/api/usage/users?days=${days}`); } catch(e) {}
  let digest = null;
  if (people) { try { digest = await api(`${API}/api/usage/adoption-digest?days=${days}`); } catch(e) {} }
  const esc = s => String(s === undefined || s === null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const tabs = d.tabs || [];
  const seen = new Set(tabs.map(t => t.name));
  const watched = new Set(SOFT_KILL.size ? SOFT_KILL : KILL_CANDIDATES);
  const never = Object.keys(PAGE_TITLES).filter(t => !seen.has(t) && !watched.has(t));
  const row = t => `<tr class="border-b" style="border-color:var(--border)"><td class="py-1.5 pr-3">${esc(PAGE_TITLES[t.name] || t.name)}${watched.has(t.name) ? ' <span class="text-[10px]" style="color:var(--amber-ink)">(เฝ้าดู)</span>' : ''}</td><td class="py-1.5 pr-3 text-right font-semibold">${(+t.hits).toLocaleString()}</td><td class="py-1.5 pr-3 text-right">${esc(t.days_seen)}</td><td class="py-1.5 text-right" style="color:var(--text-muted)">${esc(t.last_seen || '')}</td></tr>`;
  const apiRows = (d.api || []).slice(0, 20);
  // the kill list: ON HOLD while adoption is incomplete (nothing hidden/deleted) — the
  // candidates are listed with live counts; when SOFT_KILL is non-empty, that is what is folded
  const onHold = SOFT_KILL.size === 0;
  const byName = Object.fromEntries(tabs.map(t => [t.name, t]));
  const killRows = [...watched].map(t => { const u = byName[t]; return `<tr class="border-b" style="border-color:var(--border)"><td class="py-1 pr-3">${esc(PAGE_TITLES[t] || t)}</td><td class="py-1 pr-3 text-right font-semibold" style="${u ? 'color:var(--amber-ink)' : ''}">${u ? (+u.hits).toLocaleString() : 0}</td><td class="py-1 text-right" style="color:var(--text-muted)">${esc(u ? u.last_seen : '—')}</td></tr>`; }).join('');
  const killHead = onHold
    ? `Kill list — <b>พักไว้</b> · แท็บที่เฝ้าดู (${watched.size}) ${uiQ('usage:kill', 'Kill list พักไว้', '<p>เจ้าของตัดสิน 2026-09-05: รอให้ทุกคนใช้งานครบก่อน · ยังไม่ซ่อน/ไม่ลบแท็บใด</p>')}`
    : `Kill list — ซ่อนหลังปุ่ม ⋯ ในแถบมุมมองย่อยแล้ว (${SOFT_KILL.size})${KILL_REVIEW_DATE ? ' · ทบทวน ' + esc(KILL_REVIEW_DATE) : ''}`;
  // ── who uses it: the adoption view (the owner's question — who has and hasn't started) ──
  const STATUS = { active: ['ใช้งานอยู่', '#15803d', '#dcfce7'], idle: ['ไม่ได้ใช้ในช่วงนี้', '#b45309', '#fef3c7'],
                   never: ['ยังไม่เคยเข้าใช้ (รหัสเริ่มต้น)', '#b91c1c', '#fee2e2'], anonymous: ['ไม่มี login', '#6b7280', '#e5e7eb'] };
  const fmtTs = t => t ? esc(String(t).slice(0, 16).replace('T', ' ')) : '—';
  const personRow = u => { const st = STATUS[u.status] || STATUS.anonymous; return `<tr class="border-b" style="border-color:var(--border)">
      <td class="py-1.5 pr-3 font-semibold">${esc(u.username)}${u.active ? '' : ' <span class="text-[10px]" style="color:var(--text-muted)">(ปิดใช้)</span>'}</td>
      <td class="py-1.5 pr-3">${esc(u.role || '')}${u.branch ? '<br><span class="text-[10px]" style="color:var(--text-muted)">' + esc(u.branch) + '</span>' : ''}</td>
      <td class="py-1.5 pr-3"><span class="text-[11px] px-2 py-0.5 rounded-full font-semibold" style="color:${st[1]};background:${st[2]}">${st[0]}</span></td>
      <td class="py-1.5 pr-3" style="color:var(--text-muted)">${fmtTs(u.last_login)}</td>
      <td class="py-1.5 pr-3 text-right">${(+u.logins).toLocaleString()}${u.failed ? ` <span title="login ไม่สำเร็จ" style="color:var(--bad-ink)">(✗${u.failed})</span>` : ''}</td>
      <td class="py-1.5 pr-3 text-right">${(+u.days_seen).toLocaleString()}</td>
      <td class="py-1.5 pr-3 text-right font-semibold">${(+u.tab_hits).toLocaleString()}</td>
      <td class="py-1.5 text-[11px]" style="color:var(--text-secondary)">${(u.top_tabs || []).map(t => esc(PAGE_TITLES[t.tab] || t.tab) + ' (' + t.hits + ')').join(' · ') || '—'}</td></tr>`; };
  const ps = people && people.summary ? people.summary : null;
  const peopleHtml = people ? `
    <div class="mb-6">
      <div class="text-xs font-semibold mb-1" style="color:var(--text-secondary)">การเข้าใช้รายคน (${esc(days)} วันล่าสุด) — ${ps ? `<b style="color:var(--good-ink)">${ps.active}</b> ใช้งานอยู่ · <b style="color:var(--amber-ink)">${ps.idle}</b> ไม่ได้ใช้ในช่วงนี้ · <b style="color:var(--bad-ink)">${ps.never}</b> ยังไม่เคยเข้า · จาก ${ps.accounts} บัญชี` : ''} ${uiQ('usage:people', 'การเข้าใช้รายคน', '<p>"ยังไม่เคยเข้าใช้" = ยังไม่มี login สำเร็จและยังใช้รหัสเริ่มต้นอยู่ — คนที่ควรชวนเข้าใช้ก่อน · นับการเปิดแท็บของแต่ละคนตั้งแต่ 2026-09-05 (การเรียก API ของระบบ sync/สำรองข้อมูลไม่นับ)</p>')}</div>
      ${digest ? `<div class="text-[11px] mb-2 flex items-center gap-2 flex-wrap"><span style="color:var(--text-secondary)">สรุปรายสัปดาห์ (ทุกวันจันทร์ · หายไปเกิน 14 วัน / ยังไม่เคยเข้า): ${digest.latest ? `ล่าสุด <b>${esc(fmtWeekTh(digest.latest.week))}</b> — ${digest.latest.sent ? 'ส่ง Telegram แล้ว' : (digest.telegram_configured ? 'ส่ง Telegram ไม่สำเร็จ' : 'ยังไม่ได้ตั้ง Telegram — แสดงในแอปเท่านั้น')}` : 'ยังไม่มี — จะสร้างเองวันจันทร์แรกที่มีคนเปิดแอป'}</span>
        <button onclick="sendAdoptionDigest()" class="text-[11px] px-2 py-0.5 rounded-lg" style="border:1px solid var(--border);color:var(--text-secondary)">สร้าง/ส่งสรุปตอนนี้</button><span id="adoptionDigestStatus" class="text-[11px]"></span></div>` : ''}
      <div style="overflow-x:auto"><table class="text-xs w-full"><thead><tr class="text-left border-b" style="color:var(--text-muted);border-color:var(--border)"><th class="py-1 pr-3">ผู้ใช้</th><th class="py-1 pr-3">บทบาท / สาขา</th><th class="py-1 pr-3">สถานะ</th><th class="py-1 pr-3">เข้าสู่ระบบล่าสุด</th><th class="py-1 pr-3 text-right">เข้าสู่ระบบ</th><th class="py-1 pr-3 text-right">วันที่ใช้</th><th class="py-1 pr-3 text-right">เปิดแท็บ</th><th class="py-1">แท็บที่ใช้บ่อย</th></tr></thead>
      <tbody>${(people.users || []).map(personRow).join('') || '<tr><td class="py-2" colspan="8" style="color:var(--text-muted)">ยังไม่มีบัญชี</td></tr>'}</tbody></table></div>
    </div>` : '';
  el.innerHTML = peopleHtml + `
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <div>
        <div class="text-xs font-semibold mb-2" style="color:var(--text-secondary)">แท็บที่ถูกเปิด (${esc(days)} วันล่าสุด)</div>
        <table class="text-xs w-full"><thead><tr class="text-left border-b" style="color:var(--text-muted);border-color:var(--border)"><th class="py-1 pr-3">แท็บ</th><th class="py-1 pr-3 text-right">ครั้ง</th><th class="py-1 pr-3 text-right">จำนวนวัน</th><th class="py-1 text-right">ล่าสุด</th></tr></thead><tbody>${tabs.map(row).join('') || '<tr><td class="py-2" colspan="4" style="color:var(--text-muted)">ยังไม่มีข้อมูล — เริ่มนับหลัง deploy เวอร์ชันนี้</td></tr>'}</tbody></table>
      </div>
      <div>
        <div class="text-xs font-semibold mb-2" style="color:${onHold ? 'var(--amber-ink)' : 'var(--bad-ink)'}">${killHead} · <a href="/docs/LEAN_KILL_LIST_2026-09.html" target="_blank" class="underline">รายงาน</a></div>
        <table class="text-xs w-full mb-5"><thead><tr class="text-left border-b" style="color:var(--text-muted);border-color:var(--border)"><th class="py-1 pr-3">แท็บ</th><th class="py-1 pr-3 text-right">ครั้ง (${esc(days)} วัน)</th><th class="py-1 text-right">ล่าสุด</th></tr></thead><tbody>${killRows}</tbody></table>
        <div class="text-xs font-semibold mb-2" style="color:var(--bad-ink)">แท็บอื่นที่ยังไม่ถูกเปิดในช่วงนี้ (${never.length})</div>
        <div class="flex flex-wrap gap-1.5 mb-5">${never.map(t => `<span class="text-[11px] px-2 py-0.5 rounded-full" style="background:var(--bg-input);color:var(--text-muted)">${esc(PAGE_TITLES[t])}</span>`).join('') || '<span class="text-xs" style="color:var(--text-muted)">— ทุกแท็บถูกเปิดแล้ว</span>'}</div>
        <div class="text-xs font-semibold mb-2" style="color:var(--text-secondary)">Top 20 API endpoints</div>
        <table class="text-xs w-full"><tbody>${apiRows.map(a => `<tr class="border-b" style="border-color:var(--border)"><td class="py-1 pr-3 font-mono text-[10px]">${esc(a.name)}</td><td class="py-1 text-right font-semibold">${(+a.hits).toLocaleString()}</td></tr>`).join('') || '<tr><td class="py-2" style="color:var(--text-muted)">ยังไม่มีข้อมูล</td></tr>'}</tbody></table>
      </div>
    </div>`;
}

async function sendAdoptionDigest() {
  const st = document.getElementById('adoptionDigestStatus'); if (st) st.textContent = 'กำลังสร้าง…';
  try {
    const r = await api(`${API}/api/usage/adoption-digest/send`, { method: 'POST' });
    if (st) st.textContent = `✓ สัปดาห์ ${fmtWeekTh(r.week)}: ${r.sent ? 'ส่ง Telegram แล้ว' : (r.telegram_configured ? 'ส่ง Telegram ไม่สำเร็จ' : 'เก็บในแอปแล้ว (ยังไม่มี Telegram)')}`;
    setTimeout(loadUsageStats, 1500);
  } catch (e) { if (st) st.textContent = '✗ ' + (e.message || e); }
}

// ── Settings → ข้อมูลจาก POS: the cloud pull of Easy POS (backend/cloud_sync.py · docs/CLOUD_SYNC.md) ──
// Labels and numbers only (PR-D6): the explanation sits behind the "?" in the card's header. Management only.
const CS_RESULT_TH = { ok: 'สำเร็จ', failed: 'ไม่สำเร็จ', reconcile_failed: 'ไม่ผ่านการตรวจเทียบ', locked: 'ข้ามรอบ (มีงานอื่นทำอยู่)',
                       no_space: 'พื้นที่ไม่พอ', creds_missing: 'ยังไม่ตั้งรหัสเชื่อมต่อ POS' };
let _csTimer = null;
function _csWhen(iso) {                       // 2026-10-21T06:12:05+07:00 -> 21/10 06:12
  const m = /^\d{4}-(\d\d)-(\d\d)T(\d\d:\d\d)/.exec(iso || '');
  return m ? `${m[2]}/${m[1]} ${m[3]}` : '—';
}
async function loadCloudSync() {
  const el = document.getElementById('cloudSyncBody');
  if (!el) return;
  clearTimeout(_csTimer);
  let d = null;
  try { const r = await fetch(`${API}/api/admin/cloud-sync/status`); d = r.ok ? await r.json() : null; } catch (e) { d = null; }
  if (!d) { el.textContent = 'โหลดข้อมูลไม่สำเร็จ'; return; }
  const s = d.status || {};
  const bad = s.result && s.result !== 'ok';
  const chip = s.result
    ? `<span class="text-[11px] px-2 py-0.5 rounded-full font-semibold" style="color:var(${bad ? '--bad-ink' : '--good-ink'});border:1px solid currentColor">${esc(CS_RESULT_TH[s.result] || s.result)}</span>`
    : '—';
  const row = (k, v) => `<div class="flex justify-between gap-3 py-1.5 border-b" style="border-color:var(--border)"><span style="color:var(--text-muted)">${k}</span><span class="text-right font-semibold" style="color:var(--text-primary)">${v}</span></div>`;
  const num = n => (n == null ? '—' : (+n).toLocaleString());
  el.innerHTML =
    row('สถานะ', d.running ? 'กำลังดึงข้อมูล' : (d.enabled ? 'เปิดใช้' : 'ปิดอยู่')) +
    row('ผลรอบล่าสุด', chip) +
    row('เริ่ม', esc(_csWhen(s.last_start))) +
    row('จบ', esc(_csWhen(s.last_end)) + (s.duration_s != null ? ` · ${Math.round(s.duration_s / 60)} นาที` : '')) +
    row('สำเร็จล่าสุด', esc(_csWhen(s.last_ok))) +
    row('จำนวนบิลหลังดึง', num(s.tx_count)) +
    row('รอบถัดไป', d.enabled ? `${esc(_csWhen(d.next_run))} · ${d.next_kind === 'full' ? 'ดึงใหม่ทั้งหมด' : '45 วันล่าสุด'}` : '—') +
    row('พื้นที่ว่าง / ที่ต้องการ', d.free_gb == null ? '—' : `${d.free_gb} / ${d.need_gb} GB`) +
    row('หน่วยความจำสูงสุด', s.peak_rss_mb ? `${num(s.peak_rss_mb)} MB` : '—') +
    row('รหัสเชื่อมต่อ POS', d.creds_present ? 'ตั้งแล้ว' : 'ยังไม่ตั้ง') +
    ((s.log_tail || []).length ? `<details class="mt-3"><summary class="text-xs cursor-pointer" style="color:var(--text-muted)">บันทึกล่าสุด (${s.log_tail.length} บรรทัด)</summary><pre class="text-[11px] mt-2 p-2 rounded-lg overflow-x-auto" style="background:var(--bg-input);color:var(--text-secondary);white-space:pre-wrap">${esc(s.log_tail.join('\n'))}</pre></details>` : '');
  const btn = document.getElementById('csRunBtn');
  if (btn) { btn.disabled = !d.enabled || !!d.running; btn.style.opacity = btn.disabled ? '.5' : ''; }
  if (d.running) _csTimer = setTimeout(() => { if (activeSettingsTab === 'cloudsync') loadCloudSync(); }, 15000);
}
async function cloudSyncRun() {
  const btn = document.getElementById('csRunBtn');
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(`${API}/api/admin/cloud-sync/run`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const j = await r.json().catch(() => ({}));
    if (r.ok) uiToast('เริ่มดึงข้อมูลแล้ว', 'success');
    else uiToast(j.detail || ('ดึงข้อมูลไม่ได้ (' + r.status + ')'), 'error');
  } catch (e) { uiToast('ดึงข้อมูลไม่ได้: ' + (e.message || e), 'error'); }
  setTimeout(loadCloudSync, 1500);
}

// ── Settings → สำรองข้อมูล: the cloud backup of oshinei.db (backend/cloud_backup.py · docs/CLOUD_SYNC.md) ──
// Labels and numbers only; the explanation sits behind the "?" in the card's header. Management only.
const CB_RESULT_TH = { ok: 'สำเร็จ', failed: 'ไม่สำเร็จ', verify_failed: 'ทดสอบกู้คืนไม่ผ่าน', locked: 'ข้ามรอบ (มีงานอื่นทำอยู่)',
                       no_space: 'พื้นที่ไม่พอ', creds_missing: 'ยังไม่ตั้งรหัสที่เก็บข้อมูลสำรอง' };
let _cbTimer = null;
async function loadCloudBackup() {
  const el = document.getElementById('cloudBackupBody');
  if (!el) return;
  clearTimeout(_cbTimer);
  let d = null;
  try { const r = await fetch(`${API}/api/admin/cloud-backup/status`); d = r.ok ? await r.json() : null; } catch (e) { d = null; }
  if (!d) { el.textContent = 'โหลดข้อมูลไม่สำเร็จ'; return; }
  const s = d.status || {};
  const bad = s.result && s.result !== 'ok';
  const chip = s.result
    ? `<span class="text-[11px] px-2 py-0.5 rounded-full font-semibold" style="color:var(${bad ? '--bad-ink' : '--good-ink'});border:1px solid currentColor">${esc(CB_RESULT_TH[s.result] || s.result)}</span>`
    : '—';
  const vchip = s.verify
    ? `<span class="text-[11px] px-2 py-0.5 rounded-full font-semibold" style="color:var(${s.verify === 'PASS' ? '--good-ink' : '--bad-ink'});border:1px solid currentColor">${esc(s.verify)}</span>`
    : '—';
  const row = (k, v) => `<div class="flex justify-between gap-3 py-1.5 border-b" style="border-color:var(--border)"><span style="color:var(--text-muted)">${k}</span><span class="text-right font-semibold" style="color:var(--text-primary)">${v}</span></div>`;
  const num = n => (n == null ? '—' : (+n).toLocaleString());
  const objs = d.objects || [];
  const kb = n => (n / 1e6).toFixed(0) + ' MB';
  el.innerHTML =
    row('สถานะ', d.running ? 'กำลังสำรองข้อมูล' : (d.enabled ? 'เปิดใช้' : 'ปิดอยู่')) +
    row('ผลรอบล่าสุด', chip) +
    row('ทดสอบกู้คืน', vchip) +
    row('เริ่ม', esc(_csWhen(s.last_start))) +
    row('จบ', esc(_csWhen(s.last_end)) + (s.duration_s != null ? ` · ${Math.max(1, Math.round(s.duration_s / 60))} นาที` : '')) +
    row('สำเร็จล่าสุด', esc(_csWhen(s.last_ok))) +
    row('ขนาดไฟล์', s.size_mb ? `${num(s.size_mb)} MB` : '—') +
    row('จำนวนสำเนาที่เก็บ', d.objects_error ? '—' : `${objs.length} (7 วัน + 6 เดือน)`) +
    row('รอบถัดไป', d.enabled ? esc(_csWhen(d.next_run)) : '—') +
    row('พื้นที่ว่าง / ที่ต้องการ', d.free_gb == null ? '—' : `${d.free_gb} / ${d.need_gb} GB`) +
    row('รหัสที่เก็บข้อมูลสำรอง', d.creds_present ? 'ตั้งแล้ว' : 'ยังไม่ตั้ง') +
    (d.objects_error ? `<div class="text-xs mt-2" style="color:var(--bad-ink)">อ่านรายการสำเนาไม่สำเร็จ: ${esc(d.objects_error)}</div>` : '') +
    (objs.length ? `<details class="mt-3"><summary class="text-xs cursor-pointer" style="color:var(--text-muted)">สำเนาในที่เก็บข้อมูล (${objs.length})</summary><div class="text-[12px] mt-2" style="color:var(--text-secondary)">${objs.map(o => `<div class="flex justify-between gap-3 py-0.5"><span>${esc(o.key.replace(/^db\//, ''))}</span><span>${kb(o.size)}</span></div>`).join('')}</div></details>` : '') +
    ((s.log_tail || []).length ? `<details class="mt-3"><summary class="text-xs cursor-pointer" style="color:var(--text-muted)">บันทึกล่าสุด (${s.log_tail.length} บรรทัด)</summary><pre class="text-[11px] mt-2 p-2 rounded-lg overflow-x-auto" style="background:var(--bg-input);color:var(--text-secondary);white-space:pre-wrap">${esc(s.log_tail.join('\n'))}</pre></details>` : '');
  const btn = document.getElementById('cbRunBtn');
  if (btn) { btn.disabled = !d.enabled || !!d.running; btn.style.opacity = btn.disabled ? '.5' : ''; }
  if (d.running) _cbTimer = setTimeout(() => { if (activeSettingsTab === 'cloudbackup') loadCloudBackup(); }, 15000);
}
async function cloudBackupRun() {
  const btn = document.getElementById('cbRunBtn');
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(`${API}/api/admin/cloud-backup/run`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const j = await r.json().catch(() => ({}));
    if (r.ok) uiToast('เริ่มสำรองข้อมูลแล้ว', 'success');
    else uiToast(j.detail || ('สำรองข้อมูลไม่ได้ (' + r.status + ')'), 'error');
  } catch (e) { uiToast('สำรองข้อมูลไม่ได้: ' + (e.message || e), 'error'); }
  setTimeout(loadCloudBackup, 1500);
}


// ── Settings → งานอัตโนมัติ: every job the cloud runs by itself (backend/cloud_jobs.py · docs/CLOUD_SYNC.md) ──
// A table of labels and numbers (name, time, last result, next run, run-now); the explanation sits behind the "?". Management only.
const CJ_RESULT_TH = { ok: 'สำเร็จ', failed: 'ไม่สำเร็จ', verify_failed: 'ทดสอบกู้คืนไม่ผ่าน', reconcile_failed: 'ไม่ผ่านการตรวจเทียบ',
                       locked: 'ข้ามรอบ (มีงานอื่นทำอยู่)', no_space: 'พื้นที่ไม่พอ', creds_missing: 'ยังไม่ตั้งรหัส',
                       no_source: 'ยังไม่มีช่องทางรีวิว', findings: 'พบปัญหา' };
let _cjTimer = null;
async function loadCloudJobs() {
  const el = document.getElementById('cloudJobsBody');
  if (!el) return;
  clearTimeout(_cjTimer);
  let d = null;
  try { const r = await fetch(`${API}/api/admin/cloud-jobs/status`); d = r.ok ? await r.json() : null; } catch (e) { d = null; }
  if (!d) { el.textContent = 'โหลดข้อมูลไม่สำเร็จ'; return; }
  const chip = j => {
    if (j.running) return '<span class="text-[11px] px-2 py-0.5 rounded-full font-semibold" style="color:var(--amber-ink);border:1px solid currentColor">กำลังทำงาน</span>';
    if (!j.result) return '—';
    const bad = j.result !== 'ok' && j.result !== 'no_source';
    return `<span class="text-[11px] px-2 py-0.5 rounded-full font-semibold" style="color:var(${bad ? '--bad-ink' : '--good-ink'});border:1px solid currentColor">${esc(CJ_RESULT_TH[j.result] || j.result)}</span>`;
  };
  const th = 'text-left font-medium py-1.5 pr-3';
  const rows = (d.jobs || []).map(j => `<tr class="border-t" style="border-color:var(--border)">
      <td class="py-1.5 pr-3 font-semibold" style="color:var(--text-primary)">${esc(j.label)}</td>
      <td class="py-1.5 pr-3">${j.enabled ? 'เปิดใช้' : 'ปิดอยู่'}</td>
      <td class="py-1.5 pr-3">${esc((j.times || []).join(', '))}</td>
      <td class="py-1.5 pr-3">${chip(j)}</td>
      <td class="py-1.5 pr-3">${esc(_csWhen(j.last_end))}</td>
      <td class="py-1.5 pr-3">${j.enabled ? esc(_csWhen(j.next_run)) : '—'}</td>
      <td class="py-1.5">${j.job === 'feedback' || j.job === 'invisible' || j.job === 'watchdog'
        ? `<button type="button" class="text-xs px-3 py-1 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-medium" ${j.enabled && !j.running ? '' : 'disabled style="opacity:.5"'} onclick="cloudJobRun('${j.job}')">รันตอนนี้</button>`
        : '<span class="text-[11px]" style="color:var(--text-muted)">ดูที่แท็บของงาน</span>'}</td>
    </tr>`).join('');
  el.innerHTML = `<div class="overflow-x-auto"><table class="wt text-sm w-full" style="color:var(--text-secondary)"><thead><tr style="color:var(--text-muted)">
      <th class="${th}">งาน</th><th class="${th}">สถานะ</th><th class="${th}">เวลา</th><th class="${th}">ผลล่าสุด</th><th class="${th}">จบเมื่อ</th><th class="${th}">รอบถัดไป</th><th class="${th}"></th>
    </tr></thead><tbody>${rows}</tbody></table></div>`;
  if ((d.jobs || []).some(j => j.running)) _cjTimer = setTimeout(() => { if (activeSettingsTab === 'cloudjobs') loadCloudJobs(); }, 15000);
}
async function cloudJobRun(job) {
  try {
    const r = await fetch(`${API}/api/admin/cloud-jobs/run`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job }) });
    const j = await r.json().catch(() => ({}));
    if (r.ok) uiToast('เริ่มรันงานแล้ว', 'success');
    else uiToast(j.detail || ('รันงานไม่ได้ (' + r.status + ')'), 'error');
  } catch (e) { uiToast('รันงานไม่ได้: ' + (e.message || e), 'error'); }
  setTimeout(loadCloudJobs, 1500);
}


// ══════════════════════════════════════════════════════════════════════════
// DV7 (owner 2026-10-25, phone table review #10) · ปรับเมนู and โอกาสเมนู around the buffet cost per head (backend/menu_board.py).
// Both read the top bar (gPeriodQuery: kind + pick / date_from + date_to, branch, group) and reload when it changes (the key is the
// query). ปรับเมนู: three lists of buffet dishes by their baht on the cost per head (ราคาต่อจานขึ้น · กินต่อหัวมากขึ้น · ลดลง), each with
// the branches and price tiers that pushed it; à la carte below (GP). โอกาสเมนู: the branches that eat a buffet dish above the others
// (plates; baht for a cost role) and the à-la-carte dishes a branch under-sells (revenue). A list shows its first 5 (10 in a table)
// rows above the floor; "ดูทั้งหมด (N)" opens every row.
// ══════════════════════════════════════════════════════════════════════════
let _mab = { key: '', d: null, seq: 0, open: {} };
let _opp = { key: '', d: null, seq: 0, open: {} };
const _MB_SHOW = 5, _MB_TBL = 10;
// +฿924.0K / −฿1.28M / ฿0 (fmt's short form with the sign and the baht sign)
const _mbB = v => { const n = Number(v); if (v == null || !isFinite(n)) return '—'; const s = n > 0.5 ? '+' : n < -0.5 ? '−' : ''; return s + '฿' + fmt(Math.abs(n)); };
const _mbU = v => { const n = Number(v); return v == null || !isFinite(n) ? '—' : '฿' + fmt(Math.abs(n)); };
const _mbH = v => { const n = Number(v); return v == null || !isFinite(n) ? '—' : '฿' + n.toLocaleString('th-TH', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); };
const _mbN1 = v => { const n = Number(v); return v == null || !isFinite(n) ? '—' : n.toLocaleString('th-TH', { minimumFractionDigits: 1, maximumFractionDigits: 1 }); };
const _mbChg = (v, upGood) => { const n = Number(v); if (v == null || !isFinite(n) || Math.abs(n) < 0.005) return ''; const good = (n > 0) === !!upGood;
  return `<span class="${good ? 'mab-dn' : 'mab-up'}">${n > 0 ? '▲' : '▼'}${_mbH(Math.abs(n))}</span>`; };
const _MAB_BK = {
  price: { cls: 'mab-bk-price', t: 'ราคาต่อจานขึ้น', s: 'ตรวจราคาซื้อหรือสูตร' },
  eat: { cls: 'mab-bk-eat', t: 'ลูกค้ากินต่อหัวมากขึ้น', s: 'ปรับขนาดหรือการเสิร์ฟ · ตรวจสาขาที่ดัน' },
  down: { cls: 'mab-bk-down', t: 'ต้นทุนต่อหัวลดลง', s: 'เมนูที่ช่วยลดต้นทุน' },
};
const _MAB_CAUSE = { price: 'ราคาต่อจาน', eat: 'กินต่อหัว', nobase: 'ไม่ได้ขายช่วงก่อน', gone: 'ไม่ได้ขายช่วงนี้' };
function _mbScope(d) { const g = getGroup(); return d && d.branch ? d.branch : g === 'master' ? 'สาขาหลักทั้งหมด' : g === 'franchise' ? 'แฟรนไชส์ทั้งหมด' : 'ทุกสาขา'; }
// the rows a list shows: the first n above the floor (all of them once opened)
function _mbRows(rows, open, n) { return open ? rows : rows.filter(r => r.above_floor).slice(0, n); }
function _mbMore(kind, k, shown, total) {
  if (total <= shown) return '';
  const lbl = `ดูทั้งหมด (${fmtN(total)})`;
  return `<button type="button" class="${kind}-more" data-k="${esc(k)}" onclick="${kind === 'mab' ? 'mabOpen' : 'oppOpen'}('${esc(k)}')">${lbl}</button>`;
}

async function loadMenuActionBoard(force) {
  const q = gPeriodQuery();
  if (!force && _mab.key === q && _mab.d) { renderActionBoard(_mab.d); return; }
  _mab.key = q;
  const seq = ++_mab.seq;
  const st = document.getElementById('mabStatus');
  if (st) st.textContent = 'กำลังโหลด…';
  let d;
  try { d = await api(`${API}/api/menu/action-board?${q}`); }
  catch (e) { if (seq === _mab.seq && st) st.textContent = 'โหลดข้อมูลไม่สำเร็จ'; _mab.key = ''; return; }
  if (seq !== _mab.seq) return;
  _mab.d = d; _mab.open = {};
  periodChip(periodOf(d), true);
  renderActionBoard(d);
}
function mabOpen(k) { _mab.open[k] = true; if (_mab.d) renderActionBoard(_mab.d); }

function _mabDish(r) {
  const brs = (r.branches || []).map(b => `${esc(b.branch)} ${_mbB(b.impact)}`).join(' · ') + (r.branches_more ? ` · อีก ${fmtN(r.branches_more)} สาขา` : '');
  const tiers = (r.tiers || []).map(t => `${esc(t.label)} ${_mbB(t.impact)}`).join(' · ');
  const fig = r.cause === 'price'
    ? `ต้นทุนต่อจาน <b>${_mbH(r.cost_per_plate)}</b> · เดิม ${_mbH(r.prev_cost_per_plate)}`
    : `จานต่อ 100 ลูกค้า <b>${_mbN1(r.plates_per_100)}</b> · เดิม ${_mbN1(r.prev_plates_per_100)}`;
  return `<div class="mab-dish">
    <div class="mab-dish-top"><span class="mab-name">${esc(r.name)}</span><span class="mab-imp">${_mbB(r.impact)}</span></div>
    <div class="mab-line"><span class="mab-cause">${esc(_MAB_CAUSE[r.cause] || '')}</span> ต้นทุนต่อหัว <b>${_mbH(r.cost_per_head)}</b> · เดิม ${_mbH(r.prev_cost_per_head)} · ${fmtP(Math.abs(r.impact_share || 0))} ของต้นทุนอาหาร · สะสม ${fmtP(r.cum_share)}</div>
    <div class="mab-line">${fig}</div>
    ${brs ? `<div class="mab-line">สาขาที่ดัน: ${brs}</div>` : ''}
    ${tiers ? `<div class="mab-line">ระดับราคา: ${tiers}</div>` : ''}
  </div>`;
}

function renderActionBoard(d) {
  const st = document.getElementById('mabStatus'), ans = document.getElementById('mabAnswer'), board = document.getElementById('mabBoard');
  if (!board) return;
  const per = periodOf(d) || {}, h = d.headline, bf = d.buffet || {}, why = d.reasons || {};
  if (st) st.textContent = `${_mbScope(d)} · ${per.label_th || ''}${per.prev_label_th ? ' เทียบ ' + per.prev_label_th : ''}`;
  if (!h) {
    if (ans) ans.innerHTML = `<div class="mab-empty">${esc(why.cur || 'ไม่มีข้อมูลในช่วงนี้')}</div>`;
    board.innerHTML = ''; document.getElementById('mabAlc').style.display = 'none'; document.getElementById('mabNeedsWrap').style.display = 'none';
    return;
  }
  const p = h.prev, top = (bf.buckets && [...(bf.buckets.price || []), ...(bf.buckets.eat || [])].sort((a, b) => b.impact - a.impact)[0]) || null;
  const line = (lbl, v, chg, was, upGood) => `<div class="mab-ans-l"><span>${lbl}</span><span class="mab-ans-v">${_mbH(v)}</span>${_mbChg(chg, upGood)}${was != null ? `<span class="mab-was">เดิม ${_mbH(was)}</span>` : ''}</div>`;
  if (ans) ans.innerHTML = `<div class="mab-ans">
    ${line('ต้นทุนอาหารต่อหัว', h.cost_per_head, h.cost_per_head_change, p && p.cost_per_head, false)}
    ${line('รายได้ต่อหัว', h.rev_per_head, h.rev_per_head_change, p && p.rev_per_head, true)}
    <div class="mab-ans-l">${top ? `<span>ดันต้นทุนต่อหัวขึ้นมากที่สุด</span><span class="mab-ans-v">${esc(top.name)}</span><span class="mab-up">${_mbB(top.impact)}</span><span class="mab-was">${esc(_MAB_CAUSE[top.cause] || '')}</span>`
      : `<span>${esc(p ? 'ไม่มีเมนูบุฟเฟ่ต์ที่ดันต้นทุนต่อหัวขึ้น' : (why.prev || 'ไม่มีข้อมูลช่วงก่อน'))}</span>`}</div>
  </div>`;
  const food = h.food_cost || 0;
  board.innerHTML = p ? ['price', 'eat', 'down'].map(k => {
    const m = _MAB_BK[k], all = (bf.buckets || {})[k] || [], n = (bf.counts || {})[k] || 0, tot = (bf.totals || {})[k] || 0;
    const rows = _mbRows(all, _mab.open[k], _MB_SHOW);
    const body = rows.length ? rows.map(_mabDish).join('') : `<div class="mab-empty">${n ? `ไม่มีเมนูที่ถึงเกณฑ์ ${_mbU((d.floor || {}).baht)}` : 'ไม่มีเมนูในกลุ่มนี้'}</div>`;
    return `<div class="mab-bk ${m.cls}" data-bucket="${k}">
      <div class="mab-bk-h"><div class="mab-bk-t">${m.t} · ${fmtN(n)} เมนู</div><div class="mab-bk-v">${_mbB(tot)}</div>
        <div class="mab-bk-s">${food ? fmtP(Math.abs(tot) / food * 100) + ' ของต้นทุนอาหาร · ' : ''}${m.s}</div></div>
      <div class="mab-list">${body}</div>${_mbMore('mab', k, rows.length, n)}
    </div>`;
  }).join('') : '';
  if (d.tier_note && p) board.insertAdjacentHTML('beforeend', `<div class="mab-status" style="grid-column:1/-1">${esc(d.tier_note)}</div>`);
  // à la carte: revenue and GP
  const a = d.alc || {}, alcEl = document.getElementById('mabAlc');
  if (alcEl) {
    const rows = _mbRows(a.rows || [], _mab.open.alc, _MB_TBL);
    alcEl.style.display = (a.count || a.uncosted_count) ? '' : 'none';
    alcEl.innerHTML = `<div class="mab-sec-h"><span class="mab-sec-t">à la carte · กำไรขั้นต้นรายเมนู</span>
        <span class="mab-sec-s">รายได้ ${_mbU(a.revenue)}${a.revenue_share != null ? ` (${fmtP(a.revenue_share)} ของรายได้)` : ''} · GP ${_mbU(a.gp)}</span>
        <button type="button" class="ui-q" aria-label="คำอธิบาย" aria-haspopup="dialog" data-help="hlp-menuactionboard-alc" title="คำอธิบาย">?</button></div>
      ${rows.length ? `<div class="mab-wrap"><table id="mabAlcTable" class="mab-tbl"><thead><tr><th>เมนู</th><th class="mab-num">GP (฿)</th><th class="mab-num">เทียบช่วงก่อน</th>
        <th class="mab-num">รายได้</th><th class="mab-num">GP %</th><th class="mab-num">สะสม</th></tr></thead><tbody>${rows.map(r => `<tr>
        <td>${esc(r.name)}<div class="mab-sub">${fmtN(r.plates)} จาน</div></td><td class="mab-num mab-v">${_mbU(r.gp)}</td>
        <td class="mab-num">${r.gp_change == null ? '—' : `<span class="${r.gp_change >= 0 ? 'mab-dn' : 'mab-up'}">${_mbB(r.gp_change)}</span>`}</td>
        <td class="mab-num">${_mbU(r.revenue)}</td><td class="mab-num">${fmtP(r.gp_pct)}</td><td class="mab-num">${fmtP(r.cum_share)}</td></tr>`).join('')}</tbody></table></div>`
        : `<div class="mab-empty">${a.count ? `ไม่มีเมนูที่ถึงเกณฑ์ ${_mbU(a.floor_baht)}` : 'ไม่มีเมนู à la carte ที่มีต้นทุนครบ'}</div>`}
      ${_mbMore('mab', 'alc', rows.length, a.count || 0)}`;
  }
  // dishes without a cost: buffet by plates, à la carte by revenue (folded)
  const nb = bf.uncosted || [], na = a.uncosted || [], wrap = document.getElementById('mabNeedsWrap');
  const nN = (bf.uncosted_count || 0) + (a.uncosted_count || 0);
  if (wrap) {
    wrap.style.display = nN ? '' : 'none';
    document.getElementById('mabNeedsCount').textContent = fmtN(nN);
    document.getElementById('mabNeedsBody').innerHTML =
      nb.map(r => `<div class="mab-unc-row"><span>${esc(r.name)}</span><span>บุฟเฟ่ต์ · ${fmtN(r.uncosted_plates)} จาน</span></div>`).join('') +
      na.map(r => `<div class="mab-unc-row"><span>${esc(r.name)}</span><span>à la carte · ${_mbU(r.revenue)}</span></div>`).join('');
  }
}

// ── โอกาสเมนู ──────────────────────────────────────────────────────────────
async function loadMenuOpportunities(force) {
  const q = gPeriodQuery();
  if (!force && _opp.key === q && _opp.d) { renderOpportunities(_opp.d); return; }
  _opp.key = q;
  const seq = ++_opp.seq;
  const st = document.getElementById('oppStatus');
  if (st) st.textContent = 'กำลังโหลด…';
  let d;
  try { d = await api(`${API}/api/menu/opportunities?${q}`); }
  catch (e) { if (seq === _opp.seq && st) st.textContent = 'โหลดข้อมูลไม่สำเร็จ'; _opp.key = ''; return; }
  if (seq !== _opp.seq) return;
  _opp.d = d; _opp.open = {};
  periodChip(periodOf(d), true);
  renderOpportunities(d);
  const ai = document.getElementById('oppAiIdeas');   // the period or the branch changed: the old AI ideas are of another scope
  if (ai) { ai.innerHTML = ''; delete ai.dataset.generated; }
}
function oppOpen(k) { _opp.open[k] = true; if (_opp.d) renderOpportunities(_opp.d); }
const _oppEmpty = msg => `<div class="opp-empty">${esc(msg)}</div>`;

function renderOpportunities(d) {
  const st = document.getElementById('oppStatus');
  if (!document.getElementById('oppOver')) return;
  const per = periodOf(d) || {}, cv = !!d.cost_view, bo = d.buffet_over || {}, g = d.alc_gaps || {}, why = d.reasons || {};
  if (st) st.textContent = `${_mbScope(d)} · ${per.label_th || ''}`;
  const top = (bo.rows || [])[0], gt = (g.rows || [])[0];
  const brOf = r => (r.branches || [])[0];
  document.getElementById('oppAnswer').innerHTML = why.cur ? _oppEmpty(why.cur) : `<div class="opp-ans">
    <div class="opp-ans-l"><span>บุฟเฟ่ต์ ${cv ? 'ลดได้' : 'จานที่เกินสาขาอื่น'}</span><span class="opp-ans-v opp-bad">${cv ? _mbU(bo.total_cost) : fmtN(bo.total_plates) + ' จาน'}</span>${cv ? `<span>${fmtN(bo.total_plates)} จาน</span>` : ''}<span>จาก ${fmtN(bo.count)} เมนู</span></div>
    <div class="opp-ans-l"><span>à la carte รายได้ที่เพิ่มได้</span><span class="opp-ans-v opp-good">${_mbU(g.total_revenue)}</span><span>จาก ${fmtN(g.count)} เมนู</span></div>
    ${top ? `<div class="opp-ans-l"><span>อันดับแรก</span><span class="opp-ans-v">${esc(top.name)}</span>${brOf(top) ? `<span>${esc(brOf(top).branch)} ×${_mbN1(brOf(top).x)}</span>` : top.x ? `<span>×${_mbN1(top.x)}</span>` : ''}<span class="opp-bad">${cv ? _mbU(top.excess_cost) : fmtN(top.excess_plates) + ' จาน'}</span></div>` : ''}
  </div>`;
  // buffet: branches above the others
  const ov = _mbRows(bo.rows || [], _opp.open.over, _MB_TBL);
  document.getElementById('oppOverSum').textContent = (bo.basis === 'tier' ? 'เทียบลูกค้าระดับราคาเดียวกัน' : (bo.basis_note || ''));
  const ovSub = r => r.branches ? `สาขาที่เกิน: ${r.branches.map(b => `${esc(b.branch)} ×${_mbN1(b.x)} ${cv ? _mbU(b.excess_cost) : fmtN(b.excess_plates) + ' จาน'}`).join(' · ')}${r.branches_more ? ` · อีก ${fmtN(r.branches_more)} สาขา` : ''}`
    : `กิน ${_mbN1(r.rate)} ต่อ 100 ลูกค้า · ค่ากลาง ${_mbN1(r.normal_rate)} · ×${_mbN1(r.x)}${r.tier ? ` · ${esc(r.tier.label)} ${fmtN(r.tier.excess_plates)} จาน` : ''}`;
  document.getElementById('oppOver').innerHTML = ov.length ? `<table id="oppOverTable" class="opp-tbl${cv ? ' op-costed' : ''}"><thead><tr><th>เมนู</th>
      ${cv ? '<th class="opp-num">ลดได้ (฿)</th>' : ''}<th class="opp-num">จานที่เกิน</th><th class="opp-num">สาขาที่เกิน</th><th class="opp-num">สะสม</th></tr></thead>
    <tbody>${ov.map(r => `<tr><td><span class="opp-name">${esc(r.name)}</span><div class="opp-sub">${ovSub(r)}</div></td>
      ${cv ? `<td class="opp-num opp-v opp-bad">${_mbU(r.excess_cost)}</td>` : ''}<td class="opp-num${cv ? '' : ' opp-v opp-bad'}">${fmtN(r.excess_plates)}</td>
      <td class="opp-num">${fmtN(r.n_over)} / ${fmtN(r.n_selling)}</td><td class="opp-num">${fmtP(r.cum_share)}</td></tr>`).join('')}</tbody></table>`
    : _oppEmpty(bo.count ? `ไม่มีเมนูที่ถึงเกณฑ์ ${cv ? _mbU(bo.floor_baht) : fmtN(bo.floor_plates) + ' จาน'}` : 'ไม่มีสาขาที่กินเกินสาขาอื่นในช่วงนี้');
  document.getElementById('oppOver').insertAdjacentHTML('beforeend', _mbMore('opp', 'over', ov.length, bo.count || 0));
  // à la carte: under-sold dishes
  const gp = _mbRows(g.rows || [], _opp.open.gaps, _MB_TBL);
  document.getElementById('oppGapSum').textContent = g.revenue != null ? `เกณฑ์ ${_mbU(g.floor_baht)}` : '';
  const gSub = r => r.branches ? `สาขาที่ขายน้อย: ${r.branches.map(b => `${esc(b.branch)}${b.not_sold ? ' (ยังไม่ขาย)' : ''} ${_mbU(b.potential_revenue)}`).join(' · ')}${r.branches_more ? ` · อีก ${fmtN(r.branches_more)} สาขา` : ''}`
    : `ขาย ${_mbN1(r.rate)} ต่อ 100 ลูกค้า · ค่ากลาง ${_mbN1(r.median_rate)}${r.not_sold ? ' · ยังไม่ขาย' : ''}`;
  document.getElementById('oppGaps').innerHTML = gp.length ? `<table id="oppGapsTable" class="opp-tbl"><thead><tr><th>เมนู</th><th class="opp-num">รายได้ที่เพิ่มได้</th>
      <th class="opp-num">ราคาต่อจาน</th><th class="opp-num">สาขาที่ขายน้อย</th><th class="opp-num">สะสม</th></tr></thead>
    <tbody>${gp.map(r => `<tr><td><span class="opp-name">${esc(r.name)}</span><div class="opp-sub">${gSub(r)}</div></td>
      <td class="opp-num opp-v opp-good">${_mbU(r.potential_revenue)}</td><td class="opp-num">${_mbH(r.price)}</td>
      <td class="opp-num">${fmtN(r.n_branches)} / ${fmtN(r.n_selling)}</td><td class="opp-num">${fmtP(r.cum_share)}</td></tr>`).join('')}</tbody></table>`
    : _oppEmpty(g.count ? `ไม่มีเมนูที่ถึงเกณฑ์ ${_mbU(g.floor_baht)}` : 'ไม่มีเมนูที่สาขาขายน้อยกว่าสาขาอื่นในช่วงนี้');
  document.getElementById('oppGaps').insertAdjacentHTML('beforeend', _mbMore('opp', 'gaps', gp.length, g.count || 0));
  // à la carte: stopped dishes (folded) - the whole months up to the period's end
  const rv = d.revive || [], months = d.months || [];
  document.getElementById('oppReviveN').textContent = `(${fmtN(rv.length)}) · ${months.length ? fmtMonthTh(months[0]) + ' – ' + fmtMonthTh(months[months.length - 1]) : ''}`;
  document.getElementById('oppRevive').innerHTML = rv.length ? `<table id="oppReviveTable" class="opp-tbl"><thead><tr><th>เมนู</th><th class="opp-num">รายได้ต่อเดือน</th>
      <th class="opp-num">จานต่อเดือน</th><th class="opp-num">ขายเดือนสุดท้าย</th></tr></thead>
    <tbody>${rv.map(r => `<tr><td>${esc(r.name)}<div class="opp-sub">${esc(r.category)}</div></td><td class="opp-num opp-v">${_mbU(r.revenue_per_month)}</td>
      <td class="opp-num">${fmtN(r.plates_per_month)}</td><td class="opp-num">${esc(fmtMonthTh(r.last_month))}</td></tr>`).join('')}</tbody></table>`
    : _oppEmpty('ไม่มีเมนูที่หยุดขาย');
  // à la carte categories: only with 3 whole months or more and a trend that moved
  const ws = d.category_whitespace || [], wsWrap = document.getElementById('oppWhitespaceWrap');
  const wsOk = months.length >= 3 && ws.some(w => w.slope_pct);
  wsWrap.style.display = wsOk ? '' : 'none';
  const wl = { expand: ['ขยายได้', 'var(--good-ink)'], trim: ['ควรลด', 'var(--bad-ink)'], watch: ['ทรงตัว', 'var(--text-secondary)'] };
  document.getElementById('oppWsN').textContent = `(${fmtN(ws.length)})`;
  document.getElementById('oppWhitespace').innerHTML = wsOk ? `<table id="oppWsTable" class="opp-tbl"><thead><tr><th>หมวด</th><th class="opp-num">รายได้</th>
      <th class="opp-num">รายได้ต่อเมนู</th><th class="opp-num">แนวโน้มต่อเดือน</th><th>คำแนะนำ</th></tr></thead>
    <tbody>${ws.map(w => { const L = wl[w.label] || wl.watch; return `<tr><td>${esc(w.category)}<div class="opp-sub">${fmtN(w.n_skus)} เมนู</div></td>
      <td class="opp-num opp-v">${_mbU(w.revenue)}</td><td class="opp-num">${_mbU(w.revenue_per_sku)}</td><td class="opp-num">${fmtM(w.slope_pct)}</td>
      <td><span class="opp-pill" style="color:${L[1]}">${L[0]}</span></td></tr>`; }).join('')}</tbody></table>` : '';
}

async function genMenuIdeas() {
  const btn = document.getElementById('oppAiBtn'), host = document.getElementById('oppAiIdeas');
  if (!host) return;
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = 'กำลังสร้าง…';
  host.innerHTML = '<div class="text-xs" style="color:var(--text-muted)">กำลังสร้างไอเดีย…</div>';
  const body = Object.fromEntries(new URLSearchParams(gPeriodQuery()));   // the same period and scope as the page
  let d;
  try {
    d = await api(`${API}/api/menu/ai-ideas`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
  } catch (e) {
    host.innerHTML = `<div class="text-xs" style="color:var(--bad-ink)">สร้างไอเดียไม่สำเร็จ</div>`;
    btn.disabled = false; btn.textContent = old; return;
  }
  const ideas = d.ideas || [];
  const src = d.generated_by === 'claude' ? 'AI (Claude)' : 'สร้างจากข้อมูล (ยังไม่ได้ตั้งค่า AI key)';
  host.dataset.generated = '1';
  host.innerHTML = `<div class="text-[12px] mb-1" style="color:var(--text-muted)">ที่มา: ${esc(src)}</div>` + (ideas.length ? ideas.map((it, i) => `
    <div class="rounded p-3" style="background:var(--bg-subtle);border:1px solid #ec4899">
      <div class="text-sm font-semibold" style="color:var(--text-primary)">${i + 1}. ${esc(it.title)}</div>
      <div class="text-xs mt-1" style="color:var(--text-secondary)">${esc(it.rationale || '')}</div>
    </div>`).join('') : `<div class="text-xs" style="color:var(--text-muted)">ไม่มีไอเดีย</div>`);
  btn.disabled = false; btn.textContent = old;
}
