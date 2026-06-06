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
from .bank import _to_float, _parse_thai_date
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


def parse_lineman_report(file_bytes: bytes) -> list[dict]:
    """
    Parse LINE MAN Wongnai financial/order report Excel.
    Returns list of dicts matching recon_lineman_lines columns.
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

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
            if any(k.lower() in t for k in _COMM_RATE):     col.setdefault("comm_rate", j)
            if any(k.lower() in t for k in _COMM_AMT):      col.setdefault("comm_amt", j)
            if any(k.lower() in t for k in _VAT_COLS):      col.setdefault("vat", j)
            if any(k.lower() in t for k in _PROMO_COLS):    col.setdefault("promo", j)
            if any(k.lower() in t for k in _NET_COLS):      col.setdefault("net", j)
            if any(k.lower() in t for k in _PAYOUT_COLS):   col.setdefault("payout_date", j)
        if "date" in col:
            header_row = i
            break

    if header_row is None:
        raise ValueError("Lineman parser: could not find header row (date column missing)")

    results = []
    for row in rows[header_row + 1:]:
        order_date = _parse_thai_date(row[col.get("date", 0)])
        if not order_date:
            continue

        food_amt = _to_float(row[col.get("food", 0)])
        comm_amt = _to_float(row[col.get("comm_amt", 0)])
        vat_amt  = _to_float(row[col.get("vat", 0)])
        promo    = _to_float(row[col.get("promo", 0)])
        net      = _to_float(row[col.get("net", 0)]) if "net" in col else food_amt - comm_amt - vat_amt - promo

        status_raw = str(row[col.get("status", 0)] or "").strip().lower()
        if "ยกเลิก" in status_raw or "cancel" in status_raw:
            order_status = "Cancelled"
        else:
            order_status = "Completed"

        results.append({
            "order_date":        order_date,
            "order_id":          str(row[col.get("order_id", 0)] or "").strip(),
            "merchant_name":     str(row[col.get("merchant", 0)] or "").strip(),
            "order_status":      order_status,
            "food_amount":       food_amt,
            "delivery_fee":      _to_float(row[col.get("delivery", 0)]),
            "commission_rate":   _to_float(row[col.get("comm_rate", 0)]),
            "commission_amt":    comm_amt,
            "vat_on_commission": vat_amt,
            "promo_deduct":      promo,
            "net_payout":        net,
            "payout_date":       _parse_thai_date(row[col.get("payout_date", 0)]),
        })

    return results
