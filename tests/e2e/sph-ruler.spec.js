// @ts-check
// Owner 2026-10-25 · ลูกค้าและยอดต่อหัว → การกระจาย SPH ตามช่วงยอดใช้จ่าย/หัว: the percentile labels under the 100 % bar were piled on top of each
// other at the far left. They were placed on a value scale (sph_min -> sph_max) and one outlier bill (฿18,692) set the top end. The bar is a
// share-of-bills bar (bands ascending by spend), so now:
//   * the five ticks sit at their bill share: P10 10 %, P25 25 %, มัธยฐาน 50 %, P75 75 %, P90 90 % of the bar;
//   * no two visible labels overlap (a narrow card keeps only P25 · มัธยฐาน · P75 labelled; P10/P90 keep the tick);
//   * labels are >= 11 px (>= 12 px in แตะง่าย); the min/max corner texts are gone.
// The SPH answer is mocked (an outlier max, bands sent out of order) so the case exists whatever the fixture holds.
const { test, expect } = require('@playwright/test');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];
const SHOTS = process.env.SPH_SHOTS || '';      // a folder: also save a screenshot of the card per case

function sphAnswer() {
  const bands = [
    ['฿590–699', 590, 700, '#4f46e5', 2600], ['ต่ำกว่า ฿350', 0, 350, '#94a3b8', 400], ['฿350–459', 350, 460, '#eab308', 900],
    ['฿460–589', 460, 590, '#10b981', 2100], ['฿700–889', 700, 890, '#f97316', 2900], ['฿890 ขึ้นไป', 890, null, '#ef4444', 1100],
  ];
  const n = bands.reduce((s, b) => s + Number(b[4]), 0);
  const distribution = bands.map(([bucket, lo, hi, color, count]) => ({ bucket, lo, hi, color, count, pct: Math.round(Number(count) / n * 1000) / 10 }));
  return {
    by_branch: [{ branch: 'สาขาเอ', avg_sph: 690, median_sph: 655, tx_count: n }], distribution,
    overall: { avg_sph: 668, median_sph: 655, p10: 421, p25: 534, p75: 792, p90: 905, total_tx: n, dominant_tier: '฿700–889', dominant_pct: 29,
      high_spend_from: 700, pct_above_700: 40, pct_above_590: 40, pct_below_350: 4, sph_min: 0, sph_max: 18692 },
    by_daypart: [{ daypart: 'Lunch 11-14', avg_sph: 650, tx_count: 4000 }, { daypart: 'Dinner 17-22', avg_sph: 720, tx_count: 6000 }],
    trend: [{ period_month: '2026-09', avg_sph: 680 }, { period_month: '2026-10', avg_sph: 700 }],
  };
}

async function login(ctx) {
  if (process.env.E2E_USER && process.env.E2E_PASS) {
    const r = await ctx.request.post('/api/auth/login', { data: { username: process.env.E2E_USER, password: process.env.E2E_PASS } });
    expect(r.ok()).toBe(true);
  }
}

async function open(browser, opts, mode, scheme) {
  const ctx = await browser.newContext({ ...opts, colorScheme: scheme });
  await login(ctx);
  await ctx.addInitScript(m => { try { localStorage.setItem('oshinei_phmode', JSON.stringify({ guest: m })); } catch (e) { /* none */ } }, mode);
  await ctx.route('**/api/analysis/sph?*', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(sphAnswer()) }));
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
  await page.waitForFunction(() => document.querySelectorAll('#sphRuler .sph-mk').length === 5, null, { timeout: 60_000 });
  await page.locator('#sphRuler').scrollIntoViewIfNeeded();
  await page.waitForTimeout(700);                 // the fit frame and the ruler's resize check settle
  return { ctx, page, errors };
}

const READ = () => {
  const ruler = /** @type {HTMLElement} */ (document.getElementById('sphRuler'));
  const bar = /** @type {HTMLElement} */ (document.getElementById('sphStackBar'));
  const rr = bar.getBoundingClientRect();
  const marks = [...ruler.querySelectorAll('.sph-mk')].map(m => {
    const tick = /** @type {HTMLElement} */ (m.firstElementChild).getBoundingClientRect();
    const lbl = /** @type {HTMLElement} */ (m.querySelector('.sph-lbl'));
    const b = lbl.getBoundingClientRect();
    return { at: Number(m.getAttribute('data-at')), pos: ((tick.left + tick.width / 2) - rr.left) / rr.width * 100, title: m.getAttribute('title') || '',
      shown: getComputedStyle(lbl).visibility !== 'hidden', left: b.left, right: b.right, text: lbl.textContent || '',
      fs: parseFloat(getComputedStyle(lbl).fontSize) };
  });
  const segs = [...bar.children].map(c => c.getAttribute('title') || '');
  return { marks, text: ruler.textContent || '', segs, touch: document.body.classList.contains('ph-on') && !document.body.classList.contains('ph-fit') };
};

const CASES = [
  ['desktop 1280 light', { viewport: { width: 1280, height: 900 } }, 'fit', 'light'],
  ['desktop 1280 dark', { viewport: { width: 1280, height: 900 } }, 'fit', 'dark'],
  ['พอดีจอ 390 light', PHONE, 'fit', 'light'],
  ['แตะง่าย 390 light', PHONE, 'touch', 'light'],
  ['แตะง่าย 390 dark', PHONE, 'touch', 'dark'],
  ['แตะง่าย 320', { ...PHONE, viewport: { width: 320, height: 700 } }, 'touch', 'light'],
];

for (const [name, opts, mode, scheme] of CASES) {
  test(`SPH percentile ruler (${name}): ticks at 10/25/50/75/90 % of the bar, visible labels do not overlap`, async ({ browser }) => {
    test.setTimeout(150_000);
    const { ctx, page, errors } = await open(browser, /** @type {any} */ (opts), mode, scheme);
    const r = await page.evaluate(READ);
    expect(r.marks.map(m => m.at)).toEqual([10, 25, 50, 75, 90]);
    for (const m of r.marks) expect(Math.abs(m.pos - m.at), `${m.at}: tick at ${m.pos.toFixed(2)} %`).toBeLessThan(1);
    // the bands are drawn ascending by spend even when the answer lists them out of order
    expect(r.segs[0]).toContain('ต่ำกว่า ฿350');
    expect(r.segs[r.segs.length - 1]).toContain('฿890 ขึ้นไป');
    const shown = r.marks.filter(m => m.shown);
    for (let i = 1; i < shown.length; i++) expect(shown[i].left, `${shown[i - 1].text} / ${shown[i].text}`).toBeGreaterThanOrEqual(shown[i - 1].right);
    expect(shown.map(m => m.at)).toEqual(expect.arrayContaining([25, 50, 75]));
    expect(r.marks.find(m => m.at === 50)?.text).toContain('มัธยฐาน');
    for (const m of r.marks) { expect(m.title).toContain(`${m.at}%`); expect(m.fs).toBeGreaterThanOrEqual(r.touch ? 12 : 11); }
    expect(r.text).not.toContain('18,692');                  // no min/max corner texts
    if (SHOTS) await page.locator('#sphRuler').locator('xpath=..').screenshot({ path: `${SHOTS}/after-${name.replace(/\s+/g, '-')}.png` });
    if (name === 'แตะง่าย 320') {
      // the collision guard: a card too narrow for five labels keeps P25 · มัธยฐาน · P75; P10/P90 keep the tick and the tooltip
      // The width is taken from the labels' own widths (fonts differ between Windows and the Linux CI runner): just wide enough for
      // P25 · มัธยฐาน · P75 side by side (25 % apart), which is far too narrow for P10/P25 (15 % apart) - so the guard must fire.
      const narrow = await page.evaluate(() => {
        const ruler = /** @type {HTMLElement} */ (document.getElementById('sphRuler'));
        const w = Object.fromEntries([...ruler.querySelectorAll('.sph-mk')].map(m => [m.getAttribute('data-at'), /** @type {HTMLElement} */ (m.querySelector('.sph-lbl')).getBoundingClientRect().width]));
        const need = Math.max((w[25] + w[50]) / 2 + 8, (w[50] + w[75]) / 2 + 8) / 0.25;
        const clash = (w[10] + w[25]) / 2 + 4 > 0.15 * need;
        ruler.style.width = Math.ceil(need) + 'px';
        return { need: Math.ceil(need), clash };
      });
      expect(narrow.clash, `at ${narrow.need} px P10/P25 must collide`).toBe(true);
      await page.waitForTimeout(300);
      const n = await page.evaluate(READ);
      expect(n.marks.filter(m => m.shown).map(m => m.at)).toEqual([25, 50, 75]);
      const vis = n.marks.filter(m => m.shown);
      for (let i = 1; i < vis.length; i++) expect(vis[i].left).toBeGreaterThanOrEqual(vis[i - 1].right);
      await page.evaluate(() => { /** @type {HTMLElement} */ (document.getElementById('sphRuler')).style.width = ''; });
      await page.waitForTimeout(300);
      expect((await page.evaluate(READ)).marks.filter(m => m.shown).length).toBe(5);
    }
    expect(errors).toEqual([]);
    await ctx.close();
  });
}
