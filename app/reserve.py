"""EDFlow hospital-wide reserves — capacity beyond the ED's own fixed
roster, that the recommendation ladder can pull from as it escalates.

Two separate reserves, matching two separate rungs of the ladder:
- Float pool (rung 2): extra nurses, physicians, and rooms borrowed from
  elsewhere in the hospital.
- Overflow pool (rung 3): extra physical beds (e.g. hallway/surge beds)
  that can be stood up within the ED itself as a more drastic step.

This is intentionally a simple read-only model for this demo: nothing here
actually "checks out" capacity into the ED's own tables. It just reports
what's available so recommendations can be capped and phrased realistically
(e.g. "pull 2 nurses from the float pool" instead of a vague, uncapped
"add 2 nurses").
"""

import random

from app.reset_registry import register_reset

_float_pool = {"nurses_available": 8, "physicians_available": 3, "rooms_available": 3}
_overflow_pool = {"overflow_beds_available": 3}


def get_float_pool():
    return dict(_float_pool)


def get_overflow_pool():
    return dict(_overflow_pool)


def decrement_float_pool(nurses=0, physicians=0, rooms=0):
    """Actually check reserves out of the pool (Apply button, Step 3+)."""
    _float_pool["nurses_available"] = max(0, _float_pool["nurses_available"] - nurses)
    _float_pool["physicians_available"] = max(0, _float_pool["physicians_available"] - physicians)
    _float_pool["rooms_available"] = max(0, _float_pool["rooms_available"] - rooms)


def decrement_overflow_pool(beds=0):
    _overflow_pool["overflow_beds_available"] = max(0, _overflow_pool["overflow_beds_available"] - beds)


@register_reset
def reset_float_pool():
    """Randomize the reserve a bit on each demo reset, so hospital-wide
    conditions feel like they vary between runs."""
    _float_pool["nurses_available"] = random.randint(4, 10)
    _float_pool["physicians_available"] = random.randint(1, 5)
    _float_pool["rooms_available"] = random.randint(1, 4)


@register_reset
def reset_overflow_pool():
    _overflow_pool["overflow_beds_available"] = random.randint(2, 6)
