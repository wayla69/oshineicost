"""Uploads and their evidence: statements, attachments, deposit slips, coupon series.

Moved out of recon/main.py in Phase 7c (2026-09) by the AST: these endpoints and
the helpers only they use, unchanged. URLs are unchanged.
"""
from fastapi import APIRouter
from ..core import RECON_START_DAY
from ..core import _ATTACH_DIR
from ..core import _DAY_RE
from ..core import _actor
from ..core import _assert_period_open
from ..core import _audit
from ..core import _override_channel
from ..core import _statement_code_overrides
from ..core import _validate_master_branch
from ..core import _validate_period
from ..core import get_db
from ..core import require_write_token
from ..matcher import upload_match_stats
from ..parsers import parse_bank_statement
from ..parsers import parse_cc_settlement
from ..parsers import parse_grab_report
from ..parsers import parse_lineman_report
from ..parsers.loader import is_encrypted_upload as _is_encrypted_upload
from ..parsers.loader import to_parseable_xlsx
from ..parsers.loader import unlock_pdf as _unlock_pdf
from ..parsers.pdf_formats import detect_and_parse as _detect_pdf_format
from ..parsers.pdf_formats import fee_invoice_by_name as _fee_invoice_by_name
from ..parsers.pdf_formats import fee_invoice_by_text as _fee_invoice_by_text
from ..parsers.pdf_formats import unsupported_bank_pdf_message as _unsupported_bank_pdf_message
from datetime import datetime as _dt
from datetime import timedelta as _td
from fastapi import Depends
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import Request
from fastapi import UploadFile
from fastapi.responses import Response
from pathlib import Path
from pydantic import BaseModel
from typing import Literal
import hashlib
import re
import sqlite3
import uuid

router = APIRouter()


MAX_UPLOAD_BYTES = 15 * 1024 * 1024          # 15 MB


_ALLOWED_EXT     = (".xlsx", ".xls", ".csv", ".pdf")


async def _read_upload(file: UploadFile) -> bytes:
    if not (file.filename or "").lower().endswith(_ALLOWED_EXT):
        raise HTTPException(415, "Only .xlsx/.xls/.csv/.pdf files are accepted.")
    raw = await file.read()
    if not raw:
        raise HTTPException(422, "Empty file.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
    return raw


SourceType = Literal["bank", "credit_card", "grab", "lineman"]


_LINE_TABLE = {
    "bank":        "recon_bank_lines",
    "credit_card": "recon_cc_lines",
    "grab":        "recon_grab_lines",
    "lineman":     "recon_lineman_lines",
}


def _parse(source_type: SourceType, raw: bytes, provider: str, filename: str = ""):
    """Returns (provider, rows, skipped_rows). Recognised Thai bank/acquirer/Grab
    PDFs are handled by format-specific extractors; otherwise CSV/PDF inputs are
    normalised to an in-memory .xlsx so the domain parsers run unchanged."""
    if (filename or "").lower().endswith(".pdf"):
        special = _detect_pdf_format(source_type, raw)
        if special is not None:
            return special
    raw = to_parseable_xlsx(raw, filename)
    if source_type == "bank":
        prov, rows, skipped = parse_bank_statement(raw, provider or None)
    elif source_type == "credit_card":
        prov, rows, skipped = parse_cc_settlement(raw, provider or None)
    elif source_type == "grab":
        rows, skipped = parse_grab_report(raw)
        prov = "Grab"
    else:
        rows, skipped = parse_lineman_report(raw)
        prov = "Lineman"
    return prov, rows, skipped


def _specific_failure_message(source_type: SourceType, raw: bytes, filename: str) -> str | None:
    """The most specific actionable message for an upload that failed/parsed empty:
    a renamed fee invoice (A2), or an unsupported bank's statement PDF (C6)."""
    msg = _fee_invoice_by_text(raw, filename)
    if not msg and source_type == "bank":
        msg = _unsupported_bank_pdf_message(raw, filename)
    return msg


def _parse_or_422(source_type: SourceType, raw: bytes, provider: str, filename: str = ""):
    """Returns (provider, rows, skipped, raw) — raw is the UNLOCKED bytes when the
    upload was a password-locked PDF, so callers' empty-statement checks read the
    decrypted document (the file hash stays on the caller's original bytes)."""
    # C3: a password-locked file can't be type-detected or parsed — say so first.
    locked = _is_encrypted_upload(raw, filename)
    if locked:
        # KTB ships each branch a statement PDF locked with that branch's account
        # number. Try the configured passwords to unlock, then parse the decrypted
        # copy (file_hash above stays on the original bytes). Reject only if none fit.
        unlocked = _unlock_pdf(raw)
        if unlocked is not None:
            raw = unlocked
        else:
            raise HTTPException(422, locked)
    # A2: reject a known WRONG document type up front (cheap filename check) with
    # specific guidance on the correct file to upload — before any parse attempt.
    byname = _fee_invoice_by_name(source_type, filename)
    if byname:
        raise HTTPException(422, byname)
    try:
        prov, rows, skipped = _parse(source_type, raw, provider, filename)
    except HTTPException:
        raise
    except Exception as exc:
        # A statement with zero movements (e.g. KBank CA 'No Data' month) has no
        # table for any parser to find — that's a valid empty document, not an error.
        if _is_empty_statement(raw, filename):
            return (provider or "", [], 0, raw)
        raise HTTPException(422, _specific_failure_message(source_type, raw, filename)
                            or f"Parse error: {exc}")
    # Parsed but found nothing (and not a legit empty statement): give the most
    # specific message we can; otherwise A1's generic 0-row guard handles it.
    if not rows and not _is_empty_statement(raw, filename):
        msg = _specific_failure_message(source_type, raw, filename)
        if msg:
            raise HTTPException(422, msg)
    return prov, rows, skipped, raw


@router.post("/api/uploads/{source_type}/preview")
async def preview_upload(
    source_type: SourceType,
    file: UploadFile = File(...),
    branch_id: int   = Form(...),
    period_month: str = Form(...),
    provider: str    = Form(""),
):
    _validate_period(period_month)
    raw = await _read_upload(file)
    with get_db() as conn:
        _validate_master_branch(conn, branch_id)
    prov, rows, skipped, raw = _parse_or_422(source_type, raw, provider, file.filename)
    detected = _detect_branch_from_rows(source_type, rows)   # by bank account # / acquirer MID
    with get_db() as conn:
        dup_lines = sum(1 for r in rows if _bank_line_exists(conn, branch_id, r)) \
            if source_type == "bank" else 0
    return {
        "provider":     prov,
        "row_count":    len(rows),
        "skipped_rows": skipped,   # data rows dropped for an unparseable date
        # months the rows are dated in (the matcher reads lines by their own date);
        # confirm refuses a statement/Grab/LINE MAN doc with no row in period_month
        # and any doc with a row in an approved/closed month
        "row_months":   _row_months(source_type, rows),
        # bank lines already saved from another upload (re-downloaded statement) —
        # confirm skips these
        "duplicate_lines": dup_lines,
        "preview":      rows[:50],
        "columns":      list(rows[0].keys()) if rows else [],
        "empty_statement": _is_empty_statement(raw, file.filename) if not rows else False,
        # C7: warn when no format-specific extractor matched and the generic grid
        # was used (its debit/credit split can be wrong) — operator should eyeball.
        "generic_fallback": str(prov).strip().lower() in ("", "generic", "none"),
        # OCR fallback used (no-text KTB PDF): amounts passed the balance-chain
        # self-check, but the operator should still eyeball the total before close.
        "ocr_used": "ocr" in str(prov).strip().lower(),
        **detected,                # detected_branch_id / detected_branch_name / detected_key
    }


def _is_empty_statement(raw: bytes, filename: str | None) -> bool:
    """Valid statement with zero movements (e.g. KBank CA 'No Data' month)."""
    if not (filename or "").lower().endswith(".pdf"):
        return False
    from ..parsers.pdf_formats import is_empty_statement
    return is_empty_statement(raw)


def _detect_branch_from_rows(source_type: str, rows: list) -> dict:
    """Auto-attribute an upload to a branch from the identifiers inside it:
    a bank statement's account number (recon_bank_account) or an acquirer
    settlement's merchant MID (recon_acquirer_mid). Returns {} if no unambiguous
    match (the operator's chosen branch then stands)."""
    if not rows:
        return {}
    key, sql = None, None
    if source_type == "bank":
        key = next((r.get("account_no") for r in rows if r.get("account_no")), None)
        sql = "SELECT branch_id FROM recon_bank_account WHERE account_no=? AND COALESCE(active,1)=1"
    elif source_type == "credit_card":
        key = next((r.get("merchant_id") for r in rows if r.get("merchant_id")), None)
        sql = "SELECT branch_id FROM recon_acquirer_mid WHERE merchant_id=? AND COALESCE(active,1)=1"
    if not key:
        return {}
    key = str(key).strip()
    try:
        with get_db() as conn:
            hit = conn.execute(sql, (key,)).fetchone()
            if not hit:
                return {}
            name = conn.execute("SELECT name FROM branches WHERE branch_id=?", (hit["branch_id"],)).fetchone()
            return {"detected_branch_id": hit["branch_id"],
                    "detected_branch_name": name["name"] if name else None,
                    "detected_key": key}
    except sqlite3.OperationalError:
        return {}


# The date each line table is read by (matcher scopes lines by THIS date's
# month, not by the upload's period_month).
_DATE_COL = {
    "bank":        "txn_date",
    "credit_card": "settlement_date",
    "grab":        "order_date",
    "lineman":     "order_date",
}


def _row_months(source_type: str, rows: list) -> list[str]:
    key = _DATE_COL[source_type]
    return sorted({str(r[key])[:7] for r in rows if r.get(key)})


def _upload_line_months(conn, source_type: str, upload_id: int) -> list[str]:
    table, col = _LINE_TABLE[source_type], _DATE_COL[source_type]
    return [r[0] for r in conn.execute(
        f"SELECT DISTINCT substr({col},1,7) FROM {table} "
        f"WHERE upload_id=? AND {col} IS NOT NULL", (upload_id,))]


def _assert_months_open(conn, branch_id: int, months) -> None:
    """423 when any month the document's lines fall in is approved/closed."""
    for m in months:
        try:
            _assert_period_open(conn, branch_id, m)
        except HTTPException as e:
            raise HTTPException(
                423, f"เอกสารนี้มีรายการของเดือน {m} ซึ่งปิด/อนุมัติแล้ว — {e.detail}")


def _find_dup_file(conn, branch_id: int, source_type: str, file_hash: str | None,
                   exclude_upload: int | None = None):
    """A confirmed upload of the identical file for this branch, in ANY month
    (the same bytes picked under another month would double the same lines)."""
    if not file_hash:
        return None
    return conn.execute("""
        SELECT file_name, uploaded_at, period_month FROM recon_uploads
        WHERE branch_id=? AND source_type=? AND file_hash=? AND status='confirmed'
          AND upload_id IS NOT ?
    """, (branch_id, source_type, file_hash, exclude_upload)).fetchone()


def _dup_file_message(dup) -> str:
    return (f"ไฟล์นี้อัปโหลดไปแล้วสำหรับสาขานี้ (เดือน {dup['period_month']}: "
            f"'{dup['file_name']}' เมื่อ {dup['uploaded_at']})")


def _bank_line_exists(conn, branch_id: int, row: dict, exclude_upload: int | None = None) -> bool:
    """A bank line with the same date, debit, credit and running balance (same
    account when both sides know it) is already saved for this branch — i.e. a
    re-downloaded statement (different bytes, same movements). Lines without a
    balance are never matched: two genuine same-day same-amount receipts would
    be indistinguishable."""
    if row.get("balance") is None or not row.get("txn_date"):
        return False
    acct = str(row.get("account_no") or "")
    return conn.execute("""
        SELECT 1 FROM recon_bank_lines l
        JOIN recon_uploads u ON u.upload_id = l.upload_id AND u.status = 'confirmed'
        WHERE l.branch_id=? AND l.txn_date=? AND l.balance IS NOT NULL
          AND ABS(l.balance - ?) < 0.005
          AND ABS(COALESCE(l.credit, 0) - ?) < 0.005
          AND ABS(COALESCE(l.debit, 0) - ?) < 0.005
          AND (COALESCE(l.account_no, '') = '' OR ? = '' OR l.account_no = ?)
          AND l.upload_id IS NOT ?
        LIMIT 1
    """, (branch_id, row["txn_date"], float(row["balance"]),
          float(row.get("credit") or 0), float(row.get("debit") or 0),
          acct, acct, exclude_upload)).fetchone() is not None


def _ingest_one(conn, source_type: str, raw: bytes, filename: str,
                branch_id: int, period_month: str, provider: str = "",
                actor: str = "staff") -> dict:
    """Parse + validate + persist ONE document into recon_uploads + its line table.
    Runs every guard (dedup, C3 lock, A2/C6 wrong-type, wrong-branch, A1 zero-row,
    row-month period/lock, bank line overlap) and raises HTTPException on any.
    Does NOT commit — the caller owns the transaction (single commit in confirm;
    one commit per file in bulk)."""
    file_hash = hashlib.sha256(raw).hexdigest()
    dup = _find_dup_file(conn, branch_id, source_type, file_hash)
    if dup:
        raise HTTPException(409, _dup_file_message(dup))

    prov, rows, skipped, raw = _parse_or_422(source_type, raw, provider, filename)

    # Wrong-branch guard: a document that identifies its own branch (account # /
    # acquirer MID) and isn't the chosen one is refused (how a UBN stmt lands on UDN).
    detected = _detect_branch_from_rows(source_type, rows)
    det_bid = detected.get("detected_branch_id")
    if det_bid and det_bid != branch_id:
        raise HTTPException(
            409, f"เอกสารนี้เป็นของสาขา '{detected.get('detected_branch_name')}' "
                 f"(ตรวจจาก {detected.get('detected_key')}) ไม่ใช่สาขาที่เลือก")

    # Zero-row guard (A1): a non-empty file that parses to nothing is the wrong
    # type — never save it as a silent 'confirmed, 0 rows'.
    if not rows and not _is_empty_statement(raw, filename):
        raise HTTPException(
            422, "อ่านได้ 0 รายการ — น่าจะเป็นเอกสารผิดประเภทหรือรูปแบบที่ยังไม่รองรับ "
                 "(เช่น ใบกำกับภาษีค่าธรรมเนียม แทนรายงาน settlement)")

    # Row months: the matcher reads every line by its OWN date, so the picked
    # period_month alone guards nothing. A document may span a month boundary
    # (T+1 deposits, payouts landing on the 1st), so rows outside period_month
    # are allowed — but every month a row falls in must be open, and a statement
    # / delivery report with NO row in period_month was picked under the wrong
    # month. Card settlements are exempt from the latter: a per-day KTC/BAY
    # report carries only its payout date, which for month-end sales is the 1st
    # of the next month, yet it belongs to the sales month's document set.
    months = _row_months(source_type, rows)
    if months and period_month not in months and source_type != "credit_card":
        raise HTTPException(
            422, f"รายการในเอกสารนี้เป็นของเดือน {', '.join(months)} "
                 f"ไม่ใช่เดือนที่เลือก ({period_month}) — เลือกเดือนให้ตรงกับเอกสารแล้วอัปโหลดใหม่")
    _assert_months_open(conn, branch_id, months)

    # Line-level overlap (bank): a re-downloaded statement has different bytes
    # but the same movements — skip lines already saved instead of doubling.
    dup_lines = 0
    if source_type == "bank" and rows:
        kept = [r for r in rows if not _bank_line_exists(conn, branch_id, r)]
        dup_lines = len(rows) - len(kept)
        if not kept:
            raise HTTPException(
                409, f"ทุกรายการในเอกสารนี้ ({dup_lines} รายการ) อัปโหลดไว้แล้วจากไฟล์อื่น "
                     "— ไม่บันทึกซ้ำ")
        rows = kept

    note = "ไม่มีรายการในเอกสาร (No Data)" if (
        not rows and _is_empty_statement(raw, filename)) else None
    cur = conn.execute("""
        INSERT INTO recon_uploads
            (source_type, branch_id, provider, file_name, period_month, row_count, file_hash, status, error_msg)
        VALUES (?,?,?,?,?,?,?,'confirmed',?)
    """, (source_type, branch_id, prov, filename, period_month, len(rows), file_hash, note))
    upload_id = cur.lastrowid
    table = _LINE_TABLE[source_type]
    overrides = _statement_code_overrides(conn) if source_type == "bank" else {}
    for row in rows:
        row["upload_id"] = upload_id
        row["branch_id"] = branch_id
        if overrides and "channel" in row:       # manager remaps win over the parser
            row["channel"] = _override_channel(row.get("description"), row.get("channel"), overrides)
        cols         = ", ".join(row.keys())
        placeholders = ", ".join("?" * len(row))
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
                     list(row.values()))
    _audit(conn, "upload", upload_id, "create", actor,
           [{"field": "file_name", "old": None, "new": filename}])
    dkey = _DATE_COL[source_type]
    return {"upload_id": upload_id, "rows_saved": len(rows),
            "skipped_rows": skipped, "provider": prov,
            "duplicate_lines": dup_lines,   # bank lines skipped as already saved
            "row_months": months,
            # saved rows dated outside the picked month (T+1 deposits, a payout on the 1st):
            # they count in their own month — the drop-zone result names them
            "other_month_rows": sum(1 for r in rows if str(r.get(dkey) or "")[:7] not in ("", period_month))}


@router.post("/api/uploads/{source_type}/confirm", dependencies=[Depends(require_write_token)])
async def confirm_upload(
    source_type: SourceType,
    request: Request,
    file: UploadFile = File(...),
    branch_id: int   = Form(...),
    period_month: str = Form(...),
    provider: str    = Form(""),
):
    _validate_period(period_month)
    raw = await _read_upload(file)
    with get_db() as conn:
        _validate_master_branch(conn, branch_id)
        _assert_period_open(conn, branch_id, period_month)
        result = _ingest_one(conn, source_type, raw, file.filename, branch_id, period_month,
                             provider, actor=_actor(request, None))
        conn.commit()
    return result


_MAX_BULK_FILES   = 2000


_MAX_MEMBER_BYTES = 25 * 1024 * 1024


def _infer_source_type(filename: str) -> str | None:
    """Best-effort source_type from filename conventions (for 'auto' bulk). Returns
    None when unsure → the file is reported in the manifest for manual routing,
    never guessed into the wrong table."""
    n = (filename or "").lower()
    if any(k in n for k in ("rdb003", "tiv_mdr", "rtxinv", "e-tax_invoice",
                            "_rmer", "_cc_", "kbank_cc", "settle")):
        return "credit_card"
    if n.startswith("3-cz") or re.match(r"^im\d{6,}", n):
        return "grab"
    if any(k in n for k in ("lineman", "line man", "line_man", "lmin", "lmrn",
                            "balance-report", "report_may", "report_jun")):
        return "lineman"
    if "statement" in n or "stmt" in n or "_kb_" in n or "_ktb" in n:
        return "bank"
    return None


def _expand_zip(zip_name: str, raw: bytes, manifest: list) -> list:
    """Yield (member_name, bytes) for a .zip; password-locked / oversized / bad
    members are recorded in the manifest and skipped."""
    import io as _io
    import zipfile as _zip
    out = []
    try:
        with _zip.ZipFile(_io.BytesIO(raw)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                label = f"{zip_name}:{Path(info.filename).name}"
                if info.file_size > _MAX_MEMBER_BYTES:
                    manifest.append({"file": label, "status": "skipped",
                                     "rows_saved": 0, "reason": "ไฟล์ใหญ่เกินไป"})
                    continue
                try:
                    out.append((Path(info.filename).name, zf.read(info)))
                except RuntimeError:
                    manifest.append({"file": label, "status": "skipped", "rows_saved": 0,
                                     "reason": "ไฟล์ใน zip ถูกล็อกด้วยรหัสผ่าน — ถอดรหัสก่อน"})
    except Exception:
        manifest.append({"file": zip_name, "status": "error", "rows_saved": 0,
                         "reason": "เปิดไฟล์ zip ไม่ได้"})
    return out


@router.post("/api/uploads/bulk", dependencies=[Depends(require_write_token)])
async def bulk_upload(
    request: Request,
    files: list[UploadFile] = File(...),
    branch_id: int   = Form(...),
    period_month: str = Form(...),
    source_type: str = Form("auto"),   # a real type, or 'auto' (infer per file)
    provider: str    = Form(""),
):
    """Upload many files (and/or .zip archives) in one pass. Each file runs the
    same validated pipeline as a single confirm; the response is a per-file
    manifest so wrong-type / locked / duplicate files surface together instead of
    one-click-at-a-time. One commit per file → a bad file never rolls back good ones."""
    _validate_period(period_month)
    if source_type not in ("bank", "credit_card", "grab", "lineman", "auto"):
        raise HTTPException(400, "source_type ต้องเป็น bank|credit_card|grab|lineman|auto")
    if len(files) > _MAX_BULK_FILES:
        raise HTTPException(413, f"ไฟล์มากเกินไป (สูงสุด {_MAX_BULK_FILES} ไฟล์ต่อครั้ง)")

    manifest: list = []
    with get_db() as conn:
        _validate_master_branch(conn, branch_id)
        _assert_period_open(conn, branch_id, period_month)

        # Flatten: each upload is either a file or a .zip to expand.
        items: list = []
        for f in files:
            raw = await f.read()
            name = f.filename or "(unnamed)"
            if name.lower().endswith(".zip"):
                items.extend(_expand_zip(name, raw, manifest))
            else:
                items.append((name, raw))

        for name, raw in items:
            ext = Path(name).suffix.lower()
            if ext and ext not in _ALLOWED_EXT:
                manifest.append({"file": name, "status": "skipped", "rows_saved": 0,
                                 "reason": f"ไม่รองรับนามสกุล {ext}"}); continue
            if not raw:
                manifest.append({"file": name, "status": "skipped", "rows_saved": 0,
                                 "reason": "ไฟล์ว่าง"}); continue
            if len(raw) > _MAX_MEMBER_BYTES:
                manifest.append({"file": name, "status": "skipped", "rows_saved": 0,
                                 "reason": "ไฟล์ใหญ่เกินไป"}); continue
            st = source_type if source_type != "auto" else _infer_source_type(name)
            if not st:
                manifest.append({"file": name, "status": "skipped", "rows_saved": 0,
                                 "reason": "ไม่ทราบประเภทไฟล์ — เลือกช่องเองแล้วอัปโหลด"}); continue
            try:
                res = _ingest_one(conn, st, raw, name, branch_id, period_month, provider,
                                  actor=_actor(request, None))
                conn.commit()
                manifest.append({"file": name, "source_type": st, "status": "ok",
                                 "rows_saved": res["rows_saved"], "reason": "",
                                 "duplicate_lines": res["duplicate_lines"],
                                 "row_months": res["row_months"],
                                 "other_month_rows": res["other_month_rows"],
                                 "provider": res["provider"]})
            except HTTPException as e:
                conn.rollback()
                status = "skipped" if e.status_code == 409 else "error"
                manifest.append({"file": name, "source_type": st, "status": status,
                                 "rows_saved": 0, "reason": str(e.detail)})
            except Exception as e:                       # pragma: no cover
                conn.rollback()
                manifest.append({"file": name, "source_type": st, "status": "error",
                                 "rows_saved": 0, "reason": f"error: {e}"})

    ok  = sum(1 for m in manifest if m["status"] == "ok")
    skip = sum(1 for m in manifest if m["status"] == "skipped")
    err = sum(1 for m in manifest if m["status"] == "error")
    return {"summary": {"total": len(manifest), "saved": ok, "skipped": skip,
                        "errored": err, "rows": sum(m["rows_saved"] for m in manifest),
                        "duplicate_lines": sum(m.get("duplicate_lines") or 0 for m in manifest),
                        "other_month_rows": sum(m.get("other_month_rows") or 0 for m in manifest)},
            "files": manifest}


@router.get("/api/uploads")
def list_uploads(branch_id: int | None = None, period_month: str | None = None):
    with get_db() as conn:
        filters, params = [], []
        if branch_id:
            filters.append("u.branch_id=?");    params.append(branch_id)
        if period_month:
            filters.append("u.period_month=?"); params.append(period_month)
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        rows = conn.execute(f"""
            SELECT u.*, b.name AS branch_name
            FROM recon_uploads u
            LEFT JOIN branches b ON b.branch_id = u.branch_id
            {where}
            ORDER BY u.uploaded_at DESC LIMIT 200
        """, params).fetchall()
        out = [dict(r) for r in rows]

        # Per-upload match status, computed LIVE by lane (no Run Match needed).
        # Recompute once per (branch, month) present in the result, then attach.
        scopes = {(u["branch_id"], u["period_month"]) for u in out
                  if u.get("branch_id") and u.get("period_month")}
        stats: dict = {}
        for bid, pm in scopes:
            try:
                stats.update(upload_match_stats(conn, bid, pm))
            except sqlite3.OperationalError:
                pass
        for u in out:
            s = stats.get(u["upload_id"], {"total": 0, "matched": 0, "excluded": 0})
            u["match_total"]    = s["total"]
            u["match_done"]     = s["matched"]
            u["match_excluded"] = s["excluded"]
        return out


@router.get("/api/uploads/{upload_id}/lines")
def upload_lines(upload_id: int, limit: int = 300):
    """The parsed lines of one upload WITH their match status — lets staff open a
    document and see exactly which lines reconciled and which still hang."""
    from ..matcher import (_CHANNEL_CATEGORY, _provider_bank, _provider_acquirer,
                          _line_status, _lane_status_map, cash_deposit_matching)
    with get_db() as conn:
        up = conn.execute(
            "SELECT source_type, branch_id, period_month, provider FROM recon_uploads WHERE upload_id=?",
            (upload_id,)).fetchone()
        if not up:
            raise HTTPException(404, "upload not found")
        st_, bid, pm, prov = up["source_type"], up["branch_id"], up["period_month"], up["provider"]
        is_cc = st_ == "credit_card"
        spec = {
            "bank":        ("recon_bank_lines",    "txn_date",        "description", "credit"),
            "credit_card": ("recon_cc_lines",      "settlement_date", "batch_no",    "gross_amount"),
            "grab":        ("recon_grab_lines",    "order_date",      "order_id",    "food_amount"),
            "lineman":     ("recon_lineman_lines", "order_date",      "order_id",    "food_amount"),
        }[st_]
        table, date_col, ref_col, amt_col = spec
        extra = (", channel" if table == "recon_bank_lines"
                 else ", acquirer" if is_cc else "")
        rows = conn.execute(f"""
            SELECT id, {date_col} AS date, {ref_col} AS ref, {amt_col} AS amount,
                   match_status{extra}
            FROM {table} WHERE upload_id=? ORDER BY {date_col}, id LIMIT ?
        """, (upload_id, max(1, min(limit, 1000)))).fetchall()
        out = [dict(r) for r in rows]

        # Derive each line's LIVE reconciliation status by lane so the drill
        # agrees with the panel without a Run Match. One upload = one bank, so the
        # provider's bank is resolved once.
        lane_status = _lane_status_map(conn, bid, pm)
        cash_ids = {g["line_id"] for g in cash_deposit_matching(conn, bid, pm)["groups"]}
        prov_bank = _provider_bank(prov)
        prov_acq  = _provider_acquirer(prov)
        for r in out:
            if st_ == "bank":
                cat = _CHANNEL_CATEGORY.get(r.get("channel"))
                bank = "NONE" if cat == "CASH" else prov_bank
                # only revenue-channel CREDITS reconcile; debits / zero / FEE / payout
                # legs are informational (mirrors upload_match_stats' credit>0 filter)
                if cat is None or (r.get("amount") or 0) <= 0:
                    r["status"] = "informational"; continue
            elif is_cc:
                cat, bank = "CREDIT", (r.get("acquirer") or prov_acq)
            else:
                cat, bank = ("GRAB" if st_ == "grab" else "LINEMAN"), "NONE"
            r["status"] = _line_status(r["match_status"], cat, bank, lane_status, cash_ids, r["id"])
        done = sum(1 for r in out if r["status"] == "matched")
        excl = sum(1 for r in out if r["status"] == "excluded")
        # Denominator = reconcilable revenue lines (exclude informational + excluded),
        # so "X of Y" here equals the panel's match bar.
        reconcilable = sum(1 for r in out if r["status"] in ("matched", "unmatched", "manual"))
        return {"source_type": st_, "count": reconcilable, "total_lines": len(out),
                "matched": done, "excluded": excl, "lines": out}


@router.delete("/api/uploads/{upload_id}", dependencies=[Depends(require_write_token)])
def delete_upload(upload_id: int, request: Request):
    with get_db() as conn:
        up = conn.execute(
            "SELECT source_type, branch_id, period_month, file_name, file_hash FROM recon_uploads "
            "WHERE upload_id=?", (upload_id,)).fetchone()
        if not up:
            raise HTTPException(404, "upload not found")
        _assert_period_open(conn, up["branch_id"], up["period_month"])
        # its lines count in the months of their OWN dates — those must be open too
        _assert_months_open(conn, up["branch_id"],
                            _upload_line_months(conn, up["source_type"], upload_id))
        conn.execute("DELETE FROM recon_uploads WHERE upload_id=?", (upload_id,))
        # Record the file hash too, so a deleted source document stays traceable in the
        # audit log (it may be referenced by an earlier close snapshot's manifest).
        _audit(conn, "upload", upload_id, "delete", _actor(request, None),
               [{"field": "file_name", "old": up["file_name"], "new": None},
                {"field": "file_hash", "old": up["file_hash"], "new": None}])
        conn.commit()
    return {"deleted": upload_id}


class _UploadBranchBody(BaseModel):
    branch_id: int


@router.patch("/api/uploads/{upload_id}/branch", dependencies=[Depends(require_write_token)])
def reassign_upload_branch(upload_id: int, body: _UploadBranchBody, request: Request):
    """Move a confirmed upload (and all its parsed lines) to another branch —
    the 2-click fix for a statement confirmed under the wrong branch. Runs the
    upload path's guards for the TARGET branch: period/line-month locks,
    duplicate file, duplicate bank lines, and the document's own account/MID."""
    with get_db() as conn:
        up = conn.execute(
            "SELECT source_type, branch_id, period_month, file_name, file_hash "
            "FROM recon_uploads WHERE upload_id=?",
            (upload_id,)).fetchone()
        if not up:
            raise HTTPException(404, "upload not found")
        if up["branch_id"] == body.branch_id:
            return {"upload_id": upload_id, "branch_id": body.branch_id, "moved_lines": 0}
        _validate_master_branch(conn, body.branch_id)
        _assert_period_open(conn, up["branch_id"], up["period_month"])
        _assert_period_open(conn, body.branch_id, up["period_month"])
        line_months = _upload_line_months(conn, up["source_type"], upload_id)
        _assert_months_open(conn, up["branch_id"], line_months)
        _assert_months_open(conn, body.branch_id, line_months)

        table = _LINE_TABLE[up["source_type"]]
        dup = _find_dup_file(conn, body.branch_id, up["source_type"], up["file_hash"],
                             exclude_upload=upload_id)
        if dup:
            raise HTTPException(409, _dup_file_message(dup))
        lines = [dict(r) for r in conn.execute(
            f"SELECT * FROM {table} WHERE upload_id=?", (upload_id,))]
        # Wrong-branch guard, as on upload: the document's own account # / MID.
        detected = _detect_branch_from_rows(up["source_type"], lines)
        det_bid = detected.get("detected_branch_id")
        if det_bid and det_bid != body.branch_id:
            raise HTTPException(
                409, f"เอกสารนี้เป็นของสาขา '{detected.get('detected_branch_name')}' "
                     f"(ตรวจจาก {detected.get('detected_key')}) ไม่ใช่สาขาที่เลือก")
        if up["source_type"] == "bank":
            overlap = sum(1 for r in lines
                          if _bank_line_exists(conn, body.branch_id, r, exclude_upload=upload_id))
            if overlap:
                raise HTTPException(
                    409, f"สาขาปลายทางมีรายการเดียวกันนี้อยู่แล้ว {overlap} รายการ "
                         "— ย้ายแล้วจะนับซ้ำ")

        moved = conn.execute(
            f"UPDATE {table} SET branch_id=? WHERE upload_id=?",
            (body.branch_id, upload_id)).rowcount
        conn.execute("UPDATE recon_uploads SET branch_id=? WHERE upload_id=?",
                     (body.branch_id, upload_id))
        _audit(conn, "upload", upload_id, "update", _actor(request, None),
               [{"field": "branch_id", "old": up["branch_id"], "new": body.branch_id}])
        conn.commit()
    return {"upload_id": upload_id, "branch_id": body.branch_id, "moved_lines": moved}


# ── Evidence attachments (photos / PDFs per branch+day) ───────────────────────
_ATTACH_MAX = 10 * 1024 * 1024


_ATTACH_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf", ".heic")


@router.get("/api/attachments")
def list_attachments(branch_id: int, day: str):
    if not _DAY_RE.match(day or ""):
        raise HTTPException(400, "day must be in 'YYYY-MM-DD' format.")
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, branch_id, day, category, file_name, mime, size_bytes, note, "
            "uploaded_by, uploaded_at FROM recon_attachment "
            "WHERE branch_id=? AND day=? ORDER BY uploaded_at DESC", (branch_id, day))]


@router.post("/api/attachments", dependencies=[Depends(require_write_token)])
async def create_attachment(branch_id: int = Form(...), day: str = Form(...),
                            category: str = Form(""), note: str = Form(""),
                            uploaded_by: str = Form(""), file: UploadFile = File(...)):
    if not _DAY_RE.match(day or ""):
        raise HTTPException(400, "day must be in 'YYYY-MM-DD' format.")
    fn = (file.filename or "").strip()
    if not fn.lower().endswith(_ATTACH_EXT):
        raise HTTPException(415, "Only images (jpg/png/webp/gif/heic) or PDF are accepted.")
    raw = await file.read()
    if not raw:
        raise HTTPException(422, "Empty file.")
    if len(raw) > _ATTACH_MAX:
        raise HTTPException(413, f"File exceeds the {_ATTACH_MAX // (1024*1024)} MB limit.")

    # Store file on volume, not in DB
    safe_ext = Path(fn).suffix.lower()
    rel_path = f"{day[:7]}/{branch_id}_{day}_{uuid.uuid4().hex[:8]}{safe_ext}"
    dest = _ATTACH_DIR / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)

    try:
        with get_db() as conn:
            _validate_master_branch(conn, branch_id)
            cur = conn.execute(
                "INSERT INTO recon_attachment"
                "(branch_id,day,category,file_name,mime,size_bytes,file_path,note,uploaded_by) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (branch_id, day, (category or None), fn, file.content_type,
                 len(raw), rel_path, (note or None), (uploaded_by or None)))
            conn.commit()
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return {"id": cur.lastrowid, "file_name": fn, "size_bytes": len(raw)}


@router.get("/api/attachments/{att_id}/file")
def get_attachment_file(att_id: int):
    with get_db() as conn:
        r = conn.execute(
            "SELECT file_name, mime, file_path FROM recon_attachment WHERE id=?",
            (att_id,)).fetchone()
    if not r:
        raise HTTPException(404, "not found")
    fp = _ATTACH_DIR / r["file_path"] if r["file_path"] else None
    if not fp or not fp.exists():
        raise HTTPException(404, "file missing from storage")
    return Response(content=fp.read_bytes(),
                    media_type=r["mime"] or "application/octet-stream",
                    headers={"Content-Disposition": _inline_disposition(r["file_name"])})


def _inline_disposition(file_name: str | None) -> str:
    """Content-Disposition safe for any name: Thai names aren't latin-1 (a raw
    header 500s), so RFC 5987 — an ASCII fallback plus a UTF-8 filename*.
    Quotes, backslashes and control chars (CR/LF) are stripped."""
    from urllib.parse import quote
    name = re.sub(r'[\x00-\x1f\x7f"\\]', "", file_name or "").strip() or "attachment"
    if name.isascii():
        fallback = name
    else:
        ext = Path(name).suffix.encode("ascii", "ignore").decode()
        fallback = f"attachment{ext}"
    return f"inline; filename=\"{fallback}\"; filename*=UTF-8''{quote(name)}"


@router.delete("/api/attachments/{att_id}", dependencies=[Depends(require_write_token)])
def delete_attachment(att_id: int, request: Request):
    with get_db() as conn:
        r = conn.execute(
            "SELECT branch_id, day, file_name, file_path FROM recon_attachment WHERE id=?",
            (att_id,)).fetchone()
        if not r:
            raise HTTPException(404, "not found")
        _assert_period_open(conn, r["branch_id"], (r["day"] or "")[:7])
        # a deposit slip pointing at this photo must not dangle
        conn.execute("UPDATE recon_deposit_slip SET attachment_id=NULL WHERE attachment_id=?",
                     (att_id,))
        conn.execute("DELETE FROM recon_attachment WHERE id=?", (att_id,))
        _audit(conn, "attachment", att_id, "delete", _actor(request, None),
               [{"field": "file_name", "old": r["file_name"], "new": None},
                {"field": "branch_id", "old": r["branch_id"], "new": None},
                {"field": "day",       "old": r["day"],       "new": None}])
        conn.commit()
    # unlink only after the row delete committed — a failed delete keeps the file
    if r["file_path"]:
        (_ATTACH_DIR / r["file_path"]).unlink(missing_ok=True)
    return {"deleted": att_id}


class DepositSlip(BaseModel):
    branch_id:     int
    sale_day:      str                  # POS day D the cash belongs to
    amount:        float
    deposit_day:   str | None = None    # normally D+1 (before 13:00)
    attachment_id: int | None = None    # optional slip photo (recon_attachment)
    note:          str = ""
    created_by:    str = ""


@router.get("/api/deposit-slips")
def list_deposit_slips(branch_id: int, period_month: str):
    _validate_period(period_month)
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM recon_deposit_slip WHERE branch_id=? AND substr(sale_day,1,7)=? "
            "ORDER BY sale_day", (branch_id, period_month))]


@router.post("/api/deposit-slips", dependencies=[Depends(require_write_token)])
def upsert_deposit_slip(body: DepositSlip):
    """One slip per (branch, sale_day) — POSTing again replaces the amount."""
    if not _DAY_RE.match(body.sale_day or ""):
        raise HTTPException(400, "sale_day must be in 'YYYY-MM-DD' format.")
    if body.deposit_day and not _DAY_RE.match(body.deposit_day):
        raise HTTPException(400, "deposit_day must be in 'YYYY-MM-DD' format.")
    if body.amount <= 0:
        raise HTTPException(400, "amount must be positive.")
    with get_db() as conn:
        _validate_master_branch(conn, body.branch_id)
        _assert_period_open(conn, body.branch_id, body.sale_day[:7])
        old = conn.execute(
            "SELECT id, amount FROM recon_deposit_slip WHERE branch_id=? AND sale_day=?",
            (body.branch_id, body.sale_day)).fetchone()
        if old:
            conn.execute("""
                UPDATE recon_deposit_slip SET amount=?, deposit_day=?, attachment_id=?,
                    note=?, created_by=?, created_at=datetime('now') WHERE id=?
            """, (body.amount, body.deposit_day, body.attachment_id,
                  body.note or None, body.created_by or "staff", old["id"]))
            slip_id = old["id"]
            if float(old["amount"]) != body.amount:
                _audit(conn, "deposit_slip", slip_id, "update", body.created_by or "staff",
                       [{"field": "amount", "old": old["amount"], "new": body.amount}])
        else:
            cur = conn.execute("""
                INSERT INTO recon_deposit_slip
                    (branch_id, sale_day, deposit_day, amount, attachment_id, note, created_by)
                VALUES (?,?,?,?,?,?,?)
            """, (body.branch_id, body.sale_day, body.deposit_day, body.amount,
                  body.attachment_id, body.note or None, body.created_by or "staff"))
            slip_id = cur.lastrowid
            _audit(conn, "deposit_slip", slip_id, "create", body.created_by or "staff",
                   [{"field": "amount", "old": None, "new": body.amount}])
        conn.commit()
    return {"id": slip_id, "sale_day": body.sale_day, "amount": body.amount}


@router.delete("/api/deposit-slips/{slip_id}", dependencies=[Depends(require_write_token)])
def delete_deposit_slip(slip_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT branch_id, sale_day FROM recon_deposit_slip WHERE id=?", (slip_id,)).fetchone()
        if not row:
            raise HTTPException(404, "deposit slip not found")
        _assert_period_open(conn, row["branch_id"], row["sale_day"][:7])
        conn.execute("DELETE FROM recon_deposit_slip WHERE id=?", (slip_id,))
        _audit(conn, "deposit_slip", slip_id, "delete", "staff")
        conn.commit()
    return {"deleted": slip_id}


def _coupon_range(gran: str, period_month: str, day: str | None):
    """Resolve (date_from, date_to, label) for a coupon view. gran = day|week|month.
    Day strings compare lexicographically, so '-31' is a safe month upper bound."""
    if gran == "day":
        if not (day and _DAY_RE.match(day)):
            raise HTTPException(400, "gran=day needs a valid 'day' (YYYY-MM-DD)")
        return day, day, day
    if gran == "week":
        if not (day and _DAY_RE.match(day)):
            raise HTTPException(400, "gran=week needs a valid 'day' (YYYY-MM-DD)")
        d = _dt.strptime(day, "%Y-%m-%d").date()
        mon = d - _td(days=d.weekday())
        sun = mon + _td(days=6)
        return mon.isoformat(), sun.isoformat(), f"{mon.isoformat()} – {sun.isoformat()}"
    _validate_period(period_month)
    return period_month + "-01", period_month + "-31", period_month


def _coupon_prev(gran: str, period_month: str, day: str | None):
    """Previous comparable period (prev day / prev week / prev month) for MoM."""
    if gran == "day":
        d = _dt.strptime(day, "%Y-%m-%d").date() - _td(days=1)
        return d.isoformat(), d.isoformat(), d.isoformat()
    if gran == "week":
        d = _dt.strptime(day, "%Y-%m-%d").date()
        mon = d - _td(days=d.weekday() + 7)
        sun = mon + _td(days=6)
        return mon.isoformat(), sun.isoformat(), f"{mon.isoformat()} – {sun.isoformat()}"
    y, m = int(period_month[:4]), int(period_month[5:7])
    pm = f"{y-1:04d}-12" if m == 1 else f"{y:04d}-{m-1:02d}"
    return pm + "-01", pm + "-31", pm


def _coupon_series_map(conn, d_from, d_to, branch_id):
    """series_id → discount total for a date range (+ optional branch)."""
    w = ("WHERE cd.day BETWEEN ? AND ? AND COALESCE(b.branch_type,'master')='master' AND b.active=1")
    p = [d_from, d_to]
    if branch_id:
        w += " AND cd.branch_id=?"; p.append(branch_id)
    out = {}
    for r in conn.execute(f"""SELECT cd.coupon_series_id sid, SUM(cd.discount_amount) amt
                              FROM pos_coupon_day cd JOIN branches b ON b.branch_id=cd.branch_id
                              {w} GROUP BY cd.coupon_series_id""", p):
        out[r["sid"]] = r["amt"] or 0
    return out


@router.get("/api/coupon-series")
def coupon_series(period_month: str = "", gran: str = "month",
                  day: str | None = None, branch_id: int | None = None,
                  series_id: int | None = None):
    """COUPON detail by coupon series, at day / week / month grain (from
    pos_coupon_day). Floored to RECON_START_MONTH; master only. Pass series_id to
    drill one series (by_branch = that series per branch)."""
    if gran not in ("day", "week", "month"):
        raise HTTPException(400, "gran must be day, week or month")
    if gran == "month" and not period_month:
        raise HTTPException(400, "period_month required for gran=month")
    d_from, d_to, label = _coupon_range(gran, period_month, day)
    empty = {"gran": gran, "label": label, "range": [d_from, d_to],
             "series": [], "by_branch": [], "total": 0.0, "total_used": 0}
    if d_to < RECON_START_DAY:
        return empty
    if d_from < RECON_START_DAY:
        d_from = RECON_START_DAY
    with get_db() as conn:
        params = [d_from, d_to]
        where = ("WHERE cd.day BETWEEN ? AND ? "
                 "AND COALESCE(b.branch_type,'master')='master' AND b.active=1")
        if branch_id:
            where += " AND cd.branch_id=?"; params.append(branch_id)
        if series_id is not None:
            where += " AND cd.coupon_series_id=?"; params.append(series_id)
        try:
            rows = [dict(r) for r in conn.execute(f"""
                SELECT cd.coupon_series_id AS series_id,
                       SUM(cd.used_count)      AS used_count,
                       SUM(cd.discount_amount) AS discount_amount
                FROM pos_coupon_day cd JOIN branches b ON b.branch_id = cd.branch_id
                {where}
                GROUP BY cd.coupon_series_id
                ORDER BY discount_amount DESC
            """, params)]
        except sqlite3.OperationalError:
            return {**empty, "hint": "pos_coupon_day not found — run sync_mssql.py"}
        total = round(sum(r["discount_amount"] or 0 for r in rows), 2)
        total_used = int(sum(r["used_count"] or 0 for r in rows))
        name_map = {}
        try:
            for nr in conn.execute("SELECT coupon_series_id, name, code FROM coupon_series"):
                name_map[nr["coupon_series_id"]] = (nr["name"] or "", nr["code"] or "")
        except sqlite3.OperationalError:
            pass
        # Previous comparable period → MoM per series
        pf, pt, plabel = _coupon_prev(gran, period_month, day)
        if pf < RECON_START_DAY:
            pf = RECON_START_DAY
        prev_map = _coupon_series_map(conn, pf, pt, branch_id) if pt >= RECON_START_DAY else {}
        for r in rows:
            r["discount_amount"] = round(r["discount_amount"] or 0, 2)
            nm = name_map.get(r["series_id"], ("", ""))
            r["series_name"], r["series_code"] = nm[0], nm[1]
            prev = prev_map.get(r["series_id"], 0)
            r["prev_amount"] = round(prev, 2)
            r["mom_pct"] = round((r["discount_amount"] - prev) / prev * 100, 1) if prev else None
        by_branch = []
        if not branch_id:
            by_branch = [dict(r) for r in conn.execute(f"""
                SELECT b.branch_id, b.name AS branch_name,
                       SUM(cd.used_count)      AS used_count,
                       SUM(cd.discount_amount) AS discount_amount
                FROM pos_coupon_day cd JOIN branches b ON b.branch_id = cd.branch_id
                {where}
                GROUP BY b.branch_id
                ORDER BY discount_amount DESC
            """, params)]
            for r in by_branch:
                r["discount_amount"] = round(r["discount_amount"] or 0, 2)
        # Drill: per-day breakdown for a single series
        by_day = []
        if series_id is not None:
            by_day = [dict(r) for r in conn.execute(f"""
                SELECT cd.day,
                       SUM(cd.used_count)      AS used_count,
                       SUM(cd.discount_amount) AS discount_amount
                FROM pos_coupon_day cd JOIN branches b ON b.branch_id = cd.branch_id
                {where}
                GROUP BY cd.day
                ORDER BY cd.day
            """, params)]
            for r in by_day:
                r["discount_amount"] = round(r["discount_amount"] or 0, 2)
        return {"gran": gran, "label": label, "range": [d_from, d_to],
                "prev_label": plabel, "prev_range": [pf, pt],
                "period_month": period_month, "branch_id": branch_id,
                "series": rows, "by_branch": by_branch, "by_day": by_day,
                "total": total, "total_used": total_used,
                "prev_total": round(sum(prev_map.values()), 2)}
