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
//   * a role named by a department or a page is not a role of the app.
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
  vm.runInContext(core.slice(a, b) + '\n;globalThis.__R = { SUPPLY_CHIPS, TAB_CLUSTERS, DEPARTMENTS, DEPT_ICONS, allPagesModel };', ctx, { filename: 'js/01-core.js (registry)' });
  R = ctx.__R;
} catch (e) { console.error('check-departments: the registry block does not evaluate: ' + e.message); process.exit(1); }
const { SUPPLY_CHIPS, TAB_CLUSTERS, DEPARTMENTS, DEPT_ICONS, allPagesModel } = R;

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

if (fail.length) {
  fail.forEach(m => console.error('  ✗ ' + m));
  console.error(`check-departments: ${fail.length} problem(s)`);
  process.exit(1);
}
const pages = DEPARTMENTS.reduce((n, d) => n + d.pages.length, 0);
console.log(`check-departments: clean — ${DEPARTMENTS.length} departments, ${pages} tiles, ${[...inAll].length} pages in หน้ารายงานทั้งหมด`);
