"""EDFlow shadow baseline simulation — a parallel, in-memory copy of the
home ED that receives the exact same arrival stream as the real one (same
tier, same arrival time, same length of stay — see arrive() below, which
never draws its own randomness, only mirrors values the real ED already
generated in app/patients.py and app/seed.py) and runs the exact same
discharge logic from Steps 1-2 (per-patient length of stay, bed turnover,
LWBS), but never applies a recommendation, never uses the float pool or
overflow beds, and never relocates anyone.

It represents "the ED using only its own staff and beds" — the
counterfactual the situation report and Impact tab compare EDFlow's
actual outcome against (Step 5).

Kept as lightweight in-memory state, not a second set of database tables:
it only needs enough per-patient detail to compute aggregate wait/breach
metrics, not a browsable patient list, bed labels, or named staff. Its
capacity always matches the real ED's own baseline (app/config.py) and
never changes — there's no float pool or overflow for it to draw from.

advance() is called from app/allocation.py's advance_state() — the same
function every real-ED tick and poll already goes through — so both
simulations always advance on exactly the same ticks.
"""

from datetime import datetime, timedelta

from app.config import (
    BED_TURNOVER_MINUTES,
    BEDS_TOTAL,
    NURSE_COUNT,
    NURSE_MAX_PATIENTS,
    PHYSICIAN_COUNT,
    PHYSICIAN_MAX_PATIENTS,
    SIM_MINUTES_PER_REAL_SECOND,
    TIER_TARGET_MINUTES,
)
from app.reset_registry import register_reset

LWBS_MULTIPLIER = 2  # matches app/lwbs.py's rule for the real ED

_next_patient_id = 1
_patients = {}
_beds = []
_nurses = []
_physicians = []
_peak_avg_wait_minutes = 0.0

# Matches app/logic.py's get_utilization — a flat per-waiting-patient
# figure, not a true average of individual wait times, kept identical so
# the comparison in Step 5 reconciles with the dashboard's own display.
AVG_WAIT_MINUTES_PER_WAITING_PATIENT = 5.0


def _fresh_capacity():
    return (
        [{"status": "available", "cleaning_until": None} for _ in range(BEDS_TOTAL)],
        [{"current_patients": 0} for _ in range(NURSE_COUNT)],
        [{"current_patients": 0} for _ in range(PHYSICIAN_COUNT)],
    )


@register_reset
def reset_baseline():
    global _next_patient_id, _patients, _beds, _nurses, _physicians, _peak_avg_wait_minutes
    _next_patient_id = 1
    _patients = {}
    _beds, _nurses, _physicians = _fresh_capacity()
    _peak_avg_wait_minutes = 0.0


# Populate capacity at import time too, so the module is usable even
# before the first reset (mirrors the pattern in app/network.py).
_beds, _nurses, _physicians = _fresh_capacity()


def arrive(tier, arrival_time, los_seconds, force_in_bed=False):
    """Add a new waiting patient to the shadow simulation — called
    immediately after the real ED creates the corresponding patient, with
    the exact same tier, arrival time, and (already-drawn) length of
    stay. Draws no randomness of its own.

    force_in_bed is only used for the initial seed batch (app/seed.py),
    where the real ED manually pre-places a couple of specific starting
    patients regardless of tier-priority order, purely for starting-
    scenario flavor. Without mirroring that exact choice here too, the
    shadow's own (perfectly reasonable) tier-priority placement would
    admit a different subset of that tiny 6-patient batch, showing up as
    a spurious nonzero "difference" before any real intervention has
    happened."""
    global _next_patient_id
    patient_id = _next_patient_id
    _next_patient_id += 1
    _patients[patient_id] = {
        "id": patient_id,
        "tier": tier,
        "arrival_time": arrival_time,
        "los_seconds": los_seconds,
        "status": "waiting",
        "bed_index": None,
        "nurse_index": None,
        "physician_index": None,
        "bed_assigned_at": None,
        "discharge_due_at": None,
        "left_at": None,
    }
    if force_in_bed:
        _try_place(_patients[patient_id], arrival_time)
    else:
        _advance(datetime.now())
    return patient_id


def advance():
    """Called alongside the real ED's advance_state() so both simulations
    progress on exactly the same ticks."""
    _advance(datetime.now())


def _advance(now):
    _release_finished_cleanings(now)
    _process_discharges(now)
    _process_lwbs(now)
    _place_waiting_patients(now)
    _note_peak_wait(now)


def _note_peak_wait(now):
    global _peak_avg_wait_minutes
    waiting_count = sum(1 for p in _patients.values() if p["status"] == "waiting")
    current_avg_wait = waiting_count * AVG_WAIT_MINUTES_PER_WAITING_PATIENT
    _peak_avg_wait_minutes = max(_peak_avg_wait_minutes, current_avg_wait)


def get_peak_avg_wait_minutes():
    return _peak_avg_wait_minutes


def _release_finished_cleanings(now):
    for bed in _beds:
        if bed["status"] == "cleaning" and bed["cleaning_until"] is not None and now >= bed["cleaning_until"]:
            bed["status"] = "available"
            bed["cleaning_until"] = None


def _process_discharges(now):
    for patient in _patients.values():
        if patient["status"] != "in_bed" or patient["discharge_due_at"] is None or now < patient["discharge_due_at"]:
            continue

        bed = _beds[patient["bed_index"]]
        bed["status"] = "cleaning"
        bed["cleaning_until"] = now + timedelta(seconds=BED_TURNOVER_MINUTES / SIM_MINUTES_PER_REAL_SECOND)

        if patient["nurse_index"] is not None:
            _nurses[patient["nurse_index"]]["current_patients"] -= 1
        if patient["physician_index"] is not None:
            _physicians[patient["physician_index"]]["current_patients"] -= 1

        patient["status"] = "discharged"
        patient["bed_index"] = None
        patient["nurse_index"] = None
        patient["physician_index"] = None
        patient["discharge_due_at"] = None


def _process_lwbs(now):
    for patient in _patients.values():
        if patient["status"] != "waiting" or patient["tier"] not in (3, 4, 5):
            continue
        elapsed_seconds = max((now - patient["arrival_time"]).total_seconds(), 0)
        wait_minutes = elapsed_seconds * SIM_MINUTES_PER_REAL_SECOND
        target = TIER_TARGET_MINUTES.get(patient["tier"], 0)
        if target and wait_minutes > target * LWBS_MULTIPLIER:
            patient["status"] = "left_lwbs"
            patient["left_at"] = now


def _place_waiting_patients(now):
    waiting = sorted(
        (p for p in _patients.values() if p["status"] == "waiting"),
        key=lambda p: (p["tier"], p["arrival_time"]),
    )
    for patient in waiting:
        _try_place(patient, now)


def _try_place(patient, now):
    bed_index = next((i for i, b in enumerate(_beds) if b["status"] == "available"), None)
    if bed_index is None:
        return False
    nurse_index = _least_busy(_nurses, NURSE_MAX_PATIENTS)
    if nurse_index is None:
        return False
    physician_index = _least_busy(_physicians, PHYSICIAN_MAX_PATIENTS)
    if physician_index is None:
        return False

    _beds[bed_index]["status"] = "occupied"
    _nurses[nurse_index]["current_patients"] += 1
    _physicians[physician_index]["current_patients"] += 1

    patient["status"] = "in_bed"
    patient["bed_index"] = bed_index
    patient["nurse_index"] = nurse_index
    patient["physician_index"] = physician_index
    patient["bed_assigned_at"] = now
    patient["discharge_due_at"] = now + timedelta(seconds=patient["los_seconds"])
    return True


def _least_busy(staff_list, max_patients):
    best_index, best_count = None, None
    for i, staff in enumerate(staff_list):
        if staff["current_patients"] < max_patients and (best_count is None or staff["current_patients"] < best_count):
            best_index, best_count = i, staff["current_patients"]
    return best_index


def get_snapshot():
    """Aggregate counts for testing/inspection. The full comparison
    metrics (avg wait, peak wait, breach-minutes, patient-minutes waited)
    are computed in app/impact_metrics.py — Step 5."""
    counts = {"waiting": 0, "in_bed": 0, "discharged": 0, "left_lwbs": 0}
    for patient in _patients.values():
        counts[patient["status"]] = counts.get(patient["status"], 0) + 1
    beds_occupied = sum(1 for bed in _beds if bed["status"] == "occupied")
    beds_cleaning = sum(1 for bed in _beds if bed["status"] == "cleaning")
    return {
        "patients_total": len(_patients),
        "status_counts": counts,
        "beds_occupied": beds_occupied,
        "beds_cleaning": beds_cleaning,
        "beds_total": BEDS_TOTAL,
    }


def get_patients():
    """Read-only access to the raw patient dicts, for Step 5's metrics
    computation."""
    return list(_patients.values())
