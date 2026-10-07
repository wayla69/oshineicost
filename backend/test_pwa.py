"""The web app manifest and icons that let a branch phone keep the app on its
home screen (PR-15): served under /pwa, linked from index.html."""
import json


def test_manifest_and_icons_are_served(client):
    r = client.get("/pwa/manifest.json")
    assert r.status_code == 200, r.text
    m = json.loads(r.text)
    assert m["display"] == "standalone" and m["start_url"] == "/" and m["short_name"]
    assert len(m["icons"]) >= 2
    for ic in m["icons"]:
        ri = client.get(ic["src"])
        assert ri.status_code == 200, ic["src"]
        assert ri.headers["content-type"].startswith("image/png")
        assert ri.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert any(ic.get("purpose") == "maskable" for ic in m["icons"])
    assert client.get("/pwa/apple-touch-icon.png").status_code == 200


def test_index_links_the_manifest(client):
    html = client.get("/").text
    assert 'rel="manifest" href="/pwa/manifest.json"' in html
    assert 'rel="apple-touch-icon"' in html and 'name="theme-color"' in html
