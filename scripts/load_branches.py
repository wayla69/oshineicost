# -*- coding: utf-8 -*-
"""Load test for the BoM Center at the cutoff hour: N branches order, one warehouse processes, the branches
receive, while a management session keeps the heavy report busy.

    py -3.12 scripts/load_branches.py --base http://127.0.0.1:8040 --branches 30 --rounds 3 --heavy 1 \
        --out load_result.json [--create-accounts] [--token <owner write token>] [--month-end]

Run it ONLY against a scratch server on a scratch copy of the database (see scripts/load_branches_setup.py and
docs/LOAD_TEST_30_BRANCHES.md). Plain `requests` + threads, no browser.

Each round, per branch (all branches at once, released together by a barrier):
    หน้างาน  GET  /api/bom-center/todo
    plan     GET  /api/bom-center/order/plan?round=            (the page opens; the delivery chips come back)
             GET  /api/bom-center/order/plan?round=&delivery=  (the branch picks a chip)
    slip     GET  /api/bom-center/issues/sheet                  (ตัดเบิก: today's sheet, once per day)
             POST /api/bom-center/issues/save  {lines}          (the draft autosave)
             POST /api/bom-center/issues/save  {lines, submit}  (confirm: the order needs today's slip confirmed)
    draft    POST /api/bom-center/order/save  {round, delivery} (the order autosave)
    plan     GET  /api/bom-center/order/plan?round=&delivery=   (suggestion net of the system stock)
    submit   POST /api/bom-center/order/save  {lines, submit}   (a 400 `late` is confirmed with ack_late)
then ONE warehouse session, in turn, for every submitted order:
             POST /api/bom-center/hub/process/{id}              (needs X-Upload-Token)
then every branch at once:
    receive  GET  /api/bom-center/order/{id} ; POST /api/bom-center/order/{id}/receive   (one line short, reason `short`, ack)
and, with --month-end:
             GET  /api/bom-center/stock/sheet ; POST /api/bom-center/stock/count   (the month-end draft autosave)
The management session(s) loop GET /api/reports/management (a different branch/period each time, so the response
cache never answers it) and GET /api/home/brief every ~2 s from the first login to the last receive.

Records p50 / p95 / max latency per endpoint, the 4xx / 5xx counts, and any body or transport error that
mentions "locked" or "timeout". The default password is read from backend/auth.py at run time and never printed.
"""
import argparse
import concurrent.futures as cf
import json
import os
import random
import re
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
ROUND_CYCLE = ["1", "2", "salmon"]


def default_password() -> str:
    src = (ROOT / "backend" / "auth.py").read_text(encoding="utf-8")
    m = re.search(r'^DEFAULT_PASSWORD\s*=\s*"([^"]+)"', src, re.M)
    if not m:
        raise SystemExit("DEFAULT_PASSWORD not found in backend/auth.py")
    return m.group(1)


# ── measurements ────────────────────────────────────────────────────────────
class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.lat = defaultdict(list)                  # endpoint -> [seconds]
        self.codes = defaultdict(lambda: defaultdict(int))
        self.flags = defaultdict(list)                # "locked" / "timeout" / "5xx" / "4xx" / "exc" -> [samples]
        self.expected = defaultdict(int)              # endpoint -> expected 400s (late confirm)

    def add(self, name, status, secs, body="", expected=False):
        low = (body or "")[:2000].lower()
        with self.lock:
            self.lat[name].append(secs)
            self.codes[name][status] += 1
            if expected:
                self.expected[name] += 1
            if "locked" in low or "database is locked" in low:
                self.flags["locked"].append({"endpoint": name, "status": status, "body": (body or "")[:300]})
            if "timeout" in low or "timed out" in low:
                self.flags["timeout"].append({"endpoint": name, "status": status, "body": (body or "")[:300]})
            if status >= 500:
                self.flags["5xx"].append({"endpoint": name, "status": status, "body": (body or "")[:300]})
            elif status >= 400 and not expected:
                self.flags["4xx"].append({"endpoint": name, "status": status, "body": (body or "")[:300]})

    def exc(self, name, e, secs):
        with self.lock:
            self.lat[name].append(secs)
            self.codes[name]["exc"] += 1
            self.flags["exc"].append({"endpoint": name, "error": repr(e)[:300]})
            msg = repr(e).lower()
            if "timed out" in msg or "timeout" in msg:
                self.flags["timeout"].append({"endpoint": name, "error": repr(e)[:300]})

    def summary(self):
        out = {}
        for name, xs in sorted(self.lat.items()):
            s = sorted(xs)
            n = len(s)
            c = self.codes[name]
            out[name] = {
                "n": n, "p50": pct(s, 50), "p95": pct(s, 95), "max": s[-1], "mean": sum(s) / n,
                "ok": sum(v for k, v in c.items() if isinstance(k, int) and k < 400),
                "c4xx": sum(v for k, v in c.items() if isinstance(k, int) and 400 <= k < 500) - self.expected[name],
                "expected_400": self.expected[name],
                "c5xx": sum(v for k, v in c.items() if isinstance(k, int) and k >= 500),
                "exc": c.get("exc", 0)}
        return out


def pct(sorted_xs, p):
    if not sorted_xs:
        return 0.0
    k = (len(sorted_xs) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_xs) - 1)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (k - lo)


class Client:
    """A browser session: cookie jar + timed requests recorded under a normalised endpoint name."""

    def __init__(self, base, stats, timeout=120):
        self.base, self.stats, self.timeout = base.rstrip("/"), stats, timeout
        self.s = requests.Session()
        self.s.headers["Accept"] = "application/json"

    def call(self, method, path, name, expected_400=None, **kw):
        t0 = time.perf_counter()
        try:
            r = self.s.request(method, self.base + path, timeout=self.timeout, **kw)
        except Exception as e:                                   # transport error / client timeout
            self.stats.exc(name, e, time.perf_counter() - t0)
            return None
        dt = time.perf_counter() - t0
        exp = False
        if expected_400 and r.status_code == 400:
            try:
                d = r.json().get("detail")
                exp = isinstance(d, dict) and d.get("code") == expected_400
            except Exception:
                exp = False
        self.stats.add(name, r.status_code, dt, r.text if r.status_code >= 400 or "lock" in r.text[:2000].lower() else "", exp)
        return r

    def login(self, user, pw):
        r = self.call("POST", "/api/auth/login", "POST auth/login", json={"username": user, "password": pw})
        return r is not None and r.status_code == 200


def scan_server_log(path, offset):
    """What the server itself logged since `offset`: exception lines and the last app frame before each one.
    The client only sees "Internal Server Error", so a 500 caused by 'database is locked' shows up here."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            text = f.read().replace(b"\x00", b"").decode("utf-8", "replace")
    except OSError as e:
        return {"error": repr(e)}
    exc, where = defaultdict(int), defaultdict(int)
    frame = None
    for line in text.splitlines():
        m = re.search(r'File "[^"]*[\\/](bom_center|routers|backend)[\\/]([^"]+)", line (\d+), in (\w+)', line)
        if m:
            frame = f"{m.group(2).replace(chr(92), '/')}:{m.group(3)} in {m.group(4)}"
            continue
        m = re.match(r"^[\s|+-]*((?:sqlite3\.)?\w*(?:Error|Exception))\b[:(]?\s*(.*)$", line)
        if m and not line.lstrip().startswith(("ERROR:", "INFO:")):
            exc[f"{m.group(1)}: {m.group(2)[:80]}"] += 1
            if frame:
                where[f"{m.group(1)} at {frame}"] += 1
            frame = None
    return {"locked": text.count("database is locked"), "tracebacks": text.count("Traceback (most recent call last)"),
            "exceptions": dict(exc), "last_app_frame": dict(where)}


def jget(r):
    try:
        return r.json()
    except Exception:
        return {}


# ── management session: the heavy report, never answered from the cache ────
def heavy_loop(base, stats, stop, user, pw, branches, seq):
    c = Client(base, stats)
    if not c.login(user, pw):
        print("management login failed"); return
    combos = [("monthly", None), ("weekly", None), ("daily", None)]
    combos += [("monthly", b) for b in branches]
    rng = random.Random(seq)
    rng.shuffle(combos)
    i = 0
    while not stop.is_set():
        period, br = combos[i % len(combos)]
        params = {"period": period, "lang": "th"}
        if br:
            params["branch"] = br
        c.call("GET", "/api/reports/management", "GET reports/management (heavy)", params=params)
        if i % 3 == 2:
            c.call("GET", "/api/home/brief", "GET home/brief")
        i += 1
        stop.wait(2.0)


# ── one branch ──────────────────────────────────────────────────────────────
def confirm_slip(c, rng):
    """The branch page's ตัดเบิก pane: open today's sheet, key what the kitchen used (and one item wasted), confirm.
    A slip already confirmed today (a later round) is left alone. Returns None, or why it could not be done."""
    sh = jget(c.call("GET", "/api/bom-center/issues/sheet", "GET issues/sheet"))
    if (sh.get("status") or {}).get("status") in ("confirmed", "late"):
        return None
    items = [i for z in sh.get("zones") or [] for i in z["items"]]
    if not items:
        return "the issue sheet is empty"
    lines = [{"code": i["code"], "qty_used": rng.choice([0.5, 1, 2]), "unit": i["count_unit"]} for i in items[:25]]
    lines[0]["qty_waste"] = 0.5                                    # one wasted item, with its reason
    lines[0]["waste_reason"] = "waste"
    c.call("POST", "/api/bom-center/issues/save", "POST issues/save (draft)", json={"lines": lines, "submit": False})
    r = c.call("POST", "/api/bom-center/issues/save", "POST issues/save (confirm)", json={"lines": lines, "submit": True})
    if r is None or r.status_code != 200:
        return f"slip confirm failed: {None if r is None else r.status_code} {None if r is None else r.text[:120]}"
    return None


def branch_order(c, rng, used, round_pref):
    """หน้างาน → plan → today's issue slip → draft → plan → submit. Returns (order_id, info) or (None, why)."""
    c.call("GET", "/api/bom-center/todo", "GET todo")
    p0 = jget(c.call("GET", "/api/bom-center/order/plan", "GET order/plan (open)", params={"round": round_pref}))
    dels = [d for d in p0.get("deliveries") or [] if (d["round"], d["delivery"]) not in used]
    if not dels:
        return None, "no unused delivery left in the horizon"
    pick = next((d for d in dels if d["round"] == round_pref and d.get("open")), None) \
        or next((d for d in dels if d.get("open")), None) or dels[0]
    rnd, dlv = pick["round"], pick["delivery"]
    used.add((rnd, dlv))
    plan = jget(c.call("GET", "/api/bom-center/order/plan", "GET order/plan (chip)", params={"round": rnd, "delivery": dlv}))
    rows = plan.get("rows") or []
    if not rows:
        return None, f"empty plan for {rnd} {dlv}"
    # ใบตัดเบิก: the order can be submitted only once today's issue slip is confirmed — do that first
    why = confirm_slip(c, rng)
    if why:
        return None, why
    r1 = c.call("POST", "/api/bom-center/order/save", "POST order/save (draft)",
                json={"round": rnd, "delivery": dlv, "lines": [], "submit": False})
    if r1 is None or r1.status_code != 200:
        return None, f"draft save failed: {rnd} {dlv} -> {None if r1 is None else r1.status_code} {None if r1 is None else r1.text[:90]}"
    plan = jget(c.call("GET", "/api/bom-center/order/plan", "GET order/plan (after slip)", params={"round": rnd, "delivery": dlv}))
    rows = plan.get("rows") or []
    lines = [{"code": r["code"], "confirmed_units": r["order_units"]} for r in rows if (r.get("order_units") or 0) > 0]
    if not lines and rows:                                         # nothing suggested: order one unit with a reason
        lines = [{"code": rows[0]["code"], "confirmed_units": 1, "reason_code": "event", "note": "load test"}]
    body = {"round": rnd, "delivery": dlv, "lines": lines, "submit": True}
    r2 = c.call("POST", "/api/bom-center/order/save", "POST order/save (submit)", expected_400="late", json=body)
    late = False
    if r2 is not None and r2.status_code == 400 and isinstance(jget(r2).get("detail"), dict) and jget(r2)["detail"].get("code") == "late":
        late = True
        r2 = c.call("POST", "/api/bom-center/order/save", "POST order/save (submit, ack_late)", json={**body, "ack_late": True})
    if r2 is None or r2.status_code != 200:
        return None, f"submit failed: {None if r2 is None else r2.status_code} {None if r2 is None else r2.text[:160]}"
    return jget(r2).get("order_id"), {"round": rnd, "delivery": dlv, "late": late, "lines": len(lines)}


def branch_receive(c, oid):
    d = jget(c.call("GET", f"/api/bom-center/order/{oid}", "GET order/{id}"))
    lines = []
    short_done = False
    for ln in d.get("lines") or []:
        sent = ln.get("processed_units")
        if sent is None:
            sent = ln.get("confirmed_units") or 0
        q = float(sent)
        item = {"code": ln.get("ingredient_code") or ln.get("code"), "received_units": q}
        if not short_done and q >= 1:
            item["received_units"] = q - 1
            item["reason"] = "short"
            short_done = True
        lines.append(item)
    r = c.call("POST", f"/api/bom-center/order/{oid}/receive", "POST order/{id}/receive", json={"lines": lines, "ack": True})
    return r is not None and r.status_code == 200


def branch_month_end(c):
    sh = jget(c.call("GET", "/api/bom-center/stock/sheet", "GET stock/sheet"))
    items = [i for z in sh.get("zones") or [] for i in z["items"]]
    lines = [{"code": i["code"], "qty": 1, "unit": i["count_unit"]} for i in items]
    r = c.call("POST", "/api/bom-center/stock/count", "POST stock/count (draft)", json={"lines": lines, "submit": False})
    return r is not None and r.status_code == 200


# ── the run ─────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Cutoff-hour load test for the BoM Center (scratch server only)")
    ap.add_argument("--base", default="http://127.0.0.1:8040")
    ap.add_argument("--branches", type=int, default=30)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--heavy", type=int, default=1, help="concurrent management sessions looping the heavy report (0 = none)")
    ap.add_argument("--out", default="load_result.json")
    ap.add_argument("--prefix", default="load_b", help="branch account prefix: load_b01 ...")
    ap.add_argument("--create-accounts", action="store_true", help="create load_bNN accounts (management1 + the write token)")
    ap.add_argument("--token", default=os.environ.get("OSHINEI_SEED_TOKEN", ""), help="owner write token (env OSHINEI_SEED_TOKEN)")
    ap.add_argument("--warehouse-user", default="warehouse1")
    ap.add_argument("--mgmt-user", default="management1")
    ap.add_argument("--month-end", action="store_true", help="add the month-end count sheet + draft autosave per branch each round")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--stagger", type=float, default=0.0,
                    help="spread the branches' start over this many seconds (0 = all at the same instant, the worst case)")
    ap.add_argument("--server-log", default="", help="the scratch server's stderr file: scanned for 'database is locked' and tracebacks")
    a = ap.parse_args()
    if not re.match(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$", a.base.rstrip("/")):
        raise SystemExit("refusing: --base must be a local scratch server (127.0.0.1 / localhost)")
    pw = default_password()
    stats = Stats()
    n = a.branches

    # branch names the accounts are bound to: the centre's active list, real branches first
    mg = Client(a.base, stats)
    if not mg.login(a.mgmt_user, pw):
        raise SystemExit("management login failed")
    me = jget(mg.call("GET", "/api/bom-center/stock/me", "GET stock/me"))
    names = [b["name"] for b in sorted(me.get("branches") or [], key=lambda b: b["branch_id"])]
    if len(names) < n:
        raise SystemExit(f"only {len(names)} active branches in the database; need {n} (run scripts/load_branches_setup.py)")
    accounts = [(f"{a.prefix}{i + 1:02d}", names[i]) for i in range(n)]
    if a.create_accounts:
        if not a.token:
            raise SystemExit("--create-accounts needs --token (or OSHINEI_SEED_TOKEN)")
        made = 0
        for u, b in accounts:
            r = mg.s.post(a.base.rstrip("/") + "/api/auth/users", headers={"X-Upload-Token": a.token},
                          json={"username": u, "role": "branch", "branch": b})
            if r.status_code == 200:
                made += 1
            elif r.status_code != 409:
                raise SystemExit(f"account {u}: {r.status_code} {r.text[:120]}")
        print(f"accounts: {made} created, {n - made} already there")
    # a throw-away stats object for set-up traffic would hide nothing; reset so the tables show only the test itself
    stats = Stats()
    mg.stats = stats

    log_offset = os.path.getsize(a.server_log) if a.server_log and os.path.exists(a.server_log) else 0

    # baseline of the heavy report on an idle server (cold cache)
    base_c = Client(a.base, stats)
    base_c.login(a.mgmt_user, pw)
    t0 = time.perf_counter()
    base_c.call("GET", "/api/reports/management", "BASELINE reports/management (idle, cold)", params={"period": "monthly", "lang": "th", "branch": names[0]})
    print(f"idle baseline of the heavy report: {time.perf_counter() - t0:.2f} s")

    stop = threading.Event()
    heavy_threads = []
    for k in range(a.heavy):
        t = threading.Thread(target=heavy_loop, args=(a.base, stats, stop, a.mgmt_user, pw, names, a.seed + k), daemon=True)
        t.start()
        heavy_threads.append(t)

    # logins: everyone opens the app at the same moment
    clients = [Client(a.base, stats) for _ in range(n)]
    t0 = time.perf_counter()
    with cf.ThreadPoolExecutor(n) as ex:
        ok = list(ex.map(lambda ic: ic[1].login(accounts[ic[0]][0], pw), enumerate(clients)))
    print(f"logins: {sum(ok)}/{n} in {time.perf_counter() - t0:.1f} s")
    if not all(ok):
        stop.set(); raise SystemExit("some branch logins failed")
    wh = Client(a.base, stats)
    if not wh.login(a.warehouse_user, pw):
        stop.set(); raise SystemExit("warehouse login failed")
    if not a.token:
        stop.set(); raise SystemExit("the warehouse needs the write token: pass --token or set OSHINEI_SEED_TOKEN")
    wh.s.headers["X-Upload-Token"] = a.token

    used = [set() for _ in range(n)]
    rngs = [random.Random(a.seed * 1000 + i) for i in range(n)]
    rounds_out = []
    problems = []
    for ri in range(a.rounds):
        pref = ROUND_CYCLE[ri % len(ROUND_CYCLE)]
        rec = {"round": ri + 1, "preferred": pref}
        # 1. the cutoff-hour rush
        barrier = threading.Barrier(n)

        def one(i):
            barrier.wait()
            if a.stagger > 0:                                     # branches do not all press the button in the same second
                time.sleep(rngs[i].uniform(0, a.stagger))
            t = time.perf_counter()
            oid, info = branch_order(clients[i], rngs[i], used[i], pref)
            return i, oid, info, time.perf_counter() - t
        t0 = time.perf_counter()
        with cf.ThreadPoolExecutor(n) as ex:
            res = list(ex.map(one, range(n)))
        rec["order_phase_s"] = time.perf_counter() - t0
        orders = {i: oid for i, oid, info, _ in res if oid}
        rec["orders"] = len(orders)
        rec["late"] = sum(1 for _, oid, info, _ in res if oid and info.get("late"))
        rec["per_branch_flow_s"] = {"p50": pct(sorted(r[3] for r in res), 50), "max": max(r[3] for r in res)}
        for i, oid, info, _ in res:
            if not oid:
                problems.append({"round": ri + 1, "branch": accounts[i][1], "why": info})
        # 2. the warehouse, one order at a time
        t0 = time.perf_counter()
        wh.call("GET", "/api/bom-center/hub/queue", "GET hub/queue", params={"status": "submitted"})
        done = 0
        for i, oid in sorted(orders.items(), key=lambda kv: kv[1]):
            r = wh.call("POST", f"/api/bom-center/hub/process/{oid}", "POST hub/process/{id}", json={"lines": [], "po_ref": f"PO-LOAD-{oid}"})
            done += bool(r is not None and r.status_code == 200)
        rec["warehouse_phase_s"] = time.perf_counter() - t0
        rec["processed"] = done
        # 3. receiving
        barrier2 = threading.Barrier(max(len(orders), 1))

        def rcv(item):
            i, oid = item
            barrier2.wait()
            return branch_receive(clients[i], oid)
        t0 = time.perf_counter()
        with cf.ThreadPoolExecutor(max(len(orders), 1)) as ex:
            got = list(ex.map(rcv, orders.items()))
        rec["receive_phase_s"] = time.perf_counter() - t0
        rec["received"] = sum(got)
        # 4. month-end draft (optional)
        if a.month_end:
            t0 = time.perf_counter()
            with cf.ThreadPoolExecutor(n) as ex:
                me_ok = list(ex.map(branch_month_end, clients))
            rec["month_end_phase_s"] = time.perf_counter() - t0
            rec["month_end_ok"] = sum(me_ok)
        rounds_out.append(rec)
        print(f"round {ri + 1} ({pref}): orders {rec['orders']}/{n} (late {rec['late']}) in {rec['order_phase_s']:.1f}s | "
              f"warehouse {rec['processed']} in {rec['warehouse_phase_s']:.1f}s | received {rec['received']} in {rec['receive_phase_s']:.1f}s")
    stop.set()
    for t in heavy_threads:
        t.join(timeout=130)

    summ = stats.summary()
    print()
    hdr = f"{'endpoint':46} {'n':>5} {'p50':>7} {'p95':>7} {'max':>7}  {'4xx':>4} {'5xx':>4} {'exc':>4} {'exp400':>6}"
    print(hdr); print("-" * len(hdr))
    for name, s in summ.items():
        print(f"{name:46} {s['n']:5d} {s['p50']:7.3f} {s['p95']:7.3f} {s['max']:7.3f}  {s['c4xx']:4d} {s['c5xx']:4d} {s['exc']:4d} {s['expected_400']:6d}")
    fl = {k: len(v) for k, v in stats.flags.items()}
    print("\nflags (locked / timeout / 4xx / 5xx / exc):", {k: fl.get(k, 0) for k in ("locked", "timeout", "4xx", "5xx", "exc")})
    for k, v in stats.flags.items():
        for x in v[:5]:
            print("  ", k, x)
    srv = scan_server_log(a.server_log, log_offset) if a.server_log else None
    if srv:
        print("server log:", json.dumps(srv, ensure_ascii=False))
    if problems:
        print("branches that did not complete an order:", len(problems))
        for p in problems[:8]:
            print("  ", p)
    out = {"config": {"base": a.base, "branches": n, "rounds": a.rounds, "heavy": a.heavy, "month_end": a.month_end,
                      "stagger": a.stagger},
           "endpoints": summ, "rounds": rounds_out, "flags": {k: v[:50] for k, v in stats.flags.items()},
           "flag_counts": fl, "problems": problems, "server_log": srv}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("written", a.out)


if __name__ == "__main__":
    main()
