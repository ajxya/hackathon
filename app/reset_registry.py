"""Registry of reset callables for every in-memory simulation module.

Modules that keep their own in-memory state (the discharge clock, the
float pool, and — in later phases — nearby hospitals, relocation state,
event history, etc.) register a reset function here once, at import time.
`seed()` then just calls every registered function, so adding a new
stateful module later doesn't require editing the reset endpoint or seed()
itself — it self-registers.
"""

_reset_callbacks = []


def register_reset(fn):
    """Register a zero-argument function to be called on every demo reset."""
    _reset_callbacks.append(fn)
    return fn


def reset_all():
    for fn in _reset_callbacks:
        fn()
