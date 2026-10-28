"""
Role-based authentication for the Oshinei dashboard.

Self-contained, no third-party deps (stdlib hashlib/hmac only). Wired into
backend/main.py: tables are created + seeded at startup, a handful of /api/auth/*
endpoints handle login/logout/me + user admin, and an HTTP middleware enforces
per-role module access on /api/* requests.

Enforcement is env-gated, mirroring the existing OSHINEI_SEED_TOKEN philosophy:
  • OSHINEI_AUTH unset  → auth DISABLED. Login is optional, every /api/ is open.
                          Keeps local dev + the existing test-suite untouched.
  • OSHINEI_AUTH=1       → auth ENFORCED. /api/ needs a valid session cookie and
                          the user's role must grant the module a path maps to.

Sessions are stateless: a signed (HMAC-SHA256) cookie carries {uid, user, role,
exp}; nothing is stored server-side, so Fly restarts don't log everyone out as
long as OSHINEI_AUTH_SECRET is stable.
"""

from __future__ import annotations

import os
import hmac
import json
import time
import base64
import hashlib
import secrets
import sqlite3
from typing import Optional, Dict, List, Set

# ---------------------------------------------------------------------------
# Roles & modules  — the permission model
# ---------------------------------------------------------------------------
# A *module* is the smallest unit of access. Each role grants a set of modules.
# The frontend hides nav items whose module the user lacks; the middleware below
# returns 403 if a role hits an API path that maps to a module it doesn't hold.

ROLES: List[str] = [
    "management", "marketing", "operation", "analyst", "accounting", "innovation",
    "salessupport",
    "warehouse",           # คลัง: ยืนยันยอดส่งจริงของใบสั่งซื้อ (owner 2026-10-08 — ไม่ใช่ประสานงานขายอีกต่อไป)
    "branch",              # one account per branch (owner §10-5) — sees only its own branch's pages
    "franchise",           # เจ้าของสาขา (PR-L, owner 2026-10-10): one login per franchise branch, reads its own branch's summary only
]

# roles whose account is tied to ONE branch (the branch works it, the franchise owner reads it); the server pins every
# request of such a login to that branch (scope_branch). Only `branch` of these skips the write token (core.is_token_free_session).
PINNED_ROLES = ("branch", "franchise")

# roles whose signed-in login is enough to save — no write token (owner 2026-10-28: สาขา คลัง ครัว วิเคราะห์ นวัตกรรม).
# Skipping the token is not a role grant: the role rules still decide which writes each one reaches.
TOKEN_FREE_ROLES = ("branch", "warehouse", "innovation", "analyst")

MODULES: List[str] = [
    "sales.overview",      # หน้าแรก, ภาพรวม&KPI, ต้องดูวันนี้, รายวัน, แนวโน้ม, A/B, insight
    "customer.buffet",     # บุฟเฟ่ต์, บุฟเฟ่ต์เด็ก, ลูกค้า&ยอด/หัว, ช่วงเวลา
    "menu.analysis",       # วิเคราะห์เมนู, menu engineering, เทียบเมนู
    "cost.analysis",       # ต้นทุน, ต้นทุน/หัว, นักกินหนัก, ความครอบคลุม, BoM (view), GP
    "cost.simulator",      # simulators
    "bom.edit",            # แก้ไข BoM / SKU / cost (write)
    "profit.pnl",          # GP รายสาขา, P&L, กำไร SKU, benchmark
    "ops.report",          # รายงานปฏิบัติการ
    "ops.discounts",       # ส่วนลด, coupon
    "ops.cash",            # เงินขาด/เกิน
    "ops.staff",           # พนักงาน/แคชเชียร์
    "ops.posextra",        # custtype, modifiers, price-zone, cancel-bills, close-time
    "report.export",       # ส่งออกรายงาน Excel/PDF
    "report.management",   # รายงานผู้บริหาร (/api/reports/management, the desktop page and the phone's สรุปผู้บริหาร) — owner 2026-10-28:
                           # management only by default; a role gets the whole chain's management summary on purpose, not via sales.overview
    "system.settings",     # ตั้งค่า (targets, branch, sku active, closed hours)
    "system.admin",        # จัดการผู้ใช้
    "data.upload",         # อัปโหลด/seed ข้อมูล
    "customer.feedback",   # เสียงลูกค้า: รีวิว/ความรู้สึก/เมนูที่ถูกพูดถึง
    "supply.ordering",     # จัดซื้อ/วัตถุดิบ: แผนสั่ง, ยอดซื้อ vs ยอดขาย, ตั้งค่าการสั่งซื้อ
    "bom.center",          # BoM Center: ทะเบียนวัตถุดิบ, หน่วย, ราคารายเดือน (แก้ไข/นำเข้า)
    "bom.prices",          # BoM Center: ราคารายเดือนเท่านั้น (บัญชี — owner 2026-10-07)
    "bom.recipes",         # BoM Center: สูตรตาม Tier และสูตรกึ่งสำเร็จเท่านั้น (ครัว — owner 2026-10-07)
    "bom.release",         # BoM Center: ปล่อยเวอร์ชันสูตร (analyst — owner 2026-10-06)
    "bom.audit",           # BoM Center: ตรวจ/รับทราบเวอร์ชันที่ปล่อยแล้ว และย้อนกลับ (management)
    "branch.stock",        # สาขาของฉัน: นับสต๊อก (Phase 2) — a branch account works on its own branch only
    "supply.warehouse",    # คลัง: ระบุยอดส่งจริงและกด "ดำเนินการแล้ว" ที่คิวใบสั่งซื้อ (owner 2026-10-08)
    "franchise.view",      # ร้านของฉัน: สรุปผลของสาขาตนเองสำหรับเจ้าของสาขา แบบอ่านอย่างเดียว (PR-L)
    "franchise.inbox",     # ข้อความจากเจ้าของสาขา: อ่านและตอบคำถามของเจ้าของสาขาทุกสาขา (PR-F4, owner 2026-10-22)
]

# Recommended starting matrix (owner can edit user roles from Settings; the
# module-per-role mapping itself lives here in code).
ROLE_MODULES: Dict[str, Set[str]] = {
    "management": set(MODULES),                       # ดูได้ทั้งหมด
    "marketing": {
        "sales.overview", "customer.buffet", "menu.analysis", "report.export",
        "customer.feedback",
    },
    "operation": {
        "sales.overview", "ops.report", "ops.discounts", "ops.cash",
        "ops.staff", "ops.posextra", "report.export", "customer.feedback",
        "franchise.inbox",   # PR-F4: Operations answers the franchise owners beside management (routers/franchise_msgs.py)
    },
    "analyst": {
        # Brief = "วิเคราะห์ยอดขาย + ต้นทุน". Owner 2026-10-28: the analyst keeps ส่วนลด (discount leakage is
        # analysis work); the POS detail pages (บิลยกเลิก, ความต้องการพิเศษ, ราคาและโซน, ปิดร้าน) are operation's.
        "sales.overview", "customer.buffet", "menu.analysis", "cost.analysis",
        "cost.simulator", "profit.pnl", "ops.discounts",
        "report.export", "customer.feedback",
        "bom.center", "bom.release",   # owner 2026-10-06: the analyst maintains prices/recipes and RELEASES
    },
    "accounting": {
        # Brief = "งานบัญชี". ops.cash (เงินขาด/เกิน) + ops.discounts are
        # INTENTIONAL: both feed the books / month-end reconciliation.
        "sales.overview", "profit.pnl", "cost.analysis", "ops.cash",
        "ops.discounts", "report.export",
        # no BoM Center module (owner 2026-10-07): the monthly price is the analyst's to import;
        # accounting hands over the purchase file and reads the centre through cost.analysis
    },
    "innovation": {
        "sales.overview", "menu.analysis", "cost.analysis", "cost.simulator",
        "bom.edit", "system.settings", "customer.feedback", "bom.recipes",   # the kitchen edits recipes only (owner 2026-10-07)
    },
    "salessupport": {
        # Brief = "ใช้เมนู จัดซื้อ/วัตถุดิบ". Only the supply cluster — the purchase
        # sheet shows ingredient ฿ vs sales per branch, nothing chain-wide on cost.
        "sales.overview", "supply.ordering", "report.export",
    },
    "warehouse": {
        # The central warehouse updates what is actually sent (owner 2026-10-08). Like the branch login it
        # has no sales.overview: a role without it reaches only the paths a rule below names.
        "supply.warehouse",
    },
    "branch": {
        # A branch login (owner §10-5: one account per branch). Its own branch only —
        # the account's `branch` scopes every /api/bom-center/stock call. No
        # sales.overview: a role without it is DENY-by-default (path_allowed).
        "branch.stock",
    },
    "franchise": {
        # The owner of a franchise branch (PR-L). One module, one GET prefix; the only write rules are the six Web Push device
        # endpoints of its own devices (PR-F2). Every other path is closed to it (no sales.overview, so a path with no rule
        # is denied). The account's `branch` pins the summary.
        "franchise.view",
    },
}


# In-memory cache for dynamically stored role permissions (None = use ROLE_MODULES fallback).
_perm_cache: Optional[Dict[str, Set[str]]] = None


def modules_for_role(role: str) -> Set[str]:
    if _perm_cache is not None:
        return set(_perm_cache.get((role or "").lower(), set()))
    return set(ROLE_MODULES.get((role or "").lower(), set()))


def refresh_perm_cache(con: sqlite3.Connection) -> None:
    """Load role→module mapping from app_settings into memory cache. Called at
    startup and after every permission save so the middleware stays current."""
    global _perm_cache
    try:
        row = con.execute(
            "SELECT value FROM app_settings WHERE key='role_permissions'"
        ).fetchone()
    except sqlite3.OperationalError:
        _perm_cache = None
        return
    if not row:
        _perm_cache = None
        return
    try:
        data = json.loads(row["value"])
        result: Dict[str, Set[str]] = {}
        # The saved matrix predates any role/module added since. A role it has
        # never heard of starts at its code default; a module NO saved role holds
        # was introduced after the save, so it too starts at its defaults —
        # otherwise a new role would log in to nothing and management would lose
        # the new module's tabs.
        saved_mods = set().union(*[set(v) for v in data.values() if isinstance(v, list)])
        for r in ROLES:
            if r not in data:
                mods = set(ROLE_MODULES.get(r, set()))
            else:
                mods = set(data.get(r, [])) | {m for m in ROLE_MODULES.get(r, set())
                                               if m not in saved_mods}
            result[r] = mods & set(MODULES)  # only known modules
        _perm_cache = result
    except Exception:
        _perm_cache = None


def get_role_permissions(con: sqlite3.Connection) -> Dict[str, List[str]]:
    """Return the effective role→module mapping (DB overrides ROLE_MODULES)."""
    if _perm_cache is not None:
        return {r: sorted(_perm_cache.get(r, set())) for r in ROLES}
    return {r: sorted(ROLE_MODULES.get(r, set())) for r in ROLES}


def reset_role_permissions(con: sqlite3.Connection) -> None:
    """Drop the saved matrix so every role is back on ROLE_MODULES (owner 2026-10-28: the permissions page offers
    this as one click, because a saved matrix keeps winning over a default that later changes in code)."""
    con.execute("DELETE FROM app_settings WHERE key='role_permissions'")
    con.commit()
    refresh_perm_cache(con)


def set_role_permissions(con: sqlite3.Connection, perms: Dict[str, List[str]]) -> None:
    """Persist role→module mapping to app_settings and refresh in-memory cache."""
    cleaned: Dict[str, List[str]] = {}
    for role in ROLES:
        mods = set(perms.get(role, [])) & set(MODULES)
        cleaned[role] = sorted(mods)
    con.execute(
        "INSERT OR REPLACE INTO app_settings(key, value) VALUES ('role_permissions', ?)",
        (json.dumps(cleaned),),
    )
    con.commit()
    refresh_perm_cache(con)


# ---------------------------------------------------------------------------
# API path → module map  (server-side enforcement)
# ---------------------------------------------------------------------------
# PUBLIC: scaffolding every tab's filters need. Documentary only — these prefixes
# simply match no MODULE_RULES entry and so fall through to default-allow in
# path_allowed(). Listed here to make the "open to any authenticated user" intent
# explicit. NOTE: a sensitive sub-path under one of these (e.g.
# /api/branches/fixed-costs) IS tagged in MODULE_RULES and, being a longer prefix,
# overrides the public parent.
PUBLIC_PREFIXES = (
    "/api/auth/",          # login/me/logout reachable pre-authorization (middleware-exempt)
    "/api/branches",
    "/api/months",
    "/api/meta",
    "/api/data-coverage",
    "/api/market",
    "/api/search",
    "/api/nav/",           # the caller's own task counts (PR-D4): every signed-in role, no sales.overview needed
    "/api/analysis/health",
)
# what a role WITHOUT sales.overview may still reach when no rule names the path
OPEN_TO_ALL = PUBLIC_PREFIXES + ("/api/usage/",)

# Rules: (methods, path_prefix, {modules}). `methods` is "*", "GET", or "W"
# (write = POST/PUT/DELETE/PATCH). A request is allowed when the user's modules
# intersect the union of EVERY matching rule; paths matching no rule fall through
# to default-allow (every role holds sales.overview, so generic /api/sales and
# /api/analysis reads need no rule). Only the sensitive prefixes are tagged.
# Web Push (routers/push_api.py): the modules that reach every /api/push endpoint, and the device endpoints a franchise owner may
# also reach (its own devices only; PR-F2)
_PUSH_MODULES = frozenset({"sales.overview", "branch.stock", "supply.warehouse"})
PUSH_DEVICE_PATHS = ("vapid-public", "subscribe", "unsubscribe", "status", "prefs", "test")

MODULE_RULES = [
    # ── reads that must be denied to some roles ────────────────────────────
    ("GET", "/api/cost",                     {"cost.analysis"}),
    ("GET", "/api/sales/margin",             {"cost.analysis"}),
    ("GET", "/api/sales/cost-per-head",      {"cost.analysis"}),
    ("GET", "/api/sales/heavy-eaters",       {"cost.analysis"}),   # per-cover COGS/margin
    ("GET", "/api/analysis/gross_profit",    {"cost.analysis"}),
    ("GET", "/api/analysis/food_cost_trend", {"cost.analysis"}),
    ("GET", "/api/bom-cost",                 {"cost.simulator", "bom.edit", "cost.analysis"}),
    ("GET", "/api/menu/engineering",         {"cost.analysis"}),   # star/dog by margin = cost
    ("GET", "/api/menu/action-board",        {"cost.analysis"}),   # buckets dishes by margin → cost
    ("GET", "/api/menu/opportunities",       {"menu.analysis"}),   # qty/share/trend only, no COGS
    ("W",   "/api/menu/ai-ideas",            {"menu.analysis"}),   # AI menu concepts (+ write-token)
    ("*",   "/api/feedback",                 {"customer.feedback"}),  # reviews: read + upload/delete (+ write-token)
    ("*",   "/api/checklist",                {"customer.feedback"}),  # go-live checklist ticks (who = the session)
    ("GET", "/api/finance/prime-cost",       {"cost.analysis", "profit.pnl"}),
    ("GET", "/api/period/cost",              {"cost.analysis", "profit.pnl"}),   # PR-PP3: COGS / GP of one period
    ("GET", "/api/cost/ingredient-groups",   {"cost.analysis", "profit.pnl"}),   # High Value Product Monitor (+ /menus)
    ("GET", "/api/branches/fixed-costs",     {"cost.analysis", "profit.pnl", "system.settings"}),
    ("GET", "/api/branches/labor-costs",     {"cost.analysis", "profit.pnl", "system.settings"}),
    ("GET", "/api/export/monthly-pack",      {"cost.analysis", "profit.pnl"}),  # bundles P&L + cost drivers
    # Buffet: the broad prefix is customer/sales-facing (covers tier-mix, child,
    # revpash, performance, forecast…). Longest-prefix wins, so the cost-bearing
    # sub-paths below DROP customer.buffet and require a cost/profit module —
    # otherwise Marketing's customer.buffet would unlock buffet GP/COGS/P&L.
    ("GET", "/api/buffet",                   {"customer.buffet", "cost.analysis",
                                              "cost.simulator", "profit.pnl"}),
    ("GET", "/api/buffet/gp",                {"cost.analysis", "profit.pnl"}),  # +gp-calendar, gp/export
    ("GET", "/api/buffet/pnl",               {"profit.pnl", "cost.analysis"}),
    ("GET", "/api/buffet/cover-economics",   {"cost.analysis", "profit.pnl"}),
    ("GET", "/api/buffet/plate-cost-dist",   {"cost.analysis"}),
    ("GET", "/api/buffet/cost-sensitivity",  {"cost.analysis", "cost.simulator"}),
    ("GET", "/api/buffet/menu-cost-efficiency", {"cost.analysis"}),
    ("GET", "/api/buffet/heavy-eater-tail",  {"cost.analysis"}),
    ("GET", "/api/buffet/breakeven",         {"cost.analysis", "profit.pnl"}),
    ("GET", "/api/buffet/upgrade-simulator", {"cost.simulator", "cost.analysis"}),
    ("GET", "/api/profit",                   {"profit.pnl"}),
    ("GET", "/api/pnl",                      {"profit.pnl"}),
    ("GET", "/api/gp",                       {"profit.pnl"}),
    ("GET", "/api/reports/pnl-bill",         {"profit.pnl"}),
    ("GET", "/api/reports/management",       {"report.management"}),   # + /months and .xlsx (prefix): the owner's summary, management only by default
    ("GET", "/api/reports/ops",              {"ops.report"}),
    ("GET", "/api/ingredients",              {"cost.analysis", "ops.report",
                                              "supply.ordering"}),  # order plan + branch letter + purchases
    ("W",   "/api/ingredients",              {"supply.ordering", "ops.report", "bom.center"}),   # ตั้งค่าการสั่งซื้อ and the legacy plan
    ("GET", "/api/analysis/discount",        {"ops.discounts"}),
    ("GET", "/api/analysis/coupon",          {"ops.discounts"}),
    ("GET", "/api/extra/cash",               {"ops.cash"}),
    ("GET", "/api/analysis/cashier",         {"ops.staff"}),
    ("GET", "/api/extra/customer-type",      {"ops.posextra", "customer.buffet"}),
    ("GET", "/api/extra/modifiers",          {"ops.posextra"}),
    ("GET", "/api/extra/price-zone",         {"ops.posextra", "customer.buffet"}),
    ("GET", "/api/extra/cancel-bills",       {"ops.posextra"}),
    ("GET", "/api/extra/closing-time",       {"ops.posextra", "customer.buffet"}),
    ("GET", "/api/sales/skus",               {"menu.analysis", "cost.analysis"}),
    ("GET", "/api/sales/top_ordered",        {"menu.analysis", "cost.analysis"}),
    ("GET", "/api/sales/menu-compare",       {"menu.analysis"}),
    ("GET", "/api/sales/menu-spike",         {"menu.analysis", "customer.buffet"}),
    ("GET", "/api/sales/ingredient-groups-plates", {"menu.analysis", "customer.buffet"}),   # the card's plates section: no cost
    ("GET", "/api/analysis/affinity",        {"menu.analysis"}),
    ("GET", "/api/analysis/persona",         {"menu.analysis", "customer.buffet"}),
    # ── writes (also write-token gated in main.py — defense in depth) ───────
    ("W",   "/api/cost",                     {"bom.edit"}),
    ("GET", "/api/bom-center",               {"cost.analysis", "supply.ordering", "bom.center", "bom.edit", "bom.prices", "bom.recipes"}),
    ("W",   "/api/bom-center",               {"bom.center"}),
    # owner 2026-10-07: accounting keeps the monthly price (and the unit rules a price file needs),
    # the kitchen keeps the recipes and sub-recipes; everything else in the centre is bom.center
    ("W",   "/api/bom-center/prices",                  {"bom.center", "bom.prices"}),
    # PR-S2: the analyst's monthly dish cost list - read by the analyst, accounting and management; imported by the analyst (and accounting)
    ("GET", "/api/bom-center/dish-cost",               {"bom.center", "bom.prices", "cost.analysis"}),
    ("W",   "/api/bom-center/dish-cost",               {"bom.center", "bom.prices"}),
    # PR-D: Sales Support sends the month's price file; only the analyst (and accounting) imports or rejects it
    ("W",   "/api/bom-center/prices/submit",           {"supply.ordering", "bom.center", "bom.prices"}),
    ("W",   "/api/bom-center/prices/submissions",      {"bom.center", "bom.prices"}),
    ("W",   "/api/bom-center/units",                   {"bom.center", "bom.prices"}),
    # PR-U2: a branch reports a pack that is not the registered size from its receive form (pinned to its own order in the
    # router); the analyst's decision on it follows the /units writers above
    ("W",   "/api/bom-center/units/pack-report",       {"branch.stock", "bom.center"}),
    ("W",   "/api/bom-center/units/pack-report/decide", {"bom.center", "bom.prices"}),
    # PR-Y: Sales Support (and the analyst) report a new code of an existing item; the analyst decides (the /units writers' rule).
    # Recording, retiring or switching an alias on the register row is the analyst's alone (the general /api/bom-center rule)
    ("W",   "/api/bom-center/codes/request",           {"supply.ordering", "bom.center"}),
    ("W",   "/api/bom-center/codes/request/decide",    {"bom.center", "bom.prices"}),
    ("W",   "/api/bom-center/recipes",                 {"bom.center", "bom.recipes"}),
    ("W",   "/api/bom-center/prep",                    {"bom.center", "bom.recipes"}),
    # release workflow (owner 2026-10-06): the analyst releases, management acknowledges / rolls back
    ("W",   "/api/bom-center/release-act/release",     {"bom.release"}),
    ("W",   "/api/bom-center/release-act/acknowledge", {"bom.audit"}),
    ("W",   "/api/bom-center/release-act/retire",      {"bom.release", "bom.audit"}),
    ("W",   "/api/bom-center/release-act/accept",      {"bom.release"}),
    # PR-C: the kitchen (bom.recipes) opens a draft and submits it for review; only the analyst sends it back
    ("W",   "/api/bom-center/release-act/new-draft",   {"bom.center", "bom.recipes"}),
    ("W",   "/api/bom-center/release-act/submit-review", {"bom.center", "bom.recipes"}),
    ("W",   "/api/bom-center/release-act/send-back",   {"bom.release"}),
    # the order calendar is the sales team's to fill (owner §10-7); Sales Support runs suggestions
    ("W",   "/api/bom-center/order-calendar",          {"supply.ordering", "bom.center"}),
    ("W",   "/api/bom-center/event-calendar",          {"supply.ordering", "bom.center"}),   # PR-B4a (1.8): festivals / promotions, the sales team's too
    ("W",   "/api/bom-center/forecast",                {"supply.ordering", "bom.center"}),
    # Phase 2: a branch counts its own stock; the centre reads every branch's and sets zones/units
    ("GET", "/api/bom-center/stock",                   {"branch.stock", "supply.ordering", "bom.center", "ops.report"}),
    ("W",   "/api/bom-center/stock",                   {"branch.stock", "bom.center"}),
    ("W",   "/api/bom-center/stock/layout",            {"bom.center", "supply.ordering"}),
    # PR-K: the storage zones (โซนจัดเก็บ) are the centre's to add / rename / delete / reorder; a branch only reads them
    ("W",   "/api/bom-center/zones",                   {"bom.center", "supply.ordering"}),
    # a count FILE may come from the centre too (the retro close): the on-screen count stays the branch's
    ("W",   "/api/bom-center/stock/count-import",      {"branch.stock", "bom.center", "supply.ordering"}),
    # S7 usage variance: the branch reads its own and gives reasons; the centre reads every branch
    ("GET", "/api/bom-center/variance",                {"branch.stock", "supply.ordering", "bom.center", "ops.report"}),
    ("W",   "/api/bom-center/variance",                {"branch.stock", "bom.center", "supply.ordering"}),   # owner 2026-10-07: Sales Support may confirm / reason for a branch
    ("GET", "/api/bom-center/variance/overview",       {"supply.ordering", "bom.center", "ops.report"}),
    # PR-F ใบสรุปตัดเบิก: Accounting downloads it and ticks "posted" (cost.analysis, no BoM Center module needed);
    # a branch login reads its own branch only (the router pins it); posting is Accounting's / the analyst's
    ("GET", "/api/bom-center/issue-summary",           {"cost.analysis", "bom.center", "supply.ordering", "ops.report", "branch.stock"}),
    ("W",   "/api/bom-center/issue-summary",           {"cost.analysis", "bom.center"}),
    # S8 the branch order: the branch suggests/confirms/submits/receives its own; the centre may too
    ("GET", "/api/bom-center/order",                   {"branch.stock", "supply.ordering", "bom.center", "ops.report"}),
    ("W",   "/api/bom-center/order",                   {"branch.stock", "supply.ordering", "bom.center"}),
    # S9 the centre: Sales Support works the queue; Ops reads compliance and the monthly summary
    ("GET", "/api/bom-center/hub",                     {"supply.ordering", "ops.report", "bom.center", "supply.warehouse"}),
    # owner 2026-10-08: the WAREHOUSE updates what is sent (ส่งจริง + เหตุผล + PO + ดำเนินการแล้ว), no longer Sales Support
    ("W",   "/api/bom-center/hub",                     {"supply.warehouse"}),
    # Sales Support marks the orders it has keyed into the accounting system (the longer prefix beats the warehouse-only rule above)
    ("W",   "/api/bom-center/hub/keyed",               {"supply.ordering"}),
    # claims (owner 2026-10-08): the branch reports a claim (a reason per line; photos go by LINE) while receiving or after; Sales Support decides.
    # Reading is open to the supply roles; a branch login is pinned to its own branch in the router.
    ("GET", "/api/bom-center/claims",                  {"branch.stock", "supply.ordering", "bom.center", "ops.report"}),
    ("W",   "/api/bom-center/claims",                  {"branch.stock", "supply.ordering", "bom.center"}),
    ("W",   "/api/bom-center/claim-act",               {"supply.ordering"}),
    # ใบตัดเบิก (2026-10-09): the branch records and confirms its daily issue slip; the supply roles, Ops and
    # Accounting read; ONLY Sales Support / the analyst reopens a confirmed slip (the longer prefix beats the rule above it)
    ("GET", "/api/bom-center/issues",                  {"branch.stock", "supply.ordering", "bom.center", "ops.report", "cost.analysis"}),
    ("W",   "/api/bom-center/issues",                  {"branch.stock", "supply.ordering", "bom.center"}),
    ("W",   "/api/bom-center/issues/reopen",           {"supply.ordering", "bom.center"}),
    # PR-J4: the confirmed slips as Excel (Accounting posts from it); a branch login gets its own branch only (the router pins it)
    ("GET", "/api/bom-center/issues/export",           {"cost.analysis", "supply.ordering", "bom.center", "ops.report", "branch.stock"}),
    # PR-J3 issue-slip reports (system stock vs count, daily issue vs BoM): read-only; a branch login is pinned to its own branch
    ("GET", "/api/bom-center/issue-reports",           {"branch.stock", "supply.ordering", "bom.center", "ops.report", "cost.analysis"}),
    # PR-V ingredient usage per ฿100,000 of sales (report + Excel): read-only; a branch login is pinned to its own branch (the router)
    ("GET", "/api/bom-center/per-sales",              {"branch.stock", "supply.ordering", "bom.center", "ops.report", "cost.analysis"}),
    # PR-X the benchmark from the company-owned branches: the settings card is the sales team's / the analyst's (a branch login reaches
    # neither); the recipe-and-unit check against real purchases is the analyst's / management's alone (they hold bom.center)
    ("GET", "/api/bom-center/benchmark",              {"supply.ordering", "bom.center", "ops.report"}),
    ("W",   "/api/bom-center/benchmark",              {"supply.ordering", "bom.center"}),
    ("GET", "/api/bom-center/recipe-check",           {"bom.center"}),
    # batch 4b: the baht floor of a variance reason and the cycle-count switch (the analyst edits, the supply roles read); a partial month
    # close is the branch's, the analyst's or Sales Support's (the longer prefix beats the /stock rule); the tasks made from the reasons
    # are read and acknowledged by the roles that own them (the router checks which); the ranked data-quality backlog is the analyst's
    ("GET", "/api/bom-center/close-settings",         {"supply.ordering", "bom.center", "ops.report", "cost.analysis"}),
    ("W",   "/api/bom-center/close-settings",         {"bom.center"}),
    # PR-S1 the system mode: every signed-in role reads it (the menu is drawn from it - a branch or franchise login holds no sales.overview,
    # so a rule naming every module is what lets it through); only management (bom.audit) changes it
    ("GET", "/api/bom-center/mode",                   set(MODULES)),
    ("W",   "/api/bom-center/mode",                   {"bom.audit"}),
    ("W",   "/api/bom-center/stock/partial-close",    {"branch.stock", "bom.center", "supply.ordering"}),
    ("*",   "/api/bom-center/feedback",               {"bom.center", "bom.recipes", "ops.report", "supply.ordering"}),
    ("GET", "/api/bom-center/backlog",                {"bom.center", "bom.prices", "bom.recipes"}),
    # S11 the central list and the month's deliveries: the analyst or Sales Support keeps them
    ("W",   "/api/bom-center/central",                 {"supply.ordering", "bom.center"}),   # bom.edit (the kitchen) is not the centre
    # the list and the code links are read by Ops too (the close stepper opens on them)
    ("GET", "/api/bom-center/central",                 {"supply.ordering", "bom.center", "bom.edit", "ops.report", "cost.analysis", "bom.prices", "bom.recipes"}),
    # หน้างาน: every supply role reads its own to-do list (a branch login gets its branch only)
    ("GET", "/api/bom-center/todo",                    {"branch.stock", "supply.ordering", "bom.center", "bom.edit", "bom.prices", "bom.recipes",
                                                        "bom.release", "bom.audit", "ops.report", "cost.analysis", "supply.warehouse"}),
    ("GET", "/api/requisition",              {"cost.analysis", "ops.report"}),
    ("W",   "/api/requisition",              {"bom.edit", "data.upload"}),
    ("W",   "/api/skus",                     {"bom.edit"}),
    ("W",   "/api/bom",                      {"bom.edit"}),
    ("W",   "/api/settings",                 {"system.settings"}),
    ("W",   "/api/targets",                  {"system.settings"}),
    ("GET", "/api/franchise",                {"franchise.view"}),   # PR-L: ร้านของฉัน (read-only; its only W rules are the push device endpoints below)
    # PR-F4: the owner's questions to head office and the replies (routers/franchise_msgs.py). The franchise login (franchise.view) reads
    # and writes its OWN branch's threads only - the handler pins the branch to the signed session; head office (franchise.inbox:
    # management + operation) reads every thread, replies, closes and reopens. GET and W in one rule: operation holds no franchise.view,
    # so the GET /api/franchise rule above would refuse it the inbox. The handler re-checks the side of every call.
    ("*",   "/api/franchise/messages",       {"franchise.view", "franchise.inbox"}),
    # batch 3 (2.1): a phone turns on the reminders of its own login's work. Every endpoint answers for the caller's own devices; the
    # branch login and the warehouse hold no sales.overview, so they are named here
    ("*",   "/api/push",                     _PUSH_MODULES),
    # PR-F2: the franchise owner turns its own morning summary (kind "franchise") on and off. Only the six device endpoints, each
    # of which answers for the caller's own devices (push_api: _caller, username match); any other /api/push path stays closed
    *[("*", "/api/push/" + _p, _PUSH_MODULES | {"franchise.view"}) for _p in PUSH_DEVICE_PATHS],
    ("W",   "/api/branches",                 {"system.settings"}),
    ("W",   "/api/closed_hours",             {"system.settings"}),
    ("W",   "/api/upload",                   {"data.upload"}),
    ("W",   "/api/_admin/seed-db",           {"data.upload"}),
    # The off-site backup of the owner-data tables (password hashes included) is a machine
    # endpoint gated by the admin token ALONE (main._backup_token_or_403, which the login
    # middleware lets through). An empty module set = no role, not even an admin, may reach
    # it with a session: if the middleware bypass is ever removed, it fails closed.
    ("*",   "/api/_admin/backup",            set()),
    ("*",   "/api/auth/users",               {"system.admin"}),
    # the cloud pull of Easy POS (backend/cloud_sync.py): its status and the "pull now" button are Management's alone
    ("*",   "/api/admin/cloud-sync",         {"system.admin"}),
    # the cloud backup of oshinei.db (backend/cloud_backup.py): the same rule
    ("*",   "/api/admin/cloud-backup",       {"system.admin"}),
    # every cloud job in one list, and the buttons of the feedback / Invisible / watchdog jobs (backend/cloud_jobs.py)
    ("*",   "/api/admin/cloud-jobs",         {"system.admin"}),
]


def _method_matches(rule_methods: str, method: str) -> bool:
    method = (method or "GET").upper()
    if rule_methods == "*":
        return True
    if rule_methods == "GET":
        return method == "GET"
    if rule_methods == "W":
        return method in ("POST", "PUT", "DELETE", "PATCH")
    return rule_methods == method


def path_allowed(method: str, path: str, user_modules: Set[str]) -> bool:
    """True if a user holding `user_modules` may call `method path`.

    LONGEST-PREFIX wins: among the rules matching this method+path, only the
    most specific (longest prefix) decide access — so a narrow cost rule like
    ``/api/buffet/gp`` overrides the broad ``/api/buffet`` rule and a sensitive
    ``/api/branches/fixed-costs`` overrides the public ``/api/branches`` list.
    A path matching NO rule defaults to allowed (every authenticated role holds
    sales.overview, so generic /api/sales and /api/analysis reads need no rule).
    PUBLIC_PREFIXES are documentary: they simply match no rule and fall through."""
    matched = [(len(pfx), mods) for (meths, pfx, mods) in MODULE_RULES
               if path.startswith(pfx) and _method_matches(meths, method)]
    if not matched:
        # default-allow rests on every central role holding sales.overview; a role
        # without it (the branch login) reaches only the public scaffolding
        return "sales.overview" in user_modules or path.startswith(OPEN_TO_ALL)
    longest = max(length for length, _ in matched)
    allowed = set().union(*[mods for length, mods in matched if length == longest])
    return bool(allowed & user_modules)


# ---------------------------------------------------------------------------
# Passwords  (pbkdf2-hmac-sha256, stdlib)
# ---------------------------------------------------------------------------
_PBKDF2_ROUNDS = 200_000


def hash_password(password: str, salt: Optional[bytes] = None) -> Dict[str, str]:
    if salt is None:
        salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return {"hash": dk.hex(), "salt": salt.hex()}


def verify_password(password: str, pw_hash: str, pw_salt: str) -> bool:
    try:
        salt = bytes.fromhex(pw_salt)
    except (ValueError, TypeError):
        return False
    calc = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"),
                               salt, _PBKDF2_ROUNDS)
    return hmac.compare_digest(calc.hex(), pw_hash or "")


# ---------------------------------------------------------------------------
# Stateless signed-cookie sessions
# ---------------------------------------------------------------------------
SESSION_COOKIE = "oshinei_session"
SESSION_TTL = 12 * 3600   # 12 hours
SESSION_TTL_BRANCH = 30 * 24 * 3600   # the branch phone stays signed in for a month (owner 2026-10-07)
SESSION_TTL_REMEMBER = SESSION_TTL_BRANCH   # "จำการเข้าสู่ระบบบนเครื่องนี้ไว้ 30 วัน" on the login form (owner 2026-10-07)


def session_ttl(role: Optional[str], remember: bool = False) -> int:
    """How long a login lasts. The branch login lives on the shop's phone and must
    not ask for the password before every count; every other role keeps 12 h unless
    the person ticked "remember this device" on the login form (the owner's phone)."""
    if role == "branch":
        return SESSION_TTL_BRANCH
    return SESSION_TTL_REMEMBER if remember else SESSION_TTL


class AuthMisconfigured(RuntimeError):
    """Raised when auth is ENABLED but no real signing secret is configured —
    refusing to sign/verify with the public dev fallback (which would make every
    session forgeable). Callers translate this to HTTP 503."""


# Domain-separation tag: the session-signing key is derived through this prefix so
# it can NEVER equal the raw write-token, even when OSHINEI_AUTH_SECRET is unset
# and we fall back to OSHINEI_SEED_TOKEN. Without it, any write-token holder could
# mint a forged admin cookie offline.
_SESSION_KEY_DOMAIN = b"oshinei-session-key-v1|"


def secret_configured() -> bool:
    """True if a real (non-dev) signing secret is present in the environment."""
    return bool(os.environ.get("OSHINEI_AUTH_SECRET")
                or os.environ.get("OSHINEI_SEED_TOKEN"))


def _secret() -> bytes:
    raw = os.environ.get("OSHINEI_AUTH_SECRET") or os.environ.get("OSHINEI_SEED_TOKEN")
    if not raw:
        # Fail CLOSED when enforcing: never sign real sessions with a constant
        # baked into the source tree. Locally (auth off) a fixed dev key is fine.
        if auth_enabled():
            raise AuthMisconfigured(
                "OSHINEI_AUTH=1 but no OSHINEI_AUTH_SECRET/OSHINEI_SEED_TOKEN set")
        raw = "oshinei-dev-secret-change-me"
    # Domain-separate + fix the key length so a short token still yields a strong key.
    return hashlib.sha256(_SESSION_KEY_DOMAIN + raw.encode("utf-8")).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def make_token(uid: int, username: str, role: str, ttl: int = SESSION_TTL, branch: Optional[str] = None) -> str:
    payload = {"uid": uid, "u": username, "r": role, "exp": int(time.time()) + ttl}
    if branch:
        payload["b"] = branch          # the branch this login is scoped to (feedback endpoints enforce it)
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def parse_token(token: str) -> Optional[Dict]:
    """Return the payload dict if the token is well-formed, correctly signed and
    unexpired; otherwise None."""
    if not token or "." not in token:
        return None
    body, _, sig = token.partition(".")
    expected = _b64(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


def identity_from_row(row) -> Dict:
    """The parts of a LIVE app_user row the request helpers need, in the cookie's key
    names (u / r / b) so callers that read a payload keep working."""
    keys = row.keys() if hasattr(row, "keys") else ()
    return {"id": row["id"] if "id" in keys else None, "u": row["username"], "r": row["role"] or "",
            "b": (row["branch"] or None) if "branch" in keys else None,
            "active": bool(row["active"]), "live": True}


def live_session(request) -> Dict:
    """Who this request is, from the LIVE account — never from the cookie's copy of
    role / branch, which can be up to the session TTL stale (a demoted manager, a
    deactivated login, a branch moved). Returns {} when there is no valid active session.

    The auth middleware (main.enforce_auth) loads the app_user row for every gated
    request and stores identity_from_row() on request.state.auth_user; this reads it.
    On a path the middleware lets through without loading it, the row is looked up
    here. Only when the database cannot be read at all does it fall back to the
    signed cookie's own values (marked live=False)."""
    if not auth_enabled():
        return {}
    state = getattr(request, "state", None)
    cached = getattr(state, "auth_user", None) if state is not None else None
    if isinstance(cached, dict):
        return cached
    try:
        payload = parse_token(request.cookies.get(SESSION_COOKIE, "")) or {}
    except Exception:
        payload = {}
    if not payload:
        return {}
    try:
        from core import get_db as _get_db          # lazy: core imports this module lazily too
        with _get_db() as con:
            row = get_user(con, str(payload.get("u") or ""))
    except Exception:
        return {"id": payload.get("uid"), "u": payload.get("u"), "r": payload.get("r") or "",
                "b": payload.get("b") or None, "active": True, "live": False}
    if not row or not row["active"]:
        return {}
    ident = identity_from_row(row)
    if state is not None:
        try:
            state.auth_user = ident
        except Exception:
            pass
    return ident


def request_modules(request) -> Set[str]:
    """The modules the session of this request holds (every module with auth off), from
    the LIVE role (live_session) and the live role map. No / forged / inactive session
    holds none."""
    if not auth_enabled():
        return set(MODULES)
    return modules_for_role(live_session(request).get("r") or "")


def client_rules() -> List[list]:
    """The GET-relevant part of MODULE_RULES, for the browser: [[prefix, [modules...]]...] (rules for
    GET and for every method). The pages read it from /api/auth/me so that a page asks only for what the
    server will answer - ONE rule list, kept here (path_allowed below is its twin in JS)."""
    out = []
    for meths, pfx, mods in MODULE_RULES:
        if meths in ("GET", "*"):
            out.append([pfx, sorted(mods)])
    return out


def scope_branch(request) -> Optional[str]:
    """The branch a logged-in user is confined to, or None (the whole chain,
    or auth off). Read from the LIVE account (live_session), so a login moved to
    another branch, or a branch renamed, takes effect on the next request."""
    if not auth_enabled():
        return None
    return live_session(request).get("b") or None


def auth_enabled() -> bool:
    return os.environ.get("OSHINEI_AUTH", "").strip() not in ("", "0", "false", "False")


# ---------------------------------------------------------------------------
# User store
# ---------------------------------------------------------------------------
# Two seeded users per role. Default password is shared and documented — it MUST
# be changed from Settings → ผู้ใช้งาน after first login.
DEFAULT_PASSWORD = "Oshinei@2026"

SEED_USERS = [f"{role}{n}" for role in ROLES for n in (1, 2)]

# Password policy (AUD-1): a minimum length is the single highest-value rule
# against the weak / shared-password finding. Enforced on every path that sets a
# password (admin create + reset, and self-service change). Seeding bypasses it
# deliberately — the seeded default is paired with must_change=1.
MIN_PASSWORD_LEN = 10


# Temporary passwords (code guard 2, 2026-10-28): an account an admin or a script hands out gets its
# OWN random starting password, never the shared default - with predictable logins (wh_<ตัวย่อ>,
# own_<ตัวย่อ>) one branch's handout must not open every other branch. The shared default stays the
# seed of an EMPTY table (a fresh local DB, the tests) and never signs in on the live server
# (default_refused). Lower-case letters and digits without look-alikes (0/o, 1/l/i), 3 groups of 4,
# so a branch phone can type it.
_TEMP_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def temp_password() -> str:
    return "-".join("".join(secrets.choice(_TEMP_ALPHABET) for _ in range(4)) for _ in range(3))


def default_refused(password: str) -> bool:
    """On the live server (Fly sets FLY_APP_NAME) the shared default never signs anyone in."""
    return bool(os.environ.get("FLY_APP_NAME")) and (password or "").strip().lower() == DEFAULT_PASSWORD.lower()


def check_password_policy(password: str) -> None:
    """Raise ValueError if the new password is too weak or is the shared default."""
    pw = password or ""
    if len(pw) < MIN_PASSWORD_LEN:
        raise ValueError(f"รหัสผ่านต้องยาวอย่างน้อย {MIN_PASSWORD_LEN} ตัวอักษร")
    if pw.strip().lower() == DEFAULT_PASSWORD.lower():
        raise ValueError("ห้ามใช้รหัสผ่านเริ่มต้นที่แชร์กัน ให้ตั้งรหัสผ่านใหม่")


def init_auth(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS app_user (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT UNIQUE NOT NULL,
            pw_hash     TEXT NOT NULL,
            pw_salt     TEXT NOT NULL,
            role        TEXT NOT NULL,
            active      INTEGER NOT NULL DEFAULT 1,
            must_change INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    # Additive migration for DBs created before must_change existed.
    cols = {r[1] for r in con.execute("PRAGMA table_info(app_user)")}
    if "must_change" not in cols:
        con.execute("ALTER TABLE app_user ADD COLUMN must_change INTEGER NOT NULL DEFAULT 0")
    if "branch" not in cols:      # E4: a login scoped to one branch (NULL = the whole chain)
        con.execute("ALTER TABLE app_user ADD COLUMN branch TEXT")
    if "display_name" not in cols:   # PR-M1: the name people read on screens (never the login id)
        con.execute("ALTER TABLE app_user ADD COLUMN display_name TEXT")
    con.commit()


def admin_roles() -> Set[str]:
    """Roles that grant the system.admin module (i.e. can manage users)."""
    return {r for r in ROLES if "system.admin" in ROLE_MODULES.get(r, set())}


def is_admin_role(role: str) -> bool:
    return "system.admin" in modules_for_role(role)


def count_active_admins(con: sqlite3.Connection) -> int:
    """How many ACTIVE users currently hold an admin role — used to refuse the
    last-admin lockout."""
    roles = admin_roles()
    if not roles:
        return 0
    placeholders = ",".join("?" * len(roles))
    return con.execute(
        f"SELECT COUNT(*) FROM app_user WHERE active=1 AND role IN ({placeholders})",
        tuple(roles),
    ).fetchone()[0]


def provision_user(con: sqlite3.Connection, username: str, role: str,
                   branch: Optional[str] = None,
                   password: Optional[str] = None,
                   reset_existing: bool = False) -> str:
    """Provision ONE account on a temporary password with must_change=1 (the user
    is forced off it at first login: the frontend opens the change-password dialog
    before anything else).

    Without `password` the account gets its own random temp_password() - never the
    shared default. Read it back with last_temp_password() right after the call (the
    callers show it once to whoever hands it out). An explicit `password` must pass
    check_password_policy().

    Idempotent: an existing account is left alone and "exists" is returned,
    unless reset_existing=True, in which case it gets a new temporary password
    and must_change is re-armed ("reset"). A brand-new account returns "created".
    """
    username = (username or "").strip()
    if not username:
        raise ValueError("username required")
    if role not in ROLES:
        raise ValueError("invalid role")
    branch = (branch or "").strip() or None
    if branch and not branch_exists(con, branch):
        raise ValueError("ไม่พบสาขานี้")
    if password:
        check_password_policy(password)       # explicit password → normal policy
    pw = password or temp_password()
    _last_temp["pw"] = None

    existing = get_user(con, username)
    if existing and not reset_existing:
        return "exists"
    if not existing and role == "franchise" and not branch:
        raise ValueError("บัญชีเจ้าของสาขาต้องเลือกสาขา")
    h = hash_password(pw)
    if existing:
        con.execute(
            "UPDATE app_user SET pw_hash=?, pw_salt=?, must_change=1, active=1 "
            "WHERE username=?",
            (h["hash"], h["salt"], username),
        )
        con.commit()
        _last_temp["pw"] = pw
        return "reset"
    con.execute(
        "INSERT INTO app_user(username, pw_hash, pw_salt, role, must_change, branch) "
        "VALUES (?,?,?,?,1,?)",
        (username, h["hash"], h["salt"], role, branch),
    )
    con.commit()
    _last_temp["pw"] = pw
    return "created"


_last_temp: Dict[str, Optional[str]] = {"pw": None}


def last_temp_password() -> Optional[str]:
    """The password the latest provision_user() call set (None when it changed nothing)."""
    return _last_temp["pw"]


def seed_users(con: sqlite3.Connection) -> None:
    """Create the 12 default users iff the table is empty (idempotent)."""
    n = con.execute("SELECT COUNT(*) FROM app_user").fetchone()[0]
    if n:
        return
    for username in SEED_USERS:
        role = username.rstrip("12")
        h = hash_password(DEFAULT_PASSWORD)
        con.execute(
            "INSERT INTO app_user(username, pw_hash, pw_salt, role, must_change) VALUES (?,?,?,?,1)",
            (username, h["hash"], h["salt"], role),   # must_change=1 → forced off the shared default
        )
    con.commit()
    print(f"[auth] seeded {len(SEED_USERS)} users (default password '{DEFAULT_PASSWORD}' — must change on first login)")


# ── user-record helpers (used by the API endpoints in main.py) ──────────────
def get_user(con: sqlite3.Connection, username: str) -> Optional[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM app_user WHERE username = ?", (username,)
    ).fetchone()


def list_users(con: sqlite3.Connection) -> List[Dict]:
    rows = con.execute(
        "SELECT id, username, role, active, must_change, created_at, branch, display_name "
        "FROM app_user ORDER BY role, username"
    ).fetchall()
    return [dict(r) for r in rows]


# The label a person sees when no display name has been set (PR-M1): worker screens never show a login id.
ROLE_TH = {"management": "ผู้บริหาร", "marketing": "การตลาด", "operation": "ฝ่ายปฏิบัติการ",
           "analyst": "ฝ่ายวิเคราะห์", "accounting": "บัญชี", "innovation": "ครัว",
           "salessupport": "ประสานงานขาย", "warehouse": "คลัง", "branch": "สาขา",
           "franchise": "เจ้าของสาขา"}


def display_name_of(row) -> str:
    """app_user.display_name, else สาขา{branch} for a branch / franchise login, else the Thai role label."""
    keys = row.keys() if hasattr(row, "keys") else []
    dn = ((row["display_name"] if "display_name" in keys else None) or "").strip()
    if dn:
        return dn
    role = row["role"]
    br = (row["branch"] if "branch" in keys else None) or ""
    if role == "branch" and br:
        return f"สาขา{br}"
    if role == "franchise" and br:
        return f"เจ้าของสาขา{br}"
    return ROLE_TH.get(role, row["username"])


def display_name_for(con: sqlite3.Connection, username: str) -> Optional[str]:
    """The display name of a login, or None when there is no such account."""
    try:
        row = get_user(con, username)
    except sqlite3.OperationalError:
        return None
    return display_name_of(row) if row else None


def names_map(con: sqlite3.Connection, viewer_login: Optional[str] = None,
              viewer_role: Optional[str] = None) -> Dict[str, str]:
    """{username: display name} — the audit lines of the screens read names, not logins.

    A pinned session (branch / franchise) gets only its own login plus the centre accounts whose names its
    screens show; it never receives another branch's or franchise's login (a login name is half a credential).
    A centre role (and a call with no viewer) gets the full map."""
    try:
        rows = con.execute("SELECT * FROM app_user").fetchall()
    except sqlite3.OperationalError:
        return {}
    pinned = viewer_role in PINNED_ROLES
    return {r["username"]: display_name_of(r) for r in rows
            if not pinned or r["username"] == viewer_login or r["role"] not in PINNED_ROLES}


def branch_exists(con: sqlite3.Connection, name: str) -> bool:
    try:
        return bool(con.execute("SELECT 1 FROM branches WHERE name = ?", (name,)).fetchone())
    except sqlite3.OperationalError:
        return False


def create_user(con: sqlite3.Connection, username: str, password: str, role: str,
                branch: Optional[str] = None) -> None:
    if role not in ROLES:
        raise ValueError("invalid role")
    check_password_policy(password)                       # AUD-1
    branch = (branch or "").strip() or None
    if branch and not branch_exists(con, branch):
        raise ValueError("ไม่พบสาขานี้")
    if role in PINNED_ROLES and not branch:
        raise ValueError("บัญชีสาขาและบัญชีเจ้าของสาขาต้องเลือกสาขา")
    h = hash_password(password)
    con.execute(
        "INSERT INTO app_user(username, pw_hash, pw_salt, role, must_change, branch) VALUES (?,?,?,?,1,?)",
        (username.strip(), h["hash"], h["salt"], role, branch),   # new accounts must set their own password
    )
    con.commit()


UNSET = object()


def update_user(con: sqlite3.Connection, username: str, *, role: Optional[str] = None,
                password: Optional[str] = None, active: Optional[bool] = None, branch=UNSET,
                display_name=UNSET) -> None:
    sets, vals = [], []
    if display_name is not UNSET:
        sets.append("display_name = ?"); vals.append((display_name or "").strip() or None)
    if branch is not UNSET:
        b = (branch or "").strip() or None
        if b and not branch_exists(con, b):
            raise ValueError("ไม่พบสาขานี้")
        sets.append("branch = ?"); vals.append(b)
    if role is not None:
        if role not in ROLES:
            raise ValueError("invalid role")
        sets.append("role = ?"); vals.append(role)
    if password:
        check_password_policy(password)                   # AUD-1
        h = hash_password(password)
        # An admin reset hands the user a temporary password → force a change.
        sets += ["pw_hash = ?", "pw_salt = ?", "must_change = 1"]; vals += [h["hash"], h["salt"]]
    if active is not None:
        sets.append("active = ?"); vals.append(1 if active else 0)
    if not sets:
        return
    cur = get_user(con, username)
    final_role = role if role is not None else (cur["role"] if cur else None)
    final_branch = (b if branch is not UNSET else (cur["branch"] if cur else None))
    if final_role in PINNED_ROLES and not final_branch:
        raise ValueError("บัญชีสาขาและบัญชีเจ้าของสาขาต้องเลือกสาขา")
    vals.append(username)
    con.execute(f"UPDATE app_user SET {', '.join(sets)} WHERE username = ?", vals)
    con.commit()


def change_own_password(con: sqlite3.Connection, username: str,
                        old_password: str, new_password: str) -> bool:
    """Self-service change: verify the caller's CURRENT password, then set the new
    one and clear must_change. Returns False if the current password is wrong."""
    row = get_user(con, username)
    if not row or not row["active"] \
            or not verify_password(old_password or "", row["pw_hash"], row["pw_salt"]):
        return False                                       # a deactivated login cannot change it either
    check_password_policy(new_password)                   # AUD-1
    h = hash_password(new_password)
    con.execute(
        "UPDATE app_user SET pw_hash = ?, pw_salt = ?, must_change = 0 WHERE username = ?",
        (h["hash"], h["salt"], username),
    )
    con.commit()
    return True


def delete_user(con: sqlite3.Connection, username: str) -> None:
    con.execute("DELETE FROM app_user WHERE username = ?", (username,))
    con.commit()


def authenticate(con: sqlite3.Connection, username: str, password: str) -> Optional[sqlite3.Row]:
    """Return the user row on success, else None. Inactive users can't log in."""
    row = get_user(con, (username or "").strip())
    if not row or not row["active"]:
        return None
    if not verify_password(password or "", row["pw_hash"], row["pw_salt"]):
        return None
    return row


# ---------------------------------------------------------------------------
# Login throttle — in-memory, per (ip, username). Predictable usernames + a
# shared default make online brute force the real risk; pbkdf2 only slows
# OFFLINE cracking. After _MAX_FAILS failures within the cooldown, reject
# without even checking the password. Per-process is plenty for a single Fly
# instance serving ~12 users.
# ---------------------------------------------------------------------------
# Two counters share the dict: (ip, username) and ("*", username). The second caps
# the failures on ONE account from every address together, so rotating addresses
# does not buy more guesses. The dict is pruned of expired entries and capped at
# _LOGIN_MAX_KEYS so a flood of junk usernames cannot grow it without bound.
_LOGIN_FAILS: Dict[tuple, List[float]] = {}
_LOGIN_MAX_FAILS = 5
_LOGIN_USER_MAX_FAILS = 10  # per account, all addresses together, within the same window
_LOGIN_COOLDOWN = 300.0     # seconds; also the sliding window for counting fails
_LOGIN_MAX_KEYS = 5000


def client_ip(request) -> str:
    """The caller's address for rate limits and the audit log. Fly's edge proxy sets
    Fly-Client-IP itself (a value a client sends is replaced), so it is trusted; the
    first X-Forwarded-For hop is whatever the client typed and is NOT used. Without
    the Fly header (local dev, tests) the socket peer is the answer."""
    try:
        fly = (request.headers.get("fly-client-ip") or "").strip()
    except Exception:
        fly = ""
    if fly:
        return fly
    client = getattr(request, "client", None)
    return (getattr(client, "host", None) or "?") if client else "?"


def _throttle_key(ip: str, username: str) -> tuple:
    return (ip or "?", (username or "").strip().lower())


def _user_key(username: str) -> tuple:
    return ("*", (username or "").strip().lower())


def _recent(key: tuple, now: float) -> List[float]:
    fails = [t for t in _LOGIN_FAILS.get(key, []) if now - t < _LOGIN_COOLDOWN]
    if fails:
        _LOGIN_FAILS[key] = fails
    else:
        _LOGIN_FAILS.pop(key, None)
    return fails


def _prune_login_fails(now: float) -> None:
    for key in list(_LOGIN_FAILS):
        _recent(key, now)
    if len(_LOGIN_FAILS) > _LOGIN_MAX_KEYS:
        # still too many live keys: drop the ones whose last failure is oldest
        by_age = sorted(_LOGIN_FAILS, key=lambda k: _LOGIN_FAILS[k][-1])
        for key in by_age[:len(_LOGIN_FAILS) - _LOGIN_MAX_KEYS]:
            _LOGIN_FAILS.pop(key, None)


def login_throttled(ip: str, username: str) -> bool:
    """True if this (ip, username) has hit the failure ceiling within the cooldown,
    or the account itself has, counted over every address."""
    now = time.time()
    if len(_recent(_throttle_key(ip, username), now)) >= _LOGIN_MAX_FAILS:
        return True
    return len(_recent(_user_key(username), now)) >= _LOGIN_USER_MAX_FAILS


def record_login_failure(ip: str, username: str) -> None:
    now = time.time()
    for key in (_throttle_key(ip, username), _user_key(username)):
        lst = _LOGIN_FAILS.setdefault(key, [])
        lst.append(now)
        del lst[:-max(_LOGIN_MAX_FAILS, _LOGIN_USER_MAX_FAILS) * 2]   # a list never outgrows the ceiling
    if len(_LOGIN_FAILS) > _LOGIN_MAX_KEYS:
        _prune_login_fails(now)


def clear_login_failures(ip: str, username: str) -> None:
    _LOGIN_FAILS.pop(_throttle_key(ip, username), None)
    _LOGIN_FAILS.pop(_user_key(username), None)
