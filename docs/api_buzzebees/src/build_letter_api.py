# -*- coding: utf-8 -*-
"""Build Buzzebees_Proposal_OshineiAPI_EN.html (Rev. 2).
Section 3 (evidence) is taken from letter_mssql_en.html so the figures stay identical.
Usage: py -3.12 src/build_letter_api.py"""
import os

SRC = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(SRC), "Buzzebees_Proposal_OshineiAPI_EN.html")

rev1 = open(os.path.join(SRC, "letter_mssql_en.html"), encoding="utf-8").read()
start = rev1.index("<!-- ===================== 3. EVIDENCE")
end = rev1.index("<!-- ===================== 4. INTERFACE")
evidence = rev1[start:end]

SWAPS = [
    ("They size the data Buzzebees will read and justify the rules in sections 4–6.",
     "They size the data the API will carry and justify the rules in sections 4–6."),
    ("a 5-minute poll returns about 40 new or changed bills. The load on the database is small if the connector reads only changed rows (section 4.3).",
     "a 5-minute poll of <code>/v1/bills/changes</code> returns about 40 new or changed bills. Oshinei's feed reads only changed rows from HQ_Oshinei, so the extra load on the database is small."),
    ("so the connector finds voids by reading changed rows.",
     "so Oshinei's feed finds voids by reading changed rows and publishes the bill again with status <code>voided</code>."),
    ("the database model reverses every one", "this model reverses every one"),
    ("agree a target with Easy (E6). The connector should alert when a branch has not uploaded for 3 hours during trading (R7).",
     "agree a target with Easy (E3). <code>/v1/branches</code> gives each branch's last upload time, and both parties alert when a branch has not uploaded for 3 hours during trading (R7)."),
]
for old, new in SWAPS:
    assert old in evidence, old[:60]
    evidence = evidence.replace(old, new)

html = open(os.path.join(SRC, "letter_api_en.html"), encoding="utf-8").read()
css = open(os.path.join(SRC, "style.css"), encoding="utf-8").read()
html = html.replace("{{CSS}}", css).replace("{{EVIDENCE}}", evidence)
open(OUT, "w", encoding="utf-8").write(html)
print("WROTE", OUT)
