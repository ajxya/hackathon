"""EDFlow bottleneck logic — utilization percentages, a green/yellow/red
status, per-patient wait tracking, and allocation recommendations.

The thresholds and formulas below are simple, tunable rules of thumb for
this demo, not real clinical or hospital-operations standards.
"""

from datetime import datetime

from app.config import (
    NURSE_TRIAGE_WEIGHT,
    PHYSICIAN_TRIAGE_WEIGHT,
    SIM_MINUTES_PER_REAL_SECOND,
    TIER_TARGET_MINUTES,
)

YELLOW_THRESHOLD = 70  # percent
RED_THRESHOLD = 90  # percent


def _pct(used, total):
    """Safe percentage: avoids dividing by zero, rounds to 1 decimal place."""
    if not total:
        return 0.0
    return round(used / total * 100, 1)


def get_utilization(conn):
    """Read the database and compute utilization percentages for each resource."""
    beds = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'occupied' THEN 1 ELSE 0 END) AS occupied,
            SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) AS available,
            SUM(CASE WHEN status = 'cleaning' THEN 1 ELSE 0 END) AS cleaning
        FROM beds
        """
    ).fetchone()

    rooms_total = conn.execute("SELECT COUNT(*) AS total FROM rooms").fetchone()["total"]
    rooms_in_use = conn.execute(
        "SELECT COUNT(DISTINCT room_id) AS n FROM beds WHERE status = 'occupied'"
    ).fetchone()["n"]

    waiting_rows = conn.execute(
        "SELECT id FROM patients WHERE status = 'waiting' ORDER BY acuity ASC, arrival_time ASC"
    ).fetchall()
    patients_waiting = len(waiting_rows)

    # Nurses and physicians are only assigned 1:1 to ADMITTED (bed-holding)
    # patients, and at most 10 patients can ever be admitted at once (there
    # are only 10 beds) — so their raw "current_patients" totals alone would
    # always cap out at 10, making both the slot count and the percentage
    # look permanently "stuck" no matter how large the waiting queue grows.
    # To fix that, both also pick up a fraction of workload from the
    # waiting queue (nurses do more direct triage than physicians, hence
    # the different weights), and that BLENDED number — not the raw,
    # bed-capped one — is what gets displayed as "capacity_used".
    nurses_raw = conn.execute(
        "SELECT SUM(current_patients) AS used, SUM(max_patients) AS capacity FROM nurses"
    ).fetchone()
    nurse_capacity_total = nurses_raw["capacity"] or 0
    nurse_workload_units = (nurses_raw["used"] or 0) + patients_waiting * NURSE_TRIAGE_WEIGHT
    nurse_capacity_used = min(round(nurse_workload_units), nurse_capacity_total)
    nurse_pct = min(_pct(nurse_workload_units, nurse_capacity_total), 100.0)

    physicians_raw = conn.execute(
        "SELECT SUM(current_patients) AS used, SUM(max_patients) AS capacity FROM physicians"
    ).fetchone()
    physician_capacity_total = physicians_raw["capacity"] or 0
    physician_workload_units = (physicians_raw["used"] or 0) + patients_waiting * PHYSICIAN_TRIAGE_WEIGHT
    physician_capacity_used = min(round(physician_workload_units), physician_capacity_total)
    physician_pct = min(_pct(physician_workload_units, physician_capacity_total), 100.0)

    # Displayed average wait: a flat 5 minutes per patient in the waiting
    # queue. This is deliberately simple rather than a realistic queue/
    # service-rate projection — a "real" projection stays close to zero
    # whenever beds are turning over quickly, which hid the demo's own
    # escalation ladder and Impact tab behind numbers too small to notice.
    # A flat per-patient number climbs predictably as the queue grows, so
    # the recommendations and trend charts have something to visibly react to.
    avg_wait_minutes = round(patients_waiting * 5.0, 1)

    return {
        "beds": {
            "total": beds["total"],
            "occupied": beds["occupied"],
            "available": beds["available"],
            "cleaning": beds["cleaning"],
            "pct": _pct(beds["occupied"], beds["total"]),
        },
        "rooms": {
            "total": rooms_total,
            "in_use": rooms_in_use,
            "pct": _pct(rooms_in_use, rooms_total),
        },
        "nurses": {
            "capacity_used": nurse_capacity_used,
            "capacity_total": nurse_capacity_total,
            "pct": nurse_pct,
        },
        "physicians": {
            "capacity_used": physician_capacity_used,
            "capacity_total": physician_capacity_total,
            "pct": physician_pct,
        },
        "patients_waiting": patients_waiting,
        "avg_wait_minutes": avg_wait_minutes,
    }


def get_breach_summary(conn):
    """Per-tier snapshot of the waiting queue: how many are waiting, how
    many have already blown past their tier's target wait, and their
    current average wait. The Tier 1-2 breach count is EDFlow's headline
    metric — protecting the sickest patients' wait times is the core goal,
    so this drives the status banner before capacity percentages do.
    """
    now = datetime.now()
    rows = conn.execute("SELECT acuity, arrival_time FROM patients WHERE status = 'waiting'").fetchall()

    by_tier = {tier: {"waiting": 0, "breached": 0, "avg_wait_minutes": 0.0} for tier in range(1, 6)}
    tier_waits = {tier: [] for tier in range(1, 6)}

    for row in rows:
        tier = row["acuity"]
        elapsed_seconds = (now - datetime.fromisoformat(row["arrival_time"])).total_seconds()
        wait_minutes = round(max(elapsed_seconds, 0) * SIM_MINUTES_PER_REAL_SECOND, 1)
        target_minutes = TIER_TARGET_MINUTES.get(tier, 0)

        by_tier[tier]["waiting"] += 1
        if wait_minutes > target_minutes:
            by_tier[tier]["breached"] += 1
        tier_waits[tier].append(wait_minutes)

    for tier, waits in tier_waits.items():
        if waits:
            by_tier[tier]["avg_wait_minutes"] = round(sum(waits) / len(waits), 1)

    return {
        "tier1_2_breaches": by_tier[1]["breached"] + by_tier[2]["breached"],
        "total_breaches": sum(t["breached"] for t in by_tier.values()),
        "by_tier": by_tier,
    }


def get_status(utilization, breach_summary):
    """Decide green / yellow / red, with a plain-English reason.

    Protecting sickest-first wait times is the whole point of this tool, so
    a Tier 1-2 breach is checked FIRST and forces red on its own, before any
    capacity percentage gets a say.
    """
    beds = utilization["beds"]
    nurses = utilization["nurses"]
    physicians = utilization["physicians"]
    waiting = utilization["patients_waiting"]
    tier12_breaches = breach_summary["tier1_2_breaches"]
    total_breaches = breach_summary["total_breaches"]

    if tier12_breaches > 0:
        return {
            "level": "red",
            "reason": (
                f"{tier12_breaches} Tier 1-2 patient{'s are' if tier12_breaches != 1 else ' is'} past their "
                f"target wait time — the sickest patients are being underserved."
            ),
        }

    # A hard bottleneck: patients waiting with nowhere to put them, regardless
    # of what the percentages say.
    if waiting > beds["available"]:
        return {
            "level": "red",
            "reason": (
                f"{waiting} patients are waiting but only {beds['available']} beds are available "
                f"— patients cannot be placed."
            ),
        }

    # Otherwise, base the status on whichever resource is under the most strain.
    candidates = [
        (
            "beds",
            beds["pct"],
            f"Bed occupancy is at {beds['pct']}% ({beds['occupied']} of {beds['total']} beds in use).",
        ),
        (
            "nurses",
            nurses["pct"],
            f"Nurse workload is at {nurses['pct']}% of capacity "
            f"({nurses['capacity_used']} of {nurses['capacity_total']} patient slots filled, plus queue triage).",
        ),
        (
            "physicians",
            physicians["pct"],
            f"Physician workload is at {physicians['pct']}% of capacity "
            f"({physicians['capacity_used']} of {physicians['capacity_total']} patient slots filled).",
        ),
    ]
    resource, pct, detail = max(candidates, key=lambda c: c[1])

    if pct >= RED_THRESHOLD:
        return {"level": "red", "reason": f"{detail} That's critically high — the ED is at risk of gridlock."}

    if pct >= YELLOW_THRESHOLD or total_breaches > 0:
        breach_note = (
            f" {total_breaches} lower-tier patient{'s are' if total_breaches != 1 else ' is'} past target too."
            if total_breaches
            else ""
        )
        return {"level": "yellow", "reason": f"{detail} Getting busy — keep an eye on {resource}.{breach_note}"}

    return {"level": "green", "reason": "Operating normally. No breaches, and beds, staff, and rooms all have headroom."}


def get_recommendations(utilization, breach_summary, float_pool, overflow_pool):
    """Recommend the next capped allocation action, as an escalation ladder:

        1. The ED's own resources/turnover
        2. The hospital float pool (nurses, physicians, rooms)
        3. Overflow capacity (extra physical beds within the ED)
        4. Relocation to nearby facilities

    Each rung is only evaluated if the previous one can't fully cover the
    need, and every quantity is capped at what's actually available — never
    a number bigger than the reserve it's drawn from. This is a rules-based
    heuristic for a demo, not a real staffing or capacity-planning model.
    """
    beds = utilization["beds"]
    rooms = utilization["rooms"]
    nurses = utilization["nurses"]
    physicians = utilization["physicians"]
    waiting = utilization["patients_waiting"]
    avg_wait = utilization["avg_wait_minutes"]
    tier12_breaches = breach_summary["tier1_2_breaches"]

    no_strain = beds["pct"] < YELLOW_THRESHOLD and nurses["pct"] < YELLOW_THRESHOLD and physicians["pct"] < YELLOW_THRESHOLD

    # Rung 0: genuinely nothing to do.
    if tier12_breaches == 0 and no_strain:
        return [
            {
                "rung": 0,
                "action": "No action needed",
                "reason": "All resources have headroom and no Tier 1-2 patients are past target.",
                "estimated_wait_reduction_minutes": 0,
                "quantities": {},
            }
        ]

    # Rung 1: the ED's own resources/turnover. If there's strain but no
    # Tier 1-2 breach yet, normal discharge throughput should keep pace —
    # no need to reach outside the ED.
    if tier12_breaches == 0:
        return [
            {
                "rung": 1,
                "action": "Monitor — ED's own turnover should keep pace",
                "reason": (
                    f"No Tier 1-2 breaches yet, but capacity is getting tight "
                    f"(beds {beds['pct']}%, nurses {nurses['pct']}%, physicians {physicians['pct']}%). "
                    f"Normal discharge turnover should keep up without outside help."
                ),
                "estimated_wait_reduction_minutes": 0,
                "quantities": {},
            }
        ]

    # A Tier 1-2 breach exists, which means the ED's own resources (rung 1)
    # have already fallen behind — escalate, capping every quantity at
    # what's actually available.
    units_needed = max(tier12_breaches, 1)
    ladder = []

    # Rung 2: hospital float pool (nurses, physicians, rooms).
    nurses_needed_flag = nurses["pct"] >= YELLOW_THRESHOLD
    physicians_needed_flag = physicians["pct"] >= YELLOW_THRESHOLD
    rooms_needed_flag = bool(rooms["total"]) and rooms["in_use"] >= rooms["total"]

    nurses_pulled = min(units_needed, float_pool["nurses_available"]) if nurses_needed_flag else 0
    physicians_pulled = min(units_needed, float_pool["physicians_available"]) if physicians_needed_flag else 0
    rooms_pulled = min(units_needed, float_pool["rooms_available"]) if rooms_needed_flag else 0

    rung2_applicable = nurses_needed_flag or physicians_needed_flag or rooms_needed_flag
    rung2_fully_covered = True

    if rung2_applicable:
        rung2_fully_covered = (
            (not nurses_needed_flag or nurses_pulled >= units_needed)
            and (not physicians_needed_flag or physicians_pulled >= units_needed)
            and (not rooms_needed_flag or rooms_pulled >= units_needed)
        )

        parts = []
        if nurses_pulled:
            parts.append(f"{nurses_pulled} nurse{'s' if nurses_pulled != 1 else ''}")
        if physicians_pulled:
            parts.append(f"{physicians_pulled} physician{'s' if physicians_pulled != 1 else ''}")
        if rooms_pulled:
            parts.append(f"{rooms_pulled} room{'s' if rooms_pulled != 1 else ''}")

        reason = (
            f"{tier12_breaches} Tier 1-2 patient{'s' if tier12_breaches != 1 else ''} past target; "
            f"ED capacity is strained (beds {beds['pct']}%, nurses {nurses['pct']}%, "
            f"physicians {physicians['pct']}%)."
        )
        shortfalls = []
        if nurses_needed_flag and nurses_pulled < units_needed:
            shortfalls.append(f"only {float_pool['nurses_available']} nurse(s) left in the float pool")
        if physicians_needed_flag and physicians_pulled < units_needed:
            shortfalls.append(f"only {float_pool['physicians_available']} physician(s) left")
        if rooms_needed_flag and rooms_pulled < units_needed:
            shortfalls.append(f"only {float_pool['rooms_available']} room(s) left")
        if shortfalls:
            reason += " Float pool is limited: " + "; ".join(shortfalls) + "."

        action = (
            f"Pull {', '.join(parts)} from the hospital float pool"
            if parts
            else "Float pool exhausted — no staff or rooms available to pull"
        )

        ladder.append(
            {
                "rung": 2,
                "action": action,
                "reason": reason,
                "estimated_wait_reduction_minutes": round(
                    min(avg_wait, nurses_pulled * 6 + physicians_pulled * 7 + rooms_pulled * 10), 1
                ),
                "quantities": {"nurses": nurses_pulled, "physicians": physicians_pulled, "rooms": rooms_pulled},
            }
        )

    # Rung 3: overflow capacity (extra physical beds within the ED).
    beds_needed_flag = waiting > beds["available"]
    beds_opened = min(units_needed, overflow_pool["overflow_beds_available"]) if beds_needed_flag else 0
    rung3_fully_covered = (not beds_needed_flag) or beds_opened >= units_needed

    if beds_needed_flag:
        reason = (
            f"{waiting} patient{'s' if waiting != 1 else ''} waiting with only "
            f"{beds['available']} bed{'s' if beds['available'] != 1 else ''} open "
            f"({units_needed} more needed to clear the Tier 1-2 breach)."
        )
        if beds_opened < units_needed:
            reason += (
                f" Only {overflow_pool['overflow_beds_available']} overflow "
                f"bed{'s' if overflow_pool['overflow_beds_available'] != 1 else ''} available."
            )

        ladder.append(
            {
                "rung": 3,
                "action": (
                    f"Open {beds_opened} overflow bed{'s' if beds_opened != 1 else ''}"
                    if beds_opened
                    else "No overflow beds available"
                ),
                "reason": reason,
                "estimated_wait_reduction_minutes": round(min(avg_wait, beds_opened * 8), 1),
                "quantities": {"beds": beds_opened},
            }
        )

    if rung2_fully_covered and rung3_fully_covered:
        return ladder

    # Rung 4: relocate lower-tier patients to nearby facilities. Never
    # Tier 1 — the relocation engine itself enforces that; this is just the
    # recommendation surface for it.
    ladder.append(
        {
            "rung": 4,
            "action": "Relocate patients to nearby facilities",
            "reason": (
                f"ED resources, the float pool, and overflow capacity are exhausted, and "
                f"{tier12_breaches} Tier 1-2 patient{'s are' if tier12_breaches != 1 else ' is'} still past target. "
                f"Lower-tier patients (never Tier 1) can be redirected to ease the queue."
            ),
            "estimated_wait_reduction_minutes": round(min(avg_wait, units_needed * 5), 1),
            "quantities": {},
        }
    )
    return ladder


STATUS_LABELS = {
    "waiting": "Waiting",
    "in_bed": "Admitted",
    "discharged": "Discharged",
    "relocated": "Relocated",
    "left_lwbs": "Left without being seen",
}


def get_patients_list(conn):
    """All patients created since the last reset, with a live wait time
    (in simulated minutes) compared against their tier's target."""
    now = datetime.now()
    rows = conn.execute(
        """
        SELECT id, name, injury, acuity, source, status, arrival_time, bed_assigned_at
        FROM patients
        ORDER BY arrival_time DESC
        """
    ).fetchall()

    patients = []
    for row in rows:
        arrival_dt = datetime.fromisoformat(row["arrival_time"])
        if row["status"] == "waiting":
            elapsed_seconds = (now - arrival_dt).total_seconds()
        elif row["bed_assigned_at"]:
            placed_dt = datetime.fromisoformat(row["bed_assigned_at"])
            elapsed_seconds = (placed_dt - arrival_dt).total_seconds()
        else:
            elapsed_seconds = 0

        wait_minutes = round(max(elapsed_seconds, 0) * SIM_MINUTES_PER_REAL_SECOND, 1)
        target_minutes = TIER_TARGET_MINUTES.get(row["acuity"], 0)

        patients.append(
            {
                "id": row["id"],
                "name": row["name"],
                "injury": row["injury"],
                "tier": row["acuity"],
                "source": row["source"],
                "status": row["status"],
                "status_label": STATUS_LABELS.get(row["status"], row["status"]),
                "wait_minutes": wait_minutes,
                "target_minutes": target_minutes,
                "overdue": wait_minutes > target_minutes,
            }
        )
    return patients
