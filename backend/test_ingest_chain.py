"""Ingest-chain golden tests: the cleansed-raw upload path (upload_data →
reingest_raw_2026 loaders) against tiny synthetic .xlsx files + a hermetic
SQLite, and the pure Thai date/number/period helpers in ingest_easy.

The web /api/upload round-trip lives in test_write_paths.py; these tests hit
the same engines directly so a parser/loader regression is localised here
instead of surfacing as an opaque HTTP 500.
"""
import sqlite3
import sys
from pathlib import Path

import openpyxl
import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import ingest_easy as ie                 # noqa: E402
import reingest_raw_2026 as rr           # noqa: E402
import upload_data as ud                 # noqa: E402
from ingest_may26_update import _remap_branches  # noqa: E402


# ── ingest_easy pure helpers ─────────────────────────────────────────────────
@pytest.mark.parametrize("s,expected", [
    ("1,234.50", 1234.5), (".00", 0.0), ("", 0.0), ("-", 0.0),
    ("garbage", 0.0), ("  42 ", 42.0),
])
def test_parse_num(s, expected):
    assert ie.parse_num(s) == expected


@pytest.mark.parametrize("s,expected", [("1,234", 1234), ("12.9", 12), ("x", 0)])
def test_parse_int(s, expected):
    assert ie.parse_int(s) == expected


def test_thai_buddhist_era_dates():
    # BE 2568 = CE 2025; Thai month names
    assert ie.be_to_ce_year(2568) == 2025
    assert ie.parse_thai_date("23 พฤษภาคม 2568 13:11:57") == "2025-05-23T13:11:57"
    assert ie.parse_thai_date("23 พฤษภาคม 2568") == "2025-05-23T00:00:00"
    # slash format D/M/YYYY with BE year
    assert ie.parse_thai_date_slash("1/4/2569 12:24:01") == "2026-04-01T12:24:01"
    # dispatcher picks the right parser
    assert ie.parse_date_any("1/4/2569") == "2026-04-01T00:00:00"
    assert ie.period_from_thai_date("23 พฤษภาคม 2568") == "2025-05"


def test_period_from_filenames():
    # Type A filename year is GREGORIAN, Type B is BUDDHIST ERA — the classic trap
    assert ie.period_from_filename_a("Easy_May 25") == "2025-05"
    assert ie.period_from_filename_a("Easy_Apr 26") == "2026-04"
    assert ie.period_from_filename_b("Easy May_68") == "2025-05"
    assert ie.period_from_filename_b("Easy Apr_69") == "2026-04"
    assert ie.period_from_filename_es1("ES1_May 25") == "2025-05"
    assert ie.period_from_filename_a("notafile") == ""
    assert ie.period_from_filename_b("Easy Xxx_68") == ""


def test_is_branch_marker():
    known = "อุบลราชธานี"
    assert ie.is_branch_marker([known, None, None]) == known
    # marker rows must have all other cells empty
    assert ie.is_branch_marker([known, 42]) == ""
    # sub-brand menu sections are NOT branches
    assert ie.is_branch_marker(["Oshinei หม้อและน้ำซุป", None]) == ""
    assert ie.is_branch_marker(["Oshinei SOMETHING NEW", None]) == ""
    # footer labels rejected; unknown Thai text accepted by heuristic
    assert ie.is_branch_marker(["Transection", None]) == ""
    assert ie.is_branch_marker(["สาขาใหม่ทดสอบ", None]) == "สาขาใหม่ทดสอบ"


# ── reingest_raw_2026 parsers + loaders ──────────────────────────────────────
def _write_xlsx(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["hdr"] * len(rows[0]))
    for r in rows:
        ws.append(r)
    wb.save(path)


def _db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript((HERE / "schema.sql").read_text(encoding="utf-8"))
    cur = con.cursor()
    cur.execute("INSERT INTO branches(branch_id,name) VALUES(1,'อุบลราชธานี')")
    cur.execute("INSERT INTO categories(category_id,name) VALUES(1,'A La Carte')")
    cur.execute("INSERT INTO skus(sku_id,sku_code,description,category_id) VALUES(1,'A0001','known dish',1)")
    con.commit()
    return con


def test_period_of_and_num():
    assert rr.period_of("ES1_Jun 25_raw") == "2025-06"
    assert rr.period_of("23 ES_June 25_raw") == "2025-06"
    assert rr.period_of("ES1_Aug  25_raw") == "2025-08"
    assert rr.period_of("nonsense") == ""
    assert rr._num("1,234.5") == 1234.5
    assert rr._num(None) == 0.0
    assert rr._num("x") == 0.0


def test_parse_and_load_es1_aggregates_and_is_idempotent(tmp_path):
    f = tmp_path / "ES1_Mar 26_raw.xlsx"
    # layout: [_, branch, code, desc, qty, net_ex, tax1, tax2, tax3, gross_inc]
    _write_xlsx(f, [
        [1, "อุบลราชธานี", "A0001", "known dish", 2, 200, 10, 2, 2, 214],
        [2, "อุบลราชธานี", "A0001", "known dish", 3, 300, 21, 0, 0, 321],   # same SKU → aggregated
        [3, "อุบลราชธานี", "NEW01", "brand new dish", 1, 50, 3.5, 0, 0, 53.5],
    ])
    parsed = rr.parse_es1(f, "2026-03")
    rec = parsed["branches"]["อุบลราชธานี"]["A0001"]
    assert rec[1] == 5 and rec[2] == 500 and rec[3] == 35 and rec[4] == 535

    con = _db()
    assert rr.load_es1(con, parsed, f.name) == 2
    rows = {r["sku_id"]: r for r in con.execute(
        "SELECT * FROM sales_sku_monthly WHERE period_month='2026-03'")}
    assert rows[1]["qty"] == 5 and rows[1]["subtotal_ex_vat"] == 500
    assert rows[1]["source"] == "Easy"
    # unknown SKU is created under the 'Unknown' category — never dropped
    new = con.execute("SELECT s.*, c.name cat FROM skus s JOIN categories c USING(category_id)"
                      " WHERE sku_code='NEW01'").fetchone()
    assert new and new["cat"] == "Unknown"
    # idempotent: re-loading the same month REPLACES, never double-counts
    rr.load_es1(con, parsed, f.name)
    q = con.execute("SELECT SUM(qty) q, COUNT(*) n FROM sales_sku_monthly"
                    " WHERE period_month='2026-03'").fetchone()
    assert q["n"] == 2 and q["q"] == 6


def test_parse_and_load_es_tx_builds_receipts_and_summary(tmp_path):
    f = tmp_path / "23 ES_Mar 26_raw.xlsx"
    # tx layout (see rr.parse_es_tx): [_, branch, sale_no, sold_at, cashier, table,
    #   cust, _, drink, food, tip, sp_disc, discount, svc, total_ex, _, total_inc, ...(21=other)]
    def row(sale_no, day, food, total_ex):
        r = [None] * 22
        r[1], r[2], r[3] = "อุบลราชธานี", sale_no, f"2026-03-{day:02d} 12:00:00"
        r[4], r[5], r[6] = "cashier", "T1", 4
        r[8], r[9] = 100, food
        r[11], r[12], r[13] = 0, 20, 50
        r[14], r[16], r[21] = total_ex, round(total_ex * 1.07, 2), 0
        return r
    # Sale_No repeats across days (resets daily) — receipts must still be unique
    _write_xlsx(f, [row(1, 1, 900, 1000), row(1, 2, 800, 900)])
    parsed = rr.parse_es_tx(f, "2026-03")
    txs = parsed["branches"]["อุบลราชธานี"]
    assert len({t["receipt_no"] for t in txs}) == 2

    con = _db()
    tx_n, summ_n = rr.load_es_tx(con, parsed, f.name)
    assert (tx_n, summ_n) == (2, 1)
    s = con.execute("SELECT * FROM sales_month_summary WHERE period_month='2026-03'").fetchone()
    assert s["transaction_count"] == 2 and s["customer_count"] == 8
    # net_sales must be EX-VAT (VAT is never revenue)
    assert s["net_sales"] == 1900.0
    assert s["discounts"] == 40.0
    # idempotent re-run
    rr.load_es_tx(con, parsed, f.name)
    n = con.execute("SELECT COUNT(*) n FROM sales_transaction"
                    " WHERE strftime('%Y-%m', sold_at)='2026-03'").fetchone()["n"]
    assert n == 2


# ── upload_data ──────────────────────────────────────────────────────────────
def test_classify_and_detect():
    assert ud.classify(Path("ES1_Jun 25_raw.xlsx")) == "es1"
    assert ud.classify(Path("23 ES_Jun 25_raw.xlsx")) == "tx"
    assert ud.classify(Path("ES_Jun 25_raw.xlsx")) == "tx"
    # production multi-section files (no _raw) must NOT match — different layout
    assert ud.classify(Path("ES1_May 25.xlsx")) == ""
    assert ud.classify(Path("ES1_Jun 25_raw.csv")) == ""
    assert ud.detect(Path("ES1_Jun 25_raw.xlsx")) == ("es1", "2025-06")


def test_day_coverage_flags_missing_last_day():
    parsed = {"branches": {"b": [
        {"sold_at": f"2026-04-{d:02d} 12:00:00"} for d in range(1, 30)  # 1..29, no 30th
    ]}}
    cov = ud._day_coverage(parsed, "2026-04")
    assert cov["expected_days"] == 30
    assert cov["present_days"] == 29
    assert cov["missing_days"] == ["30"]
    assert cov["last_day_missing"] is True
    assert ud._day_coverage({"branches": {}}, "not-a-period") is None


def test_ingest_one_dry_run_apply_and_branch_guard(tmp_path):
    f = tmp_path / "ES1_Mar 26_raw.xlsx"
    _write_xlsx(f, [[1, "อุบลราชธานี", "A0001", "known dish", 2, 200, 14, 0, 0, 214]])
    con = _db()
    # dry-run reports without writing
    rep = ud.ingest_one(con, f, apply=False)
    assert rep["kind"] == "monthly_sku" and rep["period"] == "2026-03" and not rep["applied"]
    assert con.execute("SELECT COUNT(*) n FROM sales_sku_monthly").fetchone()["n"] == 0
    # apply writes
    rep = ud.ingest_one(con, f, apply=True)
    assert rep["applied"] and rep["rows"] == 1
    assert con.execute("SELECT COUNT(*) n FROM sales_sku_monthly").fetchone()["n"] == 1
    # unknown branch refused unless explicitly allowed (duplicate-branch trap)
    g = tmp_path / "ES1_Apr 26_raw.xlsx"
    _write_xlsx(g, [[1, "สาขาปลอม", "A0001", "d", 1, 100, 7, 0, 0, 107]])
    with pytest.raises(ValueError, match="สาขาปลอม"):
        ud.ingest_one(con, g, apply=True)
    rep = ud.ingest_one(con, g, apply=True, allow_new_branch=True)
    assert rep["new_branches"] == ["สาขาปลอม"]
    # unrecognisable file is a clean error
    with pytest.raises(ValueError, match="Unrecognised"):
        ud.ingest_one(con, tmp_path / "random.xlsx", apply=False)


def test_remap_branches_merges_alias_duplicates():
    # two raw POS names that canonicalise to the same DB branch must merge
    full_names = [k for k, v in __import__("ingest_may26_update").BRANCH_ALIAS.items()]
    parsed = {"period": "2026-03", "branches": {
        "อุบลราชธานี": {"A0001": ["dish", 1.0, 100.0, 7.0, 107.0]},
    }}
    out = _remap_branches(parsed)
    assert out["branches"]["อุบลราชธานี"]["A0001"][1] == 1.0
    if full_names:
        # an aliased full name merges into the canonical short name
        full = full_names[0]
        short = __import__("ingest_may26_update").BRANCH_ALIAS[full]
        parsed = {"period": "2026-03", "branches": {
            full:  {"A0001": ["dish", 1.0, 100.0, 7.0, 107.0]},
            short: {"A0001": ["dish", 2.0, 200.0, 14.0, 214.0]},
        }}
        out = _remap_branches(parsed)
        assert list(out["branches"]) == [short]
        assert out["branches"][short]["A0001"][1] == 3.0    # qty merged
