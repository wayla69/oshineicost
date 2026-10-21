// @ts-check
// End-to-end smoke: load the dashboard, walk EVERY sidebar tab, and assert each
// renders content with ZERO console errors. Codifies the manual preview_eval
// smoke run before each deploy. Targets BASE_URL (default the live Fly app).
const { test, expect } = require('@playwright/test');

const TABS = [
  'launcher', 'allpages', 'home', 'overall', 'dailysales', 'trend', 'compare', 'more',
  'buffetperf', 'childbuffet', 'guest', 'timeflow',
  'menu', 'engineering', 'cost', 'bomcost', 'costhead',
  'gpbranch', 'pnlbranch', 'pnlbill', 'benchmark',
  'opsreport', 'staff', 'discounts', 'ops',
  'cashctl', 'custtype', 'modifiers', 'pricezone', 'cancelbill',
  'manual', 'report', 'settings',
  // วัตถุดิบและใบสั่งซื้อ — one sidebar entry, 7 chips (2026-10-07): every tab the chips open
  'supplyhome', 'bomcenter', 'orderhub', 'branchstock', 'ordersetup', 'ordering', 'ingpurchase',
];

// Benign console noise to ignore (favicon, third-party CDN, aborted fetches on tab-switch).
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];

test('every tab renders with no console errors', async ({ page }) => {
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' && !IGNORE.some(re => re.test(m.text()))) errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));

  await page.goto('/', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => typeof window.switchTab === 'function', null, { timeout: 30_000 });

  const results = {};
  for (const t of TABS) {
    await page.evaluate(name => window.switchTab(name), t);
    await page.waitForTimeout(900);
    const info = await page.evaluate(name => {
      // an alias tab (the 2026-10-01 nav regroup) opens its host page with a sub-view:
      // judge the page switchTab actually landed on, not the alias id
      const shown = (typeof activeTab !== 'undefined' && activeTab) || name;
      const el = document.getElementById('tab-' + shown);
      const visible = !!(el && el.style.display !== 'none' && !el.classList.contains('hidden'));
      const len = el ? el.innerText.replace(/\s/g, '').length : 0;
      return { shown, visible, len };
    }, t);
    results[t] = info;
    expect(info.visible, `tab '${t}' (shown as '${info.shown}') should be visible`).toBeTruthy();
    expect(info.len, `tab '${t}' (shown as '${info.shown}') should render content`).toBeGreaterThan(30);
  }

  if (errors.length) console.log('Console errors:\n' + errors.join('\n'));
  expect(errors, `console errors:\n${errors.join('\n')}`).toHaveLength(0);
});

// Beyond "no console errors": the API must serve real data and the dashboard
// must render real (non-zero) figures from it — a blank-but-error-free page
// used to pass the smoke.
test('API serves data and headline figures actually render', async ({ page, request }) => {
  const r = await request.get('/api/summary');
  expect(r.ok(), '/api/summary should respond 200').toBeTruthy();
  const summary = await r.json();
  expect(summary.revenue, 'served revenue must be > 0').toBeGreaterThan(0);

  const months = await (await request.get('/api/months')).json();
  expect(Array.isArray(months) ? months.length : Object.keys(months).length,
         'at least one data month').toBeGreaterThan(0);

  await page.goto('/', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => typeof window.switchTab === 'function', null, { timeout: 30_000 });
  await page.evaluate(() => window.switchTab('overall'));
  await page.waitForTimeout(1200);
  const text = await page.evaluate(() => {
    const el = document.getElementById('tab-overall');
    return el ? el.innerText : '';
  });
  // a real figure (2+ digit number, optionally comma-grouped) — not just labels
  expect(text, 'overall tab should show non-zero figures').toMatch(/[1-9][\d,]{1,}/);
});
