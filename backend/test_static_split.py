"""P2 structure refactor: the SPA JS is split into 9 sequential slices under
/js/ (shared global scope — index.html must load 01→09 in order). /app.js
stays as a back-compat concatenation for stale cached shells. Everything is
served no-cache so deploys can never leave stale JS in browser caches."""

_SLICES = ["01-core.js", "02-mgt-report.js", "03-tabs-main.js", "04-report-cost.js",
           "05-pnl-settings.js", "06-features.js", "07-home.js", "08-guest-buffet.js",
           "09-deep-tools.js"]


def test_root_references_all_slices_in_order(client):
    html = client.get("/").text
    pos = [html.find(f'<script src="/js/{s}"></script>') for s in _SLICES]
    assert all(p >= 0 for p in pos), "shell must reference every slice"
    assert pos == sorted(pos), "slices must load in 01→09 order (shared global scope)"
    # the application code itself must not be inlined in the shell
    assert "function switchTab" not in html


def test_js_slices_served_nocache(client):
    for s in _SLICES:
        r = client.get(f"/js/{s}")
        assert r.status_code == 200, s
        assert "no-cache" in r.headers.get("cache-control", ""), s
    assert "function switchTab" in client.get("/js/01-core.js").text


def test_js_route_rejects_traversal(client):
    # NB: a literal "/js/.." never reaches the route — URL normalization
    # resolves it to "/" (the shell) before routing, which is harmless.
    for bad in ("..%2f..%2fmain.py", "x%2f..%2f..%2fmain.py", "MAIN.PY", "01-core.js.bak"):
        r = client.get(f"/js/{bad}")
        assert r.status_code in (404, 422), (bad, r.status_code)


def test_appjs_backcompat_concat(client):
    """A stale cached shell still loads /app.js — it must serve the full
    concatenation (all slices, load order) with no-cache."""
    r = client.get("/app.js")
    assert r.status_code == 200
    assert "function switchTab" in r.text            # slice 01
    assert "loadCohort" in r.text                    # a later slice
    assert "no-cache" in r.headers.get("cache-control", "")
