"""
_merge_endmonth.py — one-off helper: merge each regular cleansed-raw file with its
matching '<...> End Month_raw.xlsx' (the last calendar day that Easy's monthly
export drops) into a single full-month workbook, written to a temp folder.

The merged files are named exactly like the regular files (ES1_<Mon> <YY>_raw.xlsx /
23 ES_<Mon> <YY>_raw.xlsx) so the canonical upload_data.py detects period+kind
normally. Row-concat (regular rows then end-month rows) reproduces exactly what an
inclusive re-export would contain; parse_es_tx assigns a continuous seq across the
whole file, so receipt keys stay unique.

Outputs to backend/_merged_upload/. Run upload_data.py --dir on that folder.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import openpyxl

import reingest_raw_2026 as rr

HERE = Path(__file__).parent.resolve()
RAW = HERE.parent / "Data for Oshinei" / "Data Cleasing" / "raw"
# Write merged workbooks OUTSIDE OneDrive — the synced folder intermittently locks
# the dir against rmtree/rewrite. System temp is local and lock-free.
OUT = Path(tempfile.gettempdir()) / "oshinei_merged_upload"


def is_regular(p: Path) -> bool:
    n = p.name
    return (n.endswith("_raw.xlsx") and "End Month" not in n
            and (n.startswith("ES1_") or n.startswith("23 ES_")))


def rows_with_header(p: Path):
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    return rows  # [header, *data]


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    regulars = sorted(p for p in RAW.glob("*_raw.xlsx") if is_regular(p))
    end_by_period_kind: dict[tuple[str, str], Path] = {}
    for p in RAW.glob("*End Month_raw.xlsx"):
        kind = "es1" if p.name.startswith("ES1_") else "tx"
        end_by_period_kind[(rr.period_of(p.stem), kind)] = p

    merged = unpaired = 0
    for reg in regulars:
        kind = "es1" if reg.name.startswith("ES1_") else "tx"
        period = rr.period_of(reg.stem)
        end = end_by_period_kind.get((period, kind))
        dst = OUT / reg.name
        if end is None:
            shutil.copy2(reg, dst)
            unpaired += 1
            print(f"  COPY (no end-month) {reg.name}  [{period}]")
            continue
        reg_rows = rows_with_header(reg)
        end_rows = rows_with_header(end)
        wb = openpyxl.Workbook()
        ws = wb.active
        for row in reg_rows:                 # header + regular data
            ws.append(row)
        # End-month exports are occasionally contaminated (e.g. June 25's tx file
        # carries June-30 *plus all of July*). For tx files (Sale_Date in col 3),
        # only keep end-month rows whose date is in the target period; July belongs
        # to its own file. ES1 has no per-row date, so append as-is (its end-month
        # portions are single-day-sized and verified clean).
        appended = skipped = 0
        ym = "-".join(period.split("-")[:2])  # 'YYYY-MM'
        ym_easy = ym  # Sale_Date is ISO 'YYYY-MM-DD ...'
        for row in end_rows[1:]:
            if kind == "tx":
                sd = row[3]
                if sd is None or not str(sd).startswith(ym_easy):
                    skipped += 1
                    continue
            ws.append(row)
            appended += 1
        wb.save(dst)
        wb.close()
        merged += 1
        skip_note = f"  (skipped {skipped:,} out-of-period)" if skipped else ""
        print(f"  MERGE {reg.name}  [{period}]  "
              f"{len(reg_rows)-1:,} + {appended:,} = {len(reg_rows)-1+appended:,} rows{skip_note}")

    print(f"\nMerged {merged}, copied-unpaired {unpaired} -> {OUT}")


if __name__ == "__main__":
    main()
