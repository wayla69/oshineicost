"""KIRA UI for generated reports (PDF / standalone HTML).

The app itself loads KIRA UI from /vendor (see vendor/kira-ui-font.css). Reports are
different: they are rendered to PDF by headless Chrome from a local file, opened from
docs/ or forwarded as .html, so they cannot rely on a server path or on Google Fonts
(KIRA UI isn't there). Each report therefore embeds the face as data: URIs.

It embeds a SUBSET (vendor/fonts/report/*.subset.woff2: Basic Latin, Latin-1, all of
Thai incl. ฿, dashes/quotes/bullets, arrows, ≤ ≥ ≈ −, ▲▼●■ and ★ ✓ ✗ ⚠ ☐; every
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
    return "\n".join(rules)
