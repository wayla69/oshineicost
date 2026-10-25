// @ts-check
// DV5 (phone table review 2026-10-25) · the answer on the first screen, on a 390 x 844 phone:
//   * an analytics page shows the ANSWER STRIP (up to three lines): in แตะง่าย above its first card list, in พอดีจอ directly above the
//     scaled frame (outside it, touch size); the desktop shows none;
//   * the strip follows the top bar: a period change and a branch change reload the page with the new params and the strip is redrawn
//     for them (a push counts only for the filters it was made for);
//   * a ranked card list shows 10 cards (or the 5 first + the 5 last of a two-sided list) and "ดูทั้งหมด (N)";
//   * รายงานผู้บริหาร opens as the strip + เปิด PDF + สรุปผู้บริหาร; the printed report shows on demand and always in print;
//   * the answer tables come first: ตรวจสอบการสั่ง (ของหมดก่อนรอบถัดไป), เป้าลดต้นทุน (วัตถุดิบที่ควรโจมตีก่อน), Top 60 (จัดอันดับสาขา);
//     เทียบเมนู A/B is one table (ต่อหัว by default, ranked by the baht the cost differs).
const { test, expect } = require('@playwright/test');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];

async function login(page) {
  // The CI fixture runs with auth off. A local run with auth on passes a TEST account through the environment.
  if (process.env.E2E_USER && process.env.E2E_PASS) {
    await page.goto('/');
    await page.evaluate(async ([u, p]) => (await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: u, password: p }) })).status,
      [process.env.E2E_USER, process.env.E2E_PASS]);
  }
}
async function phoneShell(browser) {
  const ctx = await browser.newContext(PHONE);
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' && !IGNORE.some(re => re.test(m.text()))) errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  await login(page);
  await page.goto('/', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => document.body.classList.contains('ph-on') && document.querySelectorAll('#phNav .ph-nav-i').length > 0, null, { timeout: 60_000 });
  return { ctx, page, errors };
}
async function openPage(page, p, mode) {
  await page.evaluate(async ([x, mode]) => {
    const k = x.tab + (x.pane ? '|' + x.pane : ''), m = {}; m[k] = mode;
    try { localStorage.setItem('oshinei_phmode', JSON.stringify(m)); } catch (e) { /* none */ }
    await phOpenPage(() => x.pane ? supplyGo({ tab: x.tab, pane: x.pane }) : switchTab(x.tab));
  }, [p, mode]);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(1500);
}
const strip = (page, tab) => page.evaluate((t) => {
  const el = document.querySelector(`#tab-${t} > .ph-ans`);
  if (!el || !el.getClientRects().length) return null;
  const items = [...el.querySelectorAll('.ph-ans-i')];
  const first = document.querySelector(`#tab-${t} .ph-cl, #tab-${t} table`);
  let min = 99;
  el.querySelectorAll('*').forEach(e => { if ([...e.childNodes].some(x => x.nodeType === 3 && x.textContent.trim())) min = Math.min(min, parseFloat(getComputedStyle(e).fontSize)); });
  return { n: items.length, text: el.innerText, tones: items.map(i => i.getAttribute('data-tone')),
    hitH: Math.min(...items.map(i => i.querySelector('.ph-ans-hit').getBoundingClientRect().height)), min,
    above: !first || el.getBoundingClientRect().top <= first.getBoundingClientRect().top, top: el.getBoundingClientRect().top,
    doc: document.documentElement.scrollWidth, vw: innerWidth };
}, tab);

test('ยอดขายรายวัน: the answer strip in แตะง่าย follows the period and the branch; in พอดีจอ it sits above the frame', async ({ browser }) => {
  test.setTimeout(180_000);
  const { ctx, page, errors } = await phoneShell(browser);
  await openPage(page, { tab: 'dailysales' }, 'touch');
  await page.waitForFunction(() => !!document.querySelector('#tab-dailysales > .ph-ans .ph-ans-i'), null, { timeout: 60_000 });
  const s = await strip(page, 'dailysales');
  expect(s).not.toBeNull();
  expect(s.n).toBeGreaterThanOrEqual(1);
  expect(s.n).toBeLessThanOrEqual(3);
  expect(s.text).toContain('฿');
  expect(s.above, 'the strip sits above the first list').toBe(true);
  expect(s.top, 'the strip is on the first screen').toBeLessThan(844);
  expect(s.hitH).toBeGreaterThanOrEqual(44);
  expect(s.min).toBeGreaterThanOrEqual(12);
  expect(s.doc).toBeLessThanOrEqual(s.vw);

  // the period: a week reloads /api/sales/daily with gran=week and the strip is the week's
  const wk = page.waitForRequest(r => r.url().includes('/api/sales/daily') && r.url().includes('gran=week'), { timeout: 60_000 });
  await page.evaluate(() => gPeriodSet({ kind: 'week', pick: '' }));
  await wk;
  await page.waitForFunction(() => { const a = _pageAns.dailysales; return !!a && a.fk === ansFilterKey() && !!document.querySelector('#tab-dailysales > .ph-ans .ph-ans-i'); }, null, { timeout: 60_000 });

  // the branch: one branch reloads with branch=<it>; the strip is that branch's (the branch line, no ranking of branches)
  const br = await page.evaluate(() => [...document.getElementById('selBranch').options].map(o => o.value).filter(Boolean)[0]);
  expect(br, 'the fixture has a branch').toBeTruthy();
  const bq = page.waitForRequest(r => r.url().includes('/api/sales/daily') && decodeURIComponent(r.url()).includes('branch=' + br), { timeout: 60_000 });
  await page.evaluate((b) => phBranchPick(b), br);
  await bq;
  await page.waitForFunction(() => { const a = _pageAns.dailysales; return !!a && a.fk === ansFilterKey() && /ยอดขายของสาขา/.test((document.querySelector('#tab-dailysales > .ph-ans') || {}).textContent || ''); }, null, { timeout: 60_000 });
  // a push made for other filters is not shown
  await page.evaluate(() => { _pageAns.dailysales.fk = 'stale'; phAnswerApply(); });
  expect(await strip(page, 'dailysales')).toBeNull();
  await page.evaluate(() => { phBranchPick(''); });
  await page.waitForFunction(() => { const a = _pageAns.dailysales; return !!a && a.fk === ansFilterKey(); }, null, { timeout: 60_000 });

  // พอดีจอ: the strip sits directly above the scaled frame, outside it, at the touch size (>= 12 px text, 44 px taps), not over the frame
  await page.evaluate(() => phModeToggle());
  await page.waitForFunction(() => document.body.classList.contains('ph-fit'), null, { timeout: 30_000 });
  await page.waitForFunction(() => { const p = document.getElementById('tab-dailysales'), s = p && p.previousElementSibling;
    return !!s && s.matches('.ph-ans') && !!s.querySelector('.ph-ans-i'); }, null, { timeout: 60_000 });
  const f = await page.evaluate(() => {
    const p = document.getElementById('tab-dailysales'), s = /** @type {HTMLElement} */ (p.previousElementSibling);
    const zoomOf = e => { let z = 1; for (let q = e; q && q.nodeType === 1; q = q.parentElement) { const v = parseFloat(getComputedStyle(q).zoom); if (v && isFinite(v)) z *= v; } return z; };
    let min = 99;
    s.querySelectorAll('*').forEach(e => { if ([...e.childNodes].some(x => x.nodeType === 3 && x.textContent.trim())) min = Math.min(min, parseFloat(getComputedStyle(e).fontSize) * zoomOf(e)); });
    return { frame: p.classList.contains('ph-frame'), inside: p.contains(s), n: document.querySelectorAll('.ph-ans').length, min,
      hitH: Math.min(...[...s.querySelectorAll('.ph-ans-hit')].map(h => h.getBoundingClientRect().height)),
      gap: p.getBoundingClientRect().top - s.getBoundingClientRect().bottom, top: s.getBoundingClientRect().top,
      doc: document.documentElement.scrollWidth, vw: innerWidth };
  });
  expect(f.frame).toBe(true);
  expect(f.inside, 'the strip is outside the scaled frame').toBe(false);
  expect(f.n, 'one strip per page').toBe(1);
  expect(f.min, 'strip text is at least 12 px in fit').toBeGreaterThanOrEqual(12);
  expect(f.hitH).toBeGreaterThanOrEqual(44);
  expect(f.gap, 'the strip does not overlap the frame').toBeGreaterThanOrEqual(0);
  expect(f.top).toBeLessThan(844);
  expect(f.doc).toBeLessThanOrEqual(f.vw);
  // the cards stay touch-only: fit keeps the table
  expect(await page.evaluate(() => document.querySelectorAll('#tab-dailysales .ph-cl').length)).toBe(0);
  // a provider page in fit (no cards drawn) still answers from its table: บิลยกเลิก
  await openPage(page, { tab: 'cancelbill' }, 'fit');
  await page.waitForFunction(() => { const p = document.getElementById('tab-cancelbill'), s = p && p.previousElementSibling;
    return document.body.classList.contains('ph-fit') && !!s && s.matches('.ph-ans') && document.querySelectorAll('.ph-ans').length === 1; }, null, { timeout: 60_000 });
  expect(errors, errors.join('\n')).toEqual([]);
  await ctx.close();
});

test('the desktop has no answer strip', async ({ browser }) => {
  test.setTimeout(120_000);
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await login(page);
  await page.goto('/', { waitUntil: 'networkidle' });
  await page.evaluate(() => switchTab('dailysales'));
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(1500);
  expect(await page.evaluate(() => [...document.querySelectorAll('.ph-ans')].filter(e => e.getClientRects().length).length)).toBe(0);
  await ctx.close();
});

test('a ranked card list: 10 cards or 5 + 5, then ดูทั้งหมด (N); a to-do list is not capped', async ({ browser }) => {
  const { ctx, page } = await phoneShell(browser);
  await openPage(page, { tab: 'gpbranch' }, 'touch');
  const r = await page.evaluate(() => {
    const rows = Array.from({ length: 25 }, (_, i) => ({ i, title: 'สาขา ' + (i + 1), sub: '', figs: [{ l: 'บาท', v: String(100 - i), n: 100 - i }] }));
    const box = document.createElement('div');
    const run = (extra) => { box.innerHTML = phCardList(Object.assign({ id: 't', rows, keys: ['บาท'] }, extra)); return {
      cards: box.querySelectorAll('.ph-cl-card').length, gap: (box.querySelector('.ph-cl-gap') || {}).textContent || '',
      btn: (box.querySelector('.ph-cl-all') || {}).textContent || '', last: [...box.querySelectorAll('.ph-cl-t')].map(t => t.textContent).pop() }; };
    return { top: run({ cap: { top: 10, bottom: 0 } }), pair: run({ cap: { top: 5, bottom: 5 } }), open: run({ cap: { top: 10, bottom: 0 }, open: true }), none: run({}),
      todo: _phClCap({ tab: 'gpbranch' }, { sel: '#x' }, { hasDone: true }), later: _phClCap({ tab: 'hvmonitor' }, { sel: '#hvmTable' }, {}),
      spike: _phClCap({ tab: 'dailysales' }, { sel: '#spikeTable.sp-costed' }, {}), def: _phClCap({ tab: 'gpbranch' }, { sel: '#gpbTable' }, {}),
      managed: _phClCap({ tab: 'dailysales' }, { sel: 'table:has(#dsBody)' }, { managed: true }) };
  });
  expect(r.top.cards).toBe(10);
  expect(r.top.btn).toContain('ดูทั้งหมด (25)');
  expect(r.pair.cards).toBe(10);
  expect(r.pair.gap).toContain('อีก 15 รายการ');
  expect(r.pair.last).toBe('สาขา 25');
  expect(r.open.cards).toBe(25);
  expect(r.open.btn).toBe('ย่อ');
  expect(r.none.cards).toBe(25);
  expect(r.todo).toBeNull();
  expect(r.later).toBeNull();
  expect(r.spike).toBeNull();
  expect(r.managed).toBeNull();
  expect(r.def).toEqual({ top: 10, bottom: 0 });
  await ctx.close();
});

test('รายงานผู้บริหาร on a phone: the strip, เปิด PDF and สรุปผู้บริหาร; the report on demand and in print', async ({ browser }) => {
  test.setTimeout(150_000);
  const { ctx, page, errors } = await phoneShell(browser);
  await page.evaluate(async () => { try { localStorage.removeItem('oshinei_phmode'); } catch (e) { /* none */ } await phOpenPage(() => switchTab('mgtreport')); });
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(2000);
  const g = await page.evaluate(() => ({ fit: document.body.classList.contains('ph-fit'),
    gate: getComputedStyle(document.getElementById('mgtPhGate')).display, body: getComputedStyle(document.getElementById('mgtRptBody')).display,
    pdf: [...document.querySelectorAll('#mgtPhGate button')].map(b => b.textContent.trim()) }));
  expect(g.fit, 'รายงานผู้บริหาร opens in แตะง่าย on a phone').toBe(false);
  expect(g.gate).not.toBe('none');
  expect(g.body).toBe('none');
  expect(g.pdf).toContain('เปิด PDF');
  await page.locator('#mgtPhGate button', { hasText: 'อ่านฉบับเต็มบนจอ' }).click();
  expect(await page.evaluate(() => getComputedStyle(document.getElementById('mgtRptBody')).display)).not.toBe('none');
  await page.evaluate(() => mgtPhFull(false));
  await page.emulateMedia({ media: 'print' });
  expect(await page.evaluate(() => getComputedStyle(document.getElementById('mgtRptBody')).display), 'the print has the whole report').not.toBe('none');
  await page.emulateMedia({ media: 'screen' });
  expect(errors, errors.join('\n')).toEqual([]);
  await ctx.close();
});

test('the answer tables come first; เทียบเมนู A/B is one table ranked by the baht the cost differs', async ({ browser }) => {
  test.setTimeout(150_000);
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await login(page);
  await page.goto('/', { waitUntil: 'networkidle' });
  const before = (a, b) => page.evaluate(([a, b]) => { const x = document.querySelector(a), y = document.querySelector(b);
    return !!x && !!y && !!(x.compareDocumentPosition(y) & Node.DOCUMENT_POSITION_FOLLOWING); }, [a, b]);
  expect(await before('#ohbOutcome', '#ohbComp'), 'ของหมดก่อนรอบถัดไป before the rounds').toBe(true);
  expect(await before('#mce2IngBody', '#mce2Scatter'), 'วัตถุดิบที่ควรโจมตีก่อน before the scatter').toBe(true);
  expect(await before('#mce2IngBody', '#mce2Body'), 'and before the full list').toBe(true);
  expect(await page.evaluate(() => !!document.getElementById('mcTable2'))).toBe(false);
  await page.evaluate(() => switchTab('menucompare'));
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(2500);
  const mc = await page.evaluate(() => ({ head: document.getElementById('mcModeHead').classList.contains('gran-active'),
    th: [...document.querySelectorAll('#mcHead th')].map(t => t.textContent.trim()),
    d: [...document.querySelectorAll('#mcBody tr')].map(tr => tr.cells.length > 7 ? _phNum(tr.cells[7].textContent) : null).filter(v => v != null) }));
  expect(mc.head, 'ต่อหัว by default').toBe(true);
  expect(mc.th.some(t => t.startsWith('Δ ฿/หัว'))).toBe(true);
  for (let i = 1; i < mc.d.length; i++) expect(Math.abs(mc.d[i - 1])).toBeGreaterThanOrEqual(Math.abs(mc.d[i]) - 1e-9);
  await page.evaluate(() => switchTab('costheadtop60'));
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(2000);
  if (await page.evaluate(() => !!document.getElementById('chtOvTable'))) expect(await before('#chtRankTable', '#chtOvTable'), 'the ranking before the Top 60').toBe(true);
  await ctx.close();
});

// every page this PR touched keeps following the top bar: a period change and a branch change reload it with the new params, and a pushed
// strip is the one of the filters now on
const FOLLOW = [
  { tab: 'costhead', api: '/api/sales/cost-per-head', period: 'week', branch: true },
  { tab: 'costheadtop60', api: '/api/reports/cost-head-top60', period: 'month', branch: false },   // the page's own ขอบเขต, no branch filter
  { tab: 'menuefficiency', api: '/api/bom/consumption-analysis', period: 'month', branch: true },
  { tab: 'discounts', api: '/api/analysis/discounts', period: 'week', branch: true },
  { tab: 'gpbranch', api: '/api/period/cost', period: 'week', branch: true },
  { tab: 'cancelbill', api: '/api/extra/cancel-bills', period: 'month', branch: true },
  { tab: 'cashctl', api: '/api/extra/cash', period: 'month', branch: true },
  { tab: 'home', api: '/api/home/branch-scorecard', period: null, branch: true },
  { tab: 'overall', api: '/api/period/sales', period: 'week', branch: true },
  { tab: 'mgtreport', api: '/api/reports/management', period: null, branch: true },                 // its own รายวัน/สัปดาห์/เดือน
  { tab: 'menucompare', api: '/api/sales/menu-compare', period: null, branch: true },               // its own A / B periods
];
for (const p of FOLLOW) {
  test(`${p.tab} follows the top bar's ${[p.period && 'period', p.branch && 'branch'].filter(Boolean).join(' and ')}`, async ({ browser }) => {
    test.setTimeout(150_000);
    const { ctx, page, errors } = await phoneShell(browser);
    await page.evaluate(() => gPeriodSet({ kind: 'month', pick: '' }, true));
    const seen = [];
    page.on('request', r => { if (r.url().includes(p.api)) seen.push(r.url()); });
    await openPage(page, { tab: p.tab }, 'touch');
    await expect.poll(() => seen.length, { timeout: 60_000 }).toBeGreaterThan(0);
    const valid = () => page.evaluate((t) => { const a = _pageAns[t]; return !a || a.fk === ansFilterKey(a.follow); }, p.tab);
    if (p.period) {
      const n0 = seen.length, last = seen[seen.length - 1];
      await page.evaluate((kind) => {
        if (kind === 'week') { gPeriodSet({ kind: 'week', pick: '' }); return; }
        const a = gPeriodAnchor(), d = new Date(a + 'T00:00:00Z'); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() - 1);
        gPeriodSet({ kind: 'month', pick: d.toISOString().slice(0, 10) });
      }, p.period);
      await expect.poll(() => seen.slice(n0).some(u => u !== last), { timeout: 60_000, message: 'reloads with the new period' }).toBe(true);
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(1200);
      expect(await valid(), 'the strip is the new period\'s').toBe(true);
    }
    if (p.branch) {
      const br = await page.evaluate(() => [...document.getElementById('selBranch').options].map(o => o.value).filter(Boolean)[0]);
      const n1 = seen.length;
      await page.evaluate((b) => phBranchPick(b), br);
      await expect.poll(() => seen.slice(n1).some(u => decodeURIComponent(u).includes('branch=' + br)), { timeout: 60_000, message: 'reloads for the branch' }).toBe(true);
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(1200);
      expect(await valid(), 'the strip is the branch\'s').toBe(true);
    }
    expect(errors, errors.join('\n')).toEqual([]);
    await ctx.close();
  });
}
