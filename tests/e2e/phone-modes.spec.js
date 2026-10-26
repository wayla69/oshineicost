// @ts-check
// PR-MA (owner 2026-10-24) · the phone shell's two display modes, the row sheet and the role bottom bars, on a 390 x 844 phone.
//   * พอดีจอ (fit): the page's desktop layout in a frame scaled to the screen width - no sideways scroll of the page, the smallest text drawn
//     >= 5.5 px at rest (the lead's rule replacing the brief's 10.5 px), >= 12 px after a double tap shows a table or a chart at 100 %;
//   * a tap on a table row opens the row sheet (the row's columns as label / value); a tap on the dim closes it;
//   * แตะง่าย (touch) is one tap away and comes back with the page's own 12 px floor;
//   * one bottom bar per role: its entries are pages the role may open, the pages with tasks carry their count key, ค้นหา is the header's magnifier.
// Four representative pages: วิเคราะห์เมนู, GP รายสาขา, คิวใบสั่งซื้อ, ต้นทุนต่อจาน. "Drawn" = the computed font size x every zoom / scale above it.
const { test, expect } = require('@playwright/test');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
// DV2: วิเคราะห์เมนู's menu finder lists nothing until a name is typed - the row checks there read the TOP 60 table (always on the page),
// on its menu-name cell (column 4): the table's row-number column is only marked .ph-rn (hidden on a phone) a moment after it draws
const PAGES = [{ tab: 'menu', cell: '#orderedTable tbody tr:visible td:nth-child(4)' }, { tab: 'gpbranch' }, { tab: 'orderhub', pane: 'queue' }, { tab: 'bomcenter', pane: 'dishcost' }];
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];

// the drawn size of every visible text inside `scope` (CSS px of the screen) - the smallest one, and how many were read
const DRAWN = /** @param {string} scope */ (scope) => {
  const zoomOf = e => { let z = 1; for (let p = e; p && p.nodeType === 1; p = p.parentElement) { const c = getComputedStyle(p); const v = parseFloat(c.zoom); if (v && isFinite(v)) z *= v;
    const m = c.transform && c.transform !== 'none' && c.transform.match(/matrix\(([^)]+)\)/); if (m) { const a = m[1].split(',').map(Number); z *= Math.hypot(a[0], a[1]); } } return z; };
  const shown = e => { const r = e.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return false;
    for (let p = e; p && p.nodeType === 1; p = p.parentElement) { const c = getComputedStyle(p); if (c.display === 'none' || c.visibility === 'hidden') return false; } return true; };
  let min = 99, n = 0;
  document.querySelectorAll(scope).forEach(root => root.querySelectorAll('*').forEach(e => {
    if (/^(SCRIPT|STYLE|TEMPLATE|OPTION)$/i.test(e.tagName) || ![...e.childNodes].some(x => x.nodeType === 3 && x.textContent.trim()) || !shown(e)) return;
    const fs = parseFloat(getComputedStyle(e).fontSize) || 0;
    let d = fs * zoomOf(e);
    if (e instanceof SVGElement) { const m = /** @type {SVGGraphicsElement} */ (e).getScreenCTM(); if (m) d = fs * Math.hypot(m.b, m.d); }
    n++; min = Math.min(min, d);
  }));
  return { min: +min.toFixed(2), n };
};

async function login(page) {
  // The CI fixture runs with auth off. A local run with auth on passes a TEST account through the environment.
  if (process.env.E2E_USER && process.env.E2E_PASS) {
    await page.goto('/');
    await page.evaluate(async ([u, p]) => (await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: u, password: p }) })).status,
      [process.env.E2E_USER, process.env.E2E_PASS]);
  }
}

// the page has settled: no request in flight, the พอดีจอ frame has followed the last thing the page drew (data-ph-fit="ok", set by
// js/09c-mobile.js after each fit) and nothing on the page has changed for `quiet` ms. A late redraw (a second load, a re-fit of the frame
// that moves the rows) lands before the next step reads the page, not in the middle of it - no fixed sleep.
const INFLIGHT = new WeakMap();
async function settle(page, quiet = 400) {
  await page.evaluate(() => { const w = /** @type {any} */ (window); if (w.__e2eMut) return; w.__e2eMut = performance.now();
    new MutationObserver(() => { w.__e2eMut = performance.now(); }).observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true }); });
  const inflight = INFLIGHT.get(page) || new Set();
  await expect.poll(async () => inflight.size === 0 && await page.evaluate((q) => { const f = /** @type {HTMLElement|null} */ (document.querySelector('.ph-frame'));
    return (!f || f.dataset.phFit === 'ok') && performance.now() - /** @type {any} */ (window).__e2eMut >= q; }, quiet),
  { message: 'the page settles (no request in flight, the frame fitted, no change for ' + quiet + ' ms)', timeout: 30_000, intervals: [100] }).toBe(true);
}

async function phoneShell(browser) {
  const ctx = await browser.newContext(PHONE);
  const page = await ctx.newPage();
  const inflight = new Set();
  INFLIGHT.set(page, inflight);
  page.on('request', r => inflight.add(r));
  page.on('requestfinished', r => inflight.delete(r));
  page.on('requestfailed', r => inflight.delete(r));
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' && !IGNORE.some(re => re.test(m.text()))) errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  await login(page);
  await page.goto('/', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => document.body.classList.contains('ph-on') && document.querySelectorAll('#phNav .ph-nav-i').length > 0, null, { timeout: 60_000 });
  return { ctx, page, errors };
}

async function openPage(page, p) {
  await page.evaluate(async (x) => { try { localStorage.removeItem('oshinei_phmode'); } catch (e) { /* none */ }
    await phOpenPage(() => x.pane ? supplyGo({ tab: x.tab, pane: x.pane }) : switchTab(x.tab)); }, p);
  await page.waitForFunction(() => document.body.classList.contains('ph-fit') && !!document.querySelector('.ph-frame'), null, { timeout: 60_000 });
  // the page's rows (or its empty state) have drawn and the frame has followed them
  await page.waitForFunction(() => { const f = document.querySelector('.ph-frame'); return f && !/กำลังโหลด/.test(f.innerText) && f.innerText.replace(/\s/g, '').length > 30; }, null, { timeout: 60_000 });
  await settle(page);
}

for (const p of PAGES) {
  const name = p.tab + (p.pane ? '|' + p.pane : '');
  test(`พอดีจอ on ${name}: the frame fits the screen, text >= 5.5 px at rest and >= 12 px at 100 %, a row opens its sheet`, async ({ browser }) => {
    test.setTimeout(150_000);
    const { ctx, page, errors } = await phoneShell(browser);
    await openPage(page, p);
    const fit = await page.evaluate(() => { const f = /** @type {HTMLElement} */ (document.querySelector('.ph-frame')), ca = /** @type {HTMLElement} */ (document.querySelector('.content-area'));
      return { vw: innerWidth, doc: document.documentElement.scrollWidth, caS: ca.scrollWidth, caC: ca.clientWidth, w: parseFloat(f.style.width), drawnW: f.getBoundingClientRect().width,
        right: f.getBoundingClientRect().right, label: (document.getElementById('phModeL') || {}).textContent, pressed: document.getElementById('phModeBtn').getAttribute('aria-pressed') }; });
    expect(fit.label).toBe('พอดีจอ');
    expect(fit.pressed).toBe('true');
    expect(fit.w).toBeGreaterThanOrEqual(700);                       // the desktop layout: 760-900 px, never wider than 12 px -> 5.5 px allows
    expect(fit.w).toBeLessThanOrEqual(900);
    expect(fit.right).toBeLessThanOrEqual(fit.vw + 0.5);              // scaled to the screen width
    expect(fit.doc).toBeLessThanOrEqual(fit.vw);                      // no sideways scroll of the page
    expect(fit.caS).toBeLessThanOrEqual(fit.caC);
    const rest = await page.evaluate(DRAWN, '.ph-frame');
    expect(rest.n).toBeGreaterThan(10);
    expect(rest.min, `smallest text of ${name} at rest`).toBeGreaterThanOrEqual(5.5);
    const outside = await page.evaluate(DRAWN, '.top-bar, #clusterBar, #phNav');
    expect(outside.min, 'the top bar, the chips and the bottom bar stay at touch size').toBeGreaterThanOrEqual(12);

    // a row: its sheet (header text -> value), closed by a tap on the dim
    const cell = page.locator('.ph-frame ' + (p.cell || 'tbody tr:visible td:not(.ph-rn):not(:has(button, a, input, select, textarea))')).filter({ hasText: /\S/ }).first();
    await expect(cell, `${name} shows a table row`).toBeVisible({ timeout: 30_000 });
    await cell.scrollIntoViewIfNeeded();
    await cell.tap();
    await expect(page.locator('#msSheet.open.ph-rowsheet')).toBeVisible();
    const rows = await page.locator('#msSheetBody .ph-rs-row').count();
    expect(rows).toBeGreaterThanOrEqual(2);
    const sheetText = await page.evaluate(DRAWN, '#msSheet');
    expect(sheetText.min).toBeGreaterThanOrEqual(12);
    await page.mouse.click(195, 60);
    await expect(page.locator('#msSheet')).not.toHaveClass(/open/);

    // a double tap: that table at 100 % (text >= 12 px drawn, as wide as the screen), no sheet; again: back
    await settle(page);                                                // a page that redraws its rows (a second load) has settled again
    await expect(cell).toBeVisible();
    await cell.scrollIntoViewIfNeeded();
    const box = await cell.boundingBox();
    await page.mouse.dblclick(box.x + box.width / 2, box.y + box.height / 2);
    await expect(page.locator('.ph-z100')).toHaveCount(1);
    await settle(page);
    const z = await page.evaluate(DRAWN, '.ph-z100');
    expect(z.min, `smallest text of ${name} at 100 %`).toBeGreaterThanOrEqual(12);
    const zr = await page.evaluate(() => { const r = document.querySelector('.ph-z100').getBoundingClientRect(); return { left: r.left, right: r.right, vw: innerWidth, doc: document.documentElement.scrollWidth }; });
    expect(zr.left).toBeGreaterThanOrEqual(0);
    expect(zr.right).toBeLessThanOrEqual(zr.vw + 0.5);
    expect(zr.doc).toBeLessThanOrEqual(zr.vw);
    await expect(page.locator('#msSheet')).not.toHaveClass(/open/);
    const zc = page.locator('.ph-z100 tbody td:not(.ph-rn):not(:has(button, a, input, select, textarea))').filter({ hasText: /\S/ }).first();
    const zb = await zc.boundingBox();
    await page.mouse.dblclick(zb.x + Math.min(20, zb.width / 2), zb.y + zb.height / 2);
    await expect(page.locator('.ph-z100')).toHaveCount(0);

    // แตะง่าย is one tap away, remembered for this page; the touch layout keeps its 12 px floor
    await page.click('#phModeBtn');
    await page.waitForFunction(() => !document.body.classList.contains('ph-fit'));
    await settle(page);
    expect(await page.textContent('#phModeL')).toBe('แตะง่าย');
    const touch = await page.evaluate(DRAWN, '#tab-' + p.tab);
    expect(touch.min).toBeGreaterThanOrEqual(12);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    expect(JSON.parse(await page.evaluate(() => localStorage.getItem('oshinei_phmode') || '{}'))).toEqual(expect.objectContaining({}));
    expect(Object.values(JSON.parse(await page.evaluate(() => localStorage.getItem('oshinei_phmode') || '{}')))).toContain('touch');

    expect(errors, `console errors:\n${errors.join('\n')}`).toHaveLength(0);
    await ctx.close();
  });
}

test('a touch page stays in แตะง่าย by default (หน้างาน, the launcher)', async ({ browser }) => {
  test.setTimeout(90_000);
  const { ctx, page, errors } = await phoneShell(browser);
  expect(await page.evaluate(() => document.body.classList.contains('ph-fit'))).toBeFalsy();
  await page.evaluate(() => phOpenPage(() => supplyGo({ tab: 'supplyhome' })));
  await page.waitForFunction(() => typeof activeTab === 'string' && activeTab === 'supplyhome', null, { timeout: 60_000 });
  await page.waitForTimeout(800);
  expect(await page.evaluate(() => [document.body.classList.contains('ph-fit'), document.getElementById('phModeL').textContent])).toEqual([false, 'แตะง่าย']);
  expect(errors, errors.join('\n')).toHaveLength(0);
  await ctx.close();
});

// the bar of each role, drawn by the live code for that role (its modules from the app's own role table): the entries, their targets allowed, their counts
const BARS = {
  management: ['หน้าแรก', 'ยอดขาย', 'กำไร', 'สาขา', 'ฉัน'],
  salessupport: ['หน้าแรก', 'คิวใบสั่งซื้อ', 'ใบตัดเบิกสาขา', 'เคลม', 'ฉัน'],
  warehouse: ['หน้าแรก', 'คิวใบสั่งซื้อ', 'ฉัน'],
  analyst: ['หน้าแรก', 'ต้นทุนต่อจาน', 'ราคารายเดือน', 'ไฟล์ราคา', 'ฉัน'],
  accounting: ['หน้าแรก', 'ใบสรุปตัดเบิก', 'P&L รายสาขา', 'GP รายสาขา', 'ฉัน'],
  operation: ['หน้าแรก', 'ใบตัดเบิกสาขา', 'ผลต่างตัดเบิกรายวัน', 'ส่วนลด', 'ฉัน'],
  marketing: ['หน้าแรก', 'ลูกค้าและบุฟเฟ่ต์', 'วิเคราะห์เมนู', 'เสียงลูกค้า', 'ฉัน'],
  innovation: ['หน้าแรก', 'ทะเบียนวัตถุดิบ', 'ฉัน'],
};
test('one bottom bar per role: allowed targets, wired counts, the search in the header', async ({ browser }) => {
  test.setTimeout(90_000);
  const { ctx, page, errors } = await phoneShell(browser);
  await page.evaluate(() => phShowHome());
  await expect(page.locator('#phHeroSearch')).toBeVisible();
  expect(await page.locator('#phNav .ph-nav-i', { hasText: 'ค้นหา' }).count()).toBe(0);       // the old search slot is gone
  const keep = await page.evaluate(() => JSON.stringify(window.AUTH || null));
  for (const [role, want] of Object.entries(BARS)) {
    const got = await page.evaluate((r) => {
      window.AUTH = { enabled: true, authenticated: true, role: r, modules: ROLE_MODULES_TH[r] || [] };
      phNavRender();
      const items = _phNavItems();
      return { labels: [...document.querySelectorAll('#phNav .ph-nav-i > span:not(.nb)')].map(s => s.textContent.trim()), cols: document.getElementById('phNav').style.gridTemplateColumns,
        denied: items.filter(i => i.tab && !(i.id === 'home' ? tabAllowed(i.tab) : deptPageAllowed({ tab: i.tab, pane: i.pane || undefined }))).map(i => i.label),
        nb: [...document.querySelectorAll('#phNav .ph-nav-i')].map(b => b.getAttribute('data-nb') || '') };
    }, role);
    expect(got.labels, role).toEqual(want);
    expect(got.cols, role).toMatch(new RegExp(`^repeat\\(${want.length}, minmax\\(0(px)?, 1fr\\)\\)$`));   // a role with 3 entries shows 3, evenly
    expect(got.denied, role).toEqual([]);
    expect(got.nb[0], role).toBe('t');                                                  // the old งานของฉัน count is on หน้าแรก
  }
  const nb = await page.evaluate(() => { window.AUTH = { enabled: true, authenticated: true, role: 'salessupport', modules: ROLE_MODULES_TH.salessupport };
    phNavRender(); _NB.pages = { 'orderhub:queue': 3, 'orderhub:claims': 1 }; navBadgesApply();
    return [...document.querySelectorAll('#phNav .ph-nav-i')].map(b => (b.querySelector('.nb') || {}).textContent || ''); });
  expect(nb).toEqual(['4', '3', '', '1', '']);
  await page.evaluate((k) => { window.AUTH = JSON.parse(k); phNavRender(); }, keep);
  expect(errors, errors.join('\n')).toHaveLength(0);
  await ctx.close();
});
