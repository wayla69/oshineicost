"""
Input normaliser for recon uploads.

Every domain parser (bank / credit_card / grab / lineman) reads its data through
``openpyxl.load_workbook(BytesIO(file_bytes))`` and then sniffs cell text for
headers / provider keywords. To accept formats other than Excel we therefore do
NOT touch the parsers — we convert the incoming bytes into an in-memory .xlsx
workbook with the same tabular layout and hand those bytes to the existing
parser. Header detection, provider sniffing and date parsing then "just work".

Supported inputs:
  - .xlsx / .xls  → passed through unchanged (parsers already read these)
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
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables() or []
            if tables:
                for tbl in tables:
                    for row in tbl:
                        # pdfplumber yields None for empty cells.
                        all_rows.append(["" if c is None else c for c in row])
            else:
                all_rows.extend(_text_lines_to_rows(page))

    if not all_rows:
        raise ValueError(
            "No tabular data could be extracted from this PDF. It may be a "
            "scanned image (needs OCR) or an unsupported layout — please upload "
            "the Excel/CSV export instead."
        )
    return _rows_to_xlsx(all_rows)


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
    # .xlsx / .xls (and anything else already gated upstream) → unchanged.
    return raw
