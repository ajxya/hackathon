"""EDFlow relocation — moving lower-tier patients to nearby facilities when
the home ED's own resources, float pool, and overflow capacity are all
exhausted and Tier 1-2 patients are still past target.

Rules-based and user-approved (there's no automatic diversion): never
relocate a Tier 1 patient, prefer diverting lower-tier ambulance arrivals
first (they're easiest to redirect before they ever reach the ED), then
low-tier walk-ins, route by capability and estimated wait, and never send
anyone to a facility that's currently red.
"""

from app.network import get_all_facilities_summary, get_hospital, get_urgent_care
from app.reset_registry import register_reset

MAX_RELOCATE_PER_APPLY = 5

# Same heuristic used for the Nearby Hospitals panel (kept in sync
# deliberately — one's the display/ranking copy in JS, this is the one
# that actually decides where a real patient goes).
TIER_RELEVANT_CAPABILITIES = {
    1: ["trauma", "cardiac", "stroke"],
    2: ["trauma", "cardiac", "stroke"],
    3: ["trauma", "cardiac", "stroke", "urgent_care"],
    4: ["urgent_care", "pediatric"],
    5: ["urgent_care", "pediatric"],
}

_active_destinations = {}  # facility id -> facility name


@register_reset
def reset_relocation():
    _active_destinations.clear()


def get_active_destinations():
    return list(_active_destinations.values())


def _pick_destination(tier):
    """Best eligible facility for this tier: never red, prefer a
    capability match, then lowest wait for this tier."""
    candidates = [f for f in get_all_facilities_summary() if f["status"] != "red"]
    if not candidates:
        return None

    relevant = TIER_RELEVANT_CAPABILITIES.get(tier, [])

    def sort_key(f):
        matches = any(c in relevant for c in f["capabilities"])
        if f["type"] == "hospital":
            wait = f["estimated_wait_by_tier"].get(str(tier), f["avg_wait_minutes"])
        else:
            wait = f["avg_wait_minutes"]
        return (0 if matches else 1, wait)

    candidates.sort(key=sort_key)
    return candidates[0]


def relocate_patients(conn):
    """Pick up to MAX_RELOCATE_PER_APPLY eligible waiting patients (never
    Tier 1; ambulance arrivals first, then walk-ins, most severe of the
    eligible pool first) and move each to the best available nearby
    facility. Returns how many were actually relocated."""
    candidates = conn.execute(
        """
        SELECT id, acuity FROM patients
        WHERE status = 'waiting' AND acuity > 1
        ORDER BY
            CASE WHEN source = 'ambulance' THEN 0 ELSE 1 END,
            acuity ASC,
            arrival_time ASC
        LIMIT ?
        """,
        (MAX_RELOCATE_PER_APPLY,),
    ).fetchall()

    relocated = 0
    for patient in candidates:
        destination = _pick_destination(patient["acuity"])
        if not destination:
            break  # nothing eligible left to send anyone to

        conn.execute("UPDATE patients SET status = 'relocated' WHERE id = ?", (patient["id"],))
        _active_destinations[destination["id"]] = destination["name"]

        if destination["type"] == "hospital":
            hospital = get_hospital(destination["id"])
            hospital.receive_relocated_patient(patient["acuity"])
        else:
            urgent_care = get_urgent_care(destination["id"])
            if urgent_care:
                urgent_care.beds_available = max(0, urgent_care.beds_available - 1)

        relocated += 1

    conn.commit()
    return relocated
