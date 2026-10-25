// @ts-check
// Owner 2026-10-25 (phone screenshots of ภาพรวมยอดขาย at 390 px, พอดีจอ):
//   1. "หน้านี้อยากให้ filter รายสาขาได้ด้วย" - on a phone a one-line branch chip ("ทุกสาขา ▾") sits by the period chip and opens a sheet over the
//      top bar's own branch filter (#selBranch: a search box + its options); a pick reloads the page with branch= as the top bar does. A pinned
//      login (branch / franchise) gets no chip and no sheet (PR-K). A desktop is unchanged (no chip).
//   2. "heat map ทำให้เป็น heat map จริงๆแดงเขียวเลย" - the branch x period card colours red → neutral → green in both readings (ยอดขาย: the cell
//      vs that branch's own average; เทียบค่าปกติของสาขา: the same, the % in the cell), the vs ก่อนหน้า column too; every label >= 4.5:1.
// The heat map answer is mocked so the red and green cells exist whatever the fixture holds (PBH_LIVE=1 reads the server's own data).
const { test, expect } = require('@playwright/test');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const DESK = { viewport: { width: 1440, height: 900 } };
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];
const SHOTS = process.env.PBH_SHOTS || '';
const LIVE = process.env.PBH_LIVE === '1';

// six closed months x four branches: each branch has a weak and a strong month of its own, whatever its size
const MONTHS = ['2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09'];
const SHAPE = {
  'ขอนแก่น': [1.00, 1.04, 0.98, 1.36, 1.02, 0.70],   // big branch, a strong July, a weak September
  'นครปฐม': [1.00, 0.95, 1.08, 0.88, 1.22, 1.15],
  'บุรีรัมย์': [1.00, 1.01, 0.99, 1.00, 0.86, 0.80],
  'อุดรธานี': [1.00, 1.12, 1.25, 1.31, 1.18, 1.40],
};
const BASE = { 'ขอนแก่น': 4_200_000, 'นครปฐม': 2_600_000, 'บุรีรัมย์': 900_000, 'อุดรธานี': 1_500_000 };
function heatAnswer() {
  return Object.entries(SHAPE).flatMap(([b, k]) => MONTHS.map((p, i) => ({ branch: b, period: p, revenue: Math.round(BASE[b] * k[i]), tx_count: 1000 + i })));
}

async function login(ctx, who) {
  const name = who === 'branch' ? process.env.E2E_USER_BRANCH : process.env.E2E_USER;
  if (name && process.env.E2E_PASS) {
    const r = await ctx.request.post('/api/auth/login', { data: { username: name, password: process.env.E2E_PASS } });
    expect(r.ok()).toBe(true);
    return true;
  }
  return false;
}

async function open(browser, opts, { tab = 'overall', mode = 'fit', scheme = 'light', who = '' } = {}) {
  const ctx = await browser.newContext({ ...opts, colorScheme: scheme });
  const real = await login(ctx, who);
  await ctx.addInitScript(([m, t, d]) => {
    try {
      localStorage.setItem('oshinei_phmode', JSON.stringify({ [t]: m })); localStorage.setItem('oshinei_dark', d ? '1' : '0');
      localStorage.setItem('oshinei_gperiod', JSON.stringify({ kind: 'month', pick: '', from: '', to: '' }));
    } catch (e) { /* none */ }
  }, [mode, tab, scheme === 'dark']);
  if (who === 'branch' && !real) {        // no branch account on this server: the client sees a pinned branch login (the server pins it in pytest)
    await ctx.route('**/api/auth/me', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ enabled: true, authenticated: true,
      username: 'branch-e2e', role: 'branch', branch: 'ขอนแก่น', display_name: 'สาขาขอนแก่น', names: {}, write_gated: false,
      modules: ['sales.overview', 'supply.branch'] }) }));
  }
  if (!LIVE) await ctx.route(/\/api\/analysis\/heatmap_branch\?/, r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(heatAnswer()) }));
  const page = await ctx.newPage();
  const urls = [];
  page.on('request', r => { if (/\/api\//.test(r.url())) urls.push(r.url()); });
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' && !IGNORE.some(re => re.test(m.text()))) errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  await page.goto('/', { waitUntil: 'networkidle' });
  if (opts.isMobile) {
    await page.waitForFunction(() => document.body.classList.contains('ph-on') && typeof phOpenPage === 'function', null, { timeout: 60_000 });
    if (tab) await page.evaluate(async t => { await phOpenPage(() => switchTab(t)); }, tab);
  } else {
    await page.waitForFunction(() => typeof switchTab === 'function', null, { timeout: 60_000 });
    if (tab) await page.evaluate(t => switchTab(t), tab);
  }
  if (tab === 'overall') await page.waitForFunction(() => !!document.querySelector('#heatmapTable'), null, { timeout: 90_000 });
  await page.waitForTimeout(900);
  return { ctx, page, errors, urls };
}

async function shot(page, name, sel) {
  if (!SHOTS) return;
  if (sel) { await page.locator(sel).first().evaluate(e => { e.scrollIntoView({ block: 'start' }); window.scrollBy(0, -(document.querySelector('.top-bar')?.getBoundingClientRect().height || 0) - 8); }); await page.waitForTimeout(300); await page.screenshot({ path: `${SHOTS}/${name}.png` }); }
  else await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: false });
}

// fill / ink of the heat cells: the WCAG ratio, and red / green by the fill's channels
const CELLS = () => {
  const rgb = s => { const n = String(s).match(/[\d.]+/g) || ['0', '0', '0']; return [+n[0], +n[1], +n[2]]; };
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  return [...document.querySelectorAll('#heatmapTable .heatmap-cell.hrg')].map(e => {
    const cs = getComputedStyle(e), bg = rgb(cs.backgroundColor), fg = rgb(cs.color), a = lum(bg), b = lum(fg);
    return { step: +(e.getAttribute('data-rg') || 0), mom: e.classList.contains('hrg-mom'), k: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
      red: bg[0] > bg[1] + 30, green: bg[1] > bg[0] + 30, bg: cs.backgroundColor, fg: cs.color, txt: (e.textContent || '').trim() };
  });
};

// ── 1 · the branch chip and its sheet: 390 both modes, light and dark ──
for (const mode of ['touch', 'fit']) {
  for (const scheme of ['light', 'dark']) {
    test(`390 ${mode} ${scheme} · the branch chip opens the top bar's branch filter; a pick reloads the page with branch=`, async ({ browser }) => {
      test.setTimeout(180_000);
      const { ctx, page, errors, urls } = await open(browser, PHONE, { mode, scheme });
      const chip = page.locator('#gpBranchChip');
      await expect(chip).toBeVisible();
      await expect(chip.locator('.gb-l')).toHaveText('ทุกสาขา');
      const c = await chip.evaluate(el => {
        const r = el.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
        let span = 0;                     // the rows of pixels a tap at the chip's centre column lands on the chip
        for (let y = Math.floor(r.top) - 14; y <= Math.ceil(r.bottom) + 14; y++) { const e = document.elementFromPoint(cx, y); if (e && e.closest('#gpBranchChip') === el) span++; }
        const rc = /** @type {HTMLElement} */ (document.getElementById('gpRangeChip')).getBoundingClientRect();
        const rhit = document.elementFromPoint(rc.left + rc.width / 2, rc.top + rc.height / 2);
        return { h: r.height, right: r.right, span, cy, rangeOk: !!rhit && !!rhit.closest('#gpRangeChip'), fs: parseFloat(getComputedStyle(el).fontSize) };
      });
      expect(c.h, 'one line, 28 px drawn').toBeLessThanOrEqual(30);
      expect(c.right).toBeLessThanOrEqual(390);
      expect(c.span, '44 px to tap').toBeGreaterThanOrEqual(43);
      expect(c.rangeOk, 'the period chip keeps its own taps').toBe(true);
      expect(c.fs).toBeGreaterThanOrEqual(12);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(391);
      await shot(page, `chip_${mode}_${scheme}`);
      await chip.click();
      const sheet = page.locator('#phBranchSheet');
      await expect(sheet).toBeVisible();
      await expect(sheet.locator('#phbSearch')).toBeVisible();
      const opts = await page.evaluate(() => [...(/** @type {HTMLSelectElement} */ (document.getElementById('selBranch'))).options].map(o => o.value).filter(Boolean));
      expect(opts.length).toBeGreaterThan(0);
      // the list is the top bar's options: ทุกสาขา + every branch, one 44 px row each
      const items = await sheet.locator('.phb-item').evaluateAll(es => es.map(e => ({ v: e.getAttribute('data-v'), h: e.getBoundingClientRect().height, on: e.getAttribute('aria-checked') })));
      expect(items[0]).toMatchObject({ v: '', on: 'true' });
      expect(items.slice(1).map(i => i.v).sort()).toEqual(opts.slice().sort());
      expect(Math.min(...items.map(i => i.h))).toBeGreaterThanOrEqual(44);
      await shot(page, `sheet_${mode}_${scheme}`);
      // the search narrows the list
      const pick = opts[Math.min(1, opts.length - 1)];
      await sheet.locator('#phbSearch').fill(pick.slice(0, 3));
      const left = await sheet.locator('.phb-item').evaluateAll(es => es.map(e => e.getAttribute('data-v')));
      expect(left).toContain(pick);
      expect(left.every(v => v && v.includes(pick.slice(0, 3)))).toBe(true);
      const n0 = urls.length;
      await sheet.locator(`.phb-item[data-v="${pick}"]`).click();
      await expect(sheet).toBeHidden();
      await expect.poll(() => urls.slice(n0).some(u => new URL(u).searchParams.get('branch') === pick), { timeout: 60_000 }).toBe(true);
      expect(await page.evaluate(() => getBranch())).toBe(pick);
      await expect(chip.locator('.gb-l')).toHaveText(pick);
      await page.waitForTimeout(800);
      await shot(page, `picked_${mode}_${scheme}`);
      // and back to ทุกสาขา the same way
      await chip.click();
      await expect(sheet.locator(`.phb-item[data-v="${pick}"]`)).toHaveAttribute('aria-checked', 'true');
      await sheet.locator('.phb-item[data-v=""]').click();
      await expect(chip.locator('.gb-l')).toHaveText('ทุกสาขา');
      expect(await page.evaluate(() => getBranch())).toBe('');
      expect(errors).toEqual([]);
      await ctx.close();
    });
  }
}

test('390 · the sheet closes on ✕ and Esc without changing the branch; the period sheet still opens', async ({ browser }) => {
  test.setTimeout(120_000);
  const { ctx, page, errors } = await open(browser, PHONE, { mode: 'fit' });
  const chip = page.locator('#gpBranchChip'), sheet = page.locator('#phBranchSheet');
  await chip.click(); await expect(sheet).toBeVisible();
  await sheet.locator('.php-x').click(); await expect(sheet).toBeHidden();
  await chip.click(); await expect(sheet).toBeVisible();
  await page.keyboard.press('Escape'); await expect(sheet).toBeHidden();
  expect(await page.evaluate(() => getBranch())).toBe('');
  await page.locator('#gpRangeChip').click();
  await expect(page.locator('#phPeriodSheet')).toBeVisible();
  await expect(sheet).toBeHidden();
  expect(errors).toEqual([]);
  await ctx.close();
});

test('390 · a pinned branch login gets no branch chip, no sheet and no branch select', async ({ browser }) => {
  test.setTimeout(120_000);
  const { ctx, page } = await open(browser, PHONE, { tab: '', who: 'branch' });
  await page.waitForFunction(() => !!window.AUTH && window.AUTH.role === 'branch', null, { timeout: 60_000 });
  await page.waitForTimeout(600);
  const f = await page.evaluate(() => {
    try { branchChipRender(); phBranchOpen(); } catch (e) { /* gone */ }
    return { chip: !!document.getElementById('gpBranchChip'), sheet: !!document.querySelector('#phBranchSheet:not([hidden])'),
      select: !!document.querySelector('select#selBranch') };
  });
  expect(f).toEqual({ chip: false, sheet: false, select: false });
  await ctx.close();
});

test('desktop · no branch chip; the top bar keeps its branch select', async ({ browser }) => {
  test.setTimeout(120_000);
  const { ctx, page, errors } = await open(browser, DESK);
  expect(await page.locator('#gpBranchChip:visible').count()).toBe(0);
  await expect(page.locator('select#selBranch')).toBeVisible();
  // the heat map's cut weeks: Wk40 starts before 1 Oct, Wk43 ends after the last day with data (19 Oct) - both kept out of the normal
  const cut = await page.evaluate(() => { const keep = window.__dataLastDate; window.__dataLastDate = '2026-10-19';
    const s = [...heatPartialWeeks(['2026-W40', '2026-W41', '2026-W42', '2026-W43'], { from: '2026-10-01', to: '2026-10-31' })];
    window.__dataLastDate = keep; return s; });
  expect(cut).toEqual([0, 3]);
  expect(errors).toEqual([]);
  await ctx.close();
});

// ── 2 · the heat map: red / green cells, legible labels, both readings, phone and desktop, light and dark ──
const HEAT = [['390 touch', PHONE, 'touch'], ['390 fit', PHONE, 'fit'], ['desktop', DESK, '']];
for (const [label, opts, mode] of HEAT) {
  for (const scheme of ['light', 'dark']) {
    test(`${label} ${scheme} · heat map is red → neutral → green in both readings; labels >= 4.5:1`, async ({ browser }) => {
      test.setTimeout(180_000);
      const { ctx, page, errors } = await open(browser, opts, { mode: mode || 'fit', scheme });
      if (LIVE) {                         // the server's own data: the weeks of the top bar's month (the owner's รายสัปดาห์)
        await page.locator('#heatmapGran').selectOption('weekly');
        await page.waitForFunction(() => !!document.querySelector('#heatmapTable th') && /Wk/.test(document.querySelector('#heatmapTable thead')?.textContent || ''), null, { timeout: 90_000 });
        await page.waitForTimeout(1500);
      }
      for (const hm of ['value', 'vsnorm']) {
        await page.evaluate(m => setHeatMode(m), hm);
        const cells = await page.evaluate(CELLS);
        const grid = cells.filter(x => !x.mom), mom = cells.filter(x => x.mom);
        if (!LIVE || hm === 'value') expect(grid.length).toBeGreaterThan(0);
        if (!LIVE) {
          expect(grid.some(x => x.red && x.step < 0), 'red cells below normal').toBe(true);
          expect(grid.some(x => x.green && x.step > 0), 'green cells above normal').toBe(true);
          expect(grid.some(x => x.step === 0 && !x.red && !x.green), 'neutral cells around normal').toBe(true);
          expect(mom.length).toBe(Object.keys(SHAPE).length);
          expect(mom.some(x => x.red) && mom.some(x => x.green), 'vs ก่อนหน้า on the same scale').toBe(true);
        }
        cells.forEach(x => { if (x.step < 0) expect(x.red, `${x.bg} step ${x.step}`).toBe(true); if (x.step > 0) expect(x.green, `${x.bg} step ${x.step}`).toBe(true); });
        const worst = cells.slice().sort((a, b) => a.k - b.k)[0] || { k: 99, fg: "", bg: "" };
        expect(worst.k, `${worst.fg} on ${worst.bg}`).toBeGreaterThanOrEqual(4.5);
        if (hm === 'value') expect(grid.every(x => !/%$/.test(x.txt))).toBe(true);       // the baht figure stays in the cell
        else expect(grid.every(x => /%$/.test(x.txt))).toBe(true);
        await expect(page.locator('#heatmapWrap .hrg-legend')).toContainText('ต่ำกว่าปกติ');
        await expect(page.locator('#heatmapWrap .hrg-legend')).toContainText('สูงกว่าปกติ');
        expect(await page.locator('#heatmapWrap .hrg-sw').count()).toBe(9);
        await shot(page, `heat_${label.replace(/ /g, '_')}_${scheme}_${hm}`, '.card:has(#heatmapWrap)');
      }
      expect(errors).toEqual([]);
      await ctx.close();
    });
  }
}
