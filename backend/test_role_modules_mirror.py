"""The admin cheatsheet's fallback copy of the role matrix (js/01-core.js
ROLE_MODULES_TH / MODULE_LABEL_TH) must equal auth.ROLE_MODULES / MODULES.

It drifted once: customer.feedback was granted to four roles in auth.py but the
JS copy never learned it, so the fallback matrix showed those roles without
เสียงลูกค้า. The live matrix comes from /api/auth/role-permissions; this copy is
only shown when that call fails - which is exactly when nobody checks it.
"""
import re
from pathlib import Path

import auth

JS = (Path(__file__).resolve().parent.parent / "js" / "01-core.js").read_text(encoding="utf-8")


def _block(name: str) -> str:
    m = re.search(r"const " + name + r" = \{(.*?)\n\};", JS, re.S)
    assert m, f"{name} not found in js/01-core.js"
    return m.group(1)


def test_module_labels_cover_every_module():
    labelled = set(re.findall(r"'([a-z]+\.[a-z]+)'\s*:", _block("MODULE_LABEL_TH")))
    assert labelled == set(auth.MODULES)


def test_role_fallback_matches_auth():
    body = _block("ROLE_MODULES_TH")
    js_roles = {}
    for role, mods in re.findall(r"^\s*(\w+):\s*\[(.*?)\]", body, re.M):
        js_roles[role] = set(re.findall(r"'([a-z]+\.[a-z]+)'", mods))
    # management is "everything" in both: Object.keys(MODULE_LABEL_TH) / set(MODULES)
    assert "management" in body and "Object.keys(MODULE_LABEL_TH)" in body
    expected = {r: set(m) for r, m in auth.ROLE_MODULES.items() if r != "management"}
    assert js_roles == expected
