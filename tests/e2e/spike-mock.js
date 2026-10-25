// @ts-check
// DV3 · a /api/sales/menu-spike answer for the menu-spike specs (the card by dish). Not a spec: required by them.
//   dishAnswer(url, { cost, nDish, nMat, nNew }) - view chain (ทุกสาขา) or branch (the url names a branch); the rows per dish of
//   backend _spike_dishes / _spike_new_dishes, the per-branch rows (spikes, new_menus, spikes_plates) the CSV keeps.
function dishAnswer(url, o = {}) {
  const u = new URL(url || 'http://x/'), q = k => u.searchParams.get(k);
  const cost = o.cost !== false, br = q('branch') || '', nDish = o.nDish == null ? 20 : o.nDish, nMat = o.nMat == null ? 12 : o.nMat;
  const nNew = o.nNew == null ? 4 : o.nNew, chain = !br;
  const tiers = [{ tier: '789', label: '฿789', plates: 60, customers: 140, rate: 42.9, normal_rate: 20, excess: 32, x: 2.1, high: true },
    { tier: '999', label: '฿999', plates: 20, customers: 40, rate: 50, normal_rate: 40, excess: 4, x: 1.2, high: false }];
  const nets = Array.from({ length: nDish }, (_, i) => i < nMat ? 6000 - i * 300 : 400 - i * 10);
  const up = nets.reduce((a, b) => a + b, 0);
  let run = 0;
  const dishes = nets.map((net, i) => {
    run += net;
    const d = { menu: `ปลาแซลมอนย่างเทริยากิ (1 ชิ้น ต่อ 1 เสิร์ฟ) ${i + 1}`, kind: 'buffet', branches: chain ? 3 : 1, today: 300 - i, expected: 200, excess: 100 - i,
      x: 1.5, pct: 50, spike: i % 3 === 0, spike_branches: i % 3 === 0 ? 1 : 0, cum_pct: Math.round(run / up * 1000) / 10, material: i < nMat };
    if (chain) Object.assign(d, { pushers: [{ branch: 'สาขาเอ', excess: 50, ...(cost ? { extra_cost: net * 0.5 } : {}) },
      { branch: 'สาขาบี', excess: 30, ...(cost ? { extra_cost: net * 0.3 } : {}) }, { branch: 'สาขาซี', excess: 20, ...(cost ? { extra_cost: net * 0.2 } : {}) }],
      pushers_more: 2 });
    else Object.assign(d, { tiers, tier_rest: 0, rate: 50, normal_rate: 20, basis: 'buffet', customers: 400 });
    if (cost) Object.assign(d, { net_cost: net, cost_share: Math.round(net / 1e6 * 10000) / 100, net_pp: Math.round(net / 2e6 * 10000) / 100, uncosted: false });
    return d;
  });
  const new_dishes = Array.from({ length: nNew }, (_, j) => ({ menu: j === 0 ? '(BF) กุ้งลายเสือย่างกระเทียม (1 ตัว)(Buffet)' : `เมนูใหม่ ${j + 1}`, kind: 'alc',
    branches: chain ? 2 : 1, qty: 400 - j * 90, per_day: 32 - j * 8, rate: 13.3, returning: j === 0 ? (chain ? 2 : 1) : 0, first_sold: '2026-10-01',
    age_days: 19, trend_pct: j === 1 ? -12 : 8, top: [{ branch: 'สาขาเอ', qty: 300 - j * 50, returning: j === 0 }, { branch: 'สาขาบี', qty: 100, returning: false }],
    more: 0, material: 32 - j * 8 >= 20 || (cost && 3000 - j * 900 >= 1000),
    ...(chain ? {} : { first_sold_th: '1 ต.ค.', ...(j === 0 ? { returned_on: '2026-10-01', first_sold_ever: '2026-03-22' } : {}) }),
    ...(cost ? { period_cost: 3000 - j * 900, uncosted: false } : {}) }));
  const spikes = dishes.slice(0, 5).map(d => ({ branch: br || 'สาขาเอ', menu: d.menu, today: d.today, per_day: 40, expected: d.expected, excess: d.excess, x: 1.5,
    pct: 50, spike: d.spike, kind: 'buffet', basis: 'buffet', customers: 400, rate: 50, normal_rate: 20, tiers, tier_rest: 0,
    ...(cost ? { unit_cost: 50, extra_cost: d.excess * 50, cost_share: 0.5, extra_cost_per_head_pp: 0.25, uncosted: false, package: false } : {}) }));
  const new_menus = new_dishes.map(n => ({ branch: br || 'สาขาเอ', menu: n.menu, first_sold: '2026-10-01', first_sold_th: '1 ต.ค.', age_days: 19, qty: n.qty, per_day: n.per_day,
    kind: 'alc', basis: 'all', customers: 300, rate: 13.3, trend: null, ...(n.returning ? { returning: true, returned_on: '2026-10-01', first_sold_ever: '2026-03-22' } : {}),
    ...(cost ? { unit_cost: 25, period_cost: n.period_cost, uncosted: false, package: false } : {}) }));
  const spikes_plates = Array.from({ length: 3 }, (_, i) => ({ branch: br || 'สาขาเอ', menu: `เมนูจานเยอะ ${i + 1}`, today: 300 - i, per_day: 40, avg: 100,
    expected: 100, excess: 200 - i, x: 3.0, pct: 200, is_new: false, kind: 'buffet', in_per_head: false }));
  const range = q('period') === 'range', vs = !!q('base_from');
  const per = q('period') || 'day';
  const out = { period: per, date_from: range ? q('date_from') : per === 'mtd' ? '2026-10-01' : per === 'week' ? '2026-10-13' : '2026-10-19',
    date_to: range ? q('date_to') : '2026-10-19', date: '2026-10-19', latest_day: '2026-10-19', threshold: 1.5, top: 15, cost_view: cost,
    sort: cost ? 'extra_cost' : 'excess', view: chain ? 'chain' : 'branch',
    base: vs ? { kind: 'range', from: q('base_from'), to: q('base_to') } : { kind: 'normal', from: '2026-09-22', to: '2026-10-12' },
    summary: { customers: 52340, customers_per_day: 2754.7, base_customers_per_day: 2500, customers_vs_base_pct: 10.2, tier_top: { label: '฿789', excess: 640 }, top_excess: 1695 },
    spikes, count: spikes.length, spike_count: 2, new_menus, new_count: new_menus.length, spikes_plates, plates_count: 3, plates_only_count: 3,
    dishes, dish_count: nDish, dish_material_count: nMat, dish_floor: cost ? { baht: 1000, share_pct: 0.1 } : { plates: 20 },
    dish_totals: cost ? { up_excess: 900, down_excess: -300, net_excess: 600, up_cost: up, down_cost: -20000, net_cost: up - 20000, net_share: 1.2, net_pp: 0.6 }
      : { up_excess: 900, down_excess: -300, net_excess: 600 },
    new_dishes, new_dish_count: nNew, new_dish_material_count: new_dishes.filter(n => n.material).length,
    new_dish_floor: cost ? { per_day: 20, baht: 1000 } : { per_day: 20 } };
  if (cost) Object.assign(out, { food_cost_total: 1e6, revenue_total: 2e6, revenue_basis: 'net', top_extra_cost: 30000, top_cost_share: 3, top_pp: 1.5, uncosted_count: 0 });
  return out;
}

module.exports = { dishAnswer };
