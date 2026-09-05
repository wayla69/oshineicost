# -*- coding: utf-8 -*-
"""AUD-2 part 3 — the Fly-only tables reach the off-site backup: the export is
token-gated and streams every preserved table; the puller writes the file and
its manifest, the restore test restores it into memory and compares counts,
`restore` refills a real DB, and old files are pruned."""
import gzip
import io
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import pull_fly_state as P  # noqa: E402


def _wipe(con, table):
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        con.execute(f"DELETE FROM {table}")


def _clean_voices():
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    _wipe(con, "customer_feedback")
    con.commit(); con.close()


def _parse(content: bytes):
    lines = [json.loads(ln) for ln in gzip.decompress(content).decode("utf-8").splitlines() if ln.strip()]
    tables = {o["table"]: o["columns"] for o in lines if "table" in o}
    rows = [o for o in lines if "t" in o]
    done = next(o for o in lines if "done" in o)
    return tables, rows, done


def test_export_is_token_gated_and_streams_every_preserved_table(client, monkeypatch):
    monkeypatch.delenv("OSHINEI_SEED_TOKEN", raising=False)
    r = client.post("/api/feedback/upload", json={"rows": [{"branch": "BranchA", "date": "2026-09-01", "source": "Google", "rating": 1,
                                                            "text": "รอนานมาก", "external_id": "fs1"}], "by": "test"})
    assert r.status_code == 200
    try:
        assert client.get("/api/_admin/export-state", params={"token": "x"}).status_code == 404       # endpoint off without a token
        monkeypatch.setenv("OSHINEI_SEED_TOKEN", "backup:tok")
        assert client.get("/api/_admin/export-state").status_code == 404
        assert client.get("/api/_admin/export-state", params={"token": "wrong"}).status_code == 404
        r = client.get("/api/_admin/export-state", params={"token": "tok"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/gzip")
        tables, rows, done = _parse(r.content)
        assert "customer_feedback" in tables and "app_user" in tables          # checklist_* appear once that router has run
        assert all(t.startswith(("feedback_", "checklist_", "recon_")) or t in ("app_user", "customer_feedback", "branch_targets") for t in tables)
        assert "sales_transaction" not in tables and "skus" not in tables
        voices = [o for o in rows if o["t"] == "customer_feedback"]
        assert len(voices) == 1 and voices[0]["r"][tables["customer_feedback"].index("text")] == "รอนานมาก"
        assert done["counts"]["customer_feedback"] == 1 and done["counts"]["app_user"] >= 1 and len(done["exported_at"]) == 19
        # the export bypasses the login gate (machine-to-machine); the app's own reads do not
        monkeypatch.setenv("OSHINEI_AUTH", "1"); monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret")
        assert client.get("/api/_admin/export-state", params={"token": "tok"}).status_code == 200
        assert client.get("/api/feedback/summary").status_code == 401
    finally:
        _clean_voices()


def test_pull_verify_restore_and_prune(client, monkeypatch, tmp_path):
    monkeypatch.delenv("OSHINEI_SEED_TOKEN", raising=False)
    client.post("/api/feedback/upload", json={"rows": [{"branch": "BranchA", "date": "2026-09-02", "source": "Google", "rating": 5, "text": "ดีมาก", "external_id": "fs2"},
                                                        {"branch": "BranchB", "date": "2026-09-03", "source": "Google", "rating": 2, "text": "ช้า", "external_id": "fs3"}], "by": "test"})
    try:
        monkeypatch.setenv("OSHINEI_SEED_TOKEN", "tok")
        seen_urls = []

        def fake_open(url, timeout=300):
            seen_urls.append(url)
            path = url.split("https://app", 1)[1]
            r = client.get(path)
            assert r.status_code == 200, r.text
            return io.BytesIO(r.content)
        monkeypatch.setattr(P, "_open", fake_open)
        out = tmp_path / "bk"
        assert P.cmd_pull(SimpleNamespace(out=str(out), stamp="2026-09-05_0730", api="https://app", token=None, keep=14)) == 0
        assert seen_urls[0].startswith("https://app/api/_admin/export-state?token=tok")
        f = out / "fly_state_2026-09-05_0730.ndjson.gz"
        man = (out / "fly_state_2026-09-05_0730.manifest.txt").read_text(encoding="utf-8")
        assert f.exists() and "customer_feedback: 2" in man and "sha256: " in man
        assert P.cmd_verify(SimpleNamespace(file=str(f))) == 0
        # restore into an empty DB creates the tables; into a DB with the table it refills them
        db = tmp_path / "restore.db"
        con = sqlite3.connect(str(db)); con.execute("CREATE TABLE customer_feedback(id INTEGER PRIMARY KEY, text TEXT)")
        con.execute("INSERT INTO customer_feedback(text) VALUES ('stale')"); con.commit(); con.close()
        assert P.cmd_restore(SimpleNamespace(file=str(f), db=str(db), tables="customer_feedback,app_user")) == 0
        con = sqlite3.connect(str(db))
        assert sorted(r[0] for r in con.execute("SELECT text FROM customer_feedback")) == ["ช้า", "ดีมาก"]
        assert con.execute("SELECT COUNT(*) FROM app_user").fetchone()[0] >= 1
        assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='checklist_state'").fetchone()   # only the asked tables
        con.close()
        # a truncated file fails the restore test; pruning keeps the newest N with their manifests
        bad = out / "fly_state_2026-09-04_0730.ndjson.gz"
        bad.write_bytes(gzip.compress(b'{"table": "customer_feedback", "columns": ["id"]}\n{"t": "customer_feedback", "r": [1]}\n'))
        assert P.cmd_verify(SimpleNamespace(file=str(bad))) == 1
        (out / "fly_state_2026-09-03_0730.ndjson.gz").write_bytes(bad.read_bytes())
        removed = P.prune(out, keep=2)
        assert removed == ["fly_state_2026-09-03_0730.ndjson.gz"] and f.exists() and bad.exists()
        assert P.cmd_pull(SimpleNamespace(out=str(out), stamp="2026-09-06_0730", api="https://app", token=None, keep=2)) == 0
        assert sorted(p.name for p in out.glob("fly_state_*.ndjson.gz")) == ["fly_state_2026-09-05_0730.ndjson.gz", "fly_state_2026-09-06_0730.ndjson.gz"]
    finally:
        _clean_voices()
