"""PR-M2 — the role-scoped bar of วัตถุดิบและใบสั่งซื้อ.

SUPPLY_CHIPS (js/01-core.js) lists every page of the module. Since PR-M2 each item also names the ROLES whose bar shows
it (`roles:`); `mods:` stays the permission floor. This test reads the JS the way test_role_modules_mirror.py does and
pins, per role, the chips and pages the bar offers (plan: bom-ux-overhaul-phase3.md, PR-M2 §1):

  accounting   หน้างาน · ใบสรุปตัดเบิก · ราคารายเดือน (read-only)
  innovation   หน้างาน · สูตร · สูตรกึ่งสำเร็จ · ทะเบียนวัตถุดิบ (read-only)
  warehouse    หน้างาน · คิวใบสั่งซื้อ
  salessupport หน้างาน · ใบสั่งซื้อ · ไฟล์ราคา · ปิดงวดเดือน · ตั้งค่า (+ หน้าระบบเดิม)
  operation    ตรวจสอบการสั่ง · ผลต่างตัดเบิกรายวัน · สรุปผลต่างรายเดือน · ใบตัดเบิกสาขา
  analyst / management   PR-M7: หน้างาน · งานประจำเดือน (ไฟล์ราคา, ราคารายเดือน, สูตรตามระดับราคา, สูตรกึ่งสำเร็จ, ตรวจและประกาศใช้) ·
                         ใบสั่งซื้อ · ปิดงวดเดือน · ข้อมูลตั้งต้น (ทะเบียนวัตถุดิบ, หน่วยและการแปลง, ตรวจสูตรด้วยยอดซื้อ, ความพร้อมและรหัสสูตรเดิม) ·
                         สาขาของฉัน · ตั้งค่า (the older pages inside ตั้งค่า → หน้าระบบเดิม)
The first row of the bar (LEVEL1) is what _renderSupplyBar draws: a chip holding one page is named by that page, and a role
with SUPPLY_FLAT_MAX pages or fewer gets them as one flat row (so accounting's row is หน้างาน · ราคารายเดือน · ใบสรุปตัดเบิก).
And that nothing is listed for a role that cannot open it (the floor), and that no page is left without a chip.
"""
import re
from pathlib import Path

import pytest

import auth

JS = (Path(__file__).resolve().parent.parent / "js" / "01-core.js").read_text(encoding="utf-8")


def _chips():
    m = re.search(r"const SUPPLY_CHIPS = \[(.*?)\n\];\nconst SUPPLY_LEGACY_LABEL", JS, re.S)
    assert m, "SUPPLY_CHIPS not found in js/01-core.js"
    chips, cur = [], None
    for line in m.group(1).splitlines():
        h = re.search(r"\{ key: '(\w+)', label: '([^']+)'", line)
        if h:
            cur = {"key": h.group(1), "label": h.group(2), "items": []}
            chips.append(cur)
        for it in re.finditer(r"\{ tab: '(\w+)'(.*?)\}(?=,|\]|\s*$)", line):
            rest = it.group(2)
            pane = re.search(r"pane: '(\w+)'", rest)
            label = re.search(r"label: '([^']+)'", rest)
            roles = re.search(r"roles: \[([^\]]*)\]", rest)
            mods = re.search(r"mods: (_SUPPLY_CENTRE|\[[^\]]*\])", rest)
            group = re.search(r"group: '(\w+)'", rest)
            assert cur is not None and label
            cur["items"].append({
                "tab": it.group(1), "pane": pane.group(1) if pane else None, "label": label.group(1),
                "roles": re.findall(r"'(\w+)'", roles.group(1)) if roles else None,
                "mods": (re.findall(r"'([\w.]+)'", JS[JS.index("const _SUPPLY_CENTRE"):].split("\n", 1)[0]) if mods.group(1) == "_SUPPLY_CENTRE"
                         else re.findall(r"'([\w.]+)'", mods.group(1))) if mods else None,
                "group": group.group(1) if group else None})
    return chips


def _tab_modules():
    body = re.search(r"const TAB_MODULE = \{(.*?)\n\};", JS, re.S).group(1)
    out = {}
    for name, single, many in re.findall(r"(\w+):\s*(?:'([\w.]+)'|\[([^\]]*)\])", body):
        out[name] = [single] if single else re.findall(r"'([\w.]+)'", many)
    return out


def _visible(role):
    """[(chip key, chip label, [item labels])] the bar offers `role` (roles and the permission floor), chips in order."""
    mods = set(auth.MODULES) if role == "management" else set(auth.ROLE_MODULES.get(role, set()))
    tabmod = _tab_modules()
    out = []
    for ch in _chips():
        got = []
        for it in ch["items"]:
            if it["roles"] is not None and role not in it["roles"]:
                continue
            need = tabmod.get(it["tab"])
            if need and not (set(need) & mods):
                continue
            if it["mods"] and not (set(it["mods"]) & mods):
                continue
            got.append(it["label"])
        if got:
            out.append((ch["key"], ch["label"], got))
    return out


def _bar(role):
    """{chip label: [item labels]}"""
    return {label: items for _, label, items in _visible(role)}


def _level1(role):
    """The first row of the bar, as _renderSupplyBar draws it: a chip holding one page is named by the page, and a role with
    SUPPLY_FLAT_MAX pages or fewer gets one flat row of pages; หน้างาน and สาขาของฉัน keep their names."""
    flat_max = int(re.search(r"const SUPPLY_FLAT_MAX = (\d+)", JS).group(1))
    vis = _visible(role)
    flat = sum(len(items) for _, _, items in vis) <= flat_max
    row = []
    for key, label, items in vis:
        if key not in ("home", "branch") and (flat or len(items) == 1):
            row += items
        else:
            row.append(label)
    return row


LEGACY = ["แผนสั่งวัตถุดิบ (ระบบเดิม)", "ยอดซื้อเทียบยอดขาย", "ขนาดบรรจุ"]       # PR-B4a (1.8): ปฏิทินเทศกาลและวันหยุด moved out of this group
EVENTS = "ปฏิทินเทศกาลและวันหยุด"
HOME = "งานของฉันและปิดงวดเดือน"
ADOPTION = "การใช้งานทุกสาขา"                       # batch 3 (4.7): replaces the หน้างาน entry "สาขานำร่อง 4 สาขา"
PERSALES = "ใช้วัตถุดิบต่อยอดขาย"                      # PR-V
CODEREQ = "แจ้งเปลี่ยนรหัสสินค้า"                        # PR-Y: Sales Support reports a new code of an existing item

EXPECTED = {
    "accounting": {"หน้างาน": [HOME], "ข้อมูลตั้งต้น": ["ราคารายเดือน"], "ปิดงวดเดือน": ["ใบสรุปตัดเบิก"]},
    "innovation": {"หน้างาน": [HOME], "ข้อมูลตั้งต้น": ["ทะเบียนวัตถุดิบ"], "สูตรและการประกาศใช้": ["สูตรตามระดับราคา", "สูตรกึ่งสำเร็จ"]},
    "warehouse": {"หน้างาน": [HOME], "ใบสั่งซื้อ": ["คิวใบสั่งซื้อ"]},
    "branch": {"หน้างาน": [HOME], "สาขาของฉัน": ["ตัดเบิก", "สั่งสินค้า", "รับสินค้า", "ผลต่างการใช้", "ตรวจนับสิ้นเดือน"]},
    "salessupport": {"หน้างาน": [HOME], "ข้อมูลตั้งต้น": ["ไฟล์ราคา"],
                     "ใบสั่งซื้อ": ["คิวใบสั่งซื้อ", "เคลมสินค้า", "ยอดแนะนำรายรอบ", "ใบตัดเบิกสาขา", "ผลต่างตัดเบิกรายวัน", PERSALES, ADOPTION],
                     "ปิดงวดเดือน": ["นำเข้าข้อมูลปิดงวด", "สรุปผลต่างรายเดือน"],
                     "ตั้งค่า": ["รอบส่งของสาขา", EVENTS, "โซนจัดเก็บ", CODEREQ] + LEGACY},
    "operation": {"หน้างาน": [HOME], "ใบสั่งซื้อ": ["ตรวจสอบการสั่ง", "ใบตัดเบิกสาขา", "ผลต่างตัดเบิกรายวัน", PERSALES, ADOPTION], "ปิดงวดเดือน": ["สรุปผลต่างรายเดือน"]},
    "marketing": {},
    "franchise": {},
}
EVERYTHING = {
    "หน้างาน": [HOME],
    "งานประจำเดือน": ["ไฟล์ราคา", "ราคารายเดือน", "สูตรตามระดับราคา", "สูตรกึ่งสำเร็จ", "ตรวจและประกาศใช้"],
    "ข้อมูลตั้งต้น": ["ทะเบียนวัตถุดิบ", "หน่วยและการแปลง", "ตรวจสูตรด้วยยอดซื้อ", "ความพร้อมและรหัสสูตรเดิม"],   # PR-X: the recipe check, analyst / management only
    "ใบสั่งซื้อ": ["คิวใบสั่งซื้อ", "เคลมสินค้า", "ยอดแนะนำรายรอบ", "ตรวจสอบการสั่ง", "ใบตัดเบิกสาขา", "ผลต่างตัดเบิกรายวัน", PERSALES, ADOPTION],
    "ปิดงวดเดือน": ["นำเข้าข้อมูลปิดงวด", "สรุปผลต่างรายเดือน", "ใบสรุปตัดเบิก"],
    "สาขาของฉัน": ["ตัดเบิก", "สั่งสินค้า", "รับสินค้า", "ผลต่างการใช้", "ตรวจนับสิ้นเดือน"],
    "ตั้งค่า": ["รอบส่งของสาขา", EVENTS, "โซนจัดเก็บ", CODEREQ] + LEGACY,
}
EXPECTED["management"] = EVERYTHING
# the analyst holds neither ops.report nor supply.ordering, the floor of the plan and set-up tabs: of the older pages
# only ยอดซื้อเทียบยอดขาย (cost.analysis) opens for it, as before PR-M2
EXPECTED["analyst"] = {**EVERYTHING, "ตั้งค่า": ["รอบส่งของสาขา", EVENTS, "โซนจัดเก็บ", CODEREQ, "ยอดซื้อเทียบยอดขาย"]}


@pytest.mark.parametrize("role", sorted(EXPECTED))
def test_the_bar_each_role_gets(role):
    assert _bar(role) == EXPECTED[role]


# the first row of the bar per role: the plan's lists, read as chips
LEVEL1 = {
    "accounting": ["หน้างาน", "ราคารายเดือน", "ใบสรุปตัดเบิก"],
    "innovation": ["หน้างาน", "ทะเบียนวัตถุดิบ", "สูตรตามระดับราคา", "สูตรกึ่งสำเร็จ"],
    "warehouse": ["หน้างาน", "คิวใบสั่งซื้อ"],
    "operation": ["หน้างาน", "ใบสั่งซื้อ", "สรุปผลต่างรายเดือน"],      # PR-V: 6 pages are more than SUPPLY_FLAT_MAX, so the bar groups into chips
    "salessupport": ["หน้างาน", "ไฟล์ราคา", "ใบสั่งซื้อ", "ปิดงวดเดือน", "ตั้งค่า"],
    "branch": ["หน้างาน", "สาขาของฉัน"],
    "analyst": ["หน้างาน", "งานประจำเดือน", "ใบสั่งซื้อ", "ปิดงวดเดือน", "ข้อมูลตั้งต้น", "สาขาของฉัน", "ตั้งค่า"],
    "management": ["หน้างาน", "งานประจำเดือน", "ใบสั่งซื้อ", "ปิดงวดเดือน", "ข้อมูลตั้งต้น", "สาขาของฉัน", "ตั้งค่า"],
    "marketing": [], "franchise": [],
}


@pytest.mark.parametrize("role", sorted(LEVEL1))
def test_the_first_row_of_the_bar(role):
    assert _level1(role) == LEVEL1[role]


def test_the_older_pages_are_one_group_inside_setup_and_nothing_is_deleted():
    chips = {c["key"]: c for c in _chips()}
    legacy = [i["label"] for c in chips.values() for i in c["items"] if i["group"] == "legacy"]
    assert legacy == LEGACY and all(i["group"] == "legacy" for i in chips["setup"]["items"][4:])
    assert [c["key"] for c in chips.values() if any(i["group"] == "legacy" for i in c["items"])] == ["setup"]
    # three pages of the old ordering screens, all still reachable (the owner's rule: regroup, never delete); the fourth, the
    # festival calendar, moved to its own page of ตั้งค่า (PR-B4a 1.8) and the old pane's markup stays in tabs/08-ordersetup.html
    assert {(i["tab"], i["pane"]) for i in chips["setup"]["items"] if i["group"] == "legacy"} == {
        ("ordering", None), ("ingpurchase", None), ("ordersetup", "pack")}


def test_every_page_is_in_the_bar_of_some_role_that_can_open_it():
    seen = set()
    for role in list(EXPECTED) + ["analyst", "management"]:
        for items in _bar(role).values():
            seen.update(items)
    every = {i["label"] for c in _chips() for i in c["items"]}
    assert every <= seen, f"no role's bar offers: {sorted(every - seen)}"


def test_pricefile_is_a_pane_of_its_own_with_the_submit_card():
    html = (Path(__file__).resolve().parent.parent / "tabs" / "52-bomcenter.html").read_text(encoding="utf-8")
    pane = re.search(r'<div id="bmc-pane-pricefile".*?(?=\n  <!-- ──|\n</div>\s*$)', html, re.S)
    assert pane and 'id="bmcPriceSubmitCard"' in pane.group(0) and 'id="bmcPriceFileList"' in pane.group(0)
    price = re.search(r'<div id="bmc-pane-price".*?<div id="bmc-pane-pricefile"', html, re.S).group(0)
    assert 'id="bmcPriceSubmitCard"' not in price and 'id="bmcPriceSubList"' in price      # the analyst keeps the pending list
    assert "bmcPricePreview" in price and "bmcImportPrices" in price                          # ... and the import card


def test_every_pane_has_its_help_behind_a_question_mark():
    """PR-D6: the explanation of a page is a <template> behind the "?" of the page header (uiHelp), never a paragraph above the controls and no longer a
    folded block at the bottom. A <details class="bmc-help"> that is left (the two panes PR-R is redesigning) is closed: no `open`."""
    root = Path(__file__).resolve().parent.parent
    for f in ("tabs/52-bomcenter.html", "tabs/53-branchstock.html"):
        txt = (root / f).read_text(encoding="utf-8")
        helps = re.findall(r"<details[^>]*class=\"[^\"]*bmc-help[^\"]*\"[^>]*>", txt)
        assert not any(re.search(r"\bopen\b", h) for h in helps), f
        assert 'class="ui-q' in txt and "<template id=\"hlp-" in txt, f
    oh = (root / "tabs" / "54-orderhub.html").read_text(encoding="utf-8")
    js = (root / "js" / "15-orderhub.js").read_text(encoding="utf-8")
    assert 'data-help-tab="orderhub"' in oh
    for pane in ("queue", "claims", "issues", "issuevar", "comp", "pilot", "central", "month"):
        assert f'id="ohbHelp-{pane}"' not in oh, pane
        assert re.search(r"\n  %s: \{" % pane, js), pane               # the text of the pane, by role, in _OHB_HELP


def test_a_page_in_two_chips_is_listed_for_one_role_only():
    """PR-M7: ไฟล์ราคา, ราคารายเดือน, ทะเบียนวัตถุดิบ and the two recipe pages sit in two chips; the roles of the two copies never overlap,
    so no role's bar lists a page twice and the other roles' bars are the ones they had before."""
    seen = {}
    for ch in _chips():
        for it in ch["items"]:
            for role in it["roles"] or ["*"]:
                key = (role, it["tab"], it["pane"])
                assert key not in seen, f"{key} is listed in {seen[key]} and in {ch['key']}"
                seen[key] = ch["key"]
    for role in ("analyst", "management"):
        labels = [x for items in _bar(role).values() for x in items]
        assert len(labels) == len(set(labels)), role
