"""db_backup.py (AUD-2): a backup that a cloud folder can hold, a restore test
that opens exactly what the folder holds, and rotation that keeps evidence
without filling the folder."""
import gzip
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import db_backup as B  # noqa: E402


def _live_db(path: Path, bills=3):
    con = sqlite3.connect(str(path))
    con.executescript("""
      CREATE TABLE recon_uploads(id INTEGER PRIMARY KEY, name TEXT);
      CREATE TABLE recon_bank_lines(id INTEGER PRIMARY KEY);
      CREATE TABLE recon_audit_log(id INTEGER PRIMARY KEY);
      CREATE TABLE sales_transaction(receipt_no TEXT);
      CREATE TABLE app_user(username TEXT);
    """)
    con.execute("INSERT INTO recon_uploads(name) VALUES('stmt')")
    con.executemany("INSERT INTO sales_transaction VALUES(?)", [(f"r{i}",) for i in range(bills)])
    con.execute("INSERT INTO app_user VALUES('owner')")
    con.commit(); con.close()


def test_gzip_backup_and_restore_test_of_the_gz(tmp_path, capsys):
    live = tmp_path / "oshinei.db"; _live_db(live)
    out = tmp_path / "cloud"
    rc = B.cmd_backup(SimpleNamespace(db=str(live), out=str(out), stamp="2026-09-04_0730",
                                      gzip=True, keep_daily=0, keep_monthly=0, today=""))
    assert rc == 0
    gz = out / "oshinei_2026-09-04_0730.db.gz"
    assert gz.exists() and not (out / "oshinei_2026-09-04_0730.db").exists()
    man = (out / "oshinei_2026-09-04_0730.manifest.txt").read_text(encoding="utf-8")
    assert "gz_sha256:" in man and "sales_transaction: 3" in man
    with gzip.open(gz, "rb") as f:
        assert f.read(16) == b"SQLite format 3\x00"
    # the restore test decompresses the .gz and checks it
    rc = B.cmd_verify(SimpleNamespace(backup=str(gz)))
    out_text = capsys.readouterr().out
    assert rc == 0 and "PASS" in out_text and "integrity_check: ok" in out_text


def test_verify_fails_on_a_backup_without_sales(tmp_path, capsys):
    live = tmp_path / "oshinei.db"; _live_db(live, bills=0)
    out = tmp_path / "cloud"
    B.cmd_backup(SimpleNamespace(db=str(live), out=str(out), stamp="2026-09-04_0730",
                                 gzip=False, keep_daily=0, keep_monthly=0, today=""))
    rc = B.cmd_verify(SimpleNamespace(backup=str(out / "oshinei_2026-09-04_0730.db")))
    assert rc == 1 and "FAIL" in capsys.readouterr().out


def test_prune_keeps_recent_days_and_the_first_backup_of_each_month(tmp_path):
    out = tmp_path / "cloud"; out.mkdir()
    days = ["2026-06-01", "2026-06-15", "2026-07-01", "2026-07-20", "2026-08-01", "2026-08-30",
            "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]
    for d in days:
        (out / f"oshinei_{d}_0730.db.gz").write_bytes(b"x")
        (out / f"oshinei_{d}_0730.manifest.txt").write_text("m")
    (out / "notes.txt").write_text("keep me")
    removed = B.prune(out, keep_daily=3, keep_monthly=3, today=B._dt.date(2026, 9, 4))
    left = sorted(p.name for p in out.iterdir())
    # last 3 days: 09-02, 09-03, 09-04; first of the last 3 months: 07-01, 08-01, 09-01
    assert [n for n in left if n.endswith(".gz")] == [
        "oshinei_2026-07-01_0730.db.gz", "oshinei_2026-08-01_0730.db.gz", "oshinei_2026-09-01_0730.db.gz",
        "oshinei_2026-09-02_0730.db.gz", "oshinei_2026-09-03_0730.db.gz", "oshinei_2026-09-04_0730.db.gz"]
    assert sorted(p.name for p in removed) == [
        "oshinei_2026-06-01_0730.db.gz", "oshinei_2026-06-15_0730.db.gz",
        "oshinei_2026-07-20_0730.db.gz", "oshinei_2026-08-30_0730.db.gz"]
    assert not (out / "oshinei_2026-06-01_0730.manifest.txt").exists()   # manifests go with their file
    assert (out / "notes.txt").exists()                                  # never touches other files


def test_prune_with_no_limits_keeps_everything(tmp_path):
    out = tmp_path / "cloud"; out.mkdir()
    (out / "oshinei_2020-01-01_0730.db.gz").write_bytes(b"x")
    assert B.prune(out, 0, 0, today=B._dt.date(2026, 9, 4)) == []
    assert (out / "oshinei_2020-01-01_0730.db.gz").exists()
