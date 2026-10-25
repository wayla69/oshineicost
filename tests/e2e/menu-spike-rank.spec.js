// @ts-check
// PR-RK (owner 2026-10-25, "ตัวเลขตรงนี้น่าจะผิดปกติ เพราะเมนูที่นิยมไม่ติดเลย เรียงผิดครับ") · ยอดขายรายวัน → เมนูพุ่งผิดปกติ:
//   * เมนูเดิม is a ranking - every existing menu above its normal by ต้นทุนเพิ่ม, the 1.5x gate gone: a popular x1.1 menu with the
//     largest extra cost is row 1; rows of the old rule (spike: true) carry the "พุ่ง ≥1.5×" chip in bad ink; the เมนูพุ่ง tile counts
//     spike_count, not the ranked rows;
//   * fee lines ("(OT) ค่าปรับบุฟเฟ่ต์ …", ค่าเปิดห้อง …) are in no section (the server drops them, rules.non_food_sql): checked on the
//     real answer of the fixture server.
const { test, expect } = require('@playwright/test');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];

// the server's order: by extra_cost (backend test_menu_spike_period section 12)
const ROWS = [
  ['ซาชิมิแซลมอน', 1.1, 760, 10.15, false], ['ซาชิมิทูน่า', 1.2, 400, 10.2, false], ['ซอฟต์ครีมมัตฉะ', 8.6, 271, 10, true],
  ['ฮามาจิสไลด์', 2.1, 32, 20, true], ['ข้าวปั้นกุ้ง', 1.06, 30, 8, false],
];

function answer() {
  const spikes = ROWS.map(([menu, x, excess, uc, spike]) => ({ branch: 'ชลบุรี', menu, today: 1000, per_day: 140, expected: 1000 - excess,
    avg: 1000 - excess, excess, x, pct: Math.round((x - 1) * 100), is_new: false, spike, kind: 'buffet', basis: 'buffet', customers: 5000, rate: 20,
    normal_rate: 18, tiers: [], tier_rest: 0, unit_cost: uc, extra_cost: Math.round(excess * uc * 100) / 100, cost_share: 1.2,
    extra_cost_per_head_pp: 0.4, uncosted: false, package: false }));
  return { period: 'week', date_from: '2026-10-19', date_to: '2026-10-24', latest_day: '2026-10-24', spikes, count: 120, spike_count: 2,
    threshold: 1.5, new_menus: [], new_count: 0, top: 15, cost_view: true, sort: 'extra_cost', food_cost_total: 1e6, revenue_total: 2e6,
    revenue_basis: 'net', top_extra_cost: 17000, top_cost_share: 1.7, top_pp: 0.85, uncosted_count: 0, spikes_plates: [], plates_count: 0,
    plates_only_count: 0 };
}

async function login(ctx) {
  if (process.env.E2E_USER && process.env.E2E_PASS) {
    const r = await ctx.request.post('/api/auth/login', { data: { username: process.env.E2E_USER, password: process.env.E2E_PASS } });
    expect(r.ok()).toBe(true);
  }
}

async function open(browser, opts, mock, period) {
  const ctx = await browser.newContext(opts);
  await login(ctx);
  await ctx.addInitScript(([p]) => { try {
    localStorage.setItem('oshinei_phmode', JSON.stringify({ dailysales: 'fit' }));
    localStorage.setItem('oshinei_spike_period', JSON.stringify({ m: p, f: '', t: '' }));
    localStorage.setItem('oshinei_spike_all', '0');
  } catch (e) { /* none */ } }, [period]);
  if (mock) await ctx.route('**/api/sales/menu-spike?*', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(answer()) }));
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' && !IGNORE.some(re => re.test(m.text()))) errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  await page.goto('/', { waitUntil: 'networkidle' });
  if (opts.isMobile) {
    await page.waitForFunction(() => document.body.classList.contains('ph-on') && typeof phOpenPage === 'function', null, { timeout: 60_000 });
    await page.evaluate(async () => { await phOpenPage(() => switchTab('dailysales')); });
  } else {
    await page.waitForFunction(() => typeof switchTab === 'function', null, { timeout: 60_000 });
    await page.evaluate(() => switchTab('dailysales'));
  }
  await page.waitForFunction(() => !!document.querySelector('#dsSpike .sp-sec-old'), null, { timeout: 60_000 });
  await page.waitForTimeout(600);
  return { ctx, page, errors };
}

const READ = () => {
  const rows = [...document.querySelectorAll('#spikeTable tbody tr')];
  const baht = t => Number((t.match(/฿([\d,]+(?:\.\d+)?)/) || [0, '0'])[1].replace(/,/g, ''));
  return {
    title: (document.querySelector('#dsSpike .sp-sec-old .sp-h') || { textContent: '' }).textContent.replace(/\s+/g, ' ').trim(),
    menus: rows.map(r => (r.querySelector('.sp-menu') || { textContent: '' }).textContent.trim()),
    cost: rows.map(r => baht((r.querySelector('td:last-child .sp-v') || { textContent: '' }).textContent)),
    chips: rows.map(r => !!r.querySelector('.sp-hi-chip')),
    chipText: (document.querySelector('#spikeTable .sp-hi-chip') || { textContent: '' }).textContent.trim(),
    chipInk: (() => { const c = document.querySelector('#spikeTable .sp-hi-chip'); return c ? getComputedStyle(c).color : ''; })(),
    badInk: (() => { const p = document.createElement('span'); p.style.color = 'var(--bad-ink)'; document.body.appendChild(p);
      const c = getComputedStyle(p).color; p.remove(); return c; })(),
    tile: [...document.querySelectorAll('#spikeStrip .sp-tile')].map(t => t.textContent.replace(/\s+/g, ' ').trim()),
  };
};

for (const [name, opts] of [['desktop', { viewport: { width: 1440, height: 900 } }], ['พอดีจอ 390', PHONE]]) {
  test(`เมนูเดิม ranked (${name}): the largest extra cost first, the พุ่ง chip on >= 1.5x rows only, the tile counts spike_count`, async ({ browser }) => {
    test.setTimeout(150_000);
    const { ctx, page, errors } = await open(browser, opts, true, 'week');
    const m = await page.evaluate(READ);
    expect(m.title).toContain('เมนูเดิม · ดันต้นทุนเพิ่มสูงสุด');
    expect(m.title).toContain('(120 · พุ่ง 2');
    expect(m.menus[0]).toBe('ซาชิมิแซลมอน');
    expect(m.cost[0]).toBe(Math.max(...m.cost));
    expect(m.cost).toEqual([...m.cost].sort((a, b) => b - a));
    expect(m.chips).toEqual([false, false, true, true, false]);
    expect(m.chipText).toBe('พุ่ง ≥1.5×');
    expect(m.chipInk).toBe(m.badInk);
    expect(m.tile.find(t => t.startsWith('เมนูพุ่ง'))).toContain('2 รายการ');
    expect(m.tile.find(t => t.startsWith('ต้นทุนเพิ่มรวม'))).toContain('อันดับแรกที่แสดง');
    if (opts.isMobile) expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    expect(errors).toEqual([]);
    await ctx.close();
  });
}

test('เมนูพุ่ง on the fixture: no fee line in any section, the ranked rows in extra-cost order', async ({ browser }) => {
  test.setTimeout(150_000);
  const { ctx, page, errors } = await open(browser, { viewport: { width: 1440, height: 900 } }, false, 'mtd');
  const txt = await page.evaluate(() => document.getElementById('dsSpike').innerText);
  for (const w of ['ค่าปรับ', 'ค่าเปิดห้อง', 'ค่ามัดจำ', 'ค่าเสียหาย', '(OT)']) expect(txt).not.toContain(w);
  const m = await page.evaluate(READ);
  expect(m.cost).toEqual([...m.cost].sort((a, b) => b - a));
  expect(errors).toEqual([]);
  await ctx.close();
});
