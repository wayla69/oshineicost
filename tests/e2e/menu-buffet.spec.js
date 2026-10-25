// @ts-check
// DV7 (owner 2026-10-25, phone table review #10) · ปรับเมนู (menuactionboard) and โอกาสเมนู (menuopportunities) around the buffet cost per head.
//   * both pages follow the top bar: a period change and a branch change reload them with the new kind / pick / branch;
//   * ปรับเมนู: the 3-line answer, three lists by baht (ราคาต่อจานขึ้น · กินต่อหัวมากขึ้น · ลดลง), 5 rows above the floor, "ดูทั้งหมด (N)";
//     no lifecycle tag ("ใหม่") anywhere; à la carte as a sub-section;
//   * โอกาสเมนู: the over-eating table leads with ลดได้ (฿) for a cost role, with จานที่เกิน without one;
//   * desktop, 390 แตะง่าย and พอดีจอ, light / dark; no console error; no text under 12 px.
// The answers are mocked (the fixture has no buffet fact tables); backend/test_menu_board.py checks the numbers.
const { test, expect } = require('@playwright/test');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];
const SHOTS = process.env.MB_SHOTS || '';
const PERIOD = { kind: 'month', cur: ['2026-10-01', '2026-10-19'], prev: ['2026-09-01', '2026-09-19'], ly: ['2025-10-01', '2025-10-19'], days: 19, complete: false,
  label_th: '1–19 ต.ค. 2569', prev_label_th: '1–19 ก.ย. 2569', ly_label_th: '1–19 ต.ค. 2568', anchor: '2026-10-19', has_prev: true, has_ly: true };

function dish(name, impact, cause, i) {
  return { sku_code: 'B' + i, name, impact, cause, eat: cause === 'price' ? 0 : impact, price: cause === 'price' ? impact : 0, impact_share: impact / 300000, cost: 100000, prev_cost: 90000,
    cost_per_head: 36.72, prev_cost_per_head: 25.33, plates_per_100: 8.8, prev_plates_per_100: 1.6, cost_per_plate: 44.28, prev_cost_per_plate: 30.54,
    above_floor: Math.abs(impact) >= 30000, cum_share: 50, branches: [{ branch: 'ขอนแก่น', impact: impact / 10 }, { branch: 'อยุธยา', impact: impact / 20 }], branches_more: 3,
    tiers: cause === 'price' ? [] : [{ tier: '789', label: '฿789', impact: impact * 0.8 }] };
}
function board(branch) {
  const eat = Array.from({ length: 8 }, (_, i) => dish(`ปลาแซลมอนย่าง ${i + 1}`, 300000 - i * 35000, 'eat', i));
  return { period: PERIOD, branch: branch || null, group: null,
    headline: { customers: 78665, food_cost: 30104150, revenue: 53663474, cost_per_head: 382.69, rev_per_head: 682.18, food_cost_pct: 56.1,
      prev: { customers: 83985, food_cost: 32706575, revenue: 57884998, cost_per_head: 389.43, rev_per_head: 689.23, food_cost_pct: 56.5 },
      cost_per_head_change: -6.75, rev_per_head_change: -7.05, per_head_effect: -530638 },
    floor: { baht: 30104, share_pct: 0.1, min_baht: 1000 },
    buffet: { buckets: { price: [dish('แซลมอนดองซีอิ๊ว', 923809, 'price', 90)], eat, down: [dish('ทุเรียน', -640851, 'eat', 91)] },
      counts: { price: 1, eat: 8, down: 1 }, totals: { price: 923809, eat: 1266781, down: -640851 }, net: 1549739,
      uncosted: [{ sku_code: 'X', name: 'ปานิปูริ', plates: 5276, uncosted_plates: 5276, plates_per_100: 6.7 }], uncosted_count: 1 },
    alc: { rows: [{ sku_code: 'A1', name: 'ซาชิมิแซลมอน', plates: 2782, revenue: 109950, cost: 37640, gp: 72310, gp_pct: 65.8, prev_gp: 68965, gp_change: 3345, share: 6.3, cum_share: 6.3, above_floor: true }],
      count: 1, revenue: 1880678, gp: 1154057, revenue_share: 3.5, floor_baht: 53663, uncosted: [], uncosted_count: 0 },
    tier_note: null, reasons: {} };
}
function opp(cost) {
  const row = { sku_code: 'BF1', name: '(BF) ทุเรียน', excess_plates: 5336, n_over: 4, n_selling: 18, median_rate: null, share: 19.4, cum_share: 19.4, above_floor: true,
    branches: [{ branch: 'ขอนแก่น', excess_plates: 4124, x: 2.24, rate: 40, normal_rate: 18 }], branches_more: 3 };
  if (cost) { row.excess_cost = 362860; row.branches[0].excess_cost = 280430; }
  const bo = { rows: [row], count: 1, total_plates: 5336, floor_plates: 1860, buffet_plates: 1860000, basis: 'tier', basis_note: null };
  if (cost) Object.assign(bo, { total_cost: 362860, floor_baht: 30104, food_cost: 30104150 });
  return { period: PERIOD, branch: null, group: null, cost_view: cost, buffet_over: bo,
    alc_gaps: { rows: [{ sku_code: 'A2', name: 'ซูชิตับห่าน', price: 89.4, median_rate: 1.2, potential_revenue: 15610, potential_plates: 174, n_branches: 12, n_selling: 9,
      share: 100, cum_share: 100, above_floor: false, branches: [{ branch: 'ขอนแก่น', potential_plates: 38, potential_revenue: 3419, rate: 0, not_sold: true }], branches_more: 11 }],
      count: 1, total_revenue: 15610, floor_baht: 53663, revenue: 53663474 },
    revive: [{ sku_code: 'R1', name: 'ข้าวหน้าปลาไหล', category: 'Oshinei DON', revenue_per_month: 5400, plates_per_month: 12, last_month: '2026-07', above_floor: true }],
    category_whitespace: [{ category: 'Oshinei SUSHI', n_skus: 105, revenue: 1955976, revenue_per_sku: 18628, slope_pct: 0.7, label: 'expand', reason: '' }],
    months: ['2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09'], reasons: {} };
}

async function open(browser, opts, tab, mode, scheme, cost) {
  const ctx = await browser.newContext({ ...opts, colorScheme: scheme });
  if (process.env.E2E_USER && process.env.E2E_PASS) {
    const r = await ctx.request.post('/api/auth/login', { data: { username: process.env.E2E_USER, password: process.env.E2E_PASS } });
    expect(r.ok()).toBe(true);
  }
  await ctx.addInitScript(([t, m, d]) => {
    try { localStorage.setItem('oshinei_phmode', JSON.stringify({ [t]: m })); localStorage.setItem('oshinei_dark', d ? '1' : '0'); } catch (e) { /* none */ }
  }, [tab, mode, scheme === 'dark']);
  const urls = [];
  await ctx.route(/\/api\/menu\/action-board\?/, r => { urls.push(r.request().url()); const b = new URL(r.request().url()).searchParams.get('branch');
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(board(b)) }); });
  await ctx.route(/\/api\/menu\/opportunities\?/, r => { urls.push(r.request().url()); r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(opp(cost)) }); });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' && !IGNORE.some(re => re.test(m.text()))) errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  await page.goto('/', { waitUntil: 'networkidle' });
  if (opts.isMobile) {
    await page.waitForFunction(() => document.body.classList.contains('ph-on') && typeof phOpenPage === 'function', null, { timeout: 60_000 });
    await page.evaluate(async t => { await phOpenPage(() => switchTab(t)); }, tab);
  } else {
    await page.waitForFunction(() => typeof switchTab === 'function', null, { timeout: 60_000 });
    await page.evaluate(t => switchTab(t), tab);
  }
  await page.waitForFunction(t => { const el = document.getElementById(t === 'menuactionboard' ? 'mabBoard' : 'oppOver'); return !!el && el.children.length > 0; }, tab, { timeout: 60_000 });
  await page.waitForTimeout(600);
  return { ctx, page, errors, urls };
}
const smallText = tab => document.querySelectorAll(`#tab-${tab} *`).length && [...document.querySelectorAll(`#tab-${tab} *`)]
  .filter(e => e.childElementCount === 0 && (e.textContent || '').trim() && /** @type {HTMLElement} */ (e).offsetParent && parseFloat(getComputedStyle(e).fontSize) < 12)
  .map(e => (e.textContent || '').trim().slice(0, 30));

test.beforeEach(() => { test.setTimeout(120_000); });   // the first page of a cold server loads the whole shell

const CASES = [['desktop', {}, 'fit'], ['390 แตะง่าย', PHONE, 'touch'], ['390 พอดีจอ', PHONE, 'fit']];
for (const [name, opts, mode] of CASES) {
  for (const scheme of /** @type {const} */ (['light', 'dark'])) {
    test(`ปรับเมนู · ${name} · ${scheme}: answer, three lists by baht, floor and ดูทั้งหมด`, async ({ browser }) => {
      const { ctx, page, errors } = await open(browser, opts, 'menuactionboard', mode, scheme, true);
      const ans = await page.locator('#mabAnswer').innerText();
      expect(ans).toContain('ต้นทุนอาหารต่อหัว');
      expect(ans).toContain('รายได้ต่อหัว');
      expect(ans).toContain('แซลมอนดองซีอิ๊ว');
      const heads = await page.locator('#mabBoard .mab-bk-t').allInnerTexts();
      expect(heads.map(h => h.split(' · ')[0])).toEqual(['ราคาต่อจานขึ้น', 'ลูกค้ากินต่อหัวมากขึ้น', 'ต้นทุนต่อหัวลดลง']);
      // the eat list: 8 dishes, 7 above the floor, 5 shown, then every one
      const eat = page.locator('#mabBoard [data-bucket="eat"]');
      await expect(eat.locator('.mab-dish')).toHaveCount(5);
      await expect(eat.locator('.mab-imp').first()).toHaveText('+฿300.0K');
      await eat.locator('.mab-more').click();
      await expect(eat.locator('.mab-dish')).toHaveCount(8);
      await expect(eat.locator('.mab-line', { hasText: 'สาขาที่ดัน' }).first()).toContainText('ขอนแก่น');
      await expect(eat.locator('.mab-line', { hasText: 'ระดับราคา' }).first()).toContainText('฿789');
      expect(await page.locator('#tab-menuactionboard').innerText()).not.toContain('ใหม่');
      if (!(mode === 'touch' && opts.isMobile)) await expect(page.locator('#mabAlcTable')).toHaveCount(1);
      expect(await page.evaluate(smallText, 'menuactionboard')).toEqual([]);
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/mab_${name.replace(/\s+/g, '_')}_${scheme}.png`, fullPage: true });
      expect(errors).toEqual([]);
      await ctx.close();
    });
    test(`โอกาสเมนู · ${name} · ${scheme}: over-eating in baht for a cost role, à la carte in revenue`, async ({ browser }) => {
      const { ctx, page, errors } = await open(browser, opts, 'menuopportunities', mode, scheme, true);
      await expect(page.locator('#oppAnswer')).toContainText('ลดได้');
      if (!(mode === 'touch' && opts.isMobile)) {
        const heads = await page.locator('#oppOverTable thead th').allInnerTexts();
        expect(heads.slice(0, 3)).toEqual(['เมนู', 'ลดได้ (฿)', 'จานที่เกิน']);
      }
      await expect(page.locator('#oppOver')).toContainText('ขอนแก่น');
      await expect(page.locator('#oppGaps')).toContainText('ดูทั้งหมด (1)');        // below the floor: behind ดูทั้งหมด
      expect(await page.evaluate(smallText, 'menuopportunities')).toEqual([]);
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/opp_${name.replace(/\s+/g, '_')}_${scheme}.png`, fullPage: true });
      expect(errors).toEqual([]);
      await ctx.close();
    });
  }
}

test('โอกาสเมนู · without a cost module: plates first, no baht column', async ({ browser }) => {
  const { ctx, page, errors } = await open(browser, {}, 'menuopportunities', 'fit', 'light', false);
  const heads = await page.locator('#oppOverTable thead th').allInnerTexts();
  expect(heads).toEqual(['เมนู', 'จานที่เกิน', 'สาขาที่เกิน', 'สะสม']);
  await expect(page.locator('#oppAnswer')).toContainText('จานที่เกินสาขาอื่น');
  expect(errors).toEqual([]);
  await ctx.close();
});

for (const tab of ['menuactionboard', 'menuopportunities']) {
  test(`${tab} follows the top bar: a period change and a branch change reload it with the new params`, async ({ browser }) => {
    const { ctx, page, errors, urls } = await open(browser, {}, tab, 'fit', 'light', true);
    const first = new URL(urls[urls.length - 1]).searchParams;
    expect(first.get('kind')).toBe(await page.evaluate(() => getPeriodKind()));
    // the period: สัปดาห์
    let n = urls.length;
    await page.evaluate(() => gPeriodSet({ kind: 'week' }));
    await expect.poll(() => urls.length).toBeGreaterThan(n);
    expect(new URL(urls[urls.length - 1]).searchParams.get('kind')).toBe('week');
    // the branch: the first branch of the top bar's filter
    const b = await page.evaluate(() => [...(/** @type {HTMLSelectElement} */ (document.getElementById('selBranch'))).options].map(o => o.value).filter(Boolean)[0]);
    expect(b).toBeTruthy();
    n = urls.length;
    await page.selectOption('#selBranch', b);
    await expect.poll(() => urls.length).toBeGreaterThan(n);
    const last = new URL(urls[urls.length - 1]).searchParams;
    expect(last.get('branch')).toBe(b);
    expect(last.get('kind')).toBe('week');
    const scope = tab === 'menuactionboard' ? '#mabStatus' : '#oppStatus';
    if (tab === 'menuactionboard') await expect(page.locator(scope)).toContainText(b);
    expect(errors).toEqual([]);
    await ctx.close();
  });
}
