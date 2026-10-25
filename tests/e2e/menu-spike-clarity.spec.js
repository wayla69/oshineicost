// @ts-check
// PR-SX (owner 2026-10-25, "แก้เลย") · เมนูพุ่งผิดปกติ on ยอดขายรายวัน:
//   * the raw-plates section (แบบเดิม) starts folded (a new storage key; a state stored before is dropped); its rows mark menus that are also
//     per-head spikes (อยู่ในส่วนต่อหัวด้วย) and the header filter เฉพาะที่ไม่อยู่ในส่วนต่อหัว (on by default) hides them; the count shows both;
//   * ขายน้อยช่วงก่อน is a small muted marker (◔, title + aria-label = the text), not a grey line - in the table, the touch card and the sheet;
//   * balanced columns: เมนู <= 35 % of the table at 1280 px, the figures right-aligned;
//   * เมนูใหม่: more than half the listed rows without a dish cost -> no ต้นทุน column, a chip "ต้นทุนเมนูใหม่ยังไม่ครบ (N/M)".
// The menu-spike answer is mocked so the test does not depend on what the fixture holds.
const { test, expect } = require('@playwright/test');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const D1280 = { viewport: { width: 1280, height: 900 } };
const D1440 = { viewport: { width: 1440, height: 900 } };
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];

function answer(costedNew) {
  const tiers = [{ tier: '789', label: '฿789', plates: 60, customers: 140, rate: 42.9, normal_rate: 20, excess: 32, x: 2.1, high: true }];
  const row = (i, extra) => ({ branch: 'สาขาเอ', menu: `ซูชิแซลมอน ${i}`, today: 100, per_day: 14, expected: 40, excess: 60, x: 2.5, pct: 150, kind: 'buffet',
    basis: 'buffet', customers: 400, rate: 25, normal_rate: 10, tiers, tier_rest: 0, unit_cost: 20, extra_cost: 1200 - i, cost_share: 0.1,
    extra_cost_per_head_pp: 0.04, uncosted: false, package: false, ...extra });
  const spikes = [row(1), row(2, { thin_base: true }), row(3, { thin_base: true, x: null, pct: null })];
  const nm = (i, cost) => ({ branch: 'สาขาเอ', menu: `เมนูใหม่ ${i}`, first_sold: '2026-10-10', first_sold_th: '10 ต.ค.', age_days: 9, qty: 40, per_day: 4,
    kind: 'alc', basis: 'all', customers: 300, rate: 13.3, trend: null, unit_cost: cost ? 30 : 0, period_cost: cost ? 1200 : 0, uncosted: !cost, package: false });
  const new_menus = [nm(1, true), nm(2, costedNew >= 2), nm(3, costedNew >= 3), nm(4, costedNew >= 4)];
  const pl = (i, dup, thin) => ({ branch: 'สาขาเอ', menu: dup ? `ซูชิแซลมอน ${i}` : `เมนูจานเยอะ ${i}`, today: 300 - i, per_day: 40, avg: 100, expected: 100,
    excess: 200 - i, x: 3.0, pct: 200, is_new: false, kind: 'buffet', in_per_head: dup, ...(thin ? { thin_base: true } : {}) });
  const spikes_plates = [pl(1, true), pl(2, false, true), pl(3, true), pl(4, false), pl(5, true)];
  return { period: 'mtd', date_from: '2026-10-01', date_to: '2026-10-19', latest_day: '2026-10-19', spikes, count: 3, new_menus, new_count: 4,
    spikes_plates, plates_count: 5, plates_only_count: 2,
    base: { kind: 'normal', from: '2026-09-10', to: '2026-09-30' },
    summary: { customers: 52340, customers_per_day: 2754.7, base_customers_per_day: 2500, customers_vs_base_pct: 10.2, tier_top: { label: '฿789', excess: 96 }, top_excess: 180 },
    top: 15, cost_view: true, sort: 'extra_cost', food_cost_total: 1e6, revenue_total: 2e6, revenue_basis: 'net', top_extra_cost: 3594,
    top_cost_share: 0.36, top_pp: 0.18, uncosted_count: 0 };
}

async function open(browser, opts, { mode = 'fit', costedNew = 1, oldOpen = false } = {}) {
  const ctx = await browser.newContext(opts);
  if (process.env.E2E_USER && process.env.E2E_PASS) {
    const r = await ctx.request.post('/api/auth/login', { data: { username: process.env.E2E_USER, password: process.env.E2E_PASS } });
    expect(r.ok()).toBe(true);
  }
  await ctx.addInitScript(([m, old]) => { try {
    if (sessionStorage.getItem('sx_init')) return;          // once per test: a reload keeps what the page stored
    sessionStorage.setItem('sx_init', '1');
    localStorage.setItem('oshinei_phmode', JSON.stringify({ dailysales: m }));
    localStorage.setItem('oshinei_spike_period', JSON.stringify({ m: 'mtd', f: '', t: '' }));
    localStorage.setItem('oshinei_spike_all', '0');
    localStorage.removeItem('oshinei_spike_plates_open_v2');
    if (old) localStorage.setItem('oshinei_spike_plates_open', '1');      // a state stored before PR-SX (open)
  } catch (e) { /* none */ } }, [mode, oldOpen]);
  await ctx.route('**/api/sales/menu-spike?*', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(answer(costedNew)) }));
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
  await page.waitForFunction(() => !!document.querySelector('#dsSpike #spikeNewTable'), null, { timeout: 60_000 });
  await page.waitForTimeout(1200);
  return { ctx, page, errors };
}

for (const [name, opts] of [['1280', D1280], ['1440', D1440]]) {
  test(`เมนูพุ่ง (desktop ${name}): balanced columns, the ◔ marker instead of the grey text`, async ({ browser }) => {
    test.setTimeout(150_000);
    const { ctx, page, errors } = await open(browser, opts);
    const m = await page.evaluate(() => {
      const t = document.getElementById('spikeTable'), w = t.getBoundingClientRect().width;
      const th = [...t.tHead.rows[0].cells], wOf = i => th[i].getBoundingClientRect().width;
      const mk = [...t.querySelectorAll('.sp-mk')];
      const r1 = t.tBodies[0].rows[0];
      return { menu: wOf(1) / w, tier: wOf(2) / w, cost: wOf(th.length - 1) / w, n: mk.length,
        labels: mk.map(x => x.getAttribute('aria-label')), titles: mk.map(x => x.getAttribute('title')), glyph: mk.map(x => x.textContent.trim()),
        mkPx: mk.map(x => parseFloat(getComputedStyle(x).fontSize)),
        text: t.innerText, align: [...r1.cells].slice(3).map(c => getComputedStyle(c).textAlign),
        nameLines: [...t.querySelectorAll('.sp-menu')].map(x => Math.round(x.getBoundingClientRect().height / parseFloat(getComputedStyle(x).lineHeight))),
        layout: getComputedStyle(t).tableLayout, sw: document.documentElement.scrollWidth };
    });
    expect(m.menu, 'เมนู <= 35 % of the table').toBeLessThanOrEqual(0.35);
    expect(m.menu).toBeGreaterThanOrEqual(0.25);
    expect(m.tier).toBeGreaterThanOrEqual(0.13);
    expect(m.tier).toBeLessThanOrEqual(0.2);
    expect(m.cost).toBeLessThanOrEqual(0.17);
    expect(m.layout).toBe('fixed');
    expect(m.align.every(a => a === 'right')).toBe(true);
    for (const l of m.nameLines) expect(l).toBeLessThanOrEqual(2);
    expect(m.n).toBe(2);
    expect(m.labels).toEqual(['ขายน้อยช่วงก่อน', 'ขายน้อยช่วงก่อน']);
    expect(m.titles).toEqual(['ขายน้อยช่วงก่อน', 'ขายน้อยช่วงก่อน']);
    expect(m.glyph).toEqual(['◔', '◔']);
    for (const px of m.mkPx) expect(px).toBeGreaterThanOrEqual(12);
    expect(m.text).not.toContain('ขายน้อยช่วงก่อน');
    // the new-menu and raw-plates tables follow the same balance
    await page.evaluate(() => document.getElementById('spikePlatesFold').click());
    const o = await page.evaluate(() => ['spikeNewTable', 'spikePlatesTable'].map(id => {
      const t = document.getElementById(id), th = t.tHead.rows[0].cells;
      return th[1].getBoundingClientRect().width / t.getBoundingClientRect().width;
    }));
    for (const r of o) { expect(r).toBeLessThanOrEqual(0.35); expect(r).toBeGreaterThanOrEqual(0.25); }
    expect(await page.evaluate(() => document.getElementById('spikePlatesTable').innerText)).not.toContain('ขายน้อยช่วงก่อน');
    expect(m.sw).toBeLessThanOrEqual(opts.viewport.width);
    expect(errors).toEqual([]);
    await ctx.close();
  });
}

test('เมนูพุ่ง แบบเดิม: folded at first (an old stored state is dropped), duplicates hidden by default, both counts', async ({ browser }) => {
  test.setTimeout(150_000);
  const { ctx, page, errors } = await open(browser, D1280, { oldOpen: true });
  const f = await page.evaluate(() => ({ hidden: document.getElementById('spikePlatesBody').hidden, old: localStorage.getItem('oshinei_spike_plates_open'),
    btn: document.getElementById('spikePlatesFold').textContent.trim(), exp: document.getElementById('spikePlatesFold').getAttribute('aria-expanded'),
    n: document.getElementById('spikePlatesN').textContent.trim(), flt: document.getElementById('spikePlatesOnly').getAttribute('aria-pressed'),
    fltTxt: document.getElementById('spikePlatesOnly').textContent.trim() }));
  expect(f).toEqual({ hidden: true, old: null, btn: 'แสดง', exp: 'false', n: '(5 · ไม่ซ้ำ 2)', flt: 'true', fltTxt: 'เฉพาะที่ไม่อยู่ในส่วนต่อหัว' });
  await page.evaluate(() => document.getElementById('spikePlatesFold').click());
  const rows = () => page.evaluate(() => [...document.getElementById('spikePlatesTable').tBodies[0].rows].map(r => ({
    menu: r.querySelector('.sp-menu').textContent.trim(), dup: !!r.querySelector('.sp-dup-chip') })));
  expect(await rows()).toEqual([{ menu: 'เมนูจานเยอะ 2', dup: false }, { menu: 'เมนูจานเยอะ 4', dup: false }]);
  // the thin marker in this table too
  expect(await page.evaluate(() => document.querySelectorAll('#spikePlatesTable .sp-mk').length)).toBe(1);
  // filter off: every row, the duplicates marked
  await page.evaluate(() => document.getElementById('spikePlatesOnly').click());
  expect(await page.evaluate(() => document.getElementById('spikePlatesOnly').getAttribute('aria-pressed'))).toBe('false');
  const all = await rows();
  expect(all.length).toBe(5);
  expect(all.filter(r => r.dup).map(r => r.menu)).toEqual(['ซูชิแซลมอน 1', 'ซูชิแซลมอน 3', 'ซูชิแซลมอน 5']);
  expect(await page.evaluate(() => document.querySelector('#spikePlatesTable .sp-dup-chip').textContent.trim())).toBe('อยู่ในส่วนต่อหัวด้วย');
  // opened: remembered under the new key, kept after a reload
  expect(await page.evaluate(() => localStorage.getItem('oshinei_spike_plates_open_v2'))).toBe('1');
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForFunction(() => typeof switchTab === 'function', null, { timeout: 60_000 });
  await page.evaluate(() => switchTab('dailysales'));
  await page.waitForFunction(() => !!document.querySelector('#dsSpike #spikeNewTable'), null, { timeout: 60_000 });
  expect(await page.evaluate(() => document.getElementById('spikePlatesBody').hidden)).toBe(false);
  expect(errors).toEqual([]);
  await ctx.close();
});

for (const [costed, hide] of [[1, true], [2, false]]) {
  test(`เมนูใหม่: ${costed} of 4 rows costed -> the cost column ${hide ? 'hidden, a chip' : 'shown'}`, async ({ browser }) => {
    test.setTimeout(150_000);
    const { ctx, page, errors } = await open(browser, D1280, { costedNew: costed });
    const m = await page.evaluate(() => ({ heads: [...document.getElementById('spikeNewTable').tHead.rows[0].cells].map(c => c.textContent.trim()),
      chip: (document.getElementById('spikeNewCostChip') || { textContent: '' }).textContent.trim(),
      walls: document.querySelectorAll('#spikeNewTable .sp-nocost').length }));
    expect(m.heads.includes('ต้นทุน (ช่วงนี้)')).toBe(!hide);
    expect(m.chip).toBe(hide ? 'ต้นทุนเมนูใหม่ยังไม่ครบ (1/4)' : '');
    expect(m.walls).toBe(hide ? 0 : 2);
    expect(errors).toEqual([]);
    await ctx.close();
  });
}

test('เมนูพุ่ง (แตะง่าย 390): the touch card shows ◔ after the × figure, the row sheet the full text', async ({ browser }) => {
  test.setTimeout(150_000);
  const { ctx, page, errors } = await open(browser, PHONE, { mode: 'touch' });
  const m = await page.evaluate(() => {
    const cards = [...document.querySelectorAll('#dsSpike .ph-cl')][0].querySelectorAll('.ph-cl-card');
    return [...cards].map(c => c.innerText.replace(/\s+/g, ' '));
  });
  expect(m.length).toBe(3);
  expect(m[1]).toContain('×2.5 ◔');
  for (const t of m) expect(t).not.toContain('ขายน้อยช่วงก่อน');
  await page.evaluate(() => phRowSheet(document.getElementById('spikeTable').tBodies[0].rows[1]));
  const sheet = await page.evaluate(() => document.getElementById('msSheetBody').innerText.replace(/\s+/g, ' '));
  expect(sheet).toContain('ขายน้อยช่วงก่อน');
  expect(errors).toEqual([]);
  await ctx.close();
});
