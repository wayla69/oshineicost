// @ts-check
// Owner 2026-10-25: "food-cost target 58%" - ONE target for the whole app. ตั้งค่า → การตั้งค่าส่วนตัว has one field
// "เป้าต้นทุนอาหาร (% ของรายได้)" (default 58, saved to app_settings food_cost_target); GP รายสาขา judges food cost
// against it: at or below = ในเป้า, above = เกินเป้า x.x จุด. The test moves the target either side of the fixture's
// food cost and reads the GP page badge each time, then puts the setting back to unset (= the default 58).
const { test, expect } = require('@playwright/test');

const IGNORE = [/favicon/i, /Failed to load resource/i, /net::ERR_ABORTED/i, /cdn\.tailwindcss/i];

async function login(ctx) {
  if (process.env.E2E_USER && process.env.E2E_PASS) {
    const r = await ctx.request.post('/api/auth/login', { data: { username: process.env.E2E_USER, password: process.env.E2E_PASS } });
    expect(r.ok()).toBe(true);
  }
}

// Open ตั้งค่า → prefs and wait until loadPreferences has FINISHED. It fetches /api/settings + /api/branches
// and only then writes the fields, so typing before it resolves gets overwritten with the saved value
// (the field already holds a value from the previous step, so "not empty" is not a load signal).
async function openPrefs(page) {
  await page.evaluate(async () => {
    if (!window.__prefLoads) {
      const orig = loadPreferences;
      window.__prefLoads = [];
      window.loadPreferences = (...a) => { const p = orig(...a); window.__prefLoads.push(p); return p; };
    }
    const before = window.__prefLoads.length;
    window.switchTab('settings'); switchSettingsTab('prefs');
    const t0 = Date.now();
    while (window.__prefLoads.length === before && Date.now() - t0 < 15000) await new Promise(r => setTimeout(r, 50));
    await Promise.allSettled(window.__prefLoads);
  });
}

async function saveTarget(page, value) {
  await openPrefs(page);
  const field = page.locator('#prefFoodCostTarget');
  await expect(field).toBeVisible();
  await expect(field).not.toHaveValue('');                     // loadPreferences filled it (58 when unset)
  await field.fill(String(value));
  const saved = page.waitForResponse(r => r.url().includes('/api/settings') && r.request().method() === 'PUT');
  await page.evaluate(() => savePreferences());
  expect((await saved).ok()).toBe(true);
  await expect(page.locator('#prefSaveStatus')).toContainText('Saved');
}

// the page draws its summary after BOTH answers (the GP table and the period cost); a real-size DB recomputes them after a
// target change, so wait for the drawn target rather than for a fixed time
async function gpBadge(page, want) {
  await page.evaluate(() => window.switchTab('gpbranch'));
  await page.waitForFunction(t => typeof _gpbData !== 'undefined' && _gpbData && _gpbData.food_cost_target === t, want, { timeout: 90_000 });
  const badge = page.locator('#gpbFcBadge');
  await expect(badge).toContainText(`เป้า ${want}%`, { timeout: 60_000 });
  return badge;
}

test('the food-cost target saves in settings and the GP รายสาขา badge follows it', async ({ browser }) => {
  test.setTimeout(300_000);
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  await login(ctx);
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' && !IGNORE.some(re => re.test(m.text()))) errors.push(m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  try {
    await page.goto('/', { waitUntil: 'networkidle' });
    await page.waitForFunction(() => typeof window.switchTab === 'function' && typeof savePreferences === 'function', null, { timeout: 30_000 });

    // the default: unset → 58
    await page.request.put('/api/settings', { data: { food_cost_target: '' } });
    await page.evaluate(() => { Object.keys(_tabLastKey).forEach(k => { delete _tabLastKey[k]; }); });
    let badge = await gpBadge(page, 58);

    // a target under the fixture's food cost → เกินเป้า x.x จุด, red
    await saveTarget(page, 20);
    expect((await (await page.request.get('/api/settings')).json()).food_cost_target).toBe('20');
    badge = await gpBadge(page, 20);
    await expect(badge).toContainText(/เกินเป้า \d+\.\d จุด/);
    await expect(badge).toHaveAttribute('data-ok', '0');

    // a target over it → ในเป้า, green
    await saveTarget(page, 90);
    badge = await gpBadge(page, 90);
    await expect(badge).toContainText('เป้า 90% · ในเป้า');
    await expect(badge).toHaveAttribute('data-ok', '1');
    await expect(page.locator('#gpbTable')).toContainText('ในเป้า');
    await expect(page.locator('#tab-gpbranch')).not.toContainText('ใกล้ขอบ');
    await expect(page.locator('#tab-gpbranch')).not.toContainText('55-70');

    // out of range is refused on the page, nothing is sent
    await openPrefs(page);
    await page.locator('#prefFoodCostTarget').fill('120');
    await page.evaluate(() => savePreferences());
    await expect(page.locator('#prefSaveStatus')).toContainText('20–90');
    expect((await (await page.request.get('/api/settings')).json()).food_cost_target).toBe('90');
  } finally {
    await page.request.put('/api/settings', { data: { food_cost_target: '' } });
    await ctx.close();
  }
  expect(errors, `console errors:\n${errors.join('\n')}`).toHaveLength(0);
});
