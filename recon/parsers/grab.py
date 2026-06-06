"""
Grab for Restaurants – Financial Report parser.

Download from: Grab Merchant Portal → Finance → Financial Reports → Transaction Report

Expected Excel columns (may vary by report version):
  Merchant ID | Outlet Name | Order Date | Order ID | Order Type |
  Order Status | Food Amount | Commission Rate | Commission Amount |
  Grab Promotion Deduction | Net Payout | Estimated Payout Date
"""

from __future__ import annotations
import io
from .bank import _to_float, _parse_thai_date, _blank
import openpyxl


_DATE_COLS    = ["order date", "วันที่สั่งซื้อ", "order_date"]
_ORDERID_COLS = ["order id", "หมายเลขคำสั่งซื้อ", "order_id"]
_STATUS_COLS  = ["order status", "สถานะ"]
_TYPE_COLS    = ["order type"]
_FOOD_COLS    = ["food amount", "ราคาอาหาร", "subtotal"]
_COMM_RATE    = ["commission rate", "อัตราค่าคอมมิชชั่น"]
_COMM_AMT     = ["commission amount", "ค่าคอมมิชชั่น"]
_PROMO_COLS   = ["grab promotion", "โปรโมชั่น", "promo deduction"]
_NET_COLS     = ["net payout", "ยอดสุทธิ", "net amount"]
_PAYOUT_COLS  = ["estimated payout", "วันที่โอนเงิน", "payout date"]
_MERCHANT_COLS = ["merchant id", "outlet name", "merchant name", "ชื่อร้าน"]


def parse_grab_report(file_bytes: bytes) -> tuple[list[dict], int]:
    """
    Parse Grab financial report Excel.
    Returns (rows, skipped) — rows match recon_grab_lines columns; skipped counts
    data rows dropped for an unparseable order date.
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    col: dict[str, int] = {}
    header_row = None

    for i, row in enumerate(rows):
        texts = [str(c).strip().lower() if c else "" for c in row]
        for j, t in enumerate(texts):
            if any(k in t for k in _DATE_COLS):    col.setdefault("date", j)
            if any(k in t for k in _ORDERID_COLS): col.setdefault("order_id", j)
            if any(k in t for k in _STATUS_COLS):  col.setdefault("status", j)
            if any(k in t for k in _TYPE_COLS):    col.setdefault("order_type", j)
            if any(k in t for k in _FOOD_COLS):    col.setdefault("food", j)
            if any(k in t for k in _COMM_RATE):    col.setdefault("comm_rate", j)
            if any(k in t for k in _COMM_AMT):     col.setdefault("comm_amt", j)
            if any(k in t for k in _PROMO_COLS):   col.setdefault("promo", j)
            if any(k in t for k in _NET_COLS):     col.setdefault("net", j)
            if any(k in t for k in _PAYOUT_COLS):  col.setdefault("payout_date", j)
            if any(k in t for k in _MERCHANT_COLS): col.setdefault("merchant", j)
        if "date" in col:
            header_row = i
            break

    if header_row is None:
        raise ValueError("Grab parser: could not find header row (order date column missing)")

    results, skipped = [], 0
    for row in rows[header_row + 1:]:
        raw_date = row[col["date"]] if "date" in col else None
        order_date = _parse_thai_date(raw_date)
        if not order_date:
            if not _blank(raw_date):
                skipped += 1
            continue

        food_amt = _to_float(row[col.get("food", 0)])
        comm_amt = _to_float(row[col.get("comm_amt", 0)])
        promo    = _to_float(row[col.get("promo", 0)])
        net      = _to_float(row[col.get("net", 0)]) if "net" in col else food_amt - comm_amt - promo

        status_raw = str(row[col.get("status", 0)] or "").strip().lower()
        if "cancel" in status_raw or "ยกเลิก" in status_raw:
            order_status = "Cancelled"
        elif "refund" in status_raw or "คืน" in status_raw:
            order_status = "Refunded"
        else:
            order_status = "Completed"

        results.append({
            "order_date":      order_date,
            "order_id":        str(row[col.get("order_id", 0)] or "").strip(),
            "merchant_name":   str(row[col.get("merchant", 0)] or "").strip(),
            "order_type":      str(row[col.get("order_type", 0)] or "GrabFood").strip(),
            "order_status":    order_status,
            "food_amount":     food_amt,
            "commission_rate": _to_float(row[col.get("comm_rate", 0)]),
            "commission_amt":  comm_amt,
            "promo_deduct":    promo,
            "net_payout":      net,
            "payout_date":     _parse_thai_date(row[col.get("payout_date", 0)]),
        })

    return results, skipped
