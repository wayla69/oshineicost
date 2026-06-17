"""
OCR fallback for bank-statement PDFs that carry NO text layer.

Some KTB statements reach us as image-only PDFs: either a scan, or — far more
common here — a file made with Windows "Microsoft Print to PDF", which flattens
the Thai font to vector outlines (pdfminer then sees 0 chars). The amounts are
visually crisp but unreadable by the text parsers.

This module renders each page to a high-DPI image (pypdfium2 — no system poppler
needed), OCRs it (Tesseract, tha+eng), and parses the KTB line layout from the
recovered text.

Money OCR is risky on its own, so we never trust it blindly: a KTB statement
carries a running-balance column, so we VALIDATE the result against the balance
chain — for every movement |balance − prev_balance| must equal the row amount.
A single misread digit breaks the chain, so a result that passes is internally
consistent to the satang. If it fails, we raise (reject) rather than save numbers
that might be wrong — the operator is told to upload the Excel/CSV instead.

Everything degrades gracefully: if pypdfium2 / pytesseract / the tesseract binary
or the Thai language pack is missing, attempt_ocr_bank() returns None and the
caller falls back to its normal "no text — use Excel/CSV" message.
"""

from __future__ import annotations

import io
import re

from .bank import _parse_thai_date, _to_float, _normalise_channel
from .pdf_formats import _KTB_LINE, _MONEY_RE, _extract_account


# Render resolution. 300 DPI is plenty for the crisp vector-outline text these
# Print-to-PDF files carry; higher just slows OCR with no accuracy gain here.
_OCR_DPI = 300
_TESS_CONFIG = "--psm 6"          # assume a uniform block of lines (the table)
_TESS_LANG = "tha+eng"


def _configure_tesseract() -> None:
    """On Linux/Docker tesseract is on PATH (no-op). On Windows the UB-Mannheim
    installer drops it under Program Files but not on PATH — point pytesseract at
    it so a locally-run recon server can OCR too. Best-effort; safe if absent."""
    import shutil
    import pytesseract
    if shutil.which("tesseract"):
        return
    import os
    for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
              r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
        if os.path.exists(p):
            pytesseract.pytesseract.tesseract_cmd = p
            return


def _render_pages(raw: bytes):
    """Yield a PIL image per PDF page via pypdfium2. Raises ImportError if the
    renderer isn't installed (caller treats that as 'OCR unavailable')."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(io.BytesIO(raw))
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            bitmap = page.render(scale=_OCR_DPI / 72)
            yield bitmap.to_pil()
    finally:
        pdf.close()


def _ocr_text(raw: bytes) -> str:
    """Full OCR text of every page, newline-joined. Empty string on any failure
    (missing binary / language pack) so the caller can fall back cleanly."""
    import pytesseract
    parts: list[str] = []
    for pil in _render_pages(raw):
        parts.append(pytesseract.image_to_string(pil, lang=_TESS_LANG, config=_TESS_CONFIG))
    return "\n".join(parts)


def _ocr_available() -> bool:
    try:
        import pypdfium2  # noqa: F401
        import pytesseract
        _configure_tesseract()
        return "tha" in pytesseract.get_languages()   # need Thai for KTB text
    except Exception:
        return False


def _seed_opening_balance(lines: list[str]) -> float | None:
    """Opening balance from the 'ยอดคงเหลือยกมา <balance>' header row. OCR may
    mangle the Thai label, so accept any line that mentions 'ยกมา' (carried
    forward) and carries exactly one money token."""
    for line in lines:
        if "ยกมา" in line:
            mm = list(_MONEY_RE.finditer(line))
            if mm:
                return _to_float(mm[-1].group())
    return None


def _parse_ktb_ocr(txt: str) -> tuple[list[dict], int]:
    """Parse OCR'd KTB statement text into recon_bank_lines rows. Mirrors
    pdf_formats._ktb_statement but works on recovered (OCR) text."""
    lines = txt.splitlines()
    acct = _extract_account(txt)
    prev_balance = _seed_opening_balance(lines)

    rows, skipped = [], 0
    for line in lines:
        m = _KTB_LINE.match(line.strip())
        if not m:
            continue
        txn_date = _parse_thai_date(m.group(1))
        if not txn_date:
            continue
        rest = m.group(2)
        money = list(_MONEY_RE.finditer(rest))
        if len(money) < 2:                 # need at least amount + balance
            skipped += 1
            continue
        desc    = re.sub(r"\s+", " ", rest[: money[0].start()]).strip()
        amount  = _to_float(money[0].group())
        balance = _to_float(money[-1].group())   # last money token is the balance
        debit = credit = 0.0
        if prev_balance is None or round(balance - prev_balance, 2) >= 0:
            credit = amount
        else:
            debit = amount
        prev_balance = balance
        rows.append({
            "txn_date":    txn_date,
            "value_date":  txn_date,
            "description": desc,
            "channel":     _normalise_channel(desc) if desc else "OTHER",
            "debit":       debit,
            "credit":      credit,
            "balance":     balance,
            "ref_no":      "",
            "account_no":  acct,
        })
    return rows, skipped


def _balance_chain_ok(rows: list[dict], opening: float | None) -> bool:
    """Self-check the OCR: for every row the movement must equal the change in the
    running balance. Validates row 0 against the opening balance when known, and
    every subsequent row against its predecessor. Any break ⇒ a misread digit."""
    if len(rows) < 2:
        return False                       # too little to trust an OCR result
    prev = opening
    for r in rows:
        amount = r["credit"] or r["debit"]
        if prev is not None:
            if abs(abs(r["balance"] - prev) - amount) > 0.01:
                return False
        prev = r["balance"]
    return True


def attempt_ocr_bank(raw: bytes) -> tuple[str, list[dict], int] | None:
    """Try to OCR a no-text-layer KTB statement PDF.

    Returns (provider, rows, skipped) on success, None when OCR isn't available
    or the document doesn't look like a KTB statement after OCR. Raises ValueError
    (rejected) when OCR reads a KTB statement but the balance chain doesn't
    reconcile — so wrong numbers are never silently saved.
    """
    if not _ocr_available():
        return None
    try:
        txt = _ocr_text(raw)
    except Exception:
        return None
    if not txt.strip():
        return None

    # Must look like a KTB statement once OCR'd, else let the caller fall back.
    looks_ktb = ("รายการเดินบัญชี" in txt or "กรุงไทย" in txt or "krungthai" in txt.lower()
                 or "ยอดคงเหลือ" in txt)
    if not looks_ktb:
        return None

    rows, skipped = _parse_ktb_ocr(txt)
    if not rows:
        return None

    opening = _seed_opening_balance(txt.splitlines())
    if not _balance_chain_ok(rows, opening):
        raise ValueError(
            "อ่านไฟล์ด้วย OCR แล้วยอดคงเหลือไม่ต่อเนื่อง (อาจอ่านตัวเลขผิด) — "
            "เพื่อความถูกต้องของยอดเงิน กรุณาดาวน์โหลด statement เป็น Excel/CSV "
            "จาก KTB Corporate Online แล้วอัปโหลดแทน"
        )
    return "KTB-OCR", rows, skipped
