"""Contrast of every text pair of theme B "Vivid" (docs/ui-kit/tokens.css), light and dark -> docs/ui-kit/CONTRAST_B.md.

usage: py -3.12 scripts/contrast_b.py [path/to/tokens.css]      (exit 1 when a pair is under its floor)

The token values are read FROM tokens.css (the :root blocks for light; the same plus the body.dark blocks for dark; a
var(--x) alias is resolved inside its own theme), so the report can never drift from the file.
Floors: 4.5:1 for text, 3:1 for text of 24 px or more and bold, 3:1 for the edge of a control and for an icon.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "ui-kit" / "tokens.css"
OUT = ROOT / "docs" / "ui-kit" / "CONTRAST_B.md"


def _blocks(css, selector_re):
    """The declaration text of every top-level block whose selector matches."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        if re.fullmatch(selector_re, m.group(1).strip()):
            out.append(m.group(2))
    return out


def _decls(text):
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"(--[A-Za-z0-9-]+)\s*:\s*([^;]+);", text)}


def load(src=None):
    css = Path(src or SRC).read_text(encoding="utf-8")
    light = {}
    for b in _blocks(css, r":root|:root, body\.dark"):
        light.update(_decls(b))
    dark = dict(light)
    for b in _blocks(css, r"body\.dark|:root, body\.dark"):
        dark.update(_decls(b))
    return light, dark


def resolve(tokens, name, depth=0):
    v = tokens[name]
    m = re.fullmatch(r"var\((--[A-Za-z0-9-]+)\)", v)
    if m and depth < 10:
        return resolve(tokens, m.group(1), depth + 1)
    return v


def lum(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def cr(a, b):
    la, lb = lum(a), lum(b)
    if la < lb:
        la, lb = lb, la
    return (la + 0.05) / (lb + 0.05)


DEPTS = ["mgmt", "mkt", "ops", "ana", "kit", "ss", "wh", "acc", "brn", "own"]
HUES = ["blue", "teal", "violet", "orange", "pink", "green"]
MEAN = ["good", "watch", "fix", "neu"]
SURF = ["bg-card", "bg-page", "bg-subtle", "bg-hover", "accent-soft"]


def pairs():
    """(group, label, foreground token, background token, floor)"""
    P = []
    for fg in ("text-primary", "text-secondary", "text-muted"):
        for bg in SURF + ["bg-input", "bg-topbar"]:
            P.append(("Text on surfaces", f"{fg} / {bg}", fg, bg, 4.5))
    for bg in SURF:
        P.append(("Accent", f"accent / {bg}", "accent", bg, 4.5))
    P.append(("Accent", "on-accent / accent", "on-accent", "accent", 4.5))
    P.append(("Accent", "on-accent / accent-hover", "on-accent", "accent-hover", 4.5))
    P.append(("Accent", "gold-i / bg-card", "gold-i", "bg-card", 4.5))
    P.append(("Accent", "on-gold / gold", "on-gold", "gold", 4.5))
    for m in MEAN:
        P.append(("Meaning sets", f"{m}-on / {m}-f (pill, chip, tag)", f"{m}-on", f"{m}-f", 4.5))
        P.append(("Meaning sets", f"{m}-on / {m}-f2 (gradient end)", f"{m}-on", f"{m}-f2", 4.5))
        P.append(("Meaning sets", f"{m}-i / {m}-t (ink on its tint)", f"{m}-i", f"{m}-t", 4.5))
        for bg in ("bg-card", "bg-page", "bg-subtle", "bg-hover"):
            P.append(("Meaning sets", f"{m}-i / {bg}", f"{m}-i", bg, 4.5))
    for ink in ("good-ink", "bad-ink", "amber-ink", "orange-ink", "teal-ink", "rose-ink", "cyan-ink", "sky-ink",
                "navy-ink", "violet-ink", "fuchsia-ink", "pink-ink"):
        for bg in SURF:
            P.append(("Text inks", f"{ink} / {bg}", ink, bg, 4.5))
    for h in HUES:
        P.append(("Six hues", f"white / hue-{h}-f", "#ffffff", f"hue-{h}-f", 4.5))
        P.append(("Six hues", f"white / hue-{h}-f2", "#ffffff", f"hue-{h}-f2", 4.5))
        P.append(("Six hues", f"hue-{h}-i / hue-{h}-t", f"hue-{h}-i", f"hue-{h}-t", 4.5))
        P.append(("Six hues", f"hue-{h}-i / bg-card", f"hue-{h}-i", "bg-card", 4.5))
    for d in DEPTS:
        P.append(("Departments", f"white / dept-{d}-f", "#ffffff", f"dept-{d}-f", 4.5))
        P.append(("Departments", f"white / dept-{d}-f2", "#ffffff", f"dept-{d}-f2", 4.5))
        P.append(("Departments", f"dept-{d}-ink / dept-{d}-tint", f"dept-{d}-ink", f"dept-{d}-tint", 4.5))
        P.append(("Departments", f"dept-{d}-ink / bg-card", f"dept-{d}-ink", "bg-card", 4.5))
        P.append(("Departments", f"dept-{d}-ink / bg-page", f"dept-{d}-ink", "bg-page", 4.5))
        P.append(("Departments", f"dept-{d}-side / rail-grad1 (icon)", f"dept-{d}-side", "rail-grad1", 3.0))
        P.append(("Departments", f"dept-{d}-side / rail-grad2 (icon)", f"dept-{d}-side", "rail-grad2", 3.0))
    for fg in ("hero-ink", "hero-ink2"):
        for bg in ("hero1", "hero-mid", "hero2"):
            P.append(("Hero band", f"{fg} / {bg}", fg, bg, 4.5))
    for bg in ("bg-sidebar", "rail-grad1", "rail-grad2"):
        P.append(("Rail", f"rail-ink / {bg}", "rail-ink", bg, 4.5))
    P.append(("Rail", "rail-active-ink / rail-active-bg", "rail-active-ink", "rail-active-bg", 4.5))
    P.append(("Rail", "nb-ink / nb-bg (task count)", "nb-ink", "nb-bg", 4.5))
    for k in ("toast-ok", "toast-info", "toast-warn", "toast-bad"):
        P.append(("Toast", f"{k} / toast-bg", k, "toast-bg", 4.5))
    P.append(("Toast", "toast-fg / toast-bg", "toast-fg", "toast-bg", 4.5))
    P.append(("Controls", "border-control / bg-input (edge)", "border-control", "bg-input", 3.0))
    P.append(("Controls", "border-control / bg-card (edge)", "border-control", "bg-card", 3.0))
    return P


def evaluate(tokens):
    # body-level tokens (the toast ones) alias :root/body.dark values; load() already merged them when declared in a body block
    def val(n):
        if n.startswith("#"):
            return n
        return resolve(tokens, "--" + n)
    rows, fails = [], []
    for grp, label, fg, bg, floor in pairs():
        try:
            f, b = val(fg), val(bg)
        except KeyError as e:
            fails.append((grp, label, "missing token " + str(e), "", 0.0, floor))
            continue
        r = cr(f, b)
        rows.append((grp, label, f, b, r, floor))
        if r < floor:
            fails.append((grp, label, f, b, r, floor))
    return rows, fails


def body_blocks_into(tokens, src, dark):
    """The `body { --toast-... }` block (declared on body so a dark alias re-resolves): merge it in."""
    css = Path(src).read_text(encoding="utf-8")
    for b in _blocks(css, r"body"):
        tokens.update(_decls(b))
    return tokens


def main(src=None):
    global SRC
    if src:
        SRC = Path(src)
    light, dark = load()
    light = body_blocks_into(light, SRC, False)
    dark = body_blocks_into(dark, SRC, True)
    out = ["# Contrast, theme B \"Vivid\"", "",
           "Generated by `scripts/contrast_b.py` from `docs/ui-kit/tokens.css` (every value is read from the file; a `var()` alias is resolved inside its own theme).",
           "Floors: 4.5:1 for text; 3:1 for large bold text (24 px and up), for a control's edge and for an icon. Text floor 12 px; phone targets 44 px.", ""]
    total_fail = 0
    for theme, tokens in (("Light", light), ("Dark (body.dark)", dark)):
        rows, fails = evaluate(tokens)
        total_fail += len(fails)
        out.append(f"## {theme}: {len(rows)} pairs, {len(fails)} under the floor")
        out.append("")
        lows = sorted(rows, key=lambda x: x[4])[:5]
        out.append("Lowest five: " + "; ".join(f"{x[1]} {x[4]:.2f}" for x in lows))
        out.append("")
        grp = None
        for g, label, f, b, r, floor in rows:
            if g != grp:
                grp = g
                out += ["", f"### {g}", "", "| Pair | Text / icon | On | Ratio | Floor | |", "|---|---|---|---:|---:|---|"]
            out.append(f"| {label} | `{f}` | `{b}` | {r:.2f} | {floor:g} | {'ok' if r >= floor else 'FAIL'} |")
        out.append("")
        for x in fails:
            print("FAIL", theme, x[0], x[1], x[2], x[3], round(x[4], 2))
    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("pairs ok" if not total_fail else f"{total_fail} pairs under the floor", "->", OUT)
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
