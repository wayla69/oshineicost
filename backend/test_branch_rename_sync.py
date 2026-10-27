"""A branch renamed in the app keeps syncing (owner 2026-10-27: "fix the branch rename sync too").

The sync used to place POS sales by name only (BranchName --BRANCH_ALIAS--> branches.name), so
a rename in ตั้งค่า → สาขา cut that branch off from its sales. branch_map now maps the POS's own
BranchId the way the previous sync did (branch_pos) and uses the name only for a new POS branch;
the daily swap lays the live branches rows over the staged copy so a rename made mid-sync stays.
"""
import sqlite3

import branch_map
import core
import sync_mssql


def _db(path=":memory:"):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE branches (branch_id INTEGER PRIMARY KEY, name TEXT UNIQUE)")
    con.executemany("INSERT INTO branches VALUES (?,?)", [(3, "BranchA"), (7, "BranchB")])
    return con


def test_a_renamed_branch_keeps_its_pos_id():
    con = _db()
    sync_mssql.write_branch_pos(con, [(3, 5, "0001", "BranchA"), (7, 9, "0002", "BranchB")])
    con.execute("UPDATE branches SET name='BranchA North' WHERE branch_id=3")     # renamed in the app
    bid_map, pos_rows, unmatched = branch_map.resolve(con, [(5, "BranchA", "0001"), (9, "BranchB", "0002")])
    assert bid_map == {5: 3, 9: 7}
    assert unmatched == []
    assert (3, 5, "0001", "BranchA") in pos_rows                                   # written back for the next sync


def test_without_a_previous_mapping_the_name_still_places_the_branch():
    con = _db()
    bid_map, _, unmatched = branch_map.resolve(con, [(5, "BranchA"), (11, "Somewhere New")])
    assert bid_map == {5: 3}
    assert unmatched == ["Somewhere New"]


def test_a_mapping_to_a_deleted_branch_falls_back_to_the_name():
    con = _db()
    sync_mssql.write_branch_pos(con, [(42, 5, None, "BranchA")])                   # branch 42 no longer exists
    bid_map, _, _ = branch_map.resolve(con, [(5, "BranchA")])
    assert bid_map == {5: 3}


def test_the_swap_keeps_a_rename_made_while_the_sync_was_building(tmp_path):
    live, staged = tmp_path / "live.db", tmp_path / "staged.db"
    c = _db(live); c.execute("UPDATE branches SET name='BranchA North' WHERE branch_id=3"); c.commit(); c.close()
    c = _db(staged); c.execute("INSERT INTO branches VALUES (12, 'Ingested Branch')"); c.commit(); c.close()
    safe, reason = core._carry_forward_and_verify(str(live), str(staged))
    assert safe, reason
    c = sqlite3.connect(str(staged))
    assert dict(c.execute("SELECT branch_id, name FROM branches")) == {
        3: "BranchA North", 7: "BranchB", 12: "Ingested Branch"}
    c.close()
