"""
Ingest ingredient-level Bill of Materials (BoM) from Excel files.

Walks BoM/<category>/*.xlsx, parses stacked menu blocks, looks up sku_id from
the skus table, and populates bom_ingredients (REPLACE semantics per SKU).

Usage:  python backend/ingest_bom.py
"""
from __future__ import annotations

import re
import sys
import sqlite3
import logging
import time
from pathlib import Path
from typing import Optional, List, Dict, Tuple

import openpyxl

HERE     = Path(__file__).parent.resolve()
ROOT     = HERE.parent
DB_PATH  = HERE / "oshinei.db"
BOM_DIR  = ROOT / "BoM"

# Column indices (0-based for tuple access; Excel col letter shown for reference)
# NOTE: real files have SKU block-start code in col B (idx 1) and ingredient code in col C (idx 2).
COL_BLOCK_SKU = 1  # col B  — SKU block-start code (B0001, A0123…)
COL_SEQ   = 1   # col B   — ingredient row sequence number
COL_CODE  = 2   # col C   — ingredient code
COL_NAME  = 3   # col D   — ingredient / menu name
COL_PRICE = 5   # col F   — ราคาเต็ม
COL_PPU   = 9   # col J   — ราคา/หน่วย
COL_QTY   = 10  # col K   — ปริมาณวัตถุดิบ
COL_UNIT  = 11  # col L   — หน่วย
COL_COST  = 12  # col M   — ต้นทุนสูตร
COL_THNAME = 7  # col H   — TH name on row N+1

# Regex for SKU codes appearing in col C (block start)
SKU_RE = re.compile(r'^[AB]\d{3,6}$')

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s', stream=sys.stdout)
log = logging.getLogger("ingest_bom")


# ----------------------------------------------------------------------------
# Ingredient type classification
# ----------------------------------------------------------------------------
_PROTEIN_KW = ('แซลมอน', 'salmon', 'tuna', 'ทูน่า', 'กุ้ง', 'ปลา', 'เนื้อ', 'หมู', 'ไก่', 'ปลาหมึก', 'หอย', 'ปู', 'beef', 'pork', 'chicken', 'shrimp', 'squid')
_VEG_KW     = ('ผัก', 'แครอท', 'เห็ด', 'สาหร่าย', 'มะนาว', 'หัวหอม', 'แตงกวา', 'มะเขือ')
_SAUCE_KW   = ('ซอส', 'โชยุ', 'sauce', 'soy', 'มิริน', 'พอนสึ', 'เทอริยากิ')
_OIL_KW     = ('น้ำมัน', 'oil', 'เนย', 'butter')
_SEASON_KW  = ('เกลือ', 'พริก', 'น้ำตาล', 'งา', 'พริกไทย', 'salt', 'pepper', 'seasoning')
_CARB_KW    = ('ข้าว', 'rice', 'noodle', 'เส้น', 'โซบะ', 'อุด้ง', 'แป้ง', 'flour', 'pasta', 'pancake', 'มันฝรั่ง', 'potato')

def classify_ingredient(code: Optional[str], name: str) -> str:
    code = (code or '').strip().upper()
    name_l = (name or '').lower()

    def has(kws):
        return any(k.lower() in name_l for k in kws)

    # Prefix-based first
    if code.startswith('F-'):
        return 'protein'
    if code.startswith('V-'):
        return 'veg'
    if code.startswith('S-'):
        if has(_OIL_KW):
            return 'oil'
        if has(_SEASON_KW):
            return 'seasoning'
        return 'seasoning'
    if code.startswith(('15-', '13-')):
        return 'sauce'
    if code.startswith('CK-'):
        return 'sauce' if 'ซอส' in name_l else 'seasoning'
    if code.startswith('11-'):
        # mixed bag - sake, vinegar, sauces — fall through to keyword match
        if has(_SAUCE_KW):
            return 'sauce'
        return 'sauce'  # default for 11- (cooking liquid/condiment family)
    if code.startswith('03-'):
        return 'protein'  # seafood family seen in examples (e.g. dory fish)

    # Keyword-based fallback
    if has(_PROTEIN_KW):
        return 'protein'
    if has(_CARB_KW):
        return 'carb'
    if has(_VEG_KW):
        return 'veg'
    if has(_OIL_KW):
        return 'oil'
    if has(_SAUCE_KW):
        return 'sauce'
    if has(_SEASON_KW):
        return 'seasoning'
    return 'other'


# ----------------------------------------------------------------------------
# Excel block parser
# ----------------------------------------------------------------------------
def _to_float(v) -> float:
    """Best-effort float, treating errors/None/blank as 0."""
    if v is None or v == '':
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        s = str(v).strip().replace(',', '')
        if s.startswith('#'):   # Excel error like #REF!
            return 0.0
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _as_str(v) -> str:
    if v is None:
        return ''
    return str(v).strip()


def parse_bom_file(path: Path) -> List[Dict]:
    """
    Parse a BoM xlsx into a list of menu blocks. Each block:
        { 'sku_code': str, 'en_name': str, 'th_name': str, 'ingredients': [ {...} ], 'total_cost': float }
    """
    # OneDrive may briefly lock the file. Retry a couple of times.
    last_err = None
    for attempt in range(3):
        try:
            wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
            break
        except PermissionError as e:
            last_err = e
            time.sleep(0.5 * (attempt + 1))
    else:
        log.warning(f"Permission denied (after retries): {path.name}: {last_err}")
        return []

    blocks: List[Dict] = []
    for sn in wb.sheetnames:
        ws = wb[sn]
        rows = list(ws.iter_rows(values_only=True))
        n = len(rows)
        i = 0
        while i < n:
            row = rows[i]
            if not row or len(row) <= COL_BLOCK_SKU:
                i += 1
                continue
            code_cell = _as_str(row[COL_BLOCK_SKU])
            if SKU_RE.match(code_cell):
                # Block header detected.
                # Row i+1 typically has EN/TH name in col D / col H
                en_name = ''
                th_name = ''
                if i + 1 < n:
                    r1 = rows[i + 1]
                    if r1 and len(r1) > COL_NAME:
                        en_name = _as_str(r1[COL_NAME])
                    if r1 and len(r1) > COL_THNAME:
                        th_name = _as_str(r1[COL_THNAME])
                # Skip header row (i+2)
                # Ingredient rows: start at i+3 until total/blank
                ing_rows = []
                j = i + 3
                while j < n:
                    rj = rows[j]
                    if not rj or all(c in (None, '') for c in rj[:14]):
                        # blank row → end of ingredients
                        j += 1
                        break
                    seq_v = _as_str(rj[COL_SEQ]) if len(rj) > COL_SEQ else ''
                    code_v = _as_str(rj[COL_CODE]) if len(rj) > COL_CODE else ''
                    # End-of-block detection: 'ราคา' total row or another SKU block-start (col B)
                    if seq_v.startswith('ราคา') or SKU_RE.match(seq_v):
                        break
                    # Valid ingredient row needs a numeric seq
                    try:
                        seq_n = int(float(seq_v))
                    except (ValueError, TypeError):
                        # Not an ingredient row (could be note row) — skip
                        j += 1
                        continue
                    ing_rows.append({
                        'seq':            seq_n,
                        'ingredient_code': code_v or None,
                        'ingredient_name': _as_str(rj[COL_NAME]) if len(rj) > COL_NAME else '',
                        'price_per_unit': _to_float(rj[COL_PPU]) if len(rj) > COL_PPU else 0.0,
                        'qty_per_serving': _to_float(rj[COL_QTY]) if len(rj) > COL_QTY else 0.0,
                        'unit':           _as_str(rj[COL_UNIT]) if len(rj) > COL_UNIT else '',
                        'ingredient_cost': _to_float(rj[COL_COST]) if len(rj) > COL_COST else 0.0,
                    })
                    j += 1

                # Read total cost from total row (col M) if present
                total_cost = 0.0
                # Walk forward up to ~3 rows looking for ราคา (บาท)
                k = j
                while k < min(j + 4, n):
                    rk = rows[k]
                    if rk and len(rk) > COL_SEQ:
                        seq_v = _as_str(rk[COL_SEQ])
                        if seq_v.startswith('ราคา') and len(rk) > COL_COST:
                            total_cost = _to_float(rk[COL_COST])
                            k += 1
                            break
                    k += 1

                if not ing_rows:
                    # Empty block — skip
                    i = max(k, j, i + 1)
                    continue

                if not total_cost:
                    total_cost = sum(x['ingredient_cost'] for x in ing_rows)

                # Drop ingredient rows with no name AND no cost AND no code
                ing_rows = [x for x in ing_rows if x['ingredient_name'] or x['ingredient_code'] or x['ingredient_cost']]

                blocks.append({
                    'sku_code':   code_cell,
                    'en_name':    en_name,
                    'th_name':    th_name,
                    'ingredients': ing_rows,
                    'total_cost': total_cost,
                })
                i = max(k, j)
                continue
            i += 1

    try:
        wb.close()
    except Exception:
        pass
    return blocks


# ----------------------------------------------------------------------------
# DB ingest
# ----------------------------------------------------------------------------
DDL = """
CREATE TABLE IF NOT EXISTS bom_ingredients (
  sku_id           INTEGER NOT NULL REFERENCES skus(sku_id),
  seq              INTEGER NOT NULL,
  ingredient_code  TEXT,
  ingredient_name  TEXT NOT NULL,
  qty_per_serving  REAL,
  unit             TEXT,
  price_per_unit   REAL,
  ingredient_cost  REAL NOT NULL,
  ingredient_type  TEXT,
  source_file      TEXT,
  PRIMARY KEY (sku_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_bom_sku  ON bom_ingredients(sku_id);
CREATE INDEX IF NOT EXISTS idx_bom_code ON bom_ingredients(ingredient_code);
"""

def ensure_table(con: sqlite3.Connection):
    con.executescript(DDL)
    con.commit()


def list_bom_files() -> List[Path]:
    out: List[Path] = []
    if not BOM_DIR.exists():
        return out
    for sub in BOM_DIR.iterdir():
        if not sub.is_dir():
            continue
        # Skip Template folder (Thai-prefixed variants)
        if 'Template' in sub.name or 'template' in sub.name:
            continue
        for f in sub.glob("*.xlsx"):
            if f.name.startswith('~$'):
                continue
            if f.name.startswith('OSHINEI_'):
                continue
            out.append(f)
    return out


def ingest(con: Optional[sqlite3.Connection] = None) -> Dict:
    own = False
    if con is None:
        con = sqlite3.connect(str(DB_PATH))
        own = True
    ensure_table(con)

    sku_map: Dict[str, int] = {}
    for r in con.execute("SELECT sku_id, sku_code FROM skus"):
        sku_map[str(r[1]).strip().upper()] = int(r[0])

    # Phase 9: alias overrides (BoM Excel code → resolved DB sku_id)
    alias_map: Dict[str, int] = {}
    try:
        for r in con.execute("SELECT bom_sku_code, resolved_sku_id FROM bom_sku_alias"):
            alias_map[str(r[0]).strip().upper()] = int(r[1])
    except sqlite3.OperationalError:
        # table not yet created; that's fine — first run before match_bom_skus.py
        pass

    files = list_bom_files()
    log.info(f"Scanning {len(files)} BoM file(s) under {BOM_DIR}")

    total_blocks    = 0
    total_inserted  = 0
    not_found_codes: List[Tuple[str, str]] = []  # (sku_code, file)
    not_found_details: List[Dict] = []  # {sku_code, en_name, th_name, file}
    matched_via_alias = 0
    touched_skus = set()

    for fp in files:
        try:
            blocks = parse_bom_file(fp)
        except Exception as e:
            log.warning(f"Failed to parse {fp.name}: {e}")
            continue
        log.info(f"  {fp.parent.name[:25]} / {fp.name[:60]} -> {len(blocks)} block(s)")
        for blk in blocks:
            total_blocks += 1
            code = blk['sku_code'].strip().upper()
            sku_id = sku_map.get(code)
            if sku_id is None:
                # Try alias map before giving up
                sku_id = alias_map.get(code)
                if sku_id is not None:
                    matched_via_alias += 1
                else:
                    not_found_codes.append((code, fp.name))
                    not_found_details.append({
                        'sku_code': code,
                        'en_name':  blk.get('en_name', ''),
                        'th_name':  blk.get('th_name', ''),
                        'file':     fp.name,
                    })
                    continue
            # Replace semantics
            if sku_id not in touched_skus:
                con.execute("DELETE FROM bom_ingredients WHERE sku_id = ?", (sku_id,))
                touched_skus.add(sku_id)
            rows_to_insert = []
            for idx, ing in enumerate(blk['ingredients'], start=1):
                ing_type = classify_ingredient(ing['ingredient_code'], ing['ingredient_name'])
                rows_to_insert.append((
                    sku_id,
                    idx,
                    ing['ingredient_code'],
                    ing['ingredient_name'] or '(unnamed)',
                    ing['qty_per_serving'],
                    ing['unit'],
                    ing['price_per_unit'],
                    ing['ingredient_cost'],
                    ing_type,
                    fp.name,
                ))
            con.executemany(
                "INSERT OR REPLACE INTO bom_ingredients "
                "(sku_id, seq, ingredient_code, ingredient_name, qty_per_serving, unit, price_per_unit, ingredient_cost, ingredient_type, source_file) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows_to_insert,
            )
            total_inserted += len(rows_to_insert)
    con.commit()

    stats = {
        'files_scanned':   len(files),
        'blocks_parsed':   total_blocks,
        'rows_inserted':   total_inserted,
        'skus_touched':    len(touched_skus),
        'matched_via_alias': matched_via_alias,
        'skus_not_found':  len(not_found_codes),
        'not_found_first': not_found_codes[:10],
        'not_found_details': not_found_details,
    }
    log.info(f"Done. {stats}")
    if own:
        con.close()
    return stats


if __name__ == '__main__':
    s = ingest()
    print()
    print("==== INGEST SUMMARY ====")
    for k, v in s.items():
        print(f"  {k}: {v}")
