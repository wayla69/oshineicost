"""POS branch -> our branch_id, for the jobs that read MSSQL (sync_mssql, build_menu_fact).

Matching by name alone broke when a branch was renamed in the app (ตั้งค่า → สาขา): the
POS still says "OSHINEI ขอนแก่น (1)", BRANCH_ALIAS still turns that into the OLD short name,
and the renamed row no longer matches - the branch's sales silently stopped syncing.

The POS's own Branch.BranchId never changes, and every sync already records which of our
branches it maps to (branch_pos, written by sync_mssql.write_branch_pos). So the id that was
mapped before wins; the name is only used for a POS branch seen for the first time.
"""
from ingest_may26_update import BRANCH_ALIAS


def short_name(mssql_name: str) -> str:
    n = (mssql_name or "").strip()
    return BRANCH_ALIAS.get(n, n)


def known_pos_ids(sq) -> dict:
    """pos_branch_id -> branch_id from the previous sync, kept only while that branch exists."""
    try:
        rows = sq.execute("SELECT bp.pos_branch_id, bp.branch_id FROM branch_pos bp "
                          "JOIN branches b ON b.branch_id = bp.branch_id").fetchall()
    except Exception:                       # first sync on this DB: no branch_pos yet
        return {}
    return {int(p): int(b) for p, b in rows}


def resolve(sq, pos_branches):
    """pos_branches: iterable of (BranchId, BranchName[, BranchCode]).

    Returns (bid_map, pos_rows, unmatched): bid_map = {BranchId: branch_id}; pos_rows are the
    (branch_id, BranchId, BranchCode, BranchName) tuples write_branch_pos stores; unmatched
    lists the POS names that map to no branch of ours."""
    by_id = known_pos_ids(sq)
    by_name = {nm: b for b, nm in sq.execute("SELECT branch_id, name FROM branches")}
    bid_map, pos_rows, unmatched = {}, [], []
    for row in pos_branches:
        pid, name = int(row[0]), row[1]
        code = row[2] if len(row) > 2 else None
        bid = by_id.get(pid)
        if bid is None:
            bid = by_name.get(short_name(name))
        if bid is None:
            unmatched.append(name)
            continue
        bid_map[pid] = bid
        pos_rows.append((bid, pid, code, name))
    return bid_map, pos_rows, unmatched
