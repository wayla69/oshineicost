// @ts-check
// Table header ink follows its background: the text of a header cell must read (>= 4.5:1) against the fill actually painted
// behind it - the gradient band, a flat page fill, or an inline background - in light AND dark mode.
// Regression for "white header text on a white header" (PL3 forced white ink on every header, but the supply tables and
// cells with their own background are flat/light). Covers the slip-import check table, the order queue, an operation table
// and the variance table, then sweeps every header cell of the tabs below.
const { test, expect } = require('@playwright/test');

const MIN = 4.5;
const SWEEP_TABS = ['ops', 'opsreport', 'orderhub', 'ordering', 'ingpurchase', 'ordersetup', 'supplyhome', 'branchstock', 'bomcenter',
  'gpbranch', 'pnlbranch', 'pnlbill', 'discounts', 'cashctl', 'staff', 'franchise'];

// Computes, for every visible header cell inside `scope`, the contrast of its ink against its fill (a gradient is judged at every stop).
const MEASURE = /** @param {string} scope */ (scope) => {
  const parse = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const cr = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const over = (f, b) => ({ r: f.r * f.a + b.r * (1 - f.a), g: f.g * f.a + b.g * (1 - f.a), b: f.b * f.a + b.b * (1 - f.a), a: 1 });
  const pageBg = () => parse(getComputedStyle(document.body).backgroundColor) || { r: 255, g: 255, b: 255, a: 1 };
  const fills = el => {
    const stack = [];
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e), bi = cs.backgroundImage;
      if (bi && bi.includes('gradient')) {
        const cols = (bi.match(/rgba?\([^)]+\)/g) || []).map(parse).filter(Boolean);
        if (cols.length) { const base = e.parentElement ? fills(e.parentElement)[0] : pageBg(); return cols.map(c => over(c, base)); }
      }
      const c = parse(cs.backgroundColor);
      if (c && c.a > 0) { stack.push(c); if (c.a >= 1) break; }
    }
    let cur = pageBg(); cur = { ...cur, a: 1 };
    for (let i = stack.length - 1; i >= 0; i--) cur = over(stack[i], cur);
    return [cur];
  };
  const bad = [], seen = new Set();
  let n = 0;
  document.querySelectorAll(scope).forEach(th => {
    const r = th.getBoundingClientRect();
    if (!r.width || !r.height || !th.textContent.trim()) return;
    const cs = getComputedStyle(th);
    if (cs.visibility === 'hidden') return;
    n++;
    const fg = parse(cs.color);
    let worst = 99;
    fills(th).forEach(b => { worst = Math.min(worst, cr(over(fg, b), b)); });
    if (worst < 4.5) {
      const tb = th.closest('table');
      const key = (th.closest('[id^="tab-"]') || {}).id + '|' + (tb ? tb.id + tb.className : '') + '|' + cs.color + '|' + cs.backgroundColor;
      if (!seen.has(key)) { seen.add(key); bad.push(`${key} "${th.textContent.trim().slice(0, 16)}" ${worst.toFixed(2)}:1`); }
    }
  });
  return { n, bad };
};

async function login(page) {
  // The CI fixture runs with auth off. A local run with auth on passes a TEST account through the environment.
  if (process.env.E2E_USER && process.env.E2E_PASS) {
    await page.goto('/');
    await page.evaluate(async ([u, p]) => (await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: u, password: p }) })).status,
      [process.env.E2E_USER, process.env.E2E_PASS]);
  }
}

async function boot(page, dark) {
  await login(page);
  await page.goto('/', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => typeof window.switchTab === 'function', null, { timeout: 30_000 });
  await page.evaluate(d => document.body.classList.toggle('dark', d), dark);
}

async function open(page, tab) {
  await page.evaluate(n => window.switchTab(n), tab);
  await page.waitForTimeout(1500);
}

// Fixtures: the real tab, with the table the page would draw. The slip-import table uses the page's own renderer.
const INJECT = {
  slip: () => {
    const host = document.querySelector('#tab-branchstock #brnIssForm') || document.querySelector('#tab-branchstock');
    const box = document.createElement('div'); box.id = 'tt-slip'; host.appendChild(box);
    // eslint-disable-next-line no-undef
    box.innerHTML = _brnFormPreIss({ dates: [{ date: '2026-10-20', state: 'ok', lines: 12, used_lines: 10, waste_lines: 2, errors: [] }], file_errors: [] });
  },
  queue: () => {
    if (document.querySelector('#ohbQueue table')) return;
    const host = document.querySelector('#tab-orderhub'); const box = document.createElement('div'); box.id = 'ohbQueue'; host.appendChild(box);
    box.innerHTML = '<table class="wt"><thead><tr><th>สาขา</th><th>รอบ</th><th>สถานะ</th></tr></thead><tbody><tr><td>x</td><td>1</td><td>y</td></tr></tbody></table>';
  },
  variance: () => {
    const host = document.querySelector('#tab-branchstock'); const box = document.createElement('div'); box.id = 'tt-var'; host.appendChild(box);
    box.innerHTML = '<div style="overflow-x:auto"><table class="w-full text-xs"><thead><tr style="color:var(--text-muted);text-align:left"><th class="py-1 pr-2">รายการ</th><th class="py-1 pr-2 text-right">เบิก</th><th class="py-1 pr-2 text-right">ใช้</th></tr></thead><tbody><tr><td>a</td><td>1</td><td>2</td></tr></tbody></table></div>';
  },
};

for (const dark of [false, true]) {
  const mode = dark ? 'dark' : 'light';
  test.describe(`header ink vs fill (${mode})`, () => {
    test('slip-import check table', async ({ page }) => {
      await boot(page, dark);
      await open(page, 'branchstock');
      await page.evaluate(INJECT.slip);
      const r = await page.evaluate(MEASURE, '#tt-slip th');
      expect(r.n, 'slip-import header cells found').toBeGreaterThan(0);
      expect(r.bad, r.bad.join('\n')).toEqual([]);
    });
    test('order queue table', async ({ page }) => {
      await boot(page, dark);
      await open(page, 'orderhub');
      await page.evaluate(INJECT.queue);
      const r = await page.evaluate(MEASURE, '#ohbQueue th');
      expect(r.n).toBeGreaterThan(0);
      expect(r.bad, r.bad.join('\n')).toEqual([]);
    });
    test('operation table (ordering / ops plan)', async ({ page }) => {
      await boot(page, dark);
      await open(page, 'ordering');
      const r = await page.evaluate(MEASURE, '#tab-ordering th');
      expect(r.n, 'ordering page header cells found').toBeGreaterThan(0);
      expect(r.bad, r.bad.join('\n')).toEqual([]);
    });
    test('variance table', async ({ page }) => {
      await boot(page, dark);
      await open(page, 'branchstock');
      await page.evaluate(INJECT.variance);
      const r = await page.evaluate(MEASURE, '#tt-var th');
      expect(r.n).toBeGreaterThan(0);
      expect(r.bad, r.bad.join('\n')).toEqual([]);
    });
    test('sweep: every header cell of the working tabs', async ({ page }) => {
      await boot(page, dark);
      const all = [];
      for (const t of SWEEP_TABS) {
        await open(page, t);
        const r = await page.evaluate(MEASURE, 'th');
        all.push(...r.bad.map(b => `[${t}] ${b}`));
      }
      expect(all, all.join('\n')).toEqual([]);
    });
  });
}
