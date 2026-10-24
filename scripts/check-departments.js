#!/usr/bin/env node
// PR-D1 · the page registry check (run from scripts/check-js.sh, part of scripts/gate.sh).
//
// Owner rule: a page is never hidden or deleted on usage grounds, so the department launcher must leave nothing out.
// This evaluates the registry block of js/01-core.js (SUPPLY_CHIPS, TAB_CLUSTERS, DEPARTMENTS, allPagesModel, in a bare
// vm context - no DOM) and fails when:
//   * a tab of TAB_CLUSTERS, or an item of SUPPLY_CHIPS, is on neither a department nor หน้ารายงานทั้งหมด;
//   * a department page names a tab that TABS does not know, a pane that no supply chip lists, or an icon DEPT_ICONS lacks;
//   * a department hue has no --dept-<hue>-ink / -tint / -side token in docs/ui-kit/tokens.css (light), or no ink / tint in the
//     dark block; or the departments are not the ten the owner approved, in that order;
//   * a role named by a department or a page is not a role of the app;
//   * PR-S1 · a `bom: true` entry (a recipe page, hidden in stock mode) is not a real page, or the same page is flagged in one place and
//     not in another, or a role is left with no page at all in stock mode. It also prints how many pages each role sees in each mode.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.join(__dirname, '..');
const read = p => fs.readFileSync(path.join(root, p), 'utf8');
const core = read('js/01-core.js');
const fail = [];
const bad = m => fail.push(m);

// ── evaluate the registry block ──
const a = core.indexOf('const _SUPPLY_CENTRE');
const b = core.indexOf('// ── end page registry');
if (a < 0 || b < 0 || b < a) { console.error('check-departments: the registry block markers are missing from js/01-core.js'); process.exit(1); }
const ctx = vm.createContext({ console });
let R;
try {
  vm.runInContext(core.slice(a, b) + '\n;globalThis.__R = { SUPPLY_CHIPS, TAB_CLUSTERS, DEPARTMENTS, DEPT_ICONS, PAGE_KEYWORDS, PAGE_PHONE, allPagesModel, phonePageDefault };', ctx, { filename: 'js/01-core.js (registry)' });
  R = ctx.__R;
} catch (e) { console.error('check-departments: the registry block does not evaluate: ' + e.message); process.exit(1); }
const { SUPPLY_CHIPS, TAB_CLUSTERS, DEPARTMENTS, DEPT_ICONS, PAGE_KEYWORDS, PAGE_PHONE, allPagesModel, phonePageDefault } = R;

const tabsSrc = (/const TABS = \{([\s\S]*?)\n\};/.exec(core) || [])[1] || '';
const tabKeys = new Set([...tabsSrc.matchAll(/^\s*([A-Za-z0-9_]+)\s*:\s*\{/gm)].map(m => m[1]));
const roles = new Set([...((/const ROLE_LABEL_TH = \{([\s\S]*?)\};/.exec(core) || [])[1] || '').matchAll(/([a-z]+)\s*:/g)].map(m => m[1]));
const tokens = read('docs/ui-kit/tokens.css');
const darkTokens = (/body\.dark\s*\{[\s\S]*?--dept-[\s\S]*?\n\}/.exec(tokens.slice(tokens.indexOf('Department hues'))) || [''])[0];

// ── the ten departments, in the owner's order ──
const WANT = ['mgmt', 'mkt', 'ops', 'ana', 'kit', 'ss', 'wh', 'acc', 'brn', 'own'];
const got = DEPARTMENTS.map(d => d.key);
if (JSON.stringify(got) !== JSON.stringify(WANT)) bad(`DEPARTMENTS must be ${WANT.join(', ')} in that order (found ${got.join(', ')})`);

// ── every department page is real ──
const chipPane = new Set(SUPPLY_CHIPS.flatMap(c => c.items.map(i => i.tab + '|' + (i.pane || ''))));
DEPARTMENTS.forEach(d => {
  for (const f of ['key', 'label', 'duty', 'icon', 'hue']) if (!d[f]) bad(`department ${d.key}: missing ${f}`);
  if (!DEPT_ICONS[d.icon]) bad(`department ${d.key}: icon "${d.icon}" is not in DEPT_ICONS`);
  if (!Array.isArray(d.roles) || !d.roles.length) bad(`department ${d.key}: roles must name its home role(s)`);
  (d.roles || []).concat(...d.pages.map(p => p.roles || [])).forEach(r => { if (!roles.has(r)) bad(`department ${d.key}: "${r}" is not a role of the app`); });
  for (const s of ['ink', 'tint', 'side']) if (!new RegExp(`--dept-${d.hue}-${s}\\s*:`).test(tokens)) bad(`department ${d.key}: token --dept-${d.hue}-${s} is missing from docs/ui-kit/tokens.css`);
  for (const s of ['ink', 'tint']) if (!new RegExp(`--dept-${d.hue}-${s}\\s*:`).test(darkTokens)) bad(`department ${d.key}: no dark value for --dept-${d.hue}-${s}`);
  const seen = new Set();
  d.pages.forEach(p => {
    const at = `department ${d.key}, page "${p.label}"`;
    if (!p.label) bad(`${d.key}: a page has no label`);
    if (!tabKeys.has(p.tab)) bad(`${at}: tab "${p.tab}" is not a key of TABS`);
    if (!fs.readdirSync(path.join(root, 'tabs')).some(f => f.replace(/^\d+-|\.html$/g, '') === p.tab)) bad(`${at}: tabs/NN-${p.tab}.html does not exist`);
    if (!DEPT_ICONS[p.icon]) bad(`${at}: icon "${p.icon}" is not in DEPT_ICONS`);
    if (p.pane && !chipPane.has(p.tab + '|' + p.pane)) bad(`${at}: pane "${p.pane}" of ${p.tab} is listed by no SUPPLY_CHIPS item`);
    const k = p.tab + '|' + (p.pane || '');
    if (seen.has(k)) bad(`${at}: the same page twice in one department`);
    seen.add(k);
  });
});

// ── PR-S1 · the recipe pages (bom: true) ──
const bomFlag = new Map();     // tab|pane -> { flagged, plain } over the DEPARTMENTS pages and the SUPPLY_CHIPS items
const countFlag = e => {
  const k = e.tab + '|' + (e.pane || '');
  const c = bomFlag.get(k) || { flagged: 0, plain: 0 };
  if (e.bom === true) c.flagged++; else c.plain++;
  bomFlag.set(k, c);
};
DEPARTMENTS.forEach(d => d.pages.forEach(countFlag));
SUPPLY_CHIPS.forEach(c => c.items.forEach(countFlag));
const tabFileExists = tab => fs.readdirSync(path.join(root, 'tabs')).some(f => f.replace(/^\d+-|\.html$/g, '') === tab);
bomFlag.forEach((c, k) => {
  if (!c.flagged) return;
  const [tab, pane] = k.split('|');
  if (c.plain) bad(`bom: ${k} is flagged bom: true in ${c.flagged} place(s) and not in ${c.plain} other(s) - flag every occurrence of the page`);
  if (!tabKeys.has(tab) || !tabFileExists(tab)) bad(`bom: ${k} is flagged bom: true but "${tab}" is not a real page (TABS / tabs/NN-${tab}.html)`);
  if (pane && !chipPane.has(k)) bad(`bom: ${k} is flagged bom: true but no SUPPLY_CHIPS item lists pane "${pane}"`);
});
const bomKeys = [...bomFlag.entries()].filter(([, c]) => c.flagged).map(([k]) => k);
if (!bomKeys.length) bad('bom: no entry is flagged bom: true (the recipe pages of stock mode)');

// ── PR-S1 · pages per role and mode: the live visibility rules (tabAllowed, _supplyItemAllowed, deptPageAllowed, deptVisible) run on each role's modules ──
const fnSrc = (src, name) => {
  const m = new RegExp('^(async )?function ' + name + '\\(', 'm').exec(src);
  if (!m) throw new Error('function ' + name + ' not found');
  const open = src.indexOf('{', src.indexOf(')', m.index));
  let depth = 0, j = open;
  for (; j < src.length; j++) { if (src[j] === '{') depth++; else if (src[j] === '}' && --depth === 0) break; }
  return src.slice(m.index, j + 1);
};
const launcher = read('js/18-launcher.js');
const modeRows = {};
try {
  const pre = core.slice(core.indexOf('const TAB_MODULE'), core.indexOf('function firstAllowedTab'));
  const win = { AUTH: { enabled: true, role: 'management', modules: [] } };
  const vctx = vm.createContext({ console, window: win, document: {}, localStorage: {}, navigator: {} });
  vm.runInContext('var BC_MODE = "stock";', vctx);
  vm.runInContext(pre, vctx, { filename: 'js/01-core.js (permissions)' });
  vm.runInContext(core.slice(a, b), vctx, { filename: 'js/01-core.js (registry)' });
  vm.runInContext(['_supplyRoleOk', '_supplyItemAllowed'].map(n => fnSrc(core, n)).join('\n')
    + '\n' + ['_deptRole', 'deptPageAllowed', 'deptVisible'].map(n => fnSrc(launcher, n)).join('\n'), vctx, { filename: 'visibility' });
  const roleKeys = Object.keys(vm.runInContext('ROLE_MODULES_TH', vctx));
  for (const role of roleKeys) {
    modeRows[role] = {};
    for (const mode of ['stock', 'full']) {
      vm.runInContext(`BC_MODE = ${JSON.stringify(mode)}; window.AUTH = { enabled: true, role: ${JSON.stringify(role)}, modules: ROLE_MODULES_TH[${JSON.stringify(role)}] };`, vctx);
      const rows = vm.runInContext('deptVisible().map(r => ({ d: r.dept.key, pages: r.pages.map(x => x.p.tab + "|" + (x.p.pane || "")) }))', vctx);
      const keys = new Set(rows.flatMap(r => r.pages));
      modeRows[role][mode] = { depts: rows.length, pages: keys.size, keys };
    }
    const st = modeRows[role].stock, fu = modeRows[role].full;
    if (fu.pages && !st.pages) bad(`bom: the role ${role} sees ${fu.pages} page(s) in full mode and none in stock mode`);
    bomKeys.forEach(k => { if (st.keys.has(k)) bad(`bom: the role ${role} still sees the recipe page ${k} in stock mode`); });
  }
} catch (e) { bad('bom: the visibility check could not run: ' + e.message); }

// ── nothing in TAB_CLUSTERS or SUPPLY_CHIPS is left out ──
const inDept = new Set(DEPARTMENTS.flatMap(d => d.pages.map(p => p.tab + '|' + (p.pane || ''))));
const inDeptTab = new Set(DEPARTMENTS.flatMap(d => d.pages.map(p => p.tab)));
const inAll = new Set(allPagesModel(() => true).flatMap(g => g.entries.map(e => e.tab + '|' + (e.pane || ''))));
const inAllTab = new Set(allPagesModel(() => true).flatMap(g => g.entries.map(e => e.tab)));
TAB_CLUSTERS.forEach(cl => cl.tabs.forEach(t => {
  if (!inDeptTab.has(t[0]) && !inAllTab.has(t[0])) bad(`cluster "${cl.label}": tab ${t[0]} (${t[1]}) is on no department and not in หน้ารายงานทั้งหมด`);
}));
SUPPLY_CHIPS.forEach(ch => ch.items.forEach(it => {
  const k = it.tab + '|' + (it.pane || '');
  if (!inDept.has(k) && !inAll.has(k)) bad(`supply chip "${ch.label}": ${k} (${it.label}) is on no department and not in หน้ารายงานทั้งหมด`);
}));

// ── PR-D4 · the search words: every key of PAGE_KEYWORDS is a real page ("tab" or "tab|pane"), and every word is text ──
const pageKeys = new Set([...inDept, ...inAll].flatMap(k => [k, k.replace(/\|$/, '')]));
Object.entries(PAGE_KEYWORDS || {}).forEach(([k, words]) => {
  if (!pageKeys.has(k)) bad(`PAGE_KEYWORDS: "${k}" is not a page of a department or of หน้ารายงานทั้งหมด`);
  if (typeof words !== 'string' || !words.trim()) bad(`PAGE_KEYWORDS: "${k}" has no words`);
});
if (!PAGE_KEYWORDS || Object.keys(PAGE_KEYWORDS).length < 40) bad('PAGE_KEYWORDS is missing or too short (js/01-core.js, after DEPARTMENTS)');

// ── PR-MA · the phone display mode: every page of a department or of หน้ารายงานทั้งหมด names its default ('fit' | 'touch', or one per view),
//    and every key of PAGE_PHONE is a real page ("tab|pane" of the registry, or a key of TABS) ──
const MODES = new Set(['fit', 'touch']);
const phoneCount = { fit: 0, touch: 0 };
if (!PAGE_PHONE || typeof PAGE_PHONE !== 'object') bad('PAGE_PHONE is missing from js/01-core.js (the page registry block)');
else {
  [...pageKeys].filter(k => !k.endsWith('|')).forEach(k => {
    const e = PAGE_PHONE[k];
    if (e === undefined) { bad(`PAGE_PHONE: the page "${k}" has no phone default ('fit' or 'touch')`); return; }
    const vals = typeof e === 'object' ? Object.values(e) : [e];
    if (!vals.length || vals.some(v => !MODES.has(v))) bad(`PAGE_PHONE: "${k}" must be 'fit', 'touch' or { view: 'fit' | 'touch' } (found ${JSON.stringify(e)})`);
    phoneCount[phonePageDefault(...k.split('|'))]++;
  });
  Object.keys(PAGE_PHONE).forEach(k => { if (!pageKeys.has(k) && !tabKeys.has(k)) bad(`PAGE_PHONE: "${k}" is not a page of the registry nor a key of TABS`); });
}

if (fail.length) {
  fail.forEach(m => console.error('  ✗ ' + m));
  console.error(`check-departments: ${fail.length} problem(s)`);
  process.exit(1);
}
const pages = DEPARTMENTS.reduce((n, d) => n + d.pages.length, 0);
console.log(`check-departments: clean — ${DEPARTMENTS.length} departments, ${pages} tiles, ${[...inAll].length} pages in หน้ารายงานทั้งหมด`);
console.log(`check-departments: phone defaults - ${phoneCount.fit} pages open in พอดีจอ (fit), ${phoneCount.touch} in แตะง่าย (touch)`);
console.log(`check-departments: ${bomKeys.length} recipe pages flagged bom: true (${bomKeys.join(', ')})`);
console.log('check-departments: pages (departments) each role sees in stock mode / full mode:');
Object.entries(modeRows).forEach(([role, m]) => console.log(`  ${role.padEnd(13)} stock ${String(m.stock.pages).padStart(3)} (${m.stock.depts})   full ${String(m.full.pages).padStart(3)} (${m.full.depts})`));
