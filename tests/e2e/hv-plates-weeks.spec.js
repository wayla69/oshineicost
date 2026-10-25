// @ts-check
// Owner 2026-10-25 (phone screenshot of วัตถุดิบมูลค่าสูง · จำนวนจาน, 390 px พอดีจอ, week mode): "เพิ่มจำนวนเปรียบเทียบให้ด้วยได้ไหมครับว่าเดิมเท่าไร
// และอยาก filter เลือกเทียบอาทิตย์ได้ด้วย w1 vs w7 แบบนี้ ใช้ Mon-Sunday เป็นตัวตัด week และ tab เลือกชนิดอาหารทำให้อยู่ในหน้าเลย ไม่ต้องเลื่อน ตัวเลขใหญ่ขึ้น":
//   1. every figure shows its base under it ("▲0.9% · เดิม 193,477"), in the table, ทั้งหมด, the touch cards and the HVM tables;
//   2. the section's own week pair: สัปดาห์ [W43 · …] เทียบ [W42 · …] (Mon–Sun, ISO numbers); a week still running compares the same weekdays;
//   3. the 9 food chips wrap on a phone (no sideways scroll), 44 px to tap; the HVM chips too;
//   4. bigger figures (>= 16 px on a phone, >= 12 px text at 100 % in พอดีจอ).
// DV4 (phone table review 2026-10-25): the section is the จำนวนจาน view of High Value Product Monitor now (DV3 took it off the menu-spike
// card); the pair opens on the last finished week vs the week before, ตามแถบด้านบน follows the top bar; the page follows the top bar's
// period and branch in both views.
// The answers are mocked (the weeks list and the bases follow the request) so the cases hold whatever the fixture has.
const { test, expect } = require('@playwright/test');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const DESK = { viewport: { width: 1440, height: 900 } };
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];
const SHOTS = process.env.HVW_SHOTS || '';
const GROUPS = [['salmon', 'แซลมอน'], ['tuna', 'ทูน่า'], ['hamachi', 'ฮามาจิ'], ['amaebi', 'กุ้งหวาน'], ['scallop', 'หอยเชลล์'], ['eel', 'ปลาไหล'], ['oyster', 'หอยนางรม'], ['beef', 'เนื้อ']];
const LATEST = '2026-10-24';                        // a Saturday: W43 (19–25 ต.ค.) is still running, closed to Saturday
const MON = ['ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.', 'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.'];
const add = (iso, n) => { const d = new Date(iso + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const span = (a, b) => { const x = new Date(a + 'T00:00:00Z'), y = new Date(b + 'T00:00:00Z');
  return x.getUTCMonth() === y.getUTCMonth() ? `${x.getUTCDate()}–${y.getUTCDate()} ${MON[y.getUTCMonth()]}` : `${x.getUTCDate()} ${MON[x.getUTCMonth()]}–${y.getUTCDate()} ${MON[y.getUTCMonth()]}`; };
// W27 (29 มิ.ย.) .. W43 (19 ต.ค.), newest first, as backend periods.iso_weeks
const WEEKS = Array.from({ length: 17 }, (_, i) => {
  const from = add('2026-10-19', -7 * i), to = add(from, 6), ct = to > LATEST ? LATEST : to, w = 43 - i;
  return { key: `2026-W${w}`, year: 2026, week: w, from, to, closed_to: ct, days: ct === to ? 7 : 6, partial: ct !== to,
    label: `W${w} · ${span(from, ct)}${ct !== to ? ' (ถึงวันที่ปิด)' : ''}` };
});
const th = (a, b) => `${span(a, b)} 2569`;

function lmdi(p1, n1, p0, n0) {
  const L = Math.abs(p1 - p0) < 1e-9 ? p1 : (p1 - p0) / (Math.log(p1) - Math.log(p0));
  const c = L * Math.log(n1 / n0), h = L * Math.log((p1 / n1) / (p0 / n0));
  return { total: p1 - p0, total_pct: Math.round((p1 - p0) / p0 * 1000) / 10, customers: c, plates_per_head: h, main: Math.abs(c) >= Math.abs(h) ? 'customers' : 'plates_per_head' };
}
function platesRow(name, n1, n0, k) {
  const cells = {};
  GROUPS.forEach(([s], i) => {
    const h1 = (i ? 0.5 : 11.06) * k, h0 = (i ? 0.52 : 10.96), p1 = Math.round(n1 * h1), p0 = Math.round(n0 * h0);
    cells[s] = { plates: p1, plates_per_head: h1, prev: { plates: p0, plates_per_head: h0 },
      change: { plates: Math.round((p1 - p0) / p0 * 1000) / 10, plates_per_head: Math.round((h1 - h0) / h0 * 1000) / 10 }, cause: lmdi(p1, n1, p0, n0) };
  });
  return { branch: name, customers: n1, prev_customers: n0, change: { customers: Math.round((n1 - n0) / n0 * 1000) / 10 }, cells };
}
function platesAnswer(url) {
  const u = new URL(url), q = k => u.searchParams.get(k);
  const cur = q('period') === 'range' ? [q('date_from'), q('date_to')] : ['2026-10-19', LATEST];
  const base = q('base_from') ? [q('base_from'), q('base_to')] : ['2026-10-12', '2026-10-17'];
  return { period: { kind: 'range', cur, prev: base, label_th: th(...cur), prev_label_th: th(...base), anchor: LATEST, has_prev: true, reasons: {} },
    base: { kind: q('base_from') ? 'range' : 'prev', from: base[0], to: base[1], label_th: th(...base) },
    groups: GROUPS.map(([slug, label]) => ({ slug, label })), reasons: {}, weeks: WEEKS, latest_day: LATEST,
    chain: Object.assign(platesRow('', 17659, 17652, 1.0), { branch: undefined }),
    branches: [platesRow('ขอนแก่น', 7598, 7000, 1.05), platesRow('บุรีรัมย์', 2817, 3300, 0.97), platesRow('นครปฐม', 3878, 3700, 1.1)] };
}
function spikeAnswer(url) {
  const u = new URL(url);
  return { period: u.searchParams.get('period') || 'day', date_from: '2026-10-19', date_to: LATEST, date: LATEST, latest_day: LATEST,
    spikes: [], count: 0, new_menus: [], new_count: 0, spikes_plates: [], plates_count: 0, plates_only_count: 0,
    base: { kind: 'normal', from: '2026-09-28', to: '2026-10-18' }, summary: { customers: 5000 }, top: 15, cost_view: false };
}
function hvmAnswer() {
  const cell = k => ({ plates: 100, cost: 1500, uncosted_plates: 0, cost_per_head: 15 * k, plates_per_head: 1, cost_per_plate: 15, share_of_food_cost: 30,
    prev: { plates: 96, cost: 1400, cost_per_head: 14.5, plates_per_head: 0.96, cost_per_plate: 14.6, share_of_food_cost: 29 }, ly: null,
    change: { prev: { cost_per_head: 3.4, plates_per_head: 4.2, cost_per_plate: 2.7 }, ly: {} },
    cause: { prev: { total: 100, total_pct: 7.1, customers: 20, plates_per_head: 50, cost_per_plate: 30, main: 'plates_per_head' }, ly: { total: null } } });
  const row = (name, cust, k) => ({ branch: name, customers: cust, prev_customers: cust - 40, food_cost: cust * 400, food_cost_per_head: 400, prev_food_cost_per_head: 391,
    ly_food_cost_per_head: null, food_change: { prev: 2.3, ly: null }, cells: Object.fromEntries(GROUPS.map(([s]) => [s, cell(k)])) });
  return { period: { kind: 'month', cur: ['2026-10-01', LATEST], prev: ['2026-09-01', '2026-09-24'], days: 24, complete: false, label_th: '1–24 ต.ค. 2569',
    prev_label_th: '1–24 ก.ย. 2569', anchor: LATEST, has_prev: true, reasons: {} },
  groups: GROUPS.map(([slug, label]) => ({ slug, label })), chain: Object.assign(row('', 2000, 1), { branch: undefined }),
  branches: [row('ขอนแก่น', 1000, 1.1), row('บุรีรัมย์', 1000, 0.9)], reasons: {} };
}

async function login(ctx) {
  if (process.env.E2E_USER && process.env.E2E_PASS) {
    const r = await ctx.request.post('/api/auth/login', { data: { username: process.env.E2E_USER, password: process.env.E2E_PASS } });
    expect(r.ok()).toBe(true);
  }
}

async function open(browser, opts, { tab, mode = 'fit', scheme = 'light', slug = 'salmon', view = 'baht' }) {
  const ctx = await browser.newContext({ ...opts, colorScheme: scheme });
  await login(ctx);
  await ctx.addInitScript(([m, t, d, s, v]) => {
    try {
      if (sessionStorage.getItem('hvw_init')) return;          // a reload keeps what the page remembered
      sessionStorage.setItem('hvw_init', '1');
      localStorage.setItem('oshinei_phmode', JSON.stringify({ [t]: m })); localStorage.setItem('oshinei_dark', d ? '1' : '0');
      localStorage.setItem('oshinei_spike_period', JSON.stringify({ m: 'week', f: '', t: '' })); localStorage.setItem('oshinei_sphv_slug', s);
      localStorage.removeItem('oshinei_hvp_weeks'); localStorage.setItem('oshinei_hvm_view', v);
      localStorage.setItem('oshinei_hvm_slug', s); localStorage.setItem('oshinei_gperiod', JSON.stringify({ kind: 'month', pick: '', from: '', to: '' }));
    } catch (e) { /* none */ }
  }, [mode, tab, scheme === 'dark', slug, view]);
  const urls = [];
  const watch = (pat, body) => ctx.route(pat, r => { urls.push(r.request().url());
    return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body(r.request().url())) }); });
  await watch(/\/api\/sales\/ingredient-groups-plates\?/, platesAnswer);
  await watch(/\/api\/sales\/menu-spike\?/, spikeAnswer);
  await watch(/\/api\/cost\/ingredient-groups\?/, hvmAnswer);
  const page = await ctx.newPage();
  page.on('request', r => { if (/\/api\/cost\/ingredient-groups\?/.test(r.url()) && !urls.includes(r.url())) urls.push(r.url()); });
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' && !IGNORE.some(re => re.test(m.text()))) errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  await page.goto('/', { waitUntil: 'networkidle' });
  await go(page, opts, tab);
  return { ctx, page, errors, urls };
}
async function go(page, opts, tab, again) {
  // a phone may load the page once more right after a reload (the phone-first boot): ask again on the new document
  if (!again) { try { return await go(page, opts, tab, true); } catch (e) { if (!/Execution context was destroyed/.test(String(e))) throw e; await page.waitForLoadState('networkidle'); } }
  if (opts.isMobile) {
    await page.waitForFunction(() => document.body.classList.contains('ph-on') && typeof phOpenPage === 'function', null, { timeout: 60_000 });
    await page.evaluate(async t => { await phOpenPage(() => switchTab(t)); }, tab);
  } else {
    await page.waitForFunction(() => typeof switchTab === 'function', null, { timeout: 60_000 });
    await page.evaluate(t => switchTab(t), tab);
  }
  if (tab === 'dailysales') await page.waitForFunction(() => !!document.querySelector('#dsSpike .sp-sec-old'), null, { timeout: 60_000 });   // DV3: the card, no plates section
  if (tab === 'hvmonitor') await page.waitForFunction(() => !!document.querySelector('#hvmTable, #hvmAll, #hvpTable, #hvpMatrix'), null, { timeout: 60_000 });
  await page.waitForTimeout(900);
}
async function shot(page, name, sel) {
  if (!SHOTS) return;
  if (sel) await page.locator(sel).first().evaluate(e => e.scrollIntoView({ block: 'start' }));
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: false });
}
const lastPlates = urls => new URL(urls.filter(u => u.includes('ingredient-groups-plates')).pop() || 'http://x/');

// the chip rows: no sideways scroll, every chip on screen, 44 px to tap, drawn ~32 px
const CHIPS = sel => {
  const bs = [...document.querySelectorAll(sel)];
  const sc = bs[0] && bs[0].closest('.sp-hv-sc, .hvm-ctl');
  if (sc) sc.scrollIntoView({ block: 'center' });
  const vw = document.documentElement.clientWidth;
  const miss = [];
  bs.forEach(b => {
    const r = b.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    [-21, 21].forEach(dy => { const e = document.elementFromPoint(cx, cy + dy); if (!e || e.closest('.gran-btn, .hvm-csv') !== b) miss.push(`${b.textContent}:${dy}:${e ? e.className : 'none'}`); });
  });
  return { rows: new Set(bs.map(b => Math.round(b.getBoundingClientRect().top))).size, h: Math.max(...bs.map(b => b.getBoundingClientRect().height)),
    off: bs.filter(b => { const r = b.getBoundingClientRect(); return r.left < 0 || r.right > vw + 0.5; }).map(b => b.textContent),
    clipped: bs.filter(b => { const rg = document.createRange(); rg.selectNodeContents(b); return rg.getBoundingClientRect().width > b.getBoundingClientRect().width - 1; })
      .map(b => b.textContent),
    scroll: sc ? sc.scrollWidth - sc.clientWidth : 0, sw: document.documentElement.scrollWidth, miss };
};

const CASES = [['desktop', DESK, 'fit'], ['390 แตะง่าย', PHONE, 'touch'], ['390 พอดีจอ', PHONE, 'fit']];
for (const [name, opts, mode] of CASES) {
  for (const scheme of /** @type {const} */ (['light', 'dark'])) {
    test(`hvmonitor · จำนวนจาน · ${name} · ${scheme}: base values, the W vs W picker (last finished week first), chips that fit, larger figures`, async ({ browser }) => {
      test.setTimeout(150_000);
      const { ctx, page, errors, urls } = await open(browser, opts, { tab: 'hvmonitor', mode, scheme, view: 'plates' });
      const tag = `${name.replace(/\s+/g, '_')}_${scheme}`;
      const ph = !!opts.isMobile;
      // ── the picker: ตามแถบด้านบน first, then W labels (Mon–Sun), the running week to its closed day
      const wa = page.locator('#hvpWa');
      await expect(wa).toBeVisible();
      const optsA = await wa.locator('option').allTextContents();
      expect(optsA[0]).toBe('ตามแถบด้านบน');
      expect(optsA[1]).toBe('W43 · 19–24 ต.ค. (ถึงวันที่ปิด)');
      expect(optsA[2]).toBe('W42 · 12–18 ต.ค.');
      expect(optsA.slice(1).every(t => /^W\d{1,2} · /.test(t))).toBe(true);
      // a week by default: the last finished one (W42) vs the week before (W41), whole weeks
      await expect(page.locator('#hvpVs')).toHaveText('W42 (12–18 ต.ค.) เทียบ W41 (5–11 ต.ค.)');
      expect(await wa.inputValue()).toBe('2026-10-12');
      await expect(page.locator('#hvpWb')).toHaveValue('2026-10-05');
      let u = lastPlates(urls);
      expect([u.searchParams.get('period'), u.searchParams.get('date_from'), u.searchParams.get('date_to'), u.searchParams.get('base_from'), u.searchParams.get('base_to')])
        .toEqual(['range', '2026-10-12', '2026-10-18', '2026-10-05', '2026-10-11']);
      // ── เดิม under every figure (table): จานที่ออก · จาน/หัว · ลูกค้า
      const b0 = await page.evaluate(() => {
        const tb = /** @type {HTMLTableElement} */ (document.getElementById('hvpTable')), r = tb.tBodies[0].rows[0];
        return [3, 4, 5].map(i => (r.cells[i].querySelector('.hvm-d') || {}).textContent || '');
      });
      expect(b0[0]).toMatch(/^▲\d+\.\d% · เดิม 193,466$/);
      expect(b0[1]).toMatch(/· เดิม 10\.96$/);
      expect(b0[2]).toMatch(/· เดิม 17,652$/);
      // ── the figures are bigger: >= 16 px on a phone (15 px desktop), the base line >= 12 px
      const fs = await page.evaluate(() => {
        const px = s => [...document.querySelectorAll(s)].filter(e => e.getClientRects().length).map(e => parseFloat(getComputedStyle(e).fontSize));
        return { v: Math.min(...px('#hvpTable td.hvm-num .hvm-v:not(.hvm-cum)')), d: Math.min(...px('#hvpTable .hvm-d, #hvpTable .hvm-b')), n: Math.min(...px('#hvpTable td.hvm-name')),
          small: [...document.querySelectorAll('#hvpTable td, #hvpTable td *')].filter(e => e.textContent.trim() && e.getClientRects().length)
            .map(e => parseFloat(getComputedStyle(e).fontSize)).filter(x => x < 12).length };
      });
      if (mode !== 'touch' || !ph) {
        expect(fs.v).toBeGreaterThanOrEqual(ph ? 16 : 15);
        expect(fs.d).toBeGreaterThanOrEqual(12);
        expect(fs.n).toBeGreaterThanOrEqual(13);
        expect(fs.small, 'text >= 12 px at 100 %').toBe(0);
        // contrast of the change and the base (light / dark)
        const k = await page.evaluate(() => {
          const rgb = s => { const n = String(s).match(/[\d.]+/g) || ['0', '0', '0']; return [+n[0], +n[1], +n[2], n[3] == null ? 1 : +n[3]]; };
          const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
          const bgOf = el => { for (let e = el; e; e = e.parentElement) { const c = rgb(getComputedStyle(e).backgroundColor); if (c[3] >= 0.5) return c; } return [255, 255, 255]; };
          return Math.min(...[...document.querySelectorAll('#hvpTable .hvm-d > span, #hvpTable .hvm-v')].filter(e => e.getClientRects().length).map(e => {
            const a = lum(rgb(getComputedStyle(e).color)), b = lum(bgOf(e)); return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05); }));
        });
        expect(k).toBeGreaterThanOrEqual(4.5);
      }
      // ── the chips: desktop one row; a phone wraps them (5 + 5 with CSV), nothing off screen, no sideways scroll, 44 px to tap
      const c = await page.evaluate(CHIPS, '#hvmChips .gran-btn');
      if (ph) {
        expect(c.rows, 'the chips wrap').toBe(2);
        expect(c.off, 'every chip on screen').toEqual([]);
        expect(c.clipped, 'no chip label cut').toEqual([]);
        expect(c.sw).toBeLessThanOrEqual(390);
        if (mode === 'touch') { expect(c.miss, '44 px to tap').toEqual([]); expect(c.h).toBeLessThanOrEqual(34); }
      } else {
        expect(c.rows).toBe(1);
      }
      if (mode === 'touch' && ph) {                // the touch cards carry the base too
        const card = page.locator('#tab-hvmonitor .ph-cl-card').first();
        await expect(card).toBeVisible();
        expect(await card.textContent()).toContain('เดิม');
      }
      await shot(page, `hvp_salmon_W42vsW41_${tag}`, '#hvmView');
      // ── choose W43 (still running, จ.–ส.): the base is W42 on the same weekdays
      await wa.selectOption('2026-10-19');
      await expect(page.locator('#hvpVs')).toHaveText('W43 (จ.–ส.) เทียบ W42 (จ.–ส.)');
      u = lastPlates(urls);
      expect([u.searchParams.get('period'), u.searchParams.get('date_from'), u.searchParams.get('date_to'), u.searchParams.get('base_from'), u.searchParams.get('base_to')])
        .toEqual(['range', '2026-10-19', '2026-10-24', '2026-10-12', '2026-10-17']);
      const wb = page.locator('#hvpWb');
      await expect(wb).toBeVisible();
      expect(await wb.locator('option[value="2026-10-19"]').evaluate(o => /** @type {HTMLOptionElement} */ (o).disabled)).toBe(true);   // never itself
      // "w1 vs w7": W43 vs W37, to date
      await wb.selectOption('2026-09-07');
      await expect(page.locator('#hvpVs')).toHaveText('W43 (จ.–ส.) เทียบ W37 (จ.–ส.)');
      u = lastPlates(urls);
      expect([u.searchParams.get('base_from'), u.searchParams.get('base_to')]).toEqual(['2026-09-07', '2026-09-12']);
      await page.waitForFunction(() => !!document.getElementById('hvpTable'));
      await shot(page, `hvp_salmon_W43vsW37_${tag}`, '#hvmView');
      // two finished weeks, the base the LATER one: whole weeks, the base value reads "เทียบ"
      await wa.selectOption('2026-08-31');
      await page.locator('#hvpWb').selectOption('2026-09-28');
      await expect(page.locator('#hvpVs')).toHaveText('W36 (31 ส.ค.–6 ก.ย.) เทียบ W40 (28 ก.ย.–4 ต.ค.)');
      u = lastPlates(urls);
      expect([u.searchParams.get('date_from'), u.searchParams.get('date_to'), u.searchParams.get('base_from'), u.searchParams.get('base_to')])
        .toEqual(['2026-08-31', '2026-09-06', '2026-09-28', '2026-10-04']);
      await page.waitForFunction(() => !!document.getElementById('hvpTable'));
      expect(await page.locator('#hvpTable tbody tr').first().locator('.hvm-b').first().textContent()).toMatch(/^ · เทียบ /);
      // ── ทั้งหมด: now, "▲x% · เดิม y" and the cause word in each cell
      await wa.selectOption('2026-10-19');
      await expect(page.locator('#hvpWb')).toHaveValue('2026-10-12');
      await page.locator('#hvmChips .gran-btn[data-g="all"]').click();
      await page.waitForFunction(() => !!document.getElementById('hvpMatrix'));
      const mx = await page.evaluate(() => {
        const tb = /** @type {HTMLTableElement} */ (document.getElementById('hvpMatrix')), r = tb.tBodies[0].rows[0];
        return { cell: (r.cells[1].querySelector('.hvm-d') || {}).textContent || '', cust: (r.cells[9].querySelector('.hvm-d') || {}).textContent || '' };
      });
      expect(mx.cell).toMatch(/^[▲▼]?\d+\.\d% · เดิม 10\.96$/);
      expect(mx.cust).toMatch(/· เดิม 17,652$/);
      await shot(page, `hvp_all_W43vsW42_${tag}`, '#hvmView');
      // ── remembered on this device: a reload keeps the pair
      await page.reload({ waitUntil: 'networkidle' });
      await go(page, opts, 'hvmonitor');
      await expect(page.locator('#hvpVs')).toHaveText('W43 (จ.–ส.) เทียบ W42 (จ.–ส.)');
      expect(lastPlates(urls).searchParams.get('base_from')).toBe('2026-10-12');
      // back to the top bar's period
      await page.locator('#hvpWa').selectOption('');
      await expect(page.locator('#hvpWb')).toHaveCount(0);
      await expect.poll(() => lastPlates(urls).searchParams.get('kind')).toBe('month');
      expect(lastPlates(urls).searchParams.get('base_from')).toBeNull();
      expect(errors).toEqual([]);
      await ctx.close();
    });
  }
}

// ── the page follows the top bar: its period and its branch, in both views (principle 4 of the decision-value programme) ──
for (const [name, opts] of [['desktop', DESK], ['390', PHONE]]) {
  test(`hvmonitor · ${name}: a period change and a branch change on the top bar reload both views with the new filters`, async ({ browser }) => {
    test.setTimeout(150_000);
    const { ctx, page, errors, urls } = await open(browser, opts, { tab: 'hvmonitor', mode: 'fit' });
    const lastCost = () => new URL(urls.filter(x => /\/api\/cost\/ingredient-groups\?/.test(x)).pop() || 'http://x/');
    expect(lastCost().searchParams.get('kind')).toBe('month');
    // the period: สัปดาห์ on the top bar -> the baht view asks for the week
    let n0 = urls.length;
    await page.evaluate(() => gPeriodSet({ kind: 'week' }));
    await expect.poll(() => urls.slice(n0).some(x => /\/api\/cost\/ingredient-groups\?/.test(x) && new URL(x).searchParams.get('kind') === 'week'), { timeout: 30_000 }).toBe(true);
    // the branch: the top bar's branch filter -> the baht view asks for that branch
    const br = await page.evaluate(() => { const s = /** @type {HTMLSelectElement} */ (document.getElementById('selBranch'));
      const o = [...s.options].find(x => x.value); return o ? o.value : ''; });
    expect(br, 'the fixture has a branch to choose').not.toBe('');
    n0 = urls.length;
    await page.evaluate(b => { const s = /** @type {HTMLSelectElement} */ (document.getElementById('selBranch')); s.value = b; s.dispatchEvent(new Event('change')); }, br);
    await expect.poll(() => urls.slice(n0).some(x => /\/api\/cost\/ingredient-groups\?/.test(x) && new URL(x).searchParams.get('branch') === br), { timeout: 30_000 }).toBe(true);
    expect(lastCost().searchParams.get('kind')).toBe('week');
    // จำนวนจาน: the period changed on the top bar, so the view follows it (kind=week) with the branch
    n0 = urls.length;
    await page.locator('#hvmView .gran-btn[data-v="plates"]').click();
    await expect.poll(() => urls.slice(n0).some(x => x.includes('ingredient-groups-plates')), { timeout: 30_000 }).toBe(true);
    let u = lastPlates(urls);
    expect([u.searchParams.get('kind'), u.searchParams.get('branch'), u.searchParams.get('base_from')]).toEqual(['week', br, null]);
    // a week pair keeps the branch; a branch change keeps the pair
    await page.locator('#hvpWa').selectOption('2026-10-12');
    u = lastPlates(urls);
    expect([u.searchParams.get('period'), u.searchParams.get('date_from'), u.searchParams.get('branch')]).toEqual(['range', '2026-10-12', br]);
    n0 = urls.length;
    await page.evaluate(() => { const s = /** @type {HTMLSelectElement} */ (document.getElementById('selBranch')); s.value = ''; s.dispatchEvent(new Event('change')); });
    await expect.poll(() => urls.slice(n0).some(x => x.includes('ingredient-groups-plates') && !new URL(x).searchParams.get('branch')), { timeout: 30_000 }).toBe(true);
    u = lastPlates(urls);
    expect([u.searchParams.get('period'), u.searchParams.get('date_from')]).toEqual(['range', '2026-10-12']);
    // a period change on the top bar: the view follows the bar again
    n0 = urls.length;
    await page.evaluate(() => gPeriodSet({ kind: 'month' }));
    await expect.poll(() => urls.slice(n0).some(x => x.includes('ingredient-groups-plates') && new URL(x).searchParams.get('kind') === 'month'), { timeout: 30_000 }).toBe(true);
    await expect(page.locator('#hvpWa')).toHaveValue('');
    expect(errors).toEqual([]);
    await ctx.close();
  });
}

// ── High Value Product Monitor: เดิม under every figure, the chips (+ CSV) wrap on a phone ──
for (const [name, opts, mode] of CASES) {
  test(`hvmonitor · ${name}: เดิม under each figure, chips that fit`, async ({ browser }) => {
    test.setTimeout(120_000);
    const { ctx, page, errors } = await open(browser, opts, { tab: 'hvmonitor', mode });
    const tag = name.replace(/\s+/g, '_');
    const t = await page.evaluate(() => {
      const tb = /** @type {HTMLTableElement} */ (document.getElementById('hvmTable')), r = tb.tBodies[0].rows[0];
      return [3, 4, 5, 6].map(i => (r.cells[i].querySelector('.hvm-b') || {}).textContent || '');
    });
    expect(t).toEqual([' · เดิม ฿14.5', ' · เดิม 1.0', ' · เดิม ฿14.6', ' · เดิม 29.0%']);
    const fs = await page.evaluate(() => Math.min(...[...document.querySelectorAll('#hvmTable td.hvm-num .hvm-v:not(.hvm-cum)')].map(e => parseFloat(getComputedStyle(e).fontSize))));
    expect(fs).toBeGreaterThanOrEqual(opts.isMobile ? 16 : 15);
    const c = await page.evaluate(CHIPS, '#hvmChips .gran-btn, .hvm-csv');
    if (opts.isMobile) {
      expect(c.rows).toBe(2);
      expect(c.off).toEqual([]);
      expect(c.clipped).toEqual([]);
      expect(c.sw).toBeLessThanOrEqual(390);
      if (mode === 'touch') expect(c.miss).toEqual([]);
    }
    await shot(page, `hvm_salmon_${tag}`);
    await page.locator('#hvmChips .gran-btn[data-g="all"]').click();
    await page.waitForFunction(() => !!document.getElementById('hvmAll'));
    expect(await page.locator('#hvmAll tbody tr').first().locator('.hvm-b').first().textContent()).toBe(' · เดิม ฿1.4K');
    await shot(page, `hvm_all_${tag}`);
    expect(errors).toEqual([]);
    await ctx.close();
  });
}
