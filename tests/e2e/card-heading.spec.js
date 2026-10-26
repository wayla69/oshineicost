// @ts-check
// Owner 2026-10-26 (phone, High Value Product Monitor, 390 px, both modes, light and dark): the card heading above บาท | จำนวนจาน
// ("ต้นทุนวัตถุดิบหลักต่อหัว · ทุกสาขา") read at ~6-7 px and its two "?" (ช่วงที่เทียบ, คำอธิบาย) floated apart from it.
// Root cause, shared by every card heading (.section-title): on a phone the heading was a 12 px label, and in พอดีจอ the frame scales the page by
// ~0.5 (12 px -> ~6 px) while the "?" are counter-zoomed back to touch size - big circles beside unreadable text, pushed apart by a page margin.
// Now a heading is 15 px on a phone, plain text flow (the words wrap, each "?" follows the last word), and in พอดีจอ it is counter-zoomed with its
// "?" as one unit. Checked here: the heading draws >= 14 px (computed size x every zoom above it), each "?" starts within 16 px of the text before
// it on the same line, the two "?" do not overlap their 44 px tap areas, nothing runs past the screen - at 390 and 360 px, both modes; plus a
// heading with one "?" on another page (ปิดร้าน/ข้อมูล, a title that wraps at 360 px); the desktop heading stays 13 px with the "?" beside it.
const { test, expect } = require('@playwright/test');

const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];
const PERIOD = { kind: 'month', cur: ['2026-10-01', '2026-10-24'], prev: ['2026-09-01', '2026-09-24'], ly: ['2025-10-01', '2025-10-24'], days: 24, complete: false,
  label_th: '1–24 ต.ค. 2569', prev_label_th: '1–24 ก.ย. 2569', ly_label_th: '1–24 ต.ค. 2568', anchor: '2026-10-24', has_prev: true, reasons: {} };
const GROUPS = [['salmon', 'แซลมอน'], ['tuna', 'ทูน่า']];
// the answer only needs its period (the "?" ช่วงที่เทียบ); no rows: the page says so under the heading
const HVM = { period: PERIOD, groups: GROUPS.map(([slug, label]) => ({ slug, label })), chain: null, branches: [], reasons: {} };

// the page has settled: the พอดีจอ frame has followed the last thing the page drew and nothing has changed for `quiet` ms (as hv-phone-compact)
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
async function open(browser, { tab, width = 390, mode = 'fit', desk = false, dark = false }) {
  const ctx = await browser.newContext(desk ? { viewport: { width: 1440, height: 900 } }
    : { viewport: { width, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  await login(ctx);
  await ctx.addInitScript(([t, m, d]) => {
    try {
      localStorage.setItem('oshinei_phmode', JSON.stringify({ [t]: m })); localStorage.setItem('oshinei_dark', d ? '1' : '0');
      localStorage.setItem('oshinei_hvm_slug', 'salmon'); localStorage.setItem('oshinei_hvm_view', 'baht');
      localStorage.setItem('oshinei_gperiod', JSON.stringify({ kind: 'month', pick: '', from: '', to: '' }));
    } catch (e) { /* none */ }
  }, [tab, mode, dark]);
  await ctx.route(/\/api\/cost\/ingredient-groups\?/, r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(HVM) }));
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' && !IGNORE.some(re => re.test(m.text()))) errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  await page.goto('/', { waitUntil: 'networkidle' });
  if (desk) {
    await page.waitForFunction(() => typeof switchTab === 'function', null, { timeout: 60_000 });
    await page.evaluate(t => switchTab(t), tab);
  } else {
    await page.waitForFunction(() => document.body.classList.contains('ph-on') && typeof phOpenPage === 'function', null, { timeout: 60_000 });
    await page.evaluate(async t => { await phOpenPage(() => switchTab(t)); }, tab);
  }
  return { ctx, page, errors };
}

// one heading: its drawn size, and each "?" against the text right before it (same line, gap), the "?" against each other, the right edge
const HEAD = /** @param {string} sel */ (sel) => {
  const h = /** @type {HTMLElement} */ (document.querySelector(sel));
  const zoomOf = e => { let z = 1; for (let p = e; p && p.nodeType === 1; p = p.parentElement) { const v = parseFloat(getComputedStyle(p).zoom); if (v && isFinite(v)) z *= v; } return z; };
  const qs = [...h.querySelectorAll('.ui-q')].filter(q => q.getClientRects().length);
  const text = [...h.querySelectorAll('*')].concat([h]).filter(e => [...e.childNodes].some(x => x.nodeType === 3 && x.textContent.trim()) && !e.closest('.ui-q'));
  const q = qs.map((b, i) => {
    const br = b.getBoundingClientRect();
    const rg = document.createRange(); rg.setStart(h, 0); rg.setEndBefore(b);
    const rects = [...rg.getClientRects()].filter(r => r.width > 0 && r.height > 0 && !(r.left >= br.left - 1 && r.top >= br.top - 1 && r.right <= br.right + 1));
    // the last text box before this "?": the right-most one on the lowest line
    const last = rects.reduce((a, r) => !a || r.bottom > a.bottom + 2 || (Math.abs(r.bottom - a.bottom) <= 2 && r.right > a.right) ? r : a, null);
    const prev = i ? qs[i - 1].getBoundingClientRect() : null;
    return { gap: last ? Math.round(br.left - last.right) : null, sameLine: !!last && br.top < last.bottom && br.bottom > last.top,
      w: Math.round(br.width), h: Math.round(br.height), apart: prev ? Math.round(br.left - prev.right) : null, right: Math.round(br.right) };
  });
  return { fs: Math.min(...text.map(e => parseFloat(getComputedStyle(e).fontSize) * zoomOf(e))), right: Math.round(h.getBoundingClientRect().right),
    q, help: qs.map(b => b.getAttribute('data-help') || b.getAttribute('aria-label')), vw: window.innerWidth, sw: document.documentElement.scrollWidth };
};

for (const width of [390, 360]) {
  for (const mode of ['touch', 'fit']) {
    test(`${width} ${mode} · the HVM card heading reads >= 14 px with its two "?" right after it`, async ({ browser }) => {
      test.setTimeout(120_000);
      const { ctx, page, errors } = await open(browser, { tab: 'hvmonitor', width, mode, dark: width === 360 });
      await page.waitForFunction(() => !!document.querySelector('#hvmRangeQ .ui-q'), null, { timeout: 60_000 });
      await settle(page);
      const r = await page.evaluate(HEAD, '#tab-hvmonitor h2.section-title');
      expect(r.fs, 'the heading drawn size (computed x zoom)').toBeGreaterThanOrEqual(14);
      expect(r.q.length, 'two "?": ช่วงที่เทียบ and คำอธิบาย (different help)').toBe(2);
      expect(new Set(r.help).size).toBe(2);
      const [a, b] = r.q;
      expect(a.sameLine, 'the first "?" on the line of the last word').toBe(true);
      expect(a.gap).toBeGreaterThanOrEqual(0);
      expect(a.gap, 'the first "?" right after the heading text').toBeLessThanOrEqual(16);
      expect(b.apart, 'the second "?" right after the first').toBeLessThanOrEqual(20);
      expect(b.apart, '44 px tap areas (8 px around each 28 px "?") never overlap').toBeGreaterThanOrEqual(16);
      for (const x of r.q) { expect(x.w).toBeLessThanOrEqual(30); expect(x.w).toBeGreaterThanOrEqual(24); expect(x.right).toBeLessThanOrEqual(r.vw); }
      expect(r.sw, 'no sideways scroll').toBeLessThanOrEqual(width + 1);
      // the "?" opens its own help
      await page.locator('#hvmHelpQ').click();
      await expect(page.locator('.ui-help-box')).toBeVisible();
      expect(errors).toEqual([]);
      await ctx.close();
    });

    test(`${width} ${mode} · a one-"?" heading on another page (ปิดร้าน/ข้อมูล) wraps with the "?" after its last word`, async ({ browser }) => {
      test.setTimeout(120_000);
      const { ctx, page, errors } = await open(browser, { tab: 'closetime', width, mode });
      await page.waitForFunction(() => [...document.querySelectorAll('#tab-closetime h2.section-title')].some(h => h.getClientRects().length && h.querySelector('.ui-q')), null, { timeout: 60_000 });
      await settle(page);
      const all = await page.evaluate(() => [...document.querySelectorAll('#tab-closetime h2.section-title')].map((h, i) => { h.setAttribute('data-e2e-h', String(i)); return h.getClientRects().length && !!h.querySelector('.ui-q'); }));
      let n = 0;
      for (let i = 0; i < all.length; i++) {
        if (!all[i]) continue;
        const r = await page.evaluate(HEAD, `[data-e2e-h="${i}"]`);
        expect(r.fs).toBeGreaterThanOrEqual(14);
        for (const x of r.q) { expect(x.sameLine).toBe(true); expect(x.gap).toBeGreaterThanOrEqual(0); expect(x.gap).toBeLessThanOrEqual(16); expect(x.right).toBeLessThanOrEqual(r.vw); }
        n++;
      }
      expect(n).toBeGreaterThan(0);
      expect(errors).toEqual([]);
      await ctx.close();
    });
  }
}

test('desktop · the HVM heading keeps its 13 px and the "?" beside it', async ({ browser }) => {
  test.setTimeout(120_000);
  const { ctx, page, errors } = await open(browser, { tab: 'hvmonitor', desk: true });
  await page.waitForFunction(() => !!document.querySelector('#hvmRangeQ .ui-q'), null, { timeout: 60_000 });
  const r = await page.evaluate(HEAD, '#tab-hvmonitor h2.section-title');
  expect(r.fs).toBeGreaterThanOrEqual(12);
  expect(r.fs).toBeLessThan(14);
  expect(r.q.length).toBe(2);
  expect(r.q[0].sameLine).toBe(true);
  expect(r.q[0].gap).toBeLessThanOrEqual(16);
  expect(r.q[1].apart).toBe(8);
  expect(errors).toEqual([]);
  await ctx.close();
});
