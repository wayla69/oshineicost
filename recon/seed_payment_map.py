"""
Seed recon_payment_map from the POS payment codes actually present in
pos_payment_month.

POS payment codes are messy and branch-specific (e.g. QR appears as 'KNNQR',
'K-SHOP', 'QR code K-bank', 'QR PromptPay'; credit splits by acquirer
'Credit กรุงไทย'=KTB / 'Credit กสิกรไทย'=KBANK / 'Credit KTC'). This module
normalises each code and assigns a best-guess (category, bank). Anything that
can't be attributed to a bank with confidence is left bank='UNKNOWN' for staff
to confirm in the Setup UI.

Idempotent: existing rows are never overwritten (ON CONFLICT DO NOTHING), so
re-seeding after staff edits is safe.

CLI:
    python -m recon.seed_payment_map            # uses $OSHINEI_DB
    python recon/seed_payment_map.py --db backend/oshinei.db
"""

from __future__ import annotations
import os
import re
import sqlite3


def normalize(code: str | None) -> str:
    """Trim + collapse internal whitespace. Unifies 'Cash'/' Cash ' and
    ' KNNQR'/'KNNQR'. Case is preserved (both 'Cash' and 'CASH' map to CASH)."""
    return re.sub(r"\s+", " ", (code or "").strip())


def _bank_of(norm: str) -> str:
    n = norm.lower()
    if "กรุงไทย" in norm or "ktb" in n:                                  return "KTB"
    if "กสิกร" in norm or "kbank" in n or "k-bank" in n \
       or "k-shop" in n or "kshop" in n:                                 return "KBANK"
    if "ktc" in n:                                                       return "KTC"
    if "promptpay" in n or "prompt" in n:                               return "PROMPTPAY"
    return "UNKNOWN"


def classify(norm: str) -> tuple[str, str, int]:
    """Return (category, bank, reconcile) for a normalised payment code."""
    n = norm.lower()
    if norm == "GB" or n == "grab":                          return "GRAB", "NONE", 1
    if norm == "LM" or "line man" in n or "lineman" in n:    return "LINEMAN", "NONE", 1
    if n == "ent" or "entertain" in n:                       return "ENTERTAIN", "NONE", 0
    if "coupon" in n:                                         return "COUPON", "NONE", 1
    if "cash" in n or "เงินสด" in norm:                      return "CASH", "NONE", 1
    if "credit" in n or n == "cc":                           return "CREDIT", _bank_of(norm), 1
    if "transfer" in n or "โอน" in norm:                     return "TRANSFER", _bank_of(norm), 1
    if "qr" in n or "k-shop" in n or "kshop" in n \
       or "prompt" in n or "knn" in n:                       return "QR", _bank_of(norm), 1
    return "OTHER", "UNKNOWN", 0


def seed_payment_map(conn: sqlite3.Connection, source_table: str = "pos_payment_month") -> dict:
    """Insert a map row for every distinct POS payment code (master branches only,
    matching recon scope) not already mapped.
    Returns {'inserted': n, 'skipped': n, 'unknown_bank': n}."""
    try:
        codes = [r[0] for r in conn.execute(f"""
            SELECT DISTINCT p.payment_code
            FROM {source_table} p
            JOIN branches b ON b.branch_id = p.branch_id
            WHERE COALESCE(b.branch_type,'master')='master' AND COALESCE(b.active,1)=1
        """).fetchall()]
    except sqlite3.OperationalError:
        return {"inserted": 0, "skipped": 0, "unknown_bank": 0, "note": f"{source_table} not found"}

    seen, inserted, skipped, unknown = set(), 0, 0, 0
    for raw in codes:
        norm = normalize(raw)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        category, bank, reconcile = classify(norm)
        cur = conn.execute("""
            INSERT INTO recon_payment_map (payment_code_norm, category, bank, reconcile, updated_by)
            VALUES (?,?,?,?, 'seed')
            ON CONFLICT(payment_code_norm) DO NOTHING
        """, (norm, category, bank, reconcile))
        if cur.rowcount:
            inserted += 1
            if bank == "UNKNOWN":
                unknown += 1
        else:
            skipped += 1
    conn.commit()
    return {"inserted": inserted, "skipped": skipped, "unknown_bank": unknown}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.getenv("OSHINEI_DB", "/data/oshinei.db"))
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    # ensure the table exists when run standalone
    schema = os.path.join(os.path.dirname(__file__), "schema.sql")
    con.executescript(open(schema, encoding="utf-8").read())
    result = seed_payment_map(con)
    print("seed_payment_map:", result)
    for r in con.execute("""SELECT category, bank, COUNT(*) n FROM recon_payment_map
                            GROUP BY category, bank ORDER BY category, bank"""):
        print(f"  {r[0]:10} {r[1]:10} x{r[2]}")
    con.close()
