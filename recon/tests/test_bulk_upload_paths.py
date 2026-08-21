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
    "RUB_Statements KB_SA_05.2569.pdf",   # branch code we do not map
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
