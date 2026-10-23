# -*- coding: utf-8 -*-
"""xl_safe - Excel formula-injection guard (PL1 #9).

A free-text cell that starts with = + - @ (or a tab / carriage return) is read by Excel as a formula, so a note a user typed
("=HYPERLINK(...)") would run on whoever opens the workbook. Every workbook the app sends passes through guard_workbook() just
before it is saved: such a text gets a leading apostrophe, which Excel shows as plain text. Numbers, dates and ordinary text are
untouched. The app writes no formula of its own, so nothing intended is lost.
"""
DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(v):
    """`v` as it may be written into a cell: a text starting with = + - @ tab CR gets a leading `'`."""
    if isinstance(v, str) and v and v[0] in DANGEROUS and v != "-":
        return "'" + v
    return v


def guard_workbook(wb):
    """Apply safe_cell to every cell of every sheet (in place) and return the workbook."""
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if isinstance(v, str) and v and v[0] in DANGEROUS and v != "-":
                    c.value = "'" + v
                    c.data_type = "s"
    return wb
