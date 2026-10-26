// @ts-check
// Owner 2026-10-25 · High Value Product Monitor (tab hvmonitor): the cost per head of 8 key ingredients at every branch.
//   * 9 compact chips (ทั้งหมด + 8 groups), one period control on the page (the top bar);
//   * DV4 (phone table review 2026-10-25): the answer line first (the change in baht split ราคา · กิน · ลูกค้า);
//   * one group: the comparison table, chain row on top (bold), sorted by the change in baht, the change + its main cause in column 2, สะสม;
//   * ทั้งหมด: one table of the 8 ingredients in baht (change, main cause, cost, share of food cost, สาขาที่ดัน), sorted by the change;
//   * a row opens the sheet with now / prev / ly and the menus; desktop, 390 แตะง่าย (cards) and พอดีจอ (the table), light / dark; no console error.
// The answer is mocked so the case exists whatever the fixture holds (HVM_LIVE=1 reads the server's own data instead).
const { test, expect } = require('@playwright/test');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];
const SHOTS = process.env.HVM_SHOTS || '';
const LIVE = process.env.HVM_LIVE === '1';
const GROUPS = [['salmon', 'แซลมอน'], ['tuna', 'ทูน่า'], ['hamachi', 'ฮามาจิ'], ['amaebi', 'กุ้งหวาน'], ['scallop', 'หอยเชลล์'], ['eel', 'ปลาไหล'], ['oyster', 'หอยนางรม'], ['beef', 'เนื้อ']];

function cell(cust, pph, cpp, base) {
  const plates = cust * pph, cost = plates * cpp;
  const b = base || null;
  const pct = (x, y) => (y ? Math.round((x - y) / y * 1000) / 10 : null);
  const c = { plates, cost, uncosted_plates: 3, cost_per_head: cost / cust, plates_per_head: pph, cost_per_plate: cpp, share_of_food_cost: 30 };
  if (!b) return Object.assign(c, { prev: null, ly: null, change: { prev: {}, ly: {} }, cause: { prev: { total: null, reason: 'ไม่มีข้อมูลช่วงที่เทียบ' }, ly: { total: null } } });
  const pc = b.cust * b.pph * b.cpp, L = Math.abs(cost - pc) < 1e-9 ? cost : (cost - pc) / (Math.log(cost) - Math.log(pc));
  const parts = { customers: L * Math.log(cust / b.cust), plates_per_head: L * Math.log(pph / b.pph), cost_per_plate: L * Math.log(cpp / b.cpp) };
  const main = Object.keys(parts).sort((x, y) => Math.abs(parts[y]) - Math.abs(parts[x]))[0];
  return Object.assign(c, {
    prev: { cost_per_head: pc / b.cust, plates_per_head: b.pph, cost_per_plate: b.cpp, share_of_food_cost: 28, plates: b.cust * b.pph, uncosted_plates: 1, cost: pc },
    ly: null,
    change: { prev: { cost_per_head: pct(cost / cust, pc / b.cust), plates_per_head: pct(pph, b.pph), cost_per_plate: pct(cpp, b.cpp) }, ly: {} },
    cause: { prev: Object.assign({ total: cost - pc, total_pct: pct(cost, pc), main }, parts), ly: { total: null, reason: 'ต้นทุนรายวันมีตั้งแต่ 22 มิ.ย. 2569' } },
  });
}
function row(name, cust, k) {
  const cells = {};
  GROUPS.forEach(([s], i) => { cells[s] = cell(cust, (i ? 0.5 : 9) * k, (i ? 20 : 15) * k, { cust: cust * 0.95, pph: (i ? 0.52 : 9.4), cpp: (i ? 19 : 14) }); });
  return { branch: name, customers: cust, food_cost: cust * 400 * k, food_cost_per_head: 400 * k, prev_food_cost_per_head: 395, ly_food_cost_per_head: null,
    food_change: { prev: Math.round((400 * k - 395) / 395 * 1000) / 10, ly: null }, cells };
}
function answer() {
  const branches = [row('ขอนแก่น', 7598, 1.0), row('บุรีรัมย์', 2817, 1.3), row('นครปฐม', 3878, 0.92), row('ร้อยเอ็ด', 3366, 1.05)];
  return { period: { kind: 'month', cur: ['2026-10-01', '2026-10-19'], prev: ['2026-09-01', '2026-09-19'], ly: ['2025-10-01', '2025-10-19'], days: 19, complete: false,
    label_th: '1–19 ต.ค. 2569', prev_label_th: '1–19 ก.ย. 2569', ly_label_th: '1–19 ต.ค. 2568', anchor: '2026-10-19', has_prev: true, has_ly: true,
    reasons: { ly: 'ต้นทุนรายวันมีตั้งแต่ 22 มิ.ย. 2569' } },
  groups: GROUPS.map(([slug, label]) => ({ slug, label })), chain: Object.assign(row('ทั้งเครือ', 17659, 1.02), { branch: undefined }), branches, reasons: {} };
}
const MENUS = { period: answer().period, branch: 'ขอนแก่น', slug: 'salmon', label: 'แซลมอน', reasons: {}, menus: [
  { sku_code: 'OTHBFSSM00010', name: '(B) ซาชิมิแซลมอน (1 ชิ้น ต่อ 1 เสิร์ฟ)', plates: 44801, cost: 434570, cost_per_plate: 9.7, uncosted_plates: 0, prev_plates: 47000, mixed: false, groups: ['salmon'] },
  { sku_code: 'OTHBFSPC00090', name: '(BF) ปลาแซลมอน ปลาฮามาจิ ปลาทูน่า', plates: 46, cost: 2300, cost_per_plate: 50, uncosted_plates: 0, prev_plates: 40, mixed: true, groups: ['salmon', 'tuna', 'hamachi'] }] };

async function login(ctx) {
  if (process.env.E2E_USER && process.env.E2E_PASS) {
    const r = await ctx.request.post('/api/auth/login', { data: { username: process.env.E2E_USER, password: process.env.E2E_PASS } });
    expect(r.ok()).toBe(true);
  }
}

async function open(browser, opts, mode, scheme, slug) {
  const ctx = await browser.newContext({ ...opts, colorScheme: scheme });
  await login(ctx);
  await ctx.addInitScript(([m, s, d]) => {
    try { localStorage.setItem('oshinei_phmode', JSON.stringify({ hvmonitor: m })); localStorage.setItem('oshinei_hvm_slug', s); localStorage.setItem('oshinei_dark', d ? '1' : '0'); } catch (e) { /* none */ }
  }, [mode, slug, scheme === 'dark']);
  if (!LIVE) {
    await ctx.route('**/api/cost/ingredient-groups/menus?*', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MENUS) }));
    await ctx.route(/\/api\/cost\/ingredient-groups\?/, r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(answer()) }));
  }
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' && !IGNORE.some(re => re.test(m.text()))) errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  await page.goto('/', { waitUntil: 'networkidle' });
  if (opts.isMobile) {
    await page.waitForFunction(() => document.body.classList.contains('ph-on') && typeof phOpenPage === 'function', null, { timeout: 60_000 });
    await page.evaluate(async () => { await phOpenPage(() => switchTab('hvmonitor')); });
  } else {
    await page.waitForFunction(() => typeof switchTab === 'function', null, { timeout: 60_000 });
    await page.evaluate(() => switchTab('hvmonitor'));
  }
  await page.waitForFunction(() => !!document.querySelector('#hvmTable, #hvmAll, #hvmCompact'), null, { timeout: 60_000 });
  // owner 2026-10-26: a phone opens on the compact list (hv-phone-compact.spec.js); these cases read the full list behind ดูทั้งหมด
  if (opts.isMobile && await page.locator('#hvmCompact').count()) { await page.locator('.hvc-all').click(); await page.waitForFunction(() => !!document.querySelector('#hvmTable, #hvmAll'), null, { timeout: 60_000 }); }
  await page.waitForTimeout(700);                 // the fit frame / the cards settle
  return { ctx, page, errors };
}

async function shot(page, name) {
  if (!SHOTS) return;
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: false });
}

const CASES = [
  ['desktop', {}, 'fit'],
  ['390 แตะง่าย', PHONE, 'touch'],
  ['390 พอดีจอ', PHONE, 'fit'],
];

for (const [name, opts, mode] of CASES) {
  for (const scheme of /** @type {const} */ (['light', 'dark'])) {
    test(`hvmonitor · ${name} · ${scheme}: answer line, one group, ทั้งหมด, sheet`, async ({ browser }) => {
      const { ctx, page, errors } = await open(browser, opts, mode, scheme, 'salmon');
      const tag = `${name.replace(/\s+/g, '_')}_${scheme}`;
      // one period control: the top bar; the page draws no period bar of its own
      expect(await page.locator('#tab-hvmonitor .pbar').count()).toBe(0);
      const chips = page.locator('#hvmChips .gran-btn');
      await expect(chips).toHaveCount(9);
      const ch = await chips.first().boundingBox();
      expect(ch && ch.height).toBeLessThanOrEqual(36);          // compact, not oversized
      const t = await page.evaluate(() => {
        const tb = document.getElementById('hvmTable');
        const first = tb && tb.tBodies[0].rows[0];
        const chg = first && first.cells[1];
        const ls = chg ? [...chg.querySelectorAll('[data-ph-line]')].map(e => e.textContent.trim()) : [];
        const tot = r => { const v = r.cells[1].querySelector('.hvm-v').textContent.trim(); const n = Number(v.replace(/[^\d.]/g, '')) * (/M/.test(v) ? 1e6 : /K/.test(v) ? 1e3 : 1); return v.startsWith('−') ? -n : n; };
        const br = tb ? [...tb.tBodies[0].rows].slice(1).filter(r => !r.hidden).map(tot) : [];
        const ans = document.getElementById('hvmAnswer');
        return { rows: tb ? tb.tBodies[0].rows.length : 0, chain: first ? first.cells[0].textContent.trim() : '', chainBold: first ? getComputedStyle(first.cells[0]).fontWeight : '',
          heads: tb ? [...tb.tHead.rows[0].cells].map(c => c.textContent.replace(/[↑↓]/g, '').trim()) : [], ls, sorted: br.every((v, i) => !i || br[i - 1] >= v),
          ans: ans ? ans.textContent.replace(/\s+/g, ' ').trim() : '', ansTop: ans ? ans.getBoundingClientRect().top : 9999, ansShown: !!ans && ans.getClientRects().length > 0,
          strip: [...document.querySelectorAll('.ph-ans[data-tab="hvmonitor"] .ph-ans-i')].map(li => li.textContent.replace(/\s+/g, ' ').trim()),
          tblTop: (/** @type {HTMLElement} */ (document.querySelector('#tab-hvmonitor .ph-cl')) || tb || document.body).getBoundingClientRect().top };   // touch: the cards replace the table
      });
      expect(t.rows).toBeGreaterThan(1);
      expect(t.chain).toBe('ทั้งเครือ');
      expect(Number(t.chainBold)).toBeGreaterThanOrEqual(700);
      expect(t.heads).toEqual(['สาขา', 'เปลี่ยน', 'สะสม', 'ต้นทุน/หัว', 'จาน/หัว', 'ต้นทุน/จาน', 'สัดส่วนในต้นทุนอาหาร']);
      expect(t.ls[0], 'column 2 = the change in baht').toMatch(/^[+−±]฿/);
      expect(t.ls[1], 'its main cause under it').toMatch(/^(ราคา|กิน|ลูกค้า) [+−±]฿/);
      expect(t.sorted, 'the branches by the change in baht').toBe(true);
      // the answer line: the ingredient, the change in baht and its three causes, above the table
      expect(t.ans).toMatch(/^แซลมอน [+−]฿[\d.]+[KM]? \([+−]\d+\.\d%\):/);
      expect(t.ans).toMatch(/ราคา [+−±]฿[\d.]+[KM]? · กิน [+−±]฿[\d.]+[KM]? · ลูกค้า [+−±]฿[\d.]+[KM]?/);
      expect(t.ans).toContain('1–19 ต.ค. 2569 เทียบ 1–19 ก.ย. 2569');
      if (opts.isMobile) {
        // a phone: the DV5 answer strip carries it (3 lines: the change and its causes, the branch, the trend); the box does not repeat it
        expect(t.strip.length).toBe(3);
        expect(t.strip[0]).toMatch(/^ทั้งเครือ · ต้นทุนที่เปลี่ยน\s*แซลมอน [+−]฿[\d.]+[KM]?\s*ราคา [+−±]฿[\d.]+[KM]? · กิน [+−±]฿[\d.]+[KM]? · ลูกค้า/);
        expect(t.strip[2]).toMatch(/^ต้นทุนอาหาร\/หัว/);
        expect(t.ansShown).toBe(false);
      } else {
        expect(t.strip).toEqual([]);
        expect(t.ansShown).toBe(true);
        expect(t.ansTop).toBeLessThan(t.tblTop);
      }
      if (mode === 'touch') {
        await expect(page.locator('#tab-hvmonitor .ph-cl-card').first()).toBeVisible();
        const figs = await page.locator('#tab-hvmonitor .ph-cl-card').first().locator('.ph-cl-fl').allTextContents();
        expect(figs).toEqual(['เปลี่ยน', 'สะสม', 'ต้นทุน/หัว']);
      } else {
        await expect(page.locator('#hvmTable')).toBeVisible();
        // text >= 12 px at 100 % (พอดีจอ shows the desktop layout zoomed to the screen; a double tap shows it at 100 %)
        const small = await page.evaluate(() => [...document.querySelectorAll('#hvmTable td, #hvmTable th button, #hvmTable td *')]
          .filter(e => e.textContent.trim() && e.getClientRects().length).map(e => parseFloat(getComputedStyle(e).fontSize)).filter(px => px < 12).length);
        expect(small).toBe(0);
      }
      await shot(page, `hvm_salmon_${tag}`);
      // sorted by เปลี่ยน; a tap on ต้นทุน/จาน sorts by it
      if (mode !== 'touch') {
        await expect(page.locator('#hvmTable th[aria-sort="descending"]')).toContainText('เปลี่ยน');
        await page.locator('#hvmTable th button', { hasText: 'ต้นทุน/จาน' }).click();
        await expect(page.locator('#hvmTable th[aria-sort="descending"]')).toContainText('ต้นทุน/จาน');
      }
      // the sheet of a row: now / prev / ly and the menus
      if (mode === 'touch') {
        await page.locator('#tab-hvmonitor .ph-cl-card .ph-cl-hit').nth(1).click();
        await page.locator('#msSheet .ph-rs-act button', { hasText: 'เปิดรายละเอียด' }).click();
      } else {
        await page.evaluate(() => /** @type {HTMLElement} */ (document.querySelector('#hvmTable tbody tr:nth-child(2) td')).click());
        // a phone in พอดีจอ opens the shell's row sheet first (every column); its เปิดรายละเอียด opens the page's own sheet
        if (opts.isMobile) await page.locator('#msSheet .ph-rs-act button', { hasText: 'เปิดรายละเอียด' }).click();
      }
      const sheet = page.locator('#uiHelpDlg.hvm-sheet');
      await expect(sheet).toBeVisible();
      await expect(sheet.locator('.hvm-fig').first()).toContainText('ช่วงก่อน');
      await expect(sheet.locator('#hvmMenus table')).toBeVisible({ timeout: 30_000 });
      await expect(sheet.locator('.hvm-cause .hvm-cause-part')).toHaveCount(3);
      await shot(page, `hvm_sheet_${tag}`);
      await page.keyboard.press('Escape');
      await expect(sheet).toHaveCount(0);
      // ทั้งหมด: one row per ingredient in baht, sorted by the change, สาขาที่ดัน under the name
      await page.locator('#hvmChips .gran-btn[data-g="all"]').click();
      await page.waitForFunction(() => !!document.getElementById('hvmAll'));
      const mx = await page.evaluate(() => {
        const tb = /** @type {HTMLTableElement} */ (document.getElementById('hvmAll'));
        const th = [...tb.tHead.rows[0].cells].map(c => c.textContent.replace(/[↑↓]/g, '').trim());
        const rows = [...tb.tBodies[0].rows];
        const v = r => { const s = r.cells[1].querySelector('.hvm-v').textContent.trim(); const n = Number(s.replace(/[^\d.]/g, '')) * (/M/.test(s) ? 1e6 : /K/.test(s) ? 1e3 : 1); return s.startsWith('−') ? -n : n; };
        const vs = rows.map(v), up = vs.filter(x => x > 0), dn = vs.filter(x => x <= 0);
        return { th, n: rows.length, push: tb.querySelectorAll('.hvm-push').length,
          order: vs.slice(0, up.length).every(x => x > 0) && up.every((x, i) => !i || up[i - 1] >= x) && dn.every((x, i) => !i || Math.abs(dn[i - 1]) >= Math.abs(x)),
          ans: document.getElementById('hvmAnswer').querySelectorAll('.hvm-ans-row').length,
          strip: [...document.querySelectorAll('.ph-ans[data-tab="hvmonitor"] .ph-ans-k')].map(k => k.textContent.trim()) };
      });
      expect(mx.th).toEqual(['วัตถุดิบ', 'เปลี่ยน', 'สาเหตุหลัก', 'ต้นทุน', 'สัดส่วนในต้นทุนอาหาร']);
      expect(mx.n).toBe(8);
      expect(mx.order, 'increases first, then decreases, each by size').toBe(true);
      expect(mx.push, 'สาขาที่ดัน under each ingredient').toBeGreaterThan(0);
      expect(mx.ans, 'ทั้งหมด: the two largest items').toBe(2);
      if (opts.isMobile) await expect.poll(() => page.evaluate(() => [...document.querySelectorAll('.ph-ans[data-tab="hvmonitor"] .ph-ans-k')].map(k => k.textContent.trim())))
        .toEqual(['ของแพงที่เปลี่ยนมากสุด', 'อันดับถัดไป', 'ต้นทุนอาหาร/หัว']);
      if (mode === 'touch') {
        const figs = await page.locator('#tab-hvmonitor .ph-cl-card').first().locator('.ph-cl-fl').allTextContents();
        expect(figs).toEqual(['เปลี่ยน', 'สาเหตุหลัก', 'ต้นทุน']);
      }
      // contrast >= 4.5:1 for the figures, the cause lines and สาขาที่ดัน (light and dark)
      const worst = await page.evaluate(() => {
        const rgb = s => { const m = String(s).match(/color\(srgb ([\d.]+) ([\d.]+) ([\d.]+)/); if (m) return [+m[1] * 255, +m[2] * 255, +m[3] * 255];
          const n = String(s).match(/[\d.]+/g) || ['0', '0', '0', '0']; return [+n[0], +n[1], +n[2], n[3] == null ? 1 : +n[3]]; };
        const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
        const bgOf = el => { for (let e = el; e; e = e.parentElement) { const c = rgb(getComputedStyle(e).backgroundColor); if (c.length < 4 || c[3] >= 0.5) return c; } return [255, 255, 255]; };
        const els = [...document.querySelectorAll('#hvmAll .hvm-v, #hvmAll .hvm-d span, #hvmAll .hvm-push, #hvmAnswer .hvm-part, #hvmAnswer .hvm-ans-v, #hvmAnswer .hvm-ans-s')].filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
        const r = els.map(e => { const a = lum(rgb(getComputedStyle(e).color)), b = lum(bgOf(e));
          return { k: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05), d: `${e.className} ${getComputedStyle(e).color} on ${bgOf(e).join(',')}` }; });
        return r.sort((x, y) => x.k - y.k)[0] || { k: 99, d: '' };
      });
      expect(worst.k, worst.d).toBeGreaterThanOrEqual(4.5);
      await page.waitForTimeout(400);
      await shot(page, `hvm_all_${tag}`);
      // a row opens its ingredient; a group chip switches too
      if (mode !== 'touch') {
        await page.evaluate(() => /** @type {HTMLElement} */ (document.querySelector('#hvmAll tbody tr[data-slug="beef"] td')).click());
        if (opts.isMobile && await page.locator('#msSheet .ph-rs-act button', { hasText: 'เปิดรายละเอียด' }).count()) await page.locator('#msSheet .ph-rs-act button', { hasText: 'เปิดรายละเอียด' }).click();
        await expect(page.locator('#hvmChips .gran-btn.gran-active')).toHaveText('เนื้อ');
      }
      await page.locator('#hvmChips .gran-btn[data-g="tuna"]').click();
      await page.waitForFunction(() => !!document.getElementById('hvmTable'));
      await expect(page.locator('#hvmChips .gran-btn.gran-active')).toHaveText('ทูน่า');
      expect(errors).toEqual([]);
      await ctx.close();
    });
  }
}
