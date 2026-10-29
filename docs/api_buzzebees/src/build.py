# -*- coding: utf-8 -*-
"""Assemble the Thai and English HTML editions from body_*.html + svg_*.svg + the shared stylesheet.
Run from docs/api_buzzebees:  py -3.12 src/build.py"""
import os, re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
css = open(os.path.join(HERE, "style.css"), encoding="utf-8").read()

EDITIONS = {
    "th": ("Easy_POS_Buzzebees_API_v1.1_TH.html",
           "ข้อกำหนด API การเชื่อมต่อ Easy POS กับ Buzzebees — ฉบับร่าง 1.1"),
    "en": ("Easy_POS_Buzzebees_API_v1.1_EN.html",
           "Easy POS – Buzzebees API Specification — Draft 1.1"),
}

for lang, (out, title) in EDITIONS.items():
    body = open(os.path.join(HERE, f"body_{lang}.html"), encoding="utf-8").read()
    for key in ("map", "seq"):
        svg = open(os.path.join(HERE, f"svg_{key}_{lang}.svg"), encoding="utf-8").read()
        body = body.replace("{{SVG_%s}}" % key.upper(), svg)
    assert "{{" not in body, "unfilled placeholder"
    html = (f'<!doctype html>\n<html lang="{lang}">\n<head>\n<meta charset="utf-8">\n'
            f"<title>{title}</title>\n<style>\n{css}\n</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n")
    open(os.path.join(ROOT, out), "w", encoding="utf-8").write(html)
    print("wrote", out)
