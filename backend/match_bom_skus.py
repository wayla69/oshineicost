"""
Match unresolved BoM SKU codes (from BoM Excel parser) to DB SKUs.

Strategy (in order):
  1. Code normalisation: strip leading zeros, swap A<->B prefix, try B1 vs B0001.
  2. Exact name match: BoM th_name == skus.description (cleaned: strip (A)/(B), lowercase, strip).
  3. Fuzzy name match: difflib SequenceMatcher >= 0.85 on cleaned names.

Writes matches into bom_sku_alias. Run after running ingest_bom.py once
to populate not_found_details.

Usage:  python backend/match_bom_skus.py [--apply]
        --apply  actually write matches (default is dry-run report)
"""
from __future__ import annotations

import sys
import re
import sqlite3
import argparse
import difflib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).parent.resolve()
DB_PATH = HERE / "oshinei.db"

ALIAS_DDL = """
CREATE TABLE IF NOT EXISTS bom_sku_alias (
  bom_sku_code    TEXT PRIMARY KEY,
  resolved_sku_id INTEGER NOT NULL REFERENCES skus(sku_id),
  match_method    TEXT,
  confidence      REAL,
  notes           TEXT
);
"""

_VARIANT_PREFIX = re.compile(r'^\s*\(\s*[ABab]\s*\)\s*')
_BUFFET_SUFFIX  = re.compile(r'\s*\(\s*[Bb]uffet\s*\)\s*$')
_WS = re.compile(r'\s+')


def clean_name(s: str) -> str:
    if not s:
        return ''
    s = _VARIANT_PREFIX.sub('', s)
    s = _BUFFET_SUFFIX.sub('', s)
    s = s.lower().strip()
    s = _WS.sub(' ', s)
    return s


def normalise_code_variants(code: str) -> List[str]:
    """Return candidate codes to look up in skus."""
    out: List[str] = []
    c = code.strip().upper()
    out.append(c)
    m = re.match(r'^([AB])0*(\d+)$', c)
    if m:
        letter, digits = m.group(1), m.group(2)
        other = 'A' if letter == 'B' else 'B'
        # different padding widths
        n = int(digits)
        for prefix in (letter, other):
            for width in (3, 4, 5):
                out.append(f"{prefix}{n:0{width}d}")
            out.append(f"{prefix}{n}")
    # de-dup, preserve order
    seen, deduped = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            deduped.append(x)
    return deduped


def load_db_skus(con: sqlite3.Connection) -> Tuple[Dict[str, int], List[Tuple[int, str, str]]]:
    """Return (code->sku_id, [(sku_id, code, cleaned_description)])."""
    by_code: Dict[str, int] = {}
    rows: List[Tuple[int, str, str]] = []
    for r in con.execute("SELECT sku_id, sku_code, COALESCE(description,'') FROM skus"):
        sid, code, desc = int(r[0]), str(r[1]).strip().upper(), str(r[2])
        by_code[code] = sid
        rows.append((sid, code, clean_name(desc)))
    return by_code, rows


def match_one(bom_code: str, bom_th: str, bom_en: str,
              by_code: Dict[str, int],
              cleaned_rows: List[Tuple[int, str, str]]
              ) -> Optional[Dict]:
    # Build BoM cleaned name (prefer TH; fall back to EN)
    th = clean_name(bom_th)
    en = clean_name(bom_en)
    name_candidates = [c for c in (th, en) if c]

    # 1. Exact name match — strongest signal when present (safe to auto-apply)
    for c in name_candidates:
        for sid, code, desc_clean in cleaned_rows:
            if desc_clean and desc_clean == c:
                return {
                    'sku_id': sid,
                    'sku_code': code,
                    'method': 'name_exact',
                    'confidence': 1.0,
                    'needs_confirm': False,
                    'notes': f"exact name '{c}'",
                }

    # 2. Code normalisation. Same-letter zero-padding variants are safe; an A<->B
    # prefix swap assumes the two coding schemes are aligned, which they may not be
    # (B0001 vs A0001 can be unrelated dishes) — flag those for human confirmation.
    orig_letter = (re.match(r'^([AB])', bom_code.strip().upper()) or [None, None])[1]
    for cand in normalise_code_variants(bom_code):
        if cand in by_code:
            cand_letter = cand[0]
            swapped = orig_letter is not None and cand_letter != orig_letter
            return {
                'sku_id': by_code[cand],
                'sku_code': cand,
                'method': 'code_swapped' if swapped else 'code_normalised',
                'confidence': 0.7 if swapped else 0.9,
                'needs_confirm': swapped,
                'notes': (f"A<->B code swap {bom_code}->{cand} (UNVERIFIED)" if swapped
                          else f"code variant {cand} of BoM {bom_code}"),
            }

    # 3. Fuzzy name match — track top two so we can require the winner to be
    # clearly ahead of the runner-up (avoids near-ties on similar Thai names).
    best: Optional[Tuple[float, int, str]] = None
    second = 0.0
    for c in name_candidates:
        for sid, code, desc_clean in cleaned_rows:
            if not desc_clean:
                continue
            ratio = difflib.SequenceMatcher(None, c, desc_clean).ratio()
            if best is None or ratio > best[0]:
                second = best[0] if best else 0.0
                best = (ratio, sid, code)
            elif ratio > second:
                second = ratio
    FUZZY_MIN = 0.92      # was 0.85 — too loose for short Thai names
    FUZZY_MARGIN = 0.05   # winner must beat runner-up by this much
    if best and best[0] >= FUZZY_MIN and (best[0] - second) >= FUZZY_MARGIN:
        return {
            'sku_id': best[1],
            'sku_code': best[2],
            'method': 'name_fuzzy',
            'confidence': round(best[0], 4),
            'needs_confirm': True,   # never auto-apply fuzzy matches
            'notes': f"fuzzy match (ratio={best[0]:.3f}, runner-up={second:.3f})",
        }
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true',
                    help='Write CONFIRMED matches (exact name + same-letter code) to bom_sku_alias')
    ap.add_argument('--apply-fuzzy', action='store_true',
                    help='Also write needs-confirm matches (fuzzy + A<->B swaps). Use only after review.')
    args = ap.parse_args()

    # Pull the unmatched list from a fresh ingest
    sys.path.insert(0, str(HERE))
    from ingest_bom import ingest

    con = sqlite3.connect(str(DB_PATH))
    con.execute(ALIAS_DDL)
    con.commit()

    stats = ingest(con=con)
    unresolved = stats.get('not_found_details') or []
    print(f"\nFound {len(unresolved)} unresolved BoM codes from latest ingest.")

    by_code, cleaned = load_db_skus(con)

    from collections import defaultdict
    by_method = defaultdict(int)
    matches: List[Dict] = []
    still_unresolved: List[Dict] = []
    fuzzy_top: List[Tuple[str, str, float, str, str]] = []

    # Dedup BoM codes (same code may appear in multiple files)
    seen = set()
    for u in unresolved:
        code = u['sku_code']
        if code in seen:
            continue
        seen.add(code)
        m = match_one(code, u.get('th_name',''), u.get('en_name',''), by_code, cleaned)
        if m:
            by_method[m['method']] += 1
            matches.append({'bom_code': code, **m, 'th': u.get('th_name',''), 'en': u.get('en_name','')})
            if m['method'] == 'name_fuzzy':
                fuzzy_top.append((code, m['sku_code'], m['confidence'], u.get('th_name','')[:40], m['notes']))
        else:
            still_unresolved.append(u)

    confirmed = [m for m in matches if not m.get('needs_confirm')]
    needs_confirm = [m for m in matches if m.get('needs_confirm')]

    print("\n=== MATCH REPORT ===")
    print(f"  total unique BoM codes unresolved:  {len(seen)}")
    print(f"  matched via name_exact:             {by_method['name_exact']}")
    print(f"  matched via code_normalised:        {by_method['code_normalised']}")
    print(f"  matched via code_swapped (CONFIRM): {by_method['code_swapped']}")
    print(f"  matched via name_fuzzy   (CONFIRM): {by_method['name_fuzzy']}")
    print(f"  -> auto-appliable (confirmed):      {len(confirmed)}")
    print(f"  -> needs human confirmation:        {len(needs_confirm)}")
    print(f"  still unresolved:                   {len(still_unresolved)}")

    fuzzy_top.sort(key=lambda x: -x[2])
    if fuzzy_top:
        print("\nTop 10 fuzzy matches (review carefully):")
        for r in fuzzy_top[:10]:
            try:
                print(f"  {r[0]}  ->  {r[1]}  ratio={r[2]:.3f}  th='{r[3]}'")
            except UnicodeEncodeError:
                print(f"  {r[0]}  ->  {r[1]}  ratio={r[2]:.3f}  (non-ascii th)")

    if still_unresolved:
        print("\nTop 10 still-unresolved (need manual mapping):")
        for u in still_unresolved[:10]:
            try:
                print(f"  {u['sku_code']}  th='{u['th_name'][:40]}'  en='{u['en_name'][:30]}'")
            except UnicodeEncodeError:
                print(f"  {u['sku_code']}  (non-ascii name)")

    to_write = list(confirmed)
    if args.apply_fuzzy:
        to_write += needs_confirm

    if (args.apply or args.apply_fuzzy) and to_write:
        for m in to_write:
            con.execute(
                "INSERT OR REPLACE INTO bom_sku_alias(bom_sku_code, resolved_sku_id, match_method, confidence, notes) "
                "VALUES (?, ?, ?, ?, ?)",
                (m['bom_code'], m['sku_id'], m['method'], m['confidence'], m['notes']),
            )
        con.commit()
        print(f"\nApplied {len(to_write)} alias rows "
              f"({len(confirmed)} confirmed"
              + (f" + {len(needs_confirm)} fuzzy/swapped" if args.apply_fuzzy else "")
              + ").")
        if needs_confirm and not args.apply_fuzzy:
            print(f"  Skipped {len(needs_confirm)} needs-confirm matches. "
                  f"Review them above, then re-run with --apply-fuzzy to persist.")
    elif matches:
        print("\n(Dry-run; re-run with --apply to persist CONFIRMED matches "
              "into bom_sku_alias, or --apply-fuzzy to also include fuzzy/swapped.)")

    con.close()


if __name__ == '__main__':
    main()
