// @ts-check
// Owner 2026-10-24 · ยอดขายรายวัน → เมนูพุ่งผิดปกติ on a 390 x 844 phone in พอดีจอ: after a branch is chosen the new-menu table was drawn over
// the existing-menu one (the fit frame let a 380 px scroll box go visible while it kept its height cap). Now:
//   * the two parts stack - the existing-menu part ends above the new-menu part, in พอดีจอ, แตะง่าย and on a desktop;
//   * one branch chosen: no สาขา column in either table, the branch shows once in the header chip; ทุกสาขา: the column is back.
// The menu-spike answer is mocked (60 spiking menus, 8 new ones) so the tall table exists whatever the fixture holds.
const { test, expect } = require('@playwright/test');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];

function spikeAnswer(url) {
  const u = new URL(url), br = u.searchParams.get('branch') || '';
  const names = br ? [br] : ['สาขาเอ', 'สาขาบี', 'สาขาซี'];
  const spikes = Array.from({ length: 60 }, (_, i) => ({ branch: names[i % names.length], menu: `เมนูเดิม ${i + 1}`, today: 120 - i, per_day: 120 - i,
    expected: 50, excess: 70 - i, x: 2.4, pct: 140, thin_base: false }));
  const new_menus = Array.from({ length: 8 }, (_, i) => ({ branch: names[i % names.length], menu: `เมนูใหม่ ${i + 1}`, first_sold: '2026-10-15',
    first_sold_th: '15 ต.ค.', age_days: 5, qty: 40 - i, per_day: 8, trend: null }));
  return { period: 'week', date_from: '2026-10-19', date_to: '2026-10-23', latest_day: '2026-10-23', spikes, count: 60, new_menus, new_count: 8 };
}

async function login(page) {
  if (process.env.E2E_USER && process.env.E2E_PASS) {
    await page.goto('/');
    await page.evaluate(async ([u, p]) => (await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: u, password: p }) })).status,
      [process.env.E2E_USER, process.env.E2E_PASS]);
  }
}

async function open(browser, opts, mode) {
  const ctx = await browser.newContext(opts);
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' && !IGNORE.some(re => re.test(m.text()))) errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  await page.route('**/api/sales/menu-spike?*', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(spikeAnswer(r.request().url())) }));
  await login(page);
  await page.goto('/');
  await page.evaluate(m => { try { localStorage.setItem('oshinei_phmode', JSON.stringify({ dailysales: m })); localStorage.setItem('oshinei_spike_period', JSON.stringify({ m: 'week', f: '', t: '' })); } catch (e) { /* none */ } }, mode);
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
  await page.waitForFunction(br => { const r = document.getElementById('spikeRange'); return !!document.querySelector('#dsSpike #spikeNewTable') && !!r && (br ? r.textContent.startsWith(br) : !/·.*·/.test(r.textContent)); }, v, { timeout: 30_000 });
  await page.waitForTimeout(900);                 // the fit frame follows a re-render after 250 ms
  return v;
}

const READ = () => {
  // the PAINTED box: an element's rect clipped by every ancestor inside the card that clips (overflow other than visible) - a table that overflows a box
  // it does not clip paints past that box's own rect, which is how the new-menu table ended up on top of the existing-menu rows
  const box = s => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(); if (r.height < 1) return null; let top = r.top, bottom = r.bottom;
    for (let p = e.parentElement; p && p.id !== 'dsSpike'; p = p.parentElement) {   // inside the card: the page's own scroll does not count
      if (getComputedStyle(p).overflowY !== 'visible') { const q = p.getBoundingClientRect(); top = Math.max(top, q.top); bottom = Math.min(bottom, q.bottom); } }
    return { top, bottom }; };
  const heads = id => [...document.querySelectorAll(`#${id} thead th`)].map(th => th.textContent.trim());
  return { old: box('#spikeTable') || box('.sp-sec-old'), neu: box('#spikeNewTable') || box('.sp-sec-new'), oldHeads: heads('spikeTable'), newHeads: heads('spikeNewTable'),
    chip: (document.getElementById('spikeRange') || {}).textContent || '', fit: document.body.classList.contains('ph-fit') };
};

for (const [name, opts, mode] of [['พอดีจอ 390', PHONE, 'fit'], ['แตะง่าย 390', PHONE, 'touch'], ['desktop', { viewport: { width: 1440, height: 900 } }, 'fit']]) {
  test(`เมนูพุ่งผิดปกติ (${name}): the two parts stack after a branch change; one branch = no สาขา column, the branch in the chip`, async ({ browser }) => {
    test.setTimeout(150_000);
    const { ctx, page, errors } = await open(browser, opts, mode);
    if (name.startsWith('พอดีจอ')) expect(await page.evaluate(() => document.body.classList.contains('ph-fit'))).toBe(true);
    for (const which of [true, false, true]) {   // choose a branch, back to ทุกสาขา, a branch again: every re-render
      const br = await chooseBranch(page, which);
      const m = await page.evaluate(READ);
      expect(m.old && m.neu, 'both tables drawn').toBeTruthy();
      expect(m.old.bottom, `the existing-menu table is painted above the new-menu table (${which ? br : 'ทุกสาขา'})`).toBeLessThanOrEqual(m.neu.top + 0.5);
      if (which) {
        expect(m.oldHeads).not.toContain('สาขา');
        expect(m.newHeads).not.toContain('สาขา');
        expect(m.chip.startsWith(br + ' · ')).toBe(true);
      } else {
        expect(m.oldHeads).toContain('สาขา');
        expect(m.newHeads).toContain('สาขา');
        expect(m.chip).not.toContain(' · ');
      }
      if (name.startsWith('แตะง่าย')) {           // the cards: the branch sub-line only for ทุกสาขา
        const sub = await page.evaluate(() => (document.querySelector('#dsSpike .ph-cl') || { innerText: '' }).innerText);
        if (sub) expect(/สาขาเอ|สาขาบี|สาขาซี/.test(sub)).toBe(!which);
      }
    }
    // the page never scrolls sideways on a phone
    if (opts.isMobile) expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    expect(errors).toEqual([]);
    await ctx.close();
  });
}
