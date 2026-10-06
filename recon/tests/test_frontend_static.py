"""The recon frontend's static wiring (2026-10 UX phase 1).

index.html now loads its script as ordered slices (recon/frontend/js/NN-*.js)
and wears the dashboard's design kit (docs/ui-kit, mounted at /app/ui-kit).
A slice missing from index.html, a tag pointing at a missing file, or the kit
not being served leaves a blank or unstyled page with every API test green —
these pin the wiring instead.
"""
import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import recon.main as main  # noqa: E402

FE = Path(main.__file__).resolve().parent / "frontend"


def _index():
    return (FE / "index.html").read_text(encoding="utf-8")


def frontend_source():
    """index.html + every js slice, in load order: what the browser runs."""
    return _index() + "\n" + "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((FE / "js").glob("*.js")))


def test_every_slice_is_loaded_in_order():
    tags = re.findall(r'<script src="js/([^"]+)"></script>', _index())
    files = sorted(p.name for p in (FE / "js").glob("*.js"))
    assert tags == files, (tags, files)          # NN- prefix = load order
    assert tags[0] == "00-core.js" and tags[-1] == "90-boot.js"


def test_no_inline_app_script_left():
    # only the tiny theme-before-paint snippet stays inline; the app lives in js/
    inline = re.findall(r"<script>(.*?)</script>", _index(), flags=re.S)
    assert len(inline) == 1 and "oshinei_dark" in inline[0]


def test_core_helpers_and_routes_are_defined():
    src = frontend_source()
    for name in ("const $=", "const esc=", "const jsa=", "async function api(", "async function mgrApi(",
                 "function route(", "function header(", "function toggleTheme(", "function showSheet(",
                 "function loadOverview(", "function loadBranch(", "function loadSetup(", "function loadDiscrepancies("):
        assert name in src, name


def test_slices_kit_and_vendor_are_served_without_stale_cache():
    c = TestClient(main.app)                      # static files only: no lifespan / DB needed
    for path in ["/app/", "/app/ui-kit/tokens.css", "/app/ui-kit/theme-groups.css", "/app/vendor/kira-ui.css"] + \
            [f"/app/js/{p.name}" for p in sorted((FE / "js").glob("*.js"))]:
        r = c.get(path)
        assert r.status_code == 200, path
        assert r.headers.get("cache-control") == "no-cache, must-revalidate", path
    assert "--accent" in c.get("/app/ui-kit/tokens.css").text


def test_theme_toggle_uses_the_dashboards_key():
    src = frontend_source()
    assert "localStorage.setItem('oshinei_dark'" in src
    assert 'aria-label="สลับโหมดมืด/สว่าง"' in src
