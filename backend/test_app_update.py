"""An open page learns that a deploy happened (fix/app-update): the shell carries its build
(<meta name="app-build">), GET /api/meta/build answers the build served now, and sw.js's
BUILD is the same id — one function, main._build_id()."""
import re
from pathlib import Path

import main

ROOT = Path(__file__).resolve().parents[1]


def test_build_endpoint_equals_build_id(client):
    r = client.get("/api/meta/build")
    assert r.status_code == 200
    assert r.json() == {"build": main._build_id()}
    assert "no-store" in r.headers["cache-control"]


def test_shell_meta_and_worker_carry_the_same_build(client):
    b = main._build_id()
    html = client.get("/").text
    m = re.search(r'<meta name="app-build" content="([0-9a-f]+)">', html)
    assert m and m.group(1) == b
    assert html.index('name="app-build"') < html.index("</head>")
    sw = client.get("/sw.js").text
    assert f"const BUILD = '{b}';" in sw


def test_build_follows_the_shell_file(monkeypatch, tmp_path):
    """A deploy that changes only index.html is a new build too."""
    a = main._build_id()
    alt = tmp_path / "index.html"
    alt.write_text(main.INDEX.read_text(encoding="utf-8") + "\n<!-- changed -->", encoding="utf-8")
    monkeypatch.setattr(main, "INDEX", alt)
    assert main._build_id() != a


def test_build_endpoint_is_open_without_a_session(client, monkeypatch):
    """The login screen updates too: the id is a content hash, no data."""
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    client.cookies.clear()
    assert client.get("/api/meta/build").status_code == 200
    assert client.get("/api/meta/data-window").status_code == 401     # the rest of /api/meta stays behind the login


def test_worker_retries_the_page_and_takes_over():
    sw = (ROOT / "sw.js").read_text(encoding="utf-8")
    assert "self.skipWaiting()" in sw and "self.clients.claim()" in sw
    assert "caches.delete(k)" in sw                                  # old shell caches go in activate
    body = sw[sw.index("async function pageNetworkFirst"):sw.index("self.addEventListener('fetch'")]
    assert "setTimeout(r, 2000)" in body and "i < 2" in body          # one retry after 2 s before the cache
    assert "'skip-waiting'" in sw


def test_page_checks_the_build_on_foreground():
    core = (ROOT / "js" / "01-core.js").read_text(encoding="utf-8")
    tabs = (ROOT / "js" / "03-tabs-main.js").read_text(encoding="utf-8")
    blk = core[core.index("// ── A new version after a deploy"):core.index("// A level-1 chip")]
    assert "/api/meta/build" in blk and "cache: 'no-store'" in blk
    assert "10 * 60 * 1000" in blk                                     # at most every 10 min while visible
    assert "oshinei_upd_build" in blk                                  # the loop guard
    assert "navigator.onLine === false" in blk                         # never while offline
    assert "reg.update()" in blk                                       # the new worker first
    assert "มีเวอร์ชันใหม่ · แตะเพื่อโหลด" in blk
    for w in ("กรุณา", "ระบบจะ", "ฉบับร่าง"):
        assert w not in blk
    vis = tabs[tabs.index("document.addEventListener('visibilitychange'"):]
    assert "appUpdateCheck()" in vis[:vis.index("// ── Init")]
