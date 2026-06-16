"""
Credit card settlement parsers.

Thai banks' EDC settlement reports are downloaded from merchant portals.
Returns list of dicts matching recon_cc_lines columns.

Supported:
  - KBank Merchant (KBank Bizlink / e-EDC)
  - SCB Merchant (SCB Business Net)
  - Generic (column-name detection fallback)
"""

from __future__ import annotations
import io
import openpyxl
from .bank import _to_float, _parse_thai_date, _blank


def _detect_card_type(raw: str) -> str:
    raw = raw.upper()
    if "VISA" in raw:       return "VISA"
    if "MASTER" in raw:     return "MASTERCARD"
    if "AMEX" in raw:       return "AMEX"
    if "JCB" in raw:        return "JCB"
    if "UNION" in raw:      return "UNIONPAY"
    return "OTHER"


def _parse_kbank_merchant(ws: openpyxl.worksheet.worksheet.Worksheet) -> list[dict]:
    """
    KBank Merchant settlement report.
    Headers: Settlement Date | Terminal ID | Card Type | Transaction Count |
             Sale Amount | Refund Amount | MDR Rate | MDR Fee | Net Amount
    """
    rows = list(ws.iter_rows(values_only=True))
    col: dict[str, int] = {}
    header_row = None

    for i, row in enumerate(rows):
        texts = [str(c).strip().lower() if c else "" for c in row]
        for j, t in enumerate(texts):
            if "settlement" in t and "date" in t: col["date"] = j
            elif "terminal" in t:                 col.setdefault("terminal", j)
            elif "merchant" in t:                 col.setdefault("merchant", j)
            elif "card type" in t or "ประเภทบัตร" in t: col.setdefault("card_type", j)
            elif "count" in t or "จำนวน" in t:   col.setdefault("count", j)
            elif "sale" in t and "amount" in t:   col.setdefault("gross", j)
            elif "refund" in t:                   col.setdefault("refund", j)
            elif "mdr rate" in t or "อัตรา" in t: col.setdefault("mdr_rate", j)
            elif "mdr fee" in t or "ค่าธรรมเนียม" in t: col.setdefault("mdr_fee", j)
            elif "net" in t:                      col.setdefault("net", j)
            elif "batch" in t:                    col.setdefault("batch", j)
        if "date" in col:
            header_row = i
            break

    if header_row is None:
        raise ValueError("KBank Merchant: header row not found")

    results, skipped = [], 0
    for row in rows[header_row + 1:]:
        raw_date = row[col["date"]]
        d = _parse_thai_date(raw_date)
        if not d:
            if not _blank(raw_date):
                skipped += 1
            continue
        results.append({
            "settlement_date": d,
            "card_type":       _detect_card_type(str(row[col.get("card_type", 0)] or "")),
            "txn_count":       int(_to_float(row[col.get("count", 0)])),
            "gross_amount":    _to_float(row[col.get("gross", 0)]),
            "refund_amount":   _to_float(row[col.get("refund", 0)]),
            "mdr_fee":         _to_float(row[col.get("mdr_fee", 0)]),
            "net_amount":      _to_float(row[col.get("net", 0)]),
            "terminal_id":     str(row[col.get("terminal", 0)] or "").strip(),
            "merchant_id":     str(row[col.get("merchant", 0)] or "").strip(),
            "batch_no":        str(row[col.get("batch", 0)] or "").strip(),
        })
    return results, skipped


def _parse_krungsri_rdb003(ws: openpyxl.worksheet.worksheet.Worksheet):
    """Krungsri (BAY / Bank of Ayudhya) acquirer settlement — the 'RDB003' report.

    Layout: ~10 metadata rows ('Entity Name : KRUNGSRI', 'Bank Channel : KBANK'),
    then a header row whose columns include 'Amount Finance', 'MSF Amt', a 'VAT
    Amt' immediately after it, and 'Net Payment'. One transaction per data row.

    We aggregate per Value Date (the payout date that lands in the bank account),
    summing gross / fee / VAT / net, so each output row is a daily settlement that
    reconciles against the KBANK bank credit. Returns (rows, skipped)."""
    from collections import defaultdict

    grid = list(ws.iter_rows(values_only=True))
    col: dict[str, int] = {}
    header_row = None

    for i, row in enumerate(grid):
        texts = [str(c).strip().lower() if c is not None else "" for c in row]
        joined = " ".join(texts)
        if "net payment" in joined and any("msf amt" in t for t in texts):
            for j, t in enumerate(texts):
                if t == "value date":          col["value_date"] = j
                elif t == "transaction date":  col["txn_date"] = j
                elif t == "mid":               col["mid"] = j
                elif "amount finance" in t:    col["gross"] = j
                elif t.startswith("msf amt"):  col["msf"] = j
                elif t == "net payment":       col["net"] = j
                elif t == "card no":           col["card"] = j
            # The MSF VAT is the first 'VAT Amt' AFTER the MSF Amt column
            # (a second 'VAT Amt' later belongs to the ETL fee — not ours).
            if "msf" in col:
                for j in range(col["msf"] + 1, len(texts)):
                    if "vat amt" in texts[j]:
                        col["vat"] = j
                        break
            header_row = i
            break

    date_col = col.get("value_date", col.get("txn_date"))
    if header_row is None or "net" not in col or "gross" not in col or date_col is None:
        raise ValueError("Krungsri RDB003: header row not found")

    agg: dict[str, dict] = defaultdict(
        lambda: {"gross": 0.0, "fee": 0.0, "vat": 0.0, "net": 0.0, "count": 0, "mid": ""})
    skipped = 0
    for row in grid[header_row + 1:]:
        raw_date = row[date_col] if date_col < len(row) else None
        d = _parse_thai_date(raw_date)
        if not d:
            if not _blank(raw_date):
                skipped += 1
            continue
        a = agg[d]
        a["gross"] += _to_float(row[col["gross"]])
        a["net"]   += _to_float(row[col["net"]])
        if "msf" in col:
            a["fee"] += _to_float(row[col["msf"]])
        if "vat" in col:
            a["vat"] += _to_float(row[col["vat"]])
        a["count"] += 1
        if not a["mid"] and "mid" in col:
            a["mid"] = str(row[col["mid"]] or "").strip()

    results = []
    for d in sorted(agg):
        a = agg[d]
        results.append({
            "settlement_date": d,
            "card_type":       "OTHER",
            "txn_count":       a["count"],
            "gross_amount":    round(a["gross"], 2),
            "refund_amount":   0.0,
            "mdr_fee":         round(a["fee"], 2),
            "vat_amount":      round(a["vat"], 2),
            "net_amount":      round(a["net"], 2),
            "acquirer":        "BAY",
            "terminal_id":     "",
            "merchant_id":     a["mid"],
            "batch_no":        "",
        })
    return results, skipped


def _parse_generic_cc(ws: openpyxl.worksheet.worksheet.Worksheet) -> list[dict]:
    """Fallback: detect columns by keyword for any CC settlement format."""
    rows = list(ws.iter_rows(values_only=True))
    col: dict[str, int] = {}
    header_row = None

    date_kws   = ["settlement date", "settle date", "วันที่ settle", "วันที่"]
    gross_kws  = ["sale amount", "gross", "ยอดขาย", "total sale"]
    refund_kws = ["refund", "คืนเงิน"]
    fee_kws    = ["mdr fee", "fee", "ค่าธรรมเนียม"]
    net_kws    = ["net amount", "net", "ยอดสุทธิ"]

    for i, row in enumerate(rows):
        texts = [str(c).strip().lower() if c else "" for c in row]
        for j, t in enumerate(texts):
            if any(k in t for k in date_kws):   col.setdefault("date", j)
            if any(k in t for k in gross_kws):  col.setdefault("gross", j)
            if any(k in t for k in refund_kws): col.setdefault("refund", j)
            if any(k in t for k in fee_kws):    col.setdefault("mdr_fee", j)
            if any(k in t for k in net_kws):    col.setdefault("net", j)
            if "card" in t:                     col.setdefault("card_type", j)
            if "count" in t or "จำนวน" in t:   col.setdefault("count", j)
            if "terminal" in t:                 col.setdefault("terminal", j)
        if "date" in col:
            header_row = i
            break

    if header_row is None:
        raise ValueError("CC generic: header row not found")

    results, skipped = [], 0
    for row in rows[header_row + 1:]:
        raw_date = row[col["date"]]
        d = _parse_thai_date(raw_date)
        if not d:
            if not _blank(raw_date):
                skipped += 1
            continue
        gross  = _to_float(row[col.get("gross", 0)])
        refund = _to_float(row[col.get("refund", 0)])
        fee    = _to_float(row[col.get("mdr_fee", 0)])
        net    = _to_float(row[col.get("net", 0)]) if "net" in col else gross - refund - fee
        results.append({
            "settlement_date": d,
            "card_type":  _detect_card_type(str(row[col.get("card_type", 0)] or "")),
            "txn_count":  int(_to_float(row[col.get("count", 0)])),
            "gross_amount":  gross,
            "refund_amount": refund,
            "mdr_fee":       fee,
            "net_amount":    net,
            "terminal_id":   str(row[col.get("terminal", 0)] or "").strip(),
            "merchant_id":   "",
            "batch_no":      "",
        })
    return results, skipped


def parse_cc_settlement(
    file_bytes: bytes,
    provider: str | None = None,
) -> tuple[str, list[dict], int]:
    """
    Parse a credit card settlement Excel file.
    Returns (provider, rows, skipped).
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active

    # Detect provider from file header rows. Scan deep enough (15 rows) to catch
    # both the Krungsri marker ('Entity Name : KRUNGSRI', row ~5) and the RDB003
    # column header ('MSF Amt' / 'Net Payment', row ~11).
    detected = provider or "generic"
    header_text = " ".join(
        str(c).lower()
        for row in ws.iter_rows(min_row=1, max_row=15, values_only=True)
        for c in row if c
    )
    # The RDB003 column signature is unmistakable — let it win over everything
    # (the file also says 'Bank Channel : KBANK', which must NOT route to kbank).
    sig_rdb003 = "msf amt" in header_text and "net payment" in header_text
    if sig_rdb003:
        detected = "krungsri"
    elif not provider:
        if "krungsri" in header_text or "ayudhya" in header_text:
            detected = "krungsri"
        elif "kasikorn" in header_text or "kbank" in header_text:
            detected = "kbank"
        elif "siam commercial" in header_text or "scb" in header_text:
            detected = "scb"

    try:
        if detected == "krungsri":
            rows, skipped = _parse_krungsri_rdb003(ws)
            detected = "Krungsri"        # display label; rows carry acquirer=BAY
        elif detected == "kbank":
            rows, skipped = _parse_kbank_merchant(ws)
        else:
            rows, skipped = _parse_generic_cc(ws)
    except ValueError:
        rows, skipped = _parse_generic_cc(ws)

    return detected, rows, skipped
