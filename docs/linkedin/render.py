from playwright.sync_api import sync_playwright
import sys, os
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
                          args=["--no-sandbox","--font-render-hinting=none"])
    pg = b.new_page(viewport={"width":1360,"height":1400}, device_scale_factor=2)
    pg.goto("file://"+os.path.abspath(sys.argv[1]))
    pg.wait_for_timeout(900)
    pg.screenshot(path=sys.argv[2], full_page=True)
    b.close()
