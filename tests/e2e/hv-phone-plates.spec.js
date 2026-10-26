// @ts-check
// Owner 2026-10-25 (phone screenshot of High Value Product Monitor at 390 px, พอดีจอ):
//   1. "ทำให้เลือกช่วงเวลาได้ด้วยนะครับ" - on a phone the range chip by the title is ONE line ("1–19 ต.ค. 69 ▾") and opens a bottom sheet that
//      holds the top bar's own period control (#gPeriod moved in): changing the kind there reloads the page (HVM, ยอดขายรายวัน, ต้นทุน/หัว);
//      the HVM group chips (+ CSV) wrap into a 5-column grid, 5 + 5 (32 px drawn, 44 px to tap; owner 2026-10-25: "ไม่ต้องเลื่อน");
//   2. "เอาไปโชว์ในเมนูยอดขาย ต่อท้าย เมนูใหม่ แต่โชว์เป็นจำนวนจานที่ออก ไม่ต้องจำนวนเงิน และให้เลือกช่วงเวลาได้" - วัตถุดิบมูลค่าสูง · จำนวนจาน:
//      DV3 took it off the menu-spike card; DV4 made it the จำนวนจาน view of High Value Product Monitor: the 8 groups (+ ทั้งหมด), plates only,
//      on the top bar's period (or a week pair) and branch; desktop and 390 both modes, light / dark; marketing sees it (no toggle) and no baht.
// The answers are mocked so the cases exist whatever the fixture holds (HVP_LIVE=1 reads the server's own data instead).
const { test, expect } = require('@playwright/test');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const DESK = { viewport: { width: 1440, height: 900 } };
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];
const SHOTS = process.env.HVP_SHOTS || '';
const LIVE = process.env.HVP_LIVE === '1';
const GROUPS = [['salmon', 'แซลมอน'], ['tuna', 'ทูน่า'], ['hamachi', 'ฮามาจิ'], ['amaebi', 'กุ้งหวาน'], ['scallop', 'หอยเชลล์'], ['eel', 'ปลาไหล'], ['oyster', 'หอยนางรม'], ['beef', 'เนื้อ']];
const PERIOD = { kind: 'month', cur: ['2026-10-01', '2026-10-19'], prev: ['2026-09-01', '2026-09-19'], days: 19, complete: false,
  label_th: '1–19 ต.ค. 2569', prev_label_th: '1–19 ก.ย. 2569', anchor: '2026-10-19', has_prev: true, reasons: {} };

// ── mocked answers ──
function lmdi(p1, n1, p0, n0) {
  const L = Math.abs(p1 - p0) < 1e-9 ? p1 : (p1 - p0) / (Math.log(p1) - Math.log(p0));
  const c = L * Math.log(n1 / n0), h = L * Math.log((p1 / n1) / (p0 / n0));
  return { total: p1 - p0, total_pct: Math.round((p1 - p0) / p0 * 1000) / 10, customers: c, plates_per_head: h, main: Math.abs(c) >= Math.abs(h) ? 'customers' : 'plates_per_head' };
}
function platesRow(name, n1, n0, k) {
  const cells = {};
  GROUPS.forEach(([s], i) => {
    const h1 = (i ? 0.5 : 9) * k, h0 = (i ? 0.52 : 9.4), p1 = n1 * h1, p0 = n0 * h0;
    cells[s] = { plates: p1, plates_per_head: h1, prev: { plates: p0, plates_per_head: h0 },
      change: { plates: Math.round((p1 - p0) / p0 * 1000) / 10, plates_per_head: Math.round((h1 - h0) / h0 * 1000) / 10 }, cause: lmdi(p1, n1, p0, n0) };
  });
  return { branch: name, customers: n1, prev_customers: n0, change: { customers: Math.round((n1 - n0) / n0 * 1000) / 10 }, cells };
}
function platesAnswer() {
  return { period: PERIOD, base: { kind: 'prev', from: '2026-09-01', to: '2026-09-19', label_th: '1–19 ก.ย. 2569' },
    groups: GROUPS.map(([slug, label]) => ({ slug, label })), reasons: {},
    chain: Object.assign(platesRow('', 17659, 18000, 1.0), { branch: undefined }),
    branches: [platesRow('ขอนแก่น', 7598, 7000, 1.05), platesRow('บุรีรัมย์', 2817, 3300, 0.97), platesRow('นครปฐม', 3878, 3700, 1.1)] };
}
function spikeAnswer(url) {
  const u = new URL(url), q = k => u.searchParams.get(k);
  return { period: q('period') || 'day', date_from: '2026-10-01', date_to: '2026-10-19', date: '2026-10-19', latest_day: '2026-10-19',
    spikes: [{ branch: 'ขอนแก่น', menu: 'ปลาแซลมอนย่าง', today: 120, per_day: 6, expected: 60, excess: 60, x: 2.0, pct: 100, kind: 'buffet', basis: 'buffet',
      customers: 400, rate: 30, normal_rate: 15, tiers: null, tier_note: 'ไม่มีข้อมูลแยก tier' }], count: 1,
    new_menus: [{ branch: 'ขอนแก่น', menu: 'เมนูใหม่ 1', first_sold: '2026-10-15', first_sold_th: '15 ต.ค.', age_days: 5, qty: 40, per_day: 8, kind: 'alc', basis: 'all',
      customers: 300, rate: 13.3, trend: null }], new_count: 1, spikes_plates: [], plates_count: 0, plates_only_count: 0,
    base: { kind: 'normal', from: '2026-09-10', to: '2026-09-30' }, summary: { customers: 5000, customers_per_day: 263, base_customers_per_day: 250, customers_vs_base_pct: 5.2 },
    top: 15, cost_view: false };
}
function hvmAnswer() {
  const cell = () => ({ plates: 100, cost: 1500, uncosted_plates: 0, cost_per_head: 15, plates_per_head: 1, cost_per_plate: 15, share_of_food_cost: 30, prev: null, ly: null,
    change: { prev: {}, ly: {} }, cause: { prev: { total: null, reason: 'ไม่มีข้อมูลช่วงที่เทียบ' }, ly: { total: null } } });
  const row = (name, cust) => ({ branch: name, customers: cust, food_cost: cust * 400, food_cost_per_head: 400, prev_food_cost_per_head: null, ly_food_cost_per_head: null,
    food_change: { prev: null, ly: null }, cells: Object.fromEntries(GROUPS.map(([s]) => [s, cell()])) });
  return { period: PERIOD, groups: GROUPS.map(([slug, label]) => ({ slug, label })), chain: Object.assign(row('', 2000), { branch: undefined }),
    branches: [row('ขอนแก่น', 1000), row('บุรีรัมย์', 1000)], reasons: {} };
}

async function login(ctx, user) {
  const name = user === 'marketing' ? process.env.E2E_USER_MKT : process.env.E2E_USER;
  if (name && process.env.E2E_PASS) {
    const r = await ctx.request.post('/api/auth/login', { data: { username: name, password: process.env.E2E_PASS } });
    expect(r.ok()).toBe(true);
    return true;
  }
  return false;
}

async function open(browser, opts, { tab, mode = 'fit', scheme = 'light', user = '', view = 'baht' }) {
  const ctx = await browser.newContext({ ...opts, colorScheme: scheme });
  const real = await login(ctx, user);
  await ctx.addInitScript(([m, t, d, v]) => {
    try {
      localStorage.setItem('oshinei_phmode', JSON.stringify({ [t]: m })); localStorage.setItem('oshinei_dark', d ? '1' : '0');
      localStorage.setItem('oshinei_spike_period', JSON.stringify({ m: 'mtd', f: '', t: '' })); localStorage.setItem('oshinei_sphv_slug', 'salmon');
      localStorage.setItem('oshinei_hvm_slug', 'salmon'); localStorage.setItem('oshinei_gperiod', JSON.stringify({ kind: 'month', pick: '', from: '', to: '' }));
      localStorage.setItem('oshinei_hvm_view', v); localStorage.setItem('oshinei_hvp_weeks', JSON.stringify({ follow: 1 }));   // the plates view on the top bar's period
    } catch (e) { /* none */ }
  }, [mode, tab, scheme === 'dark', view]);
  if (user === 'marketing' && !real) {            // no marketing account on this server: the client sees the marketing role (the server's gate is in pytest)
    await ctx.route('**/api/auth/me', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ enabled: true, authenticated: true,
      username: 'mkt-e2e', role: 'marketing', display_name: 'การตลาด', names: {}, write_gated: false,
      modules: ['customer.buffet', 'customer.feedback', 'menu.analysis', 'report.export', 'sales.overview'] }) }));
  }
  const urls = [];
  const watch = (pat, body) => ctx.route(pat, r => { urls.push(r.request().url());
    return LIVE ? r.continue() : r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body(r.request().url())) }); });
  await watch(/\/api\/sales\/ingredient-groups-plates\?/, platesAnswer);
  await watch(/\/api\/sales\/menu-spike\?/, spikeAnswer);
  await watch(/\/api\/cost\/ingredient-groups\?/, hvmAnswer);
  const page = await ctx.newPage();
  page.on('request', r => { if (/\/api\/(sales\/daily|sales\/cost-per-head|cost\/ingredient-groups)\?/.test(r.url())) urls.push(r.url()); });
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
  if (tab === 'dailysales') await page.waitForFunction(() => !!document.querySelector('#dsSpike .sp-sec-old'), null, { timeout: 60_000 });   // DV3: the card, no plates section
  if (tab === 'hvmonitor') await page.waitForFunction(() => !!document.querySelector('#hvmTable, #hvmAll, #hvpTable, #hvpMatrix, #hvmCompact'), null, { timeout: 60_000 });
  // owner 2026-10-26: a phone opens on the compact list (hv-phone-compact.spec.js); these cases read the full list behind ดูทั้งหมด
  if (opts.isMobile && await page.locator('#hvmCompact').count()) { await page.locator('.hvc-all').click(); await page.waitForFunction(() => !!document.querySelector('#hvmTable, #hvmAll, #hvpTable, #hvpMatrix'), null, { timeout: 60_000 }); }
  await page.waitForFunction(() => { const c = document.getElementById('gpRangeChip'); return !!c && !c.hidden; }, null, { timeout: 60_000 }).catch(() => {});
  await page.waitForTimeout(900);
  return { ctx, page, errors, urls };
}

async function shot(page, name) {
  if (!SHOTS) return;
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: false });
}

// contrast of the figures' change lines and the cause lines (light / dark)
const WORST = sel => {
  const rgb = s => { const m = String(s).match(/color\(srgb ([\d.]+) ([\d.]+) ([\d.]+)/); if (m) return [+m[1] * 255, +m[2] * 255, +m[3] * 255];
    const n = String(s).match(/[\d.]+/g) || ['0', '0', '0', '0']; return [+n[0], +n[1], +n[2], n[3] == null ? 1 : +n[3]]; };
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const bgOf = el => { for (let e = el; e; e = e.parentElement) { const c = rgb(getComputedStyle(e).backgroundColor); if (c.length < 4 || c[3] >= 0.5) return c; } return [255, 255, 255]; };
  const r = [...document.querySelectorAll(sel)].filter(e => e.getClientRects().length).map(e => { const a = lum(rgb(getComputedStyle(e).color)), b = lum(bgOf(e));
    return { k: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05), d: `${e.className} ${getComputedStyle(e).color} on ${bgOf(e).join(',')}` }; });
  return r.sort((x, y) => x.k - y.k)[0] || { k: 99, d: '' };
};

// ── 1 · the period sheet from the range chip: HVM, ยอดขายรายวัน and one cost page ──
const SHEET = [
  ['hvmonitor', 'fit', ['สัปดาห์', 'เดือน', 'ปี', 'กำหนดเอง'], /\/api\/cost\/ingredient-groups\?.*kind=week/],
  ['dailysales', 'touch', ['วัน', 'สัปดาห์', 'เดือน', 'ปี'], /\/api\/sales\/daily\?.*gran=week/],
  ['costhead', 'fit', ['วัน', 'สัปดาห์', 'เดือน', 'ปี'], /\/api\/sales\/cost-per-head\?.*gran=week/],
];
for (const [tab, mode, kinds, want] of SHEET) {
  test(`390 · ${tab}: the range chip is one line and opens the period sheet; สัปดาห์ there reloads the page`, async ({ browser }) => {
    test.setTimeout(120_000);
    const { ctx, page, errors, urls } = await open(browser, PHONE, { tab, mode });
    const chip = page.locator('#gpRangeChip');
    await expect(chip).toBeVisible();
    const c = await chip.evaluate(el => { const r = el.getBoundingClientRect(), s = /** @type {HTMLElement} */ (el.querySelector('.gp-rc-s'));
      return { h: r.height, right: r.right, role: el.getAttribute('role'), short: s ? s.textContent : '', sh: s ? s.getBoundingClientRect().height : 0,
        fs: parseFloat(getComputedStyle(el).fontSize), full: getComputedStyle(/** @type {HTMLElement} */ (el.querySelector('.gp-rc-l'))).display }; });
    expect(c.h, 'one line, 28 px drawn').toBeLessThanOrEqual(30);
    expect(c.sh).toBeLessThanOrEqual(20);
    expect(c.right).toBeLessThanOrEqual(390);
    expect(c.role).toBe('button');
    expect(c.short).toMatch(/^\d.* \d\d$/);              // "1–19 ต.ค. 69": the year in two digits
    expect(c.full).toBe('none');
    expect(c.fs).toBeGreaterThanOrEqual(12);
    await chip.click();
    const sheet = page.locator('#phPeriodSheet');
    await expect(sheet).toBeVisible();
    await expect(sheet.locator('#gPeriod')).toHaveCount(1);                   // the top bar's own control, moved in
    expect(await page.locator('.topbar-scope #gPeriod').count()).toBe(0);
    const shown = await sheet.locator('.gp-kinds .gran-btn:visible').allTextContents();
    expect(shown.map(s => s.trim())).toEqual(kinds);
    await shot(page, `sheet_${tab}_${mode}`);
    const n0 = urls.length;
    await sheet.locator('.gp-kinds .gran-btn', { hasText: 'สัปดาห์' }).click();
    await expect.poll(() => urls.slice(n0).some(u => want.test(u)), { timeout: 30_000 }).toBe(true);
    expect(await page.evaluate(() => gPeriodGet().kind)).toBe('week');
    await expect(sheet.locator('.gp-kinds .gran-btn.gran-active')).toHaveText('สัปดาห์');
    await sheet.locator('.php-done').click();
    await expect(sheet).toBeHidden();
    expect(await page.locator('.topbar-scope #gPeriod').count()).toBe(1);   // back where it was
    expect(errors).toEqual([]);
    await ctx.close();
  });
}

test('desktop: the range chip stays the full span and opens nothing', async ({ browser }) => {
  test.setTimeout(90_000);
  const { ctx, page, errors } = await open(browser, DESK, { tab: 'hvmonitor' });
  const chip = page.locator('#gpRangeChip');
  await expect(chip).toBeVisible();
  await expect(chip.locator('.gp-rc-l')).toBeVisible();
  await expect(chip.locator('.gp-rc-s')).toBeHidden();
  expect(await chip.getAttribute('role')).toBeNull();
  await chip.click();
  expect(await page.locator('#phPeriodSheet:visible').count()).toBe(0);
  expect(errors).toEqual([]);
  await ctx.close();
});

// ── 1b · HVM group chips: a 5-column grid on a phone (5 + 5 with CSV), no sideways scroll ──
for (const mode of ['touch', 'fit']) {
  test(`390 ${mode} · HVM chips wrap (no scroll), compact, 44 px to tap`, async ({ browser }) => {
    test.setTimeout(90_000);
    const { ctx, page, errors } = await open(browser, PHONE, { tab: 'hvmonitor', mode });
    const f = await page.evaluate(() => {
      const bs = [...document.querySelectorAll('#hvmChips .gran-btn')];
      const rs = bs.map(b => b.getBoundingClientRect());
      const csv = document.querySelector('.hvm-csv').getBoundingClientRect();
      const b0 = bs[1], r = b0.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      const hit = dy => { const e = document.elementFromPoint(cx, cy + dy); return !!e && e.closest('.gran-btn') === b0; };
      return { n: bs.length, tops: [...new Set(rs.map(x => Math.round(x.top)))].length, h: Math.max(...rs.map(x => x.height)),
        fs: Math.min(...bs.map(b => parseFloat(getComputedStyle(b).fontSize))), csvH: csv.height, csvMid: csv.top + csv.height / 2,
        mid: (() => { const r5 = bs[5].getBoundingClientRect(); return r5.top + r5.height / 2; })(),
        tap: hit(-21) && hit(21), sw: document.documentElement.scrollWidth };
    });
    expect(f.n).toBe(9);
    expect(f.tops, 'two rows: 5 + 4 chips').toBe(2);
    expect(f.h, 'compact: 32 px drawn').toBeLessThanOrEqual(34);
    expect(f.fs).toBeGreaterThanOrEqual(12);
    expect(f.csvH).toBeLessThanOrEqual(34);
    expect(Math.abs(f.csvMid - f.mid), 'CSV closes the second row').toBeLessThanOrEqual(4);
    expect(f.tap, 'a chip is 44 px to tap').toBe(true);
    expect(f.sw).toBeLessThanOrEqual(390);
    // the "?" by the title: the compact ui-q (#466): <= 30 px drawn on screen, a tap 21 px off its centre still lands on it
    const qs = await page.evaluate(() => [...document.querySelectorAll('#tab-hvmonitor .hvm-head h2 .ui-q')].map(q => {
      const r = q.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      const hit = (dx, dy) => { const e = document.elementFromPoint(cx + dx, cy + dy); return !!e && e.closest('.ui-q') === q; };
      return { w: r.width, h: r.height, tap: hit(0, -21) && hit(0, 21) && hit(-21, 0) && hit(21, 0) }; }));
    expect(qs.length).toBe(2);
    for (const q of qs) { expect(q.w).toBeLessThanOrEqual(30); expect(q.h).toBeLessThanOrEqual(30); expect(q.tap, 'the "?" is 44 px to tap').toBe(true); }
    // the group pages' chip bar: one compact row that scrolls sideways
    const cb = await page.evaluate(() => { const bar = document.getElementById('clusterBar'), bs = [...bar.querySelectorAll('button')];
      return { rows: new Set(bs.map(b => Math.round(b.getBoundingClientRect().top))).size, h: Math.max(...bs.map(b => b.getBoundingClientRect().height)),
        cur: (bar.querySelector('[aria-current="page"]') || { textContent: '' }).textContent.trim() }; });
    expect(cb.rows, 'the cluster bar is one row').toBe(1);
    expect(cb.h).toBeLessThanOrEqual(34);
    expect(cb.cur).toBe('High Value Product Monitor');
    await shot(page, `hvm_head_${mode}`);
    expect(errors).toEqual([]);
    await ctx.close();
  });
}

// ── 2 · High Value Product Monitor · จำนวนจาน (DV4: moved here from the menu-spike card) ──
const CASES = [['desktop', DESK, 'fit'], ['390 แตะง่าย', PHONE, 'touch'], ['390 พอดีจอ', PHONE, 'fit']];
const NOBAHT = () => /฿|บาท/.test(['hvmBody', 'hvmAnswer'].map(i => (document.getElementById(i) || {}).textContent || '').join(' '));
for (const [name, opts, mode] of CASES) {
  for (const scheme of /** @type {const} */ (['light', 'dark'])) {
    test(`hvmonitor · จำนวนจาน · ${name} · ${scheme}: one group and ทั้งหมด, plates only, the top bar's period`, async ({ browser }) => {
      test.setTimeout(120_000);
      const { ctx, page, errors, urls } = await open(browser, opts, { tab: 'hvmonitor', mode, scheme, view: 'plates' });
      const tag = `${name.replace(/\s+/g, '_')}_${scheme}`;
      // the toggle: บาท | จำนวนจาน, จำนวนจาน on; the title and its help follow the view
      await expect(page.locator('#hvmView .gran-btn.gran-active')).toHaveText('จำนวนจาน');
      await expect(page.locator('#hvmTitle')).toHaveText('วัตถุดิบมูลค่าสูง · จำนวนจาน');
      expect(await page.locator('#hvmHelpQ').getAttribute('data-help')).toBe('hlp-hvmonitor-plates');
      // ตามแถบด้านบน: the section asks for the top bar's period (kind / pick), no week pair
      const hvU = new URL(urls.filter(u => u.includes('ingredient-groups-plates')).pop() || 'http://x/');
      expect(hvU.searchParams.get('kind')).toBe('month');
      expect(hvU.searchParams.get('base_from')).toBeNull();
      expect(urls.filter(u => /\/api\/cost\/ingredient-groups\?/.test(u)), 'the plates view reads no cost').toEqual([]);
      const chips = page.locator('#hvmChips .gran-btn');
      await expect(chips).toHaveCount(9);
      expect((await chips.first().boundingBox() || { height: 99 }).height).toBeLessThanOrEqual(34);
      if (opts.isMobile) {                                    // 5 + 5 with CSV (owner 2026-10-25: no sideways scroll), 44 px to tap; the toggle too
        const c = await page.evaluate(() => {
          const bs = [...document.querySelectorAll('#hvmChips .gran-btn')], b = bs[2];
          b.scrollIntoView({ block: 'center' });
          const hit = (el, dy) => { const r = el.getBoundingClientRect(), e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2 + dy);
            return !!e && e.closest('.gran-btn') === el ? '' : `${dy}:${e ? e.tagName + '.' + e.className : 'none'}`; };
          const v = document.querySelector('#hvmView .gran-btn');
          return { rows: new Set(bs.map(x => Math.round(x.getBoundingClientRect().top))).size, miss: [hit(b, -21), hit(b, 21), hit(v, -21), hit(v, 21)].filter(Boolean).join(' | '),
            sw: document.documentElement.scrollWidth };
        });
        expect(c.rows, 'two rows').toBe(2);
        if (mode === 'touch') expect(c.miss, 'a chip is 44 px to tap').toBe('');
        expect(c.sw).toBeLessThanOrEqual(390);
      }
      // the answer line in plates: the group, its plates and the two causes (a phone: the DV5 strip, the box hides)
      const ans = (await page.locator('#hvmAnswer').textContent() || '').replace(/\s+/g, ' ').trim();
      expect(ans).toMatch(/^แซลมอน [\d,]+ จาน \([▲▼]?\d+\.\d%\):\s?ลูกค้า [+−±][\d,]+ จาน · กิน [+−±][\d,]+ จาน/);
      const strip = await page.evaluate(() => [...document.querySelectorAll('.ph-ans[data-tab="hvmonitor"] .ph-ans-k')].map(k => k.textContent.trim()));
      if (opts.isMobile) {
        expect(strip).toEqual(['แซลมอน · จานที่ออก', 'กินต่อหัวเพิ่มมากสุด', 'จาน/หัว']);
        await expect(page.locator('#hvmAnswer')).toBeHidden();
      } else {
        expect(strip).toEqual([]);
        await expect(page.locator('#hvmAnswer')).toBeVisible();
      }
      if (mode === 'touch' && opts.isMobile) {
        await expect(page.locator('#tab-hvmonitor .ph-cl-card').first()).toBeVisible();
        const figs = await page.locator('#tab-hvmonitor .ph-cl-card').first().locator('.ph-cl-fl').allTextContents();
        expect(figs).toEqual(['จากกินต่อหัว', 'จาน/หัว', 'จานที่ออก']);
      } else {
        await expect(page.locator('#hvpTable')).toBeVisible();
      }
      const t = await page.evaluate(() => {
        const tb = /** @type {HTMLTableElement} */ (document.getElementById('hvpTable'));
        const first = tb.tBodies[0].rows[0], cause = first.cells[1];
        const eat = r => { const v = r.cells[1].querySelector('.hvm-v').textContent.trim(); const n = Number(v.replace(/[^\d.]/g, '')); return v.startsWith('−') ? -n : n; };
        const br = [...tb.tBodies[0].rows].slice(1).filter(r => !r.hidden).map(eat);
        return { heads: [...tb.tHead.rows[0].cells].map(c => c.textContent.trim()), chain: first.cells[0].textContent.trim(),
          lines: [...cause.querySelectorAll('[data-ph-line]')].map(e => e.textContent.trim()), sorted: br.every((v, i) => !i || br[i - 1] >= v) };
      });
      expect(t.heads).toEqual(['สาขา', 'จากกินต่อหัว', 'สะสม', 'จานที่ออก', 'จาน/หัว', 'ลูกค้า']);
      if (!LIVE) expect(t.chain).toBe('ทั้งเครือ');
      expect(t.lines[0], 'the plates from eating, column 2').toMatch(/^[+−±][\d,]+$/);
      expect(t.lines[1]).toMatch(/^ลูกค้า [+−±][\d,]+ · รวม [+−±][\d,]+/);
      expect(t.sorted, 'the branches by the plates from eating more').toBe(true);
      expect(await page.evaluate(NOBAHT), 'plates only').toBe(false);
      if (mode !== 'touch' || !opts.isMobile) {
        // text >= 12 px at 100 % (พอดีจอ shows the desktop layout zoomed; a double tap shows it at 100 %)
        const small = await page.evaluate(() => [...document.querySelectorAll('#hvpTable td, #hvpTable td *')]
          .filter(e => e.textContent.trim() && e.getClientRects().length).map(e => parseFloat(getComputedStyle(e).fontSize)).filter(px => px < 12).length);
        expect(small).toBe(0);
        const w = await page.evaluate(WORST, opts.isMobile ? '#hvpTable .hvm-d, #hvpTable .hvp-part, #hvpTable .hvm-v' : '#hvpTable .hvm-d, #hvpTable .hvp-part, #hvpTable .hvm-v, #hvmAnswer .hvm-part, #hvmAnswer .hvm-ans-s');
        expect(w.k, w.d).toBeGreaterThanOrEqual(4.5);
      }
      await shot(page, `hvp_salmon_${tag}`);
      // ทั้งหมด: branches x groups, plates / head with the change and the cause word
      await page.locator('#hvmChips .gran-btn[data-g="all"]').click();
      await page.waitForFunction(() => !!document.getElementById('hvpMatrix'));
      const mx = await page.evaluate(() => {
        const tb = /** @type {HTMLTableElement} */ (document.getElementById('hvpMatrix'));
        return { th: [...tb.tHead.rows[0].cells].map(c => c.textContent.replace(/\(จาน\/หัว\)/, '').trim()), words: tb.querySelectorAll('.hvp-cw').length };
      });
      expect(mx.th).toEqual(['สาขา', 'แซลมอน', 'ทูน่า', 'ฮามาจิ', 'กุ้งหวาน', 'หอยเชลล์', 'ปลาไหล', 'หอยนางรม', 'เนื้อ', 'ลูกค้า']);
      expect(mx.words).toBeGreaterThan(0);
      expect(await page.evaluate(NOBAHT)).toBe(false);
      if (mode !== 'touch' || !opts.isMobile) {
        const w = await page.evaluate(WORST, '#hvpMatrix .hvm-d, #hvpMatrix .hvp-cw, #hvpMatrix .hvm-v');
        expect(w.k, w.d).toBeGreaterThanOrEqual(4.5);
      }
      await page.waitForTimeout(400);
      await shot(page, `hvp_all_${tag}`);
      // the top bar's period moves the view (it follows the bar)
      const n0 = urls.length;
      await page.evaluate(() => gPeriodSet({ kind: 'week' }));
      await expect.poll(() => urls.slice(n0).some(u => u.includes('ingredient-groups-plates') && new URL(u).searchParams.get('kind') === 'week'), { timeout: 30_000 }).toBe(true);
      await page.locator('#hvmChips .gran-btn[data-g="salmon"]').click();
      await expect(page.locator('#hvmChips .gran-btn.gran-active')).toHaveText('แซลมอน');
      // back to บาท: the cost view and its answer line
      await page.locator('#hvmView .gran-btn[data-v="baht"]').click();
      await page.waitForFunction(() => !!document.getElementById('hvmTable'));
      await expect(page.locator('#hvmTitle')).toHaveText('ต้นทุนวัตถุดิบหลักต่อหัว · ทุกสาขา');
      expect(errors).toEqual([]);
      await ctx.close();
    });
  }
}

test('marketing: High Value Product Monitor opens on จำนวนจาน only - no toggle, no baht, no cost request', async ({ browser }) => {
  test.setTimeout(120_000);
  const { ctx, page, errors, urls } = await open(browser, DESK, { tab: 'hvmonitor', user: 'marketing', view: 'baht' });
  expect(await page.evaluate(() => window.AUTH && window.AUTH.role)).toBe('marketing');
  expect(await page.evaluate(() => tabAllowed('hvmonitor'))).toBe(true);
  await expect(page.locator('#hvpTable')).toBeVisible();
  await expect(page.locator('#hvmView')).toBeHidden();                        // a stored บาท never reaches a role without cost
  expect(await page.evaluate(NOBAHT)).toBe(false);
  expect(urls.filter(u => /\/api\/cost\//.test(u))).toEqual([]);
  await shot(page, 'hvp_marketing_desktop');
  expect(errors).toEqual([]);
  await ctx.close();
});
