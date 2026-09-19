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

from app.config import FLOAT_POOL_NURSES_RANGE, FLOAT_POOL_PHYSICIANS_RANGE, FLOAT_POOL_ROOMS_RANGE, OVERFLOW_BEDS_RANGE
from app.reset_registry import register_reset

_float_pool = {"nurses_available": 8, "physicians_available": 3, "rooms_available": 3}
_overflow_pool = {"overflow_beds_available": 3}

# Running totals of how much has actually been pulled this session, for the
# situation report / assistant ("currently in use") — separate from the
# pools above, which only track what's still left.
_float_pool_used = {"nurses": 0, "physicians": 0, "rooms": 0}
_overflow_used = {"beds": 0}


def get_float_pool():
    return dict(_float_pool)


def get_overflow_pool():
    return dict(_overflow_pool)


def get_float_pool_used():
    return dict(_float_pool_used)


def get_overflow_used():
    return dict(_overflow_used)


def decrement_float_pool(nurses=0, physicians=0, rooms=0):
    """Actually check reserves out of the pool (Apply button, Step 3+)."""
    _float_pool["nurses_available"] = max(0, _float_pool["nurses_available"] - nurses)
    _float_pool["physicians_available"] = max(0, _float_pool["physicians_available"] - physicians)
    _float_pool["rooms_available"] = max(0, _float_pool["rooms_available"] - rooms)
    _float_pool_used["nurses"] += nurses
    _float_pool_used["physicians"] += physicians
    _float_pool_used["rooms"] += rooms


def decrement_overflow_pool(beds=0):
    _overflow_pool["overflow_beds_available"] = max(0, _overflow_pool["overflow_beds_available"] - beds)
    _overflow_used["beds"] += beds


def return_float_pool(nurses=0, physicians=0, rooms=0):
    """The reverse of decrement_float_pool — an idle float nurse/
    physician/room that's no longer needed goes back into the reserve
    (app/allocation.py, once utilization is comfortably low)."""
    _float_pool["nurses_available"] += nurses
    _float_pool["physicians_available"] += physicians
    _float_pool["rooms_available"] += rooms
    _float_pool_used["nurses"] = max(0, _float_pool_used["nurses"] - nurses)
    _float_pool_used["physicians"] = max(0, _float_pool_used["physicians"] - physicians)
    _float_pool_used["rooms"] = max(0, _float_pool_used["rooms"] - rooms)


def return_overflow_pool(beds=0):
    _overflow_pool["overflow_beds_available"] += beds
    _overflow_used["beds"] = max(0, _overflow_used["beds"] - beds)


@register_reset
def reset_float_pool():
    """Randomize the reserve a bit on each demo reset, so hospital-wide
    conditions feel like they vary between runs. Ranges live in
    app/config.py."""
    _float_pool["nurses_available"] = random.randint(*FLOAT_POOL_NURSES_RANGE)
    _float_pool["physicians_available"] = random.randint(*FLOAT_POOL_PHYSICIANS_RANGE)
    _float_pool["rooms_available"] = random.randint(*FLOAT_POOL_ROOMS_RANGE)
    _float_pool_used["nurses"] = 0
    _float_pool_used["physicians"] = 0
    _float_pool_used["rooms"] = 0


@register_reset
def reset_overflow_pool():
    _overflow_pool["overflow_beds_available"] = random.randint(*OVERFLOW_BEDS_RANGE)
    _overflow_used["beds"] = 0
