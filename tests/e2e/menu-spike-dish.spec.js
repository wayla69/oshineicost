// @ts-check
// DV3 (phone table review 2026-10-25, top-10 #3) · ยอดขายรายวัน → เมนูพุ่งผิดปกติ by DISH:
//   * the chain view = one row per dish by its NET extra cost, สาขาที่ดัน under the name, the floor (the material rows first, ดูทั้งหมด (N)
//     for the rest, remembered), ต้นทุนเพิ่มสุทธิ with its % of the food cost, สะสม; a role without cost: +จาน, สูงกว่า, สะสม, no ฿;
//   * the touch cards lead with ต้นทุนเพิ่มสุทธิ · สะสม · +จาน; the strip's net tile;
//   * เมนูใหม่ per dish, FOLDED by default and remembered; its floor; กลับมาขาย N สาขา;
//   * the raw-plates section and วัตถุดิบมูลค่าสูง · จำนวนจาน are gone from the card; the CSV keeps the dish rows AND the per-branch / raw-plates rows;
//   * filters: สัปดาห์ by default; the card follows the top bar's branch (its select moves to it), its own select narrows the card alone, the
//     top bar wins again on its next change; a period chip reloads with the branch kept. Every case is checked on the request the card sends.
const { test, expect } = require('@playwright/test');
const { dishAnswer } = require('./spike-mock');

const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const DESK = { viewport: { width: 1440, height: 900 } };
const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];

async function login(ctx) {
  if (process.env.E2E_USER && process.env.E2E_PASS) {
    const r = await ctx.request.post('/api/auth/login', { data: { username: process.env.E2E_USER, password: process.env.E2E_PASS } });
    expect(r.ok()).toBe(true);
  }
}

async function open(browser, opts, { mode = 'fit', cost = true, mock = true, ls = {} } = {}) {
  const ctx = await browser.newContext(opts);
  await login(ctx);
  await ctx.addInitScript(([m, extra]) => { try {
    if (sessionStorage.getItem('dv3_init')) return;
    sessionStorage.setItem('dv3_init', '1');
    localStorage.setItem('oshinei_phmode', JSON.stringify({ dailysales: m }));
    localStorage.removeItem('oshinei_spike_period_v2'); localStorage.setItem('oshinei_spike_all', '0'); localStorage.removeItem('oshinei_spike_new_open');
    Object.entries(extra).forEach(([k, v]) => localStorage.setItem(k, v));
  } catch (e) { /* none */ } }, [mode, ls]);
  const urls = [];
  if (mock) await ctx.route('**/api/sales/menu-spike?*', r => { urls.push(r.request().url());
    return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(dishAnswer(r.request().url(), { cost })) }); });
  else await ctx.route('**/api/sales/menu-spike?*', r => { urls.push(r.request().url()); return r.continue(); });
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
  await page.waitForFunction(() => !!document.querySelector('#dsSpike .sp-sec-old'), null, { timeout: 120_000 });
  await page.waitForTimeout(1000);
  return { ctx, page, errors, urls };
}

const READ = () => {
  const t = document.getElementById('spikeTable');
  const rows = t ? [...t.tBodies[0].rows] : [];
  const baht = s => Number((String(s).match(/฿([\d,]+)/) || [0, '0'])[1].replace(/,/g, ''));
  return {
    heads: t ? [...t.tHead.rows[0].cells].map(c => c.textContent.trim().split(' ')[0]) : [],
    n: rows.length, menus: rows.map(r => (r.querySelector('.sp-menu') || {}).textContent),
    nets: rows.map(r => baht((r.querySelector('.sp-cost-td .sp-v') || { textContent: '' }).textContent)),
    push: rows.map(r => (r.querySelector('.sp-push') || { textContent: '' }).textContent.trim()),
    share: (rows[0] && rows[0].querySelector('.sp-cost-td .sp-perday') || { textContent: '' }).textContent.trim(),
    title: (document.querySelector('#dsSpike .sp-sec-old .sp-h') || { textContent: '' }).textContent.replace(/\s+/g, ' ').trim(),
    btn: (document.getElementById('spikeAllBtn') || { textContent: '' }).textContent.trim(),
    secs: [...document.querySelectorAll('#dsSpike > section')].map(s => s.className),
    tiles: [...document.querySelectorAll('#spikeStrip .sp-tile')].map(x => [...x.children].map(c => c.textContent.replace('?', '').trim()).join(' ').replace(/\s+/g, ' ')),
    hasBaht: !!t && /฿/.test(t.textContent), sw: document.documentElement.scrollWidth,
  };
};

for (const [name, opts, mode] of [['desktop', DESK, 'fit'], ['แตะง่าย 390', PHONE, 'touch']]) {
  test(`เมนูเดิม by dish (${name}): net extra cost order, สาขาที่ดัน, the floor + ดูทั้งหมด, no raw-plates or plates-of-ingredients section`, async ({ browser }) => {
    test.setTimeout(180_000);
    const { ctx, page, errors } = await open(browser, opts, { mode });
    const m = await page.evaluate(READ);
    expect(m.heads).toEqual(['#', 'เมนู', 'ต้นทุนเพิ่มสุทธิ', 'สะสม', '+จาน']);
    expect(m.n).toBe(12);                                         // the 12 material rows of 20 (dish_material_count)
    expect(m.nets).toEqual([...m.nets].sort((a, b) => b - a));
    expect(m.nets[0]).toBe(6000);
    expect(m.push[0]).toBe('สาขาที่ดัน: สาขาเอ ฿3.0K · สาขาบี ฿1.8K · สาขาซี ฿1.2K · อีก 2 สาขา');
    expect(m.share).toBe('0.6% ของต้นทุนอาหาร');
    expect(m.title).toContain('เมนูเดิม · ดันต้นทุนเพิ่มสุทธิ');
    expect(m.title).toContain('รวมทุกสาขา');
    expect(m.title).toContain('(12 ถึงเกณฑ์ · ทั้งหมด 20)');
    expect(m.btn).toBe('ดูทั้งหมด (20)');
    expect(m.secs.length).toBe(2);
    expect(m.secs.some(c => /sp-sec-plates|sp-sec-hv/.test(c))).toBe(false);
    expect(m.tiles[1]).toContain('เมนูที่ถึงเกณฑ์ 12 เมนู');
    expect(m.tiles[2]).toMatch(/^ต้นทุนเพิ่มสุทธิ \+฿[\d.]+K เพิ่ม ฿[\d.]+K · ลด ฿20\.0K$/);
    if (mode === 'touch') {
      const c = await page.evaluate(() => { const card = document.querySelector('#dsSpike .sp-sec-old .ph-cl .ph-cl-card');
        return card ? { labels: [...card.querySelectorAll('.ph-cl-fl')].map(x => x.textContent.trim()), txt: card.textContent } : null; });
      expect(c && c.labels).toEqual(['ต้นทุนเพิ่มสุทธิ', 'สะสม', '+จาน']);
      expect(c && c.txt).toContain('สาขาที่ดัน');
      expect(m.sw).toBeLessThanOrEqual(390);
    }
    // ดูทั้งหมด: every dish above its normal, remembered on this device
    await page.evaluate(() => document.getElementById('spikeAllBtn').click());
    expect(await page.evaluate(() => document.getElementById('spikeTable').tBodies[0].rows.length)).toBe(20);
    expect(await page.evaluate(() => localStorage.getItem('oshinei_spike_all'))).toBe('1');
    expect(await page.evaluate(() => document.getElementById('spikeAllBtn').textContent.trim())).toBe('แสดงเฉพาะที่ถึงเกณฑ์');
    expect(errors).toEqual([]);
    await ctx.close();
  });
}

test('เมนูเดิม by dish, a role without cost: +จาน · สูงกว่า · สะสม, no ฿ anywhere in the table', async ({ browser }) => {
  test.setTimeout(150_000);
  const { ctx, page, errors } = await open(browser, DESK, { cost: false });
  const m = await page.evaluate(READ);
  expect(m.heads).toEqual(['#', 'เมนู', '+จาน', 'สูงกว่า', 'สะสม']);
  expect(m.hasBaht).toBe(false);
  expect(m.push[0]).toBe('สาขาที่ดัน: สาขาเอ +50 จาน · สาขาบี +30 จาน · สาขาซี +20 จาน · อีก 2 สาขา');
  expect(m.tiles[2]).toContain('จานเพิ่มสุทธิ +600 จาน');
  expect(errors).toEqual([]);
  await ctx.close();
});

for (const [name, opts, mode] of [['desktop', DESK, 'fit'], ['แตะง่าย 390', PHONE, 'touch']]) {
  test(`เมนูใหม่ by dish (${name}): folded at first and remembered, the floor, กลับมาขาย N สาขา, ขาย N สาขา`, async ({ browser }) => {
    test.setTimeout(150_000);
    const { ctx, page, errors } = await open(browser, opts, { mode });
    expect(await page.evaluate(() => document.getElementById('spikeNewBody').hidden)).toBe(true);
    expect(await page.evaluate(() => document.querySelector('#dsSpike .sp-sec-new .sp-n').textContent.trim())).toBe('(3 ถึงเกณฑ์ · ทั้งหมด 4)');
    await page.evaluate(() => document.getElementById('spikeNewFold').click());
    expect(await page.evaluate(() => localStorage.getItem('oshinei_spike_new_open'))).toBe('1');
    await page.waitForTimeout(500);
    const m = await page.evaluate(() => {
      const t = document.getElementById('spikeNewTable'), r = t.tBodies[0].rows;
      return { n: r.length, heads: [...t.tHead.rows[0].cells].map(c => c.textContent.trim().split(' ')[0]),
        chip: (r[0].querySelector('.sp-back-chip') || { textContent: '' }).textContent.trim(), sub: (r[0].querySelector('.sp-push') || { textContent: '' }).textContent.trim(),
        btn: (document.getElementById('spikeNewAllBtn') || { textContent: '' }).textContent.trim(), sw: document.documentElement.scrollWidth };
    });
    expect(m.n).toBe(3);
    expect(m.heads).toEqual(['#', 'เมนู', 'ต้นทุน', 'จาน/วัน', 'ช่วงนี้', 'ต่อ', 'แนวโน้ม']);
    expect(m.chip).toBe('กลับมาขาย 2 สาขา');
    expect(m.sub).toBe('ขาย 2 สาขา: สาขาเอ 300 · สาขาบี 100');
    expect(m.btn).toBe('ดูทั้งหมด (4)');
    if (mode === 'touch') expect(m.sw).toBeLessThanOrEqual(390);
    // a redraw keeps it open; folding again is remembered too
    await page.evaluate(() => _spikeDraw(_spikeLast, _spikeOneBr));
    expect(await page.evaluate(() => document.getElementById('spikeNewBody').hidden)).toBe(false);
    await page.evaluate(() => document.getElementById('spikeNewFold').click());
    expect(await page.evaluate(() => localStorage.getItem('oshinei_spike_new_open'))).toBe('0');
    expect(errors).toEqual([]);
    await ctx.close();
  });
}

test('filters: สัปดาห์ by default; the card follows the top bar branch, its own select narrows the card, a period chip keeps the branch', async ({ browser }) => {
  test.setTimeout(240_000);
  const { ctx, page, errors, urls } = await open(browser, DESK);
  const last = () => new URL(urls[urls.length - 1]).searchParams;
  const settle = async n => { await expect.poll(() => urls.length, { timeout: 60_000 }).toBeGreaterThan(n);
    await page.waitForFunction(() => !/กำลังโหลด/.test(document.getElementById('dsSpike').textContent), null, { timeout: 60_000 }); await page.waitForTimeout(400); };
  // the first request: สัปดาห์ (no choice stored), ทุกสาขา, the chain view
  expect(last().get('period')).toBe('week');
  expect(last().get('branch')).toBe(null);
  expect(await page.evaluate(() => document.querySelector('#spikeModes .gran-active').dataset.m)).toBe('week');
  const names = await page.evaluate(() => [...document.getElementById('selBranch').options].map(o => o.value).filter(Boolean));
  expect(names.length).toBeGreaterThanOrEqual(2);
  const [a, b] = names;
  // 1 · the top bar's branch -> the card's request and its select follow it; the branch view (tier line, no สาขาที่ดัน)
  let n = urls.length;
  await page.evaluate(v => { const s = document.getElementById('selBranch'); s.value = v; s.dispatchEvent(new Event('change', { bubbles: true })); }, a);
  await settle(n);
  expect(last().get('branch')).toBe(a);
  expect(await page.evaluate(() => document.getElementById('spikeBranch').value)).toBe(a);
  expect(await page.evaluate(() => document.getElementById('spikeTable').dataset.view)).toBe('branch');
  expect(await page.evaluate(() => document.querySelector('#spikeTable .sp-push').textContent)).toContain('เพิ่มจาก tier');
  expect(await page.evaluate(() => document.getElementById('spikeRange').textContent)).toContain(a);
  // 2 · the card's own select -> only the card changes; the top bar keeps its branch
  n = urls.length;
  await page.evaluate(v => { const s = document.getElementById('spikeBranch'); s.value = v; s.dispatchEvent(new Event('change')); }, b);
  await settle(n);
  expect(last().get('branch')).toBe(b);
  expect(await page.evaluate(() => document.getElementById('selBranch').value)).toBe(a);
  // 3 · a period chip reloads with the card's branch kept
  n = urls.length;
  await page.evaluate(() => spikeSetMode('mtd'));
  await settle(n);
  expect(last().get('period')).toBe('mtd');
  expect(last().get('branch')).toBe(b);
  // 4 · the top bar changes again (ทุกสาขา) -> the card follows it: no branch, the select back to ทุกสาขา, the chain view
  n = urls.length;
  await page.evaluate(() => { const s = document.getElementById('selBranch'); s.value = ''; s.dispatchEvent(new Event('change', { bubbles: true })); });
  await settle(n);
  expect(last().get('branch')).toBe(null);
  expect(last().get('period')).toBe('mtd');
  expect(await page.evaluate(() => document.getElementById('spikeBranch').value)).toBe('');
  expect(await page.evaluate(() => document.getElementById('spikeTable').dataset.view)).toBe('chain');
  expect(errors).toEqual([]);
  await ctx.close();
});

test('CSV: the dish rows first, then the rows per branch with the raw-plates list (แบบเดิม)', async ({ browser }) => {
  test.setTimeout(150_000);
  const { ctx, page, errors } = await open(browser, DESK);
  const csv = await page.evaluate(() => { let out = ''; const keep = window.downloadCSV; window.downloadCSV = t => { out = t; };
    try { spikeExport(); } finally { window.downloadCSV = keep; } return out; });
  const rows = csv.split('\n').map(r => r.split('","').map(c => c.replace(/^"|"$/g, '')));
  expect(rows[0]).toContain('สาขาที่ดัน / สาขาที่ขาย');
  expect(rows[0]).toContain('ต้นทุนเพิ่มสุทธิ (บาท)');
  expect(rows.filter(r => r[0] === 'เมนูเดิม · ดันต้นทุนเพิ่มสุทธิ').length).toBe(20);
  expect(rows.filter(r => r[0] === 'เมนูใหม่').length).toBe(4);
  expect(rows.filter(r => r[0] === 'เมนูพุ่ง · นับจำนวนจาน (แบบเดิม)').length).toBe(3);
  expect(rows.filter(r => r[0] === 'รายสาขา · เมนูเดิม · ต้นทุนเพิ่ม').length).toBe(5);
  expect(errors).toEqual([]);
  await ctx.close();
});

test('เมนูพุ่ง on the fixture: by dish, no fee line, the net extra cost in order, two sections', async ({ browser }) => {
  test.setTimeout(180_000);
  const { ctx, page, errors } = await open(browser, DESK, { mock: false, ls: { oshinei_spike_all: '1' } });
  const txt = await page.evaluate(() => document.getElementById('dsSpike').innerText);
  for (const w of ['ค่าปรับ', 'ค่าเปิดห้อง', 'ค่ามัดจำ', 'ค่าเสียหาย', '(OT)']) expect(txt).not.toContain(w);
  const m = await page.evaluate(READ);
  const nets = m.nets.filter(v => v > 0);
  expect(nets).toEqual([...nets].sort((a, b) => b - a));
  expect(m.secs.length).toBe(2);
  expect(errors).toEqual([]);
  await ctx.close();
});
