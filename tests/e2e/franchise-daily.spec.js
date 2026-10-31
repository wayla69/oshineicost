// @ts-check
// สรุปประจำวัน (owner 2026-10-27, mockup B) on ร้านของฉัน, a 390 px phone (a franchise login, mocked on the auth-off fixture: the server's
// own answers for branch_id=1). Phone B (owner 2026-10-31): the page opens on overview cards; เมื่อวาน is the first card (full width: the
// last finished day's net sales with the เทียบปกติ pill only - no last-year chip) and opens its detail: the สรุปประจำวัน card (four tiles,
// the month so far with the target bar) and the month's daily table; the back arrow returns to the cards. The day does not follow the
// period chip; nothing scrolls sideways and no text is under 12 px.
const { test, expect } = require('@playwright/test');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];

async function openFranchise(browser) {
  const ctx = await browser.newContext({ ...PHONE });
  await ctx.addInitScript(() => {
    try { localStorage.setItem('oshinei_gperiod', JSON.stringify({ kind: 'month', pick: '', from: '', to: '' })); } catch (e) { /* none */ }
  });
  await ctx.route('**/api/auth/me', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ enabled: true, authenticated: true,
    username: 'own-e2e', role: 'franchise', branch: 'สาขาทดสอบ', display_name: 'เจ้าของสาขา', names: {}, write_gated: false, modules: ['franchise.view'] }) }));
  // auth is off on the fixture: the centre's rule (name the branch) stands in for the signed session's pin
  await ctx.route(/\/api\/franchise\/(overview|summary|decide|insight|supply|daily)(\?|$)/, r => {
    const u = new URL(r.request().url()); u.searchParams.set('branch_id', '1');
    return r.continue({ url: u.toString() });
  });
  await ctx.route(/\/api\/franchise\/messages/, r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [], unread: 0 }) }));
  const page = await ctx.newPage();
  const urls = [];
  page.on('request', r => { if (/\/api\//.test(r.url())) urls.push(decodeURIComponent(r.url())); });
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error' && !IGNORE.some(re => re.test(m.text()))) errors.push(m.text()); });
  await page.goto('/', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => document.body.classList.contains('ph-on') && typeof phOpenPage === 'function', null, { timeout: 60_000 });
  await page.evaluate(async () => { await phOpenPage(() => switchTab('franchise')); });
  await page.waitForFunction(() => document.getElementById('frcCard-yesterday'), null, { timeout: 90_000 });   // phone B: the เมื่อวาน card
  return { ctx, page, urls, errors };
}


test('ร้านของฉัน on a phone · เมื่อวาน is the first card and opens สรุปประจำวัน, no last-year chip, fits 390 px', async ({ browser }) => {
  test.setTimeout(150_000);
  const { ctx, page, urls, errors } = await openFranchise(browser);
  const api = await page.evaluate(async () => (await fetch(`${API}/api/franchise/daily?branch_id=1`)).json());
  expect(api.available).toBe(true);
  // the first card, under the page head, full width
  const card = page.locator('#frcCard-yesterday');
  await expect(card).toBeVisible();
  expect(await page.evaluate(() => document.querySelector('#frcCards .ph-ov-card').id)).toBe('frcCard-yesterday');
  expect(await page.evaluate(() => document.getElementById('frcHead').getBoundingClientRect().top < document.getElementById('frcCard-yesterday').getBoundingClientRect().top)).toBe(true);
  await expect(card).toHaveClass(/ph-ov-wide/);
  await expect(card).toContainText(await page.evaluate(v => '฿' + fmtN(v), api.day.revenue));
  await expect(card).toContainText(/เทียบปกติ|ยังไม่มีฐาน/);   // the one daily baseline (rules.daily_baseline, 2026-10-31)
  await expect(card).not.toContainText('ปีก่อน');
  // its detail: the day card and the month's daily table; the cards are gone until the back arrow
  await card.click();
  const day = page.locator('#frcDay');
  await expect(day).toBeVisible();
  await expect(page.locator('#frcCards')).toBeHidden();
  await expect(page.locator('#frcDtTitle')).toHaveText('เมื่อวาน');
  await expect(day).toContainText('สรุปประจำวัน');
  const hero = day.locator('.frc-hero');
  await expect(hero).toBeVisible();
  await expect(hero).toContainText(await page.evaluate(v => '฿' + fmtN(v), api.day.revenue));
  await expect(hero).not.toContainText('ปีก่อน');
  await expect(day.locator('.frc-tile')).toHaveCount(4);
  await expect(day.locator('.frc-tiles')).toContainText('ต้นทุนอาหาร');
  if (api.month) await expect(day.locator('.frc-dmonth')).toContainText('เดือนก่อน');
  await expect(day).not.toContainText('ถาม');                         // the ควรดู items stay on their own detail (owner 2026-10-27)
  await expect(page.locator('#frcDetail .frc-tbl').first()).toBeVisible();     // ยอดขายรายวัน of the month
  // the day does not follow the top bar's period
  const dayUrls = urls.filter(u => u.includes('/api/franchise/daily'));
  expect(dayUrls.length).toBeGreaterThan(0);
  expect(dayUrls.every(u => !/kind=|pick=|month=/.test(u))).toBe(true);
  // the phone: no sideways scroll, the two tiles of a row side by side, nothing under 12 px
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  const boxes = await day.locator('.frc-tile').evaluateAll(els => els.map(e => e.getBoundingClientRect()).map(r => ({ x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width) })));
  expect(boxes[0].y).toBe(boxes[1].y);
  expect(boxes[1].x).toBeGreaterThan(boxes[0].x);
  expect(Math.max(...boxes.map(b => b.x + b.w))).toBeLessThanOrEqual(390);
  const small = await page.evaluate(() => [...document.querySelectorAll('#frcDay *, #frcCards *')].filter(e => e.offsetParent && e.childNodes.length && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())
    && parseFloat(getComputedStyle(e).fontSize) < 12).map(e => e.textContent.trim().slice(0, 30)));
  expect(small).toEqual([]);
  await page.screenshot({ path: test.info().outputPath('franchise-daily-390.png'), fullPage: false });
  // back to the cards
  await page.locator('#frcBack').click();
  await expect(page.locator('#frcCards')).toBeVisible();
  await expect(day).toBeHidden();
  expect(errors).toEqual([]);
  await ctx.close();
});
