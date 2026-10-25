// @ts-check
// PR-TD (owner 2026-10-25, phone screenshot of ยอดขายรายวัน → เมนูพุ่งผิดปกติ at ทุกสาขา เดือนนี้):
//   * "? ใหญ่มาก" - the "?" is a <= 30 px circle in every mode; on a phone its tap area is still >= 44 px (an invisible ::after), here and on
//     three other pages;
//   * the period chips, the branch select and CSV are one balanced row: chips + select on ONE row of equal height at 390 px (both modes), every
//     control >= 44 px to tap;
//   * เพิ่มจาก tier: one tier per line (label left, +จาน right); ต้นทุนเพิ่ม: ฿ / % ของต้นทุนอาหาร / จุด on three lines, none broken;
//   * the figures read larger: >= 14 px on a desktop, >= 12 px at 100 % in พอดีจอ (the phone-modes floor), menu names >= 13 px;
//   * a menu back on sale after >= 21 days without one sits in เมนูใหม่ with the chip กลับมาขาย (table, card, CSV);
//   * (owner, later the same day) กำหนดเอง: ช่วงนี้ a week เทียบกับ a week (two week pickers, both ranges in the header chip, base_from /
//     base_to sent), compact pickers on the control row; the summary strip (4 tiles, 2-up on a phone); ต้นทุนเพิ่ม is the LAST column and
//     the last figure of a touch card; a third section at the bottom counts raw plates (แบบเดิม), top 15 + ดูทั้งหมด, folds and remembers.
// The menu-spike answer is mocked so the test does not depend on what the fixture holds.
const { test, expect } = require('@playwright/test');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const DESK = { viewport: { width: 1440, height: 900 } };
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];

function answer(url) {
  const u = new URL(url || 'http://x/'), q = k => u.searchParams.get(k);
  const tiers = [{ tier: '789', label: '฿789', plates: 60, customers: 140, rate: 42.9, normal_rate: 20, excess: 32, x: 2.1, high: true },
    { tier: '999', label: '฿999', plates: 20, customers: 40, rate: 50, normal_rate: 40, excess: 4, x: 1.2, high: false }];
  const spikes = Array.from({ length: 20 }, (_, i) => ({ branch: 'สาขาเอ', menu: `ปลาแซลมอนย่างเทริยากิ (1 ชิ้น ต่อ 1 เสิร์ฟ) ${i + 1}`, today: 200 - i, per_day: 30,
    expected: 80, excess: 120 - i, x: 2.5, pct: 150, kind: 'buffet', basis: 'buffet', customers: 400, rate: 50, normal_rate: 20, tiers, tier_rest: 0,
    unit_cost: 50, extra_cost: (120 - i) * 50, cost_share: 0.1, extra_cost_per_head_pp: 0.04, uncosted: false, package: false }));
  const new_menus = [
    { branch: 'สาขาเอ', menu: '(BF) กุ้งลายเสือย่างกระเทียม (1 ตัว)(Buffet)', first_sold: '2026-10-01', first_sold_th: '1 ต.ค.', age_days: 19, qty: 1515, per_day: 79.7,
      kind: 'buffet', basis: 'buffet', customers: 2623, rate: 57.8, trend: null, returning: true, returned_on: '2026-10-01', first_sold_ever: '2026-03-22',
      unit_cost: 25, period_cost: 37875, uncosted: false, package: false },
    { branch: 'สาขาเอ', menu: 'เมนูใหม่ 1', first_sold: '2026-10-15', first_sold_th: '15 ต.ค.', age_days: 5, qty: 40, per_day: 8, kind: 'alc', basis: 'all',
      customers: 300, rate: 13.3, trend: null, unit_cost: 30, period_cost: 1200, uncosted: false, package: false }];
  const range = q('period') === 'range', vs = !!q('base_from');
  const spikes_plates = Array.from({ length: 18 }, (_, i) => ({ branch: 'สาขาเอ', menu: `เมนูจานเยอะ ${i + 1}`, today: 300 - i, per_day: 40, avg: 100, expected: 100,
    excess: 200 - i, x: 3.0, pct: 200, is_new: false, kind: 'buffet' }));
  return { period: range ? 'range' : 'mtd', date_from: range ? q('date_from') : '2026-10-01', date_to: range ? q('date_to') : '2026-10-19',
    latest_day: '2026-10-19', spikes, count: 20, new_menus, new_count: 2, spikes_plates, plates_count: 18,
    base: vs ? { kind: 'range', from: q('base_from'), to: q('base_to') } : { kind: 'normal', from: '2026-09-10', to: '2026-09-30' },
    summary: { customers: 52340, customers_per_day: 2754.7, base_customers_per_day: 2500, customers_vs_base_pct: 10.2,
      tier_top: { label: '฿789', excess: 640 }, top_excess: 1695 },
    top: 15, cost_view: true, sort: 'extra_cost', food_cost_total: 1e6, revenue_total: 2e6, revenue_basis: 'net', top_extra_cost: 61000,
    top_cost_share: 6.1, top_pp: 3.05, uncosted_count: 0 };
}

async function login(ctx) {
  if (process.env.E2E_USER && process.env.E2E_PASS) {
    const r = await ctx.request.post('/api/auth/login', { data: { username: process.env.E2E_USER, password: process.env.E2E_PASS } });
    expect(r.ok()).toBe(true);
  }
}

async function open(browser, opts, mode, tab = 'dailysales', sm = { m: 'mtd', f: '', t: '' }) {
  const ctx = await browser.newContext(opts);
  await login(ctx);
  await ctx.addInitScript(([m, t, sp]) => { try {
    localStorage.setItem('oshinei_phmode', JSON.stringify({ [t]: m }));
    localStorage.setItem('oshinei_spike_period', JSON.stringify(sp));
    localStorage.setItem('oshinei_spike_all', '0');
  } catch (e) { /* none */ } }, [mode, tab, sm]);
  const urls = [];
  await ctx.route('**/api/sales/menu-spike?*', r => { urls.push(r.request().url());
    return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(answer(r.request().url())) }); });
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
  if (tab === 'dailysales') await page.waitForFunction(() => !!document.querySelector('#dsSpike #spikeNewTable'), null, { timeout: 60_000 });
  await page.waitForTimeout(1200);                 // the fit frame and the cards follow a re-render
  return { ctx, page, errors, urls };
}

// every "?" on screen in a scope: its drawn size, and whether a tap 21 px off its centre (inside a 44 px square) still lands on it
const Q_FACTS = scope => {
  const root = scope ? document.querySelector(scope) : document;
  const out = [];
  for (const q of root ? root.querySelectorAll('.ui-q') : []) {
    if (!q.getClientRects().length) continue;
    q.scrollIntoView({ block: 'center' });
    const r = q.getBoundingClientRect();
    if (r.width < 1 || r.top < 0 || r.bottom > innerHeight) continue;
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const hit = (dx, dy) => { const e = document.elementFromPoint(cx + dx, cy + dy); return !!e && e.closest('.ui-q') === q ? '' : `${dx},${dy}:${e ? e.tagName + '.' + e.className : 'none'}`; };
    const miss = [hit(0, -21), hit(0, 21), hit(-21, 0), hit(21, 0)].filter(Boolean);
    out.push({ w: Math.round(r.width), h: Math.round(r.height), tap: !miss.length, miss: miss.join(' | ') });
  }
  return out;
};

for (const [name, mode] of [['แตะง่าย', 'touch'], ['พอดีจอ', 'fit']]) {
  test(`เมนูพุ่ง (${name} 390): a compact "?", one row of equal-height controls with 44 px tap areas`, async ({ browser }) => {
    test.setTimeout(150_000);
    const { ctx, page, errors } = await open(browser, PHONE, mode);
    expect(await page.evaluate(() => document.body.classList.contains('ph-fit'))).toBe(mode === 'fit');
    const qs = await page.evaluate(Q_FACTS, '#tab-dailysales .card:has(#dsSpike)');
    expect(qs.length).toBeGreaterThanOrEqual(2);
    for (const q of qs) {
      expect(q.w, 'the "?" circle').toBeLessThanOrEqual(30);
      expect(q.h).toBeLessThanOrEqual(30);
      expect(q.miss, 'a tap 21 px off the centre is still the "?"').toBe('');
    }
    const row = await page.evaluate(() => {
      document.getElementById('spikeModes').scrollIntoView({ block: 'center' });
      const vis = el => { const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
        // the select draws its box inside a transparent border (its tap area is the whole border box)
        const bt = el.tagName === 'SELECT' ? parseFloat(cs.borderTopWidth) : 0, bb = el.tagName === 'SELECT' ? parseFloat(cs.borderBottomWidth) : 0;
        return { top: r.top + bt, bottom: r.bottom - bb, tapH: r.height, left: r.left, right: r.right }; };
      const track = vis(document.getElementById('spikeModes')), sel = vis(document.getElementById('spikeBranch'));
      const chips = [...document.querySelectorAll('#spikeModes .gran-btn')].map(b => {
        const r = b.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
        const hit = dy => { const e = document.elementFromPoint(cx, cy + dy); return !!e && e.closest('.gran-btn') === b; };
        return { h: r.height, w: r.width, tap: hit(-21.5) && hit(21.5) }; });
      const csv = document.querySelector('.sp-csv').getBoundingClientRect();
      const ccx = csv.left + csv.width / 2, ccy = csv.top + csv.height / 2;
      const csvTap = [-21, 21].map(dy => { const e = document.elementFromPoint(ccx, ccy + dy); return !!e && !!e.closest('.sp-csv') ? '' : `${dy}:${e ? e.tagName + '.' + e.className : 'none'}`; }).filter(Boolean).join(' | ');
      return { track, sel, chips, csvH: csv.height, csvTap, sw: document.documentElement.scrollWidth };
    });
    // one row: the select sits beside the track, the same visual height (32-36 px), its tap area 44 px
    expect(Math.abs(row.sel.top - row.track.top)).toBeLessThanOrEqual(1.5);
    expect(Math.abs(row.sel.bottom - row.track.bottom)).toBeLessThanOrEqual(1.5);
    expect(row.sel.left).toBeGreaterThan(row.track.right - 1);
    const th = row.track.bottom - row.track.top;
    expect(th).toBeGreaterThanOrEqual(31);
    expect(th).toBeLessThanOrEqual(37);
    expect(row.sel.tapH).toBeGreaterThanOrEqual(43.5);
    for (const c of row.chips) {
      expect(c.h).toBeLessThanOrEqual(36);
      expect(c.w).toBeGreaterThanOrEqual(43.5);
      expect(c.tap, 'a chip is 44 px to tap').toBe(true);
    }
    expect(row.csvH).toBeLessThanOrEqual(37);
    expect(row.csvTap, `CSV is 44 px to tap (${row.csvH})`).toBe('');
    expect(row.sw).toBeLessThanOrEqual(390);
    expect(errors).toEqual([]);
    await ctx.close();
  });
}

test('เมนูพุ่ง (แตะง่าย 390): the "?" is compact with a 44 px tap area on three other pages', async ({ browser }) => {
  test.setTimeout(240_000);
  for (const tab of ['overall', 'home', 'cost']) {
    const { ctx, page } = await open(browser, PHONE, 'touch', tab);
    await page.waitForFunction(t => [...document.querySelectorAll(`#tab-${t} .ui-q`)].some(q => q.getClientRects().length), tab, { timeout: 60_000 });
    const qs = await page.evaluate(Q_FACTS, `#tab-${tab}`);
    expect(qs.length, tab).toBeGreaterThan(0);
    for (const q of qs) {
      expect(q.w, tab).toBeLessThanOrEqual(30);
      expect(q.h, tab).toBeLessThanOrEqual(30);
      expect(q.miss, tab).toBe('');
    }
    await ctx.close();
  }
});

test('เมนูพุ่ง (desktop): one tier per line, ต้นทุนเพิ่ม on three unbroken lines, larger figures, กลับมาขาย in เมนูใหม่ and the CSV', async ({ browser }) => {
  test.setTimeout(150_000);
  const { ctx, page, errors } = await open(browser, DESK, 'fit');
  const m = await page.evaluate(() => {
    const tr = document.querySelector('#spikeTable tbody tr');
    const lines = [...tr.querySelectorAll('.sp-tl')].map(l => { const r = l.getBoundingClientRect(), k = l.querySelector('.sp-tl-k').getBoundingClientRect(),
      v = l.querySelector('.sp-tl-v').getBoundingClientRect(); return { top: r.top, h: r.height, lh: parseFloat(getComputedStyle(l).lineHeight) || 18,
      kLeft: k.left, vLeft: v.left, kTop: k.top, vTop: v.top, hi: l.classList.contains('sp-tl-hi'), txt: l.textContent.replace(/\s+/g, ' ').trim() }; });
    const cost = [...tr.querySelector('.sp-cost-td').children].map(d => ({ t: d.textContent.trim(), h: d.getBoundingClientRect().height,
      fs: parseFloat(getComputedStyle(d).fontSize), fw: +getComputedStyle(d).fontWeight }));
    const fs = sel => [...document.querySelectorAll(sel)].map(e => parseFloat(getComputedStyle(e).fontSize));
    const back = document.querySelector('#spikeNewTable tbody tr .sp-back-chip');
    return { lines, cost, v: fs('#spikeTable .sp-v'), sub: fs('#spikeTable .sp-perday'), names: fs('#dsSpike .sp-menu'),
      back: back ? back.textContent.trim() : '', backTip: back ? back.title : '', oldMenus: [...document.querySelectorAll('#spikeTable .sp-menu')].map(e => e.textContent) };
  });
  // two tiers, two lines stacked; on each, the label left and +จาน right on the same line; the high one in bad ink
  expect(m.lines.map(l => l.txt)).toEqual(['฿789 +32', '฿999 +4']);
  expect(m.lines[1].top).toBeGreaterThan(m.lines[0].top + m.lines[0].h - 1);
  for (const l of m.lines) {
    expect(l.h).toBeLessThan(l.lh * 1.6);
    expect(l.vLeft).toBeGreaterThan(l.kLeft);
    expect(Math.abs(l.vTop - l.kTop)).toBeLessThan(4);
  }
  expect(m.lines.map(l => l.hi)).toEqual([true, false]);
  // ฿ (bold) / % ของต้นทุนอาหาร / จุด: three lines, each one line tall
  expect(m.cost.map(c => c.t)).toEqual(['฿6,000', '0.1% ของต้นทุนอาหาร', '+0.04 จุด']);
  for (const c of m.cost) expect(c.h).toBeLessThan(c.fs * 1.6 + 1);
  expect(m.cost[0].fw).toBeGreaterThanOrEqual(700);
  expect(Math.min(...m.v)).toBeGreaterThanOrEqual(14);
  expect(Math.min(...m.sub)).toBeGreaterThanOrEqual(12);
  expect(Math.min(...m.names)).toBeGreaterThanOrEqual(13);
  expect(m.back).toBe('กลับมาขาย');
  expect(m.backTip).toContain('21 วัน');
  expect(m.oldMenus.some(t => t.includes('กุ้งลายเสือ'))).toBe(false);
  // the CSV carries the flag
  const csv = await page.evaluate(() => { let out = ''; const keep = window.downloadCSV; window.downloadCSV = t => { out = t; };
    try { spikeExport(); } finally { window.downloadCSV = keep; } return out; });
  const rows = csv.split('\n').map(r => r.split('","').map(c => c.replace(/^"|"$/g, '')));
  const ci = rows[0].indexOf('กลับมาขาย');
  expect(ci).toBeGreaterThan(0);
  const shrimp = rows.find(r => r[1] && r[1].includes('กุ้งลายเสือ'));
  expect(shrimp && shrimp[0]).toBe('เมนูใหม่');
  expect(shrimp && shrimp[ci]).toBe('ใช่');
  expect(rows.find(r => r[1] === 'เมนูใหม่ 1')[ci]).toBe('');
  expect(errors).toEqual([]);
  await ctx.close();
});

test('เมนูพุ่ง (พอดีจอ 390): every figure is >= 12 px at 100 %, the main figures >= 13 px', async ({ browser }) => {
  test.setTimeout(150_000);
  const { ctx, page, errors } = await open(browser, PHONE, 'fit');
  const f = await page.evaluate(() => {
    const fs = sel => [...document.querySelectorAll(sel)].filter(e => e.getClientRects().length).map(e => parseFloat(getComputedStyle(e).fontSize));
    return { all: fs('#dsSpike table tbody td, #dsSpike table tbody td *'), main: fs('#spikeTable .sp-v, #spikeNewTable .sp-v'), names: fs('#dsSpike .sp-menu') };
  });
  expect(f.all.length).toBeGreaterThan(0);
  expect(Math.min(...f.all)).toBeGreaterThanOrEqual(12);
  expect(Math.min(...f.main)).toBeGreaterThanOrEqual(13);
  expect(Math.min(...f.names)).toBeGreaterThanOrEqual(13);
  expect(errors).toEqual([]);
  await ctx.close();
});

test('เมนูพุ่ง (แตะง่าย 390): the card shows one tier per line, the row sheet keeps the lines, a returning menu carries its chip', async ({ browser }) => {
  test.setTimeout(150_000);
  const { ctx, page, errors } = await open(browser, PHONE, 'touch');
  await page.waitForFunction(() => document.querySelectorAll('#dsSpike .ph-cl').length >= 2, null, { timeout: 30_000 });
  const c = await page.evaluate(() => {
    const [old, neu] = document.querySelectorAll('#dsSpike .ph-cl');
    const card = old.querySelector('.ph-cl-card');
    const kv = [...card.querySelectorAll('.ph-cl-kv-r')].map(r => ({ parts: [...r.children].map(x => x.textContent.trim()), top: r.getBoundingClientRect().top,
      hi: r.classList.contains('ph-kv-hi') }));
    const pill = (neu.querySelector('.ph-cl-card') || document.createElement('i')).querySelector('.sp-back-chip');
    return { kv, pill: pill ? pill.textContent.trim() : '' };
  });
  expect(c.kv.map(k => k.parts)).toEqual([['฿789', '+32'], ['฿999', '+4']]);
  expect(c.kv[1].top).toBeGreaterThan(c.kv[0].top);
  expect(c.kv.map(k => k.hi)).toEqual([true, false]);
  expect(c.pill).toBe('กลับมาขาย');
  // the row sheet: ต้นทุนเพิ่ม on three lines, the tiers one per line
  await page.locator('#dsSpike .ph-cl .ph-cl-hit').first().click();
  await page.waitForFunction(() => document.getElementById('msSheet').classList.contains('open'), null, { timeout: 10_000 });
  const sh = await page.evaluate(() => [...document.querySelectorAll('#msSheetBody .ph-rs-row')].map(r => ({ dt: r.querySelector('dt').textContent.trim(),
    lines: [...r.querySelectorAll('.ph-rs-kv-r')].map(l => l.textContent.replace(/\s+/g, ' ').trim()) })));
  expect(sh.find(r => r.dt === 'ต้นทุนเพิ่ม').lines).toEqual(['฿6,000', '0.1% ของต้นทุนอาหาร', '+0.04 จุด']);
  expect(sh.find(r => /เพิ่มจาก tier/i.test(r.dt)).lines).toEqual(['฿789 +32', '฿999 +4']);
  expect(errors).toEqual([]);
  await ctx.close();
});

const WEEKS = { m: 'range', f: '2026-10-12', t: '2026-10-18', pk: 'week', bk: 'week', bf: '2026-10-05', bt: '2026-10-11' };

test('เมนูพุ่ง กำหนดเอง (desktop): a week vs a week - two week pickers, both ranges in the chip, the strip, ต้นทุนเพิ่ม last', async ({ browser }) => {
  test.setTimeout(150_000);
  const { ctx, page, errors, urls } = await open(browser, DESK, 'fit', 'dailysales', WEEKS);
  const last = new URL(urls[urls.length - 1]);
  expect([last.searchParams.get('date_from'), last.searchParams.get('date_to'), last.searchParams.get('base_from'), last.searchParams.get('base_to')])
    .toEqual(['2026-10-12', '2026-10-18', '2026-10-05', '2026-10-11']);
  const m = await page.evaluate(() => {
    const vis = id => { const e = document.getElementById(id); return !!e && e.getClientRects().length > 0; };
    const opt = id => { const e = document.getElementById(id); return e ? [e.value, e.selectedOptions[0] ? e.selectedOptions[0].textContent : ''] : null; };
    const h = id => Math.round(document.getElementById(id).getBoundingClientRect().height);
    return { cur: vis('spikeCurWeek'), base: vis('spikeBaseWeek'), curDates: vis('spikeCurRange'), curOpt: opt('spikeCurWeek'), baseOpt: opt('spikeBaseWeek'),
      chip: document.getElementById('spikeRange').textContent, heads: [...document.querySelectorAll('#spikeTable thead th')].map(t => t.textContent.trim().split(' ')[0]),
      tiles: [...document.querySelectorAll('#spikeStrip .sp-tile')].map(t => ({ l: t.querySelector('.sp-tile-l').textContent.replace('?', '').trim(),
        v: t.querySelector('.sp-tile-v').textContent.trim(), fs: parseFloat(getComputedStyle(t.querySelector('.sp-tile-v')).fontSize),
        lfs: parseFloat(getComputedStyle(t.querySelector('.sp-tile-l')).fontSize) })),
      hs: [h('spikeModes'), h('spikeBranch'), h('spikeCurWeek'), h('spikeBaseKind')], sub: document.getElementById('spikeSubTxt').textContent };
  });
  expect(m.cur && m.base && !m.curDates).toBe(true);
  expect(m.curOpt).toEqual(['2026-10-12', 'สัปดาห์ 12–18 ต.ค.']);
  expect(m.baseOpt).toEqual(['2026-10-05', 'สัปดาห์ 5–11 ต.ค.']);
  expect(m.chip).toBe('12–18 ต.ค. เทียบ 5–11 ต.ค.');
  expect(m.sub).toContain('5–11 ต.ค.');
  expect(m.heads).toEqual(['#', 'เมนู', 'เพิ่มจาก', 'ช่วงนี้', 'ช่วงเทียบ', 'สูงกว่า', 'ต้นทุนเพิ่ม']);
  expect(m.tiles.map(t => t.l)).toEqual(['ลูกค้า ช่วงนี้', 'เมนูพุ่ง', 'ต้นทุนเพิ่มรวม', 'tier ที่ดันมากสุด']);
  expect(m.tiles.map(t => t.v)).toEqual(['52,340', '20 รายการ', '฿61,000', '฿789']);
  for (const t of m.tiles) { expect(t.fs).toBeGreaterThanOrEqual(16); expect(t.fs).toBeLessThanOrEqual(18); expect(t.lfs).toBe(12); }
  for (const x of m.hs) expect(x).toBe(32);                      // one height for the track, the branch and the pickers
  // a week that does not end before ช่วงนี้ is refused in the card, in formal Thai, before any request
  const n = urls.length;
  await page.evaluate(() => { const b = document.getElementById('spikeBaseWeek'); b.value = b.options[0].value; spikeApplyRange(); });
  expect(await page.locator('#dsSpike').innerText()).toContain('ช่วงที่ใช้เทียบต้องสิ้นสุดก่อนวันเริ่มต้นของช่วงที่เลือก');
  expect(urls.length).toBe(n);
  expect(errors).toEqual([]);
  await ctx.close();
});

for (const [name, mode] of [['แตะง่าย', 'touch'], ['พอดีจอ', 'fit']]) {
  test(`เมนูพุ่ง กำหนดเอง (${name} 390): compact pickers (36 px drawn, 44 px to tap), the strip 2-up, no sideways scroll`, async ({ browser }) => {
    test.setTimeout(150_000);
    const { ctx, page, errors } = await open(browser, PHONE, mode, 'dailysales', WEEKS);
    const m = await page.evaluate(() => {
      const vis = el => { const r = el.getBoundingClientRect(), cs = getComputedStyle(el); return { h: r.height - parseFloat(cs.borderTopWidth) - parseFloat(cs.borderBottomWidth), tap: r.height }; };
      const ids = ['spikeCurKind', 'spikeCurWeek', 'spikeBaseKind', 'spikeBaseWeek'];
      const tiles = [...document.querySelectorAll('#spikeStrip .sp-tile')].map(t => t.getBoundingClientRect());
      const ap = document.querySelector('#spikeCustom .sp-apply').getBoundingClientRect();
      return { pick: ids.map(id => vis(document.getElementById(id))), track: document.getElementById('spikeModes').getBoundingClientRect().height,
        cols: new Set(tiles.map(r => Math.round(r.left))).size, rows: new Set(tiles.map(r => Math.round(r.top))).size, ap: ap.height,
        chip: document.getElementById('spikeRange').textContent, sw: document.documentElement.scrollWidth };
    });
    for (const p of m.pick) { expect(Math.abs(p.h - m.track)).toBeLessThanOrEqual(1.5); expect(p.tap).toBeGreaterThanOrEqual(43.5); }
    expect(m.ap).toBeLessThanOrEqual(37);
    expect(m.cols).toBe(2);
    expect(m.rows).toBe(2);
    expect(m.chip).toBe('12–18 ต.ค. เทียบ 5–11 ต.ค.');
    expect(m.sw).toBeLessThanOrEqual(390);
    if (mode === 'touch') {               // the card: ต้นทุนเพิ่ม is the last key figure
      const labels = await page.evaluate(() => [...document.querySelector('#dsSpike .ph-cl .ph-cl-card').querySelectorAll('.ph-cl-fl')].map(x => x.textContent.trim()));
      expect(labels).toEqual(['ต่อ 100 ลูกค้า', 'สูงกว่า', 'ต้นทุนเพิ่ม']);
    }
    expect(errors).toEqual([]);
    await ctx.close();
  });
}

for (const [name, opts, mode] of [['desktop', DESK, 'fit'], ['แตะง่าย 390', PHONE, 'touch'], ['พอดีจอ 390', PHONE, 'fit']]) {
  test(`เมนูพุ่ง แบบเดิม (${name}): the raw-plates section is the last one, 15 rows + ดูทั้งหมด, folds and remembers`, async ({ browser }) => {
    test.setTimeout(150_000);
    const { ctx, page, errors } = await open(browser, opts, mode);
    const m = await page.evaluate(() => {
      const secs = [...document.querySelectorAll('#dsSpike > section')].map(x => x.className);
      const t = document.getElementById('spikePlatesTable');
      return { secs, last: secs[secs.length - 1], rows: t ? t.tBodies[0].rows.length : 0,
        heads: t ? [...t.tHead.rows[0].cells].map(c => c.textContent.trim().split(' ')[0]) : [],
        btn: (document.getElementById('spikePlatesAll') || { textContent: '' }).textContent.trim(),
        top: (document.querySelector('.sp-sec-plates') || document.body).getBoundingClientRect().top,
        newBottom: (document.querySelector('.sp-sec-new') || document.body).getBoundingClientRect().bottom };
    });
    expect(m.secs.length).toBe(3);
    expect(m.last).toContain('sp-sec-plates');
    expect(m.top).toBeGreaterThanOrEqual(m.newBottom - 0.5);
    expect(m.rows).toBe(15);
    expect(m.heads).toEqual(['#', 'เมนู', 'ช่วงนี้', 'ปกติ', '+จาน', 'สูงกว่า']);
    expect(m.btn).toBe('ดูทั้งหมด (18)');
    await page.evaluate(() => document.getElementById('spikePlatesAll').click());
    expect(await page.evaluate(() => document.getElementById('spikePlatesTable').tBodies[0].rows.length)).toBe(18);
    // fold it: remembered on this device
    await page.evaluate(() => document.getElementById('spikePlatesFold').click());
    expect(await page.evaluate(() => { const b = document.getElementById('spikePlatesBody'); return !!b && b.hidden; })).toBe(true);
    expect(await page.evaluate(() => localStorage.getItem('oshinei_spike_plates_open'))).toBe('0');
    await page.evaluate(() => _spikeDraw(_spikeLast, _spikeOneBr));
    expect(await page.evaluate(() => document.getElementById('spikePlatesBody').hidden)).toBe(true);
    await page.evaluate(() => document.getElementById('spikePlatesFold').click());
    expect(errors).toEqual([]);
    await ctx.close();
  });
}
