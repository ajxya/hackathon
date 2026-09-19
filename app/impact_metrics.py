"""EDFlow impact metrics — the head-to-head comparison between the real ED
(EDFlow's recommendations applied as the user approves them) and the
shadow baseline simulation (app/baseline_simulation.py; the exact same
arrivals, but never any float pool, overflow, or relocation help).

Every definition here matches the scorecard and breach summary exactly —
same TIER_TARGET_MINUTES, same SIM_MINUTES_PER_REAL_SECOND, same flat
per-waiting-patient avg-wait formula as app/logic.py's get_utilization()
— so these numbers reconcile with the rest of the dashboard rather than
being a second, disagreeing set of math.

This is a simulated comparison: the baseline is a parallel copy of this
same synthetic ED, not a real historical control group.
"""

from datetime import datetime

from app import baseline_simulation
from app.config import SIM_MINUTES_PER_REAL_SECOND, TIER_TARGET_MINUTES
from app.event_log import get_events
from app.scorecard import get_scorecard

AVG_WAIT_MINUTES_PER_WAITING_PATIENT = 5.0  # matches app/logic.py's get_utilization

BASELINE_DEFINITION = (
    "The baseline is a simulated parallel run of this same ED — identical arrivals, "
    "identical discharge behavior — but with no float pool, no overflow beds, and no relocation ever applied."
)


def _resolve_end_time(patient, now):
    """When this patient's wait effectively stopped counting: now if still
    waiting, when they were placed if admitted/discharged, when they left
    if LWBS. Relocated patients are excluded entirely (see below)."""
    status = patient["status"]
    if status == "waiting":
        return now
    if status in ("in_bed", "discharged"):
        return patient["bed_assigned_at"] or now
    if status == "left_lwbs":
        return patient["left_at"] or now
    return None  # relocated (or any other status): excluded


def compute_metrics(patients, now, peak_avg_wait_minutes):
    """patients: an iterable of dicts with keys acuity, arrival_time
    (datetime), status, bed_assigned_at (datetime|None), left_at
    (datetime|None). Relocated patients are excluded — relocation only
    ever happens on the real side, and its outcome depends on a different
    facility's performance, not this ED's own capacity."""
    waiting_count = 0
    total_patient_minutes = 0.0
    tier12_breach_minutes = 0.0
    lwbs_count = 0

    for patient in patients:
        end_time = _resolve_end_time(patient, now)
        if end_time is None:
            continue

        if patient["status"] == "waiting":
            waiting_count += 1
        elif patient["status"] == "left_lwbs":
            lwbs_count += 1

        elapsed_seconds = max((end_time - patient["arrival_time"]).total_seconds(), 0)
        wait_minutes = elapsed_seconds * SIM_MINUTES_PER_REAL_SECOND
        total_patient_minutes += wait_minutes

        if patient["acuity"] in (1, 2):
            target = TIER_TARGET_MINUTES.get(patient["acuity"], 0)
            tier12_breach_minutes += max(0.0, wait_minutes - target)

    return {
        "avg_wait_minutes": round(waiting_count * AVG_WAIT_MINUTES_PER_WAITING_PATIENT, 1),
        "peak_avg_wait_minutes": round(peak_avg_wait_minutes, 1),
        "tier12_breach_minutes": round(tier12_breach_minutes, 1),
        "total_patient_minutes_waited": round(total_patient_minutes, 1),
        "left_without_being_seen": lwbs_count,
    }


def _normalize_real_patients(conn):
    rows = conn.execute(
        "SELECT acuity, arrival_time, status, bed_assigned_at, left_at FROM patients WHERE status != 'relocated'"
    ).fetchall()
    normalized = []
    for row in rows:
        normalized.append(
            {
                "acuity": row["acuity"],
                "arrival_time": datetime.fromisoformat(row["arrival_time"]),
                "status": row["status"],
                "bed_assigned_at": datetime.fromisoformat(row["bed_assigned_at"]) if row["bed_assigned_at"] else None,
                "left_at": datetime.fromisoformat(row["left_at"]) if row["left_at"] else None,
            }
        )
    return normalized


def _normalize_shadow_patients():
    return [
        {
            "acuity": p["tier"],
            "arrival_time": p["arrival_time"],
            "status": p["status"],
            "bed_assigned_at": p["bed_assigned_at"],
            "left_at": p["left_at"],
        }
        for p in baseline_simulation.get_patients()
    ]


def get_impact_comparison(conn):
    """The full with/without EDFlow comparison, plus a ready-to-display
    headline. If nothing has been applied yet, with/without are
    mathematically identical (nothing has diverged the two simulations),
    and any_intervention_applied is False so the UI can say so plainly
    instead of implying a 0% improvement is meaningful."""
    now = datetime.now()

    with_edflow = compute_metrics(_normalize_real_patients(conn), now, get_scorecard()["peak_wait_minutes"])
    without_edflow = compute_metrics(
        _normalize_shadow_patients(), now, baseline_simulation.get_peak_avg_wait_minutes()
    )

    # Whether float/overflow is CURRENTLY checked out isn't the right
    # signal here — Step 2's auto-return means it drops back to zero once
    # things settle, even though an intervention genuinely happened and
    # had a lasting effect. The event log is never cleared except on
    # reset, so "was anything ever applied this session" is answered by
    # whether it ever logged an "Applied: ..." action.
    any_intervention_applied = any(event["message"].startswith("Applied:") for event in get_events())

    # Without any intervention, the two simulations should be identical —
    # any nonzero gap here is just sampling noise from two independently-
    # ticking simulations (each keyed off its own datetime.now() calls),
    # not a real effect. Force the reported savings to a clean zero rather
    # than displaying that noise as if it meant something (per the spec:
    # "if no recommendations were applied, show zero savings and say so").
    if any_intervention_applied:
        breach_minutes_saved = without_edflow["tier12_breach_minutes"] - with_edflow["tier12_breach_minutes"]
        minutes_saved = without_edflow["total_patient_minutes_waited"] - with_edflow["total_patient_minutes_waited"]
        breach_pct_reduction = (
            round(breach_minutes_saved / without_edflow["tier12_breach_minutes"] * 100, 1)
            if without_edflow["tier12_breach_minutes"] > 0
            else 0.0
        )
        difference = {
            "avg_wait_minutes": round(without_edflow["avg_wait_minutes"] - with_edflow["avg_wait_minutes"], 1),
            "peak_avg_wait_minutes": round(
                without_edflow["peak_avg_wait_minutes"] - with_edflow["peak_avg_wait_minutes"], 1
            ),
            "tier12_breach_minutes": round(breach_minutes_saved, 1),
            "total_patient_minutes_waited": round(minutes_saved, 1),
            "left_without_being_seen": without_edflow["left_without_being_seen"] - with_edflow["left_without_being_seen"],
        }
    else:
        breach_pct_reduction = 0.0
        minutes_saved = 0.0
        difference = {
            "avg_wait_minutes": 0.0,
            "peak_avg_wait_minutes": 0.0,
            "tier12_breach_minutes": 0.0,
            "total_patient_minutes_waited": 0.0,
            "left_without_being_seen": 0,
        }

    return {
        "baseline_definition": BASELINE_DEFINITION,
        "any_intervention_applied": any_intervention_applied,
        "with_edflow": with_edflow,
        "without_edflow": without_edflow,
        "difference": difference,
        "headline": {
            "breach_pct_reduction": breach_pct_reduction,
            "patient_minutes_saved": round(minutes_saved, 1),
        },
    }
