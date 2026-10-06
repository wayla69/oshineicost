"""P2 structure refactor: the SPA JS is split into sequential slices under
/js/ (shared global scope — index.html must load them in name order; the
09-deep-tools slice became 09/09b/09c/09d/09e on 2026-09-05). /app.js
stays as a back-compat concatenation for stale cached shells. Everything is
served no-cache so deploys can never leave stale JS in browser caches."""

_SLICES = ["01-core.js", "02-mgt-report.js", "03-tabs-main.js", "04-report-cost.js",
           "05-pnl-settings.js", "06-features.js", "07-home.js", "08-guest-buffet.js",
           "09-analysis-tabs.js", "09b-cost-pnl.js", "09c-mobile.js", "09d-bom-tools.js",
           "09e-menu-compare-bom.js", "10-ordering.js", "11-feedback.js", "12-checklist.js",
           "13-bom-center.js", "14-branch.js"]


def test_root_references_all_slices_in_order_with_hashes(client):
    import re
    r = client.get("/")
    html = r.text
    # the shell itself must never be cached — it carries the js content hashes
    assert "no-cache" in r.headers.get("cache-control", "")
    pos = []
    for s in _SLICES:
        m = re.search(rf'<script src="/js/{re.escape(s)}\?v=[0-9a-f]{{8}}"></script>', html)
        assert m, f"shell must reference {s} with a content hash"
        pos.append(m.start())
    assert pos == sorted(pos), "slices must load in name order (shared global scope)"
    # the application code itself must not be inlined in the shell
    assert "function switchTab" not in html


def test_js_slices_immutable_with_hash_nocache_without(client):
    import re
    html = client.get("/").text
    for s in _SLICES:
        v = re.search(rf'/js/{re.escape(s)}\?v=([0-9a-f]{{8}})', html).group(1)
        # current hash → 1-year immutable (repeat loads skip revalidation)
        r = client.get(f"/js/{s}?v={v}")
        assert r.status_code == 200, s
        assert "immutable" in r.headers.get("cache-control", ""), s
        # no hash / stale hash → the safe no-cache behaviour
        assert "no-cache" in client.get(f"/js/{s}").headers.get("cache-control", ""), s
        assert "no-cache" in client.get(f"/js/{s}?v=deadbeef").headers.get("cache-control", ""), s
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
