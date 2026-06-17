"""
Input normaliser for recon uploads.

Every domain parser (bank / credit_card / grab / lineman) reads its data through
``openpyxl.load_workbook(BytesIO(file_bytes))`` and then sniffs cell text for
headers / provider keywords. To accept formats other than Excel we therefore do
NOT touch the parsers — we convert the incoming bytes into an in-memory .xlsx
workbook with the same tabular layout and hand those bytes to the existing
parser. Header detection, provider sniffing and date parsing then "just work".

Supported inputs:
  - .xlsx         → passed through unchanged (parsers already read these)
  - .xls          → legacy BIFF converted to .xlsx (openpyxl can't read BIFF)
  - .csv          → decoded (Thai-aware) and laid out cell-for-cell
  - .pdf          → tables extracted via pdfplumber; text-line fallback

PDF extraction is best-effort: bank statement layouts vary, so the upload UI
always shows a preview that the user must eyeball before confirming.
"""

from __future__ import annotations

import csv
import io
from typing import Iterable

import openpyxl


# Encodings to try, in order. cp874 (a.k.a. TIS-620 / "Thai") is what Windows
# and most Thai bank portals emit; utf-8-sig handles a BOM from Excel exports.
_CSV_ENCODINGS = ("utf-8-sig", "utf-8", "cp874", "tis-620", "latin-1")


def _rows_to_xlsx(rows: Iterable[Iterable]) -> bytes:
    """Write a sequence of cell-rows into a fresh single-sheet .xlsx and return
    its bytes. Values are written verbatim — the domain parsers coerce types
    themselves (``_to_float`` / ``_parse_thai_date`` accept strings)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── CSV ─────────────────────────────────────────────────────────────────────

def _decode_csv(raw: bytes) -> str:
    for enc in _CSV_ENCODINGS:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    # Last resort: never raise on decode — replace undecodable bytes so the
    # user at least gets a preview to judge.
    return raw.decode("utf-8", errors="replace")


def _csv_to_xlsx(raw: bytes) -> bytes:
    text = _decode_csv(raw)
    # Sniff the delimiter (Thai exports sometimes use ';' or tab). Fall back to
    # comma when the sniffer can't decide on a short/odd sample.
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [r for r in reader]
    if not rows:
        raise ValueError("CSV file contained no rows.")
    return _rows_to_xlsx(rows)


# ── Legacy .xls (BIFF) ───────────────────────────────────────────────────────

def _xls_to_xlsx(raw: bytes) -> bytes:
    """Convert a legacy BIFF .xls into .xlsx bytes. openpyxl only reads the
    modern OOXML .xlsx, so old portal exports (e.g. Krungsri RDB003 settlements)
    must be transcoded via xlrd first. Date cells are emitted as ISO strings so
    the domain parsers' ``_parse_thai_date`` reads them unchanged."""
    try:
        import xlrd
    except ImportError as exc:                           # pragma: no cover
        raise ValueError(
            "Legacy .xls support requires the 'xlrd' package (pip install xlrd)."
        ) from exc

    book = xlrd.open_workbook(file_contents=raw)
    sh = book.sheet_by_index(0)
    rows: list[list] = []
    for r in range(sh.nrows):
        out_row = []
        for c in range(sh.ncols):
            v = sh.cell_value(r, c)
            if sh.cell_type(r, c) == xlrd.XL_CELL_DATE:
                try:
                    v = xlrd.xldate_as_datetime(v, book.datemode).strftime("%Y-%m-%d")
                except (ValueError, OverflowError):
                    pass
            out_row.append(v)
        rows.append(out_row)
    if not rows:
        raise ValueError("The .xls file contained no rows.")
    return _rows_to_xlsx(rows)


# ── PDF ─────────────────────────────────────────────────────────────────────

def _text_lines_to_rows(page) -> list[list[str]]:
    """Fallback when a page has no ruled table: rebuild a grid from word
    positions. Words on the same line (similar `top`) become one row; large
    horizontal gaps become column breaks."""
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    if not words:
        return []
    # Group words into visual lines by their vertical position.
    lines: dict[int, list] = {}
    for w in words:
        key = round(w["top"] / 3.0)        # ~3pt bucket tolerates baseline jitter
        lines.setdefault(key, []).append(w)

    out: list[list[str]] = []
    for key in sorted(lines):
        ws = sorted(lines[key], key=lambda w: w["x0"])
        cells, cur = [], ws[0]["text"]
        prev_end = ws[0]["x1"]
        for w in ws[1:]:
            # A gap wider than ~12pt starts a new column; otherwise same cell.
            if w["x0"] - prev_end > 12:
                cells.append(cur)
                cur = w["text"]
            else:
                cur += " " + w["text"]
            prev_end = w["x1"]
        cells.append(cur)
        out.append(cells)
    return out


def _pdf_to_xlsx(raw: bytes) -> bytes:
    try:
        import pdfplumber
    except ImportError as exc:                       # pragma: no cover
        raise ValueError(
            "PDF support requires the 'pdfplumber' package (pip install pdfplumber)."
        ) from exc

    all_rows: list[list] = []
    any_text = False               # did the page expose ANY selectable text?
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            if (page.extract_text() or "").strip():
                any_text = True
            tables = page.extract_tables() or []
            if tables:
                for tbl in tables:
                    for row in tbl:
                        # pdfplumber yields None for empty cells.
                        all_rows.append(["" if c is None else c for c in row])
            else:
                all_rows.extend(_text_lines_to_rows(page))

    # A PDF with no text layer still exposes its ruled grid to extract_tables(),
    # so all_rows comes back full of EMPTY cells (the digits are pixels or vector
    # outlines, not text). Two common causes: a scanned/image PDF, OR a file made
    # with "Microsoft Print to PDF" which flattens Thai fonts to vector curves.
    # Detect that — no selectable text anywhere, or every extracted cell blank —
    # and give the actionable message instead of letting a domain parser fail
    # later with a misleading "could not find date column".
    has_content = any(str(c).strip() for row in all_rows for c in row)
    if not all_rows or not any_text or not has_content:
        raise ValueError(
            "อ่านไฟล์ PDF นี้ไม่ได้ — ไม่มีตัวอักษรให้อ่าน (เป็นไฟล์สแกน หรือ PDF ที่ได้จากการ "
            "'Print to PDF' ซึ่งแปลงตัวเลขเป็นรูปภาพ) กรุณาดาวน์โหลด statement เป็นไฟล์ "
            "Excel/CSV จากระบบธนาคาร (เช่น KTB Corporate Online) แล้วอัปโหลดแทน"
        )
    return _rows_to_xlsx(all_rows)


# ── Encryption / password detection (C3) ─────────────────────────────────────

def is_encrypted_upload(raw: bytes, filename: str = "") -> str | None:
    """Return a Thai 'locked with a password' message if the upload is an
    encrypted Office file, password-protected zip, or encrypted PDF — else None.
    These otherwise fail opaquely or as a silent 0-row parse (e.g. the 39/48
    password-protected Krungsri RDB003 archives)."""
    if not raw:
        return None
    name = (filename or "").lower()
    # Office encryption wraps the document in an OLE compound file whose payload
    # is an 'EncryptedPackage' stream (true for encrypted .xlsx/.xls/.doc).
    if raw[:8] == b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1":
        head = raw[:8192]
        if b"EncryptedPackage" in head or b"E\x00n\x00c\x00r\x00y\x00p\x00t\x00e\x00d" in head:
            return "ไฟล์ Excel นี้ถูกล็อกด้วยรหัสผ่าน — กรุณาถอดรหัสผ่านก่อนอัปโหลด"
    # A zip whose first local-file-header sets general-purpose bit 0 = encrypted
    # entry (a password-protected .zip/.xlsx member).
    if raw[:2] == b"PK" and len(raw) > 8 and (raw[6] & 0x01):
        return "ไฟล์นี้ถูกล็อกด้วยรหัสผ่าน — กรุณาถอดรหัส (unzip) ก่อนอัปโหลด"
    # Password-protected PDF: /Encrypt in the trailer region (checked near the
    # end to avoid false positives from the string appearing in a content stream).
    if name.endswith(".pdf") and b"/Encrypt" in raw[-3072:]:
        return "ไฟล์ PDF นี้ถูกล็อกด้วยรหัสผ่าน — กรุณาถอดรหัสก่อนอัปโหลด"
    return None


# ── Public API ───────────────────────────────────────────────────────────────

def to_parseable_xlsx(raw: bytes, filename: str) -> bytes:
    """Return .xlsx bytes the domain parsers can read, converting from CSV/PDF
    when needed. Excel inputs pass through untouched. Raises ValueError with a
    user-facing message on unsupported/empty content."""
    name = (filename or "").lower()
    if name.endswith(".csv"):
        return _csv_to_xlsx(raw)
    if name.endswith(".pdf"):
        return _pdf_to_xlsx(raw)
    if name.endswith(".xls"):
        return _xls_to_xlsx(raw)
    # .xlsx (and anything else already gated upstream) → unchanged.
    return raw
