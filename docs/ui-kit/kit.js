/* Oshinei UI Kit — shared page script (formal theme, 2026-10-01).
   - Chart.js defaults as the app sets them (js/09c-mobile.js setupChartDefaults +
     js/01-core.js chartTheme): KIRA UI 11.5px, theme inks, square legend markers,
     a dark tooltip card, 4px bar tips flush on the baseline, 2px lines.
   - kitChart(id, build): build(c) returns a Chart config; c gives the theme's
     colours (c.ink, c.tick, c.grid, c.s(n) = chart-n, c.v('--token')). Every
     chart is rebuilt when the theme flips, so nothing keeps light-mode colours.
   - kitToggleTheme(): flips body.dark (the app's switch) and redraws.
   Sample data only: these pages are public (/docs/ui-kit is served without a login). */
(function () {
  const charts = [];
  const css = n => getComputedStyle(document.body).getPropertyValue(n).trim();
  function palette() {
    const dark = document.body.classList.contains('dark');
    return {
      dark,
      v: css,
      ink: dark ? '#94a3b8' : '#475569',
      tick: dark ? '#cbd5e1' : '#64748b',
      grid: dark ? 'rgba(148,163,184,0.12)' : '#eef2f7',
      s: n => css('--chart-' + n) || '#4a70b0',
    };
  }
  function defaults() {
    if (typeof Chart === 'undefined') return;
    const c = palette(), FONT = "'KIRA UI','Noto Sans Thai',system-ui,sans-serif";
    Object.assign(Chart.defaults.font, { family: FONT, size: 11.5 });
    Chart.defaults.color = c.ink;
    Chart.defaults.borderColor = c.grid;
    Object.assign(Chart.defaults.elements.bar, { borderRadius: 4, borderSkipped: 'start', maxBarThickness: 46 });
    Object.assign(Chart.defaults.elements.line, { tension: 0.25, borderWidth: 2 });
    Object.assign(Chart.defaults.elements.point, { radius: 2.2, hoverRadius: 6, hitRadius: 10 });
    Object.assign(Chart.defaults.plugins.legend.labels, { usePointStyle: true, pointStyle: 'rect', boxWidth: 8, boxHeight: 8, padding: 14 });
    Object.assign(Chart.defaults.plugins.tooltip, {
      backgroundColor: 'rgba(15,23,42,0.96)', titleColor: '#f8fafc', bodyColor: '#e2e8f0',
      padding: 11, cornerRadius: 3, boxPadding: 5, usePointStyle: true, pointStyle: 'rect',
      borderColor: 'rgba(148,163,184,0.25)', borderWidth: 1,
      titleFont: { family: FONT, size: 12.5, weight: '600' }, bodyFont: { family: FONT, size: 12 },
    });
  }
  function axes(cfg, c) {
    Object.values((cfg.options && cfg.options.scales) || {}).forEach(ax => {
      ax.ticks = Object.assign({ color: c.tick }, ax.ticks || {});
      if (!ax.grid || ax.grid.display !== false) ax.grid = Object.assign({ color: c.grid }, ax.grid || {});
      if (ax.title) ax.title.color = c.tick;
    });
    return cfg;
  }
  window.kitChart = function (id, build) {
    const el = document.getElementById(id);
    if (!el || typeof Chart === 'undefined') return;
    const entry = { el, build, chart: null };
    const draw = () => { if (entry.chart) entry.chart.destroy(); defaults(); const c = palette(); entry.chart = new Chart(el, axes(build(c), c)); };
    entry.draw = draw;
    charts.push(entry);
    draw();
  };
  window.kitToggleTheme = function () {
    document.body.classList.toggle('dark');
    charts.forEach(e => e.draw());
  };
})();
