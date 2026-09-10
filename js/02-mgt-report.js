// ─── 02-mgt-report.js — slice 2/9 of the former app.js (P2 structure refactor) ───
// Sequential load order matters: index.html loads 01→09; classic scripts
// share one global scope, so this equals the original single file.

// ════════════════════════════════════════════════════════════════
// MANAGEMENT REPORT — Daily & Weekly summary (English, printable)
// Backed by /api/reports/management. Fixed to the latest day/week
// (branch + group filter only); the global period range is ignored.
// ════════════════════════════════════════════════════════════════
let mgtPeriod = 'daily';
let mgtWeek = null;          // selected week (Monday ISO date); null = latest completed week
let mgtDay = null;           // selected day (ISO date); null = latest day in data
let mgtMonth = null;         // selected month ('YYYY-MM'); null = latest complete month
let mgtLang = (() => { try { return localStorage.getItem('mgtLang') === 'th' ? 'th' : 'en'; } catch (e) { return 'en'; } })();

function _mgtToggleUI() {
  const on  = 'background:#fff;color:#0f172a';
  const off = 'background:transparent;color:#fff';
  const d = document.getElementById('mgtTabDaily');
  const w = document.getElementById('mgtTabWeekly');
  const m = document.getElementById('mgtTabMonthly');
  if (d) d.setAttribute('style', mgtPeriod === 'daily'   ? on : off);
  if (w) w.setAttribute('style', mgtPeriod === 'weekly'  ? on : off);
  if (m) m.setAttribute('style', mgtPeriod === 'monthly' ? on : off);
  const le = document.getElementById('mgtLangEn');
  const lt = document.getElementById('mgtLangTh');
  if (le) le.setAttribute('style', mgtLang === 'en' ? on : off);
  if (lt) lt.setAttribute('style', mgtLang === 'th' ? on : off);
  // each picker only shows in its own period mode
  const wp = document.getElementById('mgtWeekPicker');
  if (wp && mgtPeriod !== 'weekly') wp.classList.add('hidden');
  const dp = document.getElementById('mgtDayPicker');
  if (dp && mgtPeriod !== 'daily') dp.classList.add('hidden');
  const mp = document.getElementById('mgtMonthPicker');
  if (mp && mgtPeriod !== 'monthly') mp.classList.add('hidden');
  // legacy full-report shortcut is monthly-only
  const lb = document.getElementById('mgtLegacyBtn');
  if (lb) lb.classList.toggle('hidden', mgtPeriod !== 'monthly');
}

function setMgtLang(l) {
  mgtLang = (l === 'th') ? 'th' : 'en';
  try { localStorage.setItem('mgtLang', mgtLang); } catch (e) {}
  _tabLastKey.mgtreport = null;     // force a refetch so backend labels/actions re-localise
  _mgtToggleUI();
  loadMgtReport();
}

// Show the day picker (daily mode) bounded to the available data range, with the
// focused day pre-selected. Mirrors renderMgtWeekPicker for the weekly view.
function renderMgtDayPicker(j) {
  const dp = document.getElementById('mgtDayPicker');
  if (!dp) return;
  if (j.period !== 'daily') { dp.classList.add('hidden'); return; }
  if (j.latest_day) dp.max = j.latest_day;
  if (j.min_day)    dp.min = j.min_day;
  dp.value = (j.focus || j.latest_day || '').slice(0, 10);
  dp.classList.remove('hidden');
}

function renderMgtWeekPicker(j) {
  const sel = document.getElementById('mgtWeekPicker');
  if (!sel) return;
  if (j.period !== 'weekly' || !(j.weeks && j.weeks.length)) {
    sel.classList.add('hidden'); sel.innerHTML = ''; return;
  }
  const wkPfx = (mgtLang === 'th') ? 'สัปดาห์: ' : 'Week: ';
  sel.innerHTML = j.weeks.map(w =>
    `<option value="${w.start}"${w.start === j.selected_week ? ' selected' : ''}>${wkPfx}${w.label}</option>`
  ).join('');
  sel.classList.remove('hidden');
}

function renderMgtMonthPicker(j) {
  const sel = document.getElementById('mgtMonthPicker');
  if (!sel) return;
  if (j.period !== 'monthly' || !(j.months && j.months.length)) {
    sel.classList.add('hidden'); sel.innerHTML = ''; return;
  }
  const moPfx = (mgtLang === 'th') ? 'เดือน: ' : 'Month: ';
  sel.innerHTML = j.months.map(m =>
    `<option value="${m.month}"${m.month === j.selected_month ? ' selected' : ''}>${moPfx}${m.label}</option>`
  ).join('');
  sel.classList.remove('hidden');
}

function onMgtWeekChange(v) {
  mgtWeek = v || null;
  _tabLastKey.mgtreport = null;     // force a refetch for the chosen week
  loadMgtReport();
}

function onMgtMonthChange(v) {
  mgtMonth = v || null;
  _tabLastKey.mgtreport = null;     // force a refetch for the chosen month
  loadMgtReport();
}

// Sidebar "📄 Monthly Report" → the Management Report in monthly mode (the simple
// Daily/Weekly-style presentation). The legacy full pack stays reachable via the
// ฉบับเต็ม button (monthly mode only) → switchTab('report').
function openMonthlyReport() {
  mgtPeriod = 'monthly';
  mgtMonth = null;                  // latest complete month
  _tabLastKey.mgtreport = null;
  switchTab('mgtreport');
}

function onMgtDayChange(v) {
  mgtDay = v || null;
  _tabLastKey.mgtreport = null;     // force a refetch for the chosen day
  loadMgtReport();
}

// The chrome is hidden by a class on <body>, so it would stay hidden after
// switching to another tab. Any tab change clears it; re-entering the report
// re-applies whatever the reader chose.
let _mgtFoldedNav = false;     // did reading mode fold the rail, or had the reader?

function _mgtReadingOff() {
  document.body.classList.remove('mgt-reading');
  const sb = document.getElementById('sidebar');
  if (sb && _mgtFoldedNav) { toggleSidebar(); _mgtFoldedNav = false; }
}

function setMgtPeriod(p) {
  mgtPeriod = (p === 'weekly' || p === 'monthly') ? p : 'daily';
  mgtWeek = null;                   // reset to the latest completed week on mode switch
  mgtDay = null;                    // reset to the latest day on mode switch
  mgtMonth = null;                  // reset to the latest complete month on mode switch
  _tabLastKey.mgtreport = null;     // force a refetch for the new period
  _mgtToggleUI();
  loadMgtReport();
}

function printMgtReport() {
  // Chrome stamps ITS OWN page header/footer from document.title, so every printed
  // sheet used to read "Oshinei - Sales & Cost Analysis" - true of any page in the
  // app, and useless on a report someone is holding. Swap in this report's own
  // label (period + scope) for the duration of the print, then put it back.
  const prev = document.title;
  const lbl = (document.getElementById('mgtPeriodLabel') || {}).textContent || '';
  document.title = 'Oshinei - Management Summary' + (lbl ? ' - ' + lbl.trim() : '');
  const restore = () => { document.title = prev; window.removeEventListener('afterprint', restore); };
  window.addEventListener('afterprint', restore);
  try { window.print(); } catch (e) {}
  setTimeout(restore, 2000);          // afterprint never fires in some engines
}

// Contents -> section. A plain #anchor does not scroll reliably inside the app's
// own scroll container (.content-area owns the scrollbar, not the window).
function _mgtGo(id) {
  const el = document.getElementById(id);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── collapsible cover band: on small screens the reader wants the report, not
// the chrome — start collapsed there (persisted; an explicit choice wins).
// Print always expands via the @media print rules in index.html.
function toggleMgtCover(collapse) {
  const cov = document.getElementById('mgtCover');
  if (!cov) return;
  const c = (collapse !== undefined) ? collapse : !cov.classList.contains('collapsed');
  cov.classList.toggle('collapsed', c);
  // Collapsing the cover also folds the app chrome (top bar + preset row). On a
  // phone that chrome is most of the first screenful — nav, title, search,
  // filter, presets, user chip, upload buttons — so the report only really
  // starts below the fold. Print is unaffected: it forces the full header back.
  document.body.classList.toggle('mgt-reading', c);
  // The nav rail folds to icons using the app's OWN collapse, rather than being
  // hidden: the reader keeps a way to navigate. The width media query could not
  // do this — a phone in desktop mode reports a ~980px layout viewport, so the
  // ≤700px rule never matched and the menu bar stayed at full width.
  // Only undo it if WE folded it, so a reader who had already collapsed the rail
  // does not get it re-opened on the way out.
  const sb = document.getElementById('sidebar');
  if (sb) {
    if (c && !sb.classList.contains('collapsed')) { toggleSidebar(); _mgtFoldedNav = true; }
    else if (!c && _mgtFoldedNav) { toggleSidebar(); _mgtFoldedNav = false; }
  }
  try { localStorage.setItem('mgtCoverCollapsed', c ? '1' : '0'); } catch (e) {}
}
let _mgtCoverInited = false;
function _mgtCoverInit() {
  if (_mgtCoverInited) return;
  _mgtCoverInited = true;
  let pref = null;
  try { pref = localStorage.getItem('mgtCoverCollapsed'); } catch (e) {}
  toggleMgtCover(pref == null ? window.innerWidth < 700 : pref === '1');
}

function exportMgtReport() {
  const qs = new URLSearchParams();
  qs.set('period', mgtPeriod);
  if (mgtPeriod === 'weekly'  && mgtWeek)  qs.set('week',  mgtWeek);
  if (mgtPeriod === 'daily'   && mgtDay)   qs.set('date',  mgtDay);
  if (mgtPeriod === 'monthly' && mgtMonth) qs.set('month', mgtMonth);
  if (getBranch()) qs.set('branch', getBranch());
  if (getGroup())  qs.set('group',  getGroup());
  qs.set('lang', mgtLang);                                             // export in the on-screen language
  window.location.href = `${API}/api/reports/management.xlsx?${qs}`;   // session cookie travels with it
}

// Bumped on every load; a response whose generation is stale (the reader clicked
// Weekly while the Daily fetch was still in flight) is DISCARDED instead of
// painting the wrong period over the right one.
let _mgtGen = 0;

async function loadMgtReport() {
  _mgtCoverInit();
  const _gen = ++_mgtGen;
  const _fk = _filterKey() + '|' + mgtPeriod + '|' + (mgtWeek || '') + '|' + (mgtDay || '') + '|' + (mgtMonth || '') + '|' + mgtLang;
  if (_tabLastKey.mgtreport === _fk) return;
  _tabLastKey.mgtreport = _fk;
  _mgtToggleUI();
  const body = document.getElementById('mgtRptBody');
  if (body) body.innerHTML = `<div class="text-center text-slate-400 py-10 text-sm">${mgtLang === 'th' ? 'กำลังโหลด…' : 'Loading…'}</div>`;
  const qs = new URLSearchParams();
  qs.set('period', mgtPeriod);
  qs.set('lang', mgtLang);
  if (mgtPeriod === 'weekly'  && mgtWeek)  qs.set('week',  mgtWeek);
  if (mgtPeriod === 'daily'   && mgtDay)   qs.set('date',  mgtDay);
  if (mgtPeriod === 'monthly' && mgtMonth) qs.set('month', mgtMonth);
  if (getBranch()) qs.set('branch', getBranch());
  if (getGroup())  qs.set('group',  getGroup());
  // Fire master & franchise splits in parallel — only when no specific branch/type filter is active
  const canSplit = !getBranch() && !getGroup();
  let j, jM = null, jF = null;
  try {
    if (canSplit) {
      const qsM = new URLSearchParams(qs); qsM.set('group', 'master');
      const qsF = new URLSearchParams(qs); qsF.set('group', 'franchise');
      [j, jM, jF] = await Promise.all([
        api(`${API}/api/reports/management?${qs}`),
        api(`${API}/api/reports/management?${qsM}`).catch(() => null),
        api(`${API}/api/reports/management?${qsF}`).catch(() => null),
      ]);
    } else {
      j = await api(`${API}/api/reports/management?${qs}`);
    }
  } catch (e) {
    if (_gen !== _mgtGen) return;
    if (body) body.innerHTML = `<div class="card p-6 text-sm text-rose-600">${mgtLang === 'th' ? 'โหลดรายงานไม่สำเร็จ' : 'Failed to load the report.'}</div>`;
    return;
  }
  if (_gen !== _mgtGen) return;        // superseded by a newer period/branch/lang
  if (!j || !j.available) {
    if (body) body.innerHTML = `<div class="card p-6 text-sm text-slate-500">${mgtLang === 'th' ? 'ยังไม่มีข้อมูลการขาย — กรุณาอัปโหลดหรือซิงค์ข้อมูลก่อน' : 'No sales data yet — upload or sync data first.'}</div>`;
    return;
  }
  renderMgtReport(j,
    jM && jM.available ? jM : null,
    jF && jF.available ? jF : null);
}

// Bilingual string table for the Management Summary report (EN default / TH).
// Backend already localises j.label, j.comp_label and j.actions; everything rendered
// on the client is keyed here so the EN ⇄ ไทย toggle re-skins the whole report.
const MGT_I18N = {
  en: {
    dailyReport:'Daily report · ', weeklyReport:'Weekly report · ', monthlyReport:'Monthly report · ',
    wtd:'  (week-to-date)', dtd:'  (so far today — partial)', mtdTag:'  (month-to-date — partial)',
    scope:'Scope: ', comparedWith:'   ·   compared with ', allBranches:'All branches', groupPfx:'Group: ',
    title:'Management Summary Report', generated:'Generated:', exportExcel:'📊 Export Excel',
    printPdf:'🖨️ Print / Export PDF', daily:'Daily', weekly:'Weekly', monthly:'Monthly', loading:'Loading…',
    compDaily:'vs last week', compWeekly:'vs prev week', compMonthly:'vs prev month',
    masterFranchise:'Master · Franchise breakdown', master:'Master', franchise:'Franchise',
    netSales:'Net Sales', netSalesVat:'Net Sales (ex-VAT)', grossProfit:'Gross Profit',
    afterFoodCost:'after food cost', marginAfter:'% margin · after food cost',
    yoyHidden:'YoY hidden — sales history starts ~May 2025, so the year-ago period isn’t comparable yet.',
    customers:'Customers', covers:'covers', spendHead:'Spend / Head', perCover:'per cover',
    avgTicket:'Avg Ticket', perBill:'per bill', bills:'Bills', receipts:'receipts',
    discounts:'Discounts', ofSales:'% of sales', gp:'GP', spendHeadShort:'Spend/Head',
    // §3 pace
    monthNetSales:'Month Net Sales', mtdNetSales:'MTD Net Sales', days:' days', daysSoFar:' days so far',
    monthTotal:'Month Total', projMonthEnd:'Projected Month-End', actual:'actual', runRate:'run-rate',
    runRateDow:'day-of-week weighted run-rate', dowBase:'the weekday-matched norm for the first', vsDowBase:'vs that weekday-matched norm',
    revTarget:'Revenue Target', actualSp:'actual ', projectionSp:'projection ', ofTarget:'% of target',
    noTarget:'no target set', foodCostPct:'Food Cost %', mtdSuffix:' (MTD)', bomBasis:'BoM basis',
    lastMonthSp:' · last month ', weeklyNetSales:'Weekly net sales — ',
    weeklyHint:'(darker = latest week · amber tick = last month, same weekdays)',
    lastMonthAvgWk:'last month (same weekdays)', mtdRevenue:'MTD Revenue', projected:'Projected', target:'Target',
    lastMonth:'Last month', total:'total', overSame:'over the same', day1:'day', dayN:'days',
    thisMonthIs:'this month is', vsSameDays:'vs the same days last month',
    // §4 trend
    curveMa:'curve = moving-average trend', darkerLatest:'darker bar = latest', trendMa:'Trend (moving avg)',
    darkLatest:'dark bar = latest', day:'day', week:'week', month:'month', totalSeries:'Total',
    // §5 league
    branch:'Branch', share:'Share', noBranchData:'No branch data.', watch:'watch',
    // §6 leak
    naNoPay:'n/a — no payment detail', totalDiscounts:'Total Discounts · this', dayWord:'day', weekWord:'week', monthWord:'month',
    allDiscTie:'(all discounted bills — ties to the Discounts page)',
    heavyDisc:'Heavy Discounts', heavyHint:'Bills discounted ≥30%',
    cashHeavy:'Cash-Heavy Discounts', cashHint:'Discounted & paid mostly cash',
    reprinted:'Reprinted Bills', reprintHint:'Discounted bills printed >1×',
    zeroGross:'Zero-Gross Bills', zeroHint:'Discount-only / free bills',
    discByMaster:'Discounts by master branch · % of sales', companyFollow:'(company-owned — the follow-up)',
    discount:'Discount', salesExVat:'Sales (ex-VAT)', discBills:'Disc. bills', allMaster:'All master',
    heavyDisc30:'Heavy Discounts ≥30%', cashHeavyShort:'Cash-Heavy', reprintShort:'Reprinted Bills', zeroShort:'Zero-Gross',
    discTotalShort:'Total Discounts (all bills)',
    // §7 menu
    totFoodHead:'Total food cost / head: ', was:'was', overCovers:' covers', perHead:'/head',
    none:'None.', noCompPeriod:'No comparison period.', highestCostHead:'Highest Cost / Head',
    costHeadRising:'Cost / Head Rising ', costHeadFalling:'Cost / Head Falling ', noData:'No data.',
    // §8 peak
    daypartSplit:'Daypart split (revenue)', smallPct:'· small % = ', noData2:'No data.',
    totalC:'Total', busiestHour:'Busiest hour', busiestDay:'Busiest day',
    byHour:'By hour', byDow:'By day of week', peakHour:'Peak Hour', bestDay:'Best Day', dayparts:'Dayparts',
    Lunch:'Lunch', Dinner:'Dinner', Other:'Other',
    // §9 table
    coversTable:'Covers / Table', turnsPerTable:'turns per table', avgTurnTime:'Avg Turn Time',
    perSession:'per table session · faster = better', min:'min', tablesUsed:'Tables Used',
    tableSessions:'table sessions', totalCovers:'Total Covers', guestsSeated:'guests seated',
    tables:'Tables', coversTableShort:'Covers/Table', avgTurn:'Avg Turn', dataLbl:'Data', na:'n/a',
    // actions split
    mfPriority:'Master · Franchise priority actions', noCritical:'No critical issues.',
    // §10 payment
    branchesWord:'branches', channels:'channels', payCaption2:'% = each branch share (bold = dominant · small no. in the total row = ',
    masterBranches:'Master Branches', allBranchesCap:'All Branches', thisDay:'this day', thisWeek:'this week', thisMonth:'this month',
    payBranch:'Branch', paySum:'Total', payAll:'All',
    // exec summary header + pills
    execSummary:'📋 Executive Summary', tldr:'TL;DR — at a glance', thisDayP:'This Day', thisWeekP:'This Week', thisMonthP:'This Month',
    avg8d:'8-Day Avg', avg8w:'8-Wk Avg', avg8m:'8-Mo Avg', bestDayP:'Best Day', bestWeekP:'Best Week', bestMonthP:'Best Month',
    weakDayP:'Weakest Day', weakWeekP:'Weakest Week', weakMonthP:'Weakest Month', vsAvgP:' vs avg', dod:' DoD', wow:' WoW', mom:' MoM',
    trendAnalysis:'Trend analysis',
    // section titles
    s1:'1. Headline — ', s1d:'Latest Day', s1w:'Selected Week', s1m:'Selected Month', s2:'2. Action Items',
    s3pre:'3. ', s3mc:'Month', s3mtd:'Month-to-Date', s3post:' Pacing & Cost',
    s4pre:'4. Revenue Trend (last 8 ', s4d:'days', s4w:'weeks', s4m:'months', s4post:')',
    s5:'5. Branch League Table', s6:'6. Money-Leak Radar', s7:'7. Menu Cost / Head (by dish)',
    s8:'8. Peak Hours & Dayparts', s9:'9. Table Turnover & Occupancy', s10:'10. Payment Channels — ',
    s11:'11. Buffet Price Tiers — Guests & Cost/Head',
    s12v:'12. Customer Voice — What Guests Said & the Fix Loop', naVoice:'No customer feedback in this period yet — collect reviews or QR-survey answers in the 💬 tab.',
    vVoices:'Voices', vNeg:'Negative', vStars:'Avg ★', vResp:'Answered', vWait:'Awaiting reply now', vSla:'past SLA', vThemes:'Top themes',
    vWatch:'Branches to watch', vPraised:'Most praised', vSurvey:'QR survey', vAnswers:'answers', vActions:'Fix loop', vOpen:'open', vOverdue:'overdue',
    vDone:'done this period', vBetter:'better', vSame:'same', vWorse:'worse', vPending:'window still running', vRate:'neg / 100 voices',
    vNoActions:'No fixes recorded yet — create one from a theme in the 💬 tab, and this section will show whether it worked.',
    vRespBranch:'Answered by branch', vMedian:'median',
    tierCol:'Tier', tierGuests:'Guests', tierShare:'Share', tierRevHead:'Rev/Head',
    tierCostHead:'Cost/Head', tierGpHead:'GP/Head', tierFood:'COGS %', tierBuffetGuests:'buffet guests',
    tierMixDown:'Customers shifted to CHEAPER tiers — mix effect on spend/head ', tierMixUp:'Customers shifted to PRICIER tiers — mix effect on spend/head ',
    tierNote:'Tier = the adult buffet price printed on that period’s menu. Until Aug 2026 the menu quoted the price BEFORE service charge and VAT; the Grand Menu from 1 Sep 2026 quotes NET prices (5% service charge and 7% VAT already inside), so ฿789 net = ฿702.27 + SC + VAT. promo = a non-standard price (discount / promotion). Guests count buffet bills only, so the total can sit slightly under headline covers · Cost/Head = BoM food cost · Rev/Head is ex-VAT but still INCLUDES the 5% service charge.',
    promoTitle:'What is inside “Promo/other price”',
    promoNote:'Read from the adult buffet lines on each bill: a bill whose adult heads carry DIFFERENT prices is counted as mixed, not as a discount — its per-head average is an artefact of the mix.',
    tierDrvTitle:'Cost drivers / head', tierDrvPick:'Tier', tierDrvGroup:'Ingredient group', tierDrvMenu:'Top menu item',
    tierDrvCostHead:'Cost/Head', tierDrvShare:'% of tier cost', tierDrvVs:'vs', tierDrvQty:'Qty/100',
    tierDrvNote:'Bars scale to the biggest driver · % of tier cost = share of this tier’s total food cost/head · vs = extra ฿/head over the next-cheaper tier (red = more) · Qty/100 = pieces per 100 guests · tap a tier above to compare.',
    // cover page (contents + report information) & appendix
    tocTitle:'Contents', repInfo:'Report information',
    riType:'Report', riRange:'Period covered', riComp:'Compared with', riScope:'Scope',
    riDays:'Days in period', riData:'Data through', riPartial:'period still open — not final',
    riRev:'Revenue basis', riRevV:'Net sales ex-VAT (bill total ÷ 1.07)',
    riCost:'Cost basis', riCostV:'BoM recipe food cost',
    naTable:'Table-turnover data is not available for this period — no table sessions were recorded.',
    naPay:'Payment-channel detail is not available for this period.',
    naTier:'Buffet price-tier data is not available for this period.',
    s12:'13. Appendix — Definitions & Method',
    defs:[
      ['Net sales','bill total including VAT ÷ 1.07. VAT is never counted as revenue.'],
      ['Gross profit (GP)','net sales − food COGS. Labour, rent and utilities are NOT deducted here.'],
      ['Covers','guests served (pax on the bill). Spend / head = net sales ÷ covers.'],
      ['Avg ticket','net sales ÷ number of bills — one bill can carry several covers.'],
      ['Food cost %','recipe (BoM) food cost ÷ net sales for the period’s month. Dishes with no costed recipe book ฿0, so the figure is a FLOOR; plate coverage is disclosed in §3.'],
      ['Comparison basis','daily = the same weekday last week · weekly = the previous full Mon–Sun week · monthly = the previous calendar month, weekday-matched while the month is still partial.'],
      ['Projection','day-of-week weighted run-rate — elapsed days extended by last month’s weekday shape, not a flat daily average.'],
      ['Heavy discount','a bill discounted 30% or more of its pre-discount value.'],
      ['Cash-heavy discount','a discounted bill settled mostly in cash — the classic skim signature.'],
      ['Reprinted bill','a discounted bill printed more than once.'],
      ['Zero-gross bill','a bill left at ฿0 after discount (free / discount-only).'],
      ['Price tier','the adult buffet menu price on the bill, ex-VAT. “promo” = a non-standard price (discount / promotion).'],
      ['YoY','suppressed before mid-May 2026 — sales history starts ~May 2025, so the year-ago window is incomplete.'],
    ],
    defsNote:'Unaudited management information generated from the POS sales database. Tier, discount and cost-per-head analysis covers buffet bills; à-la-carte codes are excluded where noted.',
    dow:{Mon:'Mon',Tue:'Tue',Wed:'Wed',Thu:'Thu',Fri:'Fri',Sat:'Sat',Sun:'Sun'},
    numLocale:'en-US',
  },
  th: {
    dailyReport:'รายงานรายวัน · ', weeklyReport:'รายงานรายสัปดาห์ · ', monthlyReport:'รายงานรายเดือน · ',
    wtd:'  (ถึงปัจจุบันในสัปดาห์)', dtd:'  (เฉพาะวันนี้ — ยังไม่ครบวัน)', mtdTag:'  (ถึงปัจจุบันในเดือน — ยังไม่ครบเดือน)',
    scope:'ขอบเขต: ', comparedWith:'   ·   เทียบกับ ', allBranches:'ทุกสาขา', groupPfx:'กลุ่ม: ',
    title:'รายงานสรุปผู้บริหาร', generated:'จัดทำเมื่อ:', exportExcel:'📊 ส่งออก Excel',
    printPdf:'🖨️ พิมพ์ / บันทึก PDF', daily:'รายวัน', weekly:'รายสัปดาห์', monthly:'รายเดือน', loading:'กำลังโหลด…',
    compDaily:'เทียบสัปดาห์ก่อน', compWeekly:'เทียบสัปดาห์ก่อน', compMonthly:'เทียบเดือนก่อน',
    masterFranchise:'แยกตามบริษัท · แฟรนไชส์', master:'บริษัท', franchise:'แฟรนไชส์',
    netSales:'ยอดขายสุทธิ', netSalesVat:'ยอดขายสุทธิ (ไม่รวม VAT)', grossProfit:'กำไรขั้นต้น',
    afterFoodCost:'หลังหักต้นทุนอาหาร', marginAfter:'% อัตรากำไร · หลังหักต้นทุนอาหาร',
    yoyHidden:'ซ่อน YoY — ข้อมูลเริ่ม ~พ.ค. 2025 ช่วงเดียวกันปีก่อนจึงยังเทียบไม่ได้',
    customers:'ลูกค้า', covers:'จำนวนหัว', spendHead:'ยอดต่อหัว', perCover:'ต่อหัว',
    avgTicket:'ยอดเฉลี่ย/บิล', perBill:'ต่อบิล', bills:'จำนวนบิล', receipts:'ใบเสร็จ',
    discounts:'ส่วนลด', ofSales:'% ของยอดขาย', gp:'กำไรขั้นต้น', spendHeadShort:'ยอดต่อหัว',
    monthNetSales:'ยอดขายทั้งเดือน', mtdNetSales:'ยอดขายสะสมเดือนนี้', days:' วัน', daysSoFar:' วัน (ถึงปัจจุบัน)',
    monthTotal:'ยอดรวมทั้งเดือน', projMonthEnd:'ประมาณการสิ้นเดือน', actual:'จริง', runRate:'อัตราเฉลี่ย',
    runRateDow:'อัตราเฉลี่ยถ่วงตามวันในสัปดาห์', dowBase:'ฐานปกติ (ตามวันในสัปดาห์) ช่วง', vsDowBase:'เทียบฐานปกติตามวันในสัปดาห์ของเดือนก่อน',
    revTarget:'เป้ายอดขาย', actualSp:'จริง ', projectionSp:'ประมาณการ ', ofTarget:'% ของเป้า',
    noTarget:'ยังไม่ตั้งเป้า', foodCostPct:'ต้นทุนอาหาร %', mtdSuffix:' (สะสม)', bomBasis:'อิงสูตร BoM',
    lastMonthSp:' · เดือนก่อน ', weeklyNetSales:'ยอดขายสุทธิรายสัปดาห์ — ',
    weeklyHint:'(สีเข้ม = สัปดาห์ล่าสุด · ขีดอำพัน = เดือนก่อนเทียบวันเดียวกัน)',
    lastMonthAvgWk:'เดือนก่อน (วันเดียวกัน)', mtdRevenue:'ยอดสะสมเดือนนี้', projected:'ประมาณการ', target:'เป้า',
    lastMonth:'เดือนก่อน', total:'รวม', overSame:'ในช่วง', day1:'วัน', dayN:'วัน',
    thisMonthIs:'เดือนนี้', vsSameDays:'เทียบช่วงวันเดียวกันเดือนก่อน',
    curveMa:'เส้น = ค่าเฉลี่ยเคลื่อนที่', darkerLatest:'แท่งสีเข้ม = ล่าสุด', trendMa:'แนวโน้ม (ค่าเฉลี่ยเคลื่อนที่)',
    darkLatest:'แท่งเข้ม = ล่าสุด', day:'วัน', week:'สัปดาห์', month:'เดือน', totalSeries:'รวม',
    branch:'สาขา', share:'สัดส่วน', noBranchData:'ไม่มีข้อมูลสาขา', watch:'ต้องดู',
    naNoPay:'ไม่มีข้อมูล — ไม่มีรายละเอียดการชำระเงิน', totalDiscounts:'ส่วนลดรวม · ', dayWord:'วันนี้', weekWord:'สัปดาห์นี้', monthWord:'เดือนนี้',
    allDiscTie:'(บิลส่วนลดทั้งหมด — ตรงกับหน้าส่วนลด)',
    heavyDisc:'ส่วนลดหนัก', heavyHint:'บิลให้ส่วนลด ≥30%',
    cashHeavy:'ส่วนลดจ่ายเงินสด', cashHint:'ส่วนลด & จ่ายเงินสดเป็นหลัก',
    reprinted:'บิลพิมพ์ซ้ำ', reprintHint:'บิลส่วนลดพิมพ์ >1 ครั้ง',
    zeroGross:'บิลยอดศูนย์', zeroHint:'บิลส่วนลดล้วน / แจกฟรี',
    discByMaster:'ส่วนลดรายสาขาบริษัท · % ของยอดขาย', companyFollow:'(สาขาบริษัท — ประเด็นติดตาม)',
    discount:'ส่วนลด', salesExVat:'ยอดขาย (ไม่รวม VAT)', discBills:'บิลส่วนลด', allMaster:'รวมสาขาบริษัท',
    heavyDisc30:'ส่วนลดหนัก ≥30%', cashHeavyShort:'จ่ายเงินสด', reprintShort:'บิลพิมพ์ซ้ำ', zeroShort:'ยอดศูนย์',
    discTotalShort:'ส่วนลดรวม (ทุกบิล)',
    totFoodHead:'ต้นทุนอาหารต่อหัวรวม: ', was:'เดิม', overCovers:' หัว', perHead:'/หัว',
    none:'ไม่มี', noCompPeriod:'ไม่มีช่วงเปรียบเทียบ', highestCostHead:'ต้นทุนต่อหัวสูงสุด',
    costHeadRising:'ต้นทุนต่อหัวเพิ่มขึ้น ', costHeadFalling:'ต้นทุนต่อหัวลดลง ', noData:'ไม่มีข้อมูล',
    daypartSplit:'แบ่งตามช่วงเวลา (รายได้)', smallPct:'· เลขเล็ก = ', noData2:'ไม่มีข้อมูล',
    totalC:'รวม', busiestHour:'ชั่วโมงพีค', busiestDay:'วันพีค',
    byHour:'ตามชั่วโมง', byDow:'ตามวันในสัปดาห์', peakHour:'ชั่วโมงพีค', bestDay:'วันพีค', dayparts:'ช่วงเวลา',
    Lunch:'มื้อกลางวัน', Dinner:'มื้อเย็น', Other:'อื่นๆ',
    coversTable:'หัว/โต๊ะ', turnsPerTable:'รอบต่อโต๊ะ', avgTurnTime:'เวลาเฉลี่ย/รอบ',
    perSession:'ต่อรอบโต๊ะ · เร็ว = ดี', min:'นาที', tablesUsed:'โต๊ะที่ใช้',
    tableSessions:'รอบโต๊ะ', totalCovers:'จำนวนหัวรวม', guestsSeated:'ลูกค้าที่นั่ง',
    tables:'โต๊ะ', coversTableShort:'หัว/โต๊ะ', avgTurn:'เวลาเฉลี่ย', dataLbl:'ข้อมูล', na:'ไม่มี',
    mfPriority:'สิ่งที่ต้องทำ — บริษัท · แฟรนไชส์', noCritical:'ไม่มีประเด็นเร่งด่วน',
    branchesWord:'สาขา', channels:'ช่องทาง', payCaption2:'% = สัดส่วนของแต่ละสาขา (ตัวหนา = ช่องทางหลัก · เลขเล็กในแถวรวม = ',
    masterBranches:'สาขาบริษัท', allBranchesCap:'ทุกสาขา', thisDay:'วันนี้', thisWeek:'สัปดาห์นี้', thisMonth:'เดือนนี้',
    payBranch:'สาขา', paySum:'รวม', payAll:'ทั้งหมด',
    execSummary:'📋 บทสรุปผู้บริหาร', tldr:'TL;DR — สรุปเร็ว', thisDayP:'วันนี้', thisWeekP:'สัปดาห์นี้', thisMonthP:'เดือนนี้',
    avg8d:'เฉลี่ย 8 วัน', avg8w:'เฉลี่ย 8 สัปดาห์', avg8m:'เฉลี่ย 8 เดือน', bestDayP:'วันดีสุด', bestWeekP:'สัปดาห์ดีสุด', bestMonthP:'เดือนดีสุด',
    weakDayP:'วันต่ำสุด', weakWeekP:'สัปดาห์ต่ำสุด', weakMonthP:'เดือนต่ำสุด', vsAvgP:' เทียบเฉลี่ย', dod:' DoD', wow:' WoW', mom:' MoM',
    trendAnalysis:'บทวิเคราะห์แนวโน้ม',
    s1:'1. สรุปภาพรวม — ', s1d:'วันล่าสุด', s1w:'สัปดาห์ที่เลือก', s1m:'เดือนที่เลือก', s2:'2. สิ่งที่ต้องทำ',
    s3pre:'3. ', s3mc:'ทั้งเดือน', s3mtd:'สะสมเดือนนี้', s3post:' — จังหวะยอดขาย & ต้นทุน',
    s4pre:'4. แนวโน้มยอดขาย (8 ', s4d:'วันล่าสุด', s4w:'สัปดาห์ล่าสุด', s4m:'เดือนล่าสุด', s4post:')',
    s5:'5. ตารางจัดอันดับสาขา', s6:'6. เรดาร์จุดรั่วไหล', s7:'7. ต้นทุนเมนูต่อหัว (รายเมนู)',
    s8:'8. ชั่วโมงพีค & ช่วงเวลา', s9:'9. รอบหมุนเวียนโต๊ะ', s10:'10. ช่องทางการชำระเงิน — ',
    s11:'11. Tier ราคาบุฟเฟ่ต์ — คนทาน & ต้นทุน/หัว',
    s12v:'12. เสียงลูกค้า — ลูกค้าพูดอะไร & วงจรการแก้', naVoice:'ยังไม่มีเสียงลูกค้าในช่วงนี้ — เก็บรีวิวหรือคำตอบแบบสอบถาม QR ในแท็บ 💬',
    vVoices:'เสียง', vNeg:'เชิงลบ', vStars:'★ เฉลี่ย', vResp:'ตอบกลับ', vWait:'รอสาขาตอบตอนนี้', vSla:'เกิน SLA', vThemes:'หัวข้อที่พูดถึงมากสุด',
    vWatch:'สาขาที่ต้องดู', vPraised:'ชมมากสุด', vSurvey:'แบบสอบถาม QR', vAnswers:'คำตอบ', vActions:'วงจรการแก้', vOpen:'ค้าง', vOverdue:'เกินกำหนด',
    vDone:'เสร็จในช่วงนี้', vBetter:'ดีขึ้น', vSame:'เท่าเดิม', vWorse:'แย่ลง', vPending:'รอครบ 28 วัน', vRate:'ลบ / 100 เสียง',
    vNoActions:'ยังไม่มีงานแก้ที่บันทึกไว้ — สร้างจากหัวข้อในแท็บ 💬 แล้วส่วนนี้จะบอกว่าแก้แล้วได้ผลไหม',
    vRespBranch:'ตอบกลับรายสาขา', vMedian:'กลาง',
    tierCol:'Tier', tierGuests:'คนทาน', tierShare:'สัดส่วน', tierRevHead:'รายได้/หัว',
    tierCostHead:'ต้นทุน/หัว', tierGpHead:'GP/หัว', tierFood:'COGS %', tierBuffetGuests:'คนทานบุฟเฟ่ต์',
    tierMixDown:'ลูกค้าย้ายไป tier ถูกลง — ผลจาก mix ต่อยอด/หัว ', tierMixUp:'ลูกค้าย้ายขึ้น tier แพงขึ้น — ผลจาก mix ต่อยอด/หัว ',
    tierNote:'Tier = ราคาบุฟเฟ่ต์ผู้ใหญ่ตามเมนูของช่วงนั้น · ถึง ส.ค. 2026 เมนูตั้งราคา “ก่อน” บวก service charge และ VAT · ตั้งแต่ 1 ก.ย. 2026 (Grand Menu) เป็นราคา NET คือรวม service charge 5% และ VAT 7% ไว้แล้ว เช่น ฿789 net = ฿702.27 + SC + VAT · โปรฯ = ราคานอกมาตรฐาน (ส่วนลด/โปรโมชัน) · นับเฉพาะบิลบุฟเฟ่ต์ ยอดรวมจึงต่ำกว่าจำนวนหัวรวมเล็กน้อย · ต้นทุน/หัว = ต้นทุนอาหารอิงสูตร BoM · รายได้/หัว ถอด VAT แล้วแต่ยัง “รวม” service charge 5%',
    promoTitle:'ในช่อง “โปรฯ/ราคาอื่น” มีอะไรบ้าง',
    promoNote:'อ่านจากบรรทัดบุฟเฟ่ต์ผู้ใหญ่ในแต่ละบิล · บิลที่หัวผู้ใหญ่ราคาไม่เท่ากันจะนับเป็น “หัวฟรี/ครึ่งราคาปน” ไม่ใช่ส่วนลด เพราะราคาเฉลี่ยต่อหัวเป็นผลจากการปนกัน ไม่ใช่ราคาที่ตั้งขาย',
    tierDrvTitle:'ตัวขับต้นทุน/หัว', tierDrvPick:'เลือก tier', tierDrvGroup:'กลุ่มวัตถุดิบ', tierDrvMenu:'เมนูต้นทุนสูงสุด',
    tierDrvCostHead:'ต้นทุน/หัว', tierDrvShare:'% ต้นทุน tier', tierDrvVs:'เทียบ', tierDrvQty:'จำนวน/100หัว',
    tierDrvNote:'แท่งบาร์ยาวตามตัวที่ต้นทุนสูงสุด · % ต้นทุน tier = สัดส่วนของต้นทุนอาหาร/หัว ของ tier นี้ · เทียบ = ต้นทุน/หัว ที่มากกว่า tier ราคาถัดลงมา (แดง = มากกว่า) · จำนวน/100หัว = จำนวนชิ้นต่อ 100 คน · กดเลือก tier ด้านบนเพื่อเทียบ',
    tocTitle:'สารบัญ', repInfo:'ข้อมูลรายงาน',
    riType:'ประเภทรายงาน', riRange:'ช่วงข้อมูล', riComp:'เทียบกับ', riScope:'ขอบเขต',
    riDays:'จำนวนวัน', riData:'ข้อมูลถึงวันที่', riPartial:'ช่วงยังไม่สิ้นสุด — ตัวเลขยังไม่สรุป',
    riRev:'ฐานรายได้', riRevV:'ยอดขายสุทธิไม่รวม VAT (ยอดบิล ÷ 1.07)',
    riCost:'ฐานต้นทุน', riCostV:'ต้นทุนอาหารตามสูตร BoM',
    naTable:'ไม่มีข้อมูลรอบหมุนเวียนโต๊ะในช่วงนี้',
    naPay:'ไม่มีรายละเอียดช่องทางการชำระเงินในช่วงนี้',
    naTier:'ไม่มีข้อมูล tier ราคาบุฟเฟ่ต์ในช่วงนี้',
    s12:'13. ภาคผนวก — นิยามและวิธีคำนวณ',
    defs:[
      ['ยอดขายสุทธิ','ยอดบิลรวม VAT ÷ 1.07 — ไม่นับ VAT เป็นรายได้'],
      ['กำไรขั้นต้น (GP)','ยอดขายสุทธิ − ต้นทุนอาหาร ยังไม่หักค่าแรง ค่าเช่า ค่าสาธารณูปโภค'],
      ['จำนวนหัว','ลูกค้าที่รับบริการ (pax บนบิล) · ยอดต่อหัว = ยอดขายสุทธิ ÷ จำนวนหัว'],
      ['ยอดเฉลี่ย/บิล','ยอดขายสุทธิ ÷ จำนวนบิล — หนึ่งบิลอาจมีหลายหัว'],
      ['ต้นทุนอาหาร %','ต้นทุนตามสูตร BoM ÷ ยอดขายสุทธิของเดือนนั้น · เมนูที่ยังไม่มีต้นทุนคิดเป็น ฿0 ตัวเลขจึงเป็น“ขั้นต่ำ” — ดูความครอบคลุมในข้อ 3'],
      ['ฐานการเปรียบเทียบ','รายวัน = วันเดียวกันของสัปดาห์ก่อน · รายสัปดาห์ = สัปดาห์ จ.–อา. ก่อนหน้า · รายเดือน = เดือนก่อน (ถ่วงตามวันในสัปดาห์เมื่อเดือนยังไม่ครบ)'],
      ['ประมาณการ','อัตราเฉลี่ยถ่วงตามวันในสัปดาห์ — ต่อยอดจากวันที่ผ่านมาโดยอิงรูปแบบวันของเดือนก่อน'],
      ['ส่วนลดหนัก','บิลที่ให้ส่วนลดตั้งแต่ 30% ของมูลค่าก่อนลด'],
      ['ส่วนลดจ่ายเงินสด','บิลส่วนลดที่ชำระด้วยเงินสดเป็นหลัก — รูปแบบความเสี่ยงเงินขาด'],
      ['บิลพิมพ์ซ้ำ','บิลส่วนลดที่ถูกพิมพ์มากกว่า 1 ครั้ง'],
      ['บิลยอดศูนย์','บิลที่เหลือ ฿0 หลังหักส่วนลด (แจกฟรี / ส่วนลดล้วน)'],
      ['Tier ราคา','ราคาบุฟเฟ่ต์ผู้ใหญ่บนบิล (ไม่รวม VAT) · “โปรฯ” = ราคานอกมาตรฐาน'],
      ['YoY','ซ่อนก่อนกลาง พ.ค. 2026 — ข้อมูลเริ่ม ~พ.ค. 2025 ช่วงปีก่อนจึงยังไม่ครบ'],
    ],
    defsNote:'ข้อมูลเพื่อการบริหาร ยังไม่ผ่านการตรวจสอบบัญชี จัดทำจากฐานข้อมูลการขาย POS · การวิเคราะห์ tier ส่วนลด และต้นทุนต่อหัว นับเฉพาะบิลบุฟเฟ่ต์',
    dow:{Mon:'จ.',Tue:'อ.',Wed:'พ.',Thu:'พฤ.',Fri:'ศ.',Sat:'ส.',Sun:'อา.'},
    numLocale:'en-US',
  },
};

function renderMgtReport(j, jM, jF) {
  const T = MGT_I18N[mgtLang] || MGT_I18N.en;
  const isTH = (mgtLang === 'th');
  const dpLabel = nm => T[nm] || nm;                       // Lunch/Dinner/Other → localised
  const dowLabel = nm => (T.dow && T.dow[nm]) || nm;       // Mon..Sun → localised
  const scopeLabel = s => {                                // backend branch_scope stays EN keys
    if (!s) return s;
    if (s.toLowerCase() === 'all branches') return T.allBranches;
    if (s.indexOf('Group: ') === 0) return T.groupPfx + s.slice(7);
    return s;
  };
  const baht = n => (n == null) ? '—' : '฿' + Math.round(n).toLocaleString('en-US');
  const num  = n => (n == null) ? '—' : Math.round(n).toLocaleString('en-US');
  // Compact money for headline tiles: ฿18.3M / ฿46k — the full figure stays in the
  // cell's title tooltip via bigCell(). Scanning millions is far faster than counting
  // digits in ฿18,297,820.
  const bigBaht = n => {
    if (n == null) return '—';
    const a = Math.abs(n);
    if (a >= 1e6) return '฿' + (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
    if (a >= 1e4) return '฿' + Math.round(n / 1e3) + 'k';
    return '฿' + Math.round(n).toLocaleString('en-US');
  };
  const bigCell = n => (n == null) ? '—' : `<span title="${baht(n)}">${bigBaht(n)}</span>`;
  // YoY is only trustworthy once the year-ago period sits inside our data window
  // (sales history reliably starts ~mid-May 2025). Before ~mid-May 2026 the year-ago
  // side is empty/partial and YoY balloons — suppress it there. [[no-cumulative-totals-rule]]
  const _pDate = (j.period === 'monthly' && j.range && j.range[1])
    ? j.range[1]
    : (j.selected_week || j.focus || j.date || j.latest_day || null);
  const yoyOk = _pDate ? (new Date(_pDate) >= new Date('2026-05-15')) : true;
  const wow  = p => (p == null)
    ? '<span class="text-slate-400 text-xs">—</span>'
    : `<span class="text-xs font-semibold" style="color:${p >= 0 ? '#059669' : '#dc2626'}">${p >= 0 ? '▲' : '▼'} ${Math.abs(p).toFixed(1)}%</span>`;
  const hasSplit = !!(jM && jF);
  // Compact key-value list used inside each split panel
  const mkKv = pairs => `<div class="space-y-0.5 text-xs">${pairs.map(([l, v]) =>
    `<div class="flex justify-between gap-2 py-0.5 border-b border-slate-50 last:border-0">
       <span class="text-slate-500 shrink-0">${l}</span><span class="font-semibold text-right">${v}</span></div>`
  ).join('')}</div>`;
  // Two-panel (master=teal, franchise=violet) split block appended to a section's content
  const splitPanel = (mKv, fKv) => !hasSplit ? '' :
    `<div class="mt-4 pt-3 border-t border-slate-100">
       <p class="text-[10px] font-bold uppercase tracking-wide text-slate-400 mb-2">${T.masterFranchise}</p>
       <div class="grid grid-cols-2 gap-3">
         <div class="rounded-xl p-3" style="background:#f0fdfa;border:1px solid #5eead4">
           <p class="text-[10px] font-bold uppercase tracking-wide text-teal-700 mb-2">🏢 ${T.master}</p>${mkKv(mKv)}</div>
         <div class="rounded-xl p-3" style="background:#faf5ff;border:1px solid #c4b5fd">
           <p class="text-[10px] font-bold uppercase tracking-wide text-violet-700 mb-2">🔗 ${T.franchise}</p>${mkKv(fKv)}</div>
       </div></div>`;
  const isDaily = j.period === 'daily';
  const isMonthly = j.period === 'monthly';
  const compShort = isDaily ? T.compDaily : isMonthly ? T.compMonthly : T.compWeekly;
  // Delta badge label: daily & weekly compare week-over-week; monthly is month-over-month.
  const dLbl = isMonthly ? 'MoM' : 'WoW';
  // WoW badge straight from current/prev values (the §6/§8/§9/§10 sections whose
  // backend supplies a prev figure rather than a precomputed %). prev=0 → "—".
  const wowVal = (cur, prev) => (prev == null) ? '' : wow(prev ? (cur - prev) / prev * 100 : null);
  // Inverted-sentiment badge for "lower is better" metrics (e.g. avg turn time):
  // the arrow still shows the real direction, but a RISE is red (worse) and a FALL
  // is green (better) — the opposite of wow()'s default colouring.
  const wowInv = p => (p == null)
    ? '<span class="text-slate-400 text-xs">—</span>'
    : `<span class="text-xs font-semibold" style="color:${p <= 0 ? '#059669' : '#dc2626'}">${p >= 0 ? '▲' : '▼'} ${Math.abs(p).toFixed(1)}%</span>`;
  const wowValInv = (cur, prev) => (prev == null) ? '' : wowInv(prev ? (cur - prev) / prev * 100 : null);
  // Compact "vs last week" comparison line for a card (prev value + WoW badge).
  const cmpLine = (label, prevTxt, badge) => (prevTxt == null) ? '' :
    `<p class="text-[10px] mt-1 text-slate-400 flex items-center gap-1 flex-wrap"><span>${compShort}:</span><span class="text-slate-500">${prevTxt}</span>${badge || ''}</p>`;

  // Header band labels
  const setTxt = (id, t) => { const el = document.getElementById(id); if (el) el.textContent = t; };
  setTxt('mgtPeriodLabel', (isDaily ? T.dailyReport : isMonthly ? T.monthlyReport : T.weeklyReport) + j.label
    + (j.partial ? (isDaily ? T.dtd : isMonthly ? T.mtdTag : T.wtd) : ''));
  setTxt('mgtSlimLabel', document.getElementById('mgtPeriodLabel')?.textContent || T.title);
  setTxt('mgtBranchLabel', T.scope + scopeLabel(j.branch_scope) + T.comparedWith + j.comp_label);
  // Static cover-band chrome (title / brand / buttons) follows the language toggle too.
  setTxt('mgtTitle', T.title);
  setTxt('mgtGenLabel', T.generated);
  setTxt('mgtExportBtn', T.exportExcel);
  setTxt('mgtPrintBtn', T.printPdf);
  setTxt('mgtTabDaily', T.daily);
  setTxt('mgtTabWeekly', T.weekly);
  setTxt('mgtTabMonthly', T.monthly);
  try { setTxt('mgtGenDate', new Date().toLocaleString(isTH ? 'th-TH' : 'en-GB', { dateStyle: 'medium', timeStyle: 'short' })); }
  catch (e) { setTxt('mgtGenDate', new Date().toString()); }
  _mgtToggleUI();
  renderMgtWeekPicker(j);
  renderMgtDayPicker(j);
  renderMgtMonthPicker(j);

  const H = j.headline || {};
  // Each section carries a distinct accent (top bar + heading) so the report reads
  // as a professional, colourful template on screen AND in PDF.
  // `o` carries the print plan for the section (see the SECTIONS list below):
  //   o.id   -> anchor id, used by the table of contents
  //   o.land -> print on a LANDSCAPE sheet (wide tables / side-by-side charts);
  //             everything else gets the named PORTRAIT page. Chrome starts a new
  //             sheet whenever the @page name changes, so orientation IS the break.
  //   o.brk  -> force a page break before the section (same-orientation boundaries)
  const sec = (title, inner, accent = '#0f766e', o = {}) =>
    `<section class="card p-6 mgt-sec ${o.land ? 'pg-land' : 'pg-port'}${o.brk ? ' pg-break' : ''}"${o.id ? ` id="${o.id}"` : ''} style="border-top:3px solid ${accent}">
       <h2 class="text-base font-bold mb-4 pb-2" style="color:${accent};border-bottom:2px solid ${accent}33">${title}</h2>${inner}</section>`;
  // w = WoW delta, y = YoY delta (both optional). When given, a second line shows
  // "WoW ▲x% · YoY ▲y%" so management sees week-over-week AND year-over-year at a glance.
  const kpi = (title, value, sub, w, y) =>
    `<div class="rounded-xl p-4" style="background:var(--bg-card);border:1px solid var(--border)">
       <p class="text-[11px] uppercase tracking-wide text-slate-400 mb-1">${title}</p>
       <p class="text-2xl font-bold text-slate-800">${value}</p>
       <p class="text-xs text-slate-500 mt-1">${sub || ''}</p>
       ${(w !== undefined || y != null) ? `<p class="text-[11px] mt-1 flex items-center gap-1 flex-wrap">${w !== undefined ? '<span class="text-slate-400">' + dLbl + '</span>' + wow(w) : ''}${(w !== undefined && y != null) ? '<span class="text-slate-300">·</span>' : ''}${y != null ? '<span class="text-slate-400">YoY</span>' + wow(y) : ''}</p>` : ''}
     </div>`;

  // §1 — Headline KPIs (incl. Gross Profit; WoW + YoY where available)
  const G = j.gp || {};
  const kpis = `<div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
      ${kpi(T.netSalesVat, bigCell(H.revenue),   compShort,  H.rev_wow_pct, yoyOk ? H.rev_yoy_pct : null)}
      ${kpi(T.grossProfit, bigCell(G.gp),
            G.cost_basis === 'unavailable'
              ? `<span style="color:#d97706">${isTH ? 'ไม่มีข้อมูลต้นทุนรายวันก่อน ' + (G.cost_from || '') : 'no daily cost data before ' + (G.cost_from || '')}</span>`
              : (G.gp_pct != null ? G.gp_pct.toFixed(0) + T.marginAfter : T.afterFoodCost))}
      ${kpi(T.customers,   num(H.customers),     T.covers,   H.cust_wow_pct, yoyOk ? H.cust_yoy_pct : null)}
      ${kpi(T.spendHead,   baht(H.rev_per_head), T.perCover, H.rph_wow_pct,  yoyOk ? H.rph_yoy_pct : null)}
      ${kpi(T.avgTicket,   baht(H.avg_ticket),   T.perBill,  H.ticket_wow_pct)}
      ${kpi(T.bills,       num(H.bills),         T.receipts, H.bills_wow_pct)}
      ${kpi(T.discounts,   bigCell(H.discount),   (H.discount_pct != null ? H.discount_pct.toFixed(1) + T.ofSales : ''))}
    </div>` + ((!yoyOk && (H.rev_yoy_pct != null)) ? `<p class="text-[11px] text-slate-400 mt-2">${T.yoyHidden}</p>` : '') + (() => {
    if (!hasSplit) return '';
    const HM = jM.headline || {}, HF = jF.headline || {};
    const GM = jM.gp || {}, GF = jF.gp || {};
    const gpM = GM.gp_pct != null ? ` · ${GM.gp_pct.toFixed(0)}%` : '';
    const gpF = GF.gp_pct != null ? ` · ${GF.gp_pct.toFixed(0)}%` : '';
    return splitPanel(
      [[T.netSales, baht(HM.revenue)], [dLbl, wow(HM.rev_wow_pct)], [T.gp, baht(GM.gp) + gpM],
       [T.customers, num(HM.customers)], [T.spendHeadShort, baht(HM.rev_per_head)], [T.avgTicket, baht(HM.avg_ticket)],
       [T.discounts, baht(HM.discount) + (HM.discount_pct != null ? ` · ${HM.discount_pct.toFixed(1)}%` : '')]],
      [[T.netSales, baht(HF.revenue)], [dLbl, wow(HF.rev_wow_pct)], [T.gp, baht(GF.gp) + gpF],
       [T.customers, num(HF.customers)], [T.spendHeadShort, baht(HF.rev_per_head)], [T.avgTicket, baht(HF.avg_ticket)],
       [T.discounts, baht(HF.discount) + (HF.discount_pct != null ? ` · ${HF.discount_pct.toFixed(1)}%` : '')]]);
  })();

  // §2 — Month pacing & food cost (full month for a past period, MTD for the current one)
  // + weekly net-sales bars for the month and a last-month comparison (MoM).
  const P = j.pace || {};
  const pvt = P.proj_vs_target_pct;
  const mc = P.month_complete;
  const PMx = P.prev_month || {};
  const _fmtMc = v => v >= 1e6 ? '฿' + (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? '฿' + (v / 1e3).toFixed(0) + 'K' : '฿' + Math.round(v || 0);
  // Weekly net-sales bars for the period's month (SVG → screen + print). Each bar
  // gets its own amber tick from the backend's `fair_prev` — last month's weekday-
  // matched revenue for the SAME calendar days that make up that bar — instead of a
  // single flat "average per week" line, which made a partial in-progress week
  // (e.g. 5 of 7 days) look like a cliff-edge shortfall against a full week's norm.
  const weeklyBars = (() => {
    const wk = P.weekly || [];
    if (!wk.length) return '';
    const vals = wk.map(w => w.revenue || 0);
    const fairs = wk.map(w => (w.fair_prev != null ? w.fair_prev : null));
    const mx = Math.max(...vals, ...fairs.filter(f => f != null), 1);
    const W = 900, H = 190, padL = 46, padR = 12, padT = 18, padB = 18, n = wk.length;
    const plotW = W - padL - padR, plotH = H - padT - padB, yMax = mx * 1.14 || 1;
    const xc = i => padL + (i + 0.5) * (plotW / n), yc = v => padT + plotH * (1 - v / yMax);
    const bw = Math.min(120, (plotW / n) * 0.5), li = n - 1;
    let grid = '', ylab = '';
    for (let k = 0; k <= 4; k++) { const vv = yMax * k / 4, yy = yc(vv);
      grid += `<line x1="${padL}" y1="${yy.toFixed(1)}" x2="${W - padR}" y2="${yy.toFixed(1)}" stroke="#e8edf3" stroke-width="0.6"/>`;
      ylab += `<text x="${padL - 4}" y="${(yy + 3).toFixed(1)}" text-anchor="end" font-size="9" fill="#94a3b8">${_fmtMc(vv).replace('฿', '')}</text>`; }
    const bars = vals.map((v, i) => { const by = yc(v), bh = Math.max(0.6, padT + plotH - by);
      return `<rect x="${(xc(i) - bw / 2).toFixed(1)}" y="${by.toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="2" fill="${i === li ? '#4f46e5' : '#c7d2fe'}"/>`; }).join('');
    const vlab = vals.map((v, i) => `<text x="${xc(i).toFixed(1)}" y="${(yc(v) - 4).toFixed(1)}" text-anchor="middle" font-size="9" font-weight="${i === li ? '800' : '600'}" fill="${i === li ? '#4f46e5' : '#64748b'}">${_fmtMc(v)}</text>`).join('');
    const xlab = wk.map((w, i) => `<text x="${xc(i).toFixed(1)}" y="${(H - 5).toFixed(1)}" text-anchor="middle" font-size="9" font-weight="700" fill="#475569">${w.label}</text>`).join('');
    // One amber tick per bar, at THAT bar's own fair-comparison height, instead of a
    // single flat line spanning the whole chart.
    const ticks = fairs.map((f, i) => { if (f == null) return '';
      const ty = yc(f), tx0 = xc(i) - bw / 2 - 3, tx1 = xc(i) + bw / 2 + 3, above = f >= vals[i];
      return `<line x1="${tx0.toFixed(1)}" y1="${ty.toFixed(1)}" x2="${tx1.toFixed(1)}" y2="${ty.toFixed(1)}" stroke="#f59e0b" stroke-width="1.8" stroke-dasharray="4 3"/>`
          + `<text x="${tx1.toFixed(1)}" y="${(ty + (above ? -3 : 9)).toFixed(1)}" text-anchor="end" font-size="8" font-weight="700" fill="#d97706">${_fmtMc(f)}</text>`; }).join('');
    return `<div class="mt-4"><p class="text-xs font-semibold text-slate-500 mb-1">${T.weeklyNetSales}${P.month || ''} <span class="text-slate-400 font-normal">${T.weeklyHint}</span></p>
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" style="width:100%;height:auto;display:block">${grid}${ylab}${bars}${ticks}${vlab}${xlab}</svg></div>`;
  })();
  const momBadge = (P.mom_pct != null) ? ' · MoM ' + wow(P.mom_pct) : '';
  const fcPrevTxt = (P.food_cost_pct_prev != null) ? T.lastMonthSp + P.food_cost_pct_prev.toFixed(1) + '%' : '';
  // Food cost % is a FLOOR: dishes with no unit cost book ฿0 COGS. Disclose the
  // plate-coverage and the estimated true figure so the headline is never read
  // as exact. Silent below ~99.5% coverage would be the dishonest choice.
  const _cc = P.cost_coverage;
  const _fcCoverageNote = (_cc && _cc.coverage_pct != null && _cc.coverage_pct < 99.5)
    ? `<br><span style="color:#d97706">${isTH
        ? `ครอบคลุม ${_cc.coverage_pct}% ของจาน${_cc.est_food_cost_pct != null ? ` · ประมาณการจริง ~${_cc.est_food_cost_pct}%` : ''}`
        : `covers ${_cc.coverage_pct}% of plates${_cc.est_food_cost_pct != null ? ` · est. true ~${_cc.est_food_cost_pct}%` : ''}`}</span>`
    : '';
  // Weekday-fair MoM (backend mom_basis === 'dow' while the month is partial):
  // the shown baseline is last month's per-weekday norm summed over the same
  // elapsed weekdays, so a Wed+Thu sliver is never judged against a holiday Mon+Tue.
  const fairMoM  = P.mom_basis === 'dow' && PMx.baseline_mtd != null;
  const momBase  = fairMoM ? PMx.baseline_mtd : PMx.revenue_mtd;
  const lastMonthCaption = PMx.month
    ? `<p class="text-[11px] text-slate-500 mt-2">${T.lastMonth} (${PMx.month}): <b>${baht(PMx.revenue_full)}</b> ${T.total} · <b>${baht(momBase)}</b> ${fairMoM ? T.dowBase : T.overSame} ${P.days_elapsed || 0} ${(P.days_elapsed || 0) === 1 ? T.day1 : T.dayN}` +
      `${P.mom_pct != null ? ` — ${T.thisMonthIs} <b style="color:${P.mom_pct >= 0 ? '#059669' : '#dc2626'}">${P.mom_pct >= 0 ? '+' : '−'}${Math.abs(P.mom_pct).toFixed(1)}%</b> ${fairMoM ? T.vsDowBase : T.vsSameDays}` : ''}.</p>`
    : '';
  const pace = `<div class="grid grid-cols-2 md:grid-cols-4 gap-3">
      ${kpi(mc ? T.monthNetSales : T.mtdNetSales, baht(P.mtd_revenue), (P.month || '') + ' · ' + (P.mtd_days || 0) + (mc ? T.days : T.daysSoFar) + momBadge)}
      ${kpi(mc ? T.monthTotal : T.projMonthEnd, baht(P.projected), mc ? T.actual : (P.proj_method === 'dow' ? T.runRateDow : T.runRate))}
      ${kpi(T.revTarget, baht(P.target), (pvt != null ? (mc ? T.actualSp : T.projectionSp) + pvt.toFixed(0) + T.ofTarget : T.noTarget))}
      ${kpi(T.foodCostPct + (mc ? '' : T.mtdSuffix), (P.food_cost_pct != null ? P.food_cost_pct.toFixed(1) + '%' : '—'),
            T.bomBasis + fcPrevTxt + _fcCoverageNote)}
    </div>` + lastMonthCaption + weeklyBars + (() => {
    if (!hasSplit) return '';
    const PM = jM.pace || {}, PF = jF.pace || {};
    const fcM = PM.food_cost_pct != null ? PM.food_cost_pct.toFixed(1) + '%' : '—';
    const fcF = PF.food_cost_pct != null ? PF.food_cost_pct.toFixed(1) + '%' : '—';
    const pvtM = PM.proj_vs_target_pct != null ? ` · ${PM.proj_vs_target_pct.toFixed(0)}${T.ofTarget}` : '';
    const pvtF = PF.proj_vs_target_pct != null ? ` · ${PF.proj_vs_target_pct.toFixed(0)}${T.ofTarget}` : '';
    return splitPanel(
      [[T.mtdRevenue, baht(PM.mtd_revenue)], [T.projected, baht(PM.projected) + pvtM],
       [T.foodCostPct, fcM], [T.target, baht(PM.target)]],
      [[T.mtdRevenue, baht(PF.mtd_revenue)], [T.projected, baht(PF.projected) + pvtF],
       [T.foodCostPct, fcF], [T.target, baht(PF.target)]]);
  })();

  // §3 — Revenue trend (canvas for screen; HTML bars for print — same solid colours)
  // Canonical solid colours (no gradients) — single source for legend, canvas & print bars
  const C_HIST = '#93c5fd', C_CURR = '#1e40af';       // single: historical / current bar
  const C_LINE = '#f59e0b';                            // single: trend line (amber)
  const C_TOT_H = '#94a3b8', C_TOT_C = '#334155';     // split Total (slate)
  const C_MST_H = '#7dd3fc', C_MST_C = '#0284c7';     // split Master (sky)
  const C_FRN_H = '#d8b4fe', C_FRN_C = '#9333ea';     // split Franchise (purple)
  const _sw = c => `<span style="display:inline-block;width:12px;height:12px;border-radius:3px;background:${c}"></span>`;
  const _lg = (c, t) => `<span class="flex items-center gap-1.5">${_sw(c)} ${t}</span>`;
  const _period = isDaily ? T.day : isMonthly ? T.month : T.week;
  // Split → 3 separate charts (one per series), each self-labelled, so the legend only
  // carries the shared hints. Single → one chart with the amber moving-average trend line.
  const trendLegend = hasSplit
    ? `<div class="flex flex-wrap items-center gap-4 text-[11px] text-slate-500 mb-2">
         <span class="flex items-center gap-1.5"><span style="display:inline-block;width:16px;height:2px;background:${C_LINE}"></span> ${T.curveMa}</span>
         <span class="text-slate-400">· ${T.darkerLatest} ${_period}</span>
       </div>`
    : `<div class="flex flex-wrap items-center gap-4 text-[11px] text-slate-500 mb-2">
         ${_lg(C_CURR, T.netSales)}
         <span class="flex items-center gap-1.5"><span style="display:inline-block;width:16px;height:2px;background:${C_LINE}"></span> ${T.trendMa}</span>
         <span class="text-slate-400">· ${T.darkLatest} ${_period}</span>
       </div>`;
  // One mini-card per series in split mode (title + latest value + its own canvas).
  const _trendCard = (id, title, color) =>
    `<div style="background:#fafbfc;border:1px solid #eef2f7;border-radius:12px;padding:10px 12px 6px">
       <div class="flex items-center justify-between mb-1">
         <span class="flex items-center gap-1.5 text-xs font-bold text-slate-600">${_sw(color)} ${title}</span>
         <span id="${id}Latest" class="text-xs font-extrabold" style="color:${color}"></span>
       </div>
       <div style="height:170px"><canvas id="${id}"></canvas></div>
     </div>`;
  const trendCanvas = hasSplit
    ? `<div class="grid grid-cols-1 lg:grid-cols-3 gap-3 no-print">
         ${_trendCard('mgtTrendTotal', T.totalSeries, C_TOT_C)}
         ${_trendCard('mgtTrendMaster', T.master, C_MST_C)}
         ${_trendCard('mgtTrendFranchise', T.franchise, C_FRN_C)}
       </div>`
    : `<div style="height:300px" class="no-print"><canvas id="mgtTrendChart"></canvas></div>`;
  const trend = trendLegend + trendCanvas
    + `<div id="mgtTrendHtml" style="display:none"></div>`
    + `<div id="mgtTrendSummary"></div>`
    + `<div id="mgtTrendAnalysis"></div>`;

  // §4 — Branch league table
  const medals = ['🥇','🥈','🥉'];
  const mvBadge = rc => {
    if (rc == null) return '<span class="text-slate-300 text-xs">—</span>';
    if (rc === 0)   return '<span class="text-slate-400 text-xs font-bold">●</span>';
    const up = rc > 0;
    return `<span class="text-xs font-bold" style="color:${up?'#16a34a':'#dc2626'}">${up?'▲':'▼'}${Math.abs(rc)}</span>`;
  };
  const _brs = j.branches || [];
  const _brMax = Math.max(1, ..._brs.map(b => b.revenue || 0));
  const _watch = b => (b.wow_pct != null && b.wow_pct <= -10);   // sharp weekly drop
  const brRows = _brs.map((b, i) => `<tr class="border-b border-slate-50" ${_watch(b) ? 'style="background:#fef2f2"' : ''}>
      <td class="py-2 pl-1 text-slate-400 font-mono text-xs w-5 text-center">${b.rank || (i+1)}</td>
      <td class="py-2 w-7 text-center">${mvBadge(b.rank_change)}</td>
      <td class="py-2">${medals[i] !== undefined ? medals[i] + ' ' : ''}${esc(b.branch)}${_watch(b) ? ` <span class="text-[10px] px-1 rounded whitespace-nowrap" style="background:#fee2e2;color:#dc2626">${T.watch}</span>` : ''}</td>
      <td class="py-2 text-right" style="min-width:96px">
        <span class="font-semibold" title="${baht(b.revenue)}">${bigBaht(b.revenue)}</span>
        <span style="display:inline-block;height:4px;border-radius:2px;background:#7c3aed;vertical-align:middle;margin-left:6px;width:${Math.max(4, Math.round((b.revenue || 0) / _brMax * 48))}px"></span>
      </td>
      <td class="py-2 text-right">${num(b.customers)}</td>
      <td class="py-2 text-right">${baht(b.rev_per_head)}</td>
      <td class="py-2 text-right">${b.share_pct != null ? b.share_pct.toFixed(1) + '%' : '—'}</td>
      <td class="py-2 text-right pr-1">${wow(b.wow_pct)}</td>
    </tr>`).join('');
  // Verdict line: leader + the sharpest decliner to watch (if any)
  const _brTop = _brs.find(b => (b.rank || 99) === 1) || _brs[0];
  const _brDecl = _brs.filter(b => b.wow_pct != null).slice().sort((a, b) => a.wow_pct - b.wow_pct)[0];
  const branchVerdict = _brs.length ? `<p class="text-xs mb-3 flex flex-wrap items-center gap-x-3 gap-y-1">
      <span class="text-slate-500">${_brs.length} ${T.branchesWord}</span>
      ${_brTop ? `<span>🥇 <b>${esc(_brTop.branch)}</b> <span class="text-slate-400" title="${baht(_brTop.revenue)}">${bigBaht(_brTop.revenue)}</span></span>` : ''}
      ${(_brDecl && _brDecl.wow_pct != null && _brDecl.wow_pct < 0) ? `<span style="color:#dc2626">${T.watch}: <b>${esc(_brDecl.branch)}</b> ${wow(_brDecl.wow_pct)}</span>` : ''}
    </p>` : '';
  const branchTbl = branchVerdict + `<div class="overflow-x-auto"><table class="w-full text-sm">
      <thead><tr class="text-[11px] uppercase tracking-wide text-slate-400 border-b border-slate-200">
        <th class="text-left py-2 pl-1 w-5">#</th><th class="py-2 w-7"></th>
        <th class="text-left py-2">${T.branch}</th><th class="text-right py-2">${T.netSales}</th>
        <th class="text-right py-2">${T.covers}</th><th class="text-right py-2">${T.spendHeadShort}</th>
        <th class="text-right py-2">${T.share}</th><th class="text-right py-2 pr-1">${compShort}</th>
      </tr></thead><tbody>${brRows || `<tr><td class="py-3 text-slate-400" colspan="8">${T.noBranchData}</td></tr>`}</tbody>
    </table></div>`;

  // §5 — Money-leak radar
  const L = j.leak || {};
  const leakCard = (title, o, hint) => {
    o = o || {};
    if (o.unavailable) return `<div class="rounded-xl p-4 opacity-60" style="border:1px solid var(--border)">
        <p class="text-[11px] uppercase tracking-wide text-slate-400 mb-1">${title}</p>
        <p class="text-sm text-slate-400">${T.naNoPay}</p></div>`;
    const bad = (o.bills || 0) > 0;
    const top = (o.by_branch || []).slice(0, 2).map(x => x.branch).join(', ');
    // last-week comparison: prev bill count + WoW on bills (badge) and baht
    const cmp = (o.prev_bills != null)
      ? `<p class="text-[10px] mt-1 text-slate-400 flex items-center gap-1 flex-wrap"><span>${compShort}:</span><span class="text-slate-500">${num(o.prev_bills)} ${T.bills}</span>${wow(o.bills_wow)}<span class="text-slate-300">·</span><span class="text-slate-500">${baht(o.prev_baht)}</span>${wow(o.baht_wow)}</p>`
      : '';
    return `<div class="rounded-xl p-4" style="border:1px solid var(--border);border-left:3px solid ${bad ? '#dc2626' : '#10b981'}">
        <p class="text-[11px] uppercase tracking-wide text-slate-400 mb-1">${title}</p>
        <p class="text-xl font-bold ${bad ? 'text-rose-600' : 'text-slate-700'}">${num(o.bills || 0)} <span class="text-xs font-normal text-slate-400">${T.bills}</span></p>
        <p class="text-xs text-slate-500">${baht(o.baht || 0)}${top ? ' · ' + top : ''}</p>
        ${cmp}
        <p class="text-[11px] text-slate-400 mt-1">${hint}</p></div>`;
  };
  // Reconciling total: ALL discounted bills this period (the cards below are the
  // abnormal-only subsets carved from it). Ties out with the Discounts page for the
  // same scope — both = SUM(Sale.Discount).
  const lt = L.total || {};
  // Hero: total discount ฿ as a big number, tinted by severity (% of sales), with a
  // one-line verdict naming the biggest abnormal leak — so the eye lands on the size
  // of the leak and the single thing to chase, not a row of equal-weight cards.
  const _discPct = H.discount_pct;
  const _sevCol = _discPct == null ? '#64748b' : _discPct >= 10 ? '#dc2626' : _discPct >= 6 ? '#d97706' : '#059669';
  const _sevTxt = _discPct == null ? '' : (isTH ? (_discPct >= 10 ? 'สูง' : _discPct >= 6 ? 'เฝ้าระวัง' : 'ปกติ')
                                                 : (_discPct >= 10 ? 'high' : _discPct >= 6 ? 'watch' : 'normal'));
  const _worst = [[T.heavyDisc, L.heavy_discount], [T.cashHeavy, L.cash_heavy], [T.reprinted, L.reprinted], [T.zeroGross, L.zero_gross]]
    .map(([lbl, o]) => ({ lbl, o: o || {} })).filter(x => (x.o.bills || 0) > 0)
    .sort((a, b) => (b.o.baht || 0) - (a.o.baht || 0))[0];
  const _wBr = (_worst && _worst.o.by_branch && _worst.o.by_branch[0]) ? ' — ' + esc(_worst.o.by_branch[0].branch) : '';
  const _leakVerdict = _worst
    ? (isTH ? `เด่นสุด: <b>${esc(_worst.lbl)}</b> ${num(_worst.o.bills)} บิล (${bigBaht(_worst.o.baht)})${_wBr}`
            : `Biggest: <b>${esc(_worst.lbl)}</b> ${num(_worst.o.bills)} bills (${bigBaht(_worst.o.baht)})${_wBr}`)
    : (isTH ? 'ไม่พบบิลผิดปกติในช่วงนี้' : 'No abnormal bills flagged this period');
  const leakTotalBanner = `<div class="rounded-xl p-4 mb-3" style="border:1px solid var(--border);border-left:3px solid ${_sevCol};background:#f8fafc">
      <div class="flex items-end justify-between flex-wrap gap-2">
        <div>
          <p class="text-[11px] uppercase tracking-wide text-slate-400 mb-0.5">${T.totalDiscounts} ${isDaily ? T.dayWord : isMonthly ? T.monthWord : T.weekWord}</p>
          <p class="text-2xl font-bold" style="color:${_sevCol}" title="${baht(lt.baht || 0)}">${bigBaht(lt.baht || 0)}</p>
        </div>
        <div class="text-right text-xs text-slate-500">
          ${_discPct != null ? `<p><b style="color:${_sevCol}">${_discPct.toFixed(1)}%</b> ${T.ofSales}${_sevTxt ? ` · <span class="font-semibold" style="color:${_sevCol}">${_sevTxt}</span>` : ''}</p>` : ''}
          <p>${num(lt.bills || 0)} ${T.bills}${lt.prev_baht != null ? ` · ${compShort} ${wow(lt.baht_wow)}` : ''}</p>
        </div>
      </div>
      <p class="text-[12px] text-slate-600 mt-2 pt-2 border-t border-slate-100"><span style="color:${_sevCol}">●</span> ${_leakVerdict}</p>
      <p class="text-[10px] text-slate-300 mt-1">${T.allDiscTie}</p>
    </div>`;
  const leak = leakTotalBanner + `<div class="grid grid-cols-2 md:grid-cols-4 gap-3">
      ${leakCard(T.heavyDisc, L.heavy_discount, T.heavyHint)}
      ${leakCard(T.cashHeavy, L.cash_heavy, T.cashHint)}
      ${leakCard(T.reprinted, L.reprinted, T.reprintHint)}
      ${leakCard(T.zeroGross, L.zero_gross, T.zeroHint)}
    </div>`;

  // §6 — Menu cost per head (food COGS / cover, by dish), like วิเคราะห์เมนู
  const M = j.menu || {};
  const cnum = v => (Math.round(v * 100) / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const cph  = v => (v == null) ? '—' : '฿' + cnum(v);
  // Highest-cost list adopts the §11 treatment: rank number + proportion bar +
  // amber heat on the value, so the priciest dishes jump out at a glance.
  const _mMax = Math.max(1, ...((M.top || []).map(x => x.cost_per_head || 0)));
  const _mHeat = v => `background:rgba(202,138,4,${(0.08 + 0.42 * (v / _mMax)).toFixed(2)})`;
  const liTop = (x, i) => `<li class="flex items-center gap-2 py-1.5 border-b border-slate-50 text-sm">
      <span class="text-slate-400 tabular-nums w-4 text-right">${i + 1}</span>
      <span class="truncate flex-1">${esc(x.menu)}</span>
      <span style="display:inline-block;height:5px;border-radius:3px;background:#ca8a04;width:${Math.max(3, Math.round((x.cost_per_head || 0) / _mMax * 38))}px"></span>
      <span class="font-semibold whitespace-nowrap rounded px-1" style="${_mHeat(x.cost_per_head || 0)}">${cph(x.cost_per_head)}<span class="text-[11px] text-slate-400">${T.perHead}</span></span>
    </li>`;
  // rising food cost/head = margin risk (red); falling = good (green)
  const liMove = x => {
    const up = x.delta >= 0;
    return `<li class="py-1.5 border-b border-slate-50 text-sm">
      <div class="flex items-center justify-between gap-3">
        <span class="truncate">${esc(x.menu)}</span>
        <span class="font-semibold whitespace-nowrap" style="color:${up ? '#dc2626' : '#059669'}">${up ? '▲ +' : '▼ −'}฿${cnum(Math.abs(x.delta))}<span class="text-[11px]">${T.perHead}</span></span>
      </div>
      <div class="text-[11px] text-slate-400">${cph(x.cost_per_head_prev)} → ${cph(x.cost_per_head)}${x.pct != null ? ` · ${x.pct >= 0 ? '+' : ''}${x.pct.toFixed(0)}%` : ''}</div>
    </li>`;
  };
  const listT = (arr, empty) => (arr && arr.length) ? `<ul>${arr.map(liTop).join('')}</ul>` : `<p class="text-sm text-slate-400 py-2">${empty}</p>`;
  const listM = (arr, empty) => (arr && arr.length) ? `<ul>${arr.map(liMove).join('')}</ul>` : `<p class="text-sm text-slate-400 py-2">${empty}</p>`;
  // No cost source for this window (pre-dates the rolling sales_sku_daily table and
  // isn't whole calendar months) → say so, instead of an empty section that reads
  // like "this period had no food cost".
  const totLine = (M.cost_basis === 'unavailable')
    ? `<p class="text-xs mb-3" style="color:#d97706">${isTH
        ? `ไม่มีข้อมูลต้นทุนรายเมนูสำหรับช่วงนี้ — รายละเอียดรายวันเริ่ม ${M.cost_from || ''} (เลือกโหมด Monthly เพื่อดูเดือนเก่ากว่านั้น)`
        : `No per-dish cost data for this window — daily detail starts ${M.cost_from || ''} (use Monthly mode for older periods)`}</p>`
    : (M.total_cph != null)
    ? `<p class="text-xs text-slate-500 mb-3">${T.totFoodHead}<b>${cph(M.total_cph)}</b>${M.total_cph_prev != null ? ` (${T.was} ${cph(M.total_cph_prev)}${M.total_cph_delta != null ? `, ${M.total_cph_delta >= 0 ? '+' : '−'}฿${cnum(Math.abs(M.total_cph_delta))}` : ''})` : ''} · ${num(M.covers)}${T.overCovers}</p>`
    : '';
  // Actionable worklist: the biggest dishes with NO unit cost, ranked by plates.
  // Ranking these by revenue (the old coverage view) hid them entirely — buffet
  // dishes carry ~฿0 line revenue, so the costliest gaps never surfaced.
  const uncostedBlock = (() => {
    const cc = P.cost_coverage;
    if (!cc || !(cc.top_uncosted || []).length || cc.coverage_pct >= 99.5) return '';
    const rows = cc.top_uncosted.map((x, i) => `<li class="flex items-center gap-2 py-1 border-b border-slate-50 text-sm">
        <span class="text-slate-400 tabular-nums w-4 text-right">${i + 1}</span>
        <span class="truncate flex-1">${esc(x.menu)}</span>
        <span class="text-slate-400 text-[11px] tabular-nums">${num(x.plates)} ${isTH ? 'จาน' : 'plates'}</span>
      </li>`).join('');
    return `<div class="mt-4 pt-3 border-t border-slate-100">
        <p class="text-xs font-semibold mb-1" style="color:#d97706">${isTH
          ? `⚠️ เมนูที่ยังไม่มีต้นทุน — ${num(cc.uncosted_plates)} จาน (${cc.skus_uncosted} รายการ) ทำให้ต้นทุนอาหารต่ำกว่าจริงราว ฿${num(cc.est_missing_cost)}`
          : `⚠️ Dishes with no unit cost — ${num(cc.uncosted_plates)} plates (${cc.skus_uncosted} SKUs) understate food cost by about ฿${num(cc.est_missing_cost)}`}</p>
        <p class="text-[10px] text-slate-400 mb-2">${isTH
          ? 'เรียงตามจำนวนจาน (ไม่ใช่ยอดขาย — เมนูบุฟเฟ่ต์ไม่มีรายได้รายบรรทัด) · เติมต้นทุนที่ ตั้งค่า → Cost Management'
          : 'ranked by plates, not revenue (buffet dishes carry no line revenue) · add costs in Settings → Cost Management'}</p>
        <ul>${rows}</ul></div>`;
  })();
  const noCmp = j.has_prev ? T.none : T.noCompPeriod;
  const menu = totLine + `<div class="grid grid-cols-1 md:grid-cols-3 gap-5">
      <div><p class="text-xs font-semibold text-slate-500 mb-1">${T.highestCostHead}</p>${listT(M.top, T.noData)}</div>
      <div><p class="text-xs font-semibold text-slate-500 mb-1">${T.costHeadRising}${compShort}</p>${listM(M.risers, noCmp)}</div>
      <div><p class="text-xs font-semibold text-slate-500 mb-1">${T.costHeadFalling}${compShort}</p>${listM(M.savers, noCmp)}</div>
    </div>` + uncostedBlock;

  // §8 — Peak Hours & Dayparts
  const PK = j.peak || {};
  const dpColor = { Lunch: '#f59e0b', Dinner: '#6366f1', Other: '#94a3b8' };
  const hh = n => String(n).padStart(2, '0') + ':00';
  const miniBars = (items, valKey, lblKey, hi) => {
    const mx = Math.max(...items.map(i => i[valKey] || 0), 1);
    return `<div class="flex items-end gap-1" style="height:104px">${items.map(i =>
      `<div class="flex-1 flex flex-col items-center justify-end" title="${baht(i[valKey])}">
         <div class="w-full rounded-t" style="height:${Math.max(3, (i[valKey] || 0) / mx * 80)}px;background:${hi(i) ? '#0284c7' : '#cbd5e1'}"></div>
         <span class="text-[9px] text-slate-500 mt-1">${i[lblKey]}</span></div>`).join('')}</div>`;
  };
  // per-daypart revenue carries a WoW badge vs the same daypart last week/period
  const PKprev = (PK.prev || {}).dayparts || {};
  const dpBars = (PK.dayparts || []).map(d => {
    const pv = PKprev[d.name];
    return `
    <div class="mb-2">
      <div class="flex justify-between text-xs mb-0.5"><span class="font-medium text-slate-600">${dpLabel(d.name)}</span>
        <span class="text-slate-500">${baht(d.revenue)}${d.pct != null ? ' · ' + d.pct.toFixed(0) + '%' : ''} · ${num(d.covers)} ${T.covers} ${pv != null ? wowVal(d.revenue, pv) : ''}</span></div>
      <div class="h-2 rounded-full" style="background:var(--bg-subtle)"><div class="h-2 rounded-full" style="width:${d.pct || 0}%;background:${dpColor[d.name] || '#0891b2'}"></div></div>
    </div>`;
  }).join('');
  // total-revenue WoW + peak-hour WoW for the §8 summary line
  const PKp = PK.prev || {};
  const peakTotTxt = (PK.total_rev != null)
    ? `${T.totalC}: <b>${baht(PK.total_rev)}</b>${PKp.total_rev != null ? ' ' + wowVal(PK.total_rev, PKp.total_rev) : ''}`
    : '';
  const peakHourTxt = PK.peak_hour ? `${T.busiestHour}: <b>${hh(PK.peak_hour.hour)}</b> (${baht(PK.peak_hour.revenue)})${PKp.peak_hour ? ' ' + wowVal(PK.peak_hour.revenue, PKp.peak_hour.revenue) : ''}` : '';
  const bestDayTxt  = PK.best_day ? `${T.busiestDay}: <b>${dowLabel(PK.best_day)}</b>` : '';
  // Right panel mirrors the Daily report: always the hourly breakdown (weekly hourly
  // is the week's covers/revenue summed by hour-of-day — same shape as daily).
  const rightPanel = (PK.hourly || []).length
    ? `<p class="text-xs font-semibold text-slate-500 mb-1">${T.byHour}</p>${miniBars(PK.hourly.filter(x => x.revenue > 0), 'revenue', 'hour', x => PK.peak_hour && x.hour === PK.peak_hour.hour)}`
    : '';
  // Weekly keeps its extra day-of-week insight, shown as a full-width row BENEATH the
  // daily-style 2-column grid so the core Peak-Hours layout still matches the Daily report.
  const byDayRow = (!isDaily && (PK.by_day || []).length)
    ? `<div class="mt-4 pt-3 border-t border-slate-100"><p class="text-xs font-semibold text-slate-500 mb-1">${T.byDow}</p>${miniBars(PK.by_day.map(d => ({...d, dowL: dowLabel(d.dow)})), 'revenue', 'dowL', d => d.dow === PK.best_day)}</div>`
    : '';
  const peakHtml = `<div class="grid grid-cols-1 md:grid-cols-2 gap-6">
      <div><p class="text-xs font-semibold text-slate-500 mb-2">${T.daypartSplit} <span class="font-normal text-slate-400">${T.smallPct}${compShort}</span></p>${dpBars || `<p class="text-sm text-slate-400">${T.noData2}</p>`}
        <p class="text-xs text-slate-500 mt-2">${peakTotTxt}${peakTotTxt && (peakHourTxt || bestDayTxt) ? ' · ' : ''}${peakHourTxt}${peakHourTxt && bestDayTxt ? ' · ' : ''}${bestDayTxt}</p></div>
      <div>${rightPanel}</div>
    </div>${byDayRow}`;

  // §9 — Table Turnover & Occupancy (only when the data exists)
  const TB = j.table || {};
  let tableHtml = '';
  if (TB.available) {
    const TBp = TB.prev || {};
    const km = (t, v, s, w) => `<div class="rounded-xl p-4" style="background:var(--bg-card);border:1px solid var(--border)">
        <p class="text-[11px] uppercase tracking-wide text-slate-400 mb-1">${t}</p><p class="text-2xl font-bold text-slate-800">${v}</p>
        <p class="text-xs text-slate-500 mt-1 flex items-center gap-1 flex-wrap">${s || ''}${w ? `<span class="text-slate-300">·</span><span class="text-[10px] text-slate-400">${compShort}</span>${w}` : ''}</p></div>`;
    const trows = (TB.by_branch || []).map(b => `<tr class="border-b border-slate-50">
        <td class="py-2 pl-1">${esc(b.branch)}</td>
        <td class="py-2 text-right">${num(b.tables)}</td>
        <td class="py-2 text-right">${num(b.covers)}</td>
        <td class="py-2 text-right font-semibold">${b.covers_per_table != null ? b.covers_per_table.toFixed(2) : '—'}</td>
        <td class="py-2 text-right pr-1">${b.avg_turn_min != null ? Math.round(b.avg_turn_min) + ' ' + T.min : '—'}</td></tr>`).join('');
    tableHtml = `<div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        ${km(T.coversTable, TB.covers_per_table != null ? TB.covers_per_table.toFixed(2) : '—', T.turnsPerTable, TBp.covers_per_table != null ? wowVal(TB.covers_per_table, TBp.covers_per_table) : '')}
        ${km(T.avgTurnTime, TB.avg_turn_min != null ? Math.round(TB.avg_turn_min) + ' ' + T.min : '—', T.perSession, TBp.avg_turn_min != null ? wowValInv(TB.avg_turn_min, TBp.avg_turn_min) : '')}
        ${km(T.tablesUsed, num(TB.tables), T.tableSessions, TBp.tables != null ? wowVal(TB.tables, TBp.tables) : '')}
        ${km(T.totalCovers, num(TB.covers), T.guestsSeated, TBp.covers != null ? wowVal(TB.covers, TBp.covers) : '')}
      </div>
      <div class="overflow-x-auto"><table class="w-full text-sm">
        <thead><tr class="text-[11px] uppercase tracking-wide text-slate-400 border-b border-slate-200">
          <th class="text-left py-2 pl-1">${T.branch}</th><th class="text-right py-2">${T.tables}</th><th class="text-right py-2">${T.covers}</th>
          <th class="text-right py-2">${T.coversTableShort}</th><th class="text-right py-2 pr-1">${T.avgTurn}</th>
        </tr></thead><tbody>${trows}</tbody></table></div>`;
  }

  // §7 — Action items
  const sevColor = { high: '#dc2626', med: '#d97706', low: '#10b981' };
  const acts = (j.actions || []).map(a => `<li class="flex items-start gap-2 text-sm py-1">
      <span class="mt-0.5" style="color:${sevColor[a.sev] || '#64748b'}">●</span><span>${esc(a.text)}</span></li>`).join('');
  const actsWithSplit = `<ul class="space-y-0.5">${acts}</ul>` + (() => {
    if (!hasSplit) return '';
    const fmtActs = jx => {
      const items = (jx.actions || []).filter(a => a.sev !== 'low');
      if (!items.length) return `<p class="text-xs text-slate-400 py-1">${T.noCritical}</p>`;
      return `<ul class="space-y-0.5">${items.map(a =>
        `<li class="flex items-start gap-1.5 text-xs py-0.5">
           <span style="color:${sevColor[a.sev]||'#64748b'}">●</span><span>${esc(a.text)}</span></li>`
      ).join('')}</ul>`;
    };
    return `<div class="mt-4 pt-3 border-t border-slate-100">
      <p class="text-[10px] font-bold uppercase tracking-wide text-slate-400 mb-2">${T.mfPriority}</p>
      <div class="grid grid-cols-2 gap-3">
        <div class="rounded-xl p-3" style="background:#f0fdfa;border:1px solid #5eead4">
          <p class="text-[10px] font-bold uppercase tracking-wide text-teal-700 mb-2">🏢 ${T.master}</p>${fmtActs(jM)}</div>
        <div class="rounded-xl p-3" style="background:#faf5ff;border:1px solid #c4b5fd">
          <p class="text-[10px] font-bold uppercase tracking-wide text-violet-700 mb-2">🔗 ${T.franchise}</p>${fmtActs(jF)}</div>
      </div></div>`;
  })();

  // ── Executive Summary — auto-written ~10-paragraph overview of everything ──
  // Each paragraph is colour-tagged ({c, lead, body}) so the EN ⇄ TH lead labels
  // never need to be reverse-matched; lead + bold figures are tinted with that colour.
  const para   = p => '<p class="mb-2"><b style="color:' + p.c + '">' + p.lead + '</b> '
                    + (p.body || '').replace(/<b>/g, '<b style="color:' + p.c + '">') + '</p>';
  const mag    = v => v == null ? '' : Math.abs(v).toFixed(1) + '%';
  const signed = v => v == null ? '' : (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(1) + '%';
  const moved  = (v, up, dn, flat) => v == null ? flat : v >= 1 ? up : v <= -1 ? dn : flat;
  const compW  = isTH ? (isDaily ? 'วันเดียวกันสัปดาห์ก่อน' : isMonthly ? 'เดือนก่อนหน้า' : 'สัปดาห์ก่อนหน้า')
                      : (isDaily ? 'the same weekday last week' : isMonthly ? 'the previous month' : 'the previous week');
  const pNoun  = isTH ? (isDaily ? 'วัน' : isMonthly ? 'เดือน' : 'สัปดาห์') : (isDaily ? 'day' : isMonthly ? 'month' : 'week');
  const brs    = j.branches || [];
  const ex     = [];

  // 1 — Overview (+ Gross Profit + YoY)
  const scopeTxt = (j.branch_scope || '').toLowerCase() === 'all branches'
    ? (isTH ? 'ทั้งเครือ' : 'the chain') : scopeLabel(j.branch_scope);
  const Gx = j.gp || {};
  if (isTH) {
    const gpClause = Gx.gp_pct != null ? ' — กำไรขั้นต้น <b>' + baht(Gx.gp) + '</b> (อัตรากำไร <b>' + Gx.gp_pct.toFixed(0) + '%</b> หลังหักต้นทุนอาหาร)' : '';
    const ovYoY = (H.rev_yoy_pct != null) ? ' เทียบช่วงเดียวกันปีก่อน ยอดขาย' + moved(H.rev_yoy_pct, 'เพิ่มขึ้น', 'ลดลง', 'ทรงตัว') + ' ' + mag(H.rev_yoy_pct) : '';
    const ovVs  = H.rev_wow_pct != null
      ? 'ยอดขาย' + moved(H.rev_wow_pct, 'เพิ่มขึ้น', 'ลดลง', 'ทรงตัว') + ' ' + mag(H.rev_wow_pct) + ' เทียบ' + compW + ovYoY
      : 'ยังไม่มี' + pNoun + 'ก่อนหน้าให้เปรียบเทียบในข้อมูล' + ovYoY;
    ex.push({c:'#0f766e', lead:'ภาพรวม', body:'สำหรับ ' + j.label + ' ' + scopeTxt + ' มียอดขายสุทธิ <b>' + baht(H.revenue) +
            '</b> (ไม่รวม VAT) จาก <b>' + num(H.customers) + '</b> หัว ใน <b>' + num(H.bills) + '</b> บิล' + gpClause + ' ' + ovVs});
  } else {
    const gpClause = Gx.gp_pct != null ? ' — a gross profit of <b>' + baht(Gx.gp) + '</b> (<b>' + Gx.gp_pct.toFixed(0) + '%</b> margin after food cost)' : '';
    const ovYoY = (H.rev_yoy_pct != null) ? ' Year-on-year, net sales are ' + moved(H.rev_yoy_pct, 'up', 'down', 'flat') + ' ' + mag(H.rev_yoy_pct) + '.' : '';
    const ovVs  = H.rev_wow_pct != null
      ? 'Net sales ' + moved(H.rev_wow_pct, 'rose', 'fell', 'were flat') + ' ' + mag(H.rev_wow_pct) + ' versus ' + compW + '.' + ovYoY
      : 'There is no comparable prior ' + pNoun + ' in the data yet.' + ovYoY;
    ex.push({c:'#0f766e', lead:'Overview.', body:'For ' + j.label + ', ' + scopeTxt + ' booked net sales of <b>' + baht(H.revenue) +
            '</b> (ex-VAT) from <b>' + num(H.customers) + '</b> covers across <b>' + num(H.bills) + '</b> bills' + gpClause + '. ' + ovVs});
  }

  // 2 — Revenue quality (+ peak / dayparts / table turnover)
  const PKx = j.peak || {};
  const TBx = j.table || {};
  if (isTH) {
    const driver = (Math.abs(H.rph_wow_pct || 0) > Math.abs(H.cust_wow_pct || 0)) ? 'ยอดใช้จ่ายต่อหัว' : 'จำนวนลูกค้า';
    const rqVs = H.cust_wow_pct != null
      ? ' จำนวนหัว' + moved(H.cust_wow_pct, 'เพิ่มขึ้น', 'ลดลง', 'ทรงตัว') + ' ' + mag(H.cust_wow_pct) +
        ' ขณะที่ยอดต่อหัว' + moved(H.rph_wow_pct, 'เพิ่มขึ้น', 'ลดลง', 'ทรงตัว') + ' ' + mag(H.rph_wow_pct) +
        ' การเปลี่ยนแปลงจึงมาจาก <b>' + driver + '</b> เป็นหลัก'
      : '';
    const peakS = PKx.peak_hour ? ' ช่วงคึกคักที่สุดราว <b>' + String(PKx.peak_hour.hour).padStart(2, '0') + ':00</b>' + (PKx.best_day ? ' และ <b>' + dowLabel(PKx.best_day) + '</b> เป็นวันที่ขายดีที่สุด' : '') : '';
    const tableS = TBx.available && TBx.covers_per_table != null ? ' โต๊ะหมุนเวียน <b>' + TBx.covers_per_table.toFixed(2) + '</b> หัวต่อโต๊ะ' + (TBx.avg_turn_min != null ? ' (เฉลี่ย ' + Math.round(TBx.avg_turn_min) + ' นาที/รอบ)' : '') : '';
    ex.push({c:'#0891b2', lead:'คุณภาพรายได้', body:'ลูกค้าใช้จ่ายเฉลี่ย <b>' + baht(H.rev_per_head) + '</b> ต่อหัว และยอดต่อบิลโดยทั่วไปอยู่ที่ <b>' + baht(H.avg_ticket) + '</b>' + rqVs + peakS + tableS});
  } else {
    const driver = (Math.abs(H.rph_wow_pct || 0) > Math.abs(H.cust_wow_pct || 0)) ? 'spend per guest' : 'customer traffic';
    const rqVs = H.cust_wow_pct != null
      ? ' Covers ' + moved(H.cust_wow_pct, 'rose', 'fell', 'held') + ' ' + mag(H.cust_wow_pct) +
        ' while spend per head ' + moved(H.rph_wow_pct, 'rose', 'fell', 'held') + ' ' + mag(H.rph_wow_pct) +
        ', so the shift was driven mainly by <b>' + driver + '</b>.'
      : '';
    const peakS = PKx.peak_hour ? ' Trade peaked around <b>' + String(PKx.peak_hour.hour).padStart(2, '0') + ':00</b>' + (PKx.best_day ? ', and <b>' + dowLabel(PKx.best_day) + '</b> was the busiest day' : '') + '.' : '';
    const tableS = TBx.available && TBx.covers_per_table != null ? ' Tables turned <b>' + TBx.covers_per_table.toFixed(2) + '</b> covers each' + (TBx.avg_turn_min != null ? ' (avg ' + Math.round(TBx.avg_turn_min) + ' min/turn)' : '') + '.' : '';
    ex.push({c:'#0891b2', lead:'Revenue quality.', body:'Guests spent <b>' + baht(H.rev_per_head) + '</b> per head on average and the typical bill was <b>' + baht(H.avg_ticket) + '</b>.' + rqVs + peakS + tableS});
  }

  // 3 — Branches
  if (brs.length) {
    const best = brs[0], worst = brs[brs.length - 1];
    const upN = brs.filter(b => b.wow_pct != null && b.wow_pct > 0).length;
    const dnN = brs.filter(b => b.wow_pct != null && b.wow_pct < 0).length;
    const mvs = brs.filter(b => b.wow_pct != null).slice().sort((a, b) => Math.abs(b.wow_pct) - Math.abs(a.wow_pct));
    if (isTH) {
      const mv  = (j.has_prev && mvs.length) ? ' โดยสาขาที่เปลี่ยนแปลงมากสุดคือ <b>' + esc(mvs[0].branch) +'</b> ที่ ' + signed(mvs[0].wow_pct) : '';
      const sh  = best.share_pct != null ? ' (' + best.share_pct.toFixed(0) + '% ของทั้งเครือ)' : '';
      const cmp = j.has_prev ? ' ' + upN + ' สาขาเติบโตและ ' + dnN + ' สาขาลดลงเทียบ' + compW + mv : '';
      ex.push({c:'#7c3aed', lead:'สาขา', body:'จาก ' + brs.length + ' สาขา <b>' + esc(best.branch) +'</b> นำด้วย ' +
              baht(best.revenue) + sh + ' ขณะที่ <b>' + esc(worst.branch) +'</b> ต่ำสุดที่ ' + baht(worst.revenue) + cmp});
    } else {
      const mv  = (j.has_prev && mvs.length) ? '; the biggest swing was <b>' + esc(mvs[0].branch) +'</b> at ' + signed(mvs[0].wow_pct) : '';
      const sh  = best.share_pct != null ? ' (' + best.share_pct.toFixed(0) + '% of the chain)' : '';
      const cmp = j.has_prev ? ' ' + upN + ' grew and ' + dnN + ' declined vs ' + compW + mv + '.' : '';
      ex.push({c:'#7c3aed', lead:'Branches.', body:'Of ' + brs.length + ' branch' + (brs.length > 1 ? 'es' : '') + ', <b>' + esc(best.branch) +'</b> led with ' +
              baht(best.revenue) + sh + ', while <b>' + esc(worst.branch) +'</b> trailed at ' + baht(worst.revenue) + '.' + cmp});
    }
  }

  // 4 — Month pace
  if (P.mtd_revenue != null) {
    if (isTH) {
      const tgt  = P.target
        ? ' — ' + (P.proj_vs_target_pct != null ? '<b>' + P.proj_vs_target_pct.toFixed(0) + '%</b> ของเป้า ' + baht(P.target) : 'เทียบเป้า ' + baht(P.target))
        : ' (ยังไม่ตั้งเป้ายอดขาย)';
      const proj = mc ? ' เป็นยอดรวมสุดท้ายของ ' + P.month : ' ที่จังหวะปัจจุบัน' + (P.proj_method === 'dow' ? ' (ถ่วงตามวันในสัปดาห์)' : '') + ' คาดว่าทั้งเดือนจะปิดที่ราว <b>' + baht(P.projected) + '</b>';
      const lead2 = mc ? 'ทั้งเดือน' : 'สะสมเดือนนี้ (' + P.month + ')';
      ex.push({c:'#4f46e5', lead:'จังหวะทั้งเดือน', body:lead2 + 'อยู่ที่ <b>' + baht(P.mtd_revenue) + '</b> จาก ' + (P.mtd_days || 0) + ' วัน' + proj + tgt});
    } else {
      const tgt  = P.target
        ? ' — ' + (P.proj_vs_target_pct != null ? '<b>' + P.proj_vs_target_pct.toFixed(0) + '%</b> of the ' + baht(P.target) + ' target' : 'against a ' + baht(P.target) + ' target')
        : ' (no revenue target set)';
      const proj = mc ? 'That is the final total for ' + P.month : 'At the current pace' + (P.proj_method === 'dow' ? ' (day-of-week weighted)' : '') + ' the month should close near <b>' + baht(P.projected) + '</b>';
      const lead2 = mc ? 'The month' : 'Month-to-date (' + P.month + ')';
      ex.push({c:'#4f46e5', lead:'Month pace.', body:lead2 + ' stands at <b>' + baht(P.mtd_revenue) + '</b> over ' + (P.mtd_days || 0) +
              ' day' + ((P.mtd_days || 0) === 1 ? '' : 's') + '. ' + proj + tgt + '.'});
    }
  }

  // 5 — Food cost
  if (P.food_cost_pct != null) {
    const fc = P.food_cost_pct;
    const fcCol = fc >= 53 ? '#dc2626' : fc <= 47 ? '#16a34a' : '#d97706';
    if (isTH) {
      const v  = fc >= 53 ? 'สูงกว่าเกณฑ์ ~50% — อัตรากำไรกำลังถูกกดดัน'
               : fc <= 47 ? 'อยู่ในเกณฑ์ ~50% อย่างสบายๆ — อัตรากำไรแข็งแรง'
               : 'อยู่ราวเกณฑ์ ~50%';
      ex.push({c:'#d97706', lead:'ต้นทุนอาหาร', body:'ต้นทุนอาหารอยู่ที่ <b>' + fc.toFixed(1) + '%</b> ของยอดขาย (อิงสูตร BoM) สำหรับ ' + P.month +
              ' <span style="color:' + fcCol + ';font-weight:600">' + v + '</span>'});
    } else {
      const v  = fc >= 53 ? 'above the ~50% benchmark — margins are under pressure'
               : fc <= 47 ? 'comfortably within the ~50% benchmark — healthy margins'
               : 'around the ~50% benchmark';
      ex.push({c:'#d97706', lead:'Food cost.', body:'Food cost is running at <b>' + fc.toFixed(1) + '%</b> of sales (BoM basis) for ' + P.month +
              ', <span style="color:' + fcCol + ';font-weight:600">' + v + '</span>.'});
    }
  }

  // 6 — Discount-risk radar
  const h = L.heavy_discount || {}, chk = L.cash_heavy || {}, rp = L.reprinted || {}, zg = L.zero_gross || {};
  if (isTH) {
    const cashTxt = chk.unavailable ? '' : ' โดย <b>' + (chk.bills || 0) + '</b> บิลจ่ายด้วยเงินสดเป็นหลัก (' + baht(chk.baht || 0) + ') — สัญญาณเสี่ยงเงินขาดที่พบบ่อย';
    ex.push({c:'#dc2626', lead:'เรดาร์ความเสี่ยงส่วนลด', body:(h.bills || 0) + ' บิลให้ส่วนลด ≥30% (' + baht(h.baht || 0) + ')' + cashTxt +
            ' · ' + (rp.bills || 0) + ' บิลส่วนลดถูกพิมพ์ซ้ำมากกว่า 1 ครั้ง และ ' + (zg.bills || 0) + ' บิลยอดศูนย์ (แจกฟรี / ส่วนลดล้วน)'});
  } else {
    const cashTxt = chk.unavailable ? '' : ', of which <b>' + (chk.bills || 0) + '</b> were paid mostly in cash (' + baht(chk.baht || 0) + ') — the classic skim flag';
    ex.push({c:'#dc2626', lead:'Discount-risk radar.', body:(h.bills || 0) + ' bill' + (h.bills === 1 ? '' : 's') + ' were discounted ≥30% (' + baht(h.baht || 0) + ')' + cashTxt +
            '. ' + (rp.bills || 0) + ' discounted bill' + (rp.bills === 1 ? '' : 's') + ' were reprinted more than once and ' + (zg.bills || 0) + ' were zero-gross (free / discount-only).'});
  }

  // 7 — Discount discipline
  if (isTH) {
    const pol = H.discount_policy_pct != null ? H.discount_policy_pct : 5;
    const dd = H.discount_pct != null ? ' ราว <b>' + H.discount_pct.toFixed(1) + '%</b> ของยอดขายก่อนลด — ' + (H.discount_pct > pol ? 'เกินเพดานนโยบาย ' + pol + '% อยู่ <b>' + baht(H.discount_excess || 0) + '</b> ควรทบทวนการอนุมัติส่วนลด' : 'อยู่ในเพดานนโยบาย ' + pol + '%') : '';
    ex.push({c:'#db2777', lead:'วินัยส่วนลด', body:'ส่วนลดรวมทั้งสิ้น <b>' + baht(H.discount) + '</b>' + dd});
  } else {
    const pol = H.discount_policy_pct != null ? H.discount_policy_pct : 5;
    const dd = H.discount_pct != null ? ', about <b>' + H.discount_pct.toFixed(1) + '%</b> of pre-discount sales — ' + (H.discount_pct > pol ? '<b>' + baht(H.discount_excess || 0) + '</b> above the ' + pol + '% policy ceiling; worth reviewing discount approvals' : 'within the ' + pol + '% policy ceiling') : '';
    ex.push({c:'#db2777', lead:'Discount discipline.', body:'Total discounts were <b>' + baht(H.discount) + '</b>' + dd + '.'});
  }

  // 8 — Menu cost per head
  const mtop = (M.top || [])[0], mris = (M.risers || [])[0], msav = (M.savers || [])[0];
  if (isTH) {
    const mPrev = M.total_cph_prev != null ? ' (เดิม ฿' + M.total_cph_prev.toFixed(2) + ')' : '';
    const mTopT = mtop ? ' <b>' + esc(mtop.menu) +'</b> มีต้นทุนต่อหัวสูงสุด (฿' + (mtop.cost_per_head || 0).toFixed(2) + '/หัว)' : '';
    const mRisT = mris ? ' <b>' + esc(mris.menu) +'</b> เพิ่มขึ้นมากสุด (+฿' + Math.abs(mris.delta).toFixed(2) + '/หัว) — จุดเฝ้าระวังอัตรากำไร' : '';
    const mSavT = msav ? ' <b>' + esc(msav.menu) +'</b> ลดลงมากสุด' : '';
    ex.push({c:'#ca8a04', lead:'ต้นทุนเมนูต่อหัว', body:'ต้นทุนอาหารต่อหัวเฉลี่ย <b>฿' + (M.total_cph != null ? M.total_cph.toFixed(2) : '—') + '</b>' + mPrev + mTopT + mRisT + mSavT});
  } else {
    const mPrev = M.total_cph_prev != null ? ' (was ฿' + M.total_cph_prev.toFixed(2) + ')' : '';
    const mTopT = mtop ? ' <b>' + esc(mtop.menu) +'</b> carries the most cost per guest (฿' + (mtop.cost_per_head || 0).toFixed(2) + '/head).' : '';
    const mRisT = mris ? ' <b>' + esc(mris.menu) +'</b> climbed the most (+฿' + Math.abs(mris.delta).toFixed(2) + '/head) — a margin watch-point.' : '';
    const mSavT = msav ? ' <b>' + esc(msav.menu) +'</b> eased the most.' : '';
    ex.push({c:'#ca8a04', lead:'Menu cost per head.', body:'Food cost per guest averaged <b>฿' + (M.total_cph != null ? M.total_cph.toFixed(2) : '—') + '</b>' + mPrev + '.' + mTopT + mRisT + mSavT});
  }

  // 9 — Trend
  const trv = (j.trend || []).map(t => t.revenue || 0);
  if (trv.length >= 3) {
    const avg = trv.reduce((a, b) => a + b, 0) / trv.length, last = trv[trv.length - 1];
    if (isTH) {
      const td  = last > avg * 1.05 ? 'กำลังเพิ่มขึ้น' : last < avg * 0.95 ? 'กำลังอ่อนตัว' : 'ทรงตัว';
      ex.push({c:'#0284c7', lead:'แนวโน้ม', body:'ในช่วง ' + trv.length + ' ' + (isDaily ? 'วันล่าสุด' : isMonthly ? 'เดือนล่าสุด' : 'สัปดาห์ล่าสุด') + ' ยอดขาย <b>' + td +
              '</b> — ' + pNoun + 'ล่าสุดอยู่' + (last >= avg ? 'เหนือ' : 'ต่ำกว่า') + 'ค่าเฉลี่ยของช่วง'});
    } else {
      const td  = last > avg * 1.05 ? 'rising' : last < avg * 0.95 ? 'softening' : 'holding steady';
      ex.push({c:'#0284c7', lead:'Trend.', body:'Across the last ' + trv.length + ' ' + (isDaily ? 'days' : isMonthly ? 'months' : 'weeks') + ', revenue is <b>' + td +
              '</b> — the latest ' + pNoun + ' sits ' + (last >= avg ? 'above' : 'below') + ' the period average.'});
    }
  }

  // 10 — Bottom line
  const acts3 = (j.actions || []).filter(a => a.sev !== 'low').slice(0, 3).map(a => a.text);
  if (isTH) {
    const tone = H.rev_wow_pct == null ? 'ฐานเปรียบเทียบจะชัดขึ้นเมื่อมีข้อมูลสะสมมากขึ้น'
               : H.rev_wow_pct >= 2 ? 'โมเมนตัมเป็นบวก'
               : H.rev_wow_pct <= -5 ? 'ยอดขายต้องได้รับการดูแล'
               : 'ผลประกอบการทรงตัว';
    const actsT = acts3.length ? ' สิ่งที่ต้องทำก่อน: ' + acts3.join(' ') : ' ไม่มีประเด็นเร่งด่วนใน' + pNoun + 'นี้';
    ex.push({c:'#0f172a', lead:'สรุป', body:tone + actsT});
  } else {
    const tone = H.rev_wow_pct == null ? 'A baseline will firm up as more periods accumulate'
               : H.rev_wow_pct >= 2 ? 'Momentum is positive'
               : H.rev_wow_pct <= -5 ? 'Sales need attention'
               : 'Performance is steady';
    const actsT = acts3.length ? ' Priority actions: ' + acts3.join(' ') : ' No critical issues are flagged this ' + pNoun + '.';
    ex.push({c:'#0f172a', lead:'Bottom line.', body:tone + '.' + actsT});
  }

  // TL;DR — a 5-second scannable verdict + 3-4 bullets ABOVE the full prose, so
  // readers get the takeaway without reading ~10 paragraphs. Tone colour keys off WoW.
  const _tone = (H.rev_wow_pct == null) ? '#64748b' : (H.rev_wow_pct >= 2 ? '#059669' : H.rev_wow_pct <= -5 ? '#dc2626' : '#d97706');
  const _fc = P.food_cost_pct;
  const _bul = [];
  if (H.cust_wow_pct != null || H.rph_wow_pct != null) {
    const byT = Math.abs(H.cust_wow_pct || 0) >= Math.abs(H.rph_wow_pct || 0);
    _bul.push(isTH
      ? `การเปลี่ยนแปลงยอดขายมาจาก <b>${byT ? 'จำนวนลูกค้า' : 'ยอดใช้จ่าย/หัว'}</b> เป็นหลัก (หัว ${signed(H.cust_wow_pct)} · ต่อหัว ${signed(H.rph_wow_pct)})`
      : `Sales shift driven mainly by <b>${byT ? 'customer traffic' : 'spend per head'}</b> (covers ${signed(H.cust_wow_pct)} · per-head ${signed(H.rph_wow_pct)})`);
  }
  if (_fc != null) {
    const fCol = _fc >= 53 ? '#dc2626' : _fc <= 47 ? '#059669' : '#d97706';
    _bul.push(isTH
      ? `ต้นทุนอาหาร <b style="color:${fCol}">${_fc.toFixed(1)}%</b> ${_fc >= 53 ? 'สูงกว่าเป้า ~50%' : _fc <= 47 ? 'ต่ำกว่าเป้า ~50% (ดี)' : 'ใกล้เป้า ~50%'}`
      : `Food cost <b style="color:${fCol}">${_fc.toFixed(1)}%</b> ${_fc >= 53 ? 'above the ~50% target' : _fc <= 47 ? 'below ~50% (good)' : 'near the ~50% target'}`);
  }
  const _hv = L.heavy_discount || {};
  if ((_hv.bills || 0) > 0) {
    const _hb = (_hv.by_branch && _hv.by_branch[0]) ? ' — ' + esc(_hv.by_branch[0].branch) : '';
    _bul.push(isTH
      ? `จุดรั่วเด่น: <b>${num(_hv.bills)}</b> บิลลดหนัก ≥30% (${bigBaht(_hv.baht)})${_hb}`
      : `Top leak: <b>${num(_hv.bills)}</b> bills discounted ≥30% (${bigBaht(_hv.baht)})${_hb}`);
  }
  if (brs.length >= 2) {
    const _sorted = brs.slice().sort((a, b) => (b.revenue || 0) - (a.revenue || 0));
    _bul.push(isTH
      ? `สาขาเด่น <b>${esc(_sorted[0].branch)}</b> · ตามหลัง <b>${esc(_sorted[_sorted.length - 1].branch)}</b>`
      : `Top branch <b>${esc(_sorted[0].branch)}</b> · lagging <b>${esc(_sorted[_sorted.length - 1].branch)}</b>`);
  }
  const _head = `${isTH ? 'ยอดขาย' : 'Sales'} <b>${bigBaht(H.revenue)}</b> · ${dLbl} ${wow(H.rev_wow_pct)} · GP <b>${G.gp_pct != null ? G.gp_pct.toFixed(0) + '%' : '—'}</b>${_fc != null ? ` · Food <b>${_fc.toFixed(1)}%</b>` : ''}`;
  const tldr = `<div class="rounded-xl p-3 mb-3" style="background:#f8fafc;border:1px solid var(--border);border-left:3px solid ${_tone}">
      <p class="text-xs font-bold uppercase tracking-wide mb-1" style="color:${_tone}">${T.tldr}</p>
      <p class="text-sm text-slate-700 mb-2">${_head}</p>
      <ul class="text-[13px] text-slate-600 space-y-1">${_bul.map(b => `<li class="flex gap-2"><span style="color:${_tone}">•</span><span>${b}</span></li>`).join('')}</ul>
    </div>`;
  const execInner = tldr
    + '<div class="text-sm leading-relaxed text-slate-700">' + ex.map(para).join('') + '</div>';

  // §6 — per-MASTER-branch discount + % of sales (the company-owned follow-up line).
  // Independent of the master/franchise split; shows whenever master branches are in scope.
  const masterBranchDisc = (() => {
    const MB = L.by_master_branch || [];
    if (!MB.length) return '';
    const pctCol = p => p == null ? '#94a3b8' : p >= 8 ? '#dc2626' : p >= 4 ? '#d97706' : '#475569';
    const pctTxt = p => p == null ? '—' : p.toFixed(1) + '%';
    const tot = MB.reduce((a, b) => ({ disc: a.disc + (b.disc||0), bills: a.bills + (b.bills||0), sales: a.sales + (b.sales||0) }), { disc:0, bills:0, sales:0 });
    const totPct = tot.sales ? tot.disc / tot.sales * 100 : null;
    const rows = MB.map(b => `<tr class="border-b border-slate-50">
        <td class="py-1.5 pr-3">${esc(b.branch)}</td>
        <td class="py-1.5 pr-3 text-right tabular-nums font-semibold">${baht(b.disc)}</td>
        <td class="py-1.5 pr-3 text-right tabular-nums font-bold" style="color:${pctCol(b.pct)}">${pctTxt(b.pct)}</td>
        <td class="py-1.5 pr-3 text-right tabular-nums text-slate-400">${num(b.sales)}</td>
        <td class="py-1.5 text-right tabular-nums text-slate-400">${num(b.bills)}</td></tr>`).join('');
    return `<div class="mt-4 pt-3 border-t border-slate-100">
      <p class="text-[10px] font-bold uppercase tracking-wide text-slate-400 mb-2">${T.discByMaster} <span class="normal-case text-slate-300">${T.companyFollow}</span></p>
      <table class="w-full text-xs"><thead><tr class="text-left text-slate-400 border-b border-slate-200">
        <th class="py-1.5 pr-3">${T.branch}</th><th class="py-1.5 pr-3 text-right">${T.discount}</th>
        <th class="py-1.5 pr-3 text-right">${T.ofSales}</th><th class="py-1.5 pr-3 text-right">${T.salesExVat}</th>
        <th class="py-1.5 text-right">${T.discBills}</th></tr></thead>
      <tbody>${rows}</tbody>
      <tfoot><tr class="border-t-2 border-slate-200 font-bold">
        <td class="py-1.5 pr-3">${T.allMaster}</td>
        <td class="py-1.5 pr-3 text-right tabular-nums">${baht(tot.disc)}</td>
        <td class="py-1.5 pr-3 text-right tabular-nums" style="color:${pctCol(totPct)}">${pctTxt(totPct)}</td>
        <td class="py-1.5 pr-3 text-right tabular-nums text-slate-500">${num(tot.sales)}</td>
        <td class="py-1.5 text-right tabular-nums text-slate-500">${num(tot.bills)}</td></tr></tfoot></table></div>`;
  })();

  // §6 split — Money-Leak
  const leakWithSplit = leak + masterBranchDisc + (() => {
    if (!hasSplit) return '';
    const lkRow = (lx, key) => {
      const o = (lx.leak || {})[key] || {};
      return o.unavailable ? '—' : `${num(o.bills||0)} ${T.bills} · ${baht(o.baht||0)}`;
    };
    return splitPanel(
      [[T.discTotalShort, lkRow(jM,'total')],
       [T.heavyDisc30, lkRow(jM,'heavy_discount')], [T.cashHeavyShort, lkRow(jM,'cash_heavy')],
       [T.reprintShort, lkRow(jM,'reprinted')], [T.zeroShort, lkRow(jM,'zero_gross')]],
      [[T.discTotalShort, lkRow(jF,'total')],
       [T.heavyDisc30, lkRow(jF,'heavy_discount')], [T.cashHeavyShort, lkRow(jF,'cash_heavy')],
       [T.reprintShort, lkRow(jF,'reprinted')], [T.zeroShort, lkRow(jF,'zero_gross')]]);
  })();

  // §8 split — Peak Hours
  const peakWithSplit = peakHtml + (() => {
    if (!hasSplit) return '';
    const pkRow = jx => {
      const PX = jx.peak || {};
      const ph = PX.peak_hour ? `${String(PX.peak_hour.hour).padStart(2,'0')}:00 (${baht(PX.peak_hour.revenue)})` : '—';
      const bd = PX.best_day ? dowLabel(PX.best_day) : '—';
      const dp = (PX.dayparts || []).map(d => `${dpLabel(d.name)} ${d.pct != null ? d.pct.toFixed(0)+'%' : ''}`).join(' · ') || '—';
      return [[T.peakHour, ph], [T.bestDay, bd], [T.dayparts, dp]];
    };
    return splitPanel(pkRow(jM), pkRow(jF));
  })();

  // §9 split — Table Turnover
  const tableWithSplit = tableHtml + (() => {
    if (!hasSplit || !tableHtml) return '';
    const tbRow = jx => {
      const TX = jx.table || {};
      if (!TX.available) return [[T.dataLbl, T.na]];
      return [
        [T.coversTableShort, TX.covers_per_table != null ? TX.covers_per_table.toFixed(2) : '—'],
        [T.avgTurn, TX.avg_turn_min != null ? Math.round(TX.avg_turn_min) + ' ' + T.min : '—'],
        [T.tables, num(TX.tables)], [T.covers, num(TX.covers)],
      ];
    };
    return splitPanel(tbRow(jM), tbRow(jF));
  })();

  // §10 — Payment channels (tender mix) PER BRANCH. Master group in the all-branches
  // split view, else the current scope. Matrix: rows = branches, columns = channels
  // (colour-keyed), each cell = that channel's % of the branch total (dominant bold);
  // last column = branch total; footer = the whole-scope mix.
  const _payColors = ['#0891b2','#6366f1','#f59e0b','#10b981','#ef4444','#8b5cf6','#ec4899','#0d9488','#64748b'];
  const _payPD = (hasSplit && jM && jM.payment && jM.payment.available) ? jM.payment
               : (j.payment && j.payment.available ? j.payment : null);
  const _payScope = (hasSplit && jM && jM.payment && jM.payment.available) ? T.masterBranches
               : (getBranch() || (typeof getGroup === 'function' && getGroup() ? T.groupPfx + getGroup() : T.allBranchesCap));
  const payHtml = (() => {
    if (!_payPD || !_payPD.channels.length) return '';
    const cols = _payPD.channels;                          // column order = scope total desc
    const byB = _payPD.by_branch || [];
    const colColor = {}; cols.forEach((c, i) => colColor[c.code] = _payColors[i % _payColors.length]);
    const th = `<th class="text-left py-1.5 pr-3">${T.payBranch}</th>`
      + cols.map(c => `<th class="text-right py-1.5 px-2 whitespace-nowrap" style="color:${colColor[c.code]}" title="${esc(c.code)}">${esc(c.code)}</th>`).join('')
      + `<th class="text-right py-1.5 pl-2">${T.paySum}</th>`;
    const rows = byB.map(br => {
      const dom = Math.max(0, ...cols.map(c => (br.channels[c.code] || {}).pct || 0));
      const cells = cols.map(c => {
        const x = br.channels[c.code];
        if (!x || !x.pct) return `<td class="text-right py-1.5 px-2 text-slate-300">—</td>`;
        const isDom = x.pct >= dom;
        return `<td class="text-right py-1.5 px-2 tabular-nums ${isDom ? 'font-bold' : ''}" style="color:${colColor[c.code]}" title="${esc(c.code)}: ${baht(x.amount)}">${x.pct.toFixed(1)}%</td>`;
      }).join('');
      return `<tr class="border-t border-slate-100">
        <td class="py-1.5 pr-3 font-medium text-slate-700">${esc(br.branch)}</td>${cells}
        <td class="py-1.5 pl-2 text-right tabular-nums font-semibold text-slate-800">${baht(br.total)}</td></tr>`;
    }).join('');
    // last-period tender mix → per-channel + grand-total WoW in the footer
    const _payPrev = _payPD.prev || {};
    const _prevCh  = _payPrev.channels || {};
    const foot = `<tr class="border-t-2 border-slate-200">
      <td class="py-1.5 pr-3 font-bold text-slate-500">${T.payAll}</td>
      ${cols.map(c => { const pv = (_prevCh[c.code] || {}).amount;
        return `<td class="py-1.5 px-2 text-right tabular-nums font-bold" style="color:${colColor[c.code]}" title="${baht(c.amount)}">${c.pct.toFixed(1)}%${pv != null ? `<div class="text-[8.5px] font-normal leading-none mt-0.5">${wowVal(c.amount, pv)}</div>` : ''}</td>`; }).join('')}
      <td class="py-1.5 pl-2 text-right tabular-nums font-bold text-slate-800">${baht(_payPD.total)}${_payPrev.total != null ? `<div class="text-[8.5px] font-normal leading-none mt-0.5">${wowVal(_payPD.total, _payPrev.total)}</div>` : ''}</td></tr>`;
    return `<p class="text-[11px] text-slate-500 mb-3">${byB.length} ${T.branchesWord} · ${cols.length} ${T.channels} · ${T.total} <b>${baht(_payPD.total)}</b>${_payPrev.total != null ? ' ' + wowVal(_payPD.total, _payPrev.total) + ' <span class="text-slate-400">' + compShort + '</span>' : ''} · ${isDaily ? T.thisDay : isMonthly ? T.thisMonth : T.thisWeek} · ${T.payCaption2}${compShort})</p>
      <div class="overflow-x-auto"><table class="w-full text-xs"><thead><tr class="text-slate-500">${th}</tr></thead>
      <tbody>${rows}</tbody><tfoot>${foot}</tfoot></table></div>`;
  })();

  // §11 — Buffet price tiers (guests, share & cost/head per adult buffet price).
  // Hidden when the tier fact tables don't cover this window (j.tier == null).
  const tierHtml = (() => {
    const TD = j.tier;
    if (!TD || !(TD.rows || []).length) return '';
    const dPrev = (cur, prev) => {
      if (cur == null || prev == null) return '';
      const d = cur - prev;
      return `<div class="text-[8.5px] font-normal leading-none mt-0.5" style="color:${d > 0 ? '#dc2626' : d < 0 ? '#059669' : '#64748b'}">${d >= 0 ? '+' : ''}${d.toFixed(2)}</div>`;
    };
    const rows = TD.rows.map(r => `<tr class="border-t border-slate-100">
      <td class="py-1.5 pr-3 font-semibold text-slate-700">${esc(r.label)}</td>
      <td class="py-1.5 px-2 text-right tabular-nums">${num(r.pax)}${r.pax_prev != null ? `<div class="text-[8.5px] font-normal leading-none mt-0.5">${wowVal(r.pax, r.pax_prev)}</div>` : ''}</td>
      <td class="py-1.5 px-2 text-right tabular-nums">${r.share != null ? r.share.toFixed(1) + '%' : '—'}${r.share_prev != null ? `<div class="text-[8.5px] text-slate-400 leading-none mt-0.5">${T.was} ${r.share_prev.toFixed(1)}%</div>` : ''}</td>
      <td class="py-1.5 px-2 text-right tabular-nums">${r.rev_head != null ? baht(r.rev_head) : '—'}</td>
      <td class="py-1.5 px-2 text-right tabular-nums">${r.cogs_head != null ? baht(r.cogs_head) : '—'}${dPrev(r.cogs_head, r.cogs_head_prev)}</td>
      <td class="py-1.5 px-2 text-right tabular-nums font-semibold">${r.gp_head != null ? baht(r.gp_head) : '—'}</td>
      <td class="py-1.5 pl-2 text-right tabular-nums">${r.food_pct != null ? r.food_pct.toFixed(1) + '%' : '—'}</td></tr>`).join('');
    const mix = (TD.mix_effect != null && Math.abs(TD.mix_effect) >= 0.5)
      ? `<p class="text-[11px] mt-2 font-medium" style="color:${TD.mix_effect < 0 ? '#dc2626' : '#059669'}">${TD.mix_effect < 0 ? T.tierMixDown : T.tierMixUp}${TD.mix_effect >= 0 ? '+' : ''}${TD.mix_effect.toFixed(2)} ${isTH ? 'บาท/หัว' : 'THB/head'} <span class="text-slate-400 font-normal">${compShort}</span></p>` : '';
    // cost-driver sub-block: rendered post-innerHTML into #mgtTierDrivers by the
    // initTierDrivers IIFE below (interactive — tier picker, bars, ranks, heat,
    // auto-insight). Container only appears when j.tier.drivers.by_tier exists.
    const driversSlot = ((TD.drivers || {}).by_tier) ? `<div id="mgtTierDrivers" class="mt-4"></div>` : '';
    // "โปรฯ" is the one row whose label explains nothing, and it is not a small
    // row. Spell out what is inside it in prose: real discounts (named against
    // the tier they came off), bills carrying a comped or half-price head, and
    // fully comped bills — the last two with the branches behind them.
    const promoNote = (() => {
      const PZ = TD.promo;
      if (!PZ || !PZ.kinds) return '';
      const K = PZ.kinds, bits = [];
      // one colour per kind, so the eye can sort them without reading:
      //   free  = red     money given away outright
      //   mixed = amber   the sneaky one — not a price, an artefact of the mix
      //   disc  = teal    an ordinary, intended discount
      const C = { free: '#dc2626', mixed: '#b45309', disc: '#0f766e' };
      const chip = (k, txt) => `<span style="display:inline-block;padding:1px 7px;border-radius:999px;`
        + `background:${C[k]}18;color:${C[k]};font-weight:700;font-size:11px;white-space:nowrap">${txt}</span>`;
      const paxOf = k => (K[k] && K[k].pax) || 0;
      const billsOf = k => (K[k] && K[k].bills) || 0;
      const totPax = paxOf('disc') + paxOf('mixed') + paxOf('free');
      if (!totPax) return '';
      const brList = kind => ((PZ.branches || {})[kind] || [])
        .map(b => `${esc(b.branch)} (${num(b.pax)})`).join(' · ');
      if (paxOf('disc')) {
        const top = (PZ.discounts || []).filter(d => d.off_pct != null && d.off_pct > 0).slice(0, 3)
          .map(d => isTH
            ? `${baht(d.unit)} = ลด ${d.off_pct.toFixed(0)}% จาก ${baht(d.of_tier)} (${num(d.pax)} คน)`
            : `${baht(d.unit)} = ${d.off_pct.toFixed(0)}% off ${baht(d.of_tier)} (${num(d.pax)} covers)`);
        bits.push([ 'disc', isTH
          ? `${chip('disc', 'ส่วนลดทั้งบิล')} <b>${num(billsOf('disc'))}</b> บิล / <b>${num(paxOf('disc'))}</b> คน${top.length ? ' — ' + top.join(' · ') : ''}`
          : `${chip('disc', 'Whole-bill discount')} <b>${num(billsOf('disc'))}</b> bills / <b>${num(paxOf('disc'))}</b> covers${top.length ? ' — ' + top.join(' · ') : ''}` ]);
      }
      if (paxOf('mixed')) {
        bits.push([ 'mixed', isTH
          ? `${chip('mixed', 'หัวฟรี/ครึ่งราคาปน')} <b>${num(billsOf('mixed'))}</b> บิล / <b>${num(paxOf('mixed'))}</b> คน — ราคาเฉลี่ยต่อหัวตกอยู่ระหว่าง tier <b>ไม่ใช่ราคาขายจริง</b>${brList('mixed') ? '<br><span class="text-slate-500">สาขา: ' + brList('mixed') + '</span>' : ''}`
          : `${chip('mixed', 'Comped/half-price head mixed in')} <b>${num(billsOf('mixed'))}</b> bills / <b>${num(paxOf('mixed'))}</b> covers — the per-head average lands between tiers and is <b>not a real price</b>${brList('mixed') ? '<br><span class="text-slate-500">branches: ' + brList('mixed') + '</span>' : ''}` ]);
      }
      if (paxOf('free')) {
        bits.push([ 'free', isTH
          ? `${chip('free', 'ฟรีทั้งใบ')} <b>${num(billsOf('free'))}</b> บิล / <b>${num(paxOf('free'))}</b> คน${brList('free') ? '<br><span class="text-slate-500">สาขา: ' + brList('free') + '</span>' : ''}`
          : `${chip('free', 'Fully comped')} <b>${num(billsOf('free'))}</b> bills / <b>${num(paxOf('free'))}</b> covers${brList('free') ? '<br><span class="text-slate-500">branches: ' + brList('free') + '</span>' : ''}` ]);
      }
      if (!bits.length) return '';
      const shareTxt = TD.pax ? ` <span style="color:#92400e">(${(totPax / TD.pax * 100).toFixed(1)}% ${isTH ? 'ของคนทานทั้งหมด' : 'of all covers'})</span>` : '';
      return `<div class="mt-4 rounded-xl mgt-atom" style="background:#fffbeb;border:1px solid #fde68a;border-left:4px solid #d97706;padding:11px 14px">
          <p class="text-[12px] font-extrabold mb-2" style="color:#92400e">${T.promoTitle}${shareTxt}</p>
          <ul class="space-y-1.5" style="font-size:12.5px;color:#1f2937">${bits.map(([k, b]) =>
            `<li class="flex gap-2"><span style="color:${C[k]};font-weight:700">•</span><span>${b}</span></li>`).join('')}</ul>
          <p class="text-[10.5px] mt-2" style="color:#a16207">${T.promoNote}</p></div>`;
    })();
    return `<p class="text-[11px] text-slate-500 mb-3">${num(TD.pax)} ${T.tierBuffetGuests}${TD.pax_prev != null ? ' ' + wowVal(TD.pax, TD.pax_prev) + ' <span class="text-slate-400">' + compShort + '</span>' : ''}</p>
      <div class="overflow-x-auto"><table class="w-full text-xs"><thead><tr class="text-slate-500">
        <th class="text-left py-1.5 pr-3">${T.tierCol}</th><th class="text-right py-1.5 px-2">${T.tierGuests}</th>
        <th class="text-right py-1.5 px-2">${T.tierShare}</th><th class="text-right py-1.5 px-2">${T.tierRevHead}</th>
        <th class="text-right py-1.5 px-2">${T.tierCostHead}</th><th class="text-right py-1.5 px-2">${T.tierGpHead}</th>
        <th class="text-right py-1.5 pl-2">${T.tierFood}</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${mix}${promoNote}<p class="text-[10px] text-slate-400 mt-2">${T.tierNote}</p>${driversSlot}`;
  })();

  const body = document.getElementById('mgtRptBody');
  if (!body) return;
  const footMonth = (j.pace && j.pace.month) || (isTH ? 'เดือนนั้น' : 'the period month');
  const footnote = isTH
    ? `<p class="text-[11px] text-slate-400 px-1">ยอดขายสุทธิเป็นยอดไม่รวม VAT (ยอดรวม ÷ 1.07) · ต้นทุนต่อหัว = ต้นทุนอาหารของเมนู ÷ จำนวนหัว (อิงสูตร BoM), ${isDaily ? 'วันนี้เทียบสัปดาห์ก่อน' : isMonthly ? 'เดือนนี้เทียบเดือนก่อน' : 'สัปดาห์นี้เทียบสัปดาห์ก่อน'} · ต้นทุนอาหาร % เป็นตัวเลขอิง BoM ของ ${footMonth} · ข้อมูลล่าสุดถึงวันที่ ${j.latest_day || '—'}</p>`
    : `<p class="text-[11px] text-slate-400 px-1">Net sales are ex-VAT (total ÷ 1.07). Cost / head = dish food COGS ÷ covers (BoM basis), this ${isDaily ? 'day' : isMonthly ? 'month' : 'week'} vs ${compShort.replace('vs ', '')}. Food cost % is the BoM figure for ${footMonth}. Latest data day: ${j.latest_day || '—'}.</p>`;
  // Section verdict lines — one scannable takeaway (colour = good/watch/bad) at the
  // top of §3/§4/§8/§9/§10, matching §6/§11. Empty string when the data is absent.
  const vLine = (col, txt) => `<p class="text-[13px] mb-3 pb-2 border-b border-slate-100 flex items-start gap-2"><span style="color:${col};line-height:1.5">●</span><span class="text-slate-600">${txt}</span></p>`;
  const _sg = v => v == null ? '' : (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(1) + '%';
  const _hh = h => String(h).padStart(2, '0') + ':00';
  const v3 = (() => {
    const parts = [];
    let col = '#4f46e5';
    if (P.proj_vs_target_pct != null) {
      const p = P.proj_vs_target_pct;
      col = p >= 100 ? '#059669' : p >= 90 ? '#d97706' : '#dc2626';
      parts.push(isTH ? `คาดทั้งเดือน <b style="color:${col}">${p.toFixed(0)}%</b> ของเป้า (${p >= 100 ? 'ถึงเป้า' : p >= 90 ? 'ใกล้เป้า' : 'ต่ำกว่าเป้า'})`
                      : `Projected <b style="color:${col}">${p.toFixed(0)}%</b> of target (${p >= 100 ? 'on track' : p >= 90 ? 'near' : 'behind'})`);
    }
    if (P.mom_pct != null) parts.push(`MoM ${_sg(P.mom_pct)}`);
    if (P.food_cost_pct != null) parts.push(isTH ? `ต้นทุนอาหาร ${P.food_cost_pct.toFixed(1)}%` : `food cost ${P.food_cost_pct.toFixed(1)}%`);
    return parts.length ? vLine(col, parts.join(' · ')) : '';
  })();
  const v4 = (() => {
    const tr = (j.trend || []).map(t => t.revenue || 0);
    if (tr.length < 2) return '';
    const latest = tr[tr.length - 1], avg = tr.reduce((a, b) => a + b, 0) / tr.length;
    const gap = avg ? (latest - avg) / avg * 100 : 0;
    const col = gap >= 3 ? '#059669' : gap <= -3 ? '#dc2626' : '#d97706';
    const dir = isTH ? (gap >= 3 ? 'สูงกว่า' : gap <= -3 ? 'ต่ำกว่า' : 'ใกล้เคียง') : (gap >= 3 ? 'above' : gap <= -3 ? 'below' : 'near');
    const u = isDaily ? (isTH ? 'วัน' : 'day') : isMonthly ? (isTH ? 'เดือน' : 'month') : (isTH ? 'สัปดาห์' : 'week');
    return vLine(col, isTH
      ? `ล่าสุด <b>${bigBaht(latest)}</b> — <b style="color:${col}">${dir}</b>ค่าเฉลี่ย ${tr.length} ${u} ${_sg(gap)}`
      : `Latest <b>${bigBaht(latest)}</b> — <b style="color:${col}">${dir}</b> the ${tr.length}-${u} average by ${_sg(gap)}`);
  })();
  const v8 = (() => {
    if (!PK.peak_hour) return '';
    const dp = (PK.dayparts || []).slice().sort((a, b) => (b.pct || 0) - (a.pct || 0))[0];
    const parts = [isTH ? `พีคช่วง <b>${_hh(PK.peak_hour.hour)}</b>` : `Peaks <b>${_hh(PK.peak_hour.hour)}</b>`];
    if (PK.best_day) parts.push(isTH ? `วันขายดี <b>${dowLabel(PK.best_day)}</b>` : `busiest <b>${dowLabel(PK.best_day)}</b>`);
    if (dp && dp.pct != null) parts.push(`${dpLabel(dp.name)} ${dp.pct.toFixed(0)}%`);
    return vLine('#0284c7', parts.join(' · '));
  })();
  const v9 = (() => {
    if (!TB.available || TB.covers_per_table == null) return '';
    let col = '#9333ea';
    const parts = [isTH ? `หมุนโต๊ะ <b>${TB.covers_per_table.toFixed(2)}</b> หัว/โต๊ะ` : `<b>${TB.covers_per_table.toFixed(2)}</b> covers/table`];
    if (TB.avg_turn_min != null) {
      parts.push(isTH ? `เฉลี่ย <b>${Math.round(TB.avg_turn_min)}</b> นาที/รอบ` : `avg <b>${Math.round(TB.avg_turn_min)}</b> min/turn`);
      const tp = (TB.prev || {}).avg_turn_min;
      if (tp != null && TB.avg_turn_min !== tp) {
        const faster = TB.avg_turn_min < tp; col = faster ? '#059669' : '#dc2626';
        parts.push(isTH ? (faster ? 'เร็วขึ้น' : 'ช้าลง') : (faster ? 'faster' : 'slower'));
      }
    }
    return vLine(col, parts.join(' · '));
  })();
  const v10 = (() => {
    if (!_payPD || !(_payPD.channels || []).length) return '';
    const d = _payPD.channels[0];
    return vLine('#0d9488', isTH
      ? `ช่องทางหลัก <b>${esc(d.code)}</b> ${d.pct != null ? d.pct.toFixed(1) + '%' : ''} จาก ${_payPD.channels.length} ช่องทาง`
      : `Top channel <b>${esc(d.code)}</b> ${d.pct != null ? d.pct.toFixed(1) + '%' : ''} of ${_payPD.channels.length}`);
  })();
  // A section with no data used to VANISH, so the reader could not tell an empty
  // section from a missing one. Print the placeholder instead - a management pack
  // has to account for every numbered section its contents page promises.
  const naBox = m => `<p class="text-sm text-slate-400 py-3">${m}</p>`;

  // 12 - Appendix: definitions & method. Without it the reader has to guess what
  // "GP", "food cost %" or "cash-heavy" mean, and every number becomes arguable.
  const appendix = `<div class="grid grid-cols-1 md:grid-cols-2 gap-x-8">${
      (T.defs || []).map(([k, v]) => `<div class="py-1.5 border-b border-slate-100 mgt-atom">
         <span class="text-[12px] font-semibold text-slate-700">${k}</span>
         <span class="text-[12px] text-slate-500"> &mdash; ${v}</span></div>`).join('')
    }</div><p class="text-[11px] text-slate-400 mt-3">${T.defsNote}</p>`;

  // 12 - Customer voice: the window's voices against the window before, the
  // themes and the branches behind them, and the fix loop with its verdicts —
  // the one section where the guests, not the till, grade the period.
  const voiceHtml = (() => {
    const V = j.feedback;
    if (!V || !(V.voices || (V.actions && (V.actions.open || V.actions.doing || V.actions.done)))) return '';
    const delta = (cur, prev, digits, unit, invert) => {
      if (cur == null || prev == null) return '';
      const d = cur - prev; if (Math.abs(d) < (digits ? 0.05 : 0.5)) return `<span class="text-slate-400 text-[10px]">=</span>`;
      const good = invert ? d < 0 : d > 0;
      return `<span class="text-[10px] font-semibold" style="color:${good ? '#059669' : '#dc2626'}">${d > 0 ? '▲' : '▼'}${Math.abs(d).toFixed(digits)}${unit || ''}</span>`;
    };
    const R = V.response || {}, A = V.actions || {}, S = V.survey || {};
    const tile = (l, v, d, sub) => `<div class="rounded-lg p-3" style="background:var(--bg-card);border:1px solid var(--border)"><p class="text-[10px] uppercase tracking-wide text-slate-400">${l}</p><p class="text-lg font-bold text-slate-800 leading-tight">${v} ${d}</p>${sub ? `<p class="text-[10px] text-slate-500 mt-0.5">${sub}</p>` : ''}</div>`;
    const tiles = `<div class="grid grid-cols-2 md:grid-cols-5 gap-2 mb-3">` +
      tile(T.vVoices, V.voices, delta(V.voices, V.prev_voices, 0), `${V.positives || 0} 😊 · ${V.negatives || 0} 😟`) +
      tile(T.vNeg, V.neg_pct != null ? V.neg_pct + '%' : '—', delta(V.neg_pct, V.prev_neg_pct, 1, ' pt', true), '') +
      tile(T.vStars, V.avg_rating != null ? V.avg_rating.toFixed(2) : '—', delta(V.avg_rating, V.prev_avg_rating, 2), '') +
      tile(T.vResp, R.answerable ? R.rate + '%' : '—', delta(R.rate, V.prev_response_rate, 0, ' pt'), R.answerable ? `${R.answered}/${R.answerable}${R.median_days != null ? ' · ' + R.median_days + 'd' : ''}` : '') +
      tile(T.vWait, V.unreplied || 0, '', V.sla_breached ? `<span style="color:#dc2626;font-weight:600">${V.sla_breached} ${T.vSla}</span>` : '') + `</div>`;
    const themes = (V.themes || []).length ? `<table class="w-full text-[12px]"><thead><tr class="text-slate-400 text-[10px] uppercase"><th class="text-left py-1">${T.vThemes}</th><th class="text-right">${T.vVoices}</th><th class="text-right">${T.vNeg}</th><th class="text-right">Δ</th></tr></thead><tbody>` +
      V.themes.map(t => `<tr class="border-t border-slate-100"><td class="py-1 font-medium text-slate-700">${esc(t.theme)}</td><td class="text-right tabular-nums">${t.n}</td><td class="text-right tabular-nums" style="color:${t.neg ? '#dc2626' : 'inherit'}">${t.neg}</td><td class="text-right">${delta(t.n, t.prev_n, 0, '', true)}</td></tr>`).join('') + '</tbody></table>' : '';
    const watch = (V.branches_watch || []).length ? `<p class="text-[10px] uppercase tracking-wide text-slate-400 mt-3 mb-1">${T.vWatch}</p>` +
      V.branches_watch.map(b => `<div class="flex justify-between text-[12px] py-0.5 border-t border-slate-100"><span class="font-medium text-slate-700">${esc(b.branch)}</span><span class="tabular-nums" style="color:#dc2626">${b.neg_pct}% (${b.neg}/${b.n})${b.top_theme ? ` <span class="text-slate-400">· ${esc(b.top_theme)}</span>` : ''}</span></div>`).join('') : '';
    const praised = (V.branches_praised || []).length ? `<p class="text-[10px] uppercase tracking-wide text-slate-400 mt-3 mb-1">${T.vPraised}</p>` +
      V.branches_praised.map(b => `<div class="flex justify-between text-[12px] py-0.5 border-t border-slate-100"><span class="font-medium text-slate-700">${esc(b.branch)}</span><span class="tabular-nums" style="color:#059669">★${b.avg_rating.toFixed(2)} <span class="text-slate-400">(n=${b.n})</span></span></div>`).join('') : '';
    const respB = (V.response_by_branch || []).length ? `<p class="text-[10px] uppercase tracking-wide text-slate-400 mt-3 mb-1">${T.vRespBranch}</p>` +
      V.response_by_branch.map(x => `<div class="flex justify-between text-[12px] py-0.5 border-t border-slate-100"><span class="font-medium text-slate-700">${esc(x.branch)}</span><span class="tabular-nums" style="color:${(x.rate || 0) >= 80 ? '#059669' : (x.rate || 0) >= 50 ? '#b45309' : '#dc2626'}">${x.rate != null ? x.rate + '%' : '—'} <span class="text-slate-400">(${x.answered}/${x.answerable}${x.median_days != null ? ` · ${T.vMedian} ${x.median_days}d` : ''})</span></span></div>`).join('') : '';
    const survey = S.n ? `<p class="text-[11px] text-slate-500 mt-3">${T.vSurvey}: <b>${S.n}</b> ${T.vAnswers} ${delta(S.n, S.prev_n, 0)}${S.avg_rating != null ? ` · ★${S.avg_rating.toFixed(2)}` : ''}</p>` : '';
    const resTag = r => ({ better: ['#059669', T.vBetter], same: ['#64748b', T.vSame], worse: ['#dc2626', T.vWorse], no_data: ['#94a3b8', '—'] }[r] || ['#b45309', T.vPending]);
    const loop = `<p class="text-[10px] uppercase tracking-wide text-slate-400 mb-1">${T.vActions}</p>` +
      `<p class="text-[12px] text-slate-700">${T.vOpen} <b>${(A.open || 0) + (A.doing || 0)}</b>${A.overdue ? ` <span style="color:#dc2626">(${A.overdue} ${T.vOverdue})</span>` : ''} · ${T.vDone} <b>${A.done || 0}</b>` +
      ((A.better || A.same || A.worse) ? ` · <span style="color:#059669">${T.vBetter} ${A.better || 0}</span> / ${T.vSame} ${A.same || 0} / <span style="color:#dc2626">${T.vWorse} ${A.worse || 0}</span>` : '') + `</p>` +
      ((A.items || []).length ? `<table class="w-full text-[12px] mt-1"><tbody>` + A.items.map(it => { const [c, l] = resTag(it.result); return `<tr class="border-t border-slate-100"><td class="py-1 text-slate-700">${esc(it.title)}${it.branch ? ` <span class="text-slate-400">· ${esc(it.branch)}</span>` : ''}${it.theme ? ` <span class="text-slate-400">· ${esc(it.theme)}</span>` : ''}</td><td class="py-1 text-right tabular-nums whitespace-nowrap">${it.before_rate != null && it.after_rate != null ? `${it.before_rate} → ${it.after_rate} <span class="text-slate-400 text-[10px]">${T.vRate}</span>` : (it.days_left ? `<span class="text-slate-400 text-[10px]">${T.vPending} (${it.days_left})</span>` : '')}</td><td class="py-1 pl-2 text-right"><span class="text-[10px] font-semibold" style="color:${c}">${l}</span></td></tr>`; }).join('') + '</tbody></table>' : `<p class="text-[11px] text-slate-400 mt-1">${T.vNoActions}</p>`);
    return tiles + `<div class="grid grid-cols-1 md:grid-cols-2 gap-x-8"><div>${themes}${survey}</div><div>${watch}${praised}${respB}</div></div><div class="mt-4">${loop}</div>`;
  })();

  // -- The report plan ------------------------------------------------------
  // ONE declarative list drives three things that used to drift apart: the
  // on-screen order, the table of contents, and the printed page plan.
  //   land -> landscape sheet (wide tables, chart rows) - brk -> forced break
  const SECTIONS = [
    { id:'mgt-s0',  c:'#0f172a', brk:true, t:T.execSummary, h:execInner },
    { id:'mgt-s1',  c:'#0f766e', t:T.s1 + (isDaily ? T.s1d : isMonthly ? T.s1m : T.s1w), h:kpis },
    { id:'mgt-s2',  c:'#d97706', t:T.s2, h:actsWithSplit },
    { id:'mgt-s3',  c:'#4f46e5', t:T.s3pre + (mc ? T.s3mc : T.s3mtd) + T.s3post, h:v3 + pace },
    { id:'mgt-s4',  c:'#0891b2', land:true, t:T.s4pre + (isDaily ? T.s4d : isMonthly ? T.s4m : T.s4w) + T.s4post, h:v4 + trend },
    // §5 starts a fresh sheet ONCE THE TABLE IS LONG. With 28 branches it needs a
    // page of its own anyway, and letting it begin at the foot of §4's page
    // stranded four rows there and carried the other twenty-four over — a table
    // split 4/24 for no reason. Short tables (a single-branch report) still flow
    // inline rather than burning a sheet on six rows.
    { id:'mgt-s5',  c:'#7c3aed', land:true, brk: _brs.length >= 12, t:T.s5, h:branchTbl },
    { id:'mgt-s6',  c:'#dc2626', t:T.s6, h:leakWithSplit },
    { id:'mgt-s7',  c:'#059669', land:true, t:T.s7, h:menu },
    { id:'mgt-s8',  c:'#0284c7', land:true, t:T.s8, h:v8 + peakWithSplit },
    { id:'mgt-s9',  c:'#9333ea', t:T.s9,  h:tableHtml ? (v9 + tableWithSplit) : naBox(T.naTable) },
    { id:'mgt-s10', c:'#0d9488', land:!!payHtml,  t:T.s10 + _payScope, h:payHtml ? (v10 + payHtml) : naBox(T.naPay) },
    { id:'mgt-s11', c:'#b45309', land:!!tierHtml, t:T.s11, h:tierHtml || naBox(T.naTier) },
    { id:'mgt-s12', c:'#0f766e', t:T.s12v, h:voiceHtml || naBox(T.naVoice) },
    { id:'mgt-s13', c:'#475569', t:T.s12, h:appendix },
  ];

  // Cover page: contents + the report-information block (period, comparison basis,
  // scope, data cut-off, revenue/cost basis). Page 1 of the printed pack, and a
  // jump-nav on screen.
  const _rangeTxt = (j.range && j.range.length === 2) ? `${j.range[0]} &rarr; ${j.range[1]}` : (j.focus || '&mdash;');
  const _ri = [
    [T.riType,  (isDaily ? T.daily : isMonthly ? T.monthly : T.weekly) + ' &middot; ' + (j.label || '')],
    [T.riRange, _rangeTxt + (j.partial ? ` <span style="color:#d97706">&middot; ${T.riPartial}</span>` : '')],
    [T.riComp,  (j.comp_label || '&mdash;')],
    [T.riScope, scopeLabel(j.branch_scope)],
    [T.riDays,  (j.days_in_period != null ? j.days_in_period : '&mdash;') + ''],
    [T.riData,  j.latest_day || '&mdash;'],
    [T.riRev,   T.riRevV],
    [T.riCost,  T.riCostV + (P.month ? ' &middot; ' + P.month : '')],
  ];
  const toc = `<section class="card p-6 mgt-sec pg-port" style="border-top:3px solid #0f172a">
      <h2 class="text-base font-bold mb-3 pb-2" style="color:#0f172a;border-bottom:2px solid #0f172a33">${T.tocTitle}</h2>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-0 mb-4">
        ${SECTIONS.map(sx => `<a href="#${sx.id}" onclick="_mgtGo('${sx.id}');return false"
             class="flex items-baseline gap-2 text-[13px] py-1.5 border-b border-slate-100 mgt-atom" style="text-decoration:none">
            <span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${sx.c};flex:none"></span>
            <span class="text-slate-600">${sx.t}</span></a>`).join('')}
      </div>
      <p class="text-[10px] font-bold uppercase tracking-wide text-slate-400 mb-1">${T.repInfo}</p>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-x-8">
        ${_ri.map(([k, v]) => `<div class="flex justify-between gap-3 text-[12px] py-1 border-b border-slate-100 mgt-atom">
            <span class="text-slate-400" style="flex:none">${k}</span>
            <span class="text-slate-700 font-medium text-right">${v}</span></div>`).join('')}
      </div></section>`;

  body.innerHTML = toc
    + SECTIONS.map(sx => sec(sx.t, sx.h, sx.c, sx)).join('')
    + `<div class="pg-port">${footnote}</div>`;

  // §11 cost-driver sub-block — interactive: tier picker (compare tiers), rank
  // numbers, proportion bars, heat-tinted cost, % of tier cost, auto-insight.
  // Rendered here (after innerHTML so #mgtTierDrivers exists); the tier buttons
  // call window._mgtRenderDrivers to re-render in place without a refetch.
  (function initTierDrivers() {
    const DR = (j.tier || {}).drivers;
    const slot = document.getElementById('mgtTierDrivers');
    if (!DR || !DR.by_tier || !slot) return;
    const clean = s => esc(String(s).replace(/^\(B\)\s*/, '').replace(/\s*\(Buffet\)\s*$/i, ''));
    const vsCell = v => v == null ? '—'
      : `<span style="color:${v > 0 ? '#dc2626' : v < 0 ? '#059669' : '#64748b'}">${v >= 0 ? '+' : '−'}${baht(Math.abs(v))}</span>`;
    const bar = (v, max) => `<span style="display:inline-block;height:6px;border-radius:3px;background:#f59e0b;width:${Math.max(3, Math.round((v / max) * 52))}px;vertical-align:middle;margin-right:5px"></span>`;
    const heat = (v, max) => `background:rgba(220,38,38,${(0.06 + 0.5 * (v / max)).toFixed(2)})`;
    const shareCell = p => p == null ? '' : `<span class="text-slate-500">${p.toFixed(0)}%</span>`;
    function render(tier) {
      const cur = DR.by_tier[tier] || DR.by_tier[DR.default_tier];
      if (!cur) { slot.innerHTML = ''; return; }
      const sel = cur.focus_tier, order = DR.tier_order || [sel], showVs = !!cur.ref_label;
      const btns = order.map(t => {
        const on = t === sel;
        return `<button type="button" onclick="window._mgtRenderDrivers('${t}')" class="px-2 py-0.5 rounded text-[11px] font-semibold" style="border:1px solid ${on ? '#b45309' : 'var(--border,#e2e8f0)'};${on ? 'background:#b45309;color:#fff' : 'background:transparent;color:#64748b'}">฿${t}</button>`;
      }).join(' ');
      const gmax = Math.max(1, ...(cur.groups || []).map(g => g.cogs_head));
      const mmax = Math.max(1, ...(cur.menus || []).map(m => m.cogs_head));
      const grp = (cur.groups || []).map((g, i) => `<tr class="border-t border-slate-100">
        <td class="py-1 pr-1 text-right text-slate-400 tabular-nums" style="width:1.3em">${i + 1}</td>
        <td class="py-1 pr-3 text-slate-700">${clean(g.label)}</td>
        <td class="py-1 px-2 text-right tabular-nums font-semibold rounded" style="${heat(g.cogs_head, gmax)}">${baht(g.cogs_head)}</td>
        <td class="py-1 px-2 tabular-nums whitespace-nowrap">${bar(g.cogs_head, gmax)}${shareCell(g.share_pct)}</td>
        ${showVs ? `<td class="py-1 pl-2 text-right tabular-nums">${vsCell(g.vs_ref)}</td>` : ''}</tr>`).join('');
      const mnu = (cur.menus || []).map((m, i) => `<tr class="border-t border-slate-100">
        <td class="py-1 pr-1 text-right text-slate-400 tabular-nums" style="width:1.3em">${i + 1}</td>
        <td class="py-1 pr-3 text-slate-700">${clean(m.label)}</td>
        <td class="py-1 px-2 text-right tabular-nums font-semibold rounded" style="${heat(m.cogs_head, mmax)}">${baht(m.cogs_head)}</td>
        <td class="py-1 px-2 tabular-nums whitespace-nowrap">${bar(m.cogs_head, mmax)}${shareCell(m.share_pct)}</td>
        <td class="py-1 pl-2 text-right tabular-nums text-slate-500">${num(m.qty_100)}</td></tr>`).join('');
      const topg = (cur.groups || [])[0];
      const insight = topg ? `<p class="text-[11.5px] mt-1 mb-2" style="color:#b45309">💡 ${isTH
        ? `${clean(topg.label)} คิดเป็น <b>${topg.share_pct != null ? topg.share_pct.toFixed(0) + '%' : '—'}</b> ของต้นทุนอาหาร tier ${esc(cur.focus_label)} — จุดคุมต้นทุนสำคัญที่สุด`
        : `${clean(topg.label)} is <b>${topg.share_pct != null ? topg.share_pct.toFixed(0) + '%' : '—'}</b> of this tier's food cost (${esc(cur.focus_label)}) — the single biggest lever`}</p>` : '';
      slot.innerHTML = `<div class="pt-3 border-t border-slate-200">
        <div class="flex flex-wrap items-center gap-2 mb-2">
          <span class="text-[11px] font-semibold text-slate-600">${T.tierDrvTitle}</span>
          <span class="text-[10px] text-slate-400">${T.tierDrvPick}:</span>${btns}
          <span class="text-[11px] ml-1"><span class="font-semibold" style="color:#b45309">${esc(cur.focus_label)}</span>${cur.focus_food_pct != null ? ` <span class="text-slate-400">Food ${cur.focus_food_pct.toFixed(1)}%</span>` : ''}${
            (cur.focus_share != null && cur.focus_share < 5)
              ? ` <span style="color:#dc2626;font-weight:600">${isTH
                  ? `· ${num(cur.focus_pax)} คน (${cur.focus_share.toFixed(1)}%) — กลุ่มเล็ก ตัวเลขแกว่ง`
                  : `· ${num(cur.focus_pax)} covers (${cur.focus_share.toFixed(1)}%) — small sample, figures swing`}</span>`
              : ''}</span>
        </div>
        ${insight}
        <div class="grid md:grid-cols-2 gap-x-6 gap-y-3">
          <div class="overflow-x-auto"><table class="w-full text-xs"><thead><tr class="text-slate-500">
            <th></th><th class="text-left py-1 pr-3">${T.tierDrvGroup}</th><th class="text-right py-1 px-2">${T.tierDrvCostHead}</th>
            <th class="text-left py-1 px-2">${T.tierDrvShare}</th>${showVs ? `<th class="text-right py-1 pl-2">${T.tierDrvVs} ${esc(cur.ref_label)}</th>` : ''}</tr></thead><tbody>${grp}</tbody></table></div>
          <div class="overflow-x-auto"><table class="w-full text-xs"><thead><tr class="text-slate-500">
            <th></th><th class="text-left py-1 pr-3">${T.tierDrvMenu}</th><th class="text-right py-1 px-2">${T.tierDrvCostHead}</th>
            <th class="text-left py-1 px-2">${T.tierDrvShare}</th><th class="text-right py-1 pl-2">${T.tierDrvQty}</th></tr></thead><tbody>${mnu}</tbody></table></div>
        </div>
        <p class="text-[10px] text-slate-400 mt-2">${T.tierDrvNote}</p></div>`;
    }
    window._mgtRenderDrivers = render;
    render(DR.default_tier);

    // PRINT: the block above is a picker — it shows ONE tier, so paper was
    // getting whichever tier happened to be selected and losing the others
    // with no hint that they existed. Build every tier into a second,
    // screen-hidden container that only print reveals (see #mgtTierDriversPrint).
    const pslot = document.createElement('div');
    pslot.id = 'mgtTierDriversPrint';
    pslot.style.display = 'none';
    const order = DR.tier_order || Object.keys(DR.by_tier);
    const keep = slot.innerHTML;                       // restore the picker after harvesting
    pslot.innerHTML = order.map(t => {
      render(t);
      return '<div class="mgt-atom">' + slot.innerHTML + '</div>';
    }).join('');
    slot.innerHTML = keep;
    slot.parentNode.insertBefore(pslot, slot.nextSibling);
  })();

  // Trend chart(s). Split → 3 separate bar charts (Total/Master/Franchise), each with an
  // amber moving-average trend curve. Single → one bar chart + the same MA trend curve.
  const trData  = j.trend  || [];
  const trDataM = hasSplit ? (jM.trend || []) : [];
  const trDataF = hasSplit ? (jF.trend || []) : [];
  const lastIdx = trData.length - 1;
  const fmtM = v => v >= 1e6 ? '฿' + (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? '฿' + (v / 1e3).toFixed(0) + 'K' : '฿' + Math.round(v || 0);
  // Colours C_HIST/C_CURR/C_LINE/C_TOT_*/C_MST_*/C_FRN_* defined above (single source)

  // Shared chart options (scales/tooltip) — currC highlights the latest bar + its x label.
  const trendOpts = (li, currC, padTop = 18) => ({
    responsive: true, maintainAspectRatio: false,
    layout: { padding: { top: padTop } },
    plugins: {
      legend: { display: false },   // HTML legend above handles screen + print
      tooltip: {
        backgroundColor: '#0f172a', padding: 10, cornerRadius: 8, displayColors: false,
        filter: item => item.dataset.type !== 'line',
        callbacks: { label: c => '฿' + Math.round(c.parsed.y).toLocaleString('en-US') },
      },
      datalabels: { display: false },
    },
    scales: {
      y: {
        beginAtZero: true, grace: '18%',
        border: { display: false },
        grid: { color: 'rgba(148,163,184,0.13)', drawTicks: false },
        maxTicksLimit: 5,
        ticks: { color: '#94a3b8', font: { size: 10 }, padding: 8,
                 callback: v => v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(0) + 'K' : v },
      },
      x: {
        border: { display: false }, grid: { display: false },
        ticks: {
          color: ctx => ctx.index === li ? currC : '#64748b',
          font: ctx => ({ size: 10, weight: ctx.index === li ? '700' : '500' }),
          maxRotation: 0, autoSkip: true,
        },
      },
    },
  });

  if (hasSplit) {
    // One chart per series — solid bars (darker = latest) + amber moving-average curve.
    const mkTrend = (id, data, histC, currC) => {
      const vals = data.map(t => t.revenue || 0);
      const li = vals.length - 1;
      const lt = document.getElementById(id + 'Latest');
      if (lt) lt.textContent = fmtM(vals[li] || 0);
      mkChart(id, {
        type: 'bar',
        data: { labels: data.map(t => t.label), datasets: [
          { type: 'bar', label: 'Net Sales', data: vals,
            backgroundColor: vals.map((_, i) => i === li ? currC : histC),
            borderRadius: 5, categoryPercentage: 0.8, barPercentage: 0.74, order: 2,
            datalabels: { display: false } },
          { type: 'line', label: 'Trend', data: movingAvg(vals, 3),
            borderColor: C_LINE, backgroundColor: 'transparent',
            borderWidth: 2, tension: 0.35, fill: false, order: 1,
            pointRadius: 0, datalabels: { display: false } },
        ] },
        options: trendOpts(li, currC),
      });
    };
    mkTrend('mgtTrendTotal',     trData,  C_TOT_H, C_TOT_C);
    mkTrend('mgtTrendMaster',    trDataM, C_MST_H, C_MST_C);
    mkTrend('mgtTrendFranchise', trDataF, C_FRN_H, C_FRN_C);
  } else {
    const vals = trData.map(t => t.revenue || 0);
    mkChart('mgtTrendChart', {
      type: 'bar',
      data: { labels: trData.map(t => t.label), datasets: [
        { type: 'bar', label: 'Net Sales', data: vals,
          backgroundColor: vals.map((_, i) => i === lastIdx ? C_CURR : C_HIST),
          borderRadius: 6, categoryPercentage: 0.72, barPercentage: 0.85, order: 2,
          datalabels: {
            display: true, anchor: 'end', align: 'end', offset: 0, clamp: true,
            color: ctx => ctx.dataIndex === lastIdx ? C_CURR : '#64748b',
            font: ctx => ({ size: ctx.dataIndex === lastIdx ? 12 : 10,
                            weight: ctx.dataIndex === lastIdx ? '800' : '600' }),
            formatter: v => v ? fmtM(v) : '',
          } },
        { type: 'line', label: 'Trend', data: movingAvg(vals, 3),
          borderColor: C_LINE, backgroundColor: 'transparent',
          borderWidth: 2, tension: 0.35, fill: false, order: 1,
          pointRadius: 3, pointBackgroundColor: C_LINE,
          pointBorderColor: '#fff', pointBorderWidth: 1.5,
          datalabels: { display: false } },
      ] },
      options: trendOpts(lastIdx, C_CURR, 28),
    });
  }

  // Summary strip below chart
  const summaryEl = document.getElementById('mgtTrendSummary');
  if (summaryEl && trData.length) {
    const vals   = trData.map(t => t.revenue || 0);
    const avg8   = vals.reduce((a, b) => a + b, 0) / vals.length;
    const maxV   = Math.max(...vals);
    const posV   = vals.filter(v => v > 0);
    const minV   = posV.length ? Math.min(...posV) : 0;
    const maxT   = trData[vals.indexOf(maxV)];
    const minIdx = vals.findIndex(v => v === minV && v > 0);
    const minT   = minIdx >= 0 ? trData[minIdx] : null;
    const cur    = vals[lastIdx] || 0;
    const prv    = lastIdx > 0 ? (vals[lastIdx - 1] || 0) : 0;
    const wowP   = prv ? ((cur - prv) / prv * 100) : null;
    const vsAvg  = avg8 ? ((cur - avg8) / avg8 * 100) : null;
    const ac     = d => d == null ? '#64748b' : d >= 0 ? '#16a34a' : '#dc2626';
    const af     = d => d == null ? '—' : (d >= 0 ? '▲ ' : '▼ ') + Math.abs(d).toFixed(1) + '%';
    const pill   = (lbl, val, sub = '', sc = '#64748b') =>
      `<div style="flex:1;min-width:90px;padding:8px 10px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px">
         <div style="font-size:9px;color:#94a3b8;text-transform:uppercase;letter-spacing:.06em;font-weight:600;margin-bottom:3px">${lbl}</div>
         <div style="font-size:13px;font-weight:800;color:#1e293b;white-space:nowrap">${val}</div>
         ${sub ? `<div style="font-size:10px;font-weight:600;color:${sc};margin-top:2px">${sub}</div>` : ''}
       </div>`;
    const splitRow = hasSplit && trDataM.length
      ? pill(T.master, fmtM(trDataM[lastIdx]?.revenue || 0))
        + pill(T.franchise, fmtM(trDataF[lastIdx]?.revenue || 0))
      : '';
    // Period-aware labels — Day in daily mode, Week in weekly mode (so the two
    // reports stay consistent and the daily strip no longer mislabels days as weeks).
    const _pThis = isDaily ? T.thisDayP : isMonthly ? T.thisMonthP : T.thisWeekP;
    const _pAv = isDaily ? T.avg8d : isMonthly ? T.avg8m : T.avg8w;
    const _pDl = isDaily ? T.dod : isMonthly ? T.mom : T.wow;
    const _pBest = isDaily ? T.bestDayP : isMonthly ? T.bestMonthP : T.bestWeekP;
    const _pWeak = isDaily ? T.weakDayP : isMonthly ? T.weakMonthP : T.weakWeekP;
    summaryEl.innerHTML =
      `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;padding-top:10px;border-top:1px solid #f1f5f9">
         ${pill(_pThis, fmtM(cur), af(wowP) + _pDl, ac(wowP))}
         ${pill(_pAv, fmtM(avg8), af(vsAvg) + T.vsAvgP, ac(vsAvg))}
         ${pill(_pBest, fmtM(maxV), maxT ? maxT.label : '')}
         ${pill(_pWeak, fmtM(minV), minT ? minT.label : '')}
         ${splitRow}
       </div>`;

    // ── Auto-written trend analysis (below the chart) ──────────────────────
    const analysisEl = document.getElementById('mgtTrendAnalysis');
    if (analysisEl && trData.length >= 2) {
      const dirCol = vsAvg == null ? '#64748b' : vsAvg > 5 ? '#16a34a' : vsAvg < -5 ? '#dc2626' : '#d97706';
      // latest same-direction run at the tail of the series
      let streak = 0, sdir = 0;
      for (let i = vals.length - 1; i > 0; i--) {
        const d = vals[i] - vals[i - 1], s = d > 0 ? 1 : d < 0 ? -1 : 0;
        if (s === 0) break;
        if (sdir === 0) { sdir = s; streak = 1; } else if (s === sdir) streak++; else break;
      }
      const rises = vals.slice(1).filter((v, i) => v > vals[i]).length;
      const ma = movingAvg(vals, 3);
      const maNow = ma[ma.length - 1], maPrev = ma[Math.max(0, ma.length - 4)];
      const peakGap = maxV ? (cur - maxV) / maxV * 100 : null;
      const vsAvgTxt = vsAvg != null ? (vsAvg >= 0 ? '+' : '') + vsAvg.toFixed(1) + '%' : '—';
      let s1, s2, s3 = '';
      if (isTH) {
        const noun = isDaily ? 'วัน' : isMonthly ? 'เดือน' : 'สัปดาห์', nounP = isDaily ? 'วันล่าสุด' : isMonthly ? 'เดือนล่าสุด' : 'สัปดาห์ล่าสุด';
        const dir = vsAvg == null ? 'ทรงตัว' : vsAvg > 5 ? 'กำลังเพิ่มขึ้น' : vsAvg < -5 ? 'กำลังอ่อนตัว' : 'ทรงตัว';
        const maTrend = (maNow != null && maPrev != null) ? (maNow > maPrev * 1.02 ? 'ขาขึ้น' : maNow < maPrev * 0.98 ? 'ขาลง' : 'ทรงตัว') : 'ทรงตัว';
        s1 = `ในช่วง ${vals.length} ${nounP} ยอดขาย <b style="color:${dirCol}">${dir}</b> — ${noun}ล่าสุด (<b>${fmtM(cur)}</b>) อยู่ <b style="color:${ac(vsAvg)}">${vsAvgTxt}</b> เทียบค่าเฉลี่ย ${vals.length} ${noun}${(peakGap != null && peakGap < -1) ? ` และ <b>ต่ำกว่า ${Math.abs(peakGap).toFixed(0)}%</b> จากจุดสูงสุดของช่วง (${maxT ? maxT.label : ''})` : ' และอยู่ที่/ใกล้จุดสูงสุดของช่วง'}`;
        const streakTxt = streak >= 2 ? ` ล่าสุด <b style="color:${sdir > 0 ? '#16a34a' : '#dc2626'}">${sdir > 0 ? 'เพิ่มขึ้น' : 'ลดลง'}ติดต่อกัน ${streak} ${noun}</b>;` : '';
        const maClause = `แนวโน้มค่าเฉลี่ยเคลื่อนที่ 3 ${noun}เป็น <b>${maTrend}</b> (${rises} จาก ${vals.length - 1} ${noun}ที่เพิ่มขึ้นเทียบ${noun}ก่อน)`;
        s2 = ` ${streakTxt}${maClause}`;
        if (hasSplit && trDataM.length && trDataF.length) {
          const mv = trDataM.map(t => t.revenue || 0), fv = trDataF.map(t => t.revenue || 0);
          const mAvg = mv.reduce((a, b) => a + b, 0) / mv.length, fAvg = fv.reduce((a, b) => a + b, 0) / fv.length;
          const mVs = mAvg ? (mv[mv.length - 1] - mAvg) / mAvg * 100 : null;
          const fVs = fAvg ? (fv[fv.length - 1] - fAvg) / fAvg * 100 : null;
          const word = v => v == null ? 'เท่ากับ' : v > 3 ? 'เหนือ' : v < -3 ? 'ต่ำกว่า' : 'ใกล้';
          s3 = ` สาขาบริษัทอยู่ <b style="color:${(mVs || 0) >= 0 ? '#16a34a' : '#dc2626'}">${word(mVs)}ค่าเฉลี่ยตัวเอง</b> และแฟรนไชส์อยู่ <b style="color:${(fVs || 0) >= 0 ? '#16a34a' : '#dc2626'}">${word(fVs)}ค่าเฉลี่ยตัวเอง</b>`;
        }
      } else {
        const noun = isDaily ? 'day' : isMonthly ? 'month' : 'week', nounP = isDaily ? 'days' : isMonthly ? 'months' : 'weeks';
        const dir = vsAvg == null ? 'holding steady' : vsAvg > 5 ? 'rising' : vsAvg < -5 ? 'softening' : 'holding steady';
        const maTrend = (maNow != null && maPrev != null) ? (maNow > maPrev * 1.02 ? 'upward' : maNow < maPrev * 0.98 ? 'downward' : 'flat') : 'flat';
        s1 = `Over the last ${vals.length} ${nounP}, net sales are <b style="color:${dirCol}">${dir}</b> — the latest ${noun} (<b>${fmtM(cur)}</b>) sits <b style="color:${ac(vsAvg)}">${vsAvgTxt}</b> vs the ${vals.length}-${noun} average${(peakGap != null && peakGap < -1) ? ` and <b>${Math.abs(peakGap).toFixed(0)}% below</b> the period peak (${maxT ? maxT.label : ''})` : ' and is at/near the period peak'}.`;
        const streakTxt = streak >= 2 ? ` The latest run is <b style="color:${sdir > 0 ? '#16a34a' : '#dc2626'}">${streak} ${nounP} ${sdir > 0 ? 'up' : 'down'}</b> in a row;` : '';
        const maClause = `the 3-${noun} moving-average trend is <b>${maTrend}</b> (${rises} of ${vals.length - 1} ${nounP} rose ${noun}-over-${noun}).`;
        s2 = streakTxt ? `${streakTxt} ${maClause}` : ` ${maClause.charAt(0).toUpperCase()}${maClause.slice(1)}`;
        if (hasSplit && trDataM.length && trDataF.length) {
          const mv = trDataM.map(t => t.revenue || 0), fv = trDataF.map(t => t.revenue || 0);
          const mAvg = mv.reduce((a, b) => a + b, 0) / mv.length, fAvg = fv.reduce((a, b) => a + b, 0) / fv.length;
          const mVs = mAvg ? (mv[mv.length - 1] - mAvg) / mAvg * 100 : null;
          const fVs = fAvg ? (fv[fv.length - 1] - fAvg) / fAvg * 100 : null;
          const word = v => v == null ? 'flat' : v > 3 ? 'above' : v < -3 ? 'below' : 'near';
          s3 = ` Master is running <b style="color:${(mVs || 0) >= 0 ? '#16a34a' : '#dc2626'}">${word(mVs)} its average</b> and franchise <b style="color:${(fVs || 0) >= 0 ? '#16a34a' : '#dc2626'}">${word(fVs)} its average</b>.`;
        }
      }
      analysisEl.innerHTML = `<div style="margin-top:10px;padding:10px 12px;background:#f8fafc;border:1px solid #e8edf3;border-left:3px solid ${dirCol};border-radius:8px">
        <p style="font-size:9px;text-transform:uppercase;letter-spacing:.06em;color:#94a3b8;font-weight:700;margin-bottom:3px">${T.trendAnalysis}</p>
        <p style="font-size:11.5px;line-height:1.55;color:#334155">${s1}${s2}${s3}</p></div>`;
    }
  }

  // HTML bar chart for print (canvas doesn't render in PDF). Split → 3 separate labelled
  // mini-charts side by side (mirrors the 3 on-screen charts); single → one row. Solid
  // colours so print-color-adjust keeps them visible.
  const htmlChartEl = document.getElementById('mgtTrendHtml');
  if (htmlChartEl && trData.length) {
    // SVG chart for print — vector renders crisply & deterministically in the PDF
    // (Chart.js canvas does not print reliably). Mirrors the on-screen mini-cards:
    // y-axis gridlines + labels, bars (latest = darker) and the amber moving-average
    // trend curve. `wide` = the single full-width chart (specific branch/group); else
    // one of the three compact split charts (Total / Master / Franchise).
    const _printChart = (data, histC, currC, title, wide) => {
      const vals = data.map(t => t.revenue || 0);
      const n   = vals.length || 1;
      const mx  = Math.max(...vals, 1);
      const li  = vals.length - 1;
      const ma  = movingAvg(vals, 3);
      const W = wide ? 900 : 320, H = wide ? 230 : 178;
      const padL = wide ? 44 : 30, padR = 8, padT = wide ? 16 : 10, padB = 16;
      const plotW = W - padL - padR, plotH = H - padT - padB;
      const yMax = (mx * 1.12) || 1;
      const xc = i => padL + (i + 0.5) * (plotW / n);
      const yc = v => padT + plotH * (1 - v / yMax);
      const bw = (plotW / n) * 0.6;
      const fs = wide ? 9 : 5.5;
      let grid = '', ylab = '';
      const ticks = 4;
      for (let k = 0; k <= ticks; k++) {
        const vv = yMax * k / ticks, yy = yc(vv);
        grid += `<line x1="${padL}" y1="${yy.toFixed(1)}" x2="${(W - padR).toFixed(1)}" y2="${yy.toFixed(1)}" stroke="#e8edf3" stroke-width="0.6"/>`;
        ylab += `<text x="${(padL - 3).toFixed(1)}" y="${(yy + fs * 0.36).toFixed(1)}" text-anchor="end" font-size="${fs}" fill="#94a3b8">${fmtM(vv).replace('฿', '')}</text>`;
      }
      const bars = vals.map((v, i) => {
        const by = yc(v), bh = Math.max(0.6, padT + plotH - by);
        return `<rect x="${(xc(i) - bw / 2).toFixed(1)}" y="${by.toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="1.5" fill="${i === li ? currC : histC}"/>`;
      }).join('');
      // Per-bar value labels only in the roomy wide chart (matches on-screen datalabels);
      // the compact split charts show the latest value in the header instead.
      const vlab = wide ? vals.map((v, i) =>
        `<text x="${xc(i).toFixed(1)}" y="${(yc(v) - 3).toFixed(1)}" text-anchor="middle" font-size="8.5" font-weight="${i === li ? '800' : '600'}" fill="${i === li ? currC : '#64748b'}">${fmtM(v)}</text>`).join('') : '';
      const xlab = data.map((t, i) => `<text x="${xc(i).toFixed(1)}" y="${(H - 5).toFixed(1)}" text-anchor="middle" font-size="${fs}" font-weight="700" fill="${i === li ? currC : '#475569'}">${t.label || ''}</text>`).join('');
      const pts = ma.map((v, i) => v == null ? null : `${xc(i).toFixed(1)},${yc(v).toFixed(1)}`).filter(Boolean).join(' ');
      const maLine = pts ? `<polyline points="${pts}" fill="none" stroke="#f59e0b" stroke-width="${wide ? 2.2 : 1.6}" stroke-linejoin="round" stroke-linecap="round"/>` : '';
      const hdr = title
        ? `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:2px">
             <span style="font-size:8px;font-weight:800;color:#475569">${title}</span>
             <span style="font-size:8px;font-weight:800;color:${currC}">${fmtM(vals[li] || 0)}</span></div>`
        : '';
      return `<div style="flex:1;min-width:0;background:#fafbfc;border:1px solid #eef2f7;border-radius:8px;padding:6px 8px">
          ${hdr}
          <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" style="width:100%;height:auto;display:block">
            ${grid}${ylab}${bars}${maLine}${vlab}${xlab}
          </svg></div>`;
    };
    htmlChartEl.innerHTML = hasSplit
      ? _printChart(trData,  C_TOT_H, C_TOT_C, 'Total', false)
        + _printChart(trDataM, C_MST_H, C_MST_C, 'Master', false)
        + _printChart(trDataF, C_FRN_H, C_FRN_C, 'Franchise', false)
      : _printChart(trData, C_HIST, C_CURR, '', true);
  }
}

// loadActiveTab() now lives in 01-core.js and dispatches through TABS.
