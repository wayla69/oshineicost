"""The tab panels are served as one bundle (Phase 4, 2026-09).

index.html used to inline all 48 panels — a 5,300-line region where every
parallel edit collided. They now live in tabs/NN-<name>.html and reach the
browser concatenated at /tabs.html, loaded once at boot before init() runs.
These pin the contract the shell and the loader rely on.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_bundle_carries_every_panel_in_file_order(client):
    r = client.get("/tabs.html")
    assert r.status_code == 200
    ids = re.findall(r'<div id="tab-([a-z0-9]+)"', r.text)
    assert len(ids) == 61, len(ids)          # 56-franchise (ร้านของฉัน, PR-L), 57-launcher + 58-allpages (PR-D1), 59-fmsg (PR-F4), 60-hvmonitor, 61-pricetier (PR-AT)
    files = sorted(p.name for p in (ROOT / "tabs").glob("*.html"))
    assert [re.sub(r"^\d+-|\.html$", "", f) for f in files] == ids, "bundle order must be file order"
    assert ids[0] == "home"
    assert r.headers.get("X-Tabs-Version"), "the bundle must advertise its content hash"


def test_bundle_is_immutable_only_under_its_own_hash(client):
    h = client.get("/tabs.html").headers["X-Tabs-Version"]
    fresh = client.get("/tabs.html", params={"v": h})
    stale = client.get("/tabs.html", params={"v": "0000000000"})
    assert "immutable" in fresh.headers["Cache-Control"]
    assert "no-cache" in stale.headers["Cache-Control"]
    assert fresh.text == stale.text


def test_shell_points_at_the_matching_bundle(client):
    """A browser may keep a cached shell; the version it embeds must fetch the
    bundle that matches it, never a mismatched one."""
    shell = client.get("/").text
    m = re.search(r'<div id="tabsRoot" data-tabs-v="([0-9a-f]{10})"></div>', shell)
    assert m, "shell must carry tabsRoot with the bundle hash"
    assert m.group(1) == client.get("/tabs.html").headers["X-Tabs-Version"]
    assert 'id="tab-home"' not in shell, "panels must no longer be inlined in the shell"


def test_fragments_reproduce_the_original_region_shape():
    """Each fragment is exactly one panel: it opens with (at most a banner and)
    its <div id="tab-…"> and contains no other panel."""
    for p in sorted((ROOT / "tabs").glob("*.html")):
        text = p.read_text(encoding="utf-8")
        ids = re.findall(r'^<div id="tab-([a-z0-9]+)"', text, re.M)
        assert ids == [re.sub(r"^\d+-|\.html$", "", p.name)], p.name
