# -*- coding: utf-8 -*-
"""Render docs/COST_REDUCTION_REPORT.html -> PDF via headless Chrome (Playwright).
High fidelity: waits for web fonts, prints background colours (charts), A4."""
import os, sys
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "docs", "COST_REDUCTION_REPORT.html")
PDF = os.path.join(ROOT, "docs", "COST_REDUCTION_REPORT.pdf")
url = "file:///" + HTML.replace("\\", "/")

def run():
    with sync_playwright() as p:
        # Prefer the installed Google Chrome; fall back to bundled chromium.
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception as e:
            print("chrome channel failed, trying bundled chromium:", e)
            browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)
        # ensure web fonts are fully loaded (falls back fast if offline)
        try:
            page.evaluate("() => document.fonts && document.fonts.ready")
            page.wait_for_timeout(800)
        except Exception:
            pass
        page.emulate_media(media="print")
        page.pdf(
            path=PDF,
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
        )
        browser.close()
    print("WROTE", PDF, "| size:", os.path.getsize(PDF), "bytes")

if __name__ == "__main__":
    run()
