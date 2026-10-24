// @ts-check
// PR-MB (owner 2026-10-24) · the card lists of a phone in แตะง่าย (touch), on a 390 x 844 phone:
//   * a page's daily table is one card per row: a name, up to three key figures, a chevron; the table's own box is hidden meanwhile;
//   * the sort chips (the name + the key figures) reorder the cards; the filter chips (the pill's values) narrow them;
//   * a card opens the row sheet with every column; a tap on the dim closes it;
//   * the text of the cards is >= 12 px, the chips and the cards are >= 44 px tall, the page never scrolls sideways;
//   * พอดีจอ (fit) takes the cards away and shows the table again.
// Four representative lists: GP รายสาขา, ยอดขายรายวัน, คิวใบสั่งซื้อ, ต้นทุนต่อจาน.
const { test, expect } = require('@playwright/test');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const PAGES = [{ tab: 'gpbranch' }, { tab: 'dailysales' }, { tab: 'orderhub', pane: 'queue' }, { tab: 'bomcenter', pane: 'dishcost', view: 'table' }];
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

// open the page in แตะง่าย (its remembered mode on this device) and wait for its card list
async function openTouch(page, p) {
  await page.evaluate(async (x) => {
    const k = x.tab + (x.pane ? '|' + x.pane : '') + (x.view ? '#' + x.view : ''), m = {}; m[k] = 'touch';
    try { localStorage.setItem('oshinei_phmode', JSON.stringify(m)); } catch (e) { /* none */ }
    await phOpenPage(() => x.pane ? supplyGo({ tab: x.tab, pane: x.pane }) : switchTab(x.tab));
  }, p);
  await page.waitForFunction(() => !document.body.classList.contains('ph-fit') && !!document.querySelector('.ph-cl .ph-cl-card, .ph-cl .ph-cl-empty'), null, { timeout: 60_000 });
  await page.waitForTimeout(1200);
}

for (const p of PAGES) {
  const name = p.tab + (p.pane ? '|' + p.pane : '');
  test(`แตะง่าย on ${name}: one card per row, sort and filter chips, a card opens its sheet, fit shows the table again`, async ({ browser }) => {
    test.setTimeout(150_000);
    const { ctx, page, errors } = await phoneShell(browser);
    await openTouch(page, p);
    const facts = await page.evaluate(() => {
      const l = /** @type {HTMLElement} */ (document.querySelector('.ph-cl')), src = /** @type {HTMLElement} */ (l.nextElementSibling);
      const cards = [...l.querySelectorAll('.ph-cl-card')];
      const zoomOf = e => { let z = 1; for (let q = e; q && q.nodeType === 1; q = q.parentElement) { const v = parseFloat(getComputedStyle(q).zoom); if (v && isFinite(v)) z *= v; } return z; };
      let min = 99;
      l.querySelectorAll('*').forEach(e => { if (![...e.childNodes].some(x => x.nodeType === 3 && x.textContent.trim())) return; const r = e.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return;
        if (e instanceof SVGElement) return; min = Math.min(min, parseFloat(getComputedStyle(e).fontSize) * zoomOf(e)); });
      const chips = [...l.querySelectorAll('.ph-cl-chip')].map(c => c.getBoundingClientRect().height);
      return { n: cards.length, empty: !!l.querySelector('.ph-cl-empty'), srcHidden: !!src && src.classList.contains('ph-cl-src') && getComputedStyle(src).display === 'none',
        titles: cards.map(c => (c.querySelector('.ph-cl-t') || {}).textContent || ''), figs: cards.map(c => c.querySelectorAll('.ph-cl-f').length),
        chev: cards.every(c => !!c.querySelector('.ph-cl-chev')), hitH: Math.min(...cards.map(c => c.querySelector('.ph-cl-hit').getBoundingClientRect().height)),
        chips, min: +min.toFixed(2), vw: innerWidth, doc: document.documentElement.scrollWidth };
    });
    expect(facts.srcHidden, 'the table box is hidden while its cards show').toBe(true);
    expect(facts.n, `${name} has cards in the fixture`).toBeGreaterThan(0);
    expect(facts.titles.every(t => t.trim().length > 0)).toBe(true);
    expect(facts.figs.every(n => n >= 1 && n <= 3)).toBe(true);
    expect(facts.chev).toBe(true);
    expect(facts.hitH).toBeGreaterThanOrEqual(44);
    expect(facts.min, 'card text is at least 12 px').toBeGreaterThanOrEqual(12);
    expect(facts.doc).toBeLessThanOrEqual(facts.vw);
    if (facts.chips.length) expect(Math.min(...facts.chips)).toBeGreaterThanOrEqual(44);

    // sort: the first key figure, largest first, then the other way
    if (facts.n > 1) {
      const sorted = async () => page.evaluate(() => [...document.querySelector('.ph-cl').querySelectorAll('.ph-cl-card:not(.ph-cl-tot)')]
        .map(c => _phNum((c.querySelector('.ph-cl-f .ph-cl-fv') || {}).textContent || '')).filter(v => v != null));
      await page.locator('.ph-cl').first().locator('.ph-cl-chip').nth(1).click();
      await expect(page.locator('.ph-cl').first().locator('.ph-cl-chip').nth(1)).toHaveAttribute('aria-pressed', 'true');
      const down = await sorted();
      expect(down).toEqual([...down].sort((a, b) => b - a));
      await page.locator('.ph-cl').first().locator('.ph-cl-chip').nth(1).click();
      const up = await sorted();
      expect(up).toEqual([...up].sort((a, b) => a - b));
    }
    // filter: the second filter chip (the first pill value) leaves only its cards
    const filt = page.locator('.ph-cl').first().locator('[aria-label="กรองตามกลุ่ม"] .ph-cl-chip');
    if (await filt.count() > 1) {
      await filt.nth(1).click();
      const ok = await page.evaluate(() => { const l = document.querySelector('.ph-cl'), want = (l.querySelector('[aria-label="กรองตามกลุ่ม"] .ph-cl-chip[aria-pressed="true"]') || {}).firstChild;
        const w = want ? String(want.textContent).trim() : ''; return [...l.querySelectorAll('.ph-cl-card:not(.ph-cl-tot) .ph-cl-pill')].every(pl => pl.textContent.trim().startsWith(w)); });
      expect(ok).toBe(true);
    }
    // a card opens the row sheet with every column; the dim closes it
    const title = (await page.locator('.ph-cl').first().locator('.ph-cl-card:not(.ph-cl-tot) .ph-cl-t').first().textContent() || '').trim();
    await page.locator('.ph-cl').first().locator('.ph-cl-card:not(.ph-cl-tot) .ph-cl-hit').first().click();
    await expect(page.locator('#msSheet.open.ph-rowsheet')).toBeVisible();
    expect((await page.locator('#msSheetTitle').textContent() || '').trim()).toBe(title.slice(0, 80));
    expect(await page.locator('#msSheetBody .ph-rs-row').count()).toBeGreaterThanOrEqual(2);
    await page.locator('#phSheetBack').click({ position: { x: 20, y: 20 } });
    await expect(page.locator('#msSheet.open')).toHaveCount(0);

    // พอดีจอ: the cards go, the table is the table again
    await page.evaluate(() => phModeToggle());
    await page.waitForFunction(() => document.body.classList.contains('ph-fit') && !document.querySelector('.ph-cl') && !document.querySelector('.ph-cl-src'), null, { timeout: 30_000 });
    expect(errors, errors.join('\n')).toEqual([]);
    await ctx.close();
  });
}
