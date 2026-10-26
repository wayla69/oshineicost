// @ts-check
// Owner 2026-10-26 "Hide closed branches": on a 390 x 844 phone, ยอดขายรายวัน has no row of 0 sales for a closed branch (the fixture's
// สาขาปิดทดสอบ: marked closed, its last bills two months before the latest - no sale in the month on screen nor in its bases), the answer strip does not
// name it, and the branch sheet keeps it reachable behind its "สาขาที่ปิดแล้ว" fold (the top bar's select: the same option under an
// optgroup). A pick from the fold reloads the page for it, so its history stays one tap away.
const { test, expect } = require('@playwright/test');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];
const CLOSED = process.env.E2E_CLOSED_BRANCH || 'สาขาปิดทดสอบ';

async function login(page) {
  if (process.env.E2E_USER && process.env.E2E_PASS) {
    await page.goto('/');
    await page.evaluate(async ([u, p]) => (await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: u, password: p }) })).status,
      [process.env.E2E_USER, process.env.E2E_PASS]);
  }
}

test('ยอดขายรายวัน at 390: no 0-sales row for a closed branch; the branch sheet folds it under สาขาที่ปิดแล้ว', async ({ browser }) => {
  test.setTimeout(180_000);
  const ctx = await browser.newContext(PHONE);
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' && !IGNORE.some(re => re.test(m.text()))) errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  await login(page);
  await page.addInitScript(() => { try { localStorage.setItem('oshinei_phmode', JSON.stringify({ dailysales: 'touch' })); localStorage.setItem('oshinei_gperiod', JSON.stringify({ kind: 'month', pick: '', from: '', to: '' })); } catch (e) { /* none */ } });
  await page.goto('/', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => document.body.classList.contains('ph-on') && document.querySelectorAll('#phNav .ph-nav-i').length > 0, null, { timeout: 60_000 });

  const daily = page.waitForResponse(r => r.url().includes('/api/sales/daily') && r.url().includes('gran=month'), { timeout: 60_000 });
  await page.evaluate(() => phOpenPage(() => switchTab('dailysales')));
  const d = await (await daily).json();
  await page.waitForLoadState('networkidle');
  await page.waitForFunction(() => document.querySelectorAll('#dsBody tr').length > 0, null, { timeout: 60_000 });

  // the answer: no row for it, the base total keeps its sales (rows only)
  expect(d.branches.map(b => b.branch)).not.toContain(CLOSED);
  expect(d.branches.every(b => (b.revenue || 0) > 0 || b.mom_rev != null || b.yoy_rev != null)).toBe(true);
  // the page: every row and card, shown or behind ดูทั้งหมด, and the answer strip
  await page.evaluate(() => document.querySelectorAll('button[data-dv-for="dsBody"]').forEach(b => /** @type {HTMLElement} */ (b).click()));
  const txt = await page.evaluate(() => document.getElementById('tab-dailysales').innerText);
  expect(txt).not.toContain(CLOSED);
  const zero = await page.evaluate(() => [...document.querySelectorAll('#dsBody tr')].filter(r => /^\s*0\s*$/.test(r.cells[2] ? r.cells[2].innerText : '')).length);
  expect(zero, 'no 0-sales branch row').toBe(0);

  // the picker: the select holds it under the optgroup; the sheet folds it
  const og = await page.evaluate(() => [...document.querySelectorAll('#selBranch optgroup')].map(g => ({ l: g.label, v: [...g.querySelectorAll('option')].map(o => o.value) })));
  expect(og).toEqual([{ l: 'สาขาที่ปิดแล้ว', v: [CLOSED] }]);
  await page.evaluate(() => phBranchOpen());
  const fold = page.locator('#phbList .phb-closed');
  await expect(fold).toHaveCount(1);
  await expect(fold.locator('summary')).toHaveText(/สาขาที่ปิดแล้ว \(1\)/);
  const open = page.locator('#phbList > .phb-item');
  expect(await open.allInnerTexts()).not.toContain(CLOSED);
  const sumBox = await fold.locator('summary').boundingBox();
  expect(sumBox && sumBox.height).toBeGreaterThanOrEqual(44);
  await fold.locator('summary').click();
  const reload = page.waitForRequest(r => r.url().includes('/api/sales/daily') && decodeURIComponent(r.url()).includes(`branch=${CLOSED}`), { timeout: 60_000 });
  await fold.locator('.phb-item', { hasText: CLOSED }).click();
  await reload;
  expect(await page.evaluate(() => /** @type {HTMLSelectElement} */ (document.getElementById('selBranch')).value)).toBe(CLOSED);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  expect(errors).toEqual([]);
  await ctx.close();
});
