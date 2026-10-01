"""The response cache under load (core.ttl_cache), 2026-10-01.

Production is one shared vCPU; on 2026-09-30 a burst of cold reports starved
every other request. These pin the behaviours that stop that: one computation
per key, a cap on concurrent heavy work, stale-while-refresh, a generation
guard against storing pre-swap numbers, warming after a swap, a byte cap.
Prefix invalidation is used throughout so no test needs the database.
"""
import json
import threading
import time

import core


def _body(resp):
    return json.loads(resp.body)


def _fresh(name, fn):
    """A uniquely named cached function (ttl_cache refuses duplicate names)."""
    fn.__name__ = name
    return core.ttl_cache(fn)


def test_concurrent_misses_compute_once():
    calls, gate = [], threading.Event()

    def report(x: int = 1):
        calls.append(x)
        gate.wait(2)
        return {"x": x}
    cached = _fresh("_t_single_flight", report)
    core.invalidate_cache("_t_single_flight")
    out = []
    threads = [threading.Thread(target=lambda: out.append(_body(cached(x=5)))) for _ in range(6)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join(5)
    assert calls == [5]                       # six requests, one computation
    assert out == [{"x": 5}] * 6


def test_heavy_work_is_capped(monkeypatch):
    sem = threading.BoundedSemaphore(2)
    monkeypatch.setattr(core, "_heavy_sem", sem)
    running, peak, lock = [0], [0], threading.Lock()

    def report(i: int = 0):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.15)
        with lock:
            running[0] -= 1
        return {"i": i}
    cached = _fresh("_t_capped", report)
    core.invalidate_cache("_t_capped")
    threads = [threading.Thread(target=cached, kwargs={"i": i}) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert peak[0] == 2                        # 8 different reports, never more than 2 at once


def test_stale_entry_served_then_refreshed():
    n = [0]

    def report():
        n[0] += 1
        return {"v": n[0]}
    cached = _fresh("_t_stale", report)
    core.invalidate_cache("_t_stale")
    assert _body(cached()) == {"v": 1}
    key = core._cache_key("_t_stale", {})
    with core._cache_lock:                    # age the entry past the TTL, inside the stale window
        ts, data = core._resp_cache[key]
        core._resp_cache[key] = (ts - core._CACHE_TTL - 5, data)
    t0 = time.perf_counter()
    assert _body(cached()) == {"v": 1}         # old answer, at once
    assert time.perf_counter() - t0 < 0.1
    for _ in range(50):                        # ...while one background refresh runs
        if _body(cached()) == {"v": 2}:
            break
        time.sleep(0.05)
    assert _body(cached()) == {"v": 2}
    assert n[0] == 2


def test_result_computed_before_invalidation_is_not_stored():
    started, release = threading.Event(), threading.Event()

    def report():
        started.set()
        release.wait(2)
        return {"old": True}
    cached = _fresh("_t_generation", report)
    core.invalidate_cache("_t_generation")
    t = threading.Thread(target=cached)
    t.start()
    started.wait(2)
    core.invalidate_cache("_t_generation")     # e.g. a data swap lands mid-computation
    release.set()
    t.join(5)
    assert core._cache_key("_t_generation", {}) not in core._resp_cache


def test_warm_recomputes_the_most_requested_reports():
    n = [0]

    def report(day: str = "x"):
        n[0] += 1
        return {"day": day}
    cached = _fresh("_t_warm", report)
    core.invalidate_cache("_t_warm")
    for _ in range(3):
        cached(day="2026-09-30")
    core.invalidate_cache("_t_warm")           # the swap drops it
    assert core._cache_key("_t_warm", {"day": "2026-09-30"}) not in core._resp_cache
    before = n[0]
    core.warm_cache_async(top=500).join(10)
    assert core._cache_key("_t_warm", {"day": "2026-09-30"}) in core._resp_cache
    assert n[0] == before + 1


def test_cache_stays_under_its_byte_cap(monkeypatch):
    monkeypatch.setattr(core, "_CACHE_MAX_BYTES", 4000)

    def report(i: int = 0):
        return {"pad": "x" * 900, "i": i}
    cached = _fresh("_t_bytes", report)
    core.invalidate_cache("_t_bytes")
    for i in range(12):
        cached(i=i)
    mine = {k: v for k, v in core._resp_cache.items() if k.startswith("_t_bytes")}
    assert sum(len(v[1]) for v in mine.values()) <= 4000
    assert core._cache_key("_t_bytes", {"i": 11}) in core._resp_cache   # newest kept
