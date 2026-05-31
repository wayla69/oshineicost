# Oshinei Cost Analysis Dashboard — App Structure

_Last updated: 2026-05-28 · Easy POS only · 26 active branches · period 2025-05 → 2026-05_

## 1. File tree

```
Cost Analysis/
├── index.html                          ← canonical SPA (served at GET /)
├── เปิด Oshinei Dashboard.bat          ← Windows launcher (starts uvicorn)
├── vendor/                             ← Chart.js / Tailwind / plugins (offline)
│
├── backend/
│   ├── main.py                         ← FastAPI app (~11K lines, ~80 endpoints)
│   ├── schema.sql                      ← SQLite DDL
│   ├── oshinei.db                      ← live DB (~252 MB)
│   ├── oshinei.db.backup_*             ← timestamped safety backups
│   ├── ingest_easy.py                  ← Easy POS xlsx parser
│   ├── ingest_cm.py                    ← (legacy) CM POS parser
│   ├── ingest_bom.py                   ← BoM Excel recipe parser
│   ├── reingest_easy_safe.py           ← Phase 17a safe re-ingest tool
│   ├── match_bom_skus.py               ← BoM↔SKU alias mapper
│   ├── audit_easy*.py                  ← Phase 17a audit scripts
│   ├── db_maintenance.py
│   ├── requirements.txt                ← fastapi, openpyxl, reportlab, matplotlib
│   └── uvicorn*.log
│
├── BoM/                                ← Excel recipe source files
│   ├── 1. หมวด สเต๊ก STEAK ... /     ← 17 category folders
│   ├── OSHINEI_SKU_BOM.csv
│   └── OSHINEI_BOM_Workbook.xlsx
│
├── Data for Oshinei/                   ← POS export source files
│   ├── ES1_*.xlsx                      ← Easy monthly SKU reports (13 files)
│   ├── 23 ES_*.xlsx                    ← Easy per-transaction reports (13 files)
│   ├── CM*/23 CM*/CM POS*/CM1*         ← legacy CM data (already archived)
│   └── ต้นทุนร้านโอชิเน.zip
│
└── docs/
    └── APP_STRUCTURE.md                ← this file
```

## 2. Sidebar navigation (6 sections, 20 tabs)

```
🏠 หน้าแรก                      → /api/home/brief

📊 ภาพรวม
├── Overview / KPIs            → /api/summary, /api/sales/category, /api/sales/skus
├── 📈 แนวโน้ม                  → /api/analysis/gross_profit, /api/analysis/daily
├── ⚖️  Compare A/B             → /api/analysis/compare
├── 👥 Guest & SPH              → /api/analysis/persona, /api/analysis/sph
└── 🏆 Benchmark สาขา           → /api/analysis/branches, /api/analysis/health

🍱 บุฟเฟ่ต์
└── 🥘 Buffet Performance       → /api/buffet/performance (+ 6 nested sub-tabs)
    │
    ├── 📊 ภาพรวม                  (Overview)
    ├── 👥 ลูกค้า & ที่นั่ง
    │   ├── 💎 Tier Mix             → /api/buffet/tier-mix
    │   └── 🪑 Cover & RevPASH      → /api/buffet/cover-economics, /api/buffet/revpash
    ├── 💰 กำไร & P&L              → /api/profit/buffet_per_bill, /api/buffet/pnl
    └── 🔬 BoM & จำลอง
        ├── 🔬 BoM Deep-dive       → /api/bom/composition, /api/bom/consumption-analysis
        └── 🎛️  Simulators          → /api/buffet/upgrade-simulator, /api/buffet/cost-sensitivity

🌐 ระดับเครือ & ต้นทุน          ← chain-wide profit + cost intelligence
├── 📊 GP รายสาขา                → /api/gp/by-branch-month  (granularity: day/week/month)
├── 🔬 BoM รายสาขา               → /api/branch-bom/analysis (branch=X|ALL, granularity)
└── 🎮 BoM Cost + Simulator     ← ⭐ flagship
    ├── 📊 ภาพรวม                 → /api/bom-cost/overview
    ├── 🏪 รายสาขา                → /api/branch-bom/analysis + /api/bom-cost/recipe/{sku}
    ├── ⚖️  เปรียบเทียบ            → /api/bom-cost/compare?branches=A,B,...
    └── 🎮 Simulator              → /api/bom-cost/simulator-base
                                   + /api/bom-cost/simulate (live what-if)
                                   + /api/bom-cost/scenarios (save/load/delete)

🍣 À La Carte & เมนู
├── ⭐ Menu Engineering          → /api/menu/engineering, /api/menu/lifecycle
├── 📋 Menu Analysis             → /api/sales/skus, /api/analysis/affinity
├── 💰 ต้นทุน                    → /api/cost/analysis, /api/cost/detail
└── 📈 กำไร                      → /api/profit/by-branch, /api/profit/by-sku

📦 การดำเนินงาน
├── Staff                       → /api/analysis/cashiers
├── Discounts                   → /api/analysis/discounts
├── Time & Flow                 → /api/analysis/hourly, /api/analysis/dow, /daypart_grid
└── การดำเนินงานสาขา             → /api/analysis/branch_peak, /api/analysis/health

⚙️  เครื่องมือ
├── Reports                     → /api/export/monthly-pack?format=xlsx|pdf
└── ตั้งค่า                      → 6 sub-tabs (see Settings tree below)
```

## 3. Settings sub-tabs

```
⚙️  Settings
├── 🎯 Cost Management          (THE primary cost editing UI)
│   ├── 🎯 Cost Recalibration tool (target food% → suggested costs)
│   ├── ประเภท column (buffet/alacarte/beverage/takeaway/fee/buffet_tier/other)
│   ├── Monthly cost upload + history view
│   ├── BoM ingredient audit
│   └── CSV template + bulk upload
├── 🎯 Branch targets           (rev/customer targets per month)
├── 💼 Branch fixed costs       (rent/labor/utilities)
├── 🏢 Branches                  (active/inactive, branch_type)
├── 🚫 Deactivate
├── 🕐 Operating hours
└── 🎨 Preferences
```

## 4. Database schema

```
branches (27)
  ├─ branch_id PK
  ├─ name UNIQUE
  ├─ branch_type DEFAULT 'master'
  └─ active                ← 26 active + 1 inactive (OSHINEI ชลบุรี, CM-only)

categories (66) → skus.category_id

skus (1,988)
  ├─ sku_id PK
  ├─ sku_code UNIQUE
  ├─ description
  ├─ category_id FK
  ├─ unit
  └─ business_type        ← Phase 17b: buffet_tier|buffet|alacarte|beverage|takeaway|fee|other

sku_cost (1,754)          ← lifetime default cost
  ├─ sku_id PK FK
  ├─ unit_cost
  ├─ effective_from
  └─ updated_at

sku_cost_monthly          ← Phase 8: monthly override
  ├─ (sku_id, period_month) PK
  ├─ unit_cost
  └─ notes

cost_change_log           ← Phase 17b: audit trail (any unit_cost write)
  ├─ id, changed_at, source, sku_id, period_month, old_cost, new_cost, delta, user_note

cost_scenario + cost_scenario_item   ← Phase 17b: save what-if scenarios

bom_ingredients (1,230)   ← parsed from BoM Excel
  ├─ (sku_id, seq) PK
  ├─ ingredient_code/name, qty_per_serving, unit, price_per_unit
  ├─ ingredient_cost (THB)
  └─ ingredient_type      ← protein|carb|veg|sauce|oil|seasoning|other

bom_sku_alias (94)        ← Phase 10: maps B0xxx codes → A0xxx SKUs
  ├─ bom_sku_code PK
  ├─ resolved_sku_id FK
  ├─ match_method (name_exact|name_fuzzy|code_normalised|manual)
  └─ confidence (0-1)

sales_sku_monthly (99,224)         ← Easy SKU report by month
  ├─ (branch_id, period_month, sku_id, source) PK
  ├─ qty, subtotal_ex_vat, tax_total, total_inc_vat
  └─ source = 'Easy'

sales_transaction (535,243)        ← Easy per-bill detail
  ├─ (branch_id, receipt_no, source) PK
  ├─ sold_at, cashier, table_no, customer_count
  ├─ food_subtotal, drink_subtotal, other_subtotal
  ├─ tip, sp_discount, discount, service_charge
  └─ total_inc_vat, source = 'Easy'

sales_month_summary (265)          ← Easy month-level summary

branch_targets, branch_fixed_costs, app_settings, closed_hours

──── ARCHIVED (Phase 5, read-only via /api/legacy/cm) ────
sales_sku_monthly_archive (57,323) ← CM POS history
sales_month_summary_archive (155)
sales_transaction_test_archive (39) ← Phase 16: staff test rows
```

## 5. Helper functions (canonical — DO NOT bypass)

```python
# In backend/main.py
_EASY_TIER_CODES = ('BUFFET','BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD')
# Tier SKUs whose unit_cost is intentionally ignored to avoid double-counting
# with item-level B0xxx consumption.

_cogs_case(ssm='ssm', sk='sk', sc='sc')         # SQL CASE for COGS expression
_costed_rev_case(ssm='ssm', sk='sk', sc='sc')   # SQL CASE for "revenue that has cost data"
_effective_cost_subquery(ssm='ssm', sc='sc')    # COALESCE: monthly exact → monthly prior → default → 0
_variant_filter(variant, sk='sk', ssm='ssm')    # 'A'|'B' filter by revenue behaviour
```

## 6. Hard rules

1. **VAT is NOT revenue** — always use `subtotal_ex_vat`. `total_inc_vat` only for POS reconciliation.
2. **Analytics must be time-series** — every endpoint returns a `monthly_trend`/`weekly_trend`/`daily_trend` array, not just cumulative totals.
3. **COGS via canonical helpers** — every COGS-bearing query must JOIN `skus sk` and use `_cogs_case()` (handles tier exclusion + monthly cost overrides).
4. **Index.html is served** — `backend/main.py` line 101 `INDEX = HERE.parent / "index.html"`. Do NOT split into multiple HTML files.

## 7. Phase history (this session)

| Phase | Delivered |
|---|---|
| 0 | GP formula fix (source-aware `_cogs_case`) |
| 1 | Premium tier % split |
| 2 | Benchmark 55-70% |
| 3 | GP รายสาขา tab |
| 4 | BoM ingredient deep-dive |
| 5 | CM POS archive → Easy-only |
| 7 | Calendar year picker |
| 8 | Monthly unit_cost (`sku_cost_monthly`) |
| 9 | (A)/(B) variant filter |
| 10 | BoM alias mapping (94 SKUs) |
| 11 | Cost Recalibration tool |
| 12 | BoM by branch tab |
| 13 | VAT + time-series + monthly cost audit |
| 14 | Daily/Weekly granularity + Excel/PDF export |
| 15 | Buffet Performance 4-group nested tabs |
| 16 | BoM chain-wide toggle |
| 17a | Easy re-ingest (+17K rows, food cost 186%→100%) |
| 17b | business_type + audit log + scenarios |
| 17c | Cost Mgmt tagging UI |
| 17d | BoM Cost Analysis 4-mode + Simulator |
| 17d-fix | Port UI + serve index.html (root cause of UI invisibility) |
| 17e | Sidebar 4-group reorganize |
| BUFFET fix | Add plain 'BUFFET' to tier exclusion (food cost 100%→10.8%) |
| 18a | Consolidate HTML (one file only) |
| 18b | App structure docs + in-app User Manual |
| 18c | Double-count / missing audit |
