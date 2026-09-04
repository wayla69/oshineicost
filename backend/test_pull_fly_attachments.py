"""pull_fly_attachments.py — the recon evidence files live on the Fly volume,
outside oshinei.db; the backup must carry them, and must not write empty tarballs."""
import base64
import io
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import pull_fly_attachments as P  # noqa: E402


class _Run:
    """Stand-in for subprocess.run: answers the count and the transfer commands."""
    def __init__(self, n_files, tgz=None, count_rc=0, transfer_rc=0):
        self.n_files, self.tgz, self.count_rc, self.transfer_rc = n_files, tgz, count_rc, transfer_rc
        self.calls = []

    def __call__(self, cmd, capture_output, text, env):
        self.calls.append(cmd)
        c = cmd[-1]
        if "wc -l" in c:
            return type("R", (), {"returncode": self.count_rc, "stdout": f"Connecting to fdaa::1...\n{self.n_files}\n", "stderr": ""})()
        b64 = base64.b64encode(self.tgz or b"").decode()
        return type("R", (), {"returncode": self.transfer_rc, "stdout": f"Connecting to fdaa::1...\n{b64}\n", "stderr": ""})()


def _tgz(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name); info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_pull_writes_the_tarball_and_checks_the_member_count(tmp_path, monkeypatch, capsys):
    tgz = _tgz({"attachments/1/slip.jpg": b"jpg", "attachments/2/receipt.pdf": b"pdf"})
    run = _Run(2, tgz)
    monkeypatch.setattr(P.subprocess, "run", run)
    dest = P.pull("app", tmp_path / "cloud", "2026-09-05_0730", fly="flyctl", env={})
    assert dest == tmp_path / "cloud" / "attachments_2026-09-05_0730.tgz"
    assert dest.read_bytes() == tgz
    assert "2 file(s)" in capsys.readouterr().out
    assert [c[-1] for c in run.calls] == ["sh -c 'find /data/attachments -type f 2>/dev/null | wc -l'",
                                          "sh -c 'tar czf - -C /data attachments | base64 -w0'"]


def test_pull_writes_nothing_for_an_empty_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(P.subprocess, "run", _Run(0))
    assert P.pull("app", tmp_path / "cloud", "s", fly="flyctl", env={}) is None
    assert not (tmp_path / "cloud").exists()
    assert "empty" in capsys.readouterr().out


def test_pull_reports_a_failed_console_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(P.subprocess, "run", _Run(3, count_rc=1))
    assert P.pull("app", tmp_path / "cloud", "s", fly="flyctl", env={}) is None
    assert "could not count" in capsys.readouterr().out


def test_pull_rejects_a_garbled_transfer(tmp_path, monkeypatch, capsys):
    run = _Run(1, b"not a tarball at all, but long enough to look like base64 after encoding")
    monkeypatch.setattr(P.subprocess, "run", run)
    assert P.pull("app", tmp_path / "cloud", "s", fly="flyctl", env={}) is None
    assert "unreadable" in capsys.readouterr().out
