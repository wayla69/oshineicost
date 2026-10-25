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
// DV3 (2026-10-25): the card is by dish (tests/e2e/menu-spike-dish.spec.js has the rows); the raw-plates section left the card (CSV only), so
// the checks here keep the controls, the "?", the font floors, กลับมาขาย, กำหนดเอง and the strip on the dish answer (tests/e2e/spike-mock.js).
// The menu-spike answer is mocked so the test does not depend on what the fixture holds.
const { test, expect } = require('@playwright/test');
const { dishAnswer } = require('./spike-mock');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const DESK = { viewport: { width: 1440, height: 900 } };
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];

const answer = url => dishAnswer(url);

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
    localStorage.setItem('oshinei_spike_period_v2', JSON.stringify(sp));
    localStorage.setItem('oshinei_spike_all', '0'); localStorage.setItem('oshinei_spike_new_open', '1');
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

test('เมนูพุ่ง (desktop): ต้นทุนเพิ่มสุทธิ on two unbroken lines, larger figures, กลับมาขาย in เมนูใหม่ and the CSV', async ({ browser }) => {
  test.setTimeout(150_000);
  const { ctx, page, errors } = await open(browser, DESK, 'fit');
  const m = await page.evaluate(() => {
    const tr = document.querySelector('#spikeTable tbody tr');
    const cost = [...tr.querySelector('.sp-cost-td').children].map(d => ({ t: d.textContent.trim(), h: d.getBoundingClientRect().height,
      fs: parseFloat(getComputedStyle(d).fontSize), fw: +getComputedStyle(d).fontWeight }));
    const fs = sel => [...document.querySelectorAll(sel)].map(e => parseFloat(getComputedStyle(e).fontSize));
    const back = document.querySelector('#spikeNewTable tbody tr .sp-back-chip');
    return { cost, v: fs('#spikeTable .sp-v'), sub: fs('#spikeTable .sp-perday, #spikeTable .sp-push'), names: fs('#dsSpike .sp-menu'),
      back: back ? back.textContent.trim() : '', backTip: back ? back.title : '' };
  });
  // ฿ (bold, bad ink) / % ของต้นทุนอาหาร: two lines, each one line tall
  expect(m.cost.map(c => c.t)).toEqual(['฿6,000', '0.6% ของต้นทุนอาหาร']);
  for (const c of m.cost) expect(c.h).toBeLessThan(c.fs * 1.6 + 1);
  expect(m.cost[0].fw).toBeGreaterThanOrEqual(700);
  expect(Math.min(...m.v)).toBeGreaterThanOrEqual(14);
  expect(Math.min(...m.sub)).toBeGreaterThanOrEqual(12);
  expect(Math.min(...m.names)).toBeGreaterThanOrEqual(13);
  expect(m.back).toBe('กลับมาขาย 2 สาขา');
  expect(m.backTip).toContain('กลับมาขาย');
  // the CSV carries the flag on the per-branch new-menu rows and the count on the dish row
  const csv = await page.evaluate(() => { let out = ''; const keep = window.downloadCSV; window.downloadCSV = t => { out = t; };
    try { spikeExport(); } finally { window.downloadCSV = keep; } return out; });
  const rows = csv.split('\n').map(r => r.split('","').map(c => c.replace(/^"|"$/g, '')));
  const h2 = rows.findIndex((r, i) => i > 0 && r[0] === 'ส่วน');
  const ci = rows[h2].indexOf('กลับมาขาย');
  expect(ci).toBeGreaterThan(0);
  const shrimp = rows.slice(h2).find(r => r[1] && r[1].includes('กุ้งลายเสือ'));
  expect(shrimp && shrimp[0]).toBe('รายสาขา · เมนูใหม่');
  expect(shrimp && shrimp[ci]).toBe('ใช่');
  expect(rows.slice(h2).find(r => r[1] === 'เมนูใหม่ 2')[ci]).toBe('');
  const d0 = rows.slice(0, h2).find(r => r[1] && r[1].includes('กุ้งลายเสือ'));
  expect(d0 && d0[rows[0].indexOf('กลับมาขาย (สาขา)')]).toBe('2');
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

test('เมนูพุ่ง (แตะง่าย 390): the card leads with ต้นทุนเพิ่มสุทธิ, the row sheet keeps its two lines, a returning dish carries its chip', async ({ browser }) => {
  test.setTimeout(150_000);
  const { ctx, page, errors } = await open(browser, PHONE, 'touch');
  await page.waitForFunction(() => document.querySelectorAll('#dsSpike .ph-cl').length >= 2, null, { timeout: 30_000 });
  const c = await page.evaluate(() => {
    const [old, neu] = document.querySelectorAll('#dsSpike .ph-cl');
    const card = old.querySelector('.ph-cl-card');
    const pill = (neu.querySelector('.ph-cl-card') || document.createElement('i')).querySelector('.sp-back-chip');
    return { labels: [...card.querySelectorAll('.ph-cl-fl')].map(x => x.textContent.trim()), pill: pill ? pill.textContent.trim() : '' };
  });
  expect(c.labels).toEqual(['ต้นทุนเพิ่มสุทธิ', 'สะสม', '+จาน']);
  expect(c.pill).toBe('กลับมาขาย 2 สาขา');
  await page.locator('#dsSpike .ph-cl .ph-cl-hit').first().click();
  await page.waitForFunction(() => document.getElementById('msSheet').classList.contains('open'), null, { timeout: 10_000 });
  const sh = await page.evaluate(() => [...document.querySelectorAll('#msSheetBody .ph-rs-row')].map(r => ({ dt: r.querySelector('dt').textContent.trim(),
    lines: [...r.querySelectorAll('.ph-rs-kv-r')].map(l => l.textContent.replace(/\s+/g, ' ').trim()), txt: r.textContent.replace(/\s+/g, ' ').trim() })));
  const net = sh.find(r => r.dt === 'ต้นทุนเพิ่มสุทธิ');
  expect(net && net.txt).toContain('฿6,000');
  expect(net && net.txt).toContain('0.6% ของต้นทุนอาหาร');
  expect(errors).toEqual([]);
  await ctx.close();
});

const WEEKS = { m: 'range', f: '2026-10-12', t: '2026-10-18', pk: 'week', bk: 'week', bf: '2026-10-05', bt: '2026-10-11' };

test('เมนูพุ่ง กำหนดเอง (desktop): a week vs a week - two week pickers, both ranges in the chip, the strip, the dish table', async ({ browser }) => {
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
  expect(m.heads).toEqual(['#', 'เมนู', 'ต้นทุนเพิ่มสุทธิ', 'สะสม', '+จาน']);
  expect(m.tiles.map(t => t.l)).toEqual(['ลูกค้า ช่วงนี้', 'เมนูที่ถึงเกณฑ์', 'ต้นทุนเพิ่มสุทธิ', 'tier ที่ดันมากสุด']);
  expect(m.tiles.map(t => t.v)).toEqual(['52,340', '12 เมนู', '+฿34.2K', '฿789']);
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
    if (mode === 'touch') {               // DV3: the card leads with ต้นทุนเพิ่มสุทธิ
      const labels = await page.evaluate(() => [...document.querySelector('#dsSpike .ph-cl .ph-cl-card').querySelectorAll('.ph-cl-fl')].map(x => x.textContent.trim()));
      expect(labels).toEqual(['ต้นทุนเพิ่มสุทธิ', 'สะสม', '+จาน']);
    }
    expect(errors).toEqual([]);
    await ctx.close();
  });
}

for (const [name, opts, mode] of [['desktop', DESK, 'fit'], ['แตะง่าย 390', PHONE, 'touch'], ['พอดีจอ 390', PHONE, 'fit']]) {
  test(`เมนูพุ่ง แบบเดิม (${name}): DV3 - the raw-plates section is no longer on the card, its rows stay in the CSV`, async ({ browser }) => {
    test.setTimeout(150_000);
    const { ctx, page, errors } = await open(browser, opts, mode);
    const m = await page.evaluate(() => ({ secs: [...document.querySelectorAll('#dsSpike > section')].map(x => x.className),
      plates: !!document.getElementById('spikePlatesTable') || !!document.getElementById('spikePlatesBody'), hv: !!document.getElementById('spikeHvSec') }));
    expect(m.secs.length).toBe(2);
    expect(m.secs[0]).toContain('sp-sec-old');
    expect(m.secs[1]).toContain('sp-sec-new');
    expect(m.plates).toBe(false);
    expect(m.hv).toBe(false);
    const csv = await page.evaluate(() => { let out = ''; const keep = window.downloadCSV; window.downloadCSV = t => { out = t; };
      try { spikeExport(); } finally { window.downloadCSV = keep; } return out; });
    expect(csv.split('\n').filter(r => r.startsWith('"เมนูพุ่ง · นับจำนวนจาน (แบบเดิม)"')).length).toBe(3);
    expect(errors).toEqual([]);
    await ctx.close();
  });
}
