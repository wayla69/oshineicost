// @ts-check
// DV6 (phone table review 2026-10-25, top-10 #9 + "อะไรที่มีประโยชน์ขึ้นหน้าจอของเจ้าของสาขาด้วย"):
//   1. the executive phone summary: the to-do lines carry their baht, the สาขา tab opens on the baht against last month, the เมนู tab is
//      "เมนูที่ดันต้นทุนต่อหัวขึ้น (บาท)"; the summary takes the app's period and writes a month picked on it back to the top bar.
//   2. ร้านของฉัน on a phone (a franchise login, mocked on the auth-off fixture: the server's own answers for branch_id=1): the
//      เรื่องที่ควรดู cards draw, the period chip in the page head opens the period sheet, and สัปดาห์ reloads them with kind=week.
const { test, expect } = require('@playwright/test');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];

async function open(browser, { franchise = false } = {}) {
  const ctx = await browser.newContext({ ...PHONE });
  await ctx.addInitScript(() => {
    try { localStorage.setItem('oshinei_gperiod', JSON.stringify({ kind: 'month', pick: '', from: '', to: '' })); } catch (e) { /* none */ }
  });
  if (franchise) {
    await ctx.route('**/api/auth/me', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ enabled: true, authenticated: true,
      username: 'own-e2e', role: 'franchise', branch: 'สาขาทดสอบ', display_name: 'เจ้าของสาขา', names: {}, write_gated: false, modules: ['franchise.view'] }) }));
    // auth is off on the fixture: the centre's rule (name the branch) stands in for the signed session's pin
    await ctx.route(/\/api\/franchise\/(summary|decide|insight|supply|daily)(\?|$)/, r => {
      const u = new URL(r.request().url()); u.searchParams.set('branch_id', '1');
      return r.continue({ url: u.toString() });
    });
    await ctx.route(/\/api\/franchise\/messages/, r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [], unread: 0 }) }));
  }
  const page = await ctx.newPage();
  const urls = [];
  page.on('request', r => { if (/\/api\//.test(r.url())) urls.push(decodeURIComponent(r.url())); });
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error' && !IGNORE.some(re => re.test(m.text()))) errors.push(m.text()); });
  await page.goto('/', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => document.body.classList.contains('ph-on') && typeof phOpenPage === 'function', null, { timeout: 60_000 });
  return { ctx, page, urls, errors };
}

test('executive phone summary · baht on the to-do lines, baht-first branch and menu tabs, the period written back', async ({ browser }) => {
  test.setTimeout(150_000);
  const { ctx, page, urls, errors } = await open(browser);
  await page.evaluate(() => phShowSummary());
  await page.waitForFunction(() => (document.getElementById('msActions')?.children.length || 0) > 0, null, { timeout: 90_000 });
  const acts = await page.evaluate(() => [...document.querySelectorAll('#msActions .ms-act')].map(r => r.innerText));
  expect(acts.length).toBeGreaterThan(0);
  expect(acts.some(t => /[+−]฿/.test(t)), acts.join(' | ')).toBe(true);
  await page.evaluate(() => msSwitchView('branches'));
  await expect(page.locator('#msSortChg')).toHaveClass(/ms-seg-on/);
  await page.evaluate(() => msSwitchView('menu'));
  await expect(page.locator('#msMenuTitle')).toHaveText(/เมนูที่ดันต้นทุนต่อหัวขึ้น \(บาท\)|เมนูขายดี/);
  // a month picked on the summary becomes the top bar's period and the next request carries it
  const opt = await page.evaluate(() => { const s = document.getElementById('msMonthSel'); return [...s.options].map(o => o.value).filter(Boolean).slice(-1)[0] || ''; });
  if (opt) {
    const n0 = urls.length;
    await page.evaluate(v => { const s = document.getElementById('msMonthSel'); s.value = v; s.dispatchEvent(new Event('change')); }, opt);
    await page.waitForTimeout(2500);
    expect(urls.slice(n0).some(u => u.includes('/api/reports/management?') && u.includes('month=' + opt))).toBe(true);
    const gp = await page.evaluate(() => gPeriodGet());
    expect(gp.kind).toBe('month');
  }
  expect(errors).toEqual([]);
  await ctx.close();
});

test('ร้านของฉัน on a phone · เรื่องที่ควรดู, and the period chip switches it to the week', async ({ browser }) => {
  test.setTimeout(150_000);
  const { ctx, page, urls, errors } = await open(browser, { franchise: true });
  await page.evaluate(async () => { await phOpenPage(() => switchTab('franchise')); });
  await page.waitForFunction(() => { const b = document.getElementById('frcDecide'); return b && !/กำลังโหลด/.test(b.innerText); }, null, { timeout: 90_000 });
  await expect(page.locator('#frcDecide')).toContainText('เรื่องที่ควรดู');
  await expect(page.locator('#frcDecide')).toContainText('ต้นทุนอาหาร');
  expect(urls.some(u => u.includes('/api/franchise/decide?kind=month'))).toBe(true);
  const chip = page.locator('#frcPeriodChip');
  await expect(chip).toBeVisible();
  await expect(chip).toContainText('เดือน');
  await chip.click();
  await expect(page.locator('#phPeriodSheet')).toBeVisible();
  const weekReq = page.waitForRequest(r => r.url().includes('/api/franchise/decide?kind=week'), { timeout: 60_000 });
  await page.locator('#phPeriodSheet [data-k="week"]').click();
  await page.locator('#phPeriodSheet .php-done').click();
  await weekReq;                                    // the week reloads เรื่องที่ควรดู with kind=week
  await page.waitForFunction(() => /สัปดาห์/.test(document.getElementById('frcPeriodChip')?.innerText || ''), null, { timeout: 60_000 });
  await page.waitForFunction(() => { const b = document.getElementById('frcDecide'); return b && !/กำลังโหลด/.test(b.innerText); }, null, { timeout: 90_000 });
  // the page's text floor: nothing under 12 px in the decide cards
  const small = await page.evaluate(() => [...document.querySelectorAll('#frcDecide *')].filter(e => e.childNodes.length && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())
    && parseFloat(getComputedStyle(e).fontSize) < 12).map(e => e.textContent.trim().slice(0, 30)));
  expect(small).toEqual([]);
  expect(errors).toEqual([]);
  await ctx.close();
});
