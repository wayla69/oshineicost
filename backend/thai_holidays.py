"""Thai public holidays 2024-2027 as the restaurants feel them (days the customers are off work).

Source: the Bank of Thailand's yearly notifications "วันหยุดตามประเพณีของสถาบันการเงินและสถาบันการเงินเฉพาะกิจ"
(financial institutions' holidays, announced under the Cabinet's public-holiday resolutions). Each list holds the days
actually observed, so the moving Buddhist days (Makha, Visakha, Asarnha Bucha) carry their real date of the year and
the substitution days (ชดเชย) for holidays that fall on a Saturday or Sunday are listed on the Monday/Tuesday they
are taken.

  * 2024 (B.E. 2567) - the BoT list as reproduced by TTB "วันหยุดทำการของธนาคาร ประจำปี 2567"
    (media.ttbbank.com/1/document/branch/Special-Holidays-for-Year-2024-TH.pdf) plus BoT press release 5/2024 (12 Apr).
  * 2025 (B.E. 2568) - the BoT list as reproduced by TTB (…/Special-Holidays-for-Year-2025-TH.pdf) plus BoT press
    release 42/2024 of 27 Nov 2024 (2 Jun and 11 Aug 2025 as special holidays).
  * 2026 (B.E. 2569) - BoT Notification No. 31/2568 (announced 5 Aug 2025), bot.or.th .../FPG/2568/EngPDF/25680162.pdf;
    the 2 Jan 2026 special holiday is the Cabinet resolution of 12 Nov 2024.
  * 2027 (B.E. 2570) - BoT Notification No. 37/2569 (announced 27 Aug 2026), bot.or.th .../FPG/2569/EngPDF/25690175.pdf.

Left out on purpose: 16 Oct 2026 (a Bangkok-only special holiday for the IMF/World Bank meetings, BoT Notification
No. 26/2569 - the branches are outside Bangkok), the regional Islamic / Chinese New Year days of the four southern
provinces, and Khao Phansa (a government-only holiday). Weekend holidays are not listed; only the days people are off.

Every holiday carries a `key` that names the occasion, so that the same occasion can be found again in the previous
year (the date moves for the Buddhist days and the substitution days): `sub:<key>` is the substitution day of
`<key>`, and `special:<date>` is a one-off day with no counterpart in another year.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, NamedTuple, Optional


class Holiday(NamedTuple):
    key: str
    name: str


_RAW = [
    # --- 2024 ---------------------------------------------------------------------------------------------------------
    ("2024-01-01", "new_year", "วันขึ้นปีใหม่"),
    ("2024-02-26", "sub:makha", "ชดเชยวันมาฆบูชา"),
    ("2024-04-08", "sub:chakri", "ชดเชยวันจักรี"),
    ("2024-04-12", "special:2024-04-12", "วันหยุดพิเศษ"),
    ("2024-04-15", "songkran_15", "วันสงกรานต์"),
    ("2024-04-16", "sub:songkran", "ชดเชยวันสงกรานต์"),
    ("2024-05-01", "labour", "วันแรงงานแห่งชาติ"),
    ("2024-05-06", "sub:coronation", "ชดเชยวันฉัตรมงคล"),
    ("2024-05-22", "visakha", "วันวิสาขบูชา"),
    ("2024-06-03", "queen_suthida", "วันเฉลิมพระชนมพรรษาพระราชินี"),
    ("2024-07-22", "sub:asarnha", "ชดเชยวันอาสาฬหบูชา"),
    ("2024-07-29", "sub:king_birthday", "ชดเชยวันเฉลิมพระชนมพรรษาพระบาทสมเด็จพระเจ้าอยู่หัว"),
    ("2024-08-12", "queen_mother", "วันแม่แห่งชาติ"),
    ("2024-10-14", "sub:rama9_memorial", "ชดเชยวันนวมินทรมหาราช"),
    ("2024-10-23", "chulalongkorn", "วันปิยมหาราช"),
    ("2024-12-05", "rama9_birthday", "วันพ่อแห่งชาติ"),
    ("2024-12-10", "constitution", "วันรัฐธรรมนูญ"),
    ("2024-12-31", "new_year_eve", "วันสิ้นปี"),
    # --- 2025 ---------------------------------------------------------------------------------------------------------
    ("2025-01-01", "new_year", "วันขึ้นปีใหม่"),
    ("2025-02-12", "makha", "วันมาฆบูชา"),
    ("2025-04-07", "sub:chakri", "ชดเชยวันจักรี"),
    ("2025-04-14", "songkran_14", "วันสงกรานต์"),
    ("2025-04-15", "songkran_15", "วันสงกรานต์"),
    ("2025-05-01", "labour", "วันแรงงานแห่งชาติ"),
    ("2025-05-05", "sub:coronation", "ชดเชยวันฉัตรมงคล"),
    ("2025-05-12", "sub:visakha", "ชดเชยวันวิสาขบูชา"),
    ("2025-06-02", "special:2025-06-02", "วันหยุดพิเศษ"),
    ("2025-06-03", "queen_suthida", "วันเฉลิมพระชนมพรรษาพระราชินี"),
    ("2025-07-10", "asarnha", "วันอาสาฬหบูชา"),
    ("2025-07-28", "king_birthday", "วันเฉลิมพระชนมพรรษาพระบาทสมเด็จพระเจ้าอยู่หัว"),
    ("2025-08-11", "special:2025-08-11", "วันหยุดพิเศษ"),
    ("2025-08-12", "queen_mother", "วันแม่แห่งชาติ"),
    ("2025-10-13", "rama9_memorial", "วันนวมินทรมหาราช"),
    ("2025-10-23", "chulalongkorn", "วันปิยมหาราช"),
    ("2025-12-05", "rama9_birthday", "วันพ่อแห่งชาติ"),
    ("2025-12-10", "constitution", "วันรัฐธรรมนูญ"),
    ("2025-12-31", "new_year_eve", "วันสิ้นปี"),
    # --- 2026 ---------------------------------------------------------------------------------------------------------
    ("2026-01-01", "new_year", "วันขึ้นปีใหม่"),
    ("2026-01-02", "special:2026-01-02", "วันหยุดพิเศษ"),
    ("2026-03-03", "makha", "วันมาฆบูชา"),
    ("2026-04-06", "chakri", "วันจักรี"),
    ("2026-04-13", "songkran_13", "วันสงกรานต์"),
    ("2026-04-14", "songkran_14", "วันสงกรานต์"),
    ("2026-04-15", "songkran_15", "วันสงกรานต์"),
    ("2026-05-01", "labour", "วันแรงงานแห่งชาติ"),
    ("2026-05-04", "coronation", "วันฉัตรมงคล"),
    ("2026-06-01", "sub:visakha", "ชดเชยวันวิสาขบูชา"),
    ("2026-06-03", "queen_suthida", "วันเฉลิมพระชนมพรรษาพระราชินี"),
    ("2026-07-28", "king_birthday", "วันเฉลิมพระชนมพรรษาพระบาทสมเด็จพระเจ้าอยู่หัว"),
    ("2026-07-29", "asarnha", "วันอาสาฬหบูชา"),
    ("2026-08-12", "queen_mother", "วันแม่แห่งชาติ"),
    ("2026-10-13", "rama9_memorial", "วันนวมินทรมหาราช"),
    ("2026-10-23", "chulalongkorn", "วันปิยมหาราช"),
    ("2026-12-07", "sub:rama9_birthday", "ชดเชยวันพ่อแห่งชาติ"),
    ("2026-12-10", "constitution", "วันรัฐธรรมนูญ"),
    ("2026-12-31", "new_year_eve", "วันสิ้นปี"),
    # --- 2027 ---------------------------------------------------------------------------------------------------------
    ("2027-01-01", "new_year", "วันขึ้นปีใหม่"),
    ("2027-02-22", "sub:makha", "ชดเชยวันมาฆบูชา"),
    ("2027-04-06", "chakri", "วันจักรี"),
    ("2027-04-13", "songkran_13", "วันสงกรานต์"),
    ("2027-04-14", "songkran_14", "วันสงกรานต์"),
    ("2027-04-15", "songkran_15", "วันสงกรานต์"),
    ("2027-05-03", "sub:labour", "ชดเชยวันแรงงานแห่งชาติ"),
    ("2027-05-04", "coronation", "วันฉัตรมงคล"),
    ("2027-05-20", "visakha", "วันวิสาขบูชา"),
    ("2027-06-03", "queen_suthida", "วันเฉลิมพระชนมพรรษาพระราชินี"),
    ("2027-07-19", "sub:asarnha", "ชดเชยวันอาสาฬหบูชา"),
    ("2027-07-28", "king_birthday", "วันเฉลิมพระชนมพรรษาพระบาทสมเด็จพระเจ้าอยู่หัว"),
    ("2027-08-12", "queen_mother", "วันแม่แห่งชาติ"),
    ("2027-10-13", "rama9_memorial", "วันนวมินทรมหาราช"),
    ("2027-10-25", "sub:chulalongkorn", "ชดเชยวันปิยมหาราช"),
    ("2027-12-06", "sub:rama9_birthday", "ชดเชยวันพ่อแห่งชาติ"),
    ("2027-12-10", "constitution", "วันรัฐธรรมนูญ"),
    ("2027-12-31", "new_year_eve", "วันสิ้นปี"),
]

YEARS = (2024, 2027)                                    # first and last year the table covers

HOLIDAYS: Dict[date, Holiday] = {date.fromisoformat(d): Holiday(k, n) for d, k, n in _RAW}
_BY_KEY: Dict[tuple, date] = {(d.year, h.key): d for d, h in HOLIDAYS.items()}


def holiday_on(d: date) -> Optional[Holiday]:
    """The holiday observed on that day, or None (also None for years the table does not cover)."""
    return HOLIDAYS.get(d)


def is_holiday(d: date) -> bool:
    return d in HOLIDAYS


def same_holiday_last_year(d: date) -> Optional[date]:
    """The date the same occasion was observed on in the previous year (the 23/10 Chulalongkorn Day of last year for
    the 23/10 of this year, the real date of last year's Makha Bucha for this year's), or None when the day is not a
    holiday or the occasion has no counterpart (one-off special days, a year the table does not cover)."""
    h = HOLIDAYS.get(d)
    if h is None or h.key.startswith("special:"):
        return None
    return _BY_KEY.get((d.year - 1, h.key))


def days(start: date, end: date):
    """Every date from start to end inclusive."""
    for i in range((end - start).days + 1):
        yield start + timedelta(days=i)
