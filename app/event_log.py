"""EDFlow event log — a timestamped record of notable actions (status
changes, applied recommendations, relocations), so the demo can show what
happened, when, and that it worked. In-memory only, reset with everything
else.
"""

from collections import deque
from datetime import datetime

from app.reset_registry import register_reset

MAX_EVENTS = 100
_events = deque(maxlen=MAX_EVENTS)
_last_status_level = None


@register_reset
def _reset():
    global _last_status_level
    _events.clear()
    _last_status_level = None


def log_event(message):
    _events.appendleft({"time": datetime.now().strftime("%H:%M:%S"), "message": message})


def note_status(level, breach_summary):
    """Call once per /state build; only logs on an actual transition, so
    the log doesn't fill up with a line every 2-second poll."""
    global _last_status_level
    if _last_status_level is not None and _last_status_level != level:
        if level == "red":
            log_event(f"Status changed to RED — {breach_summary['tier1_2_breaches']} Tier 1-2 patient(s) past target")
        elif level == "green":
            log_event("Status recovered to GREEN")
        else:
            log_event(f"Status changed to {level.upper()}")
    _last_status_level = level


def get_events():
    return list(_events)
