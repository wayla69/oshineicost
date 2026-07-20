"""Lean-overhaul Phase 4: the app JS lives in /app.js (split out of
index.html). The shell must reference it and the route must serve it with
no-cache so deploys can never leave stale JS in browser caches."""


def test_root_references_appjs(client):
    html = client.get("/").text
    assert '<script src="/app.js"></script>' in html
    # the application code itself must no longer be inlined in the shell
    assert "function switchTab" not in html


def test_appjs_served_nocache(client):
    r = client.get("/app.js")
    assert r.status_code == 200
    assert "function switchTab" in r.text
    assert "no-cache" in r.headers.get("cache-control", "")
