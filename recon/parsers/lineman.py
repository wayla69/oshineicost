"""
LINE MAN Wongnai for Business – Order/Financial Report parser.

Download from: LINE MAN Partner Portal → รายงาน → รายละเอียดคำสั่งซื้อ
                                        or → รายงานการชำระเงิน

Expected columns (Thai headers common):
  วันที่สั่งอาหาร | หมายเลขคำสั่งซื้อ | ชื่อร้านอาหาร | สถานะ |
  ราคาอาหาร | ค่าจัดส่ง (paid by customer, not restaurant revenue) |
  ค่าคอมมิชชั่น | VAT (7% on commission) | ส่วนลดที่ LINE MAN รับผิดชอบ |
  ยอดเงินสุทธิที่ร้านได้รับ | วันที่ชำระเงิน
"""

from __future__ import annotations
import io
from .bank import _to_float, _parse_thai_date, _blank, _cell
import openpyxl


_DATE_COLS     = ["วันที่สั่งอาหาร", "order date", "date", "วันที่"]
_ORDERID_COLS  = ["หมายเลขคำสั่งซื้อ", "order id", "order_id", "หมายเลข"]
_STATUS_COLS   = ["สถานะ", "status", "order status"]
_MERCHANT_COLS = ["ชื่อร้านอาหาร", "ชื่อร้าน", "outlet", "merchant"]
_FOOD_COLS     = ["ราคาอาหาร", "food price", "food amount", "ราคา"]
_DELIVERY_COLS = ["ค่าจัดส่ง", "delivery fee", "shipping"]
_COMM_RATE     = ["อัตราค่าคอมมิชชั่น", "commission rate", "comm rate"]
_COMM_AMT      = ["ค่าคอมมิชชั่น", "commission", "comm amount"]
_VAT_COLS      = ["vat", "ภาษีมูลค่าเพิ่ม", "ภาษี"]
_PROMO_COLS    = ["ส่วนลด", "โปรโมชั่น", "promotion", "promo"]
_NET_COLS      = ["ยอดเงินสุทธิ", "net payout", "net amount", "ยอดสุทธิ"]
_PAYOUT_COLS   = ["วันที่ชำระเงิน", "payout date", "payment date", "วันที่โอน"]


# ── Balance Report (LINE MAN settlement export, English headers) ──────────────
# A different LINE MAN export than the Thai-header order report above. One file
# per day, columns:
#   business_id | store_id | restaurant_name | branch_name | order_id |
#   capture_time(date) | capture_time(time) | payment_method |
#   cash_amount | e_payment_amount | fee | other_cost | vat | net_amount
# fee / other_cost / vat arrive NEGATIVE (deductions). The settlement identity is
#   cash + e_payment + fee + other_cost + vat = net_amount
# so gross order value = cash + e_payment and net_amount is what LINE MAN settles
# to the bank. This is the financial report that reconciles against the deposit.
_BALANCE_SIG = ("e_payment_amount", "net_amount", "business_id")


def _header_index(header_row) -> dict[str, int]:
    """Map normalised header text → column index for an English Balance Report."""
    idx: dict[str, int] = {}
    for j, c in enumerate(header_row):
        key = str(c).strip().lower() if c is not None else ""
        if key and key not in idx:
            idx[key] = j
    return idx


def _is_balance_report(header_row) -> bool:
    keys = {str(c).strip().lower() for c in header_row if c is not None}
    return all(sig in keys for sig in _BALANCE_SIG)


def _parse_balance_report(rows: list, header_row: int) -> tuple[list[dict], int]:
    idx = _header_index(rows[header_row])

    def col(*names):
        for n in names:
            if n in idx:
                return idx[n]
        return None

    c_date     = col("capture_time", "capture_t") or next(
        (j for k, j in idx.items() if k.startswith("capture")), None)
    c_order    = col("order_id")
    c_merchant = col("restaurant_name", "branch_name")
    c_pay      = col("payment_method")
    c_cash     = col("cash_amount")
    c_epay     = col("e_payment_amount")
    c_fee      = col("fee")
    c_other    = col("other_cost")
    c_vat      = col("vat")
    c_net      = col("net_amount")

    results, skipped = [], 0
    for row in rows[header_row + 1:]:
        raw_date = row[c_date] if c_date is not None and c_date < len(row) else None
        order_date = _parse_thai_date(raw_date)
        if not order_date:
            if not _blank(raw_date):
                skipped += 1
            continue

        def g(c):
            return _to_float(row[c]) if c is not None and c < len(row) else 0.0

        cash, epay = g(c_cash), g(c_epay)
        fee, other, vat, net = g(c_fee), g(c_other), g(c_vat), g(c_net)
        gross = cash + epay
        results.append({
            "order_date":        order_date,
            "order_id":          str(row[c_order] or "").strip() if c_order is not None else "",
            "merchant_name":     str(row[c_merchant] or "").strip() if c_merchant is not None else "",
            "order_status":      "Refunded" if net < 0 else "Completed",
            "food_amount":       gross,
            "delivery_fee":      0.0,
            "commission_rate":   round(abs(fee) / gross, 4) if gross else 0.0,
            "commission_amt":    abs(fee),          # fee arrives negative
            "vat_on_commission": abs(vat),          # VAT on the commission, negative
            "promo_deduct":      abs(other),        # other_cost deductions
            "net_payout":        net,
            "payout_date":       order_date,        # Balance Report = settled same period
        })
    return results, skipped


def parse_lineman_report(file_bytes: bytes) -> tuple[list[dict], int]:
    """
    Parse LINE MAN Wongnai financial/order report Excel. Accepts both the Thai-
    header order report and the English-header Balance Report (settlement export).
    Returns (rows, skipped) — rows match recon_lineman_lines columns; skipped
    counts data rows dropped for an unparseable order date.
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    # Balance Report: detect its English signature header anywhere in the first
    # few rows and parse positionally (gross = cash + e_payment, fee/vat negative).
    for i, row in enumerate(rows[:5]):
        if _is_balance_report(row):
            return _parse_balance_report(rows, i)

    col: dict[str, int] = {}
    header_row = None

    for i, row in enumerate(rows):
        texts = [str(c).strip().lower() if c else "" for c in row]
        for j, t in enumerate(texts):
            if any(k.lower() in t for k in _DATE_COLS):     col.setdefault("date", j)
            if any(k.lower() in t for k in _ORDERID_COLS):  col.setdefault("order_id", j)
            if any(k.lower() in t for k in _STATUS_COLS):   col.setdefault("status", j)
            if any(k.lower() in t for k in _MERCHANT_COLS): col.setdefault("merchant", j)
            if any(k.lower() in t for k in _FOOD_COLS):     col.setdefault("food", j)
            if any(k.lower() in t for k in _DELIVERY_COLS): col.setdefault("delivery", j)
            # 'commission rate' contains 'commission' (amount): a rate column
            # is never the commission amount.
            if any(k.lower() in t for k in _COMM_RATE):     col.setdefault("comm_rate", j)
            elif any(k.lower() in t for k in _COMM_AMT):    col.setdefault("comm_amt", j)
            if any(k.lower() in t for k in _VAT_COLS):      col.setdefault("vat", j)
            if any(k.lower() in t for k in _PROMO_COLS):    col.setdefault("promo", j)
            if any(k.lower() in t for k in _NET_COLS):      col.setdefault("net", j)
            if any(k.lower() in t for k in _PAYOUT_COLS):   col.setdefault("payout_date", j)
        if "date" in col:
            header_row = i
            break

    if header_row is None:
        raise ValueError("Lineman parser: could not find header row (date column missing)")

    results, skipped = [], 0
    for row in rows[header_row + 1:]:
        raw_date = row[col.get("date", 0)]
        order_date = _parse_thai_date(raw_date)
        if not order_date:
            if not _blank(raw_date):
                skipped += 1
            continue

        # A column the header lacks reads as empty — never as column 0.
        food_amt = _to_float(_cell(row, col, "food"))
        comm_amt = _to_float(_cell(row, col, "comm_amt"))
        vat_amt  = _to_float(_cell(row, col, "vat"))
        promo    = _to_float(_cell(row, col, "promo"))
        net      = _to_float(_cell(row, col, "net")) if "net" in col else food_amt - comm_amt - vat_amt - promo

        status_raw = str(_cell(row, col, "status") or "").strip().lower()
        if "ยกเลิก" in status_raw or "cancel" in status_raw:
            order_status = "Cancelled"
        else:
            order_status = "Completed"

        results.append({
            "order_date":        order_date,
            "order_id":          str(_cell(row, col, "order_id") or "").strip(),
            "merchant_name":     str(_cell(row, col, "merchant") or "").strip(),
            "order_status":      order_status,
            "food_amount":       food_amt,
            "delivery_fee":      _to_float(_cell(row, col, "delivery")),
            "commission_rate":   _to_float(_cell(row, col, "comm_rate")),
            "commission_amt":    comm_amt,
            "vat_on_commission": vat_amt,
            "promo_deduct":      promo,
            "net_payout":        net,
            "payout_date":       _parse_thai_date(_cell(row, col, "payout_date")),
        })

    return results, skipped
