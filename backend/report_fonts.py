"""KIRA UI for generated reports (PDF / standalone HTML).

The app itself loads KIRA UI from /vendor (see vendor/kira-ui-font.css). Reports are
different: they are rendered to PDF by headless Chrome from a local file, opened from
docs/ or forwarded as .html, so they cannot rely on a server path or on Google Fonts
(KIRA UI isn't there). Each report therefore embeds the face as data: URIs.

It embeds a SUBSET (vendor/fonts/report/*.subset.woff2: Basic Latin, Latin-1, all of
Thai incl. ฿, dashes/quotes/bullets; every
OpenType layout feature kept, so Thai tone marks still stack and tnum still works) -
~30 KB a weight instead of ~190 KB. KIRA UI is Noto Sans + Noto Sans Thai under the
SIL OFL 1.1: vendor/fonts/KIRA-UI-LICENSE-OFL-1.1.txt.
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
