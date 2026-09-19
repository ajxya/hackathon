"""Server-side surge state (Step 1).

Before this module existed, "Run Surge" was purely a client-side
`setInterval` that called `POST /simulate/tick` once a second forever,
with no server-side notion of how long it had been running or how many
patients it had generated. Reloading the page, or anything that created a
second interval, could keep generating arrivals indefinitely. Moving the
surge's length and total-arrivals cap here means the browser can only ever
ask "how many patients arrive on this tick" — it can no longer make the
surge longer or bigger than the server allows, and a hard stop is
guaranteed even if the client never calls /surge/stop.
"""

import random

from app.config import (
    ARRIVALS_PER_TICK_HEAVY_MAX,
    ARRIVALS_PER_TICK_HEAVY_MIN,
    ARRIVALS_PER_TICK_MAX,
    ARRIVALS_PER_TICK_MIN,
    SURGE_DURATION_TICKS,
    SURGE_HEAVY_TOTAL_ARRIVALS_RANGE,
    SURGE_TOTAL_ARRIVALS_RANGE,
)
from app.reset_registry import register_reset

_active = False
_heavy = False
_ticks_elapsed = 0
_arrivals_generated = 0
_target_total = 0


def is_active():
    return _active


def start_surge(heavy=False):
    """Start a surge. No-op (returns False) if one is already running, so
    clicking Run Surge mid-surge can never start a second generator."""
    global _active, _heavy, _ticks_elapsed, _arrivals_generated, _target_total
    if _active:
        return False
    value_range = SURGE_HEAVY_TOTAL_ARRIVALS_RANGE if heavy else SURGE_TOTAL_ARRIVALS_RANGE
    _active = True
    _heavy = heavy
    _ticks_elapsed = 0
    _arrivals_generated = 0
    _target_total = random.randint(*value_range)
    return True


def stop_surge():
    """Stop Surge / Reset Demo both call this — cancels the generator
    immediately, with nothing left in flight to time out on its own."""
    global _active
    _active = False


@register_reset
def reset_surge():
    global _active, _heavy, _ticks_elapsed, _arrivals_generated, _target_total
    _active = False
    _heavy = False
    _ticks_elapsed = 0
    _arrivals_generated = 0
    _target_total = 0


def get_state():
    return {
        "active": _active,
        "ticks_elapsed": _ticks_elapsed,
        "duration_ticks": SURGE_DURATION_TICKS,
        "arrivals_generated": _arrivals_generated,
        "target_total": _target_total,
    }


def tick_arrival_count():
    """How many patients arrive on this tick, following a ramp-up / peak /
    ramp-down profile over SURGE_DURATION_TICKS, capped by the remaining
    arrivals budget. Returns 0 (and ends the surge) once the duration or
    the total-arrivals cap is reached — the hard stop the surge needs."""
    global _active, _ticks_elapsed, _arrivals_generated

    if not _active:
        return 0

    if _ticks_elapsed >= SURGE_DURATION_TICKS:
        _active = False
        return 0

    remaining_budget = _target_total - _arrivals_generated
    if remaining_budget <= 0:
        _active = False
        return 0

    # Ramp up over the first 30% of the surge, hold at peak for the middle
    # 40%, then ramp back down over the last 30% — a shape, not a flood.
    progress = _ticks_elapsed / SURGE_DURATION_TICKS
    if progress < 0.3:
        intensity = progress / 0.3
    elif progress < 0.7:
        intensity = 1.0
    else:
        intensity = max(0.0, (1.0 - progress) / 0.3)

    if _heavy:
        base = random.randint(ARRIVALS_PER_TICK_HEAVY_MIN, ARRIVALS_PER_TICK_HEAVY_MAX)
    else:
        base = random.randint(ARRIVALS_PER_TICK_MIN, ARRIVALS_PER_TICK_MAX)
    count = min(round(base * intensity), remaining_budget)

    _ticks_elapsed += 1
    _arrivals_generated += count
    return count
