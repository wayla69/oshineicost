"""Guard against the P1-split failure mode: a helper/constant moved into a
router while its consumer stayed in main (or vice-versa), leaving a global that
resolves nowhere. It never fails at import — only when the endpoint actually
runs — so three separate occurrences (the market block, COVERAGE_MIN_RATIO and
_INGREDIENT_FAMILIES) all reached production silently.

The check is a bytecode scan: for every function defined in main.py and in each
router, every global it LOADs must exist in that module's own namespace or in
builtins. Cheap, exact, and catches the whole class rather than one instance.
"""
import builtins
import dis
import importlib
import types

import pytest

ROUTERS = ["reports", "analysis", "sales", "menu", "buffet",
           "home_extra", "cost_bom", "profit_pnl"]


def _unwrap(fn):
    """Peel @ttl_cache / functools.wraps so we inspect the real endpoint body."""
    seen = set()
    while hasattr(fn, "__wrapped__") and id(fn) not in seen:
        seen.add(id(fn))
        fn = fn.__wrapped__
    return fn


def _undefined_globals(mod):
    ns = vars(mod)
    out = []
    for name, obj in list(ns.items()):
        fn = _unwrap(obj)
        if not isinstance(fn, types.FunctionType):
            continue
        # only functions whose global namespace IS this module (skip re-exports)
        if fn.__globals__ is not ns:
            continue
        try:
            instrs = list(dis.get_instructions(fn.__code__))
        except Exception:                      # pragma: no cover - defensive
            continue
        for ins in instrs:
            if ins.opname in ("LOAD_GLOBAL", "LOAD_NAME"):
                g = ins.argval
                if g in ns or hasattr(builtins, g):
                    continue
                out.append(f"{mod.__name__}.{name} -> {g}")
    return out


@pytest.mark.parametrize("modname", ["main"] + [f"routers.{r}" for r in ROUTERS])
def test_no_orphan_globals(client, modname):
    """Every global a function references must resolve in its own module."""
    mod = importlib.import_module(modname)
    missing = _undefined_globals(mod)
    assert not missing, (
        "global(s) resolve nowhere — a helper/constant was moved away from its "
        "consumer (see the P1 router split):\n  " + "\n  ".join(sorted(set(missing)))
    )
