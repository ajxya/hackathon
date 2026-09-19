"""EDFlow session summary — one structured snapshot of the whole current
session, built entirely from state that already exists elsewhere (the
scorecard, event log, reserves, relocation, and nearby-facility network).

Both the situation report (deterministic template) and the assistant
(LLM context) read from this exact function, so their numbers can never
drift from the dashboard or from each other. Nothing here makes a network
call or depends on anything but the database and other in-memory modules.
"""

from datetime import datetime

from app.event_log import get_events
from app.impact_metrics import get_impact_comparison
from app.network import get_all_facilities_summary
from app.relocation import get_active_destinations
from app.reserve import get_float_pool, get_float_pool_used, get_overflow_pool, get_overflow_used
from app.reset_registry import register_reset
from app.scorecard import get_scorecard

_session_started_at = datetime.now()


@register_reset
def _reset():
    global _session_started_at
    _session_started_at = datetime.now()


def get_session_started_at():
    return _session_started_at


def build_session_summary(conn, utilization, breach_summary, status):
    """Structured snapshot of the current session — GET /api/session-summary,
    reused by the situation report and the assistant."""
    now = datetime.now()
    scorecard = get_scorecard()

    patients_row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN source = 'ambulance' THEN 1 ELSE 0 END) AS ambulance_arrivals,
            SUM(CASE WHEN source != 'ambulance' THEN 1 ELSE 0 END) AS walk_in_arrivals,
            SUM(CASE WHEN status = 'discharged' THEN 1 ELSE 0 END) AS treated,
            SUM(CASE WHEN status = 'left_lwbs' THEN 1 ELSE 0 END) AS left_without_being_seen,
            SUM(CASE WHEN status = 'relocated' THEN 1 ELSE 0 END) AS relocated,
            COUNT(*) AS total_arrivals
        FROM patients
        """
    ).fetchone()

    nearby_facilities = [
        {
            "id": f["id"],
            "name": f["name"],
            "type": f["type"],
            "status": f["status"],
            "avg_wait_minutes": f["avg_wait_minutes"],
        }
        for f in get_all_facilities_summary()
    ]

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "session": {
            "started_at": _session_started_at.isoformat(timespec="seconds"),
            "duration_seconds": round((now - _session_started_at).total_seconds()),
        },
        "status": status,
        "peak": {
            "avg_wait_minutes": scorecard["peak_wait_minutes"],
            "avg_wait_at": scorecard["peak_wait_at"],
            "tier12_breaches": scorecard["peak_tier12_breaches"],
            "tier12_breaches_at": scorecard["peak_tier12_breaches_at"],
        },
        "breach_summary": breach_summary,
        "arrivals": {
            "ambulance": patients_row["ambulance_arrivals"] or 0,
            "walk_in": patients_row["walk_in_arrivals"] or 0,
            "total": patients_row["total_arrivals"] or 0,
        },
        "treated": patients_row["treated"] or 0,
        "left_without_being_seen": patients_row["left_without_being_seen"] or 0,
        "relocated": patients_row["relocated"] or 0,
        "float_pool": get_float_pool(),
        "float_pool_used": get_float_pool_used(),
        "overflow_pool": get_overflow_pool(),
        "overflow_used": get_overflow_used(),
        "active_relocation_destinations": get_active_destinations(),
        "nearby_facilities": nearby_facilities,
        "last_recovery_seconds": scorecard["last_recovery_seconds"],
        "currently_in_red": scorecard["currently_in_red"],
        "event_log": get_events(),
        "impact": get_impact_comparison(conn),
    }
