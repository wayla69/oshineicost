// @ts-check
// Owner 2026-10-25 · ลูกค้าและยอดต่อหัว → การกระจาย SPH: "อยากให้เพิ่มตาม new price tier ที่ใช้ตั้งแต่เดือน 9 ด้วย".
// Under the spend bands the card now shows customers by the Grand Menu price tier (from 1 Sep 2026):
//   * a 100 % bar of customers by tier, ฿459 → ฿1,299 then โปรฯ, เด็ก, ไม่ใช่บุฟเฟ่ต์, labels inside a segment only when > 4 %;
//   * one tile per tier (customers · bills · share · ยอด/หัว, +฿ above the tier price in the good ink);
//   * the chip "นับตั้งแต่ 1 ก.ย. 2569" when the period starts before 1 Sep; a one-line empty state when tiers is null;
//   * labels never overlap, >= 12 px on a phone, tiles >= 44 px tall, no console error - desktop, 390 แตะง่าย and พอดีจอ.
// The SPH answer is mocked so the case exists whatever the fixture holds.
const { test, expect } = require('@playwright/test');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];
const SHOTS = process.env.SPHT_SHOTS || '';      // a folder: also save a screenshot of the card per case

const COLORS = { 459: '#0ea5e9', 629: '#14b8a6', 789: '#6366f1', 999: '#f59e0b', 1299: '#c026d3', promo: '#8b5cf6', child: '#ec4899', alacarte: '#94a3b8' };
function tiersAnswer() {
  const raw = [['459', 154, 413, 417.8, 408.54], ['629', 1541, 4360, 548.7, 559.86], ['789', 15800, 44816, 731.1, 702.27], ['999', 4179, 11025, 867.7, 889.19],
    ['1299', 555, 1406, 1143.6, 1156.21], ['promo', 3063, 9788, 657.9, null], ['child', 5, 5, 357.3, null], ['alacarte', 3752, 6852, 438.1, null]];
  const label = { promo: 'โปรฯ/ราคาอื่น', child: 'เด็กล้วน/อื่นๆ', alacarte: 'ไม่ใช่บุฟเฟ่ต์' };
  const nb = raw.reduce((s, r) => s + Number(r[1]), 0), np = raw.reduce((s, r) => s + Number(r[2]), 0);
  const rows = raw.map(([tier, bills, pax, rph, base]) => ({
    tier, label: label[String(tier)] || `฿${Number(tier).toLocaleString('en-US')}`, price: /^\d+$/.test(String(tier)) ? Number(tier) : null,
    base_price: base, color: COLORS[String(tier)], bills, pax, revenue: Number(rph) * Number(pax),
    bill_share: Math.round(Number(bills) / nb * 10000) / 100, pax_share: Math.round(Number(pax) / np * 10000) / 100, rev_per_head: rph,
    sph_vs_price: base ? Number(rph) / Number(base) : null, over_price: base ? Number(rph) - Number(base) : null,
  }));
  return { from: '2026-09-01', to: '2026-10-24', clipped: true, data_to: '2026-10-19', lagging: true, grand_menu_from: '2026-09-01', rows };
}
function sphAnswer(withTiers) {
  const bands = [['ต่ำกว่า ฿350', 0, 350, '#94a3b8', 400], ['฿350–459', 350, 460, '#eab308', 900], ['฿460–589', 460, 590, '#10b981', 2100],
    ['฿590–699', 590, 700, '#4f46e5', 2600], ['฿700–889', 700, 890, '#f97316', 2900], ['฿890 ขึ้นไป', 890, null, '#ef4444', 1100]];
  const n = bands.reduce((s, b) => s + Number(b[4]), 0);
  return {
    by_branch: [{ branch: 'สาขาเอ', avg_sph: 690, median_sph: 655, tx_count: n }],
    distribution: bands.map(([bucket, lo, hi, color, count]) => ({ bucket, lo, hi, color, count, pct: Math.round(Number(count) / n * 1000) / 10 })),
    overall: { avg_sph: 668, median_sph: 655, p10: 421, p25: 534, p75: 792, p90: 905, total_tx: n, dominant_tier: '฿700–889', dominant_pct: 29,
      high_spend_from: 700, pct_above_700: 40, pct_above_590: 40, pct_below_350: 4, sph_min: 0, sph_max: 1800 },
    by_daypart: [{ daypart: 'Lunch 11-14', avg_sph: 650, tx_count: 4000 }], trend: [{ period_month: '2026-10', avg_sph: 700 }],
    tiers: withTiers ? tiersAnswer() : null, tiers_reason: withTiers ? null : 'before_grand_menu',
  };
}

async function login(ctx) {
  if (process.env.E2E_USER && process.env.E2E_PASS) {
    const r = await ctx.request.post('/api/auth/login', { data: { username: process.env.E2E_USER, password: process.env.E2E_PASS } });
    expect(r.ok()).toBe(true);
  }
}

async function open(browser, opts, mode, scheme, withTiers) {
  const ctx = await browser.newContext({ ...opts, colorScheme: scheme });
  await login(ctx);
  await ctx.addInitScript(m => { try { localStorage.setItem('oshinei_phmode', JSON.stringify({ guest: m })); } catch (e) { /* none */ } }, mode);
  await ctx.route('**/api/analysis/sph?*', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(sphAnswer(withTiers)) }));
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' && !IGNORE.some(re => re.test(m.text()))) errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  await page.goto('/', { waitUntil: 'networkidle' });
  if (opts.isMobile) {
    await page.waitForFunction(() => document.body.classList.contains('ph-on') && typeof phOpenPage === 'function', null, { timeout: 60_000 });
    await page.evaluate(async () => { await phOpenPage(() => switchTab('guest')); });
  } else {
    await page.waitForFunction(() => typeof switchTab === 'function', null, { timeout: 60_000 });
    await page.evaluate(() => switchTab('guest'));
  }
  await page.waitForFunction(() => !!document.querySelector('#sphGrandTiers h3'), null, { timeout: 60_000 });
  await page.locator('#sphGrandTiers').scrollIntoViewIfNeeded();
  await page.waitForTimeout(700);                 // the fit frame settles
  return { ctx, page, errors };
}

const READ = () => {
  const root = /** @type {HTMLElement} */ (document.getElementById('sphGrandTiers'));
  const segs = [...root.querySelectorAll('.sph-gt-seg')].map(s => {
    const b = s.getBoundingClientRect(), l = s.querySelector('span');
    const lb = l ? l.getBoundingClientRect() : null;
    const shown = !!l && getComputedStyle(l).visibility !== 'hidden';
    return { tier: s.getAttribute('data-tier'), left: b.left, right: b.right, label: l ? l.textContent : '', shown,
      inside: (lb && shown) ? (lb.left >= b.left - 0.5 && lb.right <= b.right + 0.5) : true, fs: l ? parseFloat(getComputedStyle(l).fontSize) : 99 };
  });
  const tiles = [...root.querySelectorAll('.sph-gt-tile')].map(t => {
    const b = t.getBoundingClientRect();
    const fonts = [...t.querySelectorAll('div,span')].filter(e => e.childNodes.length && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()))
      .map(e => parseFloat(getComputedStyle(e).fontSize));
    return { tier: t.getAttribute('data-tier'), text: t.textContent.replace(/\s+/g, ' ').trim(), h: b.height, left: b.left, right: b.right, top: b.top, bottom: b.bottom,
      minFs: Math.min(...fonts) };
  });
  return { segs, tiles, chips: [...root.querySelectorAll('.sph-gt-chip')].map(c => c.textContent), empty: root.querySelector('.sph-gt-empty')?.textContent || '',
    title: root.querySelector('h3')?.textContent || '', touch: document.body.classList.contains('ph-on') && !document.body.classList.contains('ph-fit'),
    pageW: document.documentElement.clientWidth, scrollW: document.documentElement.scrollWidth };
};

const CASES = [
  ['desktop 1280 light', { viewport: { width: 1280, height: 900 } }, 'fit', 'light'],
  ['desktop 1280 dark', { viewport: { width: 1280, height: 900 } }, 'fit', 'dark'],
  ['พอดีจอ 390 light', PHONE, 'fit', 'light'],
  ['แตะง่าย 390 light', PHONE, 'touch', 'light'],
  ['แตะง่าย 390 dark', PHONE, 'touch', 'dark'],
];

for (const [name, opts, mode, scheme] of CASES) {
  test(`SPH by Grand Menu tier (${name}): bar, tiles, chip; labels do not overlap`, async ({ browser }) => {
    test.setTimeout(150_000);
    const { ctx, page, errors } = await open(browser, /** @type {any} */ (opts), mode, scheme, true);
    const r = await page.evaluate(READ);
    expect(r.title).toContain('ตาม tier ราคาเมนูใหม่');
    expect(r.segs.map(s => s.tier)).toEqual(['459', '629', '789', '999', '1299', 'promo', 'alacarte']);   // child (0.01 %) has no segment
    for (let i = 1; i < r.segs.length; i++) expect(r.segs[i].left).toBeGreaterThanOrEqual(r.segs[i - 1].right - 0.5);
    for (const s of r.segs) expect(s.inside, `visible label of ${s.tier} stays inside its segment`).toBe(true);
    // a label only on segments above 4 %, and only where it fits (a narrow phone bar drops the ~5 % one)
    expect(r.segs.filter(s => s.label).map(s => s.tier)).toEqual(['629', '789', '999', 'promo', 'alacarte']);
    const shown = r.segs.filter(s => s.shown).map(s => s.tier);
    expect(shown).toEqual(expect.arrayContaining(['789', '999']));
    if (!opts.isMobile) expect(shown).toEqual(expect.arrayContaining(['789', '999', 'promo', 'alacarte']));
    expect(r.tiles.map(t => t.tier)).toEqual(['459', '629', '789', '999', '1299', 'promo', 'child', 'alacarte']);
    const t789 = r.tiles.find(t => t.tier === '789');
    expect(t789?.text).toContain('฿789');
    expect(t789?.text).toContain('44,816 ลูกค้า');
    expect(t789?.text).toContain('15,800 บิล');
    expect(t789?.text).toContain('ยอด/หัว ฿731');
    expect(t789?.text).toContain('+฿29 จากราคา tier');
    expect(r.tiles.find(t => t.tier === 'alacarte')?.text).toContain('ไม่ใช่บุฟเฟ่ต์');
    expect(r.chips).toContain('นับตั้งแต่ 1 ก.ย. 2569');
    // tiles never overlap one another
    for (let i = 0; i < r.tiles.length; i++) for (let j = i + 1; j < r.tiles.length; j++) {
      const a = r.tiles[i], b = r.tiles[j];
      const apart = a.right <= b.left + 0.5 || b.right <= a.left + 0.5 || a.bottom <= b.top + 0.5 || b.bottom <= a.top + 0.5;
      expect(apart, `${a.tier} / ${b.tier}`).toBe(true);
    }
    if (r.touch) {
      for (const t of r.tiles) { expect(t.h).toBeGreaterThanOrEqual(44); expect(t.minFs).toBeGreaterThanOrEqual(12); }
      for (const s of r.segs) expect(s.fs).toBeGreaterThanOrEqual(12);
      const cols = new Set(r.tiles.map(t => Math.round(t.left)));
      expect(cols.size).toBe(2);                                  // 2-up on a phone
      expect(r.scrollW).toBeLessThanOrEqual(r.pageW + 1);
    }
    if (SHOTS) await page.locator('#sphGrandTiers').locator('xpath=..').screenshot({ path: `${SHOTS}/e2e-${name.replace(/\s+/g, '-')}.png` });
    expect(errors).toEqual([]);
    await ctx.close();
  });
}

test('SPH by Grand Menu tier: a period before 1 Sep 2026 shows the one-line empty state', async ({ browser }) => {
  test.setTimeout(150_000);
  const { ctx, page, errors } = await open(browser, { viewport: { width: 1280, height: 900 } }, 'fit', 'light', false);
  const r = await page.evaluate(READ);
  expect(r.empty).toBe('เมนูใหม่เริ่ม 1 ก.ย. 2569 · เลือกช่วงตั้งแต่ ก.ย. 2569');
  expect(r.segs.length).toBe(0);
  expect(errors).toEqual([]);
  await ctx.close();
});
