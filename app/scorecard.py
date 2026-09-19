"""EDFlow session scorecard — running maximums observed during the current
run (peak wait, peak breaches) and how long the last red episode lasted, so
the Impact tab can show what a surge actually cost and how fast it
recovered. Tracked server-side (not per-browser) so every viewer sees the
same numbers, and reset with everything else.
"""

from datetime import datetime

from app.reset_registry import register_reset

_peak_wait_minutes = 0.0
_peak_wait_at = None
_peak_tier12_breaches = 0
_peak_tier12_breaches_at = None
_red_started_at = None
_last_recovery_seconds = None


@register_reset
def _reset():
    global _peak_wait_minutes, _peak_wait_at, _peak_tier12_breaches, _peak_tier12_breaches_at
    global _red_started_at, _last_recovery_seconds
    _peak_wait_minutes = 0.0
    _peak_wait_at = None
    _peak_tier12_breaches = 0
    _peak_tier12_breaches_at = None
    _red_started_at = None
    _last_recovery_seconds = None


def note(utilization, breach_summary, status_level):
    """Call once per /state build to update the running peaks."""
    global _peak_wait_minutes, _peak_wait_at, _peak_tier12_breaches, _peak_tier12_breaches_at
    global _red_started_at, _last_recovery_seconds

    now = datetime.now()

    if utilization["avg_wait_minutes"] > _peak_wait_minutes:
        _peak_wait_minutes = utilization["avg_wait_minutes"]
        _peak_wait_at = now

    if breach_summary["tier1_2_breaches"] > _peak_tier12_breaches:
        _peak_tier12_breaches = breach_summary["tier1_2_breaches"]
        _peak_tier12_breaches_at = now

    if status_level == "red" and _red_started_at is None:
        _red_started_at = now
    elif status_level == "green" and _red_started_at is not None:
        _last_recovery_seconds = (now - _red_started_at).total_seconds()
        _red_started_at = None


def get_scorecard():
    return {
        "peak_wait_minutes": round(_peak_wait_minutes, 1),
        "peak_wait_at": _peak_wait_at.strftime("%H:%M:%S") if _peak_wait_at else None,
        "peak_tier12_breaches": _peak_tier12_breaches,
        "peak_tier12_breaches_at": (
            _peak_tier12_breaches_at.strftime("%H:%M:%S") if _peak_tier12_breaches_at else None
        ),
        "last_recovery_seconds": round(_last_recovery_seconds, 1) if _last_recovery_seconds is not None else None,
        "currently_in_red": _red_started_at is not None,
    }
