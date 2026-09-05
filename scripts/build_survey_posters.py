#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The QR survey poster pack — one A5 page per branch, ready to print (E3 go-live).

Logs into the live app (backend/.refresh_login.txt), creates the survey link of
every branch that has none yet, then renders every poster into ONE PDF through
Chrome headless (the QR is drawn in the page by qrcodejs from cdnjs, so the
machine needs internet). Also writes a CSV of branch / code / link for the
office. Nothing here is committed: the codes are the branches' credentials.

    py scripts/build_survey_posters.py --out "C:/Users/ASUS/OneDrive/Oshinei Backups"
    py scripts/build_survey_posters.py --api http://localhost:8021 --out <dir>     # against a dev server
"""
import argparse
import csv
import html
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import fetch_feedback as ff  # noqa: E402
import refresh_fly_db as rf  # noqa: E402

CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")

CSS = """
@page { size: A5 portrait; margin: 10mm }
body { margin: 0; font-family: 'Segoe UI', Tahoma, system-ui, sans-serif; color: #1f2937; background: #fff }
.sheet { width: 128mm; height: 190mm; padding: 10mm 8mm; box-sizing: border-box; text-align: center; page-break-after: always; break-after: page }
.sheet:last-child { page-break-after: auto }
.brand { font-weight: 800; color: #0f766e; letter-spacing: .08em; font-size: 14px }
h1 { font-size: 26px; margin: 6px 0 2px } .sub { color: #4b5563; font-size: 14px; margin-bottom: 9mm }
.qr { display: inline-block; padding: 6mm; border: 3px solid #0f766e; border-radius: 16px } .qr img, .qr canvas { display: block }
.link { font-family: Consolas, monospace; font-size: 12px; color: #374151; margin-top: 5mm; word-break: break-all }
.branch { font-size: 18px; font-weight: 700; margin-top: 4mm }
.foot { color: #6b7280; font-size: 12px; margin-top: 6mm }
"""


def poster_html(links: list) -> str:
    sheets, scripts = [], []
    for i, l in enumerate(links):
        sheets.append(f"""<div class="sheet"><div class="brand">OSHINEI · {html.escape(l['branch'])}</div>
<h1>วันนี้เป็นอย่างไรบ้าง?</h1><div class="sub">สแกนเพื่อบอกเรา ใช้เวลาไม่ถึง 20 วินาที<br>ทุกคำตอบถึงผู้จัดการสาขาโดยตรง</div>
<div class="qr" id="qr{i}"></div><div class="branch">สาขา{html.escape(l['branch'])}</div>
<div class="link">{html.escape(l['url'])}</div><div class="foot">ขอบคุณที่ช่วยให้เราดีขึ้นทุกวัน 🙏 · รหัส {html.escape(l['code'])}</div></div>""")
        scripts.append(f"new QRCode(document.getElementById('qr{i}'), {{text: {l['url']!r}, width: 300, height: 300, correctLevel: QRCode.CorrectLevel.M}});")
    return ("<!doctype html><html lang='th'><head><meta charset='utf-8'><title>QR survey posters</title>"
            f"<style>{CSS}</style></head><body>{''.join(sheets)}"
            "<script src='https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js'></script>"
            f"<script>{' '.join(scripts)}</script></body></html>")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="QR survey poster pack (one A5 page per branch)")
    ap.add_argument("--api", default=ff.DEFAULT_API)
    ap.add_argument("--out", required=True, help="folder for the PDF, the HTML and the CSV")
    ap.add_argument("--no-create", action="store_true", help="only render the links that exist")
    args = ap.parse_args(argv)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    login = rf.get_login(types.SimpleNamespace(user=None, password=None))
    sink = ff.ApiSink(args.api, ff.resolve_token(None), login=login)
    if not args.no_create:
        made = sink._call("POST", "/api/feedback/survey-links", {"all": True, "by": "poster-pack"})
        print(f"links created for branches without one: {made.get('created', 0)}")
    links = [l for l in (sink._call("GET", "/api/feedback/survey-links") or {}).get("links", []) if l.get("code") and l.get("active", True)]
    if not links:
        print("✗ no active links"); return 1
    links.sort(key=lambda l: l["branch"])
    stamp = ff.datetime.datetime.now().strftime("%Y-%m-%d")
    html_path, pdf_path, csv_path = out / f"SURVEY_POSTERS_{stamp}.html", out / f"SURVEY_POSTERS_{stamp}.pdf", out / f"SURVEY_LINKS_{stamp}.csv"
    html_path.write_text(poster_html(links), encoding="utf-8")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["branch", "code", "url", "poster"])
        for l in links:
            w.writerow([l["branch"], l["code"], l["url"], f"{args.api.rstrip('/')}{l['print_url']}"])
    if not CHROME.exists():
        print(f"✗ Chrome not found at {CHROME} — open {html_path} and print to PDF by hand"); return 1
    cmd = [str(CHROME), "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--virtual-time-budget=20000",
           f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not pdf_path.exists():
        print(f"✗ chrome failed ({r.returncode}): {r.stderr[-300:]}"); return 1
    print(f"✓ {len(links)} posters → {pdf_path} ({pdf_path.stat().st_size // 1024} KB)")
    print(f"✓ links → {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
