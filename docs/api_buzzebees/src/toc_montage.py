# -*- coding: utf-8 -*-
"""py -3.12 toc_montage.py <html> <pdf> <mode>  mode = toc | montage
toc: find the page of each numbered h2 and patch <span class="pg" data-key=...> in the HTML.
montage: contact sheet PNG of all pages + per-page text-bottom report."""
import sys, re, io
import fitz
from PIL import Image

html_path, pdf_path, mode = sys.argv[1], sys.argv[2], sys.argv[3]
doc = fitz.open(pdf_path)
print("pages:", len(doc))

if mode == "toc":
    html = open(html_path, encoding="utf-8").read()
    titles = re.findall(r'<h2[^>]*id="(s\d+)"[^>]*>([^<]+)</h2>', html)
    found = {}
    for pno, page in enumerate(doc, start=1):
        txt = page.get_text()
        if pno > 1 and ("Contents" in txt[:200] or "สารบัญ" in txt[:200]):
            continue
        for key, t in titles:
            if key in found: continue
            head = t.strip()
            if head in txt or head.replace(" ", "") in txt.replace(" ", ""):
                found[key] = pno
    print("found:", found)
    for key, _ in titles:
        n = found.get(key, "–")
        html = re.sub(r'(<span class="pg" data-key="%s">)[^<]*(</span>)' % key, r'\g<1>%s\g<2>' % n, html)
    open(html_path, "w", encoding="utf-8").write(html)
    print("patched TOC")
else:
    out = sys.argv[4]
    zoom = 0.55
    imgs = []
    for page in doc:
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        imgs.append(Image.open(io.BytesIO(pix.tobytes("png"))))
        # report lowest text y as % of page height (overflow / near-bottom check)
        blocks = page.get_text("blocks")
        ys = [b[3] for b in blocks if b[4].strip()]
        low = max(ys) / page.rect.height * 100 if ys else 0
        print(f"p{page.number+1}: text bottom {low:.0f}% blocks={len(blocks)}")
    cols = 4
    w, h = imgs[0].size
    rows = (len(imgs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (w + 10) + 10, rows * (h + 10) + 10), "#888")
    for i, im in enumerate(imgs):
        sheet.paste(im, (10 + (i % cols) * (w + 10), 10 + (i // cols) * (h + 10)))
    sheet.save(out)
    print("montage", out, sheet.size)
