"""Theme B "Vivid" (VZ1): every token text pair clears its contrast floor in light and dark, the sheet is linked last, and the
meaning sets / department fills exist. The pairs and the ratios are scripted in scripts/contrast_b.py (-> docs/ui-kit/CONTRAST_B.md)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _contrast():
    spec = importlib.util.spec_from_file_location("contrast_b", ROOT / "scripts" / "contrast_b.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_token_text_pair_clears_its_floor_in_both_themes():
    c = _contrast()
    light, dark = c.load()
    light, dark = c.body_blocks_into(light, c.SRC, False), c.body_blocks_into(dark, c.SRC, True)
    for name, tokens in (("light", light), ("dark", dark)):
        rows, fails = c.evaluate(tokens)
        assert len(rows) > 200, name
        assert not fails, (name, [(f[1], round(f[4], 2)) for f in fails])


def test_the_report_in_the_repo_matches_the_tokens():
    report = (ROOT / "docs" / "ui-kit" / "CONTRAST_B.md").read_text(encoding="utf-8")
    assert "0 under the floor" in report and "FAIL" not in report


def test_the_vivid_sheet_is_linked_last_and_the_tokens_carry_the_b_sets():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    order = [html.index(x) for x in ("theme-formal.css", "theme-groups.css", "theme-vivid.css")]
    assert order == sorted(order) and html.index("theme-vivid.css") > html.rindex("</style>", 0, html.index("</head>"))
    tokens = (ROOT / "docs" / "ui-kit" / "tokens.css").read_text(encoding="utf-8")
    for m in ("good", "watch", "fix", "neu"):
        for part in ("f", "f2", "t", "i", "on"):
            assert f"--{m}-{part}:" in tokens, (m, part)
    for tok in ("--hero1:", "--hero2:", "--gold:", "--shadow-card:", "--shadow-tile:", "--shadow-pop:", "--kfs:", "--tfs:"):
        assert tok in tokens, tok
    for r in ("--r-sm: 14px", "--r-md: 18px", "--r-lg: 20px"):
        assert r in tokens, r
