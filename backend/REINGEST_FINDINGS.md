# Re-ingest investigation — findings (2026-06-01)

**Decision: deferred. NOTHING was written. Live DB is untouched and safe.**

## What was checked
Source requested: `Data for Oshinei/Data Cleasing/raw/`
A validated, dry-run-by-default script was written: `backend/reingest_raw_2026.py`
(it preserves all curated cost tables and only ever rebuilds Easy *sales*).

## Why a full re-ingest from this folder would be DESTRUCTIVE

| | Live DB now | This raw folder |
|---|---|---|
| Branches | **27** | **3** (ขอนแก่น (1), OSHINEI ชลบุรี, Oshinei ระยอง) |
| sku_monthly rows | 104,822 | 8,362 |
| transaction rows | 535,282 | ~280,019 |
| Month files | complete (2025-05 … 2026-05) | 26 files, **7 corrupt** |

A re-ingest = "delete ALL Easy sales, reload from files". Against this folder that
would drop the chain from **27 branches to 3** and lose 7 months entirely.

## Corrupt files (not valid .xlsx — "File is not a zip file")
- 23 ES_: Dec 25, Jan 26, Mar 26, May 26, Nov 25
- ES1_:   Feb 26, May 26
(Re-save/re-export these from the source; they are unreadable as-is.)

## Format notes (already handled in reingest_raw_2026.py, for when a full export arrives)
1. Files are FLAT single-header tables — a DIFFERENT format from the production
   multi-section .xlsx. The main `ingest_easy.py` parser CANNOT read them
   (it produced 0 rows + garbage periods). The new script parses them correctly.
2. **Sale_No resets daily**, so the same Sale_No recurs across days. Using it as
   the receipt key with INSERT OR REPLACE would collapse ~70% of real
   transactions. The new script builds a unique key = `date-saleno-seq`.
3. ES1 has duplicate (branch, product_code) rows → aggregated before insert.
4. Filenames have a `_raw` suffix, inconsistent spacing (`ES1_Aug  25`), and
   `Jun`/`June` variants — the new script's `period_of()` handles all of these,
   and ignores `test_raw.xlsx`.

## Recommended path
1. Obtain a COMPLETE, all-27-branch, non-corrupt export (same flat raw format is
   fine — the script reads it).
2. Run `python reingest_raw_2026.py` (dry-run) → confirm 27 branches + 0 errors.
3. Only then `python reingest_raw_2026.py --apply` (auto-backs-up first).

Until then: **do not apply.** Current data is correct and complete.
