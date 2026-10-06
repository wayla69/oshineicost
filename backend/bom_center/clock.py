# -*- coding: utf-8 -*-
"""Bangkok time for everything a branch does (S10 hardening).

The live server runs in UTC: until 07:00 in Bangkok its `date.today()` is still
yesterday. A morning count before opening, a receipt, the default order day and
"today" in compliance must all be the branch's calendar day, never the server's.
"""
from datetime import date, datetime, timedelta, timezone

BKK = timezone(timedelta(hours=7))


def now_bkk() -> datetime:
    return datetime.now(BKK)


def today() -> date:
    """Today's date in Bangkok."""
    return now_bkk().date()
