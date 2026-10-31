// @ts-check
// สรุปประจำวัน (owner 2026-10-27, mockup B): the card at the top of ร้านของฉัน on a 390 px phone (a franchise login, mocked on the
// auth-off fixture: the server's own answers for branch_id=1). The card is the page's first section, shows the last finished day's net
// sales with the same-weekday-last-week chip only (no last-year chip), four tiles, the month so far with the target bar; it does not
// follow the period chip; nothing scrolls sideways and no text is under 12 px.
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
  await ctx.route(/\/api\/franchise\/(summary|decide|insight|supply|daily)(\?|$)/, r => {
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
  await page.waitForFunction(() => { const b = document.getElementById('frcDay'); return b && !/กำลังโหลด/.test(b.innerText); }, null, { timeout: 90_000 });
  return { ctx, page, urls, errors };
}

test('ร้านของฉัน on a phone · สรุปประจำวัน at the top, the last finished day, no last-year chip, fits 390 px', async ({ browser }) => {
  test.setTimeout(150_000);
  const { ctx, page, urls, errors } = await openFranchise(browser);
  const card = page.locator('#frcDay');
  await expect(card).toContainText('สรุปประจำวัน');
  // the card is the first thing under the page head: above ติดต่อสำนักงานใหญ่ and สรุปของเดือนนี้
  expect(await page.evaluate(() => {
    const y = id => document.getElementById(id)?.getBoundingClientRect().top ?? -1;
    return y('frcHead') < y('frcDay') && (document.getElementById('frcMsg').hidden || y('frcDay') < y('frcMsg')) && y('frcDay') < y('frcBody');
  })).toBe(true);
  // the figures are the server's: the day's net sales on the band, the same weekday last week beside it
  const api = await page.evaluate(async () => (await fetch(`${API}/api/franchise/daily?branch_id=1`)).json());
  expect(api.available).toBe(true);
  const hero = card.locator('.frc-hero');
  await expect(hero).toBeVisible();
  await expect(hero).toContainText(await page.evaluate(v => '฿' + fmtN(v), api.day.revenue));
  await expect(hero).toContainText(/เทียบปกติ|ยังไม่มีฐาน/);   // the one daily baseline (rules.daily_baseline, 2026-10-31)
  await expect(hero).not.toContainText('ปีก่อน');
  await expect(card.locator('.frc-tile')).toHaveCount(4);
  await expect(card.locator('.frc-tiles')).toContainText('ต้นทุนอาหาร');
  if (api.month) await expect(card.locator('.frc-dmonth')).toContainText('เดือนก่อน');
  await expect(card).not.toContainText('ถาม');                        // the ควรดู items stay in their own cards (owner 2026-10-27)
  // the day does not follow the top bar's period
  const dayUrls = urls.filter(u => u.includes('/api/franchise/daily'));
  expect(dayUrls.length).toBeGreaterThan(0);
  expect(dayUrls.every(u => !/kind=|pick=|month=/.test(u))).toBe(true);
  // the phone: no sideways scroll, the two tiles of a row side by side, nothing under 12 px
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  const boxes = await card.locator('.frc-tile').evaluateAll(els => els.map(e => e.getBoundingClientRect()).map(r => ({ x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width) })));
  expect(boxes[0].y).toBe(boxes[1].y);
  expect(boxes[1].x).toBeGreaterThan(boxes[0].x);
  expect(Math.max(...boxes.map(b => b.x + b.w))).toBeLessThanOrEqual(390);
  const small = await page.evaluate(() => [...document.querySelectorAll('#frcDay *')].filter(e => e.childNodes.length && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())
    && parseFloat(getComputedStyle(e).fontSize) < 12).map(e => e.textContent.trim().slice(0, 30)));
  expect(small).toEqual([]);
  await page.screenshot({ path: test.info().outputPath('franchise-daily-390.png'), fullPage: false });
  expect(errors).toEqual([]);
  await ctx.close();
});
