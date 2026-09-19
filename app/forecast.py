"""EDFlow proactive breach forecasting (Step 3).

Tracks a short rolling history of how many beds are available, so
get_recommendations() can estimate how many simulated minutes remain
before beds run out entirely — which is the moment any newly-arriving
Tier 1-2 patient (target wait: 0-10 min) breaches. This lets the
recommendation ladder start escalating from the trend *before* a breach
has actually happened, not just react to one that already did.

This is a simple linear-rate projection from recent samples, not a real
forecasting model — good enough to say "beds are filling at roughly this
rate" for a demo.
"""

from collections import deque
from datetime import datetime

from app.config import SIM_MINUTES_PER_REAL_SECOND
from app.reset_registry import register_reset

_HISTORY_SECONDS = 20  # how far back the rate estimate looks
_MAX_SAMPLES = 12

_samples = deque(maxlen=_MAX_SAMPLES)  # [(timestamp, beds_available), ...]


@register_reset
def reset_forecast():
    _samples.clear()


def note(beds_available):
    """Record one more (now, beds_available) sample, and drop anything
    older than _HISTORY_SECONDS so the rate estimate reflects recent
    behavior, not the whole session."""
    now = datetime.now()
    _samples.append((now, beds_available))
    cutoff = now.timestamp() - _HISTORY_SECONDS
    while len(_samples) > 2 and _samples[0][0].timestamp() < cutoff:
        _samples.popleft()


def estimate_minutes_to_breach(beds_available):
    """Simulated minutes until beds run out, projected from how fast
    they've been filling over the tracked window. None if there's not
    enough history yet, or beds aren't currently trending toward zero
    (holding steady or recovering) — there's nothing to project."""
    if len(_samples) < 2:
        return None

    oldest_time, oldest_available = _samples[0]
    newest_time, _ = _samples[-1]
    elapsed_seconds = (newest_time - oldest_time).total_seconds()
    if elapsed_seconds <= 0:
        return None

    depletion_per_sec = (oldest_available - beds_available) / elapsed_seconds
    if depletion_per_sec <= 0:
        return None

    if beds_available <= 0:
        return 0.0

    real_seconds_to_zero = beds_available / depletion_per_sec
    return round(real_seconds_to_zero * SIM_MINUTES_PER_REAL_SECOND, 1)
