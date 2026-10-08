"""KIRA UI for generated reports (PDF / standalone HTML / reportlab).

The app itself loads KIRA UI from /vendor (see vendor/kira-ui-font.css). Reports are
different: they are rendered to PDF by headless Chrome from a local file, opened from
docs/ or forwarded as .html, so they cannot rely on a server path or on Google Fonts
(KIRA UI isn't there). Each report therefore embeds the face as data: URIs.

It embeds a SUBSET (vendor/fonts/report/*.subset.woff2: Basic Latin, Latin-1, all of
Thai incl. ฿, General Punctuation (dashes/quotes/bullets), € № ◌; every OpenType layout
feature kept, so Thai tone marks still stack and tnum still works) - ~32 KB a weight
instead of ~190 KB. KIRA UI 7.0 "Polished" since 2026-10-08 (3.1 before: same metrics
and coverage, ~90 Latin outlines refined, optional ss05). KIRA UI is Noto Sans + Noto Sans Thai under
the SIL OFL 1.1: vendor/fonts/KIRA-UI-LICENSE-OFL-1.1.txt.

Rebuild the subsets from the owner's TTF package with fontTools (py -3.14 has brotli):
  py -3.14 -m fontTools.subset KIRA-UI-<W>.ttf --flavor=woff2 --layout-features='*'      --name-IDs='*' --name-legacy --notdef-outline      --unicodes="U+0020-007E,U+00A0-00FF,U+0E00-0E7F,U+2000-206F,U+20AC,U+2116,U+25CC"      --output-file=vendor/fonts/report/KIRA-UI-<W>.subset.woff2
and the same without --flavor for the two .subset.ttf files reportlab reads (below). KIRA UI 7.0 ships
the SAME PostScript name (name ID 6 "KIRA UI 7.0", with a space) in all four weights, which makes a PDF
merge Regular and Bold; set ID 6 to KIRAUI-<W> (and ID 3) on every subset with fontTools afterwards.
"""
import base64
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
FONT_DIR = HERE.parent / "vendor" / "fonts" / "report"
WEIGHTS = (("Regular", 400), ("Medium", 500), ("SemiBold", 600), ("Bold", 700))

# Symbols KIRA UI lacks (→ − ▲ ▼ ✓ ✗ ★ ⚠ ☐ ① …): Noto Sans Math / Symbols 2 / Symbols subsets
# (SIL OFL 1.1, vendor/fonts/symbols/*-OFL.txt), same family, each limited to its characters:
# (file, unicode-range, size-adjust). ● ○ ◉ ■ □ ◆ ◇ are scaled to 60% - Noto draws them
# 0.73-1.0 em and the reports use them as small dots.
SYMBOL_DIR = HERE.parent / "vendor" / "fonts" / "symbols"
SYMBOL_FACES = (
    ("NotoSansMath", "U+2190-2199, U+21A9-21AA, U+21B3, U+21BA-21BB, U+21C4-21C6, U+21D0, U+21D2, U+2208, U+2212, U+221A, U+221E, U+2248, U+2260-2261, U+2264-2265, U+2298, U+22EF, U+2591-2592, U+27F3", None),
    ("NotoSansSymbols2", "U+25A2, U+25AC, U+25AE, U+25B2-25B4, U+25B6, U+25B8, U+25BC-25BE, U+25C0, U+25C2, U+25FB-25FC, U+2605-2606, U+2610-2612, U+26A0, U+2713-2718", None),
    ("NotoSansSymbols2", "U+25A0-25A1, U+25C6-25C7, U+25C9, U+25CB, U+25CF", "60%"),
    ("NotoSansSymbols", "U+238B, U+2460-2469, U+24D8, U+2690-2691", None),
)

# The family stack every report uses; heavier weights (800/900) resolve to 700.
STACK = "'KIRA UI','Noto Sans Thai',sans-serif"


@lru_cache(maxsize=1)
def font_face_css() -> str:
    """@font-face rules for the four weights, fonts inlined. Empty if the files are
    missing (the stack then falls back to Noto Sans Thai / the system face)."""
    rules = []
    for name, weight in WEIGHTS:
        f = FONT_DIR / f"KIRA-UI-{name}.subset.woff2"
        if not f.exists():
            continue
        b64 = base64.b64encode(f.read_bytes()).decode("ascii")
        rules.append("@font-face{font-family:'KIRA UI';font-style:normal;font-weight:%d;"
                     "font-display:block;src:url(data:font/woff2;base64,%s) format('woff2')}" % (weight, b64))
    # declared once per weight: Chrome matches ONE weight group, so each group needs the symbols
    for name, rng, adjust in SYMBOL_FACES:
        f = SYMBOL_DIR / f"{name}.subset.woff2"
        if not f.exists():
            continue
        b64 = base64.b64encode(f.read_bytes()).decode("ascii")
        extra = f"size-adjust:{adjust};" if adjust else ""
        for _, weight in WEIGHTS:
            rules.append("@font-face{font-family:'KIRA UI';font-style:normal;font-weight:%d;font-display:block;%s"
                         "src:url(data:font/woff2;base64,%s) format('woff2');unicode-range:%s}" % (weight, extra, b64, rng))
    return "\n".join(rules)


# ── reportlab + matplotlib (the Monthly Pack PDF, backend/routers/export.py) ─────────────
# reportlab's built-in Helvetica has no Thai, so branch and ingredient names came out as
# black boxes. The two TTF subsets (same coverage as the woff2 ones, ~73 KB each) are
# registered once per process; <b> inside a Paragraph maps to the Bold face.
TTF_FILES = {"KIRA UI": "KIRA-UI-Regular.subset.ttf", "KIRA UI Bold": "KIRA-UI-Bold.subset.ttf"}
_HELVETICA = ("Helvetica", "Helvetica-Bold")


@lru_cache(maxsize=1)
def reportlab_fonts() -> tuple:
    """(regular, bold) reportlab font names: KIRA UI when the TTF subsets exist and
    reportlab is importable, else Helvetica so the PDF still builds (Latin only)."""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.lib.fonts import addMapping
    except Exception:
        return _HELVETICA
    paths = {name: FONT_DIR / fname for name, fname in TTF_FILES.items()}
    if not all(p.exists() for p in paths.values()):
        return _HELVETICA
    try:
        for name, path in paths.items():
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(path)))
        # <b>/<i> in Paragraph markup: bold -> the Bold face, italic -> same face (none drawn)
        addMapping("KIRA UI", 0, 0, "KIRA UI"); addMapping("KIRA UI", 1, 0, "KIRA UI Bold")
        addMapping("KIRA UI", 0, 1, "KIRA UI"); addMapping("KIRA UI", 1, 1, "KIRA UI Bold")
    except Exception:
        return _HELVETICA
    return ("KIRA UI", "KIRA UI Bold")


def matplotlib_use_kira() -> bool:
    """Make matplotlib draw chart text (axis labels carry Thai branch / ingredient names)
    in KIRA UI. Returns False, leaving the default face, when the file or matplotlib
    is missing."""
    path = FONT_DIR / TTF_FILES["KIRA UI"]
    if not path.exists():
        return False
    try:
        from matplotlib import font_manager, rcParams
        font_manager.fontManager.addfont(str(path))
        rcParams["font.family"] = "KIRA UI"
        rcParams["axes.unicode_minus"] = False   # the subset has U+2212, but keep ASCII minus
        return True
    except Exception:
        return False
