// @ts-check
// Owner 2026-10-24 · ยอดขายรายวัน → เมนูพุ่งผิดปกติ on a 390 x 844 phone in พอดีจอ: after a branch is chosen the new-menu table was drawn over
// the existing-menu one (the fit frame let a 380 px scroll box go visible while it kept its height cap). Now:
//   * the two parts stack - the existing-menu part ends above the new-menu part, in พอดีจอ, แตะง่าย and on a desktop;
//   * DV3 (2026-10-25): the rows are dishes - ทุกสาขา: สาขาที่ดัน under each menu, no สาขา column; one branch chosen: the branch's tier line
//     under each menu instead, the branch once in the header chip.
// The menu-spike answer is mocked (tests/e2e/spike-mock.js) so the tall table exists whatever the fixture holds.
const { test, expect } = require('@playwright/test');
const { dishAnswer } = require('./spike-mock');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];

// one navigation only: a second goto aborts the first page's boot requests, and the app logs each as "Failed to fetch".
async function login(ctx) {
  if (process.env.E2E_USER && process.env.E2E_PASS) {
    const r = await ctx.request.post('/api/auth/login', { data: { username: process.env.E2E_USER, password: process.env.E2E_PASS } });
    expect(r.ok()).toBe(true);
  }
}

async function open(browser, opts, mode) {
  const ctx = await browser.newContext(opts);
  await login(ctx);
  await ctx.addInitScript(m => { try { localStorage.setItem('oshinei_phmode', JSON.stringify({ dailysales: m }));
    localStorage.setItem('oshinei_spike_period_v2', JSON.stringify({ m: 'week', f: '', t: '' }));
    localStorage.setItem('oshinei_spike_all', '1'); localStorage.setItem('oshinei_spike_new_open', '1'); } catch (e) { /* none */ } }, mode);
  await ctx.route('**/api/sales/menu-spike?*', r => r.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify(dishAnswer(r.request().url(), { nDish: 40, nNew: 8 })) }));
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' && !IGNORE.some(re => re.test(m.text()))) errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  await page.goto('/', { waitUntil: 'networkidle' });
  if (opts.isMobile) {
    await page.waitForFunction(() => document.body.classList.contains('ph-on') && typeof phOpenPage === 'function', null, { timeout: 60_000 });
    await page.evaluate(async () => { await phOpenPage(() => switchTab('dailysales')); });
  } else {
    await page.waitForFunction(() => typeof switchTab === 'function', null, { timeout: 60_000 });
    await page.evaluate(() => switchTab('dailysales'));
  }
  await page.waitForFunction(() => !!document.querySelector('#dsSpike #spikeNewTable'), null, { timeout: 60_000 });
  return { ctx, page, errors };
}

async function chooseBranch(page, which) {
  const v = await page.evaluate(w => {
    const s = /** @type {HTMLSelectElement} */ (document.getElementById('spikeBranch'));
    if (w && s.options.length < 2) s.insertAdjacentHTML('beforeend', '<option value="สาขาทดสอบ">สาขาทดสอบ</option>');
    s.value = w ? s.options[1].value : ''; s.dispatchEvent(new Event('change')); return s.value;
  }, which);
  await page.waitForFunction(br => { const r = document.getElementById('spikeRange'), t = document.getElementById('spikeTable');
    return !!document.querySelector('#dsSpike #spikeNewTable') && !!t && t.dataset.view === (br ? 'branch' : 'chain') && !!r && (br ? r.textContent.startsWith(br) : !/·.*·/.test(r.textContent)); },
  v, { timeout: 30_000 });
  await page.waitForTimeout(900);                 // the fit frame follows a re-render after 250 ms
  return v;
}

const READ = () => {
  // the PAINTED box: an element's rect clipped by every ancestor inside the card that clips (overflow other than visible)
  const box = s => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(); if (r.height < 1) return null; let top = r.top, bottom = r.bottom;
    for (let p = e.parentElement; p && p.id !== 'dsSpike'; p = p.parentElement) {
      if (getComputedStyle(p).overflowY !== 'visible') { const q = p.getBoundingClientRect(); top = Math.max(top, q.top); bottom = Math.min(bottom, q.bottom); } }
    return { top, bottom }; };
  const heads = id => [...document.querySelectorAll(`#${id} thead th`)].map(th => th.textContent.trim());
  const subs = [...document.querySelectorAll('#spikeTable .sp-push')].map(e => e.textContent.trim());
  return { old: box('#spikeTable') || box('.sp-sec-old'), neu: box('#spikeNewTable') || box('.sp-sec-new'), oldHeads: heads('spikeTable'), newHeads: heads('spikeNewTable'),
    chip: (document.getElementById('spikeRange') || {}).textContent || '', push: subs.filter(t => t.startsWith('สาขาที่ดัน')).length,
    tier: subs.filter(t => t.startsWith('เพิ่มจาก tier')).length, rows: document.querySelectorAll('#spikeTable tbody tr').length };
};

for (const [name, opts, mode] of [['พอดีจอ 390', PHONE, 'fit'], ['แตะง่าย 390', PHONE, 'touch'], ['desktop', { viewport: { width: 1440, height: 900 } }, 'fit']]) {
  test(`เมนูพุ่งผิดปกติ (${name}): the two parts stack after a branch change; ทุกสาขา = สาขาที่ดัน, one branch = its tier line, the branch in the chip`, async ({ browser }) => {
    test.setTimeout(150_000);
    const { ctx, page, errors } = await open(browser, opts, mode);
    if (name.startsWith('พอดีจอ')) expect(await page.evaluate(() => document.body.classList.contains('ph-fit'))).toBe(true);
    for (const which of [true, false, true]) {   // choose a branch, back to ทุกสาขา, a branch again: every re-render
      const br = await chooseBranch(page, which);
      const m = await page.evaluate(READ);
      expect(m.old && m.neu, 'both tables drawn').toBeTruthy();
      expect(m.old.bottom, `the existing-menu table is painted above the new-menu table (${which ? br : 'ทุกสาขา'})`).toBeLessThanOrEqual(m.neu.top + 0.5);
      expect(m.oldHeads).not.toContain('สาขา');
      expect(m.newHeads).not.toContain('สาขา');
      expect(m.rows).toBe(40);
      if (which) {
        expect(m.push).toBe(0);
        expect(m.tier).toBe(40);
        expect(m.chip.startsWith(br + ' · ')).toBe(true);
      } else {
        expect(m.push).toBe(40);
        expect(m.tier).toBe(0);
        expect(m.chip).not.toContain(' · ');
      }
      if (name.startsWith('แตะง่าย')) {           // the cards: the grey line under the name follows the view
        const sub = await page.evaluate(() => { const c = document.querySelector('#dsSpike .ph-cl .ph-cl-card'); return c ? c.textContent : ''; });
        expect(sub).toContain(which ? 'เพิ่มจาก tier' : 'สาขาที่ดัน');
      }
    }
    if (opts.isMobile) expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    expect(errors).toEqual([]);
    await ctx.close();
  });
}
