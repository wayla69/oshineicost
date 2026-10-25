// @ts-check
// PR-SC (owner 2026-10-25) · ยอดขายรายวัน → เมนูพุ่งผิดปกติ sorted by ต้นทุนเพิ่ม, top 15:
//   * a role that sees cost (cost_view): the ต้นทุนเพิ่ม column (฿ bold + "% ของต้นทุนอาหาร · จุด"), the header chip, 15 rows first,
//     "ดูทั้งหมด (N)" shows every row and is remembered on this device (a reload keeps it); a row without a cost says ยังไม่มีต้นทุน;
//   * a role without cost (the server strips every cost key, backend test_menu_spike_period section 9): no cost column, no chip,
//     still 15 rows first;
//   * the touch cards lead with ต้นทุนเพิ่ม for a cost role.
// The menu-spike answer is mocked so the test does not depend on what the fixture holds.
const { test, expect } = require('@playwright/test');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];
const N = 40;

function answer(cost) {
  const spikes = Array.from({ length: N }, (_, i) => {
    const s = { branch: 'สาขาเอ', menu: `เมนูเดิม ${i + 1}`, today: 200 - i, per_day: 30, expected: 80, excess: 120 - i, x: 2.5, pct: 150, kind: 'buffet',
      basis: 'buffet', customers: 400, rate: 50, normal_rate: 20, tiers: [{ tier: '789', label: '฿789', plates: 60, customers: 140, rate: 42.9, normal_rate: 20,
        excess: 30, x: 2.1, high: true }], tier_rest: 0 };
    if (cost) {
      const uc = i === N - 1 ? 0 : 50 - i;
      Object.assign(s, { unit_cost: uc, extra_cost: (120 - i) * uc, cost_share: +((120 - i) * uc / 1e6 * 100).toFixed(2),
        extra_cost_per_head_pp: +((120 - i) * uc / 2e6 * 100).toFixed(2), uncosted: uc === 0, package: false });
    }
    return s;
  });
  const new_menus = [{ branch: 'สาขาเอ', menu: 'เมนูใหม่ 1', first_sold: '2026-10-15', first_sold_th: '15 ต.ค.', age_days: 5, qty: 40, per_day: 8,
    kind: 'alc', basis: 'all', customers: 300, rate: 13.3, trend: null, ...(cost ? { unit_cost: 30, period_cost: 1200, uncosted: false, package: false } : {}) }];
  const out = { period: 'week', date_from: '2026-10-19', date_to: '2026-10-23', latest_day: '2026-10-23', spikes, count: N, new_menus, new_count: 1,
    top: 15, cost_view: cost, sort: cost ? 'extra_cost' : 'excess' };
  if (cost) Object.assign(out, { food_cost_total: 1e6, revenue_total: 2e6, revenue_basis: 'net', top_extra_cost: 61000, top_cost_share: 6.1, top_pp: 3.05,
    uncosted_count: 1 });
  return out;
}

async function login(ctx) {
  if (process.env.E2E_USER && process.env.E2E_PASS) {
    const r = await ctx.request.post('/api/auth/login', { data: { username: process.env.E2E_USER, password: process.env.E2E_PASS } });
    expect(r.ok()).toBe(true);
  }
}

async function open(browser, opts, mode, cost, all) {
  const ctx = await browser.newContext(opts);
  await login(ctx);
  await ctx.addInitScript(([m, a]) => { try {
    localStorage.setItem('oshinei_phmode', JSON.stringify({ dailysales: m }));
    localStorage.setItem('oshinei_spike_period', JSON.stringify({ m: 'week', f: '', t: '' }));
    if (a !== null && !sessionStorage.getItem('sp_init')) { localStorage.setItem('oshinei_spike_all', a); sessionStorage.setItem('sp_init', '1'); }
  } catch (e) { /* none */ } }, [mode, all]);
  await ctx.route('**/api/sales/menu-spike?*', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(answer(cost)) }));
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' && !IGNORE.some(re => re.test(m.text()))) errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  await page.goto('/', { waitUntil: 'networkidle' });
  await go(page, opts);
  return { ctx, page, errors };
}

async function go(page, opts) {
  if (opts.isMobile) {
    await page.waitForFunction(() => document.body.classList.contains('ph-on') && typeof phOpenPage === 'function', null, { timeout: 60_000 });
    await page.evaluate(async () => { await phOpenPage(() => switchTab('dailysales')); });
  } else {
    await page.waitForFunction(() => typeof switchTab === 'function', null, { timeout: 60_000 });
    await page.evaluate(() => switchTab('dailysales'));
  }
  await page.waitForFunction(() => !!document.querySelector('#dsSpike #spikeTable'), null, { timeout: 60_000 });
  await page.waitForTimeout(600);
}

const READ = () => ({
  rows: document.querySelectorAll('#spikeTable tbody tr').length,
  heads: [...document.querySelectorAll('#spikeTable thead th')].map(th => th.textContent.trim()),
  newHeads: [...document.querySelectorAll('#spikeNewTable thead th')].map(th => th.textContent.trim()),
  btn: (document.getElementById('spikeAllBtn') || { textContent: '' }).textContent.trim(),
  chip: (() => { const c = document.getElementById('spikeCost'); return c && !c.hidden ? c.textContent.trim() : ''; })(),
  first: (document.querySelector('#spikeTable tbody tr td:nth-child(3)') || { textContent: '' }).textContent.trim(),
  nocost: document.querySelectorAll('#spikeTable .sp-nocost').length,
});

for (const [name, opts, mode] of [['desktop', { viewport: { width: 1440, height: 900 } }, 'fit'], ['พอดีจอ 390', PHONE, 'fit']]) {
  test(`เมนูพุ่ง ต้นทุนเพิ่ม (${name}): 15 rows first, ดูทั้งหมด shows all and is remembered, the cost column and chip`, async ({ browser }) => {
    test.setTimeout(150_000);
    const { ctx, page, errors } = await open(browser, opts, mode, true, '0');
    let m = await page.evaluate(READ);
    expect(m.rows).toBe(15);
    expect(m.heads).toContain('ต้นทุนเพิ่ม');
    expect(m.newHeads).toContain('ต้นทุน (ช่วงนี้)');
    expect(m.btn).toBe(`ดูทั้งหมด (${N})`);
    expect(m.chip).toContain('ต้นทุนเพิ่มรวม ฿61,000');
    expect(m.chip).toContain('6.1% ของต้นทุนอาหาร');
    expect(m.first).toContain('฿6,000');
    expect(m.first).toContain('% ของต้นทุนอาหาร');
    expect(m.first).toContain('จุด');
    await page.click('#spikeAllBtn');
    m = await page.evaluate(READ);
    expect(m.rows).toBe(N);
    expect(m.btn).toBe('แสดง 15 รายการแรก');
    expect(m.nocost).toBe(1);                                  // the last row has no cost: a grey ยังไม่มีต้นทุน
    expect(await page.evaluate(() => localStorage.getItem('oshinei_spike_all'))).toBe('1');
    await page.reload({ waitUntil: 'networkidle' });
    await go(page, opts);
    expect((await page.evaluate(READ)).rows).toBe(N);         // remembered on this device
    if (opts.isMobile) expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    expect(errors).toEqual([]);
    await ctx.close();
  });
}

test('เมนูพุ่ง: a role without cost sees no cost column and no chip, still 15 rows first', async ({ browser }) => {
  test.setTimeout(150_000);
  const { ctx, page, errors } = await open(browser, { viewport: { width: 1440, height: 900 } }, 'fit', false, '0');
  const m = await page.evaluate(READ);
  expect(m.rows).toBe(15);
  expect(m.heads).not.toContain('ต้นทุนเพิ่ม');
  expect(m.newHeads).not.toContain('ต้นทุน (ช่วงนี้)');
  expect(m.chip).toBe('');
  expect(m.btn).toBe(`ดูทั้งหมด (${N})`);
  expect(await page.evaluate(() => document.querySelectorAll('#dsSpike .sp-nocost, #dsSpike .sp-sort').length)).toBe(0);
  expect(errors).toEqual([]);
  await ctx.close();
});

test('เมนูพุ่ง ต้นทุนเพิ่ม (แตะง่าย 390): the cards lead with ต้นทุนเพิ่ม', async ({ browser }) => {
  test.setTimeout(150_000);
  const { ctx, page, errors } = await open(browser, PHONE, 'touch', true, '0');
  await page.waitForFunction(() => !!document.querySelector('#dsSpike .ph-cl'), null, { timeout: 30_000 });
  const txt = await page.evaluate(() => (document.querySelector('#dsSpike .ph-cl') || { innerText: '' }).innerText);
  expect(txt).toContain('ต้นทุนเพิ่ม');
  expect(txt).toContain('฿6,000');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  const btn = page.locator('#spikeAllBtn');
  await expect(btn).toBeVisible();
  const box = await btn.boundingBox();
  expect(box && box.height).toBeGreaterThanOrEqual(44);
  expect(errors).toEqual([]);
  await ctx.close();
});
