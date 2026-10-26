// @ts-check
// Owner 2026-10-26 (phone, High Value Product Monitor, จำนวนจาน, ทุกสาขา, พอดีจอ, ~360-390 px): "หน้านี้ค้าง ทำให้ดูได้ในหน้าเดียวได้ไหม หรือจะเล็กไป".
// The freeze: the 28-row x 6-column table was laid out three times per fit (natural width, scaled, the table box), and at 360 px it was
// wider than the frame (778 > 720 px) - cut off at the edge and scrolling inside a nested box. On a phone (both modes) the page is now the
// answer strip and ONE compact list per ingredient: the chain row + the 5 largest increases + the 5 largest decreases, one line each
// (สาขา · the figure · the cause); a row opens its sheet; "ดูทั้งหมด (N)" opens every row (cards in แตะง่าย, the table in พอดีจอ - fitting the
// frame). The top bar is two rows, so "ทุกสาขา ▾" reads in full beside "⚠ ถึง …" and the period chip. Desktop unchanged.
// The answers are mocked (28 branches) so the cases exist whatever the fixture holds.
const { test, expect } = require('@playwright/test');

const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];
const SHOTS = process.env.HVC_SHOTS || '';
const GROUPS = [['salmon', 'แซลมอน'], ['tuna', 'ทูน่า'], ['hamachi', 'ฮามาจิ'], ['amaebi', 'กุ้งหวาน'], ['scallop', 'หอยเชลล์'], ['eel', 'ปลาไหล'], ['oyster', 'หอยนางรม'], ['beef', 'เนื้อ']];
const PERIOD = { kind: 'month', cur: ['2026-10-01', '2026-10-24'], prev: ['2026-09-01', '2026-09-24'], days: 24, complete: false,
  label_th: '1–24 ต.ค. 2569', prev_label_th: '1–24 ก.ย. 2569', anchor: '2026-10-24', has_prev: true, reasons: {} };
const NAMES = ['ระยอง', 'เพชรบูรณ์', 'โคราช1', 'ศรีสะเกษ', 'ชลบุรี', 'ขอนแก่น', 'บุรีรัมย์', 'นครปฐม', 'อุดรธานี', 'อุบลราชธานี', 'สุรินทร์', 'พิษณุโลก', 'ลำปาง', 'เชียงใหม่',
  'เชียงราย', 'นครสวรรค์', 'สระบุรี', 'ราชบุรี', 'หัวหิน', 'จันทบุรี', 'ตราด', 'ปราจีนบุรี', 'กาญจนบุรี', 'สกลนคร', 'มุกดาหาร', 'ร้อยเอ็ด', 'มหาสารคาม', 'ปลวกแดง'];
const N = NAMES.length;

function lmdi2(p1, n1, p0, n0) {
  const L = Math.abs(p1 - p0) < 1e-9 ? p1 : (p1 - p0) / (Math.log(p1) - Math.log(p0));
  const c = L * Math.log(n1 / n0), h = L * Math.log((p1 / n1) / (p0 / n0));
  return { total: p1 - p0, total_pct: Math.round((p1 - p0) / p0 * 1000) / 10, customers: c, plates_per_head: h, main: Math.abs(c) >= Math.abs(h) ? 'customers' : 'plates_per_head' };
}
// branch i: customers and plates per head move both ways, so there are increases and decreases
const mv = i => ({ n1: 3000 + i * 97, n0: 3000 + i * 97 + (i % 3 - 1) * 180, h1: 9 + (i % 7) * 0.11, h0: 9 + ((i + 3) % 7) * 0.11 });
function platesRow(name, i) {
  const { n1, n0, h1, h0 } = mv(i), cells = {};
  GROUPS.forEach(([s], g) => { const k = g ? 0.1 : 1, p1 = n1 * h1 * k, p0 = n0 * h0 * k;
    cells[s] = { plates: p1, plates_per_head: h1 * k, prev: { plates: p0, plates_per_head: h0 * k },
      change: { plates: Math.round((p1 - p0) / p0 * 1000) / 10, plates_per_head: Math.round((h1 - h0) / h0 * 1000) / 10 }, cause: lmdi2(p1, n1, p0, n0) }; });
  return { branch: name, customers: n1, prev_customers: n0, change: { customers: Math.round((n1 - n0) / n0 * 1000) / 10 }, cells };
}
function platesAnswer() {
  const brs = NAMES.map(platesRow), chain = platesRow('', 40);
  delete chain.branch;
  return { period: PERIOD, base: { kind: 'prev', from: '2026-09-01', to: '2026-09-24', label_th: '1–24 ก.ย. 2569' }, latest_day: '2026-10-24',
    groups: GROUPS.map(([slug, label]) => ({ slug, label })), reasons: {}, chain, branches: brs };
}
function bahtRow(name, i) {
  const { n1, n0, h1, h0 } = mv(i), cells = {};
  GROUPS.forEach(([s], g) => {
    const k = g ? 0.1 : 1, c1 = 30 + (i % 5), c0 = 30 + ((i + 2) % 5), pl1 = n1 * h1 * k, pl0 = n0 * h0 * k, cost1 = pl1 * c1, cost0 = pl0 * c0;
    const L = (cost1 - cost0) / (Math.log(cost1) - Math.log(cost0) || 1), cu = L * Math.log(n1 / n0), ph = L * Math.log((h1) / (h0)), cp = L * Math.log(c1 / c0);
    const main = [['customers', cu], ['plates_per_head', ph], ['cost_per_plate', cp]].sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))[0][0];
    const pct = (a, b) => Math.round((a - b) / b * 1000) / 10;
    cells[s] = { plates: pl1, cost: cost1, uncosted_plates: 0, cost_per_head: cost1 / n1, plates_per_head: h1 * k, cost_per_plate: c1, share_of_food_cost: 30 + i % 4,
      prev: { plates: pl0, cost: cost0, cost_per_head: cost0 / n0, plates_per_head: h0 * k, cost_per_plate: c0, share_of_food_cost: 30, uncosted_plates: 0 }, ly: null,
      change: { prev: { cost_per_head: pct(cost1 / n1, cost0 / n0), plates_per_head: pct(h1, h0), cost_per_plate: pct(c1, c0) }, ly: {} },
      cause: { prev: { total: cost1 - cost0, total_pct: pct(cost1, cost0), customers: cu, plates_per_head: ph, cost_per_plate: cp, main }, ly: { total: null } } };
  });
  return { branch: name, customers: n1, prev_customers: n0, food_cost: n1 * 400, food_cost_per_head: 400, prev_food_cost_per_head: 395, ly_food_cost_per_head: null,
    food_change: { prev: 1.3, ly: null }, cells };
}
function bahtAnswer() {
  const chain = bahtRow('', 40); delete chain.branch;
  return { period: PERIOD, groups: GROUPS.map(([slug, label]) => ({ slug, label })), chain, branches: NAMES.map(bahtRow), reasons: {} };
}

// the page has settled (phone-modes.spec.js, #483): the พอดีจอ frame has followed the last thing the page drew (data-ph-fit="ok") and nothing
// on the page has changed for `quiet` ms - no fixed sleep
async function settle(page, quiet = 400) {
  await page.evaluate(() => { const w = /** @type {any} */ (window); if (w.__e2eMut) return; w.__e2eMut = performance.now();
    new MutationObserver(() => { w.__e2eMut = performance.now(); }).observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true }); });
  await expect.poll(() => page.evaluate(q => { const f = /** @type {HTMLElement|null} */ (document.querySelector('.ph-frame'));
    return (!f || f.dataset.phFit === 'ok') && performance.now() - /** @type {any} */ (window).__e2eMut >= q; }, quiet),
  { message: 'the page settles', timeout: 30_000, intervals: [100] }).toBe(true);
}

async function login(ctx) {
  if (process.env.E2E_USER && process.env.E2E_PASS) {
    const r = await ctx.request.post('/api/auth/login', { data: { username: process.env.E2E_USER, password: process.env.E2E_PASS } });
    expect(r.ok()).toBe(true);
  }
}
async function open(browser, { width = 390, mode = 'fit', view = 'plates', desk = false, slug = 'salmon' }) {
  const ctx = await browser.newContext(desk ? { viewport: { width: 1440, height: 900 } }
    : { viewport: { width, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  await login(ctx);
  await ctx.addInitScript(([m, v, s]) => {
    try {
      localStorage.setItem('oshinei_phmode', JSON.stringify({ hvmonitor: m })); localStorage.setItem('oshinei_dark', '0');
      localStorage.setItem('oshinei_hvm_slug', s); localStorage.setItem('oshinei_hvm_view', v);
      localStorage.setItem('oshinei_gperiod', JSON.stringify({ kind: 'month', pick: '', from: '', to: '' })); localStorage.setItem('oshinei_hvp_weeks', JSON.stringify({ follow: 1 }));
    } catch (e) { /* none */ }
    // @ts-ignore
    window.__lt = [];
    // @ts-ignore
    try { new PerformanceObserver(l => l.getEntries().forEach(e => window.__lt.push(Math.round(e.duration)))).observe({ type: 'longtask', buffered: true }); } catch (e) { /* none */ }
  }, [mode, view, slug]);
  await ctx.route(/\/api\/sales\/ingredient-groups-plates\?/, r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(platesAnswer()) }));
  await ctx.route(/\/api\/cost\/ingredient-groups\?/, r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(bahtAnswer()) }));
  await ctx.route(/\/api\/cost\/ingredient-groups\/menus\?/, r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ menus: [] }) }));
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' && !IGNORE.some(re => re.test(m.text()))) errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  await page.goto('/', { waitUntil: 'networkidle' });
  if (desk) {
    await page.waitForFunction(() => typeof switchTab === 'function', null, { timeout: 60_000 });
    await page.evaluate(() => switchTab('hvmonitor'));
  } else {
    await page.waitForFunction(() => document.body.classList.contains('ph-on') && typeof phOpenPage === 'function', null, { timeout: 60_000 });
    await page.evaluate(async () => { await phOpenPage(() => switchTab('hvmonitor')); });
  }
  await page.waitForFunction(() => !!document.querySelector('#hvmCompact, #hvmTable, #hvpTable'), null, { timeout: 60_000 });
  await settle(page);
  return { ctx, page, errors };
}
// every visible cell / row of the HVM panel ends inside the frame (the panel) and the screen; no table scrolls sideways in its box
const CUT = () => {
  const panel = /** @type {HTMLElement} */ (document.getElementById('tab-hvmonitor')), pr = panel.getBoundingClientRect(), lim = Math.min(pr.right, window.innerWidth) + 1;
  const els = [...panel.querySelectorAll('td, th, .hvc-r, .ph-cl-card')].filter(e => e.getClientRects().length && !e.closest('[hidden]'));
  const tbs = [...panel.querySelectorAll('table')].filter(t => t.getClientRects().length && !t.closest('.ph-cl-src'));
  return { cut: els.filter(e => e.getBoundingClientRect().right > lim).length, n: els.length,
    wide: tbs.filter(t => t.parentElement && t.scrollWidth > t.parentElement.clientWidth + 1).length, xs: panel.querySelectorAll('.ph-xs').length,
    sw: document.documentElement.scrollWidth };
};

for (const width of [390, 360]) {
  for (const mode of ['touch', 'fit']) {
    for (const view of ['plates', 'baht']) {
      test(`${width} ${mode} ${view} · the compact list: chain + 5 up + 5 down, one line each; sheet; ดูทั้งหมด shows every row inside the frame`, async ({ browser }) => {
        test.setTimeout(150_000);
        const { ctx, page, errors } = await open(browser, { width, mode, view });
        const list = page.locator('#hvmCompact');
        await expect(list).toBeVisible();
        const f = await page.evaluate(() => {
          const rs = [...document.querySelectorAll('#hvmCompact .hvc-r')];
          const oneLine = rs.every(r => [...r.children].every(c => { const e = /** @type {HTMLElement} */ (c); const lh = parseFloat(getComputedStyle(e).lineHeight) || parseFloat(getComputedStyle(e).fontSize) * 1.6;
            return e.getBoundingClientRect().height <= lh * 1.5 + 1; }));
          const hs = rs.map(r => r.getBoundingClientRect().height);
          return { n: rs.length, chain: document.querySelectorAll('#hvmCompact .hvc-chain').length, first: rs[0] && rs[0].classList.contains('hvc-chain'), oneLine,
            minH: Math.min(...hs), maxH: Math.max(...hs), secs: [...document.querySelectorAll('#hvmCompact .hvc-sec')].map(s => s.textContent),
            fs: Math.min(...[...document.querySelectorAll('#hvmCompact .hvc-r > *')].map(e => { const r = e.getBoundingClientRect(); return parseFloat(getComputedStyle(e).fontSize) * (r.height ? 1 : 0) || 99; })),
            more: (document.querySelector('.hvc-all') || {}).textContent || '', tables: document.querySelectorAll('#hvmBody table').length,
            bottom: Math.round(document.getElementById('hvmCompact').getBoundingClientRect().bottom + (document.querySelector('.content-area') || { scrollTop: 0 }).scrollTop) };
        });
        expect(f.chain).toBe(1);
        expect(f.first).toBe(true);
        expect(f.n).toBe(11);                                    // 1 chain + 5 increases + 5 decreases (28 branches move both ways)
        expect(f.secs).toEqual(['เพิ่มขึ้นมากสุด', 'ลดลงมากสุด']);
        expect(f.oneLine, 'every row one line').toBe(true);
        expect(f.minH).toBeGreaterThanOrEqual(43);              // 44 px to tap, drawn at the touch size in both modes
        expect(f.maxH).toBeLessThanOrEqual(56);
        expect(f.more).toBe(`ดูทั้งหมด (${N})`);
        expect(f.tables, 'no table on the first screen').toBe(0);
        expect(f.bottom, 'the strip + the list within ~1.5 screens').toBeLessThanOrEqual(Math.round(844 * 1.6));
        const c0 = await page.evaluate(CUT);
        expect(c0.cut).toBe(0);
        expect(c0.sw).toBeLessThanOrEqual(width);
        if (SHOTS) await page.screenshot({ path: `${SHOTS}/compact_${width}_${mode}_${view}.png`, fullPage: true });
        // a row's sheet: every column of the table
        await page.locator('#hvmCompact .hvc-r:not(.hvc-chain)').first().click();
        const sheet = page.locator('#uiHelpDlg');
        await expect(sheet).toBeVisible();
        const want = view === 'plates' ? ['จานที่ออก', 'จาน/หัว', 'ลูกค้า', 'สาเหตุหลัก', 'จากกินต่อหัว', 'จากลูกค้า', 'สะสม']
          : ['ต้นทุน/หัว', 'จาน/หัว', 'ต้นทุน/จาน', 'สัดส่วนในต้นทุนอาหาร', 'สาเหตุหลัก', 'สะสม'];
        const txt = await sheet.textContent();
        for (const w of want) expect(txt, w).toContain(w);
        await page.keyboard.press('Escape');
        await expect(sheet).toBeHidden();
        // ดูทั้งหมด: every row - cards in แตะง่าย, the table in พอดีจอ (inside the frame, nothing cut off)
        await page.locator('.hvc-all').click();
        await expect(page.locator('#hvmCompact')).toHaveCount(0);
        await settle(page, 600);                                // the scroll to the list waits for the frame's re-fit
        if (mode === 'touch') {
          await expect.poll(() => page.locator('#tab-hvmonitor .ph-cl-card').count(), { timeout: 15_000 }).toBeGreaterThanOrEqual(N);
        } else {
          const id = view === 'plates' ? '#hvpTable' : '#hvmTable';
          await expect(page.locator(id)).toBeVisible();
          expect(await page.locator(`${id} tbody tr:not([hidden])`).count()).toBe(N + 1);
          const c1 = await page.evaluate(CUT);
          expect(c1.cut, 'no column cut off at the frame edge').toBe(0);
          expect(c1.wide, 'the table fits its box').toBe(0);
          expect(c1.xs, 'no nested scroll box').toBe(0);
          if (SHOTS) await page.screenshot({ path: `${SHOTS}/full_${width}_${mode}_${view}.png`, fullPage: true });
        }
        await page.locator('.hvc-back').click();
        await expect(page.locator('#hvmCompact')).toBeVisible();
        expect(errors).toEqual([]);
        await ctx.close();
      });
    }
  }
  test(`${width} · the top bar: "ทุกสาขา ▾" reads in full beside the period chip and "⚠ ถึง …"`, async ({ browser }) => {
    test.setTimeout(90_000);
    const { ctx, page, errors } = await open(browser, { width, mode: 'fit' });
    await page.evaluate(() => { const n = document.getElementById('dataCoverageNote'); if (n && n.style.display === 'none') { n.textContent = '⚠ ถึง 24 ต.ค.'; n.style.display = 'inline-flex'; } });
    await page.waitForTimeout(200);
    const g = await page.evaluate(() => {
      const r = s => { const e = document.querySelector(s); const b = e ? e.getBoundingClientRect() : null; return b && b.width ? { t: Math.round(b.top), b: Math.round(b.bottom), l: b.left, r: b.right } : null; };
      const l = /** @type {HTMLElement} */ (document.querySelector('#gpBranchChip .gb-l'));
      return { l: l ? { sw: l.scrollWidth, cw: l.clientWidth, txt: l.textContent } : null, range: r('#gpRangeChip'), branch: r('#gpBranchChip'), note: r('#dataCoverageNote'),
        title: r('#pageTitle'), av: r('#phTbAv'), bar: r('.top-bar') };
    });
    expect(g.l && g.l.txt).toBe('ทุกสาขา');
    expect(g.l.sw, 'no ellipsis').toBeLessThanOrEqual(g.l.cw);
    expect(g.range.t, 'the two chips on one row (#472)').toBe(g.branch.t);
    expect(g.branch.r).toBeLessThanOrEqual(width);
    expect(g.note.t < g.range.b && g.note.b > g.range.t, 'the note rides the chips row').toBe(true);
    expect(g.title.b).toBeLessThanOrEqual(g.range.t);          // two rows: the title (with the tools), then the chips
    expect(g.range.t - g.av.b, 'the tools and the chips 44 px tap areas apart').toBeGreaterThanOrEqual(8);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/head_${width}.png`, clip: { x: 0, y: 0, width, height: 140 } });
    expect(errors).toEqual([]);
    await ctx.close();
  });
}

// the long-task budget, after the page settles: a chip tap (another ingredient) never blocks the main thread for more than 200 ms (before:
// 230-490 ms on 4x CPU, the full table re-fitted). ดูทั้งหมด / ย่อรายการ bring the full 29-row table in and out of the frame: the frame lays it
// out at its natural width and again scaled (the phone shell's fit, about 550 ms on 4x CPU for this table) - asked for, not on the way in, so
// their budget is 800 ms. Locally on 4x CPU throttle (a mid-range phone); on CI (shared runners, their speed varies) without throttle.
for (const view of ['plates', 'baht']) {
  test(`390 fit ${view} · no task over 200 ms after load (chip tap, ดูทั้งหมด, ย่อรายการ)`, async ({ browser }) => {
    test.setTimeout(150_000);
    const { ctx, page, errors } = await open(browser, { width: 390, mode: 'fit', view });
    const cdp = await ctx.newCDPSession(page);
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: process.env.CI ? 1 : 4 });
    const worst = [];
    for (const step of [() => page.locator('#hvmChips [data-g="tuna"]').click(), () => page.locator('#hvmChips [data-g="salmon"]').click(),
      () => page.locator('.hvc-all').click(), () => page.locator('.hvc-back').click()]) {
      await page.evaluate(() => { /* @ts-ignore */ window.__lt = []; });
      await step();
      await settle(page, 600);
      worst.push(await page.evaluate(() => Math.max(0, /* @ts-ignore */ ...window.__lt)));
    }
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
    console.log(`longest task per step (${view}):`, worst.join(' / '), 'ms');
    expect(Math.max(worst[0], worst[1]), 'a chip tap').toBeLessThanOrEqual(200);
    expect(Math.max(worst[2], worst[3]), 'ดูทั้งหมด / ย่อรายการ').toBeLessThanOrEqual(800);
    expect(errors).toEqual([]);
    await ctx.close();
  });
}

test('desktop · unchanged: the table, no compact list', async ({ browser }) => {
  test.setTimeout(90_000);
  for (const view of ['plates', 'baht']) {
    const { ctx, page, errors } = await open(browser, { desk: true, view });
    await expect(page.locator(view === 'plates' ? '#hvpTable' : '#hvmTable')).toBeVisible();
    expect(await page.locator('#hvmCompact').count()).toBe(0);
    expect(await page.locator('.hvc-back').count()).toBe(0);
    expect(errors).toEqual([]);
    await ctx.close();
  }
});

test('390 touch · ทั้งหมด is one line per ingredient; a tap opens that ingredient', async ({ browser }) => {
  test.setTimeout(90_000);
  const { ctx, page, errors } = await open(browser, { width: 390, mode: 'touch', view: 'baht', slug: 'all' });
  await expect(page.locator('#hvmCompact .hvc-r')).toHaveCount(8);
  await expect(page.locator('.hvc-all')).toHaveText('ดูทั้งหมด (8)');
  await page.locator('#hvmCompact .hvc-r', { hasText: 'ทูน่า' }).click();
  await expect.poll(() => page.evaluate(() => _hvm.slug)).toBe('tuna');
  await expect(page.locator('#hvmCompact .hvc-chain')).toHaveCount(1);
  expect(errors).toEqual([]);
  await ctx.close();
});
