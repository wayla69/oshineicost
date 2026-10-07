"""Filename-driven routing in bulk_recon_upload.

Every bank statement we hold sits in a flat "May" bundle with no branch/channel/
month folders above it, so the branch, month and provider have to be read off
the filename. These lock that contract -- and the Thai-year conversion, which
used to turn "05.69" into 2612-05.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

bru = pytest.importorskip("bulk_recon_upload")                   # needs requests


@pytest.mark.parametrize("raw,expected", [
    (2569, 2026),   # Thai Buddhist, 4-digit
    (2026, 2026),   # Gregorian, 4-digit
    (69, 2026),     # Thai Buddhist, 2-digit -> 2569 BE
    (26, 2026),     # Gregorian, 2-digit (BE reading would be 1983)
])
def test_normalize_year(raw, expected):
    assert bru.normalize_year(raw) == expected


@pytest.mark.parametrize("folder,expected", [
    ("05.2026", "2026-05"),
    ("05.2569", "2026-05"),
    ("05.69", "2026-05"),
    ("2025", None),        # year-only folders are not months
    ("2026", None),
    ("13.2026", None),     # not a month
    ("junk", None),
])
def test_parse_month(folder, expected):
    assert bru.parse_month(folder) == expected


@pytest.mark.parametrize("name,branch_id,provider,month", [
    ("05.69-Statement KTB CNX - รหัสเปิด 0505562007318.pdf", 7, "KTB", "2026-05"),
    ("05.69-Statement KTB SRN - รหัสเปิด 0325564000291.pdf", 24, "KTB", "2026-05"),
    ("CNX_Statements KB_CA_05.2569.pdf", 7, "KBank", "2026-05"),
    ("UDN_Statements KB_SA_05.2569.pdf", 25, "KBank", "2026-05"),
])
def test_parse_loose_file_reads_the_filename(tmp_path, name, branch_id, provider, month):
    f = tmp_path / name
    f.write_bytes(b"%PDF-1.4\n")
    job = bru.parse_loose_file(f, "May")
    assert job is not None
    assert job["branch_id"] == branch_id
    assert job["source_type"] == "bank"
    assert job["provider"] == provider
    assert job["period_month"] == month


@pytest.mark.parametrize("name", [
    "CNX_Statements KB_CA.pdf",           # no month
    "CNX_mystery_05.2569.pdf",            # no document type
])
def test_parse_loose_file_refuses_what_it_cannot_identify(tmp_path, name):
    f = tmp_path / name
    f.write_bytes(b"%PDF-1.4\n")
    assert bru.parse_loose_file(f, "May") is None


def test_collect_uploads_walks_loose_bundles_but_not_unknown_branch_folders(tmp_path):
    # a flat bundle -> read per file
    may = tmp_path / "May"
    may.mkdir()
    (may / "CNX_Statements KB_CA_05.2569.pdf").write_bytes(b"%PDF-1.4\n")
    (may / "E-TAX_INVOICE_CARD_401012832421001_010526E00005640_20260501.PDF").write_bytes(b"x")

    # numbered like a branch folder we don't know -> skipped wholesale, so a
    # genuinely new branch is reported rather than silently half-imported
    unknown = tmp_path / "7.XYZ" / "1.KBANK" / "05.2026"
    unknown.mkdir(parents=True)
    (unknown / "CNX_Statements KB_CA_05.2569.pdf").write_bytes(b"%PDF-1.4\n")

    jobs = bru.collect_uploads(tmp_path)

    assert [j["file_path"].name for j in jobs] == ["CNX_Statements KB_CA_05.2569.pdf"]
    assert jobs[0]["channel"] == "May"          # came from the bundle, not 7.XYZ
    assert jobs[0]["branch_id"] == 7


def test_renshin_ubon_never_lands_on_an_oshinei_branch(tmp_path):
    """RUB is Renshin Ubon -- a different brand that shares the account holder
    and the KBank branch with UBN. Booking it against branch 26 would inflate
    Ubon's bank side against POS with takings never rung up there."""
    f = tmp_path / "RUB_Statements KB_SA_05.2569.pdf"
    f.write_bytes(b"%PDF-1.4")
    assert bru.parse_loose_file(f, "May") is None
    assert "RUB" not in bru.BRANCH_CODE_MAP
    assert "RUB" in bru.OUT_OF_SCOPE_CODES


# ── the upload path itself (2026-10-07): main() used an undefined `session` after #79,
#    so every real run crashed with NameError once it reached the upload step ──

def test_token_from_backup_reads_the_first_line(tmp_path):
    f = tmp_path / ".seed_tokens.txt"
    f.write_text("# note\nadmin:aaa111\nAR:bbb222\n", encoding="utf-8")
    assert bru.token_from_backup(f) == "aaa111"
    assert bru.token_from_backup(tmp_path / "missing.txt") is None


def test_main_uploads_with_a_session_when_recon_login_is_on(tmp_path, monkeypatch):
    seen = {}
    job = {"branch_id": 4, "branch_name": "2.KR1", "period_month": "2026-05",
           "source_type": "bank", "file_path": tmp_path / "x.pdf"}
    monkeypatch.setattr(bru, "recon_data_dir", lambda folder=None: tmp_path)
    monkeypatch.setattr(bru, "collect_uploads", lambda folder: [job])
    monkeypatch.setattr(bru, "recon_login_enabled", lambda session, url: True)
    monkeypatch.setattr(bru, "login", lambda *a, **k: pytest.fail("dashboard login must be skipped"))

    def fake_upload(session, base_url, token, j, verbose=False):
        seen.update(session=session, token=token)
        return {"status": "OK", "file": j["file_path"].name}
    monkeypatch.setattr(bru, "upload_file", fake_upload)
    monkeypatch.setattr(sys, "argv", ["bulk_recon_upload.py", "--token", "tok123", "--workers", "1"])
    try:
        bru.main()
    except SystemExit as e:
        assert e.code in (0, None)
    assert seen["token"] == "tok123" and hasattr(seen["session"], "post")
