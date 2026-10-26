"""The prebuilt Tailwind sheet (2026-10-26): the shell no longer runs vendor/tailwind.js in the browser; it links
vendor/tailwind.build.css (`npm run build:css` in tests/e2e, from tailwind.config.js) with its content hash, at the
end of <head> where the runtime injected its <style>, and the service worker precaches that exact URL.
Whether the committed sheet is current is checked in CI (`npm run check:css`, .github/workflows/test.yml)."""
import re

import main


def test_shell_links_the_versioned_sheet_and_not_the_runtime(client):
    html = client.get("/").text
    assert "_loadVendor('vendor/tailwind.js'" not in html and "cdn.tailwindcss.com" not in html
    assert not re.search(r'<script[^>]+tailwind', html)
    m = re.search(r'<link rel="stylesheet" href="(/vendor/tailwind\.build\.css\?v=([0-9a-f]{8}))">', html)
    assert m, "the shell links the prebuilt sheet with its content hash"
    assert m.group(2) == main._tw_css_hash()
    # the cascade position the runtime had: after every stylesheet of <head>, right before </head>
    head = html[: html.index("</head>")]
    assert head.rstrip().endswith(m.group(0)) or head.rindex(m.group(0)) > head.rindex("theme-polish.css")
    assert html.count('href="/vendor/tailwind.build.css') == 1


def test_sheet_is_served_and_precached(client):
    url = main._tw_css_url()
    r = client.get(url)
    assert r.status_code == 200 and "text/css" in r.headers["content-type"]
    assert ".hidden{display:none}" in r.text and "--tw-border-spacing-x" in r.text
    sw = client.get("/sw.js").text
    assert "__TW_CSS__" not in sw and f"'{url}'" in sw


def test_build_id_follows_the_sheet(monkeypatch):
    before = main._build_id()
    monkeypatch.setattr(main, "_tw_css_hash", lambda: "deadbeef")
    assert main._build_id() != before
