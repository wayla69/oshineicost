// @ts-check
// fix/app-update (owner 2026-10-24) · a page opened before a deploy loads the new version.
// GET /api/meta/build is answered with another build (a deploy happened); the page returns to the foreground:
//   * a clean page reloads at once, back on the page that was open, and does not reload a second time for the same build
//     (the loop guard: the toast instead);
//   * a page with unsaved input (a typed field, a branch form's own flag) shows the toast "มีเวอร์ชันใหม่ · แตะเพื่อโหลด" and stays;
//     a tap asks before the unsaved input is lost, then reloads.
const { test, expect } = require('@playwright/test');

const DESKTOP = { viewport: { width: 1280, height: 860 } };
const NEW_BUILD = 'e2e0new000';

async function login(page) {
  // The CI fixture runs with auth off. A local run with auth on passes a TEST account through the environment.
  if (process.env.E2E_USER && process.env.E2E_PASS) {
    await page.goto('/');
    await page.evaluate(async ([u, p]) => (await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: u, password: p }) })).status,
      [process.env.E2E_USER, process.env.E2E_PASS]);
  }
}

async function booted(page) {
  await page.waitForFunction(() => typeof _desktopBooted !== 'undefined' && _desktopBooted && typeof appUpdateCheck === 'function', null, { timeout: 60_000 });
}

async function openApp(browser) {
  const ctx = await browser.newContext(DESKTOP);
  const page = await ctx.newPage();
  await login(page);
  await page.goto('/', { waitUntil: 'networkidle' });
  await booted(page);
  return { ctx, page };
}

// a deploy happened: the server now answers another build
const deploy = (page) => page.route('**/api/meta/build', r => r.fulfill({ status: 200, contentType: 'application/json', headers: { 'Cache-Control': 'no-store' }, body: JSON.stringify({ build: NEW_BUILD }) }));
const foreground = (page) => page.evaluate(() => { /** @type {any} */ (window).__mark = 1; document.dispatchEvent(new Event('visibilitychange')); });
const marked = (page) => page.evaluate(() => /** @type {any} */ (window).__mark === 1).catch(() => false);
const toast = (page) => page.locator('.ui-toast-update');

test('the shell carries the build the server answers', async ({ browser }) => {
  const { ctx, page } = await openApp(browser);
  const meta = await page.evaluate(() => document.querySelector('meta[name="app-build"]')?.getAttribute('content'));
  const live = await page.evaluate(async () => (await (await fetch('/api/meta/build', { cache: 'no-store' })).json()).build);
  expect(meta).toBeTruthy();
  expect(meta).toBe(live);
  await ctx.close();
});

test('a clean page reloads to the same page, once', async ({ browser }) => {
  const { ctx, page } = await openApp(browser);
  await page.evaluate(() => switchTab('menu'));
  await page.waitForTimeout(1500);
  await page.evaluate(() => { try { document.activeElement && /** @type {any} */ (document.activeElement).blur(); } catch (e) { /* none */ } });
  await deploy(page);
  await foreground(page);
  await page.waitForFunction(() => /** @type {any} */ (window).__mark !== 1, null, { timeout: 30_000 });     // a new document
  await booted(page);
  expect(await page.evaluate(() => activeTab)).toBe('menu');
  expect(await page.evaluate(() => sessionStorage.getItem('oshinei_upd_build'))).toBe(NEW_BUILD);
  // the reload did not bring that build (this test's server still serves the old one): the toast, no second reload
  await page.waitForTimeout(500);
  await foreground(page);
  await expect(toast(page)).toBeVisible({ timeout: 15_000 });
  await expect(toast(page)).toContainText('มีเวอร์ชันใหม่ · แตะเพื่อโหลด');
  await page.waitForTimeout(3000);
  expect(await marked(page)).toBe(true);
  await ctx.close();
});

test('a phone on a supply page reloads back onto that page', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  await login(page);
  await page.goto('/', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => document.body.classList.contains('ph-on') && typeof appUpdateCheck === 'function', null, { timeout: 60_000 });
  await page.evaluate(async () => { await phOpenPage(() => supplyGo({ tab: 'orderhub', pane: 'queue' })); });
  await page.waitForFunction(() => activeTab === 'orderhub', null, { timeout: 60_000 });
  await page.waitForTimeout(1500);
  await page.evaluate(() => { try { /** @type {any} */ (document.activeElement).blur(); } catch (e) { /* none */ } });
  await deploy(page);
  await foreground(page);
  await page.waitForFunction(() => /** @type {any} */ (window).__mark !== 1, null, { timeout: 30_000 });
  await page.waitForFunction(() => typeof activeTab !== 'undefined' && activeTab === 'orderhub' && document.body.classList.contains('ph-on'), null, { timeout: 60_000 });
  await ctx.close();
});

test('typed input that is not saved: the toast, a tap asks, then reloads', async ({ browser }) => {
  const { ctx, page } = await openApp(browser);
  await page.evaluate(() => {
    const i = document.createElement('input'); i.type = 'number'; i.id = 'e2eUpdQty';
    (document.getElementById('tab-' + activeTab) || document.body).prepend(i);
  });
  await page.fill('#e2eUpdQty', '12');
  await page.evaluate(() => /** @type {any} */ (document.activeElement).blur());
  await deploy(page);
  await foreground(page);
  await expect(toast(page)).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(3000);
  expect(await marked(page)).toBe(true);                       // no reload over unsaved input
  await toast(page).locator('.ui-toast-text').click();
  const dlg = page.locator('[data-ui-dialog]');
  await expect(dlg).toContainText('ยังไม่บันทึก');
  await dlg.locator('[type="submit"]').click();
  await page.waitForFunction(() => /** @type {any} */ (window).__mark !== 1, null, { timeout: 30_000 });
  await booted(page);
  await ctx.close();
});

test('a branch form with unsaved lines: the toast, no reload', async ({ browser }) => {
  const { ctx, page } = await openApp(browser);
  await page.evaluate(() => { /** @type {any} */ (window).eval('_brnOrdDirty = true'); });
  await deploy(page);
  await foreground(page);
  await expect(toast(page)).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(3000);
  expect(await marked(page)).toBe(true);
  // saved: the next check reloads
  await page.evaluate(() => { /** @type {any} */ (window).eval('_brnOrdDirty = false'); });
  await page.evaluate(() => { _APP_UPD.at = 0; });
  await foreground(page);
  await page.waitForFunction(() => /** @type {any} */ (window).__mark !== 1, null, { timeout: 30_000 });
  await ctx.close();
});
